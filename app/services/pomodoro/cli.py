"""
Pomodoro Living Studio Orchestrator & CLI (`app/services/pomodoro/cli.py`)
==========================================================================
Điều phối toàn bộ quy trình sản xuất Video Nhạc Học Tập Dài v2.0 ("Living Studio"):
  1. Lập lịch Pomodoro (`Prep` -> `Focus 1..N` -> `Break 1..N`) từ 58 file WAV Google Flow.
  2. Khởi tạo `MascotPack` (5+ linh vật: Golden Retriever, Calico Cat, Corgi, Anime Girl, Capybara)
     và `ThemePack` (6 mùa/chủ đề) + `VFXConfig` (Nhiễu băng VHS Glitch định kỳ mỗi vài giây).
  3. Tạo 2 đoạn Video Loop Cinemagraph riêng biệt:
     - `focus_loop.mp4`: Linh vật thở sâu, thi thoảng vẩy tai, vẫy đuôi, nghiêng đầu, vươn vai + `Zzz`.
     - `break_loop.mp4`: Linh vật ngóc đầu tỉnh dậy vui vẻ, vểnh tai, vẫy đuôi liên tục + `♪ ☕ ✨`.
  4. Render từng Phase kèm HUD Frosted Glass, Bong Bóng Hội Thoại (`"Cậu chủ đến giờ nghỉ ngơi chút rồi! ☕"`),
     Đồng hồ đếm ngược `MM:SS`, Sóng nhạc `showwaves` và Chuông Singing Bowl.
  5. Xuất gói SEO Chapters YouTube + Thumbnail `1280x720` + Gửi Telegram Group (nếu bật `--send-telegram`).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.services.pomodoro.audio_mixer import render_phase_audio_clip
from app.services.pomodoro.hud_renderer import (
    create_phase_hud_overlay_png,
    create_speech_bubble_overlay_png,
)
from app.services.pomodoro.mascot_engine import list_available_mascots, load_mascot_pack
from app.services.pomodoro.scheduler import (
    DEFAULT_MASTER_BGM_ROOT,
    DEFAULT_OUTPUT_ROOT,
    build_pomodoro_schedule,
)
from app.services.pomodoro.scene_composer import (
    ensure_mascot_cinemagraph_loop,
    list_available_themes,
    load_theme_pack,
)
from app.services.pomodoro.seo_generator import generate_youtube_seo_package
from app.services.pomodoro.thumbnail_generator import generate_youtube_thumbnail
from app.services.pomodoro.vfx_pipeline import VFXConfig
from app.services.pomodoro.video_renderer import (
    concat_video_segments,
    render_phase_video_segment,
)


def build_pomodoro_study_video(
    output_dir: Optional[Path] = None,
    mode: str = "lofi_focus",
    focus_min: float = 50.0,
    break_min: float = 10.0,
    sessions: int = 4,
    prep_sec: float = 16.0,
    bg_media: Optional[str] = None,
    mascot: str = "golden_retriever",
    theme: str = "autumn_cozy",
    vfx: str = "all",
    glitch_interval_sec: float = 5.0,
    enable_speech_bubble: bool = True,
    fps: int = 24,
    seed: int = 42,
    bgm_root: Path = DEFAULT_MASTER_BGM_ROOT,
    send_telegram: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Pipeline tổng thể sản xuất Video Nhạc Học Tập Dài v2.0 (Living Studio)."""
    tag = f"pomodoro_{focus_min:g}_{break_min:g}_x{sessions}_{mode}_{mascot}_s{seed}".replace(".", "_")
    out_dir = Path(output_dir) if output_dir else (DEFAULT_OUTPUT_ROOT / tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = out_dir / "_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    mascot_pack = load_mascot_pack(mascot)
    theme_pack = load_theme_pack(theme)
    vfx_cfg = VFXConfig.from_cli_string(vfx, glitch_interval_sec=glitch_interval_sec)

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
        focus_bg_path = Path(bg_media)
        break_bg_path = Path(bg_media)
    else:
        focus_bg_path = ensure_mascot_cinemagraph_loop(
            work_dir=work_dir,
            mascot_pack=mascot_pack,
            theme_pack=theme_pack,
            vfx_cfg=vfx_cfg,
            is_break=False,
            fps=fps,
        )
        break_bg_path = ensure_mascot_cinemagraph_loop(
            work_dir=work_dir,
            mascot_pack=mascot_pack,
            theme_pack=theme_pack,
            vfx_cfg=vfx_cfg,
            is_break=True,
            fps=fps,
        )

    video_segments: List[Path] = []
    for ph in schedule:
        phase_wav = render_phase_audio_clip(ph, work_dir)
        hud_png = create_phase_hud_overlay_png(
            work_dir / f"hud_p{ph.index:02d}.png",
            phase=ph,
            mode=mode,
            focus_min=focus_min,
            break_min=break_min,
            mascot_pack=mascot_pack,
            theme_pack=theme_pack,
        )
        ph.hud_png_path = str(hud_png)

        bubble_png: Optional[Path] = None
        if enable_speech_bubble:
            bubble_png = create_speech_bubble_overlay_png(
                work_dir / f"bubble_p{ph.index:02d}.png",
                phase=ph,
                mascot_pack=mascot_pack,
            )
            ph.speech_bubble_png_path = str(bubble_png)

        active_bg = break_bg_path if ph.phase_type == "BREAK" else focus_bg_path
        vseg = render_phase_video_segment(
            phase=ph,
            phase_wav_path=phase_wav,
            bg_media_path=active_bg,
            hud_png_path=hud_png,
            work_dir=work_dir,
            speech_bubble_png_path=bubble_png,
            vfx_cfg=vfx_cfg,
            fps=fps,
        )
        phase_wav.unlink(missing_ok=True)
        video_segments.append(vseg)

    final_mp4 = out_dir / f"{tag}.mp4"
    concat_video_segments(video_segments, final_mp4, work_dir)

    manifest = generate_youtube_seo_package(
        schedule=schedule,
        mode=mode,
        focus_min=focus_min,
        break_min=break_min,
        sessions=sessions,
        out_dir=out_dir,
        mascot_pack=mascot_pack,
        theme_pack=theme_pack,
    )
    manifest["final_video_path"] = str(final_mp4)
    manifest["final_video_size_mb"] = round(final_mp4.stat().st_size / (1024 * 1024), 2)
    manifest["vfx_enabled"] = sorted(vfx_cfg.enabled_effects)
    manifest["glitch_interval_sec"] = vfx_cfg.glitch_interval_sec

    # Trích xuất ảnh Preview hiệp Học (Focus) & hiệp Nghỉ (Break có Speech Bubble)
    preview_focus_jpg = out_dir / "preview_frame_session1.jpg"
    focus_sample_sec = min(max(1.5, prep_sec + 18.0), max(1.0, manifest["total_duration_sec"] - 1.0))
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{focus_sample_sec:.2f}",
            "-i",
            str(final_mp4),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(preview_focus_jpg),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    manifest["preview_frame_path"] = str(preview_focus_jpg)

    break_phases = [p for p in schedule if p.phase_type == "BREAK"]
    if break_phases:
        preview_break_jpg = out_dir / "preview_frame_break1.jpg"
        brk_sec = min(break_phases[0].start_sec + 3.0, manifest["total_duration_sec"] - 0.5)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{brk_sec:.2f}",
                "-i",
                str(final_mp4),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(preview_break_jpg),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        manifest["preview_break_frame_path"] = str(preview_break_jpg)

    thumb_jpg = generate_youtube_thumbnail(
        out_jpg=out_dir / "thumbnail_1280x720.jpg",
        mascot_pack=mascot_pack,
        theme_pack=theme_pack,
        focus_min=focus_min,
        break_min=break_min,
        sessions=sessions,
    )
    manifest["thumbnail_path"] = str(thumb_jpg)

    shutil.rmtree(work_dir, ignore_errors=True)

    if send_telegram:
        proj_root = str(Path(__file__).resolve().parents[3])
        if proj_root not in sys.path:
            sys.path.insert(0, proj_root)
        from app.services.intelligence.scheduler_and_broadcast import send_telegram_video_broadcast

        chap_text = "\n".join(f"• <code>{c['timestamp']}</code> — {c['label']}" for c in manifest["chapters"])
        caption = (
            f"🐾 <b>LIVING STUDIO v2.0 — POMODORO DEMO ({round(manifest['total_duration_sec']/60.0, 1):g} PHÚT)</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🐕 <b>Linh vật:</b> <code>{mascot_pack.display_name}</code> (6 chuyển động: Thở sâu, Vẩy tai, Vẫy đuôi, Nghiêng đầu, Vươn vai, Tỉnh giấc)\n"
            f"🎨 <b>Chủ đề & VFX:</b> <code>{theme_pack.display_name}</code> + Nhiễu VHS Glitch mỗi {vfx_cfg.glitch_interval_sec:g}s\n"
            f"💬 <b>Giờ nghỉ (Break):</b> Bong bóng hội thoại <i>\"Cậu chủ đến giờ nghỉ ngơi chút rồi! ☕\"</i>\n"
            f"🎧 <b>Chế độ nhạc:</b> <code>{mode}</code> ({focus_min:g}/{break_min:g} × {sessions} hiệp)\n\n"
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

    parser = argparse.ArgumentParser(description="Pomodoro Living Studio v2.0 — Multi-Mascot & Retro VFX Video Builder")
    parser.add_argument(
        "--mode",
        choices=["lofi_focus", "piano_reading", "exam_ambient", "cozy_jazz"],
        default="lofi_focus",
    )
    parser.add_argument(
        "--mascot",
        choices=list_available_mascots(),
        default="golden_retriever",
        help="Study companion mascot ID",
    )
    parser.add_argument(
        "--theme",
        choices=list_available_themes(),
        default="autumn_cozy",
        help="Seasonal visual theme ID",
    )
    parser.add_argument(
        "--vfx",
        default="all",
        help="Comma-separated VFX list (glitch,grain,scanline,vignette,chromatic,flare) or 'all' / 'none'",
    )
    parser.add_argument(
        "--glitch-interval",
        type=float,
        default=5.0,
        help="Interval in seconds between periodic VHS glitch transitions",
    )
    parser.add_argument("--no-speech-bubble", action="store_true", help="Disable mascot speech bubble overlays")
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
        mascot=args.mascot,
        theme=args.theme,
        vfx=args.vfx,
        glitch_interval_sec=args.glitch_interval,
        enable_speech_bubble=not args.no_speech_bubble,
        focus_min=args.focus_min,
        break_min=args.break_min,
        sessions=args.sessions,
        prep_sec=args.prep_sec,
        bg_media=args.bg_media or None,
        fps=args.fps,
        seed=args.seed,
        send_telegram=args.send_telegram,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "video": res["final_video_path"],
                "size_mb": res["final_video_size_mb"],
                "duration_sec": res["total_duration_sec"],
                "mascot": res["mascot"],
                "theme": res["theme"],
                "vfx": res["vfx_enabled"],
                "telegram_sent": res.get("telegram_sent", False),
                "chapters": res["chapters"],
                "seo_file": res["seo_description_file"],
                "preview_focus": res["preview_frame_path"],
                "preview_break": res.get("preview_break_frame_path"),
                "thumbnail": res.get("thumbnail_path"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
