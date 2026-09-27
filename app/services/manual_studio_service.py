"""
ManualStudioService — Engine ghép video tùy chỉnh từ phân cảnh tự tải lên.

Quy trình:
  1. Nhận danh sách phân cảnh do người dùng tải lên (ảnh hoặc video + lời thoại).
  2. Tự động sinh giọng đọc AI (Edge-TTS) từng cảnh và đo chính xác thời lượng (duration).
  3. Tự động căn chỉnh từng media (ảnh -> Ken Burns 9:16; video -> scale/crop/loop 9:16).
  4. Ghép nối các phân cảnh với hiệu ứng chuyển cảnh (xfade).
  5. Ghép toàn bộ âm thanh lời thoại (narration).
  6. Lồng nhạc nền (BGM) với Audio Ducking thông minh + hiệu ứng âm thanh (SFX).
  7. Tự động sinh phụ đề Karaoke (ASS) và burn-in trực tiếp vào video 1080x1920.
  8. Xuất ra file MP4 chuẩn YouTube Shorts sẵn sàng xem, tải hoặc xuất bản.
"""
import asyncio
import os
import shutil
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Tuple
from pydantic import BaseModel, Field

import edge_tts
from config.settings import settings
from app.schemas.contracts import SceneContract
from app.utils.subtitle_helper import SubtitleHelper
from app.utils.ffmpeg_helper import (
    image_to_video,
    trim_video,
    concat_videos,
    concat_videos_with_xfade,
    mux_audio_video,
    mix_sfx_transitions,
    burn_subtitles,
    get_duration_ms,
    run_ffmpeg,
    list_available_sfx
)

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm", ".mkv"}


class ManualSceneItem(BaseModel):
    scene_id: int
    media_path: str
    narration: str = ""
    camera_motion: str = "zoom_in"  # zoom_in, zoom_out, pan_left, pan_right, pan_up, pan_down
    transition: str = "fade"        # fade, zoomin, wipeleft, slideright, dissolve
    sfx_cue: Optional[str] = "auto" # Tên SFX cụ thể, 'none' (tắt), hoặc 'auto'/'ai' (AI tự chọn từ kho)
    manual_duration_seconds: Optional[float] = None  # Nếu không có lời thoại thì dùng thời lượng này


class ManualStudioConfig(BaseModel):
    title: str = "Manual Video"
    voice: str = "en-US-AndrewNeural"
    voice_rate: str = "+0%"
    voice_pitch: str = "+0Hz"
    bgm_path: Optional[str] = None
    bgm_volume: float = 0.12
    use_ducking: bool = True
    subtitle_enabled: bool = True
    subtitle_font: str = "Arial"
    subtitle_font_size: int = 40
    subtitle_margin_v: int = 210
    transition_type: str = "fade"
    transition_duration: float = 0.25
    use_sfx: bool = True
    timing_mode: str = "auto"  # "auto" (AI tự căn nhịp) hoặc "timestamps" (căn theo mốc thời gian 00-10: ...)


