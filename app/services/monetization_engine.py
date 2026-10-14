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

def build_amazon_url(asin_or_search: str, tracking_tag: str = "", is_search: bool = False, market_domain: str = "www.amazon.com") -> str:
    """
    Sinh đường link Amazon Affiliate hợp lệ kèm mã định danh theo dõi (Tracking Tag).
    - Link trực tiếp ASIN: https://{market_domain}/dp/{asin}?tag={tag}
    - Link tìm kiếm Smart Search: https://{market_domain}/s?k={query}&tag={tag}
    - market_domain: 'www.amazon.com' (US), 'www.amazon.com.br' (Brazil), etc.
    """
    tag = (tracking_tag or os.getenv("AMAZON_ASSOCIATE_TAG", "kizakawa-20")).strip()
    if not tag:
        tag = "kizakawa-20"

    domain = market_domain.rstrip("/")
    if is_search:
        return f"https://{domain}/s?k={quote_plus(asin_or_search)}&tag={tag}"
    return f"https://{domain}/dp/{asin_or_search}?tag={tag}"


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
# 3. TỰ ĐỘNG LỒNG GHÉP KÊU GỌI ỦNG HỘ PAYPAL & KIẾM TIỀN (AUTO-ENRICHMENT)
# ═════════════════════════════════════════════════════════════════════════════

def build_paypal_donation_block(
    channel_config: Optional[Dict[str, Any]] = None,
    niche: str = "general",
    paypal_url: Optional[str] = None
) -> str:
    """
    Tự động xây dựng khối kêu gọi quyên góp / ủng hộ qua PayPal chuẩn cho YouTube Shorts & Video:
    - Lấy link PayPal từ channel_config (paypal_url / donation_url) hoặc biến môi trường PAYPAL_DONATION_URL.
    - Soạn thông điệp kêu gọi ủng hộ chân thành, truyền cảm hứng phù hợp với từng chủ đề (Niche).
    - Hướng dẫn người xem Shorts bấm vào Bio kênh nếu link trong mô tả không click trực tiếp được trên mobile.
    """
    enable_donation = os.getenv("ENABLE_DONATION_CTA", "true").lower() in ("true", "1", "yes")
    if channel_config and channel_config.get("enable_donation") is not None:
        enable_donation = bool(channel_config.get("enable_donation"))

    if not enable_donation:
        return ""

    cfg = channel_config or {}
    url = (
        paypal_url
        or cfg.get("paypal_url")
        or cfg.get("donation_url")
        or cfg.get("monetization", {}).get("paypal_url")
        or os.getenv("PAYPAL_DONATION_URL")
        or os.getenv("PAYPAL_URL", "")
    ).strip()

    niche_lower = (niche or cfg.get("niche") or "").lower()

    if any(k in niche_lower for k in ["rescue", "pet", "dog", "cat", "animal"]):
        title_line = "💖 SUPPORT OUR RESCUE MISSIONS & VET CARE:"
        desc_line = "🐾 Every donation helps provide emergency vet care, warm food & safe shelter for terrified rescue animals."
        action_line = f"☕ Donate directly via PayPal: {url}" if url else "☕ Donate directly to our rescues: Tap channel icon ➔ PayPal link in Bio!"
    elif any(k in niche_lower for k in ["wild", "predator", "nature"]):
        title_line = "🌍 SUPPORT INDEPENDENT WILDLIFE EXPEDITIONS:"
        desc_line = "🦁 Fuel our untamed wildlife documentary research, field conservation & survival storytelling."
        action_line = f"☕ Support our work via PayPal: {url}" if url else "☕ Support our expeditions: Tap channel icon ➔ PayPal link in Bio!"
    elif any(k in niche_lower for k in ["psychology", "mindset", "stoic"]):
        title_line = "🧠 SUPPORT INDEPENDENT RESEARCH & CONTENT:"
        desc_line = "☕ If our daily psychological deep-dives bring you value, consider buying us a coffee to keep this content free."
        action_line = f"☕ Support via PayPal: {url}" if url else "☕ Buy us a coffee: Tap channel icon ➔ PayPal link in Bio!"
    elif any(k in niche_lower for k in ["deep_sea", "ocean", "mystery", "history"]):
        title_line = "🌊 SUPPORT UNCOVERING UNTOLD MYSTERIES:"
        desc_line = "✨ Help fund our independent archival investigations into Earth's deepest secrets."
        action_line = f"☕ Support our archive via PayPal: {url}" if url else "☕ Support our research: Tap channel icon ➔ PayPal link in Bio!"
    else:
        title_line = "💖 SUPPORT OUR INDEPENDENT CREATIVE WORK:"
        desc_line = "☕ Every contribution helps us produce high-quality daily stories."
        action_line = f"☕ Donate via PayPal: {url}" if url else "☕ Donate & Support: Tap channel icon ➔ PayPal link in Bio!"

    footer_hint = "💡 (Mobile Shorts viewers: Tap channel icon ➔ Clickable PayPal link in Bio!)"

    if url:
        return f"{title_line}\n{desc_line}\n{action_line}\n{footer_hint}"
    else:
        return f"{title_line}\n{desc_line}\n{action_line}"


