"""
app.graph.tools.media_tools — Bộ công cụ (@tool) thông minh cho LLM Đạo diễn trong LangGraph.

Thiết kế theo triết lý Hybrid Viral Shorts & Adaptive Dual-Scope Search (`SCENE_SPECIFIC` vs `SCRIPT_OVERALL`):
1. Cơ chế Tự Động Phân Loại Ngữ Nghĩa Câu Văn Khi Chia Nhỏ (`search_scope`):
   - `SCENE_SPECIFIC` (Câu văn chia nhỏ VẪN CÓ NGHĨA ĐỘC LẬP):
     Khi tách câu thoại của phân cảnh ra để đi search riêng lẻ mà vẫn rõ chủ thể + hành động thị giác
     (VD: "stray golden retriever shivering in pouring rain", "vet warming tiny rescued kitten in clinic"),
     hệ thống cứ thế đi search trực tiếp theo ý câu văn nhỏ đó!
   - `SCRIPT_OVERALL` (Câu văn chia nhỏ BỊ VÔ NGHĨA / CỤT NGỦN / LỆCH CHỦ ĐỀ KHI ĐỨNG MỘT MÌNH):
     Khi kịch bản chia nhỏ thành các câu dẫn, câu chuyển ý, giải thích tâm lý, hoặc chi tiết phụ
     (VD: "For three days nobody knew why...", "When they looked inside the old leather boot...",
     "Canine behaviorists call this maternal imprinting..."), nếu đi search theo câu nhỏ sẽ vô nghĩa hoặc lạc đề.
     Khi đó hệ thống BỎ QUA từ ngữ vụn của câu nhỏ và ĐI SEARCH TỔNG THỂ XOAY QUANH KỊCH BẢN (`overall_script_query`)
     để lấy các đoạn video / hình ảnh xoay quanh câu chuyện chính ghép vào video!
2. `clone_youtube_short_peak_clip`:
   - Ưu tiên cho Cảnh 1 (Video Hook) và 2–3 cảnh Cao trào (Peak) xen kẽ trong video.
   - Hỗ trợ cả tìm kiếm theo cảnh (`SCENE_SPECIFIC`) lẫn kho video xoay quanh tổng thể kịch bản (`SCRIPT_OVERALL`),
     kèm cơ chế cắt các phân đoạn Peak không trùng lặp (`_used_short_windows`) và kiểm duyệt Gemini Vision.
3. `search_google_cinematic_image`:
   - Tìm ảnh Top Search Google Images theo câu nhỏ (`SCENE_SPECIFIC`) hoặc xoay quanh tổng thể kịch bản (`SCRIPT_OVERALL`).
"""
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain_core.tools import tool

from app.agents.media_agent import MediaAgent, clean_scene_search_query
from config.settings import settings

logger = logging.getLogger(__name__)
_yt_url_lock = threading.Lock()

# Bộ nhớ đệm kết quả tìm kiếm YouTube Shorts theo từ khóa & các khoảng thời gian đã cắt trên từng URL
_yt_flat_search_cache: Dict[str, List[Dict[str, Any]]] = {}
_used_short_windows: Dict[str, List[Tuple[float, float]]] = {}

# Chặn triệt để các video Podcast / Talking Head / Quảng cáo bán hàng / Đồ chơi / Game / Meme lệch chủ đề
_TALKING_HEAD_BLOCKLIST = (
    "podcast", "interview", "reaction", "reacts", "q&a", "livestream", "stream highlight",
    "talking", "lecture", "ted talk", "joe rogan", "lex fridman", "review", "unboxing",
    "vlog", "storytime", "asmr", "tutorial", "how to", "course", "lesson",
    "animation meme", "gacha", "roblox", "minecraft", "fortnite", "valorant", "lyrics", "karaoke",
    "ultrasonic", "chaser", "repeller", "bark control", "collar", "gadget", "device", "product",
    "amazon", "shop", "buy", "price", "discount", "commercial", "promo", "toy", " plush",
    "vet explains", "veterinarian", "doctor explains", "training tips", "compilation",
    "bootie", "michael kors", "fashion", "handbag", "leathercare", "shoe polish",
)

_YT_QUERY_FILLER_WORDS = {
    "cinematic", "4k", "8k", "hd", "edit", "edits", "visuals", "footage", "scene", "scenes",
    "dramatic", "vertical", "shot", "shots", "video", "clip", "clips", "short", "shorts",
    "movie", "still", "photo", "photograph", "picture", "image",
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "with", "by", "from", "and", "or",
}

_GENERIC_NON_SUBJECT_WORDS = {
    "looking", "seeing", "watching", "waiting", "running", "walking", "sitting", "standing",
    "crying", "sleeping", "hiding", "holding", "pulling", "dragged", "refused", "left", "found",
    "secret", "hidden", "truth", "story", "moment", "reason", "suddenly", "finally", "never",
    "inside", "under", "behind", "dark", "cold", "warm", "tiny", "huge", "giant", "old", "new",
    "aggressive", "emotional", "sad", "happy", "scared", "terrified", "shocking", "heart",
}

# Các từ đạo cụ vụn vặt hoặc từ trừu tượng khiến câu nhỏ khi tách khỏi kịch bản bị lệch chủ đề khi search
_DISTRACTOR_PROP_OR_ABSTRACT_WORDS = {
    "boot", "boots", "bootie", "shoe", "shoes", "leather", "cardboard", "blanket", "towel",
    "porch", "deck", "doorstep", "hormone", "oxytocin", "chemical", "imprinting", "behaviorist",
    "behaviorists", "scientist", "scientists", "psychologist", "theory", "phenomenon",
    "days", "hours", "weeks", "months", "years", "nobody", "knew", "froze", "realized",
    "inch", "single", "forever", "everything", "changed", "reason", "truth", "secret",
}


@tool
def clone_youtube_short_peak_clip(
    scene_id: int,
    subject_entity: str,
    search_scope: str,
    youtube_search_query: str,
    overall_script_query: str = "",
    fallback_broad_query: str = "",
    clip_role: str = "PEAK",
    target_duration_sec: float = 4.5,
    camera_motion: str = "zoom_in",
) -> Dict[str, Any]:
    """
    ƯU TIÊN SỐ 1 CHO CẢNH MỞ ĐẦU (HOOK - scene_id=1) VÀ 2-3 CẢNH CAO TRÀO (PEAK) XEN KẼ TRONG VIDEO.
    Công cụ này tìm kiếm video YouTube Shorts thực tế trên mạng, dò đoạn PEAK (Most Replayed / hành động cao trào),
    kiểm duyệt bằng Gemini Vision và cắt đoạn chuẩn 9:16.

    QUY TẮC QUAN TRỌNG NHẤT — CHỌN `search_scope` (`SCENE_SPECIFIC` vs `SCRIPT_OVERALL`):
    1. `search_scope = "SCENE_SPECIFIC"`:
       - Dùng khi câu thoại của phân cảnh này tách riêng ra VẪN CÓ NGHĨA THỊ GIÁC RÕ RÀNG, ĐỘC LẬP
         (VD: "A drenched stray dog shivering in the rain", "A mother dog carrying a tiny kitten").
       - Khi đó `youtube_search_query` đi search trực tiếp hành động của câu văn nhỏ này!
    2. `search_scope = "SCRIPT_OVERALL"`:
       - Dùng khi câu thoại của phân cảnh này nếu tách lẻ ra sẽ BỊ VÔ NGHĨA, CỤT NGỦN, TRỪU TƯỢNG, CÂU DẪN CHUYỂN Ý,
         hoặc chỉ nhắc tới đồ vật vụn vặt dễ làm lệch kết quả search (VD: "For three days nobody knew why...",
         "When they looked inside the old leather boot...", "Behaviorists call this maternal imprinting...").
       - Khi đó TUYỆT ĐỐI KHÔNG search theo từ ngữ vụn của câu nhỏ, mà đặt `youtube_search_query` xoay quanh
         TỔNG THỂ NỘI DUNG KỊCH BẢN (`overall_script_query`)!
    3. `overall_script_query`: Câu truy vấn (3-5 từ tiếng Anh) mô tả cốt truyện tổng thể của cả kịch bản
       (VD: "stray dog protecting abandoned kitten", "golden retriever rescuing kitten rain").
    """
    return {
        "tool_name": "clone_youtube_short_peak_clip",
        "scene_id": scene_id,
        "subject_entity": subject_entity,
        "search_scope": search_scope,
        "youtube_search_query": youtube_search_query,
        "overall_script_query": overall_script_query,
        "fallback_broad_query": fallback_broad_query,
        "clip_role": clip_role,
        "target_duration_sec": target_duration_sec,
        "camera_motion": camera_motion,
    }