class ManualStudioService:
    """Service xử lý ghép video từ phân cảnh người dùng tải lên."""

    def __init__(self):
        self.base_dir = Path(settings.BASE_DIR)

    def _is_image(self, file_path: str) -> bool:
        ext = Path(file_path).suffix.lower()
        return ext in IMAGE_EXTENSIONS

    def _is_video(self, file_path: str) -> bool:
        ext = Path(file_path).suffix.lower()
        return ext in VIDEO_EXTENSIONS

    async def _generate_scene_tts(
        self,
        text: str,
        voice: str,
        voice_rate: str,
        voice_pitch: str,
        output_audio_path: str
    ) -> float:
        """Sinh giọng đọc Edge-TTS cho 1 cảnh và trả về thời lượng thực tế (giây) với cơ chế tự động retry."""
        clean_text = text.strip()
        if not clean_text:
            return 0.0

        Path(output_audio_path).parent.mkdir(parents=True, exist_ok=True)
        
        last_error = None
        for attempt in range(1, 4):
            try:
                # Lần 1-2 dùng rate/pitch; nếu có lỗi lần 3 fallback bỏ pitch
                r = voice_rate if (voice_rate and voice_rate != "+0%") else None
                p = voice_pitch if (voice_pitch and voice_pitch != "+0Hz" and attempt < 3) else None

                kwargs = {"text": clean_text, "voice": voice}
                if r: kwargs["rate"] = r
                if p: kwargs["pitch"] = p

                communicate = edge_tts.Communicate(**kwargs)
                await communicate.save(output_audio_path)

                duration_ms = get_duration_ms(output_audio_path)
                if duration_ms > 0:
                    return round(duration_ms / 1000.0, 3)
            except Exception as e:
                last_error = e
                logger.warning(f"Edge-TTS attempt {attempt}/3 thất bại ({e}), đang thử lại...")
                await asyncio.sleep(0.8 * attempt)

        logger.error(f"Edge-TTS thất bại hoàn toàn sau 3 lần thử: {last_error}. Dùng fallback an toàn.")
        # Fallback an toàn: ước tính theo số từ và tạo file im lặng để pipeline không bị gián đoạn
        word_count = len(clean_text.split())
        dur = max(2.5, round(word_count / 3.2, 2))
        await self._create_silent_audio(dur, output_audio_path)
        return dur

    async def _create_silent_audio(self, duration_seconds: float, output_path: str) -> str:
        """Tạo file audio im lặng với thời lượng mong muốn khi cảnh không có lời thoại."""
        args = [
            "-f", "lavfi",
            "-i", f"anullsrc=r=44100:cl=stereo",
            "-t", str(duration_seconds),
            "-q:a", "9",
            "-acodec", "libmp3lame",
            output_path
        ]
        run_ffmpeg(args)
        return output_path

    def get_sfx_dir(self) -> Path:
        """Lấy thư mục SFX khả dụng (ưu tiên assets/sfx rồi đến assets/audio/sfx)."""
        p1 = self.base_dir / "assets" / "sfx"
        p2 = self.base_dir / "assets" / "audio" / "sfx"
        if p1.exists():
            return p1
        if p2.exists():
            return p2
        p1.mkdir(parents=True, exist_ok=True)
        return p1

    def get_available_sfx(self) -> List[Dict[str, str]]:
        """Quét danh sách hiệu ứng âm thanh SFX có sẵn trong hệ thống."""
        return list_available_sfx(self.get_sfx_dir())

    def _resolve_smart_sfx_cues(
        self,
        scenes: List[ManualSceneItem],
        available_sfx_list: List[Dict[str, str]]
    ) -> List[Optional[str]]:
        """
        Phân tích và quyết định hiệu ứng âm thanh (SFX) cho từng điểm chuyển cảnh.
        Hỗ trợ:
          - Người dùng chỉ định cụ thể (vd: 'whoosh', 'hit', 'boom', 'glitch'...)
          - Tắt âm ở cảnh đó ('none', 'off')
          - Tự động AI/Heuristic ('auto', 'ai', rỗng) phân tích ngữ cảnh lời thoại.
        Đảm bảo 100% an toàn: không bao giờ crash nếu file không tồn tại.
        """
        available_names = [s["id"].lower() for s in available_sfx_list]
        resolved_cues: List[Optional[str]] = []

        impact_keywords = [
            "bất ngờ", "sự thật", "bí mật", "chấn động", "nguy hiểm", "kinh hoàng", "sợ hãi",
            "nổ", "chết", "tiêu diệt", "shock", "danger", "secret", "suddenly", "explode",
            "death", "climax", "warning", "cảnh báo", "khủng khiếp", "không thể tin"
        ]
        tech_keywords = [
            "nhìn này", "xem", "chụp", "ảnh", "bằng chứng", "máy bay", "máy tính", "công nghệ",
            "màn hình", "camera", "photo", "screen", "system", "hệ thống", "dữ liệu", "khoa học"
        ]
        swish_keywords = [
            "tiếp theo", "ngay sau đó", "nhanh chóng", "và rồi", "tiếp tục", "chuyển sang",
            "meanwhile", "next", "then", "fast", "speed", "quickly", "lướt qua"
        ]

        # transitions = scenes[1:] (chuyển cảnh từ cảnh 1 sang cảnh 2, 2 sang 3...)
        for i, sc in enumerate(scenes[1:], start=2):
            raw_cue = (sc.sfx_cue or "").strip().lower()

            # 1. Người dùng chủ động tắt
            if raw_cue in ("none", "off", "mute", "disable", "skip"):
                resolved_cues.append("none")
                continue

            # 2. Người dùng chỉ định tên SFX cụ thể
            if raw_cue and raw_cue not in ("auto", "ai", "smart"):
                resolved_cues.append(raw_cue)
                continue

            # 3. Chế độ AI/Tự động phân tích từ kho
            if not available_names:
                resolved_cues.append("whoosh")
                continue

            text_lower = sc.narration.lower()
            chosen = None

            # Phân tích kịch tính / chấn động
            if any(k in text_lower for k in impact_keywords):
                for cand in available_names:
                    if any(t in cand for t in ["boom", "hit", "impact", "thud", "drop", "blast"]):
                        chosen = cand
                        break

            # Phân tích công nghệ / thị giác
            if not chosen and any(k in text_lower for k in tech_keywords):
                for cand in available_names:
                    if any(t in cand for t in ["click", "shutter", "glitch", "snap", "beep"]):
                        chosen = cand
                        break

            # Phân tích nhịp lướt nhanh
            if not chosen and any(k in text_lower for k in swish_keywords):
                for cand in available_names:
                    if any(t in cand for t in ["whoosh", "swoosh", "swish", "swipe"]):
                        chosen = cand
                        break

            # Fallback ưu tiên whoosh -> file đầu tiên
            if not chosen:
                whoosh_cand = next((s for s in available_names if "whoosh" in s or "swoosh" in s), None)
                chosen = whoosh_cand or available_names[0]

            logger.info(f"[ManualStudio SFX] Cảnh #{i}: AI tự động chọn SFX '{chosen}' theo ngữ cảnh: '{sc.narration[:30]}...'")
            resolved_cues.append(chosen)

        return resolved_cues

    async def _concat_audios(self, audio_paths: List[str], output_path: str) -> str:
        """Ghép N file audio narration lại thành 1 file MP3 duy nhất."""
        if len(audio_paths) == 1:
            shutil.copy2(audio_paths[0], output_path)
            return output_path

        list_file = str(Path(output_path).parent / "audio_concat_list.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for p in audio_paths:
                safe_path = Path(p).resolve().as_posix().replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        args = [
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            output_path
        ]
        run_ffmpeg(args)
        return output_path

    async def render_manual_video(
        self,
        job_id: int,
        scenes: List[ManualSceneItem],
        config: ManualStudioConfig,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> str:
        """
        Quy trình xử lý hoàn chỉnh biến danh sách phân cảnh thành video Shorts dọc 9:16.
        """
        def update_progress(percent: int, message: str):
            logger.info(f"[ManualStudio #{job_id}] [{percent}%] {message}")
            if progress_callback:
                try:
                    progress_callback(percent, message)
                except Exception as e:
                    logger.warning(f"Lỗi progress_callback: {e}")

        update_progress(5, "Đang chuẩn bị thư mục làm việc...")
        temp_dir = self.base_dir / "storage" / "temp" / f"manual_{job_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        outputs_dir = self.base_dir / "storage" / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)

        final_output_path = str(outputs_dir / f"video_{job_id}.mp4")

        # ── BƯỚC 1: XÁC MINH CÁC ASSET PHÂN CẢNH ──
        update_progress(10, "Đang kiểm tra và chuẩn hóa các tệp tin tải lên...")
        valid_scenes: List[ManualSceneItem] = []
        for idx, sc in enumerate(scenes, start=1):
            if not Path(sc.media_path).exists():
                raise FileNotFoundError(f"Cảnh #{idx}: Không tìm thấy file media tại '{sc.media_path}'")
            valid_scenes.append(sc)

        if not valid_scenes:
            raise ValueError("Không có phân cảnh hợp lệ nào để xử lý")

        # ── BƯỚC 2: SINH GIỌNG ĐỌC TTS VÀ ĐO THỜI LƯỢNG ──
        update_progress(20, f"Đang sinh giọng đọc AI ({config.voice}) cho từng cảnh...")
        scene_durations: List[float] = []
        scene_audio_paths: List[str] = []
        scene_contracts: List[SceneContract] = []

        for i, sc in enumerate(valid_scenes, start=1):
            sc_audio = str(temp_dir / f"scene_{i:03d}_voice.mp3")
            narration_text = sc.narration.strip()

            if narration_text:
                update_progress(20 + int(i / len(valid_scenes) * 15), f"Đang tạo giọng đọc cảnh {i}/{len(valid_scenes)}...")
                target_dur = sc.manual_duration_seconds
                dur = await self._generate_scene_tts(
                    text=narration_text,
                    voice=config.voice,
                    voice_rate=config.voice_rate,
                    voice_pitch=config.voice_pitch,
                    output_audio_path=sc_audio
                )

                # Nếu có mốc thời gian chỉ định (target_dur)
                if target_dur and target_dur >= 1.5:
                    if dur > target_dur:
                        # Tự động tăng tốc đọc để khớp vào khung thời gian của cảnh
                        speedup_pct = min(60, int(((dur / target_dur) - 1.0) * 100 + 12))
                        adapted_rate = f"+{speedup_pct}%"
                        logger.info(f"[ManualStudio #{job_id}] Cảnh #{i}: Thoại dài ({dur:.1f}s > {target_dur:.1f}s) -> AI tự tăng tốc {adapted_rate}")
                        dur = await self._generate_scene_tts(
                            text=narration_text,
                            voice=config.voice,
                            voice_rate=adapted_rate,
                            voice_pitch=config.voice_pitch,
                            output_audio_path=sc_audio
                        )
                    # Nếu giọng đọc ngắn hơn khung thời gian của cảnh -> đệm khoảng lặng (pad) để audio khớp với video cảnh
                    if target_dur > dur + 0.05:
                        pad_sec = round(target_dur - dur, 3)
                        pad_path = str(temp_dir / f"scene_{i:03d}_pad.mp3")
                        await self._create_silent_audio(pad_sec, pad_path)
                        padded_sc_audio = str(temp_dir / f"scene_{i:03d}_padded_voice.mp3")
                        await self._concat_audios([sc_audio, pad_path], padded_sc_audio)
                        sc_audio = padded_sc_audio
                        dur = target_dur
            else:
                # Không có thoại -> dùng manual_duration_seconds hoặc mặc định 3.5s
                dur = sc.manual_duration_seconds or 3.5
                await self._create_silent_audio(dur, sc_audio)

            scene_durations.append(dur)
            scene_audio_paths.append(sc_audio)

            # Tạo SceneContract để hỗ trợ SubtitleHelper
            scene_contracts.append(SceneContract(
                scene_id=i,
                narration=narration_text,
                visual_keyword="shorts",
                visual_prompt="",
                camera_motion=sc.camera_motion or "zoom_in",
                sfx_cue=sc.sfx_cue or "whoosh",
                audio_path=sc_audio,
                audio_duration_ms=int(dur * 1000),
                estimated_seconds=dur
            ))

        # ── BƯỚC 3: CĂN CHỈNH & CHUYỂN HÓA MEDIA SANG 9:16 ──
        update_progress(40, "Đang căn chỉnh tỷ lệ 9:16 và chuyển động điện ảnh...")
        rendered_clips: List[str] = []

        td = config.transition_duration if len(valid_scenes) > 1 else 0.0
        tail_buffer = 0.6
        adjusted_durations = [
            d + (td if idx < len(valid_scenes) - 1 else tail_buffer)
            for idx, d in enumerate(scene_durations)
        ]

        for i, sc in enumerate(valid_scenes, start=1):
            dur = adjusted_durations[i - 1]
            clip_path = str(temp_dir / f"scene_{i:03d}_clip.mp4")
            update_progress(
                40 + int(i / len(valid_scenes) * 25),
                f"Đang xử lý hình ảnh/video cảnh {i}/{len(valid_scenes)} ({dur:.1f}s)..."
            )

            if self._is_image(sc.media_path):
                # Ảnh tĩnh -> Ken Burns Motion
                image_to_video(
                    image_path=sc.media_path,
                    duration_seconds=dur,
                    output_path=clip_path,
                    camera_motion=sc.camera_motion or "zoom_in",
                    is_hook_scene=(i == 1)
                )
            else:
                # Video -> Cắt hoặc lặp theo đúng thời lượng giọng đọc, scale & crop 1080x1920
                trim_video(
                    input_path=sc.media_path,
                    duration_seconds=dur,
                    output_path=clip_path
                )

            rendered_clips.append(clip_path)

        # ── BƯỚC 4: GHÉP NỐI CÁC PHÂN CẢNH (CONCAT / XFADE) ──
        update_progress(68, "Đang ghép nối các phân cảnh với hiệu ứng chuyển cảnh...")
        raw_concat_video = str(temp_dir / "raw_video.mp4")

        if len(rendered_clips) == 1:
            shutil.copy2(rendered_clips[0], raw_concat_video)
        else:
            try:
                concat_videos_with_xfade(
                    video_paths=rendered_clips,
                    output_path=raw_concat_video,
                    transition=config.transition_type,
                    transition_duration=config.transition_duration,
                    scene_durations_seconds=adjusted_durations
                )
            except Exception as e:
                logger.warning(f"Lỗi xfade concat, fallback sang concat thông thường: {e}")
                concat_videos(rendered_clips, raw_concat_video)

        # ── BƯỚC 5: GHÉP NỐI AUDIO LỜI THOẠI ──
        update_progress(75, "Đang đồng bộ hóa toàn bộ âm thanh lời thoại...")
        full_audio_path = str(temp_dir / "full_narration.mp3")
        await self._concat_audios(scene_audio_paths, full_audio_path)

        # ── BƯỚC 6: LỒNG NHẠC NỀN & SFX CHUYỂN CẢNH ──
        update_progress(82, "Đang lồng nhạc nền (BGM) và hiệu ứng âm thanh (SFX)...")
        muxed_video = str(temp_dir / "muxed_video.mp4")
        mux_audio_video(
            video_path=raw_concat_video,
            audio_path=full_audio_path,
            output_path=muxed_video,
            bgm_path=config.bgm_path,
            bgm_volume=config.bgm_volume,
            use_ducking=config.use_ducking
        )

        current_video_stage = muxed_video

        # Mix SFX nếu bật
        if config.use_sfx:
            sfx_dir_path = self.get_sfx_dir()
            available_sfx = self.get_available_sfx()
            if sfx_dir_path.exists() and available_sfx:
                # Tính timestamps bắt đầu từng cảnh
                timestamps = [0.0]
                cum = 0.0
                for d in scene_durations[:-1]:
                    cum += d
                    timestamps.append(round(cum, 2))

                # Xác định SFX cho mở đầu (Scene 1)
                first_cue = (valid_scenes[0].sfx_cue or "").strip().lower()
                hit_sfx_name = "hit"
                if first_cue in ("none", "off", "mute", "disable", "skip"):
                    hit_sfx_name = "none"
                elif first_cue and first_cue not in ("auto", "ai", "smart"):
                    hit_sfx_name = first_cue

                # Phân tích thông minh các điểm chuyển cảnh tiếp theo
                transition_cues = self._resolve_smart_sfx_cues(valid_scenes, available_sfx)

                sfx_output = str(temp_dir / "sfx_video.mp4")
                try:
                    current_video_stage = mix_sfx_transitions(
                        video_path=muxed_video,
                        output_path=sfx_output,
                        scene_timestamps_seconds=timestamps,
                        sfx_dir=str(sfx_dir_path),
                        default_sfx="whoosh",
                        sfx_cues=transition_cues,
                        hit_sfx=hit_sfx_name
                    )
                except Exception as e:
                    logger.warning(f"Lỗi mix SFX: {e}, an toàn bỏ qua SFX và giữ nguyên video gốc")
                    current_video_stage = muxed_video
            else:
                logger.info("[ManualStudio] Không tìm thấy SFX trong kho hoặc thư mục rỗng, bỏ qua SFX an toàn.")

        # ── BƯỚC 7: TẠO PHỤ ĐỀ KARAOKE & HARD-SUB ──
        if config.subtitle_enabled and any(sc.narration.strip() for sc in valid_scenes):
            update_progress(90, "Đang tạo và gắn phụ đề Karaoke phong cách Shorts...")
            ass_path = str(temp_dir / "subtitles.ass")
            SubtitleHelper.save_ass_file(
                scenes=scene_contracts,
                output_path=ass_path,
                fontname=config.subtitle_font,
                fontsize=config.subtitle_font_size,
                margin_v=config.subtitle_margin_v
            )

            try:
                burn_subtitles(
                    video_path=current_video_stage,
                    srt_path=ass_path,
                    output_path=final_output_path,
                    font_name=config.subtitle_font,
                    font_size=config.subtitle_font_size,
                    margin_v=config.subtitle_margin_v
                )
            except Exception as e:
                logger.error(f"Lỗi burn phụ đề: {e}, sao chép video không sub làm sản phẩm")
                shutil.copy2(current_video_stage, final_output_path)
        else:
            update_progress(92, "Bỏ qua tạo phụ đề...")
            shutil.copy2(current_video_stage, final_output_path)

        update_progress(100, f"✅ Xuất video thành công: video_{job_id}.mp4")
        return final_output_path

    def _parse_timestamp_segments(self, text: str) -> List[Tuple[float, float, str]]:
        """
        Phân tích kịch bản theo từng mốc thời gian thủ công, ví dụ:
          00-10: Bạn có tin dưới đáy đại dương...
          10:25: Nơi áp lực nước kinh hoàng...
          00:25 - 00:45: Hôm nay chúng ta sẽ cùng khám phá...
          [00-10] Nội dung...
        Trả về danh sách (start_seconds, end_seconds, narration_text).
        """
        import re

        regex_pattern = re.compile(
            r'^\s*\[?\s*(\d{1,2}(?::\d{1,2})?)\s*(?:-|đến|to|->|:)\s*(\d{1,2}(?::\d{1,2})?)\s*\]?\s*[:\-]?\s*(.*)$',
            re.IGNORECASE
        )

        def _to_sec(val: str) -> float:
            v = val.strip()
            if ":" in v:
                p = v.split(":")
                if len(p) == 2:
                    return float(p[0]) * 60 + float(p[1])
                elif len(p) == 3:
                    return float(p[0]) * 3600 + float(p[1]) * 60 + float(p[2])
            return float(v)

        raw_lines = [line.strip() for line in text.split("\n") if line.strip()]
        if not raw_lines:
            return []

        parsed: List[Dict[str, Any]] = []
        for line in raw_lines:
            m = regex_pattern.match(line)
            if m:
                t1, t2, content = m.groups()
                try:
                    s1 = _to_sec(t1)
                    s2 = _to_sec(t2)
                    if s2 <= s1:
                        s2 = s1 + 3.0
                    parsed.append({"start": s1, "end": s2, "text": content.strip()})
                except Exception:
                    if parsed:
                        parsed[-1]["text"] += " " + line
            else:
                if parsed:
                    # Ghép các dòng kế tiếp không có mốc vào đoạn trước đó
                    parsed[-1]["text"] += " " + line

        parsed.sort(key=lambda x: x["start"])
        return [(p["start"], p["end"], p["text"]) for p in parsed]

    def parse_multi_scene_script(self, script_text: str, num_scenes: int, timing_mode: str = "auto") -> List[Dict[str, Any]]:
        """
        Phân tách nội dung kịch bản từ 1 ô duy nhất cho N phân cảnh tải lên.
        Hỗ trợ:
          1. Mốc thời gian (00-10: ..., 10:25: ...)
          2. Nhãn cảnh (Cảnh 1: ..., Cảnh 2: ..., Đoạn 1: ..., 1: ...)
          3. Đoạn văn tự nhiên (Paragraphs)
          4. Chia đều theo câu văn (Sentences)
        """
        import re
        raw_text = (script_text or "").strip()
        if not raw_text or num_scenes <= 0:
            return [{"narration": "", "duration": None} for _ in range(max(1, num_scenes))]

        ts_regex = re.compile(
            r'^\s*\[?\s*(\d{1,2}(?::\d{1,2})?)\s*(?:-|đến|to|->|:)\s*(\d{1,2}(?::\d{1,2})?)\s*\]?\s*[:\-]?\s*(.*)$',
            re.IGNORECASE
        )
        scene_label_regex = re.compile(
            r'^\s*(?:cảnh|phân cảnh|scene|đoạn|bước|part)?\s*#?(\d+)[:.\-]\s*(.*)$',
            re.IGNORECASE
        )

        def to_sec(v: str) -> float:
            v = v.strip()
            parts = v.split(":")
            if len(parts) == 2:
                return float(parts[0]) * 60 + float(parts[1])
            if len(parts) == 3:
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
            return float(v)

        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

        # ── 1. Thử phân tích theo mốc thời gian 00-10: ──
        ts_matches = []
        current_ts = None
        for line in lines:
            m = ts_regex.match(line)
            if m:
                t1, t2, content = m.groups()
                try:
                    s1 = to_sec(t1)
                    s2 = to_sec(t2)
                    dur = max(2.0, s2 - s1) if s2 > s1 else None
                    current_ts = {"text": content.strip(), "duration": dur}
                    ts_matches.append(current_ts)
                except Exception:
                    if current_ts:
                        current_ts["text"] += " " + line
            else:
                if current_ts:
                    current_ts["text"] += " " + line

        if ts_matches:
            results = []
            for i in range(num_scenes):
                if i < len(ts_matches):
                    results.append({"narration": ts_matches[i]["text"].strip(), "duration": ts_matches[i]["duration"]})
                else:
                    results.append({"narration": "", "duration": None})
            if len(ts_matches) > num_scenes:
                extra = " ".join(m["text"].strip() for m in ts_matches[num_scenes:])
                results[-1]["narration"] = (results[-1]["narration"] + " " + extra).strip()
            return results

        # ── 2. Thử phân tích theo nhãn Cảnh 1:, Cảnh 2:, ... ──
        scene_matches = []
        current_scene = None
        for line in lines:
            m = scene_label_regex.match(line)
            if m:
                idx_str, content = m.groups()
                current_scene = {"idx": int(idx_str), "text": content.strip()}
                scene_matches.append(current_scene)
            else:
                if current_scene:
                    current_scene["text"] += " " + line

        if scene_matches and len(scene_matches) >= 2:
            results = []
            for i in range(num_scenes):
                if i < len(scene_matches):
                    results.append({"narration": scene_matches[i]["text"].strip(), "duration": None})
                else:
                    results.append({"narration": "", "duration": None})
            if len(scene_matches) > num_scenes:
                extra = " ".join(s["text"].strip() for s in scene_matches[num_scenes:])
                results[-1]["narration"] = (results[-1]["narration"] + " " + extra).strip()
            return results

        # ── 3. Thử phân tích theo đoạn văn bản (Paragraphs) ──
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', raw_text) if p.strip()]
        if len(paragraphs) == num_scenes:
            return [{"narration": p, "duration": None} for p in paragraphs]

        # ── 4. Phân chia đều theo câu văn (Sentences) ──
        clean_all = " ".join(lines)
        raw_sentences = re.split(r'(?<=[.!?…])\s+', clean_all)
        sentences = [s.strip() for s in raw_sentences if s.strip()]

        if not sentences:
            return [{"narration": raw_text, "duration": None}] + [{"narration": "", "duration": None} for _ in range(num_scenes - 1)]

        results = []
        if len(sentences) >= num_scenes:
            k, m = divmod(len(sentences), num_scenes)
            start = 0
            for i in range(num_scenes):
                chunk_size = k + (1 if i < m else 0)
                chunk = " ".join(sentences[start:start + chunk_size])
                results.append({"narration": chunk, "duration": None})
                start += chunk_size
        else:
            for i in range(num_scenes):
                if i < len(sentences):
                    results.append({"narration": sentences[i], "duration": None})
                else:
                    results.append({"narration": "", "duration": None})

        return results

    def _parse_script_segments(self, text: str) -> List[str]:
        """Tách kịch bản thành các phân đoạn tự nhiên để căn phụ đề và chèn SFX."""
        import re
        raw_lines = [line.strip() for line in text.split("\n") if line.strip()]
        if not raw_lines:
            return [""]

        # Regex loại bỏ mốc thời gian nếu có để tránh AI đọc số ra loa
        ts_strip_pattern = re.compile(
            r'^\s*\[?\s*\d{1,2}(?::\d{1,2})?\s*(?:-|đến|to|->|:)\s*\d{1,2}(?::\d{1,2})?\s*\]?\s*[:\-]?\s*',
            re.IGNORECASE
        )

        segments = []
        for line in raw_lines:
            cleaned = ts_strip_pattern.sub('', line).strip()
            cleaned = re.sub(r'^(cảnh|đoạn|scene|phân cảnh)?\s*\d+[\s:.-]*', '', cleaned, flags=re.IGNORECASE).strip()
            if not cleaned:
                cleaned = line
            segments.append(cleaned)

        # Nếu chỉ có 1 dòng dài, tách theo dấu ngắt câu
        if len(segments) == 1 and len(segments[0]) > 120:
            parts = [p.strip() for p in re.split(r'(?<=[.!?])\s+', segments[0]) if p.strip()]
            if len(parts) > 1:
                return parts

        return segments

    async def render_single_video_studio(
        self,
        job_id: int,
        video_path: str,
        script_text: str,
        config: ManualStudioConfig,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> str:
        """
        Quy trình một chạm cho video đã dựng sẵn (từ Google Flow, CapCut, v.v.):
          1. Nhận 1 file video hoàn chỉnh + lời thoại kịch bản.
          2. Hỗ trợ 2 chế độ:
             - Tự động (Auto): AI tự phân đoạn, đọc tự nhiên, bảo toàn 100% thời lượng video gốc.
             - Theo mốc thời gian (Timestamps): 00-10: ..., 10:25: ... AI tự căn nhịp, tăng tốc nếu lời dài, đệm silence nếu lời ngắn.
          3. Căn chỉnh video (scale, crop 9:16) giữ trọn vẹn thời lượng video tải lên.
          4. Lồng nhạc nền (BGM) + Audio Ducking + SFX chuyển ý tại các mốc.
          5. Tự động sinh phụ đề Karaoke Shorts (ASS) chuẩn xác và burn-in vào video.
          6. Xuất ra file MP4 hoàn chỉnh.
        """
        def update_progress(percent: int, message: str):
            logger.info(f"[SingleVideoStudio #{job_id}] [{percent}%] {message}")
            if progress_callback:
                try:
                    progress_callback(percent, message)
                except Exception as e:
                    logger.warning(f"Lỗi progress_callback: {e}")

        update_progress(5, "Đang chuẩn bị thư mục làm việc...")
        temp_dir = self.base_dir / "storage" / "temp" / f"manual_{job_id}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        outputs_dir = self.base_dir / "storage" / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)

        final_output_path = str(outputs_dir / f"video_{job_id}.mp4")

        if not Path(video_path).exists():
            raise FileNotFoundError(f"Không tìm thấy file video tại '{video_path}'")

        # ── BƯỚC 0: ĐO THỜI LƯỢNG VIDEO GỐC ĐƯỢC TẢI LÊN ──
        uploaded_video_ms = get_duration_ms(video_path)
        uploaded_video_seconds = round(uploaded_video_ms / 1000.0, 3) if uploaded_video_ms > 0 else 0.0
        logger.info(f"[SingleVideoStudio #{job_id}] Video gốc tải lên: {uploaded_video_seconds:.2f}s ({uploaded_video_ms}ms)")

        # ── BƯỚC 1: XÁC ĐỊNH CHẾ ĐỘ CĂN CHỈNH (AUTO HOẶC TIMESTAMPS) ──
        timing_mode = getattr(config, "timing_mode", "auto").lower()
        timestamp_segments = []
        if timing_mode == "timestamps":
            timestamp_segments = self._parse_timestamp_segments(script_text)
            if not timestamp_segments:
                logger.warning(f"[SingleVideoStudio #{job_id}] Chế độ timestamps nhưng không tìm thấy mốc dạng 00-10:, tự động chuyển sang chế độ Auto")
                timing_mode = "auto"

        # ── BƯỚC 2: SINH GIỌNG ĐỌC & TẠO TIMELINE ÂM THANH ──
        update_progress(20, f"Đang sinh giọng đọc AI ({config.voice})...")
        scene_contracts: List[SceneContract] = []
        sfx_timestamps: List[float] = [0.0]

        if timing_mode == "timestamps" and timestamp_segments:
            logger.info(f"[SingleVideoStudio #{job_id}] Đang căn chỉnh theo {len(timestamp_segments)} mốc thời gian thủ công...")
            timeline_audio_paths: List[str] = []
            current_timeline = 0.0

            for idx, (start_sec, end_sec, text_content) in enumerate(timestamp_segments, start=1):
                clean_text = text_content.strip()
                window_duration = max(1.0, round(end_sec - start_sec, 3))

                # 1. Chèn khoảng im lặng nếu có gap trước mốc này
                if start_sec > current_timeline:
                    gap_sec = round(start_sec - current_timeline, 3)
                    if gap_sec >= 0.05:
                        gap_audio = str(temp_dir / f"gap_{idx:03d}.mp3")
                        await self._create_silent_audio(gap_sec, gap_audio)
                        timeline_audio_paths.append(gap_audio)
                        current_timeline += gap_sec

                # 2. Sinh giọng đọc cho khung giờ này
                seg_audio = str(temp_dir / f"slot_{idx:03d}_voice.mp3")
                if clean_text:
                    tts_dur = await self._generate_scene_tts(
                        text=clean_text,
                        voice=config.voice,
                        voice_rate=config.voice_rate,
                        voice_pitch=config.voice_pitch,
                        output_audio_path=seg_audio
                    )

                    # 3. AI Tự căn chỉnh nhịp: Nếu giọng đọc vượt quá khung giờ (tts_dur > window_duration)
                    # thì tự động tăng tốc rate để đọc vừa vặn vào khung
                    if tts_dur > window_duration:
                        speedup_pct = min(60, int(((tts_dur / window_duration) - 1.0) * 100 + 12))
                        adapted_rate = f"+{speedup_pct}%"
                        logger.info(
                            f"[Timestamps #{job_id}] Khung {start_sec:.1f}-{end_sec:.1f}s: Lời thoại dài ({tts_dur:.1f}s > {window_duration:.1f}s). "
                            f"AI tự động tăng tốc đọc lên {adapted_rate} để vừa vặn khung giờ!"
                        )
                        tts_dur = await self._generate_scene_tts(
                            text=clean_text,
                            voice=config.voice,
                            voice_rate=adapted_rate,
                            voice_pitch=config.voice_pitch,
                            output_audio_path=seg_audio
                        )
                else:
                    tts_dur = 0.5
                    await self._create_silent_audio(tts_dur, seg_audio)

                timeline_audio_paths.append(seg_audio)
                current_timeline += tts_dur

                # Thêm vào hợp đồng phân cảnh cho phụ đề Karaoke ASS
                scene_contracts.append(SceneContract(
                    scene_id=idx,
                    narration=clean_text or " ",
                    visual_keyword="shorts",
                    visual_prompt="",
                    camera_motion="zoom_in",
                    sfx_cue="auto",
                    audio_path=seg_audio,
                    audio_duration_ms=int(tts_dur * 1000),
                    estimated_seconds=tts_dur,
                    start_offset_ms=int(start_sec * 1000)
                ))

                if idx > 1:
                    sfx_timestamps.append(round(start_sec, 2))

                # 4. Nếu giọng đọc ngắn hơn khung giờ, đệm khoảng im lặng để đạt đủ window_duration
                if current_timeline < (start_sec + window_duration):
                    pad_sec = round((start_sec + window_duration) - current_timeline, 3)
                    if pad_sec >= 0.05:
                        pad_audio = str(temp_dir / f"pad_{idx:03d}.mp3")
                        await self._create_silent_audio(pad_sec, pad_audio)
                        timeline_audio_paths.append(pad_audio)
                        current_timeline += pad_sec

            # 5. Đệm phần đuôi video nếu video gốc dài hơn kịch bản (khắc phục triệt để lỗi 49s -> 36s)
            target_duration = max(uploaded_video_seconds, current_timeline)
            if current_timeline < target_duration:
                tail_gap = round(target_duration - current_timeline, 3)
                if tail_gap >= 0.05:
                    logger.info(f"[SingleVideoStudio #{job_id}] Video gốc {uploaded_video_seconds:.2f}s > audio {current_timeline:.2f}s. Đệm {tail_gap:.2f}s im lặng cuối để giữ trọn video 100%!")
                    tail_audio = str(temp_dir / "tail_silence.mp3")
                    await self._create_silent_audio(tail_gap, tail_audio)
                    timeline_audio_paths.append(tail_audio)
                    current_timeline += tail_gap

            update_progress(45, "Đang ghép nối toàn bộ âm thanh giọng đọc theo mốc...")
            full_audio_path = str(temp_dir / "full_narration.mp3")
            await self._concat_audios(timeline_audio_paths, full_audio_path)
            total_audio_ms = get_duration_ms(full_audio_path)
            total_audio_seconds = round(total_audio_ms / 1000.0, 3)
            logger.info(f"[SingleVideoStudio #{job_id}] Tổng thời lượng audio timeline mốc: {total_audio_seconds:.2f}s")

        else:
            # ── CHẾ ĐỘ 1: TỰ ĐỘNG (AI AUTO) ──
            segments = self._parse_script_segments(script_text)
            logger.info(f"[SingleVideoStudio #{job_id}] Chế độ Auto: kịch bản có {len(segments)} phân đoạn")
            segment_durations: List[float] = []
            segment_audio_paths: List[str] = []

            for i, seg_text in enumerate(segments, start=1):
                seg_audio = str(temp_dir / f"seg_{i:03d}_voice.mp3")
                if seg_text.strip():
                    dur = await self._generate_scene_tts(
                        text=seg_text,
                        voice=config.voice,
                        voice_rate=config.voice_rate,
                        voice_pitch=config.voice_pitch,
                        output_audio_path=seg_audio
                    )
                else:
                    dur = 3.0
                    await self._create_silent_audio(dur, seg_audio)

                segment_durations.append(dur)
                segment_audio_paths.append(seg_audio)

                scene_contracts.append(SceneContract(
                    scene_id=i,
                    narration=seg_text,
                    visual_keyword="shorts",
                    visual_prompt="",
                    camera_motion="zoom_in",
                    sfx_cue="auto",
                    audio_path=seg_audio,
                    audio_duration_ms=int(dur * 1000),
                    estimated_seconds=dur
                ))

            # Ghép audio lời thoại
            update_progress(45, "Đang ghép nối toàn bộ âm thanh giọng đọc...")
            full_audio_path = str(temp_dir / "full_narration.mp3")
            await self._concat_audios(segment_audio_paths, full_audio_path)
            total_audio_ms = get_duration_ms(full_audio_path)
            total_audio_seconds = round(total_audio_ms / 1000.0, 3)

            # Khắc phục lỗi video bị co ngắn: nếu video gốc dài hơn lời thoại, đệm im lặng phía sau!
            target_duration = max(uploaded_video_seconds, total_audio_seconds)
            if uploaded_video_seconds > total_audio_seconds:
                tail_gap = round(uploaded_video_seconds - total_audio_seconds, 3)
                if tail_gap >= 0.05:
                    logger.info(f"[SingleVideoStudio #{job_id}] Video gốc ({uploaded_video_seconds:.2f}s) dài hơn giọng đọc ({total_audio_seconds:.2f}s). Đệm thêm {tail_gap:.2f}s im lặng cuối để giữ trọn vẹn 100% video!")
                    tail_audio = str(temp_dir / "tail_silence.mp3")
                    await self._create_silent_audio(tail_gap, tail_audio)
                    padded_audio_path = str(temp_dir / "full_narration_padded.mp3")
                    await self._concat_audios([full_audio_path, tail_audio], padded_audio_path)
                    full_audio_path = padded_audio_path
                    total_audio_seconds = target_duration

            # Tính sfx timestamps cho auto
            cum = 0.0
            for d in segment_durations[:-1]:
                cum += d
                sfx_timestamps.append(round(cum, 2))

        # ── BƯỚC 3: CĂN CHỈNH VIDEO GIỮ NGUYÊN 100% THỜI LƯỢNG ──
        target_duration = max(uploaded_video_seconds, total_audio_seconds)
        update_progress(60, f"Đang căn chỉnh video giữ trọn vẹn thời lượng ({target_duration:.1f}s)...")
        aligned_video_path = str(temp_dir / "aligned_video.mp4")

        # Chuẩn hóa video: giữ nguyên 100% độ dài video gốc (loop nếu audio dài hơn video, không bao giờ cắt ngắn video gốc)
        trim_video(
            input_path=video_path,
            duration_seconds=target_duration,
            output_path=aligned_video_path,
            width=1080,
            height=1920
        )

        # ── BƯỚC 4: LỒNG NHẠC NỀN & SFX CHUYỂN Ý ──
        update_progress(75, "Đang lồng nhạc nền và hiệu ứng âm thanh...")
        muxed_video = str(temp_dir / "muxed_video.mp4")
        mux_audio_video(
            video_path=aligned_video_path,
            audio_path=full_audio_path,
            output_path=muxed_video,
            bgm_path=config.bgm_path,
            bgm_volume=config.bgm_volume,
            use_ducking=config.use_ducking
        )

        current_video_stage = muxed_video

        # Mix SFX nếu bật
        if config.use_sfx:
            sfx_dir_path = self.get_sfx_dir()
            available_sfx = self.get_available_sfx()
            if sfx_dir_path.exists() and available_sfx:
                pseudo_scenes = [
                    ManualSceneItem(
                        scene_id=idx,
                        media_path=video_path,
                        narration=sc.narration,
                        sfx_cue="auto"
                    )
                    for idx, sc in enumerate(scene_contracts, start=1)
                ]
                transition_cues = self._resolve_smart_sfx_cues(pseudo_scenes, available_sfx)

                sfx_output = str(temp_dir / "sfx_video.mp4")
                try:
                    current_video_stage = mix_sfx_transitions(
                        video_path=muxed_video,
                        output_path=sfx_output,
                        scene_timestamps_seconds=sfx_timestamps,
                        sfx_dir=str(sfx_dir_path),
                        default_sfx="whoosh",
                        sfx_cues=transition_cues,
                        hit_sfx="hit"
                    )
                except Exception as e:
                    logger.warning(f"Lỗi mix SFX: {e}, an toàn giữ nguyên video")
                    current_video_stage = muxed_video

        # ── BƯỚC 5: TẠO PHỤ ĐỀ KARAOKE & HARD-SUB ──
        has_sub_text = any(sc.narration.strip() for sc in scene_contracts)
        if config.subtitle_enabled and has_sub_text:
            update_progress(88, "Đang tạo phụ đề Karaoke Shorts (ASS)...")
            ass_path = str(temp_dir / "subtitles.ass")
            SubtitleHelper.save_ass_file(
                scenes=scene_contracts,
                output_path=ass_path,
                fontname=config.subtitle_font,
                fontsize=config.subtitle_font_size,
                margin_v=config.subtitle_margin_v
            )

            try:
                burn_subtitles(
                    video_path=current_video_stage,
                    srt_path=ass_path,
                    output_path=final_output_path,
                    font_name=config.subtitle_font,
                    font_size=config.subtitle_font_size,
                    margin_v=config.subtitle_margin_v
                )
            except Exception as e:
                logger.error(f"Lỗi burn phụ đề: {e}, sao chép video không sub")
                shutil.copy2(current_video_stage, final_output_path)
        else:
            update_progress(92, "Bỏ qua tạo phụ đề...")
            shutil.copy2(current_video_stage, final_output_path)

        update_progress(100, f"✅ Xuất video thành công: video_{job_id}.mp4")
        return final_output_path

