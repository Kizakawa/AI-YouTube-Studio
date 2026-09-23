import logging
import urllib.parse
from pathlib import Path
from typing import Optional
import requests
from config.settings import settings
from app.utils.key_rotator import KeyRotator

logger = logging.getLogger(__name__)

class ImageAgent:
    """
    Agent sinh hình ảnh AI chất lượng cao chuẩn tỷ lệ dọc 9:16:
    - Ưu tiên 1: Google Imagen 3 / Gemini Image (nếu tài khoản Google Cloud Ultra đã kích hoạt).
    - Ưu tiên 2 (Tự động Fallback): Pollinations AI Cinematic Portrait Engine (miễn phí 100%, không cần key, tạo ảnh 9:16 điện ảnh sắc nét).
    - Đảm bảo video luôn có hình ảnh sống động, không bị slide chữ đơn điệu.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys)

    def generate_scene_image(
        self,
        prompt: str,
        output_path: str,
        aspect_ratio: str = "9:16",
        niche: str = "cinematic"
    ) -> Optional[str]:
        """
        Sinh ảnh dọc 9:16 bám sát nội dung phân cảnh.
        Tự động fallback nếu Google API hết hạn ngạch.
        """
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        enhanced_prompt = (
            f"Cinematic {niche} shot, 8k resolution, photorealistic, dramatic volumetric lighting, "
            f"masterpiece, highly detailed: {prompt}"
        )

        # ── 1. Thử nghiệm qua Google Imagen 3 nếu có key ──
        current_key = self.rotator.get_key()
        if current_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=current_key)
                # Thử nghiệm Imagen 3 model
                logger.info(f"Đang thử sinh ảnh qua Google Imagen 3: '{prompt[:40]}...'")
                model = genai.ImageGenerationModel("imagen-3.0-generate-002")
                result = model.generate_images(prompt=enhanced_prompt, aspect_ratio="9:16", number_of_images=1)
                if result and result.images:
                    result.images[0].save(output_path)
                    if Path(output_path).exists() and Path(output_path).stat().st_size > 1000:
                        logger.info(f"✅ Đã sinh ảnh thành công bằng Google Imagen 3 → {output_path}")
                        return output_path
            except Exception as e:
                logger.info(f"Google Imagen 3 không khả dụng (Lỗi: {e}). Chuyển sang AI Portrait Engine...")

        # ── 2. Fallback sang AI Portrait Engine (Pollinations 9:16) ──
        try:
            logger.info(f"🎨 Đang sinh ảnh AI điện ảnh 9:16: '{prompt[:40]}...'")
            encoded_prompt = urllib.parse.quote(enhanced_prompt)
            # Dùng độ phân giải dọc 720x1280 (chuẩn 9:16 cho Shorts, Ken Burns sẽ upscale 1080x1920)
            url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=720&height=1280&nologo=true"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AI-Shorts-Bot/1.0"}
            
            res = requests.get(url, headers=headers, timeout=25)
            if res.status_code == 200 and len(res.content) > 5000:
                with open(output_path, "wb") as f:
                    f.write(res.content)
                logger.info(f"✅ Đã tạo ảnh AI thành công ({len(res.content)} bytes) → {output_path}")
                return output_path
            else:
                logger.warning(f"AI Portrait Engine trả về mã {res.status_code}, dung lượng {len(res.content)} bytes")
        except Exception as e:
            logger.warning(f"Lỗi khi gọi AI Portrait Engine: {e}")

        return None

