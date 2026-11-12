"""
Pomodoro Living Studio Orchestrator & CLI (`app/services/pomodoro/cli.py`)
==========================================================================
Điều phối toàn bộ quy trình sản xuất Video Nhạc Học Tập Dài v2.0 ("Living Studio"):
  1. Lập lịch Pomodoro (`Prep` -> `Focus 1..N` -> `Break 1..N`) từ 58 file WAV Google Flow.
  2. Khởi tạo `MascotPack` (5+ linh vật có cả tranh Ngủ/Học & tranh Thức dậy giờ nghỉ `waking_image`),
     `ThemePack` (6 mùa có tranh riêng `background.jpg`), và `VFXConfig` (7 hiệu ứng VHS/Retro).
  3. Trộn âm thanh 3 lớp (`audio_mixer`: Music + Bell SFX + Ambience `rain`/`fireplace`/`vinyl`/`pure`).
  4. Tạo Video Loop Cinemagraph riêng cho `FOCUS`, `BREAK` (linh vật mở mắt thức dậy), và `Milestone Hat` (hiệp 3+).
  5. Render từng Phase kèm HUD Frosted Glass, tần số đài Lo-Fi FM, Bong Bóng Hội Thoại đa ngôn ngữ (`vi`/`en`/`bilingual`)
     có hiệu ứng Fade-in/Fade-out mượt + Bong bóng cột mốc 50% hiệp.
  6. Xuất gói SEO Chapters + Thumbnail `1280x720` phủ Film Grain + Cắt tự động YouTube Shorts Teaser `9:16` (`--cut-short`)
     + Gửi Telegram Group (`--send-telegram`).
"""

