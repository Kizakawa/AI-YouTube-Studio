"""app.utils.ffmpeg.subtitles_and_thumbnail — Đốt phụ đề ASS/SRT, kiểm định đầu ra & trích xuất ảnh bìa (Thumbnail)."""
import os
import re
import shutil
import subprocess
import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Union, Any, Tuple

logger = logging.getLogger(__name__)
from app.utils.ffmpeg.core_encoder import (
    BT709_COLOR_ARGS,
    FFMPEG_TIMEOUT,
    get_duration_ms,
    get_video_encoder_args,
    probe_file,
    run_ffmpeg,
    run_ffmpeg_with_gpu_fallback,
)
from app.utils.ffmpeg.peak_and_trim import find_hook_peak_action_offset


def burn_subtitles(
    video_path: str,
    srt_path: str,
    output_path: str,
    font_name: str = "Arial",
    font_size: int = 40,
    font_color: str = "&H0000FFFF",  # Vàng tươi chuẩn phong cách Shorts viral
    outline_color: str = "&H00000000", # Viền đen tuyền
    outline_width: float = 2.5,
    shadow: float = 1.5,
    margin_v: int = 600,              # Chuẩn hóa 600px (vùng an toàn 576-672px, không bị che bởi Shorts UI)
    bold: bool = True,
) -> str:
    """
    Burn-in subtitle SRT/ASS vào video (hardcode hardsub).
    Phong cách YouTube Shorts: chữ vàng viền đen, font Arial/Segoe UI,
    đặt ở vùng an toàn (MarginV: 576-672px, mặc định 600px) để không bị đè bởi Shorts UI.
    Đã bổ sung PlayResX=1080 & PlayResY=1920 để khóa chặt độ phân giải canvas.
    """
    safe_margin = int(margin_v) if 576 <= int(margin_v) <= 672 else 600
    safe_font_name = "".join(c for c in str(font_name or "Arial") if c.isalnum() or c in (" ", "-", "_")).strip() or "Arial"
    safe_font_color = "".join(c for c in str(font_color or "&H0000FFFF") if c.isalnum() or c == "&") or "&H0000FFFF"
    safe_outline_color = "".join(c for c in str(outline_color or "&H00000000") if c.isalnum() or c == "&") or "&H00000000"
    style = (
        f"PlayResX=1080,"
        f"PlayResY=1920,"
        f"Fontname={safe_font_name},"
        f"FontSize={int(font_size)},"
        f"PrimaryColour={safe_font_color},"
        f"OutlineColour={safe_outline_color},"
        f"Outline={float(outline_width)},"
        f"Shadow={float(shadow)},"
        f"Bold={'1' if bold else '0'},"
        f"Alignment=2,"      # Bottom center
        f"MarginV={safe_margin}" # Chuẩn hóa vùng an toàn Shorts UI
    )
    # Escape path cho Windows (ffmpeg subtitles filter cần forward slashes và escape colon/quote)
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:").replace("'", "")
    is_ass = srt_path.lower().endswith(".ass")
    fonts_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"
    fontsdir_opt = ""
    if fonts_dir.exists() and any(fonts_dir.glob("*.[to]tf")):
        fdir_esc = str(fonts_dir).replace("\\", "/").replace(":", "\\:").replace("'", "")
        fontsdir_opt = f":fontsdir='{fdir_esc}'"

    # Với ASS: giữ nguyên styling nội tại (karaoke, fonts, margins) trong file
    # Với SRT: áp dụng force_style vì SRT thuần không có styling
    if is_ass:
        vf_filter = f"subtitles='{srt_escaped}'{fontsdir_opt},format=yuv420p"
    else:
        vf_filter = f"subtitles='{srt_escaped}'{fontsdir_opt}:force_style='{style}',format=yuv420p"

    enc_args = get_video_encoder_args(quality="high")
    args = [
        "-i", video_path,
        "-vf", vf_filter,
        *enc_args,
        *BT709_COLOR_ARGS,
        "-c:a", "copy",
        output_path
    ]
    run_ffmpeg_with_gpu_fallback(args, timeout=FFMPEG_TIMEOUT * 2)  # Subtitle burn chậm hơn
    return output_path


# ──────────────────────────────────────────────
# CHECKPOINT D: QA tự động
# ──────────────────────────────────────────────

