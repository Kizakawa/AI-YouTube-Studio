import logging
import re
import urllib.parse
from pathlib import Path
from typing import Optional
import requests
from config.settings import settings
from app.utils.key_rotator import KeyRotator

logger = logging.getLogger(__name__)


def master_cinematic_prompt(raw_prompt: str, scene_id: int = 0) -> str:
    """
    Chuẩn hóa prompt cho AI tạo ảnh theo phong cách điện ảnh dọc 9:16 siêu thực.
    HOÀN TOÀN ĐỘNG THEO NỘI DUNG DO AI DIRECTOR ĐỀ XUẤT — Không hardcode bất kỳ chủ đề cụ thể nào.
    """
    cleaned = re.sub(r'[\r\n\t]+', ' ', raw_prompt).strip()
    cleaned = re.sub(r'\b(watermark|logo|text|border|frame|subtitles)\b', '', cleaned, flags=re.IGNORECASE)

    if scene_id == 1:
        # Cảnh mở màn Hook 3 giây đầu: Kích hoạt tối đa độ căng thẳng thị giác và tương phản
        prefix = (
            "Ultra-high retention YouTube Shorts opening hook frame, 9:16 vertical orientation, "
            "cinematic 8k masterpiece, extreme visual intensity, dramatic chiaroscuro lighting, "
            "razor-sharp focus, award-winning cinematography: "
        )
    else:
        # Cảnh thông thường: Điện ảnh cao cấp, chân thực, giữ đúng chi tiết bối cảnh
        prefix = (
            "Cinematic 8k vertical film still, 9:16 aspect ratio, masterwork composition, "
            "hyper-realistic details, dramatic lighting, 35mm photography: "
        )

    suffix = ", photorealistic 8k, ultra-sharp detail, no watermark, no text, no border"
    return f"{prefix}{cleaned}{suffix}"


class ImageAgent:
    """
    Agent sinh hình ảnh AI chất lượng cao chuẩn tỷ lệ dọc 9:16.
    Thích ứng phong cách động 100% theo prompt của AI Director cho bất kỳ chủ đề nào.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None
        self._call_count = 0

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

        # Ưu tiên visual_prompt chi tiết từ AI Director
        base_prompt = (
            visual_prompt.strip()
            if visual_prompt and len(visual_prompt.strip()) > 10
            else prompt.strip()
        )

        anchored = master_cinematic_prompt(base_prompt, scene_id=scene_id)

        # 1. Thử sinh ảnh bằng Pollinations với prompt tối ưu (chuẩn 9:16 HD 768x1344)
        # Cho phép tới 75 từ để bảo toàn trọn vẹn mô tả của AI Director (không bị cắt vụn)
        try:
            words = anchored.split()
            compact_prompt = " ".join(words[:75])
            encoded_prompt = urllib.parse.quote(compact_prompt)

            url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=768&height=1344&nologo=true"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AI-Shorts-Studio/2.0"
            }

            res = requests.get(url, headers=headers, timeout=25)
            if res.status_code == 200 and len(res.content) > 5000:
                with open(output_path, "wb") as f:
                    f.write(res.content)
                logger.info(f"[ImageAgent] ✅ AI Image generated ({len(res.content):,} bytes) → {output_path}")
                return output_path
        except Exception as e:
            logger.warning(f"[ImageAgent] Lỗi Pollinations: {e}")

        # 2. Fallback: Thử với prompt ngắn gọn thuần túy nếu prompt dài bị nghẽn
        try:
            short_clean = " ".join(base_prompt.split()[:20])
            prefix_fb = "viral hook intense dramatic lighting 8k vertical" if scene_id == 1 else "photorealistic 8k vertical cinema"
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
