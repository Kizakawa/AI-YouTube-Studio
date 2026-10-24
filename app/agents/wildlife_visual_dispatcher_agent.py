"""
app.agents.wildlife_visual_dispatcher_agent — Tác nhân Phân Phối & Điều Phối Thị Giác Chuyên Biệt Cho Video Động Vật & Thiên Nhiên.

Nhiệm vụ cốt lõi:
1. Nhận diện đề tài Động vật hoang dã & Thiên nhiên (BBC Earth & National Geographic Standards).
2. Tôn trọng tuyệt đối chủ thể loài vật cụ thể trong kịch bản của người dùng (KHÔNG ghi đè bằng các từ khóa đóng hộp như 'baby birds' hay 'cheetah' nếu kịch bản đang nói về loài vật khác).
3. Chỉ lọc bỏ các từ khóa ẩn dụ lạc đề (human office, laptop, test tube) và chuẩn hóa prompt nhiếp ảnh thiên nhiên 8K.
"""
import re
import logging
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)

WILDLIFE_SUBGENRES: Dict[str, List[str]] = {
    "birds_and_aerial": [
        "chim", "chim non", "tổ chim", "đại bàng", "diều hâu", "cú mèo", "chim ưng", "chim bói cá",
        "chim ruồi", "hải âu", "vẹt", "bồ nông", "lông vũ", "sải cánh",
        "bird", "birds", "nest", "eagle", "hawk", "owl", "falcon", "kingfisher", "hummingbird",
        "seagull", "parrot", "wings", "plumage", "feathers",
    ],
    "apex_predators_big_cats": [
        "báo", "báo hoa mai", "báo săn", "cheetah", "leopard", "sư tử", "lion", "hổ", "tiger",
        "sói", "wolf", "gấu", "bear", "cá sấu", "crocodile", "alligator", "caiman", "jaguar",
        "rắn", "snake", "cobra", "anaconda", "badger", "honey badger", "otter", "ariranha",
        "predator", "linh cẩu", "hyena", "stalking", "hunting", "apex predator", "carnivore",
    ],
    "ocean_marine_wildlife": [
        "cá voi", "whale", "cá heo", "dolphin", "cá mập", "shark", "bạch tuộc", "octopus",
        "rùa biển", "sea turtle", "san hô", "coral reef", "chim cánh cụt", "penguin",
        "hải cẩu", "seal", "sư tử biển", "sea lion", "marine life", "orca",
    ],
    "african_savanna_safari": [
        "voi", "elephant", "hươu cao cổ", "giraffe", "ngựa vằn", "zebra", "hà mã", "hippo",
        "tê giác", "rhino", "trâu rừng", "buffalo", "linh dương", "antelope", "gazelle",
        "savanna", "thảo nguyên", "safari", "serengeti", "wildlife habitat",
    ],
    "animal_rescue_pets": [
        "cứu hộ", "giải cứu", "chó hoang", "mèo hoang", "stray dog", "stray cat",
        "rescue", "abandoned", "puppy", "kitten", "pet rescue", "shelter",
    ],
}

MONSTER_AND_HORROR_EXCLUSIONS: Tuple[str, ...] = (
    "kraken", "megalodon", "leviathan", "cthulhu", "monster", "monsters", "quái vật", "thủy quái",
    "quái thú", "sea monster", "sea serpent", "giant squid", "colossal squid", "mực khổng lồ",
    "magnapinna", "bigfin squid", "loch ness", "nessie", "bloop", "the bloop", "titanoboa",
    "mosasaurus", "liopleurodon", "dunkleosteus", "helicoprion", "livyatan", "plesiosaur",
    "thalassophobia", "megalophobia", "abyss", "abyssal", "vực thẳm", "rãnh mariana",
    "mariana trench", "point nemo", "horror", "terrifying", "creepy", "nightmare", "cryptid",
    "scp", "wendigo", "skinwalker", "siren head", "backrooms", "slenderman", "the rake",
    "eldritch", "kaiju", "godzilla", "chupacabra", "mothman", "kinh dị", "rùng rợn", "ám ảnh",
)

