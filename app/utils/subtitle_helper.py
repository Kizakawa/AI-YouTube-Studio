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
ASS_WHITE       = "&H00FFFFFF"   # Trắng tinh khiết — màu chữ đã đọc/đang đọc chuẩn @MasteringMindControl
ASS_DIM_SILVER  = "&H009E9E9E"   # Bạc xám trầm (#9E9E9E) — màu chữ sắp đọc (tạo hiệu ứng sáng dần từng từ)
ASS_YELLOW      = "&H0000FFFF"   # Vàng tươi rực #FFFF00 — từ khóa nhấn mạnh / Payoff
ASS_RED         = "&H000000FF"   # Đỏ rực — từ khóa nguy hiểm
ASS_NEON_GREEN  = "&H0033FF33"   # Xanh lá Neon (#33FF33) — chuẩn Submagic/Crayo cho Con Số / Thống Kê
ASS_CRIMSON     = "&H002222FF"   # Đỏ Crimson rực (#FF2222) — cho Từ Khóa Nguy Hiểm / Xung Đột / Cảnh Báo
ASS_CYAN        = "&H00FFFF00"   # Xanh Cyan Điện (#00FFFF) — cho Từ Khóa Bí Mật / Sự Thật / Giải Mã
ASS_BLACK       = "&H00000000"   # Đen — viền chữ (tương phản cao trên mọi nền)
ASS_SHADOW      = "&HA8000000"   # Đen 66% trong suốt — shadow sâu điện ảnh

# ── Phân tầng ngữ nghĩa 3 màu chuẩn @MasteringMindControl / Submagic (Semantic Pop) ──
DANGER_POWER_WORDS = {
    "NEVER", "DANGER", "DANGEROUS", "DEADLY", "DISASTER", "MONSTER", "KILLER",
    "MISTAKE", "WARNING", "STOP", "DESTROY", "ATTACK", "FATAL", "PREDATOR", "FEAR",
    "TERRIFYING", "HORROR", "TRAPPED", "BEAST", "LETHAL", "POISON", "VENOM",
    "LIES", "DECEPTION", "DARK", "BLOOD", "WAR", "DEATH", "NARCISSIST", "PSYCHOPATH",
    "MANIPULATE", "MANIPULATION", "MANIPULATORS", "HUMILIATE", "SHATTERS", "BROKEN", "STARVING",
    "ABANDONED", "FROZEN", "TREMBLING", "HEARTBREAKING", "CRUSHING", "NIGHTMARE",
    "NIGHTMARES", "AMBUSH", "BITE", "SKULL", "CLAWS", "EXECUTION", "COLLAPSE",
    "ISOLATION", "GASLIGHTING", "CONTROL", "CONTROLS", "CRITICIZE", "DEPEND", "DEPENDENCE",
    "GUILTY", "ADDICTED", "ANXIETY", "DOUBT", "LEASH", "TRAP", "ULTIMATUM", "ABUSE",
    "ĐỪNG", "KHÔNG", "NGUY", "HIỂM", "CHẾT", "TỬ", "THẦN", "SỐC", "KINH", "HOÀNG",
    "CẢNH", "BÁO", "DỪNG", "TIÊU", "DIỆT", "TẤN", "CÔNG", "SÁT", "THỦ", "QUÁI",
    "VẬT", "ÁC", "MỘNG", "SỢ", "HÃI", "MẮC", "KẸT", "DỐI", "TRÁ", "THAO", "TÚNG",
    "MÁU", "BẪY", "CẤM", "CÔ", "LẬP", "KIỂM", "SOÁT",
    "NUNCA", "JAMAIS", "PERIGO", "PERIGOSO", "MORTAL",
    "MONSTRO", "ASSASSINO", "ERRO", "AVISO", "PARE", "ATAQUE", "FATAL", "PREDADOR",
    "MEDO", "TERROR", "VENENO", "MENTIRA", "SANGUE", "MORTE", "PROIBIDO", "CUIDADO",
    "BRUTAL", "LETAL", "ONÇA", "SUCURI", "JACARÉ", "ARIRANHA", "HARPIA", "PIRANHA",
    "ABISMO", "ESCURIDÃO", "CRÂNIO", "MORDIDA", "GARRAS", "FERVENTE", "ATERRORIZANTE",
}

PAYOFF_POWER_WORDS = {
    "ALWAYS", "SECRET", "SECRETS", "TRUTH", "SHOCKING", "WHY", "ABSOLUTELY",
    "INSANE", "IMPOSSIBLE", "UNBELIEVABLE", "MYSTERY", "DISCOVERED", "HIDDEN",
    "REVEALED", "RULE", "RULES", "LAW", "ESCAPE", "SURVIVE", "SURVIVAL", "URGENT",
    "SILENCE", "SILENT", "STARE", "MIRACLE", "RESCUED", "ABYSS", "KRAKEN", "LEVIATHAN",
    "SONAR", "VORTEX", "ARMOR", "APEX", "EMPIRE", "FORBIDDEN", "CLASSIFIED",
    "BILLION", "TRILLION", "INSTANTLY", "GAMBIT", "FAVOR", "ENEMIES", "ALLIES",
    "PAVLOVIAN", "TRIGGER", "TRIGGERS", "REINFORCEMENT", "MIRRORING", "VERIFY",
    "REALITY", "POWER", "FREEDOM", "BOUNDARIES", "TRUST", "CERTAINTY", "MIND",
    "BÍ", "MẬT", "SỰ", "THẬT", "TUYỆT", "ĐỐI", "ĐIÊN", "RỒ", "HÉ", "LỘ",
    "ĐỘT", "NHIÊN", "KHỔNG", "LỒ", "BẤT", "NGỜ", "SINH", "TỒN", "IM", "LẶNG",
    "SEMPRE", "SEGREDO", "SEGREDOS", "VERDADE", "CHOCANTE", "IMPOSSÍVEL",
    "INCRÍVEL", "ABSURDO", "MISTÉRIO", "ESCONDIDO", "REVELADO", "REGRA", "REGRAS",
    "SOBREVIVER", "URGENTE", "AMAZÔNIA", "PANTANAL", "OCEANO", "CIÊNCIA", "MILAGRE",
    "INEXPLICÁVEL", "GIGANTE", "COLOSSAL", "PROFUNDO", "SOM", "MISTÉRIOS",
}

# ── Bộ từ khóa cảm xúc mạnh kích thích thị giác (Hypnotic Power Words) ─────
POWER_WORDS = DANGER_POWER_WORDS | PAYOFF_POWER_WORDS

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
Style: Karaoke,{fontname},{fontsize},{primary},{secondary},{outline},{back},-1,0,0,0,100,100,1.2,0,1,6.0,3.2,2,40,40,{margin_v},1
Style: HookHeadlineBanner,{fontname},52,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,1.8,0,1,4.0,2.0,8,30,30,235,1
Style: PatternInterruptBadge,{fontname},52,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,1.8,0,1,4.0,2.0,8,30,30,230,1
Style: CTABadge,{fontname},48,&H0000FFFF,&H00FFFFFF,&H00000000,&HC8080808,-1,0,0,0,100,100,1.2,0,1,4.4,2.4,8,30,30,225,1
Style: WatermarkStyle,Arial,38,&H30FFFFFF,&H30FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.2,1.4,8,30,30,90,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# ── Fonts ưu tiên (Ưu tiên Montserrat ExtraBold nhúng sẵn trong assets/fonts, fallback Segoe UI / Arial) ──
PREFERRED_FONTS = ["Montserrat ExtraBold", "Montserrat Black", "Montserrat", "Arial Black", "Segoe UI", "Arial", "Roboto"]


