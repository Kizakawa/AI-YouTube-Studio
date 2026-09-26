"""
VideoAgent — Agent dựng video hoàn chỉnh từ RenderJobContract.

Nhận input đã enriched (scenes có audio_path + media_path) từ Pipeline Orchestrator,
gọi ffmpeg_helper để render → output MP4 9:16 chuẩn YouTube Shorts.

Luồng render nâng cấp (v2):
  1. Chuẩn bị từng scene clip (image→video với Ken Burns đa chế độ HOẶC trim stock video)
  2. Concat tất cả scene clips → 1 video liên tục (silent)
  3. Mux voice narration + optional BGM với Audio Ducking thực sự (sidechaincompress)
  4. (Optional) Mix SFX vào các mốc chuyển cảnh (whoosh, hit...)
  5. (Optional) Burn-in ASS/SRT subtitles (ASS karaoke ưu tiên)
"""
import logging
from pathlib import Path
from typing import List, Optional

from app.schemas.contracts import RenderJobContract, SceneContract
from app.utils.ffmpeg_helper import (
    image_to_video,
    trim_video,
    concat_videos,
    concat_videos_with_xfade,
    mux_audio_video,
    mix_sfx_transitions,
    burn_subtitles,
)

logger = logging.getLogger(__name__)

# Thư mục SFX mặc định (tạo sẵn, người dùng thêm file MP3/WAV vào sau)
DEFAULT_SFX_DIR = "assets/sfx"


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
            # ── Step 1: Chuẩn bị scene clips với Ken Burns đa chế độ ──
            logger.info(f"[VideoAgent] Job {job.job_id}: Chuẩn bị {len(job.scenes)} scene clips (Ken Burns cinematic)...")
            await report(f"🎬 Đang chuẩn bị {len(job.scenes)} phân cảnh video 9:16 với hiệu ứng Ken Burns...", 65)
            scene_clips = self._prepare_scene_clips(job.scenes, str(work_dir), job)

            # ── Step 2: Concat tất cả scenes → 1 video silent với xfade transition ──
            logger.info(f"[VideoAgent] Job {job.job_id}: Ghép {len(scene_clips)} clips (xfade fade 0.25s)...")
            await report(f"🎬 Đang ghép nối các phân cảnh với hiệu ứng chuyển cảnh mượt...", 75)
            silent_video = str(work_dir / "concat_silent.mp4")
            scene_durations = [s.actual_duration_seconds for s in job.scenes]
            concat_videos_with_xfade(
                video_paths=scene_clips,
                output_path=silent_video,
                fps=job.fps,
                transition="fadeblack",
                transition_duration=0.3,
                scene_durations_seconds=scene_durations,
            )

            # ── Step 3: Mux audio (voice + optional BGM với Audio Ducking) ──
            logger.info(f"[VideoAgent] Job {job.job_id}: Ghép audio narration + BGM (Audio Ducking)...")
            await report(f"🎵 Đang hòa âm giọng đọc AI và nhạc nền (Audio Ducking tự động)...", 82)

            has_subtitle = job.subtitle_path and Path(job.subtitle_path).exists()
            sfx_dir = job.sfx_dir or DEFAULT_SFX_DIR

            # Xác định tên file intermediate cho mux → sfx → subtitle
            if has_subtitle:
                muxed_video = str(work_dir / "muxed_no_sub.mp4")
            else:
                muxed_video = str(work_dir / "muxed.mp4")

            mux_audio_video(
                video_path=silent_video,
                audio_path=job.full_audio_path,
                output_path=muxed_video,
                bgm_path=job.bgm_path,
                bgm_volume=job.bgm_volume,
                use_ducking=True,  # Bật Audio Ducking thực sự qua sidechaincompress
            )

            # ── Step 4: Mix SFX vào các mốc chuyển cảnh ──
            sfx_cues = [s.sfx_cue for s in job.scenes[1:]]  # SFX cho từng chuyển cảnh
            has_any_sfx_cue = any(s.sfx_cue for s in job.scenes)
            sfx_dir_exists = Path(sfx_dir).exists()

            if sfx_dir_exists or has_any_sfx_cue:
                logger.info(f"[VideoAgent] Job {job.job_id}: Mix SFX transitions từ {sfx_dir}...")
                await report(f"🔊 Đang chèn âm thanh chuyển cảnh SFX...", 86)

                # Tính timestamp bắt đầu từng scene (giây)
                scene_timestamps = self._compute_scene_timestamps(job.scenes)

                sfx_out = str(work_dir / "muxed_with_sfx.mp4")
                result_video = mix_sfx_transitions(
                    video_path=muxed_video,
                    output_path=sfx_out,
                    scene_timestamps_seconds=scene_timestamps,
                    sfx_dir=sfx_dir,
                    default_sfx="whoosh",
                    sfx_cues=sfx_cues if sfx_cues else None,
                    sfx_volume=0.35,
                )
                # mix_sfx_transitions trả video gốc nếu không có SFX file
                muxed_video = result_video

            # ── Step 5: Burn ASS/SRT subtitles ──
            if has_subtitle:
                logger.info(f"[VideoAgent] Job {job.job_id}: Burn-in subtitles ({job.subtitle_path})...")
                sub_ext = Path(job.subtitle_path).suffix.lower()
                await report(
                    f"✍️ Đang ép phụ đề {'Karaoke ASS' if sub_ext == '.ass' else 'SRT'} chuẩn YouTube 1080p...",
                    90
                )
                burn_subtitles(
                    video_path=muxed_video,
                    srt_path=job.subtitle_path,
                    output_path=job.output_path,
                )
            else:
                # Không có subtitle: copy video muxed ra output cuối
                import shutil
                shutil.copy2(muxed_video, job.output_path)

            logger.info(f"[VideoAgent] Job {job.job_id}: ✅ Render hoàn tất → {job.output_path}")
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
        - Nếu media_type == "image" → Ken Burns effect với camera_motion từ SceneContract
          (zoom_in, zoom_out, pan_left, pan_right, pan_up, pan_down xoay vòng tự động)
        - Nếu media_type == "video" → Trim + scale 9:16 (trim_video)

        Chạy SONG SONG với ThreadPoolExecutor để giảm thời gian render từ O(N) xuống ~O(1).
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _render_one(scene: SceneContract) -> tuple[int, str]:
            """Render 1 scene clip, trả về (scene_id, clip_path)."""
            clip_path = str(Path(work_dir) / f"clip_{scene.scene_id:03d}.mp4")
            duration = max(scene.actual_duration_seconds, 1.0)  # Tối thiểu 1 giây
            is_hook = (scene.scene_id == 1)
            motion = "zoom_in" if is_hook else scene.resolved_camera_motion

            # Kiểm tra media_path tồn tại trước khi render
            media_ok = scene.media_path and Path(scene.media_path).exists()
            if not media_ok:
                logger.warning(
                    f"  Scene {scene.scene_id}: media_path không tồn tại "
                    f"('{scene.media_path}') → dùng placeholder gradient"
                )
                # Tạo placeholder gradient thay vì crash
                from app.services.pipeline import VideoPipeline
                placeholder = str(Path(work_dir) / f"placeholder_{scene.scene_id:03d}.png")
                VideoPipeline._create_placeholder_image(
                    placeholder,
                    scene.visual_keyword or f"Scene {scene.scene_id}",
                    scene_id=scene.scene_id
                )
                scene = scene.model_copy(update={"media_path": placeholder, "media_type": "image"})

            raw_type = (scene.media_type or "image").lower()
            is_video_file = bool(scene.media_path and scene.media_path.lower().endswith((".mp4", ".mov", ".webm", ".mkv")))

            if raw_type in ("video", "ai_video") or is_video_file:
                logger.info(
                    f"  Scene {scene.scene_id}: video ({raw_type}) → trim {duration:.1f}s"
                    f"{' (🔥 HOOK VIDEO CLIP)' if is_hook else ''}"
                )
                trim_video(
                    input_path=scene.media_path,
                    duration_seconds=duration,
                    output_path=clip_path,
                    width=job.resolution_w,
                    height=job.resolution_h,
                    fps=job.fps,
                )
            elif raw_type in ("image", "ai_image", "photo", "documentary", "auto"):
                logger.info(
                    f"  Scene {scene.scene_id}: image ({raw_type}) → Ken Burns [{motion}] {duration:.1f}s"
                    f"{' (🔥 HOOK PUNCH-IN)' if is_hook else ''}"
                )
                image_to_video(
                    image_path=scene.media_path,
                    duration_seconds=duration,
                    output_path=clip_path,
                    width=job.resolution_w,
                    height=job.resolution_h,
                    fps=job.fps,
                    zoom_effect=True,
                    camera_motion=motion,
                    is_hook_scene=is_hook,
                )
            else:
                # Fallback graceful: log warning, render như image
                logger.warning(
                    f"  Scene {scene.scene_id}: media_type không hợp lệ '{effective_type}' → fallback image mode"
                )
                image_to_video(
                    image_path=scene.media_path,
                    duration_seconds=duration,
                    output_path=clip_path,
                    width=job.resolution_w,
                    height=job.resolution_h,
                    fps=job.fps,
                    zoom_effect=True,
                    camera_motion=motion,
                    is_hook_scene=is_hook,
                )

            return scene.scene_id, clip_path

        # ── Chạy song song với ThreadPoolExecutor ──
        # FFmpeg mỗi process dùng 1 core — safe cho multi-thread
        max_workers = min(len(scenes), 4)  # Tối đa 4 worker để tránh OOM
        results: dict[int, str] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(_render_one, s): s.scene_id for s in scenes}
            for future in as_completed(future_map):
                scene_id = future_map[future]
                try:
                    sid, clip_path = future.result()
                    results[sid] = clip_path
                    logger.info(f"  ✅ Scene {sid}: clip sẵn sàng → {clip_path}")
                except Exception as exc:
                    logger.error(f"  ❌ Scene {scene_id}: Lỗi render clip: {exc}")
                    raise  # Re-raise để pipeline biết có lỗi

        # Sắp xếp lại theo scene_id (as_completed không giữ thứ tự)
        return [results[s.scene_id] for s in scenes]

    @staticmethod
    def _compute_scene_timestamps(scenes: List[SceneContract]) -> List[float]:
        """
        Tính thời điểm bắt đầu (giây) của từng scene trong timeline.

        Returns:
            Danh sách float: [0.0, 3.5, 7.2, 12.0, ...]
            Độ dài = số scene.
        """
        timestamps = []
        current = 0.0
        for scene in scenes:
            timestamps.append(current)
            current += scene.actual_duration_seconds
        return timestamps
