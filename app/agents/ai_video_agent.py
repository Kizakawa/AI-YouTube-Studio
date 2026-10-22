"""
app.agents.ai_video_agent — Tác nhân Sinh & Điều Phối Video AI Đa Phương Pháp (AI Video Generator & Motion Engine).

Hỗ trợ 2 phương pháp sản xuất cốt lõi:
1. IMAGE_TO_VIDEO (Tạo video phụ thuộc vào ảnh theo chủ đề):
   - Sinh ảnh tĩnh 8K chuẩn bố cục, giữ nguyên tỉ lệ nhân vật / bối cảnh không bị méo.
   - Chuyển hóa ảnh thành clip MP4 1080x1920 30fps bằng Cinematic 3D Motion Engine (Parallax, Lighting, Dynamic Zoom/Pan).
   - Áp dụng khi: Cảnh có nhân vật cụ thể, đồ vật rõ nét, địa danh, hoặc cần độ chi tiết sắc nét cao.

2. TEXT_TO_VIDEO (Tạo video AI trực tiếp từ văn bản):
   - Sinh video động trực tiếp từ prompt mô tả.
   - Thử nghiệm qua mô hình Video AI ngoại vi (HuggingFace / Fal / Pollinations), tự động dự phòng sang Motion Engine.
   - Áp dụng khi: Hiện tượng siêu nhiên, năng lượng huyền bí, vũ trụ 4D, lỗ đen, khói sương trừu tượng.
"""
import asyncio
import base64
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any
import requests

from app.agents.image_agent import ImageAgent
from app.utils.ffmpeg_helper import image_to_video, run_ffmpeg, trim_video
from app.utils.key_rotator import KeyRotator
from config.settings import settings

logger = logging.getLogger(__name__)


