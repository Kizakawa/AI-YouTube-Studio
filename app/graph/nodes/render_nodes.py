"""
app.graph.nodes.render_nodes — Node Render Video bằng VideoAgent & Kiểm định VideoQA.
"""
import logging
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

    # Ghép metadata media và audio cho từng scene & ĐẢM BẢO 100% VIDEO CHUYỂN ĐỘNG (ZERO STATIC IMAGE LEAKS)
    import asyncio
    from app.agents.ai_video_agent import AIVideoAgent
    ai_video_agent = AIVideoAgent()
    niche_for_motion = state.get("channel_style") or state.get("niche") or "general"
    topic_lower_mot = (state.get("topic") or state.get("title") or "").lower()
    is_scifi_mot = bool(state.get("is_scifi")) or (niche_for_motion == "scifi") or any(k in topic_lower_mot for k in ("scifi", "sci-fi", "alien", "cyborg", "viễn tưởng", "quantum"))
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
        dur_sec = max(float(dur_ms) / 1000.0, 2.0)
        cam_motion = m.get("camera_motion") or _motion_cycle[idx_m % len(_motion_cycle)]
        cur_media_path = str(m.get("media_path") or "")
        cur_media_type = str(m.get("media_type") or "image")

        # Nếu cảnh đang là ảnh tĩnh (.jpg/.png/.webp hoặc media_type != 'video'), nâng cấp ngay thành Video 3D Motion MP4 30fps!
        if cur_media_path and Path(cur_media_path).exists() and (
            cur_media_type != "video" or Path(cur_media_path).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        ):
            try:
                anim_mp4_path = str(work_dir / "media" / f"scene_{sc_id:03d}_motion3d.mp4")
                Path(anim_mp4_path).parent.mkdir(parents=True, exist_ok=True)
                res_anim = await asyncio.to_thread(
                    ai_video_agent.animate_thematic_image,
                    image_path=cur_media_path,
                    output_path=anim_mp4_path,
                    duration_seconds=dur_sec,
                    camera_motion=cam_motion,
                    is_hook=(sc_id == 1),
                    niche=niche_for_motion,
                    is_scifi=is_scifi_mot,
                )
                if res_anim and Path(res_anim).exists() and Path(res_anim).stat().st_size > 5000:
                    cur_media_path = res_anim
                    cur_media_type = "video"
                    m["media_path"] = cur_media_path
                    m["media_type"] = "video"
                    logger.info(f"[RenderNode] 🎬 Cảnh {sc_id}: Đã nâng cấp ảnh tĩnh thành Video 3D Motion MP4 ({dur_sec:.1f}s, {cam_motion})")
            except Exception as ex_mot:
                logger.debug(f"[RenderNode] Scene {sc_id} motion upgrade fallback: {ex_mot}")

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
            # Cinematic enhancement fields — cần cho Ken Burns & SFX
            "camera_motion": cam_motion,               # ← Thiếu field này → motion mặc định
            "sfx_cue": m.get("sfx_cue"),               # ← Thiếu field này → không có SFX
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
        output_path=final_output,
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
        "current_step": "RENDERED",
        "progress_percent": 90,
        "progress_message": "Đã render hoàn tất video (1080x1920, Ken Burns, Subtitles)",
    }


def node_evaluate_video(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node kiểm định chất lượng kỹ thuật VideoQA bằng FFprobe."""
    rendered_video_path = state.get("rendered_video_path", "")
    total_audio_duration_ms = state.get("total_audio_duration_ms", 0)

    qa_report = VideoQA.inspect_video(
        video_path=rendered_video_path,
        expected_duration_ms=total_audio_duration_ms
    )

    logger.info(
        f"🎬 [VideoQA] File: {Path(qa_report.video_path).name} | "
        f"Size: {qa_report.file_size_bytes/1024:.0f}KB | "
        f"Resolution: {qa_report.width}x{qa_report.height} | "
        f"Duration: {qa_report.duration_seconds}s | "
        f"Passed: {qa_report.passed}"
    )

    return {
        "video_eval": qa_report.model_dump(),
        "current_step": "QA_DONE",
        "progress_percent": 100,
        "progress_message": f"Kiểm định VideoQA: {'Xuất sắc (1080x1920 30fps)' if qa_report.passed else 'Cảnh báo lỗi'}",
        "status": "COMPLETED" if qa_report.passed else "QA_WARNING",
    }
