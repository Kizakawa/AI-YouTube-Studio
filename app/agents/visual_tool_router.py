"""
app.agents.visual_tool_router — Bộ định tuyến công cụ hình ảnh theo BẢN CHẤT phân cảnh & Năng lực máy chủ (Visual Tool Router).

Nguyên tắc cốt lõi:
- Luôn tôn trọng truy vấn ngữ cảnh cụ thể (`stock_query` / `visual_keyword`) và phân loại `reality_class` từ bộ não LLM trước.
- Chỉ dùng phân loại từ khóa dự phòng khi phân cảnh chưa có `reality_class` từ LLM.
"""
import os
import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

REAL_FILMABLE = "REAL_FILMABLE"
SCIENTIFIC_VISUALIZATION = "SCIENTIFIC_VISUALIZATION"
PURE_FICTION = "PURE_FICTION"
ABSTRACT_CONCEPT = "ABSTRACT_CONCEPT"
DEEP_SEA_MYSTERY = "DEEP_SEA_MYSTERY"

REALITY_KEYWORDS: Dict[str, List[str]] = {
    DEEP_SEA_MYSTERY: [
        "sea monster", "kraken", "megalodon", "colossal squid", "giant squid", "deep sea creature",
        "abyssal trench", "mariana trench", "bermuda triangle", "loch ness", "leviathan", "underwater anomaly",
        "sonar ping", "sonar anomaly", "sunken ship", "deep ocean", "abyss", "giant octopus", "the bloop",
        "quái vật biển", "mực khổng lồ", "tam giác quỷ", "sinh vật biển sâu", "thủy quái", "rãnh mariana",
        "đáy đại dương", "sóng siêu âm", "tàu đắm", "vực thẳm đại dương", "biển sâu",
    ],
    REAL_FILMABLE: [
        "astronaut", "spacewalk", "space station", "iss", "rocket", "rocket launch", "launch pad", "spacex",
        "falcon 9", "starship launch", "earth from space", "earth from orbit", "satellite", "telescope",
        "observatory", "radio telescope", "mars rover", "rover", "apollo", "moon landing", "aurora",
        "northern lights", "eclipse", "night sky", "milky way", "meteor shower", "robot arm", "robotic arm",
        "humanoid robot", "boston dynamics", "drone", "server room", "data center", "laboratory", "scientist",
        "circuit board", "microchip", "computer code", "programmer", "city at night", "neon street",
        "neon city", "traffic at night", "skyscraper", "vr headset", "smartphone", "mission control",
        "phi hành gia", "trạm vũ trụ", "tên lửa", "phóng tên lửa", "trái đất nhìn từ", "vệ tinh",
        "kính thiên văn", "đài quan sát", "xe tự hành", "cực quang", "nhật thực", "nguyệt thực",
        "bầu trời đêm", "dải ngân hà", "mưa sao băng", "cánh tay robot", "phòng thí nghiệm", "nhà khoa học",
        "trung tâm dữ liệu", "máy chủ", "vi mạch", "thành phố về đêm",
    ],
    SCIENTIFIC_VISUALIZATION: [
        "black hole", "event horizon", "accretion disk", "neutron star", "pulsar", "quasar", "magnetar",
        "supernova", "nebula", "galaxy", "galaxies", "galaxy collision", "big bang", "exoplanet",
        "gravitational wave", "wormhole", "dark matter", "dark energy", "solar flare", "sun surface",
        "jupiter", "saturn", "saturn rings", "planet", "asteroid", "comet", "solar system", "white dwarf",
        "red giant", "atom", "electron", "quantum particle", "spacetime", "space-time",
        "hố đen", "lỗ đen", "chân trời sự kiện", "đĩa bồi tụ", "sao neutron", "sao xung", "siêu tân tinh",
        "tinh vân", "thiên hà", "vụ nổ big bang", "hành tinh", "ngoại hành tinh", "sóng hấp dẫn", "lỗ sâu",
        "vật chất tối", "năng lượng tối", "bão mặt trời", "sao mộc", "sao thổ", "tiểu hành tinh",
        "sao chổi", "hệ mặt trời", "sao lùn trắng", "nguyên tử", "không thời gian",
    ],
    PURE_FICTION: [
        "alien", "aliens", "alien creature", "alien city", "alien civilization", "extraterrestrial",
        "mothership", "starship fleet", "alien fleet", "space battle", "dyson sphere", "megastructure",
        "ringworld", "time machine", "time traveler", "parallel universe", "parallel world", "multiverse",
        "portal", "stargate", "warp drive", "hyperdrive", "cyborg army", "robot army", "robot uprising",
        "post-apocalyptic", "apocalypse", "year 3000", "future city", "futuristic city", "flying cars",
        "mars colony", "terraformed", "terraforming", "space colony", "laser weapon", "energy shield",
        "teleport", "teleportation", "kaiju", "dragon", "ufo",
        "người ngoài hành tinh", "sinh vật ngoài hành tinh", "văn minh ngoài hành tinh", "đĩa bay",
        "phi thuyền mẹ", "hạm đội", "chiến tranh không gian", "quả cầu dyson", "siêu cấu trúc",
        "cỗ máy thời gian", "du hành thời gian", "vũ trụ song song", "thế giới song song", "đa vũ trụ",
        "cổng không gian", "cổng dịch chuyển", "dịch chuyển tức thời", "thành phố tương lai", "năm 3000",
        "hậu tận thế", "tận thế", "đội quân robot", "robot nổi dậy", "xe bay", "thuộc địa sao hỏa",
        "cải tạo sao hỏa", "vũ khí laser",
    ],
    ABSTRACT_CONCEPT: [
        "simulation", "simulation theory", "consciousness", "infinity", "infinite", "higher dimension",
        "fourth dimension", "4th dimension", "entanglement", "quantum entanglement", "probability",
        "paradox", "singularity", "superintelligence", "the mind", "reality itself", "nothingness",
        "thuyết mô phỏng", "mô phỏng", "ý thức", "vô hạn", "vô tận", "chiều không gian", "chiều thứ tư",
        "rối lượng tử", "nghịch lý", "điểm kỳ dị", "siêu trí tuệ", "hư vô",
    ],
}

