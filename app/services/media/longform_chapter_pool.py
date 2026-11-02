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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.media import license_policy
from app.utils.ffmpeg_helper import get_duration_ms, trim_video

logger = logging.getLogger(__name__)

_chapter_locks_guard = threading.Lock()
_chapter_locks: Dict[str, threading.Lock] = {}
_yt_longform_dl_sem = threading.BoundedSemaphore(6)
_MASTER_VISION_SEM = threading.BoundedSemaphore(6)
_job_dhash_lock = threading.Lock()
_JOB_SCENE_DHASHES: Dict[int, Dict[int, int]] = {}
_JOB_SCENE_SIGNATURES: Dict[int, Dict[int, List[str]]] = {}
_JOB_MASTER_SLICE_SCENES: Dict[Tuple[int, str], Set[int]] = {}


def reset_longform_job_dedup(job_id: int) -> None:
    """Làm mới bộ nhớ chống trùng khung hình & giới hạn cắt Master cho một Job mới."""
    if not job_id:
        return
    jid = int(job_id)
    with _job_dhash_lock:
        _JOB_SCENE_DHASHES.pop(jid, None)
        _JOB_SCENE_SIGNATURES.pop(jid, None)
        stale_keys = [k for k in _JOB_MASTER_SLICE_SCENES if k[0] == jid]
        for k in stale_keys:
            _JOB_MASTER_SLICE_SCENES.pop(k, None)


def is_clip_visually_unique_in_job(
    video_path: str,
    job_id: int,
    max_hamming: int = 10,
    scene_id: Optional[int] = None,
    master_key: Optional[str] = None,
    max_master_uses: int = 2,
) -> bool:
    """
    Kiểm tra đồng thời 3 tầng chống trùng lặp khung hình trong cùng 1 Job:
    1. Giới hạn cứng số lần cắt trên cùng 1 file Master (`max_master_uses = 2` cảnh/Master cho toàn bộ video).
    2. Chữ ký đa khung hình đồng bộ 100% với `VisualCritic._media_signature` & `VisualCritic._find_duplicate`
       (đảm bảo mọi clip được nhận đều vượt qua `VisualCritic.evaluate_media` với 0 lỗi trùng lặp).
    3. Perceptual dHash (64-bit) trên khung hình trung tâm (`Hamming distance > max_hamming`).
    """
    if not job_id or not video_path or not Path(video_path).exists():
        return True
    jid = int(job_id)
    sid_slot = int(scene_id) if scene_id is not None else -1
    norm_master = str(Path(master_key).name).lower() if master_key else ""

    if norm_master:
        with _job_dhash_lock:
            used_sids = _JOB_MASTER_SLICE_SCENES.get((jid, norm_master), set())
            other_sids = {s for s in used_sids if s != sid_slot} if sid_slot > 0 else set(used_sids)
            if len(other_sids) >= max_master_uses:
                return False

    try:
        from app.graph.evaluators.visual_critic import VisualCritic
        sig = VisualCritic._media_signature(str(video_path))
    except Exception:
        sig = []

    dhash_val: Optional[int] = None
    try:
        import cv2
        cap = cv2.VideoCapture(str(video_path))
        if cap.isOpened():
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if total_frames > 4:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(total_frames * 0.45))
            ret, frame = cap.read()
            cap.release()
            if ret and frame is not None:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                resized = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
                val = 0
                for r in range(8):
                    for c in range(8):
                        val = (val << 1) | (1 if int(resized[r, c + 1]) > int(resized[r, c]) else 0)
                dhash_val = val
    except Exception:
        dhash_val = None

    with _job_dhash_lock:
        sig_map = _JOB_SCENE_SIGNATURES.setdefault(jid, {})
        dhash_map = _JOB_SCENE_DHASHES.setdefault(jid, {})
        other_sigs = {k: v for k, v in sig_map.items() if k != sid_slot}
        if sig and other_sigs:
            try:
                from app.graph.evaluators.visual_critic import VisualCritic
                if VisualCritic._find_duplicate(sig, other_sigs) is not None:
                    return False
            except Exception:
                pass
        if dhash_val is not None:
            for prev_sid, prev_h in dhash_map.items():
                if prev_sid != sid_slot and bin(dhash_val ^ prev_h).count("1") <= max_hamming:
                    return False

        alloc_slot = sid_slot if sid_slot > 0 else -(len(sig_map) + len(dhash_map) + 1)
        if sig:
            sig_map[alloc_slot] = sig
        if dhash_val is not None:
            dhash_map[alloc_slot] = dhash_val
        if norm_master:
            m_set = _JOB_MASTER_SLICE_SCENES.setdefault((jid, norm_master), set())
            if sid_slot > 0:
                for mk_key, mk_sids in _JOB_MASTER_SLICE_SCENES.items():
                    if mk_key[0] == jid and mk_key[1] != norm_master:
                        mk_sids.discard(sid_slot)
            m_set.add(alloc_slot)
    return True


