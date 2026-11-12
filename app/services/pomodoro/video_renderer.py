"""
Pomodoro Phase Video Segment Renderer (`app/services/pomodoro/video_renderer.py`)
=================================================================================
Kết xuất từng phân đoạn video (`.mp4` Full HD 1080p) tích hợp:
  - Tự động dò phần cứng tăng tốc GPU (`h264_nvenc` / `h264_qsv`) hoặc `libx264 -preset veryfast` (Module 5.2).
  - Cảnh nền Cinemagraph Loop (`focus_loop.mp4` / `break_loop.mp4` / `milestone_hat`).
  - Hiệu ứng chuyển động Fade-in 0.5s & Fade-out 1.0s mượt mà trên Bong Bóng Hội Thoại (Module 3.2)
    + Bong bóng cột mốc 50% thời gian hiệp học (`milestone_bubble_png_path`) tối ưu O(1) PTS shift (không giải mã thừa RGBA).
  - Đồng hồ đếm ngược `MM:SS` chính xác từng giây (`trunc(t)`), CFR khóa cứng & không gian màu chuẩn BT.709.
"""

import math
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

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
from app.utils.ffmpeg.core_encoder import BT709_COLOR_ARGS, _supports_fps_mode


@lru_cache(maxsize=4)
def detect_optimal_h264_encoder(ffmpeg_exe: str = "ffmpeg") -> Tuple[str, List[str]]:
    """
    Tự động kiểm tra phần cứng GPU (`h264_nvenc`, `h264_qsv`) ở chuẩn Full HD 1920x1080;
    tôn trọng `settings.ENABLE_GPU_ACCELERATION` và fallback mượt về `libx264 -preset veryfast -crf 20`.
    """
    try:
        from config.settings import settings
        if not getattr(settings, "ENABLE_GPU_ACCELERATION", True):
            return "libx264", ["-preset", "veryfast", "-crf", "20"]
    except Exception:
        pass

    candidates = [
        ("h264_nvenc", ["-preset", "p4", "-cq", "20"]),
        ("h264_qsv", ["-preset", "veryfast", "-global_quality", "20"]),
    ]
    for enc_name, enc_args in candidates:
        probe_cmd = [
            ffmpeg_exe,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1920x1080:d=0.2:r=24",
            "-frames:v",
            "3",
            "-c:v",
            enc_name,
            *enc_args,
            "-pix_fmt",
            "yuv420p",
            "-f",
            "null",
            "-",
        ]
        try:
            res = subprocess.run(probe_cmd, capture_output=True, timeout=4)
            if res.returncode == 0:
                return enc_name, enc_args
        except Exception:
            continue
    return "libx264", ["-preset", "veryfast", "-crf", "20"]


