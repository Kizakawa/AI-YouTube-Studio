"""
Kokoro-82M Local Neural ONNX TTS Engine (Inspired by shorts-flow & MoneyPrinterPlus).

Provides 100% free, offline CPU/GPU neural speech synthesis for English (en-US / en-GB)
and Brazilian Portuguese (pt-BR) with natural human prosody and breathing:
- Zero API quota limits, zero rate-limiting (runs locally via `kokoro-onnx` + `onnxruntime`).
- Maps AI YouTube Studio niche personas (`deep_history`, `detective`, `authority`,
  `storyteller_female`, `conversational`, `pt-BR`) to Kokoro v1.0 voice packs.
- Automatically converts 24kHz float32 PCM waveform to 24kHz mono 128kbps MP3 via FFmpeg.
- Gracefully degrades (returns False) if `kokoro_onnx` or local ONNX weights are not installed,
  allowing `VoiceAgent` to seamlessly continue with Edge-TTS / Gemini TTS tiers.
"""

import logging
import os
import struct
import wave
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Default model paths under storage/models/kokoro/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_KOKORO_DIR = _PROJECT_ROOT / "storage" / "models" / "kokoro"
_DEFAULT_MODEL_PATH = Path(
    os.getenv("KOKORO_MODEL_PATH", str(_DEFAULT_KOKORO_DIR / "kokoro-v1.0.onnx"))
)
_DEFAULT_VOICES_PATH = Path(
    os.getenv("KOKORO_VOICES_PATH", str(_DEFAULT_KOKORO_DIR / "voices-v1.0.bin"))
)

# Singleton Kokoro instance cache
_KOKORO_INSTANCE: Optional[Any] = None
_KOKORO_INIT_FAILED: bool = False

KOKORO_NICHE_VOICE_MAP: Dict[str, str] = {
    "default": "am_adam",            # Deep, warm American male documentary narrator
    "deep_history": "am_michael",    # Gravitas, historical & wildlife narrator
    "detective": "bm_george",        # Intense British/noir investigative narrator
    "authority": "am_adam",          # Authoritative analyst
    "storyteller_female": "af_heart",# #1 TTS Arena empathetic warm female voice
    "conversational": "af_bella",    # Engaging modern Shorts voice
    "pt_br_male": "pm_alex",         # Native Brazilian Portuguese male
    "pt_br_female": "pf_dora",       # Native Brazilian Portuguese female
}


def is_kokoro_tts_available() -> bool:
    """
    Check if `kokoro_onnx` is installed and the ONNX model + voice pack exist on disk.
    """
    if _KOKORO_INIT_FAILED:
        return False
    if not _DEFAULT_MODEL_PATH.exists() or not _DEFAULT_VOICES_PATH.exists():
        return False
    try:
        import kokoro_onnx  # noqa: F401
        return True
    except ImportError:
        return False


def resolve_kokoro_voice(
    niche_id: Optional[str] = None,
    edge_voice_hint: Optional[str] = None,
    language: str = "en",
) -> Tuple[str, str]:
    """
    Resolve `(kokoro_voice_id, kokoro_lang_code)` from channel niche and Edge-TTS voice hint.
    Returns e.g. `("am_michael", "en-us")` or `("pm_alex", "pt-br")`.
    """
    v_hint = (edge_voice_hint or "").lower()
    lang_low = (language or "en").lower()

    if v_hint.startswith("pt-") or lang_low.startswith("pt"):
        if any(f in v_hint for f in ("francisca", "thalita", "female", "dora")):
            return KOKORO_NICHE_VOICE_MAP["pt_br_female"], "pt-br"
        return KOKORO_NICHE_VOICE_MAP["pt_br_male"], "pt-br"

    niche = (niche_id or "").lower()
    if any(k in v_hint for k in ("ava", "aria", "jenny", "sonia", "libby")):
        return KOKORO_NICHE_VOICE_MAP["storyteller_female"], "en-us"
    if any(k in niche for k in ("animal", "rescue", "pets", "wholesome")):
        return KOKORO_NICHE_VOICE_MAP["storyteller_female"], "en-us"
    if any(k in niche for k in ("psych", "mind", "crime", "mystery", "noir")):
        return KOKORO_NICHE_VOICE_MAP["detective"], "en-gb"
    if any(k in niche for k in ("wildlife", "predator", "histor", "deep_sea", "space")):
        return KOKORO_NICHE_VOICE_MAP["deep_history"], "en-us"
    return KOKORO_NICHE_VOICE_MAP["default"], "en-us"