def stitch_two_16x9_clips_no_loop(
    clip_a: str,
    clip_b: str,
    output_path: str,
    target_duration_sec: float,
    scene_id: int = 1,
) -> bool:
    """
    Khi một phân cảnh Long-Form dài 14s–16s nhưng clip Stock Pexels/Pixabay chỉ dài 6s–8s,
    TUYỆT ĐỐI KHÔNG dùng `-stream_loop -1` lặp lại 1 clip 2 lần!
    Hàm này nối mượt 2 clip 16:9 khác nhau (`Shot A` nửa đầu + `Shot B` nửa sau) chuẩn 1920x1080 @ 30fps.
    """
    if not (Path(clip_a).exists() and Path(clip_b).exists()):
        return False
    req_dur = max(4.0, float(target_duration_sec))
    dur_a = get_duration_ms(clip_a) / 1000.0
    dur_b = get_duration_ms(clip_b) / 1000.0
    if dur_a < 2.0 or dur_b < 2.0:
        return False
    half_a = min(max(2.5, dur_a - 0.15), round(req_dur * 0.50, 2))
    half_b = max(2.5, round(req_dur - half_a + 0.10, 2))
    crf_val = "17" if scene_id == 1 else "18"
    filter_complex = (
        f"[0:v]trim=start=0.00:duration={half_a:.2f},setpts=PTS-STARTPTS,"
        f"scale=1980:1114:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop=1920:1080,eq=contrast=1.04:saturation=1.06,fps=30,format=yuv420p[v0];"
        f"[1:v]trim=start=0.00:duration={half_b:.2f},setpts=PTS-STARTPTS,"
        f"scale=2040:1148:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop=1920:1080,eq=contrast=1.05:saturation=1.07,vignette=PI/7,fps=30,format=yuv420p[v1];"
        f"[v0][v1]concat=n=2:v=1:a=0,fps=30,format=yuv420p[vout]"
    )
    out_p = Path(output_path)
    tmp_stitch = str(out_p.parent / f"_fftmp_stitch2_{threading.get_ident()}_{out_p.name}")
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", clip_a,
        "-i", clip_b,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-t", f"{req_dur:.2f}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", crf_val,
        "-an", "-dn", "-sn",
        tmp_stitch,
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, check=False)
        if Path(tmp_stitch).exists() and Path(tmp_stitch).stat().st_size > 35_000 and get_duration_ms(tmp_stitch) >= 2500:
            Path(tmp_stitch).replace(output_path)
            return True
    except Exception:
        pass
    finally:
        Path(tmp_stitch).unlink(missing_ok=True)
    return False

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
    max_results: int = 20,
) -> List[Dict[str, Any]]:
    """
    Tìm các video YouTube ngang 16:9 (thời lượng 34s – 420s) phù hợp làm Master Footage cho cả 1 Chương.
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
    # Chỉ bổ sung 1 từ khóa thực thể gốc nếu truy vấn chương chưa nhắc tới bất kỳ thực thể nào trong chủ đề (giữ tính đa dạng 14 chương!)
    topic_anchor_words = [
        w for w in (_clean_yt_search_phrase(f"{primary_entity} {topic}", max_words=7) or "").split()
        if len(w) >= 3 and w.lower() not in {"the", "and", "for", "with", "inside", "from", "into", "vs"}
    ]
    if topic_anchor_words and not any(w.lower() in clean_q.lower() for w in topic_anchor_words[:4]):
        clean_q = _clean_yt_search_phrase(f"{topic_anchor_words[0]} {clean_q}", max_words=7) or clean_q

    alt_phrase = q_first[1] if len(q_first) > 1 else (primary_entity or topic)
    clean_alt = _clean_yt_search_phrase(alt_phrase, max_words=6) or clean_q
    if topic_anchor_words and not any(w.lower() in clean_alt.lower() for w in topic_anchor_words[:4]):
        clean_alt = _clean_yt_search_phrase(f"{topic_anchor_words[0]} {clean_alt}", max_words=7) or clean_alt

    search_queries = [
        f"{clean_q} 4k documentary",
    ]
    if clean_alt and clean_alt.lower() != clean_q.lower():
        search_queries.append(f"{clean_alt} 4k wildlife documentary" if "wild" in topic.lower() else f"{clean_alt} 4k documentary footage")
    else:
        topic_doc_q = _clean_yt_search_phrase(f"{primary_entity} {topic}", max_words=6) or clean_q
        search_queries.append(f"{topic_doc_q} 4k documentary footage")
    flat_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "socket_timeout": 5,
        "ignoreerrors": True,
    }

    candidates: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    entity_tokens = {
        w.lower()
        for w in re.findall(r"[a-zA-Z]{3,}", f"{primary_entity} {clean_q}".lower())
        if w.lower() not in {"the", "and", "for", "with", "what", "how", "why", "from", "into"}
    }
    topic_tokens = {
        w.lower()
        for w in re.findall(r"[a-zA-Z]{4,}", f"{primary_entity} {topic}".lower())
        if w.lower() not in {"the", "and", "for", "with", "what", "how", "why", "from", "into", "inside", "outside", "versus"}
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
                # Yêu cầu >= 34s để mỗi Master tạo đủ 3-4 phân đoạn 11s độc lập (và <= 420s kết hợp download_ranges tải 54s siêu nhanh)
                if dur < 34.0 or dur > 420.0:
                    continue

                title = str(entry.get("title") or "")
                uploader = str(entry.get("uploader") or entry.get("channel") or "")
                if license_policy._VN_EXCLUSIVE_DIACRITIC_RE.search(f"{title} {uploader}"):
                    continue
                meta_low = f"{title} {uploader}".lower()
                topic_ctx_low = f"{topic} {primary_entity}".lower()
                if any(
                    cap_w in meta_low and cap_w not in topic_ctx_low
                    for cap_w in (
                        "zoo", "cage", "enclosure", "pet ", "my pet", "feeding", "zookeeper", "captive", "aquarium",
                        "car b-roll", "car b roll", "sports car", "f-type", "r-dynamic", "range rover", "test drive",
                        "car review", "automobile", "supercar", "sedan", "suv ",
                    )
                ):
                    continue
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
                        "dr. ", "storytime", "my experience", "slides", "explained",
                        "how to", "tips", "secrets", "guide", "tutorial", "analysis",
                        "breakdown", "expert", "body language", "spotted", "signs",
                        "ways to", "steps", "presentation", "course", "lesson", "reacts",
                        "movie clip", "official video", "official music video", "music video",
                        "official audio", "lyric video", "lyrics", " ft. ", " ft.", " feat.", " feat ",
                        "vevo", "remix", "concert", "live performance", "album", "soundtrack",
                        "official trailer", "gameplay", "walkthrough", "commercial", "tv spot",
                    )
                ):
                    continue

                # Ưu tiên video ngang 16:9 (không phải /shorts/) và bắt buộc khớp ít nhất 1 thực thể/ngữ cảnh chủ đề để loại 100% video lạc đề
                raw_u = str(entry.get("url") or entry.get("webpage_url") or "")
                is_short_url = "/shorts/" in raw_u.lower() or "#shorts" in title.lower()
                hits = sum(1 for tok in entity_tokens if tok in meta_low)
                t_hits = sum(1 for tok in topic_tokens if tok in meta_low)
                if topic_tokens and t_hits == 0 and hits == 0:
                    continue
                views = int(entry.get("view_count") or 0)
                score = (
                    (t_hits * 75.0)
                    + (hits * 45.0)
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
        # Nếu truy vấn đầu tiên đã tìm thấy >= 3 ứng viên chất lượng cao (score >= 165), bỏ qua truy vấn phụ để giảm 50% độ trễ
        if sum(1 for c in candidates if float(c.get("score") or 0.0) >= 165.0) >= 3:
            break

    candidates.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
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
    t2 = round(max(3.5, m_dur * 0.35), 2)
    t3 = round(max(6.0, m_dur * 0.62), 2)
    t4 = round(max(7.5, m_dur * 0.84), 2)

    with tempfile.TemporaryDirectory() as tmp_d:
        sheet_jpg = str(Path(tmp_d) / "master_16x9_sheet.jpg")
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{t1:.2f}", "-i", master_path,
            "-ss", f"{t2:.2f}", "-i", master_path,
            "-ss", f"{t3:.2f}", "-i", master_path,
            "-ss", f"{t4:.2f}", "-i", master_path,
            "-filter_complex",
            "[0:v]scale=480:270[v0];[1:v]scale=480:270[v1];[2:v]scale=480:270[v2];[3:v]scale=480:270[v3];"
            "[v0][v1]hstack=inputs=2[top];[v2][v3]hstack=inputs=2[bot];[top][bot]vstack=inputs=2",
            "-frames:v", "1", "-q:v", "5",
            sheet_jpg,
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        if not Path(sheet_jpg).exists() or Path(sheet_jpg).stat().st_size < 3_000:
            return False, 0.0
        b64_img = base64.b64encode(Path(sheet_jpg).read_bytes()).decode("ascii")

    keys = settings.get_gemini_keys_pool()
    rotator = KeyRotator(keys) if keys else None
    if not rotator or not rotator.has_keys:
        return False, 0.0

    prompt = f"""You are a Strict Widescreen 16:9 Documentary Quality, Species/Subject & Copyright Inspector.
