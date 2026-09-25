import itertools
import logging
import re
import json
import requests
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from config.settings import settings

logger = logging.getLogger(__name__)

# User-Agent tuân thủ chuẩn Wikimedia API & Commons CDN (tránh lỗi 429)
WIKIMEDIA_USER_AGENT = "AIYouTubeStudioBot/2.0 (https://github.com/Giangvv1n/AI-youtube; contact: creator@studio.ai)"

# ──────────────────────────────────────────────────────────────────────────────
# ENTITY DETECTION — Thay thế danh sách hardcode bằng AI + heuristics thông minh
# ──────────────────────────────────────────────────────────────────────────────

# Bộ patterns nhận diện thực thể (tên riêng) theo heuristics ngôn ngữ
_ENTITY_INDICATORS = [
    # Chức danh / tước hiệu thường đi kèm tên người thật
    "president", "prime minister", "king", "queen", "emperor", "general",
    "chancellor", "senator", "governor", "pope", "sultan", "pharaoh",
    "dictator", "tyrant", "ceo", "founder", "inventor", "scientist",
    "astronaut", "cosmonaut", "explorer", "admiral", "marshal", "tsar",
    "tổng thống", "thủ tướng", "chủ tịch", "hoàng đế", "vua", "nữ hoàng",
    "giáo hoàng", "nhà khoa học", "nhà phát minh", "tướng",
]

# Sự kiện / tổ chức lịch sử nổi tiếng (đã loại bỏ 'un' và dùng word boundary regex)
_KNOWN_EVENTS_ORGS = [
    "world war i", "world war ii", "world war 1", "world war 2", "ww1", "ww2",
    "cold war", "vietnam war", "korean war", "civil war",
    "french revolution", "russian revolution", "industrial revolution",
    "holocaust", "9/11", "pearl harbor", "d-day", "moon landing",
    "manhattan project", "chernobyl", "fukushima",
    "nasa", "cia", "fbi", "kgb", "nato", "united nations",
    "kremlin", "white house", "pentagon", "buckingham palace",
    "great wall of china", "pyramids", "colosseum", "taj mahal",
]

_EVENT_REGEXES = [
    (re.compile(rf"\b{re.escape(p)}\b", re.IGNORECASE), p.title())
    for p in sorted(_KNOWN_EVENTS_ORGS, key=len, reverse=True)
]

# Stopwords tiếng Anh viết hoa đầu câu thường gặp (tránh nhận nhầm làm tên người)
_SENTENCE_STARTERS = {
    "this", "that", "these", "those", "when", "what", "where", "which",
    "who", "whom", "whose", "why", "how", "people", "drop", "cold", "hot",
    "he", "she", "it", "they", "we", "you", "his", "her", "their", "our",
    "in", "on", "at", "if", "the", "a", "an", "but", "and", "so", "then",
    "there", "here", "just", "never", "always", "one", "two", "three",
    "first", "last", "some", "many", "most", "all", "every", "with",
    "from", "into", "onto", "upon", "about", "after", "before", "while",
    "because", "since", "until", "although", "even", "more",
}


# Địa danh / quốc gia phổ biến (tránh nhận nhầm làm người khi chỉ nhắc thoáng qua trong narration)
_COMMON_GEOPOLITICAL = {
    "united states", "united kingdom", "soviet union", "north korea", "south korea",
    "saudi arabia", "south africa", "new zealand", "united arab emirates",
    "america", "russia", "china", "vietnam", "japan", "germany", "france", "england",
    "britain", "uk", "usa", "italy", "spain", "canada", "mexico", "brazil", "india",
}


def _extract_proper_nouns(text: str, is_narration: bool = False) -> List[str]:
    """Trích xuất chuỗi tên riêng nhiều từ (hỗ trợ cả chữ lót như John D. Rockefeller)."""
    words = text.split()
    sequences = []
    curr = []
    for i, w in enumerate(words):
        clean = re.sub(r'[^\w]', '', w)
        if not clean:
            continue
        is_cap = clean[0].isupper()
        is_middle_initial = len(clean) == 1 and is_cap
        is_normal_cap = is_cap and not clean.isupper() and len(clean) >= 2
        is_acronym = clean.isupper() and 2 <= len(clean) <= 4

        if is_normal_cap or is_middle_initial or is_acronym:
            if is_narration and i == 0 and clean.lower() in _SENTENCE_STARTERS:
                continue
            curr.append(clean)
        else:
            if len(curr) >= 2:
                sequences.append(" ".join(curr))
            curr = []
    if len(curr) >= 2:
        sequences.append(" ".join(curr))

    valid = []
    for s in sequences:
        s_lower = s.lower()
        if is_narration and s_lower in _COMMON_GEOPOLITICAL:
            continue
        if all(w.lower() in _SENTENCE_STARTERS for w in s.split()):
            continue
        valid.append(s)
    return valid


