"""
Pomodoro YouTube SEO & Viewer Engagement Package (`app/services/pomodoro/seo_generator.py`)
==========================================================================================
Tạo tự động bộ Tiêu đề chuẩn SEO YouTube, Mô tả kèm Timestamps Chapters, Tracklist,
và Câu hỏi tương tác (Pinned Comment / Community Poll) để thu hút người xem.
"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.pomodoro.mascot_engine import MascotPack
from app.services.pomodoro.scheduler import PhaseSegment, format_timestamp
from app.services.pomodoro.scene_composer import ThemePack


def generate_youtube_seo_package(
    schedule: List[PhaseSegment],
    mode: str,
    focus_min: float,
    break_min: float,
    sessions: int,
    out_dir: Path,
    mascot_pack: Optional[MascotPack] = None,
    theme_pack: Optional[ThemePack] = None,
) -> Dict[str, Any]:
    """Tạo bộ Tiêu đề, Mô tả chuẩn SEO YouTube kèm Timestamps Chapters và Gợi ý Pinned Comment."""
    total_sec = schedule[-1].end_sec if schedule else 0.0
    total_hr_str = f"{round(total_sec / 3600.0, 1):g}"
    f_str, b_str = f"{focus_min:g}", f"{break_min:g}"

    mascot_name = mascot_pack.display_name if mascot_pack else "Golden Retriever 🐕"
    comp_title = mascot_pack.companion_title if mascot_pack else "Sleeping Golden Retriever Buddy"
    theme_name = theme_pack.display_name if theme_pack else "Autumn Rainy Study 🍂"

    title_options = [
        f"⏱️ {f_str}/{b_str} Pomodoro Timer ({total_hr_str} Hours) {mascot_name} Study With Me | {theme_name} & Lofi | No Ads",
        f"📚 {total_hr_str}-Hour Study With Me [{f_str}/{b_str} Pomodoro] 🌧️ {comp_title}, Deep Focus Lofi & Bell Timer",
        f"📖 Cozy Reading & Coding Pomodoro {f_str}/{b_str} x {sessions} ☕ Interactive {mascot_name} Companion (No Mid-Roll Ads)",
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

    pinned_comment_ideas = [
        f"🐾 {mascot_name}: 'Hôm nay cậu chủ đang ôn thi môn gì hay làm dự án gì thế? Comment mục tiêu bên dưới để mình nằm canh cho hoàn thành 100% nhé! 👇☕'",
        "🗳️ POLL TUẦN NÀY: Video Pomodoro tiếp theo các bạn muốn học cùng ai? (1) Cún Golden 🐕 (2) Mèo Tam Thể 🐱 (3) Corgi Mông Tròn 🐶 (4) Cô Bạn Anime Lofi 🎧 (5) Capybara Đội Quýt 🍊",
    ]

    desc_lines = [
        f"🐾 Welcome to Cozy Living Study Studio! Settle in for a productive {f_str}/{b_str} Pomodoro session ({sessions} cycles) with your {comp_title} ({mascot_name}) in our {theme_name} setting.",
        "",
        "✨ INTERACTIVE LIVING STUDIO FEATURES:",
        f"• {mascot_name} breathes, twitches its ears, wags its tail, and wakes up during Break Time to remind you: 'Cậu chủ đến giờ nghỉ ngơi chút rồi! ☕'",
        f"• 📚 {f_str} minutes of deep focused study / reading / coding",
        f"• ☕ {b_str} minutes of relaxing break music (with gentle singing bowl bell chimes)",
        f"• 🔁 {sessions} complete cycles • Zero mid-roll ads for uninterrupted flow state",
        "",
        "⏱️ TIMESTAMPS / STUDY CHAPTERS:",
        *chapter_lines,
        "",
        "🎵 ORIGINAL GOOGLE FLOW CURATED TRACKLIST IN THIS SESSION:",
        *[f"• {t}" for t in unique_tracks[:25]],
        "",
        "💬 PINNED COMMENT / COMMUNITY ENGAGEMENT:",
        *[f"• {c}" for c in pinned_comment_ideas],
        "",
        "#pomodorotimer #studywithme #studymusic #lofistudy #readingmusic #cozystudy #pomodoro5010 #deepfocus",
    ]

    desc_text = "\n".join(desc_lines)
    seo_txt_path = out_dir / "youtube_seo_description.txt"
    seo_txt_path.write_text(
        "=== RECOMMENDED TITLES ===\n"
        + "\n".join(f"{i+1}. {t}" for i, t in enumerate(title_options))
        + "\n\n=== YOUTUBE DESCRIPTION (COPY & PASTE) ===\n"
        + desc_text
        + "\n",
        encoding="utf-8",
    )

    manifest = {
        "mode": mode,
        "mascot": mascot_pack.id if mascot_pack else "golden_retriever",
        "theme": theme_pack.id if theme_pack else "autumn_cozy",
        "pomodoro_config": f"{f_str}/{b_str} x {sessions}",
        "total_duration_sec": total_sec,
        "recommended_titles": title_options,
        "youtube_description": desc_text,
        "youtube_tags": [
            "pomodoro timer",
            "study with me",
            "study music",
            "lofi study",
            "cozy study",
            f"pomodoro {f_str} {b_str}",
            "deep focus music",
            "no mid roll ads",
        ],
        "pinned_comment_ideas": pinned_comment_ideas,
        "chapters": [{"timestamp": format_timestamp(p.start_sec), "label": p.chapter_label} for p in schedule],
        "unique_tracks_count": len(unique_tracks),
        "seo_description_file": str(seo_txt_path),
        "phases": [asdict(p) for p in schedule],
    }
    manifest_path = out_dir / "session_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest
