"""app.services.intelligence.topic_suggester — Bộ não LLM gợi ý chủ đề thông minh cho từng kênh."""
import os
import re
import json
import time
import logging
import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Union

import requests as http_requests

from config.settings import settings
from app.database.models import get_db, VideoJob, User, ActivityLog, log_activity
from app.schemas.contracts import JobStatus, ScriptContract
from app.services.youtube_analytics import fetch_channel_live_stats
from app.services.niche_manager import get_niche, build_script_prompt_instructions
from app.services.smart_scheduler import get_next_available_golden_slot
from app.agents.content_agent import ContentAgent

logger = logging.getLogger(__name__)

# Giới hạn tối đa không có video mới trước khi kích hoạt Auto-Generation khẩn cấp (48 tiếng = 2 ngày)
KPI_CRITICAL_OVERDUE_HOURS = 48.0
# Ngưỡng cảnh báo màu vàng (24 tiếng = 1 ngày)
KPI_WARNING_HOURS = 24.0
# Thời gian chờ duyệt trước khi tự động đăng tạm (mặc định 4 tiếng)
DEFAULT_APPROVAL_GRACE_HOURS = 4.0

from app.services.intelligence.deduplication import (
    _normalize_text_for_similarity,
    check_idea_similarity,
    extract_saturated_channel_concepts,
)
from app.services.intelligence.portfolio_scanner import (
    _get_global_trends_directive,
    get_channel_existing_portfolio,
    get_winner_cloner_blueprint,
)
from app.services.intelligence.niche_topic_fallbacks import _get_curated_niche_fallbacks


