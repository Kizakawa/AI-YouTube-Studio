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

# Tiêu đề chứa các cụm này → trừ điểm (thường không phải ảnh chụp thực tế đẹp)
_SOFT_TITLE_CUES = (
    "illustration", "cartoon", "drawing", "sketch", "download", "logo", "icon",
    "infographic", "diagram", "chart", "map of", "meme", "quote", "text", "anime",
    "3d render", "toy", "figurine", "lego", "plush", "costume", "tattoo",
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


# ─────────────────────────────── Chế độ quái vật / sinh vật huyền thoại ───────────────────────────────
# Những chủ thể KHÔNG có (hoặc gần như không có) ảnh chụp thật đẹp → ảnh hấp dẫn nhất trên mạng là
# concept art, cảnh phim CGI, digital art u tối (vd: Kraken trong "Cướp biển vùng Caribe").
_CREATURE_WORDS = (
    "kraken", "leviathan", "megalodon", "sea serpent", "sea monster", "cthulhu", "godzilla",
    "loch ness", "nessie", "bloop", "mothman", "wendigo", "chupacabra", "bigfoot", "sasquatch",
    "yeti", "skinwalker", "behemoth", "basilisk", "giant squid",
    "colossal squid", "monster", "cryptid", "demon", "lusca", "jormungandr", "charybdis",
    "scylla", "tiamat", "ningen", "kaiju", "werewolf", "vampire", "ghost ship",
    "abyssal creature", "eldritch", "mermaid", "thunderbird", "jersey devil", "dogman",
)
# Tên có chữ "monster"/"dragon" nhưng là động vật THẬT → không bật chế độ quái vật
_REAL_ANIMAL_EXCEPTIONS = ("gila monster", "komodo dragon", "sea dragon", "dragonfish", "dragonfly")

# Cảnh phim nổi tiếng cho từng quái vật — Bing/DDG trả về still phim chất lượng cao
_CREATURE_FILM_REFS = {
    "kraken": "kraken pirates movie",
    "megalodon": "megalodon movie scene",
    "godzilla": "Godzilla movie scene",
    "loch ness": "Loch Ness monster",
    "cthulhu": "Cthulhu rising from the sea art",
    "leviathan": "leviathan sea monster art",
    "sea serpent": "giant sea serpent attacking ship",
    "mothman": "mothman cryptid art",
    "wendigo": "wendigo horror art",
}

_CREATURE_BONUS_CUES = (
    "concept art", "art", "artstation", "digital art", "cgi", "movie", "film", "scene", "dark",
    "horror", "terrifying", "scary", "creepy", "attack", "attacking", "giant", "wallpaper",
    "render", "fantasy", "epic", "monster", "beast", "rising",
)

_CREATURE_PENALTY_CUES = (
    "cartoon", "cute", "kawaii", "chibi", "toy", "plush", "lego", "figurine", "costume", "mask",
    "coloring", "logo", "icon", "meme", "chart", "diagram", "infographic", "sticker", "emoji",
    "clipart", "kids", "baby", "funny", "cosplay", "halloween decoration", "cake",
    "caught on camera", "spotted in real life", "top 10", "top 5", "reaction", "thumbnail",
)


def is_creature_subject(subject: Optional[str], narration: str = "", topic: str = "") -> bool:
    """True nếu chủ thể là quái vật / sinh vật huyền thoại (ảnh đẹp nhất là concept art / cảnh phim)."""
    import os
    if os.getenv("CREATURE_ART_MODE", "true").strip().lower() in ("0", "false", "no", "off"):
        return False
    subj = f" {_norm_text(subject or '')} "
    if any(f" {x} " in subj or x in subj for x in _REAL_ANIMAL_EXCEPTIONS):
        return False
    if any(f" {w} " in subj for w in _CREATURE_WORDS):
        return True
    # Chủ thể chung chung nhưng lời thoại/chủ đề nói rõ về quái vật huyền thoại
    ctx = f" {_norm_text(narration)} {_norm_text(topic)} "
    strong = ("kraken", "sea monster", "leviathan", "megalodon", "cryptid", "cthulhu", "sea serpent", "kaiju")
    return any(f" {w} " in ctx for w in strong)


def creature_queries(subject: str) -> List[str]:
    """Truy vấn tìm ảnh quái vật kịch tính: concept art, cảnh phim, dark fantasy."""
    import os
    subject = (subject or "").strip()
    if not subject:
        return []
    qs = [f"{subject} concept art", f"{subject} dark fantasy art"]
    if os.getenv("CREATURE_ALLOW_MOVIE_STILLS", "true").strip().lower() not in ("0", "false", "no", "off"):
        norm = f" {_norm_text(subject)} "
        ref = next((v for k, v in _CREATURE_FILM_REFS.items() if f" {k} " in norm), None)
        qs.insert(1, ref or f"{subject} movie scene")
    return qs


# Các tên miền báo chí khoa học / bách khoa / bảo tàng uy tín trên Google Images
_AUTHORITATIVE_DOMAINS = (
    "wikipedia.org", "wikimedia.org", "nationalgeographic", "britannica.com", "smithsonianmag",
    "nature.com", "science.org", "scientificamerican", "bbc.co", "bbc.com", "nasa.gov", "noaa.gov",
    "nhm.ac.uk", "australian.museum", "audubon.org", "livescience.com", "newscientist.com",
    "theguardian.com", "nytimes.com", "reuters.com", "apnews.com", "flickr.com", "inaturalist.org",
    "ebird.org", "fishbase", "animaldiversity", "arkive", "Montereybayaquarium", "oceana.org",
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
            score += 0.4
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
        # Khớp ĐỦ chủ thể (cả bổ ngữ loài/tên riêng) quan trọng hơn khớp một phần ("eagle" ≠ "wedge-tailed eagle")
        score += 4.2 * frac * frac
        if hits == 0 and text:
            score -= 2.0

    # Nguồn ảnh sạch & độ tin cậy thực tế (Google Images / Wikipedia / Bảo tàng / Nhiếp ảnh)
    src = (item.get("source") or "").lower()
    blob_domain = f"{item.get('creator') or ''} {item.get('page_url') or ''} {item.get('url') or ''}".lower()
    if src in ("google_images", "wikipedia", "wikimedia_commons") and frac >= 0.5:
        score += 0.8
    elif src in ("pexels", "pexels_photo", "pixabay", "unsplash") and not creature_mode:
        score += 0.5

    if any(dom in blob_domain for dom in _AUTHORITATIVE_DOMAINS):
        score += 0.6

    padded = f" {text} "
    if creature_mode:
        # Quái vật: concept art / CGI / cảnh phim là ĐIỂM CỘNG; hoạt hình dễ thương / đồ chơi là điểm trừ
        bonus = sum(1 for c in _CREATURE_BONUS_CUES if f" {c} " in padded)
        score += min(2.0, 0.7 * bonus)
        if any(f" {c} " in padded for c in _CREATURE_PENALTY_CUES):
            score -= 3.0
    elif any(f" {c} " in padded for c in _SOFT_TITLE_CUES):
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
