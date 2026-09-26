"""
Smart Golden Slot Scheduler — Thuật toán tính toán và phân bổ khe giờ vàng tự động.
Đảm bảo:
- Tối ưu 2 video / ngày vào các khung giờ có lưu lượng người xem Shorts cao nhất tại Mỹ.
- Tự động tránh trùng lịch: Nếu khung giờ đã có video, tự động nhảy sang khung giờ vàng tiếp theo.
- Tương thích 100% với YouTube Data API v3 (publishAt).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Tuple, Dict, Any, List, Optional
from app.database.models import get_db, VideoJob

logger = logging.getLogger(__name__)

# Múi giờ Việt Nam (UTC+7)
VN_TZ = timezone(timedelta(hours=7))

# ──────────────────────────────────────────────────────────────────────────────
# CẤU HÌNH KHUNG GIỜ VÀNG (GOLDEN SLOTS) CHO KHÁN GIẢ TIẾNG ANH (MỸ / TOÀN CẦU)
# ──────────────────────────────────────────────────────────────────────────────
# Slot 1: 20:00 Giờ VN = 13:00 UTC = 8:00 Sáng New York (EST) / 9:00 Sáng Bờ Đông
# Slot 2: 00:30 Đêm VN = 17:30 UTC = 12:30 Trưa New York (EST) / Giờ nghỉ trưa Mỹ
GOLDEN_SLOTS_UTC = [
    {"hour": 13, "minute": 0, "label_vn": "20:00 Tối", "label_us": "8:00 Sáng EST (Đi làm/Ăn sáng Mỹ)"},
    {"hour": 17, "minute": 30, "label_vn": "00:30 Đêm", "label_us": "12:30 Trưa EST (Nghỉ trưa Mỹ - Đỉnh xem Shorts)"},
]

WEEKDAY_VN_MAP = {
    0: "Thứ Hai",
    1: "Thứ Ba",
    2: "Thứ Tư",
    3: "Thứ Năm",
    4: "Thứ Sáu",
    5: "Thứ Bảy",
    6: "Chủ Nhật"
}


def get_next_available_golden_slot(
    channel_id: str = "channel_maya",
    min_lead_minutes: int = 20
) -> Tuple[datetime, str, str, str]:
    """
    Tính toán khe giờ vàng tiếp theo còn trống cho kênh.
    
    Returns:
        (slot_utc_datetime, slot_iso_str, display_vn, display_us)
        Ví dụ:
        (
            datetime(2026, 9, 26, 13, 0, tzinfo=timezone.utc),
            "2026-09-26T13:00:00Z",
            "20:00 Thứ Bảy, 26/09/2026 (Giờ VN)",
            "8:00 Sáng EST (New York) — Cao điểm sáng Mỹ"
        )
    """
    now_utc = datetime.now(timezone.utc)
    min_allowed_time = now_utc + timedelta(minutes=min_lead_minutes)

    # 1. Quét database tìm tất cả video đã được lên lịch hoặc đã đăng của kênh này
    occupied_slots_utc = []
    with get_db() as db:
        scheduled_jobs = db.query(VideoJob).filter(
            VideoJob.target_channel_id == channel_id,
            VideoJob.status.in_(["SCHEDULED", "PUBLISHED"]),
            VideoJob.scheduled_time.isnot(None)
        ).all()

        for j in scheduled_jobs:
            if j.scheduled_time:
                # Đảm bảo datetime có timezone UTC
                st = j.scheduled_time
                if st.tzinfo is None:
                    st = st.replace(tzinfo=timezone.utc)
                occupied_slots_utc.append(st)

    logger.debug(f"[SmartScheduler] Kênh {channel_id} hiện có {len(occupied_slots_utc)} khe giờ đã đặt lịch.")

    # 2. Duyệt tìm khe giờ trống bắt đầu từ ngày hôm nay đến 30 ngày tiếp theo
    # Duyệt tối đa 30 ngày (60 khe giờ)
    start_date = now_utc.date()
    for day_offset in range(35):
        target_date = start_date + timedelta(days=day_offset)
        for slot in GOLDEN_SLOTS_UTC:
            candidate_dt = datetime(
                year=target_date.year,
                month=target_date.month,
                day=target_date.day,
                hour=slot["hour"],
                minute=slot["minute"],
                second=0,
                tzinfo=timezone.utc
            )

            # Khe giờ phải nằm sau thời điểm tối thiểu (cần ít nhất 20 phút để YouTube xử lý)
            if candidate_dt <= min_allowed_time:
                continue

            # Kiểm tra xem khe giờ này đã bị chiếm dụng chưa (cách nhau dưới 2 tiếng được coi là trùng khe)
            is_occupied = False
            for occ in occupied_slots_utc:
                diff_seconds = abs((candidate_dt - occ).total_seconds())
                if diff_seconds < 7200:  # Trùng trong khoảng 2 tiếng
                    is_occupied = True
                    break

            if not is_occupied:
                # Tìm thấy khe giờ trống tối ưu!
                slot_iso = candidate_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

                # Chuyển đổi sang giờ Việt Nam để hiển thị
                candidate_vn = candidate_dt.astimezone(VN_TZ)
                weekday_name = WEEKDAY_VN_MAP.get(candidate_vn.weekday(), "Ngày")
                display_vn = f"{slot['label_vn']} {weekday_name}, {candidate_vn.strftime('%d/%m/%Y')} (Giờ VN)"
                display_us = slot["label_us"]

                logger.info(
                    f"🎯 [SmartScheduler] Đã xếp lịch tối ưu cho kênh {channel_id}: "
                    f"{display_vn} ({slot_iso}) — {display_us}"
                )
                return candidate_dt, slot_iso, display_vn, display_us

    # Fallback dự phòng nếu lịch quá đầy (sau 35 ngày):
    fallback_dt = min_allowed_time + timedelta(hours=2)
    fallback_iso = fallback_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    fallback_vn = fallback_dt.astimezone(VN_TZ).strftime("%H:%M %d/%m/%Y (Giờ VN)")
    return fallback_dt, fallback_iso, fallback_vn, "Khung giờ dự phòng"


def get_channel_upcoming_schedule(channel_id: str = "channel_maya", limit: int = 10) -> List[Dict[str, Any]]:
    """Lấy danh sách các video đã lên lịch trong tương lai của kênh để theo dõi."""
    now_utc = datetime.now(timezone.utc)
    results = []
    with get_db() as db:
        jobs = db.query(VideoJob).filter(
            VideoJob.target_channel_id == channel_id,
            VideoJob.status == "SCHEDULED",
            VideoJob.scheduled_time.isnot(None),
            VideoJob.scheduled_time >= now_utc
        ).order_by(VideoJob.scheduled_time.asc()).limit(limit).all()

        for j in jobs:
            st = j.scheduled_time
            if st.tzinfo is None:
                st = st.replace(tzinfo=timezone.utc)
            st_vn = st.astimezone(VN_TZ)
            weekday_name = WEEKDAY_VN_MAP.get(st_vn.weekday(), "")
            results.append({
                "job_id": j.id,
                "title": j.title or j.topic,
                "scheduled_iso": st.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "scheduled_vn": f"{st_vn.strftime('%H:%M')} {weekday_name}, {st_vn.strftime('%d/%m/%Y')}",
                "youtube_url": f"https://youtu.be/{j.youtube_video_id}" if j.youtube_video_id else None
            })
    return results
