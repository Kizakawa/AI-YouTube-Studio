"""app.utils.ffmpeg.peak_and_trim — Quét đoạn cao trào (Peak Action Scanner) & cắt cúp chuẩn hóa video 9:16."""
import os
import re
import shutil
import subprocess
import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Union, Any, Tuple

logger = logging.getLogger(__name__)
from app.utils.ffmpeg.core_encoder import (
    BT709_COLOR_ARGS,
    _supports_fps_mode,
    get_duration_ms,
    get_video_encoder_args,
    probe_file,
    run_ffmpeg_with_gpu_fallback,
)
from app.utils.ffmpeg.color_and_motion import (
    get_hook_live_fx_filter,
    get_niche_color_grading,
)



# ──────────────────────────────────────────────
# CHECKPOINT B: Cắt + Ghép N clips
# ──────────────────────────────────────────────

import threading

_peak_metrics_cache_lock = threading.Lock()
_peak_metrics_cache: Dict[Tuple[str, int, int, float, int], Dict[str, Any]] = {}


def _scan_video_peak_metrics(
    input_path: str,
    max_scan_sec: float = 90.0,
    sample_fps: int = 4,
) -> Optional[Dict[str, Any]]:
    """
    Quét siêu tốc 1 lượt duy nhất (4fps @ 90x160 RGB24) có bộ nhớ đệm (memoize theo path + size + mtime_ns) để đo đồng thời:
    - Độ sáng (`lums`) & Độ tương phản (`contrasts`)
    - Chuyển động vùng trung tâm chủ thể 9:16 (`motions`: 65% Center-ROI + 35% Full-Frame)
    - Độ sắc nét & cuốn hút thị giác vùng trung tâm (`intrigues`: Center-ROI Edge Saliency)
    Giúp phát hiện chính xác khoảnh khắc hành động bùng nổ nhất (Peak Burst) mà không bao giờ phải giải mã lại cùng 1 file 2-3 lần.
    """
    cache_key: Optional[Tuple[str, int, int, float, int]] = None
    try:
        p_obj = Path(input_path)
        if p_obj.exists():
            st_info = p_obj.stat()
            cache_key = (str(p_obj.resolve()), int(st_info.st_size), int(st_info.st_mtime_ns), float(max_scan_sec), int(sample_fps))
            with _peak_metrics_cache_lock:
                cached_m = _peak_metrics_cache.get(cache_key)
                if cached_m is not None:
                    return cached_m
    except Exception:
        cache_key = None

    try:
        src_sec = get_duration_ms(input_path) / 1000.0
        if src_sec <= 0.5:
            return None

        scan_sec = min(src_sec, max_scan_sec)
        w, h = 90, 160
        frame_bytes = w * h * 3

        cmd = [
            shutil.which("ffmpeg") or "ffmpeg",
            "-v", "quiet",
            "-i", input_path,
            "-t", f"{scan_sec:.1f}",
            "-vf", f"fps={sample_fps},scale={w}:{h}",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-",
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=16)
        raw = res.stdout or b""
        n_frames = len(raw) // frame_bytes
        if n_frames < 4:
            return None

        from PIL import Image, ImageChops, ImageFilter, ImageStat
        lums: List[float] = []
        contrasts: List[float] = []
        motions: List[float] = []
        intrigues: List[float] = []
        # Vùng trung tâm khung hình dọc 9:16 (nơi chủ thể chính xuất hiện)
        roi_box = (18, 28, 72, 132)
        prev_g = None
        prev_roi = None

        for i in range(n_frames):
            chunk = raw[i * frame_bytes : (i + 1) * frame_bytes]
            im = Image.frombytes("RGB", (w, h), chunk)
            g = im.convert("L")
            roi_g = g.crop(roi_box)
            st = ImageStat.Stat(g)
            roi_st = ImageStat.Stat(roi_g)
            lums.append(float(st.mean[0]))
            # Ưu tiên độ tương phản của chủ thể vùng trung tâm kết hợp toàn khung
            contrasts.append(float(roi_st.stddev[0]) * 0.60 + float(st.stddev[0]) * 0.40)

            edges_full = g.filter(ImageFilter.FIND_EDGES)
            edges_roi = edges_full.crop(roi_box)
            ef_st = ImageStat.Stat(edges_full)
            er_st = ImageStat.Stat(edges_roi)
            full_intrigue = float(ef_st.mean[0]) * 0.65 + float(ef_st.stddev[0]) * 0.35
            roi_intrigue = float(er_st.mean[0]) * 0.65 + float(er_st.stddev[0]) * 0.35
            intrigues.append(roi_intrigue * 0.65 + full_intrigue * 0.35)

            if prev_g is not None and prev_roi is not None:
                diff_full = float(ImageStat.Stat(ImageChops.difference(g, prev_g)).mean[0])
                diff_roi = float(ImageStat.Stat(ImageChops.difference(roi_g, prev_roi)).mean[0])
                motions.append(diff_roi * 0.65 + diff_full * 0.35)
            else:
                motions.append(0.0)
            prev_g = g
            prev_roi = roi_g

        if len(motions) > 1:
            motions[0] = motions[1]

        out_metrics = {
            "src_sec": src_sec,
            "sample_fps": sample_fps,
            "n_frames": n_frames,
            "lums": lums,
            "contrasts": contrasts,
            "motions": motions,
            "intrigues": intrigues,
        }
        if cache_key is not None:
            with _peak_metrics_cache_lock:
                if len(_peak_metrics_cache) >= 128:
                    _peak_metrics_cache.pop(next(iter(_peak_metrics_cache)), None)
                _peak_metrics_cache[cache_key] = out_metrics
        return out_metrics
    except Exception as e:
        logger.debug(f"[_scan_video_peak_metrics] Lỗi quét frame metrics: {e}")
        return None


