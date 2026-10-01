"""
app.services.media.archive_fetcher — Trích xuất tư liệu lịch sử, chân dung chính thức và ảnh web (Wikipedia, Wikimedia Commons, Google Custom Search).

Hỗ trợ:
1. Tra cứu Wikipedia API để lấy ảnh chân dung chính thức (pageimage/thumbnail 1200px chuẩn HD).
2. Tìm kiếm Wikimedia Commons nâng cao với bộ lọc ảnh bitmap, tỷ lệ khung hình, chống rác đô thị.
3. Google Custom Search Engine (CSE) dự phòng khi có cấu hình API.
"""

import logging
import requests
from typing import List, Dict, Optional

from config.settings import settings
from app.services.media.entity_detector import WIKIMEDIA_USER_AGENT, FORBIDDEN_ENTITY_NOISE

logger = logging.getLogger(__name__)


class ArchiveFetcher:
    """Fetcher chuyên trách kết nối các kho lưu trữ tri thức lịch sử và bách khoa toàn thư."""

    def __init__(self):
        self._google_cx = getattr(settings, 'GOOGLE_SEARCH_CX', '') or ''
        self._google_search_key = getattr(settings, 'GOOGLE_SEARCH_API_KEY', '') or ''

    def search_wikipedia_entity_image(self, entity_name: str) -> Optional[Dict]:
        """
        Tìm ảnh chính thức của nhân vật/địa danh qua Wikipedia API.
        Ưu tiên lấy ảnh đại diện (thumbnail 1200px) từ bài viết Wikipedia.
        """
        headers = {"User-Agent": WIKIMEDIA_USER_AGENT}
        search_params = {
            "action": "query",
            "format": "json",
            "titles": entity_name,
            "redirects": 1,
            "prop": "pageimages|images",
            "piprop": "original|thumbnail",
            "pithumbsize": 1200,
            "imlimit": 10,
        }

        try:
            res = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params=search_params, headers=headers, timeout=10
            )
            if res.status_code != 200:
                return None

            data = res.json()
            pages = data.get("query", {}).get("pages", {})

            for page_id, page_data in pages.items():
                if page_id == "-1":
                    return self._wikipedia_fuzzy_search(entity_name)

                original = page_data.get("original", {})
                thumbnail = page_data.get("thumbnail", {})

                orig_url = original.get("source") or ""
                thumb_url = thumbnail.get("source") or ""

                chosen_url = None
                chosen_w, chosen_h = 0, 0

                if thumb_url and self._is_valid_image_url(thumb_url):
                    chosen_url = thumb_url
                    chosen_w = thumbnail.get("width", 1200)
                    chosen_h = thumbnail.get("height", 1600)
                elif orig_url and self._is_valid_image_url(orig_url):
                    chosen_url = orig_url
                    chosen_w = original.get("width", 0)
                    chosen_h = original.get("height", 0)

                if chosen_url:
                    return {
                        "url": chosen_url,
                        "title": f"Wikipedia: {page_data.get('title', entity_name)}",
                        "source": "wikipedia",
                        "width": chosen_w,
                        "height": chosen_h,
                    }

                images = page_data.get("images", [])
                for img in images:
                    img_title = img.get("title", "")
                    if any(skip in img_title.lower() for skip in [
                        "commons-logo", "flag", "icon", "symbol", "coat_of_arms",
                        "map", "diagram", "chart", "logo", ".svg"
                    ]):
                        continue

                    img_info = self._get_wikimedia_file_url(img_title)
                    if img_info and self._is_valid_image_url(img_info.get("url", "")):
                        return img_info

        except Exception as e:
            logger.warning(f"Wikipedia entity search error for '{entity_name}': {e}")

        return None

    def _wikipedia_fuzzy_search(self, entity_name: str) -> Optional[Dict]:
        """Tìm kiếm mờ qua Wikipedia Search API khi tên chính xác không khớp."""
        headers = {"User-Agent": WIKIMEDIA_USER_AGENT}
        params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": entity_name,
            "srlimit": 3,
            "srprop": "snippet",
        }
        try:
            res = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params=params, headers=headers, timeout=10
            )
            if res.status_code == 200:
                results = res.json().get("query", {}).get("search", [])
                for result in results:
                    title = result.get("title", "")
                    if title:
                        return self.search_wikipedia_entity_image(title)
        except Exception as e:
            logger.debug(f"Wikipedia fuzzy search error: {e}")
        return None

    def _get_wikimedia_file_url(self, file_title: str) -> Optional[Dict]:
        """Lấy URL ảnh gốc từ Wikimedia Commons theo tên file (ưu tiên thumbnail 1280px từ CDN)."""
        headers = {"User-Agent": WIKIMEDIA_USER_AGENT}
        params = {
            "action": "query",
            "format": "json",
            "titles": file_title,
            "prop": "imageinfo",
            "iiprop": "url|size|mime",
            "iiurlwidth": 1280,
        }
        try:
            res = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params=params, headers=headers, timeout=10
            )
            if res.status_code == 200:
                pages = res.json().get("query", {}).get("pages", {})
                for _, page in pages.items():
                    ii = page.get("imageinfo", [])
                    if ii:
                        url = ii[0].get("thumburl") or ii[0].get("url", "")
                        mime = ii[0].get("mime", "")
                        w = ii[0].get("thumbwidth") or ii[0].get("width", 0)
                        h = ii[0].get("thumbheight") or ii[0].get("height", 0)
                        if url and mime.startswith("image/") and "svg" not in mime and w >= 300:
                            if self._is_valid_image_url(url):
                                return {
                                    "url": url,
                                    "title": file_title,
                                    "source": "wikimedia",
                                    "width": w,
                                    "height": h,
                                }
        except Exception:
            pass
        return None

    @staticmethod
    def _is_valid_image_url(url: str) -> bool:
        """Kiểm tra URL có phải ảnh bitmap raster hợp lệ không (loại trừ SVG, PDF, audio, cờ, huy hiệu...)."""
        if not url:
            return False
        clean_url = url.split("?")[0].split("#")[0].lower()
        if any(clean_url.endswith(ext) for ext in [".svg", ".pdf", ".ogg", ".ogv", ".webm", ".tif", ".tiff", ".gif"]):
            return False
        if ".svg" in clean_url:
            return False
        if any(skip in clean_url for skip in [
            "commons-logo", "flag_of", "coat_of_arms", "emblem", "symbol", "icon", "logo", "map", "diagram", "chart",
            "banner", "header", "ribbon", "strip", "footer", "border", "separator"
        ]):
            return False
        return True

    @staticmethod
    def _is_noisy_entity_file(title: str, url: str) -> bool:
        """Kiểm tra xem tên file hoặc URL có chứa từ khóa nhiễu địa danh/đô thị không."""
        clean = f"{title} {url}".lower()
        return any(noise in clean for noise in FORBIDDEN_ENTITY_NOISE)

    def search_web_photos(
        self,
        query: str,
        per_page: int = 3,
        filter_entity_noise: bool = False
    ) -> List[Dict]:
        """
        Tìm kiếm hình ảnh chất lượng cao trên Wikimedia Commons API.
        Dùng thumbnail 1280px từ CDN Varnish để tránh lỗi 429 và tải nhanh.
        """
        headers = {"User-Agent": WIKIMEDIA_USER_AGENT}
        queries_to_try = [query]
        if not any(w in query.lower() for w in ["photo", "portrait", "photograph", "image"]):
            queries_to_try.append(f"{query} photograph")

        for search_query in queries_to_try:
            params = {
                "action": "query",
                "generator": "search",
                "gsrsearch": f"filetype:bitmap {search_query}",
                "gsrnamespace": 6,
                "gsrlimit": per_page * 5,
                "prop": "imageinfo",
                "iiprop": "url|mime|size|extmetadata",
                "iiurlwidth": 1280,
                "iiextmetadatafilter": "ImageDescription|ObjectName",
                "format": "json",
            }
            try:
                res = requests.get(
                    "https://commons.wikimedia.org/w/api.php",
                    params=params, headers=headers, timeout=12
                )
                if res.status_code == 200:
                    pages = res.json().get("query", {}).get("pages", {})
                    photos = []
                    for _, v in pages.items():
                        ii = v.get("imageinfo", [])
                        if not ii:
                            continue
                        info = ii[0]
                        mime = info.get("mime", "")
                        url = info.get("thumburl") or info.get("url", "")
                        w = info.get("thumbwidth") or info.get("width", 0)
                        h = info.get("thumbheight") or info.get("height", 0)
                        file_title = v.get("title", "")

                        if filter_entity_noise and self._is_noisy_entity_file(file_title, url):
                            continue

                        forbidden_wiki_terms = [
                            "banner", "header", "ribbon", "strip", "footer", "border",
                            "diagram", "infographic", "flowchart", "pie_chart", "bar_graph", "schematic",
                            "blueprint", "coat_of_arms", "coat of arms", "postage_stamp", "stamp",
                            "coin", "banknote", "currency", "seal_of", "flag_of", "flag", "emblem", "crest",
                            "locator_map", "location_map", "topographic", "map_", "_map", "orthographic",
                            "distribution_map", "logo", "icon", "symbol"
                        ]
                        if any(b in file_title.lower() for b in forbidden_wiki_terms):
                            continue

                        if (mime.startswith("image/") 
                            and "svg" not in mime 
                            and url 
                            and w >= 400 
                            and h >= 400):
                            
                            aspect = max(w / h, h / w) if (w > 0 and h > 0) else 99
                            if aspect > 2.5:
                                continue

                            score = w * h
                            if w > h:
                                score *= 0.8

                            photos.append({
                                "url": url,
                                "title": file_title,
                                "width": w,
                                "height": h,
                                "source": "wikimedia_commons",
                                "score": score,
                            })

                    if photos:
                        photos.sort(key=lambda x: x.get("score", 0), reverse=True)
                        return photos[:per_page]

            except Exception as e:
                logger.warning(f"Wikimedia Commons search error for '{search_query}': {e}")

        words = query.strip().split()
        if len(words) > 2:
            simplified = " ".join(words[:2])
            logger.info(f"Wikimedia: Không thấy ảnh cho '{query}', thử lại với '{simplified}'...")
            return self.search_web_photos(simplified, per_page, filter_entity_noise=filter_entity_noise)

        return []

    def search_google_images(self, query: str, per_page: int = 3) -> List[Dict]:
        """Tìm kiếm ảnh qua Google Custom Search JSON API."""
        if not self._google_search_key or not self._google_cx:
            return []

        try:
            params = {
                "key": self._google_search_key,
                "cx": self._google_cx,
                "q": query,
                "searchType": "image",
                "num": min(per_page, 10),
                "safe": "active",
                "imgSize": "large",
            }
            res = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params=params, timeout=10
            )
            if res.status_code == 200:
                items = res.json().get("items", [])
                results = []
                for item in items:
                    link = item.get("link", "")
                    if link and self._is_valid_image_url(link):
                        img_info = item.get("image", {})
                        results.append({
                            "url": link,
                            "title": item.get("title", query),
                            "width": img_info.get("width", 1080),
                            "height": img_info.get("height", 1920),
                            "source": "google_search",
                        })
                return results
        except Exception as e:
            logger.warning(f"Google Image Search error for '{query}': {e}")
        return []
