import os
import logging
import re
import urllib.parse
from pathlib import Path
from typing import Optional
import requests
from config.settings import settings
from app.utils.key_rotator import KeyRotator

logger = logging.getLogger(__name__)


def master_cinematic_prompt(
    raw_prompt: str,
    scene_id: int = 0,
    niche_id: str = "general",
    character_anchor: Optional[str] = None,
    setting_anchor: Optional[str] = None,
) -> str:
    """
    Chuẩn hóa prompt cho AI tạo ảnh theo phong cách điện ảnh dọc 9:16 siêu thực.
    Tự động áp dụng bộ quy tắc thị giác chuyên sâu từ Niche Intelligence Profile & Seedance 2.0 Director.
    Khóa nhận diện nhân vật (Cast Anchor) và bối cảnh (Setting Anchor) theo LocalMiniDrama Nano-Pipeline.
    """
    try:
        from app.services.media.seedance_director import SeedanceDirector
        director = SeedanceDirector()
        raw_prompt = director.enrich_with_physical_anchor(raw_prompt)
        raw_prompt = director.inject_idle_business(raw_prompt)
        if character_anchor or setting_anchor:
            raw_prompt = director.bind_cast_to_prompt(
                raw_prompt,
                character_anchor=character_anchor,
                setting_anchor=setting_anchor,
            )
    except Exception as e:
        logger.debug(f"[master_cinematic_prompt] Seedance enrichment skipped: {e}")

    try:
        from app.services.niche_manager import get_niche, build_image_prompt
        if niche_id in ("general", "auto", ""):
            from app.agents.scifi_visual_dispatcher_agent import SciFiVisualDispatcherAgent
            scifi_agent = SciFiVisualDispatcherAgent()
            is_scifi, _, _, _ = scifi_agent.detect_scifi_elements(topic=raw_prompt, visual_prompt=raw_prompt)
            if is_scifi:
                niche_id = "scifi_future_tech"
            else:
                from app.agents.wildlife_visual_dispatcher_agent import WildlifeVisualDispatcherAgent
                w_agent = WildlifeVisualDispatcherAgent()
                is_wl, _, subg, _ = w_agent.detect_wildlife_elements(topic=raw_prompt, visual_prompt=raw_prompt)
                if is_wl:
                    niche_id = "animal_rescue" if subg == "animal_rescue_pets" else "wildlife_predators"

        niche_profile = get_niche(niche_id)
        full_prompt, _ = build_image_prompt(raw_prompt, scene_id=scene_id, niche_profile=niche_profile)
        if character_anchor or setting_anchor:
            try:
                from app.services.media.seedance_director import SeedanceDirector
                full_prompt = SeedanceDirector().bind_cast_to_prompt(
                    full_prompt,
                    character_anchor=character_anchor,
                    setting_anchor=setting_anchor,
                )
            except Exception:
                pass
        return full_prompt
    except Exception:
        pass

    cleaned = re.sub(r'[\r\n\t]+', ' ', raw_prompt).strip()
    cleaned = re.sub(r'\b(watermark|logo|text|border|frame|subtitles)\b', '', cleaned, flags=re.IGNORECASE)

    if scene_id == 1:
        prefix = "8k vertical dramatic cinematic frame: "
        suffix = ", extreme visual impact, vibrant cinematic lighting, rich colors, razor-sharp focus, award-winning cinematography, photorealistic 9:16 vertical composition, no watermark, no text, no border"
    else:
        prefix = "8k vertical film still: "
        suffix = ", hyper-realistic details, rich atmospheric lighting, vivid color harmony, 35mm cinematic photography, photorealistic 9:16 vertical composition, no watermark, no text, no border"

    base_res = f"{prefix}{cleaned}{suffix}"
    if character_anchor or setting_anchor:
        try:
            from app.services.media.seedance_director import SeedanceDirector
            base_res = SeedanceDirector().bind_cast_to_prompt(
                base_res,
                character_anchor=character_anchor,
                setting_anchor=setting_anchor,
            )
        except Exception:
            pass
    return base_res