@tool
def search_google_cinematic_image(
    scene_id: int,
    subject_entity: str,
    search_scope: str,
    smart_image_query: str,
    overall_script_query: str = "",
    camera_motion: str = "zoom_in",
) -> Dict[str, Any]:
    """
    CÔNG CỤ TÌM ẢNH TOP SEARCH GOOGLE IMAGES SẮC NÉT CHO CÁC CẢNH XEN KẼ GIỮA CÁC VIDEO CLIP.

    QUY TẮC QUAN TRỌNG NHẤT — CHỌN `search_scope` (`SCENE_SPECIFIC` vs `SCRIPT_OVERALL`):
    1. `search_scope = "SCENE_SPECIFIC"`:
       - Khi câu văn của phân cảnh này chia nhỏ ra VẪN CÓ NGHĨA ĐỘC LẬP, RÕ RÀNG khi đem đi search
         (VD: "vet examining rescued puppy and kitten on clinic table").
       - Cứ thế đặt `smart_image_query` theo đúng ý câu văn nhỏ đó (bắt buộc kèm `subject_entity`).
    2. `search_scope = "SCRIPT_OVERALL"`:
       - Khi câu văn của phân cảnh này chia nhỏ ra bị VÔ NGHĨA, MƠ HỒ, CÂU DẪN/KẾT LUẬN, hoặc nhắc đến
         đạo cụ phụ làm lạc đề (VD: "For three days it refused to move", "Inside the old boot...",
         "Scientists call this oxytocin bonding").
       - Khi đó KHÔNG search theo câu nhỏ vô nghĩa mà đặt `smart_image_query` xoay quanh TỔNG THỂ KỊCH BẢN
         (`overall_script_query`) với góc nhìn/khoảnh khắc phù hợp để ghép vào video!
    3. `overall_script_query`: Câu truy vấn (4-6 từ tiếng Anh) tóm tắt nội dung tổng thể xoay quanh kịch bản.
    """
    return {
        "tool_name": "search_google_cinematic_image",
        "scene_id": scene_id,
        "subject_entity": subject_entity,
        "search_scope": search_scope,
        "smart_image_query": smart_image_query,
        "overall_script_query": overall_script_query,
        "camera_motion": camera_motion,
    }


MEDIA_DIRECTOR_TOOLS = [clone_youtube_short_peak_clip, search_google_cinematic_image]


# ──────────────────────────────────────────────────────────────────────────────
# CÁC HÀM HỖ TRỢ PHÂN TÍCH PHẠM VI TÌM KIẾM (`SCENE_SPECIFIC` vs `SCRIPT_OVERALL`)
# ──────────────────────────────────────────────────────────────────────────────

def _extract_subject_anchor_tokens(subject_entity: str, topic: str, search_query: str) -> Set[str]:
    """Trích xuất tập từ khóa Danh từ Chủ thể bắt buộc phải xuất hiện trong tiêu đề YouTube Short."""
    raw = f"{subject_entity or ''} {topic or ''}".strip()
    if not raw:
        raw = search_query or ""
    tokens = re.findall(r"[a-zA-Z]{3,}", raw.lower())
    anchors = {
        t for t in tokens
        if t not in _YT_QUERY_FILLER_WORDS
        and t not in _GENERIC_NON_SUBJECT_WORDS
        and t not in _DISTRACTOR_PROP_OR_ABSTRACT_WORDS
    }
    if any(k in anchors for k in ("dog", "dogs", "puppy", "puppies", "canine", "retriever", "husky", "shepherd", "stray")):
        anchors.update({"dog", "dogs", "puppy", "puppies", "golden", "retriever", "stray", "shelter", "pet"})
    if any(k in anchors for k in ("cat", "cats", "kitten", "kittens", "feline")):
        anchors.update({"cat", "cats", "kitten", "kittens"})
    return anchors


def _clean_yt_search_phrase(
    q: str,
    subject_entity: str = "",
    max_words: int = 5,
    strip_distractor_props: bool = False,
) -> str:
    """Làm sạch câu truy vấn: bỏ từ nhiễu (4k, edit, cinematic), tùy chọn bỏ đạo cụ gây nhiễu và đảm bảo có chủ thể."""
    words = re.findall(r"[a-zA-Z0-9]+", q or "")
    core = []
    for w in words:
        wl = w.lower()
        if wl in _YT_QUERY_FILLER_WORDS or len(w) < 2:
            continue
        if strip_distractor_props and wl in _DISTRACTOR_PROP_OR_ABSTRACT_WORDS:
            continue
        core.append(w)

    subj_words = [
        w for w in re.findall(r"[a-zA-Z0-9]+", subject_entity or "")
        if w.lower() not in _YT_QUERY_FILLER_WORDS
        and w.lower() not in _DISTRACTOR_PROP_OR_ABSTRACT_WORDS
        and len(w) >= 3
    ]
    core_lower = {w.lower() for w in core}
    for sw in reversed(subj_words[:2]):
        if sw.lower() not in core_lower:
            core.insert(0, sw)
            core_lower.add(sw.lower())
    return " ".join(core[:max_words]).strip()


def _is_meaningless_or_prop_distracted_when_split(
    narration: str,
    scene_query: str,
    subject_entity: str,
) -> bool:
    """
    Kiểm tra xem câu văn của cảnh khi chia nhỏ ra có bị:
    1. Vô nghĩa / quá ngắn / chỉ là câu chuyển ý trừu tượng ("For three days nobody knew why...")
    2. Hoặc bị đạo cụ phụ làm lệch hướng tìm kiếm ("old leather boot", "oxytocin hormone", "behaviorists call this")
    Nếu trả về True -> Bắt buộc chuyển sang chế độ `SCRIPT_OVERALL` (đi search tổng thể xoay quanh kịch bản)!
    """
    q_tokens = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", scene_query or "")]
    narr_tokens = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", narration or "")]

    # Nếu câu truy vấn chứa các từ đạo cụ/trừu tượng dễ làm lệch kết quả search (VD: "boot", "hormone", "behaviorist")
    distractor_hits = sum(1 for t in q_tokens if t in _DISTRACTOR_PROP_OR_ABSTRACT_WORDS)
    if distractor_hits >= 1:
        return True

    # Kiểm tra xem câu thoại sau khi bỏ các từ chung chung và từ chủ thể còn lại từ hành động/bối cảnh thực chất nào không
    subj_tokens = {w.lower() for w in re.findall(r"[a-zA-Z]{3,}", subject_entity or "")}
    meaningful_scene_words = [
        t for t in narr_tokens
        if t not in _YT_QUERY_FILLER_WORDS
        and t not in _GENERIC_NON_SUBJECT_WORDS
        and t not in _DISTRACTOR_PROP_OR_ABSTRACT_WORDS
        and t not in subj_tokens
    ]
    if len(meaningful_scene_words) <= 1:
        return True

    return False


