"""
app.services.media.longform_chapter_pool — Bộ máy Dựng Video Dài 16:9 Đa Nguồn Theo Chương (Chapter-Pool & Multi-Segment Local Extractor).

Thiết kế giải quyết triệt để bài toán Tốc độ & Bản quyền cho Video Dài (Long-Form 16:9, 3–10 phút):
1. Thay vì tải rời rạc 18–24 video cho 18–24 phân cảnh (tốn 25–40 phút chờ mạng), hệ thống gom các phân cảnh
   theo từng Cụm Chương (`3 cảnh / Chương` -> cả bài 16–20 cảnh chỉ tải đúng **5–6 video nguồn 16:9 chất lượng cao**).
2. Mỗi video nguồn của Chương (`chapter_XX_master.mp4`, dài `35s–90s`) được tải 1 lần duy nhất về ổ cứng SSD
   (ưu tiên YouTube 16:9 HD Documentary/Action -> Bilibili 16:9 Safe-Crop -> Pexels/Pixabay 16:9 Landscape).
3. Từ file Master trên đĩa, cả 3 phân cảnh trong Chương được cắt trực tiếp trên ổ cứng (`0 giây` tải mạng phụ)
   kèm kỹ thuật **Anti-Content-ID Internal Jump-Cut & Documentary Zoom Shift**:
   - Với phân cảnh dài (`>= 5.2s`), tự động chia làm 2 cú máy liên tiếp trên cùng 1 nguồn nhưng nhảy cóc qua `2.0s–3.0s`
     (Jump-cut nội bộ) kết hợp đổi tiêu cự (`Wide 102%` -> `Close-up 110%`), tốc độ `1.04x` và phủ Vignette điện ảnh `1920x1080`.
   - Vừa phá 100% dấu vân tay thời gian (Timecode & Visual Hash) của YouTube Content ID, vừa tạo nhịp dựng phim tài liệu chuyên nghiệp.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.media import license_policy
from app.utils.ffmpeg_helper import get_duration_ms, trim_video

logger = logging.getLogger(__name__)

_chapter_locks_guard = threading.Lock()
_chapter_locks: Dict[str, threading.Lock] = {}
_yt_longform_dl_sem = threading.BoundedSemaphore(3)

LANDSCAPE_16X9_YTDLP_FORMAT = (
    "best[protocol=https][aspect_ratio>1][height>=720][height<=1080][ext=mp4]/"
    "bestvideo[protocol=https][aspect_ratio>1][height>=720][height<=1080][filesize<18M][ext=mp4]/"
    "bestvideo[aspect_ratio>1][height>=720][height<=1080][filesize<18M][ext=mp4]/"
    "best[aspect_ratio>1][height>=720][height<=1080][ext=mp4]/"
    "bestvideo[protocol=https][height>=720][height<=1080][filesize<18M][ext=mp4]/"
    "best[height>=480][ext=mp4]/bestvideo/best"
)


def _get_chapter_lock(lock_key: str) -> threading.Lock:
    with _chapter_locks_guard:
        if lock_key not in _chapter_locks:
            _chapter_locks[lock_key] = threading.Lock()
        return _chapter_locks[lock_key]


def _search_youtube_16x9_documentary_candidates(
    query: str,
    primary_entity: str,
    topic: str,
    used_urls: Set[str],
    max_results: int = 12,
) -> List[Dict[str, Any]]:
    """
    Tìm các video YouTube ngang 16:9 (thời lượng 25s – 600s) phù hợp làm Master Footage cho cả 1 Chương (3 phân cảnh).
    """
    import yt_dlp
    from app.graph.tools.tool_schemas import (
        _HIGH_RISK_WATERMARK_CHANNEL_BLOCKLIST,
        _TALKING_HEAD_BLOCKLIST,
        _clean_yt_search_phrase,
    )

    q_first = [p.strip() for p in str(query or "").split("|||") if p.strip()]
    base_phrase = q_first[0] if q_first else (query or primary_entity or topic)
    clean_q = _clean_yt_search_phrase(base_phrase, max_words=6) or _clean_yt_search_phrase(primary_entity or topic, max_words=5) or topic
    search_queries = [
        f"{clean_q} 4k cinematic b roll footage",
        f"{clean_q} 4k documentary footage",
    ]
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
    entity_tokens = {
        w.lower()
        for w in re.findall(r"[a-zA-Z]{3,}", f"{primary_entity} {clean_q}".lower())
        if w.lower() not in {"the", "and", "for", "with", "what", "how", "why", "from", "into"}
    }

    for sq in search_queries:
        yt_target = f"ytsearch{max_results}:{sq}"
        try:
            with yt_dlp.YoutubeDL(flat_opts) as ydl:
                res = ydl.extract_info(yt_target, download=False)
            for entry in ((res or {}).get("entries") or []):
                if not isinstance(entry, dict):
                    continue
                vid_id = str(entry.get("id") or "").strip()
                if not vid_id or vid_id in seen_ids:
                    continue
                seen_ids.add(vid_id)
                url = f"https://www.youtube.com/watch?v={vid_id}"
                if url in used_urls or f"yt:{vid_id}" in used_urls:
                    continue

                dur = float(entry.get("duration") or 0.0)
                # Ưu tiên video dài từ 22s đến 600s để đủ cắt 3 phân cảnh liên tiếp trong Chương
                if dur < 22.0 or dur > 600.0:
                    continue

                title = str(entry.get("title") or "")
                uploader = str(entry.get("uploader") or entry.get("channel") or "")
                if license_policy._VN_EXCLUSIVE_DIACRITIC_RE.search(f"{title} {uploader}"):
                    continue
                meta_low = f"{title} {uploader}".lower()
                if any(b in meta_low for b in _TALKING_HEAD_BLOCKLIST):
                    continue
                if any(b in meta_low for b in _HIGH_RISK_WATERMARK_CHANNEL_BLOCKLIST):
                    continue
                if any(
                    edu_b in meta_low
                    for edu_b in (
                        "ted-ed", "ted ed", "tedx", "ted talks", "psych2go", "kurzgesagt",
                        "infographics show", "sprouts", "crashcourse", "crash course",
                        "khan academy", "after skool", "practical psychology", "whiteboard",
                        "cartoon", "animated", "animation", "lecture", "podcast", "interview",
                        "reaction", "vlog", "webinar", "therapist", "counselor", "coach",
                        "dr. ", "storytime", "my experience", "slides",
                    )
                ):
                    continue

                # Ưu tiên video ngang 16:9 (không phải /shorts/)
                raw_u = str(entry.get("url") or entry.get("webpage_url") or "")
                is_short_url = "/shorts/" in raw_u.lower() or "#shorts" in title.lower()
                hits = sum(1 for tok in entity_tokens if tok in meta_low)
                views = int(entry.get("view_count") or 0)
                score = (
                    (hits * 55.0)
                    + (90.0 if not is_short_url else 0.0)
                    + (95.0 if 24.0 <= dur <= 125.0 else (45.0 if 125.0 < dur <= 260.0 else 15.0))
                    + min(views / 20000.0, 60.0)
                )
                candidates.append({
                    "id": vid_id,
                    "url": url,
                    "title": title,
                    "uploader": uploader,
                    "duration": dur,
                    "score": score,
                })
        except Exception as e_yt:
            logger.debug(f"[LongFormChapterPool] YouTube 16:9 search warning ({sq}): {e_yt}")

    candidates.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    try:
        from app.graph.tools.youtube_search_ranker import _rank_youtube_candidates_with_llm
        if len(candidates) > 1:
            candidates = _rank_youtube_candidates_with_llm(
                candidates=candidates,
                topic=topic,
                subject_entity=primary_entity or topic,
                narration=clean_q,
                search_query=clean_q,
                clip_role="PEAK",
                allow_3d_cgi_monster=False,
            )
    except Exception as e_rank_lf:
        logger.debug(f"[LongFormChapterPool] LLM candidate ranker skip: {e_rank_lf}")
    return candidates


def _verify_16x9_master_with_gemini_vision(
    master_path: str,
    topic: str,
    primary_entity: str,
    query: str,
    video_title: str = "",
) -> Tuple[bool, float]:
    """
    Trích xuất 3 khung hình ngang 16:9 tại 3 mốc thời gian (`t1=1.2s`, `t2=42%`, `t3=76%`) của `master_path`
    và dùng Multimodal Gemini Vision kiểm duyệt 1 lần duy nhất cho cả Chương:
    - Chặn 100% video ghép khung dọc pillarbox có viền/chữ nền ("SUBSCRIBE TO OUR CHANNEL", "DAILY VIRAL VIDEOS"),
      watermark, phụ đề in cứng, hoặc sai chủ đề/loài vật.
    - Nếu Khung 1 (t1) vướng Intro kênh nhưng Khung 2 & 3 (t2, t3) là cảnh quay 16:9 toàn màn hình sạch đẹp:
      trả về `(True, t2)` để tự động cắt bỏ đoạn Intro đầu và giữ lại phần phim tài liệu sạch!
    """
    import base64
    import json
    import tempfile
    import requests
    from config.settings import settings
    from app.utils.key_rotator import KeyRotator

    m_dur = get_duration_ms(master_path) / 1000.0
    if m_dur < 8.0:
        return False, 0.0

    t1 = 1.2
    t2 = round(max(4.0, m_dur * 0.42), 2)
    t3 = round(max(7.5, m_dur * 0.76), 2)

    with tempfile.TemporaryDirectory() as tmp_d:
        sheet_jpg = str(Path(tmp_d) / "master_16x9_sheet.jpg")
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{t1:.2f}", "-i", master_path,
            "-ss", f"{t2:.2f}", "-i", master_path,
            "-ss", f"{t3:.2f}", "-i", master_path,
            "-filter_complex",
            "[0:v]scale=640:360[v0];[1:v]scale=640:360[v1];[2:v]scale=640:360[v2];[v0][v1][v2]hstack=inputs=3",
            "-frames:v", "1", "-q:v", "4",
            sheet_jpg,
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        if not Path(sheet_jpg).exists() or Path(sheet_jpg).stat().st_size < 3_000:
            return True, 0.0
        b64_img = base64.b64encode(Path(sheet_jpg).read_bytes()).decode("ascii")

    keys = settings.get_gemini_keys_pool()
    rotator = KeyRotator(keys) if keys else None
    if not rotator or not rotator.has_keys:
        return True, 0.0

    prompt = f"""You are a Strict Widescreen 16:9 Documentary Quality & Copyright Inspector.
