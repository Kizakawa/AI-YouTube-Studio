"""
Mascot Animation Engine (`app/services/pomodoro/mascot_engine.py`)
==================================================================
Hệ thống điều khiển chuyển động linh vật đa vùng (Multi-Zone 2D Skeletal Mesh Warping + Awake Sprite):
  1. `idle_breathing`: Nhịp thở phập phồng sâu và rõ nét (biên độ 8.5–9.5px + giãn nở lồng ngực).
  2. `ear_twitch`: Đôi tai giật nhẹ (double-twitch) định kỳ mỗi vài giây.
  3. `tail_wag`: Đuôi bông vẫy nhịp nhàng theo chu kỳ (và vẫy liên tục khi sang giờ nghỉ Break).
  4. `sleep_shift`: Cử động xoay/nghiêng đầu nhẹ khi đang ngủ.
  5. `yawn_stretch`: Vươn vai / duỗi chân trước định kỳ + bong bóng ngủ `Zzz` bay lên.
  6. `break_wakeup`: Chuyển sang tranh thức giấc (`waking_image` nếu có) hoặc mở mắt cười vui vẻ
     trong giờ Giải lao (`BREAK`) kèm nốt nhạc `♪ ♫ ♥` và Easter Egg Vương miện/Mũ kỷ niệm.
"""

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.services.pomodoro.scheduler import (
    DEFAULT_CATALOG_BGM_ROOT,
    DEFAULT_MASCOTS_ROOT,
    DEFAULT_MASTER_BGM_ROOT,
)


@dataclass
class MascotPack:
    id: str
    display_name: str
    species: str
    companion_title: str
    description: str
    background_image: str
    waking_image: str
    placement: Dict[str, int]
    zones: Dict[str, Dict[str, Any]]
    animations: Dict[str, Dict[str, Any]]
    speech_bubble: Dict[str, Any]
    waking_zones: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    speech_bubble_break: Dict[str, Any] = field(default_factory=dict)
    scene_anchors: Dict[str, Any] = field(default_factory=dict)
    pack_dir: Optional[Path] = None
    resolved_bg_path: Optional[Path] = None
    resolved_waking_bg_path: Optional[Path] = None


def list_available_mascots(mascots_root: Path = DEFAULT_MASCOTS_ROOT) -> List[str]:
    """Trả về danh sách các `mascot_id` khả dụng."""
    if not mascots_root.exists():
        return ["golden_retriever"]
    ids = [
        d.name
        for d in sorted(mascots_root.iterdir())
        if d.is_dir() and not d.name.startswith("_") and (d / "mascot.json").exists()
    ]
    return ids or ["golden_retriever"]


def _resolve_asset_image(fname: str, pack_dir: Path) -> Optional[Path]:
    if not fname:
        return None
    candidates = [
        pack_dir / fname,
        DEFAULT_CATALOG_BGM_ROOT / fname,
        DEFAULT_MASTER_BGM_ROOT.parent / fname,
    ]
    return next((p for p in candidates if p.exists()), None)


