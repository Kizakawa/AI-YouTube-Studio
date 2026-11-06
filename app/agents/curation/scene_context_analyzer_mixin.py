"""
app.agents.curation.scene_context_analyzer_mixin — Phân tích ngữ cảnh kịch bản toàn cục, giải mã hàm ý/đại từ & tạo truy vấn sát ý.
"""
import contextvars as _contextvars
import json
import logging
import os as _os
import re
import threading as _threading
import time as _time_mod
from typing import Any, Dict, List, Optional, Tuple

from app.utils.key_rotator import GEMINI_PRIORITY_MODELS, KeyRotator
from app.utils.langsmith_tracer import trace_custom_span

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = list(GEMINI_PRIORITY_MODELS)

_VISION_SEM = _threading.BoundedSemaphore(max(16, int(_os.getenv("VISION_CONCURRENCY", "24") or 24)))

_SCENE_CANCEL_CTX: _contextvars.ContextVar[Optional[_threading.Event]] = _contextvars.ContextVar(
    "_SCENE_CANCEL_CTX", default=None
)
_SCENE_DEADLINE_CTX: _contextvars.ContextVar[Optional[float]] = _contextvars.ContextVar(
    "_SCENE_DEADLINE_CTX", default=None
)


class _SceneCancellationScope:
    """Hỗ trợ cả gọi trực tiếp `bind_scene_cancellation(...)` lẫn dùng `with bind_scene_cancellation(...):` an toàn."""

    __slots__ = ("_tok_ev", "_tok_dl")

    def __init__(self, tok_ev: Any, tok_dl: Any) -> None:
        self._tok_ev = tok_ev
        self._tok_dl = tok_dl

    def __enter__(self) -> "_SceneCancellationScope":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        try:
            _SCENE_CANCEL_CTX.reset(self._tok_ev)
            _SCENE_DEADLINE_CTX.reset(self._tok_dl)
        except Exception:
            pass
        return False


def bind_scene_cancellation(
    cancel_event: Optional[_threading.Event] = None,
    deadline_ts: Optional[float] = None,
) -> _SceneCancellationScope:
    """Gắn cờ hủy và deadline của phân cảnh hiện tại vào ContextVar (tự động kế thừa qua asyncio.to_thread)."""
    tok_ev = _SCENE_CANCEL_CTX.set(cancel_event)
    tok_dl = _SCENE_DEADLINE_CTX.set(deadline_ts)
    return _SceneCancellationScope(tok_ev, tok_dl)


def is_scene_cancelled_ctx(min_remaining_sec: float = 1.2) -> bool:
    """Kiểm tra xem phân cảnh gọi luồng hiện tại đã bị timeout/hủy hay chưa để thoát tức thì (0s zombie thread)."""
    ev = _SCENE_CANCEL_CTX.get()
    if ev is not None and ev.is_set():
        return True
    dl = _SCENE_DEADLINE_CTX.get()
    if dl is not None and (dl - _time_mod.monotonic()) <= min_remaining_sec:
        return True
    return False


def remaining_scene_budget_ctx(default_sec: float = 15.0) -> float:
    """Trả về số giây còn lại của phân cảnh hiện tại."""
    dl = _SCENE_DEADLINE_CTX.get()
    if dl is None:
        return default_sec
    return max(0.0, dl - _time_mod.monotonic())

_GENERIC_SUBJECTS = {
    "", "scene", "visual", "image", "photo", "thing", "things", "something", "people", "world",
    "reality", "life", "truth", "story", "moment", "time", "way", "fact", "facts",
}

_CAMERA_JARGON_RE = re.compile(
    r"\b(breathtaking|cinematography|cinematic|dramatic|macro\s+shot|wide[\s\-]+angle|low[\s\-]+angle|"
    r"high[\s\-]+angle|extreme\s+close[\s\-]+up|close[\s\-]+up\s+shot|tracking\s+shot|establishing\s+shot|"
    r"over[\s\-]+the[\s\-]+shoulder|dutch\s+angle|film\s+still|movie\s+still|shot\s+of|view\s+of|"
    r"8k|4k|hd|hdr|photorealistic|hyperrealistic|unreal\s+engine|octane\s+render|concept\s+art|"
    r"chiaroscuro|shadow\s+portrait|monochrome\s+aesthetic|monochrome\s+psychological\s+thriller\s+aesthetic|"
    r"render_doodle_2d_explainer_scene|clone_youtube_short_peak_clip|fetch_clean_cinematic_portrait_or_broll|"
    r"summon_epic_monster_cinematic_media|search_pexels_pixabay_stock_video|search_google_cinematic_image)\b",
    re.IGNORECASE,
)


