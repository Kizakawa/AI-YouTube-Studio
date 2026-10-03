"""
media_history.py — Quản lý lịch sử hình ảnh/video đã dùng giữa các video khác nhau (Cross-Job Deduplication).
Ngăn chặn triệt để tình trạng video sau lặp lại ảnh/clip của video trước dù kho stock rất rộng.
"""
import os
import re
import json
import time
import hashlib
import logging
import subprocess
from pathlib import Path
from typing import Set, List, Dict, Any, Optional

logger = logging.getLogger(__name__)

HISTORY_FILE = Path("storage/cache/recent_media_history.json")
MAX_HISTORY_ENTRIES = 500


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
        trimmed = entries[-MAX_HISTORY_ENTRIES:]
        tmp_file = HISTORY_FILE.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(trimmed, f, indent=2, ensure_ascii=False)
        tmp_file.replace(HISTORY_FILE)
    except Exception as e:
        logger.debug(f"[MediaHistory] Lỗi lưu {HISTORY_FILE}: {e}")


def compute_file_md5(file_path: str) -> str:
    """Tính mã băm MD5 cho một tệp tin cục bộ để phát hiện trùng lặp tuyệt đối byte-for-byte."""
    try:
        p = Path(file_path)
        if not p.exists() or p.stat().st_size == 0:
            return ""
        hasher = hashlib.md5()
        with open(p, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as e:
        logger.debug(f"[MediaHistory] Lỗi tính MD5 cho {file_path}: {e}")
        return ""


def compute_image_dhash(media_path: str) -> Optional[str]:
    """
    Tính dHash (difference hash) 64-bit cho ảnh hoặc frame đầu video để phát hiện ảnh trùng lặp
    kể cả khi bị nén lại, đổi đuôi hoặc đổi URL.
    """
    try:
        p = Path(media_path)
        if not p.exists() or p.stat().st_size < 50:
            return None

        img_target = str(p)
        temp_frame = None

        # Nếu là video, trích xuất frame tại 0.5s để tính dHash
        if p.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv"):
            temp_frame = str(p.parent / f"_hash_tmp_{p.stem}.jpg")
            cmd = [
                "ffmpeg", "-y", "-ss", "0.5", "-i", str(p),
                "-vframes", "1", "-q:v", "2", temp_frame
            ]
            res = subprocess.run(cmd, capture_output=True, timeout=8)
            if Path(temp_frame).exists() and Path(temp_frame).stat().st_size > 50:
                img_target = temp_frame
            else:
                return None

        from PIL import Image
        with Image.open(img_target) as img:
            resized = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            pixels = list(resized.getdata())
            diff = []
            for row in range(8):
                for col in range(8):
                    left = pixels[row * 9 + col]
                    right = pixels[row * 9 + col + 1]
                    diff.append("1" if left > right else "0")
            dhash_str = "".join(diff)

        if temp_frame:
            try:
                Path(temp_frame).unlink(missing_ok=True)
            except Exception:
                pass

        return dhash_str
    except Exception as e:
        logger.debug(f"[MediaHistory] Lỗi tính dHash cho {media_path}: {e}")
        return None


def hamming_distance(hash1: str, hash2: str) -> int:
    """Tính khoảng cách Hamming giữa 2 chuỗi nhị phân dHash."""
    if not hash1 or not hash2 or len(hash1) != len(hash2):
        return 999
    return sum(c1 != c2 for c1, c2 in zip(hash1, hash2))


def is_visual_duplicate(
    new_media_path: str,
    seen_md5s: Set[str],
    seen_dhashes: List[str],
    max_hamming_distance: int = 3,
) -> bool:
    """
    Kiểm tra xem file media mới có trùng lặp với bất kỳ file nào đã dùng trong video không:
    1. Kiểm tra MD5 exact match.
    2. Kiểm tra perceptual dHash (khoảng cách Hamming <= max_hamming_distance).
    """
    if not new_media_path or not Path(new_media_path).exists():
        return False

    # 1. Check MD5
    file_md5 = compute_file_md5(new_media_path)
    if file_md5 and file_md5 in seen_md5s:
        return True

    # 2. Check dHash
    file_dhash = compute_image_dhash(new_media_path)
    if file_dhash:
        for dh in seen_dhashes:
            if hamming_distance(file_dhash, dh) <= max_hamming_distance:
                return True

    return False


def extract_media_identity(url_or_path: str) -> Set[str]:
    """
    Trích xuất toàn bộ các định danh đại diện cho media (URL gốc, URL bỏ query params, ID số Pexels/Pixabay, tên tệp).
    Giúp phát hiện trùng lặp kể cả khi Pexels thêm các query params như ?auto=compress...
    """
    if not url_or_path:
        return set()

    identities = {url_or_path.strip()}
    clean_url = re.sub(r'\?.*$', '', url_or_path).strip()
    if clean_url:
        identities.add(clean_url)

    # Trích xuất Pexels ID: photos/12345/ hoặc videos/12345/
    m_px = re.search(r'/(?:photos|videos)/(\d+)/', url_or_path)
    if m_px:
        num_id = m_px.group(1)
        identities.add(num_id)
        identities.add(f"pexels:{num_id}")

    # Pexels VIDEO: file tải về (videos.pexels.com/video-files/<id>/...) và trang web (pexels.com/video/<slug>-<id>/)
    # → cùng 1 clip ở mọi độ phân giải / mọi dạng URL đều quy về 1 định danh pexels:<id>
    m_pxv = re.search(r'/video-files/(\d+)/', url_or_path) or re.search(
        r'pexels\.com/(?:[a-z-]+/)?(?:video|photo)/(?:[^/?#]*-)?(\d+)/?', url_or_path
    )
    if m_pxv:
        identities.add(f"pexels:{m_pxv.group(1)}")

    # Trích xuất Pixabay ID (trang web: pixabay.com/videos/<slug>-<id>/, CDN: cdn.pixabay.com/video/YYYY/MM/DD/<id>-<hash>_large.mp4)
    if "pixabay.com" in url_or_path:
        m_pb = (
            re.search(r'cdn\.pixabay\.com/video/\d{4}/\d{2}/\d{2}/(\d+)-', url_or_path)
            or re.search(r'pixabay\.com/(?:[a-z-]+/)?(?:videos|photos|illustrations|vectors)/(?:[^/?#]*-)?(\d+)/?', url_or_path)
        )
        if m_pb:
            identities.add(f"pixabay:{m_pb.group(1)}")

    # Trích xuất Wikimedia filename
    m_wiki = re.search(r'/([^/]+\.(?:jpg|jpeg|png|webp|mp4))', clean_url, re.IGNORECASE)
    if m_wiki:
        identities.add(f"wiki:{m_wiki.group(1).lower()}")

    return identities


def is_media_already_used(url_or_path: str, used_set: Set[str]) -> bool:
    """Kiểm tra xem media (qua bất kỳ định danh nào) đã từng có trong used_set chưa."""
    if not url_or_path or not used_set:
        return False
    keys = extract_media_identity(url_or_path)
    if any(k in used_set for k in keys):
        return True
    # used_set có thể chỉ chứa URL thô (vd: used_pool_urls.add(url)) → so khớp cả định danh của phần tử đã dùng
    canon = {k for k in keys if ":" in k and not k.startswith(("http", "wiki:"))}
    if not canon:
        return False
    for u in used_set:
        if isinstance(u, str) and u.startswith("http") and canon & extract_media_identity(u):
            return True
    return False


def get_recent_media_urls(limit: int = 250) -> Set[str]:
    """
    Lấy tập hợp các URL/ID media đã dùng gần đây kèm toàn bộ định danh canonical (Pexels ID, stripped URL).
    Dùng để nạp sẵn vào used_urls của job mới nhằm tránh trùng lặp 100%.
    """
    entries = _load_history()
    recent = entries[-limit:] if len(entries) > limit else entries
    used_set = set()
    for item in recent:
        url = item.get("url")
        if url:
            used_set.update(extract_media_identity(url))
        f_md5 = item.get("md5")
        if f_md5:
            used_set.add(f"md5:{f_md5}")
    return used_set


def record_used_media(url: str, query: str = "", job_id: int = 0, source: str = "pexels", media_path: Optional[str] = None):
    """Ghi nhận 1 media URL/file vừa được tải và sử dụng trong 1 phân cảnh."""
    if not url:
        return
    entries = _load_history()
    if any(e.get("url") == url for e in entries[-50:]):
        return

    f_md5 = compute_file_md5(media_path) if media_path and Path(media_path).exists() else ""

    entries.append({
        "url": url,
        "query": query,
        "job_id": job_id,
        "source": source,
        "md5": f_md5,
        "timestamp": int(time.time()),
    })
    _save_history(entries)
    logger.debug(f"[MediaHistory] Đã ghi nhận media mới: {url[:60]}... (Job #{job_id})")


def record_batch_used_media(urls: List[str], job_id: int = 0):
    """Ghi nhận hàng loạt media URLs sau khi hoàn tất render job."""
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