def load_mascot_pack(
    mascot_id: str = "golden_retriever",
    mascots_root: Path = DEFAULT_MASCOTS_ROOT,
) -> MascotPack:
    """Đọc cấu hình `mascot.json` và tự động tìm đường dẫn ảnh nền ngủ (`background_image`) + thức (`waking_image`)."""
    cfg_path = mascots_root / mascot_id / "mascot.json"
    if not cfg_path.exists():
        cfg_path = mascots_root / "golden_retriever" / "mascot.json"

    if cfg_path.exists():
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    else:
        data = {
            "id": "golden_retriever",
            "display_name": "Golden Retriever 🐕",
            "species": "dog",
            "companion_title": "Sleeping Golden Retriever Buddy",
            "description": "Chú chó Golden Retriever nằm ngủ ấm áp cạnh bàn học",
            "background_image": "cozy_lofi_study_room_bg.jpg",
            "waking_image": "golden_retriever_waking_bg.jpg",
            "placement": {"x": 215, "y": 630, "width": 640, "height": 330},
            "zones": {
                "body_breath": {"x1": 310, "y1": 635, "x2": 685, "y2": 875},
                "ear": {"x1": 585, "y1": 700, "x2": 705, "y2": 855, "pivot_x": 645, "pivot_y": 715},
                "tail": {"x1": 215, "y1": 805, "x2": 480, "y2": 958, "pivot_x": 255, "pivot_y": 845},
                "head": {"x1": 595, "y1": 675, "x2": 850, "y2": 900, "eye_left": [720, 792], "eye_right": [796, 785]},
                "front_paw": {"x1": 785, "y1": 795, "x2": 858, "y2": 860},
            },
            "animations": {},
            "speech_bubble": {
                "box": [180, 445, 915, 595],
                "tail_tip": [725, 680],
                "tail_base_left": [640, 595],
                "tail_base_right": [705, 595],
            },
        }

    bg_name = data.get("background_image", "cozy_lofi_study_room_bg.jpg")
    waking_name = data.get("waking_image", "")
    resolved_bg = _resolve_asset_image(bg_name, cfg_path.parent)
    if resolved_bg is None:
        resolved_bg = (
            _resolve_asset_image("sleeping.jpg", cfg_path.parent)
            or _resolve_asset_image("cozy_lofi_study_room_bg.jpg", cfg_path.parent)
        )
    resolved_waking = _resolve_asset_image(waking_name, cfg_path.parent)
    if resolved_waking is None:
        resolved_waking = _resolve_asset_image("waking.jpg", cfg_path.parent)

    return MascotPack(
        id=data.get("id", mascot_id),
        display_name=data.get("display_name", mascot_id),
        species=data.get("species", "dog"),
        companion_title=data.get("companion_title", "Cozy Study Companion"),
        description=data.get("description", ""),
        background_image=bg_name,
        waking_image=waking_name,
        placement=data.get("placement", {"x": 215, "y": 630, "width": 640, "height": 330}),
        zones=data.get("zones", {}),
        animations=data.get("animations", {}),
        speech_bubble=data.get("speech_bubble", {}),
        waking_zones=data.get("waking_zones", {}),
        speech_bubble_break=data.get("speech_bubble_break", {}),
        scene_anchors=data.get("scene_anchors", {}),
        pack_dir=cfg_path.parent,
        resolved_bg_path=resolved_bg,
        resolved_waking_bg_path=resolved_waking,
    )


def generate_cozy_dog_study_background(
    out_png: Path,
    width: int = 1920,
    height: int = 1080,
    mascot_id: str = "golden_retriever",
) -> Path:
    """Vẽ cảnh nền dự phòng Cozy Study Room + Mascot Companion (1920x1080)."""
    img = Image.new("RGBA", (width, height), (14, 18, 30, 255))
    dr = ImageDraw.Draw(img)

    for y in range(height):
        t = y / float(height)
        dr.line([(0, y), (width, y)], fill=(int(16 + 18 * t), int(20 + 16 * t), int(34 + 14 * t), 255))

    win_x1, win_y1, win_x2, win_y2 = 110, 75, 1120, 720
    dr.rounded_rectangle([(win_x1 - 12, win_y1 - 12), (win_x2 + 12, win_y2 + 12)], radius=18, fill=(58, 38, 26, 255))
    for wy in range(win_y1, win_y2):
        wt = (wy - win_y1) / float(win_y2 - win_y1)
        dr.line([(win_x1, wy), (win_x2, wy)], fill=(int(18 + 20 * wt), int(28 + 30 * wt), int(55 + 42 * wt), 255))

    rng = random.Random(101)
    glow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gdr = ImageDraw.Draw(glow_layer)
    bokeh_colors = [(255, 195, 110, 95), (255, 140, 85, 80), (135, 200, 255, 75), (255, 220, 150, 90)]
    for _ in range(32):
        bx = rng.randint(win_x1 + 40, win_x2 - 40)
        by = rng.randint(win_y1 + 180, win_y2 - 50)
        br = rng.randint(18, 52)
        gdr.ellipse([(bx - br, by - br), (bx + br, by + br)], fill=rng.choice(bokeh_colors))

    gdr.ellipse([(740, 180), (1420, 820)], fill=(255, 185, 95, 68))
    gdr.ellipse([(830, 280), (1300, 730)], fill=(255, 210, 130, 88))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=36))
    img = Image.alpha_composite(img, glow_layer)
    dr = ImageDraw.Draw(img)

    desk_y = 700
    for y in range(desk_y, height):
        dt = (y - desk_y) / float(height - desk_y)
        dr.line([(0, y), (width, y)], fill=(int(68 - 22 * dt), int(42 - 14 * dt), int(26 - 9 * dt), 255))
    dr.line([(0, desk_y), (width, desk_y)], fill=(120, 82, 52, 255), width=4)

    dr.ellipse([(180, 650), (575, 765)], fill=(128, 56, 48, 255), outline=(175, 92, 75, 255), width=4)
    dr.ellipse([(235, 615), (510, 735)], fill=(218, 152, 74, 255))
    dr.ellipse([(425, 605), (535, 705)], fill=(228, 164, 84, 255))

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(str(out_png), quality=95)
    return out_png


