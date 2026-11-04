"""app.agents.content.script_generator_mixin — Xây dựng bộ nhớ ngữ cảnh kênh, phác thảo Blueprint & sinh kịch bản 5 hồi."""
import json
import logging
import time
from typing import List, Optional
from config.settings import settings
from app.utils.key_rotator import GEMINI_PRIORITY_MODELS
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
        prose_dna_block = ""
        try:
            from app.services.niche_manager import build_million_view_showrunner_prose_dna
            prose_dna_block = "\n" + build_million_view_showrunner_prose_dna(channel_niche, language=language) + "\n"
        except Exception:
            pass
        real_world_dossier = ""
        try:
            from app.agents.content.longform_10min_generator import _mine_real_world_research_dossier
            real_world_dossier = "\n" + _mine_real_world_research_dossier(topic=topic, channel_niche=channel_niche) + "\n"
        except Exception:
            pass
        blueprint_prompt = f"""You are an Elite YouTube Shorts Showrunner, Documentary Fact Researcher, and Viral Spoken-Voice Architect for niche '{channel_niche}'.
Input Topic: "{topic}"
Target Spoken Language: {language} ({target_lang_display}){notes_ctx}
{prose_dna_block}{real_world_dossier}{niche_ctx}
{channel_memory_block[:2500]}

YOUR MISSION (DEEP PRE-SCRIPT RESEARCH, SPOKEN CREATOR VOICE & 3-ANGLE TOURNAMENT):
Do NOT write the full script yet. Spend your full reasoning capacity to deeply understand the user's intent, mine the most jaw-dropping verifiable truths about "{topic}", and pit 3 distinct viral angles against each other before selecting the #1 Winning Blueprint written in **100% authentic spoken YouTuber prose** (Zack D. Films / Casual Geographic / Charisma on Command / IFinity What / The Dodo / Você Sabia?):
0. DEEP USER INTENT & SUBTEXT COMPREHENSION (ZERO KEYWORD DEPENDENCE):
   - Analyze what the user truly wants to convey in "{topic}" (and Creator Direction if provided), even if the input is brief, conversational, or a raw script idea.
   - Deduce the hidden curiosity gap, emotional core, canonical `primary_entity`, and authentic physical environment/era of the story.
1. DEEP ENTITY ANATOMY / MECHANISM PROFILING & $1 SPOKEN EVERYDAY IMAGERY:
   - Deeply profile the specific entity/phenomenon in "{topic}": What is its exact biological structure, macroscopic physical engineering, habitat, historical setting, or real-room human dynamic?
   - Uncover 3 real, verifiable mechanisms unique to THIS entity, and express each one in **plain $1 spoken words a friend says across a kitchen table** (e.g., hollow sealed steel cans full of air vs gelatinous bodies filled with water and zero air pockets to crush; backward-pointing throat spikes pushing venomous tentacles down like spaghetti; two signatures firing on the spot unless a third commander aboard turns his key too)!
   - STRICTLY FORBIDDEN: Never dump dry physics units (`PSI`, `Newtons`, `kPa`), NEVER hand-wave with cellular biochemistry (`molecules`, `proteins`, `chemical buffers`, `permeates every cell`), NEVER write `'acting like...'`, and NEVER use $10 formal words (`dissent`, `unanimous`, `vessel`, `global nuclear exchange`, `nuclear holocaust`, `vigil`)!
2. 3-ANGLE VIRAL TOURNAMENT:
   - Angle A (Asymmetric 1v1 Clash / Direct Confrontation): Frame around "Entity A vs Entity B" with a vivid, easy-to-picture physical clash.
   - Angle B (Counter-Intuitive Paradox / Hidden Mechanism): Expose why what everyone assumes happens is flipped upside down by physical reality.
   - Angle C (In-Medias-Res Human / Survival / Social Room Moment): Drop straight into the split-second room tension, crisis, or rescue moment.
3. SELECT THE #1 WINNING BLUEPRINT FOR >70% STAYED & 100%+ APV (AUTHENTIC SPOKEN CREATOR VOICE AT 0.0s):
   - `winning_title`: Search-optimized High-CTR Title in {target_lang_display} explicitly naming the primary entities + visceral power-reversal `"Why / Por Que"` or `"[Entity A] vs [Entity B]: [Visceral Stakes]"` (6–10 words, under 55 chars, ZERO hashtags, ZERO `PSI`/`Newtons`!).
   - BROAD-APPEAL ENTITY TRANSLATION IN TITLE (NO OBSCURE JARGON HEADERS): Never start `winning_title` with obscure scientific or academic jargon ("Brinicle", "Pratfall Effect")—translate into mass-appeal everyday words.
   - `selected_hook_archetype`: Choose the #1 best spoken hook archetype for Scene 1 (`1. Instant Lethal Action / Micro-Mechanism Strike (PRIORITY #1 when Title asks Why/Por Que)`, `2. Spoken 2-Entity Paradox / Role-Reversal Clash`, `3. In-Medias-Res Human Crisis / Staged Social Line`, or `4. Counter-Intuitive "Why / Por Que" Paradox (ONLY when Title does NOT start with Why/Por Que)`).
   - `scene_1_hook_0s`: 6–11 words in {target_lang_display} sounding 100% like a real human YouTuber talking out loud (use contractions in English like `it's`, `there's`, `doesn't`, `don't`; colloquial spoken cadence in pt-BR like `pra`, `pro`, `bicho`, `na hora`):
     * 🚨 GOLDEN TITLE-VS-HOOK COMPLEMENTARITY LAW (ZERO QUESTION ECHO): If `winning_title` starts with `"Why / Por Que / Como / How / Tại sao"`, `scene_1_hook_0s` is **STRICTLY FORBIDDEN from repeating or re-asking the `"Why / Por que..."` question!** When a viewer sees a `"Why / Por Que"` title, the first `0.0s` of audio MUST immediately drop them into the **shocking physical action, lethal mechanism strike, or upside-down survival paradox happening on screen right now**!
     * MANDATORY 2-ENTITY CLASH HOOK RULE: Whenever the topic or `winning_title` pits two entities against each other (`Entity A vs Entity B`), `scene_1_hook_0s` MUST explicitly name BOTH `Entity A` AND `Entity B` right in Scene 1! NEVER hide the second entity until Scene 2 (FORBIDDEN: `"You'd think a massive whale wouldn't fear anything!"`), and NEVER use weak Yes/No questions (`"Did you know...?"`, `"Have you ever seen...?"`, `"Você sabia...?"`, `"Você já viu...?"`)!
     * Wildlife: `"One adult orca enters the bay, and every great white shark bolts for miles."` / `"Dez lobos famintos cercam esse alce na neve—mas um chute dele quebra crânios na hora!"` / `"Swallowing a venomous box jellyfish is instant death—unless you're a sea turtle."` (NEVER flat stock captions `"A turtle swims up to..."`)
     * Deep Sea / Mysteries: `"Swimming into this dark pool on the ocean floor paralyzes a shark in seconds."` / `"Nadar dentro dessa mancha escura no fundo do mar congela um tubarão na hora!"` / `"Two miles down, a colossal squid's rotating hooks rip straight into a hunting sperm whale."`
     * Psychology / Social: `"In an FBI interrogation room, that harmless paper water cup is a hidden lie detector."` / `'When someone drops a rude joke and says, "Relax, I\'m just joking," don\'t get defensive.'`
     * History / What-If: `"Two live hydrogen bombs fell straight out of a broken US bomber onto North Carolina."` / `"In 1962, two Soviet officers turned their nuclear launch keys—and one man said no."`
     * Animal Rescue: `"For three freezing weeks, this little stray dog refused to leave an empty park bench."` (Explicitly name the animal in 8–12 words—NEVER start with `"He/She/His..."`!)
   - `scene_5_concrete_escalation` (ZERO 'X IS OVERRATED' RANTS & ZERO 'MOST PEOPLE ASSUME...' SERMONS): Design Scene 5 as pure narrative escalation—for `"What If..."` history topics, play out the immediate 60-minute physical chain reaction (`"If that wire hadn't snapped, that blast wipes out the base—and triggers all-out nuclear war within an hour."`); for Psychology, explain why taking the bait makes you look defensive to everyone watching; for Wildlife/Deep Sea/Rescue, escalate the physical counter-mechanism or emotional turning point!
   - `scene_8_infinite_echo_loop`: Save the concrete physical aftermath, quiet room reaction, or sharpest ironic physical twist for this VERY LAST SCENE! NEVER write preachy essay endings (`"Real social power belongs to..."`, `"That's why the deepest places belong to..."`, `"the hunter becomes the meal"`, `"venom never reaches the heart"`, `"deixando só o silêncio... pra trás"`).
   - `counterfactual_debate_comment`: 12–22 words in {target_lang_display} ending with `(1) ... or (2) ... 👇` that was NOT already answered inside the video (ZERO 'Subscribe/Inscreva-se' spam!).

Return ONLY valid JSON:
{{
  "entity_mechanism_profile": "Deep profile of the specific entity's unique macroscopic structure, anatomical weapon, historical decision rule, or real-room social dynamic in plain $1 words",
  "mined_hard_metrics": ["plain $1 spoken physical mechanism 1", "plain $1 spoken physical mechanism 2", "plain $1 spoken physical mechanism 3"],
  "angle_a_clash": "1-sentence pitch for Angle A",
  "angle_b_paradox": "1-sentence pitch for Angle B",
  "angle_c_visceral": "1-sentence pitch for Angle C",
  "winning_angle": "Angle A | Angle B | Angle C — plus 1-sentence reason why it sounds most gripping",
  "primary_entity": "Bare 1-2 word English name of the primary subject/entity",
  "winning_title": "High-CTR Searchable Title in {target_lang_display} (28-55 chars naming exact entities + visceral stakes or Power-Reversal 'Why / Por Que', 1-2 emojis, ZERO hashtags, ZERO PSI/Newtons/N/kPa/kilotons, ZERO pronoun-blind 'He/She...')",
  "selected_hook_archetype": "1. Instant Lethal Action / Micro-Mechanism Strike | 2. Direct Spoken Paradox / Clash | 3. In-Medias-Res Human Crisis / Staged Social Line | 4. Counter-Intuitive 'Why / Por Que' Hook (ONLY if Title is not a Why question)",
  "scene_1_hook_0s": "6-12 word spoken creator hook in {target_lang_display} delivering an immediate physical shock or life-or-death paradox at 0.0s (STRICTLY FORBIDDEN from repeating the 'Why / Por que' question if `winning_title` already starts with 'Why / Por que'; ZERO passive 'X sat in a...', ZERO 'Look closely at...', ZERO weak Yes/No questions)",
  "act_2_concrete_proof": "Direct spoken play-by-play action for Scenes 2-3 using contractions and $1 words (ZERO 'Studies show...', ZERO 'Most people assume...', ZERO biochemistry molecules/proteins)",
  "scene_4_mid_video_15s_twist": "Sharp 15-second reversal twist for Scene 4 in natural spoken creator voice",
  "scene_5_concrete_escalation": "Pure narrative/physical escalation or 'What If' 60-minute domino chain reaction for Scene 5 (ZERO 'X is overrated' rants, ZERO 'Most people assume...' sermons)",
  "act_4_ultimate_payoff": "For Psychology: exact spoken counter-line in quotes ('\"...\"') + immediate room shift. For What-If History: immediate counterfactual vs real-world contrast. For Wildlife/Mysteries/Rescue: concrete physical climax for Scenes 6-7",
  "scene_8_infinite_echo_loop": "100% grammatically COMPLETE concrete physical aftermath, room reaction, or ironic physical twist on the final scene in {target_lang_display} (ZERO self-help slogans, ZERO poetic silence clichés, ZERO 'That is how/why...')",
  "counterfactual_debate_comment": "12-22 word Counterfactual 50/50 Debate Question in {target_lang_display} ending with (1) or (2) 👇 and ZERO Subscribe promo",
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
                    "🔥 STAGE-1 SHOWRUNNER WINNING VIRAL BLUEPRINT (MANDATORY SPOKEN CREATOR EXECUTION):\n"
                    f"- Entity Anatomy & $1 Mechanism Profile: {bp_data.get('entity_mechanism_profile', '')}\n"
                    f"- Mined Plain-$1 Physical Mechanisms: {json.dumps(bp_data.get('mined_hard_metrics', []), ensure_ascii=False)}\n"
                    f"- Winning Angle: {bp_data.get('winning_angle', '')}\n"
                    f"- Primary Entity: {bp_data.get('primary_entity', '')}\n"
                    f"- Recommended High-CTR Searchable Title (NO hashtags): {bp_data.get('winning_title', '')}\n"
                    f"- Selected Hook Archetype: {bp_data.get('selected_hook_archetype', '')}\n"
                    f"- Mandatory Scene 1 Spoken Hook (0.0s): {bp_data.get('scene_1_hook_0s', '')}\n"
                    f"- Mandatory Act 2 Spoken Action (Scenes 2-3): {bp_data.get('act_2_concrete_proof', '')}\n"
                    f"- Mandatory Scene 4 Mid-Video 15s Twist: {bp_data.get('scene_4_mid_video_15s_twist', '')}\n"
                    f"- Mandatory Scene 5 Concrete Escalation / What-If Chain Reaction: {bp_data.get('scene_5_concrete_escalation', '')}\n"
                    f"- Mandatory Act 4 Payoff (Scenes 6-7): {bp_data.get('act_4_ultimate_payoff', '')}\n"
                    f"- Mandatory Final Scene Concrete Mic-Drop (Save sharpest ironic twist or physical aftermath for last — ZERO essay morals!): {bp_data.get('scene_8_infinite_echo_loop', '')}\n"
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
        prose_dna_block = ""
        try:
            from app.services.niche_manager import build_million_view_showrunner_prose_dna
            prose_dna_block = "\n" + build_million_view_showrunner_prose_dna(channel_niche, language=language) + "\n"
        except Exception:
            pass
        polish_prompt = f"""You are an Executive YouTube Shorts Viral Voice Doctor (Zack D. Films / Casual Geographic / Charisma on Command / IFinity What / The Dodo / Você Sabia? Showrunner) and Peak Visual Director.
