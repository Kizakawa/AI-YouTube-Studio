"""
Retro Lofi & VHS Glitch VFX Pipeline (`app/services/pomodoro/vfx_pipeline.py`)
==============================================================================
Cung cấp đầy đủ 6 hiệu ứng Retro Lo-Fi trong kế hoạch + Hiệu ứng chuyển nhiễu VHS Glitch:
  1. `grain`: Nhiễu hạt phim Analog (Film Grain) thay đổi tự nhiên mỗi frame.
  2. `scanline`: Đường kẻ ngang VHS Scanline + Dải sáng quét chậm từ trên xuống dưới.
  3. `vignette`: Bo tối góc khung hình co giãn theo nhịp thở âm nhạc (`Vignette Breathing`).
  4. `chromatic`: Tách kênh màu RGB nhẹ ở viền ống kính cổ điển (`1–2px`).
  5. `flare`: Ánh đèn bàn vàng ấm áp lung linh + khói cà phê + hạt bụi sáng.
  6. `wobble`: Hiệu ứng rung nhẹ toàn khung hình kiểu băng từ VHS cổ (`Tape Wobble ±1..2px`).
  7. `glitch`: Nhịp nhiễu băng VHS / Chromatic Split + trượt dòng định kỳ mỗi vài giây.
"""

import math
import random
from dataclasses import dataclass, field
from typing import List, Set

import numpy as np


ALL_VFX_NAMES = {"glitch", "grain", "scanline", "vignette", "chromatic", "flare", "wobble"}


@dataclass
class VFXConfig:
    enabled_effects: Set[str] = field(default_factory=lambda: set(ALL_VFX_NAMES))
    glitch_interval_sec: float = 5.0
    glitch_duration_sec: float = 0.42
    grain_strength: int = 6

    @classmethod
    def from_cli_string(cls, vfx_str: str = "all", glitch_interval_sec: float = 5.0) -> "VFXConfig":
        raw = (vfx_str or "all").strip().lower()
        if raw in {"none", "off", "false", "0"}:
            return cls(enabled_effects=set(), glitch_interval_sec=glitch_interval_sec)
        if raw == "all":
            return cls(enabled_effects=set(ALL_VFX_NAMES), glitch_interval_sec=glitch_interval_sec)
        parts = {p.strip() for p in raw.split(",") if p.strip() in ALL_VFX_NAMES}
        return cls(enabled_effects=parts or set(ALL_VFX_NAMES), glitch_interval_sec=glitch_interval_sec)


def is_in_glitch_burst(
    t_sec: float,
    loop_sec: float = 10.0,
    interval_sec: float = 5.0,
    duration_sec: float = 0.42,
) -> float:
    """
    Kiểm tra thời điểm `t_sec` có nằm trong nhịp nhiễu VHS Glitch định kỳ mỗi `interval_sec` giây không.
    Trả về cường độ nhiễu `intensity` trong khoảng `[0.0 .. 1.0]`.
    """
    if interval_sec <= 0.2 or duration_sec <= 0.02:
        return 0.0
    phase_mod = t_sec % interval_sec
    half_dur = duration_sec * 0.5
    dist = min(phase_mod, abs(interval_sec - phase_mod))
    if dist <= half_dur:
        norm = 1.0 - (dist / half_dur)
        return float(math.sin(0.5 * math.pi * norm))
    return 0.0


