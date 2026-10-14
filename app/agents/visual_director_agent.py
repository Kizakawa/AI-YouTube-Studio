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
Bạn là TỔNG ĐẠO DIỄN HÌNH ẢNH & CHIẾN LƯỢC THỊ GIÁC (Master Visual Strategy Director & Lead Showrunner) cho kênh YouTube Shorts triệu view hàng đầu thế giới (tiêu chuẩn điện ảnh A24, điều tra Vox, kỳ vĩ National Geographic/Melodysheep, trực quan Kurzgesagt).

NHIỆM VỤ TỐI THƯỢNG: Cực đại hóa tỷ lệ giữ chân khán giả (Retention Rate > 100%, Swipe-Away Rate < 15%). 
Nếu một cảnh đã có 'media_type' cụ thể do người dùng chỉ định khác 'auto', hãy ưu tiên tôn trọng mong muốn đó.

══════════════════════════════════════════════════════════════════════════════
I. CẤU TRÚC GIỮ CHÂN 4 NHỊP VÀNG (FLEXIBLE 35 - 48s ARCHITECTURE — VƯỢT NGƯỠNG 10K-50K VIEW)
══════════════════════════════════════════════════════════════════════════════
1. BEAT 1: HOOK ĐỘT PHÁ (0 - 3s — MỤC TIÊU VIEWED > 75%, SWIPED AWAY < 25%):
   Cảnh 1 BẮT BUỘC là cú sốc thị giác (cận cảnh macro góc hiểm, chuyển động punch-in gia tốc, độ tương phản cao) kết hợp âm thanh va chạm mạnh tại t=0.0s để khóa chặt ngón tay người xem ngay frame đầu tiên.
2. BEAT 2: BẰNG CHỨNG SỐC & DẤU HIỆU LEO THANG (3 - 20s — CHỐNG RƠI RỤNG 6s-12s):
   Bóc tách 2-3 dấu hiệu nhận biết, chuyển cảnh liên tục mỗi 3-4s với hành động vật lý và b-roll sống động.
3. BEAT 3: THE HIGH-VALUE PAYOFF — CÚ LẬT MẶT & GIẢI PHÁP THỰC CHIẾN (20 - 38s):
   Cao trào giải mã sự thật và vũ khí phản đòn. Hình ảnh đặc tả khoảnh khắc lật ngược thế cờ, chi tiết đắt giá hoặc tương tác then chốt mang tính biểu tượng cao.
4. BEAT 4: CALL TO ACTION & VÒNG LẶP LIÊN TỤC (38 - 48s — KÍCH NỔ COMMENT & LƯU VIDEO):
   Lời kêu gọi đăng ký kênh tự nhiên + một câu kết thúc ám ảnh nối mượt mà quay trở lại câu Hook ở Cảnh 1 (Infinite Looping Retention).

══════════════════════════════════════════════════════════════════════════════
II. BẢN ĐỒ CHIẾN LƯỢC & PHÂN CÔNG CẤP DƯỚI (DELEGATION PROTOCOL)
══════════════════════════════════════════════════════════════════════════════
1. 'REAL_DOCUMENTARY' (Ảnh tư liệu lịch sử / Người thật / Bằng chứng mạng mở):
   - Áp dụng khi: 
     * Nhân vật lịch sử có thật (Einstein, Tesla, Napoleon...), sự kiện lịch sử (Thế chiến, thảm họa Chernobyl...), công trình/di tích cổ có thật.
     * BÍ ẨN ĐẠI DƯƠNG, QUÁI VẬT BIỂN, TRUYỀN THUYẾT, HIỆN TƯỢNG TỰ NHIÊN (Tam giác quỷ, Kraken, Megalodon, rãnh Mariana, UFO Roswell...): Cần tư liệu thực tế (tiêu bản mực khổng lồ trôi dạt bờ biển, tranh khắc gỗ thế kỷ 18, bản đồ dị thường sonar, hóa thạch răng Megalodon, ảnh vệ tinh). Kết hợp hiệu ứng Ken Burns 3D Deep Zoom biến ảnh tư liệu thành thước phim tài liệu Discovery chân thực!
   - CHỈ ĐẠO CẤP DƯỚI QUA 'search_query': Đạo diễn PHẢI tự động suy nghĩ câu lệnh tìm kiếm tiếng Anh cụ thể 2-4 từ chỉ định cấp dưới tìm đúng ảnh tư liệu trên Google/Wikipedia (Ví dụ: 'giant squid specimen beach', '18th century kraken engraving', 'Bermuda triangle sonar map anomaly', 'megalodon tooth fossil comparison'). TUYỆT ĐỐI KHÔNG dùng từ vô nghĩa như 'cinematic'.

