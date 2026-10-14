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
    r"8k|4k|hd|hdr|photorealistic|hyperrealistic|unreal\s+engine|octane\s+render|concept\s+art)\b",
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

    from app.services.media.image_quality import subject_terms, is_creature_subject, creature_queries
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

    # Xác định chế độ thị giác: chỉ bật creature_mode khi thực sự là quái vật / sinh vật thần thoại
    paradigm = str(res.get("visual_paradigm") or "").upper()
    actual_creature = is_creature_subject(subject)
    if is_ai:
        if paradigm in ("DARK_HORROR_MYTHOLOGY", "MYTHOLOGY_MONSTER") or (paradigm == "CGI_CONCEPT_ART" and actual_creature):
            res["creature_mode"] = True
            if not res.get("preferred_engines"):
                res["preferred_engines"] = ["artstation", "bing", "wallhaven", "ddgs"]
        else:
            res["creature_mode"] = False
            if not res.get("preferred_engines"):
                res["preferred_engines"] = ["bing", "ddgs", "wallhaven"]
    else:
        if actual_creature:
            c_qs = creature_queries(subject)
            res["google_image_queries"] = c_qs[:3]
            res["creature_mode"] = True
            res["creature_queries"] = c_qs
            return res

    g_queries = [_strip_metaphor_and_jargon(q.replace('"', "").strip()) for q in (res.get("google_image_queries") or []) if isinstance(q, str) and q.strip()]
    fixed_g: List[str] = []
    for gq in g_queries:
        if not gq:
            continue
        if core_entity and not _has_subject(gq):
            gq = f"{anchor_subj} {gq}"
        gq_words = gq.split()
        if len(gq_words) > 7:
            gq = " ".join(gq_words[:7])
        if gq not in fixed_g:
            fixed_g.append(gq)

    if not fixed_g:
        primary_action = " ".join((fixed[0] if fixed else short_subj).split()[:5])
        fixed_g = [
            f"{primary_action} realistic photography",
            f"{short_subj} cinematic portrait",
        ]

    res["google_image_queries"] = fixed_g[:4]
    if res.get("creature_mode"):
        res["creature_queries"] = fixed_g[:4]
    return res


def _apply_creature_mode(res: Dict[str, Any], narration: str = "", topic: str = "") -> Dict[str, Any]:
    """
    Dự phòng ngoại tuyến (Offline Heuristic Fallback):
    Nếu kết quả ĐÃ được AI Agent phân tích (analysis_source == 'ai'), tuyệt đối giữ nguyên quyết định của AI!
    Chỉ kích hoạt bộ từ khóa dự phòng khi mất kết nối LLM hoặc AI bỏ trống hoàn toàn google_image_queries.
    """
    if res.get("analysis_source") == "ai" and res.get("google_image_queries"):
        return res

    from app.services.media.image_quality import (
        is_creature_subject, creature_queries, extract_core_creature, _SPECIFIC_CREATURES, _norm_text,
    )
    subject = str(res.get("subject") or "").strip()
    if is_creature_subject(subject, narration, topic):
        core_monster = extract_core_creature(subject, narration, topic)
        norm_subj = f" {_norm_text(subject)} "
        if core_monster and f" {core_monster} " not in norm_subj:
            if not any(f" {sp} " in norm_subj for sp in _SPECIFIC_CREATURES):
                subject = f"giant {core_monster} {subject}".strip() if subject else f"giant {core_monster}"
                res["subject"] = subject
        elif not subject:
            subject = f"giant {core_monster}" if core_monster else "terrifying deep sea monster"
            res["subject"] = subject

        c_qs = creature_queries(subject, topic=topic, narration=narration)
        res["creature_mode"] = True
        res["creature_queries"] = c_qs
        res["google_image_queries"] = c_qs[:3]
        logger.info(f"🐙 [VisualCuration Fallback] Chế độ QUÁI VẬT RÙNG RỢN cho '{subject}': {res['creature_queries']}")
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
                    self.rotator.mark_model_rate_limited(key, model, status_code=res.status_code, error_text=res.text[:250])
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
        - Nhìn tổng thể mạch truyện để đảm bảo KHÔNG trùng lặp truy vấn tìm kiếm hoặc góc máy giữa các cảnh liền kề.
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
Bạn là Tổng Đạo Diễn Nghệ Thuật & Chuyên Gia Tìm Kiếm Hình Ảnh Đa Động Cơ (Autonomous Visual Intelligence & Search Director) cho YouTube Shorts triệu view.
Nhiệm vụ của bạn là TỰ ĐỘNG HIỂU SÂU BẢN CHẤT NGỮ NGHĨA của bất kỳ chủ đề hay xu hướng nào (không phụ thuộc từ khóa cố định) và thiết kế kế hoạch tìm kiếm hình ảnh BẮT MẮT, CÂU VIEW NHẤT cho toàn bộ {len(scenes_payload)} phân cảnh.

