"""
app.agents.wildlife_visual_dispatcher_agent — Tác nhân Phân Phối & Điều Phối Thị Giác Chuyên Biệt Cho Video Động Vật & Thiên Nhiên (Wildlife & Nature Visual Dispatcher Agent).

Nhiệm vụ cốt lõi:
1. Nhận diện chuyên sâu các đề tài Động vật hoang dã, Chim muông, Thú săn mồi đỉnh cao, Đại dương & Cứu hộ động vật (BBC Earth & National Geographic Standards).
2. Thanh lọc triệt để các từ khóa ẩn dụ rác (ví dụ: chim nuôi con nhưng sinh ra mắt người 'human eye', chuỗi xoắn ADN 'dna helix', văn phòng, con người).
3. Tuyển chọn nguồn Video Clip Pexels 9:16 chất lượng cao 100% động vật thực tế trong môi trường tự nhiên (loại bỏ video có người cho ăn, lồng sở thú).
4. Chuẩn hóa Master Prompt AI phong cách National Geographic 8K / BBC Planet Earth (ánh sáng hoàng hôn golden hour, lens tele 400mm-600mm, chi tiết giải phẫu lông mao/móng vuốt sắc nét) khi tạo ảnh hoặc sinh video AI.
"""
import re
import logging
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# ── BỘ TỪ ĐIỂN PHÂN LOẠI THỂ LOẠI ĐỘNG VẬT & THIÊN NHIÊN ──
WILDLIFE_SUBGENRES: Dict[str, List[str]] = {
    "birds_and_aerial": [
        "chim", "chim non", "tổ chim", "chim mẹ", "kiếm ăn", "nuôi con", "đại bàng", "diều hâu",
        "cú mèo", "chim ưng", "chim bói cá", "chim ruồi", "hải âu", "vẹt", "bồ nông", "chim lặn",
        "lông vũ", "sải cánh", "đập cánh", "chim bay", "hót",
        "bird", "birds", "nest", "baby birds", "chicks", "mother bird", "feeding chicks",
        "eagle", "hawk", "owl", "falcon", "kingfisher", "hummingbird", "seagull", "parrot",
        "wings outstretched", "plumage", "feathers", "bird flying"
    ],
    "apex_predators_big_cats": [
        "báo", "báo hoa mai", "báo săn", "cheetah", "leopard", "sư tử", "lion", "hổ", "tiger",
        "sói", "wolf", "chó sói", "gấu", "bear", "cá sấu", "crocodile", "alligator", "rắn", "snake",
        "săn mồi", "predator", "mãnh thú", "vồ mồi", "cắn xé", "lực cắn", "móng vuốt", "rượt đuổi",
        "tốc độ săn mồi", "sát thủ", "đỉnh cao chuỗi thức ăn", "con mồi", "linh cẩu", "hyena",
        "stalking", "hunting", "bite force", "claws", "apex predator", "jaws", "feline", "carnivore"
    ],
    "ocean_marine_wildlife": [
        "cá voi", "whale", "cá heo", "dolphin", "cá mập", "shark", "bạch tuộc", "octopus",
        "mực khổng lồ", "giant squid", "rùa biển", "sea turtle", "san hô", "coral reef",
        "chim cánh cụt", "penguin", "hải cẩu", "seal", "sư tử biển", "sea lion", "đáy biển",
        "đại dương", "biển sâu", "deep sea", "marine life", "orca", "cá voi sát thủ"
    ],
    "african_savanna_safari": [
        "voi", "elephant", "hươu cao cổ", "giraffe", "ngựa vằn", "zebra", "hà mã", "hippo",
        "tê giác", "rhino", "trâu rừng", "buffalo", "linh dương", "antelope", "gazelle",
        "savanna", "thảo nguyên", "châu phi", "safari", "đàn thú", "bầy đàn", "hoang dã",
        "serengeti", "maasai mara", "wildlife habitat"
    ],
    "animal_rescue_pets": [
        "cứu hộ", "giải cứu", "chó hoang", "mèo hoang", "bị bỏ rơi", "stray dog", "stray cat",
        "rescue", "abandoned", "chó con", "mèo con", "puppy", "kitten", "chữa lành", "yêu thương",
        "thú cưng", "pet rescue", "shelter", "trạm cứu hộ"
    ]
}

# ── KHO TỪ KHÓA TÌM VIDEO PEXELS 9:16 ĐỘNG VẬT HOANG DÃ CHUẨN XÁC ──
CURATED_WILDLIFE_STOCK_QUERIES: Dict[str, List[str]] = {
    "birds_and_aerial": [
        "baby birds in nest",
        "mother bird feeding chicks",
        "bird perched on tree branch",
        "eagle soaring mountain sky",
        "hawk flying across forest",
        "wild birds in nature",
        "bird wings flying slow motion"
    ],
    "apex_predators_big_cats": [
        "leopard running savanna",
        "cheetah running full speed",
        "wild leopard in tree",
        "lion walking in savanna",
        "wolf running in snow",
        "tiger in forest stream",
        "predator wild animal hunting"
    ],
    "ocean_marine_wildlife": [
        "humpback whale swimming ocean",
        "dolphins underwater ocean",
        "great white shark swimming",
        "sea turtle swimming coral reef",
        "marine life underwater vertical",
        "ocean waves deep blue"
    ],
    "african_savanna_safari": [
        "elephants walking african savanna",
        "giraffes walking in savanna",
        "zebras running in wild savanna",
        "wild animals in nature africa",
        "african wildlife safari vertical"
    ],
    "animal_rescue_pets": [
        "rescued puppy in grass",
        "golden retriever puppy smiling",
        "cute kitten purring warm light",
        "stray dog happy running meadow",
        "caring for small puppy"
    ]
}


