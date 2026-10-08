"""
StoryboardManager — Dịch vụ quản lý Storyboard trực quan & hoán đổi Media (Media Swapper Gate).

Chức năng:
1. Trích xuất Storyboard chi tiết từng phân cảnh (Scene Contact Sheet) kèm trạng thái media.
2. Hoán đổi Media tức thì (Swap Media):
   - Đổi sang Video Stock Pexels 9:16 khác.
   - Đổi sang Ảnh Stock HD.
   - Vẽ lại bằng AI 8K (Imagen / Flux / Pollinations).
   - Tải lên video/ảnh cá nhân từ máy tính.
3. Đồng bộ cập nhật vào VideoJob trong CSDL SQLite.
"""

import os
import json
import logging
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Optional

from config.settings import settings
from app.database.models import get_db, VideoJob
from app.agents.media_agent import MediaAgent
from app.agents.image_agent import ImageAgent

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = BASE_DIR / "output"


class StoryboardManager:
    """Điều phối Storyboard và thay thế Media trực quan theo từng phân cảnh."""

    def __init__(self):
        self.media_agent = MediaAgent()
        self.image_agent = ImageAgent()

    def get_job_media_dir(self, job_id: int) -> Path:
        """Thư mục lưu trữ media của job."""
        job_dir = OUTPUT_DIR / str(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

    def get_storyboard(self, job_id: int) -> Dict[str, Any]:
        """Lấy danh sách phân cảnh và file media thực tế đã được chuẩn bị trên ổ cứng."""
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not job:
                raise ValueError(f"Không tìm thấy VideoJob #{job_id}")

            scenes_raw: List[Dict[str, Any]] = []
            if job.scenes_json:
                try:
                    data = json.loads(job.scenes_json)
                    if isinstance(data, list):
                        scenes_raw = data
                    elif isinstance(data, dict):
                        scenes_raw = data.get("scenes", [])
                except Exception as e:
                    logger.warning(f"Lỗi đọc scenes_json job #{job_id}: {e}")

            job_dir = self.get_job_media_dir(job_id)
            storyboard_scenes = []

            for idx, sc in enumerate(scenes_raw):
                sc_id = sc.get("scene_id", idx + 1)
                
                # Tìm file media tương ứng trong folder output/{job_id}/
                media_path = sc.get("media_path")
                disk_file = None

                if media_path and Path(media_path).exists():
                    disk_file = Path(media_path)
                else:
                    # Quét tìm theo pattern scene_00X_*
                    pattern = f"scene_{sc_id:03d}_*"
                    found_files = list(job_dir.glob(pattern))
                    if found_files:
                        # Ưu tiên mp4 nếu có
                        mp4s = [f for f in found_files if f.suffix.lower() == ".mp4"]
                        disk_file = mp4s[0] if mp4s else found_files[0]

                media_url = None
                media_type = sc.get("media_type", "image")
                source = "unassigned"

                if disk_file and disk_file.exists():
                    # Tạo URL truy cập qua static mount /media/{job_id}/{filename}
                    rel_path = disk_file.relative_to(OUTPUT_DIR)
                    media_url = f"/media/{rel_path.as_posix()}"
                    is_vid = disk_file.suffix.lower() in (".mp4", ".mov", ".webm")
                    media_type = "video" if is_vid else "image"
                    fn = disk_file.stem
                    if "pexels" in fn:
                        source = "pexels_video" if is_vid else "pexels_photo"
                    elif "pixabay" in fn:
                        source = "pixabay_video" if is_vid else "pixabay_photo"
                    elif "_ai" in fn or "imagen" in fn:
                        source = "ai_image"
                    elif "_i2v" in fn or "_t2v" in fn or "anim" in fn:
                        source = "ai_video"
                    elif "custom" in fn or "upload" in fn:
                        source = "user_upload"
                    else:
                        source = "disk_asset"

                storyboard_scenes.append({
                    "scene_id": sc_id,
                    "narration": sc.get("narration", ""),
                    "estimated_seconds": sc.get("estimated_seconds", sc.get("actual_duration_seconds", 4.0)),
                    "visual_keyword": sc.get("visual_keyword", ""),
                    "stock_query": sc.get("stock_query", ""),
                    "visual_prompt": sc.get("visual_prompt", ""),
                    "camera_motion": sc.get("camera_motion", "zoom_in"),
                    "media_type": media_type,
                    "source": source,
                    "media_url": media_url,
                    "has_media": bool(disk_file and disk_file.exists()),
                    "is_hook": (sc_id == 1)
                })

            return {
                "job_id": job.id,
                "title": job.title or f"Job #{job.id}",
                "topic": job.topic,
                "status": job.status,
                "target_channel_id": job.target_channel_id,
                "scene_count": len(storyboard_scenes),
                "scenes": storyboard_scenes
            }

    def swap_scene_media(
        self,
        job_id: int,
        scene_id: int,
        mode: str = "pexels_video",
        search_query: Optional[str] = None,
        custom_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Hoán đổi media của 1 cảnh cụ thể:
        - mode='pexels_video': Tìm clip video 9:16 mới từ Pexels & tải về thay thế.
        - mode='pexels_photo': Tìm ảnh stock HD 9:16 mới từ Pexels.
        - mode='ai_image': Sinh ảnh AI điện ảnh 8K mới theo prompt.
        """
        job_dir = self.get_job_media_dir(job_id)

        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not job:
                raise ValueError(f"Không tìm thấy VideoJob #{job_id}")

            scenes_raw: List[Dict[str, Any]] = []
            if job.scenes_json:
                data = json.loads(job.scenes_json)
                scenes_raw = data if isinstance(data, list) else data.get("scenes", [])

            # Tìm scene tương ứng
            target_scene = None
            for sc in scenes_raw:
                if sc.get("scene_id") == scene_id:
                    target_scene = sc
                    break

            if not target_scene:
                raise ValueError(f"Không tìm thấy phân cảnh #{scene_id} trong job #{job_id}")

            query = (search_query or "").strip() or target_scene.get("stock_query") or target_scene.get("visual_keyword") or job.topic
            prompt = (custom_prompt or "").strip() or target_scene.get("visual_prompt") or query

            new_file_path = None
            new_media_type = "image"
            new_source = mode

            if mode in ("pexels_video", "video"):
                # Tìm video Pexels mới
                clip = self.media_agent.search_best_video_clip(
                    query=query,
                    narration=target_scene.get("narration", ""),
                    topic=job.topic
                )
                if not clip or not clip.get("url"):
                    # Thử query gốc
                    clip = self.media_agent.search_best_video_clip(query=query)

                if clip and clip.get("url"):
                    out_path = str(job_dir / f"scene_{scene_id:03d}_pexels_video.mp4")
                    self.media_agent.download_file(clip["url"], out_path)
                    if Path(out_path).exists() and Path(out_path).stat().st_size > 5000:
                        new_file_path = out_path
                        new_media_type = "video"
                        new_source = "pexels_video"

            elif mode in ("pexels_photo", "photo"):
                # Tìm ảnh Pexels
                photo = self.media_agent.search_best_photo(
                    query=query,
                    narration=target_scene.get("narration", ""),
                    topic=job.topic
                )
                if photo and photo.get("url"):
                    out_path = str(job_dir / f"scene_{scene_id:03d}_pexels_photo.jpg")
                    self.media_agent.download_file(photo["url"], out_path)
                    if Path(out_path).exists() and Path(out_path).stat().st_size > 3000:
                        new_file_path = out_path
                        new_media_type = "image"
                        new_source = "pexels_photo"

            elif mode in ("ai_image", "image"):
                # Tạo ảnh AI
                out_path = str(job_dir / f"scene_{scene_id:03d}_ai.jpg")
                res = self.image_agent.generate_scene_image(
                    prompt=prompt,
                    output_path=out_path,
                    aspect_ratio="9:16",
                    style="cinematic",
                    scene_id=scene_id,
                    raw_prompt=prompt,
                    topic=job.topic
                )
                if res and Path(res).exists() and Path(res).stat().st_size > 2000:
                    new_file_path = res
                    new_media_type = "image"
                    new_source = "ai_image"

            if not new_file_path:
                raise RuntimeError(f"Không thể tìm hoặc sinh media mới cho phân cảnh #{scene_id} với truy vấn '{query}'")

            # Cập nhật đường dẫn vào scene data & lưu DB
            target_scene["media_path"] = str(Path(new_file_path).resolve())
            target_scene["media_type"] = new_media_type
            target_scene["visual_source"] = new_source
            if search_query:
                target_scene["stock_query"] = search_query
            if custom_prompt:
                target_scene["visual_prompt"] = custom_prompt

            if isinstance(json.loads(job.scenes_json), dict):
                job.scenes_json = json.dumps({"scenes": scenes_raw}, ensure_ascii=False)
            else:
                job.scenes_json = json.dumps(scenes_raw, ensure_ascii=False)

            db.commit()

            rel_path = Path(new_file_path).relative_to(OUTPUT_DIR)
            return {
                "success": True,
                "scene_id": scene_id,
                "media_path": new_file_path,
                "media_url": f"/media/{rel_path.as_posix()}",
                "media_type": new_media_type,
                "source": new_source,
                "message": f"Đã thay thế media phân cảnh #{scene_id} thành công!"
            }

    def save_uploaded_media(
        self,
        job_id: int,
        scene_id: int,
        file_bytes: bytes,
        original_filename: str
    ) -> Dict[str, Any]:
        """Lưu file tải lên từ máy tính cá nhân vào phân cảnh."""
        ext = Path(original_filename).suffix.lower()
        if ext not in (".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp"):
            raise ValueError(f"Định dạng file không hỗ trợ: {ext}")

        is_vid = ext in (".mp4", ".mov")
        job_dir = self.get_job_media_dir(job_id)
        target_path = job_dir / f"scene_{scene_id:03d}_custom{ext}"

        with open(target_path, "wb") as f:
            f.write(file_bytes)

        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not job:
                raise ValueError(f"Không tìm thấy VideoJob #{job_id}")

            scenes_raw: List[Dict[str, Any]] = []
            if job.scenes_json:
                data = json.loads(job.scenes_json)
                scenes_raw = data if isinstance(data, list) else data.get("scenes", [])

            for sc in scenes_raw:
                if sc.get("scene_id") == scene_id:
                    sc["media_path"] = str(target_path.resolve())
                    sc["media_type"] = "video" if is_vid else "image"
                    sc["visual_source"] = "user_upload"
                    break

            if isinstance(json.loads(job.scenes_json), dict):
                job.scenes_json = json.dumps({"scenes": scenes_raw}, ensure_ascii=False)
            else:
                job.scenes_json = json.dumps(scenes_raw, ensure_ascii=False)

            db.commit()

        rel_path = target_path.relative_to(OUTPUT_DIR)
        return {
            "success": True,
            "scene_id": scene_id,
            "media_path": str(target_path.resolve()),
            "media_url": f"/media/{rel_path.as_posix()}",
            "media_type": "video" if is_vid else "image",
            "source": "user_upload",
            "message": f"Đã tải lên media cá nhân cho phân cảnh #{scene_id}!"
        }

    def get_shot_table(self, job_id: int) -> Dict[str, Any]:
        """
        Trích xuất Bảng Hình Học Phân Cảnh (Seedance 2.0 Shot Table) cho VideoJob:
        Gồm Floor Plan, Camera Framing, Eyeline, One Action (Emotion + Physical Anchor),
        Idle Business, Light, và Continuity Lock Line.
        """
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not job:
                raise ValueError(f"Không tìm thấy VideoJob #{job_id}")

            scenes_raw: List[Dict[str, Any]] = []
            if job.scenes_json:
                try:
                    data = json.loads(job.scenes_json)
                    scenes_raw = data if isinstance(data, list) else data.get("scenes", [])
                except Exception as e:
                    logger.warning(f"Lỗi đọc scenes_json job #{job_id}: {e}")

            from app.services.media.seedance_director import SeedanceDirector
            director = SeedanceDirector()
            shot_table = director.create_shot_table(
                scenes=scenes_raw,
                topic=job.topic or job.title or f"Job #{job_id}"
            )
            shot_table["job_id"] = job.id
            shot_table["status"] = job.status
            return shot_table

