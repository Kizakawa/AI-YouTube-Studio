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

# ─────────────────────────────── Watermark / stock / fake prop / poster risk ───────────────────────────────
# Domain ảnh stock trả phí (luôn có watermark) HOẶC trang AI chất lượng thấp / bán đồ in ấn
WATERMARK_DOMAINS = (
    "shutterstock", "alamy", "istockphoto", "gettyimages", "dreamstime", "depositphotos",
    "123rf", "stock.adobe", "adobestock", "fineartamerica", "pixels.com", "vectorstock",
    "bigstockphoto", "agefotostock", "canstockphoto", "pond5", "storyblocks", "vecteezy",
    "featurepics", "colourbox", "superstock", "masterfile", "photocase", "eyeem",
    "redbubble", "teepublic", "zazzle", "etsy", "society6", "cafepress", "displate",
    "img.freepik.com/premium-", "freepik.com/premium-", "craiyon.com", "media.craiyon.com",
    "nightcafe.studio", "images.nightcafe.studio", "deepdreamgenerator", "artbreeder.com",
    "furaffinity.net", "wattpad.com", "newgrounds.com",
    # Chặn các trang code, slide thuyết trình, tài liệu học thuật, biểu đồ và hướng dẫn nhiếp ảnh lạc đề
    "kaggle.com", "slideshare.net", "scribd.com", "researchgate.net", "academia.edu",
    "chegg.com", "coursehero.com", "quizlet.com", "github.com", "stackoverflow.com",
    "towardsdatascience.com", "analyticsvidhya.com", "promptden.com", "eartohear",
    "qaqooking", "about.com", "fity.club", "designbundles.net",
)

# Tiêu đề / URL chứa các cụm này là ảnh stock watermark / mô hình nhựa bảo tàng ("trông đểu") / poster chữ / game khối vuông / biểu đồ code
_HARD_TITLE_CUES = (
    "stock photo", "stock image", "stock picture", "premium photo", "premium vector",
    "royalty free", "royalty-free", "vector", "clipart", "clip art", "coloring page",
    "colouring", "t-shirt", "tshirt", "sticker", "mug ", "poster print", "canvas print",
    "framed print", "art print", "for sale", "buy now", "watermarked",
    "movie poster", "new poster", "official poster", "teaser poster", "poster for",
    # Chặn mô hình cao su/nhựa, tượng bảo tàng, đồ chơi, thú nhồi bông làm ảnh trông giả ("đểu")
    "animatronic", "animatronics", "prop ", "movie prop", "replica", "mannequin", "puppet",
    "wax figure", "museum exhibit", "exhibition", "theme park ride", "universal studios ride",
    "action figure", "toy ", "toys", "figurine", "lego", "plush", "stuffed animal", "taxidermy",
    "costume", "cosplay", "inflatable", "statue", "sculpture",
    "minecraft", "roblox", "fortnite",
    # Chặn ảnh chụp màn hình code, biểu đồ thống kê, sơ đồ kỹ thuật và slide bài giảng
    "k-means", "kmeans", "clustering", "python notebook", "jupyter", "matplotlib",
    "histogram", "bar chart", "pie chart", "scatter plot", "flowchart", "schematic",
    "cross-section", "cross section", "infographic", "worksheet", "slide ", "slideshare",
    "radar absorbing", "damaged zones",
)

# Tiêu đề chứa các cụm này → trừ điểm mạnh (tranh vẽ tay 2D phẳng, đồ chơi, sơ đồ, chữ chèn)
_SOFT_TITLE_CUES = (
    "cartoon", "drawing", "sketch", "watercolor", "pencil", "comic", "manga", "chibi",
    "download", "logo", "icon", "infographic", "diagram", "chart", "map of", "meme",
    "quote", "text", "tattoo", "museum", "exhibit", "specimen", "fossil", "skeleton",
    "poster", "dvd cover", "book cover",
)

