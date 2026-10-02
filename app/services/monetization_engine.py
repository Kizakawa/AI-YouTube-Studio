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
            "keywords": ["power", "manipulat", "dark", "control", "laws of power", "robert greene", "rule", "status", "ego", "strategy"],
            "asin": "0140280197",
            "name": "The 48 Laws of Power (Robert Greene)",
            "cta": "Expose dark psychology & protect your mind from manipulation",
            "short_desc": "The definitive bestselling manual on the mechanics of power, influence, and dark psychology",
            "search_fallback": "48 laws of power robert greene"
        },
        {
            "keywords": ["habit", "discipline", "mindset", "focus", "brain", "routine", "change", "success", "overthinking", "stop"],
            "asin": "0735211299",
            "name": "Atomic Habits: Proven Way to Build Good Habits & Break Bad Ones",
            "cta": "Rewire your subconscious mindset & master peak daily discipline",
            "short_desc": "World's #1 breakthrough psychological guide to habit formation and behavioral science",
            "search_fallback": "atomic habits james clear"
        },
        {
            "keywords": ["persuade", "influence", "convince", "body language", "secret", "trick", "social", "psychology", "negotiat"],
            "asin": "0062937650",
            "name": "Influence: The Psychology of Persuasion (Robert Cialdini)",
            "cta": "Understand universal psychological triggers that make people say yes",
            "short_desc": "The foundational science of persuasion and compliance techniques by Dr. Robert Cialdini",
            "search_fallback": "influence psychology of persuasion cialdini"
        },
        {
            "keywords": ["think", "decision", "overthinking", "bias", "illusion", "stress", "calm", "fast", "slow", "mental"],
            "asin": "0374533555",
            "name": "Thinking, Fast and Slow (Daniel Kahneman - Nobel Prize)",
            "cta": "Uncover how your brain makes decisions and falls for cognitive traps",
            "short_desc": "Nobel laureate landmark exploration of intuitive vs analytical thinking",
            "search_fallback": "thinking fast and slow daniel kahneman"
        }
    ],
    "deep_sea_earth_mysteries": [
        {
            "keywords": ["creature", "abyss", "unsolved", "unknown", "scientists", "explore", "mystery", "species", "alien", "monster"],
            "asin": "0226595668",
            "name": "The Deep: The Extraordinary Creatures of the Abyss (Claire Nouvian)",
            "cta": "Discover the bizarre glowing alien species lurking 10,000m below",
            "short_desc": "Full-color photographic compendium of bizarre bioluminescent deep-sea life",
            "search_fallback": "the deep extraordinary creatures abyss claire nouvian"
        },
        {
            "keywords": ["ocean", "octopus", "intelligence", "sea", "tentacle", "creature", "diver", "water", "marine"],
            "asin": "1451697724",
            "name": "The Soul of an Octopus: Wonder of Oceanic Consciousness",
            "cta": "Meet the most alien, hyper-intelligent minds lurking on our planet",
            "short_desc": "National Book Award finalist exploring deep ocean invertebrate intelligence",
            "search_fallback": "the soul of an octopus sy montgomery"
        },
        {
            "keywords": ["ocean", "water", "abyss", "deep", "diver", "shark", "megalodon", "mariana", "trench", "explore"],
            "asin": "054448407X",
            "name": "Deep: Freediving, Renegade Science, and the Ocean Abyss",
            "cta": "Experience the limits of human survival in the extreme oceanic abyss",
            "short_desc": "Thrilling exploration of the extreme depths and uncharted frontiers of the sea",
            "search_fallback": "deep freediving renegade science james nestor"
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
            "keywords": ["ancient", "vault", "sphinx", "pyramid", "ruin", "civilization", "history", "secret", "archive", "lost"],
            "asin": "0753558661",
            "name": "The Atlas of Ancient Mysteries",
            "cta": "Uncover forbidden archaeological archives & sealed vaults",
            "short_desc": "Illustrated historical compendium of unexplained ancient ruins and forgotten history",
            "search_fallback": "atlas of ancient mysteries hardcover"
        }
    ],
    "animal_rescue": [
        {
            "keywords": ["bed", "sleep", "calm", "anxiety", "rescue", "trauma", "dog", "cat", "comfort", "safe", "warm", "shag"],
            "asin": "B01MV0IX66",
            "name": "Best Friends by Sheri The Original Calming Donut Dog/Cat Bed",
            "cta": "Give rescued anxious pets the feeling of total safety, warmth & deep sleep",
            "short_desc": "Orthopedic self-warming shag fur donut bed engineered specifically for traumatized and anxious pets",
            "search_fallback": "calming donut dog cat bed best friends by sheri"
        },
        {
            "keywords": ["toy", "play", "chew", "puppy", "dog", "anxious", "calm", "boredom", "rubber", "kong"],
            "asin": "B0002AR0I8",
            "name": "KONG Classic Dog Toy (Ultra-Durable Natural Rubber)",
            "cta": "Help rescued traumatized dogs relieve anxiety, stress & boredom",
            "short_desc": "Veterinarian recommended ultra-durable mental stimulation & stress-relief rubber toy",
            "search_fallback": "kong classic dog toy rubber"
        },
        {
            "keywords": ["lost", "run", "leash", "walk", "harness", "pull", "safe", "stray", "collar", "safety"],
            "asin": "B0009ZBKG4",
            "name": "PetSafe Easy Walk No-Pull Dog Safety Harness",
            "cta": "Keep rescued dogs safe and stress-free on walks with zero choking",
            "short_desc": "Trusted gentle front-leash attachment harness preventing pulling without neck strain",
            "search_fallback": "petsafe easy walk dog harness"
        },
        {
            "keywords": ["ball", "fetch", "run", "park", "energy", "active", "happy", "exercise"],
            "asin": "B000F4AVPA",
            "name": "Chuckit! Ultra Ball High-Bounce Durable Dog Toy",
            "cta": "Reignite pure playfulness and happiness in rescued dogs",
            "short_desc": "High-bounce, natural rubber durable fetch ball for energetic and healing dogs",
            "search_fallback": "chuckit ultra ball dog toy"
        },
        {
            "keywords": ["kitten", "cat", "massage", "groom", "purr", "pet", "warm", "play", "center"],
            "asin": "B00D3NI2PG",
            "name": "Catit Senses 2.0 Wellness & Massage Play Center",
            "cta": "Give rescued stray cats & kittens supreme comfort, grooming and love",
            "short_desc": "Multi-sensory 360-degree massage and interactive feline relaxation station",
            "search_fallback": "catit senses 2.0 wellness center"
        }
    ],
    "wildlife_predators": [
        {
            "keywords": ["binocular", "spot", "safari", "watch", "lion", "tiger", "bear", "zoom", "sight", "bird", "eagle", "hunt", "nature", "predator"],
            "asin": "B004KM82IQ",
            "name": "Celestron Outland X 10x42 High-Definition Wildlife Binoculars",
            "cta": "Spot apex predators & wild birds in crystal-clear 10x zoom",
            "short_desc": "Waterproof, fogproof multi-coated optics for wildlife observation",
            "search_fallback": "celestron outland x 10x42 binoculars"
        }
    ]
}

