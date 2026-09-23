"""
VideoAgent — Agent dựng video hoàn chỉnh từ RenderJobContract.

Nhận input đã enriched (scenes có audio_path + media_path) từ Pipeline Orchestrator,
gọi ffmpeg_helper để render → output MP4 9:16 chuẩn YouTube Shorts.

Luồng render:
  1. Chuẩn bị từng scene clip (image→video hoặc trim stock video)
  2. Concat tất cả scene clips → 1 video liên tục (silent)
  3. Mux voice narration + optional BGM → final audio track
  4. (Optional) Burn-in SRT subtitles
"""
import logging
from pathlib import Path
from typing import List

from app.schemas.contracts import RenderJobContract, SceneContract
from app.utils.ffmpeg_helper import (
    image_to_video,
    trim_video,
    concat_videos,
    mux_audio_video,
    burn_subtitles,
)

logger = logging.getLogger(__name__)


class VideoAgent:
    """
    Agent chịu trách nhiệm render video MP4 hoàn chỉnh từ RenderJobContract.

    Usage (từ Pipeline Orchestrator):
        agent = VideoAgent()
        await agent.render(render_job)
    """

    async def render(self, job: RenderJobContract, progress_callback=None) -> str:
        """
        Render video MP4 hoàn chỉnh theo RenderJobContract.
        Trả về đường dẫn file output.
        """
        work_dir = Path(job.output_path).parent / f"render_tmp_{job.job_id}"
        work_dir.mkdir(parents=True, exist_ok=True)

        async def report(msg: str, pct: int):
            if progress_callback:
                try:
                    await progress_callback(msg, pct)
                except Exception:
                    pass

        try:
            # ── Step 1: Chuẩn bị scene clips ──
            logger.info(f"[VideoAgent] Job {job.job_id}: Chuẩn bị {len(job.scenes)} scene clips...")
            await report(f"🎬 Đang chuẩn bị {len(job.scenes)} phân cảnh video 9:16...", 65)
            scene_clips = self._prepare_scene_clips(job.scenes, str(work_dir), job)

            # ── Step 2: Concat tất cả scenes → 1 video silent ──
            logger.info(f"[VideoAgent] Job {job.job_id}: Ghép {len(scene_clips)} clips...")
            await report(f"🎬 Đang ghép nối các phân cảnh thành timeline video liên tục...", 75)
            silent_video = str(work_dir / "concat_silent.mp4")
            concat_videos(scene_clips, silent_video, fps=job.fps)

            # ── Step 3: Mux audio (voice + optional BGM) ──
            logger.info(f"[VideoAgent] Job {job.job_id}: Ghép audio narration + BGM...")
            await report(f"🎵 Đang hòa âm giọng đọc AI và nhạc nền...", 83)
            if job.subtitle_path and Path(job.subtitle_path).exists():
                # Nếu có subtitle, mux audio trước rồi burn subtitle sau
                muxed_video = str(work_dir / "muxed_no_sub.mp4")
            else:
                muxed_video = job.output_path

            mux_audio_video(
                video_path=silent_video,
                audio_path=job.full_audio_path,
                output_path=muxed_video,
                bgm_path=job.bgm_path,
                bgm_volume=job.bgm_volume,
            )

            # ── Step 4: Burn subtitles (nếu có) ──
            if job.subtitle_path and Path(job.subtitle_path).exists():
                logger.info(f"[VideoAgent] Job {job.job_id}: Burn-in subtitles...")
                await report(f"✍️ Đang ép phụ đề hardsub chuẩn YouTube 1080p...", 90)
                burn_subtitles(
                    video_path=muxed_video,
                    srt_path=job.subtitle_path,
                    output_path=job.output_path,
                )
            
            logger.info(f"[VideoAgent] Job {job.job_id}: Render hoàn tất → {job.output_path}")
            return job.output_path

        finally:
            # Cleanup render temp (giữ lại output)
            import shutil
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)

    def _prepare_scene_clips(
        self,
        scenes: List[SceneContract],
        work_dir: str,
        job: RenderJobContract,
    ) -> List[str]:
        """
        Tạo video clip cho từng scene:
        - Nếu media_type == "image" → Ken Burns effect (image_to_video)
        - Nếu media_type == "video" → Trim + scale 9:16 (trim_video)
        """
        clips = []

        for scene in scenes:
            clip_path = str(Path(work_dir) / f"clip_{scene.scene_id:03d}.mp4")
            duration = scene.actual_duration_seconds

            if scene.media_type == "image":
                image_to_video(
                    image_path=scene.media_path,
                    duration_seconds=duration,
                    output_path=clip_path,
                    width=job.resolution_w,
                    height=job.resolution_h,
                    fps=job.fps,
                    zoom_effect=True,
                )
            elif scene.media_type == "video":
                trim_video(
                    input_path=scene.media_path,
                    duration_seconds=duration,
                    output_path=clip_path,
                    width=job.resolution_w,
                    height=job.resolution_h,
                    fps=job.fps,
                )
            else:
                raise ValueError(
                    f"Scene {scene.scene_id}: media_type không hợp lệ: {scene.media_type}"
                )

            logger.info(
                f"  Scene {scene.scene_id}: {scene.media_type} → {duration:.1f}s clip"
            )
            clips.append(clip_path)

        return clips
