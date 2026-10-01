"""
media_history.py — Quản lý lịch sử hình ảnh/video đã dùng giữa các video khác nhau (Cross-Job Deduplication).
Ngăn chặn triệt để tình trạng video sau lặp lại ảnh/clip của video trước dù kho stock rất rộng.
"""
import os
import json
import time
import logging
from pathlib import Path
from typing import Set, List, Dict, Any, Optional

logger = logging.getLogger(__name__)

HISTORY_FILE = Path("storage/cache/recent_media_history.json")
MAX_HISTORY_ENTRIES = 300


def _load_history() -> List[Dict[str, Any]]:
    """Đọc danh sách lịch sử media đã dùng từ file JSON."""
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception as e:
        logger.debug(f"[MediaHistory] Lỗi đọc {HISTORY_FILE}: {e}")
    return []


def _save_history(entries: List[Dict[str, Any]]):
    """Lưu danh sách lịch sử media (giới hạn MAX_HISTORY_ENTRIES)."""
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Giữ lại tối đa MAX_HISTORY_ENTRIES bản ghi mới nhất
        trimmed = entries[-MAX_HISTORY_ENTRIES:]
        tmp_file = HISTORY_FILE.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(trimmed, f, indent=2, ensure_ascii=False)
        tmp_file.replace(HISTORY_FILE)
    except Exception as e:
        logger.debug(f"[MediaHistory] Lỗi lưu {HISTORY_FILE}: {e}")


def get_recent_media_urls(limit: int = 200) -> Set[str]:
    """
    Lấy tập hợp các URL/ID media (ảnh, video Pexels, Pixabay...) đã dùng trong các video gần nhất.
    Dùng để nạp sẵn vào used_urls của job mới nhằm tránh trùng lặp 100%.
    """
    entries = _load_history()
    recent = entries[-limit:] if len(entries) > limit else entries
    used_set = set()
    for item in recent:
        url = item.get("url")
        if url:
            used_set.add(url)
            # Thêm cả ID rút gọn nếu có trong URL Pexels
            # Ví dụ: https://images.pexels.com/photos/12345/pexels-photo-12345.jpeg -> 12345
            import re
            m = re.search(r'/(?:photos|videos)/(\d+)/', url)
            if m:
                used_set.add(m.group(1))
    return used_set


def record_used_media(url: str, query: str = "", job_id: int = 0, source: str = "pexels"):
    """Ghi nhận 1 media URL vừa được tải và sử dụng trong 1 phân cảnh."""
    if not url:
        return
    entries = _load_history()
    # Kiểm tra xem URL đã có trong danh sách gần nhất chưa
    if any(e.get("url") == url for e in entries[-50:]):
        return

    entries.append({
        "url": url,
        "query": query,
        "job_id": job_id,
        "source": source,
        "timestamp": int(time.time()),
    })
    _save_history(entries)
    logger.debug(f"[MediaHistory] Đã ghi nhận media mới: {url[:60]}... (Job #{job_id})")


def record_batch_used_media(urls: List[str], job_id: int = 0):
    """Ghi nhận hàng loạt media URLs sau khi render xong job."""
    if not urls:
        return
    entries = _load_history()
    existing_urls = {e.get("url") for e in entries}
    now = int(time.time())
    added = 0
    for u in urls:
        if u and u not in existing_urls:
            entries.append({
                "url": u,
                "job_id": job_id,
                "timestamp": now,
            })
            existing_urls.add(u)
            added += 1
    if added > 0:
        _save_history(entries)
        logger.info(f"[MediaHistory] Đã lưu {added} media URLs vào kho lịch sử cross-job (Tổng: {len(entries)})")
