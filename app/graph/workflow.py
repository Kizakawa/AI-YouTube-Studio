"""
app.graph.workflow — Xây dựng StateGraph đa tác nhân hoàn chỉnh bằng LangGraph.

Tích hợp:
- 4 Bộ Grader / QA độc lập: ScriptCritic, VisualCritic (kiểm tra độ sắc nét & độ phân giải), AudioQA, VideoQA.
- Vòng lặp phản biện & tự sửa chữa (Self-Correction Loops) ở mọi tầng.
- Khả năng kích hoạt sub-graph chỉnh sửa vi mô (Granular Edit).
"""
import logging
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
    """Điều hướng: Nếu kịch bản chưa đạt điểm chất lượng và chưa quá số lần thử -> Sửa lại."""
    script_eval = state.get("script_eval", {})
    passed = script_eval.get("passed", True)
    retry_count = state.get("script_retry_count", 0)
    evaluated_by = script_eval.get("evaluated_by", "ai")

    if not passed and retry_count < 2:
        logger.info(f"🔄 [ConditionalEdge] Kịch bản chưa đạt (Score: {script_eval.get('overall_score')}/10). Quay lại node_refine_script!")
        return "refine_script"

    # [P1-FIX-4] Nếu pass nhưng chỉ qua heuristic fallback (API lỗi) — ép retry 1 lần với AI thật
    if passed and evaluated_by == "heuristic" and retry_count == 0:
        logger.warning("⚠️ [ConditionalEdge] Script PASS nhưng qua heuristic fallback (API lỗi). Buộc retry với AI thật!")
        return "refine_script"

    logger.info("✅ [ConditionalEdge] Kịch bản đã đạt chuẩn chất lượng. Tiếp tục sang node_generate_audio!")
    return "generate_audio"


def should_refine_media(state: VideoWorkflowState) -> Literal["refine_media", "render_video"]:
    """Điều hướng: Kiểm tra độ sắc nét, độ phân giải & kỹ thuật media. Nếu lỗi -> Sửa lại. Nếu đạt -> Sang node_render_video!"""
    media_eval = state.get("media_eval", {})
    passed = media_eval.get("passed", True)
    retry_count = state.get("media_retry_count", 0)

    if not passed and retry_count < 2:
        logger.info(f"🔄 [ConditionalEdge] Media có cảnh chưa đạt độ sắc nét/kỹ thuật ({len(media_eval.get('failed_scene_ids', []))} cảnh). Chuyển sang node_refine_media!")
        return "refine_media"

    logger.info("✅ [ConditionalEdge] Media đạt chuẩn độ sắc nét & kỹ thuật. Chuyển sang node_render_video!")
    return "render_video"


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
    """Khởi tạo và biên dịch đồ thị LangGraph Video Engine."""
    builder = StateGraph(VideoWorkflowState)

    # Thêm các Nodes
    builder.add_node("research_and_entity", node_research_and_entity)
    builder.add_node("generate_script", node_generate_script)
    builder.add_node("evaluate_script", node_evaluate_script)
    builder.add_node("refine_script", node_refine_script)
    builder.add_node("generate_audio", node_generate_audio)
    builder.add_node("evaluate_audio", node_evaluate_audio)
    builder.add_node("visual_director", node_direct_visual_strategy)
    builder.add_node("produce_hook_video", node_produce_and_score_hook_video)
    builder.add_node("fetch_media", node_fetch_media)
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
    builder.add_edge("refine_script", "evaluate_script")  # Vòng lặp phản biện

    # ── Luồng Giọng đọc -> Đạo diễn hình ảnh -> Node Hook Video chuyên biệt (search Short & cắt đoạn Peak trước) -> Thu thập tư liệu các cảnh còn lại ──
    builder.add_edge("generate_audio", "evaluate_audio")
    builder.add_edge("evaluate_audio", "visual_director")
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
    builder.add_edge("evaluate_video", END)

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

