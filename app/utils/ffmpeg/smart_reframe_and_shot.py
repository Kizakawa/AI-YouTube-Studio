"""
app.utils.ffmpeg.smart_reframe_and_shot — Smart Auto-Reframe 9:16 & Clean Shot-Boundary Trimming.

Lấy cảm hứng từ kiến trúc của `ClipsAI` / `SamurAIGPT` & `PySceneDetect`:
1. `compute_smart_reframe_crop_ratios`:
   - Khi chuyển đổi video nguồn ngang (16:9) sang khung dọc Shorts (9:16), thay vì cắt cứng ở chính giữa
     (`(iw-ow)/2`) làm mất chủ thể nằm lệch trái/phải, hàm này quét nhanh 6 khung hình đại diện qua pipe
     RGB24 siêu nhẹ (160x90) và tính bản đồ năng lượng ngang (60% Optical Motion + 40% Edge Saliency)
     bằng NumPy để đặt cửa sổ crop 9:16 bám sát trọng tâm chủ thể hành động.
2. `snap_window_to_clean_shot_bounds`:
   - Phát hiện các điểm cắt cảnh cứng (hard cut / scene change) hoặc chớp đen ở đầu/cuối cửa sổ Peak
     ngay trên chuỗi metrics 4fps, tự động gọt bỏ các mẩu cảnh thừa (< 0.85s) ở rìa đầu/cuối hoặc chọn
     shot liền mạch dài nhất để video không bao giờ bị giật cảnh hoặc dính nửa giây màn hình đen.
"""

from __future__ import annotations

