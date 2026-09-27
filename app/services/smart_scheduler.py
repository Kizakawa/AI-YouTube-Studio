"""
Smart Golden Slot Scheduler — Thuật toán tính toán và phân bổ khe giờ vàng tự động chuẩn quốc tế.
Đảm bảo:
- Tối ưu 4 khung giờ vàng / ngày theo hành vi người xem YouTube Shorts tại Mỹ & Toàn cầu.
- Giãn cách tối thiểu >= 3.5 - 4 tiếng giữa các video trên cùng một kênh để tránh hiện tượng "nuốt view" (cannibalization).
- Tự động nhận diện Channel Family (alias & brand channels) để tránh trùng lịch chéo.
- Tự động đồng bộ trạng thái: Chuyển SCHEDULED -> PUBLISHED khi đã qua thời điểm công chiếu.
- Tương thích 100% với YouTube Data API v3 (status.publishAt).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Tuple, Dict, Any, List, Optional
from app.database.models import get_db, VideoJob

logger = logging.getLogger(__name__)

# Múi giờ Việt Nam (UTC+7) và Múi giờ bờ Đông Mỹ (New York / EDT UTC-4 hoặc EST UTC-5)
VN_TZ = timezone(timedelta(hours=7))
US_TZ = timezone(timedelta(hours=-4))  # New York / EDT (tháng 3 đến tháng 11)

# Giới hạn tối đa video trên mỗi kênh trong 1 ngày
MAX_VIDEOS_PER_DAY = 2

# Khoảng cách tối thiểu an toàn giữa 2 video (3.5 giờ = 12,600 giây) để thuật toán Shorts phân bổ
MIN_SPACING_SECONDS = 12600

# ──────────────────────────────────────────────────────────────────────────────
# CẤU HÌNH CÁC KHUNG GIỜ VÀNG QUỐC TẾ (US & GLOBAL PEAKS) CHO YOUTUBE SHORTS
# ──────────────────────────────────────────────────────────────────────────────
# CẤU HÌNH CÁC KHUNG GIỜ VÀNG QUỐC TẾ (US & GLOBAL PEAKS) CHO YOUTUBE SHORTS
# ──────────────────────────────────────────────────────────────────────────────
# Căn lịch sớm hơn giờ vàng thực tế 30 - 60 phút để AI YouTube kịp render codec,
# quét bản quyền và phân phối thử nghiệm (Seed Testing Phase) trước khi bước vào đỉnh:
# 1. 12:30 UTC = 19:30 Tối VN = 08:30 AM EST (ĐÓN ĐẦU ĐỈNH VÀNG SỐ 1: Sáng Mỹ & Chiều Châu Âu)
# 2. 22:30 UTC = 05:30 Sáng VN hôm sau = 17:30 PM EST (ĐÓN ĐẦU ĐỈNH GIẢI TRÍ TỐI MỸ: Tan sở & Ăn tối)
# 3. 04:15 UTC = 11:15 Trưa VN = 00:15 AM EST (ĐÓN ĐẦU NGHỈ TRƯA & TIỀN LỆ 11:18)
GOLDEN_SLOTS_UTC = [
    {
        "hour": 12,
        "minute": 30,
        "label_vn": "19:30 Tối",
        "label_us": "08:30 AM EST (ĐÓN ĐẦU ĐỈNH VÀNG SỐ 1 — Sáng Mỹ & Chiều Châu Âu)",
        "description": "Đăng sớm hơn đỉnh 20:30-22:00 45-60 phút để YouTube AI kịp render codec và phân phối thử nghiệm.",
        "tag": "US_MORNING_PEAK",
        "rank": 1
    },
    {
        "hour": 22,
        "minute": 30,
        "label_vn": "05:30 Sáng",
        "label_us": "17:30 PM EST (ĐÓN ĐẦU ĐỈNH GIẢI TRÍ TỐI MỸ — Tan sở & Ăn tối)",
        "description": "Đăng sớm trước 18:30-20:00 EST 45-60 phút để video sẵn sàng ngay khi người Mỹ về nhà lướt Shorts.",
        "tag": "US_EVENING_PRIME",
        "rank": 2
    },
    {
        "hour": 4,
        "minute": 15,
        "label_vn": "11:15 Trưa",
        "label_us": "00:15 AM EST (Nuôi view đêm Mỹ & Đón đầu sáng Châu Âu)",
        "description": "Đón đầu trước giờ trưa (khung chuẩn video tiền nhiệm Why You're Terrified of Deep Water lúc 11:18).",
        "tag": "GLOBAL_MORNING_PREP",
        "rank": 3
    },
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


def resolve_channel_family(channel_id: str) -> List[str]:
    """
    Tìm tất cả các ID và alias liên quan đến kênh này để chống trùng lịch chéo.
    Ví dụ:
    - 'ucsrjiil67qxidtxt70z-iwa' và 'UCsRjIIl67QxIDtXT70z-iwA'
    - 'channel_psychology' và 'channel_maya'
    """
    if not channel_id:
        return ["default"]

    canonical_map = {
        "channel_psychology": "channel_maya",
        "channel_maya": "channel_maya",
        "ucsrjiil67qxidtxt70z-iwa": "ucsrjiil67qxidtxt70z-iwa",
        "ucsjll67qxidtxt70z-iwa": "ucsrjiil67qxidtxt70z-iwa",
    }
    cid_clean = channel_id.strip()
    cid_lower = cid_clean.lower()

    family = {cid_clean, cid_lower}
    if cid_lower in canonical_map:
        canon = canonical_map[cid_lower]
        family.add(canon)
        family.add(canon.lower())

    try:
        from config.settings import settings
        channels = settings.get_channels_config()
        for ch in channels:
            curr_id = ch.get("id", "")
            if curr_id.lower() in [f.lower() for f in family] or ch.get("handle", "").lower() in [f.lower() for f in family]:
                family.add(curr_id)
                family.add(curr_id.lower())
                if ch.get("handle"):
                    family.add(ch["handle"])
    except Exception:
        pass

    return list(family)


def sync_scheduled_jobs_status() -> int:
    """
    Tự động đồng bộ trạng thái: Quét các VideoJob có status == 'SCHEDULED'.
    Nếu scheduled_time <= now_utc (thời điểm công chiếu đã qua trên máy chủ YouTube),
    tự động cập nhật status = 'PUBLISHED' để database luôn phản ánh đúng thực tế!
    """
    now_utc = datetime.now(timezone.utc)
    updated_count = 0
    try:
        with get_db() as db:
            scheduled_jobs = db.query(VideoJob).filter(
                VideoJob.status == "SCHEDULED",
                VideoJob.scheduled_time.isnot(None)
            ).all()

            for job in scheduled_jobs:
                st = job.scheduled_time
                if st.tzinfo is None:
                    st = st.replace(tzinfo=timezone.utc)
                if st <= now_utc:
                    job.status = "PUBLISHED"
                    updated_count += 1
                    logger.info(
                        f"🔄 [SmartScheduler] Video #{job.id} ('{job.title[:30] if job.title else job.id}...') "
                        f"đã qua giờ hẹn ({st.strftime('%Y-%m-%d %H:%M UTC')}) -> Chuyển status = PUBLISHED."
                    )
    except Exception as e:
        logger.warning(f"Lỗi khi đồng bộ trạng thái SCHEDULED jobs: {e}")

    return updated_count


def get_next_available_golden_slot(
    channel_id: str = "channel_maya",
    min_lead_minutes: int = 20
) -> Tuple[datetime, str, str, str]:
    """
    Tính toán khe giờ vàng tiếp theo còn trống cho kênh.
    Đảm bảo:
    - Bắt đầu từ 4 khung giờ vàng quốc tế lớn nhất.
    - Tránh va chạm thời gian (< 3.5 giờ) với bất kỳ video nào đã lên lịch hoặc vừa đăng.
    - Ánh xạ đầy đủ kênh và alias.

    Returns:
        (slot_utc_datetime, slot_iso_str, display_vn, display_us)
        Ví dụ:
        (
            datetime(2026, 9, 27, 23, 0, tzinfo=timezone.utc),
            "2026-09-27T23:00:00Z",
            "06:00 Sáng Thứ Hai, 28/09/2026 (Giờ VN)",
            "6:00 PM EST (GIỜ VÀNG LỚN NHẤT MỸ — Tan sở & Buổi tối)"
        )
    """
    # 0. Đồng bộ các video đã qua giờ hẹn trước khi tính toán
    sync_scheduled_jobs_status()

    now_utc = datetime.now(timezone.utc)
    min_allowed_time = now_utc + timedelta(minutes=min_lead_minutes)

    # 1. Tìm tất cả alias của kênh này để quét occupied slots
    channel_family = resolve_channel_family(channel_id)

    occupied_slots_utc = []
    with get_db() as db:
        scheduled_jobs = db.query(VideoJob).filter(
            VideoJob.target_channel_id.in_(channel_family),
            VideoJob.status.in_(["SCHEDULED", "PUBLISHED"]),
            VideoJob.scheduled_time.isnot(None)
        ).all()

        for j in scheduled_jobs:
            if j.scheduled_time:
                st = j.scheduled_time
                if st.tzinfo is None:
                    st = st.replace(tzinfo=timezone.utc)
                # Chỉ tính các slot đã đăng hoặc lên lịch trong vòng 48h qua hoặc trong tương lai
                if st >= (now_utc - timedelta(hours=48)):
                    occupied_slots_utc.append(st)

    logger.debug(f"[SmartScheduler] Kênh {channel_id} (Family: {channel_family}) hiện có {len(occupied_slots_utc)} khe giờ đã đặt lịch.")

    # 2. Duyệt tìm khe giờ trống bắt đầu từ ngày hôm nay đến 45 ngày tiếp theo
    # Áp dụng triệt để: TỐI ĐA 2 VIDEO / NGÀY / KÊNH
    # Ưu tiên 2 đỉnh cao nhất: Buổi tối Mỹ (23:00 UTC = 06:00 VN) & Buổi sáng Mỹ (13:00 UTC = 20:00 VN)
    # Tự động chèn khe dự phòng nếu trùng (17:30 UTC = 00:30 VN hoặc 03:00 UTC = 10:00 VN)
    start_date = now_utc.date()
    for day_offset in range(45):
        target_date = start_date + timedelta(days=day_offset)

        # Tạo danh sách các khe giờ vàng trong ngày
        candidate_slots = []
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
            if candidate_dt > min_allowed_time:
                candidate_slots.append((candidate_dt, slot))

        # Ưu tiên theo thứ hạng vàng:
        # Rank 1: 20:00 Tối VN (Khuyên dùng nhất — Cố định hàng ngày)
        # Rank 2: 06:00 Sáng VN (Đỉnh giải trí tối Mỹ 18:00-19:00 EST)
        # Rank 3: 11:30 Trưa VN (Đón đầu phân phối & video tiền nhiệm 11:18)
        candidate_slots.sort(key=lambda item: (
            item[1].get("rank", 99),
            item[0]
        ))

        for candidate_dt, slot in candidate_slots:
            # 1. Kiểm tra va chạm thời gian: Cách mọi video đã có ít nhất 3.5 giờ (12,600s)
            is_too_close = False
            for occ in occupied_slots_utc:
                diff_seconds = abs((candidate_dt - occ).total_seconds())
                if diff_seconds < MIN_SPACING_SECONDS:
                    is_too_close = True
                    break
            if is_too_close:
                continue

            # 2. KIỂM TRA QUY TẮC "TỐI ĐA 2 VIDEO / 1 NGÀY":
            # Đếm số lượng video đã đặt trên cùng chu kỳ ngày của thị trường mục tiêu US EST
            target_us_date = candidate_dt.astimezone(US_TZ).date()
            us_day_count = sum(1 for occ in occupied_slots_utc if occ.astimezone(US_TZ).date() == target_us_date)

            if us_day_count >= MAX_VIDEOS_PER_DAY:
                logger.debug(
                    f"[SmartScheduler] Ngày {target_us_date} (US) đã có đủ {us_day_count} video "
                    f"(đạt giới hạn tối đa {MAX_VIDEOS_PER_DAY} video/ngày) -> Chuyển sang ngày tiếp theo."
                )
                continue

            # 3. TÌM THẤY KHE GIỜ VÀNG TRỐNG TỐI ƯU THỎA MÃN MỌI ĐIỀU KIỆN!
            slot_iso = candidate_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Chuyển đổi sang giờ Việt Nam để hiển thị
            candidate_vn = candidate_dt.astimezone(VN_TZ)
            weekday_name = WEEKDAY_VN_MAP.get(candidate_vn.weekday(), "Ngày")
            display_vn = f"{slot['label_vn']} {weekday_name}, {candidate_vn.strftime('%d/%m/%Y')} (Giờ VN)"
            display_us = slot["label_us"]

            logger.info(
                f"🎯 [SmartScheduler] Đã xếp lịch tối ưu (Tối đa 2 video/ngày) cho kênh {channel_id}: "
                f"{display_vn} ({slot_iso}) — {display_us}"
            )
            return candidate_dt, slot_iso, display_vn, display_us

    # Fallback dự phòng nếu lịch quá đầy (sau 45 ngày):
    fallback_dt = min_allowed_time + timedelta(hours=4)
    fallback_iso = fallback_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    fallback_vn = fallback_dt.astimezone(VN_TZ).strftime("%H:%M %d/%m/%Y (Giờ VN)")
    return fallback_dt, fallback_iso, fallback_vn, "Khung giờ dự phòng"



def get_channel_upcoming_schedule(channel_id: str = "channel_maya", limit: int = 10) -> List[Dict[str, Any]]:
    """Lấy danh sách các video đã lên lịch trong tương lai của kênh để theo dõi."""
    sync_scheduled_jobs_status()
    now_utc = datetime.now(timezone.utc)
    channel_family = resolve_channel_family(channel_id)
    results = []

    with get_db() as db:
        jobs = db.query(VideoJob).filter(
            VideoJob.target_channel_id.in_(channel_family),
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

            # Tìm label US tương ứng
            slot_label_us = "Giờ hẹn YouTube"
            for s in GOLDEN_SLOTS_UTC:
                if s["hour"] == st.hour and abs(s["minute"] - st.minute) <= 15:
                    slot_label_us = s["label_us"]
                    break

            results.append({
                "job_id": j.id,
                "title": j.title or j.topic,
                "scheduled_iso": st.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "scheduled_vn": f"{st_vn.strftime('%H:%M')} {weekday_name}, {st_vn.strftime('%d/%m/%Y')}",
                "scheduled_us": slot_label_us,
                "youtube_url": f"https://youtu.be/{j.youtube_video_id}" if j.youtube_video_id else None
            })

    return results


def get_golden_slots_overview() -> List[Dict[str, Any]]:
    """Trả về bảng tổng hợp 4 khung giờ vàng Shorts để hiển thị trên Dashboard & Telegram."""
    return [
        {
            "tag": s["tag"],
            "hour_utc": f"{s['hour']:02d}:{s['minute']:02d} UTC",
            "time_vn": s["label_vn"],
            "time_us": s["label_us"],
            "description": s["description"]
        }
        for s in GOLDEN_SLOTS_UTC
    ]

