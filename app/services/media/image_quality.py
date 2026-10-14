"""
app.services.media.image_quality — Bộ lọc & xếp hạng ảnh web TRƯỚC khi đưa cho AI Vision,
và khung hình dọc 9:16 thông minh (không kéo giãn ảnh).

Mục tiêu:
1. Giữ đúng CHỦ THỂ của lời thoại (vd: "wedge-tailed eagle" chứ không phải "eagle" chung chung).
2. Loại ảnh có nguy cơ watermark / stock trả phí / clipart / sản phẩm in ấn.
3. Ưu tiên ảnh dọc, độ phân giải cao, tiêu đề khớp chủ thể.
4. Khi AI Vision không khả dụng (hết quota) vẫn chọn được ảnh tốt nhờ điểm heuristic.
5. Ảnh ngang → crop 9:16 bám theo vùng có nhiều chi tiết (saliency) hoặc nền mờ nếu ảnh quá nhỏ.
"""
from __future__ import annotations

import os
import re
from typing import Dict, Iterable, List, Optional

# ─────────────────────────────── Watermark / stock risk ───────────────────────────────
# Domain ảnh stock trả phí (ảnh preview luôn có watermark) hoặc trang bán tranh in
WATERMARK_DOMAINS = (
    "shutterstock", "alamy", "istockphoto", "gettyimages", "dreamstime", "depositphotos",
    "123rf", "stock.adobe", "adobestock", "fineartamerica", "pixels.com", "vectorstock",
    "bigstockphoto", "agefotostock", "canstockphoto", "pond5", "storyblocks", "vecteezy",
    "featurepics", "colourbox", "superstock", "masterfile", "photocase", "eyeem",
    "redbubble", "teepublic", "zazzle", "etsy", "society6", "cafepress", "displate",
    "img.freepik.com/premium-", "freepik.com/premium-", "craiyon.com", "media.craiyon.com",
    "nightcafe.studio", "images.nightcafe.studio", "deepdreamgenerator", "artbreeder.com",
)

# Tiêu đề chứa các cụm này gần như chắc chắn là ảnh stock có watermark / clipart / sản phẩm
_HARD_TITLE_CUES = (
    "stock photo", "stock image", "stock picture", "premium photo", "premium vector",
    "royalty free", "royalty-free", "vector", "clipart", "clip art", "coloring page",
    "colouring", "t-shirt", "tshirt", "sticker", "mug ", "poster print", "canvas print",
    "framed print", "art print", "for sale", "buy now", "watermarked",
)

# Tiêu đề chứa các cụm này → trừ điểm (thường không phải ảnh điện ảnh / thực tế đẹp)
_SOFT_TITLE_CUES = (
    "cartoon", "drawing", "sketch", "download", "logo", "icon",
    "infographic", "diagram", "chart", "map of", "meme", "quote", "text",
    "toy", "figurine", "lego", "plush", "costume", "tattoo",
)

# Nguồn & từ khóa Hot-Trend / Điện ảnh / Viral giúp hình ảnh bắt mắt hơn trên Shorts
_HOT_TREND_DOMAINS = (
    "artstation.com", "artstation", "deviantart.com", "deviantart", "pinterest", "pinimg.com",
    "redd.it", "reddit.com", "wallhaven", "uhdpaper", "hdqwalls", "behance.net", "imdb.com",
    "tmdb.org", "ytimg.com", "cgsociety", "cara.app", "alphacoders", "peakpx", "wallpaperflare",
    "img.freepik.com/free-",
)

_HOT_TREND_CUES = (
    "cinematic", "movie scene", "film still", "artstation", "unreal engine", "octane render",
    "8k", "4k", "concept art", "dark fantasy", "thalassophobia", "megalophobia", "wallpaper",
    "masterpiece", "vfx", "cgi", "dramatic", "photorealistic", "hyperrealistic", "imax",
)

_STOPWORDS = {
    "a", "an", "the", "of", "and", "or", "to", "in", "on", "at", "for", "with", "from", "by",
    "its", "it", "his", "her", "their", "this", "that", "these", "those", "into", "onto",
    "is", "are", "was", "were", "be", "been", "can", "could", "will", "would", "just", "even",
    "so", "very", "than", "then", "there", "here", "what", "which", "who", "when", "where",
    "while", "as", "up", "down", "out", "over", "under", "about", "your", "you", "our", "we",
    "my", "me", "i", "they", "them", "he", "she", "has", "have", "had", "do", "does", "did",
    "not", "no", "yes", "but", "if", "because", "only", "every", "all", "some", "most",
    # Trạng từ vị trí/thời gian — không phải chủ thể (vd: cảnh bị cắt giữa câu "High above,")
    "above", "below", "beneath", "high", "deep", "far", "away", "inside", "outside", "behind",
    "beyond", "now", "today", "yet", "once", "ago", "soon", "later", "first", "last", "next",
    "suddenly", "finally", "instead", "imagine", "meet",
    # Thuật ngữ góc máy / nhiếp ảnh ở đầu AI prompt — tuyệt đối không được trích thành chủ thể ("Macro shot", "Wide-angle cinematic shot")
    "macro", "shot", "shots", "wide", "angle", "wide-angle", "low-angle", "high-angle", "low",
    "close", "close-up", "closeup", "extreme", "cinematic", "cinematography", "breathtaking",
    "dramatic", "photorealistic", "hyperrealistic", "hyper-realistic", "vertical", "portrait",
    "frame", "still", "view", "perspective", "lens", "lighting", "bokeh", "volumetric",
    "tracking", "panning", "aerial", "establishing", "moody", "atmospheric", "film", "scene",
}

_NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
    "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
    "twenty", "thirty", "forty", "fifty", "hundred", "hundreds", "thousand", "thousands",
    "million", "millions", "billion", "billions", "half", "dozen",
}

# Động từ phổ biến trong lời thoại Shorts — dùng để cắt cụm danh từ chủ thể
_COMMON_VERBS = {
    "sees", "see", "saw", "looks", "look", "locks", "lock", "can", "could", "has", "have", "had",
    "is", "are", "was", "were", "will", "would", "makes", "make", "made", "takes", "take", "took",
    "gets", "get", "got", "lives", "live", "lived", "eats", "eat", "ate", "hunts", "hunt", "hunted",
    "uses", "use", "used", "becomes", "become", "became", "finds", "find", "found", "keeps", "keep",
    "spends", "spend", "spent", "survives", "survive", "survived", "dies", "die", "died", "kills",
    "kill", "killed", "swims", "swim", "flies", "fly", "flew", "runs", "run", "ran", "walks", "walk",
    "sleeps", "sleep", "grows", "grow", "grew", "builds", "build", "built", "creates", "create",
    "turns", "turn", "changes", "change", "detects", "detect", "senses", "sense", "hears", "hear",
    "feels", "feel", "knows", "know", "thinks", "think", "believes", "believe", "remembers",
    "remember", "waits", "wait", "attacks", "attack", "strikes", "strike", "spots", "spot",
    "carries", "carry", "holds", "hold", "produces", "produce", "contains", "contain", "reaches",
    "reach", "travels", "travel", "glows", "glow", "shines", "shine", "hides", "hide", "appears",
    "appear", "disappears", "disappear", "rescued", "rescues", "rescue", "saved", "saves", "save",
    "was", "tried", "tries", "try", "started", "starts", "start", "began", "begins", "begin",
    "never", "always", "still", "actually", "literally",
}

_TRAILING_PREPS = {
    "onto", "into", "its", "the", "a", "an", "to", "up", "down", "from", "at", "through", "with",
    "over", "under", "across", "around", "for", "in", "on", "of", "more", "less", "than", "every",
}


def _norm_text(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower().replace("-", " ")).strip()


def subject_terms(subject: Optional[str]) -> List[str]:
    """Tách chủ thể thành các từ khóa có nghĩa (đã chuẩn hóa, bỏ stopword)."""
    if not subject:
        return []
    return [w for w in _norm_text(subject).split() if len(w) > 2 and w not in _STOPWORDS]


def extract_subject(narration: str, primary_entity: Optional[str] = None, max_words: int = 4) -> str:
    """
    Trích cụm danh từ CHỦ THỂ từ lời thoại tiếng Anh (heuristic, không cần LLM).
    vd: "a mantis shrimp sees sixteen types of color receptors" → "mantis shrimp"
        "a wedge-tailed eagle locks onto its prey"              → "wedge-tailed eagle"
    """
    if primary_entity and len(primary_entity.strip()) > 2:
        return primary_entity.strip()
    if not narration:
        return ""
    tokens = re.findall(r"[A-Za-z][A-Za-z'\-]*", narration)
    phrase: List[str] = []
    for i, tok in enumerate(tokens):
        lw = tok.lower().strip("'")
        nxt = tokens[i + 1].lower() if i + 1 < len(tokens) else ""
        if not phrase and (lw in _STOPWORDS or lw in _NUMBER_WORDS or lw in _COMMON_VERBS):
            continue
        if phrase:
            if lw in _COMMON_VERBS or lw in _STOPWORDS:
                break
            # "...eagle locks onto" / "...shrimp sees sixteen" → từ kết thúc bằng s/ed theo sau bởi giới từ/số = động từ
            if (lw.endswith(("s", "ed")) and (nxt in _TRAILING_PREPS or nxt in _NUMBER_WORDS)
                    and len(phrase) >= 1 and lw not in ("mantis", "species", "octopus", "walrus")):
                break
        phrase.append(tok)
        if len(phrase) >= max_words:
            break
    return " ".join(phrase).strip(" -")


def is_watermark_risk(item: Dict) -> bool:
    """True nếu ảnh gần như chắc chắn có watermark / là clipart / ảnh sản phẩm in ấn."""
    blob = " ".join(str(item.get(k) or "") for k in ("url", "page_url", "creator", "thumbnail")).lower()
    if any(d in blob for d in WATERMARK_DOMAINS):
        return True
    title = f" {(item.get('title') or '')} {(item.get('alt') or '')} ".lower()
    return any(c in title for c in _HARD_TITLE_CUES)