import logging
import shutil
import statistics
import subprocess
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def compute_smart_reframe_crop_ratios(
    input_path: str,
    start_sec: float = 0.0,
    duration_sec: float = 4.0,
    target_width: int = 1080,
    target_height: int = 1920,
    n_samples: int = 6,
) -> Tuple[float, float]:
    """
    Tính tỷ lệ dịch chuyển cửa sổ cắt `(x_ratio, y_ratio)` trong khoảng `[0.0, 1.0]`
    cho biểu thức FFmpeg `crop=W:H:(iw-W)*x_ratio:(ih-H)*y_ratio`.

    - Mặc định trả về `(0.50, 0.38)` (giữa ngang, hơi ưu tiên phần trên cho khuôn mặt/chủ thể).
    - Nếu video đầu ra là ngang (`target_width >= target_height`) hoặc không đọc được stream, trả về `(0.50, 0.50)`.
    """
    if target_width >= target_height:
        return 0.50, 0.50

    default_x, default_y = 0.50, 0.38
    try:
        dur = max(1.0, min(float(duration_sec or 4.0), 10.0))
        ss = max(0.0, float(start_sec or 0.0))
        sample_fps = max(1.0, min(3.0, n_samples / dur))
        sw, sh = 160, 90
        frame_bytes = sw * sh * 3

        cmd = [
            shutil.which("ffmpeg") or "ffmpeg",
            "-v", "quiet",
            "-ss", f"{ss:.2f}",
            "-i", input_path,
            "-t", f"{dur:.2f}",
            "-vf", f"fps={sample_fps:.2f},scale={sw}:{sh}",
            "-frames:v", str(max(3, int(n_samples))),
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-",
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=4, check=False)
        raw = res.stdout or b""
        n_frames = len(raw) // frame_bytes
        if n_frames < 2:
            return default_x, default_y

        frames = np.frombuffer(raw[: n_frames * frame_bytes], dtype=np.uint8).reshape((n_frames, sh, sw, 3))
        # Chuyển sang độ chói (luma) [n_frames, sh, sw]
        gray = (
            0.299 * frames[:, :, :, 0].astype(np.float32)
            + 0.587 * frames[:, :, :, 1].astype(np.float32)
            + 0.114 * frames[:, :, :, 2].astype(np.float32)
        )

        # Kiểm tra nếu 2 dải biên trái/phải tối đen (video gốc đã là 9:16 pillarbox trong khung 16:9) -> giữ chính giữa
        left_lum = float(gray[:, :, :24].mean())
        right_lum = float(gray[:, :, sw - 24 :].mean())
        mid_lum = float(gray[:, :, 56 : sw - 56].mean())
        if left_lum < 18.0 and right_lum < 18.0 and mid_lum > 35.0:
            return 0.50, default_y

        # 1. Năng lượng chuyển động theo thời gian (Optical Frame-Diff)
        motion_map = np.abs(np.diff(gray, axis=0)).mean(axis=0)  # [sh, sw]

        # 2. Năng lượng cạnh / độ nét chủ thể (Horizontal + Vertical Gradient Saliency)
        grad_x = np.abs(np.diff(gray, axis=2, prepend=gray[:, :, :1])).mean(axis=0)
        grad_y = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1, :])).mean(axis=0)
        edge_map = (grad_x + grad_y) * 0.5

        # Tổ hợp bản đồ năng lượng 2D (ưu tiên dải giữa theo chiều dọc 15%..85% để tránh dính phụ đề/watermark góc)
        v_mask = np.ones((sh, 1), dtype=np.float32)
        v_mask[: int(sh * 0.12), :] = 0.35
        v_mask[int(sh * 0.86) :, :] = 0.25
        energy_2d = (motion_map * 0.62 + edge_map * 0.38) * v_mask

        # Chiếu xuống trục ngang 1D (width = sw = 160)
        col_energy = energy_2d.mean(axis=0)  # [sw]
        total_energy = float(col_energy.sum())
        if total_energy <= 1e-3:
            return default_x, default_y

        # Chiều rộng cửa sổ 9:16 khi cắt từ khung 16:9 (tương đương ~51 cột trên 160 cột)
        win_w = max(32, min(sw - 8, int(round(sw * (target_width / float(target_height)) * (9.0 / 16.0)))))
        max_x0 = sw - win_w
        if max_x0 <= 2:
            return default_x, default_y

        # Quét cửa sổ trượt tìm vị trí có tổng năng lượng chủ thể cao nhất (kèm ưu tiên nhẹ ở trung tâm để chống giật biên)
        best_x0 = max_x0 / 2.0
        best_val = -1.0
        center_x0 = max_x0 / 2.0
        csum = np.cumsum(np.insert(col_energy, 0, 0.0))

        for x0 in range(0, max_x0 + 1):
            win_sum = float(csum[x0 + win_w] - csum[x0])
            dist_norm = abs(x0 - center_x0) / max(center_x0, 1.0)
            # Chỉ dịch khỏi tâm nếu vùng lệch tâm thực sự có năng lượng vượt trội (> 8% so với tâm)
            center_prior = 1.0 - 0.08 * (dist_norm ** 1.5)
            score = win_sum * center_prior
            if score > best_val:
                best_val = score
                best_x0 = float(x0)

        x_ratio = best_x0 / float(max_x0)
        # Kẹp trong vùng an toàn [0.08, 0.92] và làm mượt về 0.50 nếu độ lệch không đáng kể
        x_ratio = max(0.08, min(0.92, x_ratio))
        if abs(x_ratio - 0.50) < 0.06:
            x_ratio = 0.50
        else:
            logger.info(
                f"  🎯 [Smart Auto-Reframe 9:16] Bám trọng tâm chủ thể theo phương ngang: "
                f"x_ratio={x_ratio:.2f} (lệch tâm {(x_ratio - 0.50) * 100:+.0f}%)"
            )
        return round(x_ratio, 3), default_y
    except Exception as e:
        logger.debug(f"[compute_smart_reframe_crop_ratios] Fallback center crop: {e}")
        return default_x, default_y