def detect_real_entity(visual_keyword: str, narration: str = "") -> Tuple[bool, Optional[str], str]:
    """
    Phát hiện thông minh xem cảnh có liên quan đến nhân vật/sự kiện thực hay không.
    Ưu tiên visual_keyword trước để phản ánh đúng ý đồ hình ảnh phân cảnh.
    
    Returns:
        (is_entity, entity_name, entity_type)
        entity_type: "person" | "place" | "event" | "org" | ""
    """
    vk = visual_keyword.strip()
    vk_lower = vk.lower()

    # ── 1. ƯU TIÊN CAO NHẤT: Tên riêng trong visual_keyword ──
    # "Vladimir Putin", "John D. Rockefeller", "Albert Einstein"
    names = _extract_proper_nouns(vk, is_narration=False)
    if names:
        return True, max(names, key=len), "person"

    # ── 2. Kiểm tra Title Indicators (President X, King Y...) trong visual_keyword ──
    for indicator in _ENTITY_INDICATORS:
        m = re.search(rf"\b{re.escape(indicator)}\s+([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*)", vk, re.IGNORECASE)
        if m:
            return True, m.group(1), "person"

    # ── 3. Kiểm tra Known Events/Orgs trong visual_keyword (Word-Boundary) ──
    for regex, title in _EVENT_REGEXES:
        if regex.search(vk_lower):
            return True, title, "event"

    # ── 4. Kiểm tra trong narration nếu visual_keyword là mô tả chung ──
    if narration:
        narr = narration.strip()
        narr_lower = narr.lower()

        # Kiểm tra Title Indicators trong narration
        for indicator in _ENTITY_INDICATORS:
            m = re.search(rf"\b{re.escape(indicator)}\s+([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*)", narr, re.IGNORECASE)
            if m:
                return True, m.group(1), "person"

        # Kiểm tra Proper Nouns trong narration (loại trừ từ đầu câu & quốc gia)
        narr_names = _extract_proper_nouns(narr, is_narration=True)
        if narr_names:
            return True, max(narr_names, key=len), "person"

        # Kiểm tra Events/Orgs trong narration
        for regex, title in _EVENT_REGEXES:
            if regex.search(narr_lower):
                return True, title, "event"

    return False, None, ""


_TITLE_COMMON_WORDS = {
    "dark", "psychology", "mind", "power", "history", "secrets", "secret", "tricks",
    "hacks", "truth", "facts", "rules", "habits", "ways", "things", "signs", "clues",
    "liar", "rich", "richest", "man", "men", "world", "ghost", "brain", "absolute",
    "cold", "terrifying", "lurking", "deep", "mystery", "mysteries", "the", "of",
    "and", "to", "in", "about", "what", "how", "why", "who", "for", "with", "a", "an",
    "viral", "shorts", "youtube", "best", "top", "great", "ultimate", "spot"
}

_KNOWN_FIGURES = {
    "putin": "Vladimir Putin",
    "vladimir putin": "Vladimir Putin",
    "rockefeller": "John D. Rockefeller",
    "john d rockefeller": "John D. Rockefeller",
    "john d. rockefeller": "John D. Rockefeller",
    "einstein": "Albert Einstein",
    "albert einstein": "Albert Einstein",
    "napoleon": "Napoleon Bonaparte",
    "napoleon bonaparte": "Napoleon Bonaparte",
    "tesla": "Nikola Tesla",
    "nikola tesla": "Nikola Tesla",
    "musk": "Elon Musk",
    "elon musk": "Elon Musk",
    "trump": "Donald Trump",
    "donald trump": "Donald Trump",
    "caesar": "Julius Caesar",
    "julius caesar": "Julius Caesar",
    "hitler": "Adolf Hitler",
    "adolf hitler": "Adolf Hitler",
    "stalin": "Joseph Stalin",
    "joseph stalin": "Joseph Stalin",
    "churchill": "Winston Churchill",
    "winston churchill": "Winston Churchill",
    "lincoln": "Abraham Lincoln",
    "abraham lincoln": "Abraham Lincoln",
    "washington": "George Washington",
    "george washington": "George Washington",
    "jobs": "Steve Jobs",
    "steve jobs": "Steve Jobs",
    "gates": "Bill Gates",
    "bill gates": "Bill Gates",
    "newton": "Isaac Newton",
    "isaac newton": "Isaac Newton",
    "darwin": "Charles Darwin",
    "charles darwin": "Charles Darwin",
    "da vinci": "Leonardo da Vinci",
    "leonardo da vinci": "Leonardo da Vinci",
}


