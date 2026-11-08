"""
Pomodoro Schedule & Study Library Discovery (`app/services/pomodoro/scheduler.py`)
==================================================================================
Quản lý kho nhạc 58 file WAV Google Flow Music, ưu tiên bản PRIMARY/ALTERNATE
và lập lịch Pomodoro chính xác từng giây (`Prep` -> `Focus 1..N` -> `Break 1..N`).
"""

import json
import random
import re
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MASTER_BGM_ROOT = Path(r"D:\AI_YouTube_Project_Ready\Music\06_study_music_lofi\bgm")
DEFAULT_CATALOG_BGM_ROOT = PROJECT_ROOT / "assets" / "bgm" / "06_study_music_lofi"
DEFAULT_MASCOTS_ROOT = PROJECT_ROOT / "assets" / "mascots"
DEFAULT_THEMES_ROOT = PROJECT_ROOT / "assets" / "themes"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "output" / "pomodoro_studio"

MODE_FOLDER_WEIGHTS: Dict[str, List[str]] = {
    "lofi_focus": ["01_lofi_focus", "04_jazz_cozy", "02_piano_reading"],
    "piano_reading": ["02_piano_reading", "04_jazz_cozy", "01_lofi_focus"],
    "exam_ambient": ["03_exam_ambient", "01_lofi_focus", "02_piano_reading"],
    "cozy_jazz": ["04_jazz_cozy", "02_piano_reading", "01_lofi_focus"],
}

MODE_DISPLAY_META: Dict[str, Dict[str, str]] = {
    "lofi_focus": {
        "badge": "COZY LOFI STUDY & CODING",
        "subtitle": "Warm Lofi Beats • Gentle Rain • Living Companion Studio",
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
    tracks_used: List[Dict[str, Any]] = field(default_factory=list)
    hud_png_path: Optional[str] = None
    speech_bubble_png_path: Optional[str] = None
    speech_text: Optional[str] = None


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
    """Chuyển tên file `STUDY_BGM_01_lofi_warm_rain_T2.wav` thành `#01 Lofi Warm Rain (Ver.2)`."""
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
                chapter_label=f"📚 Study Session {s} ({focus_min:g} min)",
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
                        f"🎉 Final Cooldown & Rest ({break_min:g} min)"
                        if is_last
                        else f"☕ Break Time {s} ({break_min:g} min)"
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
