"""
app.agents.video_strategist_agent — Tác nhân Đề Xuất Chiến Lược & Phương Pháp Sản Xuất Video (Video Production Strategist Agent).

Nhiệm vụ cốt lõi:
Phân tích toàn diện kịch bản, chủ đề, nhân vật và thể loại kênh để lập Bản Đề Xuất Kỹ Thuật Làm Video (Video Production Plan).
Chỉ định chính xác cho từng phân cảnh:
1. Phương pháp sản xuất tối ưu (production_method):
   - REAL_ARCHIVE   : Ảnh / tư liệu lưu trữ có thật 100% (nhân vật, di tích, sự kiện lịch sử).
   - IMAGE_TO_VIDEO : Sinh ảnh chủ đề 8K chuẩn trước, sau đó tạo video chuyển động phụ thuộc vào ảnh (để không méo mặt, chuẩn chi tiết).
   - TEXT_TO_VIDEO  : Sinh video AI trực tiếp (cho cảnh huyền bí, vũ trụ trừu tượng, năng lượng siêu thực).
   - STOCK_FOOTAGE  : Video clip B-roll chuyển động thực tế 9:16 (hành động đời thường, giữ chân người xem).
2. Công cụ thực thi (tool_selection):
   - wikimedia_archive / wikipedia
   - imagen3_i2v (ImageAgent + Cinematic Motion Engine)
   - wan_t2v (Generative Video AI)
   - pexels_video_api
3. Lời giải thích chiến lược (rationale):
   - Giải thích cặn kẽ tại sao cảnh này lại làm theo cách này để người dùng và hệ thống hiểu rõ.
"""
import json
import logging
from typing import Dict, Any, List, Optional
from config.settings import settings
from app.utils.key_rotator import KeyRotator

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
]


