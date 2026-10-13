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
        target_wpm: int = 155,
        niche_id: str = ""
    ) -> Tuple[List[str], int]:
        """
        Kiểm tra số từ và tính toán nhịp đọc (Pacing):
        - Chuẩn vàng YouTube Shorts có Payoff vượt ngưỡng 10k-50k view (35 - 48s tại 150-165 WPM) là 80 đến 130 từ (7 - 10 cảnh).
        - Riêng ngách animal_rescue (Paws & Hearts Rescue): dữ liệu thực nghiệm chứng minh video đạt 1,200 - 2,280 views & +32 subs
          nằm ở vùng 45 - 56 giây (90 - 145 từ, 8 - 10 cảnh) để đủ 4 giai đoạn chuyển hóa cảm xúc (4-Stage Transformation Arc).
        """
        issues = []
        all_narrations = " ".join(s.narration for s in script.scenes if s.narration)
        words = re.findall(r'\b\w+\b', all_narrations)
        total_words = len(words)

        topic_str = getattr(script, "topic", "") or ""
        nid_lower = (niche_id or "").lower()
        is_rescue = ("rescue" in nid_lower or "animal" in nid_lower) or any(
            k in f"{script.title} {topic_str}".lower() for k in ("rescue", "cứu hộ", "puppy", "kitten", "stray dog", "abandoned")
        )
        min_words = 85 if is_rescue else 72
        max_words = 145 if is_rescue else 135
        target_range_str = "95 to 135 words (42-55s 4-Stage Transformation Arc)" if is_rescue else "85 to 125 words (35-48s 4-Beat Payoff Arc)"

        if total_words > max_words:
            est_seconds = (total_words / target_wpm) * 60
            issues.append(
                f"Script is too long ({total_words} words, ~{est_seconds:.1f}s at {target_wpm} WPM). "
                f"Target limit: strictly {target_range_str}."
            )
        elif total_words < min_words:
            est_seconds = (total_words / target_wpm) * 60
            issues.append(
                f"Script is too short ({total_words} words, ~{est_seconds:.1f}s) — lacks depth for Mid-Video Re-Hook & Payoff! "
                f"Minimum {min_words} words required (target {target_range_str})."
            )

        # Số lượng scene: 7 đến 10 scene là nhịp tối ưu cho Shorts 35-52s (3.5 - 5.0s/scene)
        scene_count = len(script.scenes)
        min_scenes = 7 if is_rescue else 6
        if scene_count < min_scenes:
            issues.append(
                f"Too few scenes ({scene_count}). Need at least {min_scenes} scenes for visual pacing and Payoff (target 7 to 10 scenes)."
            )
        elif scene_count > 12:
            issues.append(
                f"Too many scenes ({scene_count}). Limit to 7 to 10 focused scenes (3.5-5.0s each) for optimal visual retention."
            )

        return issues, total_words

    @classmethod
    def check_retention_hooks_and_title(
        cls,
        script: ScriptContract,
        niche_profile: Dict[str, Any]
    ) -> List[str]:
        """
        Kiểm tra chất lượng Hook 3s đầu (Cảnh 1), Mid-Video Re-Hook (Cảnh 3-4) và Tiêu đề:
        1. Cảnh 1 không được quá dài (> 18 từ) làm chậm nhịp mở đầu gây Swipe-Away.
        2. Chặn tiêu đề bi lụy/tuyệt vọng thuần túy trên kênh Animal Rescue (ví dụ: 'Frozen Puppy Found in Rain')
           mà thiếu mồi câu tò mò / hứa hẹn sự chuyển hóa (Until, Moment, Refused, Realized, Saved, Safe...).
        """
        issues = []
        niche_id = str(niche_profile.get("id", "")).lower()

        if script.scenes:
            s1_narr = (script.scenes[0].narration or "").strip()
            s1_words = re.findall(r'\b\w+\b', s1_narr)
            if len(s1_words) > 18:
                issues.append(
                    f"Scene 1 hook is too long ({len(s1_words)} words). Keep Scene 1 between 5 and 14 punchy words "
                    f"for an instant 3-second thumb-stopper (Viewed > 75%)."
                )
            elif len(s1_words) < 3:
                issues.append(
                    f"Scene 1 hook is too fragmented ({len(s1_words)} words). Must be a complete 5-12 word curiosity/danger hook."
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
        target_wpm: int = 155
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
            is_rescue = "rescue" in niche_id or "animal" in niche_id
            word_target_instruction = (
                "Keep total word count strictly between 95 and 135 words (42-55s pacing at 150 WPM, 8-10 scenes) "
                "to deliver the full 4-Stage Emotional Transformation Arc (Trauma -> Gentle Contact -> Healing -> Joyful Payoff)."
                if is_rescue else
                "Keep total word count strictly between 85 and 125 words (35-48s pacing at 155 WPM, 7-10 scenes) "
                "with a 3s Hook (Scene 1), 12s Mid-Video Re-Hook Twist (Scene 4), and deep Payoff Reveal (Scenes 5-7)."
            )
            bullet_points = "\n".join(f"- {issue}" for issue in all_issues)
            retry_hint = (
                f"CRITICAL QUALITY GATE VIOLATIONS DETECTED (MUST FIX FOR YOUTUBE SHORTS RETENTION):\n"
                f"{bullet_points}\n\n"
                f"ACTION REQUIRED:\n"
                f"1. Ensure the final sentence is 100% grammatically complete, includes a natural value-driven CTA, and conceptually bridges into Scene 1's hook (Infinite Loop). NEVER end with 'because', 'which', 'and', 'but', etc.\n"
                f"2. {word_target_instruction}\n"
                f"3. Strictly avoid all forbidden concepts/terms and repetitive channel entities listed above.\n"
                f"4. Rewrite cleanly while preserving an electric 5-12 word Scene 1 hook (Viewed > 75%), a mid-video pattern interrupt at Scene 4, and an unforgettable Payoff."
            )

        return LinterResult(
            is_valid=is_valid,
            issues=all_issues,
            retry_prompt_hint=retry_hint,
            detected_banned_words=banned_detected,
            detected_cooldown_entities=ent_detected,
            total_words=word_count
        )
