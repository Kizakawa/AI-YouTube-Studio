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
    mode = os.getenv("MEDIA_LICENSE_MODE", "cc_only").strip().lower()
    return mode if mode in ("cc_only", "any") else "cc_only"


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
