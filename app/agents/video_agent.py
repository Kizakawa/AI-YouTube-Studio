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
import asyncio
import logging
import shutil
from pathlib import Path
from typing import List, Optional

from app.schemas.contracts import RenderJobContract, SceneContract
from app.utils.ffmpeg_helper import (
    image_to_video,
    multi_pose_loop_to_video,
    trim_video,
    concat_videos,
    concat_videos_with_xfade,
    resolve_niche_transitions,
    mux_audio_video,
    mix_sfx_transitions,
    resolve_smart_sfx_cue,
    burn_subtitles,
    VIDEO_DURATION_SAFETY_MARGIN,
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

        # ── Bộ máy dựng video cục bộ chuẩn hóa FFmpeg & AI Media ──

        try:
            # ── Step 1: Chuẩn bị scene clips với Ken Burns đa chế độ ──
            logger.info(f"[VideoAgent] Job {job.job_id}: Chuẩn bị {len(job.scenes)} scene clips (Ken Burns cinematic)...")
            await report(f"🎬 Đang chuẩn bị {len(job.scenes)} phân cảnh video 9:16 với hiệu ứng Ken Burns...", 65)
            scene_clips = await asyncio.to_thread(self._prepare_scene_clips, job.scenes, str(work_dir), job)

            # ── Step 2: Concat tất cả scenes → 1 video silent với xfade transition ──
            transition_duration = 0.18
            tail_buffer = 0.15
            total_scenes = len(job.scenes)

            has_subtitle = bool(job.subtitle_path and Path(job.subtitle_path).exists())
            sfx_dir = job.sfx_dir or DEFAULT_SFX_DIR

            logger.info(
                f"[VideoAgent] Job {job.job_id}: Ghép {len(scene_clips)} clips "
                f"(xfade {transition_duration}s, tail buffer {tail_buffer}s"
                f"{', Single-Pass Subtitle Burn' if has_subtitle else ''})..."
            )
            await report(
                f"🎬 Đang ghép nối phân cảnh chuyển động mượt{' & ép phụ đề Karaoke ASS 1080p' if has_subtitle else ''}...",
                76
            )
            silent_video = str(work_dir / "concat_silent.mp4")

            # Tính chính xác thời lượng từng clip (đã bù xfade overlap và tail buffer + MoneyPrinterTurbo safety margin)
            clip_durations = [
                max(s.actual_duration_seconds, 1.0) + (
                    transition_duration if idx < total_scenes - 1 else (tail_buffer + VIDEO_DURATION_SAFETY_MARGIN)
                )
                for idx, s in enumerate(job.scenes)
            ]
            is_scifi_job = getattr(job, "is_scifi", False)
            transition_sequence = resolve_niche_transitions(
                niche=job.niche,
                is_scifi=is_scifi_job,
                count=max(total_scenes - 1, 1)
            )
            logger.info(f"[VideoAgent] Dynamic transitions ({len(transition_sequence)} cuts): {transition_sequence}")

            concat_videos_with_xfade(
                video_paths=scene_clips,
                output_path=silent_video,
                fps=job.fps,
                transition=transition_sequence,
                transition_duration=transition_duration,
                scene_durations_seconds=clip_durations,
                subtitle_path=job.subtitle_path if has_subtitle else None,
            )

            # ── Step 3 & 4: Chuẩn bị SFX cues trước để quyết định có chạy loudnorm ở bước mux_audio_video hay gộp vào bước cuối ──
            from app.utils.ffmpeg_helper import _find_sfx_file
            raw_niche = (job.niche or "").lower()
            is_heavy_hit_niche = is_scifi_job or any(
                k in raw_niche for k in ("psychology", "deep_sea", "wildlife", "predator", "horror", "mystery", "history", "what_if", "alternate")
            )
            hit_sfx_name = "impact_sub" if is_heavy_hit_niche else "hit"
            default_sfx_name = "whoosh_fast" if is_scifi_job else "whoosh"

            # 1. Tự động phân tích ngữ nghĩa, cảm xúc và loại chuyển cảnh để chọn SFX chuẩn cho từng cut
            total_sc = len(job.scenes)
            resolved_sfx_cues = []
            sfx_dir_path_check = Path(sfx_dir)
            for i, s in enumerate(job.scenes[1:], start=1):
                cue = s.sfx_cue
                if (
                    not cue
                    or cue.lower() in ("whoosh", "default", "none", "auto")
                    or (sfx_dir_path_check.exists() and not _find_sfx_file(sfx_dir_path_check, cue))
                ):
                    cue = resolve_smart_sfx_cue(
                        narration=s.narration,
                        scene_idx=i,
                        total_scenes=total_sc,
                        niche=job.niche,
                    )
                    # Nếu cue là whoosh mặc định, đồng bộ với hiệu ứng chuyển cảnh thị giác
                    if cue == "whoosh" and i - 1 < len(transition_sequence):
                        t_type = transition_sequence[i - 1]
                        if t_type in ("zoomin", "hblur"):
                            cue = "whoosh_fast"
                        elif t_type == "pixelize":
                            cue = "glitch"
                        elif t_type in ("circleopen", "radial"):
                            cue = "reveal"
                        elif t_type in ("smoothleft", "smoothright", "diagtl", "diagtr"):
                            cue = "whoosh_fast"
                resolved_sfx_cues.append(cue)

            # 2. Level 4 Multi-Layer Foley: Trích xuất các mốc xuất hiện của Power Words để đánh Micro-SFX
            from app.utils.subtitle_helper import SubtitleHelper
            micro_sfx_cues = SubtitleHelper.extract_power_word_cues(job.scenes, min_interval_s=1.8)

            sfx_dir_exists = Path(sfx_dir).exists()
            will_mix_sfx = bool(sfx_dir_exists or resolved_sfx_cues or micro_sfx_cues)

            logger.info(f"[VideoAgent] Job {job.job_id}: Ghép audio narration + BGM (Audio Ducking)...")
            await report(f"🎵 Đang hòa âm giọng đọc AI và nhạc nền (Audio Ducking tự động)...", 84)

            muxed_video = str(work_dir / "muxed.mp4")

            mux_audio_video(
                video_path=silent_video,
                audio_path=job.full_audio_path,
                output_path=muxed_video,
                bgm_path=job.bgm_path,
                bgm_volume=job.bgm_volume,
                use_ducking=True,  # Bật Audio Ducking thực sự qua sidechaincompress
                normalize_loudness=(not will_mix_sfx),  # Chỉ chuẩn hóa loudnorm 1 lần duy nhất ở bước cuối cùng
            )

            if will_mix_sfx:
                logger.info(f"[VideoAgent] Job {job.job_id}: Mix Multi-layer SFX transitions từ {sfx_dir} (Niche: {job.niche})...")
                await report(f"🔊 Đang thiết kế âm thanh đa tầng Level 4 (Bass Impact, Foley & Riser)...", 92)

                # Tính timestamp bắt đầu từng scene (giây)
                scene_timestamps = self._compute_scene_timestamps(job.scenes)

                sfx_out = str(work_dir / "muxed_with_sfx.mp4")
                result_video = mix_sfx_transitions(
                    video_path=muxed_video,
                    output_path=sfx_out,
                    scene_timestamps_seconds=scene_timestamps,
                    sfx_dir=sfx_dir,
                    default_sfx=default_sfx_name,
                    sfx_cues=resolved_sfx_cues if resolved_sfx_cues else None,
                    sfx_volume=0.42 if is_heavy_hit_niche else 0.38,
                    hit_sfx=hit_sfx_name,
                    hit_volume=0.55 if is_heavy_hit_niche else 0.50,
                    micro_sfx_cues=micro_sfx_cues if micro_sfx_cues else None,
                )
                muxed_video = result_video

            # Copy video hoàn chỉnh (đã có xfade + subtitles + audio ducking + SFX) ra output cuối
            import shutil
            shutil.copy2(muxed_video, job.output_path)

            logger.info(f"[VideoAgent] Job {job.job_id}: ✅ Render hoàn tất (Single-Pass High Fidelity) → {job.output_path}")
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

        total_scenes = len(scenes)
        transition_duration = 0.18
        tail_buffer = 0.15

        def _render_one(idx: int, scene: SceneContract) -> tuple[int, str]:
            """Render 1 scene clip, trả về (scene_id, clip_path)."""
            clip_path = str(Path(work_dir) / f"clip_{scene.scene_id:03d}.mp4")
            base_dur = max(scene.actual_duration_seconds, 1.0)
            # Cảnh trước cảnh cuối bù thêm transition_duration để xfade không làm hụt video
            # Cảnh cuối cùng đệm thêm tail_buffer + VIDEO_DURATION_SAFETY_MARGIN để dứt câu hoàn toàn, không bao giờ ngắt cụt
            if idx < total_scenes - 1:
                duration = base_dur + transition_duration
            else:
                duration = base_dur + tail_buffer + VIDEO_DURATION_SAFETY_MARGIN

            is_hook = (scene.scene_id == 1)
            motion = (scene.camera_motion if scene.camera_motion in ("hyper_zoom", "warp_zoom", "zoom_in") else "zoom_in") if is_hook else scene.resolved_camera_motion

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
            clip_niche = getattr(job, "niche", None)
            is_scifi_clip = getattr(job, "is_scifi", False) or ("scifi" in str(clip_niche or "").lower()) or ("scifi" in (getattr(scene, "visual_keyword", "") or "").lower())
            is_already_rendered = bool(scene.media_path and any(tag in Path(scene.media_path).name.lower() for tag in ("_i2v", "_t2v", "_pexels_fb", "clip_", "_gsearch_3d", "_creature_3d", "_motion3d", "_anim", "_fb_motion")))

            if raw_type in ("video", "ai_video", "i2v", "t2v") or is_video_file:
                logger.info(
                    f"  Scene {scene.scene_id}: video ({raw_type}) → trim {duration:.1f}s"
                    f"{' (🔥 HOOK VIDEO CLIP)' if is_hook else ''}"
                    f"{' [Already Graded]' if is_already_rendered else f' [{clip_niche} Grading]' if clip_niche else ''}"
                )
                trim_video(
                    input_path=scene.media_path,
                    duration_seconds=duration,
                    output_path=clip_path,
                    width=job.resolution_w,
                    height=job.resolution_h,
                    fps=job.fps,
                    is_scifi=is_scifi_clip,
                    niche=clip_niche,
                    apply_grading=(not is_already_rendered),
                )
            elif raw_type in ("comic_anim", "multi_pose", "2d_loop") or getattr(scene, "pose_images", None):
                pose_list = getattr(scene, "pose_images", None) or [scene.media_path]
                logger.info(
                    f"  Scene {scene.scene_id}: Hoạt họa đa khung hình ({len(pose_list)} poses) → multi_pose_loop_to_video {duration:.1f}s"
                )
                multi_pose_loop_to_video(
                    image_paths=pose_list,
                    duration_seconds=duration,
                    output_path=clip_path,
                    width=job.resolution_w,
                    height=job.resolution_h,
                    fps=job.fps,
                    switch_interval=0.28,
                    niche=clip_niche,
                )
            elif raw_type in ("image", "ai_image", "photo", "documentary", "auto"):
                logger.info(
                    f"  Scene {scene.scene_id}: image ({raw_type}) → Ken Burns [{motion}] {duration:.1f}s"
                    f"{' (🔥 HOOK PUNCH-IN)' if is_hook else ''}{f' [{clip_niche} Grading]' if clip_niche else ''}"
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
                    is_scifi=is_scifi_clip,
                    niche=clip_niche,
                )
            else:
                # Fallback graceful: log warning, render như image
                logger.warning(
                    f"  Scene {scene.scene_id}: media_type không hợp lệ '{raw_type}' → fallback image mode"
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
                    is_scifi=is_scifi_clip,
                    niche=clip_niche,
                )

            return scene.scene_id, clip_path

        # ── Chạy song song với ThreadPoolExecutor ──
        # Mỗi worker dùng 2 threads (-threads 2) ở chế độ intermediate → chạy 8 workers song song trên CPU 16 luồng cực nhanh
        max_workers = min(len(scenes), max(4, min(8, (os.cpu_count() or 8) // 2)))
        results: dict[int, str] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(_render_one, idx, s): s.scene_id for idx, s in enumerate(scenes)}
            for future in as_completed(future_map, timeout=300):
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
