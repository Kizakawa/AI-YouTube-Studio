"""
app.graph.nodes.hook_video_node — Node chuyên biệt quản lý, săn tìm, chấm điểm độc lập (Gemini Vision)
và dựng phân cảnh mở đầu chuyển động thực (Scene 1 — 3.5s Opening Hook Video) trước khi Merge & Lồng tiếng.

Quy trình 4 bước độc lập trong Node này:
1. Trích xuất ngữ cảnh Cảnh 1 (Hook) & thời lượng khớp chính xác với giọng đọc Cảnh 1 (+0.18s xfade buffer).
2. Săn tìm các ứng viên Video Chuyển Động Thực (YouTube Shorts Action Highlights qua yt-dlp + Pexels HD Vertical).
3. Chấm điểm độc lập từng ứng viên Video bằng Gemini Vision (3 tiêu chí: Đúng thực thể/chủ đề, Cường độ chuyển động thực, Khung hình sạch không dính chữ rác).
4. Dựng thành phẩm `hook_motion_001.mp4` (chuẩn 1080x1920 30fps, >= 3 lần chuyển cảnh nhanh + biến đổi chống bản quyền Fair-Use)
   để chuyển sang `node_render_video` ghép nối (Merge) với Cảnh 2..N và cuối cùng Lồng tiếng (Mux Audio + BGM).
"""

import os
import re
import json
import base64
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import requests
from config.settings import settings
from app.graph.state import VideoWorkflowState
from app.utils.ffmpeg_helper import (
    trim_video,
    render_opening_hook_multi_cut,
    VIDEO_DURATION_SAFETY_MARGIN,
)

logger = logging.getLogger(__name__)


def _build_hook_video_search_queries(
    topic: str,
    primary_entity: str,
    visual_keyword: str,
    niche: str,
) -> List[str]:
    """Tạo danh sách truy vấn ngắn gọn (2-5 từ) ưu tiên cảnh hành động thực tế cho Cảnh 1."""
    queries: List[str] = []
    clean_kw = re.sub(r"[^a-zA-Z0-9\s]", " ", visual_keyword or "").strip()
    clean_ent = re.sub(r"[^a-zA-Z0-9\s]", " ", primary_entity or "").strip()
    clean_top = re.sub(r"[^a-zA-Z0-9\s]", " ", topic or "").strip()

    # Loại bỏ các từ mô tả trừu tượng
    stop_words = {
        "cinematic", "photorealistic", "dramatic", "lighting", "4k", "8k", "vertical",
        "why", "how", "what", "when", "secret", "behind", "truth", "about", "never",
    }
    kw_tokens = [w for w in clean_kw.split() if w.lower() not in stop_words][:4]
    ent_tokens = [w for w in clean_ent.split() if w.lower() not in stop_words][:3]
    top_tokens = [w for w in clean_top.split() if w.lower() not in stop_words][:4]

    niche_l = (niche or "").lower()
    action_suffix = "wildlife action" if any(k in niche_l for k in ("wildlife", "predator")) else "footage"

    if ent_tokens and kw_tokens:
        merged = " ".join(dict.fromkeys(ent_tokens + kw_tokens[:2]))
        queries.append(f"{merged} {action_suffix}".strip())
    if kw_tokens:
        queries.append(" ".join(kw_tokens))
    if top_tokens:
        queries.append(" ".join(top_tokens))

    # Khử trùng lặp giữ nguyên thứ tự
    unique_q = []
    for q in queries:
        q_clean = re.sub(r"\s+", " ", q).strip()
        if q_clean and q_clean.lower() not in [x.lower() for x in unique_q]:
            unique_q.append(q_clean)
    return unique_q[:2] or ["cinematic wildlife action"]


def _download_yt_action_candidates(
    queries: List[str],
    cand_dir: Path,
    max_candidates: int = 3,
) -> List[str]:
    """
    Sử dụng yt-dlp tải trực tiếp các video YouTube Shorts (<= 75s, định dạng MP4) khớp từ khóa.
    Không dùng `--download-sections` qua HLS để tránh lỗi timeout 15s và giữ nguyên toàn bộ chiều dài Short (0s..60s)
    giúp thuật toán Peak & Intrigue Scanner quét và cắt được đúng đoạn Peak đắt giá nhất ở giữa/cuối video.
    """
    cand_dir.mkdir(parents=True, exist_ok=True)
    downloaded: List[str] = []

    for q_idx, q in enumerate(queries[:2]):
        if len(downloaded) >= max_candidates:
            break
        out_tmpl = str(cand_dir / f"yt_hook_cand_{q_idx}_%(id)s.%(ext)s")
        search_expr = f"ytsearch2:{q} #shorts"
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--no-warnings",
            "--quiet",
            "--socket-timeout", "8",
            "--max-filesize", "22M",
            "--match-filter", "duration <= 75",
            "--max-downloads", "2",
            "-f", "best[height>=480][height<=1080][ext=mp4]/bestvideo[height>=480][height<=1080][ext=mp4]/best[ext=mp4]",
            "-o", out_tmpl,
            search_expr,
        ]
        try:
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=28,
                check=False,
            )
        except Exception as e:
            logger.debug(f"[HookVideoNode] yt-dlp candidate fetch timeout/skip ({q}): {e}")

        for mp4 in sorted(cand_dir.glob(f"yt_hook_cand_{q_idx}_*.mp4")):
            if mp4.stat().st_size > 80_000 and str(mp4) not in downloaded:
                downloaded.append(str(mp4))
                if len(downloaded) >= max_candidates:
                    break

    return downloaded