class WildlifeVisualDispatcherAgent:
    """Tác nhân phân loại, điều phối và đảm bảo chất lượng thị giác cho chủ đề Động vật & Thiên nhiên."""

    def __init__(self):
        self.subgenres = WILDLIFE_SUBGENRES
        self.curated_queries = CURATED_WILDLIFE_STOCK_QUERIES

    def detect_wildlife_elements(
        self,
        topic: str = "",
        narration: str = "",
        visual_keyword: str = "",
        visual_prompt: str = "",
        channel_niche: str = "",
    ) -> Tuple[bool, float, str, List[str]]:
        """
        Nhận diện xem ngữ cảnh có thuộc đề tài Động vật hoang dã / Thiên nhiên không.
        Trả về: (is_wildlife, confidence_score, subgenre, detected_keywords)
        """
        combined_text = f"{topic} {narration} {visual_keyword} {visual_prompt} {channel_niche}".lower()
        detected_keywords: List[str] = []
        genre_scores: Dict[str, int] = {k: 0 for k in self.subgenres}

        for subgenre, kw_list in self.subgenres.items():
            for kw in kw_list:
                pattern = rf"(?:\b|\s|^){re.escape(kw)}(?:\b|\s|$)"
                if re.search(pattern, combined_text, re.IGNORECASE):
                    detected_keywords.append(kw)
                    genre_scores[subgenre] += 1

        total_hits = len(detected_keywords)
        if total_hits == 0:
            if channel_niche in ("wildlife_predators", "animal_rescue", "wildlife", "nature"):
                return True, 0.8, "apex_predators_big_cats", ["channel_niche_fallback"]
            return False, 0.0, "none", []

        best_subgenre = max(genre_scores, key=lambda k: genre_scores[k])
        confidence = min(0.5 + (total_hits * 0.15), 1.0)
        return True, round(confidence, 2), best_subgenre, detected_keywords[:8]

    def get_curated_video_search_queries(self, subgenre: str = "birds_and_aerial", narration: str = "") -> List[str]:
        """Lấy danh sách từ khóa tiếng Anh thuần động vật để tìm kiếm kho Pexels 9:16."""
        queries = list(self.curated_queries.get(subgenre, self.curated_queries["birds_and_aerial"]))
        n_lower = narration.lower()

        # Tinh chỉnh query theo ngữ cảnh phân cảnh
        if any(w in n_lower for w in ["chim non", "tổ", "con non", "chick", "nest", "baby"]):
            queries.insert(0, "baby birds in nest")
            queries.insert(1, "mother bird feeding chicks")
        elif any(w in n_lower for w in ["chạy", "tốc độ", "bứt phá", "săn", "running", "chase", "sprint"]):
            queries.insert(0, "cheetah running full speed")
            queries.insert(1, "leopard running savanna")
        elif any(w in n_lower for w in ["mắt", "ánh mắt", "nhìn", "eyes", "stare"]):
            queries.insert(0, "wild animal eyes close up")
            queries.insert(1, "bird perched tree branch")
        elif any(w in n_lower for w in ["bay", "sải cánh", "lượn", "flying", "soaring"]):
            queries.insert(0, "eagle soaring mountain sky")
            queries.insert(1, "wild birds flying across sky")

        return queries

    def sanitize_animal_query(self, query: str, subgenre: str = "birds_and_aerial") -> str:
        """
        Khử toàn bộ các từ khóa ẩn dụ rác (human eye, dna helix, office, laboratory)
        và thay thế bằng từ khóa động vật hoang dã chuẩn xác 100%.
        """
        q_lower = (query or "").lower().strip()

        # Danh sách dịch các cụm từ tiếng Việt sang tiếng Anh điện ảnh chuyên ngành
        vi_animal_mappings = [
            (r"(?:chim mẹ|chim).*?(?:mớm mồi|kiếm ăn|nuôi con|tổ chim|chim non|các con)", "mother bird feeding hungry chicks in nest"),
            (r"chim non|con non", "baby birds opening beaks inside nest"),
            (r"chim bay|sải cánh|đại bàng", "majestic wild eagle soaring across sky"),
            (r"chim|lông vũ", "wild bird perched on forest tree branch"),
            (r"báo hoa mai|báo.*?săn mồi|bứt tốc|tốc độ săn mồi", "wild leopard sprinting at high speed across savanna"),
            (r"báo|cheetah|leopard", "majestic leopard in African savanna"),
            (r"sư tử", "wild lion pride in savanna grass"),
            (r"hổ|cọp", "tiger moving through jungle stream"),
            (r"sói", "wolf running through snow wilderness"),
            (r"cá voi", "humpback whale breaching turquoise ocean surface"),
            (r"cá heo", "dolphins swimming in crystal clear ocean"),
            (r"cá mập", "great white shark swimming deep water"),
            (r"chó.*?cứu hộ|chó.*?bỏ rơi", "rescued puppy running happily in sunlit meadow"),
            (r"mèo", "cute kitten resting in soft sunlight"),
        ]

        for vi_pat, vi_repl in vi_animal_mappings:
            if re.search(vi_pat, q_lower):
                return vi_repl

        # Danh sách lỗi ẩn dụ phổ biến từ LLM
        incongruent_patterns = [
            (r"\bhuman\b|\beye\b|\bpupil\b|\biris\b", "bird eye close up" if subgenre == "birds_and_aerial" else "wild animal eyes close up"),
            (r"\bdna\b|\bhelix\b|\bchromosome\b|\bgene\b", "baby birds in nest" if subgenre == "birds_and_aerial" else "wild animals in nature"),
            (r"\boffice\b|\bcorporate\b|\blaptop\b|\bbusiness\b", "mother bird flying forest" if subgenre == "birds_and_aerial" else "wildlife running savanna"),
            (r"\blaboratory\b|\btest tube\b|\bhospital\b", "wild birds nest tree" if subgenre == "birds_and_aerial" else "wildlife habitat"),
            (r"\btired bird\b", "wild bird perched branch forest"),
            (r"\bbird feeding\b", "mother bird feeding chicks in nest"),
        ]

        for pat, repl in incongruent_patterns:
            if re.search(pat, q_lower):
                logger.info(f"🌿 [WildlifeDispatcher] Đã thanh lọc từ khóa rác '{query}' -> Chuẩn hóa thành '{repl}'")
                return repl

        return query or ("wild birds in nature" if subgenre == "birds_and_aerial" else "wild animal savanna")

    def build_cinematic_wildlife_prompt(
        self,
        raw_prompt: str,
        subgenre: str = "birds_and_aerial",
        scene_id: int = 1,
    ) -> str:
        """
        Tạo Master AI Prompt đẳng cấp National Geographic / BBC Earth 8K:
        - Đặc tả giải phẫu sống động (lông vũ, ánh mắt dã thú, móng vuốt, giọt sương).
        - Ánh sáng hoàng hôn tự nhiên (golden hour dust, rim light).
        - Phủ định tuyệt đối: CẤM người, CẤM lồng sắt sở thú, CẤM vòng cổ, CẤM hoạt hình 3D.
        """
        sanitized = self.sanitize_animal_query(raw_prompt, subgenre=subgenre)

        if subgenre == "birds_and_aerial":
            prefix = "Award-winning National Geographic wildlife photograph, vertical 8k film still, "
            core = f"majestic wild {sanitized}, razor-sharp feather detail, glistening intense eyes, natural forest canopy background, soft morning sunlight bokeh"
            suffix = ", 600mm telephoto lens, hyper-realistic animal anatomy, photorealistic vertical 9:16, no humans, no cage, no zoo, no watermark, no text"
        elif subgenre == "apex_predators_big_cats":
            prefix = "BBC Planet Earth epic wildlife documentary vertical 8k film still, "
            core = f"intense apex predator {sanitized} in dynamic motion, powerful tensed muscles, amber predatory eyes, wild savanna dust particles catching golden hour rim lighting"
            suffix = ", 400mm lens, razor-sharp fur texture, hyper-realistic, vertical 9:16 composition, no humans, no collar, no cage, no 3d render, no text"
        elif subgenre == "ocean_marine_wildlife":
            prefix = "National Geographic underwater marine documentary vertical 8k, "
            core = f"majestic {sanitized} swimming through crystal clear deep turquoise ocean, sun rays refracting through water surface, glistening skin texture"
            suffix = ", underwater wide-angle cinema lens, photorealistic vertical 9:16, no text, no watermark"
        elif subgenre == "animal_rescue_pets":
            prefix = "Heartwarming animal documentary vertical 8k portrait, "
            core = f"tender emotional scene of {sanitized}, soulful shining eyes reflecting golden sunlight, soft realistic fur texture, gentle sunlit grass meadow"
            suffix = ", 50mm f/1.4 shallow depth of field, photorealistic 9:16, no text, no watermark"
        else:
            prefix = "Award-winning National Geographic wildlife photography vertical 8k, "
            core = f"wild animals {sanitized} in natural pristine wilderness, dramatic atmospheric lighting, authentic animal behavior"
            suffix = ", telephoto lens, photorealistic vertical 9:16, no humans, no cage, no watermark"

        return f"{prefix}{core}{suffix}"
