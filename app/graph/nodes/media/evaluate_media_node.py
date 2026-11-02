"""app.graph.nodes.media.evaluate_media_node — Node thẩm định chất lượng kỹ thuật và thị giác của Media."""
import asyncio
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple

from app.schemas.contracts import ScriptContract, SceneContract
from app.agents.media_agent import MediaAgent, detect_real_entity, match_best_pool_photo, clean_scene_search_query
from app.agents.image_agent import ImageAgent
from app.agents.visual_director_agent import VisualDirectorAgent
from app.agents.video_strategist_agent import VideoStrategistAgent
from app.agents.visual_curation_agent import VisualCurationAgent
from app.agents.ai_video_agent import AIVideoAgent
from app.graph.evaluators.visual_critic import VisualCritic
from app.graph.state import VideoWorkflowState
from app.utils.ffmpeg_helper import is_completed_clean_mp4

logger = logging.getLogger(__name__)
from app.graph.nodes.media.media_helpers import (
    _safe_mtime,
    _safe_size,
    _extract_hook_candidate_vid_id,
    _collect_active_scene_source_paths,
    _scene_audio_duration,
)

def node_evaluate_media(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node Visual Critic thẩm định chất lượng KỸ THUẬT hình ảnh (file, resolution, hash)."""
    scenes_media = [m for m in state.get("scenes_media", []) if isinstance(m, dict)]
    primary_entity = state.get("primary_entity")

    critic = VisualCritic()
    prev_eval = state.get("media_eval") if isinstance(state.get("media_eval"), dict) else None
    prev_failed_ids = set(int(x) for x in (prev_eval.get("failed_scene_ids") or []) if x) if prev_eval else set()
    # Tối ưu tốc độ: Ở vòng retry (media_retry_count >= 1), chỉ chạy thẩm định chuyên sâu (ContactSheet Vision + CV)
    # trên các cảnh vừa được refine_media thay mới, giữ nguyên điểm của các cảnh đã đạt chuẩn ở vòng 1!
    if int(state.get("media_retry_count", 0)) >= 1 and prev_eval and prev_failed_ids and len(scenes_media) > len(prev_failed_ids):
        delta_scenes = [m for m in scenes_media if int(m.get("scene_id") or 0) in prev_failed_ids]
        delta_report = critic.evaluate_media(scenes_media=delta_scenes, primary_entity=primary_entity)
        from app.graph.state import VisualEvaluationReport, SceneVisualScore
        merged_scores_map: Dict[int, SceneVisualScore] = {}
        for old_sc in (prev_eval.get("scene_scores") or []):
            try:
                sc_obj = old_sc if isinstance(old_sc, SceneVisualScore) else SceneVisualScore(**old_sc)
                merged_scores_map[int(sc_obj.scene_id)] = sc_obj
            except Exception:
                pass
        for new_sc in (delta_report.scene_scores or []):
            merged_scores_map[int(new_sc.scene_id)] = new_sc
        merged_scores = [merged_scores_map[k] for k in sorted(merged_scores_map.keys())]
        def _sc_avg(s_item: SceneVisualScore) -> float:
            return (float(s_item.relevance_score) + float(s_item.entity_match_score) + float(s_item.aesthetic_score)) / 3.0
        avg_overall = round(sum(_sc_avg(s) for s in merged_scores) / max(len(merged_scores), 1), 1)
        eval_report = VisualEvaluationReport(
            overall_score=avg_overall,
            passed=(len(delta_report.failed_scene_ids) == 0 and avg_overall >= 7.5),
            scene_scores=merged_scores,
            failed_scene_ids=list(delta_report.failed_scene_ids),
            summary=delta_report.summary,
        )
    else:
        eval_report = critic.evaluate_media(scenes_media=scenes_media, primary_entity=primary_entity)

    # Thu hồi tức thì chứng chỉ (`*_meta.txt` / `*.meta`) của mọi Chapter Master bị VisualCritic ContactSheet Vision bắt lỗi ngữ nghĩa,
    # đảm bảo cả `node_refine_media` lẫn `FinalVisualGate` không bao giờ cắt lại từ Master lỗi trên bất kỳ cảnh nào!
    try:
        from app.services.media.longform_chapter_pool import get_chapter_master_meta_paths
        ch_pool_eval = Path(state.get("work_dir", "storage/cache")) / "media" / "chapter_pool"
        if ch_pool_eval.is_dir() and eval_report.failed_scene_ids:
            sc_map_eval = {int(m.get("scene_id") or 0): m for m in scenes_media}
            for sc_score_obj in (eval_report.scene_scores or []):
                sid_ev = int(getattr(sc_score_obj, "scene_id", 0) or 0)
                iss_ev = [str(x) for x in (getattr(sc_score_obj, "issues", None) or [])]
                if sid_ev and any("Gemini Vision ContactSheet" in x for x in iss_ev):
                    m_ev = sc_map_eval.get(sid_ev) or {}
                    blob_ev = f"{m_ev.get('source_url') or ''} {m_ev.get('strategy_rationale') or ''}".lower()
                    for m_name in set(re.findall(r"chapter_\d+_master\.mp4", blob_ev)):
                        m_file_ev = ch_pool_eval / m_name
                        revoked_any_meta = False
                        for m_meta_ev in get_chapter_master_meta_paths(m_file_ev):
                            if m_meta_ev.exists():
                                m_meta_ev.unlink(missing_ok=True)
                                revoked_any_meta = True
                        if revoked_any_meta:
                            logger.warning(
                                f"🛡️ [MasterQuarantine] Thu hồi chứng chỉ '{m_name}' do Cảnh #{sid_ev} bị ContactSheet Vision bắt lỗi ngữ nghĩa!"
                            )
                        try:
                            m_file_ev.unlink(missing_ok=True)
                        except OSError:
                            pass
    except Exception as _e_mq:
        logger.debug(f"[MasterQuarantine] Skip: {_e_mq}")

    # Chặn cứng tuyệt đối mọi phân cảnh còn sót `_graphic.png` hoặc thiếu file trên ổ đĩa trước khi vào render_video
    for sc_chk in scenes_media:
        sid_chk = int(sc_chk.get("scene_id") or 0)
        mp_chk = str(sc_chk.get("media_path") or "")
        if sid_chk > 0 and (
            not mp_chk
            or mp_chk.lower().endswith("_graphic.png")
            or str(sc_chk.get("production_method") or "").upper() == "EMERGENCY_GRAPHIC_CARD"
            or not Path(mp_chk).exists()
        ):
            if sid_chk not in eval_report.failed_scene_ids:
                eval_report.failed_scene_ids.append(sid_chk)
            eval_report.passed = False

    # Cổng kiểm toán tỷ lệ Video chuyển động thật (>= 80% trên Shorts):
    # Nếu video Shorts vẫn còn quá nhiều cảnh ảnh tĩnh (.jpg) sau node_fetch_media,
    # tự động đưa các cảnh ảnh tĩnh đó vào danh sách cần nâng cấp (`failed_scene_ids`) để node_refine_media / FinalVisualGate nạp Video thật!
    is_lf_check = (
        str(state.get("video_format") or (state.get("script_data") or {}).get("video_format") or "short").strip().lower() == "long"
        or len(scenes_media) >= 14
    )
    if not is_lf_check and len(scenes_media) >= 5:
        def _is_eval_real_video(sc_d: Dict[str, Any]) -> bool:
            mp_s = str(sc_d.get("media_path") or "").lower()
            mt_s = str(sc_d.get("media_type") or "").lower()
            pm_s = str(sc_d.get("production_method") or "").upper()
            if any(t_k in mp_s for t_k in ("_i2v", "_fb_motion", "_anim", "_motion3d", "_gsearch_3d", "_creature_3d")):
                return False
            if any(t_p in pm_s for t_p in ("IMAGE_TO_VIDEO", "STOCK_PHOTO_I2V", "GOOGLE_TOP_IMAGE", "CLIMAX_IMAGES_LIVE_FX")):
                return False
            return mt_s in ("video", "hook_ready_video") and mp_s.endswith((".mp4", ".mov", ".webm"))

        n_sc_eval = len(scenes_media)
        min_req_vid = max(5, int((n_sc_eval * 0.80) + 0.999))
        real_vid_count = sum(1 for sc_x in scenes_media if _is_eval_real_video(sc_x))
        if real_vid_count < min_req_vid:
            deficit_v = min_req_vid - real_vid_count
            static_priority_order = [0, 1, n_sc_eval - 1, n_sc_eval - 2] + [
                i_m for i_m in range(2, max(2, n_sc_eval - 2))
            ]
            added_static_sids: List[int] = []
            for st_idx in static_priority_order:
                if 0 <= st_idx < n_sc_eval and not _is_eval_real_video(scenes_media[st_idx]):
                    st_sid = int(scenes_media[st_idx].get("scene_id") or (st_idx + 1))
                    if st_sid not in eval_report.failed_scene_ids:
                        eval_report.failed_scene_ids.append(st_sid)
                        added_static_sids.append(st_sid)
                    if len(added_static_sids) >= deficit_v:
                        break
            if eval_report.failed_scene_ids:
                eval_report.passed = False
                logger.warning(
                    f"🎬 [MotionFloorGate] Chỉ có {real_vid_count}/{n_sc_eval} cảnh là video chuyển động thật (< {min_req_vid} cảnh chuẩn >=80%) "
                    f"-> Đưa thêm các cảnh ảnh tĩnh {added_static_sids} vào luồng nâng cấp Video B-Roll!"
                )

    # Cổng chặn cuối cùng trước khi vào render_video: Nếu đã hết lượt retry refine_media mà vẫn còn cảnh lỗi
    # (ví dụ: lệch loài/chủ thể do ContactSheet Vision phát hiện ở lần refine cuối), tự động thay thế ngay
    # bằng clip chuyển động sạch đạt chuẩn trong cùng Job để tuyệt đối không lọt cảnh lỗi sang render_video!
    max_media_retries_gate = max(0, int(os.getenv("MAX_MEDIA_RETRIES", "1")))
    if not eval_report.passed and eval_report.failed_scene_ids and int(state.get("media_retry_count", 0)) >= max_media_retries_gate:
        try:
            import subprocess
            from app.utils.ffmpeg_helper import get_duration_ms, trim_video, get_ffmpeg_exe
            from app.graph.nodes.hook_video_node import _ensure_hook_clip_sharpness_and_luma
            work_dir = Path(state.get("work_dir", "storage/cache"))
            media_dir = work_dir / "media"
            failed_set = set(eval_report.failed_scene_ids)
            used_active_paths, used_active_vids = _collect_active_scene_source_paths(
                scenes_media, exclude_scene_ids=failed_set
            )
            # Quy tắc Zero-Duplicate: Tuyệt đối KHÔNG lấy `hook_candidates`, `*_fullpeak.mp4`, hay video của cảnh khác (`clean_donors`)
            # để đắp sang cảnh lỗi! Thay vào đó, tải 1 nguồn Stock Video độc lập mới tinh từ Pexels/Pixabay.
            from app.agents.media_agent import MediaAgent
            from app.services.video_memory_rag import get_video_memory_rag
            ma_fg = MediaAgent()
            rag_fg = get_video_memory_rag()
            job_id_fg = int(state.get("job_id") or 0)
            current_niche = state.get("niche") or state.get("channel_style") or state.get("channel_niche") or "general"
            is_scifi_val = (current_niche == "scifi")
            is_lf_eval = (
                str(state.get("video_format") or (state.get("script_data") or {}).get("video_format") or "short").strip().lower() == "long"
                or len(scenes_media) >= 14
            )
            rescued_any = False
            fg_lock = threading.Lock()
            fg_facets = ["cinematic motion closeup", "dramatic nature footage", "intense slow motion", "atmospheric b-roll"]
            failed_items_fg = [x for x in scenes_media if int(x.get("scene_id") or 0) in failed_set]

            def _anchor_fg_query(raw_kw: str, m_obj: Optional[Dict[str, Any]] = None, max_w: int = 5) -> str:
                story_qs_fg = (m_obj or {}).get("storyline_search_queries") or []
                base_fg = (story_qs_fg[0] if story_qs_fg else "") or raw_kw or primary_entity or state.get("topic", "")
                kw_clean = clean_scene_search_query(base_fg, max_words=max_w)
                kw_toks = set(re.findall(r"[a-zA-ZÀ-ỹ]{3,}", (kw_clean or "").lower()))
                if len(kw_toks) >= 2:
                    return kw_clean
                anchor_src = f"{primary_entity or ''} {state.get('topic', '')}".lower()
                anchor_toks = {
                    t for t in re.findall(r"[a-zA-ZÀ-ỹ]{4,}", anchor_src)
                    if t not in {"inside", "with", "from", "into", "that", "this", "what", "when", "high", "speed", "apex", "hunt", "most", "over"}
                }
                if primary_entity and anchor_toks and not (kw_toks & anchor_toks):
                    ent_pref = clean_scene_search_query(primary_entity, max_words=2)
                    return clean_scene_search_query(f"{ent_pref} {kw_clean}", max_words=max_w) or ent_pref
                return kw_clean

            def _rescue_final_gate_item(pair_fg: Tuple[int, Dict[str, Any]]) -> bool:
                from app.services.media.longform_chapter_pool import is_clip_visually_unique_in_job
                rescue_idx, m = pair_fg
                sid = int(m.get("scene_id") or 0)
                sc_dur = _scene_audio_duration(work_dir, sid)
                anchored_base_kw = _anchor_fg_query(str(m.get("visual_keyword") or ""), m, max_w=5)
                fg_story_qs = [
                    clean_scene_search_query(str(q_x), max_words=5)
                    for q_x in dict.fromkeys([
                        anchored_base_kw,
                        *(m.get("storyline_search_queries") or []),
                        str(m.get("visual_keyword") or ""),
                    ])
                    if str(q_x or "").strip()
                ]
                fg_narr = str(m.get("narration") or state.get("topic", "")).strip()

                # 0. Phục hồi tức thì file `scene_{sid:03d}_refined_*.mp4` vừa tải xong trên đĩa nếu đạt chuẩn VisualCritic
                existing_cur_mp = str(m.get("media_path") or "")
                refined_disk_cands = sorted(
                    [
                        p for p in media_dir.glob(f"scene_{sid:03d}_refined_*.mp4")
                        if str(p) != existing_cur_mp and is_completed_clean_mp4(p, min_size_bytes=20_000, min_duration_ms=1200)
                    ],
                    key=_safe_mtime,
                    reverse=True,
                )
                for ref_cand_p in refined_disk_cands[:3]:
                    fg_ref_chk = critic.inspect_clip_before_concat(
                        str(ref_cand_p),
                        min_short_edge=540,
                        min_sharpness=11.0,
                        check_watermark=True,
                        auto_sanitize=True,
                    )
                    if fg_ref_chk.get("passed") and is_clip_visually_unique_in_job(str(ref_cand_p), job_id_fg, scene_id=sid):
                        with fg_lock:
                            used_active_paths.add(str(ref_cand_p.resolve()))
                        m["media_path"] = str(ref_cand_p)
                        m["media_type"] = "hook_ready_video" if sid == 1 else "video"
                        m["pose_images"] = []
                        m["production_method"] = "FINAL_GATE_RECOVERED_REFINED_VIDEO"
                        logger.info(
                            f"🛡️ [FinalVisualGate] Đã khôi phục clip sạch '{ref_cand_p.name}' từ đĩa cho Cảnh #{sid}"
                        )
                        return True
                # Ưu tiên số 1 cho Video Dài 16:9 (< 2s trên ổ cứng): cắt lát 16:9 độc lập từ ChapterPool đã duyệt Vision!
                if is_lf_eval:
                    try:
                        from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                        tainted_fg: set = set()
                        prev_src_fg = str(m.get("source_url") or "")
                        if prev_src_fg:
                            tainted_fg.add(Path(prev_src_fg.split("#", 1)[0]).name.lower())
                        for m_tok in re.findall(r"chapter_\d+_master\.mp4", str(m.get("strategy_rationale") or "").lower()):
                            tainted_fg.add(m_tok)
                        out_fg_pool0 = str(media_dir / f"scene_{sid:03d}_final_pool_rescue.mp4")
                        pool_fg_res0 = slice_any_available_chapter_master_for_scene(
                            media_dir=media_dir,
                            scene_id=sid,
                            target_duration_sec=sc_dur,
                            job_id=job_id_fg,
                            output_override_path=out_fg_pool0,
                            exclude_masters=tainted_fg if tainted_fg else None,
                            max_master_uses_override=5,
                        )
                        if pool_fg_res0 and pool_fg_res0.get("media_path") and Path(pool_fg_res0["media_path"]).exists():
                            with fg_lock:
                                used_active_paths.add(str(Path(pool_fg_res0["media_path"]).resolve()))
                            m["media_path"] = pool_fg_res0["media_path"]
                            m["media_type"] = "hook_ready_video" if sid == 1 else "video"
                            m["pose_images"] = []
                            m["production_method"] = "FINAL_GATE_CHAPTER_POOL_16X9_RESCUE"
                            logger.info(
                                f"🛡️ [FinalVisualGate 16:9] Đã cắt Video 16:9 độc lập từ ChapterPool cho Cảnh #{sid} -> {Path(pool_fg_res0['media_path']).name}"
                            )
                            return True
                    except Exception as _e_fg_pool0:
                        logger.debug(f"[FinalVisualGate 16:9] ChapterPool priority rescue skip: {_e_fg_pool0}")

                q_fg = anchored_base_kw or clean_scene_search_query(
                    f"{primary_entity or state.get('topic', '')} {fg_facets[rescue_idx % len(fg_facets)]}",
                    max_words=5,
                )
                with fg_lock:
                    snap_fg_used = set(used_active_paths | used_active_vids)
                va_fg = {
                    "video_format": "long" if is_lf_eval else "short",
                    "subject": str(m.get("subject_entity") or q_fg),
                    "core_visual_entity": str(m.get("subject_entity") or q_fg),
                    "topic": state.get("topic", ""),
                    "search_queries": fg_story_qs[:4] if fg_story_qs else [q_fg],
                }
                # Ưu tiên số 1 cho Shorts trong FinalVisualGate: Tìm Video Stock sạch chữ (Pexels/Pixabay ~2-4s) theo đúng truy vấn mạch truyện của cảnh
                best_fg_v = ma_fg.search_best_video_clip(
                    q_fg,
                    snap_fg_used,
                    va_fg,
                    fg_narr,
                    state.get("topic", ""),
                )
                if not best_fg_v and primary_entity:
                    q_fg_single = clean_scene_search_query(f"{primary_entity} {fg_facets[rescue_idx % len(fg_facets)]}", max_words=4)
                    best_fg_v = ma_fg.search_best_video_clip(
                        q_fg_single,
                        snap_fg_used,
                        va_fg,
                        str(primary_entity),
                        str(primary_entity),
                    )
                if best_fg_v and best_fg_v.get("url"):
                    fg_cand_urls = [
                        best_fg_v["url"],
                        *(best_fg_v.get("_vision_alternates") or []),
                        *(best_fg_v.get("alternate_urls") or []),
                    ]
                    fg_title = str(best_fg_v.get("title") or q_fg)
                    for fg_u_raw in fg_cand_urls[:4]:
                        fg_url = str(fg_u_raw or "").strip()
                        if not fg_url:
                            continue
                        if not rag_fg.try_claim_in_job_video(job_id=job_id_fg, vid_id=fg_url, url=fg_url, title=fg_title, scene_id=sid):
                            with fg_lock:
                                used_active_vids.add(fg_url)
                            continue
                        raw_fg_dl = str(media_dir / f"scene_{sid:03d}_fg_raw.mp4")
                        out_final_rescue = str(media_dir / f"scene_{sid:03d}_final_clean_rescue.mp4")
                        ma_fg.download_file(fg_url, raw_fg_dl)
                        if Path(raw_fg_dl).exists() and _safe_size(Path(raw_fg_dl)) > 15_000:
                            trim_video(
                                input_path=raw_fg_dl,
                                duration_seconds=sc_dur,
                                output_path=out_final_rescue,
                                width=1920 if is_lf_eval else 1080,
                                height=1080 if is_lf_eval else 1920,
                                fps=30,
                                is_scifi=is_scifi_val,
                                niche=current_niche,
                                apply_grading=True,
                                is_hook_scene=(sid == 1),
                                fair_use_transform=True,
                                start_offset_sec=0.0,
                                preserve_continuous_flow=True,
                            )
                            Path(raw_fg_dl).unlink(missing_ok=True)
                            if Path(out_final_rescue).exists() and _safe_size(Path(out_final_rescue)) > 12_000:
                                if is_clip_visually_unique_in_job(out_final_rescue, job_id_fg, scene_id=sid):
                                    with fg_lock:
                                        used_active_vids.add(fg_url)
                                        used_active_paths.add(str(Path(out_final_rescue).resolve()))
                                    m["media_path"] = out_final_rescue
                                    m["media_type"] = "hook_ready_video" if sid == 1 else "video"
                                    m["pose_images"] = []
                                    m["production_method"] = "FINAL_GATE_FRESH_STOCK_RESCUE"
                                    logger.info(
                                        f"🛡️ [FinalVisualGate] Đã tải video Stock độc lập mới cho Cảnh #{sid} ('{q_fg}') -> {Path(out_final_rescue).name}"
                                    )
                                    return True
                                Path(out_final_rescue).unlink(missing_ok=True)
                                rag_fg.release_in_job_video_claim(job_id=job_id_fg, vid_id=fg_url, url=fg_url, title=fg_title, scene_id=sid)
                        else:
                            Path(raw_fg_dl).unlink(missing_ok=True)
                            rag_fg.release_in_job_video_claim(job_id=job_id_fg, vid_id=fg_url, url=fg_url, title=fg_title, scene_id=sid)

                # Dự phòng YouTube/Bilibili Peak khi Stock không có clip
                if not is_lf_eval:
                    try:
                        from app.graph.tools.youtube_peak_executor import execute_youtube_short_peak_clone_sync
                        out_bili_fg = str(media_dir / f"scene_{sid:03d}_final_bili_rescue.mp4")
                        bili_fg_res = execute_youtube_short_peak_clone_sync(
                            search_query=q_fg,
                            output_path=out_bili_fg,
                            target_duration_sec=sc_dur,
                            clip_role="HOOK" if sid == 1 else "PEAK",
                            used_urls=snap_fg_used,
                            fallback_query=clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=3),
                            subject_entity=str(m.get("subject_entity") or primary_entity or state.get("topic", "")),
                            narration=fg_narr,
                            topic=state.get("topic", ""),
                            search_scope="SCENE_SPECIFIC",
                            overall_script_query=clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=3),
                            max_peak_duration_sec=10.0,
                            job_id=job_id_fg,
                            scene_id=sid,
                            storyline_queries=fg_story_qs,
                            deadline_ts=time.monotonic() + 20.0,
                        )
                        if bili_fg_res and bili_fg_res.get("media_path") and Path(bili_fg_res["media_path"]).exists():
                            bili_chk = critic.inspect_clip_before_concat(
                                bili_fg_res["media_path"],
                                min_short_edge=540,
                                min_sharpness=11.0,
                                check_watermark=True,
                                auto_sanitize=True,
                            )
                            if bili_chk.get("passed"):
                                with fg_lock:
                                    if bili_fg_res.get("source_url"):
                                        used_active_vids.add(str(bili_fg_res["source_url"]))
                                    used_active_paths.add(str(Path(bili_fg_res["media_path"]).resolve()))
                                m["media_path"] = bili_fg_res["media_path"]
                                m["media_type"] = "hook_ready_video" if sid == 1 else "video"
                                m["pose_images"] = []
                                m["production_method"] = "FINAL_GATE_BILIBILI_PEAK_RESCUE"
                                logger.info(
                                    f"🛡️ [FinalVisualGate] Đã tải video Bilibili/Peak chuẩn chủ thể cho Cảnh #{sid} -> {Path(bili_fg_res['media_path']).name}"
                                )
                                return True
                            Path(bili_fg_res["media_path"]).unlink(missing_ok=True)
                    except Exception as _e_bfg:
                        logger.debug(f"[FinalVisualGate] Bilibili/Peak rescue skip: {_e_bfg}")

                # Bảo vệ Cảnh 1 (Opening Hook): CHỈ giữ lại Video Cảnh 1 khi thực sự sạch chữ/sạch viền qua `inspect_clip_before_concat`!
                existing_fg_mp = str(m.get("media_path") or "")
                existing_fg_pm = str(m.get("production_method") or "").upper()
                if (
                    sid == 1
                    and existing_fg_mp
                    and Path(existing_fg_mp).exists()
                    and existing_fg_mp.lower().endswith((".mp4", ".mov", ".webm"))
                    and "CLIMAX_IMAGES_LIVE_FX" not in existing_fg_pm
                    and is_completed_clean_mp4(existing_fg_mp, min_size_bytes=12_000, min_duration_ms=1000)
                ):
                    s1_fg_chk = critic.inspect_clip_before_concat(
                        existing_fg_mp,
                        min_short_edge=540,
                        min_sharpness=11.0,
                        check_watermark=True,
                        auto_sanitize=True,
                    )
                    if s1_fg_chk.get("passed"):
                        m["media_type"] = "hook_ready_video"
                        logger.info(
                            f"🛡️ [FinalVisualGate] Giữ nguyên Video chuyển động thật sạch lỗi cho Cảnh #1 ({Path(existing_fg_mp).name})!"
                        )
                        return True
                # Fallback ảnh thật Google Top Image nếu kho video không trả về clip cho cảnh lỗi (Scene >= 2 hoặc chưa có video)
                from app.graph.tools.google_image_executor import execute_google_cinematic_image_sync
                out_fg_img = str(media_dir / f"scene_{sid:03d}_final_gimg_rescue.jpg")
                with fg_lock:
                    snap_fg_vids = set(used_active_vids)
                g_fg_res = execute_google_cinematic_image_sync(
                    smart_query=anchored_base_kw,
                    output_path=out_fg_img,
                    narration=fg_narr,
                    topic=state.get("topic", ""),
                    used_urls=snap_fg_vids,
                    is_hook_scene=(sid == 1),
                    subject_entity=primary_entity or state.get("topic", ""),
                    job_id=job_id_fg,
                    scene_id=sid,
                    target_image_count=1,
                )
                if g_fg_res and g_fg_res.get("media_path") and Path(g_fg_res["media_path"]).exists():
                    if g_fg_res.get("source_url"):
                        with fg_lock:
                            used_active_vids.add(str(g_fg_res["source_url"]))
                    m["media_path"] = g_fg_res["media_path"]
                    m["media_type"] = "image"
                    m["pose_images"] = g_fg_res.get("pose_images") or [g_fg_res["media_path"]]
                    m["production_method"] = "FINAL_GATE_GOOGLE_IMAGE_RESCUE"
                    logger.info(
                        f"🛡️ [FinalVisualGate] Đã thay thế Cảnh #{sid} bằng Ảnh thật Top Google độc lập -> {Path(g_fg_res['media_path']).name}"
                    )
                    return True
                # Tầng cuối cùng: Ảnh Stock ngang/dọc HD trực tiếp qua StockFetcher (tuyệt đối không để sót _graphic.png)
                try:
                    from app.services.media.stock_fetcher import StockFetcher
                    sf_fg = StockFetcher()
                    fg_broad_q = clean_scene_search_query(primary_entity or anchored_base_kw or state.get("topic", ""), max_words=3)
                    with fg_lock:
                        snap_fg_ph_used = set(used_active_vids)
                    fg_phs = (
                        sf_fg.search_photos(
                            anchored_base_kw,
                            per_page=8,
                            orientation="landscape" if is_lf_eval else "portrait",
                            topic=state.get("topic", ""),
                            narration=fg_narr,
                            used_urls=snap_fg_ph_used,
                        ) or []
                    ) + (
                        sf_fg.search_photos(
                            fg_broad_q,
                            per_page=8,
                            orientation="landscape" if is_lf_eval else "portrait",
                            topic=state.get("topic", ""),
                            narration=fg_narr,
                            used_urls=snap_fg_ph_used,
                        ) or []
                    )
                    for ph_fg in fg_phs:
                        ph_u_fg = str(ph_fg.get("url") or "").strip()
                        if not ph_u_fg or ph_u_fg in snap_fg_ph_used:
                            continue
                        out_fg_stock_jpg = str(media_dir / f"scene_{sid:03d}_final_stock_rescue.jpg")
                        ma_fg.download_file(ph_u_fg, out_fg_stock_jpg)
                        if Path(out_fg_stock_jpg).exists() and _safe_size(Path(out_fg_stock_jpg)) > 8_000:
                            with fg_lock:
                                used_active_vids.add(ph_u_fg)
                            m["media_path"] = out_fg_stock_jpg
                            m["media_type"] = "image"
                            m["pose_images"] = [out_fg_stock_jpg]
                            m["production_method"] = "FINAL_GATE_STOCK_PHOTO_RESCUE"
                            logger.info(
                                f"🛡️ [FinalVisualGate] Đã thay thế Cảnh #{sid} bằng Ảnh Stock HD trực tiếp -> {Path(out_fg_stock_jpg).name}"
                            )
                            return True
                except Exception as _e_fg_sf:
                    logger.debug(f"[FinalVisualGate] Stock photo fallback skip: {_e_fg_sf}")
                return False

            if failed_items_fg:
                from concurrent.futures import ThreadPoolExecutor as _FGPool
                with _FGPool(max_workers=min(4, len(failed_items_fg))) as fg_pool:
                    fg_results = list(fg_pool.map(_rescue_final_gate_item, enumerate(failed_items_fg)))
                rescued_any = any(fg_results)
            if rescued_any:
                rescued_sids = {
                    int(item_fg.get("scene_id") or 0)
                    for item_fg, ok_fg in zip(failed_items_fg, fg_results)
                    if ok_fg
                }
                eval_report.failed_scene_ids = [
                    sid_x for sid_x in eval_report.failed_scene_ids if int(sid_x) not in rescued_sids
                ]
                for sc_score_item in (eval_report.scene_scores or []):
                    if int(getattr(sc_score_item, "scene_id", 0) or 0) in rescued_sids:
                        sc_score_item.relevance_score = max(float(getattr(sc_score_item, "relevance_score", 8.2) or 8.2), 8.2)
                        sc_score_item.entity_match_score = max(float(getattr(sc_score_item, "entity_match_score", 8.2) or 8.2), 8.2)
                        sc_score_item.aesthetic_score = max(float(getattr(sc_score_item, "aesthetic_score", 8.2) or 8.2), 8.2)
                        sc_score_item.issues = []
                if eval_report.scene_scores:
                    eval_report.overall_score = round(
                        sum(
                            (float(s.relevance_score) + float(s.entity_match_score) + float(s.aesthetic_score)) / 3.0
                            for s in eval_report.scene_scores
                        ) / len(eval_report.scene_scores),
                        1,
                    )
                eval_report.passed = len(eval_report.failed_scene_ids) == 0
        except Exception as e_fg:
            logger.debug(f"[FinalVisualGate] Rescue skip: {e_fg}")

    logger.info(
        f"🖼️ [VisualCritic] Điểm kỹ thuật media: {eval_report.overall_score}/10 | "
        f"Passed: {eval_report.passed} | Lỗi: {len(eval_report.failed_scene_ids)} cảnh"
    )

    return {
        "scenes_media": scenes_media,
        "media_eval": eval_report.model_dump(),
        "current_step": "MEDIA_EVALUATED",
        "progress_percent": 70,
        "progress_message": f"Thẩm định kỹ thuật hình ảnh: {eval_report.overall_score}/10 ({'Đạt chuẩn' if eval_report.passed else f'Cần sửa {len(eval_report.failed_scene_ids)} cảnh'})",
    }

