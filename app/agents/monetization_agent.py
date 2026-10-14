"""
app.agents.monetization_agent — Tác Nhân Phụ Trách Kiếm Tiền, Tìm Kiếm Sản Phẩm & Đề Xuất Giỏ Hàng
(E-Commerce, Affiliate & Monetization Agent)

Nhiệm vụ cốt lõi:
1. Phân tích Ý Định Mua Hàng Tiềm Ẩn (Buyer Intent) từ kịch bản, tiêu đề và cảm xúc người xem.
2. Tự động sinh bộ từ khóa tìm kiếm sản phẩm (Product Search Keywords) có tỷ lệ chuyển đổi cao nhất.
3. Đề xuất sản phẩm Amazon / TikTok Shop / Sản phẩm số tương thích 100% với nội dung video.
4. Sáng tạo câu kêu gọi hành động (Conversational Non-Pushy CTA) tự nhiên, kích thích bấm link.
5. Soạn sẵn nội dung Bình luận ghim (Pinned Comment) và Mô tả video kèm câu miễn trừ trách nhiệm (Disclaimer).
"""

import json
import logging
import re
from typing import Dict, Any, List, Optional, Tuple
from config.settings import settings
from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS
from app.services.monetization_engine import (
    find_best_amazon_product,
    build_amazon_url,
    AMAZON_COMPLIANCE_DISCLAIMER,
    NICHE_PRODUCT_CATALOG
)

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = list(GEMINI_PRIORITY_MODELS)


class MonetizationAgent:
    """
    Tác nhân AI chuyên trách thương mại điện tử, gắn link affiliate và tối ưu giỏ hàng cho kênh.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None

    def _call_gemini(self, prompt: str, timeout: int = 20) -> Optional[str]:
        """Gọi Gemini REST API với KeyRotator ưu tiên Model mạnh nhất trước."""
        if not self.rotator:
            return None

        import requests
        for model, key in self.rotator.iter_best_model_key_pairs(AVAILABLE_MODELS):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.4,
                    "maxOutputTokens": 1024,
                }
            }
            try:
                resp = requests.post(url, json=payload, timeout=timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            return parts[0].get("text", "")
                elif resp.status_code in (429, 403, 404, 503):
                    self.rotator.mark_model_rate_limited(key, model, status_code=resp.status_code, error_text=resp.text[:250])
                    continue
            except Exception as e:
                logger.debug(f"MonetizationAgent: Lỗi kết nối {model} (key: ...{key[-6:]}): {e}")
                continue

        return None

    def analyze_and_recommend(
        self,
        title: str,
        topic: str = "",
        script: str = "",
        niche: str = "psychology",
        tracking_tag: str = ""
    ) -> Dict[str, Any]:
        """
        Phân tích chuyên sâu kịch bản và đưa ra gói đề xuất kiếm tiền (Monetization Bundle):
        1. Gọi AI phân tích Buyer Intent và sinh từ khóa sản phẩm.
        2. Khớp với sản phẩm Amazon tối ưu nhất hoặc link Smart Search.
        3. Soạn câu CTA tự nhiên và Pinned Comment chuyển đổi cao.
        """
        # 1. Tra cứu sản phẩm trong kho catalog có sẵn trước (Base Catalog Match)
        base_product = find_best_amazon_product(niche=niche, title=title, topic=topic, script=script)
        affiliate_url = build_amazon_url(base_product["asin"], tracking_tag)

        # 2. Xây dựng prompt cho AI E-Commerce Director
        prompt = f"""You are an Elite E-Commerce & Affiliate Marketing Director for YouTube Shorts, TikTok Shop, and Amazon Associates.
Analyze this YouTube Short to propose the single highest-converting product recommendation and a smooth, non-pushy Call-to-Action (CTA).

[VIDEO CONTEXT]
- Channel Niche: {niche}
- Title: {title}
- Topic: {topic}
- Script Snippet: {script[:400] if script else "N/A"}

[DEFAULT BEST-SELLER IN OUR CATALOG]
- Product: {base_product['name']}
- ASIN: {base_product['asin']}

[YOUR TASK]
Return a strictly valid JSON object with the following fields:
{{
  "buyer_intent": "Brief explanation of what the viewer emotionally needs or craves after watching this video (1 sentence)",
  "search_keywords": ["keyword 1", "keyword 2", "keyword 3"],
  "recommended_product_name": "{base_product['name']}",
  "conversational_cta": "A compelling 1-sentence hook that connects the video's climax to the product naturally without sounding like a salesperson",
  "pinned_comment_copy": "Engaging pinned comment asking a viewer question, then recommending the product with [LINK]",
  "suggested_cart_tags": ["tag1", "tag2", "tag3"]
}}

Only respond with the JSON object. Do not add markdown backticks if possible.
"""
        ai_raw = self._call_gemini(prompt, timeout=15)
        ai_data: Dict[str, Any] = {}

        if ai_raw:
            try:
                clean_json = re.sub(r"^```(?:json)?\s*", "", ai_raw.strip(), flags=re.MULTILINE)
                clean_json = re.sub(r"\s*```$", "", clean_json.strip(), flags=re.MULTILINE)
                ai_data = json.loads(clean_json)
            except Exception as e:
                logger.debug(f"MonetizationAgent: Không parse được JSON từ AI: {e}")

        # 3. Tổng hợp kết quả hoàn chỉnh
        prod_name = ai_data.get("recommended_product_name") or base_product["name"]
        cta = ai_data.get("conversational_cta") or base_product["cta"]
        intent = ai_data.get("buyer_intent") or "Viewer seeks self-protection or curiosity resolution."
        keywords = ai_data.get("search_keywords") or [base_product["search_fallback"]]
        cart_tags = ai_data.get("suggested_cart_tags") or [niche, "amazon", "recommendation"]

        # Xử lý Pinned Comment Copy
        raw_pinned = ai_data.get("pinned_comment_copy") or ""
        if "[LINK]" in raw_pinned:
            pinned_comment = raw_pinned.replace("[LINK]", affiliate_url)
        elif raw_pinned:
            pinned_comment = f"{raw_pinned}\n👉 Check on Amazon: {affiliate_url}"
        else:
            pinned_comment = (
                f"🔥 {cta}\n"
                f"👉 {prod_name} on Amazon: {affiliate_url}"
            )

        # Xử lý Description Footer
        description_footer = (
            f"\n\n📌 Recommended Gear & Reading:\n"
            f"👉 {prod_name} — {cta}:\n"
            f"🔗 {affiliate_url}\n\n"
            f"{AMAZON_COMPLIANCE_DISCLAIMER}"
        )

        return {
            "status": "success",
            "niche": niche,
            "buyer_intent": intent,
            "search_keywords": keywords,
            "product_name": prod_name,
            "asin": base_product["asin"],
            "affiliate_url": affiliate_url,
            "conversational_cta": cta,
            "pinned_comment": pinned_comment,
            "description_footer": description_footer,
            "cart_tags": cart_tags,
            "tracking_tag": tracking_tag or "(Chưa có tag - Dùng link trực tiếp)"
        }


# Khởi tạo singleton instance tiện dùng trong toàn hệ thống
monetization_agent = MonetizationAgent()
