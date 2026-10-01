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

    def _call_gemini_json(self, prompt: str, timeout: int = 15) -> Optional[Dict[str, Any]]:
        """Gọi Gemini API trả về JSON với KeyRotator và fallback an toàn."""
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

        # 1. Tiền lọc: Chỉ giữ video có file dọc MP4, thời lượng hợp lệ, chưa từng sử dụng
        valid_candidates = []
        for v in candidates:
            url = v.get("url", "")
            if not url or url in used:
                continue

            v_files = v.get("video_files", [])
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

            best_file = sorted(portrait_files, key=lambda x: x.get("height", 0), reverse=True)[0]
            valid_candidates.append({
                "raw_video": v,
                "url": url,
                "download_link": best_file.get("link"),
                "width": best_file.get("width", 1080),
                "height": best_file.get("height", 1920),
                "duration": v.get("duration", 0),
                "slug": slug,
                "thumbnail": v.get("image", ""),
            })

        if not valid_candidates:
            return None, 0.0, "Tất cả ứng viên đều không đạt định dạng 9:16 hoặc đã được sử dụng.", True

        # Giới hạn tối đa 6 ứng viên để Agent phân tích nhanh
        eval_pool = valid_candidates[:6]

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
2. Video KHÔNG được dính các yếu tố cấm kỵ.
3. Nếu CÓ ứng viên đạt chuẩn (điểm >= 6.0/10) -> Chọn index của ứng viên đó.
4. NẾU TẤT CẢ ỨNG VIÊN ĐỀU KHÔNG LIÊN QUAN, LẠC ĐỀ HOẶC PHÁ HỎNG BỐI CẢNH (Tất cả < 6.0) -> Trả về winner_index = -1 và should_fallback_to_ai = true để hệ thống tự động sinh Video AI kỳ ảo.

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
                formatted = {
                    "url": winner["download_link"],
                    "web_url": winner["url"],
                    "title": f"Pexels Video: {winner['slug']}",
                    "width": winner["width"],
                    "height": winner["height"],
                    "duration": winner["duration"],
                    "media_type": "video",
                    "source": "pexels_video",
                    "thumbnail": winner["thumbnail"],
                    "curation_score": score,
                    "curation_rationale": rationale,
                }
                return formatted, score, rationale, False
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
                "title": f"Pexels Video: {best_cand['slug']}",
                "width": best_cand["width"],
                "height": best_cand["height"],
                "duration": best_cand["duration"],
                "media_type": "video",
                "source": "pexels_video",
                "thumbnail": best_cand["thumbnail"],
                "curation_score": best_score,
                "curation_rationale": f"Khớp từ vựng ngữ cảnh ({best_cand['slug']})",
            }
            return formatted, best_score, "Rule-based curation passed", False

        return None, 4.0, "Không có ứng viên nào vượt qua bộ lọc âm tính", True

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
        Agent LLM phân tích ngữ nghĩa của các ảnh ứng viên, chọn ra ảnh tốt nhất
        hoặc từ chối toàn bộ nếu lạc đề để kích hoạt sinh Ảnh AI.
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

        # Trích xuất mô tả ảnh & lọc sơ bộ bằng heuristic 0ms
        from app.services.media.entity_detector import is_incongruent_stock_image
        pool = []
        for c in valid:
            desc = c.get("title") or c.get("alt") or ""
            if not desc and "/photo/" in c.get("url", ""):
                m = re.search(r'/photo/([a-zA-Z0-9-]+)-\d+/?', c.get("url", ""))
                if m:
                    desc = m.group(1).replace("-", " ")
            if is_incongruent_stock_image(desc, topic=topic or narration, narration=narration):
                continue
            pool.append({
                "item": c,
                "description": desc or "photograph",
            })
            if len(pool) >= 6:
                break

        if not pool:
            return None, 3.0, "Toàn bộ ảnh ứng viên dính thẻ lạc đề (văn phòng, cafe, tiệc tùng, sơ đồ...)", True

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
