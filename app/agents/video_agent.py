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
import os
import shutil
from pathlib import Path
from typing import List, Optional

from app.schemas.contracts import RenderJobContract, SceneContract
from app.utils.ffmpeg_helper import (
    image_to_video,
    render_opening_hook_multi_cut,
    multi_pose_loop_to_video,
    trim_video,
    concat_videos,
    concat_videos_with_xfade,
    resolve_niche_transitions,
    mux_audio_video,
    mix_sfx_transitions,
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
            total_scenes = len(job.scenes)
            transition_duration = 0.0 if total_scenes >= 22 else 0.16
            tail_buffer = 0.15

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

            # ── Step 3: Ghép audio narration + BGM + Selective 3-Point Micro-SFX (-17dB) ──
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
                normalize_loudness=True,  # Luôn chuẩn hóa EBU R128 (-14 LUFS) cho giọng đọc + BGM
            )

            final_video_stage = muxed_video
            if os.getenv("DISABLE_CINEMATIC_SFX", "false").lower() not in ("1", "true", "yes"):
                from app.utils.ffmpeg.audio_and_sfx import resolve_smart_sfx_cue
                scene_timestamps = [0.0]
                cum_sec = 0.0
                for d in clip_durations[:-1]:
                    cum_sec += max(0.5, d - transition_duration)
                    scene_timestamps.append(round(cum_sec, 3))
                sfx_cues = [
                    resolve_smart_sfx_cue(
                        narration=s.narration,
                        scene_idx=i + 1,
                        total_scenes=total_scenes,
                        niche=job.niche,
                        explicit_cue=getattr(s, "sfx_cue", None),
                    )
                    for i, s in enumerate(job.scenes)
                ]
                sfx_mixed_video = str(work_dir / "muxed_sfx.mp4")
                sfx_res = mix_sfx_transitions(
                    video_path=muxed_video,
                    output_path=sfx_mixed_video,
                    scene_timestamps_seconds=scene_timestamps,
                    sfx_dir=sfx_dir,
                    sfx_cues=sfx_cues,
                    sfx_volume=0.14,
                    hit_sfx="impact_sub",
                    hit_volume=0.16,
                )
                if sfx_res and Path(sfx_res).exists():
                    final_video_stage = sfx_res

            # Copy video hoàn chỉnh (đã có xfade + subtitles + audio ducking + SFX) ra output cuối
            import shutil
            shutil.copy2(final_video_stage, job.output_path)

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
        transition_duration = 0.0 if total_scenes >= 22 else 0.16
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
            is_already_rendered = bool(
                scene.media_path
                and any(
                    tag in Path(scene.media_path).name.lower()
                    for tag in (
                        "_i2v", "_t2v", "_pexels_fb", "clip_", "_gsearch_3d", "_creature_3d",
                        "_motion3d", "_anim", "_fb_motion", "hook_motion_", "hook_peak_",
                        "hook_climax_", "_yt_peak", "_fullpeak", "_long_peak", "_vfirst",
                        "_16x9", "_doodle", "_clean_broll", "_motion_rescue",
                    )
                )
            )

            if raw_type == "hook_ready_video" or (scene.media_path and Path(scene.media_path).name.lower().startswith(("hook_motion_", "hook_peak_", "hook_climax_"))):
                from app.utils.ffmpeg_helper import get_duration_ms
                actual_hook_sec = get_duration_ms(scene.media_path) / 1000.0
                if actual_hook_sec > 0.5 and abs(actual_hook_sec - duration) <= 0.04 and job.resolution_w == 1080:
                    logger.info(
                        f"  Scene {scene.scene_id}: 🎯 HOOK/PEAK READY VIDEO ({Path(scene.media_path).name}, {actual_hook_sec:.1f}s) → "
                        f"khớp thời lượng ({duration:.1f}s), copy trực tiếp"
                    )
                    if os.path.abspath(scene.media_path) != os.path.abspath(clip_path):
                        shutil.copy2(scene.media_path, clip_path)
                else:
                    logger.info(
                        f"  Scene {scene.scene_id}: 🎯 HOOK/PEAK READY VIDEO ({Path(scene.media_path).name}, {actual_hook_sec:.1f}s) → "
                        f"đồng bộ thời lượng sang {duration:.1f}s ({job.resolution}) để chống hụt xfade"
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
                        apply_grading=False,
                        is_hook_scene=False,
                        fair_use_transform=False,
                        preserve_continuous_flow=True,
                    )
            elif raw_type in ("video", "ai_video", "i2v", "t2v") or is_video_file:
                if is_already_rendered and job.resolution_w == 1920:
                    from app.utils.ffmpeg_helper import get_duration_ms
                    actual_v_sec = get_duration_ms(scene.media_path) / 1000.0
                    if actual_v_sec >= duration - 0.05:
                        logger.info(
                            f"  Scene {scene.scene_id}: ⚡ 16:9 Ready Clip ({Path(scene.media_path).name}, {actual_v_sec:.1f}s >= {duration:.1f}s) → copy trực tiếp sang Single-Pass Concat"
                        )
                        if os.path.abspath(scene.media_path) != os.path.abspath(clip_path):
                            shutil.copy2(scene.media_path, clip_path)
                        return (scene.scene_id, clip_path)
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
                    is_hook_scene=is_hook,
                    fair_use_transform=False,
                    preserve_continuous_flow=True,
                )
            elif getattr(scene, "pose_images", None) and len(getattr(scene, "pose_images", [])) >= 2:
                pose_list = list(getattr(scene, "pose_images", None) or [scene.media_path])
                # Tự động tính số lượng ảnh tối đa khớp với thời lượng câu đọc (tối thiểu 2.0s/ảnh, tối đa 2 ảnh/cảnh để Ken Burns mượt mà, không nháy hình)
                min_sec_per_img = 1.7
                max_usable_imgs = max(1, min(2, int(duration // min_sec_per_img)))
                usable_poses = pose_list[:max_usable_imgs]
                dropped_count = max(0, len(pose_list) - len(usable_poses))
                if len(usable_poses) >= 2:
                    target_cuts = len(usable_poses) - 1
                    sec_per_shot = duration / len(usable_poses)
                    logger.info(
                        f"  Scene {scene.scene_id}: ⚡ {'HOOK' if is_hook else 'BODY'} DYNAMIC MULTI-IMAGE "
                        f"(Dùng {len(usable_poses)}/{len(pose_list)} ảnh, bỏ qua {dropped_count} ảnh thừa → "
                        f"đổi ảnh mỗi {sec_per_shot:.2f}s trong tổng {duration:.1f}s)"
                    )
                    render_opening_hook_multi_cut(
                        image_paths=usable_poses,
                        duration_seconds=duration,
                        output_path=clip_path,
                        width=job.resolution_w,
                        height=job.resolution_h,
                        fps=job.fps,
                        min_cuts=target_cuts,
                        is_scifi=is_scifi_clip,
                        niche=clip_niche,
                        is_hook_scene=is_hook,
                    )
                else:
                    logger.info(
                        f"  Scene {scene.scene_id}: Câu thoại ngắn ({duration:.1f}s) → dùng 1 ảnh chính (bỏ qua {dropped_count} ảnh thừa) với Ken Burns [{motion}]"
                    )
                    image_to_video(
                        image_path=usable_poses[0],
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
            elif raw_type in ("image", "ai_image", "photo", "documentary", "auto"):
                if duration >= 3.0:
                    logger.info(
                        f"  Scene {scene.scene_id}: image ({raw_type}) dài {duration:.1f}s (>= 3.0s) → "
                        f"2-Shot Dynamic Camera Cut (Focal Punch-In 1.28x giữa cảnh)"
                        f"{f' [{clip_niche} Grading]' if clip_niche else ''}"
                    )
                    render_opening_hook_multi_cut(
                        image_paths=[scene.media_path],
                        duration_seconds=duration,
                        output_path=clip_path,
                        width=job.resolution_w,
                        height=job.resolution_h,
                        fps=job.fps,
                        min_cuts=1,
                        is_scifi=is_scifi_clip,
                        niche=clip_niche,
                        is_hook_scene=is_hook,
                    )
                else:
                    logger.info(
                        f"  Scene {scene.scene_id}: image ({raw_type}) → Ken Burns [{motion}] {duration:.1f}s"
                        f"{f' [{clip_niche} Grading]' if clip_niche else ''}"
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

            # ── CỔNG TỰ CHỮA LÀNH KHUNG HÌNH ĐEN CHO TỪNG CLIP TRƯỚC KHI GHÉP (PRE-CONCAT BLACKDETECT GUARD) ──
            # Đảm bảo 100% không có clip đơn lẻ nào chứa khoảng tối/chớp đen >= 0.10s lọt vào bộ ghép xfade!
            try:
                import re as _re_cb
                import subprocess as _sp_cb
                vf_cb = "crop=800:760:140:280,blackdetect=d=0.10:pic_th=0.98"
                r_cb = _sp_cb.run(
                    ["ffmpeg", "-v", "info", "-i", clip_path, "-vf", vf_cb, "-an", "-f", "null", "-"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                cb_log = (r_cb.stderr or "") + "\n" + (r_cb.stdout or "")
                cb_matches = list(
                    _re_cb.finditer(
                        r"black_start:\s*([0-9.]+)\s+black_end:\s*([0-9.]+)\s+black_duration:\s*([0-9.]+)",
                        cb_log,
                    )
                )
                if cb_matches:
                    max_cb = max(float(m.group(3)) for m in cb_matches)
                    if max_cb >= 0.10:
                        bs_0 = float(cb_matches[0].group(1))
                        be_0 = float(cb_matches[0].group(2))
                        logger.warning(
                            f"  ⚠️ Scene {scene.scene_id}: Phát hiện chớp đen {max_cb:.2f}s ({bs_0:.2f}s->{be_0:.2f}s) "
                            f"trong '{Path(clip_path).name}' → Tự động trích xuất khung hình sáng rõ và dựng lại Ken Burns sạch 100%!"
                        )
                        rescue_t = round(bs_0 * 0.5, 2) if bs_0 >= 0.5 else round(min(duration - 0.25, be_0 + 0.45), 2)
                        rescue_jpg = str(Path(work_dir) / f"rescue_nobk_{scene.scene_id:03d}.jpg")
                        _sp_cb.run(
                            [
                                "ffmpeg", "-v", "error", "-y",
                                "-ss", f"{max(0.05, rescue_t):.2f}", "-i", clip_path,
                                "-frames:v", "1", "-q:v", "2", rescue_jpg,
                            ],
                            capture_output=True,
                            timeout=8,
                            check=False,
                        )
                        pose_candidates = [
                            p for p in (getattr(scene, "pose_images", None) or [])
                            if p and Path(p).exists() and str(p).lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
                        ]
                        if Path(rescue_jpg).exists() and Path(rescue_jpg).stat().st_size > 5000:
                            pose_candidates.insert(0, rescue_jpg)
                        if pose_candidates:
                            if len(pose_candidates) >= 2 and duration >= 3.2:
                                render_opening_hook_multi_cut(
                                    image_paths=pose_candidates[:2],
                                    duration_seconds=duration,
                                    output_path=clip_path,
                                    width=job.resolution_w,
                                    height=job.resolution_h,
                                    fps=job.fps,
                                    min_cuts=1,
                                    is_scifi=is_scifi_clip,
                                    niche=clip_niche,
                                    is_hook_scene=is_hook,
                                )
                            else:
                                image_to_video(
                                    image_path=pose_candidates[0],
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
                            logger.info(f"  ✅ Scene {scene.scene_id}: Đã khử sạch 100% khung hình đen bằng Ken Burns từ ảnh thật sáng rõ!")
            except Exception as ex_cb:
                logger.debug(f"[PreConcatBlackGuard] Scene {scene.scene_id} check skip: {ex_cb}")

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

        # Sắp xếp lại theo scene_id (as_completed không giữ thứ tự).
        # Tuyệt đối KHÔNG cắt thủng giữa cảnh để chèn clip của cảnh khác vào (_inject_dynamic_bcam_cuts),
        # đảm bảo 100% mỗi phân cảnh giữ nguyên mạch hình ảnh/video đúng với câu đọc của phân cảnh đó!
        ordered_clips = [results[s.scene_id] for s in scenes]
        return ordered_clips

    def _inject_dynamic_bcam_cuts(
        self,
        ordered_clips: List[str],
        scenes: List[SceneContract],
        work_dir: str,
        job: RenderJobContract,
    ) -> List[str]:
        """
        Đảm bảo:
        1. Cảnh 1 (Hook 0-3s) LUÔN có nhát cắt chuyển góc máy B-Cam thực thụ tại 1.25s -> 2.20s (scdet > 15).
        2. Bất kỳ phân cảnh nào dài >= 6.2s đều được chèn 1 nhát cắt B-Cam (1.0s) ở giữa cảnh để không bao giờ đứng cảnh lâu quá 5.5s.
        """
        if len(ordered_clips) < 2:
            return ordered_clips

        from app.utils.ffmpeg_helper import get_duration_ms, get_video_encoder_args, run_ffmpeg_with_gpu_fallback
        w, h, fps = job.resolution_w, job.resolution_h, job.fps
        enc_args = get_video_encoder_args(quality="intermediate")
        updated_clips = list(ordered_clips)

        for idx, clip_p in enumerate(ordered_clips):
            dur_s = get_duration_ms(clip_p) / 1000.0
            sc_obj = scenes[idx] if idx < len(scenes) else None
            sc_type = (getattr(sc_obj, "media_type", "") or "").lower()
            has_multi_poses = bool(getattr(sc_obj, "pose_images", None) and len(getattr(sc_obj, "pose_images", [])) >= 2)
            is_real_video_scene = sc_type in ("hook_ready_video", "video", "ai_video", "i2v", "t2v") or bool(
                getattr(sc_obj, "media_path", None) and str(sc_obj.media_path).lower().endswith((".mp4", ".mov", ".webm", ".mkv"))
            )

            # TUYỆT ĐỐI KHÔNG chèn cảnh 3 cắt ngang vào giữa Cảnh 1 (giây 1.20s-2.15s) khi Cảnh 1 là Video hành động thật
            # hoặc đã có bộ đa ảnh pose_images riêng — tránh làm đứt mạch thị giác ở giây 1.2s khiến người xem vuốt bỏ!
            if idx == 0:
                continue
            elif idx > 0 and dur_s >= 6.5 and not is_real_video_scene and not has_multi_poses:
                bcam_idx = (idx + 1) % len(ordered_clips)
                bcam_p = ordered_clips[bcam_idx]
                out_p = str(Path(work_dir) / f"clip_bcam_body_{idx:03d}.mp4")
                t_mid = round(dur_s * 0.48, 2)
                t_end_b = round(min(t_mid + 0.95, dur_s - 0.8), 2)
                fc = (
                    f"[0:v]split=2[a0][a1];"
                    f"[a0]trim=0:{t_mid},setpts=PTS-STARTPTS[s0];"
                    f"[1:v]trim=0.2:{0.2 + (t_end_b - t_mid):.3f},setpts=PTS-STARTPTS,scale={w}:{h},setsar=1[s1];"
                    f"[a1]trim={t_end_b}:{dur_s:.3f},setpts=PTS-STARTPTS[s2];"
                    f"[s0][s1][s2]concat=n=3:v=1:a=0,fps={fps},format=yuv420p[vout]"
                )
                args = ["-i", clip_p, "-i", bcam_p, "-filter_complex", fc, "-map", "[vout]", "-t", f"{dur_s:.3f}", *enc_args, "-r", str(fps), "-an", out_p]
                try:
                    run_ffmpeg_with_gpu_fallback(args)
                    if Path(out_p).exists() and Path(out_p).stat().st_size > 10000:
                        updated_clips[idx] = out_p
                        logger.info(f"  ⚡ [Body B-Cam Cutaway] Đã chèn cắt cảnh giữa cho Scene {idx + 1} ({dur_s:.2f}s)")
                except Exception as ex_bb:
                    logger.debug(f"[Body B-Cam Cutaway] Bỏ qua: {ex_bb}")

        return updated_clips

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
