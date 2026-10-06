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

# Múi giờ Việt Nam (UTC+7) và Múi giờ bờ Đông Mỹ (New York - tự động DST: EDT UTC-4 hoặc EST UTC-5)
from zoneinfo import ZoneInfo
VN_TZ = timezone(timedelta(hours=7))
US_EASTERN = ZoneInfo("America/New_York")
US_TZ = US_EASTERN  # Alias để đảm bảo tương thích ngược

# Giới hạn tối đa video trên mỗi kênh trong 1 ngày
MAX_VIDEOS_PER_DAY = 2

# Khoảng cách tối thiểu an toàn giữa 2 video cùng kênh (6 giờ = 21,600 giây) để tránh tự bóp tương tác
MIN_SPACING_SECONDS = 21600

# ──────────────────────────────────────────────────────────────────────────────
# CẤU HÌNH 2 KHUNG GIỜ VÀNG QUỐC TẾ (US & GLOBAL PEAKS) TỐI ƯU 2 VIDEO / NGÀY
# ──────────────────────────────────────────────────────────────────────────────
# Chuẩn hóa đúng 2 khung giờ đỉnh cao nhất trên YouTube Shorts:
#   - Khung 1 (Sáng Mỹ / Tối VN): 13:00 UTC (20:00 tối VN = 09:00 AM EST)
#     Đón đầu giờ thức dậy, đi làm/đi học tại Mỹ & giờ vàng giải trí tối tại Châu Á.
#   - Khung 2 (Tối Mỹ / Sáng VN): 21:00 UTC (04:00 sáng VN = 17:00 PM EST)
#     Đón đầu đỉnh xem tối lớn nhất tại Mỹ (18:30-21:30 EST) — giờ vàng số 1 toàn cầu.
# Hai khung giờ này giãn cách đúng 8 tiếng ban ngày và 16 tiếng qua đêm:
# Triệt tiêu 100% hiện tượng tự nuốt view (cannibalization), giúp cả 2 video đều đạt reach tối đa!
GOLDEN_SLOTS_UTC = [
    {
        "hour": 13,
        "minute": 0,
        "label_vn": "20:00 Tối",
        "label_us": "09:00 AM EST (ĐÓN ĐẦU ĐỈNH SÁNG MỸ & CHIỀU CHÂU ÂU)",
        "description": "Đón đầu giờ đi làm/đi học buổi sáng tại Mỹ (09:00 EST), đầu giờ chiều Châu Âu (14:00-15:00) và giờ vàng buổi tối tại Việt Nam/Châu Á.",
        "tag": "US_MORNING_EU_AFTERNOON",
        "rank": 1
    },
    {
        "hour": 21,
        "minute": 0,
        "label_vn": "04:00 Sáng",
        "label_us": "17:00 PM EST (ĐÓN ĐẦU ĐỈNH VÀNG SỐ 1 MỸ — Tan sở & Buổi tối)",
        "description": "Đăng sớm trước đỉnh tối Mỹ (18:30-21:30 EST) 1.5 tiếng để thuật toán thử nghiệm tệp và sẵn sàng bùng nổ đúng giờ vàng số 1 thế giới.",
        "tag": "US_EVENING_PRIME",
        "rank": 2
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
    Bao gồm: ID nội bộ, youtube_channel_id (UC...), handle (@...), và tên kênh.
    """
    if not channel_id:
        return ["default"]

    canonical_map = {
        "channel_psychology": "channel_maya",
        "channel_maya": "channel_maya",
        "uc1_lvpilxq-j4sfrqozfrkw": "channel_maya",
        "ucsrjiil67qxidtxt70z-iwa": "ch_the_mysteries_of_the_world",
        "ucsjll67qxidtxt70z-iwa": "ch_the_mysteries_of_the_world",
        "ch_the_mysteries_of_the_world": "ch_the_mysteries_of_the_world",
        "ch_paws_hearts_rescue": "ch_paws_hearts_rescue",
        "ucv-2dyuqkx1u0cjqdmzdhta": "ch_paws_hearts_rescue",
        "ch_wild_odyssey": "ch_wild_odyssey",
        "uclsjhbvjjxi-j16vzxxs68a": "ch_wild_odyssey",
        "@alternatehistorywhatif-e2x": "@alternatehistorywhatif-e2x",
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
            yt_id = ch.get("youtube_channel_id", "")
            handle = ch.get("handle", "")
            name = ch.get("name", "")
            identifiers = [curr_id, yt_id, handle, name]

            if any(ident and ident.lower() in [f.lower() for f in family] for ident in identifiers):
                for ident in identifiers:
                    if ident:
                        family.add(ident)
                        family.add(ident.lower())
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

    # Tự động quét và xả hàng đợi các video chờ nạp lại Quota
    try:
        updated_count += process_waiting_quota_jobs()
    except Exception as qe:
        logger.debug(f"Lỗi khi xử lý hàng đợi quota: {qe}")

    # ──────────────────────────────────────────────────────────────────
    # COLAB WATCHDOG TIMER: Phát hiện và khôi phục job bị treo trên Colab
    # ──────────────────────────────────────────────────────────────────
    try:
        updated_count += watchdog_colab_stuck_jobs()
    except Exception as we:
        logger.debug(f"Lỗi khi chạy Colab Watchdog: {we}")

    return updated_count


def process_waiting_quota_jobs() -> int:
    """
    Tự động xử lý hàng đợi VideoJob có status == 'WAITING_QUOTA_RESET'.
    Nếu hệ thống đã qua mốc 14:00 (Google reset Quota) và QuotaManager.can_upload() là True:
    Tự động thử upload video lên kênh tương ứng.

    Áp dụng pattern 3 bước:
    1. Đọc danh sách job cần upload và đóng DB session ngay lập tức.
    2. Thực hiện I/O nặng (upload YouTube 30-120s) NGOÀI session DB.
    3. Mở session mới ngắn hạn để cập nhật trạng thái kết quả.
    """
    from app.services.quota_manager import QuotaManager
    from app.agents.publisher_agent import PublisherAgent
    from app.services.channel_manager import get_channel
    from pathlib import Path
    
    processed_count = 0
    try:
        # Bước 1: Đọc danh sách jobs cần xử lý (session đóng ngay sau đó)
        with get_db() as db:
            waiting_jobs_data = [
                {
                    "id": j.id,
                    "target_channel_id": j.target_channel_id,
                    "video_path": j.video_path,
                    "title": j.title,
                    "description": j.description,
                    "tags": j.tags,
                    "topic": j.topic,
                    "scenes_json": j.scenes_json,
                }
                for j in db.query(VideoJob).filter(
                    VideoJob.status == "WAITING_QUOTA_RESET"
                ).order_by(VideoJob.id.asc()).all()
            ]

        if not waiting_jobs_data:
            return 0

        # Bước 2: Thực hiện upload NGOÀI session (I/O nặng không giữ kết nối DB)
        for job_data in waiting_jobs_data:
            job_id = job_data["id"]
            try:
                ch_cfg = get_channel(job_data["target_channel_id"]) or {}
                pub_agent = PublisherAgent(channel_config=ch_cfg)
                
                # Kiểm tra xem project của kênh này đã có quota chưa
                if not QuotaManager.can_upload(pub_agent.project_id):
                    logger.debug(f"[SmartScheduler] Project '{pub_agent.project_id}' vẫn chưa có quota cho Job #{job_id}, tiếp tục đợi.")
                    continue

                if not job_data["video_path"] or not Path(job_data["video_path"]).exists():
                    logger.warning(f"[SmartScheduler] File video #{job_id} không tìm thấy tại {job_data['video_path']}")
                    continue

                logger.info(f"🚀 [SmartScheduler] Quota đã được nạp lại! Đang tự động xuất bản Video #{job_id} ('{job_data['title']}')...")
                res = pub_agent.upload_video(
                    video_path=job_data["video_path"],
                    title=job_data["title"] or f"Shorts #{job_id}",
                    description=job_data["description"] or "",
                    tags=job_data["tags"].split(",") if job_data.get("tags") else None,
                    privacy_status="public",
                    default_language=ch_cfg.get("language", "en"),
                    topic=job_data.get("topic") or "",
                    script=job_data.get("scenes_json") or ""
                )

                # Bước 3: Mở session MỚI để ghi kết quả (session sống ngắn)
                if res.get("status") == "success":
                    with get_db() as db:
                        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                        if job:
                            job.status = "PUBLISHED"
                            job.youtube_video_id = res.get("video_id")
                            job.error_message = None
                    processed_count += 1
                    logger.info(f"✅ [SmartScheduler] Tự động xuất bản thành công Video #{job_id}: {res.get('shorts_url')}")

            except Exception as job_ex:
                logger.warning(f"[SmartScheduler] Lỗi khi xử lý job #{job_id}: {job_ex}")
                continue

    except Exception as ex:
        logger.warning(f"Lỗi khi xử lý waiting_quota_jobs: {ex}")

    return processed_count


def watchdog_colab_stuck_jobs(timeout_minutes: Optional[int] = None) -> int:
    """
    Quét và tự động xử lý các VideoJob bị kẹt ở trạng thái PENDING_COLAB.
    Vì Colab Pro+ hỗ trợ chạy ngầm 24h, thời gian timeout mặc định được thiết lập an toàn
    (mặc định 60 phút, hoặc lấy từ settings.COLAB_TIMEOUT_MINUTES).

    Quy trình kiểm tra 2 bước:
    1. Kiểm tra xem trên Google Drive đã có file video_{job_id}.mp4 hoàn thành chưa.
       Nếu đã có -> tự động import về và kiểm định VideoQA, chuyển RENDERED!
    2. Nếu chưa có file và thời gian kẹt > timeout_minutes:
       -> Tự động chuyển về SCRIPT_READY để hàng đợi Local có thể render dự phòng,
          không làm kẹt SmartScheduler và Dashboard.
    """
    from config.settings import settings
    if timeout_minutes is None:
        timeout_minutes = getattr(settings, "COLAB_TIMEOUT_MINUTES", 60)

    recovered_count = 0
    now_utc = datetime.utcnow()
    cutoff_time = now_utc - timedelta(minutes=timeout_minutes)

    try:
        from app.services.colab_queue_service import check_and_import_completed_video
    except Exception:
        check_and_import_completed_video = None

    try:
        with get_db() as db:
            stuck_jobs = db.query(VideoJob).filter(
                VideoJob.status == "PENDING_COLAB",
                (VideoJob.updated_at < cutoff_time) | (VideoJob.updated_at.is_(None))
            ).all()

            if not stuck_jobs:
                return 0

            for job in stuck_jobs:
                jid = job.id
                # Bước 1: Thử import nếu thực ra Colab đã làm xong trên Drive
                if check_and_import_completed_video:
                    try:
                        ok_imp, _, _ = check_and_import_completed_video(jid)
                        if ok_imp:
                            logger.info(f"🎉 [ColabWatchdog] Job #{jid} đã xong trên Drive -> Import thành công!")
                            recovered_count += 1
                            continue
                    except Exception as e_imp:
                        logger.debug(f"[ColabWatchdog] Thử import job #{jid} chưa thành công: {e_imp}")

                # Bước 2: Quá timeout mà vẫn chưa có video -> Giải phóng về SCRIPT_READY
                job.status = "SCRIPT_READY"
                job.progress_percent = 20
                job.progress_message = (
                    f"Colab Pro+ không phản hồi sau {timeout_minutes} phút. "
                    f"Tự động chuyển về hàng đợi để sẵn sàng render dự phòng."
                )
                recovered_count += 1
                logger.warning(
                    f"⏰ [ColabWatchdog] Job #{jid} kẹt ở PENDING_COLAB > {timeout_minutes}m. "
                    f"Đã tự động phục hồi về SCRIPT_READY."
                )

    except Exception as e:
        logger.warning(f"Lỗi khi chạy watchdog_colab_stuck_jobs: {e}")

    return recovered_count



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

        # Sắp xếp các khe giờ ứng viên theo trình tự thời gian tự nhiên (từ sớm đến muộn trong ngày).
        # Giúp các video được lên lịch nối tiếp mượt mà theo đúng thứ tự sản xuất,
        # không bị đảo lộn thời gian (video làm sau lại đăng trước) hay dồn cục vào ban đêm.
        candidate_slots.sort(key=lambda item: item[0])

        for candidate_dt, slot in candidate_slots:
            # 1. Kiểm tra va chạm thời gian: Cách mọi video đã có ít nhất 6.0 giờ (21,600s)
            is_too_close = False
            for occ in occupied_slots_utc:
                diff_seconds = abs((candidate_dt - occ).total_seconds())
                if diff_seconds < MIN_SPACING_SECONDS:
                    is_too_close = True
                    break
            if is_too_close:
                continue


            # 2. KIỂM TRA QUY TẮC "TỐI ĐA 2 VIDEO / 1 NGÀY":
            # Đếm số lượng video đã đặt trên cùng chu kỳ ngày của thị trường mục tiêu US EST/EDT
            target_us_date = candidate_dt.astimezone(US_EASTERN).date()
            us_day_count = sum(1 for occ in occupied_slots_utc if occ.astimezone(US_EASTERN).date() == target_us_date)

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
    """Trả về bảng tổng hợp 2 khung giờ vàng Shorts tối ưu nhất (2 video/ngày) để hiển thị trên Dashboard & Telegram."""
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