class ImageAgent:
    """
    Agent sinh hình ảnh AI chất lượng cao chuẩn tỷ lệ dọc 9:16.
    Thích ứng phong cách động 100% theo prompt của AI Director cho bất kỳ chủ đề nào.
    Tích hợp bộ khóa nhân vật & bối cảnh cố định (Cast & Setting Registry) từ LocalMiniDrama.
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
        niche: str = "general",
        scene_id: int = 0,
        visual_prompt: Optional[str] = None,
        used_urls: Optional[set] = None,
        topic: str = "",
        character_anchor: Optional[str] = None,
        setting_anchor: Optional[str] = None,
        **kwargs,
    ) -> Optional[str]:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        self._call_count += 1

        # Trích xuất linh hoạt từ kwargs nếu caller truyền kiểu khác
        niche = kwargs.get("style", niche)
        visual_prompt = visual_prompt or kwargs.get("raw_prompt")
        character_anchor = character_anchor or kwargs.get("cast_anchor")
        setting_anchor = setting_anchor or kwargs.get("scene_anchor")

        # Tự động trích xuất Cast & Setting Anchors nếu chưa được cung cấp trực tiếp
        if not character_anchor and not setting_anchor and (topic or prompt):
            try:
                from app.services.media.seedance_director import SeedanceDirector
                anchors = SeedanceDirector().extract_cast_and_scene_anchors(
                    topic=topic or prompt,
                    script_or_scenes=visual_prompt or prompt,
                    niche_id=niche,
                )
                character_anchor = anchors.get("character_anchor")
                setting_anchor = anchors.get("setting_anchor")
            except Exception as e:
                logger.debug(f"[ImageAgent] Auto cast extraction skipped: {e}")

        # Ưu tiên visual_prompt chi tiết từ AI Director
        base_prompt = (
            visual_prompt.strip()
            if visual_prompt and len(visual_prompt.strip()) > 10
            else prompt.strip()
        )

        anchored = master_cinematic_prompt(
            base_prompt,
            scene_id=scene_id,
            niche_id=niche,
            character_anchor=character_anchor,
            setting_anchor=setting_anchor,
        )

        # 1. ƯU TIÊN SỐ 1: Thử gọi trực tiếp công cụ tạo ảnh của Google (Gemini Flash/Pro Image)
        google_res = self._try_google_image_api(anchored, output_path, aspect_ratio=aspect_ratio)
        if google_res and Path(google_res).exists() and Path(google_res).stat().st_size > 5000:
            return google_res

        # 2. ƯU TIÊN SỐ 2: Thử sinh ảnh AI qua Hugging Face Serverless Inference (FLUX.1-schnell / SDXL không watermark)
        hf_res = self._try_huggingface_image_api(anchored, output_path)
        if hf_res and Path(hf_res).exists() and Path(hf_res).stat().st_size > 5000:
            return hf_res

        # 3. DỰ PHÒNG CHẤT LƯỢNG CAO: Thử sinh ảnh bằng Pollinations với prompt tối ưu (chuẩn 9:16 HD 768x1344)
        try:
            import random
            seed = random.randint(1000, 999999)
            words = anchored.split()
            if len(words) > 70:
                compact_prompt = " ".join(words[:48] + words[-20:])
            else:
                compact_prompt = " ".join(words)
            encoded_prompt = urllib.parse.quote(compact_prompt)

            url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=768&height=1344&model=flux&nologo=true&seed={seed}"
            headers = {
                "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AI-Shorts-Studio/{seed}"
            }

            res = requests.get(url, headers=headers, timeout=14)
            if res.status_code == 200 and len(res.content) > 5000:
                with open(output_path, "wb") as f:
                    f.write(res.content)
                self._sanitize_ai_image(output_path)
                logger.info(f"[ImageAgent] ✅ AI Image generated via Flux ({len(res.content):,} bytes) → {output_path}")
                return output_path
        except Exception as e:
            logger.warning(f"[ImageAgent] Lỗi Pollinations: {e}")

        # 3. Fallback: Thử với prompt ngắn gọn thuần túy nếu prompt dài bị nghẽn
        try:
            import random
            seed = random.randint(1000, 999999)
            short_clean = " ".join(base_prompt.split()[:20])
            prefix_fb = "vibrant dramatic 8k vertical, rich vivid colors" if scene_id == 1 else "photorealistic 8k vertical cinema, vibrant colorful lighting"
            p_encoded = urllib.parse.quote(f"{short_clean}, {prefix_fb}")
            url_fallback = f"https://image.pollinations.ai/prompt/{p_encoded}?width=768&height=1344&model=flux&nologo=true&seed={seed}"
            res = requests.get(url_fallback, headers={"User-Agent": f"AI-Shorts/{seed}"}, timeout=10)
            if res.status_code == 200 and len(res.content) > 5000:
                with open(output_path, "wb") as f:
                    f.write(res.content)
                self._sanitize_ai_image(output_path)
                logger.info(f"[ImageAgent] ✅ AI Image fallback generated → {output_path}")
                return output_path
        except Exception as fb_err:
            logger.debug(f"[ImageAgent] Fallback ngắn cũng lỗi: {fb_err}")

        # 4. Dự phòng ảnh thật HD từ Internet (Open Web + Wikipedia + Openverse + Pexels Photo)
        web_stock_img = self._try_web_and_stock_photo_fallback(base_prompt, output_path, used_urls=used_urls, topic=topic)
        if web_stock_img and Path(web_stock_img).exists() and Path(web_stock_img).stat().st_size > 5000:
            return web_stock_img

        logger.warning(f"[ImageAgent] ⚠️ Không thể sinh ảnh cho Cảnh {scene_id} qua AI. Chuyển quyền cho Video/Media Fallback.")
        return None

    def _try_web_and_stock_photo_fallback(
        self,
        query: str,
        output_path: str,
        used_urls: Optional[set] = None,
        topic: str = "",
    ) -> Optional[str]:
        """Tải ảnh chân thực chất lượng cao từ Internet (Open Web + Wikipedia + Pexels) khi các engine AI gặp sự cố."""
        try:
            from app.services.media.stock_fetcher import StockFetcher
            from app.services.media.entity_detector import clean_scene_search_query
            from app.services.media.media_history import extract_media_identity, is_media_already_used

            clean_q = clean_scene_search_query(query, max_words=3)
            if topic and clean_q and clean_q.lower() not in topic.lower():
                clean_topic = clean_scene_search_query(topic, max_words=2)
                clean_q = f"{clean_topic} {clean_q}".strip()

            target_query = clean_q or topic or "dramatic scene"

            # 1. Thử tìm kiếm ảnh Open Web & Wikipedia trước (bao quát mọi chủ đề không rào cản bản quyền)
            try:
                from app.services.media.web_image_fetcher import WebImageFetcher, web_image_search_enabled
                if web_image_search_enabled():
                    fetcher = WebImageFetcher()
                    web_images = fetcher.search_all(target_query, per_source=3)
                    for item in web_images:
                        img_url = item.get("url")
                        if not img_url or is_media_already_used(img_url, used_urls or set()):
                            continue
                        try:
                            headers = {
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                                "Referer": "https://www.google.com/",
                            }
                            res = requests.get(img_url, headers=headers, timeout=10)
                            if res.status_code == 200 and len(res.content) > 5000:
                                with open(output_path, "wb") as f:
                                    f.write(res.content)
                                if used_urls is not None:
                                    used_urls.update(extract_media_identity(img_url))
                                logger.info(f"[ImageAgent] 🌐 Tải ảnh thực tế thành công từ Open Web/Wiki ({item.get('source')}): '{target_query}' → {output_path}")
                                return output_path
                        except Exception as dl_err:
                            logger.debug(f"[ImageAgent] Lỗi tải ảnh web {img_url[:60]}: {dl_err}")
            except Exception as w_err:
                logger.debug(f"[ImageAgent] Lỗi tìm ảnh Open Web: {w_err}")

            # 2. Dự phòng sang kho Pexels
            stock = StockFetcher()
            photos = stock.search_photos(
                target_query,
                per_page=3,
                orientation="portrait",
                topic=topic,
                used_urls=used_urls,
            )
            for photo in photos:
                img_url = photo.get("url")
                if img_url:
                    img_res = requests.get(img_url, timeout=12)
                    if img_res.status_code == 200 and len(img_res.content) > 5000:
                        with open(output_path, "wb") as f:
                            f.write(img_res.content)
                        if used_urls is not None:
                            used_urls.update(extract_media_identity(img_url))
                        logger.info(f"[ImageAgent] 📸 Tải ảnh HD thực tế thành công từ Pexels: '{target_query}' → {output_path}")
                        return output_path
        except Exception as e:
            logger.debug(f"[ImageAgent] Photo fallback error: {e}")
        return None

    def _try_pexels_photo_fallback(
        self,
        query: str,
        output_path: str,
        used_urls: Optional[set] = None,
        topic: str = "",
    ) -> Optional[str]:
        """Tương thích ngược với các cuộc gọi cũ."""
        return self._try_web_and_stock_photo_fallback(query, output_path, used_urls=used_urls, topic=topic)

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
                        f"award-winning photography, rich details, vibrant cinematic lighting, vivid colors, no text, no watermark: {prompt}"
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

    def _try_huggingface_image_api(self, prompt: str, output_path: str) -> Optional[str]:
        """
        Sinh ảnh AI siêu thực trực tiếp qua Hugging Face Serverless Inference API.
        Ưu tiên các checkpoint hàng đầu thế giới:
        - black-forest-labs/FLUX.1-schnell (Siêu thực, không watermark)
        - stabilityai/stable-diffusion-xl-base-1.0
        """
        token = getattr(settings, 'HUGGINGFACE_TOKEN', '') or os.getenv("HUGGINGFACE_TOKEN", "") or os.getenv("HF_TOKEN", "")
        if not token or token == "your_hf_token_here":
            return None

        models_to_try = [
            "black-forest-labs/FLUX.1-schnell",
            "stabilityai/stable-diffusion-xl-base-1.0",
        ]
        headers = {"Authorization": f"Bearer {token.strip()}"}

        for model in models_to_try:
            api_url = f"https://api-inference.huggingface.co/models/{model}"
            try:
                payload = {"inputs": prompt}
                res = requests.post(api_url, headers=headers, json=payload, timeout=30)
                if res.status_code == 200 and len(res.content) > 5000:
                    with open(output_path, "wb") as f:
                        f.write(res.content)
                    logger.info(f"✨ [ImageAgent] ✅ Hugging Face ({model}) thành công: {output_path} ({len(res.content):,} bytes)")
                    return output_path
                elif res.status_code == 503:
                    logger.debug(f"[ImageAgent] Hugging Face model {model} đang khởi động (503), chuyển model tiếp...")
                else:
                    logger.debug(f"[ImageAgent] Hugging Face {model} trả về mã {res.status_code}")
            except Exception as e:
                logger.debug(f"[ImageAgent] Lỗi Hugging Face {model}: {e}")
        return None

    def _sanitize_ai_image(self, image_path: str) -> None:
        """
        Loại bỏ triệt để watermark góc dưới (như @pollinations.ai) bằng cách cắt bỏ 48px chân ảnh
        và resize giữ nguyên độ phân giải chuẩn (768x1344) với bộ lọc Lanczos cao cấp.
        """
        try:
            from PIL import Image
            if not Path(image_path).exists():
                return
            with Image.open(image_path) as img:
                w, h = img.size
                if h > 200:
                    cropped = img.crop((0, 0, w, h - 48))
                    final_img = cropped.resize((w, h), Image.Resampling.LANCZOS)
                    final_img.save(image_path, quality=95)
                    logger.debug(f"[ImageAgent] 🛡️ Đã cắt lọc watermark chân ảnh và phục hồi kích thước: {image_path}")
        except Exception as e:
            logger.debug(f"[ImageAgent] Bỏ qua hậu xử lý watermark: {e}")

