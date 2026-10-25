"""
Stikbro 2D/2.5D Hybrid Cartoon Shorts Generator (100% Automated Python Pipeline — $0.00 API Cost).

Generates a complete 18-second, 24 FPS vertical (1080x1920) Stikbro-style couple comedy Short:
  - Scene 1 (0.0s - 3.0s): "The Secret Cake Heist" (Husband stuffs his face with Wife's Red Velvet Cake)
  - Scene 2 (3.0s - 6.0s): "Wife's Spicy Trap" (Wife serves extra-chili spaghetti with an evil grin)
  - Scene 3 (6.0s - 9.5s): "SPICY OVERLOAD SCREAM!" (Husband screams breathing steam clouds, camera shake)
  - Scene 4 (9.5s - 12.2s): "Food Coma on the Sofa" (Husband snores on the yellow living room sofa)
  - Scene 5 (12.2s - 15.2s): "The Marker Revenge" (Wife sneaks behind sofa with a permanent marker)
  - Scene 6 (15.2s - 18.2s): "Instant Karma Wake-Up!" (Husband wakes up with marker mustache & glasses)

Includes:
  - 2x Super-Sampled Anti-Aliased (2160x3840 -> 1080x1920) Stikbro character rigging
  - Cozy 2.5D illustrated backgrounds + Gaussian volumetric ceiling lamp lighting
  - Procedural Cartoon SFX Synthesizer (chewing, steam whistle, snoring, marker squeaks, rimshot)
  - Optional Edge-TTS expressive short cartoon vocal reactions + quirky pizzicato BGM mix
  - Direct delivery to Telegram Group via `send_telegram_video_broadcast`
"""
from __future__ import annotations

import asyncio
import math
import os
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

W, H = 1080, 1920
SS = 2  # 2x Super-Sampling Anti-Aliasing
SW, SH = W * SS, H * SS
FPS = 24


def _hex(h: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def _draw_thick_polyline(draw: ImageDraw.ImageDraw, pts: List[Tuple[int, int]], color, width: int):
    if len(pts) < 2:
        return
    r = width // 2
    for i in range(len(pts) - 1):
        draw.line([pts[i], pts[i + 1]], fill=color, width=width)
    for x, y in pts:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color)


def _draw_oval_tilted(
    canvas: Image.Image,
    cx: int,
    cy: int,
    rx: int,
    ry: int,
    angle_deg: float,
    fill,
    outline=None,
    width: int = 10,
    cel_shade: bool = False,
):
    size = int(max(rx, ry) * 2.5)
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer, "RGBA")
    mc = size // 2
    bbox = [mc - rx, mc - ry, mc + rx, mc + ry]
    ld.ellipse(bbox, fill=fill)

    if cel_shade:
        shade_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shade_layer, "RGBA")
        sd.ellipse(bbox, fill=_hex("#D2DCE6"))
        sd.ellipse(
            [
                mc - rx + int(rx * 0.24),
                mc - ry - int(ry * 0.12),
                mc + rx + int(rx * 0.24),
                mc + ry - int(ry * 0.12),
            ],
            fill=(0, 0, 0, 0),
        )
        layer.paste(shade_layer, (0, 0), mask=shade_layer.split()[3])

    if outline and width > 0:
        ld.ellipse(bbox, outline=outline, width=width)

    if abs(angle_deg) > 0.5:
        layer = layer.rotate(angle_deg, resample=Image.Resampling.BICUBIC, expand=False)
    canvas.alpha_composite(layer, (cx - mc, cy - mc))


_BG_CACHE: Dict[str, Image.Image] = {}