# Nguồn Điện Ảnh 4K/8K & Siêu Phẩm Thị Giác Viral trên mạng (ArtStation, Pinterest, Wallhaven, 4K UHD, IMDb, Báo điện ảnh)
_HOT_TREND_DOMAINS = (
    "artstation.com", "cdna.artstation.com", "cdnb.artstation.com",
    "pinimg.com", "pinterest.com", "wallhaven.cc", "w.wallhaven.cc", "th.wallhaven.cc",
    "hdqwalls.com", "images.hdqwalls.com", "alphacoders.com", "images.alphacoders.com",
    "wallpaperflare.com", "c4.wallpaperflare.com", "uhdpaper.com", "peakpx.com", "wallhere.com",
    "imdb.com", "media-amazon.com", "tmdb.org", "image.tmdb.org", "film-grab.com",
    "shotdeck.com", "fancaps.net", "moviestillsdb.com", "collider.com", "screenrant.com", "srcdn.com",
    "variety.com", "hollywoodreporter.com", "empireonline.com", "indiewire.com", "ign.com",
    "polygon.com", "bloody-disgusting.com", "denofgeek.com", "slashfilm.com", "rogerebert.com",
    "rottentomatoes.com", "fandango.com", "futurecdn.net", "gamesradar.com",
    "redd.it", "i.redd.it", "reddit.com", "nationalgeographic.com", "bbc.co.uk", "bbc.com",
)

