"""app.utils.ffmpeg.core_encoder — Khởi tạo FFmpeg PATH, dò tìm GPU Encoder, ffprobe & thực thi lệnh cơ sở."""
import os
import re
import shutil
import subprocess
import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Union, Any, Tuple

logger = logging.getLogger(__name__)

FFMPEG_TIMEOUT = 900   # 15 phút max cho render Video Dài 10 phút 1080p
FFPROBE_TIMEOUT = 10   # 10 giây max cho probe
VIDEO_DURATION_SAFETY_MARGIN = 0.05  # Đệm an toàn chống ngắt cụt audio/video cuối câu (0.05 giây)


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


def get_ffmpeg_exe() -> str:
    """Đảm bảo FFmpeg đã nằm trong PATH và trả về đường dẫn/tên thực thi chuẩn."""
    _ensure_ffmpeg_in_path()
    return shutil.which("ffmpeg") or "ffmpeg"


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


# ── MoneyPrinterTurbo Standard: Video Duration Safety Margin (+0.1s) ──
# Bù đệm 100ms vào cảnh cuối để chống frame rounding truncation làm đứt âm thanh kết thúc
VIDEO_DURATION_SAFETY_MARGIN: float = 0.1

# ── MoneyPrinterTurbo Standard: BT.709 Color Space & Primaries Tagging ──
# Khóa chặt color primaries, transfer characteristics và matrix coefficients về BT.709 (sRGB/HDTV)
# Ngăn chặn YouTube & mobile browsers hiểu nhầm thành BT.601 dẫn đến màu sắc bị bợt nhạt, mất tương phản
BT709_COLOR_ARGS: List[str] = [
    "-colorspace", "bt709",
    "-color_primaries", "bt709",
    "-color_trc", "bt709",
    "-color_range", "tv",
]


def build_atempo_filter(speed_ratio: float) -> str:
    """
    Sinh chuỗi filter FFmpeg 'atempo' chuẩn xác để đồng bộ thời lượng âm thanh không đổi cao độ (pitch).
    Xử lý giới hạn 0.5 <= atempo <= 2.0 của FFmpeg bằng cách ghép chuỗi (chaining).
    Lấy cảm hứng từ VoiceStudio Timed Speech Synchronization.
    """
    if speed_ratio <= 0:
        return "anull"
    ratio = float(speed_ratio)
    # Nếu xấp xỉ 1.0 (độ lệch dưới 2%), không cần can thiệp
    if abs(ratio - 1.0) < 0.02:
        return "anull"

    filters = []
    # Xử lý trường hợp nén tốc độ > 2.0x (ví dụ: ratio=2.4 -> atempo=2.0,atempo=1.2)
    while ratio > 2.0:
        filters.append("atempo=2.0")
        ratio /= 2.0
    # Xử lý trường hợp kéo giãn tốc độ < 0.5x (ví dụ: ratio=0.4 -> atempo=0.5,atempo=0.8)
    while ratio < 0.5:
        filters.append("atempo=0.5")
        ratio /= 0.5

    filters.append(f"atempo={ratio:.4f}")
    return ",".join(filters)


_cached_hardware_encoder: Optional[Dict[str, Any]] = None


