"""
app.services.channel_intelligence — Hệ thống Trí Tuệ Kênh Tự Động (Channel Intelligence & Anti-Duplication).
Đảm bảo:
1. Thuật toán gợi ý ý tưởng thông minh cho từng kênh, kiểm tra tương đồng và chống trùng lặp 100% với video cũ.
2. Báo cáo & giám sát chỉ tiêu KPI định kỳ cho từng kênh (đúng hạn, sắp trễ, trễ deadline >= 2 ngày).
3. Tự động sản xuất video cứu tương tác (Auto-Generation) khi một kênh trễ deadline 2 ngày.
4. Thúc giục người dùng kiểm tra lại video (Urgent Reminder).
5. Cơ chế đăng tạm khẩn cấp (Grace-Period Fallback Publish): Nếu người dùng bận không xác nhận sau thời hạn (ví dụ 4 giờ), tự động đăng/lên lịch vào giờ vàng để giữ vững thuật toán YouTube.
"""

import os
import re
import json
import time
import logging
import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

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


# ──────────────────────────────────────────────────────────────────────────────
# 1. THUẬT TOÁN CHỐNG TRÙNG LẶP Ý TƯỞNG (ANTI-DUPLICATION ENGINE)
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_text_for_similarity(text: str) -> str:
    """Làm sạch và chuẩn hoá chuỗi văn bản để so sánh độ tương đồng."""
    if not text:
        return ""
    # Chuyển chữ thường
    clean = text.lower()
    # Loại bỏ emoji và ký tự đặc biệt
    clean = re.sub(r"[^\w\s]", " ", clean, flags=re.UNICODE)
    # Loại bỏ các từ dừng thông dụng trong tiêu đề Shorts
    stop_words = {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
        "is", "are", "this", "that", "why", "how", "what", "signs", "sign",
        "video", "shorts", "viral", "psychology", "mystery", "history",
        "những", "các", "và", "của", "là", "trong", "về", "dấu", "hiệu",
        "bí", "ẩn", "tâm", "lý", "thế", "giới"
    }
    tokens = [w.strip() for w in clean.split() if len(w.strip()) >= 3 and w.strip() not in stop_words]
    return " ".join(tokens)


def check_idea_similarity(
    candidate_title_or_topic: str,
    existing_items: List[str],
    similarity_threshold: float = 0.40
) -> Tuple[bool, float, str]:
    """
    Kiểm tra xem ý tưởng/tiêu đề mới có bị trùng lặp hoặc quá giống với danh sách video đã làm không.
    Sử dụng kết hợp Jaccard Token Overlap và Substring Keyword Containment.
    Trả về: (is_duplicate: bool, max_score: float, matched_item: str)
    """
    if not candidate_title_or_topic or not existing_items:
        return False, 0.0, ""

    norm_candidate = _normalize_text_for_similarity(candidate_title_or_topic)
    cand_tokens = set(norm_candidate.split())
    if not cand_tokens:
        return False, 0.0, ""

    max_score = 0.0
    best_match = ""

    for item in existing_items:
        if not item:
            continue
        norm_item = _normalize_text_for_similarity(item)
        item_tokens = set(norm_item.split())
        if not item_tokens:
            continue

        intersection = cand_tokens.intersection(item_tokens)
        union = cand_tokens.union(item_tokens)

        # 1. Jaccard similarity
        jaccard = len(intersection) / len(union) if union else 0.0

        # 2. Overlap trên tập từ nhỏ hơn (phát hiện ý tưởng bị bao hàm 100%)
        containment = len(intersection) / min(len(cand_tokens), len(item_tokens)) if min(len(cand_tokens), len(item_tokens)) > 0 else 0.0

        # Điểm tương đồng tổng hợp
        combined_score = max(jaccard, containment * 0.75)

        if combined_score > max_score:
            max_score = combined_score
            best_match = item

    is_duplicate = max_score >= similarity_threshold
    return is_duplicate, round(max_score, 3), best_match


# ──────────────────────────────────────────────────────────────────────────────
# 2. THU THẬP DANH MỤC VIDEO & TOP PERFORMER CỦA KÊNH
# ──────────────────────────────────────────────────────────────────────────────

