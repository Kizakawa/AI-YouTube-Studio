"""
app.graph.nodes.script_nodes — Các Node xử lý Kịch bản & Tinh chỉnh tự động trong LangGraph.
"""
import logging
from pathlib import Path
from typing import Dict, Any

from app.schemas.contracts import ScriptContract
from app.agents.content_agent import ContentAgent
from app.agents.media_agent import MediaAgent, detect_video_primary_entity
from app.graph.evaluators.script_critic import ScriptCritic
from app.graph.state import VideoWorkflowState

logger = logging.getLogger(__name__)


def node_research_and_entity(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node 1: Phân tích chủ đề và nhận diện thực thể/nhân vật chính cho toàn bộ video."""
    topic = state.get("topic", "")
    title = state.get("title", "")
    
    ent_name, ent_type = detect_video_primary_entity(
        topic=topic,
        title=title,
        narration="",
        tags=[]
    )
    
    import os
    search_first = os.getenv("SEARCH_FIRST_VISUAL_MODE", "true").strip().lower() not in ("0", "false", "no", "off")
    entity_media_pool = []
    if ent_name and not search_first:
        logger.info(f"🌟 [LangGraph] Nhận diện thực thể chính: '{ent_name}' ({ent_type}). Kích hoạt Documentary Mode!")
        media_agent = MediaAgent()
        entity_media_pool = media_agent.fetch_entity_media_pool(ent_name, ent_type, count=15)
        logger.info(f"📸 [LangGraph] Đã thu thập sẵn {len(entity_media_pool)} ảnh tư liệu cho '{ent_name}'")
    elif ent_name:
        logger.info(f"⚡ [LangGraph] Nhận diện thực thể chính: '{ent_name}' ({ent_type}) — ưu tiên Gemini Search trực tiếp theo từng cảnh (bỏ qua tải trước kho Wiki chậm)")

    return {
        "primary_entity": ent_name,
        "entity_type": ent_type,
        "entity_media_pool": entity_media_pool,
        "current_step": "RESEARCH",
        "progress_percent": 10,
        "progress_message": f"Đã nhận diện: {ent_name or 'Chủ đề chung'} ({len(entity_media_pool)} tư liệu)",
        "script_retry_count": 0,
        "media_retry_count": 0,
        "render_retry_count": 0,
    }


def node_generate_script(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node 2: Tạo kịch bản 6 cảnh cấu trúc chuẩn Shorts với ContentAgent (hoặc tái sử dụng kịch bản đã có)."""
    existing_script = state.get("script_data")
    if existing_script and existing_script.get("scenes"):
        title = existing_script.get("title", "")
        scene_count = len(existing_script.get("scenes", []))
        logger.info(f"📜 [LangGraph] Sử dụng kịch bản sẵn có từ DB: '{title}' ({scene_count} cảnh)")
        return {
            "script_data": existing_script,
            "current_step": "SCRIPT_LOADED",
            "progress_percent": 20,
            "progress_message": f"Đã nạp kịch bản: {title} ({scene_count} cảnh)",
        }

    topic = state.get("topic", "")
    channel_style = state.get("channel_style", "general")
    primary_entity = state.get("primary_entity")
    language = state.get("language", "en")
    director_notes = state.get("director_notes")

    if primary_entity and primary_entity.lower() not in topic.lower():
        topic = f"{topic} (Focus on {primary_entity})"

    target_channel_id = state.get("target_channel_id", "")
    content_agent = ContentAgent()
    script: ScriptContract = content_agent.generate_script(
        topic=topic,
        channel_niche=channel_style,
        language=language,
        director_notes=director_notes,
        channel_id=target_channel_id,
    )

    # Chống trùng lặp tiêu đề với kho video cũ của kênh
    if target_channel_id:
        try:
            from app.services.channel_intelligence import ensure_unique_title
            unique_title, was_dup, dup_match = ensure_unique_title(
                candidate_title=script.title,
                channel_id=target_channel_id,
                topic=topic,
                language=language,
                similarity_threshold=0.40
            )
            if was_dup:
                logger.info(f"🛡️ [LangGraph] Đổi tiêu đề trùng lặp: '{script.title}' -> '{unique_title}' (Trùng: '{dup_match}')")
                script.title = unique_title
        except Exception as _e_dup:
            logger.debug(f"[LangGraph] Lỗi kiểm tra trùng tiêu đề: {_e_dup}")

    script_dict = script.to_dict()
    return {
        "script_data": script_dict,
        "current_step": "SCRIPT_GENERATED",
        "progress_percent": 20,
        "progress_message": f"Đã sinh kịch bản: {script.title} ({len(script.scenes)} cảnh)",
    }


def node_evaluate_script(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node 3: Script Critic phản biện và chấm điểm kịch bản (Bỏ qua nếu kịch bản đã được người dùng duyệt sẵn từ DB)."""
    script_data = state.get("script_data", {})
    if state.get("current_step") == "SCRIPT_LOADED" and state.get("script_retry_count", 0) == 0 and script_data.get("scenes"):
        logger.info("⚡ [ScriptCritic] Kịch bản đã được duyệt sẵn từ DB (SCRIPT_LOADED) — bỏ qua chấm lặp LLM để tăng tốc!")
        return {
            "script_eval": {
                "overall_score": 9.2,
                "passed": True,
                "evaluated_by": "pre_approved",
                "actionable_critiques": [],
            },
            "current_step": "SCRIPT_EVALUATED",
            "progress_percent": 25,
            "progress_message": "Chấm điểm kịch bản: 9.2/10 (Kịch bản đã duyệt sẵn)",
        }

    topic = state.get("topic", "")
    channel_style = state.get("channel_style", "general")
    primary_entity = state.get("primary_entity")

    critic = ScriptCritic()
    eval_report = critic.evaluate_script(
        script_data=script_data,
        topic=topic,
        channel_style=channel_style,
        primary_entity=primary_entity
    )

    logger.info(
        f"🎯 [ScriptCritic] Điểm kịch bản: {eval_report.overall_score}/10 | "
        f"Passed: {eval_report.passed} | Hook: {eval_report.hook_strength.score} | "
        f"Entity: {eval_report.entity_coherence.score}"
    )

    return {
        "script_eval": eval_report.model_dump(),
        "current_step": "SCRIPT_EVALUATED",
        "progress_percent": 25,
        "progress_message": f"Chấm điểm kịch bản: {eval_report.overall_score}/10 ({'Đạt chuẩn' if eval_report.passed else 'Cần tinh chỉnh'})",
    }


def node_refine_script(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node 4: Tự động tinh chỉnh phẫu thuật (Surgical Refinement) kịch bản dựa trên nhận xét của Critic."""
    script_data = state.get("script_data", {})
    script_eval = state.get("script_eval", {})
    retry_count = state.get("script_retry_count", 0) + 1
    critiques = script_eval.get("actionable_critiques", [])
    topic = state.get("topic", "")
    channel_style = state.get("channel_style", "general")
    primary_entity = state.get("primary_entity")
    language = state.get("language", "en")
    director_notes = state.get("director_notes")
    target_channel_id = state.get("target_channel_id", "")

    logger.info(f"🔄 [LangGraph] Tự động tinh chỉnh kịch bản lần {retry_count} theo nhận xét: {critiques}")

    feedback_text = (
        f"Sửa chữa chính xác các điểm yếu sau từ Critic: {'; '.join(critiques)}. "
        f"Đảm bảo Cảnh 1 giật gân trong 2.5 giây đầu (5-9 từ), Cảnh 4 có Mid-Video Re-Hook, "
        f"Cảnh 5-9 có Payoff giá trị cao, và Cảnh cuối kết thúc bằng cầu nối Vòng lặp Vô tận (Zero Spoken Subscribe CTA)."
    )
    if primary_entity:
        feedback_text += f" Giữ vững trọng tâm vào nhân vật/thực thể chính: {primary_entity}."

    content_agent = ContentAgent()
    new_script = None
    if script_data and script_data.get("scenes"):
        try:
            current_contract = ScriptContract(**script_data)
            new_script = content_agent.refine_script(
                current_script=current_contract,
                feedback=feedback_text,
                channel_niche=channel_style,
                language=language,
                director_notes=director_notes,
            )
        except Exception as e_ref:
            logger.warning(f"[LangGraph] refine_script lỗi ({e_ref}), chuyển sang generate_script có định hướng...")

    if new_script is None:
        refined_topic = f"{topic} (Focus on {primary_entity})" if (primary_entity and primary_entity.lower() not in topic.lower()) else topic
        combined_notes = f"{director_notes or ''}\n[CRITIC REVISION]: {feedback_text}".strip()
        new_script = content_agent.generate_script(
            topic=refined_topic,
            channel_niche=channel_style,
            language=language,
            director_notes=combined_notes,
            channel_id=target_channel_id,
        )

    script_dict = new_script.to_dict() if hasattr(new_script, "to_dict") else new_script.model_dump()
    return {
        "script_data": script_dict,
        "script_retry_count": retry_count,
        "current_step": "SCRIPT_REFINED",
        "progress_message": f"Đã tinh chỉnh kịch bản theo nhận xét (Lần {retry_count})",
    }

