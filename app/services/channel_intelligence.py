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
    last_activity_time: Optional[datetime] = None

    # A. Cào dữ liệu Live từ YouTube
    yt_stats = {}
    try:
        yt_stats = fetch_channel_live_stats(handle_or_url)
        if yt_stats.get("success"):
            for vid in yt_stats.get("videos", []):
                t = vid.get("title", "").strip()
                if t and t not in existing_titles:
                    existing_titles.append(t)
                if vid.get("is_top_performer") or vid.get("views_num", 0) >= 300:
                    top_performers.append({
                        "title": t,
                        "views_num": vid.get("views_num", 0),
                        "views_text": vid.get("views_text", ""),
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
        "last_activity_time": last_activity_time,
        "hours_since_last_video": round(hours_since_last_video, 1),
        "days_since_last_video": round(days_since_last_video, 2),
        "total_existing_count": len(existing_titles)
    }


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
    - Bám sát hồ sơ ngách (Niche Profile: Tâm lý, Bí ẩn, Tài chính, v.v.).
    - Học tập từ các video thành công nhất của kênh (Top Performers).
    - LỌC TRÙNG LẶP TUYỆT ĐỐI (Strict De-duplication): Kiểm tra qua check_idea_similarity với mọi video cũ.
    """
    portfolio = get_channel_existing_portfolio(channel_id)
    niche_id = portfolio["channel_niche"]
    niche_profile = get_niche(niche_id)
    channel_name = portfolio["channel_name"]
    lang = portfolio["channel_lang"]

    all_existing = list(portfolio["existing_titles"])
    if exclude_additional:
        all_existing.extend(exclude_additional)

    # Xây dựng danh sách video cũ để AI né tránh
    clean_existing_prompt = "\n".join([f"- \"{t}\"" for t in all_existing[:15]])
    if not clean_existing_prompt:
        clean_existing_prompt = "(Chưa có video nào — đây là video khởi đầu của kênh!)"

    # Xây dựng dữ liệu Top Performers làm hình mẫu lan toả
    top_performer_prompt = ""
    if portfolio["top_performers"]:
        top_lines = [f"- \"{p['title']}\" ({p['views_text']})" for p in portfolio["top_performers"][:3]]
        top_performer_prompt = f"""
🏆 PROVEN HIGH-RETENTION BREAKTHROUGH VIDEOS ON THIS CHANNEL:
{chr(10).join(top_lines)}
Winning Mechanism: High-curiosity primal trigger, instant pattern interrupt, urgent psychological or existential stakes.
Your new ideas MUST inherit this winning emotional DNA without copying the same subject!
"""

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
🎯 OBJECTIVE:
Generate exactly {count + 2} completely unique, high-retention Shorts concepts tailored specifically to '{channel_name}'.
Each concept must trigger intense visceral curiosity (AVD > 80%, loop rate > 150%).

Return ONLY a valid JSON array of objects with the following structure:
[
  {{
    "topic": "Core punchy topic concept (e.g. The 3-second silence interrogation trick)",
    "title": "Viral YouTube Shorts Title with Emojis (under 60 chars)",
    "hook": "Electric 3-second pattern interrupt opening line (5-8 words)",
    "angle": "Micro-narrative breakdown & specific observable behaviors or phenomena",
    "pinned_comment": "Provocative engagement bait question to pin at top of comments",
    "viral_formula": "Double Tap & Protect | Debate in Comments | Curiosity Part 2",
    "predicted_retention": "150% - 165%",
    "why_viral": "Giải thích ngắn gọn bằng tiếng Việt vì sao chủ đề này sẽ bùng nổ views"
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
                "title": "3 Deep Ocean Sounds Science CANNOT Explain 🌊📻",
                "hook": "In 1997, oceanic sensors detected this terrifying subterranean creature sound...",
                "angle": "The Bloop, Julia sound, Mariana acoustic anomalies",
                "pinned_comment": "Do you believe there are unknown titanic leviathans in the abyss? 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "160% - 170%",
                "why_viral": "Ăn theo video 1.2K views 'Why You're Terrified of Deep Water' — khai thác âm thanh bí ẩn đại dương"
            },
            {
                "topic": "The Real Reason Why Nobody is Allowed to Dive Under the Sphinx",
                "title": "Why Divers Are BANNED Under The Great Sphinx 📜👁️",
                "hook": "Under the right paw of the Sphinx lies a secret sealed underground chamber...",
                "angle": "Acoustic seismology findings, Hall of Records, Egyptian secrecy",
                "pinned_comment": "What do you think is hidden under the Sphinx? Drop your theories 👇",
                "viral_formula": "Curiosity Part 2",
                "predicted_retention": "155% - 165%",
                "why_viral": "Bí ẩn Ai Cập cổ đại muôn thuở kích thích người xem xem hết video"
            },
            {
                "topic": "The Medieval Manuscript That Predicted Modern Technology in 1290",
                "title": "The 1290 Monk Who PREDICTED Airplanes & Submarines 📜⚡",
                "hook": "In 1290, a Franciscan friar wrote down machines that horrified the Church...",
                "angle": "Roger Bacon prophecy, banned scientific texts, mechanical foresight",
                "pinned_comment": "Was this divine inspiration or forbidden lost technology? 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "150% - 160%",
                "why_viral": "Nghịch lý thời gian lịch sử khơi gợi sự tò mò tri thức cực cao"
            }
        ]
    else:  # psychology / default
        return [
            {
                "topic": "The 3-Second Cold Silence Trick to Flip Any Interrogation",
                "title": "How to Use The 3-Second SILENCE Trick 🤫🧠 #psychology",
                "hook": "When someone tries to manipulate or pressure you, NEVER speak...",
                "angle": "Status reversal through unblinking silence, discomfort amplification",
                "pinned_comment": "Have you ever silenced an arrogant person with this? Tell your story 👇",
                "viral_formula": "Double Tap & Protect",
                "predicted_retention": "155% - 165%",
                "why_viral": "Vũ khí tự vệ giao tiếp thực chiến ai cũng muốn học ngay lập tức"
            },
            {
                "topic": "3 Signs Someone is Intimidated by Your Quiet Presence",
                "title": "3 Signs People Are INTIMIDATED by You 👑 #psychology",
                "hook": "If people act strangely awkward around you, it means one thing...",
                "angle": "Over-explaining, avoiding direct eye contact, nervous laughter",
                "pinned_comment": "Do people often misjudge your quietness for arrogance? 👇",
                "viral_formula": "Debate in Comments",
                "predicted_retention": "150% - 160%",
                "why_viral": "Đánh trúng đối tượng người hướng nội (introverts) muốn khẳng định giá trị bản thân"
            },
            {
                "topic": "How High-Value Minds React When Someone Insults Them in Public",
                "title": "How Elite Minds React When INSULTED Publicly 🛡️ #psychology",
                "hook": "When an amateur insults you, never insult them back. Do this...",
                "angle": "Calm pity response, turning the audience against the aggressor, emotional detachment",
                "pinned_comment": "Drop a 🧠 if you choose peace over petty drama!",
                "viral_formula": "Double Tap & Protect",
                "predicted_retention": "160% - 170%",
                "why_viral": "Kỹ năng phản đòn thanh lịch, người xem bấm lưu (save) và chia sẻ cực nhiều"
            }
        ]


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
