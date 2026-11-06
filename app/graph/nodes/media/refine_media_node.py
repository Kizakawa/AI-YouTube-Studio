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
    for _prev_rej in (state.get("rejected_media_urls") or []):
        if _prev_rej:
            used_refine_urls.add(str(_prev_rej))
    for _m in scenes_media:
        if isinstance(_m, dict) and _m.get("source_url"):
            used_refine_urls.add(_m["source_url"])

    total_scenes_refine = len(scenes_media)
    mandatory_video_refine_sids = {1, 2, max(1, total_scenes_refine - 1), total_scenes_refine}
    job_id_refine = int(state.get("job_id") or 0)
    channel_id_refine = str(state.get("target_channel_id") or state.get("channel_id") or "")
    try:
        from app.services.video_memory_rag import get_video_memory_rag
        _rag_init_ref = get_video_memory_rag()
        for _m_init in scenes_media:
            if not isinstance(_m_init, dict):
                continue
            _sid_init = int(_m_init.get("scene_id") or 0)
            _mp_init = str(_m_init.get("media_path") or "").strip()
            _surl_init = str(_m_init.get("source_url") or "").strip()
            if _surl_init:
                used_refine_urls.add(_surl_init)
            if _sid_init > 0 and _mp_init and _mp_init.lower().endswith((".mp4", ".mov", ".webm")) and Path(_mp_init).exists():
                if _sid_init not in failed_ids:
                    _rag_init_ref.verify_and_commit_in_job_clip(
                        job_id=job_id_refine,
                        scene_id=_sid_init,
                        media_path=_mp_init,
                        vid_id=_surl_init or _mp_init,
                        url=_surl_init or _mp_init,
                        title=str(_m_init.get("production_method") or ""),
                    )
    except Exception:
        pass
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
            if scene_id == 1:
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
                min_short_edge=720,
                min_sharpness=12.0 if scene_id == 1 else 5.8,
                check_watermark=False,
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
        critic_sug_q = str(item.get("critic_suggested_query") or "").strip()
        critic_broll_q = str(item.get("critic_fallback_broll_query") or "").strip()
        critic_fb = str(item.get("critic_feedback") or "").strip()
        has_semantic_vision_defect = bool(
            critic_fb
            or critic_sug_q
            or any(
                k in str(iss).lower()
                for iss in sc_issues_list
                for k in ("gemini vision", "contactsheet", "trùng lặp", "điểm ", "< 7.5", "sai ", "watermark", "logo", "phụ đề")
            )
        )
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

        existing_mp = str(item.get("media_path") or "")
        existing_pm = str(item.get("production_method") or "").upper()

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

                    from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                    if not has_semantic_vision_defect:
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

                    # Khi cảnh bị lỗi ngữ nghĩa/sai chủ thể (hoặc cắt local chưa đạt), tìm kiếm B-Roll 16:9 mới theo truy vấn của VisualCritic trước:
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
                    if not lf_ref_res and has_semantic_vision_defect and not _refine_cancelled():
                        lf_local_fb = await asyncio.to_thread(
                            slice_any_available_chapter_master_for_scene,
                            media_dir=media_dir,
                            scene_id=scene_id,
                            target_duration_sec=sc_dur_sec,
                            job_id=job_id_refine,
                            output_override_path=out_lf_ref,
                            exclude_masters=tainted_masters if tainted_masters else None,
                            max_master_uses_override=5,
                        )
                        if lf_local_fb and lf_local_fb.get("media_path") and Path(lf_local_fb["media_path"]).exists():
                            lf_ref_res = lf_local_fb
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

            # Chạy SONG SONG đồng thời cả 2 nhánh Pexels/Pixabay HD và YouTube Shorts Peak ngay từ t=0.0s:
            # Nhánh nào tìm được Video đạt chuẩn Vision + CV Gate trước sẽ lập tức hủy nhánh còn lại!
            if not replaced and not is_lf_refine and not _refine_cancelled():
                from app.graph.tools.media_tools import execute_youtube_short_peak_clone_sync
                from app.services.video_memory_rag import get_video_memory_rag
                from app.utils.ffmpeg_helper import trim_video
                from app.graph.tools.tool_schemas import build_single_entity_stock_queries

                class _RefineBranchCancelEvent:
                    def __init__(self, parent_ev: Optional[threading.Event]) -> None:
                        self._parent = parent_ev
                        self._local = threading.Event()

                    def is_set(self) -> bool:
                        return self._local.is_set() or (self._parent is not None and self._parent.is_set())

                    def set(self) -> None:
                        self._local.set()

                px_ref_ev = _RefineBranchCancelEvent(cancel_event)
                yt_ref_ev = _RefineBranchCancelEvent(cancel_event)
                bili_ref_ev = _RefineBranchCancelEvent(cancel_event)

                async def _refine_pexels_branch() -> Optional[Dict[str, Any]]:
                    try:
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
                        for cq in (*(ref_single_qs or []), critic_broll_q, clean_kw_q):
                            cq_s = str(cq or "").strip()
                            if cq_s and cq_s not in custom_pexels_qs:
                                custom_pexels_qs.append(cq_s)
                        eff_pexels_subj = subj_refine or primary_entity or clean_kw_q
                        if critic_broll_q and (retry_count >= 2 or critic_broll_q.lower() not in eff_pexels_subj.lower()):
                            eff_pexels_subj = f"{eff_pexels_subj} (or atmospheric B-roll: {critic_broll_q})"
                        va_ref_v = {
                            "video_format": "long" if is_lf_refine else "short",
                            "search_queries": custom_pexels_qs[:4] if custom_pexels_qs else [clean_kw_q],
                            "subject": eff_pexels_subj,
                            "core_visual_entity": eff_pexels_subj,
                            "negative_cues": (
                                [critic_fb]
                                if (
                                    critic_fb
                                    and "placeholder" not in critic_fb.lower()
                                    and "missing a valid preview" not in critic_fb.lower()
                                )
                                else []
                            ),
                            "skip_establishing_pass": True,
                        }
                        px_dl = (
                            min(deadline_ts - 8.0, time.monotonic() + 24.0)
                            if deadline_ts is not None
                            else (time.monotonic() + 24.0)
                        )
                        best_v = await asyncio.to_thread(
                            media_agent.search_best_video_clip,
                            custom_pexels_qs[0] if custom_pexels_qs else clean_kw_q,
                            used_refine_urls,
                            va_ref_v,
                            narr,
                            state.get("topic", ""),
                            cancel_event=px_ref_ev,
                            deadline_ts=px_dl,
                        )
                        if px_ref_ev.is_set() or not (best_v and best_v.get("url")):
                            return None
                        ref_cand_urls = [
                            best_v["url"],
                            *(best_v.get("_vision_alternates") or []),
                            *(best_v.get("alternate_urls") or []),
                        ]
                        cand_title = str(best_v.get("title") or clean_kw_q)
                        for cand_u_raw in ref_cand_urls[:4]:
                            if px_ref_ev.is_set():
                                break
                            cand_url = str(cand_u_raw or "").strip()
                            if not cand_url:
                                continue
                            if not rag_ref.try_claim_in_job_video(
                                job_id=job_id_refine, vid_id=cand_url, url=cand_url, title=cand_title, scene_id=scene_id
                            ):
                                used_refine_urls.add(cand_url)
                                continue
                            raw_v = str(media_dir / f"scene_{scene_id:03d}_refined_raw_r{retry_count}.mp4")
                            cand_v = str(media_dir / f"scene_{scene_id:03d}_refined_cand_r{retry_count}.mp4")
                            out_v = str(media_dir / f"scene_{scene_id:03d}_refined_video_r{retry_count}.mp4")
                            try:
                                await asyncio.to_thread(media_agent.download_file, cand_url, raw_v)
                                if px_ref_ev.is_set():
                                    rag_ref.release_in_job_video_claim(
                                        job_id=job_id_refine, vid_id=cand_url, url=cand_url, title=cand_title, scene_id=scene_id
                                    )
                                    return None
                                if Path(raw_v).exists() and Path(raw_v).stat().st_size > 15_000:
                                    await asyncio.to_thread(
                                        trim_video,
                                        input_path=raw_v,
                                        duration_seconds=sc_dur_sec,
                                        output_path=cand_v,
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
                                    if Path(cand_v).exists() and Path(cand_v).stat().st_size > 12_000:
                                        used_refine_urls.add(cand_url)
                                        ok_v_clean, _ = await asyncio.to_thread(
                                            _verify_and_sanitize_refined_media, cand_v, True, scene_id
                                        )
                                        is_ref_v_vis_ok = bool(best_v.get("vision_curated")) and (cand_url == str(best_v.get("url") or ""))
                                        if ok_v_clean and not is_ref_v_vis_ok:
                                            from app.graph.tools.youtube_search_ranker import _verify_short_clip_with_gemini_vision
                                            is_ref_v_vis_ok, _, _ = await asyncio.to_thread(
                                                _verify_short_clip_with_gemini_vision,
                                                video_path=cand_v,
                                                topic=state.get("topic", ""),
                                                narration=narr,
                                                subject_entity=subj_refine or primary_entity,
                                                overall_script_query=overall_refine_q,
                                                search_scope="SCENE_SPECIFIC",
                                                video_title=cand_title,
                                                allow_3d_cgi_monster=False,
                                                strict_no_fallback=True,
                                                cancel_event=px_ref_ev,
                                                deadline_ts=max(px_dl, time.monotonic() + 12.0),
                                                target_visual_query=clean_kw_q,
                                            )
                                        ok_v_sig, _ = (
                                            rag_ref.verify_and_commit_in_job_clip(
                                                job_id=job_id_refine,
                                                scene_id=scene_id,
                                                media_path=cand_v,
                                                vid_id=cand_url,
                                                url=cand_url,
                                                title=cand_title,
                                            )
                                            if (ok_v_clean and is_ref_v_vis_ok)
                                            else (False, "failed_refine_gate")
                                        )
                                        if ok_v_clean and is_ref_v_vis_ok and ok_v_sig:
                                            os.replace(cand_v, out_v)
                                            logger.info(f"Scene {scene_id}: ✅ [Parallel Pexels+YT] Sửa lỗi kỹ thuật bằng Video Clip Pexels mới độc lập: '{clean_kw_q}'")
                                            return {
                                                "media_path": out_v,
                                                "media_type": "video",
                                                "production_method": "REFINED_STOCK_VIDEO",
                                                "source_url": cand_url,
                                                "vid_id": cand_url,
                                                "title": cand_title,
                                            }
                                        else:
                                            rag_ref.release_in_job_video_claim(
                                                job_id=job_id_refine, vid_id=cand_url, url=cand_url, title=cand_title, scene_id=scene_id
                                            )
                                else:
                                    rag_ref.release_in_job_video_claim(
                                        job_id=job_id_refine, vid_id=cand_url, url=cand_url, title=cand_title, scene_id=scene_id
                                    )
                            finally:
                                Path(raw_v).unlink(missing_ok=True)
                                Path(cand_v).unlink(missing_ok=True)
                    except Exception as ex_v:
                        logger.debug(f"Scene {scene_id}: Parallel Pexels refine error: {ex_v}")
                    return None

                async def _refine_youtube_branch() -> Optional[Dict[str, Any]]:
                    try:
                        rag_yt_ref = get_video_memory_rag()
                        yt_ref_dl = (
                            min(deadline_ts - 8.0, time.monotonic() + 64.0)
                            if (deadline_ts is not None and (deadline_ts - time.monotonic()) > 14.0)
                            else (deadline_ts or (time.monotonic() + 64.0))
                        )
                        out_yt_ref = str(media_dir / f"scene_{scene_id:03d}_refined_yt_peak_r{retry_count}.mp4")
                        yt_ref_res = await asyncio.to_thread(
                            execute_youtube_short_peak_clone_sync,
                            search_query=clean_kw_q or overall_refine_q,
                            output_path=out_yt_ref,
                            target_duration_sec=sc_dur_sec,
                            clip_role="HOOK" if scene_id == 1 else "PEAK",
                            used_urls=used_refine_urls,
                            fallback_query=critic_broll_q or overall_refine_q,
                            subject_entity=subj_refine,
                            narration=narr or state.get("topic", ""),
                            topic=state.get("topic", ""),
                            search_scope="SCENE_SPECIFIC" if critic_sug_q else ("SCRIPT_OVERALL" if is_mandatory_vid else "SCENE_SPECIFIC"),
                            overall_script_query=overall_refine_q,
                            max_peak_duration_sec=14.0,
                            job_id=job_id_refine,
                            scene_id=scene_id,
                            channel_id=channel_id_refine,
                            skip_stock_fallback=True,
                            allow_bili_fallback=False,
                            cancel_event=yt_ref_ev,
                            deadline_ts=yt_ref_dl,
                        )
                        if yt_ref_res and yt_ref_res.get("media_path") and Path(yt_ref_res["media_path"]).exists():
                            ok_yt_clean, _ = await asyncio.to_thread(
                                _verify_and_sanitize_refined_media, yt_ref_res["media_path"], True, scene_id
                            )
                            vid_id_yt = str(yt_ref_res.get("source_url") or yt_ref_res["media_path"])
                            title_yt = str(yt_ref_res.get("title") or clean_kw_q)
                            ok_yt_sig, _ = (
                                rag_yt_ref.verify_and_commit_in_job_clip(
                                    job_id=job_id_refine,
                                    scene_id=scene_id,
                                    media_path=yt_ref_res["media_path"],
                                    vid_id=vid_id_yt,
                                    url=vid_id_yt,
                                    title=title_yt,
                                )
                                if ok_yt_clean
                                else (False, "failed_refine_gate")
                            )
                            if ok_yt_clean and ok_yt_sig:
                                if yt_ref_res.get("source_url"):
                                    used_refine_urls.add(str(yt_ref_res["source_url"]))
                                logger.info(f"Scene {scene_id}: ✅ [Parallel Pexels+YT] Sửa lỗi bằng YouTube Short Peak 9:16 mới: '{Path(yt_ref_res['media_path']).name}'")
                                return {
                                    "media_path": yt_ref_res["media_path"],
                                    "media_type": "video",
                                    "production_method": "REFINED_YOUTUBE_SHORT_PEAK",
                                    "source_url": vid_id_yt,
                                    "vid_id": vid_id_yt,
                                    "title": title_yt,
                                }
                            else:
                                Path(yt_ref_res["media_path"]).unlink(missing_ok=True)
                    except Exception as ex_yt_r:
                        logger.warning(f"Scene {scene_id}: Parallel YouTube Short refine error: {ex_yt_r}")
                    return None

                async def _refine_bilibili_branch() -> Optional[Dict[str, Any]]:
                    await asyncio.sleep(2.5)
                    if bili_ref_ev.is_set():
                        return None
                    try:
                        from app.graph.tools.video_fallback_executors import _try_bilibili_broll_fallback
                        bili_ref_dl = (
                            min(deadline_ts - 8.0, time.monotonic() + 64.0)
                            if (deadline_ts is not None and (deadline_ts - time.monotonic()) > 14.0)
                            else (deadline_ts or (time.monotonic() + 64.0))
                        )
                        out_bili_r = str(media_dir / f"scene_{scene_id:03d}_refined_bili_par_r{retry_count}.mp4")
                        bili_r_res = await asyncio.to_thread(
                            _try_bilibili_broll_fallback,
                            search_phrases=[q_x for q_x in dict.fromkeys([clean_kw_q, critic_broll_q, overall_refine_q]) if q_x][:4],
                            search_query=clean_kw_q or overall_refine_q,
                            subject_entity=subj_refine or primary_entity or "",
                            topic=state.get("topic", ""),
                            narration=narr or state.get("topic", ""),
                            overall_script_query=overall_refine_q,
                            effective_scope="SCRIPT_OVERALL" if retry_count >= 2 else ("SCENE_SPECIFIC" if critic_sug_q else "SCRIPT_OVERALL"),
                            output_path=out_bili_r,
                            target_duration_sec=sc_dur_sec,
                            clip_role="HOOK" if scene_id == 1 else "PEAK",
                            allow_3d_cgi_monster=False,
                            used=used_refine_urls,
                            job_id=job_id_refine,
                            eff_scene_id=scene_id,
                            cancel_event=bili_ref_ev,
                            deadline_ts=bili_ref_dl,
                        )
                        if bili_r_res and bili_r_res.get("media_path") and Path(bili_r_res["media_path"]).exists():
                            src_bili = str(bili_r_res.get("source_url") or bili_r_res["media_path"])
                            used_refine_urls.add(src_bili)
                            logger.info(f"Scene {scene_id}: ✅ [Parallel Pexels+YT+Bili] Sửa lỗi bằng Bilibili Documentary 9:16 mới: '{Path(bili_r_res['media_path']).name}'")
                            return {
                                "media_path": bili_r_res["media_path"],
                                "media_type": "hook_ready_video" if scene_id == 1 else "video",
                                "production_method": "REFINED_BILIBILI_DOCUMENTARY_VIDEO",
                                "source_url": src_bili,
                                "vid_id": src_bili,
                                "title": str(bili_r_res.get("title") or clean_kw_q),
                            }
                    except Exception as ex_bili_r:
                        logger.warning(f"Scene {scene_id}: Parallel Bilibili refine error: {ex_bili_r}")
                    return None

                t_px_r = asyncio.create_task(_refine_pexels_branch())
                t_yt_r = asyncio.create_task(_refine_youtube_branch())
                t_bili_r = asyncio.create_task(_refine_bilibili_branch())
                pending_r = {t_px_r, t_yt_r, t_bili_r}
                win_r: Optional[Dict[str, Any]] = None
                extra_r: List[Dict[str, Any]] = []
                race_ref_deadline = (
                    min(deadline_ts - 8.0, time.monotonic() + 66.0)
                    if (deadline_ts is not None and (deadline_ts - time.monotonic()) > 14.0)
                    else (min(deadline_ts - 5.0, time.monotonic() + 66.0) if deadline_ts is not None else (time.monotonic() + 66.0))
                )

                while pending_r and win_r is None:
                    wait_rem_r = race_ref_deadline - time.monotonic()
                    if wait_rem_r <= 0.15:
                        break
                    done_r, pending_r = await asyncio.wait(
                        pending_r,
                        return_when=asyncio.FIRST_COMPLETED,
                        timeout=wait_rem_r,
                    )
                    if not done_r:
                        break
                    for d_tr in done_r:
                        try:
                            rv = d_tr.result()
                        except Exception:
                            rv = None
                        if rv and rv.get("media_path") and Path(rv["media_path"]).exists():
                            if win_r is None:
                                win_r = rv
                            else:
                                extra_r.append(rv)

                if win_r is None and pending_r:
                    grace_ref_s = max(0.5, min(6.0, (deadline_ts - time.monotonic() - 8.0) if deadline_ts is not None else 6.0))
                    if grace_ref_s > 0.5:
                        done_gr, pending_r = await asyncio.wait(
                            pending_r,
                            return_when=asyncio.FIRST_COMPLETED,
                            timeout=grace_ref_s,
                        )
                        for d_gr in done_gr:
                            try:
                                rv_gr = d_gr.result()
                            except Exception:
                                rv_gr = None
                            if rv_gr and rv_gr.get("media_path") and Path(rv_gr["media_path"]).exists():
                                if win_r is None:
                                    win_r = rv_gr
                                else:
                                    extra_r.append(rv_gr)

                px_ref_ev.set()
                yt_ref_ev.set()
                bili_ref_ev.set()
                for p_tr in pending_r:
                    p_tr.cancel()

                if extra_r:
                    rag_cl_r = get_video_memory_rag()
                    for ex_item in extra_r:
                        try:
                            Path(ex_item["media_path"]).unlink(missing_ok=True)
                            rag_cl_r.release_in_job_video_claim(
                                job_id=job_id_refine,
                                vid_id=str(ex_item.get("vid_id") or ""),
                                url=str(ex_item.get("source_url") or ""),
                                title=str(ex_item.get("title") or ""),
                                scene_id=scene_id,
                            )
                        except Exception:
                            pass

                if win_r is not None:
                    item["media_path"] = win_r["media_path"]
                    item["media_type"] = win_r["media_type"]
                    item["pose_images"] = []
                    item["production_method"] = win_r["production_method"]
                    item["source_url"] = win_r["source_url"]
                    replaced = True

        # 3. Tầng 3: Ưu tiên tuyệt đối đạt > 85% thời lượng là Video chuyển động thật (5-7 đoạn video tải về).
        if not replaced and not _refine_cancelled():
            try:
                from app.graph.tools.media_tools import execute_google_cinematic_image_sync
                from app.graph.tools.video_fallback_executors import _try_bilibili_broll_fallback

                other_vid_scenes_cnt = sum(
                    1 for _sx in scenes_media
                    if isinstance(_sx, dict)
                    and int(_sx.get("scene_id") or 0) != scene_id
                    and str(_sx.get("media_type") or "").lower() in ("video", "hook_ready_video")
                    and str(_sx.get("media_path") or "").lower().endswith((".mp4", ".mov", ".webm"))
                )
                total_refine_dur_s = sum(
                    _scene_audio_duration(work_dir, int(_sx.get("scene_id") or (i_rx + 1)))
                    for i_rx, _sx in enumerate(scenes_media)
                    if isinstance(_sx, dict)
                ) or 1.0
                other_vid_dur_s = sum(
                    _scene_audio_duration(work_dir, int(_sx.get("scene_id") or (i_rx + 1)))
                    for i_rx, _sx in enumerate(scenes_media)
                    if isinstance(_sx, dict)
                    and int(_sx.get("scene_id") or 0) != scene_id
                    and str(_sx.get("media_type") or "").lower() in ("video", "hook_ready_video")
                    and str(_sx.get("media_path") or "").lower().endswith((".mp4", ".mov", ".webm"))
                )
                min_req_vids_floor = max(5, int((len(scenes_media) * 0.85) + 0.999))
                video_floor_satisfied_without_scene = (
                    other_vid_scenes_cnt >= min_req_vids_floor
                    and (other_vid_dur_s / total_refine_dur_s) > 0.85
                )

                if is_mandatory_vid or not video_floor_satisfied_without_scene:
                    out_bili_open = str(media_dir / f"scene_{scene_id:03d}_refined_bili_open_r{retry_count}.mp4")
                    bili_open_res = await asyncio.to_thread(
                        _try_bilibili_broll_fallback,
                        search_phrases=[q_x for q_x in dict.fromkeys([clean_kw_q, critic_broll_q, overall_refine_q]) if q_x][:4],
                        search_query=clean_kw_q or overall_refine_q,
                        subject_entity=subj_refine or primary_entity or "",
                        topic=state.get("topic", ""),
                        narration=narr or state.get("topic", ""),
                        overall_script_query=overall_refine_q,
                        effective_scope="SCRIPT_OVERALL" if retry_count >= 2 else ("SCENE_SPECIFIC" if critic_sug_q else "SCRIPT_OVERALL"),
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
                        logger.info(f"Scene {scene_id}: 🎬 [Mandatory >85% Video Bilibili Documentary] Đã nạp Video tư liệu kiểm định Vision cho '{clean_kw_q}'")

                # LUẬT THÉP > 85% THỜI LƯỢNG VIDEO:
                # Tuyệt đối CẤM hạ cấp một cảnh ĐANG LÀ VIDEO xuống Ảnh tĩnh Google nếu tổng thời lượng các cảnh Video thật còn lại <= 85.0%.
                # NHƯNG nếu cảnh hiện tại ĐANG LÀ MÀN HÌNH XANH RỖNG (_graphic.png / GRAPHIC_FALLBACK / mất file) và mọi tầng Video đều hết ứng viên,
                # bắt buộc thay màn hình xanh rỗng bằng Ảnh thật Google HD để không bao giờ lọt placeholder rỗng vào video xuất bản!
                is_blank_placeholder = (
                    not existing_mp
                    or not Path(existing_mp).exists()
                    or existing_mp.lower().endswith("_graphic.png")
                    or existing_pm in ("GRAPHIC_FALLBACK", "PLACEHOLDER")
                )
                can_downgrade_to_image = (
                    scene_id not in mandatory_video_refine_sids
                    and video_floor_satisfied_without_scene
                )
                if not replaced and (is_blank_placeholder or (scene_id not in (1, 2) and can_downgrade_to_image)):
                    out_resc_img = str(media_dir / f"scene_{scene_id:03d}_refined_gimg_rescue_r{retry_count}.jpg")
                    g_res = await asyncio.to_thread(
                        execute_google_cinematic_image_sync,
                        smart_query=clean_kw_q or critic_broll_q or overall_refine_q,
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
    refine_item_timeout = 88.0 if is_lf_refine else 86.0

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
                            p for p in media_dir.glob(f"scene_{sid_to:03d}_*_r{retry_count}.mp4")
                            if str(p) != prev_mp_to
                            and not any(tok in p.name.lower() for tok in ("_raw", "_cand", "_tmp", ".tmp", "_fftmp", "_stage"))
                            and is_completed_clean_mp4(p, min_size_bytes=20_000, min_duration_ms=1200)
                        ],
                        key=_safe_mtime,
                        reverse=True,
                    )
                    recovered_to = False
                    for rc_p in ref_cands_to[:2]:
                        ok_rc_to, _ = await asyncio.to_thread(_verify_and_sanitize_refined_media, str(rc_p), True, sid_to)
                        if ok_rc_to:
                            from app.graph.tools.youtube_search_ranker import _verify_short_clip_with_gemini_vision
                            ok_vis_to, _, _ = await asyncio.to_thread(
                                _verify_short_clip_with_gemini_vision,
                                video_path=str(rc_p),
                                topic=state.get("topic", ""),
                                narration=str(item.get("narration") or state.get("topic", "")),
                                subject_entity=primary_entity or state.get("topic", ""),
                                overall_script_query=primary_entity or state.get("topic", ""),
                                search_scope="SCENE_SPECIFIC",
                                video_title=rc_p.stem,
                                allow_3d_cgi_monster=False,
                                strict_no_fallback=True,
                                cancel_event=None,
                                deadline_ts=time.monotonic() + 10.0,
                                target_visual_query=str(item.get("visual_keyword") or primary_entity or ""),
                            )
                            if not ok_vis_to:
                                Path(rc_p).unlink(missing_ok=True)
                                continue
                            item["media_path"] = str(rc_p)
                            item["media_type"] = "hook_ready_video" if sid_to == 1 else "video"
                            item["pose_images"] = []
                            item["production_method"] = "REFINED_TIMEOUT_DISK_RECOVERED_VIDEO"
                            recovered_to = True
                            logger.info(
                                f"Scene {sid_to}: 🛡️ [RefineMedia Timeout Recovery] Đã khôi phục & hậu kiểm Vision file '{rc_p.name}' vừa tải xong trên đĩa!"
                            )
                            break
                    if not recovered_to and sid_to not in (1, 2):
                        ref_imgs_to = sorted(
                            [
                                p for p in media_dir.glob(f"scene_{sid_to:03d}_refined_gimg_rescue_r{retry_count}*.jpg")
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

    try:
        from app.graph.checkpoint_store import _save_node_checkpoint
        _save_node_checkpoint("fetch_media", state, {"scenes_media": scenes_media, "current_step": "MEDIA_READY"})
    except Exception:
        pass

    return {
        "scenes_media": scenes_media,
        "media_retry_count": retry_count,
        "rejected_media_urls": list(used_refine_urls)[-500:],
        "current_step": "MEDIA_REFINED",
        "progress_message": f"Đã tự động sửa lại song song {len(failed_ids)} cảnh media kỹ thuật chưa đạt",
    }