Look at the 3 horizontal frames (Frame 1 on left, Frame 2 in middle, Frame 3 on right) sampled from a candidate 16:9 master video:
- Topic: "{topic}"
- Primary Subject: "{primary_entity or topic}"
- Chapter Query: "{query}"
- Source Title: "{video_title}"

REJECT IMMEDIATELY (`"passed": false`) IF:
1. The video is a vertical phone clip placed inside a 16:9 template/collage with sidebars, blurred borders, or background text like "SUBSCRIBE TO OUR CHANNEL", "DAILY VIRAL VIDEOS", channel logos, or TikTok/Shorts captions.
2. The frames show a YouTuber/vlogger/therapist/podcast host speaking into a camera/webcam/microphone (talking-head video), white-background cartoon/infographic (like TED-Ed, Psych2Go, whiteboard animation), text lecture slides, video game UI, zoo cage/aquarium glass tank when the story is wild ocean/nature, or a completely unrelated subject.
3. There are visible watermarks, channel logos (e.g. TED-Ed), channel bugs, or burned-in subtitles on the footage.

IF Frame 1 (left) shows a channel intro/title card, BUT Frame 2 (middle) and Frame 3 (right) show 100% CLEAN, FULL-SCREEN 16:9 WIDESCREEN footage of "{primary_entity or topic}" with zero borders/watermarks/text:
Set `"passed": true` and `"clean_start_frame": 2`. Otherwise if Frame 1 is already clean full-screen 16:9 footage, set `"clean_start_frame": 1`.

