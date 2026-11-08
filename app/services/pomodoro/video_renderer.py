"""
Pomodoro Phase Video Segment Renderer (`app/services/pomodoro/video_renderer.py`)
=================================================================================
Kết xuất từng phân đoạn video (`.mp4` Full HD 1080p) kết hợp:
  - Cảnh nền Cinemagraph Loop (`focus_loop.mp4` hoặc `break_loop.mp4`)
  - Hạt nhiễu Film Grain & Vignette cổ điển (`vfx_pipeline`)
  - Lớp phủ HUD Frosted Glass (`hud_png_path`)
  - Bong Bóng Hội Thoại tương tác của Linh Vật (`speech_bubble_png_path`:
    hiển thị xuyên suốt giờ Nghỉ `BREAK` với câu `"Cậu chủ đến giờ nghỉ ngơi chút rồi! ☕"`,
    và hiển thị 10 giây đầu hiệp Học `FOCUS` để cổ vũ người xem).
  - Đồng hồ đếm ngược `MM:SS` chính xác từng giây & sóng nhạc `showwaves` thực tế.
"""

import math
import os
import subprocess
from pathlib import Path
from typing import List, Optional

from app.services.pomodoro.hud_renderer import (
    CLOCK_CENTER_X,
    CLOCK_TOP_Y,
    SHOWWAVES_H,
    SHOWWAVES_W,
    SHOWWAVES_X,
    SHOWWAVES_Y,
)
from app.services.pomodoro.scheduler import PhaseSegment
from app.services.pomodoro.vfx_pipeline import VFXConfig, build_ffmpeg_vfx_post_filter


def render_phase_video_segment(
    phase: PhaseSegment,
    phase_wav_path: Path,
    bg_media_path: Path,
    hud_png_path: Path,
    work_dir: Path,
    speech_bubble_png_path: Optional[Path] = None,
    vfx_cfg: Optional[VFXConfig] = None,
    fps: int = 24,
    ffmpeg_exe: str = "ffmpeg",
) -> Path:
    """Render đoạn video hoàn chỉnh (`.mp4` có cả hình và tiếng) cho 1 Phase."""
    seg_mp4 = work_dir / f"vseg_{phase.index:02d}.mp4"
    dur = float(phase.duration_sec)
    total_int = int(math.ceil(dur))

    is_video_bg = bg_media_path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}
    font_escaped = "C\\:/Windows/Fonts/consolab.ttf"
    if not os.path.exists(r"C:\Windows\Fonts\consolab.ttf"):
        font_escaped = "C\\:/Windows/Fonts/arialbd.ttf"

    countdown_expr = (
        f"%{{eif\\:max(0,{total_int}-t)/60\\:d\\:2}}\\:%{{eif\\:mod(max(0,{total_int}-t),60)\\:d\\:2}}"
    )
    clock_color = "0x82E0AA" if phase.phase_type == "BREAK" else "0xFFFFFF"
    wave_color = "0x82E0AA@0.85" if phase.phase_type == "BREAK" else "0xF5B041@0.88"

    vfx_post = build_ffmpeg_vfx_post_filter(vfx_cfg) if vfx_cfg else ""
    bg_chain = f"scale=1920:1080:flags=lanczos,fps={fps},format=yuv420p"
    if vfx_post:
        bg_chain = f"{bg_chain},{vfx_post}"

    has_bubble = speech_bubble_png_path is not None and speech_bubble_png_path.exists()
    if phase.phase_type in {"BREAK", "PREP"}:
        # Giờ Nghỉ & Chuẩn Bị: Hiển thị bong bóng hội thoại xuyên suốt phiên
        bubble_enable = "gte(t,0.3)"
    else:
        # Giờ Học (Focus): Hiển thị bong bóng cổ vũ trong 10 giây đầu hiệp rồi tự ẩn để người xem tập trung
        show_sec = min(10.0, max(4.0, dur * 0.25))
        bubble_enable = f"between(t,0.4,{show_sec:.1f})"

    if has_bubble:
        fc = (
            f"[0:v]{bg_chain}[bg];"
            f"[1:v]scale=1920:1080[hud];"
            f"[3:v]scale=1920:1080[bub];"
            f"[2:a]showwaves=s={SHOWWAVES_W}x{SHOWWAVES_H}:mode=cline:rate={fps}:colors={wave_color}[sw];"
            f"[bg][hud]overlay=0:0:shortest=1[v_hud];"
            f"[v_hud][bub]overlay=0:0:enable='{bubble_enable}':shortest=1[v_bub];"
            f"[v_bub][sw]overlay={SHOWWAVES_X}:{SHOWWAVES_Y}:shortest=1,"
            f"drawtext=fontfile='{font_escaped}':text='{countdown_expr}':"
            f"fontsize=116:fontcolor={clock_color}:borderw=4:bordercolor=0x080B12:"
            f"x={CLOCK_CENTER_X}-text_w/2:y={CLOCK_TOP_Y}[outv]"
        )
    else:
        fc = (
            f"[0:v]{bg_chain}[bg];"
            f"[1:v]scale=1920:1080[hud];"
            f"[2:a]showwaves=s={SHOWWAVES_W}x{SHOWWAVES_H}:mode=cline:rate={fps}:colors={wave_color}[sw];"
            f"[bg][hud]overlay=0:0:shortest=1[v_hud];"
            f"[v_hud][sw]overlay={SHOWWAVES_X}:{SHOWWAVES_Y}:shortest=1,"
            f"drawtext=fontfile='{font_escaped}':text='{countdown_expr}':"
            f"fontsize=116:fontcolor={clock_color}:borderw=4:bordercolor=0x080B12:"
            f"x={CLOCK_CENTER_X}-text_w/2:y={CLOCK_TOP_Y}[outv]"
        )

    cmd = [ffmpeg_exe, "-y"]
    if is_video_bg:
        cmd += ["-stream_loop", "-1", "-i", str(bg_media_path)]
    else:
        cmd += ["-loop", "1", "-framerate", str(fps), "-i", str(bg_media_path)]

    cmd += [
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-i",
        str(hud_png_path),
        "-i",
        str(phase_wav_path),
    ]
    if has_bubble and speech_bubble_png_path is not None:
        cmd += [
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            str(speech_bubble_png_path),
        ]

    cmd += [
        "-filter_complex",
        fc,
        "-map",
        "[outv]",
        "-map",
        "2:a:0",
        "-t",
        f"{dur:.2f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        str(seg_mp4),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return seg_mp4


def concat_video_segments(
    video_segments: List[Path],
    final_mp4: Path,
    work_dir: Path,
    ffmpeg_exe: str = "ffmpeg",
) -> Path:
    """Ghép nối không nén lại (`-c copy`) toàn bộ các phân đoạn Phase thành file MP4 hoàn chỉnh."""
    vconcat_txt = work_dir / "video_concat.txt"
    vconcat_txt.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in video_segments),
        encoding="utf-8",
    )
    subprocess.run(
        [
            ffmpeg_exe,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(vconcat_txt),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(final_mp4),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return final_mp4
