"""
app.services.media.web_image_fetcher — Tìm ảnh trên Internet CÓ GIẤY PHÉP dùng thương mại (an toàn cho YouTube kiếm tiền).

Nguồn (đều là API chính thức, không scrape):
1. Openverse (WordPress Foundation): ~800 triệu ảnh Creative Commons gom từ Flickr, Rawpixel, StockSnap,
   bảo tàng, Wikimedia... Lọc license_type=commercial,modification (được dùng thương mại + được cắt/zoom).
2. Wikimedia Commons: ~100 triệu ảnh tự do (CC BY / CC BY-SA / Public Domain), cực mạnh về địa danh,
   lịch sử, động vật, thiên nhiên.
3. Unsplash (tùy chọn, cần UNSPLASH_ACCESS_KEY): ảnh nghệ thuật chất lượng cao, giấy phép Unsplash miễn phí thương mại.

Vì sao KHÔNG scrape Google/Bing Images: phần lớn ảnh có bản quyền → nguy cơ gậy bản quyền / Content ID /
bị từ chối kiếm tiền. Bộ lọc giấy phép của Bing khi scrape trả về kết quả sai (đã kiểm chứng).

Mọi ảnh trả về đều kèm `license`, `creator`, `page_url`, `attribution` để ghi credit vào mô tả video.
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

_UA = "AIYouTubeStudio/2.0 (licensed media fetcher)"
_CREDITS_FILE = Path("storage/cache/image_credits.jsonl")
_credits_lock = threading.Lock()

# Tên file Commons chứa các từ này thường là bản đồ / biểu tượng / tài liệu — không phải ảnh minh họa đẹp
_COMMONS_BLOCKLIST = (
    " map", "map_", "_map", "map.", "diagram", "chart", "logo", "icon", "symbol", "flag_of", "flag of",
    "coat_of_arms", "coat of arms", "emblem", "seal_of", "seal of", "stamp", "banknote", "coin",
    "schematic", "blueprint", "floor plan", "infographic", "screenshot", "manuscript", "signature",
)


def web_image_search_enabled() -> bool:
    return os.getenv("ENABLE_WEB_IMAGE_SEARCH", "true").strip().lower() not in ("0", "false", "no", "off")


def _aspect_ok(w: int, h: int, max_aspect: float = 2.2) -> bool:
    if not w or not h:
        return True
    return max(w / h, h / w) <= max_aspect


class WebImageFetcher:
    """Tìm ảnh Internet có giấy phép tự do từ nhiều nguồn chính thức, chạy song song."""

    def __init__(self):
        self._unsplash_key = os.getenv("UNSPLASH_ACCESS_KEY", "").strip()

    # ─────────────────────────────── Openverse ───────────────────────────────
    def search_openverse(self, query: str, per_page: int = 8) -> List[Dict]:
        try:
            res = requests.get(
                "https://api.openverse.org/v1/images/",
                params={
                    "q": query,
                    "page_size": 20,
                    "license_type": "commercial,modification",
                    "mature": "false",
                },
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
                if not url or (w and w < 800) or (h and h < 800) or not _aspect_ok(w, h):
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
                    "license": lic,
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
            # Giữ thứ tự xếp hạng tìm kiếm của Commons
            for p in sorted(pages.values(), key=lambda x: x.get("index", 999)):
                ii = (p.get("imageinfo") or [{}])[0]
                title = p.get("title", "")
                mime = ii.get("mime", "")
                w, h = ii.get("width") or 0, ii.get("height") or 0
                full = ii.get("thumburl") or ii.get("url")
                if not full or not mime.startswith("image/") or "svg" in mime or "gif" in mime:
                    continue
                if w < 900 or h < 900 or not _aspect_ok(w, h):
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
        """Gọi song song mọi nguồn, xen kẽ kết quả để AI Vision có danh sách đa dạng."""
        if not query or not web_image_search_enabled():
            return []
        jobs = [self.search_openverse, self.search_wikimedia_commons]
        if self._unsplash_key:
            jobs.insert(0, self.search_unsplash)
        with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
            futures = [ex.submit(fn, query, per_source) for fn in jobs]
            buckets = []
            for f in futures:
                try:
                    buckets.append(f.result(timeout=15))
                except Exception:
                    buckets.append([])
        merged, seen = [], set()
        for i in range(max((len(b) for b in buckets), default=0)):
            for b in buckets:
                if i < len(b) and b[i]["url"] not in seen:
                    seen.add(b[i]["url"])
                    merged.append(b[i])
        logger.info(
            f"[WebImage] '{query}': "
            + ", ".join(f"{fn.__name__.replace('search_', '')}={len(b)}" for fn, b in zip(jobs, buckets))
        )
        return merged


def record_image_credit(item: Dict, query: str = "") -> None:
    """Ghi credit ảnh có giấy phép (CC BY yêu cầu ghi nguồn) vào storage/cache/image_credits.jsonl."""
    if not item or not item.get("license"):
        return
    try:
        _CREDITS_FILE.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "query": query,
            "source": item.get("source"),
            "license": item.get("license"),
            "creator": item.get("creator"),
            "page_url": item.get("page_url"),
            "attribution": item.get("attribution"),
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
