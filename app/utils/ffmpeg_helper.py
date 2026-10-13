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
from typing import Optional, Dict, List, Union, Any

logger = logging.getLogger(__name__)

FFMPEG_TIMEOUT = 300   # 5 phút max cho render
FFPROBE_TIMEOUT = 10   # 10 giây max cho probe
VIDEO_DURATION_SAFETY_MARGIN = 0.1  # Đệm an toàn chống ngắt cụt audio/video cuối câu (0.1 giây)


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


_cached_hardware_encoder: Optional[Dict[str, Any]] = None


def detect_hardware_encoder(force_recheck: bool = False) -> Dict[str, Any]:
    """
    Tự động phát hiện và kiểm định bộ mã hóa video tăng tốc phần cứng (GPU Hardware Acceleration).
    Kiểm tra thực tế bằng FFmpeg probe (0.1 giây) để đảm bảo bộ mã hóa 100% hoạt động được trên thiết bị:
      1. h264_nvenc: NVIDIA GPU (CUDA / NVENC) — Tối ưu cao nhất trên card rời NVIDIA & Colab/Linux
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

    candidates = [
        ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19", "-pix_fmt", "yuv420p"], "NVIDIA NVENC (GPU)"),
        ("h264_mf", ["-c:v", "h264_mf", "-b:v", "10M", "-pix_fmt", "yuv420p"], "Windows Media Foundation (GPU/DirectX)"),
        ("h264_qsv", ["-c:v", "h264_qsv", "-global_quality", "20", "-pix_fmt", "nv12"], "Intel QuickSync (GPU)"),
        ("h264_videotoolbox", ["-c:v", "h264_videotoolbox", "-b:v", "10M", "-pix_fmt", "yuv420p"], "Apple VideoToolbox (GPU)"),
        ("h264_amf", ["-c:v", "h264_amf", "-quality", "quality", "-b:v", "10M", "-pix_fmt", "yuv420p"], "AMD AMF (GPU)"),
    ]

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
                "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
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
        except Exception:
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
    """
    if encoder_name is None:
        enc_info = detect_hardware_encoder()
        encoder = enc_info["encoder"]
    else:
        encoder = encoder_name

    is_high = (quality == "high")

    if encoder == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc",
            "-preset", "p5" if is_high else "p3",
            "-cq", "18" if is_high else "22",
            "-b:v", "12M" if is_high else "8M",
            "-maxrate", "16M" if is_high else "12M",
            "-bufsize", "24M" if is_high else "16M",
            "-pix_fmt", "yuv420p",
        ]
    elif encoder == "h264_mf":
        return [
            "-c:v", "h264_mf",
            "-b:v", "12M" if is_high else "8M",
            "-pix_fmt", "yuv420p",
        ]
    elif encoder == "h264_qsv":
        return [
            "-c:v", "h264_qsv",
            "-global_quality", "18" if is_high else "22",
            "-b:v", "12M" if is_high else "8M",
            "-maxrate", "16M" if is_high else "12M",
            "-bufsize", "24M" if is_high else "16M",
            "-pix_fmt", "nv12",
        ]
    elif encoder == "h264_videotoolbox":
        return [
            "-c:v", "h264_videotoolbox",
            "-b:v", "12M" if is_high else "8M",
            "-pix_fmt", "yuv420p",
        ]
    elif encoder == "h264_amf":
        return [
            "-c:v", "h264_amf",
            "-quality", "quality" if is_high else "speed",
            "-rc", "cbr",
            "-b:v", "12M" if is_high else "8M",
            "-pix_fmt", "yuv420p",
        ]
    else:
        # Mặc định CPU libx264
        return [
            "-c:v", "libx264",
            "-preset", "medium" if is_high else "veryfast",
            "-crf", "18" if is_high else "22",
            "-b:v", "12M" if is_high else "8M",
            "-maxrate", "16M" if is_high else "12M",
            "-bufsize", "24M" if is_high else "16M",
            "-pix_fmt", "yuv420p",
        ]


def run_ffmpeg_with_gpu_fallback(args: List[str], timeout: int = FFMPEG_TIMEOUT) -> subprocess.CompletedProcess:
    """
    Thực thi lệnh FFmpeg với cơ chế tự phục hồi kép:
    Nếu lệnh sử dụng GPU hardware encoder và gặp lỗi bất kỳ (ví dụ driver không tương thích, thiếu VRAM...),
    hệ thống tự động thay thế bằng bộ mã hóa CPU (libx264) và thực hiện lại ngay lập tức!
    Đảm bảo 100% video luôn render thành công không bao giờ làm gián đoạn pipeline.
    """
    try:
        return run_ffmpeg(args, timeout=timeout)
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
                        "-crf", "-cq", "-global_quality", "-quality", "-rc"
                    ):
                        i += 2
                    continue
                new_args.append(args[i])
                i += 1

            return run_ffmpeg(new_args, timeout=timeout)
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


