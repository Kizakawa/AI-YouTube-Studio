import os
import re
import logging
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import quote_plus
from config.settings import settings

logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# 1. KHO SẢN PHẨM BEST-SELLER UY TÍN TRÊN AMAZON THEO TỪNG NGÁCH (CURATED MATRIX)
# ═════════════════════════════════════════════════════════════════════════════

NICHE_PRODUCT_CATALOG: Dict[str, List[Dict[str, Any]]] = {
    "psychology": [
        {
            "keywords": ["hypnotic", "illusion", "focus", "mind", "stress", "calm", "relax", "brain", "overthinking", "anxiety", "kinetic"],
            "asin": "B085CD6B95",
            "name": "Kinetic Optical Illusion Anti-Gravity Desk Sphere (Stress Relief)",
            "cta": "Mesmerizing 360° hypnotic spiral to reset mind & kill overthinking",
            "short_desc": "Aerospace-grade kinetic desk toy creating mind-bending continuous optical illusion",
            "search_fallback": "kinetic optical illusion desk toy stress relief"
        },
        {
            "keywords": ["manipulat", "control", "power", "dark", "glow", "night", "moon", "ambient", "desk", "room", "levitate", "shadow"],
            "asin": "B08L7V4QW6",
            "name": "Levitating Magnetic Floating Moon Lamp (3D Night Light)",
            "cta": "Bring hypnotic floating dark aesthetic ambient to your desk",
            "short_desc": "Wireless 3D magnetic floating moon lamp spinning smoothly in mid-air",
            "search_fallback": "levitating magnetic floating moon lamp"
        },
        {
            "keywords": ["voice", "sound", "light", "wave", "music", "reaction", "secret", "body language", "frequency", "vibe"],
            "asin": "B099F8D69H",
            "name": "Sound-Reactive RGB Flowing Rhythm Light Bar (32-Bit ARM)",
            "cta": "Visualize unseen sound frequencies & ambient energy in your room",
            "short_desc": "Voice-activated music rhythm pickup light with 32 vibrant RGB LEDs",
            "search_fallback": "sound reactive rgb light bar rhythm pickup"
        },
        {
            "keywords": ["habit", "discipline", "fidget", "pen", "focus", "restless", "energy", "channel", "trick", "toxic"],
            "asin": "B09M8CYX7Q",
            "name": "Polar Magnetic Modular Fidget Pen & Decompression Toy",
            "cta": "Transformable magnetic metal pen to channel restless mental energy",
            "short_desc": "Multi-functional magnetic stylus pen engineered for anxiety relief and focus",
            "search_fallback": "magnetic modular fidget pen stress toy"
        },
        {
            "keywords": ["peace", "emotion", "calm", "sleep", "jellyfish", "meditate", "stoic", "silence", "insult"],
            "asin": "B08CXK9R4Q",
            "name": "Sensory LED Floating Jellyfish Mood Aquarium Lamp",
            "cta": "Induce deep alpha brainwaves & melt tension with lifelike glowing jellyfish",
            "short_desc": "Color-changing ambient mood tank with ultra-quiet brushless motor",
            "search_fallback": "sensory led jellyfish mood lamp"
        },
        {
            "keywords": ["book", "read", "robert greene", "48 laws", "laws of power"],
            "asin": "0140280197",
            "name": "The 48 Laws of Power (Robert Greene)",
            "cta": "Expose dark psychology & protect your mind",
            "short_desc": "The bestselling guide on how power & manipulation work in real life",
            "search_fallback": "48 laws of power robert greene"
        }
    ],
    "deep_sea_earth_mysteries": [
        {
            "keywords": ["ocean", "water", "abyss", "deep", "diver", "megalodon", "shark", "monster", "lamp", "creature", "dark", "light"],
            "asin": "B0CJ6J9Z1N",
            "name": "Deep Sea Diver & Megalodon Epoxy Resin Wood Night Light",
            "cta": "Bring the glowing chill of the deep sea abyss into your room",
            "short_desc": "Handmade epoxy resin lamp featuring deep sea diver facing giant predator in glowing blue abyss",
            "search_fallback": "deep sea diver resin night light lamp"
        },
        {
            "keywords": ["sea", "wave", "mariana", "trench", "blue", "water", "room", "night", "stars", "sleep", "sound", "projector"],
            "asin": "B08H734L6J",
            "name": "Northern Ocean Wave & Galaxy Light Projector with Relaxing Sound",
            "cta": "Turn your bedroom ceiling into a glowing underwater ocean abyss",
            "short_desc": "Mesmerizing 3D ocean wave projector creating deep-sea ambience in any dark room",
            "search_fallback": "ocean wave light projector bedroom"
        },
        {
            "keywords": ["fossil", "megalodon", "tooth", "shark", "ancient", "monster", "predator", "extinct", "bone", "jaw", "beast"],
            "asin": "B08D3R48Z1",
            "name": "Megalodon Giant Prehistoric Shark Tooth Fossil Replica in Display Case",
            "cta": "Hold a 1:1 scale prehistoric deep-sea apex predator tooth",
            "short_desc": "Museum-grade 5.5 inch Megalodon shark tooth replica in collector showcase",
            "search_fallback": "megalodon shark tooth replica display"
        },
        {
            "keywords": ["creature", "abyss", "unsolved", "unknown", "scientists", "explore", "mystery", "photo", "species", "nasa"],
            "asin": "0226595668",
            "name": "The Deep: The Extraordinary Creatures of the Abyss (Claire Nouvian)",
            "cta": "Discover the bizarre glowing alien species lurking 10,000m below",
            "short_desc": "Full-color photographic compendium of bizarre bioluminescent deep-sea life",
            "search_fallback": "the deep extraordinary creatures abyss claire nouvian"
        }
    ],
    "history_mystery": [
        {
            "keywords": ["what if", "alternate", "history", "timeline", "scenario", "hypothetical", "paradox", "world war", "empire"],
            "asin": "0544272994",
            "name": "What If? Serious Scientific Answers to Absurd Hypothetical Questions",
            "cta": "Explore mind-bending hypothetical questions & alternate scenarios",
            "short_desc": "Hilarious and deeply researched hypothetical scenarios by Randall Munroe",
            "search_fallback": "what if randall munroe"
        },
        {
            "keywords": ["sky", "star", "space", "alien", "galaxy", "nasa", "planet", "astronaut", "universe", "nebula"],
            "asin": "B09S9YF57Q",
            "name": "Astronaut Galaxy Star Projector (TikTok Viral Night Light)",
            "cta": "Transform your room into a breathtaking cosmic nebula",
            "short_desc": "360-degree magnetic astronaut projector casting high-definition galaxy stars",
            "search_fallback": "astronaut galaxy star projector night light"
        },
        {
            "keywords": ["ancient", "vault", "sphinx", "pyramid", "ruin", "civilization", "history", "secret", "archive"],
            "asin": "0753558661",
            "name": "The Atlas of Ancient Mysteries",
            "cta": "Uncover forbidden archaeological archives & sealed vaults",
            "short_desc": "Illustrated historical compendium of unexplained ancient ruins",
            "search_fallback": "atlas of ancient mysteries hardcover"
        },
        {
            "keywords": ["moon", "levitate", "floating", "gravity", "technology", "quantum", "strange", "desk", "night"],
            "asin": "B08L7V4QW6",
            "name": "Levitating Magnetic Floating Moon Lamp (3D Night Light)",
            "cta": "Experience real magnetic levitation on your desk",
            "short_desc": "Wireless floating 3D moon lamp spinning smoothly in mid-air via magnetic levitation",
            "search_fallback": "levitating magnetic floating moon lamp"
        }
    ],
    "animal_rescue": [
        {
            "keywords": ["bed", "sleep", "calm", "anxiety", "rescue", "trauma", "dog", "cat", "comfort", "safe", "warm"],
            "asin": "B07G4T12WQ",
            "name": "Fluffy Calming Anti-Anxiety Donut Cuddle Dog/Cat Bed",
            "cta": "Give rescued anxious pets the feeling of total safety and warmth",
            "short_desc": "Orthopedic self-warming shag fur donut bed engineered for traumatized pets",
            "search_fallback": "calming donut dog cat bed"
        },
        {
            "keywords": ["lost", "track", "gps", "run", "stray", "find", "safe", "collar", "dog", "cat"],
            "asin": "B08M6BFVDD",
            "name": "Tractive Waterproof GPS Pet Tracker (Live Location)",
            "cta": "Never lose a beloved rescued fur baby again",
            "short_desc": "Real-time GPS tracking collar attachment for dogs & cats",
            "search_fallback": "tractive waterproof gps pet tracker"
        },
        {
            "keywords": ["kitten", "cat", "play", "warm", "toy", "joy", "heal", "lonely"],
            "asin": "B00D3NI2PG",
            "name": "Catit Senses 2.0 Wellness & Play Center",
            "cta": "Give rescued kittens comfort, grooming & endless play",
            "short_desc": "Multi-sensory massage and interactive cat play station",
            "search_fallback": "catit senses wellness center"
        }
    ],
    "wildlife_predators": [
        {
            "keywords": ["binocular", "spot", "safari", "watch", "lion", "tiger", "bear", "zoom", "sight", "bird", "eagle", "hunt", "nature"],
            "asin": "B004KM82IQ",
            "name": "Celestron Outland X 10x42 High-Definition Wildlife Binoculars",
            "cta": "Spot apex predators & wild birds in crystal-clear 10x zoom",
            "short_desc": "Waterproof, fogproof multi-coated optics for wildlife observation",
            "search_fallback": "celestron outland x 10x42 binoculars"
        },
        {
            "keywords": ["lion", "tiger", "wolf", "lamp", "desk", "room", "decor", "night", "apex", "power", "3d"],
            "asin": "B07H831N6L",
            "name": "3D Optical Illusion Majestic Predator LED Night Lamp",
            "cta": "Bring the raw power of apex predators to your desk",
            "short_desc": "Color-changing 3D optical illusion lamp featuring majestic wild beasts",
            "search_fallback": "3d optical illusion lion wolf led lamp"
        },
        {
            "keywords": ["predator", "hunt", "lethal", "bite", "strike", "biomechanic", "shark", "badger", "crocodile"],
            "asin": "1426217036",
            "name": "Predators: The World's Most Lethal Hunters (National Geographic)",
            "cta": "Dive deep into extreme predator combat biomechanics",
            "short_desc": "Full-color hardcover guide exploring lethal hunting adaptations",
            "search_fallback": "national geographic predators most lethal hunters"
        }
    ]
}

