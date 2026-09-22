import asyncio
import edge_tts
from pathlib import Path
from config.settings import settings

class VoiceAgent:
    """Agent chịu trách nhiệm tạo giọng đọc AI từ văn bản bằng Edge-TTS (hoàn toàn miễn phí)."""

    def __init__(self, default_voice: str = None):
        self.default_voice = default_voice or settings.DEFAULT_TTS_VOICE

    async def generate_speech_async(self, text: str, output_path: str, voice: str = None) -> str:
        """
        Tạo file âm thanh MP3 từ văn bản.
        """
        selected_voice = voice or self.default_voice
        communicate = edge_tts.Communicate(text, selected_voice)
        await communicate.save(output_path)
        return output_path

    def generate_speech(self, text: str, output_path: str, voice: str = None) -> str:
        """Hàm đồng bộ (sync wrapper) để gọi khi cần"""
        return asyncio.run(self.generate_speech_async(text, output_path, voice))
