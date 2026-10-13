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
import logging
import os
import re
import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

from app.agents.image_agent import ImageAgent
from app.utils.ffmpeg_helper import image_to_video, run_ffmpeg, trim_video
from app.utils.key_rotator import KeyRotator
from config.settings import settings

logger = logging.getLogger(__name__)


class AIVideoAgent:
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
    ) -> Optional[str]:
        """
        PHƯƠNG PHÁP 1: IMAGE-TO-VIDEO (Tạo video phụ thuộc vào ảnh chủ đề).
        Giúp video ĐỘNG nhưng KHÔNG BỊ MÉO HÌNH, kiểm soát hoàn hảo tỷ lệ nhân vật & bối cảnh.
        """
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        target_img_path = source_image_path
        created_temp = False

        # Nếu chưa có ảnh nguồn, sinh bức ảnh chủ đề 8K trước
        if not target_img_path or not Path(target_img_path).exists():
            clean_prompt = visual_prompt or prompt

            temp_ai_image = str(out_p.parent / f"_theme_frame_{scene_id:03d}.jpg")
            if "8k" not in clean_prompt.lower() and "cinematic" not in clean_prompt.lower():
                clean_prompt = f"{clean_prompt}, cinematic photorealistic, sharp details, volumetric lighting, vertical 9:16 composition"

            generated_img = self.image_agent.generate_scene_image(
                prompt=clean_prompt,
                output_path=temp_ai_image,
                aspect_ratio="9:16",
                niche=niche or "cinematic",
                scene_id=scene_id,
                visual_prompt=clean_prompt,
            )
            if generated_img and Path(generated_img).exists() and Path(generated_img).stat().st_size > 2000:
                target_img_path = generated_img
                created_temp = True
            else:
                logger.info(f"Scene {scene_id}: Sinh ảnh AI không thành công, thử cứu hộ bằng Pexels Video B-Roll...")
                px_vid = self._try_pexels_video_clip(
                    clean_prompt,
                    output_path,
                    duration_seconds,
                    used_urls=used_urls,
                    visual_analysis=visual_analysis,
                    narration=narration,
                    is_scifi=is_scifi,
                )
                if px_vid and Path(px_vid).exists() and Path(px_vid).stat().st_size > 10000:
                    logger.info(f"🎥 [AIVideoAgent] Cứu hộ thành công bằng Video Clip 9:16: {output_path}")
                    return output_path
                logger.warning(f"Scene {scene_id}: Không thể sinh ảnh chủ đề cho Image-to-Video.")
                return None

        # Chuyển hóa ảnh chủ đề thành video MP4 1080x1920 với chuyển động 3D chân thực
        try:
            self._render_cinematic_motion(
                image_path=target_img_path,
                output_path=output_path,
                duration_seconds=duration_seconds,
                camera_motion=camera_motion,
                is_hook=(scene_id == 1),
                is_scifi=is_scifi,
                niche=niche,
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
    ) -> Optional[str]:
        """
        PHƯƠNG PHÁP 2: TEXT-TO-VIDEO (Tạo video AI trực tiếp từ mô tả).
        Dành riêng cho hiện tượng huyền bí, kỳ ảo, vũ trụ trừu tượng không có hình mẫu cố định.
        """
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        full_prompt = visual_prompt or prompt

        # 1. ƯU TIÊN SỐ 1: Thử gọi trực tiếp công cụ tạo video AI của Google (Google Veo 3.1)
        google_veo_res = await self._try_google_veo_api(full_prompt, output_path, duration_seconds)
        if google_veo_res and Path(google_veo_res).exists() and Path(google_veo_res).stat().st_size > 10000:
            logger.info(f"🎥 [AIVideoAgent] ✅ Google Veo 3.1 Video hoàn tất: {output_path}")
            return output_path

        # 2. Thử gọi API Video AI ngoại vi nếu có cấu hình (HuggingFace Wan2.1 / Fal)
        external_res = self._try_external_video_api(full_prompt, output_path, duration_seconds)
        if external_res and Path(external_res).exists() and Path(external_res).stat().st_size > 10000:
            logger.info(f"🔮 [AIVideoAgent] ✅ Text-to-Video API ngoại vi hoàn tất: {output_path}")
            return output_path

        # 3. ƯU TIÊN SỐ 3: Tìm Video Clip B-Roll 9:16 chuyển động thực tế từ Pexels Video (cho viễn tưởng, không gian, cyberpunk, chuyển động thực)
        # Giúp cảnh có chuyển động động (dynamic video) 30fps thực sự thay vì chỉ là ảnh tĩnh phóng to!
        # BỎ QUA khi router xác định cảnh là HƯ CẤU THUẦN TÚY (skip_stock_lookup): kho stock không thể có đúng
        # người ngoài hành tinh / quả cầu Dyson, clip "gần đúng" sẽ lệch nội dung lời thoại.
        pexels_vid = None
        if not skip_stock_lookup:
            pexels_vid = self._try_pexels_video_clip(
                full_prompt,
                output_path,
                duration_seconds,
                used_urls=used_urls,
                visual_analysis=visual_analysis,
                narration=narration,
                is_scifi=is_scifi,
            )
        else:
            logger.info(f"🧭 [AIVideoAgent] Cảnh {scene_id} hư cấu thuần túy -> bỏ qua kho stock, sinh hình AI trực tiếp.")
        if pexels_vid and Path(pexels_vid).exists() and Path(pexels_vid).stat().st_size > 10000:
            logger.info(f"🎥 [AIVideoAgent] ✅ Pexels Video Clip 9:16 chuyển động hoàn tất: {output_path}")
            return output_path

        # 4. DỰ PHÒNG CHẤT LƯỢNG CAO (Zero-Failure): Generative Motion Engine siêu thực
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

        generated_img = self.image_agent.generate_scene_image(
            prompt=gen_prompt,
            output_path=temp_frame,
            aspect_ratio="9:16",
            niche=niche or target_niche,
            scene_id=scene_id,
            visual_prompt=gen_prompt,
        )

        if generated_img and Path(generated_img).exists() and Path(generated_img).stat().st_size > 2000:
            self._render_cinematic_motion(
                image_path=generated_img,
                output_path=output_path,
                duration_seconds=duration_seconds,
                camera_motion=camera_motion,
                is_hook=(scene_id == 1),
                is_scifi=is_scifi,
                niche=niche or target_niche,
            )
            if Path(output_path).exists() and Path(output_path).stat().st_size > 5000:
                logger.info(f"🔮 [AIVideoAgent] ✅ Text-to-Video hoàn tất cho Cảnh {scene_id} → {output_path}")
                try:
                    Path(temp_frame).unlink(missing_ok=True)
                except Exception:
                    pass
                return output_path

        return None

    async def _try_google_veo_api(self, prompt: str, output_path: str, duration_seconds: float) -> Optional[str]:
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
        clean_prompt = (
            f"Cinematic 9:16 vertical video, 8k masterpiece, photorealistic, dramatic lighting, "
            f"fluid dynamic camera movement, award-winning cinematography, no text, no watermark: {prompt}"
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

    def _try_external_video_api(self, prompt: str, output_path: str, duration_seconds: float) -> Optional[str]:
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
                    "messages": [{"role": "user", "content": f"Cinematic 9:16 vertical video, 8k: {prompt}"}],
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
                payload = {"inputs": f"Cinematic vertical video, {prompt}"}
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
    ) -> Optional[str]:
        """
        Tìm kiếm và tải video clip 9:16 chất lượng cao từ Pexels Video cho các chủ đề viễn tưởng / chuyển động.
        Sử dụng search_queries từ visual_analysis (2-3 từ ngắn gọn) thay vì AI prompt dài dòng.
        Tự động cắt đúng thời lượng (trim_video) để ráp nối mượt mà vào video hoàn chỉnh.
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
            is_scifi, conf, subgenre, _ = scifi_agent.detect_scifi_elements(
                topic=prompt,
                narration=narration,
            )
            if is_scifi:
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
                raw_temp = str(Path(output_path).parent / f"_raw_px_{Path(output_path).stem}.mp4")
                media_agent.download_file(cand_url, raw_temp)
                if Path(raw_temp).exists() and Path(raw_temp).stat().st_size > 10000:
                    trim_video(
                        input_path=raw_temp,
                        duration_seconds=duration_seconds,
                        output_path=output_path,
                        width=1080,
                        height=1920,
                        fps=30,
                        is_scifi=is_scifi,
                    )
                    try:
                        Path(raw_temp).unlink(missing_ok=True)
                    except Exception:
                        pass
                    if Path(output_path).exists() and Path(output_path).stat().st_size > 10000:
                        used.add(cand_url)
                        logger.info(f"🎥 [AIVideoAgent] ✅ Pexels Full Video Clip 9:16 ({duration_seconds:.1f}s) sẵn sàng: {output_path}")
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