Review and upgrade the following draft script for Topic: "{topic}" (Niche: '{channel_niche}', Language: {language} / {target_lang_display}).
{lang_instruction}{notes_ctx}
{prose_dna_block}
{deep_blueprint_block}

CURRENT DRAFT SCRIPT JSON:
{draft_script.model_dump_json(indent=2)}

YOUR EXECUTIVE VIRAL VOICE & RETENTION PUNCH-UP MANDATE (UPGRADE EVERY STIFF OR AI-SMELL LINE WHILE KEEPING STRICTLY 7 OR 8 COMPLETE STANDALONE SCENES & 95-122 TOTAL WORDS — TRIM ANY BLOATED 145+ WORD DRAFT DOWN TO 98-120 WORDS!):
1. THE KITCHEN-TABLE SPOKEN CREATOR VOICE AUDIT (HIGHEST PRIORITY — SCRUB ALL AI-SMELL & TEXTBOOK PROSE):
   - Read every scene's `narration` out loud. Every single scene MUST be a **100% standalone complete main-clause sentence** (NEVER make a scene a standalone `"Because..."` / `"Porque..."` / `"Antes de..."` subordinate fragment, and NEVER tack a broken `"—and five seconds before..."` noun fragment onto Scene 1!).
   - MANDATORY SPOKEN CONTRACTIONS & CADENCE ACROSS BOTH FIRST HALF AND SECOND HALF (SCENES 4–7/8):
     * If Language is English (`en`): Ensure the script naturally weaves **5–6 spoken contractions distributed across BOTH the opening AND closing scenes** (`it's`, `doesn't`, `don't`, `can't`, `you'd`, `didn't`, `wouldn't`, `couldn't`, `wasn't`, `that's when`, `they're`, `he's`, `would've`, `hadn't`) and 100% plain `$1` everyday words!
     * If Language is Brazilian Portuguese (`pt-BR`): Ensure every scene flows in gritty, colloquial Brazilian YouTuber `'conversa de bar'` Portuguese (`dentro d'água`, `pra`, `pro`, `tá`, `bicho`, `na hora`, `só que`, `em vez de`—do NOT overuse `num piscar de olhos`). IMMEDIATELY REWRITE any taxonomy-comparison Scene-1 openers (`Diferente de outros felinos...`, `Ao contrário de outros...`, `Enquanto outros felinos...`, `Felinos comuns`), worn phrases (`no elemento dele`, `em seu elemento`), formal written enclitics (`imobilizá-lo`, `matá-lo`, `esmagá-lo`), TV-dub hardware/medical clichés (`golpe cirúrgico`, `alicate hidráulico`, `desligando o sistema nervoso`), or stiff similes (`atravessa o osso como papel`)!
   - ZERO AI-SMELL / ESSAY TELLS — IMMEDIATELY REWRITE IF FOUND ANYWHERE IN THE DRAFT:
     * Front-loaded participial openers (`Equipped with...`, `Armed with...`, `Boasting...`, `Ostentando...`, `Trapped...`, `Cornered...`, `Surrounded...`, `Reaching down into...`, `Kneeling down...`) and generic taxonomy contrast setups (`Most predators go for...`, `Unlike other big cats...`, `Diferente de outros felinos...`, `Enquanto outros...`) -> Rewrite into active subject-verb spoken action naming the primary subject at word 1!
     * Clinical toxin jargon (`neurotoxins`, `neurotoxin`, `hemotoxin` -> say `deadly venom`!), hardware/medical/sports-gear clichés on animals (`built-in shin guards`, `built-in armor`, `like a riot shield`, `body armor`, `pile-driver`, `battering ram`, `surgical strike`, `golpe cirúrgico`, `hydraulic pliers / press`, `alicate hidráulico`, `no elemento dele`), and clunky `'acting like...'` similes -> Rewrite into raw physical biology & speed (`thick overlapping leg scales so tough fangs slide right off`, `pushing stinging tentacles straight down like harmless spaghetti`).
     * Preachy `'Most people assume...'` / `'What most people miss...'` / `'Muita gente acha que...'` / `'Studies show...'` sermons -> Cut the sermon and drop straight into the real action or room dialogue!
     * Cellular biochemistry or clinical anatomy hand-waving (`tiny cells`, `special flexible fats`, `cell membranes`, `molecules`, `organic molecules`, `proteins`, `chemical buffers`, `air bladders`, `swim bladders`, `cells are packed with`, `permeates every cell` — say `trapped air pockets` instead of `air bladders`!) -> DELETE ALL CELLULAR CHEMISTRY and rewrite into plain `$1` macroscopic reality (`hollow steel submarines full of trapped air` vs `water-filled jelly bodies where water inside pushes outward just as hard as the ocean pushes inward`).
     * Stiff past-tense history textbook phrasing (`"In [Month/Year], ..."`, `"Back in ..."`, `"sat one key turn away"`, `"unleashing a nuclear war"`, `"Under ... rules"`, `"launching that weapon required"`, `"required unanimous agreement"`, `"senior officer"`, `"nuclear exchange"`, `"atomic retaliation"`, `kiloton`, `megaton`) or front-loaded `-ed` setups (`"Trapped..."`, `"Cornered..."`) -> For History / What-If scripts, open Scene 1 with a high-voltage active action naming the person/event (`"Two live hydrogen bombs fall out of a broken US bomber—and don't explode."`), tell Scenes 2–4 in **URGENT HISTORICAL PRESENT TENSE** (`"It's [Year], and..."`, `"Inside, the commander's screaming that..."`, `"They've loaded the weapon, and it takes all three keys to fire."`), and play out the `"What If"` beat with crisp `"would've"` contractions (**6+ contractions total**)!
     * Unnamed pronoun openers in `title` or `Scene 1` (`"He wouldn't leave..."`, `"He Guarded..."`, `"She waited..."` — ALWAYS name `"this stray dog"` / `"this tiny puppy"` explicitly in 8–12 words right at 0.0s!) or novelistic mid-script prose in Animal Rescue (`"Deep down in the dark concrete tunnel, a tiny muffled whine echoed..."`, `vigil`, `peered down into`, `didn't budge`, `exact same`) -> Keep conversational spoken contractions flowing through Scenes 4–7 too (`"That's when she heard tiny cries coming from the dark water..."`, `"Turns out..."`, `"Now he's curled up on her warm rug beside that little survivor every night."`)!