def _dims(item: Dict) -> tuple:
    try:
        return int(item.get("width") or 0), int(item.get("height") or 0)
    except Exception:
        return 0, 0


# ─────────────────────────────── Chế độ quái vật / sinh vật huyền thoại & kinh dị ───────────────────────────────
# Những chủ thể quái vật, thủy quái, sinh vật tiền sử hoặc thực thể kinh dị → ảnh rùng rợn, ám ảnh nhất là
# cảnh phim CGI (The Meg, Underwater, Pirates of the Caribbean), ArtStation dark horror, Thalassophobia/Megalophobia art.
_CREATURE_WORDS = (
    "kraken", "leviathan", "megalodon", "sea serpent", "sea monster", "cthulhu", "godzilla",
    "loch ness", "nessie", "bloop", "the bloop", "mothman", "wendigo", "chupacabra", "bigfoot", "sasquatch",
    "yeti", "skinwalker", "behemoth", "basilisk", "giant squid", "colossal squid",
    "magnapinna", "bigfin squid", "monster", "monsters", "cryptid", "demon", "lusca", "jormungandr",
    "charybdis", "scylla", "tiamat", "ningen", "kaiju", "werewolf", "vampire", "ghost ship",
    "abyssal creature", "deep sea creature", "deep sea monster", "eldritch", "mermaid", "siren",
    "thunderbird", "jersey devil", "dogman", "titanoboa", "mosasaurus", "liopleurodon",
    "dunkleosteus", "helicoprion", "livyatan", "sarcosuchus", "plesiosaur", "kronosaurus",
    "basilosaurus", "megalania", "smilodon", "terror bird", "gorgonopsid", "arthropleura",
    "scp", "siren head", "backrooms", "slenderman", "the rake", "crawler", "pale crawler",
    "shadow person", "doppelganger", "analog horror", "thalassophobia", "megalophobia",
    "anglerfish", "goblin shark", "frilled shark", "vampire squid", "giant isopod", "oarfish",
    "gulper eel", "viperfish", "black swallower", "tentacle", "tentacles",
)
# Tên có chữ "monster"/"dragon" nhưng là động vật THẬT → không bật chế độ quái vật
_REAL_ANIMAL_EXCEPTIONS = ("gila monster", "komodo dragon", "sea dragon", "dragonfish", "dragonfly")

# Cảnh phim & ArtStation horror cực kỳ rùng rợn cho từng quái vật (giữ ngắn gọn 5-7 từ để tối ưu công cụ tìm kiếm)
_CREATURE_FILM_REFS = {
    "kraken": "giant kraken attacking ship movie cgi",
    "megalodon": "giant megalodon shark jaws dark ocean",
    "godzilla": "Godzilla giant monster storm movie scene",
    "loch ness": "loch ness monster dark underwater thalassophobia",
    "nessie": "loch ness monster dark underwater thalassophobia",
    "cthulhu": "Cthulhu eldritch monster rising dark ocean",
    "leviathan": "colossal leviathan sea monster dark abyss",
    "sea serpent": "giant sea serpent monster dark ocean",
    "sea monster": "giant sea monster lurking beneath boat",
    "bloop": "the bloop colossal deep sea monster",
    "giant squid": "giant kraken squid tentacles attacking ship",
    "colossal squid": "colossal squid deep dark abyssal monster",
    "magnapinna": "bigfin magnapinna squid deep sea horror",
    "bigfin squid": "bigfin magnapinna squid deep sea horror",
    "titanoboa": "giant titanoboa snake monster jungle cgi",
    "mosasaurus": "giant mosasaurus underwater jaws movie scene",
    "dunkleosteus": "giant dunkleosteus armored predator dark ocean",
    "helicoprion": "helicoprion spiral jaw shark dark ocean",
    "livyatan": "livyatan giant whale monster dark ocean",
    "mothman": "mothman glowing red eyes dark horror",
    "wendigo": "wendigo antler skull monster dark forest",
    "skinwalker": "skinwalker creature night horror found footage",
    "siren head": "siren head giant monster dark fog",
    "scp": "scp monster containment breach dark horror",
    "backrooms": "backrooms entity found footage analog horror",
    "anglerfish": "deep sea anglerfish glowing lure abyss",
    "goblin shark": "deep sea goblin shark jaws abyss",
}

_CREATURE_BONUS_CUES = (
    "concept art", "art", "artstation", "digital art", "cgi", "movie", "film", "scene", "dark",
    "horror", "terrifying", "scary", "creepy", "nightmare", "thalassophobia", "megalophobia",
    "eldritch", "abyss", "abyssal", "lurking", "silhouette", "shadow", "glowing eyes",
    "attack", "attacking", "giant", "colossal", "massive", "wallpaper", "render", "unreal engine",
    "fantasy", "epic", "monster", "beast", "rising", "deep sea", "underwater",
)