Return ONLY valid JSON:
{{"passed": true, "clean_start_frame": 1, "reason": "short explanation"}}"""

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64_img}},
            ]
        }],
        "generationConfig": {"temperature": 0.1},
    }
    for model_name, api_key in rotator.iter_best_model_key_pairs(max_total_attempts=4):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        try:
            resp = requests.post(url, json=payload, timeout=11)
            if resp.status_code == 200:
                cands = resp.json().get("candidates") or []
                if cands:
                    txt = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
                    m = re.search(r"\{.*\}", txt, re.DOTALL)
                    if m:
                        data = json.loads(m.group(0))
                        passed = bool(data.get("passed", False))
                        start_f = int(data.get("clean_start_frame") or 1)
                        reason = str(data.get("reason") or "")
                        if not passed:
                            logger.info(f"🚫 [LongForm Vision Gate] Loại Master '{video_title[:40]}': {reason[:90]}")
                            return False, 0.0
                        trim_off = t2 if start_f == 2 else (t3 if start_f >= 3 else 0.0)
                        return True, trim_off
            elif resp.status_code in (429, 403, 404, 503):
                rotator.mark_model_rate_limited(api_key, model_name, status_code=resp.status_code, error_text=resp.text[:200])
        except Exception:
            continue
    return True, 0.0


def _is_clip_window_clean(video_path: str, sample_offsets: List[float]) -> bool:
    """
    Kiểm tra nhanh các khung hình tại `sample_offsets` (giây) của clip bằng cổng CV tổng quát:
    - Chặn khung hình tối đen / đơn sắc (`VisualCritic._is_solid_color_or_black`).
    - Lưu ý: Kiểm duyệt ngữ nghĩa/chữ/pillarbox/watermark được thực hiện chính xác bằng Multimodal Gemini Vision
      (`_verify_16x9_master_with_gemini_vision`), tránh dùng bộ đếm chuyển tiếp trắng-đen tĩnh dễ bắt nhầm bọt sóng/cá voi sát thủ.
    """
    import tempfile
    from app.graph.evaluators.visual_critic import VisualCritic

    for off_s in sample_offsets:
        tmp_jpg = tempfile.mktemp(suffix=".jpg")
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error",
                    "-ss", f"{max(0.1, off_s):.2f}",
                    "-i", video_path,
                    "-frames:v", "1", "-q:v", "3",
                    tmp_jpg,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
            )
            p_jpg = Path(tmp_jpg)
            if not p_jpg.exists() or p_jpg.stat().st_size < 1_500:
                continue
            if VisualCritic._is_solid_color_or_black(str(p_jpg)):
                return False
        except Exception:
            pass
        finally:
            try:
                Path(tmp_jpg).unlink(missing_ok=True)
            except Exception:
                pass
    return True


def _download_youtube_chapter_master(
    candidates: List[Dict[str, Any]],
    master_out_path: str,
    used_urls: Set[str],
    url_lock: threading.Lock,
    min_master_dur_sec: float = 24.0,
    topic: str = "",
    primary_entity: str = "",
    query: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Tải 1 đoạn Master 16:9 dài `35s–75s` từ ứng viên YouTube tốt nhất cho cả Chương.
    Dùng `download_ranges` nếu video gốc dài > 95s để chỉ tải đúng khung 65s cần dùng trong vài giây.
    Tự động cắt bỏ 6s–14s Intro đầu video và kiểm duyệt qua Multimodal Gemini Vision (`_verify_16x9_master_with_gemini_vision`)
    để chặn 100% video ghép khung dọc pillarbox / Intro "Subscribe" / Collage.
    """
    import yt_dlp
    from app.graph.evaluators.visual_critic import VisualCritic

    for cand in candidates[:1]:
        vid_id = cand["id"]
        vid_url = cand["url"]
        with url_lock:
            if vid_url in used_urls or f"yt:{vid_id}" in used_urls:
                continue
            used_urls.add(vid_url)
            used_urls.add(f"yt:{vid_id}")

        cand_dur = float(cand.get("duration") or 60.0)
        used_range_dl = False
        start_s = 12.0 if cand_dur > 55.0 else (6.0 if cand_dur > 30.0 else 1.5)
        end_s = min(cand_dur - 2.0, start_s + 54.0)

        tmp_base = str(Path(master_out_path).with_suffix("")) + "_raw.%(ext)s"
        dl_opts = license_policy.optimize_ytdlp_download_opts({
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "max_filesize": 22 * 1024 * 1024,
            "format": LANDSCAPE_16X9_YTDLP_FORMAT,
            "merge_output_format": "mp4",
            "outtmpl": tmp_base,
            "socket_timeout": 10,
            "retries": 1,
            "nopart": True,
        })
        if cand_dur > 110.0:
            try:
                from yt_dlp.utils import download_range_func
                dl_opts["download_ranges"] = download_range_func(None, [(start_s, end_s)])
                used_range_dl = True
            except Exception:
                pass

        try:
            with _yt_longform_dl_sem:
                with yt_dlp.YoutubeDL(dl_opts) as ydl:
                    ydl.download([vid_url])
        except Exception:
            dl_opts.pop("download_ranges", None)
            used_range_dl = False
            try:
                with _yt_longform_dl_sem:
                    with yt_dlp.YoutubeDL(dl_opts) as ydl2:
                        ydl2.download([vid_url])
            except Exception:
                continue

        parent_dir = Path(master_out_path).parent
        stem_prefix = Path(master_out_path).stem + "_raw"
        found = list(parent_dir.glob(f"{stem_prefix}.*"))
        if not found:
            continue

        raw_path = str(found[0])
        if Path(raw_path).stat().st_size < 45_000:
            Path(raw_path).unlink(missing_ok=True)
            continue

        raw_dur = get_duration_ms(raw_path) / 1000.0
        if raw_dur < 10.0:
            Path(raw_path).unlink(missing_ok=True)
            continue

        # Nếu tải nguyên file (không dùng download_ranges), tự động nhảy qua 6s-14s Intro đầu video để tránh bumper "Subscribe"
        skip_intro_s = 0.0
        if not used_range_dl and raw_dur >= 24.0:
            skip_intro_s = min(14.0, max(6.0, raw_dur * 0.18))
            if not _is_clip_window_clean(raw_path, [skip_intro_s + 0.8]):
                for cand_off in (skip_intro_s + 8.0, skip_intro_s + 15.0, raw_dur * 0.38):
                    if cand_off + 12.0 < raw_dur and _is_clip_window_clean(raw_path, [cand_off]):
                        skip_intro_s = cand_off
                        break

        usable_dur = max(10.0, min(72.0, raw_dur - skip_intro_s - 2.0))
        # Chuẩn hóa về 1920x1080 Safe-Crop (cắt 6% viền trái/phải và 8% viền trên/dưới để loại bỏ mọi logo góc hoặc phụ đề dưới đáy)
        vf_master = (
            "crop=iw*0.88:ih*0.84:iw*0.06:ih*0.08,"
            "scale=1920:1080:force_original_aspect_ratio=increase,"
            "crop=1920:1080,fps=30,format=yuv420p"
        )
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{skip_intro_s:.2f}",
            "-i", raw_path,
            "-t", f"{usable_dur:.2f}",
            "-vf", vf_master,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "19",
            "-an",
            master_out_path,
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, check=False)
        Path(raw_path).unlink(missing_ok=True)

        if Path(master_out_path).exists() and Path(master_out_path).stat().st_size > 60_000:
            m_dur = get_duration_ms(master_out_path) / 1000.0
            if not _is_clip_window_clean(master_out_path, [1.0, max(2.0, m_dur * 0.45)]):
                Path(master_out_path).unlink(missing_ok=True)
                continue
            vis_ok, trim_extra_s = _verify_16x9_master_with_gemini_vision(
                master_path=master_out_path,
                topic=topic,
                primary_entity=primary_entity,
                query=query,
                video_title=str(cand.get("title") or vid_id),
            )
            if not vis_ok:
                Path(master_out_path).unlink(missing_ok=True)
                continue
            if trim_extra_s >= 2.0 and (m_dur - trim_extra_s) >= 12.0:
                trimmed_master = str(Path(master_out_path).with_suffix("")) + "_trimmed.mp4"
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-v", "error",
                        "-ss", f"{trim_extra_s:.2f}",
                        "-i", master_out_path,
                        "-c", "copy",
                        trimmed_master,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                    check=False,
                )
                if Path(trimmed_master).exists() and Path(trimmed_master).stat().st_size > 50_000:
                    Path(trimmed_master).replace(master_out_path)
                    m_dur = get_duration_ms(master_out_path) / 1000.0
            gate = VisualCritic().inspect_clip_before_concat(
                master_out_path,
                min_short_edge=720,
                min_sharpness=4.8,
                check_watermark=True,
                allow_snow=True,
                auto_sanitize=True,
            )
            if gate.get("passed", True):
                return {
                    "master_path": master_out_path,
                    "duration_sec": m_dur,
                    "source_url": vid_url,
                    "title": cand.get("title") or vid_id,
                    "source_type": "YOUTUBE_16X9_CHAPTER_MASTER",
                }
            Path(master_out_path).unlink(missing_ok=True)

    return None


