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
Style: Karaoke,{fontname},{fontsize},{primary},{secondary},{outline},{back},-1,0,0,0,100,100,1,0,1,3.2,2.0,2,30,30,{margin_v},1
Style: CTABadge,{fontname},46,&H0000FFFF,&H00FFFFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,1,0,1,3.5,2.0,2,30,30,340,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# ── Fonts ưu tiên (Arial phổ cập toàn diện trên Windows) ───────────────────
PREFERRED_FONTS = ["Arial", "Montserrat", "Segoe UI", "Arial Black", "Roboto"]


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
    def split_text_to_chunks(cls, text: str, max_words: int = 4, max_chars: int = 24) -> List[str]:
        """
        Chia một đoạn văn bản thành các cụm từ ngắn gọn (2-4 từ, tối đa 24 ký tự),
        chuẩn phong cách phụ đề Shorts hiện đại: hiển thị 1 dòng duy nhất, không rớt dòng,
        nhịp điệu thay đổi nhanh cuốn hút thị giác.
        """
        text = text.strip()
        # Normalize whitespace: collapse \n, \t, multiple spaces thành space đơn
        import re as _re
        text = _re.sub(r'\s+', ' ', text).strip()
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
                if word.endswith((".", "!", "?", ":", ";", "—")) and len(current_chunk) >= 2:
                    chunks.append(" ".join(current_chunk))
                    current_chunk = []

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        return chunks

    # ── ASS Generation ────────────────────────────────────────────────────

    @classmethod
    def _build_karaoke_text(cls, words: List[str], word_durations_cs: List[int]) -> str:
        """
        P2.2: Tạo chuỗi ASS kinetic karaoke với {\\kf} fluid tags theo từng từ.

        Cú pháp: {\\kfN} (centiseconds) đứng trước mỗi từ.
        - Trước khi đọc: từ hiển thị bằng SecondaryColour (trắng cơ sở)
        - Khi đọc đến: hiệu ứng {\\kf} quét màu mượt mà (fluid wipe) từ trái sang phải,
          chuyển sang PrimaryColour (vàng/highlight rực rỡ).
        FFmpeg burn_subtitles với libass render chuyển động mượt mà điện ảnh (không giật cục).
        """
        parts = []
        for i, (word, dur_cs) in enumerate(zip(words, word_durations_cs)):
            parts.append(f"{{\\kf{dur_cs}}}{word}")
        return " ".join(parts)

    @classmethod
    def build_ass_content(
        cls,
        scenes: List[SceneContract],
        fontname: str = "Arial",
        fontsize: int = 40,
        margin_v: int = 210,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        niche_id: Optional[str] = None,
    ) -> str:
        """
        Tạo nội dung file .ass chuẩn với karaoke word-level highlight.
        Tự động nạp font và màu sắc từ Niche Profile nếu niche_id được cung cấp.

        Mỗi chunk (2-4 từ) = 1 dialogue entry.
        Mỗi từ trong chunk có {\\k} tag để highlight lần lượt.
        Kích thước font: 40px (gọn gàng, thanh lịch, không che nhân vật).
        Lề đáy: 210px (nằm an toàn ở cuối video, trên thanh tiêu đề Shorts, giải phóng 85% khung hình).

        Returns:
            Chuỗi nội dung ASS hoàn chỉnh.
        """
        # Nếu có niche_id, tự động lấy cấu hình phụ đề từ Niche Profile
        eff_primary = primary_color or ASS_WHITE
        eff_secondary = secondary_color or ASS_YELLOW
        eff_outline = outline_color or ASS_BLACK
        eff_font = fontname
        eff_size = fontsize
        eff_margin = margin_v

        if niche_id:
            try:
                from app.services.niche_manager import get_niche, get_niche_subtitle_style
                niche_prof = get_niche(niche_id)
                niche_sub = get_niche_subtitle_style(niche_prof)
                eff_primary = primary_color or niche_sub.get("primary_ass", ASS_WHITE)
                eff_secondary = secondary_color or niche_sub.get("secondary_ass", ASS_YELLOW)
                eff_outline = outline_color or niche_sub.get("outline_ass", ASS_BLACK)
                if not fontname or fontname == "Arial":
                    eff_font = niche_sub.get("fontname", "Arial")
                eff_size = fontsize if fontsize != 40 else niche_sub.get("fontsize", 40)
                eff_margin = margin_v if margin_v != 210 else niche_sub.get("margin_v", 210)
            except Exception as e:
                pass

        header = ASS_HEADER_TEMPLATE.format(
            fontname=eff_font,
            fontsize=eff_size,
            primary=eff_secondary,     # PrimaryColour = màu highlight sau khi đọc (Vàng tươi/Gold/Neon)
            secondary=eff_primary,     # SecondaryColour = màu mặc định trước khi đọc (Trắng sáng)
            outline=eff_outline,
            back=ASS_SHADOW,
            margin_v=eff_margin,
        )

        dialogue_lines = []
        current_offset_ms = 0

        for scene in scenes:
            scene_duration_ms = scene.audio_duration_ms or int(scene.estimated_seconds * 1000)
            if scene_duration_ms <= 0:
                scene_duration_ms = 3000

            # Căn chỉnh timeline theo start_offset_ms nếu scene có mốc thời gian cố định
            if getattr(scene, "start_offset_ms", None) is not None:
                current_offset_ms = scene.start_offset_ms

            chunks = cls.split_text_to_chunks(scene.narration)
            if not chunks:
                if getattr(scene, "start_offset_ms", None) is not None:
                    current_offset_ms = scene.start_offset_ms + scene_duration_ms
                else:
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

            # Cân chỉnh lại tổng duration — giữ last chunk >= 300ms (tránh âm)
            diff = scene_duration_ms - sum(chunk_durations_ms)
            if chunk_durations_ms:
                chunk_durations_ms[-1] = max(chunk_durations_ms[-1] + diff, 300)

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

            if getattr(scene, "start_offset_ms", None) is not None:
                current_offset_ms = scene.start_offset_ms + scene_duration_ms
            else:
                current_offset_ms += scene_duration_ms

        # ── Kích hoạt Visual CTA Banner ở phân cảnh cuối (Center Above Subtitles) ──
        if scenes and current_offset_ms > 3000:
            last_scene = scenes[-1]
            last_dur = last_scene.audio_duration_ms or int(last_scene.estimated_seconds * 1000)
            cta_start_ms = max(0, current_offset_ms - min(last_dur, 4500))
            cta_end_ms = current_offset_ms
            full_text = " ".join([s.narration for s in scenes])
            is_vi = any(c in full_text.lower() for c in "àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ")
            cta_text = "🔔 ĐĂNG KÝ KÊNH ĐÓN XEM PHẦN 2 🔔" if is_vi else "🔔 SUBSCRIBE FOR PART 2 🔔"
            cta_start_str = cls._ms_to_ass_time(cta_start_ms)
            cta_end_str = cls._ms_to_ass_time(cta_end_ms)
            dialogue_lines.append(
                f"Dialogue: 1,{cta_start_str},{cta_end_str},CTABadge,,0,0,0,,{{\\fade(250,150)\\t(0,250,\\fscx110\\fscy110)\\t(250,500,\\fscx100\\fscy100)}}{cta_text}"
            )

        return header + "\n".join(dialogue_lines) + "\n"

    @classmethod
    def save_ass_file(
        cls,
        scenes: List[SceneContract],
        output_path: str,
        fontname: str = "Arial",
        fontsize: int = 40,
        margin_v: int = 210,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        niche_id: Optional[str] = None,
    ) -> str:
        """Tạo và lưu file .ass ra ổ đĩa, hỗ trợ Niche Profile."""
        content = cls.build_ass_content(
            scenes,
            fontname=fontname,
            fontsize=fontsize,
            margin_v=margin_v,
            primary_color=primary_color,
            secondary_color=secondary_color,
            outline_color=outline_color,
            niche_id=niche_id,
        )
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

            # Cân chỉnh tổng duration — giữ last chunk >= 300ms (tránh âm)
            diff = scene_duration_ms - sum(chunk_durations)
            if chunk_durations:
                chunk_durations[-1] = max(chunk_durations[-1] + diff, 300)

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
