"""
app.graph.nodes.media_nodes — Facade điều phối các Node Media trong LangGraph (mô-đun hóa qua `app/graph/nodes/media/`).

Cấu trúc các module con:
- `media_helpers.py`: Các hàm tiện ích đường dẫn, thời lượng audio và định danh clip.
- `visual_director_node.py`: Node `node_direct_visual_strategy` (LLM hoạch định chiến lược hình ảnh).
- `longform_broll_fetcher.py`: Thu thập & cắt lát B-Roll 16:9 cho Video Dài.
- `fetch_media_node.py`: Node `node_fetch_media` (thu thập đồng loạt tư liệu đa nguồn).
- `post_gather_rescue.py`: Hậu kiểm chống trùng lặp, cứu hộ tỷ lệ video & triệt tiêu thẻ placeholder.
- `evaluate_media_node.py`: Node `node_evaluate_media` (kiểm định kỹ thuật & độ sắc nét).
- `refine_media_node.py`: Node `node_refine_media` (tự động thay thế các phân cảnh chưa đạt chuẩn).
"""
from app.graph.nodes.media.media_helpers import (
    _safe_mtime,
    _safe_size,
    _extract_hook_candidate_vid_id,
    _collect_active_scene_source_paths,
    _scene_audio_duration,
)
from app.graph.nodes.media.visual_director_node import node_direct_visual_strategy
from app.graph.nodes.media.evaluate_tool_calls_node import node_evaluate_llm_tool_calls
from app.graph.nodes.media.longform_broll_fetcher import _fetch_longform_16x9_broll_clip_sync
from app.graph.nodes.media.fetch_media_node import node_fetch_media
from app.graph.nodes.media.post_gather_rescue import run_post_gather_media_rescues
from app.graph.nodes.media.evaluate_media_node import node_evaluate_media
from app.graph.nodes.media.refine_media_node import node_refine_media

__all__ = [
    "_safe_mtime",
    "_safe_size",
    "_extract_hook_candidate_vid_id",
    "_collect_active_scene_source_paths",
    "_scene_audio_duration",
    "node_direct_visual_strategy",
    "node_evaluate_llm_tool_calls",
    "_fetch_longform_16x9_broll_clip_sync",
    "node_fetch_media",
    "run_post_gather_media_rescues",
    "node_evaluate_media",
    "node_refine_media",
]