def get_channel_existing_portfolio(channel_id: str) -> Dict[str, Any]:
    """
    Thu thập toàn bộ dữ liệu lịch sử nội dung của kênh từ 2 nguồn:
    1. YouTube Live Stats (các video đã công chiếu thực tế trên YouTube).
    2. SQLite VideoJob (các video đã được tạo trong hệ thống).
    """
    cfg_channels = settings.get_channels_config()
    target_cfg = next((c for c in cfg_channels if c.get("id") == channel_id), None)
    if not target_cfg:
        # Fallback thử tìm theo handle hoặc name
        target_cfg = next((c for c in cfg_channels if c.get("handle") == channel_id or c.get("name") == channel_id), {
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
                    # Video đỉnh cao (views >= 100 hoặc video top 1 có views > 20)
                    for pv in audit_vids_parsed:
                        if pv["views_num"] >= 100 or (pv["views_num"] == audit_vids_parsed[0]["views_num"] and pv["views_num"] > 20):
                            if pv not in top_performers:
                                top_performers.append(pv)
                    # Video flop (views <= 25 hoặc nhóm views thấp nhất kênh cần né tránh)
                    for pv in reversed(audit_vids_parsed):
                        if pv["views_num"] <= 25 and pv not in flop_videos and pv not in top_performers:
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
                if vid.get("is_top_performer") or v_num >= 300:
                    top_obj = {
                        "title": t,
                        "views_num": v_num,
                        "views_text": vid.get("views_text", f"{v_num} lượt xem"),
                        "url": vid.get("url", "")
                    }
                    if not any(x["title"] == t for x in top_performers):
                        top_performers.append(top_obj)
                elif v_num < 30 and not any(x["title"] == t for x in flop_videos):
                    flop_videos.append({
                        "title": t,
                        "views_num": v_num,
                        "views_text": vid.get("views_text", f"{v_num} lượt xem"),
                        "url": vid.get("url", "")
                    })
    except Exception as e:
        logger.warning(f"[Portfolio] Lỗi lấy stats YouTube cho {channel_id}: {e}")

    # B. Lấy dữ liệu từ SQLite DB
    try:
        with get_db() as db:
            jobs = db.query(VideoJob).filter(
                VideoJob.target_channel_id == channel_id
            ).order_by(VideoJob.created_at.desc()).all()

            for j in jobs:
                if j.title and j.title not in existing_titles:
                    existing_titles.append(j.title)
                if j.topic and j.topic not in existing_topics:
                    existing_topics.append(j.topic)

            # Lấy mốc thời gian video mới nhất
            # Ưu tiên video có trạng thái hoàn tất (RENDERED, SCHEDULED, PUBLISHED, AWAITING_APPROVAL)
            active_jobs = [j for j in jobs if j.status in ("RENDERED", "SCHEDULED", "PUBLISHED", "AWAITING_APPROVAL")]
            if active_jobs:
                last_activity_time = active_jobs[0].updated_at or active_jobs[0].created_at
            elif jobs:
                last_activity_time = jobs[0].created_at
    except Exception as e:
        logger.warning(f"[Portfolio] Lỗi truy vấn DB cho {channel_id}: {e}")

    # Tính toán thời gian kể từ video cuối cùng
    now_utc = datetime.utcnow()
    hours_since_last_video = 999.0
    days_since_last_video = 99.0

    if last_activity_time:
        delta = now_utc - last_activity_time
        hours_since_last_video = max(0.0, delta.total_seconds() / 3600.0)
        days_since_last_video = max(0.0, delta.total_seconds() / 86400.0)
    else:
        # Nếu chưa có video nào trong DB, ước tính theo số video YouTube
        yt_vids_count = yt_stats.get("videos_count_num", 0) if isinstance(yt_stats, dict) else 0
        if yt_vids_count > 0:
            # Có video trên YouTube nhưng chưa có log DB -> giả định ~ 36 giờ
            hours_since_last_video = 36.0
            days_since_last_video = 1.5
            last_activity_time = now_utc - timedelta(hours=36)
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


def _get_global_trends_directive(niche_id: str) -> str:
    """Cung cấp xu hướng Shorts toàn cầu 2026 thịnh hành nhất theo từng ngách cụ thể."""
    nid = (niche_id or "").lower()
    if "mystery" in nid or "history" in nid:
        return """
🌍 2026 GLOBAL VIRAL SHORTS TRENDS (MYSTERIES & ANOMALIES):
1. Subterranean & Ocean Acoustic Anomalies: Declassified underwater hydrophone recordings (The Bloop, Julia, Upsweep, Train), Mariana acoustic anomalies with exact frequency stats. (CRITICAL: Pivot AWAY from repetitive generic 'what is at 1000m depth' lists!).
2. Forbidden Architecture & Sealed Vaults: Underground secrets beneath the Sphinx's right paw, Derinkuyu subterranean ventilation mysteries, Vatican secret vault micro-documents.
3. Glitches in Historical Reality: Prophecies with eerie modern precision (Roger Bacon 1290 machines), ancient precision masonry acoustic resonance (Kailasa, Puma Punku).
Hook Formula: 'In [Year], deep sonar sensors picked up this classified acoustic signal...'
"""
    elif "animal" in nid or "rescue" in nid or "pet" in nid:
        return """
🌍 2026 GLOBAL VIRAL SHORTS TRENDS (ANIMAL RESCUE & EMOTIONAL HEALING):
1. 3-Phase Transformation Arc: High-contrast emotional rollercoaster (Abandoned/shivering in freezing rain -> First tentative warm meal & soft blanket -> Healthy glowing coat & joyful adoption leap).
2. Emergency Cross-Species Miracles: Stray dogs protecting tiny kittens, maternal animal instincts across species.
3. Unconditional Loyalty Testimonials: Stories where the rescued pet repays human kindness or stays vigilant beside their savior.
Hook Formula: 'He had completely given up on living... until a stranger stopped their car.'
"""
    elif "wildlife" in nid or "predator" in nid or "nature" in nid:
        return """
🌍 2026 GLOBAL VIRAL SHORTS TRENDS (WILDLIFE & APEX PREDATORS):
1. Extreme Asymmetric Combat Biomechanics: Orcas using tonic immobility on Great White Sharks; Honey Badger venom neurotoxin immunity vs Black Mamba & Lion Prides; Jaguar skull-crushing bite vs Black Caiman.
2. Sensory Superpowers & Strike Physics: Cheetah elastic spine 70 mph acceleration in 3 seconds; Peregrine Falcon 240 mph air-dive aerodynamics; Mantis Shrimp cavitation bubble punch.
STRICT NEGATIVE CONSTRAINT: Strictly 100% biological animal predator behaviors. ZERO space facts, zero deep sea fantasy, zero psychology! Keep audience bucket clean!
Hook Formula: 'This 20-pound creature has zero fear of an entire pride of lions... and here is why.'
"""
    else:  # psychology / dark psychology / self-mastery
        return """
🌍 2026 GLOBAL VIRAL SHORTS TRENDS (DARK PSYCHOLOGY & HUMAN BEHAVIOR):
1. Stealth Dark Triad Detection: Micro-expressions of covert envy, conversational gaslighting traps, sudden eye pupil dilation when lying.
2. High-Status Unblinking Silence: How calm, deliberate non-reaction flips power dynamics during public disrespect or pressure.
3. Micro-Behavioral Tell Guides: '3 physical tells someone secretly resents your success', 'The 2-second shoulder freeze that reveals guilt'.
Hook Formula: 'If someone does this within the first 5 seconds of speaking to you, pay close attention...'
"""


# ──────────────────────────────────────────────────────────────────────────────
# 3. GỢI Ý Ý TƯỞNG THÔNG MINH CHO TỪNG KÊNH (CHANNEL TOPIC SUGGESTER)
# ──────────────────────────────────────────────────────────────────────────────

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
    - LỌC TRÙNG LẶP TUYỆT ĐỐI (Strict De-duplication): Kiểm tra qua check_idea_similarity với mọi video cũ.
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

    # Xây dựng danh sách video cũ để AI né tránh
    clean_existing_prompt = "\n".join([f"- \"{t}\"" for t in all_existing[:15]])
    if not clean_existing_prompt:
        clean_existing_prompt = "(Chưa có video nào — đây là video khởi đầu của kênh!)"

    # Xây dựng dữ liệu Top Performers làm hình mẫu lan toả
    top_performer_prompt = ""
    if top_performers:
        top_lines = []
        for p in top_performers[:3]:
            v_txt = p.get("views_text") or (str(p.get("views_num", 0)) + " views")
            top_lines.append(f"- \"{p['title']}\" ({v_txt})")
        top_performer_prompt = f"""
🏆 PROVEN HIGH-RETENTION BREAKTHROUGH VIDEOS ON THIS CHANNEL:
{chr(10).join(top_lines)}
Winning Mechanism: High-curiosity primal trigger, instant pattern interrupt, urgent psychological or existential stakes.
Your new ideas MUST inherit this winning emotional DNA without copying the same subject!
"""

    # Xây dựng dữ liệu Flop Videos và các cạm bẫy cần tránh
    flop_prompt = ""
    if flop_videos:
        flop_lines = []
        for p in flop_videos[:4]:
            v_txt = p.get("views_text") or (str(p.get("views_num", 0)) + " views")
            flop_lines.append(f"- \"{p['title']}\" ({v_txt})")
        flop_prompt = f"""
⚠️ UNDERPERFORMING / FLOP VIDEOS ON THIS CHANNEL (CRITICAL TRAPS TO AVOID):
{chr(10).join(flop_lines)}
Core Weaknesses Identified:
1. Topic fatigue: Do NOT repeat the exact same sub-topic or keyword consecutively (e.g., avoid repetitive deep sea ocean lists).
2. Passive / philosophical / low-urgency openings lacking a 3-second pattern interrupt.
3. Niche mismatch: Stay 100% faithful to '{niche_id}', never confuse YouTube's recommendation algorithm with off-niche topics.
Mandate: Each new idea MUST actively solve these weaknesses!
"""

    global_trends_prompt = _get_global_trends_directive(niche_id)

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
🎯 OBJECTIVE:
Generate exactly {count + 2} completely unique, high-retention Shorts concepts tailored specifically to '{channel_name}'.
Each concept must:
1. Inherit the winning emotional hook of top performers.
2. Actively eliminate and fix the channel's past flop weaknesses (topic fatigue, passive tone, niche dilution).
3. Align directly with 2026 global viral trends for this niche.
4. Keep YouTube Shorts titles concise (under 60 characters), punchy, and include #Shorts.

Return ONLY a valid JSON array of objects with the following structure:
[
  {{
    "topic": "Core punchy topic concept (e.g. The 3-second silence interrogation trick)",
    "title": "Viral YouTube Shorts Title with Emojis and #Shorts (under 60 chars)",
    "hook": "Electric 3-second pattern interrupt opening line (5-8 words)",
    "angle": "Micro-narrative breakdown & specific observable behaviors or phenomena",
    "pinned_comment": "Provocative engagement bait question to pin at top of comments",
    "viral_formula": "Double Tap & Protect | Debate in Comments | Curiosity Part 2",
    "predicted_retention": "150% - 165%",
    "why_viral": "Giải thích ngắn gọn bằng tiếng Việt vì sao chủ đề này sẽ bùng nổ views",
    "fixes_flaw": "Giải thích bằng tiếng Việt cách ý tưởng này khắc phục nhược điểm/lỗi flop của kênh (ví dụ: tránh bão hòa biển sâu, tăng tính giật gân, bám đúng ngách)",
    "trend_alignment": "Xu hướng toàn cầu 2026 được đón đầu (ví dụ: Giải mã âm thanh dưới đáy biển / Biomechanics mãnh thú / Dark Triad body language)"
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
                
                # Kiểm tra trùng lặp với kho video cũ
                is_dup_title, score_title, match_t = check_idea_similarity(cand_title, all_existing, similarity_threshold=0.38)
                is_dup_topic, score_topic, match_p = check_idea_similarity(cand_topic, all_existing, similarity_threshold=0.38)

                if is_dup_title or is_dup_topic:
                    logger.info(f"[DeDuplication] Bỏ qua ý tưởng trùng lặp: '{cand_title}' (tương đồng {max(score_title, score_topic):.2f} với '{match_t or match_p}')")
                    continue

                # Kiểm tra trùng lặp chéo với chính các ý tưởng vừa thêm
                current_accepted_titles = [x["title"] for x in verified_ideas]
                is_self_dup, _, _ = check_idea_similarity(cand_title, current_accepted_titles, similarity_threshold=0.45)
                if is_self_dup:
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
        for fb in fallbacks:
            fb_title = fb.get("title", "")
            is_dup, score, _ = check_idea_similarity(fb_title, all_existing, similarity_threshold=0.35)
            if not is_dup:
                fb_copy = dict(fb)
                fb_copy["channel_id"] = channel_id
                fb_copy["channel_name"] = channel_name
                fb_copy["niche"] = niche_id
                fb_copy["similarity_score"] = score
                verified_ideas.append(fb_copy)
                if len(verified_ideas) >= count:
                    break

    return verified_ideas[:count]


def _get_curated_niche_fallbacks(niche_id: str, lang: str = "en") -> List[Dict[str, Any]]:
    """Kho ý tưởng dự phòng chất lượng cao cho từng ngách."""
    if "mystery" in niche_id or "history" in niche_id:
        return [
            {
                "topic": "The 3 Underwater Sounds Recorded in Deep Oceans Science Can't Explain",
                "title": "3 Deep Ocean Sounds Science CANNOT Explain 🌊📻 #Shorts",
                "hook": "In 1997, oceanic sensors detected this terrifying subterranean creature sound...",
                "angle": "The Bloop, Julia sound, Mariana acoustic anomalies",
                "pinned_comment": "Do you believe there are unknown titanic leviathans in the abyss? 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "160% - 170%",
                "why_viral": "Ăn theo video 1.2K views 'Why You're Terrified of Deep Water' — khai thác âm thanh bí ẩn đại dương",
                "fixes_flaw": "Khắc phục bão hòa hình ảnh đại dương bằng cách chuyển trọng tâm sang giải mã tệp âm thanh bí ẩn (The Bloop/Julia) có tần số thực nghiệm, giữ chân người nghe bằng thính giác.",
                "trend_alignment": "Đón đầu trào lưu Subterranean Acoustic Anomalies 2026 (âm thanh bí ẩn đáy đại dương thu hút hàng triệu lượt bàn tán toàn cầu)."
            },
            {
                "topic": "The Real Reason Why Nobody is Allowed to Dive Under the Sphinx",
                "title": "Why Divers Are BANNED Under The Great Sphinx 📜👁️ #Shorts",
                "hook": "Under the right paw of the Sphinx lies a secret sealed underground chamber...",
                "angle": "Acoustic seismology findings, Hall of Records, Egyptian secrecy",
                "pinned_comment": "What do you think is hidden under the Sphinx? Drop your theories 👇",
                "viral_formula": "Curiosity Part 2",
                "predicted_retention": "155% - 165%",
                "why_viral": "Bí ẩn Ai Cập cổ đại muôn thuở kích thích người xem xem hết video",
                "fixes_flaw": "Cắt đứt chuỗi 5 video đại dương liên tiếp gây topic fatigue, đổi sang chủ đề hầm ngầm cấm kỵ Ai Cập cổ đại để làm mới luồng phân phối của thuật toán.",
                "trend_alignment": "Bí mật khảo cổ và mật thất bị niêm phong (Sealed Ancient Chambers) - chủ đề muôn thuở đứng đầu bảng xếp hạng tò mò toàn cầu."
            },
            {
                "topic": "The Medieval Manuscript That Predicted Modern Technology in 1290",
                "title": "The 1290 Monk Who PREDICTED Airplanes & Submarines 📜⚡ #Shorts",
                "hook": "In 1290, a Franciscan friar wrote down machines that horrified the Church...",
                "angle": "Roger Bacon prophecy, banned scientific texts, mechanical foresight",
                "pinned_comment": "Was this divine inspiration or forbidden lost technology? 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "150% - 160%",
                "why_viral": "Nghịch lý thời gian lịch sử khơi gợi sự tò mò tri thức cực cao",
                "fixes_flaw": "Đa dạng hóa kho nội dung, chuyển từ bí ẩn biển sâu sang nghịch lý tiên tri lịch sử có tài liệu xác thực, kích thích tranh luận mạnh trong comment.",
                "trend_alignment": "Nghịch lý thời gian và văn bản cổ tiên tri công nghệ (Historical Time Glitches & Prophecies)."
            }
        ]
    elif "animal" in niche_id or "rescue" in niche_id or "pet" in niche_id:
        return [
            {
                "topic": "The Abandoned Golden Retriever Puppy Who Waited In The Freezing Rain For 48 Hours",
                "title": "He Waited 48 Hours In The Freezing Rain... 💔🐾 #Shorts",
                "hook": "He was dumped on this empty road, but still believed his owners would return...",
                "angle": "Heartbreaking loyalty, trembling eyes, miracle rescue, glorious adoption transformation",
                "pinned_comment": "Drop a ❤️ if you believe every stray animal deserves love! 👇",
                "viral_formula": "Emotional Rollercoaster & Tears to Joy",
                "predicted_retention": "170% - 185%",
                "why_viral": "Đánh trúng lòng trắc ẩn tột cùng, người xem thả tim và bình luận cầu phúc cực kỳ mạnh",
                "fixes_flaw": "Tạo cao trào thị giác và cảm xúc đối nghịch cực đại (giữa sự lạnh lẽo bơ vơ và vòng tay ấm áp), giải quyết nhược điểm video mở đầu quá nhẹ nhàng.",
                "trend_alignment": "Cấu trúc biến đổi 3 giai đoạn (Before & After Emotional Transformation) đạt tỷ lệ thả tim và chia sẻ cao nhất YouTube Shorts."
            },
            {
                "topic": "The Exact Moment An Abused Shelter Dog Realizes He Will Never Be Hurt Again",
                "title": "The Moment He Realized He Was Finally SAFE 🥺❤️ #Shorts",
                "hook": "Every time a hand reached toward him, he flinched in terror... until today.",
                "angle": "Overcoming severe trauma, first gentle touch, trembling tail wag, unconditional human warmth",
                "pinned_comment": "Have you ever rescued an animal? Tell us their name below! 👇",
                "viral_formula": "Double Tap & Healing Empathy",
                "predicted_retention": "165% - 175%",
                "why_viral": "Video chữa lành tâm hồn, tỷ lệ lưu lại (Save/Share) cao nhất trong các thể loại Shorts",
                "fixes_flaw": "Tập trung vào vi biểu cảm ánh mắt sợ hãi chuyển sang tin tưởng của thú cưng, khắc phục sự dàn trải nội dung, kéo dài thời gian giữ chân (AVD > 85%).",
                "trend_alignment": "Nội dung chữa lành tâm hồn (Healing & Empathy) có tỷ lệ lưu lại (Save) cao nhất trên nền tảng."
            },
            {
                "topic": "Stray Dog Risks Its Own Life to Drag An Injured Kitten Out Of Highway Traffic",
                "title": "Stray Dog RISKS Life to Save A Tiny Kitten 🐕🐱 #Shorts",
                "hook": "Cars were speeding past at 50 mph, but this stray dog saw something moving in the road...",
                "angle": "Cross-species empathy, heroic rescue instinct, inseparable animal brotherhood",
                "pinned_comment": "Animals truly have purer hearts than humans. Agree or disagree? 👇",
                "viral_formula": "Debate in Comments & Heroic Miracle",
                "predicted_retention": "160% - 170%",
                "why_viral": "Câu chuyện động vật cứu nhau luôn đạt triệu view nhờ tính kỳ tích phi thường",
                "fixes_flaw": "Tạo tình huống khẩn cấp giật gân (nguy hiểm giao thông) ngay giây đầu tiên, chặn đứng tỷ lệ lướt qua (Swipe Away Rate).",
                "trend_alignment": "Kỳ tích động vật cứu nhau xuyên loài (Cross-species rescue miracle) luôn chạm mốc triệu view thế giới."
            }
        ]
    elif "wildlife" in niche_id or "predator" in niche_id or "nature" in niche_id:
        return [
            {
                "topic": "Why Even a Pride of Adult Lions Refuses to Fight The Honey Badger",
                "title": "Why Lions REFUSE to Fight The Honey Badger 🦡🦁 #Shorts",
                "hook": "A male lion has a bone-crushing 1,000 PSI bite force... yet it backs away from this 20-pound creature.",
                "angle": "Complete venom immunity, loose impenetrable rubber skin, terrifying fearless psychology",
                "pinned_comment": "Who is the REAL King of the Savanna? Drop your vote below! 👇",
                "viral_formula": "Shock Fact & Savanna Hierarchy",
                "predicted_retention": "175% - 185%",
                "why_viral": "Nhân vật Honey Badger là hiện tượng meme huyền thoại về sự lì lợm và bản lĩnh sinh tồn",
                "fixes_flaw": "Khắc phục 100% lỗi lệch ngách (đăng vũ trụ/hố đen/tâm lý trên kênh động vật) — đưa kênh về chuẩn ngách mãnh thú săn mồi với biểu tượng bất bại Honey Badger.",
                "trend_alignment": "Hiện tượng meme sức mạnh sinh tồn lì lợm (Animal Hierarchy Subversion) thu hút lượng bình luận tranh cãi khổng lồ."
            },
            {
                "topic": "What Actually Happens in The 3 Seconds Before a Cheetah Strikes at 70 MPH",
                "title": "The 3 Seconds Before a Cheetah STRIKES at 70 MPH 🐆⚡ #Shorts",
                "hook": "Her heart rate explodes to 250 beats per minute, yet her gaze never moves one millimeter...",
                "angle": "Biomechanical perfection, non-retractable claw traction, flexible spine energy storage",
                "pinned_comment": "Drop a ⚡ if you think the Cheetah is nature's ultimate speed machine!",
                "viral_formula": "Adrenaline Surge & High-Speed Physics",
                "predicted_retention": "165% - 175%",
                "why_viral": "Hình ảnh rượt đuổi tốc độ cao slow-motion kết hợp thông số giật gân giữ chân 100% người xem",
                "fixes_flaw": "Khắc phục nhịp điệu chậm, ứng dụng cơ học tốc độ cao và góc quay slow-motion để khóa chặt mắt người xem trong 3 giây mở màn.",
                "trend_alignment": "Giải phẫu cơ sinh học động vật săn mồi (Predator Biomechanics & Sensory Overload)."
            },
            {
                "topic": "The Only Ocean Predator That Terrifies The Great White Shark",
                "title": "The ONLY Creature Great White Sharks FEAR 🦈🌊 #Shorts",
                "hook": "Great White Sharks rule the open ocean... until a pod of Orcas appears on the horizon.",
                "angle": "Tactical suffocation, tonic immobility induction, surgical liver extraction",
                "pinned_comment": "Do you think Orcas are the most intelligent predators on Earth? 👇",
                "viral_formula": "Debate in Comments & Ocean Mystery",
                "predicted_retention": "170% - 180%",
                "why_viral": "Bí ẩn sức mạnh vượt trội của cá voi sát thủ (Orca) luôn là thỏi nam châm hút hàng triệu lượt xem tranh luận",
                "fixes_flaw": "Định vị lại tệp người xem yêu thích kẻ săn mồi đại dương, thay thế các video thảm họa biển sâu chung chung bằng cuộc chiến trí tuệ đỉnh cao Orca vs Cá mập.",
                "trend_alignment": "Đại chiến kẻ thống trị đại dương (Apex Clash Orca vs Shark) luôn là nam châm hút triệu view tranh luận."
            }
        ]
    else:  # psychology / default
        return [
            {
                "topic": "The 3-Second Cold Silence Trick to Flip Any Interrogation",
                "title": "How to Use The 3-Second SILENCE Trick 🤫🧠 #Shorts",
                "hook": "When someone tries to manipulate or pressure you, NEVER speak...",
                "angle": "Status reversal through unblinking silence, discomfort amplification",
                "pinned_comment": "Have you ever silenced an arrogant person with this? Tell your story 👇",
                "viral_formula": "Double Tap & Protect",
                "predicted_retention": "155% - 165%",
                "why_viral": "Vũ khí tự vệ giao tiếp thực chiến ai cũng muốn học ngay lập tức",
                "fixes_flaw": "Kế thừa trọn vẹn công thức video đạt 514 views (3 Signs They're Using You) nhưng nâng cấp thành vũ khí tự vệ phản đòn cụ thể, thay vì chỉ cảnh báo thụ động.",
                "trend_alignment": "Nghệ thuật kiểm soát vị thế bằng sự im lặng (High-Status Frame Control & Cold Silence) đang viral khắp TikTok & Shorts quốc tế."
            },
            {
                "topic": "3 Signs Someone is Intimidated by Your Quiet Presence",
                "title": "3 Signs People Are INTIMIDATED by You 👑 #Shorts",
                "hook": "If people act strangely awkward around you, it means one thing...",
                "angle": "Over-explaining, avoiding direct eye contact, nervous laughter",
                "pinned_comment": "Do people often misjudge your quietness for arrogance? 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "150% - 160%",
                "why_viral": "Đánh trúng đối tượng người hướng nội (introverts) muốn khẳng định giá trị bản thân",
                "fixes_flaw": "Khắc phục điểm yếu của video flop 'Loneliness Is Your Superpower' (quá triết lý, thụ động), chuyển sang tâm lý vị thế cao giúp người xem tự tin.",
                "trend_alignment": "Đánh trúng tâm lý người hướng nội muốn nhìn thấu phản ứng người khác (Introvert Empowerment & Status Cues)."
            },
            {
                "topic": "How High-Value Minds React When Someone Insults Them in Public",
                "title": "How Elite Minds React When INSULTED Publicly 🛡️ #Shorts",
                "hook": "When an amateur insults you, never insult them back. Do this...",
                "angle": "Calm pity response, turning the audience against the aggressor, emotional detachment",
                "pinned_comment": "Drop a 🧠 if you choose peace over petty drama!",
                "viral_formula": "Double Tap & Protect",
                "predicted_retention": "160% - 170%",
                "why_viral": "Kỹ năng phản đòn thanh lịch, người xem bấm lưu (save) và chia sẻ cực nhiều",
                "fixes_flaw": "Tạo tình huống kịch tính có tính ứng dụng xã hội cao, biến cảm giác tức giận thành phản đòn thanh lịch, kích thích người xem xem lặp lại (Loop Rate > 150%).",
                "trend_alignment": "Tâm lý học khắc kỷ thực chiến và phòng vệ thao túng nơi công cộng (Stoic Power Dynamic in Public)."
            }
        ]


# ──────────────────────────────────────────────────────────────────────────────
# 3.5 KIỂM TRA AN TOÀN & TỐI ƯU TRƯỚC KHI XUẤT BẢN (PRE-PUBLISH SAFETY AUDIT)
# ──────────────────────────────────────────────────────────────────────────────

def check_pre_publish_safety(job_id: int, channel_id: str) -> Dict[str, Any]:
    """
    Kiểm tra an toàn & tối ưu toàn diện trước khi đăng Shorts:
    1. Kiểm tra trùng lặp nội dung / tiêu đề với kho video của kênh (Duplicate Check).
    2. Kiểm tra khoảng cách thời gian đăng (Cannibalization Proximity Check):
       - Tránh tự bóp tương tác do đăng quá sát video trước (< 3 tiếng, đặc biệt < 1 tiếng như sự cố 44s ngày 26/09).
    3. Kiểm tra tối ưu hóa Shorts (Hook 3s đầu, độ dài tiêu đề <= 60 ký tự, thẻ #Shorts).
    4. Đề xuất khe giờ vàng an toàn kế tiếp để người dùng cân nhắc.
    """
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            return {"has_warnings": False, "is_safe": True, "error": f"Không tìm thấy job #{job_id}"}
        
        job_title = job.title or job.topic or ""
        job_topic = job.topic or ""
        scenes_json = job.scenes_json

    portfolio = get_channel_existing_portfolio(channel_id)
    channel_name = portfolio.get("channel_name", channel_id)
    channel_niche = portfolio.get("channel_niche", "general")
    existing_titles = portfolio.get("existing_titles", [])
    
    # Loại trừ chính tiêu đề của job này nếu có trong existing_titles
    candidate_existing = [t for t in existing_titles if t.strip() and t.strip().lower() != job_title.strip().lower()]

    warnings: List[str] = []
    recommendations: List[str] = []
    is_duplicate = False
    duplicate_match = ""
    duplicate_score = 0.0

    # 1. KIỂM TRA TRÙNG LẶP NỘI DUNG (DUPLICATE CHECK)
    if candidate_existing:
        is_dup_t, score_t, match_t = check_idea_similarity(job_title, candidate_existing, similarity_threshold=0.38)
        is_dup_p, score_p, match_p = check_idea_similarity(job_topic, candidate_existing, similarity_threshold=0.38)
        if is_dup_t or is_dup_p:
            is_duplicate = True
            duplicate_score = max(score_t, score_p)
            duplicate_match = match_t or match_p
            warnings.append(
                f"**Trùng lặp nội dung:** Tiêu đề có độ tương đồng {int(duplicate_score * 100)}% với video cũ: *\"{duplicate_match}\"* (dễ bị thuật toán đánh giá là lặp lại/spam đề tài)."
            )
            recommendations.append("Nên đổi góc tiếp cận hoặc thay đổi tiêu đề để tạo sự mới lạ 100%.")

    # 2. KIỂM TRA KHOẢNG CÁCH THỜI GIAN ĐĂNG (CANNIBALIZATION CHECK)
    hours_since_last = portfolio.get("hours_since_last_video", 999.0)
    slot_dt, slot_iso, vn_label, us_label = get_next_available_golden_slot(channel_id)

    if hours_since_last < 3.0:
        if hours_since_last < 1.0:
            mins_ago = max(1, int(hours_since_last * 60))
            warnings.append(
                f"🚨 **Nguy cơ Tự bóp tương tác (Cannibalization):** Kênh vừa xuất bản video cách đây chỉ **{mins_ago} phút**! "
                f"Đăng liền lúc 2 video (tương tự sự cố 44 giây ngày 26/09) sẽ khiến thuật toán Shorts Feed chia đôi lượt phân phối, dẫn tới cả 2 video đều bị flop (dưới 10 views)."
            )
        else:
            warnings.append(
                f"⚠️ **Khoảng cách đăng quá sát:** Kênh vừa có video mới cách đây **{hours_since_last:.1f} giờ**. "
                f"Nên giãn cách tối thiểu 4-12 giờ để video trước tích luỹ đủ lượt xem ban đầu trên Shorts Feed."
            )
        recommendations.append(f"Dời lịch đăng sang khung giờ vàng kế tiếp: **{vn_label}** (*{us_label}*) để tối ưu hóa lượt phân phối.")

    # 3. KIỂM TRA TỐI ƯU TIÊU ĐỀ & HOOK 3S ĐẦU (OPTIMIZATION CHECK)
    clean_title_no_shorts = re.sub(r"#\w+", "", job_title).strip()
    if len(clean_title_no_shorts) > 60:
        warnings.append(
            f"⚠️ **Tiêu đề quá dài ({len(clean_title_no_shorts)} ký tự):** Trên ứng dụng YouTube Shorts di động, tiêu đề dài hơn 60 ký tự sẽ bị cắt bớt (...), làm giảm tỷ lệ người xem tò mò bấm vào."
        )
        recommendations.append("Rút ngắn tiêu đề dưới 60 ký tự, đưa từ khóa kích thích tò mò lên đầu.")

    first_scene_text = ""
    if scenes_json:
        try:
            s_data = json.loads(scenes_json)
            scenes_list = s_data.get("scenes") or (s_data if isinstance(s_data, list) else [])
            if scenes_list and isinstance(scenes_list[0], dict):
                first_scene_text = scenes_list[0].get("voiceover") or scenes_list[0].get("text") or ""
        except Exception:
            pass

    if first_scene_text:
        words = first_scene_text.strip().split()
        if len(words) > 18:
            warnings.append(
                f"⚠️ **Cảnh báo Hook 3s đầu quá dài ({len(words)} từ):** Câu mở đầu dài dòng có nguy cơ làm tụt tỷ lệ Ở lại xem (Stayed to watch) dưới 30% như các video trước."
            )
            recommendations.append("Tối ưu câu hook 3s đầu ngắn gọn (5-10 từ), giật gân, đánh thẳng vào sự tò mò.")

    has_warnings = len(warnings) > 0
    return {
        "has_warnings": has_warnings,
        "is_safe": not has_warnings,
        "job_id": job_id,
        "channel_id": channel_id,
        "channel_name": channel_name,
        "channel_niche": channel_niche,
        "title": job_title,
        "hours_since_last_video": hours_since_last,
        "warnings": warnings,
        "recommendations": recommendations,
        "next_slot_vn": vn_label,
        "next_slot_us": us_label,
        "next_slot_dt": slot_dt,
        "next_slot_iso": slot_iso
    }


# ──────────────────────────────────────────────────────────────────────────────
# 4. HỆ THỐNG THEO DÕI KPI & BÁO CÁO CẢNH BÁO (CHANNEL KPI MONITOR)
# ──────────────────────────────────────────────────────────────────────────────

def calculate_channel_kpi(channel_id: str) -> Dict[str, Any]:
    """
    Tính toán chỉ số KPI sức khỏe của kênh:
    - Trạng thái: ON_TRACK (< 24h), WARNING (24-48h), OVERDUE_CRITICAL (>= 48h / 2 ngày trễ hạn).
    - Kiểm tra xem có video nào đang chờ duyệt (AWAITING_APPROVAL) hoặc đang render không.
    """
    portfolio = get_channel_existing_portfolio(channel_id)
    hours = portfolio["hours_since_last_video"]
    days = portfolio["days_since_last_video"]

    # Xác định trạng thái KPI
    if hours < KPI_WARNING_HOURS:
        kpi_status = "ON_TRACK"
        status_label = "✅ Đúng Chỉ Tiêu (Tương Tác Cao)"
        color = "green"
        urgency = "normal"
    elif hours < KPI_CRITICAL_OVERDUE_HOURS:
        kpi_status = "WARNING"
        status_label = "⚠️ Sắp Trễ Hạn (Cần Lên Video Mới)"
        color = "yellow"
        urgency = "medium"
    else:
        kpi_status = "OVERDUE_CRITICAL"
        status_label = "🚨 TRỄ DEADLINE (Quá 2 Ngày — Tụt Tương Tác!)"
        color = "red"
        urgency = "critical"

    # Kiểm tra xem kênh có job nào đang trong luồng sản xuất hoặc chờ duyệt không
    pending_job = None
    with get_db() as db:
        job = db.query(VideoJob).filter(
            VideoJob.target_channel_id == channel_id,
            VideoJob.status.in_(["RENDERING", "SCRIPT_READY", "RENDERED", "AWAITING_APPROVAL"])
        ).order_by(VideoJob.created_at.desc()).first()

        if job:
            pending_job = {
                "job_id": job.id,
                "status": job.status,
                "title": job.title or job.topic,
                "is_auto_generated": bool(job.is_auto_generated),
                "approval_deadline": job.approval_deadline.isoformat() if job.approval_deadline else None,
                "video_path": job.video_path
            }

    return {
        "channel_id": channel_id,
        "channel_name": portfolio["channel_name"],
        "niche": portfolio["channel_niche"],
        "language": portfolio["channel_lang"],
        "handle_or_url": portfolio["handle_or_url"],
        "total_existing_videos": portfolio["total_existing_count"],
        "hours_since_last_video": hours,
        "days_since_last_video": days,
        "last_activity_time": portfolio["last_activity_time"].isoformat() if portfolio["last_activity_time"] else None,
        "kpi_status": kpi_status,
        "status_label": status_label,
        "status_color": color,
        "urgency": urgency,
        "is_overdue_2_days": hours >= KPI_CRITICAL_OVERDUE_HOURS,
        "pending_job": pending_job
    }


def run_network_kpi_audit() -> Dict[str, Any]:
    """
    Kiểm tra toàn bộ mạng lưới kênh YouTube:
    - Tổng hợp danh sách kênh đúng tiến độ, cảnh báo, trễ deadline 2 ngày.
    """
    channels = settings.get_channels_config()
    channels_kpi = []
    overdue_count = 0
    warning_count = 0
    on_track_count = 0

    for ch in channels:
        cid = ch.get("id")
        if not cid:
            continue
        kpi = calculate_channel_kpi(cid)
        channels_kpi.append(kpi)
        if kpi["kpi_status"] == "OVERDUE_CRITICAL":
            overdue_count += 1
        elif kpi["kpi_status"] == "WARNING":
            warning_count += 1
        else:
            on_track_count += 1

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "total_channels": len(channels_kpi),
        "on_track_count": on_track_count,
        "warning_count": warning_count,
        "overdue_count": overdue_count,
        "channels": channels_kpi
    }


# ──────────────────────────────────────────────────────────────────────────────
# 5. GỬI THÔNG BÁO VÀ NHẮC NHỞ TELEGRAM (TELEGRAM ALERT DISPATCHER)
# ──────────────────────────────────────────────────────────────────────────────

def send_telegram_broadcast(text: str, inline_keyboard: Optional[List[List[Dict[str, str]]]] = None):
    """
    Gửi tin nhắn thông báo/nhắc nhở khẩn cấp đến tất cả Admin/Creator qua Telegram REST API.
    Không làm sập ứng dụng nếu mạng lỗi hoặc bot token chưa cấu hình.
    """
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.debug("[TelegramAlert] Không có TELEGRAM_BOT_TOKEN, bỏ qua gửi tin.")
        return

    # Lấy danh sách ID người dùng nhận tin
    chat_ids = set()
    # 1. Từ ALLOWED_TELEGRAM_USERS
    for uid in settings.get_allowed_users().keys():
        chat_ids.add(uid)

    # 2. Từ SQLite Users
    try:
        with get_db() as db:
            db_users = db.query(User).filter(User.telegram_id != 0).all()
            for u in db_users:
                chat_ids.add(u.telegram_id)
    except Exception:
        pass

    if not chat_ids:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload_base: Dict[str, Any] = {
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    if inline_keyboard:
        payload_base["reply_markup"] = {"inline_keyboard": inline_keyboard}

    for cid in chat_ids:
        try:
            p = dict(payload_base)
            p["chat_id"] = cid
            res = http_requests.post(url, json=p, timeout=8)
            if res.status_code != 200:
                # Thử fallback plain text nếu lỗi markdown
                p["text"] = re.sub(r"[*_`\[\]()<>]", "", text)
                p.pop("parse_mode", None)
                http_requests.post(url, json=p, timeout=8)
        except Exception as ex:
            logger.debug(f"[TelegramAlert] Lỗi gửi tin cho {cid}: {ex}")


# ──────────────────────────────────────────────────────────────────────────────
# 6. TỰ ĐỘNG SẢN XUẤT VIDEO CHO KÊNH TRỄ 2 NGÀY (AUTO-GENERATION ENGINE)
# ──────────────────────────────────────────────────────────────────────────────

def trigger_auto_generation_for_overdue_channel(
    channel_id: str,
    grace_hours: float = DEFAULT_APPROVAL_GRACE_HOURS,
    force: bool = False
) -> Dict[str, Any]:
    """
    Kích hoạt tự động sản xuất video cứu tương tác khi một kênh bị trễ deadline >= 2 ngày (hoặc force=True):
    1. Kiểm tra xem có video nào đang xử lý dở không (tránh sản xuất thừa lãng phí tài nguyên).
    2. Chọn 1 ý tưởng độc lạ tốt nhất từ suggest_channel_topics (chống trùng lặp 100%).
    3. Tự động sinh kịch bản chi tiết qua ContentAgent.
    4. Tạo VideoJob trong SQLite và kích hoạt Render Pipeline trong background thread.
    5. Thiết lập thời hạn duyệt (approval_deadline = now + grace_hours).
    6. Gửi cảnh báo thúc giục người dùng kiểm tra trên Telegram và Web Dashboard!
    """
    kpi = calculate_channel_kpi(channel_id)
    if not kpi["is_overdue_2_days"] and not force:
        return {
            "success": False,
            "message": f"Kênh '{kpi['channel_name']}' chưa trễ hạn 2 ngày (mới trễ {kpi['days_since_last_video']} ngày). Dùng force=True nếu muốn ép buộc tạo ngay.",
            "kpi": kpi
        }

    # Tránh spam: nếu đã có job chờ duyệt hoặc đang render, không tạo thêm
    if kpi.get("pending_job"):
        pj = kpi["pending_job"]
        return {
            "success": False,
            "message": f"Kênh đã có video đang trong hàng đợi xử lý (#{pj['job_id']} - {pj['status']}).",
            "job_id": pj["job_id"],
            "status": pj["status"]
        }

    logger.info(f"[AutoGeneration] 🚨 Kênh '{kpi['channel_name']}' ({channel_id}) trễ {kpi['days_since_last_video']} ngày! Khởi động AI Auto-Production...")

    # 1. Gợi ý ý tưởng không trùng lặp
    topics = suggest_channel_topics(channel_id, count=1)
    if not topics:
        return {"success": False, "error": "Không thể sinh ý tưởng mới cho kênh này."}

    best_topic = topics[0]
    topic_str = best_topic.get("topic", "High Retention Shorts")
    title_str = best_topic.get("title", f"Video_{datetime.utcnow().strftime('%Y%m%d_%H%M')}")
    niche_id = kpi["niche"]
    lang = kpi["language"]

    # 2. Sinh kịch bản phân cảnh chuẩn viral
    agent = ContentAgent()
    try:
        script = agent.generate_script(
            topic=topic_str,
            channel_niche=niche_id,
            language=lang,
            director_notes="Kịch bản được kích hoạt tự động theo cơ chế cứu tương tác kênh. Nhịp độ dồn dập, hook cắt đà lướt cực mạnh, CTA bám sát kịch bản."
        )
    except Exception as e:
        logger.error(f"[AutoGeneration] Lỗi sinh kịch bản cho {channel_id}: {e}")
        return {"success": False, "error": f"Lỗi sinh kịch bản: {e}"}

    # 3. Tạo VideoJob trong Database
    now_utc = datetime.utcnow()
    deadline = now_utc + timedelta(hours=grace_hours)

    with get_db() as db:
        job = VideoJob(
            user_id=0,  # 0 = System Auto-Generator
            target_channel_id=channel_id,
            topic=topic_str,
            title=script.title or title_str,
            script=script.full_narration,
            description=script.description,
            tags=",".join(script.tags),
            scenes_json=script.to_json_str(),
            status="SCRIPT_READY",
            is_auto_generated=1,
            approval_deadline=deadline,
            progress_percent=0,
            progress_message="Hệ thống tự động lên lịch dựng video cứu tương tác..."
        )
        db.add(job)
        db.flush()
        job_id = job.id

        log_activity(
            db,
            telegram_id=0,
            username="AI_Guardian",
            action_type="AUTO_GENERATE_OVERDUE",
            details=f"Tự động sản xuất video cứu tương tác cho kênh '{kpi['channel_name']}' (Job #{job_id}): '{job.title}'",
            channel_id=channel_id
        )

    # 4. Kích hoạt Render Pipeline trong background thread
    def _background_render_worker(jid: int, ch_name: str, ch_id: str, deadl: datetime):
        import asyncio
        from app.services.pipeline import VideoPipeline

        async def _do():
            try:
                # Đổi status sang RENDERING
                with get_db() as _db:
                    _j = _db.query(VideoJob).filter(VideoJob.id == jid).first()
                    if _j:
                        _j.status = "RENDERING"
                        _j.progress_message = "Đang dựng hình ảnh, giọng đọc và ghép hiệu ứng..."

                pipeline = VideoPipeline(job_id=jid)
                await asyncio.wait_for(pipeline.execute(), timeout=600)

                # Khi render xong: chuyển sang AWAITING_APPROVAL (Chờ người dùng kiểm duyệt)
                with get_db() as _db:
                    _j = _db.query(VideoJob).filter(VideoJob.id == jid).first()
                    if _j:
                        _j.status = "AWAITING_APPROVAL"
                        _j.progress_percent = 100
                        _j.progress_message = f"Render hoàn tất! Đang chờ kiểm duyệt trước {deadl.strftime('%H:%M %d/%m')}"
                        video_title = _j.title

                # Gửi thông báo thúc giục kiểm tra qua Telegram
                grace_str = f"{grace_hours:.0f}"
                alert_text = (
                    f"🚨 **CẢNH BÁO TỤT TƯƠNG TÁC KÊNH: `{ch_name}`**\n\n"
                    f"⚠️ Kênh này đã trễ deadline đăng bài **hơn 2 ngày**!\n"
                    f"🤖 **Hệ thống AI đã tự động sản xuất video cứu nguy:**\n"
                    f"🎬 **Tiêu đề:** *{video_title}*\n"
                    f"⏱️ **Hạn chót duyệt:** `{deadl.strftime('%H:%M %d/%m/2026')}` (còn ~{grace_str} tiếng)\n\n"
                    f"👉 **Nếu bạn bận không kiểm tra hoặc không phản hồi:**\n"
                    f"Hệ thống sẽ **TỰ ĐỘNG ĐĂNG TẠM** video này vào khung giờ vàng tiếp theo để duy trì đề xuất thuật toán YouTube Shorts cho kênh!\n\n"
                    f"Vui lòng nhấn nút bên dưới để duyệt hoặc mở Web Studio:"
                )
                inline_kb = [
                    [
                        {"text": f"✅ Duyệt & Đăng ngay (#{jid})", "callback_data": f"approve_auto:{jid}:now"},
                        {"text": f"⏰ Đăng theo giờ vàng (#{jid})", "callback_data": f"approve_auto:{jid}:schedule"}
                    ],
                    [
                        {"text": f"❌ Huỷ bỏ (#{jid})", "callback_data": f"approve_auto:{jid}:cancel"}
                    ]
                ]
                send_telegram_broadcast(alert_text, inline_keyboard=inline_kb)

            except Exception as ex:
                logger.error(f"[AutoGeneration] Lỗi render job #{jid}: {ex}", exc_info=True)
                with get_db() as _db:
                    _j = _db.query(VideoJob).filter(VideoJob.id == jid).first()
                    if _j:
                        _j.status = "FAILED"
                        _j.error_message = str(ex)[:400]

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_do())
        finally:
            loop.close()

    render_thread = threading.Thread(
        target=_background_render_worker,
        args=(job_id, kpi["channel_name"], channel_id, deadline),
        daemon=True
    )
    render_thread.start()

    return {
        "success": True,
        "message": f"Đã tự động khởi tạo và đưa vào hàng đợi dựng video #{job_id} cho kênh '{kpi['channel_name']}'",
        "job_id": job_id,
        "topic": topic_str,
        "title": script.title,
        "approval_deadline": deadline.isoformat(),
        "grace_hours": grace_hours
    }


# ──────────────────────────────────────────────────────────────────────────────
# 7. CƠ CHẾ ĐĂNG TẠM KHẨN CẤP (FALLBACK GRACE-PERIOD PUBLISHER)
# ──────────────────────────────────────────────────────────────────────────────

def process_fallback_publishing() -> List[Dict[str, Any]]:
    """
    Quét tất cả các video tự động sinh (is_auto_generated=1):
    - Đang ở trạng thái AWAITING_APPROVAL.
    - Đã vượt quá thời hạn chờ duyệt (approval_deadline <= now).
    - Người dùng bận không xác nhận.
    => Tự động lên lịch hoặc đăng tạm lên YouTube để duy trì nhịp tương tác thuật toán!
    """
    now_utc = datetime.utcnow()
    published_jobs = []

    with get_db() as db:
        expired_jobs = db.query(VideoJob).filter(
            VideoJob.is_auto_generated == 1,
            VideoJob.status == "AWAITING_APPROVAL",
            VideoJob.approval_deadline <= now_utc,
            VideoJob.video_path.isnot(None)
        ).all()

        for job in expired_jobs:
            # Kiểm tra file video có tồn tại
            if not Path(job.video_path).exists():
                logger.warning(f"[FallbackPublish] File video Job #{job.id} không tồn tại trên ổ đĩa!")
                continue

            target_ch = job.target_channel_id
            job_id = job.id
            job_title = job.title or job.topic

            logger.info(f"[FallbackPublish] ⏰ Video #{job_id} ('{job_title}') hết hạn duyệt ({job.approval_deadline}). Tiến hành tự động đăng tạm cho kênh '{target_ch}'...")

            # Tính toán khung giờ vàng gần nhất
            slot_dt, slot_iso, vn_label, us_label = get_next_available_golden_slot(target_ch)

            job.status = "SCHEDULED"
            job.scheduled_time = slot_dt
            job.progress_message = f"Đã tự động đăng tạm (Lên lịch công chiếu lúc {vn_label} để duy trì tương tác)"

            log_activity(
                db,
                telegram_id=0,
                username="AI_Guardian",
                action_type="FALLBACK_AUTO_PUBLISH",
                details=f"Tự động đăng tạm Video #{job_id} '{job_title}' lên lịch công chiếu lúc {vn_label} ({us_label}) do người dùng chưa duyệt sau thời hạn",
                channel_id=target_ch
            )

            published_jobs.append({
                "job_id": job_id,
                "title": job_title,
                "channel_id": target_ch,
                "scheduled_slot_vn": vn_label,
                "scheduled_slot_us": us_label
            })

            # Thông báo Telegram
            notify_text = (
                f"⏰ **TỰ ĐỘNG ĐĂNG TẠM THÀNH CÔNG (GIỮ NHỊP TƯƠNG TÁC)**\n\n"
                f"📺 **Kênh:** `{target_ch}`\n"
                f"🎬 **Video #{job_id}:** *{job_title}*\n"
                f"⌛ **Lý do:** Đã qua thời hạn duyệt nhưng bạn đang bận. Hệ thống đã kích hoạt cơ chế bảo vệ kênh.\n"
                f"📅 **Lịch công chiếu:** `{vn_label}` (Khung giờ vàng chuẩn quốc tế: `{us_label}`)\n\n"
                f"✅ Thuật toán phân phối Shorts Feed của kênh tiếp tục được duy trì ổn định!"
            )
            send_telegram_broadcast(notify_text)

    return published_jobs


# ──────────────────────────────────────────────────────────────────────────────
# 8. TIẾN TRÌNH CHẠY NGẦM ĐỊNH KỲ (CHANNEL INTELLIGENCE BACKGROUND WORKER)
# ──────────────────────────────────────────────────────────────────────────────

class ChannelIntelligenceWorker:
    """Worker chạy ngầm định kỳ (mỗi 30 phút):
    - Kiểm tra fallback publish cho các video hết hạn duyệt.
    - Kiểm tra KPI sức khỏe các kênh.
    - Tự động kích hoạt sản xuất video cho các kênh trễ deadline >= 2 ngày.
    """
    _instance = None
    _thread = None
    _running = False

    @classmethod
    def start(cls, check_interval_minutes: int = 30):
        if cls._running:
            return
        cls._running = True

        def _loop():
            logger.info(f"🚀 [ChannelIntelligence] Background Worker đã khởi động (chu kỳ kiểm tra: {check_interval_minutes} phút)!")
            while cls._running:
                try:
                    # 1. Xử lý đăng tạm cho các video hết hạn duyệt
                    process_fallback_publishing()

                    # 2. Quét KPI toàn mạng lưới kênh
                    audit = run_network_kpi_audit()
                    overdue_channels = [c for c in audit.get("channels", []) if c.get("kpi_status") == "OVERDUE_CRITICAL"]

                    for ch in overdue_channels:
                        cid = ch["channel_id"]
                        # Nếu kênh chưa có job đang chạy, tự động kích hoạt
                        if not ch.get("pending_job"):
                            logger.info(f"[ChannelIntelligence] Phát hiện kênh '{cid}' trễ deadline 2 ngày! Tự động sản xuất video cứu nguy...")
                            trigger_auto_generation_for_overdue_channel(cid)

                except Exception as ex:
                    logger.error(f"[ChannelIntelligence] Lỗi chu kỳ worker: {ex}")

                # Chờ đến chu kỳ tiếp theo
                import time
                for _ in range(check_interval_minutes * 6):
                    if not cls._running:
                        break
                    time.sleep(10)

        cls._thread = threading.Thread(target=_loop, daemon=True, name="ChannelIntelligenceWorker")
        cls._thread.start()

    @classmethod
    def stop(cls):
        cls._running = False
