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
from typing import List, Optional, Dict, Any
from datetime import timedelta

from app.schemas.contracts import SceneContract


# ── Màu sắc ASS (ABGR hex, bắt đầu bằng &H) ──────────────────────────────
# ASS format: &HAABBGGRR (alpha, blue, green, red)
ASS_WHITE       = "&H00FFFFFF"   # Trắng sáng — màu chữ mặc định (chưa highlight)
ASS_YELLOW      = "&H0000FFFF"   # Vàng tươi rực #FFFF00 — từ đang đọc (karaoke active)
ASS_RED         = "&H000000FF"   # Đỏ rực — từ đã đọc xong (karaoke past)
ASS_BLACK       = "&H00000000"   # Đen — viền chữ (tương phản cao trên mọi nền)
ASS_SHADOW      = "&HA0000000"   # Đen 63% trong suốt — shadow mềm mại

# ── Bộ từ khóa cảm xúc mạnh kích thích thị giác (Hypnotic Power Words) ─────
POWER_WORDS = {
    # English Power Words (Emotional triggers & High retention)
    "NEVER", "ALWAYS", "SECRET", "SECRETS", "DANGER", "DANGEROUS", "DEADLY",
    "TRUTH", "SHOCKING", "DISASTER", "MONSTER", "KILLER", "MISTAKE", "WHY",
    "WARNING", "STOP", "DESTROY", "ATTACK", "FATAL", "PREDATOR", "FEAR",
    "TERRIFYING", "HORROR", "TRAPPED", "ABSOLUTELY", "INSANE", "IMPOSSIBLE",
    "UNBELIEVABLE", "BEAST", "LETHAL", "POISON", "VENOM", "MYSTERY", "DISCOVERED",
    "HIDDEN", "REVEALED", "LIES", "DECEPTION", "DARK", "BLOOD", "WAR", "DEATH",
    "RULE", "RULES", "LAW", "ESCAPE", "SURVIVE", "SURVIVAL", "URGENT",
    # Vietnamese Power Words (Từ kích hoạt cảm xúc mạnh & Giữ chân người xem)
    "ĐỪNG", "KHÔNG", "BAO GIỜ", "BÍ MẬT", "NGUY HIỂM", "CHẾT", "TỬ THẦN",
    "SỰ THẬT", "SỐC", "KINH HOÀNG", "CẢNH BÁO", "DỪNG LẠI", "TIÊU DIỆT",
    "TẤN CÔNG", "SÁT THỦ", "QUÁI VẬT", "ÁC MỘNG", "SỢ HÃI", "HOẢNG LOẠN",
    "MẮC KẸT", "TUYỆT ĐỐI", "ĐIÊN RỒ", "BẤT KHẢ THI", "KHÔNG THỂ TIN",
    "DÃ THÚ", "NỌC ĐỘC", "BÍ ẨN", "HÉ LỘ", "DỐI TRÁ", "THAO TÚNG", "ĐEN TỐI",
    "MÁU", "CHIẾN TRANH", "ĐỘT NHIÊN", "KHỔNG LỒ", "ĐÁNG SỢ", "KHỦNG KHIẾP",
    "TRỪNG PHẠT", "BẪY", "HẬU QUẢ", "BÍ TRUYỀN", "BẤT NGỜ", "CẤM", "SINH TỒN",
    # Các âm tiết cốt lõi đơn để khớp phụ đề từng từ (Syllable Level Matching)
    "BÍ", "MẬT", "NGUY", "HIỂM", "CẢNH", "BÁO", "TỬ", "THẦN", "SỰ", "THẬT",
    "DỐI", "TRÁ", "THAO", "TÚNG", "KINH", "HOÀNG", "QUÁI", "VẬT", "ÁC", "MỘNG",
    "SỢ", "HÃI", "MẮC", "KẸT"
}

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
Style: Karaoke,{fontname},{fontsize},{primary},{secondary},{outline},{back},-1,0,0,0,100,100,1,0,1,4.2,2.2,2,30,30,{margin_v},1
Style: PatternInterruptBadge,{fontname},30,&H00FFFFFF,&H00FFFFFF,&H0000E6FF&,&HC0101010,-1,0,0,0,100,100,2,0,3,1.8,0.0,8,40,40,280,1
Style: CTABadge,{fontname},46,&H0000FFFF,&H00FFFFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,1,0,1,3.5,2.0,2,30,30,780,1
Style: WatermarkStyle,Arial,28,&H30FFFFFF,&H30FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,1.8,1.2,8,30,30,90,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# ── Fonts ưu tiên (Arial Black / Arial phổ cập toàn diện trên Windows) ───────
PREFERRED_FONTS = ["Arial Black", "Montserrat", "Segoe UI", "Arial", "Roboto"]


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

    @staticmethod
    def sanitize_subtitle_text(text: str) -> str:
        """
        Khử toàn bộ chuỗi rác template, placeholder, âm thanh tự sinh YouTube:
        - Loại bỏ [Music], [Âm nhạc], [Applause], [Tiếng cười], (Laughter), v.v.
        - Loại bỏ placeholder dạng {hook_quote}, {default_...}, v.v.
        - Loại bỏ từ khóa dummy: lorem ipsum, placeholder, scene X
        """
        if not text:
            return ""
        # Loại bỏ các tag âm thanh [Music], [Âm nhạc], [Applause], ...
        t = re.sub(r'\[(?:Music|Âm nhạc|Applause|Laughter|Tiếng cười|Vỗ tay|Cheering|SFX|Audio)\]', '', text, flags=re.IGNORECASE)
        t = re.sub(r'\((?:Music|Âm nhạc|Applause|Laughter|Tiếng cười|Vỗ tay|Cheering|SFX|Audio)\)', '', t, flags=re.IGNORECASE)
        # Loại bỏ các biến placeholder dạng {hook_quote}, {default_...}
        t = re.sub(r'\{[^{}]*\}', '', t)
        # Loại bỏ các từ khóa dummy/rác
        t = re.sub(r'\b(?:placeholder|dummy text|lorem ipsum|scene \d+)\b', '', t, flags=re.IGNORECASE)
        # Chuẩn hóa khoảng trắng
        t = re.sub(r'\s+', ' ', t).strip()
        return t

    # ── ASS Generation ────────────────────────────────────────────────────

    @classmethod
    def _build_karaoke_text(
        cls,
        words: List[str],
        word_durations_cs: List[int],
        default_highlight: str = "&H0000FFFF",
        power_color: str = "&H000000FF",
    ) -> str:
        """
        P2.2: Tạo chuỗi ASS kinetic karaoke với {\\kf} fluid tags theo từng từ.

        Cú pháp: {\\kfN} (centiseconds) đứng trước mỗi từ.
        - Trước khi đọc: từ hiển thị bằng SecondaryColour (trắng cơ sở)
        - Khi đọc đến: hiệu ứng {\\kf} quét màu mượt mà (fluid wipe) từ trái sang phải,
          chuyển sang PrimaryColour (vàng/highlight rực rỡ).
        Tự động nhận diện và làm nổi bật các Power Words bằng màu sắc kích thích thị giác
        (Crimson Red / Neon Accent) tạo cảm giác thôi miên (Hypnotic Kinetic Typography).
        FFmpeg burn_subtitles với libass render chuyển động mượt mà điện ảnh (không giật cục).
        """
        # Chuẩn hóa cú pháp màu ASS (luôn kết thúc bằng &)
        norm_power = power_color if power_color.endswith("&") else f"{power_color}&"
        norm_highlight = default_highlight if default_highlight.endswith("&") else f"{default_highlight}&"

        parts = []
        for i, (word, dur_cs) in enumerate(zip(words, word_durations_cs)):
            clean_word = re.sub(r'[^\w\d]', '', word.upper())
            is_power_word = clean_word in POWER_WORDS

            if is_power_word:
                # Power Word: đổi màu highlight thành power_color rực rỡ, rồi reset về default_highlight
                parts.append(f"{{\\kf{dur_cs}\\1c{norm_power}\\b1}}{word}{{\\1c{norm_highlight}}}")
            else:
                parts.append(f"{{\\kf{dur_cs}}}{word}")
        return " ".join(parts)

    @classmethod
    def build_ass_content(
        cls,
        scenes: List[SceneContract],
        fontname: str = "Arial Black",
        fontsize: int = 42,
        margin_v: int = 580,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        niche_id: Optional[str] = None,
        credit_text: Optional[str] = None,
        show_cta_badge: bool = False,
    ) -> str:
        """
        Tạo nội dung file .ass chuẩn với karaoke word-level highlight.
        Tự động nạp font và màu sắc từ Niche Profile nếu niche_id được cung cấp.

        Mỗi chunk (2-4 từ) = 1 dialogue entry.
        Mỗi từ trong chunk có {\\kf} tag để highlight lần lượt.
        Kích thước font: 42px.
        Lề đáy: 580px (Safe Zone chuẩn YouTube Shorts: cách đáy ~30.2%, hoàn toàn không bị che bởi tiêu đề, tên kênh, nút Subscribe).

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
                eff_size = fontsize if fontsize != 42 else niche_sub.get("fontsize", 42)
                eff_margin = margin_v if margin_v != 580 else niche_sub.get("margin_v", 580)
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

                # Chọn màu sắc cho Power Words theo Niche
                raw_niche = (niche_id or "").lower()
                if "scifi" in raw_niche:
                    power_color = "&H00FFFF00"  # Neon Cyan
                elif "rescue" in raw_niche:
                    power_color = "&H002080FF"  # Vivid Warm Amber / Orange
                else:
                    power_color = "&H000000FF"  # Vivid Crimson Red (High Urgency & Dopamine)

                karaoke_text = cls._build_karaoke_text(
                    words,
                    word_durations_cs,
                    default_highlight=eff_secondary,
                    power_color=power_color,
                )

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

        # ── Kích hoạt Investigative Evidence Badge ở Scene 1 & Mid-Scene (Pattern Interrupt Vox / Magnates Media) ──
        if scenes:
            raw_niche = (niche_id or "").lower()
            full_txt = " ".join([s.narration for s in scenes]).lower()
            is_vi = any(c in full_txt for c in "àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ")

            if "psychology" in raw_niche:
                badge_title = "[ 🧠 TÂM LÝ ĐEN TỐI ]" if is_vi else "[ 🧠 MENTAL TRAP ]"
            elif any(k in raw_niche for k in ("deep_sea", "ocean")):
                badge_title = "[ 🌊 VỰC THẲM BÍ ẨN ]" if is_vi else "[ 🌊 UNCHARTED ABYSS ]"
            elif any(k in raw_niche for k in ("predator", "wildlife")):
                badge_title = "[ 🦁 QUY LUẬT SĂN MỒI ]" if is_vi else "[ 🦁 APEX PREDATOR ]"
            elif any(k in raw_niche for k in ("rescue", "animal")):
                badge_title = "[ 🐾 HỒ SƠ CỨU HỘ ]" if is_vi else "[ 🐾 RESCUE ARCHIVE ]"
            elif any(k in raw_niche for k in ("history", "mystery")):
                badge_title = "[ 📜 TÀI LIỆU MẬT ]" if is_vi else "[ 📜 CLASSIFIED FILE ]"
            elif "horror" in raw_niche:
                badge_title = "[ ⚠️ CHUYỆN KINH DỊ ]" if is_vi else "[ ⚠️ URBAN LEGEND ]"
            else:
                badge_title = "[ ⚠️ ĐIỀU CHƯA BIẾT ]" if is_vi else "[ ⚠️ CRITICAL NOTICE ]"

            first_scene_dur_ms = scenes[0].audio_duration_ms or int(scenes[0].estimated_seconds * 1000)
            badge_end_ms = min(2600, max(1200, first_scene_dur_ms - 200))
            badge_start_str = cls._ms_to_ass_time(200)
            badge_end_str = cls._ms_to_ass_time(badge_end_ms)
            dialogue_lines.insert(0,
                f"Dialogue: 2,{badge_start_str},{badge_end_str},PatternInterruptBadge,,0,0,0,,{{\\fade(150,150)\\t(0,150,\\fscx106\\fscy106)}}{badge_title}"
            )

            # Mid-video evidence stamp nếu phân cảnh sau có từ khóa chứng thực
            evidence_kw = ("bằng chứng", "sự thật", "dấu hiệu", "nghiên cứu", "quy tắc", "evidence", "truth", "sign", "proof", "rule")
            for idx, sc in enumerate(scenes[1:], start=2):
                sc_txt = sc.narration.lower()
                if any(k in sc_txt for k in evidence_kw):
                    sc_start_ms = getattr(sc, "start_offset_ms", None)
                    if sc_start_ms is None:
                        sc_start_ms = sum(s.audio_duration_ms or int(s.estimated_seconds * 1000) for s in scenes[:idx-1])
                    sc_dur = sc.audio_duration_ms or int(sc.estimated_seconds * 1000)
                    stamp_end_ms = sc_start_ms + min(2200, sc_dur)
                    stamp_start_str = cls._ms_to_ass_time(sc_start_ms + 100)
                    stamp_end_str = cls._ms_to_ass_time(stamp_end_ms)
                    stamp_title = f"[ 🔍 BẰNG CHỨNG #{idx} ]" if is_vi else f"[ 🔍 EVIDENCE #{idx} ]"
                    dialogue_lines.append(
                        f"Dialogue: 2,{stamp_start_str},{stamp_end_str},PatternInterruptBadge,,0,0,0,,{{\\fade(150,150)\\t(0,150,\\fscx105\\fscy105)}}{stamp_title}"
                    )
                    break

        # ── Kích hoạt Visual CTA Banner ở phân cảnh cuối (Chỉ bật khi show_cta_badge=True, không làm gãy Infinite Loop) ──
        if show_cta_badge and scenes and current_offset_ms > 3000:
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

        # ── Kích hoạt Watermark / Credit tác giả trên đỉnh video nếu có ──
        if credit_text and credit_text.strip():
            credit_clean = credit_text.strip().replace("\n", " ")
            credit_end_str = cls._ms_to_ass_time(current_offset_ms if current_offset_ms > 0 else 60000)
            dialogue_lines.insert(0,
                f"Dialogue: 2,0:00:00.00,{credit_end_str},WatermarkStyle,,0,0,0,,{{\\fade(300,300)}}🎬 {credit_clean}"
            )

        return header + "\n".join(dialogue_lines) + "\n"

    @classmethod
    def save_ass_file(
        cls,
        scenes: List[SceneContract],
        output_path: str,
        fontname: str = "Arial Black",
        fontsize: int = 42,
        margin_v: int = 580,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        niche_id: Optional[str] = None,
        credit_text: Optional[str] = None,
        show_cta_badge: bool = False,
    ) -> str:
        """Tạo và lưu file .ass ra ổ đĩa, hỗ trợ Niche Profile & Watermark Credit."""
        content = cls.build_ass_content(
            scenes,
            fontname=fontname,
            fontsize=fontsize,
            margin_v=margin_v,
            primary_color=primary_color,
            secondary_color=secondary_color,
            outline_color=outline_color,
            niche_id=niche_id,
            credit_text=credit_text,
            show_cta_badge=show_cta_badge,
        )
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return output_path

    @classmethod
    def extract_power_word_cues(
        cls,
        scenes: List[SceneContract],
        min_interval_s: float = 2.5,
    ) -> List[tuple]:
        """
        Level 4 Multi-Layer Sound Design:
        Trích xuất các mốc thời gian xuất hiện của Power Words để làm Micro-SFX Foley (Text-Pop).
        Trả về danh sách (timestamp_seconds, sfx_cue, volume):
          - Từ khóa đe dọa/nguy hiểm: impact_sub (vol 0.24)
          - Từ khóa lừa dối/bẫy: glitch (vol 0.22)
          - Từ khóa bí mật/sự thật: reveal (vol 0.22)
          - Mặc định: whoosh_fast (vol 0.20)
        Debounce min_interval_s để nhịp âm thanh tinh tế, không dồn dập.
        """
        cues = []
        current_offset_ms = 0
        last_ts_s = -999.0

        for scene in scenes:
            scene_duration_ms = scene.audio_duration_ms or int(scene.estimated_seconds * 1000)
            if scene_duration_ms <= 0:
                scene_duration_ms = 3000

            if getattr(scene, "start_offset_ms", None) is not None:
                current_offset_ms = scene.start_offset_ms

            chunks = cls.split_text_to_chunks(scene.narration)
            if not chunks:
                current_offset_ms += scene_duration_ms
                continue

            chunks = [c.upper() for c in chunks]
            total_chars = sum(len(c) for c in chunks)
            chunk_durations_ms = []
            for c in chunks:
                ratio = len(c) / total_chars if total_chars > 0 else 1.0 / len(chunks)
                dur = int(scene_duration_ms * ratio)
                chunk_durations_ms.append(max(dur, 600))

            diff = scene_duration_ms - sum(chunk_durations_ms)
            if chunk_durations_ms:
                chunk_durations_ms[-1] = max(chunk_durations_ms[-1] + diff, 300)

            chunk_start_ms = current_offset_ms
            for chunk, chunk_dur_ms in zip(chunks, chunk_durations_ms):
                words = chunk.split()
                total_word_chars = sum(len(w) for w in words)
                word_durations_cs = []
                for w in words:
                    ratio = len(w) / total_word_chars if total_word_chars > 0 else 1.0 / len(words)
                    dur_cs = max(int(chunk_dur_ms * ratio / 10), 5)
                    word_durations_cs.append(dur_cs)

                word_offset_ms = 0
                for w, dur_cs in zip(words, word_durations_cs):
                    clean_word = re.sub(r'[^\w\d]', '', w.upper())
                    if clean_word in POWER_WORDS:
                        word_ts_s = (chunk_start_ms + word_offset_ms) / 1000.0
                        if word_ts_s - last_ts_s >= min_interval_s and word_ts_s >= 0.5:
                            if any(k in clean_word for k in ("NGUY", "HIỂM", "CHẾT", "DANGER", "DEADLY", "KILL", "TỬ")):
                                sfx_name = "impact_sub"
                                vol = 0.24
                            elif any(k in clean_word for k in ("BẪY", "DỐI", "THAO", "TÚNG", "LIES", "TRAP", "MISTAKE")):
                                sfx_name = "glitch"
                                vol = 0.22
                            elif any(k in clean_word for k in ("BÍ", "MẬT", "SỰ", "THẬT", "SECRET", "TRUTH")):
                                sfx_name = "reveal"
                                vol = 0.22
                            else:
                                sfx_name = "whoosh_fast"
                                vol = 0.20
                            cues.append((round(word_ts_s, 2), sfx_name, vol))
                            last_ts_s = word_ts_s
                    word_offset_ms += dur_cs * 10

                chunk_start_ms += chunk_dur_ms

            current_offset_ms += scene_duration_ms

        return cues

    @classmethod
    def build_ass_from_timed_segments(
        cls,
        timed_segments: List[Dict[str, Any]],
        total_duration_sec: float = 0.0,
        clip_start_offset: float = 0.0,
        fontname: str = "Arial Black",
        fontsize: int = 42,
        margin_v: int = 580,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        niche_id: Optional[str] = None,
        credit_text: Optional[str] = None,
        show_cta_badge: bool = False,
    ) -> str:
        """
        Tạo nội dung file .ass từ mốc thời gian thực của giọng đọc (ASR/Whisper/Transcript).
        - Trích xuất từng câu/cụm từ có mốc start và duration thực.
        - Khử hoàn toàn chuỗi rác template (placeholder sanitizer).
        - Phân chia thành cụm từ ngắn (2-4 từ) và gắn tag karaoke {\\kf} theo từng từ.
        - Căn chuẩn Safe Zone 30-35% từ đáy (margin_v=580), chống va chạm layer với CTA badge.
        """
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
                eff_size = fontsize if fontsize != 42 else niche_sub.get("fontsize", 42)
                eff_margin = margin_v if margin_v != 580 else niche_sub.get("margin_v", 580)
            except Exception:
                pass

        header = ASS_HEADER_TEMPLATE.format(
            fontname=eff_font,
            fontsize=eff_size,
            primary=eff_secondary,     # Highlight color
            secondary=eff_primary,     # Base color
            outline=eff_outline,
            back=ASS_SHADOW,
            margin_v=eff_margin,
        )

        dialogue_lines = []
        max_seen_end_ms = 0

        for seg in timed_segments:
            raw_text = seg.get("text", "")
            clean_text = cls.sanitize_subtitle_text(raw_text)
            if not clean_text:
                continue

            raw_start = float(seg.get("start", 0.0))
            raw_dur = float(seg.get("duration", 0.0))
            if raw_dur <= 0.0:
                raw_dur = max(float(seg.get("end", raw_start + 2.0)) - raw_start, 1.0)

            # Căn chỉnh timestamp tương đối so với điểm bắt đầu clip
            rel_start_sec = max(0.0, raw_start - clip_start_offset)
            seg_start_ms = int(rel_start_sec * 1000)
            seg_dur_ms = max(int(raw_dur * 1000), 400)
            seg_end_ms = seg_start_ms + seg_dur_ms
            max_seen_end_ms = max(max_seen_end_ms, seg_end_ms)

            chunks = cls.split_text_to_chunks(clean_text)
            if not chunks:
                continue

            chunks = [c.upper() for c in chunks]
            total_chars = sum(len(c) for c in chunks)
            chunk_durations_ms = []
            for c in chunks:
                ratio = len(c) / total_chars if total_chars > 0 else 1.0 / len(chunks)
                chunk_durations_ms.append(max(int(seg_dur_ms * ratio), 300))

            diff = seg_dur_ms - sum(chunk_durations_ms)
            if chunk_durations_ms:
                chunk_durations_ms[-1] = max(chunk_durations_ms[-1] + diff, 200)

            chunk_curr_start_ms = seg_start_ms
            for chunk, c_dur_ms in zip(chunks, chunk_durations_ms):
                chunk_curr_end_ms = chunk_curr_start_ms + c_dur_ms
                words = chunk.split()
                if not words:
                    chunk_curr_start_ms = chunk_curr_end_ms
                    continue

                total_w_chars = sum(len(w) for w in words)
                word_durations_cs = []
                for w in words:
                    w_ratio = len(w) / total_w_chars if total_w_chars > 0 else 1.0 / len(words)
                    word_durations_cs.append(max(int(c_dur_ms * w_ratio / 10), 4))

                tot_w_cs = sum(word_durations_cs)
                target_c_cs = c_dur_ms // 10
                if word_durations_cs:
                    word_durations_cs[-1] += (target_c_cs - tot_w_cs)
                    word_durations_cs[-1] = max(word_durations_cs[-1], 3)

                karaoke_str = cls._build_karaoke_text(words, word_durations_cs)
                st_str = cls._ms_to_ass_time(chunk_curr_start_ms)
                en_str = cls._ms_to_ass_time(chunk_curr_end_ms)

                dialogue_lines.append(
                    f"Dialogue: 0,{st_str},{en_str},Karaoke,,0,0,0,,{karaoke_str}"
                )
                chunk_curr_start_ms = chunk_curr_end_ms

        effective_total_ms = int(total_duration_sec * 1000) if total_duration_sec > 0 else max_seen_end_ms

        # ── Kích hoạt Visual CTA Banner ở 3.5s cuối (Layer 1, MarginV=780 tránh đè Subtitle) ──
        if show_cta_badge and effective_total_ms > 4000:
            cta_start_ms = max(0, effective_total_ms - 3500)
            cta_end_ms = effective_total_ms
            all_text_concat = " ".join([seg.get("text", "") for seg in timed_segments])
            is_vi = any(c in all_text_concat.lower() for c in "àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ")
            cta_text = "🔔 ĐĂNG KÝ KÊNH ĐÓN XEM PHẦN 2 🔔" if is_vi else "🔔 SUBSCRIBE FOR PART 2 🔔"
            cta_start_str = cls._ms_to_ass_time(cta_start_ms)
            cta_end_str = cls._ms_to_ass_time(cta_end_ms)
            dialogue_lines.append(
                f"Dialogue: 1,{cta_start_str},{cta_end_str},CTABadge,,0,0,0,,{{\\fade(250,150)\\t(0,250,\\fscx110\\fscy110)\\t(250,500,\\fscx100\\fscy100)}}{cta_text}"
            )

        # ── Watermark Credit ở đỉnh video (Layer 2) ──
        if credit_text and credit_text.strip():
            clean_credit = credit_text.strip().replace("\n", " ")
            credit_end_str = cls._ms_to_ass_time(effective_total_ms if effective_total_ms > 0 else 60000)
            dialogue_lines.insert(0,
                f"Dialogue: 2,0:00:00.00,{credit_end_str},WatermarkStyle,,0,0,0,,{{\\fade(300,300)}}🎬 {clean_credit}"
            )

        return header + "\n".join(dialogue_lines) + "\n"

    @classmethod
    def save_ass_from_timed_segments(
        cls,
        timed_segments: List[Dict[str, Any]],
        output_path: str,
        total_duration_sec: float = 0.0,
        clip_start_offset: float = 0.0,
        fontname: str = "Arial",
        fontsize: int = 42,
        margin_v: int = 580,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        niche_id: Optional[str] = None,
        credit_text: Optional[str] = None,
        show_cta_badge: bool = True,
    ) -> str:
        """Tạo và lưu file .ass từ timed_segments ra ổ đĩa."""
        content = cls.build_ass_from_timed_segments(
            timed_segments=timed_segments,
            total_duration_sec=total_duration_sec,
            clip_start_offset=clip_start_offset,
            fontname=fontname,
            fontsize=fontsize,
            margin_v=margin_v,
            primary_color=primary_color,
            secondary_color=secondary_color,
            outline_color=outline_color,
            niche_id=niche_id,
            credit_text=credit_text,
            show_cta_badge=show_cta_badge,
        )
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return output_path

    # ── Multi-Clip Viral Mashup Subtitles (Dual-Layer: Badges + Kinetic Captions) ──

    @classmethod
    def build_mashup_ass_content(
        cls,
        clips: List[Dict[str, Any]],
        fontname: str = "Arial Black",
        fontsize: int = 44,
        margin_v: int = 580,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        niche_id: Optional[str] = None,
        credit_text: Optional[str] = None,
        is_english: bool = True,
    ) -> str:
        """
        Xây dựng phụ đề chuyên biệt cho kịch bản Nấu Lại Đa Phân Đoạn (Multi-Clip Mashup):
        1. Layer 2: Chapter / Timeline Context Badges ở ĐỈNH MÀN HÌNH (MarginV=260, Alignment=8).
           Hiển thị nhãn bối cảnh (ví dụ: '🚨 THE RESCUE AT 2:00 AM', '⏪ 3 HOURS EARLIER (FLASHBACK)')
           giúp người xem nắm bắt ngay mạch thời gian đảo cảnh, chống loạn nhịp.
        2. Layer 0: Kinetic Captions Karaoke giật từng từ ở ĐÁY MÀN HÌNH (MarginV=580, Alignment=2).
           Font to, viền dày, chữ trắng quét sáng Vàng rực rỡ (Yellow Wipe) với Power Words đỏ/cam.
           Đảm bảo 100% video luôn có phụ đề, cứu vãn 70%+ người xem lướt Shorts ở chế độ tắt tiếng.
        """
        eff_primary = primary_color or ASS_WHITE
        eff_secondary = secondary_color or ASS_YELLOW
        eff_outline = outline_color or ASS_BLACK
        eff_back = "&H90000000"
        eff_font = fontname or "Arial Black"
        eff_size = fontsize or 44
        eff_margin = margin_v or 580

        if niche_id:
            try:
                from app.services.niche_manager import get_niche, get_niche_subtitle_style
                niche_prof = get_niche(niche_id)
                niche_sub = get_niche_subtitle_style(niche_prof)
                eff_primary = primary_color or niche_sub.get("primary_ass", ASS_WHITE)
                eff_secondary = secondary_color or niche_sub.get("secondary_ass", ASS_YELLOW)
                eff_outline = outline_color or niche_sub.get("outline_ass", ASS_BLACK)
                eff_font = fontname or niche_sub.get("fontname", "Arial Black")
            except Exception:
                pass

        header = ASS_HEADER_TEMPLATE.format(
            fontname=eff_font,
            fontsize=eff_size,
            primary=eff_primary,
            secondary=eff_secondary,
            outline=eff_outline,
            back=eff_back,
            margin_v=eff_margin,
        )

        # Bộ nhãn bối cảnh theo Role & Niche
        default_badges_en = {
            "HOOK": "🚨 [ COLD OPEN: THE CLIMAX ]",
            "SETUP": "⏪ [ 3 HOURS EARLIER (FLASHBACK) ]",
            "TENSION": "⏳ [ SEARCHING DEEP IN THE DARK ]",
            "CLIMAX": "🎯 [ THE MIRACLE BREAKTHROUGH ]",
            "LOOP": "🔁 [ AND THE MIRACLE LOOPS... ]",
        }
        default_badges_vi = {
            "HOOK": "🚨 [ KHOẢNH KHẮC GIẢI CỨU CUỐI CÙNG ]",
            "SETUP": "⏪ [ 3 GIỜ TRƯỚC ĐÓ - CÚ GỌI KHẨN CẤP ]",
            "TENSION": "⏳ [ TÌM KIẾM TRONG BÓNG TỐI ]",
            "CLIMAX": "🎯 [ PHÁT HIỆN CÚN CON KẸT DƯỚI GẦM ]",
            "LOOP": "🔁 [ ĐIỀU KỲ DIỆU TIẾP NỐI ]",
        }
        default_badges = default_badges_en if is_english else default_badges_vi

        default_narr_en = {
            "HOOK": "THE EXACT MOMENT THEY WERE SAVED!",
            "SETUP": "IT ALL STARTED WITH AN URGENT CALL.",
            "TENSION": "CRAWLING DEEP UNDER THE COLD HOUSE...",
            "CLIMAX": "HUDDLED TOGETHER IN THE DARKNESS!",
            "LOOP": "WATCH THIS INCREDIBLE RESCUE AGAIN!",
        }
        default_narr_vi = {
            "HOOK": "KHOẢNH KHẮC NÍN THỞ KHI ĐƯỢC GIẢI CỨU!",
            "SETUP": "TẤT CẢ BẮT ĐẦU TỪ MỘT CUỘC GỌI KHẨN...",
            "TENSION": "TRƯỜN SÂU DƯỚI GẦM NHÀ BĂNG GIÁ...",
            "CLIMAX": "CÚN CON ĐANG CO RÚM VÌ ĐÓI RÉT!",
            "LOOP": "THEO DÕI HÀNH TRÌNH TIẾP THEO NGAY!",
        }
        default_narrs = default_narr_en if is_english else default_narr_vi

        dialogue_lines = []
        current_timeline_ms = 0

        for c_idx, c in enumerate(clips, 1):
            st = max(0.0, float(c.get("start_seconds", 0.0)))
            et = float(c.get("end_seconds", st + 4.0))
            dur = max(0.8, et - st)
            clip_dur_ms = int(dur * 1000)
            clip_start_ms = current_timeline_ms
            clip_end_ms = clip_start_ms + clip_dur_ms

            role = str(c.get("role", "CLIP")).upper()

            # ── 1. TOP TIMELINE CONTEXT BADGE (HIỂN THỊ 3.5S ĐẦU MỖI CLIP) ──
            badge_text = c.get("context_badge") or default_badges.get(role, f"📍 [ PHÂN ĐOẠN #{c_idx} ]")
            badge_dur_ms = min(3500, clip_dur_ms)
            b_st_str = cls._ms_to_ass_time(clip_start_ms)
            b_en_str = cls._ms_to_ass_time(clip_start_ms + badge_dur_ms)

            badge_dialogue = (
                f"Dialogue: 2,{b_st_str},{b_en_str},PatternInterruptBadge,,0,0,0,,"
                r"{\fade(150,150)\an8\bord3.5\shad1.5\c&H00FFFFFF&\3c&H00101010&\4c&H90000000&}"
                f"{badge_text}"
            )
            dialogue_lines.append(badge_dialogue)

            # ── 2. BOTTOM KINETIC CAPTIONS (GIẬT TỪNG TỪ KARAOKE TOÀN THỜI LƯỢNG) ──
            timed_trans = c.get("timed_transcript") or []
            if timed_trans and len(timed_trans) > 0:
                for seg in timed_trans:
                    seg_text = cls.sanitize_subtitle_text(seg.get("text", ""))
                    if not seg_text:
                        continue
                    seg_st = float(seg.get("start", st))
                    seg_d = float(seg.get("duration", 2.0))
                    rel_st_s = max(0.0, seg_st - st)
                    s_ms = clip_start_ms + int(rel_st_s * 1000)
                    d_ms = max(int(seg_d * 1000), 400)
                    e_ms = min(s_ms + d_ms, clip_end_ms)

                    chunks = cls.split_text_to_chunks(seg_text, max_words=3, max_chars=20)
                    if not chunks:
                        continue
                    chunks = [ch.upper() for ch in chunks]
                    total_ch = sum(len(ch) for ch in chunks)
                    c_dur_list = [max(int((e_ms - s_ms) * (len(ch) / total_ch)), 300) for ch in chunks]

                    c_st = s_ms
                    for ch, ch_dur in zip(chunks, c_dur_list):
                        c_en = min(c_st + ch_dur, clip_end_ms)
                        words = ch.split()
                        if not words:
                            c_st = c_en
                            continue
                        w_chars = sum(len(w) for w in words)
                        w_durs = [max(int(ch_dur * (len(w) / w_chars) / 10), 4) for w in words]
                        tot_w = sum(w_durs)
                        target_c = ch_dur // 10
                        if w_durs:
                            w_durs[-1] += (target_c - tot_w)
                            w_durs[-1] = max(w_durs[-1], 3)
                        k_text = cls._build_karaoke_text(words, w_durs, default_highlight=eff_secondary)
                        dialogue_lines.append(
                            f"Dialogue: 0,{cls._ms_to_ass_time(c_st)},{cls._ms_to_ass_time(c_en)},Karaoke,,0,0,0,,{k_text}"
                        )
                        c_st = c_en
            else:
                raw_text = c.get("transcript_snippet") or c.get("text") or c.get("summary") or ""
                clean_text = cls.sanitize_subtitle_text(raw_text)
                if not clean_text or len(clean_text.split()) < 2:
                    clean_text = default_narrs.get(role, f"WATCH THIS SCENE #{c_idx}!")

                chunks = cls.split_text_to_chunks(clean_text, max_words=3, max_chars=20)
                if not chunks:
                    chunks = [clean_text[:25]]
                chunks = [ch.upper() for ch in chunks]

                total_ch = sum(len(ch) for ch in chunks)
                chunk_durs_ms = [max(int(clip_dur_ms * (len(ch) / total_ch)), 500) for ch in chunks]
                diff = clip_dur_ms - sum(chunk_durs_ms)
                if chunk_durs_ms:
                    chunk_durs_ms[-1] = max(chunk_durs_ms[-1] + diff, 300)

                c_st = clip_start_ms
                for ch, ch_dur in zip(chunks, chunk_durs_ms):
                    c_en = c_st + ch_dur
                    words = ch.split()
                    if not words:
                        c_st = c_en
                        continue
                    w_chars = sum(len(w) for w in words)
                    w_durs = [max(int(ch_dur * (len(w) / w_chars) / 10), 4) for w in words]
                    tot_w = sum(w_durs)
                    target_c = ch_dur // 10
                    if w_durs:
                        w_durs[-1] += (target_c - tot_w)
                        w_durs[-1] = max(w_durs[-1], 3)

                    k_text = cls._build_karaoke_text(words, w_durs, default_highlight=eff_secondary)
                    dialogue_lines.append(
                        f"Dialogue: 0,{cls._ms_to_ass_time(c_st)},{cls._ms_to_ass_time(c_en)},Karaoke,,0,0,0,,{k_text}"
                    )
                    c_st = c_en

            current_timeline_ms += clip_dur_ms

        # ── 3. WATERMARK CREDIT Ở ĐỈNH VIDEO ──
        if credit_text and credit_text.strip():
            clean_credit = credit_text.strip().replace("\n", " ")
            credit_end_str = cls._ms_to_ass_time(current_timeline_ms)
            dialogue_lines.insert(0,
                f"Dialogue: 2,0:00:00.00,{credit_end_str},WatermarkStyle,,0,0,0,,{{\\fade(300,300)}}🎬 {clean_credit}"
            )

        return header + "\n".join(dialogue_lines) + "\n"

    @classmethod
    def save_mashup_ass_file(
        cls,
        clips: List[Dict[str, Any]],
        output_path: str,
        fontname: str = "Arial Black",
        fontsize: int = 44,
        margin_v: int = 580,
        niche_id: Optional[str] = None,
        credit_text: Optional[str] = None,
        is_english: bool = True,
    ) -> str:
        """Tạo và lưu file .ass chuyên biệt cho kịch bản Multi-Clip Mashup ra ổ đĩa."""
        content = cls.build_mashup_ass_content(
            clips=clips,
            fontname=fontname,
            fontsize=fontsize,
            margin_v=margin_v,
            niche_id=niche_id,
            credit_text=credit_text,
            is_english=is_english,
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
