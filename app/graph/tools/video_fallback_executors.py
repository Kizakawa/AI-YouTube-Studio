"""app.graph.tools.video_fallback_executors — Tầng dự phòng Bilibili B-Roll & Pexels/Pixabay/Wikimedia HD Stock Video."""
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
from app.graph.tools.tool_schemas import (
    _yt_url_lock,
    _clean_yt_search_phrase,
    build_single_entity_stock_queries,
)
from app.graph.tools.youtube_search_ranker import _verify_short_clip_with_gemini_vision


def _try_bilibili_broll_fallback(
    search_phrases: List[str],
    search_query: str,
    subject_entity: str,
    topic: str,
    narration: str,
    overall_script_query: str,
    effective_scope: str,
    output_path: str,
    target_duration_sec: float,
    clip_role: str,
    allow_3d_cgi_monster: bool,
    used: Set[str],
    job_id: int,
    eff_scene_id: int,
    aspect_ratio: str = "9:16",
) -> Optional[Dict[str, Any]]:
    """Tầng 1.5: Săn B-Roll độc quyền trên Bilibili (Safe-Crop logo/phụ đề + duyệt Gemini Vision)."""
    from app.services.media.media_history import extract_media_identity, record_used_media
    from app.services.video_memory_rag import get_video_memory_rag
    rag = get_video_memory_rag()
    # ── TẦNG 1.5: SĂN B-ROLL ĐỘC QUYỀN TRÊN BILIBILI (ĐÃ SAFE-CROP LOGO & PHỤ ĐỀ TRUNG + DUYỆT VISION) ──
    try:
        from app.services.media.bilibili_fetcher import (
            download_and_crop_bilibili_broll_clip,
            search_bilibili_videos,
            translate_query_to_chinese,
        )

        base_q_for_bili = search_phrases[0] if search_phrases else (search_query or subject_entity or topic)
        zh_query = translate_query_to_chinese(
            query=base_q_for_bili,
            primary_entity=subject_entity or topic,
            is_3d_or_scifi=allow_3d_cgi_monster,
        )
        bili_cands = search_bilibili_videos(zh_query, max_results=6, min_dur_s=10.0, max_dur_s=540.0)
        for b_cand in bili_cands[:2]:
            bvid = str(b_cand.get("bvid") or b_cand.get("id") or "")
            b_url = str(b_cand.get("url") or f"https://www.bilibili.com/video/{bvid}")
            b_title = str(b_cand.get("title") or bvid)
            if not bvid or b_url in used:
                continue
            if not rag.try_claim_in_job_video(
                job_id=job_id,
                vid_id=bvid,
                url=b_url,
                title=b_title,
                scene_id=eff_scene_id,
            ):
                continue
            with _yt_url_lock:
                used.update(extract_media_identity(b_url))

            b_info = download_and_crop_bilibili_broll_clip(
                url_or_id=bvid,
                output_path=output_path,
                target_duration_sec=max(3.5, target_duration_sec),
                aspect_ratio=aspect_ratio,
            )
            if not b_info or not Path(output_path).exists() or Path(output_path).stat().st_size < 30_000:
                rag.release_in_job_video_claim(job_id=job_id, vid_id=bvid, url=b_url, title=b_title, scene_id=eff_scene_id)
                Path(output_path).unlink(missing_ok=True)
                continue

            ok_sig_b, _ = rag.verify_and_commit_in_job_clip(
                job_id=job_id,
                scene_id=eff_scene_id,
                media_path=output_path,
                vid_id=bvid,
                url=b_url,
                title=b_title,
                is_continuation_slice=False,
            )
            if not ok_sig_b:
                rag.release_in_job_video_claim(job_id=job_id, vid_id=bvid, url=b_url, title=b_title, scene_id=eff_scene_id)
                Path(output_path).unlink(missing_ok=True)
                continue

            passed_vis_b, vis_score_b, vis_reason_b = _verify_short_clip_with_gemini_vision(
                video_path=output_path,
                topic=topic,
                narration=narration,
                subject_entity=subject_entity or topic,
                overall_script_query=overall_script_query or base_q_for_bili,
                search_scope=effective_scope,
                video_title=f"{b_title} ({zh_query})",
                allow_3d_cgi_monster=allow_3d_cgi_monster,
                strict_no_fallback=True,
            )
            if not passed_vis_b:
                logger.info(
                    f"🚫 [BilibiliPeakTool] Gemini Vision từ chối clip Bilibili '{b_title[:40]}' "
                    f"(điểm={vis_score_b}/10: {vis_reason_b}) -> Thử ứng viên tiếp theo!"
                )
                rag.release_in_job_video_claim(job_id=job_id, vid_id=bvid, url=b_url, title=b_title, scene_id=eff_scene_id)
                Path(output_path).unlink(missing_ok=True)
                continue

            record_used_media(
                url=b_url,
                query=zh_query,
                job_id=job_id,
                source="bilibili_clean_broll",
                media_path=output_path,
            )
            logger.info(
                f"🐉 [BilibiliPeakTool] Đã săn & duyệt Vision ({vis_score_b}/10) clip Bilibili độc quyền "
                f"('{zh_query}' -> '{b_title[:45]}', {b_url})"
            )
            return {
                "media_path": output_path,
                "media_type": "hook_ready_video" if (clip_role or "").upper() == "HOOK" else "video",
                "source_url": b_url,
                "youtube_id": bvid,
                "title": b_title,
                "window": "bilibili_safe_crop",
                "vision_score": vis_score_b,
                "search_scope": effective_scope,
                "full_peak_path": None,
                "full_peak_duration_sec": round(float(target_duration_sec), 2),
                "scene_clip_duration_sec": round(float(target_duration_sec), 2),
            }
    except Exception as e_bili:
        logger.debug(f"[BilibiliPeakTool] Nhánh Bilibili B-roll skip: {e_bili}")

    return None


