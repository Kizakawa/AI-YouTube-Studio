"""
app.services.media.doodle_animation_tool — Công cụ Hoạt Họa 2D Doodle / Sketchbook Chuyên Dụng Cho Agent (Agent-Callable Tool).

Mục đích:
  Đóng gói toàn bộ bộ máy dựng hoạt họa 2D Doodle / Stickman (1080x1920 @ 24 FPS, nét vẽ bút dạ đen
  Line-Boil trên nền giấy kem ấm `#F7F3E8`, khóa nhân vật `@CHARACTER_TAG`, chuyển động máy quay 7 lớp
  EyeCandy, âm thanh SFX thủ tục, kèm xuất `edit_manifest.json` & `draft_content.json` CapCut) thành
  một Tool chuẩn có Schema và Mô tả tường minh để các Agent (`VideoStrategistAgent`, `VisualToolRouter`,
  `AIVideoAgent`, hoặc LLM Orchestrator) tự động nhận biết KHI NÀO CẦN GỌI và CÁCH SỬ DỤNG.

Khi nào Agent NÊN gọi Tool `doodle_2d_animator`:
  1. Kênh thuộc ngách:
     - `satirical_psychology_animation` (Hoạt họa Tâm lý Châm biếm)
     - `dark_psychology` / `psychology_stoicism` (Tâm lý học Đen, Thao túng tâm lý, Chủ nghĩa Khắc kỷ)
     - `personal_finance` / `wealth_traps` (Tài chính cá nhân, Bẫy nợ, Lạm phát, Kinh tế học hành vi)
     - `brain_hacks` / `dopamine_detox` (Cơ chế não bộ, Giải độc Dopamine, Thói quen & Năng suất)
     - `absurd_history` / `what_if_science` (Nghịch lý cuộc sống hiện đại, Lịch sử hài hước, Giải thích khoa học)
  2. Phân cảnh mang bản chất `ABSTRACT_CONCEPT` / `DOODLE_PSYCHOLOGY_EXPLAINER` (ẩn dụ tâm lý, dòng tiền,
     so sánh 2 lối sống, cơ chế não bộ) mà video stock người thật không thể diễn tả trực quan.
  3. Prompt hoặc kịch bản có chứa các thẻ nhân vật `@MODERNYOU`, `@PLEASER`, `@MANIPULATOR`, `@ANCESTOR`, `@FARMER`
     hoặc từ khóa `2d doodle`, `sketchbook animation`, `stickman explainer`, `whiteboard animation`.

Khi nào Agent KHÔNG NÊN gọi Tool này:
  - Các chủ đề Động vật hoang dã săn mồi (`wildlife_predators`), Cứu hộ động vật (`animal_rescue`),
    Thiên nhiên kỳ thú (`REAL_FILMABLE`), hoặc Quái vật đại dương sâu (`DEEP_SEA_MYSTERY`) — những ngách này
    bắt buộc ưu tiên Video chuyển động thật (`>= 80%` Stock Video) hoặc CGI 3D điện ảnh.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import subprocess
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.schemas.contracts import RenderJobContract, SceneContract
from app.services.media.cinematic_dictionary import (
    build_character_reference_sheet_prompt,
    build_seven_layer_cinematic_prompt,
)
from app.services.media.seedance_director import SeedanceDirector
from app.services.media.timeline_manifest import export_job_timeline_artifacts

logger = logging.getLogger(__name__)

# ── BẢNG MÀU & THÔNG SỐ CHUẨN 2D DOODLE STUDIO ──────────────────────────────
PAPER_BG = (247, 243, 232)        # #F7F3E8 Warm Cream Sketchbook Paper
PAPER_GRAIN = (234, 228, 213)     # Subtle paper texture lines
INK_BLACK = (24, 24, 24)          # Bold black marker outline
SHADOW_GREY = (210, 204, 190)     # Soft drop shadow on paper

TAG_COLOR_RGB: Dict[str, Tuple[int, int, int]] = {
    "@MODERNYOU": (74, 111, 165),    # #4A6FA5 Slate-Blue Shirt (Exhausted Modern Worker / Overthinker)
    "@PLEASER": (91, 140, 133),      # #5B8C85 Muted Teal Hoodie (People-Pleaser / Empath)
    "@MANIPULATOR": (158, 42, 43),   # #9E2A2B Deep Crimson Collar (Narcissist / Puppeteer)
    "@ANCESTOR": (200, 125, 50),     # #C87D32 Warm Ochre Tunic (Prehistoric Hunter / Stoic Sage)
    "@FARMER": (139, 94, 60),        # #8B5E3C Earthy Brown (Overburdened Laborer / Debt Prisoner)
}

ALERT_RED = (214, 48, 49)         # #D63031 Danger / Cortisol / Debt Stamp
GOLD_ACCENT = (230, 161, 0)       # #E6A100 Metric Highlight / Wealth / Insight
GREEN_CALM = (46, 139, 87)        # #2E8B57 Freedom / Recovery / Dopamine Balance

SUITABLE_DOODLE_NICHES: Tuple[str, ...] = (
    "satirical_psychology_animation",
    "dark_psychology",
    "psychology_stoicism",
    "personal_finance",
    "wealth_traps",
    "brain_hacks",
    "dopamine_detox",
    "absurd_history",
    "what_if_science",
)

EXCLUDED_REAL_FOOTAGE_NICHES: Tuple[str, ...] = (
    "wildlife_predators",
    "animal_rescue",
    "deep_sea_mysteries",
    "deep_sea_earth_mysteries",
    "nature",
)

# ── SCHEMA MÔ TẢ CÔNG CỤ DÀNH CHO LLM AGENT / FUNCTION CALLING ──────────────
DOODLE_ANIMATION_TOOL_SCHEMA: Dict[str, Any] = {
    "name": "doodle_2d_animator",
    "display_name": "GOHA 2D Doodle & Stickman Explainer Studio (24 FPS)",
    "version": "2.0",
    "description": (
        "Công cụ dựng hoạt họa 2D Doodle / Stickman Explainer chuyên nghiệp (1080x1920 @ 24 FPS) trên nền giấy kem "
        "ấm (#F7F3E8) với nét vẽ bút dạ đen rung tự nhiên (line-boil), khóa đồng nhất nhân vật bằng thẻ @CHARACTER_TAG "
        "(@MODERNYOU, @PLEASER, @MANIPULATOR, @ANCESTOR, @FARMER), tích hợp 20 chuyển động máy quay điện ảnh EyeCandy "
        "(dolly_in, overhead_90, arc_orbit, worms_eye, dolly_zoom, dolly_out) và 8 đạo cụ ẩn dụ trực quan. "
        "SỬ DỤNG KHI: làm video thuộc các ngách Tâm lý học Đen (Dark Psychology), Thao túng tâm lý, Nghịch lý cuộc sống "
        "hiện đại (Modern Rat-Race), Tài chính cá nhân & Bẫy tiền bạc (Personal Finance), Cơ chế não bộ & Dopamine Detox, "
        "hoặc khi phân cảnh là khái niệm trừu tượng (ABSTRACT_CONCEPT) mà video quay thật không thể diễn tả. "
        "KHÔNG SỬ DỤNG KHI: làm video Động vật hoang dã săn mồi (wildlife_predators), Cứu hộ động vật (animal_rescue) "
        "hoặc Thiên nhiên có thật (REAL_FILMABLE)."
    ),
    "when_to_use": [
        "Kênh thuộc ngách satirical_psychology_animation, dark_psychology, personal_finance, brain_hacks, dopamine_detox.",
        "Phân cảnh giải thích cơ chế tâm lý vô hình (gaslighting, thao túng, overthinking, burnout, cortisol spike).",
        "Phân cảnh giải thích bẫy tài chính (nợ thẻ tín dụng, lạm phát, lãi kép, nghèo đi vì mua sắm).",
        "Phân cảnh so sánh đối lập 2 lối sống hoặc 2 thời đại (Modern Worker vs Ancient Hunter, Rich vs Broke Mindset).",
        "Prompt có chứa @MODERNYOU, @PLEASER, @MANIPULATOR, @ANCESTOR, @FARMER hoặc yêu cầu hoạt họa 2D Doodle.",
    ],
    "when_not_to_use": [
        "Chủ đề Động vật hoang dã săn mồi (Báo đốm, Cá sấu, Hổ, Sư tử, Đại bàng) -> Dùng pexels_video_api (Stock Video thật).",
        "Chủ đề Bí ẩn đại dương sâu / Thủy quái CGI (Megalodon, Colossal Squid) -> Dùng creature_horror_art / generative_video.",
        "Tư liệu lịch sử có ảnh chụp/phim lưu trữ thật -> Dùng wikimedia_archive.",
    ],
    "parameters": {
        "type": "object",
        "properties": {
            "narration": {
                "type": "string",
                "description": "Lời dẫn thuyết minh của phân cảnh (cần chứa số liệu hoặc hành động ẩn dụ rõ ràng).",
            },
            "character_tag": {
                "type": "string",
                "enum": ["@MODERNYOU", "@PLEASER", "@MANIPULATOR", "@ANCESTOR", "@FARMER"],
                "description": "Thẻ nhân vật chủ đạo của phân cảnh để khóa màu áo và biểu cảm đồng nhất.",
            },
            "prop_type": {
                "type": "string",
                "enum": [
                    "alarm",
                    "block",
                    "spear_nature",
                    "split",
                    "climax",
                    "puppet_strings",
                    "brain_battery",
                    "money_scale",
                ],
                "description": (
                    "Đạo cụ ẩn dụ hình ảnh: 'alarm' (đồng hồ báo thức/áp lực thời gian), 'block' (khối tạ 500-lb đè nặng/nợ nần/burnout), "
                    "'spear_nature' (tự do/tối giản/thiên nhiên), 'split' (so sánh 2 nửa màn hình), 'climax' (bừng tỉnh/thoát bẫy), "
                    "'puppet_strings' (bàn tay thao túng giật dây con rối), 'brain_battery' (pin não bộ cạn kiệt/dopamine), "
                    "'money_scale' (cán cân tiền bạc/lãi suất/bẫy tài chính)."
                ),
            },
            "camera_motion": {
                "type": "string",
                "enum": [
                    "dolly_in",
                    "dolly_out",
                    "dolly_zoom",
                    "overhead_90",
                    "worms_eye",
                    "arc_orbit",
                    "pan_left",
                    "pan_right",
                    "whip_pan",
                    "dutch_angle",
                ],
                "description": "Chuyển động máy quay vật lý theo từ điển 20 góc máy EyeCandy.",
            },
            "headline": {
                "type": "string",
                "description": "Tiêu đề ngắn (2-4 từ IN HOA) vẽ ở đầu trang giấy để đóng đinh thông điệp.",
            },
            "sub_badge": {
                "type": "string",
                "description": "Nhãn số liệu màu đỏ/vàng đóng dấu lên khung hình (VD: '15 HRS/WEEK', '-400% DOPAMINE', '24% APR TRAP').",
            },
        },
        "required": ["narration"],
    },
}


@dataclass
class DoodleSceneConfig:
    """Cấu hình 1 phân cảnh hoạt họa 2D Doodle."""
    scene_id: int
    headline: str
    sub_badge: str
    narration: str
    character_tag: str = "@MODERNYOU"
    prop_type: str = "alarm"
    camera_motion: str = "dolly_in"
    expression: str = "exhausted"
    raw_prompt: str = ""
    duration_s: float = 5.0
    audio_path: str = ""
    accent_rgb: Tuple[int, int, int] = field(default_factory=lambda: (74, 111, 165))

    def __post_init__(self) -> None:
        tag_upper = (self.character_tag or "@MODERNYOU").strip().upper()
        if not tag_upper.startswith("@"):
            tag_upper = "@" + tag_upper
        self.character_tag = tag_upper
        self.accent_rgb = TAG_COLOR_RGB.get(tag_upper, TAG_COLOR_RGB["@MODERNYOU"])
        if not self.raw_prompt:
            self.raw_prompt = (
                f"{self.character_tag} in 2D hand-drawn doodle cartoon style on warm cream paper, "
                f"visual metaphor '{self.prop_type}', badge '{self.sub_badge}', expression {self.expression}"
            )


def _get_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _wobble_pts(
    pts: List[Tuple[float, float]],
    seed: int,
    amp: float = 2.2,
) -> List[Tuple[float, float]]:
    rng = np.random.default_rng(seed)
    return [
        (x + float(rng.uniform(-amp, amp)), y + float(rng.uniform(-amp, amp)))
        for x, y in pts
    ]


def _draw_sketch_line(
    draw: ImageDraw.ImageDraw,
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    seed: int,
    color: Tuple[int, int, int] = INK_BLACK,
    width: int = 7,
) -> None:
    steps = 7
    pts = [
        (p1[0] + (p2[0] - p1[0]) * i / steps, p1[1] + (p2[1] - p1[1]) * i / steps)
        for i in range(steps + 1)
    ]
    w_pts = _wobble_pts(pts, seed=seed, amp=2.0)
    draw.line(w_pts, fill=color, width=width, joint="curve")


def _draw_sketch_circle(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    r: float,
    seed: int,
    fill: Optional[Tuple[int, int, int]] = None,
    outline: Tuple[int, int, int] = INK_BLACK,
    width: int = 7,
) -> None:
    pts = []
    n = 28
    rng = np.random.default_rng(seed)
    for i in range(n + 1):
        ang = (2 * math.pi * i) / n
        dr = float(rng.uniform(-2.2, 2.2))
        pts.append((cx + (r + dr) * math.cos(ang), cy + (r + dr) * math.sin(ang)))
    if fill:
        draw.polygon(pts, fill=fill)
    draw.line(pts, fill=outline, width=width, joint="curve")


def _draw_doodle_character(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    scale: float,
    shirt_rgb: Tuple[int, int, int],
    expression: str,
    phase: float,
    frame_seed: int,
    hunched: bool = False,
) -> None:
    head_r = 68 * scale
    bob_y = math.sin(phase * 2 * math.pi) * (10 * scale)
    head_cy = cy - 170 * scale + bob_y + (28 * scale if hunched else 0)
    head_cx = cx + (18 * scale if hunched else 0)

    # Soft ground shadow
    draw.ellipse(
        [cx - 95 * scale, cy + 195 * scale, cx + 95 * scale, cy + 230 * scale],
        fill=SHADOW_GREY,
    )

    # Torso (colored shirt with bold ink outline)
    torso_top = head_cy + head_r * 0.85
    torso_bot = cy + 65 * scale
    torso_pts = [
        (cx - 58 * scale, torso_top),
        (cx + 58 * scale, torso_top),
        (cx + 72 * scale, torso_bot),
        (cx - 72 * scale, torso_bot),
    ]
    w_torso = _wobble_pts(torso_pts, seed=frame_seed + 11, amp=2.2)
    draw.polygon(w_torso, fill=shirt_rgb)
    draw.line(w_torso + [w_torso[0]], fill=INK_BLACK, width=max(4, int(7 * scale)), joint="curve")

    # Legs
    walk_swing = math.sin(phase * 2 * math.pi) * 22 * scale
    _draw_sketch_line(
        draw,
        (cx - 32 * scale, torso_bot),
        (cx - 42 * scale - walk_swing, cy + 210 * scale),
        seed=frame_seed + 21,
        width=max(4, int(8 * scale)),
    )
    _draw_sketch_line(
        draw,
        (cx + 32 * scale, torso_bot),
        (cx + 42 * scale + walk_swing, cy + 210 * scale),
        seed=frame_seed + 22,
        width=max(4, int(8 * scale)),
    )

    # Arms
    arm_wave = math.cos(phase * 2 * math.pi) * 30 * scale
    if expression in ("exhausted", "crushed"):
        _draw_sketch_line(
            draw,
            (cx - 55 * scale, torso_top + 20 * scale),
            (cx - 95 * scale, torso_top - 45 * scale + arm_wave * 0.4),
            seed=frame_seed + 31,
            width=max(4, int(7 * scale)),
        )
        _draw_sketch_line(
            draw,
            (cx + 55 * scale, torso_top + 20 * scale),
            (cx + 95 * scale, torso_top - 45 * scale - arm_wave * 0.4),
            seed=frame_seed + 32,
            width=max(4, int(7 * scale)),
        )
    else:
        _draw_sketch_line(
            draw,
            (cx - 55 * scale, torso_top + 20 * scale),
            (cx - 105 * scale, torso_top + 75 * scale + arm_wave),
            seed=frame_seed + 31,
            width=max(4, int(7 * scale)),
        )
        _draw_sketch_line(
            draw,
            (cx + 55 * scale, torso_top + 20 * scale),
            (cx + 105 * scale, torso_top + 45 * scale - arm_wave),
            seed=frame_seed + 32,
            width=max(4, int(7 * scale)),
        )

    # Head
    _draw_sketch_circle(
        draw,
        head_cx,
        head_cy,
        head_r,
        seed=frame_seed + 41,
        fill=(255, 253, 248),
        outline=INK_BLACK,
        width=max(4, int(7 * scale)),
    )

    # Eyes & Expression
    eye_y = head_cy - 12 * scale
    eye_dx = 24 * scale
    if expression in ("exhausted", "crushed", "shocked"):
        _draw_sketch_circle(draw, head_cx - eye_dx, eye_y, 14 * scale, seed=frame_seed + 51, fill=(255, 255, 255), width=4)
        _draw_sketch_circle(draw, head_cx + eye_dx, eye_y, 14 * scale, seed=frame_seed + 52, fill=(255, 255, 255), width=4)
        draw.ellipse([head_cx - eye_dx - 4, eye_y - 4, head_cx - eye_dx + 4, eye_y + 4], fill=INK_BLACK)
        draw.ellipse([head_cx + eye_dx - 4, eye_y - 4, head_cx + eye_dx + 4, eye_y + 4], fill=INK_BLACK)
        draw.arc(
            [head_cx - eye_dx - 16 * scale, eye_y + 6 * scale, head_cx - eye_dx + 16 * scale, eye_y + 24 * scale],
            start=15,
            end=165,
            fill=INK_BLACK,
            width=3,
        )
        draw.arc(
            [head_cx + eye_dx - 16 * scale, eye_y + 6 * scale, head_cx + eye_dx + 16 * scale, eye_y + 24 * scale],
            start=15,
            end=165,
            fill=INK_BLACK,
            width=3,
        )
        draw.arc(
            [head_cx - 26 * scale, head_cy + 18 * scale, head_cx + 26 * scale, head_cy + 48 * scale],
            start=195,
            end=345,
            fill=INK_BLACK,
            width=5,
        )
    elif expression == "smirk":
        draw.ellipse([head_cx - eye_dx - 6, eye_y - 6, head_cx - eye_dx + 6, eye_y + 6], fill=INK_BLACK)
        draw.ellipse([head_cx + eye_dx - 6, eye_y - 6, head_cx + eye_dx + 6, eye_y + 6], fill=INK_BLACK)
        _draw_sketch_line(
            draw,
            (head_cx - eye_dx - 14 * scale, eye_y - 18 * scale),
            (head_cx - eye_dx + 8 * scale, eye_y - 8 * scale),
            seed=frame_seed + 58,
            width=5,
        )
        _draw_sketch_line(
            draw,
            (head_cx + eye_dx + 14 * scale, eye_y - 18 * scale),
            (head_cx + eye_dx - 8 * scale, eye_y - 8 * scale),
            seed=frame_seed + 59,
            width=5,
        )
        draw.arc(
            [head_cx - 22 * scale, head_cy + 8 * scale, head_cx + 28 * scale, head_cy + 38 * scale],
            start=10,
            end=145,
            fill=INK_BLACK,
            width=5,
        )
    else:
        draw.ellipse([head_cx - eye_dx - 6, eye_y - 6, head_cx - eye_dx + 6, eye_y + 6], fill=INK_BLACK)
        draw.ellipse([head_cx + eye_dx - 6, eye_y - 6, head_cx + eye_dx + 6, eye_y + 6], fill=INK_BLACK)
        draw.arc(
            [head_cx - 24 * scale, head_cy + 10 * scale, head_cx + 24 * scale, head_cy + 38 * scale],
            start=15,
            end=165,
            fill=INK_BLACK,
            width=5,
        )


def render_doodle_frame(
    sc: DoodleSceneConfig,
    frame_idx: int,
    total_frames: int,
    width: int = 1080,
    height: int = 1920,
) -> Image.Image:
    """Render 1 khung hình 2D Doodle với hiệu ứng Line-Boil và Camera Motion vật lý."""
    canvas_w, canvas_h = int(width * 1.18), int(height * 1.18)
    img = Image.new("RGB", (canvas_w, canvas_h), PAPER_BG)
    draw = ImageDraw.Draw(img)

    phase = frame_idx / max(1, total_frames)
    boil_seed = sc.scene_id * 1000 + (frame_idx // 2)

    for y_line in range(140, canvas_h - 100, 76):
        draw.line([(60, y_line), (canvas_w - 60, y_line)], fill=PAPER_GRAIN, width=2)

    cx, cy = canvas_w * 0.5, canvas_h * 0.54
    font_h1 = _get_font(56, bold=True)
    font_badge = _get_font(40, bold=True)
    font_sub = _get_font(42, bold=True)

    # Top Tag Pill + Headline
    tag_box = [110, 135, canvas_w - 110, 235]
    draw.rounded_rectangle(tag_box, radius=24, fill=sc.accent_rgb, outline=INK_BLACK, width=6)
    draw.text((canvas_w // 2, 185), f"{sc.character_tag}  •  {sc.camera_motion.upper()}", fill=(255, 255, 255), font=font_badge, anchor="mm")
    draw.text((canvas_w // 2, 305), sc.headline, fill=INK_BLACK, font=font_h1, anchor="mm")

    # Ground line
    _draw_sketch_line(draw, (110, cy + 220), (canvas_w - 110, cy + 220), seed=boil_seed + 1, width=8)

    # 8 Visual Metaphor Prop Archetypes
    if sc.prop_type == "alarm":
        _draw_doodle_character(draw, cx - 130, cy, 1.15, sc.accent_rgb, "exhausted", phase, boil_seed)
        shake_x = math.sin(frame_idx * 1.9) * 14
        clock_cx, clock_cy = cx + 210 + shake_x, cy - 110
        _draw_sketch_circle(draw, clock_cx, clock_cy, 95, seed=boil_seed + 7, fill=ALERT_RED, width=8)
        _draw_sketch_circle(draw, clock_cx, clock_cy, 72, seed=boil_seed + 8, fill=(255, 255, 255), width=5)
        _draw_sketch_line(draw, (clock_cx, clock_cy), (clock_cx, clock_cy - 48), seed=boil_seed + 9, width=6)
        _draw_sketch_line(draw, (clock_cx, clock_cy), (clock_cx + 38, clock_cy + 12), seed=boil_seed + 10, width=6)
        for ang_deg in (-55, -25, 25, 55):
            rad = math.radians(ang_deg)
            _draw_sketch_line(
                draw,
                (clock_cx + 110 * math.sin(rad), clock_cy - 110 * math.cos(rad)),
                (clock_cx + 145 * math.sin(rad), clock_cy - 145 * math.cos(rad)),
                seed=boil_seed + ang_deg,
                color=ALERT_RED,
                width=6,
            )

    elif sc.prop_type == "block":
        _draw_doodle_character(draw, cx, cy + 35, 1.08, sc.accent_rgb, "crushed", phase, boil_seed, hunched=True)
        drop_y = min(0.0, -140.0 + phase * 260.0)
        bx1, by1, bx2, by2 = cx - 260, cy - 390 + drop_y, cx + 260, cy - 175 + drop_y
        pts = _wobble_pts([(bx1, by1), (bx2, by1), (bx2, by2), (bx1, by2)], seed=boil_seed + 15, amp=3.0)
        draw.polygon(pts, fill=(85, 92, 104))
        draw.line(pts + [pts[0]], fill=INK_BLACK, width=8)
        draw.text((cx, (by1 + by2) / 2), sc.sub_badge or "HEAVY BURDEN", fill=(255, 255, 255), font=font_badge, anchor="mm")

    elif sc.prop_type == "spear_nature":
        _draw_doodle_character(draw, cx - 90, cy, 1.15, sc.accent_rgb, "calm", phase, boil_seed)
        sx = cx + 70
        _draw_sketch_line(draw, (sx, cy - 260), (sx, cy + 215), seed=boil_seed + 30, color=(120, 72, 32), width=9)
        draw.polygon([(sx, cy - 305), (sx - 22, cy - 255), (sx + 22, cy - 255)], fill=GOLD_ACCENT, outline=INK_BLACK)
        _draw_sketch_circle(draw, cx + 230, cy - 290, 68, seed=boil_seed + 33, fill=GOLD_ACCENT, width=6)

    elif sc.prop_type == "split":
        _draw_sketch_line(draw, (cx, cy - 340), (cx, cy + 220), seed=boil_seed + 40, color=ALERT_RED, width=8)
        _draw_doodle_character(draw, cx - 210, cy + 15, 0.92, TAG_COLOR_RGB["@MODERNYOU"], "exhausted", phase, boil_seed)
        _draw_doodle_character(draw, cx + 210, cy + 15, 0.92, TAG_COLOR_RGB["@ANCESTOR"], "calm", phase, boil_seed + 50)
        draw.rounded_rectangle([cx - 340, cy - 320, cx - 70, cy - 235], radius=16, fill=ALERT_RED, outline=INK_BLACK, width=5)
        draw.text((cx - 205, cy - 277), "CHAOS / TRAP", fill=(255, 255, 255), font=font_badge, anchor="mm")
        draw.rounded_rectangle([cx + 70, cy - 320, cx + 340, cy - 235], radius=16, fill=GREEN_CALM, outline=INK_BLACK, width=5)
        draw.text((cx + 205, cy - 277), "CLARITY", fill=(255, 255, 255), font=font_badge, anchor="mm")

    elif sc.prop_type == "puppet_strings":
        # Dark Psychology: Manipulator control strings over Pleaser
        _draw_doodle_character(draw, cx - 150, cy + 25, 0.98, TAG_COLOR_RGB["@PLEASER"], "exhausted", phase, boil_seed, hunched=True)
        _draw_doodle_character(draw, cx + 190, cy - 40, 1.08, TAG_COLOR_RGB["@MANIPULATOR"], "smirk", phase, boil_seed + 60)
        # Puppet cross-bar & strings
        bar_y = cy - 330 + math.sin(phase * 2 * math.pi) * 12
        _draw_sketch_line(draw, (cx - 240, bar_y), (cx - 60, bar_y), seed=boil_seed + 61, color=ALERT_RED, width=9)
        for sx_off in (-210, -150, -90):
            _draw_sketch_line(draw, (cx + sx_off, bar_y), (cx + sx_off, cy - 90), seed=boil_seed + sx_off, color=ALERT_RED, width=4)

    elif sc.prop_type == "brain_battery":
        # Brain Hacks / Dopamine Detox: character + depleted battery gauge
        _draw_doodle_character(draw, cx - 140, cy + 10, 1.08, sc.accent_rgb, "exhausted", phase, boil_seed)
        bat_x1, bat_y1, bat_x2, bat_y2 = cx + 80, cy - 250, cx + 310, cy + 120
        draw.rounded_rectangle([bat_x1, bat_y1, bat_x2, bat_y2], radius=22, fill=(255, 255, 255), outline=INK_BLACK, width=8)
        draw.rectangle([cx + 145, bat_y1 - 28, cx + 245, bat_y1], fill=INK_BLACK)
        fill_ratio = max(0.12, 0.65 - 0.50 * phase)
        fill_top = bat_y2 - (bat_y2 - bat_y1 - 24) * fill_ratio
        bar_color = ALERT_RED if fill_ratio < 0.30 else GOLD_ACCENT
        draw.rounded_rectangle([bat_x1 + 14, fill_top, bat_x2 - 14, bat_y2 - 14], radius=12, fill=bar_color)
        draw.text(((bat_x1 + bat_x2) / 2, (bat_y1 + bat_y2) / 2), f"{int(fill_ratio * 100)}%", fill=INK_BLACK, font=font_h1, anchor="mm")

    elif sc.prop_type == "money_scale":
        # Personal Finance / Wealth Trap balance scale
        _draw_doodle_character(draw, cx - 190, cy + 20, 1.0, sc.accent_rgb, "shocked", phase, boil_seed)
        pivot_x, pivot_y = cx + 140, cy - 60
        _draw_sketch_line(draw, (pivot_x, pivot_y), (pivot_x, cy + 215), seed=boil_seed + 71, width=9)
        tilt = 35 * math.sin(phase * math.pi)
        _draw_sketch_line(draw, (pivot_x - 150, pivot_y - tilt), (pivot_x + 150, pivot_y + tilt), seed=boil_seed + 72, width=8)
        _draw_sketch_circle(draw, pivot_x - 150, pivot_y - tilt + 45, 48, seed=boil_seed + 73, fill=GOLD_ACCENT, width=6)
        draw.text((pivot_x - 150, pivot_y - tilt + 45), "$", fill=INK_BLACK, font=font_h1, anchor="mm")
        _draw_sketch_circle(draw, pivot_x + 150, pivot_y + tilt + 55, 62, seed=boil_seed + 74, fill=ALERT_RED, width=6)
        draw.text((pivot_x + 150, pivot_y + tilt + 55), "DEBT", fill=(255, 255, 255), font=font_badge, anchor="mm")

    else:
        # Climax / Realization
        _draw_doodle_character(draw, cx, cy + 20, 1.15, sc.accent_rgb, "calm", phase, boil_seed)
        bulb_y = cy - 335 + math.sin(phase * 2 * math.pi) * 12
        _draw_sketch_circle(draw, cx, bulb_y, 58, seed=boil_seed + 80, fill=GOLD_ACCENT, width=7)
        draw.text((cx, bulb_y), "!", fill=INK_BLACK, font=font_h1, anchor="mm")

    # Stamp Badge
    if sc.sub_badge:
        stamp_box = [canvas_w // 2 - 270, cy + 265, canvas_w // 2 + 270, cy + 355]
        draw.rounded_rectangle(stamp_box, radius=20, fill=GOLD_ACCENT, outline=INK_BLACK, width=6)
        draw.text((canvas_w // 2, cy + 310), sc.sub_badge, fill=INK_BLACK, font=font_badge, anchor="mm")

    # Subtitle Card
    words = (sc.narration or "").split()
    lines: List[str] = []
    cur: List[str] = []
    for w in words:
        cur.append(w)
        if len(" ".join(cur)) > 34:
            lines.append(" ".join(cur))
            cur = []
    if cur:
        lines.append(" ".join(cur))
    caption_text = "\n".join(lines[:3])
    cap_box = [85, canvas_h - 420, canvas_w - 85, canvas_h - 170]
    draw.rounded_rectangle(cap_box, radius=28, fill=(24, 24, 24), outline=GOLD_ACCENT, width=5)
    draw.multiline_text((canvas_w // 2, canvas_h - 295), caption_text, fill=(255, 255, 255), font=font_sub, anchor="mm", align="center", spacing=10)

    # Apply Physical 7-Layer Camera Motion Crop
    cam = sc.camera_motion
    if cam in ("dolly_in", "zoom_in", "hyper_zoom"):
        zoom = 1.0 + 0.14 * phase
    elif cam in ("dolly_out", "zoom_out"):
        zoom = 1.14 - 0.12 * phase
    elif cam == "dolly_zoom":
        zoom = 1.04 + 0.09 * math.sin(phase * math.pi)
    elif cam == "overhead_90":
        zoom = 1.08
    else:
        zoom = 1.05 + 0.06 * phase

    crop_w = int(width / zoom)
    crop_h = int(height / zoom)
    pan_x = int(math.sin(phase * 2 * math.pi) * 35) if cam == "arc_orbit" else (int((phase - 0.5) * 60) if cam in ("pan_left", "pan_right") else 0)
    pan_y = int((0.5 - phase) * 55) if cam in ("worms_eye", "overhead_90") else 0

    left = max(0, min(canvas_w - crop_w, (canvas_w - crop_w) // 2 + pan_x))
    top = max(0, min(canvas_h - crop_h, (canvas_h - crop_h) // 2 + pan_y))
    cropped = img.crop((left, top, left + crop_w, top + crop_h))
    return cropped.resize((width, height), Image.Resampling.LANCZOS)


def synthesize_doodle_sfx(path: str, kind: str = "whoosh", duration_s: float = 0.45) -> None:
    """Sinh âm thanh SFX thủ tục (alarm, thud, whoosh) cho hoạt họa 2D Doodle."""
    sr = 24000
    n = int(sr * duration_s)
    t = np.linspace(0, duration_s, n, endpoint=False)
    if kind == "alarm":
        samples = 0.25 * np.sin(2 * np.pi * 880 * t) * (np.sin(2 * np.pi * 12 * t) > 0)
    elif kind == "thud":
        freq = np.linspace(130, 38, n)
        samples = 0.45 * np.sin(2 * np.pi * freq * t) * np.exp(-6.5 * t)
    else:
        samples = 0.18 * np.sin(2 * np.pi * (220 + 400 * t) * t) * np.exp(-3.0 * t)

    pcm = np.clip(samples * 32767, -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


class DoodleAnimationTool:
    """
    Agent-Callable Tool đóng gói toàn bộ quy trình nhận diện, chọn đạo cụ ẩn dụ và dựng hoạt họa 2D Doodle 24 FPS.
    """

    TOOL_NAME: str = "doodle_2d_animator"
    SCHEMA: Dict[str, Any] = DOODLE_ANIMATION_TOOL_SCHEMA

    @classmethod
    def get_tool_specification(cls) -> Dict[str, Any]:
        """Trả về đặc tả JSON Schema + Hướng dẫn sử dụng để tiêm vào System Prompt của Agent."""
        return dict(cls.SCHEMA)

    @classmethod
    def should_activate(
        cls,
        topic: str = "",
        narration: str = "",
        niche: str = "",
        visual_prompt: str = "",
        reality_class: str = "",
    ) -> Tuple[bool, str]:
        """
        Kiểm tra xem phân cảnh hoặc chủ đề hiện tại có phù hợp để gọi `doodle_2d_animator` hay không.
        Trả về: (should_use, rationale).
        """
        niche_clean = (niche or "").strip().lower()
        if niche_clean in EXCLUDED_REAL_FOOTAGE_NICHES:
            return False, f"Ngách '{niche_clean}' yêu cầu footage quay thật hoặc CGI 3D chân thực."

        combined = f"{topic} {narration} {visual_prompt}".lower()
        if any(tag.lower() in combined for tag in TAG_COLOR_RGB.keys()):
            return True, "Phát hiện thẻ nhân vật @CHARACTER_TAG chuẩn 2D Doodle trong kịch bản/prompt."

        if any(k in combined for k in ("2d doodle", "sketchbook animation", "stickman", "whiteboard animation", "hand-drawn doodle")):
            return True, "Prompt yêu cầu trực tiếp phong cách hoạt họa 2D Doodle / Stickman."

        if niche_clean in SUITABLE_DOODLE_NICHES:
            return True, f"Kênh thuộc ngách '{niche_clean}' tối ưu cho hoạt họa 2D Doodle Explainer."

        if str(reality_class).upper() == "DOODLE_PSYCHOLOGY_EXPLAINER":
            return True, "VisualToolRouter phân loại cảnh là DOODLE_PSYCHOLOGY_EXPLAINER."

        return False, "Không thuộc ngách hoạt họa 2D Doodle mặc định."

    @classmethod
    def auto_select_prop_and_tag(
        cls,
        narration: str,
        visual_prompt: str = "",
        scene_id: int = 1,
        total_scenes: int = 6,
    ) -> Tuple[str, str, str, str, str]:
        """
        Tự động suy luận (character_tag, prop_type, camera_motion, headline, sub_badge) từ lời thoại phân cảnh.
        """
        ctx = f"{narration} {visual_prompt}".lower()

        # 1. Chọn thẻ nhân vật (@TAG)
        tag = "@MODERNYOU"
        for explicit_tag in TAG_COLOR_RGB.keys():
            if explicit_tag.lower() in ctx:
                tag = explicit_tag
                break
        else:
            if any(w in ctx for w in ("manipulat", "narcissist", "sociopath", "toxic", "gaslight", "puppeteer")):
                tag = "@MANIPULATOR"
            elif any(w in ctx for w in ("pleaser", "apologiz", "empath", "anxious", "saying sorry")):
                tag = "@PLEASER"
            elif any(w in ctx for w in ("hunter", "ancestor", "prehistoric", "stoic", "50,000", "ancient")):
                tag = "@ANCESTOR"
            elif any(w in ctx for w in ("farmer", "neolithic", "debt", "loan", "labor", "spine")):
                tag = "@FARMER"

        # 2. Chọn đạo cụ ẩn dụ (prop_type)
        if any(w in ctx for w in ("manipulat", "puppet", "control", "gaslight", "narcissist", "strings")):
            prop = "puppet_strings"
        elif any(w in ctx for w in ("dopamine", "brain", "battery", "sleep", "tired", "cortisol", "exhausted")):
            prop = "brain_battery"
        elif any(w in ctx for w in ("money", "debt", "bank", "inflation", "salary", "tax", "loan", "credit", "$")):
            prop = "money_scale"
        elif any(w in ctx for w in ("alarm", "monday", "morning", "clock", "wake up", "6:00", "hours a week")):
            prop = "alarm"
        elif any(w in ctx for w in ("crushed", "heavy", "burden", "weight", "trap", "shrunk", "inches", "stress")):
            prop = "block"
        elif any(w in ctx for w in ("vs", "compare", "while", "meanwhile", "paradox", "twice as")):
            prop = "split"
        elif any(w in ctx for w in ("hunter", "nature", "free", "spear", "walk", "leisure")):
            prop = "spear_nature"
        elif scene_id >= total_scenes:
            prop = "climax"
        else:
            prop = "alarm" if scene_id == 1 else "block"

        # 3. Chọn Camera Motion theo nhịp kịch bản
        motion_cycle = ["dolly_in", "overhead_90", "arc_orbit", "worms_eye", "dolly_zoom", "dolly_out"]
        cam = motion_cycle[(max(1, scene_id) - 1) % len(motion_cycle)]

        # 4. Trích xuất số liệu làm Sub-Badge
        m_num = re.search(
            r"\b(\d+(?:[.,]\d+)?\s*(?:%|hrs/week|hours/week|hours|hrs|years|days|inches|psi|x|\$))\b",
            narration,
            re.IGNORECASE,
        )
        badge = m_num.group(1).upper() if m_num else f"SCENE {scene_id}/{total_scenes}"

        # 5. Tạo Headline ngắn gọn
        clean_words = [re.sub(r"[^\w$-]", "", w).upper() for w in narration.split() if len(w) > 2]
        headline = " ".join(clean_words[:4]) if clean_words else f"ACT {scene_id}"

        return tag, prop, cam, headline[:28], badge[:22]

    @classmethod
    def render_scene_clip(
        cls,
        narration: str,
        output_path: str,
        duration_seconds: float = 4.5,
        scene_id: int = 1,
        total_scenes: int = 6,
        character_tag: Optional[str] = None,
        prop_type: Optional[str] = None,
        camera_motion: Optional[str] = None,
        headline: Optional[str] = None,
        sub_badge: Optional[str] = None,
        visual_prompt: str = "",
        fps: int = 24,
    ) -> Optional[str]:
        """
        Render trực tiếp 1 video clip MP4 (1080x1920 @ 24 FPS) hoạt họa 2D Doodle cho 1 phân cảnh đơn lẻ.
        Hàm này được gọi trực tiếp bởi `AIVideoAgent` hoặc Pipeline khi tool = `doodle_2d_animator`.
        """
        auto_tag, auto_prop, auto_cam, auto_head, auto_badge = cls.auto_select_prop_and_tag(
            narration=narration,
            visual_prompt=visual_prompt,
            scene_id=scene_id,
            total_scenes=total_scenes,
        )
        sc = DoodleSceneConfig(
            scene_id=scene_id,
            headline=headline or auto_head,
            sub_badge=sub_badge or auto_badge,
            narration=narration,
            character_tag=character_tag or auto_tag,
            prop_type=prop_type or auto_prop,
            camera_motion=camera_motion or auto_cam,
            duration_s=max(2.5, float(duration_seconds)),
        )

        out_p = Path(output_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        work_dir = out_p.parent / f"_doodle_s{scene_id:02d}"
        work_dir.mkdir(parents=True, exist_ok=True)

        n_unique = 14
        frame_files: List[str] = []
        for f_idx in range(n_unique):
            im = render_doodle_frame(sc, f_idx, n_unique)
            fp = str((work_dir / f"f_{f_idx:03d}.jpg").resolve())
            im.save(fp, "JPEG", quality=92)
            frame_files.append(fp)

        step_dur = sc.duration_s / n_unique
        concat_txt = work_dir / "frames_concat.txt"
        lines = [f"file '{Path(p).resolve().as_posix()}'\nduration {step_dur:.3f}" for p in frame_files]
        lines.append(f"file '{Path(frame_files[-1]).resolve().as_posix()}'")
        concat_txt.write_text("\n".join(lines), encoding="utf-8")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_txt),
            "-t", f"{sc.duration_s:.2f}",
            "-r", str(fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            str(out_p),
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

        # Clean up temporary frames
        for fp in frame_files:
            Path(fp).unlink(missing_ok=True)
        concat_txt.unlink(missing_ok=True)
        try:
            work_dir.rmdir()
        except Exception:
            pass

        if out_p.exists() and out_p.stat().st_size > 4000:
            logger.info(
                f"🎨 [DoodleAnimationTool] ✅ Đã dựng clip 2D Doodle Cảnh {scene_id} "
                f"({sc.character_tag}, prop={sc.prop_type}, cam={sc.camera_motion}) -> {output_path}"
            )
            return str(out_p)
        return None