def find_hook_peak_action_offset(
    input_path: str,
    target_duration: float,
    exclude_ranges: Optional[List[Tuple[float, float]]] = None,
    _precomputed_metrics: Optional[Dict[str, Any]] = None,
) -> float:
    """
    Quét siêu tốc (4fps @ 90x160 RGB24) toàn bộ chiều dài video Short (tối đa 50 giây)
    để tìm đúng khoảnh khắc ĐỈNH CAO NHẤT (Center-ROI Peak Action + Burst Spike) & CUỐN HÚT NHẤT
    (Visual Intrigue / Edge Saliency + Contrast Drama) + Frame 0 sáng rõ.
    Hỗ trợ `exclude_ranges` để có thể lấy thêm đoạn Peak thứ 2 ghép vào thân video (Climax Scene) mà không trùng đoạn đầu.
    """
    try:
        metrics = _precomputed_metrics or _scan_video_peak_metrics(input_path, max_scan_sec=120.0, sample_fps=4)
        if not metrics:
            return 0.0
        src_sec = float(metrics["src_sec"])
        if src_sec <= target_duration + 0.6:
            return 0.0

        sample_fps = int(metrics["sample_fps"])
        n_frames = int(metrics["n_frames"])
        lums = metrics["lums"]
        contrasts = metrics["contrasts"]
        motions = metrics["motions"]
        intrigues = metrics["intrigues"]

        win_frames = max(int(target_duration * sample_fps), 4)
        if n_frames <= win_frames + 1:
            return 0.0

        def _is_excluded(start_idx: int) -> bool:
            if not exclude_ranges:
                return False
            sec = start_idx / float(sample_fps)
            end_s = sec + target_duration
            for ex_s, ex_e in exclude_ranges:
                if not (end_s <= ex_s or sec >= ex_e):
                    return True
            return False

        def _score_window(start_idx: int) -> float:
            if _is_excluded(start_idx):
                return -999.0
            win_end = min(start_idx + win_frames, n_frames)
            if win_end - start_idx < max(win_frames - 1, 3):
                return -999.0
            freeze_streak = 0
            dark_streak = 0
            for f_i in range(start_idx, win_end):
                m_val = motions[f_i]
                if m_val < 0.85:
                    freeze_streak += 1
                    if freeze_streak >= 3:
                        return -999.0
                else:
                    freeze_streak = 0
                # Chặn cứng mọi cửa sổ có khung hình tối đen hoặc cháy trắng bên trong (ngăn lỗi blackdetect >= 0.15s)
                if lums[f_i] < 24.0 or contrasts[f_i] < 10.0:
                    dark_streak += 1
                    if lums[f_i] < 16.0 or dark_streak >= 1:
                        return -999.0
                else:
                    dark_streak = 0
            f0_lum = lums[start_idx]
            f0_con = contrasts[start_idx]
            f1_idx = min(start_idx + 1, n_frames - 1)
            # Zero-Delay Peak Entry (0.0s - 0.5s): Chấm điểm chuyển động và độ nét chủ thể ngay tại khoảnh khắc mở màn
            instant_entry_motion = motions[start_idx] * 0.65 + motions[f1_idx] * 0.35
            instant_entry_intrigue = intrigues[start_idx] * 0.65 + intrigues[f1_idx] * 0.35
            # Phạt nặng nếu Frame 0 tối đen (< 50) hoặc cháy trắng (> 225) hoặc mờ phẳng (< 24)
            if f0_lum < 50.0 or f0_lum > 225.0 or f0_con < 24.0:
                penalty = -45.0
            else:
                penalty = 0.0
            # Phạt nếu 0.5s đầu tiên đứng hình / chưa vào nhịp chuyển động (tránh 0.5s delay làm người xem vuốt bỏ)
            if instant_entry_motion < 1.4:
                penalty -= 18.0
            # Khử thiên vị giây 0.0s (thường là đoạn intro tĩnh/chưa vào đòn của video gốc)
            if start_idx == 0 and src_sec >= target_duration + 2.5:
                penalty -= 4.5

            early_end = min(start_idx + 5, n_frames)
            burst_end = min(start_idx + 8, n_frames)
            early_motion = sum(motions[start_idx:early_end]) / max(early_end - start_idx, 1)
            # Peak Burst Spike: Thưởng lớn cho cửa sổ có cú nổ hành động mạnh nhất ngay trong 0.5s - 2.0s đầu
            burst_slice = motions[start_idx:burst_end]
            peak_burst = max(burst_slice) if burst_slice else early_motion
            win_motion = sum(motions[start_idx:win_end]) / max(win_end - start_idx, 1)
            win_intrigue = sum(intrigues[start_idx:win_end]) / max(win_end - start_idx, 1)
            f0_quality = min(f0_con / 45.0, 1.65) + (0.45 if 62.0 <= f0_lum <= 198.0 else 0.0)
            return (
                instant_entry_motion * 1.15
                + instant_entry_intrigue * 0.35
                + early_motion * 1.45
                + peak_burst * 0.75
                + win_motion * 1.10
                + win_intrigue * 0.45
                + f0_quality * 8.0
                + penalty
            )

        base_score = _score_window(0)
        best_idx = 0
        best_score = base_score
        max_start_idx = max(0, n_frames - win_frames - 2)
        actual_decoded_sec = n_frames / float(sample_fps)
        effective_src_sec = min(src_sec, actual_decoded_sec)

        for idx in range(1, max_start_idx + 1):
            sc = _score_window(idx)
            if sc > best_score:
                best_score = sc
                best_idx = idx

        if best_idx > 0 and best_score > -500.0 and (best_score >= base_score * 1.02 or lums[0] < 55.0 or motions[0] < 4.0 or exclude_ranges):
            seek_sec = round(best_idx / float(sample_fps), 2)
            if seek_sec + target_duration <= effective_src_sec - 0.35:
                logger.info(
                    f"  ⚡ [Hook Peak & Center-ROI Burst Seek] Tự động cắt đoạn Peak tại {seek_sec:.2f}s -> {seek_sec + target_duration:.2f}s "
                    f"(PeakScore {base_score:.1f} → {best_score:.1f})"
                )
                return seek_sec
    except Exception as e:
        logger.debug(f"[find_hook_peak_action_offset] Bỏ qua quét peak action: {e}")
    return 0.0


