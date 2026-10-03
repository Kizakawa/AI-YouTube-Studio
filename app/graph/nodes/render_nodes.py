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

    # Ghép metadata media và audio cho từng scene
    audio_map = {s["scene_id"]: s for s in scenes_audio}
    enriched_scenes = []

    for m in scenes_media:
        sc_id = m["scene_id"]
        a_info = audio_map.get(sc_id, {})
        
        sc_dict = {
            "scene_id": sc_id,
            "narration": m.get("narration") or a_info.get("narration") or "",
            "visual_keyword": m.get("visual_keyword", ""),
            "visual_prompt": m.get("visual_prompt"),   # ← Cần cho AI image context
            "estimated_seconds": max(float(m.get("estimated_seconds") or 3.0), 1.0),
            "audio_duration_ms": a_info.get("audio_duration_ms") or a_info.get("duration_ms") or int(max(float(m.get("estimated_seconds") or 3.0), 1.0) * 1000),
            "audio_path": a_info.get("audio_path") or full_audio_path or "storage/temp/voiceover.mp3",
            "media_path": m.get("media_path", ""),
            "media_type": m.get("media_type", "image"),
            # Cinematic enhancement fields — cần cho Ken Burns & SFX
            "camera_motion": m.get("camera_motion"),   # ← Thiếu field này → motion mặc định
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

    render_job = RenderJobContract(
        job_id=job_id,
        title=title,
        scenes=enriched_scenes,
        full_audio_path=full_audio_path or (enriched_scenes[0].audio_path if enriched_scenes else ""),
        total_audio_duration_ms=resolved_total_dur,
        subtitle_path=subtitle_path,
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
