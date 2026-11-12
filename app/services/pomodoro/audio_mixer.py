"""
Pomodoro 3-Layer Audio Mixer (`app/services/pomodoro/audio_mixer.py`)
=====================================================================
Kết xuất âm thanh chuẩn 48kHz Stereo WAV cho từng Phase (`Prep`, `Focus`, `Break`):
  - Lớp 1: Nhạc nền Google Flow Music (`48kHz Stereo WAV`, fade-in/fade-out mượt giữa các bài).
  - Lớp 2: Chuông báo Singing Bowl (`STUDY_SFX_28`, `29`, `30`) vang lên chính xác ở giây 0.0.
  - Lớp 3: Lớp âm thanh không gian tùy chọn (`--ambience rain | fireplace | vinyl | pure`) ở mức êm dịu `-27dB`,
    cho phép xuất 3 phiên bản không gian khác nhau từ cùng kho nhạc (Module 6.1 #4).
"""

import os
import subprocess
from pathlib import Path
from typing import Dict, List

from app.services.pomodoro.scheduler import PhaseSegment


AMBIENCE_FILTER_PRESETS: Dict[str, str] = {
    "rain": (
        "anoisesrc=color=pink:sample_rate=48000:amplitude=0.032,"
        "highpass=f=160,lowpass=f=1450,tremolo=f=0.18:d=0.22,pan=stereo|c0=c0|c1=c0"
    ),
    "fireplace": (
        "anoisesrc=color=brown:sample_rate=48000:amplitude=0.048,"
        "lowpass=f=680,tremolo=f=0.35:d=0.32,pan=stereo|c0=c0|c1=c0"
    ),
    "vinyl": (
        "anoisesrc=color=white:sample_rate=48000:amplitude=0.011,"
        "bandpass=f=2200:width_type=h:w=1300,pan=stereo|c0=c0|c1=c0"
    ),
}


def render_phase_audio_clip(
    phase: PhaseSegment,
    work_dir: Path,
    ambience: str = "pure",
    ffmpeg_exe: str = "ffmpeg",
) -> Path:
    """
    Kết xuất âm thanh chuẩn 48kHz Stereo WAV cho 1 Phase (Prep / Focus / Break),
    tự động áp dụng fade-in/fade-out mượt cho từng bài nhạc, trộn lớp Ambience (`rain`/`fireplace`/`vinyl`)
    và tiếng Chuông báo (Bell SFX) ở giây 0.0.
    """
    phase_wav = work_dir / f"phase_{phase.index:02d}_{phase.phase_type.lower()}.wav"
    sub_clips: List[Path] = []

    n_tracks = len(phase.tracks_used)
    for t_idx, tr in enumerate(phase.tracks_used):
        sub_p = work_dir / f"p{phase.index:02d}_t{t_idx:02d}.wav"
        dur = float(tr["play_duration"])
        is_first = (t_idx == 0)
        is_last = (t_idx == n_tracks - 1)
        fade_in = min(0.85 if is_first else 0.35, dur * 0.12)
        fade_out = min(1.40 if is_last else 0.40, dur * 0.15)
        fade_out_st = max(0.0, dur - fade_out)
        vol = "0.85" if phase.phase_type == "BREAK" else "0.95"
        cmd = [
            ffmpeg_exe,
            "-y",
            "-v",
            "error",
            "-ss",
            "0",
            "-t",
            f"{dur:.2f}",
            "-i",
            tr["path"],
            "-af",
            f"volume={vol},afade=t=in:st=0:d={fade_in:.2f},afade=t=out:st={fade_out_st:.2f}:d={fade_out:.2f}",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(sub_p),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg audio track render failed ({tr['path']}): {res.stderr[-400:]}")
        sub_clips.append(sub_p)

    concat_list_file = work_dir / f"concat_p{phase.index:02d}.txt"
    lines = [f"file '{p.resolve().as_posix()}'" for p in sub_clips]
    concat_list_file.write_text("\n".join(lines), encoding="utf-8")

    music_only_wav = work_dir / f"music_p{phase.index:02d}.wav"
    subprocess.run(
        [
            ffmpeg_exe,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list_file),
            "-t",
            f"{phase.duration_sec:.2f}",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(music_only_wav),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    amb_key = (ambience or "pure").strip().lower()
    has_amb = amb_key in AMBIENCE_FILTER_PRESETS
    has_bell = bool(phase.bell_sfx_path and os.path.exists(phase.bell_sfx_path) and phase.duration_sec >= 4.0)

    if has_bell or has_amb:
        inputs = ["-i", str(music_only_wav)]
        mix_labels = ["[m]"]
        fc_parts = ["[0:a]volume=1.0[m]"]
        next_in_idx = 1

        if has_bell and phase.bell_sfx_path:
            bell_dur = min(6.5, phase.duration_sec)
            inputs += ["-i", phase.bell_sfx_path]
            fc_parts.append(
                f"[{next_in_idx}:a]atrim=0:{bell_dur:.2f},"
                f"afade=t=out:st={max(0.5, bell_dur - 2.0):.2f}:d=1.8,volume=1.25[b]"
            )
            mix_labels.append("[b]")
            next_in_idx += 1

        if has_amb:
            amb_src = f"{AMBIENCE_FILTER_PRESETS[amb_key]},atrim=0:{phase.duration_sec:.2f}[amb]"
            fc_parts.append(amb_src)
            mix_labels.append("[amb]")

        fc_parts.append(
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:"
            f"dropout_transition=0:normalize=0,alimiter=limit=0.95[outa]"
        )
        fc = ";".join(fc_parts)

        subprocess.run(
            [
                ffmpeg_exe,
                "-y",
                *inputs,
                "-filter_complex",
                fc,
                "-map",
                "[outa]",
                "-t",
                f"{phase.duration_sec:.2f}",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                str(phase_wav),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        music_only_wav.unlink(missing_ok=True)
    else:
        music_only_wav.replace(phase_wav)

    for sc in sub_clips:
        sc.unlink(missing_ok=True)
    concat_list_file.unlink(missing_ok=True)
    return phase_wav


def build_master_audio_stream(
    schedule: List[PhaseSegment],
    work_dir: Path,
    ambience: str = "pure",
    ffmpeg_exe: str = "ffmpeg",
) -> Path:
    """Ghép toàn bộ các Phase thành 1 file Master Audio WAV 48kHz Stereo duy nhất."""
    phase_wavs: List[Path] = []
    for phase in schedule:
        pw = render_phase_audio_clip(phase, work_dir, ambience=ambience, ffmpeg_exe=ffmpeg_exe)
        phase_wavs.append(pw)

    master_list = work_dir / "master_audio_concat.txt"
    master_list.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in phase_wavs),
        encoding="utf-8",
    )
    master_wav = work_dir / "master_pomodoro_audio.wav"
    subprocess.run(
        [
            ffmpeg_exe,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(master_list),
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(master_wav),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for pw in phase_wavs:
        pw.unlink(missing_ok=True)
    master_list.unlink(missing_ok=True)
    return master_wav
