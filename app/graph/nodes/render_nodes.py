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

    # Defense-in-depth: Tự động khôi phục full_voiceover.mp3 nếu bị tiến trình dọn rác xóa nhầm trước khi mux
    if not full_audio_path or not Path(full_audio_path).exists() or Path(full_audio_path).stat().st_size < 1024:
        logger.warning(f"[RenderNode] Job #{job_id}: full_audio_path ('{full_audio_path}') bị thiếu trên đĩa -> Tự động khôi phục qua node_generate_audio!")
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
        if sc_id == 1 and cur_media_path and Path(cur_media_path).exists():
            if not cur_media_path.lower().endswith((".mp4", ".mov", ".webm")):
                try:
                    from app.utils.ffmpeg_helper import render_opening_hook_multi_cut
                    pose_imgs = [p for p in (m.get("pose_images") or [cur_media_path]) if p and Path(str(p)).exists()]
                    if pose_imgs:
                        pre_hook_mp4 = str(work_dir / "hook_motion_001.mp4")
                        render_opening_hook_multi_cut(
                            image_paths=pose_imgs,
                            duration_seconds=max(dur_ms / 1000.0, 2.2) + 0.18,
                            output_path=pre_hook_mp4,
                            width=target_w,
                            height=target_h,
                            fps=30,
                            min_cuts=3,
                            is_scifi=bool(state.get("is_scifi")),
                            niche=str(state.get("channel_style") or state.get("niche") or "general"),
                            is_hook_scene=True,
                        )
                        if Path(pre_hook_mp4).exists() and Path(pre_hook_mp4).stat().st_size > 12_000:
                            cur_media_path = pre_hook_mp4
                            cur_media_type = "hook_ready_video"
                            m["media_path"] = pre_hook_mp4
                            m["media_type"] = "hook_ready_video"
                except Exception as e_s1_conv:
                    logger.debug(f"[RenderNode] Pre-render hook image-to-multicut skip: {e_s1_conv}")
            if (
                cur_media_path.lower().endswith((".mp4", ".mov", ".webm"))
                and not cur_media_path.lower().endswith(("_doodle.mp4", "_doodle_2d.mp4"))
            ):
                try:
                    from app.graph.nodes.hook_video_node import _ensure_hook_clip_sharpness_and_luma
                    _ensure_hook_clip_sharpness_and_luma(cur_media_path, min_sharpness=12.4 if render_retry_count > 0 else 12.0)
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
    import shutil
    from config.settings import settings
    outputs_dir = Path(settings.OUTPUT_DIR)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    permanent_output = str(outputs_dir / f"video_{job_id}.mp4")

    if Path(final_output).exists():
        if str(Path(final_output).resolve()) != str(Path(permanent_output).resolve()):
            shutil.copy2(final_output, permanent_output)
            logger.info(f"[RenderNode] Đã bảo lưu video thành phẩm an toàn vào: {permanent_output}")
        final_output = permanent_output

    # Tự động chèn YouTube Chapters (0:00 Intro...) vào script_data.description cho cả Long-Form 16:9 và Shorts nhiều vòng/Gauntlet
    # và tự động render Thumbnail 2–3 từ Two-Tone chuẩn @FossilVideos / @IFinity_What từ clip gốc sạch (trước khi burn phụ đề .ass)
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

    thumbnail_path_out = ""
    if Path(final_output).exists():
        try:
            from app.services.thumbnail_service import ThumbnailService
            from PIL import Image, ImageFilter, ImageStat
            import subprocess
            import tempfile
            clean_thumb_source = final_output
            best_src_score = -1.0
            with tempfile.TemporaryDirectory() as td_th:
                for idx_th, sc_th in enumerate(enriched_scenes):
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
                                top_e = float(ImageStat.Stat(e_p.crop((0, 0, 180, 56))).mean[0])
                                bot_e = float(ImageStat.Stat(e_p.crop((0, 264, 180, 320))).mean[0])
                                mid_e = float(ImageStat.Stat(e_p.crop((0, 70, 180, 250))).mean[0])
                                # Ưu tiên clip tràn viền thật (top_e & bot_e sắc nét), phạt các clip hạt/lưới sợi trừu tượng nền đen (mid_e > 44 hoặc luma < 28)
                                is_full_bleed = 1.0 if (top_e >= 6.5 and bot_e >= 6.5) else 0.35
                                natural_subject_bonus = 1.35 if (32.0 <= luma_mean <= 185.0 and 12.0 <= mid_e <= 42.0) else 0.35
                                early_scene_bonus = 1.25 if idx_th < 6 else 1.0
                                sc_val = mid_e * is_full_bleed * natural_subject_bonus * early_scene_bonus
                                if sc_val > best_src_score:
                                    best_src_score = sc_val
                                    clean_thumb_source = mp_th
                    except Exception:
                        if clean_thumb_source == final_output:
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
            best_th = th_res.get("16x9") if is_long_form else (th_res.get("9x16") or th_res.get("16x9"))
            if best_th and Path(best_th).exists():
                sidecar_thumb = Path(final_output).with_suffix(".thumb.jpg")
                shutil.copy2(best_th, str(sidecar_thumb))
                thumbnail_path_out = str(sidecar_thumb)
        except Exception as e_th_rn:
            logger.debug(f"[RenderNode] Auto thumbnail render warning: {e_th_rn}")

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

    return {
        "video_eval": qa_dict,
        "current_step": "QA_DONE",
        "progress_percent": 100,
        "progress_message": f"Kiểm định VideoQA: Đạt chuẩn ({video_score}/100, {qa_report.width}x{qa_report.height} {qa_report.fps}fps)",
        "status": "COMPLETED",
    }