def detect_video_primary_entity(
    topic: str = "",
    title: str = "",
    narration: str = "",
    tags: List[str] = None
) -> Tuple[Optional[str], str]:
    """
    Xác định thực thể/nhân vật chính xuyên suốt toàn bộ video.
    Giúp pipeline tự động chuyển sang chế độ tài liệu điện ảnh (Documentary Mode),
    thu thập trọn bộ ảnh thật từ Wikipedia/Wikimedia Commons cho tất cả các phân cảnh.
    """
    tags = tags or []
    full_text = f"{topic} {title}".strip()
    full_text_lower = full_text.lower()

    # 1. Tra cứu trực tiếp trong danh sách nhân vật đã biết
    for fig_key, fig_full in _KNOWN_FIGURES.items():
        if re.search(rf"\b{re.escape(fig_key)}\b", full_text_lower):
            return fig_full, "person"

    # 2. Kiểm tra trong tags
    for tag in tags:
        t_clean = re.sub(r'[^\w\s]', '', tag).strip().lower()
        if t_clean in _KNOWN_FIGURES:
            return _KNOWN_FIGURES[t_clean], "person"

    # 3. Kiểm tra Known Events
    for regex, ev_title in _EVENT_REGEXES:
        if regex.search(full_text_lower):
            return ev_title, "event"

    # 4. Kiểm tra trong narration
    if narration:
        for fig_key, fig_full in _KNOWN_FIGURES.items():
            if re.search(rf"\b{re.escape(fig_key)}\b", narration.lower()):
                return fig_full, "person"

        narr_names = _extract_proper_nouns(narration, is_narration=True)
        valid_names = [n for n in narr_names if not any(w.lower() in _TITLE_COMMON_WORDS for w in n.split())]
        if valid_names:
            counts = {}
            for n in valid_names:
                counts[n] = counts.get(n, 0) + 1
            most_common = max(counts.items(), key=lambda x: x[1])[0]
            return most_common, "person"

    # 5. Lọc proper nouns từ topic nếu không phải title common words
    topic_names = _extract_proper_nouns(topic, is_narration=False)
    filtered = [n for n in topic_names if not any(w.lower() in _TITLE_COMMON_WORDS for w in n.split())]
    if filtered:
        return max(filtered, key=len), "person"

    return None, ""


# ──────────────────────────────────────────────────────────────────────────────
# INTELLIGENT VISUAL SEARCH & SEMANTIC MATCHING HELPERS
# ──────────────────────────────────────────────────────────────────────────────

# Bộ từ khóa nhiễu cần loại trừ khi tìm ảnh nhân vật lịch sử / danh nhân
# Tránh lọt ảnh đô thị, xe cộ, tòa nhà hiện đại (VD: "Ho Chi Minh City", "Vietcombank tower", "street hawker")
FORBIDDEN_ENTITY_NOISE = {
    'city', 'tower', 'hotel', 'street', 'hawker', 'delivery', 'mailbox', 'road',
    'district', 'statue', 'scooter', 'motorcycle', 'traffic', 'building', 'bridge',
    'stadium', 'airport', 'hospital', 'university', 'restaurant', 'cafe', 'store',
    'shop', 'skyline', 'apartment', 'plaza', 'mall', 'highway', 'park', 'avenue',
    'billboard', 'sidewalk', 'taxi', 'pedestrian', 'alley', 'intersection'
}

# Các từ ngữ nhiễu liên quan đến thiết bị/kỹ thuật quay chụp (camera fluff) cần lọc bỏ để tìm kiếm ảnh chuẩn
CAMERA_AND_STYLE_FLUFF = {
    "cinematic", "lighting", "dramatic", "hyperrealistic", "photorealistic", "8k", "4k",
    "ultra-sharp", "ultra", "sharp", "shot", "on", "35mm", "film", "imax", "anamorphic",
    "lens", "macro", "extreme", "close-up", "close", "up", "wide-angle", "wide", "angle",
    "volumetric", "fog", "atmospheric", "masterpiece", "award-winning", "award", "winning",
    "portrait", "still", "render", "unreal", "engine", "octane", "vray", "style", "depth",
    "of", "field", "bokeh", "studio", "photography", "photo", "image", "picture",
    "vintage", "dark", "moody", "high-end", "prestigious", "editorial", "forbes", "aesthetic",
    "detailed", "hyper-detailed", "unrealengine", "octanerender"
}