def suggest_channel_topics(
    channel_id: str,
    count: int = 5,
    exclude_additional: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Thuật toán AI gợi ý ý tưởng làm video riêng biệt cho từng kênh:
    - Bám sát hồ sơ ngách (Niche Profile: Tâm lý, Bí ẩn, Động vật, v.v.).
    - Học tập và phát huy từ các video thành công nhất của kênh (Top Performers).
    - Khắc phục triệt để các nhược điểm / bẫy flop từ video trước (Topic Fatigue, Niche Confusion, Passive tone).
    - Đón đầu xu hướng toàn cầu năm 2026 theo từng ngách.
    - LỌC TRÙNG LẶP TUYỆT ĐỐI (Strict De-duplication): Kiểm tra qua check_idea_similarity + Mini-RAG với mọi kịch bản & metadata hình ảnh cũ.
    """
    portfolio = get_channel_existing_portfolio(channel_id)
    niche_id = portfolio["channel_niche"]
    niche_profile = get_niche(niche_id)
    channel_name = portfolio["channel_name"]
    lang = portfolio["channel_lang"]
    top_performers = portfolio.get("top_performers", [])
    flop_videos = portfolio.get("flop_videos", [])

    all_existing = list(portfolio["existing_titles"])
    if exclude_additional:
        all_existing.extend(exclude_additional)

    # [FIX Phase 2] Thêm các topic đã dùng gần đây từ UsedTopic DB (60 ngày) vào exclusion list
    try:
        from app.database.models import UsedTopic, get_db
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=60)
        from app.services.smart_scheduler import resolve_channel_family
        family_ids = resolve_channel_family(channel_id)
        with get_db() as _db:
            recent_used = _db.query(UsedTopic).filter(
                UsedTopic.channel_id.in_(family_ids),
                UsedTopic.used_at >= cutoff
            ).order_by(UsedTopic.used_at.desc()).limit(100).all()
            db_used_topics = [u.topic_text for u in recent_used]
        if db_used_topics:
            all_existing.extend(db_used_topics)
            logger.debug(
                f"[TopicSuggester] Đã tải {len(db_used_topics)} topic gần đây từ DB deduplication "
                f"cho kênh {channel_id} (60 ngày qua)."
            )
    except Exception as _dedup_err:
        logger.debug(f"[TopicSuggester] Không thể tải UsedTopic từ DB: {_dedup_err}")

    # Xây dựng danh sách video cũ để AI né tránh
    clean_existing_prompt = "\n".join([f"- \"{t}\"" for t in all_existing[:15]])
    if not clean_existing_prompt:
        clean_existing_prompt = "(Chưa có video nào — đây là video khởi đầu của kênh!)"

    winner_blueprint = get_winner_cloner_blueprint(channel_id, niche_id=niche_id, portfolio=portfolio)
    winner_cloner_prompt = winner_blueprint.get("prompt_directive") or ""

    # Tự động nạp 3 video thắng (Top 3 Winners) và 3 video thua (Bottom 3 Losers) kèm tỷ lệ xem hết / ở lại / vuốt bỏ thật từ DB
    db_wl_winners: List[Dict[str, Any]] = []
    db_wl_losers: List[Dict[str, Any]] = []
    try:
        from app.services.video_library import get_weekly_winners_and_losers
        wl_data = get_weekly_winners_and_losers(channel_id=channel_id, top_k=3)
        db_wl_winners = wl_data.get("winners") or []
        db_wl_losers = wl_data.get("losers") or []
    except Exception as _wl_err:
        logger.debug(f"[TopicSuggester] Không thể tải weekly winners/losers từ DB: {_wl_err}")

    # Xây dựng dữ liệu Top 3 Winners từ DB (hoặc fallback portfolio)
    top_performer_prompt = ""
    if db_wl_winners or top_performers:
        top_lines = []
        if db_wl_winners:
            for idx_w, w in enumerate(db_wl_winners[:3], start=1):
                hk = f" | Hook: \"{w['hook_text'][:110]}\"" if w.get("hook_text") else ""
                top_lines.append(
                    f"  #{idx_w}. \"{w['title']}\" — {w.get('views', 0):,} views | "
                    f"Stayed: {w.get('stayed_to_watch_pct')}% | Swipe-Away: {w.get('swipe_away_pct')}% | "
                    f"Completion (APV): {w.get('avg_view_percentage')}%{hk}"
                )
        else:
            for p in top_performers[:3]:
                v_txt = p.get("views_text") or (str(p.get("views_num", 0)) + " views")
                top_lines.append(f"- \"{p['title']}\" ({v_txt})")
        top_performer_prompt = f"""
🏆 WEEKLY TOP 3 WINNING VIDEOS ON THIS CHANNEL (REAL DB RETENTION METRICS):
{chr(10).join(top_lines)}
Winning Formula (Proven on pt-BR & Wild Odyssey -> MANDATORY FOR ALL EN & PT-BR CHANNELS):
  1. "A vs B" Asymmetric Confrontation Structure (Entity A vs Entity B / Instinct vs Reality / Hidden Mechanism vs Victim).
  2. 5-8 Word Shocking Declarative Statement with a hard physical number/measurement in the first 3 seconds (NO passive Yes/No questions).
{winner_cloner_prompt}
"""
    elif winner_cloner_prompt:
        top_performer_prompt = f"\n{winner_cloner_prompt}\n"

    # Xây dựng dữ liệu Bottom 3 Losers từ DB (hoặc fallback flop_videos)
    flop_prompt = ""
    if db_wl_losers or flop_videos:
        flop_lines = []
        if db_wl_losers:
            for idx_l, l in enumerate(db_wl_losers[:3], start=1):
                hk = f" | Failed Hook: \"{l['hook_text'][:110]}\"" if l.get("hook_text") else ""
                flop_lines.append(
                    f"  #{idx_l}. \"{l['title']}\" — {l.get('views', 0):,} views | "
                    f"Stayed: {l.get('stayed_to_watch_pct')}% | Swipe-Away: {l.get('swipe_away_pct')}% | "
                    f"Completion (APV): {l.get('avg_view_percentage')}%{hk}"
                )
        else:
            for p in flop_videos[:3]:
                v_txt = p.get("views_text") or (str(p.get("views_num", 0)) + " views")
                flop_lines.append(f"- \"{p['title']}\" ({v_txt})")
        flop_prompt = f"""
⚠️ WEEKLY BOTTOM 3 LOSING VIDEOS ON THIS CHANNEL (HIGH SWIPE-AWAY TRAPS TO AVOID):
{chr(10).join(flop_lines)}
Core Weaknesses Identified:
1. High swipe-away rate caused by slow textbook openings or generic questions instead of an "A vs B" confrontation or shocking claim.
2. Topic fatigue: Do NOT repeat the exact same sub-topic or keyword consecutively.
3. Niche mismatch: Stay 100% faithful to '{niche_id}'.
"""

    global_trends_prompt = _get_global_trends_directive(niche_id, channel_id=channel_id)

    # Tác nhân tự động nhận diện các cụm từ, số đo hoặc chủ đề đang bị bão hòa
    saturated_concepts = extract_saturated_channel_concepts(all_existing, min_frequency=2)
    saturation_prompt = ""
    if saturated_concepts:
        sat_str = ", ".join([f"\"{c}\"" for c in saturated_concepts])
        saturation_prompt = f"""
═══════════════════════════════════════════════════════════
🚨 AUTONOMOUS REPETITION & CONCEPT FATIGUE WARNING (DETECTED FROM RECENT UPLOADS):
The following specific sub-topics, numbers, entities, or phrasing have appeared repeatedly on this channel:
[{sat_str}]

AUTONOMOUS AGENT MANDATE:
YouTube's recommendation algorithm aggressively penalizes channels that repeatedly publish videos revolving around the same recurring numbers, locations, or phrasing (Topic Cannibalization).
You must AUTONOMOUSLY PIVOT to completely fresh, unvisited premises, alternative entities, or different mechanisms in '{niche_id}'.
DO NOT center any candidate around these fatigued terms!
"""

    rag_topic_memory = ""
    try:
        from app.services.video_memory_rag import get_video_memory_rag
        rag_ctx = get_video_memory_rag().build_rag_prompt_context(channel_id, niche_id, top_k=4)
        if rag_ctx:
            rag_topic_memory = f"\n═══════════════════════════════════════════════════════════\n{rag_ctx}\n"
    except Exception:
        pass

    prompt = f"""You are the Master Creative Director and Viral Algorithm Strategist for YouTube Shorts channel '{channel_name}'.
Niche: '{niche_profile.get('name')}' ({niche_id})
Target Language: {lang}

═══════════════════════════════════════════════════════════
🚫 STRICT ANTI-DUPLICATION RULE (HIGHEST PRIORITY):
The following titles and concepts ALREADY EXIST on this channel:
{clean_existing_prompt}

CRITICAL MANDATE:
- DO NOT duplicate, rephrase, or lightly modify ANY of the existing topics above!
- You MUST pioneer FRESH, UNEXPLORED angles, psychological paradoxes, or shocking historical mysteries within '{niche_id}'.
═══════════════════════════════════════════════════════════
{top_performer_prompt}
═══════════════════════════════════════════════════════════
{flop_prompt}
═══════════════════════════════════════════════════════════
{global_trends_prompt}
═══════════════════════════════════════════════════════════
{saturation_prompt}{rag_topic_memory}
═══════════════════════════════════════════════════════════
🔥 UPSTREAM VIRAL TOPIC & DUAL-PEAK RETENTION PRIMING (MANDATORY FOR EVERY IDEA):
Real YouTube Analytics proves that videos achieving 100%–127% APV and 1,500–3,000+ views succeed BEFORE the script is even written—because the TOPIC itself contains an irresistible physical anomaly and a 2-stage climax:
1. BANNED WEAK / VAGUE TOPICS (ZERO TOLERANCE):
   - NEVER suggest generic textbook topics, vague open-ended questions, or soft self-help advice (e.g., FORBIDDEN: "Where Does This Endless River Go?", "Signs Someone Is Envious of You", "How To Be More Confident", "Why The Ocean Is Scary").
2. MANDATORY TOPIC FORMULA = [CONCRETE PHYSICAL/NUMERICAL ANCHOR] + [ASYMMETRIC CLASH OR COUNTER-INTUITIVE PARADOX]:
   - Every single topic and title MUST include a specific, verifiable physical measurement, extreme metric, or historical timestamp (`1,100 PSI`, `93°C`, `2,000m`, `1883`, `0.2 seconds`, `4-second silence`, `230 dB`) paired with a high-stakes clash ("Entity A vs. Entity B") or an "impossible" real-world anomaly (e.g., "A River in the Amazon That Boils at 93°C Without a Volcano", "Colossal Squid vs. Sperm Whale at 2,000m Depth", "The 4-Second Eye Freeze That Exposes Covert Liars").
3. BUILT-IN MID-VIDEO CLIMAX TWIST (15-SECOND RE-HOOK TO PREVENT APV DROP):
   - Do NOT pick one-dimensional topics where the entire secret is given away in Scene 1!
   - Every topic MUST have a built-in **Second Climax Twist (`mid_video_climax`)** that escalates the stakes at second 14–20 (Scene 4–5)—a hidden mechanism, a counter-attack, or a second shocking discovery that forces viewers to watch until the final second.

🎯 OBJECTIVE:
Generate exactly {count + 2} completely unique, high-retention Shorts concepts tailored specifically to '{channel_name}'.
Each concept must:
1. Open with a blunt, concrete metric or "A vs B" confrontation in both the Title and 0.0s Hook (5–9 words, ZERO passive Yes/No questions).
2. Include a specific Mid-Video Climax Twist (`mid_video_climax`) for Scene 4–5 so APV stays above 90%+.
3. Actively eliminate and fix the channel's past flop weaknesses (topic fatigue, passive tone, niche dilution).
4. Keep YouTube Shorts titles concise (28–55 characters), punchy, and include #Shorts.

Return ONLY a valid JSON array of objects with the following structure:
[
  {{
    "topic": "Specific high-stakes topic with a concrete physical metric/anomaly and clash (e.g. The 93°C Boiling Amazon River That Cooks Prey Alive Without a Volcano)",
    "title": "Viral YouTube Shorts Title with Concrete Number/Clash, Emojis and #Shorts (28-55 chars)",
    "hook": "Blunt 0.0s declarative shock or 'A vs B' confrontation line with a concrete metric (5-9 words, NO passive questions)",
    "mid_video_climax": "The 15-second (Scene 4-5) escalation twist or second shocking proof that prevents mid-video drop-off",
    "angle": "Micro-narrative breakdown & specific observable physical behaviors or phenomena",
    "pinned_comment": "Provocative A-vs-B poll or debate question to pin at top of comments",
    "viral_formula": "A vs B Metric Clash | Dual-Peak Escalation | Seamless Infinite Loop",
    "predicted_retention": "110% - 135%",
    "why_viral": "Giải thích ngắn gọn bằng tiếng Việt vì sao chủ đề có con số/nghịch lý này sẽ bùng nổ views và giữ APV > 100%",
    "fixes_flaw": "Giải thích bằng tiếng Việt cách ý tưởng này khắc phục nhược điểm/lỗi tụt APV của kênh",
    "trend_alignment": "Xu hướng toàn cầu 2026 được đón đầu (ví dụ: Giải mã chấn động vật lý dưới đáy biển / Biomechanics mãnh thú / Phản xạ vi biểu cảm 0.2s)"
  }}
]
"""
    agent = ContentAgent()
    verified_ideas: List[Dict[str, Any]] = []

    try:
        raw_text = agent._call_api(prompt, json_mode=False, timeout=30)
        # Làm sạch markdown
        clean_text = raw_text.strip()
        if clean_text.startswith("```"):
            lines = clean_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            clean_text = "\n".join(lines).strip()

        start = clean_text.find("[")
        end = clean_text.rfind("]")
        if start != -1 and end != -1 and end > start:
            clean_text = clean_text[start:end+1]

        candidates = json.loads(clean_text)
        if isinstance(candidates, list):
            for cand in candidates:
                cand_title = cand.get("title", "")
                cand_topic = cand.get("topic", "")
                
                # Tự động loại bỏ ứng viên nếu vẫn lặp lại các khái niệm đang bị bão hòa của kênh
                has_fatigued_term = False
                norm_cand = _normalize_text_for_similarity(f"{cand_title} {cand_topic}")
                for sat_term in saturated_concepts:
                    if sat_term in norm_cand:
                        has_fatigued_term = True
                        logger.info(f"[AutonomousFatigueGuard] Bỏ qua ý tưởng '{cand_title}' vì chứa khái niệm đang bị bão hòa '{sat_term}' (tự động nhận diện từ lịch sử kênh)!")
                        break
                if has_fatigued_term:
                    continue

                # Kiểm tra trùng lặp với kho video cũ + Mini-RAG
                is_dup_title, score_title, match_t = check_idea_similarity(cand_title, all_existing, similarity_threshold=0.38, channel_id=channel_id)
                is_dup_topic, score_topic, match_p = check_idea_similarity(cand_topic, all_existing, similarity_threshold=0.38, channel_id=channel_id)

                if is_dup_title or is_dup_topic:
                    logger.info(f"[DeDuplication] Bỏ qua ý tưởng trùng lặp: '{cand_title}' (tương đồng {max(score_title, score_topic):.2f} với '{match_t or match_p}')")
                    continue

                # Kiểm tra trùng lặp chéo với chính các ý tưởng vừa thêm
                current_accepted_titles = [x["title"] for x in verified_ideas]
                is_self_dup, _, _ = check_idea_similarity(cand_title, current_accepted_titles, similarity_threshold=0.45)
                if is_self_dup:
                    continue

                # Kiểm tra tường lửa ngách cứng (Đặc biệt tách biệt Kênh Trên Cạn vs Kênh Về Biển)
                from app.services.niche_manager import validate_channel_niche_alignment
                niche_check = validate_channel_niche_alignment(
                    channel_id=channel_id,
                    title=cand_title,
                    topic=cand_topic,
                    script=cand.get("hook", ""),
                )
                if not niche_check.get("passed", True):
                    logger.info(f"[NicheFirewall] Bỏ qua ý tưởng lệch ngách '{cand_title}' trên {channel_id}: {niche_check.get('reason')}")
                    continue

                cand["channel_id"] = channel_id
                cand["channel_name"] = channel_name
                cand["niche"] = niche_id
                cand["similarity_score"] = max(score_title, score_topic)
                if not cand.get("fixes_flaw"):
                    cand["fixes_flaw"] = "Khắc phục tình trạng bão hòa nội dung cũ, tăng tính kích thích tò mò 3 giây đầu."
                if not cand.get("trend_alignment"):
                    cand["trend_alignment"] = "Đón đầu trào lưu Shorts 2026 với cấu trúc loop vô tận và hook thị giác."
                verified_ideas.append(cand)

                if len(verified_ideas) >= count:
                    break
    except Exception as e:
        logger.error(f"[TopicSuggester] Lỗi gọi AI gợi ý ý tưởng cho {channel_id}: {e}")

    # Fallback an toàn nếu AI gặp sự cố quota hoặc không đủ số lượng
    if len(verified_ideas) < count:
        logger.info(f"[TopicSuggester] Bổ sung fallback curated cho kênh {channel_id} (niche: {niche_id})")
        fallbacks = _get_curated_niche_fallbacks(niche_id, lang)
        scored_fallbacks = []
        existing_verified_titles = {v.get("title", "") for v in verified_ideas}
        for fb in fallbacks:
            fb_title = fb.get("title", "")
            if fb_title in existing_verified_titles:
                continue
            is_dup, score, _ = check_idea_similarity(fb_title, all_existing, similarity_threshold=0.35, channel_id=channel_id)
            fb_copy = dict(fb)
            fb_copy["channel_id"] = channel_id
            fb_copy["channel_name"] = channel_name
            fb_copy["niche"] = niche_id
            fb_copy["similarity_score"] = score
            scored_fallbacks.append((is_dup, score, fb_copy))
            if not is_dup:
                verified_ideas.append(fb_copy)
                existing_verified_titles.add(fb_title)
                if len(verified_ideas) >= count:
                    break
        # Nếu vẫn chưa đủ count, chọn các ứng viên fallback có similarity_score thấp nhất để không bao giờ trả về rỗng
        if len(verified_ideas) < count and scored_fallbacks:
            scored_fallbacks.sort(key=lambda x: x[1])
            for _, score, fb_copy in scored_fallbacks:
                if fb_copy.get("title") not in existing_verified_titles:
                    verified_ideas.append(fb_copy)
                    existing_verified_titles.add(fb_copy.get("title", ""))
                    if len(verified_ideas) >= count:
                        break

    return verified_ideas[:count]
