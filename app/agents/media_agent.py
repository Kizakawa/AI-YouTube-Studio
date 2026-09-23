import itertools
import requests
from pathlib import Path
from typing import List, Dict, Optional
from config.settings import settings

class MediaAgent:
    """Agent chịu trách nhiệm tìm kiếm và tải video/hình ảnh nền từ Pexels API (hỗ trợ xoay vòng nhiều key)."""

    def __init__(self, api_key: Optional[str] = None):
        if api_key:
            self.keys = [api_key]
        else:
            self.keys = settings.get_pexels_keys_pool()
        self._key_cycle = itertools.cycle(self.keys) if self.keys else None
        self.base_url = "https://api.pexels.com/videos/search"

    def _get_active_key(self) -> Optional[str]:
        if self._key_cycle:
            return next(self._key_cycle)
        return None

    def search_videos(self, query: str, per_page: int = 3, orientation: str = "portrait") -> List[Dict]:
        """Tìm kiếm video trên Pexels theo từ khóa với tỷ lệ dọc (portrait 9:16)."""
        key = self._get_active_key()
        if not key:
            return []

        headers = {"Authorization": key}
        params = {
            "query": query,
            "per_page": per_page,
            "orientation": orientation
        }
        res = requests.get(self.base_url, headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            return res.json().get("videos", [])
        return []

    def download_file(self, url: str, output_path: str) -> str:
        """Tải file media về máy tính"""
        res = requests.get(url, stream=True, timeout=30)
        res.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in res.iter_content(chunk_size=8192):
                f.write(chunk)
        return output_path