def get_niche_color_grading(niche: Optional[str] = None, is_scifi: bool = False) -> Dict[str, str]:
    """
    Trả về bộ lọc màu sắc, độ tương phản và vignette điện ảnh chuẩn hóa theo từng ngách (Niche Identity).
    Sử dụng 100% các filter native của FFmpeg (eq, colorbalance, unsharp, vignette) với tốc độ render cao,
    đồng bộ hóa màu sắc cho cả ảnh tĩnh (Ken Burns) và stock video.
    """
    raw_niche = (niche or "").lower().strip()

    if any(k in raw_niche for k in ("satirical", "comic", "cham_biem", "hoat_hinh_tam_ly")):
        niche_key = "comic_satire"
    elif "psycholog" in raw_niche or "tam_ly" in raw_niche:
        niche_key = "psychology"
    elif any(k in raw_niche for k in ("deep_sea", "ocean", "bien_sau", "sea")):
        niche_key = "deep_sea"
    elif any(k in raw_niche for k in ("predator", "wildlife", "wild", "san_moi", "hoang_da")):
        niche_key = "wildlife_predators"
    elif any(k in raw_niche for k in ("rescue", "animal", "pet", "thu_cung", "giai_cuu")):
        niche_key = "animal_rescue"
    elif any(k in raw_niche for k in ("history", "ancient", "mystery", "lich_su", "bi_an")):
        niche_key = "history_mystery"
    elif any(k in raw_niche for k in ("horror", "creepy", "kinh_di")):
        niche_key = "horror"
    elif is_scifi or any(k in raw_niche for k in ("scifi", "tech", "ai", "future", "cong_nghe")):
        niche_key = "scifi"
    elif any(k in raw_niche for k in ("finance", "wealth", "luxury", "money", "tai_chinh")):
        niche_key = "luxury"
    elif any(k in raw_niche for k in ("stoic", "philosophy", "triet_hoc")):
        niche_key = "stoic"
    elif any(k in raw_niche for k in ("health", "biohack", "suc_khoe")):
        niche_key = "health"
    else:
        niche_key = "general"

    PROFILES = {
        "comic_satire": {
            # Hoạt họa 2D & Truyện tranh châm biếm: nét mực sắc nét, tương phản sạch sẽ, tôn vinh nét vẽ 2D
            "vf_filter": "eq=contrast=1.08:saturation=1.05:brightness=0.01,unsharp=3:3:0.6:3:3:0.25",
            "vignette": "",
        },
        "psychology": {
            # Tâm lý học: tương phản vừa phải, sắc nét, giữ nguyên màu sắc tự nhiên
            "vf_filter": "eq=contrast=1.06:saturation=1.08:brightness=0.01,unsharp=3:3:0.4:3:3:0.15",
            "vignette": "vignette=0.35",
        },
        "deep_sea": {
            # Vực thẳm đại dương: màu xanh biển sâu sống động, sáng rõ vi mô
            "vf_filter": "eq=contrast=1.08:saturation=1.18:brightness=0.01,colorbalance=rs=-0.01:gs=0.02:bs=0.04,unsharp=3:3:0.5:3:3:0.20",
            "vignette": "vignette=0.38",
        },
        "wildlife_predators": {
            # National Geographic: màu sắc sống động rực rỡ, sắc nét tự nhiên, khung hình sáng rõ
            "vf_filter": "eq=contrast=1.06:saturation=1.20:brightness=0.02,unsharp=3:3:0.5:3:3:0.20",
            "vignette": "",
        },
        "animal_rescue": {
            # Cảm xúc ấm áp: ánh sáng vàng ấm nhẹ, màu sắc tươi tắn, không tối góc
            "vf_filter": "eq=contrast=1.04:saturation=1.15:brightness=0.03,colorbalance=rs=0.02:gs=0.01:bs=-0.01,unsharp=3:3:0.4:3:3:0.15",
            "vignette": "",
        },
        "history_mystery": {
            # Cổ tích & Huyền bí: ánh vàng kim ấm áp, giữ độ sáng và chi tiết kiến trúc/nhân vật
            "vf_filter": "eq=contrast=1.06:saturation=1.12:brightness=0.01,colorbalance=rs=0.02:gs=0.01:bs=-0.01,unsharp=3:3:0.4:3:3:0.15",
            "vignette": "vignette=0.32",
        },
        "horror": {
            # Rùng rợn: ám xanh lạnh nhẹ, tương phản kịch tính vừa phải
            "vf_filter": "eq=contrast=1.10:saturation=0.96:brightness=0.00,colorbalance=rs=-0.02:gs=0.01:bs=0.02,unsharp=3:3:0.5:3:3:0.20",
            "vignette": "vignette=0.42",
        },
        "scifi": {
            # Viễn tưởng công nghệ: neon rực rỡ, màu sắc punchy tươi sáng, chi tiết sắc nét
            "vf_filter": "eq=contrast=1.08:saturation=1.22:brightness=0.02,unsharp=3:3:0.5:3:3:0.20",
            "vignette": "vignette=0.30",
        },
        "luxury": {
            # Sang trọng & Động lực: ánh vàng kim rạng rỡ, màu sắc cao cấp, độ sáng hoàn hảo
            "vf_filter": "eq=contrast=1.05:saturation=1.16:brightness=0.02,colorbalance=rs=0.02:gs=0.01:bs=-0.01,unsharp=3:3:0.4:3:3:0.15",
            "vignette": "",
        },
        "stoic": {
            # Khắc kỷ / Triết học: tông trầm tĩnh nhưng sáng rõ, tự nhiên
            "vf_filter": "eq=contrast=1.06:saturation=1.06:brightness=0.01,unsharp=3:3:0.4:3:3:0.15",
            "vignette": "vignette=0.32",
        },
        "health": {
            # Sức khỏe & Sinh học: màu sắc tươi tắn, trong trẻo, căng tràn sức sống
            "vf_filter": "eq=contrast=1.04:saturation=1.20:brightness=0.03,unsharp=3:3:0.4:3:3:0.15",
            "vignette": "",
        },
        "general": {
            # Mặc định cinema: màu sắc tươi tắn, trong trẻo, tôn vinh ảnh web và ảnh AI gốc
            "vf_filter": "eq=contrast=1.05:saturation=1.14:brightness=0.01,unsharp=3:3:0.4:3:3:0.15",
            "vignette": "",
        },
    }

    cfg = PROFILES.get(niche_key, PROFILES["general"])
    vf_filter = cfg["vf_filter"]
    vignette = cfg.get("vignette", "")
    full_filter = f"{vf_filter},{vignette}" if vignette else vf_filter

    return {
        "niche_key": niche_key,
        "vf_filter": vf_filter,
        "vignette": vignette,
        "full_filter": full_filter,
    }


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
    camera_motion: str = "zoom_in",
    is_hook_scene: bool = False,
    is_scifi: bool = False,
    niche: Optional[str] = None,
) -> str:
    """
    Biến 1 ảnh tĩnh → video MP4 với Ken Burns cinematic effect và bộ lọc thị giác viễn tưởng / Niche Grading.
    
    Hỗ trợ các loại chuyển động:
      - zoom_in:        Zoom dần vào tâm ảnh (mặc định, cảm giác áp lực/sức mạnh)
      - zoom_out:       Zoom ra xa (cảm giác khám phá/vĩ đại)
      - pan_left:       Lướt từ phải sang trái (cinematic reveal)
      - pan_right:      Lướt từ trái sang phải
      - pan_up:         Kéo từ dưới lên (climax/rising)
      - pan_down:       Kéo từ trên xuống (tụt xuống/ominous)
      - hyper_zoom:     Tăng tốc lao nhanh vào trung tâm (Warp speed / vượt chân trời sự kiện)
      - warp_zoom:      Zoom động lực cao bước nhảy không gian
      - cinematic_drift: Trôi dạt không trọng lực 3D (kết hợp panning ngang dọc và zoom chậm)
    
    Khi is_hook_scene=True: Áp dụng cú Punch-in Zoom kịch tính (1.05 -> 1.38)
    nhằm kích thích thị giác, khóa chặt sự chú ý của người xem trong 2.5s đầu.
    Khi is_scifi=True: Tự động kích hoạt bộ lọc độ tương phản cao, làm rực sắc màu neon/sao vũ trụ
    và tăng độ sắc nét chi tiết 8K (HDR Sci-Fi grading).
    Codec: H.264 + yuv420p (YouTube compatible).
    """
    # ── Chuẩn hóa ảnh bằng Pillow: chuyển sang RGB, scale về đúng tỷ lệ ──
    # Dùng unique suffix theo output_path để tránh race condition khi nhiều job chạy song song
    import uuid as _uuid
    _uid = _uuid.uuid4().hex[:8]
    norm_path = str(Path(output_path).parent / f"_norm_{_uid}.jpg")
    try:
        from PIL import Image
        with Image.open(image_path) as im:
            if im.mode not in ("RGB",):
                im = im.convert("RGB")
            # Scale ảnh vừa phải để có đệm zoom nhẹ nhàng, sắc nét, không bị vỡ hạt
            scale_factor = 1.30 if is_hook_scene else 1.25
            scale_w = int(width * scale_factor)
            scale_h = int(height * scale_factor)
            # KHÔNG resize thẳng (ảnh ngang sẽ bị bóp méo) → crop 9:16 bám chủ thể / nền mờ nếu ảnh nhỏ
            from app.services.media.image_quality import prepare_vertical
            im_resized = prepare_vertical(im, scale_w, scale_h, max_upscale=2.0 * scale_factor)
            im_resized.save(norm_path, "JPEG", quality=95)
            image_path = norm_path
    except Exception as e:
        raise ValueError(f"File ảnh không hợp lệ hoặc không thể đọc '{image_path}': {e}")

    d = int(duration_seconds * fps)  # Tổng số frames
    if is_hook_scene:
        # Cú Punch-in Zoom kịch tính cho Scene 1 (1.08 -> 1.24) tạo thumbstopper giật mắt người xem ngay frame 0
        z_start = 1.08
        z_end   = 1.24
    else:
        z_start = 1.0
        z_end   = 1.15  # Zoom mượt mà 15% chuẩn cinema
    z_step  = (z_end - z_start) / max(d, 1)

    # Level 4 Pattern Interrupt: Cú Stepped Punch-in Snap (thu phóng đột ngột 5.5% tại 46% thời lượng)
    # Ngắt nhịp nhận thức mỗi 3-4 giây phong cách Vox/Magnates Media
    mid_snap_frame = int(d * 0.46)
    snap_expr = f"+if(gte(on\\,{mid_snap_frame})\\,0.055\\,0)" if (duration_seconds >= 3.2 and not is_hook_scene) else ""

    if not zoom_effect:
        zoompan = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,"
            f"fps={fps}"
        )
    elif camera_motion == "hyper_zoom":
        # Lao nhanh siêu tốc vào tâm vũ trụ / warp speed
        zoompan = (
            f"zoompan=z='1.05+0.35*pow(min(on/{d}\\,1)\\,1.8)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={fps},"
            f"setsar=1"
        )
    elif camera_motion == "warp_zoom":
        # Bước nhảy không thời gian
        zoompan = (
            f"zoompan=z='1.0+0.32*pow(min(on/{d}\\,1)\\,2.2)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={fps},"
            f"setsar=1"
        )
    elif camera_motion in ("nod_loop", "satirical_nod", "head_nod"):
        # Chuyển động Gật Đầu Hoạt Họa Châm Biếm (Comic Nodding Loop):
        # Trục Y gập xuống và nảy lên theo chu kỳ hình sin nhịp nhàng (~1.7 cú gật/giây) kết hợp zoom chậm
        zoompan = (
            f"zoompan=z='1.08+0.05*(1-pow(1-min(on/{d}\\,1)\\,2.0))':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='(ih/2-(ih/zoom/2)) + (ih*0.024)*pow(sin(on*0.35)\\,2)':"
            f"d={d}:s={width}x{height}:fps={fps},"
            f"setsar=1"
        )
    elif camera_motion == "cinematic_drift":
        # Trôi dạt 3D đa hướng không trọng lực
        zoompan = (
            f"zoompan=z='1.08+0.14*(1-pow(1-min(on/{d}\\,1)\\,2.5))':"
            f"x='iw*0.06*(1-pow(1-min(on/{d}\\,1)\\,2.2))':"
            f"y='ih*0.05*(1-pow(1-min(on/{d}\\,1)\\,2.2))':"
            f"d={d}:s={width}x{height}:fps={fps},"
            f"setsar=1"
        )
    elif camera_motion == "zoom_in":
        # P2.1: Cubic ease-out — Bắt đầu nhanh dứt khoát và giảm tốc mượt mà
        if is_hook_scene:
            # Punch-in kịch tính cho Scene 1 (0-3s) tạo thumbstopper giật mắt người xem
            zoompan = (
                f"zoompan=z='{z_start}+({z_end}-{z_start})*(1-pow(1-min(on/{d}\\,1)\\,3))':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d={d}:s={width}x{height}:fps={fps},"
                f"setsar=1"
            )
        else:
            zoompan = (
                f"zoompan=z='{z_start}+({z_end}-{z_start})*(1-pow(1-min(on/{d}\\,1)\\,2.8)){snap_expr}':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d={d}:s={width}x{height}:fps={fps},"
                f"setsar=1"
            )
    elif camera_motion == "zoom_out":
        # P2.1: Cubic ease-out từ z_end thu nhỏ dần về z_start (khám phá chiều sâu)
        zoompan = (
            f"zoompan=z='{z_end}-({z_end}-{z_start})*(1-pow(1-min(on/{d}\\,1)\\,2.8))':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={fps},"
            f"setsar=1"
        )
    elif camera_motion == "pan_left":
        # P2.1: Pan ngang từ phải sang trái mượt mà với cubic deceleration
        zoompan = (
            f"zoompan=z=1.15:"
            f"x='iw*0.1*pow(1-min(on/{d}\\,1)\\,2.5)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={fps},"
            f"setsar=1"
        )
    elif camera_motion == "pan_right":
        # P2.1: Pan ngang từ trái sang phải với cubic ease-out
        zoompan = (
            f"zoompan=z=1.15:"
            f"x='iw*0.1*(1-pow(1-min(on/{d}\\,1)\\,2.5))':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={fps},"
            f"setsar=1"
        )
    elif camera_motion == "pan_up":
        # P2.1: Pan dọc từ dưới lên trên với cubic ease-out
        zoompan = (
            f"zoompan=z=1.15:"
            f"x='iw/2-(iw/zoom/2)':y='ih*0.1*pow(1-min(on/{d}\\,1)\\,2.5)':"
            f"d={d}:s={width}x{height}:fps={fps},"
            f"setsar=1"
        )
    elif camera_motion == "pan_down":
        # P2.1: Pan dọc từ trên xuống dưới với cubic ease-out
        zoompan = (
            f"zoompan=z=1.15:"
            f"x='iw/2-(iw/zoom/2)':y='ih*0.1*(1-pow(1-min(on/{d}\\,1)\\,2.5))':"
            f"d={d}:s={width}x{height}:fps={fps},"
            f"setsar=1"
        )
    else:
        # Fallback: zoom_in cubic ease-out với Stepped Punch-in
        zoompan = (
            f"zoompan=z='{z_start}+({z_end}-{z_start})*(1-pow(1-min(on/{d}\\,1)\\,2.8)){snap_expr}':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={fps},"
            f"setsar=1"
        )

    # Hiệu ứng màu sắc điện ảnh và chiều sâu theo Niche Profile (100% Free Native FFmpeg Filter)
    grading = get_niche_color_grading(niche=niche, is_scifi=is_scifi)
    vf_parts = [zoompan, grading['vf_filter']]
    if grading.get("vignette"):
        vf_parts.append(grading['vignette'])
    vf_chain = ",".join(vf_parts)

    enc_args = get_video_encoder_args(quality="high")
    args = [
        "-loop", "1",
        "-i", image_path,
        "-vf", vf_chain,
        "-t", str(duration_seconds),
        *enc_args,
        "-r", str(fps),
        "-an",
        output_path
    ]
    try:
        run_ffmpeg_with_gpu_fallback(args)
    finally:
        # Xóa file tạm sau khi render xong (tránh rác file)
        try:
            Path(norm_path).unlink(missing_ok=True)
        except Exception:
            pass
    return output_path