def clean_scene_search_query(raw_query: str, max_words: int = 3) -> str:
    """
    Làm sạch query tìm kiếm ảnh: loại bỏ toàn bộ từ rác kỹ thuật/nhiếp ảnh,
    giữ lại đúng danh từ/chủ thể trọng tâm (2-3 từ).
    """
    if not raw_query:
        return ""
    words = re.sub(r'[^\w\s-]', ' ', raw_query).split()
    clean = [w for w in words if w.lower() not in CAMERA_AND_STYLE_FLUFF and len(w) > 1]
    if not clean:
        clean = [w for w in words if len(w) > 1]
    return " ".join(clean[:max_words]).strip()


def synthesize_scene_query(
    narration: str = "",
    visual_keyword: str = "",
    stock_query: Optional[str] = None,
    visual_prompt: Optional[str] = None,
    primary_entity: Optional[str] = None,
) -> Tuple[str, str, str]:
    """
    Tổng hợp query tìm kiếm chính xác, tối ưu cho từng công cụ tìm ảnh.
    
    Returns:
        (primary_query, fallback_query, intent)
        intent: "historical_entity" | "specific_object" | "concept_photo"
    """
    cleaned_stock = clean_scene_search_query(stock_query or "", max_words=3)
    cleaned_vk = clean_scene_search_query(visual_keyword or "", max_words=4)

    # Phát hiện thực thể hoặc sự kiện lịch sử trong scene
    is_ent, ent_name, ent_type = detect_real_entity(visual_keyword, narration)
    target_ent = ent_name or (
        primary_entity
        if primary_entity and any(w.lower() in narration.lower() for w in primary_entity.lower().split())
        else None
    )

    # Trích xuất năm lịch sử nếu có
    years = re.findall(r'\b(18\d\d|19\d\d|20\d\d)\b', f"{narration} {visual_keyword}")
    year_str = years[0] if years else ""

    if target_ent:
        primary_query = f"{target_ent} {year_str}".strip() if year_str else target_ent
        fallback_query = cleaned_stock or cleaned_vk or target_ent
        intent = "historical_entity"
        return primary_query, fallback_query, intent

    if cleaned_stock and len(cleaned_stock.split()) >= 1:
        primary_query = cleaned_stock
        fallback_query = cleaned_vk if cleaned_vk != cleaned_stock else ""
        intent = "specific_object"
        return primary_query, fallback_query, intent

    if cleaned_vk:
        primary_query = " ".join(cleaned_vk.split()[:3])
        fallback_query = " ".join(cleaned_vk.split()[:2])
        intent = "concept_photo"
        return primary_query, fallback_query, intent

    return "cinematic atmosphere", "dark moody", "concept_photo"


