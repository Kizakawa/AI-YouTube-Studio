"""
app.services.media.stock_fetcher — Trích xuất hình ảnh & video stock đa nguồn (Pexels, Pixabay, NASA, Openverse).

Hỗ trợ:
1. Xoay vòng đa Pexels API Key tự động khi gặp rate-limit.
2. Tìm kiếm video dọc (9:16 portrait) tối ưu cho Shorts.
3. Tích hợp VisualCurationAgent để tuyển chọn video ăn khớp bối cảnh thay vì chọn ngẫu nhiên.
4. Tự động mở rộng từ khóa theo nhóm concept khi thiếu ứng viên.
"""

import os
import time
import urllib.parse
import itertools
import logging
import requests
from typing import List, Dict, Optional, Any

from config.settings import settings
from app.services.media.entity_detector import clean_scene_search_query

logger = logging.getLogger(__name__)


# ── MoneyPrinterTurbo Standard: 10px Dimension Tolerance ──
# Bù sai số biên độ crop/nén video (ví dụ 1072x1916 hay 718x1278) để không bị lọc nhầm hoặc mất điểm tối ưu
DIMENSION_TOLERANCE_PX: int = 10


class StockFetcher:
    """Fetcher chuyên trách kết nối và trích xuất stock footage & photos chất lượng cao."""
    DIMENSION_TOLERANCE_PX: int = DIMENSION_TOLERANCE_PX

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
        if self._key_cycle:
            return next(self._key_cycle)
        return None

    def search_videos(self, query: str, per_page: int = 4, orientation: str = "portrait", page: Optional[int] = None) -> List[Dict]:
        """Tìm kiếm video trên Pexels theo từ khóa với tỷ lệ dọc (portrait 9:16) có cơ chế retry, xoay key và pagination jitter."""
        import random
        key = self._get_active_key()
        if not key:
            return []

        target_page = page if page is not None else 1
        q_low = (query or "").lower()
        if "honey badger" in q_low:
            query = "cobra snake wildlife" if ("snake" in q_low or "venom" in q_low) else "leopard wildlife savanna"
            q_low = query.lower()
        allow_snow_zoo = any(w in q_low for w in ("zoo", "cage", "snow", "winter", "arctic", "polar", "ice", "penguin", "seal", "husky"))
        is_wild_predator_q = any(
            w in q_low
            for w in ("lion", "tiger", "leopard", "jaguar", "cheetah", "panther", "cougar", "hippo", "crocodile", "caiman", "alligator", "predator", "wildlife", "savanna", "hyena", "wolf", "bear", "snake", "cobra", "eagle")
        ) and not any(w in q_low for w in ("cat", "kitten", "kitty", "dog", "puppy", "pet", "stray"))
        _bad_slugs_list = ["-zoo", "zoo-", "zoological", "cage", "enclosure", "fence", "captive", "captivity", "snow", "winter", "blizzard", "arctic", "aquarium", "concrete-pool", "swimming-pool"]
        if is_wild_predator_q:
            _bad_slugs_list.extend([
                "-cat-", "-cats-", "video/a-cat-", "video/two-cats-", "video/cat-", "kitten", "kittens", "kitty", "tabby",
                "house-cat", "domestic-cat", "pet-cat", "black-cat", "white-cat", "orange-cat", "ginger-cat",
                "-dog-", "-dogs-", "puppy", "puppies", "window-sill", "living-room", "bedroom", "sofa", "couch",
            ])
        _bad_slugs = tuple(_bad_slugs_list)

        def _filter_vids(raw_vids: List[Dict]) -> List[Dict]:
            if allow_snow_zoo:
                return raw_vids
            return [
                v for v in (raw_vids or [])
                if not any(bs in str(v.get("url", "")).lower() for bs in _bad_slugs)
            ]

        for attempt in range(2):
            try:
                headers = {"Authorization": key}
                params = {
                    "query": query,
                    "per_page": max(per_page, 10),
                    "orientation": orientation,
                    "page": target_page,
                }
                res = requests.get(self.base_url, headers=headers, params=params, timeout=12)
                if res.status_code == 200:
                    vids = _filter_vids(res.json().get("videos", []))
                    if not vids and target_page > 1:
                        params["page"] = 1
                        res1 = requests.get(self.base_url, headers=headers, params=params, timeout=10)
                        if res1.status_code == 200:
                            vids = _filter_vids(res1.json().get("videos", []))
                    return vids[:per_page] if len(vids) > per_page else vids
                elif res.status_code in (401, 429):
                    logger.warning(f"Pexels key bị giới hạn ({res.status_code}), chuyển key...")
                    key = self._get_active_key()
            except Exception as e:
                logger.debug(f"Pexels search_videos attempt {attempt+1} error for '{query}': {e}")
                time.sleep(1.0)
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
        if "honey badger" in (query or "").lower():
            qn_low = f"{query} {narration}".lower()
            if "snake" in qn_low or "venom" in qn_low:
                query = "cobra snake wildlife"
            elif any(w in qn_low for w in ("snarl", "attack", "lunge", "teeth", "claw", "jaws", "fury")):
                query = "leopard roaring teeth"
            elif any(w in qn_low for w in ("walk", "undefeated", "savanna", "bush")):
                query = "leopard hunting savanna"
            else:
                query = "african leopard closeup"
        allow_snow_zoo = any(
            w in ctx_photo_low
            for w in ("zoo", "cage", "snow", "winter", "arctic", "polar", "ice", "penguin", "seal", "husky")
        )
        is_wildlife_ctx = any(
            w in ctx_photo_low
            for w in ("leopard", "badger", "lion", "tiger", "cheetah", "jaguar", "hyena", "wolf", "bear", "snake", "cobra", "predator", "wildlife", "savanna")
        )

        try:
            headers = {"Authorization": key}
            params = {
                "query": query,
                "per_page": max(per_page * 3, 12),  # Lấy dư để lọc bỏ ảnh trùng lặp và ảnh lạc đề
                "orientation": orientation,
                "page": target_page,
            }
            res = requests.get(self.photo_url, headers=headers, params=params, timeout=10)
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
                    img_url = src.get("portrait") or src.get("large2x") or src.get("large") or src.get("original")
                    if not img_url:
                        continue

                    p_id = str(p.get("id", ""))
                    alt_text = p.get("alt", "") or query
                    alt_low = f"{alt_text} {p.get('url', '')}".lower()

                    if not allow_snow_zoo and any(
                        sz_w in alt_low
                        for sz_w in ("-zoo", "zoo-", "zoological", " in a zoo", " at the zoo", "cage", "enclosure", "fence", "in snow", "in winter", "snowy")
                    ):
                        continue
                    if is_wildlife_ctx and any(
                        bw in alt_low
                        for bw in (
                            "honeycomb", "beeswax", "beehive", "beekeeper", "woman", "man ", "person", "people",
                            "girl", "boy", "sheep", "cow", "goat", "black cat", "domestic cat", "house cat",
                            "kitten", "puppy", "dark skin", "fireworks", "boat", "celebration", "asphalt",
                            "suitcase", "wicker basket", "warrior",
                        )
                    ):
                        continue

                    # Lọc ảnh lạc quẻ (văn phòng, cafe, tiệc tùng lọt vào chủ đề khác)
                    if is_incongruent_stock_image(alt_text, topic=topic or query, narration=narration):
                        continue

                    # Lọc trùng lặp qua used_urls
                    if used_urls:
                        if is_media_already_used(img_url, used_urls) or p_id in used_urls or f"pexels:{p_id}" in used_urls:
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
                    })
                    if len(results) >= per_page:
                        break
                return results
        except Exception as e:
            logger.debug(f"Pexels search_photos error for '{query}': {e}")
        return []

    _pixabay_rate_limited_until: float = 0.0

    def search_pixabay_videos(self, query: str, per_page: int = 4) -> List[Dict]:
        """Tìm kiếm video clip trên Pixabay API (kho video 3D, CGI, chuyển động trừu tượng)."""
        import time as _time
        key = self._pixabay_key
        if not key or key == "your_pixabay_api_key_here" or _time.time() < StockFetcher._pixabay_rate_limited_until:
            return []

        try:
            if "honey badger" in (query or "").lower():
                query = "leopard wildlife"
            clean_q = clean_scene_search_query(query, max_words=3) or query
            url = f"https://pixabay.com/api/videos/?key={key}&q={urllib.parse.quote(clean_q)}&video_type=all&per_page={max(per_page, 3)}&safesearch=true"
            res = requests.get(url, timeout=6)
            if res.status_code == 429:
                StockFetcher._pixabay_rate_limited_until = _time.time() + 180.0
                return []
            if res.status_code == 200:
                hits = res.json().get("hits", [])
                results = []
                for h in hits:
                    vids = h.get("videos", {})
                    v_info = vids.get("large") or vids.get("medium") or vids.get("small")
                    if v_info and v_info.get("url"):
                        thumb = v_info.get("thumbnail") or (vids.get("medium") or {}).get("thumbnail") or (
                            f"https://i.vimeocdn.com/video/{h.get('picture_id', '')}_640x360.jpg" if h.get("picture_id") else ""
                        )
                        results.append({
                            "url": v_info["url"],
                            "title": f"Pixabay Video: {h.get('tags', clean_q)}",
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

    def search_pixabay_photos(self, query: str, per_page: int = 4) -> List[Dict]:
        """Tìm kiếm ảnh chất lượng cao trên Pixabay API."""
        import time as _time
        key = self._pixabay_key
        if not key or key == "your_pixabay_api_key_here" or _time.time() < StockFetcher._pixabay_rate_limited_until:
            return []

        try:
            if "honey badger" in (query or "").lower():
                query = "leopard wildlife"
            clean_q = clean_scene_search_query(query, max_words=3) or query
            url = f"https://pixabay.com/api/?key={key}&q={urllib.parse.quote(clean_q)}&image_type=photo&orientation=vertical&per_page={max(per_page, 3)}&safesearch=true&order=popular"
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
        if "honey badger" in full_ctx_low:
            hb_qs = (
                ["cobra snake wildlife", "leopard roaring teeth", "leopard hunting savanna"]
                if ("snake" in full_ctx_low or "venom" in full_ctx_low)
                else ["leopard roaring teeth", "leopard hunting savanna", "african leopard closeup", "leopard wildlife"]
            )
            queries = hb_qs + [q for q in queries if "honey" not in q.lower() and q not in hb_qs]
        creature_mode = bool(va.get("creature_mode"))
        creature_qs = [q for q in (va.get("creature_queries") or []) if q]
        clean_stock_only = bool(va.get("clean_stock_only"))
        _allow_snow_zoo = any(
            w in full_ctx_low
            for w in ("snow", "winter", "arctic", "polar", "ice", "zoo", "cage")
        )
        _is_wildlife_photo_ctx = any(
            w in full_ctx_low
            for w in ("leopard", "badger", "lion", "tiger", "cheetah", "jaguar", "hyena", "wolf", "bear", "snake", "cobra", "predator", "wildlife", "savanna")
        )

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
                if not _allow_snow_zoo and any(
                    sz_w in desc_low
                    for sz_w in ("-zoo", "zoo-", "zoological", " in a zoo", " at the zoo", "cage", "enclosure", "in snow", "in winter", "snowy")
                ):
                    continue
                if _is_wildlife_photo_ctx and any(
                    bw in desc_low
                    for bw in (
                        "honeycomb", "beeswax", "beehive", "beekeeper", "woman", "man ", "person", "people",
                        "girl", "boy", "sheep", "cow", "goat", "black cat", "domestic cat", "house cat",
                        "kitten", "puppy", "dark skin", "fireworks", "boat", "celebration", "asphalt",
                        "suitcase", "wicker basket", "warrior",
                    )
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

        if not clean_stock_only and (not bypass_copyright and not creature_mode):
            _fetch_licensed_stock_photos()

        web_fetcher = None
        try:
            from app.services.media.web_image_fetcher import WebImageFetcher, web_image_search_enabled
            if web_image_search_enabled() and not clean_stock_only and (bypass_copyright or creature_mode or len(candidates) < 6):
                web_fetcher = WebImageFetcher()
                google_qs = [q for q in (va.get("google_image_queries") or []) if q]
                pref_engines = va.get("preferred_engines")
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

        # 3. Gọi Pexels / Pixabay / Openverse bổ sung nếu chưa chạy ở bước 2 và tổng ứng viên < 6
        if not clean_stock_only and (bypass_copyright or creature_mode) and len(candidates) < 6:
            _fetch_licensed_stock_photos()

        # Lọc watermark/ảnh nhỏ + xếp hạng theo độ bắt mắt, chủ thể, bố cục dọc, độ phân giải
        candidates = prefilter_candidates(
            candidates, subject=subject or queries[0], limit=16,
            creature_mode=creature_mode, max_per_source=14 if (creature_mode or bypass_copyright) else 8,
        )

        if not candidates:
            return None

        if subject and not va.get("subject"):
            va = {**va, "subject": subject}

        is_hook_photo = bool(va.get("is_hook_scene") or va.get("scene_id") == 1)
        winner = None
        score = 0.0
        try:
            from app.agents.visual_curation_agent import VisualCurationAgent
            winner, score, rationale, should_ai = VisualCurationAgent().curate_candidate_photos(
                candidates=candidates,
                visual_analysis=va or None,
                narration=narration or query,
                topic=topic,
                visual_keyword=query,
                used_urls=used,
            )
            if winner is not None:
                winner["vision_score"] = float(score or 0.0)
            if not winner:
                winner = candidates[0]
        except Exception as e:
            logger.debug(f"[StockFetcher] Lỗi curate ảnh: {e}")

        if not winner:
            winner = candidates[0]
        try:
            from app.agents.media_agent import register_download_fallbacks
            register_download_fallbacks(winner, candidates)
            distinct_alts: List[str] = []
            win_u = winner.get("url")
            win_fb = winner.get("fallback_url")
            for va_u in (winner.get("_vision_alternates") or []):
                if va_u and va_u not in (win_u, win_fb) and not is_media_already_used(va_u, used) and va_u not in distinct_alts:
                    distinct_alts.append(va_u)
            for c in candidates:
                cu = c.get("url")
                if cu and cu not in (win_u, win_fb) and not is_media_already_used(cu, used) and cu not in distinct_alts:
                    distinct_alts.append(cu)
                if len(distinct_alts) >= 12:
                    break
            winner["alternate_urls"] = distinct_alts[:12]
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

        if v.get("source") in ("nasa_video", "pixabay_video") and v.get("url"):
            if not is_media_already_used(v["url"], used_set):
                return {
                    "url": v["url"],
                    "title": v.get("title", f"Video: {query}"),
                    "width": v.get("width", 1080),
                    "height": v.get("height", 1920),
                    "duration": v.get("duration", 0),
                    "media_type": "video",
                    "source": v.get("source", "video"),
                    "thumbnail": v.get("thumbnail") or v.get("image", ""),
                }
            return None

        def _res_score(vf):
            h = vf.get("height", 0)
            w = vf.get("width", 0)
            # Ưu tiên số 1: Full HD dọc ~1080x1920 (dung sai 10px)
            if abs(h - 1920) <= DIMENSION_TOLERANCE_PX and abs(w - 1080) <= (DIMENSION_TOLERANCE_PX * 2):
                return 3000
            # Ưu tiên số 2: HD dọc ~720x1280 (dung sai 10px)
            elif abs(h - 1280) <= DIMENSION_TOLERANCE_PX:
                return 2780
            elif 1080 <= h < (1920 - DIMENSION_TOLERANCE_PX):
                return 1500 + min(h, 1270)
            elif 720 <= h < 1080:
                return 500 + h
            elif h > (1920 + DIMENSION_TOLERANCE_PX):
                return 100 - h  # 4K ưu tiên thấp nhất vì quá nặng
            return h

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
                return {
                    "url": link,
                    "title": f"Pexels Video: {v.get('url', query)}",
                    "width": best.get("width", 1080),
                    "height": best.get("height", 1920),
                    "duration": v.get("duration", 0),
                    "media_type": "video",
                    "source": "pexels_video",
                    "thumbnail": v.get("image", ""),
                }
        if v.get("url", "").endswith(".mp4"):
            if not is_media_already_used(v["url"], used_set):
                return {
                    "url": v["url"],
                    "title": v.get("title", f"Video: {query}"),
                    "width": v.get("width", 1080),
                    "height": v.get("height", 1920),
                    "duration": v.get("duration", 0),
                    "media_type": "video",
                    "source": v.get("source", "video"),
                    "thumbnail": v.get("thumbnail") or v.get("image", ""),
                }
        return None

    def search_best_video_clip(
        self,
        query: str,
        used_urls: Optional[set] = None,
        visual_analysis: Optional[Dict[str, Any]] = None,
        narration: str = "",
        topic: str = "",
        _establishing_pass: bool = False,
    ) -> Optional[Dict]:
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
        establishing = [] if (_establishing_pass or is_hook_vid) else ladder["establishing"]

        candidate_queries: List[str] = []

        def _push(q: str):
            q = (q or "").strip()
            if q and q not in candidate_queries:
                candidate_queries.append(q)

        _ctx_low = f"{topic} {query} {narration}".lower()
        if (visual_analysis or {}).get("clean_stock_only"):
            for sq_clean in ((visual_analysis or {}).get("search_queries") or []):
                _push(sq_clean)
            for pq_fallback in ["close up eyes dark lighting", "man intense stare", "silhouette person walking night", "dramatic portrait lighting"]:
                _push(pq_fallback)
        if "honey badger" in _ctx_low:
            for hq in ["leopard wildlife africa", "african savanna predator", "leopard stalking prey", "badger wildlife"]:
                _push(hq)
        if visual_analysis and visual_analysis.get("search_queries"):
            for sq in visual_analysis["search_queries"]:
                if "honey badger" in sq.lower():
                    continue
                _push(sq)
        _push(clean_q)
        for b in ladder["broadened"]:
            _push(b)
        if len(words) >= 2:
            _push(" ".join(words[:2]))
        # Từ đơn chỉ dùng khi KHÔNG có thang thay thế: từ đơn như 'black' / 'swimming' / 'amazon'
        # trả về clip ngẫu nhiên (hộp hàng Amazon, người bơi hồ bơi...) làm loãng bể ứng viên.
        _COMPOUND_NO_SPLIT_WORDS = {"honey", "sea", "red", "black", "white", "blue", "golden", "king", "tasmanian", "devil", "komodo", "dragon", "mantis", "pistol", "box", "cone", "bald", "harpy", "great", "giant", "african", "wild", "water", "tree", "mountain", "snow", "clouded", "painted", "spotted"}
        if not ladder["broadened"] and not establishing and not _establishing_pass:
            for w in words:
                if not is_vague_single_word(w) and w.lower() not in _COMPOUND_NO_SPLIT_WORDS:
                    _push(w)
        _push(query)

        # Bộ từ khóa Động vật chung (leopard / tiger / lion...) chỉ là phương án CUỐI:
        # trước đây chèn lên đầu danh sách nên video cá sấu Amazon lại toàn ứng viên hổ / sư tử.
        try:
            from app.agents.wildlife_visual_dispatcher_agent import WildlifeVisualDispatcherAgent
            w_agent = WildlifeVisualDispatcherAgent()
            is_wl, _, subg, _ = w_agent.detect_wildlife_elements(topic=query, narration=narration)
            if is_wl and not establishing:
                for cw in w_agent.get_curated_video_search_queries(subgenre=subg, narration=narration):
                    _push(cw)
        except Exception:
            pass

        def _try_establishing_pass(reason: str) -> Optional[Dict]:
            """Cảnh toàn đúng địa điểm/bối cảnh thật (rừng Amazon, sông rừng rậm...) trước khi nhờ AI vẽ ảnh."""
            if not establishing:
                return None
            logger.info(f"[StockFetcher] {reason} -> thử cảnh toàn đúng bối cảnh: {establishing[:3]}")
            est_va = dict(visual_analysis or {})
            est_va["search_queries"] = list(establishing)
            est_va["visual_metaphor"] = (
                f"Establishing shot of the real location ({', '.join(establishing[:2])}) for the topic '{topic or query}'"
            )
            # Tâm trạng sẽ được xử lý bằng color grading theo niche -> không loại clip chỉ vì ánh sáng / màu
            est_va["mood"] = "bất kỳ (hệ thống sẽ tự chỉnh màu u tối theo kênh)"
            est_narr = (
                f"{narration or query}\n(Cảnh bối cảnh / establishing shot: CHẤP NHẬN mọi cảnh toàn đúng LOẠI địa hình "
                f"của chủ đề ({', '.join(establishing[:3])}). Người xem KHÔNG phân biệt được rừng mưa / sông rừng ở quốc gia nào, "
                f"nên địa danh trong mô tả clip (Bangladesh, Thái Lan, Costa Rica...) KHÔNG phải lý do loại. "
                f"Không bắt buộc thấy chính xác chủ thể trong lời thoại và không bắt buộc đúng tâm trạng/ánh sáng. "
                f"Chỉ loại clip có người/vật hiện đại lạc đề, chữ/watermark, hoặc sai loại địa hình.)"
            )
            return self.search_best_video_clip(
                query=establishing[0],
                used_urls=used_urls,
                visual_analysis=est_va,
                narration=est_narr,
                topic=topic,
                _establishing_pass=True,
            )

        collected_candidates = []

        space_words = ["space", "universe", "galaxy", "black hole", "nebula", "planet", "mars", "moon", "star", "telescope", "astronaut", "astronomy", "hố đen", "vũ trụ", "ngân hà", "hành tinh", "sao hỏa", "mặt trăng"]
        is_space = any(w in f"{query} {narration} {clean_q}".lower() for w in space_words)

        from app.services.media.entity_detector import is_incongruent_stock_image
        from app.services.media.media_history import is_media_already_used
        from app.services.media.query_broadener import conflicts_with_topic_biome
        _bad_wildlife_list = []
        if "badger" in _ctx_low or "leopard" in _ctx_low:
            _bad_wildlife_list.extend(["honeycomb", "beekeeper", "beekeeping", "apiculture", "beehive", "jar-of-honey", "pouring-honey", "bird-feeder"])
        if any(
            w in _ctx_low
            for w in ("lion", "tiger", "leopard", "jaguar", "cheetah", "panther", "cougar", "hippo", "crocodile", "caiman", "alligator", "predator", "wildlife", "savanna", "hyena", "wolf", "bear", "snake", "cobra", "eagle")
        ) and not any(w in _ctx_low for w in ("cat", "kitten", "kitty", "dog", "puppy", "pet", "stray")):
            _bad_wildlife_list.extend([
                "-cat-", "-cats-", "video/a-cat-", "video/two-cats-", "video/cat-", "kitten", "kittens", "kitty", "tabby",
                "house-cat", "domestic-cat", "pet-cat", "black-cat", "white-cat", "orange-cat", "ginger-cat",
                "-dog-", "-dogs-", "puppy", "puppies", "window-sill", "living-room", "bedroom", "sofa", "couch",
            ])
        if not any(w in _ctx_low for w in ("snow", "winter", "arctic", "polar", "ice", "zoo", "cage", "penguin", "seal", "husky")):
            _bad_wildlife_list.extend(["-zoo", "zoo-", "cage", "enclosure", "fence", "captive", "captivity", "snow", "winter", "snowy", "arctic", "blizzard", "concrete-pool", "swimming-pool"])
        _bad_wildlife_cues = tuple(_bad_wildlife_list)

        # Bắn song song 5 nhánh tìm kiếm Video B-Roll (Pexels + Pixabay + NASA) qua LangGraph Send API + operator.add
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

        pxb_added = 0
        for b_res in branch_buckets:
            b_src = b_res.get("source", "pexels")
            q_try = b_res.get("query", query)
            for v in (b_res.get("items") or []):
                v_url = v.get("url")
                v_desc = v.get("tags") or v.get("title") or v.get("url") or ""
                if (
                    v_url and not is_media_already_used(v_url, used)
                    and not any(bw in str(v_desc).lower() for bw in _bad_wildlife_cues)
                    and not is_incongruent_stock_image(v_desc, topic=f"{topic} {q_try}".strip(), narration=narration)
                    and not conflicts_with_topic_biome(v_desc, topic)
                ):
                    if v not in collected_candidates:
                        if b_src == "pixabay" and pxb_added < 3:
                            collected_candidates.insert(min(len(collected_candidates), 1 + pxb_added * 2), v)
                            pxb_added += 1
                        else:
                            collected_candidates.append(v)

        if not collected_candidates and establishing:
            est_res = _try_establishing_pass("Không tìm thấy clip cho truy vấn cụ thể")
            if est_res:
                return est_res

        if not collected_candidates and not is_hook_vid:
            combined_text = f"{query} {narration} {clean_q}".lower()
            theme_fallbacks = []
            if any(w in combined_text for w in ["animal", "wildlife", "bird", "lion", "tiger", "leopard", "cheetah", "chim", "báo", "sư tử", "hổ", "động vật", "thú"]):
                theme_fallbacks.extend(["leopard wildlife savanna", "african predator hunting", "lion wildlife savanna", "cheetah wildlife running"])
            if any(w in combined_text for w in ["ocean", "sea", "water", "underwater", "deep", "biển", "nước", "sóng"]):
                theme_fallbacks.extend(["underwater ocean", "ocean waves", "deep water", "dark ocean"])
            if any(w in combined_text for w in ["space", "galaxy", "stars", "universe", "planet", "vũ trụ"]):
                theme_fallbacks.extend(["space galaxy", "universe stars", "nebula space", "planet in space"])
            if any(w in combined_text for w in ["cyberpunk", "tech", "technology", "neon", "ai", "robot"]):
                theme_fallbacks.extend(["cyberpunk city", "neon technology", "futuristic city", "computer code"])
            if any(w in combined_text for w in ["mind", "brain", "think", "psychology", "alone", "stress"]):
                theme_fallbacks.extend(["person intense expression", "two people conversation", "dramatic lighting portrait", "person walking city street"])

            for tf in theme_fallbacks:
                videos = self.search_videos(tf, per_page=4, orientation="portrait")
                for v in videos:
                    v_url = v.get("url")
                    from app.services.media.media_history import is_media_already_used
                    if v_url and not is_media_already_used(v_url, used) and v not in collected_candidates:
                        collected_candidates.append(v)
                if len(collected_candidates) >= 4:
                    break

        if collected_candidates and (visual_analysis or narration):
            try:
                from app.agents.visual_curation_agent import VisualCurationAgent
                curator = VisualCurationAgent()
                v_analysis = visual_analysis or curator.analyze_scene_context(
                    narration=narration or query,
                    topic=query,
                )
                winner, score, rationale, should_ai = curator.curate_candidate_videos(
                    candidates=collected_candidates,
                    visual_analysis=v_analysis,
                    narration=narration or query,
                    used_urls=used,
                )
                min_vid_score = 7.2 if is_hook_vid else 4.8
                if winner and score >= min_vid_score:
                    formatted_winner = self.format_video_candidate(winner, query=query, used=used)
                    if formatted_winner and formatted_winner.get("url"):
                        record_used_media(formatted_winner["url"], query=query, source=formatted_winner.get("source", "curated_video"))
                        from app.services.media.media_history import extract_media_identity
                        ids = extract_media_identity(formatted_winner["url"])
                        used.update(ids)
                        if used_urls is not None:
                            used_urls.update(ids)
                        return formatted_winner
                if should_ai or is_hook_vid:
                    if not is_hook_vid:
                        est_res = _try_establishing_pass(f"AI Vision từ chối clip cụ thể ({str(rationale)[:80]})")
                        if est_res:
                            return est_res
                    logger.info(
                        f"[StockFetcher] {'🔥 Cảnh 1 Hook' if is_hook_vid else 'VisualCurationAgent'} từ chối stock video chưa đủ WOW "
                        f"(score={score:.1f} < {min_vid_score}, {rationale}) → chuyển sang 8K AI Masterpiece!"
                    )
                    return None
            except Exception as e:
                logger.debug(f"[StockFetcher] Lỗi khi gọi VisualCurationAgent: {e}")

        if is_hook_vid:
            return None

        candidates_found = []
        for v in collected_candidates:
            formatted = StockFetcher.format_video_candidate(v, query=query, used=used)
            if formatted:
                candidates_found.append(formatted)
            if len(candidates_found) >= 5:
                break

        if candidates_found:
            import random
            top_pool = candidates_found[:min(len(candidates_found), 3)]
            chosen = random.choice(top_pool)
            if chosen.get("url"):
                record_used_media(chosen["url"], query=query, source=chosen.get("source", "pexels_video"))
                from app.services.media.media_history import extract_media_identity
                ids = extract_media_identity(chosen["url"])
                used.update(ids)
                if used_urls is not None:
                    used_urls.update(ids)
                return chosen

        return None