def find_hook_peak_window(
    input_path: str,
    min_duration: float = 3.5,
    max_duration: float = 15.0,
    exclude_ranges: Optional[List[Tuple[float, float]]] = None,
    _precomputed_metrics: Optional[Dict[str, Any]] = None,
) -> Tuple[float, float]:
    """
    Phát hiện trọn vẹn khoảng thời gian ĐỈNH CAO LIÊN TỤC (start_offset_sec, full_peak_duration_sec)
    trong 1 lượt quét FFmpeg duy nhất (Single-Pass Center-ROI Peak Scanner):
    - Tìm điểm bắt đầu cao trào (`peak_start`) bằng `find_hook_peak_action_offset`.
    - Quét mở rộng về phía sau (tối đa `max_duration`, mặc định 15s) ngay trên mảng `motions`/`intrigues` đã giải mã,
      chừng nào năng lượng chuyển động và độ chi tiết chủ thể trung tâm vẫn đang ở nhịp cao trào,
      tự động dừng lại ngay trước khi video gốc chuyển sang màn hình tĩnh / outro / chữ cuối video.
    """
    min_dur = max(2.5, float(min_duration or 3.5))
    max_dur = max(min_dur, float(max_duration or 15.0))
    try:
        metrics = _precomputed_metrics if _precomputed_metrics is not None else _scan_video_peak_metrics(input_path, max_scan_sec=90.0, sample_fps=4)
        if not metrics:
            return 0.0, round(min_dur, 2)
        src_sec = float(metrics["src_sec"])
        if src_sec <= 0.5:
            return 0.0, min_dur
        if src_sec <= min_dur + 0.6:
            return 0.0, round(max(2.0, src_sec), 2)

        peak_start = find_hook_peak_action_offset(
            input_path,
            min_dur,
            exclude_ranges=exclude_ranges,
            _precomputed_metrics=metrics,
        )
        avail_after_start = max(2.0, src_sec - peak_start - 0.15)
        scan_dur = min(avail_after_start, max_dur)
        if scan_dur <= min_dur + 0.5:
            return peak_start, round(scan_dur, 2)

        sample_fps = int(metrics["sample_fps"])
        start_frame_idx = max(0, int(round(peak_start * sample_fps)))
        max_scan_frames = int(round(scan_dur * sample_fps))
        lums = metrics["lums"][start_frame_idx : start_frame_idx + max_scan_frames]
        contrasts = metrics["contrasts"][start_frame_idx : start_frame_idx + max_scan_frames]
        motions = metrics["motions"][start_frame_idx : start_frame_idx + max_scan_frames]
        intrigues = metrics["intrigues"][start_frame_idx : start_frame_idx + max_scan_frames]

        n_frames = len(lums)
        min_frames = max(int(min_dur * sample_fps), 4)
        if n_frames <= min_frames + 1:
            return peak_start, round(min(avail_after_start, min_dur), 2)

        energies = [motions[i] * 1.45 + intrigues[i] * 0.48 for i in range(n_frames)]
        if len(energies) > 1:
            energies[0] = energies[1]

        core_avg_energy = sum(energies[:min_frames]) / max(min_frames, 1)
        energy_threshold = max(core_avg_energy * 0.52, 4.4)

        end_frame = min_frames
        low_energy_streak = 0
        for idx in range(min_frames, n_frames):
            cur_sec = peak_start + (idx / float(sample_fps))
            if exclude_ranges and any(ex_s < cur_sec < ex_e for ex_s, ex_e in exclude_ranges):
                break
            # Dừng NGAY LẬP TỨC nếu gặp frame tối đen / fade-to-black (< 25) để không bao giờ cắt lẹm vào đoạn chuyển cảnh đen!
            if lums[idx] < 25.0 or contrasts[idx] < 10.0:
                break
            # Dừng nếu gặp frame tối mờ, cháy sáng hoặc màn hình outro phẳng lì kéo dài
            if lums[idx] < 38.0 or lums[idx] > 232.0 or contrasts[idx] < 17.0:
                low_energy_streak += 1
            else:
                win_slice = energies[max(0, idx - 2) : min(n_frames, idx + 2)]
                local_e = sum(win_slice) / max(len(win_slice), 1)
                if local_e < energy_threshold:
                    low_energy_streak += 1
                else:
                    low_energy_streak = 0
                    end_frame = idx + 1
            if low_energy_streak >= 3:  # >= 0.75s tụt năng lượng / màn hình tĩnh -> kết thúc đoạn Peak
                break

        full_peak_dur = round(min(scan_dur, max(min_dur, end_frame / float(sample_fps))), 2)
        try:
            from app.utils.ffmpeg.smart_reframe_and_shot import snap_window_to_clean_shot_bounds
            peak_start, full_peak_dur = snap_window_to_clean_shot_bounds(
                peak_start=peak_start,
                peak_duration=full_peak_dur,
                min_duration=min_dur,
                metrics=metrics,
            )
        except Exception:
            pass
        logger.info(
            f"  🔥 [Full Peak Window] Phát hiện đoạn Peak tự nhiên dài {full_peak_dur:.2f}s "
            f"({peak_start:.2f}s -> {peak_start + full_peak_dur:.2f}s, năng lượng lõi={core_avg_energy:.1f})"
        )
        return peak_start, full_peak_dur
    except Exception as e:
        logger.debug(f"[find_hook_peak_window] Fallback về min_duration ({e})")
        return 0.0, round(min_dur, 2)


