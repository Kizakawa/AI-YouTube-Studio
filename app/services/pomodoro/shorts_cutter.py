"""
Pomodoro YouTube Shorts 60s Teaser Cutter (`app/services/pomodoro/shorts_cutter.py`)
====================================================================================
Tự động trích xuất đoạn Highlight hấp dẫn nhất (45–56 giây) ngay tại thời điểm
Đồng hồ Pomodoro đếm về `00:00` ➔ Chuông Singing Bowl ngân vang ➔ Linh vật thức dậy
hiện bong bóng `"Cậu chủ đến giờ nghỉ ngơi chút rồi!"`, và chuyển đổi sang khung dọc
`1080x1920` (9:16) chuẩn YouTube Shorts để kéo lưu lượng người xem sang Video Dài (Phase 4).
"""

import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image, ImageDraw, ImageFont

from app.services.pomodoro.hud_renderer import _draw_paw_icon, clean_font_text
from app.services.pomodoro.video_renderer import detect_optimal_h264_encoder


def _create_shorts_frame_overlay(
    out_png: Path,
    mascot_name: str,
    theme_name: str,
    pomodoro_cfg: str,
) -> Path:
    """Vẽ khung Header & CTA Footer kính mờ `1080x1920` (RGBA) cho YouTube Shorts."""
    ov = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov)

    font_bold = r"C:\Windows\Fonts\arialbd.ttf"
    font_reg = r"C:\Windows\Fonts\arial.ttf"
    f_top = ImageFont.truetype(font_bold, 42)
    f_sub = ImageFont.truetype(font_reg, 26)
    f_cta = ImageFont.truetype(font_bold, 30)
    f_small = ImageFont.truetype(font_bold, 22)

    # 1. Thẻ Hook phía trên (y=170..415)
    dr.rounded_rectangle(
        [(54, 170), (1026, 415)],
        radius=34,
        fill=(12, 16, 26, 222),
        outline=(245, 176, 65, 240),
        width=4,
    )
    t1 = "STUDY WITH ME  •  POMODORO"
    b1 = dr.textbbox((0, 0), t1, font=f_top)
    dr.text(((1080 - (b1[2] - b1[0])) // 2, 206), t1, font=f_top, fill=(255, 215, 95, 255))

    t2 = clean_font_text(f"{mascot_name}  •  {theme_name}")
    b2 = dr.textbbox((0, 0), t2, font=f_sub)
    dr.text(((1080 - (b2[2] - b2[0])) // 2, 276), t2, font=f_sub, fill=(235, 244, 255, 255))

    pill_txt = f"TIMER {pomodoro_cfg.upper()}  •  ZERO MID-ROLL ADS"
    bp = dr.textbbox((0, 0), pill_txt, font=f_small)
    pw = (bp[2] - bp[0]) + 68
    px1 = (1080 - pw) // 2
    dr.rounded_rectangle([(px1, 334), (px1 + pw, 384)], radius=18, fill=(34, 153, 84, 245))
    _draw_paw_icon(dr, px1 + 26, 360, color=(255, 255, 255, 255))
    dr.text((px1 + 46, 346), pill_txt, font=f_small, fill=(255, 255, 255, 255))

    # 2. Viền sáng bao quanh khung video trung tâm (x=0..1080, y=656..1264)
    dr.rectangle([(0, 654), (1080, 658)], fill=(245, 176, 65, 215))
    dr.rectangle([(0, 1264), (1080, 1268)], fill=(88, 214, 141, 215))

    # 3. Thẻ CTA phía dưới (y=1445..1665) dẫn người xem bấm vào Video Dài liên quan
    dr.rounded_rectangle(
        [(64, 1445), (1016, 1665)],
        radius=32,
        fill=(12, 16, 26, 224),
        outline=(88, 214, 141, 235),
        width=3,
    )
    c1 = "FULL STUDY SESSION ON CHANNEL"
    bc1 = dr.textbbox((0, 0), c1, font=f_cta)
    dr.text(((1080 - (bc1[2] - bc1[0])) // 2, 1485), c1, font=f_cta, fill=(130, 224, 170, 255))

    c2 = "Tap Related Video Below to Study Together!"
    bc2 = dr.textbbox((0, 0), c2, font=f_sub)
    dr.text(((1080 - (bc2[2] - bc2[0])) // 2, 1545), c2, font=f_sub, fill=(235, 242, 255, 245))

    c3 = "Wait for the Break Bell -> Mascot Wakes Up!"
    bc3 = dr.textbbox((0, 0), c3, font=f_small)
    dr.text(((1080 - (bc3[2] - bc3[0])) // 2, 1598), c3, font=f_small, fill=(255, 215, 130, 240))

    out_png.parent.mkdir(parents=True, exist_ok=True)
    ov.save(str(out_png))
    return out_png


def cut_pomodoro_short_teaser(
    long_video_path: Path,
    manifest: Dict[str, Any],
    out_short_mp4: Optional[Path] = None,
    teaser_duration_sec: float = 54.0,
    pre_break_sec: float = 14.0,
    fps: int = 30,
    ffmpeg_exe: str = "ffmpeg",
) -> Path:
    """
    Cắt tự động 1 video YouTube Shorts dọc `1080x1920` (mặc định 54 giây < trần 60s Shorts)
    ngay tại điểm chuyển giao từ cuối Hiệp Học 1 sang Giờ Nghỉ 1 (có tiếng chuông + linh vật thức dậy).
    """
    if out_short_mp4 is None:
        out_short_mp4 = long_video_path.parent / f"{long_video_path.stem}_short_teaser.mp4"

    total_dur = float(manifest.get("total_duration_sec", 60.0))
    phases = manifest.get("phases", [])
    break_phases = [p for p in phases if p.get("phase_type") == "BREAK"]

    if break_phases:
        brk_start = float(break_phases[0].get("start_sec", 20.0))
        start_sec = max(0.0, brk_start - pre_break_sec)
    else:
        start_sec = max(0.0, min(10.0, total_dur * 0.2))

    clip_dur = min(teaser_duration_sec, max(8.0, total_dur - start_sec))
    ov_png = out_short_mp4.parent / "_tmp_shorts_overlay.png"
    _create_shorts_frame_overlay(
        out_png=ov_png,
        mascot_name=str(manifest.get("mascot_display", manifest.get("mascot", "Golden Retriever"))),
        theme_name=str(manifest.get("theme_display", manifest.get("theme", "Autumn Rainy Study"))),
        pomodoro_cfg=str(manifest.get("pomodoro_config", "50/10")),
    )

    v_enc, v_enc_args = detect_optimal_h264_encoder(ffmpeg_exe)
    fade_out_st = max(1.0, clip_dur - 1.2)

    fc = (
        f"[0:v]fps={fps},split=2[v_bg_src][v_fg_src];"
        f"[v_bg_src]scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,boxblur=24:4,eq=brightness=-0.14:saturation=1.1[v_bg];"
        f"[v_fg_src]scale=1080:608:flags=lanczos[v_fg];"
        f"[v_bg][v_fg]overlay=0:656[v_comp];"
        f"[1:v]format=rgba[ov];"
        f"[v_comp][ov]overlay=0:0,"
        f"fade=t=in:st=0:d=0.5,fade=t=out:st={fade_out_st:.2f}:d=1.0,setsar=1,format=yuv420p[outv];"
        f"[0:a]afade=t=in:st=0:d=0.5,afade=t=out:st={fade_out_st:.2f}:d=1.0[outa]"
    )

    res = subprocess.run(
        [
            ffmpeg_exe,
            "-y",
            "-v",
            "error",
            "-ss",
            f"{start_sec:.2f}",
            "-t",
            f"{clip_dur:.2f}",
            "-i",
            str(long_video_path),
            "-i",
            str(ov_png),
            "-filter_complex",
            fc,
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            "-t",
            f"{clip_dur:.2f}",
            "-c:v",
            v_enc,
            *v_enc_args,
            "-r",
            str(fps),
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(out_short_mp4),
        ],
        capture_output=True,
        text=True,
    )
    ov_png.unlink(missing_ok=True)
    if res.returncode != 0:
        raise RuntimeError(f"cut_pomodoro_short_teaser failed: {res.stderr[-500:]}")
    return out_short_mp4
