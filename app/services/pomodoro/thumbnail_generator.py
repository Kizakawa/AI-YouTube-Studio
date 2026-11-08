"""
Pomodoro YouTube Thumbnail Generator (`app/services/pomodoro/thumbnail_generator.py`)
====================================================================================
Tự động kết xuất ảnh Thumbnail YouTube chuẩn `1280x720` (CTR cao) cho mỗi video Pomodoro:
  - Phóng cận cảnh Linh Vật & Bàn Học ấm áp
  - Huy hiệu lớn: `"50/10 POMODORO"` + `"NO ADS"` (tự động căn giữa không tràn viền)
  - Bong bóng lời nhắc đáng yêu: `"Cậu chủ đến giờ nghỉ ngơi chút rồi!"` kèm Paw Icon vector.
"""

from pathlib import Path

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
    """Tạo ảnh Thumbnail YouTube `1280x720` bắt mắt cho video nhạc học tập."""
    src_bg = mascot_pack.resolved_bg_path
    if src_bg is None or not src_bg.exists():
        src_bg = generate_cozy_dog_study_background(out_jpg.parent / "_thumb_fallback.png", mascot_id=mascot_pack.id)

    im = Image.open(str(src_bg)).convert("RGBA").resize((1280, 720), Image.Resampling.LANCZOS)
    ov = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov)

    font_bold = r"C:\Windows\Fonts\arialbd.ttf"
    try:
        f_giant = ImageFont.truetype(font_bold, 54)
        f_sub = ImageFont.truetype(font_bold, 24)
        f_bub = ImageFont.truetype(font_bold, 25)
    except Exception:
        out_jpg.parent.mkdir(parents=True, exist_ok=True)
        im.convert("RGB").save(str(out_jpg), quality=95)
        return out_jpg

    # 1. Thẻ Badge lớn góc phải trên: POMODORO (tự động căn giữa trong hộp x=650..1248)
    bx1, by1, bx2, by2 = 650, 32, 1248, 205
    dr.rounded_rectangle([(bx1, by1), (bx2, by2)], radius=28, fill=(12, 16, 26, 232), outline=(245, 176, 65, 255), width=4)
    main_lbl = f"POMODORO {focus_min:g}/{break_min:g}"
    mb = dr.textbbox((0, 0), main_lbl, font=f_giant)
    if (mb[2] - mb[0]) > (bx2 - bx1 - 40):
        f_giant = ImageFont.truetype(font_bold, 46)
        mb = dr.textbbox((0, 0), main_lbl, font=f_giant)
    dr.text(((bx1 + bx2 - (mb[2] - mb[0])) // 2, by1 + 26), main_lbl, font=f_giant, fill=(255, 215, 90, 255))

    sub_lbl = f"STUDY WITH ME  •  {sessions} SESSIONS  •  NO ADS"
    sb = dr.textbbox((0, 0), sub_lbl, font=f_sub)
    dr.text(((bx1 + bx2 - (sb[2] - sb[0])) // 2, by1 + 118), sub_lbl, font=f_sub, fill=(240, 248, 255, 255))

    # 2. Bong bóng hội thoại phía trên Linh Vật
    sx1, sy1, sx2, sy2 = 55, 305, 655, 392
    dr.rounded_rectangle([(sx1 + 4, sy1 + 5), (sx2 + 4, sy2 + 5)], radius=24, fill=(8, 12, 20, 140))
    dr.polygon([(415, sy2), (465, sy2), (475, 442)], fill=(255, 250, 240, 244), outline=(88, 214, 141, 255))
    dr.rounded_rectangle([(sx1, sy1), (sx2, sy2)], radius=24, fill=(255, 250, 240, 244), outline=(88, 214, 141, 255), width=4)
    dr.line([(418, sy2), (462, sy2)], fill=(255, 250, 240, 244), width=6)

    _draw_paw_icon(dr, sx1 + 36, sy1 + 44, color=(34, 153, 84, 255))
    bub_txt = clean_font_text("Cậu chủ đến giờ nghỉ ngơi chút rồi!")
    dr.text((sx1 + 58, sy1 + 29), bub_txt, font=f_bub, fill=(20, 26, 36, 255))

    comp = Image.alpha_composite(im, ov).convert("RGB")
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    comp.save(str(out_jpg), quality=95)
    return out_jpg
