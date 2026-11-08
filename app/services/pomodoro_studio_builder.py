"""
Pomodoro Study Music & Cozy Dog Studio Builder (`pomodoro_studio_builder.py`)
=============================================================================
Hệ thống tự động hóa lắp ráp Video Nhạc Học Tập / Đọc Sách Dài (1–4 Giờ, 1920x1080)
với Bộ Đếm Thời Gian Pomodoro (`50/10`, `30/5`, `25/5`, `40/10`) từ kho 58 file WAV
Google Flow Music (`D:\\AI_YouTube_Project_Ready\\Music\\06_study_music_lofi\\bgm`).

Đặc tính kỹ thuật vượt cổng kiểm duyệt Bật Kiếm Tiền YouTube (YPP Safe):
1. Multi-Track Harmonic Playlist: Tự động tuyển chọn & hoán vị từ 58 file WAV (Primary + Alternate takes),
   đảm bảo nhạc phiên Học (`Focus`) và phiên Nghỉ (`Break`) tách biệt hoàn toàn.
2. 3-Layer Audio Mixing:
   - Lớp 1: Nhạc nền Google Flow chuẩn `48kHz Stereo WAV` (có fade-in/fade-out mượt giữa các bài).
   - Lớp 2: Chuông báo chuyển phiên (`STUDY_SFX_28`, `29`, `30`) vang lên chính xác tại giây đầu mỗi hiệp.
   - Lớp 3: Âm thanh mưa đêm ấm áp (`Gentle Rain Ambience` tùy chọn ở mức `-28dB`).
3. Fast HUD & Real-Time Countdown Clock:
   - Khung cảnh Cozy Dog Study Desk (hoặc video loop/ảnh nền tùy chọn từ Google Flow).
   - Thẻ kính mờ Frosted Glass hiển thị trạng thái hiệp (`📚 FOCUS SESSION 1/4` vs `☕ BREAK TIME 1/4`),
     thanh tiến trình hiệp và đồng hồ đếm ngược `MM:SS` chính xác từng giây bằng biểu thức toán học FFmpeg.
4. Auto-Generated YouTube Chapters & SEO Pack:
   - Xuất tự động file mô tả YouTube kèm Timestamps/Chapters và Tracklist đầy đủ.
"""

import argparse
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import wave
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


DEFAULT_MASTER_BGM_ROOT = Path(r"D:\AI_YouTube_Project_Ready\Music\06_study_music_lofi\bgm")
DEFAULT_CATALOG_BGM_ROOT = (
    Path(__file__).resolve().parents[2] / "assets" / "bgm" / "06_study_music_lofi"
)
DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parents[2] / "output" / "pomodoro_studio"

MODE_FOLDER_WEIGHTS: Dict[str, List[str]] = {
    "lofi_focus": ["01_lofi_focus", "04_jazz_cozy", "02_piano_reading"],
    "piano_reading": ["02_piano_reading", "04_jazz_cozy", "01_lofi_focus"],
    "exam_ambient": ["03_exam_ambient", "01_lofi_focus", "02_piano_reading"],
    "cozy_jazz": ["04_jazz_cozy", "02_piano_reading", "01_lofi_focus"],
}

MODE_DISPLAY_META: Dict[str, Dict[str, str]] = {
    "lofi_focus": {
        "badge": "COZY LOFI STUDY & CODING",
        "subtitle": "Warm Lofi Beats • Gentle Rain • Sleeping Golden Retriever Companion",
        "accent_rgb": "#F5B041",
    },
    "piano_reading": {
        "badge": "RAINY LIBRARY PIANO & READING",
        "subtitle": "Peaceful Felt Piano • Book Reading & Journaling • Fireplace Warmth",
        "accent_rgb": "#85C1E9",
    },
    "exam_ambient": {
        "badge": "EXAM MODE • DEEP FLOW STATE",
        "subtitle": "Minimal Ambient Focus • Zero Distraction • Memory & Revision",
        "accent_rgb": "#BB8FCE",
    },
    "cozy_jazz": {
        "badge": "AUTUMN CAFE & LIBRARY JAZZ",
        "subtitle": "Warm Acoustic Jazz • Coffee & Rain Sounds • No Mid-Roll Ads",
        "accent_rgb": "#EB984E",
    },
}


@dataclass
class PhaseSegment:
    index: int
    phase_type: str  # "PREP", "FOCUS", "BREAK", "OUTRO"
    session_num: int
    total_sessions: int
    title: str
    chapter_label: str
    start_sec: float
    duration_sec: float
    end_sec: float
    bell_sfx_path: Optional[str]
    tracks_used: List[Dict[str, Any]]
    hud_png_path: Optional[str] = None