_HOT_TREND_CUES = (
    "8k", "4k", "uhd", "imax", "vfx", "cgi", "unreal engine", "octane render", "photorealistic",
    "hyperrealistic", "movie scene", "movie still", "film still", "cinematic", "epic",
    "thalassophobia", "megalophobia", "artstation", "masterpiece", "roaring", "attack",
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
    # Thuật ngữ góc máy / nhiếp ảnh / phong cách / template ở đầu hoặc cuối AI prompt — tuyệt đối không được trích thành chủ thể
    "macro", "shot", "shots", "wide", "angle", "wide-angle", "low-angle", "high-angle", "low",
    "close", "close-up", "closeup", "extreme", "cinematic", "cinematography", "breathtaking",
    "dramatic", "photorealistic", "hyperrealistic", "hyper-realistic", "vertical", "portrait",
    "frame", "still", "view", "perspective", "lens", "lighting", "bokeh", "volumetric",
    "tracking", "panning", "aerial", "establishing", "moody", "atmospheric", "film", "scene",
    "noir", "chiaroscuro", "monochrome", "seinen", "thriller", "aesthetic", "shadow", "shadows",
    "silhouette", "dark", "realistic", "photography", "photo", "image", "picture", "wallpaper",
    "art", "illustration", "digital", "render",
    "jaw", "dropping", "jaw-dropping", "scroll", "stopping", "scroll-stopping", "visual",
    "impact", "hook", "detailed", "prompt", "english", "national", "geographic", "bbc",
    "planet", "earth", "documentary", "majestic", "aspect", "ratio", "resolution", "sharp",
    "vivid", "contrast", "dynamic", "golden", "hour", "imax", "live", "action", "live-action",
    "beautiful", "vibrant", "stunning", "epic", "4k", "8k", "razor", "focal", "depth", "rim",
    "peak", "ultra", "retention", "youtube", "shorts", "masterpiece", "award", "winning",
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


_PROMPT_BOILERPLATE_RE = re.compile(
    r"(?:"
    r"jaw[\s\-]*dropping(?:\s+frame[\s\-]*0)?(?:\s+hook)?|"
    r"scroll[\s\-]*stopping(?:\s+visual)?(?:\s+impact)?|"
    r"ultra[\s\-]*high\s+retention(?:\s+youtube\s+shorts)?(?:\s+hook\s+frame)?|"
    r"detailed(?:\s+8k|\s+4k)?(?:\s+vertical)?(?:\s+9:16)?(?:\s+photorealistic)?(?:\s+live[\s\-]*action)?(?:\s+imax)?(?:\s+movie\s+still)?(?:\s+cinematic)?(?:\s+scene)?(?:\s+prompt)?(?:\s+in\s+english)?|"
    r"national\s+geographic(?:\s+underwater|\s+marine|\s+wildlife)?(?:\s+documentary|\s+photograph|\s+photography)?|"
    r"bbc\s+planet\s+earth(?:\s+epic)?(?:\s+wildlife)?(?:\s+documentary)?|"
    r"razor[\s\-]*sharp\s+high[\s\-]*contrast\s+cinematic\s+shot(?:\s+of)?|"
    r"at\s+peak\s+dramatic\s+action(?:\s+with\s+intense\s+focal\s+depth)?(?:\s+and\s+vivid\s+rim\s+lighting)?|"
    r"9:16\s+aspect\s+ratio|8k\s+resolution|4k\s+resolution|aspect\s+ratio|resolution|"
    r"no\s+watermark|no\s+text|no\s+border|award[\s\-]*winning\s+cinematography"
    r")",
    re.IGNORECASE,
)


def clean_search_query_from_prompt(
    raw_query: str,
    narration: str = "",
    topic: str = "",
    max_words: int = 6,
) -> str:
    """
    Làm sạch hoàn toàn một chuỗi câu lệnh (có thể là AI visual_prompt dài bị truyền nhầm)
    thành câu truy vấn tìm ảnh Google/Bing tự nhiên (4-6 từ chỉ chứa chủ thể + hành động thật).
    Triệt tiêu 100% lỗi rò rỉ 'JAW DROPPING HOOK', 'Detailed prompt English', 'National Geographic documentary'...
    """
    cleaned = _PROMPT_BOILERPLATE_RE.sub(" ", raw_query or "")
    terms = [
        w for w in re.findall(r"[A-Za-z]{3,}", cleaned)
        if w.lower() not in _STOPWORDS
    ]
    res = " ".join(terms[:max_words]).strip()
    if len(res.split()) < 2 and narration:
        narr_terms = [
            w for w in re.findall(r"[A-Za-z]{3,}", narration)
            if w.lower() not in _STOPWORDS and w.lower() not in _NUMBER_WORDS
        ]
        res = " ".join(narr_terms[:max_words]).strip()
    core = extract_core_creature(raw_query, narration, topic)
    if core and core not in res.lower():
        res = f"{core} {res}".strip()
        res = " ".join(res.split()[:max_words])
    return res or "dramatic documentary photograph"


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
    cleaned_narr = _PROMPT_BOILERPLATE_RE.sub(" ", narration)
    tokens = re.findall(r"[A-Za-z][A-Za-z'\-]*", cleaned_narr)
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
    """True nếu ảnh có watermark / là clipart / poster quảng cáo phim có chữ / tranh fan-art."""
    blob = " ".join(str(item.get(k) or "") for k in ("url", "page_url", "creator", "thumbnail")).lower()
    if any(d in blob for d in WATERMARK_DOMAINS):
        return True
    if any(p in blob for p in ("-poster.", "_poster.", "-poster-", "_poster_", "/posters/", "movie-poster", "new-poster", "poster-for")):
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
    "t rex", "tyrannosaurus", "spinosaurus", "indominus", "giganotosaurus", "velociraptor",
    "allosaurus", "dinosaur", "dinosaurs", "dragon", "hydra", "cerberus", "fenrir", "minotaur",
    "king kong", "kong",
    "scp", "siren head", "backrooms", "slenderman", "the rake", "crawler", "pale crawler",
    "shadow person", "doppelganger", "analog horror", "thalassophobia", "megalophobia",
    "anglerfish", "goblin shark", "frilled shark", "vampire squid", "giant isopod", "oarfish",
    "gulper eel", "viperfish", "black swallower", "tentacle", "tentacles",
)
# Tên có chữ "monster"/"dragon" nhưng là động vật THẬT → không bật chế độ quái vật
_REAL_ANIMAL_EXCEPTIONS = ("gila monster", "komodo dragon", "sea dragon", "dragonfish", "dragonfly")