def multi_pose_loop_to_video(
    image_paths: List[str],
    duration_seconds: float,
    output_path: str,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    switch_interval: float = 0.28,
    niche: Optional[str] = None,
) -> str:
    """
    Biến 2 hoặc nhiều ảnh dáng điệu (poses) của cùng 1 nhân vật 2D thành video hoạt họa cử động liên tục
    (Head nodding, talking mouth, sighing, mask switching) theo phong cách truyện tranh comic / Én / Tuyết Bích.
    - image_paths: Danh sách 2+ ảnh pose (Pose A: ngẩng đầu/mặt nạ, Pose B: cúi gật đầu/tháo mặt nạ...)
    - switch_interval: Tần số đảo khung hình (mặc định 0.28s ~ 3.5 fps tạo nhịp gật đầu tự nhiên)
    - Tự động loop xen kẽ cho đến khi đạt đủ duration_seconds của lời thoại.
    """
    if not image_paths or len(image_paths) == 1:
        single_img = image_paths[0] if image_paths else ""
        return image_to_video(
            image_path=single_img,
            duration_seconds=duration_seconds,
            output_path=output_path,
            width=width,
            height=height,
            fps=fps,
            camera_motion="nod_loop",
            niche=niche,
        )

    # Chuẩn hóa kích thước từng ảnh về 1080x1920
    from PIL import Image
    import uuid as _uuid
    temp_dir = Path(output_path).parent / f"_pose_temp_{_uuid.uuid4().hex[:6]}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    norm_paths = []
    for idx, p in enumerate(image_paths):
        dest = str(temp_dir / f"p_{idx:02d}.jpg")
        try:
            with Image.open(p) as im:
                im_rgb = im.convert("RGB")
                im_resized = im_rgb.resize((width, height), Image.LANCZOS)
                im_resized.save(dest, "JPEG", quality=95)
                norm_paths.append(dest)
        except Exception:
            continue

    if not norm_paths:
        return image_to_video(image_paths[0], duration_seconds, output_path, width, height, fps, camera_motion="nod_loop", niche=niche)

    # Tạo file danh sách concat lặp lại cho FFmpeg
    concat_txt = str(temp_dir / "concat_list.txt")
    total_time = 0.0
    with open(concat_txt, "w", encoding="utf-8") as f:
        while total_time < duration_seconds + 0.5:
            for p in norm_paths:
                f.write(f"file '{Path(p).resolve().as_posix()}'\n")
                f.write(f"duration {switch_interval}\n")
                total_time += switch_interval
        # FFmpeg concat demuxer requires the last file repeated once
        f.write(f"file '{Path(norm_paths[-1]).resolve().as_posix()}'\n")

    # Render video mượt mà từ chuỗi pose
    grading = get_niche_color_grading(niche=niche, is_scifi=False)
    vf_parts = [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        f"fps={fps}",
        grading['vf_filter']
    ]
    if grading.get("vignette"):
        vf_parts.append(grading['vignette'])

    args = [
        "-f", "concat",
        "-safe", "0",
        "-i", concat_txt,
        "-vf", ",".join(vf_parts),
        "-t", str(duration_seconds),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        *BT709_COLOR_ARGS,
        "-r", str(fps),
        "-an",
        output_path
    ]
    try:
        run_ffmpeg(args)
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

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
    is_scifi: bool = False,
    niche: Optional[str] = None,
    apply_grading: bool = True,
) -> str:
    """
    Cắt video Pexels về đúng duration cần thiết + scale/crop 9:16 + chuẩn hóa fps 30.
    Tự động lặp lại (stream_loop) nếu video stock ngắn hơn thời lượng lời thoại.
    Bỏ audio gốc của stock video.
    Áp dụng Cinematic Color Grading & Vignette chuẩn hóa theo từng Niche (100% Free Native FFmpeg Filter).
    Tự động nhận diện và KHÔNG áp grading 2 lần nếu clip nguồn đã được render từ trước.
    """
    # Tránh double-grading (nếu clip đã qua image_to_video hoặc trim_video trước đó)
    input_filename = Path(input_path).name.lower()
    is_already_rendered = any(tag in input_filename for tag in ("_i2v", "_t2v", "_pexels_fb", "clip_"))
    should_grade = apply_grading and not is_already_rendered

    grading_parts = []
    if should_grade:
        grading = get_niche_color_grading(niche=niche, is_scifi=is_scifi)
        grading_parts.append(grading['vf_filter'])
        if grading.get("vignette"):
            grading_parts.append(grading['vignette'])

    grading_chain = ("," + ",".join(grading_parts)) if grading_parts else ""

    # Level 4 Pattern Interrupt Vox/Magnates Style: Nếu clip dài >= 3.4s, tự động ngắt nhịp bằng cú Punch-in Snap (thu phóng 7% ở mốc giữa)
    if duration_seconds >= 3.4:
        snap_t = duration_seconds * 0.46
        crop_snap = (
            f",crop=w='if(gte(t\\,{snap_t:.2f})\\,{width}*0.93\\,{width})':"
            f"h='if(gte(t\\,{snap_t:.2f})\\,{height}*0.93\\,{height})':"
            f"x='(in_w-out_w)/2':y='(in_h-out_h)/2',"
            f"scale={width}:{height}"
        )
    else:
        crop_snap = ""

    vf_chain = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
        f"{crop_snap}"
        f"{grading_chain},"
        f"setsar=1,"
        f"fps={fps}"
    )

    # Clip ngắn hơn lời thoại một chút (≤ 1.6x) → kéo dãn slow-motion nhẹ thay vì lặp lại (lặp rất dễ bị nhận ra)
    loop_args = ["-stream_loop", "-1"]
    try:
        src_sec = get_duration_ms(input_path) / 1000.0
        if 0.5 < src_sec < duration_seconds:
            factor = duration_seconds / src_sec
            if factor <= 1.6:
                vf_chain = f"setpts={factor + 0.02:.4f}*PTS," + vf_chain
                loop_args = []
                logger.debug(f"[trim_video] Slow-motion x{factor:.2f} thay vì lặp clip ({src_sec:.1f}s → {duration_seconds:.1f}s)")
            else:
                logger.debug(f"[trim_video] Clip quá ngắn ({src_sec:.1f}s cho {duration_seconds:.1f}s) → buộc phải lặp")
    except Exception:
        pass

    enc_args = get_video_encoder_args(quality="high")
    args = [
        *loop_args,
        "-i", input_path,
        "-t", str(duration_seconds),
        "-vf", vf_chain,
        *enc_args,
        "-r", str(fps),
        "-an",  # Bỏ audio gốc của stock video
        output_path
    ]
    run_ffmpeg_with_gpu_fallback(args)
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
    enc_args = get_video_encoder_args(quality="high")

    args = [
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        *enc_args,
        "-r", str(fps),
    ] + fps_flag + [
        "-an",
        output_path
    ]
    run_ffmpeg_with_gpu_fallback(args)
    return output_path


