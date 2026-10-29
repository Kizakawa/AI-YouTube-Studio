"""app.graph.tools.youtube_peak_executor — Thực thi săn & cắt đoạn Peak từ YouTube Shorts kèm Mini-RAG."""
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain_core.tools import tool
from app.utils.langsmith_tracer import trace_custom_span
from app.agents.media_agent import MediaAgent, clean_scene_search_query
from app.services.media import license_policy
from config.settings import settings

logger = logging.getLogger(__name__)
# Điều tiết tối đa 3 luồng tải yt-dlp vật lý cùng lúc để tránh YouTube CDN bóp băng thông (throttling xuống 20 KB/s)
_yt_download_sem = threading.BoundedSemaphore(3)
from app.graph.tools.tool_schemas import (
    _yt_url_lock,
    _YT_QUERY_FILLER_WORDS,
    _TALKING_HEAD_BLOCKLIST,
    _HIGH_RISK_WATERMARK_CHANNEL_BLOCKLIST,
    has_versus_pattern,
    is_edit_or_ai_slop_video_metadata,
    _extract_subject_anchor_tokens,
    _clean_yt_search_phrase,
    _resolve_adaptive_search_queries,
)
from app.graph.tools.youtube_search_ranker import (
    _find_best_peak_window,
    _verify_short_clip_with_gemini_vision,
    _search_youtube_flat_cached,
    _rank_youtube_candidates_with_llm,
)
from app.graph.tools.video_fallback_executors import (
    _try_bilibili_broll_fallback,
    _try_licensed_stock_video_fallback,
)