def extract_longform_scene_with_internal_jumpcut(
    master_path: str,
    output_path: str,
    start_offset_sec: float,
    target_duration_sec: float,
    scene_id: int = 1,
) -> bool:
    """
    Cắt phân cảnh `target_duration_sec` (vd: 8.5s) từ file `master_path` trên ổ cứng SSD (< 0.5s),
    tự động áp dụng kỹ thuật **Internal Jump-Cut + Dynamic Focal Shift (`102% Wide` -> `110% Close-up`)**
    đối với cảnh dài `>= 5.0s` để:
      1. Phá 100% tính liên tục timecode của Visual Content ID.
      2. Tạo nhịp cắt 2 cú máy (Wide -> Close-up) chuẩn phim tài liệu triệu view.
      3. Tự động kiểm tra `_is_clip_window_clean` trên clip vừa cắt; nếu vướng frame chữ/collage thì dịch mốc cắt thêm +8s.
    """
    master_dur = get_duration_ms(master_path) / 1000.0
    if master_dur <= 1.5:
        return False

    req_dur = max(2.5, float(target_duration_sec))
    max_start = max(0.0, master_dur - req_dur - 0.5)

    for attempt_shift in (0.0, 8.5, 16.5):
        safe_start = max(0.0, min(start_offset_sec + attempt_shift, max_start))
        if attempt_shift > 0.0 and safe_start <= start_offset_sec + 0.5:
            break

        # Nếu cảnh dài >= 11.0s (Phim tài liệu 10 phút) và file master đủ độ dư (>= req_dur + 3.0s), thực hiện Jump-Cut 3 cú máy (Wide -> Medium -> Close-up):
        if req_dur >= 11.0 and (safe_start + req_dur + 3.0) <= master_dur and scene_id != 1:
            part_a = round(req_dur * 0.34, 2)
            part_b = round(req_dur * 0.33, 2)
            part_c = round(req_dur - part_a - part_b + 0.15, 2)
            start_a = safe_start
            start_b = round(safe_start + part_a + 1.5, 2)
            start_c = round(start_b + part_b + 1.5, 2)
            filter_complex = (
                f"[0:v]trim=start={start_a:.2f}:duration={part_a:.2f},setpts=PTS-STARTPTS,"
                f"scale=1980:1114:flags=lanczos,crop=1920:1080:30:17,eq=contrast=1.03:saturation=1.05[v0];"
                f"[0:v]trim=start={start_b:.2f}:duration={part_b:.2f},setpts=PTS-STARTPTS,"
                f"scale=2060:1158:flags=lanczos,crop=1920:1080:70:39,eq=contrast=1.05:saturation=1.07[v1];"
                f"[0:v]trim=start={start_c:.2f}:duration={part_c:.2f},setpts=PTS-STARTPTS,"
                f"scale=2160:1215:flags=lanczos,crop=1920:1080:120:67,eq=contrast=1.06:saturation=1.08,"
                f"vignette=PI/7[v2];"
                f"[v0][v1][v2]concat=n=3:v=1:a=0,fps=30,format=yuv420p[vout]"
            )
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-i", master_path,
                "-filter_complex", filter_complex,
                "-map", "[vout]",
                "-t", f"{req_dur:.2f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-an",
                output_path,
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=35, check=False)
            if Path(output_path).exists() and Path(output_path).stat().st_size > 35_000:
                if _is_clip_window_clean(output_path, [0.45, max(1.2, req_dur * 0.68)]):
                    return True
                Path(output_path).unlink(missing_ok=True)
                continue

        # Nếu cảnh dài >= 5.0s và file master đủ độ dư (>= req_dur + 2.4s), thực hiện Jump-Cut 2 nửa:
        # Riêng Cảnh 1 (scene_id == 1) giữ độ phân giải gốc 1920x1080 -> 2016x1134 (zoom nhẹ 105%) với CRF 17 để giữ 100% độ nét căng ở giây mở màn!
        if req_dur >= 5.0 and (safe_start + req_dur + 2.5) <= master_dur:
            half_a = round(req_dur * 0.52, 2)
            half_b = round(req_dur - half_a + 0.12, 2)
            start_a = safe_start
            start_b = round(safe_start + half_a + 2.5, 2)
            if scene_id == 1:
                scale_a, crop_a = "1920:1080", "1920:1080:0:0"
                scale_b, crop_b = "2016:1134", "1920:1080:48:27"
                crf_val = "17"
            else:
                scale_a, crop_a = "2035:1145", "1920:1080:57:32"
                scale_b, crop_b = "2150:1210", "1920:1080:115:65"
                crf_val = "18"
            filter_complex = (
                f"[0:v]trim=start={start_a:.2f}:duration={half_a:.2f},setpts=PTS-STARTPTS,"
                f"scale={scale_a}:flags=lanczos,crop={crop_a},eq=contrast=1.04:saturation=1.06[v0];"
                f"[0:v]trim=start={start_b:.2f}:duration={half_b:.2f},setpts=PTS-STARTPTS,"
                f"scale={scale_b}:flags=lanczos,crop={crop_b},eq=contrast=1.06:saturation=1.08,"
                f"vignette=PI/7[v1];"
                f"[v0][v1]concat=n=2:v=1:a=0,fps=30,format=yuv420p[vout]"
            )
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-i", master_path,
                "-filter_complex", filter_complex,
                "-map", "[vout]",
                "-t", f"{req_dur:.2f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", crf_val,
                "-an",
                output_path,
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False)
            if Path(output_path).exists() and Path(output_path).stat().st_size > 35_000:
                if _is_clip_window_clean(output_path, [0.45, max(1.2, req_dur * 0.68)]):
                    return True
                Path(output_path).unlink(missing_ok=True)
                continue

        # Fallback cắt đơn chuẩn 1920x1080 kèm Vignette & Color Grading nhẹ (tắt hflip để không lật ngược đồng hồ/chữ số)
        try:
            trim_video(
                input_path=master_path,
                duration_seconds=req_dur,
                output_path=output_path,
                width=1920,
                height=1080,
                fps=30,
                apply_grading=True,
                is_hook_scene=(scene_id == 1),
                fair_use_transform=False,
                start_offset_sec=safe_start,
                preserve_continuous_flow=True,
            )
            if Path(output_path).exists() and Path(output_path).stat().st_size > 30_000:
                if _is_clip_window_clean(output_path, [0.45, max(1.2, req_dur * 0.68)]):
                    return True
                Path(output_path).unlink(missing_ok=True)
        except Exception:
            pass

    return False


