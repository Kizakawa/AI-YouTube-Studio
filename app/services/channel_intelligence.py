"""
app.services.channel_intelligence — Facade điều phối Hệ thống Trí tuệ Kênh (giữ tương thích ngược 100%).

Đã được tách thành 6 module chuyên biệt trong `app/services/intelligence/` (~190–490 dòng/file):
- `deduplication.py`: Chống trùng lặp ý tưởng & kiểm tra lệch ngách.
- `portfolio_scanner.py`: Quét lịch sử kênh, xu hướng toàn cầu & công thức nhân bản video thắng.
- `niche_topic_fallbacks.py`: Kho chủ đề dự phòng chuẩn triệu view theo ngách & ngôn ngữ.
- `topic_suggester.py`: Bộ não LLM gợi ý chủ đề thông minh cho từng kênh (`suggest_channel_topics`).
- `kpi_and_safety.py`: Kiểm tra an toàn trước khi đăng & giám sát KPI định kỳ (`check_pre_publish_safety`, `run_network_kpi_audit`).
- `scheduler_and_broadcast.py`: Thông báo Telegram, tự động sản xuất video cứu kênh & `ChannelIntelligenceWorker`.
"""
from app.services.intelligence.deduplication import (
    DEFAULT_APPROVAL_GRACE_HOURS,
    KPI_CRITICAL_OVERDUE_HOURS,
    KPI_WARNING_HOURS,
    _normalize_text_for_similarity,
    check_idea_similarity,
    ensure_unique_title,
    extract_saturated_channel_concepts,
    is_off_niche_video_title,
)
from app.services.intelligence.portfolio_scanner import (
    _get_global_trends_directive,
    get_channel_existing_portfolio,
    get_winner_cloner_blueprint,
)
from app.services.intelligence.niche_topic_fallbacks import _get_curated_niche_fallbacks
from app.services.intelligence.topic_suggester import suggest_channel_topics
from app.services.intelligence.kpi_and_safety import (
    calculate_channel_kpi,
    check_pre_publish_safety,
    run_network_kpi_audit,
)
from app.services.intelligence.scheduler_and_broadcast import (
    ChannelIntelligenceWorker,
    process_fallback_publishing,
    send_telegram_broadcast,
    send_telegram_video_broadcast,
    trigger_auto_generation_for_overdue_channel,
)

__all__ = [
    "ChannelIntelligenceWorker",
    "DEFAULT_APPROVAL_GRACE_HOURS",
    "KPI_CRITICAL_OVERDUE_HOURS",
    "KPI_WARNING_HOURS",
    "_get_curated_niche_fallbacks",
    "_get_global_trends_directive",
    "_normalize_text_for_similarity",
    "calculate_channel_kpi",
    "check_idea_similarity",
    "check_pre_publish_safety",
    "ensure_unique_title",
    "extract_saturated_channel_concepts",
    "get_channel_existing_portfolio",
    "get_winner_cloner_blueprint",
    "is_off_niche_video_title",
    "process_fallback_publishing",
    "run_network_kpi_audit",
    "send_telegram_broadcast",
    "send_telegram_video_broadcast",
    "suggest_channel_topics",
    "trigger_auto_generation_for_overdue_channel",
]
