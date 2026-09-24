import itertools
import logging
import re
import json
import requests
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from config.settings import settings

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# ENTITY DETECTION — Thay thế danh sách hardcode bằng AI + heuristics thông minh
# ──────────────────────────────────────────────────────────────────────────────

# Bộ patterns nhận diện thực thể (tên riêng) theo heuristics ngôn ngữ
_ENTITY_INDICATORS = {
    # Chức danh / tước hiệu thường đi kèm tên người thật
    "president", "prime minister", "king", "queen", "emperor", "general",
    "chancellor", "senator", "governor", "pope", "sultan", "pharaoh",
    "dictator", "tyrant", "CEO", "founder", "inventor", "scientist",
    "astronaut", "cosmonaut", "explorer", "admiral", "marshal", "tsar",
    "tổng thống", "thủ tướng", "chủ tịch", "hoàng đế", "vua", "nữ hoàng",
    "giáo hoàng", "nhà khoa học", "nhà phát minh", "tướng",
}

# Sự kiện / tổ chức lịch sử nổi tiếng
_KNOWN_EVENTS_ORGS = {
    "world war", "cold war", "vietnam war", "korean war", "civil war",
    "french revolution", "russian revolution", "industrial revolution",
    "holocaust", "9/11", "pearl harbor", "d-day", "moon landing",
    "manhattan project", "chernobyl", "fukushima",
    "nasa", "cia", "fbi", "kgb", "nato", "united nations", "un",
    "kremlin", "white house", "pentagon", "buckingham palace",
    "great wall of china", "pyramids", "colosseum", "taj mahal",
}


def detect_real_entity(visual_keyword: str, narration: str = "") -> Tuple[bool, Optional[str], str]:
    """
    Phát hiện thông minh xem cảnh có liên quan đến nhân vật/sự kiện thực hay không.
    
    Returns:
        (is_entity, entity_name, entity_type)
        entity_type: "person" | "place" | "event" | "org" | ""
    """
    text = f"{visual_keyword} {narration}".strip()
    text_lower = text.lower()

    # ── 1. Kiểm tra Known Events/Orgs ──
    for pattern in _KNOWN_EVENTS_ORGS:
        if pattern in text_lower:
            return True, pattern.title(), "event"

    # ── 2. Kiểm tra Title Indicators (President X, King Y...) ──
    for indicator in _ENTITY_INDICATORS:
        idx = text_lower.find(indicator.lower())
        if idx != -1:
            # Lấy phần sau indicator làm tên
            after = text[idx + len(indicator):].strip()
            if after:
                # Lấy 2-3 từ viết hoa đầu tiên sau title
                name_words = []
                for w in after.split()[:4]:
                    clean = re.sub(r'[^\w]', '', w)
                    if clean and (clean[0].isupper() or clean[0].isdigit()):
                        name_words.append(clean)
                    elif name_words:
                        break
                if name_words:
                    return True, " ".join(name_words), "person"
            return True, indicator.title(), "person"

    # ── 3. Heuristic: Nhiều từ viết hoa liên tiếp → tên riêng (Proper Noun) ──
    # "Vladimir Putin", "Elon Musk", "Albert Einstein"
    words = visual_keyword.split()
    capitalized_sequences = []
    current_seq = []
    for w in words:
        clean = re.sub(r'[^\w]', '', w)
        if clean and clean[0].isupper() and len(clean) >= 2 and not clean.isupper():
            current_seq.append(clean)
        else:
            if len(current_seq) >= 2:
                capitalized_sequences.append(" ".join(current_seq))
            current_seq = []
    if len(current_seq) >= 2:
        capitalized_sequences.append(" ".join(current_seq))
    
    if capitalized_sequences:
        best = max(capitalized_sequences, key=len)
        return True, best, "person"

    # ── 4. Kiểm tra trong narration nếu visual_keyword không rõ ──
    if narration:
        narr_words = narration.split()
        current_seq = []
        for w in narr_words:
            clean = re.sub(r'[^\w]', '', w)
            if clean and clean[0].isupper() and len(clean) > 1 and not clean.isupper():
                current_seq.append(clean)
            else:
                if len(current_seq) >= 2:
                    capitalized_sequences.append(" ".join(current_seq))
                current_seq = []
        if len(current_seq) >= 2:
            capitalized_sequences.append(" ".join(current_seq))
        if capitalized_sequences:
            best = max(capitalized_sequences, key=len)
            return True, best, "person"

    return False, None, ""