class SubtitleHelper:
    """Helper xử lý và tạo phụ đề video — ASS (karaoke) + SRT (legacy)."""

    # ── Utilities ─────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_effective_font(requested_font: Optional[str] = None) -> str:
        """
        Tự động chọn font chữ dày, sắc nét chuẩn Shorts Viral (Montserrat ExtraBold / Black)
        từ kho assets/fonts đã nhúng sẵn, hoặc fallback thông minh sang font hệ thống có thật.
        """
        req = (requested_font or "").strip()
        fonts_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"
        has_bundled_montserrat = (fonts_dir / "Montserrat-ExtraBold.ttf").exists() or (fonts_dir / "Montserrat-Black.ttf").exists()

        if has_bundled_montserrat:
            if not req or req.lower() in ("montserrat", "montserrat, sans-serif", "sans-serif"):
                return "Montserrat ExtraBold" if (fonts_dir / "Montserrat-ExtraBold.ttf").exists() else "Montserrat Black"
            return req

        # Nếu không có thư mục assets/fonts, kiểm tra Windows Fonts tránh trường hợp thiếu ariblk.ttf
        win_fonts = Path("C:/Windows/Fonts")
        if req.lower() == "arial black" and win_fonts.exists() and not (win_fonts / "ariblk.ttf").exists():
            return "Segoe UI" if (win_fonts / "segoeuib.ttf").exists() else "Arial"
        return req or "Arial"

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
    def align_word_timings_with_narration(
        cls,
        word_timings: List[Dict[str, Any]],
        narration: str,
        scene_duration_ms: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Đối chiếu và căn chỉnh từ vựng (Lexical Alignment) giữa mốc thời gian `word_timings` (từ TTS/ASR)
        và văn bản kịch bản chuẩn `narration` (kế thừa từ Y2A-Auto srt_transform_engine.py):
        - Chống triệt để lỗi ASR/WordBoundary làm cụt âm tiết (ví dụ "EXLY" -> khôi phục nguyên vẹn "EXACTLY")
          hoặc tách rời từ ghép / con số ("3,700", "don't", "self-control").
        - Giữ nguyên 100% mốc thời gian thực (start_ms, end_ms) từ giọng đọc.
        """
        if not word_timings:
            return []
        clean_narration = cls.sanitize_subtitle_text(narration or "")
        canon_words = [w.strip() for w in re.sub(r"\s+", " ", clean_narration).split() if w.strip()]
        if not canon_words:
            return word_timings

        import difflib

        def _norm_stem(token: str) -> str:
            return re.sub(r"[^\w\d]", "", str(token or "").lower())

        wt_stems = [_norm_stem(w.get("word", "")) for w in word_timings]
        canon_stems = [_norm_stem(w) for w in canon_words]

        matcher = difflib.SequenceMatcher(None, wt_stems, canon_stems, autojunk=False)
        aligned: List[Dict[str, Any]] = []
        total_dur_fallback = max(scene_duration_ms, int(word_timings[-1].get("end_ms", 1000) or 1000))

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for idx_offset in range(i2 - i1):
                    wt_item = dict(word_timings[i1 + idx_offset])
                    wt_item["word"] = canon_words[j1 + idx_offset]
                    aligned.append(wt_item)
            elif tag in ("replace", "insert", "delete"):
                target_canon = canon_words[j1:j2]
                src_wts = word_timings[i1:i2]
                if not target_canon:
                    continue
                if len(src_wts) == len(target_canon):
                    for wt_orig, c_word in zip(src_wts, target_canon):
                        wt_item = dict(wt_orig)
                        wt_item["word"] = c_word
                        aligned.append(wt_item)
                else:
                    if src_wts:
                        span_start = int(src_wts[0].get("start_ms", 0))
                        span_end = int(src_wts[-1].get("end_ms", span_start + 120))
                    elif aligned:
                        span_start = int(aligned[-1].get("end_ms", 0))
                        span_end = min(total_dur_fallback, span_start + 180 * len(target_canon))
                    else:
                        span_start = 0
                        span_end = min(total_dur_fallback, 180 * len(target_canon))
                    span_dur = max(40 * len(target_canon), span_end - span_start)
                    char_weights = [max(1, len(_norm_stem(cw))) for cw in target_canon]
                    total_w = sum(char_weights) or 1
                    cursor = span_start
                    for k_idx, (cw, weight) in enumerate(zip(target_canon, char_weights)):
                        if k_idx == len(target_canon) - 1:
                            w_end = max(cursor + 35, span_start + span_dur)
                        else:
                            w_dur = max(35, int(round(span_dur * (weight / float(total_w)))))
                            w_end = cursor + w_dur
                        aligned.append({
                            "word": cw,
                            "start_ms": cursor,
                            "end_ms": w_end,
                            "duration_ms": max(35, w_end - cursor),
                        })
                        cursor = w_end

        return aligned if aligned else word_timings

    # Các từ nối / mạo từ / giới từ gắn chặt với cụm danh từ hoặc động từ phía sau (VideoLingo NLP Semantic Chunking):
    # Không để các từ này đứng trơ trọi ở cuối một dòng phụ đề khi không có dấu ngắt câu.
    _PROCLITIC_ATTACH_NEXT_WORDS = frozenset({
        # English
        "A", "AN", "THE", "OF", "IN", "ON", "AT", "TO", "FOR", "WITH", "BY", "FROM", "INTO",
        "UNTIL", "UNDER", "OVER", "AND", "OR", "BUT", "IF", "THAN", "THAT", "THIS", "THESE",
        "THOSE", "MY", "YOUR", "HIS", "HER", "ITS", "OUR", "THEIR", "IS", "ARE", "WAS", "WERE",
        "HAS", "HAVE", "HAD", "WILL", "CAN", "COULD", "WOULD", "SHOULD", "NO", "NOT",
        # Portuguese (pt-BR)
        "O", "OS", "AS", "UM", "UMA", "UNS", "UMAS", "DE", "DO", "DA", "DOS", "DAS", "EM",
        "NO", "NA", "NOS", "NAS", "POR", "PELO", "PELA", "PARA", "COM", "SEM", "SOB", "QUE",
        "SE", "OU", "E", "MAS", "MEU", "MINHA", "SEU", "SUA", "FOI", "ERA", "NÃO", "MAIS",
        # Vietnamese
        "CỦA", "TRONG", "TRÊN", "DƯỚI", "VỚI", "CHO", "TỪ", "MỘT", "NHỮNG", "CÁC", "LÀ", "VÀ", "ĐỂ",
    })

    @classmethod
    def _is_dangling_proclitic(cls, token: str) -> bool:
        raw = str(token or "").strip()
        if not raw or raw.endswith((".", "!", "?", ":", ";", ",", "—", "-")):
            return False
        clean = re.sub(r"[^\w\d]", "", raw.upper())
        return clean in cls._PROCLITIC_ATTACH_NEXT_WORDS

    @classmethod
    def split_text_to_chunks(cls, text: str, max_words: int = 3, max_chars: int = 20) -> List[str]:
        """
        Chia một đoạn văn bản thành các cụm từ siêu ngắn gọn (2-3 từ, tối đa 20 ký tự),
        kết hợp thuật toán Semantic NLP Chunking (chuẩn VideoLingo / Submagic / Hormozi):
        - Nhịp nhảy chữ nhanh bám sát mắt người xem, ngăn đọc trước giọng đọc.
        - Không bao giờ để mạo từ / giới từ / liên từ ('THE', 'OF', 'IN', 'A', 'TO'...) bị treo lẻ loi ở cuối dòng.
        """
        text = cls.sanitize_subtitle_text(text)
        # Normalize whitespace: collapse \n, \t, multiple spaces thành space đơn
        import re as _re
        text = _re.sub(r'\s+', ' ', text).strip()
        if not text:
            return []

        words = text.split()
        if len(words) <= max_words and len(text) <= max_chars:
            return [text]

        word_lists: List[List[str]] = []
        current_chunk: List[str] = []

        for idx, word in enumerate(words):
            candidate = " ".join(current_chunk + [word])
            # Nếu đã có >= 1 từ và từ hiện tại là mạo từ/giới từ mở đầu cụm mới mà thêm từ sau nữa sẽ tràn dòng -> ngắt trước mạo từ
            next_word = words[idx + 1] if idx + 1 < len(words) else ""
            would_leave_dangling = (
                bool(current_chunk)
                and bool(next_word)
                and cls._is_dangling_proclitic(word)
                and (len(current_chunk) + 1 >= max_words or len(candidate + " " + next_word) > max_chars)
            )
            if ((len(current_chunk) >= max_words or len(candidate) > max_chars) and current_chunk) or would_leave_dangling:
                word_lists.append(current_chunk)
                current_chunk = [word]
            else:
                current_chunk.append(word)
                if word.endswith((".", "!", "?", ":", ";", ",", "—")) and len(current_chunk) >= 2:
                    word_lists.append(current_chunk)
                    current_chunk = []

        if current_chunk:
            word_lists.append(current_chunk)

        # Hậu kiểm: nếu 1 chunk >= 2 từ vẫn kết thúc bằng từ treo (proclitic), chuyển từ đó sang đầu chunk kế tiếp
        for i in range(len(word_lists) - 1):
            if len(word_lists[i]) >= 2 and cls._is_dangling_proclitic(word_lists[i][-1]) and len(word_lists[i + 1]) <= max_words:
                moved = word_lists[i].pop()
                word_lists[i + 1].insert(0, moved)

        return [" ".join(w_list) for w_list in word_lists if w_list]

    @staticmethod
    def sanitize_subtitle_text(text: str) -> str:
        """
        Khử toàn bộ chuỗi rác template, placeholder, stage tags ([LOOP], [HOOK]), âm thanh tự sinh YouTube:
        - Loại bỏ [Music], [Âm nhạc], [Applause], [Tiếng cười], [LOOP], [HOOK], [ACT 1], v.v.
        - Loại bỏ placeholder dạng {hook_quote}, {default_...}, v.v.
        - Loại bỏ từ khóa dummy: lorem ipsum, placeholder, scene X
        """
        if not text:
            return ""
        # Loại bỏ các tag chỉ dẫn đạo diễn / âm thanh [LOOP], [HOOK], [Music], [Âm nhạc], [Applause], ...
        t = re.sub(
            r'\[(?:Music|Âm nhạc|Applause|Laughter|Tiếng cười|Vỗ tay|Cheering|SFX|Audio|LOOP|HOOK|ACT\s*\d*|SCENE\s*\d*|PAUSE|BEAT|SILENCE|WHISPER|INTRO|OUTRO|END)[^\]]*\]',
            '',
            text,
            flags=re.IGNORECASE,
        )
        t = re.sub(
            r'\((?:Music|Âm nhạc|Applause|Laughter|Tiếng cười|Vỗ tay|Cheering|SFX|Audio|LOOP|HOOK|PAUSE|BEAT|SILENCE|WHISPER)\)',
            '',
            t,
            flags=re.IGNORECASE,
        )
        # Loại bỏ các biến placeholder dạng {hook_quote}, {default_...}
        t = re.sub(r'\{[^{}]*\}', '', t)
        # Loại bỏ các từ khóa dummy/rác
        t = re.sub(r'\b(?:placeholder|dummy text|lorem ipsum|scene \d+)\b', '', t, flags=re.IGNORECASE)
        # Chuẩn hóa khoảng trắng
        t = re.sub(r'\s+', ' ', t).strip()
        return t

    # ── ASS Generation ────────────────────────────────────────────────────

    @staticmethod
    def _strip_ass_unsupported_emojis(text: str) -> str:
        """
        Loại bỏ các Emoji màu (Astral Plane / Dingbats) không được font Arial Black / Montserrat hỗ trợ trong libass,
        ngăn chặn triệt để lỗi hiển thị ô vuông rỗng (Tofu box □) trên màn hình video.
        """
        if not text:
            return ""
        cleaned = re.sub(r'[\U00010000-\U0010ffff\u2600-\u27bf\ufe0f]', '', text)
        return re.sub(r'\s+', ' ', cleaned).strip()

    @classmethod
    def _build_karaoke_text(
        cls,
        words: List[str],
        word_durations_cs: List[int],
        default_highlight: str = "&H00FFFFFF",
        power_color: str = "&H000000FF",
        scene_highlights: Optional[set] = None,
    ) -> str:
        """
        @MasteringMindControl 2-Line Stacked Kinetic Impact Typography + 3-Color Semantic Highlighting:
        - Tự động xếp chồng 2 dòng (Line 1 dẫn nhịp -> Line 2 \\N phóng đại từ khóa trọng tâm 122%-126%)
          như phong cách @MasteringMindControl ("THAT'S WHY \\N SILENCE", "FIRST, \\N THEY CRITICIZE").
        - Hiệu ứng sáng dần từng từ (Silver-Grey -> Crisp Pure White) kết hợp 3 tầng màu ngữ nghĩa:
          * Tầng 1 (Số liệu / Thống kê): Xanh lá Neon (&H0033FF33&) + Pop 124%
          * Tầng 2 (Nguy hiểm / Thao túng / Xung đột): Đỏ Crimson (&H002222FF&) + Pop 124%
          * Tầng 3 (Bí mật / Hiệu ứng Tâm lý / Giải mã): Xanh Cyan / Vàng Gold + Pop 122%
        """
        norm_power = power_color if power_color.endswith("&") else f"{power_color}&"
        norm_highlight = default_highlight if default_highlight.endswith("&") else f"{default_highlight}&"
        norm_green = f"{ASS_NEON_GREEN}&"
        norm_crimson = f"{ASS_CRIMSON}&"
        norm_cyan = f"{ASS_CYAN}&"
        custom_set = scene_highlights or set()

        n_words = len(words)
        split_after_idx = -1
        focal_idx = n_words - 1
        lead_transition_words = {"FIRST", "SECOND", "THIRD", "THEN", "SAY", "STOP", "WAIT", "LOOK", "NEVER", "NOW"}

        if n_words == 2:
            split_after_idx = 0
            w0_clean = re.sub(r'[^\w\d]', '', words[0].upper())
            if words[0].endswith((",", ":", "—", "!")) or w0_clean in lead_transition_words:
                focal_idx = 0
            else:
                focal_idx = 1
        elif n_words == 3:
            w0_clean = re.sub(r'[^\w\d]', '', words[0].upper())
            if words[0].endswith((",", ":", "—", "!")) or w0_clean in lead_transition_words:
                split_after_idx = 0  # vd: "FIRST, \N THEY CRITICIZE"
                focal_idx = 0
            else:
                split_after_idx = 1  # vd: "THAT'S WHY \N SILENCE"
                focal_idx = 2
        elif n_words >= 4:
            split_after_idx = (n_words // 2) - 1
            focal_idx = n_words - 1

        parts = []
        cum_ms = 0
        for i, (word, dur_cs) in enumerate(zip(words, word_durations_cs)):
            dur_ms = max(dur_cs * 10, 50)
            clean_word = re.sub(r'[^\w\d]', '', word.upper())
            has_shock_number = bool(re.search(r'\d', word))
            is_danger = clean_word in DANGER_POWER_WORDS
            is_payoff = (clean_word in PAYOFF_POWER_WORDS) or (clean_word in custom_set)
            is_focal = (i == focal_idx and n_words >= 2)

            t_start = cum_ms
            t_peak = cum_ms + min(75, max(35, dur_ms // 3))
            t_settle = cum_ms + min(180, max(80, (dur_ms * 2) // 3))

            if has_shock_number:
                token_str = (
                    f"{{\\kf{dur_cs}\\1c{norm_green}\\fscx124\\fscy124\\b1"
                    f"\\t({t_start},{t_peak},\\fscx130\\fscy130)\\t({t_peak},{t_settle},\\fscx124\\fscy124)}}{word}"
                    f"{{\\1c{norm_highlight}\\fscx100\\fscy100}}"
                )
            elif is_danger:
                token_str = (
                    f"{{\\kf{dur_cs}\\1c{norm_crimson}\\fscx124\\fscy124\\b1"
                    f"\\t({t_start},{t_peak},\\fscx130\\fscy130)\\t({t_peak},{t_settle},\\fscx124\\fscy124)}}{word}"
                    f"{{\\1c{norm_highlight}\\fscx100\\fscy100}}"
                )
            elif is_payoff:
                chosen_payoff_color = norm_power if "2080FF" in norm_power else norm_cyan
                token_str = (
                    f"{{\\kf{dur_cs}\\1c{chosen_payoff_color}\\fscx122\\fscy122\\b1"
                    f"\\t({t_start},{t_peak},\\fscx128\\fscy128)\\t({t_peak},{t_settle},\\fscx122\\fscy122)}}{word}"
                    f"{{\\1c{norm_highlight}\\fscx100\\fscy100}}"
                )
            elif is_focal:
                # Từ trọng tâm ở dòng 2 (hoặc từ mở nhịp dòng 1) được phóng đại 122% chuẩn @MasteringMindControl
                token_str = (
                    f"{{\\kf{dur_cs}\\1c{norm_highlight}\\fscx122\\fscy122\\b1"
                    f"\\t({t_start},{t_peak},\\fscx128\\fscy128)\\t({t_peak},{t_settle},\\fscx122\\fscy122)}}{word}"
                    f"{{\\fscx100\\fscy100}}"
                )
            else:
                token_str = f"{{\\kf{dur_cs}}}{word}"

            parts.append(token_str)
            cum_ms += dur_ms

        if 0 <= split_after_idx < len(parts) - 1:
            line1 = " ".join(parts[: split_after_idx + 1])
            line2 = " ".join(parts[split_after_idx + 1 :])
            return f"{line1}\\N{line2}"
        return " ".join(parts)

    @classmethod
    def _build_visual_cta_banner_text(cls, raw_niche: str, is_vi: bool, is_pt: bool) -> str:
        """
        Tạo nội dung dòng chữ kêu gọi hành động (Visual CTA Banner 2 dòng) ở cuối mỗi video:
        - Kêu gọi đồng thời cả 3 hành động: LIKE + COMMENT + SUBSCRIBE bằng thị giác (thay vì đọc trong lời thoại,
          giúp giữ nguyên 100% Seamless Infinite Loop của giọng đọc mà vẫn đẩy mạnh tương tác).
        - Phối 3 màu nổi bật (Vàng Gold + Xanh Neon + Đỏ Crimson) ở dòng 1 và câu hỏi kích thích bình luận ở dòng 2.
        """
        niche_l = (raw_niche or "").lower()
        if is_vi:
            line1 = r"{\1c&H0000FFFF&}[ ★ BẤM THÍCH  {\1c&H0033FF33&}•  BÌNH LUẬN 1 HOẶC 2  {\1c&H002222FF&}•  ĐĂNG KÝ ★ ]"
            if "psychology" in niche_l:
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ TỪNG GẶP NGƯỜI NHƯ VẬY? GÕ (1) CÓ HOẶC (2) CHƯA ▼"
            elif any(k in niche_l for k in ("predator", "wildlife")):
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ KÈO NÀY AI THẮNG? BÌNH CHỌN (1) HOẶC (2) BÊN DƯỚI ▼"
            elif any(k in niche_l for k in ("deep_sea", "ocean", "mystery")):
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ (1) SINH VẬT BÍ ẨN HAY (2) DỊ THƯỜNG? GÕ 1 HOẶC 2 ▼"
            elif any(k in niche_l for k in ("history", "what_if", "alternate")):
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ (1) GIỮ LỊCH SỬ HAY (2) THAY ĐỔI? GÕ 1 HOẶC 2 ▼"
            elif any(k in niche_l for k in ("rescue", "animal", "pet")):
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ CHẤM ĐIỂM TỪ 1-10 CHO BÉ Ở PHẦN BÌNH LUẬN NHÉ ▼"
            else:
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ BẠN ĐÃ BIẾT ĐIỀU NÀY CHƯA? GÕ (1) RỒI HOẶC (2) CHƯA ▼"
        elif is_pt:
            line1 = r"{\1c&H0000FFFF&}[ ★ CURTA  {\1c&H0033FF33&}•  VOTE 1 OU 2  {\1c&H002222FF&}•  INSCREVA-SE ★ ]"
            if "psychology" in niche_l:
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ JÁ PASSOU POR ISSO? COMENTE (1) SIM OU (2) NÃO ▼"
            elif any(k in niche_l for k in ("predator", "wildlife")):
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ QUEM VENCE ESSE DUELO? VOTE (1) OU (2) ABAIXO ▼"
            elif any(k in niche_l for k in ("deep_sea", "ocean", "mystery")):
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ (1) CRIATURA OCULTA OU (2) ANOMALIA? VOTE 1 OU 2 ▼"
            elif any(k in niche_l for k in ("history", "what_if", "alternate")):
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ (1) MANTER OU (2) MUDAR A HISTÓRIA? VOTE 1 OU 2 ▼"
            elif any(k in niche_l for k in ("rescue", "animal", "pet")):
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ DÊ UMA NOTA DE 1 A 10 NOS COMENTÁRIOS ABAIXO ▼"
            else:
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ VOCÊ SABIA DISSO? COMENTE (1) SIM OU (2) NÃO ▼"
        else:
            line1 = r"{\1c&H0000FFFF&}[ ★ LIKE  {\1c&H0033FF33&}•  VOTE 1 OR 2  {\1c&H002222FF&}•  SUBSCRIBE ★ ]"
            if "psychology" in niche_l:
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ EVER MET SOMEONE LIKE THIS? COMMENT (1) YES OR (2) NO ▼"
            elif any(k in niche_l for k in ("predator", "wildlife")):
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ WHO WINS THIS 1V1? VOTE (1) OR (2) IN COMMENTS ▼"
            elif any(k in niche_l for k in ("deep_sea", "ocean", "mystery")):
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ (1) UNDISCOVERED SPECIES OR (2) ANOMALY? VOTE 1 OR 2 ▼"
            elif any(k in niche_l for k in ("history", "what_if", "alternate")):
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ (1) KEEP TIMELINE OR (2) REWRITE HISTORY? VOTE 1 OR 2 ▼"
            elif any(k in niche_l for k in ("rescue", "animal", "pet")):
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ RATE THIS TRANSFORMATION 1-10 IN THE COMMENTS ▼"
            else:
                line2 = r"{\1c&H00FFFFFF&\fs36}▼ DID YOU KNOW THIS? COMMENT (1) YES OR (2) NO BELOW ▼"
        return f"{line1}\\N{line2}"

    @classmethod
    def build_ass_content(
        cls,
        scenes: List[SceneContract],
        fontname: str = "Montserrat ExtraBold",
        fontsize: int = 78,
        margin_v: int = 720,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        niche_id: Optional[str] = None,
        credit_text: Optional[str] = None,
        show_cta_badge: bool = True,
        hook_title: Optional[str] = None,
        is_long_form: bool = False,
    ) -> str:
        """
        Tạo nội dung file .ass chuẩn với 2-Line Stacked Kinetic Impact (Shorts 9:16)
        hoặc Widescreen Cinema Subtitle (Long-form 16:9, PlayRes 1920x1080) & karaoke word-level highlight.
        Tự động nạp font và màu sắc từ Niche Profile nếu niche_id được cung cấp.
        """
        eff_primary = primary_color or ASS_DIM_SILVER
        eff_secondary = secondary_color or ASS_WHITE
        eff_outline = outline_color or ASS_BLACK
        eff_font = fontname
        eff_size = 50 if is_long_form else (fontsize if fontsize >= 74 else 78)
        eff_margin = 75 if is_long_form else margin_v

        if niche_id:
            try:
                from app.services.niche_manager import get_niche, get_niche_subtitle_style
                niche_prof = get_niche(niche_id)
                niche_sub = get_niche_subtitle_style(niche_prof)
                eff_primary = primary_color or niche_sub.get("primary_ass", ASS_DIM_SILVER)
                eff_secondary = secondary_color or niche_sub.get("secondary_ass", ASS_WHITE)
                eff_outline = outline_color or niche_sub.get("outline_ass", ASS_BLACK)
                if not fontname or fontname in ("Arial", "Arial Black", "Montserrat ExtraBold"):
                    eff_font = niche_sub.get("fontname", "Montserrat ExtraBold")
                if not is_long_form:
                    eff_size = fontsize if fontsize not in (42, 44, 68, 78) and fontsize >= 74 else max(int(niche_sub.get("fontsize", 78) or 78), 76)
                    eff_margin = margin_v if margin_v not in (580, 720) else niche_sub.get("margin_v", 720)
            except Exception as e:
                pass

        eff_font = cls._resolve_effective_font(eff_font)

        header = ASS_HEADER_TEMPLATE.format(
            fontname=eff_font,
            fontsize=eff_size,
            primary=eff_secondary,     # PrimaryColour = màu highlight sau khi đọc (Trắng sáng #FFFFFF / Vàng)
            secondary=eff_primary,     # SecondaryColour = màu mặc định trước khi đọc (Bạc xám #9E9E9E)
            outline=eff_outline,
            back=ASS_SHADOW,
            margin_v=eff_margin,
        )
        if is_long_form:
            header = header.replace("PlayResX: 1080\nPlayResY: 1920", "PlayResX: 1920\nPlayResY: 1080")
            header = header.replace(",235,1", ",55,1").replace(",230,1", ",55,1").replace(",225,1", ",55,1")

        dialogue_lines = []
        current_offset_ms = 0

        for scene in scenes:
            scene_duration_ms = scene.audio_duration_ms or int(scene.estimated_seconds * 1000)
            if scene_duration_ms <= 0:
                scene_duration_ms = 3000

            # Căn chỉnh timeline theo start_offset_ms nếu scene có mốc thời gian cố định
            if getattr(scene, "start_offset_ms", None) is not None:
                current_offset_ms = scene.start_offset_ms

            # Chọn màu sắc cho Power Words theo Niche
            raw_niche = (niche_id or "").lower()
            if "scifi" in raw_niche:
                power_color = "&H00FFFF00"  # Neon Cyan
            elif "rescue" in raw_niche:
                power_color = "&H002080FF"  # Vivid Warm Amber / Orange
            else:
                power_color = "&H000000FF"  # Vivid Crimson Red (High Urgency & Dopamine)

            # Trích xuất bộ từ khóa ngữ cảnh do AI Director chọn cho riêng cảnh này
            raw_hl = getattr(scene, "highlight_words", None) or []
            scene_hl_set = set()
            for hw in raw_hl:
                for token in str(hw).upper().split():
                    cleaned_tok = re.sub(r'[^\w\d]', '', token)
                    if len(cleaned_tok) >= 2:
                        scene_hl_set.add(cleaned_tok)

            word_timings = getattr(scene, "word_timings", None)
            if word_timings and len(word_timings) > 0:
                word_timings = cls.align_word_timings_with_narration(
                    word_timings,
                    scene.narration,
                    scene_duration_ms=scene_duration_ms,
                )

            if word_timings and len(word_timings) > 0:
                # ── CHẾ ĐỘ 1: ĐỒNG BỘ CHÍNH XÁC THEO MỐC TỪNG TỪ CỦA GIỌNG ĐỌC AI ──
                chunks_data = []
                cur_words = []
                cur_len = 0
                for w_info in word_timings:
                    raw_w = w_info.get("word", "").strip()
                    raw_w = re.sub(r'\[(?:LOOP|HOOK|ACT\s*\d*|SCENE\s*\d*|PAUSE|BEAT|SILENCE|WHISPER)[^\]]*\]', '', raw_w, flags=re.IGNORECASE).strip()
                    w_text = raw_w.upper()
                    if not w_text:
                        continue
                    candidate_len = cur_len + len(w_text) + (1 if cur_words else 0)
                    max_w_lim = 6 if is_long_form else 3
                    max_c_lim = 38 if is_long_form else 20
                    should_break = (
                        (len(cur_words) >= max_w_lim or candidate_len > max_c_lim) and len(cur_words) >= 2
                    ) or (
                        w_text.endswith((".", "!", "?", ":", ";", "—")) and len(cur_words) >= 2
                    )
                    if should_break and cur_words:
                        chunks_data.append(cur_words)
                        cur_words = [{**w_info, "word": w_text}]
                        cur_len = len(w_text)
                    else:
                        cur_words.append({**w_info, "word": w_text})
                        cur_len = candidate_len

                if cur_words:
                    chunks_data.append(cur_words)

                # VideoLingo Semantic Rebalance: Không để mạo từ / giới từ ('THE', 'OF', 'IN', 'A', 'TO'...) bị treo ở cuối dòng phụ đề
                for c_i in range(len(chunks_data) - 1):
                    if (
                        len(chunks_data[c_i]) >= 2
                        and cls._is_dangling_proclitic(chunks_data[c_i][-1].get("word", ""))
                        and len(chunks_data[c_i + 1]) <= 3
                    ):
                        moved_w = chunks_data[c_i].pop()
                        chunks_data[c_i + 1].insert(0, moved_w)
                chunks_data = [c for c in chunks_data if c]

                for chunk_idx, c_words in enumerate(chunks_data):
                    chunk_start_ms = current_offset_ms + c_words[0]["start_ms"]
                    chunk_end_ms = current_offset_ms + c_words[-1]["end_ms"]

                    # Chuẩn Y2A-Auto (srt_transform_engine.py & subtitle_qc.py):
                    # 1. Đảm bảo mật độ hiển thị CPS <= 22 ký tự/giây và tối thiểu 450ms
                    vis_chars = sum(len(re.sub(r"\s+", "", w["word"])) for w in c_words)
                    min_req_ms = max(450, min(950, int(vis_chars * 1000.0 / 22.0)))
                    if chunk_end_ms - chunk_start_ms < min_req_ms:
                        chunk_end_ms = chunk_start_ms + min_req_ms

                    # 2. Gộp khoảng nghỉ siêu nhỏ (< 250ms) và chống chồng lấn (Overlap Guard) với chunk kế tiếp
                    if chunk_idx < len(chunks_data) - 1:
                        next_start_ms = current_offset_ms + chunks_data[chunk_idx + 1][0]["start_ms"]
                        if next_start_ms > chunk_end_ms and (next_start_ms - chunk_end_ms) < 250:
                            chunk_end_ms = next_start_ms
                        elif chunk_end_ms > next_start_ms and next_start_ms > chunk_start_ms + 180:
                            chunk_end_ms = next_start_ms

                    words = [w["word"] for w in c_words]
                    word_durations_cs = []
                    for idx_w, w in enumerate(c_words):
                        dur_cs = max(int(w.get("duration_ms", 200) / 10), 5)
                        if idx_w < len(c_words) - 1:
                            gap = c_words[idx_w + 1]["start_ms"] - w["end_ms"]
                            if gap > 0:
                                dur_cs += int(gap / 10)
                        word_durations_cs.append(dur_cs)

                    chunk_pop_tag = r"{\t(0,65,\fscx107\fscy107)\t(65,160,\fscx100\fscy100)}"
                    karaoke_text = cls._build_karaoke_text(
                        words,
                        word_durations_cs,
                        default_highlight=eff_secondary,
                        power_color=power_color,
                        scene_highlights=scene_hl_set,
                    )

                    start_str = cls._ms_to_ass_time(chunk_start_ms)
                    end_str = cls._ms_to_ass_time(chunk_end_ms)

                    dialogue_lines.append(
                        f"Dialogue: 0,{start_str},{end_str},Karaoke,,0,0,0,,{chunk_pop_tag}{karaoke_text}"
                    )
            else:
                # ── CHẾ ĐỘ 2: FALLBACK PHÂN BỔ KÝ TỰ (Khi không có word_timings) ──
                chunks = cls.split_text_to_chunks(
                    scene.narration,
                    max_words=(6 if is_long_form else 3),
                    max_chars=(38 if is_long_form else 20),
                )
                if not chunks:
                    if getattr(scene, "start_offset_ms", None) is not None:
                        current_offset_ms = scene.start_offset_ms + scene_duration_ms
                    else:
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
                    chunk_end_ms = chunk_start_ms + chunk_dur_ms
                    words = chunk.split()

                    total_word_chars = sum(len(w) for w in words)
                    word_durations_cs = []
                    for w in words:
                        ratio = len(w) / total_word_chars if total_word_chars > 0 else 1.0 / len(words)
                        dur_cs = max(int(chunk_dur_ms * ratio / 10), 5)
                        word_durations_cs.append(dur_cs)

                    total_word_cs = sum(word_durations_cs)
                    chunk_cs = chunk_dur_ms // 10
                    if word_durations_cs:
                        word_durations_cs[-1] += (chunk_cs - total_word_cs)
                        word_durations_cs[-1] = max(word_durations_cs[-1], 5)

                    chunk_pop_tag = r"{\t(0,65,\fscx107\fscy107)\t(65,160,\fscx100\fscy100)}"
                    karaoke_text = cls._build_karaoke_text(
                        words,
                        word_durations_cs,
                        default_highlight=eff_secondary,
                        power_color=power_color,
                        scene_highlights=scene_hl_set,
                    )

                    start_str = cls._ms_to_ass_time(chunk_start_ms)
                    end_str = cls._ms_to_ass_time(chunk_end_ms)

                    dialogue_lines.append(
                        f"Dialogue: 0,{start_str},{end_str},Karaoke,,0,0,0,,{chunk_pop_tag}{karaoke_text}"
                    )

                    chunk_start_ms = chunk_end_ms

            if getattr(scene, "start_offset_ms", None) is not None:
                current_offset_ms = scene.start_offset_ms + scene_duration_ms
            else:
                current_offset_ms += scene_duration_ms

        # ── Đã tắt thanh phù hiệu màu vàng ở đỉnh màn hình ([ // ... // ]) để giữ khung hình điện ảnh sạch 100% ──
        if False and scenes:
            raw_niche = (niche_id or "").lower()
            full_txt = " ".join([s.narration for s in scenes]).lower()
            # Phân biệt chuẩn xác Tiếng Việt (vi), Tiếng Bồ Đào Nha Brazil (pt-BR) và Tiếng Anh (en):
            # Tính theo ĐA SỐ CẢNH (Majority Scene Language) để 1 ký tự rò rỉ không bao giờ lật ngược ngôn ngữ toàn bài!
            vn_exclusive_re = re.compile(r'[ơớờởỡợưứừửữựđăắằẳẵặảẩẫậẻểễệỉịỏổỗộủửữựỷỹỵ]', re.IGNORECASE)
            pt_words = {
                "não", "você", "uma", "para", "com", "mais", "pelo", "pela", "está",
                "são", "isso", "até", "mas", "nas", "nos", "dos", "das", "onça",
                "oceano", "mistério", "ciência", "metros", "selva", "amazônia", "pantanal",
                "sucuri", "jacaré", "quando", "apenas", "fundo", "água", "águas",
            }
            txt_words = set(re.findall(r'\b\w+\b', full_txt))
            vi_scene_count = sum(1 for s in scenes if vn_exclusive_re.search(str(getattr(s, "narration", "") or "")))
            is_majority_vi = vi_scene_count >= max(2, (len(scenes) + 1) // 2)
            is_pt = ("ç" in full_txt or "õ" in full_txt or "ã" in full_txt or len(txt_words & pt_words) >= 2) and not is_majority_vi
            is_vi = is_majority_vi and not is_pt

            # ── Tối ưu Anti-Swipe 0-3s: Chỉ hiển thị DUY NHẤT 1 phù hiệu HUD nhỏ gọn (1 dòng, <= 26 ký tự)
            # ở sát mép trên (MarginV=230), tuyệt đối KHÔNG chồng 2 lớp HookHeadlineBanner + PatternInterruptBadge
            # gây che khuất khuôn mặt/chủ thể chính ở trung tâm khung hình!
            custom_open_badge = cls._strip_ass_unsupported_emojis(getattr(scenes[0], "overlay_badge", "") or "").strip()
            effective_hook = cls._strip_ass_unsupported_emojis((hook_title or "").strip())
            # Chặn tuyệt đối phụ đề/banner Tiếng Việt rò rỉ ở đầu video của kênh ngoại ngữ (en / pt-BR)
            if not is_vi:
                if custom_open_badge and (vn_exclusive_re.search(custom_open_badge) or "TỪ IN HOA" in custom_open_badge.upper() or "SỰ THẬT" in custom_open_badge.upper()):
                    custom_open_badge = ""
                if effective_hook and (vn_exclusive_re.search(effective_hook) or "TỪ IN HOA" in effective_hook.upper() or "SỰ THẬT" in effective_hook.upper()):
                    effective_hook = ""
            if custom_open_badge and len(custom_open_badge) <= 24:
                badge_core = custom_open_badge.upper()
            elif effective_hook:
                clean_hook = re.sub(r'[—–―]+', ' ', effective_hook)
                clean_hook = re.sub(r'#\w+', '', clean_hook).strip().upper()
                hook_words = clean_hook.split()
                badge_core = " ".join(hook_words[:4])
                if len(badge_core) > 24:
                    badge_core = badge_core[:24].rstrip()
            elif "psychology" in raw_niche:
                badge_core = "TÂM LÝ ĐEN TỐI" if is_vi else ("MENTE SOMBRIA" if is_pt else "MENTAL TRAP")
            elif any(k in raw_niche for k in ("deep_sea", "ocean")):
                badge_core = "VỰC THẲM BÍ ẨN" if is_vi else ("ABISMO OCULTO" if is_pt else "UNCHARTED ABYSS")
            elif any(k in raw_niche for k in ("predator", "wildlife")):
                badge_core = "ĐỐI ĐẦU SINH TỬ" if is_vi else ("DUELO MORTAL" if is_pt else "APEX DUEL")
            elif any(k in raw_niche for k in ("rescue", "animal")):
                badge_core = "HỒ SƠ CỨU HỘ" if is_vi else ("RESGATE REAL" if is_pt else "RESCUE ARCHIVE")
            elif any(k in raw_niche for k in ("history", "mystery", "alternate")):
                badge_core = "TÀI LIỆU MẬT" if is_vi else ("ARQUIVO SECRETO" if is_pt else "CLASSIFIED FILE")
            elif "horror" in raw_niche:
                badge_core = "HỒ SƠ KINH DỊ" if is_vi else ("LENDA URBANA" if is_pt else "URBAN LEGEND")
            else:
                badge_core = "ĐIỀU CHƯA BIẾT" if is_vi else ("ARQUIVO CRÍTICO" if is_pt else "CRITICAL FILE")

            badge_title = f"[ // {badge_core} // ]"
            first_scene_dur_ms = scenes[0].audio_duration_ms or int(scenes[0].estimated_seconds * 1000)
            badge_end_ms = min(2400, max(1200, first_scene_dur_ms - 150))
            badge_start_str = cls._ms_to_ass_time(0)
            badge_end_str = cls._ms_to_ass_time(badge_end_ms)
            dialogue_lines.insert(0,
                f"Dialogue: 2,{badge_start_str},{badge_end_str},PatternInterruptBadge,,0,0,0,,{{\\fade(120,150)\\t(0,140,\\fscx105\\fscy105)}}{badge_title}"
            )

            # Mid-video & Climax Pattern Interrupt + Quantitative Stat Card Engine (Giây 10-16s và Giây 22-32s):
            # 1. Tự động trích xuất con số/thống kê định lượng trong lời thoại (3,700 PSI, 11,000 METERS, 95%, $400B...) thành Stat Card
            # 2. Hoặc ưu tiên overlay_badge do AI Director đặt / từ khóa bước ngoặt
            # 3. Đặt tối đa 2 phù hiệu HUD cách nhau >= 7.5 giây để phá điểm rơi rụng ở giữa và cao trào video
            stamps_added = 0
            last_stamp_ms = badge_end_ms
            evidence_kw = (
                "bằng chứng", "sự thật", "dấu hiệu", "nghiên cứu", "quy tắc", "nhưng", "bất ngờ",
                "evidence", "truth", "sign", "proof", "rule", "until", "however", "suddenly", "yet",
                "verdade", "prova", "sinal", "estudo", "regra", "mas", "porém", "até que", "de repente", "cientistas", "ninguém",
            )
            stat_regex = re.compile(
                r'(\$?\d[\d,\.]*\s*(?:%|percent|por cento|psi|mph|km/h|km\b|meters|metros|metro|m\b|feet|ft|tons|toneladas|kg|quilos|lbs|pound|pounds|billion|bilhões|million|milhões|trillion|b\b|m\b|milliseconds|seconds|segundos|sec|ms|hours|horas|days|dias|years|anos|°c|°f|graus|db|decibels|decibéis|x\b))',
                re.IGNORECASE,
            )
            mid_scenes_pool = scenes[1:-1] if len(scenes) >= 3 else scenes[1:]
            for idx, sc in enumerate(mid_scenes_pool, start=2):
                if stamps_added >= 2:
                    break
                sc_start_ms = getattr(sc, "start_offset_ms", None)
                if sc_start_ms is None:
                    sc_start_ms = sum(s.audio_duration_ms or int(s.estimated_seconds * 1000) for s in scenes[:idx-1])
                if sc_start_ms - last_stamp_ms < 4000:
                    continue

                sc_badge = cls._strip_ass_unsupported_emojis(getattr(sc, "overlay_badge", "") or "")
                if not is_vi and sc_badge and (vn_exclusive_re.search(sc_badge) or "TỪ IN HOA" in sc_badge.upper()):
                    sc_badge = ""
                sc_txt = sc.narration.lower()
                stat_match = stat_regex.search(sc.narration)

                if sc_badge or stat_match or any(k in sc_txt for k in evidence_kw):
                    sc_dur = sc.audio_duration_ms or int(sc.estimated_seconds * 1000)
                    stamp_end_ms = sc_start_ms + min(2400, sc_dur)
                    stamp_start_str = cls._ms_to_ass_time(sc_start_ms + 100)
                    stamp_end_str = cls._ms_to_ass_time(stamp_end_ms)
                    if sc_badge:
                        stamp_title = f"[ // {sc_badge.upper()} // ]"
                    elif stat_match:
                        raw_stat = stat_match.group(1).strip().upper()
                        prefix = "DỮ LIỆU" if is_vi else ("DADOS" if is_pt else "DATA")
                        stamp_title = f"[ // {prefix}: {raw_stat} // ]"
                    else:
                        if stamps_added == 0:
                            stamp_title = "[ // BƯỚC NGOẶT // ]" if is_vi else ("[ // A REVIRAVOLTA // ]" if is_pt else "[ // THE TURNING POINT // ]")
                        else:
                            stamp_title = "[ // SỰ THẬT CUỐI CÙNG // ]" if is_vi else ("[ // REVELAÇÃO FINAL // ]" if is_pt else "[ // THE FINAL REVEAL // ]")
                    dialogue_lines.append(
                        f"Dialogue: 2,{stamp_start_str},{stamp_end_str},PatternInterruptBadge,,0,0,0,,{{\\fade(150,150)\\t(0,140,\\fscx108\\fscy108)\\t(140,260,\\fscx100\\fscy100)}}{stamp_title}"
                    )
                    stamps_added += 1
                    last_stamp_ms = stamp_end_ms

            if stamps_added == 0 and len(scenes) >= 4:
                mid_idx = min(3, len(scenes) // 2)  # Cảnh 4 (0-indexed = 3) ~ giây 12-15
                sc = scenes[mid_idx]
                sc_start_ms = getattr(sc, "start_offset_ms", None)
                if sc_start_ms is None:
                    sc_start_ms = sum(s.audio_duration_ms or int(s.estimated_seconds * 1000) for s in scenes[:mid_idx])
                sc_dur = sc.audio_duration_ms or int(sc.estimated_seconds * 1000)
                stamp_end_ms = sc_start_ms + min(2400, sc_dur)
                stamp_start_str = cls._ms_to_ass_time(sc_start_ms + 100)
                stamp_end_str = cls._ms_to_ass_time(stamp_end_ms)
                stamp_title = "[ // SỰ THẬT NGẦM // ]" if is_vi else ("[ // O SEGREDO OCULTO // ]" if is_pt else "[ // THE HIDDEN TWIST // ]")
                dialogue_lines.append(
                    f"Dialogue: 2,{stamp_start_str},{stamp_end_str},PatternInterruptBadge,,0,0,0,,{{\\fade(150,150)\\t(0,140,\\fscx108\\fscy108)\\t(140,260,\\fscx100\\fscy100)}}{stamp_title}"
                )

        # ── Kích hoạt Visual CTA Banner (Like + Comment + Subscribe) ở phân cảnh cuối (chỉ áp dụng cho Shorts) ──
        if show_cta_badge and (not is_long_form) and scenes and current_offset_ms > 3000:
            last_scene = scenes[-1]
            last_dur = last_scene.audio_duration_ms or int(last_scene.estimated_seconds * 1000)
            cta_dur = max(min(last_dur, 4200), 3200)
            cta_start_ms = max(0, current_offset_ms - cta_dur)
            cta_end_ms = current_offset_ms
            full_text = " ".join([s.narration for s in scenes]).lower()
            vn_exclusive_re = re.compile(r'[ơớờởỡợưứừửữựđăắằẳẵặảẩẫậẻểễệỉịỏổỗộủửữựỷỹỵ]', re.IGNORECASE)
            pt_words = {
                "não", "você", "uma", "para", "com", "mais", "pelo", "pela", "está",
                "são", "isso", "até", "mas", "nas", "nos", "dos", "das", "onça",
                "oceano", "mistério", "ciência", "metros", "selva", "amazônia", "pantanal",
                "sucuri", "jacaré", "quando", "apenas", "fundo", "água", "águas",
            }
            txt_words = set(re.findall(r'\b\w+\b', full_text))
            vi_scene_count = sum(1 for s in scenes if vn_exclusive_re.search(str(getattr(s, "narration", "") or "")))
            is_majority_vi = vi_scene_count >= max(2, (len(scenes) + 1) // 2)
            is_pt = ("ç" in full_text or "õ" in full_text or "ã" in full_text or len(txt_words & pt_words) >= 2) and not is_majority_vi
            is_vi = is_majority_vi and not is_pt
            raw_niche = (niche_id or "").lower()

            cta_text = cls._build_visual_cta_banner_text(raw_niche, is_vi, is_pt)
            cta_start_str = cls._ms_to_ass_time(cta_start_ms)
            cta_end_str = cls._ms_to_ass_time(cta_end_ms)
            dialogue_lines.append(
                f"Dialogue: 1,{cta_start_str},{cta_end_str},CTABadge,,0,0,0,,{{\\fade(200,150)\\t(0,180,\\fscx108\\fscy108)\\t(180,360,\\fscx100\\fscy100)}}{cta_text}"
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
        fontsize: int = 68,
        margin_v: int = 720,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        niche_id: Optional[str] = None,
        credit_text: Optional[str] = None,
        show_cta_badge: bool = True,
        hook_title: Optional[str] = None,
        is_long_form: bool = False,
    ) -> str:
        """Tạo và lưu file .ass ra ổ đĩa, hỗ trợ Niche Profile, Watermark Credit, Hook Headline Banner & Long-Form 16:9."""
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
            hook_title=hook_title,
            is_long_form=is_long_form,
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

            scene_highlights = set()
            for hw in (getattr(scene, "highlight_words", None) or []):
                for tok in str(hw).split():
                    cl_tok = re.sub(r'[^\w\d]', '', tok.upper())
                    if len(cl_tok) >= 2:
                        scene_highlights.add(cl_tok)

            word_timings = getattr(scene, "word_timings", None)
            if word_timings and len(word_timings) > 0:
                for w_info in word_timings:
                    w = w_info.get("word", "").strip()
                    clean_word = re.sub(r'[^\w\d]', '', w.upper())
                    if clean_word in POWER_WORDS or clean_word in scene_highlights:
                        word_ts_s = (current_offset_ms + w_info.get("start_ms", 0)) / 1000.0
                        if word_ts_s - last_ts_s >= min_interval_s and word_ts_s >= 0.5:
                            if any(k in clean_word for k in ("NGUY", "HIỂM", "CHẾT", "DANGER", "DEADLY", "KILL", "TỬ", "DESTROY", "CRASH", "DOOM")):
                                sfx_name = "impact_sub"
                                vol = 0.24
                            elif any(k in clean_word for k in ("BẪY", "DỐI", "THAO", "TÚNG", "LIES", "TRAP", "MISTAKE", "SCAM", "ILLUSION")):
                                sfx_name = "glitch"
                                vol = 0.22
                            elif any(k in clean_word for k in ("BÍ", "MẬT", "SỰ", "THẬT", "SECRET", "TRUTH", "HIDDEN", "FORBIDDEN")):
                                sfx_name = "reveal"
                                vol = 0.22
                            else:
                                sfx_name = "whoosh_fast"
                                vol = 0.20
                            cues.append((round(word_ts_s, 2), sfx_name, vol))
                            last_ts_s = word_ts_s
            else:
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
                        if clean_word in POWER_WORDS or clean_word in scene_highlights:
                            word_ts_s = (chunk_start_ms + word_offset_ms) / 1000.0
                            if word_ts_s - last_ts_s >= min_interval_s and word_ts_s >= 0.5:
                                if any(k in clean_word for k in ("NGUY", "HIỂM", "CHẾT", "DANGER", "DEADLY", "KILL", "TỬ", "DESTROY", "CRASH", "DOOM")):
                                    sfx_name = "impact_sub"
                                    vol = 0.24
                                elif any(k in clean_word for k in ("BẪY", "DỐI", "THAO", "TÚNG", "LIES", "TRAP", "MISTAKE", "SCAM", "ILLUSION")):
                                    sfx_name = "glitch"
                                    vol = 0.22
                                elif any(k in clean_word for k in ("BÍ", "MẬT", "SỰ", "THẬT", "SECRET", "TRUTH", "HIDDEN", "FORBIDDEN")):
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
        fontsize: int = 68,
        margin_v: int = 720,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        niche_id: Optional[str] = None,
        credit_text: Optional[str] = None,
        show_cta_badge: bool = True,
    ) -> str:
        """
        Tạo nội dung file .ass từ mốc thời gian thực của giọng đọc (ASR/Whisper/Transcript).
        - Trích xuất từng câu/cụm từ có mốc start và duration thực.
        - Khử hoàn toàn chuỗi rác template (placeholder sanitizer).
        - Phân chia thành cụm từ ngắn (2-4 từ) và gắn tag karaoke {\\kf} theo từng từ.
        - Căn chuẩn Safe Zone 30-35% từ đáy (margin_v=720), chống va chạm layer với CTA badge.
        """
        eff_primary = primary_color or ASS_WHITE
        eff_secondary = secondary_color or ASS_YELLOW
        eff_outline = outline_color or ASS_BLACK
        eff_font = fontname
        eff_size = fontsize if fontsize >= 62 else 68
        eff_margin = margin_v

        if niche_id:
            try:
                from app.services.niche_manager import get_niche, get_niche_subtitle_style
                niche_prof = get_niche(niche_id)
                niche_sub = get_niche_subtitle_style(niche_prof)
                eff_primary = primary_color or niche_sub.get("primary_ass", ASS_WHITE)
                eff_secondary = secondary_color or niche_sub.get("secondary_ass", ASS_YELLOW)
                eff_outline = outline_color or niche_sub.get("outline_ass", ASS_BLACK)
                if not fontname or fontname in ("Arial", "Arial Black", "Montserrat ExtraBold"):
                    eff_font = niche_sub.get("fontname", "Montserrat ExtraBold")
                eff_size = fontsize if fontsize not in (42, 44, 68) and fontsize >= 62 else niche_sub.get("fontsize", 68)
                eff_margin = margin_v if margin_v not in (580, 720) else niche_sub.get("margin_v", 720)
            except Exception:
                pass

        eff_font = cls._resolve_effective_font(eff_font)

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

        # ── Kích hoạt Visual CTA Banner (Like + Comment + Subscribe) ở 3.5s - 4.2s cuối (MarginV=465 tránh đè Subtitle) ──
        if show_cta_badge and effective_total_ms > 4000:
            cta_dur = min(4000, max(2800, int(effective_total_ms * 0.16)))
            cta_start_ms = max(0, effective_total_ms - cta_dur)
            cta_end_ms = effective_total_ms
            all_text_concat = " ".join([seg.get("text", "") for seg in timed_segments]).lower()
            vn_exclusive_re = re.compile(r'[ơớờởỡợưứừửữựđăắằẳẵặảẩẫậẻểễệỉịỏổỗộủửữựỷỹỵ]', re.IGNORECASE)
            vi_seg_count = sum(1 for seg in timed_segments if vn_exclusive_re.search(str(seg.get("text", "") or "")))
            is_majority_vi = vi_seg_count >= max(2, (len(timed_segments) + 1) // 2)
            is_pt = ("ç" in all_text_concat or "õ" in all_text_concat or "ã" in all_text_concat) and not is_majority_vi
            is_vi = is_majority_vi and not is_pt
            raw_niche = (niche_id or "").lower()
            cta_text = cls._build_visual_cta_banner_text(raw_niche, is_vi, is_pt)
            cta_start_str = cls._ms_to_ass_time(cta_start_ms)
            cta_end_str = cls._ms_to_ass_time(cta_end_ms)
            dialogue_lines.append(
                f"Dialogue: 1,{cta_start_str},{cta_end_str},CTABadge,,0,0,0,,{{\\fade(200,150)\\t(0,180,\\fscx108\\fscy108)\\t(180,360,\\fscx100\\fscy100)}}{cta_text}"
            )

        # ── Watermark Credit ở đỉnh video (Layer 2) ──
        if credit_text and credit_text.strip():
            clean_credit = cls._strip_ass_unsupported_emojis(credit_text.strip().replace("\n", " "))
            credit_end_str = cls._ms_to_ass_time(effective_total_ms if effective_total_ms > 0 else 60000)
            dialogue_lines.insert(0,
                f"Dialogue: 2,0:00:00.00,{credit_end_str},WatermarkStyle,,0,0,0,,{{\\fade(300,300)}}// {clean_credit}"
            )

        return header + "\n".join(dialogue_lines) + "\n"

    @classmethod
    def save_ass_from_timed_segments(
        cls,
        timed_segments: List[Dict[str, Any]],
        output_path: str,
        total_duration_sec: float = 0.0,
        clip_start_offset: float = 0.0,
        fontname: str = "Montserrat ExtraBold",
        fontsize: int = 68,
        margin_v: int = 720,
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
        fontname: str = "Montserrat ExtraBold",
        fontsize: int = 68,
        margin_v: int = 720,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        niche_id: Optional[str] = None,
        credit_text: Optional[str] = None,
        is_english: bool = True,
        is_voiceover: bool = False,
    ) -> str:
        """
        Xây dựng phụ đề chuyên biệt cho kịch bản Nấu Lại Đa Phân Đoạn (Multi-Clip Mashup):
        1. Layer 2: Chapter / Timeline Context Badges ở ĐỈNH MÀN HÌNH (MarginV=260, Alignment=8).
           Hiển thị nhãn bối cảnh (ví dụ: '🚨 THE RESCUE AT 2:00 AM', '⏪ 3 HOURS EARLIER (FLASHBACK)')
           giúp người xem nắm bắt ngay mạch thời gian đảo cảnh, chống loạn nhịp.
        2. Layer 0: Kinetic Captions Karaoke giật từng từ ở ĐÁY MÀN HÌNH (MarginV=720, Alignment=2).
           Font to, viền dày, chữ trắng quét sáng Vàng rực rỡ (Yellow Wipe) với Power Words đỏ/cam.
           Đảm bảo 100% video luôn có phụ đề, cứu vãn 70%+ người xem lướt Shorts ở chế độ tắt tiếng.
        """
        eff_primary = primary_color or ASS_WHITE
        eff_secondary = secondary_color or ASS_YELLOW
        eff_outline = outline_color or ASS_BLACK
        eff_back = "&H90000000"
        eff_font = fontname or "Montserrat ExtraBold"
        eff_size = fontsize if (fontsize and fontsize >= 62) else 68
        eff_margin = margin_v or 720

        if niche_id:
            try:
                from app.services.niche_manager import get_niche, get_niche_subtitle_style
                niche_prof = get_niche(niche_id)
                niche_sub = get_niche_subtitle_style(niche_prof)
                eff_primary = primary_color or niche_sub.get("primary_ass", ASS_WHITE)
                eff_secondary = secondary_color or niche_sub.get("secondary_ass", ASS_YELLOW)
                eff_outline = outline_color or niche_sub.get("outline_ass", ASS_BLACK)
                eff_font = fontname or niche_sub.get("fontname", "Montserrat ExtraBold")
                eff_size = fontsize if (fontsize and fontsize not in (42, 44, 68) and fontsize >= 62) else niche_sub.get("fontsize", 68)
            except Exception:
                pass

        eff_font = cls._resolve_effective_font(eff_font)

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
            if timed_trans and len(timed_trans) > 0 and not is_voiceover:
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
                raw_text = c.get("custom_voiceover_text") or c.get("transcript_snippet") or c.get("text") or c.get("summary") or ""
                clean_text = cls.sanitize_subtitle_text(raw_text)
                if not clean_text or len(clean_text.split()) < 2:
                    clean_text = default_narrs.get(role, f"WATCH THIS SCENE #{c_idx}!")

                chunks = cls.split_text_to_chunks(clean_text, max_words=3, max_chars=20)
                if not chunks:
                    chunks = [clean_text[:25]]
                # Nếu có voice_dur_ms thì phụ đề karaoke phân bổ theo thời gian đọc thật của voice, không bị kéo dãn lê thê
                raw_voice_ms = c.get("voice_dur_ms")
                sub_active_ms = min(clip_dur_ms, int(raw_voice_ms)) if raw_voice_ms and int(raw_voice_ms) > 300 else clip_dur_ms
                total_ch = sum(len(ch) for ch in chunks)
                chunk_durs_ms = [max(int(sub_active_ms * (len(ch) / total_ch)), 400) for ch in chunks]
                diff = sub_active_ms - sum(chunk_durs_ms)
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
        fontsize: int = 68,
        margin_v: int = 720,
        niche_id: Optional[str] = None,
        credit_text: Optional[str] = None,
        is_english: bool = True,
        is_voiceover: bool = False,
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
            is_voiceover=is_voiceover,
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
