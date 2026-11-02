"""app.graph.nodes.media.visual_director_node — Node LLM đạo diễn chiến lược hình ảnh."""
import asyncio
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple

from app.schemas.contracts import ScriptContract, SceneContract
from app.agents.media_agent import MediaAgent, detect_real_entity, match_best_pool_photo, clean_scene_search_query
from app.agents.image_agent import ImageAgent
from app.agents.visual_director_agent import VisualDirectorAgent
from app.agents.video_strategist_agent import VideoStrategistAgent
from app.agents.visual_curation_agent import VisualCurationAgent
from app.agents.ai_video_agent import AIVideoAgent
from app.graph.evaluators.visual_critic import VisualCritic
from app.graph.state import VideoWorkflowState

logger = logging.getLogger(__name__)

async def node_direct_visual_strategy(state: VideoWorkflowState) -> Dict[str, Any]:
    """
    Node LLM Đánh Giá & Quyết Định Chiến Lược Tìm Kiếm (`visual_director`):
    1. Đọc toàn bộ kịch bản và từng câu văn đã chia nhỏ (`scenes`).
    2. Dùng LLM Tool-Calling (`plan_media_tools_with_llm_sync`) + LLM Thẩm Định Phạm Vi (`audit_and_decide_search_scopes_with_llm_sync`)
       để đánh giá từng câu văn nhỏ:
       - Nếu câu văn chia nhỏ ra VẪN CÓ NGHĨA ĐỘC LẬP (`SCENE_SPECIFIC`) -> Duyệt đi search theo ý câu nhỏ đó.
       - Nếu câu văn chia nhỏ ra BỊ VÔ NGHĨA / CỤT NGỦN / LỆCH CHỦ ĐỀ (`SCRIPT_OVERALL`) -> Quyết định đi search
         nội dung xoay quanh TỔNG THỂ KỊCH BẢN (`overall_script_query`).
    """
    topic = state.get("topic", "")
    script_data = state.get("script_data", {})
    primary_entity = state.get("primary_entity") or script_data.get("primary_entity")
    channel_style = state.get("channel_style", "cinematic")
    scenes = script_data.get("scenes", [])
    video_format = str(
        state.get("video_format")
        or (script_data or {}).get("video_format")
        or ("long" if len(scenes) >= 14 else "short")
    ).strip().lower()
    is_long_form = video_format == "long"

    from app.graph.tools.media_tools import (
        plan_media_tools_with_llm_sync,
        audit_and_decide_search_scopes_with_llm_sync,
    )

    existing_plan = state.get("visual_plan")
    if existing_plan and existing_plan.get("scenes_strategy"):
        strat_list = existing_plan.get("scenes_strategy", [])
        if not existing_plan.get("scope_audited") and not all(isinstance(d, dict) and d.get("scope_audited") for d in strat_list):
            strat_list = await asyncio.to_thread(
                audit_and_decide_search_scopes_with_llm_sync,
                topic=topic,
                scenes=scenes,
                proposed_decisions=strat_list,
                primary_entity=primary_entity,
                channel_niche=channel_style,
            )
            existing_plan = {
                **existing_plan,
                "scenes_strategy": strat_list,
                "scope_audited": True,
            }
        summary = ", ".join([
            f"S{s.get('scene_id')}:{s.get('search_scope', 'SCENE_SPECIFIC')}[{s.get('tool_name', '')}]"
            for s in strat_list[:6]
        ])
        logger.info(f"⚖️ [VisualDirector Node] LLM đã đánh giá & chốt quyết định Search Scope: {summary}...")
        return {
            "visual_plan": existing_plan,
            "current_step": "VISUAL_STRATEGY_PLANNED",
            "progress_percent": 55,
            "progress_message": f"⚖️ Node LLM đã đánh giá & quyết định phạm vi tìm kiếm cho {len(strat_list)} cảnh",
        }

    tool_decisions = await asyncio.to_thread(
        plan_media_tools_with_llm_sync,
        topic=topic,
        scenes=scenes,
        primary_entity=primary_entity,
        channel_niche=channel_style,
        is_long_form=is_long_form,
    )
    if tool_decisions and all(isinstance(d, dict) and d.get("scope_audited") for d in tool_decisions):
        audited_decisions = tool_decisions
    else:
        audited_decisions = await asyncio.to_thread(
            audit_and_decide_search_scopes_with_llm_sync,
            topic=topic,
            scenes=scenes,
            proposed_decisions=tool_decisions,
            primary_entity=primary_entity,
            channel_niche=channel_style,
        )
    plan = {
        "topic_classification": "HYBRID_TOOL_CALLING",
        "director_vision": "Adaptive Dual-Scope Search (SCENE_SPECIFIC vs SCRIPT_OVERALL) + YouTube Peak & Google Images",
        "scenes_strategy": audited_decisions,
        "scope_audited": True,
    }

    proposals_summary = ", ".join([
        f"S{s.get('scene_id')}:{s.get('search_scope')}[{s.get('tool_name')}]"
        for s in audited_decisions[:6]
    ])
    logger.info(f"🎬 [VisualDirector Node] LLM đã đánh giá & chọn công cụ: {proposals_summary}...")

    return {
        "visual_plan": plan,
        "current_step": "VISUAL_STRATEGY_PLANNED",
        "progress_percent": 55,
        "progress_message": f"🎬 Node LLM đã đánh giá & chọn công cụ ({len(audited_decisions)} cảnh)",
    }