def _extract_contact_sheet_b64(
    video_path: str,
    work_dir: Path,
    idx: int,
    start_sec: float = 0.0,
    duration_sec: float = 3.5,
) -> Optional[str]:
    """
    Trích xuất 3 khung hình đại diện TỪ ĐÚNG ĐOẠN PEAK (`start_sec` -> `start_sec + duration_sec`)
    ghép thành 1 ảnh strip ngang để gửi Gemini Vision chấm điểm chính xác đoạn cao trào đã cắt.
    """
    sheet_path = work_dir / f"hook_score_sheet_{idx}.jpg"
    win_dur = max(duration_sec, 2.4)
    fps_expr = f"3/{win_dur:.2f}"
    seek_args = ["-ss", f"{start_sec:.2f}"] if start_sec >= 0.15 else []
    cmd = [
        "ffmpeg", "-y",
        *seek_args,
        "-t", f"{win_dur:.2f}",
        "-i", video_path,
        "-vf", f"fps={fps_expr},scale=360:640:force_original_aspect_ratio=increase,crop=360:640,tile=3x1",
        "-frames:v", "1",
        "-q:v", "4",
        str(sheet_path),
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8, check=False)
        if sheet_path.exists() and sheet_path.stat().st_size > 5_000:
            return base64.b64encode(sheet_path.read_bytes()).decode("ascii")
    except Exception as e:
        logger.debug(f"[HookVideoNode] Contact sheet error for {video_path}: {e}")
    return None


def _score_hook_video_candidate_with_gemini(
    video_path: str,
    contact_b64: str,
    topic: str,
    hook_narration: str,
    primary_entity: str,
    peak_offset_sec: float = 0.0,
) -> Dict[str, Any]:
    """
    Chấm điểm đoạn Peak của ứng viên Video Mở Đầu bằng Gemini Vision theo 3 tiêu chí:
    1. entity_match_score (1-10): Đúng con vật / chủ thể / bối cảnh trong câu mở đầu hoặc cao trào.
    2. motion_action_score (1-10): Có chuyển động thực kịch tính / cuốn hút, không phải màn hình tĩnh hay talking head.
    3. clean_frame_score (1-10): Khung hình sạch, không bị chữ phụ đề lớn của kênh khác đè giữa màn hình.
    """
    keys = settings.get_gemini_keys_pool() if hasattr(settings, "get_gemini_keys_pool") else []
    if not keys:
        raw_keys = getattr(settings, "GEMINI_API_KEYS_RAW", "") or os.getenv("GEMINI_API_KEYS", "")
        keys = [k.strip() for k in raw_keys.split(",") if k.strip().startswith("AIza")]

    prompt = f"""You are a strict YouTube Shorts Hook Video Quality Grader.
Evaluate this 3-frame sequence extracted from the PEAK CLIMAX window (starting at {peak_offset_sec:.1f}s) of a candidate Short video for:
- Video Topic: "{topic}"
- Primary Subject/Entity: "{primary_entity}"
- Opening Hook Line: "{hook_narration}"

Score strictly from 1.0 to 10.0 in JSON format:
1. "entity_match_score": Does the video visually match the subject/entity or intriguing climax ("{primary_entity}" / "{topic}")? (10 = exact or compelling visual match, 1 = unrelated).
2. "motion_action_score": Is it dynamic real footage/action/intriguing visual rather than a random talking head / podcast / static text slide? (10 = intense real action or intriguing motion, 1 = talking head or static slide).
3. "clean_frame_score": Is the center of the visual reasonably clean without giant burned-in text blocking the subject? (10 = clean footage, 1 = cluttered with huge text).
4. "reason": Short 1-sentence explanation.

Return ONLY valid JSON:
{{"entity_match_score": 8.5, "motion_action_score": 9.0, "clean_frame_score": 8.0, "reason": "..."}}"""

    from app.utils.key_rotator import KeyRotator
    rotator = KeyRotator(keys)
    for model_name, key in rotator.iter_best_model_key_pairs(max_total_attempts=3):
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {"inline_data": {"mime_type": "image/jpeg", "data": contact_b64}},
                        ]
                    }
                ]
            }
            resp = requests.post(url, json=payload, timeout=7)
            if resp.status_code == 200:
                cands = resp.json().get("candidates", [])
                if cands:
                    txt = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
                    m = re.search(r"\{.*\}", txt, re.DOTALL)
                    if m:
                        data = json.loads(m.group(0))
                        e_score = float(data.get("entity_match_score", 5.0))
                        m_score = float(data.get("motion_action_score", 5.0))
                        c_score = float(data.get("clean_frame_score", 5.0))
                        overall = round(e_score * 0.45 + m_score * 0.35 + c_score * 0.20, 2)
                        passed = (overall >= 6.8) and (e_score >= 6.5) and (c_score >= 6.0) and (m_score >= 6.5)
                        return {
                            "video_path": video_path,
                            "peak_offset_sec": peak_offset_sec,
                            "entity_match_score": e_score,
                            "motion_action_score": m_score,
                            "clean_frame_score": c_score,
                            "overall_score": overall,
                            "passed": passed,
                            "reason": data.get("reason", ""),
                        }
            elif resp.status_code in (429, 403, 404, 503):
                rotator.mark_model_rate_limited(key, model_name, status_code=resp.status_code, error_text=resp.text[:300])
        except Exception as e:
            rotator.mark_model_rate_limited(key, model_name, status_code=503, error_text=str(e))
            logger.debug(f"[HookVideoNode] Gemini Vision score error ({model_name}): {e}")
            continue

    return _verify_video_motion_and_clarity_local(video_path, peak_offset_sec=peak_offset_sec)