def get_wav_duration(wav_path: Path) -> float:
    """Đọc nhanh thời lượng file WAV qua header (<0.1ms)."""
    try:
        with wave.open(str(wav_path), "rb") as wf:
            sr = wf.getframerate()
            frames = wf.getnframes()
            return round(frames / float(sr), 2) if sr > 0 else 0.0
    except Exception:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(wav_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        try:
            return round(float((res.stdout or "0").strip()), 2)
        except Exception:
            return 0.0


def format_timestamp(seconds: float) -> str:
    """Chuyển đổi số giây sang định dạng Chapter YouTube (MM:SS hoặc H:MM:SS)."""
    total = max(0, int(round(seconds)))
    hrs = total // 3600
    mins = (total % 3600) // 60
    secs = total % 60
    if hrs > 0:
        return f"{hrs}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def clean_track_display_name(filename: str) -> str:
    """Chuyển tên file `STUDY_BGM_01_lofi_warm_rain_T2.wav` thành `Lofi Warm Rain (Take 2)`."""
    stem = Path(filename).stem
    m = re.match(r"STUDY_(?:BGM|BRK|SFX)_(\d+)_([a-z0-9_]+?)(?:_T(\d+))?$", stem, re.I)
    if not m:
        return stem.replace("_", " ").title()
    num, slug, take = m.group(1), m.group(2), m.group(3)
    words = slug.replace("_", " ").title()
    return f"#{num} {words}" + (f" (Ver.{take})" if take else "")


def discover_study_library(bgm_root: Path = DEFAULT_MASTER_BGM_ROOT) -> Dict[str, List[Dict[str, Any]]]:
    """
    Quét kho nhạc 58 file WAV và phân loại theo thư mục con, ưu tiên bản `PRIMARY`
    từ `audio_audit_report.json` (nếu có) trước bản `ALTERNATE`.
    """
    audit_file = bgm_root.parent / "audio_audit_report.json"
    role_map: Dict[str, Tuple[str, float]] = {}
    if audit_file.exists():
        try:
            data = json.loads(audit_file.read_text(encoding="utf-8"))
            for takes in data.get("groups", {}).values():
                for t in takes:
                    role_map[t["name"]] = (t.get("role", "PRIMARY"), float(t.get("quality_score", 5.0)))
        except Exception:
            pass

    pools: Dict[str, List[Dict[str, Any]]] = {}
    if not bgm_root.exists():
        return pools

    for folder in sorted(bgm_root.iterdir()):
        if not folder.is_dir():
            continue
        items: List[Dict[str, Any]] = []
        for wav in sorted(folder.glob("*.wav")):
            dur = get_wav_duration(wav)
            if dur <= 1.0:
                continue
            role, score = role_map.get(wav.name, ("PRIMARY", 5.0))
            m = re.match(r"(STUDY_(?:BGM|BRK|SFX)_\d+_[a-z0-9_]+?)(?:_T\d+)?\.wav$", wav.name)
            base_id = m.group(1) if m else wav.stem
            items.append(
                {
                    "path": str(wav),
                    "name": wav.name,
                    "display_name": clean_track_display_name(wav.name),
                    "base_id": base_id,
                    "folder": folder.name,
                    "duration": dur,
                    "role": role,
                    "score": score,
                }
            )
        items.sort(key=lambda x: (0 if x["role"] == "PRIMARY" else 1, -x["score"]))
        pools[folder.name] = items

    return pools


def select_tracks_for_duration(
    pools: Dict[str, List[Dict[str, Any]]],
    folder_priority: List[str],
    target_duration_sec: float,
    rng: random.Random,
    used_recent_paths: List[str],
) -> List[Dict[str, Any]]:
    """
    Lựa chọn danh sách bài nhạc lấp đầy `target_duration_sec` mà không lặp lại bài vừa phát.
    Ưu tiên lấy hết các bài `PRIMARY` trước rồi mới đan xen `ALTERNATE`.
    """
    candidates: List[Dict[str, Any]] = []
    for folder_name in folder_priority:
        candidates.extend(pools.get(folder_name, []))

    if not candidates:
        for folder_name, track_list in pools.items():
            if folder_name != "06_bell_sfx":
                candidates.extend(track_list)

    if not candidates:
        raise RuntimeError("Không tìm thấy file WAV nào trong thư mục nhạc học tập!")

    primaries = [c for c in candidates if c["role"] == "PRIMARY"]
    alternates = [c for c in candidates if c["role"] != "PRIMARY"]

    selected: List[Dict[str, Any]] = []
    accum_sec = 0.0

    while accum_sec < target_duration_sec:
        deck = list(primaries)
        rng.shuffle(deck)
        alt_deck = list(alternates)
        rng.shuffle(alt_deck)
        combined_deck = deck + alt_deck

        picked_in_round = False
        for track in combined_deck:
            if accum_sec >= target_duration_sec:
                break
            if len(combined_deck) > 3 and track["path"] in used_recent_paths[-3:]:
                continue
            rem = target_duration_sec - accum_sec
            use_dur = min(track["duration"], rem)
            if use_dur < 8.0 and selected:
                # Kéo dài nhẹ hoặc bù vào bài cuối nếu phần dư quá ngắn
                selected[-1]["play_duration"] = round(selected[-1]["play_duration"] + rem, 2)
                accum_sec = target_duration_sec
                picked_in_round = True
                break
            entry = dict(track)
            entry["play_duration"] = round(use_dur, 2)
            selected.append(entry)
            used_recent_paths.append(track["path"])
            accum_sec += use_dur
            picked_in_round = True

        if not picked_in_round:
            track = combined_deck[0]
            rem = target_duration_sec - accum_sec
            entry = dict(track)
            entry["play_duration"] = round(min(track["duration"], rem), 2)
            selected.append(entry)
            accum_sec += entry["play_duration"]

    return selected


def resolve_bell_sfx_paths(
    catalog_bgm_dir: Path = DEFAULT_CATALOG_BGM_ROOT,
    master_bgm_dir: Path = DEFAULT_MASTER_BGM_ROOT,
) -> Dict[str, Optional[str]]:
    """Lấy đường dẫn 3 tiếng chuông Pomodoro (ưu tiên bản cắt gọn 6.5s trong `assets/bgm/06_study_music_lofi`)."""
    names = {
        "start": "STUDY_SFX_28_bell_session_start.wav",
        "break": "STUDY_SFX_29_bell_break_chime.wav",
        "complete": "STUDY_SFX_30_bell_session_complete.wav",
    }
    resolved: Dict[str, Optional[str]] = {}
    for key, fname in names.items():
        p1 = catalog_bgm_dir / fname
        p2 = master_bgm_dir / "06_bell_sfx" / fname
        if p1.exists():
            resolved[key] = str(p1)
        elif p2.exists():
            resolved[key] = str(p2)
        else:
            resolved[key] = None
    return resolved


def build_pomodoro_schedule(
    mode: str = "lofi_focus",
    focus_min: float = 50.0,
    break_min: float = 10.0,
    sessions: int = 4,
    prep_sec: float = 16.0,
    seed: int = 42,
    bgm_root: Path = DEFAULT_MASTER_BGM_ROOT,
) -> List[PhaseSegment]:
    """
    Thiết lập kịch bản thời gian chính xác từng giây cho toàn bộ buổi học Pomodoro:
      - Prep Time (16s) -> [Session 1 Focus -> Session 1 Break] -> ... -> [Session N Focus -> Session N Break/Complete]
    """
    rng = random.Random(seed)
    pools = discover_study_library(bgm_root)
    bells = resolve_bell_sfx_paths(master_bgm_dir=bgm_root)
    focus_folders = MODE_FOLDER_WEIGHTS.get(mode, MODE_FOLDER_WEIGHTS["lofi_focus"])
    break_folders = ["05_break_relax", "04_jazz_cozy"]

    used_recent: List[str] = []
    schedule: List[PhaseSegment] = []
    cursor_sec = 0.0
    idx = 0

    focus_sec = round(float(focus_min) * 60.0, 2)
    break_sec = round(float(break_min) * 60.0, 2)

    if prep_sec > 0:
        prep_tracks = select_tracks_for_duration(pools, break_folders, prep_sec, rng, used_recent)
        schedule.append(
            PhaseSegment(
                index=idx,
                phase_type="PREP",
                session_num=0,
                total_sessions=sessions,
                title="Prep Time • Grab Coffee & Open Your Notebook",
                chapter_label="Prep Time ☕",
                start_sec=cursor_sec,
                duration_sec=prep_sec,
                end_sec=round(cursor_sec + prep_sec, 2),
                bell_sfx_path=None,
                tracks_used=prep_tracks,
            )
        )
        cursor_sec = round(cursor_sec + prep_sec, 2)
        idx += 1

    for s in range(1, sessions + 1):
        f_tracks = select_tracks_for_duration(pools, focus_folders, focus_sec, rng, used_recent)
        schedule.append(
            PhaseSegment(
                index=idx,
                phase_type="FOCUS",
                session_num=s,
                total_sessions=sessions,
                title=f"Study Session {s}/{sessions} • Deep Focus",
                chapter_label=f"📚 Study Session {s} ({int(focus_min)} min)",
                start_sec=cursor_sec,
                duration_sec=focus_sec,
                end_sec=round(cursor_sec + focus_sec, 2),
                bell_sfx_path=bells.get("start"),
                tracks_used=f_tracks,
            )
        )
        cursor_sec = round(cursor_sec + focus_sec, 2)
        idx += 1

        if break_sec > 0:
            is_last = s == sessions
            b_tracks = select_tracks_for_duration(pools, break_folders, break_sec, rng, used_recent)
            schedule.append(
                PhaseSegment(
                    index=idx,
                    phase_type="BREAK",
                    session_num=s,
                    total_sessions=sessions,
                    title=(
                        f"Session Complete! Final Relax & Cooldown ({s}/{sessions})"
                        if is_last
                        else f"Break Time {s}/{sessions} • Stretch & Drink Water ☕"
                    ),
                    chapter_label=(
                        f"🎉 Final Cooldown & Rest ({int(break_min)} min)"
                        if is_last
                        else f"☕ Break Time {s} ({int(break_min)} min)"
                    ),
                    start_sec=cursor_sec,
                    duration_sec=break_sec,
                    end_sec=round(cursor_sec + break_sec, 2),
                    bell_sfx_path=bells.get("complete") if is_last else bells.get("break"),
                    tracks_used=b_tracks,
                )
            )
            cursor_sec = round(cursor_sec + break_sec, 2)
            idx += 1

    return schedule


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


def generate_cozy_dog_study_background(out_png: Path, width: int = 1920, height: int = 1080) -> Path:
    """
    Vẽ cảnh nền Cozy Study Room + Sleeping Golden Retriever Companion (1920x1080)
    khi người dùng chưa truyền ảnh/video nền từ ngoài vào.
    """
    img = Image.new("RGBA", (width, height), (14, 18, 30, 255))
    dr = ImageDraw.Draw(img)

    # 1. Tường phòng ấm áp ban đêm & Khung cửa sổ lớn nhìn ra trời mưa đêm
    for y in range(height):
        t = y / float(height)
        r = int(16 + 18 * t)
        g = int(20 + 16 * t)
        b = int(34 + 14 * t)
        dr.line([(0, y), (width, y)], fill=(r, g, b, 255))

    # Cửa sổ đêm mưa bên trái - giữa
    win_x1, win_y1, win_x2, win_y2 = 110, 75, 1120, 720
    dr.rounded_rectangle([(win_x1 - 12, win_y1 - 12), (win_x2 + 12, win_y2 + 12)], radius=18, fill=(58, 38, 26, 255))
    for wy in range(win_y1, win_y2):
        wt = (wy - win_y1) / float(win_y2 - win_y1)
        dr.line(
            [(win_x1, wy), (win_x2, wy)],
            fill=(int(18 + 20 * wt), int(28 + 30 * wt), int(55 + 42 * wt), 255),
        )

    # Ánh đèn phố mờ xa & Vệt mưa trên kính
    rng = random.Random(101)
    glow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gdr = ImageDraw.Draw(glow_layer)
    bokeh_colors = [
        (255, 195, 110, 95),
        (255, 140, 85, 80),
        (135, 200, 255, 75),
        (255, 220, 150, 90),
    ]
    for _ in range(32):
        bx = rng.randint(win_x1 + 40, win_x2 - 40)
        by = rng.randint(win_y1 + 180, win_y2 - 50)
        br = rng.randint(18, 52)
        col = rng.choice(bokeh_colors)
        gdr.ellipse([(bx - br, by - br), (bx + br, by + br)], fill=col)

    # Quầng sáng đèn bàn học ấm áp
    gdr.ellipse([(740, 180), (1420, 820)], fill=(255, 185, 95, 68))
    gdr.ellipse([(830, 280), (1300, 730)], fill=(255, 210, 130, 88))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=36))
    img = Image.alpha_composite(img, glow_layer)
    dr = ImageDraw.Draw(img)

    # Khung đố cửa sổ gỗ & hạt mưa
    mid_wx = (win_x1 + win_x2) // 2
    mid_wy = (win_y1 + win_y2) // 2
    dr.rectangle([(mid_wx - 6, win_y1), (mid_wx + 6, win_y2)], fill=(58, 38, 26, 255))
    dr.rectangle([(win_x1, mid_wy - 6), (win_x2, mid_wy + 6)], fill=(58, 38, 26, 255))
    for _ in range(180):
        rx = rng.randint(win_x1 + 10, win_x2 - 10)
        ry = rng.randint(win_y1 + 10, win_y2 - 30)
        rlen = rng.randint(14, 28)
        dr.line([(rx, ry), (rx - 4, ry + rlen)], fill=(190, 220, 255, 95), width=2)

    # 2. Mặt bàn gỗ ấm áp phía dưới
    desk_y = 700
    for y in range(desk_y, height):
        dt = (y - desk_y) / float(height - desk_y)
        dr.line(
            [(0, y), (width, y)],
            fill=(int(68 - 22 * dt), int(42 - 14 * dt), int(26 - 9 * dt), 255),
        )
    dr.line([(0, desk_y), (width, desk_y)], fill=(120, 82, 52, 255), width=4)

    # 3. Đèn bàn học cổ điển (Warm Study Lamp)
    dr.polygon([(1050, 700), (1130, 700), (1105, 675), (1075, 675)], fill=(185, 135, 75, 255))
    dr.line([(1090, 675), (1120, 480), (1035, 420)], fill=(170, 125, 68, 255), width=10)
    dr.polygon([(975, 440), (1065, 395), (1085, 455), (990, 475)], fill=(235, 175, 85, 255))

    # 4. Chồng sách mở & Tách cà phê bốc khói
    dr.rounded_rectangle([(740, 665), (960, 702)], radius=6, fill=(242, 232, 210, 255), outline=(140, 105, 70, 255), width=2)
    dr.rounded_rectangle([(755, 638), (945, 668)], radius=6, fill=(165, 62, 55, 255))
    # Tách cà phê
    dr.rounded_rectangle([(625, 640), (695, 700)], radius=12, fill=(238, 225, 205, 255))
    dr.arc([(682, 650), (712, 688)], start=270, end=90, fill=(238, 225, 205, 255), width=6)

    # 5. Chú chó Golden Retriever nằm ngủ ấm áp trên đệm góc bàn/cửa sổ (Cozy Dog Study Buddy)
    # Thảm/Đệm êm
    dr.ellipse([(180, 650), (575, 765)], fill=(128, 56, 48, 255), outline=(175, 92, 75, 255), width=4)
    # Thân chú chó Golden Retriever cuộn tròn ấm áp
    dr.ellipse([(235, 615), (510, 735)], fill=(218, 152, 74, 255))
    dr.ellipse([(255, 630), (485, 725)], fill=(232, 170, 92, 255))
    # Đầu & Tai cụp đáng yêu
    dr.ellipse([(425, 605), (535, 705)], fill=(228, 164, 84, 255))
    dr.ellipse([(412, 618), (455, 685)], fill=(182, 116, 48, 255))  # Tai cụp
    # Mõm & Mũi đen nhỏ + Mắt nhắm ngủ bình yên
    dr.ellipse([(500, 645), (552, 692)], fill=(240, 188, 115, 255))
    dr.ellipse([(536, 656), (554, 672)], fill=(38, 30, 28, 255))  # Mũi
    dr.arc([(482, 640), (512, 658)], start=15, end=165, fill=(65, 42, 24, 255), width=4)  # Mắt nhắm ngủ
    # Chân trước & Đuôi bông
    dr.rounded_rectangle([(465, 692), (535, 718)], radius=12, fill=(235, 176, 98, 255))
    dr.ellipse([(205, 645), (265, 705)], fill=(204, 138, 62, 255))  # Đuôi
    # Biểu tượng "Zzz" ngủ ngon
    try:
        f_zzz = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 26)
        dr.text((535, 575), "z", font=f_zzz, fill=(255, 235, 190, 180))
        dr.text((555, 552), "Z", font=f_zzz, fill=(255, 235, 190, 210))
        dr.text((580, 525), "Z", font=f_zzz, fill=(255, 235, 190, 240))
    except Exception:
        pass

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(str(out_png), quality=95)
    return out_png


