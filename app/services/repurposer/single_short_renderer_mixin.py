"""app.services.repurposer.single_short_renderer_mixin — Tổng hợp giọng đọc & render video Short đơn từ khoảnh khắc viral."""
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



class SingleShortRendererMixin:
    def _synthesize_voiceover_sync(
        self,
        text: str,
        output_path: str,
        niche_id: str = "general",
        voice_override: Optional[str] = None,
    ) -> bool:
        """
        Sinh file audio giọng đọc AI đồng bộ (VoiceStudio-standard) từ văn bản sử dụng Edge-TTS.
        Đặc biệt:
        - Tự động nhận diện ngôn ngữ: Văn bản tiếng Việt dùng vi-VN-NamMinhNeural / vi-VN-HoaiMyNeural;
          Văn bản tiếng Anh (US Export) dùng US Neural Voice Persona tương ứng với Niche.
        - Cơ chế retry và voice fallback dự phòng chống rớt mạng.
        - Khả năng chạy an toàn đa luồng/event loop (tránh asyncio.run runtime crash).
        """
        import asyncio
        import concurrent.futures
        import edge_tts
        from app.agents.voice_agent import VoiceAgent

        clean_text = SubtitleHelper.sanitize_subtitle_text(text)
        if not clean_text or len(clean_text.strip()) < 2:
            return False

        # Xác định giọng đọc phù hợp
        # 🇺🇸 US Export Mode: Nếu đang bật chế độ xuất khẩu Hoa Kỳ (DISABLE_VIETNAMESE_VOICE),
        # tự động chuyển dịch toàn bộ văn bản sang tiếng Anh và chỉ dùng US Neural Voice Personas
        if VoiceAgent.DISABLE_VIETNAMESE_VOICE:
            if VoiceAgent._is_vietnamese(clean_text):
                clean_text = self._ensure_english_narration(clean_text, niche_id)
            is_vn = False
            if voice_override and not voice_override.lower().startswith("vi-"):
                primary_voice = voice_override
            else:
                primary_voice = VoiceAgent.get_persona_voice(niche_id=niche_id)
            candidate_voices = [primary_voice]
            for v in ["en-US-AndrewNeural", "en-US-GuyNeural", "en-US-ChristopherNeural", "en-US-JennyNeural"]:
                if v not in candidate_voices:
                    candidate_voices.append(v)
            tts_rate = "+12%"
        else:
            is_vn = VoiceAgent._is_vietnamese(clean_text)
            if voice_override:
                primary_voice = voice_override
                if voice_override.lower().startswith("vi-"):
                    is_vn = True
                elif voice_override.lower().startswith("en-"):
                    is_vn = False
            elif is_vn:
                primary_voice = "vi-VN-NamMinhNeural"
            else:
                primary_voice = VoiceAgent.get_persona_voice(niche_id=niche_id)

            candidate_voices = [primary_voice]
            if is_vn:
                for v in ["vi-VN-NamMinhNeural", "vi-VN-HoaiMyNeural"]:
                    if v not in candidate_voices:
                        candidate_voices.append(v)
            else:
                for v in ["en-US-AndrewNeural", "en-US-GuyNeural", "en-US-ChristopherNeural", "en-US-JennyNeural"]:
                    if v not in candidate_voices:
                        candidate_voices.append(v)
            tts_rate = "+12%" if not is_vn else "+7%"

        async def _do_gen():
            for v in candidate_voices:
                for attempt in range(2):
                    try:
                        comm = edge_tts.Communicate(clean_text, v, rate=tts_rate)
                        await comm.save(output_path)
                        p = Path(output_path)
                        if p.exists() and p.stat().st_size > 800:
                            return True
                    except Exception as err:
                        logger.warning(f"[Voiceover] Lỗi Edge-TTS '{v}' (attempt {attempt+1}): {err}")
                        await asyncio.sleep(0.5)
            return False

        try:
            loop = None
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    success = pool.submit(asyncio.run, _do_gen()).result(timeout=25)
            else:
                success = asyncio.run(_do_gen())
            return bool(success and Path(output_path).exists() and Path(output_path).stat().st_size > 800)
        except Exception as e:
            logger.warning(f"[Voiceover] Lỗi tạo thuyết minh AI cho '{clean_text[:40]}...': {e}")
            return False

    def render_repurposed_short(
        self,
        source_video_path: str,
        start_seconds: float,
        end_seconds: float,
        output_filename: str,
        niche_id: str = "general",
        layout_mode: str = "smart_crop_full",
        audio_mode: str = "original",
        add_subtitles: bool = True,
        subtitle_text: str = "",
        timed_transcript: Optional[List[Dict[str, Any]]] = None,
        credit_text: Optional[str] = None,
        device_id: str = "default_device",
        device_name: str = "Thiết bị khách",
        client_ip: str = "",
        title: str = "",
        description: str = "",
        channel_name: str = "",
        source_url: str = "",
        author: str = "",
        mode: str = "self_owned",
        crop_focus: str = "center",
        mute_original: bool = False,
        voice_id: Optional[str] = None,
        custom_voiceover_text: Optional[str] = None,
        bgm_mood: Optional[str] = None,
        blur_watermark: bool = False,
        watermark_position: str = "top_right",
    ) -> Dict[str, Any]:
        """
        Cắt đoạn highlight, chuyển sang tỉ lệ dọc 9:16 (1080x1920) hoặc vuông 1:1, bóc tách sandwich banners,
        làm mờ logo watermark và đốt phụ đề Karaoke ASS chuẩn Niche Profile.
        Tích hợp toàn diện:
        1. VAD Dead-Air Removal: Tự động dịch mốc cắt tới điểm bắt đầu giọng nói, triệt tiêu khoảng lặng ở 2s đầu.
        2. Framing thông minh: smart_crop_full, strip_sandwich_square (bóc tách 1:1), strip_sandwich_clean_916, strip_sandwich_bokeh.
        3. Phụ đề Karaoke Dynamic: Kết nối trực tiếp mốc thời gian thực từng từ từ timed_transcript hoặc sinh theo giọng đọc mới.
        4. Audio Envelope & Vocal Ducking: Bắt buộc áp dụng fade-in/out 0.25s; hỗ trợ Tắt tiếng cũ + Lồng giọng đọc AI 100% US English.
        5. Pre-Flight QA Gate: Tự động kiểm tra LUFS, dead-air và độ đồng bộ trước khi bàn giao clip.
        """
        if not os.path.exists(source_video_path):
            raise FileNotFoundError(f"Không tìm thấy file nguồn: {source_video_path}")

        from app.agents.voice_agent import VoiceAgent

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

        # 0. Tích hợp Voice Activity Detection (VAD) để loại bỏ dead-air ở đầu video
        speech_offset = detect_audio_speech_start(
            file_path=source_video_path,
            start_seconds=start_seconds,
            scan_duration=2.5,
            noise_threshold_db=-32.0,
        )
        if speech_offset > 0.15 and (end_seconds - (start_seconds + speech_offset)) >= 8.0:
            logger.info(f"VAD: Tự động dịch start_seconds từ {start_seconds:.2f}s lên {start_seconds + speech_offset:.2f}s (cắt bỏ {speech_offset:.2f}s dead-air)")
            start_seconds += speech_offset

        duration = max(end_seconds - start_seconds, 5.0)
        output_path = CLIPS_DIR / output_filename
        output_path_str = str(output_path)
        temp_files: List[Path] = []

        # Chuẩn hóa tiêu đề tiếng Anh viral
        clean_title = self._ensure_viral_english_title(title, niche_id)

        # Xác định văn bản giọng đọc thuyết minh (dành cho chế độ lồng tiếng mới)
        raw_voice_text = custom_voiceover_text or subtitle_text or title or ""
        # 🇺🇸 US Export Mode: Tự động chuyển dịch sang tiếng Anh nếu phát hiện tiếng Việt
        if VoiceAgent.DISABLE_VIETNAMESE_VOICE and VoiceAgent._is_vietnamese(raw_voice_text):
            clean_voice_text = self._ensure_english_narration(raw_voice_text, niche_id)
        else:
            clean_voice_text = SubtitleHelper.sanitize_subtitle_text(raw_voice_text)

        if not clean_voice_text or len(clean_voice_text.split()) < 2:
            clean_voice_text = self._ensure_english_narration("", niche_id)

        # Tự động tạo mô tả video & bình luận ghim kèm link Store ID kizakawa-20 (Amazon Associates)
        from app.services.monetization_engine import (
            build_repurposed_video_description,
            build_repurposed_pinned_comment,
            find_best_amazon_product,
            build_amazon_url
        )
        if not description or not description.strip():
            description = build_repurposed_video_description(
                title=clean_title,
                summary=subtitle_text,
                niche_id=niche_id,
                channel_name=channel_name,
                source_url=source_url,
                author=author,
                mode=mode,
                tracking_tag="kizakawa-20",
            )
        amazon_prod = find_best_amazon_product(niche=niche_id, title=clean_title)
        amazon_prod["affiliate_url"] = build_amazon_url(
            amazon_prod["asin"],
            "kizakawa-20",
            is_search=amazon_prod.get("is_search", False)
        )

        # Tự động tạo bình luận ghim kèm Store ID kizakawa-20
        pinned_comment = build_repurposed_pinned_comment(
            title=clean_title,
            niche_id=niche_id,
            source_url=source_url,
            author=author,
            channel_name=channel_name,
            tracking_tag="kizakawa-20",
        )

        # Lấy thông tin phụ đề Niche Profile
        niche_profile = get_niche(niche_id)
        niche_sub = get_niche_subtitle_style(niche_profile)
        fontname = niche_sub.get("fontname", "Arial Black")
        highlight_color = niche_sub.get("secondary_ass", "&H0000FFFF")

        # 1. Tạo file phụ đề ASS nếu có yêu cầu
        ass_file_path = None
        effective_credit = credit_text
        if not effective_credit and author:
            effective_credit = f"Via: {author} • Full Video in Desc"
        elif not effective_credit and source_url:
            effective_credit = "Full Video in Description"

        if add_subtitles:
            ass_filename = f"sub_{Path(output_filename).stem}.ass"
            ass_path = SUBS_DIR / ass_filename
            ass_file_path = str(ass_path)

            eff_margin_v = 200 if layout_mode == "strip_sandwich_square" else 580

            if is_voiceover_mode:
                # Khi lồng giọng đọc AI/Tự sinh: Phụ đề BẮT BUỘC phải khớp 100% với lời thuyết minh mới,
                # KHÔNG dùng timed_transcript của video cũ (vì từ ngữ và thời điểm phát âm đã thay đổi hoàn toàn).
                from app.schemas.contracts import SceneContract
                logger.info(f"Tạo phụ đề Kinetic Karaoke ASS khớp giọng đọc mới: '{clean_voice_text[:40]}...'")
                pseudo_scene = SceneContract(
                    scene_id=1,
                    narration=clean_voice_text,
                    visual_keyword="viral highlight",
                    audio_duration_ms=int(duration * 1000),
                    estimated_seconds=min(max(float(duration), 1.0), 300.0),
                )
                SubtitleHelper.save_ass_file(
                    scenes=[pseudo_scene],
                    output_path=ass_file_path,
                    fontname=fontname,
                    fontsize=44,
                    margin_v=eff_margin_v,
                    niche_id=niche_id,
                    credit_text=effective_credit,
                )
            elif timed_transcript and len(timed_transcript) > 0:
                # GẮN KẾT TRỰC TIẾP TIMED TRANSCRIPT (MỐC THỜI GIAN THẬT TỪNG TỪ)
                logger.info(f"Tạo phụ đề Karaoke ASS từ {len(timed_transcript)} timed segments chuẩn xác.")
                SubtitleHelper.save_ass_from_timed_segments(
                    timed_segments=timed_transcript,
                    output_path=ass_file_path,
                    total_duration_sec=duration,
                    clip_start_offset=start_seconds,
                    fontname=fontname,
                    fontsize=44,
                    margin_v=eff_margin_v,
                    niche_id=niche_id,
                    credit_text=effective_credit,
                    show_cta_badge=True,
                )
            elif subtitle_text.strip():
                clean_sub = SubtitleHelper.sanitize_subtitle_text(subtitle_text)
                from app.schemas.contracts import SceneContract
                pseudo_scene = SceneContract(
                    scene_id=1,
                    narration=clean_sub,
                    visual_keyword="viral highlight",
                    audio_duration_ms=int(duration * 1000),
                    estimated_seconds=min(max(float(duration), 1.0), 300.0),
                )
                SubtitleHelper.save_ass_file(
                    scenes=[pseudo_scene],
                    output_path=ass_file_path,
                    fontname=fontname,
                    fontsize=42,
                    margin_v=eff_margin_v,
                    niche_id=niche_id,
                    credit_text=effective_credit,
                    show_cta_badge=True,
                )
            else:
                # Nếu không có transcript, sinh phụ đề hook dẫn chuyện theo tiêu đề video
                from app.schemas.contracts import SceneContract
                clean_title_sub = SubtitleHelper.sanitize_subtitle_text(clean_title)
                pseudo_scene = SceneContract(
                    scene_id=1,
                    narration=clean_title_sub or "WATCH THIS UNBELIEVABLE MOMENT!",
                    visual_keyword="viral highlight",
                    audio_duration_ms=int(duration * 1000),
                    estimated_seconds=min(max(float(duration), 1.0), 300.0),
                )
                SubtitleHelper.save_ass_file(
                    scenes=[pseudo_scene],
                    output_path=ass_file_path,
                    fontname=fontname,
                    fontsize=44,
                    margin_v=eff_margin_v,
                    niche_id=niche_id,
                    credit_text=effective_credit,
                )

        # Kiểm tra xem video nguồn có audio stream không
        has_audio = False
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
        except Exception as e:
            logger.warning(f"Lỗi probe source stream: {e}")
            has_audio = True

        sandwich_core = "crop=in_w:in_h*0.51:0:in_h*0.205" if is_source_vertical else "crop=in_h:in_h:(in_w-in_h)/2:0"

        # Chuẩn hóa crop_focus: 'center' (mặc định), 'left' (khách mời/khung trái), 'right' (MC/khung phải)
        focus = (crop_focus or "center").strip().lower()
        if focus not in ("left", "right", "center"):
            focus = "center"

        if focus == "left":
            smart_crop_x = "(in_w-out_w)*0.18"
            center_crop_x = "(iw-ih*9/16)*0.18"
        elif focus == "right":
            smart_crop_x = "(in_w-out_w)*0.82"
            center_crop_x = "(iw-ih*9/16)*0.82"
        else: # center
            smart_crop_x = "(in_w-out_w)/2"
            center_crop_x = "(iw-ih*9/16)/2"

        # 2. Xây dựng bộ lọc FFmpeg chuyển sang tỉ lệ dọc 9:16 (1080x1920) hoặc vuông 1:1, bóc tách sandwich & làm mờ logo
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

        def _apply_watermark_and_sub(source_pad: str, dest_pad: str = "v") -> str:
            segments = []
            cur = source_pad
            if blur_watermark:
                segments.append(
                    f"{cur}split[wm_in][wm_crp];"
                    f"[wm_crp]crop={wm_crop_w}:{wm_crop_h}:{wm_crop_x}:{wm_crop_y},boxblur=18:5[wm_blured];"
                    f"[wm_in][wm_blured]overlay={wm_ov_x}:{wm_ov_y}[v_wm_done]"
                )
                cur = "[v_wm_done]"

            if ass_file_path and os.path.exists(ass_file_path):
                escaped_ass = ass_file_path.replace("\\", "/").replace(":", "\\:")
                segments.append(f"{cur}ass='{escaped_ass}'[{dest_pad}]")
            else:
                segments.append(f"{cur}null[{dest_pad}]")
            return ";".join(segments)

        filter_parts = []
        if layout_mode == "strip_sandwich_square":
            # ⏹️ Bóc tách video vuông 1:1 nguyên bản (Cắt bỏ 100% banner trên và footer phụ đề dưới, xuất 1080x1080)
            v_core = (
                f"[0:v]{sandwich_core},"
                "scale=1080:1080,"
                "eq=contrast=1.04:brightness=0.01:saturation=1.05[v_core_sq]"
            )
            filter_parts.append(v_core)
            filter_parts.append(_apply_watermark_and_sub("[v_core_sq]", "v"))

        elif layout_mode == "strip_sandwich_clean_916":
            # ✂️ Bóc tách lõi giữa và phóng to lấp đầy 9:16 Shorts (1080x1920) không bị dính chữ của banner/phụ đề cũ
            v_core = (
                f"[0:v]{sandwich_core},"
                "scale=1080*1.78:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920:{smart_crop_x}:(in_h-out_h)/2,"
                "eq=contrast=1.04:brightness=0.01:saturation=1.05[v_core_916]"
            )
            filter_parts.append(v_core)
            filter_parts.append(_apply_watermark_and_sub("[v_core_916]", "v"))

        elif layout_mode == "strip_sandwich_bokeh":
            # 🎨 Bóc tách lõi giữa đặt trên nền Bokeh Blur 9:16 sạch đẹp (1080x1920), triệt tiêu hoàn toàn viền chữ cũ
            v_core = (
                f"[0:v]{sandwich_core}[c_raw];"
                "[c_raw]split[c_bg][c_fg];"
                "[c_bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=15:3,scale=1080:1920[bg];"
                "[c_fg]scale=1080:1080,eq=contrast=1.03:brightness=0.01:saturation=1.04[fg];"
                "[bg][fg]overlay=0:(H-h)/2[v_core_bk]"
            )
            filter_parts.append(v_core)
            filter_parts.append(_apply_watermark_and_sub("[v_core_bk]", "v"))

        elif layout_mode == "smart_crop_full":
            v_smart = (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920:{smart_crop_x}:(in_h-out_h)/2,"
                "eq=contrast=1.04:brightness=0.01:saturation=1.05[v_core_sm]"
            )
            filter_parts.append(v_smart)
            filter_parts.append(_apply_watermark_and_sub("[v_core_sm]", "v"))

        elif layout_mode == "center_crop":
            v_crop = (
                f"[0:v]crop=ih*9/16:ih:{center_crop_x}:0,scale=1080:1920,"
                "eq=contrast=1.03:brightness=0.01:saturation=1.04[v_core_cc]"
            )
            filter_parts.append(v_crop)
            filter_parts.append(_apply_watermark_and_sub("[v_core_cc]", "v"))

        else: # blur_background
            v_blur = (
                "[0:v]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=12:2,scale=1080:1920[bg];"
                "[0:v]crop=iw*0.92:ih*0.90:(iw-out_w)/2:(ih-out_h)/2,scale=1080:-2:flags=lanczos,eq=contrast=1.03:brightness=0.01:saturation=1.04[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[v_core_bb]"
            )
            filter_parts.append(v_blur)
            filter_parts.append(_apply_watermark_and_sub("[v_core_bb]", "v"))

        # Bộ lọc audio: Envelope Fade In/Out (0.25s) bắt buộc chống clipping/popping
        fade_out_st = max(0.0, duration - 0.3)
        extra_input_args = []
        voice_tmp_path = None

        if is_voiceover_mode:
            # Thuyết minh AI mới chuẩn VoiceStudio:
            # 1. 🎙️ Voice Persona Registry: Hỗ trợ cả giọng Việt và giọng Mỹ đa dạng theo lựa chọn
            # 2. ⏱️ Timed Speech Synchronization: Tự động co/giãn tốc độ qua atempo để khớp khít thời lượng video clip
            # 3. 🔇 Tắt hoàn toàn âm thanh video cũ (should_mute_original) hoặc hạ nhỏ âm lượng (Ducking)
            try:
                voice_tmp_file = REPURPOSER_DIR / f"temp_voice_{Path(output_filename).stem}.mp3"
                voice_tmp_path = str(voice_tmp_file)
                temp_files.append(voice_tmp_file)

                voice_ok = self._synthesize_voiceover_sync(
                    text=clean_voice_text,
                    output_path=voice_tmp_path,
                    niche_id=niche_id,
                    voice_override=voice_id,
                )

                if voice_ok and os.path.exists(voice_tmp_path) and os.path.getsize(voice_tmp_path) > 800:
                    extra_input_args = ["-i", voice_tmp_path]

                    # Tính toán tỷ lệ đồng bộ thời gian (Timed Speech Sync: Giữ nguyên 1.0x tự nhiên, không bao giờ kéo lê làm chậm giọng)
                    voice_dur_ms = get_duration_ms(voice_tmp_path)
                    voice_dur_s = (voice_dur_ms / 1000.0) if voice_dur_ms > 0 else duration
                    if voice_dur_s <= duration:
                        tempo_str = "anull"
                    else:
                        speed_ratio = min(voice_dur_s / max(duration, 1.0), 1.25)
                        tempo_str = build_atempo_filter(speed_ratio)
                    tempo_chain = f"{tempo_str}," if tempo_str != "anull" else ""

                    if should_mute_original:
                        # 🔇 TẮT HOÀN TOÀN ÂM THANH GỐC: [0:a] bị triệt tiêu 100%!
                        bgm_target = None
                        if bgm_mood and bgm_mood not in ("none", "mute"):
                            bgm_map = {
                                "suspense": "assets/bgm/dark_suspense.wav",
                                "epic": "assets/bgm/inspiring_epic.wav",
                                "chill": "assets/bgm/chill_ambient.mp3",
                                "cyber": "assets/bgm/scifi_cyber.wav",
                            }
                            cand_target = bgm_map.get(bgm_mood)
                            if cand_target and os.path.exists(cand_target):
                                bgm_target = cand_target

                        if bgm_target:
                            extra_input_args += ["-stream_loop", "-1", "-i", bgm_target]
                            # Input 0: video, Input 1: voiceover mp3, Input 2: bgm audio
                            filter_parts.append(
                                f"[1:a]{tempo_chain}volume=1.20,aresample=44100,aformat=channel_layouts=stereo,apad=whole_dur={duration:.3f},atrim=0:{duration:.3f}[voice];"
                                f"[2:a]lowpass=f=4200,equalizer=f=3200:t=q:w=1.2:g=-6,volume=0.035,aresample=44100,aformat=channel_layouts=stereo,atrim=0:{duration:.3f}[bgm];"
                                f"[voice][bgm]amix=inputs=2:duration=first:dropout_transition=1,"
                                f"afade=t=in:ss=0:d=0.25,afade=t=out:st={fade_out_st:.2f}:d=0.3[a]"
                            )
                        else:
                            filter_parts.append(
                                f"[1:a]{tempo_chain}volume=1.20,aresample=44100,aformat=channel_layouts=stereo,apad=whole_dur={duration:.3f},atrim=0:{duration:.3f},"
                                f"afade=t=in:ss=0:d=0.25,afade=t=out:st={fade_out_st:.2f}:d=0.3[a]"
                            )
                        has_audio = True
                    else:
                        # Chế độ Ducking: Giữ âm thanh gốc nhưng ép nhỏ khi có giọng nói
                        if has_audio:
                            filter_parts.append(
                                f"[1:a]{tempo_chain}volume=1.15,aresample=44100,aformat=channel_layouts=stereo,apad=whole_dur={duration:.3f},atrim=0:{duration:.3f},asplit=2[v_sc][v_mix];"
                                f"[0:a]aresample=44100,aformat=channel_layouts=stereo,atrim=0:{duration:.3f}[bg_clean];"
                                f"[bg_clean][v_sc]sidechaincompress=threshold=0.03:ratio=7:attack=20:release=350[bg_ducked];"
                                f"[bg_ducked][v_mix]amix=inputs=2:duration=first:dropout_transition=1,"
                                f"afade=t=in:ss=0:d=0.25,afade=t=out:st={fade_out_st:.2f}:d=0.3[a]"
                            )
                        else:
                            filter_parts.append(
                                f"[1:a]{tempo_chain}volume=1.15,aresample=44100,aformat=channel_layouts=stereo,apad=whole_dur={duration:.3f},atrim=0:{duration:.3f},"
                                f"afade=t=in:ss=0:d=0.25,afade=t=out:st={fade_out_st:.2f}:d=0.3[a]"
                            )
                        has_audio = True
            except Exception as e:
                logger.warning(f"Lỗi tạo AI Voiceover: {e}, fallback sang original audio")

        elif audio_mode == "mute":
            has_audio = False

        if not extra_input_args and has_audio and audio_mode != "mute":
            # Chế độ Original: Anti-Content ID (micro pitch shift 1.5%) + Envelope Fade
            filter_parts.append(
                f"[0:a]asetrate=44100*1.015,atempo=1/1.015,aresample=44100,"
                f"afade=t=in:ss=0:d=0.25,afade=t=out:st={fade_out_st:.2f}:d=0.3[a]"
            )

        filter_complex_str = ";".join(filter_parts)

        # 3. Chạy FFmpeg cắt chính xác và render (có Semaphore giới hạn tải đồng thời)
        ffmpeg_args = [
            "-ss", str(start_seconds),
            "-t", str(duration),
            "-i", source_video_path,
        ]
        if extra_input_args:
            ffmpeg_args += extra_input_args

        ffmpeg_args += [
            "-filter_complex", filter_complex_str,
            "-map", "[v]",
        ]
        if has_audio:
            ffmpeg_args += ["-map", "[a]"]
        else:
            ffmpeg_args += ["-an"]

        ffmpeg_args += [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            *BT709_COLOR_ARGS,
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            output_path_str,
        ]

        logger.info(f"[{device_name} ({client_ip})] Đang render Short 9:16 ({layout_mode}, {audio_mode}): {output_path_str}")
        with _render_semaphore:
            run_ffmpeg(ffmpeg_args, timeout=360)

        if not os.path.exists(output_path_str):
            raise RuntimeError(f"Render Short thất bại, không tìm thấy file: {output_path_str}")

        file_size_mb = round(os.path.getsize(output_path_str) / (1024 * 1024), 2)

        # Lưu file metadata định danh thiết bị tạo clip và thông tin Amazon
        meta_file = CLIPS_DIR / f"{Path(output_filename).stem}.meta.json"
        meta_data = {
            "filename": output_filename,
            "device_id": device_id,
            "device_name": device_name,
            "client_ip": client_ip,
            "created_at": time.time(),
            "duration": round(duration, 1),
            "file_size_mb": file_size_mb,
            "niche_id": niche_id,
            "layout_mode": layout_mode,
            "audio_mode": audio_mode,
            "mute_original": should_mute_original,
            "voice_id": voice_id or "",
            "voiceover_text": clean_voice_text if is_voiceover_mode else "",
            "title": clean_title,
            "subtitle_text": subtitle_text[:120],
            "description": description,
            "pinned_comment": pinned_comment,
            "amazon_product": amazon_prod,
            "source_url": source_url,
            "author": author,
            "mode": mode,
        }

        # 4. Kiểm định chất lượng Pre-Flight QA Gate tự động trước khi bàn giao
        qa_report = preflight_repurpose_qa(output_path_str, ass_file_path, meta_data)
        meta_data["qa_report"] = qa_report
        try:
            with open(meta_file, "w", encoding="utf-8") as mf:
                json.dump(meta_data, mf, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Lỗi lưu clip meta: {e}")

        # Xóa voice file tạm và các temp_files nếu có
        for tf in temp_files:
            try:
                if tf.exists():
                    tf.unlink()
            except Exception:
                pass
        if voice_tmp_path and os.path.exists(voice_tmp_path):
            try:
                os.remove(voice_tmp_path)
            except Exception:
                pass

        return {
            "success": True,
            "output_path": output_path_str,
            "relative_url": f"/media/repurposer/clips/{output_filename}",
            "filename": output_filename,
            "duration": round(duration, 1),
            "file_size_mb": file_size_mb,
            "niche_id": niche_id,
            "layout_mode": layout_mode,
            "audio_mode": audio_mode,
            "device_id": device_id,
            "device_name": device_name,
            "client_ip": client_ip,
            "title": clean_title,
            "description": description,
            "pinned_comment": pinned_comment,
            "amazon_product": amazon_prod,
            "qa_report": qa_report,
        }