class AIVideoAgent:
    # ── BỘ 3 PHONG CÁCH ĐẠO DIỄN HÌNH ẢNH (3-STYLE VIDEO DIRECTOR ARCHETYPES - PROMPT #3 & #4) ──
    DIRECTOR_STYLE_ARCHETYPES: Dict[str, str] = {
        "luxury_commercial_imax": (
            "Shot on RED V-Raptor 8K VV macro-cinema lens, high-end commercial luxury aesthetics, "
            "layered foreground-to-background 3D depth separation, crisp volumetric rim lighting, "
            "floating ambient particles, ultra-smooth 60fps optical clarity"
        ),
        "natural_documentary": (
            "Shot on Sony FX9 85mm telephoto lens, authentic high-end wildlife & real-life camera aesthetics, "
            "organic camera breathing stability, natural environmental lighting, "
            "realistic micro-movements of hair, fur, fabric folds, and foliage reacting naturally to wind"
        ),
        "cinematic_emotional": (
            "Shot on Arri Alexa 65 anamorphic cinema lens, deep emotional film atmosphere, "
            "shallow depth-of-field with smooth rack focus on subject's expressive eyes and micro-expressions, "
            "subtle chiaroscuro volumetric lighting, rich cinematic color depth without gaudy filters"
        ),
    }

    # ── HƯỚNG DẪN CHUYỂN ĐỘNG MÁY QUAY CHUYÊN NGHIỆP (CAMERA MOTION DIRECTIVES - PROMPT #2 & #4) ──
    CAMERA_MOTION_DIRECTIVES: Dict[str, str] = {
        "zoom_in": "smooth motorized slow dolly push-in toward the primary subject with shallow depth-of-field rack focus",
        "zoom_in_fast": "dynamic cinematic push-in locking focus on the central subject's eyes and posture",
        "hyper_zoom": "assertive optical push-in with crisp focal lock on the main subject and 3D background compression",
        "ken_burns_zoom_in": "gradual cinematic dolly-in with natural foreground-to-background parallax depth",
        "zoom_out": "smooth motorized dolly pull-back revealing the surrounding environment and spatial scale",
        "ken_burns_zoom_out": "slow atmospheric pull-back revealing full environmental depth and lighting",
        "pan_left": "fluid lateral tracking pan left with natural 3D foreground-background parallax separation",
        "pan_right": "fluid lateral tracking pan right with natural 3D foreground-background parallax separation",
        "tilt_up": "steady vertical crane tilt-up revealing vertical scale, posture, and volumetric lighting",
        "dolly_zoom": "subtle cinematic vertigo dolly-zoom keeping the subject locked in razor-sharp focus while background perspective shifts",
    }

    # ── KHÓA CHẶT CHẤT LƯỢNG VẬT LÝ & CHỐNG MÉO HÌNH (ANTI-MORPHING & QUALITY LOCK - PROMPT #1 & #5) ──
    ANTI_MORPH_QUALITY_LOCK: str = (
        "STRICT QUALITY & PHYSICS LOCK: preserve exact facial features, eye symmetry, character expression, "
        "clothing details, and original composition; subtle natural micro-movements of hair, fabric, and light; "
        "physically accurate fluid motion, stable background geometry, zero facial morphing, zero limb distortion, "
        "no jitter, no warping, no text or watermark"
    )

    @classmethod
    def resolve_director_style(
        cls,
        niche: Optional[str] = None,
        narration: str = "",
        prompt: str = "",
    ) -> str:
        """
        Tự động chọn 1 trong 3 phong cách đạo diễn (Prompt #3):
        - luxury_commercial_imax: Sang trọng, sắc nét, chiều sâu 3D (Deep Sea, Sci-Fi, Space, Alternate History)
        - natural_documentary: Đời thường, chân thực như quay bằng máy ảnh cao cấp (Wildlife, Animal Rescue, Nature)
        - cinematic_emotional: Giàu cảm xúc như cảnh trong phim điện ảnh (Psychology, History Mystery, True Crime)
        """
        n_low = str(niche or "").strip().lower()
        ctx = f"{n_low} {narration} {prompt}".lower()

        if n_low in ("animal_rescue", "wildlife_predators", "nature", "pets") or any(
            k in ctx for k in ("rescue", "puppy", "kitten", "predator", "wildlife", "jaguar", "crocodile", "lion", "eagle", "stray")
        ):
            return "natural_documentary"

        if n_low in ("psychology", "history_mystery", "true_crime", "philosophy", "stoicism") or any(
            k in ctx for k in ("psychology", "manipulat", "narcissist", "emotion", "silence", "gaze", "eyes", "tears", "betrayal", "secret")
        ):
            return "cinematic_emotional"

        if n_low in ("deep_sea_earth_mysteries", "scifi", "alternate_history", "space", "ancient_civilizations") or any(
            k in ctx for k in ("abyss", "trench", "ocean", "bioluminescent", "cosmos", "galaxy", "sonar", "artifact", "megalodon")
        ):
            return "luxury_commercial_imax"

        return "cinematic_emotional"

    @classmethod
    def build_cinematic_motion_prompt(
        cls,
        prompt: str,
        niche: Optional[str] = None,
        camera_motion: str = "zoom_in",
        style_mode: str = "auto",
        scene_id: int = 1,
        narration: str = "",
    ) -> str:
        """
        Tổng hợp bộ 5 Prompt Đạo Diễn Chuyển Động (Thổi hồn ảnh tĩnh + Chuyển động siêu mượt +
        3 Kiểu Video + Máy ảnh 100 củ + Khóa chất lượng chống méo mặt/nền rung) thành Prompt hoàn chỉnh
        cho Google Veo 3.1 / Wan 2.2 / Wan 2.1.
        """
        style_key = (
            style_mode
            if style_mode in cls.DIRECTOR_STYLE_ARCHETYPES
            else cls.resolve_director_style(niche=niche, narration=narration, prompt=prompt)
        )
        style_clause = cls.DIRECTOR_STYLE_ARCHETYPES[style_key]
        cam_key = str(camera_motion or "zoom_in").strip().lower()
        cam_clause = cls.CAMERA_MOTION_DIRECTIVES.get(
            cam_key,
            cls.CAMERA_MOTION_DIRECTIVES["zoom_in"],
        )
        hook_pacing = (
            "Immediate thumb-stopping opening frame with crisp subject separation; "
            if scene_id == 1
            else ""
        )
        clean_subj = str(prompt or "").strip().rstrip(".")
        return (
            f"Cinematic 9:16 vertical video (5-8s). {hook_pacing}"
            f"Subject & Action: {clean_subj}. "
            f"Camera & Optics: {style_clause}; {cam_clause}. "
            f"{cls.ANTI_MORPH_QUALITY_LOCK}."
        )

    @classmethod
    def build_three_video_style_variants(
        cls,
        prompt: str,
        camera_motion: str = "zoom_in",
        narration: str = "",
    ) -> Dict[str, str]:
        """
        Thực thi Prompt #3 (BIẾN 1 ẢNH THÀNH 3 KIỂU VIDEO KHÁC NHAU):
        Trả về 3 phương án prompt hoàn chỉnh (Quảng cáo sang trọng / Đời thường chân thực / Điện ảnh giàu cảm xúc)
        với góc máy và nhịp hình riêng biệt.
        """
        return {
            "luxury_commercial_imax": cls.build_cinematic_motion_prompt(
                prompt=prompt,
                camera_motion="dolly_zoom" if camera_motion == "zoom_in" else camera_motion,
                style_mode="luxury_commercial_imax",
                narration=narration,
            ),
            "natural_documentary": cls.build_cinematic_motion_prompt(
                prompt=prompt,
                camera_motion="pan_right" if camera_motion == "zoom_in" else camera_motion,
                style_mode="natural_documentary",
                narration=narration,
            ),
            "cinematic_emotional": cls.build_cinematic_motion_prompt(
                prompt=prompt,
                camera_motion="zoom_in",
                style_mode="cinematic_emotional",
                narration=narration,
            ),
        }

    @classmethod
    def direct_camera_motion_from_image(
        cls,
        image_path: str,
        fallback_motion: str = "zoom_in",
        scene_id: int = 1,
        narration: str = "",
        visual_analysis: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Thực thi Prompt #1 & #4 (PHÂN TÍCH KHUNG HÌNH ẢNH TĨNH & ĐẠO DIỄN GÓC MÁY):
        Đọc bức ảnh thực tế trên đĩa bằng OpenCV, đo phân bổ độ nét/tương phản (Saliency & Gradient Energy)
        giữa trung tâm, trái/phải và trên/dưới để chọn chuyển động máy quay tự nhiên nhất cho bức ảnh đó.
        """
        if not image_path or not Path(image_path).exists():
            return fallback_motion
        if scene_id == 1 and fallback_motion in ("hyper_zoom", "zoom_in_fast", "dolly_zoom"):
            return fallback_motion

        try:
            from PIL import Image
            import numpy as np

            with Image.open(str(image_path)) as pil_img:
                img = np.asarray(pil_img.convert("L"), dtype=np.float32)
            import numpy as np


            if img is None or img.size == 0:
                return fallback_motion
            h, w = img.shape[:2]
            if h < 64 or w < 64:
                return fallback_motion

            gy, gx = np.gradient(img)
            mag = np.hypot(gx, gy)


            mean_total = float(np.mean(mag)) + 1e-5

            center_crop = mag[int(h * 0.25):int(h * 0.75), int(w * 0.22):int(w * 0.78)]
            center_ratio = float(np.mean(center_crop)) / mean_total

            left_energy = float(np.mean(mag[:, :int(w * 0.45)]))
            right_energy = float(np.mean(mag[:, int(w * 0.55):]))
            top_energy = float(np.mean(mag[:int(h * 0.42), :]))
            bottom_energy = float(np.mean(mag[int(h * 0.58):, :]))

            narr_low = str(narration or "").lower()
            is_emotional_or_twist = any(
                w in narr_low
                for w in ("secret", "eyes", "silence", "truth", "never", "shock", "fear", "mind", "bí mật", "ánh mắt", "sự thật")
            )

            if center_ratio >= 1.32:
                return "dolly_zoom" if is_emotional_or_twist else "zoom_in"
            if right_energy > left_energy * 1.24:
                return "pan_right"
            if left_energy > right_energy * 1.24:
                return "pan_left"
            if top_energy > bottom_energy * 1.28:
                return "tilt_up"
            if center_ratio <= 0.92:
                return "zoom_out"
        except Exception as e:
            logger.debug(f"[AIVideoAgent] direct_camera_motion_from_image fallback: {e}")

        return fallback_motion
    """
    Tác nhân sinh video AI chuyên nghiệp.
    Cung cấp cả 2 giải pháp: Text-to-Video trực tiếp & Image-to-Video có kiểm soát chi tiết.
    Tích hợp trực tiếp Google Veo 3.1 & Gemini Image cùng Generative Motion Engine dự phòng.
    """

    def __init__(self):
        self.image_agent = ImageAgent()
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None

    async def generate_scene_video(
        self,
        prompt: str,
        output_path: str,
        duration_seconds: float = 3.5,
        scene_id: int = 1,
        camera_motion: str = "zoom_in",
        visual_prompt: Optional[str] = None,
        method: str = "AUTO",
        source_image_path: Optional[str] = None,
        used_urls: Optional[set] = None,
        visual_analysis: Optional[Dict[str, Any]] = None,
        narration: str = "",
    ) -> Optional[str]:
        """
        Điểm điều phối chung: Nhận yêu cầu và tự động chuyển về đúng phương pháp:
        - Nếu method == 'IMAGE_TO_VIDEO' hoặc có source_image_path -> Chạy Image-to-Video
        - Nếu method == 'TEXT_TO_VIDEO' -> Chạy Text-to-Video trực tiếp
        - Nếu AUTO -> Tự động quyết định dựa trên tính chất câu lệnh
        """
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        # Quyết định phương pháp nếu là AUTO hoặc kiểm tra yếu tố Viễn tưởng
        from app.agents.scifi_visual_dispatcher_agent import SciFiVisualDispatcherAgent
        scifi_agent = SciFiVisualDispatcherAgent()
        is_scifi, conf, subgenre, _ = scifi_agent.detect_scifi_elements(
            topic=prompt,
            narration=narration,
            visual_prompt=visual_prompt,
        )

        skip_stock = False
        if is_scifi and method == "AUTO":
            # Không ép cứng TEXT_TO_VIDEO nữa: hỏi VisualToolRouter bản chất cảnh (thật / mô phỏng / hư cấu / trừu tượng)
            from app.agents.visual_tool_router import VisualToolRouter
            route = VisualToolRouter().route_scene({"narration": narration, "visual_prompt": visual_prompt or prompt})
            skip_stock = route["prefer_generation"]
            method = route["production_method"] if route["production_method"] in ("IMAGE_TO_VIDEO", "TEXT_TO_VIDEO") else "TEXT_TO_VIDEO"
            logger.info(f"🚀 [AIVideoAgent] Phân cảnh {scene_id} viễn tưởng ({subgenre}) -> {method} | {route['rationale']}")
        elif method == "AUTO":
            abstract_cues = ["energy", "portal", "wormhole", "cosmic ray", "hyperspace", "vortex", "dimension", "black hole", "nebula", "huyền bí", "vũ trụ", "siêu nhiên", "ảo diệu"]
            p_lower = (prompt + " " + (visual_prompt or "")).lower()
            if any(c in p_lower for c in abstract_cues):
                method = "TEXT_TO_VIDEO"
            else:
                method = "IMAGE_TO_VIDEO"

        logger.info(f"🔮 [AIVideoAgent] Cảnh {scene_id} thực thi theo phương pháp: '{method}' (Thời lượng: {duration_seconds:.1f}s)")

        if method == "IMAGE_TO_VIDEO":
            return await self.generate_image_to_video(
                prompt=prompt,
                output_path=output_path,
                duration_seconds=duration_seconds,
                scene_id=scene_id,
                camera_motion=camera_motion,
                visual_prompt=visual_prompt,
                source_image_path=source_image_path,
                used_urls=used_urls,
                visual_analysis=visual_analysis,
                narration=narration,
            )
        else:
            return await self.generate_text_to_video(
                prompt=prompt,
                output_path=output_path,
                duration_seconds=duration_seconds,
                scene_id=scene_id,
                camera_motion=camera_motion,
                visual_prompt=visual_prompt,
                used_urls=used_urls,
                visual_analysis=visual_analysis,
                narration=narration,
                skip_stock_lookup=skip_stock,
            )

    def _try_gemini_search_image(
        self,
        prompt: str,
        output_img_path: str,
        scene_id: int = 1,
        used_urls: Optional[set] = None,
        visual_analysis: Optional[Dict[str, Any]] = None,
        narration: str = "",
    ) -> Optional[str]:
        """
        Săn ảnh BẮT MẮT, CÂU VIEW chuẩn Gemini Search (Bing HD + ArtStation 4K + Wallhaven 4K + DDGS + Gemini Vision Curation).
        Không yêu cầu ảnh chụp thật 100% — ưu tiên CGI, Movie Still, Concept Art 8K, Nhiếp ảnh kịch tính.
        """
        if os.getenv("SEARCH_FIRST_VISUAL_MODE", "true").strip().lower() in ("0", "false", "no", "off"):
            return None
        try:
            import re as _re
            from app.agents.media_agent import MediaAgent
            from app.services.media.image_quality import (
                extract_core_creature, is_creature_subject, creature_queries, extract_subject,
                clean_search_query_from_prompt,
            )
            va = dict(visual_analysis or {})
            g_queries = (
                [q for q in (va.get("creature_queries") or []) if q]
                or [q for q in (va.get("google_image_queries") or []) if q]
                or [q for q in (va.get("search_queries") or []) if q]
            )
            raw_search_q = (g_queries[0] if g_queries else None) or str(va.get("subject") or "").strip() or prompt
            # Luôn lọc sạch prompt boilerplate ("JAW-DROPPING FRAME-0 HOOK", "Detailed prompt in English", "9:16 aspect ratio"...)
            search_q = clean_search_query_from_prompt(
                raw_search_q,
                narration=narration,
                topic=prompt,
                max_words=6,
            )
            if len(search_q.split()) > 8 or not search_q:
                core_m = extract_core_creature(raw_search_q, narration, prompt)
                subj_extracted = extract_subject(narration or raw_search_q, max_words=5)
                if core_m:
                    search_q = f"giant {core_m} {subj_extracted}".strip()
                elif subj_extracted:
                    search_q = f"{subj_extracted} photography"
                else:
                    search_q = clean_search_query_from_prompt(narration or prompt, max_words=5)

            if is_creature_subject(search_q, narration=narration, topic=prompt) and not va.get("creature_mode"):
                c_qs = creature_queries(search_q, topic=prompt, narration=narration)
                va["creature_mode"] = True
                va["creature_queries"] = c_qs
                va["google_image_queries"] = c_qs[:3]
                if c_qs:
                    search_q = c_qs[0]

            m_agent = MediaAgent()
            best_photo = m_agent.search_best_photo(
                query=search_q,
                used_urls=used_urls,
                visual_analysis=va,
                narration=narration or prompt,
                topic=prompt,
            )
            if best_photo and best_photo.get("url"):
                m_agent.download_file(best_photo["url"], output_img_path)
                p = Path(output_img_path)
                if p.exists() and p.stat().st_size > 3000:
                    if used_urls is not None:
                        used_urls.add(best_photo["url"])
                    logger.info(
                        f"Scene {scene_id}: 🔎 [Gemini Search Image] Chọn ảnh bắt mắt từ {best_photo.get('source')} "
                        f"(điểm {best_photo.get('curation_score', '-')}): {str(best_photo.get('title', ''))[:55]}"
                    )
                    return output_img_path
        except Exception as e:
            logger.debug(f"Scene {scene_id}: _try_gemini_search_image error: {e}")
        return None

    async def generate_image_to_video(
        self,
        prompt: str,
        output_path: str,
        duration_seconds: float = 3.5,
        scene_id: int = 1,
        camera_motion: str = "zoom_in",
        visual_prompt: Optional[str] = None,
        source_image_path: Optional[str] = None,
        used_urls: Optional[set] = None,
        visual_analysis: Optional[Dict[str, Any]] = None,
        narration: str = "",
        niche: Optional[str] = None,
        is_scifi: bool = False,
        skip_web_search: bool = False,
    ) -> Optional[str]:
        """
        PHƯƠNG PHÁP 1: IMAGE-TO-VIDEO (Tạo video phụ thuộc vào ảnh chủ đề).
        Ưu tiên #1: Săn ảnh bắt mắt từ kết quả tìm kiếm Google Images (Gemini Search + Vision) rồi tạo chuyển động 3D.
        Dự phòng #2: Sinh bức ảnh AI 8K rồi tạo chuyển động 3D.
        """
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        target_img_path = source_image_path
        created_temp = False

        # Nếu chưa có ảnh nguồn và chưa tìm kiếm web ở bước trước: Tìm ảnh từ Google Images (Gemini Search)
        if (not target_img_path or not Path(target_img_path).exists()) and not skip_web_search:
            clean_prompt = visual_prompt or prompt
            temp_search_image = str(out_p.parent / f"_gsearch_frame_{scene_id:03d}.jpg")
            searched_img = await asyncio.to_thread(
                self._try_gemini_search_image,
                clean_prompt,
                temp_search_image,
                scene_id,
                used_urls,
                visual_analysis,
                narration,
            )
            if searched_img and Path(searched_img).exists() and Path(searched_img).stat().st_size > 3000:
                target_img_path = searched_img
                created_temp = True

        # Dự phòng nếu Google Search không có ảnh phù hợp: Sinh bức ảnh chủ đề AI 8K
        if not target_img_path or not Path(target_img_path).exists():
            clean_prompt = visual_prompt or prompt

            temp_ai_image = str(out_p.parent / f"_theme_frame_{scene_id:03d}.jpg")
            if "8k" not in clean_prompt.lower() and "cinematic" not in clean_prompt.lower():
                clean_prompt = f"{clean_prompt}, cinematic photorealistic, sharp details, volumetric lighting, vertical 9:16 composition"

            generated_img = await asyncio.to_thread(
                self.image_agent.generate_scene_image,
                clean_prompt,
                temp_ai_image,
                "9:16",
                niche or "cinematic",
                scene_id,
                clean_prompt,
            )
            if generated_img and Path(generated_img).exists() and Path(generated_img).stat().st_size > 2000:
                target_img_path = generated_img
                created_temp = True
            else:
                logger.info(f"Scene {scene_id}: Sinh ảnh AI không thành công, thử cứu hộ bằng Pexels Video B-Roll...")
                px_vid = await asyncio.to_thread(
                    self._try_pexels_video_clip,
                    clean_prompt,
                    output_path,
                    duration_seconds,
                    used_urls,
                    visual_analysis,
                    narration,
                    is_scifi,
                )
                if px_vid and Path(px_vid).exists() and Path(px_vid).stat().st_size > 10000:
                    logger.info(f"🎥 [AIVideoAgent] Cứu hộ thành công bằng Video Clip 9:16: {output_path}")
                    return output_path
                logger.warning(f"Scene {scene_id}: Không thể sinh ảnh chủ đề cho Image-to-Video.")
                return None

        # Đạo diễn chuyển động máy quay dựa trên bố cục thực tế của bức ảnh (Prompt #1 & #4)
        camera_motion = self.direct_camera_motion_from_image(
            image_path=target_img_path,
            fallback_motion=camera_motion,
            scene_id=scene_id,
            narration=narration,
            visual_analysis=visual_analysis,
        )
        # Chuyển hóa ảnh chủ đề thành video MP4 1080x1920 với chuyển động 3D chân thực (chạy trong thread pool)
        try:
            await asyncio.to_thread(
                self._render_cinematic_motion,
                target_img_path,
                output_path,
                duration_seconds + 0.22,
                camera_motion,
                (scene_id == 1),
                is_scifi,
                niche,
            )

            if Path(output_path).exists() and Path(output_path).stat().st_size > 5000:
                logger.info(f"🎨 [AIVideoAgent] ✅ Image-to-Video hoàn tất cho Cảnh {scene_id} ({duration_seconds:.1f}s) → {output_path}")
                if created_temp:
                    try:
                        Path(target_img_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                return output_path
        except Exception as e:
            logger.error(f"Scene {scene_id}: Lỗi khi render Image-to-Video: {e}")

        return None

    async def generate_text_to_video(
        self,
        prompt: str,
        output_path: str,
        duration_seconds: float = 3.5,
        scene_id: int = 1,
        camera_motion: str = "zoom_in",
        visual_prompt: Optional[str] = None,
        used_urls: Optional[set] = None,
        visual_analysis: Optional[Dict[str, Any]] = None,
        narration: str = "",
        skip_stock_lookup: bool = False,
        niche: Optional[str] = None,
        is_scifi: bool = False,
        skip_web_search: bool = False,
    ) -> Optional[str]:
        """
        PHƯƠNG PHÁP 2: TEXT-TO-VIDEO (Tạo video AI trực tiếp từ mô tả).
        Dành riêng cho hiện tượng huyền bí, kỳ ảo, vũ trụ trừu tượng không có hình mẫu cố định.
        """
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        full_prompt = visual_prompt or prompt

        # 1. ƯU TIÊN SỐ 1: Thử gọi trực tiếp công cụ tạo video AI của Google (Google Veo 3.1)
        google_veo_res = await self._try_google_veo_api(
            full_prompt,
            output_path,
            duration_seconds,
            niche=niche,
            camera_motion=camera_motion,
            scene_id=scene_id,
            narration=narration,
        )
        if google_veo_res and Path(google_veo_res).exists() and Path(google_veo_res).stat().st_size > 10000:
            logger.info(f"🎥 [AIVideoAgent] ✅ Google Veo 3.1 Video hoàn tất: {output_path}")
            return output_path

        # 2. Thử gọi API Video AI ngoại vi nếu có cấu hình (HuggingFace Wan2.1 / Fal)
        external_res = await asyncio.to_thread(
            self._try_external_video_api,
            full_prompt,
            output_path,
            duration_seconds,
            niche,
            camera_motion,
            scene_id,
            narration,
        )
        if external_res and Path(external_res).exists() and Path(external_res).stat().st_size > 10000:
            logger.info(f"🔮 [AIVideoAgent] ✅ Text-to-Video API ngoại vi hoàn tất: {output_path}")
            return output_path

        # 3. ƯU TIÊN SỐ 3: Săn ảnh BẮT MẮT CÂU VIEW từ Google Images (Gemini Search + Vision) + Chuyển động 3D (nếu chưa tìm ở Tier 0)
        if not skip_web_search:
            temp_search_frame = str(out_p.parent / f"_t2v_gsearch_{scene_id:03d}.jpg")
            searched_frame = await asyncio.to_thread(
                self._try_gemini_search_image,
                full_prompt,
                temp_search_frame,
                scene_id,
                used_urls,
                visual_analysis,
                narration,
            )
            if searched_frame and Path(searched_frame).exists() and Path(searched_frame).stat().st_size > 3000:
                await asyncio.to_thread(
                    self._render_cinematic_motion,
                    searched_frame,
                    output_path,
                    duration_seconds + 0.22,
                    camera_motion,
                    (scene_id == 1),
                    is_scifi,
                    niche or "cinematic",
                )
                try:
                    Path(temp_search_frame).unlink(missing_ok=True)
                except Exception:
                    pass
                if Path(output_path).exists() and Path(output_path).stat().st_size > 5000:
                    logger.info(f"🔎 [AIVideoAgent] ✅ Gemini Search + 3D Motion hoàn tất cho Cảnh {scene_id} → {output_path}")
                    return output_path

        # 4. Tìm Video Clip B-Roll 9:16 chuyển động thực tế từ Pexels Video
        pexels_vid = None
        if not skip_stock_lookup:
            pexels_vid = await asyncio.to_thread(
                self._try_pexels_video_clip,
                full_prompt,
                output_path,
                duration_seconds,
                used_urls,
                visual_analysis,
                narration,
                is_scifi,
            )
        if pexels_vid and Path(pexels_vid).exists() and Path(pexels_vid).stat().st_size > 10000:
            logger.info(f"🎥 [AIVideoAgent] ✅ Pexels Video Clip 9:16 chuyển động hoàn tất: {output_path}")
            return output_path

        # 5. DỰ PHÒNG CHẤT LƯỢNG CAO (Zero-Failure): Generative Motion Engine siêu thực
        temp_frame = str(out_p.parent / f"_t2v_frame_{scene_id:03d}.jpg")
        try:
            from app.agents.wildlife_visual_dispatcher_agent import WildlifeVisualDispatcherAgent
            w_agent = WildlifeVisualDispatcherAgent()
            is_wl, _, w_subg, _ = w_agent.detect_wildlife_elements(topic=full_prompt, narration=narration)
            if is_wl:
                gen_prompt = w_agent.build_cinematic_wildlife_prompt(full_prompt, subgenre=w_subg, scene_id=scene_id)
                target_niche = "animal_rescue" if w_subg == "animal_rescue_pets" else "wildlife_predators"
            else:
                gen_prompt = f"Breathtaking cinematography, {full_prompt}, dynamic lighting, vivid vibrant colors, 8k vertical video still, crisp clarity"
                target_niche = niche or "cinematic"
        except Exception:
            gen_prompt = f"Breathtaking cinematography, {full_prompt}, dynamic lighting, vivid vibrant colors, 8k vertical video still, crisp clarity"
            target_niche = niche or "cinematic"

        generated_img = await asyncio.to_thread(
            self.image_agent.generate_scene_image,
            gen_prompt,
            temp_frame,
            "9:16",
            niche or target_niche,
            scene_id,
            gen_prompt,
        )

        if generated_img and Path(generated_img).exists() and Path(generated_img).stat().st_size > 2000:
            await asyncio.to_thread(
                self._render_cinematic_motion,
                generated_img,
                output_path,
                duration_seconds + 0.22,
                camera_motion,
                (scene_id == 1),
                is_scifi,
                niche or target_niche,
            )
            if Path(output_path).exists() and Path(output_path).stat().st_size > 5000:
                logger.info(f"🔮 [AIVideoAgent] ✅ Text-to-Video hoàn tất cho Cảnh {scene_id} → {output_path}")
                try:
                    Path(temp_frame).unlink(missing_ok=True)
                except Exception:
                    pass
                return output_path

        return None

    async def _try_google_veo_api(
        self,
        prompt: str,
        output_path: str,
        duration_seconds: float,
        niche: Optional[str] = None,
        camera_motion: str = "zoom_in",
        scene_id: int = 1,
        narration: str = "",
    ) -> Optional[str]:
        """
        Gọi trực tiếp công cụ sinh video AI chính hãng của Google (Google Veo 3.1).
        Hỗ trợ các model:
        - veo-3.1-fast-generate-preview (predictLongRunning)
        - veo-3.1-generate-preview (predictLongRunning)
        Tự động xoay vòng API Key và thăm dò kết quả (polling) không làm tắc nghẽn tiến trình.
        """
        if not self.rotator or not self.rotator.has_keys:
            return None

        # Google Veo yêu cầu Google Cloud Billing trả phí; mặc định tắt để tránh treo luồng 150s
        if not getattr(settings, "GOOGLE_VEO_ENABLED", False):
            return None

        veo_models = [
            "veo-3.1-fast-generate-preview",
            "veo-3.1-generate-preview",
        ]

        # Google Veo yêu cầu durationSeconds là số nguyên từ 4 đến 8 giây
        dur_int = max(4, min(8, int(round(duration_seconds))))
        clean_prompt = self.build_cinematic_motion_prompt(
            prompt=prompt,
            niche=niche,
            camera_motion=camera_motion,
            scene_id=scene_id,
            narration=narration,
        )


        total_keys = len(self.rotator.keys)
        for _ in range(min(total_keys, 3)):
            api_key = self.rotator.get_key()
            if not api_key:
                break

            for model in veo_models:
                try:
                    submit_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:predictLongRunning?key={api_key}"
                    payload = {
                        "instances": [
                            {"prompt": clean_prompt}
                        ],
                        "parameters": {
                            "aspectRatio": "9:16",
                            "durationSeconds": dur_int
                        }
                    }
                    headers = {"Content-Type": "application/json"}

                    res = requests.post(submit_url, json=payload, headers=headers, timeout=25)
                    if res.status_code == 200:
                        op_data = res.json()
                        op_name = op_data.get("name")
                        if not op_name:
                            continue

                        logger.info(f"⏳ [AIVideoAgent] Google Veo ({model}) đã tiếp nhận lệnh tạo video ({op_name}). Đang xử lý...")
                        poll_url = f"https://generativelanguage.googleapis.com/v1beta/{op_name}?key={api_key}"

                        for _ in range(30):
                            await asyncio.sleep(3)
                            poll_res = requests.get(poll_url, timeout=15)
                            if poll_res.status_code != 200:
                                break
                            status_data = poll_res.json()
                            if status_data.get("done"):
                                if "error" in status_data:
                                    logger.warning(f"[AIVideoAgent] Google Veo hoàn thành với lỗi: {status_data['error']}")
                                    break

                                resp = status_data.get("response", {})
                                samples = resp.get("generateVideoResponse", {}).get("generatedSamples", [])
                                if not samples:
                                    samples = resp.get("videos", [])

                                for item in samples:
                                    vid_info = item.get("video", item)
                                    if vid_info.get("bytesBase64Encoded"):
                                        b64 = vid_info["bytesBase64Encoded"]
                                        v_bytes = base64.b64decode(b64)
                                        if len(v_bytes) > 10000:
                                            with open(output_path, "wb") as f:
                                                f.write(v_bytes)
                                            logger.info(f"🎥 [AIVideoAgent] ✅ Google Veo ({model}) thành công: {output_path} ({len(v_bytes):,} bytes)")
                                            return output_path
                                    elif vid_info.get("uri"):
                                        v_uri = vid_info["uri"]
                                        download_url = f"{v_uri}&key={api_key}" if "?" in v_uri else f"{v_uri}?key={api_key}"
                                        d_res = requests.get(download_url, timeout=60)
                                        if d_res.status_code == 200 and len(d_res.content) > 10000:
                                            with open(output_path, "wb") as f:
                                                f.write(d_res.content)
                                            logger.info(f"🎥 [AIVideoAgent] ✅ Google Veo tải video thành công: {output_path} ({len(d_res.content):,} bytes)")
                                            return output_path
                                break
                    elif res.status_code == 429:
                        logger.debug(f"[AIVideoAgent] Google Veo {model} 429 (Cần bật Google Cloud Billing Pay-As-You-Go để kích hoạt quota Veo video).")
                        break
                    else:
                        logger.debug(f"[AIVideoAgent] Google Veo {model} status {res.status_code}: {res.text[:120]}")
                except Exception as e:
                    logger.debug(f"[AIVideoAgent] Lỗi khi gọi Google Veo {model}: {e}")

            self.rotator.rotate_key()

        return None

    def animate_thematic_image(
        self,
        image_path: str,
        output_path: str,
        duration_seconds: float = 3.5,
        camera_motion: str = "zoom_in",
        is_hook: bool = False,
        niche: Optional[str] = None,
        is_scifi: bool = False,
    ) -> Optional[str]:
        """Biến bất kỳ ảnh tư liệu lịch sử hoặc ảnh nghệ thuật nào thành video chuyển động 9:16 30fps."""
        if not Path(image_path).exists():
            return None
        self._render_cinematic_motion(
            image_path=image_path,
            output_path=output_path,
            duration_seconds=duration_seconds,
            camera_motion=camera_motion,
            is_hook=is_hook,
            is_scifi=is_scifi,
            niche=niche,
        )
        if Path(output_path).exists() and Path(output_path).stat().st_size > 5000:
            return output_path
        return None

    def _try_external_video_api(
        self,
        prompt: str,
        output_path: str,
        duration_seconds: float,
        niche: Optional[str] = None,
        camera_motion: str = "zoom_in",
        scene_id: int = 1,
        narration: str = "",
    ) -> Optional[str]:
        """Thử nghiệm các API sinh video ngoại vi (Pollinations / Fal / HuggingFace)."""
        import requests

        # 1. Thử Pollinations Video API nếu có POLLINATIONS_API_KEY
        poll_key = os.getenv("POLLINATIONS_API_KEY")
        if poll_key:
            try:
                headers = {"Authorization": f"Bearer {poll_key}", "Content-Type": "application/json"}
                url = "https://gen.pollinations.ai/v1/chat/completions"
                payload = {
                    "model": "alibaba/wan-2.2-fast",
                    "messages": [{"role": "user", "content": self.build_cinematic_motion_prompt(prompt=prompt, niche=niche, camera_motion=camera_motion, scene_id=scene_id, narration=narration)}],
                }
                res = requests.post(url, json=payload, headers=headers, timeout=60)
                if res.status_code == 200:
                    data = res.json()
                    choices = data.get("choices", [])
                    if choices:
                        content = choices[0].get("message", {}).get("content", "")
                        # Trích xuất URL video từ content nếu có
                        import re
                        m = re.search(r'https?://[^\s]+\.mp4', content)
                        if m:
                            v_url = m.group(0)
                            v_down = requests.get(v_url, timeout=45)
                            if v_down.status_code == 200 and len(v_down.content) > 10000:
                                with open(output_path, "wb") as f:
                                    f.write(v_down.content)
                                logger.info(f"🔮 [AIVideoAgent] ✅ Pollinations Wan 2.2 Video hoàn tất: {output_path}")
                                return output_path
            except Exception as e:
                logger.debug(f"[AIVideoAgent] Pollinations video error: {e}")

        # 2. Thử Hugging Face Inference API nếu có HUGGINGFACE_TOKEN
        hf_token = os.getenv("HUGGINGFACE_TOKEN") or os.getenv("HF_TOKEN")
        if hf_token and os.getenv("HF_VIDEO_ENDPOINT_ENABLED", "0") == "1":
            try:
                headers = {"Authorization": f"Bearer {hf_token}"}
                api_url = "https://api-inference.huggingface.co/models/Wan-AI/Wan2.1-T2V-1.3B"
                payload = {"inputs": self.build_cinematic_motion_prompt(prompt=prompt, niche=niche, camera_motion=camera_motion, scene_id=scene_id, narration=narration)}
                res = requests.post(api_url, headers=headers, json=payload, timeout=5)
                if res.status_code == 200 and len(res.content) > 10000:
                    with open(output_path, "wb") as f:
                        f.write(res.content)
                    logger.info(f"🔮 [AIVideoAgent] ✅ Hugging Face Wan 2.1 Video hoàn tất: {output_path}")
                    return output_path
            except Exception as e:
                logger.debug(f"[AIVideoAgent] HF video api error: {e}")

        return None

    def _try_pexels_video_clip(
        self,
        prompt: str,
        output_path: str,
        duration_seconds: float,
        used_urls: Optional[set] = None,
        visual_analysis: Optional[Dict[str, Any]] = None,
        narration: str = "",
        is_scifi: bool = False,
        **kwargs,
    ) -> Optional[str]:
        """
        Tìm kiếm và tải video clip 9:16 chất lượng cao từ Pexels Video cho các chủ đề viễn tưởng / chuyển động.
        Sử dụng search_queries từ visual_analysis (2-3 từ ngắn gọn) thay vì AI prompt dài dòng.
        Tải trực tiếp file video gốc để VideoAgent._prepare_scene_clips cắt và chỉnh màu đồng loạt 1 lần duy nhất.
        Đảm bảo không trùng lặp URL với các phân cảnh khác.
        """
        try:
            from app.agents.media_agent import MediaAgent, clean_scene_search_query
            media_agent = MediaAgent()
            if not media_agent.api_key:
                return None

            used = used_urls if used_urls is not None else set()

            queries_to_try = []
            if visual_analysis and visual_analysis.get("search_queries"):
                queries_to_try.extend([q for q in visual_analysis["search_queries"] if q])

            # Tự động nạp bộ từ khóa kho viễn tưởng chuyên dụng từ SciFiVisualDispatcherAgent
            from app.agents.scifi_visual_dispatcher_agent import SciFiVisualDispatcherAgent
            scifi_agent = SciFiVisualDispatcherAgent()
            det_scifi, conf, subgenre, _ = scifi_agent.detect_scifi_elements(
                topic=prompt,
                narration=narration,
            )
            if det_scifi or is_scifi:
                curated_scifi_qs = scifi_agent.get_curated_video_search_queries(subgenre=subgenre, narration=narration)
                for csq in curated_scifi_qs:
                    if csq not in queries_to_try:
                        queries_to_try.append(csq)

            # Tự động nạp bộ từ khóa kho động vật hoang dã chuyên dụng từ WildlifeVisualDispatcherAgent
            try:
                from app.agents.wildlife_visual_dispatcher_agent import WildlifeVisualDispatcherAgent
                w_agent = WildlifeVisualDispatcherAgent()
                is_wl, _, w_subg, _ = w_agent.detect_wildlife_elements(topic=prompt, narration=narration)
                if is_wl:
                    curated_wl_qs = w_agent.get_curated_video_search_queries(subgenre=w_subg, narration=narration)
                    for cwq in curated_wl_qs:
                        if cwq not in queries_to_try:
                            queries_to_try.append(cwq)
            except Exception:
                pass

            cleaned_q = clean_scene_search_query(prompt, max_words=3)
            if cleaned_q and cleaned_q not in queries_to_try:
                queries_to_try.append(cleaned_q)

            best_vid = None
            for q_candidate in queries_to_try[:6]:
                candidate_vid = media_agent.search_best_video_clip(
                    query=q_candidate,
                    used_urls=used,
                    visual_analysis=visual_analysis,
                    narration=narration,
                )
                if candidate_vid and candidate_vid.get("url") and candidate_vid["url"] not in used:
                    best_vid = candidate_vid
                    logger.info(f"🎥 [AIVideoAgent] Tìm thấy Video Clip kho 9:16 phù hợp với từ khóa '{q_candidate}': {best_vid.get('url', '')[:60]}...")
                    break
            if best_vid and best_vid.get("url") and best_vid["url"] not in used:
                cand_url = best_vid["url"]
                media_agent.download_file(cand_url, output_path)
                if Path(output_path).exists() and Path(output_path).stat().st_size > 10000:
                    used.add(cand_url)
                    logger.info(f"🎥 [AIVideoAgent] ✅ Pexels Video Clip 9:16 tải xong (sẽ trim+grade ở bước Render): {output_path}")
                    return output_path
        except Exception as e:
            logger.debug(f"[AIVideoAgent] _try_pexels_video_clip error: {e}")
        return None

    def _render_cinematic_motion(
        self,
        image_path: str,
        output_path: str,
        duration_seconds: float,
        camera_motion: str = "zoom_in",
        is_hook: bool = False,
        is_scifi: bool = False,
        niche: Optional[str] = None,
    ):
        """
        Dùng FFmpeg tạo video clip 1080x1920 30fps có chuyển động camera 3D mượt mà
        với độ sắc nét cao từ bức ảnh nguồn.
        """
        image_to_video(
            image_path=image_path,
            duration_seconds=duration_seconds,
            output_path=output_path,
            width=1080,
            height=1920,
            fps=30,
            zoom_effect=True,
            camera_motion=camera_motion,
            is_hook_scene=is_hook,
            is_scifi=is_scifi,
            niche=niche,
        )
