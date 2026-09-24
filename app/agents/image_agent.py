import logging
import re
import urllib.parse
from pathlib import Path
from typing import Optional
import requests
from config.settings import settings
from app.utils.key_rotator import KeyRotator

logger = logging.getLogger(__name__)

# ── Visual Style Anchor — bọc cứng MỌI prompt AI để đảm bảo nhất quán ────────
# Phong cách: Christopher Nolan Interstellar / dark cosmic sci-fi photorealistic
VISUAL_STYLE_PREFIX = (
    "Cinematic 8k film still, Christopher Nolan Interstellar style, "
    "shot on 70mm IMAX, authentic deep space cosmic phenomena, "
    "hyper-realistic, dark space ambiance, dramatic volumetric lighting, "
    "photorealistic, shallow depth of field, film grain, "
)
VISUAL_STYLE_SUFFIX = (
    ", professional teal-and-orange color grading, "
    "no cartoon, no anime, no abstract 3D shapes, no metallic balls, "
    "no wireframe grid, no disco lights, no retro sci-fi aesthetics, "
    "no text, no watermark, no logo, no border, "
    "highly detailed, photorealistic"
)

# Từ khóa nguy hiểm → gây ra ảnh biến dạng / sai phong cách
_DANGER_REPLACEMENTS = {
    "spaghettification":     "gravitational tidal disruption near event horizon",
    "visual representation": "dramatic cinematic depiction of",
    "abstract concept":      "striking cosmic imagery of",
    "representation of":     "cinematic depiction of",
    "visualization":         "dramatic astrophotography shot of",
    "wireframe":             "deep space astrophotography",
    "3d render":             "photorealistic space photography",
    "metallic sphere":       "glowing plasma nebula",
    "abstract shape":        "cosmic phenomenon",
    "grain of sand":         "dust particle in cosmic void",
    "crushed into":          "collapsing into",
    "mountain":              "massive stellar body",
}

# Pollinations: model=flux (photorealistic tốt nhất)
POLLINATIONS_MODEL = "flux"


class ImageAgent:
    """
    Agent sinh hình ảnh AI chất lượng cao chuẩn tỷ lệ dọc 9:16.

    Kiến trúc:
    - Ưu tiên visual_prompt (Director's description) hơn visual_keyword (short tag).
    - Mọi prompt đều bọc trong VISUAL_STYLE_PREFIX + SUFFIX → nhất quán phong cách.
    - Pollinations có &nologo=true&nofeed=true → không watermark, không xuất hiện public feed.
    - Seed theo scene_id → mỗi scene ra ảnh KHÁC NHAU, không trùng lặp.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys)
        self._call_count = 0

    def _sanitize_and_anchor(self, raw_prompt: str) -> str:
        """
        1. Loại bỏ/thay thế từ ngữ nguy hiểm gây sai phong cách hoặc ảnh dị.
        2. Bọc trong Visual Style Anchor.
        """
        cleaned = raw_prompt
        for danger, replacement in _DANGER_REPLACEMENTS.items():
            cleaned = re.sub(re.escape(danger), replacement, cleaned, flags=re.IGNORECASE)
        return f"{VISUAL_STYLE_PREFIX}{cleaned}{VISUAL_STYLE_SUFFIX}"

    def generate_scene_image(
        self,
        prompt: str,
        output_path: str,
        aspect_ratio: str = "9:16",
        niche: str = "cinematic",
        scene_id: int = 0,
        visual_prompt: Optional[str] = None,
    ) -> Optional[str]:
        """
        Sinh ảnh dọc 9:16 với phong cách cinematic nhất quán.

        Args:
            prompt:        visual_keyword (ngắn, dùng nếu không có visual_prompt)
            output_path:   Đường dẫn lưu ảnh
            aspect_ratio:  Luôn 9:16
            niche:         Niche kênh
            scene_id:      ID cảnh → tạo seed đa dạng, tránh ảnh trùng
            visual_prompt: Mô tả cảnh quay chi tiết từ Director (ưu tiên cao nhất)
        """
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        self._call_count += 1

        # Ưu tiên visual_prompt (đã qua Director) > visual_keyword thô
        base_prompt = (
            visual_prompt.strip()
            if visual_prompt and len(visual_prompt.strip()) > 10
            else prompt.strip()
        )
        anchored = self._sanitize_and_anchor(base_prompt)

        # ── 1. Thử Google GenAI (Gemini Image / Imagen 3) ─────────────────────
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
                            f"[ImageAgent] Google AI '{model_name}' scene {scene_id}: '{base_prompt[:60]}...'"
                        )
                        response = client.models.generate_content(
                            model=model_name,
                            contents=(
                                "Generate a vertical 9:16 cinematic photorealistic shot. "
                                "Christopher Nolan dark sci-fi style. "
                                f"Scene: {anchored}"
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
                    f"[ImageAgent] Google AI không khả dụng ({e}). Fallback Pollinations Flux..."
                )

        # ── 2. Fallback: Pollinations Flux ────────────────────────────────────
        # nologo=true:  triệt watermark logo góc dưới
        # nofeed=true:  không đăng ảnh vào public feed Pollinations
        # enhance=true: AI tự cải thiện prompt chất lượng
        # model=flux:   photorealistic engine tốt nhất của Pollinations
        try:
            seed = (scene_id * 137 + self._call_count * 31) % 99999
            logger.info(
                f"[ImageAgent] 🎨 Pollinations Flux scene {scene_id} (seed={seed}): "
                f"'{base_prompt[:60]}...'"
            )
            encoded_prompt = urllib.parse.quote(anchored)

            url = (
                f"https://image.pollinations.ai/prompt/{encoded_prompt}"
                f"?width=1080&height=1920"
                f"&model={POLLINATIONS_MODEL}"
                f"&nologo=true"
                f"&nofeed=true"
                f"&enhance=true"
                f"&seed={seed}"
            )
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AI-Shorts-Studio/2.0"
            }

            res = requests.get(url, headers=headers, timeout=60)
            if res.status_code == 200 and len(res.content) > 5000:
                with open(output_path, "wb") as f:
                    f.write(res.content)
                logger.info(
                    f"[ImageAgent] ✅ Pollinations Flux ({len(res.content):,} bytes) → {output_path}"
                )
                return output_path
            else:
                logger.warning(
                    f"[ImageAgent] Pollinations {res.status_code}, size={len(res.content)} bytes"
                )
        except Exception as e:
            logger.warning(f"[ImageAgent] Lỗi Pollinations Flux: {e}")

        return None
