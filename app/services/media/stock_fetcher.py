"""
app.services.media.stock_fetcher — Trích xuất hình ảnh & video stock đa nguồn (Pexels, Pixabay, NASA, Openverse).

Hỗ trợ:
1. Xoay vòng đa Pexels API Key tự động khi gặp rate-limit.
2. Tìm kiếm video dọc (9:16 portrait) tối ưu cho Shorts.
3. Tích hợp VisualCurationAgent để tuyển chọn video ăn khớp bối cảnh thay vì chọn ngẫu nhiên.
4. Tự động mở rộng từ khóa theo nhóm concept khi thiếu ứng viên.
"""

import os
import re
import time
import threading
import urllib.parse
import itertools
import logging
import requests
from typing import List, Dict, Optional, Any, Tuple

from app.utils.langsmith_tracer import trace_custom_span
from config.settings import settings
from app.services.media.entity_detector import clean_scene_search_query

logger = logging.getLogger(__name__)
_stock_video_claim_lock = threading.Lock()


# ── MoneyPrinterTurbo Standard: 10px Dimension Tolerance ──
# Bù sai số biên độ crop/nén video (ví dụ 1072x1916 hay 718x1278) để không bị lọc nhầm hoặc mất điểm tối ưu
DIMENSION_TOLERANCE_PX: int = 10