CLASS_QUERY_HINTS: Dict[str, List[str]] = {
    DEEP_SEA_MYSTERY: [
        "giant {subject} ocean",
        "{subject} attacking ship",
        "deep ocean submarine spotlight",
    ],
    SCIENTIFIC_VISUALIZATION: ["{subject} animation", "{subject} 3d", "{subject} space"],
    ABSTRACT_CONCEPT: [
        "abstract particles glowing",
        "light tunnel motion",
        "glowing network abstract",
    ],
    REAL_FILMABLE: ["{subject}"],
    PURE_FICTION: [],
}


def _hits(text: str, keywords: List[str]) -> List[str]:
    found = []
    for kw in sorted(keywords, key=len, reverse=True):
        if re.search(rf"(?<![\w]){re.escape(kw)}(?![\w])", text, re.IGNORECASE):
            if not any(kw in f for f in found):
                found.append(kw)
    return found


def get_generation_capabilities() -> Dict[str, bool]:
    """Năng lực sinh hình THẬT đang bật trên máy chủ (đọc từ cấu hình, không gọi mạng)."""
    try:
        from config.settings import settings
        veo = bool(getattr(settings, "GOOGLE_VEO_ENABLED", False))
    except Exception:
        veo = False
    if os.getenv("GOOGLE_VEO_ENABLED", "").lower() in ("1", "true", "yes"):
        veo = True
    pollinations = bool(os.getenv("POLLINATIONS_API_KEY"))
    hf_video = bool(os.getenv("HUGGINGFACE_TOKEN") or os.getenv("HF_TOKEN")) and os.getenv("HF_VIDEO_ENDPOINT_ENABLED", "0") == "1"
    return {
        "true_text_to_video": veo or pollinations or hf_video,
        "veo": veo,
        "pollinations_video": pollinations,
        "hf_video": hf_video,
        "stock_video": bool(os.getenv("PEXELS_API_KEY") or os.getenv("PEXELS_API_KEYS") or os.getenv("PIXABAY_API_KEY")),
    }


