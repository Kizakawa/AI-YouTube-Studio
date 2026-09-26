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
    "gemini-3.1-flash-lite-preview",
    "gemini-flash-latest",
    "gemini-3.1-pro-preview",
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
        key = self.rotator.get_key()
        if not key:
            return None

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
                    key = self.rotator.get_key()
            except Exception as e:
                logger.debug(f"[VisualCurationAgent] Gemini call error on {model}: {e}")
                continue

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
        - Xác định phong cách hình ảnh & ẩn dụ thị giác.
        - Tạo 3 truy vấn tìm kiếm chuyên biệt cho stock video.
        - Tạo prompt AI 9:16 sắc nét để sẵn sàng fallback.
        """
        prompt = f"""
Bạn là Tổng Đạo Diễn Nghệ Thuật & Giám Sát Thị Giác (Cinematographer & Visual Curation Director) cho YouTube Shorts hàng đầu.

NHIỆM VỤ:
Phân tích lời thoại của phân cảnh này để đưa ra chỉ đạo hình ảnh CHÍNH XÁC VỀ MẶT CẢM XÚC VÀ NGỤ Ý, TUYỆT ĐỐI KHÔNG BẮT TỪ KHÓA BỀ MẶT (LITERAL KEYWORD).

VÍ DỤ VỀ SAI LẦM CẦN TRÁNH:
- Lời thoại: "Bộ não của bạn không sợ thứ ở dưới nước. Nó sợ thứ bạn không nhìn thấy."
  -> SAI: Tìm "não người", "uống nước", "hồ bơi" (ngây ngô, phá nát video).
  -> ĐÚNG: Visual Metaphor: "Đáy đại dương sâu thẳm tối tăm, bóng đen bí ẩn khổng lồ lờ mờ ẩn dưới mặt nước rợn người".
- Lời thoại: "Thương trường như chiến trường, một quyết định sai lầm sẽ mất tất cả."
  -> SAI: Tìm chiến tranh Thế chiến 2 hoặc lính bắn nhau.
  -> ĐÚNG: Visual Metaphor: "Thương trường tài chính hiện đại, đồ thị sụp đổ, ánh mắt căng thẳng của doanh nhân trong văn phòng đêm".

THÔNG TIN ĐẦU VÀO:
- Chủ đề video: "{topic}"
- Niche kênh: "{channel_niche}"
- Phân cảnh số: {scene_id}/{total_scenes}
- Thực thể chính (nếu có): "{primary_entity or 'Không có'}" (Loại: {entity_type})
- Lời thoại phân cảnh: "{narration}"
- Ngữ cảnh xung quanh: "{surrounding_context}"

