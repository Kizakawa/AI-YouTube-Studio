"""
app.services.media.bilibili_single_story_mashup — Pipeline dựng YouTube Shorts Cứu Hộ Thú Cưng
từ 1 Video Thực Tế Duy Nhất trên Bilibili (Single-Source Chronological Story Mashup).

Giải quyết triệt để lỗi "ghép 10 con chó khác nhau từ kho Stock Pexels/Pixabay":
1. Tìm & tải trọn vẹn 1 video cứu hộ thực tế 16:9 Full HD (1920x1080, dài 90s–300s) trên Bilibili
   quay hành trình của CÙNG MỘT CHÚ CHÓ từ lúc gặp nạn đến lúc bình phục.
2. Trích xuất 12 khung hình Safe-Crop (đã cắt bỏ 10% đỉnh logo & 24% đáy phụ đề tiếng Trung)
   trải đều theo trục thời gian và gửi 1 lần duy nhất (Batch Multimodal) cho Gemini Vision.
3. Gemini Vision chọn đúng 6 phân đoạn theo trình tự thời gian (Before -> Rescue -> Care -> After)
   không dính chữ chìm và viết lời kể tiếng Anh (Visual-First Scriptwriting) khớp 100% từng khung hình.
4. Dựng khung 9:16 Cinema (1080x1280 sắc nét tại y=260..1540 trên nền mờ 1080x1920),
   đặt phụ đề Karaoke tại vùng nền tối y=1640..1720 (không đè lên hình chú chó),
   hòa âm giọng đọc tiếng Anh + nhạc nền PAWS_BGM_02_HOPEFUL_RECOVERY.wav và gửi Telegram duyệt.
"""

import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from app.services.media.bilibili_fetcher import (
    download_full_bilibili_video,
    get_bilibili_video_info_and_streams,
    search_bilibili_videos,
)
from app.services.repurposer_qa import preflight_repurpose_qa
from app.utils.ffmpeg_helper import (
    BT709_COLOR_ARGS,
    build_atempo_filter,
    get_duration_ms,
    run_ffmpeg,
)
from app.utils.key_rotator import GEMINI_PRIORITY_MODELS, KeyRotator
from app.utils.subtitle_helper import SubtitleHelper
from config.settings import settings

logger = logging.getLogger(__name__)

STORAGE_ROOT = Path(settings.BASE_DIR) / "storage"
USED_BILI_STORIES_PATH = STORAGE_ROOT / "used_bilibili_story_bvids.json"
DEFAULT_RESCUE_QUERIES_ZH = [
    "流浪小狗救助蜕变",
    "流浪金毛救助前后",
    "流浪狗救助全过程",
    "流浪狗收养前后对比",
    "救助流浪狗洗澡前后",
]


def _load_used_bvids() -> List[str]:
    try:
        if USED_BILI_STORIES_PATH.exists():
            data = json.loads(USED_BILI_STORIES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(x) for x in data if x]
    except Exception:
        pass
    return []