import argparse
import json
import os
import random
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
from app.services.pomodoro.mascot_engine import (
    AnimationController,
    list_available_mascots,
    load_mascot_pack,
)
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
from app.services.pomodoro.shorts_cutter import cut_pomodoro_short_teaser
from app.services.pomodoro.thumbnail_generator import generate_youtube_thumbnail
from app.services.pomodoro.vfx_pipeline import VFXConfig
from app.services.pomodoro.video_renderer import (
    concat_video_segments,
    detect_optimal_h264_encoder,
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
    ambience: str = "auto",
    speech_lang: str = "en",
    enable_speech_bubble: bool = True,
    cut_short_teaser: bool = False,
    fps: int = 24,
    seed: int = 42,
    bgm_root: Path = DEFAULT_MASTER_BGM_ROOT,
    send_telegram: bool = False,
    upload_youtube: bool = False,
    youtube_channel: str = "ch_pomodoro_focus_academy",
    youtube_privacy: str = "private",
    **kwargs: Any,
) -> Dict[str, Any]:
    """Pipeline tổng thể sản xuất Video Nhạc Học Tập Dài v2.0 (Living Studio)."""
    tag = f"pomodoro_{focus_min:g}_{break_min:g}_x{sessions}_{mode}_{mascot}_{theme}_s{seed}".replace(".", "_")
    out_dir = Path(output_dir) if output_dir else (DEFAULT_OUTPUT_ROOT / tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = out_dir / "_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    mascot_pack = load_mascot_pack(mascot)
    theme_pack = load_theme_pack(theme)
    vfx_cfg = VFXConfig.from_cli_string(vfx, glitch_interval_sec=glitch_interval_sec)

    resolved_ambience = theme_pack.default_ambience if ambience == "auto" else ambience

    schedule = build_pomodoro_schedule(
        mode=mode,
        focus_min=focus_min,
        break_min=break_min,
        sessions=sessions,
        prep_sec=prep_sec,
        seed=seed,
        bgm_root=bgm_root,
    )

    has_milestone_sessions = sessions >= 3
    milestone_focus_bg: Optional[Path] = None

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
            milestone_hat=False,
            fps=fps,
        )
        break_bg_path = ensure_mascot_cinemagraph_loop(
            work_dir=work_dir,
            mascot_pack=mascot_pack,
            theme_pack=theme_pack,
            vfx_cfg=vfx_cfg,
            is_break=True,
            milestone_hat=has_milestone_sessions,
            fps=fps,
        )
        if has_milestone_sessions:
            milestone_focus_bg = ensure_mascot_cinemagraph_loop(
                work_dir=work_dir,
                mascot_pack=mascot_pack,
                theme_pack=theme_pack,
                vfx_cfg=vfx_cfg,
                is_break=False,
                milestone_hat=True,
                fps=fps,
            )

    video_segments: List[Path] = []
    for ph in schedule:
        phase_wav = render_phase_audio_clip(ph, work_dir, ambience=resolved_ambience)
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
        milestone_bubble_png: Optional[Path] = None
        if enable_speech_bubble:
            bubble_png = create_speech_bubble_overlay_png(
                work_dir / f"bubble_p{ph.index:02d}.png",
                phase=ph,
                mascot_pack=mascot_pack,
                speech_lang=speech_lang,
                is_milestone=False,
            )
            ph.speech_bubble_png_path = str(bubble_png)

            if ph.phase_type == "FOCUS" and ph.duration_sec >= 150.0:
                milestone_bubble_png = create_speech_bubble_overlay_png(
                    work_dir / f"mbubble_p{ph.index:02d}.png",
                    phase=ph,
                    mascot_pack=mascot_pack,
                    speech_lang=speech_lang,
                    is_milestone=True,
                )

        if ph.phase_type == "BREAK":
            active_bg = break_bg_path
        elif ph.session_num >= 3 and milestone_focus_bg is not None:
            active_bg = milestone_focus_bg
        else:
            active_bg = focus_bg_path

        vseg = render_phase_video_segment(
            phase=ph,
            phase_wav_path=phase_wav,
            bg_media_path=active_bg,
            hud_png_path=hud_png,
            work_dir=work_dir,
            speech_bubble_png_path=bubble_png,
            milestone_bubble_png_path=milestone_bubble_png,
            vfx_cfg=vfx_cfg,
            fps=fps,
        )
        phase_wav.unlink(missing_ok=True)
        video_segments.append(vseg)

    final_mp4 = out_dir / f"{tag}.mp4"
    concat_video_segments(video_segments, final_mp4, work_dir)

    v_enc, _ = detect_optimal_h264_encoder()
    manifest = generate_youtube_seo_package(
        schedule=schedule,
        mode=mode,
        focus_min=focus_min,
        break_min=break_min,
        sessions=sessions,
        out_dir=out_dir,
        mascot_pack=mascot_pack,
        theme_pack=theme_pack,
        speech_lang=speech_lang,
    )
    manifest["mascot_display"] = mascot_pack.display_name
    manifest["theme_display"] = theme_pack.display_name
    manifest["ambience"] = resolved_ambience
    manifest["speech_lang"] = speech_lang
    manifest["video_encoder"] = v_enc
    manifest["final_video_path"] = str(final_mp4)
    manifest["final_video_size_mb"] = round(final_mp4.stat().st_size / (1024 * 1024), 2)
    manifest["vfx_enabled"] = sorted(vfx_cfg.enabled_effects)
    manifest["glitch_interval_sec"] = vfx_cfg.glitch_interval_sec

    anim_events = AnimationController(mascot=mascot_pack, fps=fps).generate_event_schedule(
        total_duration_sec=float(manifest["total_duration_sec"]),
        rng=random.Random(seed),
    )
    manifest["animation_schedule_summary"] = {
        "total_events": len(anim_events),
        "sample_events": anim_events[:12],
    }

    preview_focus_jpg = out_dir / "preview_frame_session1.jpg"
    focus_phases = [p for p in schedule if p.phase_type == "FOCUS"]
    if focus_phases:
        f_p = focus_phases[0]
        # Chụp frame lúc đã qua câu chào đầu hiệp để thấy cảnh học sạch sẽ (hoặc giữa hiệp nếu hiệp rất ngắn)
        offset_f = min(14.0, max(1.0, f_p.duration_sec * 0.6))
        focus_sample_sec = min(f_p.start_sec + offset_f, max(0.5, manifest["total_duration_sec"] - 0.5))
    else:
        focus_sample_sec = 1.5

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
        brk_offset = min(3.0, max(1.2, break_phases[0].duration_sec * 0.35))
        brk_sec = min(break_phases[0].start_sec + brk_offset, manifest["total_duration_sec"] - 0.5)
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
        speech_lang=speech_lang,
    )
    manifest["thumbnail_path"] = str(thumb_jpg)

    if cut_short_teaser:
        short_mp4 = cut_pomodoro_short_teaser(
            long_video_path=final_mp4,
            manifest=manifest,
            fps=30,
        )
        manifest["short_teaser_path"] = str(short_mp4)

    if upload_youtube:
        try:
            from app.agents.publisher_agent import PublisherAgent

            pub = PublisherAgent(channel_id=youtube_channel or "ch_pomodoro_focus_academy")
            yt_res = pub.upload_video(
                video_path=str(final_mp4),
                title=manifest["recommended_titles"][0],
                description=manifest.get("youtube_description", ""),
                tags=manifest.get("youtube_tags", []),
                privacy_status=youtube_privacy,
                pinned_comment=(manifest.get("pinned_comment_ideas") or [""])[0],
                thumbnail_path=str(thumb_jpg),
            )
            manifest["youtube_upload"] = yt_res
        except Exception as e_yt:
            manifest["youtube_upload"] = {"status": "failed", "error": str(e_yt)}

    (out_dir / "session_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    shutil.rmtree(work_dir, ignore_errors=True)

    if send_telegram:
        from app.services.intelligence.scheduler_and_broadcast import send_telegram_video_broadcast

        chap_text = "\n".join(f"• <code>{c['timestamp']}</code> — {c['label']}" for c in manifest["chapters"])
        bubble_sample = (
            "Time for a cozy break — stretch & hydrate!"
            if str(speech_lang).lower().startswith("en")
            else "Cậu chủ đến giờ nghỉ ngơi chút rồi!"
        )
        caption = (
            f"🐾 <b>LIVING STUDIO v2.0 FULL — POMODORO ({round(manifest['total_duration_sec']/60.0, 1):g} PHÚT)</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🐕 <b>Linh vật:</b> <code>{mascot_pack.display_name}</code> (Ngủ/Học + Thức giấc mở mắt khi sang Break)\n"
            f"🎨 <b>Chủ đề mùa:</b> <code>{theme_pack.display_name}</code> • Ambience: <code>{resolved_ambience}</code>\n"
            f"📼 <b>VFX:</b> 7 lớp Retro VHS + Nhiễu băng mỗi {vfx_cfg.glitch_interval_sec:g}s • Encoder: <code>{v_enc}</code>\n"
            f"💬 <b>Tương tác:</b> Bong bóng hội thoại <i>\"{bubble_sample}\"</i> ({speech_lang})\n\n"
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

    parser = argparse.ArgumentParser(description="Pomodoro Living Studio v2.0 — Multi-Mascot & Seasonal Video Builder")
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
        "--ambience",
        choices=["auto", "rain", "fireplace", "vinyl", "pure"],
        default="auto",
        help="Background ambient sound layer (-27dB)",
    )
    parser.add_argument(
        "--speech-lang",
        choices=["vi", "en", "bilingual"],
        default="en",
        help="Language for mascot speech bubbles",
    )
    parser.add_argument(
        "--vfx",
        default="all",
        help="Comma-separated VFX list (glitch,grain,scanline,vignette,chromatic,flare,wobble) or 'all' / 'none'",
    )
    parser.add_argument(
        "--glitch-interval",
        type=float,
        default=5.0,
        help="Interval in seconds between periodic VHS glitch transitions",
    )
    parser.add_argument("--no-speech-bubble", action="store_true", help="Disable mascot speech bubble overlays")
    parser.add_argument("--cut-short", action="store_true", help="Also cut a 60s 9:16 YouTube Shorts teaser")
    parser.add_argument("--focus-min", type=float, default=50.0, help="Focus duration per session in minutes")
    parser.add_argument("--break-min", type=float, default=10.0, help="Break duration per session in minutes")
    parser.add_argument("--sessions", type=int, default=4, help="Number of Pomodoro cycles")
    parser.add_argument("--prep-sec", type=float, default=16.0, help="Prep time at 00:00 in seconds")
    parser.add_argument("--bg-media", default="", help="Optional custom background image or loop video path")
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default="", help="Optional output folder")
    parser.add_argument("--send-telegram", action="store_true", help="Send rendered video to Telegram group")
    parser.add_argument("--upload-youtube", action="store_true", help="Upload video & thumbnail to YouTube channel")
    parser.add_argument("--youtube-channel", default="ch_pomodoro_focus_academy", help="Target YouTube channel ID in channels.yaml")
    parser.add_argument("--youtube-privacy", choices=["private", "unlisted", "public"], default="private")
    args = parser.parse_args()

    res = build_pomodoro_study_video(
        output_dir=Path(args.output_dir) if args.output_dir else None,
        mode=args.mode,
        mascot=args.mascot,
        theme=args.theme,
        ambience=args.ambience,
        speech_lang=args.speech_lang,
        vfx=args.vfx,
        glitch_interval_sec=args.glitch_interval,
        enable_speech_bubble=not args.no_speech_bubble,
        cut_short_teaser=args.cut_short,
        focus_min=args.focus_min,
        break_min=args.break_min,
        sessions=args.sessions,
        prep_sec=args.prep_sec,
        bg_media=args.bg_media or None,
        fps=args.fps,
        seed=args.seed,
        send_telegram=args.send_telegram,
        upload_youtube=args.upload_youtube,
        youtube_channel=args.youtube_channel,
        youtube_privacy=args.youtube_privacy,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "video": res["final_video_path"],
                "short_teaser": res.get("short_teaser_path"),
                "size_mb": res["final_video_size_mb"],
                "duration_sec": res["total_duration_sec"],
                "mascot": res["mascot"],
                "theme": res["theme"],
                "ambience": res["ambience"],
                "encoder": res["video_encoder"],
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
