"""
app.agents.media_agent — Orchestrator tìm kiếm và chuẩn bị media đa tầng cho video Shorts.

Kiến trúc mô-đun hóa (P1 Refactored):
- app.services.media.entity_detector: Nhận diện thực thể, tri thức lịch sử, lọc nhiễu kịch bản.
- app.services.media.stock_fetcher: Tương tác Pexels, Pixabay, Openverse, NASA media.
- app.services.media.archive_fetcher: Tương tác Wikipedia, Wikimedia Commons, Google Search.
- MediaAgent: Lớp điều phối cấp cao (Facade) đảm bảo tính tương thích ngược 100%.
"""

import os
import time
import json
import logging
import requests
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

from config.settings import settings

# ── RE-EXPORT TẤT CẢ HELPER & DETECTOR ĐỂ ĐẢM BẢO TƯƠNG THÍCH NGƯỢC 100% ──
from app.services.media.entity_detector import (
    WIKIMEDIA_USER_AGENT,
    FORBIDDEN_ENTITY_NOISE,
    CAMERA_AND_STYLE_FLUFF,
    is_valid_entity_name,
    detect_real_entity,
    detect_video_primary_entity,
    clean_scene_search_query,
    synthesize_scene_query,
    match_best_pool_photo,
    build_character_media_pool,
)
from app.services.media.stock_fetcher import StockFetcher
from app.services.media.archive_fetcher import ArchiveFetcher

import threading as _threading

logger = logging.getLogger(__name__)

_download_fallback_lock = _threading.Lock()
_download_fallback_registry: Dict[str, Dict[str, Any]] = {}


def register_download_fallbacks(winner: Optional[Dict[str, Any]], candidates: Optional[List[Dict[str, Any]]] = None) -> None:
    """Lưu thông tin Referer (page_url), CDN fallback_url của ảnh chính, và danh sách URL dự phòng (Vision-ranked alternates)."""
    if not winner or not winner.get("url"):
        return
    u = winner["url"]
    alts: List[str] = []
    # Ưu tiên số 1: Bản sao độ phân giải cao trên CDN (Bing/ArtStation/Wallhaven/Google) của chính bức ảnh thắng cuộc
    fb_u = winner.get("fallback_url") or winner.get("thumbnail")
    if fb_u and fb_u != u:
        alts.append(fb_u)
    for va in winner.get("_vision_alternates") or []:
        if va and va != u and va not in alts:
            alts.append(va)
    distinct_urls: List[str] = []
    for va in winner.get("_vision_alternates") or []:
        if va and va not in (u, fb_u) and va not in distinct_urls:
            distinct_urls.append(va)
    if candidates:
        for c in candidates:
            cu = c.get("url")
            if cu and cu not in (u, fb_u) and cu not in distinct_urls:
                distinct_urls.append(cu)
            for cu_all in (c.get("url"), c.get("fallback_url")):
                if cu_all and cu_all != u and cu_all not in alts:
                    alts.append(cu_all)
            if len(alts) >= 6 and len(distinct_urls) >= 4:
                break
    if distinct_urls and not winner.get("alternate_urls"):
        winner["alternate_urls"] = distinct_urls[:4]
    with _download_fallback_lock:
        if len(_download_fallback_registry) > 500:
            _download_fallback_registry.clear()
        _download_fallback_registry[u] = {
            "referer": winner.get("page_url") or "",
            "alternates": alts[:6],
        }


