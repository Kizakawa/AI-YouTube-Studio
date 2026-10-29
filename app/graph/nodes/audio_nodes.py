"""
app.graph.nodes.audio_nodes — Node xử lý Giọng đọc AI & Kiểm định nhịp điệu trong LangGraph.
"""
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, Optional

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
    Nếu được gọi lại từ vòng lặp tự sửa lỗi AudioQA (`audio_retry_count > 0`), tự động điều chỉnh `rate`
    hoặc xóa cache cảnh lỗi để khắc phục triệt để lỗi tốc độ WPM / khoảng lặng.
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

    audio_retry_count = int(state.get("audio_retry_count", 0) or 0)
    prev_audio_eval = state.get("audio_eval") or {}
    explicit_rate: Optional[str] = None

    if audio_retry_count > 0:
        pacing_rating = str(prev_audio_eval.get("pacing_rating") or "")
        feedback_reason = str(prev_audio_eval.get("feedback") or pacing_rating)
        if pacing_rating in ("TOO_FAST", "TOO_SHORT"):
            explicit_rate = "-6%" if audio_retry_count == 1 else "-12%"
        elif pacing_rating in ("TOO_SLOW", "TOO_LONG"):
            explicit_rate = "+14%" if audio_retry_count == 1 else "+20%"
        else:
            # Lỗi HAS_SILENCE hoặc thiếu file audio cảnh -> Xóa cache metadata để buộc tổng hợp lại
            for meta_file in audio_dir.glob("*.meta.json"):
                try:
                    meta_file.unlink(missing_ok=True)
                except Exception:
                    pass
            explicit_rate = "+6%"
        logger.info(
            f"🔄 [AudioNode] Tái tạo giọng đọc (Lần thử lại #{audio_retry_count}) "
            f"với rate={explicit_rate} để khắc phục lỗi AudioQA: {feedback_reason}"
        )

    job_id = state.get("job_id")
    target_channel_id = state.get("target_channel_id")
    video_format = str(
        state.get("video_format")
        or getattr(script, "video_format", "")
        or (script_data or {}).get("video_format")
        or ("long" if len(script.scenes) >= 14 else "short")
    ).strip().lower()
    is_long_form = video_format == "long"

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
                is_long_form=is_long_form,
            )
            return {
                "topic_classification": "LONGFORM_16X9_HYBRID" if is_long_form else "HYBRID_TOOL_CALLING",
                "director_vision": (
                    "Long-Form 16:9 Chapter-Pool Master Video + Internal Jump-Cuts + Hybrid 2D Doodle Explainer"
                    if is_long_form
                    else "YouTube Shorts Peak Clone (Hook + 2-3 Peak Clips) kết hợp Google Top Search Images"
                ),
                "scenes_strategy": tool_decisions,
                "scope_audited": False,
            }
        except Exception as e_plan:
            logger.warning(f"[ParallelPlanner] Lỗi lập kế hoạch Tool song song: {e_plan}")
            return None

    # Chạy SONG SONG: (1) Sinh Audio TTS tất cả các cảnh + (2) LLM bind_tools chọn công cụ hình ảnh cho tất cả các cảnh

    gen_kwargs: Dict[str, Any] = {
        "scenes": script.scenes,
        "output_dir": str(audio_dir),
        "voice": preferred_voice,
        "niche": channel_style,
        "channel_id": target_channel_id,
        "job_id": job_id,
        "is_long_form": is_long_form,
    }
    if explicit_rate:
        gen_kwargs["rate"] = explicit_rate

    scenes_objs, precomputed_visual_plan = await asyncio.gather(
        voice_agent.generate_per_scene_audio(**gen_kwargs),
        _run_visual_tool_planner(),
    )

    used_voice_engine = getattr(voice_agent, "last_used_engine", "edge_tts")
    if job_id:
        try:
            from app.database.models import get_db, VideoJob
            with get_db() as db:
                j_row = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if j_row:
                    j_row.voice_engine = used_voice_engine
        except Exception as ex_ve:
            logger.debug(f"[AudioNode] Không thể lưu voice_engine vào DB: {ex_ve}")

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
            show_cta_badge=False,
            is_long_form=is_long_form,
        ),
    )

    out_state: Dict[str, Any] = {
        "scenes_audio": enriched_scenes,
        "full_audio_path": full_audio_path,
        "total_audio_duration_ms": total_duration_ms,
        "subtitle_path": subtitle_path,
        "voice_engine": used_voice_engine,
        "video_format": "long" if is_long_form else "short",
        "current_step": "AUDIO_READY",
        "progress_percent": 45,
        "progress_message": f"Đã tạo giọng đọc ({used_voice_engine}, {total_duration_ms/1000:.1f}s) + phụ đề ASS + Kế hoạch Tool Hình ảnh",
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
    retry_count = int(state.get("audio_retry_count", 0) or 0)
    video_format = str(
        state.get("video_format")
        or (state.get("script_data") or {}).get("video_format")
        or "short"
    ).strip().lower()
    is_long_form = (video_format == "long") or (len(scenes_audio) >= 14)

    eval_report = AudioQA.evaluate_audio(
        scenes_audio=scenes_audio,
        full_audio_path=full_audio_path,
        total_duration_ms=total_duration,
        subtitle_path=subtitle_path,
        is_long_form=is_long_form,
    )

    logger.info(
        f"🎙️ [AudioQA] Thời lượng: {eval_report.total_duration_ms/1000:.1f}s | "
        f"Tốc độ: {eval_report.words_per_minute} WPM ({eval_report.pacing_rating}) | "
        f"Passed: {eval_report.passed}"
    )

    out: Dict[str, Any] = {
        "audio_eval": eval_report.model_dump(),
        "current_step": "AUDIO_EVALUATED",
        "progress_percent": 50,
        "progress_message": f"Kiểm định âm thanh: {eval_report.words_per_minute:.0f} WPM ({eval_report.pacing_rating})",
    }
    if not eval_report.passed:
        out["audio_retry_count"] = retry_count + 1
    return out
