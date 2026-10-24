"""
app.services.video_repurposer — Dịch vụ tái chế & cắt ghép Video Viral Shorts (mô-đun hóa qua 4 Mixins).

Cấu trúc module trong `app/services/repurposer/` (~480–580 dòng/file):
- `transcript_and_fetcher_mixin.py`: Trích xuất metadata, phụ đề, căn chỉnh biên câu & tải phân đoạn video.
- `viral_moment_analyzer_mixin.py`: Bộ não LLM phân tích khoảnh khắc viral (`analyze_viral_moments`) & kịch bản Mashup (`analyze_viral_mashups`).
- `single_short_renderer_mixin.py`: Tổng hợp giọng đọc & render Short đơn (`render_repurposed_short`).
- `mashup_short_renderer_mixin.py`: Render video Mashup đa phân đoạn (`render_mashup_short`).
"""
from app.services.repurposer.mashup_short_renderer_mixin import MashupShortRendererMixin
from app.services.repurposer.single_short_renderer_mixin import SingleShortRendererMixin
from app.services.repurposer.transcript_and_fetcher_mixin import (
    CLIPS_DIR,
    DOWNLOADS_DIR,
    REPURPOSER_DIR,
    SUBS_DIR,
    TranscriptAndFetcherMixin,
    _get_video_download_lock,
    extract_youtube_video_id,
)
from app.services.repurposer.viral_moment_analyzer_mixin import ViralMomentAnalyzerMixin


class VideoRepurposerService(
    TranscriptAndFetcherMixin,
    ViralMomentAnalyzerMixin,
    SingleShortRendererMixin,
    MashupShortRendererMixin,
):
    """Dịch vụ phân tích và cắt ghép các đoạn cao trào từ video dài thành YouTube Shorts."""
    pass


video_repurposer = VideoRepurposerService()

__all__ = [
    "CLIPS_DIR",
    "DOWNLOADS_DIR",
    "REPURPOSER_DIR",
    "SUBS_DIR",
    "VideoRepurposerService",
    "_get_video_download_lock",
    "extract_youtube_video_id",
    "video_repurposer",
]
