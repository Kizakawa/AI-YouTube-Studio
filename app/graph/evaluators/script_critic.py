"""
app.graph.evaluators.script_critic — Bộ chấm điểm & phản biện kịch bản tự động bằng Gemini.

Đánh giá 5 tiêu chí chuẩn hóa:
1. Hook Strength (Sức hút 3s đầu): 30%
2. Story Pacing & Flow (Nhịp điệu kể chuyện): 25%
3. Entity & Historical Coherence (Tính chân thật & thực thể): 25%
4. Visual Direction (Khả năng hiện thực hóa hình ảnh): 10%
5. Viral Shorts Loop (Vòng lặp giữ chân): 10%
"""
import json
import logging
import re
from typing import Dict, Any, Optional

import requests
from config.settings import settings
from app.graph.state import ScriptEvaluationReport, ScriptCriteriaScore
from app.utils.key_rotator import KeyRotator
from app.utils.langsmith_tracer import trace_custom_span

logger = logging.getLogger(__name__)


class ScriptCritic:
    """
    Critic Agent độc lập đóng vai trò Giám đốc Nội dung (Content Director).
    Phản biện kịch bản một cách khắt khe để đảm bảo chất lượng triệu view cho YouTube Shorts.
    """

    def __init__(self):
        keys = settings.get_gemini_keys_pool()
        self.rotator = KeyRotator(keys) if keys else None

    @classmethod
    def get_calibrated_thresholds(
        cls,
        channel_id: Optional[str] = None,
        channel_style: str = "general",
    ) -> Dict[str, Any]:
        """
        Hiệu chỉnh ngưỡng chấm Hook (`min_hook_score`) và Tổng điểm (`min_overall_score`)
        dựa trên tương quan với tỷ lệ vuốt bỏ thật (`swipe_away_pct`) của kênh trong DB:
        - Nếu kênh có tỷ lệ vuốt bỏ trung bình cao (swipe_away_pct >= 34%): siết ngưỡng Hook lên 8.5/10, Overall >= 8.2/10.
        - Nếu kênh có tỷ lệ vuốt bỏ trung bình vừa (30% <= swipe_away_pct < 34%): ngưỡng Hook >= 8.2/10, Overall >= 8.1/10.
        - Nếu kênh có tỷ lệ giữ chân tốt (swipe_away_pct < 30%): ngưỡng Hook >= 8.0/10, Overall >= 8.0/10.
        """
        avg_swipe = 31.0
        avg_stayed = 69.0
        avg_apv = 85.0
        winners = []
        losers = []
        canon_cid = ""

        try:
            from app.database.models import normalize_channel_id
            from app.services.video_library import get_weekly_winners_and_losers

            if channel_id:
                canon_cid = normalize_channel_id(channel_id)
            else:
                niche_low = (channel_style or "").strip().lower()
                for ch in settings.get_channels_config():
                    if str(ch.get("niche") or "").strip().lower() == niche_low:
                        canon_cid = ch.get("id", "")
                        break
            if canon_cid:
                perf = get_weekly_winners_and_losers(canon_cid, top_k=3)
                avg_swipe = float(perf.get("avg_swipe_away_pct", 31.0))
                avg_stayed = float(perf.get("avg_stayed_to_watch_pct", 69.0))
                avg_apv = float(perf.get("avg_view_percentage", 85.0))
                winners = perf.get("winners", [])
                losers = perf.get("losers", [])
        except Exception as e:
            logger.debug(f"[ScriptCritic] Không thể đọc dữ liệu swipe_away_pct cho {channel_id}: {e}")

        if avg_swipe >= 34.0:
            min_hook = 8.5
            min_overall = 8.2
        elif avg_swipe >= 30.0:
            min_hook = 8.2
            min_overall = 8.1
        else:
            min_hook = 8.0
            min_overall = 8.0

        return {
            "channel_id": canon_cid,
            "avg_swipe_away_pct": avg_swipe,
            "avg_stayed_to_watch_pct": avg_stayed,
            "avg_view_percentage": avg_apv,
            "min_hook_score": min_hook,
            "min_overall_score": min_overall,
            "winners": winners,
            "losers": losers,
        }

    @classmethod
    def _calibrate_hook_score_with_real_data(
        cls,
        raw_hook_score: float,
        script_data: Optional[Dict[str, Any]] = None,
        calibration: Optional[Dict[str, Any]] = None,
        channel_style: str = "general",
        scene_1_narration: Optional[str] = None,
        title: Optional[str] = None,
        thresholds: Optional[Dict[str, Any]] = None,
    ) -> tuple[float, list[str]]:
        """
        Kiểm định thực nghiệm câu Hook (Cảnh 1) dựa trên dữ liệu vuốt bỏ thật của kênh
        và công thức chiến thắng "A vs B" + câu khẳng định gây sốc của pt-BR / Wild Odyssey.
        Hỗ trợ cả 2 kiểu truyền tham số (script_data/calibration và scene_1_narration/title/thresholds).
        """
        extra_critiques: list[str] = []
        score = float(raw_hook_score)
        s_data = script_data or {}
        cal = calibration or thresholds or {"min_hook_score": 8.0, "min_overall_score": 8.0, "losers": []}
        scenes = s_data.get("scenes") or []
        eff_title = str(title if title is not None else (s_data.get("title") or "")).strip()
        if scene_1_narration is not None:
            s1_narr = str(scene_1_narration).strip()
        else:
            s1_narr = str(scenes[0].get("narration") or "").strip() if scenes and isinstance(scenes[0], dict) else ""
        combined = f"{eff_title} {s1_narr}".strip()

        words = re.findall(r"\b\w+\b", s1_narr, flags=re.UNICODE)
        w_cnt = len(words)

        # 0. Kiểm tra rò rỉ Tiếng Việt trong kịch bản Tiếng Anh / Tiếng Bồ Đào Nha (chặn triệt để lỗi phụ đề Tiếng Việt ở đầu video)
        vn_exclusive_re = re.compile(r"[ơớờởỡợưứừửữựđăắằẳẵặảẩẫậẻểễệỉịỏổỗộủửữựỷỹỵ]", re.IGNORECASE)
        vn_hyphen_re = re.compile(r"\b(?:xé-to-cando|va-cham|sự\s+thật|bạn\s+có\s+biết)\b", re.IGNORECASE)
        all_narr_text = " ".join(str(s.get("narration") or "") for s in scenes if isinstance(s, dict))
        script_lang = str(s_data.get("language") or "").strip().lower()
        is_non_vi_script = script_lang.startswith(("en", "pt")) or (
            not script_lang
            and len(vn_exclusive_re.findall(all_narr_text)) < max(8, len(all_narr_text.split()) // 4)
        )
        if is_non_vi_script and (vn_exclusive_re.search(f"{eff_title} {all_narr_text}") or vn_hyphen_re.search(f"{eff_title} {all_narr_text}")):
            score = min(score, 2.0)
            extra_critiques.append(
                "CRITICAL LANGUAGE LEAKAGE: The script contains Vietnamese words or syllables inside an English/Portuguese script! "
                "Rewrite ALL scenes (especially Scene 1 Hook) 100% in the target language (English or Brazilian Portuguese) with ZERO Vietnamese words."
            )

        # 1. Cấm câu hỏi Yes/No yếu hoặc ẩn dụ văn chương trừu tượng ở Cảnh 1 (tương quan trực tiếp với Swipe-Away > 70%)
        weak_q = re.match(
            r"^(?:did you know|do you know|have you ever|are you|can you|would you|imagine if|what if i told you|bạn có biết|você sabia)\b",
            s1_narr,
            re.IGNORECASE,
        )
        abstract_metaphor = re.search(
            r"\b(?:loaded weapon|silent cage|invisible armor|a word\.\s*a face|sweet compliment is actually)\b",
            s1_narr,
            re.IGNORECASE,
        )
        if weak_q or abstract_metaphor:
            score = min(score, 5.5)
            extra_critiques.append(
                f"HOOK BLOCKED: Scene 1 uses a weak Yes/No question or abstract metaphor ('{s1_narr}') which causes >70% swipe-away. "
                f"Immediately name the primary entity/combatants + a concrete physical action or paradox in 6-10 words!"
            )

        # 1B. Chặn Đọc Nhãn Tiêu Đề Có Dấu Hai Chấm ("A vs B: ...") VÀ Chặn Hook Mơ Hồ Giấu Chủ Thể ("Look closely at..." / "Watch the exact moment...")
        # Thực nghiệm YouTube Analytics v2 (Jobs #615 5.9% Stay, #618 7.8% Stay, #632 16.9% Stay, #623 30.1% Stay vs Winners #555 51.4% Stay, #589 1,325 views/12h):
        # - Khi Cảnh 1 mở đầu bằng câu chỉ trỏ mơ hồ ("Look closely at why you cannot find the exit", "Watch the exact moment this massive predator feels pain")
        #   mà KHÔNG nêu đích danh Thực thể chính hoặc con số/xung đột cụ thể ngay ở 0.0s, 70%–94% người xem trên Shorts Feed vuốt bỏ ngay lập tức!
        # - Ngược lại, 100% video đạt 45%–54% Stayed và >1,000–40,000 views đều nêu đích danh Thực thể + Con số/Nghịch lý sinh tử ngay ở câu đầu tiên!
        _stop_echo = {
            "the", "and", "for", "with", "from", "into", "this", "that", "how", "why", "what", "when", "who",
            "uma", "uns", "umas", "dos", "das", "nos", "nas", "por", "para", "com", "sem", "sob", "sobre",
            "que", "como", "mais", "muito", "este", "esta", "esse", "essa", "isso", "pelo", "pela",
            "shorts", "short", "video",
        }
        title_clean_no_tag = re.sub(r"#\w+", " ", eff_title).strip()
        title_tokens = {
            w.lower()
            for w in re.findall(r"\b[a-zA-ZÀ-ỹ0-9]{3,}\b", title_clean_no_tag, flags=re.UNICODE)
            if w.lower() not in _stop_echo and not w.isdigit()
        }
        s1_first_clause = re.split(r"[.!?]", s1_narr)[0].strip() if s1_narr else ""
        s1_clause_tokens = {
            w.lower()
            for w in re.findall(r"\b[a-zA-ZÀ-ỹ0-9]{3,}\b", s1_first_clause, flags=re.UNICODE)
            if w.lower() not in _stop_echo
        }
        is_colon_header_echo = bool(
            re.match(
                r"^[^.!?]{2,40}\b(?:vs\.?|versus|contra)\b[^.!?]{2,40}:",
                s1_narr,
                re.IGNORECASE | re.UNICODE,
            )
        )
        is_early_outcome_spoiler = bool(
            re.search(
                r"\b(?:vencem|vence|venceu|derrotam|derrota|ganha|wins|won|defeats|defeated|beats)\b",
                s1_first_clause,
                re.IGNORECASE | re.UNICODE,
            )
            and is_colon_header_echo
        )
        is_vague_pointer_prefix = bool(
            re.match(
                r"^(?:look\s+closely\s+at|watch\s+the\s+exact\s+moment|watch\s+what\s+happens\s+when|olhe\s+bem\s+para|repare\s+bem\s+n[oa]s?)\b",
                s1_first_clause,
                re.IGNORECASE | re.UNICODE,
            )
        )
        _num_word_re = (
            r"(?:\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|percent|milliseconds|seconds|psi|"
            r"um|uma|dois|duas|três|quatro|cinco|seis|sete|oito|nove|dez|cem|duzentos|trezentos|quatrocentos|quinhentos|mil|milissegundos|segundos|quilos|metros|toneladas)\b)"
        )
        is_entity_blind_hook = bool(
            title_tokens
            and len(title_tokens) >= 2
            and len(title_tokens & s1_clause_tokens) == 0
            and not re.search(_num_word_re, s1_first_clause, re.IGNORECASE | re.UNICODE)
        )
        if is_colon_header_echo or is_early_outcome_spoiler:
            score = min(score, 5.8)
            extra_critiques.append(
                f"COLON TITLE HEADER ECHO BLOCKED: Scene 1 ('{s1_first_clause[:65]}') reads a colon header or spoils the winner at 0.0s! "
                f"Write a natural active sentence naming the primary entity + visceral physical impact or life-or-death paradox at 0.0s."
            )
        elif is_vague_pointer_prefix or is_entity_blind_hook:
            score = min(score, 5.8)
            extra_critiques.append(
                f"ENTITY-BLIND / VAGUE POINTER HOOK BLOCKED: Scene 1 ('{s1_first_clause[:65]}') hides the primary subject or uses a generic 'Look closely / Watch the exact moment' pointer (proven 70%-94% Swipe-Away on Shorts)! "
                f"Scene 1 MUST explicitly name the primary entity/combatants from the title + a vivid physical clash or shocking paradox right at 0.0s!"
            )

        # 2. Kiểm tra độ dài Cảnh 1 chuẩn triệu view (4-14 từ cho Shorts, 4-34 từ cho Long-Form Documentary)
        is_lf_hook = str(s_data.get("video_format") or "short").strip().lower() == "long" or len(scenes) >= 14
        max_hook_w = 46 if is_lf_hook else 14
        if w_cnt > max_hook_w or w_cnt < 3:
            score = max(1.0, score - 1.2)
            extra_critiques.append(
                f"Scene 1 hook length ({w_cnt} words) is suboptimal. Condense to 6-9 words (max {max_hook_w} words) to match the opening cut."
            )

        # 2B. Chặn nhồi nhét nhiều vòng đấu (Round 1 / Round 2 / Round 3) vào Shorts 35s gây rớt view ở giây 14 (như Job #623)
        if not is_lf_hook and isinstance(scenes, list):
            full_short_text = " ".join(str(sc.get("narration") or "") for sc in scenes if isinstance(sc, dict))
            if re.search(r"\bround\s+(?:one|two|three|1|2|3)\b", full_short_text, re.IGNORECASE):
                score = min(score, 5.5)
                extra_critiques.append(
                    "MULTI-ROUND BAIT-AND-SWITCH IN SHORTS BLOCKED: Never use 'Round One / Round Two / Round Three' or switch to a 3rd unannounced opponent inside a 35-second Short! "
                    "Focus 100% of all 7-8 scenes on the exact 1v1 duel or single phenomenon promised in the Title."
                )

        # 3. Đối chiếu chống lặp lại mô-típ của Bottom 3 Losers (video có swipe_away_pct cao nhất kênh)
        s1_tokens = {w.lower() for w in words if len(w) > 3}
        for loser in cal.get("losers", []):
            l_hook = str(loser.get("hook_text") or loser.get("title") or "")
            l_tokens = {w.lower() for w in re.findall(r"\b\w+\b", l_hook, flags=re.UNICODE) if len(w) > 3}
            if s1_tokens and l_tokens:
                overlap = len(s1_tokens & l_tokens) / max(1, min(len(s1_tokens), len(l_tokens)))
                if overlap >= 0.65 and len(s1_tokens & l_tokens) >= 4:
                    score = max(1.0, score - 2.0)
                    l_swp = loser.get("swipe_away_pct")
                    swp_label = f"{float(l_swp):.1f}%" if l_swp is not None else "high"
                    extra_critiques.append(
                        f"Scene 1 repeats the exact pattern of a high-swipe-away loser video ({swp_label} swipe-away: '{l_hook[:60]}'). Use a completely fresh hook angle!"
                    )
                    break

        # 4. Kiểm tra cấu trúc Tiêu đề không giấu thực thể ("This Beast / This Creature") — phần đánh giá ngữ nghĩa & độ hấp dẫn giao hoàn toàn cho LLM Critic
        vague_title_match = bool(
            eff_title
            and re.search(
                r"\b(?:this\s+(?:cute\s+|deadly\s+|terrifying\s+|tiny\s+|giant\s+)?(?:creature|animal|beast|place|point|bird|thing|monster|predator)|"
                r"invisible\s+monsters|one\s+bite\s+is\s+all(?:\s+it\s+takes)?|the\s+point\s+in\s+the\s+ocean\s+where|"
                r"este\s+animal|esta\s+criatura|esta\s+ave)\b",
                eff_title,
                re.IGNORECASE,
            )
        )
        if vague_title_match:
            score = min(score, 6.5)
            extra_critiques.append(
                f"VAGUE TITLE BLOCKED: Title hides the primary entity ('{eff_title}'). "
                f"Explicitly name the exact species/entity/location in the title!"
            )

        return round(score, 1), extra_critiques

    @trace_custom_span("ScriptCritic.evaluate_script", run_type="llm")
    def evaluate_script(
        self,
        script_data: Dict[str, Any],
        topic: str,
        channel_style: str = "general",
        primary_entity: Optional[str] = None,
        channel_id: Optional[str] = None,
    ) -> ScriptEvaluationReport:
        """Chấm điểm kịch bản theo dữ liệu giữ chân thật và trả về báo cáo chi tiết kèm hướng dẫn sửa chữa."""
        calibration = self.get_calibrated_thresholds(channel_id=channel_id, channel_style=channel_style)
        if not self.rotator:
            return self._heuristic_evaluate(script_data, primary_entity, calibration=calibration, channel_style=channel_style)

        key = self.rotator.get_key()
        if not key:
            return self._heuristic_evaluate(script_data, primary_entity, calibration=calibration, channel_style=channel_style)

        scenes = script_data.get("scenes", [])
        title = script_data.get("title", "")
        scenes_formatted = "\n".join([
            f"Scene {s.get('scene_id')}: [Visual: {s.get('visual_keyword')}] -> \"{s.get('narration')}\""
            for s in scenes
        ])

        entity_instruction = ""
        if primary_entity:
            entity_instruction = (
                f"- Primary Entity/Subject is '{primary_entity}'. "
                f"The script and visual keywords MUST stay anchored to this exact entity."
            )

        winners_block = ""
        if calibration.get("winners"):
            w_lines = [
                f"  + WINNER: \"{w['title']}\" (Hook: \"{w['hook_text']}\") -> {w['views']} views, Stayed: {w['stayed_to_watch_pct']}%, Swipe-Away: {w['swipe_away_pct']}%"
                for w in calibration["winners"][:3]
            ]
            winners_block += "\n- REAL TOP 3 WINNING VIDEOS OF THIS CHANNEL:\n" + "\n".join(w_lines)
        if calibration.get("losers"):
            l_lines = [
                f"  - LOSER: \"{l['title']}\" (Hook: \"{l['hook_text']}\") -> {l['views']} views, Swipe-Away: {l['swipe_away_pct']}% (DO NOT REPEAT THIS HOOK PATTERN)"
                for l in calibration["losers"][:3]
            ]
            winners_block += "\n- REAL BOTTOM 3 LOSING VIDEOS OF THIS CHANNEL:\n" + "\n".join(l_lines)

        min_hook_req = calibration.get("min_hook_score", 8.2)
        avg_swipe_pct = calibration.get("avg_swipe_away_pct", 31.0)
        is_long_form = str(script_data.get("video_format") or "short").strip().lower() == "long"
        pacing_criterion_desc = (
            "2. story_pacing (Target Long-Form 16:9 Documentary: 8.0 - 11.0 min, 1,200 - 1,700 words, 36 - 48 scenes across 8-10 Chapters — NEVER ABRUPT):"
            if is_long_form
            else "2. story_pacing (Target Shorts 9:16: Smooth, complete 5-Act story of 30 - 40s, 85 - 120 words, 7 - 9 scenes — NEVER ABRUPT):"
        )

        from app.services.niche_manager import build_llm_production_overview_prompt
        niche_yaml_blueprint = build_llm_production_overview_prompt(
            channel_style or "general",
            include_all_niches_map=False,
        )

        prompt = f"""You are a World-Class YouTube {'16:9 Documentary & ' if is_long_form else ''}Shorts Creative Director and Retention Script Critic.
Your task: Semantically evaluate and critique the following script based on REAL RETENTION DATA and the YAML NICHE BLUEPRINT of the channel (use contextual intelligence — never apply rigid keyword censorship to valid scientific, historical, psychological, or wildlife vocabulary).

Topic: {topic}
Proposed Title: {title}
Format Mode: {'LONG-FORM DOCUMENTARY 16:9 (36-48 scenes, 1,200-1,700 words, ~10 minutes)' if is_long_form else 'SHORTS 9:16 (7-9 scenes, 85-120 words)'}
Channel Style: {channel_style}
Channel Average Swipe-Away Rate: {avg_swipe_pct:.1f}% -> Mandatory threshold for hook_strength >= {min_hook_req}/10.
{entity_instruction}{winners_block}

{niche_yaml_blueprint}

--- SCRIPT CONTENT ---
{scenes_formatted}

--- 5-ACT RETENTION EVALUATION RUBRIC (Scale 1 - 10) ---
1. hook_strength (Hard gate >= {min_hook_req}/10 — Target: Viewed > 75%, Swipe-Away < 25%):
   - Scene 1 (6-10 words) MUST explicitly name the primary entity/combatants and open with a concrete physical action, entity-specific sensory image, or sharp paradox.
   - Fails (< 6.0) if it dumps unexplained textbook/laboratory units that ordinary viewers cannot visualize, opens with a weak Yes/No question ("Did you know...", "Have you ever..."), textbook definition, repeats a Loser hook pattern, or contains any leaked foreign/Vietnamese words that do not match the script's language.
{pacing_criterion_desc}
   - Reward (>= 8.5) smooth, vivid 5-Act storytelling that deeply understands the specific entity's real anatomy, physical mechanism, environment, or human behavior, translating complex mechanics into fresh, intuitive, tailor-made everyday imagery rather than abstract textbook units.
   - Penalize (< 6.5) scripts that read like a dry science lecture citing raw physics/lab units without visualizable human-scale context, OR scripts that lazily paste generic recycled clichés onto entities without explaining how that specific entity actually works.
3. entity_coherence:
   - Evaluate how deeply and accurately the script characterizes the specific entity/phenomenon (its unique weapon, vulnerability, tactic, or psychological mechanism) rather than generic interchangeable filler.
4. visual_direction: `visual_keyword` and `stock_query` are concrete English physical nouns/actions matching the topic (>= 8.5).
5. viral_loop (ZERO spoken Subscribe CTA at the end):
   - Final scene delivers a satisfying payoff and bridges seamlessly back to Scene 1.

--- OUTPUT REQUIREMENT ---
CRITICAL LANGUAGE RULE: Write all `feedback` and `actionable_critiques` in ENGLISH, and if you suggest any example hook/narration phrasing, write that example strictly in the EXACT SAME LANGUAGE as the script above (NEVER suggest Vietnamese sentences for an English or Portuguese script!).
Return ONLY a single valid JSON object (no markdown) with this schema:
{{
  "hook_strength": {{"score": 8.5, "feedback": "Detailed feedback on Scene 1 Hook..."}},
  "story_pacing": {{"score": 8.8, "feedback": "Feedback on smooth 5-Act flow (85-120 words)..."}},
  "entity_coherence": {{"score": 9.0, "feedback": "Detailed feedback..."}},
  "visual_direction": {{"score": 8.8, "feedback": "Feedback on visual context..."}},
  "viral_loop": {{"score": 8.8, "feedback": "Feedback on final scene payoff & loop bridge..."}},
  "actionable_critiques": [
    "Specific surgical instructions in English on how to improve Scene 1 Hook or pacing..."
  ]
}}"""

        from app.utils.key_rotator import GEMINI_PRIORITY_MODELS
        for model, key in self.rotator.iter_best_model_key_pairs(GEMINI_PRIORITY_MODELS, max_total_attempts=6):
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}]
                }
                req_timeout = 14 if model.startswith("gemma-") else 20
                r = requests.post(url, json=payload, timeout=req_timeout)
                if r.status_code in (429, 403, 404, 500, 502, 503, 504):
                    logger.warning(f"ScriptCritic: Model {model} trên key {key[:8]}... gặp mã {r.status_code}, chuyển ứng viên kế tiếp...")
                    self.rotator.mark_model_rate_limited(key, model, status_code=r.status_code, error_text=r.text[:250])
                    continue

                if r.status_code == 200:
                    data = r.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        raw = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
                        raw = raw.strip()
                        if "```" in raw:
                            raw = re.sub(r"^```(?:json)?\s*", "", raw)
                            raw = re.sub(r"\s*```$", "", raw)
                        start = raw.find("{")
                        end = raw.rfind("}")
                        if start != -1 and end != -1:
                            raw = raw[start:end+1]
                        parsed = json.loads(raw)
                        return self._build_report(parsed, script_data=script_data, calibration=calibration, channel_style=channel_style)
            except Exception as e:
                logger.warning(f"ScriptCritic ({model}) error: {e}")
                self.rotator.mark_model_rate_limited(key, model, status_code=503, error_text=str(e))
                continue

        return self._heuristic_evaluate(script_data, primary_entity, calibration=calibration, channel_style=channel_style)

    def _build_report(
        self,
        parsed: Dict[str, Any],
        script_data: Optional[Dict[str, Any]] = None,
        calibration: Optional[Dict[str, Any]] = None,
        channel_style: str = "general",
    ) -> ScriptEvaluationReport:
        cal = calibration or {"min_hook_score": 8.0, "min_overall_score": 8.0, "losers": []}
        h = parsed.get("hook_strength", {})
        p = parsed.get("story_pacing", {})
        e = parsed.get("entity_coherence", {})
        v = parsed.get("visual_direction", {})
        l = parsed.get("viral_loop", {})

        raw_hook_s = float(h.get("score", 7.5))
        hook_s, hook_extra_critiques = self._calibrate_hook_score_with_real_data(
            raw_hook_score=raw_hook_s,
            script_data=script_data or {},
            calibration=cal,
            channel_style=channel_style,
        )
        pace_s = float(p.get("score", 7.5))
        ent_s = float(e.get("score", 7.5))
        vis_s = float(v.get("score", 7.5))
        loop_s = float(l.get("score", 7.5))

        # Trọng số chuẩn hóa: Hook 30%, Pacing 25%, Entity 25%, Visual 10%, Loop 10%
        overall = (hook_s * 0.30) + (pace_s * 0.25) + (ent_s * 0.25) + (vis_s * 0.10) + (loop_s * 0.10)
        overall = round(overall, 1)

        critiques = list(parsed.get("actionable_critiques", []) or [])
        for hc in reversed(hook_extra_critiques):
            if hc not in critiques:
                critiques.insert(0, hc)

        min_hook = float(cal.get("min_hook_score", 8.0))
        min_overall = float(cal.get("min_overall_score", 8.0))
        # CHẶN CỨNG: Bắt buộc tổng điểm, điểm Hook Cảnh 1 VÀ nhịp điệu Pacing (>= 7.5) đều phải vượt qua ngưỡng chuẩn!
        passed = (overall >= min_overall) and (hook_s >= min_hook) and (pace_s >= 7.5)
        if not passed and hook_s < min_hook:
            msg = f"Scene 1 Hook ({hook_s:.1f}/10) is below the channel anti-swipe threshold ({min_hook:.1f}/10 — Avg Swipe-Away: {cal.get('avg_swipe_away_pct', 31.0):.1f}%)."
            if msg not in critiques:
                critiques.insert(0, msg)
        if not passed and pace_s < 7.5:
            msg_p = f"Story Pacing ({pace_s:.1f}/10) is too flat or mechanical (< 7.5/10). Inject a dramatic 2-way escalation and mid-video reversal twist at Scene 4!"
            if msg_p not in critiques:
                critiques.insert(0, msg_p)

        return ScriptEvaluationReport(
            hook_strength=ScriptCriteriaScore(score=hook_s, feedback=h.get("feedback", "")),
            story_pacing=ScriptCriteriaScore(score=pace_s, feedback=p.get("feedback", "")),
            entity_coherence=ScriptCriteriaScore(score=ent_s, feedback=e.get("feedback", "")),
            visual_direction=ScriptCriteriaScore(score=vis_s, feedback=v.get("feedback", "")),
            viral_loop=ScriptCriteriaScore(score=loop_s, feedback=l.get("feedback", "")),
            overall_score=overall,
            passed=passed,
            actionable_critiques=critiques,
        )

    def _heuristic_evaluate(
        self,
        script_data: Dict[str, Any],
        primary_entity: Optional[str] = None,
        calibration: Optional[Dict[str, Any]] = None,
        channel_style: str = "general",
    ) -> ScriptEvaluationReport:
        """Đánh giá dự phòng bằng quy tắc thuật toán khi không thể gọi LLM, áp dụng cùng bộ lọc dữ liệu thật."""
        cal = calibration or self.get_calibrated_thresholds(channel_style=channel_style)
        scenes = script_data.get("scenes", [])
        total_words = sum(len(s.get("narration", "").split()) for s in scenes)
        scene_count = len(scenes)

        # Kiểm tra Hook ở cảnh 1
        scene1 = scenes[0] if scenes else {}
        narr1 = scene1.get("narration", "").lower()
        words1 = narr1.split()
        is_short_hook = 3 <= len(words1) <= 18
        raw_hook_score = 8.8 if is_short_hook else 7.6
        hook_score, hook_extra = self._calibrate_hook_score_with_real_data(
            raw_hook_score=raw_hook_score,
            script_data=script_data,
            calibration=cal,
            channel_style=channel_style,
        )

        # Kiểm tra nhịp điệu & độ đầy đặn của mạch truyện theo định dạng Short vs Long
        is_long_form = str(script_data.get("video_format") or "short").strip().lower() == "long"
        if is_long_form:
            pace_score = 8.9 if 240 <= total_words <= 2400 and 14 <= scene_count <= 60 else 6.8
        else:
            pace_score = 8.8 if 55 <= total_words <= 155 and 5 <= scene_count <= 12 else 6.8

        # Kiểm tra thực thể tương đối trên tổng thể kịch bản (không khóa cứng theo visual_keyword từng cảnh)
        ent_score = 8.8
        if primary_entity:
            full_narr_lower = " ".join(s.get("narration", "") for s in scenes).lower()
            ent_score = 9.0 if primary_entity.lower() in full_narr_lower else 8.5

        vis_score = 8.8

        # Kiểm tra vòng lặp vô tận (Seamless Loop) & KHÔNG dính câu xin Subscribe ở cảnh cuối
        last_scene = scenes[-1] if scenes else {}
        last_narr = last_scene.get("narration", "").strip().lower()
        has_bad_outro_cta = any(w in last_narr for w in ["subscribe", "inscreva-se", "đăng ký kênh", "leave a comment", "leave your theory"])
        has_loop_context = (not has_bad_outro_cta) and (len(last_narr.split()) >= 5 and last_narr[-1:] in (".", "!", "?"))
        loop_score = 8.8 if has_loop_context else 6.5

        overall = round((hook_score * 0.3) + (pace_score * 0.25) + (ent_score * 0.25) + (vis_score * 0.1) + (loop_score * 0.1), 1)

        min_hook = float(cal.get("min_hook_score", 8.0))
        min_overall = float(cal.get("min_overall_score", 8.0))
        critiques = list(hook_extra)
        if hook_score < min_hook:
            critiques.append(f"Scene 1 ({hook_score:.1f}/10) needs an urgent 'A vs B' or shocking metric hook (channel threshold >= {min_hook:.1f}/10).")
        if pace_score < 8.0:
            critiques.append("Script pacing needs a complete 5-Act flow (88-118 words across 7-9 complete scenes, 32-42 seconds total).")
        if loop_score < 8.0:
            critiques.append("Final scene must not include spoken Subscribe CTAs; bridge seamlessly back to Scene 1.")

        passed = (overall >= min_overall) and (hook_score >= min_hook) and (pace_score >= 7.5)

        return ScriptEvaluationReport(
            hook_strength=ScriptCriteriaScore(score=hook_score, feedback="Evaluated against real channel swipe-away rate"),
            story_pacing=ScriptCriteriaScore(score=pace_score, feedback=f"Total words: {total_words} across {scene_count} scenes"),
            entity_coherence=ScriptCriteriaScore(score=ent_score, feedback=f"Primary entity: {primary_entity or 'General topic'}"),
            visual_direction=ScriptCriteriaScore(score=vis_score, feedback="Visual keywords validated"),
            viral_loop=ScriptCriteriaScore(score=loop_score, feedback="Loop bridge validated"),
            overall_score=overall,
            passed=passed,
            actionable_critiques=critiques,
            evaluated_by="heuristic",
        )

