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

    # ─────────────────────────────── Open Web (Bing trực tiếp + DDGS) ───────────────────────────
    _engine_block_until: Dict[str, float] = {}

    @classmethod
    def _engine_ok(cls, name: str) -> bool:
        import time as _t
        return _t.time() >= cls._engine_block_until.get(name, 0)

    @classmethod
    def _engine_fail(cls, name: str, minutes: int = 2) -> None:
        import time as _t
        cls._engine_block_until[name] = _t.time() + minutes * 60
        logger.warning(f"[WebImage] Nguồn '{name}' lỗi/timeout → tạm ngắt {minutes} phút, dùng nguồn khác.")

    @staticmethod
    def _raw_bing_images(query: str, limit: int = 35) -> List[Dict]:
        """Bing Images qua endpoint async (HTML) — ưu tiên ảnh độ phân giải lớn (imagesize-large), trả kèm kích thước & thumbnail CDN."""
        import html as _html
        import re as _re
        from urllib.parse import urlparse
        res = requests.get(
            "https://www.bing.com/images/async",
            params={
                "q": query,
                "first": 0,
                "count": min(limit, 35),
                "mmasync": 1,
                "adlt": "moderate",
                "qft": "+filterui:imagesize-large",
            },
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=10,
        )
        if res.status_code != 200:
            raise RuntimeError(f"Bing HTTP {res.status_code}")
        out = []
        for chunk in res.text.split('class="iusc"')[1:]:
            m = _re.search(r'\bm="(\{[^"]+\})"', chunk)
            if not m:
                continue
            try:
                d = json.loads(_html.unescape(m.group(1)))
            except Exception:
                continue
            size = _re.search(r'nowrap[^>]*>\s*(\d+)\s*(?:x|×|&#215;)\s*(\d+)', chunk)
            purl = d.get("purl") or ""
            out.append({
                "image": d.get("murl"),
                "thumbnail": d.get("turl"),
                "title": _html.unescape(d.get("t") or ""),
                "width": int(size.group(1)) if size else 0,
                "height": int(size.group(2)) if size else 0,
                "source": urlparse(purl).netloc.replace("www.", "") if purl else "web",
                "url": purl,
            })
        return out

    @staticmethod
    def _raw_ddgs_images(query: str, limit: int = 15) -> List[Dict]:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        return list(DDGS(timeout=8).images(query, region="wt-wt", safesearch="moderate", max_results=limit))

    @staticmethod
    def _raw_serper_images(query: str, limit: int = 20) -> List[Dict]:
        """Google Images qua Serper.dev (API chính thức, cần SERPER_API_KEY) — ổn định, không bị chặn như cào web."""
        key = os.getenv("SERPER_API_KEY", "").strip()
        if not key:
            return []
        res = requests.post(
            "https://google.serper.dev/images",
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json={"q": query, "num": min(limit, 30)},
            timeout=12,
        )
        if res.status_code != 200:
            raise RuntimeError(f"Serper HTTP {res.status_code}: {res.text[:120]}")
        out = []
        for r in res.json().get("images", []):
            out.append({
                "image": r.get("imageUrl"),
                "thumbnail": r.get("thumbnailUrl") or r.get("imageUrl"),
                "title": r.get("title") or "",
                "width": r.get("imageWidth") or 0,
                "height": r.get("imageHeight") or 0,
                "source": r.get("domain") or r.get("source") or "web",
                "url": r.get("link") or "",
            })
        return out

    @staticmethod
    def _raw_google_images(query: str, limit: int = 35) -> List[Dict]:
        """
        Tìm kiếm trực tiếp trên Google Images (chế độ ảnh lớn tbs=isz:l) như thanh tìm kiếm Google/Gemini:
        - Ưu tiên Google Custom Search API nếu có GOOGLE_SEARCH_API_KEY + GOOGLE_SEARCH_CX.
        - Trích xuất trực tiếp từ dữ liệu nhúng AF_initDataCallback của Google Images:
          + URL ảnh gốc độ phân giải cao (full-res original image URL) kèm kích thước thực (width, height).
          + URL thumbnail trên máy chủ Google (encrypted-tbn0.gstatic.com) để Gemini Vision luôn đọc được 100% không bị chặn 403/Cloudflare.
          + Tiêu đề bài viết gốc và tên miền nguồn (source domain, page_url) để chấm điểm ngữ cảnh & gửi Referer khi tải.
        """
        import html as _html
        import re as _re
        from urllib.parse import urlparse

        out: List[Dict] = []
        seen_urls = set()

        # 1. Nếu có Google Custom Search API Key chính thức thì lấy thêm từ API
        g_key = os.getenv("GOOGLE_SEARCH_API_KEY", "").strip()
        g_cx = os.getenv("GOOGLE_SEARCH_CX", "").strip()
        if g_key and g_cx and g_key != "your_google_search_api_key":
            try:
                res_api = requests.get(
                    "https://www.googleapis.com/customsearch/v1",
                    params={
                        "key": g_key,
                        "cx": g_cx,
                        "q": query,
                        "searchType": "image",
                        "imgSize": "xlarge",
                        "num": min(limit, 10),
                        "safe": "active",
                    },
                    timeout=8,
                )
                if res_api.status_code == 200:
                    for item in res_api.json().get("items", []):
                        link = item.get("link")
                        if not link or link in seen_urls:
                            continue
                        seen_urls.add(link)
                        img_meta = item.get("image") or {}
                        ctx_link = img_meta.get("contextLink") or item.get("displayLink") or ""
                        out.append({
                            "image": link,
                            "thumbnail": img_meta.get("thumbnailLink") or link,
                            "title": item.get("title") or item.get("snippet") or query,
                            "width": int(img_meta.get("width") or 1280),
                            "height": int(img_meta.get("height") or 720),
                            "source": (item.get("displayLink") or "google").replace("www.", ""),
                            "url": ctx_link,
                        })
            except Exception as e:
                logger.debug(f"[WebImage] Google CSE fallback: {e}")

        # 2. Truy vấn trực tiếp Google Images (tbm=isch & tbs=isz:l ưu tiên ảnh độ phân giải lớn)
        headers = {
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cookie": "CONSENT=YES+cb.20230531-04-p0.en+FX+908;",
        }
        res = requests.get(
            "https://www.google.com/search",
            params={
                "q": query,
                "tbm": "isch",
                "tbs": "isz:l",
                "hl": "en",
                "gl": "us",
                "safe": "active",
                "ijn": "0",
            },
            headers=headers,
            timeout=10,
        )
        if res.status_code != 200:
            if out:
                return out
            raise RuntimeError(f"Google Images HTTP {res.status_code}")

        # Giải mã các ký tự escape trong khối dữ liệu JSON nhúng của Google Images
        text = (
            res.text
            .replace("\\u003d", "=")
            .replace("\\u0026", "&")
            .replace("\\u003c", "<")
            .replace("\\u003e", ">")
            .replace("\\/", "/")
            .replace('\\"', '"')
        )

        # Mẫu dữ liệu chuẩn trong AF_initDataCallback của Google Images:
        # ["https://encrypted-tbn0.gstatic.com/images?q=tbn:...", thumb_h, thumb_w], ["https://full-res-url...", orig_h, orig_w]
        pair_pattern = _re.compile(
            r'\[\s*"(https://encrypted-tbn0\.gstatic\.com/images\?[^"]+)"\s*,\s*(\d+)\s*,\s*(\d+)\s*\]'
            r'\s*,\s*\[\s*"(https?://(?!encrypted-tbn0\.gstatic\.com)[^"\s]+)"\s*,\s*(\d+)\s*,\s*(\d+)\s*\]'
        )
        meta_2003_pattern = _re.compile(
            r'"2003"\s*:\s*\[\s*null\s*,\s*"[^"]*"\s*,\s*"(https?://[^"]+)"\s*,\s*"([^"]{2,220})"'
        )

        for m in pair_pattern.finditer(text):
            thumb_url = m.group(1)
            orig_url = m.group(4)
            # Lưu ý: Google Images lưu [url, height, width] (chiều cao trước, chiều rộng sau)
            orig_h = int(m.group(5) or 0)
            orig_w = int(m.group(6) or 0)

            if not orig_url or orig_url in seen_urls:
                continue
            low_u = orig_url.lower()
            if any(bad in low_u for bad in ("gstatic.com", "google.com/images", "googleapis.com", "favicon", ".svg", ".gif", ".ico")):
                continue
            seen_urls.add(orig_url)

            # Tìm thông tin trang đích & tiêu đề ngay phía sau cặp ảnh trong cấu trúc cây JSON của Google
            tail = text[m.end(): m.end() + 1400]
            page_url = ""
            title = ""
            m_meta = meta_2003_pattern.search(tail)
            if m_meta:
                page_url = m_meta.group(1)
                title = _html.unescape(m_meta.group(2))
            else:
                # Fallback tìm cặp (page_url, title) gần nhất trong phạm vi 900 ký tự
                m_alt = _re.search(r'"(https?://(?!encrypted-tbn0\.gstatic\.com)[^"]+)"\s*,\s*"([^"\n]{4,180})"', tail[:900])
                if m_alt:
                    page_url = m_alt.group(1)
                    title = _html.unescape(m_alt.group(2))

            domain = urlparse(page_url or orig_url).netloc.replace("www.", "") or "google_web"
            out.append({
                "image": orig_url,
                "thumbnail": thumb_url or orig_url,
                "title": title or query,
                "width": orig_w or 1280,
                "height": orig_h or 720,
                "source": domain,
                "url": page_url,
                "engine": "google_images",
            })
            if len(out) >= limit:
                break

        if not out and "/httpservice/retry/enablejs" in res.text:
            raise RuntimeError("Google Images yêu cầu JS challenge (chuyển sang Serper/Bing/DDGS)")
        return out

    @staticmethod
    def _looks_relevant(raw: List[Dict], query: str) -> bool:
        """
        Nguồn cào miễn phí khi bị giới hạn thường trả kết quả RÁC (vd: tìm "kraken" ra xe máy / nước ngọt).
        Nếu < 25% tiêu đề chứa từ khóa chính của truy vấn → coi như nguồn đang hỏng.
        """
        from app.services.media.image_quality import subject_terms
        generic = {"concept", "art", "movie", "scene", "dark", "fantasy", "close", "photo", "photograph",
                   "historical", "the", "giant", "attacking", "hunting", "soaring", "flying", "eye", "eyes",
                   "high", "resolution", "real", "authentic", "documentary"}
        terms = [t for t in subject_terms(query) if t not in generic]
        if not terms or not raw:
            return bool(raw)
        sample = raw[:15]
        hits = sum(
            1 for r in sample
            if any(
                t in f"{r.get('title') or ''} {r.get('url') or ''} {r.get('image') or ''}".lower().replace("-", " ")
                for t in terms
            )
        )
        return hits / len(sample) >= 0.25

    def search_open_web(self, query: str, per_page: int = 8) -> List[Dict]:
        """
        Tìm kiếm ảnh thực tế từ toàn bộ mạng Internet với ưu tiên số 1 là Google Images (như Gemini trên Google Search),
        kết hợp Serper / Bing / DuckDuckGo.
        Thứ tự nguồn: OPEN_WEB_ENGINES (mặc định "google,serper,bing,ddgs").
        """
        from app.services.media.image_quality import is_watermark_risk

        out: List[Dict] = []
        seen = set()
        engines = [e.strip() for e in os.getenv("OPEN_WEB_ENGINES", "google,serper,bing,ddgs").split(",") if e.strip()]

        for eng in engines:
            if not self._engine_ok(eng):
                continue
            if eng == "serper" and not os.getenv("SERPER_API_KEY", "").strip():
                continue
            try:
                raw_results: List[Dict] = []
                if eng == "google":
                    raw_results = self._raw_google_images(query, limit=max(per_page * 4, 30))
                elif eng == "serper":
                    raw_results = self._raw_serper_images(query, limit=25)
                elif eng == "bing":
                    raw_results = self._raw_bing_images(query, limit=35)
                elif eng == "ddgs":
                    raw_results = self._raw_ddgs_images(query, limit=max(per_page * 3, 15))

                if raw_results and not self._looks_relevant(raw_results, query):
                    logger.info(f"[WebImage] {eng} trả kết quả ít liên quan cho '{query}' → thử nguồn kế tiếp")
                    continue

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
                    engine_tag = "google_images" if eng in ("google", "serper") else "open_web"

                    item = {
                        "url": img_url,
                        "thumbnail": r.get("thumbnail") or img_url,
                        "fallback_url": r.get("thumbnail") if (r.get("thumbnail") and "gstatic.com" in str(r.get("thumbnail"))) else "",
                        "title": title,
                        "alt": title,
                        "width": w,
                        "height": h,
                        "source": engine_tag,
                        "provider": eng,
                        "license": "Web Public (Fair Use)",
                        "creator": source_site,
                        "page_url": r.get("url") or "",
                        "attribution": f"Web image from {source_site} (via {eng})",
                    }
                    # Bỏ ảnh từ trang stock trả phí / bán tranh in (luôn dính watermark) và ảnh quá nhỏ
                    if is_watermark_risk(item) or min(w, h) < 450:
                        continue
                    out.append(item)
                    if len(out) >= per_page:
                        break

                if len(out) >= max(4, per_page // 2):
                    break
            except Exception as e:
                logger.debug(f"[WebImage] {eng} lỗi cho '{query}': {e}")
                self._engine_fail(eng, minutes=1 if eng == "google" else 2)

        return out

    # ─────────────────────────────── Wikipedia Page Images ─────────────────────
    def search_wikipedia_images(self, query: str, per_page: int = 4) -> List[Dict]:
        """
        Tìm ảnh trực tiếp từ các bài viết Wikipedia liên quan đến chủ đề/thực thể.
        Đặc biệt hiệu quả với nhân vật, sự kiện lịch sử, địa danh, sinh vật và thí nghiệm khoa học.

        query nên là TÊN THỰC THỂ (vd: "wedge-tailed eagle"). Tra cứu đúng tiêu đề bài trước,
        sau đó mới full-text search và chỉ giữ bài có tiêu đề khớp từ khóa (tránh kết quả rác kiểu
        "eagle flying sky" → "San Antonio Airport", "Area 51").
        """
        try:
            from app.services.media.image_quality import subject_terms
            url = "https://en.wikipedia.org/w/api.php"
            terms = subject_terms(query)
            base = {
                "action": "query",
                "prop": "pageimages",
                "piprop": "original|thumbnail",
                "pithumbsize": 1280,
                "format": "json",
                "redirects": 1,
            }
            pages: List[Dict] = []
            # 1. Tra đúng tiêu đề bài (kèm redirect: "mantis shrimp" → "Mantis shrimp")
            res = requests.get(url, params={**base, "titles": query.strip()[:250]},
                               headers={"User-Agent": WIKIMEDIA_USER_AGENT}, timeout=10)
            if res.status_code == 200:
                for p in res.json().get("query", {}).get("pages", {}).values():
                    if "missing" not in p and (p.get("original") or p.get("thumbnail")):
                        pages.append({**p, "index": 0})
            # 2. Full-text search, chỉ giữ bài có tiêu đề chứa từ khóa chủ thể
            if len(pages) < per_page:
                res = requests.get(url, params={
                    **base, "generator": "search", "gsrsearch": query, "gsrlimit": max(per_page * 2, 6),
                }, headers={"User-Agent": WIKIMEDIA_USER_AGENT}, timeout=10)
                if res.status_code == 200:
                    need = max(1, -(-len(terms) * 2 // 3)) if terms else 0  # ≥ 2/3 số từ khóa chủ thể
                    for p in res.json().get("query", {}).get("pages", {}).values():
                        t = (p.get("title") or "").lower().replace("-", " ")
                        if terms and sum(1 for term in terms if term in t) < need:
                            continue
                        pages.append(p)
            out = []
            seen = set()
            for p in sorted(pages, key=lambda x: x.get("index", 999)):
                orig = p.get("original", {})
                thumb = p.get("thumbnail", {})
                img_url = orig.get("source") or thumb.get("source")
                if not img_url or img_url in seen:
                    continue
                if str(img_url).lower().split("?")[0].endswith((".svg", ".gif", ".tif", ".tiff")):
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
    def search_all(
        self,
        query: str,
        per_source: int = 5,
        subject: Optional[str] = None,
        extra_queries: Optional[List[str]] = None,
        creature_mode: bool = False,
    ) -> List[Dict]:
        """
        Gọi song song mọi nguồn ảnh trên toàn bộ mạng Internet, tổng hợp và ưu tiên ảnh chân thực.

        subject: tên chủ thể ngắn (vd: "wedge-tailed eagle"). Wikipedia / Commons / Openverse khớp từ khóa
        kiểu AND nên dùng tên chủ thể sẽ chính xác hơn nhiều so với truy vấn mô tả dài.
        """
        if not query or not web_image_search_enabled():
            return []

        subject = (subject or "").strip()
        entity_q = subject or query
        jobs = [
            (self.search_open_web, "google_web", query),
            (self.search_wikimedia_commons, "commons", entity_q),
            (self.search_openverse, "openverse", entity_q),
        ]
        # Wikipedia chỉ hữu ích khi tra theo TÊN THỰC THỂ; truy vấn mô tả dài trả về bài rác
        if subject or len(query.split()) <= 3:
            jobs.insert(1, (self.search_wikipedia_images, "wikipedia", entity_q))
        if self._unsplash_key:
            jobs.append((self.search_unsplash, "unsplash", query))

        # Tự động bổ sung truy vấn Google Search chuẩn Gemini (neo chặt chủ thể + nhiếp ảnh thực tế)
        auto_extra = list(extra_queries or [])
        if not auto_extra and subject and subject.lower() != query.lower():
            auto_extra.append(f'"{subject}" real photograph')
        for i, xq in enumerate(q for q in auto_extra[:2] if q and q != query):
            jobs.append((self.search_open_web, f"google_web+{i + 1}", xq))

        with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
            futures = [ex.submit(fn, q, per_source) for fn, _, q in jobs]
            buckets = []
            for f in futures:
                try:
                    buckets.append(f.result(timeout=15))
                except Exception:
                    buckets.append([])

        # Open web quá ít kết quả với truy vấn dài → thử lại bằng tên chủ thể
        if subject and subject.lower() != query.lower() and len(buckets[0]) < 3:
            try:
                buckets[0] = buckets[0] + self.search_open_web(subject, per_source)
            except Exception:
                pass

        merged, seen = [], set()
        # Ưu tiên xen kẽ: Google Web và Wikipedia lên đầu danh sách ứng viên
        for i in range(max((len(b) for b in buckets), default=0)):
            for b in buckets:
                if i < len(b) and b[i]["url"] not in seen:
                    seen.add(b[i]["url"])
                    merged.append(b[i])

        # Lọc watermark / ảnh nhỏ và xếp hạng theo chủ thể + bố cục dọc + độ phân giải
        try:
            from app.services.media.image_quality import prefilter_candidates
            merged = prefilter_candidates(
                merged, subject=subject or query, limit=max(12, per_source * 3), creature_mode=creature_mode,
                max_per_source=8 if creature_mode else 7,
            )
        except Exception as e:
            logger.debug(f"[WebImage] prefilter lỗi: {e}")

        summary_parts = [f"{name}={len(b)}" for (_, name, _), b in zip(jobs, buckets)]
        logger.info(
            f"[WebImage] 🌐 Tìm ảnh mạng cho '{query}' (subject='{subject}'): {len(merged)} ảnh ({', '.join(summary_parts)})"
        )
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