class VideoStrategistAgent:
    """
    Tác nhân Đề Xuất Chiến Lược & Phương Pháp Làm Video (Video Production Strategist).
    Đóng vai trò Tổng Đạo Diễn Hình Ảnh (Director of Photography) trong tiền kỳ sản xuất.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None

    def _call_gemini(self, prompt: str, timeout: int = 20) -> Optional[str]:
        """Gọi Gemini REST API với KeyRotator và timeout an toàn."""
        if not self.rotator:
            return None

        import requests
        total_keys = max(len(self.rotator.keys), 1)

        for attempt in range(total_keys * 2):
            key = self.rotator.get_key()
            if not key:
                return None

            key_failed = False
            for model in AVAILABLE_MODELS:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.35,
                        "responseMimeType": "application/json",
                    }
                }
                try:
                    res = requests.post(url, json=payload, timeout=timeout)
                    if res.status_code == 200:
                        data = res.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                            return text
                    elif res.status_code in (429, 403):
                        self.rotator.rotate_key()
                        key_failed = True
                        break
                except Exception as e:
                    logger.debug(f"[VideoStrategist] Gemini call error on {model}: {e}")
                    continue

            if not key_failed:
                self.rotator.rotate_key()

        return None

    def propose_production_strategy(
        self,
        topic: str,
        scenes: List[Dict[str, Any]],
        primary_entity: Optional[str] = None,
        entity_type: str = "general",
        channel_niche: str = "general",
    ) -> Dict[str, Any]:
        """
        Lập bản đề xuất chiến lược kỹ thuật sản xuất video cho từng cảnh.
        Tích hợp SciFiVisualDispatcherAgent:
        - Nhận diện chuyên sâu yếu tố Viễn tưởng / Vũ trụ / Cyberpunk / Đa vũ trụ.
        - Ép phương thức sản xuất thành TEXT_TO_VIDEO (AI Video) hoặc STOCK_FOOTAGE (Kho video 9:16).
        - Tuyệt đối ngăn chặn hiện tượng tạo mỗi ảnh tĩnh chuyển qua lại gây nhàm chán!
        """
        from app.agents.scifi_visual_dispatcher_agent import SciFiVisualDispatcherAgent
        scifi_agent = SciFiVisualDispatcherAgent()
        is_scifi_topic, conf, subgenre, scifi_kws = scifi_agent.detect_scifi_elements(
            topic=topic,
            channel_niche=channel_niche,
        )

        if is_scifi_topic:
            logger.info(
                f"🚀 [VideoStrategist] PHÁT HIỆN CHỦ ĐỀ VIỄN TƯỞNG ({subgenre}, Độ tin cậy: {conf:.2f}). "
                f"KÍCH HOẠT CHẾ ĐỘ ĐIỀU PHỐI ĐỘNG (ƯU TIÊN AI VIDEO & KHO VIDEO 9:16, CHỐNG SLIDESHOW ẢNH TĨNH)!"
            )

        # 1. Thử gọi AI Strategist
        ai_proposal = self._propose_with_ai(topic, scenes, primary_entity, entity_type, channel_niche)
        if ai_proposal and "scene_proposals" in ai_proposal and len(ai_proposal["scene_proposals"]) == len(scenes):
            logger.info("🎬 [VideoStrategist] ✅ Đã phê duyệt Bản Đề Xuất Chiến Lược Sản Xuất Video từ AI Strategist!")
            final_plan = ai_proposal
        else:
            # 2. Heuristic Rules Fallback
            logger.info("🎬 [VideoStrategist] Kích hoạt Heuristic Rule-Based Strategist Fallback...")
            final_plan = self._propose_with_heuristics(topic, scenes, primary_entity, entity_type, channel_niche)

        # 3. ÉP CHẶT ĐIỀU PHỐI VIỄN TƯỞNG CHO TOÀN BỘ PHÂN CẢNH (Anti-Slideshow Enforcement)
        if is_scifi_topic and final_plan:
            proposals = final_plan.get("scene_proposals") or []
            strategies = final_plan.get("scenes_strategy") or []

            for idx, sc_dict in enumerate(scenes):
                d_meta = scifi_agent.dispatch_scene_strategy(
                    scene=sc_dict,
                    topic=topic,
                    channel_niche=channel_niche,
                    idx=idx,
                    total_scenes=len(scenes),
                )
                if idx < len(proposals):
                    prop = proposals[idx]
                    # Nếu đề xuất cũ là ảnh tĩnh IMAGE_TO_VIDEO, nâng cấp ngay sang AI Video hoặc Kho Video Clip
                    if prop.get("production_method") in ("IMAGE_TO_VIDEO", "REAL_ARCHIVE"):
                        prop["production_method"] = d_meta["production_method"]
                        prop["tool"] = d_meta["tool"]
                        prop["rationale"] = d_meta["rationale"]
                    prop["search_queries"] = d_meta["search_queries"]
                    prop["search_query"] = d_meta["search_query"]
                    prop["ai_prompt"] = d_meta["ai_prompt"]
                    prop["camera_motion"] = d_meta.get("camera_motion", "zoom_in")
                    prop["sfx_cue"] = d_meta.get("sfx_cue", "whoosh_fast")
                    prop["is_scifi"] = True
                    prop["reject_static_slideshow"] = True

                if idx < len(strategies):
                    strat = strategies[idx]
                    if strat.get("production_method") in ("IMAGE_TO_VIDEO", "REAL_ARCHIVE"):
                        strat["production_method"] = d_meta["production_method"]
                        strat["strategy"] = "AI_VIDEO" if d_meta["production_method"] == "TEXT_TO_VIDEO" else "STOCK_VIDEO"
                        strat["tool"] = d_meta["tool"]
                        strat["rationale"] = d_meta["rationale"]
                    strat["search_queries"] = d_meta["search_queries"]
                    strat["search_query"] = d_meta["search_query"]
                    strat["ai_prompt"] = d_meta["ai_prompt"]
                    strat["camera_motion"] = d_meta.get("camera_motion", "zoom_in")
                    strat["sfx_cue"] = d_meta.get("sfx_cue", "whoosh_fast")
                    strat["is_scifi"] = True
                    strat["reject_static_slideshow"] = True

            final_plan["is_scifi_video"] = True
            final_plan["scifi_subgenre"] = subgenre

        return final_plan

    def _propose_with_ai(
        self,
        topic: str,
        scenes: List[Dict[str, Any]],
        primary_entity: Optional[str],
        entity_type: str,
        channel_niche: str,
    ) -> Optional[Dict[str, Any]]:
        """Sử dụng Gemini phân tích chuyên sâu từng cảnh theo 4 phương pháp cốt lõi."""
        scenes_context = []
        for s in scenes:
            scenes_context.append({
                "scene_id": s.get("scene_id"),
                "narration": s.get("narration"),
                "visual_keyword": s.get("visual_keyword"),
                "visual_prompt": s.get("visual_prompt"),
                "media_type": s.get("media_type", "auto"),
            })

        system_prompt = f"""
