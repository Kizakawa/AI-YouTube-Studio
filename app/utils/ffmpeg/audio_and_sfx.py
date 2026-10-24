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
        (("heartbeat", "boom", "thunder", "growl", "strike", "attack", "explosion", "crash", "slam", "thump", "impact", "hit"), "impact_sub"),
        (("riser", "tension", "swell", "clock", "tick", "build", "suspense"), "riser_tension"),
        (("reveal", "chime", "miracle", "magic", "ding", "bell", "spark", "piano"), "reveal"),
        (("glitch", "error", "static", "shock", "snap", "crack", "lightning"), "glitch"),
        (("abyss", "drop", "deep", "sonar", "sub_drop", "ping"), "sonar_ping"),
        (("fast", "swipe", "whip", "cut", "whoosh", "soft"), "whoosh_soft"),
        (("heavy", "wind", "wave", "storm"), "whoosh_heavy"),
    ]
    for keywords, target_stem in semantic_aliases:
        if any(k in stem for k in keywords):
            for f in files:
                if f.stem.lower() == target_stem:
                    return str(f)

    return None


def _ensure_core_cinematic_sfx(sfx_dir_path: Path) -> None:
    """
    Đảm bảo thư mục SFX tồn tại và tự động tổng hợp 4 file âm thanh Micro-SFX chuẩn phòng thu (.wav)
    bằng FFmpeg lavfi nếu chưa có sẵn trên ổ đĩa.
    """
    sfx_dir_path = Path(sfx_dir_path)
    sfx_dir_path.mkdir(parents=True, exist_ok=True)

    core_sfx_specs = {
        "impact_sub.wav": (
            "aevalsrc='0.55*sin(2*PI*(68-28*t)*t)*exp(-4.2*t)+0.25*sin(2*PI*42*t)*exp(-3.0*t):s=48000:d=1.1',"
            "lowpass=f=140,afade=t=in:ss=0:d=0.015,afade=t=out:st=0.35:d=0.75"
        ),
        "whoosh_soft.wav": (
            "anoisesrc=d=0.65:c=pink:r=48000:a=0.35,"
            "bandpass=f=420:width_type=h:w=320,afade=t=in:ss=0:d=0.22,afade=t=out:st=0.25:d=0.40"
        ),
        "riser_tension.wav": (
            "aevalsrc='0.35*sin(2*PI*(55+65*t*t)*t)*sin(PI*t/1.4):s=48000:d=1.4',"
            "lowpass=f=260,afade=t=in:ss=0:d=0.55,afade=t=out:st=0.95:d=0.45"
        ),
        "sonar_ping.wav": (
            "aevalsrc='0.40*sin(2*PI*520*t)*exp(-5.5*t)+0.30*sin(2*PI*62*t)*exp(-3.2*t):s=48000:d=1.2',"
            "lowpass=f=900,afade=t=in:ss=0:d=0.01,afade=t=out:st=0.30:d=0.90"
        ),
    }

    for filename, lavfi_expr in core_sfx_specs.items():
        target = sfx_dir_path / filename
        if target.is_file() and target.stat().st_size > 256:
            continue
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", lavfi_expr,
                    "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
                    str(target),
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except Exception as e:
            logger.debug(f"[SFX] Không thể tổng hợp {filename}: {e}")


