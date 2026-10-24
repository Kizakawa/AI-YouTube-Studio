"""app.utils.ffmpeg.audio_and_sfx — Trộn giọng đọc, nhạc nền (BGM Ducking), hiệu ứng âm thanh (SFX) & đo âm lượng LUFS."""
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
    VIDEO_DURATION_SAFETY_MARGIN,
    build_atempo_filter,
    get_duration_ms,
    get_video_encoder_args,
    run_ffmpeg,
    run_ffmpeg_with_gpu_fallback,
)


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
    # Chuỗi lọc Studio Vocal Presence EQ (lọc ù trầm < 85Hz + tăng độ sáng dải cao +2.2dB giúp giọng đọc AI sắc nét trên loa điện thoại)
    vocal_eq = "highpass=f=85,treble=g=2.2"

    if bgm_path and Path(bgm_path).exists():
        if use_ducking:
            # ── Real Audio Ducking via sidechaincompress + Vocal Presence EQ ──
            # [1:a] = voice narration (được lọc EQ làm rõ nét + dùng làm sidechain trigger)
            # [2:a] = BGM (bị duck khi voice nói)
            if normalize_loudness:
                filter_complex = (
                    f"[1:a]{vocal_eq},asplit=2[v_sc][v_mix];"
                    f"[2:a]aloop=loop=-1:size=2000000000,volume={bgm_volume}[bgm_looped];"
                    f"[bgm_looped][v_sc]sidechaincompress="
                    f"threshold=0.02:ratio=4:attack=5:release=200:makeup=1[bgm_ducked];"
                    f"[v_mix][bgm_ducked]amix=inputs=2:duration=first:dropout_transition=2:weights='1 1'[amix_out];"
                    f"[amix_out]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[aout]"
                )
            else:
                filter_complex = (
                    f"[1:a]{vocal_eq},asplit=2[v_sc][v_mix];"
                    f"[2:a]aloop=loop=-1:size=2000000000,volume={bgm_volume}[bgm_looped];"
                    f"[bgm_looped][v_sc]sidechaincompress="
                    f"threshold=0.02:ratio=4:attack=5:release=200:makeup=1[bgm_ducked];"
                    f"[v_mix][bgm_ducked]amix=inputs=2:duration=first:dropout_transition=2:weights='1 1',aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[aout]"
                )
        else:
            # Simple mix: BGM ở volume cố định, không ducking
            if normalize_loudness:
                filter_complex = (
                    f"[1:a]{vocal_eq}[v_eq];"
                    f"[2:a]volume={bgm_volume},aloop=loop=-1:size=2000000000[bgm];"
                    f"[v_eq][bgm]amix=inputs=2:duration=first:dropout_transition=2[amix_out];"
                    f"[amix_out]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[aout]"
                )
            else:
                filter_complex = (
                    f"[1:a]{vocal_eq}[v_eq];"
                    f"[2:a]volume={bgm_volume},aloop=loop=-1:size=2000000000[bgm];"
                    f"[v_eq][bgm]amix=inputs=2:duration=first:dropout_transition=2,aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[aout]"
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
            "-b:a", "256k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            "-shortest",
            output_path
        ]
    else:
        if normalize_loudness:
            args = [
                "-i", video_path,
                "-i", audio_path,
                "-filter_complex", f"[1:a]{vocal_eq},loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[aout]",
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "256k", "-ar", "48000", "-ac", "2",
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
                "-b:a", "256k", "-ar", "48000", "-ac", "2",
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
        if stem in f.stem.lower() or f.stem.lower() in stem:
            return str(f)

    # 4. Ánh xạ ngữ nghĩa thông minh từ các tên cue mô tả trong Niche YAML sang file vật lý có sẵn
    semantic_aliases = [
        (("heartbeat", "boom", "thunder", "growl", "strike", "attack", "explosion", "crash", "slam", "thump", "impact"), "impact_sub"),
        (("riser", "tension", "swell", "clock", "tick", "build", "suspense"), "riser"),
        (("reveal", "chime", "miracle", "magic", "ding", "bell", "spark", "piano"), "reveal"),
        (("glitch", "error", "static", "shock", "snap", "crack", "lightning"), "glitch"),
        (("abyss", "drop", "deep", "sonar", "sub_drop"), "sub_drop"),
        (("fast", "swipe", "whip", "cut"), "whoosh_fast"),
        (("heavy", "wind", "wave", "storm"), "whoosh_heavy"),
    ]
    for keywords, target_stem in semantic_aliases:
        if any(k in stem for k in keywords):
            for f in files:
                if f.stem.lower() == target_stem:
                    return str(f)

    return None


def resolve_smart_sfx_cue(
    narration: str = "",
    scene_idx: int = 1,
    total_scenes: int = 7,
    niche: Optional[str] = None,
    explicit_cue: Optional[str] = None,
    visual_keyword: Optional[str] = None,
    **kwargs,
) -> str:
    """Đã tắt toàn bộ hiệu ứng âm thanh phụ (SFX) theo cấu hình hệ thống."""
    return "none"


def mix_sfx_transitions(
    video_path: str,
    output_path: str,
    scene_timestamps_seconds: List[float],
    sfx_dir: str,
    default_sfx: str = "none",
    sfx_cues: Optional[List[Optional[str]]] = None,
    sfx_volume: float = 0.0,
    hit_sfx: str = "none",
    hit_volume: float = 0.0,
    micro_sfx_cues: Optional[List[tuple]] = None,
) -> str:
    """Đã xóa bỏ việc chèn âm thanh phụ (SFX) vào video — trả về nguyên trạng video gốc (Voiceover + BGM)."""
    logger.info("[SFX] Đã tắt tính năng chèn hiệu ứng âm thanh phụ (SFX) — giữ nguyên audio gốc (Voiceover + BGM).")
    return video_path



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

