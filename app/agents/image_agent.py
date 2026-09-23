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

        # ── 1. Thử nghiệm qua Google GenAI (Gemini Image Models / Imagen 3) ──
        current_key = self.rotator.get_key()
        if current_key:
            try:
                from google import genai
                client = genai.Client(api_key=current_key)
                
                # Thử các mô hình hình ảnh chính thức của Google
                for model_name in ["gemini-2.5-flash-image", "gemini-3.1-flash-image", "imagen-3.0-generate-002"]:
                    try:
                        logger.info(f"Đang gọi mô hình Google AI '{model_name}': '{prompt[:40]}...'")
                        response = client.models.generate_content(
                            model=model_name,
                            contents=f"Generate a vertical 9:16 cinematic photorealistic shot, 8k: {enhanced_prompt}",
                        )
                        if response and response.candidates:
                            for part in response.candidates[0].content.parts:
                                if hasattr(part, "inline_data") and part.inline_data and part.inline_data.data:
                                    with open(output_path, "wb") as f:
                                        f.write(part.inline_data.data)
                                    if Path(output_path).exists() and Path(output_path).stat().st_size > 1000:
                                        logger.info(f"✅ Đã tạo ảnh thành công bằng Google AI ({model_name}) → {output_path}")
                                        return output_path
                    except Exception as model_err:
                        # Log nhẹ để không làm tắc luồng
                        logger.debug(f"Mô hình {model_name} chưa hỗ trợ trên key này: {model_err}")
            except Exception as e:
                logger.info(f"Bộ công cụ Google AI Image không khả dụng trên key hiện tại ({e}). Tự động chuyển sang AI Portrait Engine...")

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

