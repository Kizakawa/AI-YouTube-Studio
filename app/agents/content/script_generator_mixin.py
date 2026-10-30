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
            """🔥 MANDATORY CEILING-BREAKER & VISCERAL ANALOGY ARCHITECTURE (APPLY STRICTLY TO ALL EN & pt-BR CHANNELS):
1. GOLDEN 0.0s RETENTION LOCK: EXPLICIT ENTITY + VISCERAL EVERYDAY IMAGE OR LETHAL PARADOX IN SCENE 1:
   - `title`: MUST explicitly name high-search-volume entities + vivid, easy-to-picture clash or stakes ("Lula-Colossal vs Cachalote: O Duelo Mortal No Fundo Do Mar 🦑🐋", "Komodo Dragon vs Water Buffalo: One Bite Seals Its Fate 🦎🐃"). NEVER hide the entity in the title, AND NEVER put dry textbook physics units (`PSI`, `Newtons`, `kPa`, `Joules`, `LD50`) in the title!
   - `Scene 1 (0.0s–2.2s, 6–10 words)`: Viewers scrolling the Shorts Feed DO NOT read the title before hearing the first 2 seconds! Therefore, Scene 1 MUST explicitly name the primary entity/combatants AND paint an immediate entity-specific physical action or paradox right at 0.0s!
   - 🧠 DEEP ENTITY PROFILING OVER DRY UNITS OR CLICHÉS: Understand what makes THIS specific entity's anatomy, physical structure, environment, or psychological mechanism unique, and express its real power in intuitive everyday imagery that viewers instantly picture—never dump unexplained laboratory/physics units that ordinary viewers cannot visualize, and never copy-paste the same generic stock metaphor across different videos!
   - STRICTLY FORBIDDEN VAGUE POINTERS: NEVER start Scene 1 with vague entity-blind pointers like "Look closely at why...", "Watch the exact moment...", or "Olhe bem para..." that hide the subject! Real YouTube Analytics proves hiding the entity in Scene 1 causes 70%–94% Swipe-Away!
   - STRICTLY FORBIDDEN IN SHORTS: NEVER split a 35-second Short into "Round 1, Round 2, Round 3" or switch to a 3rd unannounced opponent! Keep 100% of all 7–8 scenes focused on the exact entities promised in the `title`!"""
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
        niche_instructions: str = "",
    ) -> str:
        """
        STAGE 1 (Deep Showrunner Research & 3-Angle Viral Tournament LLM Pass):
        Đầu tư thêm 1 lượt gọi LLM chuyên sâu để đào sâu thông số vật lý/sinh học/lịch sử/tâm lý thật,
        đặt lên bàn cân 3 góc tiếp cận Viral khác nhau (1v1 Clash vs Paradox Twist vs Behavioral Glitch),
        chọn ra Blueprint thắng cuộc phân tách rạch ròi giữa `winning_title` (YT_SEARCH) và `scene_1_hook_0s` (4 Đòn Tâm Lý Thị Giác).
        """
        notes_ctx = f'\nCreator Direction: "{director_notes.strip()}"' if (director_notes and len(director_notes.strip()) > 3) else ""
        niche_ctx = f"\n{niche_instructions[:2200]}\n" if niche_instructions else ""
        real_world_dossier = ""
        try:
            from app.agents.content.longform_10min_generator import _mine_real_world_research_dossier
            real_world_dossier = "\n" + _mine_real_world_research_dossier(topic=topic, channel_niche=channel_niche) + "\n"
        except Exception:
            pass
        blueprint_prompt = f"""You are an Elite YouTube Shorts Showrunner, Documentary Fact Researcher, and Viral Retention Architect for niche '{channel_niche}'.
Input Topic: "{topic}"
Target Spoken Language: {language} ({target_lang_display}){notes_ctx}
{real_world_dossier}{niche_ctx}
{channel_memory_block[:2500]}

YOUR MISSION (DEEP PRE-SCRIPT RESEARCH & 3-ANGLE TOURNAMENT):
Do NOT write the full script yet. Spend your full reasoning capacity to deeply understand the user's intent, mine the most jaw-dropping verifiable truths about "{topic}", and pit 3 distinct viral angles against each other before selecting the #1 Winning Blueprint:
0. DEEP USER INTENT & SUBTEXT COMPREHENSION (ZERO KEYWORD DEPENDENCE):
   - Analyze what the user truly wants to convey in "{topic}" (and Creator Direction if provided), even if the input is brief, conversational, or a raw script idea.
   - Deduce the hidden curiosity gap, emotional core, canonical `primary_entity`, and authentic physical environment/era of the story.
1. DEEP ENTITY ANATOMY / MECHANISM PROFILING & TAILOR-MADE INTUITIVE IMAGERY:
   - First, deeply profile the specific entity/phenomenon in "{topic}": What is its exact biological structure, physical engineering, habitat, historical setting, or psychological trigger? How does Entity A's specific weapon/mechanism physically interact with Entity B's specific defense/body/mind?
   - Uncover 3 real, verifiable mechanisms unique to THIS entity, and translate each one into **fresh, tailor-made everyday physical imagery** derived directly from how this entity actually works so a viewer with zero science background immediately sees and feels it!
   - NEVER dump unexplained laboratory/textbook physics units that viewers cannot picture, AND NEVER paste recycled generic stock clichés onto unrelated subjects!
2. 3-ANGLE VIRAL TOURNAMENT:
   - Angle A (Asymmetric 1v1 Clash / Direct Confrontation): Frame around "Entity A vs Entity B" with a visceral, easy-to-picture physical clash.
   - Angle B (Counter-Intuitive Paradox / Hidden Lethal Mechanism): Expose why what 99% of people believe about this topic is dead wrong.
   - Angle C (Visceral 0.0s Survival / Micro-Behavior Test): Immediate high-stakes sensory danger or observable real-life trap.
3. SELECT THE #1 WINNING BLUEPRINT FOR >70% STAYED & 100%+ APV (EXPLICIT ENTITY + VISCERAL SENSORY SHOCK AT 0.0s):
   - `winning_title`: Search-optimized High-CTR Title in {target_lang_display} explicitly naming the primary entities + visceral stakes or clash (`[Entity A] vs [Entity B]: [Visceral Stakes]`, ZERO `PSI` / `Newtons` / dry lab units!).
   - BROAD-APPEAL ENTITY TRANSLATION IN TITLE (NO OBSCURE JARGON HEADERS): If the input topic uses an obscure scientific, geological, or academic psychology term that 90% of broad viewers do not instantly recognize (e.g., "Brinicle", "Pratfall Effect", "Zeigarnik Effect"), NEVER start `winning_title` with that obscure jargon word! Translate it into an instantly recognizable mass-appeal entity or physical confrontation (e.g., instead of "The Antarctic Brinicle", write "Underwater Ice Tornado vs Sea Life: Freezing Trap"; instead of "The Pratfall Effect", write "Why One Clumsy Mistake Makes People Trust You Instantly").
   - `selected_hook_archetype`: Choose the #1 best high-retention hook archetype for Scene 1 (`1. Direct Physical Clash`, `2. Walking Dead / Lethal Paradox with Named Entity`, `3. Visceral Historical / Sensory Shock Event`, or `4. Named Psychological / Behavioral Trap`).
   - `scene_1_hook_0s`: 6–10 words in {target_lang_display} using `selected_hook_archetype`. CRITICAL: Viewers in the Shorts Feed DO NOT read the title before hearing the first 2 seconds! Therefore, `scene_1_hook_0s` MUST explicitly name the primary entity/combatants from `winning_title` + a vivid physical action, everyday comparison, or lethal paradox right at 0.0s (ZERO `PSI` / `Newtons` / dry units)! NEVER use vague pointers like "Look closely at..." or "Watch the exact moment..." that hide the entity!
   - `comment_war_bait_claim` (CUNNINGHAM'S LAW CONTROVERSY ENGINE — VIEWS & COMMENTS > POLITE NEUTRALITY): Design 1 bold, provocative, unapologetically polarizing statement or "Sacred-Cow Breaker" to drop in Scene 4–5 (e.g., declaring the fan-favorite predator overrated, calling a polite social habit a scam, or challenging the official explanation) so "armchair experts" rush into the comments to argue while the video loops in the background!
   - `counterfactual_debate_comment`: 12–22 words in {target_lang_display}. Design an unresolved Counterfactual Twist / Escalated 2v1 Odds / Next-Opponent 50-50 Debate Question ending with `(1) ... or (2) ... 👇` that was NOT already answered inside the video (ZERO 'Subscribe/Inscreva-se' spam!).

Return ONLY valid JSON:
{{
  "entity_mechanism_profile": "Deep profile of the specific entity's unique anatomy, physical structure, vulnerability, or psychological dynamic (what makes THIS entity physically/behaviorally distinct from all others)",
  "mined_hard_metrics": ["entity-specific intuitive mechanism/imagery 1", "entity-specific intuitive mechanism/imagery 2", "entity-specific intuitive mechanism/imagery 3"],
  "angle_a_clash": "1-sentence pitch for Angle A",
  "angle_b_paradox": "1-sentence pitch for Angle B",
  "angle_c_visceral": "1-sentence pitch for Angle C",
  "winning_angle": "Angle A | Angle B | Angle C — plus 1-sentence reason why it maximizes Stayed-to-Watch % and APV %",
  "primary_entity": "Bare 1-2 word English name of the primary subject/entity",
  "winning_title": "High-CTR Searchable Title in {target_lang_display} (28-55 chars naming exact entities + visceral clash/stakes + emojis, ZERO PSI/Newtons)",
  "selected_hook_archetype": "1. Direct Physical Clash | 2. Lethal Paradox with Named Entity | 3. Visceral Sensory Shock Event | 4. Named Behavioral Trap",
  "scene_1_hook_0s": "6-10 word hook in {target_lang_display} explicitly naming the primary entity/combatants + vivid physical action or paradox at 0.0s (ZERO PSI/Newtons, NEVER use vague 'Look closely at...' or 'Watch the exact moment...'; NO '?' questions)",
  "act_2_concrete_proof": "Direct visceral action, real-life spoken quote, or named historical event + first everyday physical analogy for Scenes 2-3 (STRICTLY FORBIDDEN: NEVER start with 'Studies show...' and NEVER dump dry units like PSI/Newtons)",
  "scene_4_mid_video_15s_twist": "Explosive 15-second Plot Twist / Second Hook for Scene 4 that flips expectations with a 2nd vivid physical mechanism or everyday comparison",
  "comment_war_bait_claim": "Bold, polarizing 'Sacred-Cow Breaker' / Cunningham's Law claim in {target_lang_display} to spark fierce debate in the comment section",
  "act_4_ultimate_payoff": "For Psychology/Social topics: MUST include an exact word-for-word spoken sentence in quotation marks ('\"...\"') that the viewer can say out loud to flip the trap. For Wildlife/Mysteries/History: decisive biomechanical knockout or anomaly revelation for Scenes 6-7",
  "scene_8_infinite_echo_loop": "A 100% grammatically COMPLETE, self-contained closing punchline sentence in {target_lang_display} that conceptually echoes Scene 1's theme (STRICTLY FORBIDDEN: NEVER write an unfinished half-sentence ending with 'because...', 'ensure that...', 'which is why...', or 'and that is why')",
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
                if bp_data.get("primary_entity"):
                    self._last_primary_entity = str(bp_data.get("primary_entity")).strip()
                logger.info(
                    f"🧠 [ContentAgent Stage-1 Deep Blueprint] Winning Angle: {bp_data.get('winning_angle', '')[:80]} | "
                    f"Archetype: {bp_data.get('selected_hook_archetype', '')} | "
                    f"Hook 0.0s: '{bp_data.get('scene_1_hook_0s', '')}' | "
                    f"15s Twist: '{str(bp_data.get('scene_4_mid_video_15s_twist', ''))[:70]}'"
                )
                return (
                    "\n═══════════════════════════════════════════════════════════\n"
                    "🔥 STAGE-1 SHOWRUNNER WINNING VIRAL BLUEPRINT (MANDATORY EXECUTION):\n"
                    f"- Entity Anatomy & Mechanism Profile: {bp_data.get('entity_mechanism_profile', '')}\n"
                    f"- Mined Entity-Specific Intuitive Mechanisms: {json.dumps(bp_data.get('mined_hard_metrics', []), ensure_ascii=False)}\n"
                    f"- Winning Angle: {bp_data.get('winning_angle', '')}\n"
                    f"- Primary Entity: {bp_data.get('primary_entity', '')}\n"
                    f"- Recommended High-CTR Searchable Title: {bp_data.get('winning_title', '')}\n"
                    f"- Selected Hook Archetype: {bp_data.get('selected_hook_archetype', '')}\n"
                    f"- Mandatory Scene 1 Hook (0.0s — explicitly name entity + shock metric/paradox): {bp_data.get('scene_1_hook_0s', '')}\n"
                    f"- Mandatory Act 2 Proof + First Hard Metric (Scenes 2-3): {bp_data.get('act_2_concrete_proof', '')}\n"
                    f"- Mandatory Scene 4 Mid-Video 15s Reversal Twist + Second Metric: {bp_data.get('scene_4_mid_video_15s_twist', '')}\n"
                    f"- 🔥 Mandatory Comment-War / Cunningham's Law Bait Claim (Weave into Scene 4 or 5 to trigger comment debates): {bp_data.get('comment_war_bait_claim', '')}\n"
                    f"- Mandatory Act 4 Payoff (Scenes 6-7): {bp_data.get('act_4_ultimate_payoff', '')}\n"
                    f"- Mandatory Scene 8 Complete Closing Punchline (100% grammatically complete standalone sentence — NEVER leave an unfinished half-sentence!): {bp_data.get('scene_8_infinite_echo_loop', '')}\n"
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
        - Đảm bảo Cảnh 1 (0.0s Hook) nêu đích danh Thực thể chính + thông số gây sốc hoặc nghịch lý sinh tử ngay ở giây 0.0s (CẤM dùng câu chỉ trỏ mơ hồ "Look closely at..." giấu chủ thể).
        - Đảm bảo không nhồi nhét "Round 1, Round 2, Round 3" vào Shorts 35s gây rớt view ở giây 14.
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
1. GOLDEN 0.0s RETENTION LOCK IN `SCENE 1 HOOK` (0.0s–2.2s | 6 TO 10 WORDS) & ZERO DRY PHYSICS UNITS:
   - Keep `title` razor-sharp for `YT_SEARCH` + CTR (explicitly naming the primary entities + visceral clash/stakes — ZERO dry physics units like `PSI`, `Newtons`, `kPa`, `Joules`, or `LD50`!).
   - Check Scene 1 (`scenes[0].narration` and `hook`): Scene 1 MUST explicitly name the primary entity/combatants from `title` AND paint an immediate visceral physical clash, everyday sensory image, or lethal paradox right at 0.0s (e.g., "One jaguar bite punches straight through a caiman's armored skull.", "This giant buffalo walked away from a Komodo dragon, already dead.", "Las Vegas casinos remove every clock to hijack your brain's sense of time.")!
   - 🧠 ENTITY-AUTHENTIC IMAGERY CHECK: Scan every scene's `narration`, `title`, and `thumbnail_text` — if any sentence relies on dry textbook/laboratory physics units that ordinary viewers cannot visualize OR generic recycled stock clichés, REWRITE IT into fresh, tactile imagery grounded directly in THAT specific entity's real anatomy, structure, habitat, or psychological mechanism!
   - STRICTLY BAN VAGUE POINTERS: If Scene 1 starts with a vague entity-blind pointer like "Look closely at...", "Watch the exact moment...", or "Olhe bem para..." that hides the subject, REWRITE Scene 1 immediately so it explicitly names the primary entity + visceral impact or paradox! NEVER use '?' questions in Scene 1.
   - ZERO MULTI-ROUND BAIT-AND-SWITCH: If any scene in this Short mentions "Round One / Round Two / Round Three" or switches to a 3rd animal not in `title`, REWRITE those scenes so 100% of the Short stays focused on the exact 1v1 duel or phenomenon promised in `title`!
2. CAUSAL STORYTELLING FLOW ("BUT / THEREFORE" TRANSITIONS):
   - Ensure every scene flows into the next with high-tension causal connectors rather than flat textbook listing. Every scene MUST be 100% grammatically complete (11–16 words per body scene).
3. SCENE 4 MID-VIDEO CLIMAX TWIST (14.0s–20.0s | ANTI-DROP-OFF ENGINE):
   - Ensure Scene 4 (`climax_scene_id`) delivers an explosive SECOND HOOK / REVERSAL TWIST backed by a second concrete metric/mechanism, and has a sharp uppercase HUD `overlay_badge` (e.g. `"[ // THE 15-SECOND TWIST // ]"`).
4. SCENE 7/8 NATURAL FINALE (APV > 100%):
   - Craft the final scene's closing sentence as a punchy, memorable conclusion that connects naturally back to the opening premise (NEVER write robotic phrases like "that is why you should look closely at..."). ZERO spoken Subscribe/Follow/Comment CTA!
5. DISTILL 5 DUAL-TRACK YOUTUBE PEAK SEARCH QUERIES (`hook_search_queries` — FOLLOW THE ACTIVE YAML `video_search_strategy`):
   - Provide 5 distinct, concrete English search queries (3–5 words each): probe at most 1–2 genuine real-camera encounter queries (`"[Entity A] attacking [Entity B]"`, `"[Entity A] vs [Entity B]"`) ONLY if the script is a 2-entity physical duel, and make the remaining 3–5 queries **100% CLEAN SINGLE-ENTITY REAL-ACTION QUERIES** (`"[Single Entity] + [Real Physical Action / Environment]"`, ZERO `"vs"`) so our engine searches both and Fast-Drops `"A vs B"` immediately if no clean clash clip exists!
6. SELF-CONTAINED, EVERYDAY YOUTUBE-NATIVE BODY VISUAL QUERIES (`visual_keyword`, `stock_query`, `visual_prompt` — ZERO SIDE-PROPS, ZERO ACADEMIC JARGON, ZERO `"A vs B"` STUFFING IN BODY SCENES):
   - Every scene's `visual_keyword` and `stock_query` MUST be 100% self-contained when isolated from the script (zero pronouns like "it/he/they", zero literal metaphor props like "hydraulic press" or "silent cage", and ZERO `@tool` function names like `render_doodle_2d_explainer_scene`).
   - 🚫 ZERO SIDE-PROP / OBSERVER ROOM DRIFT: If a body scene mentions a secondary side-effect or observer (e.g., car engines dying during a volcanic gas disaster, engineers in a command room during a weapon test, or empty trees/valleys), NEVER set `visual_keyword` or `stock_query` to the side-prop or room (`"car dashboard night"`, `"engineers planning"`). Always anchor `visual_keyword` and `stock_query` to a distinct visual angle of the **Primary Phenomenon/Entity**!
   - 🚫 EVERYDAY YOUTUBE CREATOR VOCABULARY ONLY (NO TEXTBOOK JARGON): Never put academic/Latin/clinical terms (`"benthic crustacean"`, `"apex elapid"`, `"limnic eruption"`) in `visual_keyword` or `stock_query`—always use simple 3–5 word everyday English that real YouTube creators put in video titles (`"deep sea crab underwater"`, `"king cobra strike"`, `"volcanic crater lake gas"`).
   - CRITICAL YAML DUAL-TRACK RULE: For individual body scenes focusing on ONE contender/mechanism/environment, NEVER stuff `"A vs B"` or `"vs"` into `visual_keyword` (which lures `#edit` comparison spam and AI Veo clips) — write ONLY **`[Single Active Entity] + [Real Physical Action / Environment]`**! And `stock_query` (2–4 words) MUST ALWAYS be 100% single-entity with **ZERO `"vs"` / `"versus"`**!
7. COUNTERFACTUAL 50/50 DEBATE COMMENT (`pinned_comment` — ZERO SUBSCRIBE SPAM & NEVER ASK WHAT THE VIDEO ALREADY ANSWERED):
   - If `pinned_comment` asks a question the video already answered (e.g. "Who wins: A or B?") or includes robotic "SUBSCRIBE / INSCREVA-SE" spam, REWRITE it into a punchy 12–22 word Counterfactual Escalation / Next-Opponent 50/50 Debate Question ending with `(1) ... or (2) ... 👇`!

Return ONLY the complete upgraded JSON matching the exact same schema as CURRENT DRAFT SCRIPT JSON."""
        try:
            raw_polished = self._call_api(polish_prompt, json_mode=True, timeout=18, max_total_seconds=28)
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
        niche_instructions = build_script_prompt_instructions(niche_profile, language=language, video_format=vf)
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
                niche_instructions=niche_instructions,
            )

        if is_long_form:
            try:
                from app.agents.content.longform_10min_generator import generate_10min_longform_documentary
                doc_10min = generate_10min_longform_documentary(
                    agent=self,
                    topic=topic,
                    channel_niche=channel_niche,
                    language=language,
                    target_lang_display=target_lang_display,
                    lang_instruction=lang_instruction,
                    director_block=director_block,
                    channel_memory_block=channel_memory_block,
                    niche_instructions=niche_instructions,
                )
                if doc_10min and len(doc_10min.scenes) >= 32:
                    if doc_10min.primary_entity:
                        self._last_primary_entity = doc_10min.primary_entity
                    return doc_10min
            except Exception as e_10m:
                logger.warning(f"[ContentAgent] 10-Min LongForm Generator fallback: {e_10m}")
            duration_spec = "8 TO 10.5 MINUTES CINEMATIC 16:9 WIDESCREEN DOCUMENTARY (36 TO 44 SCENES ACROSS 10 CHAPTERS, 1,350 TO 1,600 WORDS TOTAL — SCENE 1 COLD-OPEN HOOK 12-18 WORDS, BODY SCENES 34-42 WORDS EACH)"
            word_range_en = "1,350 to 1,600 words (8 to 10.5 minutes Widescreen 16:9 Documentary across 36 to 44 rich scenes; Scene 1 Cold-Open Hook MUST be 12-18 words; Body scenes MUST be 34-42 words each)"
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
1. Viewed vs. Swiped-Away > 75%: Achieved by explicitly naming the primary entity/combatants in BOTH `title` (for `YT_SEARCH`) AND `Scene 1 Hook` ({'8–14 words' if is_long_form else '6–10 words, ~1.6–2.2s'} delivering an immediate shocking physical metric, clash, or lethal paradox right at 0.0s — NEVER hiding the subject behind vague "Look closely at..." or "Watch the exact moment..." pointers!).
2. High Retention + Smooth, Complete Narrative ({'360–560 words across 16–22 widescreen scenes, 18–26 words per body scene, 8–12s per scene' if is_long_form else '92–118 words, 34–42s across 7–8 scenes, 11–16 words per body scene'}):
   - NEVER write an abrupt, choppy, or truncated script ("cụt lủn")! Let the sentences flow naturally into one another with smooth transitions, clear context, and complete explanations.
   - {'For LONG-FORM 16:9 mode: Structure the 16–22 scenes across 5 gripping Documentary Chapters using one of the 3 Competitor Million-View Blueprints:\n     * (A) `@FossilVideos` Gauntlet / Survival Tier-List (Wildlife): Chapter 1 (Scenes 1–3: Cold Open & Contender Stats), Chapter 2 (Scenes 4–7: Round 1 & Round 2 Challengers), Chapter 3 (Scenes 8–12: Round 3 Armored/Venom Threat & Mid-Point Twist), Chapter 4 (Scenes 13–17: Round 4 Pack/Apex Confrontation), Chapter 5 (Scenes 18–20: The Final Boss & Ultimate Verdict).\n     * (B) `@disastersexplained1` "Every X Record/Type Explained" (Mysteries/Ocean): Open Scene 1 with a 2nd-person sensory danger hook ("You notice a calm line on the water... You think you still have time. That is the mistake."), then escalate Chapter 1 -> Chapter 5 from the strangest warning sign to the deadliest unbroken record.\n     * (C) `@IFinity_What` "What If YOU..." 2nd-Person Timeline Escalation (Alternate History & Psychology): Frame directly around "YOU" escalating from Chapter 1 (Minute 1: The Superpower/Control Illusion) -> Chapter 2 (Hour 12: The First Hidden Glitch) -> Chapter 3 (Day 3: Systemic/Neural Overload) -> Chapter 4 (Day 7: Complete Collapse) -> Chapter 5 (The Final Aftermath).\n     * Put clean Chapter HUD badges like "[ // CHAPTER 1: THE SILENT TRAP // ]" or "[ // ROUND 1: THE SPEED TEST // ]" in `overlay_badge` ONLY on the first scene of each chapter (Scenes 1, 4, 8, 13, 18) and `null` on all other scenes!' if is_long_form else 'For SHORTS 9:16 mode: Keep 100% of all 7–8 scenes locked on the exact 1v1 duel or single phenomenon promised in the `title`! NEVER split a 35-second Short into "Round One / Round Two / Round Three" or switch to a 3rd unannounced opponent!'}
   - Every scene MUST be a 100% grammatically complete sentence. NEVER chop one sentence across two scenes!
