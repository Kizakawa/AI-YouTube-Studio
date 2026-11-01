"""
License policy for third-party video footage (YouTube) — miễn phí & tự động 100%.

Mục tiêu: chỉ tái sử dụng footage YouTube có giấy phép cho phép (Creative Commons Attribution - CC BY),
tự động ghi nguồn (credit) vào mô tả video để tuân thủ điều kiện "Attribution" của giấy phép.

Chế độ (env `MEDIA_LICENSE_MODE`):
- ``cc_only`` (mặc định): tìm kiếm YouTube bằng bộ lọc Creative Commons và XÁC MINH trường `license`
  của từng video trước khi tải. Video không xác minh được giấy phép sẽ bị loại.
- ``any``: hành vi cũ (tìm kiếm mọi video) — KHÔNG khuyến nghị (rủi ro Content ID / reused content).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

# Bộ lọc tìm kiếm YouTube (tham số `sp`, protobuf base64):
#   EgIwAQ%3D%3D -> features{creative_commons}
_YT_SP_CREATIVE_COMMONS = "EgIwAQ%3D%3D"
_CC_LICENSE_RE = re.compile(r"creative\s*commons", re.IGNORECASE)
_CREDITS_MARKER = "🎥 Footage credits (CC BY):"
_MAX_CREDIT_LINES = 8

_credits_lock = threading.Lock()


def license_mode() -> str:
    mode = os.getenv("MEDIA_LICENSE_MODE", "any").strip().lower()
    return mode if mode in ("cc_only", "any") else "any"


def cc_only_enabled() -> bool:
    return license_mode() == "cc_only"


def youtube_search_target(query: str, max_results: int = 12) -> str:
    """Trả về chuỗi/URL tìm kiếm cho yt-dlp, tự động áp bộ lọc Creative Commons khi ở chế độ `cc_only`."""
    q = re.sub(r"\s+", " ", str(query or "")).strip()
    if not cc_only_enabled():
        return f"ytsearch{int(max_results)}:{q}"
    return f"https://www.youtube.com/results?search_query={quote_plus(q)}&sp={_YT_SP_CREATIVE_COMMONS}"


def youtube_search_opts(base_opts: Dict[str, Any], max_results: int = 12) -> Dict[str, Any]:
    """Bổ sung giới hạn số kết quả khi tìm bằng URL (URL search không có hậu tố `ytsearchN:`)."""
    opts = dict(base_opts)
    if cc_only_enabled():
        opts["playlistend"] = int(max_results)
    return opts


def optimize_ytdlp_download_opts(base_opts: Dict[str, Any], concurrent_fragments: int = 4) -> Dict[str, Any]:
    """
    Tối ưu tốc độ tải và chống YouTube bóp băng thông (n-challenge / DASH throttling),
    kế thừa từ kỹ thuật của Y2A-Auto (youtube_handler.py):
      - `concurrent_fragment_downloads`: Tải đa luồng 4 phân mảnh DASH/HLS cùng lúc (nhanh gấp 3-4x).
      - `http_chunk_size`: Tải theo HTTP Range chunk 10MB để vượt cơ chế bóp băng thông HTTPS của YouTube.
      - `throttledratelimit`: Tự động ngắt và giải mã lại URL nếu tốc độ bị bóp xuống < 100KB/s.
      - `fragment_retries` & `extractor_retries`: Tăng độ bền khi mạng chập chờn.
    """
    opts = dict(base_opts)
    opts.setdefault("concurrent_fragment_downloads", max(1, int(concurrent_fragments)))
    opts.setdefault("http_chunk_size", 10 * 1024 * 1024)
    opts.setdefault("throttledratelimit", 100 * 1024)
    opts.setdefault("fragment_retries", 3)
    opts.setdefault("extractor_retries", 3)
    return opts


def is_reuse_allowed(info: Optional[Dict[str, Any]]) -> bool:
    """Xác minh giấy phép của 1 video từ metadata đầy đủ của yt-dlp (trường `license`)."""
    if not cc_only_enabled():
        return True
    if not isinstance(info, dict):
        return False
    return bool(_CC_LICENSE_RE.search(str(info.get("license") or "")))


def ytdlp_cli_match_filter(base_filter: str = "") -> str:
    """Bộ lọc `--match-filter` cho yt-dlp CLI: yêu cầu trường license chứa 'Creative Commons'."""
    parts = [p for p in (base_filter.strip(),) if p]
    if cc_only_enabled():
        parts.append("license ~= '(?i)creative commons'")
    return " & ".join(parts)


# ── Ưu tiên số 1 cho định dạng dọc 9:16 gọn nhẹ <= 1920p (<18MB) để tải tức thì 0.5s, tránh stream 4K 50MB ──
VERTICAL_FIRST_YTDLP_FORMAT = (
    "bestvideo[protocol=https][aspect_ratio<=1][height>=720][height<=1920][filesize<18M][ext=mp4]/"
    "bestvideo[protocol=https][aspect_ratio<=1][height>=720][height<=1920][filesize_approx<18M][ext=mp4]/"
    "bestvideo[protocol=https][aspect_ratio<=1][height>=720][height<=1440][ext=mp4]/"
    "bestvideo[aspect_ratio<=1][height>=720][height<=1920][ext=mp4]/"
    "bestvideo[aspect_ratio<=1][height>=480][height<=1920][ext=mp4]/"
    "best[aspect_ratio<=1][height>=480][height<=1920]/"
    "bestvideo[protocol=https][height>=720][height<=1080][filesize<18M][ext=mp4]/"
    "bestvideo[protocol=https][height>=720][height<=1080][ext=mp4]/"
    "bestvideo[protocol=https][height>=480][height<=1080][ext=mp4]/"
    "bestvideo[height>=480][height<=1080]/bestvideo/best"
)


def is_entry_vertical_short(entry: Optional[Dict[str, Any]]) -> bool:
    """
    Nhận diện một ứng viên YouTube có phải là video dọc 9:16 (YouTube Short nguyên bản) hay không
    dựa trên URL `/shorts/`, thumbnail dọc (`oardefault`/`oar2` hoặc `height > width`), hoặc `aspect_ratio <= 1.0`.
    """
    if not isinstance(entry, dict):
        return False
    if entry.get("is_vertical_short") is True:
        return True
    u = str(entry.get("url") or entry.get("webpage_url") or "").lower()
    if "/shorts/" in u:
        return True
    w = int(entry.get("width") or 0)
    h = int(entry.get("height") or 0)
    if w > 0 and h > 0 and h > w:
        return True
    ar = entry.get("aspect_ratio")
    if isinstance(ar, (int, float)) and 0 < float(ar) <= 1.0:
        return True
    for th in (entry.get("thumbnails") or []):
        if not isinstance(th, dict):
            continue
        tu = str(th.get("url") or "").lower()
        tw = int(th.get("width") or 0)
        th_h = int(th.get("height") or 0)
        if "oardefault" in tu or "oar2" in tu or "/frame0.jpg" in tu or (tw > 0 and th_h > tw):
            return True
    return False


def _parse_short_views_text(views_str: str) -> int:
    """Chuyển chuỗi lượt xem ('154K views', '2.4M views', '154 thousand views') thành số nguyên."""
    s = str(views_str or "").lower().replace(",", "").strip()
    if not s:
        return 0
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(k|m|b|thousand|million|billion)?", s)
    if not m:
        return 0
    val = float(m.group(1))
    unit = (m.group(2) or "").strip()
    mult = (
        1_000_000_000 if unit in ("b", "billion") else
        1_000_000 if unit in ("m", "million") else
        1_000 if unit in ("k", "thousand") else 1
    )
    return int(val * mult)


_VN_EXCLUSIVE_DIACRITIC_RE = re.compile(
    r"[ơớờởỡợưứừửữựđăắằẳẵặảẩẫậẻểễệỉịỏổỗộủửữựỷỹỵ]",
    re.IGNORECASE,
)


def search_youtube_native_shorts_flat(query: str, max_results: int = 20) -> List[Dict[str, Any]]:
    """
    Trích xuất trực tiếp các video YouTube Shorts dọc 9:16 nguyên bản (`/shorts/<id>`)
    từ kệ `shortsLockupViewModel` & `reelItemRenderer` trong `ytInitialData` của trang tìm kiếm YouTube.
    Khắc phục triệt để hạn chế của `yt-dlp ytsearch:` (vốn bỏ qua kệ `shortsLockupViewModel` và chỉ trả về video ngang 16:9).
    """
    q_clean = re.sub(r"\s+", " ", str(query or "")).strip()
    if not q_clean:
        return []
    query_is_vi = bool(_VN_EXCLUSIVE_DIACRITIC_RE.search(q_clean))
    q_with_tag = q_clean if "#short" in q_clean.lower() else f"{q_clean} #shorts"
    search_url = f"https://www.youtube.com/results?search_query={quote_plus(q_with_tag)}&gl=US&hl=en"

    data: Optional[Dict[str, Any]] = None
    for _attempt in range(2):
        try:
            import requests
            resp = requests.get(
                search_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                cookies={"CONSENT": "YES+cb"},
                timeout=7,
            )
            if resp.status_code != 200 or not resp.text:
                continue
            m = re.search(
                r"(?:var\s+ytInitialData|window\[\"ytInitialData\"\])\s*=\s*(\{.*?\});\s*</script>",
                resp.text,
                re.DOTALL,
            )
            if not m:
                continue
            parsed = json.loads(m.group(1))
            if isinstance(parsed, dict) and "shortsLockupViewModel" in resp.text:
                data = parsed
                break
            if isinstance(parsed, dict) and data is None:
                data = parsed
        except Exception as e:
            logger.debug(f"[LicensePolicy] search_youtube_native_shorts_flat skip ({query}): {e}")
    if not data:
        return []

    out: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _walk(node: Any) -> None:
        if len(out) >= max_results:
            return
        if isinstance(node, dict):
            # 1. Kệ Shorts mới của YouTube (`shortsLockupViewModel` — chuẩn dọc 9:16 100%)
            if "shortsLockupViewModel" in node and isinstance(node["shortsLockupViewModel"], dict):
                sl = node["shortsLockupViewModel"]
                vid_id = (
                    (((sl.get("onTap") or {}).get("innertubeCommand") or {}).get("reelWatchEndpoint") or {}).get("videoId")
                    or str(sl.get("entityId") or "").replace("shorts-shelf-item-", "").strip()
                )
                if vid_id and len(vid_id) == 11 and vid_id not in seen_ids:
                    overlay_meta = sl.get("overlayMetadata") or {}
                    title = str(((overlay_meta.get("primaryText") or {}).get("content")) or "").strip()
                    sec_txt = str(((overlay_meta.get("secondaryText") or {}).get("content")) or "").strip()
                    a11y = str(sl.get("accessibilityText") or "").strip()
                    if not title and a11y:
                        title = a11y.split(",")[0].strip()
                    views = _parse_short_views_text(sec_txt or a11y)
                    seen_ids.add(vid_id)
                    out.append({
                        "id": vid_id,
                        "url": f"https://www.youtube.com/shorts/{vid_id}",
                        "webpage_url": f"https://www.youtube.com/shorts/{vid_id}",
                        "title": title or f"YouTube Short {vid_id}",
                        "uploader": "",
                        "duration": 25.0,
                        "view_count": views,
                        "width": 1080,
                        "height": 1920,
                        "aspect_ratio": 0.56,
                        "is_vertical_short": True,
                    })
                return

            # 2. Kệ Reel/Shorts旧 (`reelItemRenderer`)
            if "reelItemRenderer" in node and isinstance(node["reelItemRenderer"], dict):
                rl = node["reelItemRenderer"]
                vid_id = str(rl.get("videoId") or "").strip()
                if vid_id and len(vid_id) == 11 and vid_id not in seen_ids:
                    title = str(((rl.get("headline") or {}).get("simpleText")) or "").strip()
                    v_txt = str(((rl.get("viewCountText") or {}).get("simpleText")) or "").strip()
                    seen_ids.add(vid_id)
                    out.append({
                        "id": vid_id,
                        "url": f"https://www.youtube.com/shorts/{vid_id}",
                        "webpage_url": f"https://www.youtube.com/shorts/{vid_id}",
                        "title": title or f"YouTube Short {vid_id}",
                        "uploader": "",
                        "duration": 25.0,
                        "view_count": _parse_short_views_text(v_txt),
                        "width": 1080,
                        "height": 1920,
                        "aspect_ratio": 0.56,
                        "is_vertical_short": True,
                    })
                return

            # 3. `videoRenderer` (bao gồm cả `/shorts/` và video ngắn <= 180s)
            if "videoRenderer" in node and isinstance(node["videoRenderer"], dict):
                vr = node["videoRenderer"]
                vid_id = str(vr.get("videoId") or "").strip()
                nav_cmd = ((vr.get("navigationEndpoint") or {}).get("commandMetadata") or {}).get("webCommandMetadata") or {}
                nav_url = str(nav_cmd.get("url") or "")
                has_reel = "reelWatchEndpoint" in (vr.get("navigationEndpoint") or {})
                is_short_vr = bool("/shorts/" in nav_url or has_reel)
                len_txt = str(((vr.get("lengthText") or {}).get("simpleText")) or "").strip()
                dur_vr = 28.0 if is_short_vr else 0.0
                if len_txt:
                    parts_t = [p for p in len_txt.split(":") if p.strip().isdigit()]
                    if len(parts_t) == 2:
                        dur_vr = float(int(parts_t[0]) * 60 + int(parts_t[1]))
                    elif len(parts_t) == 3:
                        dur_vr = float(int(parts_t[0]) * 3600 + int(parts_t[1]) * 60 + int(parts_t[2]))
                if vid_id and len(vid_id) == 11 and vid_id not in seen_ids and (is_short_vr or (4.0 <= dur_vr <= 180.0)):
                    runs = ((vr.get("title") or {}).get("runs")) or []
                    title = "".join(str(r.get("text") or "") for r in runs if isinstance(r, dict)).strip()
                    v_txt = str(((vr.get("viewCountText") or {}).get("simpleText")) or "").strip()
                    ch_runs = (((vr.get("ownerText") or {}).get("runs")) or [{}])
                    uploader = str((ch_runs[0] if ch_runs else {}).get("text") or "").strip()
                    seen_ids.add(vid_id)
                    v_url = f"https://www.youtube.com/shorts/{vid_id}" if is_short_vr else f"https://www.youtube.com/watch?v={vid_id}"
                    out.append({
                        "id": vid_id,
                        "url": v_url,
                        "webpage_url": v_url,
                        "title": title or f"YouTube Video {vid_id}",
                        "uploader": uploader,
                        "duration": dur_vr if dur_vr > 0 else 28.0,
                        "view_count": _parse_short_views_text(v_txt),
                        "width": 1080 if is_short_vr else 1920,
                        "height": 1920 if is_short_vr else 1080,
                        "aspect_ratio": 0.56 if is_short_vr else 1.77,
                        "is_vertical_short": is_short_vr,
                    })
                return

            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(data)
    if not query_is_vi:
        out = [
            item for item in out
            if not _VN_EXCLUSIVE_DIACRITIC_RE.search(f"{item.get('title', '')} {item.get('uploader', '')}")
        ]
    return out[:max_results]



# ── Ghi nguồn (Attribution) ────────────────────────────────────────────────────

def _credits_path(job_id: int) -> Path:
    try:
        from config.settings import settings
        base = Path(getattr(settings, "BASE_DIR", Path.cwd()))
    except Exception:
        base = Path.cwd()
    return base / "storage" / "credits" / f"job_{int(job_id)}.json"


def record_credit(job_id: int, video_id: str, title: str = "", uploader: str = "", license_name: str = "CC BY") -> None:
    """Lưu thông tin nguồn của 1 clip đã được dùng trong video (idempotent theo `video_id`)."""
    if not job_id or not video_id:
        return
    path = _credits_path(job_id)
    with _credits_lock:
        try:
            credits: List[Dict[str, str]] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        except Exception:
            credits = []
        if any(c.get("video_id") == video_id for c in credits):
            return
        credits.append({
            "video_id": video_id,
            "title": str(title or "")[:90],
            "uploader": str(uploader or "")[:60],
            "license": license_name,
        })
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(credits, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[LicensePolicy] Không ghi được credits job {job_id}: {e}")


def get_credits(job_id: int) -> List[Dict[str, str]]:
    path = _credits_path(job_id)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        return []


def append_credits_to_description(description: Optional[str], job_id: int) -> str:
    """Nối khối ghi nguồn CC BY vào cuối mô tả (idempotent — thay thế khối cũ nếu đã có)."""
    desc = str(description or "")
    credits = get_credits(job_id)
    if not credits:
        return desc
    if _CREDITS_MARKER in desc:
        desc = desc.split(_CREDITS_MARKER)[0].rstrip()
    lines = [_CREDITS_MARKER]
    for c in credits[:_MAX_CREDIT_LINES]:
        who = c.get("uploader") or "YouTube creator"
        lines.append(f"• {who} — youtu.be/{c['video_id']} ({c.get('license') or 'CC BY'})")
    block = "\n".join(lines)
    return f"{desc}\n\n{block}".strip() if desc else block