_CREATURE_PENALTY_CUES = (
    "cartoon", "cute", "kawaii", "chibi", "toy", "plush", "lego", "figurine", "costume", "mask",
    "coloring", "logo", "icon", "meme", "chart", "diagram", "infographic", "sticker", "emoji",
    "clipart", "kids", "baby", "funny", "cosplay", "halloween decoration", "cake",
    "caught on camera", "spotted in real life", "top 10", "top 5", "reaction", "thumbnail",
    # Chặn ảnh hóa thạch khô khan, răng bảo tàng, xác chết dạt bờ hoặc bản khắc gỗ thế kỷ 18 kém rùng rợn
    "fossil", "tooth", "teeth", "jawbone", "skeleton", "bone", "bones", "skull", "museum",
    "exhibit", "specimen", "preserved", "jar", "engraving", "woodcut", "stamp", "coin",
    "statue", "sculpture", "map", "book cover", "novel", "washed ashore", "beached",
)

# Thứ tự ưu tiên nhận diện tên quái vật đặc trưng (ưu tiên tên riêng trước các từ chung như "tentacle", "monster")
_SPECIFIC_CREATURES = (
    "kraken", "leviathan", "megalodon", "cthulhu", "godzilla", "loch ness", "nessie",
    "bloop", "jormungandr", "charybdis", "scylla", "lusca", "tiamat", "ningen", "kaiju",
    "mothman", "wendigo", "chupacabra", "bigfoot", "sasquatch", "yeti", "skinwalker",
    "behemoth", "basilisk", "titanoboa", "mosasaurus", "liopleurodon", "dunkleosteus",
    "helicoprion", "livyatan", "sarcosuchus", "plesiosaur", "kronosaurus", "basilosaurus",
    "siren head", "slenderman", "backrooms", "scp", "magnapinna", "bigfin squid",
    "colossal squid", "giant squid", "anglerfish", "goblin shark", "frilled shark",
    "vampire squid", "oarfish", "gulper eel", "viperfish", "sea serpent", "sea monster",
)


def is_creature_subject(subject: Optional[str], narration: str = "", topic: str = "") -> bool:
    """True nếu chủ thể hoặc ngữ cảnh là quái vật / thủy quái / thực thể kinh dị (ưu tiên CGI / dark horror art)."""
    import os
    if os.getenv("CREATURE_ART_MODE", "true").strip().lower() in ("0", "false", "no", "off"):
        return False
    subj = f" {_norm_text(subject or '')} "
    if any(f" {x} " in subj or x in subj for x in _REAL_ANIMAL_EXCEPTIONS):
        return False
    if any(f" {w} " in subj for w in _CREATURE_WORDS):
        return True
    # Kiểm tra cả lời thoại và chủ đề: nếu nói về quái vật, thủy quái, vực thẳm kinh dị hoặc thực thể ám ảnh
    ctx = f" {_norm_text(narration)} {_norm_text(topic)} "
    if any(f" {w} " in ctx for w in _CREATURE_WORDS):
        return True
    raw_ctx = f"{subject or ''} {narration or ''} {topic or ''}".lower()
    vi_horror_cues = (
        "quái vật", "thủy quái", "quái thú", "sinh vật biển sâu", "mực khổng lồ",
        "rùng rợn", "kinh dị", "ác mộng", "vực thẳm", "huyền thoại biển",
        "người đi biển", "thần thoại",
    )
    return any(cue in raw_ctx for cue in vi_horror_cues)


def extract_core_creature(*texts: str) -> str:
    """Trích xuất tên quái vật/sinh vật cốt lõi từ subject, narration hoặc topic (ưu tiên tên riêng như kraken, leviathan)."""
    combined_norm = " " + " ".join(_norm_text(t) for t in texts if t) + " "
    if any(f" {x} " in combined_norm for x in _REAL_ANIMAL_EXCEPTIONS):
        return ""
    for c in _SPECIFIC_CREATURES:
        if f" {c} " in combined_norm:
            return c
    raw_low = " ".join((t or "").lower() for t in texts if t)
    if any(k in raw_low for k in ("thủy quái", "mực khổng lồ", "bạch tuộc khổng lồ", "quái vật biển", "người đi biển")):
        return "kraken"
    if any(k in combined_norm for k in (" tentacle ", " tentacles ", " suction ", " ship ", " sailor ", " whirlpool ")):
        if any(k in raw_low for k in ("quái vật", "thần thoại", "biển", "đại dương", "ocean", "sea", "abyss")):
            return "kraken"
    for w in _CREATURE_WORDS:
        if f" {w} " in combined_norm and w not in ("monster", "monsters", "tentacle", "tentacles"):
            return w
    return ""


