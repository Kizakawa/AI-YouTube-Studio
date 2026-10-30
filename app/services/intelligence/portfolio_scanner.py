"""app.services.intelligence.portfolio_scanner — Quét hồ sơ kênh, xu hướng toàn cầu & công thức nhân bản video thắng."""
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

from app.services.intelligence.deduplication import is_off_niche_video_title


def get_channel_existing_portfolio(channel_id: str) -> Dict[str, Any]:
    """
    Thu thập toàn bộ dữ liệu lịch sử nội dung của kênh từ 2 nguồn:
    1. YouTube Live Stats (các video đã công chiếu thực tế trên YouTube).
    2. SQLite VideoJob & PublishedVideo (các video đã được tạo trong hệ thống qua toàn bộ danh sách alias của kênh).
    """
    from app.services.smart_scheduler import resolve_channel_family
    family_ids = resolve_channel_family(channel_id)
    fam_lowers = [f.lower() for f in family_ids]

    cfg_channels = settings.get_channels_config()
    target_cfg = next(
        (c for c in cfg_channels if any(
            ident and str(ident).lower() in fam_lowers
            for ident in (c.get("id"), c.get("youtube_channel_id"), c.get("handle"), c.get("name"))
        )),
        None
    )
    if not target_cfg:
        target_cfg = next((c for c in cfg_channels if c.get("id") == channel_id or c.get("handle") == channel_id or c.get("name") == channel_id), {
            "id": channel_id,
            "name": channel_id,
            "niche": "psychology",
            "language": "en"
        })

    handle_or_url = target_cfg.get("handle") or target_cfg.get("channel_url") or channel_id
    channel_name = target_cfg.get("name") or channel_id
    channel_niche = target_cfg.get("niche") or "general"
    channel_lang = target_cfg.get("language") or "en"

    existing_titles: List[str] = []
    existing_topics: List[str] = []
    top_performers: List[Dict[str, Any]] = []
    flop_videos: List[Dict[str, Any]] = []
    last_activity_time: Optional[datetime] = None

    # A0. Đọc dữ liệu từ file audit kênh mới nhất (storage/channel_audit_latest.json)
    try:
        audit_file = Path(__file__).resolve().parent.parent.parent / "storage" / "channel_audit_latest.json"
        if audit_file.exists():
            audit_data = json.loads(audit_file.read_text(encoding="utf-8"))
            matched_audit = audit_data.get(channel_id)
            if not matched_audit:
                for k, v in audit_data.items():
                    if (
                        v.get("id") == channel_id
                        or (v.get("handle") and v.get("handle").lower() == str(handle_or_url).lower())
                        or (v.get("name") and v.get("name").lower() == str(channel_name).lower())
                    ):
                        matched_audit = v
                        break

            if matched_audit:
                raw_vids = matched_audit.get("videos") or []
                if not raw_vids and matched_audit.get("live_scraper_stats"):
                    raw_vids = matched_audit.get("live_scraper_stats", {}).get("videos", [])

                audit_vids_parsed = []
                for v in raw_vids:
                    t = (v.get("title") or "").strip()
                    if not t:
                        continue
                    if t not in existing_titles:
                        existing_titles.append(t)
                    
                    v_views = v.get("views") if v.get("views") is not None else v.get("views_num", 0)
                    try:
                        v_views = int(v_views)
                    except Exception:
                        v_views = 0

                    audit_vids_parsed.append({
                        "title": t,
                        "views_num": v_views,
                        "views_text": v.get("views_text") or f"{v_views} lượt xem",
                        "url": v.get("url") or f"https://youtube.com/shorts/{v.get('id', '')}"
                    })

                if audit_vids_parsed:
                    # Sắp xếp lượt xem giảm dần
                    audit_vids_parsed.sort(key=lambda x: x["views_num"], reverse=True)
                    on_niche_vids = [pv for pv in audit_vids_parsed if not is_off_niche_video_title(pv["title"], channel_niche)]
                    best_on_niche_views = on_niche_vids[0]["views_num"] if on_niche_vids else 0
                    # Video đỉnh cao (views >= 100 hoặc video đúng ngách top 1 có views > 15)
                    for pv in on_niche_vids:
                        if pv["views_num"] >= 100 or (pv["views_num"] == best_on_niche_views and pv["views_num"] > 15):
                            if pv not in top_performers:
                                top_performers.append(pv)
                    # Video flop hoặc video lệch ngách (cần tuyệt đối né tránh lặp lại)
                    for pv in reversed(audit_vids_parsed):
                        if (pv["views_num"] <= 25 or is_off_niche_video_title(pv["title"], channel_niche)) and pv not in flop_videos and pv not in top_performers:
                            flop_videos.append(pv)
    except Exception as e:
        logger.warning(f"[Portfolio] Lỗi đọc channel_audit_latest.json cho {channel_id}: {e}")

    # A. Cào dữ liệu Live từ YouTube
    yt_stats = {}
    try:
        yt_stats = fetch_channel_live_stats(handle_or_url)
        if yt_stats.get("success"):
            for vid in yt_stats.get("videos", []):
                t = vid.get("title", "").strip()
                if t and t not in existing_titles:
                    existing_titles.append(t)
                v_num = vid.get("views_num", 0)
                off_niche = is_off_niche_video_title(t, channel_niche)
                if not off_niche and (vid.get("is_top_performer") or v_num >= 300):
                    top_obj = {
                        "title": t,
                        "views_num": v_num,
                        "views_text": vid.get("views_text", f"{v_num} lượt xem"),
                        "url": vid.get("url", "")
                    }
                    if not any(x["title"] == t for x in top_performers):
                        top_performers.append(top_obj)
                elif (v_num < 30 or off_niche) and not any(x["title"] == t for x in flop_videos):
                    flop_videos.append({
                        "title": t,
                        "views_num": v_num,
                        "views_text": vid.get("views_text", f"{v_num} lượt xem"),
                        "url": vid.get("url", "")
                    })
    except Exception as e:
        logger.warning(f"[Portfolio] Lỗi lấy stats YouTube cho {channel_id}: {e}")

    # B. Lấy dữ liệu từ SQLite DB (Toàn bộ họ kênh / alias)
    try:
        with get_db() as db:
            jobs = db.query(VideoJob).filter(
                VideoJob.target_channel_id.in_(family_ids),
                VideoJob.youtube_video_id.isnot(None),
                VideoJob.youtube_video_id != "",
            ).order_by(VideoJob.created_at.desc()).all()

            for j in jobs:
                if j.title and j.title not in existing_titles:
                    existing_titles.append(j.title)
                if j.topic and j.topic not in existing_topics:
                    existing_topics.append(j.topic)

            from app.database.models import PublishedVideo
            pub_vids = db.query(PublishedVideo).filter(
                PublishedVideo.channel_id.in_(family_ids)
            ).all()
            for pv in pub_vids:
                if pv.title and pv.title not in existing_titles:
                    existing_titles.append(pv.title)

            # Lấy mốc thời gian video đã đăng YouTube mới nhất
            if jobs:
                last_activity_time = jobs[0].updated_at or jobs[0].created_at
            elif pub_vids:
                pub_dates = [pv.published_at for pv in pub_vids if pv.published_at]
                if pub_dates:
                    last_activity_time = max(pub_dates)
    except Exception as e:
        logger.warning(f"[Portfolio] Lỗi truy vấn DB cho {channel_id}: {e}")

    now_utc = datetime.utcnow()
    if last_activity_time:
        delta = now_utc - last_activity_time
        hours_since_last_video = max(0.0, delta.total_seconds() / 3600.0)
        days_since_last_video = max(0.0, delta.total_seconds() / 86400.0)
    else:
        # Thử lấy thời gian video mới nhất từ YouTube Data API (nếu kênh có token cấu hình)
        try:
            from app.agents.publisher_agent import PublisherAgent
            channels_list = settings.get_channels_config()
            matched_cfg = None
            for ch_item in channels_list:
                if ch_item.get("id") == channel_id or ch_item.get("handle") == handle_or_url:
                    matched_cfg = ch_item
                    break
            if matched_cfg:
                pub = PublisherAgent(channel_config=matched_cfg)
                if pub.service:
                    ch_res = pub.service.channels().list(mine=True, part="contentDetails").execute()
                    if ch_res.get("items"):
                        up_id = ch_res["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
                        pl_res = pub.service.playlistItems().list(playlistId=up_id, part="snippet", maxResults=1).execute()
                        if pl_res.get("items"):
                            p_at = pl_res["items"][0]["snippet"]["publishedAt"]
                            dt = datetime.fromisoformat(p_at.replace("Z", "+00:00")).replace(tzinfo=None)
                            last_activity_time = dt
                            delta = now_utc - last_activity_time
                            hours_since_last_video = max(0.0, delta.total_seconds() / 3600.0)
                            days_since_last_video = max(0.0, delta.total_seconds() / 86400.0)
        except Exception as e_yt:
            logger.debug(f"[Portfolio] Không thể lấy thời gian thực từ YouTube API cho {channel_id}: {e_yt}")

    # Fallback nếu vẫn chưa xác định được last_activity_time
    if not last_activity_time:
        yt_vids_count = yt_stats.get("videos_count_num", 0) if isinstance(yt_stats, dict) else 0
        if yt_vids_count > 0:
            # Có video trên YouTube nhưng chưa có log DB -> giả định đã trễ 52 giờ để AI kích hoạt cứu tương tác
            hours_since_last_video = 52.0
            days_since_last_video = 2.1
            last_activity_time = now_utc - timedelta(hours=52)
        else:
            # Kênh hoàn toàn mới, chưa có video
            hours_since_last_video = 120.0
            days_since_last_video = 5.0
            last_activity_time = now_utc - timedelta(days=5)

    return {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "channel_niche": channel_niche,
        "channel_lang": channel_lang,
        "handle_or_url": handle_or_url,
        "existing_titles": existing_titles,
        "existing_topics": existing_topics,
        "top_performers": top_performers,
        "flop_videos": flop_videos,
        "last_activity_time": last_activity_time,
        "hours_since_last_video": round(hours_since_last_video, 1),
        "days_since_last_video": round(days_since_last_video, 2),
        "total_existing_count": len(existing_titles)
    }


def _get_global_trends_directive(niche_id: str, channel_id: Optional[str] = None) -> str:
    """
    Cung cấp định hướng cấu trúc nội dung theo ngách kết hợp dữ liệu thực tế từ DB & Auditor
    (Không dùng con số view/APV vá tay tĩnh).
    """
    nid = (niche_id or "").lower()
    auditor_note = ""
    if channel_id:
        try:
            from app.services.video_library import get_weekly_winners_and_losers
            wl = get_weekly_winners_and_losers(channel_id, top_k=3)
            if wl.get("auditor_ai_insights"):
                # Trích đoạn chẩn đoán ngắn gọn từ Auditor
                short_insight = wl["auditor_ai_insights"][:450].replace("\n", " ")
                auditor_note = f"\n📡 LIVE AUDITOR STRATEGIC FEEDBACK: {short_insight}\n"
        except Exception:
            pass

    if any(k in nid for k in ("deep_sea", "ocean", "thalassophobia", "bien_sau", "abyss", "mysteries")):
        return f"""
🌍 VIRAL SHORTS STRUCTURAL DIRECTIVES (DEEP SEA & OCEAN MYSTERIES ONLY — 100% MARINE / UNDERWATER):
1. Deep Ocean & Marine Anomalies ONLY ("Entity A vs Entity B" or "Submarine/Diver vs Abyssal Anomaly"): Every concept MUST take place in the OCEAN, DEEP SEA, ABYSSAL TRENCHES, POINT NEMO, or MARINE PREDATORS (Colossal Squid, Sperm Whale, Greenland Shark, Orca vs Great White Shark, Deep-Sea Hydrophones, Saturation Diving).
2. Visceral Abyssal Stakes & Everyday Physical Analogies (ZERO DRY PHYSICS UNITS): Open with an immediate, terrifyingly vivid physical image anyone can feel (crushing a steel hull like an empty soda can under 50 passenger jets, boiling water inside lungs, pitch-black abyssal ambush)—NEVER dump dry textbook units like PSI, kPa, Newtons, or Hz.
3. STRICT DOMAIN LOCK (CHỐNG NHẦM LẪN VỚI KÊNH TRÊN CẠN): 100% OCEAN / SEA / MARINE topics ONLY! NEVER suggest terrestrial land animals (NO jaguars/onça, NO anacondas/sucuri, NO caimans, NO eagles, NO deserts, NO forests/savannas).{auditor_note}
"""
    elif any(k in nid for k in ("alternate_history", "what_if", "timeline", "temporal")):
        return f"""
🌍 VIRAL SHORTS STRUCTURAL DIRECTIVES (ALTERNATE HISTORY & WHAT IF?):
1. High-Stakes Single-Second Historical Turning Points ("Decision A vs Catastrophe B"): Lead with an immediate life-or-death historical confrontation or explicit "What If...?" paradox.
2. 2nd-Person Modern Reality Hook (0-3s): Anchor the historical turning point directly to a concrete physical number or countdown in the first 3 seconds.
3. 3-Stage Butterfly Domino Effect (Mid-Video Re-Hook at 12s): Show step-by-step how 1 changed decision rewrites borders, technology, or survival.
STRICT CONSTRAINT: 100% historical scenarios and turning points. Zero psychology advice, zero medical topics.{auditor_note}
"""
    elif "animal" in nid or "rescue" in nid or "pet" in nid:
        return f"""
🌍 VIRAL SHORTS STRUCTURAL DIRECTIVES (ANIMAL RESCUE & EMOTIONAL HEALING):
1. Active Curiosity + Refusal To Give Up Hook: Open with a specific, observable animal action that creates an immediate curiosity gap in the first 3 seconds.
2. The Exact Breakthrough Moment: Show the concrete turning point from fear/danger to trust and safety.
3. Joyful Recovery Payoff: Complete the 4-stage emotional transformation arc (crisis -> intervention -> recovery -> thriving payoff).
CRITICAL CONSTRAINT: Avoid passive or purely bleak titles; every title and hook must promise a compelling transformation.{auditor_note}
"""
    elif "wildlife" in nid or "predator" in nid or "nature" in nid:
        return f"""
🌍 VIRAL SHORTS STRUCTURAL DIRECTIVES (TERRESTRIAL / LAND WILDLIFE & APEX PREDATORS ONLY — 100% TRÊN CẠN):
1. Head-to-Head Land & River-Jungle Apex Predator Duels ("Entity A vs Entity B"): Frame the opening around an asymmetric biological confrontation between two named TERRESTRIAL / JUNGLE / SAVANNA creatures (e.g., Jaguar/Onça-pintada, Caiman/Jacaré-açu, Anaconda/Sucuri, Harpy Eagle/Harpia, Honey Badger/Texugo-do-mel, Komodo Dragon, Giant River Otter/Ariranha, Snow Leopard, Lion, Hippo, Black Mamba).
2. Visceral Biomechanical Weapons & Everyday Analogies (ZERO DRY PHYSICS UNITS): Anchor the first 3 seconds on a vivid, easy-to-picture physical clash (fangs punching straight through armored skull bone like a soda can, talons as big as grizzly claws, strikes faster than an eye blink)—NEVER dump dry textbook units like PSI, Newtons, Joules, or LD50.
3. STRICT DOMAIN LOCK (CHỐNG NHẦM LẪN VỚI KÊNH BIỂN SÂU): 100% TERRESTRIAL / LAND & JUNGLE WILDLIFE ONLY! NEVER suggest deep-sea, ocean trenches, submarines, whales, sharks, or marine monsters.{auditor_note}
"""
    else:  # psychology / forensic crime, extreme brain science & money traps
        return f"""
🌍 VIRAL SHORTS STRUCTURAL DIRECTIVES (FORENSIC CRIME, EXTREME BRAIN EXPERIMENTS & MONEY PSYCHOLOGY — ZERO GENERIC DATING/TEXTING LISTICLES):
1. 3 High-Retention, High-RPM Pillars ONLY:
   - Pillar A (Forensic & Courtroom Psychology): Real FBI interrogation traps, courtroom suspect tells, polygraph secrets, hostage negotiation tactics (proven #1 channel winner formula: 'Why Serial Killers Always Smile In Court').
   - Pillar B (Extreme Brain & Body Neuro-Experiments): What happens to the human brain inside a room so silent you hear your own blood rushing, 72-hour white room isolation, Stanford Prison 6-day collapse, Milgram obedience shock experiment, or total sensory deprivation.
   - Pillar C (Billionaire, Casino & Financial Mind Traps): How casinos, auctions, and tech giants hijack dopamine and decision-making (Decoy Effect, Sunk Cost Trap, Gruen Transfer).
2. STRICTLY FORBIDDEN TOPICS: NEVER suggest generic relationship/texting/fake-friend listicles ('3 Signs They're Using You', 'Narcissist Texts', 'Crush on You', 'How to Not Be Ignored')—these suffer >80% swipe-away!
3. Visceral Bodily & Behavioral Stakes in First 2 Seconds: Every title and 0.0s hook MUST paint an immediate human sensory picture or observable physical tell (hearing your own heartbeat, frozen feet under an interrogation desk, 72 hours in a pure white room)—NEVER dump dry lab jargon like decibels, kPa, or clinical ratios.{auditor_note}
"""


def get_winner_cloner_blueprint(
    channel_id: str,
    niche_id: Optional[str] = None,
    portfolio: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Phân tích động 3 video thắng (Top 3 Winners) và 3 video thua (Bottom 3 Losers) từ DB thật
    (`PublishedVideo` & `VideoJob`) để trích xuất:
    1. Cấu trúc cú pháp tiêu đề & Hook chiến thắng thực tế (kèm APV%, Stayed%, Swipe-Away%).
    2. Danh sách chủ đề chưa từng dùng từ `viral_topic_bank_2026` của Niche YAML (đã lọc chống trùng lặp
       qua `check_idea_similarity` và `UsedTopic` DB — hoàn toàn không dùng danh sách vá tay tĩnh).
    """
    from app.database.models import normalize_channel_id
    from app.services.video_library import get_weekly_winners_and_losers

    canon_cid = normalize_channel_id(channel_id)
    port = portfolio or get_channel_existing_portfolio(canon_cid)
    eff_niche = (niche_id or port.get("channel_niche") or "psychology").lower()
    existing_titles = list(port.get("existing_titles") or []) + list(port.get("existing_topics") or [])
    top_performers = port.get("top_performers") or []

    wl = get_weekly_winners_and_losers(canon_cid, top_k=3)
    db_winners = wl.get("winners") or []
    db_losers = wl.get("losers") or []

    niche_prof = get_niche(eff_niche)
    blueprint_cfg = niche_prof.get("script_blueprint") or {}
    yaml_topic_bank = list(blueprint_cfg.get("viral_topic_bank_2026") or [])

    if db_winners:
        winner_title = db_winners[0].get("title", "")
        winner_views = int(db_winners[0].get("views", 0) or 0)
        winner_stayed = float(db_winners[0].get("stayed_to_watch_pct") or 0.0)
        winner_apv = float(db_winners[0].get("avg_view_percentage") or 0.0)
    else:
        winner_title = top_performers[0]["title"] if top_performers else ""
        winner_views = top_performers[0].get("views_num", 0) if top_performers else 0
        winner_stayed = 0.0
        winner_apv = 0.0

    # Lọc yaml_topic_bank chống trùng lặp với toàn bộ lịch sử kênh (bao gồm Mini-RAG kịch bản + visual metadata)
    from app.services.intelligence.deduplication import check_idea_similarity
    fresh_candidates: List[str] = []
    for cand in yaml_topic_bank:
        if not cand or cand in fresh_candidates:
            continue
        is_dup, _, _ = check_idea_similarity(cand, existing_titles, similarity_threshold=0.38, channel_id=canon_cid)
        if not is_dup:
            fresh_candidates.append(cand)

    prompt_directive = ""
    if winner_title:
        metrics_badge = f"{winner_views:,} views"
        if winner_stayed > 0 or winner_apv > 0:
            metrics_badge += f", Stayed: {winner_stayed:.1f}%, APV: {winner_apv:.1f}%"
        prompt_directive = (
            f"🔥 DYNAMIC WINNER CLONER DIRECTIVE (Live DB #1 Winner: \"{winner_title}\" — {metrics_badge}):\n"
            f"  - Replicate the exact high-stakes curiosity tension, 'A vs B' confrontation framing, and concrete metric hook of this proven winner on a 100% FRESH topic.\n"
        )
        if fresh_candidates:
            prompt_directive += "  - Unexplored candidate angles from niche configuration: " + "; ".join(
                f'"{c}"' for c in fresh_candidates[:3]
            )
    elif fresh_candidates:
        prompt_directive = (
            f"🔥 UNEXPLORED NICHE TOPIC CANDIDATES:\n  - "
            + "\n  - ".join(f'"{c}"' for c in fresh_candidates[:4])
        )

    return {
        "winner_title": winner_title,
        "winner_views": winner_views,
        "winners": db_winners,
        "losers": db_losers,
        "fresh_sequel_topics": fresh_candidates,
        "prompt_directive": prompt_directive,
    }
