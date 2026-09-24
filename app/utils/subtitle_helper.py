"""
SubtitleHelper — Tạo phụ đề chuẩn YouTube Shorts.

Hỗ trợ 2 định dạng:
  1. ASS (Advanced SubStation Alpha) — Mặc định cho video mới.
     - Font Montserrat/Roboto Bold, size 18–22, viền đen tương phản cao
     - Word-level Karaoke: highlight màu vàng → đỏ theo từng từ khi phát
     - Căn giữa đáy màn hình, cách đáy 180px (an toàn khỏi UI Shorts)
  2. SRT (legacy) — Dùng để tương thích ngược với các hệ thống cũ.

Cả hai format đều tính toán timestamp lũy kế (cumulative timeline) chính xác.
"""
import re
from pathlib import Path
from typing import List, Optional
from datetime import timedelta

from app.schemas.contracts import SceneContract


# ── Màu sắc ASS (ABGR hex, bắt đầu bằng &H) ──────────────────────────────
# ASS format: &HAABBGGRR (alpha, blue, green, red)
ASS_WHITE       = "&H00FFFFFF"   # Trắng sáng — màu chữ mặc định (chưa highlight)
ASS_YELLOW      = "&H0000FFFF"   # Vàng tươi rực #FFFF00 — từ đang đọc (karaoke active)
ASS_RED         = "&H000000FF"   # Đỏ rực — từ đã đọc xong (karaoke past)
ASS_BLACK       = "&H00000000"   # Đen — viền chữ (tương phản cao trên mọi nền)
ASS_SHADOW      = "&HA0000000"   # Đen 63% trong suốt — shadow mềm mại

# ── ASS Style template ────────────────────────────────────────────────────
# Thứ tự field: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour,
#               OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut,
#               ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,
#               Alignment, MarginL, MarginR, MarginV, Encoding
ASS_HEADER_TEMPLATE = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
YCbCr Matrix: None

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{fontname},{fontsize},{primary},{secondary},{outline},{back},-1,0,0,0,100,100,1,0,1,4,2,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# ── Fonts ưu tiên ─────────────────────────────────────────────────────────
PREFERRED_FONTS = ["Montserrat", "Arial Black", "The Bold Font", "Roboto", "Arial"]