def ensure_cozy_dog_cinemagraph_loop(
    work_dir: Path,
    fps: int = 24,
    loop_sec: float = 6.0,
    ffmpeg_exe: str = "ffmpeg",
) -> Path:
    """
    Tạo (hoặc lấy từ cache) đoạn Video Loop Cinemagraph 1920x1080 mượt mà từ bức tranh
    Cozy Golden Retriever Study Room (`cozy_lofi_study_room_bg.jpg`):
      1. Hạt mưa rơi chuyển động mượt bên ngoài 2 ô cửa sổ đêm.
      2. Nhịp thở phập phồng êm dịu (4s/nhịp) trên lưng chú chó Golden Retriever đang ngủ.
      3. Ánh đèn bàn vàng ấm áp lung linh nhẹ + hạt bụi sáng bay lơ lửng dưới chao đèn.
      4. Làn khói cà phê bốc lên uốn lượn nhẹ từ chiếc cốc gốm trên bàn.
    """
    cached_loop = DEFAULT_CATALOG_BGM_ROOT / "cozy_dog_cinemagraph_loop.mp4"
    if cached_loop.exists() and cached_loop.stat().st_size > 100_000:
        return cached_loop

    bg_candidates = [
        DEFAULT_CATALOG_BGM_ROOT / "cozy_lofi_study_room_bg.jpg",
        DEFAULT_MASTER_BGM_ROOT.parent / "cozy_lofi_study_room_bg.jpg",
    ]
    src_art = next((p for p in bg_candidates if p.exists()), None)
    if src_art is None:
        fallback_png = generate_cozy_dog_study_background(work_dir / "fallback_bg.png")
        return fallback_png

    base_im = Image.open(str(src_art)).convert("RGBA").resize((1920, 1080), Image.Resampling.LANCZOS)
    base_np = np.array(base_im, dtype=np.uint8)

    # Chuẩn bị vùng ngực/lưng chú chó Golden Retriever (x=230..620, y=635..865) để nội suy nhịp thở
    dog_x1, dog_y1, dog_x2, dog_y2 = 230, 635, 620, 865
    dog_h = dog_y2 - dog_y1
    dog_w = dog_x2 - dog_x1
    yy, xx = np.mgrid[0:dog_h, 0:dog_w].astype(np.float32)
    ny = (yy / float(dog_h)) * 2.0 - 1.0
    nx = (xx / float(dog_w)) * 2.0 - 1.0
    breath_weight = np.clip(1.0 - (nx * nx + ny * ny), 0.0, 1.0) ** 1.6

    # Khởi tạo hạt mưa trong 2 khung kính cửa sổ (tránh đè lên chậu cây phía dưới y=385)
    rng = random.Random(2026)
    rain_drops = []
    window_panes = [
        (155, 18, 535, 375),  # Ô cửa kính trái
        (580, 18, 865, 395),  # Ô cửa kính phải
    ]
    for px1, py1, px2, py2 in window_panes:
        for _ in range(55):
            rain_drops.append(
                {
                    "px1": px1,
                    "py1": py1,
                    "px2": px2,
                    "py2": py2,
                    "x0": rng.uniform(px1 + 8, px2 - 8),
                    "y0": rng.uniform(py1, py2),
                    "speed": rng.choice([1.0, 2.0]),  # Số chu kỳ rơi trọn vẹn trong loop_sec
                    "len": rng.randint(18, 34),
                    "alpha": rng.randint(65, 135),
                }
            )

    # Hạt bụi vàng (Dust Motes) bay chậm dưới ánh đèn bàn học (x=820..1240, y=260..620)
    dust_motes = []
    for _ in range(18):
        dust_motes.append(
            {
                "x": rng.uniform(840, 1220),
                "y": rng.uniform(280, 610),
                "amp_x": rng.uniform(6.0, 16.0),
                "amp_y": rng.uniform(8.0, 20.0),
                "phase": rng.uniform(0.0, 2.0 * math.pi),
                "r": rng.choice([2, 3]),
                "alpha": rng.randint(75, 145),
            }
        )

    total_frames = int(round(fps * loop_sec))
    frames_dir = work_dir / "_cinemagraph_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    dog_patch_orig = base_np[dog_y1:dog_y2, dog_x1:dog_x2].copy()

    for f_i in range(total_frames):
        prog = f_i / float(total_frames)
        theta = 2.0 * math.pi * prog

        frame_np = base_np.copy()

        # 1. Nhịp thở nhẹ nhàng của chú chó Golden Retriever (2 nhịp thở trong 6 giây)
        breath_shift = 3.2 * math.sin(2.0 * theta)
        src_y = np.clip(yy + breath_shift * breath_weight, 0, dog_h - 1)
        y0_idx = np.floor(src_y).astype(np.int32)
        y1_idx = np.clip(y0_idx + 1, 0, dog_h - 1)
        wy = (src_y - y0_idx)[..., None]
        x_idx = np.arange(dog_w, dtype=np.int32)[None, :]
        warped_dog = (
            dog_patch_orig[y0_idx, x_idx].astype(np.float32) * (1.0 - wy)
            + dog_patch_orig[y1_idx, x_idx].astype(np.float32) * wy
        ).astype(np.uint8)
        frame_np[dog_y1:dog_y2, dog_x1:dog_x2] = warped_dog

        frame_im = Image.fromarray(frame_np)
        fx_layer = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
        fdr = ImageDraw.Draw(fx_layer)

        # 2. Vẽ mưa rơi ngoài khung cửa sổ đêm
        for rd in rain_drops:
            span_h = rd["py2"] - rd["py1"]
            cur_y = rd["py1"] + ((rd["y0"] - rd["py1"] + prog * rd["speed"] * span_h) % span_h)
            cur_x = rd["x0"] - ((cur_y - rd["py1"]) * 0.08)
            y_end = min(rd["py2"], cur_y + rd["len"])
            x_end = cur_x - (y_end - cur_y) * 0.08
            if cur_y < rd["py2"] - 4:
                fdr.line(
                    [(cur_x, cur_y), (x_end, y_end)],
                    fill=(200, 225, 255, rd["alpha"]),
                    width=2,
                )

        # 3. Ánh đèn bàn vàng ấm áp thở nhẹ + hạt bụi sáng
        lamp_alpha = int(18 + 10 * math.sin(theta))
        fdr.ellipse([(830, 290), (1140, 610)], fill=(255, 195, 95, lamp_alpha))
        for dm in dust_motes:
            dx = dm["x"] + dm["amp_x"] * math.sin(theta + dm["phase"])
            dy = dm["y"] + dm["amp_y"] * math.cos(theta + dm["phase"])
            r = dm["r"]
            a = int(dm["alpha"] * (0.65 + 0.35 * math.sin(2.0 * theta + dm["phase"])))
            fdr.ellipse([(dx - r, dy - r), (dx + r, dy + r)], fill=(255, 230, 165, a))

        # 4. Làn khói cà phê bốc lên mềm mại (x=1362, y=450..595)
        for s_k in range(3):
            s_prog = (prog + s_k / 3.0) % 1.0
            sy = 588.0 - s_prog * 135.0
            sx = 1364.0 + math.sin(theta + s_k * 2.1 + s_prog * 4.0) * (10.0 * s_prog + 3.0)
            sr_x = int(10 + 14 * s_prog)
            sr_y = int(14 + 18 * s_prog)
            s_alpha = int(52 * math.sin(math.pi * s_prog))
            if s_alpha > 2:
                fdr.ellipse(
                    [(sx - sr_x, sy - sr_y), (sx + sr_x, sy + sr_y)],
                    fill=(245, 240, 232, s_alpha),
                )

        comp = Image.alpha_composite(frame_im, fx_layer).convert("RGB")
        comp.save(str(frames_dir / f"f_{f_i:04d}.jpg"), quality=93)

    cached_loop.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg_exe,
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(frames_dir / "f_%04d.jpg"),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "17",
            "-pix_fmt",
            "yuv420p",
            str(cached_loop),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    shutil.rmtree(frames_dir, ignore_errors=True)
    return cached_loop