def _warp_patch_2d(
    frame_np: np.ndarray,
    orig_np: np.ndarray,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    dx_map: np.ndarray,
    dy_map: np.ndarray,
) -> None:
    """Nội suy song tuyến tính (Bilinear Patch Warp) mượt mà tại vùng `(x1, y1, x2, y2)`."""
    h, w = y2 - y1, x2 - x1
    if h <= 2 or w <= 2:
        return
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    src_x = np.clip(xx - dx_map, 0.0, float(w - 1))
    src_y = np.clip(yy - dy_map, 0.0, float(h - 1))

    x0 = np.floor(src_x).astype(np.int32)
    x1_i = np.clip(x0 + 1, 0, w - 1)
    y0 = np.floor(src_y).astype(np.int32)
    y1_i = np.clip(y0 + 1, 0, h - 1)

    wx = (src_x - x0)[..., None]
    wy = (src_y - y0)[..., None]

    patch = orig_np[y1:y2, x1:x2].astype(np.float32)
    warped = (
        patch[y0, x0] * (1.0 - wx) * (1.0 - wy)
        + patch[y0, x1_i] * wx * (1.0 - wy)
        + patch[y1_i, x0] * (1.0 - wx) * wy
        + patch[y1_i, x1_i] * wx * wy
    )
    frame_np[y1:y2, x1:x2] = np.clip(warped, 0, 255).astype(np.uint8)


def _radial_mask(h: int, w: int, power: float = 1.6) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    ny = (yy / float(max(1, h - 1))) * 2.0 - 1.0
    nx = (xx / float(max(1, w - 1))) * 2.0 - 1.0
    weight = np.clip(1.0 - (nx * nx + ny * ny), 0.0, 1.0) ** power
    return weight, nx, ny


def _window_envelope(t_sec: float, windows: List[List[float]]) -> Tuple[bool, float]:
    for w in windows:
        if len(w) >= 2 and w[0] <= t_sec <= w[1]:
            span = max(0.05, float(w[1] - w[0]))
            return True, (t_sec - w[0]) / span
    return False, 0.0


