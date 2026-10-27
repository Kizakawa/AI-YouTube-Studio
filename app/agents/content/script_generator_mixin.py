"""app.agents.content.script_generator_mixin — Xây dựng bộ nhớ ngữ cảnh kênh, phác thảo Blueprint & sinh kịch bản 5 hồi."""
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


class ScriptGeneratorMixin:
    def _build_channel_context_memory(
        self,
        channel_niche: str,
        topic: str,
        channel_id: Optional[str] = None,
    ) -> str:
        """
        Hệ thống Trí nhớ Ngữ cảnh Kênh & Phản Hồi Dữ Liệu Thật Hàng Tuần (Weekly Real-Data Closed Loop):
        1. Mỗi tuần tự động truy vấn từ DB (`PublishedVideo`, `VideoJob`) để đưa:
           - Top 3 video thắng (Winners) kèm Views, Tỷ lệ xem hết (APV %), Tỷ lệ ở lại xem (Stayed %) & Tỷ lệ vuốt bỏ (Swipe-Away %) + câu Hook mở đầu.
           - Bottom 3 video thua (Losers) kèm chỉ số vuốt bỏ thật để LLM tránh lặp lại mô-típ mở đầu yếu.
           - 8 tiêu đề gần nhất đã sản xuất (chống lặp góc tiếp cận, câu mở đầu và từ khóa bão hòa).
        2. Áp dụng Quy tắc Vàng Phân Vai (`Title` ăn tìm kiếm `YT_SEARCH` vs. `Scene 1 Hook 0.0s` dùng 4 Đòn Tâm Lý Thị Giác khóa mắt người xem mà không lặp lại tiêu đề).
        """
        top_winners: List[str] = []
        bottom_losers: List[str] = []
        recent_titles: List[str] = []
        saturated_terms: List[str] = []
        channel_avg_swipe: float = 30.0
        week_label: str = ""
        canon_cid: Optional[str] = None

        try:
            from app.database.models import get_db, PublishedVideo, VideoJob, normalize_channel_id
            from app.services.smart_scheduler import resolve_channel_family
            from app.services.video_library import get_weekly_winners_and_losers

            target_ids: List[str] = []
            matched_handle: Optional[str] = None
            canon_cid = normalize_channel_id(channel_id) if channel_id else None
            niche_low = (channel_niche or "").strip().lower()
            for ch in settings.get_channels_config():
                cid = ch.get("id")
                ch_niche = str(ch.get("niche") or "").strip().lower()
                if (canon_cid and cid == canon_cid) or (not canon_cid and ch_niche == niche_low):
                    if cid:
                        if not canon_cid:
                            canon_cid = cid
                        target_ids.extend(resolve_channel_family(cid))
                    if not matched_handle and ch.get("youtube_handle"):
                        matched_handle = str(ch.get("youtube_handle")).strip()

            if canon_cid and not target_ids:
                target_ids.extend(resolve_channel_family(canon_cid))

            from app.services.channel_intelligence import is_off_niche_video_title, extract_saturated_channel_concepts

            # 1. Lấy tự động 3 Video Thắng (Top 3 Winners) & 3 Video Thua (Bottom 3 Losers) theo tuần từ DB thật
            if canon_cid:
                weekly_perf = get_weekly_winners_and_losers(canon_cid, top_k=3)
                week_label = weekly_perf.get("week_key", "")
                channel_avg_swipe = float(weekly_perf.get("avg_swipe_away_pct", 30.0))
                for idx, w in enumerate(weekly_perf.get("winners", []), 1):
                    w_title = w.get("title", "")
                    if w_title and not is_off_niche_video_title(w_title, niche_low):
                        w_hook = w.get("hook_text") or w_title
                        w_ret = (
                            f'Stayed: {w["stayed_to_watch_pct"]:.1f}% | Swipe-Away: {w["swipe_away_pct"]:.1f}% | Completion (APV): {w["avg_view_percentage"]:.1f}%'
                            if w.get("stayed_to_watch_pct") is not None and w.get("avg_view_percentage") is not None
                            else "⏳ Analytics pending 24-48h"
                        )
                        top_winners.append(
                            f'  * [WINNER #{idx}] Title: "{w_title}" | Scene 1 Hook: "{w_hook}" '
                            f'-> {w["views"]:,} views | {w_ret}'
                        )
                for idx, l in enumerate(weekly_perf.get("losers", []), 1):
                    l_title = l.get("title", "")
                    if l_title:
                        l_hook = l.get("hook_text") or l_title
                        l_ret = (
                            f'Stayed: {l["stayed_to_watch_pct"]:.1f}% | Swipe-Away: {l["swipe_away_pct"]:.1f}% | Completion (APV): {l["avg_view_percentage"]:.1f}%'
                            if l.get("stayed_to_watch_pct") is not None and l.get("avg_view_percentage") is not None
                            else "⏳ Analytics pending 24-48h"
                        )
                        bottom_losers.append(
                            f'  * [LOSER #{idx}] Title: "{l_title}" | Scene 1 Hook: "{l_hook}" '
                            f'-> {l["views"]:,} views | {l_ret} '
                            f'(HIGH SWIPE-AWAY — DO NOT REPEAT THIS SLOW/VAGUE HOOK PATTERN!)'
                        )

            if target_ids:
                unique_ids = list(dict.fromkeys(target_ids))
                with get_db() as db:
                    rec_pub = (
                        db.query(PublishedVideo)
                        .filter(PublishedVideo.channel_id.in_(unique_ids))
                        .order_by(PublishedVideo.published_at.desc())
                        .limit(10)
                        .all()
                    )
                    for rp in rec_pub:
                        if rp.title and rp.title.strip() not in recent_titles:
                            recent_titles.append(rp.title.strip())

                    rec_jobs = (
                        db.query(VideoJob)
                        .filter(
                            VideoJob.target_channel_id.in_(unique_ids),
                            VideoJob.youtube_video_id.isnot(None),
                            VideoJob.youtube_video_id != "",
                        )
                        .order_by(VideoJob.created_at.desc())
                        .limit(10)
                        .all()
                    )
                    for rj in rec_jobs:
                        if rj.title and rj.title.strip() not in recent_titles:
                            recent_titles.append(rj.title.strip())

            # Closed-Loop Live YouTube Winner DNA Sync: Nếu DB cục bộ chưa cập nhật đủ top_winners,
            # tự động đồng bộ từ thống kê kênh YouTube thực tế (có bộ nhớ đệm TTL 30 phút chống chậm pipeline)
            if not top_winners and matched_handle:
                import time as _time
                cache_attr = "_live_yt_winner_cache"
                cache_store = getattr(self.__class__, cache_attr, None)
                if cache_store is None:
                    cache_store = {}
                    setattr(self.__class__, cache_attr, cache_store)
                now_ts = _time.time()
                cached_entry = cache_store.get(matched_handle)
                if not cached_entry or (now_ts - cached_entry.get("ts", 0)) > 1800:
                    try:
                        from app.services.youtube_analytics import fetch_channel_live_stats
                        live_res = fetch_channel_live_stats(matched_handle)
                        if live_res.get("success") and live_res.get("videos"):
                            cache_store[matched_handle] = {"ts": now_ts, "videos": live_res["videos"]}
                            cached_entry = cache_store[matched_handle]
                    except Exception:
                        pass
                if cached_entry and cached_entry.get("videos"):
                    for v_info in cached_entry["videos"][:3]:
                        v_title = str(v_info.get("title") or "").strip()
                        v_views = int(v_info.get("views_num") or 0)
                        if v_title and v_views >= 20 and not is_off_niche_video_title(v_title, niche_low):
                            top_winners.append(f'  * "{v_title}" ({v_views:,} live YouTube views)')
                    for v_info in list(reversed(cached_entry["videos"]))[:3]:
                        v_title = str(v_info.get("title") or "").strip()
                        v_views = int(v_info.get("views_num") or 0)
                        if v_title:
                            bottom_losers.append(f'  * "{v_title}" ({v_views:,} views — High Swipe-Away Loser)')
                    for v_info in cached_entry["videos"][:8]:
                        v_title = str(v_info.get("title") or "").strip()
                        if v_title and v_title not in recent_titles:
                            recent_titles.append(v_title)

            if len(recent_titles) >= 2:
                saturated_terms = extract_saturated_channel_concepts(recent_titles, min_frequency=2)
                try:
                    from app.services.channel_intelligence import extract_lead_subject_stems
                    recent_lead_stems: List[str] = []
                    for rt in recent_titles[:3]:
                        for st in extract_lead_subject_stems(rt):
                            if st not in recent_lead_stems:
                                recent_lead_stems.append(st)
                    for st in reversed(recent_lead_stems):
                        if st not in saturated_terms:
                            saturated_terms.insert(0, st)
                except Exception:
                    pass
                setattr(self, "_last_saturated_terms", list(saturated_terms[:8]))
        except Exception as ex_mem:
            logger.debug(f"[ContentAgent] Không thể đọc episodic memory cho {channel_niche}: {ex_mem}")

        memory_sections = [
            "═══════════════════════════════════════════════════════════",
            f"🧠 WEEKLY REAL-DATA ANALYTICS LOOP ({week_label or 'LIVE'}) — CHANNEL SWIPE-AWAY BASELINE: {channel_avg_swipe:.1f}%:",
        ]
        if top_winners:
            memory_sections.append(
                "🏆 TOP 3 WINNING VIDEOS OF THIS CHANNEL (High Stayed-to-Watch % & High Completion APV % — Replicate their direct confrontation & concrete physical numbers with a FRESH topic):\n"
                + "\n".join(top_winners[:3])
            )
        if bottom_losers:
            memory_sections.append(
                "💀 BOTTOM 3 LOSING VIDEOS OF THIS CHANNEL (High Swipe-Away % & Low Retention — STRICTLY AVOID their slow setup, generic phrasing, or passive opening hooks):\n"
                + "\n".join(bottom_losers[:3])
            )
        memory_sections.append(
            """🔥 MANDATORY CEILING-BREAKER ARCHITECTURE (APPLY STRICTLY TO ALL EN & pt-BR CHANNELS):
1. GOLDEN SEPARATION OF DUTY: `TITLE` (YT_SEARCH ENGINE) VS. `SCENE 1 HOOK 0.0s` (RETENTION LOCK):
   - `title`: MUST explicitly name high-search-volume entities + extreme physical metric ("Lula-Colossal vs Cachalote: O Duelo a 2.000m 🦑🐋", "Onça-Pintada vs Jacaré: A Mordida de 1.500 PSI 🐆🐊", "Green Anaconda vs. Black Caiman: 1,500 PSI Duel"). NEVER hide the entity in the title!
   - `Scene 1 (0.0s–2.2s)`: NEVER repeat the title verbatim or spoil the final metric at 0.0s like a dry textbook! Save the hard number (PSI, meters, dB, °C) for Act 2 (Scenes 2–3) and Act 3 (Scene 4 Twist).
2. OPEN SCENE 1 (6–10 WORDS MAX) USING 1 OF 4 PSYCHOLOGICAL-VISUAL HOOK ARCHETYPES:
   - Archetype 1 (Visual Micro-Observation / Camouflage Pointer): "Olhe bem para essas marcas redondas na pele desse cachalote." / "What looks like a dead tree branch in this river just opened its eyes."
   - Archetype 2 (The Fatal Mistake / Hunter Becomes Hunted): "O último erro desse jacaré foi subir nesse barranco sem olhar para cima." / "This caiman's last mistake was climbing onto this riverbank alone."
   - Archetype 3 (2nd-Person Survival Simulation): "Se um submarino tocar nessa linha branca no fundo do mar, ele não volta." / "If someone touches their neck while apologizing, watch their eyes next."
   - Archetype 4 (The Illusion Breaker / Role Reversal): "Seis leões famintos cercaram esse pequeno animal, mas os leões estão fugindo." / "Six starving lions surrounded this tiny animal, and the lions are running away."
   - ZERO passive introductions, ZERO rhetorical or Yes/No questions ("Did you know...", "Você sabia...")."""
        )
        if recent_titles:
            memory_sections.append(
                "🚫 RECENTLY PRODUCED VIDEOS ON THIS CHANNEL (DO NOT repeat these exact titles, opening hook sentences, or examples):\n"
                + "\n".join(f'  - "{t}"' for t in recent_titles[:8])
            )
        if saturated_terms:
            memory_sections.append(
                f"⚠️ SATURATED & RECENTLY FEATURED LEAD SUBJECTS (COOLDOWN ACTIVE): {', '.join(saturated_terms[:8])}\n"
                "🛡️ MANDATORY 1-1 VISUAL CONTRAST & LEAD-SUBJECT ALTERNATION RULE (BREAKING THE ~1,000-VIEW SEED COHORT CEILING):\n"
                "   - Empirical YouTube Analytics v2 telemetry proves that starting consecutive videos with the same primary creature/entity word in the Title & Scene 1 (e.g., 4 consecutive 'Tubarão' videos or repeating the exact same opening color palette) causes Audience Cohort Fatigue (Stayed-to-Watch drops from ~48.5% to ~32.8%, locking the video at ~1,000 views).\n"
                "   - NEVER start your `title` or `Scene 1 Hook` with any of the saturated/recent lead subjects listed above! If the input topic mentions both an extreme physical/geological phenomenon AND a recently featured creature (or Entity A vs Entity B), ALWAYS place the FRESH, non-repeated phenomenon/opponent FIRST in the Title and open Scene 1 with a contrasting visual color palette!"
            )

        # Mini-RAG Semantic Script & Visual Metadata Memory (tránh lặp nội dung kịch bản + từ khóa hình ảnh cũ)
        if canon_cid:
            try:
                from app.services.video_memory_rag import get_video_memory_rag
                rag_ctx = get_video_memory_rag().build_rag_prompt_context(canon_cid, topic, top_k=4)
                if rag_ctx:
                    memory_sections.append(rag_ctx)
            except Exception as ex_rag:
                logger.debug(f"[ContentAgent] Mini-RAG context warning: {ex_rag}")

        try:
            from app.services.niche_manager import get_niche
            n_prof = get_niche(channel_niche)
            topic_bank_2026 = (n_prof.get("script_blueprint") or {}).get("viral_topic_bank_2026") or []
            if topic_bank_2026:
                memory_sections.append(
                    "🔥 2026 MILLION-VIEW COMPETITOR BENCHMARK HOOKS FOR THIS NICHE:\n"
                    + "\n".join(f'  * "{tb}"' for tb in topic_bank_2026[:4])
                )
        except Exception:
            pass

        memory_sections.append(
            f"""🌐 ZERO-SHOT MULTI-NICHE COGNITIVE ADAPTATION (Niche: '{channel_niche}' | Topic: '{topic}'):
- Even when scaling to brand-new channels or custom sub-niches, autonomously deduce the target audience's #1 psychological trigger (e.g. Fear of Deception, Awe of Unseen Scale, Empathy & Moral Justice, Survival Instinct, or Forbidden Status/Wealth).
- SCENE-LEVEL AUDIO-VISUAL DIRECTION (MANDATORY FOR EVERY SCENE):
  1. `highlight_words`: Pick 1-2 most emotionally or factually explosive words/numbers directly from that scene's `narration` (e.g. ["BLIND", "0.2"], ["4,000", "CRUSHED"], ["BETRAYED"]). Our subtitle engine will render these exact words in glowing Neon Cyan/Yellow and trigger synchronized micro-SFX foley!
  2. `overlay_badge`: Provide a clean, NO-EMOJI uppercase broadcast HUD stamp (2-4 words in brackets, e.g. "[ // HIDDEN SIGN #1 // ]", "[ // 12-SECOND TWIST // ]", "[ // FATAL MISTAKE // ]") on Scene 1 and Scene 4/5 (the 12-second Mid-Video Re-Hook) to reset viewer attention. Set `null` on other scenes.
  3. `camera_motion`: Dynamically vary camera movement across consecutive scenes (`"zoom_in_fast"` for Scene 1 Hook & climax, alternating `"ken_burns_zoom_out"`, `"pan_right"`, `"ken_burns_zoom_in"`, `"pan_left"`).
  4. `sfx_cue`: Choose the exact sound design cue matching the scene's emotion (`"impact_boom"`, `"whoosh_heavy"`, `"riser_tension"`, `"heartbeat_thump"`, `"glitch"`, `"reveal"`)."""
        )
        memory_sections.append("═══════════════════════════════════════════════════════════")
        return "\n".join(memory_sections)

    @trace_custom_span("ContentAgent.brainstorm_deep_viral_blueprint", run_type="llm")
    def _brainstorm_deep_viral_blueprint(
        self,
        topic: str,
        channel_niche: str,
        language: str,
        target_lang_display: str,
        channel_memory_block: str,
        director_notes: Optional[str] = None,
    ) -> str:
        """
        STAGE 1 (Deep Showrunner Research & 3-Angle Viral Tournament LLM Pass):
        Đầu tư thêm 1 lượt gọi LLM chuyên sâu để đào sâu thông số vật lý/sinh học/lịch sử/tâm lý thật,
        đặt lên bàn cân 3 góc tiếp cận Viral khác nhau (1v1 Clash vs Paradox Twist vs Behavioral Glitch),
        chọn ra Blueprint thắng cuộc phân tách rạch ròi giữa `winning_title` (YT_SEARCH) và `scene_1_hook_0s` (4 Đòn Tâm Lý Thị Giác).
        """
        notes_ctx = f'\nCreator Direction: "{director_notes.strip()}"' if (director_notes and len(director_notes.strip()) > 3) else ""
        blueprint_prompt = f"""You are an Elite YouTube Shorts Showrunner, Documentary Fact Researcher, and Viral Retention Architect for niche '{channel_niche}'.
Input Topic: "{topic}"
Target Spoken Language: {language} ({target_lang_display}){notes_ctx}

{channel_memory_block[:2500]}

YOUR MISSION (DEEP PRE-SCRIPT RESEARCH & 3-ANGLE TOURNAMENT):
Do NOT write the full script yet. Spend your full reasoning capacity to deeply understand the user's intent, mine the most jaw-dropping verifiable truths about "{topic}", and pit 3 distinct viral angles against each other before selecting the #1 Winning Blueprint:
0. DEEP USER INTENT & SUBTEXT COMPREHENSION (ZERO KEYWORD DEPENDENCE):
   - Analyze what the user truly wants to convey in "{topic}" (and Creator Direction if provided), even if the input is brief, conversational, or a raw script idea.
   - Deduce the hidden curiosity gap, emotional core, canonical `primary_entity`, and authentic physical environment/era of the story.
1. DEEP FACT & METRIC MINING:
   - Extract 3 concrete, verifiable numbers/mechanisms (e.g. exact PSI bite force, °C temperature, ocean depth in meters, decibels, reaction speed in seconds/milliseconds, historical year/casualty count, or exact neurological/behavioral reflex).
2. 3-ANGLE VIRAL TOURNAMENT:
   - Angle A (Asymmetric 1v1 Clash / Direct Confrontation): Frame around "Entity A vs Entity B" with an extreme physical metric.
   - Angle B (Counter-Intuitive Paradox / Hidden Lethal Mechanism): Expose why what 99% of people believe about this topic is dead wrong.
   - Angle C (Visceral 0.0s Survival / Micro-Behavior Test): Immediate high-stakes action or observable real-life trap.
3. SELECT THE #1 WINNING BLUEPRINT FOR >70% STAYED & 100%+ APV (SEPARATE TITLE VS. SCENE 1 HOOK):
   - `winning_title`: Search-optimized High-CTR Title in {target_lang_display} explicitly naming the primary entities + concrete metric (`[Entity A] vs [Entity B]: [Extreme Metric]`).
   - BROAD-APPEAL ENTITY TRANSLATION IN TITLE (NO OBSCURE JARGON HEADERS): If the input topic uses an obscure scientific, geological, or academic psychology term that 90% of broad viewers do not instantly recognize (e.g., "Brinicle", "Pratfall Effect", "Zeigarnik Effect"), NEVER start `winning_title` with that obscure jargon word! Translate it into an instantly recognizable mass-appeal entity or physical confrontation (e.g., instead of "The Antarctic Brinicle", write "Underwater Ice Tornado vs Sea Life: -2°C Death Trap"; instead of "The Pratfall Effect", write "Why One Clumsy Mistake Makes People Trust You Instantly"). Save the technical term for Act 2 (Scene 2-3)!
   - `selected_hook_archetype`: Choose the #1 best psychological-visual hook archetype for Scene 1 (`1. Visual Micro-Observation ("Look closely at...")`, `2. The Fatal Mistake ("O último erro...")`, `3. 2nd-Person Survival Simulation ("If you ever...")`, or `4. The Illusion Breaker / Role Reversal`).
   - `scene_1_hook_0s`: 6–10 words in {target_lang_display} using `selected_hook_archetype`. CRITICAL: NEVER repeat `winning_title` verbatim or spoil the final metric/winner in `scene_1_hook_0s`! Lock the viewer's eyes onto a strange visual detail, fatal mistake, or survival warning at 0.0s, and save the hard metrics for `act_2_concrete_proof` (Scenes 2–3) and `scene_4_mid_video_15s_twist` (Scene 4)!
   - `counterfactual_debate_comment`: 12–22 words in {target_lang_display}. Design an unresolved Counterfactual Twist / Escalated 2v1 Odds / Next-Opponent 50-50 Debate Question ending with `(1) ... or (2) ... 👇` that was NOT already answered inside the video (ZERO 'Subscribe/Inscreva-se' spam!).

Return ONLY valid JSON:
{{
  "mined_hard_metrics": ["metric/fact 1 with exact number", "metric/fact 2 with exact number", "metric/fact 3 with exact number"],
  "angle_a_clash": "1-sentence pitch for Angle A",
  "angle_b_paradox": "1-sentence pitch for Angle B",
  "angle_c_visceral": "1-sentence pitch for Angle C",
  "winning_angle": "Angle A | Angle B | Angle C — plus 1-sentence reason why it maximizes Stayed-to-Watch % and APV %",
  "primary_entity": "Bare 1-2 word English name of the primary subject/entity",
  "winning_title": "High-CTR Searchable Title in {target_lang_display} (28-55 chars naming exact entities + concrete number or 'A vs B' + emojis)",
  "selected_hook_archetype": "1. Visual Micro-Observation | 2. The Fatal Mistake | 3. 2nd-Person Survival Simulation | 4. The Illusion Breaker",
  "scene_1_hook_0s": "6-10 word psychological-visual hook in {target_lang_display} using selected_hook_archetype (NEVER repeats winning_title verbatim or spoils the final metric at 0.0s; NO '?' questions)",
  "act_2_concrete_proof": "Specific real-world example, quote, or documented event + first hard metric for Scenes 2-3",
  "scene_4_mid_video_15s_twist": "Explosive 15-second Plot Twist / Second Hook for Scene 4 that flips expectations with a 2nd concrete metric",
  "act_4_ultimate_payoff": "Decisive biomechanical outcome, scientific revelation, or surgical counter-move for Scenes 6-7",
  "scene_8_infinite_echo_loop": "Exact conceptual bridge connecting the end of Scene 8 directly back into Scene 1's opening hook",
  "counterfactual_debate_comment": "12-22 word Counterfactual 50/50 Debate Question in {target_lang_display} (e.g. 2v1 escalation, terrain flip, or next opponent) ending with (1) or (2) 👇 and ZERO Subscribe promo",
  "hot_yt_shorts_peak_queries": [
    "concrete English query 1 for 0s peak action YouTube Short (3-5 words)",
    "concrete English query 2 for close-up strike/clash YouTube Short (3-5 words)",
    "concrete English query 3 with shorts keyword (3-5 words)",
    "concrete English query 4 for Scene 4 mid-video climax peak (3-5 words)",
    "concrete English query 5 for secondary reaction/angle beat (3-5 words)"
  ]
}}"""
        try:
            raw_bp = self._call_api(blueprint_prompt, json_mode=True, timeout=22, max_total_seconds=38)
            start = raw_bp.find("{")
            end = raw_bp.rfind("}")
            if start != -1 and end != -1 and end > start:
                bp_data = json.loads(raw_bp[start : end + 1])
                logger.info(
                    f"🧠 [ContentAgent Stage-1 Deep Blueprint] Winning Angle: {bp_data.get('winning_angle', '')[:80]} | "
                    f"Archetype: {bp_data.get('selected_hook_archetype', '')} | "
                    f"Hook 0.0s: '{bp_data.get('scene_1_hook_0s', '')}' | "
                    f"15s Twist: '{str(bp_data.get('scene_4_mid_video_15s_twist', ''))[:70]}'"
                )
                return (
                    "\n═══════════════════════════════════════════════════════════\n"
                    "🔥 STAGE-1 SHOWRUNNER WINNING VIRAL BLUEPRINT (MANDATORY EXECUTION):\n"
                    f"- Mined Verifiable Metrics: {json.dumps(bp_data.get('mined_hard_metrics', []), ensure_ascii=False)}\n"
                    f"- Winning Angle: {bp_data.get('winning_angle', '')}\n"
                    f"- Primary Entity: {bp_data.get('primary_entity', '')}\n"
                    f"- Recommended High-CTR Searchable Title: {bp_data.get('winning_title', '')}\n"
                    f"- Selected Psychological-Visual Hook Archetype: {bp_data.get('selected_hook_archetype', '')}\n"
                    f"- Mandatory Scene 1 Hook (0.0s — NEVER repeat Title verbatim): {bp_data.get('scene_1_hook_0s', '')}\n"
                    f"- Mandatory Act 2 Proof + First Hard Metric (Scenes 2-3): {bp_data.get('act_2_concrete_proof', '')}\n"
                    f"- Mandatory Scene 4 Mid-Video 15s Reversal Twist + Second Metric: {bp_data.get('scene_4_mid_video_15s_twist', '')}\n"
                    f"- Mandatory Act 4 Payoff (Scenes 6-7): {bp_data.get('act_4_ultimate_payoff', '')}\n"
                    f"- Mandatory Scene 8 Infinite Echo-Loop Bridge: {bp_data.get('scene_8_infinite_echo_loop', '')}\n"
                    f"- Mandatory Counterfactual 50/50 Debate Comment (pinned_comment): {bp_data.get('counterfactual_debate_comment', '')}\n"
                    f"- Hot YouTube Shorts Peak Search Queries: {json.dumps(bp_data.get('hot_yt_shorts_peak_queries', []), ensure_ascii=False)}\n"
                    "═══════════════════════════════════════════════════════════\n"
                )
        except Exception as e_bp:
            logger.debug(f"[ContentAgent] Stage-1 Deep Blueprint fallback to single-pass: {e_bp}")
        return ""

    @trace_custom_span("ContentAgent.showrunner_retention_polish", run_type="llm")
    def _polish_script_for_maximum_retention(
        self,
        draft_script: ScriptContract,
        topic: str,
        channel_niche: str,
        language: str,
        target_lang_display: str,
        deep_blueprint_block: str = "",
        director_notes: Optional[str] = None,
    ) -> Optional[ScriptContract]:
        """
        STAGE 3 (Executive Showrunner Retention Doctor & Hot Peak Query Distiller LLM Pass):
        Đầu tư thêm 1 lượt gọi LLM độc lập sau khi có bản nháp để chuốt sắc từng câu chữ:
        - Đảm bảo Tiêu đề (`title`) có tên thực thể + con số cực hạn để ăn tìm kiếm `YT_SEARCH`.
        - Đảm bảo Cảnh 1 (0.0s Hook) dùng 1 trong 4 Đòn Tâm Lý Thị Giác (6-10 từ), KHÔNG đọc lặp lại Tiêu đề và KHÔNG tiết lộ luôn kết quả ở giây 0.0s.
        - Đảm bảo Cảnh 2-3 và Cảnh 4 (15s Mid-Video Climax) tung ra các thông số vật lý/cú bẻ lái (Plot Twist) chống tụt APV.
        - Nối mạch nhân quả ("Nhưng / Chính vì vậy / Cho đến khi") giữa 8 phân cảnh và khóa vòng lặp vô tận ở Cảnh 8.
        - Chắt lọc 5 truy vấn tiếng Anh cực chuẩn (`hook_search_queries`) để săn cảnh Peak trên YouTube Shorts.
        """
        lang_instruction = build_language_integrity_instruction(language)
        notes_ctx = f'\nCREATOR NOTES: "{director_notes.strip()}"' if (director_notes and len(director_notes.strip()) > 3) else ""
        polish_prompt = f"""You are an Executive YouTube Shorts Showrunner, Retention Doctor (100%+ APV Specialist), and Peak Visual Director.
Review and upgrade the following draft script for Topic: "{topic}" (Niche: '{channel_niche}', Language: {language} / {target_lang_display}).
{lang_instruction}{notes_ctx}
{deep_blueprint_block}

CURRENT DRAFT SCRIPT JSON:
{draft_script.model_dump_json(indent=2)}

YOUR EXECUTIVE PUNCH-UP MANDATE (UPGRADE EVERY WEAK LINK WHILE PRESERVING 7-8 SCENES & 92-118 TOTAL WORDS):
1. GOLDEN SEPARATION BETWEEN `TITLE` VS. `SCENE 1 HOOK` (0.0s–2.2s | 6 TO 10 WORDS):
   - Keep `title` razor-sharp for `YT_SEARCH` + CTR (explicitly naming the primary entities + concrete metric/confrontation).
   - Check Scene 1 (`scenes[0].narration` and `hook`): If Scene 1 repeats the `title` verbatim or sounds like a dry textbook reading a number at 0.0s, REWRITE Scene 1 into one of the **4 Psychological-Visual Hook Archetypes** (*1. Visual Micro-Observation "Look closely at..." / "Olhe bem para..."*, *2. The Fatal Mistake "O último erro..." / "...last mistake was..."*, *3. 2nd-Person Survival Simulation "If you..." / "Se você..."*, or *4. The Illusion Breaker / Role Reversal*) while shifting the hard numerical metric into Scenes 2–3! NEVER use '?' questions in Scene 1.
2. CAUSAL STORYTELLING FLOW ("BUT / THEREFORE" TRANSITIONS):
   - Ensure every scene flows into the next with high-tension causal connectors rather than flat textbook listing. Every scene MUST be 100% grammatically complete (11–16 words per body scene).
3. SCENE 4 MID-VIDEO CLIMAX TWIST (14.0s–20.0s | ANTI-DROP-OFF ENGINE):
   - Ensure Scene 4 (`climax_scene_id`) delivers an explosive SECOND HOOK / REVERSAL TWIST backed by a second concrete metric/mechanism, and has a sharp uppercase HUD `overlay_badge` (e.g. `"[ // THE 15-SECOND TWIST // ]"`).
4. SCENE 7/8 INFINITE ECHO-LOOP FINALE (APV > 100%):
   - Craft the final scene's closing sentence so its thought/paradox bridges directly back into Scene 1's opening hook. ZERO spoken Subscribe/Follow/Comment CTA!
5. DISTILL 5 HOT YOUTUBE SHORTS PEAK SEARCH QUERIES (`hook_search_queries`):
   - Based on the hottest physical climax moments in this script, provide 5 distinct, concrete English search queries (3–5 words each, subject + explosive peak action/setting) optimized for finding viral YouTube Shorts peak clips.
6. SELF-CONTAINED, PRONOUN-FREE VISUAL QUERIES (`visual_keyword`, `stock_query`, `visual_prompt`):
   - Every scene's `visual_keyword` and `stock_query` MUST be 100% self-contained when isolated from the script: always include the resolved protagonist/entity name + concrete physical action/environment (NEVER leave pronouns like "it", "he", "they" or literal metaphor props like "hydraulic press", "silent cage", "loaded weapon" in visual fields!).
7. COUNTERFACTUAL 50/50 DEBATE COMMENT (`pinned_comment` — ZERO SUBSCRIBE SPAM & NEVER ASK WHAT THE VIDEO ALREADY ANSWERED):
   - If `pinned_comment` asks a question the video already answered (e.g. "Who wins: A or B?") or includes robotic "SUBSCRIBE / INSCREVA-SE" spam, REWRITE it into a punchy 12–22 word Counterfactual Escalation / Next-Opponent 50/50 Debate Question ending with `(1) ... or (2) ... 👇`!

Return ONLY the complete upgraded JSON matching the exact same schema as CURRENT DRAFT SCRIPT JSON."""
        try:
            raw_polished = self._call_api(polish_prompt, json_mode=False, timeout=28, max_total_seconds=45)
            polished_contract = self._parse_json_script(
                raw_polished,
                channel_niche=channel_niche,
                topic=topic,
            )
            if polished_contract and len(polished_contract.scenes) >= 6:
                logger.info(
                    f"✨ [ContentAgent Stage-3 Showrunner Polish] Đã chuốt sắc kịch bản: "
                    f"Title='{polished_contract.title}' | Hook='{polished_contract.hook}' | "
                    f"PeakQueries={polished_contract.hook_search_queries}"
                )
                return polished_contract
        except Exception as e_pol:
            logger.debug(f"[ContentAgent] Stage-3 Showrunner Polish skip: {e_pol}")
        return None

    def generate_script(
        self,
        topic: str,
        channel_niche: str = "general",
        language: str = "en",
        director_notes: Optional[str] = None,
        cooldown_entities: Optional[List[str]] = None,
        min_acceptable_score: Optional[float] = None,
        video_format: str = "short",
        **kwargs
    ) -> ScriptContract:
        """
        Sinh kịch bản video theo chế độ định dạng (`video_format`):
        - `"short"` (mặc định): YouTube Shorts dọc 9:16 (7-8 phân cảnh, 92-118 từ, ~34-42 giây).
        - `"long"`: YouTube Phim Tài Liệu Ngang 16:9 (5 Chương / 16-22 phân cảnh, 360-560 từ, ~3-4.5 phút).
        """
        vf = str(video_format or kwargs.get("video_format") or "short").strip().lower()
        is_long_form = vf in ("long", "long_form", "long-form", "16:9", "landscape", "horizontal", "documentary")
        vf = "long" if is_long_form else "short"

        director_block = ""
        if director_notes and len(director_notes.strip()) > 3:
            director_block = f"""
═══════════════════════════════════════════════════════════
🎯 CREATOR'S CUSTOM CREATIVE DIRECTION (HIGHEST PRIORITY):
The creator provided the following specific guidance for this video:
"{director_notes.strip()}"
You MUST strictly align the angle, narrative tone, character focus, and visual atmosphere with these instructions!
═══════════════════════════════════════════════════════════
"""

        from app.services.niche_manager import get_niche, build_script_prompt_instructions
        niche_profile = get_niche(channel_niche)
        niche_instructions = build_script_prompt_instructions(niche_profile, language=language)
        lang_instruction = build_language_integrity_instruction(language)
        regional_hook_instruction = build_regional_hook_mastery_instruction(language, channel_niche=channel_niche)
        channel_memory_block = self._build_channel_context_memory(
            channel_niche=channel_niche,
            topic=topic,
            channel_id=kwargs.get("channel_id"),
        )
        lang_clean = (language or "en").strip().lower()
        if lang_clean.startswith("vi"):
            target_lang_display = "TIẾNG VIỆT"
        elif lang_clean.startswith("pt"):
            target_lang_display = "PORTUGUÊS BRASILEIRO"
        else:
            target_lang_display = "ENGLISH"

        deep_blueprint_block = ""
        if not kwargs.get("skip_deep_blueprint") and not is_long_form:
            deep_blueprint_block = self._brainstorm_deep_viral_blueprint(
                topic=topic,
                channel_niche=channel_niche,
                language=language,
                target_lang_display=target_lang_display,
                channel_memory_block=channel_memory_block,
                director_notes=director_notes,
            )

        if is_long_form:
            duration_spec = "3 TO 4.5 MINUTES CINEMATIC 16:9 WIDESCREEN DOCUMENTARY (16 TO 22 SCENES ACROSS 5 CHAPTERS, 360 TO 560 WORDS TOTAL — SCENE 1 COLD-OPEN HOOK 8-14 WORDS, BODY SCENES 18-26 WORDS EACH)"
            word_range_en = "360 to 560 words (3 to 4.5 minutes Widescreen 16:9 Documentary across 16 to 22 rich scenes; Scene 1 Cold-Open Hook MUST be 8-14 words; Body scenes MUST be 18-26 words each)"
        else:
            duration_spec = "34 TO 42 SECONDS GOLDEN HIGH-APV DURATION (7 TO 8 COMPLETE SCENES, 92 TO 118 WORDS TOTAL — SCENE 1 HOOK 6-10 WORDS USING VISUAL-PSYCHOLOGICAL POINTER IN FIRST 1.8 SECONDS, SMOOTH NARRATIVE FLOW)"
            word_range_en = "92 to 118 words (34-42s High-APV Golden Duration across 7-8 rich, complete scenes; Scene 1 Hook MUST be 6-10 words ~1.6-2.2s)"

        prompt = f"""You are an elite YouTube {'Widescreen 16:9 Documentary' if is_long_form else 'Shorts'} Master Showrunner, Behavioral Storyteller, and Viral Retention Architect for the channel '{niche_profile.get('name')}' (niche: '{channel_niche}').
Topic: '{topic}'
Target Language: {language} ({target_lang_display})
Video Format Mode: {'LONG-FORM 16:9 DOCUMENTARY (16-22 scenes, 360-560 words, ~3.5 minutes, NO #Shorts tag in title)' if is_long_form else 'VERTICAL 9:16 YOUTUBE SHORTS (7-8 scenes, 92-118 words, ~38 seconds)'}
{lang_instruction}
{regional_hook_instruction}
{director_block}
{channel_memory_block}
{deep_blueprint_block}
{niche_instructions}
═══════════════════════════════════════════════════════════
MASTER-CLASS YOUTUBE {'LONG-FORM 5-CHAPTER DOCUMENTARY (3–4.5 MINUTES | 360–560 WORDS | 16–22 SCENES)' if is_long_form else 'SHORTS ARCHITECTURE (34–42 SECONDS | 92–118 WORDS | MILLION-VIEW EMPIRICAL BLUEPRINT)'}
YouTube rewards TWO metrics simultaneously:
1. Viewed vs. Swiped-Away > 75%: Achieved by separating the role of `title` (explicitly naming high-search entities + extreme metric for `YT_SEARCH`) from `Scene 1 Hook` ({'8–14 words' if is_long_form else '6–10 words, ~1.6–2.2s'} using one of the 4 Psychological-Visual Hook Archetypes that locks the viewer's eyes without repeating the title verbatim).
2. High Retention + Smooth, Complete Narrative ({'360–560 words across 16–22 widescreen scenes, 18–26 words per body scene, 8–12s per scene' if is_long_form else '92–118 words, 34–42s across 7–8 scenes, 11–16 words per body scene'}):
   - NEVER write an abrupt, choppy, or truncated script ("cụt lủn")! Let the sentences flow naturally into one another with smooth transitions, clear context, and complete explanations.
   - {'For LONG-FORM 16:9 mode: Structure the 16–22 scenes across 5 gripping Documentary Chapters: Chapter 1 (Scenes 1–3: Cold Open & The Anomaly), Chapter 2 (Scenes 4–7: Historical/Scientific Origin & Real-World Evidence), Chapter 3 (Scenes 8–12: Deep Mechanism & The Hidden Twist), Chapter 4 (Scenes 13–17: High-Stakes Case Study / Apex Confrontation), Chapter 5 (Scenes 18–20: Ultimate Resolution & Thought-Provoking Finale). Put Chapter HUD badges like "[ // CHAPTER 1: THE ANOMALY // ]" in overlay_badge at the start of each chapter!' if is_long_form else 'Natural documentary sentence length per body scene (11 to 16 words per body scene) — prioritize natural storytelling rhythm and emotional depth over rigid word counting!'}
   - Every scene MUST be a 100% grammatically complete sentence. NEVER chop one sentence across two scenes!
═══════════════════════════════════════════════════════════
1. THE MANDATORY 5-ACT DUAL-PEAK STORYTELLING BLUEPRINT (target: {word_range_en}):
   - DURATION & SCENE COUNT: {duration_spec}.
   - UPSTREAM TOPIC & PREMISE ELEVATION (CRITICAL FIRST STEP):
     * Before writing Scene 1, if the input Topic is broad or lacks a number, you MUST autonomously anchor the story around a **real, verifiable extreme physical/biological/historical metric** (`1,500 PSI`, `93°C`, `2,000m`, `1883`, `0.2 seconds`, `4-second silence`, `230 dB`) AND a **visceral "A vs B" confrontation or counter-intuitive anomaly**.
   - YOU MUST EXECUTE ALL 5 ACTS SMOOTHLY IN ORDER across {'16 to 22 scenes (5 Documentary Chapters)' if is_long_form else '7 to 8 scenes'}:

     * ACT 1 — THE 1.8-SECOND PSYCHOLOGICAL-VISUAL HOOK (Scene 1 | 6 to 10 words MAX):
       - CRITICAL RULE: DO NOT repeat the `title` verbatim or spoil the final numerical outcome at 0.0s! Open *in media res* using 1 of the **4 Million-View Psychological-Visual Hook Archetypes**:
         1. Visual Micro-Observation / Camouflage Pointer ("Olhe bem para essas marcas redondas na pele desse cachalote.", "What looks like a dead tree branch in this river just opened its eyes.")
         2. The Fatal Mistake / Hunter Becomes Hunted ("O último erro desse jacaré foi subir nesse barranco sem olhar para cima.", "This caiman's last mistake was climbing onto this riverbank alone.")
         3. 2nd-Person Survival / Behavioral Simulation ("Se um submarino tocar nessa linha branca no fundo do mar, ele não volta.", "If someone touches their neck while apologizing, watch their eyes next.")
         4. The Illusion Breaker / Role Reversal ("Seis leões famintos cercaram esse pequeno animal, mas os leões estão fugindo.", "Six starving lions surrounded this tiny animal, and the lions are running away.")
       - FORBIDDEN: Verbatim title repetition, dry Wikipedia metric readouts at 0.0s, abstract poetic metaphors ("loaded weapon", "silent cage"), or passive "Did you know?" questions.

     * ACT 2 — NATURAL CONTEXT, CONCRETE PROOF & FIRST HARD METRIC (Scenes 2 – 3):
       - Smoothly build the context, reveal the **first concrete physical/scientific metric** (PSI, depth, temperature, year, reaction time), and show real-life proof:
         * For Psychology / Manipulation / Relationships / Stoicism: Describe the observable physical behavior or spoken trap ('Right after crossing your boundary, they smile and say, "You are overreacting again."').
         * For Mysteries / Deep Sea / History: Set the historical/scientific scene with the exact year, location, depth, or documented event.
         * For Wildlife / Animal Rescue: Build the physical tension of the encounter, exact biomechanical weapon/metric, or emotional turning point.

     * ACT 3 — THE 15-SECOND MID-VIDEO CLIMAX & REVERSAL TWIST (Scenes 4 – 5 | ANTI-DROP-OFF ENGINE):
       - CRITICAL FOR APV > 95%: Viewers swipe away at second 14–18 if Scenes 4–5 sound like a flat textbook lecture!
       - You MUST write Scene 4 (`climax_scene_id`) as a **SECOND EXPLOSIVE PEAK / PLOT TWIST** that flips the viewer's initial assumption ("But the real weapon isn't its teeth—it's a 230-decibel sonar shockwave.", "Yet when hydrophones listened deeper, the 93-degree water hid a lethal fault line.", "But FBI profilers ignore the smile and watch the feet freeze for two seconds.").
       - Introduce a **second shocking metric or hidden mechanism** (*Cavitation Shockwave*, *The Isolation Gambit*, *Thermal Fault Rupture*, *Cortisol Freeze*) and set `overlay_badge` on Scene 4 to a sharp uppercase HUD tag like `"[ // THE 15-SECOND TWIST // ]"` or `"[ // FATAL MECHANISM // ]"`.

     * ACT 4 — THE SURGICAL COUNTER-MOVE / ULTIMATE PAYOFF (Scenes 6 – 7):
       - Deliver the full climax payoff, resolution, or practical word-for-word Counter-Move that rewards the viewer for staying until the end:
         * For Psychology: Give the exact calm verbal/behavioral counter-move that disarms the trap on the spot.
         * For Wildlife / Rescue / Mysteries: Deliver the decisive biomechanical outcome, joyful transformation, or scientific revelation that resolves the twist.

     * ACT 5 — ECHO-LOOP FINALE & SEAMLESS INFINITE BRIDGE (Final Scene 7 or 8 -> Loops into Scene 1):
       - ZERO SPOKEN OUTRO CTA: NEVER say "Subscribe", "Follow", "Leave a comment", "Inscreva-se", or "Đăng ký kênh" in the narration!
       - Craft the final complete sentence so its final concept/paradox echoes and bridges directly back into the exact visual situation of Scene 1's opening hook (driving repeat views and `APV > 100%`).

2. HIGH-CTR SEARCHABLE TITLE VS. SCENE 1 HOOK RULES (LEARNED FROM 368 MILLION-VIEW SHORTS):
   - The first 1.5 - 2.2 seconds determines 100% of Shorts performance (Viewed vs Swiped away).
   - HIGH-CTR TITLE MANDATE (`YT_SEARCH` DISCOVERY ENGINE):
     * NEVER write bleak/depressing-only titles for Animal Rescue (FORBIDDEN: "Frozen Puppy Found in Rain"). ALWAYS build a transformation/recognition gap ("The Exact Moment This Stray Dog Realized He Was Safe 🥺❤️", "He Guarded This Drain For 72 Hours Until Rescuers Looked Inside 🐾").
     * NEVER write vague generic titles that hide the subject (FORBIDDEN: "Invisible Monsters Waiting To Strike", "That Fake Smile Exposes Their Envy", "Where Does This Endless River Go?"). ALWAYS name the specific entity/phenomenon + concrete number or "A vs B" clash ("Green Anaconda vs. Black Caiman: 1,500 PSI River Duel! 🐍🐊", "The 93°C Boiling Amazon River With Zero Volcanoes! 🔥🌊", "The 4-Second Eye Freeze That Exposes Fake Smiles 👁️🚩").
     * NEVER repeat saturated numbers like "11,000m" on Mysteries; use fresh, verifiable physical metrics and visceral sensory/fear hooks.
   - SCENE 1 HOOK ARCHETYPES (6–10 WORDS MAX — CHOOSE 1 OF 4 & NEVER REPEAT THE TITLE VERBATIM):
     * Archetype 1 (Visual Micro-Observation / Camouflage Pointer):
       Point directly at a strange detail, scar, or camouflaged threat on screen so the viewer's eyes lock onto the frame:
       - "Olhe bem para essas marcas redondas na pele desse cachalote."
       - "O que parece um tronco podre nessa água rasa acabou de abrir os olhos."
       - "Look closely at the circular scars on this whale's head."
       - "Watch this man's hands the second the detective asks about the car."
     * Archetype 2 (The Fatal Mistake / Hunter Becomes Hunted):
       Drop into the exact moment a predator, historical figure, or person makes a fatal miscalculation:
       - "O último erro desse jacaré foi subir nesse barranco sem olhar para cima."
       - "This caiman's last mistake was climbing onto this riverbank alone."
       - "One wrong turn by a single driver in 1914 triggered a world war."
     * Archetype 3 (2nd-Person Survival / Behavioral Simulation):
       Put the viewer inside the danger zone using "Se você..." / "If you..." / "When someone...":
       - "Se um submarino tocar nessa linha branca no fundo do mar, ele não volta."
       - "If someone touches their neck while apologizing, watch their eyes next."
     * Archetype 4 (The Illusion Breaker / Role Reversal):
       Present a visual scene that defies common sense:
       - "Seis leões famintos cercaram esse pequeno animal, mas os leões estão fugindo."
       - "Six starving lions surrounded this tiny animal, and the lions are running away."
   - NEVER start with abstract literary metaphors, textbook definitions, greetings, or passive rhetorical questions ("Did you know...", "Welcome to...").
   - thumbnail_text MUST be 2 to 4 words in ALL CAPS with 1 high-contrast emoji (e.g. "PAUSE 4 SECONDS 🧠", "1,500 PSI BITE! 🐆", "FINALLY SAFE 🥺", "2,000M DUEL! 🌊").

3. SEAMLESS INFINITE LOOP ENDING (NO SPOKEN OUTRO CTA):
   - NEVER include "Subscribe", "Like", "Follow", "Leave a comment", "Inscreva-se", or "Đăng ký kênh" in ANY scene's spoken narration!
   - Every single scene's spoken narration MUST be a 100% grammatically complete sentence with proper closing punctuation. NEVER end any scene with dangling prepositions or conjunctions ('because', 'which', 'and', 'so', 'that', 'but', 'with').

4. CONTEXT-ANCHORED, SELF-CONTAINED VISUAL QUERIES (`visual_keyword` & `stock_query` — ZERO PRONOUNS OR METAPHOR PROPS):
   - Downstream visual agents search each scene independently, so every scene's `visual_keyword` (2–5 English words) and `stock_query` (2–4 English words) MUST be 100% self-contained: always resolve pronouns ("It", "He", "They") back to the main subject/entity (`primary_entity`) + concrete physical action or setting (e.g. `"honey badger fighting lion"`, `"man serious gaze dark noir"`, `"roman empire senate painting"`).
   - NEVER put literal metaphor words or inanimate comparisons (`hydraulic press`, `knife`, `cage`, `loaded weapon`) into `visual_keyword` or `stock_query` — always depict the actual protagonist and physical reality of the story!

5. CINEMATIC ATMOSPHERE & LIGHTING:
   - Choose a cohesive visual mood for `visual_prompt` (e.g. Dark Psychological Noir for psychology, Warm Golden Hour or Documentary Realism for history/wildlife, Deep Bioluminescent Blue for ocean mysteries).

6. TITLE & LANGUAGE INTEGRITY (100% STRICT ENFORCEMENT):
   - The entire video script (spoken narration in all scenes, hook, full_narration, title) MUST be 100% in {target_lang_display}.
   - Clean, punchy title under 60 characters with emojis.

7. NATURAL MEDIA SELECTION (`media_type` PER SCENE):
   - Choose `'video'`, `'photo'`, `'documentary'`, or `'ai_image'` naturally based on the topic.

8. COHERENT, FULL-BODIED STORYTELLING (NEVER ABRUPT OR TRUNCATED):
   - Make sure every idea is fully developed and explained before moving to the next point.
   - Connect scenes with smooth narrative flow so the entire {'360–560 word (16–22 scenes) widescreen documentary' if is_long_form else '85–120 word (7–8 scenes) script'} reads like a seamless, gripping documentary from start to finish.

9. HOOK VISUAL MODE DECISION (`hook_visual_mode`: ALWAYS `"video"`, `is_abstract_keyword`, & `climax_scene_id`):
   - ALWAYS set `"hook_visual_mode": "video"` and `"is_abstract_keyword": false` across ALL niches so Scene 1 (0.0s–3.0s) opens with high-retention real moving video footage (Dual-Beat Fast-Cut).
   - For psychological, behavioral, or counterfactual ("What If") topics, translate the opening hook into CONCRETE, OBSERVABLE PHYSICAL ACTIONS, MICRO-EXPRESSIONS, OR HISTORICAL SPECTACLES (e.g. `"man cold smirk dark room"`, `"intense eye contact interrogation"`, `"submarine underwater ocean"`, `"nuclear explosion mushroom cloud"`), NEVER abstract nouns.
   - Set `"climax_scene_id"` (integer between 2 and 7) and provide 2–5 concise English `"hook_search_queries"` (2–4 words each, bare canonical noun + physical action/setting, NO literary adjectives like 'colossal', 'abyssal', 'terrifying'; e.g. `"jaguar hunting caiman river"`, `"sperm whale diving deep ocean"`, `"honey badger fighting lion"`, `"man intense eye contact noir"`).

═══════════════════════════════════════════════════════════
OUTPUT — Return ONLY valid JSON, no markdown, no extra text:
═══════════════════════════════════════════════════════════
{{
  "video_format": "{vf}",
  "title": "Viral YouTube {'Documentary' if is_long_form else 'Shorts'} Title with Emojis (28-55 chars naming exact searchable entities + metric, NO 'Variant' prefix{', NO #Shorts tag' if is_long_form else ''})",
  "title_variants": [
    "Alternative High-CTR Entity A vs Entity B + Metric Title with Emoji (28-55 chars, NO 'Variant' prefix)",
    "Alternative Direct Warning / Numbered Title with Emoji (28-55 chars, NO 'Variant' prefix)",
    "Alternative Extreme Contrast / Counter-Move Title with Emoji (28-55 chars, NO 'Variant' prefix)"
  ],
  "primary_entity": "Bare canonical 1-2 word name of main person/species/creature WITHOUT descriptive adjectives (e.g. 'Colossal Squid', 'Honey Badger', 'Julius Caesar') or null for psychology",
  "hook": "The opening 6-10 word psychological-visual hook sentence (NEVER repeats title verbatim or uses '?' questions)",
  "hook_variants": [
    "Archetype 1 (Visual Micro-Observation) hook sentence (6-10 words)",
    "Archetype 2 (The Fatal Mistake) hook sentence (6-10 words)",
    "Archetype 3/4 (2nd-Person Survival or Illusion Breaker) hook sentence (6-10 words)"
  ],
  "hook_visual_mode": "video | scene",
  "is_abstract_keyword": false,
  "climax_scene_id": {8 if is_long_form else 3},
  "hook_search_queries": ["bare noun + action/format (2-4 words)", "bare noun + angle (2-4 words)"],
  "full_narration": "Complete, smooth, well-developed spoken story without stage directions ({'360-560 words across 16-22 scenes (~3.5 min 5-Chapter Documentary)' if is_long_form else '92-118 words, 34-42s High-APV 5-Act story'} with Real-Life Example, Mechanism, Counter-Move/Climax, and zero spoken Subscribe CTA)",
  "thumbnail_text": "2-4 BOLD ALL-CAPS CURIOSITY WORDS IN {target_lang_display} FOR THUMBNAIL",
  "scenes": [
    {{
      "scene_id": 1,
      "visual_reasoning": "Narrative and visual intent of this scene",
      "narration": "Complete, natural spoken sentence for this scene ({'Scene 1: 8-14 words; Body scenes: 18-26 words flowing naturally, 16 to 22 scenes total' if is_long_form else 'Scene 1: 6-10 words using 1 of the 4 Psychological-Visual Hook Archetypes without repeating title; Body scenes: 11-16 words flowing naturally, 7 to 8 scenes total'})",
      "visual_keyword": "simple relevant search query for the subject/theme (2-5 English words)",
      "visual_prompt": "Cinematic {'16:9 widescreen ' if is_long_form else ''}visual description fitting the overall story mood",
      "stock_query": "simple subject/mood query for stock video (2-4 English words)",
      "media_type": "video | ai_image | photo | documentary",
      "camera_motion": "zoom_in_fast | ken_burns_zoom_out | pan_right | ken_burns_zoom_in | pan_left",
      "sfx_cue": "impact_boom | whoosh_heavy | riser_tension | heartbeat_thump | glitch | reveal",
      "highlight_words": ["WORD1", "WORD2"],
      "overlay_badge": "[ // THE REAL TRAP // ] or null",
      "estimated_seconds": {8.5 if is_long_form else 4.0}
    }}
  ],
  "tags": {json.dumps(["documentary", "viral"] if is_long_form else ["shorts", "viral"])},
  "description": "Engaging description with hashtags",
  "pinned_comment": "12-22 word Counterfactual 50/50 Debate Question in {target_lang_display} (e.g. 'What if it were 2v1?', 'Next matchup: X vs Y', or a sharp personal dilemma ending with '(1) ... or (2) ... 👇' — NEVER ask a question the video already answered and NEVER include 'Subscribe/Inscreva-se' spam!)",
  "viral_formula": "5-Act Full Storytelling | Smooth Transitions | Surgical Counter-Move | Seamless Loop"
}}
"""
        from app.services.content_linter import ScriptValidationLinter
        if cooldown_entities:
            topic_low = (topic or "").lower()
            cooldown_entities = [ce for ce in cooldown_entities if str(ce).lower() not in topic_low]

        max_attempts = 3
        current_prompt = prompt
        last_script: Optional[ScriptContract] = None
        last_issues: List[str] = []

        for attempt in range(1, max_attempts + 1):
            try:
                raw_text = self._call_api(current_prompt, json_mode=False, timeout=45 if is_long_form else 30)
                script = self._parse_json_script(raw_text, channel_niche=channel_niche, topic=topic, video_format=vf)
                last_script = script

                linter_res = ScriptValidationLinter.lint(
                    script=script,
                    niche_profile=niche_profile,
                    cooldown_entities=cooldown_entities,
                    target_wpm=niche_profile.get("target_wpm", 170)
                )

                if linter_res.is_valid:
                    logger.info(f"✅ [ContentAgent] Script ({vf}) passed Quality Gate Linter on attempt {attempt}/{max_attempts}")
                    if not kwargs.get("skip_deep_polish") and not is_long_form:
                        polished = self._polish_script_for_maximum_retention(
                            draft_script=script,
                            topic=topic,
                            channel_niche=channel_niche,
                            language=language,
                            target_lang_display=target_lang_display,
                            deep_blueprint_block=deep_blueprint_block,
                            director_notes=director_notes,
                        )
                        if polished is not None:
                            pol_lint = ScriptValidationLinter.lint(
                                script=polished,
                                niche_profile=niche_profile,
                                cooldown_entities=cooldown_entities,
                                target_wpm=niche_profile.get("target_wpm", 170),
                            )
                            if pol_lint.is_valid:
                                logger.info("🔥 [ContentAgent] Stage-3 Showrunner Polished Script passed Linter — adopting upgraded masterpiece!")
                                return polished
                    return script

                last_issues = linter_res.issues
                logger.warning(
                    f"⚠️ [ContentAgent] Quality Gate failed (attempt {attempt}/{max_attempts}): "
                    f"{'; '.join(linter_res.issues)}. Retrying with corrective prompt..."
                )

                current_prompt = f"""{prompt}

═══════════════════════════════════════════════════════════
🚨 CORRECTION FEEDBACK FROM QUALITY LINTER (ATTEMPT {attempt}/{max_attempts}):
{linter_res.retry_prompt_hint}
PREVIOUS ATTEMPT FAILED ON:
{'; '.join(linter_res.issues)}
Rewrite immediately addressing ALL violations above!
═══════════════════════════════════════════════════════════
"""
            except Exception as e:
                logger.warning(f"[ContentAgent] Generation attempt {attempt}/{max_attempts} error: {e}")
                if attempt < max_attempts:
                    time.sleep(2.5)
                    try:
                        self.rotator.probe_all_models_concurrently(force=True)
                    except Exception:
                        pass
                    continue
                if not last_script:
                    logger.info(
                        f"🛡️ [ContentAgent] Kích hoạt Curated 5-Act Viral Script Fallback cho '{topic}' ({channel_niche}, {language}, format={vf})"
                    )
                    return self._build_curated_5act_script_fallback(
                        topic=topic,
                        channel_niche=channel_niche,
                        language=language,
                        video_format=vf,
                    )

        logger.error(f"❌ [ContentAgent] Script failed Quality Gate Linter after {max_attempts} attempts: {last_issues}")
        if last_script:
            return last_script
        raise RuntimeError(f"Script validation failed after {max_attempts} attempts: {'; '.join(last_issues)}")