═══════════════════════════════════════════════════════════
1. THE MANDATORY 5-ACT DUAL-PEAK STORYTELLING BLUEPRINT (target: {word_range_en}):
   - DURATION & SCENE COUNT: {duration_spec}.
   - DEEP ENTITY PROFILING & INTUITIVE STORYTELLING (CRITICAL FIRST STEP):
     * Before writing Scene 1, analyze the specific entity/phenomenon in Depth (using `Entity Anatomy & Mechanism Profile` from Stage 1): What makes THIS specific entity's physical structure, anatomical weapon, habitat, historical setting, or human psychology unique?
     * Express every force, strike, pressure, speed, or mental reaction through **tailor-made, tactile everyday imagery derived directly from that entity's real mechanism**—never leave unexplained laboratory/physics units that viewers cannot visualize, and never copy-paste generic stock clichés across unrelated topics!
   - YOU MUST EXECUTE ALL 5 ACTS SMOOTHLY IN ORDER across {'16 to 22 scenes (5 Documentary Chapters)' if is_long_form else '7 to 8 scenes'}:

     * ACT 1 — THE 1.8-SECOND ENTITY + VISCERAL SHOCK / PARADOX HOOK (Scene 1 | 6 to 10 words MAX):
       - CRITICAL RULE: Viewers in the Shorts Feed DO NOT read the bottom title before hearing the first 2 seconds of voiceover! Therefore, Scene 1 MUST explicitly name the primary entity/combatants from the `title` AND paint an **immediate visceral physical action, everyday comparison, or lethal paradox** right at 0.0s (ZERO `PSI` / `Newtons` / dry lab units!):
         1. Direct Visceral Impact Clash ("Uma única mordida da onça fura o crânio do jacaré.", "One jaguar bite punches straight through a caiman's armored skull.")
         2. Lethal Paradox with Named Entity ("This giant buffalo walked away from a Komodo dragon, already dead.", "Esta mamba-negra mata em segundos, mas o mangusto ri do veneno.")
         3. Visceral Historical / Sensory Shock Event ("In 1815, Mount Tambora's ash turned June into freezing midnight.")
         4. Named Psychological / Behavioral Trap ("The second you apologize first, a manipulator smells easy prey.")
       - FORBIDDEN: Dry textbook units (`1,500 PSI`, `195 Newtons`, `kPa`), vague entity-blind pointers ("Look closely at why...", "Watch the exact moment..."), raw colon headers ("A vs B: ..."), abstract poetic metaphors ("loaded weapon", "silent cage"), or passive "Did you know?" questions.

     * ACT 2 — NATURAL CONTEXT, CONCRETE PROOF & FIRST EVERYDAY ANALOGY (Scenes 2 – 3):
       - STRICT BAN ON ACADEMIC STUDY OPENERS: NEVER start Scene 2 or 3 with "Studies show...", "Psychological studies reveal...", "Research indicates...", or "Estudos de biomecânica mostram que..."! Drop the viewer directly into the action, contrast, or named real-world event ("While a lion spends ten minutes choking the throat, the jaguar...", "Enquanto o leão tenta sufocar a garganta, a onça...").
       - Smoothly build the context and explain the **first real weapon or mechanism through a vivid everyday analogy** (ZERO dry physics units):
         * For Psychology / Manipulation / Relationships / Stoicism: Describe the observable everyday trap or spoken line ('Right after crossing your boundary, they smile and say, "You are overreacting again."').
         * For Mysteries / Deep Sea / History: Paint what the human body, eyes, lungs, or vessel structure specifically undergoes in that exact environment or historical moment.
         * For Wildlife / Animal Rescue: Describe how that animal's specific anatomical structure (jaw hinge, talon curvature, quill barbs, loose hide, venom pathway) physically operates on its opponent, or show the exact emotional turning point.

     * ACT 3 — THE 15-SECOND MID-VIDEO CLIMAX TWIST (Scene 4) & CUNNINGHAM'S LAW COMMENT-BAIT CLAIM (Scene 5):
       - CRITICAL FOR APV > 95% & MASSIVE COMMENT VOLUME:
       - **Scene 4 (`climax_scene_id` — The 15-Second Plot Twist)**: Flip the viewer's initial assumption with a **hidden counter-mechanism or second vivid physical twist** ("But the real weapon isn't its teeth—it fires an underwater sound blast strong enough to stun prey from the inside.", "Mas o jacaré esconde um ponto fraco do tamanho de uma moeda bem atrás dos olhos.") and set `overlay_badge` on Scene 4 in {target_lang_display} (e.g., `"[ // THE 15-SECOND TWIST // ]"` for EN or `"[ // O PONTO CEGO // ]"` for pt-BR).
       - **Scene 5 (`THE COMMENT-WAR BAIT SCENE` — Cunningham's Law)**: In Scene 5, you MUST deliver the unapologetically bold, polarizing **Sacred-Cow Breaker / Fanboy Trigger claim** from `comment_war_bait_claim` (e.g., for Psychology: *"If you don't have the capacity to walk away, your kindness isn't a virtue—it is just fear."*; for Wildlife: *"Quilo por quilo, o leão africano parece um amador perto da precisão brutal da onça-pintada."*) so viewers rush to the comments to argue while the video loops!

     * ACT 4 — THE SURGICAL COUNTER-MOVE / ULTIMATE PAYOFF (Scenes 6 – 7):
       - Deliver the full climax payoff, resolution, or practical word-for-word Counter-Move that rewards the viewer for staying until the end:
         * For Psychology: NEVER write abstract advice like "set a visible boundary". You MUST give the viewer an exact, verbatim 1-sentence spoken counter-line inside quotation marks (`"..."`) to say out loud on the spot (e.g., 'Next time they push your limit, hold eye contact for three seconds and ask: "Are you asking me, or telling me?"').
         * For Wildlife / Rescue / Mysteries: Deliver the decisive biomechanical outcome, joyful transformation, or sensory revelation that resolves the twist.

     * ACT 5 — NATURAL PUNCHY FINALE & COMPLETE CLOSING PUNCHLINE (Final Scene 7 or 8):
       - ZERO SPOKEN OUTRO CTA: NEVER say "Subscribe", "Follow", "Leave a comment", "Inscreva-se", or "Đăng ký kênh" in the narration!
       - Craft the final scene as a 100% grammatically COMPLETE, self-contained closing punchline sentence with a full subject, verb, and object! STRICTLY FORBIDDEN: NEVER write an unfinished half-sentence loop (e.g., NEVER end with "...which is why", "...ensure that", "...because", or "that is why you should look closely at").

2. HIGH-CTR SEARCHABLE TITLE VS. SCENE 1 HOOK RULES (PROVEN BY OUR OWN TOP ANALYTICS WINNERS):
   - The first 1.5 - 2.2 seconds determines 100% of Shorts performance (Viewed vs Swiped away).
   - HIGH-CTR TITLE MANDATE (`YT_SEARCH` DISCOVERY ENGINE — ZERO DRY PHYSICS UNITS):
     * NEVER write bleak/depressing-only titles for Animal Rescue (FORBIDDEN: "Frozen Puppy Found in Rain"). ALWAYS build a transformation/recognition gap ("The Exact Moment This Stray Dog Realized He Was Safe 🥺❤️", "He Guarded This Drain For 72 Hours Until Rescuers Looked Inside 🐾").
     * NEVER write vague generic titles that hide the subject (FORBIDDEN: "Invisible Monsters Waiting To Strike", "That Fake Smile Exposes Their Envy", "Where Does This Endless River Go?"). ALWAYS name the specific entity/phenomenon + vivid clash or stakes ("Green Anaconda vs. Black Caiman: Death Grip In The Amazon! 🐍🐊", "The Boiling Amazon River That Cooks Prey Without A Volcano! 🔥🌊", "The 4-Second Eye Freeze That Exposes Fake Smiles 👁️🚩").
     * NEVER put dry physics/lab units (`PSI`, `Newtons`, `kPa`, `Joules`, `LD50`) in titles or hooks!
   - SCENE 1 HOOK ARCHETYPES (6–10 WORDS MAX — EXPLICITLY NAME THE ENTITY + VISCERAL IMPACT OR PARADOX AT 0.0s):
     * Archetype 1 (Direct Visceral Physical Clash):
       - "One jaguar bite punches straight through a caiman's armored skull."
       - "Thirty thousand razor quills versus the deadliest jaws in Africa."
     * Archetype 2 (Lethal Paradox with Named Entity):
       - "This giant buffalo walked away from a Komodo dragon, already dead."
       - "Esta mamba-negra mata em segundos, mas o mangusto ri do veneno!"
     * Archetype 3 (Visceral Historical / Sensory Shock Event):
       - "One hundred thousand Mongol horsemen vanished forty miles from Vienna."
       - "In 1815, Mount Tambora's eruption buried summer under freezing snowstorms."
     * Archetype 4 (Named Psychological / Behavioral Trap):
       - "Las Vegas casinos remove every clock to hijack your brain's time."
       - "FBI interrogators place one paper cup beside a guilty suspect's hand."
   - NEVER start with vague entity-blind pointers ("Look closely at...", "Watch the exact moment..."), abstract literary metaphors, textbook definitions, greetings, or passive rhetorical questions ("Did you know...", "Welcome to...").
   - thumbnail_text MUST be STRICTLY 2 to 3 ULTRA-PUNCHY WORDS MAX in ALL CAPS with ZERO emojis and ZERO dry physics units (competitor `@FossilVideos` & `@IFinity_What` formula: e.g. "TOO EASY?", "TURN INVISIBLE.", "EVERY RECORD", "CRUSHES SKULLS!", "ZERO SURVIVORS", "4 SECONDS MAX"). Our Thumbnail Engine renders Word 1 in Pure White and Word 2-3 in High-Contrast Yellow/Red!

3. SEAMLESS INFINITE LOOP ENDING (NO SPOKEN OUTRO CTA):
   - NEVER include "Subscribe", "Like", "Follow", "Leave a comment", "Inscreva-se", or "Đăng ký kênh" in ANY scene's spoken narration!
   - Every single scene's spoken narration MUST be a 100% grammatically complete sentence with proper closing punctuation. NEVER end any scene with dangling prepositions or conjunctions ('because', 'which', 'and', 'so', 'that', 'but', 'with').

4. CONTEXT-ANCHORED, SEARCH-ENGINE-NATIVE VISUAL QUERIES (`visual_keyword` & `stock_query` — STRICTLY FOLLOW SECTION 9 `YAML VIDEO SEARCH & DUAL-TRACK QUERY STRATEGY` ABOVE):
   - Downstream visual agents search YouTube (`visual_keyword`, 3–5 English words) and Pexels/Pixabay Stock APIs (`stock_query`, 2–4 English words) for each scene independently:
     * `visual_keyword` (YouTube Dual-Track search in Everyday Creator Vocabulary): Follow the active niche's YAML `Single-Entity Body Scene Rule` and `YAML Clean Single-Entity Real-Action Query Examples` in Section 9 above! For every body scene focusing on ONE contender, weapon, mechanism, or environment, write ONLY **`[Single Canonical Entity] + [Real Physical Action / Environment]`** — NEVER stuff `"A vs B"` or `"vs"` into single-entity body scenes (which lures comparison `#edit` and AI Veo videos)! Reserve `"Entity A attacking/vs Entity B"` strictly for direct 2-entity clash moments when permitted by the YAML `Dual-Track Clash vs. Single-Entity Rule`.
     * 🚫 ZERO SIDE-PROP / OBSERVER DRIFT & ZERO ACADEMIC JARGON IN QUERIES: Never search for tangential side props or rooms mentioned in a sentence (`"car dashboard"`, `"military engineers planning"`, `"rainforest canopy"`)—always keep `visual_keyword` and `stock_query` anchored to the Primary Subject/Phenomenon in a distinct action/angle. Translate all scientific/textbook terms (`"benthic crustacean"`, `"limnic eruption"`) into everyday YouTube words (`"deep sea crab underwater"`, `"volcanic crater lake gas"`). NEVER write `@tool` function names or literary storytelling phrases inside `visual_keyword`!
     * `stock_query` (Pexels/Pixabay API search — STRICTLY SINGLE-ENTITY, ZERO `"vs"`): Follow the YAML `Stock & Fallback Broad Query Rule` and `YAML Canonical Stock/Fallback Query Examples` in Section 9 above (`[Single Canonical Subject Noun] + [Setting/Action]`, 2–4 everyday English words). NEVER include `"vs"` or `"versus"` in `stock_query`, NEVER drop the main head noun, and NEVER write generic material textures or inanimate metaphor objects!
     * `hook_search_queries` (5 Dual-Track queries): In a 2-entity duel, include at most 1–2 genuine real-encounter queries (`"[Entity A] attacking [Entity B]"`) plus 3–4 clean single-entity real-action queries (`"[Entity A] + [real action/habitat]"`, `"[Entity B] + [real action/habitat]"`) so our engine searches both simultaneously and Fast-Drops `"A vs B"` immediately if no clean clash clip exists!

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
  "thumbnail_text": "STRICTLY 2-3 PUNCHY ALL-CAPS WORDS IN {target_lang_display} WITH ZERO DRY PHYSICS UNITS ({'e.g. FÁCIL DEMAIS?, ESMAGA CRÂNIOS!, RECORDE MORTAL!' if str(language or '').lower().startswith('pt') else 'e.g. TOO EASY?, TURN INVISIBLE., CRUSHES SKULLS!'})",
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
                raw_text = self._call_api(
                    current_prompt,
                    json_mode=True,
                    timeout=35 if is_long_form else 20,
                    max_total_seconds=60 if is_long_form else 38,
                )
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
                    hook_txt = str((script.scenes[0].narration if script.scenes else script.hook) or "").strip()
                    hook_wc = len(hook_txt.split())
                    title_clean = re.sub(r"[^\w\s]", "", str(script.title or "").lower()).strip()
                    hook_clean = re.sub(r"[^\w\s]", "", hook_txt.lower()).strip()
                    draft_already_sharp = (
                        6 <= hook_wc <= 12
                        and "?" not in hook_txt
                        and title_clean[:20] not in hook_clean
                        and len(getattr(script, "hook_search_queries", None) or []) >= 3
                    )
                    if draft_already_sharp:
                        logger.info(
                            f"⚡ [ContentAgent Fast-Track] Bản thảo Stage-2 đã đạt chuẩn 5-Act & 4 Đòn Tâm Lý Thị Giác "
                            f"(Hook {hook_wc} từ, {len(script.scenes)} cảnh) -> Bỏ qua lượt gọi LLM Stage-3 dư thừa!"
                        )
                        return script
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
                    time.sleep(1.0)
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
