import asyncio
import logging
import subprocess
import json
import re
import sys
import time
from pathlib import Path
from typing import List, Optional, Dict, Any
import edge_tts
from config.settings import settings
from app.schemas.contracts import SceneContract, AudioSegment

# Đảm bảo Sec-MS-GEC-Version luôn ở chuẩn v2+ kể cả khi tiến trình cũ chưa nạp lại constants mới
try:
    import edge_tts.constants as _edge_const
    if getattr(_edge_const, "SEC_MS_GEC_VERSION", "").startswith("1-"):
        _edge_const.SEC_MS_GEC_VERSION = "2-" + _edge_const.SEC_MS_GEC_VERSION[2:]
except Exception:
    pass

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
    _edge_ws_blocked_until: float = 0.0
    _edge_403_streak: int = 0

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

        # 2. Khớp theo Chủ đề Ngách (Niche Profile - hỗ trợ mở rộng không giới hạn ngách mới)
        if any(k in niche for k in ["scifi", "tech", "future", "ai", "finance", "money", "business", "crypto", "wealth", "economy", "luxury"]):
            return cls.US_VOICE_PERSONAS["authority"]
        if any(k in niche for k in ["crime", "noir", "detective", "heist", "mafia", "thriller", "conspiracy"]):
            return cls.US_VOICE_PERSONAS["detective"]
        if any(k in niche for k in ["histor", "stoic", "deep_sea", "mystery", "space", "cosmos", "universe", "myth", "philosophy"]):
            return cls.US_VOICE_PERSONAS["deep_history"]
        if any(k in niche for k in ["animal", "rescue", "pets", "wholesome", "emotional", "kindness"]):
            return cls.US_VOICE_PERSONAS["storyteller_female"] if is_dialogue else cls.US_VOICE_PERSONAS["conversational"]
        if any(k in niche for k in ["wildlife", "predators", "survival", "nature", "combat"]):
            return cls.US_VOICE_PERSONAS["deep_history"]
        if any(k in niche for k in ["sport", "gaming", "anime", "fitness", "gym", "hype", "cars"]):
            return cls.US_VOICE_PERSONAS["energetic"]

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
        vn_unique_pattern = r'[ơớờởỡợưứừửữựđĐăắằẳẵặảẩẫậẻểễệỉịỏổỗộủửữựỷỹỵ]'
        if re.search(vn_unique_pattern, text):
            pt_words = {"não", "você", "com", "uma", "um", "para", "que", "dos", "das", "selvagem", "amazônia", "mistério"}
            words = set(re.findall(r'\b\w+\b', text.lower()))
            if len(words.intersection(pt_words)) >= 2:
                return False
            return True
        return False

    @staticmethod
    def _is_portuguese(text: str) -> bool:
        """
        Kiểm tra văn bản có phải tiếng Bồ Đào Nha (pt-BR) hay không.
        """
        import re
        if VoiceAgent._is_vietnamese(text):
            return False
        t_low = text.lower()
        pt_words = {
            "não", "você", "uma", "para", "com", "mais", "pelo", "pela", "está",
            "são", "isso", "até", "mas", "nas", "nos", "dos", "das", "onça",
            "oceano", "mistério", "ciência", "metros", "selva", "amazônia", "pantanal",
            "sucuri", "jacaré", "quando", "apenas", "fundo", "água", "águas",
            "eles", "elas", "nunca", "jamais", "deveriam", "caminho", "coração",
            "cerrado", "abismo", "puxa", "tudo", "baixo", "inteiros", "marinha",
            "gravou", "ninguém", "explica", "batalha", "duelo", "floresta",
            "sobrevive", "rainha", "fantasma", "contra", "sozinha", "bando",
            "ariranha", "lobo", "tatu", "fossa", "navios", "buraco", "engole",
            "olham", "noite", "cérebro", "cientistas", "segredo", "verdade",
            "perigo", "mortal", "ataque", "veneno", "predador", "garras",
            "força", "mordida", "quase", "porque", "seu", "sua", "seus", "suas",
            "este", "esta", "esse", "essa", "muito", "todos", "todas", "depois",
            "antes", "agora", "sempre", "onde", "quem",
        }
        words = set(re.findall(r'\b\w+\b', t_low))
        if len(words.intersection(pt_words)) >= 2:
            return True
        if ("ç" in t_low or "õ" in t_low or "ã" in t_low) and len(words.intersection(pt_words)) >= 1:
            return True
        return False

    def _resolve_safe_voice(self, text: str, requested_voice: Optional[str] = None) -> str:
        """
        Bảo vệ khớp ngôn ngữ - giọng đọc:
        - Nếu văn bản là tiếng Bồ Đào Nha (pt-BR) mà giọng đọc không phải pt-* -> tự động chuyển sang 'pt-BR-AntonioNeural'.
        - Nếu giọng chỉ định là các ngôn ngữ quốc tế khác (es-, fr-, de-...) -> Giữ nguyên.
        - Nếu văn bản tiếng Việt mà giọng đọc tiếng Anh -> tự động chuyển sang giọng Việt chuẩn (vi-VN-NamMinhNeural).
        - Nếu văn bản tiếng Anh mà giọng đọc tiếng Việt -> tự động chuyển sang giọng Mỹ chuẩn (en-US-AndrewNeural).
        """
        voice = requested_voice or self.default_voice
        voice_lower = voice.lower()

        is_pt_text = self._is_portuguese(text)
        is_pt_voice = voice_lower.startswith("pt-")
        if is_pt_text and not is_pt_voice:
            logger.warning(
                f"⚠️ Phát hiện văn bản tiếng Bồ Đào Nha pt-BR ('{text[:30]}...') nhưng giọng đọc chỉ định là '{voice}'. "
                f"Tự động chuyển sang 'pt-BR-AntonioNeural' để đọc chuẩn bản ngữ Brazil!"
            )
            return "pt-BR-AntonioNeural"

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

    @staticmethod
    def resolve_optimal_tts_rate(voice: str, niche: Optional[str] = None, explicit_rate: Optional[str] = None) -> str:
        """
        Xác định tốc độ đọc TTS tối ưu (Hyper-Retention Viral Shorts Pacing ~200-230 WPM, dồn dập cuốn hút):
        - animal_rescue / wholesome (JennyNeural / HoaiMyNeural): +26% (truyền cảm nhưng nhịp nhanh, dứt khoát).
        - deep_sea_earth_mysteries / horror / crime / history / stoicism: +31% (dồn dập, nghẹt thở, khóa chặt người nghe).
        - psychology / wildlife_predators / alternate_history / finance / tech / scifi / general: +33% (tốc độ cao chuẩn Zack D. Films / MrBeast Shorts).
        - sports / gaming / hype: +36% (nhịp độ cao trào cực đại).
        """
        if explicit_rate:
            return explicit_rate

        nid = (niche or "").strip().lower()
        v_low = (voice or "").lower()
        is_gentle_niche = any(k in nid for k in ("rescue", "animal", "wholesome", "satirical")) or "jenny" in v_low or "hoaimy" in v_low
        min_floor = 26 if is_gentle_niche else 30

        if nid:
            try:
                from app.services.niche_manager import get_niche
                profile = get_niche(nid)
                if str(profile.get("id") or "").strip().lower() == nid:
                    audio_cfg = profile.get("audio", {})
                    cfg_rate = audio_cfg.get("tts_rate") or audio_cfg.get("rate")
                    if cfg_rate and isinstance(cfg_rate, str) and cfg_rate.endswith("%"):
                        try:
                            val = int(cfg_rate.replace("%", "").replace("+", "").strip())
                            if val >= min_floor:
                                return f"+{val}%"
                        except Exception:
                            pass
            except Exception:
                pass

        if is_gentle_niche:
            return "+26%"
        if any(k in nid for k in ("deep_sea", "mystery", "ocean", "space", "cosmos", "horror", "creepy", "crime", "noir", "histor", "stoic", "philosophy")):
            return "+31%"
        if any(k in nid for k in ("sport", "gaming", "hype", "anime")):
            return "+36%"
        if v_low.startswith("vi-"):
            return "+29%"
        if v_low.startswith("pt-"):
            return "+30%"
        return "+33%"

    # ──────────────────────────────────────────
    # PUBLIC API — Gọi từ Pipeline Orchestrator
    # ──────────────────────────────────────────

    async def generate_per_scene_audio(
        self,
        scenes: List[SceneContract],
        output_dir: str,
        voice: str = None,
        rate: Optional[str] = None,
        niche: Optional[str] = None,
    ) -> List[SceneContract]:
        """
        Sinh MP3 cho từng scene. Trả về list SceneContract đã enriched
        với audio_path và audio_duration_ms.
        Tích hợp Metadata Verification để bảo vệ cache: chỉ tái sử dụng nếu
        nội dung lời thoại (narration), giọng đọc (voice) và tốc độ (rate) khớp chính xác 100%.
        """
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        sem = asyncio.Semaphore(4)
        # Xác định giọng đọc đồng nhất trên toàn bộ kịch bản (tránh trường hợp 1 câu ngắn bị nhận diện lệch ngôn ngữ)
        full_script_text = " ".join(getattr(s, "narration", "") or "" for s in scenes)
        global_safe_voice = self._resolve_safe_voice(full_script_text, voice or self.default_voice)

        async def _process_single_scene(scene: SceneContract) -> SceneContract:
            async with sem:
                # Giãn nhẹ nhịp mở WebSocket tránh nghẽn đồng thời
                if scene.scene_id > 1:
                    await asyncio.sleep(0.06 * ((scene.scene_id - 1) % 4))
                scene_audio_path = str(Path(output_dir) / f"scene_{scene.scene_id:03d}.mp3")
                meta_path = Path(output_dir) / f"scene_{scene.scene_id:03d}.meta.json"
                words_meta_path = str(Path(output_dir) / f"scene_{scene.scene_id:03d}.words.jsonl")

                safe_voice = self._resolve_safe_voice(scene.narration, global_safe_voice)
                expected_rate = self.resolve_optimal_tts_rate(safe_voice, niche=niche, explicit_rate=rate)

                # Checkpoint: Chỉ tái sử dụng audio từ trước nếu file audio > 1KB VÀ metadata khớp chính xác text, voice, rate và đã qua mastering v4
                if Path(scene_audio_path).exists() and Path(scene_audio_path).stat().st_size > 1024 and meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        if (
                            meta.get("narration") == scene.narration
                            and meta.get("voice") == safe_voice
                            and meta.get("rate") == expected_rate
                            and meta.get("mastered_v4") is True
                        ):
                            duration_ms = meta.get("duration_ms") or await asyncio.to_thread(self._get_audio_duration_ms, scene_audio_path)
                            if duration_ms > 0:
                                cached_words = meta.get("word_timings")
                                if not cached_words and Path(words_meta_path).exists():
                                    cached_words = self._parse_words_jsonl(words_meta_path, max_duration_ms=duration_ms)
                                if not cached_words:
                                    cached_words = self._synthesize_word_timings(scene.narration, duration_ms)
                                logger.info(
                                    f"Scene {scene.scene_id}: Tái sử dụng audio hợp lệ từ checkpoint "
                                    f"({duration_ms}ms, voice={safe_voice}, rate={expected_rate}, {len(cached_words or [])} words)"
                                )
                                return scene.model_copy(update={
                                    "audio_path": scene_audio_path,
                                    "audio_duration_ms": duration_ms,
                                    "word_timings": cached_words if cached_words else None,
                                })
                    except Exception as ex:
                        logger.debug(f"Scene {scene.scene_id}: Lỗi đọc cache metadata: {ex}")

                # Cache không hợp lệ hoặc lời thoại / giọng đọc / tốc độ đã đổi -> xóa file cũ
                if Path(scene_audio_path).exists():
                    logger.info(f"Scene {scene.scene_id}: Lời thoại, giọng đọc hoặc tốc độ ({expected_rate}) đã đổi. Xóa audio cũ để tạo mới...")
                    try:
                        Path(scene_audio_path).unlink(missing_ok=True)
                        if Path(words_meta_path).exists():
                            Path(words_meta_path).unlink(missing_ok=True)
                    except Exception:
                        pass

                # Sinh audio cho 1 scene với cơ chế retry và fallback voice đúng ngôn ngữ
                success = False
                used_http_fallback = False
                last_err = None
                candidate_voices = [safe_voice]
                if safe_voice.startswith("vi-"):
                    for v_alt in ("vi-VN-NamMinhNeural", "vi-VN-HoaiMyNeural"):
                        if v_alt not in candidate_voices:
                            candidate_voices.append(v_alt)
                elif safe_voice.startswith("pt-"):
                    for v_alt in ("pt-BR-AntonioNeural", "pt-BR-FranciscaNeural", "pt-BR-ThalitaMultilingualNeural"):
                        if v_alt not in candidate_voices:
                            candidate_voices.append(v_alt)
                else:
                    for v_alt in ("en-US-AndrewNeural", "en-US-BrianNeural", "en-US-ChristopherNeural", "en-US-GuyNeural", "en-US-AriaNeural"):
                        if v_alt not in candidate_voices:
                            candidate_voices.append(v_alt)

                clean_text = re.sub(r'^[^\w\s\(\)]+', '', scene.narration).strip()
                if not clean_text or len(clean_text) < 2:
                    clean_text = scene.narration.strip() or "..."

                # Nếu Bing Edge-TTS đang bị chặn 403 trong cửa sổ cooldown -> bỏ qua vòng lặp WebSocket để tránh treo 2 phút/cảnh
                ws_circuit_open = time.time() < VoiceAgent._edge_ws_blocked_until
                if not ws_circuit_open:
                    for c_voice in candidate_voices:
                        for attempt in range(2):
                            try:
                                tts_rate = self.resolve_optimal_tts_rate(c_voice, niche=niche, explicit_rate=rate)
                                try:
                                    communicate = edge_tts.Communicate(clean_text, c_voice, rate=tts_rate, boundary="WordBoundary")
                                    await communicate.save(scene_audio_path, metadata_fname=words_meta_path)
                                except TypeError:
                                    communicate = edge_tts.Communicate(clean_text, c_voice, rate=tts_rate)
                                    await communicate.save(scene_audio_path)

                                if Path(scene_audio_path).exists() and Path(scene_audio_path).stat().st_size > 1024:
                                    safe_voice = c_voice
                                    success = True
                                    VoiceAgent._edge_403_streak = 0
                                    VoiceAgent._edge_ws_blocked_until = 0.0
                                    break
                                else:
                                    raise RuntimeError("Audio file empty or incomplete")
                            except Exception as err_tts:
                                last_err = err_tts
                                err_str = f"{type(err_tts).__name__}: {err_tts}"
                                is_403 = "403" in err_str or "Invalid response status" in err_str
                                is_no_audio = "NoAudioReceived" in err_str or "No audio" in err_str

                                if is_403:
                                    VoiceAgent._edge_403_streak += 1
                                    if VoiceAgent._edge_403_streak >= 2:
                                        VoiceAgent._edge_ws_blocked_until = time.time() + 45.0
                                        logger.warning(
                                            f"Scene {scene.scene_id}: Edge-TTS WebSocket trả mã 403 liên tiếp ({VoiceAgent._edge_403_streak} lần) "
                                            f"-> Mở Circuit Breaker 45s và chuyển nhanh sang Google HTTP TTS."
                                        )
                                        ws_circuit_open = True
                                        break
                                elif is_no_audio and attempt == 0:
                                    try:
                                        import shutil
                                        edge_cli = shutil.which("edge-tts") or str(Path(sys.executable).parent / "edge-tts.exe")
                                        cmd = [edge_cli, "--text", clean_text, "--voice", c_voice, f"--rate={tts_rate}", "--write-media", scene_audio_path]
                                        res = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=20)
                                        if res.returncode == 0 and Path(scene_audio_path).exists() and Path(scene_audio_path).stat().st_size > 1024:
                                            safe_voice = c_voice
                                            success = True
                                            logger.info(f"Scene {scene.scene_id}: ✅ Tạo audio thành công qua CLI fallback ({c_voice})")
                                            break
                                    except Exception:
                                        pass

                                backoff_s = 0.8 * (attempt + 1)
                                logger.warning(f"Scene {scene.scene_id}: Lỗi TTS '{c_voice}' (lần {attempt+1}/2, nghỉ {backoff_s:.1f}s): {err_tts}")
                                await asyncio.sleep(backoff_s)
                        if success or ws_circuit_open:
                            break

                # Cứu hộ khẩn cấp qua Google Translate TTS HTTP MP3 nếu Edge-TTS bị Bing chặn 403 tạm thời
                if not success:
                    try:
                        import requests as _req
                        from urllib.parse import quote as _quote
                        tl = "vi" if safe_voice.startswith("vi-") else ("pt" if safe_voice.startswith("pt-") else "en")
                        chunks = self._split_text_chunks(clean_text, max_chars=190)
                        audio_bytes = bytearray()
                        for chunk in chunks:
                            gtts_url = f"https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob&tl={tl}&q={_quote(chunk)}"
                            r_tts = await asyncio.to_thread(
                                _req.get,
                                gtts_url,
                                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                                timeout=15,
                            )
                            if r_tts.status_code == 200 and len(r_tts.content) > 256:
                                audio_bytes.extend(r_tts.content)
                        if len(audio_bytes) > 1024:
                            Path(scene_audio_path).write_bytes(bytes(audio_bytes))
                            success = True
                            used_http_fallback = True
                            logger.info(f"Scene {scene.scene_id}: 🛡️ Đã cứu hộ audio thành công qua Google HTTP TTS ({tl}, {len(audio_bytes):,} bytes)")
                    except Exception as ex_gtts:
                        logger.debug(f"Scene {scene.scene_id}: Google HTTP TTS fallback error: {ex_gtts}")

                if not success:
                    raise last_err or RuntimeError(f"Không thể sinh audio cho cảnh {scene.scene_id}")

                # Hậu kỳ âm thanh Broadcast Voice Mastering (EQ ấm dày + Compressor + Cắt khoảng lặng thừa cuối câu)
                await asyncio.to_thread(self._master_and_trim_scene_audio, scene_audio_path, used_http_fallback)

                # Đo duration thực bằng ffprobe (non-blocking) sau khi đã cắt khoảng lặng đuôi
                duration_ms = await asyncio.to_thread(self._get_audio_duration_ms, scene_audio_path)

                # Đọc word-level timings chính xác từ file metadata (hoặc nội suy thông minh nếu dùng fallback HTTP TTS)
                word_timings = self._parse_words_jsonl(words_meta_path, max_duration_ms=duration_ms)
                if not word_timings and duration_ms > 0:
                    word_timings = self._synthesize_word_timings(scene.narration, duration_ms)

                # Lưu metadata để xác minh checkpoint trong tương lai
                try:
                    meta_path.write_text(json.dumps({
                        "narration": scene.narration,
                        "voice": safe_voice,
                        "rate": expected_rate,
                        "mastered_v4": True,
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
    def _split_text_chunks(text: str, max_chars: int = 190) -> List[str]:
        """Chia văn bản dài thành các đoạn <= max_chars theo ranh giới câu/từ cho HTTP TTS fallback."""
        words = (text or "").split()
        if not words:
            return ["..."]
        chunks: List[str] = []
        current: List[str] = []
        cur_len = 0
        for w in words:
            add_len = len(w) + (1 if current else 0)
            if current and cur_len + add_len > max_chars:
                chunks.append(" ".join(current))
                current = [w]
                cur_len = len(w)
            else:
                current.append(w)
                cur_len += add_len
        if current:
            chunks.append(" ".join(current))
        return chunks

    @staticmethod
    def _synthesize_word_timings(text: str, duration_ms: int) -> List[Dict[str, Any]]:
        """
        Nội suy mốc thời gian từng từ (Proportional Phonetic/Punctuation Weighting)
        khi dùng Google HTTP TTS fallback hoặc khi thiếu WordBoundary từ Edge-TTS.
        Đảm bảo phụ đề Karaoke từng từ luôn hoạt động mượt 100% (không bao giờ bị 0 words).
        """
        raw_words = [w.strip() for w in (text or "").split() if w.strip()]
        if not raw_words or duration_ms <= 0:
            return []
        usable_ms = max(200, duration_ms - 40)
        weights: List[float] = []
        for w in raw_words:
            clean_len = max(1, len(re.sub(r"[^\w]", "", w)))
            pause_bonus = 2.2 if w.endswith((".", "!", "?", "…")) else (1.1 if w.endswith((",", ";", ":", "—", "-")) else 0.0)
            weights.append(float(clean_len) + pause_bonus + 1.5)
        total_weight = sum(weights) or 1.0
        timings: List[Dict[str, Any]] = []
        cursor_ms = 0
        for idx, (w, weight) in enumerate(zip(raw_words, weights)):
            if idx == len(raw_words) - 1:
                end_ms = usable_ms
            else:
                end_ms = min(usable_ms, cursor_ms + max(60, int(round(usable_ms * (weight / total_weight)))))
            dur = max(40, end_ms - cursor_ms)
            timings.append({
                "word": w,
                "start_ms": cursor_ms,
                "end_ms": cursor_ms + dur,
                "duration_ms": dur,
            })
            cursor_ms = cursor_ms + dur
        return timings

    @staticmethod
    def _parse_words_jsonl(words_file_path: str, max_duration_ms: Optional[int] = None) -> List[Dict[str, Any]]:
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
                        end_ms = start_ms + dur_ms
                        if max_duration_ms and max_duration_ms > 0:
                            if start_ms >= max_duration_ms:
                                start_ms = max(0, max_duration_ms - 80)
                            if end_ms > max_duration_ms:
                                end_ms = max_duration_ms
                                dur_ms = max(40, end_ms - start_ms)
                        text = item.get("text", "").strip()
                        if text:
                            words.append({
                                "word": text,
                                "start_ms": start_ms,
                                "end_ms": end_ms,
                                "duration_ms": dur_ms,
                            })
        except Exception as e:
            logger.debug(f"Lỗi parse words jsonl {words_file_path}: {e}")
        return words

    @staticmethod
    def _master_and_trim_scene_audio(file_path: str, is_http_fallback: bool = False) -> None:
        """
        Broadcast Voice Mastering & Ultra-Tight Dead-Air Elimination cho từng phân cảnh:
        - Nếu là audio cứu hộ từ Google HTTP TTS (tốc độ gốc 1.0x chậm), tự động tăng tốc atempo=1.32 (+32%).
        - Áp dụng EQ Podcast/Radio (Highpass 80Hz khử ù + Warmth +2.2dB @ 130Hz + Presence +1.8dB @ 4.2kHz)
          kết hợp Compressor nhẹ (-18dB, ratio 2.4) để giọng đọc dày, uy lực và nổi bật trên loa điện thoại.
        - Cắt sát khoảng lặng chết & hơi thở thừa ở CUỐI câu (trailing silence > -32dB) bằng areverse -> silenceremove -> areverse,
          KHÔNG chèn apad ở cuối để chuyển cảnh nối tiếp (J-cut) dồn dập tức thì, triệt tiêu hoàn toàn cảm giác lê thê!
        - KHÔNG cắt đầu câu (0.0ms) trên Edge-TTS để mốc thời gian WordBoundary (start_ms/end_ms) khớp 100% đến từng mili-giây!
        """
        p = Path(file_path)
        if not p.exists() or p.stat().st_size <= 1024:
            return
        tmp_out = str(p.with_suffix(".mastered.mp3"))
        tempo_prefix = "atempo=1.32,silenceremove=start_periods=1:start_duration=0.01:start_threshold=-34dB," if is_http_fallback else ""
        af_chain = (
            f"{tempo_prefix}"
            "highpass=f=80,"
            "equalizer=f=130:width_type=q:width=1.2:g=2.2,"
            "equalizer=f=4200:width_type=q:width=1.5:g=1.8,"
            "acompressor=threshold=-18dB:ratio=2.4:attack=15:release=180:makeup=1.5dB,"
            "areverse,silenceremove=start_periods=1:start_duration=0.01:start_threshold=-32dB,areverse"
        )
        try:
            from app.utils.ffmpeg_helper import run_ffmpeg, get_duration_ms
            orig_dur = get_duration_ms(file_path)
            run_ffmpeg([
                "-i", file_path,
                "-af", af_chain,
                "-ar", "24000",
                "-ac", "1",
                "-c:a", "libmp3lame",
                "-b:a", "128k",
                tmp_out,
            ], timeout=25)
            new_dur = get_duration_ms(tmp_out)
            min_valid_dur = 250 if is_http_fallback else max(300, orig_dur - 1350)
            if Path(tmp_out).exists() and Path(tmp_out).stat().st_size > 1024 and new_dur >= min_valid_dur:
                Path(tmp_out).replace(p)
            else:
                Path(tmp_out).unlink(missing_ok=True)
        except Exception as e:
            logger.debug(f"[VoiceAgent] Bỏ qua mastering '{file_path}' (giữ nguyên file gốc): {e}")
            try:
                Path(tmp_out).unlink(missing_ok=True)
            except Exception:
                pass

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
        # Tạo file silence cực ngắn 0.02s (20ms) để nối Seamless Infinite Loop liền mạch sang Cảnh 1 mà không bị khoảng lặng chết
        from app.utils.ffmpeg_helper import run_ffmpeg, get_duration_ms
        tail_silence_path = str(Path(output_path).parent / "tail_silence.mp3")
        has_tail = False
        try:
            run_ffmpeg([
                "-f", "lavfi",
                "-i", "anullsrc=r=24000:cl=mono",
                "-t", "0.02",
                "-c:a", "libmp3lame",
                "-b:a", "128k",
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
