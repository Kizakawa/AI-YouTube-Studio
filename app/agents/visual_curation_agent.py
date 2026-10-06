"""
app.agents.visual_curation_agent — Tác nhân Phân tích Ngữ cảnh & Tuyển chọn Thị giác Thông minh (Smart Visual Curation Agent).

Nhiệm vụ cốt lõi:
1. Phân tích ngữ cảnh & ngụ ý sâu sắc (Narrative Subtext & Context Analysis):
   - Không dựa vào từ khóa bề mặt (literal keyword matching) máy móc.
   - Hiểu rõ tâm trạng (mood), ẩn dụ điện ảnh (visual metaphor) và thông điệp thực sự của phân cảnh.
   - Xác định phương thức sản xuất tối ưu (STOCK_FOOTAGE, TEXT_TO_VIDEO, IMAGE_TO_VIDEO, REAL_ARCHIVE).
   - Đưa ra 3-4 câu lệnh tìm kiếm chuyên sâu (search_queries) và các yếu tố cấm kỵ (negative_cues).
   - Chuẩn bị sẵn prompt AI 9:16 điện ảnh (cinematic_ai_prompt) phòng khi tư liệu thực tế không đáp ứng.

2. Đánh giá & Tuyển chọn ứng viên tư liệu (Candidate Curation & Decision Making):
   - Đọc tiêu đề, mô tả và url slug của các video/ảnh stock tìm được từ thư viện (Pexels, Wikimedia...).
   - Agent trực tiếp chấm điểm độ phù hợp (0.0 - 10.0) và LỰA CHỌN ứng viên xuất sắc nhất.
   - Nếu TẤT CẢ ứng viên đều không đạt chuẩn (< 6.0/10 hoặc sai lệch hoàn toàn tinh thần cảnh quay):
     Agent dứt khoát TỪ CHỐI toàn bộ stock và kích hoạt chuyển sang tạo Video AI / Ảnh AI trực tiếp!
"""
import json
import logging
import re
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

from config.settings import settings
from app.utils.key_rotator import KeyRotator

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = [
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
]