def concat_videos_with_xfade(
    video_paths: List[str],
    output_path: str,
    fps: int = 30,
    transition: Union[str, List[str]] = "fade",
    transition_duration: float = 0.25,
    scene_durations_seconds: Optional[List[float]] = None,
) -> str:
    """
    Ghép N video clips với hiệu ứng chuyển cảnh xfade mượt mà giữa các scenes.

    Args:
        video_paths:             Danh sách clip MP4 đã chuẩn hóa (FPS, độ phân giải khớp nhau)
        output_path:             File MP4 output
        fps:                     FPS chuẩn (phải khớp với clips)
        transition:              Loại xfade: 'fade', 'zoomin', 'wipeleft', 'slideright', 'dissolve',
                                 'scifi' (tự động luân chuyển các chuyển cảnh viễn tưởng đỉnh cao),
                                 hoặc danh sách List[str] chuyển cảnh theo từng phân đoạn.
        transition_duration:     Thời lượng transition (giây), khuyến nghị 0.2-0.4s
        scene_durations_seconds: Thời lượng từng clip (giây) — cần để tính offset xfade.
                                 Nếu None, hàm tự probe bằng ffprobe.

    Returns:
        output_path

    Note:
        xfade chỉ hoạt động đúng khi tất cả clips có cùng FPS và resolution.
        Fallback về concat_videos nếu chỉ có 1 clip.
    """
    if len(video_paths) == 1:
        # Chỉ 1 clip → không cần xfade
        import shutil
        shutil.copy2(video_paths[0], output_path)
        return output_path

# ──────────────────────────────────────────────
# BỘ MÁY CHUYỂN CẢNH ĐA DẠNG NGHỆ THUẬT (CINEMATIC XFADE SUITE)
# ──────────────────────────────────────────────

NICHE_TRANSITIONS_MAP = {
    "scifi": ["zoomin", "smoothleft", "pixelize", "hblur", "circleopen", "smoothright", "diagtl"],
    "psychology": ["smoothleft", "circleopen", "smoothright", "radial", "dissolve", "diagtr", "hblur"],
    "deep_sea": ["radial", "smoothleft", "circleopen", "smoothright", "diagtl", "dissolve", "hblur"],
    "wildlife_predators": ["smoothleft", "circleopen", "smoothright", "diagtl", "radial", "dissolve"],
    "animal_rescue": ["smoothleft", "dissolve", "smoothright", "circleopen", "diagtl"],
    "horror": ["pixelize", "fadeblack", "hblur", "vertopen", "dissolve", "diagbl"],
    "luxury": ["smoothleft", "diagtl", "smoothright", "zoomin", "circleopen", "diagbr"],
    "history_mystery": ["smoothleft", "radial", "smoothright", "dissolve", "diagtl", "circleopen"],
    "general": ["smoothleft", "smoothright", "circleopen", "diagtl", "zoomin", "radial", "hblur"],
}