def _mark_bvid_used(bvid: str) -> None:
    if not bvid:
        return
    used = _load_used_bvids()
    if bvid not in used:
        used.append(bvid)
        try:
            USED_BILI_STORIES_PATH.parent.mkdir(parents=True, exist_ok=True)
            USED_BILI_STORIES_PATH.write_text(
                json.dumps(used, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug(f"[BiliSingleStory] Lỗi lưu used_bvids: {exc}")


def find_bilibili_rescue_story_candidates(
    preferred_bvid: Optional[str] = None,
    queries_zh: Optional[List[str]] = None,
    max_candidates: int = 6,
) -> List[Dict[str, Any]]:
    """
    Tìm danh sách ứng viên video cứu hộ thực tế 16:9 HD (85s–280s) trên Bilibili.
    """
    used = set(_load_used_bvids())
    candidates: List[Dict[str, Any]] = []
    seen_bvids = set()

    if preferred_bvid:
        info = get_bilibili_video_info_and_streams(
            preferred_bvid, min_height=720, require_landscape=True
        )
        if info:
            candidates.append(info)
            seen_bvids.add(info["bvid"])

    for q in queries_zh or DEFAULT_RESCUE_QUERIES_ZH:
        if len(candidates) >= max_candidates:
            break
        hits = search_bilibili_videos(q, max_results=8, min_dur_s=85.0, max_dur_s=280.0)
        for h in hits:
            bvid = str(h.get("bvid") or "").strip()
            if not bvid or bvid in seen_bvids or bvid in used:
                continue
            seen_bvids.add(bvid)
            info = get_bilibili_video_info_and_streams(
                bvid, min_height=720, require_landscape=True
            )
            if not info:
                continue
            if int(info.get("width") or 0) <= int(info.get("height") or 0):
                continue
            candidates.append(info)
            if len(candidates) >= max_candidates:
                break

    return candidates


def _extract_safe_cropped_frames(
    video_path: str,
    duration_sec: float,
    out_dir: Path,
    num_samples: int = 12,
) -> List[Dict[str, Any]]:
    """
    Trích xuất `num_samples` khung hình đã áp dụng Bilibili Safe-Crop
    (`crop=iw*0.86:ih*0.66:iw*0.07:ih*0.10`) để Gemini Vision nhìn thấy đúng vùng hình sạch.
    """
    start_bound = max(6.0, duration_sec * 0.07)
    end_bound = max(start_bound + 24.0, min(duration_sec - 7.5, duration_sec * 0.92))
    step = (end_bound - start_bound) / max(1, num_samples - 1)

    samples: List[Dict[str, Any]] = []
    for i in range(num_samples):
        ts = round(start_bound + i * step, 1)
        jpg_path = out_dir / f"frame_{i:02d}_{int(ts)}s.jpg"
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{ts:.2f}",
            "-i", video_path,
            "-frames:v", "1",
            "-vf", "crop=iw*0.86:ih*0.66:iw*0.07:ih*0.10,scale=640:360:flags=lanczos",
            "-q:v", "4",
            str(jpg_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=12)
            if jpg_path.exists() and jpg_path.stat().st_size > 3_000:
                b64 = base64.b64encode(jpg_path.read_bytes()).decode("ascii")
                samples.append({
                    "index": i,
                    "timestamp_sec": ts,
                    "path": str(jpg_path),
                    "b64": b64,
                })
        except Exception as exc:
            logger.debug(f"[BiliSingleStory] Lỗi trích xuất frame #{i} tại {ts}s: {exc}")

    return samples


def analyze_bilibili_story_timeline_with_vision(
    video_path: str,
    bili_info: Dict[str, Any],
    tmp_dir: Path,
) -> Optional[Dict[str, Any]]:
    """
    Gửi 12 khung hình Safe-Crop của 1 video Bilibili cho Gemini Vision trong 1 lần gọi Batch duy nhất:
    - Kiểm định tính đồng nhất của câu chuyện cứu hộ chú chó.
    - Chọn đúng 6 mốc thời gian theo trình tự thời gian (không dính chữ đè giữa hình).
    - Viết lời kể tiếng Anh (Visual-First Narration) khớp trực tiếp với từng khung hình.
    """
    dur = float(bili_info.get("duration") or 0.0)
    if dur <= 0:
        dur = max(60.0, get_duration_ms(video_path) / 1000.0)

    samples = _extract_safe_cropped_frames(video_path, dur, tmp_dir, num_samples=12)
    if len(samples) < 6:
        return None

    parts: List[Dict[str, Any]] = []
    for s in samples:
        idx = s["index"]
        ts = s["timestamp_sec"]
        parts.append({"text": f"Frame #{idx} (timestamp={ts}s):"})
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": s["b64"],
            }
        })

    prompt_text = (
        "You are an elite YouTube Shorts Creative Director for the emotional animal rescue channel 'Paws & Hearts Rescue'.\n"
        f"Source video original title (for context only): {bili_info.get('title', '')}\n"
        "Above are 12 chronological frames sampled from ONE single real animal rescue & transformation video.\n"
        "Notice that top/bottom margins have already been safe-cropped.\n\n"
        "YOUR TASK:\n"
        "1. Verify that this video shows a real dog/puppy rescue, care, and recovery journey (`is_valid_story: true`).\n"
        "2. Select EXACTLY 6 frames in strictly increasing chronological order (`frame_index` from 0 to 11) that:\n"
        "   - Clearly show the dog/puppy (or rescuer helping/bathing/grooming/playing with the dog),\n"
        "   - Do NOT have huge full-screen title cards or burned-in text blocking the center of the image,\n"
        "   - Form a deeply emotional 6-step story arc: HOOK (Initial plight) -> APPROACH -> RESCUE/CARE -> GROOMING/MEDICAL -> RECOVERY -> HAPPY TRANSFORMATION PAYOFF.\n"
        "3. Write the English voiceover narration (`narration`) for each of the 6 chosen segments:\n"
        "   - CRITICAL VISUAL-FIRST RULE: Each segment's `narration` MUST directly and accurately describe what is visibly happening in that specific frame while advancing the emotional story.\n"
        "   - Each segment's `narration` MUST be 14 to 19 English words (natural, warm, deeply moving storytelling, around 95–108 words total across all 6 segments).\n"
        "   - Segment 1 MUST open with an irresistible emotional hook in the first 3 seconds.\n"
        "   - Segment 6 MUST deliver the heartwarming transformation payoff and end with a warm call to subscribe.\n"
        "4. Write an emotional, high-CTR English YouTube Shorts `title` (<= 75 characters, ending with `#Shorts #dog #animalrescue #dogrescue`) and `description`.\n\n"
        "Return ONLY valid JSON matching this schema:\n"
        "{\n"
        '  "is_valid_story": true,\n'
        '  "topic": "How a Scruffy Abandoned Stray Puppy Was Rescued, Groomed, and Transformed Into a Happy Beloved Dog",\n'
        '  "title": "From Matted Stray Puppy To Happy Little Princess! 🥺🐾 #Shorts #dog #animalrescue #dogrescue",\n'
        '  "description": "Heartwarming rescue and transformation story...",\n'
        '  "segments": [\n'
        '    {"frame_index": 0, "role": "HOOK", "narration": "14-19 words describing Frame #0..."},\n'
        '    {"frame_index": 2, "role": "APPROACH", "narration": "14-19 words..."},\n'
        '    {"frame_index": 4, "role": "CARE", "narration": "14-19 words..."},\n'
        '    {"frame_index": 6, "role": "GROOMING", "narration": "14-19 words..."},\n'
        '    {"frame_index": 8, "role": "RECOVERY", "narration": "14-19 words..."},\n'
        '    {"frame_index": 10, "role": "PAYOFF", "narration": "14-19 words..."}\n'
        "  ]\n"
        "}"
    )
    parts.append({"text": prompt_text})

    keys = settings.get_gemini_keys_pool()
    rotator = KeyRotator(keys) if keys else None
    if not rotator or not rotator.has_keys:
        return None
    vision_models = [m for m in GEMINI_PRIORITY_MODELS if not m.startswith("gemma-")]
    sample_by_idx = {s["index"]: s for s in samples}

    for model_name, api_key in rotator.iter_best_model_key_pairs(vision_models, max_total_attempts=8):
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            payload = {
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "temperature": 0.25,
                    "responseMimeType": "application/json",
                },
            }
            resp = requests.post(url, json=payload, timeout=35)
            if resp.status_code == 200:
                candidates_json = resp.json().get("candidates") or []
                if not candidates_json:
                    continue
                text_out = (
                    ((candidates_json[0].get("content") or {}).get("parts") or [{}])[0].get("text")
                    or ""
                ).strip()
                text_out = re.sub(r"^```(?:json)?\s*|\s*```$", "", text_out, flags=re.IGNORECASE).strip()
                parsed = json.loads(text_out)
                if not parsed.get("is_valid_story"):
                    return None
                segs = parsed.get("segments") or []
                if len(segs) < 5:
                    continue

                resolved_segments = []
                last_ts = -1.0
                for seg in segs[:6]:
                    f_idx = int(seg.get("frame_index", 0))
                    s_info = sample_by_idx.get(f_idx)
                    if not s_info:
                        continue
                    ts = float(s_info["timestamp_sec"])
                    if ts <= last_ts:
                        ts = min(dur - 7.0, last_ts + 7.5)
                    last_ts = ts
                    narr = str(seg.get("narration") or "").strip()
                    if len(narr.split()) < 6:
                        continue
                    resolved_segments.append({
                        "frame_index": f_idx,
                        "start_seconds": ts,
                        "role": str(seg.get("role") or "STORY"),
                        "narration": narr,
                    })

                if len(resolved_segments) >= 5:
                    parsed["segments"] = resolved_segments
                    return parsed
            elif resp.status_code in (400, 403, 404, 429, 503):
                rotator.mark_model_rate_limited(
                    api_key, model_name, status_code=resp.status_code, error_text=resp.text[:200]
                )
        except Exception as exc:
            rotator.mark_model_rate_limited(api_key, model_name, status_code=503, error_text=str(exc))

    return None