2. GOLDEN 0.0s SPOKEN HOOK IN `SCENE 1` (6 TO 12 WORDS) & CLEAN TITLE (UNDER 58 CHARS, ZERO `#HASHTAGS`, ZERO TITLE-TO-HOOK QUESTION ECHO):
   - Keep `title` under 58 characters with ZERO `#hashtags`, ZERO unnamed pronoun openers (`"He Guarded..."` -> `"Stray Dog Guarded..."`), and ZERO dry physics units (`PSI`, `Newtons`, `N`, `kPa`, `kiloton`).
   - 🚨 ZERO TITLE-TO-HOOK ECHO (MANDATORY): If `title` starts with `"Why..."`, `"Por que..."`, `"Como..."`, or `"How..."`, `scenes[0].narration` and `hook` MUST NOT start with `"Why / Por que / Como / How"` or repeat the title question! Rewrite Scene 1 immediately into a punchy 6–12 word declarative action/paradox sentence that drops the viewer right into the lethal physical event at 0.0s!
   - Scene 1 (`scenes[0].narration` and `hook`, 6–12 crisp spoken words) MUST explicitly name the primary entity/animal/situation (and BOTH entities if the title pits `Entity A vs Entity B`)—NEVER lead Scene 1 with `"Diferente de outros..."` / `"Unlike other..."`, NEVER lead with `"In [Month/Year], ..."`, NEVER lead with `"He/She/It..."`, and NEVER use weak Yes/No questions (`"Did you know...?"`, `"Have you ever...?"`, `"Você sabia...?"`)!
   - ZERO MULTI-ROUND BAIT-AND-SWITCH: Keep 100% of scenes locked on the exact subject promised in `title`.