def creature_queries(subject: str, topic: str = "", narration: str = "") -> List[str]:
    """
    Truy vấn tìm ảnh quái vật cực kỳ rùng rợn, ám ảnh (4-7 từ ngắn gọn để Bing/DDGS/ArtStation/Wallhaven trả kết quả chuẩn xác nhất).
    """
    import os
    subject = (subject or "").strip()
    core = extract_core_creature(subject, narration, topic)
    norm_all = f" {_norm_text(subject)} {_norm_text(narration)} {_norm_text(topic)} "

    # Trích 1-2 từ khóa bối cảnh/bộ phận đặc tả của phân cảnh (vd: tentacles, ship, eye, whirlpool, abyss)
    scene_focus_words = []
    focus_candidates = (
        "tentacles", "tentacle", "ship", "eye", "eyes", "jaws", "teeth", "whirlpool",
        "shipwreck", "submarine", "diver", "sailor", "storm", "abyss", "silhouette",
    )
    subj_norm = f" {_norm_text(subject)} {_norm_text(narration)} "
    for fw in focus_candidates:
        if f" {fw} " in subj_norm and fw not in (core or ""):
            scene_focus_words.append(fw)
        if len(scene_focus_words) >= 2:
            break
    focus_str = " ".join(scene_focus_words)

    base_creature = core or " ".join(subject_terms(subject)[:3]) or "giant sea monster"
    is_marine = any(
        k in norm_all
        for k in (
            "kraken", "megalodon", "leviathan", "cthulhu", "loch ness", "nessie", "bloop",
            "squid", "sea", "ocean", "abyss", "shark", "whale", "mosasaurus", "liopleurodon",
            "dunkleosteus", "helicoprion", "livyatan", "anglerfish", "oarfish", "thalassophobia",
            "ship", "sailor", "biển", "đại dương", "thủy quái",
        )
    )

    qs: List[str] = []
    prefix_giant = "" if base_creature.lower().startswith(("giant ", "colossal ")) else "giant "
    # 1. Truy vấn gắn trực tiếp hành động/chi tiết của phân cảnh + tên quái vật cốt lõi (4-6 từ)
    if focus_str:
        qs.append(f"{prefix_giant}{base_creature} {focus_str} cinematic cgi")
    if os.getenv("CREATURE_ALLOW_MOVIE_STILLS", "true").strip().lower() not in ("0", "false", "no", "off"):
        ref = _CREATURE_FILM_REFS.get(core) or next(
            (v for k, v in _CREATURE_FILM_REFS.items() if f" {k} " in norm_all), None
        )
        if ref and ref not in qs:
            qs.append(ref)

    # 2. Truy vấn ArtStation / Concept Art 8K ngắn gọn (5-6 từ)
    if is_marine:
        q_art = f"{prefix_giant}{base_creature} {scene_focus_words[0] if scene_focus_words else 'ocean'} monster artstation"
        q_thal = f"{base_creature} sea monster thalassophobia 8k"
        for q in (q_art, q_thal):
            if q not in qs:
                qs.append(q)
    else:
        q_art = f"terrifying {base_creature} {focus_str} horror artstation".strip()
        q_dark = f"{base_creature} monster dark horror cgi"
        for q in (q_art, q_dark):
            if q not in qs:
                qs.append(q)

    # 3. Truy vấn cô đọng 2-3 từ cho ArtStation / Wallhaven API
    short_q = f"{base_creature} monster" if "monster" not in base_creature else base_creature
    if short_q not in qs:
        qs.append(short_q)

    return qs[:4]


# Các tên miền báo chí khoa học / bách khoa / bảo tàng uy tín trên Google Images (dùng cho chủ thể khoa học/lịch sử thường)
_AUTHORITATIVE_DOMAINS = (
    "wikipedia.org", "wikimedia.org", "nationalgeographic", "britannica.com", "smithsonianmag",
    "nature.com", "science.org", "scientificamerican", "bbc.co", "bbc.com", "nasa.gov", "noaa.gov",
    "nhm.ac.uk", "australian.museum", "audubon.org", "livescience.com", "newscientist.com",
    "theguardian.com", "nytimes.com", "reuters.com", "apnews.com", "flickr.com", "inaturalist.org",
    "ebird.org", "fishbase", "animaldiversity", "arkive", "montereybayaquarium", "oceana.org",
)


