"""
Stikbro 2D/2.5D Hybrid Cartoon Shorts Generator — Episode 2:
"The 3 AM Bed Hog & Midnight Clay Mask Revenge!" (Recreating the 5.5M-view Bedroom, 336K Floor Tantrum & Couple Face-Mask scenes).

6-Scene Storyboard (18.6s @ 24 FPS, 1080x1920):
  - Scene 1 (0.0s - 3.2s): Cozy Blue Bedroom (5.5M-view setup) — Husband & Wife sleeping on the yellow bed with rainbow mattress
  - Scene 2 (3.2s - 6.2s): The 3 AM Kung-Fu Sleep Kick — Husband sprawls & kicks Wife right off the edge of the bed!
  - Scene 3 (6.2s - 9.2s): Floor Tantrum (336K-view setup) — Wife lands sitting flat on the wooden floor, fuming in rage!
  - Scene 4 (9.2s - 12.4s): Midnight Skincare Ghost — Wife puts on a spooky grey clay beauty mask & sneaks up to the bed
  - Scene 5 (12.4s - 15.4s): Monster Wake-Up Scream — Husband wakes up nose-to-nose with the clay mask & screams in terror!
  - Scene 6 (15.4s - 18.6s): Forced Couple Spa Day (Thumbnail 5 setup) — Both sitting on the sofa in grey clay face masks!
"""
from __future__ import annotations

import asyncio
import math
import os
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.generate_stikbro_demo import (
    W,
    H,
    SS,
    SW,
    SH,
    FPS,
    _hex,
    _draw_thick_polyline,
    _draw_oval_tilted,
    apply_volumetric_lamp_glow,
    _apply_camera_transform,
    get_cozy_room_background,
)

_EP2_BG_CACHE: Dict[str, Image.Image] = {}


