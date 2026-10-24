import json
"""
app.graph.nodes.script_nodes — Các Node xử lý Kịch bản & Tinh chỉnh tự động trong LangGraph.
"""
import logging
import os
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
        "audio_retry_count": 0,
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
    video_format = str(state.get("video_format") or "short").strip().lower()
    if video_format not in ("short", "long"):
        video_format = "long" if "long" in video_format or "16:9" in video_format else "short"

    content_agent = ContentAgent()
    script: ScriptContract = content_agent.generate_script(
        topic=topic,
        channel_niche=channel_style,
        language=language,
        director_notes=director_notes,
        channel_id=target_channel_id,
        video_format=video_format,
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
        "video_format": script.video_format,
        "current_step": "SCRIPT_GENERATED",
        "progress_percent": 20,
        "progress_message": f"Đã sinh kịch bản ({'Video Dài 16:9' if script.video_format == 'long' else 'Shorts 9:16'}): {script.title} ({len(script.scenes)} cảnh)",
    }


def node_evaluate_script(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node 3: Script Critic phản biện, chấm điểm kịch bản và lưu lại ứng viên đạt điểm cao nhất (Best-Candidate Tracking)."""
    script_data = state.get("script_data", {})
    topic = state.get("topic", "")
    channel_style = state.get("channel_style", "general")
    primary_entity = state.get("primary_entity")
    target_channel_id = state.get("target_channel_id")

    critic = ScriptCritic()
    eval_report = critic.evaluate_script(
        script_data=script_data,
        topic=topic,
        channel_style=channel_style,
        primary_entity=primary_entity,
        channel_id=target_channel_id,
    )
    eval_dict = eval_report.model_dump()

    # Lưu giữ phiên bản kịch bản tốt nhất qua các vòng tinh chỉnh (tránh vòng cuối bị tụt điểm đè mất bản tốt trước đó)
    best_data = state.get("best_script_data")
    best_eval = state.get("best_script_eval")

    def _rank(ev: Dict[str, Any]) -> tuple:
        if not ev:
            return (0, 0.0, 0.0)
        hk = float((ev.get("hook_strength") or {}).get("score", 0.0) or 0.0)
        ov = float(ev.get("overall_score", 0.0) or 0.0)
        ps = 1 if (ev.get("passed") and ov >= 8.0 and hk >= 8.0) else 0
        return (ps, round(hk * 0.55 + ov * 0.45, 3), ov)

    if not best_eval or _rank(eval_dict) >= _rank(best_eval):
        best_data = script_data
        best_eval = eval_dict

    logger.info(
        f"🎯 [ScriptCritic] Điểm kịch bản: {eval_report.overall_score}/10 | "
        f"Passed: {eval_report.passed} | Hook: {eval_report.hook_strength.score} | "
        f"Best Overall so far: {(best_eval or {}).get('overall_score')}/10"
    )

    import os
    retry_count = state.get("script_retry_count", 0)
    max_retries = max(2, int(os.getenv("MAX_SCRIPT_RETRIES", "5")))
    out_script_data = script_data
    out_eval_dict = eval_dict
    if retry_count >= max_retries and best_data and best_eval and _rank(best_eval) > _rank(eval_dict):
        logger.info(f"🛡️ [BestCandidateRestore] Khôi phục bản kịch bản tốt nhất (Overall={best_eval.get('overall_score')}/10) ở vòng thử cuối!")
        out_script_data = best_data
        out_eval_dict = best_eval

    job_id = state.get("job_id")
    if job_id and out_script_data and out_script_data.get("scenes"):
        try:
            from app.database.models import get_db, VideoJob
            with get_db() as _db:
                _j = _db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if _j:
                    _j.scenes_json = json.dumps(out_script_data, ensure_ascii=False)
                    if out_script_data.get("title"):
                        _j.title = out_script_data["title"]
        except Exception as _e_sc:
            logger.debug(f"[ScriptNode] Early scenes_json checkpoint skip: {_e_sc}")

    return {
        "script_data": out_script_data,
        "script_eval": out_eval_dict,
        "best_script_data": best_data,
        "best_script_eval": best_eval,
        "current_step": "SCRIPT_EVALUATED",
        "progress_percent": 25,
        "progress_message": f"Chấm điểm kịch bản: {eval_report.overall_score}/10 ({'Đạt chuẩn' if eval_report.passed else 'Cần tinh chỉnh'})",
    }


def node_refine_script(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node 4: Tự động tinh chỉnh phẫu thuật (Surgical Refinement) hoặc tái tạo Hook sắc bén từ ứng viên tốt nhất."""
    # Luôn lấy ứng viên tốt nhất làm nền tảng để cải tiến thay vì bản bị tụt điểm
    script_data = state.get("best_script_data") or state.get("script_data", {})
    script_eval = state.get("best_script_eval") or state.get("script_eval", {})
    retry_count = state.get("script_retry_count", 0) + 1
    max_retries = max(1, int(os.getenv("MAX_SCRIPT_RETRIES", "5")))
    critiques = script_eval.get("actionable_critiques", [])
    cur_overall = float((script_eval or {}).get("overall_score", 0.0) or 0.0)
    cur_hook = float(((script_eval or {}).get("hook_strength") or {}).get("score", 0.0) or 0.0)
    topic = state.get("topic", "")
    channel_style = state.get("channel_style", "general")
    primary_entity = state.get("primary_entity")
    language = state.get("language", "en")
    director_notes = state.get("director_notes")
    target_channel_id = state.get("target_channel_id", "")
    video_format = str(
        state.get("video_format")
        or (script_data or {}).get("video_format")
        or "short"
    ).strip().lower()
    if video_format not in ("short", "long"):
        video_format = "long" if "long" in video_format or "16:9" in video_format else "short"

    logger.info(
        f"🔄 [LangGraph] Tinh chỉnh kịch bản ({video_format.upper()}) lần {retry_count}/{max_retries} "
        f"(Điểm nền: Overall={cur_overall}/10, Hook={cur_hook}/10) theo nhận xét: {critiques}"
    )

    if video_format == "long":
        feedback_text = (
            f"[VÒNG TINH CHỈNH VIDEO DÀI 16:9 {retry_count}/{max_retries} — Điểm hiện tại: Overall={cur_overall}/10, Hook={cur_hook}/10 -> "
            f"MỤC TIÊU BẮT BUỘC: Overall >= 8.2/10 và Hook Cảnh 1 >= 8.5/10]. "
            f"Sửa chữa TRIỆT ĐỂ các lỗi sau từ Critic: {'; '.join(critiques)}. "
            f"YÊU CẦU CỐT LÕI VIDEO DÀI: "
            f"(1) CẢNH 1 (COLD OPEN HOOK 8-14 TỪ) BẮT BUỘC ĐẶT TÊN THỰC THỂ CHÍNH + CON SỐ/BIẾN CỐ CỤ THỂ ngay câu đầu; "
            f"(2) TỔNG ĐỘ DÀI KỊCH BẢN PHIM TÀI LIỆU 5 CHƯƠNG: 350-650 từ chia thành 16-24 phân cảnh (mỗi cảnh thân 18-28 từ sâu sắc, giàu dẫn chứng khoa học/lịch sử); "
            f"(3) Tuyệt đối KHÔNG gắn #Shorts vào tiêu đề."
        )
    else:
        feedback_text = (
            f"[VÒNG TINH CHỈNH {retry_count}/{max_retries} — Điểm hiện tại: Overall={cur_overall}/10, Hook={cur_hook}/10 -> "
            f"MỤC TIÊU BẮT BUỘC: Overall >= 8.2/10 và Hook Cảnh 1 >= 8.5/10]. "
            f"Sửa chữa TRIỆT ĐỂ các lỗi sau từ Critic: {'; '.join(critiques)}. "
            f"YÊU CẦU CỐT LÕI: "
            f"(1) CẢNH 1 (HOOK 6-8 TỪ, ~2.0 GIÂY) BẮT BUỘC ĐẶT TÊN CON VẬT / THỰC THỂ CHÍNH NGAY TỪ THỨ 1-2 + HÀNH ĐỘNG VA CHẠM + CON SỐ VẬT LÝ CỤ THỂ ngay giây 0.0s "
            f"(Cấm xóa tên con vật ở Cảnh 1, cấm mở đầu bằng đại từ mơ hồ 'Uma mordida/It/They' hay câu hỏi Yes/No!); "
            f"(2) TỔNG ĐỘ DÀI KỊCH BẢN: 75-92 từ chia đều cho 7-8 phân cảnh (mỗi cảnh thân 10-13 từ mạch lạc, KHÔNG lặp lại cùng một con số ở 2 cảnh khác nhau, tạo thời lượng vàng 28s-36s); "
            f"(3) Cảnh cuối nối vòng lặp vô tận (Zero Spoken Subscribe CTA)."
        )
    if primary_entity:
        feedback_text += f" Giữ vững trọng tâm vào thực thể chính: {primary_entity}."

    content_agent = ContentAgent()
    new_script = None
    # Nếu đã thử >= 2 lần mà Hook vẫn < 8.0 hoặc điểm chung < 7.5 -> Tái tạo mới hoàn toàn với góc tiếp cận sốc hơn
    force_fresh_generation = (retry_count >= 2 and (cur_hook < 8.0 or cur_overall < 7.5)) or (retry_count >= max_retries)

    if not force_fresh_generation and script_data and script_data.get("scenes"):
        try:
            current_contract = ScriptContract(**script_data)
            new_script = content_agent.refine_script(
                current_script=current_contract,
                feedback=feedback_text,
                channel_niche=channel_style,
                language=language,
                director_notes=director_notes,
                video_format=video_format,
            )
        except Exception as e_ref:
            logger.warning(f"[LangGraph] refine_script lỗi ({e_ref}), chuyển sang generate_script có định hướng...")

    if new_script is None:
        refined_topic = f"{topic} (Focus on {primary_entity})" if (primary_entity and primary_entity.lower() not in topic.lower()) else topic
        combined_notes = f"{director_notes or ''}\n[CRITIC MANDATORY FIX]: {feedback_text}".strip()
        new_script = content_agent.generate_script(
            topic=refined_topic,
            channel_niche=channel_style,
            language=language,
            director_notes=combined_notes,
            channel_id=target_channel_id,
            video_format=video_format,
        )

    script_dict = new_script.to_dict() if hasattr(new_script, "to_dict") else new_script.model_dump()
    return {
        "script_data": script_dict,
        "video_format": getattr(new_script, "video_format", video_format),
        "script_retry_count": retry_count,
        "current_step": "SCRIPT_REFINED",
        "progress_message": f"Đã tinh chỉnh kịch bản theo nhận xét (Lần {retry_count}/{max_retries})",
    }
