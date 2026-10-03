"""
app.services.media.query_broadener — Thang truy vấn stock (Query Ladder) cho chủ đề thiên nhiên / địa danh thật.

Vấn đề: kho stock miễn phí (Pexels / Pixabay) gần như không có loài hoặc địa danh hiếm
("black caiman amazon river" -> 0 kết quả), nhưng lại có rất nhiều clip của:
  - Nhóm loài gần giống:   caiman  -> crocodile / alligator
  - Bối cảnh / quần xã:     Amazon  -> "amazon rainforest aerial", "jungle river"

Module này dựng bậc thang truy vấn theo 3 nấc:
  1. specific      : truy vấn gốc (đã làm sạch)
  2. broadened     : thay loài/địa danh hiếm bằng từ đồng nghĩa phổ biến trên kho stock
  3. establishing  : cảnh toàn đúng bối cảnh (rừng mưa, sông rừng rậm, sa mạc...) — luôn đúng chủ đề,
                     dùng làm phương án dự phòng TRƯỚC khi phải nhờ AI vẽ ảnh.
"""
import re
from typing import Dict, List, Tuple

# ── Loài / đối tượng hiếm -> từ khóa phổ biến trên kho stock (ưu tiên từ trái sang phải) ──
SUBJECT_SYNONYMS: Dict[str, List[str]] = {
    # Amazon & rừng mưa nhiệt đới
    "black caiman": ["crocodile", "alligator"],
    "caiman": ["crocodile", "alligator"],
    "cá sấu": ["crocodile", "alligator"],
    "green anaconda": ["anaconda", "python snake"],
    "anaconda": ["anaconda", "python snake", "snake"],
    "trăn": ["python snake", "snake"],
    "piranha": ["piranha", "fish underwater"],
    "cá piranha": ["piranha", "fish underwater"],
    "jaguar": ["jaguar", "leopard"],
    "báo đốm": ["jaguar", "leopard"],
    "poison dart frog": ["poison frog", "tree frog"],
    "ếch phi tiêu": ["poison frog", "tree frog"],
    "electric eel": ["eel", "fish underwater"],
    "lươn điện": ["eel", "fish underwater"],
    "arapaima": ["giant fish", "fish underwater"],
    "pink river dolphin": ["dolphin"],
    "river dolphin": ["dolphin"],
    "giant otter": ["otter"],
    "harpy eagle": ["eagle"],
    "tarantula": ["tarantula", "spider"],
    "bullet ant": ["ants", "ant macro"],
    "army ant": ["ants", "ant colony"],
    "kiến": ["ants", "ant macro"],
    "macaw": ["macaw", "parrot"],
    "toucan": ["toucan", "tropical bird"],
    "sloth": ["sloth"],
    "capybara": ["capybara"],
    "vampire bat": ["bat", "bats flying"],
    "dơi": ["bat", "bats flying"],
    # Biển sâu
    "giant squid": ["squid", "octopus underwater"],
    "colossal squid": ["squid", "octopus underwater"],
    "anglerfish": ["deep sea fish", "jellyfish dark"],
    "cá lồng đèn": ["deep sea fish", "jellyfish dark"],
    "megalodon": ["great white shark", "shark"],
    "great white": ["great white shark", "shark"],
    "orca": ["orca", "killer whale"],
    # Thú lớn khác
    "komodo dragon": ["komodo dragon", "monitor lizard"],
    "rồng komodo": ["komodo dragon", "monitor lizard"],
    "grizzly": ["grizzly bear", "brown bear"],
    "polar bear": ["polar bear"],
    "gấu bắc cực": ["polar bear"],
    "hippo": ["hippo", "hippopotamus"],
    "hà mã": ["hippo", "hippopotamus"],
}