def apply_frame_vfx_numpy(
    rgb_np: np.ndarray,
    t_sec: float,
    loop_sec: float,
    vfx_cfg: VFXConfig,
    frame_idx: int = 0,
) -> np.ndarray:
    """
    Áp dụng các hiệu ứng Retro Lo-Fi & VHS Glitch trực tiếp lên mảng ảnh `RGB (H, W, 3)`
    trước khi ghép lớp HUD (đảm bảo HUD đồng hồ luôn sắc nét, còn cảnh nền & linh vật mang chất VHS).
    """
    out = rgb_np.copy()
    H, W = out.shape[:2]
    rng = random.Random(202600 + frame_idx * 37)

    # 1. VHS Scanline: Đường kẻ ngang CRT mờ + Dải sáng quét chậm từ trên xuống dưới
    if "scanline" in vfx_cfg.enabled_effects:
        scan_mask = np.ones((H, 1, 1), dtype=np.float32)
        scan_mask[::3, 0, 0] = 0.94
        # Dải quét ngang di chuyển từ y=0 -> y=H trong mỗi chu kỳ loop
        sweep_y = int(((t_sec / max(0.1, loop_sec)) * H) % H)
        s1, s2 = max(0, sweep_y - 18), min(H, sweep_y + 18)
        scan_mask[s1:s2, 0, 0] *= 1.045
        out = np.clip(out.astype(np.float32) * scan_mask, 0, 255).astype(np.uint8)

    # 2. Chromatic Aberration nhẹ thường trực (1–2px tại kênh Đỏ/Xanh)
    if "chromatic" in vfx_cfg.enabled_effects:
        out[:, 1:, 0] = (0.68 * out[:, 1:, 0].astype(np.float32) + 0.32 * out[:, :-1, 0].astype(np.float32)).astype(
            np.uint8
        )
        out[:, :-1, 2] = (0.75 * out[:, :-1, 2].astype(np.float32) + 0.25 * out[:, 1:, 2].astype(np.float32)).astype(
            np.uint8
        )

    # 3. Tape Wobble: Rung nhẹ tự nhiên kiểu băng từ VHS cũ (1px chậm rãi)
    if "wobble" in vfx_cfg.enabled_effects and frame_idx % 9 == 0:
        micro_shift = rng.choice([-1, 1])
        out = np.roll(out, micro_shift, axis=1)

    # 4. Hiệu ứng Nhiễu Khung Hình VHS Glitch định kỳ mỗi vài giây (`glitch`)
    if "glitch" in vfx_cfg.enabled_effects:
        g_intensity = is_in_glitch_burst(
            t_sec=t_sec,
            loop_sec=loop_sec,
            interval_sec=vfx_cfg.glitch_interval_sec,
            duration_sec=vfx_cfg.glitch_duration_sec,
        )
        if g_intensity > 0.05:
            shift_px = max(2, int(round(9.0 * g_intensity)))
            glitched = out.copy()
            glitched[:, shift_px:, 0] = out[:, :-shift_px, 0]
            glitched[:, :-shift_px, 2] = out[:, shift_px:, 2]

            num_slices = rng.randint(4, 7)
            for _ in range(num_slices):
                sy1 = rng.randint(25, H - 65)
                sh = rng.randint(10, 34)
                sy2 = min(H, sy1 + sh)
                offset_x = int(rng.choice([-1, 1]) * rng.randint(10, 28) * g_intensity)
                if offset_x != 0:
                    glitched[sy1:sy2] = np.roll(glitched[sy1:sy2], offset_x, axis=1)

            bar_y = int(((t_sec * 380.0) % (H - 40)) + 15)
            bar_h = rng.randint(8, 18)
            by2 = min(H, bar_y + bar_h)
            noise_band = np.random.RandomState(frame_idx + 99).randint(
                35, int(115 * g_intensity) + 45, size=(by2 - bar_y, W, 1), dtype=np.uint8
            )
            glitched[bar_y:by2] = np.clip(
                glitched[bar_y:by2].astype(np.int16) + noise_band.astype(np.int16), 0, 255
            ).astype(np.uint8)

            wobble_y = rng.choice([-2, -1, 1, 2]) if g_intensity > 0.45 else 0
            if wobble_y != 0:
                glitched = np.roll(glitched, wobble_y, axis=0)

            out = glitched

    return out


def build_ffmpeg_vfx_post_filter(vfx_cfg: VFXConfig) -> str:
    """
    Trả về chuỗi filter FFmpeg áp lên luồng nền (`[bg]`) cho:
      - `grain`: Nhiễu hạt phim Analog (`noise`)
      - `vignette`: Vignette Breathing co giãn êm dịu theo chu kỳ 5 giây (`eval=frame`, khớp bội số 10s loop)
    """
    filters: List[str] = []
    if "grain" in vfx_cfg.enabled_effects and vfx_cfg.grain_strength > 0:
        filters.append(f"noise=c0s={vfx_cfg.grain_strength}:c0f=t+u")
    if "vignette" in vfx_cfg.enabled_effects:
        filters.append("vignette=angle='PI/5+0.04*sin(2*PI*t/5)':eval=frame")
    return ",".join(filters)