Bạn là Tổng Đạo Diễn Kỹ Thuật & Kiến Trúc Sư Thị Giác (Master Video Production Strategist & Director of Photography) cho YouTube Shorts hàng đầu thế giới (chuẩn Vox, Magnates Media, Kurzgesagt, Melodysheep).

TRIẾT LÝ NGHỆ THUẬT: ĐAN XEN ĐỈNH CAO GIỮA VIDEO ĐỘNG VÀ NGHỆ THUẬT AI (CINEMATIC WEAVING):
Người xem YouTube Shorts lướt qua cực nhanh (Swipe-away < 2s). Một video thuần ghép ảnh tĩnh nhạt nhẽo sẽ mất tương tác, nhưng một video lạm dụng stock footage đại trà, lặp đi lặp lại cũng sẽ khiến người xem nhàm chán!
Đạo diễn xuất sắc là người biết:
1. Đan xen chuẩn xác giữa VIDEO CHUYỂN ĐỘNG THỰC (B-Roll 60fps), VIDEO TỪ ẢNH CHỦ ĐỀ AI (Image-to-Video 3D Parallax 8K), VIDEO AI TRỰC TIẾP (Text-to-Video), và TƯ LIỆU LỊCH SỬ THẬT.
2. Với các chủ đề TÂM LÝ HỌC (Psychology), KHẮC KỶ (Stoicism), THAO TÚNG (Dark Psychology), BÍ ẨN (Mysteries), TRIẾT HỌC: Kho stock video thường rất hạn chế, dễ trùng lặp và không thể hiện được chiều sâu ẩn dụ -> BẮT BUỘC ƯU TIÊN 'IMAGE_TO_VIDEO' (Sinh ảnh 8K Flux/Imagen với ánh sáng Chiaroscuro siêu thực, sau đó chuyển động 3D Camera / Parallax) chiếm 50% - 75% số cảnh!

BẢN ĐỒ LỰA CHỌN CÔNG CỤ & Ý TƯỞNG (QUY TẮC BẮT BUỘC):

1. 'IMAGE_TO_VIDEO' (Công cụ: 'imagen3_i2v' - Tạo ảnh 8K chuẩn trước, sau đó chuyển động 3D Camera / Parallax):
   - Khi nào dùng:
     * ĐẶC BIỆT ƯU TIÊN cho các chủ đề Tâm lý học, Khắc kỷ, Thao túng, Triết học, Bí ẩn, Khoa học viễn tưởng.
     * Cảnh cần ĐẶC TẢ CHI TIẾT CHÂN THỰC, ÁNH MẮT, KHUÔN MẶT, BIỂU CẢM, TƯỢNG ĐÁ KHẮC KỶ, KHÔNG GIAN TÂM TRÍ, ẨN DỤ TÂM LÝ mà stock footage không có hoặc rất giả tạo.
     * Cảnh Cú sốc thị giác Hook (Scene 1) mang tính tâm lý / chân dung sắc lạnh với cú zoom-in 3D nghẹt thở.

2. 'STOCK_FOOTAGE' (Công cụ: 'pexels_video_api' - Video clip B-Roll 9:16 chuyển động thực 30-60fps):
   - Khi nào dùng: Cảnh có HÀNH ĐỘNG VẬT LÝ THỰC TẾ (bước đi trong mưa, gõ phím, lật trang sách, đếm tiền, uống cà phê), KHÔNG KHÍ ĐÔ THỊ (dòng người hối hả, xe cộ đêm neon), ĐỘNG VẬT HOANG DÃ (săn mồi, chim bay), THIÊN NHIÊN DỮ DỘI (sóng biển cuộn, sấm chớp, bão cát, lửa cháy, khói sương bốc lên), BIỂU TƯỢNG VẬT LÝ (đồng hồ cát rơi, đồng hồ kim tích tắc, con lắc, bàn cờ vua đổ).

3. 'TEXT_TO_VIDEO' (Công cụ: 'wan_t2v' / 'ai_video_generative' - Sinh Video AI chuyển động trực tiếp):
   - Khi nào dùng: Cảnh mang tính HUYỀN BÍ, VŨ TRỤ TRỪU TƯỢNG, NĂNG LƯỢNG SIÊU THỰC (lỗ đen bẻ cong không thời gian, cổng không gian ánh sáng, luồng năng lượng tâm linh, khói ma mị biến hình, vũ trụ 4D, nơ-ron thần kinh phát sáng, hố sâu tâm lý vô tận).