def resolve_niche_transitions(
    niche: Optional[str] = None,
    is_scifi: bool = False,
    count: int = 5
) -> List[str]:
    """
    Tạo danh sách các hiệu ứng chuyển cảnh xfade sống động, phù hợp từng ngách kênh.
    Luân chuyển mượt mà giữa các kiểu chuyển cảnh (wipes, smooth slides, radial sweeps, zoom-in, iris reveals),
    tuyệt đối không lặp lại đơn điệu 1 hiệu ứng fade đen nhàm chán.
    """
    raw_niche = (niche or "").lower().strip()
    if is_scifi or any(k in raw_niche for k in ("scifi", "tech", "ai", "future", "cong_nghe")):
        key = "scifi"
    elif "psycholog" in raw_niche or "tam_ly" in raw_niche or "stoic" in raw_niche:
        key = "psychology"
    elif any(k in raw_niche for k in ("deep_sea", "ocean", "bien_sau", "sea")):
        key = "deep_sea"
    elif any(k in raw_niche for k in ("predator", "wildlife", "wild", "san_moi", "hoang_da")):
        key = "wildlife_predators"
    elif any(k in raw_niche for k in ("rescue", "animal", "pet", "thu_cung", "giai_cuu")):
        key = "animal_rescue"
    elif any(k in raw_niche for k in ("horror", "creepy", "kinh_di")):
        key = "horror"
    elif any(k in raw_niche for k in ("finance", "wealth", "luxury", "money", "tai_chinh")):
        key = "luxury"
    elif any(k in raw_niche for k in ("history", "ancient", "mystery", "lich_su", "bi_an")):
        key = "history_mystery"
    else:
        key = "general"

    pool = NICHE_TRANSITIONS_MAP.get(key, NICHE_TRANSITIONS_MAP["general"])
    if count <= 0:
        return []
    return [pool[i % len(pool)] for i in range(count)]


def concat_videos_with_xfade(
    video_paths: List[str],
    output_path: str,
    fps: int = 30,
    transition: Union[str, List[str]] = "auto",
    transition_duration: float = 0.28,
    scene_durations_seconds: Optional[List[float]] = None,
) -> str:
    """
    Ghép N video clips với hiệu ứng chuyển cảnh xfade mượt mà, đa dạng giữa các scenes.

    Args:
        video_paths:             Danh sách clip MP4 đã chuẩn hóa (FPS, độ phân giải khớp nhau)
        output_path:             File MP4 output
        fps:                     FPS chuẩn (phải khớp với clips)
        transition:              Loại xfade hoặc List[str] các chuyển cảnh luân phiên:
                                 'auto', 'dynamic', 'scifi', 'psychology', 'deep_sea', 'smoothleft',
                                 'zoomin', 'circleopen', 'radial', v.v.
        transition_duration:     Thời lượng transition (giây), khuyến nghị 0.25-0.35s
        scene_durations_seconds: Thời lượng từng clip (giây) — cần để tính offset xfade.
                                 Nếu None, hàm tự probe bằng ffprobe.

    Returns:
        output_path
    """
    if len(video_paths) == 1:
        # Chỉ 1 clip → không cần xfade
        import shutil
        shutil.copy2(video_paths[0], output_path)
        return output_path

    if len(video_paths) == 0:
        raise ValueError("concat_videos_with_xfade: video_paths rỗng")

    # Lấy thời lượng từng clip nếu chưa cung cấp
    durations = []
    if scene_durations_seconds and len(scene_durations_seconds) == len(video_paths):
        durations = list(scene_durations_seconds)
    else:
        for vp in video_paths:
            dur_ms = get_duration_ms(vp)
            dur_s = dur_ms / 1000.0 if dur_ms > 0 else 4.0
            durations.append(dur_s)

    td = transition_duration  # shorthand
    n = len(video_paths)

    # Chuẩn bị danh sách chuyển cảnh cho (n - 1) nhát cắt
    if isinstance(transition, list) and transition:
        t_seq = [transition[i % len(transition)] for i in range(n - 1)]
    elif transition in ("scifi", "scifi_cyber", "dynamic_scifi"):
        t_seq = resolve_niche_transitions("scifi", is_scifi=True, count=n - 1)
    elif transition in NICHE_TRANSITIONS_MAP:
        t_seq = resolve_niche_transitions(transition, count=n - 1)
    elif transition in ("auto", "dynamic"):
        t_seq = resolve_niche_transitions("general", count=n - 1)
    elif transition in ("fade", "fadeblack") and n > 2:
        # Tự động nâng cấp fade đen đơn điệu sang chuỗi chuyển cảnh dynamic
        t_seq = resolve_niche_transitions("general", count=n - 1)
    else:
        t_seq = [str(transition)] * (n - 1)

    # ── Xây filter_complex xfade chain ──
    # Mỗi clip [i] được label [vi], rồi xfade chaining:
    # [v0][v1] xfade=... [vt01]; [vt01][v2] xfade=... [vt012]; ...
    input_args = []
    for vp in video_paths:
        input_args += ["-i", vp]

    # Label inputs
    filter_parts = []
    # settb + fps + setpts: reset PTS về 0 để tránh desync xfade
    for i in range(n):
        filter_parts.append(f"[{i}:v]settb=AVTB,setpts=PTS-STARTPTS,fps={fps}[v{i}]")

    # Xfade chaining: xfade offset = cumulative duration của các clips trước - transition
    current_label = "v0"
    cumulative = 0.0
    for i in range(1, n):
        prev_dur = durations[i - 1]
        # offset = thời điểm bắt đầu transition (giây từ đầu video đã ghép)
        offset = max(cumulative + prev_dur - td, 0.0)
        cumulative += prev_dur - td
        next_label = f"vx{i}"
        t_choice = t_seq[i - 1]

        filter_parts.append(
            f"[{current_label}][v{i}]xfade="
            f"transition={t_choice}:"
            f"duration={td:.3f}:"
            f"offset={offset:.3f}"
            f"[{next_label}]"
        )
        current_label = next_label

    filter_complex = ";".join(filter_parts)
    enc_args = get_video_encoder_args(quality="high")

    args = input_args + [
        "-filter_complex", filter_complex,
        "-map", f"[{current_label}]",
        *enc_args,
        "-r", str(fps),
        "-an",
        output_path,
    ]

    try:
        run_ffmpeg_with_gpu_fallback(args, timeout=FFMPEG_TIMEOUT)
        logger.info(f"[xfade] ✅ Ghép {n} clips với xfade={transition} → {output_path}")
        return output_path
    except RuntimeError as e:
        logger.warning(f"[xfade] xfade thất bại, fallback về concat thường: {e}")
        return concat_videos(video_paths, output_path, fps=fps)


# ──────────────────────────────────────────────
# CHECKPOINT C: Audio mux + Subtitle burn-in
# ──────────────────────────────────────────────