# Truy vấn Top Google Images bổ trợ theo từng quái vật cụ thể
_CREATURE_FILM_REFS = {
    "kraken": "giant kraken monster attacking ship",
    "megalodon": "giant megalodon shark attacking submarine",
    "godzilla": "Godzilla King of the Monsters giant monster",
    "loch ness": "giant Loch Ness monster underwater",
    "nessie": "giant Loch Ness monster underwater",
    "cthulhu": "giant Cthulhu monster ocean",
    "leviathan": "giant leviathan sea monster underwater",
    "sea serpent": "giant sea serpent monster ocean",
    "sea monster": "giant sea monster deep ocean thalassophobia",
    "bloop": "the bloop giant sea monster",
    "giant squid": "giant squid attacking ship underwater",
    "colossal squid": "colossal squid deep sea monster",
    "magnapinna": "bigfin squid magnapinna deep sea ROV footage",
    "bigfin squid": "bigfin squid magnapinna deep sea ROV footage",
    "titanoboa": "giant titanoboa snake",
    "mosasaurus": "Jurassic World Mosasaurus giant underwater",
    "t rex": "Tyrannosaurus rex Jurassic World roaring",
    "tyrannosaurus": "Tyrannosaurus rex Jurassic World roaring",
    "spinosaurus": "Spinosaurus dinosaur giant predator",
    "indominus": "Indominus rex Jurassic World",
    "dunkleosteus": "giant Dunkleosteus prehistoric fish",
    "helicoprion": "Helicoprion prehistoric shark",
    "livyatan": "Livyatan melvillei giant whale",
    "mothman": "terrifying Mothman glowing red eyes",
    "wendigo": "terrifying Wendigo monster dark forest",
    "skinwalker": "terrifying skinwalker monster",
    "siren head": "giant Siren Head monster",
    "scp": "SCP terrifying monster containment",
    "backrooms": "Backrooms found footage monster",
    "anglerfish": "deep sea anglerfish glowing",
    "goblin shark": "deep sea goblin shark jaws",
}

_CREATURE_BONUS_CUES = (
    "movie", "movie scene", "movie still", "film", "vfx", "cgi", "8k", "4k", "photorealistic",
    "hyperrealistic", "imax", "underwater", "the meg", "godzilla", "jurassic", "kraken",
    "leviathan", "thalassophobia", "megalophobia", "colossal", "giant", "massive", "roaring",
    "terrifying", "dark", "deep sea", "ocean", "monster", "creature", "attack", "jaws",
)

_CREATURE_PENALTY_CUES = (
    "cartoon", "cute", "kawaii", "chibi", "toy", "plush", "lego", "figurine", "costume", "mask",
    "coloring", "logo", "icon", "meme", "chart", "diagram", "infographic", "sticker", "emoji",
    "clipart", "kids", "baby", "funny", "cosplay", "halloween decoration", "cake",
    "caught on camera", "spotted in real life", "top 10", "top 5", "reaction", "thumbnail",
    # Chặn mô hình cao su/nhựa bảo tàng ("trông đểu"), tranh khắc gỗ cổ, hóa thạch xương khô, game khối vuông
    "animatronic", "animatronics", "prop", "replica", "mannequin", "puppet", "wax figure",
    "minecraft", "roblox", "subnautica", "ark survival", "sketch", "pencil drawing",
    "fossil", "tooth", "teeth", "jawbone", "skeleton", "bone", "bones", "skull", "museum",
    "exhibit", "exhibition", "specimen", "preserved", "jar", "engraving", "woodcut", "stamp", "coin",
    "statue", "sculpture", "map", "book cover", "dvd cover", "washed ashore", "beached",
)

# Thứ tự ưu tiên nhận diện tên quái vật đặc trưng (ưu tiên tên riêng trước các từ chung như "tentacle", "monster")
_SPECIFIC_CREATURES = (
    "kraken", "leviathan", "megalodon", "cthulhu", "godzilla", "loch ness", "nessie",
    "bloop", "jormungandr", "charybdis", "scylla", "lusca", "tiamat", "ningen", "kaiju",
    "mothman", "wendigo", "chupacabra", "bigfoot", "sasquatch", "yeti", "skinwalker",
    "behemoth", "basilisk", "titanoboa", "mosasaurus", "liopleurodon", "dunkleosteus",
    "helicoprion", "livyatan", "sarcosuchus", "plesiosaur", "kronosaurus", "basilosaurus",
    "t rex", "tyrannosaurus", "spinosaurus", "indominus", "giganotosaurus", "velociraptor",
    "allosaurus", "dinosaur", "dragon", "hydra", "cerberus", "fenrir", "minotaur", "king kong",
    "siren head", "slenderman", "backrooms", "scp", "magnapinna", "bigfin squid",
    "colossal squid", "giant squid", "anglerfish", "goblin shark", "frilled shark",
    "vampire squid", "oarfish", "gulper eel", "viperfish", "sea serpent", "sea monster",
)


