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
            "keywords": ["manipulat", "control", "power", "dark", "influence", "trick", "toxic", "lie", "liar"],
            "asin": "0140280197",
            "name": "The 48 Laws of Power (Robert Greene)",
            "cta": "Expose dark psychology & protect your mind",
            "short_desc": "The bestselling guide on how power & manipulation work in real life",
            "search_fallback": "48 laws of power robert greene"
        },
        {
            "keywords": ["idiot", "behavior", "type", "personality", "mbti", "people", "conflict", "argue"],
            "asin": "1250251145",
            "name": "Surrounded by Idiots (Thomas Erikson)",
            "cta": "Decode human behavior & the 4 personality types",
            "short_desc": "Learn how to communicate with people who seem impossible to understand",
            "search_fallback": "surrounded by idiots thomas erikson"
        },
        {
            "keywords": ["persuade", "persuasion", "influence", "sell", "agree", "comply", "negotiat"],
            "asin": "006124189X",
            "name": "Influence: The Psychology of Persuasion (Robert Cialdini)",
            "cta": "Master the 6 universal triggers of human compliance",
            "short_desc": "The foundational science of persuasion and ethical influence",
            "search_fallback": "influence psychology of persuasion cialdini"
        },
        {
            "keywords": ["stoic", "calm", "silence", "emotion", "anxiety", "react", "insult", "peace"],
            "asin": "0525534393",
            "name": "The Daily Stoic Journal (Ryan Holiday)",
            "cta": "Master high-status unshakeable calm & cold silence",
            "short_desc": "Daily stoic practices for emotional control and mental toughness",
            "search_fallback": "daily stoic journal ryan holiday"
        }
    ],
    "history_mystery": [
        {
            "keywords": ["sky", "star", "space", "alien", "telescope", "nasa", "planet", "galaxy", "night"],
            "asin": "B001TI9Y2M",
            "name": "Celestron 70mm Portable Astronomy Telescope",
            "cta": "Explore the unseen night sky & cosmic mysteries",
            "short_desc": "High-clarity travel telescope perfect for stargazers & sky watchers",
            "search_fallback": "celestron travel telescope 70mm"
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
            "keywords": ["ocean", "water", "abyss", "deep", "dark", "flashlight", "survival", "gear", "underground"],
            "asin": "B08B81QG62",
            "name": "Ultra-Bright Tactical Waterproof Exploration Flashlight",
            "cta": "Gear up for dark underground & outdoor exploration",
            "short_desc": "10,000 Lumen military-grade waterproof rechargeable torch",
            "search_fallback": "high lumen waterproof tactical flashlight"
        }
    ],
    "animal_rescue": [
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
        },
        {
            "keywords": ["first aid", "emergency", "wound", "heal", "vet", "rescue", "health", "care"],
            "asin": "B08HMR1F7H",
            "name": "Tactical Emergency Pet First Aid Kit for Dogs & Cats",
            "cta": "Essential medical rescue gear every pet parent needs",
            "short_desc": "Compact veterinary emergency kit for pet injuries & cuts",
            "search_fallback": "pet first aid kit for dogs cats"
        }
    ],
    "wildlife_predators": [
        {
            "keywords": ["binocular", "spot", "safari", "watch", "lion", "tiger", "bear", "zoom", "sight"],
            "asin": "B004KM82IQ",
            "name": "Celestron Outland X 10x42 High-Definition Wildlife Binoculars",
            "cta": "Spot apex predators in crystal-clear 10x zoom",
            "short_desc": "Waterproof, fogproof multi-coated optics for wildlife observation",
            "search_fallback": "celestron outland x 10x42 binoculars"
        },
        {
            "keywords": ["predator", "hunt", "lethal", "bite", "strike", "biomechanic", "shark", "badger", "crocodile"],
            "asin": "1426217036",
            "name": "Predators: The World's Most Lethal Hunters (National Geographic)",
            "cta": "Dive deep into extreme predator combat biomechanics",
            "short_desc": "Full-color hardcover guide exploring lethal hunting adaptations",
            "search_fallback": "national geographic predators most lethal hunters"
        },
        {
            "keywords": ["distance", "range", "track", "field", "speed", "measure"],
            "asin": "B08KGWD554",
            "name": "Bushnell Bone Collector Laser Rangefinder for Wildlife",
            "cta": "Precision long-range tracking for outdoor wildlife explorers",
            "short_desc": "Instant distance measurement optics for open terrain",
            "search_fallback": "bushnell laser rangefinder wildlife"
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

def build_amazon_url(asin_or_search: str, tracking_tag: str, is_search: bool = False) -> str:
    """
    Sinh đường link Amazon Affiliate hợp lệ kèm mã định danh theo dõi (Tracking Tag).
    - Link trực tiếp ASIN: https://www.amazon.com/dp/{asin}?tag={tag}
    - Link tìm kiếm Smart Search: https://www.amazon.com/s?k={query}&tag={tag}
    """
    tag = (tracking_tag or os.getenv("AMAZON_ASSOCIATE_TAG", "")).strip()
    if not tag:
        # Nếu chưa cấu hình tag, link sẽ là link Amazon sạch, sẵn sàng thêm tag khi user đăng ký xong
        if is_search:
            return f"https://www.amazon.com/s?k={quote_plus(asin_or_search)}"
        return f"https://www.amazon.com/dp/{asin_or_search}"

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
    niche_clean = "psychology"
    if "mystery" in niche or "history" in niche:
        niche_clean = "history_mystery"
    elif "rescue" in niche or "cat" in niche or "dog" in niche:
        niche_clean = "animal_rescue"
    elif "wild" in niche or "predator" in niche or "animal" in niche:
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
        f"\n\n📌 Recommended Resources & Gear:\n"
        f"👉 {product['name']} — {product['cta']}:\n"
        f"🔗 {affiliate_url}\n\n"
        f"{AMAZON_COMPLIANCE_DISCLAIMER}"
    )

    # Tránh chèn trùng lặp nếu mô tả đã có link amazon này rồi
    if product['asin'] not in desc_base and "amazon.com" not in desc_base:
        enriched_desc = f"{desc_base}{monetization_footer}".strip()
    else:
        enriched_desc = desc_base

    # 2. Nâng cấp bình luận ghim (Pinned Comment)
    comment_base = (pinned_comment or "").strip()
    if comment_base:
        # Nếu đã có câu hỏi tương tác, gắn thêm gợi ý sản phẩm ngay bên dưới
        enriched_comment = (
            f"{comment_base}\n\n"
            f"📖 Recommended: {product['name']}\n"
            f"👉 Check availability on Amazon: {affiliate_url}"
        )
    else:
        # Nếu chưa có comment, tự sinh câu comment mồi hấp dẫn
        enriched_comment = (
            f"🔥 {product['cta']}!\n"
            f"👉 {product['name']} on Amazon: {affiliate_url}"
        )

    product_info = {
        "asin": product["asin"],
        "name": product["name"],
        "cta": product["cta"],
        "affiliate_url": affiliate_url,
        "tracking_tag": tracking_tag or "(Chưa điền tag - Dùng link trực tiếp)"
    }

    return enriched_desc, enriched_comment, product_info
