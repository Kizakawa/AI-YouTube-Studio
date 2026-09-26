"""
app.graph.nodes.audio_nodes — Node xử lý Giọng đọc AI & Kiểm định nhịp điệu trong LangGraph.
"""
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any

from app.schemas.contracts import ScriptContract, SceneContract
from app.agents.voice_agent import VoiceAgent
from app.utils.subtitle_helper import SubtitleHelper
from app.graph.evaluators.audio_qa import AudioQA
from app.graph.state import VideoWorkflowState

logger = logging.getLogger(__name__)


async def node_generate_audio(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node sinh giọng đọc AI Edge-TTS từng cảnh và tạo tệp phụ đề SRT."""
    script_data = state.get("script_data", {})
    work_dir = Path(state.get("work_dir", "storage/cache"))
    audio_dir = work_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    script = ScriptContract(**script_data)
    voice_agent = VoiceAgent()

    # Sinh audio cho từng cảnh qua VoiceAgent
    scenes_objs = await voice_agent.generate_per_scene_audio(
        scenes=script.scenes,
        output_dir=str(audio_dir)
    )

    enriched_scenes = [
        {
            **s.model_dump(),
            "duration_ms": getattr(s, "audio_duration_ms", 3000) or 3000
        }
        for s in scenes_objs
    ]

    # Ghép toàn bộ audio và lưu phụ đề ASS Karaoke
    full_audio_path = str(work_dir / "full_voiceover.mp3")
    subtitle_path = str(work_dir / "subtitles.ass")   # ASS karaoke thay SRT thông thường

    total_duration_ms = await voice_agent.concat_scene_audio(
        scenes=scenes_objs,
        output_path=full_audio_path
    )

    # Tạo phụ đề ASS chuẩn Shorts với word-level karaoke highlight theo phong cách Niche Profile
    channel_style = state.get("channel_style") or state.get("channel_niche") or "general"
    SubtitleHelper.save_ass_file(scenes_objs, subtitle_path, niche_id=channel_style)

    return {
        "scenes_audio": enriched_scenes,
        "full_audio_path": full_audio_path,
        "total_audio_duration_ms": total_duration_ms,
        "subtitle_path": subtitle_path,
        "current_step": "AUDIO_READY",
        "progress_percent": 45,
        "progress_message": f"Đã tạo giọng đọc Edge-TTS ({total_duration_ms/1000:.1f}s) + phụ đề ASS Karaoke",
    }


def node_evaluate_audio(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node kiểm định chất lượng âm thanh và nhịp điệu (WPM)."""
    scenes_audio = state.get("scenes_audio", [])
    full_audio_path = state.get("full_audio_path", "")
    total_duration = state.get("total_audio_duration_ms", 0)
    subtitle_path = state.get("subtitle_path", "")

    eval_report = AudioQA.evaluate_audio(
        scenes_audio=scenes_audio,
        full_audio_path=full_audio_path,
        total_duration_ms=total_duration,
        subtitle_path=subtitle_path
    )

    logger.info(
        f"🎙️ [AudioQA] Thời lượng: {eval_report.total_duration_ms/1000:.1f}s | "
        f"Tốc độ: {eval_report.words_per_minute} WPM ({eval_report.pacing_rating}) | "
        f"Passed: {eval_report.passed}"
    )

    return {
        "audio_eval": eval_report.model_dump(),
        "current_step": "AUDIO_EVALUATED",
        "progress_percent": 50,
        "progress_message": f"Kiểm định âm thanh: {eval_report.words_per_minute:.0f} WPM ({eval_report.pacing_rating})",
    }
