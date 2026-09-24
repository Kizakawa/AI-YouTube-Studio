"""
FFmpeg Helper — Các hàm thao tác FFmpeg cấp thấp với timeout an toàn.
Mọi subprocess.run() đều có timeout để tránh zombie process.

Render Checkpoints:
  A: image_to_video()     — 1 ảnh tĩnh + duration → MP4 (Ken Burns)
  B: concat_videos()      — Ghép N clips → 1 video liên tục
  C: mux_audio_video()    — Ghép voice + BGM + video → final
     burn_subtitles()     — Burn-in SRT phụ đề
  D: validate_output()    — QA tự động bằng ffprobe
"""
import os
import shutil
import subprocess
import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

FFMPEG_TIMEOUT = 300   # 5 phút max cho render
FFPROBE_TIMEOUT = 10   # 10 giây max cho probe


def _ensure_ffmpeg_in_path():
    """
    Tự động bổ sung PATH từ WinGet, Windows Environment & các thư mục thường dùng nếu tiến trình hiện tại chưa nhận.
    ƯU TIÊN TUYỆT ĐỐI các bản FFmpeg hiện đại (WinGet Gyan.dev, C:\\ffmpeg\\bin) và LOẠI BỎ triệt để
    bản FFmpeg 4.0 cổ lỗ sĩ đi kèm phần mềm thứ ba (ví dụ Proteus 2018 gây lỗi -fps_mode).
    """
    if os.name == "nt":
        import glob
        
        # 1. Quét WinGet Packages hiện đại (ví dụ: Gyan.FFmpeg.Essentials)
        winget_pattern = os.path.expanduser("~\\AppData\\Local\\Microsoft\\WinGet\\Packages\\*FFmpeg*\\*\\bin")
        winget_dirs = [d for d in glob.glob(winget_pattern) if os.path.isdir(d)]

        # 2. Thư mục cài đặt chuẩn phổ biến
        standard_dirs = [
            r"C:\ffmpeg\bin",
            r"D:\ffmpeg\bin",
            r"C:\ProgramData\chocolatey\bin",
        ]
        standard_dirs = [d for d in standard_dirs if os.path.isdir(d)]

        # 3. Lấy PATH từ Registry Current User
        registry_dirs = []
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                user_path, _ = winreg.QueryValueEx(key, "Path")
                for p in user_path.split(";"):
                    clean_p = p.strip()
                    if clean_p and os.path.isdir(clean_p) and "proteus" not in clean_p.lower():
                        registry_dirs.append(clean_p)
        except Exception:
            pass

        # Tập hợp các thư mục ưu tiên hàng đầu (WinGet đứng đầu)
        priority_dirs = [d for d in (winget_dirs + standard_dirs) if "proteus" not in d.lower()]

        # Lọc bỏ Proteus khỏi PATH hiện tại
        current_paths = [
            p for p in os.environ.get("PATH", "").split(os.pathsep)
            if p.strip() and "proteus" not in p.lower()
        ]

        # Ghép lại PATH: priority_dirs ĐỨNG ĐẦU TIÊN để luôn gọi FFmpeg mới nhất
        merged_dirs = []
        for d in priority_dirs + registry_dirs + current_paths:
            if d not in merged_dirs:
                merged_dirs.append(d)

        os.environ["PATH"] = os.pathsep.join(merged_dirs)

    # Fallback cho cả Linux (Render Native) và Windows nếu chưa có ffmpeg trong PATH
    if not shutil.which("ffmpeg"):
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            if exe and os.path.exists(exe):
                exe_dir = str(Path(exe).parent)
                # Đảm bảo có binary chuẩn tên 'ffmpeg'
                target_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
                target_path = Path(exe_dir) / target_name
                if not target_path.exists():
                    try:
                        shutil.copy2(exe, str(target_path))
                    except Exception:
                        pass
                if exe_dir not in os.environ.get("PATH", "").split(os.pathsep):
                    os.environ["PATH"] = exe_dir + os.pathsep + os.environ.get("PATH", "")
        except Exception:
            pass


_ensure_ffmpeg_in_path()
_cached_ffmpeg_installed: Optional[bool] = None
_cached_ffmpeg_version: Optional[str] = None
_cached_supports_fps_mode: Optional[bool] = None


def _supports_fps_mode() -> bool:
    """Kiểm tra xem phiên bản FFmpeg hiện tại có hỗ trợ flag -fps_mode hay không (FFmpeg >= 5.1)"""
    global _cached_supports_fps_mode
    if _cached_supports_fps_mode is not None:
        return _cached_supports_fps_mode
    try:
        r = subprocess.run(
            ["ffmpeg", "-f", "lavfi", "-i", "nullsrc=s=10x10:d=0.01", "-fps_mode", "cfr", "-f", "null", "-"],
            capture_output=True,
            timeout=3
        )
        _cached_supports_fps_mode = (r.returncode == 0)
    except Exception:
        _cached_supports_fps_mode = False
    return _cached_supports_fps_mode


