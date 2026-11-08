"""
Pomodoro HUD & Interactive Speech Bubble Renderer (`app/services/pomodoro/hud_renderer.py`)
===========================================================================================
1. Vẽ lớp phủ HUD Kính mờ (Frosted Glass Card `1920x1080` RGBA) bên cánh phải (`x=1230..1875`),
   chừa khoảng trống thông thoáng tại `y=550..740` để KHÔNG CHE chiếc cốc cà phê bốc khói,
   chú chó Golden Retriever (hoặc linh vật khác), đèn bàn học và cửa sổ mưa đêm.
2. Hệ thống Bong Bóng Hội Thoại (`Speech Bubble System`):
   - Giờ Nghỉ (`BREAK`): Hiển thị câu nhắc ấm áp của linh vật:
     `"Cậu chủ đến giờ nghỉ ngơi chút rồi!"` kèm biểu tượng chân cún vector và đuôi bong bóng trỏ thẳng vào đầu linh vật.
   - Giờ Chuẩn Bị (`PREP`) & Đầu Hiệp Học (`FOCUS`): Hiển thị lời chào động viên của linh vật.
"""

import re
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from app.services.pomodoro.mascot_engine import MascotPack
from app.services.pomodoro.scheduler import MODE_DISPLAY_META, PhaseSegment
from app.services.pomodoro.scene_composer import ThemePack


# Tọa độ chuẩn cho `video_renderer.py` ghép Đồng hồ đếm ngược và Sóng nhạc `showwaves`
CLOCK_CENTER_X = 1552
CLOCK_TOP_Y = 232
SHOWWAVES_X = 1260
SHOWWAVES_Y = 832
SHOWWAVES_W = 585
SHOWWAVES_H = 62


BREAK_SPEECH_MESSAGES: List[Tuple[str, str]] = [
    (
        "Cậu chủ đến giờ nghỉ ngơi chút rồi!",
        "Đứng dậy vươn vai, uống ngụm nước ấm và thư giãn mắt nhé~",
    ),
    (
        "Cậu chủ đến giờ nghỉ ngơi chút rồi!",
        "Giỏi lắm! Nhắm mắt 30 giây cho đỡ mỏi rồi đi uống nước nha~",
    ),
    (
        "Cậu chủ đến giờ nghỉ ngơi chút rồi!",
        "Mình tự hào về cậu chủ lắm! Nghỉ tay hít thở sâu chút nào~",
    ),
]

FINAL_BREAK_SPEECH: Tuple[str, str] = (
    "Cậu chủ đến giờ nghỉ ngơi chút rồi!",
    "Hoàn thành xuất sắc phiên học rồi đó! Thưởng cho bản thân chút gì ngon đi nào~",
)

PREP_SPEECH_MESSAGE: Tuple[str, str] = (
    "Chào cậu chủ! Chuẩn bị sách vở học cùng mình nhé!",
    "Lấy tách trà/cà phê ấm, mở sổ tay và sẵn sàng vào nhịp tập trung nào~",
)

FOCUS_START_SPEECH_MESSAGES: List[Tuple[str, str]] = [
    (
        "Tập trung 100% nào cậu chủ! Mình nằm canh cho nhé~",
        "Hãy gác điện thoại sang một bên để vào trạng thái Deep Focus nha!",
    ),
    (
        "Hiệp học mới bắt đầu rồi! Cố lên cậu chủ ơi~",
        "Mình sẽ nằm ngoan ở đây đợi đến giờ nghỉ sẽ gọi cậu chủ dậy nhé!",
    ),
]


def clean_font_text(text: str) -> str:
    """Loại bỏ các ký tự Emoji ngoài vùng hỗ trợ của font `arial.ttf` để tránh ô vuông `□`."""
    cleaned = "".join(
        ch for ch in (text or "") if ord(ch) < 0x2600 or ch in {"•", "–", "—", "♪", "♫", "♥", "★"}
    )
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _hex_to_rgba(hex_str: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    h = hex_str.lstrip("#")
    if len(h) == 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)
    return (245, 176, 65, alpha)


def _draw_paw_icon(
    dr: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    color: Tuple[int, int, int, int] = (255, 255, 255, 255),
) -> None:
    """Vẽ biểu tượng dấu chân thú cưng (Paw Print) sắc nét bằng vector."""
    dr.ellipse([(cx - 6, cy - 2), (cx + 6, cy + 8)], fill=color)
    for tx, ty in [(-8, -7), (-3, -10), (3, -10), (8, -7)]:
        dr.ellipse([(cx + tx - 2, cy + ty - 3), (cx + tx + 2, cy + ty + 2)], fill=color)