THÔNG TIN VIDEO:
- Chủ đề: "{topic}"
- Niche kênh: "{channel_niche}"
- Thực thể chính (nếu đã biết): "{primary_entity or 'Tự suy luận từ chủ đề & kịch bản'}" (Loại: {entity_type})
- Danh sách phân cảnh:
{json.dumps(scenes_payload, ensure_ascii=False, indent=2)}

NHIỆM VỤ TƯ DUY ĐẠO DIỄN TỰ HÀNH (AUTONOMOUS SEMANTIC REASONING):
1. Xác định "core_visual_entity" (Chủ thể Thị giác Vật lý Trung tâm của toàn video bằng tiếng Anh, 1-3 từ):
   - CHỈ ĐẶT "core_visual_entity" khi video nói về 1 sinh vật, động vật, nhân vật lịch sử hoặc vật thể cụ thể xuyên suốt (vd: "harpy eagle", "julius caesar", "giant kraken").
   - NẾU video thuộc chủ đề Tâm lý học (Psychology), Hành vi con người, Mối quan hệ, Triết học, Tài chính, Phát triển bản thân: BẮT BUỘC đặt `"core_visual_entity": ""` (chuỗi rỗng)! TUYỆT ĐỐI CẤM bịa ra ẩn dụ trừu tượng kiểu "manipulative puppet master", "toxic trap", "mind prison" làm core_visual_entity vì công cụ tìm ảnh sẽ trả về ảnh quái vật/rối gỗ/lồng sắt kỳ quặc không liên quan!
2. Xác định "visual_paradigm" (Bản chất thị giác của từng cảnh dựa trên ngữ nghĩa chủ đề):
   - "CINEMATIC_REALISM": Tâm lý học, thao túng tâm lý, ngôn ngữ cơ thể, cảm xúc con người, giao tiếp xã hội, tội phạm, tài chính, triết học đời sống → ưu tiên ảnh chụp điện ảnh chân thực về biểu cảm/hành động con người (Movie Still, Dramatic Portrait, Moody Cinematic Photography), dùng động cơ ["bing", "ddgs"]. CẤM dùng "artstation" cho chủ đề tâm lý/đời sống!
   - "REAL_WORLD_ACTION": Động vật hoang dã có thật, thiên nhiên có thật, giải cứu động vật, hành động vật lý đời thực → ưu tiên nhiếp ảnh kịch tính & footage thật ["bing", "ddgs"].
   - "HISTORICAL_ARCHIVE": Nhân vật lịch sử có thật, sự kiện lịch sử, cổ vật/di tích thật → ưu tiên tư liệu lịch sử & tranh/ảnh chân thực ["bing", "ddgs"].
   - "SCIENTIFIC_3D": Vũ trụ, thiên văn, giải phẫu, vi sinh, vật lý lượng tử → ưu tiên mô phỏng 3D khoa học & NASA ["bing", "ddgs", "wallhaven"].
   - "CGI_CONCEPT_ART": CHỈ DÙNG CHO thần thoại, quái vật, thủy quái, kinh dị siêu nhiên, viễn tưởng (Sci-Fi) → ưu tiên ["artstation", "bing", "wallhaven", "ddgs"].
   - "STYLIZED_ANIMATION": Hoạt hình 2D châm biếm, minh họa.
3. Chọn "preferred_engines": Mảng động cơ tìm kiếm phù hợp nhất cho cảnh đó từ ["bing", "ddgs", "wallhaven", "artstation"].
4. Thiết kế "search_queries" và "google_image_queries" (3 câu truy vấn ngắn gọn 3-6 từ tiếng Anh cho mỗi cảnh):
   - Mô tả TRỰC TIẾP hình ảnh con người/vật thể/hành động đời thực cần thấy trên màn hình (vd: "man giving fake charming smile", "woman looking anxious dark room", "two people whispering office").
   - TUYỆT ĐỐI CẤM đưa thuật ngữ góc máy/nhiếp ảnh ("Macro shot", "Wide-angle", "Close-up", "Low-angle", "Breathtaking cinematography") vào `search_queries` hoặc `google_image_queries`!
   - TUYỆT ĐỐI CẤM đưa từ khóa ẩn dụ trừu tượng ("puppet master", "mental trap", "invisible cage") vào câu tìm kiếm khi video nói về tâm lý con người!
   - Các phân cảnh liền kề KHÔNG được trùng lặp truy vấn.