class VisualCurationAgent:
    """
    Tác nhân Tuyển chọn & Giám sát Thị giác Thông minh (Smart Visual Curation Agent).
    Đóng vai trò Trợ lý Đạo diễn Tuyển chọn Tư liệu (Visual Researcher & Curation Director).
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None

    def _call_gemini_json(
        self,
        prompt: str,
        timeout: int = 15,
        images: Optional[List[Tuple[str, str]]] = None,
        models: Optional[List[str]] = None,
        max_attempts: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Gọi Gemini API trả về JSON với KeyRotator và fallback an toàn.
        images: danh sách (mime_type, base64_data) để gửi kèm (Gemini Vision).
        """
        if not self.rotator:
            return None

        import requests
        total_keys = max(len(self.rotator.keys), 1)

        parts: List[Dict[str, Any]] = [{"text": prompt}]
        for mime, b64 in (images or []):
            parts.append({"inline_data": {"mime_type": mime, "data": b64}})

        for attempt in range(max_attempts or total_keys * 2):
            key = self.rotator.get_key()
            if not key:
                return None

            key_failed = False
            for model in (models or AVAILABLE_MODELS):
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                payload = {
                    "contents": [{"parts": parts}],
                    "generationConfig": {
                        "temperature": 0.2,
                        "responseMimeType": "application/json",
                    }
                }
                try:
                    res = requests.post(url, json=payload, timeout=timeout)
                    if res.status_code == 200:
                        data = res.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            raw_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                            if raw_text:
                                return json.loads(raw_text)
                    elif res.status_code in (429, 403):
                        self.rotator.rotate_key()
                        key_failed = True
                        break
                except Exception as e:
                    logger.debug(f"[VisualCurationAgent] Gemini call error on {model}: {e}")
                    continue

            if not key_failed:
                self.rotator.rotate_key()

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # PHASE 1: PHÂN TÍCH NGỮ CẢNH & Ý NIỆM THỊ GIÁC (CONTEXTUAL SCENE ANALYSIS)
    # ──────────────────────────────────────────────────────────────────────────

    def analyze_scene_context(
        self,
        narration: str,
        topic: str = "",
        scene_id: int = 1,
        total_scenes: int = 1,
        primary_entity: Optional[str] = None,
        entity_type: str = "general",
        surrounding_context: str = "",
        channel_niche: str = "general",
    ) -> Dict[str, Any]:
        """
        Phân tích chuyên sâu ngữ cảnh và cảm xúc của phân cảnh:
        - Đọc hiểu hàm ý (subtext), không dịch thô từ khóa bề mặt.
        - Đánh giá hoàn cảnh: lúc nào nên dùng Full Video chuyển động thực, lúc nào tạo Full Video bằng AI, lúc nào ghép ảnh tĩnh nghệ thuật.
        - Tạo 3-4 truy vấn tìm kiếm chuyên biệt cho stock video (2-3 từ tiếng Anh).
        - Tạo prompt AI 9:16 sắc nét để sẵn sàng fallback.
        """
        prompt = f"""
Bạn là Tổng Đạo Diễn Nghệ Thuật & Giám Sát Thị Giác (Cinematographer & Visual Curation Director) cho YouTube Shorts hàng đầu.

NHIỆM VỤ CỐT LÕI:
Đánh giá hoàn cảnh của phân cảnh để LỰA CHỌN PHƯƠNG PHÁP & CÔNG CỤ SẢN XUẤT THỊ GIÁC TỐI ƯU NHẤT:
Khán giả xem Shorts đòi hỏi VIDEO ĐỘNG CHUYỂN ĐỘNG SỐNG ĐỘNG (FULL VIDEO), KHÔNG ĐƯỢC để video thành "thuần ghép ảnh tĩnh chuyển cảnh" đơn điệu. Tuy nhiên, cũng phải linh động theo hoàn cảnh:

QUY TẮC ĐÁNH GIÁ CÔNG CỤ THEO HOÀN CẢNH:
1. 'STOCK_FOOTAGE' (Công cụ: pexels_video_clip - Full Video chuyển động thực 30fps/60fps 9:16):
   - Áp dụng khi: Cảnh miêu tả HÀNH ĐỘNG CON NGƯỜI, cử chỉ, biểu cảm vi mô (ánh mắt căng thẳng, thì thầm, bước đi trong mưa/bóng tối, gõ phím, thao tác, nhìn gương, đơn độc trong quán cà phê), hoặc BỐI CẢNH THỰC TẾ/THIÊN NHIÊN (sóng biển gầm, mây giông cuộn, thành phố đêm neon, khói thuốc bay, đồng hồ đếm ngược).
   - Hook (Cảnh 1) BẮT BUỘC ưu tiên Full Video để giữ chân khán giả (>70% tỷ lệ giữ chân).

2. 'TEXT_TO_VIDEO' (Công cụ: ai_text_to_video - Full Video do AI sinh trực tiếp):
   - Áp dụng khi: Cảnh mang tính HUYỀN BÍ, VŨ TRỤ TRỪU TƯỢNG, SIÊU THỰC (lỗ đen bẻ cong không thời gian, cổng không gian ánh sáng, luồng năng lượng tâm linh, khói ma mị biến hình, hố sâu tâm lý vô tận) — những thứ KHÔNG CÓ THẬT ngoài đời thực.

3. 'IMAGE_TO_VIDEO' (Công cụ: ai_image_to_video - Tạo ảnh AI 8K rồi chuyển động 3D Ken Burns):
   - Áp dụng khi: Cảnh cần ĐẶC TẢ CHI TIẾT TĨNH CHÂN THỰC TUYỆT ĐỐI (chân dung 1 triết gia giả tưởng, sinh vật huyền thoại có khuôn mặt cố định, cổ vật huyền bí, bản đồ cổ) mà nếu sinh video AI trực tiếp sẽ bị méo mặt hoặc dị dạng chi tiết.

4. 'REAL_ARCHIVE' (Công cụ: real_archive_photo - Ảnh tư liệu lịch sử thật 100%):
   - Áp dụng khi: Nhắc trực tiếp đến NHÂN VẬT LỊCH SỬ CÓ THẬT (Einstein, Tesla, Napoleon...), SỰ KIỆN LỊCH SỬ (Thế chiến, Apollo...), DI TÍCH LỊCH SỬ CỔ ĐẠI (Kim Tự Tháp, Vạn Lý Trường Thành). Cần bằng chứng lịch sử xác thực, cấm dùng AI giả mạo.

TỶ LỆ VÀNG CHO VIDEO VIRAL (KHOA HỌC / TÂM LÝ / BÍ ẨN):
- Ít nhất 60% - 75% các cảnh phải là FULL VIDEO CHUYỂN ĐỘNG (STOCK_FOOTAGE hoặc TEXT_TO_VIDEO).
- Tối đa 25% - 40% là GHÉP ẢNH TĨNH / TƯ LIỆU THẬT (REAL_ARCHIVE hoặc IMAGE_TO_VIDEO) đúng chỗ cần tĩnh lặng hoặc chân dung.

THÔNG TIN ĐẦU VÀO:
- Chủ đề video: "{topic}"
- Niche kênh: "{channel_niche}"
- Phân cảnh số: {scene_id}/{total_scenes}
- Thực thể chính (nếu có): "{primary_entity or 'Không có'}" (Loại: {entity_type})
- Lời thoại phân cảnh: "{narration}"
- Ngữ cảnh xung quanh: "{surrounding_context}"

HÃY PHÂN TÍCH VÀ TRẢ VỀ JSON DUY NHẤT:
{{
  "mood": "Tâm trạng chủ đạo (dark_ominous | tense_mysterious | epic_heroic | vibrant_energetic | melancholy_thoughtful | scientific_curiosity)",
  "narrative_subtext": "Ngụ ý và cảm xúc sâu xa mà phân cảnh này muốn truyền tải (1-2 câu tiếng Việt)",
  "visual_metaphor": "Mô tả bối cảnh điện ảnh cụ thể mà khán giả nên nhìn thấy trên màn hình 9:16 (tiếng Anh & tiếng Việt)",
  "production_method": "STOCK_FOOTAGE | TEXT_TO_VIDEO | IMAGE_TO_VIDEO | REAL_ARCHIVE",
  "tool_choice": "pexels_video_clip | ai_text_to_video | ai_image_to_video | real_archive_photo",
  "motion_level": "HIGH_MOTION | MEDIUM_MOTION | STATIC_DETAILED",
  "search_queries": [
    "Truy vấn tiếng Anh 2-3 từ cụ thể cho thư viện video stock (vd: deep ocean abyss)",
    "Truy vấn thay thế góc nhìn 2 (vd: dark underwater trench)",
    "Truy vấn thay thế góc nhìn 3 (vd: gloomy water depth)"
  ],
  "negative_cues": [
    "Từ/khái niệm cấm không được xuất hiện (vd: swimming pool, sunny beach, smiling corporate people, modern office)"
  ],
  "cinematic_ai_prompt": "Câu lệnh tiếng Anh chi tiết phong cách điện ảnh 8K tỷ lệ 9:16 dùng để sinh Video AI hoặc Ảnh AI nếu kho stock không có tư liệu đạt chuẩn"
}}
"""
        res = self._call_gemini_json(prompt)
        if res and "search_queries" in res and res.get("visual_metaphor"):
            logger.info(
                f"🎬 [VisualCuration] Cảnh {scene_id} [{res.get('production_method')} / {res.get('tool_choice')}]: "
                f"{res.get('narrative_subtext', '')[:60]}..."
            )
            return res

        # Heuristic fallback an toàn nếu Gemini tạm ngưng
        return self._heuristic_scene_analysis(narration, topic, primary_entity, entity_type, scene_id, total_scenes)

    def _heuristic_scene_analysis(
        self,
        narration: str,
        topic: str,
        primary_entity: Optional[str],
        entity_type: str,
        scene_id: int = 1,
        total_scenes: int = 1,
    ) -> Dict[str, Any]:
        """Quy tắc dự phòng thông minh khi không có kết nối LLM."""
        narr_lower = narration.lower()
        topic_lower = topic.lower()

        is_dark = any(w in narr_lower for w in ["sợ", "bí ẩn", "tối", "chết", "nguy hiểm", "bóng đêm", "abyss", "fear", "dark", "horror", "pressure", "nỗi sợ", "ác mộng", "rùng mình"])
        is_cosmic = any(w in narr_lower or w in topic_lower for w in ["vũ trụ", "hố đen", "lỗ đen", "không gian", "ngân hà", "space", "galaxy", "black hole", "cosmic", "wormhole"])
        is_ocean = any(w in narr_lower or w in topic_lower for w in ["ocean", "sea", "biển", "abyss", "vực", "mariana", "thalassophobia", "bloop", "point nemo", "sâu thẳm", "sonar", "đại dương", "submarine", "tàu ngầm", "hải cẩu"])
        is_historical = bool(primary_entity) and entity_type in ("person", "civilization", "monument", "event", "historical_figure")
        is_face_or_portrait = any(w in narr_lower for w in ["khuôn mặt", "chân dung", "ánh mắt nhìn", "nụ cười quái dị", "tượng đá", "triết gia", "portrait", "face", "statue"])

        from app.agents.scifi_visual_dispatcher_agent import SciFiVisualDispatcherAgent
        scifi_agent = SciFiVisualDispatcherAgent()
        is_scifi, conf, subgenre, scifi_kws = scifi_agent.detect_scifi_elements(
            topic=topic,
            narration=narration,
        )

        # Cảnh 1 (Hook) luôn ưu tiên FULL VIDEO động
        if scene_id == 1:
            method = "STOCK_FOOTAGE"
            tool = "pexels_video_clip"
            motion = "HIGH_MOTION"
            mood = "dark_ominous" if is_dark else "tense_mysterious"
            if is_scifi:
                q = scifi_agent.get_curated_video_search_queries(subgenre=subgenre, narration=narration)
                ai_p = scifi_agent.create_cinematic_ai_video_prompt(topic or narration, subgenre=subgenre, scene_id=scene_id)
            elif is_ocean:
                q = ["deep sea submarine light", "dark underwater abyss", "ocean dark depths scuba", "deep ocean trench"]
                ai_p = f"Cinematic 8k dramatic vertical hook shot of pitch black deep sea abyss, submarine spotlight beam penetrating the dark water, colossal shadow in distance, thalassophobia, 9:16"
            else:
                q = ["cinematic dark mystery intro", "dramatic slow motion silhouette", "intense eye stare close up"]
                ai_p = f"Cinematic 8k dramatic vertical hook shot of {topic or 'mystery'}, dark moody lighting, 9:16"
        elif is_historical and primary_entity and primary_entity.lower() in narr_lower:
            method = "REAL_ARCHIVE"
            tool = "real_archive_photo"
            motion = "STATIC_DETAILED"
            mood = "historical_epic"
            q = [f"{primary_entity} authentic history", f"{primary_entity} portrait", f"{primary_entity} archives"]
            ai_p = f"Cinematic historical documentary authentic photorealistic portrait of {primary_entity}, 8k, period accurate, 9:16"
        elif is_scifi or is_cosmic:
            # Viễn tưởng / Vũ trụ: BẮT BUỘC ƯU TIÊN TEXT_TO_VIDEO hoặc STOCK_FOOTAGE, CẤM THUẦN ẢNH TĨNH
            method = "TEXT_TO_VIDEO"
            tool = "ai_text_to_video"
            motion = "HIGH_MOTION"
            mood = "cosmic_wonder"
            q = scifi_agent.get_curated_video_search_queries(subgenre=subgenre, narration=narration)
            ai_p = scifi_agent.create_cinematic_ai_video_prompt(topic or narration, subgenre=subgenre, scene_id=scene_id)
        elif is_face_or_portrait:
            method = "IMAGE_TO_VIDEO"
            tool = "ai_image_to_video"
            motion = "STATIC_DETAILED"
            mood = "tense_mysterious"
            q = ["dramatic character silhouette", "mysterious person face shadow", "thoughtful thinker portrait"]
            ai_p = f"Cinematic 8k vertical portrait, highly detailed facial expression, dramatic chiaroscuro lighting, 9:16"
        elif is_dark:
            method = "STOCK_FOOTAGE"
            tool = "pexels_video_clip"
            motion = "HIGH_MOTION"
            mood = "dark_ominous"
            q = ["dark moody cinematic shadows", "eerie mysterious atmosphere", "dramatic storm night"]
            ai_p = f"Cinematic dark moody atmosphere, dramatic volumetric chiaroscuro lighting, highly detailed 8k, 9:16"
        else:
            method = "STOCK_FOOTAGE"
            tool = "pexels_video_clip"
            motion = "MEDIUM_MOTION"
            mood = "vibrant_energetic"
            q = ["cinematic modern aesthetic b roll", "dramatic slow motion cinematic", "inspiring natural landscape"]
            ai_p = f"Cinematic vibrant 8k vertical shot, beautiful golden hour lighting, photorealistic 9:16"

        return {
            "mood": mood,
            "narrative_subtext": "Phân tích ngữ cảnh tự động theo trường ngữ nghĩa và vị trí phân cảnh.",
            "visual_metaphor": f"Cinematic visual representation of {topic or narration[:40]}",
            "production_method": method,
            "tool_choice": tool,
            "motion_level": motion,
            "search_queries": q,
            "negative_cues": ["cartoon", "low quality", "blurry", "text overlay", "smiling corporate", "sunny beach"],
            "cinematic_ai_prompt": ai_p,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # PHASE 1.5: AI VISION — NHÌN THẬT THUMBNAIL ĐỂ CHẤM ĐỘ HẤP DẪN & LIÊN QUAN
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _vision_enabled() -> bool:
        import os
        return os.getenv("ENABLE_VISION_CURATION", "true").strip().lower() not in ("0", "false", "no", "off")

    @staticmethod
    def _fetch_thumbnail_b64(url: str, timeout: int = 6) -> Optional[Tuple[str, str]]:
        """Tải thumbnail nhỏ của ứng viên và trả về (mime, base64). Bỏ qua ảnh quá lớn / lỗi."""
        if not url:
            return None
        import base64
        import requests
        try:
            ua = "Mozilla/5.0 AIYouTubeStudio/2.0"
            if "wikimedia.org" in url or "wikipedia.org" in url:
                from app.services.media.entity_detector import WIKIMEDIA_USER_AGENT
                ua = WIKIMEDIA_USER_AGENT
            res = requests.get(url, timeout=timeout, headers={"User-Agent": ua})
            if res.status_code != 200 or not res.content:
                return None
            data = res.content
            if len(data) < 1500 or len(data) > 2_500_000:
                return None
            mime = (res.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
            if not mime.startswith("image/"):
                mime = "image/jpeg"
            return mime, base64.b64encode(data).decode("ascii")
        except Exception:
            return None

    def vision_rank_candidates(
        self,
        candidates: List[Dict[str, Any]],
        narration: str,
        topic: str = "",
        visual_analysis: Optional[Dict[str, Any]] = None,
        kind: str = "video",
        max_candidates: int = 8,
    ) -> Optional[Tuple[int, float, str]]:
        """
        Gemini Vision nhìn trực tiếp thumbnail của từng ứng viên và chấm:
          - relevance (0-10): khớp lời thoại / chủ đề / không khí cảnh.
          - appeal    (0-10): độ hấp dẫn thị giác (bố cục, ánh sáng, màu, độ nét, sức hút giữ chân).
        Mỗi candidate cần có khóa 'thumbnail' (URL ảnh xem trước).

        Returns: (best_index, combined_score, rationale) hoặc None nếu Vision không khả dụng
                 (caller sẽ tự fallback về tuyển chọn bằng văn bản).
                 best_index = -1 nghĩa là Vision từ chối toàn bộ ứng viên.
        """
        if not self.rotator or not self._vision_enabled() or not candidates:
            return None

        from concurrent.futures import ThreadPoolExecutor

        pool = candidates[:max_candidates]
        with ThreadPoolExecutor(max_workers=min(8, len(pool))) as ex:
            thumbs = list(ex.map(lambda c: self._fetch_thumbnail_b64(c.get("thumbnail", "")), pool))

        indexed = [(i, t) for i, t in enumerate(thumbs) if t]
        if len(indexed) < 1:
            return None

        va = visual_analysis or {}
        mood = va.get("mood", "cinematic")
        metaphor = va.get("visual_metaphor", "")
        negative_cues = va.get("negative_cues", [])

        meta_lines = []
        for img_no, (orig_idx, _) in enumerate(indexed):
            c = pool[orig_idx]
            w, h = c.get("width") or 0, c.get("height") or 0
            orient = "dọc 9:16" if (w and h and h >= w) else ("ngang" if (w and h) else "không rõ")
            desc = (c.get("description") or c.get("title") or c.get("alt") or "")[:90]
            meta_lines.append(f"- Ảnh #{img_no}: nguồn={c.get('source', 'stock')}, bố cục={orient}, mô tả='{desc}'")

        prompt = f"""
Bạn là Giám Đốc Hình Ảnh (Director of Photography) cho kênh YouTube Shorts triệu view.
Bạn được xem {len(indexed)} ảnh thumbnail của các {'video clip' if kind == 'video' else 'bức ảnh'} ứng viên (theo đúng thứ tự Ảnh #0, #1, ...).
Hãy NHÌN KỸ từng ảnh và chọn ứng viên vừa ĐÚNG NỘI DUNG vừa HẤP DẪN THỊ GIÁC NHẤT cho phân cảnh.

PHÂN CẢNH:
- Chủ đề video: "{topic}"
- Lời thoại: "{narration}"
- Ý niệm thị giác: "{metaphor}"
- Không khí (mood): "{mood}"
- Yếu tố cấm kỵ: {json.dumps(negative_cues, ensure_ascii=False)}

THÔNG TIN ỨNG VIÊN:
{chr(10).join(meta_lines)}

TIÊU CHÍ CHẤM (mỗi ứng viên):
1. relevance (0-10): Hình ảnh có thể hiện đúng chủ thể/hành động/cảm xúc của lời thoại không? Lạc đề, sai thời đại, sai thể loại -> dưới 4.
2. appeal (0-10): Sức hút thị giác giữ chân người xem: chủ thể rõ ràng, bố cục mạnh, ánh sáng điện ảnh, màu sắc ấn tượng, sắc nét, có chiều sâu/cảm xúc. Ảnh nhạt nhòa, mờ, chung chung, stock sến, có chữ/watermark -> dưới 5.
3. Ưu tiên bố cục dọc hoặc chủ thể nằm giữa (dễ cắt 9:16).

TRẢ VỀ JSON DUY NHẤT:
{{
  "scores": [{{"image": 0, "relevance": 8.0, "appeal": 7.5}}],
  "best_image": 0,
  "rationale": "1 câu giải thích vì sao chọn (hoặc vì sao từ chối tất cả)",
  "reject_all": false
}}
Nếu KHÔNG ảnh nào có relevance >= 5 -> đặt best_image = -1 và reject_all = true.
"""
        res = self._call_gemini_json(
            prompt,
            timeout=25,
            images=[t for _, t in indexed],
            models=["gemini-flash-latest", "gemini-flash-lite-latest"],
            max_attempts=3,
        )
        if not res:
            return None

        try:
            score_map: Dict[int, Tuple[float, float]] = {}
            for s in res.get("scores", []) or []:
                img_no = int(s.get("image", -1))
                if 0 <= img_no < len(indexed):
                    score_map[img_no] = (float(s.get("relevance", 0) or 0), float(s.get("appeal", 0) or 0))

            rationale = str(res.get("rationale", ""))[:200]
            if res.get("reject_all") or int(res.get("best_image", -1)) < 0:
                logger.info(f"👁️ [VisionCuration] Từ chối toàn bộ {len(indexed)} ứng viên {kind}: {rationale}")
                return -1, 0.0, rationale

            # Chọn theo điểm tổng hợp (60% liên quan + 40% hấp dẫn), bắt buộc relevance >= 5
            best_img, best_combined = -1, -1.0
            for img_no, (rel, app) in score_map.items():
                if rel < 5.0:
                    continue
                combined = 0.6 * rel + 0.4 * app
                if combined > best_combined:
                    best_img, best_combined = img_no, combined

            if best_img < 0:
                suggested = int(res.get("best_image", -1))
                if 0 <= suggested < len(indexed) and suggested not in score_map:
                    best_img, best_combined = suggested, 6.0
                else:
                    logger.info(f"👁️ [VisionCuration] Không ứng viên {kind} nào đạt relevance >= 5: {rationale}")
                    return -1, 0.0, rationale

            orig_idx = indexed[best_img][0]
            rel, app = score_map.get(best_img, (best_combined, best_combined))
            logger.info(
                f"👁️ [VisionCuration] ✅ Chọn {kind} #{orig_idx} ({pool[orig_idx].get('source', 'stock')}) | "
                f"Liên quan {rel:.1f} · Hấp dẫn {app:.1f} · Tổng {best_combined:.1f}/10 | {rationale}"
            )
            return orig_idx, round(best_combined, 2), rationale
        except Exception as e:
            logger.debug(f"[VisionCuration] Parse error: {e}")
            return None

    # ──────────────────────────────────────────────────────────────────────────
    # PHASE 2: ĐÁNH GIÁ & TUYỂN CHỌN ỨNG VIÊN TƯ LIỆU (CANDIDATE CURATION)
    # ──────────────────────────────────────────────────────────────────────────

    def curate_candidate_videos(
        self,
        candidates: List[Dict[str, Any]],
        visual_analysis: Dict[str, Any],
        narration: str = "",
        used_urls: Optional[set] = None,
    ) -> Tuple[Optional[Dict[str, Any]], float, str, bool]:
        """
        Đánh giá toàn diện danh sách video ứng viên từ kho Pexels:
        - Đọc slug URL, tiêu đề, thời lượng, bố cục dọc 9:16.
        - Agent chấm điểm độ tương thích ngữ nghĩa và cảm xúc.
        - Lựa chọn video xuất sắc nhất, hoặc quyết định từ chối toàn bộ nếu không đạt chuẩn.

        Returns:
            (winner_candidate, confidence_score, rationale, should_fallback_to_ai)
        """
        used = used_urls or set()
        if not candidates:
            return None, 0.0, "Không có ứng viên video nào từ kho tư liệu.", True

        # 1. Tiền lọc: Chỉ giữ video có file MP4 hợp lệ, chưa từng sử dụng
        #    - Pexels: chọn file dọc, ưu tiên 1080x1920 (tải nhanh, đủ nét) thay vì 4K nặng.
        #    - Pixabay / NASA: đã được chuẩn hóa sẵn (url trực tiếp + thumbnail).
        def _res_pref(vf: Dict[str, Any]) -> int:
            h = vf.get("height", 0) or 0
            if h == 1920:
                return 10000
            if 1280 <= h < 1920:
                return 8000 + h
            if 720 <= h < 1280:
                return 6000 + h
            if h > 1920:
                return 4000 - h // 10
            return h

        valid_candidates = []
        for v in candidates:
            url = v.get("url", "")
            if not url or url in used:
                continue

            v_files = v.get("video_files", [])
            if not v_files:
                # Ứng viên đã chuẩn hóa (Pixabay / NASA / nguồn khác)
                if v.get("media_type", "video") != "video":
                    continue
                valid_candidates.append({
                    "raw_video": v,
                    "url": url,
                    "download_link": url,
                    "width": v.get("width", 1920),
                    "height": v.get("height", 1080),
                    "duration": v.get("duration", 0),
                    "slug": re.sub(r"^(Pixabay Video|NASA[^:]*):\s*", "", v.get("title", "")) or "video clip",
                    "thumbnail": v.get("thumbnail", ""),
                    "source": v.get("source", "stock_video"),
                })
                continue

            portrait_files = [
                vf for vf in v_files
                if vf.get("width", 0) <= vf.get("height", 0) and (
                    vf.get("link", "").endswith(".mp4") or "mp4" in vf.get("file_type", "")
                )
            ]
            if not portrait_files:
                continue

            # Trích xuất slug mang ý nghĩa nội dung từ URL
            # Ví dụ: 'https://www.pexels.com/video/a-dark-cloudy-sky-with-waves-19079582/' -> 'a dark cloudy sky with waves'
            slug = ""
            m = re.search(r'/video/([a-zA-Z0-9-]+)-\d+/?', url)
            if m:
                slug = m.group(1).replace("-", " ")
            else:
                slug = v.get("tags") or v.get("title") or "video clip"

            best_file = sorted(portrait_files, key=_res_pref, reverse=True)[0]
            valid_candidates.append({
                "raw_video": v,
                "url": url,
                "download_link": best_file.get("link"),
                "width": best_file.get("width", 1080),
                "height": best_file.get("height", 1920),
                "duration": v.get("duration", 0),
                "slug": slug,
                "thumbnail": v.get("image", ""),
                "source": "pexels_video",
            })

        if not valid_candidates:
            return None, 0.0, "Tất cả ứng viên đều không đạt định dạng hoặc đã được sử dụng.", True

        # Ưu tiên clip dọc lên trước, tối đa 8 ứng viên cho Agent phân tích
        valid_candidates.sort(key=lambda c: 0 if (c["height"] or 0) >= (c["width"] or 0) else 1)
        eval_pool = valid_candidates[:8]

        def _format_winner(winner: Dict[str, Any], score: float, rationale: str) -> Dict[str, Any]:
            return {
                "url": winner["download_link"],
                "web_url": winner["url"],
                "title": f"{winner['source']}: {winner['slug']}",
                "width": winner["width"],
                "height": winner["height"],
                "duration": winner["duration"],
                "media_type": "video",
                "source": winner["source"],
                "thumbnail": winner["thumbnail"],
                "curation_score": score,
                "curation_rationale": rationale,
            }

        # 2A. AI VISION: Nhìn thumbnail thật để chọn clip vừa đúng nội dung vừa hấp dẫn nhất
        vision = self.vision_rank_candidates(
            [{**c, "description": c["slug"]} for c in eval_pool],
            narration=narration,
            topic=visual_analysis.get("visual_metaphor", ""),
            visual_analysis=visual_analysis,
            kind="video",
        )
        if vision is not None:
            v_idx, v_score, v_rat = vision
            if v_idx >= 0:
                return _format_winner(eval_pool[v_idx], max(v_score, 6.0), f"[Vision] {v_rat}"), max(v_score, 6.0), v_rat, False
            return None, 0.0, f"[Vision] {v_rat}", True

        # 2B. Fallback: Agent tuyển chọn dựa trên mô tả văn bản (slug)
        eval_pool = eval_pool[:6]

        # 2. Gọi Agent phân tích & tuyển chọn
        mood = visual_analysis.get("mood", "cinematic")
        metaphor = visual_analysis.get("visual_metaphor", "")
        negative_cues = visual_analysis.get("negative_cues", [])

        prompt = f"""
Bạn là Tổng Đạo Diễn Hình Ảnh (Director of Photography).
Nhiệm vụ: Duyệt qua các đoạn video ứng viên từ kho tư liệu và LỰA CHỌN DUY NHẤT 1 VIDEO phù hợp nhất với tinh thần phân cảnh.

BỐI CẢNH PHÂN CẢNH:
- Lời thoại: "{narration}"
- Ý niệm thị giác & Ẩn dụ: "{metaphor}"
- Tâm trạng / Không khí (Mood): "{mood}"
- Yếu tố cấm kỵ (Negative Cues): {json.dumps(negative_cues, ensure_ascii=False)}

DANH SÁCH ỨNG VIÊN VIDEO CÓ SẴN (Nội dung thực tế trích xuất từ video):
{json.dumps([{"index": idx, "description": c["slug"], "duration": f"{c['duration']}s"} for idx, c in enumerate(eval_pool)], ensure_ascii=False, indent=2)}

QUY TẮC TUYỂN CHỌN:
1. Video phải có cùng tâm trạng (ví dụ nếu u ám thì không chọn cảnh có nắng tươi vui).
2. TUYỆT ĐỐI LOẠI TRỪ video lạc đề, sai lệch thời đại (anachronism) hoặc lệch thể loại (ví dụ: người văn phòng/laptop/quán cafe/tiệc bar/hộp đêm/selfie trong video về vũ trụ, triết học, lịch sử, thiên nhiên hoang dã).
3. Video KHÔNG được dính các yếu tố cấm kỵ (Negative Cues).
4. Nếu CÓ ứng viên đạt chuẩn (điểm >= 6.0/10) -> Chọn index của ứng viên đó.
5. NẾU TẤT CẢ ỨNG VIÊN ĐỀU KHÔNG LIÊN QUAN, LẠC ĐỀ HOẶC PHÁ HỎNG BỐI CẢNH (Tất cả < 6.0) -> Trả về winner_index = -1 và should_fallback_to_ai = true để hệ thống tự động sinh Video AI kỳ ảo.

TRẢ VỀ JSON DUY NHẤT:
{{
  "winner_index": 0,
  "confidence_score": 8.5,
  "rationale": "Giải thích ngắn gọn 1 câu vì sao chọn (hoặc vì sao từ chối toàn bộ)",
  "should_fallback_to_ai": false
}}
"""
        agent_dec = self._call_gemini_json(prompt)
        if agent_dec:
            w_idx = agent_dec.get("winner_index", -1)
            score = float(agent_dec.get("confidence_score", 0.0))
            rationale = agent_dec.get("rationale", "")
            fallback_ai = bool(agent_dec.get("should_fallback_to_ai", False))

            if w_idx >= 0 and w_idx < len(eval_pool) and score >= 6.0 and not fallback_ai:
                winner = eval_pool[w_idx]
                logger.info(
                    f"🎬 [VisualCuration] ✅ Đã tuyển chọn Video #{winner['raw_video'].get('id')} "
                    f"('{winner['slug']}') | Điểm: {score}/10 | {rationale}"
                )
                return _format_winner(winner, score, rationale), score, rationale, False
            else:
                logger.warning(
                    f"🎬 [VisualCuration] ⚠️ Agent từ chối toàn bộ stock videos (Score: {score}/10). "
                    f"Lý do: {rationale}. Tự động chuyển hướng sang Generative AI!"
                )
                return None, score, rationale, True

        # Rule-based fallback nếu Agent không phản hồi
        return self._heuristic_curate_videos(eval_pool, visual_analysis)

    def _heuristic_curate_videos(
        self,
        candidates: List[Dict[str, Any]],
        visual_analysis: Dict[str, Any],
    ) -> Tuple[Optional[Dict[str, Any]], float, str, bool]:
        """Lọc và chấm điểm bằng rule-based nếu Gemini bận."""
        negative_cues = [c.lower() for c in visual_analysis.get("negative_cues", [])]
        queries = [q.lower() for q in visual_analysis.get("search_queries", [])]

        best_cand = None
        best_score = -1.0

        for c in candidates:
            slug = c["slug"].lower()
            if any(nc in slug for nc in negative_cues):
                continue

            score = 5.0
            # Thưởng điểm nếu slug chứa các từ khóa trong search_queries
            for q in queries:
                for w in q.split():
                    if len(w) > 3 and w in slug:
                        score += 1.5

            if score > best_score:
                best_score = score
                best_cand = c

        if best_cand and best_score >= 6.0:
            formatted = {
                "url": best_cand["download_link"],
                "web_url": best_cand["url"],
                "title": f"{best_cand.get('source', 'pexels_video')}: {best_cand['slug']}",
                "width": best_cand["width"],
                "height": best_cand["height"],
                "duration": best_cand["duration"],
                "media_type": "video",
                "source": best_cand.get("source", "pexels_video"),
                "thumbnail": best_cand["thumbnail"],
                "curation_score": best_score,
                "curation_rationale": f"Khớp từ vựng ngữ cảnh ({best_cand['slug']})",
            }
            return formatted, best_score, "Rule-based curation passed", False

        return None, 4.0, "Không có ứng viên nào vượt qua bộ lọc âm tính", True

    def check_media_congruence(
        self,
        media_desc: str,
        topic: str = "",
        narration: str = "",
        visual_keyword: str = "",
    ) -> Tuple[bool, float, str]:
        """
        Agent LLM phân tích ngữ nghĩa sâu sắc: Thẩm định xem 1 mô tả ảnh/video
        có bị lạc đề, lệch thời đại (anachronism) hay phá vỡ thể loại video hay không.
        Trả về (is_incongruent: bool, score: float, rationale: str).
        """
        if not media_desc:
            return False, 5.0, "Không có mô tả tư liệu"

        prompt = f"""
Bạn là Tổng Giám Khảo Thẩm Định Tư Liệu Hình Ảnh & Video (Visual Relevance & Congruence Critic) cho YouTube Shorts.
Nhiệm vụ: Đánh giá xem tư liệu sau ĐÂY CÓ BỊ LẠC ĐỀ HOÀN TOÀN (INCONGRUENT) so với chủ đề và nội dung phân cảnh video hay không.

NGỮ CẢNH VIDEO:
- Chủ đề video: "{topic}"
- Lời thoại phân cảnh: "{narration}"
- Từ khóa mong muốn: "{visual_keyword}"

TƯ LIỆU CẦN THẨM ĐỊNH:
- Mô tả / Tiêu đề / Thẻ: "{media_desc}"

TIÊU CHUẨN ĐÁNH GIÁ LẠC ĐỀ:
1. Lạc đề thể loại (Genre Incongruence): Ví dụ cảnh văn phòng/hội họp công sở/gõ laptop/uống cafe/tiệc bar/hộp đêm/người mẫu runway lọt vào video về vũ trụ, triết học cổ đại, chiến tranh lịch sử, sinh tồn hoang dã.
2. Lạc đề thời đại (Anachronism): Đồ vật hiện đại trong bối cảnh cổ đại / trung cổ.
3. Lạc đề định dạng: Bản đồ hành chính, biểu đồ kỹ thuật, tem thư nếu kịch bản không yêu cầu.

TRẢ VỀ JSON DUY NHẤT:
{{
  "is_incongruent": true,
  "congruence_score": 2.5,
  "rationale": "Giải thích ngắn gọn 1 câu vì sao lạc đề hoặc vì sao ăn khớp"
}}
"""
        res = self._call_gemini_json(prompt, timeout=10)
        if res:
            is_incong = bool(res.get("is_incongruent", False))
            score = float(res.get("congruence_score", 5.0))
            if score < 5.0:
                is_incong = True
            rationale = res.get("rationale", "")
            return is_incong, score, rationale

        # Fallback sang heuristic keyword nếu LLM không phản hồi
        from app.services.media.entity_detector import _is_incongruent_stock_heuristic
        heur_incong = _is_incongruent_stock_heuristic(media_desc, topic=topic, narration=narration)
        return heur_incong, (3.0 if heur_incong else 7.0), "Heuristic keyword fallback"

    def curate_candidate_photos(
        self,
        candidates: List[Dict[str, Any]],
        visual_analysis: Optional[Dict[str, Any]] = None,
        narration: str = "",
        topic: str = "",
        visual_keyword: str = "",
        used_urls: Optional[set] = None,
    ) -> Tuple[Optional[Dict[str, Any]], float, str, bool]:
        """
        Đánh giá và tuyển chọn ảnh chụp tĩnh (Pexels, Wikimedia, Openverse):
        Agent LLM phân tích ngữ nghĩa của các ảnh ứng viên, phát hiện thể loại lạc đề,
        chọn ra ảnh tốt nhất hoặc từ chối toàn bộ nếu lạc đề để kích hoạt sinh Ảnh AI.
        """
        used = used_urls or set()
        valid = [c for c in candidates if c.get("url") and c.get("url") not in used]
        if not valid:
            return None, 0.0, "Không có ứng viên ảnh nào khả dụng.", True

        if not visual_analysis:
            visual_analysis = self.analyze_scene_context(
                narration=narration or visual_keyword,
                topic=topic or visual_keyword,
            )

        # Trích xuất mô tả ảnh đầy đủ để gửi cho LLM Agent thẩm định toàn diện
        pool = []
        for c in valid:
            desc = c.get("title") or c.get("alt") or ""
            if not desc and "/photo/" in c.get("url", ""):
                m = re.search(r'/photo/([a-zA-Z0-9-]+)-\d+/?', c.get("url", ""))
                if m:
                    desc = m.group(1).replace("-", " ")
            pool.append({
                "item": c,
                "description": desc or "photograph",
            })
            if len(pool) >= 8:
                break

        if not pool:
            return None, 3.0, "Không có ứng viên ảnh nào khả dụng.", True

        # AI VISION: Nhìn trực tiếp ảnh để chọn bức vừa đúng nội dung vừa hấp dẫn nhất
        vision = self.vision_rank_candidates(
            [{**p["item"], "description": p["description"]} for p in pool],
            narration=narration,
            topic=topic or visual_keyword,
            visual_analysis=visual_analysis,
            kind="photo",
        )
        if vision is not None:
            v_idx, v_score, v_rat = vision
            if v_idx >= 0:
                winner_item = pool[v_idx]["item"]
                winner_item["curation_score"] = max(v_score, 6.0)
                winner_item["curation_rationale"] = f"[Vision] {v_rat}"
                return winner_item, max(v_score, 6.0), v_rat, False
            return None, 0.0, f"[Vision] {v_rat}", True

        pool = pool[:6]

        mood = visual_analysis.get("mood", "cinematic")
        metaphor = visual_analysis.get("visual_metaphor", "")
        negative_cues = visual_analysis.get("negative_cues", [])

        prompt = f"""
Bạn là Tổng Đạo Diễn Hình Ảnh & Giám Khảo Tuyển Chọn Tư Liệu (Visual Curation Director) cho YouTube Shorts.
Nhiệm vụ: Duyệt qua các bức ảnh ứng viên từ kho tư liệu và LỰA CHỌN DUY NHẤT 1 ẢNH phù hợp nhất với tinh thần phân cảnh.

BỐI CẢNH PHÂN CẢNH:
- Chủ đề video: "{topic}"
- Lời thoại (Narration): "{narration}"
- Từ khóa thị giác (Visual Keyword): "{visual_keyword or metaphor}"
- Không khí (Mood): "{mood}"
- Yếu tố cấm kỵ (Negative Cues): {json.dumps(negative_cues, ensure_ascii=False)}

DANH SÁCH ẢNH ỨNG VIÊN CÓ SẴN TỪ KHO TƯ LIỆU:
{json.dumps([{"index": i, "description": p["description"][:120], "source": p["item"].get("source", "stock")} for i, p in enumerate(pool)], ensure_ascii=False, indent=2)}

QUY TẮC TUYỂN CHỌN:
1. Ảnh phải phù hợp ngữ cảnh, thời đại và không khí của phân cảnh.
2. TUYỆT ĐỐI KHÔNG CHỌN ảnh lạc quẻ, sai lệch thời đại (ví dụ: ảnh người hiện đại/công sở/tiệc tùng/quán cafe trong bối cảnh vũ trụ, lịch sử cổ đại, triết học; hoặc sơ đồ/biểu đồ/tem thư/bản đồ không hợp kịch bản).
3. Nếu có ứng viên đạt chuẩn (điểm >= 6.0/10) -> winner_index = index của ứng viên đó.
4. NẾU TẤT CẢ ỨNG VIÊN ĐỀU KHÔNG LIÊN QUAN, LẠC ĐỀ HOẶC PHÁ HỎNG BỐI CẢNH (Tất cả < 6.0) -> Trả về winner_index = -1 và should_fallback_to_ai = true để hệ thống tự động sinh Ảnh AI kỳ ảo độc bản.

TRẢ VỀ JSON DUY NHẤT:
{{
  "winner_index": 0,
  "confidence_score": 8.0,
  "rationale": "Giải thích ngắn gọn 1 câu vì sao chọn (hoặc vì sao từ chối toàn bộ)",
  "should_fallback_to_ai": false
}}
"""
        agent_dec = self._call_gemini_json(prompt)
        if agent_dec:
            w_idx = agent_dec.get("winner_index", -1)
            score = float(agent_dec.get("confidence_score", 0.0))
            rationale = agent_dec.get("rationale", "")
            fallback_ai = bool(agent_dec.get("should_fallback_to_ai", False))

            if w_idx >= 0 and w_idx < len(pool) and score >= 6.0 and not fallback_ai:
                winner_item = pool[w_idx]["item"]
                winner_item["curation_score"] = score
                winner_item["curation_rationale"] = rationale
                logger.info(
                    f"📸 [VisualCuration] ✅ Đã tuyển chọn Ảnh: '{pool[w_idx]['description'][:50]}' | "
                    f"Điểm: {score}/10 | {rationale}"
                )
                return winner_item, score, rationale, False
            else:
                logger.info(
                    f"📸 [VisualCuration] ⚠️ Agent từ chối các ảnh stock (Score: {score}/10 | {rationale}). "
                    f"Tự động chuyển hướng sang AI Image Generation!"
                )
                return None, score, rationale, True

        # Heuristic curation dự phòng khi Gemini không phản hồi
        return self._heuristic_curate_photos(pool, visual_analysis, topic=topic, narration=narration)

    def _heuristic_curate_photos(
        self,
        pool: List[Dict[str, Any]],
        visual_analysis: Dict[str, Any],
        topic: str = "",
        narration: str = "",
    ) -> Tuple[Optional[Dict[str, Any]], float, str, bool]:
        """Lọc và chấm điểm ảnh bằng heuristic, loại bỏ negative cues."""
        if not pool:
            return None, 0.0, "Không có ảnh ứng viên.", True

        from app.services.media.entity_detector import is_incongruent_stock_image
        negative_cues = [nc.lower() for nc in visual_analysis.get("negative_cues", [])]
        queries = [q.lower() for q in visual_analysis.get("search_queries", [])]

        best_item = None
        best_score = -1.0

        for p in pool:
            desc = p.get("description", "").lower()
            if any(nc in desc for nc in negative_cues) or is_incongruent_stock_image(desc, topic=topic, narration=narration):
                continue

            score = 5.0
            for q in queries:
                for w in q.split():
                    if len(w) > 3 and w in desc:
                        score += 1.5

            if score > best_score:
                best_score = score
                best_item = p["item"]

        if best_item and best_score >= 6.0:
            best_item["curation_score"] = best_score
            best_item["curation_rationale"] = "Khớp từ khóa ngữ cảnh qua bộ lọc heuristic"
            return best_item, best_score, "Heuristic pass", False

        return None, 4.0, "Tất cả ảnh ứng viên đều dính negative cues hoặc không đủ liên quan", True
