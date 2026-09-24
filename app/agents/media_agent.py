import itertools
import logging
import requests
from pathlib import Path
from typing import List, Dict, Optional
from config.settings import settings

logger = logging.getLogger(__name__)

class MediaAgent:
    """Agent chịu trách nhiệm tìm kiếm và tải video/hình ảnh nền từ Pexels API & Web (hỗ trợ xoay vòng nhiều key)."""

    def __init__(self, api_key: Optional[str] = None):
        if api_key and api_key.strip() and api_key != "your_pexels_api_key_here":
            self.keys = [api_key.strip()]
        else:
            self.keys = [
                k for k in settings.get_pexels_keys_pool()
                if k and k.strip() and k != "your_pexels_api_key_here"
            ]
        self._key_cycle = itertools.cycle(self.keys) if self.keys else None
        self.base_url = "https://api.pexels.com/videos/search"
        self.photo_url = "https://api.pexels.com/v1/search"

    @property
    def api_key(self) -> Optional[str]:
        return self.keys[0] if self.keys else None

    def _get_active_key(self) -> Optional[str]:
        if self._key_cycle:
            return next(self._key_cycle)
        return None

    def search_videos(self, query: str, per_page: int = 3, orientation: str = "portrait") -> List[Dict]:
        """Tìm kiếm video trên Pexels theo từ khóa với tỷ lệ dọc (portrait 9:16)."""
        key = self._get_active_key()
        if not key:
            return []

        try:
            headers = {"Authorization": key}
            params = {
                "query": query,
                "per_page": per_page,
                "orientation": orientation
            }
            res = requests.get(self.base_url, headers=headers, params=params, timeout=10)
            if res.status_code == 200:
                return res.json().get("videos", [])
            elif res.status_code == 401:
                logger.warning("PEXELS_API_KEY không hợp lệ hoặc đã hết hạn.")
        except Exception as e:
            logger.warning(f"Lỗi khi tìm kiếm Pexels video: {e}")
        return []

    def search_photos(self, query: str, per_page: int = 3, orientation: str = "portrait") -> List[Dict]:
        """Tìm kiếm ảnh tĩnh trên Pexels theo từ khóa với tỷ lệ dọc (portrait 9:16) cho Ken Burns."""
        key = self._get_active_key()
        if not key:
            return []

        try:
            headers = {"Authorization": key}
            params = {
                "query": query,
                "per_page": per_page,
                "orientation": orientation
            }
            res = requests.get(self.photo_url, headers=headers, params=params, timeout=10)
            if res.status_code == 200:
                return res.json().get("photos", [])
            elif res.status_code == 401:
                logger.warning("PEXELS_API_KEY không hợp lệ hoặc đã hết hạn.")
        except Exception as e:
            logger.warning(f"Lỗi khi tìm kiếm Pexels photo: {e}")
        return []

    def search_web_photos(self, query: str, per_page: int = 3) -> List[Dict]:
        """
        Tìm kiếm hình ảnh mở chất lượng cao trên Web (Wikimedia Commons API).
        Miễn phí 100%, không cần API Key, tối ưu cho tài liệu / nhân vật lịch sử / tin tức thực tế.
        """
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AI-Shorts-Studio/2.0"}
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,  # 6 = File namespace
            "gsrlimit": per_page * 3,
            "prop": "imageinfo",
            "iiprop": "url|mime|size",
            "format": "json"
        }
        try:
            res = requests.get("https://commons.wikimedia.org/w/api.php", params=params, headers=headers, timeout=10)
            if res.status_code == 200:
                pages = res.json().get("query", {}).get("pages", {})
                photos = []
                for _, v in pages.items():
                    ii = v.get("imageinfo", [])
                    if ii:
                        mime = ii[0].get("mime", "")
                        url = ii[0].get("url", "")
                        w = ii[0].get("width", 0)
                        h = ii[0].get("height", 0)
                        # Chỉ lấy ảnh bitmap thực tế (jpg, png, webp) độ phân giải tốt (w >= 400), loại trừ SVG/PDF
                        if mime.startswith("image/") and not mime.endswith("svg+xml") and url and w >= 400:
                            photos.append({
                                "url": url,
                                "title": v.get("title", ""),
                                "width": w,
                                "height": h
                            })
                if photos:
                    return photos[:per_page]

            # Nếu tìm kiếm cụm từ dài không có, tự động rút gọn về 2-3 từ đầu (chủ thể chính/tên riêng)
            words = query.strip().split()
            if len(words) > 2:
                simplified = " ".join(words[:2])
                logger.info(f"Wikimedia: Không thấy ảnh cho '{query}', tự động thử lại với '{simplified}'...")
                return self.search_web_photos(simplified, per_page)
        except Exception as e:
            logger.warning(f"Lỗi khi tìm kiếm ảnh web (Wikimedia): {e}")
        return []

    def download_file(self, url: str, output_path: str) -> str:
        """Tải file media về máy tính với stream và timeout an toàn"""
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AI-Shorts-Bot/1.0"}
        res = requests.get(url, headers=headers, stream=True, timeout=30)
        res.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in res.iter_content(chunk_size=8192):
                f.write(chunk)
        return output_path