class MediaAgent:
    """
    Agent tìm kiếm và tải media thông minh đa tầng cho video YouTube Shorts.
    
    Hệ thống phân cấp tìm kiếm:
      Tầng A: Wikipedia/Wikidata (Ảnh chân dung chính thức cho nhân vật/địa danh thật)
      Tầng B: Wikimedia Commons (Ảnh tài liệu lịch sử chất lượng cao)
      Tầng C: Google Custom Search (Nếu có key — tìm ảnh rộng trên toàn web)
      Tầng D: Pexels Video/Photo & Pixabay (Stock footage chất lượng cao 9:16)
      Tầng E: AI Image/Video Generation (Fallback khi không tìm được ảnh thật)
    """

    def __init__(self, api_key: Optional[str] = None):
        self._stock = StockFetcher(api_key=api_key)
        self._archive = ArchiveFetcher()

    @property
    def keys(self) -> List[str]:
        return self._stock.keys

    @property
    def api_key(self) -> Optional[str]:
        return self._stock.api_key

    @property
    def _pixabay_key(self) -> str:
        return self._stock._pixabay_key

    @property
    def _google_cx(self) -> str:
        return self._archive._google_cx

    @property
    def _google_search_key(self) -> str:
        return self._archive._google_search_key

    def _get_active_key(self) -> Optional[str]:
        return self._stock._get_active_key()

    # ── DELEGATIONS SANG ARCHIVE FETCHER ──
    def search_wikipedia_entity_image(self, entity_name: str) -> Optional[Dict]:
        return self._archive.search_wikipedia_entity_image(entity_name)

    def _wikipedia_fuzzy_search(self, entity_name: str) -> Optional[Dict]:
        return self._archive._wikipedia_fuzzy_search(entity_name)

    def _get_wikimedia_file_url(self, file_title: str) -> Optional[Dict]:
        return self._archive._get_wikimedia_file_url(file_title)

    @staticmethod
    def _is_valid_image_url(url: str) -> bool:
        return ArchiveFetcher._is_valid_image_url(url)

    @staticmethod
    def _is_noisy_entity_file(title: str, url: str) -> bool:
        return ArchiveFetcher._is_noisy_entity_file(title, url)

    def search_web_photos(self, query: str, per_page: int = 3, filter_entity_noise: bool = False) -> List[Dict]:
        return self._archive.search_web_photos(query, per_page=per_page, filter_entity_noise=filter_entity_noise)

    def search_google_images(self, query: str, per_page: int = 3) -> List[Dict]:
        return self._archive.search_google_images(query, per_page=per_page)

    def search_open_web_photos(self, query: str, per_page: int = 6) -> List[Dict]:
        """Tìm kiếm ảnh trực tiếp từ toàn bộ mạng Internet (DDGS + Wikipedia + Commons)."""
        try:
            from app.services.media.web_image_fetcher import WebImageFetcher, web_image_search_enabled
            if web_image_search_enabled():
                return WebImageFetcher().search_all(query, per_source=max(2, per_page // 3))
        except Exception as e:
            logger.debug(f"[MediaAgent] search_open_web_photos error: {e}")
        return []

    # ── DELEGATIONS SANG STOCK FETCHER ──
    def search_videos(self, query: str, per_page: int = 4, orientation: str = "portrait") -> List[Dict]:
        return self._stock.search_videos(query, per_page=per_page, orientation=orientation)

    def search_photos(
        self,
        query: str,
        per_page: int = 3,
        orientation: str = "portrait",
        topic: str = "",
        narration: str = "",
        used_urls: Optional[set] = None,
    ) -> List[Dict]:
        return self._stock.search_photos(
            query,
            per_page=per_page,
            orientation=orientation,
            topic=topic,
            narration=narration,
            used_urls=used_urls,
        )

    def search_pixabay_videos(self, query: str, per_page: int = 4) -> List[Dict]:
        return self._stock.search_pixabay_videos(query, per_page=per_page)

    def search_pixabay_photos(self, query: str, per_page: int = 4) -> List[Dict]:
        return self._stock.search_pixabay_photos(query, per_page=per_page)

    def search_nasa_media(self, query: str, media_type: str = "video,image", per_page: int = 4) -> List[Dict]:
        return self._stock.search_nasa_media(query, media_type=media_type, per_page=per_page)

    def search_openverse(self, query: str, per_page: int = 5) -> List[Dict]:
        return self._stock.search_openverse(query, per_page=per_page)

    def search_best_photo(
        self,
        query: str,
        used_urls: Optional[set] = None,
        visual_analysis: Optional[Dict[str, Any]] = None,
        narration: str = "",
        topic: str = "",
    ) -> Optional[Dict]:
        """Ảnh tĩnh đa nguồn (Pexels + Pixabay + Openverse) được AI Vision tuyển chọn."""
        return self._stock.search_best_photo(
            query, used_urls=used_urls, visual_analysis=visual_analysis, narration=narration, topic=topic,
        )

    def search_best_video_clip(
        self,
        query: str,
        used_urls: Optional[set] = None,
        visual_analysis: Optional[Dict[str, Any]] = None,
        narration: str = "",
        topic: str = "",
    ) -> Optional[Dict]:
        return self._stock.search_best_video_clip(
            query=query,
            used_urls=used_urls,
            visual_analysis=visual_analysis,
            narration=narration,
            topic=topic,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # TẦNG ĐIỀU PHỐI ĐA NGUỒN & QUẢN LÝ POOL
    # ──────────────────────────────────────────────────────────────────────────

    def search_multi_source_images(self, query: str, per_page: int = 6) -> List[Dict]:
        """Tìm kiếm tổng hợp đa nguồn từ các thư viện ảnh hàng đầu."""
        combined = []
        seen_urls = set()

        def add_item(item):
            if not item:
                return
            u = item.get("url")
            if u and u not in seen_urls and self._is_valid_image_url(u):
                seen_urls.add(u)
                combined.append(item)

        # Open Web (DuckDuckGo Live Image Search) - bỏ qua rào cản bản quyền, phủ sóng ảnh thực tế
        try:
            from app.services.media.web_image_fetcher import WebImageFetcher, web_image_search_enabled
            if web_image_search_enabled():
                web_imgs = WebImageFetcher().search_open_web(query, per_page=per_page)
                for wi in web_imgs:
                    add_item(wi)
        except Exception as e:
            logger.debug(f"[MediaAgent] Open web search error: {e}")

        wiki_img = self.search_wikipedia_entity_image(query)
        if wiki_img:
            add_item(wiki_img)

        commons = self.search_web_photos(query, per_page=per_page)
        for c in commons:
            add_item(c)

        openverse_items = self.search_openverse(query, per_page=per_page)
        for o in openverse_items:
            add_item(o)

        if self._google_search_key and self._google_cx:
            g_items = self.search_google_images(query, per_page=per_page)
            for g in g_items:
                add_item(g)

        if self.api_key:
            px_items = self.search_photos(query, per_page=per_page)
            for p in px_items:
                add_item(p)

        space_words = ["space", "universe", "galaxy", "black hole", "nebula", "planet", "vũ trụ"]
        if any(w in query.lower() for w in space_words):
            nasa_imgs = self.search_nasa_media(query, media_type="image", per_page=per_page)
            for ni in nasa_imgs:
                add_item(ni)

        if self._pixabay_key:
            pxb_imgs = self.search_pixabay_photos(query, per_page=per_page)
            for pi in pxb_imgs:
                add_item(pi)

        return combined[:per_page]

    def ai_detect_entities(self, text: str) -> List[Dict]:
        """Dùng Gemini API nhận diện thực thể trong văn bản khi heuristic không chắc chắn."""
        from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS
        keys = settings.get_gemini_keys_pool()
        if not keys:
            return []

        rotator = KeyRotator(keys)
        prompt = f"""Analyze this text and identify ALL real-world entities (people, places, events, organizations).
Return ONLY a JSON array. Each element: {{"name": "Full Official Name", "type": "person|place|event|org", "search_query": "best search term to find their photo"}}

Text: "{text}"

If no real entities found, return empty array: []"""

        for model, key in rotator.iter_best_model_key_pairs(GEMINI_PRIORITY_MODELS):
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                payload = {"contents": [{"parts": [{"text": prompt}]}]}
                r = requests.post(url, json=payload, timeout=12)
                if r.status_code == 200:
                    data = r.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        raw = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "[]").strip()
                        start = raw.find("[")
                        end = raw.rfind("]")
                        if start != -1 and end != -1:
                            raw = raw[start:end+1]
                        return json.loads(raw)
                elif r.status_code in (429, 403, 404, 503):
                    rotator.mark_model_rate_limited(key, model, status_code=r.status_code, error_text=r.text[:250])
                    continue
            except Exception as e:
                logger.debug(f"AI entity detection ({model}): {e}")
                continue

        return []

    def fetch_entity_media_pool(
        self,
        entity_name: str,
        entity_type: str = "person",
        count: int = 15
    ) -> List[Dict]:
        """Thu thập bộ sưu tập ảnh tài liệu chất lượng cao đa dạng cho thực thể chính."""
        pool = []
        seen_urls = set()

        def add_unique(item: Optional[Dict]):
            if not item:
                return
            u = item.get("url", "")
            t = item.get("title", "")
            if not u or u in seen_urls or not self._is_valid_image_url(u):
                return
            if entity_type == "person" and self._is_noisy_entity_file(t, u):
                logger.debug(f"Bỏ qua ảnh nhiễu địa danh cho nhân vật: {t}")
                return
            seen_urls.add(u)
            pool.append(item)

        wiki_portrait = self.search_wikipedia_entity_image(entity_name)
        if wiki_portrait and not (entity_type == "person" and self._is_noisy_entity_file(wiki_portrait.get("title", ""), wiki_portrait.get("url", ""))):
            add_unique(wiki_portrait)

        if entity_type == "civilization":
            search_queries = [
                f"{entity_name} architecture",
                f"{entity_name} temple ruins",
                f"{entity_name} archaeological artifacts",
                f"{entity_name} museum",
            ]
        elif entity_type == "concept":
            search_queries = [
                f"{entity_name} statue marble",
                f"{entity_name} philosophy portrait",
                "stoic marble bust Roman",
                "ancient library scrolls",
            ]
        else:
            search_queries = [
                f'"{entity_name}"',
                f'"{entity_name}" portrait',
                f'"{entity_name}" photograph',
                f'"{entity_name}" official',
                f'"{entity_name}" vintage',
            ]

        for q in search_queries:
            if len(pool) >= count:
                break
            results = self.search_web_photos(
                q,
                per_page=min(count, 8),
                filter_entity_noise=(entity_type == "person")
            )
            for item in results:
                add_unique(item)
                if len(pool) >= count:
                    break

        if self.api_key and len(pool) < count and entity_type in ("civilization", "concept"):
            for pq in search_queries[:3]:
                if len(pool) >= count:
                    break
                px_photos = self.search_photos(pq, per_page=3, orientation="portrait")
                for p in px_photos:
                    add_unique(p)

        if len(pool) < count:
            ov_queries = [f'"{entity_name}"', f"{entity_name} archive"]
            for oq in ov_queries:
                if len(pool) >= count:
                    break
                ov_results = self.search_openverse(oq, per_page=min(count - len(pool), 6))
                for item in ov_results:
                    add_unique(item)

        if len(pool) < count and self._google_search_key and self._google_cx:
            g_results = self.search_google_images(f"{entity_name} photo portrait", per_page=min(count - len(pool), 5))
            for item in g_results:
                add_unique(item)

        logger.info(f"📸 Đã thu thập được {len(pool)} ảnh tài liệu thực tế chuẩn cho '{entity_name}' (type={entity_type})")
        return pool

    def search_scene_media_multi_source(
        self,
        scene_narration: str,
        visual_keyword: str,
        stock_query: Optional[str] = None,
        visual_prompt: Optional[str] = None,
        primary_entity: Optional[str] = None,
        entity_type: str = "person",
        used_urls: Optional[set] = None,
        niche: str = "general",
        scene_id: int = 0,
        prefer_video: bool = False,
        prefer_photo: bool = False,
        visual_analysis: Optional[Dict[str, Any]] = None,
        topic: str = "",
    ) -> Optional[Dict]:
        """Tìm kiếm media đa tầng tối ưu cao nhất cho 1 phân cảnh."""
        from app.services.media.entity_detector import is_incongruent_stock_image
        from app.services.media.media_history import extract_media_identity, is_media_already_used

        used = used_urls if used_urls is not None else set()
        primary_q, fallback_q, intent = synthesize_scene_query(
            narration=scene_narration,
            visual_keyword=visual_keyword,
            stock_query=stock_query,
            visual_prompt=visual_prompt,
            primary_entity=primary_entity,
            topic=topic,
            channel_niche=niche,
        )

        v_analysis = dict(visual_analysis) if visual_analysis else None
        if not v_analysis and scene_narration:
            try:
                from app.agents.visual_curation_agent import VisualCurationAgent
                curator = VisualCurationAgent()
                v_analysis = curator.analyze_scene_context(
                    narration=scene_narration,
                    topic=topic or primary_entity or visual_keyword or "",
                    scene_id=scene_id,
                    primary_entity=primary_entity,
                    entity_type=entity_type,
                    channel_niche=niche,
                )
            except Exception as e:
                logger.debug(f"[MediaAgent] Context analysis fallback: {e}")
        if v_analysis is not None:
            v_analysis["scene_id"] = scene_id
            v_analysis["is_hook_scene"] = (scene_id == 1)

        logger.info(
            f"[MediaAgent] Scene {scene_id} Search: intent='{intent}' | "
            f"query='{primary_q}' (fallback='{fallback_q}') | topic='{topic}' | prefer_video={prefer_video} | prefer_photo={prefer_photo}"
        )

        is_historical = (
            intent == "historical_entity"
            or (primary_entity and entity_type in ("person", "civilization", "monument", "event", "historical_figure"))
        )
        if is_historical and not prefer_video:
            # Ưu tiên Google Images + Gemini Vision chọn bức ảnh bắt mắt nhất (không lấy mù quáng ảnh Wikipedia đầu tiên)
            hist_pool: List[Dict] = []
            try:
                from app.services.media.web_image_fetcher import WebImageFetcher, web_image_search_enabled, bypass_copyright_enabled
                if web_image_search_enabled() and bypass_copyright_enabled():
                    for wi in WebImageFetcher().search_all(primary_q, per_source=6, subject=primary_q):
                        wu = wi.get("url")
                        if wu and not is_media_already_used(wu, used):
                            hist_pool.append(wi)
            except Exception as e:
                logger.debug(f"[MediaAgent] Historical open web search error: {e}")

            wiki_img = self.search_wikipedia_entity_image(primary_q)
            if wiki_img and wiki_img.get("url") and not is_media_already_used(wiki_img["url"], used):
                if not (entity_type == "person" and self._is_noisy_entity_file(wiki_img.get("title", ""), wiki_img["url"])):
                    hist_pool.append(wiki_img)

            if len(hist_pool) < 4:
                wiki_queries = [f'"{primary_q}"', primary_q]
                if fallback_q and fallback_q != primary_q:
                    wiki_queries.append(fallback_q)
                for wq in wiki_queries:
                    commons = self.search_web_photos(wq, per_page=4, filter_entity_noise=(entity_type == "person"))
                    for c in commons:
                        u = c.get("url")
                        if u and not is_media_already_used(u, used) and self._is_valid_image_url(u):
                            if not is_incongruent_stock_image(c.get("title", ""), topic=topic, narration=scene_narration):
                                if not any(x.get("url") == u for x in hist_pool):
                                    hist_pool.append(c)
                    if len(hist_pool) >= 10:
                        break

            if hist_pool:
                from app.services.media.image_quality import prefilter_candidates
                hist_pool = prefilter_candidates(hist_pool, subject=primary_q, limit=10, max_per_source=10)
            if hist_pool:
                win, rejected = None, False
                try:
                    from app.agents.visual_curation_agent import VisualCurationAgent
                    va_hist = {**(v_analysis or {}), "subject": primary_q, "scene_id": scene_id, "is_hook_scene": (scene_id == 1)}
                    win, score, rationale, rejected = VisualCurationAgent().curate_candidate_photos(
                        candidates=hist_pool, visual_analysis=va_hist, narration=scene_narration,
                        topic=topic, visual_keyword=primary_q, used_urls=used,
                    )
                except Exception as e:
                    logger.debug(f"[MediaAgent] Historical curation error: {e}")
                if not win and not rejected and scene_id != 1:
                    win = hist_pool[0]
                if win and win.get("url"):
                    register_download_fallbacks(win, hist_pool)
                    used.update(extract_media_identity(win["url"]))
                    return win

        motion_keywords = {
            "walk", "walking", "run", "running", "moving", "movement", "crowd", "people", "city", "street",
            "traffic", "night", "dark", "shadow", "silhouette", "rain", "storm", "fire", "water", "sea", "ocean",
            "clock", "time", "hourglass", "hands", "typing", "money", "cash", "brain", "mind", "synapse",
            "space", "galaxy", "stars", "looking", "staring", "smoke", "lights", "neon", "car", "train", "sky",
            "burning", "war", "explosion", "universe"
        }
        text_for_motion = f"{topic} {scene_narration} {visual_keyword} {stock_query or ''}".lower()
        has_motion_cue = any(mk in text_for_motion for mk in motion_keywords)

        should_try_video = (prefer_video or has_motion_cue or scene_id == 1 or not prefer_photo)
        if should_try_video and self.api_key:
            target_query = stock_query or primary_q or visual_keyword
            best_clip = self.search_best_video_clip(
                query=target_query,
                used_urls=used,
                visual_analysis=v_analysis,
                narration=scene_narration,
                topic=topic,
            )
            if best_clip and best_clip.get("url") and not is_media_already_used(best_clip["url"], used):
                used.update(extract_media_identity(best_clip["url"]))
                return best_clip

        # Thu thập ứng viên ảnh tĩnh từ Google Images / Open Web (ưu tiên tuyệt đối)
        candidate_photos = []
        from app.services.media.image_quality import extract_subject, prefilter_candidates
        scene_subject = (
            str((v_analysis or {}).get("subject") or "").strip()
            or primary_entity
            or extract_subject(scene_narration)
        )

        # 1. Ưu tiên hàng đầu: Ảnh Google Images / Bing HD bắt mắt câu view (Gemini Search)
        try:
            from app.services.media.web_image_fetcher import WebImageFetcher, web_image_search_enabled
            if web_image_search_enabled():
                google_qs = [q for q in ((v_analysis or {}).get("google_image_queries") or []) if q]
                web_q = (
                    (google_qs[0] if google_qs else None)
                    or ((v_analysis or {}).get("search_queries") or [None])[0]
                    or primary_q
                    or fallback_q
                )
                web_items = WebImageFetcher().search_all(
                    web_q,
                    per_source=6,
                    subject=scene_subject,
                    extra_queries=google_qs[1:] if len(google_qs) > 1 else None,
                    creature_mode=bool((v_analysis or {}).get("creature_mode")),
                    preferred_engines=(v_analysis or {}).get("preferred_engines"),
                )
                for item in web_items:
                    u = item.get("url")
                    if u and not is_media_already_used(u, used) and not any(c.get("url") == u for c in candidate_photos):
                        candidate_photos.append(item)
        except Exception as e:
            logger.debug(f"[MediaAgent] Candidate web photos error: {e}")

        # 2. Chỉ gọi Pexels / Wikimedia Commons khi Google Search trả về ít hơn 4 ảnh
        if len(candidate_photos) < 4:
            if self.api_key:
                px_photos = self.search_photos(
                    primary_q or fallback_q,
                    per_page=4,
                    orientation="portrait",
                    topic=topic,
                    narration=scene_narration,
                    used_urls=used,
                )
                candidate_photos.extend(px_photos)

            commons_fallback = self.search_web_photos(primary_q or fallback_q, per_page=4)
            for c in commons_fallback:
                u = c.get("url")
                if u and not is_media_already_used(u, used) and not any(x.get("url") == u for x in candidate_photos):
                    candidate_photos.append(c)

        if candidate_photos:
            candidate_photos = prefilter_candidates(
                candidate_photos,
                subject=scene_subject or primary_q,
                limit=12,
                creature_mode=bool((v_analysis or {}).get("creature_mode")),
                max_per_source=12,
            )
            if scene_subject and v_analysis is not None and not v_analysis.get("subject"):
                v_analysis = {**v_analysis, "subject": scene_subject}
        if candidate_photos:
            try:
                from app.agents.visual_curation_agent import VisualCurationAgent
                curator = VisualCurationAgent()
                v_analysis = v_analysis or curator.analyze_scene_context(
                    narration=scene_narration or primary_q,
                    topic=topic or primary_q,
                    scene_id=scene_id,
                )
                v_analysis["scene_id"] = scene_id
                v_analysis["is_hook_scene"] = (scene_id == 1)
                winner_photo, score, rationale, should_ai = curator.curate_candidate_photos(
                    candidates=candidate_photos,
                    visual_analysis=v_analysis,
                    narration=scene_narration,
                    topic=topic,
                    visual_keyword=visual_keyword or primary_q,
                    used_urls=used,
                )
                min_p_score = 7.0 if scene_id == 1 else 6.0
                if winner_photo and score >= min_p_score and not should_ai:
                    u = winner_photo.get("url")
                    if u and not is_media_already_used(u, used):
                        register_download_fallbacks(winner_photo, candidate_photos)
                        used.update(extract_media_identity(u))
                        return winner_photo
                elif should_ai or scene_id == 1:
                    logger.info(f"[MediaAgent] VisualCurationAgent từ chối ảnh chưa đủ WOW ({rationale}), chuyển sang 8K AI Masterpiece.")
            except Exception as e:
                logger.debug(f"[MediaAgent] VisualCurationAgent candidate photos error: {e}")
                if scene_id != 1:
                    # Dự phòng heuristic nếu Agent lỗi (không áp dụng mù quáng cho Cảnh 1 Hook)
                    for p in candidate_photos:
                        u = p.get("url")
                        if u and not is_media_already_used(u, used):
                            alt_desc = p.get("alt") or p.get("title", "")
                            if not is_incongruent_stock_image(alt_desc, topic=topic, narration=scene_narration):
                                register_download_fallbacks(p, candidate_photos)
                                used.update(extract_media_identity(u))
                                return p

        if self.api_key and not should_try_video:
            target_query = stock_query or fallback_q
            clip_fb = self.search_best_video_clip(
                query=target_query,
                used_urls=used,
                visual_analysis=v_analysis,
                narration=scene_narration,
            )
            if clip_fb and clip_fb.get("url") and not is_media_already_used(clip_fb["url"], used):
                used.update(extract_media_identity(clip_fb["url"]))
                return clip_fb

        return None

    def smart_search_media(
        self,
        keyword: str,
        narration: str = "",
        per_page: int = 4,
        allow_video: bool = True
    ) -> List[Dict]:
        """Tìm kiếm media đa nguồn (images & videos) với khử trùng lặp."""
        all_results = []
        seen = set()

        def add_cand(cand):
            u = cand.get("url")
            if u and u not in seen:
                seen.add(u)
                all_results.append(cand)

        is_ent, ent_name, ent_type = detect_real_entity(keyword, narration)
        if is_ent and ent_name:
            wiki_p = self.search_wikipedia_entity_image(ent_name)
            if wiki_p:
                add_cand(wiki_p)
            for c in self.search_web_photos(ent_name, per_page=per_page):
                add_cand(c)

        if allow_video and self.api_key:
            v_clip = self.search_best_video_clip(query=keyword, narration=narration)
            if v_clip:
                add_cand(v_clip)

        for p in self.search_multi_source_images(keyword, per_page=per_page):
            add_cand(p)

        return all_results[:per_page]

    def download_file(self, url: str, output_path: str, max_retries: int = 3) -> str:
        """
        Tải file media về máy tính với stream, Referer thông minh chống chặn hotlink (403),
        tự động chuyển sang ứng viên dự phòng đã qua Vision kiểm duyệt nếu URL chính lỗi,
        và kiểm định bitmap raster bằng Pillow.
        """
        from urllib.parse import urlparse

        with _download_fallback_lock:
            fb_info = _download_fallback_registry.get(url) or {}
        referer_override = fb_info.get("referer") or ""
        alt_urls: List[str] = list(fb_info.get("alternates") or [])

        urls_to_try = [url] + [au for au in alt_urls if au and au != url]
        ext = Path(output_path).suffix.lower()
        is_image_target = ext in [".jpg", ".jpeg", ".png", ".webp"]

        last_err = None
        for idx_u, target_u in enumerate(urls_to_try):
            parsed = urlparse(target_u)
            origin_ref = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else ""
            ref = referer_override if (idx_u == 0 and referer_override) else origin_ref

            headers = {
                "User-Agent": (
                    WIKIMEDIA_USER_AGENT
                    if any(domain in target_u for domain in ["wikimedia.org", "wikipedia.org"])
                    else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8" if is_image_target else "*/*",
            }
            if ref and not any(domain in target_u for domain in ["wikimedia.org", "wikipedia.org", "pexels.com", "pixabay.com", "bing.net", "gstatic.com"]):
                headers["Referer"] = ref

            downloaded_ok = False
            tries_for_url = max_retries if idx_u == 0 else min(2, max_retries)
            for attempt in range(tries_for_url):
                try:
                    res = requests.get(target_u, headers=headers, stream=True, timeout=20)
                    if res.status_code == 429:
                        sleep_time = 1.5 * (attempt + 1)
                        logger.warning(f"Rate limited (429) khi tải {target_u[:60]}... Đợi {sleep_time}s")
                        time.sleep(sleep_time)
                        continue
                    if res.status_code in (403, 401) and "Referer" in headers:
                        # Thử bỏ Referer nếu server chặn cross-referer
                        headers.pop("Referer", None)
                        res = requests.get(target_u, headers=headers, stream=True, timeout=15)
                    res.raise_for_status()
                    with open(output_path, "wb") as f:
                        for chunk in res.iter_content(chunk_size=8192):
                            f.write(chunk)
                    downloaded_ok = True
                    break
                except Exception as e:
                    last_err = e
                    if attempt < tries_for_url - 1:
                        time.sleep(0.8)

            if not downloaded_ok:
                if idx_u + 1 < len(urls_to_try):
                    logger.info(f"[MediaAgent] Tải URL chính thất bại ({last_err}) → thử ứng viên dự phòng #{idx_u + 1}")
                continue

            if is_image_target:
                try:
                    from PIL import Image
                    with Image.open(output_path) as im:
                        im.verify()

                    with Image.open(output_path) as im:
                        w, h = im.size
                        if w < 250 or h < 250 or max(w / h, h / w) > 2.8:
                            raise ValueError(f"Ảnh không đạt chuẩn kích thước/tỷ lệ ({w}x{h}, aspect={max(w/h, h/w):.2f})")
                        if im.mode not in ("RGB",):
                            rgb_im = im.convert("RGB")
                            save_fmt = "JPEG" if ext in [".jpg", ".jpeg"] else "PNG"
                            rgb_im.save(output_path, format=save_fmt, quality=95)
                    return output_path
                except Exception as e:
                    last_err = ValueError(f"File tải về không phải ảnh bitmap raster hợp lệ ({e}): {target_u}")
                    try:
                        Path(output_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    if idx_u + 1 < len(urls_to_try):
                        logger.info(f"[MediaAgent] Ảnh chính không hợp lệ ({e}) → thử ứng viên dự phòng #{idx_u + 1}")
                        continue
                    raise last_err
            else:
                return output_path

        if last_err:
            raise last_err
        return output_path
