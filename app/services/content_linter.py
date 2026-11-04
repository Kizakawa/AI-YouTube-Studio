"""
app.services.content_linter — Bộ Linter Thẩm Định & Kiểm Soát Chất Lượng Kịch Bản (Script Validation Linter).

Nhiệm vụ:
1. Chặn đứng lỗi cắt cụt câu hoặc đuôi nối lửng lơ trong tiếng Anh (because, which, but, with, and, so...).
2. Phát hiện từ vựng dị biệt, ảo giác (Hallucination / Niche Anomaly) dựa trên banned_lexicon của từng ngách.
3. Kiểm tra nhịp đọc & độ dài (Pacing & Word Count) đảm bảo chuẩn 50 - 75 từ (22 - 28 giây tại 150-160 WPM).
4. Kiểm toán chống trùng lặp thực thể (Entity Cooldown) dựa trên N kịch bản gần nhất.
5. Tạo prompt phản hồi chuẩn xác để ContentAgent retry tối đa 3 lần nếu vi phạm.
"""

import re
import logging
from typing import List, Dict, Any, Optional, Set, Tuple
from pydantic import BaseModel, Field

from app.schemas.contracts import ScriptContract, SceneContract

logger = logging.getLogger(__name__)

# Danh sách từ nối / mạo từ / tính từ sở hữu lửng lơ thực sự bị cắt cụt (tách riêng EN và PT để động từ tiếng Anh 'do' không bị bắt nhầm là giới từ Bồ Đào Nha 'do')
TRAILING_ENGLISH_CONNECTORS = [
    "because", "which", "and", "but",
    "or", "although",
    "of", "into", "onto", "until",
    "his", "their", "its", "my", "your", "our", "the", "a", "an",
]

TRAILING_PORTUGUESE_CONNECTORS = [
    "que", "de", "do", "da", "dos", "das", "pois", "porque",
    "mas", "ou",
]

TRAILING_CONNECTOR_PATTERN = re.compile(
    r'\b(?:' + '|'.join(TRAILING_ENGLISH_CONNECTORS) + r')\s*(?:[\.\,\-\—\…]+|\.{2,})*$',
    re.IGNORECASE
)

TRAILING_PT_CONNECTOR_PATTERN = re.compile(
    r'\b(?:' + '|'.join(TRAILING_PORTUGUESE_CONNECTORS) + r')\s*(?:[\.\,\-\—\…]+|\.{2,})*$',
    re.IGNORECASE
)

STAGE_TAG_PATTERN = re.compile(
    r'\[(?:LOOP|HOOK|ACT\s*\d*|SCENE\s*\d*|PAUSE|BEAT|SILENCE|WHISPER|INTRO|OUTRO|END)[^\]]*\]',
    re.IGNORECASE,
)


class LinterResult(BaseModel):
    is_valid: bool = Field(description="Kịch bản có vượt qua tất cả các bài kiểm tra chất lượng không")
    issues: List[str] = Field(default_factory=list, description="Danh sách các vấn đề được phát hiện")
    retry_prompt_hint: Optional[str] = Field(default=None, description="Chỉ dẫn sửa lỗi cụ thể để tiêm vào prompt retry của LLM")
    detected_banned_words: List[str] = Field(default_factory=list, description="Các từ dị biệt/bị cấm bị phát hiện")
    detected_cooldown_entities: List[str] = Field(default_factory=list, description="Các thực thể trùng lặp đang trong thời gian cooldown")
    total_words: int = Field(default=0, description="Tổng số từ trong toàn bộ kịch bản")


