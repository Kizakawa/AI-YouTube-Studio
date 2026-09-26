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
    "gemini-3.1-flash-lite-preview",
    "gemini-flash-latest",
    "gemini-3.1-pro-preview",
]


class VideoStrategistAgent:
    """
    Tác nhân Đề Xuất Chiến Lược & Phương Pháp Làm Video (Video Production Strategist).
    Đóng vai trò Tổng Đạo Diễn Hình Ảnh (Director of Photography) trong tiền kỳ sản xuất.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None

    def _call_gemini(self, prompt: str, timeout: int = 25) -> Optional[str]:
        """Gọi Gemini REST API với KeyRotator và timeout an toàn."""
        if not self.rotator:
            return None

        import requests
        key = self.rotator.get_key()
        if not key:
            return None

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
                    key = self.rotator.get_key()
            except Exception as e:
                logger.debug(f"[VideoStrategist] Gemini call error on {model}: {e}")
                continue

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
        Ưu tiên AI Strategist thông minh, tự động dự phòng sang Heuristic Rules nếu mất kết nối.
        """
        # 1. Thử gọi AI Strategist
        ai_proposal = self._propose_with_ai(topic, scenes, primary_entity, entity_type, channel_niche)
        if ai_proposal and "scene_proposals" in ai_proposal and len(ai_proposal["scene_proposals"]) == len(scenes):
            logger.info("🎬 [VideoStrategist] ✅ Đã phê duyệt Bản Đề Xuất Chiến Lược Sản Xuất Video từ AI Strategist!")
            return ai_proposal

        # 2. Heuristic Rules Fallback
        logger.info("🎬 [VideoStrategist] Kích hoạt Heuristic Rule-Based Strategist Fallback...")
        return self._propose_with_heuristics(topic, scenes, primary_entity, entity_type, channel_niche)

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
Bạn là Tổng Đạo Diễn Kỹ Thuật & Chiến Lược Sản Xuất Video (Master Video Production Strategist & Director of Photography) cho YouTube Shorts hàng đầu thế giới.

BỐI CẢNH VẤN ĐỀ:
Người xem rất nhạy cảm với hình ảnh:
- Không phải lúc nào cũng chỉ là ảnh tĩnh chuyển cảnh đơn điệu.
- Cũng KHÔNG ĐƯỢC lạm dụng sinh video AI trực tiếp (Text-to-Video) bừa bãi vì Text-to-Video rất dễ làm méo mó khuôn mặt, dị dạng bàn tay, sai bối cảnh lịch sử.
- Vì vậy, một đạo diễn giỏi PHẢI BIẾT LÚC NÀO NÊN DÙNG CÁCH NÀO CHO PHÙ HỢP!

QUY TẮC PHÂN ĐỊNH 4 PHƯƠNG PHÁP LÀM VIDEO (BẮT BUỘC TUÂN THỦ):

1. 'REAL_ARCHIVE' (Tư liệu lịch sử thật 100%):
   - Áp dụng khi: Cảnh nhắc trực tiếp đến NHÂN VẬT LỊCH SỬ CÓ THẬT (Einstein, Tesla, Napoleon, danh nhân...), SỰ KIỆN LỊCH SỬ CÓ THẬT (Thế chiến, thảm họa Chernobyl...), CÔNG TRÌNH / DI TÍCH CỔ CÓ THẬT (Kim tự tháp Giza, Vạn lý trường thành, tượng Nhân sư...).
   - Công cụ: 'wikimedia_archive' / 'wikipedia'.
   - Lý do: Khán giả cần thấy bằng chứng và chân dung thật ngoài đời để tin tưởng. TUYỆT ĐỐI KHÔNG sinh AI giả mạo làm sai lệch lịch sử.

2. 'IMAGE_TO_VIDEO' (Tạo video chuyển động PHỤ THUỘC VÀO ẢNH THEO CHỦ ĐỀ):
   - Áp dụng khi: Cảnh miêu tả một nhân vật hư cấu cụ thể, bức tranh nghệ thuật sâu sắc, chân dung triết gia, hoặc bối cảnh kiến trúc rõ ràng CẦN ĐỘ SẮC NÉT VÀ ĐỘ CHÍNH XÁC CAO.
   - Cơ chế: Sinh bức ảnh 8K chuẩn bố cục trước (để khóa chặt tỷ lệ khuôn mặt và bối cảnh, không bị biến dạng), sau đó áp dụng hiệu ứng chuyển động camera 3D (Parallax, dynamic lighting, zoom-in có chiều sâu) để biến thành video MP4 sống động!
   - Công cụ: 'imagen3_i2v' (ImageAgent -> Motion Engine).
   - Lý do: Vừa đảm bảo video ĐỘNG, vừa KHÔNG BỊ BIẾN DẠNG như khi sinh video AI trực tiếp.

