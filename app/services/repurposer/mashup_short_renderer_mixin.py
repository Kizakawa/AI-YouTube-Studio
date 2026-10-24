"""app.services.repurposer.mashup_short_renderer_mixin — Render video Mashup Short ghép đa phân đoạn cao trào."""
"""
VideoRepurposerService — Tái chế video dài thành Shorts viral chuẩn bản quyền & tối ưu thuật toán.

Đặc tính chính:
1. An toàn bản quyền (Copyright & Fair Use):
   - Chế độ "Kênh nhà" (Self-Owned Content): Tái chế video dài của chính kênh thành 5-10 Shorts.
   - Chế độ "Bình luận Chuyển hóa" (Transformative Fair-Use Commentary): Bổ sung góc nhìn phân tích,
     trích dẫn nguồn minh bạch (Credit watermark), trích dẫn clip ngắn kèm luận điểm mới.
2. Phát hiện phân đoạn Viral (AI Highlight Detection):
   - Sử dụng Gemini AI phân tích transcript/ngữ cảnh để tìm 3-5 khoảnh khắc đắt giá nhất (30-55s).
   - Chấm điểm Viral Score (1-100), Hook Score, gợi ý tiêu đề Shorts giật tít và lý do viral.
3. Chuyển đổi khung hình 9:16 thông minh & Phụ đề Karaoke Niche Profile:
   - Layout Blur Background hoặc Center Crop chuẩn Shorts/TikTok/Reels.
   - Tự động đồng bộ font chữ, màu kinetic karaoke theo Niche Profile của kênh.
"""

import os
import re
import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from config.settings import settings
from app.agents.content_agent import ContentAgent
from app.services.niche_manager import get_niche, get_niche_subtitle_style
from app.utils.subtitle_helper import SubtitleHelper, ASS_WHITE, ASS_YELLOW, ASS_BLACK
from app.utils.ffmpeg_helper import run_ffmpeg, probe_file, get_duration_ms, detect_audio_speech_start, BT709_COLOR_ARGS, build_atempo_filter
from app.services.repurposer_qa import preflight_repurpose_qa

logger = logging.getLogger(__name__)

REPURPOSER_DIR = Path("output/repurposer")
DOWNLOADS_DIR = REPURPOSER_DIR / "downloads"
CLIPS_DIR = REPURPOSER_DIR / "clips"
SUBS_DIR = REPURPOSER_DIR / "subtitles"