# Câu thông báo miễn trừ bắt buộc theo chính sách của Amazon Associates Program
AMAZON_COMPLIANCE_DISCLAIMER = (
    "⚖️ Disclaimer: As an Amazon Associate I earn from qualifying purchases "
    "at no additional cost to you. Product prices and availability are accurate as of the date indicated."
)


# ═════════════════════════════════════════════════════════════════════════════
# 2. BỘ TẠO LINK AFFILIATE AMAZON CHUẨN XÁC
# ═════════════════════════════════════════════════════════════════════════════

def build_amazon_url(asin_or_search: str, tracking_tag: str = "", is_search: bool = False) -> str:
    """
    Sinh đường link Amazon Affiliate hợp lệ kèm mã định danh theo dõi (Tracking Tag).
    - Link trực tiếp ASIN: https://www.amazon.com/dp/{asin}?tag={tag}
    - Link tìm kiếm Smart Search: https://www.amazon.com/s?k={query}&tag={tag}
    """
    tag = (tracking_tag or os.getenv("AMAZON_ASSOCIATE_TAG", "kizakawa-20")).strip()
    if not tag:
        tag = "kizakawa-20"

    if is_search:
        return f"https://www.amazon.com/s?k={quote_plus(asin_or_search)}&tag={tag}"
    return f"https://www.amazon.com/dp/{asin_or_search}?tag={tag}"


