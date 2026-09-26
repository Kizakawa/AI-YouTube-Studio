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
    ) -> Optional[str]:
        """
        Điểm điều phối chung: Nhận yêu cầu và tự động chuyển về đúng phương pháp:
        - Nếu method == 'IMAGE_TO_VIDEO' hoặc có source_image_path -> Chạy Image-to-Video
        - Nếu method == 'TEXT_TO_VIDEO' -> Chạy Text-to-Video trực tiếp
        - Nếu AUTO -> Tự động quyết định dựa trên tính chất câu lệnh
        """
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        # Quyết định phương pháp nếu là AUTO
        if method == "AUTO":
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
            )
        else:
            return await self.generate_text_to_video(
                prompt=prompt,
                output_path=output_path,
                duration_seconds=duration_seconds,
                scene_id=scene_id,
                camera_motion=camera_motion,
                visual_prompt=visual_prompt,
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

            # Nếu cảnh có các yếu tố chuyển động thực tế / viễn tưởng / không gian và không phải chân dung nhân vật cụ thể:
            # Ưu tiên lấy Video Clip 9:16 chuyển động thực tế để video sống động (dynamic video)
            motion_cues = [
                "spaceship", "flying", "cyberpunk", "galaxy", "stars", "universe", "wormhole", "robot", "laser", "space flight",
                "phi thuyền", "vũ trụ", "ngân hà", "hành tinh", "lỗ đen", "ocean", "water", "sea", "wave", "deep sea", "storm",
                "abyss", "underwater", "cockpit", "airplane", "flight", "traffic", "city", "crowd", "running", "moving", "smoke",
                "fire", "explosion", "speed", "dark water", "bioluminescent", "clouds", "sky", "dive", "diving", "swimming", "boat"
            ]
            p_check = clean_prompt.lower()
            is_specific_character = any(f in p_check for f in ["portrait", "face", "philosopher", "chân dung", "khuôn mặt", "character closeup"])
            if any(c in p_check for c in motion_cues) and not is_specific_character:
                px_vid = self._try_pexels_video_clip(clean_prompt, output_path, duration_seconds)
                if px_vid and Path(px_vid).exists() and Path(px_vid).stat().st_size > 10000:
                    logger.info(f"🎥 [AIVideoAgent] ✅ Nâng cấp thành Video Clip 9:16 chuyển động thực tế: {output_path}")
                    return output_path

            temp_ai_image = str(out_p.parent / f"_theme_frame_{scene_id:03d}.jpg")
            if "8k" not in clean_prompt.lower() and "cinematic" not in clean_prompt.lower():
                clean_prompt = f"Cinematic photorealistic 8k, {clean_prompt}, sharp details, volumetric lighting, vertical 9:16 composition"

            generated_img = self.image_agent.generate_scene_image(
                prompt=clean_prompt,
                output_path=temp_ai_image,
                aspect_ratio="9:16",
                niche="cinematic",
                scene_id=scene_id,
                visual_prompt=clean_prompt,
            )
            if generated_img and Path(generated_img).exists() and Path(generated_img).stat().st_size > 2000:
                target_img_path = generated_img
                created_temp = True
            else:
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
        pexels_vid = self._try_pexels_video_clip(full_prompt, output_path, duration_seconds)
        if pexels_vid and Path(pexels_vid).exists() and Path(pexels_vid).stat().st_size > 10000:
            logger.info(f"🎥 [AIVideoAgent] ✅ Pexels Video Clip 9:16 chuyển động hoàn tất: {output_path}")
            return output_path

        # 4. DỰ PHÒNG CHẤT LƯỢNG CAO (Zero-Failure): Generative Motion Engine siêu thực
        temp_frame = str(out_p.parent / f"_t2v_frame_{scene_id:03d}.jpg")
        mystic_prompt = f"Breathtaking mystical cinematography, {full_prompt}, glowing dynamic energy, 8k vertical video still, cinematic dark atmosphere"

        generated_img = self.image_agent.generate_scene_image(
            prompt=mystic_prompt,
            output_path=temp_frame,
            aspect_ratio="9:16",
            niche="cinematic",
            scene_id=scene_id,
            visual_prompt=mystic_prompt,
        )

        if generated_img and Path(generated_img).exists() and Path(generated_img).stat().st_size > 2000:
            self._render_cinematic_motion(
                image_path=generated_img,
                output_path=output_path,
                duration_seconds=duration_seconds,
                camera_motion=camera_motion,
                is_hook=(scene_id == 1),
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

        import asyncio
        import base64
        import requests

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
        if hf_token:
            try:
                headers = {"Authorization": f"Bearer {hf_token}"}
                api_url = "https://api-inference.huggingface.co/models/Wan-AI/Wan2.1-T2V-1.3B"
                payload = {"inputs": f"Cinematic vertical video, {prompt}"}
                res = requests.post(api_url, headers=headers, json=payload, timeout=50)
                if res.status_code == 200 and len(res.content) > 10000:
                    with open(output_path, "wb") as f:
                        f.write(res.content)
                    logger.info(f"🔮 [AIVideoAgent] ✅ Hugging Face Wan 2.1 Video hoàn tất: {output_path}")
                    return output_path
            except Exception as e:
                logger.debug(f"[AIVideoAgent] HF video api error: {e}")

        return None

    def _try_pexels_video_clip(self, prompt: str, output_path: str, duration_seconds: float) -> Optional[str]:
        """
        Tìm kiếm và tải video clip 9:16 chất lượng cao từ Pexels Video cho các chủ đề viễn tưởng / chuyển động.
        Tự động cắt đúng thời lượng (trim_video) để ráp nối mượt mà vào video hoàn chỉnh.
        """
        try:
            from app.agents.media_agent import MediaAgent
            media_agent = MediaAgent()
            if not media_agent.api_key:
                return None

            best_vid = media_agent.search_best_video_clip(prompt)
            if best_vid and best_vid.get("url"):
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
                        fps=30
                    )
                    try:
                        Path(raw_temp).unlink(missing_ok=True)
                    except Exception:
                        pass
                    if Path(output_path).exists() and Path(output_path).stat().st_size > 10000:
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
            is_hook_scene=is_hook
        )
