import logging
import re
import urllib.parse
from pathlib import Path
from typing import Optional
import requests
from config.settings import settings
from app.utils.key_rotator import KeyRotator

logger = logging.getLogger(__name__)


def get_niche_style_anchor(niche: str, prompt: str) -> tuple[str, str]:
    """
    Tự động chọn phong cách hình ảnh phù hợp với chủ đề & ngữ cảnh (thay vì ép cứng vũ trụ/Nolan).
    """
    p_lower = prompt.lower()
    n_lower = (niche or "").lower()

    # 1. Lịch sử / Nhân vật / Tư liệu
    if any(k in p_lower or k in n_lower for k in [
        "history", "historical", "vintage", "century", "war", "battle", "president",
        "leader", "soldier", "archive", "revolution", "tổng thống", "lịch sử",
        "hồ chí minh", "nguyễn ái quốc", "rockefeller", "napoleon", "einstein", "1911", "1945"
    ]):
        prefix = "Authentic historical documentary photography, period-accurate setting and clothing, "
        suffix = ", vintage 35mm film still, photorealistic 8k, natural dramatic museum lighting, ultra-sharp detail, no watermark, no text, no modern objects"
        return prefix, suffix

    # 2. Tâm lý học / Bí ẩn / Dark Psychology / Giật gân
    if any(k in p_lower or k in n_lower for k in [
        "psychology", "dark", "manipulate", "mystery", "secret", "detect", "liar",
        "mind", "thriller", "tâm lý", "bí ẩn"
    ]):
        prefix = "Cinematic psychological thriller still, David Fincher aesthetic, "
        suffix = ", dramatic chiaroscuro shadows, high contrast cinematic lighting, 35mm film still, photorealistic 8k, award-winning cinematography, no text, no watermark"
        return prefix, suffix

    # 3. Kinh doanh / Tài chính / Triệu phú / Forbes
    if any(k in p_lower or k in n_lower for k in [
        "business", "money", "wealth", "rich", "billionaire", "corporate", "investor",
        "kinh doanh", "tiền tệ", "tài chính"
    ]):
        prefix = "Prestigious luxury editorial corporate photography, Forbes aesthetic, "
        suffix = ", high-end architectural setting, elegant professional lighting, sharp 8k detail, cinematic composition, no text, no watermark"
        return prefix, suffix

    # 4. Khoa học / Vũ trụ / Thiên văn
    if any(k in p_lower or k in n_lower for k in [
        "space", "astronomy", "cosmos", "galaxy", "black hole", "star", "physics", "vũ trụ"
    ]):
        prefix = "Hyper-realistic scientific astrophotography, James Webb and IMAX clarity, "
        suffix = ", deep space volumetric lighting, photorealistic 8k, authentic cosmic phenomena, no cartoon, no text, no watermark"
        return prefix, suffix

    # 5. Mặc định: Điện ảnh điện ảnh cao cấp
    prefix = "Cinematic 8k vertical film still, 9:16 aspect ratio, "
    suffix = ", shot on 35mm anamorphic lens, masterwork composition, hyper-realistic details, dramatic lighting, no text, no watermark, no border"
    return prefix, suffix