def match_best_pool_photo(
    scene_narration: str,
    scene_kw: str,
    pool: List[Dict],
    used_urls: set,
    threshold: int = 25
) -> Optional[Dict]:
    """
    Tìm ảnh trong pool có độ khớp ngữ nghĩa cao nhất với cảnh hiện tại.
    Thay vì gán tuần tự theo index, thuật toán tính điểm tương đồng:
    - Trùng năm lịch sử (1911, 1945, 1870, 1920...): +65 điểm
    - Trùng từ khóa ngữ nghĩa chính (tàu, đại hội, tuyên ngôn, dầu mỏ, gia đình...): +35-55 điểm
    - Khớp từ trong tiêu đề ảnh: +12 điểm/từ
    Nếu không có ảnh nào trong pool đạt ngưỡng điểm -> Trả về None để hệ thống
    tự tìm kiếm tư liệu đặc thù cho cảnh đó (tránh ép gán ảnh sai lệch).
    """
    if not pool:
        return None

    full_scene_text = f"{scene_narration} {scene_kw}".lower()

    # Trích xuất năm lịch sử
    scene_years = set(re.findall(r'\b(18\d\d|19\d\d|20\d\d)\b', full_scene_text))

    concept_mappings = {
        ("tàu", "thuyền", "ship", "steamship", "boat", "port", "cảng", "latouche", "treville"): 50,
        ("tours", "đại hội", "congres", "pháp", "france", "nguyễn ái quốc", "nguyen ai quoc"): 45,
        ("ba đình", "ba dinh", "tuyên ngôn", "proclamation", "độc lập", "independence", "quảng trường"): 55,
        ("phụ bếp", "bếp", "kitchen", "cook", "than", "cào than", "rửa bát"): 40,
        ("dầu", "oil", "refinery", "nhà máy", "standard oil", "cleveland"): 50,
        ("tiền", "money", "sổ sách", "ledger", "accounting", "bank", "ngân hàng"): 40,
        ("gia đình", "family", "vợ", "con", "children", "wife"): 35,
        ("chân dung", "portrait", "leader", "chủ tịch", "tổng thống", "president"): 25,
    }

    best_candidate = None
    best_score = -1

    for cand in pool:
        url = cand.get("url")
        if not url or url in used_urls:
            continue

        p_title = cand.get("title", "").lower()
        score = 0

        # 1. Điểm trùng năm
        cand_years = set(re.findall(r'\b(18\d\d|19\d\d|20\d\d)\b', p_title))
        if scene_years and cand_years:
            if scene_years.intersection(cand_years):
                score += 65
            else:
                score -= 20  # Lệch năm lịch sử -> giảm ưu tiên

        # 2. Điểm khái niệm cốt lõi
        for triggers, pts in concept_mappings.items():
            if any(tr in full_scene_text for tr in triggers) and any(tr in p_title for tr in triggers):
                score += pts

        # 3. Điểm khớp từ thông thường (từ > 3 ký tự)
        scene_words = {w for w in re.sub(r'[^\w\s]', '', full_scene_text).split() if len(w) > 3}
        title_words = {w for w in re.sub(r'[^\w\s]', '', p_title).split() if len(w) > 3}
        overlap = scene_words.intersection(title_words)
        score += len(overlap) * 12

        if score > best_score:
            best_score = score
            best_candidate = cand

    if best_candidate and best_score >= threshold:
        return best_candidate

    return None


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
        headers = {"User-Agent": WIKIMEDIA_USER_AGENT}
        
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
                
                # Lấy ảnh chính (pageimage): kiểm tra cả original lẫn thumbnail
                original = page_data.get("original", {})
                thumbnail = page_data.get("thumbnail", {})
                
                orig_url = original.get("source") or ""
                thumb_url = thumbnail.get("source") or ""

                chosen_url = None
                chosen_w, chosen_h = 0, 0

                # Ưu tiên thumbnail (1200px chuẩn HD, cache trên CDN Varnish toàn cầu, tránh 429 và file gốc quá nặng)
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
                
                # Fallback: Lấy ảnh đầu tiên hợp lệ từ danh sách images
                images = page_data.get("images", [])
                for img in images:
                    img_title = img.get("title", "")
                    # Bỏ qua icon, logo nhỏ, commons-logo, flag, map, svg
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
                        # Đệ quy gọi lại với tên chính xác từ Wikipedia
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
        # Loại bỏ query parameters (?...) và fragments (#...)
        clean_url = url.split("?")[0].split("#")[0].lower()

        # Loại trừ dứt khoát định dạng vector / tài liệu / âm thanh
        if any(clean_url.endswith(ext) for ext in [".svg", ".pdf", ".ogg", ".ogv", ".webm", ".tif", ".tiff", ".gif"]):
            return False
        if ".svg" in clean_url:
            return False

        # Loại trừ icon, biểu tượng, quốc huy, cờ, bản đồ, logo
        if any(skip in clean_url for skip in [
            "commons-logo", "flag_of", "coat_of_arms", "emblem", "symbol", "icon", "logo", "map", "diagram", "chart"
        ]):
            return False

        return True

    # ──────────────────────────────────────────────────────────────────────────
    # TẦNG B: WIKIMEDIA COMMONS — Tìm kiếm nâng cao
    # ──────────────────────────────────────────────────────────────────────────

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
        Cải thiện: Dùng thumbnail 1280px từ CDN Varnish để tránh lỗi 429 và tải nhanh.
        Hỗ trợ filter_entity_noise để lọc bỏ ảnh đường phố/đô thị khi tìm nhân vật.
        """
        headers = {"User-Agent": WIKIMEDIA_USER_AGENT}
        
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
                        
                        # Lọc bỏ ảnh nhiễu địa danh nếu đang tìm người thật
                        if filter_entity_noise and self._is_noisy_entity_file(file_title, url):
                            continue
                        
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
                                "title": file_title,
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
            return self.search_web_photos(simplified, per_page, filter_entity_noise=filter_entity_noise)
        
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
    # TẦNG C2: OPENVERSE (800+ triệu ảnh Creative Commons, bảo tàng, lưu trữ mở)
    # ──────────────────────────────────────────────────────────────────────────

    def search_openverse(self, query: str, per_page: int = 5) -> List[Dict]:
        """
        Tìm kiếm hình ảnh mở tự do trên Openverse API (Creative Commons, bảo tàng, thư viện quốc tế).
        Miễn phí 100%, không cần API key, cực kỳ giàu ảnh lịch sử và tư liệu chân thật.
        """
        headers = {"User-Agent": "AIYouTubeStudioBot/2.0 (https://github.com/Giangvv1n/AI-youtube)"}
        params = {
            "q": query,
            "page_size": min(per_page * 2, 20),
        }
        try:
            res = requests.get(
                "https://api.openverse.org/v1/images/",
                params=params, headers=headers, timeout=10
            )
            if res.status_code == 200:
                data = res.json()
                results = data.get("results", [])
                photos = []
                for item in results:
                    url = item.get("url", "")
                    if not self._is_valid_image_url(url):
                        continue
                    w = item.get("width") or 1080
                    h = item.get("height") or 1920
                    photos.append({
                        "url": url,
                        "title": item.get("title", query),
                        "width": w,
                        "height": h,
                        "source": "openverse",
                        "thumbnail": item.get("thumbnail") or url,
                    })
                    if len(photos) >= per_page:
                        break
                return photos
        except Exception as e:
            logger.debug(f"Openverse search error for '{query}': {e}")
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

    def search_best_video_clip(self, query: str) -> Optional[Dict]:
        """
        Tìm kiếm 1 video clip dọc (9:16 portrait) chất lượng cao nhất từ Pexels.
        Trả về dictionary gồm link tải MP4 1080p và metadata.
        """
        videos = self.search_videos(query, per_page=6, orientation="portrait")
        for v in videos:
            v_files = v.get("video_files", [])
            # Lọc các file MP4 tỉ lệ dọc (w <= h)
            portrait_files = [
                vf for vf in v_files
                if vf.get("width", 0) <= vf.get("height", 0) and (vf.get("link", "").endswith(".mp4") or "mp4" in vf.get("file_type", ""))
            ]
            if portrait_files:
                # Ưu tiên độ phân giải cao nhất (>= 1080p hoặc 720p)
                hd_files = sorted(portrait_files, key=lambda x: x.get("height", 0), reverse=True)
                best = hd_files[0]
                return {
                    "url": best.get("link"),
                    "title": f"Pexels Video: {query}",
                    "width": best.get("width", 1080),
                    "height": best.get("height", 1920),
                    "duration": v.get("duration", 0),
                    "media_type": "video",
                    "source": "pexels_video",
                    "thumbnail": v.get("image", ""),
                }
        return None

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

    def search_multi_source_images(self, query: str, per_page: int = 6) -> List[Dict]:
        """
        Tìm kiếm tổng hợp đa nguồn từ các thư viện ảnh hàng đầu:
        Wikipedia / Wikimedia Commons + Openverse (800M+ ảnh) + Google Search + Pexels Photos.
        Tự động khử trùng lặp và phân loại nguồn.
        """
        combined = []
        seen_urls = set()

        def add_item(item):
            if not item:
                return
            u = item.get("url")
            if u and u not in seen_urls and self._is_valid_image_url(u):
                seen_urls.add(u)
                combined.append(item)

        # 1. Wikipedia Ảnh chính thức
        wiki_img = self.search_wikipedia_entity_image(query)
        if wiki_img:
            add_item(wiki_img)

        # 2. Wikimedia Commons
        commons = self.search_web_photos(query, per_page=per_page)
        for c in commons:
            add_item(c)

        # 3. Openverse
        openverse_items = self.search_openverse(query, per_page=per_page)
        for o in openverse_items:
            add_item(o)

        # 4. Google Custom Search (nếu có key)
        if self._google_search_key and self._google_cx:
            g_items = self.search_google_images(query, per_page=per_page)
            for g in g_items:
                add_item(g)

        # 5. Pexels Photos
        if self.api_key:
            px_items = self.search_photos(query, per_page=per_page)
            for p in px_items:
                src = p.get("src", {})
                url = src.get("large2x") or src.get("portrait") or src.get("original")
                if url:
                    add_item({
                        "url": url,
                        "title": p.get("alt", query),
                        "width": p.get("width", 0),
                        "height": p.get("height", 0),
                        "source": "pexels",
                        "thumbnail": src.get("medium", url),
                    })

        return combined[:per_page]

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

        models = ["gemini-3-flash-preview", "gemini-3.5-flash", "gemini-flash-latest"]
        for model in models:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}]
                }
                r = requests.post(url, json=payload, timeout=12)
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
                elif r.status_code in (429, 403):
                    key = rotator.rotate_key()
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
        """
        Thu thập bộ sưu tập ảnh tài liệu thực tế chất lượng cao đa dạng cho thực thể chính.
        Đảm bảo video có đủ ảnh khác nhau cho từng phân cảnh, không bị trùng lặp.
        Loại trừ triệt để ảnh địa danh/đô thị/xe cộ hiện đại gây nhiễu nhân vật lịch sử.
        """
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

        # 1. Ảnh chân dung chính thức từ bài Wikipedia
        wiki_portrait = self.search_wikipedia_entity_image(entity_name)
        if wiki_portrait and not (entity_type == "person" and self._is_noisy_entity_file(wiki_portrait.get("title", ""), wiki_portrait.get("url", ""))):
            add_unique(wiki_portrait)

        # 2. Tìm kiếm nhiều góc ảnh từ Wikimedia Commons với dấu ngoặc kép và từ khóa lịch sử
        search_queries = [
            f'"{entity_name}"',
            f'"{entity_name}" portrait',
            f'"{entity_name}" photograph',
        ]
        if entity_type == "person":
            search_queries.extend([
                f'"{entity_name}" 1945',
                f'"{entity_name}" official',
                f'"{entity_name}" vintage',
            ])
            # Hỗ trợ nhân vật lịch sử nổi tiếng đặc biệt
            ent_lower = entity_name.lower()
            if "hồ chí minh" in ent_lower or "ho chi minh" in ent_lower:
                search_queries.extend([
                    '"Nguyen Ai Quoc"',
                    '"Ho Chi Minh" 1946',
                    'Ba Dinh Square 1945',
                ])
            elif "rockefeller" in ent_lower:
                search_queries.extend([
                    '"John D. Rockefeller" vintage',
                    'Standard Oil refinery 1880',
                ])

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

        # 3. Mở rộng từ Openverse (kho lưu trữ ảnh mở, bảo tàng)
        if len(pool) < count:
            ov_queries = [f'"{entity_name}"', f"{entity_name} archive"]
            for oq in ov_queries:
                if len(pool) >= count:
                    break
                ov_results = self.search_openverse(oq, per_page=min(count - len(pool), 6))
                for item in ov_results:
                    add_unique(item)
                    if len(pool) >= count:
                        break

        # 4. Google Custom Search (nếu có key)
        if len(pool) < count and self._google_search_key and self._google_cx:
            g_results = self.search_google_images(f"{entity_name} photo portrait", per_page=min(count - len(pool), 5))
            for item in g_results:
                add_unique(item)

        logger.info(f"📸 Đã thu thập được {len(pool)} ảnh tài liệu thực tế chuẩn cho '{entity_name}' (Wikimedia + Openverse)")
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
    ) -> Optional[Dict]:
        """
        Tìm kiếm hình ảnh đa tầng tối ưu cao nhất cho 1 phân cảnh:
        1. Query Synthesizer: làm sạch và tổng hợp từ khóa cốt lõi (2-3 từ)
        2. Nếu phân cảnh nhắc đến thực thể/sự kiện/địa danh lịch sử cụ thể -> Wikipedia / Wikimedia Commons
        3. Pexels HD Photos (Portrait 9:16) siêu nét cho đồ vật, con người, phong cảnh thực
        4. Openverse Archival (800M+ ảnh bảo tàng & thư viện thế giới)
        5. Pexels Video (chỉ dùng cho chuyển động tự nhiên/không gian/thành phố khi không có entity)
        """
        used = used_urls if used_urls is not None else set()
        primary_q, fallback_q, intent = synthesize_scene_query(
            narration=scene_narration,
            visual_keyword=visual_keyword,
            stock_query=stock_query,
            visual_prompt=visual_prompt,
            primary_entity=primary_entity,
        )

        logger.info(
            f"[MediaAgent] Scene {scene_id} Search: intent='{intent}' | "
            f"query='{primary_q}' (fallback='{fallback_q}')"
        )

        # ── TẦNG 1: Wikipedia / Wikimedia Commons cho thực thể / sự kiện lịch sử ──
        if intent == "historical_entity" or primary_entity:
            # A. Thử tìm trên Wikipedia
            wiki_img = self.search_wikipedia_entity_image(primary_q)
            if wiki_img and wiki_img.get("url") and wiki_img["url"] not in used:
                if not (entity_type == "person" and self._is_noisy_entity_file(wiki_img.get("title", ""), wiki_img["url"])):
                    return wiki_img

            # B. Thử tìm trên Wikimedia Commons
            wiki_queries = [f'"{primary_q}"', primary_q]
            if fallback_q and fallback_q != primary_q:
                wiki_queries.append(fallback_q)

            for wq in wiki_queries:
                commons = self.search_web_photos(
                    wq,
                    per_page=4,
                    filter_entity_noise=(entity_type == "person")
                )
                for c in commons:
                    u = c.get("url")
                    if u and u not in used and self._is_valid_image_url(u):
                        return c

        # ── TẦNG 2: Pexels HD Photos (Portrait 9:16) — Đồ vật, đời sống, phong cảnh ──
        # Tỷ lệ dọc 9:16 siêu nét (4K/HD), màu sắc điện ảnh, cực kỳ phù hợp hiệu ứng Ken Burns
        if self.api_key:
            px_queries = [primary_q]
            if fallback_q and fallback_q != primary_q:
                px_queries.append(fallback_q)

            for pq in px_queries:
                photos = self.search_photos(pq, per_page=4, orientation="portrait")
                for p in photos:
                    src = p.get("src", {})
                    img_url = src.get("large2x") or src.get("portrait") or src.get("original")
                    if img_url and img_url not in used and self._is_valid_image_url(img_url):
                        return {
                            "url": img_url,
                            "title": p.get("alt", pq),
                            "source": "pexels_photo",
                            "media_type": "image",
                            "width": p.get("width", 1080),
                            "height": p.get("height", 1920),
                        }

        # ── TẦNG 3: Openverse Archival (800M+ ảnh bảo tàng & thư viện thế giới) ──
        ov_queries = [primary_q]
        if fallback_q and fallback_q != primary_q:
            ov_queries.append(fallback_q)

        for oq in ov_queries:
            ov_items = self.search_openverse(oq, per_page=3)
            for item in ov_items:
                u = item.get("url")
                if u and u not in used and self._is_valid_image_url(u):
                    return {
                        "url": u,
                        "title": item.get("title", oq),
                        "source": "openverse",
                        "media_type": "image",
                        "width": item.get("width", 1080),
                        "height": item.get("height", 1920),
                    }

        # ── TẦNG 4: Pexels Video Clip (Chỉ dùng khi KHÔNG phải nhân vật tài liệu) ──
        if not primary_entity and self.api_key:
            best_vid = self.search_best_video_clip(primary_q)
            if best_vid and best_vid.get("url") and best_vid["url"] not in used:
                return best_vid

        return None

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

    def download_file(self, url: str, output_path: str, max_retries: int = 3) -> str:
        """Tải file media về máy tính với stream, timeout an toàn, retry backoff chống 429 và kiểm định bitmap raster bằng Pillow"""
        import time
        headers = {
            "User-Agent": (
                WIKIMEDIA_USER_AGENT
                if any(domain in url for domain in ["wikimedia.org", "wikipedia.org"])
                else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
        }
        
        last_err = None
        for attempt in range(max_retries):
            try:
                res = requests.get(url, headers=headers, stream=True, timeout=25)
                if res.status_code == 429:
                    sleep_time = 1.5 * (attempt + 1)
                    logger.warning(f"Rate limited (429) khi tải {url[:60]}... Đợi {sleep_time}s rồi thử lại")
                    time.sleep(sleep_time)
                    continue
                res.raise_for_status()
                with open(output_path, "wb") as f:
                    for chunk in res.iter_content(chunk_size=8192):
                        f.write(chunk)
                break
            except Exception as e:
                last_err = e
                if attempt < max_retries - 1:
                    time.sleep(1.0)
                else:
                    raise last_err

        # Kiểm tra tính toàn vẹn nếu là file ảnh (.jpg, .jpeg, .png, .webp)
        ext = Path(output_path).suffix.lower()
        if ext in [".jpg", ".jpeg", ".png", ".webp"]:
            try:
                from PIL import Image
                with Image.open(output_path) as im:
                    im.verify()  # Bắt lỗi file hỏng, file SVG đội lốt .png, hoặc file HTML lỗi

                # Mở lại để chuẩn hóa sang RGB nếu mode không tương thích FFmpeg
                with Image.open(output_path) as im:
                    if im.mode not in ("RGB",):
                        rgb_im = im.convert("RGB")
                        save_fmt = "JPEG" if ext in [".jpg", ".jpeg"] else "PNG"
                        rgb_im.save(output_path, format=save_fmt, quality=95)
            except Exception as e:
                # File ảnh hỏng hoặc không phải bitmap hợp lệ -> xóa ngay để tránh làm sập FFmpeg
                try:
                    Path(output_path).unlink(missing_ok=True)
                except Exception:
                    pass
                raise ValueError(f"File tải về không phải ảnh bitmap raster hợp lệ ({e}): {url}")

        return output_path