def _resolve_adaptive_search_queries(
    search_scope: str,
    scene_query: str,
    overall_script_query: str,
    fallback_query: str,
    subject_entity: str,
    topic: str,
    narration: str,
    max_words: int = 5,
) -> Tuple[str, List[str]]:
    """
    Trả về `(effective_scope, ordered_queries)` theo đúng nguyên tắc của người dùng:
    - Nếu chia nhỏ câu văn ra đi search mà VẪN CÓ NGHĨA (`SCENE_SPECIFIC`):
      Ưu tiên số 1 là `scene_query` (câu nhỏ), tiếp theo mới đến `overall_script_query` (tổng thể kịch bản).
    - Nếu chia nhỏ câu văn ra bị VÔ NGHĨA hoặc LỆCH CHỦ ĐỀ (`SCRIPT_OVERALL`):
      Ưu tiên số 1 là `overall_script_query` (xoay quanh tổng thể kịch bản), loại bỏ các từ đạo cụ gây nhiễu!
    """
    scope_norm = (search_scope or "SCENE_SPECIFIC").strip().upper()
    if scope_norm not in ("SCENE_SPECIFIC", "SCRIPT_OVERALL"):
        scope_norm = "SCENE_SPECIFIC"

    # Tự động phát hiện nếu câu nhỏ thực chất bị vô nghĩa hoặc vướng đạo cụ gây nhiễu (như "old leather boot")
    if scope_norm == "SCENE_SPECIFIC" and _is_meaningless_or_prop_distracted_when_split(
        narration=narration,
        scene_query=scene_query,
        subject_entity=subject_entity,
    ):
        scope_norm = "SCRIPT_OVERALL"

    overall_base = overall_script_query or topic or subject_entity
    q_overall = _clean_yt_search_phrase(
        overall_base,
        subject_entity=subject_entity,
        max_words=max_words,
        strip_distractor_props=True,
    )
    q_fallback = _clean_yt_search_phrase(
        fallback_query or f"{subject_entity} {topic}",
        subject_entity=subject_entity,
        max_words=max_words,
        strip_distractor_props=True,
    )

    if scope_norm == "SCRIPT_OVERALL":
        # Câu nhỏ vô nghĩa khi tách lẻ -> Lấy nội dung xoay quanh TỔNG THỂ KỊCH BẢN làm gốc!
        q_scene_cleaned = _clean_yt_search_phrase(
            scene_query,
            subject_entity=subject_entity,
            max_words=max_words,
            strip_distractor_props=True,
        )
        ordered = [q for q in dict.fromkeys([q_overall, q_scene_cleaned, q_fallback]) if q]
    else:
        # Câu nhỏ vẫn có nghĩa rõ ràng -> Cứ thế search trực tiếp câu nhỏ trước, dự phòng bằng tổng thể kịch bản!
        q_scene = _clean_yt_search_phrase(
            scene_query,
            subject_entity=subject_entity,
            max_words=max_words,
            strip_distractor_props=False,
        )
        ordered = [q for q in dict.fromkeys([q_scene, q_overall, q_fallback]) if q]

    return scope_norm, ordered


def _find_best_peak_window(
    info: Dict[str, Any],
    clip_dur: float,
    role: str = "PEAK",
    existing_windows: Optional[List[Tuple[float, float]]] = None,
) -> Optional[Tuple[float, float]]:
    """
    Xác định mốc thời gian (start_sec, end_sec) đắt giá nhất trong chiếc YouTube Short,
    đồng thời đảm bảo KHÔNG TRÙNG LẶP với các khoảng `existing_windows` nếu cùng 1 video tổng thể
    được cắt nhiều phân đoạn khác nhau để ghép vào kịch bản!
    """
    total_dur = float(info.get("duration") or 15.0)
    clip_dur = max(3.0, min(clip_dur, 8.0))
    used_wins = existing_windows or []

    def _overlaps(s: float, e: float) -> bool:
        for us, ue in used_wins:
            if max(s, us) < min(e, ue) - 0.4:
                return True
        return False

    if total_dur <= clip_dur + 1.0:
        if used_wins:
            return None
        return 0.0, max(2.0, total_dur)

    # 1. Thử lấy từ đỉnh Most Replayed (heatmap)
    heatmap = info.get("heatmap")
    if isinstance(heatmap, list) and len(heatmap) >= 3:
        sorted_pts = sorted(heatmap, key=lambda x: float(x.get("value", 0.0)), reverse=True)
        for pt in sorted_pts[:4]:
            if float(pt.get("value", 0.0)) > 0.25:
                peak_center = (float(pt.get("start_time", 0.0)) + float(pt.get("end_time", 0.0))) / 2.0
                start_s = max(0.5, min(peak_center - clip_dur / 2.0, total_dur - clip_dur - 0.3))
                end_s = start_s + clip_dur
                if not _overlaps(start_s, end_s):
                    return round(start_s, 2), round(end_s, 2)

    # 2. Các mốc ứng viên theo vai trò (HOOK đầu video, hoặc các khúc PEAK rải đều trong video tổng thể)
    candidate_ratios = [0.05, 0.32, 0.58, 0.75] if (role or "").upper() == "HOOK" else [0.32, 0.58, 0.15, 0.75]
    for ratio in candidate_ratios:
        start_s = 0.8 if ratio <= 0.08 else max(1.0, min(total_dur * ratio, total_dur - clip_dur - 0.4))
        end_s = start_s + clip_dur
        if end_s <= total_dur and not _overlaps(start_s, end_s):
            return round(start_s, 2), round(end_s, 2)

    return None