4. 'REAL_ARCHIVE' (Công cụ: 'wikimedia_archive' / 'wikipedia' - Ảnh tư liệu lịch sử có thật 100%):
   - Khi nào dùng: Nhắc trực tiếp đến NHÂN VẬT LỊCH SỬ CÓ THẬT (Einstein, Tesla, Napoleon, Julius Caesar...), SỰ KIỆN LỊCH SỬ (Thế chiến, Apollo 11, sụp đổ La Mã...), DI TÍCH LỊCH SỬ CỔ ĐẠI (Kim Tự Tháp Giza, Đấu trường Colosseum).

QUY TẮC ĐAN XEN NHỊP ĐIỆU (RHYTHMIC WEAVING):
- Với chủ đề Động vật hoang dã / Đời sống thực tế: 70% - 90% là STOCK_FOOTAGE.
- Với chủ đề Tâm lý học, Khắc kỷ, Tư duy, Bí ẩn: 50% - 75% là IMAGE_TO_VIDEO (8K AI + 3D motion), đan xen nhịp nhàng với STOCK_FOOTAGE ở các cảnh hành động thực tế.
- Đảm bảo mọi cảnh dù dùng ảnh AI cũng được chuyển hóa thành video động (3D Zoom / Pan / Parallax), không có ảnh tĩnh đứng yên!

THÔNG TIN ĐẦU VÀO:
- Chủ đề: {topic}
- Nhân vật / Thực thể chính: {primary_entity or 'Không có nhân vật cụ thể'} (Loại: {entity_type})
- Thể loại kênh: {channel_niche}
- Các phân cảnh:
{json.dumps(scenes_context, ensure_ascii=False, indent=2)}