3. 'TEXT_TO_VIDEO' (Tạo video AI trực tiếp từ văn bản):
   - Áp dụng khi: Chủ đề hoặc phân cảnh mang tính HUYỀN BÍ, VŨ TRỤ TRỪU TƯỢNG (lỗ đen, chiều không gian thứ 4, cổng không gian, năng lượng tâm linh, khói bụi ma mị, sinh vật kỳ ảo) — những thứ KHÔNG CÓ HÌNH THÙ CỐ ĐỊNH NGOÀI ĐỜI.
   - Cơ chế: Sinh video chuyển đổi dynamic liên tục từ prompt.
   - Công cụ: 'wan_t2v' / 'ai_video_generative'.
   - Lý do: Các hiện tượng siêu thực cần sự biến ảo không ngừng của AI Video để tạo sự choáng ngợp.

4. 'STOCK_FOOTAGE' (Video clip B-Roll 9:16 chuyển động thực tế & viễn tưởng):
   - Áp dụng khi: Cảnh cần CHUYỂN ĐỘNG THỰC 30fps/60fps (thay vì ảnh tĩnh zoom Ken Burns).
   - Kho Pexels Video chứa hàng triệu video clip 9:16 chuyển động tuyệt đẹp:
     * Viễn tưởng / Sci-Fi / Không gian: Phi thuyền bay trong không gian (spaceship space), lỗ đen (black hole), dải ngân hà quay (galaxy stars), thành phố tương lai neon (cyberpunk city), robot/AI (robot android), cổng không gian (wormhole tunnel), công nghệ hologram (futuristic technology), địa hình hành tinh xa xôi (alien planet)...
     * Hành động / Đời thực: Sóng biển cuộn, bão tố, sấm chớp, thành phố đêm, xe cộ, dòng người, thao tác công nghệ, phòng thí nghiệm...
   - QUY TẮC BẮT BUỘC: Khi làm chủ đề VIỄN TƯỞNG, HUYỀN BÍ, KHOA HỌC, khán giả xem Shorts để thấy VIDEO ĐỘNG CHUYỂN ĐỘNG SỐNG ĐỘNG!
     TUYỆT ĐỐI TRÁNH việc biến toàn bộ video thành ảnh tĩnh chuyển cảnh (IMAGE_TO_VIDEO).
     PHẢI CHỈ ĐỊNH ÍT NHẤT 50% - 70% CÁC CẢNH DÙNG 'STOCK_FOOTAGE' (pexels_video_api) hoặc 'TEXT_TO_VIDEO' để đảm bảo video có CHUYỂN ĐỘNG ĐỘNG (DYNAMIC VIDEO) 30fps thực sự!
     Chỉ dùng 'IMAGE_TO_VIDEO' cho 1-2 cảnh khi cần đặc tả chân dung một nhân vật cụ thể hoặc sinh vật kỳ dị không thể tìm thấy video.

THÔNG TIN ĐẦU VÀO:
- Chủ đề: {topic}
- Nhân vật / Thực thể chính: {primary_entity or 'Không có nhân vật cụ thể'} (Loại: {entity_type})
- Thể loại kênh: {channel_niche}
- Các phân cảnh:
{json.dumps(scenes_context, ensure_ascii=False, indent=2)}

