"""
app.agents.visual_director_agent — Tác nhân Đạo Diễn Hình Ảnh & Chiến Lược Thị Giác Thông Minh.

Nhiệm vụ cốt lõi:
- Phân tích toàn diện ý tưởng, bối cảnh kịch bản và từng phân cảnh.
- Ra quyết định chiến lược thị giác tối ưu (Visual Modality) cho TỪNG cảnh:
    1. REAL_DOCUMENTARY : Nhân vật có thật, sự kiện lịch sử, di tích/công trình thật -> Lấy tư liệu thật.
    2. STOCK_VIDEO      : Cảnh có yếu tố hành động, chuyển động thực tế, b-roll đời sống -> Lấy Video B-Roll từ Pexels.
    3. AI_VIDEO         : Chủ đề huyền bí, giả tưởng, vũ trụ trừu tượng, khái niệm siêu thực, nội dung không có thực tế -> Tạo Video AI ảo.
    4. AI_IMAGE         : Khung hình nghệ thuật điện ảnh, concept art 8K bằng Imagen 3.
- Cung cấp câu lệnh tìm kiếm hoặc prompt AI được cá nhân hóa cao độ cho từng cảnh.
"""
import json
import logging
import re
from typing import Dict, List, Any, Optional
from config.settings import settings
from app.utils.key_rotator import KeyRotator

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
]


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
        Lập bản kế hoạch chiến lược thị giác cho toàn bộ kịch bản.
        Sử dụng VideoStrategistAgent để phân định chính xác:
        - REAL_ARCHIVE (Tư liệu lịch sử thật)
        - IMAGE_TO_VIDEO (Video phụ thuộc vào ảnh chủ đề - giữ nét, không méo)
        - TEXT_TO_VIDEO (Video AI trực tiếp - siêu thực, huyền bí)
        - STOCK_FOOTAGE (Video B-roll thực tế)
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
        """Sử dụng Gemini để phân tích kịch bản theo 4 chiến lược thị giác."""
        scenes_context = []
        for s in scenes:
            scenes_context.append({
                "scene_id": s.get("scene_id"),
                "narration": s.get("narration"),
                "visual_keyword": s.get("visual_keyword"),
                "visual_prompt": s.get("visual_prompt"),
                "media_type": s.get("media_type", "auto"),
            })

        # Nạp chỉ dẫn thị giác chuyên sâu từ Niche Intelligence Profile
        niche_visual_info = ""
        try:
            from app.services.niche_manager import get_niche, build_visual_strategy_prompt_instructions
            niche_prof = get_niche(channel_niche)
            niche_visual_info = f"\nQUY TẮC MỸ THUẬT & THỊ GIÁC CỦA NGÁCH ({niche_prof.get('name', channel_niche)}):\n" + build_visual_strategy_prompt_instructions(niche_prof)
        except Exception:
            pass

        system_prompt = f"""
Bạn là Tổng Đạo Diễn Hình Ảnh & Chiến Lược Thị Giác (Master Visual Strategy Director) cho kênh YouTube Shorts triệu view.
Nhiệm vụ: Phân tích kịch bản và ra quyết định chính xác từng cảnh quay nên dùng tư liệu loại nào. Nếu một cảnh đã có 'media_type' cụ thể do người dùng chỉ định khác 'auto', hãy ưu tiên tôn trọng mong muốn đó.

QUY TẮC PHÂN ĐỊNH CHIẾN LƯỢC & ĐAN XEN LINH HOẠT (BẮT BUỘC TUÂN THỦ):
TRIỆT TIÊU TƯ DUY GHÉP ẢNH TĨNH: YouTube Shorts yêu cầu chuyển động linh hoạt (Video 9:16). Ít nhất 60% - 75% video phải là Video chuyển động (STOCK_VIDEO hoặc AI_VIDEO). Tuyệt đối tránh để 2 cảnh ảnh tĩnh nối tiếp nhau!

1. 'STOCK_VIDEO' (Video B-Roll chuyển động thực tế 30-60fps):
   - Áp dụng khi: Cảnh miêu tả HÀNH ĐỘNG đời thực, CHUYỂN ĐỘNG thực tế (con người bước đi trên phố, xe cộ tấp nập, bàn tay gõ phím, đồng hồ cát trôi, giọt mưa rơi, biển sóng cuộn, khói bốc lên...).
   - BẮT BUỘC: Cảnh 1 (Hook mở màn 3s đầu) PHẢI là STOCK_VIDEO chuyển động mạnh để khóa chặt người xem, chống lướt qua.

2. 'AI_VIDEO' (Video AI ảo / Giả tưởng / Huyền bí / Siêu thực):
   - Áp dụng khi: Chủ đề HUYỀN BÍ (bí ẩn vũ trụ, năng lượng tâm linh, thuyết âm mưu viễn tưởng, không gian 4 chiều, lỗ đen, sinh vật huyền bí, hiện tượng siêu nhiên), hoặc NỘI DUNG CHUNG CHUNG TRỪU TƯỢNG không có ngoài đời thực.
   - Lý do: Thế giới giả tưởng và huyền bí không có ảnh hay video thật ngoài đời -> PHẢI dùng AI Video để tạo cảm giác choáng ngợp, kỳ ảo.

3. 'REAL_DOCUMENTARY' (Ảnh tư liệu lịch sử / Người thật):
   - Áp dụng khi: Cảnh nhắc trực tiếp đến NHÂN VẬT CÓ THẬT (Einstein, Tesla, Napoleon, danh nhân...), SỰ KIỆN LỊCH SỬ có thật (Thế chiến, thảm họa Chernobyl...), CÔNG TRÌNH / DI TÍCH cổ có thật (Kim tự tháp Giza, Vạn lý trường thành, tượng Nhân sư...).
   - Lý do: Khán giả cần thấy bằng chứng và nhân vật thật ngoài đời để tin tưởng (Authenticity).

4. 'AI_IMAGE' (Ảnh AI nghệ thuật điện ảnh 8K kết hợp 3D Parallax):
   - Áp dụng khi: Cảnh cần một bức tranh nghệ thuật điện ảnh tĩnh sâu sắc, concept art kịch tính, chân dung nhân vật giả tưởng cần cố định khuôn mặt không méo mó. Xen kẽ làm khoảng lặng sau các phân cảnh chuyển động nhanh.

THÔNG TIN ĐẦU VÀO:
- Chủ đề: {topic}
- Nhân vật / Thực thể chính: {primary_entity or 'Không có nhân vật cụ thể'} (Loại: {entity_type})
- Niche Kênh: {channel_niche}{niche_visual_info}
- Các phân cảnh:
{json.dumps(scenes_context, ensure_ascii=False, indent=2)}

HÃY TRẢ VỀ JSON DUY NHẤT THEO SCHEMA:
{{
  "topic_classification": "HISTORICAL_REALITY | MYSTERY_FANTASY | SCIENCE_ABSTRACT | DAILY_REALITY",
  "director_vision": "Mô tả ngắn gọn tầm nhìn tổng thể về màu sắc, không khí và nhịp điệu video",
  "scenes_strategy": [
    {{
      "scene_id": 1,
      "strategy": "REAL_DOCUMENTARY | STOCK_VIDEO | AI_VIDEO | AI_IMAGE",
      "rationale": "Giải thích ngắn gọn 1 câu vì sao chọn chiến lược này (bằng tiếng Việt)",
      "search_query": "Từ khóa tiếng Anh 2-3 từ để tìm tư liệu nếu là REAL_DOCUMENTARY hoặc STOCK_VIDEO",
      "ai_prompt": "Câu lệnh tiếng Anh chi tiết 9:16 phong cách điện ảnh nếu là AI_VIDEO hoặc AI_IMAGE",
      "camera_motion": "zoom_in | zoom_out | pan_left | pan_right | pan_up | pan_down",
      "sfx_cue": "whoosh | hit | tension_riser | cinematic_swell | heartbeat"
    }}
  ]
}}
"""
        raw = self._call_gemini(system_prompt)
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
        """Tự động phân loại dựa trên từ điển ngôn ngữ và bản chất chủ đề."""
        is_mystery_or_fantasy = any(w in topic.lower() for w in [
            "mystery", "mysteries", "secret", "alien", "ufo", "space", "galaxy", "supernatural",
            "paranormal", "magic", "fantasy", "myth", "monster", "ghost", "dimension", "portal",
            "time travel", "future", "huyền bí", "bí ẩn", "vũ trụ", "tâm linh", "giả tưởng", "kỳ lạ"
        ])

        is_historical = (bool(primary_entity) and entity_type in ("person", "civilization", "monument", "event", "historical_figure")) or any(w in topic.lower() for w in [
            "history", "war", "battle", "emperor", "king", "ancient", "century", "empire",
            "lịch sử", "thế chiến", "hoàng đế", "vua", "cổ đại", "triều đại"
        ])

        topic_class = "HISTORICAL_REALITY" if is_historical else ("MYSTERY_FANTASY" if is_mystery_or_fantasy else "DAILY_REALITY")

        motion_cues = {
            "walk", "run", "running", "moving", "movement", "crowd", "people", "city", "street",
            "traffic", "night", "dark", "shadow", "silhouette", "rain", "storm", "fire", "water", "sea",
            "clock", "time", "hourglass", "hands", "typing", "money", "cash", "brain", "mind",
            "looking", "staring", "whisper", "talking", "smoke", "candle", "door", "window", "alone"
        }

        fantasy_cues = {
            "energy", "glowing", "glow", "cosmic", "universe", "alien", "portal", "dimensional",
            "supernatural", "pyramid energy", "secret", "magic", "spirit", "floating", "beam",
            "ray", "power", "ancient technology", "mystic", "huyền bí", "phép thuật", "năng lượng"
        }

        motions_list = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"]
        scenes_strategy = []

        for idx, sc in enumerate(scenes):
            sc_id = sc.get("scene_id", idx + 1)
            narr = (sc.get("narration") or "").lower()
            kw = (sc.get("visual_keyword") or "").lower()
            vp = sc.get("visual_prompt") or ""
            sq = sc.get("stock_query") or kw

            text_all = f"{narr} {kw} {sq}".lower()

            strategy = "STOCK_VIDEO"
            rationale = "Cảnh có yếu tố chuyển động đời thực, sử dụng video B-Roll sinh động."

            user_pref = sc.get("media_type")
            if user_pref == "ai_video":
                strategy = "AI_VIDEO"
                rationale = "Chỉ định thủ công: Tạo Video AI ảo huyền bí / giả tưởng."
            elif user_pref == "video":
                strategy = "STOCK_VIDEO"
                rationale = "Chỉ định thủ công: Dùng Video B-Roll chuyển động 9:16."
            elif user_pref in ("photo", "documentary"):
                if primary_entity and entity_type in ("person", "civilization", "monument", "event", "historical_figure"):
                    strategy = "REAL_DOCUMENTARY"
                    rationale = "Chỉ định thủ công: Tìm ảnh tư liệu thực tế."
                else:
                    strategy = "STOCK_VIDEO"
                    rationale = "Chỉ định thủ công: Dùng Video B-Roll chuyển động 9:16."
            elif user_pref == "ai_image":
                strategy = "AI_IMAGE"
                rationale = "Chỉ định thủ công: Sinh ảnh nghệ thuật AI 8K."
            # Ưu tiên 1: Thực thể lịch sử có thật
            elif primary_entity and entity_type in ("person", "civilization", "monument", "event", "historical_figure") and (idx in (0, 1) or primary_entity.lower() in text_all):
                strategy = "REAL_DOCUMENTARY"
                rationale = f"Nhắc đến nhân vật/địa danh lịch sử có thật '{primary_entity}' -> dùng ảnh tư liệu thật để đảm bảo tính xác thực."
            # Ưu tiên 2: Yếu tố kỳ ảo, huyền bí, vũ trụ trừu tượng
            elif any(fc in text_all for fc in fantasy_cues) or (is_mystery_or_fantasy and idx in (1, 3, 5)):
                strategy = "AI_VIDEO"
                rationale = "Ý tưởng huyền bí / giả tưởng siêu thực không có ngoài đời -> tạo Video AI ảo để tăng sự cuốn hút."
            # Ưu tiên 3: Chuyển động b-roll
            elif any(mc in text_all for mc in motion_cues) or idx == 0:
                strategy = "STOCK_VIDEO"
                rationale = "Cảnh chuyển động hành động đời thực -> dùng Video B-Roll Pexels 9:16 giữ chân người xem."
            # Ưu tiên 4: Cảnh nghệ thuật tĩnh
            else:
                strategy = "AI_IMAGE"
                rationale = "Cảnh cần khung hình nghệ thuật điện ảnh đặc thù -> tạo ảnh AI chất lượng cao."

            scenes_strategy.append({
                "scene_id": sc_id,
                "strategy": strategy,
                "rationale": rationale,
                "search_query": sq or kw or "cinematic",
                "ai_prompt": vp or f"Cinematic 8k vertical shot of {kw or 'mystery scene'}, highly detailed, dramatic lighting, 9:16",
                "camera_motion": motions_list[idx % len(motions_list)],
                "sfx_cue": "whoosh" if idx == 0 else "hit"
            })

        return {
            "topic_classification": topic_class,
            "director_vision": f"Chiến lược kết hợp linh hoạt ({topic_class}): Tư liệu thật cho thực thể lịch sử, Video B-roll cho chuyển động đời thực, Video AI ảo cho yếu tố huyền bí.",
            "scenes_strategy": scenes_strategy
        }