def _get_kokoro_engine() -> Optional[Any]:
    """Lazy-initialize and cache the `kokoro_onnx.Kokoro` instance."""
    global _KOKORO_INSTANCE, _KOKORO_INIT_FAILED
    if _KOKORO_INSTANCE is not None:
        return _KOKORO_INSTANCE
    if _KOKORO_INIT_FAILED:
        return None
    if not _DEFAULT_MODEL_PATH.exists() or not _DEFAULT_VOICES_PATH.exists():
        return None
    try:
        from kokoro_onnx import Kokoro
        _KOKORO_INSTANCE = Kokoro(str(_DEFAULT_MODEL_PATH), str(_DEFAULT_VOICES_PATH))
        logger.info(
            f"🎙️ [Kokoro-82M] Loaded local neural TTS model ({_DEFAULT_MODEL_PATH.name})"
        )
        return _KOKORO_INSTANCE
    except Exception as e:
        _KOKORO_INIT_FAILED = True
        logger.debug(f"[Kokoro-82M] Initialization skipped: {e}")
        return None


def _write_float32_wav(wav_path: str, samples: Any, sample_rate: int = 24000) -> bool:
    """Convert float32 [-1.0, 1.0] numpy array to 16-bit PCM WAV file without requiring soundfile."""
    try:
        import numpy as np
        arr = np.asarray(samples, dtype=np.float32).flatten()
        if arr.size < 128:
            return False
        arr = np.clip(arr, -1.0, 1.0)
        pcm16 = (arr * 32767.0).astype(np.int16)
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(sample_rate))
            wf.writeframes(pcm16.tobytes())
        return Path(wav_path).exists() and Path(wav_path).stat().st_size > 512
    except Exception as e:
        logger.debug(f"[Kokoro-82M] WAV write error: {e}")
        return False


def synthesize_with_kokoro_tts(
    text: str,
    output_mp3_path: str,
    niche_id: Optional[str] = None,
    edge_voice_hint: Optional[str] = None,
    language: str = "en",
    speed: float = 1.04,
) -> Tuple[bool, str]:
    """
    Synthesize speech locally using Kokoro-82M ONNX and output a 24kHz mono MP3.
    Returns `(success, voice_used)`.
    """
    v_hint = (edge_voice_hint or "").lower()
    if v_hint.startswith("vi-") or (language or "").lower().startswith("vi"):
        # Kokoro v1.0 does not natively support Vietnamese phonemes; defer to Edge-TTS
        return False, ""

    kokoro = _get_kokoro_engine()
    if kokoro is None:
        return False, ""

    voice_id, lang_code = resolve_kokoro_voice(
        niche_id=niche_id, edge_voice_hint=edge_voice_hint, language=language
    )
    tmp_wav = str(Path(output_mp3_path).with_suffix(".kokoro_raw.wav"))
    try:
        samples, sample_rate = kokoro.create(
            text,
            voice=voice_id,
            speed=float(max(0.85, min(1.30, speed))),
            lang=lang_code,
        )
        if not _write_float32_wav(tmp_wav, samples, sample_rate=int(sample_rate or 24000)):
            return False, voice_id

        from app.utils.ffmpeg_helper import run_ffmpeg
        run_ffmpeg(
            [
                "-i",
                tmp_wav,
                "-ar",
                "24000",
                "-ac",
                "1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                output_mp3_path,
            ],
            timeout=20,
        )
        out_p = Path(output_mp3_path)
        if out_p.exists() and out_p.stat().st_size > 1024:
            return True, voice_id
    except Exception as e:
        logger.debug(f"[Kokoro-82M] Synthesis error ({voice_id}): {e}")
    finally:
        Path(tmp_wav).unlink(missing_ok=True)

    return False, voice_id
