"""
app.graph.tools.media_tools — Bộ công cụ (@tool) thông minh cho LLM Đạo diễn trong LangGraph.

Thiết kế theo triết lý Hybrid Viral Shorts:
1. `clone_youtube_short_peak_clip`:
   - Ưu tiên cho Cảnh 1 (Video Hook) và 2–3 cảnh Cao trào (Peak) xen kẽ trong video.
   - Dùng `yt-dlp` tìm YouTube Shorts hot nhất theo từ khóa, tự động phát hiện đoạn "Most Replayed"
     (heatmap peak) hoặc khúc cao trào, tải & cắt siêu tốc đoạn ngắn chuẩn 9:16 (xóa tiếng gốc).
   - Nếu chủ đề quá trừu tượng hoặc không tìm được Shorts đạt chuẩn -> tự động fallback mượt mà.
2. `search_google_cinematic_image`:
   - Dùng cho các cảnh trừu tượng, nội tâm, giải thích khái niệm, nhân vật lịch sử, xen kẽ giữa các video clip.
   - LLM tự chuyển hóa ý kịch bản thành từ khóa điện ảnh (`smart_image_query`) để lấy ảnh Top Search Google Images sắc nét nhất.
"""
import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain_core.tools import tool

from app.agents.media_agent import MediaAgent, clean_scene_search_query
from app.services.media.web_image_fetcher import WebImageFetcher
from config.settings import settings

logger = logging.getLogger(__name__)
_yt_url_lock = threading.Lock()

# Các từ khóa tiêu đề YouTube cần loại bỏ để tránh cắt nhầm video người ngồi nói chuyện (Podcast / Talking Head)
_TALKING_HEAD_BLOCKLIST = (
    "podcast", "interview", "reaction", "reacts", "q&a", "livestream", "stream highlight",
    "talking", "lecture", "ted talk", "joe rogan", "lex fridman", "review", "unboxing",
    "vlog", "storytime", "asmr", "tutorial", "how to", "course", "lesson",
)


@tool
def clone_youtube_short_peak_clip(
    scene_id: int,
    youtube_search_query: str,
    clip_role: str = "PEAK",
    target_duration_sec: float = 4.5,
    camera_motion: str = "zoom_in",
) -> Dict[str, Any]:
    """
    ƯU TIÊN SỐ 1 CHO CẢNH MỞ ĐẦU (HOOK - scene_id=1) VÀ 2-3 CẢNH CAO TRÀO (PEAK) XEN KẼ TRONG VIDEO.
    Công cụ này tìm kiếm video YouTube Shorts viral trên mạng theo `youtube_search_query`,
    tự động dò tìm đoạn PEAK (khoảnh khắc cao trào / Most Replayed) trong video đó,
    cắt lấy đúng phân đoạn ấn tượng nhất (không tiếng) để ghép vào video của chúng ta.

    Khi nào LLM nên chọn công cụ này:
    - Cảnh 1 (Hook): Cần hình ảnh chuyển động bùng nổ, kịch tính ngay 3 giây đầu để giữ chân người xem.
    - 2 đến 3 cảnh Peak (xen kẽ giữa video, ví dụ cảnh 3, 5, 7): Khi lời thoại mô tả hành động mạnh,
      sự kiện kịch tính, cảnh phim, thiên nhiên kỳ vĩ, vũ trụ, công nghệ, thí nghiệm, chiến đấu.
    - KHÔNG chọn công cụ này nếu cảnh quá trừu tượng/triết học vô hình (dễ ra video người ngồi nói chuyện);
      trường hợp đó hãy dùng `search_google_cinematic_image`.

    Tham số:
    - scene_id: Số thứ tự phân cảnh (1, 2, 3...).
    - youtube_search_query: Câu truy vấn tiếng Anh ngắn gọn (4-7 từ) tối ưu cho YouTube Shorts,
      luôn kèm từ khóa thị giác như 'cinematic scene', '4k edit', 'footage', 'visuals'
      (VD: 'Oppenheimer trinity nuclear explosion 4k edit', 'giant wave hitting ship cinematic footage').
    - clip_role: 'HOOK' (nếu là cảnh 1 mở đầu) hoặc 'PEAK' (nếu là cảnh cao trào giữa video).
    - target_duration_sec: Thời lượng đoạn video cần cắt (mặc định 4.5 giây).
    - camera_motion: Chuyển động phụ trợ ('hyper_zoom', 'zoom_in', 'pan_right', 'zoom_out').
    """
    return {
        "tool_name": "clone_youtube_short_peak_clip",
        "scene_id": scene_id,
        "youtube_search_query": youtube_search_query,
        "clip_role": clip_role,
        "target_duration_sec": target_duration_sec,
        "camera_motion": camera_motion,
    }