# ── Quần xã / bối cảnh: (khóa, từ kích hoạt, truy vấn cảnh toàn) ──
BIOMES: List[Tuple[str, List[str], List[str]]] = [
    (
        "amazon_rainforest",
        ["amazon", "rainforest", "rain forest", "jungle", "rừng mưa", "rừng nhiệt đới", "rừng rậm",
         "rừng amazon", "nhiệt đới", "tropical forest", "congo", "borneo"],
        ["amazon rainforest aerial", "jungle river aerial", "tropical rainforest", "rainforest canopy",
         "dense jungle", "jungle river"],
    ),
    (
        "river_swamp",
        ["murky river", "muddy river", "swamp", "marsh", "đầm lầy", "sông đục", "dòng sông", "bayou"],
        ["murky river", "jungle river", "swamp water", "river surface"],
    ),
    (
        "deep_ocean",
        ["deep sea", "deep ocean", "ocean floor", "abyss", "mariana", "trench", "biển sâu", "đáy biển",
         "đại dương", "rãnh"],
        ["deep sea underwater", "dark ocean underwater", "underwater light rays", "ocean depth"],
    ),
    (
        "desert",
        ["desert", "sahara", "dune", "sa mạc", "cồn cát", "gobi", "atacama"],
        ["desert dunes aerial", "sahara desert", "sand dunes"],
    ),
    (
        "polar_ice",
        ["antarctica", "arctic", "glacier", "iceberg", "nam cực", "bắc cực", "băng tuyết", "sông băng"],
        ["antarctica ice", "glacier aerial", "arctic ice", "iceberg ocean"],
    ),
    (
        "volcano",
        ["volcano", "lava", "magma", "eruption", "núi lửa", "dung nham", "phun trào"],
        ["volcano eruption", "lava flow", "volcano aerial"],
    ),
    (
        "cave",
        ["cave", "cavern", "underground", "hang động", "hang", "son doong"],
        ["cave interior", "dark cave", "underground cave"],
    ),
    (
        "savanna",
        ["savanna", "savannah", "serengeti", "safari", "thảo nguyên", "châu phi", "africa"],
        ["african savanna", "savanna wildlife", "savanna sunset"],
    ),
    (
        "mountain",
        ["himalaya", "everest", "mountain", "núi", "dãy núi", "alps", "andes"],
        ["mountain peak aerial", "snowy mountains", "mountain clouds"],
    ),
]

# Từ đơn mơ hồ: tìm riêng lẻ trên kho stock sẽ ra clip ngẫu nhiên, lạc đề
VAGUE_SINGLE_WORDS = {
    "black", "white", "dark", "light", "big", "giant", "massive", "huge", "small", "deep", "murky",
    "swimming", "running", "hunting", "shadow", "water", "close", "macro", "aerial", "cinematic",
    "animation", "silent", "terrifying", "ancient", "mysterious", "secret", "monster", "predator",
    "prey", "danger", "dangerous", "scary", "glowing", "real", "footage", "shot", "view", "scene",
    "amazon", "nile", "congo", "china", "egypt",
}


def _contains(text: str, kw: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(kw)}(?![\w])", text, re.IGNORECASE) is not None


def detect_biomes(*texts: str) -> List[str]:
    """Trả về danh sách khóa quần xã xuất hiện trong văn bản (nhiều lượt khớp nhất đứng trước)."""
    combined = " ".join(t for t in texts if t).lower()
    if not combined.strip():
        return []
    scored = []
    for key, triggers, _ in BIOMES:
        hits = sum(1 for t in triggers if _contains(combined, t))
        if hits:
            scored.append((hits, key))
    scored.sort(key=lambda x: -x[0])
    return [k for _, k in scored]


def establishing_queries(*texts: str, limit: int = 6) -> List[str]:
    """Truy vấn cảnh toàn đúng bối cảnh cho chủ đề (ví dụ Amazon -> 'amazon rainforest aerial')."""
    out: List[str] = []
    biome_map = {k: qs for k, _, qs in BIOMES}
    for key in detect_biomes(*texts):
        for q in biome_map.get(key, []):
            if q not in out:
                out.append(q)
    return out[:limit]