HÃY TRẢ VỀ JSON DUY NHẤT THEO SCHEMA:
{{
  "topic_classification": "HISTORICAL_DOCUMENTARY | MYSTERY_ABSTRACT | CINEMATIC_STORY | REAL_LIFE_MOTION",
  "director_overview": "Tầm nhìn nghệ thuật tổng thể (Màu sắc, phong cách, nhịp điệu đan xen)",
  "scene_proposals": [
    {{
      "scene_id": 1,
      "production_method": "REAL_ARCHIVE | IMAGE_TO_VIDEO | TEXT_TO_VIDEO | STOCK_FOOTAGE",
      "tool": "wikimedia_archive | imagen3_i2v | wan_t2v | pexels_video_api",
      "rationale": "Giải thích chi tiết 1 câu vì sao chọn phương pháp & công cụ này cho cảnh này (bằng tiếng Việt)",
      "search_query": "Từ khóa tiếng Anh 2-3 từ ngắn gọn tốt nhất để tìm tư liệu nếu là REAL_ARCHIVE hoặc STOCK_FOOTAGE",
      "ai_prompt": "Câu lệnh tiếng Anh chi tiết phong cách điện ảnh 8K 9:16 nếu là IMAGE_TO_VIDEO hoặc TEXT_TO_VIDEO",
      "camera_motion": "zoom_in | zoom_out | pan_left | pan_right | pan_up | pan_down",
      "sfx_cue": "whoosh | hit | tension_riser | cinematic_swell | heartbeat"
    }}
  ]
}}"""
        raw = self._call_gemini(system_prompt)
        if not raw:
            return None

        try:
            plan = json.loads(raw)
            if "scene_proposals" in plan and isinstance(plan["scene_proposals"], list):
                for p in plan["scene_proposals"]:
                    sq = p.get("search_query") or "cinematic"
                    ap = p.get("ai_prompt") or f"Cinematic 8k dramatic vertical shot of {sq}, dramatic lighting, 9:16"
                    p["search_query"] = sq
                    p["ai_prompt"] = ap

                plan["scenes_strategy"] = [
                    {
                        "scene_id": p.get("scene_id"),
                        "strategy": self._map_method_to_legacy_strategy(p.get("production_method")),
                        "production_method": p.get("production_method"),
                        "tool": p.get("tool"),
                        "rationale": p.get("rationale"),
                        "search_query": p.get("search_query"),
                        "ai_prompt": p.get("ai_prompt"),
                        "camera_motion": p.get("camera_motion", "zoom_in"),
                        "sfx_cue": p.get("sfx_cue", "whoosh"),
                    }
                    for p in plan["scene_proposals"]
                ]
                return plan
        except Exception as e:
            logger.warning(f"[VideoStrategist] Lỗi parse JSON từ Gemini: {e}")

        return None

    def _propose_with_heuristics(
        self,
        topic: str,
        scenes: List[Dict[str, Any]],
        primary_entity: Optional[str],
        entity_type: str,
        channel_niche: str,
    ) -> Dict[str, Any]:
        """Tự động phân tích và ra quyết định bằng bộ quy tắc nghiệp vụ chặt chẽ."""
        topic_lower = topic.lower()
        is_mystery_or_fantasy = any(w in topic_lower for w in [
            "mystery", "mysteries", "secret", "alien", "ufo", "space", "galaxy", "supernatural",
            "paranormal", "magic", "fantasy", "myth", "monster", "ghost", "dimension", "portal",
            "time travel", "future", "huyền bí", "bí ẩn", "vũ trụ", "tâm linh", "giả tưởng", "kỳ lạ", "lỗ đen",
            "viễn tưởng", "khoa học viễn tưởng", "sci-fi", "scifi", "science fiction", "cyberpunk",
            "futuristic", "robot", "người máy", "trí tuệ nhân tạo", "ai", "không gian", "ngân hà", "hố đen",
            "ngoài hành tinh", "phi thuyền", "hành tinh", "chiến tranh giữa các vì sao", "vũ trụ quan"
        ])

        is_historical = (bool(primary_entity) and entity_type in ("person", "civilization", "monument", "event", "historical_figure")) or any(w in topic_lower for w in [
            "history", "war", "battle", "emperor", "king", "ancient", "century", "empire",
            "lịch sử", "thế chiến", "hoàng đế", "vua", "cổ đại", "triều đại", "di tích"
        ])

        is_psychology_or_philosophy = any(w in topic_lower or channel_niche.lower() for w in [
            "psychology", "stoic", "stoicism", "mindset", "tâm lý", "khắc kỷ", "triết lý", "thao túng",
            "manipulation", "dark psychology", "tư duy", "triết học", "nghĩ", "bản ngã", "ego", "focus",
            "human behavior", "psychological", "habits", "mental", "thói quen"
        ])

        topic_class = "HISTORICAL_DOCUMENTARY" if is_historical else (
            "MYSTERY_ABSTRACT" if is_mystery_or_fantasy else (
                "CINEMATIC_STORY" if is_psychology_or_philosophy else "REAL_LIFE_MOTION"
            )
        )

        motion_cues = {
            "walk", "run", "running", "moving", "movement", "crowd", "people", "street",
            "traffic", "rain", "storm", "fire", "water", "sea",
            "ocean", "wave", "waves", "underwater", "deep sea", "fish", "whale", "submarine", "radar",
            "dive", "diving", "swimming", "boat", "ship", "depth", "abyss", "clock", "time", "hourglass",
            "typing", "money", "cash",
            "door", "window", "sky", "clouds", "flight", "car", "nature",
            # Sci-Fi / Không gian / Viễn tưởng B-Roll
            "spaceship", "space", "galaxy", "stars", "planet", "alien planet", "wormhole", "black hole",
            "cyberpunk", "neon", "futuristic", "robot", "android", "laser", "portal", "orbit", "cosmos",
            "technology", "hologram", "warp", "speed", "flight", "flying", "universe", "nebula",
            "phi thuyền", "ngân hà", "hành tinh", "vũ trụ", "lỗ đen", "công nghệ", "viễn tưởng"
        }

        abstract_fantasy_cues = {
            "energy", "glowing", "glow", "cosmic", "universe", "alien", "portal", "dimensional",
            "supernatural", "secret", "magic", "spirit", "floating", "beam", "ray", "power",
            "mystic", "huyền bí", "phép thuật", "năng lượng", "lỗ đen", "vũ trụ", "vô tận"
        }

        psychology_ai_cues = {
            "portrait", "face", "philosopher", "chân dung", "khuôn mặt", "statue", "monk", "warrior",
            "creature", "monster", "leviathan", "eye", "eyes", "stare", "gaze", "whisper", "shadow",
            "silhouette", "alone", "mind", "brain", "thought", "mask", "manipulation", "reflection",
            "mirror", "stoic", "dark", "ego", "fear", "tension", "interrogation", "unblinking",
            "gaslighting", "soul", "subconscious", "thao túng", "tâm trí", "ảo giác"
        }

        motions_list = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"]
        scene_proposals = []
        scenes_strategy = []

        for idx, sc in enumerate(scenes):
            sc_id = sc.get("scene_id", idx + 1)
            narr = (sc.get("narration") or "").lower()
            kw = (sc.get("visual_keyword") or "").lower()
            vp = sc.get("visual_prompt") or ""
            sq = sc.get("stock_query") or kw
            text_all = f"{narr} {kw} {sq}".lower()

            user_pref = sc.get("media_type")

            # ── 1. Xét chỉ định thủ công từ người dùng hoặc kịch bản (nếu có) ──
            if user_pref == "ai_image":
                method = "IMAGE_TO_VIDEO"
                tool = "imagen3_i2v"
                rationale = "Chỉ định kịch bản / Người dùng: Tạo ảnh nghệ thuật 8K siêu nét rồi chuyển hóa thành video 3D."
            elif user_pref == "ai_video":
                method = "TEXT_TO_VIDEO"
                tool = "wan_t2v"
                rationale = "Chỉ định thủ công: Sinh Video AI trực tiếp cho cảnh này."
            elif user_pref == "video":
                method = "STOCK_FOOTAGE"
                tool = "pexels_video_api"
                rationale = "Chỉ định thủ công: Dùng Video B-Roll chuyển động 9:16."
            elif user_pref in ("photo", "documentary"):
                if primary_entity and entity_type in ("person", "civilization", "monument", "event", "historical_figure"):
                    method = "REAL_ARCHIVE"
                    tool = "wikimedia_archive"
                    rationale = "Chỉ định thủ công: Tìm ảnh tư liệu lịch sử thực tế."
                else:
                    method = "STOCK_FOOTAGE"
                    tool = "pexels_photo_api"
                    rationale = "Chỉ định thủ công: Tìm ảnh chụp chất lượng cao (Pexels Photo)."

            # ── 2. Đạo Diễn Tự Quyết Định Tối Ưu (Auto Strategist & Dynamic Weaving) ──
            # Quy tắc 1: Thực thể lịch sử thật (từ Cảnh 2 trở đi hoặc khi nhắc đích danh) -> REAL_ARCHIVE
            elif primary_entity and entity_type in ("person", "civilization", "monument", "event", "historical_figure") and (idx == 1 or primary_entity.lower() in text_all):
                method = "REAL_ARCHIVE"
                tool = "wikimedia_archive"
                rationale = f"Nhắc đến nhân vật/địa danh lịch sử có thật '{primary_entity}' -> dùng ảnh tư liệu thật 100% để đảm bảo tính xác thực."

            # Quy tắc 2: Hiện tượng huyền bí, vũ trụ siêu thực không có hình thái cố định -> TEXT_TO_VIDEO
            elif any(fc in text_all for fc in abstract_fantasy_cues) or (is_mystery_or_fantasy and idx in (2, 3)):
                method = "TEXT_TO_VIDEO"
                tool = "wan_t2v"
                rationale = "Ý tưởng huyền bí / năng lượng trừu tượng không có hình thể cố định -> sinh Video AI trực tiếp để tạo sự choáng ngợp."

            # Quy tắc 3: Chủ đề Tâm lý học / Khắc kỷ / Bí ẩn / Triết học -> BẮT BUỘC ƯU TIÊN IMAGE_TO_VIDEO (8K AI + 3D Camera)
            elif is_psychology_or_philosophy and (idx == 0 or any(pc in text_all for pc in psychology_ai_cues)):
                method = "IMAGE_TO_VIDEO"
                tool = "imagen3_i2v"
                rationale = "Chủ đề Tâm lý / Khắc kỷ: Tạo ảnh nghệ thuật 8K Chiaroscuro siêu nét theo chuẩn phong cách kênh, chuyển hóa thành video 3D Parallax mượt mà."

            # Cảnh 1 Hook cho các chủ đề khác: Video B-Roll chuyển động mạnh để giữ chân khán giả
            elif idx == 0:
                method = "STOCK_FOOTAGE"
                tool = "pexels_video_api"
                rationale = "Cảnh 1 (Primal Hook): Bắt buộc dùng Video B-Roll chuyển động 9:16 để giật sự chú ý, chống lướt qua."

            # Quy tắc 4: Cảnh có nhân vật, chân dung cụ thể hoặc sinh vật đặc thù cần độ nét tuyệt đối -> IMAGE_TO_VIDEO
            elif any(c in text_all for c in ["portrait", "face", "philosopher", "chân dung", "khuôn mặt", "statue", "monk", "warrior", "creature", "monster", "leviathan"]):
                method = "IMAGE_TO_VIDEO"
                tool = "imagen3_i2v"
                rationale = "Cảnh cần đặc tả nhân vật/sinh vật cụ thể -> tạo ảnh chủ đề 8K chuẩn bố cục rồi chuyển hóa thành video 3D."

            # Quy tắc 5: Chuyển động đời thực, bối cảnh viễn tưởng, không gian, b-roll động -> STOCK_FOOTAGE
            elif any(mc in text_all for mc in motion_cues):
                method = "STOCK_FOOTAGE"
                tool = "pexels_video_api"
                rationale = "Cảnh miêu tả chuyển động hoặc bối cảnh đời thực -> dùng Video B-Roll Pexels 9:16 giữ chân người xem bằng video động."

            # Quy tắc mặc định: Đan xen nhịp nhàng giữa Video Động và Ảnh AI Chuyển động 3D
            else:
                prev_method = scene_proposals[-1]["production_method"] if scene_proposals else ""
                if is_psychology_or_philosophy:
                    # Tâm lý học: 70% IMAGE_TO_VIDEO, 30% STOCK_FOOTAGE
                    method = "STOCK_FOOTAGE" if (prev_method == "IMAGE_TO_VIDEO" and idx % 3 == 0) else "IMAGE_TO_VIDEO"
                    tool = "pexels_video_api" if method == "STOCK_FOOTAGE" else "imagen3_i2v"
                    rationale = "Đan xen nhịp nhàng: Giữ tính nghệ thuật 8K AI độc bản kết hợp B-roll thực tế."
                elif prev_method in ("IMAGE_TO_VIDEO", "REAL_ARCHIVE"):
                    # Nếu cảnh trước đã là ảnh tĩnh/I2V -> Cảnh này chuyển sang Video động
                    method = "STOCK_FOOTAGE"
                    tool = "pexels_video_api"
                    rationale = "Đan xen nhịp điệu: Sau cảnh tĩnh, chuyển sang Video B-roll động 9:16 để video thở và giữ nhịp."
                else:
                    method = "IMAGE_TO_VIDEO" if (idx % 3 == 1) else "STOCK_FOOTAGE"
                    tool = "imagen3_i2v" if method == "IMAGE_TO_VIDEO" else "pexels_video_api"
                    rationale = "Đảm bảo video chuyển động sống động (Full Video 9:16) kết hợp chuyển động camera mượt mà."

            cam_motion = motions_list[idx % len(motions_list)]
            sfx = "whoosh" if idx == 0 else "hit"
            legacy_strat = self._map_method_to_legacy_strategy(method)

            item = {
                "scene_id": sc_id,
                "production_method": method,
                "tool": tool,
                "rationale": rationale,
                "search_query": sq or kw or "cinematic",
                "ai_prompt": vp or f"Cinematic 8k vertical shot of {kw or 'scene'}, highly detailed, dramatic lighting, 9:16",
                "camera_motion": cam_motion,
                "sfx_cue": sfx
            }
            scene_proposals.append(item)

            scenes_strategy.append({
                "scene_id": sc_id,
                "strategy": legacy_strat,
                "production_method": method,
                "tool": tool,
                "rationale": rationale,
                "search_query": item["search_query"],
                "ai_prompt": item["ai_prompt"],
                "camera_motion": cam_motion,
                "sfx_cue": sfx
            })

        return {
            "topic_classification": topic_class,
            "director_overview": f"Chiến lược sản xuất đa phương pháp ({topic_class}): Cân bằng hoàn hảo giữa tư liệu thật, video B-roll, video từ ảnh chủ đề (I2V) và video AI trực tiếp (T2V).",
            "scene_proposals": scene_proposals,
            "scenes_strategy": scenes_strategy,
        }

    @staticmethod
    def _map_method_to_legacy_strategy(method: str) -> str:
        """Map từ 4 production methods sang các strategy tương thích."""
        mapping = {
            "REAL_ARCHIVE": "REAL_DOCUMENTARY",
            "IMAGE_TO_VIDEO": "AI_VIDEO",
            "TEXT_TO_VIDEO": "AI_VIDEO",
            "STOCK_FOOTAGE": "STOCK_VIDEO",
        }
        return mapping.get(method, "STOCK_VIDEO")
