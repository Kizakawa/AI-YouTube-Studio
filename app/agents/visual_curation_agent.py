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
import os
import re
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

from config.settings import settings
from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = list(GEMINI_PRIORITY_MODELS)

# Giới hạn số lệnh Gemini Vision chạy đồng thời (mặc định 4 luồng song song giúp tăng tốc gấp đôi)
import threading as _threading
import os as _os
_VISION_SEM = _threading.BoundedSemaphore(max(1, int(_os.getenv("VISION_CONCURRENCY", "4") or 4)))

_GENERIC_SUBJECTS = {
    "", "scene", "visual", "image", "photo", "thing", "things", "something", "people", "world",
    "reality", "life", "truth", "story", "moment", "time", "way", "fact", "facts",
}


_ABSTRACT_METAPHOR_WORDS = {
    "puppet", "puppeteer", "master", "manipulator", "manipulative", "toxic", "compliment",
    "trap", "ego", "narcissist", "gaslighting", "psychology", "psychological", "mind", "mental",
    "secret", "truth", "illusion", "deception", "matrix", "shadow", "cage", "strings",
}

_CAMERA_JARGON_RE = re.compile(
    r"\b(breathtaking|cinematography|cinematic|dramatic|macro\s+shot|wide[\s\-]+angle|low[\s\-]+angle|"
    r"high[\s\-]+angle|extreme\s+close[\s\-]+up|close[\s\-]+up\s+shot|tracking\s+shot|establishing\s+shot|"
    r"over[\s\-]+the[\s\-]+shoulder|dutch\s+angle|film\s+still|movie\s+still|shot\s+of|view\s+of|"
    r"8k|4k|hd|hdr|photorealistic|hyperrealistic|unreal\s+engine|octane\s+render|concept\s+art|"
    r"chiaroscuro|shadow\s+portrait|monochrome\s+aesthetic|monochrome\s+psychological\s+thriller\s+aesthetic)\b",
    re.IGNORECASE,
)