@trace_custom_span("MediaTools.execute_youtube_or_stock_video", run_type="tool")
def execute_youtube_short_peak_clone_sync(
    search_query: str,
    output_path: str,
    target_duration_sec: float = 4.5,
    clip_role: str = "PEAK",
    used_urls: Optional[Set[str]] = None,
    fallback_query: str = "",
    subject_entity: str = "",
    narration: str = "",
    topic: str = "",
    search_scope: str = "SCENE_SPECIFIC",
    overall_script_query: str = "",
    max_peak_duration_sec: float = 14.0,
    job_id: int = 0,
    scene_id: int = 0,
    channel_id: str = "",
    allow_3d_cgi_monster: bool = False,
    skip_youtube_search: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Thực thi tìm kiếm & cắt đoạn Peak từ YouTube Shorts tích hợp hệ thống Mini-RAG (`VideoMemoryRAG`):
    - Chống lặp lại 100% các cảnh cắt BÊN TRONG CÙNG 1 VIDEO (`job_id`) qua khóa nguyên tử ID/URL,
      so khớp ngữ nghĩa tiêu đề chống bản re-upload, và đối chiếu chữ ký thị giác 3-frame `dHash`
      (không cần lưu khóa cứng giữa các bài đăng khác nhau).
    - Xếp hạng ứng viên bằng Hybrid BM25/Cosine của Mini-RAG kết hợp trọng số giữ chân lịch sử (`stayed_to_watch_pct`).
    - Nếu đoạn Peak thực tế trong video nguồn kéo dài (ví dụ 6s - 14s), tự động trích xuất TRỌN VẸN cả đoạn Peak dài
      (`full_peak_path` & `full_peak_duration_sec`) để bộ lắp ghép Timeline (`node_fetch_media`) ưu tiên phủ liền mạch
      đoạn video Peak dài đó qua nhiều phân cảnh liên tiếp trước khi dùng đến ảnh tĩnh!
    """
    from app.services.media.media_history import (
        extract_media_identity,
        is_media_already_used,
        record_used_media,
    )
    from app.services.video_memory_rag import get_video_memory_rag

    rag = get_video_memory_rag()
    used = used_urls if used_urls is not None else set()

    eff_scene_id = int(scene_id or 0)
    if eff_scene_id <= 0 and output_path:
        m_sc = re.search(r"scene_(\d+)", Path(output_path).name)
        if m_sc:
            eff_scene_id = int(m_sc.group(1))
        elif "hook_motion_001" in Path(output_path).name:
            eff_scene_id = 1

    effective_scope, search_phrases = _resolve_adaptive_search_queries(
        search_scope=search_scope,
        scene_query=search_query,
        overall_script_query=overall_script_query,
        fallback_query=fallback_query,
        subject_entity=subject_entity,
        topic=topic,
        narration=narration,
        max_words=5,
    )
    if allow_3d_cgi_monster:
        c_core = _clean_yt_search_phrase(subject_entity or topic, max_words=2, strip_distractor_props=True) or "sea monster"
        monster_angle_variants = [
            f"{c_core} 3d animation",
            f"{c_core} thalassophobia",
            f"{c_core} megalophobia",
            f"{c_core} submarine animation",
            "sea monster thalassophobia",
        ]
        rot_idx = max(0, (eff_scene_id - 1)) % len(monster_angle_variants)
        rotated_angles = monster_angle_variants[rot_idx:] + monster_angle_variants[:rot_idx]
        monster_yt_phrases = [
            *search_phrases,
            *rotated_angles[:2],
        ]
        search_phrases = [q for q in dict.fromkeys(monster_yt_phrases) if q][:4]

    subject_anchors = _extract_subject_anchor_tokens(subject_entity, topic, search_phrases[0] if search_phrases else search_query)

    logger.info(
        f"🎯 [YouTubePeakTool + MiniRAG] Scene=#{eff_scene_id} | Scope={effective_scope} | Role={clip_role} | "
        f"Queries={search_phrases} (raw_scene='{search_query}', overall='{overall_script_query}')"
    )

    all_query_tokens = {
        w.lower()
        for q in search_phrases
        for w in re.findall(r"[a-zA-Z]{3,}", q.lower())
        if w.lower() not in _YT_QUERY_FILLER_WORDS
    }

    # ── TẦNG 1 (ƯU TIÊN SỐ 1): SĂN & CẮT ĐOẠN PEAK TỪ VIDEO YOUTUBE RỦI RO THẤP (ĐÃ QUA 4 LỚP LỌC WATERMARK & >= 720P) ──
    allow_low_risk_yt = (not skip_youtube_search) and (
        allow_3d_cgi_monster or (
            os.getenv("ALLOW_LOW_RISK_YOUTUBE_CLIPS", "true").strip().lower() not in ("0", "false", "no", "off")
        )
    )
    if allow_low_risk_yt:
        try:
            import yt_dlp
            from app.graph.evaluators.visual_critic import VisualCritic

            resolved_niche = ""
            if channel_id:
                try:
                    from app.services.niche_manager import get_channel_niche
                    resolved_niche = str((get_channel_niche(channel_id) or {}).get("id") or "")
                except Exception:
                    resolved_niche = ""

            candidates = _search_youtube_flat_cached(
                search_phrases,
                subject_anchors,
                all_query_tokens,
                allow_3d_cgi_monster=allow_3d_cgi_monster,
                channel_niche=resolved_niche,
            )
            unused_cands = [
                c for c in candidates
                if not is_media_already_used(c["url"], used) and f"yt:{c['id']}" not in used
            ]
            unused_cands = _rank_youtube_candidates_with_llm(
                candidates=unused_cands,
                topic=topic,
                subject_entity=subject_entity or topic,
                narration=narration,
                search_query=search_phrases[0] if search_phrases else search_query,
                clip_role=clip_role,
                allow_3d_cgi_monster=allow_3d_cgi_monster,
            )
            ordered_cands = rag.rank_video_candidates_for_scene(
                job_id=job_id,
                candidates=unused_cands,
                scene_narration=narration,
                scene_query=search_query,
                topic=topic,
                subject_entity=subject_entity or topic,
                channel_id=channel_id,
            )

            max_claimed_probes = 3 if (clip_role or "").upper() == "HOOK" else 2
            claimed_probes = 0
            # Fast-Drop 'A vs B': chỉ thử tối đa 1 ứng viên mang tiêu đề 'A vs B'; nếu thất bại thì bỏ ngay toàn bộ nhánh 'vs' để lấy clip đơn chủ thể sạch!
            vs_probes_attempted = 0
            cand_pool = list(ordered_cands[:20])
            if len(cand_pool) > 4 and eff_scene_id > 1:
                rot_c = (eff_scene_id - 1) % min(5, len(cand_pool))
                cand_pool = cand_pool[rot_c:] + cand_pool[:rot_c]

            for cand in cand_pool:
                if claimed_probes >= max_claimed_probes:
                    break
                vid_url = cand["url"]
                vid_id = cand.get("id") or ""
                cand_title = str(cand.get("title") or "")
                is_vs_cand = has_versus_pattern(cand_title)
                if is_vs_cand and vs_probes_attempted >= 1:
                    logger.info(
                        f"⚡ [Fast-Drop A vs B] Đã thử nhánh 'vs' không tìm được cảnh thật sạch -> Bỏ qua '{cand_title[:45]}' để chuyển ngay sang clip đơn chủ thể!"
                    )
                    continue

                if not rag.try_claim_in_job_video(
                    job_id=job_id,
                    vid_id=vid_id,
                    url=vid_url,
                    title=cand_title,
                    scene_id=eff_scene_id,
                ):
                    continue

                with _yt_url_lock:
                    if vid_url in used:
                        rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                        continue
                    used.update(extract_media_identity(vid_url))

                claimed_probes += 1
                if is_vs_cand:
                    vs_probes_attempted += 1
                info_entry: Dict[str, Any] = cand
                license_verified = not license_policy.cc_only_enabled()
                if license_policy.cc_only_enabled():
                    try:
                        info_opts = {
                            "quiet": True,
                            "no_warnings": True,
                            "skip_download": True,
                            "socket_timeout": 8,
                            "ignore_no_formats_error": True,
                            "format": "bestvideo/best",
                        }
                        with yt_dlp.YoutubeDL(info_opts) as ydl_info:
                            full_info = ydl_info.extract_info(vid_url, download=False)
                            if isinstance(full_info, dict):
                                license_verified = license_policy.is_reuse_allowed(full_info)
                                dur_full = float(full_info.get("duration") or 0)
                                if dur_full < 4 or dur_full > 300:
                                    rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                                    continue
                                w_full = int(full_info.get("width") or 0)
                                h_full = int(full_info.get("height") or 0)
                                if w_full > 0 and h_full > 0 and min(w_full, h_full) < 480:
                                    rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                                    continue
                                meta_full_lower = (
                                    f"{full_info.get('title') or ''} {full_info.get('uploader') or ''} "
                                    f"{full_info.get('channel') or ''} {(full_info.get('description') or '')[:300]}"
                                ).lower()
                                if any(bad in meta_full_lower for bad in _TALKING_HEAD_BLOCKLIST) or any(
                                    bad_c in meta_full_lower for bad_c in _HIGH_RISK_WATERMARK_CHANNEL_BLOCKLIST
                                ):
                                    rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                                    continue
                                info_entry = full_info
                    except Exception:
                        pass

                if not license_verified:
                    logger.info(
                        f"🚫 [LicensePolicy] Bỏ qua '{cand_title[:45]}' ({vid_url}): "
                        f"không xác minh được giấy phép Creative Commons"
                    )
                    rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                    continue

                win_pair = _find_best_peak_window(
                    info_entry,
                    target_duration_sec + 0.5,
                    role=clip_role,
                    existing_windows=None,
                    max_peak_dur=max_peak_duration_sec,
                )
                if not win_pair:
                    rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                    continue
                start_s, end_s = win_pair

                with tempfile.TemporaryDirectory() as tmp_dir:
                    raw_dl_template = str(Path(tmp_dir) / "raw_short.%(ext)s")
                    dl_opts = license_policy.optimize_ytdlp_download_opts({
                        "quiet": True,
                        "no_warnings": True,
                        "noplaylist": True,
                        "max_filesize": 38 * 1024 * 1024,
                        "format": license_policy.VERTICAL_FIRST_YTDLP_FORMAT,
                        "merge_output_format": "mp4",
                        "outtmpl": raw_dl_template,
                        "socket_timeout": 12,
                        "retries": 1,
                        "nopart": True,
                    })
                    # Chỉ dùng download_ranges khi video gốc dài > 75s; với YouTube Shorts (<= 75s), tải thẳng MP4 qua HTTP nhanh gấp 4-6 lần và tránh treo ffmpeg HTTP seek
                    cand_dur_est = float(info_entry.get("duration") or cand.get("duration") or 0)
                    if cand_dur_est > 75.0:
                        try:
                            from yt_dlp.utils import download_range_func
                            dl_opts["download_ranges"] = download_range_func(None, [(start_s, end_s + 1.0)])
                        except Exception:
                            pass

                    try:
                        with _yt_download_sem:
                            with yt_dlp.YoutubeDL(dl_opts) as ydl_dl:
                                ydl_dl.download([vid_url])
                    except Exception:
                        dl_opts.pop("download_ranges", None)
                        try:
                            with _yt_download_sem:
                                with yt_dlp.YoutubeDL(dl_opts) as ydl_dl2:
                                    ydl_dl2.download([vid_url])
                        except Exception:
                            rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                            continue

                    downloaded_files = list(Path(tmp_dir).glob("raw_short.*"))
                    if not downloaded_files and "download_ranges" in dl_opts:
                        dl_opts.pop("download_ranges", None)
                        try:
                            with _yt_download_sem:
                                with yt_dlp.YoutubeDL(dl_opts) as ydl_dl2:
                                    ydl_dl2.download([vid_url])
                        except Exception:
                            pass
                        downloaded_files = list(Path(tmp_dir).glob("raw_short.*"))

                    if not downloaded_files:
                        rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                        continue
                    raw_file = str(downloaded_files[0])
                    if Path(raw_file).stat().st_size < 15000:
                        rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                        continue

                    from app.utils.ffmpeg_helper import find_hook_peak_window, get_duration_ms, trim_video
                    raw_dur_sec = max(0.0, get_duration_ms(raw_file) / 1000.0)
                    min_req_dur = max(3.0, target_duration_sec)
                    if "download_ranges" in dl_opts:
                        rel_start, natural_peak_len = find_hook_peak_window(
                            raw_file,
                            min_duration=min_req_dur,
                            max_duration=min(max_peak_duration_sec, max(min_req_dur, raw_dur_sec)),
                        )
                        actual_start_s = rel_start
                    else:
                        det_start, natural_peak_len = find_hook_peak_window(
                            raw_file,
                            min_duration=min_req_dur,
                            max_duration=max_peak_duration_sec,
                        )
                        actual_start_s = det_start if det_start > 0.0 else start_s

                    natural_peak_len = max(min_req_dur, min(natural_peak_len, max_peak_duration_sec))
                    scene_clip_len = min_req_dur

                    # [FRAME-0 ACTION BOOST]: Nếu là Cảnh 1 (HOOK), tự động dịch điểm cắt vào sâu +0.35s
                    # (nếu độ dài video cho phép) để triệt tiêu hoàn toàn khung hình tĩnh/fade-in ở giây 0.0s!
                    if (clip_role or "").upper() == "HOOK" and raw_dur_sec >= actual_start_s + scene_clip_len + 0.45:
                        actual_start_s = round(actual_start_s + 0.35, 2)

                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    try:
                        trim_video(
                            input_path=raw_file,
                            duration_seconds=scene_clip_len,
                            output_path=output_path,
                            width=1080,
                            height=1920,
                            fps=30,
                            apply_grading=True,
                            is_hook_scene=((clip_role or "").upper() in ("HOOK", "PEAK")),
                            fair_use_transform=True,
                            start_offset_sec=actual_start_s,
                            preserve_continuous_flow=True,
                        )
                    except Exception:
                        vf_filter = (
                            "scale=1080:1920:force_original_aspect_ratio=increase,"
                            "crop=1080:1920,fps=30,format=yuv420p"
                        )
                        cmd = [
                            "ffmpeg", "-y", "-v", "error",
                            "-ss", f"{actual_start_s:.2f}",
                            "-i", raw_file,
                            "-t", f"{scene_clip_len:.2f}",
                            "-an", "-vf", vf_filter,
                            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "21",
                            "-pix_fmt", "yuv420p", "-color_range", "tv",
                            output_path,
                        ]
                        subprocess.run(cmd, capture_output=True, timeout=25, check=False)

                    if Path(output_path).exists() and Path(output_path).stat().st_size > 20000:
                        ok_sig, dup_sid = rag.verify_and_commit_in_job_clip(
                            job_id=job_id,
                            scene_id=eff_scene_id,
                            media_path=output_path,
                            vid_id=vid_id,
                            url=vid_url,
                            title=cand_title,
                            is_continuation_slice=False,
                        )
                        if not ok_sig:
                            rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                            Path(output_path).unlink(missing_ok=True)
                            continue

                        passed_vis, vis_score, vis_reason = _verify_short_clip_with_gemini_vision(
                            video_path=output_path,
                            topic=topic,
                            narration=narration,
                            subject_entity=subject_entity or topic,
                            overall_script_query=overall_script_query or (search_phrases[0] if search_phrases else topic),
                            search_scope=effective_scope,
                            video_title=cand_title,
                            allow_3d_cgi_monster=allow_3d_cgi_monster,
                            strict_no_fallback=True,
                        )
                        if not passed_vis:
                            logger.info(
                                f"🚫 [YouTubePeakTool] Gemini Vision từ chối clip '{cand_title[:45]}' "
                                f"(điểm={vis_score}/10: {vis_reason}) -> Thử ứng viên tiếp theo!"
                            )
                            rag.release_in_job_video_claim(job_id=job_id, vid_id=vid_id, url=vid_url, title=cand_title, scene_id=eff_scene_id)
                            Path(output_path).unlink(missing_ok=True)
                            continue

                        full_peak_path: Optional[str] = None
                        if (
                            (clip_role or "").upper() == "HOOK"
                            and natural_peak_len >= scene_clip_len + 1.8
                            and raw_dur_sec >= actual_start_s + scene_clip_len + 1.5
                        ):
                            cand_full_path = str(Path(output_path).parent / f"{Path(output_path).stem}_fullpeak.mp4")
                            try:
                                trim_video(
                                    input_path=raw_file,
                                    duration_seconds=natural_peak_len,
                                    output_path=cand_full_path,
                                    width=1080,
                                    height=1920,
                                    fps=30,
                                    apply_grading=True,
                                    is_hook_scene=False,
                                    fair_use_transform=True,
                                    start_offset_sec=actual_start_s,
                                    preserve_continuous_flow=True,
                                )
                                if Path(cand_full_path).exists() and Path(cand_full_path).stat().st_size > 25000:
                                    full_peak_path = cand_full_path
                            except Exception as e_fp:
                                logger.debug(f"[YouTubePeakTool] Lỗi xuất full_peak_path: {e_fp}")

                        win_start = (start_s + actual_start_s) if "download_ranges" in dl_opts else actual_start_s
                        win_end = win_start + (natural_peak_len if full_peak_path else scene_clip_len)

                        record_used_media(
                            url=vid_url,
                            query=search_phrases[0] if search_phrases else search_query,
                            job_id=job_id,
                            source="low_risk_clean_video",
                            media_path=output_path,
                        )
                        if license_policy.cc_only_enabled():
                            license_policy.record_credit(
                                job_id=job_id,
                                video_id=vid_id,
                                title=cand_title,
                                uploader=str(info_entry.get("channel") or info_entry.get("uploader") or cand.get("uploader") or ""),
                            )

                        logger.info(
                            f"🔥 [YouTubePeakTool + MiniRAG] Đã cắt & duyệt Vision ({vis_score}/10, scope={effective_scope}) "
                            f"đoạn {clip_role} ({win_start:.1f}s-{win_end:.1f}s, full_peak={natural_peak_len:.1f}s) "
                            f"từ nguồn YouTube sạch watermark: '{cand_title[:55]}' ({vid_url})"
                        )
                        return {
                            "media_path": output_path,
                            "media_type": "hook_ready_video" if (clip_role or "").upper() == "HOOK" else "video",
                            "source_url": vid_url,
                            "youtube_id": vid_id,
                            "title": cand_title,
                            "window": f"{win_start:.1f}s-{win_end:.1f}s",
                            "vision_score": vis_score,
                            "search_scope": effective_scope,
                            "full_peak_path": full_peak_path,
                            "full_peak_duration_sec": round(natural_peak_len if full_peak_path else scene_clip_len, 2),
                            "scene_clip_duration_sec": round(scene_clip_len, 2),
                        }
        except Exception as e_yt:
            logger.debug(f"[YouTubePeakTool] Lỗi nhánh YouTube video: {e_yt}")


    # ── TẦNG 2 (ƯU TIÊN SỐ 2): PEXELS HD / PIXABAY HD / WIKIMEDIA STOCK VIDEO SẠCH 100% (TẢI SIÊU TỐC, KHÔNG DÍNH SUB TRUNG) ──
    stock_res = _try_licensed_stock_video_fallback(
        search_phrases=search_phrases,
        search_query=search_query,
        subject_entity=subject_entity,
        topic=topic,
        narration=narration,
        overall_script_query=overall_script_query,
        effective_scope=effective_scope,
        output_path=output_path,
        target_duration_sec=target_duration_sec,
        max_peak_duration_sec=max_peak_duration_sec,
        clip_role=clip_role,
        allow_3d_cgi_monster=allow_3d_cgi_monster,
        used=used,
        job_id=job_id,
        eff_scene_id=eff_scene_id,
    )
    if stock_res:
        return stock_res

    # ── TẦNG 3 (DỰ PHÒNG CUỐI KHI STOCK HD KHÔNG CÓ): SĂN B-ROLL TRÊN BILIBILI (SAFE-CROP & DUYỆT VISION) ──
    if allow_low_risk_yt:
        bili_res = _try_bilibili_broll_fallback(
            search_phrases=search_phrases,
            search_query=search_query,
            subject_entity=subject_entity,
            topic=topic,
            narration=narration,
            overall_script_query=overall_script_query,
            effective_scope=effective_scope,
            output_path=output_path,
            target_duration_sec=target_duration_sec,
            clip_role=clip_role,
            allow_3d_cgi_monster=allow_3d_cgi_monster,
            used=used,
            job_id=job_id,
            eff_scene_id=eff_scene_id,
        )
        if bili_res:
            return bili_res

    return None