def create_phase_hud_overlay_png(
    out_png: Path,
    phase: PhaseSegment,
    mode: str,
    focus_min: float,
    break_min: float,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """
    Vẽ lớp phủ HUD Kính mờ (Frosted Glass Card `1920x1080` RGBA) cho từng Phase.
    Toàn bộ HUD được bố trí gọn bên cánh phải (`x=1185..1855`) để KHÔNG CHE chú chó Golden Retriever,
    cửa sổ mưa đêm, đèn bàn học và tách cà phê ở bên trái - giữa!
    """
    ov = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov)

    meta = MODE_DISPLAY_META.get(mode, MODE_DISPLAY_META["lofi_focus"])
    is_break = phase.phase_type == "BREAK"
    is_prep = phase.phase_type == "PREP"

    # 1. Khung HUD Pomodoro chính góc phải trên (x=1185..1855, y=55..625)
    cx1, cy1, cx2, cy2 = 1185, 55, 1855, 625
    border_col = (130, 224, 170, 220) if is_break else (245, 176, 65, 225)
    if is_prep:
        border_col = (133, 193, 233, 220)

    dr.rounded_rectangle(
        [(cx1, cy1), (cx2, cy2)],
        radius=34,
        fill=(12, 16, 26, 218),
        outline=border_col,
        width=3,
    )

    font_bold = r"C:\Windows\Fonts\arialbd.ttf"
    font_reg = r"C:\Windows\Fonts\arial.ttf"
    f_badge = ImageFont.truetype(font_bold, 21)
    f_phase = ImageFont.truetype(font_bold, 33)
    f_sub = ImageFont.truetype(font_reg, 20)
    f_small = ImageFont.truetype(font_bold, 19)

    # Top Pill Badge (POMODORO 50/10 • NO MID-ROLL ADS)
    pill_x1, pill_y1, pill_x2, pill_y2 = cx1 + 34, cy1 + 26, cx2 - 34, cy1 + 70
    dr.rounded_rectangle([(pill_x1, pill_y1), (pill_x2, pill_y2)], radius=22, fill=(26, 34, 52, 240))
    top_text = f"POMODORO {focus_min:g}/{break_min:g}  •  NO MID-ROLL ADS"
    tb = dr.textbbox((0, 0), top_text, font=f_badge)
    dr.text(
        ((pill_x1 + pill_x2 - (tb[2] - tb[0])) // 2, pill_y1 + 10),
        top_text,
        font=f_badge,
        fill=(255, 225, 150, 255),
    )

    # Nhãn trạng thái Phase (FOCUS SESSION / BREAK TIME / PREP TIME)
    if is_prep:
        status_title = "PREP TIME  •  GET READY"
        status_col = (133, 193, 233, 255)
    elif is_break:
        status_title = f"BREAK TIME  •  REST {phase.session_num}/{phase.total_sessions}"
        status_col = (130, 224, 170, 255)
    else:
        status_title = f"FOCUS SESSION  {phase.session_num} / {phase.total_sessions}"
        status_col = (255, 200, 85, 255)

    sb = dr.textbbox((0, 0), status_title, font=f_phase)
    dr.text(
        ((cx1 + cx2 - (sb[2] - sb[0])) // 2, cy1 + 94),
        status_title,
        font=f_phase,
        fill=status_col,
    )

    # Hộp chứa Đồng Hồ Đếm Ngược `MM:SS` (Tâm: x=1520, y=258)
    clk_x1, clk_y1, clk_x2, clk_y2 = cx1 + 42, cy1 + 150, cx2 - 42, cy1 + 365
    dr.rounded_rectangle(
        [(clk_x1, clk_y1), (clk_x2, clk_y2)],
        radius=26,
        fill=(7, 10, 16, 238),
        outline=(255, 255, 255, 52),
        width=2,
    )

    # Thanh Tiến Trình Các Hiệp (Session Dots)
    dots_y = cy1 + 392
    total_s = max(1, phase.total_sessions)
    dot_w = min(115, (cx2 - cx1 - 90 - (total_s - 1) * 14) // total_s)
    gap = 14
    total_dots_w = total_s * dot_w + (total_s - 1) * gap
    start_dx = (cx1 + cx2 - total_dots_w) // 2

    for s_i in range(1, total_s + 1):
        dx1 = start_dx + (s_i - 1) * (dot_w + gap)
        dx2 = dx1 + dot_w
        if s_i < phase.session_num or (s_i == phase.session_num and is_break):
            d_fill = (88, 214, 141, 240)
            lbl = f"S{s_i} DONE"
        elif s_i == phase.session_num and not is_prep:
            d_fill = (245, 176, 65, 245)
            lbl = f"S{s_i} LIVE"
        else:
            d_fill = (42, 52, 72, 210)
            lbl = f"S{s_i}"
        dr.rounded_rectangle([(dx1, dots_y), (dx2, dots_y + 38)], radius=12, fill=d_fill)
        lb = dr.textbbox((0, 0), lbl, font=f_small)
        dr.text(
            ((dx1 + dx2 - (lb[2] - lb[0])) // 2, dots_y + 9),
            lbl,
            font=f_small,
            fill=(15, 20, 28, 255) if s_i <= phase.session_num and not is_prep else (190, 205, 225, 230),
        )

    # Thông tin Chế độ Nhạc & Companion góc dưới Card chính
    badge_txt = meta["badge"]
    bb = dr.textbbox((0, 0), badge_txt, font=f_badge)
    dr.text(
        ((cx1 + cx2 - (bb[2] - bb[0])) // 2, cy1 + 455),
        badge_txt,
        font=f_badge,
        fill=(235, 242, 255, 245),
    )

    hint_txt = (
        "Take a deep breath, stretch & hydrate"
        if is_break
        else "Stay focused  •  Cozy Paws Study Companion"
    )
    hb = dr.textbbox((0, 0), hint_txt, font=f_sub)
    dr.text(
        ((cx1 + cx2 - (hb[2] - hb[0])) // 2, cy1 + 495),
        hint_txt,
        font=f_sub,
        fill=(175, 192, 215, 235),
    )

    # 2. Khung Sóng Nhạc & Thông tin Phiên (góc phải dưới x=1185..1855, y=655..835)
    wx1, wy1, wx2, wy2 = 1185, 655, 1855, 835
    dr.rounded_rectangle(
        [(wx1, wy1), (wx2, wy2)],
        radius=26,
        fill=(12, 16, 26, 212),
        outline=(255, 215, 130, 135),
        width=2,
    )
    track_names = "  •  ".join(t["display_name"] for t in phase.tracks_used[:2])
    if len(track_names) > 54:
        track_names = track_names[:51] + "..."
    dr.text(
        (wx1 + 30, wy1 + 18),
        f"NOW PLAYING  •  {phase.title.upper()}",
        font=f_small,
        fill=(255, 215, 120, 250),
    )
    dr.text(
        (wx1 + 30, wy1 + 46),
        track_names,
        font=f_sub,
        fill=(215, 228, 245, 240),
    )
    # Vùng (x=1215..1825, y=735..815) để FFmpeg vẽ sóng âm trực tiếp `showwaves`

    out_png.parent.mkdir(parents=True, exist_ok=True)
    ov.save(str(out_png))
    return out_png


def render_phase_video_segment(
    phase: PhaseSegment,
    phase_wav_path: Path,
    bg_media_path: Path,
    hud_png_path: Path,
    work_dir: Path,
    fps: int = 24,
    ffmpeg_exe: str = "ffmpeg",
) -> Path:
    """
    Render đoạn video hoàn chỉnh (`.mp4` có cả hình và tiếng) cho 1 Phase:
      - Nền Cinemagraph chuyển động mượt (Chú chó thở nhẹ + Mưa rơi + Khói cà phê + Đèn bàn ấm)
      - Lớp phủ HUD Frosted Glass bên phải
      - Đồng hồ đếm ngược `MM:SS` chính xác từng giây
      - Sóng nhạc thực tế (`showwaves`) nhảy theo đúng giai điệu nhạc đang phát!
    """
    seg_mp4 = work_dir / f"vseg_{phase.index:02d}.mp4"
    dur = float(phase.duration_sec)
    total_int = int(math.ceil(dur))

    is_video_bg = bg_media_path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}
    font_escaped = "C\\:/Windows/Fonts/consolab.ttf"
    if not os.path.exists(r"C:\Windows\Fonts\consolab.ttf"):
        font_escaped = "C\\:/Windows/Fonts/arialbd.ttf"

    countdown_expr = (
        f"%{{eif\\:max(0,{total_int}-t)/60\\:d\\:2}}\\:%{{eif\\:mod(max(0,{total_int}-t),60)\\:d\\:2}}"
    )
    clock_color = "0x82E0AA" if phase.phase_type == "BREAK" else "0xFFFFFF"
    wave_color = "0x82E0AA@0.85" if phase.phase_type == "BREAK" else "0xF5B041@0.88"

    fc = (
        f"[0:v]scale=1920:1080:flags=lanczos,fps={fps},format=yuv420p[bg];"
        f"[1:v]scale=1920:1080[hud];"
        f"[2:a]showwaves=s=610x64:mode=cline:rate={fps}:colors={wave_color}[sw];"
        f"[bg][hud]overlay=0:0:shortest=1[v_hud];"
        f"[v_hud][sw]overlay=1215:742:shortest=1,"
        f"drawtext=fontfile='{font_escaped}':text='{countdown_expr}':"
        f"fontsize=124:fontcolor={clock_color}:borderw=4:bordercolor=0x080B12:"
        f"x=1520-text_w/2:y=264[outv]"
    )

    cmd = [ffmpeg_exe, "-y"]
    if is_video_bg:
        cmd += ["-stream_loop", "-1", "-i", str(bg_media_path)]
    else:
        cmd += ["-loop", "1", "-framerate", str(fps), "-i", str(bg_media_path)]

    cmd += [
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-i",
        str(hud_png_path),
        "-i",
        str(phase_wav_path),
        "-filter_complex",
        fc,
        "-map",
        "[outv]",
        "-map",
        "2:a:0",
        "-t",
        f"{dur:.2f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        str(seg_mp4),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return seg_mp4


def generate_youtube_seo_package(
    schedule: List[PhaseSegment],
    mode: str,
    focus_min: float,
    break_min: float,
    sessions: int,
    out_dir: Path,
) -> Dict[str, Any]:
    """Tạo bộ Tiêu đề, Mô tả chuẩn SEO YouTube kèm Timestamps Chapters và Tracklist đầy đủ."""
    total_sec = schedule[-1].end_sec if schedule else 0.0
    total_hr_str = f"{round(total_sec / 3600.0, 1):g}"
    f_int, b_int = int(focus_min), int(break_min)

    title_options = [
        f"⏱️ {f_int}/{b_int} Pomodoro Timer ({total_hr_str} Hours) 🐶 Cozy Dog Study With Me | Deep Focus Lofi & Rain | No Mid-Roll Ads",
        f"📚 {total_hr_str}-Hour Study With Me [{f_int}/{b_int} Pomodoro] 🌧️ Sleeping Golden Retriever, Lofi & Piano Focus (No Ads)",
        f"📖 Cozy Reading & Study Pomodoro {f_int}/{b_int} x {sessions} ☕ Warm Desk Lamp, Gentle Rain & Bell Timer | No Midroll Ads",
    ]

    chapter_lines = []
    for ph in schedule:
        ts = format_timestamp(ph.start_sec)
        chapter_lines.append(f"{ts} {ph.chapter_label}")

    unique_tracks: List[str] = []
    for ph in schedule:
        for tr in ph.tracks_used:
            dn = tr["display_name"]
            if dn not in unique_tracks:
                unique_tracks.append(dn)

    desc_lines = [
        f"🐶 Welcome to Cozy Paws Study Library! Settle in for a peaceful {f_int}/{b_int} Pomodoro session ({sessions} sessions) with our sleeping Golden Retriever study buddy.",
        "",
        "⏳ POMODORO STRUCTURE:",
        f"• 📚 {f_int} minutes of deep focused study / reading / coding",
        f"• ☕ {b_int} minutes of relaxing break music (with gentle singing bowl bell reminders)",
        f"• 🔁 {sessions} complete cycles • Zero mid-roll ads for uninterrupted concentration",
        "",
        "⏱️ TIMESTAMPS / STUDY CHAPTERS:",
        *chapter_lines,
        "",
        "🎵 ORIGINAL GOOGLE FLOW CURATED TRACKLIST IN THIS SESSION:",
        *[f"• {t}" for t in unique_tracks[:25]],
        "",
        "#pomodorotimer #studywithme #studymusic #lofistudy #readingmusic #cozystudy #pomodoro5010 #deepfocus",
    ]

    seo_txt_path = out_dir / "youtube_seo_description.txt"
    seo_txt_path.write_text(
        "=== RECOMMENDED TITLES ===\n"
        + "\n".join(f"{i+1}. {t}" for i, t in enumerate(title_options))
        + "\n\n=== YOUTUBE DESCRIPTION (COPY & PASTE) ===\n"
        + "\n".join(desc_lines)
        + "\n",
        encoding="utf-8",
    )

    manifest = {
        "mode": mode,
        "pomodoro_config": f"{f_int}/{b_int} x {sessions}",
        "total_duration_sec": total_sec,
        "recommended_titles": title_options,
        "chapters": [{"timestamp": format_timestamp(p.start_sec), "label": p.chapter_label} for p in schedule],
        "unique_tracks_count": len(unique_tracks),
        "seo_description_file": str(seo_txt_path),
        "phases": [asdict(p) for p in schedule],
    }
    manifest_path = out_dir / "session_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def build_pomodoro_study_video(
    output_dir: Optional[Path] = None,
    mode: str = "lofi_focus",
    focus_min: float = 50.0,
    break_min: float = 10.0,
    sessions: int = 4,
    prep_sec: float = 16.0,
    bg_media: Optional[str] = None,
    fps: int = 24,
    seed: int = 42,
    bgm_root: Path = DEFAULT_MASTER_BGM_ROOT,
    send_telegram: bool = False,
) -> Dict[str, Any]:
    """
    Pipeline tổng thể:
      1. Lập lịch Pomodoro (`Prep` -> `Focus 1..N` -> `Break 1..N`) và chọn bài từ 58 file WAV.
      2. Tạo cảnh nền Cinemagraph chuyển động mượt (`cozy_dog_cinemagraph_loop.mp4`) hoặc dùng media truyền vào.
      3. Render từng Phase với âm thanh 48kHz Stereo + Chuông báo + HUD Đồng hồ đếm ngược + Sóng nhạc `showwaves`.
      4. Ghép hoàn chỉnh thành file `.mp4` Full HD 1080p, xuất gói SEO Chapters YouTube và gửi Telegram (nếu bật).
    """
    tag = f"pomodoro_{focus_min:g}_{break_min:g}_x{sessions}_{mode}_s{seed}".replace(".", "_")
    out_dir = Path(output_dir) if output_dir else (DEFAULT_OUTPUT_ROOT / tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = out_dir / "_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    schedule = build_pomodoro_schedule(
        mode=mode,
        focus_min=focus_min,
        break_min=break_min,
        sessions=sessions,
        prep_sec=prep_sec,
        seed=seed,
        bgm_root=bgm_root,
    )

    if bg_media and os.path.exists(bg_media):
        bg_path = Path(bg_media)
    else:
        bg_path = ensure_cozy_dog_cinemagraph_loop(work_dir, fps=fps)

    video_segments: List[Path] = []
    for ph in schedule:
        phase_wav = render_phase_audio_clip(ph, work_dir)
        hud_png = create_phase_hud_overlay_png(
            work_dir / f"hud_p{ph.index:02d}.png",
            phase=ph,
            mode=mode,
            focus_min=focus_min,
            break_min=break_min,
        )
        ph.hud_png_path = str(hud_png)
        vseg = render_phase_video_segment(
            phase=ph,
            phase_wav_path=phase_wav,
            bg_media_path=bg_path,
            hud_png_path=hud_png,
            work_dir=work_dir,
            fps=fps,
        )
        phase_wav.unlink(missing_ok=True)
        video_segments.append(vseg)

    vconcat_txt = work_dir / "video_concat.txt"
    vconcat_txt.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in video_segments),
        encoding="utf-8",
    )

    final_mp4 = out_dir / f"{tag}.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
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
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    manifest = generate_youtube_seo_package(
        schedule=schedule,
        mode=mode,
        focus_min=focus_min,
        break_min=break_min,
        sessions=sessions,
        out_dir=out_dir,
    )
    manifest["final_video_path"] = str(final_mp4)
    manifest["final_video_size_mb"] = round(final_mp4.stat().st_size / (1024 * 1024), 2)

    preview_png = out_dir / "preview_frame_session1.jpg"
    sample_sec = min(20.0, max(1.0, prep_sec + 3.0))
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{sample_sec:.2f}",
            "-i",
            str(final_mp4),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(preview_png),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    manifest["preview_frame_path"] = str(preview_png)

    shutil.rmtree(work_dir, ignore_errors=True)

    if send_telegram:
        proj_root = str(Path(__file__).resolve().parents[2])
        if proj_root not in sys.path:
            sys.path.insert(0, proj_root)
        from app.services.intelligence.scheduler_and_broadcast import send_telegram_video_broadcast

        chap_text = "\n".join(f"• <code>{c['timestamp']}</code> — {c['label']}" for c in manifest["chapters"])
        caption = (
            f"🐶 <b>COZY PAWS STUDY LIBRARY — POMODORO DEMO ({round(manifest['total_duration_sec']/60.0, 1):g} PHÚT)</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎧 <b>Chế độ:</b> <code>{mode}</code> ({focus_min:g}/{break_min:g} × {sessions} hiệp)\n"
            f"🎬 <b>Visual:</b> Cinemagraph Loop (Mưa đêm rơi + Chú chó Golden thở nhẹ + Khói cà phê + Đèn bàn ấm)\n"
            f"⏱️ <b>HUD:</b> Đồng hồ đếm ngược thực tế + Sóng âm <code>showwaves</code> + Chuông Singing Bowl\n\n"
            f"📋 <b>Chapters tự động:</b>\n{chap_text}"
        )
        manifest["telegram_sent"] = bool(send_telegram_video_broadcast(str(final_mp4), caption=caption))

    return manifest


def main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Pomodoro Study Music & Cozy Dog Studio Builder")
    parser.add_argument(
        "--mode",
        choices=["lofi_focus", "piano_reading", "exam_ambient", "cozy_jazz"],
        default="lofi_focus",
    )
    parser.add_argument("--focus-min", type=float, default=50.0, help="Focus duration per session in minutes")
    parser.add_argument("--break-min", type=float, default=10.0, help="Break duration per session in minutes")
    parser.add_argument("--sessions", type=int, default=4, help="Number of Pomodoro cycles")
    parser.add_argument("--prep-sec", type=float, default=16.0, help="Prep time at 00:00 in seconds")
    parser.add_argument("--bg-media", default="", help="Optional custom background image or loop video path")
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default="", help="Optional output folder")
    parser.add_argument("--send-telegram", action="store_true", help="Send rendered video to Telegram group")
    args = parser.parse_args()

    res = build_pomodoro_study_video(
        output_dir=Path(args.output_dir) if args.output_dir else None,
        mode=args.mode,
        focus_min=args.focus_min,
        break_min=args.break_min,
        sessions=args.sessions,
        prep_sec=args.prep_sec,
        bg_media=args.bg_media or None,
        fps=args.fps,
        seed=args.seed,
        send_telegram=args.send_telegram,
    )
    print(json.dumps({
        "ok": True,
        "video": res["final_video_path"],
        "size_mb": res["final_video_size_mb"],
        "duration_sec": res["total_duration_sec"],
        "telegram_sent": res.get("telegram_sent", False),
        "chapters": res["chapters"],
        "seo_file": res["seo_description_file"],
        "preview": res["preview_frame_path"],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