def snap_window_to_clean_shot_bounds(
    peak_start: float,
    peak_duration: float,
    min_duration: float,
    metrics: Optional[Dict[str, Any]],
) -> Tuple[float, float]:
    """
     Tinh chỉnh `(peak_start, peak_duration)` dựa trên phát hiện biên giới Shot (`PySceneDetect` style)
    ngay trên chuỗi metrics 4fps:
    - Nếu trong vòng 0.75s đầu tiên hoặc 0.75s cuối cùng của cửa sổ có một cú cắt cảnh cứng (hard cut)
      hoặc sụt sáng đột ngột, tự động gọt bỏ phần rìa đó để tránh hiện tượng clip bắt đầu/kết thúc
      bằng vài khung hình lạc quẻ của cảnh trước/sau.
    - Nếu giữa cửa sổ có một cú cắt sang cảnh tối/tĩnh, tự động cắt dừng ngay trước điểm chuyển cảnh.
    """
    if not metrics:
        return peak_start, peak_duration

    try:
        sample_fps = int(metrics.get("sample_fps") or 4)
        lums: List[float] = metrics.get("lums") or []
        contrasts: List[float] = metrics.get("contrasts") or []
        motions: List[float] = metrics.get("motions") or []
        n_total = len(lums)
        if n_total < 6 or sample_fps <= 0:
            return peak_start, peak_duration

        s_idx = max(0, min(n_total - 2, int(round(peak_start * sample_fps))))
        e_idx = max(s_idx + 2, min(n_total, int(round((peak_start + peak_duration) * sample_fps))))
        min_frames = max(4, int(round(min_duration * sample_fps)))
        if e_idx - s_idx <= min_frames:
            return peak_start, peak_duration

        win_motions = motions[s_idx:e_idx]
        med_motion = statistics.median(win_motions) if win_motions else 3.0
        # Ngưỡng phát hiện hard cut: biến thiên frame-diff vọt gấp ~3.6 lần trung vị và kèm nhảy sáng
        hard_cut_motion_th = max(24.0, med_motion * 3.6)

        cut_offsets: List[int] = []
        for local_i in range(1, e_idx - s_idx):
            g_i = s_idx + local_i
            lum_jump = abs(lums[g_i] - lums[g_i - 1])
            con_jump = abs(contrasts[g_i] - contrasts[g_i - 1])
            if (motions[g_i] >= hard_cut_motion_th and (lum_jump >= 18.0 or con_jump >= 9.0)) or lum_jump >= 42.0:
                cut_offsets.append(local_i)

        if not cut_offsets:
            return peak_start, peak_duration

        win_len = e_idx - s_idx
        edge_margin = max(2, int(round(0.75 * sample_fps)))  # ~3 frames (0.75s)

        new_s_idx = s_idx
        new_e_idx = e_idx

        # 1. Nếu có cú hard cut ngay sát đầu cửa sổ (<= 0.75s đầu) và phần còn lại vẫn đủ min_frames -> nhảy qua cú cut đầu
        first_cut = cut_offsets[0]
        if first_cut <= edge_margin and (win_len - first_cut) >= min_frames:
            new_s_idx = s_idx + first_cut

        # 2. Nếu có cú hard cut ngay sát cuối cửa sổ (<= 0.75s cuối) và phần trước đó đủ min_frames -> dừng trước cú cut cuối
        last_cut = cut_offsets[-1]
        if (win_len - last_cut) <= edge_margin and (s_idx + last_cut - new_s_idx) >= min_frames:
            new_e_idx = s_idx + last_cut

        if new_s_idx != s_idx or new_e_idx != e_idx:
            clean_start = round(new_s_idx / float(sample_fps), 2)
            clean_dur = round(max(min_duration, (new_e_idx - new_s_idx) / float(sample_fps)), 2)
            logger.info(
                f"  ✂️ [Clean Shot-Boundary Snap] Gọt bỏ rìa chuyển cảnh thừa: "
                f"({peak_start:.2f}s, {peak_duration:.2f}s) -> ({clean_start:.2f}s, {clean_dur:.2f}s)"
            )
            return clean_start, clean_dur
    except Exception as e:
        logger.debug(f"[snap_window_to_clean_shot_bounds] Bỏ qua: {e}")

    return peak_start, peak_duration

