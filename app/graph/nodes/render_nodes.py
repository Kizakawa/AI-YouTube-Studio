"""
app.graph.nodes.render_nodes — Node Render Video bằng VideoAgent & Kiểm định VideoQA.
"""
import logging
import os
from pathlib import Path
from typing import Dict, Any

from app.schemas.contracts import RenderJobContract, SceneContract
from app.agents.video_agent import VideoAgent
from app.graph.evaluators.video_qa import VideoQA
from app.graph.state import VideoWorkflowState

logger = logging.getLogger(__name__)


async def node_render_video(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node dựng video hoàn chỉnh bằng VideoAgent & FFmpeg."""
    work_dir = Path(state.get("work_dir", "storage/cache"))
    scenes_media = state.get("scenes_media", [])
    scenes_audio = state.get("scenes_audio", [])
    full_audio_path = state.get("full_audio_path", "")
    subtitle_path = state.get("subtitle_path", "")
    job_id = state.get("job_id", 0)
    target_channel_id = state.get("target_channel_id", "default")
    total_audio_duration_ms = state.get("total_audio_duration_ms", 0)

    # Defense-in-depth: Tự động khôi phục scenes_audio / full_voiceover.mp3 từ step_checkpoint.json hoặc node_generate_audio
    if not scenes_audio:
        try:
            ckpt_file = work_dir / "step_checkpoint.json"
            if ckpt_file.exists():
                import json as _json_ck
                ckpt_data = _json_ck.loads(ckpt_file.read_text(encoding="utf-8"))
                gen_aud_ck = (ckpt_data.get("steps") or {}).get("generate_audio") or ckpt_data.get("generate_audio") or {}
                scenes_audio = gen_aud_ck.get("scenes_audio") or scenes_audio
                full_audio_path = full_audio_path or gen_aud_ck.get("full_audio_path") or ""
                subtitle_path = subtitle_path or gen_aud_ck.get("subtitle_path") or ""
                total_audio_duration_ms = total_audio_duration_ms or gen_aud_ck.get("total_audio_duration_ms") or 0
        except Exception:
            pass
    if not scenes_audio or not full_audio_path or not Path(full_audio_path).exists() or Path(full_audio_path).stat().st_size < 1024:
        logger.warning(f"[RenderNode] Job #{job_id}: full_audio_path/scenes_audio bị thiếu -> Tự động khôi phục qua node_generate_audio!")
        from app.graph.nodes.audio_nodes import node_generate_audio
        re_audio = await node_generate_audio(state)
        scenes_audio = re_audio.get("scenes_audio") or scenes_audio
        full_audio_path = re_audio.get("full_audio_path") or full_audio_path
        subtitle_path = re_audio.get("subtitle_path") or subtitle_path
        total_audio_duration_ms = re_audio.get("total_audio_duration_ms") or total_audio_duration_ms

    # Ghép metadata media và audio cho từng scene (VideoAgent._prepare_scene_clips sẽ render song song 3D Ken Burns cho mọi ảnh tĩnh đúng 1 lần duy nhất)
    _motion_cycle = ["zoom_in", "pan_right", "zoom_out", "pan_left"]

    audio_map = {s.get("scene_id"): s for s in scenes_audio if isinstance(s, dict) and s.get("scene_id")}
    enriched_scenes = []
    video_format_pre = str(
        state.get("video_format")
        or (state.get("script_data") or {}).get("video_format")
        or ("long" if len(scenes_media) >= 14 else "short")
    ).strip().lower()
    is_lf_pre = video_format_pre == "long"
    target_w = 1920 if is_lf_pre else 1080
    target_h = 1080 if is_lf_pre else 1920
    render_retry_count = int(state.get("render_retry_count", 0) or 0)
    # Giữ lại các clip_*.mp4 còn nguyên vẹn & có mtime >= media_path để VideoAgent tái sử dụng tức thì khi QA retry
    if render_retry_count > 0:
        active_mtimes = {
            int(m.get("scene_id") or 0): Path(str(m.get("media_path"))).stat().st_mtime
            for m in scenes_media
            if isinstance(m, dict) and m.get("scene_id") and m.get("media_path") and Path(str(m.get("media_path"))).exists()
        }
        for _cand_clip in (work_dir / "clips").glob("clip_*.mp4"):
            m_cid = __import__("re").search(r"clip_(\d+)\.mp4$", _cand_clip.name)
            cid_int = int(m_cid.group(1)) if m_cid else -1
            src_mt = active_mtimes.get(cid_int)
            if src_mt is None or _cand_clip.stat().st_size <= 25_000 or _cand_clip.stat().st_mtime < src_mt:
                _cand_clip.unlink(missing_ok=True)

    for idx_m, m in enumerate(scenes_media):
        if not isinstance(m, dict):
            continue
        sc_id = m.get("scene_id")
        if not sc_id:
            continue
        a_info = audio_map.get(sc_id, {})
        dur_ms = a_info.get("audio_duration_ms") or a_info.get("duration_ms") or int(max(float(m.get("estimated_seconds") or 3.0), 1.0) * 1000)
        cam_motion = m.get("camera_motion") or ("hyper_zoom" if sc_id == 1 else _motion_cycle[idx_m % len(_motion_cycle)])
        cur_media_path = str(m.get("media_path") or "")
        cur_media_type = str(m.get("media_type") or "image")
        if not cur_media_path or not Path(cur_media_path).exists() or cur_media_path.lower().endswith("_graphic.png"):
            try:
                from app.utils.ffmpeg_helper import get_duration_ms, is_completed_clean_mp4, trim_video
                req_sc_dur = max(dur_ms / 1000.0, 2.5) + 0.25
                out_pre_resc = str(work_dir / "media" / f"scene_{int(sc_id):03d}_prerender_rescue.mp4")
                rescued_pre = False
                if is_lf_pre:
                    from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                    pre_pool_res = slice_any_available_chapter_master_for_scene(
                        media_dir=work_dir / "media",
                        scene_id=int(sc_id),
                        target_duration_sec=req_sc_dur,
                        job_id=job_id,
                        output_override_path=out_pre_resc,
                        max_master_uses_override=24,
                    )
                    if pre_pool_res and pre_pool_res.get("media_path") and Path(pre_pool_res["media_path"]).exists():
                        cur_media_path = pre_pool_res["media_path"]
                        cur_media_type = "hook_ready_video" if sc_id == 1 else "video"
                        rescued_pre = True
                if not rescued_pre:
                    m_dir_pre = work_dir / "media"
                    ch_dir_pre = m_dir_pre / "chapter_pool"
                    # 1. Ưu tiên khôi phục file media của CHÍNH phân cảnh sc_id trên đĩa (nếu có)
                    same_scene_vids = sorted(
                        [
                            p for p in (list(m_dir_pre.glob(f"scene_{int(sc_id):03d}_*.mp4")) if m_dir_pre.exists() else [])
                            if is_completed_clean_mp4(p, min_size_bytes=20_000, min_duration_ms=1200)
                        ],
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    if same_scene_vids:
                        cur_media_path = str(same_scene_vids[0])
                        cur_media_type = "hook_ready_video" if sc_id == 1 else "video"
                        rescued_pre = True
                    elif int(sc_id) not in (1, 2):
                        same_scene_imgs = sorted(
                            [
                                p for p in (list(m_dir_pre.glob(f"scene_{int(sc_id):03d}_*.jpg")) if m_dir_pre.exists() else [])
                                if p.exists() and p.stat().st_size > 3_000 and "_cand" not in p.name
                            ],
                            key=lambda p: (0 if "_cut" not in p.name else 1, -p.stat().st_mtime),
                        )
                        if same_scene_imgs:
                            rec_sc_poses = [str(p) for p in same_scene_imgs[:2]]
                            cur_media_path = rec_sc_poses[0]
                            cur_media_type = "image"
                            if sc_id in media_map:
                                media_map[sc_id]["pose_images"] = rec_sc_poses
                            rescued_pre = True
                            logger.info(
                                f"🛡️ [PreRenderRescue] Đã khôi phục ảnh thật '{same_scene_imgs[0].name}' trên đĩa cho Cảnh #{sc_id} (thay thế _graphic.png)!"
                            )
                    if not rescued_pre:
                        # 2. Chỉ cắt lát không chồng lấn từ các file Master dài (*_master.mp4 >= 14s),
                        # TUYỆT ĐỐI KHÔNG copy các clip ngắn (scene_YYY_*.mp4) của phân cảnh khác gây lặp cảnh!
                        from app.services.media.longform_chapter_pool import (
                            _commit_master_used_range,
                            _find_non_overlapping_master_offset,
                            is_clip_visually_unique_in_job,
                        )
                        cand_masters = sorted(
                            [
                                p for p in (list(ch_dir_pre.glob("*_master.mp4")) if ch_dir_pre.exists() else [])
                                if is_completed_clean_mp4(p, min_size_bytes=50_000, min_duration_ms=14000)
                            ],
                            key=lambda p: p.stat().st_size,
                            reverse=True,
                        )
                        for d_pick in cand_masters:
                            d_tot_s = get_duration_ms(str(d_pick)) / 1000.0
                            d_span = max(0.0, d_tot_s - req_sc_dur - 0.5)
                            pref_off = round((((int(sc_id) * 43 + 17) % 89) / 89.0) * d_span, 2) if d_span > 0.5 else 0.0
                            d_off = _find_non_overlapping_master_offset(
                                job_id=job_id,
                                master_key=str(d_pick),
                                scene_id=int(sc_id),
                                preferred_offset=pref_off,
                                req_dur=req_sc_dur,
                                master_dur=d_tot_s,
                            )
                            if d_off is None:
                                continue
                            trim_video(
                                input_path=str(d_pick),
                                duration_seconds=req_sc_dur,
                                output_path=out_pre_resc,
                                width=target_w,
                                height=target_h,
                                fps=30,
                                start_offset_sec=d_off,
                                apply_grading=True,
                                is_hook_scene=(sc_id == 1),
                                fair_use_transform=True,
                                preserve_continuous_flow=True,
                            )
                            if Path(out_pre_resc).exists() and Path(out_pre_resc).stat().st_size > 15_000:
                                if is_clip_visually_unique_in_job(out_pre_resc, job_id, scene_id=int(sc_id), master_key=str(d_pick)):
                                    _commit_master_used_range(job_id, str(d_pick), int(sc_id), d_off, min(d_tot_s, d_off + req_sc_dur))
                                    cur_media_path = out_pre_resc
                                    cur_media_type = "hook_ready_video" if sc_id == 1 else "video"
                                    rescued_pre = True
                                    break
                                Path(out_pre_resc).unlink(missing_ok=True)
                if rescued_pre:
                    m["media_path"] = cur_media_path
                    m["media_type"] = cur_media_type
                    m["pose_images"] = []
                    logger.info(f"[RenderNode] 🛡️ Đã thay thế _graphic.png/missing ở Cảnh #{sc_id} trước khi render -> {Path(cur_media_path).name}")
            except Exception as _e_pre_g:
                logger.debug(f"[RenderNode] Pre-render graphic rescue skip for Scene #{sc_id}: {_e_pre_g}")
        if sc_id in (1, 2):
            if sc_id == 1 and is_lf_pre and cur_media_path and Path(cur_media_path).exists() and (
                not cur_media_path.lower().endswith((".mp4", ".mov", ".webm"))
                or "hook_motion_" in Path(cur_media_path).name.lower()
            ):
                try:
                    from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                    lf_s1_out = str(work_dir / "media" / "scene_001_prerender_sharp_16x9.mp4")
                    lf_s1_res = slice_any_available_chapter_master_for_scene(
                        media_dir=work_dir / "media",
                        scene_id=1,
                        target_duration_sec=max(dur_ms / 1000.0, 2.5) + 0.2,
                        job_id=job_id,
                        output_override_path=lf_s1_out,
                    )
                    if lf_s1_res and lf_s1_res.get("media_path") and Path(lf_s1_res["media_path"]).exists():
                        cur_media_path = lf_s1_res["media_path"]
                        cur_media_type = "hook_ready_video"
                        m["media_path"] = cur_media_path
                        m["media_type"] = cur_media_type
                        m["pose_images"] = []
                        logger.info(f"[RenderNode 16:9] Đã nâng cấp Cảnh 1 sang Video 16:9 sắc nét: {Path(cur_media_path).name}")
                except Exception as _e_lf_s1:
                    logger.debug(f"[RenderNode 16:9] Pre-render Scene 1 pool upgrade skip: {_e_lf_s1}")
            # CỔNG BẢO VỆ 5-7S ĐẦU TIÊN (Scene 1 & Scene 2): Bắt buộc 100% là Video chuyển động thật, TUYỆT ĐỐI CẤM dùng ảnh tĩnh hay render_opening_hook_multi_cut!
            if not is_lf_pre:
                try:
                    from app.utils.ffmpeg_helper import verify_hook_clip_genuine_motion, trim_video, is_completed_clean_mp4
                    from app.graph.tools.tool_schemas import build_single_entity_stock_queries
                    from app.services.media.stock_fetcher import StockFetcher
                    from app.agents.media_agent import MediaAgent
                    is_cur_genuine_vid = (
                        bool(cur_media_path)
                        and Path(cur_media_path).exists()
                        and cur_media_path.lower().endswith((".mp4", ".mov", ".webm"))
                        and verify_hook_clip_genuine_motion(cur_media_path)[0]
                    )
                    if not is_cur_genuine_vid:
                        target_dur_open = max(dur_ms / 1000.0, 2.2) + 0.18
                        disk_open_cands: List[Path] = []
                        if sc_id == 1:
                            for p_hk_d in (
                                work_dir / "hook_motion_001_fullpeak.mp4",
                                work_dir / "hook_motion_001.mp4",
                                *sorted((work_dir / "hook_candidates").glob("*.mp4")),
                            ):
                                if p_hk_d.exists() and is_completed_clean_mp4(p_hk_d, min_size_bytes=18_000, min_duration_ms=1200):
                                    disk_open_cands.append(p_hk_d)
                        for p_sc_d in sorted((work_dir / "media").glob(f"scene_{sc_id:03d}_*.mp4")):
                            if p_sc_d.exists() and is_completed_clean_mp4(p_sc_d, min_size_bytes=18_000, min_duration_ms=1200):
                                disk_open_cands.append(p_sc_d)
                        recovered_open = False
                        for cand_op in disk_open_cands:
                            if verify_hook_clip_genuine_motion(str(cand_op))[0]:
                                out_op_trim = str(work_dir / ("hook_motion_001.mp4" if sc_id == 1 else f"media/scene_{sc_id:03d}_prerender_pure_vid.mp4"))
                                if str(cand_op.resolve()) != str(Path(out_op_trim).resolve()):
                                    trim_video(
                                        input_path=str(cand_op),
                                        duration_seconds=target_dur_open,
                                        output_path=out_op_trim,
                                        width=target_w,
                                        height=target_h,
                                        fps=30,
                                        is_scifi=bool(state.get("is_scifi")),
                                        niche=str(state.get("channel_style") or state.get("niche") or "general"),
                                        apply_grading=True,
                                        is_hook_scene=(sc_id == 1),
                                        fair_use_transform=True,
                                        start_offset_sec=0.0,
                                        preserve_continuous_flow=True,
                                    )
                                final_op_p = out_op_trim if (Path(out_op_trim).exists() and Path(out_op_trim).stat().st_size > 12_000) else str(cand_op)
                                cur_media_path = final_op_p
                                cur_media_type = "hook_ready_video" if sc_id == 1 else "video"
                                m["media_path"] = cur_media_path
                                m["media_type"] = cur_media_type
                                m["pose_images"] = []
                                m["production_method"] = "PRERENDER_RECOVERED_GENUINE_MOTION_VIDEO"
                                recovered_open = True
                                logger.info(f"[RenderNode] 🎬 Đã khôi phục Video chuyển động thật '{cand_op.name}' cho Cảnh #{sc_id} (cấm ảnh tĩnh 5-7s đầu)!")
                                break
                        if not recovered_open:
                            sf_pre = StockFetcher()
                            ma_pre = MediaAgent()
                            open_2w_qs = build_single_entity_stock_queries(
                                primary_query=str(m.get("visual_keyword") or state.get("primary_entity") or state.get("topic") or ""),
                                secondary_query=str(state.get("primary_entity") or ""),
                                subject_entity=str(m.get("subject_entity") or state.get("primary_entity") or ""),
                                topic=str(state.get("topic") or ""),
                                narration=str(m.get("narration") or ""),
                                max_words=2,
                            )
                            for q_op_2w in open_2w_qs[:4]:
                                if recovered_open or not q_op_2w:
                                    break
                                v_op_hits = (
                                    sf_pre.search_videos(q_op_2w, per_page=6, min_duration=2, orientation="portrait")
                                    or sf_pre.search_pixabay_videos(q_op_2w, per_page=6, min_duration=2, orientation="portrait")
                                    or []
                                )
                                for vh_op in v_op_hits[:3]:
                                    vh_op_u = str(vh_op.get("url") or "").strip()
                                    if not vh_op_u:
                                        continue
                                    raw_op_dl = str(work_dir / "media" / f"scene_{sc_id:03d}_prerender_raw.mp4")
                                    out_op_dl = str(work_dir / ("hook_motion_001.mp4" if sc_id == 1 else f"media/scene_{sc_id:03d}_prerender_pure_vid.mp4"))
                                    ma_pre.download_file(vh_op_u, raw_op_dl)
                                    if Path(raw_op_dl).exists() and Path(raw_op_dl).stat().st_size > 15_000:
                                        trim_video(
                                            input_path=raw_op_dl,
                                            duration_seconds=target_dur_open,
                                            output_path=out_op_dl,
                                            width=target_w,
                                            height=target_h,
                                            fps=30,
                                            is_scifi=bool(state.get("is_scifi")),
                                            niche=str(state.get("channel_style") or state.get("niche") or "general"),
                                            apply_grading=True,
                                            is_hook_scene=(sc_id == 1),
                                            fair_use_transform=True,
                                            start_offset_sec=0.0,
                                            preserve_continuous_flow=True,
                                        )
                                        Path(raw_op_dl).unlink(missing_ok=True)
                                        if Path(out_op_dl).exists() and verify_hook_clip_genuine_motion(out_op_dl)[0]:
                                            cur_media_path = out_op_dl
                                            cur_media_type = "hook_ready_video" if sc_id == 1 else "video"
                                            m["media_path"] = cur_media_path
                                            m["media_type"] = cur_media_type
                                            m["pose_images"] = []
                                            m["production_method"] = "PRERENDER_PURE_STOCK_OPENING_VIDEO"
                                            recovered_open = True
                                            logger.info(f"[RenderNode] 🎬 Đã tải Video chuyển động thật cho Cảnh #{sc_id} ('{q_op_2w}') trước khi render!")
                                            break
                                    else:
                                        Path(raw_op_dl).unlink(missing_ok=True)
                except Exception as e_s1_conv:
                    logger.debug(f"[RenderNode] Pre-render pure video opening guard skip: {e_s1_conv}")
            if (
                sc_id == 1
                and cur_media_path
                and Path(cur_media_path).exists()
                and cur_media_path.lower().endswith((".mp4", ".mov", ".webm"))
                and not cur_media_path.lower().endswith(("_doodle.mp4", "_doodle_2d.mp4"))
            ):
                try:
                    from app.graph.nodes.hook_video_node import _ensure_hook_clip_sharpness_and_luma
                    ok_pre_sh, pre_sh_val = _ensure_hook_clip_sharpness_and_luma(
                        cur_media_path,
                        min_sharpness=12.8 if is_lf_pre else (12.4 if render_retry_count > 0 else 12.0),
                    )
                    if not ok_pre_sh and is_lf_pre:
                        from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                        lf_s1_sharp = str(work_dir / "media" / "scene_001_prerender_sharp_rescue_16x9.mp4")
                        lf_s1_r2 = slice_any_available_chapter_master_for_scene(
                            media_dir=work_dir / "media",
                            scene_id=1,
                            target_duration_sec=max(dur_ms / 1000.0, 2.5) + 0.2,
                            job_id=job_id,
                            output_override_path=lf_s1_sharp,
                        )
                        if lf_s1_r2 and lf_s1_r2.get("media_path") and Path(lf_s1_r2["media_path"]).exists():
                            cur_media_path = lf_s1_r2["media_path"]
                            cur_media_type = "hook_ready_video"
                            m["media_path"] = cur_media_path
                            m["media_type"] = cur_media_type
                            m["pose_images"] = []
                            logger.info(
                                f"[RenderNode 16:9] Cảnh 1 độ nét thấp ({pre_sh_val:.2f} < 12.8) -> Đã thay bằng Video 16:9 sắc nét: {Path(cur_media_path).name}"
                            )
                except Exception as e_s1_pre:
                    logger.debug(f"[RenderNode] Pre-render hook sharpness check skip: {e_s1_pre}")

        sc_dict = {
            "scene_id": sc_id,
            "narration": m.get("narration") or a_info.get("narration") or "",
            "visual_keyword": m.get("visual_keyword", ""),
            "visual_prompt": m.get("visual_prompt"),   # ← Cần cho AI image context
            "estimated_seconds": max(float(m.get("estimated_seconds") or 3.0), 1.0),
            "audio_duration_ms": dur_ms,
            "audio_path": a_info.get("audio_path") or full_audio_path or "storage/temp/voiceover.mp3",
            "word_timings": a_info.get("word_timings") or m.get("word_timings"),
            "media_path": cur_media_path,
            "media_type": cur_media_type,
            "pose_images": m.get("pose_images"),
            # Cinematic enhancement fields — cần cho Ken Burns, SFX & Phụ đề ngữ cảnh
            "camera_motion": cam_motion,               # ← Thiếu field này → motion mặc định
            "sfx_cue": m.get("sfx_cue") or a_info.get("sfx_cue"),
            "highlight_words": m.get("highlight_words") or a_info.get("highlight_words"),
            "overlay_badge": m.get("overlay_badge") or a_info.get("overlay_badge"),
            "visual_strategy": m.get("visual_strategy"),
            "production_method": m.get("production_method"),
            "tool": m.get("tool"),
            "strategy_rationale": m.get("strategy_rationale"),
        }
        enriched_scenes.append(SceneContract(**sc_dict))

    final_output = str(work_dir / f"final_shorts_{job_id}.mp4")

    title = state.get("title") or (state.get("script_data") or {}).get("title") or f"Shorts #{job_id}"
    resolved_total_dur = int(total_audio_duration_ms or sum(s.audio_duration_ms for s in enriched_scenes))

    # Tự động chọn BGM theo Niche và SFX Transitions
    from config.settings import settings
    from app.services.pipeline import VideoPipeline
    niche = state.get("channel_style") or state.get("niche") or "general"
    topic_lower = (state.get("topic") or title or "").lower()
    is_scifi = bool(state.get("is_scifi")) or (niche == "scifi") or any(k in topic_lower for k in ("scifi", "sci-fi", "alien", "cyborg", "viễn tưởng", "quantum"))
    bgm_path = VideoPipeline.find_bgm_for_niche(niche, is_scifi=is_scifi, scifi_subgenre=state.get("scifi_subgenre", "general_scifi"))
    sfx_dir = str(Path(settings.BASE_DIR) / "assets" / "sfx")

    if not subtitle_path:
        subtitle_path = str(work_dir / "subtitles.ass")
    video_format = str(
        state.get("video_format")
        or (state.get("script_data") or {}).get("video_format")
        or ("long" if len(enriched_scenes) >= 14 else "short")
    ).strip().lower()
    is_long_form = video_format == "long"

    try:
        sub_ok = Path(subtitle_path).exists() and ("Dialogue: 0," in Path(subtitle_path).read_text(encoding="utf-8", errors="ignore"))
        if not sub_ok and enriched_scenes:
            from app.utils.subtitle_helper import SubtitleHelper
            hook_banner_text = (state.get("script_data") or {}).get("thumbnail_text") or title
            SubtitleHelper.save_ass_file(
                enriched_scenes,
                subtitle_path,
                niche_id=niche,
                hook_title=hook_banner_text,
                show_cta_badge=False,
                is_long_form=is_long_form,
            )
    except Exception as e_sub:
        logger.debug(f"[RenderNode] Auto-repair subtitles.ass warning: {e_sub}")

    import shutil
    from config.settings import settings
    outputs_dir = Path(settings.OUTPUT_DIR)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    # Tự động chèn YouTube Chapters (0:00 Intro...) vào script_data.description cho cả Long-Form 16:9 và Shorts nhiều vòng/Gauntlet
    # và tự động render Thumbnail 2–3 từ Two-Tone chuẩn @FossilVideos / @IFinity_What từ clip gốc sạch TRƯỚC khi gọi VideoAgent.render
    # để VideoAgent có thể nhúng Single-Pass In-Video Autogen Thumbnail (0.0s, 25%, 50%, 75% I-Frames) vào luồng video!
    updated_script_data = dict(state.get("script_data") or {})
    if enriched_scenes and len(enriched_scenes) >= 3:
        try:
            from app.agents.publisher.seo_tags_mixin import build_youtube_chapters_from_scenes
            chap_str = build_youtube_chapters_from_scenes(
                scenes=enriched_scenes,
                language=str(state.get("language") or "en"),
            )
            if chap_str:
                cur_desc = str(updated_script_data.get("description") or "").strip()
                if "0:00" not in cur_desc:
                    updated_script_data["description"] = f"{cur_desc}\n\n{chap_str}".strip() if cur_desc else chap_str
                updated_script_data["youtube_chapters"] = chap_str
                logger.info(f"⏱️ [RenderNode] Đã tự động tạo YouTube Chapters cho Job #{job_id}:\n{chap_str}")
        except Exception as e_ch_rn:
            logger.debug(f"[RenderNode] YouTube Chapters generation skip: {e_ch_rn}")

    th_res = {}
    best_th = ""
    try:
        from app.services.thumbnail_service import ThumbnailService
        from PIL import Image, ImageFilter, ImageStat
        import subprocess
        import tempfile
        clean_thumb_source = str(getattr(enriched_scenes[0], "media_path", "") or "") if enriched_scenes else ""
        best_src_score = -1.0
        with tempfile.TemporaryDirectory() as td_th:
            for idx_th, sc_th in enumerate(enriched_scenes[:4]):
                mp_th = str(
                    (sc_th.get("media_path") if isinstance(sc_th, dict) else getattr(sc_th, "media_path", ""))
                    or ""
                )
                if not (mp_th and Path(mp_th).exists() and mp_th.lower().endswith((".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png"))):
                    continue
                try:
                    f_probe = str(Path(td_th) / f"th_p_{idx_th}.jpg")
                    if mp_th.lower().endswith((".mp4", ".mov", ".webm")):
                        subprocess.run(
                            ["ffmpeg", "-v", "error", "-y", "-ss", "0.8", "-i", mp_th, "-frames:v", "1", f_probe],
                            capture_output=True, timeout=6, check=False,
                        )
                    else:
                        f_probe = mp_th
                    if Path(f_probe).exists() and Path(f_probe).stat().st_size > 1000:
                        if mp_th.lower().endswith(("_doodle.mp4", "_doodle_2d.mp4")):
                            continue
                        with Image.open(f_probe) as im_p:
                            g_p = im_p.convert("L").resize((180, 320))
                            luma_mean = float(ImageStat.Stat(g_p).mean[0])
                            e_p = g_p.filter(ImageFilter.FIND_EDGES)
                            mid_e = float(ImageStat.Stat(e_p.crop((24, 56, 156, 264))).mean[0])
                            center_std = float(ImageStat.Stat(g_p.crop((24, 56, 156, 264))).stddev[0])
                            natural_subject_bonus = 1.40 if (32.0 <= luma_mean <= 195.0 and 8.0 <= mid_e <= 44.0) else 0.45
                            early_scene_bonus = 1.65 if idx_th == 0 else (1.35 if idx_th == 1 else 1.0)
                            sc_val = (mid_e * 0.6 + center_std * 0.8) * natural_subject_bonus * early_scene_bonus
                            if sc_val > best_src_score:
                                best_src_score = sc_val
                                clean_thumb_source = mp_th
                except Exception:
                    if not clean_thumb_source:
                        clean_thumb_source = mp_th
        th_srv = ThumbnailService(output_dir=str(outputs_dir))
        th_res = th_srv.generate_viral_thumbnail(
            job_id=job_id,
            topic=str(state.get("topic") or title),
            viral_title=title,
            video_path=clean_thumb_source,
            niche_id=str(niche),
            custom_text=str(updated_script_data.get("thumbnail_text") or ""),
        )
        best_th = (th_res.get("16x9") if is_long_form else (th_res.get("9x16") or th_res.get("16x9"))) or ""
    except Exception as e_th_pre:
        logger.debug(f"[RenderNode] Pre-render thumbnail generation warning: {e_th_pre}")

    render_job = RenderJobContract(
        job_id=job_id,
        title=title,
        scenes=enriched_scenes,
        full_audio_path=full_audio_path or (enriched_scenes[0].audio_path if enriched_scenes else ""),
        total_audio_duration_ms=resolved_total_dur,
        subtitle_path=subtitle_path,
        bgm_path=bgm_path,
        niche=niche,
        is_scifi=is_scifi,
        scifi_subgenre=state.get("scifi_subgenre", "general_scifi"),
        sfx_dir=sfx_dir,
        enable_sfx=True,
        output_path=final_output,
        video_format="long" if is_long_form else "short",
        thumbnail_path=str(th_res.get("16x9") or ""),
        thumbnail_vertical_path=str(th_res.get("9x16") or ""),
    )

    agent = VideoAgent()
    await agent.render(render_job)

    # Nếu đang ở vòng thử lại render (render_retry_count > 0), đảm bảo khung hình mở đầu của file đã render đạt độ nét >= 12.5
    if Path(final_output).exists() and render_retry_count > 0:
        try:
            from app.graph.nodes.hook_video_node import _ensure_hook_clip_sharpness_and_luma
            _ensure_hook_clip_sharpness_and_luma(final_output, min_sharpness=12.6)
        except Exception as _e_post_sh:
            logger.debug(f"[RenderNode] Post-render sharpness boost skip: {_e_post_sh}")

    # Đảm bảo video thành phẩm được lưu vĩnh viễn vào storage/outputs/video_{job_id}.mp4
    # (Tránh trường hợp bị xóa mất khi dọn dẹp thư mục tạm work_dir)
    permanent_output = str(outputs_dir / f"video_{job_id}.mp4")

    if Path(final_output).exists():
        if str(Path(final_output).resolve()) != str(Path(permanent_output).resolve()):
            shutil.copy2(final_output, permanent_output)
            logger.info(f"[RenderNode] Đã bảo lưu video thành phẩm an toàn vào: {permanent_output}")
        final_output = permanent_output

    thumbnail_path_out = ""
    if Path(final_output).exists():
        try:
            if not (best_th and Path(best_th).exists()):
                from app.services.thumbnail_service import ThumbnailService
                th_srv = ThumbnailService(output_dir=str(outputs_dir))
                th_res = th_srv.generate_viral_thumbnail(
                    job_id=job_id,
                    topic=str(state.get("topic") or title),
                    viral_title=title,
                    video_path=final_output,
                    niche_id=str(niche),
                    custom_text=str(updated_script_data.get("thumbnail_text") or ""),
                )
                best_th = (th_res.get("16x9") if is_long_form else (th_res.get("9x16") or th_res.get("16x9"))) or ""
            if best_th and Path(best_th).exists():
                sidecar_thumb = Path(final_output).with_suffix(".thumb.jpg")
                shutil.copy2(best_th, str(sidecar_thumb))
                thumbnail_path_out = str(sidecar_thumb)
        except Exception as e_th_rn:
            logger.debug(f"[RenderNode] Auto thumbnail sidecar warning: {e_th_rn}")

    return {
        "rendered_video_path": final_output,
        "thumbnail_path": thumbnail_path_out,
        "script_data": updated_script_data,
        "video_format": "long" if is_long_form else "short",
        "current_step": "RENDERED",
        "progress_percent": 90,
        "progress_message": f"Đã render hoàn tất video ({'1920x1080 16:9 + Chapters' if is_long_form else '1080x1920 9:16'}, Ken Burns, Subtitles, Thumbnail)",
    }


def node_evaluate_video(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node kiểm định chất lượng kỹ thuật VideoQA bằng FFprobe & QualityEvaluator (Tự động render lại nếu lỗi, Hard-Block nếu vẫn không đạt)."""
    rendered_video_path = state.get("rendered_video_path", "")
    total_audio_duration_ms = state.get("total_audio_duration_ms", 0)
    render_retry_count = int(state.get("render_retry_count", 0) or 0)
    max_render_retries = max(0, int(os.getenv("MAX_RENDER_RETRIES", "1")))
    video_format = str(
        state.get("video_format")
        or (state.get("script_data") or {}).get("video_format")
        or "short"
    ).strip().lower()

    qa_report = VideoQA.inspect_video(
        video_path=rendered_video_path,
        expected_duration_ms=total_audio_duration_ms
    )

    from app.services.quality_evaluator import QualityEvaluator
    eval_res = QualityEvaluator().evaluate_video(
        video_path=rendered_video_path,
        expected_duration_ms=total_audio_duration_ms,
        video_format=video_format,
    )
    video_score = eval_res.get("score", 0)
    combined_passed = bool(qa_report.passed and eval_res.get("passed", True))
    combined_issues = list(dict.fromkeys(list(qa_report.issues) + list(eval_res.get("issues", []))))

    # Hard-block bất kỳ video nào còn sót thẻ chữ xanh dương placeholder (_graphic.png)
    graphic_scenes = [
        int(sc_m.get("scene_id") or 0)
        for sc_m in (state.get("scenes_media") or [])
        if isinstance(sc_m, dict) and str(sc_m.get("media_path") or "").lower().endswith("_graphic.png")
    ]
    if graphic_scenes:
        video_score = max(0, video_score - 30)
        combined_passed = False
        combined_issues.append(f"Còn sót thẻ chữ placeholder (_graphic.png) ở cảnh {graphic_scenes}")

    # Hard-block bất kỳ video Shorts nào có 5-7s đầu tiên là ảnh tĩnh hoặc bị khựng khung hình (freeze_max_sec > 0)
    if video_format != "long" and rendered_video_path and Path(rendered_video_path).exists():
        try:
            from app.utils.ffmpeg_helper import verify_hook_clip_genuine_motion
            ok_final_hook_mot, _, final_hook_rsn = verify_hook_clip_genuine_motion(rendered_video_path)
            if not ok_final_hook_mot:
                video_score = max(0, video_score - 35)
                combined_passed = False
                combined_issues.append(f"Mở đầu 5-7s đầu tiên không phải video chuyển động thật ({final_hook_rsn})")
        except Exception as _e_qa_mot:
            logger.debug(f"[VideoQA] Opening motion check warning: {_e_qa_mot}")

    logger.info(
        f"🎬 [VideoQA] File: {Path(qa_report.video_path).name} | "
        f"Size: {qa_report.file_size_bytes/1024:.0f}KB | "
        f"Resolution: {qa_report.width}x{qa_report.height} | "
        f"Duration: {qa_report.duration_seconds}s | "
        f"Score: {video_score}/100 | Passed: {combined_passed}"
    )

    qa_dict = qa_report.model_dump()
    qa_dict["score"] = video_score
    qa_dict["passed"] = combined_passed
    qa_dict["issues"] = combined_issues
    qa_dict["metrics"] = eval_res

    if not combined_passed:
        next_retry = render_retry_count + 1
        if next_retry <= max_render_retries:
            logger.warning(
                f"🔄 [VideoQA] Video chưa đạt chuẩn ở lần dựng #{next_retry} (Score={video_score}/100: {'; '.join(combined_issues)}). "
                f"Kích hoạt dựng lại tự động ({next_retry}/{max_render_retries})..."
            )
            return {
                "video_eval": qa_dict,
                "render_retry_count": next_retry,
                "current_step": "QA_RETRY_RENDER",
                "progress_percent": 88,
                "progress_message": f"VideoQA phát hiện lỗi ({video_score}/100), đang tự động render lại (Lần {next_retry}/{max_render_retries})...",
            }

        raise RuntimeError(
            f"🚫 [HardBlock VideoQA] Video không đạt chuẩn kỹ thuật/chất lượng sau {next_retry} lần render "
            f"(Score={video_score}/100): {'; '.join(combined_issues) or 'Lỗi kiểm định video'}"
        )

    # Đọn dẹp ngay các file render trung gian nặng trong work_dir sau khi VideoQA đã đạt chuẩn (tránh phình đĩa hàng GB)
    try:
        work_dir_qa = Path(state.get("work_dir", "storage/cache"))
        if work_dir_qa.exists() and "temp" in str(work_dir_qa).replace("\\", "/"):
            for _inter_sub in ("clips", "hook_candidates", "render"):
                _sub_p = work_dir_qa / _inter_sub
                if _sub_p.exists():
                    shutil.rmtree(_sub_p, ignore_errors=True)
            for _dup_mp4 in (
                *work_dir_qa.glob("final_shorts_*.mp4"),
                *work_dir_qa.glob("silent_*.mp4"),
                *work_dir_qa.glob("muxed_*.mp4"),
                *(work_dir_qa / "media").glob("*_raw.mp4"),
                *(work_dir_qa / "media").glob("chapter_*_master_*.mp4"),
            ):
                _dup_mp4.unlink(missing_ok=True)
            # Tự động dọn các thư mục job_* cũ > 24h trong storage/temp
            import time as _t_qa
            _now_ts = _t_qa.time()
            _temp_root = work_dir_qa.parent
            if _temp_root.name == "temp" and _temp_root.exists():
                for _old_job_dir in _temp_root.iterdir():
                    if _old_job_dir.is_dir() and _old_job_dir != work_dir_qa:
                        try:
                            if (_now_ts - _old_job_dir.stat().st_mtime) > 86400:
                                shutil.rmtree(_old_job_dir, ignore_errors=True)
                        except Exception:
                            pass
    except Exception as _e_qa_clean:
        logger.debug(f"[VideoQA] Post-QA temp cleanup skip: {_e_qa_clean}")

    return {
        "video_eval": qa_dict,
        "current_step": "QA_DONE",
        "progress_percent": 100,
        "progress_message": f"Kiểm định VideoQA: Đạt chuẩn ({video_score}/100, {qa_report.width}x{qa_report.height} {qa_report.fps}fps)",
        "status": "COMPLETED",
    }