class StockFetcher:
    """Fetcher chuyên trách kết nối và trích xuất stock footage & photos chất lượng cao."""
    DIMENSION_TOLERANCE_PX: int = DIMENSION_TOLERANCE_PX
    _pexels_key_cooldown_until: Dict[str, float] = {}
    _pexels_cycle_lock = threading.Lock()
    _pexels_rr_idx: int = 0

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
        self._pixabay_key = getattr(settings, 'PIXABAY_API_KEY', '') or os.getenv("PIXABAY_API_KEY", "")

    @property
    def api_key(self) -> Optional[str]:
        return self.keys[0] if self.keys else None

    def _get_active_key(self) -> Optional[str]:
        if not self.keys:
            return None
        now = time.time()
        n_keys = len(self.keys)
        with StockFetcher._pexels_cycle_lock:
            for offset in range(n_keys):
                idx = (StockFetcher._pexels_rr_idx + offset) % n_keys
                cand_k = self.keys[idx]
                if now >= StockFetcher._pexels_key_cooldown_until.get(cand_k, 0.0):
                    StockFetcher._pexels_rr_idx = (idx + 1) % n_keys
                    return cand_k
        return None

    @staticmethod
    def _mark_pexels_key_limited(key: Optional[str], status_code: int) -> None:
        if not key:
            return
        now = time.time()
        cooldown_sec = 600.0 if status_code == 401 else 65.0
        with StockFetcher._pexels_cycle_lock:
            prev_until = StockFetcher._pexels_key_cooldown_until.get(key, 0.0)
            StockFetcher._pexels_key_cooldown_until[key] = max(prev_until, now + cooldown_sec)
            should_log = now >= prev_until
        if should_log:
            logger.warning(f"Pexels key bị giới hạn ({status_code}), tạm ngắt key {cooldown_sec:.0f}s và chuyển key...")

    def search_videos(self, query: str, per_page: int = 4, orientation: str = "portrait", page: Optional[int] = None) -> List[Dict]:
        """Tìm kiếm video trên Pexels theo từ khóa với tỷ lệ dọc (portrait 9:16) có cơ chế retry, xoay key và pagination jitter."""
        key = self._get_active_key()
        if not key:
            return []

        target_page = page if page is not None else 1
        q_low = (query or "").lower()
        allow_zoo = any(w in q_low for w in ("zoo", "cage", "aquarium"))
        _bad_zoo_slugs = ("-zoo", "zoo-", "zoological", "cage", "enclosure", "fence", "captive", "captivity", "aquarium")

        def _filter_vids(raw_vids: List[Dict]) -> List[Dict]:
            if allow_zoo:
                return raw_vids
            return [
                v for v in (raw_vids or [])
                if not any(bs in str(v.get("url", "")).lower() for bs in _bad_zoo_slugs)
            ]

        from app.agents.curation.scene_context_analyzer_mixin import is_scene_cancelled_ctx, remaining_scene_budget_ctx
        for attempt in range(2):
            if not key or is_scene_cancelled_ctx(min_remaining_sec=1.5):
                break
            try:
                headers = {"Authorization": key}
                params = {
                    "query": query,
                    "per_page": max(per_page, 10),
                    "orientation": orientation,
                    "page": target_page,
                }
                req_to = min(6.0, max(2.5, remaining_scene_budget_ctx(8.0) - 1.0))
                res = requests.get(self.base_url, headers=headers, params=params, timeout=(2.8, req_to))
                if res.status_code == 200:
                    vids = _filter_vids(res.json().get("videos", []))
                    if not vids and target_page > 1 and not is_scene_cancelled_ctx(min_remaining_sec=3.0):
                        params["page"] = 1
                        res1 = requests.get(self.base_url, headers=headers, params=params, timeout=(2.5, min(5.0, req_to)))
                        if res1.status_code == 200:
                            vids = _filter_vids(res1.json().get("videos", []))
                    return vids[:per_page] if len(vids) > per_page else vids
                elif res.status_code in (401, 429):
                    self._mark_pexels_key_limited(key, res.status_code)
                    key = self._get_active_key()
                    continue
                break
            except Exception as e:
                logger.debug(f"Pexels search_videos attempt {attempt+1} error for '{query}': {e}")
                break
        return []

    def search_photos(
        self,
        query: str,
        per_page: int = 3,
        orientation: str = "portrait",
        page: Optional[int] = None,
        topic: str = "",
        narration: str = "",
        used_urls: Optional[set] = None,
    ) -> List[Dict]:
        """Tìm kiếm ảnh tĩnh trên Pexels theo từ khóa với tỷ lệ dọc (portrait 9:16) cho Ken Burns, có pagination jitter và lọc lạc đề."""
        import random
        from app.services.media.entity_detector import is_incongruent_stock_image
        from app.services.media.media_history import is_media_already_used, extract_media_identity

        key = self._get_active_key()
        if not key:
            return []

        target_page = page if page is not None else 1
        ctx_photo_low = f"{topic} {query} {narration}".lower()
        allow_zoo = any(w in ctx_photo_low for w in ("zoo", "cage", "aquarium"))

        try:
            headers = {"Authorization": key}
            params = {
                "query": query,
                "per_page": max(per_page * 3, 12),  # Lấy dư để lọc bỏ ảnh trùng lặp và ảnh lạc đề
                "orientation": orientation,
                "page": target_page,
            }
            res = requests.get(self.photo_url, headers=headers, params=params, timeout=10)
            if res.status_code in (401, 429):
                self._mark_pexels_key_limited(key, res.status_code)
                return []
            if res.status_code == 200:
                data = res.json().get("photos", [])
                if not data and target_page > 1:
                    params["page"] = 1
                    res1 = requests.get(self.photo_url, headers=headers, params=params, timeout=10)
                    if res1.status_code == 200:
                        data = res1.json().get("photos", [])
                results = []
                for p in data:
                    src = p.get("src", {})
                    if str(orientation).lower() == "landscape":
                        img_url = src.get("landscape") or src.get("large2x") or src.get("large") or src.get("original")
                    else:
                        img_url = src.get("portrait") or src.get("large2x") or src.get("large") or src.get("original")
                    if not img_url:
                        continue

                    p_id = str(p.get("id", ""))
                    p_creator = f"pexels_user:{p.get('photographer_id')}" if p.get("photographer_id") else ""
                    alt_text = p.get("alt", "") or query
                    alt_low = f"{alt_text} {p.get('url', '')}".lower()

                    if not allow_zoo and any(
                        sz_w in alt_low
                        for sz_w in ("-zoo", "zoo-", "zoological", " in a zoo", " at the zoo", "cage", "enclosure", "fence")
                    ):
                        continue

                    # Lọc ảnh lạc quẻ (văn phòng, cafe, tiệc tùng lọt vào chủ đề khác)
                    if is_incongruent_stock_image(alt_text, topic=topic or query, narration=narration):
                        continue

                    # Lọc trùng lặp qua used_urls (bao gồm cả cùng 1 nhiếp ảnh gia/buổi chụp)
                    if used_urls:
                        if (
                            is_media_already_used(img_url, used_urls)
                            or p_id in used_urls
                            or f"pexels:{p_id}" in used_urls
                            or (p_creator and p_creator in used_urls)
                        ):
                            continue

                    results.append({
                        "url": img_url,
                        "title": f"Pexels: {alt_text}",
                        "id": p_id,
                        "alt": alt_text,
                        "width": p.get("width", 1080),
                        "height": p.get("height", 1920),
                        "source": "pexels",
                        "thumbnail": src.get("medium") or img_url,
                        "creator_key": p_creator,
                    })
                    if len(results) >= per_page:
                        break
                return results
        except Exception as e:
            logger.debug(f"Pexels search_photos error for '{query}': {e}")
        return []

    _pixabay_rate_limited_until: float = 0.0

    def search_pixabay_videos(self, query: str, per_page: int = 4, video_type: str = "auto") -> List[Dict]:
        """Tìm kiếm video clip trên Pixabay API (tự động ưu tiên video quay thật `film` trừ khi truy vấn cần 3D/CGI/khoa học)."""
        import time as _time
        key = self._pixabay_key
        if not key or key == "your_pixabay_api_key_here" or _time.time() < StockFetcher._pixabay_rate_limited_until:
            return []

        try:
            clean_q = clean_scene_search_query(query, max_words=3) or query
            q_low = f"{query} {clean_q}".lower()
            allow_cgi = any(
                w in q_low
                for w in (
                    "neural", "brain", "synapse", "cortex", "neuron", "dna", "molecule",
                    "atom", "galaxy", "universe", "space", "cosmos", "nebula", "black hole",
                    "wormhole", "planet", "3d", "cgi", "animation", "network", "cyber",
                    "matrix", "digital", "particles", "energy", "hourglass", "clock gears",
                )
            )
            resolved_vtype = (
                ("all" if allow_cgi else "film")
                if video_type == "auto"
                else video_type
            )
            url = (
                f"https://pixabay.com/api/videos/?key={key}"
                f"&q={urllib.parse.quote(clean_q)}"
                f"&video_type={resolved_vtype}"
                f"&per_page={max(per_page, 4)}&safesearch=true"
            )
            res = requests.get(url, timeout=6)
            if res.status_code == 429:
                StockFetcher._pixabay_rate_limited_until = _time.time() + 180.0
                return []
            if res.status_code == 200:
                hits = res.json().get("hits", [])
                _generic_action_words = {
                    "running", "swimming", "walking", "jumping", "flying", "hunting", "sleeping",
                    "water", "ocean", "sea", "river", "lake", "snow", "ice", "mountain", "forest",
                    "jungle", "desert", "beach", "night", "day", "fast", "slow", "giant", "wild",
                    "deep", "underwater", "waves", "storm", "rain", "wind", "fire", "rescue",
                }
                _subj_tokens = [
                    w.lower() for w in re.findall(r"[a-zA-Z]{3,}", clean_q)
                    if w.lower() not in _generic_action_words
                ]
                results = []
                for h in hits:
                    h_type = str(h.get("type") or "film").lower()
                    h_tags = str(h.get("tags") or "")
                    h_tags_low = h_tags.lower()
                    if not allow_cgi and (
                        h_type == "animation"
                        or any(bad_t in h_tags_low for bad_t in ("cartoon", "anime", "comic", "2d", "illustration", "cute heart"))
                    ):
                        continue
                    if _subj_tokens and not any(st in h_tags_low for st in _subj_tokens):
                        continue
                    vids = h.get("videos", {})
                    med_v = vids.get("medium") or {}
                    v_info = (
                        med_v
                        if int(med_v.get("height") or 0) >= 720 and med_v.get("url")
                        else (vids.get("large") or med_v or vids.get("small"))
                    )
                    if v_info and v_info.get("url"):
                        thumb = v_info.get("thumbnail") or (vids.get("medium") or {}).get("thumbnail") or (
                            f"https://i.vimeocdn.com/video/{h.get('picture_id', '')}_640x360.jpg" if h.get("picture_id") else ""
                        )
                        results.append({
                            "id": h.get("id"),
                            "user_id": h.get("user_id"),
                            "creator_key": f"pixabay_user:{h.get('user_id')}" if h.get("user_id") else "",
                            "url": v_info["url"],
                            "title": f"Pixabay Video: {h.get('tags', clean_q)}",
                            "tags": h_tags,
                            "video_type": h_type,
                            "width": v_info.get("width", 1080),
                            "height": v_info.get("height", 1920),
                            "duration": h.get("duration", 0),
                            "media_type": "video",
                            "source": "pixabay_video",
                            "thumbnail": thumb,
                        })
                return results
        except Exception as e:
            logger.debug(f"Pixabay video search error for '{query}': {e}")
        return []

    def search_wikimedia_videos(self, query: str, per_page: int = 4) -> List[Dict]:
        """Tìm kiếm video tài liệu & thiên nhiên thật trên Wikimedia Commons (Public Domain / CC BY, sạch watermark 100%)."""
        try:
            clean_q = clean_scene_search_query(query, max_words=3) or query
            params = {
                "action": "query",
                "generator": "search",
                "gsrnamespace": "6",
                "gsrsearch": f"filetype:video {clean_q}",
                "gsrlimit": str(max(per_page * 2, 6)),
                "prop": "imageinfo",
                "iiprop": "url|dimensions|mime",
                "iiurlwidth": "640",
                "format": "json",
            }
            res = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params=params,
                headers={"User-Agent": "AIYouTubeStudio/2.0 (https://github.com/vv1nc)"},
                timeout=6,
            )
            if res.status_code == 200:
                pages = (res.json().get("query") or {}).get("pages") or {}
                results: List[Dict] = []
                for p in pages.values():
                    iinfo_list = p.get("imageinfo") or []
                    if not iinfo_list:
                        continue
                    ii = iinfo_list[0]
                    v_url = str(ii.get("url") or "")
                    mime = str(ii.get("mime") or "").lower()
                    if not v_url or ("video" not in mime and not v_url.lower().endswith((".webm", ".mp4", ".ogv"))):
                        continue
                    w = int(ii.get("width") or 1080)
                    h = int(ii.get("height") or 1920)
                    if min(w, h) < 480:
                        continue
                    title_raw = str(p.get("title") or "").replace("File:", "").rsplit(".", 1)[0]
                    t_raw_low = title_raw.lower()
                    if any(
                        bad_w in t_raw_low
                        for bad_w in ("meat", "slaughter", "butcher", "investigation", "animated", "cartoon", "cgi spot", "corpse", "autopsy")
                    ):
                        continue
                    results.append({
                        "url": v_url,
                        "title": f"Wikimedia Video: {title_raw}",
                        "width": w,
                        "height": h,
                        "duration": 0,
                        "media_type": "video",
                        "source": "wikimedia_video",
                        "thumbnail": str(ii.get("thumburl") or ""),
                    })
                    if len(results) >= per_page:
                        break
                return results
        except Exception as e:
            logger.debug(f"Wikimedia video search error for '{query}': {e}")
        return []

    def search_pixabay_photos(self, query: str, per_page: int = 4, orientation: str = "vertical") -> List[Dict]:
        """Tìm kiếm ảnh chất lượng cao trên Pixabay API."""
        import time as _time
        key = self._pixabay_key
        if not key or key == "your_pixabay_api_key_here" or _time.time() < StockFetcher._pixabay_rate_limited_until:
            return []

        try:
            clean_q = clean_scene_search_query(query, max_words=3) or query
            pix_orient = "horizontal" if str(orientation).lower() in ("landscape", "horizontal") else "vertical"
            url = f"https://pixabay.com/api/?key={key}&q={urllib.parse.quote(clean_q)}&image_type=photo&orientation={pix_orient}&per_page={max(per_page, 3)}&safesearch=true&order=popular"
            res = requests.get(url, timeout=6)
            if res.status_code == 429:
                StockFetcher._pixabay_rate_limited_until = _time.time() + 180.0
                return []
            if res.status_code == 200:
                hits = res.json().get("hits", [])
                results = []
                for h in hits:
                    img_url = h.get("largeImageURL") or h.get("webformatURL")
                    if img_url:
                        results.append({
                            "url": img_url,
                            "title": f"Pixabay: {h.get('tags', clean_q)}",
                            "width": h.get("imageWidth", 1080),
                            "height": h.get("imageHeight", 1920),
                            "source": "pixabay",
                            "alt": h.get("tags", clean_q),
                            "thumbnail": h.get("webformatURL") or h.get("previewURL") or img_url,
                        })
                return results
        except Exception as e:
            logger.debug(f"Pixabay photo search error for '{query}': {e}")
        return []

    def search_nasa_media(self, query: str, media_type: str = "video,image", per_page: int = 4) -> List[Dict]:
        """Tìm kiếm từ Thư viện Ảnh & Video của NASA (Public Domain 100%)."""
        try:
            clean_q = clean_scene_search_query(query, max_words=3) or query
            encoded_q = urllib.parse.quote(clean_q)
            url = f"https://images-api.nasa.gov/search?q={encoded_q}&media_type={media_type}"
            res = requests.get(url, timeout=12)
            if res.status_code == 200:
                items = res.json().get("collection", {}).get("items", [])
                results = []
                for item in items[:per_page * 2]:
                    data_list = item.get("data", [{}])
                    m_type = data_list[0].get("media_type", "image")
                    title = data_list[0].get("title", f"NASA {clean_q}")
                    links = item.get("links", [{}])
                    thumb = links[0].get("href", "") if links else ""

                    if m_type == "video":
                        col_url = item.get("href")
                        if col_url:
                            try:
                                col_res = requests.get(col_url, timeout=5)
                                if col_res.status_code == 200:
                                    vid_links = col_res.json()
                                    mp4s = [lnk for lnk in vid_links if lnk.endswith("~orig.mp4") or lnk.endswith("~medium.mp4") or lnk.endswith(".mp4")]
                                    if mp4s:
                                        results.append({
                                            "url": mp4s[0],
                                            "title": f"NASA Video: {title}",
                                            "width": 1080,
                                            "height": 1920,
                                            "media_type": "video",
                                            "source": "nasa_video",
                                            "thumbnail": thumb,
                                        })
                            except Exception:
                                pass
                    elif m_type == "image":
                        if thumb:
                            orig_url = thumb.replace("~thumb.", "~orig.").replace("~medium.", "~orig.")
                            results.append({
                                "url": orig_url if "~orig." in orig_url else thumb,
                                "title": f"NASA Photo: {title}",
                                "width": 1080,
                                "height": 1920,
                                "source": "nasa",
                                "thumbnail": thumb,
                            })
                    if len(results) >= per_page:
                        break
                return results
        except Exception as e:
            logger.debug(f"NASA media search error for '{query}': {e}")
        return []

    def search_openverse(self, query: str, per_page: int = 5) -> List[Dict]:
        """Tìm kiếm ảnh trên Openverse API (tôn trọng bypass_copyright nếu được bật)."""
        try:
            from app.services.media.web_image_fetcher import bypass_copyright_enabled
            clean_q = clean_scene_search_query(query, max_words=3) or query
            license_filter = "" if bypass_copyright_enabled() else "&license_type=commercial"
            url = f"https://api.openverse.org/v1/images/?q={urllib.parse.quote(clean_q)}&page_size={per_page}{license_filter}"
            res = requests.get(url, timeout=8, headers={"User-Agent": "AIYouTubeStudio/2.0"})
            if res.status_code == 200:
                results_raw = res.json().get("results", [])
                out = []
                for r in results_raw:
                    if r.get("url"):
                        out.append({
                            "url": r["url"],
                            "title": r.get("title", clean_q),
                            "width": r.get("width", 1080),
                            "height": r.get("height", 1920),
                            "source": "openverse",
                            "thumbnail": r.get("thumbnail") or r["url"],
                        })
                return out
        except Exception as e:
            logger.debug(f"Openverse search error: {e}")
        return []

    def search_best_photo(
        self,
        query: str,
        used_urls: Optional[set] = None,
        visual_analysis: Optional[Dict[str, Any]] = None,
        narration: str = "",
        topic: str = "",
    ) -> Optional[Dict]:
        """
        Tìm ảnh tĩnh HẤP DẪN NHẤT cho phân cảnh từ nhiều kho (Internet Open Web, Wikipedia, Pexels + Pixabay + Openverse/Flickr, Wikimedia Commons, Unsplash),
        sau đó để VisualCurationAgent (AI Vision) nhìn trực tiếp và chọn ảnh tốt nhất.
        """
        from app.services.media.media_history import (
            get_recent_media_urls, record_used_media, is_media_already_used, extract_media_identity,
        )
        from app.services.media.entity_detector import is_incongruent_stock_image

        used = set(used_urls) if used_urls else set()
        used.update(get_recent_media_urls(limit=150))

        from app.services.media.image_quality import extract_subject, prefilter_candidates
        from app.services.media.query_broadener import is_vague_single_word
        va = visual_analysis or {}
        cq = clean_scene_search_query(query, max_words=6)
        raw_subj = str(va.get("subject") or "").strip() or extract_subject(query) or extract_subject(narration)
        if raw_subj and len(raw_subj.split()) == 1 and (is_vague_single_word(raw_subj) or len(raw_subj) <= 5):
            subject = cq or extract_subject(topic) or raw_subj
        else:
            subject = raw_subj or cq

        queries: List[str] = []
        for q in va.get("search_queries", [])[:2]:
            if q and q not in queries:
                queries.append(q)
        if cq and cq not in queries:
            queries.append(cq)
        if query and len(query.split()) <= 7 and query not in queries:
            queries.append(query)
        if subject and len(subject.split()) >= 2 and subject not in queries:
            queries.append(subject)
        if not queries and subject:
            queries.append(subject)
        if not queries:
            return None

        candidates: List[Dict] = []

        congruence_topic = f"{topic} {query}".strip()
        full_ctx_low = f"{congruence_topic} {narration}".lower()
        creature_mode = bool(va.get("creature_mode"))
        creature_qs = [q for q in (va.get("creature_queries") or []) if q]
        clean_stock_only = bool(va.get("clean_stock_only"))
        _allow_zoo = any(w in full_ctx_low for w in ("zoo", "cage", "aquarium"))

        def _add(items: List[Dict]):
            for it in items:
                u = it.get("url")
                if not u or is_media_already_used(u, used) or any(c.get("url") == u for c in candidates):
                    continue
                desc_low = f"{it.get('alt') or ''} {it.get('title') or ''} {u}".lower()
                if not creature_mode and any(
                    art_w in desc_low
                    for art_w in ("painting", "illustration", "drawing", "cartoon", "clipart", "vector", "sketch", "book cover", "infographic", "poster", "meme", "fantasy art", "watercolor")
                ):
                    continue
                if not _allow_zoo and any(
                    sz_w in desc_low
                    for sz_w in ("-zoo", "zoo-", "zoological", " in a zoo", " at the zoo", "cage", "enclosure")
                ):
                    continue
                if is_incongruent_stock_image(it.get("alt") or it.get("title") or "", topic=congruence_topic, narration=narration):
                    continue
                candidates.append(it)

        bypass_copyright = False
        try:
            from app.services.media.web_image_fetcher import bypass_copyright_enabled
            bypass_copyright = bypass_copyright_enabled()
        except Exception:
            bypass_copyright = False

        # 1. Nếu Agent gọi công cụ `fetch_clean_cinematic_portrait_or_broll` (`clean_stock_only=True`):
        #    Ưu tiên tuyệt đối kho ảnh chụp thật Pexels / Pixabay HD không chữ (bỏ qua Open Web để tránh Quote Card / bìa sách)
        if clean_stock_only:
            for pq_p in (queries + ["man intense eyes dark shadow", "dramatic portrait dark lighting", "person silhouette shadow"]):
                _add(self.search_photos(pq_p, per_page=5, orientation="portrait", topic="dramatic portrait", narration="", used_urls=used))
                _add(self.search_pixabay_photos(pq_p, per_page=5))
                if len(candidates) >= 10:
                    break

        # 2. Khi tắt `bypass_copyright` (chế độ an toàn bản quyền & ảnh chụp thật) và không phải `creature_mode`:
        #    Truy vấn Pexels HD + Pixabay HD + Openverse TRƯỚC để đảm bảo 100% ảnh chụp thật sắc nét, không dính tranh vẽ/infographic!
        def _fetch_licensed_stock_photos():
            stock_queries = ([subject] if subject else queries[:1]) if creature_mode else queries[:3]
            photo_tasks: List[Dict[str, Any]] = []
            for q in stock_queries:
                photo_tasks.append({"src": "pexels", "q": q})
                photo_tasks.append({"src": "pixabay", "q": q})
            photo_tasks.append({"src": "openverse", "q": subject or queries[0]})

            try:
                from app.graph.parallel_search import run_langgraph_parallel_tasks

                def _exec_photo_api(t: Dict[str, Any]) -> List[Dict]:
                    src_t = t["src"]
                    q_t = t["q"]
                    if src_t == "pexels":
                        return self.search_photos(
                            q_t, per_page=6, orientation="portrait",
                            topic=f"{congruence_topic} {q_t}".strip(), narration=narration, used_urls=used
                        )
                    if src_t == "pixabay":
                        return self.search_pixabay_photos(q_t, per_page=6)
                    if src_t == "openverse":
                        return self.search_openverse(q_t, per_page=5)
                    return []

                completed_photos = run_langgraph_parallel_tasks(
                    tasks=photo_tasks,
                    worker_fn=_exec_photo_api,
                    task_group=f"StockPhotoAPIs({len(photo_tasks)} calls)",
                )
                for cp in completed_photos:
                    _add(cp.get("result") or [])
            except Exception:
                for q in stock_queries:
                    _add(self.search_photos(q, per_page=6, orientation="portrait", topic=f"{congruence_topic} {q}".strip(), narration=narration, used_urls=used))
                    _add(self.search_pixabay_photos(q, per_page=6))

        prefer_google_web = bool(va.get("prefer_google_web")) or (
            "google" in [str(e).lower() for e in (va.get("preferred_engines") or [])]
        )
        if not clean_stock_only and (not bypass_copyright and not creature_mode and not prefer_google_web):
            _fetch_licensed_stock_photos()

        web_fetcher = None

        def _fetch_open_web_google_photos() -> None:
            nonlocal web_fetcher
            try:
                from app.services.media.web_image_fetcher import WebImageFetcher, web_image_search_enabled
                if web_image_search_enabled() and not clean_stock_only:
                    web_fetcher = WebImageFetcher()
                    google_qs = [q for q in (va.get("google_image_queries") or []) if q]
                    pref_engines = va.get("preferred_engines") or ["google", "bing"]
                    if creature_mode and creature_qs:
                        web_items = web_fetcher.search_all(
                            creature_qs[0], per_source=8, subject=subject,
                            extra_queries=creature_qs[1:], creature_mode=True,
                            preferred_engines=pref_engines,
                        )
                    else:
                        primary_web_q = google_qs[0] if google_qs else queries[0]
                        extra_web_qs = (google_qs[1:] + queries[:2]) if google_qs else queries[1:3]
                        web_items = web_fetcher.search_all(
                            primary_web_q, per_source=8, subject=subject,
                            extra_queries=extra_web_qs, preferred_engines=pref_engines,
                        )
                    _add(web_items)
                    logger.info(
                        f"[StockFetcher] 🔎 Gemini Google Search '{queries[0]}' (subject='{subject}'): "
                        f"{len(candidates)} ảnh ứng viên (paradigm={va.get('visual_paradigm', 'auto')}, creature={creature_mode})"
                    )
            except Exception as e:
                logger.debug(f"[StockFetcher] Lỗi nguồn ảnh web: {e}")

        if not clean_stock_only and (bypass_copyright or creature_mode or prefer_google_web or len(candidates) < 6):
            _fetch_open_web_google_photos()

        # 3. Gọi Pexels / Pixabay / Openverse bổ sung nếu chưa chạy ở bước 2 và tổng ứng viên < 6
        if not clean_stock_only and (bypass_copyright or creature_mode or prefer_google_web) and len(candidates) < 6:
            _fetch_licensed_stock_photos()

        # Lọc watermark/ảnh nhỏ + xếp hạng theo độ bắt mắt, chủ thể, bố cục dọc, độ phân giải
        candidates = prefilter_candidates(
            candidates, subject=subject or queries[0], limit=16,
            creature_mode=creature_mode, max_per_source=14 if (creature_mode or bypass_copyright or prefer_google_web) else 8,
        )

        if not candidates:
            return None

        if subject and not va.get("subject"):
            va = {**va, "subject": subject}

        is_hook_photo = bool(va.get("is_hook_scene") or va.get("scene_id") == 1)
        winner = None
        score = 0.0
        should_ai = True
        try:
            from app.agents.curation.scene_context_analyzer_mixin import is_scene_cancelled_ctx
            if is_scene_cancelled_ctx():
                return None
            from app.agents.visual_curation_agent import VisualCurationAgent
            winner, score, rationale, should_ai = VisualCurationAgent().curate_candidate_photos(
                candidates=candidates,
                visual_analysis=va or None,
                narration=narration or query,
                topic=topic,
                visual_keyword=query,
                used_urls=used,
            )
            if winner is None and should_ai and len(candidates) > 5 and not is_scene_cancelled_ctx():
                winner, score, rationale, should_ai = VisualCurationAgent().curate_candidate_photos(
                    candidates=candidates[5:],
                    visual_analysis=va or None,
                    narration=narration or query,
                    topic=topic,
                    visual_keyword=query,
                    used_urls=used,
                )
            # Nếu kho Pexels/Pixabay trả về >= 6 ảnh chung chung nhưng bị Vision từ chối hết (winner is None)
            # và chưa quét Google Images (web_fetcher is None), tự động mở khóa quét Google Images theo các chunk của LLM!
            if winner is None and web_fetcher is None and not clean_stock_only and not is_scene_cancelled_ctx():
                prev_urls = {c.get("url") for c in candidates if c.get("url")}
                _fetch_open_web_google_photos()
                new_web_cands = prefilter_candidates(
                    [c for c in candidates if c.get("url") not in prev_urls],
                    subject=subject or queries[0],
                    limit=12,
                    creature_mode=creature_mode,
                    max_per_source=12,
                )
                if new_web_cands:
                    winner, score, rationale, should_ai = VisualCurationAgent().curate_candidate_photos(
                        candidates=new_web_cands,
                        visual_analysis=va or None,
                        narration=narration or query,
                        topic=topic,
                        visual_keyword=query,
                        used_urls=used,
                    )
                    if winner is not None:
                        candidates = new_web_cands + candidates
            if winner is not None:
                winner["vision_score"] = float(score or 0.0)
        except Exception as e:
            logger.debug(f"[StockFetcher] Lỗi curate ảnh: {e}")
            winner = None

        if not winner:
            logger.info(f"[StockFetcher] 🚫 PhotoVisionAudit từ chối hoặc chưa xác thực được ứng viên ảnh cho '{query}' -> Không lấy bừa candidates[0]!")
            return None
        try:
            from app.agents.media_agent import register_download_fallbacks
            register_download_fallbacks(winner, candidates)
            distinct_alts: List[str] = []
            win_u = winner.get("url")
            win_fb = winner.get("fallback_url")
            for va_u in (winner.get("_vision_alternates") or []):
                if va_u and va_u not in (win_u, win_fb) and not is_media_already_used(va_u, used) and va_u not in distinct_alts:
                    distinct_alts.append(va_u)
            winner["alternate_urls"] = distinct_alts[:8]
        except Exception:
            pass
        record_used_media(winner["url"], query=query, source=winner.get("source", "stock_photo"))
        if winner.get("license"):
            try:
                from app.services.media.web_image_fetcher import record_image_credit
                record_image_credit(winner, query=query)
                if winner.get("source") == "unsplash" and web_fetcher:
                    web_fetcher.track_unsplash_download(winner)
            except Exception:
                pass
        ids = extract_media_identity(winner["url"])
        if winner.get("creator_key"):
            ids.add(str(winner["creator_key"]))
        if used_urls is not None:
            used_urls.update(ids)
        return winner

    @staticmethod
    def format_video_candidate(v: Optional[Dict], query: str = "", used: Optional[set] = None) -> Optional[Dict]:
        """Chuẩn hóa và trích xuất direct link MP4 chuẩn dọc 9:16 có độ phân giải tối ưu từ candidate."""
        if not v:
            return None
        used_set = used if used is not None else set()
        from app.services.media.media_history import is_media_already_used

        if v.get("source") in ("nasa_video", "pixabay_video", "wikimedia_video", "pixabay_16x9_video") and v.get("url"):
            cand_urls = [v["url"], *(v.get("_vision_alternates") or [])]
            chosen_direct = next((u for u in cand_urls if u and not is_media_already_used(u, used_set)), None)
            if chosen_direct:
                out_cand = {
                    "url": chosen_direct,
                    "title": v.get("title", f"Video: {query}"),
                    "width": v.get("width", 1080),
                    "height": v.get("height", 1920),
                    "duration": v.get("duration", 0),
                    "media_type": "video",
                    "source": v.get("source", "video"),
                    "thumbnail": v.get("thumbnail") or v.get("image", ""),
                }
                for keep_k in ("web_url", "_vision_alternates", "curation_score", "curation_rationale", "creator_key"):
                    if keep_k in v:
                        out_cand[keep_k] = v[keep_k]
                return out_cand
            return None

        v_dur_sec = float(v.get("duration") or 0.0)

        def _res_score(vf):
            h = vf.get("height", 0)
            w = vf.get("width", 0)
            fps = float(vf.get("fps") or 30.0)
            fps_pen = 1200 if (v_dur_sec > 20.0 and fps > 35.0) else (400 if fps > 50.0 else 0)
            if v_dur_sec > 40.0 and abs(h - 1280) <= DIMENSION_TOLERANCE_PX:
                return 3200 - fps_pen
            # Ưu tiên số 1: Full HD dọc ~1080x1920 (dung sai 10px)
            if abs(h - 1920) <= DIMENSION_TOLERANCE_PX and abs(w - 1080) <= (DIMENSION_TOLERANCE_PX * 2):
                return 3000 - fps_pen
            # Ưu tiên số 2: HD dọc ~720x1280 (dung sai 10px)
            elif abs(h - 1280) <= DIMENSION_TOLERANCE_PX:
                return 2780 - fps_pen
            elif 1080 <= h < (1920 - DIMENSION_TOLERANCE_PX):
                return 1500 + min(h, 1270) - fps_pen
            elif 720 <= h < 1080:
                return 500 + h - fps_pen
            elif h > (1920 + DIMENSION_TOLERANCE_PX):
                return 100 - h - fps_pen  # 4K ưu tiên thấp nhất vì quá nặng
            return h - fps_pen

        v_files = v.get("video_files", [])
        portrait_files = [
            vf for vf in v_files
            if vf.get("width", 0) <= (vf.get("height", 0) + DIMENSION_TOLERANCE_PX) and (
                str(vf.get("link", "")).endswith(".mp4") or "mp4" in str(vf.get("file_type", ""))
            )
        ]
        files_to_check = portrait_files if portrait_files else v_files
        hd_files = sorted(files_to_check, key=_res_score, reverse=True)
        for best in hd_files:
            link = best.get("link")
            if link and not is_media_already_used(link, used_set):
                out_cand = {
                    "url": link,
                    "title": f"Pexels Video: {v.get('url', query)}",
                    "width": best.get("width", 1080),
                    "height": best.get("height", 1920),
                    "duration": v.get("duration", 0),
                    "media_type": "video",
                    "source": "pexels_video",
                    "thumbnail": v.get("image", ""),
                }
                for keep_k in ("web_url", "_vision_alternates", "curation_score", "curation_rationale"):
                    if keep_k in v:
                        out_cand[keep_k] = v[keep_k]
                return out_cand
        raw_u_no_qs = str(v.get("url") or "").split("?", 1)[0].lower()
        if raw_u_no_qs.endswith(".mp4"):
            if not is_media_already_used(v["url"], used_set):
                out_cand = {
                    "url": v["url"],
                    "title": v.get("title", f"Video: {query}"),
                    "width": v.get("width", 1080),
                    "height": v.get("height", 1920),
                    "duration": v.get("duration", 0),
                    "media_type": "video",
                    "source": v.get("source", "video"),
                    "thumbnail": v.get("thumbnail") or v.get("image", ""),
                }
                for keep_k in ("web_url", "_vision_alternates", "curation_score", "curation_rationale"):
                    if keep_k in v:
                        out_cand[keep_k] = v[keep_k]
                return out_cand
        for alt_u in (v.get("_vision_alternates") or []):
            if alt_u and not is_media_already_used(str(alt_u), used_set):
                out_cand = {
                    "url": str(alt_u),
                    "title": v.get("title", f"Pexels Video: {query}"),
                    "width": 1080,
                    "height": 1920,
                    "duration": v.get("duration", 0),
                    "media_type": "video",
                    "source": v.get("source", "pexels_video"),
                    "thumbnail": v.get("image", ""),
                }
                for keep_k in ("web_url", "_vision_alternates", "curation_score", "curation_rationale"):
                    if keep_k in v:
                        out_cand[keep_k] = v[keep_k]
                return out_cand
        return None

    @trace_custom_span("StockFetcher.search_best_video_clip", run_type="tool")
    def search_best_video_clip(
        self,
        query: str,
        used_urls: Optional[set] = None,
        visual_analysis: Optional[Dict[str, Any]] = None,
        narration: str = "",
        topic: str = "",
        _establishing_pass: bool = False,
        cancel_event: Optional[threading.Event] = None,
        deadline_ts: Optional[float] = None,
    ) -> Optional[Dict]:
        eff_cancel = cancel_event if cancel_event is not None else (visual_analysis or {}).get("_cancel_event")
        eff_deadline = deadline_ts if deadline_ts is not None else (visual_analysis or {}).get("_deadline_ts")
        if str((visual_analysis or {}).get("video_format") or "").strip().lower() == "long":
            long_qs: List[str] = []
            for rq in [query, *((visual_analysis or {}).get("search_queries") or [])]:
                rq_s = str(rq or "").strip()
                if rq_s and rq_s not in long_qs:
                    long_qs.append(rq_s)
            return self.search_landscape_16x9_video_clip(
                queries=long_qs or [query],
                used_urls=used_urls,
                topic=topic,
                narration=narration,
                primary_entity=str(
                    (visual_analysis or {}).get("core_visual_entity")
                    or (visual_analysis or {}).get("subject")
                    or ""
                ).strip(),
                cancel_event=eff_cancel,
                deadline_ts=eff_deadline,
            )

        from app.agents.curation.scene_context_analyzer_mixin import (
            _SCENE_CANCEL_CTX,
            _SCENE_DEADLINE_CTX,
            bind_scene_cancellation,
            is_scene_cancelled_ctx,
            remaining_scene_budget_ctx,
        )
        prev_c_ctx = _SCENE_CANCEL_CTX.get()
        prev_d_ctx = _SCENE_DEADLINE_CTX.get()
        bound_local_ctx = False
        local_cap_ts = time.monotonic() + (14.0 if _establishing_pass else 24.0)
        target_dl = (
            min(float(eff_deadline), local_cap_ts)
            if eff_deadline is not None
            else (min(prev_d_ctx, local_cap_ts) if prev_d_ctx is not None else local_cap_ts)
        )
        target_ev = eff_cancel if eff_cancel is not None else prev_c_ctx
        if prev_d_ctx is None or eff_deadline is not None or eff_cancel is not None:
            bind_scene_cancellation(cancel_event=target_ev, deadline_ts=target_dl)
            bound_local_ctx = True

        try:
            if is_scene_cancelled_ctx(min_remaining_sec=1.8):
                return None
            return self._search_best_video_clip_shorts_impl(
                query=query,
                used_urls=used_urls,
                visual_analysis=visual_analysis,
                narration=narration,
                topic=topic,
                _establishing_pass=_establishing_pass,
                cancel_event=target_ev,
                deadline_ts=target_dl,
            )
        finally:
            if bound_local_ctx:
                bind_scene_cancellation(cancel_event=prev_c_ctx, deadline_ts=prev_d_ctx)

    def _search_best_video_clip_shorts_impl(
        self,
        query: str,
        used_urls: Optional[set] = None,
        visual_analysis: Optional[Dict[str, Any]] = None,
        narration: str = "",
        topic: str = "",
        _establishing_pass: bool = False,
        cancel_event: Optional[threading.Event] = None,
        deadline_ts: Optional[float] = None,
    ) -> Optional[Dict]:
        from app.agents.curation.scene_context_analyzer_mixin import is_scene_cancelled_ctx, remaining_scene_budget_ctx
        from app.services.media.media_history import get_recent_media_urls, record_used_media
        from app.services.media.query_broadener import build_query_ladder, is_vague_single_word
        used = set(used_urls) if used_urls else set()
        recent_history = get_recent_media_urls(limit=150)
        used.update(recent_history)

        is_hook_vid = bool((visual_analysis or {}).get("is_hook_scene") or (visual_analysis or {}).get("scene_id") == 1)
        clean_q = clean_scene_search_query(query, max_words=3)
        words = clean_q.split() if clean_q else query.split()

        # Thang truy vấn: cụ thể -> đồng nghĩa phổ biến (caiman -> crocodile) -> cảnh toàn đúng bối cảnh
        # CẤM TUYỆT ĐỐI dùng cảnh toàn (establishing shot rừng/sông trống rỗng) cho Cảnh 1 (Hook 0.0s)!
        ladder = build_query_ladder(query, narration=narration, topic=topic)
        skip_est = bool((visual_analysis or {}).get("skip_establishing_pass"))
        establishing = [] if (_establishing_pass or is_hook_vid or skip_est) else ladder["establishing"]

        candidate_queries: List[str] = []

        def _push(q: str):
            q = (q or "").strip()
            if not q:
                return
            if re.search(r"\b(?:vs\.?|versus)\b", q, re.IGNORECASE):
                try:
                    from app.graph.tools.tool_schemas import build_single_entity_stock_queries
                    for sq_single in build_single_entity_stock_queries([q], subject_entity="", topic=topic, max_words=3):
                        if sq_single and sq_single not in candidate_queries:
                            candidate_queries.append(sq_single)
                    return
                except Exception:
                    pass
            if q not in candidate_queries:
                candidate_queries.append(q)

        _ctx_low = f"{topic} {query} {narration}".lower()
        has_preplanned_queries = bool(visual_analysis and visual_analysis.get("search_queries"))
        if not _establishing_pass and not has_preplanned_queries and not clean_q:
            try:
                from app.agents.visual_curation_agent import VisualCurationAgent
                llm_va = VisualCurationAgent().analyze_scene_context(
                    narration=narration or query,
                    topic=topic or query,
                )
                if isinstance(llm_va, dict) and llm_va.get("search_queries"):
                    visual_analysis = {**(visual_analysis or {}), **llm_va}
            except Exception as e_pre_va:
                logger.debug(f"[StockFetcher] Pre-API LLM query planner fallback: {e_pre_va}")
        if (visual_analysis or {}).get("clean_stock_only"):
            for sq_clean in ((visual_analysis or {}).get("search_queries") or []):
                _push(sq_clean)
            for pq_fallback in ["close up eyes dark lighting", "man intense stare", "silhouette person walking night", "dramatic portrait lighting"]:
                _push(pq_fallback)
        _push(clean_q)
        if len(words) >= 2:
            _push(" ".join(words[:2]))
        if visual_analysis and visual_analysis.get("search_queries"):
            for sq in visual_analysis["search_queries"]:
                _push(sq)
        for b in ladder["broadened"]:
            _push(b)
        if len(words) >= 2:
            _push(" ".join(words[-2:]))
        if not has_preplanned_queries and not ladder["broadened"] and not establishing and not _establishing_pass:
            for w in words:
                if not is_vague_single_word(w) and len(w) >= 4:
                    _push(w)
        _push(query)

        def _try_establishing_pass(reason: str) -> Optional[Dict]:
            """Cảnh toàn đúng địa điểm/bối cảnh thật trước khi nhờ AI vẽ ảnh (chỉ chạy khi còn >= 11s ngân sách)."""
            if not establishing or is_scene_cancelled_ctx(min_remaining_sec=11.0):
                return None
            if deadline_ts is not None and (deadline_ts - time.monotonic()) < 11.0:
                return None
            logger.info(f"[StockFetcher] {reason} -> thử cảnh toàn đúng bối cảnh: {establishing[:3]}")
            est_va = dict(visual_analysis or {})
            est_va["search_queries"] = list(establishing)
            est_va["subject"] = f"Bối cảnh thiên nhiên / địa hình ({', '.join(establishing[:2])})"
            est_va["core_visual_entity"] = establishing[0]
            est_va["expected_visual_content"] = (
                f"Cảnh toàn bối cảnh thiên nhiên trống trải ({', '.join(establishing[:2])}) "
                f"hoặc đúng chủ thể của '{topic or query}' — TUYỆT ĐỐI KHÔNG chứa loài động vật khác lạc đề"
            )
            est_va["visual_metaphor"] = (
                f"Pure landscape establishing shot of ({', '.join(establishing[:2])}) for '{topic or query}' with ZERO unrelated animal species"
            )
            est_va["mood"] = "bất kỳ (hệ thống sẽ tự chỉnh màu u tối theo kênh)"
            est_narr = (
                f"{narration or query}\n(Cảnh bối cảnh / establishing shot: CHỈ CHẤP NHẬN cảnh thiên nhiên/địa hình trống trải "
                f"({', '.join(establishing[:3])}) hoặc có đúng chủ thể của '{topic or query}'. "
                f"BẮT BUỘC TỪ CHỐI mọi clip quay loài động vật khác không có trong chủ đề '{topic or query}', người/vật hiện đại lạc đề, chữ/watermark.)"
            )
            return self.search_best_video_clip(
                query=establishing[0],
                used_urls=used_urls,
                visual_analysis=est_va,
                narration=est_narr,
                topic=topic,
                _establishing_pass=True,
                cancel_event=cancel_event,
                deadline_ts=deadline_ts,
            )

        collected_candidates = []

        space_words = ["space", "universe", "galaxy", "black hole", "nebula", "planet", "mars", "moon", "star", "telescope", "astronaut", "astronomy", "hố đen", "vũ trụ", "ngân hà", "hành tinh", "sao hỏa", "mặt trăng"]
        is_space = any(w in f"{query} {narration} {clean_q}".lower() for w in space_words)

        from app.services.media.entity_detector import is_incongruent_stock_image
        from app.services.media.media_history import is_media_already_used
        from app.services.media.query_broadener import conflicts_with_topic_biome
        allow_zoo_vid = any(w in _ctx_low for w in ("zoo", "cage", "aquarium", "captive"))
        _bad_captive_cues = () if allow_zoo_vid else ("-zoo", "zoo-", "cage", "enclosure", "fence", "captive", "captivity")

        # Bắn song song đa nhánh tìm kiếm Video B-Roll (Pexels + Pixabay + Wikimedia + NASA)
        try:
            from app.graph.parallel_search import run_langgraph_5way_stock_video_search
            branch_buckets = run_langgraph_5way_stock_video_search(
                stock_fetcher=self,
                candidate_queries=candidate_queries,
                include_pixabay=bool(self._pixabay_key),
                include_nasa=is_space,
                nasa_query=query,
            )
        except Exception as e_lg_v:
            logger.debug(f"[StockFetcher] LangGraph 5-Way Video fallback: {e_lg_v}")
            branch_buckets = [
                {"source": "pexels", "query": q_try, "items": self.search_videos(q_try, per_page=6, orientation="portrait")}
                for q_try in candidate_queries[:3]
            ]

        # Chỉ truy vấn bổ sung Wikimedia Commons Video khi Pexels/Pixabay trả về ít (< 4 mục) và vẫn còn >= 8s ngân sách
        total_initial_items = sum(len(b.get("items") or []) for b in branch_buckets)
        if (
            not (visual_analysis or {}).get("clean_stock_only")
            and candidate_queries
            and total_initial_items < 4
            and not is_scene_cancelled_ctx(min_remaining_sec=8.0)
        ):
            try:
                wiki_vids = self.search_wikimedia_videos(candidate_queries[0], per_page=4)
                if wiki_vids:
                    branch_buckets.append({"source": "wikimedia", "query": candidate_queries[0], "items": wiki_vids})
            except Exception:
                pass

        _DISTINCT_SPECIES_GUARD = (
            "giraffe", "elephant", "zebra", "rhino", "rhinoceros", "hippo", "hippopotamus",
            "buffalo", "bison", "camel", "dromedary", "lion", "tiger", "cheetah", "serval",
            "caracal", "hyena", "monkey", "gorilla", "chimpanzee", "baboon", "orangutan",
            "kangaroo", "koala", "panda", "sloth", "penguin", "flamingo", "ostrich", "pelican",
            "toucan", "parrot", "owl", "peacock", "deer", "moose", "elk", "reindeer", "gazelle",
            "antelope", "impala", "wildebeest", "horse", "stallion", "donkey", "cow", "cattle",
            "sheep", "goat", "pig", "boar", "dog", "puppy", "cat", "kitten", "rabbit", "squirrel",
            "hedgehog", "dolphin", "whale", "orca", "seal", "walrus", "manatee", "otter",
        )
        _allowed_species_ctx = f"{topic} {narration} {query} {clean_q} {' '.join(candidate_queries)}".lower()

        pxb_added = 0
        for b_res in branch_buckets:
            b_src = b_res.get("source", "pexels")
            q_try = b_res.get("query", query)
            for v in (b_res.get("items") or []):
                v_url = v.get("url")
                v_user = v.get("user") if isinstance(v.get("user"), dict) else {}
                v_creator_key = str(
                    v.get("creator_key")
                    or (f"pexels_user:{v_user.get('id')}" if v_user.get("id") else "")
                    or (f"pixabay_user:{v.get('user_id')}" if v.get("user_id") else "")
                ).strip()
                if v_creator_key:
                    v["creator_key"] = v_creator_key
                v_desc = v.get("tags") or v.get("title") or v.get("url") or ""
                v_desc_low = str(v_desc).lower()
                if any(
                    sp_w not in _allowed_species_ctx and re.search(rf"\b{re.escape(sp_w)}s?\b", v_desc_low)
                    for sp_w in _DISTINCT_SPECIES_GUARD
                ):
                    continue
                if (
                    v_url and not is_media_already_used(v_url, used)
                    and (not v_creator_key or v_creator_key not in used)
                    and not any(bw in str(v_desc).lower() for bw in _bad_captive_cues)
                    and not is_incongruent_stock_image(v_desc, topic=f"{topic} {q_try}".strip(), narration=narration)
                    and not conflicts_with_topic_biome(v_desc, topic)
                ):
                    if v not in collected_candidates:
                        desc_l = str(v_desc).lower()
                        is_portrait_v = (v.get("height") or 0) >= (v.get("width") or 0)
                        has_kw_match = any(w.lower() in desc_l for w in words if len(w) >= 3)
                        if b_src in ("pixabay", "wikimedia") and pxb_added < 3 and is_portrait_v and has_kw_match:
                            collected_candidates.insert(min(len(collected_candidates), 2 + pxb_added * 2), v)
                            pxb_added += 1
                        else:
                            collected_candidates.append(v)

        if not collected_candidates and establishing:
            est_res = _try_establishing_pass("Không tìm thấy clip cho truy vấn cụ thể")
            if est_res:
                return est_res

        if collected_candidates:
            try:
                from app.agents.curation.scene_context_analyzer_mixin import is_scene_cancelled_ctx
                if is_scene_cancelled_ctx():
                    return None
                from app.agents.visual_curation_agent import VisualCurationAgent
                curator = VisualCurationAgent()
                v_analysis = dict(visual_analysis or {
                    "subject": clean_q or query,
                    "topic": topic or query,
                    "search_queries": candidate_queries[:3],
                })
                v_analysis.setdefault("topic", topic or query)
                winner, score, rationale, should_ai = curator.curate_candidate_videos(
                    candidates=collected_candidates,
                    visual_analysis=v_analysis,
                    narration=narration or query,
                    used_urls=used,
                    topic=topic or query,
                )
                min_vid_score = 7.2 if is_hook_vid else 6.5
                if winner and score >= min_vid_score and not should_ai:
                    from app.services.media.media_history import extract_media_identity
                    with _stock_video_claim_lock:
                        live_used = set(used)
                        if used_urls is not None:
                            live_used.update(used_urls)
                        formatted_winner = self.format_video_candidate(winner, query=query, used=live_used)
                        if formatted_winner and formatted_winner.get("url"):
                            cand_win_urls = [
                                formatted_winner["url"],
                                *(formatted_winner.get("_vision_alternates") or []),
                            ]
                            chosen_win_url = None
                            rem_alt_urls = []
                            for cw_u in cand_win_urls:
                                if not cw_u:
                                    continue
                                if chosen_win_url is None and not is_media_already_used(cw_u, live_used):
                                    chosen_win_url = cw_u
                                elif cw_u != chosen_win_url and not is_media_already_used(cw_u, live_used):
                                    rem_alt_urls.append(cw_u)
                            if chosen_win_url:
                                win_creator_k = str(
                                    formatted_winner.get("creator_key") or winner.get("creator_key") or ""
                                ).strip()
                                if (
                                    chosen_win_url == winner.get("url")
                                    and win_creator_k
                                    and win_creator_k in live_used
                                ):
                                    chosen_win_url = rem_alt_urls.pop(0) if rem_alt_urls else None
                            if chosen_win_url:
                                is_primary_win = (chosen_win_url == winner.get("url"))
                                formatted_winner["url"] = chosen_win_url
                                formatted_winner["_vision_alternates"] = rem_alt_urls
                                formatted_winner["vision_curated"] = (rationale != "Rule-based curation passed")
                                formatted_winner["vision_score"] = round(float(score), 2)
                                ids = extract_media_identity(chosen_win_url)
                                if is_primary_win and formatted_winner.get("web_url"):
                                    ids.update(extract_media_identity(str(formatted_winner["web_url"])))
                                win_creator_k = str(
                                    formatted_winner.get("creator_key") or winner.get("creator_key") or ""
                                ).strip()
                                if is_primary_win and win_creator_k:
                                    ids.add(win_creator_k)
                                used.update(ids)
                                used.add(chosen_win_url)
                                if used_urls is not None:
                                    used_urls.update(ids)
                                    used_urls.add(chosen_win_url)
                            else:
                                formatted_winner = None
                    if formatted_winner and formatted_winner.get("url"):
                        record_used_media(formatted_winner["url"], query=query, source=formatted_winner.get("source", "curated_video"))
                        return formatted_winner
                if not is_hook_vid:
                    est_res = _try_establishing_pass(f"LLM Link & Vision Auditor từ chối clip cụ thể ({str(rationale)[:80]})")
                    if est_res:
                        return est_res
                logger.info(
                    f"[StockFetcher] 🚫 LLM Link & Vision Auditor từ chối các link stock video không khớp chủ thể "
                    f"(score={score:.1f} < {min_vid_score}, {rationale}) → Bỏ qua, không chọn ngẫu nhiên!"
                )
                return None
            except Exception as e:
                logger.debug(f"[StockFetcher] Lỗi khi gọi VisualCurationAgent: {e}")

        return None

    def search_landscape_16x9_video_clip(
        self,
        queries: List[str],
        used_urls: Optional[set] = None,
        topic: str = "",
        narration: str = "",
        prefer_pixabay: bool = False,
        primary_entity: str = "",
        cancel_event: Optional[Any] = None,
        deadline_ts: Optional[float] = None,
        claim_in_used: bool = True,
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        """
        Tìm kiếm Video B-Roll chuẩn ngang 16:9 (1920x1080 / 1280x720, width > height) đa nguồn
        (Pexels 16:9 HD + Pixabay 16:9 HD) kèm thẩm định ngữ nghĩa trực tiếp bằng Multimodal Gemini Vision
        (`VisualCurationAgent.curate_candidate_videos`) dành riêng cho Video Dài (Long-Form 16:9 Documentary).
        Hỗ trợ chạy song song không khóa (lock-free) và hủy sớm hợp tác (`cancel_event` / `deadline_ts`).
        """
        from app.services.media.media_history import (
            extract_media_identity,
            get_recent_media_urls,
            is_media_already_used,
            record_used_media,
        )
        from app.services.media.entity_detector import is_incongruent_stock_image
        from app.services.media.query_broadener import conflicts_with_topic_biome

        def _is_cancelled() -> bool:
            return (cancel_event is not None and cancel_event.is_set()) or (
                deadline_ts is not None and (deadline_ts - time.monotonic()) <= 2.0
            )

        if _is_cancelled():
            return None

        used = set(used_urls) if used_urls else set()
        used.update(get_recent_media_urls(limit=120))

        # Ưu tiên toàn bộ các truy vấn gốc khác nhau (bao gồm cả truy vấn tách đơn thực thể 'Entity A', 'Entity B')
        # trước khi chèn bản rút gọn 2 từ để kho Pexels/Pixabay luôn quét được cả 2 thực thể!
        primary_q_list: List[str] = []
        short_q_list: List[str] = []
        for raw_q in (queries or []):
            q_c = clean_scene_search_query(str(raw_q or ""), max_words=4) or str(raw_q or "").strip()
            if q_c and q_c not in primary_q_list:
                primary_q_list.append(q_c)
            q_short = clean_scene_search_query(str(raw_q or ""), max_words=2)
            if q_short and q_short not in primary_q_list and q_short not in short_q_list:
                short_q_list.append(q_short)
        search_q_list: List[str] = primary_q_list[:5] + [q for q in short_q_list if q not in primary_q_list][:3]

        _STOP_TOK = {
            "the", "and", "with", "from", "into", "over", "under", "slow",
            "motion", "cinematic", "dark", "shot", "view", "close", "wide",
            "video", "footage", "background", "scene", "high", "quality",
            "psychological", "abstract", "aesthetic", "lighting", "moody",
        }
        _WEAK_VERB_TOK = {
            "walking", "walk", "standing", "stand", "sitting", "sit",
            "looking", "look", "talking", "talk", "moving", "move",
            "running", "run", "holding", "hold", "person", "people",
        }
        _OFF_DOMAIN_ANIMALS_AND_ALIENS = (
            "snake", "python", "cobra", "viper", "lizard", "iguana", "frog", "toad",
            "turtle", "dog", "puppy", "cat", "kitten", "hamster", "parrot", "monkey",
            "gorilla", "chimp", "elephant", "giraffe", "zebra", "rhino", "hippo",
            "lion", "tiger", "leopard", "bear", "wolf", "fox", "deer", "stag", "buck",
            "roe", "fawn", "elk", "moose", "antler", "gazelle", "antelope", "horse",
            "stallion", "mare", "camel", "bison", "buffalo", "squirrel", "rabbit",
            "hare", "hedgehog", "bird", "owl", "eagle", "hawk", "crow", "pigeon",
            "seagull", "butterfly", "moth", "caterpillar", "snail", "cow", "pig",
            "sheep", "goat", "chicken", "rooster", "duck", "shark", "whale", "dolphin",
            "octopus", "squid", "crab", "lobster", "spider", "scorpion", "insect",
            "beetle", "ant", "bee", "wasp", "alien", "extraterrestrial", "martian",
            "ufo", "cartoon", "anime", "karate", "taekwondo", "kung fu", "zombie",
            "green screen", "greenscreen", "chroma key", "chromakey", "blue screen",
            "sprout", "twig", "flower", "blossom", "gardening", "botany", "agriculture",
            "farm", "barn", "pasture", "beach", "resort", "marina", "vacation",
            "tourist", "palm tree", "swimming pool", "bikini",
        )
        ctx_all_low = f"{topic} {primary_entity} {narration} {' '.join(search_q_list)}".lower()
        is_animal_or_scifi_ctx = any(
            w in ctx_all_low
            for w in (
                "animal", "wildlife", "predator", "prey", "species", "creature",
                "nature", "jungle", "forest", "ocean", "sea", "marine", "safari",
                "alien", "mars", "extraterrestrial", "ufo", "scifi", "sci-fi",
                "plant", "botany", "beach", "farm",
            )
        )

        def _score_candidate_text(desc_text: str, q_try: str) -> int:
            d_low = str(desc_text or "").lower()
            if any(gs in d_low for gs in ("green screen", "greenscreen", "chroma key", "chromakey", "blue screen")):
                return -100
            for cap_tok in (
                "zoo", "cage", "enclosure", "chain link", "captive",
                "zookeeper", "petting", "swimming pool", "goggles", "swimsuit",
            ):
                if cap_tok in d_low and cap_tok not in ctx_all_low:
                    return -100
            if is_incongruent_stock_image(d_low, topic=f"{topic} {q_try}".strip(), narration=narration):
                return -100
            if conflicts_with_topic_biome(d_low, topic):
                return -100
            for sp_g in (
                "giraffe", "elephant", "zebra", "rhino", "rhinoceros", "hippo", "hippopotamus",
                "buffalo", "bison", "camel", "lion", "tiger", "cheetah", "hyena", "kangaroo",
                "koala", "panda", "penguin", "flamingo", "ostrich", "deer", "moose", "gazelle",
                "wildebeest", "horse", "cow", "cattle", "sheep", "goat", "pig", "dog", "puppy",
                "kitten", "dolphin", "whale", "orca", "seal", "walrus",
            ):
                if sp_g not in ctx_all_low and re.search(rf"\b{re.escape(sp_g)}s?\b", d_low):
                    return -100
            if not is_animal_or_scifi_ctx:
                for bad_w in _OFF_DOMAIN_ANIMALS_AND_ALIENS:
                    if re.search(rf"\b{re.escape(bad_w)}s?\b", d_low) and bad_w not in q_try.lower():
                        return -100
            q_toks = [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", q_try) if w.lower() not in _STOP_TOK]
            strong_q_toks = [t for t in q_toks if t not in _WEAK_VERB_TOK]
            matched_q = [t for t in q_toks if re.search(rf"\b{re.escape(t)}", d_low)]
            matched_strong = [t for t in strong_q_toks if re.search(rf"\b{re.escape(t)}", d_low)]
            if q_toks and not matched_q:
                return -10
            if strong_q_toks and not matched_strong and len(matched_q) < 2:
                return -10
            return (len(matched_strong) * 30) + (len(matched_q) * 15)

        scored_pool: List[Tuple[int, Dict[str, Any]]] = []
        seen_cand_keys: set = set()

        def _collect_pixabay_for_q(q_try: str, boost: int = 0) -> None:
            px_vids = self.search_pixabay_videos(q_try, per_page=10)
            for pv in (px_vids or []):
                p_url = str(pv.get("url") or "")
                px_id_key = f"pixabay_vid_{pv.get('id') or p_url}"
                pw = int(pv.get("width") or 0)
                ph = int(pv.get("height") or 0)
                p_title = f"{pv.get('title') or ''} {pv.get('tags') or ''}"
                if (
                    not p_url
                    or pw <= ph
                    or ph < 720
                    or px_id_key in used
                    or px_id_key in seen_cand_keys
                    or is_media_already_used(p_url, used)
                ):
                    continue
                sc = _score_candidate_text(p_title, q_try)
                if sc < 0:
                    continue
                seen_cand_keys.add(px_id_key)
                res_bonus = (15 if ph >= 1080 else 5) + boost
                pv_copy = dict(pv)
                pv_copy["source"] = "pixabay_16x9_video"
                pv_copy["_cand_key"] = px_id_key
                scored_pool.append((sc + res_bonus, pv_copy))

        def _collect_pexels_for_q(q_try: str, boost: int = 0) -> None:
            vids = self.search_videos(q_try, per_page=12, orientation="landscape")
            for v in (vids or []):
                v_id_key = f"pexels_vid_{v.get('id') or v.get('url')}"
                if v_id_key in used or v_id_key in seen_cand_keys:
                    continue
                raw_v_url = str(v.get("url") or "")
                m_slug = re.search(r"/video/([a-zA-Z0-9-]+)-\d+/?", raw_v_url)
                slug_txt = m_slug.group(1).replace("-", " ") if m_slug else str(v.get("title") or raw_v_url)
                sc = _score_candidate_text(slug_txt, q_try)
                if sc < 0:
                    continue
                v_files = v.get("video_files") or []
                landscape_files = [
                    vf for vf in v_files
                    if int(vf.get("width") or 0) > int(vf.get("height") or 0)
                    and 720 <= int(vf.get("height") or 0) <= 1080
                    and int(vf.get("width") or 0) <= 1920
                    and (str(vf.get("link", "")).endswith(".mp4") or "mp4" in str(vf.get("file_type", "")))
                    and not is_media_already_used(str(vf.get("link") or ""), used)
                ]
                if not landscape_files:
                    continue
                seen_cand_keys.add(v_id_key)
                has_1080p = any(int(vf.get("height") or 0) >= 1080 for vf in landscape_files)
                res_bonus = (15 if has_1080p else 5) + boost
                v_copy = dict(v)
                v_copy["video_files"] = landscape_files
                v_copy["_cand_key"] = v_id_key
                scored_pool.append((sc + res_bonus, v_copy))

        for idx_q, q_try in enumerate(search_q_list[:5]):
            if _is_cancelled():
                return None
            use_px_first = prefer_pixabay if idx_q == 0 else ((idx_q % 2 == 1) ^ prefer_pixabay)
            pref_boost = 8 if idx_q == 0 else 0
            if use_px_first:
                _collect_pixabay_for_q(q_try, boost=pref_boost + 4)
                if not _is_cancelled():
                    _collect_pexels_for_q(q_try, boost=pref_boost)
            else:
                _collect_pexels_for_q(q_try, boost=pref_boost + 4)
                if not _is_cancelled():
                    _collect_pixabay_for_q(q_try, boost=pref_boost)
            if len(scored_pool) >= 12:
                break

        if not scored_pool or _is_cancelled():
            return None

        scored_pool.sort(key=lambda pair: pair[0], reverse=True)
        top_candidates = [pair[1] for pair in scored_pool[:8]]

        try:
            from app.agents.visual_curation_agent import VisualCurationAgent
            curator = VisualCurationAgent()
            # Ưu tiên mục tiêu thị giác riêng của phân cảnh (`search_q_list[0]`: thực thể chính, thực thể đối đầu, hoặc môi trường tự nhiên của cảnh)
            # đặt trong ngữ cảnh chủ đề tổng thể, tránh ép cứng mọi cảnh B-Roll môi trường phải xuất hiện duy nhất `primary_entity`!
            scene_visual_target = (
                (search_q_list[0] if search_q_list else "")
                or str(primary_entity or "").strip()
                or topic
            )
            prim_clean = str(primary_entity or "").strip()
            if prim_clean and scene_visual_target.lower() != prim_clean.lower():
                resolved_subj = f"{scene_visual_target} (B-Roll thuộc chủ đề: {topic or prim_clean})"
            else:
                resolved_subj = prim_clean or scene_visual_target or topic
            v_analysis_16x9 = {
                "video_format": "long",
                "subject": resolved_subj,
                "core_visual_entity": scene_visual_target,
                "expected_visual_content": (
                    f"Cảnh quay 16:9 thực tế sắc nét (0 chữ, 0 khách du lịch/thuyền du lịch, 0 thú nuôi/sở thú) "
                    f"thể hiện '{scene_visual_target}' hoặc thực thể/hệ sinh thái tự nhiên thuộc '{topic or prim_clean}'"
                ),
                "topic": topic or resolved_subj,
                "search_queries": search_q_list[:5],
            }
            if _is_cancelled():
                return None
            winner, score, rationale, should_ai = curator.curate_candidate_videos(
                candidates=top_candidates,
                visual_analysis=v_analysis_16x9,
                narration=narration or resolved_subj,
                used_urls=used,
                topic=topic or resolved_subj,
            )
            if winner and score >= 7.0 and not should_ai and winner.get("url"):
                chosen_url = str(winner["url"])
                alt_list = [str(a) for a in (winner.get("_vision_alternates") or []) if a]
                if is_media_already_used(chosen_url, used):
                    for alt_u in alt_list:
                        if not is_media_already_used(alt_u, used):
                            chosen_url = alt_u
                            break
                if chosen_url and not is_media_already_used(chosen_url, used):
                    src_out = str(winner.get("source") or "pexels_16x9_video")
                    if "pixabay" in src_out.lower():
                        src_out = "pixabay_16x9_video"
                    else:
                        src_out = "pexels_16x9_video"
                    q_used = search_q_list[0] if search_q_list else (topic or "16x9_broll")
                    if claim_in_used:
                        record_used_media(chosen_url, query=q_used, source=src_out)
                        ids = extract_media_identity(chosen_url)
                        used.update(ids)
                        used.add(chosen_url)
                        if winner.get("web_url"):
                            used.add(str(winner["web_url"]))
                        if used_urls is not None:
                            used_urls.update(ids)
                            used_urls.add(chosen_url)
                            if winner.get("web_url"):
                                used_urls.add(str(winner["web_url"]))
                    alt_clean = [u for u in alt_list if u != chosen_url]
                    return {
                        "url": chosen_url,
                        "web_url": str(winner.get("web_url") or ""),
                        "alternate_urls": alt_clean,
                        "_vision_alternates": alt_clean,
                        "query_used": q_used,
                        "title": str(winner.get("title") or f"16:9 HD: {q_used}"),
                        "width": int(winner.get("width") or 1920),
                        "height": int(winner.get("height") or 1080),
                        "duration": float(winner.get("duration") or 10.0),
                        "media_type": "video",
                        "source": src_out,
                        "vision_curated": True,
                        "vision_score": round(float(score), 2),
                    }
            logger.info(
                f"[StockFetcher 16:9] 🚫 Gemini Vision từ chối clip stock 16:9 không khớp ngữ cảnh "
                f"(score={score:.1f}, reason={str(rationale)[:90]}) → Chuyển sang Chapter Pool 16:9 / Ảnh thực tế!"
            )
            return None
        except Exception as e_vis_16x9:
            logger.debug(f"[StockFetcher 16:9] Vision curation error: {e_vis_16x9}")
            return None