def is_creature_subject(subject: Optional[str], narration: str = "", topic: str = "") -> bool:
    """True nếu chủ thể hoặc ngữ cảnh là quái vật / thủy quái / khủng long / thực thể kinh dị."""
    import os
    if os.getenv("CREATURE_ART_MODE", "true").strip().lower() in ("0", "false", "no", "off"):
        return False
    subj = f" {_norm_text(subject or '')} "
    if any(f" {x} " in subj or x in subj for x in _REAL_ANIMAL_EXCEPTIONS):
        return False
    if any(f" {w} " in subj for w in _CREATURE_WORDS):
        return True
    ctx = f" {_norm_text(narration)} {_norm_text(topic)} "
    if any(f" {w} " in ctx for w in _CREATURE_WORDS):
        return True
    raw_ctx = f"{subject or ''} {narration or ''} {topic or ''}".lower()
    vi_horror_cues = (
        "quái vật", "thủy quái", "quái thú", "sinh vật biển sâu", "mực khổng lồ",
        "khủng long", "bạo chúa", "rồng", "rùng rợn", "kinh dị", "ác mộng",
        "vực thẳm", "huyền thoại biển", "người đi biển", "thần thoại",
    )
    return any(cue in raw_ctx for cue in vi_horror_cues)


def extract_core_creature(*texts: str) -> str:
    """Trích xuất tên quái vật/sinh vật cốt lõi từ subject, narration hoặc topic (ưu tiên tên riêng như kraken, leviathan, t rex)."""
    combined_norm = " " + " ".join(_norm_text(t) for t in texts if t) + " "
    if any(f" {x} " in combined_norm for x in _REAL_ANIMAL_EXCEPTIONS):
        return ""
    for c in _SPECIFIC_CREATURES:
        if f" {c} " in combined_norm:
            return "t-rex" if c == "t rex" else c
    raw_low = " ".join((t or "").lower() for t in texts if t)
    if any(k in raw_low for k in ("khủng long bạo chúa", "bạo chúa")):
        return "t-rex"
    if "khủng long" in raw_low:
        return "dinosaur"
    if any(k in raw_low for k in ("thủy quái", "mực khổng lồ", "bạch tuộc khổng lồ", "quái vật biển", "người đi biển")):
        return "kraken"
    if any(k in combined_norm for k in (" tentacle ", " tentacles ", " suction ", " ship ", " sailor ", " whirlpool ")):
        if any(k in raw_low for k in ("quái vật", "thần thoại", "biển", "đại dương", "ocean", "sea", "abyss")):
            return "kraken"
    for w in _CREATURE_WORDS:
        if f" {w} " in combined_norm and w not in ("monster", "monsters", "tentacle", "tentacles"):
            return "t-rex" if w == "t rex" else w
    return ""


def creature_queries(subject: str, topic: str = "", narration: str = "") -> List[str]:
    """
    Sinh bộ truy vấn TOP SEARCH GOOGLE IMAGES bám sát ĐÚNG tên quái vật/chủ thể mà người dùng tìm kiếm
    (không bao giờ tự ý đổi sang tên bộ phim của quái vật khác).
    """
    subject = (subject or "").strip()
    core = extract_core_creature(subject, narration, topic)
    norm_all = f" {_norm_text(subject)} {_norm_text(narration)} {_norm_text(topic)} "

    scene_focus_words = []
    focus_candidates = (
        "tentacles", "tentacle", "ship", "eye", "eyes", "jaws", "teeth", "whirlpool",
        "shipwreck", "submarine", "diver", "sailor", "storm", "abyss", "roaring", "glowing",
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
            "ship", "sailor", "biển", "đại dương", "thủy quái", "mariana", "vực",
        )
    )

    qs: List[str] = []
    # 1. Truy vấn Top Search Google Images trực diện đúng chủ thể
    ref = _CREATURE_FILM_REFS.get(core) or _CREATURE_FILM_REFS.get(core.replace("-", " "))
    if ref:
        qs.append(ref)

    if is_marine:
        candidates = [
            f"giant {base_creature} sea monster" if "monster" not in base_creature else f"giant {base_creature}",
            f"{base_creature} {focus_str} underwater monster".strip() if focus_str else f"{base_creature} attacking ship ocean",
            f"{base_creature}",
        ]
    else:
        candidates = [
            f"giant {base_creature} {focus_str} roaring".strip(),
            f"{base_creature} movie scene",
            f"{base_creature}",
        ]

    for cq in candidates:
        cq_clean = re.sub(r"\s+", " ", cq).strip()
        if cq_clean and cq_clean.lower() not in [x.lower() for x in qs]:
            qs.append(cq_clean)

    return qs[:4]