class MediaAgent:
    """
    Agent tìm kiếm và tải media thông minh đa tầng cho video YouTube Shorts.
    
    Hệ thống phân cấp tìm kiếm:
      Tầng A: Wikipedia/Wikidata (Ảnh chân dung chính thức cho nhân vật/địa danh thật)
      Tầng B: Wikimedia Commons (Ảnh tài liệu lịch sử chất lượng cao)
      Tầng C: Google Custom Search (Nếu có key — tìm ảnh rộng trên toàn web)
      Tầng D: Pexels Video/Photo (Stock footage chất lượng cao)
      Tầng E: AI Image Generation (Fallback khi không tìm được ảnh thật)
    """

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
        
        # Google Custom Search Engine (nếu có)
        self._google_cx = getattr(settings, 'GOOGLE_SEARCH_CX', '') or ''
        self._google_search_key = getattr(settings, 'GOOGLE_SEARCH_API_KEY', '') or ''

    @property
    def api_key(self) -> Optional[str]:
        return self.keys[0] if self.keys else None

    def _get_active_key(self) -> Optional[str]:
        if self._key_cycle:
            return next(self._key_cycle)
        return None

    # ──────────────────────────────────────────────────────────────────────────
    # TẦNG A: WIKIPEDIA / WIKIDATA — Ảnh chân dung chính thức
    # ──────────────────────────────────────────────────────────────────────────

    def search_wikipedia_entity_image(self, entity_name: str) -> Optional[Dict]:
        """
        Tìm ảnh chính thức của nhân vật/địa danh qua Wikipedia API.
        Ưu tiên lấy ảnh đại diện (thumbnail) từ bài viết Wikipedia.
        
        Chiến lược:
        1. Tìm bài Wikipedia tiếng Anh cho entity
        2. Lấy ảnh chính (pageimage / thumbnail) của bài viết
        3. Nếu không có, lấy ảnh từ phần images của bài viết
        """
        headers = {"User-Agent": "AI-YouTube-Studio/2.0 (Educational Project)"}
        
        # ── Bước 1: Tìm bài Wikipedia chính xác ──
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
                    # Không tìm thấy bài viết, thử tìm kiếm mờ
                    return self._wikipedia_fuzzy_search(entity_name)
                
                # Lấy ảnh chính (pageimage)
                original = page_data.get("original", {})
                thumbnail = page_data.get("thumbnail", {})
                
                img_url = original.get("source") or thumbnail.get("source")
                if img_url and self._is_valid_image_url(img_url):
                    return {
                        "url": img_url,
                        "title": f"Wikipedia: {page_data.get('title', entity_name)}",
                        "source": "wikipedia",
                        "width": original.get("width", 0) or thumbnail.get("width", 0),
                        "height": original.get("height", 0) or thumbnail.get("height", 0),
                    }
                
                # Fallback: Lấy ảnh đầu tiên hợp lệ từ danh sách images
                images = page_data.get("images", [])
                for img in images:
                    img_title = img.get("title", "")
                    # Bỏ qua icon, logo nhỏ, commons-logo, flag
                    if any(skip in img_title.lower() for skip in [
                        "commons-logo", "flag", "icon", "symbol", "coat_of_arms",
                        "map", "diagram", "chart", "logo", ".svg"
                    ]):
                        continue
                    
                    img_info = self._get_wikimedia_file_url(img_title)
                    if img_info:
                        return img_info
                        
        except Exception as e:
            logger.warning(f"Wikipedia entity search error for '{entity_name}': {e}")
        
        return None

    def _wikipedia_fuzzy_search(self, entity_name: str) -> Optional[Dict]:
        """Tìm kiếm mờ qua Wikipedia Search API khi tên chính xác không khớp."""
        headers = {"User-Agent": "AI-YouTube-Studio/2.0 (Educational Project)"}
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
                        # Đệ quy gọi lại với tên chính xác từ Wikipedia
                        return self.search_wikipedia_entity_image(title)
        except Exception as e:
            logger.debug(f"Wikipedia fuzzy search error: {e}")
        return None

    def _get_wikimedia_file_url(self, file_title: str) -> Optional[Dict]:
        """Lấy URL ảnh gốc từ Wikimedia Commons theo tên file."""
        headers = {"User-Agent": "AI-YouTube-Studio/2.0 (Educational Project)"}
        params = {
            "action": "query",
            "format": "json",
            "titles": file_title,
            "prop": "imageinfo",
            "iiprop": "url|size|mime",
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
                        url = ii[0].get("url", "")
                        mime = ii[0].get("mime", "")
                        w = ii[0].get("width", 0)
                        if url and mime.startswith("image/") and "svg" not in mime and w >= 300:
                            return {
                                "url": url,
                                "title": file_title,
                                "source": "wikimedia",
                                "width": w,
                                "height": ii[0].get("height", 0),
                            }
        except Exception:
            pass
        return None

    @staticmethod
    def _is_valid_image_url(url: str) -> bool:
        """Kiểm tra URL có phải ảnh bitmap hợp lệ không (loại trừ SVG, PDF, audio...)."""
        url_lower = url.lower()
        if any(url_lower.endswith(ext) for ext in [".svg", ".pdf", ".ogg", ".ogv", ".webm"]):
            return False
        if any(skip in url_lower for skip in ["commons-logo", "icon", "symbol"]):
            return False
        return True

    # ──────────────────────────────────────────────────────────────────────────
    # TẦNG B: WIKIMEDIA COMMONS — Tìm kiếm nâng cao
    # ──────────────────────────────────────────────────────────────────────────

    def search_web_photos(self, query: str, per_page: int = 3) -> List[Dict]:
        """
        Tìm kiếm hình ảnh chất lượng cao trên Wikimedia Commons API.
        Cải thiện: Thử nhiều biến thể query, lọc chất lượng cao, ưu tiên ảnh photograph.
        """
        headers = {"User-Agent": "AI-YouTube-Studio/2.0 (Educational Project)"}
        
        # Chiến lược tìm kiếm: thử query gốc, sau đó tinh chỉnh
        queries_to_try = [query]
        
        # Thêm biến thể "photograph" cho người thật
        if not any(w in query.lower() for w in ["photo", "portrait", "photograph", "image"]):
            queries_to_try.append(f"{query} photograph")
        
        for search_query in queries_to_try:
            params = {
                "action": "query",
                "generator": "search",
                "gsrsearch": f"filetype:bitmap {search_query}",
                "gsrnamespace": 6,  # File namespace
                "gsrlimit": per_page * 5,  # Lấy nhiều hơn để lọc
                "prop": "imageinfo",
                "iiprop": "url|mime|size|extmetadata",
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
                        url = info.get("url", "")
                        w = info.get("width", 0)
                        h = info.get("height", 0)
                        
                        # Chỉ lấy ảnh bitmap, kích thước đủ lớn
                        if (mime.startswith("image/") 
                            and "svg" not in mime 
                            and url 
                            and w >= 400 
                            and h >= 300):
                            
                            # Tính điểm ưu tiên: ảnh lớn + tỉ lệ phù hợp
                            score = w * h
                            if w > h:  # Ảnh ngang → giảm ưu tiên nhẹ cho video dọc
                                score *= 0.8
                            
                            photos.append({
                                "url": url,
                                "title": v.get("title", ""),
                                "width": w,
                                "height": h,
                                "source": "wikimedia_commons",
                                "score": score,
                            })
                    
                    if photos:
                        # Sắp xếp theo score giảm dần
                        photos.sort(key=lambda x: x.get("score", 0), reverse=True)
                        return photos[:per_page]
                        
            except Exception as e:
                logger.warning(f"Wikimedia Commons search error for '{search_query}': {e}")

        # Nếu tìm kiếm cụm từ dài không có, tự động rút gọn
        words = query.strip().split()
        if len(words) > 2:
            simplified = " ".join(words[:2])
            logger.info(f"Wikimedia: Không thấy ảnh cho '{query}', thử lại với '{simplified}'...")
            return self.search_web_photos(simplified, per_page)
        
        return []

    # ──────────────────────────────────────────────────────────────────────────
    # TẦNG C: GOOGLE CUSTOM SEARCH (Nếu có API Key + CX)
    # ──────────────────────────────────────────────────────────────────────────

    def search_google_images(self, query: str, per_page: int = 3) -> List[Dict]:
        """
        Tìm kiếm ảnh qua Google Custom Search JSON API.
        Cần: GOOGLE_SEARCH_API_KEY + GOOGLE_SEARCH_CX trong .env
        Miễn phí: 100 queries/ngày.
        """
        if not self._google_search_key or not self._google_cx:
            return []
        
        try:
            params = {
                "key": self._google_search_key,
                "cx": self._google_cx,
                "q": query,
                "searchType": "image",
                "num": min(per_page, 10),
                "imgSize": "large",
                "imgType": "photo",
                "safe": "active",
                "rights": "cc_publicdomain|cc_attribute|cc_sharealike",
            }
            res = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params=params, timeout=10
            )
            if res.status_code == 200:
                items = res.json().get("items", [])
                photos = []
                for item in items:
                    img = item.get("image", {})
                    photos.append({
                        "url": item.get("link", ""),
                        "title": item.get("title", ""),
                        "width": img.get("width", 0),
                        "height": img.get("height", 0),
                        "source": "google_search",
                        "thumbnail": img.get("thumbnailLink", ""),
                    })
                return photos
            else:
                logger.debug(f"Google Image Search returned {res.status_code}")
        except Exception as e:
            logger.warning(f"Google Image Search error: {e}")
        
        return []

    # ──────────────────────────────────────────────────────────────────────────
    # TẦNG D: PEXELS VIDEO & PHOTO (Stock footage)
    # ──────────────────────────────────────────────────────────────────────────

    def search_videos(self, query: str, per_page: int = 3, orientation: str = "portrait") -> List[Dict]:
        """Tìm kiếm video trên Pexels theo từ khóa với tỷ lệ dọc (portrait 9:16)."""
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
            res = requests.get(self.base_url, headers=headers, params=params, timeout=10)
            if res.status_code == 200:
                return res.json().get("videos", [])
            elif res.status_code == 401:
                logger.warning("PEXELS_API_KEY không hợp lệ hoặc đã hết hạn.")
        except Exception as e:
            logger.warning(f"Lỗi khi tìm kiếm Pexels video: {e}")
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
                return res.json().get("photos", [])
            elif res.status_code == 401:
                logger.warning("PEXELS_API_KEY không hợp lệ hoặc đã hết hạn.")
        except Exception as e:
            logger.warning(f"Lỗi khi tìm kiếm Pexels photo: {e}")
        return []

    # ──────────────────────────────────────────────────────────────────────────
    # TẦNG E: AI ENTITY DETECTION (Dùng Gemini để nhận diện)
    # ──────────────────────────────────────────────────────────────────────────

    def ai_detect_entities(self, text: str) -> List[Dict]:
        """
        Dùng Gemini API nhận diện thực thể (nhân vật, địa danh, sự kiện thực) trong đoạn text.
        Trả về danh sách entities với metadata.
        Chỉ dùng khi heuristic không chắc chắn.
        """
        from app.utils.key_rotator import KeyRotator
        keys = settings.get_gemini_keys_pool()
        if not keys:
            return []
        
        rotator = KeyRotator(keys)
        key = rotator.get_key()
        if not key:
            return []
        
        prompt = f"""Analyze this text and identify ALL real-world entities (people, places, events, organizations).
Return ONLY a JSON array. Each element: {{"name": "Full Official Name", "type": "person|place|event|org", "search_query": "best search term to find their photo"}}

Text: "{text}"

If no real entities found, return empty array: []"""

        models = ["gemini-flash-lite-latest", "gemini-3-flash-preview"]
        for model in models:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json"}
                }
                r = requests.post(url, json=payload, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        raw = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "[]")
                        # Parse JSON
                        raw = raw.strip()
                        start = raw.find("[")
                        end = raw.rfind("]")
                        if start != -1 and end != -1:
                            raw = raw[start:end+1]
                        return json.loads(raw)
            except Exception as e:
                logger.debug(f"AI entity detection ({model}): {e}")
                continue
        
        return []

    # ──────────────────────────────────────────────────────────────────────────
    # SMART SEARCH ORCHESTRATOR — Tìm kiếm tổng hợp thông minh
    # ──────────────────────────────────────────────────────────────────────────

    def smart_search_media(
        self,
        visual_keyword: str,
        narration: str = "",
        per_page: int = 3,
        prefer_real: bool = True,
    ) -> List[Dict]:
        """
        Tìm kiếm media thông minh đa tầng, tự động phát hiện entity và chọn nguồn tối ưu.
        
        Luồng:
        1. Detect entity (heuristic + AI fallback)
        2. Nếu entity → Wikipedia → Wikimedia Commons → Google Search
        3. Nếu generic → Pexels → Wikimedia → Google Search
        
        Returns: List[Dict] với keys: url, title, source, width, height
        """
        all_results = []
        
        # ── Bước 1: Nhận diện thực thể ──
        is_entity, entity_name, entity_type = detect_real_entity(visual_keyword, narration)
        
        if is_entity and entity_name:
            logger.info(f"🔎 Phát hiện thực thể: '{entity_name}' (type={entity_type})")
            
            # ── A. Wikipedia/Wikidata — Ảnh chính thức ──
            wiki_img = self.search_wikipedia_entity_image(entity_name)
            if wiki_img:
                all_results.append(wiki_img)
                logger.info(f"✅ Tìm thấy ảnh Wikipedia cho '{entity_name}'")
            
            # ── B. Wikimedia Commons — Ảnh tài liệu ──
            wiki_search_terms = [
                f"{entity_name} photograph",
                entity_name,
            ]
            if entity_type == "person":
                wiki_search_terms.insert(0, f"{entity_name} portrait official")
            
            for term in wiki_search_terms:
                if len(all_results) >= per_page:
                    break
                commons_photos = self.search_web_photos(term, per_page - len(all_results))
                for p in commons_photos:
                    if p.get("url") not in [r.get("url") for r in all_results]:
                        all_results.append(p)

            # ── C. Google Custom Search ──
            if len(all_results) < per_page:
                google_results = self.search_google_images(entity_name, per_page - len(all_results))
                for g in google_results:
                    if g.get("url") not in [r.get("url") for r in all_results]:
                        all_results.append(g)
        
        else:
            # ── Không phải entity → dùng stock footage ──
            # Pexels Photos (Ken Burns)
            if self.api_key:
                pexels_photos = self.search_photos(visual_keyword, per_page)
                for p in pexels_photos:
                    src = p.get("src", {})
                    url = src.get("large2x") or src.get("portrait") or src.get("original")
                    if url:
                        all_results.append({
                            "url": url,
                            "title": p.get("alt", visual_keyword),
                            "source": "pexels",
                            "width": p.get("width", 0),
                            "height": p.get("height", 0),
                        })
            
            # Wikimedia Commons fallback
            if len(all_results) < per_page:
                commons = self.search_web_photos(visual_keyword, per_page - len(all_results))
                for c in commons:
                    if c.get("url") not in [r.get("url") for r in all_results]:
                        all_results.append(c)
        
        return all_results[:per_page]

    # ──────────────────────────────────────────────────────────────────────────
    # DOWNLOAD HELPER
    # ──────────────────────────────────────────────────────────────────────────

    def download_file(self, url: str, output_path: str) -> str:
        """Tải file media về máy tính với stream và timeout an toàn"""
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AI-Shorts-Bot/2.0"}
        res = requests.get(url, headers=headers, stream=True, timeout=30)
        res.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in res.iter_content(chunk_size=8192):
                f.write(chunk)
        return output_path
