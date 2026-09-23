import requests
from pathlib import Path
from typing import List, Dict, Optional
from config.settings import settings

class MediaAgent:
    """Agent chịu trách nhiệm tìm kiếm và tải video/hình ảnh nền từ Pexels API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.PEXELS_API_KEY
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
        except Exception:
            pass
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
        except Exception:
            pass
        return []

    def download_file(self, url: str, output_path: str) -> str:
        """Tải file media về máy tính"""
        res = requests.get(url, stream=True, timeout=30)
        res.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in res.iter_content(chunk_size=8192):
                f.write(chunk)
        return output_path