def _clean_search_phrase(q: str) -> str:
    """Loại bỏ từ khóa góc máy / kỹ thuật điện ảnh khỏi câu truy vấn tìm kiếm ảnh/video."""
    cleaned = _CAMERA_JARGON_RE.sub(" ", q or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-:")
    return cleaned


def _enforce_subject_queries(res: Dict[str, Any], subject: str) -> Dict[str, Any]:
    """
    Chuẩn hóa truy vấn tìm kiếm:
    - TÔN TRỌNG phân tích ngữ nghĩa của AI Agent, nhưng loại bỏ các từ khóa góc máy (Macro shot, Wide-angle...)
      và KHÔNG ghép các ẩn dụ trừu tượng (như 'manipulative puppet master') vào mọi câu tìm kiếm.
    - Chỉ bật creature_mode khi chủ đề thực sự là sinh vật/quái vật (is_creature_subject hoặc MYTHOLOGY_MONSTER).
    """
    is_ai = (res.get("analysis_source") == "ai")
    raw_core = _clean_search_phrase(str(res.get("core_visual_entity") or "").strip())
    core_entity = raw_core
    subject = _clean_search_phrase((subject or core_entity or "").strip())

    # Không dùng core_entity làm mỏ neo bắt buộc nếu nó chỉ là ẩn dụ tâm lý trừu tượng
    stripped_metaphor = ""
    core_words = set(re.findall(r"[a-z]+", core_entity.lower()))
    if core_words and (core_words & _ABSTRACT_METAPHOR_WORDS):
        stripped_metaphor = core_entity
        core_entity = ""
        res["core_visual_entity"] = ""
        if stripped_metaphor and stripped_metaphor.lower() in subject.lower():
            subject = re.sub(re.escape(stripped_metaphor), " ", subject, flags=re.IGNORECASE).strip() or "person"

    def _strip_metaphor_and_jargon(text: str) -> str:
        s = _clean_search_phrase(text)
        if stripped_metaphor:
            s = re.sub(re.escape(stripped_metaphor), " ", s, flags=re.IGNORECASE)
        s = re.sub(r"\b(manipulative\s+puppet\s+master|puppet\s+master)\b", " ", s, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", s).strip(" ,.-:")

    if not subject or subject.lower() in _GENERIC_SUBJECTS:
        return res

    from app.services.media.image_quality import subject_terms, creature_queries
    if core_entity and core_entity.lower() not in _GENERIC_SUBJECTS:
        core_terms = subject_terms(core_entity)
        subj_lower = subject.lower()
        if core_terms and not any(ct in subj_lower for ct in core_terms[:2]):
            subject = f"{core_entity} {subject}".strip()

    terms = subject_terms(core_entity or subject)
    short_subj = " ".join(subject_terms(subject)[:4]) if len(subject.split()) > 4 else subject
    anchor_subj = " ".join(terms[:3]) if terms else short_subj

    raw_queries = [q for q in (res.get("search_queries") or []) if isinstance(q, str) and q.strip()]
    queries = [_strip_metaphor_and_jargon(q) for q in raw_queries if _strip_metaphor_and_jargon(q)]

    def _has_subject(q: str) -> bool:
        ql = q.lower().replace("-", " ")
        return bool(terms) and any(t in ql for t in terms[:2])

    fixed: List[str] = []
    for q in queries:
        # Chỉ ghép anchor_subj nếu core_entity là thực thể cụ thể (động vật, nhân vật lịch sử, quái vật)
        if core_entity and not _has_subject(q):
            q = f"{anchor_subj} {q}" if len(q.split()) <= 4 else f"{anchor_subj} {' '.join(q.split()[:3])}"
        if q and q not in fixed:
            fixed.append(q)
    if short_subj and short_subj not in fixed:
        fixed.insert(1 if fixed else 0, short_subj)
    res["search_queries"] = fixed[:4]
    res["subject"] = subject

    # Xác định chế độ thị giác dựa trên quyết định ngữ nghĩa của AI (`visual_paradigm` hoặc cờ `creature_mode` từ @tool)
    paradigm = str(res.get("visual_paradigm") or "").upper()
    if is_ai and paradigm in ("DARK_HORROR_MYTHOLOGY", "MYTHOLOGY_MONSTER", "HOLLYWOOD_VFX_MOVIE", "CGI_CONCEPT_ART"):
        res["creature_mode"] = True
        res["preferred_engines"] = ["google"]
    else:
        res.setdefault("creature_mode", False)
        res["preferred_engines"] = ["google"]

    g_queries = [_strip_metaphor_and_jargon(q.replace('"', "").strip()) for q in (res.get("google_image_queries") or []) if isinstance(q, str) and q.strip()]
    fixed_g: List[str] = []
    for gq in g_queries:
        if not gq:
            continue
        gq = re.sub(r"\s+", " ", gq).strip()
        if core_entity and not _has_subject(gq):
            gq = f"{anchor_subj} {gq}"
        gq_words = gq.split()
        if len(gq_words) > 6:
            gq = " ".join(gq_words[:6])
        if gq and gq.lower() not in [x.lower() for x in fixed_g]:
            fixed_g.append(gq)

    if not fixed_g:
        primary_action = " ".join((fixed[0] if fixed else short_subj).split()[:5])
        fixed_g = [
            primary_action,
            f"{short_subj} epic 4k",
        ]

    res["google_image_queries"] = fixed_g[:4]
    if res.get("creature_mode"):
        res["creature_queries"] = creature_queries(subject, action_query=fixed_g[0] if fixed_g else "")
    return res


def _apply_creature_mode(res: Dict[str, Any], narration: str = "", topic: str = "", channel_niche: str = "") -> Dict[str, Any]:
    """
    Giữ hàm tương thích ngược. Không còn dò từ khóa tĩnh; chỉ đồng bộ `creature_queries` nếu
    LLM Agent đã chọn paradigm quái vật hoặc bật `creature_mode=True` qua `@tool`.
    """
    from app.services.media.image_quality import creature_queries
    if res.get("creature_mode"):
        subject = str(res.get("subject") or "").strip()
        ai_qs = [q for q in (res.get("google_image_queries") or []) if q]
        c_qs = creature_queries(subject, topic=topic, narration=narration, action_query=ai_qs[0] if ai_qs else "")
        merged_qs: List[str] = []
        for q in (ai_qs + c_qs):
            if q and q.lower() not in [x.lower() for x in merged_qs]:
                merged_qs.append(q)
        res["creature_queries"] = merged_qs[:4]
        res["google_image_queries"] = merged_qs[:4]
    return res


class VisualCurationAgent:
    """
    Tác nhân Tuyển chọn & Giám sát Thị giác Thông minh (Smart Visual Curation Agent).
    Đóng vai trò Trợ lý Đạo diễn Tuyển chọn Tư liệu (Visual Researcher & Curation Director).
    """

    def __init__(self):
        import requests
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None
        self.session = requests.Session()

    def _call_gemini_json(
        self,
        prompt: str,
        timeout: int = 15,
        images: Optional[List[Tuple[str, str]]] = None,
        models: Optional[List[str]] = None,
        max_attempts: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Gọi Gemini API trả về JSON với KeyRotator ưu tiên Model mạnh nhất trước.
        images: danh sách (mime_type, base64_data) để gửi kèm (Gemini Vision).
        """
        if not self.rotator:
            return None

        import requests
        parts: List[Dict[str, Any]] = [{"text": prompt}]
        for mime, b64 in (images or []):
            parts.append({"inline_data": {"mime_type": mime, "data": b64}})

        conn_errors = 0
        for model, key in self.rotator.iter_best_model_key_pairs(models or AVAILABLE_MODELS, max_total_attempts=max_attempts):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            payload = {
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "temperature": 0.2,
                    "responseMimeType": "application/json",
                }
            }
            try:
                res = self.session.post(url, json=payload, timeout=timeout)
                if res.status_code == 200:
                    data = res.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        raw_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                        if raw_text:
                            return json.loads(raw_text)
                elif res.status_code in (429, 403, 404, 503):
                    self.rotator.mark_model_rate_limited(key, model, status_code=res.status_code, error_text=res.text[:800])
                    continue
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                conn_errors += 1
                logger.debug(f"[VisualCurationAgent] Network error on {model}: {e}")
                if conn_errors >= 2:
                    logger.warning("[VisualCurationAgent] Mất kết nối tới Gemini → bỏ qua, dùng heuristic.")
                    return None
                continue
            except Exception as e:
                logger.debug(f"[VisualCurationAgent] Gemini call error on {model}: {e}")
                continue

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # PHASE 1: PHÂN TÍCH NGỮ CẢNH & Ý NIỆM THỊ GIÁC (BATCH & SINGLE SCENE)
    # ──────────────────────────────────────────────────────────────────────────

    def analyze_scenes_batch(
        self,
        scenes: List[Dict[str, Any]],
        topic: str = "",
        primary_entity: Optional[str] = None,
        entity_type: str = "general",
        channel_niche: str = "general",
    ) -> Dict[int, Dict[str, Any]]:
        """
        Phân tích đồng loạt toàn bộ các phân cảnh trong 1 lần gọi Gemini duy nhất:
        - Tiết kiệm ~85% số lệnh gọi LLM, giảm độ trễ và tránh lỗi 429 Quota.
        - Thiết kế truy vấn TOP SEARCH GOOGLE IMAGES ngắn gọn (2-5 từ tiếng Anh), ấn tượng nhất (PEAK / WOW factor)
          và tuyệt đối KHÔNG trùng lặp giữa các phân cảnh liền kề.
        """
        if not scenes:
            return {}

        from app.services.media.image_quality import extract_subject
        scenes_payload = []
        for idx, s in enumerate(scenes):
            scenes_payload.append({
                "scene_id": s.get("scene_id", idx + 1),
                "narration": s.get("narration", ""),
                "visual_keyword": s.get("visual_keyword", ""),
                "stock_query": s.get("stock_query", ""),
                "visual_prompt": s.get("visual_prompt", ""),
            })

        prompt = f"""
Bạn là Tổng Đạo Diễn Hình Ảnh & Chuyên Gia Truy Vấn Top Search Google Images (Viral Visual & Google Search Director) cho YouTube Shorts triệu view.
Nhiệm vụ của bạn là đọc hiểu sâu mạch truyện của toàn bộ {len(scenes_payload)} phân cảnh và thiết kế bộ từ khóa tìm kiếm `google_image_queries` NGẮN GỌN, ĐẮT GIÁ NHẤT (2–5 từ tiếng Anh) để công cụ **Top Search Google Images** trả ra những bức ảnh PEAK / WOW / ẤN TƯỢNG NHẤT trên mạng, khớp hoàn hảo với lời thoại từng cảnh.

THÔNG TIN VIDEO:
- Chủ đề: "{topic}"
- Niche kênh: "{channel_niche}"
- Thực thể chính (nếu đã biết): "{primary_entity or 'Tự suy luận từ chủ đề & kịch bản'}" (Loại: {entity_type})
- Danh sách phân cảnh:
{json.dumps(scenes_payload, ensure_ascii=False, indent=2)}

NGUYÊN TẮC VÀNG KHI TẠO TRUY VẤN TOP SEARCH GOOGLE IMAGES (`google_image_queries`):
1. Xác định "core_visual_entity" (Chủ thể Thị giác Vật lý Trung tâm của toàn video bằng tiếng Anh, 1-3 từ):
   - CHỈ ĐẶT "core_visual_entity" khi video nói về 1 sinh vật, động vật, nhân vật lịch sử hoặc vật thể cụ thể xuyên suốt (vd: "kraken", "megalodon", "harpy eagle", "julius caesar").
   - NẾU video thuộc chủ đề Tâm lý học (Psychology), Hành vi con người, Mối quan hệ, Triết học, Tài chính, Phát triển bản thân: BẮT BUỘC đặt `"core_visual_entity": ""` (chuỗi rỗng)! TUYỆT ĐỐI CẤM bịa ra ẩn dụ trừu tượng kiểu "manipulative puppet master", "toxic trap", "mind prison" hay đạo cụ nghĩa đen ("knife", "blade", "puppet strings", "cage")!
2. Xác định "visual_paradigm" (Bản chất thị giác của từng cảnh dựa trên ngữ nghĩa chủ đề):
   - "CINEMATIC_REALISM": Tâm lý học, thao túng tâm lý, ngôn ngữ cơ thể, cảm xúc con người, giao tiếp xã hội, tội phạm, tài chính, triết học đời sống → ưu tiên ảnh điện ảnh tâm lý / Dark Noir / Chiaroscuro sắc lạnh hoặc Seinen Thriller cực ngầu (vd: `"man cold calculating stare noir"`, `"johan liebert monster anime"`, `"woman anxious dark room"`, `"intense eye contact shadow"`). TUYỆT ĐỐI CẤM ảnh văn phòng công sở cười tươi giả tạo (cheesy corporate stock photo)!
   - "REAL_WORLD_ACTION": Động vật hoang dã có thật, thiên nhiên có thật, giải cứu động vật, hành động vật lý đời thực → truy vấn nhiếp ảnh kịch tính sắc nét (vd: `"harpy eagle talons close up"`, `"harpy eagle hunting monkey"`).
   - "HISTORICAL_ARCHIVE": Nhân vật lịch sử có thật, sự kiện lịch sử, cổ vật/di tích thật → truy vấn chân dung/tượng/tranh lịch sử hoành tráng (vd: `"julius caesar marble bust"`, `"roman senate assassination painting"`).
   - "SCIENTIFIC_3D": Vũ trụ, thiên văn, giải phẫu, vi sinh, vật lý lượng tử → truy vấn hình ảnh không gian/khoa học ngoạn mục (vd: `"supermassive black hole accretion disk"`, `"james webb nebula pillar"`).
   - "HOLLYWOOD_VFX_MOVIE": Thần thoại, quái vật, thủy quái, kinh dị siêu nhiên, viễn tưởng (Sci-Fi) → truy vấn NGẮN GỌN, TRỰC DIỆN (2–5 từ tiếng Anh) giúp Top Search Google Images trả về ngay kiệt tác CGI / VFX / Thalassophobia đỉnh nhất (vd: Cảnh 1: `"giant kraken ocean"`, Cảnh 2: `"kraken attacking ship"`, Cảnh 3: `"deep sea submarine spotlight"`, Cảnh 4: `"giant kraken tentacles"`, Cảnh 5: `"leviathan deep ocean monster"`).
   - "STYLIZED_ANIMATION": Hoạt hình 2D châm biếm, minh họa.
3. Chọn "preferred_engines": Luôn đặt `["google"]` để lấy trực tiếp từ Top Search Google Images.
4. CÔNG THỨC TẠO `google_image_queries` & `search_queries` (CỰC KỲ QUAN TRỌNG):
   - Độ dài chuẩn: **2 đến 5 từ tiếng Anh** (`[Chủ thể cụ thể] + [Hành động / Bối cảnh / Biểu cảm đắt giá của riêng cảnh đó]`).
   - Truy vấn ngắn gọn, súc tích (2–5 từ) luôn cho kết quả Top #1–#10 Google Images đẹp và chuẩn xác gấp nhiều lần câu văn dài dòng!
   - KHÔNG nhồi nhét thuật ngữ kỹ thuật máy quay (`"8k"`, `"9:16"`, `"macro shot"`, `"wide angle"`, `"camera pans"`, `"photorealistic"`, `"chiaroscuro"`) vào `google_image_queries`.
   - **ĐA DẠNG HÓA 100% GIỮA CÁC CẢNH**: Mỗi phân cảnh từ Cảnh 1 đến Cảnh {len(scenes_payload)} BẮT BUỘC phải khai thác một GÓC NHÌN / HÀNH ĐỘNG / CHI TIẾT KHÁC NHAU khớp với lời thoại của cảnh đó. TUYỆT ĐỐI KHÔNG lặp lại cùng một câu truy vấn đầu tiên (`google_image_queries[0]`) ở 2 cảnh khác nhau!

TRẢ VỀ JSON DUY NHẤT THEO ĐỊNH DẠNG:
{{
  "core_visual_entity": "Tên tiếng Anh của thực thể vật lý cụ thể (hoặc chuỗi rỗng '' nếu là chủ đề tâm lý/con người/trừu tượng)",
  "overall_visual_paradigm": "CINEMATIC_REALISM | REAL_WORLD_ACTION | HISTORICAL_ARCHIVE | SCIENTIFIC_3D | HOLLYWOOD_VFX_MOVIE | STYLIZED_ANIMATION",
  "scenes_analysis": [
    {{
      "scene_id": 1,
      "core_visual_entity": "Thực thể vật lý cụ thể hoặc ''",
      "visual_paradigm": "CINEMATIC_REALISM | REAL_WORLD_ACTION | HISTORICAL_ARCHIVE | SCIENTIFIC_3D | HOLLYWOOD_VFX_MOVIE | STYLIZED_ANIMATION",
      "preferred_engines": ["google"],
      "mood": "dark_ominous | tense_mysterious | epic_heroic | vibrant_energetic | melancholy_thoughtful | scientific_curiosity",
      "narrative_subtext": "Ngụ ý cảm xúc của cảnh (1 câu ngắn)",
      "visual_metaphor": "Mô tả hình ảnh cụ thể, ấn tượng trên màn hình 9:16 khớp lời thoại",
      "subject": "Chủ thể + hành động/đặc điểm cụ thể của cảnh bằng tiếng Anh (2-4 từ)",
      "production_method": "IMAGE_TO_VIDEO | STOCK_FOOTAGE | TEXT_TO_VIDEO | REAL_ARCHIVE",
      "tool_choice": "ai_image_to_video | pexels_video_clip | ai_text_to_video | real_archive_photo",
      "motion_level": "HIGH_MOTION | MEDIUM_MOTION | STATIC_DETAILED",
      "search_queries": ["iconic 2-5 word query 1", "iconic 2-5 word query 2", "iconic 2-5 word query 3"],
      "google_image_queries": [
        "Primary iconic Top Search Google query matching this exact scene (2-5 English words)",
        "Alternative high-impact Google query for this scene's action/scale (2-5 English words)",
        "Third distinct visual angle query for this scene (2-5 English words)"
      ],
      "negative_cues": ["text overlay", "watermark", "meme", "diagram", "chart", "infographic", "blurry", "low resolution", "toy", "plush"],
      "cinematic_ai_prompt": "Detailed 8K vertical 9:16 photorealistic IMAX visual prompt in English describing the exact scene, dramatic lighting, atmosphere physics, 35mm film grain"
    }}
  ]
}}
"""
        res = self._call_gemini_json(prompt, timeout=22)
        result_map: Dict[int, Dict[str, Any]] = {}
        if res and isinstance(res.get("scenes_analysis"), list):
            video_core_entity = str(res.get("core_visual_entity") or primary_entity or "").strip()
            video_paradigm = str(res.get("overall_visual_paradigm") or "").strip()
            used_primary_queries: set = set()

            for item in res["scenes_analysis"]:
                if not isinstance(item, dict):
                    continue
                sid = item.get("scene_id")
                if sid is None:
                    continue
                sid_int = int(sid)
                item["scene_id"] = sid_int
                item["is_hook_scene"] = (sid_int == 1)
                item["analysis_source"] = "ai"
                if not item.get("core_visual_entity") and video_core_entity:
                    item["core_visual_entity"] = video_core_entity
                if not item.get("visual_paradigm") and video_paradigm:
                    item["visual_paradigm"] = video_paradigm
                sc_orig = next((x for x in scenes_payload if x["scene_id"] == sid), {})
                narr = sc_orig.get("narration", "")
                orig_vk = _clean_search_phrase(str(sc_orig.get("visual_keyword") or "").strip())
                if orig_vk and 2 <= len(orig_vk.split()) <= 6:
                    existing_gqs = list(item.get("google_image_queries") or [])
                    if orig_vk.lower() not in [str(x).lower() for x in existing_gqs]:
                        existing_gqs.append(orig_vk)
                    item["google_image_queries"] = existing_gqs

                subj = str(item.get("subject") or item.get("core_visual_entity") or "").strip() or extract_subject(narr, primary_entity)
                item = _enforce_subject_queries(item, subj)
                item = _apply_creature_mode(item, narr, topic, channel_niche=channel_niche)

                # Đảm bảo mỗi phân cảnh trong cùng 1 video có truy vấn ưu tiên #1 (google_image_queries[0]) KHÁC NHAU
                for q_key in ("google_image_queries", "creature_queries", "search_queries"):
                    q_list = list(item.get(q_key) or [])
                    if len(q_list) > 1 and q_list[0].lower() in used_primary_queries:
                        non_used = [q for q in q_list if q.lower() not in used_primary_queries]
                        used_ones = [q for q in q_list if q.lower() in used_primary_queries]
                        item[q_key] = non_used + used_ones
                top_q = (item.get("google_image_queries") or item.get("search_queries") or [""])[0].strip().lower()
                if top_q:
                    used_primary_queries.add(top_q)

                result_map[sid_int] = item
            if result_map:
                logger.info(
                    f"⚡ [VisualCuration AI] Đã phân tích ngữ nghĩa đồng loạt {len(result_map)}/{len(scenes_payload)} cảnh "
                    f"(core_entity='{video_core_entity}', paradigm='{video_paradigm}')!"
                )
        return result_map

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
        Phân tích chuyên sâu ngữ cảnh và bản chất thị giác của phân cảnh bằng AI Agent:
        - Tự động suy luận core_visual_entity, visual_paradigm, preferred_engines=["google"] và google_image_queries
          ngắn gọn (2-5 từ tiếng Anh) chuẩn Top Search Google Images.
        """
        prompt = f"""
Bạn là Tổng Đạo Diễn Hình Ảnh & Chuyên Gia Truy Vấn Top Search Google Images (Viral Visual & Google Search Director) cho YouTube Shorts triệu view.
Hãy phân tích sâu chủ đề và lời thoại phân cảnh dưới đây để tạo bộ từ khóa tìm kiếm `google_image_queries` NGẮN GỌN, ẤN TƯỢNG NHẤT (2–5 từ tiếng Anh) giúp **Top Search Google Images** trả về những bức ảnh PEAK / WOW / CÂU VIEW NHẤT, khớp chặt chẽ với nội dung phân cảnh.

THÔNG TIN ĐẦU VÀO:
- Chủ đề video: "{topic}"
- Niche kênh: "{channel_niche}"
- Phân cảnh số: {scene_id}/{total_scenes}
- Thực thể chính (nếu đã biết): "{primary_entity or 'Tự suy luận từ chủ đề & lời thoại'}" (Loại: {entity_type})
- Lời thoại phân cảnh: "{narration}"
- Ngữ cảnh xung quanh: "{surrounding_context}"

NGUYÊN TẮC TƯ DUY ĐẠO DIỄN & TẠO TRUY VẤN GOOGLE IMAGES:
1. "core_visual_entity": CHỈ ĐẶT khi video nói về 1 sinh vật, động vật, nhân vật lịch sử hoặc vật thể cụ thể (vd: "kraken", "megalodon", "harpy eagle", "julius caesar"). Nếu video nói về Tâm lý học, Hành vi con người, Mối quan hệ, Triết học, Tài chính -> BẮT BUỘC để chuỗi rỗng `""`!
2. "visual_paradigm": Chọn đúng bản chất thị giác của cảnh:
   - "CINEMATIC_REALISM": Tâm lý học, thao túng tâm lý, cảm xúc con người, giao tiếp xã hội, tội phạm, đời sống -> ưu tiên ảnh biểu cảm tâm lý Dark Noir / Chiaroscuro hoặc Seinen Thriller sắc lạnh -> `["google"]`.
   - "REAL_WORLD_ACTION": Động vật hoang dã thật, thiên nhiên thật, hành động đời thực -> `["google"]`.
   - "HISTORICAL_ARCHIVE": Nhân vật/sự kiện lịch sử có thật -> `["google"]`.
   - "SCIENTIFIC_3D": Vũ trụ, thiên văn, giải phẫu, tế bào, vật lý -> `["google"]`.
   - "HOLLYWOOD_VFX_MOVIE": Thần thoại, quái vật, thủy quái, kinh dị siêu nhiên, viễn tưởng -> ưu tiên truy vấn 2–5 từ tiếng Anh trả về kiệt tác CGI / VFX / Thalassophobia ấn tượng nhất trên Top Search Google Images (vd: `"giant kraken ocean"`, `"kraken attacking ship"`, `"deep sea submarine spotlight"`) -> `["google"]`.
   - "STYLIZED_ANIMATION": Hoạt hình 2D châm biếm, minh họa.
3. "preferred_engines": Luôn chọn `["google"]`.
4. "search_queries" & "google_image_queries": 3 câu truy vấn NGẮN GỌN, TRỰC DIỆN, ĐẮT GIÁ (**2 đến 5 từ tiếng Anh**) mô tả đúng `[Chủ thể cụ thể] + [Hành động / Quy mô / Biểu cảm của cảnh này]`. KHÔNG nhồi nhét từ khóa kỹ thuật máy quay (`"8k"`, `"9:16"`, `"macro shot"`, `"wide angle"`, `"chiaroscuro"`).

HÃY PHÂN TÍCH VÀ TRẢ VỀ JSON DUY NHẤT:
{{
  "core_visual_entity": "Thực thể vật lý cụ thể hoặc ''",
  "visual_paradigm": "CINEMATIC_REALISM | REAL_WORLD_ACTION | HISTORICAL_ARCHIVE | SCIENTIFIC_3D | HOLLYWOOD_VFX_MOVIE | STYLIZED_ANIMATION",
  "preferred_engines": ["google"],
  "mood": "dark_ominous | tense_mysterious | epic_heroic | vibrant_energetic | melancholy_thoughtful | scientific_curiosity",
  "narrative_subtext": "Ngụ ý và cảm xúc sâu xa mà phân cảnh này muốn truyền tải (1-2 câu tiếng Việt)",
  "visual_metaphor": "Mô tả bối cảnh hình ảnh ấn tượng trên màn hình 9:16 khớp lời thoại",
  "subject": "Chủ thể + hành động cụ thể của cảnh bằng tiếng Anh (2-4 từ)",
  "production_method": "IMAGE_TO_VIDEO | STOCK_FOOTAGE | TEXT_TO_VIDEO | REAL_ARCHIVE",
  "tool_choice": "ai_image_to_video | pexels_video_clip | ai_text_to_video | real_archive_photo",
  "motion_level": "HIGH_MOTION | MEDIUM_MOTION | STATIC_DETAILED",
  "search_queries": [
    "iconic 2-5 word English query 1",
    "iconic 2-5 word English query 2",
    "iconic 2-5 word English query 3"
  ],
  "google_image_queries": [
    "Primary iconic Top Search Google query matching this scene (2-5 từ tiếng Anh)",
    "Alternative high-impact Google query for this scene's action/scale (2-5 từ tiếng Anh)",
    "Third distinct visual angle query for this scene (2-5 từ tiếng Anh)"
  ],
  "negative_cues": [
    "text overlay, watermark, meme, diagram, chart, infographic, blurry, low resolution, toy, plush"
  ],
  "cinematic_ai_prompt": "Detailed 8K vertical 9:16 photorealistic IMAX visual prompt in English, dramatic lighting and atmosphere physics, 35mm film grain."
}}
"""
        res = self._call_gemini_json(prompt)
        if res and "search_queries" in res and res.get("visual_metaphor"):
            from app.services.media.image_quality import extract_subject
            res["scene_id"] = int(scene_id)
            res["is_hook_scene"] = (int(scene_id) == 1)
            res["analysis_source"] = "ai"
            subj = str(res.get("subject") or res.get("core_visual_entity") or "").strip() or extract_subject(narration, primary_entity)
            res = _enforce_subject_queries(res, subj)
            res = _apply_creature_mode(res, narration, topic, channel_niche=channel_niche)
            logger.info(
                f"🎬 [VisualCuration AI] Cảnh {scene_id} [{res.get('visual_paradigm')} / {res.get('production_method')}] "
                f"entity='{res.get('core_visual_entity', '')}' subject='{res.get('subject', '')}' "
                f"queries={res.get('google_image_queries', [])[:2]}"
            )
            return res

        # Heuristic fallback an toàn nếu Gemini tạm ngưng
        res = self._heuristic_scene_analysis(narration, topic, primary_entity, entity_type, scene_id, total_scenes)
        res["scene_id"] = int(scene_id)
        res["is_hook_scene"] = (int(scene_id) == 1)
        from app.services.media.image_quality import extract_subject
        subj = extract_subject(narration, primary_entity)
        if not subj or subj.lower() in _GENERIC_SUBJECTS:
            # Cảnh bị cắt giữa câu ("High above,") → lấy chủ thể từ ngữ cảnh xung quanh
            subj = extract_subject(surrounding_context) if surrounding_context else ""
        if subj and subj.lower() not in _GENERIC_SUBJECTS and res.get("production_method") != "REAL_ARCHIVE":
            generic = list(res.get("search_queries") or [])
            res["search_queries"] = [subj, f"{subj} close up"] + generic[:1]
            res["google_image_queries"] = [subj, f"{subj} close up", f"{subj} epic"]
            res["subject"] = subj
            res = _apply_creature_mode(res, narration, topic, channel_niche=channel_niche)
            logger.info(f"🎬 [VisualCuration] Cảnh {scene_id} (heuristic) subject='{subj}' queries={res['search_queries']}")
        return res

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
                q = ["dark deep ocean abyssal underwater", "submarine spotlight pitch black water", "terrifying deep sea creature silhouette", "deep ocean trench"]
                ai_p = f"Masterpiece terrifying thalassophobia vertical hook shot of deep sea abyss exploration, submarine xenon beam illuminating colossal shadow in pitch-black water, 8k, 9:16"
            else:
                q = ["cinematic intense hook intro", "dramatic slow motion cinematic", "expressive close up emotion"]
                ai_p = f"Cinematic 8k dramatic vertical hook shot of {topic or 'visual hook'}, vivid atmospheric lighting, rich contrast, 9:16"
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
            q = ["thoughtful thinker portrait", "expressive human emotion face", "focused eyes looking camera"]
            ai_p = f"Cinematic 8k vertical portrait, highly detailed facial expression, crisp studio lighting, rich depth of field, 9:16"
        elif is_dark:
            method = "STOCK_FOOTAGE"
            tool = "pexels_video_clip"
            motion = "HIGH_MOTION"
            mood = "dark_ominous"
            q = ["dramatic cinematic shadows", "atmospheric tension scene", "dramatic storm sky"]
            ai_p = f"Cinematic atmospheric tension scene, dramatic volumetric lighting, rich color contrast, highly detailed 8k, 9:16"
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
            "negative_cues": ["cartoon", "low quality", "blurry", "text overlay", "distorted faces", "muddy dark tones"],
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

        # Ưu tiên clip dọc lên trước, tối đa 6 ứng viên cho Agent phân tích
        valid_candidates.sort(key=lambda c: 0 if (c["height"] or 0) >= (c["width"] or 0) else 1)
        eval_pool = valid_candidates[:6]

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
                logger.info(
                    f"🎬 [VisualCuration] ℹ️ Gemini chấm stock videos mức trung bình (Score: {score}/10: {rationale}) -> "
                    f"Chuyển sang Heuristic B-Roll Selector để giữ video chuyển động thật thay vì ảnh tĩnh!"
                )
                return self._heuristic_curate_videos(eval_pool, visual_analysis)

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

            score = 7.4 if (c.get("height", 0) or 0) >= 1280 else 6.5
            # Thưởng điểm nếu slug chứa các từ khóa trong search_queries
            for q in queries:
                for w in q.split():
                    if len(w) > 3 and w in slug:
                        score += 1.0

            if score > best_score:
                best_score = min(score, 9.5)
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

    @staticmethod
    def _inspect_candidate_sharpness(cand: Dict[str, Any], timeout: float = 4.0) -> Optional[Dict[str, Any]]:
        """
        Tải thử ảnh ứng viên và đo trực tiếp ĐỘ SẮC NÉT BIÊN CẠNH (Edge Sharpness), ĐỘ PHÂN GIẢI & TƯƠNG PHẢN bằng PIL.
        Loại bỏ ngay ảnh mờ nhòe (sharpness < 6.0), ảnh quá nhỏ (< 400px) hoặc ảnh hỏng/đơn sắc.
        """
        import io as _io
        import requests
        from PIL import Image, ImageFilter, ImageStat

        urls = [u for u in (cand.get("url"), cand.get("fallback_url"), cand.get("thumbnail")) if u]
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        for target_u in urls:
            try:
                headers = {"User-Agent": ua, "Accept": "image/*,*/*;q=0.8"}
                if cand.get("page_url"):
                    headers["Referer"] = str(cand["page_url"])
                res = requests.get(target_u, timeout=timeout, headers=headers)
                if res.status_code != 200 or not res.content or len(res.content) < 2500:
                    continue
                with Image.open(_io.BytesIO(res.content)) as im:
                    w, h = im.size
                    if min(w, h) < 540 or max(w / max(h, 1), h / max(w, 1)) > 2.6:
                        continue
                    gray = im.convert("L").resize((256, 256), Image.Resampling.BILINEAR)
                    g_stat = ImageStat.Stat(gray)
                    std_con = float(g_stat.stddev[0])
                    lum_mean = float(g_stat.mean[0])
                    if std_con < 18.0 or lum_mean < 24.0 or lum_mean > 228.0:
                        continue
                    edges = gray.filter(ImageFilter.FIND_EDGES)
                    sharpness = float(ImageStat.Stat(edges).mean[0])
                    from app.services.video_memory_rag import VideoMemoryRAG
                    has_bad_text, _ = VideoMemoryRAG._has_embedded_text_or_collage_artifacts(res.content)
                    if sharpness < 5.8 or has_bad_text:
                        continue
                    return {
                        "width": w,
                        "height": h,
                        "sharpness": round(sharpness, 2),
                        "contrast": round(std_con, 2),
                        "verified_url": target_u,
                    }
            except Exception:
                continue
        return None

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
        Tuyển chọn ảnh trực tiếp dựa trên THỨ HẠNG TOP SEARCH GOOGLE IMAGES + KIỂM ĐỊNH ĐỘ SẮC NÉT & ĐỘ PHÂN GIẢI:
        - Đã bỏ bước kiểm tra độ liên quan bằng AI Vision/LLM (tránh từ chối nhầm ảnh đẹp từ Google Images và tiết kiệm 10-15s/cảnh).
        - Đo trực tiếp độ sắc nét biên cạnh (sharpness), độ phân giải thực tế (width x height) và độ tương phản của Top ứng viên.
        """
        from concurrent.futures import ThreadPoolExecutor

        used = used_urls or set()
        valid = [c for c in candidates if c.get("url") and c.get("url") not in used]
        if not valid:
            return None, 0.0, "Không có ứng viên ảnh nào khả dụng.", True

        top_pool = valid[:6]
        with ThreadPoolExecutor(max_workers=min(6, len(top_pool))) as ex:
            metrics_list = list(ex.map(self._inspect_candidate_sharpness, top_pool))

        scored_sharp: List[Tuple[float, int, Dict[str, Any]]] = []
        for idx, (cand, m) in enumerate(zip(top_pool, metrics_list)):
            if not m:
                continue
            w, h = m["width"], m["height"]
            sharpness = m["sharpness"]
            contrast = m["contrast"]
            g_rank = int(cand.get("_google_rank") or 999)
            h_score = float(cand.get("_hscore") or 7.0)

            # Điểm tổng hợp: Độ sắc nét + Độ phân giải HD/4K + Thứ hạng Top Search Google Images
            res_bonus = 1.4 if min(w, h) >= 1000 else (1.0 if min(w, h) >= 720 else 0.4)
            sharp_bonus = min(2.4, sharpness / 10.0)
            rank_bonus = 1.5 if g_rank <= 3 else (1.1 if g_rank <= 8 else (0.6 if g_rank <= 15 else 0.0))
            total_score = min(9.9, round(4.8 + res_bonus + sharp_bonus + rank_bonus + (h_score * 0.12), 2))
            scored_sharp.append((total_score, -idx, {**cand, "_sharp_metrics": m}))

        if scored_sharp:
            scored_sharp.sort(reverse=True)
            best_score, _, winner_item = scored_sharp[0]
            m = winner_item.pop("_sharp_metrics", {})
            alt_urls = [
                item.get("url")
                for _, _, item in scored_sharp[1:5]
                if item.get("url") and item.get("url") != winner_item.get("url")
            ]
            if alt_urls:
                winner_item["_vision_alternates"] = alt_urls
            g_rank = int(winner_item.get("_google_rank") or 999)
            rank_str = f"Top #{g_rank} Google Images" if g_rank < 900 else winner_item.get("source", "web")
            rationale = (
                f"[{rank_str} · Sắc nét đạt chuẩn] Độ nét={m.get('sharpness')}, "
                f"Tương phản={m.get('contrast')}, Độ phân giải={m.get('width')}x{m.get('height')}px"
            )
            winner_item["curation_score"] = best_score
            winner_item["curation_rationale"] = rationale
            logger.info(f"📸 [SharpnessCuration] ✅ Chọn ảnh {rank_str} | Điểm độ nét: {best_score}/10 | {rationale}")
            return winner_item, best_score, rationale, False

        # Nếu các server chặn tải kiểm thử nhanh (timeout), lấy ngay ứng viên Top #1 Google Images có metadata tốt nhất
        fallback_winner = valid[0]
        fb_score = float(fallback_winner.get("_hscore") or 7.5)
        fb_rat = "Chọn trực tiếp Top #1 Google Images (bỏ qua bước kiểm tra độ liên quan)"
        fallback_winner["curation_score"] = fb_score
        fallback_winner["curation_rationale"] = fb_rat
        return None, 0.0, "Không có ứng viên ảnh nào đạt chuẩn độ nét (>=5.8) và sạch watermark", True
