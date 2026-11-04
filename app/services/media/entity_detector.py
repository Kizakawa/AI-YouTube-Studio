"""
app.services.media.entity_detector — Bộ công cụ nhận diện thực thể, tri thức lịch sử & ngữ nghĩa media.

Chức năng:
1. Nhận diện nhân vật lịch sử, nền văn minh, địa danh, sự kiện trong kịch bản.
2. Lọc bỏ các từ khóa nhiễu (camera fluff, từ trừu tượng, từ ngữ thông thường).
3. Tổng hợp query tìm kiếm tư liệu chính xác (synthesize_scene_query).
4. Tính toán mức độ tương đồng ngữ nghĩa để chọn ảnh khớp nhất từ media pool (match_best_pool_photo).
"""

import re
import logging
from typing import List, Dict, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# User-Agent tuân thủ chuẩn Wikimedia API & Commons CDN (tránh lỗi 429)
WIKIMEDIA_USER_AGENT = "AIYouTubeStudioBot/2.0 (https://github.com/Giangvv1n/AI-youtube; contact: creator@studio.ai)"

# ──────────────────────────────────────────────────────────────────────────────
# ENTITY DETECTION — Heuristics & Knowledge Base
# ──────────────────────────────────────────────────────────────────────────────

_ENTITY_INDICATORS = [
    # Chức danh / tước hiệu thường đi kèm tên người thật
    "president", "prime minister", "king", "queen", "emperor", "general",
    "chancellor", "senator", "governor", "pope", "sultan", "pharaoh",
    "dictator", "tyrant", "ceo", "founder", "inventor", "scientist",
    "astronaut", "cosmonaut", "explorer", "admiral", "marshal", "tsar",
    "tổng thống", "thủ tướng", "chủ tịch", "hoàng đế", "vua", "nữ hoàng",
    "giáo hoàng", "nhà khoa học", "nhà phát minh", "tướng",
]

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

_COMMON_GEOPOLITICAL = {
    "united states", "united kingdom", "soviet union", "north korea", "south korea",
    "saudi arabia", "south africa", "new zealand", "united arab emirates",
    "america", "russia", "china", "vietnam", "japan", "germany", "france", "england",
    "britain", "uk", "usa", "italy", "spain", "canada", "mexico", "brazil", "india",
    "trung mỹ", "bắc mỹ", "nam mỹ", "châu âu", "châu á", "châu phi", "châu mỹ", "châu đại dương",
    "central america", "north america", "south america", "latin america", "europe", "asia", "africa",
    "hà nội", "sài gòn", "đà nẵng", "hồ chí minh city", "hcmc", "trung my", "nam my", "bac my",
}

_NON_ENTITY_WORDS = {
    # Đại từ & trợ động từ
    "am", "i", "me", "my", "myself", "you", "your", "yourself", "he", "him", "his", "himself",
    "she", "her", "hers", "herself", "it", "its", "itself", "we", "us", "our", "ours", "ourselves",
    "they", "them", "their", "theirs", "themselves", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having", "do", "does", "did", "doing", "will", "would", "shall", "should",
    "can", "could", "may", "might", "must", "ought",
    # Hành động & quan sát thị giác
    "stop", "trying", "look", "looking", "stare", "staring", "watch", "watching", "see", "seeing",
    "feel", "feeling", "think", "thinking", "know", "knowing", "say", "saying", "speak", "speaking",
    "tell", "telling", "talk", "talking", "walk", "walking", "run", "running", "stand", "standing",
    "sit", "sitting", "write", "writing", "read", "reading", "listen", "listening", "smile", "smiling",
    "laugh", "laughing", "cry", "crying", "sweat", "sweating", "tremble", "trembling", "shake", "shaking",
    "hyperventilate", "hyperventilating", "check", "checking", "use", "using", "work", "working",
    # Danh từ cảnh & bộ phận cơ thể
    "hand", "hands", "face", "faces", "eye", "eyes", "head", "body", "finger", "fingers", "voice",
    "person", "people", "man", "men", "woman", "women", "human", "humans", "someone", "everyone", "nobody",
    "room", "door", "window", "wall", "mirror", "podium", "stage", "notebook", "pen", "pencil", "phone", "phones",
    "street", "road", "city", "hall", "car", "cars", "table", "chair", "light", "lights", "shadow", "shadows",
    "dark", "darkness", "neon", "crowd", "party", "meeting", "cafe", "coffee", "office", "desk",
    # Sci-Fi / Vũ trụ / Công nghệ
    "space", "ship", "spaceship", "star", "stars", "planet", "planets", "galaxy", "universe", "cosmos",
    "black", "hole", "wormhole", "robot", "robots", "android", "cyborg", "drone", "laser", "portal",
    "future", "futuristic", "cyberpunk", "hologram", "alien", "aliens", "technology", "quantum", "ai",
    # Tâm lý học & khái niệm trừu tượng
    "confidence", "confident", "anxiety", "anxious", "stress", "stressed", "stressful", "panic",
    "psychology", "psychological", "mind", "minds", "brain", "brains", "secret", "secrets", "habit", "habits",
    "rule", "rules", "law", "laws", "truth", "fact", "facts", "trick", "tricks", "hack", "hacks",
    "way", "ways", "thing", "things", "sign", "signs", "power", "effect", "effects", "spotlight",
    "subconscious", "unconscious", "mental", "emotion", "emotions", "social", "success", "failure",
    "morning", "routine", "day", "night", "time", "clock", "hour", "hours", "minute", "minutes",
    "life", "world", "every", "right", "now", "here", "there", "today", "tomorrow", "yesterday",
}