def get_ep2_background(scene_type: str = "bedroom") -> Image.Image:
    """
    Renders Episode 2 backgrounds:
      - 'bedroom': Recreates the 5.5M-view Stikbro Blue Bedroom (blue walls, framed mountain art,
                   bookshelf with colorful books, red window curtain, warm nightstand lamp, wooden floor).
      - 'hallway_floor': Recreates the 336K-view Hallway/Bedroom floor corner with cobweb & doorway.
      - 'living': Uses the cozy living room from Ep 1.
    """
    if scene_type == "living":
        return get_cozy_room_background("living")
    if scene_type in _EP2_BG_CACHE:
        return _EP2_BG_CACHE[scene_type].copy()

    y_grid, x_grid = np.mgrid[0:H, 0:W].astype(np.float32)

    if scene_type == "bedroom":
        # Cozy slate-blue / teal bedroom wall (matching Thumbnail 4 - 5.5M views!)
        wall_top = np.array([78, 128, 158], dtype=np.float32)
        wall_bot = np.array([108, 162, 192], dtype=np.float32)
        t_wall = np.clip(y_grid / (H * 0.68), 0, 1)[..., None]
        arr = wall_top * (1 - t_wall) + wall_bot * t_wall
        stripe = ((np.sin(x_grid * 2 * math.pi / 92.0) > 0).astype(np.float32))[..., None]
        arr = arr + stripe * np.array([6, 9, 11], dtype=np.float32)
    else:
        # Warm peach/ochre hallway corner (matching Thumbnail 2 - 336K views!)
        wall_top = np.array([188, 128, 86], dtype=np.float32)
        wall_bot = np.array([226, 172, 125], dtype=np.float32)
        t_wall = np.clip(y_grid / (H * 0.68), 0, 1)[..., None]
        arr = wall_top * (1 - t_wall) + wall_bot * t_wall
        stripe = ((np.sin(x_grid * 2 * math.pi / 100.0) > 0).astype(np.float32))[..., None]
        arr = arr + stripe * np.array([8, 6, 4], dtype=np.float32)

    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")

    # Ceiling trim
    draw.rectangle([0, 0, W, 110], fill=_hex("#5A3822"))
    draw.rectangle([0, 105, W, 126], fill=_hex("#3E2414"))

    floor_y = 1260
    # Wooden baseboard
    draw.rectangle([0, floor_y - 34, W, floor_y], fill=_hex("#6E3D20"))

    # Warm wooden floor planks
    floor_overlay = Image.new("RGBA", (W, H - floor_y), _hex("#9C5B32"))
    f_draw = ImageDraw.Draw(floor_overlay, "RGBA")
    for fy in range(0, H - floor_y, 74):
        shade = max(0, min(40, fy // 14))
        f_draw.rectangle([0, fy, W, fy + 72], fill=(156 - shade, 91 - shade // 2, 50 - shade // 3, 255))
        f_draw.line([(0, fy), (W, fy)], fill=_hex("#5E3218", 180), width=3)
    for px in range(-220, W + 440, 210):
        f_draw.line([(W // 2 + int((px - W // 2) * 0.42), 0), (px, H - floor_y)], fill=_hex("#5E3218", 130), width=3)
    img.paste(floor_overlay, (0, floor_y))

    bg_decor = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d_draw = ImageDraw.Draw(bg_decor, "RGBA")

    if scene_type == "bedroom":
        # 1. Framed Mountain Sunset Picture on Upper-Left Wall (exact match to Thumbnail 4!)
        x1, y1, x2, y2 = 65, 250, 325, 490
        d_draw.rounded_rectangle([x1 + 10, y1 + 12, x2 + 10, y2 + 12], radius=10, fill=(15, 25, 35, 80))
        d_draw.rounded_rectangle([x1, y1, x2, y2], radius=10, fill=_hex("#9E5B30"), outline=_hex("#4E2710"), width=5)
        d_draw.rectangle([x1 + 18, y1 + 18, x2 - 18, y2 - 18], fill=_hex("#F7EBD4"))
        ix1, iy1, ix2, iy2 = x1 + 32, y1 + 32, x2 - 32, y2 - 32
        d_draw.rectangle([ix1, iy1, ix2, iy2], fill=_hex("#F5B971"))
        d_draw.polygon([(ix1, iy2), (ix1 + 65, iy1 + 45), (ix2 - 20, iy2)], fill=_hex("#D47A55"))
        d_draw.polygon([(ix1 + 50, iy2), (ix2 - 45, iy1 + 60), (ix2, iy2)], fill=_hex("#8C533E"))

        # 2. Wooden Wall Bookshelf with Colorful Books (Upper-Center/Right — exact match to Thumbnail 4!)
        sx1, sy1, sx2, sy2 = 460, 340, 820, 372
        d_draw.rounded_rectangle([sx1, sy1, sx2, sy2], radius=6, fill=_hex("#A86032"), outline=_hex("#542C12"), width=4)
        # Colorful tilted & upright books on shelf
        books = [
            (485, 240, 34, 100, "#E04F4F"),
            (522, 225, 38, 115, "#F2B84B"),
            (563, 250, 32, 90, "#4FA3D1"),
            (635, 235, 36, 105, "#5C9E57"),
            (674, 220, 40, 120, "#D96B43"),
            (718, 245, 34, 95, "#8E6BB8"),
        ]
        for bx, by, bw, bh, bcol in books:
            d_draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=4, fill=_hex(bcol), outline=_hex("#2E1A0F"), width=3)

        # 3. Bedroom Window on Far Right with Red/Coral Curtain (exact match to Thumbnail 4!)
        wx1, wy1, wx2, wy2 = 910, 210, 1080, 960
        d_draw.rectangle([wx1, wy1, wx2, wy2], fill=_hex("#2C4766"), outline=_hex("#5E3419"), width=10)
        # Moonlit night sky & stars
        d_draw.ellipse([965, 280, 1025, 340], fill=_hex("#FFF3B8", 210))
        # Red/Coral draped curtain on left side of window
        d_draw.polygon(
            [(845, 185), (945, 185), (925, 985), (825, 985)],
            fill=_hex("#D95B5B"),
            outline=_hex("#7A2828"),
        )
        # Curtain vertical folds
        for cx_f in (865, 895):
            d_draw.line([(cx_f, 190), (cx_f - 15, 980)], fill=_hex("#A83838"), width=4)

        # 4. Nightstand & Glowing Bedside Lamp on Right (behind/next to bed)
        d_draw.rounded_rectangle([740, 770, 920, 1080], radius=10, fill=_hex("#8F4E27"), outline=_hex("#4A2510"), width=5)
        # Bedside lamp base & warm cone shade
        d_draw.rectangle([808, 690, 842, 775], fill=_hex("#D9B26F"), outline=_hex("#5E3B18"), width=4)
        d_draw.polygon([(755, 695), (895, 695), (865, 575), (785, 575)], fill=_hex("#FFE89E"), outline=_hex("#B8863B"), width=5)

    else:
        # Hallway / Bedroom Floor Corner (Thumbnail 2 - 336K views: Cobweb at top, open doorway on right, framed art)
        # Cobweb in top-center ceiling corner!
        cx_w, cy_w = 420, 125
        for ang in range(15, 175, 25):
            rad = math.radians(ang)
            d_draw.line([(cx_w, cy_w), (cx_w + int(math.cos(rad) * 210), cy_w + int(math.sin(rad) * 210))], fill=(255, 255, 255, 95), width=2)
        for r_w in (65, 120, 175):
            d_draw.arc([cx_w - r_w, cy_w - r_w, cx_w + r_w, cy_w + r_w], start=15, end=165, fill=(255, 255, 255, 85), width=2)

        # Framed art on wall
        d_draw.rounded_rectangle([180, 320, 370, 560], radius=10, fill=_hex("#8B4C27"), outline=_hex("#4E2710"), width=5)
        d_draw.rectangle([198, 338, 352, 542], fill=_hex("#F4E6CE"))
        d_draw.ellipse([235, 390, 315, 480], fill=_hex("#D97A54"))

        # Bright open doorway on right leading to hallway with vase & cabinet
        d_draw.rectangle([620, 210, 1020, floor_y], fill=_hex("#F5DEC2"), outline=_hex("#7A4928"), width=14)
        # Cabinet inside hallway
        d_draw.rounded_rectangle([760, 860, 990, floor_y - 10], radius=8, fill=_hex("#B86E3E"), outline=_hex("#5E3219"), width=5)
        # Flower vase on cabinet
        d_draw.ellipse([845, 770, 915, 865], fill=_hex("#7BA0B8"), outline=_hex("#3B586E"), width=4)
        for fx_o, fy_o in [(-25, -35), (0, -50), (25, -35)]:
            d_draw.ellipse([880 + fx_o - 22, 760 + fy_o - 22, 880 + fx_o + 22, 760 + fy_o + 22], fill=_hex("#5C9E57"))

    bg_decor = bg_decor.filter(ImageFilter.GaussianBlur(radius=1.8))
    img = Image.alpha_composite(img, bg_decor)

    # Top Center Glowing Pendant Lamp
    lamp_draw = ImageDraw.Draw(img, "RGBA")
    lamp_cx = W // 2
    lamp_draw.line([(lamp_cx, 0), (lamp_cx, 130)], fill=_hex("#2B1B12"), width=9)
    lamp_draw.chord([lamp_cx - 150, 100, lamp_cx + 150, 260], start=180, end=360, fill=_hex("#5C3722"), outline=_hex("#2E1A0F"), width=6)
    lamp_draw.ellipse([lamp_cx - 145, 165, lamp_cx + 145, 202], fill=_hex("#FFF3B8"), outline=_hex("#D19B4B"), width=4)

    upscaled = img.resize((SW, SH), Image.Resampling.BILINEAR)
    _EP2_BG_CACHE[scene_type] = upscaled
    return upscaled.copy()


def draw_cozy_striped_bed(canvas: Image.Image):
    """
    Draws the iconic 5.5M-view Stikbro Bedroom Bed onto `canvas` (SW x SH):
      - Curved Golden-Yellow Wooden Headboard (`#E5A135`)
      - Perspective Mattress with white top sheet & colorful rainbow side stripes (pink, green, blue, yellow)
      - Two fluffy white pillows with soft cel shading
      - Wooden bed frame base & legs
    """
    draw = ImageDraw.Draw(canvas, "RGBA")
    OUTLINE = _hex("#1E120A")

    # Soft floor shadow under the bed
    draw.polygon(
        [(30 * SS, 1420 * SS), (920 * SS, 1280 * SS), (1060 * SS, 1690 * SS), (40 * SS, 1790 * SS)],
        fill=(20, 10, 4, 55),
    )

    # 1. Curved Golden-Yellow Headboard (upper-left to center-right)
    hb_pts = [
        (45 * SS, 820 * SS),
        (160 * SS, 650 * SS),
        (380 * SS, 575 * SS),
        (615 * SS, 600 * SS),
        (695 * SS, 745 * SS),
        (695 * SS, 1020 * SS),
        (45 * SS, 1140 * SS),
    ]
    draw.polygon(hb_pts, fill=_hex("#E8A234"), outline=OUTLINE, width=11)
    # Inner headboard panel highlight
    hb_inner = [
        (85 * SS, 845 * SS),
        (185 * SS, 695 * SS),
        (380 * SS, 625 * SS),
        (585 * SS, 645 * SS),
        (655 * SS, 765 * SS),
        (655 * SS, 985 * SS),
        (85 * SS, 1095 * SS),
    ]
    draw.polygon(hb_inner, fill=_hex("#F4B94B"), outline=_hex("#B87618"), width=6)

    # 2. Two Fluffy White Pillows resting against the headboard
    _draw_oval_tilted(canvas, 250 * SS, 920 * SS, 145 * SS, 72 * SS, angle_deg=12, fill=_hex("#FFFFFF"), outline=OUTLINE, width=10, cel_shade=True)
    _draw_oval_tilted(canvas, 535 * SS, 865 * SS, 140 * SS, 68 * SS, angle_deg=10, fill=_hex("#FFFFFF"), outline=OUTLINE, width=10, cel_shade=True)

    # 3. Mattress Top Surface (3D perspective diamond/quad)
    # Top-left (60, 980) -> Top-right (690, 880) -> Front-right (1035, 1345) -> Front-left (315, 1510)
    m_tl = (55 * SS, 980 * SS)
    m_tr = (690 * SS, 875 * SS)
    m_fr = (1035 * SS, 1335 * SS)
    m_fl = (315 * SS, 1505 * SS)
    draw.polygon([m_tl, m_tr, m_fr, m_fl], fill=_hex("#F4F7FA"), outline=OUTLINE, width=10)

    # Subtle sheet fold line across mattress
    draw.line([(165 * SS, 1185 * SS), (835 * SS, 1070 * SS)], fill=_hex("#CFD8E3"), width=8)

    # 4. Rainbow / Pastel Striped Mattress Sides (Left side: m_tl -> m_fl, Front side: m_fl -> m_fr)
    stripe_h = 135 * SS
    stripe_colors = ["#F26B7A", "#68C4AF", "#F7D056", "#5DA9E9"]
    n_st = len(stripe_colors)
    for i, col in enumerate(stripe_colors):
        y0_off = int(i * stripe_h / n_st)
        y1_off = int((i + 1) * stripe_h / n_st)
        # Left side stripe
        draw.polygon(
            [
                (m_tl[0], m_tl[1] + y0_off),
                (m_fl[0], m_fl[1] + y0_off),
                (m_fl[0], m_fl[1] + y1_off),
                (m_tl[0], m_tl[1] + y1_off),
            ],
            fill=_hex(col),
        )
        # Front side stripe
        draw.polygon(
            [
                (m_fl[0], m_fl[1] + y0_off),
                (m_fr[0], m_fr[1] + y0_off),
                (m_fr[0], m_fr[1] + y1_off),
                (m_fl[0], m_fl[1] + y1_off),
            ],
            fill=_hex(col),
        )

    # Mattress side outlines
    _draw_thick_polyline(draw, [m_tl, m_fl, m_fr], OUTLINE, 10)
    _draw_thick_polyline(draw, [(m_tl[0], m_tl[1] + stripe_h), (m_fl[0], m_fl[1] + stripe_h), (m_fr[0], m_fr[1] + stripe_h)], OUTLINE, 10)
    draw.line([m_fl, (m_fl[0], m_fl[1] + stripe_h)], fill=OUTLINE, width=10)

    # 5. Golden-Yellow Wooden Bed Frame Base (below the rainbow mattress)
    base_h = 95 * SS
    b_tl = (m_tl[0], m_tl[1] + stripe_h)
    b_fl = (m_fl[0], m_fl[1] + stripe_h)
    b_fr = (m_fr[0], m_fr[1] + stripe_h)
    draw.polygon(
        [b_tl, b_fl, (b_fl[0], b_fl[1] + base_h), (b_tl[0], b_tl[1] + base_h)],
        fill=_hex("#D99226"),
        outline=OUTLINE,
        width=10,
    )
    draw.polygon(
        [b_fl, b_fr, (b_fr[0], b_fr[1] + base_h), (b_fl[0], b_fl[1] + base_h)],
        fill=_hex("#E8A234"),
        outline=OUTLINE,
        width=10,
    )
    # Wooden bed feet
    for lx, ly in [(b_tl[0] + 25 * SS, b_tl[1] + base_h), (b_fl[0] - 10 * SS, b_fl[1] + base_h), (b_fr[0] - 55 * SS, b_fr[1] + base_h)]:
        draw.rounded_rectangle([lx, ly - 10, lx + 55 * SS, ly + 65 * SS], radius=12, fill=_hex("#9E5B24"), outline=OUTLINE, width=9)


def draw_ep2_character(
    canvas: Image.Image,
    char_type: str,
    cx: int,
    cy: int,
    expression: str = "sleeping_peaceful",
    pose: str = "lying_bed",
    facing: int = 1,
    t: float = 0.0,
    scale: float = 1.45,
    face_mask: bool = False,
):
    """
    Renders Stikbro Male & Female characters with Episode 2 poses & expressions:
      - Poses: 'lying_bed', 'bed_kick', 'bed_falling_edge', 'sitting_floor_tantrum', 'sitting_sofa', 'standing_creep'
      - Expressions: 'sleeping_peaceful', 'angry_tantrum', 'shocked_terror', 'face_mask_grumpy', 'face_mask_smirk'
    """
    s = SS * scale
    X = int(cx * SS)
    Y = int(cy * SS)
    OUTLINE = _hex("#121214")
    WHITE = _hex("#FFFFFF")
    SHADE = _hex("#D2DCE6")
    MASK_COLOR = _hex("#9CB4C2")  # Authentic Stikbro grey-blue clay face mask (Thumbnail 5!)
    lw = max(5, int(7.5 * s))
    limb_w = max(8, int(10.5 * s))

    draw = ImageDraw.Draw(canvas, "RGBA")
    bob = int(math.sin(t * 6.0) * 3.5 * s)
    if expression in ("angry_tantrum", "shocked_terror"):
        bob = int(math.sin(t * 36.0) * 6.5 * s)

    # ── 1. LEGS ──
    hip_y = Y + int(78 * s) + bob
    hip_l = (X - int(24 * s * facing), hip_y)
    hip_r = (X + int(24 * s * facing), hip_y)

    if pose == "lying_bed":
        # Lying diagonally on the mattress (Thumbnail 4 - 5.5M views!)
        knee_l = (hip_l[0] + int(35 * s), hip_l[1] + int(65 * s))
        foot_l = (knee_l[0] + int(65 * s), knee_l[1] + int(65 * s))
        knee_r = (hip_r[0] + int(65 * s), hip_r[1] + int(45 * s))
        foot_r = (knee_r[0] + int(45 * s), knee_r[1] + int(75 * s))
    elif pose == "bed_kick":
        # Husband doing a huge kung-fu kick across the bed toward Wife!
        kick_ext = int((0.7 + 0.3 * math.sin(t * 18.0)) * 135 * s)
        knee_l = (hip_l[0] + int(20 * s), hip_l[1] + int(75 * s))
        foot_l = (knee_l[0] + int(35 * s), knee_l[1] + int(70 * s))
        knee_r = (hip_r[0] + int(85 * s), hip_r[1] - int(15 * s))
        foot_r = (knee_r[0] + kick_ext, knee_r[1] - int(25 * s))
    elif pose == "bed_falling_edge":
        # Wife being shoved off the right edge of the mattress!
        flail = int(math.sin(t * 28.0) * 20 * s)
        knee_l = (hip_l[0] + int(55 * s), hip_l[1] + int(45 * s) + flail)
        foot_l = (knee_l[0] + int(60 * s), knee_l[1] + int(65 * s))
        knee_r = (hip_r[0] + int(85 * s), hip_r[1] + int(25 * s) - flail)
        foot_r = (knee_r[0] + int(75 * s), knee_r[1] + int(55 * s))
    elif pose == "sitting_floor_tantrum":
        # Recreating Thumbnail 2 (336K views): Sitting flat on the wooden floor with legs bent out to the right!
        hip_y_fl = Y + int(88 * s)
        hip_l = (X - int(10 * s), hip_y_fl)
        hip_r = (X + int(28 * s), hip_y_fl)
        knee_l = (hip_l[0] + int(85 * s), hip_l[1] - int(22 * s))
        foot_l = (knee_l[0] + int(70 * s), knee_l[1] + int(68 * s))
        knee_r = (hip_r[0] + int(95 * s), hip_r[1] - int(35 * s))
        foot_r = (knee_r[0] + int(78 * s), knee_r[1] + int(65 * s))
    elif pose == "sitting_sofa":
        swing = int(math.sin(t * 4.5) * 7 * s)
        knee_l = (hip_l[0] + int(45 * s * facing), hip_l[1] + int(35 * s))
        foot_l = (knee_l[0] + int(25 * s * facing) + swing, knee_l[1] + int(112 * s))
        knee_r = (hip_r[0] + int(58 * s * facing), hip_r[1] + int(42 * s))
        foot_r = (knee_r[0] + int(32 * s * facing) - swing, knee_r[1] + int(112 * s))
    else:
        walk = int(math.sin(t * 10.0) * 20 * s)
        knee_l = (hip_l[0] - walk // 2, hip_l[1] + int(85 * s))
        foot_l = (hip_l[0] - walk, hip_l[1] + int(165 * s))
        knee_r = (hip_r[0] + walk // 2, hip_r[1] + int(85 * s))
        foot_r = (hip_r[0] + walk, hip_r[1] + int(165 * s))

    for hip, knee, foot in [(hip_l, knee_l, foot_l), (hip_r, knee_r, foot_r)]:
        _draw_thick_polyline(draw, [hip, knee, foot], OUTLINE, limb_w)
        fx_end = foot[0] + int(26 * s * facing)
        fy_end = foot[1] - int(6 * s)
        _draw_thick_polyline(draw, [foot, (fx_end, fy_end)], OUTLINE, int(limb_w * 1.15))

    # ── 2. TORSO ──
    torso_y = Y + bob
    torso_angle = 24 if pose in ("lying_bed", "bed_kick", "bed_falling_edge") else (-8 if pose == "sitting_floor_tantrum" else -4 * facing)

    if char_type == "male":
        _draw_oval_tilted(canvas, X, torso_y, int(55 * s), int(92 * s), angle_deg=torso_angle, fill=WHITE, outline=OUTLINE, width=lw, cel_shade=True)
    else:
        if pose in ("lying_bed", "bed_falling_edge"):
            # Angled white dress on bed
            _draw_oval_tilted(canvas, X, torso_y, int(56 * s), int(90 * s), angle_deg=torso_angle, fill=WHITE, outline=OUTLINE, width=lw, cel_shade=True)
        else:
            top_w, bot_w, th = int(38 * s), int(74 * s), int(96 * s)
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

    # ── 3. HEAD & HAIR ──
    if pose in ("lying_bed", "bed_kick", "bed_falling_edge"):
        head_cx = X - int(58 * s)
        head_cy = torso_y - int(148 * s)
    else:
        head_cx = X + int(6 * s * facing)
        head_cy = torso_y - int(174 * s)

    head_rx = int(112 * s)
    head_ry = int(104 * s)

    if char_type == "female":
        hw = head_rx + int(22 * s)
        htop = head_cy - head_ry - int(14 * s)
        hbot = head_cy + head_ry + int(18 * s)
        draw.rounded_rectangle([head_cx - hw, htop + int(20 * s), head_cx - int(25 * s), hbot], radius=int(38 * s), fill=OUTLINE)
        draw.rounded_rectangle([head_cx + int(25 * s), htop + int(20 * s), head_cx + hw, hbot], radius=int(38 * s), fill=OUTLINE)
        draw.ellipse([head_cx - hw + int(4 * s), htop, head_cx + hw - int(4 * s), head_cy + int(55 * s)], fill=OUTLINE)

    # Head base (Pure white)
    _draw_oval_tilted(canvas, head_cx, head_cy, head_rx, head_ry, angle_deg=0, fill=WHITE, outline=OUTLINE, width=lw, cel_shade=True)

    # Optional Grey-Blue Clay Beauty Face Mask (Exact match to Thumbnail 5!)
    if face_mask:
        m_rx, m_ry = int(head_rx * 0.88), int(head_ry * 0.86)
        draw.ellipse([head_cx - m_rx, head_cy - m_ry + int(6 * s), head_cx + m_rx, head_cy + m_ry + int(4 * s)], fill=MASK_COLOR, outline=_hex("#6D8796"), width=max(3, int(4 * s)))
        # Subtle clay texture wrinkle marks on forehead & cheeks
        for wx_o, wy_o in [(-18, -58), (12, -55), (-65, 22), (65, 22)]:
            draw.line(
                [(head_cx + int(wx_o * s), head_cy + int(wy_o * s)), (head_cx + int((wx_o + 14) * s), head_cy + int((wy_o + 8) * s))],
                fill=_hex("#6D8796"),
                width=max(3, int(4 * s)),
            )

    if char_type == "male":
        # On terror scream, hair stands straight up and vibrates!
        hair_len = 48 if expression == "shocked_terror" else 32
        for hx_off, h_ang in [(-16, -20), (0, -4), (16, 15)]:
            bx = head_cx + int(hx_off * s)
            by = head_cy - head_ry + int(4 * s)
            tx = bx + int(math.sin(math.radians(h_ang)) * hair_len * s)
            ty = by - int(hair_len * s)
            _draw_thick_polyline(draw, [(bx, by), (tx, ty)], OUTLINE, max(5, int(6 * s)))
    else:
        part_x = head_cx + int(16 * s * facing)
        top_y = head_cy - head_ry - int(6 * s)
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
    ex_l = head_cx - eye_sep + int(8 * s * facing)
    ex_r = head_cx + eye_sep + int(8 * s * facing)

    if face_mask:
        # White skin cutouts around eyes & mouth on top of the clay mask (Thumbnail 5!)
        for ex in (ex_l, ex_r):
            draw.ellipse([ex - int(34 * s), eye_y - int(38 * s), ex + int(34 * s), eye_y + int(38 * s)], fill=WHITE)
        draw.ellipse([head_cx - int(42 * s), head_cy + int(20 * s), head_cx + int(42 * s), head_cy + int(68 * s)], fill=WHITE)

    if expression == "sleeping_peaceful":
        # Peaceful curved closed eyes with eyelashes (Thumbnail 4!)
        for ex in (ex_l, ex_r):
            erx, ery = int(26 * s), int(22 * s)
            draw.arc([ex - erx, eye_y - ery, ex + erx, eye_y + ery], start=15, end=165, fill=OUTLINE, width=max(4, int(6.5 * s)))
            # Cute sleep eyelash ticks
            draw.line([(ex - int(18 * s), eye_y + int(12 * s)), (ex - int(24 * s), eye_y + int(22 * s))], fill=OUTLINE, width=max(3, int(5 * s)))
            draw.line([(ex, eye_y + int(18 * s)), (ex, eye_y + int(28 * s))], fill=OUTLINE, width=max(3, int(5 * s)))
        # Happy sleeping smile / snore mouth
        my = head_cy + int(38 * s)
        if char_type == "male":
            mr = int((12 + 4 * math.sin(t * 5.5)) * s)
            draw.ellipse([head_cx - mr, my - mr, head_cx + mr, my + mr], fill=_hex("#5B141B"), outline=OUTLINE, width=max(4, int(5.5 * s)))
        else:
            draw.arc([head_cx - int(22 * s), my - int(12 * s), head_cx + int(22 * s), my + int(14 * s)], start=15, end=165, fill=OUTLINE, width=max(4, int(6 * s)))

    elif expression == "angry_tantrum":
        # Recreating Thumbnail 2 (336K views): Wife's giant angry eyes + deep V-brows + gritted-teeth open mouth!
        for ex in (ex_l, ex_r):
            erx, ery = int(31 * s), int(37 * s)
            draw.ellipse([ex - erx, eye_y - ery, ex + erx, eye_y + ery], fill=WHITE, outline=OUTLINE, width=max(4, int(6.5 * s)))
            px, py, pr = ex + int(6 * s * facing), eye_y + int(2 * s), int(9 * s)
            draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=OUTLINE)
            # Under-eye stress line
            draw.arc([ex - int(24 * s), eye_y + int(22 * s), ex + int(24 * s), eye_y + int(46 * s)], start=200, end=340, fill=OUTLINE, width=max(3, int(4.5 * s)))
        # Deep furious V-eyebrows
        by = eye_y - int(46 * s)
        bw = max(5, int(8 * s))
        _draw_thick_polyline(draw, [(ex_l - int(28 * s), by - int(12 * s)), (ex_l + int(25 * s), by + int(16 * s))], OUTLINE, bw)
        _draw_thick_polyline(draw, [(ex_r - int(25 * s), by + int(16 * s)), (ex_r + int(28 * s), by - int(12 * s))], OUTLINE, bw)
        # Wide angry shouting mouth with white top & bottom gritted teeth (Thumbnail 2!)
        mx, my = head_cx + int(6 * s * facing), head_cy + int(44 * s)
        mrx, mry = int(46 * s), int(32 * s)
        draw.rounded_rectangle([mx - mrx, my - mry, mx + mrx, my + mry], radius=int(14 * s), fill=_hex("#5B141B"), outline=OUTLINE, width=max(4, int(6.5 * s)))
        draw.rectangle([mx - mrx + int(6 * s), my - mry + int(4 * s), mx + mrx - int(6 * s), my - mry + int(16 * s)], fill=WHITE, outline=OUTLINE, width=3)
        draw.chord([mx - int(32 * s), my + int(4 * s), mx + int(32 * s), my + mry], start=180, end=360, fill=_hex("#F07482"))

    elif expression == "shocked_terror":
        # Huge popped-out eyes with tiny pinpoint pupils + screaming mouth
        for ex in (ex_l, ex_r):
            erx, ery = int(34 * s), int(42 * s)
            draw.ellipse([ex - erx, eye_y - ery, ex + erx, eye_y + ery], fill=WHITE, outline=OUTLINE, width=max(4, int(6.5 * s)))
            pr = int(6 * s)
            draw.ellipse([ex - pr, eye_y - pr, ex + pr, eye_y + pr], fill=OUTLINE)
        by = eye_y - int(54 * s)
        bw = max(4, int(7 * s))
        _draw_thick_polyline(draw, [(ex_l - int(24 * s), by), (ex_l, by - int(18 * s)), (ex_l + int(22 * s), by - int(4 * s))], OUTLINE, bw)
        _draw_thick_polyline(draw, [(ex_r - int(22 * s), by - int(4 * s)), (ex_r, by - int(18 * s)), (ex_r + int(24 * s), by)], OUTLINE, bw)
        mx, my = head_cx + int(8 * s * facing), head_cy + int(44 * s)
        mrx, mry = int(48 * s), int(38 * s)
        draw.rounded_rectangle([mx - mrx, my - mry, mx + mrx, my + mry], radius=int(20 * s), fill=_hex("#5B141B"), outline=OUTLINE, width=max(4, int(6.5 * s)))
        draw.chord([mx - int(34 * s), my + int(4 * s), mx + int(34 * s), my + mry], start=180, end=360, fill=_hex("#F07482"))
        # Cold sweat drops flying off head
        for sx_o, sy_o in [(-88, -35), (88, -25)]:
            sx, sy = head_cx + int(sx_o * s), head_cy + int(sy_o * s)
            draw.ellipse([sx - int(9 * s), sy - int(14 * s), sx + int(9 * s), sy + int(14 * s)], fill=_hex("#6EC6FF"), outline=OUTLINE, width=3)

    elif expression == "face_mask_grumpy":
        # Recreating Thumbnail 5 (34.8K views): Half-closed exhausted droopy eyes + frowning mouth inside clay mask!
        for ex in (ex_l, ex_r):
            erx, ery = int(26 * s), int(28 * s)
            draw.ellipse([ex - erx, eye_y - ery, ex + erx, eye_y + ery], fill=WHITE, outline=OUTLINE, width=max(4, int(5.5 * s)))
            # Droopy top eyelid covering upper half of eye
            draw.chord([ex - erx, eye_y - ery, ex + erx, eye_y + ery], start=180, end=360, fill=_hex("#E2E8F0"), outline=OUTLINE, width=max(4, int(5.5 * s)))
            pr = int(7 * s)
            draw.ellipse([ex - pr, eye_y + int(6 * s) - pr, ex + pr, eye_y + int(6 * s) + pr], fill=OUTLINE)
            # Tired eye-bag arc below eye
            draw.arc([ex - int(20 * s), eye_y + int(22 * s), ex + int(20 * s), eye_y + int(38 * s)], start=20, end=160, fill=_hex("#6D8796"), width=max(3, int(4 * s)))
        # Sad/grumpy eyebrows
        by = eye_y - int(38 * s)
        bw = max(4, int(6.5 * s))
        _draw_thick_polyline(draw, [(ex_l - int(22 * s), by + int(8 * s)), (ex_l + int(20 * s), by - int(6 * s))], OUTLINE, bw)
        _draw_thick_polyline(draw, [(ex_r - int(20 * s), by - int(6 * s)), (ex_r + int(22 * s), by + int(8 * s))], OUTLINE, bw)
        # Grumpy downturned mouth with teeth
        mx, my = head_cx, head_cy + int(44 * s)
        draw.chord([mx - int(28 * s), my - int(14 * s), mx + int(28 * s), my + int(22 * s)], start=180, end=360, fill=WHITE, outline=OUTLINE, width=max(4, int(5.5 * s)))

    else:  # "face_mask_smirk" / happy
        for ex in (ex_l, ex_r):
            erx, ery = int(27 * s), int(32 * s)
            draw.ellipse([ex - erx, eye_y - ery, ex + erx, eye_y + ery], fill=WHITE, outline=OUTLINE, width=max(4, int(5.5 * s)))
            pr = int(8 * s)
            draw.ellipse([ex - pr, eye_y - pr, ex + pr, eye_y + pr], fill=OUTLINE)
        mx, my = head_cx, head_cy + int(40 * s)
        draw.chord([mx - int(30 * s), my - int(18 * s), mx + int(30 * s), my + int(22 * s)], start=0, end=180, fill=_hex("#5B141B"), outline=OUTLINE, width=max(4, int(5.5 * s)))

    # ── 5. ARMS ──
    sh_y = torso_y - int(48 * s)
    sh_l = (X - int(38 * s * facing), sh_y)
    sh_r = (X + int(38 * s * facing), sh_y)

    if pose == "lying_bed":
        # Arms relaxed downward on mattress (Thumbnail 4)
        elb_l = (sh_l[0] - int(28 * s), sh_l[1] + int(42 * s))
        hand_l = (elb_l[0] + int(18 * s), elb_l[1] + int(42 * s))
        elb_r = (sh_r[0] + int(42 * s), sh_r[1] + int(25 * s))
        hand_r = (elb_r[0] + int(22 * s), elb_r[1] + int(45 * s))
    elif pose == "bed_kick":
        # Husband sprawling arms wide while kicking!
        elb_l = (sh_l[0] - int(65 * s), sh_l[1] - int(35 * s))
        hand_l = (elb_l[0] - int(45 * s), elb_l[1] - int(25 * s))
        elb_r = (sh_r[0] + int(75 * s), sh_r[1] - int(20 * s))
        hand_r = (elb_r[0] + int(65 * s), elb_r[1] - int(10 * s))
    elif pose == "sitting_floor_tantrum":
        # Thumbnail 2 pose: Left arm bracing straight down on the floor, right arm bent in angry fist!
        elb_l = (X - int(65 * s), torso_y + int(35 * s))
        hand_l = (X - int(95 * s), torso_y + int(105 * s))
        elb_r = (X + int(58 * s), torso_y + int(18 * s))
        hand_r = (X + int(28 * s), torso_y + int(72 * s))
    elif expression == "shocked_terror":
        shake = int(math.sin(t * 45.0) * 10 * s)
        elb_l = (sh_l[0] - int(55 * s), sh_l[1] - int(25 * s) + shake)
        hand_l = (head_cx - int(95 * s), head_cy - int(10 * s) - shake)
        elb_r = (sh_r[0] + int(55 * s), sh_r[1] - int(25 * s) - shake)
        hand_r = (head_cx + int(95 * s), head_cy - int(10 * s) + shake)
    else:
        # Relaxed folded/lap arms on sofa (Thumbnail 5)
        elb_l = (sh_l[0] - int(38 * s * facing), sh_l[1] + int(52 * s))
        hand_l = (elb_l[0] + int(32 * s * facing), elb_l[1] + int(32 * s))
        elb_r = (sh_r[0] + int(38 * s * facing), sh_r[1] + int(52 * s))
        hand_r = (elb_r[0] - int(22 * s * facing), elb_r[1] + int(32 * s))

    for sh, elb, hand in [(sh_l, elb_l, hand_l), (sh_r, elb_r, hand_r)]:
        _draw_thick_polyline(draw, [sh, elb, hand], OUTLINE, limb_w)
        hr = int(limb_w * 0.85)
        draw.ellipse([hand[0] - hr, hand[1] - hr, hand[0] + hr, hand[1] + hr], fill=OUTLINE)


def _draw_ep2_vfx(img: Image.Image, scene_idx: int, t_rel: float):
    draw = ImageDraw.Draw(img, "RGBA")
    try:
        font_wm = ImageFont.truetype(str(PROJECT_ROOT / "assets" / "fonts" / "Montserrat-ExtraBold.ttf"), 40)
        font_vfx = ImageFont.truetype(str(PROJECT_ROOT / "assets" / "fonts" / "Montserrat-Black.ttf"), 68)
    except Exception:
        font_wm = ImageFont.load_default()
        font_vfx = ImageFont.load_default()

    if scene_idx == 1:
        # Floating Zzz above sleeping couple (Thumbnail 4!)
        for i, (zx, zy) in enumerate([(220, 740), (175, 665), (135, 590)]):
            dy = int(((t_rel * 40 + i * 18) % 40))
            draw.text((zx + 3, zy - dy + 3), "Z", font=font_vfx, fill=(0, 0, 0, 190))
            draw.text((zx, zy - dy), "Z", font=font_vfx, fill=(255, 255, 255, 235))
    elif scene_idx == 2 and t_rel > 0.4:
        # Comic POW / KICK impact burst!
        bx, by = 575, 1080
        pts = []
        for i in range(16):
            ang = i * math.pi / 8.0
            r = 85 if i % 2 == 0 else 42
            pts.append((bx + int(math.cos(ang) * r), by + int(math.sin(ang) * r)))
        draw.polygon(pts, fill=_hex("#FFCC00", 230), outline=_hex("#D92B2B"), width=5)
        draw.text((bx - 52, by - 35), "BAM!", font=font_wm, fill=_hex("#121214"))
    elif scene_idx == 3:
        # Throbbing red cartoon anger vein symbol above Wife's head on floor
        vx, vy = 625, 725
        sc_v = int(18 + 4 * math.sin(t_rel * 22.0))
        for dx_v, dy_v in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
            p1 = (vx + dx_v * sc_v * 2, vy + dy_v * int(sc_v * 0.5))
            p2 = (vx + dx_v * int(sc_v * 0.6), vy + dy_v * int(sc_v * 0.6))
            p3 = (vx + dx_v * int(sc_v * 0.5), vy + dy_v * sc_v * 2)
            _draw_thick_polyline(draw, [p1, p2, p3], _hex("#FF2D2D"), 8)

    wm_text = "Stikbro AI"
    draw.text((W - 248, H - 78), wm_text, font=font_wm, fill=(0, 0, 0, 140))
    draw.text((W - 250, H - 80), wm_text, font=font_wm, fill=(255, 255, 255, 210))


def render_ep2_frame(global_t: float) -> Image.Image:
    """Renders a single frame of Episode 2 at `global_t` seconds (0.0 .. 18.6s)."""
    OUTLINE = _hex("#1E120A")

    if global_t < 3.2:
        scene_idx, t_rel = 1, global_t
    elif global_t < 6.2:
        scene_idx, t_rel = 2, global_t - 3.2
    elif global_t < 9.2:
        scene_idx, t_rel = 3, global_t - 6.2
    elif global_t < 12.4:
        scene_idx, t_rel = 4, global_t - 9.2
    elif global_t < 15.4:
        scene_idx, t_rel = 5, global_t - 12.4
    else:
        scene_idx, t_rel = 6, global_t - 15.4

    if scene_idx in (1, 2, 4, 5):
        # Bedroom Scenes (1: Peaceful Sleep, 2: Kung-Fu Bed Kick, 4: Clay Mask Creep, 5: Monster Scream!)
        canvas = get_ep2_background("bedroom")
        draw_cozy_striped_bed(canvas)

        if scene_idx == 1:
            # Recreating Thumbnail 4 (5.5M views): Husband & Wife sleeping on the rainbow mattress bed!
            draw_ep2_character(canvas, "male", cx=335, cy=1115, expression="sleeping_peaceful", pose="lying_bed", facing=1, t=global_t, scale=1.35)
            draw_ep2_character(canvas, "female", cx=645, cy=1055, expression="sleeping_peaceful", pose="lying_bed", facing=1, t=global_t, scale=1.32)
        elif scene_idx == 2:
            # Husband kicks across the mattress; Wife slides off the right edge in shock!
            push_x = int(min(1.0, t_rel / 1.8) * 135)
            push_y = int(min(1.0, t_rel / 1.8) * 95)
            draw_ep2_character(canvas, "male", cx=375, cy=1110, expression="sleeping_peaceful", pose="bed_kick", facing=1, t=global_t, scale=1.38)
            draw_ep2_character(canvas, "female", cx=665 + push_x, cy=1065 + push_y, expression="shocked_terror", pose="bed_falling_edge", facing=1, t=global_t, scale=1.34)
        elif scene_idx == 4:
            # Husband snoring on bed while Wife creeps up in the foreground wearing a spooky grey clay face mask!
            draw_ep2_character(canvas, "male", cx=365, cy=1105, expression="sleeping_peaceful", pose="lying_bed", facing=1, t=global_t, scale=1.36)
            creep_x = int(880 - min(1.0, t_rel / 2.4) * 210)
            draw_ep2_character(canvas, "female", cx=creep_x, cy=1220, expression="face_mask_smirk", pose="standing_creep", facing=-1, t=global_t, scale=1.55, face_mask=True)
        else:
            # Scene 5: Husband wakes up nose-to-nose with Clay-Mask Wife and screams in terror!
            jump_y = int(abs(math.sin(t_rel * 12.0)) * 55)
            draw_ep2_character(canvas, "male", cx=310, cy=1040 - jump_y, expression="shocked_terror", pose="sitting_sofa", facing=1, t=global_t, scale=1.52)
            draw_ep2_character(canvas, "female", cx=735, cy=1140, expression="face_mask_smirk", pose="standing_creep", facing=-1, t=global_t, scale=1.54, face_mask=True)

        out = canvas.resize((W, H), Image.Resampling.LANCZOS)
        out = apply_volumetric_lamp_glow(out, intensity=0.92 if scene_idx == 4 else 1.04)

        if scene_idx == 1:
            out = _apply_camera_transform(out, zoom=1.0 + 0.08 * (t_rel / 3.2), pan_x=10, pan_y=25, t=global_t)
        elif scene_idx == 2:
            shake = 10.0 if 0.35 < t_rel < 1.4 else 0.0
            out = _apply_camera_transform(out, zoom=1.10, pan_x=45, pan_y=40, shake_px=shake, t=global_t)
        elif scene_idx == 4:
            out = _apply_camera_transform(out, zoom=1.12, pan_x=35, pan_y=30, t=global_t)
        else:
            out = _apply_camera_transform(out, zoom=1.16, pan_x=-20, pan_y=10, shake_px=8.5, t=global_t)

    elif scene_idx == 3:
        # Scene 3: Recreating Thumbnail 2 (336K views) — Wife sitting flat on the wooden floor throwing a tantrum!
        canvas = get_ep2_background("hallway_floor")
        draw = ImageDraw.Draw(canvas, "RGBA")
        # Soft shadow under Wife on floor
        draw.ellipse([230 * SS, 1510 * SS, 820 * SS, 1650 * SS], fill=(30, 14, 5, 50))
        draw_ep2_character(
            canvas, "female", cx=440, cy=1335, expression="angry_tantrum", pose="sitting_floor_tantrum", facing=1, t=global_t, scale=1.68
        )
        out = canvas.resize((W, H), Image.Resampling.LANCZOS)
        out = apply_volumetric_lamp_glow(out, intensity=1.05)
        out = _apply_camera_transform(out, zoom=1.04 + 0.10 * (t_rel / 3.0), pan_x=-15, pan_y=45, shake_px=5.5, t=global_t)

    else:
        # Scene 6: Recreating Thumbnail 5 (34.8K views) — Couple sitting on the living room sofa in Grey Clay Face Masks!
        canvas = get_ep2_background("living")
        draw = ImageDraw.Draw(canvas, "RGBA")
        sy = 1220 * SS
        draw.ellipse([60 * SS, 1510 * SS, 1020 * SS, 1660 * SS], fill=(30, 14, 5, 45))
        # Plush Cozy Sofa
        draw.rounded_rectangle([50 * SS, sy - 420, 990 * SS, sy + 240], radius=60, fill=_hex("#D9A782"), outline=OUTLINE, width=12)
        draw.line([(520 * SS, sy - 390), (520 * SS, sy + 180)], fill=_hex("#B07D59"), width=10)
        draw.rounded_rectangle([95 * SS, sy - 270, 315 * SS, sy + 20], radius=34, fill=_hex("#E07A5F"), outline=OUTLINE, width=8)
        draw.rounded_rectangle([30 * SS, sy + 20, 1010 * SS, sy + 330], radius=44, fill=_hex("#C9946E"), outline=OUTLINE, width=12)

        # Husband grumpy/exhausted in clay face mask; Wife relaxed in clay face mask!
        draw_ep2_character(canvas, "male", cx=295, cy=1135, expression="face_mask_grumpy", pose="sitting_sofa", facing=1, t=global_t, scale=1.56, face_mask=True)
        draw_ep2_character(canvas, "female", cx=775, cy=1145, expression="face_mask_grumpy", pose="sitting_sofa", facing=-1, t=global_t, scale=1.48, face_mask=True)

        out = canvas.resize((W, H), Image.Resampling.LANCZOS)
        out = apply_volumetric_lamp_glow(out, intensity=1.04)
        out = _apply_camera_transform(out, zoom=1.05 + 0.08 * min(1.0, t_rel / 2.5), pan_x=0, pan_y=15, t=global_t)

    _draw_ep2_vfx(out, scene_idx, t_rel)
    return out


def synthesize_ep2_audio(out_wav_path: str, duration_s: float = 18.6, sr: int = 44100):
    """Synthesizes Episode 2 cartoon SFX + sneaky lullaby-to-chaos comedy BGM."""
    n_samples = int(duration_s * sr)
    track = np.zeros(n_samples, dtype=np.float32)

    def add_tone(t_start: float, dur: float, f0: float, f1: Optional[float] = None, vol: float = 0.25, wave_type: str = "sine", vib_hz: float = 0.0, vib_depth: float = 0.0):
        i0 = max(0, int(t_start * sr))
        i1 = min(n_samples, int((t_start + dur) * sr))
        if i1 <= i0:
            return
        lt = np.linspace(0, dur, i1 - i0, endpoint=False, dtype=np.float32)
        freq = f0 + (f1 - f0) * (lt / dur) if (f1 is not None and f1 != f0) else np.full_like(lt, f0)
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
        attack = np.minimum(1.0, lt / 0.012)
        decay = np.exp(-lt / max(0.04, dur * 0.45))
        track[i0:i1] += sig * attack * decay * vol

    def add_noise_burst(t_start: float, dur: float, vol: float = 0.2, mod_hz: float = 0.0):
        i0 = max(0, int(t_start * sr))
        i1 = min(n_samples, int((t_start + dur) * sr))
        if i1 <= i0:
            return
        lt = np.linspace(0, dur, i1 - i0, endpoint=False, dtype=np.float32)
        rng = np.random.default_rng(int(t_start * 1000) + 19)
        noise = rng.standard_normal(i1 - i0).astype(np.float32)
        noise = np.convolve(noise, np.ones(7, dtype=np.float32) / 7.0, mode="same")
        env = np.sin(np.pi * lt / dur) ** 0.7
        if mod_hz > 0:
            env = env * (0.55 + 0.45 * np.sin(2 * np.pi * mod_hz * lt))
        track[i0:i1] += noise * env * vol

    # 1. Playful Music Box / Pizzicato BGM
    beat_dur = 0.25
    bass_notes = [196.0, 246.94, 293.66, 246.94, 174.61, 220.0, 261.63, 220.0]
    melody_notes = [392.0, 0, 493.88, 587.33, 493.88, 0, 392.0, 0, 349.23, 0, 440.0, 523.25, 440.0, 392.0, 349.23, 0]
    step = 0
    cur_t = 0.0
    while cur_t < duration_s - 0.3:
        b_f = bass_notes[step % len(bass_notes)]
        add_tone(cur_t, 0.16, b_f * 0.5, vol=0.10, wave_type="triangle")
        m_f = melody_notes[step % len(melody_notes)]
        if m_f > 0:
            add_tone(cur_t, 0.14, m_f, vol=0.09, wave_type="sine")
        cur_t += beat_dur
        step += 1

    # Scene 1 (0.0s - 3.2s): Peaceful Snoring on Bed
    for sn_t in [0.35, 1.75]:
        add_tone(sn_t, 0.45, 125.0, 160.0, vol=0.25, wave_type="saw", vib_hz=26.0, vib_depth=0.14)
        for mi in range(3):
            add_tone(sn_t + 0.50 + mi * 0.14, 0.10, 660.0 + mi * 50.0, 710.0 + mi * 50.0, vol=0.18, wave_type="sine")

    # Scene 2 (3.2s - 6.2s): Kung-Fu Kick WHOOSH + BAM Impact + Falling Slide Whistle!
    add_noise_burst(3.45, 0.25, vol=0.32, mod_hz=12.0)
    add_tone(3.45, 0.22, 220.0, 580.0, vol=0.28, wave_type="sine")
    # Heavy BAM! kick impact
    add_tone(3.72, 0.32, 140.0, 45.0, vol=0.42, wave_type="sine")
    add_noise_burst(3.72, 0.22, vol=0.35)
    # Falling off bed slide whistle + Floor THUD!
    add_tone(4.15, 0.65, 980.0, 210.0, vol=0.30, wave_type="sine")
    add_tone(4.85, 0.35, 110.0, 42.0, vol=0.42, wave_type="triangle")

    # Scene 3 (6.2s - 9.2s): Wife Angry Tantrum on Floor (Boiling Teapot Rumble + Grumble)
    add_noise_burst(6.35, 2.40, vol=0.26, mod_hz=32.0)
    add_tone(6.40, 2.20, 180.0, 290.0, vol=0.24, wave_type="saw", vib_hz=20.0, vib_depth=0.15)

    # Scene 4 (9.2s - 12.4s): Spooky Theremin Ghost Creep ("WooooOOOOoooo...")
    add_tone(9.40, 2.70, 440.0, 660.0, vol=0.26, wave_type="sine", vib_hz=7.5, vib_depth=0.09)
    add_tone(9.40, 2.70, 880.0, 1320.0, vol=0.12, wave_type="triangle", vib_hz=7.5, vib_depth=0.09)

    # Scene 5 (12.4s - 15.4s): Husband Terror Wake-up Scream!
    add_tone(12.50, 0.20, 300.0, 1200.0, vol=0.35, wave_type="triangle")
    add_tone(12.75, 2.30, 820.0, 680.0, vol=0.28, wave_type="saw", vib_hz=18.0, vib_depth=0.14)
    add_noise_burst(12.75, 2.20, vol=0.22, mod_hz=18.0)

    # Scene 6 (15.4s - 18.6s): Grumpy Couple Face Mask Sigh + Sad Trombone ("Wah-wah-wah-waaaah!")
    add_tone(15.65, 0.32, 392.0, 370.0, vol=0.30, wave_type="saw", vib_hz=6.0, vib_depth=0.04)
    add_tone(16.02, 0.32, 369.99, 349.23, vol=0.30, wave_type="saw", vib_hz=6.0, vib_depth=0.04)
    add_tone(16.40, 0.32, 349.23, 329.63, vol=0.30, wave_type="saw", vib_hz=6.0, vib_depth=0.04)
    add_tone(16.78, 1.15, 329.63, 277.18, vol=0.34, wave_type="saw", vib_hz=10.0, vib_depth=0.08)

    peak = float(np.max(np.abs(track))) or 1.0
    track = np.clip(track / peak * 0.88, -0.98, 0.98)
    pcm16 = (track * 32767.0).astype(np.int16)
    stereo = np.column_stack([pcm16, pcm16]).ravel()

    with wave.open(out_wav_path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(stereo.tobytes())


async def mix_ep2_vocals(base_wav_path: str, final_wav_path: str, temp_dir: Path):
    import edge_tts

    clips_spec = [
        (3.55, "en-US-RogerNeural", "+20%", "+20Hz", "Hiyah!"),
        (4.10, "en-US-AvaMultilingualNeural", "+20%", "+28Hz", "Whoa! Hey!"),
        (6.40, "en-US-AvaMultilingualNeural", "+15%", "+18Hz", "Ugh! You kicked me off the bed!"),
        (9.55, "en-US-AvaMultilingualNeural", "+10%", "+12Hz", "Hehehe... skincare revenge!"),
        (12.60, "en-US-RogerNeural", "+25%", "+35Hz", "AAAAH! A GHOST!"),
        (15.60, "en-US-RogerNeural", "-5%", "-10Hz", "Why do I have to wear this mask too..."),
    ]

    generated: List[Tuple[int, str]] = []
    for idx, (t_sec, voice, rate, pitch, text) in enumerate(clips_spec):
        out_mp3 = temp_dir / f"ep2_voc_{idx}.mp3"
        try:
            comm = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
            await comm.save(str(out_mp3))
            if out_mp3.exists() and out_mp3.stat().st_size > 500:
                generated.append((int(t_sec * 1000), str(out_mp3)))
        except Exception as e:
            print(f"[Ep2 Vocal warn] {e}")

    if not generated:
        import shutil
        shutil.copyfile(base_wav_path, final_wav_path)
        return

    cmd = ["ffmpeg", "-y", "-i", base_wav_path]
    for _, mp3_p in generated:
        cmd.extend(["-i", mp3_p])

    filter_parts = ["[0:a]volume=0.82[bgm]"]
    mix_inputs = ["[bgm]"]
    for i, (delay_ms, _) in enumerate(generated, start=1):
        tag = f"[v{i}]"
        filter_parts.append(f"[{i}:a]adelay={delay_ms}|{delay_ms},volume=1.35{tag}")
        mix_inputs.append(tag)

    amix_str = "".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=0,alimiter=limit=0.95[outa]"
    filter_parts.append(amix_str)

    cmd.extend(["-filter_complex", ";".join(filter_parts), "-map", "[outa]", "-c:a", "pcm_s16le", final_wav_path])
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def build_ep2_video(send_telegram: bool = True) -> str:
    t0 = time.time()
    out_dir = PROJECT_ROOT / "storage" / "outputs"
    temp_dir = PROJECT_ROOT / "storage" / "temp" / "stikbro_ep2"
    out_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    duration_s = 18.6
    total_frames = int(duration_s * FPS)

    # 1. Save 2x2 Storyboard Grid first
    f1 = render_ep2_frame(1.5)
    f2 = render_ep2_frame(7.5)
    f3 = render_ep2_frame(13.6)
    f4 = render_ep2_frame(16.8)
    grid = Image.new("RGB", (W * 2, H * 2))
    grid.paste(f1, (0, 0))
    grid.paste(f2, (W, 0))
    grid.paste(f3, (0, H))
    grid.paste(f4, (W, H))
    grid_path = PROJECT_ROOT / "scratch" / "stikbro_ep2_storyboard.jpg"
    grid.resize((1080, 1920), Image.Resampling.LANCZOS).save(str(grid_path), "JPEG", quality=92)
    print(f"✅ Saved Ep2 storyboard -> {grid_path}")

    # 2. Synthesize audio + vocals
    base_wav = str(temp_dir / "ep2_sfx_bgm.wav")
    final_wav = str(temp_dir / "ep2_master_audio.wav")
    synthesize_ep2_audio(base_wav, duration_s=duration_s)
    asyncio.run(mix_ep2_vocals(base_wav, final_wav, temp_dir))

    # 3. Render 24 FPS video stream
    out_mp4 = str(out_dir / "stikbro_ep2_bed_kick_mask_revenge.mp4")
    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{W}x{H}", "-pix_fmt", "rgb24", "-r", str(FPS),
        "-i", "-",
        "-i", final_wav,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart",
        out_mp4,
    ]
    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert proc.stdin is not None
    for f_idx in range(total_frames):
        frame_img = render_ep2_frame(f_idx / float(FPS))
        proc.stdin.write(frame_img.tobytes())
    proc.stdin.close()
    proc.wait(timeout=180)

    elapsed = time.time() - t0
    size_mb = Path(out_mp4).stat().st_size / (1024 * 1024)
    print(f"🎬 Rendered Ep2 MP4 ({total_frames} frames, {size_mb:.2f} MB) in {elapsed:.1f}s -> {out_mp4}")

    if send_telegram:
        from app.services.intelligence.scheduler_and_broadcast import send_telegram_video_broadcast
        caption = (
            "🎬 <b>[TẬP 2 - CÂU CHUYỆN MỚI] Stikbro AI: Cú Đá Giữa Đêm & Mặt Nạ Đất Sét Trả Thù! 🛏️💥🎭</b>\n\n"
            "📌 <b>Kịch bản (6 cảnh - 18.6s, tái hiện 3 bối cảnh triệu view mới trong ảnh mẫu):</b>\n"
            "• <b>Cảnh 1 (Phòng ngủ xanh 5.5M view):</b> Hai vợ chồng đang ngủ trên chiếc giường đệm cầu vồng\n"
            "• <b>Cảnh 2:</b> Chồng ngủ mơ tung cú đá Kung-Fu đẩy vợ văng khỏi mép giường!\n"
            "• <b>Cảnh 3 (Ngồi bệt sàn nhà 336K view):</b> Vợ ngã ngồi bệt dưới sàn gỗ, nghiến răng tức điên 💢\n"
            "• <b>Cảnh 4:</b> Nửa đêm vợ đắp mặt nạ đất sét xanh xám lẻn vào phòng như bóng ma\n"
            "• <b>Cảnh 5:</b> Chồng mở mắt thấy mặt nạ sát mặt, hét thất thanh dựng đứng 3 sợi tóc!\n"
            "• <b>Cảnh 6 (Đắp mặt nạ đôi trên Sofa):</b> Sáng hôm sau chồng bị phạt ngồi đắp mặt nạ đất sét cùng vợ kèm tiếng kèn <i>Sad Trombone</i>!"
        )
        ok = send_telegram_video_broadcast(out_mp4, caption=caption, prefer_groups=True)
        print(f"📨 Telegram Group Broadcast status: {ok}")

    return out_mp4


if __name__ == "__main__":
    build_ep2_video(send_telegram=True)