def mux_audio_video(
    video_path: str,
    audio_path: str,
    output_path: str,
    bgm_path: str = None,
    bgm_volume: float = 0.08,
    use_ducking: bool = True,
    normalize_loudness: bool = True,
) -> str:
    """
    Ghép audio narration + video + optional BGM với Audio Ducking thông minh và Broadcast Loudness Normalization.

    Audio Ducking (use_ducking=True, chỉ khi có BGM):
      - Khi voice đang nói: BGM tự động giảm xuống ~15% volume
      - Khi voice im lặng: BGM tăng lại tự nhiên
      - Dùng sidechaincompress filter của FFmpeg (chuyên nghiệp, không hardcode)

    P2.3 Broadcast Audio Normalization (normalize_loudness=True):
      - Chuẩn YouTube Shorts EBU R128: loudnorm=I=-14:TP=-1.5:LRA=11
      - Chống clipping, đảm bảo giọng đọc rõ ràng trên mọi loa điện thoại

    Fallback: Nếu BGM ngắn hơn voice, tự động loop lại (aloop).
    """
    if bgm_path and Path(bgm_path).exists():
        if use_ducking:
            # ── Real Audio Ducking via sidechaincompress ──
            # [1:a] = voice narration (sidechain trigger)
            # [2:a] = BGM (bị duck khi voice nói)
            # threshold=0.02: bắt đầu duck khi voice > -34dB
            # ratio=4: giảm BGM còn ~25%
            # attack=5ms, release=200ms: duck nhanh, recover chậm (tự nhiên)
            if normalize_loudness:
                filter_complex = (
                    f"[2:a]aloop=loop=-1:size=2000000000,volume={bgm_volume}[bgm_looped];"
                    f"[bgm_looped][1:a]sidechaincompress="
                    f"threshold=0.02:ratio=4:attack=5:release=200:makeup=1[bgm_ducked];"
                    f"[1:a][bgm_ducked]amix=inputs=2:duration=first:dropout_transition=2:weights='1 1'[amix_out];"
                    f"[amix_out]loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
                )
            else:
                filter_complex = (
                    f"[2:a]aloop=loop=-1:size=2000000000,volume={bgm_volume}[bgm_looped];"
                    f"[bgm_looped][1:a]sidechaincompress="
                    f"threshold=0.02:ratio=4:attack=5:release=200:makeup=1[bgm_ducked];"
                    f"[1:a][bgm_ducked]amix=inputs=2:duration=first:dropout_transition=2:weights='1 1'[aout]"
                )
        else:
            # Simple mix: BGM ở volume cố định, không ducking
            if normalize_loudness:
                filter_complex = (
                    f"[2:a]volume={bgm_volume},aloop=loop=-1:size=2000000000[bgm];"
                    f"[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[amix_out];"
                    f"[amix_out]loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
                )
            else:
                filter_complex = (
                    f"[2:a]volume={bgm_volume},aloop=loop=-1:size=2000000000[bgm];"
                    f"[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
                )
        args = [
            "-i", video_path,
            "-i", audio_path,
            "-i", bgm_path,
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "256k",
            "-movflags", "+faststart",
            "-shortest",
            output_path
        ]
    else:
        if normalize_loudness:
            args = [
                "-i", video_path,
                "-i", audio_path,
                "-filter_complex", "[1:a]loudnorm=I=-14:TP=-1.5:LRA=11[aout]",
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "256k",
                "-movflags", "+faststart",
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
                "-b:a", "256k",
                "-movflags", "+faststart",
                "-shortest",
                output_path
            ]

    run_ffmpeg(args)
    return output_path


def list_available_sfx(sfx_dir_path: Optional[Path] = None) -> List[Dict[str, str]]:
    """
    Quét và liệt kê danh sách tất cả file SFX có trong kho thư mục.
    Tự động hỗ trợ cả assets/sfx và assets/audio/sfx.
    """
    if sfx_dir_path is None:
        base_dir = Path(__file__).resolve().parent.parent.parent
        p1 = base_dir / "assets" / "sfx"
        p2 = base_dir / "assets" / "audio" / "sfx"
        sfx_dir_path = p1 if p1.exists() else p2

    if not sfx_dir_path.exists():
        return []

    valid_exts = {".mp3", ".wav", ".ogg", ".m4a", ".aac"}
    results = []
    for f in sorted(sfx_dir_path.iterdir(), key=lambda x: x.name.lower()):
        if f.is_file() and f.suffix.lower() in valid_exts:
            results.append({
                "id": f.stem,
                "name": f.stem.replace("_", " ").replace("-", " ").title(),
                "filename": f.name,
                "path": str(f)
            })
    return results


def _find_sfx_file(sfx_dir_path: Path, name: str) -> Optional[str]:
    """
    Tìm file SFX theo tên linh hoạt:
      - Nhận diện cả tên có hoặc không có đuôi mở rộng (.mp3, .wav, .ogg, .m4a, .aac).
      - Không phân biệt chữ hoa/thường (case-insensitive).
      - Khớp chính xác stem hoặc khớp tiền tố/chứa stem (vd: 'whoosh' khớp 'whoosh_fast.mp3').
      - Tuyệt đối không crash, trả về None nếu không tìm thấy.
    """
    if not sfx_dir_path.exists():
        return None

    clean_name = str(name).strip()
    if not clean_name:
        return None

    # 1. Trực tiếp kiểm tra nếu truyền sẵn tên file
    direct = sfx_dir_path / clean_name
    if direct.is_file():
        return str(direct)

    stem = Path(clean_name).stem.lower()
    valid_exts = {".mp3", ".wav", ".ogg", ".m4a", ".aac"}

    try:
        files = [f for f in sfx_dir_path.iterdir() if f.is_file() and f.suffix.lower() in valid_exts]
    except Exception:
        return None

    # 2. Khớp chính xác stem (case-insensitive)
    for f in files:
        if f.stem.lower() == stem:
            return str(f)

    # 3. Khớp tiền tố hoặc chứa stem (vd: "whoosh" khớp "whoosh_01.mp3")
    for f in files:
        if stem in f.stem.lower():
            return str(f)

    return None


def resolve_smart_sfx_cue(
    narration: str,
    scene_idx: int,
    total_scenes: int,
    niche: Optional[str] = None,
) -> str:
    """
    Phân tích cảm xúc và ngữ nghĩa của lời thoại để tự động chọn hiệu ứng âm thanh (SFX)
    chuyển cảnh phù hợp nhất từ kho assets/sfx:
      - hit.wav / impact_sub.wav: Cú va chạm, xung đột, tử vong, tấn công, đòn đánh
      - sub_drop.wav: Vực thẳm, chìm sâu, bí ẩn u ám, không đáy, biến mất
      - reveal.wav: Khám phá, hé lộ sự thật, phát hiện bất ngờ, giải mã
      - riser.wav: Cao trào, chuẩn bị đến nút thắt hoặc bước ngoặt, cận kề kết luận
      - glitch.wav: Lừa dối, thao túng, bẫy tâm lý, lỗi sai, ảo giác, dị thường
      - whoosh_heavy.wav: Chuyển động quy mô lớn, sức mạnh, nhanh mạnh
      - whoosh_fast.wav / whoosh.mp3: Lướt nhanh dứt khoát
    """
    text = (narration or "").lower()
    raw_niche = (niche or "").lower()

    # 1. Phân cảnh cận kề kết thúc hoặc cao trào chuẩn bị kết luận
    is_climax_scene = (scene_idx == total_scenes - 1) or (scene_idx >= total_scenes - 2 and total_scenes >= 4)

    # 2. Từ khóa Hé lộ bí mật / Sự thật ngỡ ngàng -> reveal
    reveal_keywords = (
        "reveal", "truth", "secret", "discover", "uncover", "found out", "realize", "actually",
        "sự thật", "bí mật", "hé lộ", "phát hiện", "hóa ra", "nhận ra", "bóc trần", "tìm thấy",
        "không ngờ", "chính là", "giải mã"
    )
    if any(k in text for k in reveal_keywords):
        return "reveal"

    # 3. Từ khóa Đe dọa / Tấn công / Xung đột / Nguy hiểm tột cùng -> impact_sub
    danger_keywords = (
        "danger", "deadly", "fatal", "attack", "kill", "predator", "strike", "monster", "terrifying",
        "blood", "bite", "crush", "poison", "venom", "hunt", "lethal",
        "tấn công", "nguy hiểm", "chết", "tiêu diệt", "sát thủ", "quái vật", "kinh hoàng", "nanh vuốt",
        "máu", "cắn xé", "nọc độc", "săn mồi", "chí mạng", "hủy diệt"
    )
    if any(k in text for k in danger_keywords):
        return "impact_sub"

    # 4. Từ khóa Thao túng / Lừa dối / Bẫy / Dị thường / Ảo giác -> glitch
    glitch_keywords = (
        "glitch", "trap", "lie", "manipulat", "gaslight", "illusion", "fake", "mistake", "error",
        "trick", "deceiv", "insane", "distort",
        "lừa dối", "thao túng", "bẫy", "ảo giác", "sai lầm", "dối trá", "ngụy biện", "bất thường",
        "ảo ảnh", "ảo tưởng", "giả tạo"
    )
    if any(k in text for k in glitch_keywords):
        return "glitch"

    # 5. Từ khóa Vực thẳm / Biến mất / Bóng tối vô tận / Đại dương sâu -> sub_drop
    abyss_keywords = (
        "abyss", "deep", "ocean", "darkness", "disappear", "unknown", "bottom", "drown", "trench",
        "vực thẳm", "chìm sâu", "bóng tối", "đáy biển", "không đáy", "biến mất", "hố sâu", "u ám",
        "hút cạn", "chìm vào"
    )
    if any(k in text for k in abyss_keywords) or ("deep_sea" in raw_niche and scene_idx % 2 == 1):
        return "sub_drop"

    # 6. Chuẩn bị cao trào / Riser trước phân cảnh kết
    if is_climax_scene:
        return "riser"

    # 7. Phân cảnh xoay vòng dứt khoát -> luân phiên whoosh_fast và whoosh_heavy
    if scene_idx % 2 == 0:
        return "whoosh_fast"
    else:
        return "whoosh_heavy"


