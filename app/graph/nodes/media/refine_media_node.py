"""app.graph.nodes.media.refine_media_node — Node tự động sửa chữa và thay thế các phân cảnh Media chưa đạt chuẩn."""
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
from app.graph.nodes.media.longform_broll_fetcher import _fetch_longform_16x9_broll_clip_sync

async def node_refine_media(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node tự động sửa chữa SONG SONG các cảnh bị đánh giá FAIL kỹ thuật hoặc TRÙNG LẶP CẢNH CẮT trong cùng video."""
    scenes_media = list(state.get("scenes_media", []))
    media_eval = state.get("media_eval", {})
    failed_ids = set(media_eval.get("failed_scene_ids", []))
    primary_entity = state.get("primary_entity")
    work_dir = Path(state.get("work_dir", "storage/cache"))
    media_dir = work_dir / "media"
    retry_count = state.get("media_retry_count", 0) + 1

    image_agent = ImageAgent()
    media_agent = MediaAgent()
    ai_video_agent = AIVideoAgent()
    current_niche = state.get("niche") or state.get("channel_style") or state.get("channel_niche") or "general"
    is_scifi_val = (current_niche == "scifi") or any(k in state.get("topic", "").lower() for k in ("scifi", "sci-fi", "alien", "cyborg", "viễn tưởng"))
    logger.info(f"🔄 [LangGraph] Tự động sửa chữa SONG SONG media kỹ thuật / chống trùng cảnh cắt cho: {sorted(failed_ids)} (Lần {retry_count})")

    from app.services.media.media_history import get_recent_media_urls
    from app.services.media.image_quality import clean_search_query_from_prompt
    used_refine_urls = get_recent_media_urls(limit=150)
    for _m in scenes_media:
        if isinstance(_m, dict) and _m.get("source_url"):
            used_refine_urls.add(_m["source_url"])

    total_scenes_refine = len(scenes_media)
    mandatory_video_refine_sids = {1, 2, max(1, total_scenes_refine - 1), total_scenes_refine}
    job_id_refine = int(state.get("job_id") or 0)
    channel_id_refine = str(state.get("target_channel_id") or state.get("channel_id") or "")
    is_lf_refine = (
        str(state.get("video_format") or (state.get("script_data") or {}).get("video_format") or "short").strip().lower() == "long"
        or total_scenes_refine >= 14
    )
    w_ref = 1920 if is_lf_refine else 1080
    h_ref = 1080 if is_lf_refine else 1920
    ar_ref = "16:9" if is_lf_refine else "9:16"
    refine_url_lock = threading.Lock()
    refine_used_bvids: set = set()

    def _verify_and_sanitize_refined_media(
        cand_path: str,
        is_video_file: bool,
        scene_id: int,
    ) -> tuple[bool, str]:
        """
        Cổng kiểm định & tự làm sạch bắt buộc cho mọi media thay thế trong `node_refine_media`:
        - Chặn tuyệt đối ảnh/video tối đen, mờ nhòe (`sharpness < 12.0` cho Cảnh 1, `< 6.0` cho cảnh thân),
          hoặc còn sót chữ poster/báo chí (`poster_text_rows`).
        - Tự động crop lề chữ poster hoặc nâng nét UnsharpMask/FFmpeg trước khi kết luận loại bỏ.
        """
        from app.graph.evaluators.visual_critic import VisualCritic
        from app.services.video_memory_rag import VideoMemoryRAG

        p_cand = Path(cand_path)
        if not p_cand.exists():
            return False, "missing_file"

        req_sharp = 12.0 if scene_id == 1 else 6.0
        if is_video_file:
            if p_cand.stat().st_size < 10_000:
                return False, "video_too_small"
            if scene_id in (1, 2):
                from app.utils.ffmpeg_helper import verify_hook_clip_genuine_motion
                ok_gen_m, _, gen_m_rsn = verify_hook_clip_genuine_motion(str(p_cand))
                if not ok_gen_m:
                    return False, f"static_or_frozen_opening:{gen_m_rsn}"
            if scene_id == 1:
                from app.graph.nodes.hook_video_node import _ensure_hook_clip_sharpness_and_luma
                ok_hk_sh, hk_sh_val = _ensure_hook_clip_sharpness_and_luma(str(p_cand), min_sharpness=12.0)
                if not ok_hk_sh:
                    return False, f"hook_sharpness={hk_sh_val:.2f}<12.0"
            gate = VisualCritic().inspect_clip_before_concat(
                str(p_cand),
                min_short_edge=540,
                min_sharpness=12.0 if scene_id == 1 else 5.8,
                check_watermark=True,
                auto_sanitize=True,
            )
            if not gate.get("passed", False):
                return False, "; ".join(gate.get("issues") or ["clip_gate_failed"])
            return True, "clean_video"

        if p_cand.stat().st_size < 2_500:
            return False, "image_too_small"
        if VisualCritic._is_solid_color_or_black(str(p_cand)):
            return False, "solid_or_black_image"

        try:
            has_bad_art, art_reason = VideoMemoryRAG._has_embedded_text_or_collage_artifacts(p_cand.read_bytes())
            if has_bad_art:
                from PIL import Image
                with Image.open(str(p_cand)) as im_raw:
                    im_rgb = im_raw.convert("RGB")
                    w_i, h_i = im_rgb.size
                    if min(w_i, h_i) >= 600:
                        cropped = im_rgb.crop((int(w_i * 0.08), int(h_i * 0.14), int(w_i * 0.92), int(h_i * 0.86)))
                        cropped = cropped.resize((w_i, h_i), Image.Resampling.LANCZOS)
                        cropped.save(str(p_cand), format="JPEG", quality=94)
                has_bad_art, art_reason = VideoMemoryRAG._has_embedded_text_or_collage_artifacts(p_cand.read_bytes())
                if has_bad_art:
                    return False, f"embedded_artifact:{art_reason}"
        except Exception as e_art:
            return False, f"image_verify_err:{e_art}"

        sharp_val = VisualCritic._measure_sharpness(str(p_cand))
        if sharp_val < req_sharp:
            try:
                from PIL import Image, ImageEnhance, ImageFilter
                with Image.open(str(p_cand)) as im_sh:
                    im_rgb = im_sh.convert("RGB")
                    im_enh = ImageEnhance.Contrast(im_rgb).enhance(1.14)
                    im_enh = im_enh.filter(ImageFilter.UnsharpMask(radius=2, percent=155, threshold=2))
                    im_enh.save(str(p_cand), format="JPEG", quality=95)
                sharp_val = VisualCritic._measure_sharpness(str(p_cand))
            except Exception:
                pass
            if sharp_val < req_sharp:
                return False, f"low_sharpness:{sharp_val:.2f}<{req_sharp:.1f}"

        return True, "clean_image"

    refine_visual_plan = state.get("visual_plan") or {}
    refine_strategy_map = {
        int(s.get("scene_id") or 0): s
        for s in (refine_visual_plan.get("scenes_strategy") or [])
        if isinstance(s, dict) and s.get("scene_id")
    }
    scene_eval_issues_map: Dict[int, List[str]] = {}
    for sc_sc in (media_eval.get("scene_scores") or []):
        if isinstance(sc_sc, dict) and sc_sc.get("scene_id"):
            scene_eval_issues_map[int(sc_sc["scene_id"])] = [str(x) for x in (sc_sc.get("issues") or [])]

    async def _refine_single_item(
        item: Dict[str, Any],
        cancel_event: Optional[threading.Event] = None,
        deadline_ts: Optional[float] = None,
    ) -> None:
        def _refine_cancelled() -> bool:
            return (cancel_event is not None and cancel_event.is_set()) or (
                deadline_ts is not None and (deadline_ts - time.monotonic()) <= 1.8
            )

        if item.get("scene_id") not in failed_ids:
            return
        scene_id = int(item["scene_id"])
        kw = item.get("visual_keyword", "")
        narr = item.get("narration", "")
        vp = item.get("visual_prompt", None)
        sc_strat_ref = refine_strategy_map.get(scene_id) or {}
        sc_subj_raw = sc_strat_ref.get("creature_name") or sc_strat_ref.get("subject_entity") or ""
        audited_yt_q = (
            sc_strat_ref.get("youtube_search_query")
            or sc_strat_ref.get("action_or_concept_query")
            or sc_strat_ref.get("human_action_query")
            or kw
        )
        audited_fb_q = str(
            sc_strat_ref.get("fallback_broad_query")
            or sc_strat_ref.get("secondary_stock_query")
            or item.get("fallback_broad_query")
            or ""
        ).strip()
        clean_kw_q = clean_search_query_from_prompt(audited_yt_q or kw or vp or "", narration=narr, topic=state.get("topic", ""), max_words=5) or kw
        overall_refine_q = clean_scene_search_query(
            sc_strat_ref.get("overall_script_query") or primary_entity or state.get("topic", ""),
            max_words=4,
        ) or clean_kw_q
        subj_refine = clean_scene_search_query(sc_subj_raw or primary_entity or state.get("topic", ""), max_words=2) or overall_refine_q
        refine_angle_mods = ("close up action", "real footage", "documentary clip", "dramatic angle")
        angle_mod = refine_angle_mods[(scene_id + retry_count) % len(refine_angle_mods)]
        if audited_fb_q and len((clean_kw_q or "").split()) < 2:
            clean_kw_q = clean_scene_search_query(audited_fb_q, max_words=4) or clean_kw_q
        if (
            subj_refine
            and len((clean_kw_q or "").split()) < 2
            and subj_refine.lower().split()[0] not in (clean_kw_q or "").lower()
        ):
            clean_kw_q = clean_scene_search_query(f"{subj_refine} {clean_kw_q or angle_mod}", max_words=4) or clean_kw_q
        is_mandatory_vid = scene_id in mandatory_video_refine_sids
        is_video = True
        replaced = False
        sc_dur_sec = _scene_audio_duration(work_dir, scene_id)
        sc_issues_list = scene_eval_issues_map.get(scene_id) or []
        has_semantic_vision_defect = any("Gemini Vision ContactSheet" in iss or "trùng lặp" in iss.lower() for iss in sc_issues_list)
        critic_sug_q = str(item.get("critic_suggested_query") or "").strip()
        critic_broll_q = str(item.get("critic_fallback_broll_query") or "").strip()
        critic_fb = str(item.get("critic_feedback") or "").strip()
        if not critic_sug_q or not critic_broll_q:
            for iss_s in sc_issues_list:
                if not critic_sug_q:
                    m_sq = re.search(r"\[Suggested Query:\s*([^\]]+)\]", str(iss_s))
                    if m_sq:
                        critic_sug_q = m_sq.group(1).strip()
                if not critic_broll_q:
                    m_bq = re.search(r"\[Fallback B-Roll:\s*([^\]]+)\]", str(iss_s))
                    if m_bq:
                        critic_broll_q = m_bq.group(1).strip()
        if critic_sug_q:
            clean_kw_q = clean_scene_search_query(critic_sug_q, max_words=4) or critic_sug_q
            logger.info(
                f"🎯 [Self-Scoring Refine] Cảnh #{scene_id}: Đổi truy vấn theo đánh giá của VisualCritic -> '{clean_kw_q}' "
                f"(B-Roll dự phòng: '{critic_broll_q}' | Lý do loại vòng trước: {critic_fb or '; '.join(sc_issues_list)})"
            )

        # 0. Bảo vệ Video Hook thật ở Cảnh 1 & Cảnh 2 (CHỈ khi không bị Gemini Vision ContactSheet bắt lỗi AI dị dạng/chữ rác/trùng lặp):
        # Lưu ý: File video Hook thật của Cảnh 1 có tên `hook_motion_001.mp4` (`CLIMAX_REAL_VIDEO_CONTINUOUS_PEAK` / `CLIMAX_STOCK_VIDEO_REAL_MOTION`),
        # nên chỉ loại trừ `CLIMAX_IMAGES_LIVE_FX` (slide ảnh), TUYỆT ĐỐI không loại trừ tên file `hook_motion_`!
        existing_mp = str(item.get("media_path") or "")
        existing_pm = str(item.get("production_method") or "").upper()
        if (
            scene_id in (1, 2)
            and not has_semantic_vision_defect
            and existing_mp
            and Path(existing_mp).exists()
            and existing_mp.lower().endswith((".mp4", ".mov", ".webm"))
            and "_i2v" not in existing_mp.lower()
            and "CLIMAX_IMAGES_LIVE_FX" not in existing_pm
            and "GOOGLE_MULTI_CHUNK_HOOK_VIDEO" not in existing_pm
        ):
            try:
                ok_inp_clean, _ = await asyncio.to_thread(
                    _verify_and_sanitize_refined_media, existing_mp, True, scene_id
                )
                if ok_inp_clean:
                    item["media_type"] = "hook_ready_video" if scene_id == 1 else "video"
                    replaced = True
                    logger.info(f"Scene {scene_id}: 🛡️ Đã làm sạch/nâng nét tại chỗ Video chuyển động thật '{Path(existing_mp).name}' (giữ nguyên Hook Video thật)")
            except Exception as _e_inp:
                logger.debug(f"Scene {scene_id}: In-place hook video sanitize skip: {_e_inp}")

        if scene_id == 1 and not replaced and not has_semantic_vision_defect:
            for hk_disk_cand in (work_dir / "hook_motion_001_fullpeak.mp4", work_dir / "hook_motion_001.mp4"):
                if not hk_disk_cand.exists() or str(hk_disk_cand) == existing_mp:
                    continue
                try:
                    ok_hk_d, _ = await asyncio.to_thread(
                        _verify_and_sanitize_refined_media, str(hk_disk_cand), True, 1
                    )
                    if ok_hk_d:
                        from app.utils.ffmpeg_helper import trim_video
                        out_hk_rec = str(media_dir / f"scene_001_refined_fullpeak_r{retry_count}.mp4")
                        await asyncio.to_thread(
                            trim_video,
                            input_path=str(hk_disk_cand),
                            duration_seconds=sc_dur_sec,
                            output_path=out_hk_rec,
                            width=w_ref,
                            height=h_ref,
                            fps=30,
                            is_scifi=is_scifi_val,
                            niche=current_niche,
                            apply_grading=True,
                            is_hook_scene=True,
                            fair_use_transform=True,
                            start_offset_sec=0.0,
                            preserve_continuous_flow=True,
                        )
                        target_hk_p = out_hk_rec if (Path(out_hk_rec).exists() and Path(out_hk_rec).stat().st_size > 12_000) else str(hk_disk_cand)
                        item["media_path"] = target_hk_p
                        item["media_type"] = "hook_ready_video"
                        item["pose_images"] = []
                        item["production_method"] = "REFINED_RECOVERED_HOOK_FULLPEAK_VIDEO"
                        replaced = True
                        logger.info(f"Scene 1: 🛡️ Đã khôi phục Video Hook chuyển động thật '{hk_disk_cand.name}' cho Cảnh 1!")
                        break
                except Exception as _e_hk_d:
                    logger.debug(f"Scene 1: Fullpeak disk recovery skip: {_e_hk_d}")

        if is_video:
            if is_lf_refine and not _refine_cancelled():
                try:
                    out_lf_ref = str(media_dir / f"scene_{scene_id:03d}_refined_16x9_r{retry_count}.mp4")
                    lf_ref_res = None
                    tainted_masters: set = set()
                    if has_semantic_vision_defect:
                        src_u_prev = str(item.get("source_url") or "")
                        if src_u_prev:
                            tainted_masters.add(Path(src_u_prev.split("#", 1)[0]).name.lower())
                        tainted_masters.add(f"chapter_{((int(scene_id) - 1) // 3) + 1:02d}_master.mp4")
                        for m_tok in re.findall(r"chapter_\d+_master\.mp4", str(item.get("strategy_rationale") or "").lower()):
                            tainted_masters.add(m_tok)

                    # Ưu tiên số 1 (~1.5–2.5s trên ổ cứng): Cắt lát mới không trùng từ kho Chapter Master 16:9 SẠCH (loại trừ Master bị lỗi ngữ nghĩa)
                    from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                    lf_local_res = await asyncio.to_thread(
                        slice_any_available_chapter_master_for_scene,
                        media_dir=media_dir,
                        scene_id=scene_id,
                        target_duration_sec=sc_dur_sec,
                        job_id=job_id_refine,
                        output_override_path=out_lf_ref,
                        exclude_masters=tainted_masters if tainted_masters else None,
                        max_master_uses_override=5,
                    )
                    if lf_local_res and lf_local_res.get("media_path") and Path(lf_local_res["media_path"]).exists():
                        ok_loc_clean, _ = await asyncio.to_thread(
                            _verify_and_sanitize_refined_media, lf_local_res["media_path"], True, scene_id
                        )
                        if ok_loc_clean:
                            lf_ref_res = lf_local_res
                        else:
                            Path(lf_local_res["media_path"]).unlink(missing_ok=True)

                    # Ưu tiên số 2 (chỉ khi cắt local chưa đạt, dành riêng 26s để luôn chừa >= 18s cho kho Stock/Ảnh HD dự phòng):
                    if not lf_ref_res and not _refine_cancelled():
                        now_ref_ts = time.monotonic()
                        lf_vid_dl = (
                            min(deadline_ts - 18.0, now_ref_ts + 26.0)
                            if (deadline_ts is not None and (deadline_ts - now_ref_ts) > 22.0)
                            else deadline_ts
                        )
                        lf_ref_res = await asyncio.to_thread(
                            _fetch_longform_16x9_broll_clip_sync,
                            scene_id=scene_id,
                            query=clean_kw_q or overall_refine_q,
                            primary_entity=primary_entity or subj_refine,
                            topic=state.get("topic", ""),
                            narration=narr or state.get("topic", ""),
                            target_duration_sec=sc_dur_sec,
                            media_dir=media_dir,
                            used_bvids=refine_used_bvids,
                            used_urls=used_refine_urls,
                            bvid_lock=refine_url_lock,
                            media_agent=media_agent,
                            is_scifi=is_scifi_val,
                            job_id=job_id_refine,
                            output_override_path=out_lf_ref,
                            cancel_event=cancel_event,
                            deadline_ts=lf_vid_dl,
                        )
                    if lf_ref_res and lf_ref_res.get("media_path") and Path(lf_ref_res["media_path"]).exists():
                        ok_lf_clean, _ = await asyncio.to_thread(
                            _verify_and_sanitize_refined_media, lf_ref_res["media_path"], True, scene_id
                        )
                        if ok_lf_clean:
                            item["media_path"] = lf_ref_res["media_path"]
                            item["media_type"] = "video"
                            item["pose_images"] = []
                            item["production_method"] = "REFINED_LONGFORM_16X9_VIDEO"
                            replaced = True
                            logger.info(f"Scene {scene_id}: ✅ Sửa lỗi kỹ thuật bằng Video 16:9 Widescreen mới: '{Path(lf_ref_res['media_path']).name}'")
                        else:
                            Path(lf_ref_res["media_path"]).unlink(missing_ok=True)
                except Exception as ex_lf_r:
                    logger.debug(f"Scene {scene_id}: Long-form 16:9 refine error: {ex_lf_r}")

            # Khi cảnh bị VisualCritic bắt lỗi sai thực thể/ngữ nghĩa (has_semantic_vision_defect=True),
            # ưu tiên săn YouTube Shorts chuyên sâu TRƯỚC (vì kho Pexels miễn phí đã trượt ở vòng đầu),
            # sau đó mới quét Pexels với truy vấn B-Roll bối cảnh/thực thể phụ (`critic_broll_q`) do VisualCritic đề xuất!
            if has_semantic_vision_defect and not replaced and not is_lf_refine and not _refine_cancelled():
                try:
                    from app.graph.tools.media_tools import execute_youtube_short_peak_clone_sync
                    from app.services.video_memory_rag import get_video_memory_rag
                    rag_yt_first = get_video_memory_rag()
                    yt_first_dl = (
                        min(deadline_ts - 18.0, time.monotonic() + 34.0)
                        if (deadline_ts is not None and (deadline_ts - time.monotonic()) > 24.0)
                        else (deadline_ts or (time.monotonic() + 34.0))
                    )
                    out_yt_first = str(media_dir / f"scene_{scene_id:03d}_refined_yt_peak_r{retry_count}.mp4")
                    yt_first_res = await asyncio.to_thread(
                        execute_youtube_short_peak_clone_sync,
                        search_query=clean_kw_q or overall_refine_q,
                        output_path=out_yt_first,
                        target_duration_sec=sc_dur_sec,
                        clip_role="HOOK" if scene_id == 1 else "PEAK",
                        used_urls=used_refine_urls,
                        fallback_query=critic_broll_q or overall_refine_q,
                        subject_entity=subj_refine,
                        narration=narr or state.get("topic", ""),
                        topic=state.get("topic", ""),
                        search_scope="SCENE_SPECIFIC" if critic_sug_q else "SCRIPT_OVERALL",
                        overall_script_query=overall_refine_q,
                        max_peak_duration_sec=14.0,
                        job_id=job_id_refine,
                        scene_id=scene_id,
                        channel_id=channel_id_refine,
                        skip_stock_fallback=True,
                        cancel_event=cancel_event,
                        deadline_ts=yt_first_dl,
                    )
                    if yt_first_res and yt_first_res.get("media_path") and Path(yt_first_res["media_path"]).exists():
                        ok_yt1_clean, _ = await asyncio.to_thread(
                            _verify_and_sanitize_refined_media, yt_first_res["media_path"], True, scene_id
                        )
                        ok_yt1_sig, _ = (
                            rag_yt_first.verify_and_commit_in_job_clip(
                                job_id=job_id_refine,
                                scene_id=scene_id,
                                media_path=yt_first_res["media_path"],
                                vid_id=str(yt_first_res.get("source_url") or yt_first_res["media_path"]),
                                url=str(yt_first_res.get("source_url") or yt_first_res["media_path"]),
                                title=str(yt_first_res.get("title") or clean_kw_q),
                            )
                            if ok_yt1_clean
                            else (False, "failed_refine_gate")
                        )
                        if ok_yt1_clean and ok_yt1_sig:
                            if yt_first_res.get("source_url"):
                                used_refine_urls.add(str(yt_first_res["source_url"]))
                            item["media_path"] = yt_first_res["media_path"]
                            item["media_type"] = "video"
                            item["pose_images"] = []
                            item["production_method"] = "REFINED_YOUTUBE_SHORT_PEAK"
                            item["source_url"] = str(yt_first_res.get("source_url") or yt_first_res["media_path"])
                            replaced = True
                            logger.info(f"Scene {scene_id}: ✅ Sửa lỗi thực thể/ngữ nghĩa bằng YouTube Short Peak 9:16 mới: '{Path(yt_first_res['media_path']).name}'")
                        else:
                            Path(yt_first_res["media_path"]).unlink(missing_ok=True)
                except Exception as ex_yt1:
                    logger.debug(f"Scene {scene_id}: YouTube Short priority refine error: {ex_yt1}")

            # 1. Tầng 1: Tìm kiếm Video Clip Pexels/Pixabay HD mới (sử dụng critic_broll_q nếu có để lấy B-Roll bối cảnh/thực thể phụ sạch chuẩn Tầng 2)
            if not replaced and not _refine_cancelled():
                try:
                    from app.services.video_memory_rag import get_video_memory_rag
                    from app.utils.ffmpeg_helper import trim_video
                    from app.graph.tools.tool_schemas import build_single_entity_stock_queries
                    rag_ref = get_video_memory_rag()
                    primary_pexels_q = clean_kw_q or critic_broll_q
                    ref_single_qs = build_single_entity_stock_queries(
                        primary_query=primary_pexels_q,
                        secondary_query=critic_broll_q if primary_pexels_q != critic_broll_q else (audited_fb_q or overall_refine_q),
                        subject_entity=subj_refine or primary_entity or "",
                        topic=state.get("topic", ""),
                        narration=narr,
                        max_words=3,
                    )
                    custom_pexels_qs: List[str] = []
                    for cq in (clean_kw_q, critic_broll_q, *(ref_single_qs or [])):
                        cq_s = str(cq or "").strip()
                        if cq_s and cq_s not in custom_pexels_qs:
                            custom_pexels_qs.append(cq_s)
                    va_ref_v = {
                        "video_format": "long" if is_lf_refine else "short",
                        "search_queries": custom_pexels_qs[:4] if custom_pexels_qs else [clean_kw_q],
                        "subject": subj_refine or primary_entity or clean_kw_q,
                        "core_visual_entity": subj_refine or primary_entity or clean_kw_q,
                        "negative_cues": [critic_fb] if critic_fb else [],
                        "skip_establishing_pass": True,
                    }
                    best_v = await asyncio.to_thread(
                        media_agent.search_best_video_clip,
                        custom_pexels_qs[0] if custom_pexels_qs else clean_kw_q,
                        used_refine_urls,
                        va_ref_v,
                        narr,
                        state.get("topic", ""),
                    )
                    if best_v and best_v.get("url"):
                        ref_cand_urls = [
                            best_v["url"],
                            *(best_v.get("_vision_alternates") or []),
                            *(best_v.get("alternate_urls") or []),
                        ]
                        cand_title = str(best_v.get("title") or clean_kw_q)
                        for cand_u_raw in ref_cand_urls[:4]:
                            cand_url = str(cand_u_raw or "").strip()
                            if not cand_url:
                                continue
                            if not rag_ref.try_claim_in_job_video(
                                job_id=job_id_refine, vid_id=cand_url, url=cand_url, title=cand_title, scene_id=scene_id
                            ):
                                used_refine_urls.add(cand_url)
                                continue
                            raw_v = str(media_dir / f"scene_{scene_id:03d}_refined_raw_r{retry_count}.mp4")
                            out_v = str(media_dir / f"scene_{scene_id:03d}_refined_video_r{retry_count}.mp4")
                            await asyncio.to_thread(media_agent.download_file, cand_url, raw_v)
                            if Path(raw_v).exists() and Path(raw_v).stat().st_size > 15_000:
                                await asyncio.to_thread(
                                    trim_video,
                                    input_path=raw_v,
                                    duration_seconds=sc_dur_sec,
                                    output_path=out_v,
                                    width=w_ref,
                                    height=h_ref,
                                    fps=30,
                                    is_scifi=is_scifi_val,
                                    niche=current_niche,
                                    apply_grading=True,
                                    is_hook_scene=(scene_id == 1),
                                    fair_use_transform=True,
                                    start_offset_sec=0.0,
                                    preserve_continuous_flow=True,
                                )
                                Path(raw_v).unlink(missing_ok=True)
                                if Path(out_v).exists() and Path(out_v).stat().st_size > 12_000:
                                    used_refine_urls.add(cand_url)
                                    ok_v_clean, _ = await asyncio.to_thread(
                                        _verify_and_sanitize_refined_media, out_v, True, scene_id
                                    )
                                    ok_v_sig, _ = (
                                        rag_ref.verify_and_commit_in_job_clip(
                                            job_id=job_id_refine,
                                            scene_id=scene_id,
                                            media_path=out_v,
                                            vid_id=cand_url,
                                            url=cand_url,
                                            title=cand_title,
                                        )
                                        if ok_v_clean
                                        else (False, "failed_refine_gate")
                                    )
                                    if ok_v_clean and ok_v_sig:
                                        item["media_path"] = out_v
                                        item["media_type"] = "video"
                                        item["pose_images"] = []
                                        item["production_method"] = "REFINED_STOCK_VIDEO"
                                        item["source_url"] = cand_url
                                        replaced = True
                                        logger.info(f"Scene {scene_id}: ✅ Sửa lỗi kỹ thuật bằng Video Clip Pexels mới độc lập: '{clean_kw_q}'")
                                        break
                                    else:
                                        Path(out_v).unlink(missing_ok=True)
                                        rag_ref.release_in_job_video_claim(
                                            job_id=job_id_refine, vid_id=cand_url, url=cand_url, title=cand_title, scene_id=scene_id
                                        )
                            else:
                                Path(raw_v).unlink(missing_ok=True)
                                rag_ref.release_in_job_video_claim(
                                    job_id=job_id_refine, vid_id=cand_url, url=cand_url, title=cand_title, scene_id=scene_id
                                )
                except Exception as ex_v:
                    logger.debug(f"Scene {scene_id}: Video refine error: {ex_v}")

            # 2. Tầng 2: Săn YouTube Shorts dọc 9:16 mới (chỉ chạy nếu chưa chạy ở nhánh ưu tiên has_semantic_vision_defect phía trên)
            if not replaced and not has_semantic_vision_defect and not is_lf_refine and not _refine_cancelled():
                try:
                    from app.graph.tools.media_tools import execute_youtube_short_peak_clone_sync
                    from app.services.video_memory_rag import get_video_memory_rag
                    rag_yt_ref = get_video_memory_rag()
                    yt_ref_dl = (
                        min(deadline_ts, time.monotonic() + 30.0)
                        if deadline_ts is not None
                        else (time.monotonic() + 30.0)
                    )
                    out_yt_ref = str(media_dir / f"scene_{scene_id:03d}_refined_yt_peak_r{retry_count}.mp4")
                    yt_ref_res = await asyncio.to_thread(
                        execute_youtube_short_peak_clone_sync,
                        search_query=clean_kw_q or overall_refine_q,
                        output_path=out_yt_ref,
                        target_duration_sec=sc_dur_sec,
                        clip_role="HOOK" if scene_id == 1 else "PEAK",
                        used_urls=used_refine_urls,
                        fallback_query=overall_refine_q,
                        subject_entity=subj_refine,
                        narration=narr or state.get("topic", ""),
                        topic=state.get("topic", ""),
                        search_scope="SCRIPT_OVERALL" if (is_mandatory_vid or has_semantic_vision_defect) else "SCENE_SPECIFIC",
                        overall_script_query=overall_refine_q,
                        max_peak_duration_sec=14.0,
                        job_id=job_id_refine,
                        scene_id=scene_id,
                        channel_id=channel_id_refine,
                        skip_stock_fallback=True,
                        cancel_event=cancel_event,
                        deadline_ts=yt_ref_dl,
                    )
                    if yt_ref_res and yt_ref_res.get("media_path") and Path(yt_ref_res["media_path"]).exists():
                        ok_yt_clean, _ = await asyncio.to_thread(
                            _verify_and_sanitize_refined_media, yt_ref_res["media_path"], True, scene_id
                        )
                        ok_yt_sig, _ = (
                            rag_yt_ref.verify_and_commit_in_job_clip(
                                job_id=job_id_refine,
                                scene_id=scene_id,
                                media_path=yt_ref_res["media_path"],
                                vid_id=str(yt_ref_res.get("source_url") or yt_ref_res["media_path"]),
                                url=str(yt_ref_res.get("source_url") or yt_ref_res["media_path"]),
                                title=str(yt_ref_res.get("title") or clean_kw_q),
                            )
                            if ok_yt_clean
                            else (False, "failed_refine_gate")
                        )
                        if ok_yt_clean and ok_yt_sig:
                            if yt_ref_res.get("source_url"):
                                used_refine_urls.add(str(yt_ref_res["source_url"]))
                            item["media_path"] = yt_ref_res["media_path"]
                            item["media_type"] = "video"
                            item["pose_images"] = []
                            item["production_method"] = "REFINED_YOUTUBE_SHORT_PEAK"
                            item["source_url"] = str(yt_ref_res.get("source_url") or yt_ref_res["media_path"])
                            replaced = True
                            logger.info(f"Scene {scene_id}: ✅ Sửa lỗi kỹ thuật bằng YouTube Short Peak 9:16 mới: '{Path(yt_ref_res['media_path']).name}'")
                        else:
                            Path(yt_ref_res["media_path"]).unlink(missing_ok=True)
                except Exception as ex_yt_r:
                    logger.debug(f"Scene {scene_id}: YouTube Short refine error: {ex_yt_r}")

        # 3. Tầng 3: Riêng 5-7s đầu (Scene 1 & 2) quét trực tiếp StockFetcher 2 từ cốt lõi; các cảnh >= 3 lấy 1 ảnh Google HD nếu chưa tìm được video
        if not replaced and not _refine_cancelled():
            try:
                from app.utils.ffmpeg_helper import trim_video
                from app.graph.tools.media_tools import execute_google_cinematic_image_sync
                from app.graph.tools.tool_schemas import build_single_entity_stock_queries
                from app.services.media.stock_fetcher import StockFetcher
                if is_mandatory_vid:
                    from app.graph.tools.video_fallback_executors import _try_bilibili_broll_fallback
                    from app.utils.ffmpeg_helper import get_duration_ms
                    from app.graph.nodes.hook_video_node import verify_hook_clip_genuine_motion
                    out_bili_open = str(media_dir / f"scene_{scene_id:03d}_refined_bili_open_r{retry_count}.mp4")
                    bili_open_res = await asyncio.to_thread(
                        _try_bilibili_broll_fallback,
                        search_phrases=[q_x for q_x in dict.fromkeys([clean_kw_q, critic_broll_q, overall_refine_q]) if q_x][:4],
                        search_query=clean_kw_q or overall_refine_q,
                        subject_entity=subj_refine or primary_entity or "",
                        topic=state.get("topic", ""),
                        narration=narr or state.get("topic", ""),
                        overall_script_query=overall_refine_q,
                        effective_scope="SCENE_SPECIFIC" if critic_sug_q else "SCRIPT_OVERALL",
                        output_path=out_bili_open,
                        target_duration_sec=sc_dur_sec,
                        clip_role="HOOK" if scene_id == 1 else "PEAK",
                        allow_3d_cgi_monster=False,
                        used=used_refine_urls,
                        job_id=job_id_refine,
                        eff_scene_id=scene_id,
                        cancel_event=cancel_event,
                        deadline_ts=deadline_ts,
                    )
                    if bili_open_res and bili_open_res.get("media_path") and Path(bili_open_res["media_path"]).exists():
                        item["media_path"] = bili_open_res["media_path"]
                        item["media_type"] = "hook_ready_video" if scene_id == 1 else "video"
                        item["pose_images"] = []
                        item["production_method"] = "REFINED_BILIBILI_DOCUMENTARY_VIDEO"
                        item["source_url"] = str(bili_open_res.get("source_url") or bili_open_res["media_path"])
                        replaced = True
                        logger.info(f"Scene {scene_id}: 🎬 [Mandatory Video Bilibili Documentary] Đã nạp Video tư liệu kiểm định Vision cho '{clean_kw_q}'")
                    if not replaced and scene_id in (1, 2):
                        reserve_cand_ref = (
                            work_dir / "hook_motion_001_fullpeak.mp4"
                            if scene_id == 1
                            else work_dir / "hook_reserve_002.mp4"
                        )
                        if not reserve_cand_ref.exists() and scene_id == 2:
                            reserve_cand_ref = work_dir / "hook_motion_001_fullpeak.mp4"
                        if reserve_cand_ref.exists() and reserve_cand_ref.stat().st_size > 20_000:
                            res_dur_ref = get_duration_ms(str(reserve_cand_ref)) / 1000.0
                            start_off_ref = 0.0 if reserve_cand_ref.name == "hook_reserve_002.mp4" or scene_id == 1 else 3.35
                            if res_dur_ref >= start_off_ref + max(2.4, sc_dur_sec * 0.75):
                                out_res_ref = str(media_dir / f"scene_{scene_id:03d}_refined_hook_reserve_r{retry_count}.mp4")
                                await asyncio.to_thread(
                                    trim_video,
                                    input_path=str(reserve_cand_ref),
                                    duration_seconds=sc_dur_sec,
                                    output_path=out_res_ref,
                                    width=w_ref,
                                    height=h_ref,
                                    fps=30,
                                    is_scifi=is_scifi_val,
                                    niche=current_niche,
                                    apply_grading=True,
                                    is_hook_scene=(scene_id == 1),
                                    fair_use_transform=(start_off_ref > 0.0),
                                    start_offset_sec=start_off_ref,
                                    preserve_continuous_flow=True,
                                )
                                if Path(out_res_ref).exists() and Path(out_res_ref).stat().st_size > 15_000:
                                    ok_ref_mot, _, _ = await asyncio.to_thread(verify_hook_clip_genuine_motion, out_res_ref)
                                    if ok_ref_mot:
                                        item["media_path"] = out_res_ref
                                        item["media_type"] = "hook_ready_video" if scene_id == 1 else "video"
                                        item["pose_images"] = []
                                        item["production_method"] = "REFINED_YOUTUBE_SHORT_PEAK"
                                        item["source_url"] = str(reserve_cand_ref)
                                        replaced = True
                                        logger.info(
                                            f"Scene {scene_id}: 🔥 [Opening 5-7s Hook Reserve] Đã nạp Video chuyển động thật đã duyệt Vision ('{reserve_cand_ref.name}') -> {Path(out_res_ref).name}"
                                        )
                                    else:
                                        Path(out_res_ref).unlink(missing_ok=True)
                if not replaced and scene_id not in (1, 2):
                    out_resc_img = str(media_dir / f"scene_{scene_id:03d}_refined_gimg_rescue_r{retry_count}.jpg")
                    g_res = await asyncio.to_thread(
                        execute_google_cinematic_image_sync,
                        smart_query=clean_kw_q or overall_refine_q,
                        output_path=out_resc_img,
                        narration=narr,
                        topic=state.get("topic", ""),
                        is_hook_scene=False,
                        subject_entity=subj_refine or primary_entity,
                        used_urls=used_refine_urls,
                        job_id=job_id_refine,
                        scene_id=scene_id,
                    )
                    if g_res and g_res.get("media_path") and Path(g_res["media_path"]).exists():
                        item["media_path"] = g_res["media_path"]
                        item["media_type"] = "image"
                        item["pose_images"] = g_res.get("pose_images") or [g_res["media_path"]]
                        item["production_method"] = "REFINED_GOOGLE_IMAGE_RESCUE"
                        item["source_url"] = str(g_res.get("source_url") or "")
                        replaced = True
                        logger.info(f"Scene {scene_id}: 🛡️ [RefineMedia] Đã tải nguồn Ảnh Google độc lập mới ('{clean_kw_q}')")
            except Exception as ex_resc:
                logger.debug(f"Scene {scene_id}: Clean motion rescue error: {ex_resc}")

        if scene_id == 1 and replaced and str(item.get("media_path") or "") != existing_mp:
            item["hook_verified"] = False
            item.pop("hook_sharpness", None)

    refine_sem = asyncio.Semaphore(8 if is_lf_refine else 6)
    refine_item_timeout = 68.0 if is_lf_refine else 66.0

    async def _guarded_refine_single_item(item: Dict[str, Any]) -> None:
        if item.get("scene_id") not in failed_ids:
            return
        async with refine_sem:
            ref_cancel_ev = threading.Event()
            ref_deadline_ts = time.monotonic() + (refine_item_timeout - 2.0)
            from app.agents.curation.scene_context_analyzer_mixin import bind_scene_cancellation
            bind_scene_cancellation(ref_cancel_ev, ref_deadline_ts)
            try:
                await asyncio.wait_for(
                    _refine_single_item(
                        item,
                        cancel_event=ref_cancel_ev,
                        deadline_ts=ref_deadline_ts,
                    ),
                    timeout=refine_item_timeout,
                )
            except Exception as _e_ref_to:
                ref_cancel_ev.set()
                logger.debug(f"[RefineMedia] Scene {item.get('scene_id')} timeout/skip: {_e_ref_to}")
                try:
                    sid_to = int(item.get("scene_id") or 0)
                    prev_mp_to = str(item.get("media_path") or "")
                    ref_cands_to = sorted(
                        [
                            p for p in media_dir.glob(f"scene_{sid_to:03d}_refined_*.mp4")
                            if str(p) != prev_mp_to and is_completed_clean_mp4(p, min_size_bytes=20_000, min_duration_ms=1200)
                        ],
                        key=_safe_mtime,
                        reverse=True,
                    )
                    recovered_to = False
                    for rc_p in ref_cands_to[:2]:
                        ok_rc_to, _ = await asyncio.to_thread(_verify_and_sanitize_refined_media, str(rc_p), True, sid_to)
                        if ok_rc_to:
                            item["media_path"] = str(rc_p)
                            item["media_type"] = "hook_ready_video" if sid_to == 1 else "video"
                            item["pose_images"] = []
                            item["production_method"] = "REFINED_TIMEOUT_DISK_RECOVERED_VIDEO"
                            recovered_to = True
                            logger.info(
                                f"Scene {sid_to}: 🛡️ [RefineMedia Timeout Recovery] Đã khôi phục file '{rc_p.name}' vừa tải xong trên đĩa!"
                            )
                            break
                    if not recovered_to and sid_to not in (1, 2):
                        ref_imgs_to = sorted(
                            [
                                p for p in media_dir.glob(f"scene_{sid_to:03d}_*.jpg")
                                if _safe_size(p) > 3_000 and "_cand" not in p.name
                            ],
                            key=lambda p: (0 if "_cut" not in p.name else 1, -_safe_mtime(p)),
                        )
                        if ref_imgs_to:
                            rec_img_poses = [str(p) for p in ref_imgs_to[:2]]
                            item["media_path"] = rec_img_poses[0]
                            item["media_type"] = "image"
                            item["pose_images"] = rec_img_poses
                            item["production_method"] = "REFINED_TIMEOUT_DISK_RECOVERED_IMAGE"
                            logger.info(
                                f"Scene {sid_to}: 🛡️ [RefineMedia Timeout Recovery] Đã khôi phục ảnh '{ref_imgs_to[0].name}' vừa tải xong trên đĩa!"
                            )
                except Exception:
                    pass
                if is_lf_refine and str(item.get("media_path") or "").lower().endswith("_graphic.png"):
                    try:
                        from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                        sid_to = int(item.get("scene_id") or 0)
                        dur_to = _scene_audio_duration(work_dir, sid_to)
                        out_to_ref = str(media_dir / f"scene_{sid_to:03d}_refined_timeout_pool_r{retry_count}.mp4")
                        to_pool_res = await asyncio.to_thread(
                            slice_any_available_chapter_master_for_scene,
                            media_dir=media_dir,
                            scene_id=sid_to,
                            target_duration_sec=dur_to,
                            job_id=job_id_refine,
                            output_override_path=out_to_ref,
                            max_master_uses_override=20,
                        )
                        if to_pool_res and to_pool_res.get("media_path") and Path(to_pool_res["media_path"]).exists():
                            item["media_path"] = to_pool_res["media_path"]
                            item["media_type"] = "video"
                            item["pose_images"] = []
                            item["production_method"] = "REFINED_LONGFORM_16X9_TIMEOUT_RESCUE"
                            logger.info(f"Scene {sid_to}: 🛡️ [RefineMedia Timeout Rescue] Thay _graphic.png bằng lát cắt ChapterPool 16:9 -> {Path(to_pool_res['media_path']).name}")
                    except Exception:
                        pass

    await asyncio.gather(*[_guarded_refine_single_item(item) for item in scenes_media if isinstance(item, dict)])

    return {
        "scenes_media": scenes_media,
        "media_retry_count": retry_count,
        "current_step": "MEDIA_REFINED",
        "progress_message": f"Đã tự động sửa lại song song {len(failed_ids)} cảnh media kỹ thuật chưa đạt",
    }