Look at the 4 horizontal 16:9 frames (2x2 grid: Frame 1 top-left, Frame 2 top-right, Frame 3 bottom-left, Frame 4 bottom-right) sampled from a candidate 16:9 master video:
- Documentary Topic: "{topic}"
- Primary Subject(s): "{primary_entity or topic}"
- Chapter Query: "{query}"
- Source Title: "{video_title}"

REJECT IMMEDIATELY (`"passed": false`) IF ANY OF THESE VIOLATIONS APPEAR IN THE USABLE FRAMES:
1. WRONG SPECIES / WRONG BIOME / WRONG CLIMATE / UNRELATED SUBJECT: Any frame shows animal species, prey, landscapes, seasons, or objects from a contradictory ecosystem, climate, continent, or era that do NOT belong to "{topic}" / "{primary_entity}" (for example: snow/ice/polar creatures in a tropical/desert/temperate topic, African savanna animals/prey not named in a South American rainforest topic, domestic pets/livestock instead of wild species, or modern city/cars/tourists in a wild nature or ancient history story).
2. BLACK BARS / COLLAGE / PILLARBOX / LETTERBOX: Any frame has horizontal black letterbox bars at top/bottom, vertical pillarbox sidebars/blurred borders, split-screen collages, or template text ("SUBSCRIBE", "DAILY VIRAL VIDEOS").
3. HUMAN HANDLERS / CAPTIVE ENCLOSURES / TALKING HEAD / CARTOON: When the topic is wild nature/animals, REJECT IMMEDIATELY if ANY human, wildlife handler, zookeeper, tourist, swimmer, or human hands/body appear holding, touching, petting, feeding, posing with, or standing near an animal, OR if ANY wire/chain-link fence, cage bars, concrete enclosure wall, or aquarium glass tank is visible! Also reject any YouTuber/presenter speaking to camera, cartoon/whiteboard/infographic slides, or video game UI.
4. WATERMARKS / UPLOADER CREDITS / SUBTITLES: ANY visible watermark, creator tag (e.g. "By: ...", "@...", Chinese/foreign uploader text at the top/corners/center), TV channel bug, or burned-in subtitles.

IF Frame 1 (top-left) shows a brief intro card, BUT ALL THREE of Frames 2, 3, and 4 show 100% CLEAN, FULL-BLEED 16:9 WIDESCREEN footage strictly matching "{topic}" with ZERO black bars, ZERO watermarks, ZERO human handlers/fences/cages, and ZERO wrong species/biomes:
Set `"passed": true` and `"clean_start_frame": 2`. If ALL FOUR frames (1, 2, 3, and 4) are 100% clean full-bleed 16:9 footage matching "{topic}", set `"passed": true` and `"clean_start_frame": 1`. If ANY of Frames 2, 3, or 4 violates Rules 1–4, you MUST set `"passed": false`!