class ImageAgent:
    """
    Agent sinh hình ảnh AI chất lượng cao chuẩn tỷ lệ dọc 9:16.
    Thích ứng phong cách động theo từng chủ đề kịch bản.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None
        self._call_count = 0

    def _sanitize_and_anchor(self, raw_prompt: str, niche: str = "general") -> str:
        """Làm sạch từ rác và bọc trong style anchor phù hợp ngữ cảnh."""
        cleaned = re.sub(r'[\r\n\t]+', ' ', raw_prompt).strip()
        cleaned = re.sub(r'\b(watermark|logo|text|border|frame)\b', '', cleaned, flags=re.IGNORECASE)
        prefix, suffix = get_niche_style_anchor(niche, cleaned)
        return f"{prefix}{cleaned}{suffix}"

    def generate_scene_image(
        self,
        prompt: str,
        output_path: str,
        aspect_ratio: str = "9:16",
        niche: str = "cinematic",
        scene_id: int = 0,
        visual_prompt: Optional[str] = None,
    ) -> Optional[str]:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        self._call_count += 1

        base_prompt = (
            visual_prompt.strip()
            if visual_prompt and len(visual_prompt.strip()) > 10
            else prompt.strip()
        )
        anchored = self._sanitize_and_anchor(base_prompt, niche)
        if scene_id == 1:
            lower_p = f"{base_prompt} {niche}".lower()
            if any(k in lower_p for k in ["sea", "ocean", "water", "deep", "abyss", "creature", "monster", "leviathan", "biển", "đại dương"]):
                hook_prefix = "Thalassophobia viral hook, colossal terrifying sea monster shadow lurking in dark ocean abyss beneath tiny diver, bioluminescent giant eye, "
            elif any(k in lower_p for k in ["woman", "girl", "female", "love", "attract", "seduce", "beauty", "phụ nữ", "quyến rũ", "gái"]):
                hook_prefix = "Alluring mysterious femme fatale viral hook, stunning beauty, seductive gaze over shoulder, mesmerizing piercing eyes, dramatic chiaroscuro noir lighting, "
            elif any(k in lower_p for k in ["money", "wealth", "rich", "billionaire", "gold", "tiền", "tài chính", "triệu phú"]):
                hook_prefix = "Billionaire luxury power viral hook, gleaming gold vault doors in dark shadows, raining cash, dark cinematic aesthetic, "
            elif any(k in lower_p for k in ["psychology", "liar", "lie", "manipulate", "betray", "dark", "secret", "tâm lý", "dối", "thao túng"]):
                hook_prefix = "Dark psychology viral hook, extreme macro close up of sharp intense unblinking eyes, whispering shadow, cold dangerous smirk, David Fincher lighting, "
            else:
                hook_prefix = "Viral YouTube Shorts hook frame, extreme close up, intense dramatic lighting, piercing focus, "
            anchored = f"{hook_prefix}{anchored}"

        # 1. Thử sinh ảnh bằng Pollinations với prompt tối ưu (chuẩn 9:16 HD 768x1344)
        try:
            words = anchored.split()
            compact_prompt = " ".join(words[:40])
            encoded_prompt = urllib.parse.quote(compact_prompt)

            url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=768&height=1344&nologo=true"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AI-Shorts-Studio/2.0"
            }

            res = requests.get(url, headers=headers, timeout=20)
            if res.status_code == 200 and len(res.content) > 5000:
                with open(output_path, "wb") as f:
                    f.write(res.content)
                logger.info(f"[ImageAgent] ✅ AI Image generated ({len(res.content):,} bytes) → {output_path}")
                return output_path
        except Exception as e:
            logger.warning(f"[ImageAgent] Lỗi Pollinations: {e}")

        # 2. Fallback: Thử với prompt ngắn gọn thuần túy nếu prompt dài bị lỗi
        try:
            short_clean = " ".join(base_prompt.split()[:18])
            prefix_fb = "viral hook extreme close up intense lighting vertical" if scene_id == 1 else "photorealistic 8k vertical"
            p_encoded = urllib.parse.quote(f"{prefix_fb} {short_clean}")
            url_fallback = f"https://image.pollinations.ai/prompt/{p_encoded}?width=768&height=1344&nologo=true"
            res = requests.get(url_fallback, headers={"User-Agent": "AI-Shorts/2.0"}, timeout=20)
            if res.status_code == 200 and len(res.content) > 5000:
                with open(output_path, "wb") as f:
                    f.write(res.content)
                logger.info(f"[ImageAgent] ✅ AI Image fallback generated → {output_path}")
                return output_path
        except Exception as fb_err:
            logger.debug(f"[ImageAgent] Fallback ngắn cũng lỗi: {fb_err}")

        return None
