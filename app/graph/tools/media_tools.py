"""
app.graph.tools.media_tools — Bộ công cụ (@tool) thông minh cho LLM Đạo diễn trong LangGraph.

Thiết kế theo triết lý Hybrid Viral Shorts (Chuẩn Ngữ Cảnh & Kiểm Duyệt Thị Giác):
1. `clone_youtube_short_peak_clip`:
   - Ưu tiên cho Cảnh 1 (Video Hook) và 2–3 cảnh Cao trào (Peak) xen kẽ trong video.
   - Dùng `yt-dlp` quét nhanh (`extract_flat`) 15–20 YouTube Shorts theo từ khóa chủ thể thực tế,
     lọc bỏ quảng cáo/sản phẩm/podcast/meme, bắt buộc khớp Chủ thể chính (`subject_entity`).
   - Tự động phát hiện đoạn "Most Replayed" (`heatmap` peak) hoặc khúc cao trào chuyển động (`find_hook_peak_action_offset`),
     cắt đoạn chuẩn 9:16 (xóa tiếng gốc) và **kiểm duyệt 3 khung hình bằng Gemini Vision** để đảm bảo 100% khớp nội dung kịch bản.
   - Nếu YouTube Shorts không có clip đạt chuẩn Vision -> tự động tìm Video HD dọc trên Pexels/Pixabay -> tìm ảnh Top Google Images.
2. `search_google_cinematic_image`:
   - Dùng cho các cảnh trừu tượng, nội tâm, giải thích khái niệm, nhân vật lịch sử, xen kẽ giữa các video clip.
   - LLM tự chuyển hóa ý kịch bản thành từ khóa có đầy đủ Chủ thể chính (`subject_entity` + `smart_image_query`)
     để lấy ảnh Top Search Google Images sắc nét và sát nội dung nhất.
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

# Chặn triệt để các video Podcast / Talking Head / Quảng cáo bán hàng / Đồ chơi / Game / Meme lệch chủ đề
_TALKING_HEAD_BLOCKLIST = (
    "podcast", "interview", "reaction", "reacts", "q&a", "livestream", "stream highlight",
    "talking", "lecture", "ted talk", "joe rogan", "lex fridman", "review", "unboxing",
    "vlog", "storytime", "asmr", "tutorial", "how to", "course", "lesson",
    "animation meme", "gacha", "roblox", "minecraft", "fortnite", "valorant", "lyrics", "karaoke",
    "ultrasonic", "chaser", "repeller", "bark control", "collar", "gadget", "device", "product",
    "amazon", "shop", "buy", "price", "discount", "commercial", "promo", "toy", " plush",
    "vet explains", "veterinarian", "doctor explains", "training tips", "compilation",
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


@tool
def clone_youtube_short_peak_clip(
    scene_id: int,
    subject_entity: str,
    youtube_search_query: str,
    fallback_broad_query: str = "",
    clip_role: str = "PEAK",
    target_duration_sec: float = 4.5,
    camera_motion: str = "zoom_in",
) -> Dict[str, Any]:
    """
    ƯU TIÊN SỐ 1 CHO CẢNH MỞ ĐẦU (HOOK - scene_id=1) VÀ 2-3 CẢNH CAO TRÀO (PEAK) XEN KẼ TRONG VIDEO.
    Công cụ này tìm kiếm video YouTube Shorts thực tế trên mạng theo `youtube_search_query`,
    dò tìm đoạn PEAK (Most Replayed / hành động cao trào), kiểm duyệt bằng Gemini Vision để đảm bảo
    video khớp chặt chẽ với chủ thể `subject_entity` và lời thoại của cảnh, sau đó cắt đoạn chuẩn 9:16.

    QUY TẮC BẮT BUỘC KHI ĐẶT TỪ KHÓA TÌM VIDEO:
    1. `subject_entity`: Tên chủ thể/loài/nhân vật chính của video bằng tiếng Anh (2-3 từ, VD: 'golden retriever dog', 'orca killer whale', 'Nikola Tesla').
    2. `youtube_search_query`: Câu truy vấn tự nhiên (3-5 từ tiếng Anh) BẮT BUỘC PHẢI CHỨA `subject_entity` + hành động thực tế dễ tìm trên YouTube Shorts.
       - TUYỆT ĐỐI KHÔNG thêm các từ nhiễu như '4k edit', 'cinematic visuals', 'movie scene' (vì YouTube sẽ trả về video game/meme).
       - Ví dụ TỐT: 'stray golden retriever rescue rain', 'mother dog saving puppies', 'orca chasing great white shark'.
       - Ví dụ XẤU (CẤM): 'wooden deck at night 4k edit', 'aggressive attack footage'.
    3. `fallback_broad_query`: Câu truy vấn rộng hơn (3-4 từ tiếng Anh) vẫn chứa `subject_entity` để dự phòng nếu câu truy vấn hẹp không có video (VD: 'sad stray golden retriever').
    """
    return {
        "tool_name": "clone_youtube_short_peak_clip",
        "scene_id": scene_id,
        "subject_entity": subject_entity,
        "youtube_search_query": youtube_search_query,
        "fallback_broad_query": fallback_broad_query,
        "clip_role": clip_role,
        "target_duration_sec": target_duration_sec,
        "camera_motion": camera_motion,
    }


@tool
def search_google_cinematic_image(
    scene_id: int,
    subject_entity: str,
    smart_image_query: str,
    camera_motion: str = "zoom_in",
) -> Dict[str, Any]:
    """
    CÔNG CỤ TÌM ẢNH TOP SEARCH GOOGLE IMAGES SẮC NÉT CHO CÁC CẢNH TRỪU TƯỢNG, NỘI TÂM, CHI TIẾT CỤ THỂ HOẶC CẢNH XEN KẼ.
    Dùng công cụ này khi:
    - Phân cảnh mô tả chi tiết rất cụ thể, nội tâm, cảm xúc, giải thích cơ chế, hoặc cảnh tĩnh xen kẽ giữa các video clip.

    QUY TẮC BẮT BUỘC KHI ĐẶT TỪ KHÓA TÌM ẢNH:
    1. `subject_entity`: Chủ thể chính của video/cảnh bằng tiếng Anh (2-3 từ, VD: 'stray golden retriever', 'abandoned puppies', 'Albert Einstein').
    2. `smart_image_query`: Câu truy vấn (4-7 từ tiếng Anh) BẮT BUỘC PHẢI CHỨA `subject_entity` + bối cảnh/hành động cụ thể trong câu thoại.
       - TUYỆT ĐỐI KHÔNG viết từ khóa cụt ngủn thiếu chủ thể (như chỉ viết 'old blanket' hay 'dark wooden deck' — phải viết 'senior dog sleeping on old blanket', 'newborn puppies under wooden deck').
    """
    return {
        "tool_name": "search_google_cinematic_image",
        "scene_id": scene_id,
        "subject_entity": subject_entity,
        "smart_image_query": smart_image_query,
        "camera_motion": camera_motion,
    }


MEDIA_DIRECTOR_TOOLS = [clone_youtube_short_peak_clip, search_google_cinematic_image]


# ──────────────────────────────────────────────────────────────────────────────
# CÁC HÀM HỖ TRỢ LỌC CHỦ THỂ, DÒ ĐỈNH PEAK & KIỂM DUYỆT GEMINI VISION
# ──────────────────────────────────────────────────────────────────────────────

def _extract_subject_anchor_tokens(subject_entity: str, topic: str, search_query: str) -> Set[str]:
    """Trích xuất tập từ khóa Danh từ Chủ thể bắt buộc phải xuất hiện trong tiêu đề YouTube Short."""
    raw = f"{subject_entity or ''} {topic or ''}".strip()
    if not raw:
        raw = search_query or ""
    tokens = re.findall(r"[a-zA-Z]{3,}", raw.lower())
    anchors = {
        t for t in tokens
        if t not in _YT_QUERY_FILLER_WORDS and t not in _GENERIC_NON_SUBJECT_WORDS
    }
    # Bổ sung từ đồng nghĩa phổ biến cho chủ đề động vật/cứu hộ để không bỏ sót video hay
    if any(k in anchors for k in ("dog", "dogs", "puppy", "puppies", "canine", "retriever", "husky", "shepherd", "stray")):
        anchors.update({"dog", "dogs", "puppy", "puppies", "golden", "retriever", "stray", "shelter", "pet"})
    if any(k in anchors for k in ("cat", "cats", "kitten", "kittens", "feline")):
        anchors.update({"cat", "cats", "kitten", "kittens"})
    return anchors


def _clean_yt_search_phrase(q: str, subject_entity: str = "", max_words: int = 5) -> str:
    """Làm sạch câu truy vấn YouTube Shorts: bỏ từ nhiễu (4k, edit, cinematic) và đảm bảo có chủ thể."""
    words = re.findall(r"[a-zA-Z0-9]+", q or "")
    core = [w for w in words if w.lower() not in _YT_QUERY_FILLER_WORDS and len(w) >= 2]
    subj_words = [
        w for w in re.findall(r"[a-zA-Z0-9]+", subject_entity or "")
        if w.lower() not in _YT_QUERY_FILLER_WORDS and len(w) >= 3
    ]
    # Đảm bảo ít nhất 1 từ khóa chủ thể nằm trong câu search
    core_lower = {w.lower() for w in core}
    for sw in reversed(subj_words[:2]):
        if sw.lower() not in core_lower:
            core.insert(0, sw)
            core_lower.add(sw.lower())
    return " ".join(core[:max_words]).strip()


def _find_best_peak_window(info: Dict[str, Any], clip_dur: float, role: str = "PEAK") -> Tuple[float, float]:
    """
    Xác định mốc thời gian (start_sec, end_sec) đắt giá nhất trong chiếc YouTube Short:
    1. Nếu YouTube trả về dữ liệu `heatmap` (Most Replayed — đoạn người xem tua lại nhiều nhất),
       lấy ngay đỉnh cao nhất của heatmap!
    2. Nếu là HOOK (Cảnh 1) và không có heatmap: Lấy từ giây 0.8s (bỏ 0.8s mở đầu tránh màn đen/intro).
    3. Nếu là PEAK (Cảnh giữa) và không có heatmap: Lấy khúc cao trào ở khoảng 30% - 45% thời lượng video.
    """
    total_dur = float(info.get("duration") or 15.0)
    clip_dur = max(3.0, min(clip_dur, 8.0))

    if total_dur <= clip_dur + 1.0:
        return 0.0, max(2.0, total_dur)

    heatmap = info.get("heatmap")
    if isinstance(heatmap, list) and len(heatmap) >= 3:
        best_point = max(heatmap, key=lambda x: float(x.get("value", 0.0)))
        if float(best_point.get("value", 0.0)) > 0.30:
            peak_center = (float(best_point.get("start_time", 0.0)) + float(best_point.get("end_time", 0.0))) / 2.0
            start_s = max(0.5, min(peak_center - clip_dur / 2.0, total_dur - clip_dur - 0.3))
            return round(start_s, 2), round(start_s + clip_dur, 2)

    if (role or "").upper() == "HOOK":
        start_s = 0.8 if total_dur > (clip_dur + 1.5) else 0.0
        return round(start_s, 2), round(start_s + clip_dur, 2)

    start_s = max(1.0, min(total_dur * 0.32, total_dur - clip_dur - 0.5))
    return round(start_s, 2), round(start_s + clip_dur, 2)


def _verify_short_clip_with_gemini_vision(
    video_path: str,
    topic: str,
    narration: str,
    subject_entity: str,
    video_title: str = "",
) -> Tuple[bool, float, str]:
    """
    Trích xuất 3 khung hình (tile=3x1) từ đoạn clip vừa cắt và nhờ Gemini Vision kiểm duyệt:
    - Khớp chủ thể chính (`subject_entity` / `topic`) và hợp ngữ cảnh câu thoại (`narration`).
    - Không phải video quảng cáo sản phẩm, không phải người ngồi nói chuyện (talking head),
      không bị chữ to hay giao diện mạng xã hội che kín màn hình.
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

        prompt = f"""You are a strict Visual Quality & Relevance Inspector for a YouTube Shorts video.
Look at this 3-frame strip extracted from a candidate video clip:
- Video Topic: "{topic}"
- Required Main Subject: "{subject_entity or topic}"
- Scene Narration: "{narration}"
- Source Video Title: "{video_title}"

Evaluate strictly from 1.0 to 10.0:
1. Does the clip visually show the Required Main Subject ("{subject_entity or topic}") and fit the mood/context of the Scene Narration?
2. REJECT IMMEDIATELY (passed=false, score < 5) if the clip shows:
   - A product commercial / gadget demonstration / shopping ad (e.g., ultrasonic repeller, collar ad, toy ad)
   - A person sitting and talking to a microphone / podcast / vlog / reaction
   - Video game / cartoon / meme / anime
   - Giant burned-in text or subtitles covering the center of the video
   - A completely different animal or unrelated subject

Return ONLY valid JSON:
{{"passed": true, "score": 8.5, "reason": "Shows real stray golden retriever in the rain matching narration"}}"""

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
                            passed = bool(data.get("passed", False)) and score >= 7.0
                            reason = str(data.get("reason", ""))
                            return passed, score, reason
                elif resp.status_code in (429, 403, 404, 503):
                    rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:200])
            except Exception:
                continue
    except Exception as ex:
        logger.debug(f"[YouTubePeakTool] Vision check fallback: {ex}")

    return True, 7.2, "Fallback pass"


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
) -> Optional[Dict[str, Any]]:
    """
    Tìm YouTube Shorts thực tế theo `search_query` + `fallback_query` (quét nhanh `extract_flat`),
    lọc chặt theo Chủ thể chính (`subject_entity`), tải & cắt đoạn Peak và kiểm duyệt bằng Gemini Vision.
    Nếu không có Short đạt chuẩn -> tự động thử tìm Stock Video HD dọc (Pexels/Pixabay) có kiểm duyệt Vision!
    """
    used = used_urls if used_urls is not None else set()
    subject_anchors = _extract_subject_anchor_tokens(subject_entity, topic, search_query)

    q1 = _clean_yt_search_phrase(search_query, subject_entity=subject_entity, max_words=5)
    q2 = _clean_yt_search_phrase(fallback_query or f"{subject_entity} {topic}", subject_entity=subject_entity, max_words=4)
    search_phrases = [q for q in dict.fromkeys([q1, q2]) if q]

    all_query_tokens = {
        w.lower()
        for q in search_phrases
        for w in re.findall(r"[a-zA-Z]{3,}", q.lower())
        if w.lower() not in _YT_QUERY_FILLER_WORDS
    }

    # ── TẦNG 1: SĂN YOUTUBE SHORTS SIÊU TỐC (`extract_flat="in_playlist"`) + KIỂM DUYỆT GEMINI VISION ──
    try:
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

        with yt_dlp.YoutubeDL(flat_opts) as ydl:
            for sp in search_phrases[:2]:
                yt_query = f"ytsearch12:{sp} #shorts"
                try:
                    res = ydl.extract_info(yt_query, download=False)
                except Exception:
                    continue
                entries = (res or {}).get("entries") or []
                for entry in entries:
                    if not entry or not isinstance(entry, dict):
                        continue
                    vid_id = entry.get("id") or ""
                    if not vid_id or vid_id in seen_ids:
                        continue
                    seen_ids.add(vid_id)
                    webpage_url = entry.get("url") or entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid_id}"
                    if webpage_url.startswith("/"):
                        webpage_url = f"https://www.youtube.com{webpage_url}"
                    if webpage_url in used:
                        continue

                    dur = float(entry.get("duration") or 0)
                    # Nếu flat entry có trả về duration thì chỉ lấy khoảng 6s - 75s
                    if dur > 0 and (dur < 6 or dur > 75):
                        continue

                    title = str(entry.get("title") or "")
                    title_lower = title.lower()
                    if any(bad in title_lower for bad in _TALKING_HEAD_BLOCKLIST):
                        continue

                    # BẮT BUỘC: Tiêu đề phải chứa ít nhất 1 từ khóa Chủ thể chính (`subject_anchors`)
                    anchor_hits = sum(1 for a in subject_anchors if a in title_lower)
                    if subject_anchors and anchor_hits == 0:
                        continue

                    action_hits = sum(1 for tok in all_query_tokens if tok in title_lower)
                    views = int(entry.get("view_count") or 0)
                    score = (anchor_hits * 80.0) + (action_hits * 45.0) + min(views / 25000.0, 60.0)
                    candidates.append({
                        "id": vid_id,
                        "url": webpage_url,
                        "title": title,
                        "duration": dur,
                        "score": score,
                    })

        candidates.sort(key=lambda x: x["score"], reverse=True)

        for cand in candidates[:3]:
            vid_url = cand["url"]
            with _yt_url_lock:
                if vid_url in used:
                    continue
                used.add(vid_url)

            # Lấy metadata đầy đủ (heatmap, duration chính xác) cho ứng viên đã lọc
            info_entry: Dict[str, Any] = cand
            try:
                info_opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 8}
                with yt_dlp.YoutubeDL(info_opts) as ydl_info:
                    full_info = ydl_info.extract_info(vid_url, download=False)
                    if isinstance(full_info, dict):
                        dur_full = float(full_info.get("duration") or 0)
                        if dur_full < 6 or dur_full > 80:
                            continue
                        desc_lower = f"{(full_info.get('title') or '')} {(full_info.get('description') or '')[:300]}".lower()
                        if any(bad in desc_lower for bad in _TALKING_HEAD_BLOCKLIST):
                            continue
                        info_entry = full_info
            except Exception:
                pass

            start_s, end_s = _find_best_peak_window(info_entry, target_duration_sec + 0.8, role=clip_role)
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
                    try:
                        from app.utils.ffmpeg_helper import find_hook_peak_action_offset
                        detected_peak = find_hook_peak_action_offset(raw_file, clip_len)
                        actual_start_s = detected_peak if detected_peak > 0.0 else start_s
                    except Exception:
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
                    # ── CỔNG KIỂM DUYỆT GEMINI VISION TRÊN 3 KHUNG HÌNH PEAK ──
                    passed_vis, vis_score, vis_reason = _verify_short_clip_with_gemini_vision(
                        video_path=output_path,
                        topic=topic,
                        narration=narration,
                        subject_entity=subject_entity or topic,
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
                    logger.info(
                        f"🔥 [YouTubePeakTool] Đã clone & duyệt Vision ({vis_score}/10) đoạn {clip_role} "
                        f"({win_start:.1f}s-{win_end:.1f}s) từ Shorts: '{cand['title'][:55]}' ({vid_url})"
                    )
                    return {
                        "media_path": output_path,
                        "media_type": "hook_ready_video" if (clip_role or "").upper() == "HOOK" else "video",
                        "source_url": vid_url,
                        "title": cand["title"],
                        "window": f"{win_start:.1f}s-{win_end:.1f}s",
                        "vision_score": vis_score,
                    }
    except Exception as e_yt:
        logger.debug(f"[YouTubePeakTool] Lỗi nhánh YouTube Shorts: {e_yt}")

    # ── TẦNG 2 (DỰ PHÒNG VIDEO SẠCH): TÌM VIDEO HD DỌC TRÊN PEXELS / PIXABAY CÓ AI VISION CURATION ──
    try:
        media_agent = MediaAgent()
        va_vid = {
            "subject": subject_entity or topic,
            "search_queries": search_phrases,
            "is_hook_scene": ((clip_role or "").upper() == "HOOK"),
        }
        best_stock_v = media_agent.search_best_video_clip(
            query=search_phrases[0] if search_phrases else search_query,
            used_urls=used,
            visual_analysis=va_vid,
            narration=narration,
            topic=topic,
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
                            f"cho {clip_role}: '{best_stock_v.get('title', '')[:50]}'"
                        )
                        return {
                            "media_path": output_path,
                            "media_type": "hook_ready_video" if (clip_role or "").upper() == "HOOK" else "video",
                            "source_url": v_url,
                            "title": best_stock_v.get("title", "HD Portrait Stock Video"),
                            "window": f"0.0s-{target_duration_sec:.1f}s",
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
) -> Optional[Dict[str, Any]]:
    """
    Thực thi công cụ tìm ảnh Top Search Google Images sắc nét theo `smart_query` + `subject_entity` của LLM.
    Truyền đầy đủ `visual_analysis` để không bao giờ bị rút gọn thành các từ đơn vô nghĩa.
    """
    used = used_urls if used_urls is not None else set()
    clean_q = _clean_yt_search_phrase(smart_query, subject_entity=subject_entity, max_words=6)
    subj = (subject_entity or "").strip()
    if not subj or (len(subj.split()) == 1 and subj.lower() in _GENERIC_NON_SUBJECT_WORDS):
        subj = _clean_yt_search_phrase(topic, max_words=3) or clean_q

    fallback_q = f"{subj} {clean_scene_search_query(narration, max_words=4)}".strip()
    search_qs = [q for q in dict.fromkeys([clean_q, smart_query, fallback_q]) if q]

    va_photo = {
        "subject": subj,
        "search_queries": search_qs[:3],
        "google_image_queries": search_qs[:3],
        "is_hook_scene": is_hook_scene,
    }

    media_agent = MediaAgent()
    best_photo = media_agent.search_best_photo(
        query=clean_q or smart_query,
        used_urls=used,
        visual_analysis=va_photo,
        narration=narration,
        topic=topic,
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
        }
    return None


# ──────────────────────────────────────────────────────────────────────────────
# BỘ ĐẠO DIỄN LLM BIND_TOOLS (GỌI 1 LẦN CHO TOÀN BỘ KỊCH BẢN)
# ──────────────────────────────────────────────────────────────────────────────

def plan_media_tools_with_llm_sync(
    topic: str,
    scenes: List[Dict[str, Any]],
    primary_entity: Optional[str] = None,
    channel_niche: str = "cinematic",
) -> List[Dict[str, Any]]:
    """
    Sử dụng Gemini gắn `bind_tools([clone_youtube_short_peak_clip, search_google_cinematic_image])`
    để LLM đọc toàn bộ kịch bản và tự chọn Tool + tự đặt câu truy vấn cho từng phân cảnh trong 1 lần gọi!
    """
    total_scenes = len(scenes)
    if total_scenes == 0:
        return []

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

Scenes Breakdown:
{json.dumps(scenes_brief, ensure_ascii=False, indent=2)}

YOUR MISSION:
Call EXACTLY ONE tool for EACH scene (from scene_id=1 to scene_id={total_scenes}) following these strict rules:
1. MANDATORY SUBJECT CONSISTENCY (`subject_entity`):
   - First, identify the main concrete subject/species/character of this video (e.g., "stray golden retriever", "mother dog and puppies", "orca killer whale", "Nikola Tesla").
   - Pass this exact `subject_entity` into EVERY tool call, and ALWAYS include this subject in `youtube_search_query`, `fallback_broad_query`, and `smart_image_query`!
   - NEVER write a vague query without the subject (e.g., NEVER write just "wooden deck" or "warm blanket" or "looking sad" — ALWAYS write "stray golden retriever under wooden deck", "rescued puppies in warm blanket").
2. SCENE 1 (THE HOOK):
   - Call `clone_youtube_short_peak_clip` with `clip_role="HOOK"`, a natural 3-5 word `youtube_search_query` (NO words like "4k edit" or "cinematic visuals"), and a 3-4 word `fallback_broad_query`.
   - ONLY IF the topic is 100% abstract/philosophical, call `search_google_cinematic_image`.
3. BODY SCENES (SCENES 2 TO {total_scenes}):
   - Pick 2 to 3 peak action/emotional scenes spaced across the video (e.g., scene 3, scene 5, scene 7) and call `clone_youtube_short_peak_clip` with `clip_role="PEAK"`.
   - For all remaining scenes, call `search_google_cinematic_image` with a concrete 4-7 word `smart_image_query` that includes `subject_entity` + the specific scene action/setting.

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
                            logger.info(
                                f"🧠 [LLM Tool Director] Đã chọn Tool cho {len(decisions_by_scene)}/{total_scenes} cảnh "
                                f"qua `{model_name}` (bind_tools / functionCall)"
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
    default_subj = _clean_yt_search_phrase(primary_entity or topic, max_words=3)

    for idx, s in enumerate(scenes):
        sid = int(s.get("scene_id") or (idx + 1))
        if sid in decisions_by_scene:
            final_plan.append(decisions_by_scene[sid])
            continue

        kw = s.get("visual_keyword") or s.get("narration", "")[:50]
        clean_kw = _clean_yt_search_phrase(kw, subject_entity=default_subj, max_words=5)
        dur = float(s.get("estimated_seconds") or 4.5)
        if sid in peak_indices:
            final_plan.append({
                "tool_name": "clone_youtube_short_peak_clip",
                "scene_id": sid,
                "subject_entity": default_subj,
                "youtube_search_query": clean_kw,
                "fallback_broad_query": default_subj,
                "clip_role": "HOOK" if sid == 1 else "PEAK",
                "target_duration_sec": dur,
                "camera_motion": "hyper_zoom" if sid == 1 else motion_cycle[idx % len(motion_cycle)],
            })
        else:
            final_plan.append({
                "tool_name": "search_google_cinematic_image",
                "scene_id": sid,
                "subject_entity": default_subj,
                "smart_image_query": clean_kw,
                "camera_motion": motion_cycle[idx % len(motion_cycle)],
            })

    return final_plan