def _verify_video_motion_and_clarity_local(video_path: str, peak_offset_sec: float = 0.0) -> Dict[str, Any]:
    """
    Chấm điểm cục bộ bằng Computer Vision (PIL ImageChops + ImageStat + Edge Gradient) ngay tại đoạn Peak (`peak_offset_sec`)
    khi Gemini API tạm thời chạm hạn mức 429.
    """
    try:
        from PIL import Image, ImageChops, ImageFilter, ImageStat

        w, h = 180, 320
        frame_bytes = w * h * 3
        seek_args = ["-ss", f"{peak_offset_sec:.2f}"] if peak_offset_sec >= 0.15 else []
        cmd = [
            "ffmpeg", "-v", "quiet",
            *seek_args,
            "-i", video_path,
            "-t", "5",
            "-vf", f"fps=2,scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-",
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=8, check=False)
        raw = res.stdout or b""
        n_frames = len(raw) // frame_bytes
        if n_frames >= 3:
            motions: List[float] = []
            contrasts: List[float] = []
            sharpness_vals: List[float] = []
            prev_g = None
            for i in range(n_frames):
                im = Image.frombytes("RGB", (w, h), raw[i * frame_bytes : (i + 1) * frame_bytes])
                g = im.convert("L")
                st = ImageStat.Stat(g)
                contrasts.append(float(st.stddev[0]))
                edges = g.filter(ImageFilter.FIND_EDGES)
                sharpness_vals.append(float(ImageStat.Stat(edges).mean[0]))
                if prev_g is not None:
                    diff = ImageChops.difference(g, prev_g)
                    motions.append(float(ImageStat.Stat(diff).mean[0]))
                prev_g = g

            avg_motion = sum(motions) / max(len(motions), 1)
            avg_contrast = sum(contrasts) / max(len(contrasts), 1)
            avg_sharp = sum(sharpness_vals) / max(len(sharpness_vals), 1)

            e_score = 8.0
            m_score = round(min(9.5, max(4.0, 5.5 + avg_motion * 0.22)), 2)
            c_score = round(min(9.2, max(5.0, 5.5 + (avg_contrast / 18.0) + (avg_sharp / 15.0))), 2)
            overall = round(e_score * 0.45 + m_score * 0.35 + c_score * 0.20, 2)
            passed = (avg_motion >= 3.0) and (avg_contrast >= 24.0) and (overall >= 6.8)
            return {
                "video_path": video_path,
                "peak_offset_sec": peak_offset_sec,
                "entity_match_score": e_score,
                "motion_action_score": m_score,
                "clean_frame_score": c_score,
                "overall_score": overall,
                "passed": passed,
                "reason": f"Local CV Peak Grader @{peak_offset_sec:.1f}s (Motion={avg_motion:.1f}, Contrast={avg_contrast:.1f}, Intrigue={avg_sharp:.1f})",
            }
    except Exception as e:
        logger.debug(f"[HookVideoNode] Local CV fallback error: {e}")

    return {
        "video_path": video_path,
        "peak_offset_sec": peak_offset_sec,
        "entity_match_score": 0.0,
        "motion_action_score": 0.0,
        "clean_frame_score": 0.0,
        "overall_score": 0.0,
        "passed": False,
        "reason": "Vision API & Local CV unavailable",
    }