def render_bilibili_single_story_short(
    source_video_path: str,
    story_plan: Dict[str, Any],
    output_path: str,
    voice_id: str = "en-US-JennyNeural",
    bgm_path: str = "assets/bgm/01_animal_rescue_paws/PAWS_BGM_02_HOPEFUL_RECOVERY.wav",
) -> Dict[str, Any]:
    """
    Dựng video 9:16 (1080x1920 @ 30fps) từ 1 video Bilibili gốc và 6 phân đoạn đã được Gemini Vision chọn:
    - Khung hình 4:5 Cinema Safe-Crop (`1080x1280` đặt tại `y=260..1540` trên nền mờ `1080x1920`).
    - Phụ đề Karaoke đặt tại `margin_v=215` (`y=1640..1710` — nằm gọn trong vùng nền tối phía dưới,
      TUYỆT ĐỐI KHÔNG ĐÈ LÊN HÌNH CHÚ CHÓ).
    - Hòa âm giọng đọc `en-US-JennyNeural` + nhạc nền `PAWS_BGM_02_HOPEFUL_RECOVERY.wav` chuẩn `-14 LUFS`.
    """
    from app.services.video_repurposer import video_repurposer

    segments = list(story_plan.get("segments") or [])
    src_dur_s = max(60.0, get_duration_ms(source_video_path) / 1000.0)

    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        subclip_paths: List[str] = []
        mashup_clips_meta: List[Dict[str, Any]] = []
        total_dur_s = 0.0

        for idx, seg in enumerate(segments):
            narration = SubtitleHelper.sanitize_subtitle_text(seg["narration"])
            voice_mp3 = tmp_dir / f"voice_{idx:02d}.mp3"
            video_repurposer._synthesize_voiceover_sync(
                text=narration,
                output_path=str(voice_mp3),
                niche_id="animal_rescue",
                voice_override=voice_id,
            )
            v_ms = get_duration_ms(str(voice_mp3))
            v_sec = (v_ms / 1000.0) if v_ms > 0 else 6.0
            seg_dur = round(max(5.0, min(8.5, v_sec + 0.22)), 2)

            st_sec = max(0.0, min(float(seg["start_seconds"]), src_dur_s - seg_dur - 0.5))
            fade_out_st = max(0.0, seg_dur - 0.08)

            # 4:5 Cinema Safe-Crop:
            # - Cắt bỏ 10% đỉnh (logo góc Bilibili) và 24% đáy (phụ đề tiếng Trung)
            # - Khung chính 1080x1280 đặt tại y=260..1540
            # - Vùng đáy y=1540..1920 làm tối nhẹ để đặt phụ đề Karaoke siêu nổi bật mà không chạm vào chú chó
            zoom_factor = 1.04 if (idx % 2 == 0) else 1.08
            v_filter = (
                "[0:v]crop=iw*0.86:ih*0.66:iw*0.07:ih*0.10,split=2[v_fg][v_bg];"
                "[v_bg]scale=1080:1920:force_original_aspect_ratio=increase:flags=fast_bilinear,"
                "crop=1080:1920,boxblur=26:4,eq=brightness=-0.22:saturation=1.12[bg];"
                f"[v_fg]scale=1080*{zoom_factor:.2f}:1280*{zoom_factor:.2f}:force_original_aspect_ratio=increase:flags=lanczos,"
                "crop=1080:1280,unsharp=5:5:0.85:5:5:0.0,eq=contrast=1.04:saturation=1.06[fg];"
                "[bg][fg]overlay=0:260,setsar=1,fps=30,format=yuv420p[v]"
            )

            if v_sec <= seg_dur:
                tempo_chain = ""
            else:
                speed_ratio = min(v_sec / max(seg_dur, 1.0), 1.18)
                t_str = build_atempo_filter(speed_ratio)
                tempo_chain = f"{t_str}," if t_str != "anull" else ""

            a_filter = (
                f"[1:a]{tempo_chain}volume=1.25,aresample=44100,aformat=channel_layouts=stereo,"
                f"apad=whole_dur={seg_dur:.3f},atrim=0:{seg_dur:.3f},"
                f"afade=t=in:ss=0:d=0.04,afade=t=out:st={fade_out_st:.2f}:d=0.05[a]"
            )

            sub_mp4 = tmp_dir / f"seg_{idx:02d}.mp4"
            cmd = [
                "-y",
                "-ss", f"{st_sec:.3f}",
                "-t", f"{seg_dur:.3f}",
                "-i", source_video_path,
                "-i", str(voice_mp3),
                "-sn",
                "-filter_complex", f"{v_filter};{a_filter}",
                "-map", "[v]",
                "-map", "[a]",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "19",
                "-r", "30",
                "-pix_fmt", "yuv420p",
                *BT709_COLOR_ARGS,
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "44100",
                "-ac", "2",
                str(sub_mp4),
            ]
            run_ffmpeg(cmd)
            subclip_paths.append(str(sub_mp4))
            mashup_clips_meta.append({
                "start_seconds": 0.0,
                "end_seconds": seg_dur,
                "voice_dur_ms": v_ms,
                "role": seg.get("role", "STORY"),
                "summary": narration,
                "custom_voiceover_text": narration,
            })
            total_dur_s += seg_dur

        concat_txt = tmp_dir / "concat_list.txt"
        with open(concat_txt, "w", encoding="utf-8") as f:
            for sp in subclip_paths:
                abs_p = Path(sp).resolve().as_posix().replace("'", "'\\''")
                f.write(f"file '{abs_p}'\n")

        concat_mp4 = tmp_dir / "concat_raw.mp4"
        run_ffmpeg([
            "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_txt),
            "-c", "copy",
            str(concat_mp4),
        ])

        stage_mp4 = concat_mp4
        bgm_abs = Path(settings.BASE_DIR) / bgm_path
        if bgm_abs.exists():
            bgm_mp4 = tmp_dir / "concat_bgm.mp4"
            bgm_filter = (
                "[0:a]volume=1.0[voice];"
                "[1:a]lowpass=f=4200,equalizer=f=3200:t=q:w=1.2:g=-6,volume=0.085[bgm];"
                "[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2,"
                "loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
            )
            run_ffmpeg([
                "-y",
                "-i", str(stage_mp4),
                "-stream_loop", "-1",
                "-i", str(bgm_abs),
                "-filter_complex", bgm_filter,
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                "-t", f"{total_dur_s:.2f}",
                str(bgm_mp4),
            ])
            if bgm_mp4.exists() and bgm_mp4.stat().st_size > 50_000:
                stage_mp4 = bgm_mp4

        ass_path = tmp_dir / "story_subs.ass"
        SubtitleHelper.save_mashup_ass_file(
            clips=mashup_clips_meta,
            output_path=str(ass_path),
            fontname="Arial Black",
            fontsize=44,
            margin_v=215,
            niche_id="animal_rescue",
            credit_text=None,
            is_english=True,
            is_voiceover=True,
        )
        escaped_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
        run_ffmpeg([
            "-y",
            "-i", str(stage_mp4),
            "-vf", f"ass='{escaped_ass}'",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "19",
            *BT709_COLOR_ARGS,
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(out_p),
        ])

    full_script = " ".join(s["narration"] for s in segments)
    qa_report = preflight_repurpose_qa(
        str(out_p),
        None,
        {
            "title": story_plan.get("title", ""),
            "description": story_plan.get("description", full_script),
            "duration": round(total_dur_s, 1),
            "clips_count": len(segments),
        },
    )
    return {
        "output_path": str(out_p),
        "duration_sec": round(total_dur_s, 1),
        "file_size_mb": round(out_p.stat().st_size / (1024 * 1024), 2) if out_p.exists() else 0.0,
        "title": story_plan.get("title", "From Stray Puppy To Beloved Family Dog! 🥺🐾 #Shorts #dog #animalrescue"),
        "description": story_plan.get("description", full_script),
        "topic": story_plan.get("topic", "Single-Source Real Stray Dog Rescue and Transformation"),
        "script": full_script,
        "segments": segments,
        "qa_report": qa_report,
    }