def broaden_subject(query: str, context: str = "") -> List[str]:
    """
    Thay loài / đối tượng hiếm bằng từ phổ biến trên kho stock, giữ lại 1 từ bối cảnh nếu có.
    'black caiman amazon river' -> ['crocodile river', 'alligator river', 'crocodile', 'alligator']
    """
    text = f"{query} {context}".lower()
    out: List[str] = []
    for key in sorted(SUBJECT_SYNONYMS, key=len, reverse=True):
        if not _contains(text, key):
            continue
        # Từ bối cảnh còn lại trong query (river / water / jungle...) giúp clip đúng không khí hơn
        rest = re.sub(rf"(?<![\w]){re.escape(key)}(?![\w])", " ", query.lower())
        ctx_words = [w for w in re.findall(r"[a-z]+", rest)
                     if w in ("river", "water", "underwater", "jungle", "swamp", "forest", "ocean", "tree", "night")]
        ctx = ctx_words[0] if ctx_words else ""
        for syn in SUBJECT_SYNONYMS[key]:
            for cand in ([f"{syn} {ctx}"] if ctx and ctx not in syn else []) + [syn]:
                if cand not in out:
                    out.append(cand)
        if len(out) >= 4:
            break
    return out[:4]


def build_query_ladder(query: str, narration: str = "", topic: str = "") -> Dict[str, List[str]]:
    """
    Dựng bậc thang truy vấn: specific -> broadened -> establishing.
    Không bao giờ trả về từ đơn mơ hồ (black / swimming / dark...).
    """
    q = (query or "").strip()
    specific = [q] if q else []
    broadened = broaden_subject(q, narration)
    # Cảnh toàn: ưu tiên bối cảnh của TOPIC (chủ đề video), sau đó mới đến lời thoại / query
    establishing = establishing_queries(topic, q, narration)
    return {
        "specific": specific,
        "broadened": [b for b in broadened if b not in specific],
        "establishing": [e for e in establishing if e not in specific and e not in broadened],
    }


def is_real_world_nature_topic(topic: str = "", narration: str = "") -> bool:
    """Chủ đề thiên nhiên / địa danh có thật -> nên ưu tiên footage thật thay vì ảnh AI tự vẽ."""
    return bool(detect_biomes(topic, narration))


def is_vague_single_word(word: str) -> bool:
    w = (word or "").strip().lower()
    return len(w.split()) == 1 and (w in VAGUE_SINGLE_WORDS or len(w) <= 3)


# ── Từ khóa xung đột quần xã: clip chứa các từ này chắc chắn sai bối cảnh của chủ đề ──
# (ví dụ video sông rừng Amazon nhưng chọn clip "sharks swimming in blue ocean")
BIOME_CONFLICTS: Dict[str, List[str]] = {
    "amazon_rainforest": ["ocean", "sea", "coral", "reef", "beach", "desert", "snow", "ice", "arctic",
                          "savanna", "city", "office", "aquarium", "zoo", "pool"],
    "river_swamp": ["ocean", "coral", "reef", "beach", "desert", "snow", "aquarium", "pool"],
    "deep_ocean": ["river", "lake", "forest", "jungle", "desert", "city", "pool", "aquarium"],
    "desert": ["ocean", "underwater", "forest", "jungle", "snow", "ice"],
    "polar_ice": ["desert", "jungle", "tropical", "beach", "palm"],
    "volcano": ["snow", "underwater", "aquarium"],
    "cave": ["beach", "city", "office"],
    "savanna": ["ocean", "underwater", "snow", "ice", "jungle"],
    "mountain": ["underwater", "beach", "aquarium"],
}


def conflicts_with_topic_biome(media_desc: str, topic: str = "") -> bool:
    """True nếu mô tả clip/ảnh mang dấu hiệu quần xã KHÁC hẳn quần xã chính của chủ đề."""
    if not media_desc or not topic:
        return False
    biomes = detect_biomes(topic)
    if not biomes:
        return False
    desc = re.sub(r"[-_/]+", " ", media_desc.lower())
    # Chỉ xét quần xã CHÍNH; nếu chủ đề tự nhắc tới từ đó (vd 'Amazon ocean dolphins') thì không chặn
    topic_l = topic.lower()
    return any(_contains(desc, w) and not _contains(topic_l, w) for w in BIOME_CONFLICTS.get(biomes[0], []))