def heuristic_score(item: Dict, terms: Iterable[str], creature_mode: bool = False) -> float:
    """Điểm 0-10 ước lượng độ hấp dẫn + liên quan của ảnh dựa trên metadata (không cần AI)."""
    terms = list(terms or [])
    w, h = _dims(item)
    score = 4.0

    # Bố cục: Shorts cần ảnh dọc hoặc ảnh độ phân giải cao đủ để crop 9:16 sắc nét
    if w and h:
        ratio = w / h
        if ratio <= 0.8:
            score += 1.5
        elif ratio <= 1.25:
            score += 1.1
        elif ratio <= 1.85 and min(w, h) >= 900:
            score += 0.8
        elif ratio >= 2.1:
            score -= 1.0
        short_side = min(w, h)
        if short_side >= 1080:
            score += 1.5
        elif short_side >= 720:
            score += 0.9
        elif short_side < 500:
            score -= 2.0

    # Tiêu đề / mô tả / URL bài viết khớp chủ thể
    text = _norm_text(
        f"{item.get('title') or ''} {item.get('alt') or ''} "
        f"{item.get('description') or ''} {item.get('page_url') or ''}"
    )
    if not text and "/photo/" in (item.get("url") or ""):
        m = re.search(r"/photo/([a-zA-Z0-9-]+)-\d+/?", item.get("url", ""))
        text = _norm_text(m.group(1)) if m else ""

    frac = 0.0
    if terms:
        hits = sum(1 for t in terms if re.search(rf"\b{re.escape(t)}s?\b", text))
        frac = hits / len(terms)
        # Thưởng điểm khớp chủ thể nhưng không phạt nặng các bức ảnh nghệ thuật/CGI có tiêu đề ẩn dụ từ Google Search
        score += 3.6 * frac
        if hits == 0 and text:
            score -= 0.8

    src = (item.get("source") or "").lower()
    blob_domain = f"{item.get('creator') or ''} {item.get('page_url') or ''} {item.get('url') or ''}".lower()
    padded = f" {text} "

    # Thưởng điểm cho nguồn Hot-Trend / Điện ảnh / Viral (ArtStation, Pinterest, Reddit, Wallpaper 8K, Movie Stills)
    is_hot_domain = any(dom in blob_domain for dom in _HOT_TREND_DOMAINS)
    hot_cue_hits = sum(1 for c in _HOT_TREND_CUES if f" {c} " in padded)
    epic_cue_hits = sum(1 for c in _CREATURE_BONUS_CUES if f" {c} " in padded)

    if creature_mode:
        # Chế độ Quái Vật / Kinh Dị: Ưu tiên tuyệt đối Google Images + ArtStation / Movie CGI / Thalassophobia
        if src in ("google_images", "open_web"):
            score += 1.3
        if is_hot_domain:
            score += 1.8
        # Trừ điểm Wikipedia/Wikimedia/Bảo tàng trong creature_mode vì hay trả về tranh khắc gỗ cũ hoặc hóa thạch răng
        if src in ("wikipedia", "wikimedia_commons", "openverse") or any(
            d in blob_domain for d in ("wikimedia.org", "wikipedia.org", "nhm.ac.uk", "smithsonian", "fishbase", "inaturalist")
        ):
            score -= 1.8
        score += min(2.6, 0.75 * epic_cue_hits)
        if any(f" {c} " in padded for c in _CREATURE_PENALTY_CUES):
            score = min(score - 5.5, 3.5)
    else:
        # Chế độ Gemini Search (Không cần ảnh thật 100%, chỉ cần BẮT MẮT, CÂU VIEW, ĐIỆN ẢNH 8K):
        if src in ("google_images", "open_web"):
            score += 1.3
        elif src in ("pexels", "pexels_photo", "pixabay", "unsplash"):
            score += 0.2
        elif src in ("wikipedia", "wikimedia_commons", "openverse"):
            score -= 0.6

        if is_hot_domain:
            score += 1.5
        if hot_cue_hits > 0:
            score += min(1.6, 0.55 * hot_cue_hits)
        if epic_cue_hits > 0:
            score += min(1.2, 0.4 * epic_cue_hits)
        if any(dom in blob_domain for dom in _AUTHORITATIVE_DOMAINS):
            score += 0.4
        if any(f" {c} " in padded for c in _SOFT_TITLE_CUES):
            score -= 2.0

    return round(max(0.0, min(10.0, score)), 2)


def prefilter_candidates(
    candidates: List[Dict],
    subject: Optional[str] = None,
    limit: int = 12,
    max_per_source: int = 5,
    creature_mode: bool = False,
) -> List[Dict]:
    """
    Loại ảnh watermark/quá nhỏ, chấm heuristic rồi sắp xếp giảm dần (giữ đa dạng nguồn & tên miền).
    Gắn '_hscore' vào từng item để các bước sau dùng khi Vision không khả dụng.
    """
    terms = subject_terms(subject)
    kept: List[Dict] = []
    seen = set()
    for idx, it in enumerate(candidates or []):
        u = it.get("url")
        if not u or u in seen:
            continue
        seen.add(u)
        if is_watermark_risk(it):
            continue
        w, h = _dims(it)
        if w and h and min(w, h) < 400:
            continue
        it["_hscore"] = heuristic_score(it, terms, creature_mode=creature_mode)
        it["_order"] = idx
        kept.append(it)

    kept.sort(key=lambda x: (-x["_hscore"], x["_order"]))

    out: List[Dict] = []
    per_src: Dict[str, int] = {}
    per_domain: Dict[str, int] = {}
    overflow: List[Dict] = []
    for it in kept:
        src = it.get("source") or "?"
        dom = (it.get("creator") or it.get("provider") or src).lower()
        # Giới hạn tối đa 3 ảnh trên cùng 1 tên miền (vd: artstation.com, wallhaven.cc, deviantart.com) ở lượt đầu
        if per_src.get(src, 0) >= max_per_source or per_domain.get(dom, 0) >= 3:
            overflow.append(it)
            continue
        per_src[src] = per_src.get(src, 0) + 1
        per_domain[dom] = per_domain.get(dom, 0) + 1
        out.append(it)
        if len(out) >= limit:
            break
    if len(out) < limit:
        out.extend(overflow[: limit - len(out)])
    return out


# ─────────────────────────────── Khung hình dọc 9:16 ───────────────────────────────

