"""
app.graph.workflow — Xây dựng StateGraph đa tác nhân hoàn chỉnh bằng LangGraph.

Tích hợp:
- 5 Bộ Grader / QA độc lập: ScriptCritic, HookVideoGrader, VisualCritic, AudioQA, VideoQA.
- Vòng lặp phản biện & tự sửa chữa (Self-Correction Loops) ở mọi tầng:
  1. evaluate_script -> refine_script (hoặc generate_audio)
  2. evaluate_audio  -> generate_audio (hoặc visual_director)
  3. evaluate_media  -> refine_media (hoặc render_video)
  4. evaluate_video  -> render_video (hoặc END / Hard-Block)
- Khả năng kích hoạt sub-graph chỉnh sửa vi mô (Granular Edit).
"""
import logging
import os
from typing import Literal

from langgraph.graph import StateGraph, END
from app.graph.state import VideoWorkflowState
from app.graph.nodes import (
    node_research_and_entity,
    node_generate_script,
    node_evaluate_script,
    node_refine_script,
    node_generate_audio,
    node_evaluate_audio,
    node_direct_visual_strategy,
    node_fetch_media,
    node_evaluate_media,
    node_refine_media,
    node_produce_and_score_hook_video,
    node_render_video,
    node_evaluate_video,
    node_execute_edit,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# CONDITIONAL EDGES (CÁC ĐIỀU KIỆN ĐIỀU HƯỚNG VÒNG LẶP)
# ──────────────────────────────────────────────────────────────────────────────

def should_refine_script(state: VideoWorkflowState) -> Literal["refine_script", "generate_audio"]:
    """Điều hướng: Bắt buộc kịch bản đạt chuẩn (Overall >= 8.0 và Hook >= 8.0). Nếu hết lượt thử, khôi phục phiên bản đạt điểm cao nhất (Best Candidate) trong LangGraph!"""
    script_eval = state.get("script_eval", {})
    passed = bool(script_eval.get("passed", False))
    hook_s = float((script_eval.get("hook_strength") or {}).get("score", 0.0) or 0.0)
    overall_s = float(script_eval.get("overall_score", 0.0) or 0.0)
    retry_count = state.get("script_retry_count", 0)
    evaluated_by = script_eval.get("evaluated_by", "ai")
    max_script_retries = max(0, int(os.getenv("MAX_SCRIPT_RETRIES", "5")))

    is_strictly_passing = passed and overall_s >= 8.4 and hook_s >= 8.4

    if not is_strictly_passing and retry_count < max_script_retries:
        logger.info(
            f"🔄 [ConditionalEdge] Kịch bản chưa đạt chuẩn 8.4+ (Overall={overall_s}/10, Hook={hook_s}/10 | "
            f"Lần {retry_count + 1}/{max_script_retries}). Quay lại node_refine_script!"
        )
        return "refine_script"

    if is_strictly_passing and evaluated_by == "heuristic" and retry_count == 0 and max_script_retries > 0:
        logger.warning("⚠️ [ConditionalEdge] Script PASS nhưng qua heuristic fallback. Buộc retry với AI thật!")
        return "refine_script"

    if not is_strictly_passing:
        best_data = state.get("best_script_data")
        best_eval = state.get("best_script_eval")
        if best_data and best_eval:
            b_ov = float(best_eval.get("overall_score", 0.0) or 0.0)
            b_hk = float((best_eval.get("hook_strength") or {}).get("score", 0.0) or 0.0)
            if (b_ov + b_hk) > (overall_s + hook_s):
                logger.info(
                    f"🛡️ [BestCandidateRestore] Khôi phục phiên bản kịch bản tốt nhất từ các vòng trước "
                    f"(Overall={b_ov}/10, Hook={b_hk}/10 thay cho vòng cuối {overall_s}/10)!"
                )
                state["script_data"] = best_data
                state["script_eval"] = best_eval
                overall_s, hook_s = b_ov, b_hk
        logger.warning(
            f"⚡ [ScriptGate] Hoàn tất {retry_count}/{max_script_retries} vòng tối ưu với bản tốt nhất "
            f"(Overall={overall_s}/10, Hook={hook_s}/10) -> Giữ nguyên trong LangGraph để đảm bảo Hook Video & Audio Cap!"
        )
        return "generate_audio"

    logger.info(
        f"✅ [ConditionalEdge] Kịch bản đạt chuẩn xuất sắc (Overall={overall_s}/10, Hook={hook_s}/10 sau {retry_count} lần tinh chỉnh). "
        f"Tiếp tục sang node_generate_audio!"
    )
    return "generate_audio"


def should_refine_audio(state: VideoWorkflowState) -> Literal["generate_audio", "visual_director"]:
    """Điều hướng: Nếu AudioQA phát hiện lỗi tốc độ (WPM > 180 / > 65s) hoặc khoảng lặng > 1.5s -> Tạo lại Audio với tốc độ TTS điều chỉnh."""
    audio_eval = state.get("audio_eval") or {}
    passed = bool(audio_eval.get("passed", True))
    retry_count = int(state.get("audio_retry_count", 0) or 0)
    max_audio_retries = max(0, int(os.getenv("MAX_AUDIO_RETRIES", "2")))

    if not passed and retry_count <= max_audio_retries:
        logger.warning(
            f"🔄 [ConditionalEdge] AudioQA chưa đạt chuẩn ({audio_eval.get('pacing_rating')}: {audio_eval.get('feedback')} | "
            f"Lần {retry_count}/{max_audio_retries}). Quay lại node_generate_audio để điều chỉnh tốc độ TTS!"
        )
        return "generate_audio"

    if not passed:
        logger.warning(
            f"⚠️ [AudioGate] Đã thử tối ưu Audio {retry_count - 1}/{max_audio_retries} lần ({audio_eval.get('feedback')}). "
            f"Tiếp tục sang visual_director với bản thu hiện tại."
        )
    else:
        logger.info(
            f"✅ [ConditionalEdge] AudioQA đạt chuẩn ({audio_eval.get('words_per_minute', 0):.0f} WPM, {audio_eval.get('pacing_rating')}). "
            f"Tiếp tục sang visual_director!"
        )
    return "visual_director"


def should_refine_media(state: VideoWorkflowState) -> Literal["refine_media", "render_video"]:
    """Điều hướng: Kiểm tra độ sắc nét, độ phân giải & kỹ thuật media. Nếu lỗi -> Sửa lại. Nếu đạt -> Sang node_render_video!"""
    media_eval = state.get("media_eval", {})
    passed = bool(media_eval.get("passed", True))
    retry_count = int(state.get("media_retry_count", 0) or 0)
    max_media_retries = max(0, int(os.getenv("MAX_MEDIA_RETRIES", "2")))
    failed_ids = media_eval.get("failed_scene_ids", [])

    if not passed and failed_ids and retry_count < max_media_retries:
        logger.info(
            f"🔄 [ConditionalEdge] Media có cảnh chưa đạt độ sắc nét/kỹ thuật ({len(failed_ids)} cảnh: {failed_ids} | "
            f"Lần {retry_count + 1}/{max_media_retries}). Chuyển sang node_refine_media!"
        )
        return "refine_media"

    if not passed:
        logger.warning(
            f"⚠️ [MediaGate] Đã hoàn tất {retry_count}/{max_media_retries} vòng sửa media "
            f"(Cảnh còn hạn chế: {failed_ids}, điểm chung: {media_eval.get('overall_score')}/10). Chuyển sang node_render_video!"
        )
    else:
        logger.info("✅ [ConditionalEdge] Media đạt chuẩn độ sắc nét & kỹ thuật. Chuyển sang node_render_video!")
    return "render_video"


def should_retry_render(state: VideoWorkflowState) -> Literal["render_video", "__end__"]:
    """Điều hướng: Nếu VideoQA phát hiện lỗi kỹ thuật ở lần render đầu -> Cho phép render lại (tối đa MAX_RENDER_RETRIES lần)."""
    video_eval = state.get("video_eval") or {}
    passed = bool(video_eval.get("passed", True))
    retry_count = int(state.get("render_retry_count", 0) or 0)
    max_render_retries = max(0, int(os.getenv("MAX_RENDER_RETRIES", "1")))

    if not passed and retry_count <= max_render_retries:
        logger.warning(
            f"🔄 [ConditionalEdge] VideoQA phát hiện lỗi kỹ thuật (Lần {retry_count}/{max_render_retries}). "
            f"Quay lại node_render_video để dựng lại!"
        )
        return "render_video"
    return "__end__"


def route_entry_point(state: VideoWorkflowState) -> Literal["execute_edit", "research_and_entity"]:
    """Điểm vào: Nếu có yêu cầu chỉnh sửa vi mô thì nhảy thẳng vào edit node."""
    if state.get("edit_request"):
        return "execute_edit"
    return "research_and_entity"


def route_after_edit(state: VideoWorkflowState) -> Literal["render_video", "__end__"]:
    """Nếu chỉ đổi tiêu đề (metadata) thì kết thúc ngay, không cần render lại toàn bộ video."""
    edit_req = state.get("edit_request") or {}
    if state.get("current_step") == "TITLE_EDITED" or edit_req.get("action_type") == "edit_title":
        logger.info("⚡ [ConditionalEdge] Đã cập nhật tiêu đề xong — bỏ qua render lại video.")
        return "__end__"
    return "render_video"


# ──────────────────────────────────────────────────────────────────────────────
# GRAPH BUILDER
# ──────────────────────────────────────────────────────────────────────────────

def create_video_workflow_graph() -> StateGraph:
    """Khởi tạo và biên dịch đồ thị LangGraph Video Engine (kèm Step-Level Disk Checkpoint Resume)."""
    from app.graph.checkpoint_store import wrap_node_with_checkpoint

    builder = StateGraph(VideoWorkflowState)

    # Thêm các Nodes (bọc Step-Level Disk Checkpoint cho các bước tốn tài nguyên TTS / Media / Hook)
    builder.add_node("research_and_entity", node_research_and_entity)
    builder.add_node("generate_script", node_generate_script)
    builder.add_node("evaluate_script", node_evaluate_script)
    builder.add_node("refine_script", node_refine_script)
    builder.add_node("generate_audio", wrap_node_with_checkpoint("generate_audio", node_generate_audio))
    builder.add_node("evaluate_audio", wrap_node_with_checkpoint("evaluate_audio", node_evaluate_audio))
    builder.add_node("visual_director", wrap_node_with_checkpoint("visual_director", node_direct_visual_strategy))
    builder.add_node("produce_hook_video", wrap_node_with_checkpoint("produce_hook_video", node_produce_and_score_hook_video))
    builder.add_node("fetch_media", wrap_node_with_checkpoint("fetch_media", node_fetch_media))
    builder.add_node("evaluate_media", node_evaluate_media)
    builder.add_node("refine_media", node_refine_media)
    builder.add_node("render_video", node_render_video)
    builder.add_node("evaluate_video", node_evaluate_video)
    builder.add_node("execute_edit", node_execute_edit)

    # Thiết lập Entry Point theo điều kiện
    builder.set_conditional_entry_point(
        route_entry_point,
        {
            "research_and_entity": "research_and_entity",
            "execute_edit": "execute_edit",
        }
    )

    # ── Luồng Kịch bản & Critic ──
    builder.add_edge("research_and_entity", "generate_script")
    builder.add_edge("generate_script", "evaluate_script")
    builder.add_conditional_edges(
        "evaluate_script",
        should_refine_script,
        {
            "refine_script": "refine_script",
            "generate_audio": "generate_audio",
        }
    )
    builder.add_edge("refine_script", "evaluate_script")  # Vòng lặp phản biện kịch bản

    # ── Luồng Giọng đọc & AudioQA (có vòng lặp tự điều chỉnh tốc độ TTS) -> Đạo diễn hình ảnh -> Node Hook Video -> Thu thập Media ──
    builder.add_edge("generate_audio", "evaluate_audio")
    builder.add_conditional_edges(
        "evaluate_audio",
        should_refine_audio,
        {
            "generate_audio": "generate_audio",
            "visual_director": "visual_director",
        }
    )
    builder.add_edge("visual_director", "produce_hook_video")
    builder.add_edge("produce_hook_video", "fetch_media")

    # ── Kiểm tra ĐỘ SẮC NÉT & KỸ THUẬT media (VisualCritic) -> Merge & Lồng tiếng cuối cùng (render_video) -> VideoQA ──
    builder.add_edge("fetch_media", "evaluate_media")
    builder.add_conditional_edges(
        "evaluate_media",
        should_refine_media,
        {
            "refine_media": "refine_media",
            "render_video": "render_video",
        }
    )
    builder.add_edge("refine_media", "evaluate_media")  # Vòng lặp sửa file mờ/lỗi kỹ thuật

    builder.add_edge("render_video", "evaluate_video")
    builder.add_conditional_edges(
        "evaluate_video",
        should_retry_render,
        {
            "render_video": "render_video",
            "__end__": END,
        }
    )

    # ── Luồng Chỉnh sửa vi mô (Edit Sub-Graph) ──
    builder.add_conditional_edges(
        "execute_edit",
        route_after_edit,
        {
            "render_video": "render_video",
            "__end__": END,
        }
    )

    return builder.compile()


# Khởi tạo đồ thị biên dịch sẵn
video_graph = create_video_workflow_graph()