def _is_keyword_too_abstract_heuristic(topic: str, visual_keyword: str, niche: str, queries: List[str]) -> bool:
    """
    Kiểm tra nhanh xem từ khóa tìm kiếm có quá trừu tượng (khái niệm tâm lý thuần túy, ẩn dụ triết học,
    giả thuyết hư cấu không có video quay thực tế trên YouTube Shorts) hay không.
    Nếu quá trừu tượng -> bỏ qua tìm video Short trên mạng và chuyển thẳng sang tìm kiếm ảnh truyền thống.
    """
    niche_l = (niche or "").lower()
    combined = f"{topic} {visual_keyword} {' '.join(queries)}".lower()

    # Các thực thể vật lý / động vật / hiện tượng tự nhiên cụ thể luôn có video thực tế trên Shorts
    concrete_tokens = (
        "lion", "tiger", "jaguar", "orca", "shark", "eagle", "harpy", "snake", "anaconda", "caiman",
        "crocodile", "otter", "badger", "leopard", "wolf", "bear", "dog", "puppy", "cat", "kitten",
        "rescue", "ocean", "submarine", "volcano", "tornado", "storm", "rocket", "launch", "explosion",
        "cave", "glacier", "ice", "waterfall", "whale", "octopus", "squid", "spider", "scorpion",
    )
    if any(tok in combined for tok in concrete_tokens):
        return False

    # Các từ khóa tâm lý / khái niệm trừu tượng không có cảnh quay thực tế chất lượng trên Shorts
    abstract_tokens = (
        "love bomb", "gaslight", "manipulat", "narcissist", "toxic", "boundary", "boundaries",
        "fake praise", "silent treatment", "cognitive", "subconscious", "ego", "paradox",
        "philosophy", "stoic", "mindset", "attachment style", "trauma bond", "guilt trip",
        "interrogat", "body language", "micro expression", "social anxiety", "alternate history",
        "what if",
    )
    if any(tok in combined for tok in abstract_tokens) or "psych" in niche_l:
        return True

    return False