# ─────────────────────────────── Chế độ Tâm Lý Học Đen Tối / Seinen Noir (@MasteringMindControl) ───────────────────────────────
_PSYCHOLOGY_NOIR_CUES = (
    "psychology", "dark psychology", "mind control", "manipulation", "manipulator", "manipulators",
    "gaslight", "gaslighting", "narcissist", "narcissistic", "sociopath", "psychopath",
    "micro expression", "micro expressions", "silent treatment", "love bombing",
    "intermittent reinforcement", "pavlovian", "ben franklin effect", "isolation gambit",
    "emotional mirroring", "false choice", "pratfall effect", "cognitive dissonance",
    "tâm lý", "thao túng", "đọc vị", "ngôn ngữ cơ thể", "im lặng", "ái kỷ",
)

_PSYCHOLOGY_NOIR_BONUS_CUES = (
    "noir", "monochrome", "black and white", "johan", "liebert", "monster anime", "seinen",
    "psychological", "thriller", "shadow", "shadows", "silhouette", "chiaroscuro",
    "cold gaze", "intense eyes", "dark aesthetic", "cinematic", "mystery",
)

_CHEESY_CORPORATE_PENALTY_CUES = (
    "happy business", "office team", "cheerful", "thumbs up", "corporate meeting",
    "smiling coworker", "handshake deal", "customer service", "call center", "stock photo smile",
)


def is_psychology_noir_subject(subject: Optional[str], narration: str = "", topic: str = "", channel_niche: str = "") -> bool:
    """True nếu chủ đề, kênh hoặc phân cảnh thuộc Tâm lý học đen tối / Thao túng tâm lý / Đọc vị hành vi."""
    raw_ctx = f"{subject or ''} {narration or ''} {topic or ''} {channel_niche or ''}".lower()
    return any(cue in raw_ctx for cue in _PSYCHOLOGY_NOIR_CUES)