def find_best_amazon_product(niche: str, title: str, topic: str = "", script: str = "") -> Dict[str, Any]:
    """
    Thuật toán AI đối chiếu nội dung video để tìm sản phẩm Amazon phù hợp nhất:
    1. Chuẩn hóa text tiêu đề + chủ đề + kịch bản thành chữ thường.
    2. Quét bộ từ khóa (keywords) trong kho catalog của ngách đó.
    3. Trả về sản phẩm có điểm tương đồng cao nhất, hoặc fallback về Best Seller số 1 của ngách.
    """
    # Chuẩn hóa niche
    niche_lower = (niche or "").lower()
    niche_clean = "psychology"
    if any(k in niche_lower for k in ["deep_sea", "ocean", "sea", "marine", "abyss"]):
        niche_clean = "deep_sea_earth_mysteries"
    elif any(k in niche_lower for k in ["mystery", "mysteries", "history", "ancient", "space", "earth"]):
        niche_clean = "history_mystery"
    elif any(k in niche_lower for k in ["rescue", "cat", "dog", "pet"]):
        niche_clean = "animal_rescue"
    elif any(k in niche_lower for k in ["wild", "predator", "animal"]):
        niche_clean = "wildlife_predators"

    catalog = NICHE_PRODUCT_CATALOG.get(niche_clean, NICHE_PRODUCT_CATALOG["psychology"])

    full_text = f"{title} {topic} {script}".lower()

    best_match = None
    best_score = 0

    for item in catalog:
        score = 0
        for kw in item.get("keywords", []):
            if kw.lower() in full_text:
                score += 1
        if score > best_score:
            best_score = score
            best_match = item

    # Nếu không khớp từ khóa cụ thể nào, chọn sản phẩm đầu tiên (Bestseller #1) của ngách
    if not best_match:
        best_match = catalog[0]

    return {
        "niche": niche_clean,
        "asin": best_match["asin"],
        "name": best_match["name"],
        "cta": best_match["cta"],
        "short_desc": best_match["short_desc"],
        "search_fallback": best_match.get("search_fallback", "")
    }


