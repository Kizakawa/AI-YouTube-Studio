"""
app.services.media.doodle_animation_tool — Công cụ Hoạt Họa 2D Doodle / Sketchbook 30 FPS Siêu Mượt Cho Agent (Agent-Callable Tool).

Cải tiến Kỹ thuật Chống Khựng Hình (Zero-Judder 30 FPS Continuous Physics Engine):
  1. True 30 FPS Unique Frame Rendering (150-180 khung hình độc lập cho mỗi cảnh 5-6s, thay vì 14 khung hình tĩnh).
  2. Sub-Pixel Floating-Point Camera Glide (`Image.Transform.EXTENT` + `BILINEAR` + `ease_in_out_cubic` + Whip-Pan Entry):
     Triệt tiêu hoàn toàn hiện tượng giật cục từng pixel (integer crop snapping) khi zoom/pan.
  3. Coherent Organic Line-Boil & Articulated 2-Joint Kinematics:
     Chuyển động tay/chân 2 khớp mượt mà, nhịp thở lồng ngực, chớp mắt tự nhiên (chu kỳ 2.7s),
     vật lý lò xo (spring overshoot) cho đạo cụ và sóng hình sin liên tục (không nhảy ngẫu nhiên).
  4. Live Word-by-Word Karaoke Subtitle Highlighting:
     Phụ đề chạy sáng từng từ theo thời gian thực 30 FPS khớp với tiến độ giọng đọc.
  5. Direct RawVideo Memory Pipe (`ffmpeg -f rawvideo -pix_fmt rgb24`):
     Truyền thẳng khung hình từ RAM qua ống dẫn vào FFmpeg (0 file JPEG tạm trên ổ cứng).
"""
from __future__ import annotations

import logging
import math
import re
import subprocess
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)
_doodle_render_sem = threading.BoundedSemaphore(2)

# ── BẢNG MÀU & THÔNG SỐ CHUẨN 2D DOODLE STUDIO (30 FPS) ──────────────────────
DEFAULT_FPS = 30
PAPER_BG = (247, 243, 232)        # #F7F3E8 Warm Cream Sketchbook Paper
PAPER_GRAIN = (235, 229, 214)     # Subtle notebook horizontal rule
INK_BLACK = (24, 24, 24)          # Bold black marker outline
SHADOW_GREY = (212, 206, 192)     # Soft drop shadow on paper

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

SUITABLE_DOODLE_NICHES: Tuple[str, ...] = ()

EXCLUDED_REAL_FOOTAGE_NICHES: Tuple[str, ...] = (
    "wildlife_predators",
    "animal_rescue",
    "deep_sea_mysteries",
    "deep_sea_earth_mysteries",
    "nature",
)

DOODLE_ANIMATION_TOOL_SCHEMA: Dict[str, Any] = {
    "name": "doodle_2d_animator",
    "display_name": "GOHA 2D Doodle & Stickman Explainer Studio (30 FPS Sub-Pixel Smooth)",
    "version": "2.5",
    "description": (
        "Công cụ dựng hoạt họa 2D Doodle / Stickman Explainer chuyên nghiệp (1080x1920 @ 30 FPS Sub-Pixel Smooth) "
        "trên nền giấy kem ấm (#F7F3E8) với chuyển động khớp tay chân 2 bậc, vật lý lò xo mượt mà, phụ đề Karaoke "
        "từng từ, khóa đồng nhất nhân vật bằng thẻ @CHARACTER_TAG (@MODERNYOU, @PLEASER, @MANIPULATOR, @ANCESTOR, @FARMER), "
        "tích hợp 20 chuyển động máy quay điện ảnh EyeCandy (dolly_in, overhead_90, arc_orbit, worms_eye, dolly_zoom, dolly_out) "
        "và 8 đạo cụ ẩn dụ trực quan. "
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
                "description": "Đạo cụ ẩn dụ hình ảnh trực quan cho phân cảnh.",
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
                "description": "Tiêu đề ngắn (2-4 từ IN HOA) vẽ ở đầu trang giấy.",
            },
            "sub_badge": {
                "type": "string",
                "description": "Nhãn số liệu đóng dấu lên khung hình (VD: '15 HRS/WEEK', '-400% DOPAMINE').",
            },
        },
        "required": ["narration"],
    },
}

# ── BỘ NHỚ ĐỆM FONT & TRANG GIẤY NỀN (ZERO DISK I/O KHI RENDER 30 FPS) ───────
_FONT_CACHE: Dict[Tuple[int, bool], ImageFont.FreeTypeFont] = {}
_PAPER_CANVAS_CACHE: Dict[Tuple[int, int], Image.Image] = {}


def _get_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                f = ImageFont.truetype(path, size)
                _FONT_CACHE[key] = f
                return f
            except Exception:
                pass
    f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def _get_base_paper_canvas(canvas_w: int, canvas_h: int) -> Image.Image:
    key = (canvas_w, canvas_h)
    if key not in _PAPER_CANVAS_CACHE:
        base = Image.new("RGB", (canvas_w, canvas_h), PAPER_BG)
        draw = ImageDraw.Draw(base)
        for y_line in range(140, canvas_h - 100, 76):
            draw.line([(60, y_line), (canvas_w - 60, y_line)], fill=PAPER_GRAIN, width=2)
        _PAPER_CANVAS_CACHE[key] = base
    return _PAPER_CANVAS_CACHE[key].copy()


