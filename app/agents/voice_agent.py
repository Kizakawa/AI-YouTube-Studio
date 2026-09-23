import asyncio
import logging
import subprocess
import json
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

    # ──────────────────────────────────────────
    # PUBLIC API — Gọi từ Pipeline Orchestrator
    # ──────────────────────────────────────────

    async def generate_per_scene_audio(
        self,
        scenes: List[SceneContract],
        output_dir: str,
        voice: str = None
    ) -> List[SceneContract]:
        """
        Sinh MP3 cho từng scene. Trả về list SceneContract đã enriched
        với audio_path và audio_duration_ms.
        """
        selected_voice = voice or self.default_voice
        enriched_scenes = []

        for scene in scenes:
            scene_audio_path = str(Path(output_dir) / f"scene_{scene.scene_id:03d}.mp3")

            # Sinh audio cho 1 scene
            communicate = edge_tts.Communicate(scene.narration, selected_voice)
            await communicate.save(scene_audio_path)

            # Đo duration thực bằng ffprobe
            duration_ms = self._get_audio_duration_ms(scene_audio_path)

            # Enriched scene với actual timing
            enriched = scene.model_copy(update={
                "audio_path": scene_audio_path,
                "audio_duration_ms": duration_ms,
            })
            enriched_scenes.append(enriched)

            logger.info(
                f"Scene {scene.scene_id}: audio={duration_ms}ms "
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
        # Tạo file list cho ffmpeg concat
        list_file = str(Path(output_path).parent / "audio_concat_list.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for scene in scenes:
                if not scene.audio_path:
                    raise ValueError(f"Scene {scene.scene_id} chưa có audio_path")
                # Escape single quotes trong path
                safe_path = scene.audio_path.replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        # FFmpeg concat demuxer
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            output_path
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg concat audio failed: {proc.stderr}")

        total_ms = self._get_audio_duration_ms(output_path)
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
        selected_voice = voice or self.default_voice
        communicate = edge_tts.Communicate(text, selected_voice)
        await communicate.save(output_path)
        return output_path

    # ──────────────────────────────────────────
    # PRIVATE HELPERS
    # ──────────────────────────────────────────

    @staticmethod
    def _get_audio_duration_ms(file_path: str) -> int:
        """Đo chính xác duration bằng ffprobe (ms). Fallback = 0."""
        try:
            cmd = [
                "ffprobe",
                "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "json",
                file_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            data = json.loads(result.stdout)
            duration_sec = float(data["format"]["duration"])
            return int(duration_sec * 1000)
        except Exception as e:
            logger.warning(f"Không thể đo duration cho {file_path}: {e}")
            return 0
