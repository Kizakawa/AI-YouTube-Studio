"""
Scene Composer & Seasonal Theme Engine (`app/services/pomodoro/scene_composer.py`)
==================================================================================
Kết hợp Cảnh nền + Hệ thống 6 Chuyển động Linh Vật (`MascotEngine`) + Thời tiết theo Mùa (`ThemePack`)
+ Hiệu ứng VHS Glitch/Retro (`VFXConfig`) thành đoạn Video Loop Cinemagraph 1920x1080 mượt mà.
Hỗ trợ tạo riêng Loop cho phiên Học (`FOCUS`) và phiên Nghỉ (`BREAK` — linh vật thức dậy vẫy đuôi).
"""

import hashlib
import json
import math
import random
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw

from app.services.pomodoro.mascot_engine import (
    AnimationController,
    MascotPack,
    generate_cozy_dog_study_background,
    load_mascot_pack,
)
from app.services.pomodoro.scheduler import DEFAULT_CATALOG_BGM_ROOT, DEFAULT_THEMES_ROOT
from app.services.pomodoro.vfx_pipeline import VFXConfig, apply_frame_vfx_numpy


@dataclass
class ThemePack:
    id: str
    display_name: str
    season: str
    particle_type: str  # "rain", "snow", "sakura", "fireflies", "snow_lights", "embers"
    particle_color_rgba: Tuple[int, int, int, int]
    accent_hex: str
    break_accent_hex: str
    color_tint_rgb: Tuple[float, float, float]
    lamp_glow_rgba: Tuple[int, int, int, int]
    subtitle_tag: str


def list_available_themes(themes_root: Path = DEFAULT_THEMES_ROOT) -> List[str]:
    """Liệt kê tất cả `theme_id` trong `assets/themes/`."""
    if not themes_root.exists():
        return ["autumn_cozy"]
    ids = [
        d.name
        for d in sorted(themes_root.iterdir())
        if d.is_dir() and not d.name.startswith("_") and (d / "theme.json").exists()
    ]
    return ids or ["autumn_cozy"]


def load_theme_pack(
    theme_id: str = "autumn_cozy",
    themes_root: Path = DEFAULT_THEMES_ROOT,
) -> ThemePack:
    """Đọc cấu hình `theme.json` cho mùa/chủ đề được chọn."""
    cfg_path = themes_root / theme_id / "theme.json"
    if not cfg_path.exists():
        cfg_path = themes_root / "autumn_cozy" / "theme.json"

    if cfg_path.exists():
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    else:
        data = {}

    p_col = data.get("particle_color_rgba", [200, 225, 255, 110])
    tint = data.get("color_tint_rgb", [1.0, 0.98, 0.94])
    lglow = data.get("lamp_glow_rgba", [255, 195, 95, 24])

    return ThemePack(
        id=data.get("id", theme_id),
        display_name=data.get("display_name", "Autumn Rainy Study 🍂"),
        season=data.get("season", "autumn"),
        particle_type=data.get("particle_type", "rain"),
        particle_color_rgba=(int(p_col[0]), int(p_col[1]), int(p_col[2]), int(p_col[3])),
        accent_hex=data.get("accent_hex", "#F5B041"),
        break_accent_hex=data.get("break_accent_hex", "#82E0AA"),
        color_tint_rgb=(float(tint[0]), float(tint[1]), float(tint[2])),
        lamp_glow_rgba=(int(lglow[0]), int(lglow[1]), int(lglow[2]), int(lglow[3])),
        subtitle_tag=data.get("subtitle_tag", "Cozy Rain • Warm Study Lamp • Living Companion"),
    )