@tool
def search_google_cinematic_image(
    scene_id: int,
    smart_image_query: str,
    camera_motion: str = "zoom_in",
) -> Dict[str, Any]:
    """
    CÔNG CỤ TÌM ẢNH TOP SEARCH GOOGLE IMAGES SẮC NÉT CHO CÁC CẢNH TRỪU TƯỢNG, NỘI TÂM, NHÂN VẬT CỤ THỂ HOẶC CẢNH XEN KẼ.
    Dùng công cụ này khi:
    - Phân cảnh mang tính trừu tượng, tâm lý, khái niệm triết học/khoa học cần LLM chủ động chuyển hóa
      thành từ khóa hình ảnh cụ thể để tìm ảnh ghép hợp ngữ cảnh.
    - Phân cảnh cần hình ảnh chân dung nhân vật lịch sử, tài liệu thật, hoặc cảnh phim tĩnh sắc nét.
    - Các cảnh nằm xen kẽ giữa các đoạn video clip để tạo nhịp điệu hài hòa.

    Tham số:
    - scene_id: Số thứ tự phân cảnh (1, 2, 3...).
    - smart_image_query: Câu truy vấn tiếng Anh (4-8 từ) đã được LLM cụ thể hóa từ kịch bản,
      ưu tiên thêm tên phim/nhân vật/bối cảnh thật + 'movie still' hoặc 'historical photo'
      (VD: 'Nikola Tesla sitting alone dark lab lightning movie still').
    - camera_motion: Hiệu ứng chuyển động Ken Burns ('zoom_in', 'pan_right', 'zoom_out', 'pan_left').
    """
    return {
        "tool_name": "search_google_cinematic_image",
        "scene_id": scene_id,
        "smart_image_query": smart_image_query,
        "camera_motion": camera_motion,
    }


MEDIA_DIRECTOR_TOOLS = [clone_youtube_short_peak_clip, search_google_cinematic_image]


# ──────────────────────────────────────────────────────────────────────────────
# BỘ THỰC THI CÔNG CỤ CẮT PEAK YOUTUBE SHORTS & TÌM ẢNH GOOGLE CHẠY SONG SONG
# ──────────────────────────────────────────────────────────────────────────────

def _find_best_peak_window(info: Dict[str, Any], clip_dur: float, role: str = "PEAK") -> Tuple[float, float]:
    """
    Xác định mốc thời gian (start_sec, end_sec) đắt giá nhất trong chiếc YouTube Short:
    1. Nếu YouTube trả về dữ liệu `heatmap` (Most Replayed — đoạn người xem tua lại nhiều nhất),
       lấy ngay đỉnh cao nhất của heatmap!
    2. Nếu là HOOK (Cảnh 1) và không có heatmap: Lấy ngay từ giây 0.6s đầu tiên (khúc Hook của Short gốc).
    3. Nếu là PEAK (Cảnh giữa) và không có heatmap: Lấy khúc cao trào ở khoảng 30% - 45% thời lượng video.
    """
    total_dur = float(info.get("duration") or 15.0)
    clip_dur = max(3.0, min(clip_dur, 8.0))

    if total_dur <= clip_dur + 1.0:
        return 0.0, max(2.0, total_dur)

    # Ưu tiên 1: Dò tìm đỉnh "Most Replayed" từ heatmap của YouTube
    heatmap = info.get("heatmap")
    if isinstance(heatmap, list) and len(heatmap) >= 3:
        best_point = max(heatmap, key=lambda x: float(x.get("value", 0.0)))
        if float(best_point.get("value", 0.0)) > 0.35:
            peak_center = (float(best_point.get("start_time", 0.0)) + float(best_point.get("end_time", 0.0))) / 2.0
            start_s = max(0.5, min(peak_center - clip_dur / 2.0, total_dur - clip_dur - 0.3))
            return round(start_s, 2), round(start_s + clip_dur, 2)

    # Ưu tiên 2: Nếu là cảnh HOOK đầu video -> lấy ngay đoạn mở đầu bùng nổ của Short (bỏ 0.6s đầu tránh fade-in đen)
    if (role or "").upper() == "HOOK":
        start_s = 0.6 if total_dur > (clip_dur + 1.2) else 0.0
        return round(start_s, 2), round(start_s + clip_dur, 2)

    # Ưu tiên 3: Nếu là cảnh PEAK giữa video -> lấy điểm 32% thời lượng (nơi các Shorts thường đặt Climax)
    start_s = max(1.0, min(total_dur * 0.32, total_dur - clip_dur - 0.5))
    return round(start_s, 2), round(start_s + clip_dur, 2)


