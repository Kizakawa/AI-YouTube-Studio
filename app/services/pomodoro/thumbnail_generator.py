"""
Pomodoro YouTube Thumbnail Generator (`app/services/pomodoro/thumbnail_generator.py`)
====================================================================================
Tự động kết xuất ảnh Thumbnail YouTube chuẩn `1280x720` (Module 5.3):
  - Ảnh linh vật + bàn học ấm áp + lớp phủ Film Grain nhẹ chuẩn Lo-Fi
  - Text lớn: `"50/10 POMODORO • X HOURS"`
  - Huy hiệu đôi: `"NO ADS"` + `"COZY STUDY"`
  - Bong bóng lời nhắc đáng yêu: `"Cậu chủ đến giờ nghỉ ngơi chút rồi!"`
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app.services.pomodoro.hud_renderer import _draw_paw_icon, clean_font_text
from app.services.pomodoro.mascot_engine import MascotPack, generate_cozy_dog_study_background
from app.services.pomodoro.scene_composer import ThemePack


def generate_youtube_thumbnail(
    out_jpg: Path,
    mascot_pack: MascotPack,
    theme_pack: ThemePack,
    focus_min: float = 50.0,
    break_min: float = 10.0,
    sessions: int = 4,
) -> Path:
    """Tạo ảnh Thumbnail YouTube `1280x720` chuyên nghiệp có lớp Film Grain nhẹ."""
    src_bg = mascot_pack.resolved_bg_path
    if (
        mascot_pack.id == "golden_retriever"
        and theme_pack.id != "autumn_cozy"
        and theme_pack.resolved_bg_path
        and theme_pack.resolved_bg_path.exists()
    ):
        src_bg = theme_pack.resolved_bg_path
    if src_bg is None or not src_bg.exists():
        src_bg = generate_cozy_dog_study_background(out_jpg.parent / "_thumb_fallback.png", mascot_id=mascot_pack.id)

    im = Image.open(str(src_bg)).convert("RGBA").resize((1280, 720), Image.Resampling.LANCZOS)

    # Phủ hạt Film Grain nhẹ lên nền trước khi vẽ chữ để chữ sắc nét mà ảnh vẫn có hồn Retro
    rgb_arr = np.array(im.convert("RGB"), dtype=np.int16)
    grain = np.random.RandomState(42).randint(-8, 9, size=rgb_arr.shape, dtype=np.int16)
    im = Image.fromarray(np.clip(rgb_arr + grain, 0, 255).astype(np.uint8)).convert("RGBA")

    ov = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov)

    font_bold = r"C:\Windows\Fonts\arialbd.ttf"
    try:
        f_giant = ImageFont.truetype(font_bold, 50)
        f_sub = ImageFont.truetype(font_bold, 22)
        f_pill = ImageFont.truetype(font_bold, 20)
        f_bub = ImageFont.truetype(font_bold, 24)
    except Exception:
        out_jpg.parent.mkdir(parents=True, exist_ok=True)
        im.convert("RGB").save(str(out_jpg), quality=95)
        return out_jpg

    total_hr = round(((focus_min + break_min) * sessions) / 60.0, 1)
    hr_lbl = f"{total_hr:g} HOURS" if total_hr >= 1.0 else f"{int(round((focus_min + break_min) * sessions))} MINS"

    # 1. Thẻ Badge lớn góc phải trên: "50/10 POMODORO • 4 HOURS" + Pills "NO ADS" & "COZY STUDY"
    bx1, by1, bx2, by2 = 620, 28, 1252, 236
    dr.rounded_rectangle([(bx1, by1), (bx2, by2)], radius=28, fill=(12, 16, 26, 234), outline=(245, 176, 65, 255), width=4)

    main_lbl = f"{focus_min:g}/{break_min:g} POMODORO"
    mb = dr.textbbox((0, 0), main_lbl, font=f_giant)
    if (mb[2] - mb[0]) > (bx2 - bx1 - 40):
        f_giant = ImageFont.truetype(font_bold, 42)
        mb = dr.textbbox((0, 0), main_lbl, font=f_giant)
    dr.text(((bx1 + bx2 - (mb[2] - mb[0])) // 2, by1 + 22), main_lbl, font=f_giant, fill=(255, 215, 90, 255))

    sub_lbl = clean_font_text(f"{hr_lbl}  •  {mascot_pack.display_name.upper()}  •  {theme_pack.display_name.upper()}")
    if len(sub_lbl) > 46:
        sub_lbl = sub_lbl[:44] + "..."
    sb = dr.textbbox((0, 0), sub_lbl, font=f_sub)
    dr.text(((bx1 + bx2 - (sb[2] - sb[0])) // 2, by1 + 96), sub_lbl, font=f_sub, fill=(235, 244, 255, 255))

    # Hai Pill Badge nổi bật: "NO ADS" & "COZY STUDY"
    dr.rounded_rectangle([(bx1 + 95, by1 + 146), (bx1 + 275, by1 + 190)], radius=18, fill=(88, 214, 141, 245))
    dr.text((bx1 + 142, by1 + 156), "NO ADS", font=f_pill, fill=(12, 18, 24, 255))

    dr.rounded_rectangle([(bx1 + 295, by1 + 146), (bx1 + 535, by1 + 190)], radius=18, fill=(245, 176, 65, 245))
    dr.text((bx1 + 342, by1 + 156), "COZY STUDY", font=f_pill, fill=(12, 18, 24, 255))

    # 2. Bong bóng hội thoại phía trên Linh Vật (tự căn chỉnh để không đè mặt nhân vật Anime)
    sy1 = 42 if mascot_pack.species == "anime" else 305
    sx1, sx2, sy2 = 42, 600, sy1 + 86
    dr.rounded_rectangle([(sx1 + 4, sy1 + 5), (sx2 + 4, sy2 + 5)], radius=24, fill=(8, 12, 20, 145))
    dr.polygon([(365, sy2), (415, sy2), (425, sy2 + 44)], fill=(255, 250, 240, 244), outline=(88, 214, 141, 255))
    dr.rounded_rectangle([(sx1, sy1), (sx2, sy2)], radius=24, fill=(255, 250, 240, 244), outline=(88, 214, 141, 255), width=4)
    dr.line([(368, sy2), (412, sy2)], fill=(255, 250, 240, 244), width=6)

    _draw_paw_icon(dr, sx1 + 34, sy1 + 43, color=(34, 153, 84, 255))
    bub_txt = clean_font_text("Cậu chủ đến giờ nghỉ ngơi chút rồi!")
    dr.text((sx1 + 56, sy1 + 28), bub_txt, font=f_bub, fill=(20, 26, 36, 255))

    comp = Image.alpha_composite(im, ov).convert("RGB")
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    comp.save(str(out_jpg), quality=95)
    return out_jpg
