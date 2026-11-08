"""
Pomodoro 3-Layer Audio Mixer (`app/services/pomodoro/audio_mixer.py`)
=====================================================================
Kết xuất âm thanh chuẩn 48kHz Stereo WAV cho từng Phase (`Prep`, `Focus`, `Break`),
tự động áp dụng fade-in/fade-out mượt giữa các bài nhạc và trộn tiếng Chuông Singing Bowl
(`STUDY_SFX_28`, `29`, `30`) tại giây 0.0 của mỗi phiên.
"""

import os
import subprocess
from pathlib import Path
from typing import List

from app.services.pomodoro.scheduler import PhaseSegment


def render_phase_audio_clip(
    phase: PhaseSegment,
    work_dir: Path,
    ffmpeg_exe: str = "ffmpeg",
) -> Path:
    """
    Kết xuất âm thanh chuẩn 48kHz Stereo WAV cho 1 Phase (Prep / Focus / Break),
    tự động áp dụng fade-in/fade-out mượt cho từng bài nhạc và trộn tiếng Chuông báo (Bell SFX) ở giây 0.0.
    """
    phase_wav = work_dir / f"phase_{phase.index:02d}_{phase.phase_type.lower()}.wav"
    sub_clips: List[Path] = []

    for t_idx, tr in enumerate(phase.tracks_used):
        sub_p = work_dir / f"p{phase.index:02d}_t{t_idx:02d}.wav"
        dur = float(tr["play_duration"])
        fade_in = min(1.5, dur * 0.15)
        fade_out = min(2.2, dur * 0.2)
        fade_out_st = max(0.0, dur - fade_out)
        vol = "0.85" if phase.phase_type == "BREAK" else "0.95"
        cmd = [
            ffmpeg_exe,
            "-y",
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
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

    if phase.bell_sfx_path and os.path.exists(phase.bell_sfx_path) and phase.duration_sec >= 4.0:
        bell_dur = min(6.5, phase.duration_sec)
        fc = (
            f"[0:a]volume=1.0[m];"
            f"[1:a]atrim=0:{bell_dur:.2f},afade=t=out:st={max(0.5, bell_dur-2.0):.2f}:d=1.8,volume=1.25[b];"
            f"[m][b]amix=inputs=2:duration=first:dropout_transition=0,alimiter=limit=0.95[outa]"
        )
        subprocess.run(
            [
                ffmpeg_exe,
                "-y",
                "-i",
                str(music_only_wav),
                "-i",
                phase.bell_sfx_path,
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
    ffmpeg_exe: str = "ffmpeg",
) -> Path:
    """Ghép toàn bộ các Phase thành 1 file Master Audio WAV 48kHz Stereo duy nhất."""
    phase_wavs: List[Path] = []
    for phase in schedule:
        pw = render_phase_audio_clip(phase, work_dir, ffmpeg_exe=ffmpeg_exe)
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