def _saliency_center_x(im, crop_w: int) -> int:
    """Tìm vị trí cửa sổ crop (theo trục ngang) chứa nhiều chi tiết/biên cạnh nhất."""
    from PIL import ImageFilter

    w, h = im.size
    small_w = 256
    scale = small_w / w
    small = im.convert("L").resize((small_w, max(1, int(h * scale))))
    edges = small.filter(ImageFilter.FIND_EDGES)
    px = edges.load()
    sw, sh = edges.size
    # Bỏ qua 4% viền (biên ảnh thường có cạnh giả)
    y0, y1 = int(sh * 0.04), int(sh * 0.96)
    col = [0.0] * sw
    for x in range(sw):
        s = 0
        for y in range(y0, y1, 2):
            s += px[x, y]
        col[x] = s
    # Ưu tiên nhẹ vùng trung tâm (chủ thể thường ở giữa)
    for x in range(sw):
        d = abs(x - sw / 2) / (sw / 2)
        col[x] *= 1.0 - 0.35 * d
    win = max(1, int(crop_w * scale))
    if win >= sw:
        return w // 2
    cur = sum(col[:win])
    best, best_x = cur, 0
    for x in range(1, sw - win + 1):
        cur += col[x + win - 1] - col[x - 1]
        if cur > best:
            best, best_x = cur, x
    return int((best_x + win / 2) / scale)


def _saliency_center_y(im, crop_h: int) -> int:
    """Tìm vị trí cửa sổ crop (theo trục dọc) chứa nhiều chi tiết nhất, ưu tiên vùng 1/3 trên (đầu/mặt chủ thể)."""
    from PIL import ImageFilter

    w, h = im.size
    small_h = 256
    scale = small_h / h
    small = im.convert("L").resize((max(1, int(w * scale)), small_h))
    edges = small.filter(ImageFilter.FIND_EDGES)
    px = edges.load()
    sw, sh = edges.size
    x0, x1 = int(sw * 0.04), int(sw * 0.96)
    row = [0.0] * sh
    for y in range(sh):
        s = 0
        for x in range(x0, x1, 2):
            s += px[x, y]
        row[y] = s
    # Ưu tiên vùng trọng tâm 38% từ đỉnh ảnh (tránh cắt mất đầu nhân vật/động vật)
    focal_y = sh * 0.38
    for y in range(sh):
        d = abs(y - focal_y) / (sh * 0.62)
        row[y] *= max(0.55, 1.0 - 0.35 * d)
    win = max(1, int(crop_h * scale))
    if win >= sh:
        return h // 2
    cur = sum(row[:win])
    best, best_y = cur, 0
    for y in range(1, sh - win + 1):
        cur += row[y + win - 1] - row[y - 1]
        if cur > best:
            best, best_y = cur, y
    return int((best_y + win / 2) / scale)


def _enhance_visual_clarity(im, is_hook_scene: bool = False):
    """
    Tăng độ sắc nét vi mô (micro-sharpness), độ tương phản và độ bão hòa màu:
    - Với Cảnh 1 (is_hook_scene=True): Tự động cứu sáng nếu ảnh gốc quá tối (< 72 lum),
      kích màu HDR (+16% Color), tương phản (+10% Contrast) và độ nét (+22% Sharpness)
      để tạo hiệu ứng thị giác 'WOW' ngay ở Frame 0.0s.
    """
    from PIL import ImageEnhance, ImageStat
    try:
        if is_hook_scene:
            lum_stat = ImageStat.Stat(im.convert("L"))
            mean_lum = float(lum_stat.mean[0]) if lum_stat.mean else 100.0
            if mean_lum < 72.0:
                lift = min(1.36, max(1.06, 80.0 / max(mean_lum, 26.0)))
                im = ImageEnhance.Brightness(im).enhance(lift)
            im = ImageEnhance.Color(im).enhance(1.16)
            im = ImageEnhance.Contrast(im).enhance(1.10)
            im = ImageEnhance.Sharpness(im).enhance(1.22)
        else:
            im = ImageEnhance.Sharpness(im).enhance(1.08)
            im = ImageEnhance.Contrast(im).enhance(1.04)
    except Exception:
        pass
    return im