def _try_licensed_stock_video_fallback(
    search_phrases: List[str],
    search_query: str,
    subject_entity: str,
    topic: str,
    narration: str,
    overall_script_query: str,
    effective_scope: str,
    output_path: str,
    target_duration_sec: float,
    max_peak_duration_sec: float,
    clip_role: str,
    allow_3d_cgi_monster: bool,
    used: Set[str],
    job_id: int,
    eff_scene_id: int,
    aspect_ratio: str = "9:16",
) -> Optional[Dict[str, Any]]:
    """Tầng 2: Fallback tự động sang Video HD dọc bản quyền Pexels / Pixabay / Wikimedia Commons."""
    from app.services.media.media_history import extract_media_identity, is_media_already_used, record_used_media
    from app.services.video_memory_rag import get_video_memory_rag
    rag = get_video_memory_rag()
    # ── TẦNG 2 (FALLBACK TỰ ĐỘNG KHI KHÔNG CÓ CLIP YOUTUBE ĐẠT CHUẨN): VIDEO HD DỌC TRÊN PEXELS / PIXABAY ──
    if not allow_3d_cgi_monster:
        try:
            from app.graph.evaluators.visual_critic import VisualCritic
            media_agent = MediaAgent()
            raw_cand_sources = (
                [search_query, *search_phrases]
                if effective_scope == "SCENE_SPECIFIC"
                else [*search_phrases, overall_script_query, subject_entity]
            )
            # Fast-Drop 'A vs B' khi xuống kho Pexels/Pixabay/Wikimedia: tách sạch thành truy vấn ĐƠN CHỦ THỂ!
            stock_query_candidates = build_single_entity_stock_queries(
                raw_queries=raw_cand_sources,
                subject_entity=subject_entity,
                topic=topic,
                max_words=4,
            )
            if not stock_query_candidates:
                stock_query_candidates = [
                    _clean_yt_search_phrase(search_query or subject_entity or topic, max_words=3, strip_distractor_props=True)
                    or "wildlife nature"
                ]
            scene_core_2w = _clean_yt_search_phrase(stock_query_candidates[0], max_words=2, strip_distractor_props=True)
            scene_subj_hint = (
                f"{stock_query_candidates[0]} (hoặc {subject_entity or scene_core_2w})"
                if effective_scope == "SCENE_SPECIFIC"
                else (subject_entity or topic)
            )

            va_vid = {
                "analysis_source": "ai",
                "subject": scene_subj_hint,
                "core_visual_entity": scene_subj_hint,
                "topic": topic or overall_script_query,
                "expected_visual_content": f"{scene_subj_hint} — {narration}",
                "search_queries": stock_query_candidates[:3],
                "is_hook_scene": ((clip_role or "").upper() == "HOOK"),
            }
            effective_narr = (
                f"{topic or overall_script_query}. {narration}"
                if effective_scope == "SCRIPT_OVERALL"
                else narration
            )
            # Thử tối đa 3 truy vấn từ cụ thể -> rút gọn danh từ gốc (2 từ -> 1 từ) + bổ sung nguồn thứ 5 Wikimedia Commons HD Video
            for idx_sq, st_q in enumerate(stock_query_candidates[:2]):
                va_iter = dict(va_vid)
                q_2w = _clean_yt_search_phrase(st_q, max_words=2)
                va_iter["search_queries"] = list(dict.fromkeys([q for q in (st_q, q_2w) if q]))
                best_stock_v = None
                for _claim_retry in range(2):
                    best_stock_v = media_agent.search_best_video_clip(
                        query=st_q,
                        used_urls=used,
                        visual_analysis=va_iter,
                        narration=effective_narr,
                        topic=topic or overall_script_query,
                    )
                    stock_backend = getattr(media_agent, "_stock", None) or getattr(media_agent, "_stock_fetcher", None)
                    if (not best_stock_v or not best_stock_v.get("url")) and stock_backend is not None:
                        try:
                            wiki_vids = stock_backend.search_wikimedia_videos(st_q, per_page=6) or []
                            for wv in wiki_vids:
                                w_url = wv.get("url")
                                if w_url and not is_media_already_used(w_url, used):
                                    best_stock_v = wv
                                    break
                        except Exception:
                            pass
                    if not best_stock_v or not best_stock_v.get("url"):
                        break
                    v_url_try = best_stock_v["url"]
                    st_title_try = str(best_stock_v.get("title") or "")
                    if rag.try_claim_in_job_video(job_id=job_id, url=v_url_try, title=st_title_try, scene_id=eff_scene_id):
                        break
                    with _yt_url_lock:
                        used.update(extract_media_identity(v_url_try))
                        used.add(v_url_try)
                    best_stock_v = None
                if not best_stock_v or not best_stock_v.get("url"):
                    continue
                st_title = str(best_stock_v.get("title") or "")
                candidate_stock_urls = [best_stock_v["url"]]
                for alt_u in (best_stock_v.get("_vision_alternates") or []):
                    if alt_u and alt_u not in candidate_stock_urls and not is_media_already_used(alt_u, used):
                        candidate_stock_urls.append(alt_u)

                for cand_idx_u, v_url in enumerate(candidate_stock_urls[:3]):
                    if cand_idx_u > 0:
                        if not rag.try_claim_in_job_video(job_id=job_id, url=v_url, title=st_title, scene_id=eff_scene_id):
                            continue
                    with tempfile.TemporaryDirectory() as tmp_s:
                        raw_stock = str(Path(tmp_s) / "stock_raw.mp4")
                        media_agent.download_file(v_url, raw_stock)
                        if not Path(raw_stock).exists() or Path(raw_stock).stat().st_size <= 20000:
                            rag.release_in_job_video_claim(job_id=job_id, url=v_url, title=st_title, scene_id=eff_scene_id)
                            continue
                        # Kiểm tra độ phân giải >= 720p, độ nét >= 5.2 và độ sáng trên stock clip bản quyền (Pexels/Pixabay không có watermark kênh thứ 3)
                        raw_gate = VisualCritic.inspect_clip_before_concat(
                            raw_stock, min_short_edge=720, min_sharpness=5.2, check_watermark=False, allow_snow=True
                        )
                        if not raw_gate.get("passed", False):
                            logger.info(
                                f"🚫 [LicensedStockGate] Bỏ qua stock clip '{st_title[:45]}' vì không đạt chuẩn kỹ thuật: "
                                f"{'; '.join(raw_gate.get('issues') or [])}"
                            )
                            rag.release_in_job_video_claim(job_id=job_id, url=v_url, title=st_title, scene_id=eff_scene_id)
                            continue

                        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                        from app.utils.ffmpeg_helper import find_hook_peak_window, get_duration_ms, trim_video
                        raw_st_dur = max(0.0, get_duration_ms(raw_stock) / 1000.0)
                        min_st_dur = max(3.0, target_duration_sec)
                        st_start, st_peak_len = find_hook_peak_window(
                            raw_stock,
                            min_duration=min_st_dur,
                            max_duration=min(max_peak_duration_sec, max(min_st_dur, raw_st_dur)),
                        )
                        trim_video(
                            input_path=raw_stock,
                            duration_seconds=min_st_dur,
                            output_path=output_path,
                            width=1080,
                            height=1920,
                            fps=30,
                            apply_grading=True,
                            is_hook_scene=((clip_role or "").upper() == "HOOK"),
                            fair_use_transform=False,
                            start_offset_sec=st_start,
                            preserve_continuous_flow=True,
                        )
                        if Path(output_path).exists() and Path(output_path).stat().st_size > 20000:
                            ok_st_sig, _ = rag.verify_and_commit_in_job_clip(
                                job_id=job_id,
                                scene_id=eff_scene_id,
                                media_path=output_path,
                                url=v_url,
                                title=st_title,
                                is_continuation_slice=False,
                            )
                            if not ok_st_sig:
                                rag.release_in_job_video_claim(job_id=job_id, url=v_url, title=st_title, scene_id=eff_scene_id)
                                Path(output_path).unlink(missing_ok=True)
                                continue

                            # Nếu clip đã được LLM Link & Vision Auditor duyệt trực tiếp qua ảnh Thumbnail (score >= 6.5, khớp ngưỡng StockFetcher),
                            # không gọi lại Gemini Vision lần thứ 2 trên cùng 1 video để tiết kiệm 6.5 giây/cảnh!
                            pre_vis_score = float(best_stock_v.get("vision_score") or best_stock_v.get("curation_score") or 0.0)
                            is_pre_curated = bool(best_stock_v.get("vision_curated") or ("curation_score" in best_stock_v))
                            if is_pre_curated and pre_vis_score >= 6.5:
                                passed_st_vis = True
                                st_vis_score = pre_vis_score
                                st_vis_reason = "Approved by LLM Link & Thumbnail Vision Auditor"
                            else:
                                passed_st_vis, st_vis_score, st_vis_reason = _verify_short_clip_with_gemini_vision(
                                    video_path=output_path,
                                    topic=topic,
                                    narration=narration,
                                    subject_entity=subject_entity or topic,
                                    overall_script_query=overall_script_query or st_q,
                                    search_scope=effective_scope,
                                    video_title=st_title,
                                    allow_3d_cgi_monster=False,
                                    strict_no_fallback=True,
                                )
                            if not passed_st_vis:
                                logger.info(
                                    f"🚫 [LicensedStockVision] Gemini Vision từ chối stock clip '{st_title[:45]}' "
                                    f"({st_vis_score}/10: {st_vis_reason}) -> Thử ứng viên tiếp theo!"
                                )
                                rag.release_in_job_video_claim(job_id=job_id, url=v_url, title=st_title, scene_id=eff_scene_id)
                                Path(output_path).unlink(missing_ok=True)
                                continue

                            full_peak_path_st = None
                            if st_peak_len >= min_st_dur + 1.8 and raw_st_dur >= st_start + min_st_dur + 1.5:
                                cand_full_st = str(Path(output_path).parent / f"{Path(output_path).stem}_fullpeak.mp4")
                                try:
                                    trim_video(
                                        input_path=raw_stock,
                                        duration_seconds=st_peak_len,
                                        output_path=cand_full_st,
                                        width=1080,
                                        height=1920,
                                        fps=30,
                                        apply_grading=True,
                                        is_hook_scene=False,
                                        fair_use_transform=False,
                                        start_offset_sec=st_start,
                                        preserve_continuous_flow=True,
                                    )
                                    if Path(cand_full_st).exists() and Path(cand_full_st).stat().st_size > 25000:
                                        full_peak_path_st = cand_full_st
                                except Exception:
                                    pass

                            used.update(extract_media_identity(v_url))
                            record_used_media(
                                url=v_url,
                                query=st_q,
                                job_id=job_id,
                                source="pexels_stock_video",
                                media_path=output_path,
                            )
                            logger.info(
                                f"🎬 [LicensedStockVideo HD Fallback] Đã tải & duyệt video Pexels/Pixabay "
                                f"(scope={effective_scope}, peak={st_peak_len:.1f}s, vis={st_vis_score}/10) cho {clip_role}: '{st_title[:50]}'"
                            )
                            return {
                                "media_path": output_path,
                                "media_type": "hook_ready_video" if (clip_role or "").upper() == "HOOK" else "video",
                                "source_url": v_url,
                                "title": st_title or "HD Portrait Stock Video",
                                "window": f"{st_start:.1f}s-{st_start + (st_peak_len if full_peak_path_st else min_st_dur):.1f}s",
                                "vision_score": st_vis_score,
                                "search_scope": effective_scope,
                                "full_peak_path": full_peak_path_st,
                                "full_peak_duration_sec": round(st_peak_len if full_peak_path_st else min_st_dur, 2),
                                "scene_clip_duration_sec": round(min_st_dur, 2),
                            }
        except Exception as e_st:
            logger.debug(f"[LicensedStockVideo] Lỗi tìm stock video có giấy phép: {e_st}")

    return None