TRẢ VỀ JSON DUY NHẤT THEO ĐỊNH DẠNG:
{{
  "core_visual_entity": "Tên tiếng Anh của thực thể vật lý cụ thể (hoặc chuỗi rỗng '' nếu là chủ đề tâm lý/con người/trừu tượng)",
  "overall_visual_paradigm": "CINEMATIC_REALISM | REAL_WORLD_ACTION | HISTORICAL_ARCHIVE | SCIENTIFIC_3D | CGI_CONCEPT_ART | STYLIZED_ANIMATION",
  "scenes_analysis": [
    {{
      "scene_id": 1,
      "core_visual_entity": "Thực thể vật lý cụ thể hoặc ''",
      "visual_paradigm": "CINEMATIC_REALISM | REAL_WORLD_ACTION | HISTORICAL_ARCHIVE | SCIENTIFIC_3D | CGI_CONCEPT_ART | STYLIZED_ANIMATION",
      "preferred_engines": ["bing", "ddgs"],
      "mood": "dark_ominous | tense_mysterious | epic_heroic | vibrant_energetic | melancholy_thoughtful | scientific_curiosity",
      "narrative_subtext": "Ngụ ý cảm xúc của cảnh (1 câu ngắn)",
      "visual_metaphor": "Mô tả hình ảnh cụ thể, chân thực trên màn hình 9:16",
      "subject": "Chủ thể cụ thể của cảnh bằng tiếng Anh (vd: 'man with subtle smirk', 'woman feeling trapped')",
      "production_method": "IMAGE_TO_VIDEO | STOCK_FOOTAGE | TEXT_TO_VIDEO | REAL_ARCHIVE",
      "tool_choice": "ai_image_to_video | pexels_video_clip | ai_text_to_video | real_archive_photo",
      "motion_level": "HIGH_MOTION | MEDIUM_MOTION | STATIC_DETAILED",
      "search_queries": ["concrete visual query 1", "concrete visual query 2", "concrete visual query 3"],
      "google_image_queries": [
        "concrete subject + specific human expression/action + dramatic film still (4-6 words)",
        "concrete subject + realistic setting + moody lighting (4-6 words)",
        "concrete subject + emotional context + cinematic photo (4-6 words)"
      ],
      "negative_cues": ["text overlay", "watermark", "blurry", "unrelated monster", "lingerie", "nsfw"],
      "cinematic_ai_prompt": "Detailed 8K vertical 9:16 photorealistic prompt in English describing the exact scene, subject expression, environment, and lighting"
    }}
  ]
}}
"""
        res = self._call_gemini_json(prompt, timeout=22)
        result_map: Dict[int, Dict[str, Any]] = {}
        if res and isinstance(res.get("scenes_analysis"), list):
            video_core_entity = str(res.get("core_visual_entity") or primary_entity or "").strip()
            video_paradigm = str(res.get("overall_visual_paradigm") or "").strip()
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
                subj = str(item.get("subject") or item.get("core_visual_entity") or "").strip() or extract_subject(narr, primary_entity)
                item = _enforce_subject_queries(item, subj)
                item = _apply_creature_mode(item, narr, topic)
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
        - Tự động suy luận core_visual_entity, visual_paradigm, preferred_engines và google_image_queries từ ngữ nghĩa chủ đề.
        """
        prompt = f"""
Bạn là Tổng Đạo Diễn Nghệ Thuật & Chuyên Gia Tìm Kiếm Hình Ảnh Đa Động Cơ (Autonomous Visual Intelligence & Search Director) cho YouTube Shorts triệu view.
Hãy TỰ ĐỘNG HIỂU SÂU BẢN CHẤT NGỮ NGHĨA của chủ đề và phân cảnh dưới đây để tạo bộ truy vấn tìm kiếm hình ảnh CHÍNH XÁC, KHỚP NỘI DUNG NHẤT.

THÔNG TIN ĐẦU VÀO:
- Chủ đề video: "{topic}"
- Niche kênh: "{channel_niche}"
- Phân cảnh số: {scene_id}/{total_scenes}
- Thực thể chính (nếu đã biết): "{primary_entity or 'Tự suy luận từ chủ đề & lời thoại'}" (Loại: {entity_type})
- Lời thoại phân cảnh: "{narration}"
- Ngữ cảnh xung quanh: "{surrounding_context}"

NHIỆM VỤ TƯ DUY ĐẠO DIỄN TỰ HÀNH:
1. "core_visual_entity": CHỈ ĐẶT khi video nói về 1 sinh vật, động vật, nhân vật lịch sử hoặc vật thể cụ thể (vd: "harpy eagle", "julius caesar", "giant kraken"). Nếu video nói về Tâm lý học, Hành vi con người, Mối quan hệ, Triết học, Tài chính -> BẮT BUỘC để chuỗi rỗng `""`! CẤM bịa ra cụm từ ẩn dụ kiểu "manipulative puppet master" hay "toxic trap".
2. "visual_paradigm": Chọn đúng bản chất thị giác của cảnh:
   - "CINEMATIC_REALISM": Tâm lý học, thao túng tâm lý, cảm xúc con người, giao tiếp xã hội, tội phạm, đời sống -> ưu tiên ảnh chụp điện ảnh chân thực ["bing", "ddgs"].
   - "REAL_WORLD_ACTION": Động vật hoang dã thật, thiên nhiên thật, hành động đời thực -> ["bing", "ddgs"].
   - "HISTORICAL_ARCHIVE": Nhân vật/sự kiện lịch sử có thật -> ["bing", "ddgs"].
   - "SCIENTIFIC_3D": Vũ trụ, thiên văn, giải phẫu, tế bào, vật lý -> ["bing", "ddgs", "wallhaven"].
   - "CGI_CONCEPT_ART": CHỈ DÙNG CHO thần thoại, quái vật, kinh dị siêu nhiên, viễn tưởng -> ["artstation", "bing", "wallhaven", "ddgs"].
   - "STYLIZED_ANIMATION": Hoạt hình 2D châm biếm, minh họa.
3. "preferred_engines": Chọn thứ tự động cơ tìm kiếm phù hợp nhất từ ["bing", "ddgs", "wallhaven", "artstation"].
4. "search_queries" & "google_image_queries": 3 câu truy vấn 3-6 từ tiếng Anh mô tả TRỰC TIẾP hình ảnh con người/sự vật thật cần hiển thị. CẤM đưa thuật ngữ góc máy ("Macro shot", "Wide-angle", "Close-up", "Breathtaking cinematography") hoặc ẩn dụ trừu tượng vào câu tìm kiếm!

HÃY PHÂN TÍCH VÀ TRẢ VỀ JSON DUY NHẤT:
{{
  "core_visual_entity": "Thực thể vật lý cụ thể hoặc ''",
  "visual_paradigm": "CINEMATIC_REALISM | REAL_WORLD_ACTION | HISTORICAL_ARCHIVE | SCIENTIFIC_3D | CGI_CONCEPT_ART | STYLIZED_ANIMATION",
  "preferred_engines": ["bing", "ddgs"],
  "mood": "dark_ominous | tense_mysterious | epic_heroic | vibrant_energetic | melancholy_thoughtful | scientific_curiosity",
  "narrative_subtext": "Ngụ ý và cảm xúc sâu xa mà phân cảnh này muốn truyền tải (1-2 câu tiếng Việt)",
  "visual_metaphor": "Mô tả bối cảnh điện ảnh chân thực, khớp nội dung trên màn hình 9:16",
  "subject": "Chủ thể cụ thể của cảnh bằng tiếng Anh (không chứa góc máy)",
  "production_method": "IMAGE_TO_VIDEO | STOCK_FOOTAGE | TEXT_TO_VIDEO | REAL_ARCHIVE",
  "tool_choice": "ai_image_to_video | pexels_video_clip | ai_text_to_video | real_archive_photo",
  "motion_level": "HIGH_MOTION | MEDIUM_MOTION | STATIC_DETAILED",
  "search_queries": [
    "concrete subject + action (3-5 từ tiếng Anh)",
    "concrete subject + mood/setting (3-5 từ tiếng Anh)",
    "concrete subject + expression (3-5 từ tiếng Anh)"
  ],
  "google_image_queries": [
    "concrete subject + specific scene action + cinematic film still (4-6 từ tiếng Anh)",
    "concrete subject + dramatic expression + moody photography (4-6 từ tiếng Anh)",
    "concrete subject + realistic context + HD (4-6 từ tiếng Anh)"
  ],
  "negative_cues": [
    "text overlay, watermark, blurry, low resolution, unrelated monster, lingerie, nsfw"
  ],
  "cinematic_ai_prompt": "Detailed 8K vertical 9:16 cinematic photorealistic prompt in English, dramatic lighting, vivid contrast, scroll-stopping visual impact."
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
            res = _apply_creature_mode(res, narration, topic)
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
            res["google_image_queries"] = [f'"{subj}" high resolution photograph', f"{subj} close up real photo"]
            res["subject"] = subj
            res = _apply_creature_mode(res, narration, topic)
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
    # PHASE 1.5: AI VISION — NHÌN THẬT THUMBNAIL ĐỂ CHẤM ĐỘ HẤP DẪN & LIÊN QUAN
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _vision_enabled() -> bool:
        import os
        return os.getenv("ENABLE_VISION_CURATION", "true").strip().lower() not in ("0", "false", "no", "off")

    @staticmethod
    def _fetch_thumbnail_b64(url: str, timeout: float = 4.0, fallback_url: str = "") -> Optional[Tuple[str, str]]:
        """Tải thumbnail của ứng viên, nén nhẹ về <=512px JPEG trong RAM và trả về (mime, base64) cho Gemini Vision."""
        if not url and not fallback_url:
            return None
        import base64
        import io as _io
        import requests
        urls = [u for u in (url, fallback_url) if u]
        for target_u in urls:
            try:
                ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                if "wikimedia.org" in target_u or "wikipedia.org" in target_u:
                    from app.services.media.entity_detector import WIKIMEDIA_USER_AGENT
                    ua = WIKIMEDIA_USER_AGENT
                res = requests.get(target_u, timeout=timeout, headers={"User-Agent": ua, "Accept": "image/*,*/*;q=0.8"})
                if res.status_code != 200 or not res.content:
                    continue
                data = res.content
                if len(data) < 1500 or len(data) > 6_000_000:
                    continue
                try:
                    from PIL import Image
                    with Image.open(_io.BytesIO(data)) as im:
                        im = im.convert("RGB")
                        im.thumbnail((512, 512))
                        buf = _io.BytesIO()
                        im.save(buf, format="JPEG", quality=82)
                        return "image/jpeg", base64.b64encode(buf.getvalue()).decode("ascii")
                except Exception:
                    if len(data) <= 1_500_000:
                        mime = (res.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
                        if not mime.startswith("image/"):
                            mime = "image/jpeg"
                        return mime, base64.b64encode(data).decode("ascii")
            except Exception:
                continue
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
            thumbs = list(
                ex.map(
                    lambda c: self._fetch_thumbnail_b64(
                        c.get("thumbnail") or c.get("url", ""),
                        fallback_url=c.get("fallback_url") or c.get("url", ""),
                    ),
                    pool,
                )
            )

        indexed = [(i, t) for i, t in enumerate(thumbs) if t]
        if len(indexed) < 1:
            return None

        va = visual_analysis or {}
        is_hook_scene = bool(va.get("is_hook_scene") or va.get("scene_id") == 1)
        mood = va.get("mood", "cinematic")
        metaphor = va.get("visual_metaphor", "")
        negative_cues = va.get("negative_cues", [])
        subject = str(va.get("subject") or "").strip()
        subject_rule = (
            f"\nCHỦ THỂ TRỌNG TÂM TRONG ẢNH: \"{subject}\".\n"
            f"- KHÔNG CẦN PHẢI LÀ ẢNH CHỤP THẬT 100%: Mọi chất liệu thị giác gồm Cảnh phim CGI (Movie Still), ArtStation 8K Concept Art, "
            f"3D Render, tranh kỹ thuật số siêu thực hay nhiếp ảnh kịch tính ĐỀU ĐƯỢC KHUYẾN KHÍCH miễn là CỰC KỲ BẮT MẮT ĐỂ CÂU VIEW và khớp chủ đề!\n"
            if subject else ""
        )
        if is_hook_scene:
            subject_rule += (
                f"\n🔥 QUY TẮC SỐNG CÒN CHO CẢNH 1 (FRAME 0.0s WOW HOOK — QUYẾT ĐỊNH 85% TỶ LỆ Ở LẠI XEM):\n"
                f"- Đây là hình ảnh mở màn giây 0.0s! Hình ảnh BẮT BUỘC phải đạt hiệu ứng 'WOW' choáng ngợp ngay lập tức: "
                f"cận cảnh cực nét (Extreme Close-Up), khoảnh khắc hành động bùng nổ (Mid-Action Strike/Splash), tương phản quy mô khổng lồ (Megalophobia/Scale Shock), hoặc ánh mắt/biểu cảm trực diện đầy kịch tính.\n"
                f"- KHẮT KHE TUYỆT ĐỐI: Nếu ảnh/video bị tối mù, mờ nhòe, chủ thể ở quá xa/nhỏ bé, góc quay tĩnh nhàm chán, hoặc màu sắc nhạt nhòa -> BẮT BUỘC chấm appeal <= 4.0! Chỉ chấm appeal >= 7.5 cho hình ảnh thực sự xuất sắc, giữ chân ngón tay người xem!\n"
            )
        if va.get("creature_mode"):
            subject_rule += (
                f"\nĐÂY LÀ CHẾ ĐỘ QUÁI VẬT / THỦY QUÁI / KINH DỊ RÙNG RỢN (\"{subject}\"):\n"
                f"- ƯU TIÊN TUYỆT ĐỐI (chấm appeal 9-10): Cảnh phim CGI điện ảnh, ArtStation 8K Dark Horror, Thalassophobia / Megalophobia; "
                f"quái vật KHỔNG LỒ, RÙNG RỢN, ÁM ẢNH, quy mô áp đảo (vd: bóng đen khổng lồ dưới đáy biển sâu trước đèn pha tàu ngầm, "
                f"hàm răng/xúc tu khổng lồ nhấn chìm tàu, đôi mắt phát sáng trong bóng tối).\n"
                f"- LOẠI BỎ NGAY (chấm relevance <= 2, appeal <= 2): Ảnh hóa thạch răng/xương trong bảo tàng, xác cá/mực chết dạt bờ, "
                f"bản vẽ khắc gỗ đen trắng cổ điển khô khan, bản đồ, biểu đồ, hoạt hình dễ thương, chibi, đồ chơi, mô hình, "
                f"hoặc ảnh con vật thật bình thường nhỏ bé trong bể cá!\n"
            )

        meta_lines = []
        for img_no, (orig_idx, _) in enumerate(indexed):
            c = pool[orig_idx]
            w, h = c.get("width") or 0, c.get("height") or 0
            orient = "dọc 9:16" if (w and h and h >= w) else ("ngang" if (w and h) else "không rõ")
            desc = (c.get("description") or c.get("title") or c.get("alt") or "")[:90]
            meta_lines.append(f"- Ảnh #{img_no}: nguồn={c.get('source', 'stock')}, bố cục={orient}, mô tả='{desc}'")

        prompt = f"""
