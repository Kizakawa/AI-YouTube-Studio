"""
app.agents.visual_curation_agent — Tác nhân Phân tích Ngữ cảnh Kịch bản & Tuyển chọn Thị giác Thông minh (Facade).

Cấu trúc mô-đun trong `app/agents/curation/`:
- `scene_context_analyzer_mixin.py`: Giải mã ngữ cảnh kịch bản toàn cục (2-Step Holistic Script & Subtext Reasoning),
  khôi phục đại từ/chủ ngữ ẩn, dịch ẩn dụ sang hành động vật lý và tạo truy vấn sát ý kịch bản.
- `media_vision_curator_mixin.py`: Kiểm định và chấm chọn ứng viên Video/Ảnh bằng Multimodal Gemini Vision.
"""
import requests
from config.settings import settings
from app.utils.key_rotator import KeyRotator
from app.agents.curation.scene_context_analyzer_mixin import (
    AVAILABLE_MODELS,
    SceneContextAnalyzerMixin,
    _VISION_SEM,
    _GENERIC_SUBJECTS,
    _CAMERA_JARGON_RE,
    _clean_search_phrase,
    _enforce_subject_queries,
    _apply_creature_mode,
)
from app.agents.curation.media_vision_curator_mixin import MediaVisionCuratorMixin


class VisualCurationAgent(SceneContextAnalyzerMixin, MediaVisionCuratorMixin):
    """
    Tác nhân Tuyển chọn & Giám sát Thị giác Thông minh (Smart Visual Curation Agent).
    Đóng vai trò Trợ lý Đạo diễn Tuyển chọn Tư liệu dựa trên tư duy ngữ nghĩa kịch bản toàn cục.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None
        self.session = requests.Session()


__all__ = [
    "AVAILABLE_MODELS",
    "VisualCurationAgent",
    "_VISION_SEM",
    "_GENERIC_SUBJECTS",
    "_CAMERA_JARGON_RE",
    "_clean_search_phrase",
    "_enforce_subject_queries",
    "_apply_creature_mode",
]