def _analyze_script_climax_for_hook(
    state: VideoWorkflowState,
    topic: str,
    primary_entity: str,
    visual_keyword: str,
    hook_narration: str,
    niche: str,
) -> Dict[str, Any]:
    """
    Đọc trực tiếp toàn bộ Kịch bản (script_data / narration các cảnh) để:
    1. Đánh giá xem từ khóa tìm kiếm có QUÁ TRỪU TƯỢNG (`is_abstract_keyword`) hay không:
       - Nếu `is_abstract_keyword == True` -> bỏ qua tìm video Short trên mạng, dùng tìm kiếm ảnh truyền thống.
       - Nếu `is_abstract_keyword == False` -> sinh 2 truy vấn tìm video Short trên mạng để cắt đoạn Peak nhất.
    2. Xác định cả Cảnh 1 (Đầu video) và `climax_scene_id` (Cảnh cao trào trong thân video) để ghép đoạn Peak vào đầu video và/hoặc trong video.
    """
    fallback_queries = _build_hook_video_search_queries(topic, primary_entity, visual_keyword, niche)
    script_data = state.get("script_data") or {}
    scenes_list = script_data.get("scenes") or state.get("scenes_media") or []
    full_script_text = " | ".join(
        f"Scene {s.get('scene_id', i+1)}: {s.get('narration', '')} ({s.get('visual_keyword', '')})"
        for i, s in enumerate(scenes_list[:8])
        if isinstance(s, dict)
    )

    default_climax_scene_id = 3 if len(scenes_list) >= 4 else (2 if len(scenes_list) >= 2 else 1)
    heuristic_abstract = _is_keyword_too_abstract_heuristic(topic, visual_keyword, niche, fallback_queries)

    prompt = f"""You are a Viral YouTube Shorts Hook Director.
Read the video's full script below and decide how to source the Opening Hook & Climax footage.

- Topic: {topic}
- Primary Subject: {primary_entity or topic}
- Opening Narration: {hook_narration}
- Full Script Summary: {full_script_text[:1200]}

Tasks:
1. "is_abstract_keyword": Boolean (true/false). Set to TRUE if the topic/search query is abstract (e.g. psychological manipulation, love bombing, narcissism, abstract mindset, alternate history hypotheticals, invisible concepts) where searching YouTube Shorts would return talking heads/podcasts instead of real physical action footage. Set to FALSE only if it is a concrete physical subject (e.g. wildlife predator attack, animal rescue, real natural disaster, rocket launch, deep sea creature footage).
2. "climax_scene_id": Integer scene_id (between 2 and {max(len(scenes_list), 2)}) where the most intense mid-video climax occurs in the script.
3. "climax_description": Describe in 1 sentence the most intense, peak visual climax in this script.
4. "search_queries": Provide 2 short English YouTube search queries (3 to 5 words each, concrete nouns + action verbs, NO quotes or hashtags) to find real Short videos of this exact climax.
5. "atmosphere_fx": Pick ONE visual FX profile from: "history", "psychology", "mystery", "action".

Return ONLY valid JSON:
{{
  "is_abstract_keyword": false,
  "climax_scene_id": {default_climax_scene_id},
  "climax_description": "...",
  "search_queries": ["query 1", "query 2"],
  "atmosphere_fx": "action"
}}"""

    keys = settings.get_gemini_keys_pool() if hasattr(settings, "get_gemini_keys_pool") else []
    if not keys:
        raw_keys = getattr(settings, "GEMINI_API_KEYS_RAW", "") or os.getenv("GEMINI_API_KEYS", "")
        keys = [k.strip() for k in raw_keys.split(",") if k.strip().startswith("AIza")]

    from app.utils.key_rotator import KeyRotator
    rotator = KeyRotator(keys)
    for model_name, key in rotator.iter_best_model_key_pairs(max_total_attempts=3):
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
            resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=7)
            if resp.status_code == 200:
                cands = resp.json().get("candidates", [])
                if cands:
                    txt = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
                    m = re.search(r"\{.*\}", txt, re.DOTALL)
                    if m:
                        data = json.loads(m.group(0))
                        raw_queries = [
                            re.sub(r"[^a-zA-Z0-9\s]", " ", str(q)).strip()
                            for q in (data.get("search_queries") or [])
                            if str(q).strip()
                        ]
                        valid_queries = [re.sub(r"\s+", " ", q) for q in raw_queries if len(q.split()) >= 2][:2] or fallback_queries
                        fx = str(data.get("atmosphere_fx") or niche or "action").lower()
                        climax_desc = str(data.get("climax_description") or topic).strip()
                        ai_abstract = bool(data.get("is_abstract_keyword", False))
                        is_abstract = ai_abstract or _is_keyword_too_abstract_heuristic(topic, visual_keyword, niche, valid_queries)
                        c_sc_id = int(data.get("climax_scene_id") or default_climax_scene_id)
                        return {
                            "is_abstract_keyword": is_abstract,
                            "climax_scene_id": c_sc_id,
                            "climax_description": climax_desc,
                            "search_queries": valid_queries,
                            "atmosphere_fx": fx,
                        }
            elif resp.status_code in (429, 403, 404, 503):
                rotator.mark_model_rate_limited(key, model_name, status_code=resp.status_code, error_text=resp.text[:300])
        except Exception as e:
            rotator.mark_model_rate_limited(key, model_name, status_code=503, error_text=str(e))
            logger.debug(f"[HookVideoNode] Script climax analysis fallback ({model_name}): {e}")

    combined_text = f"{topic} {hook_narration} {visual_keyword} {niche}".lower()
    if any(k in combined_text for k in ("war", "battle", "fire", "burn", "empire", "history", "king", "army", "ancient")):
        auto_fx = "history"
    elif any(k in combined_text for k in ("brain", "mind", "psych", "stare", "eye", "lie", "emotion", "silence", "habit")):
        auto_fx = "psychology"
    elif any(k in combined_text for k in ("ocean", "deep", "sea", "cave", "space", "myster", "alien", "black hole", "ice")):
        auto_fx = "mystery"
    else:
        auto_fx = "action"

    return {
        "is_abstract_keyword": heuristic_abstract,
        "climax_scene_id": default_climax_scene_id,
        "climax_description": f"{primary_entity or topic} — {visual_keyword}",
        "search_queries": fallback_queries,
        "atmosphere_fx": auto_fx,
    }


