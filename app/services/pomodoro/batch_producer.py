"""
Pomodoro Batch Producer & 7-Day Challenge Runner (`app/services/pomodoro/batch_producer.py`)
============================================================================================
Sản xuất hàng loạt Video Nhạc Học Tập (kèm cắt tự động YouTube Shorts Teaser 9:16 và Thumbnail)
theo các gói chiến dịch (Phase 4 & Module 6.1):
  - `3combo_verify`: Kiểm thử nhanh 3 tổ hợp `(Mascot × Theme × Mode)` hoàn toàn khác nhau.
  - `7day_challenge`: Chuỗi 7 ngày "Study With Me Challenge" phủ đều 5 linh vật và 6 chủ đề mùa.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.services.pomodoro.scheduler import DEFAULT_OUTPUT_ROOT


BATCH_PRESETS: Dict[str, List[Dict[str, Any]]] = {
    "3combo_verify": [
        {
            "slug": "combo1_golden_winter",
            "mascot": "golden_retriever",
            "theme": "winter_cabin",
            "mode": "lofi_focus",
            "ambience": "fireplace",
            "focus_min": 0.5,
            "break_min": 0.35,
            "sessions": 1,
            "prep_sec": 6.0,
            "seed": 101,
        },
        {
            "slug": "combo2_anime_autumn",
            "mascot": "anime_lofi_girl",
            "theme": "autumn_cozy",
            "mode": "piano_reading",
            "ambience": "rain",
            "focus_min": 0.5,
            "break_min": 0.35,
            "sessions": 1,
            "prep_sec": 6.0,
            "seed": 202,
        },
        {
            "slug": "combo3_calico_sakura",
            "mascot": "calico_cat",
            "theme": "spring_sakura",
            "mode": "cozy_jazz",
            "ambience": "vinyl",
            "focus_min": 0.5,
            "break_min": 0.35,
            "sessions": 1,
            "prep_sec": 6.0,
            "seed": 303,
        },
    ],
    "7day_challenge": [
        {"slug": "day1_golden_autumn_50_10", "mascot": "golden_retriever", "theme": "autumn_cozy", "mode": "lofi_focus", "ambience": "rain", "focus_min": 50.0, "break_min": 10.0, "sessions": 2, "seed": 701},
        {"slug": "day2_anime_winter_25_5", "mascot": "anime_lofi_girl", "theme": "winter_cabin", "mode": "piano_reading", "ambience": "fireplace", "focus_min": 25.0, "break_min": 5.0, "sessions": 4, "seed": 702},
        {"slug": "day3_calico_sakura_40_10", "mascot": "calico_cat", "theme": "spring_sakura", "mode": "cozy_jazz", "ambience": "vinyl", "focus_min": 40.0, "break_min": 10.0, "sessions": 2, "seed": 703},
        {"slug": "day4_corgi_summer_30_5", "mascot": "corgi_puppy", "theme": "summer_breeze", "mode": "lofi_focus", "ambience": "vinyl", "focus_min": 30.0, "break_min": 5.0, "sessions": 4, "seed": 704},
        {"slug": "day5_capybara_exam_50_10", "mascot": "capybara", "theme": "autumn_cozy", "mode": "exam_ambient", "ambience": "rain", "focus_min": 50.0, "break_min": 10.0, "sessions": 2, "seed": 705},
        {"slug": "day6_golden_christmas_50_10", "mascot": "golden_retriever", "theme": "christmas_lofi", "mode": "cozy_jazz", "ambience": "fireplace", "focus_min": 50.0, "break_min": 10.0, "sessions": 3, "seed": 706},
        {"slug": "day7_golden_halloween_25_5", "mascot": "golden_retriever", "theme": "halloween_study", "mode": "exam_ambient", "ambience": "rain", "focus_min": 25.0, "break_min": 5.0, "sessions": 4, "seed": 707},
    ],
}


def run_pomodoro_batch(
    preset: str = "3combo_verify",
    batch_root: Optional[Path] = None,
    cut_shorts: bool = True,
    speech_lang: str = "en",
) -> Dict[str, Any]:
    """Chạy kết xuất tuần tự một lô video theo `preset` và xuất báo cáo tổng hợp `batch_report.json`."""
    from app.services.pomodoro.cli import build_pomodoro_study_video

    jobs = BATCH_PRESETS.get(preset)
    if not jobs:
        raise ValueError(f"Unknown batch preset: {preset}. Choices: {list(BATCH_PRESETS.keys())}")

    root_dir = Path(batch_root) if batch_root else (DEFAULT_OUTPUT_ROOT / f"batch_{preset}")
    root_dir.mkdir(parents=True, exist_ok=True)

    results: List[Dict[str, Any]] = []
    for idx, job in enumerate(jobs, 1):
        job_dir = root_dir / job["slug"]
        manifest = build_pomodoro_study_video(
            output_dir=job_dir,
            mode=job["mode"],
            mascot=job["mascot"],
            theme=job["theme"],
            ambience=job.get("ambience", "auto"),
            speech_lang=speech_lang,
            focus_min=float(job["focus_min"]),
            break_min=float(job["break_min"]),
            sessions=int(job["sessions"]),
            prep_sec=float(job.get("prep_sec", 16.0)),
            seed=int(job.get("seed", 42 + idx)),
            cut_short_teaser=cut_shorts,
        )
        results.append(
            {
                "index": idx,
                "slug": job["slug"],
                "mascot": manifest["mascot"],
                "theme": manifest["theme"],
                "mode": manifest["mode"],
                "ambience": manifest.get("ambience"),
                "video": manifest["final_video_path"],
                "short_teaser": manifest.get("short_teaser_path"),
                "thumbnail": manifest.get("thumbnail_path"),
                "preview_focus": manifest.get("preview_frame_path"),
                "preview_break": manifest.get("preview_break_frame_path"),
                "duration_sec": manifest["total_duration_sec"],
            }
        )

    summary = {
        "preset": preset,
        "total_jobs": len(results),
        "batch_root": str(root_dir),
        "jobs": results,
    }
    (root_dir / "batch_report.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return summary


def main() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Pomodoro Batch Producer & 7-Day Challenge Runner")
    parser.add_argument("--preset", choices=list(BATCH_PRESETS.keys()), default="3combo_verify")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--no-shorts", action="store_true")
    parser.add_argument("--speech-lang", choices=["vi", "en", "bilingual"], default="en")
    args = parser.parse_args()

    summary = run_pomodoro_batch(
        preset=args.preset,
        batch_root=Path(args.output_dir) if args.output_dir else None,
        cut_shorts=not args.no_shorts,
        speech_lang=args.speech_lang,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