HÃY PHÂN TÍCH VÀ TRẢ VỀ JSON DUY NHẤT:
{{
  "mood": "Tâm trạng chủ đạo (ví dụ: dark_ominous | epic_heroic | tense_mysterious | vibrant_energetic | melancholy_thoughtful | scientific_curiosity)",
  "narrative_subtext": "Ngụ ý và cảm xúc sâu xa mà phân cảnh này muốn truyền tải (1-2 câu tiếng Việt)",
  "visual_metaphor": "Mô tả bối cảnh điện ảnh cụ thể mà khán giả nên nhìn thấy trên màn hình 9:16 (tiếng Anh & tiếng Việt)",
  "production_method": "STOCK_FOOTAGE | TEXT_TO_VIDEO | IMAGE_TO_VIDEO | REAL_ARCHIVE",
  "search_queries": [
    "Truy vấn tiếng Anh 2-3 từ cụ thể cho thư viện stock (vd: deep dark ocean abyss)",
    "Truy vấn thay thế góc nhìn 2 (vd: underwater dark sea trench)",
    "Truy vấn thay thế góc nhìn 3 (vd: gloomy ocean depth night)"
  ],
  "negative_cues": [
    "Từ/khái niệm cấm không được xuất hiện trong ảnh/video (vd: swimming pool, sunny beach, smiling people, drinking glass)"
  ],
  "cinematic_ai_prompt": "Câu lệnh tiếng Anh chi tiết phong cách điện ảnh 8K tỷ lệ 9:16 dùng để sinh Video AI hoặc Ảnh AI nếu kho stock không có tư liệu đạt chuẩn"
}}
"""
        res = self._call_gemini_json(prompt)
        if res and "search_queries" in res and res.get("visual_metaphor"):
            logger.info(f"🎬 [VisualCuration] Cảnh {scene_id} ({res.get('mood')}): {res.get('narrative_subtext', '')[:60]}...")
            return res

        # Heuristic fallback an toàn nếu Gemini tạm ngưng
        return self._heuristic_scene_analysis(narration, topic, primary_entity, entity_type)

    def _heuristic_scene_analysis(
        self,
        narration: str,
        topic: str,
        primary_entity: Optional[str],
        entity_type: str,
    ) -> Dict[str, Any]:
        """Quy tắc dự phòng thông minh khi không có kết nối LLM."""
        narr_lower = narration.lower()
        topic_lower = topic.lower()

        is_dark = any(w in narr_lower for w in ["sợ", "bí ẩn", "tối", "chết", "nguy hiểm", "bóng đêm", "abyss", "fear", "dark", "horror", "pressure"])
        is_cosmic = any(w in narr_lower or w in topic_lower for w in ["vũ trụ", "hố đen", "lỗ đen", "không gian", "ngân hà", "space", "galaxy", "black hole", "cosmic"])
        is_historical = bool(primary_entity) and entity_type in ("person", "civilization", "monument", "event", "historical_figure")

        if is_historical:
            method = "REAL_ARCHIVE"
            mood = "historical_epic"
            q = [f"{primary_entity} authentic history", f"{primary_entity} historical portrait", f"{primary_entity} archives"]
            ai_p = f"Cinematic historical documentary authentic photorealistic portrait of {primary_entity}, 8k, period accurate, 9:16"
        elif is_cosmic:
            method = "TEXT_TO_VIDEO"
            mood = "cosmic_wonder"
            q = ["black hole event horizon", "galaxy space nebula", "deep space cosmos stars"]
            ai_p = f"Cinematic 8k vertical shot of {topic or 'cosmic universe'}, warping space-time, glowing accretion disk, photorealistic 9:16"
        elif is_dark:
            method = "STOCK_FOOTAGE"
            mood = "tense_mysterious"
            q = ["dark moody cinematic shadows", "eerie mysterious atmosphere", "dramatic storm night"]
            ai_p = f"Cinematic dark moody atmosphere, dramatic volumetric chiaroscuro lighting, highly detailed 8k, 9:16"
        else:
            method = "STOCK_FOOTAGE"
            mood = "vibrant_energetic"
            q = ["cinematic modern aesthetic b roll", "dramatic slow motion cinematic", "inspiring natural landscape"]
            ai_p = f"Cinematic vibrant 8k vertical shot, beautiful golden hour lighting, photorealistic 9:16"

        return {
            "mood": mood,
            "narrative_subtext": "Phân tích dự phòng: Tự động nhận diện bối cảnh và cảm xúc theo trường ngữ nghĩa.",
            "visual_metaphor": f"Cinematic representation of {topic or narration[:40]}",
            "production_method": method,
            "search_queries": q,
            "negative_cues": ["cartoon", "low quality", "blurry", "text overlay"],
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
        visual_analysis: Dict[str, Any],
        narration: str = "",
        used_urls: Optional[set] = None,
    ) -> Tuple[Optional[Dict[str, Any]], float, str, bool]:
        """Đánh giá và tuyển chọn ảnh chụp tĩnh (Pexels, Wikimedia, Openverse)."""
        used = used_urls or set()
        valid = [c for c in candidates if c.get("url") and c.get("url") not in used]
        if not valid:
            return None, 0.0, "Không có ứng viên ảnh nào khả dụng.", True

        # Trích xuất mô tả ảnh
        pool = []
        for c in valid[:6]:
            desc = c.get("title") or c.get("alt") or ""
            if not desc and "/photo/" in c.get("url", ""):
                m = re.search(r'/photo/([a-zA-Z0-9-]+)-\d+/?', c.get("url", ""))
                if m:
                    desc = m.group(1).replace("-", " ")
            pool.append({
                "item": c,
                "description": desc or "photograph",
            })

        mood = visual_analysis.get("mood", "cinematic")
        metaphor = visual_analysis.get("visual_metaphor", "")
        negative_cues = visual_analysis.get("negative_cues", [])

        prompt = f"""
Bạn là Tổng Đạo Diễn Hình Ảnh. Hãy tuyển chọn 1 bức ảnh phù hợp nhất từ kho tư liệu cho phân cảnh:
- Lời thoại: "{narration}"
- Ý niệm thị giác: "{metaphor}"
- Không khí (Mood): "{mood}"
- Tránh (Negative Cues): {json.dumps(negative_cues, ensure_ascii=False)}

DANH SÁCH ẢNH ỨNG VIÊN:
{json.dumps([{"index": i, "description": p["description"][:100]} for i, p in enumerate(pool)], ensure_ascii=False, indent=2)}

Nếu có ảnh phù hợp (điểm >= 6.0/10) -> winner_index = index.
Nếu tất cả đều không phù hợp -> winner_index = -1, should_fallback_to_ai = true.

TRẢ VỀ JSON DUY NHẤT:
{{
  "winner_index": 0,
  "confidence_score": 8.0,
  "rationale": "...",
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
                return winner_item, score, rationale, False
            else:
                return None, score, rationale, True

        return pool[0]["item"], 5.0, "Default heuristic pick", False
