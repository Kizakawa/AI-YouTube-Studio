"""app.graph.nodes.media.post_gather_rescue — Các bước hậu kiểm, khử trùng lặp và cứu hộ Media sau khi gather."""
import asyncio
import logging
import re
import threading
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Set

from app.schemas.contracts import ScriptContract
from app.agents.media_agent import MediaAgent, clean_scene_search_query
from app.agents.image_agent import ImageAgent
from app.graph.evaluators.visual_critic import VisualCritic
from app.graph.state import VideoWorkflowState

logger = logging.getLogger(__name__)
from app.graph.nodes.media.media_helpers import (
    _safe_mtime,
    _safe_size,
    _extract_hook_candidate_vid_id,
    _collect_active_scene_source_paths,
    _scene_audio_duration,
)
from app.graph.nodes.media.longform_broll_fetcher import _fetch_longform_16x9_broll_clip_sync


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
    # ── Bước A.0: CỔNG RÀ SOÁT ĐỒNG BỘ CHỐNG TRÙNG VIDEO LIÊN TIẾP TRONG CÙNG 1 JOB (POST-GATHER INTRA-VIDEO DEDUP GATE) ──
    # Đăng ký toàn bộ các clip video đã thu thập từ các luồng song song vào bộ nhớ dHash 3-mốc (Đầu-Giữa-Cuối).
    # Nếu phát hiện bất kỳ phân cảnh sau nào vô tình trùng khung hình/nguồn với phân cảnh trước,
    # lập tức thu hồi phân cảnh trùng đó để Bước B (`Real-Motion Floor & Ending Rescue`) tự động lấy clip mới 100% khác biệt!
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

    # ══════════════════════════════════════════════════════════════════════════
    # BỘ LẮP GHÉP TIMELINE ƯU TIÊN VIDEO PEAK DÀI -> ẢNH TĨNH (VIDEO-FIRST TIMELINE ASSEMBLER)
    # 1. Mở rộng các đoạn Peak dài (`full_peak_path` 6s-14s) chảy liền mạch qua các phân cảnh kế tiếp
    #    thay vì khóa cứng 3 giây rồi cắt cụt sang ảnh tĩnh!
    # 2. Tổng hợp & ưu tiên xếp các phân đoạn Video lên trước, các phân đoạn Ảnh tĩnh ở phía sau
    #    để tạo luồng video liền mạch trước khi ghép giọng đọc (voiceover) và phụ đề (subtitles).
    # ══════════════════════════════════════════════════════════════════════════
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

    # ── Bước A: ĐÃ TẮT CƠ CHẾ CẮT NỐI TIẾP 1 VIDEO SANG CẢNH KẾ TIẾP (`LONG_PEAK_CONTINUOUS_FLOW`) ──
    # Quy tắc Vàng: 1 Cảnh = 1 Nguồn Video Độc Lập 100% (Tuyệt đối không cắt phần dư của Cảnh N sang Cảnh N+1
    # để ngăn chặn triệt để hiện tượng lặp lại cùng 1 bối cảnh/chuyển động máy quay giữa các phân cảnh).

    # ── Bước B: CỔNG BẢO ĐẢM 100% VIDEO CHUYỂN ĐỘNG THẬT Ở ĐẦU (CẢNH 1, 2) & CUỐI VIDEO (CẢNH CUỐI, ÁP CUỐI) + TỶ LỆ VIDEO >= 75% ──
    try:
        def _is_real_video_slot(sc_dict: Any) -> bool:
            if not isinstance(sc_dict, dict):
                return False
            mt = str(sc_dict.get("media_type") or "image").lower()
            mp = str(sc_dict.get("media_path") or "").lower()
            pm = str(sc_dict.get("production_method") or "").upper()
            vs = str(sc_dict.get("visual_strategy") or "").upper()
            # Không coi các file ảnh tĩnh zoompan (_i2v, _fb_motion, _anim, _motion3d) hoặc multi-cut ảnh tĩnh là video chuyển động thật
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
            return mt in ("video", "hook_ready_video") and mp.endswith((".mp4", ".mov", ".webm"))

        n_slots = len(scenes_media)
        min_vid_ratio = 0.80 if is_long_form else 0.85
        min_video_slots = max(5, int((n_slots * min_vid_ratio) + 0.999)) if n_slots >= 5 else n_slots
        current_video_slots = sum(1 for s_it in scenes_media if _is_real_video_slot(s_it))

        # Bắt buộc 100% Cảnh 1 (0), Cảnh 2 (1), Cảnh cuối (n_slots - 1) và Cảnh áp cuối (n_slots - 2) phải là Video chuyển động thật
        # Ưu tiên Cảnh 1 (0) và Cảnh 2 (1) chạy TRƯỚC để đảm bảo Opening Hook luôn có đầy đủ ngân sách tải Video chuyển động thật!
        mandatory_slot_indices: List[int] = []
        for m_idx in [0, 1, n_slots - 1, n_slots - 2]:
            if 0 <= m_idx < n_slots and m_idx not in mandatory_slot_indices:
                if not _is_real_video_slot(scenes_media[m_idx]):
                    mandatory_slot_indices.append(m_idx)

        rescue_candidates_idx: List[int] = list(mandatory_slot_indices)
        for idx_mid in range(n_slots - 1, -1, -1):
            if idx_mid not in rescue_candidates_idx and not _is_real_video_slot(scenes_media[idx_mid]):
                rescue_candidates_idx.append(idx_mid)

        needed_rescues = max(
            len(mandatory_slot_indices),
            min_video_slots - current_video_slots,
        )
        if needed_rescues > 0 and rescue_candidates_idx:
            from app.graph.tools.media_tools import execute_youtube_short_peak_clone_sync
            from app.utils.ffmpeg_helper import get_duration_ms, trim_video
            logger.info(
                f"🎬 [Real-Motion Floor & Ending Rescue] Hiện có {current_video_slots}/{n_slots} cảnh video thật "
                f"(bắt buộc 100% đầu & cuối video là video thật, tối thiểu {min_video_slots}/{n_slots} cảnh >= 85%). "
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

                if True:
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
                    r_out_mp4 = str(media_dir / f"scene_{r_sid:03d}_motion_rescue.mp4")
                    r_subj = clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=2) or r_kw
                    r_tool = str(r_sc.get("tool") or "")
                    is_portrait_tool = (r_tool == "fetch_clean_cinematic_portrait_or_broll") or is_psych_niche
                    already_tried_scene_kw = r_tool in (
                        "clone_youtube_short_peak_clip",
                        "search_pexels_pixabay_stock_video",
                    )
                    rescued_ok = False

                    # 0A. Nếu là Video Dài 16:9 (Long-Form), ưu tiên cắt/tải qua Chapter-Pool 16:9 Widescreen
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
                                rescued_ok = True
                                logger.info(f"  ✅ [Real-Motion Floor Rescue 16:9] Scene {r_sid}: Đã nâng cấp sang Video 16:9 ({Path(lf_resc['media_path']).name})")
                        except Exception as _re_lf:
                            logger.debug(f"[Real-Motion Floor Rescue] Scene {r_sid} 16:9 rescue skip: {_re_lf}")

                    # 0B. Nếu là phân cảnh Tâm lý / Triết học (`fetch_clean_cinematic_portrait_or_broll`), lấy Video Forensic / B-Roll diễn viên thật sạch chữ
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
                                        rescued_ok = True
                                        logger.info(f"  ✅ [Real-Motion Floor Rescue] Scene {r_sid}: Đã nâng cấp sang Portrait B-Roll Video độc lập ({Path(c_vid_res['media_path']).name})")
                        except Exception as _re_pb:
                            logger.debug(f"[Real-Motion Floor Rescue] Scene {r_sid} Portrait B-Roll rescue skip: {_re_pb}")

                    # Chuẩn bị truy vấn theo sát thực thể riêng của phân cảnh (không gộp cứng mọi cảnh về cùng 1 từ khóa chung!)
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
                        max_words=3,
                    )

                    async def _run_stock_video_rescue_step() -> None:
                        nonlocal rescued_ok
                        if rescued_ok or _rescue_cancelled():
                            return
                        stock_queries: List[str] = []
                        cand_sq_list = (
                            (portrait_broll_facets[r_pos % len(portrait_broll_facets)], r_stock_q, overall_rescue_q)
                            if is_portrait_tool
                            else (
                                r_stock_q,
                                r_kw,
                                *(scene_single_queries[:2]),
                                overall_rescue_q,
                                f"{r_subj} {facet_tag}".strip(),
                            )
                        )
                        for sq in cand_sq_list:
                            sq_clean = (sq or "").strip()
                            if sq_clean and sq_clean not in stock_queries:
                                stock_queries.append(sq_clean)
                        for sq_try in stock_queries[:2]:
                            if rescued_ok or _rescue_cancelled():
                                break
                            try:
                                va_resc = {
                                    "video_format": "long" if is_long_form else "short",
                                    "search_queries": stock_queries[:3],
                                    "subject": (primary_entity or sq_try) if is_long_form else sq_try,
                                    "core_visual_entity": (primary_entity or sq_try) if is_long_form else sq_try,
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
                                if best_v_fb and best_v_fb.get("url"):
                                    cand_fb_urls = [
                                        best_v_fb["url"],
                                        *(best_v_fb.get("_vision_alternates") or []),
                                    ]
                                    cand_title_fb = str(best_v_fb.get("title") or sq_try)
                                    for cand_u_fb_raw in cand_fb_urls[:4]:
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
                                        if Path(raw_dl_mp4).exists() and Path(raw_dl_mp4).stat().st_size > 15_000:
                                            await asyncio.to_thread(
                                                trim_video,
                                                input_path=raw_dl_mp4,
                                                duration_seconds=r_dur + 0.25,
                                                output_path=r_out_mp4,
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
                                            if Path(r_out_mp4).exists() and Path(r_out_mp4).stat().st_size > 15_000:
                                                if any(k_wm in cand_u_fb.lower() for k_wm in ("wikimedia.org", "archive.org")):
                                                    from app.graph.evaluators.visual_critic import VisualCritic
                                                    vc_wm = await asyncio.to_thread(
                                                        VisualCritic.inspect_clip_before_concat,
                                                        r_out_mp4,
                                                        min_short_edge=720,
                                                        min_sharpness=5.2,
                                                        check_watermark=True,
                                                        allow_snow=True,
                                                        auto_sanitize=False,
                                                    )
                                                    if not vc_wm.get("passed", False):
                                                        Path(r_out_mp4).unlink(missing_ok=True)
                                                        rag.release_in_job_video_claim(
                                                            job_id=job_id, vid_id=cand_u_fb, url=cand_u_fb, title=cand_title_fb, scene_id=r_sid
                                                        )
                                                        continue
                                                ok_sig, _ = rag.verify_and_commit_in_job_clip(
                                                    job_id=job_id,
                                                    scene_id=r_sid,
                                                    media_path=r_out_mp4,
                                                    vid_id=cand_u_fb,
                                                    url=cand_u_fb,
                                                    title=cand_title_fb,
                                                )
                                                used_pool_urls.add(cand_u_fb)
                                                if ok_sig:
                                                    r_sc["media_path"] = r_out_mp4
                                                    r_sc["media_type"] = "video"
                                                    r_sc["pose_images"] = []
                                                    r_sc["production_method"] = "MOTION_FLOOR_RESCUE_STOCK_VIDEO"
                                                    r_sc["visual_strategy"] = "REAL_DOCUMENTARY_VIDEO"
                                                    rescued_ok = True
                                                    logger.info(
                                                        f"  ✅ [Real-Motion Floor Rescue] Scene {r_sid}: Đã nâng cấp sang Stock Video độc lập ('{sq_try}') -> {Path(r_out_mp4).name}"
                                                    )
                                                    break
                                                else:
                                                    Path(r_out_mp4).unlink(missing_ok=True)
                                        else:
                                            Path(raw_dl_mp4).unlink(missing_ok=True)
                                            rag.release_in_job_video_claim(
                                                job_id=job_id, vid_id=cand_u_fb, url=cand_u_fb, title=cand_title_fb, scene_id=r_sid
                                            )
                            except Exception as _re_doc:
                                logger.debug(f"[Real-Motion Floor Rescue] Scene {r_sid} Stock rescue ({sq_try}) skip: {_re_doc}")

                    async def _run_yt_peak_rescue_step() -> None:
                        nonlocal rescued_ok
                        if rescued_ok or is_long_form or _rescue_cancelled():
                            return
                        yt_rescue_queries = [
                            (f"{r_stock_q or r_kw} {facet_tag}".strip(), r_stock_q or overall_rescue_q, "SCENE_SPECIFIC"),
                        ]
                        if not already_tried_scene_kw:
                            yt_rescue_queries.insert(0, (r_kw, r_stock_q or overall_rescue_q, "SCENE_SPECIFIC"))
                        for q_main, q_fb, q_scope in yt_rescue_queries[:1]:
                            if rescued_ok or is_long_form or _rescue_cancelled():
                                break
                            try:
                                yt_sub_dl = (
                                    min(deadline_ts, time.monotonic() + 22.0)
                                    if deadline_ts is not None
                                    else (time.monotonic() + 22.0)
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
                                    search_scope=q_scope,
                                    overall_script_query=overall_rescue_q,
                                    max_peak_duration_sec=14.0,
                                    job_id=job_id,
                                    scene_id=r_sid,
                                    channel_id=channel_id_val,
                                    cancel_event=cancel_event,
                                    deadline_ts=yt_sub_dl,
                                )
                                if yt_rescue and yt_rescue.get("media_path") and Path(yt_rescue["media_path"]).exists():
                                    r_sc["media_path"] = yt_rescue["media_path"]
                                    r_sc["media_type"] = "video"
                                    r_sc["pose_images"] = []
                                    r_sc["production_method"] = "MOTION_FLOOR_RESCUE_YT_PEAK"
                                    r_sc["visual_strategy"] = "YOUTUBE_SHORT_PEAK_CLONE"
                                    rescued_ok = True
                                    logger.info(
                                        f"  ✅ [Real-Motion Floor Rescue] Scene {r_sid}: Đã nâng cấp sang Video Peak độc lập ({Path(yt_rescue['media_path']).name})"
                                    )
                            except Exception as _re_yt:
                                logger.debug(f"[Real-Motion Floor Rescue] Scene {r_sid} YT peak rescue ({q_main}) skip: {_re_yt}")

                    # Luôn ưu tiên chạy Stock Video Pexels/Pixabay 1080x1920 HD (~2s) trước khi gọi yt-dlp để tăng tốc độ dựng gấp 5 lần!
                    await _run_stock_video_rescue_step()
                    if not rescued_ok:
                        await _run_yt_peak_rescue_step()

                    # 3. Cứu hộ từ các ứng viên `hook_candidates` ĐỘC LẬP chưa dùng (khác hoàn toàn video ID với Cảnh 1)
                    if not rescued_ok and not _rescue_cancelled():
                        try:
                            from app.graph.evaluators.visual_critic import VisualCritic
                            hc_dir = work_dir / "hook_candidates"
                            active_paths_r, active_vids_r = _collect_active_scene_source_paths(
                                scenes_media, exclude_scene_ids={r_sid}
                            )
                            unused_hook_cands = sorted(
                                [
                                    *list(hc_dir.glob("hook_peak_clean_*.mp4")),
                                    *list(hc_dir.glob("s1_stock_rescue_*.mp4")),
                                ],
                                key=_safe_size,
                                reverse=True,
                            )
                            for src_vp in unused_hook_cands:
                                if not src_vp.exists() or src_vp.stat().st_size < 25_000:
                                    continue
                                if str(src_vp.resolve()) in active_paths_r:
                                    continue
                                cand_yt_id = _extract_hook_candidate_vid_id(src_vp)
                                if cand_yt_id in active_vids_r:
                                    continue
                                if not rag.try_claim_in_job_video(
                                    job_id=job_id,
                                    vid_id=cand_yt_id,
                                    url=f"https://www.youtube.com/watch?v={cand_yt_id}" if len(cand_yt_id) == 11 else str(src_vp),
                                    title=f"Hook Candidate {cand_yt_id}",
                                    scene_id=r_sid,
                                ):
                                    continue
                                src_total_s = get_duration_ms(str(src_vp)) / 1000.0
                                offset_s = max(0.5, min(src_total_s - r_dur - 0.3, src_total_s * 0.35)) if src_total_s > r_dur + 1.5 else 0.0
                                trim_video(
                                    input_path=str(src_vp),
                                    duration_seconds=r_dur + 0.3,
                                    output_path=r_out_mp4,
                                    width=1920 if is_long_form else 1080,
                                    height=1080 if is_long_form else 1920,
                                    fps=30,
                                    is_scifi=is_scifi_val,
                                    niche=current_niche,
                                    apply_grading=True,
                                    is_hook_scene=False,
                                    fair_use_transform=True,
                                    start_offset_sec=offset_s,
                                    preserve_continuous_flow=True,
                                )
                                if Path(r_out_mp4).exists() and Path(r_out_mp4).stat().st_size > 15_000:
                                    vc_res = VisualCritic.inspect_clip_before_concat(
                                        r_out_mp4,
                                        min_short_edge=720,
                                        min_sharpness=5.4,
                                        check_watermark=True,
                                        allow_snow=True,
                                        auto_sanitize=True,
                                    )
                                    if not vc_res.get("passed", False):
                                        logger.warning(
                                            f"  🚫 [Ending Video Guarantee] Scene {r_sid}: Loại ứng viên '{src_vp.name}' do dính chữ/watermark ({vc_res.get('reason')})"
                                        )
                                        Path(r_out_mp4).unlink(missing_ok=True)
                                        continue
                                    ok_cand_sig, _ = rag.verify_and_commit_in_job_clip(
                                        job_id=job_id,
                                        scene_id=r_sid,
                                        media_path=r_out_mp4,
                                        vid_id=cand_yt_id,
                                        url=str(src_vp),
                                        title=f"Unused Hook Candidate {cand_yt_id}",
                                    )
                                    if ok_cand_sig:
                                        r_sc["media_path"] = r_out_mp4
                                        r_sc["media_type"] = "video"
                                        r_sc["pose_images"] = []
                                        r_sc["production_method"] = "ENDING_UNUSED_CANDIDATE_VIDEO"
                                        r_sc["visual_strategy"] = "ENDING_UNUSED_CANDIDATE_VIDEO"
                                        rescued_ok = True
                                        logger.info(
                                            f"  🔥 [Ending Video Guarantee] Scene {r_sid}: Đã sử dụng ứng viên video độc lập chưa dùng '{src_vp.name}' (@{offset_s:.1f}s)!"
                                        )
                                        break
                                    else:
                                        Path(r_out_mp4).unlink(missing_ok=True)
                        except Exception as _re_slice:
                            logger.debug(f"[Ending Video Guarantee] Scene {r_sid} unused candidate skip: {_re_slice}")

            async def _guarded_rescue_single_scene_motion(r_pos: int, r_idx: int) -> None:
                from app.agents.curation.scene_context_analyzer_mixin import bind_scene_cancellation
                async with rescue_sem:
                    r_cancel_ev = threading.Event()
                    r_deadline_ts = time.monotonic() + 38.5
                    bind_scene_cancellation(r_cancel_ev, r_deadline_ts)
                    try:
                        await asyncio.wait_for(
                            _rescue_single_scene_motion(
                                r_pos,
                                r_idx,
                                cancel_event=r_cancel_ev,
                                deadline_ts=r_deadline_ts,
                            ),
                            timeout=40.0,
                        )
                    except Exception as _e_r_to:
                        r_cancel_ev.set()
                        logger.debug(f"[Real-Motion Floor Rescue] Slot {r_idx} timeout/skip: {_e_r_to}")
                        try:
                            r_sc_to = scenes_media[r_idx]
                            r_sid_to = int(r_sc_to.get("scene_id") or (r_idx + 1))
                            r_out_to = media_dir / f"scene_{r_sid_to:03d}_motion_rescue.mp4"
                            if not _is_real_video_slot(r_sc_to) and r_out_to.exists() and r_out_to.stat().st_size > 15_000:
                                r_sc_to["media_path"] = str(r_out_to)
                                r_sc_to["media_type"] = "video"
                                r_sc_to["pose_images"] = []
                                r_sc_to["production_method"] = "MOTION_FLOOR_RESCUE_LATE_RECOVERED"
                                r_sc_to["visual_strategy"] = "REAL_DOCUMENTARY_VIDEO"
                                logger.info(
                                    f"  ♻️ [Real-Motion Floor Rescue] Scene {r_sid_to}: Khôi phục file video '{r_out_to.name}' vừa cắt xong trên đĩa!"
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

    # ══════════════════════════════════════════════════════════════════════════
    # Bước C: BỘ ĐIỀU PHỐI MẬT ĐỘ ẢNH TĨNH MƯỢT MÀ (TỐI ĐA 1-2 ẢNH / CẢNH ẢNH, MỖI ẢNH >= 2.0 GIÂY)
    # - Chấm dứt hoàn toàn hiện tượng nhồi 3 ảnh vào 1 cảnh 3.5s gây nháy hình (strobe) làm rớt người xem!
    # - Cảnh < 4.0s chỉ dùng 1 ảnh Ken Burns mượt mà; cảnh >= 4.0s dùng tối đa 2 ảnh (mỗi ảnh 2.0s - 2.5s).
    # ══════════════════════════════════════════════════════════════════════════
    try:
        hook_img_count = 0
        if scenes_media and isinstance(scenes_media[0], dict):
            sc0 = scenes_media[0]
            if str(sc0.get("production_method") or "").startswith("CLIMAX_IMAGES_LIVE_FX_"):
                hook_img_count = len([p for p in (sc0.get("pose_images") or []) if p and Path(p).exists()])

        body_img_indices = [
            idx_b for idx_b, m_b in enumerate(scenes_media)
            if isinstance(m_b, dict) and str(m_b.get("media_type") or "image").lower() == "image"
        ]

        if body_img_indices:
            scene_quotas: Dict[int, int] = {
                i_slot: (2 if _get_slot_dur(i_slot) >= 3.4 else 1)
                for i_slot in body_img_indices
            }

            angle_facets = [
                "dramatic close up portrait",
                "intense action moment",
                "cinematic wide angle",
                "emotional eye contact",
                "dark atmospheric detail",
                "natural habitat behavior",
                "turning point scene",
                "vivid high contrast shot",
            ]

            async def _fill_scene_image_quota(slot_idx: int) -> None:
                sc_item = scenes_media[slot_idx]
                sid = int(sc_item.get("scene_id") or (slot_idx + 1))
                quota = scene_quotas.get(slot_idx, 2)
                primary_p = sc_item.get("media_path")
                existing_poses = [
                    p for p in (sc_item.get("pose_images") or ([primary_p] if primary_p else []))
                    if p and not str(p).endswith("_graphic.png") and Path(p).exists() and Path(p).stat().st_size > 2500
                ]
                if not existing_poses and primary_p and not str(primary_p).endswith("_graphic.png") and Path(primary_p).exists():
                    existing_poses = [primary_p]

                verified_poses: List[str] = []
                for ep in existing_poses:
                    if rag.verify_and_commit_in_job_image(job_id=job_id, scene_id=sid, image_path=ep, url=ep):
                        verified_poses.append(ep)
                if not verified_poses and existing_poses:
                    verified_poses = [existing_poses[0]]

                # Nếu phân cảnh ĐÃ có ít nhất 1 ảnh thật đã qua Vision Curation -> giữ nguyên, TUYỆT ĐỐI KHÔNG tải ảnh web thô chưa qua Vision!
                if len(verified_poses) >= 1:
                    if str(sc_item.get("media_path") or "").endswith("_graphic.png"):
                        sc_item["media_path"] = verified_poses[0]
                    sc_item["pose_images"] = verified_poses[:quota]
                    return

                short_subj = clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=3)
                sc_story_qs = list(
                    sc_item.get("storyline_search_queries")
                    or (strategy_map.get(sid) or {}).get("storyline_search_queries")
                    or []
                )
                raw_sc_kw = (sc_story_qs[0] if sc_story_qs else "") or sc_item.get("visual_keyword") or sc_item.get("visual_prompt") or short_subj
                sc_kw = clean_scene_search_query(raw_sc_kw, max_words=5) or short_subj

                # Nếu phân cảnh đang rơi vào _graphic.png (chưa có ảnh thật nào), gọi công cụ chuẩn có Vision Curation
                try:
                    from app.graph.tools.google_image_executor import execute_google_cinematic_image_sync
                    resc_img_p = str(media_dir / f"scene_{sid:03d}_vision_rescue.jpg")
                    g_res = await asyncio.to_thread(
                        execute_google_cinematic_image_sync,
                        smart_query=sc_kw,
                        output_path=resc_img_p,
                        narration=str(sc_item.get("narration") or state.get("topic", "")),
                        topic=state.get("topic", ""),
                        used_urls=used_pool_urls,
                        is_hook_scene=(sid == 1),
                        subject_entity=short_subj or state.get("topic", ""),
                        search_scope="SCENE_SPECIFIC",
                        overall_script_query=primary_entity or state.get("topic", ""),
                        job_id=job_id,
                        scene_id=sid,
                        target_image_count=1,
                        storyline_queries=sc_story_qs,
                    )
                    if g_res and g_res.get("media_path") and Path(g_res["media_path"]).exists():
                        verified_poses.append(str(g_res["media_path"]))
                except Exception as _g_resc_err:
                    logger.debug(f"[Visual Density Engine] Scene {sid} vision photo rescue skip: {_g_resc_err}")

                if not verified_poses:
                    try:
                        ai_cut_p = str(media_dir / f"scene_{sid:03d}_ai_rescue.jpg")
                        ar_str = "16:9" if is_long_form else "9:16"
                        ai_q = f"{sc_kw}, dramatic {'widescreen 16:9' if is_long_form else 'vertical 9:16'} photography, 8k sharp focus"
                        gen_p = await asyncio.to_thread(
                            image_agent.generate_scene_image,
                            ai_q,
                            ai_cut_p,
                            ar_str,
                            state.get("channel_style", "cinematic"),
                            sid,
                            sc_item.get("visual_prompt"),
                        )
                        if gen_p and Path(gen_p).exists() and Path(gen_p).stat().st_size > 2500:
                            verified_poses.append(gen_p)
                    except Exception:
                        pass

                real_poses = [p for p in verified_poses if p and not str(p).endswith("_graphic.png")]
                if real_poses:
                    verified_poses = real_poses
                    if str(sc_item.get("media_path") or "").endswith("_graphic.png"):
                        sc_item["media_path"] = real_poses[0]
                    sc_item["pose_images"] = verified_poses[:quota]

            density_sem = asyncio.Semaphore(5)

            async def _guarded_fill_scene_image_quota(i_slot: int) -> None:
                from app.agents.curation.scene_context_analyzer_mixin import bind_scene_cancellation
                async with density_sem:
                    d_cancel_ev = threading.Event()
                    bind_scene_cancellation(d_cancel_ev, time.monotonic() + 28.0)
                    try:
                        await asyncio.wait_for(_fill_scene_image_quota(i_slot), timeout=30.0)
                    except Exception as _e_dens_to:
                        d_cancel_ev.set()
                        logger.debug(f"[Visual Density Engine] Slot {i_slot} timeout/skip: {_e_dens_to}")

            await asyncio.gather(*[_guarded_fill_scene_image_quota(i_slot) for i_slot in body_img_indices])

            total_video_images = hook_img_count + sum(
                len(scenes_media[i_s].get("pose_images") or [scenes_media[i_s].get("media_path")])
                for i_s in body_img_indices
            )
            per_scene_log = ", ".join(
                f"S{scenes_media[i_s].get('scene_id')}:{len(scenes_media[i_s].get('pose_images') or [])}img"
                for i_s in body_img_indices
            )
            logger.info(
                f"🖼️ [Visual Density Engine] Đã chuẩn bị tổng cộng {total_video_images} ảnh thật độc lập cho video "
                f"(Hook={hook_img_count}img | Body: {per_scene_log})"
            )
    except Exception as e_dens:
        logger.warning(f"[MediaNodes] Visual Density Engine warning: {e_dens}")

    # ══════════════════════════════════════════════════════════════════════════
    # Bước D: BẢO HIỂM TRIỆT TIÊU 100% THẺ XANH `_graphic.png` (`SCENE #X` PLACEHOLDER CARD)
    # - Không bao giờ để lọt thẻ placeholder `_graphic.png` vào bản dựng cuối!
    # ══════════════════════════════════════════════════════════════════════════
    try:
        from app.utils.ffmpeg_helper import get_duration_ms, trim_video, is_completed_clean_mp4
        graphic_slots = [
            idx_g for idx_g, sc_g in enumerate(scenes_media)
            if isinstance(sc_g, dict) and (
                str(sc_g.get("media_path") or "").endswith("_graphic.png")
                or not sc_g.get("media_path")
                or not Path(str(sc_g.get("media_path"))).exists()
            )
        ]
        if graphic_slots:
            logger.warning(
                f"🛡️ [Zero-Graphic-Card Guard] Phát hiện {len(graphic_slots)} phân cảnh còn thẻ placeholder `_graphic.png` -> Thay thế bằng Video/Animation thật ngay!"
            )
            is_psych_guard = str(current_niche).lower() in (
                "psychology", "satirical_psychology_animation", "doodle", "philosophy", "self_improvement"
            )
            from app.services.media.longform_chapter_pool import has_verified_chapter_master_meta
            hc_dir_g = work_dir / "hook_candidates"
            ch_pool_dir_g = media_dir / "chapter_pool"
            active_paths_zg, active_vids_zg = _collect_active_scene_source_paths(scenes_media)
            # TUYỆT ĐỐI KHÔNG đưa các clip phân cảnh ngắn đã gán (`media_dir/*.mp4`, `hook_motion_*.mp4`) vào kho cắt lát,
            # chỉ lấy các file Master dài (`chapter_*_master.mp4`) hoặc các ứng viên `hook_candidates` chưa dùng!
            all_real_mp4s = sorted(
                [
                    p for p in [
                        *(
                            [m_p for m_p in ch_pool_dir_g.glob("chapter_*_master.mp4") if has_verified_chapter_master_meta(m_p)]
                            if ch_pool_dir_g.exists()
                            else []
                        ),
                        *(
                            [
                                c_p for c_p in (
                                    *list(hc_dir_g.glob("hook_peak_clean_*.mp4")),
                                    *list(hc_dir_g.glob("s1_stock_rescue_*.mp4")),
                                )
                                if str(c_p.resolve()) not in active_paths_zg
                                and _extract_hook_candidate_vid_id(c_p) not in active_vids_zg
                            ]
                            if hc_dir_g.exists()
                            else []
                        ),
                    ]
                    if is_completed_clean_mp4(p, min_size_bytes=25_000, min_duration_ms=4500)
                ],
                key=_safe_size,
                reverse=True,
            )
            d_chars_g = ["@MANIPULATOR", "@MODERNYOU", "@PLEASER", "@ANCESTOR"]
            d_props_g = ["puppet_strings", "mask", "cage", "broken_compass", "scroll_feed", "treadmill"]

            def _anchor_zg_query(raw_kw: str, sc_obj: Optional[Dict[str, Any]] = None) -> str:
                sid_z = int((sc_obj or {}).get("scene_id") or 0)
                story_qs_zg = (
                    (sc_obj or {}).get("storyline_search_queries")
                    or (strategy_map.get(sid_z) or {}).get("storyline_search_queries")
                    or []
                )
                base_zg = (story_qs_zg[0] if story_qs_zg else "") or raw_kw or primary_entity or state.get("topic", "")
                kw_clean = clean_scene_search_query(base_zg, max_words=5)
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
                    return clean_scene_search_query(f"{ent_pref} {kw_clean}", max_words=4) or ent_pref
                return kw_clean

            zg_sem = asyncio.Semaphore(5)

            async def _resolve_one_graphic_slot(g_pos: int, g_idx: int) -> None:
                from app.services.media.longform_chapter_pool import is_clip_visually_unique_in_job
                async with zg_sem:
                    await _resolve_one_graphic_slot_inner(g_pos, g_idx, is_clip_visually_unique_in_job)

            async def _resolve_one_graphic_slot_inner(
                g_pos: int,
                g_idx: int,
                is_clip_visually_unique_in_job: Any,
            ) -> None:
                from app.agents.curation.scene_context_analyzer_mixin import bind_scene_cancellation
                if True:
                    sc_g = scenes_media[g_idx]
                    sid_g = int(sc_g.get("scene_id") or (g_idx + 1))
                    dur_g = _get_slot_dur(g_idx)
                    out_g_mp4 = str(media_dir / f"scene_{sid_g:03d}_zero_graphic.mp4")
                    replaced_g = False

                    # 0. Phục hồi tức thì file video/ảnh thật của chính phân cảnh này vừa ghi xong trên đĩa (< 1ms)
                    _cand_g_vids = list(media_dir.glob(f"scene_{sid_g:03d}_*.mp4"))
                    if sid_g == 1:
                        _cand_g_vids.extend([
                            p for p in (work_dir / "hook_motion_001.mp4", work_dir / "hook_motion_001_fullpeak.mp4")
                            if p.exists()
                        ])
                    disk_g_vids = sorted(
                        [
                            p for p in _cand_g_vids
                            if is_completed_clean_mp4(p, min_size_bytes=20_000, min_duration_ms=1200)
                        ],
                        key=_safe_mtime,
                        reverse=True,
                    )
                    if disk_g_vids:
                        for dv_p in disk_g_vids:
                            if is_clip_visually_unique_in_job(str(dv_p), job_id, scene_id=sid_g):
                                sc_g["media_path"] = str(dv_p)
                                sc_g["media_type"] = "video"
                                sc_g["pose_images"] = []
                                sc_g["production_method"] = "ZERO_GRAPHIC_RECOVERED_VIDEO"
                                sc_g["visual_strategy"] = "REAL_DOCUMENTARY_VIDEO"
                                replaced_g = True
                                break
                    if not replaced_g:
                        disk_g_jpgs = sorted(
                            [
                                p for p in media_dir.glob(f"scene_{sid_g:03d}_*.jpg")
                                if _safe_size(p) > 4_000 and "_cand" not in p.name
                            ],
                            key=_safe_mtime,
                            reverse=True,
                        )
                        if disk_g_jpgs:
                            sc_g["media_path"] = str(disk_g_jpgs[0])
                            sc_g["media_type"] = "image"
                            sc_g["pose_images"] = [str(p) for p in disk_g_jpgs[: (3 if sid_g == 1 else 2)]]
                            sc_g["production_method"] = "ZERO_GRAPHIC_RECOVERED_PHOTO"
                            sc_g["visual_strategy"] = "GEMINI_SEARCH_3D"
                            replaced_g = True

                    if not replaced_g and is_long_form:
                        try:
                            from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                            zg_pool = await asyncio.to_thread(
                                slice_any_available_chapter_master_for_scene,
                                media_dir=media_dir,
                                scene_id=sid_g,
                                target_duration_sec=dur_g,
                                job_id=job_id,
                                output_override_path=out_g_mp4,
                                max_master_uses_override=15,
                            )
                            if zg_pool and zg_pool.get("media_path") and Path(zg_pool["media_path"]).exists():
                                sc_g["media_path"] = zg_pool["media_path"]
                                sc_g["media_type"] = "video"
                                sc_g["pose_images"] = []
                                sc_g["production_method"] = "ZERO_GRAPHIC_CHAPTER_POOL_UNIQUE"
                                sc_g["visual_strategy"] = "LONGFORM_16X9_DOCUMENTARY_VIDEO"
                                sc_g["source_url"] = str(zg_pool.get("source_url") or zg_pool["media_path"])
                                replaced_g = True
                                logger.info(
                                    f"  ✅ [Zero-Graphic-Card Guard] Scene {sid_g}: Cắt lát 16:9 độc lập (0% trùng) -> '{Path(zg_pool['media_path']).name}'"
                                )
                        except Exception as _e_zgp:
                            logger.debug(f"[Zero-Graphic-Card Guard] Scene {sid_g} chapter pool rescue skip: {_e_zgp}")

                    zg_q = _anchor_zg_query(str(sc_g.get("visual_keyword") or ""), sc_g)
                    zg_story_qs = [
                        clean_scene_search_query(str(q_x), max_words=5)
                        for q_x in dict.fromkeys([
                            zg_q,
                            *(sc_g.get("storyline_search_queries") or []),
                            str(sc_g.get("visual_keyword") or ""),
                        ])
                        if str(q_x or "").strip()
                    ]
                    zg_narr = str(sc_g.get("narration") or state.get("topic", "")).strip()
                    if not replaced_g:
                        t2_ev = threading.Event()
                        try:
                            def _run_zg_vid():
                                with bind_scene_cancellation(t2_ev, time.monotonic() + 14.0):
                                    return media_agent.search_best_video_clip(
                                        zg_q,
                                        used_pool_urls,
                                        {"video_format": "long", "subject": primary_entity or zg_q} if is_long_form else None,
                                        zg_narr,
                                        state.get("topic", ""),
                                    )
                            best_zg_v = await asyncio.wait_for(asyncio.to_thread(_run_zg_vid), timeout=14.0)
                            if best_zg_v and best_zg_v.get("url"):
                                cand_zg_urls = [
                                    str(best_zg_v["url"]),
                                    *[str(u) for u in (best_zg_v.get("_vision_alternates") or []) if u],
                                ]
                                zg_v_title = str(best_zg_v.get("title") or zg_q)
                                raw_zg_mp4 = str(media_dir / f"scene_{sid_g:03d}_zero_graphic_raw.mp4")
                                for zg_v_url in cand_zg_urls[:4]:
                                    if not zg_v_url:
                                        continue
                                    if not rag.try_claim_in_job_video(
                                        job_id=job_id,
                                        vid_id=zg_v_url,
                                        url=zg_v_url,
                                        title=zg_v_title,
                                        scene_id=sid_g,
                                    ):
                                        used_pool_urls.add(zg_v_url)
                                        continue
                                    await asyncio.wait_for(
                                        asyncio.to_thread(media_agent.download_file, zg_v_url, raw_zg_mp4),
                                        timeout=10.0,
                                    )
                                    if Path(raw_zg_mp4).exists() and _safe_size(Path(raw_zg_mp4)) > 15_000:
                                        await asyncio.to_thread(
                                            trim_video,
                                            input_path=raw_zg_mp4,
                                            duration_seconds=dur_g + 0.25,
                                            output_path=out_g_mp4,
                                            width=1920 if is_long_form else 1080,
                                            height=1080 if is_long_form else 1920,
                                            fps=30,
                                            is_scifi=is_scifi_val,
                                            niche=current_niche,
                                            apply_grading=True,
                                            is_hook_scene=False,
                                            fair_use_transform=True,
                                        )
                                        Path(raw_zg_mp4).unlink(missing_ok=True)
                                        if Path(out_g_mp4).exists() and _safe_size(Path(out_g_mp4)) > 15_000:
                                            ok_zg_sig, _ = rag.verify_and_commit_in_job_clip(
                                                job_id=job_id,
                                                scene_id=sid_g,
                                                media_path=out_g_mp4,
                                                vid_id=zg_v_url,
                                                url=zg_v_url,
                                                title=zg_v_title,
                                            )
                                            if ok_zg_sig and is_clip_visually_unique_in_job(out_g_mp4, job_id, scene_id=sid_g):
                                                used_pool_urls.add(zg_v_url)
                                                sc_g["media_path"] = out_g_mp4
                                                sc_g["media_type"] = "video"
                                                sc_g["pose_images"] = []
                                                sc_g["production_method"] = "ZERO_GRAPHIC_FRESH_STOCK_VIDEO"
                                                sc_g["visual_strategy"] = "REAL_DOCUMENTARY_VIDEO"
                                                sc_g["source_url"] = zg_v_url
                                                replaced_g = True
                                                logger.info(
                                                    f"  ✅ [Zero-Graphic-Card Guard] Scene {sid_g}: Đã thay thẻ placeholder bằng Video Stock HD độc lập mới ('{zg_q}')"
                                                )
                                                break
                                            else:
                                                Path(out_g_mp4).unlink(missing_ok=True)
                                                rag.release_in_job_video_claim(
                                                    job_id=job_id, vid_id=zg_v_url, url=zg_v_url, title=zg_v_title, scene_id=sid_g
                                                )
                                    else:
                                        Path(raw_zg_mp4).unlink(missing_ok=True)
                                        rag.release_in_job_video_claim(
                                            job_id=job_id, vid_id=zg_v_url, url=zg_v_url, title=zg_v_title, scene_id=sid_g
                                        )
                        except Exception as _e_zgv:
                            logger.debug(f"[Zero-Graphic-Card Guard] Scene {sid_g} stock video rescue skip: {_e_zgv}")
                        finally:
                            t2_ev.set()

                    if not replaced_g:
                        t3_ev = threading.Event()
                        try:
                            def _run_zg_ph():
                                with bind_scene_cancellation(t3_ev, time.monotonic() + 16.0):
                                    return media_agent.search_best_photo(
                                        zg_q,
                                        used_pool_urls,
                                        {"video_format": "long", "subject": primary_entity or zg_q} if is_long_form else None,
                                        zg_narr,
                                        state.get("topic", ""),
                                    )
                            best_zg_ph = await asyncio.wait_for(asyncio.to_thread(_run_zg_ph), timeout=16.0)
                            if best_zg_ph and best_zg_ph.get("url"):
                                out_zg_jpg = str(media_dir / f"scene_{sid_g:03d}_zero_graphic.jpg")
                                await asyncio.wait_for(
                                    asyncio.to_thread(media_agent.download_file, str(best_zg_ph["url"]), out_zg_jpg),
                                    timeout=10.0,
                                )
                                if Path(out_zg_jpg).exists() and _safe_size(Path(out_zg_jpg)) > 4_000:
                                    used_pool_urls.add(str(best_zg_ph["url"]))
                                    sc_g["media_path"] = out_zg_jpg
                                    sc_g["media_type"] = "image"
                                    sc_g["pose_images"] = [out_zg_jpg]
                                    sc_g["production_method"] = "ZERO_GRAPHIC_STOCK_PHOTO_HD"
                                    sc_g["visual_strategy"] = "GEMINI_SEARCH_3D"
                                    sc_g["source_url"] = str(best_zg_ph["url"])
                                    replaced_g = True
                                    logger.info(
                                        f"  ✅ [Zero-Graphic-Card Guard] Scene {sid_g}: Đã thay thẻ placeholder bằng ảnh tài liệu HD độc lập ('{zg_q}')"
                                    )
                        except Exception as _e_zgph:
                            logger.debug(f"[Zero-Graphic-Card Guard] Scene {sid_g} stock photo rescue skip: {_e_zgph}")
                        finally:
                            t3_ev.set()

                    if not replaced_g:
                        t4_ev = threading.Event()
                        try:
                            from app.graph.tools.media_tools import execute_google_cinematic_image_sync
                            out_zg_gimg = str(media_dir / f"scene_{sid_g:03d}_zero_graphic_gsearch.jpg")
                            def _run_zg_gimg():
                                with bind_scene_cancellation(t4_ev, time.monotonic() + 18.0):
                                    return execute_google_cinematic_image_sync(
                                        smart_query=zg_q,
                                        output_path=out_zg_gimg,
                                        narration=zg_narr,
                                        topic=state.get("topic", ""),
                                        used_urls=used_pool_urls,
                                        is_hook_scene=(sid_g == 1),
                                        subject_entity=primary_entity or state.get("topic", ""),
                                        search_scope="SCENE_SPECIFIC",
                                        overall_script_query=primary_entity or state.get("topic", ""),
                                        job_id=job_id,
                                        scene_id=sid_g,
                                        target_image_count=1,
                                        storyline_queries=zg_story_qs,
                                    )
                            g_zg_res = await asyncio.wait_for(asyncio.to_thread(_run_zg_gimg), timeout=18.0)
                            if g_zg_res and g_zg_res.get("media_path") and Path(g_zg_res["media_path"]).exists():
                                sc_g["media_path"] = str(g_zg_res["media_path"])
                                sc_g["media_type"] = "image"
                                sc_g["pose_images"] = list(g_zg_res.get("pose_images") or [g_zg_res["media_path"]])
                                sc_g["production_method"] = "ZERO_GRAPHIC_GOOGLE_PHOTO_HD"
                                sc_g["visual_strategy"] = "GEMINI_SEARCH_3D"
                                sc_g["source_url"] = str(g_zg_res.get("source_url") or "")
                                replaced_g = True
                                logger.info(
                                    f"  ✅ [Zero-Graphic-Card Guard] Scene {sid_g}: Đã thay thẻ placeholder bằng ảnh Google Vision HD ('{zg_q}')"
                                )
                        except Exception as _e_zgg:
                            logger.debug(f"[Zero-Graphic-Card Guard] Scene {sid_g} google image rescue skip: {_e_zgg}")
                        finally:
                            t4_ev.set()

                    # Tầng bảo hiểm Video Cắt Lát Độc Lập từ kho Master/MP4 dài trên đĩa (0% trùng dHash/signature)
                    if not replaced_g and all_real_mp4s:
                        for cand_m_i, src_mp4_g in enumerate(all_real_mp4s[:12]):
                            try:
                                src_dur_s = get_duration_ms(str(src_mp4_g)) / 1000.0
                                if src_dur_s < max(4.0, dur_g + 1.5):
                                    continue
                                max_off_g = max(0.5, src_dur_s - dur_g - 0.5)
                                off_g = round(((sid_g * 7.3 + cand_m_i * 5.7 + g_pos * 4.1) % max_off_g), 2)
                                cand_slice_mp4 = str(media_dir / f"scene_{sid_g:03d}_zg_slice_{cand_m_i}.mp4")
                                await asyncio.wait_for(
                                    asyncio.to_thread(
                                        trim_video,
                                        input_path=str(src_mp4_g),
                                        duration_seconds=dur_g + 0.15,
                                        output_path=cand_slice_mp4,
                                        width=1920 if is_long_form else 1080,
                                        height=1080 if is_long_form else 1920,
                                        fps=30,
                                        is_scifi=is_scifi_val,
                                        niche=current_niche,
                                        apply_grading=True,
                                        is_hook_scene=False,
                                        fair_use_transform=False,
                                        start_offset_sec=off_g,
                                        preserve_continuous_flow=True,
                                    ),
                                    timeout=16.0,
                                )
                                if is_completed_clean_mp4(cand_slice_mp4, min_size_bytes=25_000, min_duration_ms=1200):
                                    from app.graph.evaluators.visual_critic import VisualCritic
                                    zg_slice_chk = await asyncio.to_thread(
                                        VisualCritic.inspect_clip_before_concat,
                                        cand_slice_mp4,
                                        540,
                                        11.0,
                                        True,
                                        False,
                                        True,
                                    )
                                    if zg_slice_chk.get("passed") and is_clip_visually_unique_in_job(
                                        cand_slice_mp4,
                                        job_id,
                                        scene_id=sid_g,
                                        master_key=str(src_mp4_g.resolve()),
                                        max_master_uses=5,
                                    ):
                                        Path(cand_slice_mp4).replace(out_g_mp4)
                                        sc_g["media_path"] = out_g_mp4
                                        sc_g["media_type"] = "video"
                                        sc_g["pose_images"] = []
                                        sc_g["production_method"] = "ZERO_GRAPHIC_UNIQUE_MASTER_SLICE"
                                        sc_g["visual_strategy"] = "LONGFORM_16X9_DOCUMENTARY_VIDEO" if is_long_form else "REAL_DOCUMENTARY_VIDEO"
                                        sc_g["source_url"] = f"{src_mp4_g.name}#t={off_g:.1f}"
                                        replaced_g = True
                                        logger.info(
                                            f"  ✅ [Zero-Graphic-Card Guard] Scene {sid_g}: Cắt lát độc lập từ '{src_mp4_g.name}' (@{off_g:.1f}s)"
                                        )
                                        break
                                Path(cand_slice_mp4).unlink(missing_ok=True)
                            except Exception:
                                pass

                    # Tầng bảo hiểm cuối: Ảnh Stock ngang/dọc HD trực tiếp qua StockFetcher (không để sót bất kỳ _graphic.png nào)
                    if not replaced_g:
                        try:
                            from app.services.media.stock_fetcher import StockFetcher
                            sf_zg = StockFetcher()
                            broad_ph_q = clean_scene_search_query(primary_entity or zg_q or state.get("topic", ""), max_words=3)
                            ph_orient = "landscape" if is_long_form else "portrait"
                            raw_phs = await asyncio.wait_for(
                                asyncio.to_thread(
                                    lambda: (
                                        (sf_zg.search_photos(
                                            zg_q,
                                            per_page=10,
                                            orientation=ph_orient,
                                            topic=state.get("topic", ""),
                                            narration=zg_narr,
                                            used_urls=used_pool_urls,
                                        ) or [])
                                        + (sf_zg.search_photos(
                                            broad_ph_q,
                                            per_page=10,
                                            orientation=ph_orient,
                                            topic=state.get("topic", ""),
                                            narration=zg_narr,
                                            used_urls=used_pool_urls,
                                        ) or [])
                                        + (sf_zg.search_pixabay_photos(
                                            broad_ph_q,
                                            per_page=10,
                                            orientation="horizontal" if is_long_form else "vertical",
                                        ) or [])
                                    )
                                ),
                                timeout=12.0,
                            )
                            for ph_item in raw_phs:
                                ph_u = str(ph_item.get("url") or "").strip()
                                ph_w = int(ph_item.get("width") or 0)
                                ph_h = int(ph_item.get("height") or 0)
                                if not ph_u or ph_u in used_pool_urls:
                                    continue
                                if is_long_form and ph_w > 0 and ph_h > 0 and ph_w < ph_h:
                                    continue
                                out_zg_direct = str(media_dir / f"scene_{sid_g:03d}_zero_graphic_direct.jpg")
                                await asyncio.wait_for(
                                    asyncio.to_thread(media_agent.download_file, ph_u, out_zg_direct),
                                    timeout=8.0,
                                )
                                if Path(out_zg_direct).exists() and _safe_size(Path(out_zg_direct)) > 8_000:
                                    used_pool_urls.add(ph_u)
                                    sc_g["media_path"] = out_zg_direct
                                    sc_g["media_type"] = "image"
                                    sc_g["pose_images"] = [out_zg_direct]
                                    sc_g["production_method"] = "ZERO_GRAPHIC_DIRECT_STOCK_PHOTO"
                                    sc_g["visual_strategy"] = "GEMINI_SEARCH_3D"
                                    sc_g["source_url"] = ph_u
                                    replaced_g = True
                                    logger.info(
                                        f"  ✅ [Zero-Graphic-Card Guard] Scene {sid_g}: Đã thay thẻ placeholder bằng ảnh Stock HD trực tiếp ('{broad_ph_q}')"
                                    )
                                    break
                        except Exception as _e_zgd:
                            logger.debug(f"[Zero-Graphic-Card Guard] Scene {sid_g} direct photo skip: {_e_zgd}")

            await asyncio.gather(*[_resolve_one_graphic_slot(gp, gi) for gp, gi in enumerate(graphic_slots)])
    except Exception as _e_guard_d:
        logger.debug(f"[Zero-Graphic-Card Guard] Step D warning: {_e_guard_d}")

    # Đa dạng hóa chuyển động máy quay (Camera Motion Alternation) giữa các phân cảnh liền kề
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

    # Báo cáo nguồn hình ảnh từng cảnh để kiểm tra chất lượng (storage/temp/job_x/visual_report.json)
    try:
        import json as _json
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