Bạn là Giám Đốc Hình Ảnh (Director of Photography) kiêm Chuyên Gia Tuyển Chọn Hình Ảnh Câu View (Gemini Search Visual Curator) cho kênh YouTube Shorts triệu view.
Bạn được xem {len(indexed)} ảnh thumbnail của các {'video clip' if kind == 'video' else 'bức ảnh'} ứng viên (theo đúng thứ tự Ảnh #0, #1, ...).
Hãy NHÌN KỸ từng ảnh và chọn ứng viên BẮT MẮT NHẤT, CÂU VIEW NHẤT và khớp ngữ cảnh phân cảnh.

PHÂN CẢNH:
- Chủ đề video: "{topic}"
- Lời thoại: "{narration}"
- Ý niệm thị giác: "{metaphor}"
- Không khí (mood): "{mood}"
- Yếu tố cấm kỵ: {json.dumps(negative_cues, ensure_ascii=False)}
{subject_rule}
THÔNG TIN ỨNG VIÊN:
{chr(10).join(meta_lines)}

TIÊU CHÍ CHẤM NGHIÊM NGẶT (mỗi ứng viên):
1. relevance (0-10) — TIÊU CHÍ QUAN TRỌNG NHẤT:
   - Hình ảnh PHẢI thể hiện ĐÚNG chủ đề, hoàn cảnh, cảm xúc và nội dung của lời thoại!
   - NẾU video nói về Tâm lý học / Con người / Đời sống mà ảnh ứng viên lại là: quái vật giả tưởng, mặt nạ kinh dị, trang phục hóa trang, người mặc đồ lót/bikini, xe đẩy siêu thị kỳ quặc, phong cảnh/đồ vật ngẫu nhiên không ăn nhập với lời thoại -> BẮT BUỘC chấm relevance <= 2.0!
   - Lạc đề hoặc chỉ khớp 1 từ khóa nghĩa đen vô nghĩa (vd lời thoại nói "trap/cage" trong tâm lý mà ảnh là lồng chim/xe đẩy) -> chấm relevance <= 2.5!
2. appeal (0-10): Độ sắc nét, ánh sáng điện ảnh, bố cục 9:16 đẹp mắt. Ảnh có WATERMARK, logo trang web, chữ chèn lớn, khung viền, ảnh ghép collage -> appeal tối đa 2.0.
3. refined_google_query: Nếu các ảnh hiện tại chưa khớp nội dung (relevance < 6.5), hãy viết 1 câu truy vấn tiếng Anh mô tả CỤ THỂ hình ảnh đời thực cần tìm (4-6 từ, KHÔNG dùng từ khóa góc máy như "Macro shot" hay ẩn dụ như "puppet master").

TRẢ VỀ JSON DUY NHẤT:
{{
  "scores": [{{"image": 0, "relevance": 8.5, "appeal": 8.5}}],
  "best_image": 0,
  "rationale": "1 câu giải thích vì sao bức ảnh này khớp ngữ cảnh lời thoại nhất (hoặc vì sao từ chối tất cả)",
  "refined_google_query": "concrete visual subject and action moody film still",
  "reject_all": false
}}
Nếu KHÔNG ảnh nào có relevance >= 6.5 -> BẮT BUỘC đặt best_image = -1 và reject_all = true.
"""
        with _VISION_SEM:
            res = self._call_gemini_json(
                prompt,
                timeout=16,
                images=[t for _, t in indexed],
                models=AVAILABLE_MODELS,
                max_attempts=max(2, len(self.rotator.keys)),
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
            refined_q = str(res.get("refined_google_query") or "").strip()
            if isinstance(visual_analysis, dict) and refined_q:
                visual_analysis["_refined_google_query"] = refined_q

            if res.get("reject_all") or int(res.get("best_image", -1)) < 0:
                logger.info(f"👁️ [VisionCuration] Từ chối toàn bộ {len(indexed)} ứng viên {kind}: {rationale} (refined_q='{refined_q}')")
                return -1, 0.0, rationale

            # Xếp hạng ứng viên: Ưu tiên ĐỘ LIÊN QUAN NGỮ CẢNH (relevance >= 6.5, trọng số 65%) để tuyệt đối không lọt ảnh nhảm/lạc đề
            min_rel = 6.8 if is_hook_scene else 6.5
            min_app = 6.8 if is_hook_scene else 5.0
            rel_weight = 0.65
            app_weight = 0.35

            ranked_valid: List[Tuple[float, int]] = []
            for img_no, (rel, app) in score_map.items():
                if rel < min_rel or app < min_app:
                    continue
                combined = rel_weight * rel + app_weight * app
                ranked_valid.append((combined, img_no))
            ranked_valid.sort(reverse=True)

            if ranked_valid:
                best_combined, best_img = ranked_valid[0]
            else:
                logger.info(
                    f"👁️ [VisionCuration] Không ứng viên {kind} nào đạt chuẩn khớp nội dung (rel>={min_rel}, appeal>={min_app}): {rationale}"
                )
                return -1, 0.0, rationale

            orig_idx = indexed[best_img][0]
            # Lưu danh sách ứng viên dự phòng đã qua kiểm duyệt Vision (phòng khi server ảnh #1 chặn tải 403)
            if len(ranked_valid) > 1 and isinstance(pool[orig_idx], dict):
                alt_urls = []
                for _, alt_img_no in ranked_valid[1:4]:
                    alt_cand = pool[indexed[alt_img_no][0]]
                    if alt_cand.get("url"):
                        alt_urls.append(alt_cand["url"])
                if alt_urls:
                    pool[orig_idx]["_vision_alternates"] = alt_urls

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

        # Trích xuất mô tả ảnh đầy đủ và đảm bảo đa dạng tên miền (tối đa 2 ảnh/domain ở lượt đầu) để Gemini Vision có 8 góc nhìn tốt nhất
        pool = []
        overflow_pool = []
        domain_counts: Dict[str, int] = {}
        for c in valid:
            desc = c.get("title") or c.get("alt") or ""
            if not desc and "/photo/" in c.get("url", ""):
                m = re.search(r'/photo/([a-zA-Z0-9-]+)-\d+/?', c.get("url", ""))
                if m:
                    desc = m.group(1).replace("-", " ")
            dom = (c.get("creator") or c.get("provider") or c.get("source") or "web").lower()
            entry = {
                "item": c,
                "description": desc or "photograph",
            }
            if domain_counts.get(dom, 0) < 2:
                domain_counts[dom] = domain_counts.get(dom, 0) + 1
                pool.append(entry)
            else:
                overflow_pool.append(entry)
            if len(pool) >= 8:
                break
        if len(pool) < 8:
            pool.extend(overflow_pool[: 8 - len(pool)])

        if not pool:
            return None, 3.0, "Không có ứng viên ảnh nào khả dụng.", True

        # AI VISION: Nhìn trực tiếp ảnh để chọn bức vừa đúng nội dung vừa hấp dẫn nhất
        vision_pool = [{**p["item"], "description": p["description"]} for p in pool]
        vision = self.vision_rank_candidates(
            vision_pool,
            narration=narration,
            topic=topic or visual_keyword,
            visual_analysis=visual_analysis,
            kind="photo",
            max_candidates=8,
        )
        if vision is not None:
            v_idx, v_score, v_rat = vision
            is_hook_photo = bool((visual_analysis or {}).get("is_hook_scene") or (visual_analysis or {}).get("scene_id") == 1)
            refined_q = str((visual_analysis or {}).get("_refined_google_query") or "").strip()
            min_accept_score = 7.3 if is_hook_photo else 5.6

            # Với Cảnh 1 (Hook), nếu điểm < 7.3 (chưa đủ WOW), lập tức kích hoạt tìm kiếm vòng 2 với refined_google_query
            if (v_idx < 0 or v_score < min_accept_score) and refined_q:
                try:
                    from app.services.media.web_image_fetcher import WebImageFetcher, web_image_search_enabled
                    from app.services.media.image_quality import prefilter_candidates
                    if web_image_search_enabled():
                        logger.info(f"🔎 [VisionCuration] Tự động tinh chỉnh truy vấn Google Images: '{refined_q}'")
                        existing_urls = {p["item"].get("url") for p in pool} | set(used)
                        fresh_google = [
                            it for it in WebImageFetcher().search_open_web(refined_q, per_page=8)
                            if it.get("url") and it["url"] not in existing_urls
                        ]
                        fresh_google = prefilter_candidates(
                            fresh_google,
                            subject=(visual_analysis or {}).get("subject") or refined_q,
                            limit=6,
                            creature_mode=bool((visual_analysis or {}).get("creature_mode")),
                        )
                        if fresh_google:
                            fresh_vpool = [{**it, "description": it.get("title") or it.get("alt") or refined_q} for it in fresh_google]
                            vision2 = self.vision_rank_candidates(
                                fresh_vpool,
                                narration=narration,
                                topic=topic or visual_keyword,
                                visual_analysis=visual_analysis,
                                kind="photo",
                                max_candidates=6,
                            )
                            if vision2 is not None:
                                v2_idx, v2_score, v2_rat = vision2
                                if v2_idx >= 0 and v2_score > v_score:
                                    winner_item = fresh_google[v2_idx]
                                    if fresh_vpool[v2_idx].get("_vision_alternates"):
                                        winner_item["_vision_alternates"] = fresh_vpool[v2_idx]["_vision_alternates"]
                                    winner_item["curation_score"] = max(v2_score, 6.2)
                                    winner_item["curation_rationale"] = f"[Vision Refined Google] {v2_rat}"
                                    return winner_item, max(v2_score, 6.2), v2_rat, False
                except Exception as e:
                    logger.debug(f"[VisionCuration] Refined Google search error: {e}")

            if v_idx >= 0 and (not is_hook_photo or v_score >= 7.0):
                winner_item = pool[v_idx]["item"]
                if vision_pool[v_idx].get("_vision_alternates"):
                    winner_item["_vision_alternates"] = vision_pool[v_idx]["_vision_alternates"]
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
        from app.services.media.image_quality import heuristic_score, subject_terms, is_watermark_risk
        negative_cues = [nc.lower() for nc in visual_analysis.get("negative_cues", [])]
        queries = [q.lower() for q in visual_analysis.get("search_queries", [])]
        terms = subject_terms(visual_analysis.get("subject"))

        best_item = None
        best_score = -1.0

        for p in pool:
            desc = p.get("description", "").lower()
            if any(nc in desc for nc in negative_cues) or is_incongruent_stock_image(desc, topic=topic, narration=narration):
                continue
            if is_watermark_risk(p["item"]):
                continue

            if terms:
                # Điểm chất lượng metadata: khớp chủ thể + ảnh dọc + độ phân giải + không watermark
                score = p["item"].get("_hscore") or heuristic_score({**p["item"], "description": desc}, terms)
            else:
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
