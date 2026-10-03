import asyncio
import logging
import subprocess
import json
import re
from pathlib import Path
from typing import List, Optional
import edge_tts
from config.settings import settings
from app.schemas.contracts import SceneContract, AudioSegment

logger = logging.getLogger(__name__)


class VoiceAgent:
    """
    Agent chịu trách nhiệm tạo giọng đọc AI từ văn bản bằng Edge-TTS.

    Chiến lược Audio-First Timing:
      1. Sinh MP3 cho TỪNG scene riêng biệt.
      2. Đo duration thực bằng ffprobe.
      3. Ghi audio_path + audio_duration_ms ngược lại vào SceneContract.
      4. Concat tất cả scene audio → 1 file full_narration.mp3 cho FFmpeg.
    """

    def __init__(self, default_voice: str = None):
        self.default_voice = default_voice or settings.DEFAULT_TTS_VOICE

    @staticmethod
    def _is_vietnamese(text: str) -> bool:
        """Kiểm tra văn bản có chứa ký tự tiếng Việt có dấu hay không."""
        import re
        vn_pattern = r'[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]'
        return bool(re.search(vn_pattern, text, re.IGNORECASE))

    def _resolve_safe_voice(self, text: str, requested_voice: Optional[str] = None) -> str:
        """
        Bảo vệ khớp ngôn ngữ - giọng đọc:
        - Nếu văn bản tiếng Việt mà giọng đọc tiếng Anh -> tự động chuyển sang giọng Việt chuẩn (vi-VN-NamMinhNeural).
        - Nếu văn bản tiếng Anh mà giọng đọc tiếng Việt -> tự động chuyển sang giọng Mỹ chuẩn (en-US-AndrewNeural).
        """
        voice = requested_voice or self.default_voice
        is_vn_text = self._is_vietnamese(text)
        is_vn_voice = voice.lower().startswith("vi-")

        if is_vn_text and not is_vn_voice:
            logger.warning(
                f"⚠️ Phát hiện văn bản tiếng Việt ('{text[:30]}...') nhưng giọng đọc chỉ định là '{voice}' (tiếng Anh). "
                f"Tự động chuyển sang 'vi-VN-NamMinhNeural' để tránh giọng đọc ngọng / phát âm bồi dị hợm!"
            )
            return "vi-VN-NamMinhNeural"
        elif not is_vn_text and is_vn_voice:
            logger.warning(
                f"⚠️ Phát hiện văn bản tiếng Anh ('{text[:30]}...') nhưng giọng đọc chỉ định là '{voice}' (tiếng Việt). "
                f"Tự động chuyển sang 'en-US-AndrewNeural' để đọc chuẩn bản ngữ!"
            )
            return "en-US-AndrewNeural"
        return voice

    # ──────────────────────────────────────────
    # PUBLIC API — Gọi từ Pipeline Orchestrator
    # ──────────────────────────────────────────

    async def generate_per_scene_audio(
        self,
        scenes: List[SceneContract],
        output_dir: str,
        voice: str = None,
        rate: Optional[str] = None,
    ) -> List[SceneContract]:
        """
        Sinh MP3 cho từng scene. Trả về list SceneContract đã enriched
        với audio_path và audio_duration_ms.
        Tích hợp Metadata Verification để bảo vệ cache: chỉ tái sử dụng nếu
        cả nội dung lời thoại (narration) và giọng đọc (voice) khớp chính xác 100%.
        """
        enriched_scenes = []
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        for scene in scenes:
            scene_audio_path = str(Path(output_dir) / f"scene_{scene.scene_id:03d}.mp3")
            meta_path = Path(output_dir) / f"scene_{scene.scene_id:03d}.meta.json"

            safe_voice = self._resolve_safe_voice(scene.narration, voice or self.default_voice)

            # Checkpoint: Chỉ tái sử dụng audio từ trước nếu file audio > 1KB VÀ metadata khớp chính xác cả text lẫn voice
            cache_valid = False
            if Path(scene_audio_path).exists() and Path(scene_audio_path).stat().st_size > 1024 and meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if meta.get("narration") == scene.narration and meta.get("voice") == safe_voice:
                        duration_ms = meta.get("duration_ms") or self._get_audio_duration_ms(scene_audio_path)
                        if duration_ms > 0:
                            cache_valid = True
                            logger.info(f"Scene {scene.scene_id}: Tái sử dụng audio hợp lệ từ checkpoint ({duration_ms}ms, voice={safe_voice})")
                            enriched = scene.model_copy(update={
                                "audio_path": scene_audio_path,
                                "audio_duration_ms": duration_ms,
                            })
                            enriched_scenes.append(enriched)
                except Exception as ex:
                    logger.debug(f"Scene {scene.scene_id}: Lỗi đọc cache metadata: {ex}")

            if cache_valid:
                continue

            # Cache không hợp lệ hoặc lời thoại / giọng đọc đã đổi -> xóa file cũ
            if Path(scene_audio_path).exists():
                logger.info(f"Scene {scene.scene_id}: Lời thoại hoặc giọng đọc đã đổi. Xóa audio cũ để tạo mới...")
                try:
                    Path(scene_audio_path).unlink(missing_ok=True)
                except Exception:
                    pass

            # Sinh audio cho 1 scene với cơ chế retry và fallback voice
            success = False
            last_err = None
            candidate_voices = [safe_voice]
            if safe_voice.startswith("vi-"):
                if "HoaiMy" in safe_voice:
                    candidate_voices.append("vi-VN-NamMinhNeural")
                else:
                    candidate_voices.append("vi-VN-HoaiMyNeural")
            else:
                if "Andrew" in safe_voice:
                    candidate_voices.extend(["en-US-GuyNeural", "en-US-ChristopherNeural"])
                elif "Guy" in safe_voice:
                    candidate_voices.extend(["en-US-AndrewNeural", "en-US-ChristopherNeural"])
                else:
                    candidate_voices.extend(["en-US-GuyNeural", "en-US-AndrewNeural"])

            for c_voice in candidate_voices:
                for attempt in range(3):
                    try:
                        clean_text = re.sub(r'^[^\w\s\(\)]+', '', scene.narration).strip()
                        if not clean_text or len(clean_text) < 2:
                            clean_text = scene.narration.strip() or "..."
                        # Tốc độ đọc chuẩn hóa 160-175 WPM (rate: +12% cho Tiếng Anh, +7% cho Tiếng Việt) dồn dập, tự nhiên, chống tụt retention
                        tts_rate = rate or ("+7%" if c_voice.startswith("vi-") else "+12%")
                        communicate = edge_tts.Communicate(clean_text, c_voice, rate=tts_rate)
                        await communicate.save(scene_audio_path)
                        if Path(scene_audio_path).exists() and Path(scene_audio_path).stat().st_size > 1024:
                            safe_voice = c_voice
                            success = True
                            break
                        else:
                            raise RuntimeError("Audio file empty or incomplete")
                    except Exception as err_tts:
                        last_err = err_tts
                        logger.warning(f"Scene {scene.scene_id}: Lỗi TTS '{c_voice}' (lần {attempt+1}/3): {err_tts}")
                        await asyncio.sleep(1.0 * (attempt + 1))
                if success:
                    break

            if not success:
                raise last_err or RuntimeError(f"Không thể sinh audio cho cảnh {scene.scene_id}")

            # Đo duration thực bằng ffprobe
            duration_ms = self._get_audio_duration_ms(scene_audio_path)

            # Lưu metadata để xác minh checkpoint trong tương lai
            try:
                meta_path.write_text(json.dumps({
                    "narration": scene.narration,
                    "voice": safe_voice,
                    "duration_ms": duration_ms,
                }, ensure_ascii=False), encoding="utf-8")
            except Exception as ex:
                logger.debug(f"Scene {scene.scene_id}: Lỗi ghi cache metadata: {ex}")

            # Enriched scene với actual timing
            enriched = scene.model_copy(update={
                "audio_path": scene_audio_path,
                "audio_duration_ms": duration_ms,
            })
            enriched_scenes.append(enriched)

            logger.info(
                f"Scene {scene.scene_id}: audio={duration_ms}ms (voice={safe_voice}) "
                f"(estimated={scene.estimated_seconds}s, actual={duration_ms/1000:.1f}s)"
            )

        return enriched_scenes

    async def concat_scene_audio(
        self,
        scenes: List[SceneContract],
        output_path: str
    ) -> int:
        """
        Ghép tất cả scene audio thành 1 file MP3 liên tục.
        Trả về total_duration_ms.
        """
        # Tạo file silence 0.6s để làm đệm kết thúc (giúp giọng đọc dứt câu trọn vẹn, không ngắt đột ngột)
        from app.utils.ffmpeg_helper import run_ffmpeg, get_duration_ms
        tail_silence_path = str(Path(output_path).parent / "tail_silence.mp3")
        has_tail = False
        try:
            run_ffmpeg([
                "-f", "lavfi",
                "-i", "anullsrc=r=24000:cl=mono",
                "-t", "0.6",
                "-c:a", "libmp3lame",
                tail_silence_path
            ])
            has_tail = Path(tail_silence_path).exists()
        except Exception as e:
            logger.warning(f"Không thể tạo tail silence: {e}")

        # Tạo file list cho ffmpeg concat
        list_file = str(Path(output_path).parent / "audio_concat_list.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for scene in scenes:
                if not scene.audio_path:
                    raise ValueError(f"Scene {scene.scene_id} chưa có audio_path")
                # Chuẩn hóa path sang forward slash (as_posix) an toàn cho FFmpeg concat trên Windows/Linux
                safe_path = Path(scene.audio_path).resolve().as_posix().replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")
            if has_tail:
                safe_tail = Path(tail_silence_path).resolve().as_posix().replace("'", "'\\''")
                f.write(f"file '{safe_tail}'\n")

        # FFmpeg concat demuxer an toàn qua run_ffmpeg
        run_ffmpeg([
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            output_path
        ])

        # Áp dụng bộ lọc Pacing silenceremove an toàn (chỉ cắt khoảng lặng > 0.3s, giữ đệm thở tự nhiên 0.1-0.15s)
        try:
            import shutil
            from app.utils.ffmpeg_helper import trim_audio_silence
            trimmed_temp = str(Path(output_path).parent / "narration_trimmed.mp3")
            if trim_audio_silence(output_path, trimmed_temp, silence_duration=0.3, silence_threshold_db=-40.0):
                shutil.move(trimmed_temp, output_path)
                logger.info("[Pacing] ✅ Đã tối ưu nhịp thở silenceremove cho full narration audio")
        except Exception as e_trim:
            logger.debug(f"[Pacing] Bỏ qua silenceremove: {e_trim}")

        total_ms = get_duration_ms(output_path)
        logger.info(f"Full narration audio: {total_ms}ms ({total_ms/1000:.1f}s)")
        return total_ms

    async def generate_full_narration(
        self,
        text: str,
        output_path: str,
        voice: str = None
    ) -> str:
        """
        Backward-compatible: Tạo 1 file audio từ toàn bộ narration.
        Dùng cho trường hợp không cần per-scene timing.
        """
        safe_voice = self._resolve_safe_voice(text, voice or self.default_voice)
        communicate = edge_tts.Communicate(text, safe_voice)
        await communicate.save(output_path)
        return output_path

    # ──────────────────────────────────────────
    # PRIVATE HELPERS
    # ──────────────────────────────────────────

    @staticmethod
    def _get_audio_duration_ms(file_path: str) -> int:
        """Đo chính xác duration bằng ffprobe (ms). Fallback = 0."""
        from app.utils.ffmpeg_helper import get_duration_ms
        return get_duration_ms(file_path)