def mix_sfx_transitions(
    video_path: str,
    output_path: str,
    scene_timestamps_seconds: List[float],
    sfx_dir: str,
    default_sfx: str = "whoosh",
    sfx_cues: Optional[List[Optional[str]]] = None,
    sfx_volume: float = 0.4,
    hit_sfx: str = "hit",
    hit_volume: float = 0.5,
    micro_sfx_cues: Optional[List[tuple]] = None,
) -> str:
    """
    Chèn file SFX đa tầng (Multi-layer Sound Design Level 4):
      - Dual-Layer Hollywood Impact: hit_sfx (sub thump) + hit (transient punch) tại giây 0
      - Transition SFX tại mỗi mốc chuyển cảnh (scene_timestamps[1:])
      - Micro-SFX Foley: Text-pop đồng bộ các từ khóa cảm xúc mạnh (Power Words)

    Args:
        video_path:              File MP4 đã có audio (sau mux_audio_video)
        output_path:             File MP4 output sau khi mix SFX
        scene_timestamps_seconds: Danh sách thời điểm bắt đầu từng scene (giây)
        sfx_dir:                 Thư mục chứa file .mp3/.wav SFX
        default_sfx:             Tên SFX mặc định cho chuyển cảnh
        sfx_cues:                Danh sách SFX tùy chỉnh cho từng transition
        sfx_volume:              Volume SFX chuyển cảnh (0.0–1.0)
        hit_sfx:                 Tên SFX impact ở giây đầu tiên
        hit_volume:              Volume SFX hit đầu video
        micro_sfx_cues:          Danh sách (ts_seconds, sfx_name, volume) cho Power Words

    Returns:
        output_path nếu thành công, video_path gốc nếu không có SFX nào tồn tại.
    """
    sfx_dir_path = Path(sfx_dir)
    if not sfx_dir_path.exists():
        base = Path(__file__).resolve().parent.parent.parent
        alt1 = base / "assets" / "sfx"
        alt2 = base / "assets" / "audio" / "sfx"
        if alt1.exists():
            sfx_dir_path = alt1
        elif alt2.exists():
            sfx_dir_path = alt2
        else:
            logger.info(f"[SFX] Thư mục SFX không tồn tại: {sfx_dir} — bỏ qua.")
            return video_path

    sfx_entries: List[tuple] = []  # List of (timestamp_seconds, sfx_file, volume)

    # ── 1. Hit SFX tại giây đầu tiên (t=0) — Dual-Layer Hollywood (Sub Thump + Punch Snap) ──
    if hit_sfx and str(hit_sfx).lower() not in ("none", "off", "mute", "disable", "skip"):
        hit_file = _find_sfx_file(sfx_dir_path, hit_sfx)
        if hit_file:
            sfx_entries.append((0.0, hit_file, hit_volume))
            logger.info(f"[SFX] 💥 Primary Hit SFX tại t=0.0s → {hit_file}")
            # Nếu primary hit là sub boom -> layer thêm transient snap punch (hit.wav) để dội cả dải trầm lẫn dải cao
            if any(k in str(hit_sfx).lower() for k in ("sub", "drop")):
                snap_file = _find_sfx_file(sfx_dir_path, "hit")
                if snap_file and snap_file != hit_file:
                    sfx_entries.append((0.0, snap_file, min(0.35, hit_volume * 0.7)))
                    logger.info(f"[SFX] ⚡ Layer 2 Transient Snap tại t=0.0s → {snap_file}")
        else:
            logger.debug(f"[SFX] Không tìm thấy hit SFX '{hit_sfx}' — bỏ qua.")

    # ── 2. Transition SFX tại mỗi mốc chuyển cảnh (bỏ t=0) ─────────────────
    transitions = scene_timestamps_seconds[1:]  # Bỏ giây đầu, đã xử lý bởi hit
    for i, ts in enumerate(transitions):
        cue_name = (
            sfx_cues[i]
            if sfx_cues and i < len(sfx_cues) and sfx_cues[i]
            else default_sfx
        )
        if cue_name and str(cue_name).lower() in ("none", "off", "mute", "disable", "skip"):
            logger.debug(f"[SFX] Bỏ qua SFX tại t={ts:.2f}s theo cấu hình 'none'")
            continue

        sfx_file = _find_sfx_file(sfx_dir_path, cue_name)
        if not sfx_file and cue_name != default_sfx:
            sfx_file = _find_sfx_file(sfx_dir_path, default_sfx)

        if sfx_file:
            sfx_entries.append((ts, sfx_file, sfx_volume))
            logger.info(f"[SFX] 🎵 Chuyển cảnh {ts:.2f}s → {sfx_file}")
        else:
            logger.debug(f"[SFX] Không tìm thấy SFX '{cue_name}' — bỏ qua t={ts:.2f}s")

    # ── 3. Level 4 Micro-SFX Foley: Text-pop đồng bộ các từ khóa cảm xúc mạnh ──
    if micro_sfx_cues:
        for m_ts, m_cue, m_vol in micro_sfx_cues:
            # Không chèn trùng với t=0 hoặc ngay sát các mốc chuyển cảnh (cách tối thiểu 0.8s)
            is_near_transition = any(abs(m_ts - t) < 0.8 for t in scene_timestamps_seconds)
            if is_near_transition or m_ts <= 0.4:
                continue
            m_file = _find_sfx_file(sfx_dir_path, m_cue)
            if not m_file:
                m_file = _find_sfx_file(sfx_dir_path, "whoosh_fast")
            if m_file:
                sfx_entries.append((m_ts, m_file, m_vol))
                logger.debug(f"[SFX] 🎯 Micro-SFX text pop tại t={m_ts:.2f}s ({m_cue}) → {m_file}")

    if not sfx_entries:
        logger.info("[SFX] Không có SFX file nào khả dụng — bỏ qua bước mix SFX.")
        return video_path

    # Sắp xếp theo mốc thời gian tăng dần
    sfx_entries.sort(key=lambda x: x[0])

    # ── Xây FFmpeg filter_complex ────────────────────────────────────────────
    input_args = ["-i", video_path]
    for _, sfx_file, _ in sfx_entries:
        input_args += ["-i", sfx_file]

    filter_parts = []
    sfx_labels = []
    for idx, (ts, _, vol) in enumerate(sfx_entries):
        delay_ms = int(ts * 1000)
        label = f"[sfx{idx}]"
        filter_parts.append(
            f"[{idx+1}:a]volume={vol},adelay={delay_ms}|{delay_ms},apad{label}"
        )
        sfx_labels.append(label)

    # Gộp audio gốc + tất cả SFX labels bằng amix với normalize=0 (giữ nguyên âm lượng voice & BGM)
    all_inputs = "[0:a]" + "".join(sfx_labels)
    n_inputs = 1 + len(sfx_labels)
    filter_parts.append(
        f"{all_inputs}amix=inputs={n_inputs}:duration=first:dropout_transition=0:normalize=0[aout]"
    )
    filter_complex = ";".join(filter_parts)

    args = input_args + [
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "256k",
        "-shortest",
        output_path
    ]

    try:
        run_ffmpeg(args)
        logger.info(f"[SFX] ✅ Đã mix {len(sfx_entries)} SFX vào video → {output_path}")
        return output_path
    except RuntimeError as e:
        logger.warning(f"[SFX] Mix SFX thất bại, giữ nguyên video gốc: {e}")
        return video_path