def trim_video(
    input_path: str,
    duration_seconds: float,
    output_path: str,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    is_scifi: bool = False,
    niche: Optional[str] = None,
    apply_grading: bool = True,
    is_hook_scene: bool = False,
    fair_use_transform: bool = False,
    start_offset_sec: Optional[float] = None,
    preserve_continuous_flow: bool = True,
) -> str:
    """
    Cắt video Pexels / YouTube Action về đúng duration cần thiết + scale/crop 9:16 + chuẩn hóa fps 30.
    Tự động lặp lại (stream_loop) nếu video stock ngắn hơn thời lượng lời thoại.
    Bỏ audio gốc của stock video.
    Áp dụng Cinematic Color Grading & Vignette chuẩn hóa theo từng Niche (100% Free Native FFmpeg Filter).
    Đặc biệt:
    - Khi `preserve_continuous_flow=True` (mặc định mới): Giữ nguyên mạch chuyển động mượt mà của đoạn Peak
      (kèm biến đổi Fair-Use lật ngang `hflip` + phóng nhẹ 106% + chỉnh màu điện ảnh) thay vì cắt vụn 3s,
      giúp các đoạn Peak dài chảy liền mạch qua nhiều phân cảnh.
    """
    # Tránh double-grading & double-flip (nếu clip đã qua image_to_video, trim_video, hoặc cắt từ YouTube Peak trước đó)
    input_filename = Path(input_path).name.lower()
    is_already_rendered = any(
        tag in input_filename
        for tag in (
            "_i2v", "_t2v", "_pexels_fb", "clip_", "_gsearch_3d", "_creature_3d",
            "_motion3d", "_anim", "_fb_motion", "hook_motion_", "hook_peak_",
            "hook_climax_", "_yt_peak", "_fullpeak", "_long_peak", "_vfirst",
            "_16x9", "bili_16x9_",
        )
    )
    should_grade = apply_grading and not is_already_rendered

    grading_parts = []
    if should_grade:
        grading = get_niche_color_grading(niche=niche, is_scifi=is_scifi, is_hook_scene=is_hook_scene)
        grading_parts.append(grading['vf_filter'])
        if grading.get("vignette"):
            grading_parts.append(grading['vignette'])

    # Đo kích thước & thời lượng thực tế của video nguồn (1 lần gọi ffprobe duy nhất)
    probe_info = probe_file(input_path)
    src_w, src_h = 0, 0
    src_sec_probed = 0.0
    if probe_info:
        try:
            src_sec_probed = float((probe_info.get("format") or {}).get("duration") or 0.0)
        except (ValueError, TypeError):
            src_sec_probed = 0.0
        for st in (probe_info.get("streams") or []):
            if st.get("codec_type") == "video":
                src_w = int(st.get("width") or 0)
                src_h = int(st.get("height") or 0)
                if src_w > 0 and src_h > 0:
                    break

    is_native_portrait = bool(src_w > 0 and src_h > 0 and src_w <= src_h * 1.05)
    is_landscape_source = bool(src_w > 0 and src_h > 0 and src_w > src_h * 1.15)

    # Giữ trọn vẹn chủ thể trong khung hình 9:16:
    # - Nếu nguồn đã là 9:16 Native Short (is_native_portrait): chỉ zoom vi mô 1.04x (khi bật fair_use) hoặc 1.0x để giữ 96-100% khung hình gốc.
    # - Nếu nguồn là 16:9 ngang (is_landscape_source) cần đưa vào khung 9:16: tuyệt đối KHÔNG cộng dồn zoom 1.18x (vì scale 9:16 đã tự động lấy trọn chiều dọc 100%, nếu zoom thêm sẽ làm mất góc chủ thể).
    if width < height:
        if is_native_portrait:
            # Khi bật fair_use_transform trên nguồn dọc (YouTube/Bilibili Shorts), phóng 1.16x để tự động cắt gọt sạch
            # 8% mép trên và 10% mép dưới (nơi thường chứa nhãn '+IA', phụ đề đốt cứng ở đáy hoặc logo góc của kênh gốc)
            zoom_mult = 1.16 if (fair_use_transform and not is_already_rendered) else 1.0
        elif is_landscape_source:
            zoom_mult = 1.0
        else:
            zoom_mult = 1.12 if (fair_use_transform and not is_already_rendered) else 1.02
    else:
        if is_already_rendered:
            zoom_mult = 1.0
        else:
            zoom_mult = 1.06 if fair_use_transform else 1.02

    has_letterbox = False
    top_bar_only = False
    bot_bar_only = False
    try:
        probe_ss = f"{start_offset_sec + 0.15:.2f}" if (start_offset_sec and start_offset_sec > 0) else "0.15"
        p_cmd = [
            shutil.which("ffmpeg") or "ffmpeg", "-v", "quiet",
            "-ss", probe_ss, "-i", input_path,
            "-frames:v", "1", "-vf", "scale=90:160", "-f", "rawvideo", "-pix_fmt", "gray", "-"
        ]
        p_res = subprocess.run(p_cmd, capture_output=True, timeout=4)
        raw_g = p_res.stdout or b""
        if len(raw_g) >= 90 * 160:
            top_slice = raw_g[: 90 * 12]
            bot_slice = raw_g[90 * 148 : 90 * 160]
            top_edge = sum(top_slice) / len(top_slice)
            bot_edge = sum(bot_slice) / len(bot_slice)
            top_std = (sum((px - top_edge) ** 2 for px in top_slice) / len(top_slice)) ** 0.5
            bot_std = (sum((px - bot_edge) ** 2 for px in bot_slice) / len(bot_slice)) ** 0.5
            mid_band = sum(raw_g[90 * 50 : 90 * 110]) / (90 * 60)
            # Kích hoạt cắt viền đen / viền mờ (Anti-Letterbox & Anti-Blurred-Padbar) khi nguồn dọc có 2 dải đen hoặc 2 dải mờ trên/dưới
            if (not is_landscape_source) and mid_band > 42.0:
                top_grad = sum(abs(top_slice[i + 1] - top_slice[i]) for i in range(len(top_slice) - 1)) / max(1, len(top_slice) - 1)
                bot_grad = sum(abs(bot_slice[i + 1] - bot_slice[i]) for i in range(len(bot_slice) - 1)) / max(1, len(bot_slice) - 1)
                mid_slice = raw_g[90 * 50 : 90 * 110]
                mid_grad = sum(abs(mid_slice[i + 1] - mid_slice[i]) for i in range(len(mid_slice) - 1)) / max(1, len(mid_slice) - 1)
                is_black_bar = top_edge < 13.5 and bot_edge < 13.5 and top_std < 4.5 and bot_std < 4.5
                is_blurred_padbar = mid_grad >= 4.2 and top_grad < mid_grad * 0.24 and bot_grad < mid_grad * 0.24
                if is_black_bar or is_blurred_padbar:
                    zoom_mult = 1.36 if is_blurred_padbar else 1.32
                    has_letterbox = True
                    logger.info(
                        f"  🔍 [Anti-Letterbox Crop] Phát hiện dải viền {'mờ (blurred padbar)' if is_blurred_padbar else 'đen'} trên/dưới "
                        f"(top_grad={top_grad:.1f}, bot_grad={bot_grad:.1f}, mid_grad={mid_grad:.1f}) "
                        f"-> Phóng {zoom_mult}x lấp đầy khung hình 9:16!"
                    )
            if is_hook_scene and not is_already_rendered and mid_band < 60.0:
                grading_parts.append("eq=brightness=0.04:contrast=1.06")
                logger.info(f"  💡 [First-Frame Visual Pop] Khung hình mở đầu Cảnh 1 hơi tối (luma={mid_band:.1f}) -> Tự động kích sáng +0.04 brightness & +6% contrast tại giây 0.0s!")
    except Exception:
        pass

    seek_args: List[str] = []
    src_sec_for_seek = src_sec_probed if src_sec_probed > 0 else (get_duration_ms(input_path) / 1000.0)
    eff_start_sec = float(start_offset_sec or 0.0)
    if start_offset_sec is not None and start_offset_sec >= 0.20:
        eff_start_sec = float(start_offset_sec)
    elif is_hook_scene and not is_already_rendered:
        seek_sec = find_hook_peak_action_offset(input_path, duration_seconds)
        if seek_sec >= 0.25:
            eff_start_sec = seek_sec
    # [Anti-Frame-Loop Seek Clamp]: Nếu mốc seek quá sát cuối file khiến phần còn lại ngắn hơn duration_seconds,
    # tự động lùi mốc cắt về trước để lấy trọn vẹn thời lượng tự nhiên, tuyệt đối không để hụt frame rồi lặp lại từ đầu!
    if eff_start_sec >= 0.20 and src_sec_for_seek > 0.5 and (src_sec_for_seek - eff_start_sec) < duration_seconds:
        clamped_start = max(0.0, round(src_sec_for_seek - duration_seconds - 0.08, 2))
        logger.debug(
            f"[trim_video] Lùi mốc seek {eff_start_sec:.2f}s -> {clamped_start:.2f}s "
            f"(src={src_sec_for_seek:.2f}s, req={duration_seconds:.2f}s) để chống hụt thời lượng & lặp khung hình"
        )
        eff_start_sec = clamped_start
    if eff_start_sec >= 0.20:
        seek_args = ["-ss", f"{eff_start_sec:.2f}"]

    grading_chain = ("," + ",".join(grading_parts)) if grading_parts else ""
    zw, zh = ((int(width * zoom_mult) // 2) * 2), ((int(height * zoom_mult) // 2) * 2)
    crop_x_ratio, crop_y_ratio = 0.50, (0.50 if (is_native_portrait or has_letterbox) else 0.42)
    if (not is_already_rendered) and (not is_native_portrait) and width < height:
        try:
            from app.utils.ffmpeg.smart_reframe_and_shot import compute_smart_reframe_crop_ratios
            crop_x_ratio, crop_y_ratio = compute_smart_reframe_crop_ratios(
                input_path=input_path,
                start_sec=eff_start_sec,
                duration_sec=duration_seconds,
                target_width=width,
                target_height=height,
            )
        except Exception:
            crop_x_ratio, crop_y_ratio = 0.50, (0.50 if has_letterbox else 0.42)
    if has_letterbox:
        if top_bar_only:
            crop_y_ratio = max(0.62, min(0.78, crop_y_ratio))
        elif bot_bar_only:
            crop_y_ratio = max(0.22, min(0.38, crop_y_ratio))
        else:
            crop_y_ratio = max(0.42, min(0.58, crop_y_ratio))

    if fair_use_transform and not is_already_rendered:
        # Tuyệt đối KHÔNG dùng hflip (lật gương ngang) vì sẽ làm ngược chữ/con số/biển báo trong cảnh quay thật
        eff_y_ratio = 0.44 if (is_native_portrait and not has_letterbox) else crop_y_ratio
        scale_crop_chain = (
            f"scale={zw}:{zh}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height}:(iw-{width})*{crop_x_ratio:.3f}:(ih-{height})*{eff_y_ratio:.3f}"
        )
    else:
        eff_sw, eff_sh = (zw, zh) if zoom_mult > 1.10 else (width, height)
        scale_crop_chain = (
            f"scale={eff_sw}:{eff_sh}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height}:(iw-{width})*{crop_x_ratio:.3f}:(ih-{height})*{crop_y_ratio:.3f}"
        )

    vf_chain = (
        f"{scale_crop_chain}"
        f"{grading_chain},"
        f"setsar=1,"
        f"fps={fps},format=yuv420p"
    )

    # TUYỆT ĐỐI KHÔNG dùng `-stream_loop -1` lặp lại từ đầu video (0.0s) giữa chừng phân cảnh!
    # Nếu clip ngắn hơn lời thoại (hoặc cộng thêm 0.16s-0.43s bù xfade/tail_buffer ở bước render),
    # dùng giãn nhịp mượt (setpts) kết hợp tpad clone frame cuối cực ngắn để 0% bị nhảy ngược về Frame 0!
    loop_args: List[str] = []
    pts_prefix = ""
    tpad_suffix = ""
    try:
        src_sec = src_sec_for_seek if src_sec_for_seek > 0 else (get_duration_ms(input_path) / 1000.0)
        eff_avail = max(0.1, src_sec - eff_start_sec) if seek_args else src_sec
        if 0.25 < eff_avail < duration_seconds:
            factor = duration_seconds / eff_avail
            shortfall = duration_seconds - eff_avail
            if shortfall <= 0.65:
                stretch_f = min(factor + 0.015, 1.38)
                pts_prefix = f"setpts={stretch_f:.4f}*PTS,"
                tpad_suffix = ",tpad=stop_mode=clone:stop_duration=0.12"
                vf_chain = pts_prefix + vf_chain + tpad_suffix
                logger.debug(
                    f"[trim_video] Bù đuôi mượt x{stretch_f:.3f} + tpad ({eff_avail:.2f}s → {duration_seconds:.2f}s, 0% loop)"
                )
            elif factor <= 2.85:
                pts_prefix = f"setpts={factor + 0.02:.4f}*PTS,"
                tpad_suffix = ",tpad=stop_mode=clone:stop_duration=0.10"
                vf_chain = pts_prefix + vf_chain + tpad_suffix
                logger.debug(
                    f"[trim_video] Slow-motion x{factor:.2f} thay vì lặp clip ({eff_avail:.1f}s → {duration_seconds:.1f}s)"
                )
            else:
                pts_prefix = "setpts=2.8500*PTS,"
                rem_pad = max(0.15, duration_seconds - (eff_avail * 2.80))
                tpad_suffix = f",tpad=stop_mode=clone:stop_duration={rem_pad:.2f}"
                vf_chain = pts_prefix + vf_chain + tpad_suffix
                logger.debug(
                    f"[trim_video] Clip ngắn ({eff_avail:.1f}s cho {duration_seconds:.1f}s) → setpts 2.85x + tpad {rem_pad:.2f}s (cấm lặp lại từ 0.0s)"
                )
        elif eff_avail >= duration_seconds:
            if fair_use_transform and not is_already_rendered and eff_avail >= duration_seconds * 1.06:
                pts_prefix = "setpts=0.9615*PTS,"
                vf_chain = pts_prefix + vf_chain
    except Exception:
        pass

    enc_args = get_video_encoder_args(quality="intermediate")
    if is_hook_scene and (not preserve_continuous_flow) and duration_seconds >= 1.8:
        t1 = round(duration_seconds * 0.28, 3)
        t2 = round(duration_seconds * 0.56, 3)
        t3 = round(duration_seconds * 0.80, 3)
        t4 = round(duration_seconds, 3)
        zw_h, zh_h = ((int(width * 1.08) // 2) * 2), ((int(height * 1.08) // 2) * 2)
        cw1, ch1 = max(2, (int(width / 1.24) // 2) * 2), max(2, (int(height / 1.24) // 2) * 2)
        cw2, ch2 = max(2, (int(width / 1.16) // 2) * 2), max(2, (int(height / 1.16) // 2) * 2)
        cw3, ch3 = max(2, (int(width / 1.36) // 2) * 2), max(2, (int(height / 1.36) // 2) * 2)
        fair_use_prefix = f"scale={zw_h}:{zh_h}:force_original_aspect_ratio=increase,crop={width}:{height}," if fair_use_transform else f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
        filter_complex = (
            f"[0:v]{pts_prefix}{fair_use_prefix}setsar=1,fps={fps},split=4[v0][v1][v2][v3];"
            f"[v0]trim=0:{t1},setpts=PTS-STARTPTS[s0];"
            f"[v1]trim={t1}:{t2},setpts=PTS-STARTPTS,"
            f"crop={cw1}:{ch1}:(iw-ow)*0.50:(ih-oh)*0.34,scale={width}:{height},setsar=1[s1];"
            f"[v2]trim={t2}:{t3},setpts=PTS-STARTPTS,"
            f"crop={cw2}:{ch2}:(iw-ow)*0.36:(ih-oh)*0.52,scale={width}:{height},setsar=1[s2];"
            f"[v3]trim={t3}:{t4},setpts=PTS-STARTPTS,"
            f"crop={cw3}:{ch3}:(iw-ow)*0.56:(ih-oh)*0.38,scale={width}:{height},setsar=1[s3];"
            f"[s0][s1][s2][s3]concat=n=4:v=1:a=0{grading_chain},setsar=1,fps={fps},format=yuv420p[vout]"
        )
        args = [
            *loop_args,
            *seek_args,
            "-i", input_path,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-t", str(duration_seconds),
            *enc_args,
            *BT709_COLOR_ARGS,
            "-r", str(fps),
            "-an",
            output_path,
        ]
        logger.info(
            f"  ⚡ [Hook Video Multi-Cut + Fair-Use 108% hflip] 4 shots (3 nhát cắt: 0s -> {t1:.2f}s -> {t2:.2f}s -> {t3:.2f}s -> {t4:.2f}s)"
        )
    else:
        args = [
            *loop_args,
            *seek_args,
            "-i", input_path,
            "-t", str(duration_seconds),
            "-vf", vf_chain,
            *enc_args,
            *BT709_COLOR_ARGS,
            "-r", str(fps),
            "-an",  # Bỏ audio gốc của stock video
            output_path
        ]
    run_ffmpeg_with_gpu_fallback(args)
    return output_path

