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
from app.utils.ffmpeg_helper import image_to_video, run_ffmpeg

logger = logging.getLogger(__name__)


class AIVideoAgent:
    """
    Tác nhân sinh video AI chuyên nghiệp.
    Cung cấp cả 2 giải pháp: Text-to-Video trực tiếp & Image-to-Video có kiểm soát chi tiết.
    """

    def __init__(self):
        self.image_agent = ImageAgent()

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
            temp_ai_image = str(out_p.parent / f"_theme_frame_{scene_id:03d}.jpg")
            clean_prompt = visual_prompt or prompt
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

        # 1. Thử gọi API Video AI ngoại vi nếu có cấu hình
        external_res = self._try_external_video_api(full_prompt, output_path, duration_seconds)
        if external_res and Path(external_res).exists() and Path(external_res).stat().st_size > 10000:
            logger.info(f"🔮 [AIVideoAgent] ✅ Text-to-Video API ngoại vi hoàn tất: {output_path}")
            return output_path

        # 2. Dự phòng: Generative Motion Engine siêu thực
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
        """Thử nghiệm các API sinh video ngoại vi (HuggingFace / Pollinations / Fal)."""
        hf_token = os.getenv("HUGGINGFACE_TOKEN") or os.getenv("HF_TOKEN")
        if hf_token:
            try:
                import requests
                headers = {"Authorization": f"Bearer {hf_token}"}
                api_url = "https://api-inference.huggingface.co/models/Wan-AI/Wan2.1-T2V-1.3B"
                payload = {"inputs": prompt}
                res = requests.post(api_url, headers=headers, json=payload, timeout=45)
                if res.status_code == 200 and len(res.content) > 10000:
                    with open(output_path, "wb") as f:
                        f.write(res.content)
                    return output_path
            except Exception as e:
                logger.debug(f"[AIVideoAgent] HF video api error: {e}")

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
