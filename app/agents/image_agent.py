import logging
import urllib.parse
from pathlib import Path
from typing import Optional
import requests
from config.settings import settings
from app.utils.key_rotator import KeyRotator

logger = logging.getLogger(__name__)

# ── Visual Style Anchor — cố định phong cách cinematic cho TẤT CẢ cảnh ──────
# Bọc cứng mọi prompt AI để đảm bảo nhất quán phong cách, không cartoon/anime/biến dạng
VISUAL_STYLE_PREFIX = (
    "Cinematic 8K film still, Christopher Nolan Interstellar style, "
    "dark cosmic sci-fi thriller, dramatic volumetric god rays, "
    "photorealistic, IMAX 70mm film grain, shallow depth of field, "
    "9:16 vertical portrait format, ultra high detail, "
)
VISUAL_STYLE_SUFFIX = (
    ", professional color grading, teal and orange cinematic LUT, "
    "no cartoon, no anime, no 2D illustration, no distorted anatomy, "
    "no text overlay, no watermark, no logo, no border, "
    "highly detailed faces and textures"
)

# Pollinations: luôn kèm model=flux (photorealistic tốt nhất), nologo=true, enhance=true
POLLINATIONS_MODEL   = "flux"
POLLINATIONS_BASE    = "https://image.pollinations.ai/prompt/{prompt}"
POLLINATIONS_PARAMS  = "width={w}&height={h}&model={model}&nologo=true&enhance=true&seed={seed}"


class ImageAgent:
    """
    Agent sinh hình ảnh AI chất lượng cao chuẩn tỷ lệ dọc 9:16.

    Cơ chế Visual Style Anchor:
      - Mọi prompt đầu vào được bọc trong VISUAL_STYLE_PREFIX + suffix cứng
        để giữ phong cách cinematic nhất quán giữa các cảnh.
      - Tuyệt đối không để watermark Pollinations: luôn kèm &nologo=true&model=flux
      - Fallback thứ tự: Google Imagen → Pollinations Flux
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys)
        self._call_count = 0  # Dùng làm seed đa dạng ảnh

    def _build_anchored_prompt(self, raw_prompt: str) -> str:
        """
        Bọc prompt thô trong Visual Style Anchor.
        Loại bỏ các từ nguy hiểm gây biến dạng trước khi ghép.
        """
        # Danh sách từ khóa trừu tượng/nguy hiểm → thay bằng cụ thể hơn
        danger_terms = {
            "spaghettification": "gravitational tidal distortion near black hole event horizon",
            "visual representation": "dramatic cinematic scene showing",
            "abstract concept": "striking symbolic imagery of",
            "representation of": "cinematic depiction of",
            "visualization": "dramatic cinematic shot of",
        }
        cleaned = raw_prompt.lower()
        for danger, replacement in danger_terms.items():
            if danger in cleaned:
                raw_prompt = raw_prompt.replace(danger, replacement)
                cleaned = cleaned.replace(danger, replacement)

        return f"{VISUAL_STYLE_PREFIX}{raw_prompt}{VISUAL_STYLE_SUFFIX}"

    def generate_scene_image(
        self,
        prompt: str,
        output_path: str,
        aspect_ratio: str = "9:16",
        niche: str = "cinematic",
        scene_id: int = 0,
    ) -> Optional[str]:
        """
        Sinh ảnh dọc 9:16 bám sát nội dung phân cảnh với phong cách cinematic nhất quán.

        Args:
            prompt:      Visual keyword từ Content Agent (mô tả cảnh quay cụ thể)
            output_path: Đường dẫn lưu ảnh
            aspect_ratio: Luôn là 9:16
            niche:       Niche kênh (thông tin thêm cho prompt prefix)
            scene_id:    ID cảnh để tạo seed đa dạng (không trùng lặp ảnh giữa scenes)
        """
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        self._call_count += 1

        # Anchored prompt — nhất quán 100% phong cách giữa các cảnh
        anchored_prompt = self._build_anchored_prompt(prompt)

        # ── 1. Thử Google GenAI (Gemini Image / Imagen 3) ──────────────────
        current_key = self.rotator.get_key()
        if current_key:
            try:
                from google import genai
                client = genai.Client(api_key=current_key)

                for model_name in [
                    "gemini-2.5-flash-image",
                    "gemini-3.1-flash-image",
                    "imagen-3.0-generate-002",
                ]:
                    try:
                        logger.info(
                            f"[ImageAgent] Gọi Google AI '{model_name}' scene {scene_id}: '{prompt[:50]}...'"
                        )
                        response = client.models.generate_content(
                            model=model_name,
                            contents=(
                                f"Generate a vertical 9:16 cinematic photorealistic shot. "
                                f"Style: Christopher Nolan dark sci-fi. "
                                f"Scene: {anchored_prompt}"
                            ),
                        )
                        if response and response.candidates:
                            for part in response.candidates[0].content.parts:
                                if (
                                    hasattr(part, "inline_data")
                                    and part.inline_data
                                    and part.inline_data.data
                                ):
                                    with open(output_path, "wb") as f:
                                        f.write(part.inline_data.data)
                                    if (
                                        Path(output_path).exists()
                                        and Path(output_path).stat().st_size > 1000
                                    ):
                                        logger.info(
                                            f"[ImageAgent] ✅ Google AI ({model_name}) → {output_path}"
                                        )
                                        return output_path
                    except Exception as model_err:
                        logger.debug(f"[ImageAgent] {model_name} không khả dụng: {model_err}")
            except Exception as e:
                logger.info(
                    f"[ImageAgent] Google AI Image không khả dụng ({e}). "
                    f"Chuyển sang Pollinations Flux..."
                )

        # ── 2. Fallback: Pollinations Flux (photorealistic, nologo, no watermark) ──
        try:
            # Seed duy nhất theo scene để tránh ảnh trùng lặp
            seed = (scene_id * 137 + self._call_count * 31) % 99999

            logger.info(
                f"[ImageAgent] 🎨 Pollinations Flux scene {scene_id} "
                f"(seed={seed}): '{prompt[:50]}...'"
            )
            encoded_prompt = urllib.parse.quote(anchored_prompt)

            # 1080x1920 thực để Ken Burns không bị upscale mờ
            url = (
                f"https://image.pollinations.ai/prompt/{encoded_prompt}"
                f"?width=1080&height=1920"
                f"&model={POLLINATIONS_MODEL}"
                f"&nologo=true"
                f"&enhance=true"
                f"&seed={seed}"
            )
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AI-Shorts-Studio/2.0"
                )
            }

            res = requests.get(url, headers=headers, timeout=45)
            if res.status_code == 200 and len(res.content) > 5000:
                with open(output_path, "wb") as f:
                    f.write(res.content)
                logger.info(
                    f"[ImageAgent] ✅ Pollinations Flux ({len(res.content):,} bytes) → {output_path}"
                )
                return output_path
            else:
                logger.warning(
                    f"[ImageAgent] Pollinations trả về {res.status_code}, "
                    f"size={len(res.content)} bytes"
                )
        except Exception as e:
            logger.warning(f"[ImageAgent] Lỗi Pollinations Flux: {e}")

        return None