def _clean_search_phrase(q: str) -> str:
    """Loại bỏ thuật ngữ góc máy / kỹ thuật điện ảnh khỏi câu truy vấn tìm kiếm ảnh/video."""
    cleaned = _CAMERA_JARGON_RE.sub(" ", q or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-:")
    return cleaned


def _enforce_subject_queries(res: Dict[str, Any], subject: str) -> Dict[str, Any]:
    """
    Chuẩn hóa nhẹ nhàng truy vấn tìm kiếm:
    - TÔN TRỌNG tư duy ngữ nghĩa của LLM Agent, làm sạch các từ khóa góc máy (`8k`, `wide-angle`...)
      và bảo toàn tính đặc thù của thực thể gốc (`core_visual_entity`, chống suy thoái về danh từ chung như `'bird'`, `'raptor'`, `'predator'`, `'hunter'`).
    """
    is_ai = (res.get("analysis_source") == "ai")
    core_entity = _clean_search_phrase(str(res.get("core_visual_entity") or "").strip())
    subject = _clean_search_phrase((subject or core_entity or "").strip())

    if not subject or subject.lower() in _GENERIC_SUBJECTS:
        return res

    from app.services.media.image_quality import subject_terms, creature_queries
    from app.graph.tools.llm_media_director import (
        _GENERIC_ROLE_OR_CLASS_HYPERNYMS,
        _restore_full_canonical_entity_in_query,
    )
    terms = subject_terms(core_entity or subject)
    short_subj = " ".join(subject_terms(subject)[:4]) if len(subject.split()) > 4 else subject
    anchor_subj = " ".join(terms[:3]) if terms else short_subj

    # Nếu `subject` bị rút gọn thành danh từ chung ('bird in tree branches', 'predator', 'hunter')
    # hoặc bị cắt cụt từ định danh đầu ('eagle' thay vì 'harpy eagle') trong khi `core_entity` là thực thể cụ thể
    if core_entity:
        subj_toks = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", subject)]
        core_toks = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", core_entity)]
        subj_meaningful = [w for w in subj_toks if w not in _GENERIC_ROLE_OR_CLASS_HYPERNYMS]
        is_suffix_truncation = len(core_toks) >= 2 and subj_meaningful == [core_toks[-1]]
        if not subj_meaningful or is_suffix_truncation:
            subject = _restore_full_canonical_entity_in_query(subject, core_entity, max_words=4) or core_entity
            short_subj = subject

    raw_queries = [q for q in (res.get("search_queries") or []) if isinstance(q, str) and q.strip()]
    queries = [_clean_search_phrase(q) for q in raw_queries if _clean_search_phrase(q)]

    fixed: List[str] = []
    for q in queries:
        if core_entity:
            q = _restore_full_canonical_entity_in_query(q, core_entity, max_words=5)
        elif not is_ai and anchor_subj and anchor_subj.lower() not in q.lower():
            q = f"{anchor_subj} {q}" if len(q.split()) <= 4 else f"{anchor_subj} {' '.join(q.split()[:3])}"
        if q and q.lower() not in [x.lower() for x in fixed]:
            fixed.append(q)
    if not fixed and short_subj:
        fixed.append(short_subj)
    res["search_queries"] = fixed[:4]
    res["subject"] = subject

    paradigm = str(res.get("visual_paradigm") or "").upper()
    if is_ai and paradigm in ("DARK_HORROR_MYTHOLOGY", "MYTHOLOGY_MONSTER", "HOLLYWOOD_VFX_MOVIE", "CGI_CONCEPT_ART"):
        res["creature_mode"] = True
        res["preferred_engines"] = ["google"]
    else:
        res.setdefault("creature_mode", False)
        res["preferred_engines"] = ["google"]

    g_queries = [_clean_search_phrase(q.replace('"', "").strip()) for q in (res.get("google_image_queries") or []) if isinstance(q, str) and q.strip()]
    fixed_g: List[str] = []
    for gq in g_queries:
        if not gq:
            continue
        gq = re.sub(r"\s+", " ", gq).strip()
        if core_entity:
            gq = _restore_full_canonical_entity_in_query(gq, core_entity, max_words=5)
        elif not is_ai and anchor_subj and anchor_subj.lower() not in gq.lower():
            gq = f"{anchor_subj} {gq}"
        gq_words = gq.split()
        if len(gq_words) > 6:
            gq = " ".join(gq_words[:6])
        if gq and gq.lower() not in [x.lower() for x in fixed_g]:
            fixed_g.append(gq)

    if not fixed_g:
        primary_action = " ".join((fixed[0] if fixed else short_subj).split()[:5])
        fixed_g = [primary_action, f"{short_subj} close up"]

    res["google_image_queries"] = fixed_g[:4]
    if res.get("creature_mode"):
        res["creature_queries"] = creature_queries(subject, action_query=fixed_g[0] if fixed_g else "")
    return res


def _apply_creature_mode(res: Dict[str, Any], narration: str = "", topic: str = "", channel_niche: str = "") -> Dict[str, Any]:
    """Đồng bộ `creature_queries` khi LLM Agent đã bật `creature_mode=True`."""
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


class SceneContextAnalyzerMixin:
    """Mixin phân tích ngữ cảnh toàn bộ kịch bản của người dùng và giải mã hàm ý cho từng cảnh."""

    @trace_custom_span("VisualCurationAgent.gemini_json", run_type="llm")
    def _call_gemini_json(
        self,
        prompt: str,
        timeout: int = 15,
        images: Optional[List[Tuple[str, str]]] = None,
        models: Optional[List[str]] = None,
        max_attempts: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Gọi Gemini API trả về JSON với KeyRotator, thread-local Session và giới hạn tổng wall-clock."""
        if not self.rotator or is_scene_cancelled_ctx():
            return None

        import time as _time
        import requests

        parts: List[Dict[str, Any]] = [{"text": prompt}]
        for mime, b64 in (images or []):
            parts.append({"inline_data": {"mime_type": mime, "data": b64}})

        # Thread-local requests.Session tránh xung đột SSL socket khi nhiều worker threads gọi Vision song song
        tls = getattr(self, "_tls_http", None)
        if tls is None:
            tls = _threading.local()
            self._tls_http = tls
        http_sess = getattr(tls, "session", None)
        if http_sess is None:
            http_sess = requests.Session()
            tls.session = http_sess

        t_call_entry = _time.monotonic()
        per_req_timeout = min(max(4.0, float(timeout or 10)), 9.0)
        total_wall_budget_sec = min(max(per_req_timeout * 1.85, 12.5), 18.5)
        call_deadline_ts = t_call_entry + min(
            total_wall_budget_sec,
            max(2.0, remaining_scene_budget_ctx(total_wall_budget_sec + 1.0) - 0.8),
        )

        conn_errors = 0
        default_attempts = 6 if images else 5
        eff_attempts = min(int(max_attempts or default_attempts), 6)
        cand_models = [
            m for m in (models or AVAILABLE_MODELS)
            if not (images and str(m).startswith("gemma-"))
        ]
        for model, key in self.rotator.iter_best_model_key_pairs(
            cand_models,
            max_total_attempts=eff_attempts,
            require_vision=bool(images),
        ):
            if is_scene_cancelled_ctx():
                return None
            remaining_wall = call_deadline_ts - _time.monotonic()
            if remaining_wall <= 1.8:
                logger.warning(
                    f"[VisualCurationAgent] Đạt ngưỡng tổng wall-clock budget ({total_wall_budget_sec:.1f}s) cho gemini_json -> thoát sớm chống treo."
                )
                break
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            gen_cfg = KeyRotator.build_fast_json_gen_config(model, temperature=0.2)
            payload = {
                "contents": [{"parts": parts}],
                "generationConfig": gen_cfg,
            }
            sem_wait = min(14.0, max(2.0, remaining_wall - 2.0))
            if not _VISION_SEM.acquire(timeout=sem_wait):
                logger.warning(f"[VisualCurationAgent] Quá hạn chờ _VISION_SEM ({sem_wait:.1f}s) -> thoát sớm tránh dồn hàng đợi.")
                break
            t_req_start = _time.monotonic()
            req_elapsed = 0.0
            try:
                remaining_after_sem = call_deadline_ts - t_req_start
                if is_scene_cancelled_ctx() or remaining_after_sem <= 1.6:
                    return None
                eff_read = min(per_req_timeout, max(3.5, remaining_after_sem - 0.4))
                eff_conn = min(3.2, max(1.5, eff_read * 0.45))
                res = http_sess.post(url, json=payload, timeout=(eff_conn, eff_read))
                req_elapsed = max(0.05, _time.monotonic() - t_req_start)
            except requests.exceptions.Timeout as e:
                self.rotator.mark_model_rate_limited(key, model, status_code=504, error_text=f"Timeout: {e}")
                continue
            except requests.exceptions.ConnectionError:
                conn_errors += 1
                if conn_errors >= 2:
                    return None
                continue
            except Exception as e:
                logger.debug(f"[VisualCurationAgent] Gemini call error on {model}: {e}")
                continue
            finally:
                _VISION_SEM.release()

            try:
                if res.status_code == 200:
                    self.rotator.record_model_success(key, model, req_elapsed)
                    data = res.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        c_parts = candidates[0].get("content", {}).get("parts", [])
                        raw_text = "".join(
                            str(p.get("text") or "")
                            for p in c_parts
                            if isinstance(p, dict) and not p.get("thought")
                        ).strip()
                        if not raw_text and c_parts:
                            raw_text = str(c_parts[-1].get("text") or "").strip()
                        if raw_text:
                            m_json = re.search(r"\{.*\}", raw_text, re.DOTALL)
                            return json.loads(m_json.group(0) if m_json else raw_text)
                elif res.status_code in (429, 403, 404, 503, 400):
                    self.rotator.mark_model_rate_limited(key, model, status_code=res.status_code, error_text=res.text[:800])
                    continue
            except Exception as e:
                logger.debug(f"[VisualCurationAgent] Gemini parse error on {model}: {e}")
                continue

        return None

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
        Sử dụng Cơ chế Tư Duy Giải Mã Ngữ Cảnh & Hàm Ý Kịch Bản 2 Bước (2-Step Holistic Script & Subtext Reasoning)
        để hiểu sâu ý nghĩa thực sự của kịch bản người dùng, tránh phụ thuộc vào từ khóa bề mặt.
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
        full_script_story = " ".join(str(s.get("narration") or "").strip() for s in scenes_payload if s.get("narration"))

        from app.services.niche_manager import build_llm_production_overview_prompt
        niche_yaml_blueprint = build_llm_production_overview_prompt(
            channel_niche or "general",
            include_all_niches_map=False,
        )

        prompt = f"""Bạn là Tổng Đạo Diễn Hình Ảnh & Chuyên Gia Giải Mã Ngữ Cảnh Kịch Bản (Holistic Script Comprehension & Visual Director) cho YouTube Shorts triệu view.
Nhiệm vụ tối thượng của bạn là ĐỌC HIỂU SÂU TOÀN BỘ KỊCH BẢN CỦA NGƯỜI DÙNG cùng BẢN ĐỒ ĐẠO DIỄN NGÁCH YAML bên dưới trước khi phân tích từng phân cảnh nhỏ, tự động suy luận chủ thể ngầm định, bối cảnh/môi trường và hàm ý cảm xúc để tạo ra câu trả lời & truy vấn hình ảnh SÁT Ý NHẤT — TUYỆT ĐỐI KHÔNG dịch từng chữ máy móc hay phụ thuộc vào từ khóa bề mặt!

THÔNG TIN KỊCH BẢN CỦA NGƯỜI DÙNG:
- Chủ đề (Topic): "{topic}"
- Ngách kênh (Niche): "{channel_niche}"
- Thực thể chính gợi ý: "{primary_entity or 'Tự suy luận từ toàn bộ mạch kịch bản'}" (Loại: {entity_type})
- TOÀN BỘ MẠCH TRUYỆN KỊCH BẢN (FULL USER SCRIPT):
"{full_script_story}"

{niche_yaml_blueprint}

- Danh sách {len(scenes_payload)} phân cảnh cần đạo diễn:
{json.dumps(scenes_payload, ensure_ascii=False, indent=2)}

══════════════════════════════════════════════════════════════════════════════
QUY TRÌNH TƯ DUY SÂU 2 BƯỚC (2-STEP HOLISTIC SCRIPT & SUBTEXT REASONING):
══════════════════════════════════════════════════════════════════════════════
BƯỚC 1 — ĐỌC HIỂU TOÀN CỤC & GIẢI MÃ ĐẠI TỪ / ẨN DỤ / CÂU CHUYỂN Ý:
1. Giải mã Đại từ, Từ chỉ vai trò & Chủ ngữ ẩn (Pronoun, Role Epithet & Implicit Subject Resolution):
   - Khi một câu thoại bị tách nhỏ chỉ dùng đại từ ("It", "He", "She", "They", "This creature", "Nó", "Họ", "Kẻ này"), từ chỉ vai trò ("the predator", "the hunter", "its living prey", "the giant", "the bird") hoặc chỉ nêu thông số/hệ quả mà không nhắc lại tên chủ thể, bạn PHẢI đối chiếu với `FULL USER SCRIPT` để khôi phục đúng **Tên Thực Thể Cụ Thể Chính Xác (Canonical Entity)** đang thực hiện hành động ở câu đó.
2. 🚨 KHÓA CHẶT TÍNH ĐẶC THÙ CỦA THỰC THỂ GỐC (CANONICAL ENTITY SPECIFICITY LOCK — CẤM SUY THOÁI VỀ DANH TỪ CHUNG):
   - Một khi đã xác định thực thể cụ thể của kịch bản (ví dụ `"harpy eagle"`, `"three-toed sloth"`, `"komodo dragon"`, `"colossal squid"`), MỌI trường `"core_visual_entity"`, `"subject"`, `"search_queries"` và `"google_image_queries"` BẮT BUỘC phải giữ nguyên **TÊN ĐẦY ĐỦ CỦA THỰC THỂ ĐÓ** (ví dụ `"harpy eagle diving forest"`, `"harpy eagle talons branch"`, `"three toed sloth hanging"`).
   - **CẤM TUYỆT ĐỐI** rút gọn hoặc thay thế tên loài/thực thể cụ thể bằng các danh từ chung mơ hồ như `"eagle"`, `"bird"`, `"bird of prey"`, `"raptor"`, `"predator"`, `"hunter"`, `"prey"`, `"animal"`, `"creature"`, `"beast"`!
   - **CẤM SAO CHÉP GỢI Ý LỖI (`visual_keyword` / `stock_query` đầu vào)**: Xem `visual_keyword` và `stock_query` trong dữ liệu đầu vào chỉ là bản nháp thô có thể chứa lỗi ẩn dụ nghĩa đen (`"steel clamps"`), từ chỉ vai trò (`"hunter"`, `"predator"`, `"prey"`) hoặc danh từ chung (`"bird in tree branches"`). Nếu phát hiện các từ lỗi đó, hãy **VỨT BỎ HOÀN TOÀN** và tự viết mới truy vấn dựa trên `narration` + `FULL USER SCRIPT`!
3. Giải mã Ẩn dụ, So sánh, Đạo cụ phụ & Câu chuyển ý (Metaphor, Side-Prop & Subtext Decoding):
   - Nếu câu thoại dùng hình ảnh so sánh, ẩn dụ hoặc nhắc tới một đạo cụ phụ/người quan sát để nhấn mạnh quy mô của sự kiện chính (ví dụ: `"locking like steel clamps"`, `"động cơ ô tô chết máy giữa đường chạy trốn"`, `"các kỹ sư quân sự nhận ra sức công phá"`, `"bộ giáp vô hình"`, `"cỗ máy nghiền thủy lực"`, `"chiếc lồng im lặng"`, `"như một con rối"`), TUYỆT ĐỐI KHÔNG tìm kiếm vật thể nghĩa đen của từ ẩn dụ hay đạo cụ phụ đó (CẤM tìm `"steel clamps"`, `"car dashboard"`, `"engineers planning"`, cái lồng, con rối hay máy thủy lực!). Hãy luôn giữ ống kính khóa chặt vào **hành động vật lý, hiện tượng cốt lõi, cận cảnh giải phẫu hoặc biểu cảm thực tế của chủ thể chính trong kịch bản**.
4. Ngôn ngữ Tìm kiếm Phổ thông của Creator (Everyday YouTube/Stock Vocabulary — CẤM Từ ngữ Học thuật / Sách giáo khoa):
   - Người đăng video lên YouTube/Pexels luôn đặt tiêu đề bằng từ vựng tiếng Anh đời thường, trực quan. Nếu lời thoại dùng thuật ngữ sinh học/địa chất/học thuật (như `"benthic crustacean"`, `"apex elapid"`, `"limnic eruption"`, `"cervical vertebrae"`), BẮT BUỘC dịch ngay sang cụm từ tìm kiếm phổ thông 2–5 từ mà video thật luôn dùng (như `"deep sea crab underwater"`, `"king cobra strike"`, `"volcanic crater lake gas"`, `"jaguar biting caiman"`).
5. Suy luận Bối cảnh / Môi trường / Thời đại ngầm định (Implicit Habitat & Era Lock):
   - Tự động suy luận môi trường sinh thái, mùa, độ sâu, ánh sáng hoặc thời đại lịch sử xuyên suốt của toàn bộ kịch bản (ví dụ: rừng tuyết Siberia, đáy biển sâu tối đen, sông Amazon, La Mã cổ đại, hay phòng tối tâm lý Noir) và giữ sự đồng nhất bối cảnh đó cho mọi phân cảnh.

BƯỚC 2 — THIẾT KẾ TRUY VẤN SÁT Ý KỊCH BẢN (`google_image_queries` & `search_queries`):
1. "core_visual_entity" (Danh từ gốc chỉ Chủ thể Vật lý Trung tâm bằng tiếng Anh, 1–3 từ):
   - Đặt ĐẦY ĐỦ tên loài/thực thể/nhân vật cụ thể của phân cảnh đó (vd: `"harpy eagle"`, `"three toed sloth"`, `"honey badger"`, `"julius caesar"` — CẤM rút gọn `"harpy eagle"` thành `"eagle"` hay `"bird"`). Không nhồi tính từ hoa mỹ.
   - Nếu kịch bản thuộc chủ đề Tâm lý học, Hành vi con người, Mối quan hệ, Triết học: đặt `"core_visual_entity": ""` (chuỗi rỗng).
2. "visual_paradigm":
   - `"CINEMATIC_REALISM"`: Tâm lý học, thao túng tâm lý, ngôn ngữ cơ thể, cảm xúc con người -> ưu tiên biểu cảm con người điện ảnh Dark Noir / Chiaroscuro sắc lạnh (cấm ảnh văn phòng cười giả tạo hoặc đạo cụ nghĩa đen).
   - `"REAL_WORLD_ACTION"`: Động vật hoang dã, thiên nhiên, giải cứu động vật, hành động đời thực.
   - `"HISTORICAL_ARCHIVE"`: Nhân vật/sự kiện/di tích lịch sử có thật.
   - `"SCIENTIFIC_3D"`: Vũ trụ, thiên văn, giải phẫu thần kinh/sinh học, vật lý.
   - `"HOLLYWOOD_VFX_MOVIE"`: Thần thoại, thủy quái, sinh vật tiền sử khổng lồ, viễn tưởng.
3. Công thức tạo `google_image_queries` & `search_queries` (2–5 từ tiếng Anh):
   - Tập trung vào **1 chủ thể cụ thể duy nhất (ghi đủ tên loài/thực thể đầy đủ) đang thực hiện hành động/trạng thái của phân cảnh đó + môi trường đặc trưng của kịch bản** (CẤM dùng `"vs"`, `"versus"` hoặc ghép 2 loài khác nhau trong 1 câu truy vấn để tránh ảnh ghép chia đôi màn hình, và CẤM dùng danh từ chung `"bird"`, `"raptor"`, `"predator"`, `"hunter"`, `"prey"`).
   - Không nhồi từ khóa kỹ thuật máy quay (`"8k"`, `"9:16"`, `"macro shot"`, `"photorealistic"`).
   - Đảm bảo cả {len(scenes_payload)} phân cảnh có góc tiếp cận/hành động đa dạng theo tiến trình câu chuyện, không trùng lặp câu truy vấn đầu tiên giữa các cảnh.

TRẢ VỀ JSON DUY NHẤT THEO ĐỊNH DẠNG:
{{
  "holistic_script_summary": "Tóm tắt 1 câu về cốt truyện thực sự, chủ thể trung tâm và bối cảnh ngầm định của toàn bộ kịch bản",
  "core_visual_entity": "Tên tiếng Anh đầy đủ của thực thể vật lý cụ thể (hoặc '' nếu là tâm lý/con người)",
  "overall_visual_paradigm": "CINEMATIC_REALISM | REAL_WORLD_ACTION | HISTORICAL_ARCHIVE | SCIENTIFIC_3D | HOLLYWOOD_VFX_MOVIE | STYLIZED_ANIMATION",
  "scenes_analysis": [
    {{
      "scene_id": 1,
      "core_visual_entity": "Tên tiếng Anh đầy đủ của thực thể vật lý cụ thể được giải mã cho cảnh này (cấm viết chung chung bird/eagle/predator/hunter/prey) hoặc ''",
      "visual_paradigm": "CINEMATIC_REALISM | REAL_WORLD_ACTION | HISTORICAL_ARCHIVE | SCIENTIFIC_3D | HOLLYWOOD_VFX_MOVIE | STYLIZED_ANIMATION",
      "preferred_engines": ["google"],
      "mood": "dark_ominous | tense_mysterious | epic_heroic | vibrant_energetic | melancholy_thoughtful | scientific_curiosity",
      "narrative_subtext": "Giải mã ý nghĩa thực sự của câu thoại này khi đặt trong toàn bộ kịch bản (đã giải quyết đại từ/ẩn dụ)",
      "visual_metaphor": "Mô tả khung hình cụ thể, sát ý kịch bản nhất để hiển thị lên màn hình",
      "subject": "Tên đầy đủ của chủ thể cụ thể + hành động/trạng thái của cảnh bằng tiếng Anh (2-4 từ)",
      "production_method": "IMAGE_TO_VIDEO | STOCK_FOOTAGE | TEXT_TO_VIDEO | REAL_ARCHIVE",
      "tool_choice": "ai_image_to_video | pexels_video_clip | ai_text_to_video | real_archive_photo",
      "motion_level": "HIGH_MOTION | MEDIUM_MOTION | STATIC_DETAILED",
      "search_queries": ["2-5 word query 1 with full canonical entity name", "2-5 word query 2 with full canonical entity name", "2-5 word query 3 with full canonical entity name"],
      "google_image_queries": [
        "Primary 2-5 word English query with full canonical entity name capturing this scene's true meaning",
        "Alternative 2-5 word English query with full canonical entity name for this scene's action and environment",
        "Third distinct angle query with full canonical entity name for this scene (2-5 English words)"
      ],
      "negative_cues": ["text overlay", "watermark", "meme", "diagram", "chart", "infographic", "blurry", "low resolution", "split screen collage"],
      "cinematic_ai_prompt": "Detailed 8K vertical 9:16 photorealistic IMAX visual prompt in English faithfully depicting this scene within the full script's setting and mood"
    }}
  ]
}}"""
        res = self._call_gemini_json(prompt, timeout=22)
        result_map: Dict[int, Dict[str, Any]] = {}
        if res and isinstance(res.get("scenes_analysis"), list):
            is_psych_niche = "psych" in str(channel_niche or "").lower()
            raw_v_core = res.get("core_visual_entity")
            if is_psych_niche or ("core_visual_entity" in res and raw_v_core is not None):
                video_core_entity = str(raw_v_core or "").strip()
            else:
                video_core_entity = str(raw_v_core or primary_entity or "").strip()
            video_paradigm = str(res.get("overall_visual_paradigm") or "").strip()
            holistic_summary = str(res.get("holistic_script_summary") or "").strip()
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
                if holistic_summary:
                    item["holistic_script_summary"] = holistic_summary
                if not item.get("core_visual_entity") and video_core_entity and not is_psych_niche:
                    if "core_visual_entity" not in item or not str(item.get("subject") or "").strip():
                        item["core_visual_entity"] = video_core_entity
                if not item.get("visual_paradigm") and video_paradigm:
                    item["visual_paradigm"] = video_paradigm
                sc_orig = next((x for x in scenes_payload if x["scene_id"] == sid), {})
                narr = sc_orig.get("narration", "")
                # KHÔNG nối mù quáng `orig_vk` (visual_keyword thô chưa giải mã) vào cuối `google_image_queries`
                # khi LLM đã sinh ra danh sách truy vấn sạch, tránh tái nhiễm các cụm ẩn dụ như 'steel clamps' hay 'hunter dangling'!
                if not item.get("google_image_queries"):
                    orig_vk = _clean_search_phrase(str(sc_orig.get("visual_keyword") or "").strip())
                    if orig_vk and 2 <= len(orig_vk.split()) <= 6:
                        item["google_image_queries"] = [orig_vk]

                subj = str(item.get("subject") or item.get("core_visual_entity") or "").strip() or extract_subject(narr, primary_entity)
                item = _enforce_subject_queries(item, subj)
                item = _apply_creature_mode(item, narr, topic, channel_niche=channel_niche)

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
                    f"⚡ [VisualCuration AI] Đã giải mã ngữ cảnh kịch bản đồng loạt {len(result_map)}/{len(scenes_payload)} cảnh "
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
        Phân tích chuyên sâu ngữ cảnh và hàm ý của một phân cảnh đơn lẻ đặt trong mạch kịch bản xung quanh.
        """
        prompt = f"""Bạn là Tổng Đạo Diễn Hình Ảnh & Chuyên Gia Giải Mã Ngữ Cảnh Kịch Bản (Holistic Script Comprehension & Visual Director) cho YouTube Shorts triệu view.
Hãy đọc kỹ Chủ đề, Ngữ cảnh xung quanh và Lời thoại phân cảnh dưới đây để giải mã ý nghĩa thực sự của câu thoại (tự động khôi phục chủ ngữ nếu câu dùng đại từ, và dịch các ẩn dụ sang hành động vật lý thực tế của chủ thể chính), từ đó tạo ra bộ truy vấn `google_image_queries` & `search_queries` (2–5 từ tiếng Anh) sát ý kịch bản nhất.

THÔNG TIN ĐẦU VÀO:
- Chủ đề video (Topic): "{topic}"
- Ngách kênh (Niche): "{channel_niche}"
- Phân cảnh số: {scene_id}/{total_scenes}
- Thực thể chính (nếu đã biết): "{primary_entity or 'Tự suy luận từ chủ đề & ngữ cảnh kịch bản'}" (Loại: {entity_type})
- Ngữ cảnh kịch bản xung quanh (Surrounding Script Context): "{surrounding_context}"
- Lời thoại phân cảnh hiện tại: "{narration}"

QUY TẮC SUY LUẬN NGỮ NGHĨA (KHÔNG DỊCH TỪ KHÓA MÁY MÓC):
1. Giải mã Đại từ, Từ chỉ vai trò, Ẩn dụ & Chống lệch sang Đạo cụ phụ: Nếu lời thoại phân cảnh hiện tại dùng đại từ ("It", "He", "They", "Nó", "Họ"), từ chỉ vai trò ("the predator", "the hunter", "its living prey", "the bird"), ẩn dụ ("like steel clamps"), hoặc nhắc tới đạo cụ phụ/người quan sát bên lề (như xe cộ, phòng họp, tán cây trống rỗng), hãy dựa vào `Topic` và `Surrounding Script Context` để khôi phục đúng **Tên Thực Thể Cụ Thể Chính Xác (Canonical Entity)** của câu chuyện.
2. 🚨 KHÓA CHẶT TÍNH ĐẶC THÙ CỦA THỰC THỂ GỐC ("core_visual_entity"): Đặt ĐẦY ĐỦ tên thực thể/loài vật/nhân vật cụ thể bằng từ vựng tiếng Anh phổ thông (1–3 từ, ví dụ `"harpy eagle"`, `"three toed sloth"`, `"komodo dragon"` — CẤM TUYỆT ĐỐI rút gọn thành danh từ chung như `"eagle"`, `"bird"`, `"raptor"`, `"predator"`, `"hunter"`, `"prey"`). Nếu là chủ đề Tâm lý học, Hành vi con người, Triết học -> đặt `""`.
3. "search_queries" & "google_image_queries": Viết 3 câu truy vấn tiếng Anh phổ thông ngắn gọn (2–5 từ, dịch mọi thuật ngữ học thuật sang từ khóa đời thường dễ tìm video thật) mô tả đúng `[Tên đầy đủ Chủ thể đơn lẻ của cảnh] + [Môi trường/bối cảnh của kịch bản] + [Hành động/trạng thái]`. Tuyệt đối không dùng `"vs"` hay ghép 2 loài khác nhau trong 1 câu truy vấn, và không dùng danh từ chung `"bird"`, `"predator"`, `"hunter"`, `"prey"`.

TRẢ VỀ JSON DUY NHẤT:
{{
  "core_visual_entity": "Thực thể vật lý cụ thể hoặc ''",
  "visual_paradigm": "CINEMATIC_REALISM | REAL_WORLD_ACTION | HISTORICAL_ARCHIVE | SCIENTIFIC_3D | HOLLYWOOD_VFX_MOVIE | STYLIZED_ANIMATION",
  "preferred_engines": ["google"],
  "mood": "dark_ominous | tense_mysterious | epic_heroic | vibrant_energetic | melancholy_thoughtful | scientific_curiosity",
  "narrative_subtext": "Ý nghĩa thực sự của phân cảnh này sau khi đối chiếu với ngữ cảnh kịch bản (1-2 câu tiếng Việt)",
  "visual_metaphor": "Mô tả hình ảnh cụ thể, sát ý kịch bản nhất trên màn hình 9:16",
  "subject": "Chủ thể + hành động cụ thể của cảnh bằng tiếng Anh (2-4 từ)",
  "production_method": "IMAGE_TO_VIDEO | STOCK_FOOTAGE | TEXT_TO_VIDEO | REAL_ARCHIVE",
  "tool_choice": "ai_image_to_video | pexels_video_clip | ai_text_to_video | real_archive_photo",
  "motion_level": "HIGH_MOTION | MEDIUM_MOTION | STATIC_DETAILED",
  "search_queries": [
    "contextual 2-4 word English query 1",
    "contextual 2-4 word English query 2",
    "contextual 2-4 word English query 3"
  ],
  "google_image_queries": [
    "Primary 2-5 word English query matching this scene's true script meaning",
    "Alternative 2-5 word English query for this scene's action/environment",
    "Third distinct visual angle query (2-5 English words)"
  ],
  "negative_cues": [
    "text overlay, watermark, meme, diagram, chart, infographic, blurry, low resolution, split screen collage"
  ],
  "cinematic_ai_prompt": "Detailed 8K vertical 9:16 photorealistic IMAX visual prompt in English matching the script's exact subject, environment, and atmosphere."
}}"""
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

        res = self._heuristic_scene_analysis(narration, topic, primary_entity, entity_type, scene_id, total_scenes)
        res["scene_id"] = int(scene_id)
        res["is_hook_scene"] = (int(scene_id) == 1)
        from app.services.media.image_quality import extract_subject
        subj = extract_subject(narration, primary_entity)
        if not subj or subj.lower() in _GENERIC_SUBJECTS:
            subj = extract_subject(surrounding_context or topic)
        if subj and subj.lower() not in _GENERIC_SUBJECTS and res.get("production_method") != "REAL_ARCHIVE":
            res["search_queries"] = [subj, f"{subj} close up", f"{subj} documentary"]
            res["google_image_queries"] = [subj, f"{subj} close up", f"{subj} dramatic"]
            res["subject"] = subj
            res = _apply_creature_mode(res, narration, topic, channel_niche=channel_niche)
        return res

    def _heuristic_scene_analysis(
        self,
        narration: str = "",
        topic: str = "",
        primary_entity: Optional[str] = None,
        entity_type: str = "general",
        scene_id: int = 1,
        total_scenes: int = 1,
        visual_keyword: str = "",
        channel_niche: str = "general",
        **kwargs,
    ) -> Dict[str, Any]:
        """Dự phòng tổng quát dựa trên chủ thể của kịch bản khi toàn bộ kết nối LLM tạm thời gián đoạn."""
        from app.services.media.image_quality import extract_subject
        core_subj = _clean_search_phrase(
            primary_entity or visual_keyword or extract_subject(f"{topic} {narration}") or topic or "documentary scene"
        )
        short_subj = " ".join(core_subj.split()[:4]) or "documentary scene"
        is_historical = bool(primary_entity) and entity_type in ("person", "civilization", "monument", "event", "historical_figure")

        if is_historical and scene_id > 1:
            method = "REAL_ARCHIVE"
            tool = "real_archive_photo"
            motion = "STATIC_DETAILED"
            mood = "historical_epic"
            queries = [f"{short_subj} historical archive", f"{short_subj} portrait", short_subj]
        else:
            method = "STOCK_FOOTAGE"
            tool = "pexels_video_clip"
            motion = "HIGH_MOTION" if scene_id == 1 else "MEDIUM_MOTION"
            mood = "tense_mysterious"
            queries = [short_subj, f"{short_subj} close up", f"{short_subj} action"]

        return {
            "mood": mood,
            "narrative_subtext": f"Hình ảnh bám sát chủ thể kịch bản: {short_subj}",
            "visual_metaphor": f"Cinematic representation of {short_subj}",
            "subject": short_subj,
            "production_method": method,
            "tool_choice": tool,
            "motion_level": motion,
            "search_queries": queries,
            "google_image_queries": queries,
            "negative_cues": ["cartoon", "low quality", "blurry", "text overlay", "split screen collage"],
            "cinematic_ai_prompt": f"Cinematic 8k vertical shot of {short_subj}, dramatic lighting, photorealistic 9:16",
        }
