"""
app.services.media.web_image_fetcher — Tìm kiếm hình ảnh thực tế đa nguồn trên toàn bộ mạng Internet.

Hỗ trợ lấy ảnh liên quan, độ phân giải cao và bỏ qua các rào cản bản quyền khắt khe:
1. Open Web (DDGS / Live Web Index): Tìm kiếm ảnh thực tế từ toàn bộ mạng Internet (báo chí, tài liệu, bài viết, hình ảnh thực).
2. Wikipedia Direct Knowledge: Tìm ảnh trực tiếp từ bài viết Wikipedia theo thực thể/chủ đề (độ phân giải 1280px+).
3. Openverse (Không giới hạn): ~800 triệu ảnh từ Flickr, bảo tàng, phòng lưu trữ, Wikimedia.
4. Wikimedia Commons: ~100 triệu ảnh tư liệu tự do về lịch sử, địa danh, khoa học, động vật.
5. Unsplash (Nếu có key): Ảnh nghệ thuật nhiếp ảnh hiện đại chuẩn 9:16.
"""

import os
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Optional

import requests

from app.services.media.entity_detector import WIKIMEDIA_USER_AGENT

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
_CREDITS_FILE = Path("storage/cache/image_credits.jsonl")
_credits_lock = threading.Lock()

# Tên file Commons chứa các từ này thường là bản đồ / biểu tượng / sơ đồ — không phải ảnh minh họa đẹp
_COMMONS_BLOCKLIST = (
    " map", "map_", "_map", "map.", "diagram", "chart", "logo", "icon", "symbol", "flag_of", "flag of",
    "coat_of_arms", "coat of arms", "emblem", "seal_of", "seal of", "stamp", "banknote", "coin",
    "schematic", "blueprint", "floor plan", "infographic", "screenshot", "signature",
)


def web_image_search_enabled() -> bool:
    return os.getenv("ENABLE_WEB_IMAGE_SEARCH", "true").strip().lower() not in ("0", "false", "no", "off")


def bypass_copyright_enabled() -> bool:
    """Kiểm tra có bỏ qua rào cản bản quyền để tìm kiếm ảnh thực tế rộng rãi trên mạng hay không."""
    return os.getenv("BYPASS_IMAGE_COPYRIGHT", "true").strip().lower() not in ("0", "false", "no", "off")


def _aspect_ok(w: int, h: int, max_aspect: float = 2.8) -> bool:
    if not w or not h:
        return True
    return max(w / h, h / w) <= max_aspect