class VisualToolRouter:
    """Quyết định công cụ sản xuất hình cho từng cảnh dựa trên bản chất nội dung + năng lực thật của hệ thống."""

    def __init__(self, capabilities: Optional[Dict[str, bool]] = None):
        self.caps = capabilities or get_generation_capabilities()

    def classify_scene(
        self,
        narration: str = "",
        visual_keyword: str = "",
        stock_query: str = "",
        visual_prompt: str = "",
    ) -> Tuple[str, Dict[str, List[str]]]:
        visual_text = f"{visual_keyword} {stock_query} {visual_prompt}".lower()
        narr_text = (narration or "").lower()
        matched: Dict[str, List[str]] = {}
        scores: Dict[str, float] = {}
        for cls, kws in REALITY_KEYWORDS.items():
            v_hits = _hits(visual_text, kws)
            n_hits = [h for h in _hits(narr_text, kws) if h not in v_hits]
            if v_hits or n_hits:
                matched[cls] = v_hits + n_hits
                scores[cls] = 2.0 * len(v_hits) + 1.0 * len(n_hits)

        if not scores:
            if visual_text.strip():
                return REAL_FILMABLE, matched
            return ABSTRACT_CONCEPT, matched

        priority = {
            DEEP_SEA_MYSTERY: 4,
            PURE_FICTION: 3,
            SCIENTIFIC_VISUALIZATION: 2,
            REAL_FILMABLE: 1,
            ABSTRACT_CONCEPT: 0,
        }
        best = max(scores, key=lambda c: (scores[c], priority.get(c, 0)))
        return best, matched

    def route_scene(
        self,
        scene: Dict[str, Any],
        topic: str = "",
        subgenre_queries: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        narr = scene.get("narration") or ""
        kw = scene.get("visual_keyword") or ""
        sq = scene.get("stock_query") or ""
        vp = scene.get("visual_prompt") or ""

        # Ưu tiên phân loại ngữ nghĩa sẵn có từ LLM trên scene trước khi dùng classify_scene dự phòng
        explicit_reality = str(scene.get("reality_class") or "").strip().upper()
        reality, matched = self.classify_scene(narr, kw, sq, vp)
        if explicit_reality in REALITY_KEYWORDS:
            reality = explicit_reality

        subject = (matched.get(reality) or [""])[0]

        queries: List[str] = []

        def _add(q: str):
            q = (q or "").strip()
            if q and q not in queries:
                queries.append(q)

        _add(sq)
        _add(" ".join(kw.split()[:5]))
        for hint in CLASS_QUERY_HINTS.get(reality, []):
            if "{subject}" in hint:
                if subject:
                    _add(hint.format(subject=subject))
            else:
                _add(hint)
        for q in subgenre_queries or []:
            _add(q)

        true_t2v = self.caps.get("true_text_to_video", False)
        prefer_generation = False
        if reality == PURE_FICTION:
            prefer_generation = True
            if true_t2v:
                method, tool = "TEXT_TO_VIDEO", "generative_video"
                why = "thứ không tồn tại ngoài đời -> sinh video AI chuyển động thật"
            else:
                method, tool = "IMAGE_TO_VIDEO", "ai_image_motion"
                why = "thứ không tồn tại ngoài đời -> sinh ảnh AI + chuyển động"
        elif reality == DEEP_SEA_MYSTERY:
            method, tool = "IMAGE_TO_VIDEO", "creature_horror_art"
            why = "bí ẩn đại dương & quái vật biển -> ưu tiên săn ảnh Movie CGI / ArtStation Dark Horror kết hợp chuyển động 3D"
        elif reality == SCIENTIFIC_VISUALIZATION:
            method, tool = "STOCK_FOOTAGE", "nasa_pixabay_cgi"
            why = "có thật nhưng không quay được -> ưu tiên mô phỏng CGI/NASA có sẵn trong kho"
        elif reality == REAL_FILMABLE:
            method, tool = "STOCK_FOOTAGE", "nasa_pexels_footage"
            why = "có thật và đã được quay -> footage thật thuyết phục hơn ảnh AI"
        else:
            method, tool = "STOCK_FOOTAGE", "abstract_motion_graphics"
            why = "khái niệm trừu tượng -> motion graphics chuyển động thật từ kho"

        return {
            "reality_class": reality,
            "matched_keywords": matched.get(reality, [])[:5],
            "production_method": method,
            "tool": tool,
            "prefer_generation": prefer_generation,
            "search_queries": queries[:6],
            "search_query": queries[0] if queries else (kw or topic),
            "rationale": f"[{reality}] {why}" + (f" (khớp: {', '.join(matched.get(reality, [])[:3])})" if matched.get(reality) else ""),
        }
