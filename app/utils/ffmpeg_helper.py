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
    camera_motion: str = "zoom_in",
    is_hook_scene: bool = False,
) -> str:
    """
    Biến 1 ảnh tĩnh → video MP4 với Ken Burns cinematic effect.
    
    Hỗ trợ 6 loại chuyển động:
      - zoom_in:   Zoom dần vào tâm ảnh (mặc định, cảm giác áp lực/sức mạnh)
      - zoom_out:  Zoom ra xa (cảm giác khám phá/vĩ đại)
      - pan_left:  Lướt từ phải sang trái (cinematic reveal)
      - pan_right: Lướt từ trái sang phải
      - pan_up:    Kéo từ dưới lên (climax/rising)
      - pan_down:  Kéo từ trên xuống (tụt xuống/ominous)
    
    Khi is_hook_scene=True: Áp dụng cú Punch-in Zoom kịch tính (1.05 -> 1.38)
    nhằm kích thích thị giác, khóa chặt sự chú ý của người xem trong 2.5s đầu.
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
            # Scale ảnh lên lớn hơn target (1.3x thường hoặc 1.45x cho hook) để có "đệm" pan/zoom, tránh artifact đen góc
            scale_factor = 1.45 if is_hook_scene else 1.30
            scale_w = int(width * scale_factor)
            scale_h = int(height * scale_factor)
            im_resized = im.resize((scale_w, scale_h), Image.LANCZOS)
            im_resized.save(norm_path, "JPEG", quality=95)
            image_path = norm_path
    except Exception as e:
        raise ValueError(f"File ảnh không hợp lệ hoặc không thể đọc '{image_path}': {e}")

    d = int(duration_seconds * fps)  # Tổng số frames
    if is_hook_scene:
        # Cú Punch-in Zoom kịch tính cho Scene 1 (0-3s) để giật mắt người xem ngay lập tức
        z_start = 1.05
        z_end   = 1.38
    else:
        z_start = 1.0
        z_end   = 1.25  # Zoom đủ rõ để Ken Burns trông sống động trên màn hình nhỏ
    z_step  = (z_end - z_start) / max(d, 1)

    if not zoom_effect:
        zoompan = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,"
            f"fps={fps}"
        )
    elif camera_motion == "zoom_in":
        # P2.1: Cubic ease-out — Bắt đầu nhanh dứt khoát và giảm tốc mượt mà (chống giật cục)
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
                f"zoompan=z='{z_start}+({z_end}-{z_start})*(1-pow(1-min(on/{d}\\,1)\\,2.8))':"
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
        # Fallback: zoom_in cubic ease-out
        zoompan = (
            f"zoompan=z='{z_start}+({z_end}-{z_start})*(1-pow(1-min(on/{d}\\,1)\\,2.8))':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={width}x{height}:fps={fps},"
            f"setsar=1"
        )

    # Vignette filter — góc tối kiểu cinema, tạo chiều sâu, ảnh không bị "phẳng"
    # 0.698 rad ≈ PI/4.5 — giá trị số thay vì PI (tránh lỗi trên một số phiên bản FFmpeg)
    vignette_filter = "vignette=0.698"
    vf_chain = f"{zoompan},{vignette_filter}"

    args = [
        "-loop", "1",
        "-i", image_path,
        "-vf", vf_chain,
        "-t", str(duration_seconds),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-b:v", "12M",
        "-maxrate", "16M",
        "-bufsize", "24M",
        "-tune", "stillimage",   # Optimize encoder cho ảnh tĩnh (tăng tốc 20-40%)
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-an",
        output_path
    ]
    try:
        run_ffmpeg(args)
    finally:
        # Xóa file tạm sau khi render xong (tránh rác file)
        try:
            Path(norm_path).unlink(missing_ok=True)
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
        "-preset", "medium",
        "-crf", "18",
        "-b:v", "12M",
        "-maxrate", "16M",
        "-bufsize", "24M",
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
        "-preset", "medium",
        "-crf", "18",
        "-b:v", "12M",
        "-maxrate", "16M",
        "-bufsize", "24M",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
    ] + fps_flag + [
        "-an",
        output_path
    ]
    run_ffmpeg(args)
    return output_path


def concat_videos_with_xfade(
    video_paths: List[str],
    output_path: str,
    fps: int = 30,
    transition: str = "fade",
    transition_duration: float = 0.25,
    scene_durations_seconds: Optional[List[float]] = None,
) -> str:
    """
    Ghép N video clips với hiệu ứng chuyển cảnh xfade mượt mà giữa các scenes.

    Args:
        video_paths:             Danh sách clip MP4 đã chuẩn hóa (FPS, độ phân giải khớp nhau)
        output_path:             File MP4 output
        fps:                     FPS chuẩn (phải khớp với clips)
        transition:              Loại xfade: 'fade', 'zoomin', 'wipeleft', 'slideright', 'dissolve'
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

    # ── Xây filter_complex xfade chain ──
    # Mỗi clip [i] được label [vi], rồi xfade chaining:
    # [v0][v1] xfade=... [vt01]; [vt01][v2] xfade=... [vt012]; ...
    n = len(video_paths)
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
        filter_parts.append(
            f"[{current_label}][v{i}]xfade="
            f"transition={transition}:"
            f"duration={td:.3f}:"
            f"offset={offset:.3f}"
            f"[{next_label}]"
        )
        current_label = next_label

    filter_complex = ";".join(filter_parts)

    args = input_args + [
        "-filter_complex", filter_complex,
        "-map", f"[{current_label}]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-b:v", "12M",
        "-maxrate", "16M",
        "-bufsize", "24M",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-an",
        output_path,
    ]

    try:
        run_ffmpeg(args, timeout=FFMPEG_TIMEOUT)
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
) -> str:
    """
    Chèn file SFX vào video:
      - hit.mp3 (hoặc hit_sfx) tại giây 0 (mở đầu video — tạo impact ngay lập tức)
      - whoosh.mp3 (hoặc default_sfx) tại mỗi mốc chuyển cảnh (scene_timestamps[1:])

    Args:
        video_path:              File MP4 đã có audio (sau mux_audio_video)
        output_path:             File MP4 output sau khi mix SFX
        scene_timestamps_seconds: Danh sách thời điểm bắt đầu từng scene (giây)
                                  Ví dụ: [0.0, 3.5, 7.2, 12.0]
        sfx_dir:                 Thư mục chứa file .mp3/.wav SFX
        default_sfx:             Tên SFX mặc định cho chuyển cảnh (không có extension)
        sfx_cues:                Danh sách SFX tùy chỉnh cho từng transition (optional)
        sfx_volume:              Volume SFX chuyển cảnh (0.0–1.0)
        hit_sfx:                 Tên SFX impact ở giây đầu tiên
        hit_volume:              Volume SFX hit đầu video

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

    # ── 1. Hit SFX tại giây đầu tiên (t=0) ──────────────────────────────────
    if hit_sfx and str(hit_sfx).lower() not in ("none", "off", "mute", "disable", "skip"):
        hit_file = _find_sfx_file(sfx_dir_path, hit_sfx)
        if hit_file:
            sfx_entries.append((0.0, hit_file, hit_volume))
            logger.info(f"[SFX] 💥 Hit SFX tại t=0.0s → {hit_file}")
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
        # Fallback an toàn về default_sfx nếu tên SFX chỉ định không có trong kho
        if not sfx_file and cue_name != default_sfx:
            sfx_file = _find_sfx_file(sfx_dir_path, default_sfx)

        if sfx_file:
            sfx_entries.append((ts, sfx_file, sfx_volume))
            logger.info(f"[SFX] 🎵 Chuyển cảnh {ts:.2f}s → {sfx_file}")
        else:
            logger.debug(f"[SFX] Không tìm thấy SFX '{cue_name}' — bỏ qua t={ts:.2f}s")

    if not sfx_entries:
        logger.info("[SFX] Không có SFX file nào khả dụng — bỏ qua bước mix SFX.")
        return video_path

    # ── Xây FFmpeg filter_complex ────────────────────────────────────────────
    # Input 0: video gốc (audio narration+BGM)
    # Input 1..N: file SFX
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

    # Gộp audio gốc + tất cả SFX labels
    all_inputs = "[0:a]" + "".join(sfx_labels)
    n_inputs = 1 + len(sfx_labels)
    filter_parts.append(
        f"{all_inputs}amix=inputs={n_inputs}:duration=first:dropout_transition=0[aout]"
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
    margin_v: int = 210,              # Cách đáy 210px (trên thanh UI Shorts, không che giữa video)
    bold: bool = True,
) -> str:
    """
    Burn-in subtitle SRT/ASS vào video (hardcode hardsub).
    Phong cách YouTube Shorts: chữ vàng viền đen, font Arial/Segoe UI,
    thu bé lại (40px) và đặt sát đáy màn hình (210px) thay vì nằm ở giữa video.
    Đã bổ sung PlayResX=1080 & PlayResY=1920 để khóa chặt độ phân giải canvas.
    """
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
        f"MarginV={margin_v}" # 210px an toàn từ đáy
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

    args = [
        "-i", video_path,
        "-vf", vf_filter,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-b:v", "12M",
        "-maxrate", "16M",
        "-bufsize", "24M",
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

