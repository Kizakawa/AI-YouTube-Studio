"""
app.graph.nodes.media.evaluate_tool_calls_node — Node LangGraph `node_evaluate_llm_tool_calls`.

Kiểm định và tự chữa lành (Auto-Heal) toàn bộ quyết định gọi `@tool` và câu truy vấn tìm kiếm của các LLM Nodes
(`node_generate_script` -> `node_direct_visual_strategy`) trước khi hệ thống tải video ở `produce_hook_video` và `fetch_media`.
"""
import asyncio
import logging
from typing import Any, Dict

from app.graph.evaluators.llm_tool_call_critic import LLMToolCallCritic
from app.graph.state import VideoWorkflowState

logger = logging.getLogger(__name__)


async def node_evaluate_llm_tool_calls(state: VideoWorkflowState) -> Dict[str, Any]:
    """
    Node 5.5 (`evaluate_tool_calls`):
    - Kiểm tra xem Node LLM Đạo Diễn (`visual_director`) đã gọi đúng `@tool` video chuyển động thật (>= 85% thời lượng)
      và truyền đúng `subject_entity` + `youtube_search_query` + `fallback_broad_query` khớp với từng phân cảnh kịch bản chưa.
    - Nếu có bất kỳ phân cảnh nào bị lệch chủ thể, dịch ẩn dụ theo nghĩa đen, hoặc chọn nhầm tool ảnh tĩnh,
      `LLMToolCallCritic` sẽ tự động sửa lại (`AUTO_HEALED`) ngay trên `visual_plan` và `script_data` trước khi tải media.
    """
    topic = state.get("topic", "")
    script_data = state.get("script_data") or {}
    visual_plan = state.get("visual_plan") or {}
    primary_entity = state.get("primary_entity") or script_data.get("primary_entity")
    channel_style = state.get("channel_style", "general")

    critic = LLMToolCallCritic()
    report, healed_visual_plan, healed_script_data = await asyncio.to_thread(
        critic.evaluate_and_heal_tool_calls,
        topic=topic,
        script_data=script_data,
        visual_plan=visual_plan,
        primary_entity=primary_entity,
        channel_niche=channel_style,
    )

    rep_dict = report.model_dump()
    logger.info(f"🛠️ [ToolCallCritic Node] {report.summary}")

    return {
        "visual_plan": healed_visual_plan,
        "script_data": healed_script_data,
        "tool_call_eval": rep_dict,
        "current_step": "TOOL_CALLS_EVALUATED",
        "progress_percent": 58,
        "progress_message": (
            f"🛠️ Đã kiểm định Tool-Calling ({report.overall_tool_call_score}/10, "
            f"{report.moving_video_planned_ratio_pct:.0f}% Video Tools)"
        ),
    }
