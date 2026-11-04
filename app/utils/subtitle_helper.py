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
from typing import List, Optional, Dict, Any, Tuple
from datetime import timedelta

from app.schemas.contracts import SceneContract


# ── Màu sắc ASS (ABGR hex, bắt đầu bằng &H) ──────────────────────────────
# ASS format: &HAABBGGRR (alpha, blue, green, red)
ASS_WHITE       = "&H00FFFFFF"   # Trắng tinh khiết — màu chữ chính sắc nét, dễ đọc trên mọi nền
ASS_SOFT_WHITE  = "&H00E4E4E4"   # Trắng ngọc dịu (#E4E4E4) — màu chữ sắp đọc trong cùng câu (không bị nửa xanh nửa trắng)
ASS_DIM_SILVER  = "&H00E4E4E4"   # Alias tương thích ngược
ASS_YELLOW      = "&H0000E5FF"   # Vàng Gold ấm (#FFE500) — nhấn từ khóa / số liệu trọng tâm (tối đa 1 từ/cụm)
ASS_RED         = "&H000000FF"   # Đỏ rực — từ khóa nguy hiểm
ASS_NEON_GREEN  = "&H0033FF33"   # Xanh lá Neon (#33FF33) — dự phòng
ASS_CRIMSON     = "&H002222FF"   # Đỏ Crimson (#FF2222) — nhấn từ khóa cảnh báo / xung đột mạnh
ASS_CYAN        = "&H00FFFF00"   # Xanh Cyan (#00FFFF) — dự phòng
ASS_BLACK       = "&H00000000"   # Đen — viền chữ tương phản cao
ASS_SHADOW      = "&H96000000"   # Đen ~59% trong suốt — bóng đổ thanh thoát, không bết nền

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
WrapStyle: 2
YCbCr Matrix: None

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{fontname},{fontsize},{primary},{secondary},{outline},{back},-1,0,0,0,100,100,0.8,0,1,3.8,1.8,2,115,115,{margin_v},1
Style: PatternInterruptBadge,{fontname},46,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,-1,0,0,0,100,100,1.4,0,1,3.4,1.6,8,80,80,190,1
Style: AutoThumbBanner,{fontname},64,&H0000E5FF,&H00FFFFFF,&H00000000,&HB4000000,-1,0,0,0,100,100,1.5,0,1,4.8,2.4,8,90,90,44,1
Style: WatermarkStyle,Arial,34,&H38FFFFFF,&H38FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.0,1.2,8,60,60,90,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# ── Fonts ưu tiên (Ưu tiên Montserrat ExtraBold nhúng sẵn trong assets/fonts, fallback Segoe UI / Arial) ──
PREFERRED_FONTS = ["Montserrat ExtraBold", "Montserrat Black", "Montserrat", "Arial Black", "Segoe UI", "Arial", "Roboto"]

# Map tên font hệ thống Windows thực tế có sẵn (tránh truyền Google Web Fonts chưa cài như Cinzel/Outfit khiến libass rớt về Arial mảnh)
_WINDOWS_INSTALLED_FONT_FILES = {
    "arial black": "ariblk.ttf",
    "arial": "arialbd.ttf",
    "segoe ui": "segoeuib.ttf",
    "impact": "impact.ttf",
    "verdana": "verdanab.ttf",
    "tahoma": "tahomabd.ttf",
    "georgia": "georgiab.ttf",
}