class WebImageFetcher:
    """Tìm kiếm ảnh thực tế trên toàn bộ mạng Internet đa nguồn, chạy song song."""

    def __init__(self):
        self._unsplash_key = os.getenv("UNSPLASH_ACCESS_KEY", "").strip()

    # ─────────────────────────────── Open Web (DDGS) ───────────────────────────
    def search_open_web(self, query: str, per_page: int = 8) -> List[Dict]:
        """
        Tìm kiếm ảnh thực tế từ toàn bộ mạng Internet (Google/Bing/DuckDuckGo index).
        Bỏ qua rào cản bản quyền để lấy những hình ảnh chân thực, chính xác và có độ phân giải cao nhất.
        """
        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS

            ddgs = DDGS(timeout=10)
            raw_results = list(ddgs.images(
                query,
                region="wt-wt",
                safesearch="moderate",
                max_results=max(per_page * 2, 10),
            ))

            out = []
            seen = set()
            for r in raw_results:
                img_url = r.get("image")
                if not img_url or img_url in seen:
                    continue
                seen.add(img_url)

                clean_url = str(img_url).lower().split("?")[0]
                if clean_url.endswith((".svg", ".gif", ".ico")):
                    continue

                w = r.get("width") or 1080
                h = r.get("height") or 1920
                try:
                    w, h = int(w), int(h)
                except Exception:
                    w, h = 1080, 1920

                if not _aspect_ok(w, h, max_aspect=2.8):
                    continue

                source_site = r.get("source") or "web"
                title = r.get("title") or query

                out.append({
                    "url": img_url,
                    "thumbnail": r.get("thumbnail") or img_url,
                    "title": title,
                    "alt": title,
                    "width": w,
                    "height": h,
                    "source": "open_web",
                    "provider": "open_web",
                    "license": "Web Public (Fair Use)",
                    "creator": source_site,
                    "page_url": r.get("url") or "",
                    "attribution": f"Web image from {source_site}",
                })
                if len(out) >= per_page:
                    break

            return out
        except Exception as e:
            logger.debug(f"[WebImage] Open web search error cho '{query}': {e}")
            return []

    # ─────────────────────────────── Wikipedia Page Images ─────────────────────
    def search_wikipedia_images(self, query: str, per_page: int = 4) -> List[Dict]:
        """
        Tìm ảnh trực tiếp từ các bài viết Wikipedia liên quan đến chủ đề/thực thể.
        Đặc biệt hiệu quả với nhân vật, sự kiện lịch sử, địa danh, sinh vật và thí nghiệm khoa học.
        """
        try:
            url = "https://en.wikipedia.org/w/api.php"
            params = {
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrlimit": max(per_page * 2, 6),
                "prop": "pageimages|images",
                "piprop": "original|thumbnail",
                "pithumbsize": 1280,
                "format": "json"
            }
            res = requests.get(url, params=params, headers={"User-Agent": WIKIMEDIA_USER_AGENT}, timeout=10)
            if res.status_code != 200:
                return []
            pages = res.json().get("query", {}).get("pages", {})
            out = []
            seen = set()
            for p in sorted(pages.values(), key=lambda x: x.get("index", 999)):
                orig = p.get("original", {})
                thumb = p.get("thumbnail", {})
                img_url = orig.get("source") or thumb.get("source")
                if not img_url or img_url in seen:
                    continue
                seen.add(img_url)

                title = p.get("title") or query
                w = orig.get("width") or thumb.get("width") or 1080
                h = orig.get("height") or thumb.get("height") or 1920

                out.append({
                    "url": img_url,
                    "thumbnail": thumb.get("source") or img_url,
                    "title": title,
                    "alt": title,
                    "width": w,
                    "height": h,
                    "source": "wikipedia",
                    "provider": "wikipedia",
                    "license": "Wikipedia Public Domain / CC",
                    "creator": "Wikipedia",
                    "page_url": f"https://en.wikipedia.org/?curid={p.get('pageid')}",
                    "attribution": f"Image via Wikipedia article '{title}'",
                })
                if len(out) >= per_page:
                    break
            return out
        except Exception as e:
            logger.debug(f"[WebImage] Wikipedia image search error cho '{query}': {e}")
            return []

    # ─────────────────────────────── Openverse ───────────────────────────────
    def search_openverse(self, query: str, per_page: int = 8) -> List[Dict]:
        """Tìm kiếm ảnh trên Openverse (~800 triệu ảnh từ Flickr, phòng lưu trữ, bảo tàng...)."""
        try:
            params = {
                "q": query,
                "page_size": 20,
                "mature": "false",
            }
            # Nếu người dùng muốn tuân thủ chặt giấy phép thương mại thì bật filter, ngược lại mở rộng toàn bộ
            if not bypass_copyright_enabled():
                params["license_type"] = "commercial,modification"

            res = requests.get(
                "https://api.openverse.org/v1/images/",
                params=params,
                headers={"User-Agent": _UA},
                timeout=10,
            )
            if res.status_code != 200:
                logger.debug(f"[WebImage] Openverse HTTP {res.status_code} cho '{query}'")
                return []
            out = []
            for r in res.json().get("results", []):
                url = r.get("url")
                w, h = r.get("width") or 0, r.get("height") or 0
                if not url or (w and w < 600) or (h and h < 600) or not _aspect_ok(w, h):
                    continue
                if str(url).lower().split("?")[0].endswith((".svg", ".gif", ".tif", ".tiff")):
                    continue
                lic = f"CC {str(r.get('license', '')).upper()} {r.get('license_version') or ''}".strip()
                if str(r.get("license", "")).lower() in ("cc0", "pdm"):
                    lic = "Public Domain" if r.get("license") == "pdm" else "CC0"
                provider = r.get("source") or r.get("provider") or "openverse"
                out.append({
                    "url": url,
                    "thumbnail": r.get("thumbnail") or url,
                    "title": r.get("title") or query,
                    "alt": r.get("title") or query,
                    "width": w or 1080,
                    "height": h or 1080,
                    "source": "openverse",
                    "provider": provider,
                    "license": lic or "Openverse",
                    "creator": r.get("creator") or "",
                    "page_url": r.get("foreign_landing_url") or "",
                    "attribution": r.get("attribution") or "",
                })
                if len(out) >= per_page:
                    break
            return out
        except Exception as e:
            logger.debug(f"[WebImage] Openverse lỗi '{query}': {e}")
            return []

    # ─────────────────────────── Wikimedia Commons ───────────────────────────
    def search_wikimedia_commons(self, query: str, per_page: int = 6) -> List[Dict]:
        """Tìm ảnh tài liệu trên Wikimedia Commons."""
        try:
            res = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": f"filetype:bitmap {query}",
                    "gsrnamespace": 6,
                    "gsrlimit": 20,
                    "prop": "imageinfo",
                    "iiprop": "url|size|mime|extmetadata",
                    "iiurlwidth": 1280,
                    "iiextmetadatafilter": "LicenseShortName|Artist|ImageDescription",
                    "format": "json",
                },
                headers={"User-Agent": WIKIMEDIA_USER_AGENT},
                timeout=12,
            )
            if res.status_code != 200:
                return []
            pages = res.json().get("query", {}).get("pages", {})
            out = []
            for p in sorted(pages.values(), key=lambda x: x.get("index", 999)):
                ii = (p.get("imageinfo") or [{}])[0]
                title = p.get("title", "")
                mime = ii.get("mime", "")
                w, h = ii.get("width") or 0, ii.get("height") or 0
                full = ii.get("thumburl") or ii.get("url")
                if not full or not mime.startswith("image/") or "svg" in mime or "gif" in mime:
                    continue
                if w < 600 or h < 600 or not _aspect_ok(w, h):
                    continue
                if any(b in title.lower() for b in _COMMONS_BLOCKLIST):
                    continue
                meta = ii.get("extmetadata", {})
                lic = (meta.get("LicenseShortName", {}) or {}).get("value", "") or "Wikimedia Commons"
                artist = _strip_html((meta.get("Artist", {}) or {}).get("value", ""))
                desc = _strip_html((meta.get("ImageDescription", {}) or {}).get("value", ""))[:160]
                thumb = full.replace("/1280px-", "/400px-") if "/1280px-" in full else full
                clean_title = title.replace("File:", "").rsplit(".", 1)[0]
                out.append({
                    "url": full,
                    "thumbnail": thumb,
                    "title": clean_title,
                    "alt": desc or clean_title,
                    "width": ii.get("thumbwidth") or w,
                    "height": ii.get("thumbheight") or h,
                    "source": "wikimedia_commons",
                    "provider": "wikimedia",
                    "license": lic,
                    "creator": artist,
                    "page_url": ii.get("descriptionurl", ""),
                    "attribution": f'"{clean_title}" by {artist or "unknown"} ({lic}), via Wikimedia Commons',
                })
                if len(out) >= per_page:
                    break
            return out
        except Exception as e:
            logger.debug(f"[WebImage] Commons lỗi '{query}': {e}")
            return []

    # ──────────────────────────────── Unsplash ───────────────────────────────
    def search_unsplash(self, query: str, per_page: int = 6) -> List[Dict]:
        if not self._unsplash_key:
            return []
        try:
            res = requests.get(
                "https://api.unsplash.com/search/photos",
                params={"query": query, "per_page": per_page, "orientation": "portrait", "content_filter": "high"},
                headers={"Authorization": f"Client-ID {self._unsplash_key}", "Accept-Version": "v1"},
                timeout=10,
            )
            if res.status_code != 200:
                logger.debug(f"[WebImage] Unsplash HTTP {res.status_code}")
                return []
            out = []
            for r in res.json().get("results", []):
                urls = r.get("urls", {})
                raw = urls.get("raw")
                if not raw:
                    continue
                user = (r.get("user") or {}).get("name", "")
                alt = r.get("alt_description") or r.get("description") or query
                out.append({
                    "url": f"{raw}&w=1080&h=1920&fit=crop&q=85",
                    "thumbnail": urls.get("small") or urls.get("thumb"),
                    "title": alt,
                    "alt": alt,
                    "width": 1080,
                    "height": 1920,
                    "source": "unsplash",
                    "provider": "unsplash",
                    "license": "Unsplash License",
                    "creator": user,
                    "page_url": (r.get("links") or {}).get("html", ""),
                    "attribution": f"Photo by {user} on Unsplash",
                    "download_location": (r.get("links") or {}).get("download_location", ""),
                })
            return out
        except Exception as e:
            logger.debug(f"[WebImage] Unsplash lỗi '{query}': {e}")
            return []

    def track_unsplash_download(self, item: Dict) -> None:
        """Theo hướng dẫn API Unsplash: báo lượt tải khi ảnh thực sự được dùng."""
        loc = item.get("download_location")
        if not loc or not self._unsplash_key:
            return
        try:
            requests.get(loc, headers={"Authorization": f"Client-ID {self._unsplash_key}"}, timeout=5)
        except Exception:
            pass

    # ─────────────────────────────── Tổng hợp ────────────────────────────────
    def search_all(self, query: str, per_source: int = 5) -> List[Dict]:
        """Gọi song song mọi nguồn ảnh trên toàn bộ mạng Internet, tổng hợp và ưu tiên ảnh chân thực."""
        if not query or not web_image_search_enabled():
            return []

        jobs = [
            (self.search_open_web, "open_web"),
            (self.search_wikipedia_images, "wikipedia"),
            (self.search_wikimedia_commons, "commons"),
            (self.search_openverse, "openverse"),
        ]
        if self._unsplash_key:
            jobs.append((self.search_unsplash, "unsplash"))

        with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
            futures = [ex.submit(fn, query, per_source) for fn, _ in jobs]
            buckets = []
            for f in futures:
                try:
                    buckets.append(f.result(timeout=15))
                except Exception:
                    buckets.append([])

        merged, seen = [], set()
        # Ưu tiên xen kẽ: Open Web và Wikipedia lên đầu danh sách ứng viên
        for i in range(max((len(b) for b in buckets), default=0)):
            for b in buckets:
                if i < len(b) and b[i]["url"] not in seen:
                    seen.add(b[i]["url"])
                    merged.append(b[i])

        summary_parts = [f"{name}={len(b)}" for (_, name), b in zip(jobs, buckets)]
        logger.info(f"[WebImage] 🌐 Tìm ảnh mạng cho '{query}': {len(merged)} ảnh ({', '.join(summary_parts)})")
        return merged


def record_image_credit(item: Dict, query: str = "") -> None:
    """Ghi credit ảnh có giấy phép vào storage/cache/image_credits.jsonl."""
    if not item:
        return
    try:
        _CREDITS_FILE.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "query": query,
            "source": item.get("source"),
            "license": item.get("license", "Fair Use"),
            "creator": item.get("creator", "Unknown"),
            "page_url": item.get("page_url", ""),
            "attribution": item.get("attribution", ""),
            "url": item.get("url"),
        }
        with _credits_lock, open(_CREDITS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug(f"[WebImage] Không ghi được credit: {e}")


def _strip_html(s: str) -> str:
    import re
    import html as _html
    return _html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()