3. END RIGHT ON THE PHYSICAL TWIST OR CONCRETE ACTION — ZERO PASSIVE STANDING OR SUMMARY MORALS:
   - Check the final scene (`scenes[-1].narration`):
     * NEVER put the sharpest ironic physical twist in Scene 6 and tack an AI summary moral onto Scene 7 (`"It's the ultimate survival trick..."`, `"It's a perfect natural design"`).
     * NEVER end on passive standing-around filler (`"just stands there, calm as ever"`, `"ready for its next meal"`)—end on a vivid physical action (`"swallows the venomous snake whole and walks off without a single scratch"`)!
     * IMMEDIATELY DELETE & REWRITE any self-help guru slogan (`Real social power belongs to...`), wildlife-TV cliché (`the hunter becomes the meal`, `just became a snack`, `venom never reaches the heart`, `That is how a...`), or poetic Brazilian ending (`deixando só o silêncio... pra trás`, `O Pantanal pune quem falha`).
     * Ensure `full_narration` is the exact space-joined concatenation of all scenes' upgraded `narration` strings (`" ".join(s.narration for s in scenes)`). ZERO spoken Subscribe/Follow/Comment CTA!
4. SCENE 4 MID-VIDEO CLIMAX TWIST (14.0s–20.0s):
   - Ensure Scene 4 (`climax_scene_id`) delivers a sharp reversal twist in conversational spoken prose with an uppercase HUD `overlay_badge` in {target_lang_display}.
5. DISTILL 5 DUAL-TRACK YOUTUBE PEAK SEARCH QUERIES (`hook_search_queries`):
   - Provide 5 distinct, concrete English search queries (3–5 words each): at most 1–2 real-camera encounter queries (`"[Entity A] attacking [Entity B]"`) ONLY if the script is a 2-entity duel, and 3–5 **100% CLEAN SINGLE-ENTITY REAL-ACTION QUERIES** (`"[Single Entity] + [Real Physical Action / Environment]"`, ZERO `"vs"`).
6. SELF-CONTAINED, CANONICAL-ENTITY-LOCKED BODY VISUAL QUERIES (`visual_keyword`, `stock_query`, `visual_prompt`):
   - Every scene's `visual_keyword` (3–5 everyday English words) and `stock_query` (2–4 everyday English words, ZERO `"vs"`) MUST be 100% self-contained and include the **COMPLETE CANONICAL SPECIFIC NAME** of the entity acting in that scene (e.g., write `"harpy eagle talons closeup"`, `"harpy eagle flying jungle"`, `"three toed sloth hanging"` in BOTH `visual_keyword` and `stock_query`).
   - 🚨 ZERO GENERIC HYPERNYM DOWNGRADE IN `stock_query` OR `visual_keyword`: NEVER shorten or simplify a specific multi-word species/entity into a generic category noun like bare `"eagle"`, `"bird"`, `"bird of prey"`, `"raptor"`, `"predator"`, `"hunter"`, `"prey"`, `"animal"`, or `"creature"`, and NEVER use literal metaphor objects (`"steel clamps"`), side-props, academic/Latin jargon, pronouns, or `@tool` names!