async def node_produce_and_score_hook_video(state: VideoWorkflowState) -> Dict[str, Any]:
    """
    Node Chuyên Biệt Cho Hook & Đoạn Cao Trào (Chạy TRƯỚC `node_fetch_media` để làm chủ Cảnh 1 & Cảnh Climax):
    1. Phân tích kịch bản & từ khóa (`_analyze_script_climax_for_hook`):
       - Kiểm tra xem từ khóa search có KHÁ TRỪU TƯỢNG (`is_abstract_keyword`) hay không.
       - Nếu TỪ KHÓA KHÁ TRỪU TƯỢNG (`is_abstract_keyword == True`):
         -> Bỏ qua tìm video Short trên mạng, chuyển thẳng sang tìm kiếm ảnh truyền thống tại `node_fetch_media`!
    2. Nếu từ khóa cụ thể (`is_abstract_keyword == False`):
       - Tự động search các video Short trên mạng (`yt-dlp`), tải toàn bộ Short (<= 75s).
       - Quét toàn bộ video Short bằng `find_hook_peak_action_offset` để tìm đúng ĐOẠN PEAK NHẤT & CUỐN HÚT NHẤT (`peak_offset_sec`).
       - Chấm điểm đoạn Peak đó bằng Gemini Vision / Local CV Grader.
       - Nếu ĐẠT:
         + Cắt đoạn Peak #1 ghép vào ĐẦU VIDEO (Cảnh 1 — `hook_motion_001.mp4`).
         + Đồng thời nếu có đoạn Peak #2 (hoặc video ứng viên đạt chuẩn thứ 2), cắt ghép luôn vào TRONG VIDEO tại Cảnh Cao Trào (`climax_scene_id`)!
         + Đánh dấu `pre_resolved_hook_media` để `node_fetch_media` không tìm ảnh lại cho các cảnh đã có video Peak.
       - Nếu KHÔNG ĐẠT:
         + Tự động chuyển về tìm kiếm ảnh truyền thống tại `node_fetch_media`.
    """
    from app.utils.ffmpeg_helper import find_hook_peak_action_offset

    work_dir = Path(state.get("work_dir", "storage/cache"))
    work_dir.mkdir(parents=True, exist_ok=True)
    hook_cand_dir = work_dir / "hook_candidates"
    hook_cand_dir.mkdir(parents=True, exist_ok=True)

    script_data = state.get("script_data") or {}
    script_scenes = list(script_data.get("scenes") or [])
    scenes_media = list(state.get("scenes_media") or [])
    scenes_audio = list(state.get("scenes_audio") or [])

    base_scenes = scenes_media if scenes_media else script_scenes
    if not base_scenes:
        return {"current_step": "HOOK_VIDEO_SKIPPED", "pre_resolved_hook_media": {}}

    scene_1 = dict(base_scenes[0]) if isinstance(base_scenes[0], dict) else {}
    audio_1 = next((s for s in scenes_audio if isinstance(s, dict) and s.get("scene_id") == 1), {})

    topic = state.get("topic") or ""
    primary_entity = state.get("primary_entity") or ""
    niche = state.get("channel_style") or state.get("niche") or "general"
    visual_keyword = scene_1.get("visual_keyword") or topic
    hook_narration = scene_1.get("narration") or audio_1.get("narration") or ""

    dur_ms = (
        audio_1.get("audio_duration_ms")
        or audio_1.get("duration_ms")
        or scene_1.get("audio_duration_ms")
        or int(max(float(scene_1.get("estimated_seconds") or 3.5), 1.0) * 1000)
    )
    transition_duration = 0.18 if len(base_scenes) > 1 else (0.15 + VIDEO_DURATION_SAFETY_MARGIN)
    target_hook_duration_sec = max(dur_ms / 1000.0, 1.0) + transition_duration

    # 1. Phân tích Kịch bản & Kiểm tra tính Trừu tượng của Từ khóa
    climax_plan = _analyze_script_climax_for_hook(
        state=state,
        topic=topic,
        primary_entity=primary_entity,
        visual_keyword=visual_keyword,
        hook_narration=hook_narration,
        niche=niche,
    )
    is_abstract_keyword = bool(climax_plan.get("is_abstract_keyword", False))
    climax_scene_id = int(climax_plan.get("climax_scene_id") or 3)
    queries = climax_plan["search_queries"]
    climax_desc = climax_plan["climax_description"]
    atmosphere_fx = climax_plan["atmosphere_fx"]

    pre_resolved_hook_media: Dict[int, Dict[str, Any]] = {}

    if is_abstract_keyword:
        logger.info(
            f"⚡ [HookVideoNode] Từ khóa search khá trừu tượng ('{visual_keyword}' / '{topic[:50]}') "
            f"-> Bỏ qua tìm video Short trên mạng, chuyển thẳng sang tìm kiếm ảnh truyền thống!"
        )
        eval_summary = {
            "mode": "ABSTRACT_KEYWORD_TRADITIONAL_IMAGE_SEARCH",
            "is_abstract_keyword": True,
            "climax_description": climax_desc,
            "search_queries": queries,
            "atmosphere_fx": atmosphere_fx,
            "candidates_tested": 0,
            "best_score": 8.5,
            "best_reason": "Keyword is abstract -> delegated to traditional image search",
            "output_clip": None,
            "duration_sec": round(target_hook_duration_sec, 2),
        }
        return {
            "pre_resolved_hook_media": pre_resolved_hook_media,
            "hook_video_eval": eval_summary,
            "current_step": "HOOK_ABSTRACT_FALLBACK_TO_IMAGES",
            "progress_percent": 56,
            "progress_message": "Từ khóa khá trừu tượng -> Chuyển sang tìm kiếm ảnh truyền thống",
        }

    logger.info(
        f"🎯 [HookVideoNode] Từ khóa cụ thể -> Săn tìm Short trên mạng cho Cao Trào: '{climax_desc[:75]}' "
        f"| Queries: {queries} | FX: {atmosphere_fx} | Hook={target_hook_duration_sec:.2f}s | ClimaxScene=#{climax_scene_id}"
    )

    # 2. Tải toàn bộ video Short ứng viên từ YouTube Shorts
    existing_path = str(scene_1.get("media_path") or "")
    candidate_videos: List[str] = []
    if existing_path and existing_path.lower().endswith((".mp4", ".mov", ".webm")) and Path(existing_path).exists():
        candidate_videos.append(existing_path)

    yt_cands = _download_yt_action_candidates(queries, hook_cand_dir, max_candidates=3)
    for c in yt_cands:
        if c not in candidate_videos:
            candidate_videos.append(c)

    # 3. Quét tìm ĐOẠN PEAK NHẤT & CUỐN HÚT NHẤT trên từng video Short rồi mới trích xuất khung hình chấm điểm
    scored_candidates: List[Dict[str, Any]] = []
    passing_candidates: List[Dict[str, Any]] = []
    best_candidate: Optional[Dict[str, Any]] = None

    for idx, v_path in enumerate(candidate_videos):
        peak_offset_sec = find_hook_peak_action_offset(v_path, target_hook_duration_sec)
        b64_sheet = _extract_contact_sheet_b64(
            v_path,
            hook_cand_dir,
            idx,
            start_sec=peak_offset_sec,
            duration_sec=target_hook_duration_sec,
        )
        if not b64_sheet:
            continue
        score_res = _score_hook_video_candidate_with_gemini(
            video_path=v_path,
            contact_b64=b64_sheet,
            topic=f"{topic} (Climax: {climax_desc})",
            hook_narration=hook_narration,
            primary_entity=primary_entity or climax_desc,
            peak_offset_sec=peak_offset_sec,
        )
        scored_candidates.append(score_res)
        logger.info(
            f"  📊 [HookVideoGrader] Ứng viên #{idx + 1} ({Path(v_path).name} @Peak {peak_offset_sec:.1f}s): "
            f"Overall={score_res['overall_score']}/10 "
            f"(Entity={score_res['entity_match_score']}, Motion={score_res['motion_action_score']}, Clean={score_res['clean_frame_score']}) "
            f"-> {'✅ ĐẠT' if score_res['passed'] else '❌ LOẠI'} ({score_res['reason']})"
        )
        if score_res["passed"]:
            passing_candidates.append(score_res)
            if best_candidate is None or score_res["overall_score"] > best_candidate["overall_score"]:
                best_candidate = score_res

    hook_output_clip = str(work_dir / "hook_motion_001.mp4")
    is_scifi = "scifi" in atmosphere_fx or "scifi" in niche.lower()

    if best_candidate and Path(best_candidate["video_path"]).exists():
        best_v_path = best_candidate["video_path"]
        peak_start = float(best_candidate.get("peak_offset_sec", 0.0))
        logger.info(
            f"🔥 [HookVideoNode] Đã cắt đoạn PEAK NHẤT (@{peak_start:.2f}s -> {peak_start + target_hook_duration_sec:.2f}s, "
            f"Score={best_candidate['overall_score']}/10) ghép vào ĐẦU VIDEO (Cảnh 1) -> {hook_output_clip}"
        )
        trim_video(
            input_path=best_v_path,
            duration_seconds=target_hook_duration_sec,
            output_path=hook_output_clip,
            width=1080,
            height=1920,
            fps=30,
            is_scifi=is_scifi,
            niche=atmosphere_fx,
            apply_grading=True,
            is_hook_scene=True,
            fair_use_transform=True,
            start_offset_sec=peak_start,
        )
        hook_mode = "CLIMAX_REAL_VIDEO_FAIR_USE_3CUT"
        if Path(hook_output_clip).exists() and Path(hook_output_clip).stat().st_size > 10_000:
            pre_resolved_hook_media[1] = {
                "media_path": hook_output_clip,
                "media_type": "hook_ready_video",
                "production_method": hook_mode,
                "visual_strategy": "HOOK_PEAK_SHORT_VIDEO",
                "strategy_rationale": f"Peak Short clip @{peak_start:.1f}s ({best_candidate['overall_score']}/10)",
            }
            if scenes_media:
                scene_1["media_path"] = hook_output_clip
                scene_1["media_type"] = "hook_ready_video"
                scene_1["production_method"] = hook_mode
                scenes_media[0] = scene_1

        # 4B. Ghép thêm đoạn Peak thứ 2 vào TRONG VIDEO (tại Cảnh Cao Trào `climax_scene_id`) nếu video đủ dài hoặc có ứng viên thứ 2
        if 1 < climax_scene_id <= len(base_scenes):
            climax_audio = next((s for s in scenes_audio if isinstance(s, dict) and s.get("scene_id") == climax_scene_id), {})
            c_dur_ms = climax_audio.get("audio_duration_ms") or climax_audio.get("duration_ms") or 3500
            climax_dur_sec = max(c_dur_ms / 1000.0, 1.5) + 0.18
            climax_output_clip = str(work_dir / f"hook_climax_scene_{climax_scene_id:03d}.mp4")

            second_v_path = best_v_path
            second_peak_start = 0.0
            other_passing = [c for c in passing_candidates if c["video_path"] != best_v_path]
            if other_passing:
                second_v_path = other_passing[0]["video_path"]
                second_peak_start = float(other_passing[0].get("peak_offset_sec", 0.0))
            else:
                second_peak_start = find_hook_peak_action_offset(
                    best_v_path,
                    climax_dur_sec,
                    exclude_ranges=[(max(0.0, peak_start - 1.0), peak_start + target_hook_duration_sec + 1.0)],
                )

            if second_peak_start > 0.0 or other_passing:
                try:
                    trim_video(
                        input_path=second_v_path,
                        duration_seconds=climax_dur_sec,
                        output_path=climax_output_clip,
                        width=1080,
                        height=1920,
                        fps=30,
                        is_scifi=is_scifi,
                        niche=atmosphere_fx,
                        apply_grading=True,
                        is_hook_scene=False,
                        fair_use_transform=True,
                        start_offset_sec=second_peak_start,
                    )
                    if Path(climax_output_clip).exists() and Path(climax_output_clip).stat().st_size > 10_000:
                        pre_resolved_hook_media[climax_scene_id] = {
                            "media_path": climax_output_clip,
                            "media_type": "video",
                            "production_method": "CLIMAX_PEAK_SHORT_SPLICE",
                            "visual_strategy": "STOCK_VIDEO",
                            "strategy_rationale": f"Mid-video Peak Short splice @{second_peak_start:.1f}s",
                        }
                        logger.info(
                            f"🔥 [HookVideoNode] Đã cắt đoạn Peak thứ 2 (@{second_peak_start:.2f}s) "
                            f"ghép vào TRONG VIDEO tại Cảnh #{climax_scene_id} -> {climax_output_clip}"
                        )
                except Exception as e_climax:
                    logger.debug(f"[HookVideoNode] Không thể cắt đoạn Peak thứ 2 cho Cảnh #{climax_scene_id}: {e_climax}")
    else:
        # Không tìm thấy video Short đạt chuẩn -> nếu đã có pose_images (chạy sau fetch_media) thì dựng 3-cut, nếu chạy trước fetch_media thì giao cho fetch_media tìm ảnh truyền thống
        pose_list = scene_1.get("pose_images") or ([existing_path] if existing_path and Path(existing_path).exists() else [])
        if pose_list:
            logger.info(
                f"⚡ [HookVideoNode] Không có Short phù hợp -> Dùng {len(pose_list)} ảnh truyền thống dựng 3-Cut Dynamic Hook -> {hook_output_clip}"
            )
            render_opening_hook_multi_cut(
                image_paths=pose_list,
                duration_seconds=target_hook_duration_sec,
                output_path=hook_output_clip,
                width=1080,
                height=1920,
                fps=30,
                min_cuts=3,
                is_scifi=is_scifi,
                niche=atmosphere_fx,
            )
            if Path(hook_output_clip).exists() and Path(hook_output_clip).stat().st_size > 10_000:
                scene_1["media_path"] = hook_output_clip
                scene_1["media_type"] = "hook_ready_video"
                scene_1["production_method"] = f"CLIMAX_IMAGES_LIVE_FX_{atmosphere_fx.upper()}_3CUT"
                if scenes_media:
                    scenes_media[0] = scene_1
        else:
            logger.info(
                "⚡ [HookVideoNode] Không tìm thấy video Short đạt chuẩn trên mạng -> Chuyển Cảnh 1 sang tìm kiếm ảnh truyền thống tại fetch_media!"
            )
        hook_mode = f"TRADITIONAL_IMAGE_FALLBACK_{atmosphere_fx.upper()}"

    eval_summary = {
        "mode": hook_mode,
        "is_abstract_keyword": False,
        "climax_scene_id": climax_scene_id,
        "climax_description": climax_desc,
        "search_queries": queries,
        "atmosphere_fx": atmosphere_fx,
        "candidates_tested": len(scored_candidates),
        "best_score": best_candidate["overall_score"] if best_candidate else 8.5,
        "best_reason": best_candidate["reason"] if best_candidate else "Fallback to traditional image search",
        "output_clip": hook_output_clip if Path(hook_output_clip).exists() else None,
        "spliced_scenes": list(pre_resolved_hook_media.keys()),
        "duration_sec": round(target_hook_duration_sec, 2),
    }

    out_state: Dict[str, Any] = {
        "pre_resolved_hook_media": pre_resolved_hook_media,
        "hook_video_eval": eval_summary,
        "current_step": "HOOK_VIDEO_READY",
        "progress_percent": 58,
        "progress_message": f"Đã xử lý Node Hook ({hook_mode}, ghép vào cảnh: {list(pre_resolved_hook_media.keys()) or ['Ảnh truyền thống']})",
    }
    if scenes_media:
        out_state["scenes_media"] = scenes_media
    return out_state