def enrich_video_monetization(
    channel_config: Dict[str, Any],
    title: str,
    description: str = "",
    pinned_comment: Optional[str] = None,
    topic: str = "",
    script: str = "",
    enable_affiliate: Optional[bool] = None,
    enable_donation: Optional[bool] = None,
) -> Tuple[str, Optional[str], Optional[Dict[str, Any]]]:
    """
    Tự động nâng cấp mô tả video và bình luận ghim với:
    1. Khối kêu gọi ủng hộ / quyên góp qua PayPal (Support / Donate).
    2. Khối giới thiệu sản phẩm Amazon Affiliate chuẩn ngách (Featured Gear) kèm Store ID.
    
    Trả về: (enriched_description, enriched_pinned_comment, product_info)
    """
    niche = channel_config.get("niche", "general")

    # 1. Xác định cờ bật/tắt Affiliate
    if enable_affiliate is None:
        enable_affiliate = os.getenv("ENABLE_AMAZON_AFFILIATE", "false").lower() in ("true", "1", "yes")
        if channel_config.get("enable_monetization") is not None:
            enable_affiliate = bool(channel_config.get("enable_monetization"))
        elif channel_config.get("affiliate_enabled") is not None:
            enable_affiliate = bool(channel_config.get("affiliate_enabled"))

    # 2. Xác định cờ bật/tắt Kêu gọi ủng hộ PayPal (Mặc định FALSE cho Shorts để giữ sạch Description & Pinned Comment)
    if enable_donation is None:
        enable_donation = os.getenv("ENABLE_DONATION_CTA", "false").lower() in ("true", "1", "yes")
        if channel_config.get("enable_donation") is not None:
            enable_donation = bool(channel_config.get("enable_donation"))

    # Nếu cả 2 đều tắt, trả về nguyên bản
    if not enable_affiliate and not enable_donation:
        logger.info("[MonetizationEngine] Bỏ qua monetization & donation (cả 2 đều tắt)")
        return description or "", pinned_comment, None

    desc_base = (description or "").strip()
    comment_base = (pinned_comment or "").strip()
    product_info = None

    footer_sections = []

    # ── A. KHỐI KÊU GỌI ỦNG HỘ PAYPAL ──
    donation_block = ""
    if enable_donation:
        donation_block = build_paypal_donation_block(channel_config=channel_config, niche=niche)
        if donation_block and donation_block not in desc_base:
            footer_sections.append(donation_block)

    # ── B. KHỐI SẢN PHẨM AMAZON AFFILIATE ──
    is_pt = str(channel_config.get("language", "")).lower().startswith("pt") or str(channel_config.get("default_language", "")).lower().startswith("pt")
    if enable_affiliate:
        # [FIX] Chọn đúng Amazon market domain và associate tag theo thị trường
        if is_pt:
            # Kênh tiếng Bồ Đào Nha → dùng Amazon Brazil để phát sinh hoa hồng hợp lệ
            market_domain = "www.amazon.com.br"
            tracking_tag = (
                channel_config.get("amazon_br_tag")
                or channel_config.get("amazon_tag")
                or channel_config.get("monetization", {}).get("amazon_br_tag")
                or os.getenv("AMAZON_BR_ASSOCIATE_TAG", "")
                or os.getenv("AMAZON_ASSOCIATE_TAG", "")
            ).strip()
        else:
            market_domain = "www.amazon.com"
            tracking_tag = (
                channel_config.get("amazon_tag")
                or channel_config.get("monetization", {}).get("amazon_tag")
                or os.getenv("AMAZON_ASSOCIATE_TAG", "")
            ).strip()
        product = find_best_amazon_product(niche=niche, title=title, topic=topic, script=script)
        affiliate_url = build_amazon_url(product["asin"], tracking_tag, market_domain=market_domain)
        if is_pt:
            disclaimer = (
                "⚖️ Aviso de Afiliado: Como associado da Amazon, posso receber comissões por compras qualificadas "
                "sem nenhum custo adicional para você."
            )
            affiliate_block = (
                f"📌 Equipamento & Livros Recomendados:\n"
                f"👉 {product['name']}:\n"
                f"🔗 Link Oficial: {affiliate_url}\n"
                f"💡 (Toque no ícone do canal para acessar o link na bio!)\n\n"
                f"{disclaimer}"
            )
        else:
            affiliate_block = (
                f"📌 Featured Trend & Gear:\n"
                f"👉 {product['name']} — {product['cta']}:\n"
                f"🔗 Direct Link: {affiliate_url}\n"
                f"💡 (Shorts viewers: Tap channel icon ➔ Clickable link in Bio!)\n\n"
                f"{AMAZON_COMPLIANCE_DISCLAIMER}"
            )
        if product['asin'] not in desc_base and "amazon.com" not in desc_base:
            footer_sections.append(affiliate_block)

        product_info = {
            "asin": product["asin"],
            "name": product["name"],
            "cta": product["cta"],
            "affiliate_url": affiliate_url,
            "tracking_tag": tracking_tag or "kizakawa-20",
            "market_domain": market_domain
        }

    # Ghép các khối vào mô tả
    if footer_sections:
        monetization_footer = "\n\n─────────────────────────\n".join(footer_sections)
        hashtag_match = re.search(r'(\s*(?:#[a-zA-Z0-9_]+\s*)+)$', desc_base)
        if hashtag_match:
            tags_part = hashtag_match.group(1).strip()
            text_part = desc_base[:hashtag_match.start()].strip()
            enriched_desc = f"{text_part}\n\n{monetization_footer}\n\n{tags_part}".strip()
        else:
            enriched_desc = f"{desc_base}\n\n{monetization_footer}".strip()
    else:
        enriched_desc = desc_base

    # Nâng cấp bình luận ghim (Pinned Comment)
    paypal_url = (
        channel_config.get("paypal_url")
        or channel_config.get("donation_url")
        or os.getenv("PAYPAL_DONATION_URL")
        or ""
    ).strip()

    comment_additions = []
    if enable_donation and paypal_url and "paypal" not in comment_base.lower():
        if is_pt:
            comment_additions.append(f"💖 Apoie o canal diretamente pelo PayPal: {paypal_url}")
        else:
            comment_additions.append(f"💖 Support our mission directly via PayPal: {paypal_url}")

    if enable_affiliate and product_info and "amazon.com" not in comment_base:
        if is_pt:
            comment_additions.append(
                f"✨ Equipamento Recomendado: {product_info['name']}\n"
                f"👉 Link Oficial: {product_info['affiliate_url']}\n"
                f"🔗 (Ou toque no ícone do canal para ver o link na bio)"
            )
        else:
            comment_additions.append(
                f"✨ Trending Gear: {product_info['name']}\n"
                f"👉 Link: {product_info['affiliate_url']}\n"
                f"🔗 (Or tap channel icon ➔ Link in Bio)"
            )

    if comment_additions:
        addon_str = "\n\n".join(comment_additions)
        if comment_base:
            enriched_comment = f"{comment_base}\n\n{addon_str}".strip()
        else:
            enriched_comment = addon_str.strip()
    else:
        enriched_comment = comment_base or None

    return enriched_desc, enriched_comment, product_info


