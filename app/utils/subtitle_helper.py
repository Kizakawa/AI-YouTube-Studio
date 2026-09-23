"""
SubtitleHelper — Tạo và đồng bộ phụ đề SRT chuẩn phong cách YouTube Shorts.

Tính năng:
- Ngắt lời thoại thành các cụm từ ngắn gọn (3-6 từ/dòng), dễ đọc trên điện thoại.
- Tính toán timestamp lũy kế (cumulative timeline) chính xác qua từng scene.
- Phù hợp cả tiếng Việt (có dấu) và tiếng Anh.
- Định dạng SRT chuẩn cho FFmpeg hardsub burn-in.
"""
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import timedelta

from app.schemas.contracts import SceneContract


class SubtitleHelper:
    """Helper xử lý và tạo phụ đề video."""

    @staticmethod
    def format_srt_timestamp(ms: int) -> str:
        """Chuyển milliseconds thành định dạng SRT HH:MM:SS,mmm."""
        td = timedelta(milliseconds=ms)
        total_seconds = int(td.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        millis = ms % 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    @classmethod
    def split_text_to_chunks(cls, text: str, max_words: int = 5, max_chars: int = 32) -> List[str]:
        """
        Chia một đoạn văn bản thành các cụm từ ngắn gọn (3-5 từ),
        chuẩn phong cách phụ đề Shorts cuốn hút, không bị tràn màn hình.
        """
        text = text.strip()
        if not text:
            return []

        words = text.split()
        if len(words) <= max_words and len(text) <= max_chars:
            return [text]

        chunks = []
        current_chunk = []

        for word in words:
            candidate = " ".join(current_chunk + [word])
            # Ngắt nếu vượt số từ, vượt độ dài ký tự hoặc gặp dấu câu mạnh
            if (len(current_chunk) >= max_words or len(candidate) > max_chars) and current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = [word]
            else:
                current_chunk.append(word)
                if word.endswith((".", "!", "?", ":", ";", "—")) and len(current_chunk) >= 3:
                    chunks.append(" ".join(current_chunk))
                    current_chunk = []

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        return chunks

    @classmethod
    def build_srt_content(cls, scenes: List[SceneContract]) -> str:
        """
        Tạo nội dung SRT từ danh sách SceneContract đã có audio timing.
        Đảm bảo timeline liên tục, không bị đè timestamp.
        """
        srt_lines = []
        cue_index = 1
        current_offset_ms = 0

        for scene in scenes:
            scene_duration_ms = scene.audio_duration_ms or int(scene.estimated_seconds * 1000)
            if scene_duration_ms <= 0:
                scene_duration_ms = 3000

            chunks = cls.split_text_to_chunks(scene.narration)
            if not chunks:
                current_offset_ms += scene_duration_ms
                continue

            # Phân bổ thời lượng cho từng chunk theo tỷ lệ độ dài ký tự
            total_chars = sum(len(c) for c in chunks)
            chunk_durations = []
            for c in chunks:
                ratio = len(c) / total_chars if total_chars > 0 else 1.0 / len(chunks)
                dur = int(scene_duration_ms * ratio)
                chunk_durations.append(max(dur, 600))  # Ít nhất 600ms cho 1 phụ đề

            # Cân chỉnh lại tổng duration để khớp đúng scene_duration_ms
            diff = scene_duration_ms - sum(chunk_durations)
            if chunk_durations:
                chunk_durations[-1] += diff

            chunk_start = current_offset_ms
            for i, chunk in enumerate(chunks):
                chunk_dur = chunk_durations[i]
                chunk_end = chunk_start + chunk_dur

                start_str = cls.format_srt_timestamp(chunk_start)
                end_str = cls.format_srt_timestamp(chunk_end)

                srt_lines.append(f"{cue_index}")
                srt_lines.append(f"{start_str} --> {end_str}")
                srt_lines.append(chunk)
                srt_lines.append("")

                cue_index += 1
                chunk_start = chunk_end

            current_offset_ms += scene_duration_ms

        return "\n".join(srt_lines)

    @classmethod
    def save_srt_file(cls, scenes: List[SceneContract], output_path: str) -> str:
        """Tạo và lưu file .srt ra ổ đĩa."""
        content = cls.build_srt_content(scenes)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return output_path