7. COUNTERFACTUAL 50/50 DEBATE COMMENT (`pinned_comment`):
   - Ensure `pinned_comment` is a 12–22 word Counterfactual Escalation / Next-Opponent 50/50 Debate Question ending with `(1) ... or (2) ... 👇` (never asking what the video already answered, ZERO Subscribe spam).

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
                if doc_10min and len(doc_10min.scenes) >= 24:
                    if doc_10min.primary_entity:
                        self._last_primary_entity = doc_10min.primary_entity
                    return doc_10min
            except Exception as e_10m:
                logger.warning(f"[ContentAgent] 10-Min LongForm Generator fallback: {e_10m}")
            duration_spec = "8 TO 10.5 MINUTES CINEMATIC 16:9 WIDESCREEN DOCUMENTARY (36 TO 44 SCENES ACROSS 10 CHAPTERS, 1,350 TO 1,600 WORDS TOTAL — SCENE 1 COLD-OPEN HOOK 12-18 WORDS, BODY SCENES 34-42 WORDS EACH)"
            word_range_en = "1,350 to 1,600 words (8 to 10.5 minutes Widescreen 16:9 Documentary across 36 to 44 rich scenes; Scene 1 Cold-Open Hook MUST be 12-18 words; Body scenes MUST be 34-42 words each)"
        else:
            duration_spec = "34 TO 42 SECONDS GOLDEN HIGH-APV DURATION (7 TO 8 COMPLETE SPOKEN SCENES, 95 TO 130 WORDS TOTAL — SCENE 1 SPOKEN HOOK 6-13 WORDS WITH NATURAL CREATOR CADENCE & CONTRACTIONS, SMOOTH CONVERSATIONAL FLOW)"
            word_range_en = "95 to 130 words (34-42s High-APV Golden Duration across 7-8 rich, complete spoken scenes; Scene 1 Hook MUST be 6-13 words ~1.6-2.5s)"

        prompt = f"""You are an elite YouTube {'Widescreen 16:9 Documentary' if is_long_form else 'Shorts'} Master Showrunner, Spoken Creator Voice Architect (Zack D. Films / Casual Geographic / Charisma on Command / IFinity What / The Dodo / Você Sabia?), and Viral Retention Director for the channel '{niche_profile.get('name')}' (niche: '{channel_niche}').
Topic: '{topic}'
Target Language: {language} ({target_lang_display})
Video Format Mode: {'LONG-FORM 16:9 DOCUMENTARY (30-36 scenes across 10 Chapters, 1,500-1,750 words, ~10 minutes, NO #Shorts tag in title)' if is_long_form else 'VERTICAL 9:16 YOUTUBE SHORTS (7-8 scenes, 95-130 words, ~38 seconds, ZERO #hashtags in title)'}
{lang_instruction}
{regional_hook_instruction}
{director_block}
{channel_memory_block}
{deep_blueprint_block}
{niche_instructions}
═══════════════════════════════════════════════════════════
MASTER-CLASS YOUTUBE {'LONG-FORM 10-CHAPTER DOCUMENTARY (9–10.5 MINUTES | 1,500–1,750 WORDS | 30–36 SCENES)' if is_long_form else 'SHORTS ARCHITECTURE (34–42 SECONDS | 95–130 WORDS | AUTHENTIC SPOKEN YOUTUBER VOICE)'}
YouTube rewards TWO metrics simultaneously:
1. Viewed vs. Swiped-Away > 75%: Achieved by explicitly naming the primary entity/situation in `title` (under 58 chars, ZERO `#hashtags`, ZERO `PSI`/`Newtons`) AND opening `Scene 1 Hook` ({'10–16 words' if is_long_form else '6–13 words'}) with a natural spoken creator hook (using contractions in English / colloquial cadence in pt-BR — NEVER passive `"X sat in a..."`, NEVER flat stock captions, and NEVER `"Look closely at..."`).
2. High Retention + Authentic Spoken Creator Voice ({'1,500–1,750 words across 30–36 widescreen scenes (10 Chapters), 48–58 words per body scene' if is_long_form else '95–130 words, 34–42s across 7–8 scenes, 12–18 words per body scene'}):
   - Write 100% like a real human YouTuber talking into a mic across a kitchen table! In English, naturally weave in **at least 3–5 spoken contractions** (`it's`, `doesn't`, `don't`, `can't`, `you'd`, `didn't`, `would've`, `hadn't`, `wasn't`, `they're`, `that's`, `he'd`) and plain `$1` words. In `pt-BR`, use colloquial spoken Brazilian YouTuber Portuguese (`pra`, `pro`, `tá`, `bicho`, `na hora`, `num piscar de olhos`, `só que`, `em vez de` — ZERO formal enclitics like `imobilizá-lo`).
   - {'For LONG-FORM 16:9 mode: Structure the 16–22 scenes across 5 gripping Documentary Chapters using one of the 3 Competitor Million-View Blueprints:\n     * (A) `@FossilVideos` Gauntlet / Survival Tier-List (Wildlife): Chapter 1 (Scenes 1–3: Cold Open & Contender Stats), Chapter 2 (Scenes 4–7: Round 1 & Round 2 Challengers), Chapter 3 (Scenes 8–12: Round 3 Armored/Venom Threat & Mid-Point Twist), Chapter 4 (Scenes 13–17: Round 4 Pack/Apex Confrontation), Chapter 5 (Scenes 18–20: The Final Boss & Ultimate Verdict).\n     * (B) `@disastersexplained1` "Every X Record/Type Explained" (Mysteries/Ocean): Open Scene 1 with a 2nd-person sensory danger hook ("You notice a calm line on the water... You think you still have time. That is the mistake."), then escalate Chapter 1 -> Chapter 5 from the strangest warning sign to the deadliest unbroken record.\n     * (C) `@IFinity_What` "What If YOU..." 2nd-Person Timeline Escalation (Alternate History & Psychology): Frame directly around "YOU" escalating from Chapter 1 (Minute 1: The Superpower/Control Illusion) -> Chapter 2 (Hour 12: The First Hidden Glitch) -> Chapter 3 (Day 3: Systemic/Neural Overload) -> Chapter 4 (Day 7: Complete Collapse) -> Chapter 5 (The Final Aftermath).\n     * Put clean Chapter HUD badges like "[ // CHAPTER 1: THE SILENT TRAP // ]" or "[ // ROUND 1: THE SPEED TEST // ]" in `overlay_badge` ONLY on the first scene of each chapter (Scenes 1, 4, 8, 13, 18) and `null` on all other scenes!' if is_long_form else 'For SHORTS 9:16 mode: Keep 100% of all 7–8 scenes locked on the exact 1v1 duel or single phenomenon promised in the `title`! NEVER split a 35-second Short into "Round One / Round Two / Round Three" or switch to a 3rd unannounced opponent!'}
   - Every scene MUST be a 100% grammatically complete spoken sentence. NEVER chop one sentence across two scenes!
═══════════════════════════════════════════════════════════
1. THE MANDATORY 5-ACT SPOKEN CREATOR STORYTELLING BLUEPRINT (target: {word_range_en}):
   - DURATION & SCENE COUNT: {duration_spec}.
   - DEEP ENTITY PROFILING & $1 SPOKEN IMAGERY (CRITICAL FIRST STEP):
     * Before writing Scene 1, analyze the specific entity/phenomenon using `Entity Anatomy & $1 Mechanism Profile` from Stage 1.
     * Express every mechanism in **plain $1 spoken words a friend uses across a table**—ZERO dry physics units (`PSI`, `Newtons`, `kPa`), ZERO biochemistry hand-waving (`molecules`, `proteins`, `chemical buffers`), ZERO `'acting like...'`, ZERO `'Equipped with...'`, and ZERO `$10` formal words (`dissent`, `unanimous`, `vessel`, `global nuclear exchange`, `nuclear holocaust`, `vigil`)!
   - YOU MUST EXECUTE ALL 5 ACTS SMOOTHLY IN ORDER across {'16 to 22 scenes (5 Documentary Chapters)' if is_long_form else '7 to 8 scenes'}:

     * ACT 1 — THE 0.0s SPOKEN CREATOR HOOK (Scene 1 | 6 to 12 words MAX — COMPLEMENT THE TITLE, NEVER ECHO IT!):
       - MANDATORY 2-ENTITY RULE: If `title` or the topic pits two entities against each other (`Entity A vs Entity B`), Scene 1 MUST explicitly name BOTH `Entity A` AND `Entity B` right in Scene 1!
       - 🚨 ZERO TITLE-TO-HOOK QUESTION ECHO: If `title` starts with `"Why / Por Que / Como / How"`, Scene 1 is **STRICTLY FORBIDDEN from starting with `"Why / Por que / Como / How"` or repeating the title question**! Instead, open right at 0.0s with an immediate physical strike, lethal trap, or upside-down survival paradox:
         1. Instant Lethal Action / Trap Strike (PRIORITY #1 when `title` asks `"Why / Por Que"`): `"Swimming into this dark pool on the ocean floor paralyzes a shark in seconds."`, `"Nadar dentro dessa mancha escura no fundo do mar congela um tubarão na hora!"`, `"Dez lobos famintos cercam esse alce na neve—mas um chute dele quebra crânios na hora!"`
         2. Spoken Wildlife / Deep-Sea Paradox (`"Swallowing a venomous box jellyfish is instant death—unless you're a sea turtle."`, `"A onça-pintada não sufoca o jacaré-açu—ela fura o crânio blindado dele na hora."`, `"Two miles down, a colossal squid's rotating hooks rip straight into a hunting sperm whale."`)
         3. Active Split-Second Historical / Mystery Crisis (`"Two live hydrogen bombs fell straight out of a broken US bomber onto North Carolina."`, `"In an FBI interrogation room, that harmless paper water cup is a hidden lie detector."`)
         4. Counter-Intuitive "Why / Por Que" Hook (ONLY when `title` is declarative `"A vs B"`, NEVER when `title` already starts with `"Why / Por Que"`!)
       - FORBIDDEN: Repeating the `title` question in Scene 1, passive `"X sat in a..."` scenery, `"locked in a deadly fight"` clichés, Wikipedia spoilers, dry units (`PSI`, `Newtons`), vague `"Look closely at..."` pointers, `'Most people assume...'`, or weak Yes/No questions (`"Did you know...?"`, `"Have you ever...?"`, `"Você sabia...?"`).

     * ACT 2 — DIRECT PLAY-BY-PLAY ACTION & PLAIN-$1 MECHANISM (Scenes 2 – 3):
       - STRICT BAN ON ACADEMIC / SERMON OPENERS: NEVER start Scene 2 or 3 with `"Studies show..."`, `"Most people assume..."`, `"What most people miss..."`, `"Muita gente acha que..."`, or `"Equipped with..."`! Drop directly into the action or dialogue:
         * For Psychology / Social Dynamics: Show the exact room play-by-play in conversational contractions ('Right after taking a subtle dig at you, they smile so if you push back, you look way too sensitive.').
         * For Mysteries / Deep Sea: Stick 100% to macroscopic physical reality (`sealed hollow steel submarines full of air` vs `gelatinous bodies made almost entirely of water with zero air pockets to crush`—ZERO `molecules`/`proteins`/`chemical buffers`!).
         * For History / What-If: Tell rules through human action with contractions (`"Normally two officers' keys would've launched on the spot—except the flotilla boss happened to be aboard that sub, so they needed his third key too."`—NEVER `"Under naval rules, launching required unanimous agreement"`).
         * For Wildlife / Animal Rescue: Explain how that animal's exact body part works in plain spoken words, or tell the rescue story with raw, simple emotion (`"He didn't bark or chase the car—he just curled up on that blanket and waited."`).

     * ACT 3 — THE 15-SECOND MID-VIDEO TWIST (Scene 4) & PURE NARRATIVE ESCALATION / WHAT-IF CHAIN REACTION (Scene 5):
       - **Scene 4 (`climax_scene_id` — The 15-Second Plot Twist)**: Flip the situation with a sharp spoken twist and set `overlay_badge` on Scene 4 in {target_lang_display} (e.g., `"[ // THE 15-SECOND TWIST // ]"` for EN or `"[ // A VIRADA // ]"` for pt-BR).
       - **Scene 5 (`CONCRETE ESCALATION / WHAT-IF DOMINO SCENE` — ZERO 'X IS OVERRATED' RANTS)**: Escalate the story naturally without preachy commentary:
         * If the topic asks `"What If..."` (History/Science): Dedicate Scenes 5–6 to playing out the immediate 60-minute physical domino chain reaction in plain `$1` words (`"If he'd said yes, that nuclear blast wipes out the carrier group on the spot—and triggers full-scale nuclear strikes within an hour."`)!
         * For Psychology: Explain why arguing or snapping back hands them control of the room.
         * For Wildlife / Deep Sea / Rescue: Escalate the physical counter-move or turning point.

     * ACT 4 — THE SURGICAL COUNTER-MOVE / DECISIVE PAYOFF (Scenes 6 – 7):
       - Deliver the full climax payoff:
         * For Psychology: Give the viewer an exact, casual 1-sentence spoken counter-line inside quotation marks (`"..."`) to say out loud on the spot (e.g., 'Instead, look them dead in the eye with zero smile and ask, "Wait—what\'s the funny part?"').
         * For Wildlife / Rescue / Mysteries / History: Deliver the decisive physical resolution or real-world turn.

     * ACT 5 — CONCRETE MIC-DROP FINALE (Final Scene 7 or 8 — SAVE SHARPEST TWIST OR PHYSICAL AFTERMATH FOR LAST!):
       - ZERO SPOKEN OUTRO CTA: NEVER say "Subscribe", "Follow", "Leave a comment", "Inscreva-se", or "Đăng ký kênh"!
       - End RIGHT on a concrete physical action, quiet room shift, or sharpest ironic physical twist! STRICTLY FORBIDDEN: NEVER end on self-help slogans (`"Real social power belongs to..."`), poetic essays (`"That's why the deepest places belong to..."`, `"They never fight the crushing abyss"`, `"It's a high-stakes game of..."`, `"It's a brutal, silent end"`), wildlife-TV clichés (`"the hunter becomes the meal"`, `"venom never reaches the heart"`, `"That is how a..."`, `"domina o terreno porque conhece..."`, `"transforma esse gigante em..."`), or Brazilian proverbs/school-essay fillers (`"No fim das contas"`, `"Muita gente acha que"`, `"deixando só o silêncio... pra trás"`, `"O Pantanal pune quem falha"`).

2. HIGH-CTR SEARCHABLE TITLE VS. SCENE 1 HOOK RULES (ZERO `#HASHTAGS`, UNDER 58 CHARS, ZERO QUESTION ECHO):
   - Keep `title` clean, punchy, under 58 characters with 1–2 emojis, ZERO `#hashtags`, and ZERO dry physics units (`PSI`, `Newtons`, `kPa`).
   - SCENE 1 SPOKEN HOOK ARCHETYPES (6–12 WORDS MAX — NEVER ECHO A "WHY / POR QUE" TITLE QUESTION IN SCENE 1, NAME BOTH ENTITIES IF 'A VS B'):
     * Archetype 1 (PRIORITY #1 when `title` starts with `"Why / Por Que"` — Instant Lethal Action / Trap Strike): `"Swimming into this dark pool on the ocean floor paralyzes a shark in seconds."` / `"Nadar dentro dessa mancha escura no fundo do mar congela um tubarão na hora!"` / `"Dez lobos famintos cercam esse alce na neve—mas um chute dele quebra crânios na hora!"`
     * Archetype 2 (Spoken Wildlife / 2-Entity Paradox): `"Swallowing a venomous box jellyfish is instant death—unless you're a sea turtle."` / `"A onça-pintada não sufoca o jacaré-açu—ela fura o crânio blindado dele na hora."`
     * Archetype 3 (Active Split-Second Historical / Forensic Crisis): `"In an FBI interrogation room, that harmless paper water cup is a hidden lie detector."` / `"Two live hydrogen bombs fell straight out of a broken US bomber onto North Carolina."`
     * Archetype 4 (Staged Real-Room Social Line): `'When someone drops a rude joke and says, "Relax, I\'m just joking," don\'t argue.'`
   - NEVER repeat the `title` question in Scene 1, NEVER start with passive `"X sat in a..."`, `"locked in a deadly fight"`, vague `"Look closely at..."`, textbook definitions, greetings, or weak Yes/No questions (`"Did you know...?"`, `"Have you ever...?"`).
   - thumbnail_text MUST be STRICTLY 2 to 3 ULTRA-PUNCHY WORDS MAX in ALL CAPS with ZERO emojis and ZERO dry physics units (e.g. "TOO EASY?", "ZERO AIR", "ONE VOTE", "4 SECONDS MAX").

3. SEAMLESS INFINITE LOOP ENDING (NO SPOKEN OUTRO CTA):
   - NEVER include "Subscribe", "Like", "Follow", "Leave a comment", "Inscreva-se", or "Đăng ký kênh" in ANY scene's spoken narration!
   - Every single scene's spoken narration MUST be a 100% grammatically complete sentence with proper closing punctuation. NEVER end any scene with dangling prepositions or conjunctions ('because', 'which', 'and', 'so', 'that', 'but', 'with').

4. CONTEXT-ANCHORED, CANONICAL-ENTITY-LOCKED VISUAL QUERIES (`visual_keyword` & `stock_query` — STRICTLY FOLLOW SECTION 9 `YAML VIDEO SEARCH & DUAL-TRACK QUERY STRATEGY` ABOVE):
   - Downstream visual agents search YouTube (`visual_keyword`, 3–5 English words) and Pexels/Pixabay Stock APIs (`stock_query`, 2–4 English words) for each scene independently:
     * `visual_keyword` (YouTube Dual-Track search in Everyday Creator Vocabulary): Follow the active niche's YAML `Single-Entity Body Scene Rule` and `YAML Clean Single-Entity Real-Action Query Examples` in Section 9 above! For every body scene focusing on ONE contender, weapon, mechanism, or environment, write ONLY **`[Full Canonical Specific Entity] + [Real Physical Action / Environment]`** — NEVER stuff `"A vs B"` or `"vs"` into single-entity body scenes (which lures comparison `#edit` and AI Veo videos)! Reserve `"Entity A attacking/vs Entity B"` strictly for direct 2-entity clash moments when permitted by the YAML `Dual-Track Clash vs. Single-Entity Rule`.
     * 🚨 CANONICAL ENTITY SPECIFICITY LOCK (ZERO GENERIC HYPERNYM DOWNGRADE): In BOTH `visual_keyword` AND `stock_query`, ALWAYS keep the **FULL CANONICAL SPECIFIC NAME** of the entity acting in that scene (e.g., write `"harpy eagle talons closeup"`, `"harpy eagle flying jungle"`, `"three toed sloth hanging"` — NEVER shorten a multi-word species into generic hypernyms like bare `"eagle"`, `"bird"`, `"bird of prey"`, `"raptor"`, `"predator"`, `"hunter"`, `"prey"`, `"animal"`, or `"creature"`!).
     * 🚫 ZERO SIDE-PROP / METAPHOR OBJECT / OBSERVER DRIFT & ZERO ACADEMIC JARGON IN QUERIES: Never search for literal metaphor objects (`"steel clamps"`, `"hydraulic press"`), tangential side props, or rooms mentioned in a sentence (`"car dashboard"`, `"military engineers planning"`, `"rainforest canopy"`)—always keep `visual_keyword` and `stock_query` anchored to the Primary Subject/Phenomenon in a distinct action/angle. Translate all scientific/textbook terms (`"benthic crustacean"`, `"limnic eruption"`) into everyday YouTube words (`"deep sea crab underwater"`, `"volcanic crater lake gas"`). NEVER write `@tool` function names or literary storytelling phrases inside `visual_keyword`!
     * `stock_query` (Pexels/Pixabay API search — STRICTLY SINGLE-ENTITY WITH FULL CANONICAL NAME, ZERO `"vs"`): Follow the YAML `Stock & Fallback Broad Query Rule` and `YAML Canonical Stock/Fallback Query Examples` in Section 9 above (`[Full Canonical Subject Noun] + [Setting/Action]`, 2–4 everyday English words). NEVER include `"vs"` or `"versus"` in `stock_query`, NEVER simplify the species name into a generic class word (`"bird of prey"`, bare `"eagle"`, `"bird"`), and NEVER write generic material textures or inanimate metaphor objects!
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
   - Connect scenes with smooth narrative flow so the entire {'1,500–1,750 word (30–36 scenes / 10 Chapters, ~10 min) widescreen documentary' if is_long_form else '85–120 word (7–8 scenes) script'} reads like a seamless, gripping documentary from start to finish.

9. HOOK VISUAL MODE DECISION (`hook_visual_mode`: ALWAYS `"video"`, `is_abstract_keyword`, & `climax_scene_id`):
   - ALWAYS set `"hook_visual_mode": "video"` and `"is_abstract_keyword": false` across ALL niches so Scene 1 (0.0s–3.0s) opens with high-retention real moving video footage (Dual-Beat Fast-Cut).
   - For psychological, behavioral, or counterfactual ("What If") topics, translate the opening hook into CONCRETE, OBSERVABLE PHYSICAL ACTIONS, MICRO-EXPRESSIONS, OR HISTORICAL SPECTACLES (e.g. `"man cold smirk dark room"`, `"intense eye contact interrogation"`, `"submarine underwater ocean"`, `"nuclear explosion mushroom cloud"`), NEVER abstract nouns.
   - Set `"climax_scene_id"` (integer between 2 and 7) and provide 2–5 concise English `"hook_search_queries"` (2–4 words each, bare canonical noun + physical action/setting, NO literary adjectives like 'colossal', 'abyssal', 'terrifying'; e.g. `"sea turtle eating jellyfish"`, `"sperm whale diving deep ocean"`, `"mongoose fighting cobra"`, `"man intense eye contact noir"`).

═══════════════════════════════════════════════════════════
OUTPUT — Return ONLY valid JSON, no markdown, no extra text:
═══════════════════════════════════════════════════════════
{{
  "video_format": "{vf}",
  "title": "Viral YouTube {'Documentary' if is_long_form else 'Shorts'} Title with Emojis (28-55 chars naming exact searchable entities in a natural 'Why / Por que / How' or high-stakes format, ZERO #hashtags, ZERO PSI/Newtons, NO 'Variant' prefix{', NO #Shorts tag' if is_long_form else ''})",
  "title_variants": [
    "Alternative High-CTR 'Why / Por Que + [Entities] + [Counter-Intuitive Action]' Title with Emoji (28-55 chars, NO 'Variant' prefix)",
    "Alternative 'How + [Entities] + [Everyday Analogy]' Title with Emoji (28-55 chars, NO 'Variant' prefix)",
    "Alternative Extreme Contrast / Counter-Move Title with Emoji (28-55 chars, NO 'Variant' prefix)"
  ],
  "primary_entity": "Bare canonical 1-2 word ENGLISH name of main person/species/creature WITHOUT descriptive adjectives (ALWAYS in English even when script language is pt-BR or vi, e.g. 'Whale Skeleton', 'Colossal Squid', 'Honey Badger', 'Julius Caesar') or null for psychology",
  "hook": "The opening 6-12 word Scene 1 hook sentence — deliver an immediate physical shock, lethal strike, or spoken paradox at 0.0s (NEVER repeats or paraphrases a 'Why / Por que' title question, ZERO weak Yes/No 'Did you know...?' questions)",
  "hook_variants": [
    "Archetype 1 (PRIORITY #1: Instant Physical Action / Lethal Trap Strike naming explicit entities, 6-12 words, declarative)",
    "Archetype 2 (Spoken 2-Entity Paradox / Role-Reversal Clash, 6-12 words, declarative)",
    "Archetype 3/4 (Active Crisis, Staged Real-Room Line, or Counter-Intuitive 'Why' Hook if title is not a question, 6-12 words)"
  ],
  "hook_visual_mode": "video | scene",
  "is_abstract_keyword": false,
  "climax_scene_id": {20 if is_long_form else 3},
  "hook_search_queries": ["bare noun + action/format (2-4 words)", "bare noun + angle (2-4 words)"],
  "full_narration": "Complete, smooth, well-developed spoken story without stage directions ({'1,500-1,750 words across 30-36 scenes (~10-Min 10-Chapter Documentary)' if is_long_form else '92-118 words, 34-42s High-APV 5-Act story'} with Real-Life Example, Mechanism, Counter-Move/Climax, and zero spoken Subscribe CTA)",
  "thumbnail_text": "STRICTLY 2-3 PUNCHY ALL-CAPS WORDS IN {target_lang_display} WITH ZERO DRY PHYSICS UNITS ({'e.g. FÁCIL DEMAIS?, ESMAGA CRÂNIOS!, RECORDE MORTAL!' if str(language or '').lower().startswith('pt') else 'e.g. TOO EASY?, TURN INVISIBLE., CRUSHES SKULLS!'})",
  "scenes": [
    {{
      "scene_id": 1,
      "visual_reasoning": "Narrative and visual intent of this scene",
      "narration": "Complete, natural spoken sentence for this scene ({'Scene 1: 12-18 words; Body scenes: 48-58 words flowing naturally, 30 to 36 scenes total (~10 minutes)' if is_long_form else 'Scene 1: 6-12 words opening with a Why / Por que...? curiosity paradox or sharp spoken clash without repeating title verbatim; Body scenes: 11-16 words flowing naturally, 7 to 8 scenes total'})",
      "visual_keyword": "full canonical entity name + physical action/habitat (3-5 English words, never generic bird/eagle/predator)",
      "visual_prompt": "Cinematic {'16:9 widescreen ' if is_long_form else ''}visual description fitting the overall story mood",
      "stock_query": "full canonical entity name + simple action/setting for stock video (2-4 English words, zero vs, never generic bird/eagle/predator)",
      "media_type": "video | ai_image | photo | documentary",
      "camera_motion": "zoom_in_fast | ken_burns_zoom_out | pan_right | ken_burns_zoom_in | pan_left",
      "sfx_cue": "impact_boom | whoosh_heavy | riser_tension | heartbeat_thump | glitch | reveal",
      "highlight_words": ["WORD1", "WORD2"],
      "overlay_badge": "[ // THE REAL TRAP // ] or null",
      "estimated_seconds": {19.5 if is_long_form else 4.0}
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
                                logger.info("🔥 [ContentAgent] Stage-3 Viral Voice Polished Script passed Linter — adopting upgraded masterpiece!")
                                return polished
                            logger.warning(f"⚠️ [ContentAgent] Stage-3 Polished Script linter issues ({'; '.join(pol_lint.issues)}) — keeping Stage-2 draft.")
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