_HISTORICAL_ENTITIES_AND_CIVILIZATIONS = {
    # ── Ancient Civilizations & Empires ──
    "maya": ("Maya Civilization", "civilization"),
    "mayan": ("Maya Civilization", "civilization"),
    "người maya": ("Maya Civilization", "civilization"),
    "đế chế maya": ("Maya Civilization", "civilization"),
    "văn minh maya": ("Maya Civilization", "civilization"),
    "aztec": ("Aztec Civilization", "civilization"),
    "người aztec": ("Aztec Civilization", "civilization"),
    "inca": ("Inca Empire", "civilization"),
    "người inca": ("Inca Empire", "civilization"),
    "ai cập": ("Ancient Egypt", "civilization"),
    "ancient egypt": ("Ancient Egypt", "civilization"),
    "egyptian": ("Ancient Egypt", "civilization"),
    "pharaoh": ("Ancient Egyptian Pharaoh", "civilization"),
    "kim tự tháp": ("Giza Pyramids", "civilization"),
    "tutankhamun": ("Tutankhamun", "person"),
    "cleopatra": ("Cleopatra", "person"),
    "ramses": ("Ramesses II", "person"),
    "la mã": ("Ancient Rome", "civilization"),
    "đế chế la mã": ("Ancient Rome", "civilization"),
    "roman": ("Ancient Rome", "civilization"),
    "roman empire": ("Ancient Rome", "civilization"),
    "gladiator": ("Roman Gladiator", "civilization"),
    "đấu sĩ la mã": ("Roman Gladiator", "civilization"),
    "colosseum": ("Colosseum Rome", "place"),
    "sparta": ("Ancient Sparta", "civilization"),
    "spartan": ("Ancient Sparta", "civilization"),
    "chiến binh sparta": ("Ancient Sparta", "civilization"),
    "hy lạp": ("Ancient Greece", "civilization"),
    "hy lạp cổ đại": ("Ancient Greece", "civilization"),
    "ancient greece": ("Ancient Greece", "civilization"),
    "athens": ("Ancient Athens", "civilization"),
    "viking": ("Vikings", "civilization"),
    "vikings": ("Vikings", "civilization"),
    "người viking": ("Vikings", "civilization"),
    "samurai": ("Samurai Feudal Japan", "civilization"),
    "shogun": ("Shogun Japan", "civilization"),
    "ninja": ("Ninja Feudal Japan", "civilization"),
    "nhật bản phong kiến": ("Feudal Japan", "civilization"),
    "trung cổ": ("Medieval Europe", "civilization"),
    "thời trung cổ": ("Medieval Europe", "civilization"),
    "medieval": ("Medieval Europe", "civilization"),
    "middle ages": ("Medieval Europe", "civilization"),
    "hiệp sĩ": ("Medieval Knights", "civilization"),
    "knights": ("Medieval Knights", "civilization"),
    "thập tự chinh": ("The Crusades", "event"),
    "crusades": ("The Crusades", "event"),
    "mông cổ": ("Mongol Empire", "civilization"),
    "đế chế mông cổ": ("Mongol Empire", "civilization"),
    "mongol": ("Mongol Empire", "civilization"),
    "genghis khan": ("Genghis Khan", "person"),
    "thành cát tư hãn": ("Genghis Khan", "person"),
    "lưỡng hà": ("Ancient Mesopotamia", "civilization"),
    "mesopotamia": ("Ancient Mesopotamia", "civilization"),
    "babylon": ("Ancient Babylon", "civilization"),
    "ba tư": ("Persian Empire", "civilization"),
    "persian empire": ("Persian Empire", "civilization"),
    "ottoman": ("Ottoman Empire", "civilization"),
    "thổ nhĩ kỳ ottoman": ("Ottoman Empire", "civilization"),
    "phục hưng": ("Italian Renaissance", "event"),
    "renaissance": ("Italian Renaissance", "event"),

    # ── Philosophers & Thinkers ──
    "khắc kỷ": ("Stoicism Philosophy", "concept"),
    "stoic": ("Stoicism Philosophy", "concept"),
    "stoicism": ("Stoicism Philosophy", "concept"),
    "marcus aurelius": ("Marcus Aurelius", "person"),
    "seneca": ("Seneca the Younger", "person"),
    "epictetus": ("Epictetus", "person"),
    "socrates": ("Socrates", "person"),
    "plato": ("Plato", "person"),
    "aristotle": ("Aristotle", "person"),
    "nietzsche": ("Friedrich Nietzsche", "person"),
    "friedrich nietzsche": ("Friedrich Nietzsche", "person"),
    "carl jung": ("Carl Jung", "person"),
    "jung": ("Carl Jung", "person"),
    "sigmund freud": ("Sigmund Freud", "person"),
    "freud": ("Sigmund Freud", "person"),
    "machiavelli": ("Niccolo Machiavelli", "person"),
    "sun tzu": ("Sun Tzu", "person"),
    "tôn tử": ("Sun Tzu", "person"),
    "khổng tử": ("Confucius", "person"),
    "confucius": ("Confucius", "person"),
    "lão tử": ("Laozi", "person"),

    # ── Scientists & Leaders ──
    "oppenheimer": ("J. Robert Oppenheimer", "person"),
    "j. robert oppenheimer": ("J. Robert Oppenheimer", "person"),
    "alexander the great": ("Alexander the Great", "person"),
    "alexander đại đế": ("Alexander the Great", "person"),
    "julius caesar": ("Julius Caesar", "person"),
    "caesar": ("Julius Caesar", "person"),
    "napoleon": ("Napoleon Bonaparte", "person"),
    "napoleon bonaparte": ("Napoleon Bonaparte", "person"),
    "einstein": ("Albert Einstein", "person"),
    "albert einstein": ("Albert Einstein", "person"),
    "nikola tesla": ("Nikola Tesla", "person"),
    "tesla": ("Nikola Tesla", "person"),
    "edison": ("Thomas Edison", "person"),
    "thomas edison": ("Thomas Edison", "person"),
    "rockefeller": ("John D. Rockefeller", "person"),
    "john d. rockefeller": ("John D. Rockefeller", "person"),
    "sir isaac newton": ("Isaac Newton", "person"),
    "isaac newton": ("Isaac Newton", "person"),
    "darwin": ("Charles Darwin", "person"),
    "charles darwin": ("Charles Darwin", "person"),
    "da vinci": ("Leonardo da Vinci", "person"),
    "leonardo da vinci": ("Leonardo da Vinci", "person"),

    # ── Vietnamese Historical Leaders ──
    "hồ chí minh": ("Ho Chi Minh", "person"),
    "ho chi minh": ("Ho Chi Minh", "person"),
    "bác hồ": ("Ho Chi Minh", "person"),
    "nguyễn ái quốc": ("Ho Chi Minh", "person"),
    "nguyen ai quoc": ("Ho Chi Minh", "person"),
    "võ nguyên giáp": ("Vo Nguyen Giap", "person"),
    "vo nguyen giap": ("Vo Nguyen Giap", "person"),
    "đại tướng võ nguyên giáp": ("Vo Nguyen Giap", "person"),
    "trần hưng đạo": ("Tran Hung Dao", "person"),
    "quang trung": ("Quang Trung Emperor", "person"),
    "nguyễn huệ": ("Quang Trung Emperor", "person"),
    "hai bà trưng": ("Hai Ba Trung Sisters", "person"),
    "lý thường kiệt": ("Ly Thuong Kiet", "person"),
    "đinh bộ lĩnh": ("Dinh Bo Linh Emperor", "person"),
    "ngô quyền": ("Ngo Quyen King", "person"),
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

    "steve jobs": "Steve Jobs",

    "bill gates": "Bill Gates",
    "sir isaac newton": "Isaac Newton",
    "isaac newton": "Isaac Newton",
    "darwin": "Charles Darwin",
    "charles darwin": "Charles Darwin",
    "da vinci": "Leonardo da Vinci",
    "leonardo da vinci": "Leonardo da Vinci",
    "marcus aurelius": "Marcus Aurelius",
    "seneca": "Seneca the Younger",
    "epictetus": "Epictetus",
    "socrates": "Socrates",
    "plato": "Plato",
    "aristotle": "Aristotle",
    "machiavelli": "Niccolo Machiavelli",
    "sun tzu": "Sun Tzu",
    "confucius": "Confucius",
    "laozi": "Laozi",
    "oppenheimer": "J. Robert Oppenheimer",
    "galileo": "Galileo Galilei",
    "genghis khan": "Genghis Khan",
    "cleopatra": "Cleopatra",
    "tutankhamun": "Tutankhamun",
    "gandhi": "Mahatma Gandhi",
    "mandela": "Nelson Mandela",
}