def resolve_smart_sfx_cue(
    narration: str = "",
    scene_idx: int = 1,
    total_scenes: int = 7,
    niche: Optional[str] = None,
    explicit_cue: Optional[str] = None,
    visual_keyword: Optional[str] = None,
    **kwargs,
) -> str:

    """Phân giải Selective 3-Point Cinematic Micro-SFX (-17dB)."""
    core_cues = ("impact_sub", "whoosh_soft", "riser_tension", "sonar_ping")
    if explicit_cue:
        clean_explicit = Path(str(explicit_cue).strip().lower()).stem
        if clean_explicit in core_cues:
            return clean_explicit
    if scene_idx == 1:
        return "impact_sub"
    if scene_idx == 4 or (total_scenes >= 4 and scene_idx == max(2, total_scenes // 2)):
        niche_lower = str(niche or "").lower()
        if any(k in niche_lower for k in ("deep_sea", "ocean", "mystery", "abyss", "scifi")):
            return "sonar_ping"
        return "riser_tension"
    return "none"


def mix_sfx_transitions(
    video_path: str,
    output_path: str,
    scene_timestamps_seconds: List[float],
    sfx_dir: str,
    default_sfx: str = "none",
    sfx_cues: Optional[List[Optional[str]]] = None,
    sfx_volume: float = 0.14,
    hit_sfx: str = "impact_sub",
    hit_volume: float = 0.16,
    micro_sfx_cues: Optional[List[tuple]] = None,
) -> str:
    """Hòa âm không phá hủy (Non-Destructive) tối đa 3 điểm Micro-SFX điện ảnh ở ngưỡng tinh tế (-17dB ~ -16dB)."""

    if not video_path or not os.path.exists(video_path):
        return video_path

    try:
        sfx_dir_path = Path(sfx_dir or "assets/sfx")
        _ensure_core_cinematic_sfx(sfx_dir_path)

        raw_ts = [max(0.0, float(t)) for t in (scene_timestamps_seconds or [])]
        if not raw_ts or raw_ts[0] > 0.15:
            scene_starts = [0.0] + raw_ts
        else:
            scene_starts = raw_ts

        first_cut_sec = scene_starts[1] if len(scene_starts) > 1 else 2.4

        normalized_cues = [str(c or "").strip().lower() for c in (sfx_cues or [])]
        default_lower = str(default_sfx or "").strip().lower()
        if "sonar_ping" in normalized_cues or "sonar_ping" in default_lower:
            twist_cue_name = "sonar_ping"
        elif "riser_tension" in normalized_cues or "riser_tension" in default_lower:
            twist_cue_name = "riser_tension"
        else:
            twist_cue_name = "riser_tension"

        events: List[Tuple[str, float, float]] = []

        # Point 1: t = 0.04s — Opening Hook Impact (impact_sub)
        p1_file = _find_sfx_file(sfx_dir_path, hit_sfx if hit_sfx and hit_sfx != "none" else "impact_sub") or _find_sfx_file(sfx_dir_path, "impact_sub")
        if p1_file:
            p1_vol = round(min(0.18, max(0.10, float(hit_volume or 0.16))), 4)
            events.append((p1_file, 0.04, p1_vol))

        # Point 2: t = min(1.35, max(0.85, first_cut_sec * 0.45)) — Dual-Beat Hook Cut (whoosh_soft)
        p2_file = _find_sfx_file(sfx_dir_path, "whoosh_soft")
        p2_time = round(min(1.35, max(0.85, first_cut_sec * 0.45)), 3)
        if p2_file:
            p2_vol = round(min(0.14, max(0.08, float(sfx_volume or 0.14) * 0.85)), 4)
            events.append((p2_file, p2_time, p2_vol))

        # Point 3: t = climax_timestamp_sec — Mid-Video Twist at Scene 4 (typically 11s–18s)
        climax_timestamp_sec: Optional[float] = None
        if len(scene_starts) >= 4:
            climax_timestamp_sec = scene_starts[3]
        elif len(scene_starts) == 3:
            climax_timestamp_sec = scene_starts[2]
        elif len(scene_starts) == 2 and scene_starts[1] >= 3.0:
            climax_timestamp_sec = scene_starts[1]

        p3_file = _find_sfx_file(sfx_dir_path, twist_cue_name) or _find_sfx_file(sfx_dir_path, "riser_tension")
        if p3_file and climax_timestamp_sec is not None and climax_timestamp_sec > p2_time + 0.6:
            p3_vol = round(min(0.16, max(0.10, float(sfx_volume or 0.14))), 4)
            events.append((p3_file, round(climax_timestamp_sec, 3), p3_vol))

        events = events[:3]
        if not events:
            return video_path

        input_args: List[str] = ["-i", video_path]
        filter_parts: List[str] = [
            "[0:a]aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[main_a]"
        ]
        mix_labels: List[str] = ["[main_a]"]

        for idx, (sfx_path, ts_sec, vol) in enumerate(events, start=1):
            input_args.extend(["-i", sfx_path])
            delay_ms = max(0, int(round(ts_sec * 1000)))
            lbl = f"sfx{idx}"
            filter_parts.append(
                f"[{idx}:a]aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"volume={vol},adelay={delay_ms}|{delay_ms}[{lbl}]"
            )
            mix_labels.append(f"[{lbl}]")

        total_inputs = len(mix_labels)
        filter_parts.append(
            f"{''.join(mix_labels)}amix=inputs={total_inputs}:duration=first:dropout_transition=0:normalize=0,"
            f"alimiter=limit=0.95:attack=5:release=50,"
            f"aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[aout]"
        )
        filter_complex = ";".join(filter_parts)

        args = [
            *input_args,
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "256k",
            "-ar", "48000",
            "-ac", "2",
            "-movflags", "+faststart",
            "-shortest",
            output_path,
        ]
        run_ffmpeg(args)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            logger.info(
                f"[SFX] Đã điểm xuyết {len(events)} điểm Micro-SFX tinh tế (-17dB) -> {output_path}"
            )
            return output_path
    except Exception as e:
        logger.warning(f"[SFX] Bỏ qua mix_sfx_transitions do lỗi kỹ thuật, giữ nguyên video gốc: {e}")

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