def detect_hardware_encoder(force_recheck: bool = False) -> Dict[str, Any]:
    """
    Tự động phát hiện và kiểm định bộ mã hóa video tăng tốc phần cứng (GPU Hardware Acceleration).
    Kiểm tra thực tế bằng FFmpeg probe (0.1 giây) để đảm bảo bộ mã hóa 100% hoạt động được trên thiết bị:
      1. h264_nvenc: NVIDIA GPU (CUDA / NVENC) — Tối ưu cao nhất trên card rời NVIDIA (Windows/Linux)
      2. h264_mf: Windows Media Foundation — Tăng tốc GPU DirectX mượt mà trên Windows (hỗ trợ NVIDIA, Intel, AMD)
      3. h264_qsv: Intel QuickSync Video — Tận dụng nhân đồ họa tích hợp Intel Iris Xe / UHD
      4. h264_videotoolbox: Apple Silicon (M1/M2/M3/M4) trên macOS
      5. h264_amf: AMD Advanced Media Framework trên card AMD Radeon
      6. Fallback: libx264 (CPU) — Bộ mã hóa phần mềm tiêu chuẩn
    """
    global _cached_hardware_encoder
    if _cached_hardware_encoder is not None and not force_recheck:
        return _cached_hardware_encoder

    # Kiểm tra cấu hình có bật GPU acceleration không
    enable_gpu = True
    forced_encoder = ""
    try:
        from config.settings import settings
        enable_gpu = getattr(settings, "ENABLE_GPU_ACCELERATION", True)
        forced_encoder = getattr(settings, "FORCE_GPU_ENCODER", "")
    except Exception:
        pass

    if not enable_gpu:
        _cached_hardware_encoder = {
            "encoder": "libx264",
            "is_gpu": False,
            "display_name": "CPU Software (libx264 - disabled by settings)"
        }
        return _cached_hardware_encoder

    ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"

    # Lưu ý kỹ thuật: KHÔNG tự động kích hoạt `h264_mf` (Windows Media Foundation MFT) vì trên Windows FFmpeg,
    # `h264_mf` khóa đơn luồng D3D11 MFT gây nghẽn hàng đợi 2-4 phút khi chạy đa luồng và làm sai lệch NAL/PTS khi ghép `-f concat`.
    candidates = [
        ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19", "-pix_fmt", "yuv420p"], "NVIDIA NVENC (GPU)"),
        ("h264_qsv", ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "20", "-pix_fmt", "nv12"], "Intel QuickSync (GPU)"),
        ("h264_amf", ["-c:v", "h264_amf", "-quality", "quality", "-b:v", "10M", "-pix_fmt", "yuv420p"], "AMD AMF (GPU)"),
        ("h264_videotoolbox", ["-c:v", "h264_videotoolbox", "-b:v", "10M", "-pix_fmt", "yuv420p"], "Apple VideoToolbox (GPU)"),
    ]
    if forced_encoder == "h264_mf":
        candidates.insert(0, ("h264_mf", ["-c:v", "h264_mf", "-b:v", "10M", "-pix_fmt", "yuv420p"], "Windows Media Foundation (Forced)"))

    if forced_encoder:
        if forced_encoder == "libx264":
            _cached_hardware_encoder = {
                "encoder": "libx264",
                "is_gpu": False,
                "display_name": "CPU Software (libx264 - forced)"
            }
            return _cached_hardware_encoder
        # Đưa encoder chỉ định lên đầu
        candidates = [c for c in candidates if c[0] == forced_encoder] + [c for c in candidates if c[0] != forced_encoder]

    for enc_name, test_args, disp_name in candidates:
        try:
            probe_cmd = [
                ffmpeg_bin, "-v", "error",
                "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
                *test_args,
                "-f", "null", "-"
            ]
            r = subprocess.run(probe_cmd, capture_output=True, timeout=4)
            if r.returncode == 0:
                _cached_hardware_encoder = {
                    "encoder": enc_name,
                    "is_gpu": True,
                    "display_name": disp_name
                }
                logger.info(f"🚀 [HardwareAcceleration] Đã phát hiện và kích hoạt thành công GPU Video Encoder: '{enc_name}' ({disp_name})! Tốc độ render tăng 3x-10x.")
                return _cached_hardware_encoder
            elif enc_name in ("h264_nvenc", "h264_qsv", forced_encoder):
                err_msg = (r.stderr or b"").decode("utf-8", errors="ignore").strip().splitlines()
                short_err = err_msg[0][:180] if err_msg else f"returncode={r.returncode}"
                logger.warning(f"⚠️ [HardwareAcceleration] Probe '{enc_name}' không khả dụng ({short_err}) -> thử GPU kế tiếp.")
        except Exception as e_probe:
            if enc_name in ("h264_nvenc", forced_encoder):
                logger.debug(f"[HardwareAcceleration] Probe '{enc_name}' exception: {e_probe}")
            continue

    # Nếu tất cả GPU thất bại, dùng CPU
    _cached_hardware_encoder = {
        "encoder": "libx264",
        "is_gpu": False,
        "display_name": "CPU Software (libx264)"
    }
    logger.info("💻 [HardwareAcceleration] Không phát hiện GPU khả dụng phù hợp, sử dụng bộ mã hóa CPU 'libx264'.")
    return _cached_hardware_encoder


