import logging
import requests
from pathlib import Path
from typing import List, Dict, Optional
from config.settings import settings

logger = logging.getLogger(__name__)

class MediaAgent:
    """Agent chịu trách nhiệm tìm kiếm và tải video/hình ảnh nền từ Pexels API & Web."""

    def __init__(self, api_key: Optional[str] = None):
        raw_key = api_key or settings.PEXELS_API_KEY
        # Kiểm tra key thực tế, bỏ qua nếu là placeholder
        if raw_key and raw_key.strip() and raw_key != "your_pexels_api_key_here":
            self.api_key = raw_key.strip()
        else:
            self.api_key = None
            
        self.base_url = "https://api.pexels.com/videos/search"
        self.photo_url = "https://api.pexels.com/v1/search"

    def search_videos(self, query: str, per_page: int = 3, orientation: str = "portrait") -> List[Dict]:
        """Tìm kiếm video trên Pexels theo từ khóa với tỷ lệ dọc (portrait 9:16)."""
        if not self.api_key:
            return []

        try:
            headers = {"Authorization": self.api_key}
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
        if not self.api_key:
            return []

        try:
            headers = {"Authorization": self.api_key}
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
        Miễn phí 100%, không cần API Key, hoạt động ổn định khi không có Pexels.
        """
        headers = {"User-Agent": "YouTubeShortsBot/1.0 (contact@example.com)"}
        params = {
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,  # 6 = File namespace
            "gsrlimit": per_page * 2,
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
                        # Chỉ lấy ảnh bitmap thực tế (jpg, png, webp), loại trừ SVG/PDF
                        if mime.startswith("image/") and not mime.endswith("svg+xml") and url:
                            photos.append({
                                "url": url,
                                "title": v.get("title", ""),
                                "width": ii[0].get("width", 0),
                                "height": ii[0].get("height", 0)
                            })
                return photos[:per_page]
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

