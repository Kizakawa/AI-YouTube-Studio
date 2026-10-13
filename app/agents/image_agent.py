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
        # Nguồn thực tế của ảnh gần nhất (cloudflare_flux2 / gemini_image / web_fallback ...) để báo cáo
        self.last_source: str = ""

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
        premium: Optional[bool] = None,
    ) -> Optional[str]:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        self._call_count += 1
        self.last_source = ""

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
        )

        # 1. Chuỗi engine AI (Cloudflare FLUX → Gemini Image → Pollinations có key), có circuit breaker.
        #    Cảnh Hook (scene 1) được ưu tiên engine cao cấp nếu đã bật.
        from app.services.media.image_engines import generate_image
        is_premium = (scene_id == 1) if premium is None else bool(premium)
        ai_path, engine_info = generate_image(anchored, output_path, premium=is_premium)
        if ai_path and Path(ai_path).exists() and Path(ai_path).stat().st_size > 5000:
            self.last_source = engine_info
            return ai_path

        logger.warning(
            f"[ImageAgent] ⚠️ Cảnh {scene_id}: KHÔNG sinh được ảnh AI ({engine_info}) → dùng ảnh web/stock dự phòng "
            f"(nguy cơ lạc đề). Prompt: '{base_prompt[:80]}'"
        )

        # 2. Dự phòng ảnh thật HD từ Internet (Open Web + Wikipedia + Openverse + Pexels Photo)
        web_stock_img = self._try_web_and_stock_photo_fallback(base_prompt, output_path, used_urls=used_urls, topic=topic)
        if web_stock_img and Path(web_stock_img).exists() and Path(web_stock_img).stat().st_size > 5000:
            self.last_source = "web_fallback"
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

            # Giữ tới 5 từ để không làm mất tên thực thể (vd "mantis shrimp" + "eyes")
            clean_q = clean_scene_search_query(query, max_words=5)
            if topic and clean_q and clean_q.lower() not in topic.lower():
                clean_topic = clean_scene_search_query(topic, max_words=2)
                clean_q = f"{clean_topic} {clean_q}".strip()

            target_query = clean_q or topic or "dramatic scene"
            landscape_backup: Optional[bytes] = None
            landscape_backup_url = ""

            def _accept(content: bytes, url: str, label: str) -> bool:
                """Ưu tiên ảnh dọc; ảnh ngang chỉ giữ làm dự phòng cuối cùng."""
                nonlocal landscape_backup, landscape_backup_url
                try:
                    from PIL import Image
                    import io as _io
                    with Image.open(_io.BytesIO(content)) as im:
                        w, h = im.size
                    if w / max(h, 1) > 0.9:
                        if landscape_backup is None:
                            landscape_backup, landscape_backup_url = content, url
                        logger.debug(f"[ImageAgent] Bỏ qua ảnh ngang {w}x{h} từ {label} (giữ làm dự phòng)")
                        return False
                except Exception:
                    return False
                with open(output_path, "wb") as f:
                    f.write(content)
                if used_urls is not None:
                    used_urls.update(extract_media_identity(url))
                logger.info(f"[ImageAgent] 🌐 Ảnh dự phòng từ {label}: '{target_query}' → {output_path}")
                return True

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
                                if _accept(res.content, img_url, f"Open Web/Wiki ({item.get('source')})"):
                                    return output_path
                        except Exception as dl_err:
                            logger.debug(f"[ImageAgent] Lỗi tải ảnh web {img_url[:60]}: {dl_err}")
            except Exception as w_err:
                logger.debug(f"[ImageAgent] Lỗi tìm ảnh Open Web: {w_err}")

            # 2. Dự phòng sang kho Pexels (đã lọc portrait)
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
                        if _accept(img_res.content, img_url, "Pexels"):
                            return output_path

            # 3. Cuối cùng mới dùng ảnh ngang (renderer sẽ crop tâm)
            if landscape_backup is not None:
                with open(output_path, "wb") as f:
                    f.write(landscape_backup)
                if used_urls is not None:
                    used_urls.update(extract_media_identity(landscape_backup_url))
                logger.warning(f"[ImageAgent] ⚠️ Chỉ tìm được ảnh NGANG cho '{target_query}' → dùng tạm (sẽ bị crop).")
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