def get_video_encoder_args(quality: str = "high", encoder_name: Optional[str] = None) -> List[str]:
    """
    Trả về danh sách cờ tham số FFmpeg phù hợp nhất cho bộ mã hóa được chọn (GPU hoặc CPU).
    Tự động chuẩn hóa bitrate, preset, pixel format để video đầu ra tương thích 100% YouTube Shorts 1080x1920.
    Hỗ trợ chế độ 'intermediate' siêu tốc (ultrafast CRF 18) cho các clip trung gian trước khi ghép xfade.
    """
    if encoder_name is None:
        enc_info = detect_hardware_encoder()
        encoder = enc_info["encoder"]
    else:
        encoder = encoder_name

    # Chế độ intermediate (dùng cho _prepare_scene_clips trước khi ghép xfade):
    # Ưu tiên tốc độ giải mã/mã hóa cực đại + CRF 18 giữ nguyên chất lượng ảnh gốc, giới hạn 2 threads/worker để chạy 8 workers song song không nghẽn CPU/GPU queue.
    if quality == "intermediate":
        if encoder == "h264_nvenc":
            return [
                "-c:v", "h264_nvenc",
                "-preset", "p1",
                "-cq", "18",
                "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            ]
        # Tr?nh d?ng h264_qsv / h264_amf cho intermediate clips (ch?y 6-8 lu?ng song song g?y ngh?n h?ng ??i D3D11/MediaSDK)
        return [
            "-threads", "2",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "18",
            "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        ]

    if quality == "longform":
        if encoder == "h264_nvenc":
            return [
                "-c:v", "h264_nvenc",
                "-preset", "p2",
                "-cq", "19",
                "-b:v", "4.6M",
                "-maxrate", "6.0M",
                "-bufsize", "12M",
                "-spatial-aq", "1",
                "-aq-strength", "8",
                "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            ]
        if encoder == "h264_qsv":
            return [
                "-c:v", "h264_qsv",
                "-preset", "veryfast",
                "-global_quality", "19",
                "-b:v", "4.6M",
                "-maxrate", "6.0M",
                "-bufsize", "12M",
                "-pix_fmt", "nv12", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            ]
        if encoder == "h264_amf":
            return [
                "-c:v", "h264_amf",
                "-quality", "balanced",
                "-rc", "cbr",
                "-b:v", "4.6M",
                "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            ]
        return [
            "-c:v", "libx264",
            "-preset", "superfast",
            "-crf", "19",
            "-b:v", "4.5M",
            "-maxrate", "6.0M",
            "-bufsize", "12M",
            "-aq-mode", "3",
            "-aq-strength", "0.8",
            "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        ]

    is_high = (quality == "high")

    # Trên máy nhiều nhân CPU (>= 8 luồng), libx264 superfast nhanh hơn và nét hơn h264_mf (Windows Media Foundation CBR)
    if encoder == "h264_mf" and (os.cpu_count() or 4) >= 8:
        encoder = "libx264"

    if encoder == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc",
            "-preset", "p3" if is_high else "p2",
            "-cq", "20" if is_high else "22",
            "-b:v", "5M" if is_high else "4M",
            "-maxrate", "6.5M" if is_high else "5.5M",
            "-bufsize", "13M" if is_high else "11M",
            "-spatial-aq", "1",
            "-aq-strength", "8",
            "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        ]
    elif encoder == "h264_mf":
        return [
            "-c:v", "h264_mf",
            "-b:v", "5M" if is_high else "4M",
            "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        ]
    elif encoder == "h264_qsv":
        return [
            "-c:v", "h264_qsv",
            "-preset", "veryfast",
            "-global_quality", "20" if is_high else "22",
            "-b:v", "5M" if is_high else "4M",
            "-maxrate", "6.5M" if is_high else "5.5M",
            "-bufsize", "13M" if is_high else "11M",
            "-pix_fmt", "nv12", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        ]
    elif encoder == "h264_videotoolbox":
        return [
            "-c:v", "h264_videotoolbox",
            "-b:v", "5M" if is_high else "4M",
            "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        ]
    elif encoder == "h264_amf":
        return [
            "-c:v", "h264_amf",
            "-quality", "balanced" if is_high else "speed",
            "-rc", "cbr",
            "-b:v", "5M" if is_high else "4M",
            "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        ]
    else:
        # Mặc định CPU libx264 (superfast nhanh hơn veryfast ~35% trên hệ thống đa nhân mà vẫn giữ nguyên độ nét 1080p Shorts)
        # Bổ sung -aq-mode 3 (Auto-Variance AQ ưu tiên giữ chi tiết vùng tối/biển sâu) + -x264-params VUI color tags chuẩn Y2A-Auto
        # Tối ưu bitrate chuẩn YouTube Shorts 1080p30 (-crf 20 -b:v 5M -maxrate 6.5M -bufsize 13M -> ~18-22MB/video, upload nhanh gấp 3x)
        return [
            "-c:v", "libx264",
            "-preset", "superfast",
            "-crf", "20" if is_high else "22",
            "-b:v", "5M" if is_high else "4M",
            "-maxrate", "6.5M" if is_high else "5.5M",
            "-bufsize", "13M" if is_high else "11M",
            "-aq-mode", "3",
            "-aq-strength", "0.8",
            "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709",
            "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        ]


def run_ffmpeg_with_gpu_fallback(args: List[str], timeout: int = FFMPEG_TIMEOUT) -> subprocess.CompletedProcess:
    """
    Thực thi lệnh FFmpeg với cơ chế tự phục hồi kép:
    Nếu lệnh sử dụng GPU hardware encoder và gặp lỗi bất kỳ (ví dụ driver không tương thích, thiếu VRAM...),
    hệ thống tự động thay thế bằng bộ mã hóa CPU (libx264) và thực hiện lại ngay lập tức!
    Đảm bảo 100% video luôn render thành công không bao giờ làm gián đoạn pipeline.
    """
    try:
        import sys as _sys
        _helper_mod = _sys.modules.get("app.utils.ffmpeg_helper")
        _run_fn = getattr(_helper_mod, "run_ffmpeg", run_ffmpeg) if _helper_mod is not None else run_ffmpeg
        return _run_fn(args, timeout=timeout)
    except Exception as gpu_err:
        enc_info = detect_hardware_encoder()
        current_enc = enc_info["encoder"]
        # Chỉ kích hoạt fallback nếu đang dùng GPU encoder
        if current_enc != "libx264" and any(current_enc in arg for arg in args):
            logger.warning(
                f"⚠️ [HardwareAcceleration Fallback] Bộ mã hóa GPU '{current_enc}' gặp sự cố ({str(gpu_err)[:80]}). "
                f"Đang tự động chuyển tiếp sang bộ mã hóa CPU (libx264) dự phòng an toàn..."
            )
            cpu_args = get_video_encoder_args(quality="high", encoder_name="libx264")
            new_args = []
            i = 0
            while i < len(args):
                if args[i] == "-c:v" and i + 1 < len(args) and args[i+1] == current_enc:
                    new_args.extend(cpu_args)
                    i += 2
                    # Bỏ qua các flag đi kèm encoder cũ cho đến khi gặp flag mới không liên quan
                    while i < len(args) and args[i] in (
                        "-b:v", "-maxrate", "-bufsize", "-pix_fmt", "-preset",
                        "-crf", "-cq", "-global_quality", "-quality", "-rc", "-color_range", "-colorspace", "-color_primaries", "-color_trc",
                        "-spatial-aq", "-aq-mode", "-aq-strength", "-x264-params"
                    ):
                        i += 2
                    continue
                new_args.append(args[i])
                i += 1

            import sys as _sys
            _helper_mod = _sys.modules.get("app.utils.ffmpeg_helper")
            _run_fn = getattr(_helper_mod, "run_ffmpeg", run_ffmpeg) if _helper_mod is not None else run_ffmpeg
            return _run_fn(new_args, timeout=timeout)
        else:
            raise gpu_err


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


_INCOMPLETE_MP4_NAME_TOKENS = (
    "_fftmp_",
    ".tmp",
    "_tmp",
    ".stitch",
    ".vtrim",
    "_raw",
    ".part",
    "_fullpeak",
)


def is_completed_clean_mp4(
    file_path: Union[str, Path],
    min_size_bytes: int = 15_000,
    min_duration_ms: int = 1000,
    allow_raw_source: bool = False,
) -> bool:
    """
    Kiểm tra nghiêm ngặt một file video `.mp4` đã hoàn tất ghi đĩa (có đầy đủ `moov` atom,
    thời lượng `>= min_duration_ms`, không phải file tạm FFmpeg/yt-dlp đang ghi dở).
    """
    try:
        p = Path(file_path)
        if not p.is_file():
            return False
        name_low = p.name.lower()
        blocked_tokens = (
            tuple(tok for tok in _INCOMPLETE_MP4_NAME_TOKENS if tok != "_raw")
            if allow_raw_source
            else _INCOMPLETE_MP4_NAME_TOKENS
        )
        if any(tok in name_low for tok in blocked_tokens):
            return False
        if p.stat().st_size < min_size_bytes:
            return False
        return get_duration_ms(str(p)) >= min_duration_ms
    except Exception:
        return False


def get_video_dimensions(file_path: Union[str, Path]) -> Tuple[int, int]:
    """Đọc kích thước thực tế `(width, height)` của luồng video đầu tiên qua `ffprobe`."""
    info = probe_file(str(file_path))
    if info and isinstance(info.get("streams"), list):
        for st in info["streams"]:
            if st.get("codec_type") == "video":
                w = int(st.get("width") or 0)
                h = int(st.get("height") or 0)
                if w > 0 and h > 0:
                    return w, h
    return 0, 0


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