def get_phase_speech_message(phase: PhaseSegment) -> Tuple[str, str]:
    """Lấy cặp `(headline, subline)` bong bóng hội thoại của linh vật theo từng Phase."""
    if phase.phase_type == "PREP":
        return PREP_SPEECH_MESSAGE
    if phase.phase_type == "BREAK":
        if phase.session_num >= phase.total_sessions and phase.total_sessions > 1:
            return FINAL_BREAK_SPEECH
        idx = max(0, phase.session_num - 1) % len(BREAK_SPEECH_MESSAGES)
        return BREAK_SPEECH_MESSAGES[idx]
    idx = max(0, phase.session_num - 1) % len(FOCUS_START_SPEECH_MESSAGES)
    return FOCUS_START_SPEECH_MESSAGES[idx]


def draw_speech_bubble_on_image(
    img: Image.Image,
    mascot_pack: MascotPack,
    headline: str,
    subline: str,
    is_break: bool = True,
    badge_label: str = "ĐẾN GIỜ NGHỈ NGƠI",
) -> None:
    """
    Vẽ Bong Bóng Hội Thoại (Speech Bubble) phong cách Cozy Game/Anime phía trên Linh Vật,
    có mũi nhọn trỏ thẳng xuống đầu Linh Vật và tự động căn khung vừa khít độ dài chữ.
    """
    dr = ImageDraw.Draw(img)
    sb_cfg = mascot_pack.speech_bubble or {}
    box = sb_cfg.get("box", [180, 445, 910, 595])
    bx1, by1, bx2, by2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
    tail_tip = tuple(sb_cfg.get("tail_tip", [725, 680]))
    tail_l = tuple(sb_cfg.get("tail_base_left", [640, by2]))
    tail_r = tuple(sb_cfg.get("tail_base_right", [705, by2]))

    headline_clean = clean_font_text(headline)
    subline_clean = clean_font_text(subline)
    mascot_clean = clean_font_text(mascot_pack.display_name)
    badge_clean = clean_font_text(badge_label)

    accent_col = (88, 214, 141, 255) if is_break else (245, 176, 65, 255)
    bg_fill = (252, 248, 240, 244)
    shadow_fill = (8, 12, 20, 150)

    # 1. Đổ bóng mờ phía dưới Bong Bóng và Đuôi trỏ
    dr.polygon(
        [(tail_l[0] + 4, tail_l[1] + 5), (tail_r[0] + 4, tail_r[1] + 5), (tail_tip[0] + 4, tail_tip[1] + 6)],
        fill=shadow_fill,
    )
    dr.rounded_rectangle([(bx1 + 5, by1 + 6), (bx2 + 5, by2 + 6)], radius=28, fill=shadow_fill)

    # 2. Vẽ thân Bong Bóng và Đuôi tam giác trỏ xuống đầu Linh Vật
    dr.polygon([tail_l, tail_r, tail_tip], fill=bg_fill, outline=accent_col)
    dr.rounded_rectangle([(bx1, by1), (bx2, by2)], radius=28, fill=bg_fill, outline=accent_col, width=4)
    dr.line([(tail_l[0] + 4, by2), (tail_r[0] - 4, by2)], fill=bg_fill, width=6)

    font_bold = r"C:\Windows\Fonts\arialbd.ttf"
    font_reg = r"C:\Windows\Fonts\arial.ttf"
    try:
        f_tag = ImageFont.truetype(font_bold, 16)
        f_head = ImageFont.truetype(font_bold, 28)
        f_sub = ImageFont.truetype(font_reg, 19)
    except Exception:
        return

    max_inner_w = (bx2 - bx1) - 52
    hb = dr.textbbox((0, 0), headline_clean, font=f_head)
    if (hb[2] - hb[0]) > max_inner_w:
        f_head = ImageFont.truetype(font_bold, 24)

    # 3. Nhãn Pill góc trái trên của Bong bóng (tự động co giãn độ rộng theo text)
    pill_text = f"{mascot_clean}  •  {badge_clean}"
    ptb = dr.textbbox((0, 0), pill_text, font=f_tag)
    pill_w = (ptb[2] - ptb[0]) + 56
    pill_bg = (34, 153, 84, 255) if is_break else (211, 84, 0, 255)
    dr.rounded_rectangle([(bx1 + 24, by1 + 14), (bx1 + 24 + pill_w, by1 + 44)], radius=15, fill=pill_bg)
    _draw_paw_icon(dr, bx1 + 42, by1 + 30, color=(255, 255, 255, 255))
    dr.text((bx1 + 58, by1 + 19), pill_text, font=f_tag, fill=(255, 255, 255, 255))

    # 4. Câu nói chính (`"Cậu chủ đến giờ nghỉ ngơi chút rồi!"`)
    dr.text((bx1 + 26, by1 + 56), headline_clean, font=f_head, fill=(18, 24, 34, 255))

    # 5. Dòng phụ gợi ý hành động
    dr.text((bx1 + 26, by1 + 98), subline_clean, font=f_sub, fill=(68, 80, 98, 255))