def ensure_mascot_cinemagraph_loop(
    work_dir: Path,
    mascot_pack: MascotPack,
    theme_pack: ThemePack,
    vfx_cfg: VFXConfig,
    is_break: bool = False,
    fps: int = 24,
    loop_sec: float = 10.0,
    ffmpeg_exe: str = "ffmpeg",
    force_rebuild: bool = False,
) -> Path:
    """
    Tạo (hoặc lấy từ cache băm) đoạn Video Loop Cinemagraph 1920x1080 (`.mp4`) tích hợp:
      - Hệ thống 6 chuyển động linh vật (`MascotEngine`: thở sâu, vẩy tai, vẫy đuôi, xoay đầu, vươn vai, thức dậy lúc nghỉ).
      - Hạt thời tiết theo mùa ngoài cửa sổ (`rain`, `snow`, `sakura`, `fireflies`, `snow_lights`, `embers`).
      - Ánh đèn bàn thở ấm áp + hạt bụi vàng bay lơ lửng + khói cà phê uốn lượn.
      - Hiệu ứng nhiễu băng VHS Glitch / Chromatic Split định kỳ mỗi vài giây (`vfx_cfg`).
    """
    vfx_sig = ",".join(sorted(vfx_cfg.enabled_effects)) + f"_gi{vfx_cfg.glitch_interval_sec:g}"
    state_tag = "break" if is_break else "focus"
    sig_hash = hashlib.md5(
        f"v2.2_{mascot_pack.id}_{theme_pack.id}_{state_tag}_{vfx_sig}_{fps}_{loop_sec}".encode("utf-8")
    ).hexdigest()[:10]

    cache_dir = DEFAULT_CATALOG_BGM_ROOT / "loops_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached_loop = cache_dir / f"loop_{mascot_pack.id}_{theme_pack.id}_{state_tag}_{sig_hash}.mp4"
    if not force_rebuild and cached_loop.exists() and cached_loop.stat().st_size > 120_000:
        return cached_loop

    src_art = mascot_pack.resolved_bg_path
    if src_art is None or not src_art.exists():
        src_art = generate_cozy_dog_study_background(
            work_dir / f"fallback_{mascot_pack.id}.png",
            mascot_id=mascot_pack.id,
        )

    base_im = Image.open(str(src_art)).convert("RGBA").resize((1920, 1080), Image.Resampling.LANCZOS)
    base_np = np.array(base_im, dtype=np.uint8)

    # Áp dụng bảng màu theo Mùa (`color_tint_rgb`)
    tr, tg, tb = theme_pack.color_tint_rgb
    if abs(tr - 1.0) > 0.01 or abs(tg - 1.0) > 0.01 or abs(tb - 1.0) > 0.01:
        tinted = base_np.astype(np.float32)
        tinted[..., 0] = np.clip(tinted[..., 0] * tr, 0, 255)
        tinted[..., 1] = np.clip(tinted[..., 1] * tg, 0, 255)
        tinted[..., 2] = np.clip(tinted[..., 2] * tb, 0, 255)
        base_np = tinted.astype(np.uint8)

    rng = random.Random(2026)
    window_panes = [
        (155, 18, 535, 375),  # Ô cửa kính trái
        (580, 18, 865, 395),  # Ô cửa kính phải
    ]
    weather_particles: List[Dict[str, Any]] = []
    for px1, py1, px2, py2 in window_panes:
        count = 52 if theme_pack.particle_type == "rain" else 36
        for _ in range(count):
            weather_particles.append(
                {
                    "px1": px1,
                    "py1": py1,
                    "px2": px2,
                    "py2": py2,
                    "x0": rng.uniform(px1 + 8, px2 - 8),
                    "y0": rng.uniform(py1, py2),
                    "speed": rng.choice([1.0, 2.0, 3.0]) if theme_pack.particle_type == "rain" else rng.choice([1.0, 2.0]),
                    "len": rng.randint(18, 34),
                    "r": rng.randint(3, 6),
                    "phase": rng.uniform(0.0, 2.0 * math.pi),
                    "alpha": rng.randint(75, 155),
                }
            )

    dust_motes: List[Dict[str, Any]] = []
    for _ in range(18):
        dust_motes.append(
            {
                "x": rng.uniform(840, 1220),
                "y": rng.uniform(280, 610),
                "amp_x": rng.uniform(8.0, 18.0),
                "amp_y": rng.uniform(10.0, 22.0),
                "phase": rng.uniform(0.0, 2.0 * math.pi),
                "r": rng.choice([2, 3]),
                "alpha": rng.randint(80, 150),
            }
        )

    anim_ctrl = AnimationController(mascot=mascot_pack, loop_sec=loop_sec, fps=fps)
    total_frames = int(round(fps * loop_sec))
    frames_dir = work_dir / f"_frames_{mascot_pack.id}_{state_tag}"
    frames_dir.mkdir(parents=True, exist_ok=True)

    for f_i in range(total_frames):
        prog = f_i / float(total_frames)
        t_sec = prog * loop_sec
        theta = 2.0 * math.pi * prog

        frame_np = base_np.copy()

        # 1. Thực thi 6 hiệu ứng chuyển động Linh Vật (Thở sâu, Vẩy tai, Vẫy đuôi, Nghiêng đầu, Vươn vai, Tỉnh giấc)
        frame_np = anim_ctrl.apply_mascot_animations(
            frame_np=frame_np,
            base_np=base_np,
            t_sec=t_sec,
            is_break=is_break,
        )

        frame_im = Image.fromarray(frame_np)
        fx_layer = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
        fdr = ImageDraw.Draw(fx_layer)

        # 2. Vẽ hiệu ứng Thời tiết ngoài cửa sổ theo Mùa (`rain`, `snow`, `sakura`, `fireflies`, ...)
        p_r, p_g, p_b, _ = theme_pack.particle_color_rgba
        ptype = theme_pack.particle_type
        for wp in weather_particles:
            span_h = wp["py2"] - wp["py1"]
            span_w = wp["px2"] - wp["px1"]
            if ptype == "rain":
                cur_y = wp["py1"] + ((wp["y0"] - wp["py1"] + prog * wp["speed"] * span_h) % span_h)
                cur_x = wp["x0"] - ((cur_y - wp["py1"]) * 0.08)
                y_end = min(wp["py2"], cur_y + wp["len"])
                x_end = cur_x - (y_end - cur_y) * 0.08
                if cur_y < wp["py2"] - 4:
                    fdr.line([(cur_x, cur_y), (x_end, y_end)], fill=(p_r, p_g, p_b, wp["alpha"]), width=2)
            elif ptype in {"snow", "snow_lights"}:
                cur_y = wp["py1"] + ((wp["y0"] - wp["py1"] + prog * wp["speed"] * span_h) % span_h)
                sway_x = math.sin(theta * 2.0 + wp["phase"]) * 12.0
                cur_x = wp["px1"] + ((wp["x0"] - wp["px1"] + sway_x) % span_w)
                r = wp["r"]
                fdr.ellipse([(cur_x - r, cur_y - r), (cur_x + r, cur_y + r)], fill=(p_r, p_g, p_b, wp["alpha"]))
            elif ptype == "sakura":
                cur_y = wp["py1"] + ((wp["y0"] - wp["py1"] + prog * wp["speed"] * span_h) % span_h)
                cur_x = wp["px1"] + ((wp["x0"] - wp["px1"] - prog * span_w * 0.6 + math.sin(theta + wp["phase"]) * 14.0) % span_w)
                r = wp["r"]
                fdr.ellipse([(cur_x - r - 2, cur_y - r), (cur_x + r + 2, cur_y + r)], fill=(p_r, p_g, p_b, wp["alpha"]))
            else:
                # Fireflies / Embers
                cur_x = wp["x0"] + math.sin(theta + wp["phase"]) * 14.0
                cur_y = wp["y0"] + math.cos(theta + wp["phase"]) * 14.0
                glow_a = int(wp["alpha"] * (0.45 + 0.55 * abs(math.sin(2.0 * theta + wp["phase"]))))
                r = wp["r"]
                fdr.ellipse([(cur_x - r, cur_y - r), (cur_x + r, cur_y + r)], fill=(p_r, p_g, p_b, glow_a))

        # 3. Hiệu ứng Đèn bàn ấm áp + Hạt bụi vàng + Khói cà phê (`flare`)
        if "flare" in vfx_cfg.enabled_effects:
            lr, lg, lb, l_base_a = theme_pack.lamp_glow_rgba
            lamp_alpha = int(l_base_a + 12 * math.sin(2.0 * theta))
            fdr.ellipse([(820, 275), (1155, 620)], fill=(lr, lg, lb, lamp_alpha))
            for dm in dust_motes:
                dx = dm["x"] + dm["amp_x"] * math.sin(theta + dm["phase"])
                dy = dm["y"] + dm["amp_y"] * math.cos(theta + dm["phase"])
                r = dm["r"]
                a = int(dm["alpha"] * (0.6 + 0.4 * math.sin(2.0 * theta + dm["phase"])))
                fdr.ellipse([(dx - r, dy - r), (dx + r, dy + r)], fill=(255, 232, 170, a))

            # Khói cà phê bốc lên từ cốc gốm (x=1364, y=445..590)
            for s_k in range(4):
                s_prog = (prog * 2.0 + s_k / 4.0) % 1.0
                sy = 588.0 - s_prog * 145.0
                sx = 1364.0 + math.sin(2.0 * theta + s_k * 1.8 + s_prog * 4.2) * (12.0 * s_prog + 3.0)
                sr_x = int(11 + 15 * s_prog)
                sr_y = int(15 + 19 * s_prog)
                s_alpha = int(60 * math.sin(math.pi * s_prog))
                if s_alpha > 2:
                    fdr.ellipse([(sx - sr_x, sy - sr_y), (sx + sr_x, sy + sr_y)], fill=(248, 242, 235, s_alpha))

        # 4. Vẽ ký hiệu cảm xúc của Linh Vật (`Zzz` khi Học / `♪ ☕ ✨` khi Nghỉ)
        anim_ctrl.draw_mood_particles(fx_draw=fdr, t_sec=t_sec, is_break=is_break)

        comp_rgb = np.array(Image.alpha_composite(frame_im, fx_layer).convert("RGB"), dtype=np.uint8)

        # 5. Áp dụng hiệu ứng Retro VHS Glitch / Scanline / Chromatic Aberration định kỳ mỗi vài giây
        comp_rgb = apply_frame_vfx_numpy(
            rgb_np=comp_rgb,
            t_sec=t_sec,
            loop_sec=loop_sec,
            vfx_cfg=vfx_cfg,
            frame_idx=f_i,
        )

        Image.fromarray(comp_rgb).save(str(frames_dir / f"f_{f_i:04d}.jpg"), quality=93)

    subprocess.run(
        [
            ffmpeg_exe,
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(frames_dir / "f_%04d}.jpg").replace("}", ""),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "17",
            "-pix_fmt",
            "yuv420p",
            str(cached_loop),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    shutil.rmtree(frames_dir, ignore_errors=True)
    return cached_loop


def ensure_cozy_dog_cinemagraph_loop(
    work_dir: Path,
    fps: int = 24,
    loop_sec: float = 10.0,
    ffmpeg_exe: str = "ffmpeg",
) -> Path:
    """Hàm tương thích ngược (Backward-Compatible) tạo Cinemagraph Loop mặc định."""
    mascot = load_mascot_pack("golden_retriever")
    theme = load_theme_pack("autumn_cozy")
    vfx = VFXConfig.from_cli_string("all")
    return ensure_mascot_cinemagraph_loop(
        work_dir=work_dir,
        mascot_pack=mascot,
        theme_pack=theme,
        vfx_cfg=vfx,
        is_break=False,
        fps=fps,
        loop_sec=loop_sec,
        ffmpeg_exe=ffmpeg_exe,
    )