def execute_youtube_short_peak_clone_sync(
    search_query: str,
    output_path: str,
    target_duration_sec: float = 4.5,
    clip_role: str = "PEAK",
    used_urls: Optional[Set[str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Tìm YouTube Shorts viral theo `search_query`, chọn clip tốt nhất, tải và cắt đúng đoạn Peak chuẩn 9:16.
    Trả về dict thông tin media nếu thành công, hoặc None nếu cần fallback.
    """
    try:
        import yt_dlp
    except ImportError:
        logger.warning("[YouTubePeakTool] Chưa cài đặt yt_dlp, chuyển sang fallback.")
        return None

    used = used_urls if used_urls is not None else set()
    clean_q = re.sub(r"\b(shorts?|#shorts?)\b", "", search_query, flags=re.IGNORECASE).strip()
    yt_query = f"ytsearch6:{clean_q} #shorts"

    search_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
        "socket_timeout": 10,
        "ignoreerrors": True,
    }

    candidates: List[Dict[str, Any]] = []
    try:
        with yt_dlp.YoutubeDL(search_opts) as ydl:
            res = ydl.extract_info(yt_query, download=False)
            entries = (res or {}).get("entries") or []
            for entry in entries:
                if not entry or not isinstance(entry, dict):
                    continue
                vid_id = entry.get("id")
                webpage_url = entry.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else "")
                if not webpage_url or webpage_url in used:
                    continue
                dur = float(entry.get("duration") or 0)
                # Chỉ lấy video ngắn dạng Shorts / Edit (từ 6s đến 75s)
                if dur < 6 or dur > 75:
                    continue
                title_lower = (entry.get("title") or "").lower()
                if any(bad in title_lower for bad in _TALKING_HEAD_BLOCKLIST):
                    continue

                views = int(entry.get("view_count") or 0)
                likes = int(entry.get("like_count") or 0)
                w = int(entry.get("width") or 0)
                h = int(entry.get("height") or 0)
                is_vertical = (h >= w) if (w and h) else True
                has_heatmap = bool(entry.get("heatmap"))

                # Chấm điểm ưu tiên: khung dọc 9:16 + có heatmap Most Replayed + lượt view cao
                score = (views / 10000.0) + (likes / 500.0) + (50.0 if is_vertical else 0.0) + (30.0 if has_heatmap else 0.0)
                candidates.append({
                    "entry": entry,
                    "url": webpage_url,
                    "score": score,
                    "title": entry.get("title") or "",
                })
    except Exception as ex:
        logger.debug(f"[YouTubePeakTool] Lỗi tìm kiếm Shorts '{yt_query}': {ex}")
        return None

    if not candidates:
        return None

    candidates.sort(key=lambda x: x["score"], reverse=True)

    # Thử tải ứng viên tốt nhất (tối đa 2 ứng viên đầu để đảm bảo tốc độ)
    for cand in candidates[:3]:
        entry = cand["entry"]
        vid_url = cand["url"]
        with _yt_url_lock:
            if vid_url in used:
                continue
            used.add(vid_url)
        start_s, end_s = _find_best_peak_window(entry, target_duration_sec + 0.8, role=clip_role)
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
            # Tải trực tiếp phân đoạn Peak bằng download_ranges của yt-dlp để siêu nhanh
            try:
                from yt_dlp.utils import download_range_func
                dl_opts["download_ranges"] = download_range_func(None, [(start_s, end_s + 0.5)])
            except Exception:
                pass

            try:
                with yt_dlp.YoutubeDL(dl_opts) as ydl_dl:
                    ydl_dl.download([vid_url])
            except Exception as dl_err:
                logger.debug(f"[YouTubePeakTool] Tải phân đoạn lỗi ({dl_err}), thử tải nhanh toàn clip ngắn...")
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

            # Nếu tải toàn bộ Short (chưa cắt qua download_ranges), dùng thuật toán quét chuyển động
            # `find_hook_peak_action_offset` để tìm chính xác từng phần chục giây có hành động PEAK nhất!
            actual_start_s = 0.0
            if "download_ranges" not in dl_opts:
                try:
                    from app.utils.ffmpeg_helper import find_hook_peak_action_offset
                    detected_peak = find_hook_peak_action_offset(raw_file, clip_len)
                    if detected_peak > 0.0:
                        actual_start_s = detected_peak
                    else:
                        actual_start_s = start_s
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
                    "crop=1080:1920,"
                    "fps=30,format=yuv420p"
                )
                cmd = [
                    "ffmpeg", "-y", "-v", "error",
                    "-ss", f"{actual_start_s:.2f}",
                    "-i", raw_file,
                    "-t", f"{clip_len:.2f}",
                    "-an",
                    "-vf", vf_filter,
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "21",
                    output_path,
                ]
                subprocess.run(cmd, capture_output=True, timeout=25, check=False)

            if Path(output_path).exists() and Path(output_path).stat().st_size > 20000:
                used.add(vid_url)
                win_start = start_s if "download_ranges" in dl_opts else actual_start_s
                win_end = win_start + clip_len
                logger.info(
                    f"🔥 [YouTubePeakTool] Đã clone & cắt đoạn {clip_role} ({win_start:.1f}s-{win_end:.1f}s) "
                    f"từ Shorts: '{cand['title'][:55]}' ({vid_url})"
                )
                return {
                    "media_path": output_path,
                    "media_type": "hook_ready_video" if (clip_role or "").upper() == "HOOK" else "video",
                    "source_url": vid_url,
                    "title": cand["title"],
                    "window": f"{win_start:.1f}s-{win_end:.1f}s",
                }

    return None


def execute_google_cinematic_image_sync(
    smart_query: str,
    output_path: str,
    narration: str = "",
    topic: str = "",
    used_urls: Optional[Set[str]] = None,
    is_hook_scene: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Thực thi công cụ tìm ảnh Top Search Google Images sắc nét theo `smart_query` của LLM.
    Trả về đường dẫn ảnh đã tải + danh sách alternate_urls (nếu cần ghép multi-cut).
    """
    used = used_urls if used_urls is not None else set()
    media_agent = MediaAgent()
    best_photo = media_agent.search_best_photo(
        query=smart_query,
        used_urls=used,
        visual_analysis=None,
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
    Nếu API lỗi hoặc thiếu cảnh, tự động điền fallback thông minh để không bao giờ gián đoạn.
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
Primary Entity/Subject: "{primary_entity or topic}"
Channel Style: "{channel_niche}"
Total Scenes: {total_scenes}

Scenes Breakdown:
{json.dumps(scenes_brief, ensure_ascii=False, indent=2)}

YOUR MISSION:
Call EXACTLY ONE tool for EACH scene (from scene_id=1 to scene_id={total_scenes}) following this viral pacing formula:
1. SCENE 1 (THE HOOK):
   - Prefer calling `clone_youtube_short_peak_clip` with `clip_role="HOOK"` to grab an explosive, high-retention opening video clip from YouTube Shorts.
   - ONLY IF Scene 1 is extremely abstract/philosophical where YouTube Shorts would only return talking-head podcasts, call `search_google_cinematic_image` with a striking cinematic query instead.
2. BODY SCENES (SCENES 2 TO {total_scenes}):
   - Pick 2 to 3 peak/dynamic action scenes spaced out across the video (e.g., around scene 3, scene 5 or 6) and call `clone_youtube_short_peak_clip` with `clip_role="PEAK"`.
   - For all remaining abstract, narrative, historical, or explanation scenes, call `search_google_cinematic_image` and craft a vivid, concrete `smart_image_query` (translate abstract ideas into concrete cinematic scenes, movie stills, or real historical photographs).

IMPORTANT: Emit tool calls for ALL {total_scenes} scenes in a single response!"""

    decisions_by_scene: Dict[int, Dict[str, Any]] = {}

    try:
        import requests
        from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS

        keys = settings.get_gemini_keys_pool()
        rotator = KeyRotator(keys) if keys else None
        if rotator and rotator.has_keys:
            # Tự động chuyển đổi các @tool của LangGraph sang chuẩn function_declarations của Gemini
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

    # Đảm bảo 100% các cảnh đều có lệnh gọi Tool (điền bổ sung nếu LLM bỏ sót cảnh nào)
    final_plan: List[Dict[str, Any]] = []
    motion_cycle = ["zoom_in", "pan_right", "zoom_out", "pan_left"]
    peak_indices = {1, max(2, total_scenes // 2), max(3, (total_scenes * 3) // 4)}

    for idx, s in enumerate(scenes):
        sid = int(s.get("scene_id") or (idx + 1))
        if sid in decisions_by_scene:
            final_plan.append(decisions_by_scene[sid])
            continue

        kw = s.get("visual_keyword") or s.get("narration", "")[:50]
        clean_kw = clean_scene_search_query(kw, max_words=5) or topic
        dur = float(s.get("estimated_seconds") or 4.5)
        if sid in peak_indices:
            final_plan.append({
                "tool_name": "clone_youtube_short_peak_clip",
                "scene_id": sid,
                "youtube_search_query": f"{primary_entity or ''} {clean_kw} cinematic 4k scene".strip(),
                "clip_role": "HOOK" if sid == 1 else "PEAK",
                "target_duration_sec": dur,
                "camera_motion": "hyper_zoom" if sid == 1 else motion_cycle[idx % len(motion_cycle)],
            })
        else:
            final_plan.append({
                "tool_name": "search_google_cinematic_image",
                "scene_id": sid,
                "smart_image_query": f"{primary_entity or ''} {clean_kw} movie still dramatic".strip(),
                "camera_motion": motion_cycle[idx % len(motion_cycle)],
            })

    return final_plan
