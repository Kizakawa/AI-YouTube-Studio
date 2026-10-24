"""
app.agents.content_agent — Agent lên ý tưởng, viết kịch bản 5 hồi và tối ưu SEO (mô-đun hóa qua 5 files).

Cấu trúc module trong `app/agents/content/` (~175–520 dòng/file):
- `prompt_instructions.py`: Chỉ dẫn toàn vẹn ngôn ngữ & công thức Hook triệu view theo thị trường.
- `script_json_parser_mixin.py`: Gọi Gemini REST API xoay vòng khóa & chuẩn hóa JSON kịch bản.
- `script_generator_mixin.py`: Bộ nhớ ngữ cảnh kênh, phác thảo Blueprint & sinh kịch bản (`generate_script`).
- `script_fallback_and_refine_mixin.py`: Kịch bản dự phòng 5 hồi, tinh chỉnh (`refine_script`) & hội thoại Brainstorm.
- `viral_ideation_mixin.py`: Tuyển chọn kịch bản tốt nhất (`generate_best_script`) & gợi ý ý tưởng (`suggest_viral_ideas`).
"""
from app.utils.key_rotator import GEMINI_PRIORITY_MODELS
from app.agents.content.prompt_instructions import (
    build_language_integrity_instruction,
    build_regional_hook_mastery_instruction,
)
from app.agents.content.script_fallback_and_refine_mixin import ScriptFallbackAndRefineMixin
from app.agents.content.script_generator_mixin import ScriptGeneratorMixin
from app.agents.content.script_json_parser_mixin import ScriptJsonParserMixin
from app.agents.content.viral_ideation_mixin import ViralIdeationMixin

AVAILABLE_MODELS = list(GEMINI_PRIORITY_MODELS)


class ContentAgent(
    ScriptJsonParserMixin,
    ScriptGeneratorMixin,
    ScriptFallbackAndRefineMixin,
    ViralIdeationMixin,
):
    """Agent chịu trách nhiệm lên ý tưởng, viết kịch bản giật gân (hook), phân cảnh và metadata SEO."""
    pass


__all__ = [
    "AVAILABLE_MODELS",
    "ContentAgent",
    "build_language_integrity_instruction",
    "build_regional_hook_mastery_instruction",
]
