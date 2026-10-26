"""
app.graph.tools.media_tools — Facade điều phối bộ công cụ (@tool) truyền thông cho LLM Đạo diễn trong LangGraph.

Đã được mô-đun hóa theo chuẩn Single Responsibility (`architecture-first-design`, ~200–450 dòng/file):
- `tool_schemas.py`: Định nghĩa 5 `@tool` chuẩn LangGraph & hàm chuẩn hóa câu truy vấn.
- `youtube_search_ranker.py`: Tìm kiếm phẳng YouTube Shorts, bộ não LLM chọn ứng viên (`_rank_youtube_candidates_with_llm`) & Gemini Vision Gate.
- `youtube_peak_executor.py`: Thực thi săn & cắt đoạn Peak từ YouTube Shorts (`execute_youtube_short_peak_clone_sync`).
- `video_fallback_executors.py`: Các tầng dự phòng Bilibili B-Roll & Pexels/Pixabay/Wikimedia HD Stock Video.
- `google_image_executor.py`: Thực thi tìm kiếm đa ảnh Top Google Images (`execute_google_cinematic_image_sync`).
- `monster_media_executor.py`: Thực thi công cụ Quái vật khổng lồ 3D CGI & 8K Concept Art (`execute_epic_monster_cinematic_media_sync`).
- `portrait_broll_executor.py`: Thực thi công cụ Video B-Roll & Chân dung nghệ thuật sạch chữ (`execute_clean_cinematic_portrait_or_broll_sync`).
- `llm_media_director.py`: Bộ não LLM Đạo diễn chọn công cụ (`plan_media_tools_with_llm_sync`) & LLM Giám khảo duyệt phạm vi (`audit_and_decide_search_scopes_with_llm_sync`).
"""
from app.graph.tools.tool_schemas import (
    _yt_url_lock,
    _yt_search_sem,
    _yt_flat_search_cache,
    _used_short_windows,
    _TALKING_HEAD_BLOCKLIST,
    _HIGH_RISK_WATERMARK_CHANNEL_BLOCKLIST,
    _YT_QUERY_FILLER_WORDS,
    clone_youtube_short_peak_clip,
    search_google_cinematic_image,
    summon_epic_monster_cinematic_media,
    fetch_clean_cinematic_portrait_or_broll,
    render_doodle_2d_explainer_scene,
    search_pexels_pixabay_stock_video,
    MEDIA_DIRECTOR_TOOLS,
    _extract_subject_anchor_tokens,
    _clean_yt_search_phrase,
    _resolve_adaptive_search_queries,
    _sanitize_tool_arg,
)
from app.graph.tools.youtube_search_ranker import (
    _find_best_peak_window,
    _verify_short_clip_with_gemini_vision,
    _search_youtube_flat_cached,
    _rank_youtube_candidates_with_llm,
)
from app.graph.tools.video_fallback_executors import (
    _try_bilibili_broll_fallback,
    _try_licensed_stock_video_fallback,
)
from app.graph.tools.youtube_peak_executor import execute_youtube_short_peak_clone_sync
from app.graph.tools.google_image_executor import execute_google_cinematic_image_sync
from app.graph.tools.monster_media_executor import execute_epic_monster_cinematic_media_sync
from app.graph.tools.portrait_broll_executor import execute_clean_cinematic_portrait_or_broll_sync
from app.graph.tools.llm_media_director import (
    plan_media_tools_with_llm_sync,
    audit_and_decide_search_scopes_with_llm_sync,
)

__all__ = [
    "MEDIA_DIRECTOR_TOOLS",
    "clone_youtube_short_peak_clip",
    "search_google_cinematic_image",
    "summon_epic_monster_cinematic_media",
    "fetch_clean_cinematic_portrait_or_broll",
    "render_doodle_2d_explainer_scene",
    "search_pexels_pixabay_stock_video",
    "execute_youtube_short_peak_clone_sync",
    "execute_google_cinematic_image_sync",
    "execute_epic_monster_cinematic_media_sync",
    "execute_clean_cinematic_portrait_or_broll_sync",
    "plan_media_tools_with_llm_sync",
    "audit_and_decide_search_scopes_with_llm_sync",
    "_extract_subject_anchor_tokens",
    "_clean_yt_search_phrase",
    "_resolve_adaptive_search_queries",
    "_sanitize_tool_arg",
    "_find_best_peak_window",
    "_verify_short_clip_with_gemini_vision",
    "_search_youtube_flat_cached",
    "_rank_youtube_candidates_with_llm",
]