# ── HÀM NỘI SUY VẬT LÝ MƯỢT (EASING & SPRING FUNCTIONS) ─────────────────────
def _ease_in_out_cubic(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 4.0 * x * x * x if x < 0.5 else 1.0 - ((-2.0 * x + 2.0) ** 3) / 2.0


def _ease_out_back(x: float, overshoot: float = 1.35) -> float:
    x = max(0.0, min(1.0, x))
    c3 = overshoot + 1.0
    return 1.0 + c3 * ((x - 1.0) ** 3) + overshoot * ((x - 1.0) ** 2)


def _spring_drop(t_sec: float, impact_time: float = 0.55) -> Tuple[float, float]:
    """
    Trả về (drop_offset_px, squash_factor) cho vật nặng rơi xuống có độ nảy đàn hồi mượt mà.
    """
    if t_sec < impact_time:
        p = t_sec / max(0.01, impact_time)
        # Gia tốc trọng trường mượt (p^2)
        return -240.0 * (1.0 - p * p), 0.0
    dt = t_sec - impact_time
    # Dao động tắt dần sau va chạm
    bounce = -28.0 * math.exp(-7.5 * dt) * abs(math.sin(dt * 14.0))
    squash = math.exp(-5.5 * dt) * math.sin(dt * 16.0)
    return bounce, squash


def _coherent_wobble_pts(
    pts: List[Tuple[float, float]],
    t_sec: float,
    seed: int,
    amp: float = 1.45,
) -> List[Tuple[float, float]]:
    """
    Tạo hiệu ứng Line-Boil vẽ tay MƯỢT MÀ bằng sóng sin liên tục theo thời gian `t_sec`
    (thay vì nhảy ngẫu nhiên gây giật mắt).
    """
    out: List[Tuple[float, float]] = []
    # Làm tròn t_sec theo nhịp 12Hz êm ái nhưng kết hợp pha liên tục để không bị giật cục
    boil_phase = t_sec * 8.5
    for idx, (x, y) in enumerate(pts):
        ang = boil_phase + seed * 0.73 + idx * 1.91
        dx = math.sin(ang) * amp
        dy = math.cos(ang * 1.13 + 0.5) * amp
        out.append((x + dx, y + dy))
    return out


def _draw_sketch_line(
    draw: ImageDraw.ImageDraw,
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    t_sec: float,
    seed: int,
    color: Tuple[int, int, int] = INK_BLACK,
    width: int = 7,
) -> None:
    steps = 8
    pts = [
        (p1[0] + (p2[0] - p1[0]) * i / steps, p1[1] + (p2[1] - p1[1]) * i / steps)
        for i in range(steps + 1)
    ]
    w_pts = _coherent_wobble_pts(pts, t_sec=t_sec, seed=seed, amp=1.35)
    draw.line(w_pts, fill=color, width=width, joint="curve")


def _draw_sketch_circle(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    r: float,
    t_sec: float,
    seed: int,
    fill: Optional[Tuple[int, int, int]] = None,
    outline: Tuple[int, int, int] = INK_BLACK,
    width: int = 7,
) -> None:
    n = 32
    boil_phase = t_sec * 8.0 + seed * 0.5
    pts: List[Tuple[float, float]] = []
    for i in range(n + 1):
        ang = (2.0 * math.pi * i) / n
        dr = math.sin(boil_phase + ang * 3.0) * 1.4
        pts.append((cx + (r + dr) * math.cos(ang), cy + (r + dr) * math.sin(ang)))
    if fill:
        draw.polygon(pts, fill=fill)
    draw.line(pts, fill=outline, width=width, joint="curve")


@dataclass
class DoodleSceneConfig:
    """Cấu hình 1 phân cảnh hoạt họa 2D Doodle 30 FPS."""
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
        raw_p = (self.prop_type or "").strip().lower()
        valid_props = (
            "alarm",
            "block",
            "spear_nature",
            "split",
            "puppet_strings",
            "brain_battery",
            "money_scale",
            "mask",
            "magnifying_glass",
            "climax",
        )
        if raw_p in valid_props:
            self.prop_type = raw_p
        else:
            ctx_p = f"{raw_p} {self.narration}".lower()
            if any(k in ctx_p for k in ("puppet", "manipulat", "control", "string", "toxic")):
                self.prop_type = "puppet_strings"
            elif any(k in ctx_p for k in ("neural", "brain", "lobe", "cortex", "amygdala", "dopamine", "cortisol", "battery", "fuel")):
                self.prop_type = "brain_battery"
            elif any(k in ctx_p for k in ("clock", "timer", "alarm", "second", "pause", "time")):
                self.prop_type = "alarm"
            elif any(k in ctx_p for k in ("scale", "money", "debt", "power", "balance", "status")):
                self.prop_type = "money_scale"
            elif any(k in ctx_p for k in ("split", "conflict", "attack", "defense", "dissonance", "vs")):
                self.prop_type = "split"
            elif any(k in ctx_p for k in ("spear", "nature", "gaze", "calm", "stoic", "boundary", "holding")):
                self.prop_type = "spear_nature"
            elif any(k in ctx_p for k in ("block", "trap", "shield", "spotlight", "weight", "burden", "stress")):
                self.prop_type = "block"
            else:
                self.prop_type = valid_props[max(0, self.scene_id) % len(valid_props)]
        if self.sub_badge and self.sub_badge.upper().startswith("SCENE "):
            self.sub_badge = ""
        if not self.raw_prompt:
            self.raw_prompt = (
                f"{self.character_tag} in 2D hand-drawn doodle cartoon style on warm cream paper, "
                f"visual metaphor '{self.prop_type}', badge '{self.sub_badge}', expression {self.expression}"
            )


def _draw_doodle_character(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    scale: float,
    shirt_rgb: Tuple[int, int, int],
    expression: str,
    t_sec: float,
    seed: int,
    hunched: bool = False,
    squash: float = 0.0,
) -> None:
    """
    Vẽ nhân vật 2D Doodle với chuyển động khớp tay/chân 2 đoạn (2-Joint Articulated Limbs),
    nhịp thở lồng ngực mượt, nghiêng đầu tự nhiên và chớp mắt định kỳ.
    """
    head_r = 68.0 * scale
    breath = math.sin(t_sec * 3.6) * (7.5 * scale)
    squash_y = squash * 22.0 * scale
    squash_x = squash * 12.0 * scale

    head_cy = cy - 172.0 * scale + breath + (30.0 * scale if hunched else 0.0) + squash_y
    head_cx = cx + math.sin(t_sec * 2.2) * (5.0 * scale) + (16.0 * scale if hunched else 0.0)

    # Bóng đổ dưới chân co giãn nhẹ theo nhịp thở
    shadow_rx = (96.0 + math.sin(t_sec * 3.6) * 5.0) * scale
    draw.ellipse(
        [cx - shadow_rx, cy + 196.0 * scale, cx + shadow_rx, cy + 230.0 * scale],
        fill=SHADOW_GREY,
    )

    # Thân áo (Torso) có nhịp thở mượt
    torso_top = head_cy + head_r * 0.84
    torso_bot = cy + 64.0 * scale + squash_y * 0.5
    torso_pts = [
        (cx - (58.0 * scale + squash_x), torso_top),
        (cx + (58.0 * scale + squash_x), torso_top),
        (cx + (72.0 * scale + squash_x), torso_bot),
        (cx - (72.0 * scale + squash_x), torso_bot),
    ]
    w_torso = _coherent_wobble_pts(torso_pts, t_sec=t_sec, seed=seed + 11, amp=1.4)
    draw.polygon(w_torso, fill=shirt_rgb)
    draw.line(w_torso + [w_torso[0]], fill=INK_BLACK, width=max(4, int(7 * scale)), joint="curve")

    # Chân 2 khớp (Đùi -> Đầu gối -> Bàn chân)
    knee_bend = abs(math.sin(t_sec * 3.2)) * 14.0 * scale + (18.0 * scale if hunched else 0.0)
    l_hip = (cx - 32.0 * scale, torso_bot)
    l_knee = (cx - 42.0 * scale - knee_bend * 0.5, cy + 135.0 * scale)
    l_foot = (cx - 40.0 * scale, cy + 210.0 * scale)
    _draw_sketch_line(draw, l_hip, l_knee, t_sec, seed + 21, width=max(4, int(8 * scale)))
    _draw_sketch_line(draw, l_knee, l_foot, t_sec, seed + 22, width=max(4, int(8 * scale)))

    r_hip = (cx + 32.0 * scale, torso_bot)
    r_knee = (cx + 42.0 * scale + knee_bend * 0.5, cy + 135.0 * scale)
    r_foot = (cx + 40.0 * scale, cy + 210.0 * scale)
    _draw_sketch_line(draw, r_hip, r_knee, t_sec, seed + 23, width=max(4, int(8 * scale)))
    _draw_sketch_line(draw, r_knee, r_foot, t_sec, seed + 24, width=max(4, int(8 * scale)))

    # Tay 2 khớp (Vai -> Khuỷu tay -> Bàn tay)
    arm_osc = math.sin(t_sec * 4.5) * 20.0 * scale
    l_sh = (cx - 56.0 * scale, torso_top + 18.0 * scale)
    r_sh = (cx + 56.0 * scale, torso_top + 18.0 * scale)

    if expression in ("exhausted", "crushed", "shocked"):
        # Đưa 2 tay lên ôm đầu / đỡ vật nặng
        l_elb = (cx - 102.0 * scale, torso_top - 12.0 * scale + arm_osc * 0.35)
        l_hnd = (head_cx - 64.0 * scale, head_cy - 18.0 * scale + arm_osc * 0.5)
        r_elb = (cx + 102.0 * scale, torso_top - 12.0 * scale - arm_osc * 0.35)
        r_hnd = (head_cx + 64.0 * scale, head_cy - 18.0 * scale - arm_osc * 0.5)
    else:
        l_elb = (cx - 92.0 * scale, torso_top + 52.0 * scale + arm_osc * 0.5)
        l_hnd = (cx - 110.0 * scale, torso_top + 92.0 * scale + arm_osc)
        r_elb = (cx + 92.0 * scale, torso_top + 42.0 * scale - arm_osc * 0.5)
        r_hnd = (cx + 115.0 * scale, torso_top + 28.0 * scale - arm_osc)

    _draw_sketch_line(draw, l_sh, l_elb, t_sec, seed + 31, width=max(4, int(7 * scale)))
    _draw_sketch_line(draw, l_elb, l_hnd, t_sec, seed + 32, width=max(4, int(7 * scale)))
    _draw_sketch_line(draw, r_sh, r_elb, t_sec, seed + 33, width=max(4, int(7 * scale)))
    _draw_sketch_line(draw, r_elb, r_hnd, t_sec, seed + 34, width=max(4, int(7 * scale)))

    # Đầu nhân vật
    _draw_sketch_circle(
        draw,
        head_cx,
        head_cy,
        head_r,
        t_sec=t_sec,
        seed=seed + 41,
        fill=(255, 253, 248),
        outline=INK_BLACK,
        width=max(4, int(7 * scale)),
    )

    # Chớp mắt tự nhiên (mỗi 2.6 giây chớp 1 lần trong 0.14s)
    blink_cycle = (t_sec + seed * 0.17) % 2.6
    is_blinking = blink_cycle > 2.46

    eye_y = head_cy - 12.0 * scale
    eye_dx = 24.0 * scale
    pupil_shift = math.sin(t_sec * 2.5) * 3.5 * scale

    if is_blinking:
        _draw_sketch_line(
            draw,
            (head_cx - eye_dx - 12.0 * scale, eye_y),
            (head_cx - eye_dx + 12.0 * scale, eye_y),
            t_sec,
            seed + 50,
            width=4,
        )
        _draw_sketch_line(
            draw,
            (head_cx + eye_dx - 12.0 * scale, eye_y),
            (head_cx + eye_dx + 12.0 * scale, eye_y),
            t_sec,
            seed + 51,
            width=4,
        )
    elif expression in ("exhausted", "crushed", "shocked"):
        _draw_sketch_circle(draw, head_cx - eye_dx, eye_y, 14.0 * scale, t_sec, seed + 51, fill=(255, 255, 255), width=4)
        _draw_sketch_circle(draw, head_cx + eye_dx, eye_y, 14.0 * scale, t_sec, seed + 52, fill=(255, 255, 255), width=4)
        draw.ellipse(
            [head_cx - eye_dx - 4 + pupil_shift, eye_y - 4, head_cx - eye_dx + 4 + pupil_shift, eye_y + 4],
            fill=INK_BLACK,
        )
        draw.ellipse(
            [head_cx + eye_dx - 4 + pupil_shift, eye_y - 4, head_cx + eye_dx + 4 + pupil_shift, eye_y + 4],
            fill=INK_BLACK,
        )
        # Quầng thâm mắt (eye bags)
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
        # Giọt mồ hôi rơi mượt theo t_sec
        sweat_p = (t_sec * 1.4) % 1.0
        sweat_y = head_cy - 28.0 * scale + sweat_p * 42.0 * scale
        draw.ellipse(
            [head_cx + head_r + 8 * scale, sweat_y, head_cx + head_r + 18 * scale, sweat_y + 16 * scale],
            fill=(74, 144, 226),
            outline=INK_BLACK,
            width=2,
        )
    else:
        draw.ellipse([head_cx - eye_dx - 6 + pupil_shift, eye_y - 6, head_cx - eye_dx + 6 + pupil_shift, eye_y + 6], fill=INK_BLACK)
        draw.ellipse([head_cx + eye_dx - 6 + pupil_shift, eye_y - 6, head_cx + eye_dx + 6 + pupil_shift, eye_y + 6], fill=INK_BLACK)

    # Miệng & Lông mày biểu cảm
    if expression in ("exhausted", "crushed", "shocked"):
        draw.arc(
            [head_cx - 26 * scale, head_cy + 18 * scale, head_cx + 26 * scale, head_cy + 48 * scale],
            start=195,
            end=345,
            fill=INK_BLACK,
            width=5,
        )
    elif expression == "smirk":
        _draw_sketch_line(
            draw,
            (head_cx - eye_dx - 14 * scale, eye_y - 18 * scale),
            (head_cx - eye_dx + 8 * scale, eye_y - 8 * scale),
            t_sec,
            seed + 58,
            width=5,
        )
        _draw_sketch_line(
            draw,
            (head_cx + eye_dx + 14 * scale, eye_y - 18 * scale),
            (head_cx + eye_dx - 8 * scale, eye_y - 8 * scale),
            t_sec,
            seed + 59,
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
        draw.arc(
            [head_cx - 24 * scale, head_cy + 10 * scale, head_cx + 24 * scale, head_cy + 38 * scale],
            start=15,
            end=165,
            fill=INK_BLACK,
            width=5,
        )


def _draw_karaoke_caption_box(
    draw: ImageDraw.ImageDraw,
    narration: str,
    phase: float,
    canvas_w: int,
    canvas_h: int,
) -> None:
    """
    Vẽ hộp phụ đề Karaoke tô sáng từng từ theo tiến độ thời gian thực `phase` (0.0 -> 1.0) ở 30 FPS.
    """
    font_sub = _get_font(42, bold=True)
    words = [w for w in (narration or "").split() if w.strip()]
    if not words:
        return

    active_idx = min(len(words) - 1, int(phase * len(words)))

    # Chia thành tối đa 3 dòng cân đối
    lines_words: List[List[Tuple[int, str]]] = []
    cur_line: List[Tuple[int, str]] = []
    cur_len = 0
    for idx, w in enumerate(words):
        cur_line.append((idx, w))
        cur_len += len(w) + 1
        if cur_len > 30 and len(lines_words) < 2:
            lines_words.append(cur_line)
            cur_line = []
            cur_len = 0
    if cur_line:
        lines_words.append(cur_line)

    cap_box = [80, canvas_h - 425, canvas_w - 80, canvas_h - 165]
    draw.rounded_rectangle(cap_box, radius=28, fill=(24, 24, 24), outline=GOLD_ACCENT, width=5)

    total_lines = len(lines_words)
    start_y = (canvas_h - 295) - ((total_lines - 1) * 56) // 2

    for line_i, line_items in enumerate(lines_words):
        line_str = " ".join(w for _, w in line_items)
        bbox = draw.textbbox((0, 0), line_str, font=font_sub)
        line_w = bbox[2] - bbox[0]
        cursor_x = (canvas_w - line_w) / 2.0
        y_pos = start_y + line_i * 56

        for w_idx, word_text in line_items:
            w_bbox = draw.textbbox((0, 0), word_text + " ", font=font_sub)
            w_width = w_bbox[2] - w_bbox[0]
            if w_idx == active_idx:
                # Pill vàng nổi bật dưới từ đang đọc
                word_only_box = draw.textbbox((0, 0), word_text, font=font_sub)
                ww = word_only_box[2] - word_only_box[0]
                draw.rounded_rectangle(
                    [cursor_x - 6, y_pos - 26, cursor_x + ww + 6, y_pos + 26],
                    radius=10,
                    fill=GOLD_ACCENT,
                )
                draw.text((cursor_x, y_pos), word_text, fill=INK_BLACK, font=font_sub, anchor="lm")
            elif w_idx < active_idx:
                draw.text((cursor_x, y_pos), word_text, fill=(255, 255, 255), font=font_sub, anchor="lm")
            else:
                draw.text((cursor_x, y_pos), word_text, fill=(175, 175, 175), font=font_sub, anchor="lm")
            cursor_x += w_width


def render_doodle_frame(
    sc: DoodleSceneConfig,
    frame_idx: int,
    total_frames: int,
    width: int = 1080,
    height: int = 1920,
    fps: int = DEFAULT_FPS,
) -> Image.Image:
    """
    Render 1 khung hình 2D Doodle ở chuẩn 30 FPS với:
    - Chuyển động Vật lý & Khớp xương liên tục theo `t_sec`
    - Phụ đề Karaoke từng từ
    - Sub-Pixel Floating-Point Camera Transform (`Image.Transform.EXTENT` + `BILINEAR`) không bao giờ bị khựng pixel.
    """
    canvas_w, canvas_h = int(width * 1.18), int(height * 1.18)
    img = _get_base_paper_canvas(canvas_w, canvas_h)
    draw = ImageDraw.Draw(img)

    phase = frame_idx / max(1, total_frames - 1)
    t_sec = frame_idx / float(max(1, fps))
    scene_seed = sc.scene_id * 100

    is_landscape = width > height
    cx = canvas_w * 0.5
    cy = canvas_h * (0.57 if is_landscape else 0.53)
    font_h1 = _get_font(44 if is_landscape else 56, bold=True)
    font_badge = _get_font(34 if is_landscape else 40, bold=True)

    # Hiệu ứng Pop-in mượt ở 0.3s đầu cảnh
    entry_p = min(1.0, t_sec / 0.32)
    entry_scale = _ease_out_back(entry_p, overshoot=1.15)
    header_y_off = (1.0 - entry_scale) * -60.0

    # Top Concept Headline Banner (đặt tại y=185..255 cho 16:9 để không bao giờ bị cắt viền trên khi camera zoom 1.14x)
    if is_landscape:
        banner_w = min(canvas_w - 320, max(720, len(sc.headline) * 31 + 120))
        b_x1 = int(cx - banner_w // 2)
        b_x2 = int(cx + banner_w // 2)
        tag_box = [b_x1, int(185 + header_y_off), b_x2, int(255 + header_y_off)]
        draw.rounded_rectangle(tag_box, radius=20, fill=sc.accent_rgb, outline=INK_BLACK, width=6)
        draw.text(
            (canvas_w // 2, int(220 + header_y_off)),
            sc.headline,
            fill=(255, 255, 255),
            font=font_h1,
            anchor="mm",
        )
    else:
        # Đặt tại y=380..476 và x=220..canvas_w-220 để nằm gọn 100% trong vùng an toàn (Safe Zone)
        # ngay cả khi camera zoom 1.14x (top_f lên tới ~319px, left_f lên tới ~205px)
        tag_box = [220, int(380 + header_y_off), canvas_w - 220, int(476 + header_y_off)]
        draw.rounded_rectangle(tag_box, radius=24, fill=sc.accent_rgb, outline=INK_BLACK, width=6)
        draw.text(
            (canvas_w // 2, int(428 + header_y_off)),
            sc.headline,
            fill=(255, 255, 255),
            font=font_badge,
            anchor="mm",
        )

    # Mặt đất vẽ tay
    ground_y = cy + (195 if is_landscape else 220)
    _draw_sketch_line(draw, (110, ground_y), (canvas_w - 110, ground_y), t_sec=t_sec, seed=scene_seed + 1, width=8)

    # 8 Đạo cụ Ẩn dụ Trực quan (với chuyển động liên tục theo t_sec)
    if sc.prop_type == "alarm":
        _draw_doodle_character(draw, cx - 130, cy, 1.15, sc.accent_rgb, "exhausted", t_sec, scene_seed)
        ring_shake_x = math.sin(t_sec * 38.0) * 12.0
        ring_shake_y = abs(math.cos(t_sec * 38.0)) * -10.0
        clock_cx, clock_cy = cx + 205 + ring_shake_x, cy - 110 + ring_shake_y
        # Vòng sóng âm thanh lan tỏa mượt (Expanding Sound Waves)
        for w_i in range(2):
            wave_p = (t_sec * 1.8 + w_i * 0.5) % 1.0
            wave_r = 98.0 + wave_p * 75.0
            draw.arc(
                [clock_cx - wave_r, clock_cy - wave_r, clock_cx + wave_r, clock_cy + wave_r],
                start=210,
                end=330,
                fill=ALERT_RED,
                width=max(2, int(6 * (1.0 - wave_p))),
            )
        _draw_sketch_circle(draw, clock_cx, clock_cy, 95, t_sec, scene_seed + 7, fill=ALERT_RED, width=8)
        _draw_sketch_circle(draw, clock_cx, clock_cy, 72, t_sec, scene_seed + 8, fill=(255, 255, 255), width=5)
        # Kim đồng hồ quay nhanh mượt mà
        min_ang = t_sec * 9.0
        hr_ang = t_sec * 1.5
        _draw_sketch_line(
            draw,
            (clock_cx, clock_cy),
            (clock_cx + 48 * math.sin(min_ang), clock_cy - 48 * math.cos(min_ang)),
            t_sec,
            scene_seed + 9,
            width=6,
        )
        _draw_sketch_line(
            draw,
            (clock_cx, clock_cy),
            (clock_cx + 34 * math.sin(hr_ang), clock_cy - 34 * math.cos(hr_ang)),
            t_sec,
            scene_seed + 10,
            width=6,
        )

    elif sc.prop_type == "block":
        drop_y, squash = _spring_drop(t_sec, impact_time=0.52)
        _draw_doodle_character(
            draw,
            cx,
            cy + 35,
            1.08,
            sc.accent_rgb,
            "crushed",
            t_sec,
            scene_seed,
            hunched=True,
            squash=squash,
        )
        bx1, by1, bx2, by2 = cx - 265, cy - 395 + drop_y, cx + 265, cy - 175 + drop_y
        pts = _coherent_wobble_pts([(bx1, by1), (bx2, by1), (bx2, by2), (bx1, by2)], t_sec=t_sec, seed=scene_seed + 15, amp=1.8)
        draw.polygon(pts, fill=(85, 92, 104))
        draw.line(pts + [pts[0]], fill=INK_BLACK, width=8)
        draw.text((cx, (by1 + by2) / 2), sc.sub_badge or "HEAVY BURDEN", fill=(255, 255, 255), font=font_badge, anchor="mm")

    elif sc.prop_type == "spear_nature":
        _draw_doodle_character(draw, cx - 90, cy, 1.15, sc.accent_rgb, "calm", t_sec, scene_seed)
        sx = cx + 70 + math.sin(t_sec * 3.0) * 6.0
        _draw_sketch_line(draw, (sx, cy - 260), (sx, cy + 215), t_sec, scene_seed + 30, color=(120, 72, 32), width=9)
        draw.polygon([(sx, cy - 305), (sx - 22, cy - 255), (sx + 22, cy - 255)], fill=GOLD_ACCENT, outline=INK_BLACK)
        sun_r = 66.0 + math.sin(t_sec * 4.0) * 5.0
        _draw_sketch_circle(draw, cx + 230, cy - 290, sun_r, t_sec, scene_seed + 33, fill=GOLD_ACCENT, width=6)

    elif sc.prop_type == "split":
        _draw_sketch_line(draw, (cx, cy - 340), (cx, cy + 220), t_sec, scene_seed + 40, color=ALERT_RED, width=8)
        _draw_doodle_character(draw, cx - 210, cy + 15, 0.92, TAG_COLOR_RGB["@MODERNYOU"], "exhausted", t_sec, scene_seed)
        _draw_doodle_character(draw, cx + 210, cy + 15, 0.92, TAG_COLOR_RGB["@ANCESTOR"], "calm", t_sec, scene_seed + 50)
        draw.rounded_rectangle([cx - 380, cy - 320, cx - 50, cy - 235], radius=16, fill=ALERT_RED, outline=INK_BLACK, width=5)
        draw.text((cx - 215, cy - 277), "CHAOS / TRAP", fill=(255, 255, 255), font=font_badge, anchor="mm")
        draw.rounded_rectangle([cx + 50, cy - 320, cx + 380, cy - 235], radius=16, fill=GREEN_CALM, outline=INK_BLACK, width=5)
        draw.text((cx + 215, cy - 277), "CLARITY", fill=(255, 255, 255), font=font_badge, anchor="mm")

    elif sc.prop_type == "puppet_strings":
        _draw_doodle_character(draw, cx - 150, cy + 25, 0.98, TAG_COLOR_RGB["@PLEASER"], "exhausted", t_sec, scene_seed, hunched=True)
        _draw_doodle_character(draw, cx + 190, cy - 40, 1.08, TAG_COLOR_RGB["@MANIPULATOR"], "smirk", t_sec, scene_seed + 60)
        bar_y = cy - 330 + math.sin(t_sec * 4.2) * 16.0
        bar_tilt = math.cos(t_sec * 4.2) * 18.0
        _draw_sketch_line(draw, (cx - 245, bar_y - bar_tilt), (cx - 55, bar_y + bar_tilt), t_sec, scene_seed + 61, color=ALERT_RED, width=9)
        for i_s, sx_off in enumerate((-210, -150, -90)):
            top_sy = bar_y + (i_s - 1) * bar_tilt * 0.6
            _draw_sketch_line(draw, (cx + sx_off, top_sy), (cx + sx_off, cy - 88), t_sec, scene_seed + 70 + i_s, color=ALERT_RED, width=4)

    elif sc.prop_type == "brain_battery":
        _draw_doodle_character(draw, cx - 140, cy + 10, 1.08, sc.accent_rgb, "exhausted", t_sec, scene_seed)
        bat_x1, bat_y1, bat_x2, bat_y2 = cx + 80, cy - 250, cx + 310, cy + 120
        draw.rounded_rectangle([bat_x1, bat_y1, bat_x2, bat_y2], radius=22, fill=(255, 255, 255), outline=INK_BLACK, width=8)
        draw.rectangle([cx + 145, bat_y1 - 28, cx + 245, bat_y1], fill=INK_BLACK)
        fill_ratio = max(0.12, 0.72 - 0.58 * _ease_in_out_cubic(phase))
        fill_top = bat_y2 - (bat_y2 - bat_y1 - 24) * fill_ratio
        bar_color = ALERT_RED if fill_ratio < 0.32 else GOLD_ACCENT
        draw.rounded_rectangle([bat_x1 + 14, fill_top, bat_x2 - 14, bat_y2 - 14], radius=12, fill=bar_color)
        draw.text(((bat_x1 + bat_x2) / 2, (bat_y1 + bat_y2) / 2), f"{int(round(fill_ratio * 100))}%", fill=INK_BLACK, font=font_h1, anchor="mm")

    elif sc.prop_type == "money_scale":
        _draw_doodle_character(draw, cx - 190, cy + 20, 1.0, sc.accent_rgb, "shocked", t_sec, scene_seed)
        pivot_x, pivot_y = cx + 140, cy - 60
        _draw_sketch_line(draw, (pivot_x, pivot_y), (pivot_x, cy + 215), t_sec, scene_seed + 71, width=9)
        tilt = 42.0 * math.sin(t_sec * 2.6)
        _draw_sketch_line(draw, (pivot_x - 150, pivot_y - tilt), (pivot_x + 150, pivot_y + tilt), t_sec, scene_seed + 72, width=8)
        _draw_sketch_circle(draw, pivot_x - 150, pivot_y - tilt + 48, 48, t_sec, scene_seed + 73, fill=GOLD_ACCENT, width=6)
        draw.text((pivot_x - 150, pivot_y - tilt + 48), "$", fill=INK_BLACK, font=font_h1, anchor="mm")
        _draw_sketch_circle(draw, pivot_x + 150, pivot_y + tilt + 58, 62, t_sec, scene_seed + 74, fill=ALERT_RED, width=6)
        draw.text((pivot_x + 150, pivot_y + tilt + 58), "DEBT", fill=(255, 255, 255), font=font_badge, anchor="mm")

    elif sc.prop_type == "mask":
        _draw_doodle_character(draw, cx - 150, cy + 20, 1.05, sc.accent_rgb, "smirk", t_sec, scene_seed)
        mask_x = cx + 145 + math.sin(t_sec * 3.0) * 18.0
        mask_y = cy - 95
        _draw_sketch_circle(draw, mask_x, mask_y, 78, t_sec, scene_seed + 91, fill=(255, 255, 255), width=7)
        draw.arc([mask_x - 36, mask_y + 2, mask_x + 36, mask_y + 42], 15, 165, fill=GREEN_CALM, width=6)
        draw.text((mask_x, mask_y - 108), "FAKE PERSONA", fill=ALERT_RED, font=font_badge, anchor="mm")

    elif sc.prop_type == "magnifying_glass":
        _draw_doodle_character(draw, cx - 140, cy + 20, 1.05, sc.accent_rgb, "shocked", t_sec, scene_seed)
        lens_x = cx + 120 + math.cos(t_sec * 2.5) * 22.0
        lens_y = cy - 85 + math.sin(t_sec * 2.5) * 14.0
        _draw_sketch_circle(draw, lens_x, lens_y, 74, t_sec, scene_seed + 95, fill=GOLD_ACCENT, width=8)
        _draw_sketch_line(draw, (lens_x + 52, lens_y + 52), (lens_x + 125, lens_y + 125), t_sec, scene_seed + 96, width=12)
        draw.text((lens_x, lens_y), "LIE?", fill=INK_BLACK, font=font_h1, anchor="mm")

    else:
        _draw_doodle_character(draw, cx, cy + 20, 1.15, sc.accent_rgb, "calm", t_sec, scene_seed)
        bulb_y = cy - 335 + math.sin(t_sec * 4.5) * 14.0
        _draw_sketch_circle(draw, cx, bulb_y, 58, t_sec, scene_seed + 80, fill=GOLD_ACCENT, width=7)
        draw.text((cx, bulb_y), "!", fill=INK_BLACK, font=font_h1, anchor="mm")

    # Stamp Badge có nhịp thở nhẹ (chỉ vẽ khi có số liệu thật, đặt ở góc trên bên phải cho 16:9 để nhường đáy cho phụ đề ASS)
    if sc.sub_badge and not sc.sub_badge.upper().startswith("SCENE "):
        pulse = math.sin(t_sec * 5.0) * 5.0
        if is_landscape:
            bx_c = int(canvas_w - 270)
            by_c = int(152 + header_y_off)
            stamp_box = [
                int(bx_c - 145 - pulse),
                int(by_c - 38 - pulse * 0.3),
                int(bx_c + 145 + pulse),
                int(by_c + 38 + pulse * 0.3),
            ]
            draw.rounded_rectangle(stamp_box, radius=18, fill=GOLD_ACCENT, outline=INK_BLACK, width=5)
            draw.text((bx_c, by_c), sc.sub_badge, fill=INK_BLACK, font=font_badge, anchor="mm")
        else:
            badge_y = int(cy + 275)
            stamp_box = [
                int(canvas_w // 2 - 250 - pulse),
                int(badge_y - 42 - pulse * 0.4),
                int(canvas_w // 2 + 250 + pulse),
                int(badge_y + 42 + pulse * 0.4),
            ]
            draw.rounded_rectangle(stamp_box, radius=20, fill=GOLD_ACCENT, outline=INK_BLACK, width=6)
            draw.text((canvas_w // 2, badge_y), sc.sub_badge, fill=INK_BLACK, font=font_badge, anchor="mm")

    # Chỉ vẽ hộp phụ đề nội bộ khi render clip Doodle độc lập có đính kèm audio_path riêng
    # (Trong luồng LangGraph/VideoAgent chính, phụ đề Karaoke ASS đã được burn-in thống nhất toàn video, tránh đè 2 lớp phụ đề!)
    if sc.audio_path:
        _draw_karaoke_caption_box(draw, sc.narration, phase, canvas_w, canvas_h)

    # ── SUB-PIXEL FLOATING-POINT CAMERA ENGINE (ZERO PIXEL JUDDER) ────────────
    eased_p = _ease_in_out_cubic(phase)
    # Hiệu ứng Whip-Pan / Glide nhẹ lúc chuyển cảnh (0.25s đầu)
    whip_x = (1.0 - _ease_in_out_cubic(min(1.0, t_sec / 0.26))) * (42.0 if sc.scene_id % 2 == 1 else -42.0)

    cam = sc.camera_motion
    if cam in ("dolly_in", "zoom_in", "hyper_zoom"):
        zoom = 1.01 + 0.13 * eased_p
    elif cam in ("dolly_out", "zoom_out"):
        zoom = 1.14 - 0.12 * eased_p
    elif cam == "dolly_zoom":
        zoom = 1.03 + 0.09 * math.sin(phase * math.pi)
    elif cam == "overhead_90":
        zoom = 1.07 + 0.04 * eased_p
    else:
        zoom = 1.03 + 0.07 * eased_p

    crop_w_f = float(width) / zoom
    crop_h_f = float(height) / zoom

    if cam == "arc_orbit":
        pan_x_f = math.sin(phase * 2.0 * math.pi) * 38.0 + whip_x
    elif cam in ("pan_left", "pan_right"):
        pan_x_f = (eased_p - 0.5) * 68.0 + whip_x
    else:
        pan_x_f = whip_x

    if cam in ("worms_eye", "overhead_90"):
        pan_y_f = (0.5 - eased_p) * 58.0
    else:
        pan_y_f = math.sin(phase * math.pi) * 14.0

    left_f = max(0.0, min(float(canvas_w) - crop_w_f, (float(canvas_w) - crop_w_f) * 0.5 + pan_x_f))
    top_f = max(0.0, min(float(canvas_h) - crop_h_f, (float(canvas_h) - crop_h_f) * 0.5 + pan_y_f))

    # Dùng Image.Transform.EXTENT với tọa độ thực (float) + nội suy BILINEAR để trượt mượt dưới mức 1 pixel
    return img.transform(
        (width, height),
        Image.Transform.EXTENT,
        (left_f, top_f, left_f + crop_w_f, top_f + crop_h_f),
        Image.Resampling.BILINEAR,
    )


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
    Agent-Callable Tool đóng gói toàn bộ quy trình nhận diện, chọn đạo cụ ẩn dụ và dựng hoạt họa 2D Doodle 30 FPS Siêu Mượt.
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
        Đã tắt hoàn toàn tính năng người que / 2D Doodle theo yêu cầu người dùng — toàn hệ thống dùng 100% B-Roll người thật / tư liệu điện ảnh.
        """
        return False, "Tính năng người que / 2D Doodle đã tắt hoàn toàn — dùng 100% video/ảnh điện ảnh thật."

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

        if any(w in ctx for w in ("manipulat", "puppet", "gaslight", "narcissist", "strings")):
            prop = "puppet_strings"
        elif any(w in ctx for w in ("point directly", "command them", "break this paralysis", "solution", "breakthrough", "take action", "call an ambulance")):
            prop = "climax"
        elif any(w in ctx for w in ("lie", "deceit", "clue", "detect", "investigat", "witness", "assumed someone else", "specific person", "lock eyes")):
            prop = "magnifying_glass"
        elif any(w in ctx for w in ("fake", "persona", "pretend", "disguise", "two-faced", "mask")):
            prop = "mask"
        elif any(w in ctx for w in ("crushed", "heavy", "burden", "weight", "trap", "shrunk", "inches", "stress", "paralysis", "locked their windows")):
            prop = "block"
        elif any(w in ctx for w in ("vs", "compare", "while", "meanwhile", "paradox", "twice as", "divides", "crowd forms", "group")):
            prop = "split"
        elif any(w in ctx for w in ("dopamine", "battery", "sleep", "tired", "cortisol", "exhausted", "drained")):
            prop = "brain_battery"
        elif any(w in ctx for w in ("money", "debt", "bank", "inflation", "salary", "tax", "loan", "credit", "$")):
            prop = "money_scale"
        elif any(w in ctx for w in ("alarm", "monday", "morning", "clock", "wake up", "6:00", "hours a week", "emergency")):
            prop = "alarm"
        elif any(w in ctx for w in ("hunter", "nature", "free", "spear", "walk", "leisure")):
            prop = "spear_nature"
        elif scene_id >= total_scenes:
            prop = "climax"
        else:
            prop = "alarm" if scene_id == 1 else "block"

        motion_cycle = ["dolly_in", "overhead_90", "arc_orbit", "worms_eye", "dolly_zoom", "dolly_out"]
        cam = motion_cycle[(max(1, scene_id) - 1) % len(motion_cycle)]

        m_num = re.search(
            r"\b(\d+(?:[.,]\d+)?\s*(?:%|hrs/week|hours/week|hours|hrs|years|days|inches|psi|x|\$))\b",
            narration,
            re.IGNORECASE,
        )
        badge = m_num.group(1).upper() if m_num else ""

        _HEADLINE_FILLER = {
            "THE", "AND", "FOR", "WITH", "FROM", "INTO", "ONTO", "UPON", "THAT", "THIS",
            "THESE", "THOSE", "WHEN", "WHAT", "WHERE", "WHICH", "WHO", "WHY", "HOW",
            "PEOPLE", "CLAIM", "TELL", "TOLD", "SAY", "SAYS", "SAID", "THINK", "BELIEVE",
            "MOST", "EXPERTS", "EVERYONE", "SOMEONE", "THEY", "YOU", "YOUR", "THEIR",
            "OUR", "BEING", "BECOME", "MAKES", "MAKE", "GIVES", "GIVE", "ACTUALLY",
            "REALLY", "JUST", "ONLY", "ALWAYS", "NEVER", "ABOUT", "BECAUSE", "WHILE",
            "2D", "DOODLE", "SKETCH", "ANIMATION", "MINIMALIST", "LINE", "ART",
            "CONCEPT", "PROCESS", "EXPLAINER", "WHITEBOARD", "ILLUSTRATION",
            "STICKMAN", "VECTOR", "DRAWING", "HAND", "DRAWN", "STYLE", "KEY",
        }
        vp_clean_words = [
            re.sub(r"[^\w$-]", "", w).upper()
            for w in str(visual_prompt or "").split()
            if len(w) > 2 and re.sub(r"[^\w$-]", "", w).upper() not in _HEADLINE_FILLER
            and re.sub(r"[^\w$-]", "", w).upper() not in {"CINEMATIC", "CLOSE", "SHOT", "SLOW", "MOTION", "DARK", "ROOM", "LIGHTING", "CAMERA", "VIDEO"}
        ]
        narr_clean_words = [
            re.sub(r"[^\w$-]", "", w).upper()
            for w in narration.split()
            if len(w) > 2 and re.sub(r"[^\w$-]", "", w).upper() not in _HEADLINE_FILLER
        ]
        source_words = vp_clean_words[:4] if len(vp_clean_words) >= 3 else narr_clean_words[:4]
        if not source_words:
            source_words = [re.sub(r"[^\w$-]", "", w).upper() for w in narration.split() if len(w) > 2][:4]
        acc_words: List[str] = []
        for cw in source_words[:4]:
            if not cw:
                continue
            cand_h = " ".join(acc_words + [cw])
            if len(cand_h) <= 28 or not acc_words:
                acc_words.append(cw)
            else:
                break
        headline = " ".join(acc_words) if acc_words else f"ACT {scene_id}"

        return tag, prop, cam, headline, badge[:22]

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
        audio_path: Optional[str] = None,
        sfx_path: Optional[str] = None,
        fps: int = DEFAULT_FPS,
        width: int = 1080,
        height: int = 1920,
    ) -> Optional[str]:
        """
        Đã vô hiệu hóa hoàn toàn công cụ dựng người que / 2D Doodle — luôn trả về None để hệ thống chuyển sang tải Video B-Roll / Ảnh điện ảnh thật.
        """
        return None