HÃY TRẢ VỀ JSON DUY NHẤT THEO SCHEMA:
{{
  "topic_classification": "HISTORICAL_DOCUMENTARY | MYSTERY_ABSTRACT | CINEMATIC_STORY | REAL_LIFE_MOTION",
  "director_overview": "Tầm nhìn nghệ thuật tổng thể (Màu sắc, phong cách, nhịp điệu)",
  "scene_proposals": [
    {{
      "scene_id": 1,
      "production_method": "REAL_ARCHIVE | IMAGE_TO_VIDEO | TEXT_TO_VIDEO | STOCK_FOOTAGE",
      "tool": "wikimedia_archive | imagen3_i2v | wan_t2v | pexels_video_api",
      "rationale": "Giải thích chi tiết 1 câu vì sao chọn phương pháp này cho cảnh này (bằng tiếng Việt)",
      "search_query": "Từ khóa tiếng Anh 2-3 từ ngắn gọn nếu là REAL_ARCHIVE hoặc STOCK_FOOTAGE",
      "ai_prompt": "Câu lệnh tiếng Anh chi tiết phong cách điện ảnh 8K 9:16 nếu là IMAGE_TO_VIDEO hoặc TEXT_TO_VIDEO",
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
            if "scene_proposals" in plan and isinstance(plan["scene_proposals"], list):
                # Chuẩn hóa để tương thích với các component đang dùng 'scenes_strategy'
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

        topic_class = "HISTORICAL_DOCUMENTARY" if is_historical else ("MYSTERY_ABSTRACT" if is_mystery_or_fantasy else "REAL_LIFE_MOTION")

        motion_cues = {
            "walk", "run", "running", "moving", "movement", "crowd", "people", "city", "street",
            "traffic", "night", "dark", "shadow", "silhouette", "rain", "storm", "fire", "water", "sea",
            "ocean", "wave", "waves", "underwater", "deep sea", "fish", "whale", "submarine", "radar",
            "dive", "diving", "swimming", "boat", "ship", "depth", "abyss", "clock", "time", "hourglass",
            "hands", "typing", "money", "cash", "brain", "mind", "looking", "staring", "whisper",
            "talking", "smoke", "candle", "door", "window", "alone", "sky", "clouds", "flight", "car", "nature",
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

            # ── 1. Xét chỉ định thủ công từ người dùng (nếu có) ──
            if user_pref == "ai_video":
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
            elif user_pref == "ai_image":
                method = "IMAGE_TO_VIDEO"
                tool = "imagen3_i2v"
                rationale = "Chỉ định thủ công: Tạo ảnh chủ đề 8K rồi chuyển hóa thành video động."

            # ── 2. Đạo Diễn Tự Quyết Định Tối Ưu (Auto Strategist) ──
            # Quy tắc 1: Thực thể lịch sử thật -> REAL_ARCHIVE
            elif primary_entity and entity_type in ("person", "civilization", "monument", "event", "historical_figure") and (idx in (0, 1) or primary_entity.lower() in text_all):
                method = "REAL_ARCHIVE"
                tool = "wikimedia_archive"
                rationale = f"Nhắc đến nhân vật/địa danh lịch sử có thật '{primary_entity}' -> dùng ảnh tư liệu thật 100% để đảm bảo tính xác thực."

            # Quy tắc 2: Hiện tượng huyền bí, vũ trụ siêu thực không có hình thái cố định -> TEXT_TO_VIDEO
            elif any(fc in text_all for fc in abstract_fantasy_cues) or (is_mystery_or_fantasy and idx in (1, 3)):
                method = "TEXT_TO_VIDEO"
                tool = "wan_t2v"
                rationale = "Ý tưởng huyền bí / năng lượng trừu tượng không có hình thể cố định -> sinh Video AI trực tiếp để tạo sự choáng ngợp."

            # Quy tắc 3: Chuyển động đời thực, bối cảnh viễn tưởng, không gian, b-roll động -> STOCK_FOOTAGE
            elif any(mc in text_all for mc in motion_cues) or idx == 0 or topic_class in ("REAL_LIFE_MOTION", "MYSTERY_ABSTRACT"):
                method = "STOCK_FOOTAGE"
                tool = "pexels_video_api"
                rationale = "Cảnh miêu tả chuyển động hoặc bối cảnh viễn tưởng/thực tế -> dùng Video B-Roll Pexels 9:16 giữ chân người xem bằng video động."

            # Quy tắc 4: Cảnh có nhân vật, chân dung cụ thể hoặc sinh vật đặc thù cần độ nét tuyệt đối -> IMAGE_TO_VIDEO
            elif any(c in text_all for c in ["portrait", "face", "philosopher", "chân dung", "khuôn mặt", "statue", "monk", "warrior", "creature", "monster", "leviathan"]):
                method = "IMAGE_TO_VIDEO"
                tool = "imagen3_i2v"
                rationale = "Cảnh cần đặc tả nhân vật/sinh vật cụ thể -> tạo ảnh chủ đề 8K chuẩn bố cục rồi chuyển hóa thành video 3D."

            # Quy tắc mặc định: Xen kẽ Video chuyển động thực và Video nghệ thuật để nhịp điệu sinh động
            else:
                method = "STOCK_FOOTAGE" if idx % 2 == 0 else "IMAGE_TO_VIDEO"
                tool = "pexels_video_api" if method == "STOCK_FOOTAGE" else "imagen3_i2v"
                rationale = "Cân bằng nhịp điệu video -> xen kẽ video chuyển động thực và nghệ thuật điện ảnh."

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
