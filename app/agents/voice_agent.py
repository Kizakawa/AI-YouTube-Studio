import asyncio
import logging
import subprocess
import json
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
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

    # ── Đa thị trường: Mặc định bật bảo vệ US channels (triệt tiêu giọng Việt trừ khi chỉ định) ──
    DISABLE_VIETNAMESE_VOICE: bool = True

    US_VOICE_PERSONAS: Dict[str, str] = {
        "default": "en-US-AndrewNeural",              # Warm, engaging, versatile YouTube host
        "authority": "en-US-ChristopherNeural",       # Documentary, tech, business, executive
        "deep_history": "en-US-EricNeural",           # Stoic, philosopher, emperor, deep baritone
        "detective": "en-US-GuyNeural",               # Gritty, noir, crime, investigative
        "storyteller_female": "en-US-JennyNeural",    # Emotional, young woman, drama
        "conversational": "en-US-BrianNeural",        # Friendly, casual, modern Shorts
        "energetic": "en-US-RogerNeural",             # Fast-paced, punchy hook
    }

    @classmethod
    def get_persona_voice(
        cls,
        archetype: Optional[str] = None,
        niche_id: Optional[str] = None,
        is_dialogue: bool = False,
    ) -> str:
        """
        Ánh xạ nhân vật (Cast Archetype) và ngách (Niche) sang Giọng đọc Mỹ chuẩn (US Neural Voice Persona).
        Lấy cảm hứng từ VoiceStudio Voice Design & Cast Registry.
        """
        arch = (archetype or "").lower()
        niche = (niche_id or "").lower()

        # 1. Khớp theo Hình mẫu nhân vật (Cast Archetype)
        if any(k in arch for k in ["tesla", "einstein", "marcus", "caesar", "monarch", "elder", "philosopher"]):
            return cls.US_VOICE_PERSONAS["deep_history"]
        if any(k in arch for k in ["detective", "investigator", "noir", "cop"]):
            return cls.US_VOICE_PERSONAS["detective"]
        if any(k in arch for k in ["hacker", "netrunner", "tech", "specialist", "executive", "ceo", "commander"]):
            return cls.US_VOICE_PERSONAS["authority"]
        if any(k in arch for k in ["woman", "girl", "lady", "cleopatra", "female"]):
            return cls.US_VOICE_PERSONAS["storyteller_female"]

        # 2. Khớp theo Chủ đề Ngách (Niche Profile)
        if any(k in niche for k in ["scifi", "tech", "future"]):
            return cls.US_VOICE_PERSONAS["authority"]
        if any(k in niche for k in ["histor", "stoic", "deep_sea", "mystery"]):
            return cls.US_VOICE_PERSONAS["deep_history"]
        if any(k in niche for k in ["animal", "rescue", "pets"]):
            return cls.US_VOICE_PERSONAS["storyteller_female"] if is_dialogue else cls.US_VOICE_PERSONAS["conversational"]
        if any(k in niche for k in ["wildlife", "predators"]):
            return cls.US_VOICE_PERSONAS["deep_history"]

        # 3. Mặc định giọng nam Mỹ tự nhiên cuốn hút
        return cls.US_VOICE_PERSONAS["default"]

    def __init__(self, default_voice: str = None, disable_vietnamese: Optional[bool] = None):
        self.default_voice = default_voice or settings.DEFAULT_TTS_VOICE
        if disable_vietnamese is not None:
            self.DISABLE_VIETNAMESE_VOICE = disable_vietnamese
        elif self.default_voice and self.default_voice.lower().startswith("vi-"):
            self.DISABLE_VIETNAMESE_VOICE = False

    @staticmethod
    def _is_vietnamese(text: str) -> bool:
        """
        Kiểm tra văn bản có phải tiếng Việt hay không.
        Phải phân biệt với tiếng Bồ Đào Nha (pt-BR) và các ngôn ngữ Latin khác
        (vốn cũng có các ký tự như à, á, ã, é, ê, í, ó, ú).
        """
        import re
        # Các ký tự đặc trưng riêng biệt chỉ có trong tiếng Việt
        vn_unique_pattern = r'[ơớờởỡợưứừửữựđĐăắằẳẵặâấầẩẫậêếềểễệôốồổỗộ]'
        if re.search(vn_unique_pattern, text):
            # Kiểm tra thêm nếu có từ tiếng Bồ Đào Nha phổ biến thì không phải tiếng Việt
            pt_words = {"não", "você", "com", "uma", "um", "para", "que", "dos", "das", "selvagem", "amazônia", "mistério"}
            words = set(re.findall(r'\b\w+\b', text.lower()))
            if len(words.intersection(pt_words)) >= 2:
                return False
            return True
        return False

    def _resolve_safe_voice(self, text: str, requested_voice: Optional[str] = None) -> str:
        """
        Bảo vệ khớp ngôn ngữ - giọng đọc:
        - Nếu giọng chỉ định là tiếng Bồ Đào Nha (pt-), Tây Ban Nha (es-), Pháp (fr-)... -> Giữ nguyên tuyệt đối.
        - Nếu văn bản tiếng Việt mà giọng đọc tiếng Anh -> tự động chuyển sang giọng Việt chuẩn (vi-VN-NamMinhNeural).
        - Nếu văn bản tiếng Anh mà giọng đọc tiếng Việt -> tự động chuyển sang giọng Mỹ chuẩn (en-US-AndrewNeural).
        """
        voice = requested_voice or self.default_voice
        voice_lower = voice.lower()

        # Không can thiệp nếu là giọng đọc các ngôn ngữ quốc tế khác (pt-BR, es, fr, de, ja, ko...)
        if voice_lower.startswith(("pt-", "es-", "fr-", "de-", "it-", "ja-", "ko-", "zh-")):
            return voice

        is_vn_text = self._is_vietnamese(text)
        is_vn_voice = voice_lower.startswith("vi-")

        if is_vn_text and not is_vn_voice:
            logger.warning(
                f"⚠️ Phát hiện văn bản tiếng Việt ('{text[:30]}...') nhưng giọng đọc chỉ định là '{voice}' (tiếng Anh). "
                f"Tự động chuyển sang 'vi-VN-NamMinhNeural' để tránh giọng đọc ngọng!"
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
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        sem = asyncio.Semaphore(3)

        async def _process_single_scene(scene: SceneContract) -> SceneContract:
            async with sem:
                scene_audio_path = str(Path(output_dir) / f"scene_{scene.scene_id:03d}.mp3")
                meta_path = Path(output_dir) / f"scene_{scene.scene_id:03d}.meta.json"
                words_meta_path = str(Path(output_dir) / f"scene_{scene.scene_id:03d}.words.jsonl")

                safe_voice = self._resolve_safe_voice(scene.narration, voice or self.default_voice)

                # Checkpoint: Chỉ tái sử dụng audio từ trước nếu file audio > 1KB VÀ metadata khớp chính xác cả text lẫn voice
                if Path(scene_audio_path).exists() and Path(scene_audio_path).stat().st_size > 1024 and meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        if meta.get("narration") == scene.narration and meta.get("voice") == safe_voice:
                            duration_ms = meta.get("duration_ms") or await asyncio.to_thread(self._get_audio_duration_ms, scene_audio_path)
                            if duration_ms > 0:
                                cached_words = meta.get("word_timings")
                                if not cached_words and Path(words_meta_path).exists():
                                    cached_words = self._parse_words_jsonl(words_meta_path)
                                logger.info(
                                    f"Scene {scene.scene_id}: Tái sử dụng audio hợp lệ từ checkpoint "
                                    f"({duration_ms}ms, voice={safe_voice}, {len(cached_words or [])} words)"
                                )
                                return scene.model_copy(update={
                                    "audio_path": scene_audio_path,
                                    "audio_duration_ms": duration_ms,
                                    "word_timings": cached_words if cached_words else None,
                                })
                    except Exception as ex:
                        logger.debug(f"Scene {scene.scene_id}: Lỗi đọc cache metadata: {ex}")

                # Cache không hợp lệ hoặc lời thoại / giọng đọc đã đổi -> xóa file cũ
                if Path(scene_audio_path).exists():
                    logger.info(f"Scene {scene.scene_id}: Lời thoại hoặc giọng đọc đã đổi. Xóa audio cũ để tạo mới...")
                    try:
                        Path(scene_audio_path).unlink(missing_ok=True)
                        if Path(words_meta_path).exists():
                            Path(words_meta_path).unlink(missing_ok=True)
                    except Exception:
                        pass

                # Sinh audio cho 1 scene với cơ chế retry và fallback voice đúng ngôn ngữ
                success = False
                last_err = None
                candidate_voices = [safe_voice]
                if safe_voice.startswith("vi-"):
                    if "HoaiMy" in safe_voice:
                        candidate_voices.append("vi-VN-NamMinhNeural")
                    else:
                        candidate_voices.append("vi-VN-HoaiMyNeural")
                elif safe_voice.startswith("pt-"):
                    if "Antonio" in safe_voice:
                        candidate_voices.append("pt-BR-FranciscaNeural")
                    else:
                        candidate_voices.append("pt-BR-AntonioNeural")
                else:
                    if "Andrew" in safe_voice:
                        candidate_voices.extend(["en-US-GuyNeural", "en-US-ChristopherNeural"])
                    elif "Guy" in safe_voice:
                        candidate_voices.extend(["en-US-AndrewNeural", "en-US-ChristopherNeural"])
                    else:
                        candidate_voices.extend(["en-US-GuyNeural", "en-US-AndrewNeural"])

                clean_text = re.sub(r'^[^\w\s\(\)]+', '', scene.narration).strip()
                if not clean_text or len(clean_text) < 2:
                    clean_text = scene.narration.strip() or "..."

                for c_voice in candidate_voices:
                    for attempt in range(3):
                        try:
                            tts_rate = rate or ("+7%" if c_voice.startswith("vi-") else ("+9%" if c_voice.startswith("pt-") else "+12%"))
                            try:
                                communicate = edge_tts.Communicate(clean_text, c_voice, rate=tts_rate, boundary="WordBoundary")
                                await communicate.save(scene_audio_path, metadata_fname=words_meta_path)
                            except Exception as e_bound:
                                logger.debug(f"Scene {scene.scene_id}: boundary WordBoundary fallback: {e_bound}")
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
                            if "NoAudioReceived" in str(type(err_tts).__name__) or "No audio" in str(err_tts):
                                try:
                                    import shutil, subprocess
                                    edge_cli = shutil.which("edge-tts") or str(Path(sys.executable).parent / "edge-tts.exe")
                                    cmd = [edge_cli, "--text", clean_text, "--voice", c_voice, "--write-media", scene_audio_path]
                                    res = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=25)
                                    if res.returncode == 0 and Path(scene_audio_path).exists() and Path(scene_audio_path).stat().st_size > 1024:
                                        safe_voice = c_voice
                                        success = True
                                        logger.info(f"Scene {scene.scene_id}: ✅ Tạo audio thành công qua CLI fallback ({c_voice})")
                                        break
                                except Exception:
                                    pass
                            logger.warning(f"Scene {scene.scene_id}: Lỗi TTS '{c_voice}' (lần {attempt+1}/3): {err_tts}")
                            await asyncio.sleep(0.5)
                    if success:
                        break

                if not success:
                    try:
                        import shutil, subprocess
                        edge_cli = shutil.which("edge-tts") or str(Path(sys.executable).parent / "edge-tts.exe")
                        cmd = [edge_cli, "--text", clean_text, "--voice", safe_voice, "--write-media", scene_audio_path]
                        res = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=25)
                        if res.returncode == 0 and Path(scene_audio_path).exists() and Path(scene_audio_path).stat().st_size > 1024:
                            success = True
                            logger.info(f"Scene {scene.scene_id}: ✅ Tạo audio thành công qua CLI fallback ({Path(scene_audio_path).stat().st_size:,} bytes)")
                    except Exception as ex_cli:
                        logger.debug(f"Scene {scene.scene_id}: CLI fallback error: {ex_cli}")

                if not success:
                    raise last_err or RuntimeError(f"Không thể sinh audio cho cảnh {scene.scene_id}")

                # Đo duration thực bằng ffprobe (non-blocking)
                duration_ms = await asyncio.to_thread(self._get_audio_duration_ms, scene_audio_path)

                # Đọc word-level timings chính xác từ file metadata
                word_timings = self._parse_words_jsonl(words_meta_path)

                # Lưu metadata để xác minh checkpoint trong tương lai
                try:
                    meta_path.write_text(json.dumps({
                        "narration": scene.narration,
                        "voice": safe_voice,
                        "duration_ms": duration_ms,
                        "word_timings": word_timings,
                    }, ensure_ascii=False), encoding="utf-8")
                except Exception as ex:
                    logger.debug(f"Scene {scene.scene_id}: Lỗi ghi cache metadata: {ex}")

                enriched = scene.model_copy(update={
                    "audio_path": scene_audio_path,
                    "audio_duration_ms": duration_ms,
                    "word_timings": word_timings if word_timings else None,
                })

                logger.info(
                    f"Scene {scene.scene_id}: audio={duration_ms}ms ({len(word_timings)} words, voice={safe_voice}) "
                    f"(estimated={scene.estimated_seconds}s, actual={duration_ms/1000:.1f}s)"
                )
                return enriched

        enriched_scenes = list(await asyncio.gather(*[_process_single_scene(sc) for sc in scenes]))
        return enriched_scenes

    @staticmethod
    def _parse_words_jsonl(words_file_path: str) -> List[Dict[str, Any]]:
        """Đọc và trích xuất mốc thời gian từng từ từ file JSONL sinh bởi Edge-TTS."""
        p = Path(words_file_path)
        if not p.exists() or p.stat().st_size == 0:
            return []
        words = []
        try:
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line_s = line.strip()
                    if not line_s:
                        continue
                    item = json.loads(line_s)
                    if item.get("type") == "WordBoundary":
                        start_ms = int(item.get("offset", 0) / 10000)
                        dur_ms = int(item.get("duration", 0) / 10000)
                        text = item.get("text", "").strip()
                        if text:
                            words.append({
                                "word": text,
                                "start_ms": start_ms,
                                "end_ms": start_ms + dur_ms,
                                "duration_ms": dur_ms,
                            })
        except Exception as e:
            logger.debug(f"Lỗi parse words jsonl {words_file_path}: {e}")
        return words

    async def concat_scene_audio(
        self,
        scenes: List[SceneContract],
        output_path: str
    ) -> int:
        """
        Ghép tất cả scene audio thành 1 file MP3 liên tục.
        Đảm bảo ĐỒNG BỘ TUYỆT ĐỐI (Bit-for-bit Deterministic Timeline):
        Tổng thời lượng đúng chính xác bằng tổng thời lượng từng scene + tail silence đệm.
        Tuyệt đối KHÔNG áp dụng silenceremove toàn cục làm co ngắn audio gây lệch phụ đề và video cut.
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

        # FFmpeg concat demuxer an toàn qua run_ffmpeg (giữ nguyên timestamp chuẩn hóa)
        run_ffmpeg([
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            output_path
        ])

        total_ms = get_duration_ms(output_path)
        logger.info(f"Full narration audio (perfect deterministic sync): {total_ms}ms ({total_ms/1000:.1f}s)")
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
