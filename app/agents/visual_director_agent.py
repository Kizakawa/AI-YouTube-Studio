"""
app.agents.visual_director_agent — Tác nhân Đạo Diễn Hình Ảnh & Chiến Lược Thị Giác Thông Minh.

Nhiệm vụ cốt lõi:
- Phân tích toàn diện ý tưởng, cốt truyện tổng thể (`full_script_story`), ngữ cảnh ngầm và từng phân cảnh bằng LLM.
- Ra quyết định chiến lược thị giác tối ưu (Visual Modality) cho TỪNG cảnh mà không phụ thuộc vào danh sách từ khóa cứng.
"""
import json
import logging
from typing import Dict, List, Any, Optional
from config.settings import settings
from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = list(GEMINI_PRIORITY_MODELS)


class VisualDirectorAgent:
    """
    Tác nhân Đạo Diễn Hình Ảnh (Smart Visual Strategy Director).
    Phân tích kịch bản và chỉ định phương thức tạo media tối ưu cho từng cảnh.
    """

    def __init__(self):
        from app.agents.video_strategist_agent import VideoStrategistAgent
        self.strategist = VideoStrategistAgent()
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None

    def create_visual_plan(
        self,
        topic: str,
        scenes: List[Dict[str, Any]],
        primary_entity: Optional[str] = None,
        entity_type: str = "general",
        channel_niche: str = "general",
    ) -> Dict[str, Any]:
        """
        Lập bản kế hoạch chiến lược thị giác cho toàn bộ kịch bản thông qua VideoStrategistAgent.
        """
        logger.info(f"🎬 [VisualDirector] Khởi chạy Video Strategist phân tích kịch bản: '{topic}' (Entity: '{primary_entity}')")
        return self.strategist.propose_production_strategy(
            topic=topic,
            scenes=scenes,
            primary_entity=primary_entity,
            entity_type=entity_type,
            channel_niche=channel_niche,
        )

    def _plan_with_ai(
        self,
        topic: str,
        scenes: List[Dict[str, Any]],
        primary_entity: Optional[str],
        entity_type: str,
        channel_niche: str,
    ) -> Optional[Dict[str, Any]]:
        """Sử dụng Gemini để phân tích kịch bản tổng thể và chỉ định chiến lược thị giác cho từng cảnh."""
        full_script_story = " ".join(str(s.get("narration") or "").strip() for s in scenes if s.get("narration"))
        scenes_context = [
            {
                "scene_id": s.get("scene_id"),
                "narration": s.get("narration"),
                "visual_keyword": s.get("visual_keyword"),
                "visual_prompt": s.get("visual_prompt"),
                "media_type": s.get("media_type", "auto"),
            }
            for s in scenes
        ]

        niche_visual_info = ""
        try:
            from app.services.niche_manager import get_niche, build_visual_strategy_prompt_instructions
            niche_prof = get_niche(channel_niche)
            niche_visual_info = f"\nQUY TẮC MỸ THUẬT & THỊ GIÁC CỦA NGÁCH ({niche_prof.get('name', channel_niche)}):\n" + build_visual_strategy_prompt_instructions(niche_prof)
        except Exception:
            pass

        system_prompt = f"""
Bạn là TỔNG ĐẠO DIỄN HÌNH ẢNH & CHIẾN LƯỢC THỊ GIÁC (Master Visual Strategy Director & Lead Showrunner) cho kênh YouTube Shorts triệu view.

══════════════════════════════════════════════════════════════════════════════
QUY TRÌNH TƯ DUY NGỮ NGHĨA 2 BƯỚC (HOLISTIC SCRIPT & SUBTEXT REASONING):
══════════════════════════════════════════════════════════════════════════════
1. BƯỚC 1 (Đọc hiểu toàn bộ kịch bản, Giải mã đại từ & Ẩn dụ):
   - Đọc kỹ `FULL SCRIPT STORY` trước khi xét từng phân cảnh.
   - Giải mã mọi đại từ ("It", "He", "They", "Nó", "Họ", "Ele") về đúng chủ thể chính (`{primary_entity or topic}`).
   - Chuyển hóa các phép ẩn dụ, ví von hoặc số liệu trừu tượng sang HÀNH ĐỘNG VẬT LÝ CỤ THỂ của chủ thể chính trong môi trường thật của câu chuyện.
2. BƯỚC 2 (Chọn chiến lược & viết truy vấn tự đứng vững cho từng cảnh):
   - 'REAL_DOCUMENTARY': Nhân vật/sự kiện lịch sử có thật, di tích, bằng chứng khoa học thực tế.
   - 'STOCK_VIDEO': Hành động vật lý đời thực, thiên nhiên, động vật hoang dã, biểu cảm con người chuyển động 30-60fps.
   - 'AI_IMAGE': Khung hình nghệ thuật 8K đặc tả chiều sâu tâm lý, ẩn dụ điện ảnh hoặc quái vật/thủy quái quy mô lớn kết hợp 3D Parallax.
   - 'AI_VIDEO': Hiện tượng giả tưởng/vũ trụ siêu thực không tồn tại ngoài đời.

THÔNG TIN ĐẦU VÀO:
- Chủ đề: {topic}
- Nhân vật / Thực thể chính: {primary_entity or 'Không có nhân vật cụ thể'} (Loại: {entity_type})
- Niche Kênh: {channel_niche}{niche_visual_info}
- TOÀN BỘ CỐT TRUYỆN (FULL SCRIPT STORY):
"{full_script_story}"
- Các phân cảnh:
{json.dumps(scenes_context, ensure_ascii=False, indent=2)}

HÃY TRẢ VỀ JSON DUY NHẤT THEO SCHEMA:
{{
  "topic_classification": "HISTORICAL_REALITY | MYSTERY_FANTASY | SCIENCE_ABSTRACT | DAILY_REALITY",
  "director_vision": "Mô tả ngắn gọn tầm nhìn tổng thể về màu sắc, không khí và nhịp điệu video",
  "scenes_strategy": [
    {{
      "scene_id": 1,
      "visual_reasoning": "Giải mã đại từ/ẩn dụ từ kịch bản tổng thể và cách chuyển dịch thành hình ảnh kịch tính",
      "strategy": "REAL_DOCUMENTARY | STOCK_VIDEO | AI_VIDEO | AI_IMAGE",
      "rationale": "Giải thích ngắn gọn 1 câu vì sao chọn chiến lược này (bằng tiếng Việt)",
      "search_query": "Từ khóa tiếng Anh 2-4 từ cụ thể (Chủ thể đã giải mã đại từ + hành động vật lý)",
      "ai_prompt": "Câu lệnh tiếng Anh 9:16 ghép theo công thức điện ảnh 7 tầng: [Shot Size] + [Camera Angle/POV] + [Lens] + [Physical Camera Movement] + [Composition] + [Lighting] + [Optical/Film Effect] + Hành động cụ thể (hỗ trợ gắn thẻ @CHARACTER_TAG)",
      "camera_motion": "dolly_in | dolly_out | arc_orbit | rack_focus | whip_pan | trucking_left | trucking_right | pedestal_up | worms_eye | overhead_90 | first_person_pov | over_the_shoulder | voyeur_shot | fpv_drone | dolly_zoom | zoom_in | zoom_out | pan_left | pan_right | tilt_up",
      "sfx_cue": "whoosh | hit | tension_riser | cinematic_swell | heartbeat"
    }}
  ]
}}
"""
        raw = self.strategist._call_gemini(system_prompt)
        if not raw:
            return None

        try:
            plan = json.loads(raw)
            if "scenes_strategy" in plan and isinstance(plan["scenes_strategy"], list):
                return plan
        except Exception as e:
            logger.warning(f"[VisualDirector] Lỗi parse JSON từ Gemini: {e}")

        return None

    def _plan_with_heuristics(
        self,
        topic: str,
        scenes: List[Dict[str, Any]],
        primary_entity: Optional[str],
        entity_type: str,
        channel_niche: str,
    ) -> Dict[str, Any]:
        """Dự phòng giữ nguyên ngữ cảnh của từng phân cảnh khi Gemini tạm thời offline."""
        delegated = self.strategist._propose_with_heuristics(
            topic=topic,
            scenes=scenes,
            primary_entity=primary_entity,
            entity_type=entity_type,
            channel_niche=channel_niche,
        )
        return {
            "topic_classification": delegated.get("topic_classification", "DAILY_REALITY"),
            "director_vision": delegated.get("director_overview", ""),
            "scenes_strategy": delegated.get("scenes_strategy", []),
        }
