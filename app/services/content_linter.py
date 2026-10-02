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

# Danh sách từ nối lửng lơ trong tiếng Anh tuyệt đối không được đứng cuối câu/kịch bản
TRAILING_ENGLISH_CONNECTORS = [
    "because", "which", "and", "so", "that", "but", "with",
    "or", "while", "as", "if", "since", "although", "though", "where"
]

# Regex nhận diện đuôi câu kết thúc bằng từ nối đơn độc hoặc dấu ba chấm lửng
TRAILING_CONNECTOR_PATTERN = re.compile(
    r'\b(?:' + '|'.join(TRAILING_ENGLISH_CONNECTORS) + r')\s*(?:[\.\,\-\—\…]+|\.{2,})*$',
    re.IGNORECASE
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
        Kiểm tra toàn bộ phân cảnh và câu kết của kịch bản xem có bị cắt cụt lửng lơ không.
        """
        errors = []
        scenes = script.scenes or []
        if not scenes:
            return ["Script has no scenes."]

        # 1. Kiểm tra cảnh cuối cùng (quan trọng nhất đối với Infinite Shorts Loop)
        last_scene = scenes[-1]
        last_text = (last_scene.narration or "").strip()
        
        # Bỏ dấu chấm than, chấm hỏi cuối câu nếu có
        cleaned_last = re.sub(r'[\s\.\!\?]+$', '', last_text)
        if TRAILING_CONNECTOR_PATTERN.search(cleaned_last):
            match = TRAILING_CONNECTOR_PATTERN.search(cleaned_last).group(0).strip(" .,-—…")
            errors.append(
                f"Scene {last_scene.scene_id} (Final Scene) ends abruptly on an incomplete connector '{match}'. "
                f"Full sentences are required. Do NOT leave trailing prepositions or conjunctions."
            )

        # 2. Kiểm tra từng scene riêng lẻ xem có scene nào bị cắt cụt không
        for sc in scenes[:-1]:
            sc_text = (sc.narration or "").strip()
            cleaned_sc = re.sub(r'[\s\.\!\?]+$', '', sc_text)
            if TRAILING_CONNECTOR_PATTERN.search(cleaned_sc):
                match = TRAILING_CONNECTOR_PATTERN.search(cleaned_sc).group(0).strip(" .,-—…")
                errors.append(
                    f"Scene {sc.scene_id} narration ends on a dangling connector '{match}'. "
                    f"Each scene must be a grammatically coherent clause."
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
        target_wpm: int = 155
    ) -> Tuple[List[str], int]:
        """
        Kiểm tra số từ và tính toán nhịp đọc (Pacing):
        Chuẩn vàng YouTube Shorts Viral (35 - 45s tại 150-160 WPM) là 80 đến 115 từ.
        """
        issues = []
        all_narrations = " ".join(s.narration for s in script.scenes if s.narration)
        words = re.findall(r'\b\w+\b', all_narrations)
        total_words = len(words)

        # Quá dài (> 130 từ) -> Thời lượng vượt quá 50s, AVD sẽ tụt
        if total_words > 130:
            est_seconds = (total_words / target_wpm) * 60
            issues.append(
                f"Script is too long ({total_words} words, ~{est_seconds:.1f}s at {target_wpm} WPM). "
                f"Target limit: 80 to 115 words total to keep duration around 35-45 seconds for optimal viral retention."
            )
        # Quá ngắn (< 65 từ) -> Video dưới 25s không đủ độ sâu dẫn dắt nội dung
        elif total_words < 65:
            issues.append(
                f"Script is too short ({total_words} words). "
                f"Minimum 65 words required for adequate storytelling depth (target 80-115 words, 35-45s)."
            )

        # Số lượng scene: 8 đến 10 scene là nhịp tối ưu (3.0 - 4.5s/scene)
        scene_count = len(script.scenes)
        if scene_count < 6:
            issues.append(f"Too few scenes ({scene_count}). Need 8 to 10 fast-paced scenes for 35-45s duration.")
        elif scene_count > 12:
            issues.append(f"Too many scenes ({scene_count}). Limit to 8 to 10 focused scenes.")

        return issues, total_words

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
        target_wpm: int = 155
    ) -> LinterResult:
        """
        Hàm thẩm định tổng thể (Quality Gate).
        Trả về LinterResult với is_valid=True nếu đạt toàn bộ tiêu chuẩn.
        """
        all_issues: List[str] = []

        # 1. Trailing connector check
        conn_issues = cls.check_trailing_connectors(script)
        all_issues.extend(conn_issues)

        # 2. Banned lexicon & anomaly check
        lex_issues, banned_detected = cls.check_banned_lexicon(script, niche_profile)
        all_issues.extend(lex_issues)

        # 3. Pacing & length check
        pac_issues, word_count = cls.check_pacing_and_length(script, target_wpm)
        all_issues.extend(pac_issues)

        # 4. Entity cooldown check
        ent_issues, ent_detected = cls.check_entity_cooldown(script, cooldown_entities)
        all_issues.extend(ent_issues)

        is_valid = len(all_issues) == 0

        retry_hint = None
        if not is_valid:
            bullet_points = "\n".join(f"- {issue}" for issue in all_issues)
            retry_hint = (
                f"CRITICAL QUALITY GATE VIOLATIONS DETECTED (MUST FIX):\n"
                f"{bullet_points}\n\n"
                f"ACTION REQUIRED:\n"
                f"1. Ensure the final sentence is 100% grammatically complete. NEVER end with 'because', 'which', 'and', 'but', etc.\n"
                f"2. Keep total word count strictly between 80 and 115 words (35-45s pacing at 155 WPM, 8-10 scenes).\n"
                f"3. Strictly avoid the forbidden concepts/terms listed above.\n"
                f"4. Rewrite cleanly while preserving high retention hook, seamless loop, and cinematic intensity."
            )

        return LinterResult(
            is_valid=is_valid,
            issues=all_issues,
            retry_prompt_hint=retry_hint,
            detected_banned_words=banned_detected,
            detected_cooldown_entities=ent_detected,
            total_words=word_count
        )