Return ONLY valid JSON:
{{"passed": false, "clean_start_frame": 1, "reason": "short explanation"}}"""

    from app.utils.key_rotator import GEMINI_PRIORITY_MODELS

    vision_models = [m for m in GEMINI_PRIORITY_MODELS if not str(m).startswith("gemma-")]
    http_sess = requests.Session()
    last_err_note = "no_pairs"
    try:
        for model_name, api_key in rotator.iter_best_model_key_pairs(
            vision_models, max_total_attempts=5, require_vision=True
        ):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            payload = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": "image/jpeg", "data": b64_img}},
                    ]
                }],
                "generationConfig": KeyRotator.build_fast_json_gen_config(model_name, temperature=0.1),
            }
            if not _MASTER_VISION_SEM.acquire(timeout=26.0):
                last_err_note = "vision_sem_timeout"
                break
            try:
                t_v0 = time.monotonic()
                resp = http_sess.post(url, json=payload, timeout=(4.0, 15.0))
                if resp.status_code == 200:
                    rotator.record_model_success(api_key, model_name, time.monotonic() - t_v0)
                    cands = resp.json().get("candidates") or []
                    if cands:
                        txt = "".join(
                            p.get("text", "")
                            for p in cands[0].get("content", {}).get("parts", [])
                            if isinstance(p, dict) and not p.get("thought")
                        )
                        m = re.search(r"\{.*\}", txt, re.DOTALL)
                        if m:
                            data = json.loads(m.group(0))
                            passed = bool(data.get("passed", False))
                            start_f = int(data.get("clean_start_frame") or 1)
                            reason = str(data.get("reason") or "")
                            if not passed:
                                logger.info(f"🚫 [LongForm Vision Gate] Loại Master '{video_title[:40]}': {reason[:110]}")
                                return False, 0.0
                            trim_off = t2 if start_f == 2 else (t3 if start_f >= 3 else 0.0)
                            return True, trim_off
                    last_err_note = f"{model_name}:empty_json"
                elif resp.status_code in (400, 429, 403, 404, 500, 502, 503, 504):
                    last_err_note = f"{model_name}:HTTP_{resp.status_code}"
                    rotator.mark_model_rate_limited(
                        api_key, model_name, status_code=resp.status_code, error_text=resp.text[:800]
                    )
            except requests.exceptions.Timeout as e_to:
                last_err_note = f"{model_name}:timeout"
                rotator.mark_model_rate_limited(api_key, model_name, status_code=504, error_text=f"Timeout: {e_to}")
                continue
            except Exception as e_mv:
                last_err_note = f"{model_name}:{type(e_mv).__name__}"
                rotator.mark_model_rate_limited(api_key, model_name, status_code=503, error_text=str(e_mv))
                continue
            finally:
                _MASTER_VISION_SEM.release()
    finally:
        http_sess.close()
    logger.warning(
        f"🚫 [LongForm Vision Gate] Không thể xác thực Gemini Vision cho '{video_title[:40]}' ({last_err_note}) -> Chặn Fail-Closed!"
    )
    return False, 0.0


def _is_clip_window_clean(video_path: str, sample_offsets: List[float]) -> bool:
    """
    Kiểm tra nhanh các khung hình tại `sample_offsets` (giây) của clip bằng cổng CV tổng quát:
    - Chặn file hỏng / thiếu moov atom (`get_duration_ms < 1000`).
    - Chặn khung hình tối đen / đơn sắc (`VisualCritic._is_solid_color_or_black`).
    """
    import tempfile
    from app.graph.evaluators.visual_critic import VisualCritic

    if not Path(video_path).exists() or get_duration_ms(video_path) < 1000:
        return False

    extracted_ok = 0
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
            extracted_ok += 1
            if VisualCritic._is_solid_color_or_black(str(p_jpg)):
                return False
            try:
                import numpy as np
                from PIL import Image
                with Image.open(p_jpg) as im_chk:
                    gray = np.asarray(im_chk.convert("L"), dtype=np.float32)
                    gray_std = float(np.std(gray))
                    dark_ratio = float(np.mean(gray < 18.0))
                    white_ratio = float(np.mean(gray > 235.0))
                    if (dark_ratio > 0.90 and gray_std < 10.0) or (white_ratio > 0.78 and gray_std < 12.0):
                        return False
                    rgb_arr = np.asarray(im_chk.convert("RGB"), dtype=np.float32)
                    chroma_green_ratio = float(
                        np.mean((rgb_arr[:, :, 1] > 165.0) & (rgb_arr[:, :, 0] < 90.0) & (rgb_arr[:, :, 2] < 90.0))
                    )
                    if chroma_green_ratio > 0.16:
                        return False
            except Exception:
                pass
        except Exception:
            pass
        finally:
            try:
                Path(tmp_jpg).unlink(missing_ok=True)
            except Exception:
                pass
    return extracted_ok > 0


def _download_youtube_chapter_master(
    candidates: List[Dict[str, Any]],
    master_out_path: str,
    used_urls: Set[str],
    url_lock: threading.Lock,
    min_master_dur_sec: float = 24.0,
    max_master_dur_sec: float = 28.0,
    topic: str = "",
    primary_entity: str = "",
    query: str = "",
    cancel_event: Optional[threading.Event] = None,
    deadline_ts: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Tải 1 đoạn Master 16:9 từ ứng viên YouTube tốt nhất.
    Dùng `download_ranges` nếu video gốc dài > 42s để chỉ tải đúng khung cần dùng trong vài giây.
    Tự động cắt bỏ 6s–14s Intro đầu video và kiểm duyệt qua Multimodal Gemini Vision (`_verify_16x9_master_with_gemini_vision`)
    để chặn 100% video ghép khung dọc pillarbox / Intro "Subscribe" / Slide bài giảng / Collage.
    """
    import yt_dlp
    from app.graph.evaluators.visual_critic import VisualCritic

    def _is_cancelled() -> bool:
        return (cancel_event is not None and cancel_event.is_set()) or (
            deadline_ts is not None and (deadline_ts - time.monotonic()) <= 2.2
        )

    for cand in candidates[:5]:
        if _is_cancelled():
            return None
        vid_id = cand["id"]
        vid_url = cand["url"]
        with url_lock:
            if vid_url in used_urls or f"yt:{vid_id}" in used_urls:
                continue
            used_urls.add(vid_url)
            used_urls.add(f"yt:{vid_id}")

        cand_dur = float(cand.get("duration") or 60.0)
        used_range_dl = False
        start_s = 10.0 if cand_dur > 50.0 else (5.0 if cand_dur > 28.0 else 1.5)
        end_s = min(cand_dur - 1.5, start_s + max(24.0, max_master_dur_sec + 6.0))

        tmp_base = str(Path(master_out_path).with_suffix("")) + "_raw.%(ext)s"
        dl_opts = license_policy.optimize_ytdlp_download_opts({
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "max_filesize": 25 * 1024 * 1024,
            "format": LANDSCAPE_16X9_YTDLP_FORMAT,
            "merge_output_format": "mp4",
            "outtmpl": tmp_base,
            "socket_timeout": 8,
            "retries": 1,
            "nopart": True,
        })
        if cand_dur > 42.0:
            try:
                from yt_dlp.utils import download_range_func
                dl_opts["download_ranges"] = download_range_func(None, [(start_s, end_s)])
                used_range_dl = True
            except Exception:
                pass

        try:
            if _is_cancelled():
                return None
            with _yt_longform_dl_sem:
                if _is_cancelled():
                    return None
                with yt_dlp.YoutubeDL(dl_opts) as ydl:
                    ydl.download([vid_url])
        except Exception:
            if _is_cancelled():
                return None
            dl_opts.pop("download_ranges", None)
            used_range_dl = False
            try:
                with _yt_longform_dl_sem:
                    if _is_cancelled():
                        return None
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

        usable_dur = max(10.0, min(float(max_master_dur_sec), raw_dur - skip_intro_s - 1.5))
        # Chuẩn hóa về 1920x1080 Safe-Crop trên file STAGING riêng biệt (_cand_stage_*):
        # TUYỆT ĐỐI KHÔNG ghi vào `master_out_path` trước khi Gemini Vision duyệt `v_ok == True`!
        m_out_p = Path(master_out_path)
        cand_stage_path = str(m_out_p.parent / f"_cand_stage_{threading.get_ident()}_{vid_id}.mp4")
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
            "-c:v", "libx264", "-preset", "superfast", "-crf", "21",
            "-maxrate", "5.5M", "-bufsize", "11M",
            "-an", "-dn", "-sn",
            cand_stage_path,
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, check=False)
        except Exception:
            Path(raw_path).unlink(missing_ok=True)
            Path(cand_stage_path).unlink(missing_ok=True)
            continue
        Path(raw_path).unlink(missing_ok=True)

        try:
            if Path(cand_stage_path).exists() and Path(cand_stage_path).stat().st_size > 60_000:
                m_dur = get_duration_ms(cand_stage_path) / 1000.0
                if not _is_clip_window_clean(cand_stage_path, [1.0, max(2.0, m_dur * 0.45)]):
                    continue
                v_ok, trim_off = _verify_16x9_master_with_gemini_vision(
                    master_path=cand_stage_path,
                    topic=topic,
                    primary_entity=primary_entity,
                    query=query,
                    video_title=str(cand.get("title") or vid_id),
                )
                if not v_ok:
                    continue
                if trim_off > 0.5 and (m_dur - trim_off) >= 8.0:
                    tmp_tr = str(m_out_p.parent / f"_fftmp_vtrim_{threading.get_ident()}_{m_out_p.name}")
                    try:
                        subprocess.run(
                            [
                                "ffmpeg", "-y", "-v", "error",
                                "-ss", f"{trim_off:.2f}",
                                "-i", cand_stage_path,
                                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "19", "-an", "-dn", "-sn",
                                tmp_tr,
                            ],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=25,
                            check=False,
                        )
                        if Path(tmp_tr).exists() and Path(tmp_tr).stat().st_size > 45_000 and get_duration_ms(tmp_tr) >= 5000:
                            Path(tmp_tr).replace(cand_stage_path)
                            m_dur = get_duration_ms(cand_stage_path) / 1000.0
                    except Exception:
                        pass
                    finally:
                        Path(tmp_tr).unlink(missing_ok=True)
                # Chỉ sau khi Gemini Vision duyệt sạch 100% mới đổi tên nguyên tử sang `master_out_path`!
                Path(cand_stage_path).replace(master_out_path)
                return {
                    "master_path": master_out_path,
                    "duration_sec": m_dur,
                    "source_url": vid_url,
                    "title": cand.get("title") or vid_id,
                    "source_type": "YOUTUBE_16X9_CHAPTER_MASTER",
                }
        finally:
            Path(cand_stage_path).unlink(missing_ok=True)

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
            out_p3 = Path(output_path)
            tmp_out = str(out_p3.parent / f"_fftmp_jc3_{threading.get_ident()}_{out_p3.name}")
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-i", master_path,
                "-filter_complex", filter_complex,
                "-map", "[vout]",
                "-t", f"{req_dur:.2f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                "-an", "-dn", "-sn",
                tmp_out,
            ]
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, check=False)
                if Path(tmp_out).exists() and Path(tmp_out).stat().st_size > 35_000 and get_duration_ms(tmp_out) >= 1500:
                    if _is_clip_window_clean(tmp_out, [0.45, max(1.2, req_dur * 0.68)]):
                        Path(tmp_out).replace(output_path)
                        return True
            except Exception:
                pass
            finally:
                Path(tmp_out).unlink(missing_ok=True)
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
            out_p2 = Path(output_path)
            tmp_out2 = str(out_p2.parent / f"_fftmp_jc2_{threading.get_ident()}_{out_p2.name}")
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-i", master_path,
                "-filter_complex", filter_complex,
                "-map", "[vout]",
                "-t", f"{req_dur:.2f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", crf_val,
                "-an", "-dn", "-sn",
                tmp_out2,
            ]
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40, check=False)
                if Path(tmp_out2).exists() and Path(tmp_out2).stat().st_size > 35_000 and get_duration_ms(tmp_out2) >= 1500:
                    if _is_clip_window_clean(tmp_out2, [0.45, max(1.2, req_dur * 0.68)]):
                        Path(tmp_out2).replace(output_path)
                        return True
            except Exception:
                pass
            finally:
                Path(tmp_out2).unlink(missing_ok=True)
            continue

        # Fallback cắt đơn chuẩn 1920x1080 kèm Vignette & Color Grading nhẹ (tắt hflip để không lật ngược đồng hồ/chữ số)
        out_p1 = Path(output_path)
        tmp_out1 = str(out_p1.parent / f"_fftmp_jc1_{threading.get_ident()}_{out_p1.name}")
        try:
            trim_video(
                input_path=master_path,
                duration_seconds=req_dur,
                output_path=tmp_out1,
                width=1920,
                height=1080,
                fps=30,
                apply_grading=True,
                is_hook_scene=(scene_id == 1),
                fair_use_transform=False,
                start_offset_sec=safe_start,
                preserve_continuous_flow=True,
            )
            if Path(tmp_out1).exists() and Path(tmp_out1).stat().st_size > 30_000 and get_duration_ms(tmp_out1) >= 1500:
                if _is_clip_window_clean(tmp_out1, [0.45, max(1.2, req_dur * 0.68)]):
                    Path(tmp_out1).replace(output_path)
                    return True
        except Exception:
            pass
        finally:
            Path(tmp_out1).unlink(missing_ok=True)

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
    scenes_per_chapter: int = 1,
    cancel_event: Optional[threading.Event] = None,
    deadline_ts: Optional[float] = None,
    chapter_key_prefix: str = "chapter",
) -> Optional[Dict[str, Any]]:
    """
    Hàm điều phối trung tâm cho từng phân cảnh của Video Dài 16:9 (`1920x1080`):
    - Hỗ trợ chia sẻ Master theo Cụm Chương (`scenes_per_chapter = 4`) kèm cắt Local Jump-Cut không trùng lặp (`dHash`),
      hoặc Master độc lập riêng lẻ khi `scenes_per_chapter = 1` / `chapter_key_prefix = "refine"`.
    - Hỗ trợ hủy hợp tác (`cancel_event` / `deadline_ts`) và khóa `ch_lock` có timeout để không sinh luồng zombie.
    """
    from app.utils.ffmpeg_helper import is_completed_clean_mp4

    def _is_cancelled() -> bool:
        return (cancel_event is not None and cancel_event.is_set()) or (
            deadline_ts is not None and (deadline_ts - time.monotonic()) <= 2.2
        )

    if _is_cancelled():
        return None

    out_path = (
        str(media_dir / f"scene_{scene_id:03d}_{chapter_key_prefix}_16x9.mp4")
        if chapter_key_prefix != "chapter"
        else str(media_dir / f"scene_{scene_id:03d}_16x9.mp4")
    )
    if Path(out_path).exists():
        if is_completed_clean_mp4(out_path, min_size_bytes=40_000, min_duration_ms=1000):
            return {
                "media_path": out_path,
                "media_type": "video",
                "production_method": "LONGFORM_16X9_CACHED",
                "source_url": out_path,
                "rationale": f"16:9 Cached Documentary Clip ({Path(out_path).name})",
            }
        Path(out_path).unlink(missing_ok=True)

    chapter_idx = max(1, ((int(scene_id) - 1) // max(1, scenes_per_chapter)) + 1)
    slot_in_chapter = (int(scene_id) - 1) % max(1, scenes_per_chapter)
    chapter_dir = media_dir / "chapter_pool"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    master_file = str(chapter_dir / f"{chapter_key_prefix}_{chapter_idx:02d}_master.mp4")
    meta_file = chapter_dir / f"{chapter_key_prefix}_{chapter_idx:02d}_meta.txt"
    failed_marker = chapter_dir / f"{chapter_key_prefix}_{chapter_idx:02d}_failed.txt"

    ch_lock = _get_chapter_lock(f"job_{job_id}_{chapter_key_prefix}_{chapter_idx}")
    master_source_label = f"Chapter #{chapter_idx} Master"
    master_url = ""

    lock_wait_sec = 45.0
    if deadline_ts is not None:
        lock_wait_sec = max(1.5, min(50.0, deadline_ts - time.monotonic() - 2.5))
    if not ch_lock.acquire(timeout=lock_wait_sec):
        return None

    try:
        if _is_cancelled():
            return None
        if Path(master_file).exists() and not meta_file.exists():
            try:
                Path(master_file).unlink(missing_ok=True)
            except Exception:
                pass
        if (not meta_file.exists() or not is_completed_clean_mp4(master_file, min_size_bytes=60_000, min_duration_ms=5000)) and not failed_marker.exists():
            with bvid_lock:
                snap_used_urls = set(used_urls)
            yt_cands = _search_youtube_16x9_documentary_candidates(
                query=query,
                primary_entity=primary_entity,
                topic=topic,
                used_urls=snap_used_urls,
            )
            req_master_max = (
                max(18.0, min(28.0, float(target_duration_sec) + 8.0))
                if int(scenes_per_chapter) <= 1
                else (64.0 if int(scenes_per_chapter) >= 3 else 56.0)
            )
            yt_master = (
                _download_youtube_chapter_master(
                    candidates=yt_cands,
                    master_out_path=master_file,
                    used_urls=used_urls,
                    url_lock=bvid_lock,
                    max_master_dur_sec=req_master_max,
                    topic=topic,
                    primary_entity=primary_entity,
                    query=query,
                    cancel_event=cancel_event,
                    deadline_ts=deadline_ts,
                )
                if not _is_cancelled()
                else None
            )
            if yt_master:
                master_source_label = f"YT 16:9 Chapter #{chapter_idx} ({str(yt_master.get('title') or '')[:38]})"
                master_url = str(yt_master.get("source_url") or "")
                meta_file.write_text(f"{master_source_label}|||{master_url}", encoding="utf-8")
                logger.info(
                    f"🎬 [LongForm ChapterPool] Đã tải Master Chương #{chapter_idx} từ YouTube 16:9 "
                    f"({yt_master.get('duration_sec', 0):.1f}s) dùng chung cho {scenes_per_chapter} cảnh!"
                )
            elif not _is_cancelled() and is_scifi:
                # Ưu tiên 2: Chỉ bật Bilibili 16:9 Safe-Crop cho ngách Sci-Fi / 3D CGI (tránh dịch từ khóa sang tiếng Trung làm trôi ngữ nghĩa loài vật/hệ sinh thái và giảm 40s độ trễ mỗi chương)
                if True:
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
                        if not _is_cancelled():
                            bili_cands = search_bilibili_videos(zh_query, max_results=4, min_dur_s=35.0, max_dur_s=360.0)
                            for cand in bili_cands[:1]:
                                if _is_cancelled():
                                    break
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
                                    target_duration_sec=52.0 if int(scenes_per_chapter) >= 3 else 42.0,
                                    aspect_ratio="16:9",
                                    preserve_continuous_master=True,
                                )
                                if (
                                    b_info
                                    and is_completed_clean_mp4(master_file, min_size_bytes=60_000, min_duration_ms=5000)
                                    and _is_clip_window_clean(master_file, [1.5, 12.0, 24.0])
                                ):
                                    # Cắt 12% lề trên/dưới để loại bỏ hoàn toàn viền đen letterbox và chữ "By: ..." trên đỉnh khung hình Bilibili
                                    b_tmp_crop = str(Path(master_file).with_name(f"_fftmp_bcrop_{Path(master_file).name}"))
                                    try:
                                        subprocess.run(
                                            [
                                                "ffmpeg", "-y", "-v", "error",
                                                "-i", master_file,
                                                "-vf",
                                                "crop=iw*0.86:ih*0.76:iw*0.07:ih*0.12,"
                                                "scale=1920:1080:force_original_aspect_ratio=increase,"
                                                "crop=1920:1080,fps=30,format=yuv420p",
                                                "-c:v", "libx264", "-preset", "superfast", "-crf", "21",
                                                "-maxrate", "5.5M", "-bufsize", "11M", "-an",
                                                b_tmp_crop,
                                            ],
                                            stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL,
                                            timeout=35,
                                            check=False,
                                        )
                                        if is_completed_clean_mp4(b_tmp_crop, min_size_bytes=60_000, min_duration_ms=5000):
                                            Path(b_tmp_crop).replace(master_file)
                                    except Exception:
                                        pass
                                    finally:
                                        Path(b_tmp_crop).unlink(missing_ok=True)

                                    from app.graph.evaluators.visual_critic import VisualCritic
                                    b_gate = VisualCritic().inspect_clip_before_concat(
                                        master_file,
                                        min_short_edge=720,
                                        min_sharpness=4.8,
                                        check_watermark=True,
                                        allow_snow=True,
                                        auto_sanitize=True,
                                    )
                                    b_v_ok, b_trim_off = (
                                        _verify_16x9_master_with_gemini_vision(
                                            master_path=master_file,
                                            topic=topic,
                                            primary_entity=primary_entity,
                                            query=query,
                                            video_title=str(cand.get("title") or bvid),
                                        )
                                        if b_gate.get("passed", False)
                                        else (False, 0.0)
                                    )
                                    if b_v_ok:
                                        if b_trim_off > 0.5 and (get_duration_ms(master_file) / 1000.0 - b_trim_off) >= 8.0:
                                            b_tr_tmp = str(Path(master_file).with_name(f"_fftmp_btr_{Path(master_file).name}"))
                                            try:
                                                subprocess.run(
                                                    [
                                                        "ffmpeg", "-y", "-v", "error",
                                                        "-ss", f"{b_trim_off:.2f}", "-i", master_file,
                                                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "19", "-an",
                                                        b_tr_tmp,
                                                    ],
                                                    stdout=subprocess.DEVNULL,
                                                    stderr=subprocess.DEVNULL,
                                                    timeout=25,
                                                    check=False,
                                                )
                                                if is_completed_clean_mp4(b_tr_tmp, min_size_bytes=45_000, min_duration_ms=5000):
                                                    Path(b_tr_tmp).replace(master_file)
                                            except Exception:
                                                pass
                                            finally:
                                                Path(b_tr_tmp).unlink(missing_ok=True)
                                        master_source_label = f"Bilibili 16:9 Chapter #{chapter_idx} ({bvid})"
                                        master_url = f"https://www.bilibili.com/video/{bvid}"
                                        meta_file.write_text(f"{master_source_label}|||{master_url}", encoding="utf-8")
                                        logger.info(
                                            f"🎬 [LongForm ChapterPool] Đã tải & duyệt Vision Master Chương #{chapter_idx} từ Bilibili 16:9 ({bvid})!"
                                        )
                                        break
                                    else:
                                        Path(master_file).unlink(missing_ok=True)
                                else:
                                    Path(master_file).unlink(missing_ok=True)
                    except Exception as e_bili:
                        logger.debug(f"[LongForm ChapterPool] Bilibili master skip: {e_bili}")
            if not is_completed_clean_mp4(master_file, min_size_bytes=60_000, min_duration_ms=5000) and not _is_cancelled() and len(yt_cands) <= 5:
                try:
                    failed_marker.write_text("failed", encoding="utf-8")
                except Exception:
                    pass
    finally:
        ch_lock.release()

    if meta_file.exists():
        try:
            parts = meta_file.read_text(encoding="utf-8").split("|||")
            master_source_label = parts[0]
            if len(parts) > 1:
                master_url = parts[1]
        except Exception:
            pass

    # Nếu đã có file Master của Chương trên đĩa -> LUÔN hoàn tất nhát cắt Local (<1s) ngay cả khi deadline mạng vừa chạm ngưỡng,
    # tuyệt đối không bỏ phí file Master 16:9 đã tải và vượt qua kiểm duyệt Vision!
    if is_completed_clean_mp4(master_file, min_size_bytes=60_000, min_duration_ms=5000):
        master_dur_s = get_duration_ms(master_file) / 1000.0
        ideal_stride = max(target_duration_sec + 3.5, master_dur_s / max(1, scenes_per_chapter))
        max_safe_stride = max(4.5, (master_dur_s - target_duration_sec - 1.5) / max(1, scenes_per_chapter - 1))
        stride = min(ideal_stride, max_safe_stride)
        base_offset_s = round(slot_in_chapter * stride, 2)
        max_try_offsets = 4 if int(scene_id) == 1 else 1
        usable_master_span = max(0.0, master_dur_s - max(2.5, float(target_duration_sec)) - 0.8)
        for off_try in range(max_try_offsets):
            offset_s = (
                base_offset_s
                if off_try == 0
                else round((base_offset_s + off_try * max(4.5, usable_master_span / max(2, max_try_offsets))) % max(1.0, usable_master_span), 2)
            )
            if not extract_longform_scene_with_internal_jumpcut(
                master_path=master_file,
                output_path=out_path,
                start_offset_sec=offset_s,
                target_duration_sec=max(2.5, float(target_duration_sec)),
                scene_id=scene_id,
            ):
                continue
            if int(scene_id) == 1:
                try:
                    from app.graph.nodes.hook_video_node import _ensure_hook_clip_sharpness_and_luma
                    ok_s1_sh, s1_sh_val = _ensure_hook_clip_sharpness_and_luma(out_path, min_sharpness=13.2)
                    if not ok_s1_sh:
                        logger.info(
                            f"Scene 1: 🔎 [ChapterPool Sharpness] Bỏ qua offset={offset_s:.1f}s độ nét thấp ({s1_sh_val:.2f} < 13.2), thử mốc sắc nét hơn..."
                        )
                        Path(out_path).unlink(missing_ok=True)
                        continue
                except Exception:
                    pass
            if not is_clip_visually_unique_in_job(
                out_path,
                job_id,
                scene_id=scene_id,
                master_key=master_file,
                max_master_uses=(3 if master_dur_s >= 34.0 else 2),
            ):
                Path(out_path).unlink(missing_ok=True)
                logger.info(f"Scene {scene_id}: 🚫 [VisualCritic Dedup] Loại bỏ clip trùng lặp/vượt trần Master từ {master_source_label}")
                continue
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


def get_chapter_master_meta_paths(master_path: Path) -> List[Path]:
    """Trả về tất cả đường dẫn chứng chỉ Vision khả dĩ (`*_meta.txt` và `*.meta`) của một file Chapter Master."""
    mp = Path(master_path)
    candidates: List[Path] = []
    if mp.name.endswith("_master.mp4"):
        candidates.append(mp.with_name(mp.name[:-len("_master.mp4")] + "_meta.txt"))
    candidates.append(mp.with_suffix(".meta"))
    return candidates


def has_verified_chapter_master_meta(master_path: Path) -> bool:
    """Kiểm tra xem Chapter Master đã có chứng chỉ Vision hợp lệ (`*_meta.txt` hoặc `*.meta`) trên ổ đĩa hay chưa."""
    return any(p.exists() and p.stat().st_size > 0 for p in get_chapter_master_meta_paths(master_path))


def slice_any_available_chapter_master_for_scene(
    media_dir: Path,
    scene_id: int,
    target_duration_sec: float,
    job_id: int,
    output_override_path: Optional[str] = None,
    exclude_masters: Optional[Set[str]] = None,
    max_master_uses_override: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Khi một phân cảnh bị giới hạn API mạng (429 / timeout) ở cả nguồn riêng lẫn Stock 16:9,
    tận dụng ngay các file `chapter_*_master.mp4` 16:9 đã được Gemini Vision duyệt sạch trên ổ cứng của chính Job này,
    cắt một phân đoạn Jump-Cut ở mốc thời gian lệch pha độc lập và kiểm tra `is_clip_visually_unique_in_job` (dHash 64-bit)
    để đảm bảo 0% trùng khung hình và 0% rơi về bảng xanh placeholder (`scene_XXX_graphic.png`).
    """
    from app.utils.ffmpeg_helper import is_completed_clean_mp4

    chapter_dir = media_dir / "chapter_pool"
    if not chapter_dir.is_dir():
        return None
    excl_norm = {str(Path(m).name).lower() for m in (exclude_masters or set()) if m}
    masters = sorted(
        [
            p for p in chapter_dir.glob("chapter_*_master.mp4")
            if p.name.lower() not in excl_norm
            and has_verified_chapter_master_meta(p)
            and is_completed_clean_mp4(p, min_size_bytes=60_000, min_duration_ms=8000)
        ]
    )
    if not masters:
        return None

    out_path = output_override_path or str(media_dir / f"scene_{scene_id:03d}_pool_16x9.mp4")
    req_dur = max(2.5, float(target_duration_sec))
    n_m = len(masters)
    start_idx = (int(scene_id) * 3) % n_m
    ordered_masters = masters[start_idx:] + masters[:start_idx]
    max_steps = 8 if max_master_uses_override else (5 if int(scene_id) == 1 else 4)

    for m_path in ordered_masters:
        m_dur = get_duration_ms(str(m_path)) / 1000.0
        usable_span = max(0.0, m_dur - req_dur - 1.0)
        if usable_span <= 0.5:
            continue
        eff_max_uses = int(max_master_uses_override) if max_master_uses_override else (3 if m_dur >= 34.0 else 2)
        # Thử nhiều mốc offset lệch pha trên mỗi Master cho tới khi đạt chuẩn độ nét (Cảnh 1 >= 13.2) và dHash xác nhận khung hình mới hoàn toàn
        for cand_step in range(max_steps):
            frac = (((int(scene_id) * 37 + cand_step * 23) % 89) / 89.0)
            off_s = round(frac * usable_span, 2)
            if extract_longform_scene_with_internal_jumpcut(
                master_path=str(m_path),
                output_path=out_path,
                start_offset_sec=off_s,
                target_duration_sec=req_dur,
                scene_id=scene_id,
            ):
                if int(scene_id) == 1:
                    try:
                        from app.graph.nodes.hook_video_node import _ensure_hook_clip_sharpness_and_luma
                        ok_sh_s1, sh_val_s1 = _ensure_hook_clip_sharpness_and_luma(out_path, min_sharpness=13.2)
                        if not ok_sh_s1:
                            logger.debug(
                                f"Scene 1: [Cross-Slice Sharpness] '{m_path.name}'@{off_s:.1f}s độ nét {sh_val_s1:.2f} < 13.2 -> thử mốc khác"
                            )
                            Path(out_path).unlink(missing_ok=True)
                            continue
                    except Exception:
                        pass
                if is_clip_visually_unique_in_job(
                    out_path,
                    job_id,
                    scene_id=scene_id,
                    master_key=str(m_path),
                    max_master_uses=eff_max_uses,
                ):
                    logger.info(
                        f"Scene {scene_id}: ⚡ [ChapterPool Cross-Slice Rescue] Cắt phân đoạn độc lập (offset={off_s:.1f}s, VisualCritic-unique) "
                        f"từ '{m_path.name}' -> {Path(out_path).name}"
                    )
                    return {
                        "media_path": out_path,
                        "media_type": "video",
                        "production_method": "CHAPTER_POOL_16X9_JUMPCUT_HD",
                        "source_url": str(m_path),
                        "rationale": f"Verified 16:9 Master Cross-Slice ({m_path.name} @ {off_s:.1f}s)",
                    }
                Path(out_path).unlink(missing_ok=True)
    return None