def validate_output(file_path: str) -> Dict:
    import sys as _sys
    _helper_mod = _sys.modules.get("app.utils.ffmpeg_helper")
    _Path = getattr(_helper_mod, "Path", Path) if _helper_mod is not None else Path
    _probe_fn = getattr(_helper_mod, "probe_file", probe_file) if _helper_mod is not None else probe_file
    _dur_fn = getattr(_helper_mod, "get_duration_ms", get_duration_ms) if _helper_mod is not None else get_duration_ms
    """
    QA tự động — Kiểm tra file output bằng ffprobe.
    Trả dict kết quả kiểm tra bao gồm valid flag, metadata, và danh sách lỗi.
    """
    result = {
        "valid": False,
        "file_exists": _Path(file_path).exists(),
        "file_size_mb": 0,
        "duration_ms": 0,
        "has_video": False,
        "has_audio": False,
        "resolution": "",
        "codec_video": "",
        "codec_audio": "",
        "color_space": "",
        "color_primaries": "",
        "color_trc": "",
        "errors": [],
    }

    if not result["file_exists"]:
        result["errors"].append("File không tồn tại")
        return result

    result["file_size_mb"] = round(_Path(file_path).stat().st_size / (1024 * 1024), 2)

    info = _probe_fn(file_path)
    if not info:
        result["errors"].append("ffprobe không đọc được file")
        return result

    result["duration_ms"] = _dur_fn(file_path)

    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            result["has_video"] = True
            result["resolution"] = f"{stream.get('width')}x{stream.get('height')}"
            result["codec_video"] = stream.get("codec_name", "unknown")
            result["color_space"] = stream.get("color_space") or stream.get("colorspace", "")
            result["color_primaries"] = stream.get("color_primaries", "")
            result["color_trc"] = stream.get("color_transfer") or stream.get("color_trc", "")
        elif stream.get("codec_type") == "audio":
            result["has_audio"] = True
            result["codec_audio"] = stream.get("codec_name", "unknown")

    # Validation rules cho YouTube Shorts
    if not result["has_video"]:
        result["errors"].append("Không có video stream")
    if not result["has_audio"]:
        result["errors"].append("Không có audio stream")
    if result["duration_ms"] < 5000:
        result["errors"].append(f"Video quá ngắn: {result['duration_ms']}ms (tối thiểu 5s)")
    if result["duration_ms"] > 61000:
        result["errors"].append(f"Video quá dài cho Shorts: {result['duration_ms']}ms (tối đa 60s)")
    if result["file_size_mb"] < 0.1:
        result["errors"].append(f"File quá nhỏ: {result['file_size_mb']}MB — có thể bị corrupt")

    result["valid"] = len(result["errors"]) == 0
    return result


def create_placeholder_image(path: str, text: str = "Scene", scene_id: int = 1) -> str:
    """Tạo ảnh đồ họa dọc 1080x1920 phong cách điện ảnh cho trường hợp dự phòng khẩn cấp."""
    try:
        from PIL import Image, ImageDraw
        width, height = 1080, 1920
        base = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(base)

        top_color = (48, 68, 110)      # Bright slate blue (never triggers blackdetect)
        bottom_color = (72, 58, 128)   # Bright cinematic indigo

        for y in range(0, height, 2):
            ratio = y / height
            r = int(top_color[0] + (bottom_color[0] - top_color[0]) * ratio)
            g = int(top_color[1] + (bottom_color[1] - top_color[1]) * ratio)
            b = int(top_color[2] + (bottom_color[2] - top_color[2]) * ratio)
            draw.line([(0, y), (width, y)], fill=(r, g, b), width=2)

        # Subtle frame
        draw.rectangle([(40, 60), (width - 40, height - 60)], outline=(51, 65, 85), width=2)

        # Scene badge
        badge_text = f"SCENE #{scene_id:02d}"
        draw.rectangle([(width // 2 - 140, 240), (width // 2 + 140, 310)], fill=(30, 41, 59))
        draw.text((width // 2, 275), badge_text, fill=(226, 232, 240), anchor="mm")

        # Visual Keyword
        display_text = (text or f"Scene {scene_id}")[:80]
        draw.text((width // 2, height // 2 - 40), display_text, fill=(248, 250, 252), anchor="mm")

        base.save(path)
        return path
    except Exception as e:
        logger.debug(f"create_placeholder_image error: {e}")
        try:
            from PIL import Image
            Image.new("RGB", (1080, 1920), color=(15, 23, 42)).save(path)
            return path
        except Exception:
            return path



def extract_shock_thumbnail(
    video_path: str,
    output_path: str,
    timestamp_seconds: float = 0.8
) -> str:
    """
    Trích xuất frame đỉnh cao (Shock Frame) tại timestamp_seconds (mặc định 0.8s, giữa Hook Cảnh 1) làm thumbnail Shorts.
    Khung hình này có tác động thị giác mạnh mẽ nhất ngăn người xem vuốt qua (Swipe Away).
    """
    if not Path(video_path).exists():
        raise FileNotFoundError(f"Video file không tồn tại: {video_path}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-ss", f"{timestamp_seconds:.2f}",
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "2",
        "-y",
        output_path
    ]
    try:
        run_ffmpeg(args)
        logger.info(f"[Thumbnail] 📸 Đã trích xuất shock thumbnail tại {timestamp_seconds}s → {output_path}")
        return output_path
    except Exception as e:
        logger.warning(f"[Thumbnail] Không thể trích xuất thumbnail tại {timestamp_seconds}s: {e}")
        # Fallback về 0.1s
        if timestamp_seconds != 0.1:
            try:
                args[1] = "0.10"
                run_ffmpeg(args)
                return output_path
            except Exception:
                pass
        return ""