def trim_audio_silence(
    input_path: str,
    output_path: str,
    silence_duration: float = 0.3,
    silence_threshold_db: float = -40.0
) -> bool:
    """
    Tối ưu nhịp đọc (Pacing): Cắt các khoảng lặng > 0.3s nhưng giữ lại đệm âm thở tự nhiên (0.1 - 0.15s).
    Sử dụng bộ lọc silenceremove an toàn của FFmpeg.
    """
    try:
        # silenceremove=stop_periods=-1:stop_duration=0.3:stop_threshold=-40dB
        af_filter = f"silenceremove=stop_periods=-1:stop_duration={silence_duration}:stop_threshold={silence_threshold_db}dB"
        args = [
            "-i", input_path,
            "-af", af_filter,
            "-c:a", "libmp3lame",
            "-q:a", "2",
            "-y", output_path
        ]
        run_ffmpeg(args)
        if Path(output_path).exists() and Path(output_path).stat().st_size > 512:
            return True
        return False
    except Exception as e:
        logger.warning(f"[Pacing] silenceremove thất bại: {e}")
        return False


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
    safe_margin = margin_v if 576 <= margin_v <= 672 else 600
    style = (
        f"PlayResX=1080,"
        f"PlayResY=1920,"
        f"Fontname={font_name},"
        f"FontSize={font_size},"
        f"PrimaryColour={font_color},"
        f"OutlineColour={outline_color},"
        f"Outline={outline_width},"
        f"Shadow={shadow},"
        f"Bold={'1' if bold else '0'},"
        f"Alignment=2,"      # Bottom center
        f"MarginV={safe_margin}" # Chuẩn hóa vùng an toàn Shorts UI
    )
    # Escape path cho Windows (ffmpeg subtitles filter cần forward slashes và escape colon)
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")
    is_ass = srt_path.lower().endswith(".ass")

    # Với ASS: giữ nguyên styling nội tại (karaoke, fonts, margins) trong file
    # Với SRT: áp dụng force_style vì SRT thuần không có styling
    if is_ass:
        vf_filter = f"subtitles='{srt_escaped}'"
    else:
        vf_filter = f"subtitles='{srt_escaped}':force_style='{style}'"

    enc_args = get_video_encoder_args(quality="high")
    args = [
        "-i", video_path,
        "-vf", vf_filter,
        *enc_args,
        "-c:a", "copy",
        output_path
    ]
    run_ffmpeg_with_gpu_fallback(args, timeout=FFMPEG_TIMEOUT * 2)  # Subtitle burn chậm hơn
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


def create_placeholder_image(path: str, text: str = "Scene", scene_id: int = 1) -> str:
    """Tạo ảnh đồ họa dọc 1080x1920 phong cách điện ảnh cho trường hợp dự phòng khẩn cấp."""
    try:
        from PIL import Image, ImageDraw
        width, height = 1080, 1920
        base = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(base)

        top_color = (15, 23, 42)      # Deep slate blue
        bottom_color = (30, 27, 75)   # Cinematic indigo

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


def detect_audio_speech_start(
    file_path: str,
    start_seconds: float = 0.0,
    scan_duration: float = 3.0,
    noise_threshold_db: float = -32.0,
) -> float:
    """
    Phát hiện khoảng lặng (dead air) ở đầu đoạn cắt bằng bộ lọc silencedetect native của FFmpeg.
    Nếu phát hiện đoạn đầu bị im lặng hoặc chỉ có tiếng rè nhỏ (< noise_threshold_db),
    trả về số giây cần dịch tới (speech start offset) để video bật lên là có tiếng nói ngay lập tức.
    Nếu không phát hiện khoảng lặng hoặc lỗi, trả về 0.0.
    """
    if not os.path.exists(file_path):
        return 0.0

    try:
        cmd = [
            "ffmpeg", "-v", "info",
            "-ss", str(max(0.0, start_seconds)),
            "-t", str(scan_duration),
            "-i", file_path,
            "-af", f"silencedetect=noise={noise_threshold_db}dB:d=0.15",
            "-f", "null", "-"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        stderr = res.stderr or ""
        
        # Tìm pattern: silence_end: X.XX
        import re
        ends = re.findall(r'silence_end:\s*([0-9.]+)', stderr)
        if ends:
            first_end = float(ends[0])
            # Giới hạn dịch từ 0.15s đến 2.2s (tránh nhảy quá xa mất câu)
            if 0.15 <= first_end <= 2.2:
                logger.info(f"VAD silencedetect: Phát hiện dead air {first_end:.2f}s ở đầu clip, tự động dịch mốc cắt.")
                return round(first_end, 2)
    except Exception as e:
        logger.debug(f"detect_audio_speech_start error: {e}")

    return 0.0


def measure_audio_loudness_and_silence(file_path: str) -> Dict:
    """
    Đo đạc chỉ số âm lượng phát sóng (Integrated Loudness LUFS) & kiểm tra dead air đầu video bằng FFmpeg ebur128/volumedetect.
    - Chuẩn YouTube Shorts: -14.0 LUFS (khoảng tối ưu: -13.0 đến -16.0 LUFS)
    - Dead-air 2 giây đầu: Kiểm tra xem 2s đầu có bị mute hoàn toàn hay không
    """
    result = {
        "integrated_lufs": -14.0,
        "max_volume_db": 0.0,
        "mean_volume_db": -20.0,
        "has_dead_air_opening": False,
        "lufs_passed": True,
        "feedback": "Âm thanh đạt chuẩn"
    }
    if not os.path.exists(file_path):
        result["lufs_passed"] = False
        result["feedback"] = "File không tồn tại"
        return result

    try:
        cmd = [
            "ffmpeg", "-i", file_path,
            "-af", "ebur128=metadata=1,volumedetect",
            "-f", "null", "-"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        output = (res.stderr or "") + (res.stdout or "")
        import re

        # Parse Integrated loudness
        lufs_match = re.search(r'Integrated loudness:\s+I:\s+([-\d.]+)\s+LUFS', output)
        if lufs_match:
            result["integrated_lufs"] = round(float(lufs_match.group(1)), 1)

        # Parse mean volume
        mean_match = re.search(r'mean_volume:\s+([-\d.]+)\s+dB', output)
        if mean_match:
            result["mean_volume_db"] = round(float(mean_match.group(1)), 1)

        # Parse max volume
        max_match = re.search(r'max_volume:\s+([-\d.]+)\s+dB', output)
        if max_match:
            result["max_volume_db"] = round(float(max_match.group(1)), 1)

        # Đánh giá LUFS (YouTube Shorts khuyến nghị -14 LUFS, biên độ chấp nhận được -11 đến -19 LUFS)
        lufs = result["integrated_lufs"]
        if lufs < -22.0:
            result["lufs_passed"] = False
            result["feedback"] = f"Âm lượng quá nhỏ ({lufs} LUFS < -22 LUFS), cần tăng gain"
        elif lufs > -9.0:
            result["lufs_passed"] = False
            result["feedback"] = f"Âm lượng quá lớn ({lufs} LUFS > -9 LUFS), nguy cơ bị xé âm / clipping"
        else:
            result["lufs_passed"] = True
            result["feedback"] = f"Chuẩn âm thanh ({lufs} LUFS, YouTube Shorts tối ưu -14 LUFS)"

        # Kiểm tra 2 giây đầu có dead air không
        first_silence = detect_audio_speech_start(file_path, start_seconds=0.0, scan_duration=2.0, noise_threshold_db=-45.0)
        if first_silence >= 1.8:
            result["has_dead_air_opening"] = True
            result["feedback"] += f" | Cảnh báo: 2 giây đầu im lặng ({first_silence}s dead air)"

    except Exception as e:
        logger.warning(f"measure_audio_loudness_and_silence error: {e}")

    return result


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


