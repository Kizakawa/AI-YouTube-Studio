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

        # 1. ƯU TIÊN SỐ 1: Thử gọi trực tiếp công cụ tạo ảnh của Google (Gemini Flash/Pro Image)
        google_res = self._try_google_image_api(base_prompt, output_path, aspect_ratio=aspect_ratio)
        if google_res and Path(google_res).exists() and Path(google_res).stat().st_size > 5000:
            return google_res

        # 2. DỰ PHÒNG CHẤT LƯỢNG CAO: Thử sinh ảnh bằng Pollinations với prompt tối ưu (chuẩn 9:16 HD 768x1344)
        # Cho phép tới 75 từ để bảo toàn trọn vẹn mô tả của AI Director (không bị cắt vụn)
        try:
            words = anchored.split()
            compact_prompt = " ".join(words[:75])
            encoded_prompt = urllib.parse.quote(compact_prompt)

            url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=768&height=1344&nologo=true"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AI-Shorts-Studio/2.0"
            }

            res = requests.get(url, headers=headers, timeout=12)
            if res.status_code == 200 and len(res.content) > 5000:
                with open(output_path, "wb") as f:
                    f.write(res.content)
                logger.info(f"[ImageAgent] ✅ AI Image generated ({len(res.content):,} bytes) → {output_path}")
                return output_path
        except Exception as e:
            logger.warning(f"[ImageAgent] Lỗi Pollinations: {e}")

        # 3. Fallback: Thử với prompt ngắn gọn thuần túy nếu prompt dài bị nghẽn
        try:
            short_clean = " ".join(base_prompt.split()[:20])
            prefix_fb = "viral hook intense dramatic lighting 8k vertical" if scene_id == 1 else "photorealistic 8k vertical cinema"
            p_encoded = urllib.parse.quote(f"{prefix_fb} {short_clean}")
            url_fallback = f"https://image.pollinations.ai/prompt/{p_encoded}?width=768&height=1344&nologo=true"
            res = requests.get(url_fallback, headers={"User-Agent": "AI-Shorts/2.0"}, timeout=8)
            if res.status_code == 200 and len(res.content) > 5000:
                with open(output_path, "wb") as f:
                    f.write(res.content)
                logger.info(f"[ImageAgent] ✅ AI Image fallback generated → {output_path}")
                return output_path
        except Exception as fb_err:
            logger.debug(f"[ImageAgent] Fallback ngắn cũng lỗi: {fb_err}")

        # 4. Ultimate Fallback (Không bao giờ lỗi): Tạo khung nền điện ảnh 1080x1920 bằng Pillow
        try:
            from PIL import Image, ImageDraw
            width, height = 1080, 1920
            base = Image.new("RGB", (width, height))
            draw = ImageDraw.Draw(base)
            top_color = (12, 17, 30)
            bottom_color = (30, 27, 75)
            for y in range(0, height, 4):
                ratio = y / height
                r = int(top_color[0] + (bottom_color[0] - top_color[0]) * ratio)
                g = int(top_color[1] + (bottom_color[1] - top_color[1]) * ratio)
                b = int(top_color[2] + (bottom_color[2] - top_color[2]) * ratio)
                draw.line([(0, y), (width, y)], fill=(r, g, b), width=4)
            base.save(output_path, quality=95)
            logger.info(f"[ImageAgent] 🛡️ Đã tạo frame điện ảnh dự phòng an toàn (PIL) → {output_path}")
            return output_path
        except Exception as pil_err:
            logger.debug(f"[ImageAgent] Lỗi tạo ảnh dự phòng PIL: {pil_err}")

        return None

    def _try_google_image_api(self, prompt: str, output_path: str, aspect_ratio: str = "9:16") -> Optional[str]:
        """
        Gọi trực tiếp công cụ tạo ảnh chính hãng của Google (Gemini Flash/Pro Image).
        Hỗ trợ các model mới nhất của Google:
        - gemini-2.5-flash-image
        - gemini-3.1-flash-image
        - gemini-3-pro-image
        Tự động xoay vòng qua danh sách API Key của người dùng.
        """
        if not self.rotator or not self.rotator.has_keys:
            return None

        import base64

        models_to_try = [
            "gemini-2.5-flash-image",
            "gemini-3.1-flash-image",
            "gemini-3-pro-image",
            "gemini-3.1-flash-lite-image",
        ]

        total_keys = len(self.rotator.keys)
        for _ in range(min(total_keys, 3)):
            api_key = self.rotator.get_key()
            if not api_key:
                break

            for model in models_to_try:
                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                    image_prompt = (
                        f"Ultra-high quality photorealistic 8k vertical 9:16 cinematic image, "
                        f"award-winning photography, rich details, dramatic lighting, no text, no watermark: {prompt}"
                    )
                    payload = {
                        "contents": [
                            {
                                "parts": [
                                    {"text": image_prompt}
                                ]
                            }
                        ],
                        "generationConfig": {
                            "responseMimeType": "image/jpeg"
                        }
                    }
                    headers = {"Content-Type": "application/json"}
                    res = requests.post(url, json=payload, headers=headers, timeout=25)

                    if res.status_code == 200:
                        data = res.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            for part in parts:
                                inline = part.get("inlineData") or part.get("inline_data")
                                if inline and inline.get("data"):
                                    img_data = base64.b64decode(inline["data"])
                                    if len(img_data) > 5000:
                                        with open(output_path, "wb") as f:
                                            f.write(img_data)
                                        logger.info(f"✨ [ImageAgent] ✅ Google Image ({model}) thành công: {output_path} ({len(img_data):,} bytes)")
                                        return output_path
                    elif res.status_code == 429:
                        logger.debug(f"[ImageAgent] Google Image {model} 429 (Cần bật Google Cloud Billing Pay-As-You-Go để kích hoạt quota ảnh Google).")
                        break
                    else:
                        logger.debug(f"[ImageAgent] Google Image {model} status {res.status_code}: {res.text[:120]}")
                except Exception as e:
                    logger.debug(f"[ImageAgent] Lỗi khi gọi Google Image {model}: {e}")

            # Thử key tiếp theo nếu key hiện tại hết quota
            self.rotator.rotate_key()

        return None

