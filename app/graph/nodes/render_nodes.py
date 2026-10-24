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
            "sfx_cue": None,
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
                show_cta_badge=True,
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
        sfx_dir=None,
        enable_sfx=False,
        output_path=final_output,
        video_format="long" if is_long_form else "short",
    )

    agent = VideoAgent()
    await agent.render(render_job)

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

    return {
        "rendered_video_path": final_output,
        "video_format": "long" if is_long_form else "short",
        "current_step": "RENDERED",
        "progress_percent": 90,
        "progress_message": f"Đã render hoàn tất video ({'1920x1080 16:9' if is_long_form else '1080x1920 9:16'}, Ken Burns, Subtitles)",
    }


def node_evaluate_video(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node kiểm định chất lượng kỹ thuật VideoQA bằng FFprobe & QualityEvaluator (Tự động render lại nếu lỗi, Hard-Block nếu vẫn không đạt)."""
    rendered_video_path = state.get("rendered_video_path", "")
    total_audio_duration_ms = state.get("total_audio_duration_ms", 0)
    render_retry_count = int(state.get("render_retry_count", 0) or 0)
    max_render_retries = max(0, int(os.getenv("MAX_RENDER_RETRIES", "1")))

    qa_report = VideoQA.inspect_video(
        video_path=rendered_video_path,
        expected_duration_ms=total_audio_duration_ms
    )

    from app.services.quality_evaluator import QualityEvaluator
    eval_res = QualityEvaluator().evaluate_video(
        video_path=rendered_video_path,
        expected_duration_ms=total_audio_duration_ms,
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
        "progress_message": f"Kiểm định VideoQA: Đạt chuẩn ({video_score}/100, 1080x1920 30fps)",
        "status": "COMPLETED",
    }
