"""
Mascot Animation Engine (`app/services/pomodoro/mascot_engine.py`)
==================================================================
Hệ thống điều khiển chuyển động linh vật đa vùng (Multi-Zone 2D Skeletal Mesh Warping):
  1. `idle_breathing`: Nhịp thở phập phồng sâu và rõ nét (biên độ 8.5–9.5px + giãn nở lồng ngực).
  2. `ear_twitch`: Đôi tai giật nhẹ (double-twitch) định kỳ mỗi vài giây.
  3. `tail_wag`: Đuôi bông vẫy nhịp nhàng theo chu kỳ (và vẫy liên tục khi sang giờ nghỉ Break).
  4. `sleep_shift`: Cử động xoay/nghiêng đầu nhẹ khi đang ngủ.
  5. `yawn_stretch`: Vươn vai / duỗi chân trước định kỳ + bong bóng ngủ `Zzz` bay lên.
  6. `break_wakeup`: Tư thế tỉnh giấc vui vẻ trong giờ Giải lao (`BREAK`) kèm nốt nhạc `♪ ☕ ✨`.

Hỗ trợ đa linh vật qua cấu hình `assets/mascots/<mascot_id>/mascot.json`:
  - `golden_retriever` (Chú chó Golden Retriever)
  - `calico_cat` (Mèo Tam Thể)
  - `corgi_puppy` (Chó Corgi)
  - `anime_lofi_girl` (Cô gái Anime Lofi)
  - `capybara` (Capybara đội quýt)
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
    placement: Dict[str, int]
    zones: Dict[str, Dict[str, Any]]
    animations: Dict[str, Dict[str, Any]]
    speech_bubble: Dict[str, Any]
    pack_dir: Optional[Path] = None
    resolved_bg_path: Optional[Path] = None


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


def load_mascot_pack(
    mascot_id: str = "golden_retriever",
    mascots_root: Path = DEFAULT_MASCOTS_ROOT,
) -> MascotPack:
    """Đọc cấu hình `mascot.json` và tự động tìm đường dẫn ảnh nền tương ứng."""
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
                "box": [180, 445, 910, 595],
                "tail_tip": [725, 680],
                "tail_base_left": [640, 595],
                "tail_base_right": [705, 595],
            },
        }

    bg_name = data.get("background_image", "cozy_lofi_study_room_bg.jpg")
    candidates = [
        cfg_path.parent / bg_name,
        DEFAULT_CATALOG_BGM_ROOT / bg_name,
        DEFAULT_MASTER_BGM_ROOT.parent / bg_name,
        DEFAULT_CATALOG_BGM_ROOT / "cozy_lofi_study_room_bg.jpg",
    ]
    resolved_bg = next((p for p in candidates if p.exists()), None)

    return MascotPack(
        id=data.get("id", mascot_id),
        display_name=data.get("display_name", mascot_id),
        species=data.get("species", "dog"),
        companion_title=data.get("companion_title", "Cozy Study Companion"),
        description=data.get("description", ""),
        background_image=bg_name,
        placement=data.get("placement", {"x": 215, "y": 630, "width": 640, "height": 330}),
        zones=data.get("zones", {}),
        animations=data.get("animations", {}),
        speech_bubble=data.get("speech_bubble", {}),
        pack_dir=cfg_path.parent,
        resolved_bg_path=resolved_bg,
    )


def generate_cozy_dog_study_background(
    out_png: Path,
    width: int = 1920,
    height: int = 1080,
    mascot_id: str = "golden_retriever",
) -> Path:
    """
    Vẽ cảnh nền dự phòng Cozy Study Room + Mascot Companion (1920x1080)
    khi chưa có file ảnh nền trên ổ đĩa.
    """
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
    """
    Nội suy song tuyến tính (Bilinear Patch Warp) mượt mà tại vùng `(x1, y1, x2, y2)`
    với trường dịch chuyển `(dx_map, dy_map)`, giữ nguyên viền ngoài để không bị rách ảnh.
    """
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
    c00 = patch[y0, x0]
    c01 = patch[y0, x1_i]
    c10 = patch[y1_i, x0]
    c11 = patch[y1_i, x1_i]

    warped = (
        c00 * (1.0 - wx) * (1.0 - wy)
        + c01 * wx * (1.0 - wy)
        + c10 * (1.0 - wx) * wy
        + c11 * wx * wy
    )
    frame_np[y1:y2, x1:x2] = np.clip(warped, 0, 255).astype(np.uint8)


def _radial_mask(h: int, w: int, power: float = 1.6) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Tạo mặt nạ elip mềm từ tâm `(nx, ny)` trong khoảng `[-1..1]`."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    ny = (yy / float(max(1, h - 1))) * 2.0 - 1.0
    nx = (xx / float(max(1, w - 1))) * 2.0 - 1.0
    weight = np.clip(1.0 - (nx * nx + ny * ny), 0.0, 1.0) ** power
    return weight, nx, ny


def _window_envelope(t_sec: float, windows: List[List[float]]) -> Tuple[bool, float]:
    """Kiểm tra thời điểm `t_sec` có nằm trong khung giờ cử động `[start, end]` không và trả về tiến trình `[0..1]`."""
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
      6. `break_wakeup`: Ngóc đầu tỉnh táo trong giờ nghỉ Giải lao (`BREAK`) + nốt nhạc vui tươi
    """

    def __init__(self, mascot: MascotPack, loop_sec: float = 10.0, fps: int = 24):
        self.mascot = mascot
        self.loop_sec = loop_sec
        self.fps = fps

    def apply_mascot_animations(
        self,
        frame_np: np.ndarray,
        base_np: np.ndarray,
        t_sec: float,
        is_break: bool = False,
    ) -> np.ndarray:
        zones = self.mascot.zones
        anims = self.mascot.animations
        H, W = frame_np.shape[:2]

        # 1. IDLE BREATHING + YAWN STRETCH trên vùng ngực/lưng (`body_breath`)
        bz = zones.get("body_breath", {"x1": 310, "y1": 635, "x2": 685, "y2": 875})
        bx1, by1 = max(0, int(bz["x1"])), max(0, int(bz["y1"]))
        bx2, by2 = min(W, int(bz["x2"])), min(H, int(bz["y2"]))
        bh, bw = by2 - by1, bx2 - bx1
        if bh > 10 and bw > 10:
            w_mask, nx, ny = _radial_mask(bh, bw, power=1.45)
            b_cfg = anims.get("idle_breathing", {})
            amp_y = float(b_cfg.get("amplitude_px", 9.5))
            expand_x = float(b_cfg.get("expand_px", 4.5))
            # Chu kỳ thở: 3 nhịp trong 10 giây (mỗi nhịp ~3.33s)
            breath_phase = math.sin(2.0 * math.pi * (3.0 * t_sec / self.loop_sec))

            # Kiểm tra cử động vươn vai (`yawn_stretch`)
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
            hw_mask, hnx, hny = _radial_mask(hh, hw, power=1.5)
            ss_cfg = anims.get("sleep_shift", {})
            in_shift, sh_prog = _window_envelope(t_sec, ss_cfg.get("windows_sec", [[4.2, 5.6]]))
            shift_env = math.sin(math.pi * sh_prog) if in_shift else 0.0

            # Nhịp gật đầu nhẹ theo hơi thở + xoay đầu định kỳ
            head_bob_y = -4.2 * math.sin(2.0 * math.pi * (3.0 * t_sec / self.loop_sec) - 0.35)
            head_dx = float(ss_cfg.get("shift_x_px", 7.0)) * shift_env
            head_dy = head_bob_y + float(ss_cfg.get("shift_y_px", -6.0)) * shift_env

            if is_break:
                wk_cfg = anims.get("break_wakeup", {})
                lift_y = float(wk_cfg.get("head_lift_px", -14.0))
                # Trong giờ nghỉ, linh vật ngóc đầu lên và lắc nhẹ vui vẻ theo nhịp nhạc
                head_dy = lift_y + 4.5 * math.sin(2.0 * math.pi * (2.0 * t_sec / self.loop_sec))
                head_dx = 5.0 * math.sin(2.0 * math.pi * (1.0 * t_sec / self.loop_sec))

            _warp_patch_2d(
                frame_np,
                frame_np.copy(),
                hx1,
                hy1,
                hx2,
                hy2,
                head_dx * hw_mask,
                head_dy * hw_mask,
            )

        # 3. EAR TWITCH (Đôi tai giật nhẹ double-twitch rất sinh động)
        ez = zones.get("ear", {"x1": 585, "y1": 700, "x2": 705, "y2": 855})
        ex1, ey1 = max(0, int(ez["x1"])), max(0, int(ez["y1"]))
        ex2, ey2 = min(W, int(ez["x2"])), min(H, int(ez["y2"]))
        eh, ew = ey2 - ey1, ex2 - ex1
        if eh > 10 and ew > 10:
            ew_mask, enx, eny = _radial_mask(eh, ew, power=1.4)
            et_cfg = anims.get("ear_twitch", {})
            in_ear, e_prog = _window_envelope(t_sec, et_cfg.get("windows_sec", [[1.4, 2.3], [5.8, 6.7]]))
            ear_dx, ear_dy = 0.0, 0.0
            if in_ear:
                # Double-twitch: 2 nhịp giật tai nhanh liên tiếp
                twitch_pulse = abs(math.sin(2.0 * math.pi * e_prog)) * math.sin(math.pi * e_prog)
                ear_dy = float(et_cfg.get("shift_y_px", -14.0)) * twitch_pulse
                ear_dx = float(et_cfg.get("shift_x_px", 5.0)) * twitch_pulse
            elif is_break:
                # Trong giờ nghỉ, tai vểnh cao và cử động nhẹ
                ear_dy = -10.0 + 4.0 * math.sin(2.0 * math.pi * (4.0 * t_sec / self.loop_sec))
                ear_dx = 3.5 * math.cos(2.0 * math.pi * (4.0 * t_sec / self.loop_sec))

            if abs(ear_dx) > 0.2 or abs(ear_dy) > 0.2:
                _warp_patch_2d(
                    frame_np,
                    frame_np.copy(),
                    ex1,
                    ey1,
                    ex2,
                    ey2,
                    ear_dx * ew_mask,
                    ear_dy * ew_mask,
                )

        # 4. TAIL WAG (Đuôi bông vẫy nhịp nhàng)
        tz = zones.get("tail", {"x1": 215, "y1": 805, "x2": 480, "y2": 958})
        tx1, ty1 = max(0, int(tz["x1"])), max(0, int(tz["y1"]))
        tx2, ty2 = min(W, int(tz["x2"])), min(H, int(tz["y2"]))
        th, tw = ty2 - ty1, tx2 - tx1
        if th > 10 and tw > 10:
            tw_mask, tnx, tny = _radial_mask(th, tw, power=1.35)
            tw_cfg = anims.get("tail_wag", {})
            in_wag, w_prog = _window_envelope(t_sec, tw_cfg.get("windows_sec", [[2.6, 4.8], [7.2, 9.4]]))
            wag_amp = float(tw_cfg.get("amplitude_px", 16.0))
            if is_break or in_wag:
                env = 1.0 if is_break else math.sin(math.pi * w_prog)
                wag_wave = math.sin(2.0 * math.pi * (5.0 * t_sec / self.loop_sec))
                # Đầu mút đuôi lắc mạnh hơn gốc đuôi
                distal_factor = np.clip((tnx + 1.0) * 0.65, 0.2, 1.2)
                tdy = wag_amp * env * wag_wave * tw_mask * distal_factor
                tdx = (wag_amp * 0.45) * env * wag_wave * tw_mask * distal_factor
                _warp_patch_2d(frame_np, frame_np.copy(), tx1, ty1, tx2, ty2, tdx, tdy)

        return frame_np

    def draw_mood_particles(
        self,
        fx_draw: ImageDraw.ImageDraw,
        t_sec: float,
        is_break: bool = False,
    ) -> None:
        """
        Vẽ các ký hiệu cảm xúc bay lên từ phía trên đầu Linh Vật:
          - Khi đang Học (`FOCUS`): Bong bóng chữ `z`, `Z`, `Zzz` bay lên êm dịu.
          - Khi đến Giờ Nghỉ (`BREAK`): Nốt nhạc `♪`, `♫`, `☕`, `✨` kèm má hồng ấm áp.
        """
        hz = self.mascot.zones.get("head", {"x1": 595, "y1": 675, "x2": 850, "y2": 900})
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
        symbols = ["♪", "♫", "♥"] if is_break else ["z", "Z", "Z"]
        colors = (
            [(130, 224, 170), (255, 215, 120), (255, 185, 195)]
            if is_break
            else [(255, 238, 195), (255, 225, 165), (240, 215, 155)]
        )

        for idx_s in range(3):
            p_i = (prog * 2.0 + idx_s / 3.0) % 1.0
            px = anchor_x + 25 + idx_s * 18 + math.sin(2.0 * math.pi * p_i + idx_s) * 14.0
            py = anchor_y - 10 - p_i * 95.0
            alpha = int(215 * math.sin(math.pi * p_i))
            if alpha > 15:
                col = colors[idx_s % len(colors)]
                # Viền tối mỏng để chữ nổi bật trên mọi nền
                fx_draw.text((px + 2, py + 2), symbols[idx_s], font=fonts[idx_s], fill=(10, 14, 22, int(alpha * 0.75)))
                fx_draw.text((px, py), symbols[idx_s], font=fonts[idx_s], fill=(col[0], col[1], col[2], alpha))

        if is_break:
            # Thêm đốm má hồng đáng yêu khi linh vật thức dậy nhắc giờ nghỉ
            eye_l = hz.get("eye_left", [720, 792])
            cheek_x, cheek_y = int(eye_l[0] - 18), int(eye_l[1] + 22)
            cheek_alpha = int(85 + 35 * math.sin(2.0 * math.pi * (2.0 * t_sec / self.loop_sec)))
            fx_draw.ellipse(
                [(cheek_x - 18, cheek_y - 10), (cheek_x + 18, cheek_y + 10)],
                fill=(255, 135, 145, cheek_alpha),
            )