def _verify_short_clip_with_gemini_vision(
    video_path: str,
    topic: str,
    narration: str,
    subject_entity: str,
    overall_script_query: str = "",
    search_scope: str = "SCENE_SPECIFIC",
    video_title: str = "",
) -> Tuple[bool, float, str]:
    """
    Trích xuất 3 khung hình (tile=3x1) từ đoạn clip vừa cắt và nhờ Gemini Vision kiểm duyệt:
    - Nếu `search_scope == "SCENE_SPECIFIC"`: Kiểm tra khớp chủ thể + hành động của phân cảnh hoặc kịch bản.
    - Nếu `search_scope == "SCRIPT_OVERALL"`: Câu thoại lẻ bị cụt/vô nghĩa nên ta đánh giá xem video có
      XOAY QUANH CỐT TRUYỆN TỔNG THỂ CỦA KỊCH BẢN (`overall_script_query` / `topic`) và đúng chủ thể chính hay không!
    """
    if not Path(video_path).exists():
        return False, 0.0, "File không tồn tại"

    with tempfile.TemporaryDirectory() as tmp_v:
        sheet_jpg = str(Path(tmp_v) / "verify_sheet.jpg")
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", video_path,
            "-vf", "fps=1,scale=360:640:force_original_aspect_ratio=increase,crop=360:640,tile=3x1",
            "-frames:v", "1", "-q:v", "4",
            sheet_jpg,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=8, check=False)
        except Exception as e:
            return True, 7.2, f"Skip sheet extract ({e})"

        if not Path(sheet_jpg).exists() or Path(sheet_jpg).stat().st_size < 3000:
            return True, 7.2, "Sheet fallback"

        b64_img = base64.b64encode(Path(sheet_jpg).read_bytes()).decode("ascii")

    try:
        import requests
        from app.utils.key_rotator import KeyRotator

        keys = settings.get_gemini_keys_pool()
        rotator = KeyRotator(keys) if keys else None
        if not rotator or not rotator.has_keys:
            return True, 7.5, "No Gemini keys for vision check"

        scope_note = (
            f'NOTE: This scene line is part of an overall story ("{overall_script_query or topic}"). '
            f'APPROVE (passed=true, score>=7.5) if the clip shows real footage revolving around the overall story '
            f'and main subject ("{subject_entity or topic}"), even if it does not show minor props mentioned in the line!'
            if search_scope == "SCRIPT_OVERALL"
            else f'APPROVE (passed=true, score>=7.5) if the clip shows the main subject ("{subject_entity or topic}") '
                 f'fitting the scene or overall story ("{overall_script_query or topic}").'
        )

        prompt = f"""You are a Visual Quality & Story Relevance Inspector for a YouTube Shorts video.
Look at this 3-frame strip extracted from a candidate video clip:
- Overall Script Topic: "{topic}"
- Core Story Theme: "{overall_script_query or topic}"
- Main Subject: "{subject_entity or topic}"
- Current Scene Line ({search_scope}): "{narration}"
- Source Video Title: "{video_title}"

{scope_note}

REJECT IMMEDIATELY (passed=false, score < 5) ONLY IF the clip shows:
1. A product commercial / fashion ad / gadget demo / shopping promotion (e.g., shoe/boot ad, ultrasonic repeller, collar ad)
2. A person sitting and talking to a camera/microphone (podcast, reaction, vlog, lecture)
3. Video game / cartoon / anime / meme
4. Giant burned-in text covering the middle of the frame
5. A completely unrelated subject that does not belong to "{overall_script_query or topic}"

Return ONLY valid JSON:
{{"passed": true, "score": 8.5, "reason": "Shows real footage matching the script story"}}"""

        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": b64_img}},
                ]
            }],
            "generationConfig": {"temperature": 0.1},
        }

        for model_name, api_key in rotator.iter_best_model_key_pairs(max_total_attempts=2):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            try:
                resp = requests.post(url, json=payload, timeout=8)
                if resp.status_code == 200:
                    cands = resp.json().get("candidates") or []
                    if cands:
                        txt = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
                        m = re.search(r"\{.*\}", txt, re.DOTALL)
                        if m:
                            data = json.loads(m.group(0))
                            score = float(data.get("score", 0.0))
                            passed = bool(data.get("passed", False)) and score >= 6.8
                            reason = str(data.get("reason", ""))
                            return passed, score, reason
                elif resp.status_code in (429, 403, 404, 503):
                    rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:200])
            except Exception:
                continue
    except Exception as ex:
        logger.debug(f"[YouTubePeakTool] Vision check fallback: {ex}")

    return True, 7.2, "Fallback pass"


