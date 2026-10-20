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

# Danh sách từ nối / giới từ / đại từ sở hữu lửng lơ (EN + PT + VI) tuyệt đối không được đứng cuối câu/kịch bản
TRAILING_ENGLISH_CONNECTORS = [
    "because", "which", "and", "so", "that", "but", "with",
    "or", "while", "as", "if", "since", "although", "though", "where",
    "of", "to", "for", "from", "in", "on", "at", "by", "into", "onto", "until",
    "his", "her", "their", "its", "my", "your", "our", "the", "a", "an",
    "que", "de", "do", "da", "dos", "das", "pois", "porque", "para", "com",
    "em", "no", "na", "nos", "nas", "mas", "ou", "quando", "onde",
]

# Regex nhận diện đuôi câu kết thúc bằng từ nối đơn độc hoặc dấu ba chấm lửng
TRAILING_CONNECTOR_PATTERN = re.compile(
    r'\b(?:' + '|'.join(TRAILING_ENGLISH_CONNECTORS) + r')\s*(?:[\.\,\-\—\…]+|\.{2,})*$',
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

        # 1. Kiểm tra cảnh cuối cùng (quan trọng nhất đối với Infinite Shorts Loop)
        last_scene = scenes[-1]
        last_text = STAGE_TAG_PATTERN.sub("", (last_scene.narration or "")).strip()
        
        # Bỏ dấu chấm than, chấm hỏi cuối câu nếu có
        cleaned_last = re.sub(r'[\s\.\!\?]+$', '', last_text)
        if TRAILING_CONNECTOR_PATTERN.search(cleaned_last):
            match = TRAILING_CONNECTOR_PATTERN.search(cleaned_last).group(0).strip(" .,-—…")
            errors.append(
                f"Scene {last_scene.scene_id} (Final Scene) ends abruptly on an incomplete word '{match}'. "
                f"Full sentences are required. Do NOT leave trailing prepositions, possessives, or conjunctions."
            )

        # 2. Kiểm tra từng scene riêng lẻ xem có scene nào bị cắt cụt không
        for sc in scenes[:-1]:
            sc_text = STAGE_TAG_PATTERN.sub("", (sc.narration or "")).strip()
            cleaned_sc = re.sub(r'[\s\.\!\?]+$', '', sc_text)
            if TRAILING_CONNECTOR_PATTERN.search(cleaned_sc):
                match = TRAILING_CONNECTOR_PATTERN.search(cleaned_sc).group(0).strip(" .,-—…")
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
        Kiểm tra xem kịch bản có chứa từ ngữ dị biệt, lạc ngách hoặc ảo giác (hallucination) không.
        Đọc cấu hình động từ niche_profile (banned_lexicon).
        """
        issues = []
        detected_words = []

        blueprint = niche_profile.get("script_blueprint", {})
        banned_list = niche_profile.get("banned_lexicon") or blueprint.get("banned_lexicon") or []
        
        # Bổ sung các từ bị cấm mặc định trong blueprint
        forbidden_cliches = blueprint.get("forbidden_words", [])

        full_text = f"{script.title} {script.hook} {script.full_narration}".lower()

        # 1. Kiểm tra banned_lexicon (từ ngữ dị biệt / lạc ngách)
        for word in banned_list:
            word_clean = str(word).strip().lower()
            if not word_clean:
                continue
            # Dùng regex word boundary để tránh bắt nhầm substring
            pattern = r'\b' + re.escape(word_clean) + r'\b'
            if re.search(pattern, full_text):
                detected_words.append(word_clean)
                issues.append(
                    f"Forbidden/out-of-niche term detected: '{word_clean}'. "
                    f"This concept is strictly irrelevant or incongruent with the '{niche_profile.get('name', 'current')}' niche."
                )

        # 2. Kiểm tra forbidden clichés (câu sáo rỗng mở đầu/kết thúc)
        for cliche in forbidden_cliches:
            cliche_clean = str(cliche).strip().lower()
            if len(cliche_clean) > 3 and cliche_clean in full_text:
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
        - Cho phép biên độ thoải mái từ 75 đến 135 từ (mục tiêu 85 - 120 từ, 6 - 11 cảnh) để LLM dẫn dắt câu chuyện tự nhiên, giải thích thấu đáo, không bị cắt cụt ý.
        """
        issues = []
        all_narrations = " ".join(s.narration for s in script.scenes if s.narration)
        words = re.findall(r'\b\w+\b', all_narrations)
        total_words = len(words)

        min_words = 75
        max_words = 125
        target_range_str = "85 to 110 words (33-40s smooth, complete 5-Act story across 7-9 natural scenes)"

        if total_words > max_words:
            est_seconds = (total_words / max(target_wpm, 165)) * 60
            issues.append(
                f"Script is overly long ({total_words} words, ~{est_seconds:.1f}s). "
                f"Keep around {target_range_str}."
            )
        elif total_words < min_words:
            est_seconds = (total_words / max(target_wpm, 165)) * 60
            issues.append(
                f"Script is too short and abrupt ({total_words} words, ~{est_seconds:.1f}s) — feels truncated and lacks smooth context/explanation! "
                f"Expand to at least {min_words} words (target {target_range_str}) so the story flows naturally from start to finish."
            )

        scene_count = len(script.scenes)
        min_scenes = 6
        if scene_count < min_scenes:
            issues.append(
                f"Too few scenes ({scene_count}). Need 7 to 9 scenes (minimum {min_scenes}) to tell a complete, well-paced story."
            )
        elif scene_count > 11:
            issues.append(
                f"Too many scenes ({scene_count}). Keep between 7 and 10 complete-sentence scenes so the narrative flows smoothly."
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
            s1_words = re.findall(r'\b\w+\b', s1_narr)
            if len(s1_words) > 12:
                issues.append(
                    f"Scene 1 hook is too long ({len(s1_words)} words). Keep Scene 1 between 5 and 10 punchy words for an instant thumb-stopper."
                )
            elif len(s1_words) < 3:
                issues.append(
                    f"Scene 1 hook is too fragmented ({len(s1_words)} words). Must be a complete 5-9 word declarative hook."
                )

            # CẤM câu hỏi Yes/No yếu ở Cảnh 1 (trừ ngách Alternate History cho phép 'What if...')
            is_what_if_niche = "what_if" in niche_id or "alternate_history" in niche_id
            weak_yes_no_pattern = re.compile(
                r"^(?:did you|do you|have you|are you|can you|would you|could you|is it|was it|why do|why did|what did|"
                r"bạn có biết|bạn đã bao giờ|có bao giờ bạn|bạn có đang|phải chăng|"
                r"você sabia|você já)\b",
                re.IGNORECASE,
            )
            if weak_yes_no_pattern.search(s1_narr) or ("?" in s1_narr and not (is_what_if_niche and s1_narr.lower().startswith("what if"))):
                issues.append(
                    f"Scene 1 hook uses a weak question ('{s1_narr}'). "
                    f"100% of Scene 1 hooks MUST be a sharp declarative statement or warning (NO '?' or Yes/No questions like 'Have you ever...' / 'Do you...')."
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

        # Kiểm tra tiêu đề kênh Animal Rescue tránh rơi vào bẫy bi lụy đơn thuần không có curiosity/payoff
        if "rescue" in niche_id or "animal" in niche_id:
            title_lower = (script.title or "").lower()
            bleak_only_terms = ("frozen", "shivering", "starving", "tied in rain", "dying", "paralyzed", "bleeding")
            transformation_cues = (
                "until", "finally", "moment", "realized", "refused", "saved", "safe",
                "miracle", "changed", "never", "watch what", "wait for", "heart", "love"
            )
            if any(b in title_lower for b in bleak_only_terms) and not any(t in title_lower for t in transformation_cues):
                issues.append(
                    f"Bleak/passive rescue title detected ('{script.title}'). Purely depressing titles get swiped away! "
                    f"Reframe the title around a curiosity gap or emotional breakthrough (e.g., 'He Refused to Let Go Until...', "
                    f"'The Exact Moment This Shivering Puppy Realized He Was Safe')."
                )

        # Kiểm tra tiêu đề kênh Wildlife Predators tránh chung chung mơ hồ ('Invisible Monsters Waiting To Strike')
        if "wildlife" in niche_id or "predator" in niche_id:
            title_lower = (script.title or "").lower()
            vague_wildlife_titles = ("invisible monsters", "unstoppable survivers", "unstoppable survivors", "prehistoric apex predators")
            if any(v in title_lower for v in vague_wildlife_titles):
                issues.append(
                    f"Vague/generic wildlife title detected ('{script.title}'). Always name the specific apex predator or 1v1 matchup "
                    f"(e.g., 'Green Anaconda vs. Black Caiman', 'Why 6 Lions Backed Down From 1 Honey Badger')."
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

        retry_hint = None
        if not is_valid:
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
                f"   - Act 1 (Scene 1, 5-9 words): Sharp declarative statement, 1v1 duel, or warning hook (NO weak Yes/No questions).\n"
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