2. 'STOCK_VIDEO' (Video B-Roll chuyển động thực tế 30-60fps):
   - Áp dụng khi: Cảnh miêu tả HÀNH ĐỘNG đời thực, CHUYỂN ĐỘNG thực tế (con người bước đi trên phố, xe cộ tấp nập, bàn tay gõ phím, đồng hồ cát trôi, giọt mưa rơi, biển sóng cuộn, khói bốc lên, đại dương sâu rùng rợn...).
   - Chỉ đạo cấp dưới qua 'search_query': từ khóa b-roll khí quyển (Ví dụ: 'deep ocean submarine abyss', 'stormy ocean waves night', 'hourglass sand falling').

3. 'AI_IMAGE' (Ảnh AI nghệ thuật điện ảnh 8K kết hợp 3D Parallax):
   - NGUYÊN LÝ TƯ DUY LOGIC CỦA ĐẠO DIỄN (CHỦ ĐỘNG SUY LUẬN THEO NỘI DUNG KỊCH BẢN, TUYỆT ĐỐI KHÔNG DÙNG MẪU TỪ KHÓA ĐÓNG ĐINH):
     * Bước 1: Đọc hiểu kịch bản và phân tích: "Đâu là mâu thuẫn ngầm, điểm bất thường hoặc cảm xúc cốt lõi của cảnh này?"
     * Bước 2: Chuyển đổi logic ngữ nghĩa sang hình ảnh cụ thể: Tự suy luận một tình huống/hành động tương tác cụ thể trong thế giới thực hoặc ẩn dụ điện ảnh phản ánh đúng nội dung (ví dụ: hành vi lén lút, sự đối đầu giữa 2 nhân vật, ánh nhìn lật tẩy, một vật chứng bị giấu kín, một khoảnh khắc sụp đổ).
     * Bước 3: Tạo khoảng trống tò mò (Visual Curiosity Gap): Bức ảnh phải khiến người xem đặt câu hỏi: "Tại sao lại như vậy? Ai đang làm gì?".
     * BẮT BUỘC TUÂN THỦ NGUYÊN TẮC QUANG HỌC: Chủ thể phải có ánh sáng định hình rõ ràng (Key Light & Rim Light), có độ tương phản cao để nổi bật trên nền tối. CẤM tạo ảnh mặt người tối màu vô hồn, cấm cận cảnh mắt đơn độc thiếu bối cảnh!

4. 'AI_VIDEO' (Video AI ảo / Giả tưởng / Siêu thực thuần túy):
   - Áp dụng khi: NĂNG LƯỢNG SIÊU THỰC thuần túy (cổng không gian ma mị, luồng năng lượng biến ảo, khói ma biến hình) hoặc cảnh hành động viễn tưởng không có trong thế giới thực.

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
      "visual_reasoning": "Tư duy logic đạo diễn: Phân tích mâu thuẫn kịch bản và cách chuyển dịch thành hình ảnh kịch tính",
      "strategy": "REAL_DOCUMENTARY | STOCK_VIDEO | AI_VIDEO | AI_IMAGE",
      "rationale": "Giải thích ngắn gọn 1 câu vì sao chọn chiến lược này (bằng tiếng Việt)",
      "search_query": "Từ khóa tiếng Anh 2-3 từ để tìm tư liệu nếu là REAL_DOCUMENTARY hoặc STOCK_VIDEO",
      "ai_prompt": "Câu lệnh tiếng Anh chi tiết 9:16 phong cách điện ảnh do Đạo diễn tự sáng tạo theo logic kịch bản nếu là AI_VIDEO hoặc AI_IMAGE",
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
            "traffic", "rain", "storm", "fire", "water", "sea",
            "clock", "time", "hourglass", "hands", "typing", "money", "cash",
            "looking", "staring", "whisper", "talking", "smoke", "candle", "door", "window"
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
            # Ưu tiên 4: Xử lý cảnh viễn tưởng / kỳ ảo (TUYỆT ĐỐI KHÔNG DÙNG ẢNH TĨNH)
            elif is_mystery_or_fantasy:
                strategy = "AI_VIDEO"
                rationale = "Chủ đề viễn tưởng / kỳ ảo: Ưu tiên Video AI chuyển động điện ảnh, cấm dùng ảnh tĩnh."
            # Ưu tiên 5: Cảnh nghệ thuật tĩnh cho các chủ đề khác
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