def _search_youtube_flat_cached(
    search_phrases: List[str],
    subject_anchors: Set[str],
    all_query_tokens: Set[str],
) -> List[Dict[str, Any]]:
    """
    Quét nhanh danh sách ứng viên YouTube Shorts (`extract_flat="in_playlist"`) có bộ nhớ đệm (cache)
    để khi nhiều phân cảnh cùng tìm xoay quanh tổng thể kịch bản (`overall_script_query`),
    các luồng song song dùng chung ngay danh sách Shorts chất lượng cao mà không phải đợi tìm lại!
    """
    import yt_dlp

    flat_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "socket_timeout": 8,
        "ignoreerrors": True,
    }

    candidates: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    for sp in search_phrases[:3]:
        cache_key = sp.lower().strip()
        with _yt_url_lock:
            cached_entries = _yt_flat_search_cache.get(cache_key)

        if cached_entries is None:
            yt_query = f"ytsearch15:{sp} #shorts"
            try:
                with yt_dlp.YoutubeDL(flat_opts) as ydl:
                    res = ydl.extract_info(yt_query, download=False)
                cached_entries = [
                    e for e in ((res or {}).get("entries") or [])
                    if e and isinstance(e, dict)
                ]
            except Exception:
                cached_entries = []
            with _yt_url_lock:
                _yt_flat_search_cache[cache_key] = cached_entries

        for entry in cached_entries:
            vid_id = entry.get("id") or ""
            if not vid_id or vid_id in seen_ids:
                continue
            seen_ids.add(vid_id)
            webpage_url = entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid_id}"
            if webpage_url.startswith("/"):
                webpage_url = f"https://www.youtube.com{webpage_url}"

            dur = float(entry.get("duration") or 0)
            if dur > 0 and (dur < 6 or dur > 85):
                continue

            title = str(entry.get("title") or "")
            title_lower = title.lower()
            if any(bad in title_lower for bad in _TALKING_HEAD_BLOCKLIST):
                continue

            anchor_hits = sum(1 for a in subject_anchors if a in title_lower)
            if subject_anchors and anchor_hits == 0:
                continue

            action_hits = sum(1 for tok in all_query_tokens if tok in title_lower)
            views = int(entry.get("view_count") or 0)
            score = (anchor_hits * 85.0) + (action_hits * 45.0) + min(views / 25000.0, 60.0)
            candidates.append({
                "id": vid_id,
                "url": webpage_url,
                "title": title,
                "duration": dur,
                "score": score,
            })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def execute_youtube_short_peak_clone_sync(
    search_query: str,
    output_path: str,
    target_duration_sec: float = 4.5,
    clip_role: str = "PEAK",
    used_urls: Optional[Set[str]] = None,
    fallback_query: str = "",
    subject_entity: str = "",
    narration: str = "",
    topic: str = "",
    search_scope: str = "SCENE_SPECIFIC",
    overall_script_query: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Thực thi tìm kiếm & cắt đoạn Peak từ YouTube Shorts theo chiến lược kép:
    - Nếu `search_scope == "SCENE_SPECIFIC"` (câu văn nhỏ có nghĩa rõ ràng):
      Tìm trực tiếp theo câu văn nhỏ (`search_query`), và dùng `overall_script_query` (tổng thể kịch bản) làm dự phòng ngay phía sau.
    - Nếu `search_scope == "SCRIPT_OVERALL"` (câu văn nhỏ khi tách lẻ bị vô nghĩa / cụt ngủn / vướng đạo cụ phụ):
      Đi search trực tiếp các video xoay quanh TỔNG THỂ KỊCH BẢN (`overall_script_query`),
      lấy các video khác nhau hoặc cắt các khúc Peak không trùng lặp để ghép vào video!
    """
    used = used_urls if used_urls is not None else set()
    effective_scope, search_phrases = _resolve_adaptive_search_queries(
        search_scope=search_scope,
        scene_query=search_query,
        overall_script_query=overall_script_query,
        fallback_query=fallback_query,
        subject_entity=subject_entity,
        topic=topic,
        narration=narration,
        max_words=5,
    )
    subject_anchors = _extract_subject_anchor_tokens(subject_entity, topic, search_phrases[0] if search_phrases else search_query)

    logger.info(
        f"🎯 [YouTubePeakTool] Scope={effective_scope} | Role={clip_role} | "
        f"Queries={search_phrases} (raw_scene='{search_query}', overall='{overall_script_query}')"
    )

    all_query_tokens = {
        w.lower()
        for q in search_phrases
        for w in re.findall(r"[a-zA-Z]{3,}", q.lower())
        if w.lower() not in _YT_QUERY_FILLER_WORDS
    }

    # ── TẦNG 1: SĂN YOUTUBE SHORTS XOAY QUANH CẢNH HOẶC TỔNG THỂ KỊCH BẢN ──
    try:
        import yt_dlp

        candidates = _search_youtube_flat_cached(search_phrases, subject_anchors, all_query_tokens)

        # Ưu tiên các URL chưa từng dùng trước; nếu hết URL mới thì cho phép cắt khúc Peak khác (không trùng lặp) từ video dài >= 16s
        unused_cands = [c for c in candidates if c["url"] not in used]
        reusable_multi_window_cands = [
            c for c in candidates
            if c["url"] in used and (float(c.get("duration") or 0) == 0 or float(c.get("duration") or 0) >= 16.0)
        ]
        ordered_cands = unused_cands[:4] + reusable_multi_window_cands[:2]

        for cand in ordered_cands[:4]:
            vid_url = cand["url"]
            with _yt_url_lock:
                existing_wins = list(_used_short_windows.get(vid_url) or [])
                if vid_url in used and len(existing_wins) >= 2:
                    continue
                used.add(vid_url)

            info_entry: Dict[str, Any] = cand
            try:
                info_opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 8}
                with yt_dlp.YoutubeDL(info_opts) as ydl_info:
                    full_info = ydl_info.extract_info(vid_url, download=False)
                    if isinstance(full_info, dict):
                        dur_full = float(full_info.get("duration") or 0)
                        if dur_full < 6 or dur_full > 85:
                            continue
                        desc_lower = f"{(full_info.get('title') or '')} {(full_info.get('description') or '')[:300]}".lower()
                        if any(bad in desc_lower for bad in _TALKING_HEAD_BLOCKLIST):
                            continue
                        info_entry = full_info
            except Exception:
                pass

            win_pair = _find_best_peak_window(
                info_entry,
                target_duration_sec + 0.8,
                role=clip_role,
                existing_windows=existing_wins,
            )
            if not win_pair:
                continue
            start_s, end_s = win_pair
            clip_len = max(3.0, end_s - start_s)

            with tempfile.TemporaryDirectory() as tmp_dir:
                raw_dl_template = str(Path(tmp_dir) / "raw_short.%(ext)s")
                dl_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "format": "bestvideo[height<=1920][ext=mp4]/best[height<=1920][ext=mp4]/best[ext=mp4]/best",
                    "outtmpl": raw_dl_template,
                    "socket_timeout": 12,
                    "retries": 1,
                    "nopart": True,
                }
                try:
                    from yt_dlp.utils import download_range_func
                    dl_opts["download_ranges"] = download_range_func(None, [(start_s, end_s + 0.5)])
                except Exception:
                    pass

                try:
                    with yt_dlp.YoutubeDL(dl_opts) as ydl_dl:
                        ydl_dl.download([vid_url])
                except Exception:
                    dl_opts.pop("download_ranges", None)
                    try:
                        with yt_dlp.YoutubeDL(dl_opts) as ydl_dl2:
                            ydl_dl2.download([vid_url])
                    except Exception:
                        continue

                downloaded_files = list(Path(tmp_dir).glob("raw_short.*"))
                if not downloaded_files:
                    continue
                raw_file = str(downloaded_files[0])
                if Path(raw_file).stat().st_size < 15000:
                    continue

                actual_start_s = 0.0
                if "download_ranges" not in dl_opts:
                    if not existing_wins:
                        try:
                            from app.utils.ffmpeg_helper import find_hook_peak_action_offset
                            detected_peak = find_hook_peak_action_offset(raw_file, clip_len)
                            actual_start_s = detected_peak if detected_peak > 0.0 else start_s
                        except Exception:
                            actual_start_s = start_s
                    else:
                        actual_start_s = start_s

                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                try:
                    from app.utils.ffmpeg_helper import trim_video
                    trim_video(
                        input_path=raw_file,
                        duration_seconds=clip_len,
                        output_path=output_path,
                        width=1080,
                        height=1920,
                        fps=30,
                        apply_grading=True,
                        is_hook_scene=((clip_role or "").upper() == "HOOK"),
                        fair_use_transform=True,
                        start_offset_sec=actual_start_s,
                    )
                except Exception:
                    vf_filter = (
                        "scale=1150:2044:force_original_aspect_ratio=increase,"
                        "crop=1080:1920,fps=30,format=yuv420p"
                    )
                    cmd = [
                        "ffmpeg", "-y", "-v", "error",
                        "-ss", f"{actual_start_s:.2f}",
                        "-i", raw_file,
                        "-t", f"{clip_len:.2f}",
                        "-an", "-vf", vf_filter,
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "21",
                        output_path,
                    ]
                    subprocess.run(cmd, capture_output=True, timeout=25, check=False)

                if Path(output_path).exists() and Path(output_path).stat().st_size > 20000:
                    passed_vis, vis_score, vis_reason = _verify_short_clip_with_gemini_vision(
                        video_path=output_path,
                        topic=topic,
                        narration=narration,
                        subject_entity=subject_entity or topic,
                        overall_script_query=overall_script_query or (search_phrases[0] if search_phrases else topic),
                        search_scope=effective_scope,
                        video_title=cand["title"],
                    )
                    if not passed_vis:
                        logger.info(
                            f"🚫 [YouTubePeakTool] Gemini Vision từ chối Short '{cand['title'][:45]}' "
                            f"(điểm={vis_score}/10: {vis_reason}) -> Thử ứng viên tiếp theo!"
                        )
                        try:
                            Path(output_path).unlink(missing_ok=True)
                        except Exception:
                            pass
                        continue

                    win_start = start_s if "download_ranges" in dl_opts else actual_start_s
                    win_end = win_start + clip_len
                    with _yt_url_lock:
                        _used_short_windows.setdefault(vid_url, []).append((win_start, win_end))

                    logger.info(
                        f"🔥 [YouTubePeakTool] Đã clone & duyệt Vision ({vis_score}/10, scope={effective_scope}) "
                        f"đoạn {clip_role} ({win_start:.1f}s-{win_end:.1f}s) từ Shorts: '{cand['title'][:55]}' ({vid_url})"
                    )
                    return {
                        "media_path": output_path,
                        "media_type": "hook_ready_video" if (clip_role or "").upper() == "HOOK" else "video",
                        "source_url": vid_url,
                        "title": cand["title"],
                        "window": f"{win_start:.1f}s-{win_end:.1f}s",
                        "vision_score": vis_score,
                        "search_scope": effective_scope,
                    }
    except Exception as e_yt:
        logger.debug(f"[YouTubePeakTool] Lỗi nhánh YouTube Shorts: {e_yt}")

    # ── TẦNG 2 (DỰ PHÒNG VIDEO SẠCH): TÌM VIDEO HD DỌC TRÊN PEXELS / PIXABAY XOAY QUANH KỊCH BẢN ──
    try:
        media_agent = MediaAgent()
        va_vid = {
            "subject": subject_entity or topic,
            "search_queries": search_phrases,
            "is_hook_scene": ((clip_role or "").upper() == "HOOK"),
        }
        effective_narr = (
            f"{overall_script_query or topic}. {narration}"
            if effective_scope == "SCRIPT_OVERALL"
            else narration
        )
        best_stock_v = media_agent.search_best_video_clip(
            query=search_phrases[0] if search_phrases else search_query,
            used_urls=used,
            visual_analysis=va_vid,
            narration=effective_narr,
            topic=overall_script_query or topic,
        )
        if best_stock_v and best_stock_v.get("url"):
            v_url = best_stock_v["url"]
            with tempfile.TemporaryDirectory() as tmp_s:
                raw_stock = str(Path(tmp_s) / "stock_raw.mp4")
                media_agent.download_file(v_url, raw_stock)
                if Path(raw_stock).exists() and Path(raw_stock).stat().st_size > 20000:
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    from app.utils.ffmpeg_helper import trim_video
                    trim_video(
                        input_path=raw_stock,
                        duration_seconds=max(3.0, target_duration_sec),
                        output_path=output_path,
                        width=1080,
                        height=1920,
                        fps=30,
                        apply_grading=True,
                        is_hook_scene=((clip_role or "").upper() == "HOOK"),
                        fair_use_transform=False,
                    )
                    if Path(output_path).exists() and Path(output_path).stat().st_size > 20000:
                        used.add(v_url)
                        logger.info(
                            f"🎬 [YouTubePeakTool -> StockVideo HD] Đã tải & duyệt video Pexels/Pixabay "
                            f"(scope={effective_scope}) cho {clip_role}: '{best_stock_v.get('title', '')[:50]}'"
                        )
                        return {
                            "media_path": output_path,
                            "media_type": "hook_ready_video" if (clip_role or "").upper() == "HOOK" else "video",
                            "source_url": v_url,
                            "title": best_stock_v.get("title", "HD Portrait Stock Video"),
                            "window": f"0.0s-{target_duration_sec:.1f}s",
                            "search_scope": effective_scope,
                        }
    except Exception as e_st:
        logger.debug(f"[YouTubePeakTool] Stock video fallback skip: {e_st}")

    return None


def execute_google_cinematic_image_sync(
    smart_query: str,
    output_path: str,
    narration: str = "",
    topic: str = "",
    used_urls: Optional[Set[str]] = None,
    is_hook_scene: bool = False,
    subject_entity: str = "",
    search_scope: str = "SCENE_SPECIFIC",
    overall_script_query: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Thực thi công cụ tìm ảnh Top Search Google Images theo cơ chế kép (`SCENE_SPECIFIC` vs `SCRIPT_OVERALL`):
    - Nếu câu nhỏ có nghĩa độc lập (`SCENE_SPECIFIC`): Tìm trực tiếp theo ý câu nhỏ (`smart_query`), dự phòng bằng `overall_script_query`.
    - Nếu câu nhỏ khi tách lẻ bị vô nghĩa / cụt ngủn / vướng đạo cụ phụ (`SCRIPT_OVERALL`):
      Tìm trực tiếp các hình ảnh xoay quanh TỔNG THỂ KỊCH BẢN (`overall_script_query`) và lọc trùng lặp (`used_urls`)
      để ghép vào video mượt mà, 100% đúng chủ đề kịch bản!
    """
    used = used_urls if used_urls is not None else set()
    effective_scope, search_qs = _resolve_adaptive_search_queries(
        search_scope=search_scope,
        scene_query=smart_query,
        overall_script_query=overall_script_query,
        fallback_query=f"{subject_entity} {topic}",
        subject_entity=subject_entity,
        topic=topic,
        narration=narration,
        max_words=6,
    )

    subj = (subject_entity or "").strip()
    if not subj or (len(subj.split()) == 1 and subj.lower() in _GENERIC_NON_SUBJECT_WORDS):
        subj = _clean_yt_search_phrase(overall_script_query or topic, max_words=3, strip_distractor_props=True) or search_qs[0]

    primary_q = search_qs[0] if search_qs else smart_query
    logger.info(
        f"🖼️ [GoogleImageTool] Scope={effective_scope} | Primary='{primary_q}' | "
        f"Pool={search_qs[:3]} (raw_scene='{smart_query}')"
    )

    va_photo = {
        "subject": subj,
        "literal_subject": (overall_script_query or subj) if effective_scope == "SCRIPT_OVERALL" else subj,
        "search_queries": search_qs[:3],
        "google_image_queries": search_qs[:3],
        "is_hook_scene": is_hook_scene,
    }

    # Khi câu nhỏ bị vô nghĩa (`SCRIPT_OVERALL`), truyền ngữ cảnh tổng thể kịch bản cho bộ chấm điểm Vision
    # để Vision chọn ảnh xoay quanh câu chuyện chính thay vì đòi hỏi chi tiết vụn vặt trong câu dẫn!
    effective_narration = (
        f"Overall story: {overall_script_query or topic} ({subj}). Context: {narration}"
        if effective_scope == "SCRIPT_OVERALL"
        else narration
    )

    media_agent = MediaAgent()
    best_photo = media_agent.search_best_photo(
        query=primary_q,
        used_urls=used,
        visual_analysis=va_photo,
        narration=effective_narration,
        topic=overall_script_query or topic,
    )
    if not best_photo or not best_photo.get("url"):
        return None

    img_url = best_photo["url"]
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    media_agent.download_file(img_url, output_path)
    if Path(output_path).exists() and Path(output_path).stat().st_size > 3000:
        used.add(img_url)
        return {
            "media_path": output_path,
            "media_type": "image",
            "source_url": img_url,
            "title": best_photo.get("title", ""),
            "curation_score": best_photo.get("curation_score", 9.0),
            "alternate_urls": list(best_photo.get("alternate_urls") or []),
            "search_scope": effective_scope,
        }
    return None


# ──────────────────────────────────────────────────────────────────────────────
# BỘ ĐẠO DIỄN LLM BIND_TOOLS (PHÂN TÍCH KỊCH BẢN TỔNG THỂ VS CÂU NHỎ TỰ ĐỘNG)
# ──────────────────────────────────────────────────────────────────────────────

def plan_media_tools_with_llm_sync(
    topic: str,
    scenes: List[Dict[str, Any]],
    primary_entity: Optional[str] = None,
    channel_niche: str = "cinematic",
) -> List[Dict[str, Any]]:
    """
    Sử dụng Gemini gắn `bind_tools([clone_youtube_short_peak_clip, search_google_cinematic_image])`
    để LLM đọc toàn bộ kịch bản và quyết định cho từng phân cảnh:
    - Khi câu văn nhỏ tự thân CÓ NGHĨA (`search_scope="SCENE_SPECIFIC"`): đi search trực tiếp theo ý câu văn nhỏ.
    - Khi câu văn nhỏ bị VÔ NGHĨA / CỤT NGỦN / LỆCH CHỦ ĐỀ nếu tách rời (`search_scope="SCRIPT_OVERALL"`):
      đi search nội dung xoay quanh TỔNG THỂ KỊCH BẢN (`overall_script_query`)!
    """
    total_scenes = len(scenes)
    if total_scenes == 0:
        return []

    full_script_text = " ".join(str(s.get("narration") or "").strip() for s in scenes if s.get("narration"))
    scenes_brief = [
        {
            "scene_id": s.get("scene_id"),
            "narration": s.get("narration", ""),
            "visual_keyword": s.get("visual_keyword", ""),
            "estimated_seconds": s.get("estimated_seconds", 4.0),
        }
        for s in scenes
    ]

    prompt = f"""You are an Elite Viral YouTube Shorts Visual Director.
Topic: "{topic}"
Primary Subject/Entity: "{primary_entity or topic}"
Channel Niche: "{channel_niche}"
Total Scenes: {total_scenes}

FULL SCRIPT STORY:
"{full_script_text}"

Individual Scenes Breakdown:
{json.dumps(scenes_brief, ensure_ascii=False, indent=2)}

YOUR MISSION:
Call EXACTLY ONE tool for EACH scene (from scene_id=1 to scene_id={total_scenes}) following these strict rules:

1. DEFINE THE OVERALL SCRIPT QUERY (`overall_script_query`) & MAIN SUBJECT (`subject_entity`):
   - First, read the FULL SCRIPT STORY and summarize its core visual story in a natural 3-5 word query: `overall_script_query` (e.g., "stray dog protecting abandoned kitten", "mother dog rescuing puppies rain").
   - Define `subject_entity` (e.g., "stray dog and kitten", "golden retriever dog"). Pass both `subject_entity` and `overall_script_query` into EVERY tool call!

2. DECIDE `search_scope` FOR EACH INDIVIDUAL SCENE (`"SCENE_SPECIFIC"` vs `"SCRIPT_OVERALL"`):
   - Evaluate whether the individual scene's sentence (`narration`) still makes clear, standalone visual sense when searched on YouTube/Google by itself:
     * CASE A — `search_scope = "SCENE_SPECIFIC"` (Split sentence IS visually meaningful on its own):
       The sentence describes a clear, concrete visual action of the main subject (e.g., "A drenched golden retriever shivering in the rain", "A veterinarian warming the rescued kitten in a clinic").
       -> Search directly for that scene's specific visual meaning!
     * CASE B — `search_scope = "SCRIPT_OVERALL"` (Split sentence becomes MEANINGLESS, FRAGMENTED, TRANSITIONAL, or PROP-DISTRACTED when isolated):
       The sentence is a time transition ("For three days nobody knew why it refused to move"), an abstract/scientific explanation ("Canine behaviorists call this maternal imprinting"), an emotional conclusion ("That single moment changed everything"), or focuses on a minor inanimate prop that would return unrelated product ads if searched literally (e.g., "inside an old leather boot" -> would return shoe ads!).
       -> Set `search_scope = "SCRIPT_OVERALL"` and write `youtube_search_query` / `smart_image_query` revolving around the OVERALL SCRIPT STORY (`overall_script_query`) with a natural angle variation (NEVER include distracting props like "boot" or abstract words)!

3. TOOL SELECTION (HOOK + 2-3 PEAK VIDEOS INTERLEAVED WITH IMAGES):
   - SCENE 1 (THE HOOK): Call `clone_youtube_short_peak_clip` with `clip_role="HOOK"`.
   - BODY SCENES (2 to {total_scenes}): Pick 2 to 3 peak scenes spaced across the script (e.g., scene 3, scene 5, scene 7) and call `clone_youtube_short_peak_clip` with `clip_role="PEAK"`.
   - For all other scenes, call `search_google_cinematic_image`.
   - NEVER use filler words like "4k edit", "cinematic visuals", or "movie scene" in `youtube_search_query`.

IMPORTANT: Emit tool calls for ALL {total_scenes} scenes in a single response!"""

    decisions_by_scene: Dict[int, Dict[str, Any]] = {}

    try:
        import requests
        from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS

        keys = settings.get_gemini_keys_pool()
        rotator = KeyRotator(keys) if keys else None
        if rotator and rotator.has_keys:
            func_declarations = []
            for t_obj in MEDIA_DIRECTOR_TOOLS:
                schema = t_obj.args_schema.model_json_schema() if getattr(t_obj, "args_schema", None) else {}
                props = {}
                for k_prop, v_prop in (schema.get("properties") or {}).items():
                    p_type = (v_prop.get("type") or "string").upper()
                    if p_type == "NUMBER":
                        p_type = "NUMBER"
                    elif p_type == "INTEGER":
                        p_type = "INTEGER"
                    else:
                        p_type = "STRING"
                    props[k_prop] = {
                        "type": p_type,
                        "description": v_prop.get("description") or k_prop,
                    }
                func_declarations.append({
                    "name": t_obj.name,
                    "description": t_obj.description,
                    "parameters": {
                        "type": "OBJECT",
                        "properties": props,
                        "required": schema.get("required") or ["scene_id"],
                    },
                })

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{"function_declarations": func_declarations}],
                "tool_config": {"function_calling_config": {"mode": "ANY"}},
                "generationConfig": {"temperature": 0.2},
            }

            for model_name, api_key in rotator.iter_best_model_key_pairs(GEMINI_PRIORITY_MODELS):
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                try:
                    resp = requests.post(url, json=payload, timeout=22)
                    if resp.status_code == 200:
                        data = resp.json()
                        parts = (
                            data.get("candidates", [{}])[0]
                            .get("content", {})
                            .get("parts", [])
                        )
                        for p in parts:
                            fc = p.get("functionCall")
                            if fc and isinstance(fc, dict):
                                t_name = fc.get("name", "")
                                t_args = dict(fc.get("args") or {})
                                sid = int(t_args.get("scene_id") or 0)
                                if sid > 0:
                                    t_args["tool_name"] = t_name
                                    decisions_by_scene[sid] = t_args
                        if decisions_by_scene:
                            scope_summary = ", ".join(
                                f"S{sid}:{d.get('search_scope', 'SCENE_SPECIFIC')}"
                                for sid, d in sorted(decisions_by_scene.items())
                            )
                            logger.info(
                                f"🧠 [LLM Tool Director] Đã chọn Tool & Scope cho {len(decisions_by_scene)}/{total_scenes} cảnh "
                                f"qua `{model_name}` ({scope_summary})"
                            )
                            break
                    elif resp.status_code in (429, 403, 404, 503):
                        rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:200])
                except Exception as req_err:
                    logger.debug(f"[LLM Tool Director] {model_name} error: {req_err}")
                    continue
    except Exception as ex:
        logger.warning(f"[LLM Tool Director] bind_tools fallback sang heuristic thông minh: {ex}")

    final_plan: List[Dict[str, Any]] = []
    motion_cycle = ["zoom_in", "pan_right", "zoom_out", "pan_left"]
    peak_indices = {1, max(2, total_scenes // 2), max(3, (total_scenes * 3) // 4)}
    default_subj = _clean_yt_search_phrase(primary_entity or topic, max_words=3, strip_distractor_props=True)
    default_overall_q = _clean_yt_search_phrase(topic, subject_entity=default_subj, max_words=5, strip_distractor_props=True)

    for idx, s in enumerate(scenes):
        sid = int(s.get("scene_id") or (idx + 1))
        if sid in decisions_by_scene:
            d = decisions_by_scene[sid]
            if not d.get("overall_script_query"):
                d["overall_script_query"] = default_overall_q
            if not d.get("search_scope"):
                d["search_scope"] = "SCENE_SPECIFIC"
            final_plan.append(d)
            continue

        narr = s.get("narration", "")
        kw = s.get("visual_keyword") or narr[:50]
        is_meaningless_split = _is_meaningless_or_prop_distracted_when_split(narr, kw, default_subj)
        scope = "SCRIPT_OVERALL" if is_meaningless_split else "SCENE_SPECIFIC"
        clean_kw = (
            default_overall_q
            if is_meaningless_split
            else _clean_yt_search_phrase(kw, subject_entity=default_subj, max_words=5)
        )
        dur = float(s.get("estimated_seconds") or 4.5)
        if sid in peak_indices:
            final_plan.append({
                "tool_name": "clone_youtube_short_peak_clip",
                "scene_id": sid,
                "subject_entity": default_subj,
                "search_scope": scope,
                "youtube_search_query": clean_kw,
                "overall_script_query": default_overall_q,
                "fallback_broad_query": default_overall_q,
                "clip_role": "HOOK" if sid == 1 else "PEAK",
                "target_duration_sec": dur,
                "camera_motion": "hyper_zoom" if sid == 1 else motion_cycle[idx % len(motion_cycle)],
            })
        else:
            final_plan.append({
                "tool_name": "search_google_cinematic_image",
                "scene_id": sid,
                "subject_entity": default_subj,
                "search_scope": scope,
                "smart_image_query": clean_kw,
                "overall_script_query": default_overall_q,
                "camera_motion": motion_cycle[idx % len(motion_cycle)],
            })

    return final_plan


def audit_and_decide_search_scopes_with_llm_sync(
    topic: str,
    scenes: List[Dict[str, Any]],
    proposed_decisions: List[Dict[str, Any]],
    primary_entity: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    LLM Node Đánh Giá & Phê Duyệt Quyết Định Tìm Kiếm (`SCENE_SPECIFIC` vs `SCRIPT_OVERALL`):
    Đọc toàn bộ kịch bản gốc (`full_script_text`) đối chiếu với từng câu văn đã chia nhỏ (`scenes`)
    và bản kế hoạch `proposed_decisions`:
    - Đánh giá từng câu văn nhỏ: Khi tách riêng ra khỏi kịch bản để đi search, câu này CÓ NGHĨA ĐỘC LẬP (`SCENE_SPECIFIC`)
      hay BỊ VÔ NGHĨA / CỤT NGỦN / LỆCH SANG ĐỒ VẬT PHỤ (`SCRIPT_OVERALL`)?
    - Nếu câu nhỏ vô nghĩa hoặc dễ gây lạc đề khi search riêng lẻ -> ép chuyển sang `SCRIPT_OVERALL` và viết lại
      câu truy vấn xoay quanh tổng thể kịch bản (`overall_script_query`)!
    - Nếu câu nhỏ có nghĩa rõ ràng -> duyệt `SCENE_SPECIFIC` và tinh chỉnh từ khóa sát câu nhỏ đó.
    """
    if not scenes or not proposed_decisions:
        return proposed_decisions

    full_script_text = " ".join(str(s.get("narration") or "").strip() for s in scenes if s.get("narration"))
    review_items = []
    dec_map = {int(d.get("scene_id") or 0): dict(d) for d in proposed_decisions if isinstance(d, dict)}

    for idx, s in enumerate(scenes):
        sid = int(s.get("scene_id") or (idx + 1))
        d = dec_map.get(sid, {})
        q_cur = d.get("youtube_search_query") or d.get("smart_image_query") or ""
        review_items.append({
            "scene_id": sid,
            "split_sentence": s.get("narration", ""),
            "proposed_tool": d.get("tool_name", "search_google_cinematic_image"),
            "proposed_scope": d.get("search_scope", "SCENE_SPECIFIC"),
            "proposed_query": q_cur,
            "overall_script_query": d.get("overall_script_query", ""),
        })

    audit_prompt = f"""You are the Chief LLM Search Scope Judge & Visual Auditor in a LangGraph video pipeline.
Topic: "{topic}"
Primary Subject: "{primary_entity or topic}"

FULL SCRIPT STORY:
"{full_script_text}"

PROPOSED SCENE DECISIONS TO AUDIT:
{json.dumps(review_items, ensure_ascii=False, indent=2)}

YOUR TASK:
Evaluate each scene (from scene_id=1 to {len(scenes)}) and decide whether searching the split sentence on its own makes sense:
1. If `split_sentence` when isolated from the script still has a clear, concrete, self-contained visual action of the main subject:
   - Set `"approved_scope": "SCENE_SPECIFIC"`
   - Keep or refine `"approved_query"` so it searches directly for that sentence's visual meaning (must include the main subject).
2. If `split_sentence` when isolated from the script becomes MEANINGLESS, VAGUE, TRANSITIONAL (e.g. "For three days nobody knew why...", "That moment changed everything"), SCIENTIFIC/ABSTRACT ("Behaviorists call this imprinting"), or focuses on a minor inanimate prop that would hijack search results (e.g. "inside an old leather boot" returning shoe commercials):
   - Set `"approved_scope": "SCRIPT_OVERALL"`
   - Rewrite `"approved_query"` so it searches for content REVOLVING AROUND THE OVERALL SCRIPT STORY (using a distinct angle/moment of the main story so scenes do not duplicate).

Return ONLY a valid JSON object in this exact format:
{{
  "overall_script_query": "3-5 word core story search query",
  "subject_entity": "2-4 word main subject",
  "scenes_audit": [
    {{
      "scene_id": 1,
      "approved_scope": "SCENE_SPECIFIC",
      "approved_query": "stray golden retriever shivering in rain",
      "reason": "Sentence clearly describes the dog shivering in rain"
    }}
  ]
}}"""

    try:
        import requests
        from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS

        keys = settings.get_gemini_keys_pool()
        rotator = KeyRotator(keys) if keys else None
        if rotator and rotator.has_keys:
            payload = {
                "contents": [{"parts": [{"text": audit_prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                },
            }
            for model_name, api_key in rotator.iter_best_model_key_pairs(GEMINI_PRIORITY_MODELS):
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                try:
                    resp = requests.post(url, json=payload, timeout=18)
                    if resp.status_code == 200:
                        cands = resp.json().get("candidates") or []
                        if cands:
                            txt = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
                            m = re.search(r"\{.*\}", txt, re.DOTALL)
                            if m:
                                parsed = json.loads(m.group(0))
                                top_overall_q = str(parsed.get("overall_script_query") or "").strip()
                                top_subj = str(parsed.get("subject_entity") or "").strip()
                                audits = parsed.get("scenes_audit") or []
                                audit_by_sid = {
                                    int(a.get("scene_id") or 0): a
                                    for a in audits
                                    if isinstance(a, dict) and a.get("scene_id")
                                }
                                if audit_by_sid:
                                    audited_list: List[Dict[str, Any]] = []
                                    for d in proposed_decisions:
                                        sid = int(d.get("scene_id") or 0)
                                        item = dict(d)
                                        if top_overall_q:
                                            item["overall_script_query"] = top_overall_q
                                        if top_subj:
                                            item["subject_entity"] = top_subj
                                        if sid in audit_by_sid:
                                            au = audit_by_sid[sid]
                                            app_scope = str(au.get("approved_scope") or item.get("search_scope") or "SCENE_SPECIFIC").upper()
                                            app_query = str(au.get("approved_query") or "").strip()
                                            item["search_scope"] = app_scope if app_scope in ("SCENE_SPECIFIC", "SCRIPT_OVERALL") else "SCENE_SPECIFIC"
                                            item["scope_audit_reason"] = str(au.get("reason") or "")
                                            if app_query:
                                                if item.get("tool_name") == "clone_youtube_short_peak_clip":
                                                    item["youtube_search_query"] = app_query
                                                else:
                                                    item["smart_image_query"] = app_query
                                        item["scope_audited"] = True
                                        audited_list.append(item)

                                    verdict_log = ", ".join(
                                        f"S{x.get('scene_id')}:{x.get('search_scope')}('{x.get('youtube_search_query') or x.get('smart_image_query')}')"
                                        for x in audited_list
                                    )
                                    logger.info(
                                        f"⚖️ [LLM Scope Judge Node] Đã thẩm định xong {len(audited_list)} cảnh "
                                        f"qua `{model_name}` -> {verdict_log}"
                                    )
                                    return audited_list
                    elif resp.status_code in (429, 403, 404, 503):
                        rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:200])
                except Exception as e_req:
                    logger.debug(f"[LLM Scope Judge] {model_name} error: {e_req}")
                    continue
    except Exception as ex:
        logger.debug(f"[LLM Scope Judge] Fallback pass: {ex}")

    return proposed_decisions

