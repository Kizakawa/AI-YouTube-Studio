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
)

# Tiêu đề chứa các cụm này gần như chắc chắn là ảnh stock có watermark / clipart / sản phẩm
_HARD_TITLE_CUES = (
    "stock photo", "stock image", "stock picture", "premium photo", "premium vector",
    "royalty free", "royalty-free", "vector", "clipart", "clip art", "coloring page",
    "colouring", "t-shirt", "tshirt", "sticker", "mug ", "poster print", "canvas print",
    "framed print", "art print", "for sale", "buy now",
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

# Cảnh phim & ArtStation horror cực kỳ rùng rợn cho từng quái vật
_CREATURE_FILM_REFS = {
    "kraken": "terrifying giant kraken tentacles attacking ship dark ocean movie cgi",
    "megalodon": "terrifying giant megalodon shark jaws lurking deep dark ocean movie scene",
    "godzilla": "Godzilla terrifying giant monster dark storm movie scene 8k",
    "loch ness": "terrifying loch ness monster dark underwater silhouette thalassophobia art",
    "nessie": "terrifying loch ness monster dark underwater silhouette thalassophobia art",
    "cthulhu": "terrifying Cthulhu eldritch horror rising dark ocean megalophobia artstation",
    "leviathan": "terrifying colossal leviathan sea monster dark abyss thalassophobia artstation",
    "sea serpent": "terrifying giant sea serpent monster dark ocean thalassophobia art",
    "sea monster": "terrifying giant sea monster lurking beneath boat thalassophobia artstation",
    "bloop": "the bloop terrifying colossal deep sea monster thalassophobia megalophobia",
    "giant squid": "terrifying colossal giant squid tentacles attacking submarine dark ocean cgi",
    "colossal squid": "terrifying colossal squid deep dark abyssal ocean monster artstation",
    "magnapinna": "bigfin magnapinna squid eerie deep sea ROV footage horror",
    "bigfin squid": "bigfin magnapinna squid eerie deep sea ROV footage horror",
    "titanoboa": "terrifying giant titanoboa snake monster dark jungle movie cgi",
    "mosasaurus": "terrifying giant mosasaurus underwater jaws Jurassic World scene",
    "dunkleosteus": "terrifying giant dunkleosteus armored predator dark ocean artstation",
    "helicoprion": "terrifying helicoprion spiral saw jaw shark dark ocean art",
    "livyatan": "terrifying livyatan melvillei giant whale monster vs megalodon art",
    "mothman": "terrifying mothman glowing red eyes dark night horror artstation",
    "wendigo": "terrifying wendigo antler skull monster dark forest horror artstation",
    "skinwalker": "terrifying skinwalker creature night vision horror found footage",
    "siren head": "terrifying siren head giant monster dark fog horror trevor henderson",
    "scp": "terrifying scp monster containment breach dark horror artstation",
    "backrooms": "terrifying backrooms entity found footage analog horror",
    "anglerfish": "terrifying deep sea anglerfish glowing lure pitch black abyss",
    "goblin shark": "terrifying deep sea goblin shark protruding jaws pitch black water",
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
    )
    return any(cue in raw_ctx for cue in vi_horror_cues)


def creature_queries(subject: str) -> List[str]:
    """Truy vấn tìm ảnh quái vật cực kỳ rùng rợn, ám ảnh: Thalassophobia, Megalophobia, Movie CGI, Dark Eldritch ArtStation."""
    import os
    subject = (subject or "").strip()
    if not subject:
        return []
    norm = f" {_norm_text(subject)} "
    is_marine = any(
        k in norm
        for k in (
            "kraken", "megalodon", "leviathan", "cthulhu", "loch ness", "nessie", "bloop",
            "squid", "sea", "ocean", "abyss", "shark", "whale", "mosasaurus", "liopleurodon",
            "dunkleosteus", "helicoprion", "livyatan", "anglerfish", "oarfish", "thalassophobia",
        )
    )
    qs: List[str] = []
    if os.getenv("CREATURE_ALLOW_MOVIE_STILLS", "true").strip().lower() not in ("0", "false", "no", "off"):
        ref = next((v for k, v in _CREATURE_FILM_REFS.items() if f" {k} " in norm), None)
        if ref:
            qs.append(ref)
        else:
            qs.append(f"{subject} terrifying horror movie scene cgi")

    if is_marine:
        qs.append(f"{subject} terrifying thalassophobia megalophobia giant monster dark ocean")
        qs.append(f"{subject} dark abyssal sea monster artstation 8k")
    else:
        qs.append(f"{subject} terrifying dark horror monster artstation 8k")
        qs.append(f"{subject} creepy nightmare creature lurking in darkness")

    qs.append(f"{subject} dark eldritch horror concept art")
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
        elif ratio <= 1.65 and min(w, h) >= 900:
            score += 0.5
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
            score += 1.4
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
            score += 1.2
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
    Loại ảnh watermark/quá nhỏ, chấm heuristic rồi sắp xếp giảm dần (giữ đa dạng nguồn).
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
    overflow: List[Dict] = []
    for it in kept:
        src = it.get("source") or "?"
        if per_src.get(src, 0) >= max_per_source:
            overflow.append(it)
            continue
        per_src[src] = per_src.get(src, 0) + 1
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


def _enhance_visual_clarity(im):
    """Tăng nhẹ độ sắc nét vi mô (micro-sharpness) và độ tương phản để ảnh web/stock rực rỡ chuẩn Full HD."""
    from PIL import ImageEnhance
    try:
        im = ImageEnhance.Sharpness(im).enhance(1.08)
        im = ImageEnhance.Contrast(im).enhance(1.04)
    except Exception:
        pass
    return im


def prepare_vertical(im, target_w: int, target_h: int, max_upscale: float = 2.0):
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
        return _enhance_visual_clarity(im.resize((target_w, target_h), Image.LANCZOS))

    if src_ratio < target_ratio:
        # Ảnh cao/hẹp hơn 9:16 → cắt bớt trên/dưới theo saliency dọc (bảo toàn mặt/đầu chủ thể)
        new_h = int(w / target_ratio)
        cy = _saliency_center_y(im, new_h)
        top = min(max(0, cy - new_h // 2), h - new_h)
        return _enhance_visual_clarity(im.crop((0, top, w, top + new_h)).resize((target_w, target_h), Image.LANCZOS))

    # Ảnh rộng hơn 9:16
    crop_w = int(h * target_ratio)
    upscale = target_h / h
    if upscale <= max_upscale or src_ratio < 1.0:
        cx = _saliency_center_x(im, crop_w)
        left = min(max(0, cx - crop_w // 2), w - crop_w)
        return _enhance_visual_clarity(im.crop((left, 0, left + crop_w, h)).resize((target_w, target_h), Image.LANCZOS))

    # Ảnh nhỏ & rất ngang: nền mờ + foreground rõ nét (zoom nhẹ 1.12 để chiếm nhiều diện tích hơn)
    bg_scale = max(target_w / w, target_h / h)
    bg = im.resize((int(w * bg_scale) + 1, int(h * bg_scale) + 1), Image.LANCZOS)
    bl = (bg.width - target_w) // 2
    bt = (bg.height - target_h) // 2
    bg = bg.crop((bl, bt, bl + target_w, bt + target_h)).filter(ImageFilter.GaussianBlur(28))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    fg_w = int(target_w * 1.12)
    fg_h = int(h * fg_w / w)
    fg = _enhance_visual_clarity(im.resize((fg_w, fg_h), Image.LANCZOS))
    fl = (fg_w - target_w) // 2
    fg = fg.crop((fl, 0, fl + target_w, fg_h))
    bg.paste(fg, (0, (target_h - fg_h) // 2))
    return bg
