"""app.graph.nodes.media.post_gather_rescue — Khử trùng lặp nội bộ và đảm bảo tỷ lệ Video chuyển động thật sau khi gather."""
import asyncio
import json as _json
import logging
import threading
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Set

from app.schemas.contracts import ScriptContract
from app.agents.media_agent import MediaAgent, clean_scene_search_query
from app.agents.image_agent import ImageAgent
from app.graph.state import VideoWorkflowState
from app.graph.nodes.media.media_helpers import (
    _safe_size,
    _extract_hook_candidate_vid_id,
    _collect_active_scene_source_paths,
)
from app.graph.nodes.media.longform_broll_fetcher import _fetch_longform_16x9_broll_clip_sync

logger = logging.getLogger(__name__)


async def run_post_gather_media_rescues(
    *,
    state: VideoWorkflowState,
    script: ScriptContract,
    scenes_media: List[Dict[str, Any]],
    work_dir: Path,
    media_dir: Path,
    job_id: int,
    channel_id_val: str,
    primary_entity: Optional[str],
    current_niche: str,
    is_long_form: bool,
    is_scifi_val: bool,
    media_agent: MediaAgent,
    image_agent: ImageAgent,
    rag: Any,
    strategy_map: Dict[int, Dict[str, Any]],
    used_pool_urls: Set[str],
    longform_used_bvids: Set[str],
    longform_bvid_lock: threading.Lock,
) -> List[Dict[str, Any]]:
    # Đồng bộ toàn bộ source_url đã dùng ở các luồng song song vào used_pool_urls
    for sc_init in scenes_media:
        if isinstance(sc_init, dict) and sc_init.get("source_url"):
            used_pool_urls.add(str(sc_init["source_url"]).strip())

    # ── Bước A.0: CỔNG RÀ SOÁT ĐỒNG BỘ CHỐNG TRÙNG VIDEO LIÊN TIẾP TRONG CÙNG 1 JOB (POST-GATHER INTRA-VIDEO DEDUP GATE) ──
    try:
        from app.services.media.longform_chapter_pool import is_clip_visually_unique_in_job as _post_gather_uniq
        seen_real_video_paths: Set[str] = set()
        for idx_pg, sc_pg in enumerate(scenes_media):
            if not isinstance(sc_pg, dict):
                continue
            sid_pg = int(sc_pg.get("scene_id") or (idx_pg + 1))
            mp_pg = str(sc_pg.get("media_path") or "").strip()
            if not mp_pg or not mp_pg.lower().endswith((".mp4", ".mov", ".webm")) or not Path(mp_pg).exists():
                continue
            abs_mp_pg = str(Path(mp_pg).resolve())
            is_dup_pg = False
            if abs_mp_pg in seen_real_video_paths:
                is_dup_pg = True
            elif not _post_gather_uniq(abs_mp_pg, job_id, scene_id=sid_pg):
                is_dup_pg = True
            if is_dup_pg and sid_pg > 1:
                logger.warning(
                    f"🚫 [Post-Gather Intra-Video Dedup] Phát hiện Cảnh #{sid_pg} ('{Path(mp_pg).name}') "
                    f"bị trùng lặp khung hình với cảnh trước -> Thu hồi ngay để tải video mới độc lập ở Bước B!"
                )
                sc_pg["media_path"] = ""
                sc_pg["media_type"] = "image"
                sc_pg["production_method"] = "DUPLICATE_DEMOTED_FOR_RESCUE"
            else:
                seen_real_video_paths.add(abs_mp_pg)
    except Exception as _e_pg_dedup:
        logger.debug(f"[Post-Gather Intra-Video Dedup] warning: {_e_pg_dedup}")

    scenes_audio_list = state.get("scenes_audio", [])

    def _get_slot_dur(slot_idx: int) -> float:
        sid = slot_idx + 1
        if scenes_audio_list:
            sc_a = next((a for a in scenes_audio_list if isinstance(a, dict) and int(a.get("scene_id") or 0) == sid), None)
            if sc_a and sc_a.get("audio_duration_ms"):
                return max(float(sc_a["audio_duration_ms"]) / 1000.0, 1.2)
        if 0 <= slot_idx < len(script.scenes):
            return max(float(getattr(script.scenes[slot_idx], "estimated_seconds", 4.0) or 4.0), 1.2)
        return 3.8

    # ── Bước B: CỔNG DUY NHẤT BẢO ĐẢM 100% VIDEO CHUYỂN ĐỘNG Ở 5-7S ĐẦU (CẢNH 1, 2), CUỐI VIDEO & BÙ CẢNH RỖNG ──
    try:
        def _is_empty_or_graphic(sc_dict: Any) -> bool:
            if not isinstance(sc_dict, dict):
                return True
            mp_raw = str(sc_dict.get("media_path") or "").strip()
            return (
                not mp_raw
                or mp_raw.lower().endswith("_graphic.png")
                or not Path(mp_raw).exists()
                or _safe_size(Path(mp_raw)) < 2500
            )

        def _is_real_video_slot(sc_dict: Any) -> bool:
            if not isinstance(sc_dict, dict) or _is_empty_or_graphic(sc_dict):
                return False
            mt = str(sc_dict.get("media_type") or "image").lower()
            mp = str(sc_dict.get("media_path") or "").lower()
            pm = str(sc_dict.get("production_method") or "").upper()
            vs = str(sc_dict.get("visual_strategy") or "").upper()
            if any(tag in mp for tag in ("_i2v", "_fb_motion", "_anim", "_motion3d", "_gsearch_3d", "_creature_3d")):
                return False
            if any(
                tag in pm
                for tag in (
                    "CLIMAX_IMAGES_LIVE_FX",
                    "IMAGE_TO_VIDEO",
                    "STOCK_PHOTO_I2V",
                    "GOOGLE_TOP_IMAGE",
                    "GEMINI_SEARCH_3D",
                    "CREATURE_ART",
                    "REFINED_IMAGE_TO_VIDEO",
                    "REFINED_CLEAN_MULTI_IMAGE",
                )
            ):
                return False
            if vs in ("HOOK_MULTI_IMAGE_RAPID_PEAK", "GEMINI_SEARCH_3D", "CREATURE_ART", "AI_IMAGE"):
                return False
            if not (mt in ("video", "hook_ready_video") and mp.endswith((".mp4", ".mov", ".webm"))):
                return False
            sid_slot = int(sc_dict.get("scene_id") or 0)
            if sid_slot == 1:
                try:
                    from app.graph.nodes.hook_video_node import verify_hook_clip_genuine_motion
                    ok_slot_mot, _, _ = verify_hook_clip_genuine_motion(str(sc_dict.get("media_path")))
                    if not ok_slot_mot:
                        return False
                except Exception:
                    pass
            return True

        n_slots = len(scenes_media)
        min_vid_ratio = 0.80 if is_long_form else 0.855
        min_video_slots = max(5, int((n_slots * min_vid_ratio) + 0.999)) if n_slots >= 5 else n_slots
        current_video_slots = sum(1 for s_it in scenes_media if _is_real_video_slot(s_it))
        total_slots_dur = sum(_get_slot_dur(i_s) for i_s in range(n_slots)) or 1.0
        current_video_dur = sum(_get_slot_dur(i_s) for i_s in range(n_slots) if _is_real_video_slot(scenes_media[i_s]))

        # Ưu tiên số 1: Mọi cảnh bị rỗng/_graphic.png + Cảnh 1 (0), Cảnh 2 (1), Cảnh cuối (n_slots - 1), Cảnh áp cuối (n_slots - 2)
        mandatory_slot_indices: List[int] = []
        for idx_em in range(n_slots):
            if _is_empty_or_graphic(scenes_media[idx_em]):
                mandatory_slot_indices.append(idx_em)
        for m_idx in [0, 1, n_slots - 1, n_slots - 2]:
            if 0 <= m_idx < n_slots and m_idx not in mandatory_slot_indices:
                if not _is_real_video_slot(scenes_media[m_idx]):
                    mandatory_slot_indices.append(m_idx)

        rescue_candidates_idx: List[int] = list(mandatory_slot_indices)
        # Ưu tiên cứu các cảnh thân bài có thời lượng dài nhất trước để nhanh chóng vượt mốc > 85% thời lượng video
        remaining_mid_idxs = sorted(
            [idx_m for idx_m in range(n_slots) if idx_m not in rescue_candidates_idx and not _is_real_video_slot(scenes_media[idx_m])],
            key=lambda i_m: _get_slot_dur(i_m),
            reverse=True,
        )
        rescue_candidates_idx.extend(remaining_mid_idxs)

        dur_deficit_slots = 0
        sim_v_dur = current_video_dur
        for rc_i in rescue_candidates_idx:
            if (sim_v_dur / total_slots_dur) > min_vid_ratio:
                break
            sim_v_dur += _get_slot_dur(rc_i)
            dur_deficit_slots += 1

        needed_rescues = max(
            len(mandatory_slot_indices),
            min_video_slots - current_video_slots,
            dur_deficit_slots,
        )
        if needed_rescues > 0 and rescue_candidates_idx:
            from app.graph.tools.media_tools import execute_youtube_short_peak_clone_sync
            from app.utils.ffmpeg_helper import get_duration_ms, trim_video
            logger.info(
                f"🎬 [Real-Motion Floor & Ending Rescue] Hiện có {current_video_slots}/{n_slots} cảnh video thật "
                f"(bắt buộc 100% đầu & cuối video là video thật, tối thiểu {min_video_slots}/{n_slots} cảnh). "
                f"Đang nâng cấp {min(needed_rescues, len(rescue_candidates_idx))} cảnh sang Video chuyển động thật độc lập..."
            )
            overall_rescue_q = clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=4)
            rescue_angle_facets = [
                "cinematic motion closeup",
                "dramatic real footage",
                "intense slow motion",
                "atmospheric closeup",
            ]
            portrait_broll_facets = [
                "close up eyes dark lighting",
                "silhouette person walking night",
                "dramatic portrait shadow",
                "person thinking dark room",
                "man walking rain night",
                "mysterious face shadow closeup",
            ]
            is_psych_niche = str(current_niche).lower() in (
                "psychology",
                "satirical_psychology_animation",
                "doodle",
                "philosophy",
                "self_improvement",
            )
            rescue_sem = asyncio.Semaphore(6 if is_long_form else 3)

            async def _rescue_single_scene_motion(
                r_pos: int,
                r_idx: int,
                cancel_event: Optional[threading.Event] = None,
                deadline_ts: Optional[float] = None,
            ) -> None:
                def _rescue_cancelled() -> bool:
                    return (cancel_event is not None and cancel_event.is_set()) or (
                        deadline_ts is not None and (deadline_ts - time.monotonic()) <= 1.8
                    )

                r_sc = scenes_media[r_idx]
                r_sid = int(r_sc.get("scene_id") or (r_idx + 1))
                r_dur = _get_slot_dur(r_idx)
                sc_obj_r = script.scenes[r_idx] if (script and hasattr(script, "scenes") and 0 <= r_idx < len(script.scenes)) else None
                r_stock_q = clean_scene_search_query(
                    getattr(sc_obj_r, "stock_query", "") or r_sc.get("fallback_broad_query") or "",
                    max_words=4,
                )
                r_kw = clean_scene_search_query(
                    r_sc.get("visual_keyword") or r_stock_q or primary_entity or state.get("topic", ""),
                    max_words=4,
                ) or overall_rescue_q
                from app.graph.tools.tool_schemas import resolve_scene_active_entity
                r_out_mp4 = str(media_dir / f"scene_{r_sid:03d}_motion_rescue.mp4")
                r_subj = resolve_scene_active_entity(
                    scene_query=f"{r_kw} {r_stock_q}".strip(),
                    narration=str(r_sc.get("narration") or ""),
                    subject_entity=clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=3),
                    topic=state.get("topic", ""),
                ) or clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=2) or r_kw
                r_tool = str(r_sc.get("tool") or "")
                is_portrait_tool = (r_tool == "fetch_clean_cinematic_portrait_or_broll") or is_psych_niche
                already_tried_scene_kw = r_tool in (
                    "clone_youtube_short_peak_clip",
                    "search_pexels_pixabay_stock_video",
                )
                rescued_ok = False

                # 0A. Video Dài 16:9 (Long-Form): Cắt/tải qua Chapter-Pool 16:9 Widescreen
                if not rescued_ok and is_long_form and not _rescue_cancelled():
                    try:
                        lf_resc = await asyncio.to_thread(
                            _fetch_longform_16x9_broll_clip_sync,
                            scene_id=r_sid,
                            query=r_kw,
                            primary_entity=primary_entity or state.get("topic", ""),
                            topic=state.get("topic", ""),
                            narration=str(r_sc.get("narration") or state.get("topic", "")),
                            target_duration_sec=r_dur,
                            media_dir=media_dir,
                            used_bvids=longform_used_bvids,
                            used_urls=used_pool_urls,
                            bvid_lock=longform_bvid_lock,
                            media_agent=media_agent,
                            is_scifi=is_scifi_val,
                            job_id=job_id,
                            output_override_path=r_out_mp4,
                            cancel_event=cancel_event,
                            deadline_ts=deadline_ts,
                        )
                        if not (lf_resc and lf_resc.get("media_path") and Path(lf_resc["media_path"]).exists()):
                            from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                            lf_resc = await asyncio.to_thread(
                                slice_any_available_chapter_master_for_scene,
                                media_dir=media_dir,
                                scene_id=r_sid,
                                target_duration_sec=r_dur,
                                job_id=job_id,
                                output_override_path=r_out_mp4,
                            )
                        if lf_resc and lf_resc.get("media_path") and Path(lf_resc["media_path"]).exists():
                            r_sc["media_path"] = lf_resc["media_path"]
                            r_sc["media_type"] = "hook_ready_video" if r_sid == 1 else "video"
                            r_sc["pose_images"] = []
                            r_sc["production_method"] = lf_resc.get("production_method", "MOTION_FLOOR_RESCUE_16X9")
                            r_sc["visual_strategy"] = "LONGFORM_16X9_DOCUMENTARY_VIDEO"
                            r_sc["source_url"] = str(lf_resc.get("source_url") or lf_resc["media_path"])
                            rescued_ok = True
                            logger.info(f"  ✅ [Real-Motion Floor Rescue 16:9] Scene {r_sid}: Đã nâng cấp sang Video 16:9 ({Path(lf_resc['media_path']).name})")
                    except Exception as _re_lf:
                        logger.debug(f"[Real-Motion Floor Rescue] Scene {r_sid} 16:9 rescue skip: {_re_lf}")

                # 0B. Phân cảnh Tâm lý / Triết học: Lấy Video Forensic / B-Roll diễn viên thật
                if not rescued_ok and is_portrait_tool and not is_long_form:
                    try:
                        from app.graph.tools.media_tools import execute_clean_cinematic_portrait_or_broll_sync
                        r_story_qs = list(
                            r_sc.get("storyline_search_queries")
                            or strategy_map.get(r_sid, {}).get("storyline_search_queries")
                            or []
                        )
                        p_facet = portrait_broll_facets[r_pos % len(portrait_broll_facets)]
                        cand_p_queries = list(dict.fromkeys([q for q in (*r_story_qs, r_kw, r_stock_q, p_facet) if q]))[:3]
                        for p_q in cand_p_queries:
                            if rescued_ok or not p_q:
                                break
                            c_vid_res = await asyncio.to_thread(
                                execute_clean_cinematic_portrait_or_broll_sync,
                                subject_entity=r_subj,
                                human_action_query=p_q,
                                output_path=r_out_mp4,
                                media_preference="YOUTUBE_FORENSIC_OR_CHARACTER_SHORT" if r_pos % 2 == 0 else "VIDEO_BROLL",
                                overall_script_query=overall_rescue_q,
                                search_scope="SCENE_SPECIFIC",
                                narration=str(r_sc.get("narration") or state.get("topic", "")),
                                topic=state.get("topic", ""),
                                target_duration_sec=r_dur + 0.25,
                                used_urls=used_pool_urls,
                                is_hook_scene=(r_sid == 1),
                                job_id=job_id,
                                scene_id=r_sid,
                                fallback_query=r_stock_q,
                                storyline_queries=cand_p_queries,
                                cancel_event=cancel_event,
                                deadline_ts=deadline_ts,
                            )
                            if c_vid_res and c_vid_res.get("media_path") and Path(c_vid_res["media_path"]).exists():
                                if str(c_vid_res.get("media_type") or "").lower() in ("video", "hook_ready_video"):
                                    r_sc["media_path"] = c_vid_res["media_path"]
                                    r_sc["media_type"] = "video"
                                    r_sc["pose_images"] = []
                                    r_sc["production_method"] = "MOTION_FLOOR_RESCUE_PORTRAIT_BROLL"
                                    r_sc["visual_strategy"] = "CLEAN_ACTOR_VIDEO_BROLL"
                                    r_sc["source_url"] = str(c_vid_res.get("source_url") or c_vid_res["media_path"])
                                    rescued_ok = True
                                    logger.info(f"  ✅ [Real-Motion Floor Rescue] Scene {r_sid}: Đã nâng cấp sang Portrait B-Roll Video độc lập ({Path(c_vid_res['media_path']).name})")
                    except Exception as _re_pb:
                        logger.debug(f"[Real-Motion Floor Rescue] Scene {r_sid} Portrait B-Roll rescue skip: {_re_pb}")

                facet_tag = (
                    portrait_broll_facets[r_pos % len(portrait_broll_facets)]
                    if is_portrait_tool
                    else rescue_angle_facets[r_pos % len(rescue_angle_facets)]
                )
                from app.graph.tools.tool_schemas import build_single_entity_stock_queries
                scene_single_queries = build_single_entity_stock_queries(
                    [q_c for q_c in (r_stock_q, r_kw, overall_rescue_q) if q_c],
                    subject_entity=r_subj,
                    topic=state.get("topic", ""),
                    narration=str(r_sc.get("narration") or ""),
                    max_words=3,
                )

                # 1. Kho Stock Video Pexels/Pixabay HD
                if not rescued_ok and not _rescue_cancelled():
                    if r_sid == 1:
                        try:
                            from app.graph.nodes.hook_video_node import verify_hook_clip_genuine_motion
                            for p_hk_disk in (work_dir / "hook_motion_001.mp4", work_dir / "hook_motion_001_fullpeak.mp4"):
                                if p_hk_disk.exists() and _safe_size(p_hk_disk) > 20_000:
                                    ok_hk_m, _, _ = await asyncio.to_thread(verify_hook_clip_genuine_motion, str(p_hk_disk))
                                    if ok_hk_m:
                                        await asyncio.to_thread(
                                            trim_video,
                                            input_path=str(p_hk_disk),
                                            duration_seconds=r_dur + 0.25,
                                            output_path=r_out_mp4,
                                            width=1920 if is_long_form else 1080,
                                            height=1080 if is_long_form else 1920,
                                            fps=30,
                                            is_scifi=is_scifi_val,
                                            niche=current_niche,
                                            apply_grading=True,
                                            is_hook_scene=True,
                                            fair_use_transform=False,
                                            start_offset_sec=0.0,
                                            preserve_continuous_flow=True,
                                        )
                                        if Path(r_out_mp4).exists() and _safe_size(Path(r_out_mp4)) > 15_000:
                                            r_sc["media_path"] = r_out_mp4
                                            r_sc["media_type"] = "hook_ready_video"
                                            r_sc["pose_images"] = []
                                            r_sc["production_method"] = "MOTION_FLOOR_RESCUE_HOOK_RECOVERY"
                                            r_sc["visual_strategy"] = "HOOK_PEAK_SHORT_VIDEO"
                                            r_sc["source_url"] = str(p_hk_disk)
                                            rescued_ok = True
                                            logger.info(
                                                f"  🔥 [Real-Motion Floor Rescue] Scene 1: Khôi phục Video Hook chuyển động thật từ '{p_hk_disk.name}' -> {Path(r_out_mp4).name}"
                                            )
                                            break
                        except Exception as _e_hk_r:
                            logger.debug(f"[Real-Motion Floor Rescue] Scene 1 disk hook recovery skip: {_e_hk_r}")

                # 1 & 2. PARALLEL RACE: Quét ĐỒNG THỜI Pexels/Pixabay Stock HD + YouTube Short Peak Clone ngay từ giây 0.0s!
                # Nhánh nào tìm được clip đạt chuẩn Vision + RAG trước sẽ hủy ngay nhánh còn lại.
                if not rescued_ok and not _rescue_cancelled():
                    stock_queries: List[str] = []
                    cand_sq_list = (
                        (portrait_broll_facets[r_pos % len(portrait_broll_facets)], r_stock_q, overall_rescue_q)
                        if is_portrait_tool
                        else (
                            *(scene_single_queries[:4]),
                            r_stock_q,
                            r_kw,
                            overall_rescue_q,
                        )
                    )
                    for sq in cand_sq_list:
                        sq_clean = (sq or "").strip()
                        if sq_clean and sq_clean not in stock_queries:
                            stock_queries.append(sq_clean)

                    class _RescueBranchCancelEvent:
                        def __init__(self, parent_ev):
                            self._parent = parent_ev
                            self._local = False

                        def set(self):
                            self._local = True

                        def is_set(self) -> bool:
                            if self._local:
                                return True
                            if self._parent is not None and hasattr(self._parent, "is_set"):
                                return bool(self._parent.is_set())
                            return False

                    px_rescue_cancel_ev = _RescueBranchCancelEvent(cancel_event)
                    yt_rescue_cancel_ev = _RescueBranchCancelEvent(cancel_event)
                    r_out_px_mp4 = str(media_dir / f"scene_{r_sid:03d}_motion_rescue_px.mp4")

                    async def _rescue_pexels_branch():
                        for sq_try in stock_queries[:2]:
                            if px_rescue_cancel_ev.is_set() or _rescue_cancelled():
                                break
                            try:
                                va_resc = {
                                    "video_format": "long" if is_long_form else "short",
                                    "search_queries": stock_queries[:3],
                                    "subject": (primary_entity or sq_try) if is_long_form else (r_subj or sq_try),
                                    "core_visual_entity": (primary_entity or sq_try) if is_long_form else (r_subj or sq_try),
                                    "topic": state.get("topic", ""),
                                    "skip_establishing_pass": (r_sid == 1),
                                }
                                best_v_fb = await asyncio.to_thread(
                                    media_agent.search_best_video_clip,
                                    sq_try,
                                    used_pool_urls,
                                    va_resc,
                                    str(r_sc.get("narration") or state.get("topic", "")),
                                    state.get("topic", ""),
                                )
                                if px_rescue_cancel_ev.is_set() or _rescue_cancelled():
                                    return None
                                if best_v_fb and best_v_fb.get("url"):
                                    cand_fb_urls = [
                                        best_v_fb["url"],
                                        *(best_v_fb.get("_vision_alternates") or []),
                                    ]
                                    cand_title_fb = str(best_v_fb.get("title") or sq_try)
                                    for cand_u_fb_raw in cand_fb_urls[:4]:
                                        if px_rescue_cancel_ev.is_set() or _rescue_cancelled():
                                            return None
                                        cand_u_fb = str(cand_u_fb_raw or "").strip()
                                        if not cand_u_fb:
                                            continue
                                        if not rag.try_claim_in_job_video(
                                            job_id=job_id, vid_id=cand_u_fb, url=cand_u_fb, title=cand_title_fb, scene_id=r_sid
                                        ):
                                            used_pool_urls.add(cand_u_fb)
                                            continue
                                        raw_dl_mp4 = str(media_dir / f"scene_{r_sid:03d}_stock_raw.mp4")
                                        await asyncio.to_thread(media_agent.download_file, cand_u_fb, raw_dl_mp4)
                                        if px_rescue_cancel_ev.is_set() or _rescue_cancelled():
                                            Path(raw_dl_mp4).unlink(missing_ok=True)
                                            rag.release_in_job_video_claim(
                                                job_id=job_id, vid_id=cand_u_fb, url=cand_u_fb, title=cand_title_fb, scene_id=r_sid
                                            )
                                            return None
                                        if Path(raw_dl_mp4).exists() and Path(raw_dl_mp4).stat().st_size > 15_000:
                                            await asyncio.to_thread(
                                                trim_video,
                                                input_path=raw_dl_mp4,
                                                duration_seconds=r_dur + 0.25,
                                                output_path=r_out_px_mp4,
                                                width=1920 if is_long_form else 1080,
                                                height=1080 if is_long_form else 1920,
                                                fps=30,
                                                is_scifi=is_scifi_val,
                                                niche=current_niche,
                                                apply_grading=True,
                                                is_hook_scene=False,
                                                fair_use_transform=True,
                                            )
                                            Path(raw_dl_mp4).unlink(missing_ok=True)
                                            if Path(r_out_px_mp4).exists() and Path(r_out_px_mp4).stat().st_size > 15_000:
                                                ok_sig, _ = rag.verify_and_commit_in_job_clip(
                                                    job_id=job_id,
                                                    scene_id=r_sid,
                                                    media_path=r_out_px_mp4,
                                                    vid_id=cand_u_fb,
                                                    url=cand_u_fb,
                                                    title=cand_title_fb,
                                                )
                                                used_pool_urls.add(cand_u_fb)
                                                if ok_sig:
                                                    return {
                                                        "branch": "pexels",
                                                        "media_path": r_out_px_mp4,
                                                        "media_type": "video",
                                                        "production_method": "MOTION_FLOOR_RESCUE_STOCK_VIDEO",
                                                        "visual_strategy": "REAL_DOCUMENTARY_VIDEO",
                                                        "source_url": cand_u_fb,
                                                        "title": cand_title_fb,
                                                        "query": sq_try,
                                                    }
                                                else:
                                                    Path(r_out_px_mp4).unlink(missing_ok=True)
                                        else:
                                            Path(raw_dl_mp4).unlink(missing_ok=True)
                                            rag.release_in_job_video_claim(
                                                job_id=job_id, vid_id=cand_u_fb, url=cand_u_fb, title=cand_title_fb, scene_id=r_sid
                                            )
                            except Exception as _re_doc:
                                logger.debug(f"[Real-Motion Floor Rescue] Scene {r_sid} Stock rescue ({sq_try}) skip: {_re_doc}")
                        return None

                    async def _rescue_youtube_branch():
                        if is_long_form or yt_rescue_cancel_ev.is_set() or _rescue_cancelled():
                            return None
                        q_main = r_kw if not already_tried_scene_kw else f"{r_stock_q or r_kw} {facet_tag}".strip()
                        q_fb = r_stock_q or overall_rescue_q
                        try:
                            yt_sub_dl = (
                                min(deadline_ts, time.monotonic() + 28.0)
                                if deadline_ts is not None
                                else (time.monotonic() + 28.0)
                            )
                            yt_rescue = await asyncio.to_thread(
                                execute_youtube_short_peak_clone_sync,
                                search_query=q_main,
                                output_path=r_out_mp4,
                                target_duration_sec=r_dur,
                                clip_role="PEAK",
                                used_urls=used_pool_urls,
                                fallback_query=q_fb,
                                subject_entity=r_subj,
                                narration=str(r_sc.get("narration") or state.get("topic", "")),
                                topic=state.get("topic", ""),
                                search_scope="SCENE_SPECIFIC",
                                overall_script_query=overall_rescue_q,
                                max_peak_duration_sec=14.0,
                                job_id=job_id,
                                scene_id=r_sid,
                                channel_id=channel_id_val,
                                skip_stock_fallback=True,
                                cancel_event=yt_rescue_cancel_ev,
                                deadline_ts=yt_sub_dl,
                            )
                            if yt_rescue_cancel_ev.is_set() or _rescue_cancelled():
                                if yt_rescue and yt_rescue.get("media_path"):
                                    Path(yt_rescue["media_path"]).unlink(missing_ok=True)
                                return None
                            if yt_rescue and yt_rescue.get("media_path") and Path(yt_rescue["media_path"]).exists():
                                return {
                                    "branch": "youtube",
                                    "media_path": yt_rescue["media_path"],
                                    "media_type": "video",
                                    "production_method": "MOTION_FLOOR_RESCUE_YT_PEAK",
                                    "visual_strategy": "YOUTUBE_SHORT_PEAK_CLONE",
                                    "source_url": str(yt_rescue.get("source_url") or yt_rescue["media_path"]),
                                    "title": q_main,
                                    "query": q_main,
                                }
                        except Exception as _re_yt:
                            logger.debug(f"[Real-Motion Floor Rescue] Scene {r_sid} YT peak rescue ({q_main}) skip: {_re_yt}")
                        return None

                    px_r_task = asyncio.create_task(_rescue_pexels_branch())
                    yt_r_task = asyncio.create_task(_rescue_youtube_branch())
                    pending_r = {px_r_task, yt_r_task}
                    winner_r = None
                    rescue_race_dl = (
                        min(deadline_ts - 8.0, time.monotonic() + 30.0)
                        if deadline_ts is not None
                        else (time.monotonic() + 30.0)
                    )

                    while pending_r and winner_r is None:
                        wait_rem_resc = rescue_race_dl - time.monotonic()
                        if wait_rem_resc <= 0.15:
                            break
                        done_r, pending_r = await asyncio.wait(
                            pending_r,
                            return_when=asyncio.FIRST_COMPLETED,
                            timeout=wait_rem_resc,
                        )
                        if not done_r:
                            break
                        for d_r in done_r:
                            try:
                                res_r = d_r.result()
                            except Exception:
                                res_r = None
                            if res_r and res_r.get("media_path") and Path(res_r["media_path"]).exists():
                                if winner_r is None:
                                    winner_r = res_r
                                    px_rescue_cancel_ev.set()
                                    yt_rescue_cancel_ev.set()
                                    for p_r in pending_r:
                                        p_r.cancel()
                                else:
                                    try:
                                        loser_r_url = str(res_r.get("source_url") or "")
                                        if loser_r_url:
                                            rag.release_in_job_video_claim(
                                                job_id=job_id,
                                                vid_id=loser_r_url,
                                                url=loser_r_url,
                                                title=str(res_r.get("title") or ""),
                                                scene_id=r_sid,
                                            )
                                        Path(res_r["media_path"]).unlink(missing_ok=True)
                                    except Exception:
                                        pass
                    px_rescue_cancel_ev.set()
                    yt_rescue_cancel_ev.set()
                    for p_r in pending_r:
                        p_r.cancel()
                        if winner_r is not None:
                            break

                    if pending_r:
                        await asyncio.gather(*pending_r, return_exceptions=True)

                    if winner_r is not None:
                        r_sc["media_path"] = winner_r["media_path"]
                        r_sc["media_type"] = winner_r["media_type"]
                        r_sc["pose_images"] = []
                        r_sc["production_method"] = winner_r["production_method"]
                        r_sc["visual_strategy"] = winner_r["visual_strategy"]
                        r_sc["source_url"] = winner_r["source_url"]
                        rescued_ok = True
                        logger.info(
                            f"  ✅ [Real-Motion Floor Rescue Parallel Winner: {winner_r['branch'].upper()}] Scene {r_sid}: "
                            f"Đã nâng cấp sang Video chuyển động độc lập ({Path(winner_r['media_path']).name})"
                        )

                # 3. Nếu phân cảnh thân bài (r_sid >= 3) đang bị rỗng/_graphic.png mà chưa tìm được video -> Nạp 1 ảnh Google HD để triệt tiêu 100% _graphic.png
                if not rescued_ok and r_sid not in (1, 2) and _is_empty_or_graphic(r_sc) and not _rescue_cancelled():
                    try:
                        from app.graph.tools.media_tools import execute_google_cinematic_image_sync
                        out_gimg_fb = str(media_dir / f"scene_{r_sid:03d}_rescue_gsearch.jpg")
                        g_res_fb = await asyncio.to_thread(
                            execute_google_cinematic_image_sync,
                            smart_query=r_kw,
                            output_path=out_gimg_fb,
                            narration=str(r_sc.get("narration") or state.get("topic", "")),
                            topic=state.get("topic", ""),
                            used_urls=used_pool_urls,
                            is_hook_scene=False,
                            subject_entity=primary_entity or state.get("topic", ""),
                            search_scope="SCENE_SPECIFIC",
                            overall_script_query=overall_rescue_q,
                            job_id=job_id,
                            scene_id=r_sid,
                            target_image_count=1,
                            storyline_queries=scene_single_queries,
                        )
                        if g_res_fb and g_res_fb.get("media_path") and Path(g_res_fb["media_path"]).exists():
                            r_sc["media_path"] = str(g_res_fb["media_path"])
                            r_sc["media_type"] = "image"
                            r_sc["pose_images"] = list(g_res_fb.get("pose_images") or [g_res_fb["media_path"]])
                            r_sc["production_method"] = "ZERO_GRAPHIC_GOOGLE_PHOTO_HD"
                            r_sc["visual_strategy"] = "GEMINI_SEARCH_3D"
                            r_sc["source_url"] = str(g_res_fb.get("source_url") or "")
                            rescued_ok = True
                    except Exception as _e_gfb:
                        logger.debug(f"[Real-Motion Floor Rescue] Scene {r_sid} empty-slot photo fallback skip: {_e_gfb}")

            async def _guarded_rescue_single_scene_motion(r_pos: int, r_idx: int) -> None:
                from app.agents.curation.scene_context_analyzer_mixin import bind_scene_cancellation
                from app.utils.ffmpeg_helper import is_completed_clean_mp4 as _is_clean_mp4_pg
                async with rescue_sem:
                    r_cancel_ev = threading.Event()
                    r_deadline_ts = time.monotonic() + 46.0
                    bind_scene_cancellation(r_cancel_ev, r_deadline_ts)
                    try:
                        await asyncio.wait_for(
                            _rescue_single_scene_motion(
                                r_pos,
                                r_idx,
                                cancel_event=r_cancel_ev,
                                deadline_ts=r_deadline_ts,
                            ),
                            timeout=48.0,
                        )
                    except Exception as _e_r_to:
                        r_cancel_ev.set()
                        logger.debug(f"[Real-Motion Floor Rescue] Slot {r_idx} timeout/skip: {_e_r_to}")
                        try:
                            r_sc_to = scenes_media[r_idx]
                            r_sid_to = int(r_sc_to.get("scene_id") or (r_idx + 1))
                            r_out_to = media_dir / f"scene_{r_sid_to:03d}_motion_rescue.mp4"
                            if r_out_to.exists() and _is_clean_mp4_pg(r_out_to, min_size_bytes=15_000, min_duration_ms=1000):
                                r_sc_to["media_path"] = str(r_out_to)
                                r_sc_to["media_type"] = "hook_ready_video" if r_sid_to == 1 else "video"
                                r_sc_to["pose_images"] = []
                                r_sc_to["production_method"] = "MOTION_FLOOR_RESCUE_TIMEOUT_RECOVERED"
                                r_sc_to["visual_strategy"] = "REAL_DOCUMENTARY_VIDEO"
                                logger.info(
                                    f"  🛡️ [Real-Motion Floor Rescue] Scene {r_sid_to}: Khôi phục file '{r_out_to.name}' vừa hoàn tất trên đĩa khi hết giờ!"
                                )
                        except Exception:
                            pass

            await asyncio.gather(
                *[
                    _guarded_rescue_single_scene_motion(r_pos, r_idx)
                    for r_pos, r_idx in enumerate(rescue_candidates_idx[:needed_rescues])
                ]
            )
    except Exception as _step_b_err:
        logger.debug(f"[Real-Motion Floor Rescue] Step B warning: {_step_b_err}")

    # ── Bước C: CHUẨN HÓA SỐ LƯỢNG ẢNH TĨNH TRÊN MỖI CẢNH ẢNH (0 LỆNH GỌI MẠNG) & ĐA DẠNG HÓA CAMERA MOTION ──
    for idx_c, sc_c in enumerate(scenes_media):
        if not isinstance(sc_c, dict):
            continue
        if str(sc_c.get("media_type") or "image").lower() == "image":
            quota_c = 2 if _get_slot_dur(idx_c) >= 3.4 else 1
            primary_c = sc_c.get("media_path")
            poses_c = [
                p for p in (sc_c.get("pose_images") or ([primary_c] if primary_c else []))
                if p and not str(p).endswith("_graphic.png") and Path(p).exists() and _safe_size(Path(p)) > 2500
            ]
            if poses_c:
                sc_c["media_path"] = poses_c[0]
                sc_c["pose_images"] = poses_c[:quota_c]

    _motion_pool = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_up", "pan_down"]
    prev_motion = None
    for idx_m, m_item in enumerate(scenes_media):
        if not isinstance(m_item, dict):
            continue
        cur_m = m_item.get("camera_motion") or "zoom_in"
        if idx_m > 0 and cur_m == prev_motion:
            alt_options = [m for m in _motion_pool if m != prev_motion]
            cur_m = alt_options[idx_m % len(alt_options)]
            m_item["camera_motion"] = cur_m
        prev_motion = cur_m

    try:
        report = [
            {
                "scene_id": m.get("scene_id") if isinstance(m, dict) else None,
                "file": Path(m.get("media_path") or "").name if isinstance(m, dict) else "",
                "media_type": m.get("media_type") if isinstance(m, dict) else "image",
                "image_count": len(m.get("pose_images") or ([m.get("media_path")] if m.get("media_type") == "image" else [])) if isinstance(m, dict) else 0,
                "pose_files": [Path(p).name for p in (m.get("pose_images") or [])] if isinstance(m, dict) else [],
                "production_method": m.get("production_method") if isinstance(m, dict) else "unknown",
                "narration": (m.get("narration") or "")[:120] if isinstance(m, dict) else "",
            }
            for m in scenes_media if isinstance(m, dict)
        ]
        (work_dir / "visual_report.json").write_text(_json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as rep_err:
        logger.debug(f"Không ghi được visual_report.json: {rep_err}")

    return scenes_media