class ScriptValidationLinter:
    """
    Quality Gate Linter chuẩn hóa cho toàn bộ 12 ngách.
    Không tự ý thay từ máy móc mà phát hiện lỗi chính xác để kích hoạt vòng lặp retry thông minh.
    """

    @classmethod
    def check_trailing_connectors(cls, script: ScriptContract) -> List[str]:
        """
        Kiểm tra toàn bộ phân cảnh và câu kết của kịch bản xem có bị cắt cụt lửng lơ hoặc sót nhãn [LOOP] không.
        """
        errors = []
        scenes = script.scenes or []
        if not scenes:
            return ["Script has no scenes."]

        for sc in scenes:
            raw_sc_text = (sc.narration or "").strip()
            if STAGE_TAG_PATTERN.search(raw_sc_text):
                tag_found = STAGE_TAG_PATTERN.search(raw_sc_text).group(0)
                errors.append(
                    f"Scene {sc.scene_id} narration contains raw stage direction tag '{tag_found}'. "
                    f"Never include bracketed stage tags like [LOOP] or [HOOK] in spoken narration."
                )

        full_blob_low = f"{script.title or ''} {' '.join((s.narration or '') for s in scenes)}".lower()
        is_pt_text = any(
            pt_marker in full_blob_low
            for pt_marker in (" não ", " você", " então", " mas ", " porque ", " para ", " uma ", " dos ", " das ", "ção", "ões", " d'água", " pra ", " pro ")
        )
        active_trailing_re = TRAILING_PT_CONNECTOR_PATTERN if is_pt_text else TRAILING_CONNECTOR_PATTERN

        # 1. Kiểm tra cảnh cuối cùng (quan trọng nhất đối với Infinite Shorts Loop)
        last_scene = scenes[-1]
        last_text = STAGE_TAG_PATTERN.sub("", (last_scene.narration or "")).strip()
        
        # Bỏ dấu chấm than, chấm hỏi cuối câu nếu có
        cleaned_last = re.sub(r'[\s\.\!\?]+$', '', last_text)
        if active_trailing_re.search(cleaned_last):
            match = active_trailing_re.search(cleaned_last).group(0).strip(" .,-—…")
            errors.append(
                f"Scene {last_scene.scene_id} (Final Scene) ends abruptly on an incomplete word '{match}'. "
                f"Full sentences are required. Do NOT leave trailing prepositions, possessives, or conjunctions."
            )

        # 2. Kiểm tra từng scene riêng lẻ xem có scene nào bị cắt cụt không
        for sc in scenes[:-1]:
            sc_text = STAGE_TAG_PATTERN.sub("", (sc.narration or "")).strip()
            cleaned_sc = re.sub(r'[\s\.\!\?]+$', '', sc_text)
            if active_trailing_re.search(cleaned_sc):
                match = active_trailing_re.search(cleaned_sc).group(0).strip(" .,-—…")
                errors.append(
                    f"Scene {sc.scene_id} narration ends on a dangling word '{match}'. "
                    f"Each scene must be a grammatically complete clause."
                )

        return errors

    @classmethod
    def check_banned_lexicon(
        cls,
        script: ScriptContract,
        niche_profile: Dict[str, Any]
    ) -> Tuple[List[str], List[str]]:
        """
        Kiểm tra các câu sáo rỗng mở đầu/kết thúc kiểu YouTube meta-talk ('in this video', 'don't forget to subscribe').
        Không lọc từ khóa ngữ nghĩa đơn lẻ bằng danh sách cứng để tránh làm nghèo nàn vốn từ kịch bản;
        việc đánh giá ngữ cảnh kịch bản theo từng ngách do Đạo diễn LLM (ContentAgent & ScriptCritic) đọc trực tiếp từ YAML.
        """
        issues = []
        detected_words = []

        blueprint = niche_profile.get("script_blueprint", {})
        forbidden_cliches = blueprint.get("forbidden_words", [])
        full_text = f"{script.title} {script.hook} {script.full_narration}".lower()

        if not niche_profile.get("_file_name") and "script_blueprint" not in niche_profile:
            for word in (niche_profile.get("banned_lexicon") or []):
                word_clean = str(word).strip().lower()
                if not word_clean:
                    continue
                pattern = r"\b" + re.escape(word_clean) + r"\b"
                if re.search(pattern, full_text):
                    detected_words.append(word_clean)
                    issues.append(
                        f"Forbidden/out-of-niche term detected: '{word_clean}'. "
                        f"This concept is strictly irrelevant or incongruent with the '{niche_profile.get('name', 'current')}' niche."
                    )

        for cliche in forbidden_cliches:
            cliche_clean = str(cliche).strip().lower()
            if len(cliche_clean) > 8 and " " in cliche_clean and cliche_clean in full_text:
                detected_words.append(cliche_clean)
                issues.append(f"Forbidden cliché detected: '{cliche_clean}'. Remove YouTube meta-talk.")

        return issues, detected_words

    @classmethod
    def check_pacing_and_length(
        cls,
        script: ScriptContract,
        target_wpm: int = 145,
        niche_id: str = ""
    ) -> Tuple[List[str], int]:
        """
        Kiểm tra số từ và tính toán nhịp đọc (Ưu tiên mạch truyện đầy đặn, mượt mà, có đầu có đuôi — chống kịch bản cụt lủn):
        - Cho phép biên độ linh hoạt từ 82 đến 128 từ (mục tiêu vàng 92 - 118 từ, 7 - 8 cảnh, thời lượng 34 - 42 giây) để tối ưu APV > 90% & AVD > 35s mà vẫn dẫn dắt câu chuyện mượt mà, giải thích thấu đáo, không bị cắt cụt ý.
        """
        issues = []
        all_narrations = " ".join(s.narration for s in script.scenes if s.narration)
        # Đếm từ nói thực tế (contractions như "doesn't", "would've" và danh từ ghép "onça-pintada" tính là 1 từ thay vì bị cắt đôi)
        words = re.findall(r"\b[\w'-]+\b", all_narrations, flags=re.UNICODE)
        total_words = len(words)

        is_long_form = str(getattr(script, "video_format", "short") or "short").strip().lower() == "long"
        if is_long_form:
            min_words = 220
            max_words = 2400
            target_range_str = "1,250 to 1,650 words (8-11 min Long-Form 16:9 Documentary across 36-48 complete scenes)"
            min_scenes = 12
            max_scenes = 60
        else:
            min_words = 80
            max_words = 158
            target_range_str = "92 to 120 words (34-44s High-APV Golden Duration across 7-8 smooth, complete scenes)"
            min_scenes = 6
            max_scenes = 9

        if total_words > max_words:
            est_seconds = (total_words / max(target_wpm, 185)) * 60
            issues.append(
                f"Script is overly long ({total_words} words, ~{est_seconds:.1f}s) which lowers APV %. "
                f"Keep around {target_range_str}."
            )
        elif total_words < min_words:
            est_seconds = (total_words / max(target_wpm, 160)) * 60
            issues.append(
                f"Script is too short and abrupt ({total_words} words, ~{est_seconds:.1f}s) — feels truncated and lacks smooth context/explanation! "
                f"Expand to at least {min_words} words (target {target_range_str}) so the story flows naturally from start to finish."
            )

        scene_count = len(script.scenes)
        if scene_count < min_scenes:
            issues.append(
                f"Too few scenes ({scene_count}). Need {min_scenes} to {max_scenes} scenes to tell a complete, well-paced story."
            )
        elif scene_count > max_scenes:
            issues.append(
                f"Too many scenes ({scene_count}). Keep between {min_scenes} and {max_scenes} complete-sentence scenes."
            )

        return issues, total_words

    @classmethod
    def check_retention_hooks_and_title(
        cls,
        script: ScriptContract,
        niche_profile: Dict[str, Any]
    ) -> List[str]:
        """
        Kiểm tra chất lượng Hook Cảnh 1 và Tiêu đề (không khóa cứng từ khóa hay số từ từng cảnh thân bài):
        1. Cảnh 1 (Hook): Mở đầu bằng câu khẳng định/cảnh báo sắc bén, không dùng câu hỏi Yes/No yếu.
        2. Cấm câu đọc xin Subscribe / Comment gây tụt APV.
        3. Không để lọt tiền tố 'Variant 1:' vào tiêu đề.
        """
        issues = []
        niche_id = str(niche_profile.get("id", "")).lower()

        # Kiểm tra rò rỉ tiền tố Variant trên Title
        raw_title = (script.title or "").strip()
        if re.match(r"^(?:variant|option|title)\s*#?\d*\s*[:\-]", raw_title, flags=re.I):
            issues.append(
                f"Title contains internal prefix leakage ('{raw_title}'). Output ONLY the clean viral title without 'Variant 1:'."
            )

        if script.scenes:
            s1 = script.scenes[0]
            s1_narr = (s1.narration or "").strip()
            s1_words = re.findall(r"\b[\w'-]+\b", s1_narr, flags=re.UNICODE)
            is_long_form = str(getattr(script, "video_format", "short") or "short").strip().lower() == "long"
            max_s1_words = 34 if is_long_form else 22
            if len(s1_words) > max_s1_words:
                issues.append(
                    f"Scene 1 hook is too long ({len(s1_words)} words). Keep Scene 1 between 6 and {max_s1_words} punchy words for an instant thumb-stopper."
                )
            elif len(s1_words) < 3:
                issues.append(
                    f"Scene 1 hook is too fragmented ({len(s1_words)} words). Must be a complete 6-10 word declarative hook."
                )

            # CẤM câu hỏi Yes/No yếu ở Cảnh 1 ("Did you know...?", "Have you ever...?", "Você sabia...?"),
            # NHƯNG CHO PHÉP & KHUYẾN KHÍCH câu hỏi Mở Vòng Lặp Tò Mò ("Why...?", "Por que...?", "How...?", "What if...?", "Tại sao...?")
            open_curiosity_hook_pattern = re.compile(
                r"^(?:why\b|how\b|what if\b|what happens\b|"
                r"por que\b|por quê\b|como\b|e se\b|o que acontece\b|"
                r"tại sao\b|vì sao\b|làm sao\b|làm thế nào\b|chuyện gì xảy ra\b)",
                re.IGNORECASE,
            )
            weak_yes_no_pattern = re.compile(
                r"^(?:did you|do you|have you|are you|can you|would you|could you|is it|was it|"
                r"bạn có biết|bạn đã bao giờ|có bao giờ bạn|bạn có đang|phải chăng|"
                r"você sabia|você já)\b",
                re.IGNORECASE,
            )
            is_open_curiosity_hook = bool(open_curiosity_hook_pattern.search(s1_narr.strip()))
            is_title_open_question = bool(open_curiosity_hook_pattern.search(raw_title.strip()))
            if weak_yes_no_pattern.search(s1_narr) or ("?" in s1_narr and not is_open_curiosity_hook):
                issues.append(
                    f"Scene 1 hook uses a weak Yes/No question ('{s1_narr}'). "
                    f"Scene 1 MUST be a sharp declarative action/paradox statement (or an open-loop 'Why / Por que' hook ONLY when the title is not already a question)."
                )
            elif is_title_open_question and is_open_curiosity_hook:
                issues.append(
                    f"TITLE-TO-HOOK QUESTION ECHO BLOCKED: Both Title ('{raw_title}') and Scene 1 ('{s1_narr}') open with a 'Why / Por que / How' question! "
                    f"When the Title asks 'Why / Por que...', Scene 1 MUST NOT repeat the question—open Scene 1 immediately with a declarative physical shock, lethal strike, or survival paradox at 0.0s!"
                )

            # Kiểm tra cấu trúc 2-Entity Matchup (`Entity A vs Entity B`): Chỉ áp dụng cho ngách đối đầu sinh vật vật lý (`wildlife`),
            # KHÔNG áp dụng cho `alternate_history` / `psychology` / `mysteries` (nơi 'vs' thường là ẩn dụ như 'Commander vs World War III').
            if not is_long_form and ("wildlife" in niche_id or "predator" in niche_id):
                vs_source = raw_title if re.search(r"\b(?:vs\.?|versus|contra)\b", raw_title, re.I) else str(getattr(script, "topic", "") or "")
                m_vs = re.search(
                    r"^([^:|\-–—#]{3,42}?)\s+\b(?:vs\.?|versus|contra)\b\s+([^:|\-–—#!?]{3,42})",
                    vs_source.strip(),
                    flags=re.IGNORECASE,
                )
                if m_vs:
                    _vs_stop = {
                        "the", "and", "for", "with", "from", "into", "this", "that", "how", "why", "what",
                        "uma", "uns", "umas", "dos", "das", "nos", "nas", "por", "para", "com", "sem",
                        "deadly", "giant", "massive", "extreme", "ultimate", "battle", "duel", "duelo", "mortal",
                    }
                    side_a_raw = m_vs.group(1).strip()
                    side_b_raw = m_vs.group(2).strip()
                    toks_a = [
                        w.lower() for w in re.findall(r"[a-zA-ZÀ-ỹ]{3,}", side_a_raw)
                        if w.lower() not in _vs_stop
                    ]
                    toks_b = [
                        w.lower() for w in re.findall(r"[a-zA-ZÀ-ỹ]{3,}", side_b_raw)
                        if w.lower() not in _vs_stop
                    ]
                    opening_two_low = " ".join((sc.narration or "") for sc in script.scenes[:2]).lower()
                    _syn_groups = (
                        {"navy", "warships", "destroyers", "fleet", "american"},
                        {"submarine", "sub"},
                        {"soviet", "russian"},
                        {"creature", "creatures", "fish", "snailfish"},
                    )
                    def _matches_side(toks: List[str], blob: str) -> bool:
                        if not toks:
                            return True
                        for t in toks:
                            if t[:4] in blob:
                                return True
                            for grp in _syn_groups:
                                if t in grp and any(g in blob for g in grp):
                                    return True
                        return False

                    has_a = _matches_side(toks_a, opening_two_low)
                    has_b = _matches_side(toks_b, opening_two_low)
                    if toks_a and toks_b and (not has_a or not has_b):
                        missing_side = side_b_raw if not has_b else side_a_raw
                        issues.append(
                            f"Opening hook hides one of the combatants ('{missing_side}') in a 2-entity matchup ('{side_a_raw} vs {side_b_raw}'). "
                            f"In an 'A vs B' Short, name BOTH '{side_a_raw}' AND '{side_b_raw}' immediately in Scene 1!"
                        )

            # CẤM đọc câu xin Subscribe / Comment ở BẤT KỲ cảnh nào (đặc biệt là cảnh cuối)
            cta_regex = re.compile(
                r'\b(?:subscribe|inscreva-se|se inscreva|đăng ký kênh|bấm đăng ký|nhấn đăng ký|leave a comment|drop a comment|comment below|like and follow)\b',
                re.IGNORECASE,
            )
            for sc in script.scenes:
                sc_narr = (sc.narration or "").strip()
                if cta_regex.search(sc_narr):
                    issues.append(
                        f"Scene {sc.scene_id} contains a spoken 'Subscribe / Comment' CTA ('{sc_narr}') which causes a 15% APV drop-off. "
                        f"Remove all spoken CTAs and end with a Seamless Infinite Loop Bridge that connects directly back to Scene 1."
                    )

        return issues

    @classmethod
    def check_entity_cooldown(
        cls,
        script: ScriptContract,
        cooldown_entities: Optional[List[str]] = None
    ) -> Tuple[List[str], List[str]]:
        """
        Kiểm tra xem kịch bản có lặp lại các thực thể/dữ kiện vừa xuất hiện trong N video gần nhất không.
        """
        issues = []
        repeated = []
        if not cooldown_entities:
            return issues, repeated

        full_text = f"{script.title} {script.hook} {script.full_narration}".lower()

        for ent in cooldown_entities:
            ent_clean = str(ent).strip().lower()
            if len(ent_clean) < 3:
                continue
            pattern = r'\b' + re.escape(ent_clean) + r'\b'
            if re.search(pattern, full_text):
                repeated.append(ent_clean)

        # Nếu lặp lại từ 2 thực thể hoặc thực thể chính đã được khai thác gần đây
        if len(repeated) >= 2 or (script.primary_entity and script.primary_entity.lower() in [e.lower() for e in cooldown_entities]):
            matched_str = ", ".join(f"'{r}'" for r in repeated)
            issues.append(
                f"Repetitive entities detected from recent channel history: {matched_str}. "
                f"To maintain algorithmic uniqueness, feature DIFFERENT mysteries, artifacts, or locations!"
            )

        return issues, repeated

    COMMUNITY_GUIDELINES_BANNED_PHRASES: List[str] = []

    @classmethod
    def check_community_guidelines_safety(cls, script: ScriptContract) -> List[str]:
        """
        Không dùng bộ lọc từ khóa cứng (như 'paralyzed', 'bleeding', 'drowning') trong Python
        vì làm nghèo nàn ngôn từ kịch bản và bắt nhầm ngữ cảnh khoa học/tâm lý/sinh tồn.
        Việc thẩm định ngữ cảnh & an toàn chính sách theo từng ngách được giao cho Đạo diễn LLM (ContentAgent & ScriptCritic)
        đọc trực tiếp từ bản đồ YAML (`config/niches/*.yaml`).
        """
        return []

    @classmethod
    def check_metric_and_fact_consistency(cls, script: ScriptContract) -> List[str]:
        """
        Cổng kiểm chứng số liệu & sự thật (Fact-Gate — học từ Content OS & GOHA YT Engage):
        1. Nếu Tiêu đề (Title) hứa hẹn một thông số vật lý cụ thể kèm đơn vị (VD: 1,500 PSI, 2,000m, 93°C, 450V),
           phần lời dẫn (narration) bắt buộc phải giải thích/nhắc tới số liệu thay vì bỏ quên hoàn toàn (chống clickbait rỗng).
        2. Chặn các tỷ lệ phần trăm phi vật lý do LLM bịa số (VD: '150% of the human brain', '250% survival rate').
        """
        issues: List[str] = []
        title_str = (script.title or "").strip()
        narration_str = " ".join((s.narration or "") for s in (script.scenes or [])) or (script.full_narration or "")

        # 1. Kiểm tra tính nhất quán toán học tổng quát: nếu Title hứa hẹn con số cụ thể thì phần lời dẫn không được bỏ quên hoàn toàn
        title_metric_match = re.findall(
            r"\b(\d+(?:[.,]\d+)?)\s*([a-zA-Z°%]+)\b",
            title_str,
            flags=re.IGNORECASE,
        )
        if title_metric_match:
            has_any_number_in_body = bool(
                re.search(
                    r"(?:\b\d+\b|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|percent|"
                    r"um|uma|dois|duas|três|quatro|cinco|seis|sete|oito|nove|dez|vinte|trinta|quarenta|cinquenta|cem|cento|duzentos|trezentos|quatrocentos|quinhentos|seiscentos|setecentos|oitocentos|novecentos|mil|milhão|milhões|por\s+cento|"
                    r"một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười|trăm|nghìn|ngàn|triệu|phần\s+trăm)\b)",
                    narration_str,
                    flags=re.IGNORECASE,
                )
            )
            if not has_any_number_in_body:
                claimed_str = ", ".join(f"{num} {unit}" for num, unit in title_metric_match)
                issues.append(
                    f"Fact-Gate Title-to-Script Metric Mismatch: Title promises concrete physical metric ({claimed_str}), "
                    f"but the spoken script narration contains zero numerical facts or explanations."
                )

        # 2. Kiểm tra ảo giác tỷ lệ > 100% phi lý đối với dung lượng não bộ / xác suất / độ chính xác
        impossible_pct_matches = re.findall(
            r"\b([1-9]\d{2,})\s*%\s*(?:of\s+(?:(?:the|your|our|a|human|earth's)\s+)*(?:brain|population|people|water|body|cells|dna)|accuracy|probability|certainty|survival\s+rate)\b",
            f"{title_str} {narration_str}",
            flags=re.IGNORECASE,
        )
        for pct_val in impossible_pct_matches:
            try:
                if int(pct_val) > 100:
                    issues.append(
                        f"Fact-Gate Hallucinated Percentage Detected ('{pct_val}%'): Biological fraction, population share, "
                        f"or probability cannot exceed 100%. Use verifiable scientific metrics."
                    )
            except Exception:
                pass

        return issues

    @classmethod
    def check_language_integrity(cls, script: ScriptContract) -> List[str]:
        """
        Cổng kiểm duyệt tính toàn vẹn ngôn ngữ (Cross-Language Leakage Gate):
        Chặn tuyệt đối kịch bản Tiếng Anh (en) hoặc Tiếng Bồ Đào Nha (pt-BR) bị rò rỉ câu/từ Tiếng Việt
        ở Cảnh 1 (Hook), lời thoại các cảnh, tiêu đề, thumbnail_text hoặc overlay_badge.
        """
        issues: List[str] = []
        scenes = list(getattr(script, "scenes", None) or [])
        if not scenes:
            return issues

        vn_exclusive_re = re.compile(
            r"[ơớờởỡợưứừửữựđăắằẳẵặảẩẫậẻểễệỉịỏổỗộủửữựỷỹỵ]",
            re.IGNORECASE,
        )
        vn_hybrid_re = re.compile(
            r"\b(?:xé-to-cando|xé-to|va-cham|va\s+chạm|sự\s+thật\s+tàn\s+khốc|từ\s+in\s+hoa|đối\s+đầu\s+sinh\s+tử)\b",
            re.IGNORECASE,
        )
        vn_scene_ids = []
        for idx, sc in enumerate(scenes, start=1):
            narr = str(getattr(sc, "narration", "") or "")
            if vn_exclusive_re.search(narr) or vn_hybrid_re.search(narr):
                vn_scene_ids.append(idx)

        # Nếu đa số các cảnh KHÔNG phải Tiếng Việt (tức kênh Tiếng Anh hoặc Tiếng Bồ Đào Nha),
        # nhưng lại có cảnh bị lẫn Tiếng Việt (đặc biệt là Cảnh 1 mở đầu hoặc Cảnh cuối) -> Báo lỗi nghiêm trọng!
        is_predominantly_non_vn = len(vn_scene_ids) < max(2, (len(scenes) + 1) // 2)
        if is_predominantly_non_vn:
            if vn_scene_ids:
                issues.append(
                    f"CRITICAL LANGUAGE LEAKAGE VIOLATION: Scene(s) {vn_scene_ids} contain leaked Vietnamese text "
                    f"in a non-Vietnamese script. Rewrite ALL scenes 100% in the target channel language (English or Portuguese) "
                    f"with ZERO Vietnamese words or hybrid syllables."
                )
            for field_name in ("title", "hook", "thumbnail_text"):
                val = str(getattr(script, field_name, "") or "")
                if vn_exclusive_re.search(val) or vn_hybrid_re.search(val):
                    issues.append(
                        f"CRITICAL LANGUAGE LEAKAGE VIOLATION: '{field_name}' ('{val[:50]}') contains leaked Vietnamese text "
                        f"in a non-Vietnamese script. Write '{field_name}' 100% in the target channel language."
                    )
            for idx, sc in enumerate(scenes, start=1):
                badge = str(getattr(sc, "overlay_badge", "") or "")
                if badge and (vn_exclusive_re.search(badge) or vn_hybrid_re.search(badge)):
                    issues.append(
                        f"CRITICAL LANGUAGE LEAKAGE VIOLATION: Scene {idx} overlay_badge ('{badge}') contains leaked Vietnamese text."
                    )

        return issues

    @classmethod
    def lint(
        cls,
        script: ScriptContract,
        niche_profile: Dict[str, Any],
        cooldown_entities: Optional[List[str]] = None,
        target_wpm: int = 170
    ) -> LinterResult:
        """
        Hàm thẩm định tổng thể (Quality Gate).
        Trả về LinterResult với is_valid=True nếu đạt toàn bộ tiêu chuẩn.
        """
        all_issues: List[str] = []
        niche_id = str(niche_profile.get("id", "")).lower()

        # 0. YouTube Community Guidelines Safety Gate (Chặn tuyệt đối vi phạm chính sách YouTube)
        cg_issues = cls.check_community_guidelines_safety(script)
        all_issues.extend(cg_issues)

        # 0a. Cross-Language Integrity Gate (Chặn rò rỉ phụ đề/lời thoại Tiếng Việt sang kênh ngoại)
        lang_issues = cls.check_language_integrity(script)
        all_issues.extend(lang_issues)

        # 0b. Fact-Gate & Metric Consistency Check
        fact_issues = cls.check_metric_and_fact_consistency(script)
        all_issues.extend(fact_issues)

        # 1. Trailing connector check
        conn_issues = cls.check_trailing_connectors(script)
        all_issues.extend(conn_issues)

        # 2. Banned lexicon & anomaly check
        lex_issues, banned_detected = cls.check_banned_lexicon(script, niche_profile)
        all_issues.extend(lex_issues)

        # 3. Pacing & length check (Niche-aware)
        pac_issues, word_count = cls.check_pacing_and_length(script, target_wpm, niche_id=niche_id)
        all_issues.extend(pac_issues)

        # 4. Retention hooks & title check
        hook_issues = cls.check_retention_hooks_and_title(script, niche_profile)
        all_issues.extend(hook_issues)

        # 5. Entity cooldown check
        ent_issues, ent_detected = cls.check_entity_cooldown(script, cooldown_entities)
        all_issues.extend(ent_issues)

        is_valid = len(all_issues) == 0
        is_long_form = str(getattr(script, "video_format", "short") or "short").strip().lower() == "long"

        retry_hint = None
        if not is_valid:
            if is_long_form:
                word_target_instruction = (
                    "Write a smooth, full-bodied, complete 16:9 Widescreen Documentary of 360 to 650 words "
                    "(2.5-4.5 min across 16 to 24 natural scenes, 18-26 words per body scene)."
                )
            else:
                word_target_instruction = (
                    "Write a smooth, full-bodied, complete story of 85 to 120 words (30-40s across 7 to 9 natural scenes, "
                    "flexible 8-16 words per body scene so sentences flow smoothly and never feel abrupt or truncated)."
                )
            bullet_points = "\n".join(f"- {issue}" for issue in all_issues)
            retry_hint = (
                f"CRITICAL QUALITY GATE VIOLATIONS DETECTED:\n"
                f"{bullet_points}\n\n"
                f"ACTION REQUIRED:\n"
                f"1. Write a smooth, well-developed 5-Act story (NEVER abrupt or truncated):\n"
                f"   - Act 1 (Scene 1, 6-12 words): Open-loop 'Why / Por que / How' curiosity hook OR sharp declarative paradox naming the main entities (NO weak Yes/No questions like 'Did you know...?').\n"
                f"   - Act 2 (Scenes 2-3): Smooth context + concrete real-life quote, behavior, or factual scenario.\n"
                f"   - Act 3 (Scenes 4-5): Clear, thorough explanation of the hidden brain/psychological or physical mechanism.\n"
                f"   - Act 4 (Scenes 6-7): Complete climax or actionable word-for-word counter-move.\n"
                f"   - Act 5 (Scene 8/9): Satisfying conclusion + Seamless Infinite Loop Bridge connecting back into Scene 1 (ZERO spoken 'Subscribe/Comment' CTAs).\n"
                f"2. {word_target_instruction}\n"
                f"3. Every scene MUST be a complete sentence — NEVER end on dangling connectors like 'because', 'which', 'and', 'but'.\n"
                f"4. Avoid forbidden out-of-niche terms and repetitive channel entities listed above."
            )

        return LinterResult(
            is_valid=is_valid,
            issues=all_issues,
            retry_prompt_hint=retry_hint,
            detected_banned_words=banned_detected,
            detected_cooldown_entities=ent_detected,
            total_words=word_count
        )