def psychology_noir_queries(subject: str, topic: str = "", narration: str = "") -> List[str]:
    """
    Sinh bộ truy vấn Google Images / Bing HD / Wallhaven chuẩn phong cách @MasteringMindControl
    (Dark Psychological Thriller / Monochrome Seinen Noir)
    bám sát hành động và cảm xúc cụ thể của từng câu thoại (KHÔNG chèn từ khóa học thuật nhiếp ảnh như 'chiaroscuro' làm lệch kết quả).
    """
    ctx = f"{subject or ''} {narration or ''} {topic or ''}".lower()
    qs: List[str] = []

    if any(k in ctx for k in ("silence", "silent", "argument", "confront", "enemy", "enemies", "ally", "allies", "favor", "distance", "im lặng")):
        qs.extend([
            "two people tense silence argument dark room",
            "johan liebert monster anime confrontation scene",
            "two people silent tension psychological thriller",
        ])
    elif any(k in ctx for k in ("smile", "smirk", "charm", "disguise", "familiar", "mirror", "love bomb", "friend", "kind", "compliment", "cười", "mặt nạ")):
        qs.extend([
            "narcissist fake smile cold gaze psychology",
            "johan liebert monster cold smile anime",
            "man subtle calculating smirk dark thriller",
        ])
    elif any(k in ctx for k in ("isolat", "shrink", "alone", "depend", "trap", "leash", "guilt", "guilty", "anxiet", "doubt", "cô lập", "bẫy")):
        qs.extend([
            "person sitting alone dark room anxiety",
            "seinen anime character alone in dark room",
            "gaslighting emotional manipulation dark psychology",
        ])
    else:
        qs.extend([
            "johan liebert monster intense cold eyes",
            "intense eye contact psychological thriller",
            "man cold unblinking stare dark room",
        ])

    clean_subj = " ".join(subject_terms(subject)[:4])
    if clean_subj and clean_subj not in ("person", "people", "man", "woman", "mind", "brain"):
        qs.insert(0, f"{clean_subj} psychological thriller")

    return list(dict.fromkeys(qs))[:4]


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

    # Bố cục: Cảnh phim điện ảnh thật (Movie Still / Film Frame) hầu hết là khung ngang 16:9 hoặc 2.39:1 CinemaScope!
    # Ảnh dọc trong kết quả tìm kiếm phim thường là Poster quảng cáo hoặc Wallpaper điện thoại nên không được ưu tiên hơn khung hình phim ngang.
    if w and h:
        ratio = w / h
        if 1.3 <= ratio <= 2.45 and min(w, h) >= 500:
            score += 1.5
        elif ratio <= 0.8:
            score += 0.6 if creature_mode else 1.2
        elif ratio < 1.3:
            score += 1.0
        elif ratio > 2.45:
            score -= 0.8
        short_side = min(w, h)
        if short_side >= 1000:
            score += 1.5
        elif short_side >= 680:
            score += 1.1
        elif short_side >= 500:
            score += 0.6
        else:
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
        score += 3.8 * frac
        if hits == 0 and text:
            score -= 1.5

    src = (item.get("source") or "").lower()
    blob_domain = f"{item.get('creator') or ''} {item.get('page_url') or ''} {item.get('url') or ''}".lower()
    padded = f" {text} "

    # Phạt ảnh thumbnail YouTube (ytimg.com) vì 90% có chữ clickbait đè giữa ảnh, và phạt bài hướng dẫn chụp ảnh / prompt catalog
    if "ytimg.com" in blob_domain or "youtube.com/watch" in blob_domain:
        score -= 1.8
    if any(tut in padded for tut in (" portrait photography ", " photography tips ", " how to shoot ", " promptden ", " lighting tutorial ", " camera settings ")):
        score -= 2.2

    # Thưởng điểm mạnh cho thứ hạng TOP SEARCH GOOGLE IMAGES (_google_rank từ 1..25)
    g_rank = int(item.get("_google_rank") or 999)
    if item.get("provider") == "google" or src == "google_images":
        if g_rank <= 3:
            score += 2.6
        elif g_rank <= 8:
            score += 2.0
        elif g_rank <= 15:
            score += 1.4
        elif g_rank <= 25:
            score += 0.8

    # Thưởng điểm cho nguồn Hot-Trend / Điện ảnh / Viral (ArtStation, Pinterest, Reddit, Wallpaper 8K, Movie Stills)
    is_hot_domain = any(dom in blob_domain for dom in _HOT_TREND_DOMAINS)
    hot_cue_hits = sum(1 for c in _HOT_TREND_CUES if f" {c} " in padded)
    epic_cue_hits = sum(1 for c in _CREATURE_BONUS_CUES if f" {c} " in padded)
    noir_cue_hits = sum(1 for c in _PSYCHOLOGY_NOIR_BONUS_CUES if f" {c} " in padded)

    if creature_mode:
        # Chế độ Quái Vật / Kinh Dị: Ưu tiên tuyệt đối Top Search Google Images
        if src == "google_images":
            score += 1.5
        elif src == "open_web":
            score += 0.8
        if is_hot_domain:
            score += 1.4
        # Trừ điểm Wikipedia/Wikimedia/Bảo tàng trong creature_mode vì hay trả về tranh khắc gỗ cũ hoặc hóa thạch răng
        if src in ("wikipedia", "wikimedia_commons", "openverse") or any(
            d in blob_domain for d in ("wikimedia.org", "wikipedia.org", "nhm.ac.uk", "smithsonian", "fishbase", "inaturalist")
        ):
            score -= 2.2
        score += min(2.2, 0.65 * epic_cue_hits)
        if any(f" {c} " in padded for c in _CREATURE_PENALTY_CUES):
            score = min(score - 5.5, 3.5)
    else:
        if src == "google_images":
            score += 1.5
        elif src == "open_web":
            score += 0.8
        elif src in ("pexels", "pexels_photo", "pixabay", "unsplash"):
            score += 0.3
        elif src in ("wikipedia", "wikimedia_commons", "openverse"):
            score -= 0.6

        # Chỉ thưởng điểm style/domain khi ảnh thực sự khớp ít nhất 1 từ khóa chủ thể (tránh ảnh lạc đề chỉ khớp từ khóa style)
        has_subject_match = (frac > 0.0) or (not terms)
        if is_hot_domain and has_subject_match:
            score += 1.2
        if hot_cue_hits > 0 and has_subject_match:
            score += min(1.3, 0.45 * hot_cue_hits)
        if epic_cue_hits > 0 and has_subject_match:
            score += min(1.0, 0.35 * epic_cue_hits)
        if noir_cue_hits > 0 and has_subject_match:
            score += min(1.2, 0.45 * noir_cue_hits)
        if any(dom in blob_domain for dom in _AUTHORITATIVE_DOMAINS):
            score += 0.5
        if any(f" {c} " in padded for c in _CHEESY_CORPORATE_PENALTY_CUES):
            score -= 2.5
        if any(f" {c} " in padded for c in _SOFT_TITLE_CUES):
            score -= 2.0

    return round(max(0.0, min(10.0, score)), 2)