class SubtitleHelper:
    """Helper xử lý và tạo phụ đề video — ASS (clean safe-zone) + SRT (legacy)."""

    # ── Utilities ─────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_effective_font(requested_font: Optional[str] = None) -> str:
        """
        Tự động chọn font chữ dày, sắc nét chuẩn Shorts (Montserrat ExtraBold / Black)
        từ kho assets/fonts đã nhúng sẵn, hoặc giữ font hệ thống nếu thực sự tồn tại trên máy.
        Chặn lỗi truyền web-font chưa cài (Cinzel, Outfit, Orbitron, Poppins...) làm libass fallback về Arial mảnh.
        """
        req = (requested_font or "").split(",")[0].strip()
        req_low = req.lower()
        fonts_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"
        has_extra_bold = (fonts_dir / "Montserrat-ExtraBold.ttf").exists()
        has_black = (fonts_dir / "Montserrat-Black.ttf").exists()
        bundled_default = "Montserrat ExtraBold" if has_extra_bold else ("Montserrat Black" if has_black else "")

        if req_low in ("montserrat extrabold", "montserrat extra bold") and has_extra_bold:
            return "Montserrat ExtraBold"
        if req_low == "montserrat black" and has_black:
            return "Montserrat Black"
        if req_low in ("", "montserrat", "sans-serif") and bundled_default:
            return bundled_default

        win_fonts = Path("C:/Windows/Fonts")
        mapped_ttf = _WINDOWS_INSTALLED_FONT_FILES.get(req_low)
        if mapped_ttf and win_fonts.exists() and (win_fonts / mapped_ttf).exists():
            return req

        if bundled_default:
            return bundled_default

        if req_low == "arial black" and win_fonts.exists() and not (win_fonts / "ariblk.ttf").exists():
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

    # Các từ nối / mạo từ / giới từ / đại từ chỉ định gắn chặt với cụm danh từ hoặc động từ phía sau:
    # Không bao giờ để các từ này đứng trơ trọi ở cuối một dòng hoặc cuối một thẻ phụ đề.
    _PROCLITIC_ATTACH_NEXT_WORDS = frozenset({
        # English
        "A", "AN", "THE", "OF", "IN", "ON", "AT", "TO", "FOR", "WITH", "BY", "FROM", "INTO",
        "UNTIL", "UNDER", "OVER", "THROUGH", "WITHOUT", "WITHIN", "AGAINST", "BETWEEN", "AMONG",
        "ACROSS", "AROUND", "BEHIND", "BEYOND", "DURING", "BEFORE", "AFTER", "TOWARD", "TOWARDS",
        "UPON", "BENEATH", "BESIDE", "AND", "OR", "BUT", "IF", "AS", "THAN", "THAT", "THIS",
        "THESE", "THOSE", "EACH", "EVERY", "SOME", "ANY", "SUCH", "ANOTHER", "BECAUSE", "WHILE",
        "WHEN", "WHERE", "WHOSE", "WHICH", "WHAT", "MY", "YOUR", "HIS", "HER", "ITS", "OUR",
        "THEIR", "IS", "ARE", "WAS", "WERE", "BE", "BEEN", "HAS", "HAVE", "HAD", "WILL", "CAN",
        "COULD", "WOULD", "SHOULD", "MAY", "MIGHT", "MUST", "NO", "NOT",
        "YET", "SO", "NOR", "STILL", "EVEN", "ONCE", "JUST", "ONLY", "ALSO", "THEN", "NOW", "SOON", "THUS",
        # Portuguese (pt-BR)
        "O", "A", "OS", "AS", "UM", "UMA", "UNS", "UMAS", "DE", "DO", "DA", "DOS", "DAS",
        "DESTE", "DESTA", "DESTES", "DESTAS", "DISTO", "DESSE", "DESSA", "DESSES", "DESSAS", "DISSO",
        "DAQUELE", "DAQUELA", "DAQUELES", "DAQUELAS", "EM", "NO", "NA", "NOS", "NAS",
        "NESTE", "NESTA", "NESTES", "NESTAS", "NESSE", "NESSA", "NESSES", "NESSAS", "NUM", "NUMA",
        "AO", "AOS", "À", "ÀS", "POR", "PELO", "PELA", "PELOS", "PELAS", "PARA", "COM", "SEM",
        "SOB", "SOBRE", "ENTRE", "CONTRA", "ATÉ", "APÓS", "DESDE", "DURANTE", "PERANTE",
        "ESSE", "ESSA", "ESTE", "ESTA", "AQUELE", "AQUELA", "CADA", "TODO", "TODA", "TODOS", "TODAS",
        "QUE", "SE", "OU", "E", "MAS", "MEU", "MINHA", "MEUS", "MINHAS", "SEU", "SUA", "SEUS", "SUAS",
        "FOI", "ERA", "SÃO", "ESTÁ", "ESTÃO", "NÃO", "MAIS", "MUITO", "TÃO",
        "PORÉM", "AINDA", "ENTÃO", "AGORA", "LOGO", "POIS", "JÁ", "SÓ", "NEM",
        # Vietnamese
        "CỦA", "TRONG", "TRÊN", "DƯỚI", "VỚI", "CHO", "TỪ", "MỘT", "NHỮNG", "CÁC", "LÀ", "VÀ",
        "ĐỂ", "NHƯ", "BỞI", "TẠI", "VỀ", "MỌI", "MỖI",
        "NHƯNG", "TUY", "BỖNG", "CHỢT", "NGAY", "CHỈ", "CŨNG", "VẪN", "ĐÃ", "SẼ", "ĐANG",
    })

    _ABBREVIATIONS = frozenset({
        "DR.", "MR.", "MRS.", "MS.", "VS.", "ST.", "PROF.", "GEN.", "NO.", "SR.", "JR.", "U.S.", "E.G.", "I.E.",
    })

    _NUMBER_LEAD_WORDS = frozenset({
        "TEN", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY", "NINETY", "HUNDRED",
        "DEZ", "VINTE", "TRINTA", "QUARENTA", "CINQUENTA", "CEM", "DUZENTOS", "TREZENTOS", "QUINHENTOS",
        "MƯỜI", "TRĂM",
    })

    _NUMBER_SCALE_WORDS = frozenset({
        "HUNDRED", "THOUSAND", "MILLION", "BILLION", "TRILLION",
        "MIL", "MILHÃO", "MILHÕES", "BILHÃO", "BILHÕES",
        "TRĂM", "NGHÌN", "NGÀN", "TRIỆU", "TỶ",
    })

    @classmethod
    def _is_dangling_proclitic(cls, token: str, next_token: str = "") -> bool:
        raw = str(token or "").strip()
        if not raw or raw.endswith((".", "!", "?", ":", ";", ",", "—", "…")):
            return False
        if raw.endswith("-"):
            return True
        # Từ chỉ kích thước gạch nối như "16-FOOT", "4,000-POUND" luôn gắn chặt với danh từ ngay sau
        if re.search(r"\d", raw) and "-" in raw:
            return True
        clean = re.sub(r"[^\w\d]", "", raw.upper())
        if clean in cls._PROCLITIC_ATTACH_NEXT_WORDS:
            return True
        if next_token:
            next_clean = re.sub(r"[^\w\d]", "", str(next_token).upper())
            if clean in cls._NUMBER_LEAD_WORDS and next_clean in cls._NUMBER_SCALE_WORDS:
                return True
            # Số lượng từ (vd: '70', '1,500', '2.000') đi ngay trước danh từ/đơn vị ('quilos', 'PSI', 'meters') luôn gắn liền thành 1 cụm
            if re.fullmatch(r"\d+(?:[.,]\d+)*", raw) and next_clean and next_clean not in cls._PROCLITIC_ATTACH_NEXT_WORDS:
                return True
        return False

    @classmethod
    def _ends_clause(cls, token: str) -> bool:
        raw = str(token or "").strip()
        if not raw:
            return False
        up = raw.upper()
        if up in cls._ABBREVIATIONS:
            return False
        if up.endswith(".") and len(re.sub(r"[^\w]", "", up)) == 1:
            return False
        return raw.endswith((".", "!", "?", ":", ";", ",", "—", "…"))

    @classmethod
    def _chunk_word_items(
        cls,
        items: List[Dict[str, Any]],
        max_words: int = 4,
        max_chars: int = 22,
    ) -> List[List[Dict[str, Any]]]:
        """
        Chia danh sách token (có hoặc không có mốc thời gian) thành các cụm ngữ nghĩa tự nhiên:
        - Luôn ngắt SAU dấu câu kết mệnh đề ('.', '!', '?', ':', ';', ',', '—'), TUYỆT ĐỐI không ngắt trước dấu chấm
          làm rơi từ cuối câu thành thẻ 1 từ lẻ loi ('PREDATOR.', 'TOXINAS.', 'WATERS.').
        - Không bao giờ để mạo từ / giới từ / đại từ chỉ định ('THE', 'OF', 'IN', 'A', 'DESTE', 'PARA'...) bị treo cuối thẻ.
        - Tự động cân bằng lại cụm cuối mệnh đề để tránh thẻ 1 từ chớp nháy khó đọc.
        """
        if not items:
            return []

        # Bước 1: Tách theo ranh giới mệnh đề tự nhiên (dấu phẩy / chấm câu)
        clauses: List[List[Dict[str, Any]]] = []
        cur_clause: List[Dict[str, Any]] = []
        for it in items:
            w = str(it.get("word", "")).strip()
            if not w:
                continue
            cur_clause.append(it)
            if cls._ends_clause(w):
                clauses.append(cur_clause)
                cur_clause = []
        if cur_clause:
            clauses.append(cur_clause)

        final_chunks: List[List[Dict[str, Any]]] = []

        for clause in clauses:
            if not clause:
                continue
            clause_text = " ".join(str(x["word"]) for x in clause)
            if len(clause) <= max_words and len(clause_text) <= max_chars + 2:
                final_chunks.append(clause)
                continue

            c_chunks: List[List[Dict[str, Any]]] = []
            cur: List[Dict[str, Any]] = []
            cur_len = 0

            for idx, it in enumerate(clause):
                w = str(it["word"])
                next_w = str(clause[idx + 1]["word"]) if idx + 1 < len(clause) else ""
                cand_len = cur_len + len(w) + (1 if cur else 0)

                # Nếu từ hiện tại là từ nối/mạo từ mở đầu cụm mới và thêm từ kế tiếp sẽ vượt ngưỡng -> ngắt TRƯỚC từ nối
                would_strand_proclitic = (
                    len(cur) >= 2
                    and bool(next_w)
                    and cls._is_dangling_proclitic(w, next_w)
                    and (len(cur) + 1 >= max_words or (cand_len + 1 + len(next_w)) > max_chars + 1)
                )
                # Giữ từ nối cuối cùng của cur gắn với danh từ/động từ hiện tại nếu không quá dài
                prev_is_proclitic = bool(cur) and cls._is_dangling_proclitic(str(cur[-1]["word"]), w)
                exceeds_limit = (len(cur) >= max_words or cand_len > max_chars) and not (
                    prev_is_proclitic and len(cur) == 1 and cand_len <= max_chars + 8
                )

                if cur and (would_strand_proclitic or exceeds_limit):
                    c_chunks.append(cur)
                    cur = [it]
                    cur_len = len(w)
                else:
                    cur.append(it)
                    cur_len = cand_len

            if cur:
                c_chunks.append(cur)

            # Bước 2: Đẩy từ nối/mạo từ bị treo ở cuối chunk sang đầu chunk kế tiếp trong cùng mệnh đề
            for i in range(len(c_chunks) - 1):
                while len(c_chunks[i]) >= 2 and cls._is_dangling_proclitic(
                    str(c_chunks[i][-1]["word"]),
                    str(c_chunks[i + 1][0]["word"]) if c_chunks[i + 1] else "",
                ):
                    moved = c_chunks[i].pop()
                    c_chunks[i + 1].insert(0, moved)

            # Bước 3: Chống thẻ 1 từ mồ côi ở bất kỳ vị trí nào trong mệnh đề (vd: ["VANISHED", "INTO", "DEEP"] + ["WATERS."])
            idx_m = len(c_chunks) - 1
            while idx_m >= 1:
                if len(c_chunks[idx_m]) == 1:
                    prev_c = c_chunks[idx_m - 1]
                    curr_c = c_chunks[idx_m]
                    merged_txt = " ".join(str(x["word"]) for x in (prev_c + curr_c))
                    if len(prev_c) + len(curr_c) <= max_words and len(merged_txt) <= max_chars + 4:
                        prev_c.extend(curr_c)
                        c_chunks.pop(idx_m)
                    elif len(prev_c) >= 2:
                        # Thử tách cụm danh từ/giới từ cuối của prev_c sang curr_c để cả hai đều cân đối
                        peel_count = 1
                        while (
                            peel_count < len(prev_c)
                            and cls._is_dangling_proclitic(
                                str(prev_c[len(prev_c) - 1 - peel_count]["word"]),
                                str(prev_c[len(prev_c) - peel_count]["word"]),
                            )
                        ):
                            peel_count += 1
                        rem_prev = len(prev_c) - peel_count
                        if rem_prev >= 2 or (rem_prev == 1 and idx_m >= 2):
                            moved_tail = prev_c[rem_prev:]
                            del prev_c[rem_prev:]
                            c_chunks[idx_m] = moved_tail + curr_c
                        elif len(prev_c) + len(curr_c) <= max_words + 1 and len(merged_txt) <= max_chars + 12:
                            prev_c.extend(curr_c)
                            c_chunks.pop(idx_m)
                idx_m -= 1

            # Bước 4: Chống thẻ 1 từ chớp nháy ở đầu mệnh đề (vd: ["YET"] + ["A", "TWO-THOUSAND-POUND"])
            if len(c_chunks) >= 2 and len(c_chunks[0]) == 1:
                first_w = str(c_chunks[0][0]["word"])
                next_first_w = str(c_chunks[1][0]["word"]) if c_chunks[1] else ""
                merged_head_txt = " ".join(str(x["word"]) for x in (c_chunks[0] + c_chunks[1]))
                if (
                    cls._is_dangling_proclitic(first_w, next_first_w)
                    and len(c_chunks[0]) + len(c_chunks[1]) <= max_words + 1
                    and len(merged_head_txt) <= max_chars + 10
                ) or (
                    len(c_chunks[0]) + len(c_chunks[1]) <= max_words
                    and len(merged_head_txt) <= max_chars + 8
                ):
                    c_chunks[1].insert(0, c_chunks[0][0])
                    c_chunks.pop(0)

            final_chunks.extend([c for c in c_chunks if c])

        return final_chunks

    @classmethod
    def split_text_to_chunks(cls, text: str, max_words: int = 4, max_chars: int = 22) -> List[str]:
        """
        Chia văn bản thành các cụm từ ngữ nghĩa tự nhiên, dễ đọc (2-4 từ, tối đa ~22 ký tự cho Shorts):
        - Ngắt đúng ranh giới dấu câu, không bỏ rơi từ cuối câu thành thẻ 1 từ.
        - Không bao giờ để mạo từ / giới từ / liên từ ('THE', 'OF', 'IN', 'A', 'DESTE', 'PARA'...) treo cuối dòng.
        """
        text = cls.sanitize_subtitle_text(text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return []

        words = text.split()
        if len(words) <= max_words and len(text) <= max_chars + 2:
            return [text]

        items = [{"word": w} for w in words]
        chunked = cls._chunk_word_items(items, max_words=max_words, max_chars=max_chars)
        return [" ".join(it["word"] for it in grp) for grp in chunked if grp]

    @staticmethod
    def sanitize_subtitle_text(text: str) -> str:
        """
        Khử toàn bộ chuỗi rác template, placeholder, stage tags ([LOOP], [HOOK]), âm thanh tự sinh YouTube,
        và loại bỏ các câu kêu gọi Like / Share / Comment / Subscribe rác nếu bị lọt vào phụ đề:
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
        # Loại bỏ câu kêu gọi Like / Share / Comment / Subscribe công nghiệp (EN / PT-BR / VI) mà không làm mất dấu chấm câu trước đó
        t = re.sub(
            r'(?:^|(?<=[.!?])\s+)'
            r'(?:don\'?t\s+forget\s+to\s+|please\s+|make\s+sure\s+to\s+|remember\s+to\s+|hit\s+|smash\s+(?:that\s+)?|leave\s+a\s+|drop\s+a\s+'
            r'|não\s+(?:se\s+)?esqueça\s+de\s+|deixe\s+(?:o\s+|seu\s+)?|deixa\s+o\s+'
            r'|(?:đừng\s+quên|nhớ|hãy)\s+(?:(?:bấm|nhấn|ấn|để\s+lại)\s+)?|(?:bấm|nhấn|ấn)\s+)?'
            r'(?:like,?\s*share(?:,?\s*(?:and|&))?\s*(?:comment|subscribe)?|like,?\s*comment(?:,?\s*(?:and|&))?\s*subscribe'
            r'|like\s+(?:and|&)\s+(?:subscribe|comment)|subscribe\s+(?:and\s+hit|for\s+more|to\s+(?:our|the|this)\s+channel)'
            r'|(?:curta|curtir),?\s*(?:compartilhe|compartilhar|comente|comentar)(?:,?\s*e\s+(?:se\s+)?inscrev\w+)?'
            r'|(?:curta|curtir|like)\s+e\s+(?:se\s+)?inscrev\w+|inscreva-se\s+no\s+canal'
            r'|(?:like|thích)\s*(?:,\s*|\s+và\s+|\s+)(?:share|chia\s+sẻ|comment|bình\s+luận|subscribe|đăng\s+ký)'
            r'|đăng\s+ký\s+kênh)[^.!?]*[.!?]?',
            ' ',
            t,
            flags=re.IGNORECASE,
        )
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
    def _choose_balanced_line_split(cls, words: List[str], max_single_line_chars: int = 22) -> int:
        """
        Chỉ ngắt thành 2 dòng khi cụm từ dài hơn ngưỡng 1 dòng (22 ký tự cho Shorts, 42 cho Long-form).
        Các cụm ngắn 2-3 từ ('WATCH GIANT JAWS', 'IN SECONDS.', '1,820 PSI BITE FORCE') giữ gọn trên 1 dòng ngang,
        không ép chồng 2 tầng làm che chủ thể hoặc tách rời giới từ ('DEEP \\N IN').
        """
        n = len(words)
        full_text = " ".join(words)
        if n <= 1 or len(full_text) <= max_single_line_chars:
            return -1

        best_idx = -1
        best_cost = 10**9
        for idx in range(n - 1):
            next_w = words[idx + 1] if idx + 1 < n else ""
            proclitic_penalty = 500 if cls._is_dangling_proclitic(words[idx], next_w) else 0
            line1_len = len(" ".join(words[: idx + 1]))
            line2_len = len(" ".join(words[idx + 1 :]))
            cost = abs(line1_len - line2_len) + max(line1_len, line2_len) * 0.25 + proclitic_penalty
            if cost < best_cost:
                best_cost = cost
                best_idx = idx
        return best_idx

    @classmethod
    def _select_single_accent_word_idx(
        cls,
        words: List[str],
        scene_highlights: Optional[set] = None,
    ) -> Tuple[int, str]:
        """
        Chọn TỐI ĐA 1 từ trọng tâm thực sự (con số định lượng hoặc từ khóa mạnh, KHÔNG BAO GIỜ chọn giới từ/mạo từ)
        trên mỗi thẻ phụ đề để nhấn màu gọn gàng, sang trọng — chấm dứt tình trạng 4 màu loè loẹt trên cùng 1 câu.
        Trả về (word_index, accent_kind) với accent_kind thuộc {'danger', 'gold', 'none'}.
        """
        custom_set = scene_highlights or set()
        best_idx = -1
        best_priority = 0
        best_kind = "none"

        for idx, word in enumerate(words):
            clean_word = re.sub(r"[^\w\d]", "", word.upper())
            if clean_word in cls._PROCLITIC_ATTACH_NEXT_WORDS:
                continue
            if len(clean_word) < 2 and not re.search(r"\d", clean_word):
                continue

            if re.search(r"\d", word):
                prio, kind = 4, "gold"
            elif clean_word in DANGER_POWER_WORDS:
                prio, kind = 3, "danger"
            elif clean_word in custom_set or clean_word in PAYOFF_POWER_WORDS:
                prio, kind = 2, "gold"
            else:
                continue

            if prio > best_priority:
                best_priority = prio
                best_idx = idx
                best_kind = kind

        return best_idx, best_kind

    @classmethod
    def _build_karaoke_text(
        cls,
        words: List[str],
        word_durations_cs: List[int],
        default_highlight: str = "&H00FFFFFF",
        power_color: str = "&H000000FF",
        scene_highlights: Optional[set] = None,
        is_long_form: bool = False,
        use_smooth_wipe: bool = False,
    ) -> str:
        """
        Tạo phụ đề sạch, hiện đại, dễ đọc chuẩn Documentary / Viral Shorts:
        - Mặc định dùng `\\k` (nhảy sáng nguyên vẹn cả từ theo đúng nhịp đọc) thay vì `\\kf` quét nửa chữ
          (ngăn hoàn toàn lỗi 1 từ bị chia nửa xanh nửa trắng kiểu 'MANDÍBU' + 'LAS').
        - Chữ nền Trắng tinh khiết (#FFFFFF), tối đa 1 từ khóa/con số mỗi thẻ được nhấn Vàng Gold (#FFE500)
          hoặc Đỏ Crimson (#FF2222) với độ phóng nhẹ 105% tinh tế, thẳng hàng.
        - Ưu tiên 1 dòng ngang gọn gàng ở vùng Safe Zone dưới; chỉ ngắt 2 dòng cân đối khi cụm dài > 22 ký tự.
        """
        norm_base = f"{ASS_WHITE}&"
        norm_gold = f"{ASS_YELLOW}&"
        norm_crimson = f"{ASS_CRIMSON}&"
        k_tag = "kf" if use_smooth_wipe else "k"

        accent_idx, accent_kind = cls._select_single_accent_word_idx(words, scene_highlights=scene_highlights)
        max_line_chars = 42 if is_long_form else 22
        split_after_idx = cls._choose_balanced_line_split(words, max_single_line_chars=max_line_chars)

        parts = []
        for i, (word, dur_cs) in enumerate(zip(words, word_durations_cs)):
            safe_cs = max(int(dur_cs), 3)
            if i == accent_idx and accent_kind != "none":
                chosen_color = norm_crimson if accent_kind == "danger" else norm_gold
                token_str = (
                    f"{{\\{k_tag}{safe_cs}\\1c{chosen_color}\\fscx105\\fscy105}}{word}"
                    f"{{\\1c{norm_base}\\fscx100\\fscy100}}"
                )
            else:
                token_str = f"{{\\{k_tag}{safe_cs}\\1c{norm_base}}}{word}"
            parts.append(token_str)

        if 0 <= split_after_idx < len(parts) - 1:
            line1 = " ".join(parts[: split_after_idx + 1])
            line2 = " ".join(parts[split_after_idx + 1 :])
            return f"{line1}\\N{line2}"
        return " ".join(parts)

    @classmethod
    def format_auto_thumb_banner_text(cls, raw_title: Optional[str]) -> Optional[str]:
        """
        Chuyển tiêu đề hoặc hook_title thành dòng chữ Two-Tone (Trắng + Vàng Gold #FFE500)
        ngắn gọn, sắc nét để hiển thị trên nền video đang chuyển động tại mốc 50% & 75%
        (và 25% cho video dài), giúp YouTube Auto-Thumbnail chụp trúng khung hình có tiêu đề
        nổi bật khi kênh chưa xác thực SĐT (không bao giờ chèn ở 0.0s đầu).
        """
        if not raw_title:
            return None
        clean = re.sub(r"#\S+", "", str(raw_title))
        clean = cls._strip_ass_unsupported_emojis(clean)
        clean = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", clean)
        clean = re.sub(r"\s+", " ", clean).strip(" :-–—|!?.").upper()
        if not clean:
            return None

        if ":" in clean:
            left_p, right_p = [p.strip() for p in clean.split(":", 1)]
            w_left = left_p.split()[:5]
            w_right = right_p.split()[:5]
            if w_left and w_right:
                return f"{{\\1c{ASS_WHITE}&}}{' '.join(w_left)}\\N{{\\1c{ASS_YELLOW}&}}{' '.join(w_right)}!"

        words = clean.split()
        if len(words) <= 4:
            if len(words) >= 2:
                return f"{{\\1c{ASS_WHITE}&}}{' '.join(words[:-1])} {{\\1c{ASS_YELLOW}&}}{words[-1]}!"
            return f"{{\\1c{ASS_YELLOW}&}}{clean}!"

        words = words[:8]
        mid = max(2, len(words) // 2)
        line1 = " ".join(words[:mid])
        line2 = " ".join(words[mid:])
        return f"{{\\1c{ASS_WHITE}&}}{line1}\\N{{\\1c{ASS_YELLOW}&}}{line2}"

    @classmethod
    def build_ass_content(
        cls,
        scenes: List[SceneContract],
        fontname: str = "Montserrat ExtraBold",
        fontsize: int = 56,
        margin_v: int = 420,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        niche_id: Optional[str] = None,
        credit_text: Optional[str] = None,
        show_cta_badge: bool = False,
        hook_title: Optional[str] = None,
        is_long_form: bool = False,
    ) -> str:
        """
        Tạo nội dung file .ass chuẩn ở vùng Safe Zone tối ưu:
        - Shorts 9:16 (1080x1920): Font 56px, MarginV=420px (nằm gọn dưới chủ thể trung tâm và phía trên vùng tiêu đề/tên kênh của YouTube Shorts), MarginL/R=115px.
        - Long-form 16:9 (1920x1080): Font 46px, MarginV=68px, MarginL/R=140px.
        - Đã xóa hoàn toàn dòng chữ banner kêu gọi Like / Share / Comment / Subscribe gây rối mắt trên video.
        """
        eff_unspoken = primary_color or ASS_SOFT_WHITE
        eff_spoken = secondary_color or ASS_WHITE
        eff_outline = outline_color or ASS_BLACK
        eff_font = fontname
        eff_size = 46 if is_long_form else (fontsize if 48 <= int(fontsize or 56) <= 64 else 56)
        eff_margin = 68 if is_long_form else (margin_v if 360 <= int(margin_v or 420) <= 500 else 420)

        if niche_id:
            try:
                from app.services.niche_manager import get_niche, get_niche_subtitle_style
                niche_prof = get_niche(niche_id)
                niche_sub = get_niche_subtitle_style(niche_prof)
                eff_outline = outline_color or niche_sub.get("outline_ass", ASS_BLACK)
                if not fontname or fontname in ("Arial", "Montserrat ExtraBold"):
                    eff_font = niche_sub.get("fontname", "Montserrat ExtraBold")
                if not is_long_form:
                    niche_fs = int(niche_sub.get("fontsize", 56) or 56)
                    if fontsize in (40, 42, 44, 56, 68, 78):
                        eff_size = niche_fs if 48 <= niche_fs <= 64 else 56
                    niche_mv = int(niche_sub.get("margin_v", 420) or 420)
                    if margin_v in (210, 420, 580, 600, 720):
                        eff_margin = niche_mv if 360 <= niche_mv <= 500 else 420
            except Exception:
                pass

        eff_font = cls._resolve_effective_font(eff_font)

        header = ASS_HEADER_TEMPLATE.format(
            fontname=eff_font,
            fontsize=eff_size,
            primary=eff_spoken,       # PrimaryColour = chữ trắng tinh khiết (#FFFFFF) khi đọc tới
            secondary=eff_unspoken,   # SecondaryColour = chữ trắng dịu (#E4E4E4) trước khi đọc tới
            outline=eff_outline,
            back=ASS_SHADOW,
            margin_v=eff_margin,
        )
        if is_long_form:
            header = header.replace("PlayResX: 1080\nPlayResY: 1920", "PlayResX: 1920\nPlayResY: 1080")
            header = header.replace(",2,115,115,", ",2,140,140,")

        dialogue_lines = []
        current_offset_ms = 0
        max_w_lim = 7 if is_long_form else 4
        max_c_lim = 42 if is_long_form else 22

        for scene in scenes:
            scene_duration_ms = scene.audio_duration_ms or int(scene.estimated_seconds * 1000)
            if scene_duration_ms <= 0:
                scene_duration_ms = 3000

            if getattr(scene, "start_offset_ms", None) is not None:
                current_offset_ms = scene.start_offset_ms

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
                cleaned_wt_items: List[Dict[str, Any]] = []
                for w_info in word_timings:
                    raw_w = str(w_info.get("word", "")).strip()
                    raw_w = re.sub(
                        r'\[(?:LOOP|HOOK|ACT\s*\d*|SCENE\s*\d*|PAUSE|BEAT|SILENCE|WHISPER)[^\]]*\]',
                        '',
                        raw_w,
                        flags=re.IGNORECASE,
                    ).strip()
                    raw_w = cls._strip_ass_unsupported_emojis(raw_w)
                    w_text = raw_w.upper()
                    if not w_text:
                        continue
                    cleaned_wt_items.append({**w_info, "word": w_text})

                chunks_data = cls._chunk_word_items(
                    cleaned_wt_items,
                    max_words=max_w_lim,
                    max_chars=max_c_lim,
                )

                for chunk_idx, c_words in enumerate(chunks_data):
                    chunk_start_ms = current_offset_ms + c_words[0]["start_ms"]
                    chunk_end_ms = current_offset_ms + c_words[-1]["end_ms"]

                    vis_chars = sum(len(re.sub(r"\s+", "", w["word"])) for w in c_words)
                    min_req_ms = max(420, min(920, int(vis_chars * 1000.0 / 22.0)))
                    if chunk_end_ms - chunk_start_ms < min_req_ms:
                        chunk_end_ms = chunk_start_ms + min_req_ms

                    if chunk_idx < len(chunks_data) - 1:
                        next_start_ms = current_offset_ms + chunks_data[chunk_idx + 1][0]["start_ms"]
                        if next_start_ms > chunk_end_ms and (next_start_ms - chunk_end_ms) < 220:
                            chunk_end_ms = next_start_ms
                        elif chunk_end_ms > next_start_ms and next_start_ms > chunk_start_ms + 160:
                            chunk_end_ms = next_start_ms

                    words = [w["word"] for w in c_words]
                    word_durations_cs = []
                    for idx_w, w in enumerate(c_words):
                        dur_cs = max(int(w.get("duration_ms", 200) / 10), 4)
                        if idx_w < len(c_words) - 1:
                            gap = c_words[idx_w + 1]["start_ms"] - w["end_ms"]
                            if gap > 0:
                                dur_cs += int(gap / 10)
                        word_durations_cs.append(dur_cs)

                    karaoke_text = cls._build_karaoke_text(
                        words,
                        word_durations_cs,
                        default_highlight=eff_spoken,
                        scene_highlights=scene_hl_set,
                        is_long_form=is_long_form,
                    )

                    start_str = cls._ms_to_ass_time(chunk_start_ms)
                    end_str = cls._ms_to_ass_time(chunk_end_ms)

                    dialogue_lines.append(
                        f"Dialogue: 0,{start_str},{end_str},Karaoke,,0,0,0,,{karaoke_text}"
                    )
            else:
                # ── CHẾ ĐỘ 2: FALLBACK PHÂN BỔ KÝ TỰ (Khi không có word_timings) ──
                chunks = cls.split_text_to_chunks(
                    scene.narration,
                    max_words=max_w_lim,
                    max_chars=max_c_lim,
                )
                if not chunks:
                    if getattr(scene, "start_offset_ms", None) is not None:
                        current_offset_ms = scene.start_offset_ms + scene_duration_ms
                    else:
                        current_offset_ms += scene_duration_ms
                    continue

                chunks = [cls._strip_ass_unsupported_emojis(c).upper() for c in chunks]
                chunks = [c for c in chunks if c]
                if not chunks:
                    current_offset_ms += scene_duration_ms
                    continue

                total_chars = sum(len(c) for c in chunks)
                chunk_durations_ms = []
                for c in chunks:
                    ratio = len(c) / total_chars if total_chars > 0 else 1.0 / len(chunks)
                    dur = int(scene_duration_ms * ratio)
                    chunk_durations_ms.append(max(dur, 500))

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
                        dur_cs = max(int(chunk_dur_ms * ratio / 10), 4)
                        word_durations_cs.append(dur_cs)

                    total_word_cs = sum(word_durations_cs)
                    chunk_cs = chunk_dur_ms // 10
                    if word_durations_cs:
                        word_durations_cs[-1] += (chunk_cs - total_word_cs)
                        word_durations_cs[-1] = max(word_durations_cs[-1], 4)

                    karaoke_text = cls._build_karaoke_text(
                        words,
                        word_durations_cs,
                        default_highlight=eff_spoken,
                        scene_highlights=scene_hl_set,
                        is_long_form=is_long_form,
                    )

                    start_str = cls._ms_to_ass_time(chunk_start_ms)
                    end_str = cls._ms_to_ass_time(chunk_end_ms)

                    dialogue_lines.append(
                        f"Dialogue: 0,{start_str},{end_str},Karaoke,,0,0,0,,{karaoke_text}"
                    )

                    chunk_start_ms = chunk_end_ms

            if getattr(scene, "start_offset_ms", None) is not None:
                current_offset_ms = scene.start_offset_ms + scene_duration_ms
            else:
                current_offset_ms += scene_duration_ms

        # ── Auto-Thumbnail Moving Title Banner tại 50% & 75% (gần cuối video) + 25% (Long-Form) ──
        # Tuyệt đối KHÔNG chèn ở 0.0s đầu để bảo vệ 100% Retention. Hiển thị trên nền video đang chạy
        # để khi kênh chưa xác thực SĐT, máy chủ YouTube tự chụp ảnh ở 50% / 75% sẽ bắt trúng tiêu đề nổi.
        if hook_title and current_offset_ms >= 15000:
            auto_banner_txt = cls.format_auto_thumb_banner_text(hook_title)
            if auto_banner_txt:
                pct_targets = (0.25, 0.50, 0.75) if (is_long_form or current_offset_ms >= 120000) else (0.50, 0.75)
                half_win_ms = 3200 if is_long_form else 2200
                for pct in pct_targets:
                    center_ms = int(current_offset_ms * pct)
                    b_st_ms = max(5500, center_ms - half_win_ms)
                    b_en_ms = min(current_offset_ms - 500, center_ms + half_win_ms)
                    if b_en_ms > b_st_ms + 800:
                        dialogue_lines.append(
                            f"Dialogue: 3,{cls._ms_to_ass_time(b_st_ms)},{cls._ms_to_ass_time(b_en_ms)},"
                            f"AutoThumbBanner,,0,0,0,,{{\\fad(150,150)}}{auto_banner_txt}"
                        )

        # ── Watermark / Credit tác giả gọn nhẹ trên đỉnh video (nếu có yêu cầu ghi nguồn) ──
        if credit_text and credit_text.strip():
            credit_clean = cls._strip_ass_unsupported_emojis(credit_text.strip().replace("\n", " "))
            if credit_clean:
                credit_end_str = cls._ms_to_ass_time(current_offset_ms if current_offset_ms > 0 else 60000)
                dialogue_lines.insert(
                    0,
                    f"Dialogue: 2,0:00:00.00,{credit_end_str},WatermarkStyle,,0,0,0,,{{\\fade(300,300)}}{credit_clean}",
                )

        return header + "\n".join(dialogue_lines) + "\n"

    @classmethod
    def save_ass_file(
        cls,
        scenes: List[SceneContract],
        output_path: str,
        fontname: str = "Montserrat ExtraBold",
        fontsize: int = 56,
        margin_v: int = 420,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        niche_id: Optional[str] = None,
        credit_text: Optional[str] = None,
        show_cta_badge: bool = False,
        hook_title: Optional[str] = None,
        is_long_form: bool = False,
    ) -> str:
        """Tạo và lưu file .ass ra ổ đĩa ở vùng Safe Zone chuẩn, không chèn dòng chữ kêu gọi Like/Share/Comment."""
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
            show_cta_badge=False,
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
        fontsize: int = 56,
        margin_v: int = 420,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        niche_id: Optional[str] = None,
        credit_text: Optional[str] = None,
        show_cta_badge: bool = False,
    ) -> str:
        """
        Tạo nội dung file .ass từ mốc thời gian thực của giọng đọc (ASR/Whisper/Transcript)
        ở vùng Safe Zone tối ưu (MarginV=420), không chèn banner kêu gọi Like/Share/Comment.
        """
        eff_unspoken = primary_color or ASS_SOFT_WHITE
        eff_spoken = secondary_color or ASS_WHITE
        eff_outline = outline_color or ASS_BLACK
        eff_font = fontname
        eff_size = fontsize if 48 <= int(fontsize or 56) <= 64 else 56
        eff_margin = margin_v if 360 <= int(margin_v or 420) <= 500 else 420

        if niche_id:
            try:
                from app.services.niche_manager import get_niche, get_niche_subtitle_style
                niche_prof = get_niche(niche_id)
                niche_sub = get_niche_subtitle_style(niche_prof)
                eff_outline = outline_color or niche_sub.get("outline_ass", ASS_BLACK)
                if not fontname or fontname in ("Arial", "Montserrat ExtraBold"):
                    eff_font = niche_sub.get("fontname", "Montserrat ExtraBold")
                niche_fs = int(niche_sub.get("fontsize", 56) or 56)
                if fontsize in (40, 42, 44, 56, 68, 78):
                    eff_size = niche_fs if 48 <= niche_fs <= 64 else 56
                niche_mv = int(niche_sub.get("margin_v", 420) or 420)
                if margin_v in (210, 420, 580, 600, 720):
                    eff_margin = niche_mv if 360 <= niche_mv <= 500 else 420
            except Exception:
                pass

        eff_font = cls._resolve_effective_font(eff_font)

        header = ASS_HEADER_TEMPLATE.format(
            fontname=eff_font,
            fontsize=eff_size,
            primary=eff_spoken,
            secondary=eff_unspoken,
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

            rel_start_sec = max(0.0, raw_start - clip_start_offset)
            seg_start_ms = int(rel_start_sec * 1000)
            seg_dur_ms = max(int(raw_dur * 1000), 400)
            seg_end_ms = seg_start_ms + seg_dur_ms
            max_seen_end_ms = max(max_seen_end_ms, seg_end_ms)

            chunks = cls.split_text_to_chunks(clean_text, max_words=4, max_chars=22)
            if not chunks:
                continue

            chunks = [cls._strip_ass_unsupported_emojis(c).upper() for c in chunks]
            chunks = [c for c in chunks if c]
            if not chunks:
                continue

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

        # ── Watermark Credit ở đỉnh video (Layer 2, nếu có yêu cầu ghi nguồn) ──
        if credit_text and credit_text.strip():
            clean_credit = cls._strip_ass_unsupported_emojis(credit_text.strip().replace("\n", " "))
            if clean_credit:
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
        fontsize: int = 56,
        margin_v: int = 420,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        niche_id: Optional[str] = None,
        credit_text: Optional[str] = None,
        show_cta_badge: bool = False,
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
            show_cta_badge=False,
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
        fontsize: int = 56,
        margin_v: int = 420,
        primary_color: Optional[str] = None,
        secondary_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        niche_id: Optional[str] = None,
        credit_text: Optional[str] = None,
        is_english: bool = True,
        is_voiceover: bool = False,
    ) -> str:
        """
        Xây dựng phụ đề chuyên biệt cho kịch bản Nấu Lại Đa Phân Đoạn (Multi-Clip Mashup) ở vùng Safe Zone (MarginV=420).
        """
        eff_unspoken = primary_color or ASS_SOFT_WHITE
        eff_spoken = secondary_color or ASS_WHITE
        eff_outline = outline_color or ASS_BLACK
        eff_back = ASS_SHADOW
        eff_font = fontname or "Montserrat ExtraBold"
        eff_size = fontsize if (fontsize and 48 <= fontsize <= 64) else 56
        eff_margin = margin_v if (margin_v and 360 <= margin_v <= 500) else 420

        if niche_id:
            try:
                from app.services.niche_manager import get_niche, get_niche_subtitle_style
                niche_prof = get_niche(niche_id)
                niche_sub = get_niche_subtitle_style(niche_prof)
                eff_outline = outline_color or niche_sub.get("outline_ass", ASS_BLACK)
                eff_font = fontname or niche_sub.get("fontname", "Montserrat ExtraBold")
            except Exception:
                pass

        eff_font = cls._resolve_effective_font(eff_font)

        header = ASS_HEADER_TEMPLATE.format(
            fontname=eff_font,
            fontsize=eff_size,
            primary=eff_spoken,
            secondary=eff_unspoken,
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

                    chunks = cls.split_text_to_chunks(seg_text, max_words=4, max_chars=22)
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
                        k_text = cls._build_karaoke_text(
                            words,
                            w_durs,
                            default_highlight=eff_spoken,
                            use_smooth_wipe=True,
                        )
                        dialogue_lines.append(
                            f"Dialogue: 0,{cls._ms_to_ass_time(c_st)},{cls._ms_to_ass_time(c_en)},Karaoke,,0,0,0,,{k_text}"
                        )
                        c_st = c_en
            else:
                raw_text = c.get("custom_voiceover_text") or c.get("transcript_snippet") or c.get("text") or c.get("summary") or ""
                clean_text = cls.sanitize_subtitle_text(raw_text)
                if not clean_text or len(clean_text.split()) < 2:
                    clean_text = default_narrs.get(role, f"WATCH THIS SCENE #{c_idx}!")

                chunks = cls.split_text_to_chunks(clean_text, max_words=4, max_chars=22)
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

                    k_text = cls._build_karaoke_text(
                        words,
                        w_durs,
                        default_highlight=eff_spoken,
                        use_smooth_wipe=True,
                    )
                    dialogue_lines.append(
                        f"Dialogue: 0,{cls._ms_to_ass_time(c_st)},{cls._ms_to_ass_time(c_en)},Karaoke,,0,0,0,,{k_text}"
                    )
                    c_st = c_en

            current_timeline_ms += clip_dur_ms

        # ── 3. WATERMARK CREDIT Ở ĐỈNH VIDEO ──
        if credit_text and credit_text.strip():
            clean_credit = cls._strip_ass_unsupported_emojis(credit_text.strip().replace("\n", " "))
            if clean_credit:
                credit_end_str = cls._ms_to_ass_time(current_timeline_ms)
                dialogue_lines.insert(0,
                    f"Dialogue: 2,0:00:00.00,{credit_end_str},WatermarkStyle,,0,0,0,,{{\\fade(300,300)}}{clean_credit}"
                )

        return header + "\n".join(dialogue_lines) + "\n"

    @classmethod
    def save_mashup_ass_file(
        cls,
        clips: List[Dict[str, Any]],
        output_path: str,
        fontname: str = "Montserrat ExtraBold",
        fontsize: int = 56,
        margin_v: int = 420,
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
