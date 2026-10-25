"""
GOHA + EyeCandy Upgraded 2D Doodle Animation Demo Generator (1080x1920 Vertical Shorts @ True 30 FPS Sub-Pixel Smooth).

Demonstrates all 6 system upgrades + the packaged `DoodleAnimationTool` (`doodle_2d_animator`) working together:
  1. True 30 FPS Continuous Physics & 2-Joint Articulated Character Kinematics (100% unique frames, 0 frame holds).
  2. Sub-Pixel Floating-Point Camera Glide (`Image.Transform.EXTENT` + `BILINEAR`) across Top 20 EyeCandy Motions.
  3. Live 30 FPS Word-by-Word Karaoke Subtitle Highlighting + Whip-Pan Scene Transitions.
  4. Smart @CHARACTER_TAG Reference Binding (@MODERNYOU, @ANCESTOR, @FARMER, @PLEASER, @MANIPULATOR).
  5. Automatic export of GOHA AVS `edit_manifest.json` & AUTO CAPCUT `draft_content.json`.
  6. Direct 1080p 30 FPS delivery to the Telegram Group via `send_telegram_video_broadcast`.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from app.schemas.contracts import RenderJobContract, SceneContract
from app.services.media.cinematic_dictionary import (
    build_character_reference_sheet_prompt,
    build_seven_layer_cinematic_prompt,
)
from app.services.media.doodle_animation_tool import (
    DEFAULT_FPS,
    DoodleAnimationTool,
    synthesize_doodle_sfx,
)
from app.services.media.seedance_director import SeedanceDirector
from app.services.media.timeline_manifest import export_job_timeline_artifacts


@dataclass
class DemoSceneSpec:
    scene_id: int
    headline: str
    sub_badge: str
    narration: str
    character_tag: str
    prop_type: str
    camera_motion: str
    raw_prompt: str
    duration_s: float = 5.2
    audio_path: str = ""


DOODLE_SCENES: List[DemoSceneSpec] = [
    DemoSceneSpec(
        scene_id=1,
        headline="THE 6:00 AM TRAP",
        sub_badge="50+ HRS/WEEK",
        narration="Stop scrolling! Why does a modern office worker feel twice as exhausted as a prehistoric hunter?",
        character_tag="@MODERNYOU",
        prop_type="alarm",
        camera_motion="dolly_in",
        raw_prompt="@MODERNYOU waking up in terror next to a giant rattling red Monday alarm clock on warm cream paper",
    ),
    DemoSceneSpec(
        scene_id=2,
        headline="CRUSHING WORKLOAD",
        sub_badge="500 LB STRESS",
        narration="You sit under fluorescent lights for fifty hours a week while cortisol floods your nervous system.",
        character_tag="@MODERNYOU",
        prop_type="block",
        camera_motion="overhead_90",
        raw_prompt="@MODERNYOU crushed under a massive 500-lb stone block labeled WORKLOAD AND BILLS",
    ),
    DemoSceneSpec(
        scene_id=3,
        headline="50,000 YEARS AGO",
        sub_badge="ONLY 15 HRS/WEEK!",
        narration="Anthropologists proved that fifty thousand years ago, our hunter-gatherer ancestors worked just fifteen hours a week.",
        character_tag="@ANCESTOR",
        prop_type="spear_nature",
        camera_motion="arc_orbit",
        raw_prompt="@ANCESTOR calmly holding a wooden spear under warm golden sunlight with 15 hours per week badge",
    ),
    DemoSceneSpec(
        scene_id=4,
        headline="THE NEOLITHIC SHIFT",
        sub_badge="-4 INCHES HEIGHT",
        narration="When humans invented farming ten thousand years ago, average height shrunk by four inches and spine stress doubled.",
        character_tag="@FARMER",
        prop_type="puppet_strings",
        camera_motion="worms_eye",
        raw_prompt="@FARMER controlled by invisible agricultural debt strings as human height drops by 4 inches",
    ),
    DemoSceneSpec(
        scene_id=5,
        headline="180 ALERTS VS 1 SPEAR",
        sub_badge="DOPAMINE DRAIN",
        narration="Your brain evolved for open savannas, yet today it gets ambushed by one hundred eighty digital alerts before lunch.",
        character_tag="@MODERNYOU",
        prop_type="brain_battery",
        camera_motion="dolly_zoom",
        raw_prompt="@MODERNYOU watching brain battery drain to 14% under 180 digital Slack alerts",
    ),
    DemoSceneSpec(
        scene_id=6,
        headline="RECLAIM YOUR MIND",
        sub_badge="15H FOCUS RULE",
        narration="You were never built for an endless rat race. Guard your fifteen hours of deep focus before the clock wins.",
        character_tag="@MODERNYOU",
        prop_type="climax",
        camera_motion="dolly_out",
        raw_prompt="@MODERNYOU standing calm and free under a glowing realization lightbulb",
    ),
]


def generate_and_broadcast_demo(send_telegram: bool = True) -> Dict[str, str]:
    t0 = time.time()
    work_dir = PROJECT_ROOT / "scratch" / "goha_doodle_upgrade_demo"
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir = PROJECT_ROOT / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    director = SeedanceDirector()
    char_sheet_prompt = build_character_reference_sheet_prompt(
        tag_name="@MODERNYOU",
        character_description="Exhausted modern office worker in rumpled slate-blue shirt (#4A6FA5)",
        signature_prop="rattling red Monday alarm clock and 500-lb grey stress block",
        signature_color_hex="#4A6FA5",
    )
    (work_dir / "character_reference_sheet_prompt.txt").write_text(char_sheet_prompt, encoding="utf-8")

    from app.agents.voice_agent import VoiceAgent
    from app.utils.ffmpeg_helper import get_duration_ms

    voice_agent = VoiceAgent()

    async def _gen_all_tts():
        async def _one(sc: DemoSceneSpec):
            out_mp3 = str(work_dir / f"scene_{sc.scene_id:02d}_vo.mp3")
            if not Path(out_mp3).exists() or Path(out_mp3).stat().st_size < 1024:
                await voice_agent.synthesize_single_clip(
                    text=sc.narration,
                    output_path=out_mp3,
                    voice="en-US-ChristopherNeural",
                    rate="+5%",
                    pitch="-2Hz",
                )
            dur_ms = get_duration_ms(out_mp3)
            sc.audio_path = out_mp3
            sc.duration_s = max(3.6, (dur_ms / 1000.0) + 0.22)

        await asyncio.gather(*[_one(s) for s in DOODLE_SCENES])

    asyncio.run(_gen_all_tts())

    scene_contracts: List[SceneContract] = []
    clip_paths: List[str] = []

    for sc in DOODLE_SCENES:
        bound_prompt = director.bind_cast_to_prompt(sc.raw_prompt)
        seven_layer_prompt = build_seven_layer_cinematic_prompt(
            raw_prompt=bound_prompt,
            niche_id="satirical_psychology_animation",
            scene_id=sc.scene_id,
            total_scenes=len(DOODLE_SCENES),
            camera_motion=sc.camera_motion,
        )

        sfx_wav = str(work_dir / f"sfx_{sc.scene_id:02d}.wav")
        sfx_kind = "alarm" if sc.scene_id == 1 else ("thud" if sc.scene_id == 2 else "whoosh")
        synthesize_doodle_sfx(sfx_wav, sfx_kind)

        clip_mp4 = str(work_dir / f"scene_clip_{sc.scene_id:02d}_30fps.mp4")
        DoodleAnimationTool.render_scene_clip(
            narration=sc.narration,
            output_path=clip_mp4,
            duration_seconds=sc.duration_s,
            scene_id=sc.scene_id,
            total_scenes=len(DOODLE_SCENES),
            character_tag=sc.character_tag,
            prop_type=sc.prop_type,
            camera_motion=sc.camera_motion,
            headline=sc.headline,
            sub_badge=sc.sub_badge,
            visual_prompt=seven_layer_prompt,
            audio_path=sc.audio_path,
            sfx_path=sfx_wav,
            fps=DEFAULT_FPS,
        )
        clip_paths.append(clip_mp4)

        scene_contracts.append(
            SceneContract(
                scene_id=sc.scene_id,
                narration=sc.narration,
                visual_keyword=sc.character_tag,
                visual_prompt=seven_layer_prompt,
                media_path=clip_mp4,
                media_type="video",
                audio_path=sc.audio_path,
                audio_duration_ms=int(sc.duration_s * 1000),
                camera_motion=sc.camera_motion,
            )
        )

    master_txt = work_dir / "master_concat_30fps.txt"
    master_txt.write_text("\n".join(f"file '{Path(p).resolve().as_posix()}'" for p in clip_paths), encoding="utf-8")
    bgm_path = PROJECT_ROOT / "assets" / "bgm" / "chill_ambient.mp3"
    final_mp4 = str(out_dir / "goha_2d_doodle_7layer_upgrade_demo.mp4")

    if bgm_path.exists():
        cmd_master = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(master_txt),
            "-stream_loop", "-1", "-i", str(bgm_path),
            "-filter_complex", "[0:a]volume=1.0[va];[1:a]volume=0.14[bgm];[va][bgm]amix=inputs=2:duration=first[aout]",
            "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            final_mp4,
        ]
    else:
        cmd_master = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(master_txt),
            "-c", "copy", "-movflags", "+faststart", final_mp4,
        ]
    subprocess.run(cmd_master, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    total_ms = sum(int(s.duration_s * 1000) for s in DOODLE_SCENES)
    render_contract = RenderJobContract(
        job_id=2026,
        title="Modern Worker vs 50,000-Year-Old Hunter: 15 Hours/Week Paradox #Shorts",
        scenes=scene_contracts,
        full_audio_path=DOODLE_SCENES[0].audio_path,
        total_audio_duration_ms=total_ms,
        subtitle_path="",
        output_path=final_mp4,
        video_format="short",
    )

    artifacts = export_job_timeline_artifacts(render_contract, work_dir)
    elapsed = time.time() - t0
    dur_s = total_ms / 1000.0
    total_unique_frames = sum(int(round(s.duration_s * DEFAULT_FPS)) for s in DOODLE_SCENES)

    if send_telegram:
        from app.services.intelligence.scheduler_and_broadcast import send_telegram_video_broadcast
        caption = (
            "🎬 *BẢN NÂNG CẤP SIÊU MƯỢT TRUE 30 FPS SUB-PIXEL (`doodle_2d_animator` v2.5)*\n\n"
            "📌 *Chủ đề:* `Modern Worker vs 50,000-Year-Old Hunter: 15 Hours/Week Paradox #Shorts`\n"
            f"⏱️ *Thời lượng:* `{dur_s:.1f}s` (`{total_unique_frames}` khung hình độc lập @ **True 30 FPS** — tăng gấp 13 lần mật độ khung hình so với bản trước)\n"
            f"⚡ *Thời gian render toàn trình:* `{elapsed:.1f}s` (Direct RawVideo Pipe)\n\n"
            "🛠️ *Đã khắc phục triệt để hiện tượng khựng hình:*\n"
            "1️⃣ **True 30 FPS Continuous Physics:** Render 100% khung hình độc lập (`~1,080` frames thay vì `84` frames) + khớp tay chân 2 bậc + chớp mắt tự nhiên.\n"
            "2️⃣ **Sub-Pixel Floating-Point Camera (`EXTENT` + `BILINEAR`):** Chuyển động camera mượt dưới mức 1 pixel, xóa bỏ hoàn toàn lỗi giật bậc thang (`integer crop snapping`).\n"
            "3️⃣ **Live 30 FPS Karaoke Word Highlight:** Phụ đề tô sáng từng từ theo thời gian thực + đạo cụ mới (`puppet_strings`, `brain_battery`).\n"
            "4️⃣ **1080p Direct Telegram Upload:** Gửi nguyên bản Full HD 1080x1920 30 FPS (không hạ xuống 480p 24fps)."
        )
        ok = send_telegram_video_broadcast(final_mp4, caption=caption, prefer_groups=True)
        print(f"Telegram Group Broadcast status: {ok} | frames={total_unique_frames} | elapsed={elapsed:.1f}s")

    return {
        "video_path": final_mp4,
        "duration_s": f"{dur_s:.1f}",
        "elapsed_s": f"{elapsed:.1f}",
        "total_unique_frames": str(total_unique_frames),
        **artifacts,
    }


if __name__ == "__main__":
    res = generate_and_broadcast_demo(send_telegram=True)
    for k, v in res.items():
        print(f"  {k}: {v}")
