"""
app.services.media — Bộ module tìm kiếm, phân tích và trích xuất tư liệu truyền thông thông minh.
Tách biệt trách nhiệm (SRP):
- entity_detector: Nhận diện thực thể, tri thức lịch sử, lọc nhiễu, mapping kịch bản.
- stock_fetcher: Tương tác Pexels API, Pixabay API, Openverse, NASA media.
- archive_fetcher: Tương tác Wikipedia, Wikimedia Commons, Google Search cho tư liệu lịch sử.
"""

from app.services.media.entity_detector import (
    detect_real_entity,
    detect_video_primary_entity,
    build_character_media_pool,
    match_best_pool_photo,
    clean_scene_search_query,
    synthesize_scene_query,
    is_valid_entity_name,
)
from app.services.media.stock_fetcher import StockFetcher
from app.services.media.archive_fetcher import ArchiveFetcher
from app.services.media.media_history import (
    get_recent_media_urls,
    record_used_media,
    record_batch_used_media,
)

__all__ = [
    "detect_real_entity",
    "detect_video_primary_entity",
    "build_character_media_pool",
    "match_best_pool_photo",
    "clean_scene_search_query",
    "synthesize_scene_query",
    "is_valid_entity_name",
    "StockFetcher",
    "ArchiveFetcher",
    "get_recent_media_urls",
    "record_used_media",
    "record_batch_used_media",
]
