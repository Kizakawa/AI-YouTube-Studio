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


class StockFetcher:
    """Fetcher chuyên trách kết nối và trích xuất stock footage & photos chất lượng cao."""

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

    def search_videos(self, query: str, per_page: int = 4, orientation: str = "portrait") -> List[Dict]:
        """Tìm kiếm video trên Pexels theo từ khóa với tỷ lệ dọc (portrait 9:16) có cơ chế retry và xoay key."""
        key = self._get_active_key()
        if not key:
            return []

        for attempt in range(2):
            try:
                headers = {"Authorization": key}
                params = {
                    "query": query,
                    "per_page": per_page,
                    "orientation": orientation
                }
                res = requests.get(self.base_url, headers=headers, params=params, timeout=12)
                if res.status_code == 200:
                    return res.json().get("videos", [])
                elif res.status_code in (401, 429):
                    logger.warning(f"Pexels key bị giới hạn ({res.status_code}), chuyển key...")
                    key = self._get_active_key()
            except Exception as e:
                logger.debug(f"Pexels search_videos attempt {attempt+1} error for '{query}': {e}")
                time.sleep(1.0)
        return []

    def search_photos(self, query: str, per_page: int = 3, orientation: str = "portrait") -> List[Dict]:
        """Tìm kiếm ảnh tĩnh trên Pexels theo từ khóa với tỷ lệ dọc (portrait 9:16) cho Ken Burns."""
        key = self._get_active_key()
        if not key:
            return []

        try:
            headers = {"Authorization": key}
            params = {
                "query": query,
                "per_page": per_page,
                "orientation": orientation
            }
            res = requests.get(self.photo_url, headers=headers, params=params, timeout=10)
            if res.status_code == 200:
                data = res.json().get("photos", [])
                results = []
                for p in data:
                    src = p.get("src", {})
                    img_url = src.get("portrait") or src.get("large2x") or src.get("large") or src.get("original")
                    if img_url:
                        results.append({
                            "url": img_url,
                            "title": f"Pexels: {p.get('alt') or query}",
                            "width": p.get("width", 1080),
                            "height": p.get("height", 1920),
                            "source": "pexels",
                            "thumbnail": src.get("medium") or img_url,
                        })
                return results
        except Exception as e:
            logger.debug(f"Pexels search_photos error for '{query}': {e}")
        return []

    def search_pixabay_videos(self, query: str, per_page: int = 4) -> List[Dict]:
        """Tìm kiếm video clip trên Pixabay API (kho video 3D, CGI, chuyển động trừu tượng)."""
        key = self._pixabay_key
        if not key or key == "your_pixabay_api_key_here":
            return []

        try:
            clean_q = clean_scene_search_query(query, max_words=3) or query
            url = f"https://pixabay.com/api/videos/?key={key}&q={urllib.parse.quote(clean_q)}&video_type=all&per_page={max(per_page, 3)}&safesearch=true"
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                hits = res.json().get("hits", [])
                results = []
                for h in hits:
                    vids = h.get("videos", {})
                    v_info = vids.get("large") or vids.get("medium") or vids.get("small")
                    if v_info and v_info.get("url"):
                        results.append({
                            "url": v_info["url"],
                            "title": f"Pixabay Video: {h.get('tags', clean_q)}",
                            "width": v_info.get("width", 1080),
                            "height": v_info.get("height", 1920),
                            "duration": h.get("duration", 0),
                            "media_type": "video",
                            "source": "pixabay_video",
                            "thumbnail": f"https://i.vimeocdn.com/video/{h.get('picture_id', '')}_640x360.jpg" if h.get("picture_id") else "",
                        })
                return results
        except Exception as e:
            logger.debug(f"Pixabay video search error for '{query}': {e}")
        return []

    def search_pixabay_photos(self, query: str, per_page: int = 4) -> List[Dict]:
        """Tìm kiếm ảnh chất lượng cao trên Pixabay API."""
        key = self._pixabay_key
        if not key or key == "your_pixabay_api_key_here":
            return []

        try:
            clean_q = clean_scene_search_query(query, max_words=3) or query
            url = f"https://pixabay.com/api/?key={key}&q={urllib.parse.quote(clean_q)}&image_type=photo,illustration&per_page={max(per_page, 3)}&safesearch=true"
            res = requests.get(url, timeout=10)
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
                            "thumbnail": h.get("previewURL", img_url),
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
        """Tìm kiếm ảnh Creative Commons trên Openverse API."""
        try:
            clean_q = clean_scene_search_query(query, max_words=3) or query
            url = f"https://api.openverse.engineering/v1/images/?q={urllib.parse.quote(clean_q)}&page_size={per_page}&license_type=commercial"
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

    def search_best_video_clip(
        self,
        query: str,
        used_urls: Optional[set] = None,
        visual_analysis: Optional[Dict[str, Any]] = None,
        narration: str = "",
    ) -> Optional[Dict]:
        """Tìm kiếm 1 video clip dọc (9:16) chất lượng cao nhất, có tích hợp VisualCurationAgent."""
        used = used_urls or set()
        clean_q = clean_scene_search_query(query, max_words=3)
        words = clean_q.split() if clean_q else query.split()

        candidate_queries = []
        if visual_analysis and visual_analysis.get("search_queries"):
            for sq in visual_analysis["search_queries"]:
                if sq and sq not in candidate_queries:
                    candidate_queries.append(sq)

        if clean_q and clean_q not in candidate_queries:
            candidate_queries.append(clean_q)
        if len(words) >= 2:
            two_words = " ".join(words[:2])
            if two_words not in candidate_queries:
                candidate_queries.append(two_words)
        if len(words) >= 1:
            for w in words:
                if len(w) > 2 and w not in candidate_queries:
                    candidate_queries.append(w)
        if query and query not in candidate_queries:
            candidate_queries.append(query)

        collected_candidates = []

        space_words = ["space", "universe", "galaxy", "black hole", "nebula", "planet", "mars", "moon", "star", "telescope", "astronaut", "astronomy", "hố đen", "vũ trụ", "ngân hà", "hành tinh", "sao hỏa", "mặt trăng"]
        is_space = any(w in f"{query} {narration} {clean_q}".lower() for w in space_words)
        if is_space:
            nasa_vids = self.search_nasa_media(query, media_type="video", per_page=3)
            for nv in nasa_vids:
                if nv.get("url") and nv["url"] not in used and nv not in collected_candidates:
                    collected_candidates.append(nv)

        for q_try in candidate_queries:
            videos = self.search_videos(q_try, per_page=6, orientation="portrait")
            for v in videos:
                if v.get("url") and v["url"] not in used and v not in collected_candidates:
                    collected_candidates.append(v)
            if len(collected_candidates) >= 8:
                break

        if len(collected_candidates) < 4 and self._pixabay_key:
            for q_try in candidate_queries[:2]:
                pxb_vids = self.search_pixabay_videos(q_try, per_page=4)
                for pv in pxb_vids:
                    if pv.get("url") and pv["url"] not in used and pv not in collected_candidates:
                        collected_candidates.append(pv)
                if len(collected_candidates) >= 8:
                    break

        if not collected_candidates:
            combined_text = f"{query} {narration} {clean_q}".lower()
            theme_fallbacks = []
            if any(w in combined_text for w in ["ocean", "sea", "water", "underwater", "deep", "biển", "nước", "sóng"]):
                theme_fallbacks.extend(["underwater ocean", "ocean waves", "deep water", "dark ocean"])
            if any(w in combined_text for w in ["space", "galaxy", "stars", "universe", "planet", "vũ trụ"]):
                theme_fallbacks.extend(["space galaxy", "universe stars", "nebula space", "planet in space"])
            if any(w in combined_text for w in ["cyberpunk", "tech", "technology", "neon", "ai", "robot"]):
                theme_fallbacks.extend(["cyberpunk city", "neon technology", "futuristic city", "computer code"])
            if any(w in combined_text for w in ["mind", "brain", "think", "alone", "dark", "shadow", "stress"]):
                theme_fallbacks.extend(["dark silhouette", "person thinking", "shadow in dark", "alone night"])

            for tf in theme_fallbacks:
                videos = self.search_videos(tf, per_page=4, orientation="portrait")
                for v in videos:
                    if v.get("url") and v["url"] not in used and v not in collected_candidates:
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
                if winner and score >= 4.8:
                    return winner
                if should_ai:
                    logger.info(f"[StockFetcher] VisualCurationAgent từ chối stock video ({rationale}), chuyển sang AI Video.")
                    return None
            except Exception as e:
                logger.debug(f"[StockFetcher] Lỗi khi gọi VisualCurationAgent: {e}")

        for v in collected_candidates:
            if v.get("source") in ("nasa_video", "pixabay_video") and v.get("url") and v["url"] not in used:
                return v

            v_files = v.get("video_files", [])
            portrait_files = [
                vf for vf in v_files
                if vf.get("width", 0) <= vf.get("height", 0) and (
                    vf.get("link", "").endswith(".mp4") or "mp4" in vf.get("file_type", "")
                )
            ]
            if portrait_files:
                # Tối ưu tốc độ: Ưu tiên Full HD (1080x1920) hoặc HD (720x1280) để tải nhanh gấp 5 lần
                # và FFmpeg render CPU mượt mà, tránh các file 4K (height > 1920) nặng 40-60MB gây chậm hệ thống.
                def _res_score(vf):
                    h = vf.get("height", 0)
                    if h == 1920:
                        return 1000
                    elif 1080 <= h < 1920:
                        return 800 + h
                    elif 720 <= h < 1080:
                        return 600 + h
                    elif h > 1920:
                        return 200 - h  # 4K ưu tiên thấp nhất
                    return h

                hd_files = sorted(portrait_files, key=_res_score, reverse=True)
                for best in hd_files:
                    link = best.get("link")
                    if link and link not in used:
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
        return None