def prefilter_candidates(
    candidates: List[Dict],
    subject: Optional[str] = None,
    limit: int = 12,
    max_per_source: int = 12,
    creature_mode: bool = False,
) -> List[Dict]:
    """
    Loại ảnh watermark/quá nhỏ, chấm điểm ưu tiên TOP SEARCH GOOGLE IMAGES rồi sắp xếp giảm dần.
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

    kept.sort(key=lambda x: (-x["_hscore"], int(x.get("_google_rank") or 999), x["_order"]))

    out: List[Dict] = []
    per_src: Dict[str, int] = {}
    per_domain: Dict[str, int] = {}
    overflow: List[Dict] = []
    for it in kept:
        src = it.get("source") or "?"
        dom = (it.get("creator") or it.get("provider") or src).lower()
        if per_src.get(src, 0) >= max_per_source or per_domain.get(dom, 0) >= 4:
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


def prepare_vertical(im, target_w: int, target_h: int, max_upscale: float = 3.8, is_hook_scene: bool = False):
    """
    Đưa ảnh bất kỳ về khung dọc 9:16 (target_w×target_h) FULL MÀN HÌNH KHÔNG KÉO GIÃN:
    - Tỷ lệ gần đúng → cover-crop chính giữa.
    - Ảnh cao hơn 9:16 → cover-crop dọc bám theo vùng chi tiết/khuôn mặt (_saliency_center_y).
    - Ảnh ngang (chiều cao >= 420px và tỷ lệ <= 2.15) → LUÔN cover-crop full màn hình 9:16 bám theo vùng chủ thể (_saliency_center_x) + làm nét Lanczos, triệt tiêu hoàn toàn 2 dải viền xám mờ!
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

    # Ảnh rộng hơn 9:16: Ưu tiên tuyệt đối crop dọc full màn hình 9:16 bám theo chủ thể (saliency)
    crop_w = max(1, int(h * target_ratio))
    upscale = target_h / max(1, h)
    if upscale <= max(max_upscale, 3.8) or (h >= 420 and src_ratio <= 2.15) or src_ratio < 1.0:
        cx = _saliency_center_x(im, crop_w)
        left = min(max(0, cx - crop_w // 2), max(0, w - crop_w))
        return _enhance_visual_clarity(
            im.crop((left, 0, left + crop_w, h)).resize((target_w, target_h), Image.LANCZOS),
            is_hook_scene=is_hook_scene,
        )

    # Chỉ dùng nền mờ khi ảnh thực sự là dải banner siêu dẹt (src_ratio > 2.15) và quá bé (h < 420px)
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
