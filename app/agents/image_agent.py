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
        """Tải ảnh bắt mắt chất lượng cao từ Internet (Bing HD + ArtStation 4K + Wallhaven 4K + DDGS + Vision Curation) và crop dọc 9:16 thông minh."""
        try:
            from app.services.media.stock_fetcher import StockFetcher
            from app.services.media.entity_detector import clean_scene_search_query
            from app.services.media.image_quality import (
                extract_core_creature, is_creature_subject, creature_queries, prepare_vertical,
            )
            from app.agents.media_agent import MediaAgent

            # Loại bỏ các cụm boilerplate của AI prompt (8k vertical film still, photorealistic, no watermark...)
            stripped_q = re.sub(
                r"\b(8k|4k|vertical|film still|dramatic|cinematic|frame|photorealistic|hyper-realistic|composition|no watermark|no text|no border|award-winning|35mm|lighting|details)\b",
                " ",
                query or "",
                flags=re.IGNORECASE,
            )
            # Chỉ giữ từ tiếng Anh ASCII sạch để tránh ghép nhầm từ tiếng Việt từ topic
            ascii_words = [w for w in re.findall(r"[A-Za-z]{3,}", stripped_q) if w.lower() not in ("the", "and", "with", "from", "into", "shot", "view")]
            clean_q = " ".join(ascii_words[:6]) or clean_scene_search_query(stripped_q, max_words=5)

            core_monster = extract_core_creature(query, topic)
            if core_monster and core_monster not in clean_q.lower():
                clean_q = f"giant {core_monster} {clean_q}".strip()

            target_query = clean_q or "dramatic cinematic scene"
            is_creature = is_creature_subject(target_query, narration=query, topic=topic)
            va_hint = None
            if is_creature:
                c_qs = creature_queries(target_query, topic=topic, narration=query)
                va_hint = {
                    "subject": target_query,
                    "creature_mode": True,
                    "creature_queries": c_qs,
                    "google_image_queries": c_qs[:3],
                }

            stock = StockFetcher()
            best = stock.search_best_photo(
                target_query,
                used_urls=used_urls,
                visual_analysis=va_hint,
                narration=query,
                topic=topic,
            )
            if best and best.get("url"):
                m_agent = MediaAgent()
                m_agent.download_file(best["url"], output_path)
                p = Path(output_path)
                if p.exists() and p.stat().st_size > 3000:
                    try:
                        from PIL import Image
                        with Image.open(output_path) as im:
                            v_im = prepare_vertical(im, 1080, 1920)
                            v_im.save(output_path, format="JPEG", quality=94)
                    except Exception:
                        pass
                    logger.info(
                        f"[ImageAgent] 🌐 Ảnh Web 8K (Vision Curated) từ {best.get('source')}: "
                        f"'{target_query}' → {output_path}"
                    )
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