def assess_image_wow_factor(image_path: str) -> Dict[str, float]:
    """
    Kiểm định chất lượng thị giác 'WOW' thực tế của file ảnh bitmap (0.0 - 10.0):
    Đo độ sáng sau khi nâng HDR Cảnh 1 (luminance), độ tương phản (contrast stddev),
    độ chi tiết biên (edge sharpness) và độ rực màu.
    Hỗ trợ cả phong cách Chiaroscuro / Dark Abyssal (nền tối sâu nhưng có điểm nhấn sáng rực).
    """
    from PIL import Image, ImageStat, ImageFilter
    try:
        with Image.open(image_path) as im:
            rgb = im.convert("RGB")
            w, h = rgb.size
            small = _enhance_visual_clarity(rgb.resize((180, 320), Image.BILINEAR), is_hook_scene=True)
            gray = small.convert("L")
            g_stat = ImageStat.Stat(gray)
            mean_lum = float(g_stat.mean[0])
            std_con = float(g_stat.stddev[0])
            hist = gray.histogram()
            total_px = max(1, sum(hist))
            # Điểm sáng 5% đỉnh (p95) để nhận diện mắt phát sáng / rim light trong cảnh biển sâu / kinh dị
            cum = 0
            p95_lum = 255
            for val in range(255, -1, -1):
                cum += hist[val]
                if cum >= total_px * 0.05:
                    p95_lum = val
                    break

            edges = gray.filter(ImageFilter.FIND_EDGES)
            edge_energy = float(ImageStat.Stat(edges).mean[0])

            rgb_stat = ImageStat.Stat(small)
            r_m, g_m, b_m = rgb_stat.mean[:3]
            color_spread = max(abs(r_m - g_m), abs(g_m - b_m), abs(r_m - b_m)) + sum(rgb_stat.stddev[:3]) / 3.0

            score = 5.4
            if 45.0 <= mean_lum <= 200.0:
                score += 1.2
            elif mean_lum >= 25.0 and p95_lum >= 95:
                # Phong cách Dark Fantasy / Thalassophobia có điểm nhấn sáng (glowing eyes / storm lightning)
                score += 0.9
            elif mean_lum < 18.0 or mean_lum > 240.0:
                score -= 2.5
            else:
                score -= 0.4

            if std_con >= 42.0 or (std_con >= 30.0 and p95_lum >= 110):
                score += 1.5
            elif std_con >= 26.0:
                score += 0.8
            elif std_con < 16.0:
                score -= 2.0

            if edge_energy >= 20.0:
                score += 1.3
            elif edge_energy >= 12.0:
                score += 0.7
            elif edge_energy < 7.5:
                score -= 1.8

            if color_spread >= 55.0:
                score += 1.0
            elif color_spread < 24.0:
                score -= 0.6

            if min(w, h) >= 720:
                score += 0.6

            final_score = round(max(0.0, min(10.0, score)), 2)
            return {
                "wow_score": final_score,
                "is_wow": final_score >= 6.5,
                "luminance": round(mean_lum, 1),
                "contrast": round(std_con, 1),
                "sharpness": round(edge_energy, 1),
            }
    except Exception:
        return {"wow_score": 5.0, "is_wow": False, "luminance": 0.0, "contrast": 0.0, "sharpness": 0.0}


def prepare_vertical(im, target_w: int, target_h: int, max_upscale: float = 2.0, is_hook_scene: bool = False):
    """
    Đưa ảnh bất kỳ về khung target_w×target_h KHÔNG kéo giãn:
    - Tỷ lệ gần đúng → cover-crop chính giữa.
    - Ảnh cao hơn 9:16 → cover-crop dọc bám theo vùng chi tiết/khuôn mặt (_saliency_center_y).
    - Ảnh ngang đủ lớn → cover-crop ngang bám theo vùng nhiều chi tiết nhất (_saliency_center_x).
    - Ảnh ngang quá nhỏ (crop sẽ vỡ hạt) → nền mờ phóng to + ảnh gốc sắc nét ở giữa.
    """
    from PIL import Image, ImageFilter, ImageEnhance

    if im.mode != "RGB":
        im = im.convert("RGB")
    w, h = im.size
    target_ratio = target_w / target_h
    src_ratio = w / h

    if abs(src_ratio - target_ratio) / target_ratio < 0.04:
        return _enhance_visual_clarity(im.resize((target_w, target_h), Image.LANCZOS), is_hook_scene=is_hook_scene)

    if src_ratio < target_ratio:
        # Ảnh cao/hẹp hơn 9:16 → cắt bớt trên/dưới theo saliency dọc (bảo toàn mặt/đầu chủ thể)
        new_h = int(w / target_ratio)
        cy = _saliency_center_y(im, new_h)
        top = min(max(0, cy - new_h // 2), h - new_h)
        return _enhance_visual_clarity(
            im.crop((0, top, w, top + new_h)).resize((target_w, target_h), Image.LANCZOS),
            is_hook_scene=is_hook_scene,
        )

    # Ảnh rộng hơn 9:16
    crop_w = int(h * target_ratio)
    upscale = target_h / h
    if upscale <= max_upscale or src_ratio < 1.0:
        cx = _saliency_center_x(im, crop_w)
        left = min(max(0, cx - crop_w // 2), w - crop_w)
        return _enhance_visual_clarity(
            im.crop((left, 0, left + crop_w, h)).resize((target_w, target_h), Image.LANCZOS),
            is_hook_scene=is_hook_scene,
        )

    # Ảnh nhỏ & rất ngang: nền mờ + foreground rõ nét (zoom nhẹ 1.12 để chiếm nhiều diện tích hơn)
    bg_scale = max(target_w / w, target_h / h)
    bg = im.resize((int(w * bg_scale) + 1, int(h * bg_scale) + 1), Image.LANCZOS)
    bl = (bg.width - target_w) // 2
    bt = (bg.height - target_h) // 2
    bg = bg.crop((bl, bt, bl + target_w, bt + target_h)).filter(ImageFilter.GaussianBlur(28))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    fg_w = int(target_w * 1.12)
    fg_h = int(h * fg_w / w)
    fg = _enhance_visual_clarity(im.resize((fg_w, fg_h), Image.LANCZOS), is_hook_scene=is_hook_scene)
    fl = (fg_w - target_w) // 2
    fg = fg.crop((fl, 0, fl + target_w, fg_h))
    bg.paste(fg, (0, (target_h - fg_h) // 2))
    return bg