def create_speech_bubble_overlay_png(
    out_png: Path,
    phase: PhaseSegment,
    mascot_pack: MascotPack,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """
    Xuất riêng lớp phủ Bong Bóng Hội Thoại (`1920x1080` RGBA) để FFmpeg có thể
    bật/tắt theo khung thời gian (hiển thị suốt giờ Nghỉ `BREAK`, hoặc 10 giây đầu hiệp Học `FOCUS`).
    """
    ov = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    headline, subline = get_phase_speech_message(phase)
    phase.speech_text = headline
    is_break = phase.phase_type == "BREAK"
    badge = "ĐẾN GIỜ NGHỈ NGƠI" if is_break else ("CHUẨN BỊ HỌC" if phase.phase_type == "PREP" else "BẮT ĐẦU TẬP TRUNG")
    draw_speech_bubble_on_image(
        img=ov,
        mascot_pack=mascot_pack,
        headline=headline,
        subline=subline,
        is_break=is_break,
        badge_label=badge,
    )
    out_png.parent.mkdir(parents=True, exist_ok=True)
    ov.save(str(out_png))
    return out_png


def create_phase_hud_overlay_png(
    out_png: Path,
    phase: PhaseSegment,
    mode: str,
    focus_min: float,
    break_min: float,
    mascot_pack: Optional[MascotPack] = None,
    theme_pack: Optional[ThemePack] = None,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """
    Vẽ lớp phủ HUD Kính mờ (Frosted Glass Card `1920x1080` RGBA) cho từng Phase.
    Bố cục v2.0 đặt gọn bên phải (`x=1230..1875`) và tách khoảng trống tại `y=545..745`
    để cốc cà phê bốc khói (`x=1330..1435, y=585..705`) luôn hiển thị trọn vẹn!
    """
    ov = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov)

    meta = MODE_DISPLAY_META.get(mode, MODE_DISPLAY_META["lofi_focus"])
    is_break = phase.phase_type == "BREAK"
    is_prep = phase.phase_type == "PREP"

    accent_hex = theme_pack.accent_hex if theme_pack else meta.get("accent_rgb", "#F5B041")
    break_hex = theme_pack.break_accent_hex if theme_pack else "#82E0AA"

    # 1. Khung HUD Pomodoro chính góc phải trên (x=1230..1875, y=38..542)
    cx1, cy1, cx2, cy2 = 1230, 38, 1875, 542
    border_col = _hex_to_rgba(break_hex, 225) if is_break else _hex_to_rgba(accent_hex, 230)
    if is_prep:
        border_col = (133, 193, 233, 225)

    dr.rounded_rectangle(
        [(cx1, cy1), (cx2, cy2)],
        radius=32,
        fill=(12, 16, 26, 218),
        outline=border_col,
        width=3,
    )

    font_bold = r"C:\Windows\Fonts\arialbd.ttf"
    font_reg = r"C:\Windows\Fonts\arial.ttf"
    f_badge = ImageFont.truetype(font_bold, 19)
    f_phase = ImageFont.truetype(font_bold, 30)
    f_sub = ImageFont.truetype(font_reg, 18)
    f_small = ImageFont.truetype(font_bold, 17)

    # Top Pill Badge (POMODORO 50/10 • NO MID-ROLL ADS)
    pill_x1, pill_y1, pill_x2, pill_y2 = cx1 + 28, cy1 + 20, cx2 - 28, cy1 + 60
    dr.rounded_rectangle([(pill_x1, pill_y1), (pill_x2, pill_y2)], radius=20, fill=(26, 34, 52, 240))
    top_text = f"POMODORO {focus_min:g}/{break_min:g}  •  NO MID-ROLL ADS"
    tb = dr.textbbox((0, 0), top_text, font=f_badge)
    dr.text(
        ((pill_x1 + pill_x2 - (tb[2] - tb[0])) // 2, pill_y1 + 9),
        top_text,
        font=f_badge,
        fill=(255, 225, 150, 255),
    )

    # Nhãn trạng thái Phase (FOCUS SESSION / BREAK TIME / PREP TIME)
    if is_prep:
        status_title = "PREP TIME  •  GET READY"
        status_col = (133, 193, 233, 255)
    elif is_break:
        status_title = f"BREAK TIME  •  REST {phase.session_num}/{phase.total_sessions}"
        status_col = _hex_to_rgba(break_hex, 255)
    else:
        status_title = f"FOCUS SESSION  {phase.session_num} / {phase.total_sessions}"
        status_col = _hex_to_rgba(accent_hex, 255)

    sb = dr.textbbox((0, 0), status_title, font=f_phase)
    dr.text(
        ((cx1 + cx2 - (sb[2] - sb[0])) // 2, cy1 + 78),
        status_title,
        font=f_phase,
        fill=status_col,
    )

    # Hộp chứa Đồng Hồ Đếm Ngược `MM:SS` (Tâm: x=1552, y=232)
    clk_x1, clk_y1, clk_x2, clk_y2 = cx1 + 36, cy1 + 128, cx2 - 36, cy1 + 322
    dr.rounded_rectangle(
        [(clk_x1, clk_y1), (clk_x2, clk_y2)],
        radius=24,
        fill=(7, 10, 16, 238),
        outline=(255, 255, 255, 55),
        width=2,
    )

    # Thanh Tiến Trình Các Hiệp (Session Dots)
    dots_y = cy1 + 344
    total_s = max(1, phase.total_sessions)
    dot_w = min(110, (cx2 - cx1 - 80 - (total_s - 1) * 12) // total_s)
    gap = 12
    total_dots_w = total_s * dot_w + (total_s - 1) * gap
    start_dx = (cx1 + cx2 - total_dots_w) // 2

    for s_i in range(1, total_s + 1):
        dx1 = start_dx + (s_i - 1) * (dot_w + gap)
        dx2 = dx1 + dot_w
        if s_i < phase.session_num or (s_i == phase.session_num and is_break):
            d_fill = (88, 214, 141, 240)
            lbl = f"S{s_i} DONE"
        elif s_i == phase.session_num and not is_prep:
            d_fill = _hex_to_rgba(accent_hex, 245)
            lbl = f"S{s_i} LIVE"
        else:
            d_fill = (42, 52, 72, 210)
            lbl = f"S{s_i}"
        dr.rounded_rectangle([(dx1, dots_y), (dx2, dots_y + 34)], radius=11, fill=d_fill)
        lb = dr.textbbox((0, 0), lbl, font=f_small)
        dr.text(
            ((dx1 + dx2 - (lb[2] - lb[0])) // 2, dots_y + 7),
            lbl,
            font=f_small,
            fill=(15, 20, 28, 255) if s_i <= phase.session_num and not is_prep else (190, 205, 225, 230),
        )

    # Thông tin Chế độ Nhạc & Companion góc dưới Card chính
    badge_txt = clean_font_text(meta["badge"])
    bb = dr.textbbox((0, 0), badge_txt, font=f_badge)
    dr.text(
        ((cx1 + cx2 - (bb[2] - bb[0])) // 2, cy1 + 402),
        badge_txt,
        font=f_badge,
        fill=(235, 242, 255, 245),
    )

    comp_name = clean_font_text(mascot_pack.display_name) if mascot_pack else "Golden Retriever"
    theme_name = clean_font_text(theme_pack.display_name) if theme_pack else "Autumn Rainy Study"
    hint_txt = (
        f"Break Time  •  Rest with {comp_name}"
        if is_break
        else f"Buddy: {comp_name}  •  {theme_name}"
    )
    hb = dr.textbbox((0, 0), hint_txt, font=f_sub)
    dr.text(
        ((cx1 + cx2 - (hb[2] - hb[0])) // 2, cy1 + 440),
        hint_txt,
        font=f_sub,
        fill=(185, 205, 228, 240),
    )

    # 2. Khung Sóng Nhạc & Thông tin Phiên (góc phải dưới x=1230..1875, y=748..922 — nằm dưới mặt bàn để lộ cốc cà phê)
    wx1, wy1, wx2, wy2 = 1230, 748, 1875, 922
    dr.rounded_rectangle(
        [(wx1, wy1), (wx2, wy2)],
        radius=24,
        fill=(12, 16, 26, 216),
        outline=border_col,
        width=2,
    )
    now_hdr = clean_font_text(f"NOW PLAYING  •  {phase.title.upper()}")
    if len(now_hdr) > 48:
        now_hdr = now_hdr[:45] + "..."
    track_names = clean_font_text("  •  ".join(t["display_name"] for t in phase.tracks_used[:2]))
    if len(track_names) > 52:
        track_names = track_names[:49] + "..."
    dr.text(
        (wx1 + 26, wy1 + 16),
        now_hdr,
        font=f_small,
        fill=(255, 215, 120, 250),
    )
    dr.text(
        (wx1 + 26, wy1 + 42),
        track_names,
        font=f_sub,
        fill=(215, 228, 245, 240),
    )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    ov.save(str(out_png))
    return out_png