FORBIDDEN_ENTITY_NOISE = {
    'city', 'tower', 'hotel', 'street', 'hawker', 'delivery', 'mailbox', 'road',
    'district', 'statue', 'scooter', 'motorcycle', 'traffic', 'building', 'bridge',
    'stadium', 'airport', 'hospital', 'university', 'restaurant', 'cafe', 'store',
    'shop', 'skyline', 'apartment', 'plaza', 'mall', 'highway', 'park', 'avenue',
    'billboard', 'sidewalk', 'taxi', 'pedestrian', 'alley', 'intersection'
}

CAMERA_AND_STYLE_FLUFF = {
    "cinematic", "cinematography", "breathtaking", "lighting", "dramatic", "hyperrealistic", "photorealistic", "8k", "4k",
    "ultra-sharp", "ultra", "sharp", "shot", "shots", "on", "35mm", "film", "imax", "anamorphic",
    "lens", "macro", "extreme", "close-up", "closeup", "close", "up", "wide-angle", "wide", "angle",
    "low-angle", "high-angle", "low", "high", "view", "perspective", "shallow",
    "volumetric", "fog", "atmospheric", "masterpiece", "award-winning", "award", "winning",
    "portrait", "still", "render", "unreal", "engine", "octane", "vray", "style", "depth",
    "of", "field", "bokeh", "studio", "photography", "photo", "image", "picture",
    "vintage", "dark", "moody", "high-end", "prestigious", "editorial", "forbes", "aesthetic",
    "detailed", "hyper-detailed", "unrealengine", "octanerender", "tracking", "panning", "establishing",
    # Từ ngữ trừu tượng/tâm lý gây nhiễu search engine hình ảnh thực
    "anxiety", "confident", "confidence", "psychology", "hacks", "secrets", "mindset",
    "subconscious", "spotlight", "effect", "theory", "concept", "tricks", "habits"
}


