"""app.agents.content.viral_ideation_mixin — Đấu trường kịch bản tốt nhất, tự sửa theo chấm điểm QA & gợi ý ý tưởng viral."""
import json
import logging
import requests
import time
import re
from typing import Dict, List, Any, Optional
from config.settings import settings
from app.utils.key_rotator import KeyRotator, GEMINI_PRIORITY_MODELS
from app.utils.langsmith_tracer import trace_custom_span
from app.schemas.contracts import ScriptContract

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = list(GEMINI_PRIORITY_MODELS)



from app.agents.content.prompt_instructions import (
    build_language_integrity_instruction,
    build_regional_hook_mastery_instruction,
)


class ViralIdeationMixin:
    def generate_best_script(
        self,
        topic: str,
        channel_niche: str = "general",
        language: str = "en",
        director_notes: Optional[str] = None,
        min_acceptable_score: float = 8.0,
        max_attempts: int = 3,
        cooldown_entities: Optional[List[str]] = None,
        channel_id: Optional[str] = None,
        strict_block: bool = True,
        **kwargs,
    ) -> tuple:
        """
        Quy trình biên soạn cao cấp: Tạo kịch bản -> Tự động chấm điểm bằng ScriptCritic
        với ngưỡng Hook được hiệu chỉnh theo tỷ lệ vuốt bỏ thật (swipe_away_pct) của kênh.
        Nếu sau max_attempts vẫn không đạt ngưỡng và strict_block=True -> Chặn cứng (raise RuntimeError).
        Trả về tuple: (ScriptContract, ScriptEvaluationReport).
        """
        from app.graph.evaluators.script_critic import ScriptCritic
        critic = ScriptCritic()
        cal_thresh = critic.get_calibrated_thresholds(channel_id=channel_id, channel_style=channel_niche)
        effective_min_overall = max(float(min_acceptable_score or 8.0), float(cal_thresh.get("min_overall_score", 8.0)))
        effective_min_hook = float(cal_thresh.get("min_hook_score", 8.0))
        avg_swipe = cal_thresh.get("avg_swipe_away_pct")

        # 1. Tạo kịch bản sơ khởi
        best_script = self.generate_script(
            topic=topic,
            channel_niche=channel_niche,
            language=language,
            director_notes=director_notes,
            cooldown_entities=cooldown_entities,
            channel_id=channel_id,
            **kwargs,
        )

        try:
            best_eval = critic.evaluate_script(
                script_data=best_script.model_dump(),
                topic=topic,
                channel_style=channel_niche,
                primary_entity=getattr(best_script, "primary_entity", None),
                channel_id=channel_id,
            )
        except Exception as e:
            logger.warning(f"Lỗi khi chấm điểm kịch bản sơ bộ: {e}")
            if strict_block:
                raise RuntimeError(f"🚫 [HardBlock QA] Không thể thẩm định chất lượng kịch bản: {e}")
            return best_script, None

        # Nếu ngay vòng 1 đã đạt chuẩn xuất sắc (cả overall lẫn hook_strength) -> Trả về ngay
        if (
            best_eval.overall_score >= effective_min_overall
            and best_eval.hook_strength.score >= effective_min_hook
            and best_eval.passed
        ):
            logger.info(
                f"✅ [ContentAgent] Kịch bản đạt chuẩn ngay vòng 1: "
                f"Overall={best_eval.overall_score:.1f}/{effective_min_overall:.1f} | "
                f"Hook={best_eval.hook_strength.score:.1f}/{effective_min_hook:.1f}"
            )
            return best_script, best_eval

        # 2. Vòng lặp tự động tái tạo & hoàn thiện cho đến khi đạt điểm chuẩn
        logger.info(
            f"⚠️ [ContentAgent] Kịch bản sơ bộ chưa đạt "
            f"(Overall={best_eval.overall_score:.1f}/{effective_min_overall:.1f}, "
            f"Hook={best_eval.hook_strength.score:.1f}/{effective_min_hook:.1f}, "
            f"Channel Avg Swipe-Away={avg_swipe}%). "
            f"Bắt đầu quy trình tự động tái tạo (Tối đa {max_attempts} lần thử)..."
        )

        lang_low = str(language or "en").lower()
        if lang_low.startswith("vi"):
            target_lang_note = "QUAN TRỌNG: NGÔN NGỮ KỊCH BẢN BẮT BUỘC LÀ 100% TIẾNG VIỆT. Toàn bộ lời thoại và tiêu đề phải viết bằng Tiếng Việt chuẩn."
        elif lang_low.startswith("pt"):
            target_lang_note = "CRITICAL: TARGET SCRIPT LANGUAGE IS 100% BRAZILIAN PORTUGUESE (PORTUGUÊS DO BRASIL). All rewritten narration, hook, and title MUST be in natural Brazilian Portuguese."
        else:
            target_lang_note = "CRITICAL: TARGET SCRIPT LANGUAGE IS 100% ENGLISH. All rewritten narration, hook, and title MUST be in natural English."

        for attempt in range(1, max_attempts + 1):
            critiques_list = list(best_eval.actionable_critiques) if best_eval.actionable_critiques else []

            # Thêm chỉ đạo chuyên sâu theo các tiêu chí bị điểm kém (đặc biệt là ngưỡng Hook theo swipe_away_pct)
            if best_eval.hook_strength.score < effective_min_hook:
                critiques_list.insert(
                    0,
                    f"Scene 1 Hook is below the anti-swipe threshold ({best_eval.hook_strength.score:.1f}/{effective_min_hook:.1f}, "
                    f"channel swipe_away={avg_swipe}%): {best_eval.hook_strength.feedback}. "
                    f"MUST use an urgent 6-9 word visual pointer or 'Entity A vs Entity B' confrontation with a concrete physical metric at 0.0s."
                )
            vf_mode = "long" if str(kwargs.get("video_format") or getattr(best_script, "video_format", "short")).lower() in ("long", "16:9", "documentary") else "short"
            if best_eval.story_pacing.score < 8.0:
                if vf_mode == "long":
                    critiques_list.append(
                        f"Long-Form 16:9 pacing needs adjustment ({best_eval.story_pacing.score:.1f}/10): {best_eval.story_pacing.feedback}. "
                        f"Target 380-650 words across 16-22 scenes (8-14s/scene) in 5 Chapters."
                    )
                else:
                    critiques_list.append(
                        f"Pacing needs adjustment ({best_eval.story_pacing.score:.1f}/10): {best_eval.story_pacing.feedback}. "
                        f"Target 88-118 words across 7-8 complete scenes (3.5-4.5s/scene)."
                    )
            if getattr(best_eval, "visual_direction", None) and best_eval.visual_direction.score < 8.0:
                critiques_list.append(f"Weak visual direction ({best_eval.visual_direction.score:.1f}/10): visual_keyword and stock_query MUST be concrete English physical nouns/actions.")

            directive = (
                f"PREVIOUS DRAFT SCORED BELOW THRESHOLD (Overall={best_eval.overall_score:.1f}/{effective_min_overall:.1f}, "
                f"Hook={best_eval.hook_strength.score:.1f}/{effective_min_hook:.1f}). {target_lang_note} "
                f"MANDATORY VIRAL REVISIONS: " + " | ".join(critiques_list)
            )

            try:
                if attempt < max_attempts:
                    # Tinh chỉnh sâu kịch bản hiện tại với các chỉ đạo sửa lỗi
                    candidate_script = self.refine_script(
                        current_script=best_script,
                        feedback=directive,
                        channel_niche=channel_niche,
                        language=language,
                        director_notes=director_notes,
                        video_format=vf_mode,
                    )
                else:
                    # Lần thử cuối cùng: Tái tạo kịch bản từ đầu với góc nhìn sốc và sắc bén hơn
                    refreshed_topic = f"{topic} (A vs B confrontation, shocking physical claim hook)"
                    merged_notes = f"{director_notes or ''}\nMUST FIX: {'; '.join(critiques_list[:3])}".strip()
                    candidate_script = self.generate_script(
                        topic=refreshed_topic,
                        channel_niche=channel_niche,
                        language=language,
                        director_notes=merged_notes,
                        cooldown_entities=cooldown_entities,
                        channel_id=channel_id,
                        **kwargs,
                    )

                candidate_eval = critic.evaluate_script(
                    script_data=candidate_script.model_dump(),
                    topic=topic,
                    channel_style=channel_niche,
                    primary_entity=getattr(candidate_script, "primary_entity", None),
                    channel_id=channel_id,
                )

                logger.info(
                    f"🔄 [ContentAgent Auto-Retry {attempt}/{max_attempts}] "
                    f"Overall: {candidate_eval.overall_score:.1f}/{effective_min_overall:.1f} | "
                    f"Hook: {candidate_eval.hook_strength.score:.1f}/{effective_min_hook:.1f} | "
                    f"Passed: {candidate_eval.passed}"
                )

                # Cập nhật ứng viên tốt nhất (ưu tiên kịch bản passed hoặc có tổng điểm + hook cao hơn)
                cand_rank = (1 if candidate_eval.passed else 0, candidate_eval.hook_strength.score * 0.5 + candidate_eval.overall_score * 0.5)
                best_rank = (1 if best_eval.passed else 0, best_eval.hook_strength.score * 0.5 + best_eval.overall_score * 0.5)
                if cand_rank > best_rank:
                    best_script = candidate_script
                    best_eval = candidate_eval

                # Kiểm tra nếu đã vượt qua ngưỡng chuẩn
                if (
                    candidate_eval.overall_score >= effective_min_overall
                    and candidate_eval.hook_strength.score >= effective_min_hook
                    and candidate_eval.passed
                ):
                    logger.info(
                        f"🎉 [ContentAgent] Kịch bản đã đạt chuẩn xuất sắc "
                        f"(Overall={candidate_eval.overall_score:.1f}, Hook={candidate_eval.hook_strength.score:.1f}) ở lần thử {attempt}!"
                    )
                    return candidate_script, candidate_eval

            except Exception as e:
                logger.warning(f"Lỗi trong vòng lặp tự sửa kịch bản lần {attempt}: {e}")
                continue

        if strict_block and (
            best_eval.overall_score < (effective_min_overall - 0.25)
            or best_eval.hook_strength.score < (effective_min_hook - 0.25)
        ):
            raise RuntimeError(
                f"🚫 [HardBlock ScriptQA] Kịch bản/Hook không đạt ngưỡng giữ chân sau {max_attempts} vòng tối ưu "
                f"(Overall={best_eval.overall_score:.1f}/{effective_min_overall:.1f}, "
                f"Hook={best_eval.hook_strength.score:.1f}/{effective_min_hook:.1f}, "
                f"Channel Swipe-Away={avg_swipe}%). Đã chặn sản xuất video không đạt chuẩn!"
            )

        logger.info(f"🏁 [ContentAgent] Hoàn thành tối ưu hóa kịch bản, điểm tốt nhất đạt được: {best_eval.overall_score:.1f}/10")
        return best_script, best_eval

    def refine_with_feedback_and_eval(
        self,
        current_script: ScriptContract,
        feedback: str,
        channel_niche: str = "general",
        language: str = "en",
        min_acceptable_score: float = 8.0,
        max_attempts: int = 3
    ) -> tuple:
        """
        Tiếp thu góp ý chỉnh sửa của người dùng, tổng hợp lại kịch bản,
        tự động chấm điểm qua ScriptCritic và tinh chỉnh liên tục cho đến khi đạt chuẩn.
        Trả về tuple: (ScriptContract, ScriptEvaluationReport).
        """
        from app.graph.evaluators.script_critic import ScriptCritic
        critic = ScriptCritic()

        logger.info(f"✍️ [ContentAgent] Bắt đầu tổng hợp góp ý: '{feedback}' cho kênh {channel_niche} ({language})")

        # 1. Tinh chỉnh lần 1 dựa trên góp ý trực tiếp của người dùng
        best_script = self.refine_script(
            current_script=current_script,
            feedback=feedback,
            channel_niche=channel_niche,
            language=language
        )

        try:
            best_eval = critic.evaluate_script(
                script_data=best_script.model_dump(),
                topic=best_script.title,
                channel_style=channel_niche,
                primary_entity=getattr(best_script, "primary_entity", None)
            )
        except Exception as e:
            logger.warning(f"Lỗi chấm điểm sau khi nhận feedback: {e}")
            return best_script, None

        if best_eval.overall_score >= min_acceptable_score and best_eval.passed:
            logger.info(f"✅ [ContentAgent] Kịch bản sau góp ý đạt chuẩn ngay lần 1: {best_eval.overall_score:.1f}/10")
            return best_script, best_eval

        # 2. Vòng lặp cải thiện: Vừa giữ trọn vẹn yêu cầu của người dùng, vừa khắc phục điểm yếu của Critic
        logger.info(
            f"⚠️ [ContentAgent] Kịch bản sau góp ý đạt {best_eval.overall_score:.1f}/10 (< {min_acceptable_score}). "
            f"Tiến hành tối ưu tự động để vừa thỏa mãn góp ý, vừa đạt điểm chất lượng cao..."
        )

        lang_low = str(language or "en").lower()
        if lang_low.startswith("vi"):
            target_lang_note = "QUAN TRỌNG: NGÔN NGỮ KỊCH BẢN BẮT BUỘC LÀ 100% TIẾNG VIỆT. Toàn bộ lời thoại và tiêu đề phải viết bằng Tiếng Việt chuẩn."
        elif lang_low.startswith("pt"):
            target_lang_note = "CRITICAL: TARGET SCRIPT LANGUAGE IS 100% BRAZILIAN PORTUGUESE (PORTUGUÊS DO BRASIL). All rewritten narration, hook, and title MUST be in natural Brazilian Portuguese."
        else:
            target_lang_note = "CRITICAL: TARGET SCRIPT LANGUAGE IS 100% ENGLISH. All rewritten narration, hook, and title MUST be in natural English."

        for attempt in range(1, max_attempts + 1):
            critiques_list = list(best_eval.actionable_critiques) if best_eval.actionable_critiques else []
            combined_directive = (
                f"YÊU CẦU CỐT LÕI CỦA NGƯỜI DÙNG (BẮT BUỘC TUÂN THỦ 100%): \"{feedback}\"\n"
                f"{target_lang_note}\n"
                f"ĐỒNG THỜI KHẮC PHỤC CÁC LỖI CRITIC SAU ĐỂ ĐẠT CHUẨN VIRAL: " + " | ".join(critiques_list[:3])
            )

            try:
                candidate_script = self.refine_script(
                    current_script=best_script,
                    feedback=combined_directive,
                    channel_niche=channel_niche,
                    language=language
                )

                candidate_eval = critic.evaluate_script(
                    script_data=candidate_script.model_dump(),
                    topic=candidate_script.title,
                    channel_style=channel_niche,
                    primary_entity=getattr(candidate_script, "primary_entity", None)
                )

                logger.info(
                    f"🔄 [ContentAgent Feedback-Retry {attempt}/{max_attempts}] "
                    f"Điểm mới: {candidate_eval.overall_score:.1f}/10 (Cũ: {best_eval.overall_score:.1f}/10)"
                )

                if candidate_eval.overall_score > best_eval.overall_score:
                    best_script = candidate_script
                    best_eval = candidate_eval

                if candidate_eval.overall_score >= min_acceptable_score and candidate_eval.passed:
                    logger.info(f"🎉 [ContentAgent] Kịch bản tinh chỉnh đạt điểm xuất sắc: {candidate_eval.overall_score:.1f}/10!")
                    return candidate_script, candidate_eval

            except Exception as e:
                logger.warning(f"Lỗi khi tinh chỉnh kịch bản theo góp ý lần {attempt}: {e}")
                continue

        return best_script, best_eval

    def suggest_viral_ideas(
        self,
        channel_id: str = "ch_psychology_of_the_world",
        channel_name: str = "Psychology of the world",
        channel_niche: str = "psychology",
        language: str = "en",
        recent_titles: Optional[List[str]] = None,
        count: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Sinh danh sách các chủ đề Shorts triệu view bám sát nội dung thực tế của kênh:
        - Đọc danh sách các video đã đăng/sản xuất của kênh để tránh trùng lặp.
        - Trích xuất động Top 3 Winners & Bottom 3 Losers từ DB thực tế (APV%, Stay%, Swipe%, Views).
        - Xây dựng cụm chủ đề liên hoàn (Topic Cluster / Binge-watching Playlist) kích thích xem tiếp.
        """
        from app.database.models import normalize_channel_id
        channel_id = normalize_channel_id(channel_id)
        is_psychology = "psychology" in channel_niche.lower() or "tâm lý" in channel_niche.lower()

        live_feedback_block = ""
        try:
            from app.services.video_library import get_weekly_winners_and_losers
            wl = get_weekly_winners_and_losers(channel_id, top_k=3)
            live_feedback_block = wl.get("prompt_block", "")
        except Exception as e:
            logger.debug(f"[ContentAgent] Could not fetch live winners/losers for {channel_id}: {e}")

        recent_context_block = ""
        clean_titles = [f"  - \"{t}\"" for t in (recent_titles or [])[:8] if t]
        recent_titles_str = chr(10).join(clean_titles) if clean_titles else "  - (See live channel database telemetry below)"
        recent_context_block = f"""
═══════════════════════════════════════════════════════════
📺 CHANNEL IDENTITY & LIVE PERFORMANCE TELEMETRY:
Channel ID: '{channel_id}' | Channel Name: '{channel_name}' | Niche: '{channel_niche}'
Recently Produced / Uploaded Videos on this Channel (DO NOT DUPLICATE):
{recent_titles_str}

{live_feedback_block}

CRITICAL CHANNEL ALIGNMENT & BINGE-WATCHING RULES:
1. DEEP CHANNEL SYNERGY: The {count} suggested concepts MUST feel like natural sister episodes in a binge-worthy playlist for '{channel_name}'.
2. NO DUPLICATES: DO NOT suggest topics or titles already covered in the recent videos list or live telemetry above!
3. DATA-DRIVEN REPLICATION: Replicate the hook architecture, pacing, and curiosity angle of the Top 3 Proven Winners above while strictly avoiding the structural patterns of the Bottom 3 Losers.
═══════════════════════════════════════════════════════════
"""

        fallback_psychology_en = [
            {
                "topic": "The Interrogation Water Cup Test: Why FBI Agents Place a Cup 6 Inches From a Suspect",
                "title": "Why FBI Agents Put a Water Cup 6 Inches Away 🕵️‍♂️🥤 #Shorts #psychology #fbi",
                "hook": "Guilty suspects always fail this simple water cup test.",
                "angle": "Autonomic dry-mouth reflex under cortisol spike, barrier placement between suspect and detective",
                "pinned_comment": "Quick Poll: In an interrogation, would you (1) drink the water or (2) never touch it? Vote 1 or 2! 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "140% - 160%",
                "why_viral": "Kỹ thuật thẩm vấn FBI trực quan nối tiếp bài thắng 966 views 'Serial Killers In Court'"
            },
            {
                "topic": "Inside the -9.4 Decibel Chamber: Why No Human Brain Can Survive 45 Minutes of Total Silence",
                "title": "Why Nobody Survives 45 Minutes in This Silent Room 🧠🔇 #Shorts #psychology #brain",
                "hook": "Forty-five minutes in this silent room causes terrifying hallucinations.",
                "angle": "Orfield Anechoic Chamber -9.4 dBA, hearing your own blood flow and lungs, auditory cortex hallucination",
                "pinned_comment": "Quick Poll: Could you last (1) 45 minutes in total silence or (2) tap out in 10 minutes? Vote 1 or 2! 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "145% - 165%",
                "why_viral": "Ngách Thí nghiệm Não bộ Cực đoan đạt trung bình 4 triệu views trên YouTube Shorts Mỹ"
            },
            {
                "topic": "How Las Vegas Casinos Use Oxygen, Carpet Patterns, and Zero Clocks to Hijack Your Brain",
                "title": "How Casinos Hijack Your Brain Without You Knowing 🎰🧠 #Shorts #psychology #money",
                "hook": "Casinos banned clocks and windows to trap your brain.",
                "angle": "Gruen transfer labyrinth layout, geometric carpet eye-push, near-miss slot machine dopamine spike",
                "pinned_comment": "Quick Poll: Have you ever lost track of time inside a casino or mall? (1) Yes or (2) Never! 👇",
                "viral_formula": "Double Tap & Protect",
                "predicted_retention": "135% - 155%",
                "why_viral": "Giải mã bẫy tâm lý tiền bạc và dopamine — thu hút tệp quảng cáo RPM cao nhất tại Mỹ"
            },
            {
                "topic": "The 1971 Stanford Prison Collapse: How 24 Normal Students Turned Ruthless in Just 6 Days",
                "title": "The Brain Experiment Shut Down After Just 6 Days 🚨🧠 #Shorts #psychology #experiment",
                "hook": "Twenty-four healthy students turned ruthless inside a basement prison.",
                "angle": "Philip Zimbardo Stanford Prison Experiment, mirrored sunglasses deindividuation, 6-day psychological breakdown",
                "pinned_comment": "Quick Poll: Does power (1) corrupt good people or (2) expose who they really are? Vote 1 or 2! 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "145% - 165%",
                "why_viral": "Thí nghiệm tâm lý học bị cấm trong lịch sử luôn có APV xem lặp trên 110%"
            },
            {
                "topic": "Why Trained Liars Control Their Face But Always Betray Themselves With Their Feet (FBI Profiling)",
                "title": "Why FBI Agents Watch Your FEET When You Lie 🕵️‍♂️👟 #Shorts #psychology #interrogation",
                "hook": "FBI interrogators watch your feet, never your eyes, when you lie.",
                "angle": "Limbic freeze response, unconscious foot-pointing toward exit door while face smiles",
                "pinned_comment": "Quick Poll: When someone lies, do you check (1) their eyes or (2) their hands/feet? Vote 1 or 2! 👇",
                "viral_formula": "Double Tap & Protect",
                "predicted_retention": "140% - 160%",
                "why_viral": "Kỹ thuật đọc vị cơ thể tại phòng thẩm vấn FBI dễ kiểm chứng ngay lập tức"
            }
        ]

        fallback_psychology_vi = fallback_psychology_en

        niche_topic_bank: List[str] = []
        try:
            from app.services.niche_manager import get_niche
            n_prof = get_niche(channel_niche)
            niche_topic_bank = list((n_prof.get("script_blueprint") or {}).get("viral_topic_bank_2026") or [])
        except Exception:
            pass

        hook_mastery_block = build_regional_hook_mastery_instruction(language=language, channel_niche=channel_niche)
        topic_bank_block = (
            "\nPROVEN 2026 TOPIC ARCHETYPES FOR THIS NICHE:\n" + "\n".join(f"  - {t}" for t in niche_topic_bank[:6])
            if niche_topic_bank else ""
        )

        prompt = f"""You are an elite YouTube Shorts Growth Hacker and Viral Strategist for the channel '{channel_name}' in niche: '{channel_niche}'.
Language: {language}
Goal: Generate {count} ULTRA-VIRAL video concepts tailored specifically to match this channel's existing identity and build upon its highest-performing video.
{recent_context_block}
{hook_mastery_block}{topic_bank_block}

CORE RETENTION PRINCIPLES:
1. High-Stakes Niche Confrontation ('A vs. B' + Concrete Physical/Historical/Behavioral Metric): Frame every concept around an undeniable physical or situational clash backed by a hard number.
2. Electric 1.8s Hook (5-8 words max in Grade-5 conversational language): Immediate deictic pointer or physical shock at 0.0s.
3. Psychological Call to Action (Double Tap & Protect, Debate in Comments, Curiosity Part 2).
4. Pinned Bait Question: A binary 1-vs-2 poll question to pin at the top of comments to ignite massive debate and subscriber conversion.

Return ONLY a JSON array with exactly {count} objects:
[
  {{
    "topic": "Clean topic name",
    "title": "Viral YouTube Shorts Title with Emojis and #hashtags",
    "hook": "Opening 5-8 word hook line",
    "angle": "Core physical/historical/behavioral mechanism and mid-video twist",
    "pinned_comment": "High-converting binary (1 vs 2) pinned bait question for the comment section",
    "viral_formula": "Double Tap & Protect | Debate in Comments | Curiosity Part 2",
    "predicted_retention": "135% - 165%",
    "why_viral": "Brief 1-sentence reason why this will blow up and fits the channel"
  }}
]
"""
        try:
            raw_text = self._call_api(prompt, json_mode=False, timeout=20)
            if raw_text:
                import re
                text = raw_text.strip()
                if text.startswith("```"):
                    lines = text.split("\n")
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    text = "\n".join(lines).strip()
                start = text.find("[")
                end = text.rfind("]")
                if start != -1 and end != -1 and end > start:
                    text = text[start:end+1]
                ideas = json.loads(text)
                if isinstance(ideas, list) and len(ideas) > 0:
                    return ideas[:count]
        except Exception as e:
            logger.warning(f"[ContentAgent] Lỗi khi sinh ý tưởng viral từ Gemini: {e}")

        # Fallback preset chuẩn chất lượng cao theo đúng ngách kênh
        if is_psychology:
            return (fallback_psychology_vi if language == "vi" else fallback_psychology_en)[:count]
        if niche_topic_bank:
            return [
                {
                    "topic": tb,
                    "title": f"{tb[:58]} 🔥 #{channel_niche.split('_')[0]}",
                    "hook": tb.split(":")[-1].strip() if ":" in tb else tb,
                    "angle": "High-stakes confrontation with exact physical metrics and mid-video twist",
                    "pinned_comment": "Be honest: if it were a 2-on-1 ambush at the same time, does the outcome flip? (1) Yes or (2) No chance? 👇🔥",
                    "viral_formula": "Debate in Comments",
                    "predicted_retention": "130% - 155%",
                    "why_viral": f"Proven A-vs-B million-view archetype for {channel_name}",
                }
                for tb in niche_topic_bank[:count]
            ]
        return fallback_psychology_en[:count]