CURATED_WILDLIFE_STOCK_QUERIES: Dict[str, List[str]] = {
    "birds_and_aerial": [
        "wild eagle soaring mountain sky",
        "wild birds in natural forest",
        "bird perched on tree branch",
    ],
    "apex_predators_big_cats": [
        "apex predator hunting wild nature",
        "wild predator in natural habitat",
        "wildlife documentary action",
    ],
    "ocean_marine_wildlife": [
        "marine wildlife swimming ocean",
        "underwater ocean wildlife",
        "deep blue ocean marine life",
    ],
    "african_savanna_safari": [
        "wild animals in african savanna",
        "african wildlife safari nature",
    ],
    "animal_rescue_pets": [
        "rescued dog happy in grass",
        "gentle puppy warm sunlight",
    ],
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
        niche_lower = (channel_niche or "").lower().strip()
        if any(k in niche_lower for k in ("horror", "deep_sea", "mystery", "mysteries", "history", "scifi", "psychology", "stoic")):
            return False, 0.0, "none", []

        combined_text = f"{topic} {narration} {visual_keyword} {visual_prompt} {channel_niche}".lower()
        for excl in MONSTER_AND_HORROR_EXCLUSIONS:
            if re.search(rf"(?:\b|\s|^){re.escape(excl)}(?:\b|\s|$)", combined_text, re.IGNORECASE):
                return False, 0.0, "none", []

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
        """Lấy danh sách truy vấn dự phòng tổng quát theo phân nhánh, không ép cứng loài vật lệch kịch bản."""
        return list(self.curated_queries.get(subgenre, self.curated_queries["apex_predators_big_cats"]))

    def sanitize_animal_query(self, query: str, subgenre: str = "birds_and_aerial") -> str:
        """
        Làm sạch các từ khóa ẩn dụ phi thiên nhiên (office, corporate, laptop, laboratory)
        mà vẫn giữ nguyên 100% tên loài vật và hành động thật trong `query` của kịch bản.
        """
        q_clean = " ".join((query or "").strip().split())
        if not q_clean:
            return "wild animals in natural habitat"

        # Chỉ loại bỏ các từ khóa bối cảnh công sở / phòng thí nghiệm nhân tạo bị lẫn từ phép ẩn dụ
        cleaned = re.sub(
            r"\b(?:office|corporate|laptop|business|boardroom|laboratory|test\s+tube|hospital|factory|hydraulic\s+press)\b",
            "",
            q_clean,
            flags=re.IGNORECASE,
        )
        cleaned = " ".join(cleaned.split()).strip()
        return cleaned if len(cleaned) >= 3 else q_clean

    def build_cinematic_wildlife_prompt(
        self,
        raw_prompt: str,
        subgenre: str = "birds_and_aerial",
        scene_id: int = 1,
    ) -> str:
        """
        Tạo Master AI Prompt đẳng cấp National Geographic / BBC Earth 8K bám sát chủ thể thực tế của cảnh.
        """
        sanitized = self.sanitize_animal_query(raw_prompt, subgenre=subgenre)

        if subgenre == "birds_and_aerial":
            prefix = "Award-winning National Geographic wildlife photograph, vertical 8k film still, "
            core = f"{sanitized}, razor-sharp feather detail, glistening intense eyes, natural wilderness background, soft morning sunlight bokeh"
            suffix = ", 600mm telephoto lens, hyper-realistic animal anatomy, photorealistic vertical 9:16, no humans, no cage, no zoo, no watermark, no text"
        elif subgenre == "apex_predators_big_cats":
            prefix = "BBC Planet Earth epic wildlife documentary vertical 8k film still, "
            core = f"{sanitized} in dynamic motion, powerful tensed muscles, intense predatory eyes, natural habitat atmospheric lighting"
            suffix = ", 400mm lens, razor-sharp texture, hyper-realistic, vertical 9:16 composition, no humans, no collar, no cage, no 3d render, no text"
        elif subgenre == "ocean_marine_wildlife":
            prefix = "National Geographic underwater marine documentary vertical 8k, "
            core = f"{sanitized} swimming through deep ocean water, natural light rays refracting through water surface, glistening skin texture"
            suffix = ", underwater wide-angle cinema lens, photorealistic vertical 9:16, no text, no watermark"
        elif subgenre == "animal_rescue_pets":
            prefix = "Heartwarming animal documentary vertical 8k portrait, "
            core = f"tender emotional scene of {sanitized}, soulful shining eyes reflecting golden sunlight, soft realistic fur texture"
            suffix = ", 50mm f/1.4 shallow depth of field, photorealistic 9:16, no text, no watermark"
        else:
            prefix = "Award-winning National Geographic wildlife photography vertical 8k, "
            core = f"{sanitized} in natural pristine wilderness, dramatic atmospheric lighting, authentic animal behavior"
            suffix = ", telephoto lens, photorealistic vertical 9:16, no humans, no cage, no watermark"

        return f"{prefix}{core}{suffix}"
