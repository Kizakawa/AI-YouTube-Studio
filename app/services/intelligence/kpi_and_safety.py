"""app.services.intelligence.kpi_and_safety — Kiểm tra an toàn trước khi đăng & giám sát KPI định kỳ toàn mạng lưới."""
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

from app.services.intelligence.deduplication import check_idea_similarity, ensure_unique_title
from app.services.intelligence.portfolio_scanner import get_channel_existing_portfolio


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

    # Kiểm tra xem kênh có job nào đang trong luồng sản xuất hoặc chờ duyệt không (chỉ tính trong 3 giờ gần nhất)
    pending_job = None
    recent_cutoff = datetime.utcnow() - timedelta(hours=3)
    with get_db() as db:
        job = db.query(VideoJob).filter(
            VideoJob.target_channel_id == channel_id,
            VideoJob.status.in_(["RENDERING", "SCRIPT_READY", "RENDERED", "AWAITING_APPROVAL"]),
            VideoJob.created_at >= recent_cutoff,
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