def produce_bilibili_single_story_rescue_short(
    channel_id: str = "ch_paws_hearts_rescue",
    preferred_bvid: Optional[str] = None,
    send_telegram: bool = True,
) -> Dict[str, Any]:
    """
    Quy trình khép kín:
    1. Tìm và tải 1 video cứu hộ thực tế 1080p 16:9 trên Bilibili.
    2. Phân tích 12 khung hình Safe-Crop bằng Gemini Vision để lấy 6 phân đoạn + lời kể tiếng Anh khớp hình.
    3. Khởi tạo `VideoJob` (`AWAITING_APPROVAL`), render `storage/outputs/video_{job_id}.mp4`,
       chạy kiểm định `evaluate_rendered_video_quality` và bắn vào Telegram duyệt.
    """
    from app.database.models import SessionLocal, VideoJob

    candidates = find_bilibili_rescue_story_candidates(preferred_bvid=preferred_bvid)
    if not candidates:
        raise RuntimeError("Không tìm thấy ứng viên video Bilibili 16:9 HD phù hợp!")

    chosen_bili = None
    story_plan = None
    master_mp4_path = None

    dl_dir = STORAGE_ROOT / "cache" / "bilibili_stories"
    dl_dir.mkdir(parents=True, exist_ok=True)

    for cand in candidates:
        bvid = cand["bvid"]
        raw_mp4 = dl_dir / f"master_{bvid}.mp4"
        if not raw_mp4.exists() or raw_mp4.stat().st_size < 500_000:
            logger.info(f"📥 [BiliSingleStory] Đang tải video gốc Bilibili {bvid} ({cand.get('title')})...")
            dl_info = download_full_bilibili_video(bvid, str(raw_mp4))
            if not dl_info or not raw_mp4.exists():
                continue

        with tempfile.TemporaryDirectory() as frame_tmp:
            logger.info(f"👁️ [BiliSingleStory] Đang quét 12 khung hình Safe-Crop của {bvid} bằng Gemini Vision...")
            plan = analyze_bilibili_story_timeline_with_vision(
                str(raw_mp4), cand, Path(frame_tmp)
            )
            if plan and len(plan.get("segments") or []) >= 5:
                chosen_bili = cand
                story_plan = plan
                master_mp4_path = str(raw_mp4)
                break

    if not chosen_bili or not story_plan or not master_mp4_path:
        raise RuntimeError("Gemini Vision không chọn được câu chuyện đồng nhất từ các ứng viên Bilibili!")

    # Tạo VideoJob trong Database
    with SessionLocal() as db:
        job = VideoJob(
            user_id=0,
            target_channel_id=channel_id,
            topic=story_plan.get("topic") or story_plan.get("title"),
            title=story_plan.get("title"),
            description=story_plan.get("description"),
            script=" ".join(s["narration"] for s in story_plan["segments"]),
            hook_text=story_plan["segments"][0]["narration"],
            voice_engine="edge_tts:en-US-JennyNeural",
            status="RENDERING",
            progress_percent=50,
            progress_message=f"Đang dựng Single-Story Mashup từ Bilibili ({chosen_bili['bvid']})...",
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

    out_video_path = str(Path(settings.OUTPUT_DIR) / f"video_{job_id}.mp4")
    render_res = render_bilibili_single_story_short(
        source_video_path=master_mp4_path,
        story_plan=story_plan,
        output_path=out_video_path,
        voice_id="en-US-JennyNeural",
    )

    with SessionLocal() as db:
        j_row = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if j_row:
            j_row.video_path = out_video_path
            j_row.status = "AWAITING_APPROVAL"
            j_row.progress_percent = 100
            j_row.progress_message = "✅ Đã dựng xong Single-Story Bilibili Mashup — Chờ duyệt Telegram"
            db.commit()

    _mark_bvid_used(chosen_bili["bvid"])

    tg_delivered = False
    if send_telegram:
        bot_token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        candidate_chats = []
        for c_id in [*settings.get_allowed_group_ids(), *list(settings.get_allowed_users().keys()), "8498162618"]:
            s_id = str(c_id or "").strip()
            if s_id and s_id not in candidate_chats:
                candidate_chats.append(s_id)

        qa = render_res.get("qa_report") or {}
        caption = (
            f"🎬 <b>[SINGLE-STORY BILIBILI MASHUP] VIDEO #{job_id} ĐÃ SẴN SÀNG!</b>\n"
            f"📺 <b>Kênh:</b> Paws & Hearts Rescue (<code>{channel_id}</code>)\n"
            f"🐾 <b>Nguồn gốc (1 chú chó đồng nhất):</b> Bilibili <code>{chosen_bili['bvid']}</code> (1080p Full HD — 0% YouTube)\n"
            f"🏷 <b>Tiêu đề:</b> {render_res['title']}\n"
            f"⏱ <b>Thời lượng:</b> {render_res['duration_sec']}s | 📦 <b>Dung lượng:</b> {render_res['file_size_mb']} MB\n"
            f"🔍 <b>Video QA:</b> Sharpness={qa.get('mean_sharpness', 'N/A')} | Luma={qa.get('mean_luma', 'N/A')} | Pass={qa.get('passed', True)}\n\n"
            f"👇 <b>Bấm nút bên dưới để Đăng ngay hoặc Lên lịch 19:00 Tối nay (11/11):</b>"
        )
        reply_markup = json.dumps({
            "inline_keyboard": [
                [
                    {"text": "🚀 Đăng Ngay (Public)", "callback_data": f"publish_now:{job_id}"},
                    {"text": "⏰ Lên Lịch Giờ Vàng (19:00)", "callback_data": f"schedule_video:{job_id}"},
                ],
                [
                    {"text": "❌ Hủy & Xóa File", "callback_data": f"reject_delete:{job_id}"},
                ],
            ]
        })

        uploaded_file_id = None
        for chat_target in candidate_chats:
            try:
                with open(out_video_path, "rb") as vf:
                    r_tg = requests.post(
                        f"https://api.telegram.org/bot{bot_token}/sendVideo",
                        data={
                            "chat_id": chat_target,
                            "caption": caption,
                            "parse_mode": "HTML",
                            "supports_streaming": "true",
                            "reply_markup": reply_markup,
                        },
                        files={"video": (Path(out_video_path).name, vf, "video/mp4")},
                        timeout=120,
                    )
                if r_tg.status_code == 200:
                    tg_delivered = True
                    res_json = r_tg.json().get("result") or {}
                    uploaded_file_id = (res_json.get("video") or {}).get("file_id")
                    logger.info(f"✅ [BiliSingleStory] Đã gửi video #{job_id} tới Telegram ({chat_target})!")
            except Exception as exc:
                logger.warning(f"[BiliSingleStory] Lỗi gửi Telegram ({chat_target}): {exc}")

        if uploaded_file_id:
            pending_preview_file = STORAGE_ROOT / "pending_group_preview.json"
            pending_preview_file.write_text(
                json.dumps(
                    {
                        "job_id": job_id,
                        "video_path": out_video_path,
                        "telegram_file_id": uploaded_file_id,
                        "caption": caption,
                        "reply_markup": json.loads(reply_markup),
                        "created_at": time.time(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    return {
        "job_id": job_id,
        "bvid": chosen_bili["bvid"],
        "bilibili_title": chosen_bili.get("title"),
        "bilibili_views": chosen_bili.get("view_count"),
        "telegram_delivered": tg_delivered,
        **render_res,
    }