class SubtitleHelper:
    """Helper xử lý và tạo phụ đề video — ASS (karaoke) + SRT (legacy)."""

    # ── Utilities ─────────────────────────────────────────────────────────

    @staticmethod
    def _ms_to_ass_time(ms: int) -> str:
        """Chuyển milliseconds → ASS timestamp H:MM:SS.cc (centiseconds)."""
        total_cs = ms // 10  # centiseconds
        cs = total_cs % 100
        total_s = total_cs // 100
        hours = total_s // 3600
        minutes = (total_s % 3600) // 60
        seconds = total_s % 60
        return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"

    @staticmethod
    def _ms_to_srt_time(ms: int) -> str:
        """Chuyển milliseconds → SRT timestamp HH:MM:SS,mmm."""
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

    # ── ASS Generation ────────────────────────────────────────────────────

    @classmethod
    def _build_karaoke_text(cls, words: List[str], word_durations_cs: List[int]) -> str:
        """
        Tạo chuỗi ASS karaoke với {\\k} tags theo từng từ.

        Cú pháp: {\\kN} (centiseconds) đứng trước mỗi từ.
        Primary color = white (chưa đọc đến) → Yellow (đang đọc) → Red (đã đọc).
        FFmpeg burn_subtitles hỗ trợ \\k karaoke khi render với libass.
        """
        parts = []
        for i, (word, dur_cs) in enumerate(zip(words, word_durations_cs)):
            parts.append(f"{{\\k{dur_cs}}}{word}")
        return " ".join(parts)

    @classmethod
    def build_ass_content(
        cls,
        scenes: List[SceneContract],
        fontname: str = "Montserrat",
        fontsize: int = 64,
        margin_v: int = 380,
    ) -> str:
        """
        Tạo nội dung file .ass chuẩn với karaoke word-level highlight.

        Mỗi chunk (3-5 từ) = 1 dialogue entry.
        Mỗi từ trong chunk có {\\k} tag để highlight lần lượt.

        Returns:
            Chuỗi nội dung ASS hoàn chỉnh.
        """
        header = ASS_HEADER_TEMPLATE.format(
            fontname=fontname,
            fontsize=fontsize,
            primary=ASS_WHITE,
            secondary=ASS_YELLOW,   # SecondaryColour = màu karaoke đang đọc
            outline=ASS_BLACK,
            back=ASS_SHADOW,
            margin_v=margin_v,
        )

        dialogue_lines = []
        current_offset_ms = 0

        for scene in scenes:
            scene_duration_ms = scene.audio_duration_ms or int(scene.estimated_seconds * 1000)
            if scene_duration_ms <= 0:
                scene_duration_ms = 3000

            chunks = cls.split_text_to_chunks(scene.narration)
            if not chunks:
                current_offset_ms += scene_duration_ms
                continue

            # Chuyển ALL CAPS — phong cách phiếu phụ đề Shorts viral
            chunks = [c.upper() for c in chunks]

            # Phân bổ thời lượng cho từng chunk theo tỷ lệ ký tự
            total_chars = sum(len(c) for c in chunks)
            chunk_durations_ms = []
            for c in chunks:
                ratio = len(c) / total_chars if total_chars > 0 else 1.0 / len(chunks)
                dur = int(scene_duration_ms * ratio)
                chunk_durations_ms.append(max(dur, 600))

            # Cân chỉnh lại tổng duration
            diff = scene_duration_ms - sum(chunk_durations_ms)
            if chunk_durations_ms:
                chunk_durations_ms[-1] += diff

            chunk_start_ms = current_offset_ms
            for chunk, chunk_dur_ms in zip(chunks, chunk_durations_ms):
                chunk_end_ms = chunk_start_ms + chunk_dur_ms
                words = chunk.split()

                # Phân bổ thời lượng theo từ (theo số ký tự của từng từ)
                total_word_chars = sum(len(w) for w in words)
                word_durations_cs = []
                for w in words:
                    ratio = len(w) / total_word_chars if total_word_chars > 0 else 1.0 / len(words)
                    dur_cs = max(int(chunk_dur_ms * ratio / 10), 5)  # centiseconds, tối thiểu 5cs
                    word_durations_cs.append(dur_cs)

                # Cân chỉnh tổng word duration bằng chunk duration
                total_word_cs = sum(word_durations_cs)
                chunk_cs = chunk_dur_ms // 10
                if word_durations_cs:
                    word_durations_cs[-1] += (chunk_cs - total_word_cs)
                    word_durations_cs[-1] = max(word_durations_cs[-1], 5)

                karaoke_text = cls._build_karaoke_text(words, word_durations_cs)

                start_str = cls._ms_to_ass_time(chunk_start_ms)
                end_str = cls._ms_to_ass_time(chunk_end_ms)

                # Dialogue: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
                dialogue_lines.append(
                    f"Dialogue: 0,{start_str},{end_str},Karaoke,,0,0,0,,{karaoke_text}"
                )

                chunk_start_ms = chunk_end_ms

            current_offset_ms += scene_duration_ms

        return header + "\n".join(dialogue_lines) + "\n"

    @classmethod
    def save_ass_file(
        cls,
        scenes: List[SceneContract],
        output_path: str,
        fontname: str = "Montserrat",
        fontsize: int = 64,
        margin_v: int = 380,
    ) -> str:
        """Tạo và lưu file .ass ra ổ đĩa."""
        content = cls.build_ass_content(scenes, fontname=fontname, fontsize=fontsize, margin_v=margin_v)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return output_path

    # ── SRT Generation (Legacy) ───────────────────────────────────────────

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

            total_chars = sum(len(c) for c in chunks)
            chunk_durations = []
            for c in chunks:
                ratio = len(c) / total_chars if total_chars > 0 else 1.0 / len(chunks)
                dur = int(scene_duration_ms * ratio)
                chunk_durations.append(max(dur, 600))

            diff = scene_duration_ms - sum(chunk_durations)
            if chunk_durations:
                chunk_durations[-1] += diff

            chunk_start = current_offset_ms
            for i, chunk in enumerate(chunks):
                chunk_dur = chunk_durations[i]
                chunk_end = chunk_start + chunk_dur

                start_str = cls._ms_to_srt_time(chunk_start)
                end_str = cls._ms_to_srt_time(chunk_end)

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
        """Tạo và lưu file .srt ra ổ đĩa (legacy, backward-compatible)."""
        content = cls.build_srt_content(scenes)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return output_path

    # ── Unified Save (ASS preferred, SRT fallback) ────────────────────────

    @classmethod
    def save_subtitle_file(
        cls,
        scenes: List[SceneContract],
        output_path: str,
        prefer_ass: bool = True,
    ) -> str:
        """
        Lưu phụ đề ở định dạng phù hợp nhất.
        
        Nếu prefer_ass=True và output_path không phải .srt:
          → Lưu định dạng ASS với karaoke
        Nếu output_path kết thúc bằng .srt hoặc prefer_ass=False:
          → Lưu SRT legacy
        
        Returns:
            Đường dẫn thực tế của file đã lưu.
        """
        p = Path(output_path)
        if prefer_ass and p.suffix.lower() != ".srt":
            # Đảm bảo extension là .ass
            ass_path = str(p.with_suffix(".ass"))
            return cls.save_ass_file(scenes, ass_path)
        else:
            return cls.save_srt_file(scenes, output_path)