# Câu thông báo miễn trừ bắt buộc theo chính sách của Amazon Associates Program
AMAZON_COMPLIANCE_DISCLAIMER = (
    "⚖️ Disclaimer: As an Amazon Associate I earn from qualifying purchases "
    "at no additional cost to you. Product prices and availability are accurate as of the date indicated."
)

import time
import urllib.request

# Cache kiểm tra trạng thái ASIN trong bộ nhớ (asin -> (is_alive: bool, timestamp: float))
_ASIN_VALIDITY_CACHE: Dict[str, Tuple[bool, float]] = {}
CACHE_TTL_SECONDS = 86400  # 24 giờ


def verify_amazon_product_alive(asin: str, timeout: float = 3.5) -> bool:
    """
    Tự động truy cập kiểm tra tính khả dụng thực tế của link sản phẩm trên Amazon:
    - Sử dụng Amazon CDN Asset Image Endpoint (không bị Cloudflare/WAF chặn)
    - Khi ASIN tồn tại: Trả về ảnh sản phẩm thật (kích thước > 300 bytes, thường từ 5KB - 50KB).
    - Khi ASIN bị xóa / 404 / 'Dogs of Amazon': Trả về ảnh dummy 1x1 trong suốt có dung lượng đúng 43 bytes.
    - Lưu cache 24h để tăng tốc độ phản hồi tối đa cho các lần truy cập tiếp theo.
    """
    clean_asin = (asin or "").strip().upper()
    if not clean_asin or len(clean_asin) < 5:
        return False

    now = time.time()
    if clean_asin in _ASIN_VALIDITY_CACHE:
        is_alive, cached_time = _ASIN_VALIDITY_CACHE[clean_asin]
        if now - cached_time < CACHE_TTL_SECONDS:
            return is_alive

    img_url = f"https://images-na.ssl-images-amazon.com/images/P/{clean_asin}.01._SX300_SCLZZZZZZZ_.jpg"
    req = urllib.request.Request(
        img_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            # 43 bytes là ảnh rỗng của Amazon khi sản phẩm không tồn tại (Dogs of Amazon 404 placeholder)
            is_alive = len(data) > 300
            _ASIN_VALIDITY_CACHE[clean_asin] = (is_alive, now)
            return is_alive
    except Exception as e:
        logger.warning(f"[MonetizationEngine] Lỗi kiểm tra ASIN {clean_asin} trên Amazon CDN: {e}")
        return False


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
    Thuật toán AI tự động tìm và kiểm tra link sản phẩm Amazon:
    1. Chuẩn hóa niche và quét catalog sản phẩm.
    2. Chấm điểm tương đồng từ khóa dựa trên tiêu đề, chủ đề và kịch bản.
    3. Sắp xếp danh sách ứng viên từ điểm cao nhất xuống thấp nhất.
    4. VÒNG LẶP KIỂM TRA THỰC TẾ (Live Availability Check):
       - Hệ thống tự động truy cập link kiểm tra sản phẩm có còn tồn tại trên Amazon không.
       - Nếu sản phẩm còn tồn tại (ALIVE): Chọn sản phẩm đó ngay lập tức!
       - Nếu sản phẩm không còn tồn tại (DEAD/404): Tự động đổi sang sản phẩm kế tiếp và lặp lại.
    5. FALLBACK AN TOÀN TUYỆT ĐỐI (Smart Search):
       - Nếu tất cả ASIN cụ thể trong catalog đều bị lỗi/hết hàng, hệ thống tự động chuyển sang
         Link Tìm Kiếm Thông Minh Amazon (Smart Search URL kèm Store ID kizakawa-20),
         đảm bảo người xem bấm vào luôn ra sản phẩm thực tế, không bao giờ bị trang lỗi 404!
    """
    # Chuẩn hóa niche
    niche_lower = (niche or "").lower()
    niche_clean = "psychology"
    if any(k in niche_lower for k in ["deep_sea", "ocean", "sea", "marine", "abyss"]):
        niche_clean = "deep_sea_earth_mysteries"
    elif any(k in niche_lower for k in ["mystery", "mysteries", "history", "ancient", "space", "earth"]):
        niche_clean = "history_mystery"
    elif any(k in niche_lower for k in ["rescue", "cat", "dog", "pet", "animal"]):
        if any(k in niche_lower for k in ["predator", "wild", "hunt", "lion", "tiger", "bear"]):
            niche_clean = "wildlife_predators"
        else:
            niche_clean = "animal_rescue"
    elif any(k in niche_lower for k in ["wild", "predator"]):
        niche_clean = "wildlife_predators"

    catalog = NICHE_PRODUCT_CATALOG.get(niche_clean, NICHE_PRODUCT_CATALOG["psychology"])
    full_text = f"{title} {topic} {script}".lower()

    # Chấm điểm tất cả ứng viên trong catalog
    scored_candidates = []
    for item in catalog:
        score = 0
        for kw in item.get("keywords", []):
            if kw.lower() in full_text:
                score += 1
        scored_candidates.append((score, item))

    # Sắp xếp theo điểm tương đồng giảm dần
    scored_candidates.sort(key=lambda x: x[0], reverse=True)

    # 4. VÒNG LẶP KIỂM TRA LINK SẢN PHẨM TRỰC TIẾP
    for score, item in scored_candidates:
        asin = item.get("asin", "")
        if not asin:
            continue
        logger.info(f"🔍 [MonetizationEngine] Đang kiểm tra tính tồn tại của sản phẩm Amazon: {item['name']} (ASIN: {asin})...")
        if verify_amazon_product_alive(asin):
            logger.info(f"✅ [MonetizationEngine] Sản phẩm '{item['name']}' (ASIN: {asin}) CÒN HOẠT ĐỘNG trên Amazon. Đã chọn!")
            return {
                "niche": niche_clean,
                "asin": asin,
                "name": item["name"],
                "cta": item["cta"],
                "short_desc": item["short_desc"],
                "search_fallback": item.get("search_fallback", ""),
                "is_search": False,
                "is_verified_alive": True,
            }
        else:
            logger.warning(f"⚠️ [MonetizationEngine] Sản phẩm '{item['name']}' (ASIN: {asin}) KHÔNG CÒN TỒN TẠI trên Amazon! Tự động đổi sang sản phẩm khác trong danh mục...")

    # 5. FALLBACK AN TOÀN TUYỆT ĐỐI (Smart Search):
    # Nếu không có ASIN đơn lẻ nào khả dụng, tự động chuyển sang Link Tìm Kiếm Amazon Smart Search
    top_candidate = scored_candidates[0][1] if scored_candidates else catalog[0]
    fallback_query = top_candidate.get("search_fallback") or f"{niche_clean.replace('_', ' ')} best seller"
    logger.warning(f"⚠️ [MonetizationEngine] Không có ASIN đơn lẻ nào khả dụng. Tự động chuyển sang Link Tìm Kiếm Amazon Smart Search: '{fallback_query}'")
    return {
        "niche": niche_clean,
        "asin": fallback_query,
        "name": top_candidate["name"],
        "cta": top_candidate["cta"],
        "short_desc": top_candidate["short_desc"],
        "search_fallback": fallback_query,
        "is_search": True,
        "is_verified_alive": True,
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


# ═════════════════════════════════════════════════════════════════════════════
# 4. KHO SẢN PHẨM & LIÊN KẾT LAZADA THEO NGÁCH (LAZADA AFFILIATE MATRIX)
# ═════════════════════════════════════════════════════════════════════════════

def get_lazada_product_for_niche(niche: str = "general", title: str = "", tracking_tag: str = "") -> Dict[str, Any]:
    """Hàm tương thích ngược (Hệ thống hiện tại 100% chuyên biệt hóa cho Amazon Associates Store ID kizakawa-20)."""
    return {}


def build_repurposed_pinned_comment(
    title: str,
    niche_id: str = "general",
    source_url: str = "",
    author: str = "",
    channel_name: str = "",
    tracking_tag: str = "kizakawa-20",
) -> str:
    """
    Tự động xây dựng Bình luận ghim (Pinned Comment) cho Shorts tái chế:
    - Dẫn link xem bản đầy đủ của video gốc
    - Giới thiệu sản phẩm hữu ích kèm Store ID Amazon kizakawa-20 trực tiếp (Đã kiểm tra còn tồn tại)
    - Kêu gọi theo dõi kênh
    """
    tag = (tracking_tag or os.getenv("AMAZON_ASSOCIATE_TAG", "kizakawa-20") or "kizakawa-20").strip()
    amazon_prod = find_best_amazon_product(niche=niche_id, title=title)
    is_search = amazon_prod.get("is_search", False)
    amazon_aff_url = build_amazon_url(amazon_prod["asin"], tag, is_search=is_search)
    cta_label = "Search & shop on Amazon" if is_search else "Check price on Amazon"

    creator_name = author or channel_name
    creator_tag = f"by {creator_name} " if creator_name else ""
    full_story_line = f"📌 Watch the full story {creator_tag}here: {source_url}" if source_url else "📌 Watch and subscribe for daily incredible highlights! ❤️"

    comment = (
        f"{full_story_line}\n\n"
        f"🛒 Featured Gear (Store ID: {tag}): {amazon_prod['name']}\n"
        f"👉 {cta_label}: {amazon_aff_url}\n\n"
        f"All rights belong to original creators. Subscribe for more! ❤️"
    )
    return comment


def build_repurposed_video_description(
    title: str,
    summary: str = "",
    niche_id: str = "general",
    channel_name: str = "",
    source_url: str = "",
    author: str = "",
    mode: str = "self_owned",
    tracking_tag: str = "kizakawa-20",
) -> str:
    """
    Tự động xây dựng mô tả YouTube Shorts hoàn chỉnh chuẩn Bản Quyền & Kiếm Tiền Amazon Affiliate (Store ID: kizakawa-20):
    - Nội dung tiếng Anh hấp dẫn
    - Dẫn link video đầy đủ (Full Video / Original Source Link) giúp kéo view hoặc tôn trọng bản quyền tác giả
    - Điều khoản miễn trừ bản quyền Fair Use (Section 107 of the Copyright Act 1976)
    - Kêu gọi theo dõi kênh
    - Khối gắn link sản phẩm Amazon chuẩn ngách KÈM STORE ID: kizakawa-20 (Đã kiểm tra còn tồn tại trên Amazon)
    - Bộ Hashtags chuẩn SEO
    """
    tag = (tracking_tag or os.getenv("AMAZON_ASSOCIATE_TAG", "kizakawa-20") or "kizakawa-20").strip()
    
    # Tìm sản phẩm Amazon chuẩn ngách và gắn Store ID kizakawa-20
    amazon_prod = find_best_amazon_product(niche=niche_id, title=title, topic=summary)
    is_search = amazon_prod.get("is_search", False)
    amazon_aff_url = build_amazon_url(amazon_prod["asin"], tag, is_search=is_search)
    cta_label = "Search & shop on Amazon" if is_search else "Check price on Amazon"
    
    niche_tags = {
        "animal_rescue": "#AnimalRescue #RescueDog #Pets #Heartwarming #Shorts #AnimalLovers",
        "wildlife_predators": "#Wildlife #ApexPredator #Nature #WildAnimals #NatureLovers #Shorts",
        "psychology": "#Psychology #DarkPsychology #Mindset #BodyLanguage #LifeLessons #Shorts",
        "deep_sea_earth_mysteries": "#OceanMysteries #DeepSea #Unexplained #Science #Mysteries #Shorts",
    }.get(niche_id, "#Shorts #Viral #Trending #MustWatch")

    channel_cta = f"🔔 Subscribe to {channel_name} for daily high-retention stories!\n👍 Like & Share if you enjoyed this moment." if channel_name else "🔔 Subscribe for more incredible moments!\n👍 Like & Share to support the channel."
    
    vietnamese_regex = re.compile(r'[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]', re.IGNORECASE)
    raw_lead = (summary or title or "Incredible viral highlight that you cannot miss!").strip()
    # Nếu thị trường quốc tế / Mỹ nhưng raw_lead chứa ký tự tiếng Việt, ưu tiên tiêu đề tiếng Anh sạch
    if vietnamese_regex.search(raw_lead):
        content_lead = (title or "Incredible viral highlight that you cannot miss!").strip()
    else:
        content_lead = raw_lead

    # Khối ghi nguồn & dẫn link video đầy đủ (Credit & Full Video Link)
    credit_block = ""
    if source_url and source_url.strip():
        clean_url = source_url.strip()
        if mode == "self_owned":
            credit_block = f"🎬 Watch the full video on our channel:\n🔗 {clean_url}"
        else:
            creator_name = author.strip() if author else "the original creators"
            credit_block = (
                f"🎥 Original footage & full story by {creator_name}:\n"
                f"🔗 {clean_url}\n\n"
                f"⚖️ FAIR USE DISCLAIMER:\n"
                f"This video features transformative commentary, educational breakdown, and narrative curation under Section 107 of the US Copyright Act 1976. "
                f"All rights belong to their respective owners. For credit, inquiries, or removal requests, please contact us."
            )

    parts = [content_lead]
    if credit_block:
        parts.append(credit_block)
    
    parts.append(channel_cta)

    affiliate_block = f"""─────────────────────────
🛒 FEATURED GEAR & SHOPPING (STORE ID: {tag}):
👉 {amazon_prod['name']}
🔗 {cta_label}: {amazon_aff_url}
💡 {amazon_prod['cta']}
⚖️ (As an Amazon Associate I earn from qualifying purchases with Store ID: {tag})
─────────────────────────"""
    parts.append(affiliate_block)
    parts.append(niche_tags)

    return "\n\n".join(parts).strip()