for d in [REPURPOSER_DIR, DOWNLOADS_DIR, CLIPS_DIR, SUBS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

import time
import uuid
import threading

# Giới hạn tối đa 2 render FFmpeg đồng thời trên toàn server để tránh đơ CPU/tràn RAM
_render_semaphore = threading.Semaphore(2)

# Lock download theo video_id để tránh việc 2 máy cùng tải đè hoặc xung đột file
_download_locks: Dict[str, threading.Lock] = {}
_dl_pool_lock = threading.Lock()

def _get_video_download_lock(vid: str) -> threading.Lock:
    with _dl_pool_lock:
        if vid not in _download_locks:
            _download_locks[vid] = threading.Lock()
        return _download_locks[vid]


def extract_youtube_video_id(url_or_id: str) -> Optional[str]:
    """Trích xuất Video ID từ các định dạng URL YouTube hoặc Bilibili (BV1.../av.../b23.tv)."""
    if not url_or_id:
        return None
    url_or_id = url_or_id.strip()
    try:
        from app.services.media.bilibili_fetcher import extract_bilibili_id, is_bilibili_source
        if is_bilibili_source(url_or_id):
            b_parsed = extract_bilibili_id(url_or_id)
            if b_parsed and b_parsed.get("id"):
                return b_parsed["id"]
    except Exception:
        pass
    if len(url_or_id) == 11 and re.match(r'^[0-9A-Za-z_-]{11}$', url_or_id):
        return url_or_id
    patterns = [
        r'(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/|\/live\/)([0-9A-Za-z_-]{11})',
        r'[\?&]v=([0-9A-Za-z_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url_or_id)
        if m:
            return m.group(1)
    return None



class MashupShortRendererMixin:
    def render_mashup_short(
        self,
        source_video_path: str,
        clips: List[Dict[str, Any]],
        output_filename: str,
        niche_id: str = "general",
        layout_mode: str = "smart_crop_full",
        audio_mode: str = "original",
        add_subtitles: bool = True,
        enable_sfx: bool = True,
        bgm_mood: Optional[str] = "suspense",
        title: str = "",
        crop_focus: str = "center",
        device_id: str = "default_device",
        device_name: str = "Thiết bị khách",
        client_ip: str = "",
        source_url: str = "",
        author: str = "",
        mode: str = "self_owned",
        channel_name: str = "",
        description: str = "",
        mute_original: bool = False,
        voice_id: Optional[str] = None,
        blur_watermark: bool = False,
        watermark_position: str = "top_right",
    ) -> Dict[str, Any]:
        """
        Nấu lại video dạng Mashup Multi-Clip:
        1. Trích xuất từng micro-clip với tỉ lệ 9:16 hoặc vuông 1:1, bóc tách sandwich banners.
        2. Tự động làm mờ logo watermark (Anti-Watermark Blur) nếu được yêu cầu.
        3. Drop phụ đề cũ bằng cờ -sn (Lossless-Cut inspired).
        4. Ghép nối liền mạch các clips không bị giật lag khung hình.
        5. Chèn hiệu ứng SFX Whoosh tại điểm nhảy cảnh và nhạc nền BGM ducking xuyên suốt.
        6. Đốt phụ đề Karaoke ASS chuẩn Safe Zone (100% US English).
        """
        import shutil
        if not os.path.exists(source_video_path):
            raise FileNotFoundError(f"Không tìm thấy file nguồn: {source_video_path}")

        from app.agents.voice_agent import VoiceAgent

        if not clips or len(clips) < 2:
            raise ValueError("Chế độ Mashup yêu cầu tối thiểu 2 phân đoạn micro-clips.")

        # Nhận diện chế độ âm thanh
        is_voiceover_mode = audio_mode in (
            "mute_original_voiceover",
            "ai_voiceover_mute",
            "ai_voiceover",
            "duck_original_voiceover",
        )
        should_mute_original = (
            audio_mode in ("mute_original_voiceover", "ai_voiceover_mute", "mute")
            or (audio_mode == "ai_voiceover" and mute_original)
        )

        # Probe audio & video stream
        has_audio = False
        is_source_vertical = True  # Mặc định ưu tiên layout dọc chuẩn video bánh kẹp TikTok/Shorts
        try:
            info = probe_file(source_video_path)
            if info and "streams" in info:
                has_audio = any(s.get("codec_type") == "audio" for s in info["streams"])
                for s in info["streams"]:
                    if s.get("codec_type") == "video":
                        vw = int(s.get("width", 0))
                        vh = int(s.get("height", 0))
                        if vw > 0 and vh > 0:
                            is_source_vertical = (vh >= vw)
                        break
        except Exception:
            has_audio = True

        sandwich_core = "crop=in_w:in_h*0.51:0:in_h*0.205" if is_source_vertical else "crop=in_h:in_h:(in_w-in_h)/2:0"

        uid = uuid.uuid4().hex[:8]
        temp_files: List[Path] = []
        output_path = CLIPS_DIR / output_filename
        output_path_str = str(output_path)

        with _render_semaphore:
            try:
                subclip_paths = []
                clean_title = self._ensure_viral_english_title(title, niche_id)

                focus = (crop_focus or "center").strip().lower()
                if focus == "left":
                    smart_crop_x = "(in_w-out_w)*0.18"
                    center_crop_x = "(iw-ih*9/16)*0.18"
                elif focus == "right":
                    smart_crop_x = "(in_w-out_w)*0.78"
                    center_crop_x = "(iw-ih*9/16)*0.78"
                else:
                    smart_crop_x = "(in_w-out_w)/2"
                    center_crop_x = "(iw-ih*9/16)/2"

                watermark_pos = (watermark_position or "top_right").lower()
                if watermark_pos == "top_left":
                    wm_crop_x, wm_crop_y, wm_crop_w, wm_crop_h = "iw*0.02", "ih*0.02", "iw*0.38", "ih*0.11"
                    wm_ov_x, wm_ov_y = "W*0.02", "H*0.02"
                elif watermark_pos == "bottom_right":
                    wm_crop_x, wm_crop_y, wm_crop_w, wm_crop_h = "iw*0.60", "ih*0.87", "iw*0.38", "ih*0.11"
                    wm_ov_x, wm_ov_y = "W*0.60", "H*0.87"
                elif watermark_pos == "bottom_left":
                    wm_crop_x, wm_crop_y, wm_crop_w, wm_crop_h = "iw*0.02", "ih*0.87", "iw*0.38", "ih*0.11"
                    wm_ov_x, wm_ov_y = "W*0.02", "H*0.87"
                else: # top_right
                    wm_crop_x, wm_crop_y, wm_crop_w, wm_crop_h = "iw*0.60", "ih*0.02", "iw*0.38", "ih*0.11"
                    wm_ov_x, wm_ov_y = "W*0.60", "H*0.02"

                def _apply_wm_filter(base_expr: str) -> str:
                    if not blur_watermark:
                        return f"{base_expr}[v]"
                    return (
                        f"{base_expr}[raw_c];"
                        f"[raw_c]split[wm_in][wm_crp];"
                        f"[wm_crp]crop={wm_crop_w}:{wm_crop_h}:{wm_crop_x}:{wm_crop_y},boxblur=18:5[wm_blured];"
                        f"[wm_in][wm_blured]overlay={wm_ov_x}:{wm_ov_y}[v]"
                    )

                # ── BƯỚC 1: RENDER TỪNG MICRO-CLIP 9:16/1:1 VỚI ZOOM PUNCH VÀ DEAD-AIR FADE ──
                for idx, c in enumerate(clips):
                    st = max(0.0, float(c.get("start_seconds", 0.0)))
                    et = float(c.get("end_seconds", st + 4.0))
                    dur = max(0.8, et - st)
                    zoom = max(1.20, float(c.get("zoom_punch", 1.20)))

                    clip_out = REPURPOSER_DIR / f"temp_{uid}_c{idx}.mp4"
                    temp_files.append(clip_out)

                    if layout_mode == "strip_sandwich_square":
                        base_v = (
                            f"[0:v]{sandwich_core},"
                            "scale=1080:1080,"
                            "eq=contrast=1.04:brightness=0.01:saturation=1.05"
                        )
                        v_filter = _apply_wm_filter(base_v)
                    elif layout_mode == "strip_sandwich_clean_916":
                        base_v = (
                            f"[0:v]{sandwich_core},"
                            f"scale=1080*{zoom:.2f}:1920*{zoom:.2f}:force_original_aspect_ratio=increase,"
                            f"crop=1080:1920:{smart_crop_x}:(in_h-out_h)/2,"
                            "eq=contrast=1.04:brightness=0.01:saturation=1.05"
                        )
                        v_filter = _apply_wm_filter(base_v)
                    elif layout_mode == "strip_sandwich_bokeh":
                        base_v = (
                            f"[0:v]{sandwich_core}[mc_raw];"
                            "[mc_raw]split[mc_bg][mc_fg];"
                            "[mc_bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=15:3,scale=1080:1920[bg];"
                            "[mc_fg]scale=1080:1080,eq=contrast=1.03:brightness=0.01:saturation=1.04[fg];"
                            "[bg][fg]overlay=0:(H-h)/2"
                        )
                        v_filter = _apply_wm_filter(base_v)
                    elif layout_mode == "smart_crop_full":
                        base_v = (
                            f"[0:v]scale=1080*{zoom:.2f}:1920*{zoom:.2f}:force_original_aspect_ratio=increase,"
                            f"crop=1080:1920:{smart_crop_x}:(in_h-out_h)/2,"
                            "eq=contrast=1.04:brightness=0.01:saturation=1.05"
                        )
                        v_filter = _apply_wm_filter(base_v)
                    elif layout_mode == "center_crop":
                        base_v = f"[0:v]crop=ih*9/16:ih:{center_crop_x}:0,scale=1080:1920,eq=contrast=1.03:brightness=0.01:saturation=1.04"
                        v_filter = _apply_wm_filter(base_v)
                    else:  # blur_background
                        base_v = (
                            "[0:v]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:2,scale=1080:1920[bg];"
                            f"[0:v]crop=iw*0.92:ih*0.90:(iw-out_w)/2:(ih-out_h)/2,scale=1080*{zoom:.2f}:-2:flags=lanczos,eq=contrast=1.03:brightness=0.01:saturation=1.04[fg];"
                            "[bg][fg]overlay=(W-w)/2:(H-h)/2"
                        )
                        v_filter = _apply_wm_filter(base_v)

                    fade_st = max(0.0, dur - 0.06)
                    voice_ok = False
                    voice_clip_file = None

                    if is_voiceover_mode:
                        raw_narration = c.get("custom_voiceover_text") or c.get("transcript_snippet") or c.get("summary") or c.get("text") or ""
                        # 🇺🇸 US Export Mode: Nếu phát hiện tiếng Việt, tự động chuyển dịch sang tiếng Anh viral
                        if VoiceAgent.DISABLE_VIETNAMESE_VOICE and VoiceAgent._is_vietnamese(raw_narration):
                            clean_narration = self._ensure_english_narration(raw_narration, niche_id)
                        else:
                            clean_narration = SubtitleHelper.sanitize_subtitle_text(raw_narration)
                        if not clean_narration or len(clean_narration.split()) < 2:
                            role = str(c.get("role", "CLIP")).upper()
                            default_narr_en = {
                                "HOOK": "The most heartbreaking moment nobody saw coming...",
                                "SETUP": "It all began with a desperate call for help...",
                                "TENSION": "Crawling deep beneath the freezing porch...",
                                "CLIMAX": "A helpless puppy shivering in the dark!",
                                "LOOP": "Follow for the next miracle chapter now!",
                            }
                            clean_narration = default_narr_en.get(role, f"Watch this scene #{idx+1}!")

                        voice_clip_file = REPURPOSER_DIR / f"temp_{uid}_voice_c{idx}.mp3"
                        temp_files.append(voice_clip_file)
                        voice_ok = self._synthesize_voiceover_sync(
                            text=clean_narration,
                            output_path=str(voice_clip_file),
                            niche_id=niche_id,
                            voice_override=voice_id,
                        )

                    if voice_ok and voice_clip_file and voice_clip_file.exists() and voice_clip_file.stat().st_size > 800:
                        v_dur_ms = get_duration_ms(str(voice_clip_file))
                        v_dur_s = (v_dur_ms / 1000.0) if v_dur_ms > 0 else dur
                        c["voice_dur_ms"] = v_dur_ms

                        if v_dur_s <= dur:
                            tempo_str = "anull"
                        else:
                            speed_ratio = min(v_dur_s / max(dur, 0.8), 1.25)
                            tempo_str = build_atempo_filter(speed_ratio)
                        tempo_chain = f"{tempo_str}," if tempo_str != "anull" else ""

                        if should_mute_original or not has_audio:
                            # 🔇 Tắt hoàn toàn âm thanh gốc, chỉ lấy âm thanh giọng đọc mới
                            a_filter = (
                                f"[1:a]{tempo_chain}volume=1.20,aresample=44100,aformat=channel_layouts=stereo,"
                                f"apad=whole_dur={dur:.3f},atrim=0:{dur:.3f},"
                                f"afade=t=in:ss=0:d=0.05,afade=t=out:st={fade_st:.2f}:d=0.05[a]"
                            )
                        else:
                            # Chế độ Ducking: Giữ tiếng gốc nhỏ (ambient)
                            a_filter = (
                                f"[1:a]{tempo_chain}volume=1.20,aresample=44100,aformat=channel_layouts=stereo,"
                                f"apad=whole_dur={dur:.3f},atrim=0:{dur:.3f}[v_voice];"
                                f"[0:a]volume=0.10,aresample=44100,aformat=channel_layouts=stereo,atrim=0:{dur:.3f}[v_amb];"
                                f"[v_voice][v_amb]amix=inputs=2:duration=first:dropout_transition=1,"
                                f"afade=t=in:ss=0:d=0.05,afade=t=out:st={fade_st:.2f}:d=0.05[a]"
                            )

                        clip_cmd = [
                            "-y",
                            "-ss", f"{st:.3f}",
                            "-t", f"{dur:.3f}",
                            "-i", source_video_path,
                            "-i", str(voice_clip_file),
                            "-sn",
                            "-filter_complex", f"{v_filter};{a_filter}",
                            "-map", "[v]",
                            "-map", "[a]",
                        ]
                    elif audio_mode == "mute":
                        clip_cmd = [
                            "-y",
                            "-ss", f"{st:.3f}",
                            "-t", f"{dur:.3f}",
                            "-i", source_video_path,
                            "-sn",
                            "-filter_complex", f"{v_filter}",
                            "-map", "[v]",
                            "-an",
                        ]
                    else:
                        a_filter = f"[0:a]afade=t=in:ss=0:d=0.05,afade=t=out:st={fade_st:.2f}:d=0.05[a]"
                        clip_cmd = [
                            "-y",
                            "-ss", f"{st:.3f}",
                            "-t", f"{dur:.3f}",
                            "-i", source_video_path,
                            "-sn",
                        ]
                        if has_audio:
                            clip_cmd += [
                                "-filter_complex", f"{v_filter};{a_filter}",
                                "-map", "[v]",
                                "-map", "[a]",
                            ]
                        else:
                            clip_cmd += [
                                "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo",
                                "-filter_complex", v_filter,
                                "-map", "[v]",
                                "-map", "1:a",
                            ]

                    clip_cmd += [
                        "-c:v", "libx264",
                        "-preset", "veryfast",
                        "-crf", "20",
                        "-r", "30",
                        "-pix_fmt", "yuv420p",
                        *BT709_COLOR_ARGS,
                        "-c:a", "aac",
                        "-b:a", "192k",
                        "-ar", "44100",
                        "-ac", "2",
                        str(clip_out)
                    ]

                    run_ffmpeg(clip_cmd)
                    subclip_paths.append(str(clip_out))

                # ── BƯỚC 2: GHÉP NỐI CÁC CLIPS (CONCAT DEMUXER SIÊU TỐC) ──
                concat_list_file = REPURPOSER_DIR / f"temp_{uid}_list.txt"
                temp_files.append(concat_list_file)
                with open(concat_list_file, "w", encoding="utf-8") as f:
                    for scp in subclip_paths:
                        abs_p = Path(scp).resolve().as_posix().replace("'", "'\\''")
                        f.write(f"file '{abs_p}'\n")

                temp_concat_video = REPURPOSER_DIR / f"temp_{uid}_concat.mp4"
                temp_files.append(temp_concat_video)

                concat_cmd = [
                    "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", str(concat_list_file),
                    "-c", "copy",
                    str(temp_concat_video)
                ]
                run_ffmpeg(concat_cmd)

                # ── BƯỚC 3: NHẠC NỀN BGM DUCKING & SFX CHUYỂN CẢNH (WHOOSH) ──
                current_stage_video = temp_concat_video
                total_duration = sum(max(0.8, float(c.get("end_seconds", 4.0)) - max(0.0, float(c.get("start_seconds", 0.0)))) for c in clips)

                # Tìm file BGM phù hợp
                bgm_map = {
                    "suspense": "assets/bgm/dark_suspense.wav",
                    "epic": "assets/bgm/inspiring_epic.wav",
                    "chill": "assets/bgm/chill_ambient.mp3",
                    "cyber": "assets/bgm/scifi_cyber.wav",
                }
                bgm_target = bgm_map.get(bgm_mood or "suspense")
                if not bgm_target or not os.path.exists(bgm_target):
                    for cand in bgm_map.values():
                        if os.path.exists(cand):
                            bgm_target = cand
                            break

                temp_audio_video = REPURPOSER_DIR / f"temp_{uid}_bgm.mp4"
                if bgm_target and os.path.exists(bgm_target) and audio_mode != "mute":
                    temp_files.append(temp_audio_video)
                    bgm_vol = 0.035 if is_voiceover_mode else 0.05
                    # Bộ lọc Cinematic De-Harshing:
                    # 1. lowpass=f=4200: Cắt bỏ tần số cao sắc nhọn (>4.2kHz) gây chói tai và xé tiếng trên loa điện thoại
                    # 2. equalizer=f=3200: Giảm nhẹ dải 3.2kHz tạo cảm giác âm thanh ấm áp, êm dịu, chìm dưới giọng đọc
                    bgm_filter = f"[1:a]lowpass=f=4200,equalizer=f=3200:t=q:w=1.2:g=-6,volume={bgm_vol:.3f}[bgm]"
                    bgm_cmd = [
                        "-y",
                        "-i", str(current_stage_video),
                        "-stream_loop", "-1",
                        "-i", bgm_target,
                        "-filter_complex",
                        f"[0:a]volume=1.0[voice];{bgm_filter};[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                        "-map", "0:v",
                        "-map", "[aout]",
                        "-c:v", "copy",
                        "-c:a", "aac",
                        "-b:a", "192k",
                        "-t", f"{total_duration:.2f}",
                        str(temp_audio_video)
                    ]
                    try:
                        run_ffmpeg(bgm_cmd)
                        current_stage_video = temp_audio_video
                    except Exception as e_bgm:
                        logger.warning(f"[Mashup] Không thể mix BGM: {e_bgm}")

                # SFX chuyển cảnh (Whoosh Transitions & Impact Hit)
                if enable_sfx:
                    temp_sfx_video = REPURPOSER_DIR / f"temp_{uid}_sfx.mp4"
                    temp_files.append(temp_sfx_video)
                    try:
                        from app.utils.ffmpeg_helper import mix_sfx_transitions
                        scene_ts = [0.0]
                        cur = 0.0
                        for c in clips[:-1]:
                            cur += max(0.8, float(c.get("end_seconds", 4.0)) - max(0.0, float(c.get("start_seconds", 0.0))))
                            scene_ts.append(round(cur, 2))

                        mix_sfx_transitions(
                            video_path=str(current_stage_video),
                            output_path=str(temp_sfx_video),
                            scene_timestamps_seconds=scene_ts,
                            sfx_dir="assets/sfx",
                            default_sfx="whoosh",
                            sfx_volume=0.16,
                            hit_sfx="hit",
                            hit_volume=0.18,
                        )
                        if temp_sfx_video.exists() and temp_sfx_video.stat().st_size > 1000:
                            current_stage_video = temp_sfx_video
                    except Exception as e_sfx:
                        logger.warning(f"[Mashup] Không thể mix SFX transitions: {e_sfx}")

                # ── BƯỚC 4: ĐỐT PHỤ ĐỀ KINETIC KARAOKE ASS & NHÃN BỐI CẢNH TIMELINE ──
                ass_path = None
                effective_add_subtitles = add_subtitles if add_subtitles is not None else True
                if effective_add_subtitles:
                    ass_path = SUBS_DIR / f"sub_mashup_{Path(output_filename).stem}.ass"
                    temp_files.append(ass_path)

                    from app.services.niche_manager import get_niche, get_niche_subtitle_style
                    niche_profile = get_niche(niche_id)
                    niche_sub = get_niche_subtitle_style(niche_profile)
                    fontname = niche_sub.get("fontname", "Arial Black")

                    mashup_margin_v = 200 if layout_mode == "strip_sandwich_square" else 580
                    SubtitleHelper.save_mashup_ass_file(
                        clips=clips,
                        output_path=str(ass_path),
                        fontname=fontname,
                        fontsize=44,
                        margin_v=mashup_margin_v,
                        niche_id=niche_id,
                        credit_text=author or channel_name or None,
                        is_english=True,
                        is_voiceover=is_voiceover_mode,
                    )

                    escaped_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
                    burn_cmd = [
                        "-y",
                        "-i", str(current_stage_video),
                        "-vf", f"ass='{escaped_ass}'",
                        "-c:v", "libx264",
                        "-preset", "veryfast",
                        "-crf", "20",
                        "-c:a", "copy",
                        str(output_path)
                    ]
                    try:
                        run_ffmpeg(burn_cmd)
                    except Exception as e_sub:
                        logger.warning(f"[Mashup] Lỗi burn phụ đề ASS, fallback dùng video không sub: {e_sub}")
                        shutil.copy2(str(current_stage_video), str(output_path))
                else:
                    shutil.copy2(str(current_stage_video), str(output_path))

                # ── BƯỚC 5: TẠO METADATA MONETIZATION & PRE-FLIGHT QA ──
                from app.services.monetization_engine import (
                    build_repurposed_video_description,
                    build_repurposed_pinned_comment,
                    find_best_amazon_product,
                )
                full_summary = " ".join(c.get("summary", "") for c in clips)
                if not description or not description.strip():
                    description = build_repurposed_video_description(
                        title=clean_title,
                        summary=full_summary,
                        niche_id=niche_id,
                        channel_name=channel_name,
                        source_url=source_url,
                        author=author,
                        mode=mode,
                        tracking_tag="kizakawa-20",
                    )

                pinned_comment = build_repurposed_pinned_comment(
                    title=clean_title,
                    niche_id=niche_id,
                    source_url=source_url,
                    channel_name=channel_name,
                    tracking_tag="kizakawa-20",
                )
                amazon_prod = find_best_amazon_product(clean_title, niche_id)

                file_size_mb = round(output_path.stat().st_size / (1024 * 1024), 2)
                
                meta_file = CLIPS_DIR / f"{Path(output_filename).stem}.meta.json"
                meta_data = {
                    "filename": output_filename,
                    "device_id": device_id,
                    "device_name": device_name,
                    "client_ip": client_ip,
                    "created_at": time.time(),
                    "duration": round(total_duration, 1),
                    "file_size_mb": file_size_mb,
                    "niche_id": niche_id,
                    "layout_mode": layout_mode,
                    "audio_mode": audio_mode,
                    "mute_original": should_mute_original,
                    "voice_id": voice_id or "",
                    "title": clean_title,
                    "subtitle_text": full_summary[:120],
                    "description": description,
                    "pinned_comment": pinned_comment,
                    "amazon_product": amazon_prod,
                    "source_url": source_url,
                    "author": author,
                    "mode": mode,
                    "is_mashup": True,
                    "clips_count": len(clips),
                }

                qa_report = preflight_repurpose_qa(
                    output_path_str,
                    str(ass_path) if (ass_path and ass_path.exists()) else None,
                    meta_data
                )
                meta_data["qa_report"] = qa_report

                try:
                    with open(meta_file, "w", encoding="utf-8") as mf:
                        json.dump(meta_data, mf, ensure_ascii=False, indent=2)
                except Exception as e_meta:
                    logger.warning(f"[Mashup] Lỗi lưu meta_file: {e_meta}")

                return {
                    "success": True,
                    "output_path": output_path_str,
                    "relative_url": f"/media/repurposer/clips/{output_filename}",
                    "filename": output_filename,
                    "duration": round(total_duration, 1),
                    "file_size_mb": file_size_mb,
                    "clips_count": len(clips),
                    "niche_id": niche_id,
                    "layout_mode": layout_mode,
                    "audio_mode": audio_mode,
                    "title": clean_title,
                    "description": description,
                    "pinned_comment": pinned_comment,
                    "amazon_product": amazon_prod,
                    "qa_report": qa_report,
                }
            finally:
                for tf in temp_files:
                    try:
                        if tf.exists():
                            tf.unlink()
                    except Exception:
                        pass