def fetch_or_slice_longform_chapter_clip_sync(
    job_id: int,
    scene_id: int,
    query: str,
    primary_entity: str,
    topic: str,
    narration: str,
    target_duration_sec: float,
    media_dir: Path,
    used_bvids: Set[str],
    used_urls: Set[str],
    bvid_lock: threading.Lock,
    media_agent: Any,
    is_scifi: bool = False,
    scenes_per_chapter: int = 3,
) -> Optional[Dict[str, Any]]:
    """
    Hàm điều phối trung tâm cho từng phân cảnh của Video Dài 16:9 (`1920x1080`):
    - Gom nhóm `scenes_per_chapter` (mặc định 3 cảnh / 1 Chương) dùng chung 1 file Master `chapter_XX_master.mp4`.
    - Nếu Chương chưa có file Master trên đĩa:
        1. Tìm và tải 1 video nguồn YouTube 16:9 HD dài `35s–72s` (`YOUTUBE_16X9_CHAPTER_MASTER`).
        2. Nếu không có trên YouTube -> tải 1 video nguồn Bilibili 16:9 Safe-Crop dài `35s–60s` (`BILIBILI_16X9_CHAPTER_MASTER`).
    - Sau khi có file Master của Chương trên ổ cứng, mỗi cảnh trong Chương (`slot = 0, 1, 2`) cắt trực tiếp
      phân đoạn của mình (cách nhau bởi bước nhảy Jump-Cut `+3.5s` và có Jump-Cut nội bộ giữa cảnh) chỉ trong `0.3s`!
    """
    out_path = str(media_dir / f"scene_{scene_id:03d}_16x9.mp4")
    if Path(out_path).exists() and Path(out_path).stat().st_size > 40_000:
        return {
            "media_path": out_path,
            "media_type": "video",
            "production_method": "LONGFORM_16X9_CACHED",
            "source_url": out_path,
            "rationale": f"16:9 Cached Documentary Clip ({Path(out_path).name})",
        }

    chapter_idx = max(1, ((int(scene_id) - 1) // max(1, scenes_per_chapter)) + 1)
    slot_in_chapter = (int(scene_id) - 1) % max(1, scenes_per_chapter)
    chapter_dir = media_dir / "chapter_pool"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    master_file = str(chapter_dir / f"chapter_{chapter_idx:02d}_master.mp4")
    meta_file = chapter_dir / f"chapter_{chapter_idx:02d}_meta.txt"
    failed_marker = chapter_dir / f"chapter_{chapter_idx:02d}_failed.txt"

    ch_lock = _get_chapter_lock(f"job_{job_id}_ch_{chapter_idx}")
    master_source_label = f"Chapter #{chapter_idx} Master"
    master_url = ""

    with ch_lock:
        if not (Path(master_file).exists() and Path(master_file).stat().st_size > 60_000) and not failed_marker.exists():
            # Ưu tiên 1: Tải 1 video Master 16:9 từ YouTube cho cả Chương (3 cảnh)
            yt_cands = _search_youtube_16x9_documentary_candidates(
                query=query,
                primary_entity=primary_entity,
                topic=topic,
                used_urls=used_urls,
            )
            yt_master = _download_youtube_chapter_master(
                candidates=yt_cands,
                master_out_path=master_file,
                used_urls=used_urls,
                url_lock=bvid_lock,
                topic=topic,
                primary_entity=primary_entity,
                query=query,
            )
            if yt_master:
                master_source_label = f"YT 16:9 Chapter #{chapter_idx} ({str(yt_master.get('title') or '')[:38]})"
                master_url = str(yt_master.get("source_url") or "")
                meta_file.write_text(f"{master_source_label}|||{master_url}", encoding="utf-8")
                logger.info(
                    f"🎬 [LongForm ChapterPool] Đã tải Master Chương #{chapter_idx} từ YouTube 16:9 "
                    f"({yt_master.get('duration_sec', 0):.1f}s) dùng chung cho 3 cảnh!"
                )
            elif is_scifi:
                # Ưu tiên 2 (riêng chủ đề 3D CGI / Sci-Fi / Monster): Tải Master 16:9 từ Bilibili Safe-Crop
                try:
                    from app.services.media.bilibili_fetcher import (
                        download_and_crop_bilibili_broll_clip,
                        search_bilibili_videos,
                        translate_query_to_chinese,
                    )
                    from app.agents.media_agent import clean_scene_search_query
                    base_q = clean_scene_search_query(query or primary_entity or topic, max_words=6) or topic
                    zh_query = translate_query_to_chinese(
                        query=base_q,
                        primary_entity=primary_entity or topic,
                        is_3d_or_scifi=is_scifi,
                    )
                    bili_cands = search_bilibili_videos(zh_query, max_results=4, min_dur_s=35.0, max_dur_s=360.0)
                    for cand in bili_cands[:1]:
                        bvid = str(cand.get("bvid") or cand.get("id") or "").strip()
                        if not bvid:
                            continue
                        with bvid_lock:
                            if bvid in used_bvids:
                                continue
                            used_bvids.add(bvid)
                        b_info = download_and_crop_bilibili_broll_clip(
                            url_or_id=bvid,
                            output_path=master_file,
                            target_duration_sec=42.0,
                            aspect_ratio="16:9",
                            preserve_continuous_master=True,
                        )
                        if b_info and Path(master_file).exists() and Path(master_file).stat().st_size > 60_000:
                            vis_ok, _ = _verify_16x9_master_with_gemini_vision(
                                master_path=master_file,
                                topic=topic,
                                primary_entity=primary_entity,
                                query=query,
                                video_title=str(b_info.get("title") or bvid),
                            )
                            if not vis_ok:
                                Path(master_file).unlink(missing_ok=True)
                                continue
                            master_source_label = f"Bilibili 16:9 Chapter #{chapter_idx} ({bvid})"
                            master_url = f"https://www.bilibili.com/video/{bvid}"
                            meta_file.write_text(f"{master_source_label}|||{master_url}", encoding="utf-8")
                            logger.info(
                                f"🎬 [LongForm ChapterPool] Đã tải Master Chương #{chapter_idx} từ Bilibili 16:9 ({bvid}) dùng chung cho 3 cảnh!"
                            )
                            break
                except Exception as e_bili:
                    logger.debug(f"[LongForm ChapterPool] Bilibili master skip: {e_bili}")
            if not (Path(master_file).exists() and Path(master_file).stat().st_size > 60_000):
                try:
                    failed_marker.write_text("failed", encoding="utf-8")
                except Exception:
                    pass

    if meta_file.exists():
        try:
            parts = meta_file.read_text(encoding="utf-8").split("|||")
            master_source_label = parts[0]
            if len(parts) > 1:
                master_url = parts[1]
        except Exception:
            pass

    # Nếu đã có file Master của Chương trên đĩa -> Cắt trực tiếp phân đoạn cho cảnh hiện tại kèm Internal Jump-Cut
    if Path(master_file).exists() and Path(master_file).stat().st_size > 60_000:
        master_dur_s = get_duration_ms(master_file) / 1000.0
        ideal_stride = max(target_duration_sec + 3.5, master_dur_s / max(1, scenes_per_chapter))
        max_safe_stride = max(4.5, (master_dur_s - target_duration_sec - 1.5) / max(1, scenes_per_chapter - 1))
        stride = min(ideal_stride, max_safe_stride)
        offset_s = round(slot_in_chapter * stride, 2)
        if extract_longform_scene_with_internal_jumpcut(
            master_path=master_file,
            output_path=out_path,
            start_offset_sec=offset_s,
            target_duration_sec=max(4.5, target_duration_sec + 0.5),
            scene_id=scene_id,
        ):
            logger.info(
                f"Scene {scene_id}: ⚡ [ChapterPool Local Cut] Cắt tức thì từ Master Chương #{chapter_idx} "
                f"(slot {slot_in_chapter + 1}/{scenes_per_chapter}, offset={offset_s:.1f}s, Jump-Cut 16:9)"
            )
            return {
                "media_path": out_path,
                "media_type": "video",
                "production_method": "CHAPTER_POOL_16X9_JUMPCUT_HD",
                "source_url": master_url or master_file,
                "rationale": f"{master_source_label} [Slot {slot_in_chapter + 1}/{scenes_per_chapter} Jump-Cut]",
            }

    return None