def is_ffmpeg_installed() -> bool:
    """Kiểm tra xem ffmpeg đã có trong PATH hệ thống hay chưa (kết quả được lưu cache)"""
    global _cached_ffmpeg_installed
    if _cached_ffmpeg_installed is None:
        _cached_ffmpeg_installed = shutil.which("ffmpeg") is not None
    return _cached_ffmpeg_installed


def get_ffmpeg_version() -> str:
    """Lấy thông tin phiên bản ffmpeg (kết quả được lưu cache in-memory)"""
    global _cached_ffmpeg_version
    if _cached_ffmpeg_version is not None:
        return _cached_ffmpeg_version
    if not is_ffmpeg_installed():
        _cached_ffmpeg_version = "Not installed"
        return _cached_ffmpeg_version
    try:
        res = subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=FFPROBE_TIMEOUT
        )
        _cached_ffmpeg_version = res.stdout.split("\n")[0]
        return _cached_ffmpeg_version
    except Exception as e:
        return f"Error: {e}"


def probe_file(file_path: str) -> Optional[Dict]:
    """
    Chạy ffprobe và trả về metadata JSON đầy đủ.
    Trả None nếu lỗi.
    """
    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT)
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception as e:
        logger.warning(f"ffprobe failed for {file_path}: {e}")
    return None


def get_duration_ms(file_path: str) -> int:
    """Lấy duration (ms) của file audio/video. Trả 0 nếu lỗi."""
    info = probe_file(file_path)
    if info and "format" in info:
        try:
            return int(float(info["format"]["duration"]) * 1000)
        except (KeyError, ValueError):
            pass
    return 0


def run_ffmpeg(args: List[str], timeout: int = FFMPEG_TIMEOUT) -> subprocess.CompletedProcess:
    """
    Wrapper an toàn cho ffmpeg subprocess.
    - Luôn có timeout (tránh zombie process)
    - Tự động áp dụng giới hạn luồng (-threads) nếu có biến FFMPEG_THREADS để tránh tràn RAM (OOM) trên Cloud
    - Raise RuntimeError nếu returncode != 0
    """
    thread_args = []
    max_threads = os.getenv("FFMPEG_THREADS")
    if max_threads and "-threads" not in args:
        thread_args = ["-threads", str(max_threads)]

    cmd = ["ffmpeg", "-y"] + thread_args + args
    logger.info(f"FFmpeg command: {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed (code={result.returncode}):\n"
            f"stderr: {result.stderr[-500:]}"  # Chỉ lấy 500 ký tự cuối
        )

    return result


# ──────────────────────────────────────────────
# CHECKPOINT A: Ảnh tĩnh → Video (Ken Burns)
# ──────────────────────────────────────────────

def image_to_video(
    image_path: str,
    duration_seconds: float,
    output_path: str,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    zoom_effect: bool = True,
) -> str:
    """
    Biến 1 ảnh tĩnh → video MP4 với Ken Burns (pan & zoom) effect.
    Codec: H.264 + yuv420p (YouTube compatible).
    """
    if zoom_effect:
        # Ken Burns: Slow zoom in từ 1.0x → 1.15x
        zoompan = (
            f"zoompan=z='min(zoom+0.0005,1.15)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={int(duration_seconds * fps)}:s={width}x{height}:fps={fps},"
            f"setsar=1"
        )
    else:
        zoompan = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,"
            f"fps={fps}"
        )

    args = [
        "-loop", "1",
        "-i", image_path,
        "-vf", zoompan,
        "-t", str(duration_seconds),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-an",
        output_path
    ]
    run_ffmpeg(args)
    return output_path


# ──────────────────────────────────────────────
# CHECKPOINT B: Cắt + Ghép N clips
# ──────────────────────────────────────────────

