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
    return os.getenv("BYPASS_IMAGE_COPYRIGHT", "false").strip().lower() not in ("0", "false", "no", "off")


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
    _engine_fail_streak: Dict[str, int] = {}
    _open_engine_cache: Dict[str, List[Dict]] = {}
    _cache_lock = threading.Lock()

    @classmethod
    def _engine_ok(cls, name: str) -> bool:
        import time as _t
        return _t.time() >= cls._engine_block_until.get(name, 0)

    @classmethod
    def _engine_fail(cls, name: str, minutes: float = 0.25) -> None:
        import time as _t
        streak = cls._engine_fail_streak.get(name, 0) + 1
        cls._engine_fail_streak[name] = streak
        if name == "google" and streak < 4:
            return
        cls._engine_block_until[name] = _t.time() + minutes * 60
        logger.warning(f"[WebImage] Nguồn '{name}' lỗi/timeout ({streak} lần) → tạm ngắt {int(minutes * 60)}s, dùng nguồn khác.")

    @staticmethod
    def _concise_art_query(query: str, max_words: int = 3) -> str:
        """Rút gọn truy vấn dài thành 2-3 từ khóa chủ thể cốt lõi cho ArtStation / Wallhaven API."""
        import re as _re
        fluff = {
            "cinematic", "8k", "4k", "concept", "art", "artstation", "movie", "scene", "cgi",
            "still", "dramatic", "hd", "stunning", "viral", "high", "resolution", "wallpaper",
            "photography", "photo", "terrifying", "scary", "creepy", "nightmare", "thalassophobia",
            "megalophobia", "eldritch", "unreal", "engine", "render", "masterpiece", "hyperrealistic",
            "photorealistic", "vertical", "the", "and", "with", "from", "into", "under", "over",
            "chiaroscuro", "noir", "monochrome", "seinen", "thriller", "aesthetic", "portrait",
            "shadow", "shadows", "silhouette", "lighting", "realistic", "shot", "frame", "digital",
            "illustration", "psychological", "manipulation", "manipulative",
        }
        words = [
            w for w in _re.findall(r"[A-Za-z0-9]+", (query or "").lower())
            if len(w) > 2 and w not in fluff
        ]
        if not words:
            return (query or "").strip()
        return " ".join(words[:max_words])

    @staticmethod
    def _get_primp_client(timeout: int = 9):
        """Khởi tạo primp Client giả lập TLS Chrome chuẩn xác (tương thích cả primp 1.x và 2.x)."""
        import primp
        for imp in ("chrome", "edge", None):
            try:
                return primp.Client(impersonate=imp, timeout=timeout) if imp else primp.Client(timeout=timeout)
            except Exception:
                continue
        return primp.Client(timeout=timeout)

    @classmethod
    def _raw_bing_images(cls, query: str, limit: int = 35) -> List[Dict]:
        """Bing Images qua endpoint async (HTML) — dùng primp (Chrome TLS fingerprint) + ưu tiên ảnh độ phân giải lớn (imagesize-large)."""
        import html as _html
        import re as _re
        from urllib.parse import urlparse
        safe_mode = "off" if bypass_copyright_enabled() else "moderate"

        def _parse_bing_html(html_text: str) -> List[Dict]:
            parsed: List[Dict] = []
            seen_m = set()
            for chunk in html_text.split("iusc")[1:]:
                m = _re.search(r'\bm="(\{[^"]+\})"', chunk)
                if not m:
                    continue
                try:
                    d = json.loads(_html.unescape(m.group(1)))
                except Exception:
                    continue
                size = _re.search(r'nowrap[^>]*>\s*(\d+)\s*(?:x|×|&#215;)\s*(\d+)', chunk)
                purl = d.get("purl") or ""
                murl = d.get("murl") or ""
                turl = d.get("turl") or ""
                if not murl or murl in seen_m:
                    continue
                seen_m.add(murl)
                hi_turl = f"{turl}&w=1080&h=1920&c=7&rs=1&pid=ImgDetMain" if (turl and "bing.net" in turl and "&w=" not in turl) else turl
                parsed.append({
                    "image": murl,
                    "thumbnail": turl or murl,
                    "fallback_url": hi_turl or turl,
                    "title": _html.unescape(d.get("t") or ""),
                    "width": int(size.group(1)) if size else 1080,
                    "height": int(size.group(2)) if size else 1920,
                    "source": urlparse(purl).netloc.replace("www.", "") if purl else "bing_web",
                    "url": purl,
                    "engine": "bing",
                })
            return parsed

        out: List[Dict] = []
        base_params = {
            "q": query,
            "first": "0",
            "count": str(min(limit, 35)),
            "mmasync": "1",
        }
        try:
            client = cls._get_primp_client(timeout=9)
            # Ưu tiên lọc ảnh kích thước lớn (large HD) như Google tbs=isz:l
            r_large = client.get(
                "https://www.bing.com/images/async",
                params={**base_params, "qft": "+filterui:imagesize-large"},
            )
            if r_large.status_code == 200 and "iusc" in r_large.text:
                out.extend(_parse_bing_html(r_large.text))
            if len(out) < 10:
                r_all = client.get("https://www.bing.com/images/async", params=base_params)
                if r_all.status_code == 200 and "iusc" in r_all.text:
                    seen_u = {x["image"] for x in out}
                    for item in _parse_bing_html(r_all.text):
                        if item["image"] not in seen_u:
                            seen_u.add(item["image"])
                            out.append(item)
        except Exception:
            pass

        if not out:
            res = requests.get(
                "https://www.bing.com/images/async",
                params={**base_params, "adlt": safe_mode},
                headers={
                    "User-Agent": _UA,
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=9,
            )
            if res.status_code != 200:
                raise RuntimeError(f"Bing HTTP {res.status_code}")
            out = _parse_bing_html(res.text)
        return out[:limit]

    @staticmethod
    def _raw_ddgs_images(query: str, limit: int = 20) -> List[Dict]:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        safe_mode = "off" if bypass_copyright_enabled() else "moderate"
        raw = list(DDGS(timeout=8).images(query, region="us-en", safesearch=safe_mode, max_results=limit))
        for r in raw:
            turl = r.get("thumbnail") or ""
            if turl and not r.get("fallback_url"):
                hi_turl = (
                    f"{turl}&w=1080&h=1920&c=7&rs=1"
                    if ("bing.net" in turl and "&w=" not in turl)
                    else turl
                )
                r["fallback_url"] = hi_turl
            r["engine"] = "ddgs"
        return raw

    @classmethod
    def _raw_artstation_images(cls, query: str, limit: int = 18) -> List[Dict]:
        """ArtStation API v2 — Kho tác phẩm 3D Render, Concept Art 8K, Dark Fantasy, Quái vật CGI đỉnh cao thế giới (dùng primp vượt Cloudflare)."""
        import re as _re
        short_q = cls._concise_art_query(query, max_words=3)
        if not short_q:
            return []
        headers = {
            "User-Agent": _UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.artstation.com/search",
        }
        data = []
        try:
            client = cls._get_primp_client(timeout=8)
            r_p = client.get(
                "https://www.artstation.com/api/v2/search/projects.json",
                params={"query": short_q, "page": 1, "per_page": min(limit, 24), "sorting": "relevance"},
                headers=headers,
            )
            if r_p.status_code == 200:
                data = r_p.json().get("data") or []
                if len(data) < 3 and len(short_q.split()) > 1:
                    core_q = cls._concise_art_query(query, max_words=2)
                    if core_q and core_q != short_q:
                        r_p2 = client.get(
                            "https://www.artstation.com/api/v2/search/projects.json",
                            params={"query": core_q, "page": 1, "per_page": min(limit, 24), "sorting": "relevance"},
                            headers=headers,
                        )
                        if r_p2.status_code == 200:
                            data = data + (r_p2.json().get("data") or [])
        except Exception:
            pass
        if not data:
            res = requests.get(
                "https://www.artstation.com/api/v2/search/projects.json",
                params={"query": short_q, "page": 1, "per_page": min(limit, 24), "sorting": "relevance"},
                headers=headers,
                timeout=8,
            )
            if res.status_code != 200:
                raise RuntimeError(f"ArtStation HTTP {res.status_code}")
            data = res.json().get("data") or []

        out: List[Dict] = []
        seen = set()
        for item in data:
            # Ưu tiên cover_url (không vuông) làm fallback_url để tránh viền mờ trên dưới của smaller_square (400x400)
            cover = item.get("cover_url") or item.get("smaller_square_cover_url") or ""
            if not cover or not cover.startswith("http"):
                continue
            medium_url = _re.sub(
                r"/(smaller_square|small|micro_square|square)/",
                "/medium/",
                cover,
                count=1,
            )
            full_url = _re.sub(
                r"/(smaller_square|medium|small|micro_square|square|\d+)/",
                "/large/",
                cover,
                count=1,
            )
            if full_url in seen:
                continue
            seen.add(full_url)
            title = (item.get("title") or short_q).strip()
            desc = (item.get("description") or "").strip()
            permalink = item.get("permalink") or item.get("url") or "https://www.artstation.com"
            art_blob = f"{title} {desc} {permalink}".lower()
            if any(
                bad_art in art_blob
                for bad_art in (
                    "minecraft", "voxel", "blockbench", "roblox", "fortnite", "pixel art",
                    "low poly", "low-poly", "chibi", "anime", "waifu", "genshin", "character sheet",
                    "concept sheet", "reference sheet", "turnaround", "subnautica", "stylized",
                )
            ):
                continue
            out.append({
                "image": full_url,
                "thumbnail": medium_url,
                "fallback_url": medium_url,
                "title": f"{title} - ArtStation 8K Concept Art ({short_q})",
                "width": 1920,
                "height": 1080,
                "source": "artstation.com",
                "url": permalink,
                "engine": "artstation",
            })
            if len(out) >= limit:
                break
        return out

    @classmethod
    def _raw_wallhaven_images(cls, query: str, limit: int = 16) -> List[Dict]:
        """Wallhaven API v1 — Kho hình nền 2K/4K/8K điện ảnh, siêu nét, không bao giờ có watermark."""
        short_q = cls._concise_art_query(query, max_words=2)
        if not short_q:
            return []
        res = requests.get(
            "https://wallhaven.cc/api/v1/search",
            params={"q": short_q, "sorting": "relevance", "purity": "100", "page": 1},
            headers={"User-Agent": _UA},
            timeout=8,
        )
        if res.status_code != 200:
            raise RuntimeError(f"Wallhaven HTTP {res.status_code}")
        data = res.json().get("data") or []
        if len(data) < 2 and len(short_q.split()) > 1:
            first_word = short_q.split()[0]
            if len(first_word) > 3 and first_word not in ("giant", "dark", "deep"):
                res2 = requests.get(
                    "https://wallhaven.cc/api/v1/search",
                    params={"q": first_word, "sorting": "relevance", "purity": "100", "page": 1},
                    headers={"User-Agent": _UA},
                    timeout=7,
                )
                if res2.status_code == 200:
                    data = data + (res2.json().get("data") or [])

        out: List[Dict] = []
        seen = set()
        for item in data:
            full_url = item.get("path") or ""
            if not full_url or full_url in seen:
                continue
            seen.add(full_url)
            thumbs = item.get("thumbs") or {}
            thumb_url = thumbs.get("large") or thumbs.get("original") or full_url
            w = int(item.get("dimension_x") or 1920)
            h = int(item.get("dimension_y") or 1080)
            out.append({
                "image": full_url,
                "thumbnail": thumb_url,
                "fallback_url": thumb_url,
                "title": f"{short_q} 4K Ultra HD Wallpaper ({item.get('category', 'digital art')})",
                "width": w,
                "height": h,
                "source": "wallhaven.cc",
                "url": item.get("url") or "https://wallhaven.cc",
                "engine": "wallhaven",
            })
            if len(out) >= limit:
                break
        return out

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
                "fallback_url": r.get("thumbnailUrl") or "",
                "title": r.get("title") or "",
                "width": r.get("imageWidth") or 0,
                "height": r.get("imageHeight") or 0,
                "source": r.get("domain") or r.get("source") or "web",
                "url": r.get("link") or "",
                "engine": "serper",
            })
        return out

    _CHROME_POOL_SIZE = 3
    _chrome_slot_locks = [threading.Lock() for _ in range(3)]
    _headless_chrome_sem = threading.BoundedSemaphore(2)
    _browser_exe_cache: Optional[str] = None

    @classmethod
    def _find_headless_browser(cls) -> Optional[str]:
        if cls._browser_exe_cache and os.path.exists(cls._browser_exe_cache):
            return cls._browser_exe_cache
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        for p in candidates:
            if os.path.exists(p):
                cls._browser_exe_cache = p
                return p
        return None

    @classmethod
    def _fetch_google_isch_dom_headless(cls, query: str, safe_mode: str = "off", tbs: str = "isz:l") -> str:
        """Lấy trực tiếp DOM kết quả Top Google Images bằng Chrome/Edge Headless (vượt JS challenge SG_SS trong ~1.8s)."""
        import subprocess
        from urllib.parse import urlencode

        browser_exe = cls._find_headless_browser()
        if not browser_exe:
            return ""

        params = {
            "q": query,
            "tbm": "isch",
            "hl": "en",
            "gl": "us",
            "safe": safe_mode,
        }
        if tbs:
            params["tbs"] = tbs
        target_url = f"https://www.google.com/search?{urlencode(params)}"

        slot_idx = -1
        acquired_lock = None
        for idx, lk in enumerate(cls._chrome_slot_locks):
            if lk.acquire(blocking=False):
                slot_idx = idx
                acquired_lock = lk
                break

        try:
            cmd = [
                browser_exe,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-sync",
                "--disable-background-networking",
                "--blink-settings=imagesEnabled=false",
                f"--user-agent={_UA}",
            ]
            if slot_idx >= 0:
                profile_dir = os.path.abspath(f"storage/cache/chrome_gimg_pool/slot_{slot_idx}")
                os.makedirs(profile_dir, exist_ok=True)
                cmd.append(f"--user-data-dir={profile_dir}")
            else:
                cmd.append("--incognito")

            cmd.append("--dump-dom")
            cmd.append(target_url)

            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
            with cls._headless_chrome_sem:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=14,
                    creationflags=creationflags,
                )
            return proc.stdout or ""
        except Exception as e:
            logger.debug(f"[WebImage] Headless Google Images DOM error for '{query}': {e}")
            return ""
        finally:
            if acquired_lock is not None:
                acquired_lock.release()

    @classmethod
    def _raw_google_images(cls, query: str, limit: int = 35) -> List[Dict]:
        """
        Tìm kiếm trực tiếp TOP SEARCH trên Google Images (www.google.com/search?tbm=isch&tbs=isz:l):
        - Ưu tiên Google Custom Search API nếu có GOOGLE_SEARCH_API_KEY + GOOGLE_SEARCH_CX.
        - Mặc định dùng Chrome/Edge Headless trích xuất trực tiếp Top Search Google Images (100+ ảnh gốc sắc nét).
        """
        import html as _html
        import re as _re
        from urllib.parse import urlparse

        out: List[Dict] = []
        seen_urls = set()
        safe_mode = "off" if bypass_copyright_enabled() else "active"

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
                        "safe": safe_mode,
                    },
                    timeout=8,
                )
                if res_api.status_code == 200:
                    for rank_i, item in enumerate(res_api.json().get("items", []), start=1):
                        link = item.get("link")
                        if not link or link in seen_urls:
                            continue
                        seen_urls.add(link)
                        img_meta = item.get("image") or {}
                        ctx_link = img_meta.get("contextLink") or item.get("displayLink") or ""
                        out.append({
                            "image": link,
                            "thumbnail": img_meta.get("thumbnailLink") or link,
                            "fallback_url": img_meta.get("thumbnailLink") or "",
                            "title": item.get("title") or item.get("snippet") or query,
                            "width": int(img_meta.get("width") or 1280),
                            "height": int(img_meta.get("height") or 720),
                            "source": (item.get("displayLink") or "google").replace("www.", ""),
                            "url": ctx_link,
                            "engine": "google_images",
                            "google_rank": rank_i,
                        })
            except Exception as e:
                logger.debug(f"[WebImage] Google CSE fallback: {e}")

        # 2. Truy vấn trực tiếp Top Search Google Images (tbm=isch & tbs=isz:l ưu tiên ảnh độ phân giải lớn)
        raw_html = cls._fetch_google_isch_dom_headless(query, safe_mode=safe_mode, tbs="isz:l")
        if not raw_html or "gstatic.com" not in raw_html:
            # Dự phòng HTTP thường nếu máy không có Chrome/Edge
            headers = {
                "User-Agent": _UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cookie": "CONSENT=YES+cb.20230531-04-p0.en+FX+908;",
            }
            res = requests.get(
                "https://www.google.com/search",
                params={"q": query, "tbm": "isch", "tbs": "isz:l", "hl": "en", "gl": "us", "safe": safe_mode, "ijn": "0"},
                headers=headers,
                timeout=8,
            )
            if res.status_code == 200:
                raw_html = res.text

        if not raw_html:
            if out:
                return out
            raise RuntimeError("Không lấy được HTML từ Google Images")

        text = (
            raw_html
            .replace("\\u003d", "=")
            .replace("\\u0026", "&")
            .replace("\\u003c", "<")
            .replace("\\u003e", ">")
            .replace("\\/", "/")
            .replace('\\"', '"')
        )

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
            orig_h = int(m.group(5) or 0)
            orig_w = int(m.group(6) or 0)

            if not orig_url or orig_url in seen_urls:
                continue
            low_u = orig_url.lower()
            if any(bad in low_u for bad in ("gstatic.com", "google.com/images", "googleapis.com", "favicon", ".svg", ".gif", ".ico")):
                continue
            seen_urls.add(orig_url)

            tail = text[m.end(): m.end() + 1400]
            page_url = ""
            title = ""
            m_meta = meta_2003_pattern.search(tail)
            if m_meta:
                page_url = m_meta.group(1)
                title = _html.unescape(m_meta.group(2))
            else:
                m_alt = _re.search(r'"(https?://(?!encrypted-tbn0\.gstatic\.com)[^"]+)"\s*,\s*"([^"\n]{4,180})"', tail[:900])
                if m_alt:
                    page_url = m_alt.group(1)
                    title = _html.unescape(m_alt.group(2))

            domain = urlparse(page_url or orig_url).netloc.replace("www.", "") or "google_web"
            out.append({
                "image": orig_url,
                "thumbnail": thumb_url or orig_url,
                "fallback_url": thumb_url or "",
                "title": title or query,
                "width": orig_w or 1280,
                "height": orig_h or 720,
                "source": domain,
                "url": page_url,
                "engine": "google_images",
                "google_rank": len(out) + 1,
            })
            if len(out) >= limit:
                break

        if not out and "/httpservice/retry/enablejs" in raw_html:
            raise RuntimeError("Google Images yêu cầu JS challenge")
        return out

    @staticmethod
    def _looks_relevant(raw: List[Dict], query: str) -> bool:
        """
        Nguồn cào miễn phí khi bị giới hạn thường trả kết quả RÁC (vd: tìm "kraken" ra xe máy / nước ngọt).
        Nếu < 20% tiêu đề chứa từ khóa chính của truy vấn → coi như nguồn đang hỏng.
        """
        from app.services.media.image_quality import subject_terms
        generic = {
            "concept", "art", "movie", "scene", "dark", "fantasy", "close", "photo", "photograph",
            "historical", "the", "giant", "attacking", "hunting", "soaring", "flying", "eye", "eyes",
            "high", "resolution", "real", "authentic", "documentary", "terrifying", "horror", "scary",
            "creepy", "nightmare", "thalassophobia", "megalophobia", "eldritch", "artstation", "cgi",
            "8k", "4k", "unreal", "engine", "lurking", "darkness", "abyss", "abyssal", "colossal",
            "cinematic", "still", "monster", "creature", "ocean", "sea", "deep", "underwater",
            "chiaroscuro", "noir", "monochrome", "seinen", "thriller", "aesthetic", "portrait",
            "shadow", "shadows", "silhouette", "lighting", "realistic", "shot", "frame", "digital",
            "illustration", "wallpaper", "photography", "photorealistic",
        }
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
        return hits / len(sample) >= 0.20

    def _run_single_open_engine(self, eng: str, query: str, per_page: int) -> List[Dict]:
        """Chạy 1 engine tìm kiếm ảnh đơn lẻ và chuẩn hóa kết quả (có cache bộ nhớ đệm chống gọi trùng lặp)."""
        from app.services.media.image_quality import is_watermark_risk
        if not self._engine_ok(eng):
            return []
        if eng == "serper" and not os.getenv("SERPER_API_KEY", "").strip():
            return []
        cache_key = f"{eng}:{(query or '').strip().lower()}"
        with self._cache_lock:
            cached = self._open_engine_cache.get(cache_key)
        if cached is not None:
            return list(cached[: max(per_page, 14)])
        try:
            raw_results: List[Dict] = []
            if eng == "google":
                raw_results = self._raw_google_images(query, limit=max(per_page * 4, 35))
            elif eng == "serper":
                raw_results = self._raw_serper_images(query, limit=25)
            elif eng == "bing":
                raw_results = self._raw_bing_images(query, limit=35)
            elif eng == "artstation":
                raw_results = self._raw_artstation_images(query, limit=max(per_page * 2, 14))
            elif eng == "ddgs":
                raw_results = self._raw_ddgs_images(query, limit=max(per_page * 3, 20))
            elif eng == "wallhaven":
                raw_results = self._raw_wallhaven_images(query, limit=max(per_page * 2, 12))

            if raw_results and eng != "google" and not self._looks_relevant(raw_results, query):
                logger.info(f"[WebImage] {eng} trả kết quả ít liên quan cho '{query}' → bỏ qua")
                return []

            items: List[Dict] = []
            for r in raw_results:
                img_url = r.get("image")
                if not img_url:
                    continue
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
                thumb = r.get("thumbnail") or img_url
                fb_url = r.get("fallback_url") or (thumb if thumb != img_url else "")
                engine_tag = "google_images" if eng in ("google", "serper") else "open_web"

                item = {
                    "url": img_url,
                    "thumbnail": thumb,
                    "fallback_url": fb_url,
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
                    "_google_rank": int(r.get("google_rank") or 999),
                }
                if is_watermark_risk(item) or min(w, h) < 420:
                    continue
                items.append(item)
                if len(items) >= max(per_page, 14):
                    break
            if items:
                self._engine_fail_streak[eng] = 0
                with self._cache_lock:
                    self._open_engine_cache[cache_key] = list(items)
            return items
        except Exception as e:
            logger.debug(f"[WebImage] {eng} lỗi cho '{query}': {e}")
            self._engine_fail(eng, minutes=0.25)
            return []

    def search_open_web(
        self,
        query: str,
        per_page: int = 12,
        preferred_engines: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        Ưu tiên số 1: Tìm kiếm trực tiếp TOP SEARCH GOOGLE IMAGES (google.com/search?tbm=isch&tbs=isz:l).
        Chỉ dùng Bing / DDGS làm dự phòng khi Google Images trả về quá ít kết quả (< 6 ảnh).
        """
        target_limit = max(per_page, 12)
        # 1. Chạy trực tiếp Top Search Google Images trước
        if self._engine_ok("google"):
            google_items = self._run_single_open_engine("google", query, target_limit)
            if len(google_items) >= 6:
                return google_items[:target_limit]
        else:
            google_items = []

        # 2. Dự phòng (fallback) nếu Google Images trả về < 6 ảnh
        default_engines = "bing,ddgs,serper"
        env_engines = [e.strip() for e in os.getenv("OPEN_WEB_ENGINES", default_engines).split(",") if e.strip() and e.strip() != "google"]
        if preferred_engines:
            valid_pref = [e.strip().lower() for e in preferred_engines if isinstance(e, str) and e.strip() and e.strip().lower() != "google"]
            engines = valid_pref + [e for e in env_engines if e not in valid_pref]
        else:
            engines = env_engines
        active_engines = [
            e for e in engines
            if self._engine_ok(e) and not (e == "serper" and not os.getenv("SERPER_API_KEY", "").strip())
        ]
        if not active_engines:
            active_engines = ["bing", "ddgs"]
            for e in active_engines:
                self._engine_block_until.pop(e, None)

        parallel_batch = active_engines[:3]
        buckets: List[List[Dict]] = [google_items] if google_items else []
        with ThreadPoolExecutor(max_workers=len(parallel_batch)) as ex:
            futures = [ex.submit(self._run_single_open_engine, eng, query, per_page) for eng in parallel_batch]
            for f in futures:
                try:
                    buckets.append(f.result(timeout=9))
                except Exception:
                    buckets.append([])

        out: List[Dict] = []
        seen = set()
        for b in buckets:
            for it in b:
                u = it.get("url")
                if u and u not in seen:
                    seen.add(u)
                    out.append(it)
                    if len(out) >= target_limit:
                        return out
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

    # ─────────────────────────────── Tổng hợp (LangGraph 5-Way Parallel Send + operator.add) ────────────────────────────────
    def search_all(
        self,
        query: str,
        per_source: int = 5,
        subject: Optional[str] = None,
        extra_queries: Optional[List[str]] = None,
        creature_mode: bool = False,
        preferred_engines: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        Thực thi tìm kiếm ảnh song song 5 luồng (5-Way Parallel Execution) bằng LangGraph `Send` API
        kết hợp `Annotated[List[Dict[str, Any]], operator.add]` để bắn đồng thời 5 nhánh
        (Google Top Images, ArtStation 8K / Google Angle 2, Bing HD, Wallhaven 4K / DDGS, Wikimedia / Google Angle 3)
        và tự động gom (reduce) 5 kết quả lại trong 1 Superstep duy nhất!
        """
        if not query or not web_image_search_enabled():
            return []

        subject_clean = (subject or "").strip()
        use_pure_google_search = creature_mode or bypass_copyright_enabled()
        auto_extra = list(extra_queries or [])
        if creature_mode and not auto_extra:
            try:
                from app.services.media.image_quality import creature_queries
                auto_extra = creature_queries(subject_clean or query)
            except Exception:
                auto_extra = [f"giant {subject_clean or query} concept art 8k"]

        try:
            from app.graph.parallel_search import run_langgraph_5way_image_search
            return run_langgraph_5way_image_search(
                fetcher=self,
                query=query,
                per_source=per_source,
                subject=subject_clean,
                extra_queries=auto_extra,
                creature_mode=creature_mode,
                use_pure_google_search=use_pure_google_search,
                preferred_engines=preferred_engines,
            )
        except Exception as e_lg:
            logger.warning(f"[WebImage] LangGraph 5-Way Search fallback: {e_lg}")
            return self.search_open_web(query, per_page=max(per_source, 12), preferred_engines=preferred_engines)


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