def get_cozy_room_background(scene_type: str = "dining") -> Image.Image:
    """Cached 2.5D warm illustrated cozy background (1080x1920) scaled to SW x SH."""
    if scene_type in _BG_CACHE:
        return _BG_CACHE[scene_type].copy()

    y_grid, x_grid = np.mgrid[0:H, 0:W].astype(np.float32)

    if scene_type == "dining":
        wall_top = np.array([172, 108, 66], dtype=np.float32)
        wall_bot = np.array([216, 150, 96], dtype=np.float32)
        t_wall = np.clip(y_grid / (H * 0.66), 0, 1)[..., None]
        arr = wall_top * (1 - t_wall) + wall_bot * t_wall
        stripe = ((np.sin(x_grid * 2 * math.pi / 96.0) > 0).astype(np.float32))[..., None]
        arr = arr + stripe * np.array([9, 6, 3], dtype=np.float32)
    else:
        wall_top = np.array([194, 138, 96], dtype=np.float32)
        wall_bot = np.array([232, 186, 142], dtype=np.float32)
        t_wall = np.clip(y_grid / (H * 0.66), 0, 1)[..., None]
        arr = wall_top * (1 - t_wall) + wall_bot * t_wall
        stripe = ((np.sin(x_grid * 2 * math.pi / 110.0) > 0).astype(np.float32))[..., None]
        arr = arr + stripe * np.array([8, 5, 3], dtype=np.float32)

    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")

    # Ceiling beams
    draw.rectangle([0, 0, W, 135], fill=_hex("#6B3D22"))
    for bx in range(0, W + 180, 180):
        draw.polygon([(bx, 0), (bx + 90, 0), (bx + 60, 140), (bx - 30, 140)], fill=_hex("#552E18"))
    draw.rectangle([0, 130, W, 152], fill=_hex("#462411"))

    # Wainscoting & wooden floor
    floor_y = 1280
    draw.rectangle([0, 940, W, floor_y], fill=_hex("#9E5E36"))
    draw.rectangle([0, 930, W, 952], fill=_hex("#7A4322"))
    for px in range(35, W - 35, 160):
        draw.rounded_rectangle(
            [px, 975, px + 128, floor_y - 32],
            radius=8,
            fill=_hex("#8D512C"),
            outline=_hex("#6E3B1C"),
            width=4,
        )
    draw.rectangle([0, floor_y - 26, W, floor_y], fill=_hex("#5E3219"))

    floor_overlay = Image.new("RGBA", (W, H - floor_y), _hex("#8B4F2B"))
    f_draw = ImageDraw.Draw(floor_overlay, "RGBA")
    for fy in range(0, H - floor_y, 76):
        shade = max(0, min(42, fy // 14))
        f_draw.rectangle([0, fy, W, fy + 74], fill=(142 - shade, 82 - shade // 2, 45 - shade // 3, 255))
        f_draw.line([(0, fy), (W, fy)], fill=_hex("#5A3018", 180), width=3)
    for px in range(-220, W + 440, 220):
        f_draw.line([(W // 2 + int((px - W // 2) * 0.42), 0), (px, H - floor_y)], fill=_hex("#5A3018", 130), width=3)
    img.paste(floor_overlay, (0, floor_y))

    bg_decor = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d_draw = ImageDraw.Draw(bg_decor, "RGBA")

    def draw_framed_art(x1, y1, x2, y2, motif="hills"):
        d_draw.rounded_rectangle([x1 + 10, y1 + 14, x2 + 10, y2 + 14], radius=10, fill=(30, 15, 5, 75))
        d_draw.rounded_rectangle([x1, y1, x2, y2], radius=10, fill=_hex("#8B4C27"), outline=_hex("#4E2710"), width=5)
        pad = 18
        d_draw.rectangle([x1 + pad, y1 + pad, x2 - pad, y2 - pad], fill=_hex("#F4E6CE"), outline=_hex("#C9B08A"), width=3)
        ix1, iy1, ix2, iy2 = x1 + pad + 12, y1 + pad + 12, x2 - pad - 12, y2 - pad - 12
        if motif == "hills":
            d_draw.rectangle([ix1, iy1, ix2, iy2], fill=_hex("#F2C17D"))
            d_draw.ellipse([ix2 - 60, iy1 + 16, ix2 - 18, iy1 + 58], fill=_hex("#FFE89E"))
            d_draw.polygon([(ix1, iy2), (ix1 + 48, iy1 + 50), (ix2, iy2)], fill=_hex("#7BA06B"))
            d_draw.polygon([(ix1 + 35, iy2), (ix2 - 30, iy1 + 65), (ix2, iy2)], fill=_hex("#587D4B"))
        else:
            d_draw.rectangle([ix1, iy1, ix2, iy2], fill=_hex("#DFB888"))
            cx, cy = (ix1 + ix2) // 2, (iy1 + iy2) // 2
            d_draw.rectangle([cx - 20, cy + 10, cx + 20, iy2 - 8], fill=_hex("#B86B43"))
            d_draw.ellipse([cx - 38, cy - 42, cx + 38, cy + 22], fill=_hex("#689B5E"))

    def draw_potted_plant(px, py, scale_p=1.0):
        pw, ph = int(68 * scale_p), int(82 * scale_p)
        d_draw.polygon([(px - pw, py), (px + pw, py), (px + int(pw * 0.78), py + ph), (px - int(pw * 0.78), py + ph)], fill=_hex("#C8643B"), outline=_hex("#5E2B16"), width=5)
        d_draw.rounded_rectangle([px - int(pw * 1.08), py - 14, px + int(pw * 1.08), py + 12], radius=6, fill=_hex("#D97248"), outline=_hex("#5E2B16"), width=5)
        for lx, ly, lrx, lry, ang in [(-55, -65, 45, 24, -35), (55, -65, 45, 24, 35), (-30, -110, 48, 26, -15), (35, -105, 48, 26, 18), (0, -135, 38, 52, 0)]:
            _draw_oval_tilted(bg_decor, px + int(lx * scale_p), py + int(ly * scale_p), int(lrx * scale_p), int(lry * scale_p), ang, fill=_hex("#53944B"), outline=_hex("#2B5427"), width=5)

    if scene_type == "dining":
        draw_framed_art(85, 310, 285, 560, "hills")
        draw_framed_art(415, 355, 605, 585, "plant")
        wx1, wy1, wx2, wy2 = 820, 280, 1080, 880
        d_draw.rectangle([wx1 - 12, wy1 + 12, wx2, wy2 + 12], fill=(30, 15, 5, 70))
        d_draw.rectangle([wx1, wy1, wx2, wy2], fill=_hex("#4A6D8C"), outline=_hex("#6E3D20"), width=12)
        d_draw.rectangle([wx1 + 14, wy1 + 14, wx2 - 14, wy2 - 14], fill=_hex("#5C82A6"))
        d_draw.line([(wx1, (wy1 + wy2) // 2), (wx2, (wy1 + wy2) // 2)], fill=_hex("#6E3D20"), width=10)
        d_draw.polygon([(wx1 - 35, wy1 - 20), (wx1 + 55, wy1 - 20), (wx1 + 30, wy2 + 30), (wx1 - 45, wy2 + 30)], fill=_hex("#C85A3B"), outline=_hex("#8E3820"))
    else:
        d_draw.rectangle([40, 260, 320, floor_y], fill=_hex("#F5D69B"), outline=_hex("#6E3D20"), width=14)
        d_draw.polygon([(305, 270), (415, 315), (415, floor_y - 10), (305, floor_y)], fill=_hex("#8B4E28"), outline=_hex("#542C12"))
        draw_framed_art(485, 290, 675, 535, "hills")
        draw_framed_art(735, 335, 920, 570, "plant")
        draw_potted_plant(160, 860, scale_p=1.15)
        d_draw.polygon([(835, 640), (965, 640), (1000, 750), (800, 750)], fill=_hex("#FFE39F"), outline=_hex("#B88940"), width=4)
        d_draw.rectangle([886, 750, 914, 835], fill=_hex("#7A4928"))

    bg_decor = bg_decor.filter(ImageFilter.GaussianBlur(radius=2.0))
    img = Image.alpha_composite(img, bg_decor)

    lamp_draw = ImageDraw.Draw(img, "RGBA")
    lamp_cx = W // 2
    lamp_draw.line([(lamp_cx, 0), (lamp_cx, 155)], fill=_hex("#2B1B12"), width=9)
    lamp_draw.chord([lamp_cx - 155, 125, lamp_cx + 155, 295], start=180, end=360, fill=_hex("#5C3722"), outline=_hex("#2E1A0F"), width=6)
    lamp_draw.ellipse([lamp_cx - 150, 192, lamp_cx + 150, 232], fill=_hex("#FFF3B8"), outline=_hex("#D19B4B"), width=4)

    upscaled = img.resize((SW, SH), Image.Resampling.BILINEAR)
    _BG_CACHE[scene_type] = upscaled
    return upscaled.copy()


_LIGHT_MASKS: Optional[Tuple[np.ndarray, np.ndarray]] = None


def apply_volumetric_lamp_glow(img: Image.Image, intensity: float = 1.05) -> Image.Image:
    """Fast precomputed volumetric ceiling lamp light cone & cozy corner vignette."""
    global _LIGHT_MASKS
    if _LIGHT_MASKS is None:
        lamp_cx, lamp_y = 540.0, 215.0
        y_grid, x_grid = np.mgrid[0:H, 0:W].astype(np.float32)
        dy = np.maximum(1.0, y_grid - lamp_y)
        cone_half_w = 145.0 + dy * 0.48
        dx = np.abs(x_grid - lamp_cx)
        in_cone = np.clip(1.0 - (dx / cone_half_w) ** 2.2, 0.0, 1.0)
        vert_mask = np.clip((y_grid - (lamp_y - 20)) / 60.0, 0.0, 1.0) * np.clip(1.0 - (y_grid - lamp_y) / 1680.0, 0.18, 1.0)
        cone_alpha = in_cone * vert_mask * 0.35

        dist_bulb = np.sqrt((x_grid - lamp_cx) ** 2 + ((y_grid - lamp_y) * 1.4) ** 2)
        bulb_halo = np.clip(1.0 - dist_bulb / 400.0, 0.0, 1.0) ** 2 * 0.44
        base_light = np.clip(cone_alpha + bulb_halo, 0.0, 0.65)[..., None]

        nx = (x_grid - W * 0.5) / (W * 0.5)
        ny = (y_grid - H * 0.52) / (H * 0.52)
        vig = np.clip(1.0 - 0.20 * (nx ** 2 + ny ** 2), 0.76, 1.0)[..., None]
        _LIGHT_MASKS = (base_light, vig)

    base_light, vig = _LIGHT_MASKS
    arr = np.array(img.convert("RGB"), dtype=np.float32)
    warm_color = np.array([255.0, 236.0, 168.0], dtype=np.float32)
    arr = (arr + (warm_color - arr * 0.35) * (base_light * intensity)) * vig
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def draw_stikbro_character(
    canvas: Image.Image,
    char_type: str,
    cx: int,
    cy: int,
    expression: str = "happy",
    pose: str = "sitting_table",
    facing: int = 1,
    t: float = 0.0,
    scale: float = 1.52,
    doodled: bool = False,
    draw_legs: bool = True,
    holding_fork: bool = False,
):
    """Draws a 2x super-sampled Stikbro character with authentic Stikbro hair, face & limb rigging."""
    s = SS * scale
    X = int(cx * SS)
    Y = int(cy * SS)
    OUTLINE = _hex("#121214")
    WHITE = _hex("#FFFFFF")
    SHADE = _hex("#D2DCE6")
    lw = max(5, int(7.5 * s))
    limb_w = max(8, int(10.5 * s))

    draw = ImageDraw.Draw(canvas, "RGBA")

    bob = int(math.sin(t * 6.0) * 3.5 * s)
    if expression in ("screaming_spicy", "angry_tantrum"):
        bob = int(math.sin(t * 34.0) * 6.0 * s)

    # ── 1. LEGS ──
    if draw_legs:
        hip_y = Y + int(82 * s) + bob
        hip_l = (X - int(25 * s * facing), hip_y)
        hip_r = (X + int(25 * s * facing), hip_y)

        if pose == "sitting_table":
            knee_l = (hip_l[0] + int(42 * s * facing), hip_l[1] + int(52 * s))
            foot_l = (knee_l[0] + int(6 * s * facing), knee_l[1] + int(118 * s))
            knee_r = (hip_r[0] + int(52 * s * facing), hip_r[1] + int(48 * s))
            foot_r = (knee_r[0] + int(10 * s * facing), knee_r[1] + int(118 * s))
        elif pose == "sitting_sofa":
            swing = int(math.sin(t * 4.5) * 8 * s)
            knee_l = (hip_l[0] + int(48 * s * facing), hip_l[1] + int(32 * s))
            foot_l = (knee_l[0] + int(28 * s * facing) + swing, knee_l[1] + int(110 * s))
            knee_r = (hip_r[0] + int(58 * s * facing), hip_r[1] + int(40 * s))
            foot_r = (knee_r[0] + int(34 * s * facing) - swing, knee_r[1] + int(110 * s))
        elif pose == "reclining_sofa":
            knee_l = (hip_l[0] + int(30 * s * facing), hip_l[1] + int(55 * s))
            foot_l = (knee_l[0] + int(45 * s * facing), knee_l[1] + int(95 * s))
            knee_r = (hip_r[0] + int(55 * s * facing), hip_r[1] + int(45 * s))
            foot_r = (knee_r[0] + int(55 * s * facing), knee_r[1] + int(95 * s))
        else:
            walk = int(math.sin(t * 8.0) * 18 * s)
            knee_l = (hip_l[0] - walk // 2, hip_l[1] + int(90 * s))
            foot_l = (hip_l[0] - walk, hip_l[1] + int(175 * s))
            knee_r = (hip_r[0] + walk // 2, hip_r[1] + int(90 * s))
            foot_r = (hip_r[0] + walk, hip_r[1] + int(175 * s))

        for hip, knee, foot in [(hip_l, knee_l, foot_l), (hip_r, knee_r, foot_r)]:
            _draw_thick_polyline(draw, [hip, knee, foot], OUTLINE, limb_w)
            fx_end = foot[0] + int(26 * s * facing)
            fy_end = foot[1] - int(5 * s)
            _draw_thick_polyline(draw, [foot, (fx_end, fy_end)], OUTLINE, int(limb_w * 1.15))

    # ── 2. TORSO ──
    torso_y = Y + bob
    torso_tilt = -14 * facing if pose == "reclining_sofa" else -4 * facing
    if char_type == "male":
        _draw_oval_tilted(canvas, X, torso_y, int(56 * s), int(94 * s), angle_deg=torso_tilt, fill=WHITE, outline=OUTLINE, width=lw, cel_shade=True)
    else:
        top_w = int(38 * s)
        bot_w = int(72 * s)
        th = int(96 * s)
        dress_pts = [
            (X - top_w, torso_y - th + int(14 * s)),
            (X + top_w, torso_y - th + int(14 * s)),
            (X + bot_w, torso_y + th),
            (X - bot_w, torso_y + th),
        ]
        draw.polygon(dress_pts, fill=WHITE)
        shade_pts = [
            dress_pts[0],
            (X - top_w + int(24 * s), dress_pts[0][1]),
            (X - bot_w + int(34 * s), dress_pts[3][1]),
            dress_pts[3],
        ]
        draw.polygon(shade_pts, fill=SHADE)
        _draw_thick_polyline(draw, dress_pts + [dress_pts[0]], OUTLINE, lw)

    # ── 3. HEAD & HAIR (Clean Chin & Authentic Side-Locks for Female) ──
    head_cx = X + int((18 if pose == "reclining_sofa" else 6) * s * facing)
    head_cy = torso_y - int(176 * s)
    head_rx = int(112 * s)
    head_ry = int(104 * s)

    if char_type == "female":
        # Back hair silhouette: drapes down behind the left & right sides to shoulder level,
        # while keeping the bottom center under the chin clean so the neck & white jawline stand out!
        hw = head_rx + int(22 * s)
        htop = head_cy - head_ry - int(14 * s)
        hbot = head_cy + head_ry + int(18 * s)
        # Left hair lock behind head
        draw.rounded_rectangle([head_cx - hw, htop + int(20 * s), head_cx - int(25 * s), hbot], radius=int(38 * s), fill=OUTLINE)
        # Right hair lock behind head
        draw.rounded_rectangle([head_cx + int(25 * s), htop + int(20 * s), head_cx + hw, hbot], radius=int(38 * s), fill=OUTLINE)
        # Top hair crown behind head
        draw.ellipse([head_cx - hw + int(4 * s), htop, head_cx + hw - int(4 * s), head_cy + int(55 * s)], fill=OUTLINE)

    # Main round white head with 2D cel-shading and crisp black outline
    _draw_oval_tilted(canvas, head_cx, head_cy, head_rx, head_ry, angle_deg=0, fill=WHITE, outline=OUTLINE, width=lw, cel_shade=True)

    if char_type == "male":
        for hx_off, h_ang in [(-16, -20), (0, -4), (16, 15)]:
            bx = head_cx + int(hx_off * s)
            by = head_cy - head_ry + int(4 * s)
            tx = bx + int(math.sin(math.radians(h_ang)) * 34 * s)
            ty = by - int(32 * s)
            _draw_thick_polyline(draw, [(bx, by), (tx, ty)], OUTLINE, max(5, int(6 * s)))
    else:
        # Forehead hair bangs ONLY on the top corners of the forehead (above eye_y - 35*s)
        part_x = head_cx + int(16 * s * facing)
        top_y = head_cy - head_ry - int(6 * s)
        # Left top bang curve
        draw.polygon(
            [
                (part_x, top_y),
                (head_cx - int(75 * s), top_y + int(4 * s)),
                (head_cx - head_rx - int(6 * s), head_cy - int(25 * s)),
                (head_cx - head_rx + int(10 * s), head_cy - int(20 * s)),
                (head_cx - int(62 * s), head_cy - int(68 * s)),
                (part_x, head_cy - head_ry + int(16 * s)),
            ],
            fill=OUTLINE,
        )
        # Right top bang curve
        draw.polygon(
            [
                (part_x, top_y),
                (head_cx + int(75 * s), top_y + int(4 * s)),
                (head_cx + head_rx + int(6 * s), head_cy - int(25 * s)),
                (head_cx + head_rx - int(10 * s), head_cy - int(20 * s)),
                (head_cx + int(62 * s), head_cy - int(68 * s)),
                (part_x, head_cy - head_ry + int(16 * s)),
            ],
            fill=OUTLINE,
        )

    # ── 4. FACIAL EXPRESSIONS ──
    eye_y = head_cy - int(14 * s)
    eye_sep = int(36 * s)
    ex_l = head_cx - eye_sep + int(10 * s * facing)
    ex_r = head_cx + eye_sep + int(10 * s * facing)

    def draw_cartoon_eyes(rx_e=29, ry_e=36, pupil_dx=7, pupil_dy=0, pupil_r=10, blink_now=False, brow_type="normal"):
        if blink_now:
            for ex in (ex_l, ex_r):
                draw.arc([ex - int(rx_e * s), eye_y - int(10 * s), ex + int(rx_e * s), eye_y + int(16 * s)], start=15, end=165, fill=OUTLINE, width=max(4, int(6.5 * s)))
        else:
            for ex in (ex_l, ex_r):
                erx, ery = int(rx_e * s), int(ry_e * s)
                draw.ellipse([ex - erx, eye_y - ery, ex + erx, eye_y + ery], fill=WHITE, outline=OUTLINE, width=max(4, int(6 * s)))
                px = ex + int(pupil_dx * s * facing)
                py = eye_y + int(pupil_dy * s)
                pr = int(pupil_r * s)
                draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=OUTLINE)
                hr = max(2, int(pr * 0.35))
                draw.ellipse([px - pr // 2 - hr, py - pr // 2 - hr, px - pr // 2 + hr, py - pr // 2 + hr], fill=WHITE)

        by = eye_y - int((ry_e + 14) * s)
        bw = max(4, int(7 * s))
        if brow_type == "normal":
            _draw_thick_polyline(draw, [(ex_l - int(22 * s), by + int(5 * s)), (ex_l, by - int(4 * s)), (ex_l + int(20 * s), by + int(4 * s))], OUTLINE, bw)
            _draw_thick_polyline(draw, [(ex_r - int(20 * s), by + int(4 * s)), (ex_r, by - int(4 * s)), (ex_r + int(22 * s), by + int(5 * s))], OUTLINE, bw)
        elif brow_type == "shocked":
            _draw_thick_polyline(draw, [(ex_l - int(22 * s), by - int(4 * s)), (ex_l, by - int(18 * s)), (ex_l + int(20 * s), by - int(6 * s))], OUTLINE, bw)
            _draw_thick_polyline(draw, [(ex_r - int(20 * s), by - int(6 * s)), (ex_r, by - int(18 * s)), (ex_r + int(22 * s), by - int(4 * s))], OUTLINE, bw)
        elif brow_type == "angry":
            _draw_thick_polyline(draw, [(ex_l - int(24 * s), by - int(8 * s)), (ex_l + int(22 * s), by + int(14 * s))], OUTLINE, int(bw * 1.15))
            _draw_thick_polyline(draw, [(ex_r - int(22 * s), by + int(14 * s)), (ex_r + int(24 * s), by - int(8 * s))], OUTLINE, int(bw * 1.15))

    is_blinking = (int(t * 10) % 28 == 0)

    if expression == "screaming_spicy":
        ey = eye_y - int(10 * s)
        bw = max(4, int(7 * s))
        _draw_thick_polyline(draw, [(ex_l - int(24 * s), ey - int(15 * s)), (ex_l + int(10 * s), ey), (ex_l - int(24 * s), ey + int(15 * s))], OUTLINE, bw)
        _draw_thick_polyline(draw, [(ex_r + int(24 * s), ey - int(15 * s)), (ex_r - int(10 * s), ey), (ex_r + int(24 * s), ey + int(15 * s))], OUTLINE, bw)
        _draw_thick_polyline(draw, [(ex_l - int(18 * s), ey - int(32 * s)), (ex_l + int(14 * s), ey - int(18 * s))], OUTLINE, int(bw * 0.85))
        _draw_thick_polyline(draw, [(ex_r + int(18 * s), ey - int(32 * s)), (ex_r - int(14 * s), ey - int(18 * s))], OUTLINE, int(bw * 0.85))

        mx = head_cx + int(18 * s * facing)
        my = head_cy + int(34 * s)
        mrx, mry = int(66 * s), int(56 * s)
        draw.rounded_rectangle([mx - mrx, my - mry, mx + mrx, my + mry], radius=int(30 * s), fill=_hex("#5B141B"), outline=OUTLINE, width=lw)
        draw.chord([mx - mrx + int(4 * s), my - mry, mx + mrx - int(4 * s), my - mry + int(40 * s)], start=180, end=360, fill=WHITE, outline=OUTLINE, width=max(3, int(5 * s)))
        tg_wobble = int(math.sin(t * 38.0) * 7 * s)
        draw.chord([mx - mrx + int(12 * s) + tg_wobble, my + int(4 * s), mx + mrx - int(8 * s) + tg_wobble, my + mry + int(14 * s)], start=180, end=360, fill=_hex("#F07482"), outline=OUTLINE, width=max(3, int(5 * s)))

        # Animated puffing white steam cloud shooting out of mouth
        steam_dist = int((25 + (t * 85) % 55) * s)
        sx = mx + int((mrx + 38 * s) * facing) + int(steam_dist * 0.42 * facing)
        sy = my - int(12 * s) - int(steam_dist * 0.18)
        for dx_s, dy_s, r_s in [(0, 0, 34), (24, -15, 28), (26, 15, 26), (50, -4, 32)]:
            cx_s = sx + int(dx_s * s * facing)
            cy_s = sy + int(dy_s * s)
            rs = int(r_s * s)
            draw.ellipse([cx_s - rs, cy_s - rs, cx_s + rs, cy_s + rs], fill=_hex("#F7F9FC", 230))

    elif expression == "chewing_chubby":
        draw_cartoon_eyes(rx_e=27, ry_e=33, pupil_dx=8, pupil_dy=4, pupil_r=10, blink_now=is_blinking, brow_type="normal")
        chew = int(math.sin(t * 16.0) * 7 * s)
        for ch_x in (head_cx - int(62 * s), head_cx + int(68 * s)):
            ch_y = head_cy + int(30 * s) + chew
            crx, cry = int(44 * s), int(38 * s)
            draw.ellipse([ch_x - crx, ch_y - cry, ch_x + crx, ch_y + cry], fill=WHITE, outline=OUTLINE, width=max(4, int(6 * s)))
            inner_x = ch_x + (int(18 * s) if ch_x < head_cx else -int(18 * s))
            draw.ellipse([inner_x - int(30 * s), ch_y - int(30 * s), inner_x + int(30 * s), ch_y + int(30 * s)], fill=WHITE)
            draw.ellipse([ch_x - int(16 * s), ch_y - int(5 * s), ch_x + int(16 * s), ch_y + int(15 * s)], fill=_hex("#FF9E9E", 165))
            for cr_dx, cr_dy in [(-11, -12), (9, 12), (14, -7)]:
                draw.ellipse([ch_x + int(cr_dx * s) - 5, ch_y + int(cr_dy * s) - 5, ch_x + int(cr_dx * s) + 5, ch_y + int(cr_dy * s) + 5], fill=_hex("#C92834"))
        my = head_cy + int(34 * s) + chew
        draw.arc([head_cx - int(20 * s), my - int(12 * s), head_cx + int(20 * s), my + int(12 * s)], start=10, end=170, fill=OUTLINE, width=max(4, int(6 * s)))

    elif expression == "shocked":
        draw_cartoon_eyes(rx_e=30, ry_e=38, pupil_dx=8, pupil_dy=2, pupil_r=8, blink_now=False, brow_type="shocked")
        mx = head_cx + int(6 * s * facing)
        my = head_cy + int(40 * s)
        mrx, mry = int(32 * s), int(25 * s)
        draw.ellipse([mx - mrx, my - mry, mx + mrx, my + mry], fill=_hex("#5B141B"), outline=OUTLINE, width=max(4, int(6 * s)))
        draw.chord([mx - int(20 * s), my + int(2 * s), mx + int(20 * s), my + mry], start=180, end=360, fill=_hex("#F07482"))

    elif expression in ("angry_tantrum", "evil_marker"):
        draw_cartoon_eyes(rx_e=28, ry_e=34, pupil_dx=6, pupil_dy=4, pupil_r=9, blink_now=False, brow_type="angry")
        mx = head_cx + int(6 * s * facing)
        my = head_cy + int(42 * s)
        if expression == "evil_marker":
            mrx, mry = int(26 * s), int(20 * s)
            draw.ellipse([mx - mrx, my - mry, mx + mrx, my + mry], fill=_hex("#5B141B"), outline=OUTLINE, width=max(4, int(6 * s)))
            draw.chord([mx - int(16 * s), my + int(2 * s), mx + int(16 * s), my + mry], start=180, end=360, fill=_hex("#F07482"))
        else:
            mrx, mry = int(40 * s), int(28 * s)
            draw.rounded_rectangle([mx - mrx, my - mry, mx + mrx, my + mry], radius=int(12 * s), fill=_hex("#5B141B"), outline=OUTLINE, width=max(4, int(6 * s)))
            draw.rectangle([mx - mrx + int(8 * s), my - mry + int(4 * s), mx + mrx - int(8 * s), my - mry + int(15 * s)], fill=WHITE)

    elif expression == "sleeping":
        for ex in (ex_l, ex_r):
            erx, ery = int(27 * s), int(30 * s)
            draw.ellipse([ex - erx, eye_y - ery, ex + erx, eye_y + ery], fill=WHITE, outline=OUTLINE, width=max(4, int(5.5 * s)))
            draw.line([(ex - erx, eye_y + int(3 * s)), (ex + erx, eye_y + int(3 * s))], fill=OUTLINE, width=max(4, int(6 * s)))
        mx = head_cx + int(8 * s * facing)
        my = head_cy + int(42 * s)
        mr = int((11 + 4.0 * math.sin(t * 5.0)) * s)
        draw.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=_hex("#5B141B"), outline=OUTLINE, width=max(4, int(5.5 * s)))
        # Expanding/shrinking light-blue sleep bubble!
        b_rad = int((18 + 10 * math.sin(t * 5.0)) * s)
        bx_s = mx - int(22 * s)
        by_s = my - int(8 * s)
        draw.ellipse([bx_s - b_rad, by_s - b_rad, bx_s + b_rad, by_s + b_rad], fill=_hex("#B8E6FF", 150), outline=_hex("#4FA3D1", 210), width=4)

    else:
        draw_cartoon_eyes(rx_e=28, ry_e=35, pupil_dx=6, pupil_dy=0, pupil_r=10, blink_now=is_blinking, brow_type="normal")
        mx = head_cx + int(8 * s * facing)
        my = head_cy + int(30 * s)
        draw.chord([mx - int(44 * s), my - int(26 * s), mx + int(44 * s), my + int(36 * s)], start=0, end=180, fill=_hex("#5B141B"), outline=OUTLINE, width=max(4, int(6 * s)))
        draw.chord([mx - int(28 * s), my + int(6 * s), mx + int(28 * s), my + int(34 * s)], start=0, end=180, fill=_hex("#F07482"))

    # Marker Doodle Prank on Husband's Face!
    if doodled:
        mw = max(5, int(6 * s))
        for ex in (ex_l, ex_r):
            draw.ellipse([ex - int(35 * s), eye_y - int(41 * s), ex + int(35 * s), eye_y + int(41 * s)], outline=_hex("#1A1A1A"), width=mw)
        draw.line([(ex_l + int(35 * s), eye_y), (ex_r - int(35 * s), eye_y)], fill=_hex("#1A1A1A"), width=mw)
        # Curly French handlebar mustache above mouth
        my_m = head_cy + int(14 * s)
        _draw_thick_polyline(draw, [(head_cx, my_m), (head_cx - int(34 * s), my_m + int(8 * s)), (head_cx - int(58 * s), my_m - int(10 * s)), (head_cx - int(44 * s), my_m - int(22 * s))], _hex("#1A1A1A"), mw)
        _draw_thick_polyline(draw, [(head_cx, my_m), (head_cx + int(34 * s), my_m + int(8 * s)), (head_cx + int(58 * s), my_m - int(10 * s)), (head_cx + int(44 * s), my_m - int(22 * s))], _hex("#1A1A1A"), mw)
        # Swirly spiral on cheek
        draw.arc([head_cx - int(82 * s), head_cy + int(6 * s), head_cx - int(44 * s), head_cy + int(44 * s)], start=0, end=300, fill=_hex("#1A1A1A"), width=mw)

    # ── 5. ARMS & PROPS ──
    sh_y = torso_y - int(52 * s)
    sh_l = (X - int(40 * s * facing), sh_y)
    sh_r = (X + int(40 * s * facing), sh_y)

    if expression == "screaming_spicy":
        shake = int(math.sin(t * 42.0) * 9 * s)
        elb_l = (sh_l[0] - int(52 * s * facing), sh_l[1] + int(42 * s) + shake)
        hand_l = (elb_l[0] + int(32 * s * facing), elb_l[1] + int(32 * s) - shake)
        elb_r = (sh_r[0] + int(52 * s * facing), sh_r[1] + int(42 * s) - shake)
        hand_r = (elb_r[0] - int(12 * s * facing), elb_r[1] + int(32 * s) + shake)
    elif expression == "chewing_chubby":
        scoop = int(math.sin(t * 14.0) * 14 * s)
        elb_l = (sh_l[0] - int(32 * s * facing), sh_l[1] + int(60 * s))
        hand_l = (elb_l[0] + int(52 * s * facing), elb_l[1] + int(12 * s) - scoop)
        elb_r = (sh_r[0] + int(42 * s * facing), sh_r[1] + int(48 * s))
        hand_r = (elb_r[0] + int(32 * s * facing), elb_r[1] - int(18 * s) + scoop)
    elif expression == "shocked" and holding_fork:
        elb_l = (sh_l[0] - int(28 * s * facing), sh_l[1] + int(50 * s))
        hand_l = (elb_l[0] + int(60 * s * facing), elb_l[1] - int(32 * s))
        elb_r = (sh_r[0] + int(38 * s * facing), sh_r[1] + int(60 * s))
        hand_r = (elb_r[0] + int(28 * s * facing), elb_r[1] + int(22 * s))
    elif expression == "shocked":
        # Hands up in shock on sofa!
        elb_l = (sh_l[0] - int(48 * s * facing), sh_l[1] + int(28 * s))
        hand_l = (elb_l[0] - int(15 * s * facing), elb_l[1] - int(45 * s))
        elb_r = (sh_r[0] + int(48 * s * facing), sh_r[1] + int(28 * s))
        hand_r = (elb_r[0] + int(15 * s * facing), elb_r[1] - int(45 * s))
    elif expression == "sleeping":
        elb_l = (sh_l[0] - int(45 * s * facing), sh_l[1] + int(55 * s))
        hand_l = (elb_l[0] + int(18 * s * facing), elb_l[1] + int(55 * s))
        elb_r = (sh_r[0] + int(75 * s * facing), sh_r[1] - int(35 * s))
        hand_r = (head_cx + int(22 * s * facing), head_cy + int(8 * s))
    elif expression == "evil_marker":
        scribble = int(math.sin(t * 26.0) * 10 * s)
        elb_l = (sh_l[0] - int(30 * s * facing), sh_l[1] + int(42 * s))
        hand_l = (elb_l[0] + int(60 * s * facing) + scribble, elb_l[1] - int(26 * s) - scribble // 2)
        elb_r = (sh_r[0] + int(35 * s * facing), sh_r[1] + int(52 * s))
        hand_r = (elb_r[0] - int(22 * s * facing), elb_r[1] + int(15 * s))
    else:
        wave_y = int(math.sin(t * 14.0) * 10 * s)
        elb_l = (sh_l[0] - int(42 * s * facing), sh_l[1] + int(52 * s))
        hand_l = (elb_l[0] + int(22 * s * facing), elb_l[1] + int(42 * s) + wave_y)
        elb_r = (sh_r[0] + int(42 * s * facing), sh_r[1] + int(52 * s))
        hand_r = (elb_r[0] + int(22 * s * facing), elb_r[1] + int(42 * s) - wave_y)

    for sh, elb, hand in [(sh_l, elb_l, hand_l), (sh_r, elb_r, hand_r)]:
        _draw_thick_polyline(draw, [sh, elb, hand], OUTLINE, limb_w)
        hr = int(limb_w * 0.85)
        draw.ellipse([hand[0] - hr, hand[1] - hr, hand[0] + hr, hand[1] + hr], fill=OUTLINE)

    if holding_fork or expression == "chewing_chubby":
        fx, fy = hand_l
        _draw_thick_polyline(draw, [(fx, fy), (fx + int(16 * s * facing), fy - int(60 * s))], _hex("#B8C4D0"), max(4, int(5.5 * s)))
        _draw_thick_polyline(draw, [(fx + int(10 * s * facing), fy - int(60 * s)), (fx + int(22 * s * facing), fy - int(60 * s))], _hex("#B8C4D0"), max(4, int(5 * s)))
    elif expression == "evil_marker":
        mx_p, my_p = hand_l
        pen_pts = [
            (mx_p - int(12 * s), my_p - int(36 * s)),
            (mx_p + int(12 * s), my_p - int(30 * s)),
            (mx_p - int(8 * s), my_p + int(40 * s)),
            (mx_p - int(30 * s), my_p + int(34 * s)),
        ]
        draw.polygon(pen_pts, fill=_hex("#1C1C20"), outline=WHITE, width=3)
        draw.polygon([(mx_p - int(16 * s), my_p + int(36 * s)), (mx_p - int(10 * s), my_p + int(54 * s)), (mx_p - int(24 * s), my_p + int(50 * s))], fill=OUTLINE)


def _apply_camera_transform(img: Image.Image, zoom: float = 1.0, pan_x: float = 0.0, pan_y: float = 0.0, shake_px: float = 0.0, t: float = 0.0) -> Image.Image:
    """Applies smooth camera zoom, pan, and comedic screen shake while maintaining 1080x1920."""
    if zoom <= 1.002 and abs(pan_x) < 1 and abs(pan_y) < 1 and shake_px <= 0.1:
        return img
    cw = int(W / max(1.0, zoom))
    ch = int(H / max(1.0, zoom))
    sx = int(math.sin(t * 55.0) * shake_px) if shake_px > 0 else 0
    sy = int(math.cos(t * 62.0) * shake_px) if shake_px > 0 else 0
    cx = int(W // 2 + pan_x + sx)
    cy = int(H // 2 + pan_y + sy)
    x1 = max(0, min(W - cw, cx - cw // 2))
    y1 = max(0, min(H - ch, cy - ch // 2))
    cropped = img.crop((x1, y1, x1 + cw, y1 + ch))
    return cropped.resize((W, H), Image.Resampling.BILINEAR)


def _draw_watermark_and_vfx(img: Image.Image, scene_idx: int, t_rel: float):
    """Adds subtle comic VFX icons (Zzz, anger vein, exclamation) and the corner 'Stikbro' style watermark."""
    draw = ImageDraw.Draw(img, "RGBA")
    try:
        font_wm = ImageFont.truetype(str(PROJECT_ROOT / "assets" / "fonts" / "Montserrat-ExtraBold.ttf"), 40)
        font_vfx = ImageFont.truetype(str(PROJECT_ROOT / "assets" / "fonts" / "Montserrat-Black.ttf"), 68)
    except Exception:
        font_wm = ImageFont.load_default()
        font_vfx = ImageFont.load_default()

    # Floating "Zzz" in Scene 4 (Sleeping on Sofa)
    if scene_idx == 4:
        for i, (zx, zy) in enumerate([(220, 780), (170, 690), (120, 595)]):
            dy = int(((t_rel * 45 + i * 20) % 45))
            alpha = max(60, 240 - dy * 3)
            draw.text((zx + 3, zy - dy + 3), "Z", font=font_vfx, fill=(0, 0, 0, alpha))
            draw.text((zx, zy - dy), "Z", font=font_vfx, fill=(255, 255, 255, alpha))

    # Shock "!" or Anger Vein in Scene 1 & 2
    if scene_idx == 1 and t_rel > 1.2:
        draw.text((835, 495), "?!", font=font_vfx, fill=_hex("#FF3B30"))
    elif scene_idx == 2:
        # Spicy bottle icon / warning near pasta
        draw.text((485, 920), "🌶️🔥", font=font_wm, fill=_hex("#FF3B30"))

    # Bottom-right watermark matching screenshot
    wm_text = "Stikbro AI"
    draw.text((W - 248, H - 78), wm_text, font=font_wm, fill=(0, 0, 0, 140))
    draw.text((W - 250, H - 80), wm_text, font=font_wm, fill=(255, 255, 255, 210))


def render_story_frame(global_t: float) -> Image.Image:
    """
    Renders a single frame at `global_t` seconds (0.0 .. 18.2s) across the 6-scene Stikbro story.
    """
    OUTLINE = _hex("#1E120A")

    # Determine scene & local time
    if global_t < 3.0:
        scene_idx, t_rel = 1, global_t
    elif global_t < 6.0:
        scene_idx, t_rel = 2, global_t - 3.0
    elif global_t < 9.5:
        scene_idx, t_rel = 3, global_t - 6.0
    elif global_t < 12.2:
        scene_idx, t_rel = 4, global_t - 9.5
    elif global_t < 15.2:
        scene_idx, t_rel = 5, global_t - 12.2
    else:
        scene_idx, t_rel = 6, global_t - 15.2

    if scene_idx in (1, 2, 3):
        canvas = get_cozy_room_background("dining")
        draw = ImageDraw.Draw(canvas, "RGBA")

        # Soft floor shadow under dining table
        draw.ellipse([110 * SS, 1590 * SS, 970 * SS, 1730 * SS], fill=(30, 14, 5, 48))

        # Wooden Chair Backs
        for ch_cx in (200, 880):
            cx_s, cy_s = ch_cx * SS, 1080 * SS
            draw.rounded_rectangle([cx_s - 180, cy_s - 340, cx_s + 180, cy_s + 420], radius=22, fill=_hex("#8F4E27"), outline=OUTLINE, width=10)
            draw.rectangle([cx_s - 140, cy_s - 270, cx_s + 140, cy_s + 140], fill=_hex("#B86E3E"), outline=OUTLINE, width=8)
            draw.rectangle([cx_s - 160, cy_s + 380, cx_s - 115, 1690 * SS], fill=_hex("#6E3918"), outline=OUTLINE, width=8)
            draw.rectangle([cx_s + 115, cy_s + 380, cx_s + 160, 1690 * SS], fill=_hex("#6E3918"), outline=OUTLINE, width=8)

        # Characters at Dining Table
        if scene_idx == 1:
            # Scene 1: Husband stuffing his face with Red Velvet Cake, Wife shocked!
            draw_stikbro_character(canvas, "male", cx=235, cy=1045, expression="chewing_chubby", pose="sitting_table", facing=1, t=global_t, scale=1.54)
            draw_stikbro_character(canvas, "female", cx=845, cy=1075, expression="shocked", pose="sitting_table", facing=-1, t=global_t, scale=1.44, holding_fork=True)
        elif scene_idx == 2:
            # Scene 2: Wife angry/plotting slides extra-spicy chili spaghetti to Husband!
            draw_stikbro_character(canvas, "male", cx=235, cy=1045, expression="happy", pose="sitting_table", facing=1, t=global_t, scale=1.52, holding_fork=True)
            draw_stikbro_character(canvas, "female", cx=845, cy=1075, expression="angry_tantrum", pose="sitting_table", facing=-1, t=global_t, scale=1.45, holding_fork=True)
        else:
            # Scene 3: Husband screaming breathing steam from spicy spaghetti!
            draw_stikbro_character(canvas, "male", cx=220, cy=1045, expression="screaming_spicy", pose="sitting_table", facing=1, t=global_t, scale=1.54)
            draw_stikbro_character(canvas, "female", cx=855, cy=1075, expression="shocked", pose="sitting_table", facing=-1, t=global_t, scale=1.44, holding_fork=True)

        # Foreground Wooden Dining Table
        ty = 1210 * SS
        draw.rectangle([250 * SS, ty + 70, 310 * SS, 1715 * SS], fill=_hex("#7A411E"), outline=OUTLINE, width=10)
        draw.rectangle([770 * SS, ty + 70, 830 * SS, 1715 * SS], fill=_hex("#7A411E"), outline=OUTLINE, width=10)
        table_pts = [(130 * SS, ty - 60), (950 * SS, ty - 60), (1020 * SS, ty + 68), (60 * SS, ty + 68)]
        draw.polygon(table_pts, fill=_hex("#C87D4B"), outline=OUTLINE, width=10)
        draw.rectangle([60 * SS, ty + 62, 1020 * SS, ty + 142], fill=_hex("#9E5B30"), outline=OUTLINE, width=10)

        if scene_idx == 1:
            # 3-Layer Red Velvet Cake shrinking as Husband eats it!
            px, py = 540 * SS, ty + 8
            draw.ellipse([px - 210, py - 52, px + 210, py + 52], fill=_hex("#FFFFFF"), outline=OUTLINE, width=8)
            bite_shrink = int(min(1.0, t_rel / 2.5) * 90)
            ck_x1, ck_x2 = px - 120 + bite_shrink, px + 110
            ck_y2 = py + 12
            ck_y1 = ck_y2 - 210
            draw.rectangle([ck_x1, ck_y1, ck_x2, ck_y2], fill=_hex("#B81D24"), outline=OUTLINE, width=8)
            for ly in range(ck_y1 + 50, ck_y2, 62):
                draw.rectangle([ck_x1 + 6, ly, ck_x2 - 6, ly + 20], fill=_hex("#FFFDF9"))
            draw.polygon([(ck_x1, ck_y1), (ck_x1 + 45, ck_y1 - 38), (ck_x2, ck_y1)], fill=_hex("#FFFDF9"), outline=OUTLINE, width=7)
            draw.ellipse([ck_x2 - 65, ck_y1 - 52, ck_x2 - 15, ck_y1 - 8], fill=_hex("#E02424"), outline=OUTLINE, width=6)
        else:
            # Plates of Super Spicy Spaghetti!
            slide = int(max(0.0, 1.0 - t_rel * 1.2) * 180 * SS) if scene_idx == 2 else 0
            for idx_p, base_px in enumerate((445 * SS, 735 * SS)):
                px = base_px + (slide if idx_p == 0 else 0)
                py = ty + 6
                draw.ellipse([px - 165, py - 46, px + 165, py + 46], fill=_hex("#F2F5F8"), outline=OUTLINE, width=8)
                draw.ellipse([px - 125, py - 64, px + 125, py + 26], fill=_hex("#F5C35B"), outline=OUTLINE, width=6)
                sauce_col = _hex("#E61919") if idx_p == 0 else _hex("#D93829")
                draw.ellipse([px - 85, py - 56, px + 85, py + 12], fill=sauce_col)
                for gx in (-40, 10, 45):
                    draw.ellipse([px + gx - 18, py - 42, px + gx + 18, py - 20], fill=_hex("#4C9E45"))

        out = canvas.resize((W, H), Image.Resampling.LANCZOS)
        out = apply_volumetric_lamp_glow(out, intensity=1.12 if scene_idx == 3 else 1.05)

        # Camera dynamics per scene
        if scene_idx == 1:
            out = _apply_camera_transform(out, zoom=1.0 + 0.12 * (t_rel / 3.0), pan_x=-25 * (t_rel / 3.0), pan_y=-15 * (t_rel / 3.0), t=global_t)
        elif scene_idx == 2:
            out = _apply_camera_transform(out, zoom=1.08, pan_x=35 * math.cos(t_rel * 1.0), pan_y=-10, t=global_t)
        else:
            out = _apply_camera_transform(out, zoom=1.16, pan_x=-65, pan_y=-35, shake_px=9.0, t=global_t)

    else:
        # Living Room Yellow Sofa Scenes (Scenes 4, 5, 6)
        canvas = get_cozy_room_background("living")
        draw = ImageDraw.Draw(canvas, "RGBA")

        # Soft floor shadow under sofa
        draw.ellipse([60 * SS, 1510 * SS, 1020 * SS, 1660 * SS], fill=(30, 14, 5, 45))

        sy = 1220 * SS

        if scene_idx == 5:
            # Wife standing BEHIND the yellow sofa (cy=955 so dress extends behind sofa backrest at y=1010!)
            sneak_y = int(max(0.0, 1.0 - t_rel * 1.5) * 90)
            draw_stikbro_character(
                canvas, "female", cx=790, cy=955 + sneak_y, expression="evil_marker", pose="standing", facing=-1, t=global_t, scale=1.65, draw_legs=False
            )

        # Yellow Cozy Sofa Backrest & Seat Cushion
        draw.rounded_rectangle([50 * SS, sy - 420, 990 * SS, sy + 240], radius=60, fill=_hex("#F2B84B"), outline=OUTLINE, width=12)
        draw.line([(520 * SS, sy - 390), (520 * SS, sy + 180)], fill=_hex("#C98E28"), width=10)
        draw.rounded_rectangle([95 * SS, sy - 270, 315 * SS, sy + 20], radius=34, fill=_hex("#E07A5F"), outline=OUTLINE, width=8)
        draw.rounded_rectangle([30 * SS, sy + 20, 1010 * SS, sy + 330], radius=44, fill=_hex("#E5A93C"), outline=OUTLINE, width=12)

        if scene_idx in (4, 5):
            # Husband reclining & snoring on the yellow sofa
            draw_stikbro_character(
                canvas, "male", cx=365, cy=1295, expression="sleeping", pose="reclining_sofa", facing=-1, t=global_t, scale=1.72
            )
        else:
            # Scene 6: Husband wakes up in shock with marker doodles on his face, Wife laughs!
            draw_stikbro_character(
                canvas, "male", cx=305, cy=1135, expression="shocked", pose="sitting_sofa", facing=1, t=global_t, scale=1.55, doodled=True, holding_fork=False
            )
            draw_stikbro_character(
                canvas, "female", cx=775, cy=1145, expression="happy", pose="sitting_sofa", facing=-1, t=global_t, scale=1.48
            )

        out = canvas.resize((W, H), Image.Resampling.LANCZOS)
        out = apply_volumetric_lamp_glow(out, intensity=1.04)

        if scene_idx == 4:
            out = _apply_camera_transform(out, zoom=1.06 + 0.05 * (t_rel / 2.7), pan_x=-40, pan_y=35, t=global_t)
        elif scene_idx == 5:
            out = _apply_camera_transform(out, zoom=1.12, pan_x=25, pan_y=-15, t=global_t)
        else:
            shake = 7.0 if t_rel < 0.6 else 0.0
            out = _apply_camera_transform(out, zoom=1.08 + 0.10 * min(1.0, t_rel / 1.5), pan_x=-15, pan_y=10, shake_px=shake, t=global_t)

    _draw_watermark_and_vfx(out, scene_idx, t_rel)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Procedural Cartoon Audio + SFX + Quirky BGM Synthesizer
# ──────────────────────────────────────────────────────────────────────────────
def synthesize_stikbro_audio(out_wav_path: str, duration_s: float = 18.2, sr: int = 44100):
    """
    Synthesizes a complete stereo 44.1kHz soundtrack combining:
      1. Bouncy Pizzicato / Marimba Comedy BGM groove
      2. Synchronized Cartoon Sound Effects (chewing, gulp, slide whistle, steam kettle scream,
         snoring, marker cap pop, squeaky marker scribbling, boing & rimshot punchline)
    """
    n_samples = int(duration_s * sr)
    t = np.linspace(0, duration_s, n_samples, endpoint=False, dtype=np.float32)
    track = np.zeros(n_samples, dtype=np.float32)

    def add_tone(t_start: float, dur: float, f0: float, f1: Optional[float] = None, vol: float = 0.25, wave_type: str = "sine", vib_hz: float = 0.0, vib_depth: float = 0.0):
        i0 = max(0, int(t_start * sr))
        i1 = min(n_samples, int((t_start + dur) * sr))
        if i1 <= i0:
            return
        lt = np.linspace(0, dur, i1 - i0, endpoint=False, dtype=np.float32)
        if f1 is not None and f1 != f0:
            freq = f0 + (f1 - f0) * (lt / dur)
        else:
            freq = np.full_like(lt, f0)
        if vib_hz > 0:
            freq = freq * (1.0 + vib_depth * np.sin(2 * np.pi * vib_hz * lt))
        phase = 2 * np.pi * np.cumsum(freq) / sr
        if wave_type == "sine":
            sig = np.sin(phase)
        elif wave_type == "triangle":
            sig = 2.0 * np.abs(2.0 * ((phase / (2 * np.pi)) % 1.0) - 1.0) - 1.0
        elif wave_type == "saw":
            sig = 2.0 * ((phase / (2 * np.pi)) % 1.0) - 1.0
        else:
            sig = np.sign(np.sin(phase))
        # Pluck / ADSR envelope
        attack = np.minimum(1.0, lt / 0.012)
        decay = np.exp(-lt / max(0.04, dur * 0.45))
        track[i0:i1] += sig * attack * decay * vol

    def add_noise_burst(t_start: float, dur: float, vol: float = 0.2, mod_hz: float = 0.0):
        i0 = max(0, int(t_start * sr))
        i1 = min(n_samples, int((t_start + dur) * sr))
        if i1 <= i0:
            return
        lt = np.linspace(0, dur, i1 - i0, endpoint=False, dtype=np.float32)
        rng = np.random.default_rng(int(t_start * 1000) + 7)
        noise = rng.standard_normal(i1 - i0).astype(np.float32)
        # Simple moving-average bandpass feel
        noise = np.convolve(noise, np.ones(7, dtype=np.float32) / 7.0, mode="same")
        env = np.sin(np.pi * lt / dur) ** 0.7
        if mod_hz > 0:
            env = env * (0.55 + 0.45 * np.sin(2 * np.pi * mod_hz * lt))
        track[i0:i1] += noise * env * vol

    # 1. Quirky Sneaky Pizzicato Comedy BGM (A minor / C major bouncy bass & pluck)
    beat_dur = 0.24  # ~125 BPM eighth notes
    bass_pattern = [220.0, 261.63, 329.63, 261.63, 196.0, 246.94, 293.66, 246.94]
    melody_pattern = [440.0, 0, 523.25, 493.88, 440.0, 0, 392.0, 440.0, 523.25, 0, 659.25, 587.33, 523.25, 493.88, 440.0, 0]
    step = 0
    cur_t = 0.0
    while cur_t < duration_s - 0.3:
        b_freq = bass_pattern[step % len(bass_pattern)]
        add_tone(cur_t, 0.16, b_freq * 0.5, vol=0.11, wave_type="triangle")
        m_freq = melody_pattern[step % len(melody_pattern)]
        if m_freq > 0:
            add_tone(cur_t, 0.13, m_freq, vol=0.09, wave_type="sine")
            add_tone(cur_t, 0.08, m_freq * 2.0, vol=0.04, wave_type="triangle")
        cur_t += beat_dur
        step += 1

    # 2. Scene 1 (0.0s - 3.0s): "Nom-nom-nom" Cake Chewing + Gulp + Wife Shock "! Ding!"
    for chomp_t in [0.18, 0.52, 0.86, 1.20, 1.54, 1.88]:
        add_noise_burst(chomp_t, 0.14, vol=0.28, mod_hz=18.0)
        add_tone(chomp_t, 0.12, 180.0, 310.0, vol=0.22, wave_type="triangle")
    # Big cartoon Gulp!
    add_tone(2.25, 0.28, 520.0, 140.0, vol=0.32, wave_type="sine")
    # Wife Shocked "! Ding!"
    add_tone(2.55, 0.35, 880.0, 1320.0, vol=0.28, wave_type="triangle")

    # 3. Scene 2 (3.0s - 6.0s): Evil Plotting Chord + Plate Slide Whoosh + Slurp!
    add_tone(3.05, 0.45, 220.0, 207.65, vol=0.24, wave_type="saw")
    add_noise_burst(3.55, 0.40, vol=0.26, mod_hz=8.0)  # plate slide whoosh
    add_tone(3.55, 0.40, 300.0, 680.0, vol=0.20, wave_type="sine")
    # Noodle Slurp!
    add_tone(4.85, 0.45, 260.0, 920.0, vol=0.30, wave_type="sine", vib_hz=22.0, vib_depth=0.08)
    add_noise_burst(5.35, 0.18, vol=0.25)

    # 4. Scene 3 (6.0s - 9.5s): SPICY OVERLOAD! Rising Kettle Whistle + Steam Roar + Vibrato Scream!
    add_tone(6.00, 0.65, 550.0, 1850.0, vol=0.34, wave_type="sine")
    # Roaring steam cloud hiss + high cartoon vibrato scream
    add_noise_burst(6.45, 2.75, vol=0.36, mod_hz=24.0)
    add_tone(6.50, 2.60, 740.0, 620.0, vol=0.26, wave_type="saw", vib_hz=16.0, vib_depth=0.12)
    add_tone(6.50, 2.60, 1480.0, 1240.0, vol=0.16, wave_type="triangle", vib_hz=16.0, vib_depth=0.12)

    # 5. Scene 4 (9.5s - 12.2s): Sleepy Sofa Snore ("Hoooonk... mimimimimi!")
    for snore_t in [9.75, 11.05]:
        add_tone(snore_t, 0.48, 130.0, 165.0, vol=0.26, wave_type="saw", vib_hz=28.0, vib_depth=0.15)
        add_noise_burst(snore_t, 0.45, vol=0.16, mod_hz=28.0)
        for mi_i in range(4):
            add_tone(snore_t + 0.52 + mi_i * 0.13, 0.10, 680.0 + mi_i * 45.0, 740.0 + mi_i * 45.0, vol=0.20, wave_type="sine")

    # 6. Scene 5 (12.2s - 15.2s): Marker Cap POP! + Squeaky Felt-Tip Marker Scribbling!
    add_tone(12.45, 0.12, 320.0, 1150.0, vol=0.35, wave_type="sine")  # Cap POP!
    for sq_i, sq_t in enumerate(np.arange(12.80, 14.95, 0.19)):
        f_start = 1350.0 if sq_i % 2 == 0 else 1720.0
        f_end = 1820.0 if sq_i % 2 == 0 else 1280.0
        add_tone(float(sq_t), 0.14, f_start, f_end, vol=0.22, wave_type="sine", vib_hz=35.0, vib_depth=0.06)

    # 7. Scene 6 (15.2s - 18.2s): Wake-up BOING! + Shock Sting + Wife Laugh + Rimshot (Ba-dum-tss!)
    add_tone(15.25, 0.45, 190.0, 640.0, vol=0.36, wave_type="triangle", vib_hz=18.0, vib_depth=0.22)  # Spring BOING!
    # Wife Ha-ha-ha-ha cartoon laugh notes
    for l_i, l_freq in enumerate([659.25, 587.33, 523.25, 493.88, 523.25, 659.25]):
        add_tone(15.85 + l_i * 0.18, 0.14, l_freq, l_freq * 0.92, vol=0.25, wave_type="triangle")
    # Classic Ba-Dum-Tss! Rimshot at 17.15s
    add_tone(17.15, 0.14, 140.0, 95.0, vol=0.38, wave_type="sine")       # Ba
    add_tone(17.32, 0.18, 185.0, 110.0, vol=0.40, wave_type="sine")      # Dum
    add_noise_burst(17.52, 0.55, vol=0.34, mod_hz=0.0)                   # Tsssss!
    add_tone(17.52, 0.50, 523.25, 523.25, vol=0.22, wave_type="triangle")

    # Normalize & write 16-bit stereo WAV
    peak = float(np.max(np.abs(track))) or 1.0
    track = np.clip(track / peak * 0.88, -0.98, 0.98)
    pcm16 = (track * 32767.0).astype(np.int16)
    stereo = np.column_stack([pcm16, pcm16]).ravel()

    with wave.open(out_wav_path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(stereo.tobytes())


async def generate_vocal_reactions_and_mix(base_wav_path: str, final_wav_path: str, temp_dir: Path):
    """
    Generates short, punchy comedic vocal reactions via Edge-TTS and mixes them with our cartoon SFX/BGM wav
    plus project SFX (`whoosh_soft.wav`, `impact_sub.wav`) via FFmpeg!
    """
    import edge_tts

    clips_spec = [
        (0.15, "en-US-RogerNeural", "+18%", "+25Hz", "Yum yum yum! So good!"),
        (2.15, "en-US-AvaMultilingualNeural", "+15%", "+20Hz", "Hey! My cake?!"),
        (3.15, "en-US-AvaMultilingualNeural", "+10%", "+10Hz", "Hehehe... try this spicy pasta!"),
        (6.20, "en-US-RogerNeural", "+25%", "+35Hz", "AAAAH! TOO SPICY! HOT HOT HOT!"),
        (12.35, "en-US-AvaMultilingualNeural", "+12%", "+18Hz", "Payback time!"),
        (15.35, "en-US-RogerNeural", "+20%", "+30Hz", "WHAT?! MY FACE!"),
        (16.55, "en-US-AvaMultilingualNeural", "+18%", "+25Hz", "Hahaha! Gotcha!"),
    ]

    generated_vocals: List[Tuple[int, str]] = []
    for idx, (t_sec, voice, rate, pitch, text) in enumerate(clips_spec):
        out_mp3 = temp_dir / f"stikbro_voc_{idx}.mp3"
        try:
            comm = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
            await comm.save(str(out_mp3))
            if out_mp3.exists() and out_mp3.stat().st_size > 500:
                generated_vocals.append((int(t_sec * 1000), str(out_mp3)))
        except Exception as e:
            print(f"[Vocal warn] {e}")

    if not generated_vocals:
        # Fallback: use synthesized cartoon SFX/BGM directly
        import shutil
        shutil.copyfile(base_wav_path, final_wav_path)
        return

    cmd = ["ffmpeg", "-y", "-i", base_wav_path]
    for _, mp3_p in generated_vocals:
        cmd.extend(["-i", mp3_p])

    filter_parts = ["[0:a]volume=0.82[bgm]"]
    mix_inputs = ["[bgm]"]
    for i, (delay_ms, _) in enumerate(generated_vocals, start=1):
        tag = f"[v{i}]"
        filter_parts.append(f"[{i}:a]adelay={delay_ms}|{delay_ms},volume=1.35{tag}")
        mix_inputs.append(tag)

    amix_str = "".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=0,alimiter=limit=0.95[outa]"
    filter_parts.append(amix_str)

    cmd.extend([
        "-filter_complex", ";".join(filter_parts),
        "-map", "[outa]",
        "-c:a", "pcm_s16le",
        final_wav_path,
    ])
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if not Path(final_wav_path).exists():
        import shutil
        shutil.copyfile(base_wav_path, final_wav_path)


def build_stikbro_demo_video(send_telegram: bool = True) -> str:
    t0 = time.time()
    out_dir = PROJECT_ROOT / "storage" / "outputs"
    temp_dir = PROJECT_ROOT / "storage" / "temp" / "stikbro_demo"
    out_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    duration_s = 18.2
    total_frames = int(duration_s * FPS)

    # 1. Save a 2x2 Storyboard Grid first so we can inspect/verify visual perfection
    s1 = render_story_frame(1.2)
    s3 = render_story_frame(7.2)
    s5 = render_story_frame(13.8)
    s6 = render_story_frame(16.6)
    grid = Image.new("RGB", (W * 2, H * 2))
    grid.paste(s1, (0, 0))
    grid.paste(s3, (W, 0))
    grid.paste(s5, (0, H))
    grid.paste(s6, (W, H))
    grid_path = PROJECT_ROOT / "scratch" / "stikbro_final_storyboard.jpg"
    grid.resize((1080, 1920), Image.Resampling.LANCZOS).save(str(grid_path), "JPEG", quality=92)
    print(f"✅ Saved storyboard preview -> {grid_path}")

    # 2. Synthesize Cartoon SFX + BGM + Expressive Vocal Reactions
    base_wav = str(temp_dir / "stikbro_sfx_bgm.wav")
    final_wav = str(temp_dir / "stikbro_master_audio.wav")
    synthesize_stikbro_audio(base_wav, duration_s=duration_s)
    asyncio.run(generate_vocal_reactions_and_mix(base_wav, final_wav, temp_dir))
    print(f"✅ Synthesized master cartoon soundtrack -> {final_wav}")

    # 3. Stream all 24 FPS frames directly into FFmpeg pipe -> 1080x1920 MP4
    out_mp4 = str(out_dir / "stikbro_couple_comedy_demo.mp4")
    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{W}x{H}",
        "-pix_fmt", "rgb24",
        "-r", str(FPS),
        "-i", "-",
        "-i", final_wav,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "160k",
        "-shortest",
        "-movflags", "+faststart",
        out_mp4,
    ]
    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert proc.stdin is not None
    for f_idx in range(total_frames):
        cur_t = f_idx / float(FPS)
        frame_img = render_story_frame(cur_t)
        proc.stdin.write(frame_img.tobytes())
    proc.stdin.close()
    proc.wait(timeout=120)

    elapsed = time.time() - t0
    size_mb = Path(out_mp4).stat().st_size / (1024 * 1024)
    print(f"🎬 Rendered Stikbro MP4 ({total_frames} frames @ {FPS}fps, {size_mb:.2f} MB) in {elapsed:.1f}s -> {out_mp4}")

    if send_telegram:
        from app.services.intelligence.scheduler_and_broadcast import send_telegram_video_broadcast
        caption = (
            "🎬 <b>[DEMO THỬ NGHIỆM] Video Hoạt Hình 2D Phong Cách STIKBRO (100% Code Python Tự Động)</b>\n\n"
            "📌 <b>Kịch bản (6 cảnh - 18s):</b> <i>Never Eat Your Wife's Last Slice of Cake! 🍰🌶️🖊️</i>\n"
            "• <b>Cảnh 1:</b> Chồng ăn vụng hết chiếc bánh Red Velvet của vợ\n"
            "• <b>Cảnh 2:</b> Vợ nổi giận lén đổi đĩa mì siêu cay 🌶️\n"
            "• <b>Cảnh 3:</b> Chồng hét to phun khói trắng vì quá cay\n"
            "• <b>Cảnh 4:</b> Chồng ngủ say ngáy khò khò trên ghế sofa vàng\n"
            "• <b>Cảnh 5:</b> Vợ cầm bút dạ đen lẻn ra sau ghế sofa trả thù\n"
            "• <b>Cảnh 6:</b> Chồng tỉnh dậy với bộ râu & mắt kính vẽ đầy mặt!\n\n"
            "💰 <b>Chi phí API tạo ảnh/video:</b> <b>$0.00 (Miễn phí 100%)</b>\n"
            "⚡ <b>Tốc độ dựng (1080x1920 @ 24fps):</b> ~" + f"{elapsed:.0f} giây trên CPU"
        )
        ok = send_telegram_video_broadcast(out_mp4, caption=caption, prefer_groups=True)
        print(f"📨 Telegram Group Broadcast status: {ok}")

    return out_mp4


if __name__ == "__main__":
    build_stikbro_demo_video(send_telegram=True)