def trim_video(
    input_path: str,
    duration_seconds: float,
    output_path: str,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> str:
    """
    Cắt video Pexels về đúng duration cần thiết + scale/crop 9:16 + chuẩn hóa fps 30.
    Tự động lặp lại (stream_loop) nếu video stock ngắn hơn thời lượng lời thoại.
    Bỏ audio gốc của stock video.
    """
    args = [
        "-stream_loop", "-1",
        "-i", input_path,
        "-t", str(duration_seconds),
        "-vf", (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"setsar=1,"
            f"fps={fps}"
        ),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-an",  # Bỏ audio gốc của stock video
        output_path
    ]
    run_ffmpeg(args)
    return output_path


def concat_videos(
    video_paths: List[str],
    output_path: str,
    fps: int = 30,
) -> str:
    """
    Ghép N video clips đã chuẩn hóa thành 1 file liên tục.
    Tái tạo timeline chuẩn xác, chống đứng hình / lệch timebase giữa các stock clips.
    """
    list_file = str(Path(output_path).parent / "video_concat_list.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for vp in video_paths:
            abs_path = Path(vp).resolve().as_posix()
            safe = abs_path.replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    # Thích ứng tự động: FFmpeg >= 5.1 dùng -fps_mode cfr, FFmpeg cũ dùng -vsync cfr
    fps_flag = ["-fps_mode", "cfr"] if _supports_fps_mode() else ["-vsync", "cfr"]

    args = [
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
    ] + fps_flag + [
        "-an",
        output_path
    ]
    run_ffmpeg(args)
    return output_path


# ──────────────────────────────────────────────
# CHECKPOINT C: Audio mux + Subtitle burn-in
# ──────────────────────────────────────────────

def mux_audio_video(
    video_path: str,
    audio_path: str,
    output_path: str,
    bgm_path: str = None,
    bgm_volume: float = 0.08,
) -> str:
    """
    Ghép audio narration + video + optional BGM.
    Audio ducking: BGM ở volume thấp, narration ở volume đầy đủ.
    """
    if bgm_path and Path(bgm_path).exists():
        # Complex filter: mix narration (loud) + BGM (quiet)
        args = [
            "-i", video_path,
            "-i", audio_path,
            "-i", bgm_path,
            "-filter_complex",
            (
                f"[2:a]volume={bgm_volume},aloop=loop=-1:size=2e+09[bgm];"
                f"[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            ),
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            output_path
        ]
    else:
        args = [
            "-i", video_path,
            "-i", audio_path,
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            output_path
        ]

    run_ffmpeg(args)
    return output_path


def burn_subtitles(
    video_path: str,
    srt_path: str,
    output_path: str,
    font_name: str = "Arial",
    font_size: int = 22,
    font_color: str = "&H0000FFFF",  # Vàng tươi chuẩn phong cách Shorts viral
    outline_color: str = "&H00000000", # Viền đen tuyền
    outline_width: int = 3,
    shadow: int = 1,
    margin_v: int = 180,              # Cách đáy 180px tránh bị UI YouTube che
    bold: bool = True,
) -> str:
    """
    Burn-in subtitle SRT vào video (hardcode hardsub).
    Phong cách YouTube Shorts: chữ vàng viền đen, font Arial/Segoe UI, cách đáy an toàn.
    """
    style = (
        f"Fontname={font_name},"
        f"FontSize={font_size},"
        f"PrimaryColour={font_color},"
        f"OutlineColour={outline_color},"
        f"Outline={outline_width},"
        f"Shadow={shadow},"
        f"Bold={'1' if bold else '0'},"
        f"Alignment=2,"      # Bottom center
        f"MarginV={margin_v}" # Margin an toàn từ đáy tránh UI Shorts
    )
    # Escape path cho Windows (ffmpeg subtitles filter cần forward slashes và escape colon)
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")

    args = [
        "-i", video_path,
        "-vf", f"subtitles='{srt_escaped}':force_style='{style}'",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-c:a", "copy",
        output_path
    ]
    run_ffmpeg(args, timeout=FFMPEG_TIMEOUT * 2)  # Subtitle burn chậm hơn
    return output_path


# ──────────────────────────────────────────────
# CHECKPOINT D: QA tự động
# ──────────────────────────────────────────────

def validate_output(file_path: str) -> Dict:
    """
    QA tự động — Kiểm tra file output bằng ffprobe.
    Trả dict kết quả kiểm tra bao gồm valid flag, metadata, và danh sách lỗi.
    """
    result = {
        "valid": False,
        "file_exists": Path(file_path).exists(),
        "file_size_mb": 0,
        "duration_ms": 0,
        "has_video": False,
        "has_audio": False,
        "resolution": "",
        "codec_video": "",
        "codec_audio": "",
        "errors": [],
    }

    if not result["file_exists"]:
        result["errors"].append("File không tồn tại")
        return result

    result["file_size_mb"] = round(Path(file_path).stat().st_size / (1024 * 1024), 2)

    info = probe_file(file_path)
    if not info:
        result["errors"].append("ffprobe không đọc được file")
        return result

    result["duration_ms"] = get_duration_ms(file_path)

    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            result["has_video"] = True
            result["resolution"] = f"{stream.get('width')}x{stream.get('height')}"
            result["codec_video"] = stream.get("codec_name", "unknown")
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