def is_valid_entity_name(name: str) -> bool:
    """Kiểm tra chuỗi có phải tên nhân vật/thực thể hợp lệ không (loại trừ từ vựng chung, đại từ, hành động)."""
    if not name or len(name.strip()) < 3:
        return False
    words = [re.sub(r'[^\w]', '', w).lower() for w in name.split()]
    words = [w for w in words if w]
    if len(words) < 2:
        return False
    if any(w in _NON_ENTITY_WORDS for w in words):
        return False
    if any(w in _SENTENCE_STARTERS for w in words):
        return False
    if name.lower() in _COMMON_GEOPOLITICAL:
        return False
    return True


def _extract_proper_nouns(text: str, is_narration: bool = False) -> List[str]:
    """Trích xuất chuỗi tên riêng nhiều từ (chỉ giữ lại chuỗi thỏa mãn quy chuẩn tên thực thể)."""
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

        if clean.lower() in _NON_ENTITY_WORDS:
            if len(curr) >= 2:
                cand = " ".join(curr)
                if is_valid_entity_name(cand):
                    sequences.append(cand)
            curr = []
            continue

        if is_normal_cap or is_middle_initial or is_acronym:
            if is_narration and i == 0 and clean.lower() in _SENTENCE_STARTERS:
                continue
            curr.append(clean)
        else:
            if len(curr) >= 2:
                cand = " ".join(curr)
                if is_valid_entity_name(cand):
                    sequences.append(cand)
            curr = []
    if len(curr) >= 2:
        cand = " ".join(curr)
        if is_valid_entity_name(cand):
            sequences.append(cand)

    return sequences