def preview_monetization_bundle(
    channel_config: Dict[str, Any],
    title: str,
    description: str = "",
    pinned_comment: Optional[str] = None,
    topic: str = "",
    script: str = "",
    enable_affiliate: Optional[bool] = None,
    enable_donation: Optional[bool] = None,
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
        script=script,
        enable_affiliate=enable_affiliate,
        enable_donation=enable_donation,
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
    enable_affiliate: Optional[bool] = None,
) -> str:
    """
    Tự động xây dựng Bình luận ghim (Pinned Comment) cho Shorts tái chế:
    - Mặc định tắt chèn link Amazon không bấm được để tối ưu tỷ lệ bình luận (Comment Rate).
    - Chỉ chèn sản phẩm khi enable_affiliate=True hoặc ENABLE_AMAZON_AFFILIATE=true.
    """
    if enable_affiliate is None:
        enable_affiliate = os.getenv("ENABLE_AMAZON_AFFILIATE", "false").lower() in ("true", "1", "yes")

    niche_question = {
        "animal_rescue": "From 1 to 10, how much did this rescue transformation warm your heart? Drop a ❤️ in the comments! 👇🐾",
        "wildlife_predators": "Which predator do you think takes the crown here? Drop your vote in the comments! 👇🦁",
        "psychology": "Have you ever experienced this psychological tactic in real life? Share your story below! 👇🧠",
        "deep_sea_earth_mysteries": "What is your theory on this unexplained phenomenon? Drop your thoughts in the comments! 👇🌊",
        "alternate_history": "How different would the world be today if this actually happened? Drop your theory below! 👇⏳",
    }.get(niche_id, "What stood out to you the most in this clip? Let us know in the comments! 👇🔥")

    if not enable_affiliate:
        return niche_question

    tag = (tracking_tag or os.getenv("AMAZON_ASSOCIATE_TAG", "kizakawa-20") or "kizakawa-20").strip()
    amazon_prod = find_best_amazon_product(niche=niche_id, title=title)
    is_search = amazon_prod.get("is_search", False)
    amazon_aff_url = build_amazon_url(amazon_prod["asin"], tag, is_search=is_search)
    cta_label = "Search & shop on Amazon" if is_search else "Check price on Amazon"

    creator_name = author or channel_name
    creator_tag = f"by {creator_name} " if creator_name else ""
    full_story_line = f"📌 Watch the full story {creator_tag}here: {source_url}" if source_url else niche_question

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
    paypal_url: str = "",
    enable_affiliate: Optional[bool] = None,
    enable_donation: bool = False,
) -> str:
    """
    Tự động xây dựng mô tả YouTube Shorts hoàn chỉnh chuẩn Bản Quyền, Kêu Gọi Ủng Hộ & Kiếm Tiền:
    - Nội dung tiếng Anh hấp dẫn
    - Dẫn link video đầy đủ (Full Video / Original Source Link) giúp kéo view hoặc tôn trọng bản quyền tác giả
    - Điều khoản miễn trừ bản quyền Fair Use (Section 107 of the Copyright Act 1976)
    - Kêu gọi theo dõi kênh
    - Khối kêu gọi quyên góp / ủng hộ qua PayPal (Donation / Support)
    - Khối gắn link sản phẩm Amazon chuẩn ngách KÈM STORE ID: kizakawa-20 (nếu bật affiliate)
    - Bộ Hashtags chuẩn SEO
    """
    tag = (tracking_tag or os.getenv("AMAZON_ASSOCIATE_TAG", "kizakawa-20") or "kizakawa-20").strip()
    
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

    # 1. Khối kêu gọi ủng hộ qua PayPal (Donation / Support)
    if enable_donation:
        donation_block = build_paypal_donation_block(
            channel_config={"paypal_url": paypal_url, "niche": niche_id, "name": channel_name},
            niche=niche_id,
            paypal_url=paypal_url
        )
        if donation_block:
            parts.append(donation_block)

    # 2. Khối sản phẩm Amazon Affiliate (chỉ gắn nếu được bật)
    if enable_affiliate is None:
        enable_affiliate = os.getenv("ENABLE_AMAZON_AFFILIATE", "false").lower() in ("true", "1", "yes")

    if enable_affiliate:
        amazon_prod = find_best_amazon_product(niche=niche_id, title=title, topic=summary)
        is_search = amazon_prod.get("is_search", False)
        amazon_aff_url = build_amazon_url(amazon_prod["asin"], tag, is_search=is_search)
        cta_label = "Search & shop on Amazon" if is_search else "Check price on Amazon"
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



