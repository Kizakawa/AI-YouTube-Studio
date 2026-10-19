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
    """
    Node sinh giọng đọc AI Edge-TTS từng cảnh + phụ đề ASS SONG SONG với bước LLM Đạo diễn chọn Tool hình ảnh.
    Giúp tiết kiệm 100% thời gian chờ gọi LLM ở bước visual_director tiếp theo!
    """
    script_data = state.get("script_data", {})
    work_dir = Path(state.get("work_dir", "storage/cache"))
    audio_dir = work_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    preferred_voice = state.get("preferred_voice")
    channel_style = state.get("channel_style") or state.get("channel_niche") or "general"
    topic = state.get("topic", "")
    primary_entity = state.get("primary_entity")
    script = ScriptContract(**script_data)
    voice_agent = VoiceAgent()

    async def _run_visual_tool_planner():
        if state.get("visual_plan"):
            return state.get("visual_plan")
        try:
            from app.graph.tools.media_tools import plan_media_tools_with_llm_sync
            scenes_dicts = [s.model_dump() for s in script.scenes]
            tool_decisions = await asyncio.to_thread(
                plan_media_tools_with_llm_sync,
                topic=topic,
                scenes=scenes_dicts,
                primary_entity=primary_entity,
                channel_niche=channel_style,
            )
            return {
                "topic_classification": "HYBRID_TOOL_CALLING",
                "director_vision": "YouTube Shorts Peak Clone (Hook + 2-3 Peak Clips) kết hợp Google Top Search Images",
                "scenes_strategy": tool_decisions,
            }
        except Exception as e_plan:
            logger.warning(f"[ParallelPlanner] Lỗi lập kế hoạch Tool song song: {e_plan}")
            return None

    # Chạy SONG SONG: (1) Sinh Audio TTS tất cả các cảnh + (2) LLM bind_tools chọn công cụ hình ảnh cho tất cả các cảnh
    scenes_objs, precomputed_visual_plan = await asyncio.gather(
        voice_agent.generate_per_scene_audio(
            scenes=script.scenes,
            output_dir=str(audio_dir),
            voice=preferred_voice,
            niche=channel_style,
        ),
        _run_visual_tool_planner(),
    )

    enriched_scenes = [
        {
            **s.model_dump(),
            "duration_ms": getattr(s, "audio_duration_ms", 3000) or 3000
        }
        for s in scenes_objs
    ]

    # Ghép toàn bộ audio và lưu phụ đề ASS Karaoke SONG SONG
    full_audio_path = str(work_dir / "full_voiceover.mp3")
    subtitle_path = str(work_dir / "subtitles.ass")   # ASS karaoke thay SRT thông thường
    hook_banner_text = getattr(script, "thumbnail_text", None) or getattr(script, "title", None) or state.get("title")

    total_duration_ms, _ = await asyncio.gather(
        voice_agent.concat_scene_audio(
            scenes=scenes_objs,
            output_path=full_audio_path
        ),
        asyncio.to_thread(
            SubtitleHelper.save_ass_file,
            scenes_objs,
            subtitle_path,
            niche_id=channel_style,
            hook_title=hook_banner_text,
            show_cta_badge=True,
        ),
    )

    out_state: Dict[str, Any] = {
        "scenes_audio": enriched_scenes,
        "full_audio_path": full_audio_path,
        "total_audio_duration_ms": total_duration_ms,
        "subtitle_path": subtitle_path,
        "current_step": "AUDIO_READY",
        "progress_percent": 45,
        "progress_message": f"Đã tạo giọng đọc Edge-TTS ({total_duration_ms/1000:.1f}s) + phụ đề ASS + Kế hoạch Tool Hình ảnh (Song song)",
    }
    if precomputed_visual_plan:
        out_state["visual_plan"] = precomputed_visual_plan
    return out_state


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