def detect_real_entity(visual_keyword: str, narration: str = "") -> Tuple[bool, Optional[str], str]:
    """
    Phát hiện thông minh xem cảnh có liên quan đến nhân vật/nền văn minh/sự kiện thực hay không.
    Returns: (is_entity, entity_name, entity_type)
    """
    vk = visual_keyword.strip()
    vk_lower = vk.lower()

    for key, (full_name, ent_type) in sorted(_HISTORICAL_ENTITIES_AND_CIVILIZATIONS.items(), key=lambda x: len(x[0]), reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", vk_lower):
            return True, full_name, ent_type

    for fig_key, fig_full in sorted(_KNOWN_FIGURES.items(), key=lambda x: len(x[0]), reverse=True):
        if re.search(rf"\b{re.escape(fig_key)}\b", vk_lower):
            return True, fig_full, "person"

    for indicator in _ENTITY_INDICATORS:
        m = re.search(rf"\b{re.escape(indicator)}\s+([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*)", vk, re.IGNORECASE)
        if m:
            cand = m.group(1)
            if is_valid_entity_name(cand):
                return True, f"{indicator.title()} {cand}", "person"

    for regex, title in _EVENT_REGEXES:
        if regex.search(vk_lower):
            return True, title, "event"

    if narration:
        narr_lower = narration.strip().lower()
        for key, (full_name, ent_type) in sorted(_HISTORICAL_ENTITIES_AND_CIVILIZATIONS.items(), key=lambda x: len(x[0]), reverse=True):
            if re.search(rf"\b{re.escape(key)}\b", narr_lower):
                return True, full_name, ent_type

        for fig_key, fig_full in sorted(_KNOWN_FIGURES.items(), key=lambda x: len(x[0]), reverse=True):
            if re.search(rf"\b{re.escape(fig_key)}\b", narr_lower):
                return True, fig_full, "person"

        for indicator in _ENTITY_INDICATORS:
            m = re.search(rf"\b{re.escape(indicator)}\s+([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*)", narration, re.IGNORECASE)
            if m:
                cand = m.group(1)
                if is_valid_entity_name(cand):
                    return True, f"{indicator.title()} {cand}", "person"

        for regex, title in _EVENT_REGEXES:
            if regex.search(narr_lower):
                return True, title, "event"

    return False, None, ""


def detect_video_primary_entity(
    topic: str = "",
    title: str = "",
    narration: str = "",
    tags: List[str] = None
) -> Tuple[Optional[str], str]:
    """
    Xác định thực thể/nền văn minh/nhân vật chính xuyên suốt toàn bộ video.
    Chỉ kích hoạt Documentary Mode khi THỰC SỰ là video tài liệu về nhân vật/di tích/sự kiện lịch sử.
    """
    tags = tags or []
    full_text = f"{topic} {title}".strip()
    full_text_lower = full_text.lower()

    for key, (full_name, ent_type) in sorted(_HISTORICAL_ENTITIES_AND_CIVILIZATIONS.items(), key=lambda x: len(x[0]), reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", full_text_lower):
            return full_name, ent_type

    for fig_key, fig_full in sorted(_KNOWN_FIGURES.items(), key=lambda x: len(x[0]), reverse=True):
        if re.search(rf"\b{re.escape(fig_key)}\b", full_text_lower):
            return fig_full, "person"

    for tag in tags:
        t_clean = re.sub(r'[^\w\s]', '', tag).strip().lower()
        if t_clean in _HISTORICAL_ENTITIES_AND_CIVILIZATIONS:
            return _HISTORICAL_ENTITIES_AND_CIVILIZATIONS[t_clean]
        if t_clean in _KNOWN_FIGURES:
            return _KNOWN_FIGURES[t_clean], "person"

    for regex, ev_title in _EVENT_REGEXES:
        if regex.search(full_text_lower):
            return ev_title, "event"

    for indicator in _ENTITY_INDICATORS:
        m = re.search(rf"\b{re.escape(indicator)}\s+([A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*)", full_text, re.IGNORECASE)
        if m:
            cand = m.group(1)
            if is_valid_entity_name(cand):
                return f"{indicator.title()} {cand}", "person"

    if narration:
        narr_lower = narration.lower()
        for key, (full_name, ent_type) in sorted(_HISTORICAL_ENTITIES_AND_CIVILIZATIONS.items(), key=lambda x: len(x[0]), reverse=True):
            if len(re.findall(rf"\b{re.escape(key)}\b", narr_lower)) >= 2:
                return full_name, ent_type

        for fig_key, fig_full in sorted(_KNOWN_FIGURES.items(), key=lambda x: len(x[0]), reverse=True):
            if len(re.findall(rf"\b{re.escape(fig_key)}\b", narr_lower)) >= 2:
                return fig_full, "person"

    return None, ""


_DANGLING_QUERY_CONNECTORS = {
    "vs", "versus", "and", "or", "with", "from", "into", "of", "in", "on",
    "at", "to", "for", "by", "the", "a", "an", "between", "against",
    "inside", "where", "when", "while", "than",
}

_QUERY_QUESTION_AND_NON_EN_STOPWORDS = {
    "why", "how", "what", "when", "where", "who", "whom", "whose", "which",
    "does", "do", "did", "can", "could", "would", "should", "is", "are", "was", "were", "will",
    "por", "que", "quê", "porque", "porquê", "como", "quando", "onde", "quem", "qual", "quais",
    "uma", "um", "uns", "umas", "dos", "das", "nos", "nas", "num", "numa", "pelo", "pela", "pelos", "pelas",
    "esse", "essa", "esses", "essas", "este", "esta", "estes", "estas", "isso", "isto", "aquilo",
    "nesse", "nessa", "desse", "dessa", "neste", "nesta", "deste", "desta", "nao", "não", "mais", "muito",
    "sobre", "para", "pra", "pro", "com", "sem", "sob", "entre", "contra", "dentro", "fora",
    "fica", "ficam", "foge", "fogem", "ataca", "atacam", "nunca", "sempre",
    "tai", "sao", "vi", "lam", "the", "nao", "nhung", "cac", "cua", "trong", "khong",
}


def clean_scene_search_query(raw_query: str, max_words: int = 3) -> str:
    """Làm sạch query tìm kiếm ảnh/video: loại bỏ từ để hỏi ('Why', 'Por Que'), từ phi tiếng Anh có dấu, từ rác kỹ thuật và từ nối cụt đuôi."""
    if not raw_query:
        return ""
    # Nếu là tiêu đề câu hỏi tiếng Bồ Đào Nha / Việt chứa cụm 'Por Que' hoặc ký tự có dấu phi tiếng Anh
    if re.search(
        r"\b(?:por\s+que|por\s+quê|porque|como\s+(?:o|a|um|uma)|alcateia\s+de|fundo\s+do\s+mar|tại\s+sao|vì\s+sao)\b",
        raw_query,
        flags=re.IGNORECASE | re.UNICODE,
    ):
        raw_query = re.sub(
            r"\b(?:por\s+que|por\s+quê|porque|como\s+(?:o|a|um|uma)|alcateia\s+de|fundo\s+do\s+mar|tại\s+sao|vì\s+sao)\b.*",
            " ",
            raw_query,
            flags=re.IGNORECASE | re.UNICODE,
        ).strip()
    # Loại bỏ nguyên vẹn từ có dấu phi tiếng Anh (tránh cắt "Tubarões" thành "Tubar" + "es")
    text = re.sub(r"\b\w*[^\x00-\x7F]+\w*\b", " ", raw_query, flags=re.UNICODE)
    # Loại bỏ tỉ lệ khung hình / độ phân giải trước (vd: "9:16", "16x9", "1080p", "60fps")
    text = re.sub(r'\b\d+\s*[:x×/]\s*\d+\b', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'\b\d+\s*(?:p|k|fps|mm)\b', ' ', text, flags=re.IGNORECASE)
    words = re.findall(r"[a-zA-Z0-9-]+", text)

    def _is_junk(w: str) -> bool:
        lw = w.lower().strip("-")
        return (
            lw in CAMERA_AND_STYLE_FLUFF
            or lw in _FORMAT_FLUFF
            or lw in _QUERY_QUESTION_AND_NON_EN_STOPWORDS
            or lw in _DANGLING_QUERY_CONNECTORS
            or len(lw) <= 1
            or lw.isdigit()
        )

    clean = [w for w in words if not _is_junk(w)]
    if not clean:
        clean = [
            w for w in words
            if len(w) > 1
            and not w.isdigit()
            and w.lower() not in _QUERY_QUESTION_AND_NON_EN_STOPWORDS
        ]

    # Nếu chuỗi có cấu trúc "Entity A vs Entity B" và max_words nhỏ (<= 3), lấy trọn vẹn thực thể A phía trước "vs"
    # để không bao giờ cắt cụt thành "Orca vs" hoặc "Orca vs Great" gây hỏng truy vấn Stock/Web!
    if max_words <= 3 and clean:
        lower_clean = [w.lower() for w in clean]
        for vs_tok in ("vs", "versus"):
            if vs_tok in lower_clean:
                vs_idx = lower_clean.index(vs_tok)
                if 1 <= vs_idx <= max_words:
                    clean = clean[:vs_idx]
                break

    sliced = list(clean[:max_words])
    while len(sliced) > 1 and sliced[-1].lower() in _DANGLING_QUERY_CONNECTORS:
        sliced.pop()
    while len(sliced) > 1 and sliced[0].lower() in _DANGLING_QUERY_CONNECTORS:
        sliced.pop(0)
    return " ".join(sliced).strip()


# Từ mô tả định dạng/khung hình — vô nghĩa với search engine stock (gây ra query rác kiểu "16 vertical")
_FORMAT_FLUFF = {
    "vertical", "horizontal", "landscape", "orientation", "aspect", "ratio", "format",
    "fps", "hd", "uhd", "fullhd", "resolution", "high", "quality", "footage", "video",
    "clip", "b-roll", "broll", "stock", "scene", "shorts", "short", "tiktok", "reels",
    "frame", "framing", "composition", "slow", "motion", "slow-motion", "timelapse",
    "camera", "drone", "pan", "zoom", "dolly", "tracking", "realistic", "hyper",
}


_INCONGRUENT_TAG_GROUPS = {
    # Văn phòng / Công sở hiện đại
    "office_corporate": [
        "office", "corporate", "coworking", "meeting room", "boardroom", "cubicle",
        "laptop", "typing on keyboard", "handshake", "colleagues", "spreadsheets",
        "whiteboard", "presentation screen", "businesswoman", "businessman", "office desk",
        "office workers",
    ],
    # Cafe / Đồ uống / Đồ ăn nhanh
    "cafe_fastfood": [
        "coffee cup", "coffee shop", "cafe", "café", "latte", "espresso", "cappuccino",
        "barista", "burger", "fast food", "restaurant table", "starbucks",
    ],
    # Tiệc tùng / Bar / Hộp đêm
    "nightlife": [
        "nightclub", "party dj", "disco", "clubbing", "beer toast", "beer party",
        "drinking beer", "cheering concert", "confetti party", "cocktail bar", "pub party",
        "rave party", "club party", "party celebration",
    ],
    # Làm đẹp / Thời trang / Selfie / Đồ lót / Ảnh kỳ quặc (NSFW/Bizarre stock)
    "glamour_selfie": [
        "fashion model", "runway", "makeup tutorial", "selfie", "posing for camera",
        "influencer posing", "lingerie", "bikini", "underwear", "bra", "panties",
        "swimsuit", "shopping cart", "boudoir",
    ],
    # Hóa trang / Mặt nạ quái vật giả tạo (tránh lọt vào video tâm lý học / đời sống)
    "costume_monster": [
        "eerie creature", "mask and claws", "halloween mask", "horror mask", "cosplay",
        "clown mask", "zombie makeup", "monster costume", "alien", "extraterrestrial",
        "martian", "ufo", "zombie", "vampire", "werewolf", "goblin",
    ],
    "cartoon_kids_martial_arts": [
        "cartoon", "anime", "comic", "manga", "2d animation", "cute heart",
        "valentine heart", "emoji", "emoticon", "mascot", "chibi", "playdough",
        "claymation", "karate", "taekwondo", "kung fu", "judo", "kickboxing",
        "playground", "trampoline", "toddler", "kids playing",
    ],
    "domestic_pets_reptiles": [
        "snake", "python", "cobra", "viper", "boa", "lizard", "iguana", "gecko",
        "chameleon", "turtle", "tortoise", "frog", "toad", "puppy", "kitten",
        "pet dog", "house cat", "pet cat", "hamster", "guinea pig", "goldfish", "parrot",
    ],
    # Đồ họa / Sơ đồ / Bản đồ / Tiền tệ (Wikimedia)
    "wikimedia_diagrams": [
        "diagram", "infographic", "flowchart", "pie chart", "bar graph", "schematic",
        "blueprint", "coat of arms", "postage stamp", "commemorative coin", "seal of",
        "flag of", "emblem of", "crest of", "location map", "locator map",
    ],
}



def _is_incongruent_stock_heuristic(title_or_alt: str, topic: str = "", narration: str = "") -> bool:
    """Bộ lọc heuristic từ khóa 0ms để phát hiện các nhóm thể loại lạc quẻ phổ biến (khớp nguyên từ \b)."""
    if not title_or_alt:
        return False

    t_lower = title_or_alt.lower()
    context_lower = f"{topic} {narration}".lower()

    is_modern_human_topic = any(
        w in context_lower
        for w in ("manipulat", "psycholog", "narcissist", "silence", "finance", "money", "career", "relationship", "negotiation", "corporate", "empathy")
    )
    for group_name, bad_words in _INCONGRUENT_TAG_GROUPS.items():
        if group_name == "office_corporate" and is_modern_human_topic:
            continue
        matched_bad = [
            bw for bw in bad_words
            if re.search(rf"\b{re.escape(bw)}\b", t_lower)
        ]
        if matched_bad:
            is_legit = any(re.search(rf"\b{re.escape(bw)}\b", context_lower) for bw in matched_bad)
            if not is_legit:
                logger.info(f"🚫 [EntityDetector:Heuristic] Từ chối ảnh stock lạc đề ('{title_or_alt[:45]}...') do chứa từ khóa: {matched_bad}")
                return True

    return False


def is_incongruent_stock_image(
    title_or_alt: str,
    topic: str = "",
    narration: str = "",
    use_llm: bool = False,
) -> bool:
    """
    Kiểm tra xem mô tả/alt text/tiêu đề của ảnh/video stock có thuộc nhóm thể loại lạc đề,
    lạc lõng hoàn toàn so với chủ đề video hay không (ví dụ: ảnh văn phòng, cafe, tiệc tùng
    lọt vào video về vũ trụ, đáy biển sâu, tâm lý học, lịch sử).

    Hỗ trợ 2 chế độ:
    - use_llm=True: Ủy quyền cho VisualCurationAgent (Gemini LLM) phân tích ngữ nghĩa sâu sắc.
    - use_llm=False: Dùng bộ lọc heuristic 0ms qua danh mục từ khóa phủ định.
    """
    if not title_or_alt:
        return False

    if use_llm:
        try:
            from app.agents.visual_curation_agent import VisualCurationAgent
            curator = VisualCurationAgent()
            is_incong, score, rationale = curator.check_media_congruence(
                media_desc=title_or_alt,
                topic=topic,
                narration=narration,
            )
            if is_incong:
                logger.info(f"🚫 [EntityDetector:LLM] VisualCurationAgent từ chối ('{title_or_alt[:45]}...') | Lý do: {rationale}")
            return is_incong
        except Exception as e:
            logger.debug(f"[EntityDetector] LLM incongruence check error ({e}), fallback sang heuristic")

    return _is_incongruent_stock_heuristic(title_or_alt, topic=topic, narration=narration)


async def is_incongruent_stock_image_async(
    title_or_alt: str,
    topic: str = "",
    narration: str = "",
) -> bool:
    """Phiên bản bất đồng bộ gọi LLM Agent thẩm định độ lạc đề của media."""
    import asyncio
    return await asyncio.to_thread(
        is_incongruent_stock_image,
        title_or_alt,
        topic=topic,
        narration=narration,
        use_llm=True,
    )


def synthesize_scene_query(
    narration: str = "",
    visual_keyword: str = "",
    stock_query: Optional[str] = None,
    visual_prompt: Optional[str] = None,
    primary_entity: Optional[str] = None,
    topic: str = "",
    channel_niche: str = "",
) -> Tuple[str, str, str]:
    """
    Tổng hợp query tìm kiếm chính xác cho từng công cụ tìm ảnh.
    Tự động gắn neo ngữ cảnh chủ đề (Topic Anchoring) để chống lạc quẻ stock.
    """
    cleaned_stock = clean_scene_search_query(stock_query or "", max_words=3)
    cleaned_vk = clean_scene_search_query(visual_keyword or "", max_words=4)
    cleaned_topic = clean_scene_search_query(topic or "", max_words=3)

    is_ent, ent_name, ent_type = detect_real_entity(visual_keyword, narration)
    target_ent = ent_name or (
        primary_entity
        if primary_entity and any(w.lower() in narration.lower() for w in primary_entity.lower().split())
        else None
    )

    years = re.findall(r'\b(18\d\d|19\d\d|20\d\d)\b', f"{narration} {visual_keyword}")
    year_str = years[0] if years else ""

    if target_ent:
        primary_query = f"{target_ent} {year_str}".strip() if year_str else target_ent
        fallback_query = cleaned_stock or cleaned_vk or target_ent
        return primary_query, fallback_query, "historical_entity"

    # Danh sách các từ khóa trừu tượng / chung chung dễ gây hiểu lầm trên Stock Search
    _VAGUE_SINGLE_WORDS = {
        "trap", "mind", "power", "money", "secret", "dark", "light", "focus", "pressure",
        "fall", "rise", "time", "clock", "hands", "hand", "man", "woman", "person", "room",
        "eye", "eyes", "face", "door", "alone", "deep", "space", "future", "fear", "brain",
        "primal", "threat", "evolution", "panic", "reflex", "alarm", "instinct", "hunting", "prey", "predator", "survival",
        "bẫy", "tâm trí", "sức mạnh", "tiền", "bí mật", "tối", "sáng", "thời gian", "đồng hồ"
    }

    if cleaned_stock and len(cleaned_stock.split()) >= 1:
        # Nếu từ khóa quá chung chung hoặc là 1 từ đơn, bắt buộc neo vào topic
        is_vague = (len(cleaned_stock.split()) == 1 and cleaned_stock.lower() in _VAGUE_SINGLE_WORDS)
        if is_vague and cleaned_topic and not any(w in cleaned_stock.lower() for w in cleaned_topic.lower().split()):
            primary_query = f"{cleaned_topic} {cleaned_stock}"
            fallback_query = cleaned_stock
        else:
            primary_query = cleaned_stock
            fallback_query = f"{cleaned_topic} {cleaned_stock}" if (cleaned_topic and cleaned_topic not in cleaned_stock) else (cleaned_vk or cleaned_stock)
        return primary_query, fallback_query, "specific_object"

    if cleaned_vk:
        is_vague = (len(cleaned_vk.split()) == 1 and cleaned_vk.lower() in _VAGUE_SINGLE_WORDS)
        if is_vague and cleaned_topic and not any(w in cleaned_vk.lower() for w in cleaned_topic.lower().split()):
            primary_query = f"{cleaned_topic} {cleaned_vk}"
            fallback_query = cleaned_vk
        else:
            primary_query = " ".join(cleaned_vk.split()[:3])
            fallback_query = " ".join(cleaned_vk.split()[:2])
        return primary_query, fallback_query, "concept_photo"

    # Fallback khi cả stock_query và visual_keyword đều trống:
    # Tuyệt đối không dùng "cinematic atmosphere" vì sẽ trả về người uống cà phê / công sở!
    if cleaned_topic:
        primary_query = cleaned_topic
        fallback_query = f"{cleaned_topic} landscape"
        return primary_query, fallback_query, "topic_anchor"

    narr_clean = clean_scene_search_query(narration or "", max_words=3)
    if narr_clean:
        return narr_clean, f"{narr_clean} background", "narration_anchor"

    return "dramatic landscape", "epic mysterious nature", "concept_photo"


def match_best_pool_photo(
    scene_narration: str,
    scene_kw: str,
    pool: List[Dict],
    used_urls: set,
    threshold: int = 25
) -> Optional[Dict]:
    """Tìm ảnh trong pool có độ khớp ngữ nghĩa cao nhất với cảnh hiện tại."""
    if not pool:
        return None

    full_scene_text = f"{scene_narration} {scene_kw}".lower()
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

        cand_years = set(re.findall(r'\b(18\d\d|19\d\d|20\d\d)\b', p_title))
        if scene_years and cand_years:
            if scene_years.intersection(cand_years):
                score += 65
            else:
                score -= 20

        for triggers, pts in concept_mappings.items():
            if any(tr in full_scene_text for tr in triggers) and any(tr in p_title for tr in triggers):
                score += pts

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


def build_character_media_pool(character_name: str, max_images: int = 15) -> List[Dict]:
    """Wrapper hỗ trợ tạo media pool cho nhân vật/thực thể thông qua MediaAgent."""
    from app.agents.media_agent import MediaAgent
    agent = MediaAgent()
    return agent.fetch_entity_media_pool(character_name, entity_type="person", max_images=max_images)