def render_phase_video_segment(
    phase: PhaseSegment,
    phase_wav_path: Path,
    bg_media_path: Path,
    hud_png_path: Path,
    work_dir: Path,
    speech_bubble_png_path: Optional[Path] = None,
    milestone_bubble_png_path: Optional[Path] = None,
    vfx_cfg: Optional[VFXConfig] = None,
    fps: int = 24,
    use_hw_accel: bool = True,
    ffmpeg_exe: str = "ffmpeg",
) -> Path:
    """Render đoạn video hoàn chỉnh (`.mp4` có cả hình và tiếng, CFR + BT.709) cho 1 Phase."""
    seg_mp4 = work_dir / f"vseg_{phase.index:02d}.mp4"
    dur = float(phase.duration_sec)
    total_int = int(math.ceil(dur))

    is_video_bg = bg_media_path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}
    is_cached_cinemagraph = is_video_bg and "loops_cache" in str(bg_media_path)

    font_escaped = "C\\:/Windows/Fonts/consolab.ttf"
    if not os.path.exists(r"C:\Windows\Fonts\consolab.ttf"):
        font_escaped = "C\\:/Windows/Fonts/arialbd.ttf"

    # Dùng trunc(t) để hiển thị đủ trọn 1.0s đầu tiên tại mốc chẵn (ví dụ 50:00 không bị hụt về 49:59 ở frame 1)
    countdown_expr = (
        f"%{{eif\\:max(0,{total_int}-trunc(t))/60\\:d\\:2}}\\:%{{eif\\:mod(max(0,{total_int}-trunc(t)),60)\\:d\\:2}}"
    )
    clock_color = "0x82E0AA" if phase.phase_type == "BREAK" else "0xFFFFFF"
    wave_color = "0x82E0AA@0.85" if phase.phase_type == "BREAK" else "0xF5B041@0.88"

    if is_cached_cinemagraph:
        bg_chain = f"fps={fps},format=yuv420p"
    else:
        vfx_post = build_ffmpeg_vfx_post_filter(vfx_cfg) if vfx_cfg else ""
        bg_chain = f"scale=1920:1080:flags=lanczos,fps={fps},format=yuv420p"
        if vfx_post:
            bg_chain = f"{bg_chain},{vfx_post}"

    has_bubble = speech_bubble_png_path is not None and speech_bubble_png_path.exists()
    has_milestone = (
        phase.phase_type == "FOCUS"
        and dur >= 150.0
        and milestone_bubble_png_path is not None
        and milestone_bubble_png_path.exists()
    )

    fc_lines = [
        f"[0:v]{bg_chain}[bg]",
        "[1:v]format=rgba[hud]",
        f"[2:a]showwaves=s={SHOWWAVES_W}x{SHOWWAVES_H}:mode=cline:rate={fps}:colors={wave_color}[sw]",
        "[bg][hud]overlay=0:0[v_stage0]",
    ]
    cur_v = "v_stage0"
    next_input_idx = 3
    bubble_stream_dur = dur

    if has_bubble:
        if phase.phase_type in {"BREAK", "PREP"}:
            fade_out_st = max(1.0, dur - 1.2)
            bubble_stream_dur = dur
            fc_lines.append(
                f"[{next_input_idx}:v]format=rgba,"
                f"fade=t=in:st=0.3:d=0.5:alpha=1,fade=t=out:st={fade_out_st:.2f}:d=1.0:alpha=1[bub]"
            )
            fc_lines.append(f"[{cur_v}][bub]overlay=0:0:eof_action=pass[v_stage1]")
        else:
            show_end = min(9.5, max(4.5, dur * 0.22))
            fade_out_st = max(1.0, show_end - 1.0)
            bubble_stream_dur = show_end
            fc_lines.append(
                f"[{next_input_idx}:v]format=rgba,"
                f"fade=t=in:st=0.4:d=0.5:alpha=1,fade=t=out:st={fade_out_st:.2f}:d=1.0:alpha=1[bub]"
            )
            fc_lines.append(f"[{cur_v}][bub]overlay=0:0:eof_action=pass[v_stage1]")
        cur_v = "v_stage1"
        next_input_idx += 1

    milestone_window_dur = 0.0
    if has_milestone:
        m_st = round(dur * 0.5, 2)
        m_end = round(min(dur - 1.5, m_st + 8.0), 2)
        milestone_window_dur = max(2.0, round(m_end - m_st, 2))
        m_local_fout = max(0.6, milestone_window_dur - 1.0)
        # Chỉ giải mã đúng ~8s RGBA của bong bóng cột mốc rồi dịch PTS tới giữa hiệp (nhanh hơn 180x!)
        fc_lines.append(
            f"[{next_input_idx}:v]format=rgba,"
            f"fade=t=in:st=0:d=0.5:alpha=1,fade=t=out:st={m_local_fout:.2f}:d=1.0:alpha=1,"
            f"setpts=PTS+{m_st:.2f}/TB[mbub]"
        )
        fc_lines.append(
            f"[{cur_v}][mbub]overlay=0:0:enable='between(t,{m_st:.2f},{m_end:.2f})':eof_action=pass[v_stage2]"
        )
        cur_v = "v_stage2"
        next_input_idx += 1

    fc_lines.append(
        f"[{cur_v}][sw]overlay={SHOWWAVES_X}:{SHOWWAVES_Y}:shortest=1,"
        f"drawtext=fontfile='{font_escaped}':text='{countdown_expr}':"
        f"fontsize=116:fontcolor={clock_color}:borderw=4:bordercolor=0x080B12:"
        f"x={CLOCK_CENTER_X}-text_w/2:y={CLOCK_TOP_Y},format=yuv420p[outv]"
    )
    fc = ";".join(fc_lines)

    cmd = [ffmpeg_exe, "-y", "-v", "error"]
    if is_video_bg:
        cmd += ["-stream_loop", "-1", "-t", f"{dur:.2f}", "-i", str(bg_media_path)]
    else:
        cmd += ["-loop", "1", "-t", f"{dur:.2f}", "-framerate", str(fps), "-i", str(bg_media_path)]

    cmd += [
        "-i",
        str(hud_png_path),
        "-i",
        str(phase_wav_path),
    ]
    if has_bubble and speech_bubble_png_path is not None:
        cmd += [
            "-loop",
            "1",
            "-t",
            f"{bubble_stream_dur:.2f}",
            "-framerate",
            str(fps),
            "-i",
            str(speech_bubble_png_path),
        ]
    if has_milestone and milestone_bubble_png_path is not None:
        cmd += [
            "-loop",
            "1",
            "-t",
            f"{milestone_window_dur:.2f}",
            "-framerate",
            str(fps),
            "-i",
            str(milestone_bubble_png_path),
        ]

    if use_hw_accel:
        v_enc, v_enc_args = detect_optimal_h264_encoder(ffmpeg_exe)
    else:
        v_enc, v_enc_args = "libx264", ["-preset", "veryfast", "-crf", "20"]

    fps_flag = ["-fps_mode", "cfr"] if _supports_fps_mode() else ["-vsync", "cfr"]
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
        v_enc,
        *v_enc_args,
        "-r",
        str(fps),
        *fps_flag,
        "-pix_fmt",
        "yuv420p",
        *BT709_COLOR_ARGS,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(seg_mp4),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        if v_enc != "libx264":
            # Tự động fallback về CPU libx264 nếu GPU encoder báo lỗi
            return render_phase_video_segment(
                phase=phase,
                phase_wav_path=phase_wav_path,
                bg_media_path=bg_media_path,
                hud_png_path=hud_png_path,
                work_dir=work_dir,
                speech_bubble_png_path=speech_bubble_png_path,
                milestone_bubble_png_path=milestone_bubble_png_path,
                vfx_cfg=vfx_cfg,
                fps=fps,
                use_hw_accel=False,
                ffmpeg_exe=ffmpeg_exe,
            )
        raise RuntimeError(f"Pomodoro phase {phase.index} FFmpeg failed: {res.stderr[-500:]}")
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
    res = subprocess.run(
        [
            ffmpeg_exe,
            "-y",
            "-v",
            "error",
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
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"Pomodoro concat_video_segments failed: {res.stderr[-500:]}")
    return final_mp4