# ═════════════════════════════════════════════════════════════════════════════
# 3. TỰ ĐỘNG LỒNG GHÉP VÀO MÔ TẢ & BÌNH LUẬN GHI ĐIỂM (AUTO-ENRICHMENT)
# ═════════════════════════════════════════════════════════════════════════════

def enrich_video_monetization(
    channel_config: Dict[str, Any],
    title: str,
    description: str = "",
    pinned_comment: Optional[str] = None,
    topic: str = "",
    script: str = ""
) -> Tuple[str, Optional[str], Dict[str, Any]]:
    """
    Tự động nâng cấp mô tả video và bình luận ghim với link Amazon Affiliate:
    - Tìm sản phẩm chuẩn xác nhất dựa trên nội dung video.
    - Chèn link sản phẩm + câu Disclaimer tuân thủ chính sách Amazon vào Mô tả.
    - Gắn CTA dẫn tới sản phẩm vào Bình luận mồi (Pinned Comment) được ghim trên cùng.
    
    Trả về: (enriched_description, enriched_pinned_comment, product_info)
    """
    # ── BẢO VỆ AN TOÀN: Kiểm tra cấu hình cho phép gắn Affiliate ──
    # Mặc định TẮT (False) để bảo vệ kênh không bị YouTube phạt spam link / hạn chế đề xuất Shorts
    enable_affiliate = os.getenv("ENABLE_AMAZON_AFFILIATE", "false").lower() in ("true", "1", "yes")
    channel_override = channel_config.get("enable_monetization")
    if channel_override is not None:
        enable_affiliate = bool(channel_override)

    if not enable_affiliate:
        logger.info("[MonetizationEngine] Bỏ qua gắn Amazon Affiliate (chế độ bảo vệ an toàn: ENABLE_AMAZON_AFFILIATE=false)")
        return description or "", pinned_comment, None

    niche = channel_config.get("niche", "general")
    tracking_tag = (
        channel_config.get("amazon_tag")
        or channel_config.get("monetization", {}).get("amazon_tag")
        or os.getenv("AMAZON_ASSOCIATE_TAG", "")
    ).strip()

    product = find_best_amazon_product(niche=niche, title=title, topic=topic, script=script)
    affiliate_url = build_amazon_url(product["asin"], tracking_tag)

    # 1. Nâng cấp mô tả (Description)
    desc_base = (description or "").strip()
    monetization_footer = (
        f"\n\n📌 Featured Trend & Gear:\n"
        f"👉 {product['name']} — {product['cta']}:\n"
        f"🔗 Direct Link: {affiliate_url}\n"
        f"💡 (Shorts viewers: Tap channel icon ➔ Clickable link in Bio!)\n\n"
        f"{AMAZON_COMPLIANCE_DISCLAIMER}"
    )

    # Tránh chèn trùng lặp nếu mô tả đã có link amazon này rồi
    if product['asin'] not in desc_base and "amazon.com" not in desc_base:
        hashtag_match = re.search(r'(\s*(?:#[a-zA-Z0-9_]+\s*)+)$', desc_base)
        if hashtag_match:
            tags_part = hashtag_match.group(1).strip()
            text_part = desc_base[:hashtag_match.start()].strip()
            enriched_desc = f"{text_part}{monetization_footer}\n\n{tags_part}".strip()
        else:
            enriched_desc = f"{desc_base}{monetization_footer}".strip()
    else:
        enriched_desc = desc_base

    # 2. Nâng cấp bình luận ghim (Pinned Comment)
    comment_base = (pinned_comment or "").strip()
    if comment_base:
        # Nếu đã có câu hỏi tương tác, gắn thêm gợi ý sản phẩm ngay bên dưới
        if "amazon.com" not in comment_base and product['asin'] not in comment_base:
            enriched_comment = (
                f"{comment_base}\n\n"
                f"✨ Trending Gear: {product['name']}\n"
                f"👉 Link: {affiliate_url}\n"
                f"🔗 (Or tap channel icon ➔ Link in Bio)"
            )
        else:
            enriched_comment = comment_base
    else:
        # Nếu chưa có comment, tự sinh câu comment mồi hấp dẫn
        enriched_comment = (
            f"🔥 {product['cta']}!\n"
            f"👉 {product['name']}: {affiliate_url}\n"
            f"🔗 (Or tap channel icon ➔ Link in Bio)"
        )

    product_info = {
        "asin": product["asin"],
        "name": product["name"],
        "cta": product["cta"],
        "affiliate_url": affiliate_url,
        "tracking_tag": tracking_tag or "kizakawa-20"
    }

    return enriched_desc, enriched_comment, product_info


def preview_monetization_bundle(
    channel_config: Dict[str, Any],
    title: str,
    description: str = "",
    pinned_comment: Optional[str] = None,
    topic: str = "",
    script: str = ""
) -> Dict[str, Any]:
    """
    Sinh gói xem trước (Preview Bundle) để hiển thị trực tiếp lên Web Studio hoặc Telegram Bot
    trước khi người dùng bấm xác nhận xuất bản.
    """
    enriched_desc, enriched_comment, product_info = enrich_video_monetization(
        channel_config=channel_config,
        title=title,
        description=description,
        pinned_comment=pinned_comment,
        topic=topic,
        script=script
    )
    return {
        "product": product_info,
        "enriched_description": enriched_desc,
        "enriched_pinned_comment": enriched_comment,
        "channel_id": channel_config.get("id"),
        "channel_name": channel_config.get("name"),
        "tracking_tag": product_info.get("tracking_tag") if product_info else None
    }