class AnimationController:
    """
    Bộ điều khiển 6 loại chuyển động của Linh Vật trên từng khung hình:
      1. `idle_breathing`: Thở sâu rõ rệt + phồng lồng ngực
      2. `ear_twitch`: Đôi tai giật nhẹ (double-twitch)
      3. `tail_wag`: Vẫy đuôi bông mềm mại
      4. `sleep_shift`: Nghiêng đầu / rúc mũi ấm áp
      5. `yawn_stretch`: Duỗi người / vươn vai + bong bóng `Zzz`
      6. `break_wakeup`: Thức giấc vui vẻ trong giờ nghỉ (`BREAK`) + nốt nhạc `♪ ♫ ♥`
    """

    def __init__(self, mascot: MascotPack, loop_sec: float = 10.0, fps: int = 24):
        self.mascot = mascot
        self.loop_sec = loop_sec
        self.fps = fps
        self.scheduled_events: List[Dict[str, Any]] = []

    def generate_event_schedule(
        self,
        total_duration_sec: float,
        rng: random.Random,
    ) -> List[Dict[str, Any]]:
        """
        Lập lịch trước toàn bộ sự kiện cử động ngẫu nhiên có kiểm soát bằng `seed`
        cho một khoảng thời gian bất kỳ (`ear_twitch`, `tail_wag`, `sleep_shift`, `yawn_stretch`).
        """
        events: List[Dict[str, Any]] = []
        specs = [
            ("ear_twitch", 15.0, 32.0, 0.85),
            ("tail_wag", 20.0, 45.0, 2.1),
            ("sleep_shift", 42.0, 85.0, 2.8),
            ("yawn_stretch", 110.0, 210.0, 3.8),
        ]
        for etype, min_gap, max_gap, dur in specs:
            cursor = rng.uniform(2.0, min(8.0, min_gap))
            while cursor + dur < total_duration_sec:
                events.append({"type": etype, "start": round(cursor, 2), "end": round(cursor + dur, 2)})
                cursor += rng.uniform(min_gap, max_gap)
        events.sort(key=lambda e: e["start"])
        self.scheduled_events = events
        return events

    def apply_mascot_animations(
        self,
        frame_np: np.ndarray,
        base_np: np.ndarray,
        t_sec: float,
        is_break: bool = False,
        has_dedicated_waking_art: bool = False,
    ) -> np.ndarray:
        zones = (
            self.mascot.waking_zones
            if (is_break and has_dedicated_waking_art and self.mascot.waking_zones)
            else self.mascot.zones
        )
        anims = self.mascot.animations
        H, W = frame_np.shape[:2]

        # 1. IDLE BREATHING + YAWN STRETCH trên vùng ngực/lưng (`body_breath`)
        bz = zones.get("body_breath", {"x1": 310, "y1": 635, "x2": 685, "y2": 875})
        bx1, by1 = max(0, int(bz["x1"])), max(0, int(bz["y1"]))
        bx2, by2 = min(W, int(bz["x2"])), min(H, int(bz["y2"]))
        bh, bw = by2 - by1, bx2 - bx1
        if bh > 10 and bw > 10:
            w_mask, nx, _ = _radial_mask(bh, bw, power=1.45)
            b_cfg = anims.get("idle_breathing", {})
            amp_y = float(b_cfg.get("amplitude_px", 9.5))
            expand_x = float(b_cfg.get("expand_px", 4.5))
            breath_phase = math.sin(2.0 * math.pi * (3.0 * t_sec / self.loop_sec))

            ys_cfg = anims.get("yawn_stretch", {})
            in_stretch, s_prog = _window_envelope(t_sec, ys_cfg.get("windows_sec", [[8.0, 9.6]]))
            stretch_boost = math.sin(math.pi * s_prog) if in_stretch else 0.0

            dy_map = (-amp_y * breath_phase - 7.5 * stretch_boost) * w_mask
            dx_map = (expand_x * breath_phase + 6.0 * stretch_boost) * nx * w_mask
            _warp_patch_2d(frame_np, base_np, bx1, by1, bx2, by2, dx_map, dy_map)

        # 2. SLEEP SHIFT & BREAK WAKEUP trên vùng đầu (`head`)
        hz = zones.get("head", {"x1": 595, "y1": 675, "x2": 850, "y2": 900})
        hx1, hy1 = max(0, int(hz["x1"])), max(0, int(hz["y1"]))
        hx2, hy2 = min(W, int(hz["x2"])), min(H, int(hz["y2"]))
        hh, hw = hy2 - hy1, hx2 - hx1
        if hh > 10 and hw > 10:
            hw_mask, _, _ = _radial_mask(hh, hw, power=1.5)
            ss_cfg = anims.get("sleep_shift", {})
            in_shift, sh_prog = _window_envelope(t_sec, ss_cfg.get("windows_sec", [[4.2, 5.6]]))
            shift_env = math.sin(math.pi * sh_prog) if in_shift else 0.0

            head_bob_y = -4.2 * math.sin(2.0 * math.pi * (3.0 * t_sec / self.loop_sec) - 0.35)
            head_dx = float(ss_cfg.get("shift_x_px", 7.0)) * shift_env
            head_dy = head_bob_y + float(ss_cfg.get("shift_y_px", -6.0)) * shift_env

            if is_break:
                wk_cfg = anims.get("break_wakeup", {})
                # Nếu đã có tranh thức dậy riêng (`waking_image`), chỉ cần lắc lư đầu nhẹ nhàng theo điệu nhạc
                lift_y = -4.0 if has_dedicated_waking_art else float(wk_cfg.get("head_lift_px", -14.0))
                head_dy = lift_y + 4.8 * math.sin(2.0 * math.pi * (2.0 * t_sec / self.loop_sec))
                head_dx = 5.2 * math.sin(2.0 * math.pi * (1.0 * t_sec / self.loop_sec))

            _warp_patch_2d(frame_np, frame_np.copy(), hx1, hy1, hx2, hy2, head_dx * hw_mask, head_dy * hw_mask)

        # 3. EAR TWITCH (Đôi tai giật nhẹ double-twitch rất sinh động)
        ez = zones.get("ear", {"x1": 585, "y1": 700, "x2": 705, "y2": 855})
        ex1, ey1 = max(0, int(ez["x1"])), max(0, int(ez["y1"]))
        ex2, ey2 = min(W, int(ez["x2"])), min(H, int(ez["y2"]))
        eh, ew = ey2 - ey1, ex2 - ex1
        if eh > 10 and ew > 10:
            ew_mask, _, _ = _radial_mask(eh, ew, power=1.4)
            et_cfg = anims.get("ear_twitch", {})
            in_ear, e_prog = _window_envelope(t_sec, et_cfg.get("windows_sec", [[1.4, 2.3], [5.8, 6.7]]))
            ear_dx, ear_dy = 0.0, 0.0
            if in_ear:
                twitch_pulse = abs(math.sin(2.0 * math.pi * e_prog)) * math.sin(math.pi * e_prog)
                ear_dy = float(et_cfg.get("shift_y_px", -14.0)) * twitch_pulse
                ear_dx = float(et_cfg.get("shift_x_px", 5.0)) * twitch_pulse
            elif is_break:
                base_perk = -3.0 if has_dedicated_waking_art else -10.0
                ear_dy = base_perk + 4.0 * math.sin(2.0 * math.pi * (4.0 * t_sec / self.loop_sec))
                ear_dx = 3.5 * math.cos(2.0 * math.pi * (4.0 * t_sec / self.loop_sec))

            if abs(ear_dx) > 0.2 or abs(ear_dy) > 0.2:
                _warp_patch_2d(frame_np, frame_np.copy(), ex1, ey1, ex2, ey2, ear_dx * ew_mask, ear_dy * ew_mask)

        # 4. TAIL WAG (Đuôi bông vẫy nhịp nhàng)
        tz = zones.get("tail", {"x1": 215, "y1": 805, "x2": 480, "y2": 958})
        tx1, ty1 = max(0, int(tz["x1"])), max(0, int(tz["y1"]))
        tx2, ty2 = min(W, int(tz["x2"])), min(H, int(tz["y2"]))
        th, tw = ty2 - ty1, tx2 - tx1
        if th > 10 and tw > 10:
            tw_mask, tnx, _ = _radial_mask(th, tw, power=1.35)
            tw_cfg = anims.get("tail_wag", {})
            in_wag, w_prog = _window_envelope(t_sec, tw_cfg.get("windows_sec", [[2.6, 4.8], [7.2, 9.4]]))
            wag_amp = float(tw_cfg.get("amplitude_px", 16.0))
            if is_break or in_wag:
                env = 1.0 if is_break else math.sin(math.pi * w_prog)
                wag_wave = math.sin(2.0 * math.pi * (5.0 * t_sec / self.loop_sec))
                distal_factor = np.clip((tnx + 1.0) * 0.65, 0.2, 1.2)
                tdy = wag_amp * env * wag_wave * tw_mask * distal_factor
                tdx = (wag_amp * 0.45) * env * wag_wave * tw_mask * distal_factor
                _warp_patch_2d(frame_np, frame_np.copy(), tx1, ty1, tx2, ty2, tdx, tdy)

        # 5. FRONT PAW STRETCH (Duỗi bàn chân trước khi vươn vai `yawn_stretch` hoặc gõ nhịp giờ nghỉ)
        pz = zones.get("front_paw") or self.mascot.zones.get("front_paw")
        if pz:
            px1, py1 = max(0, int(pz["x1"])), max(0, int(pz["y1"]))
            px2, py2 = min(W, int(pz["x2"])), min(H, int(pz["y2"]))
            ph, pw = py2 - py1, px2 - px1
            if ph > 8 and pw > 8:
                pw_mask, _, _ = _radial_mask(ph, pw, power=1.4)
                ys_cfg = anims.get("yawn_stretch", {})
                in_p_stretch, ps_prog = _window_envelope(t_sec, ys_cfg.get("windows_sec", [[8.0, 9.6]]))
                paw_dx, paw_dy = 0.0, 0.0
                if in_p_stretch:
                    s_env = math.sin(math.pi * ps_prog)
                    paw_dx = 6.5 * s_env
                    paw_dy = 3.5 * s_env
                elif is_break:
                    paw_dy = 2.8 * math.sin(2.0 * math.pi * (3.0 * t_sec / self.loop_sec))
                if abs(paw_dx) > 0.2 or abs(paw_dy) > 0.2:
                    _warp_patch_2d(frame_np, frame_np.copy(), px1, py1, px2, py2, paw_dx * pw_mask, paw_dy * pw_mask)

        return frame_np

    def draw_mood_particles(
        self,
        fx_draw: ImageDraw.ImageDraw,
        t_sec: float,
        is_break: bool = False,
        milestone_hat: bool = False,
        has_dedicated_waking_art: bool = False,
    ) -> None:
        """
        Vẽ ký hiệu cảm xúc bay lên từ phía trên đầu Linh Vật:
          - Khi Học (`FOCUS` của thú cưng ngủ): Bong bóng chữ `z`, `Z`, `Z` (riêng `anime_lofi_girl` đang học sẽ bay nốt nhạc nhẹ `♪`).
          - Khi Nghỉ (`BREAK`): Nốt nhạc `♪`, `♫`, `♥` kèm má hồng ấm áp.
          - Milestone Easter Egg (`milestone_hat=True`): Vẽ chiếc Vương miện nhỏ / Mũ tiệc vàng đáng yêu khi người xem học tới phiên 3+.
        """
        active_zones = (
            self.mascot.waking_zones
            if (is_break and has_dedicated_waking_art and self.mascot.waking_zones)
            else self.mascot.zones
        )
        hz = active_zones.get("head", {"x1": 595, "y1": 675, "x2": 850, "y2": 900})
        anchor_x = int((hz.get("x1", 595) + hz.get("x2", 850)) * 0.52)
        anchor_y = int(hz.get("y1", 675) + 15)

        try:
            f_sm = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 22)
            f_md = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 28)
            f_lg = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 34)
            fonts = [f_sm, f_md, f_lg]
        except Exception:
            return

        prog = (t_sec / self.loop_sec) % 1.0
        is_anime_studying = self.mascot.species == "anime" and not is_break
        if is_break or is_anime_studying:
            symbols = ["♪", "♫", "♥"] if is_break else ["♪", "♫", "♪"]
            colors = [(130, 224, 170), (255, 215, 120), (255, 175, 190)]
        else:
            symbols = ["z", "Z", "Z"]
            colors = [(255, 238, 195), (255, 225, 165), (240, 215, 155)]

        for idx_s in range(3):
            p_i = (prog * 2.0 + idx_s / 3.0) % 1.0
            px = anchor_x + 25 + idx_s * 18 + math.sin(2.0 * math.pi * p_i + idx_s) * 14.0
            py = anchor_y - 10 - p_i * 95.0
            alpha = int(215 * math.sin(math.pi * p_i))
            if alpha > 15:
                col = colors[idx_s % len(colors)]
                fx_draw.text((px + 2, py + 2), symbols[idx_s], font=fonts[idx_s], fill=(10, 14, 22, int(alpha * 0.75)))
                fx_draw.text((px, py), symbols[idx_s], font=fonts[idx_s], fill=(col[0], col[1], col[2], alpha))

        if is_break:
            eye_l = hz.get("eye_left", [720, 792])
            cheek_x, cheek_y = int(eye_l[0] - 18), int(eye_l[1] + 22)
            cheek_alpha = int(85 + 35 * math.sin(2.0 * math.pi * (2.0 * t_sec / self.loop_sec)))
            fx_draw.ellipse(
                [(cheek_x - 18, cheek_y - 10), (cheek_x + 18, cheek_y + 10)],
                fill=(255, 135, 145, cheek_alpha),
            )

        if milestone_hat:
            # Milestone Easter Egg: Chiếc mũ tiệc / vương miện nhỏ màu vàng kim thưởng cho người học bền bỉ
            ca = hz.get("crown_anchor", [anchor_x, anchor_y - 8])
            cx, cy = int(ca[0]), int(ca[1] - 3 * math.sin(2.0 * math.pi * (3.0 * t_sec / self.loop_sec)))
            fx_draw.polygon(
                [(cx - 18, cy), (cx - 14, cy - 22), (cx - 5, cy - 10), (cx, cy - 26), (cx + 5, cy - 10), (cx + 14, cy - 22), (cx + 18, cy)],
                fill=(255, 212, 75, 235),
                outline=(140, 88, 18, 245),
            )
            fx_draw.ellipse([(cx - 3, cy - 30), (cx + 3, cy - 24)], fill=(255, 95, 110, 245))
