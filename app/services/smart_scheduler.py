"""
Smart Golden Slot Scheduler — Thuật toán tính toán và phân bổ khe giờ vàng tự động chuẩn quốc tế.
Đảm bảo:
- Tối ưu 4 khung giờ vàng / ngày theo hành vi người xem YouTube Shorts tại Mỹ & Toàn cầu.
- Giãn cách tối thiểu >= 3.5 - 4 tiếng giữa các video trên cùng một kênh để tránh hiện tượng "nuốt view" (cannibalization).
- Tự động nhận diện Channel Family (alias & brand channels) để tránh trùng lịch chéo.
- Tự động đồng bộ trạng thái: Chuyển SCHEDULED -> PUBLISHED khi đã qua thời điểm công chiếu.
- Tương thích 100% với YouTube Data API v3 (status.publishAt).
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Tuple, Dict, Any, List, Optional, Set
from app.database.models import get_db, VideoJob

logger = logging.getLogger(__name__)

# Múi giờ Việt Nam (UTC+7), Múi giờ bờ Đông Mỹ (New York - EDT/EST) và Múi giờ Brazil (Brasília/São Paulo - BRT UTC-3)
from zoneinfo import ZoneInfo
VN_TZ = timezone(timedelta(hours=7))
US_EASTERN = ZoneInfo("America/New_York")
US_TZ = US_EASTERN  # Alias để đảm bảo tương thích ngược
BR_TZ = ZoneInfo("America/Sao_Paulo")  # Giờ chuẩn Brazil (BRT = UTC-3)

# Giới hạn tối đa video trên mỗi kênh trong 1 ngày
MAX_VIDEOS_PER_DAY = 2

# Khoảng cách tối thiểu an toàn giữa 2 video cùng kênh (6 giờ = 21,600 giây) để tránh tự bóp tương tác
MIN_SPACING_SECONDS = 21600

# ──────────────────────────────────────────────────────────────────────────────
# CẤU HÌNH 2 KHUNG GIỜ VÀNG QUỐC TẾ (US & GLOBAL PEAKS) TỐI ƯU 2 VIDEO / NGÀY
# ĐÃ TỐI ƯU HÓA: ĐĂNG SỚM HƠN ĐỈNH VÀNG 60 - 90 PHÚT ĐỂ THUẬT TOÁN KỊP PHÂN PHỐI
# ──────────────────────────────────────────────────────────────────────────────
GOLDEN_SLOTS_UTC = [
    {
        "hour": 15,
        "minute": 0,
        "label_vn": "22:00 Đêm",
        "label_us": "11:00 AM EDT / 08:00 AM PDT (ĐÓN ĐẦU ĐỈNH TRƯA MỸ)",
        "description": "Đăng sớm trước giờ nghỉ trưa Mỹ 60 phút để YouTube xử lý độ nét, phụ đề và kiểm thử tệp; bùng nổ đúng giờ ăn trưa (12:00-13:30).",
        "tag": "US_LUNCH_LEAD",
        "rank": 1
    },
    {
        "hour": 21,
        "minute": 15,
        "label_vn": "04:15 Sáng",
        "label_us": "17:15 PM EDT / 14:15 PM PDT (ĐÓN ĐẦU ĐỈNH TỐI VÀNG MỸ #1)",
        "description": "Đăng sớm trước đỉnh tối Mỹ (19:00-22:00 EDT) 90 phút để video index từ khóa và đón trọn đỉnh lướt Shorts lớn nhất toàn cầu khi người Mỹ tan sở về nhà.",
        "tag": "US_EVENING_LEAD",
        "rank": 2
    },
]

# ──────────────────────────────────────────────────────────────────────────────
# CẤU HÌNH 2 KHUNG GIỜ VÀNG THỊ TRƯỜNG BRAZIL (BRT = UTC-3) CHO KÊNH pt-BR
# ──────────────────────────────────────────────────────────────────────────────
#   - Khung 1 (Đón đầu Đỉnh Trưa Brazil - Horário de Almoço 11:30 - 13:30 BRT):
#     Base 13:30 UTC (10:30 AM BRT = 20:30 Tối VN) -> Đăng sớm 60p trước giờ ăn trưa Brazil.
#   - Khung 2 (Đón đầu Đỉnh Tối Vàng Brazil - Horário Nobre 19:00 - 22:30 BRT):
#     Base 21:00 UTC (18:00 PM BRT = 04:00 Sáng VN) -> Đăng sớm 60-90p trước đỉnh tối Brazil.
BRAZIL_GOLDEN_SLOTS_UTC = [
    {
        "hour": 13,
        "minute": 30,
        "label_vn": "20:30 Tối",
        "label_us": "10:30 AM BRT (ĐÓN ĐẦU ĐỈNH TRƯA BRAZIL - HORÁRIO DE ALMOÇO)",
        "description": "Đăng sớm 60 phút trước giờ nghỉ trưa tại São Paulo/Brasília (11:30-13:30 BRT) để thuật toán YouTube Brazil phân phối đúng tệp pt-BR.",
        "tag": "BR_LUNCH_LEAD",
        "rank": 1
    },
    {
        "hour": 21,
        "minute": 0,
        "label_vn": "04:00 Sáng",
        "label_us": "18:00 PM BRT (ĐÓN ĐẦU ĐỈNH TỐI VÀNG BRAZIL - HORÁRIO NOBRE #1)",
        "description": "Đăng sớm 60-90 phút trước giờ vàng giải trí buổi tối lớn nhất Brazil (19:00-22:30 BRT), lúc 100% người dùng VN đang ngủ say.",
        "tag": "BR_EVENING_LEAD",
        "rank": 2
    },
]

# Độ lệch thông minh theo từng kênh (phút) để chống trùng khớp giây xuất bản (Bot Footprint)
# trên các kênh dùng chung Google Account:
#   [US Channels]
#   - Alternate History: -30p -> 14:30 UTC (21:30 VN) & 20:45 UTC (03:45 VN)
#   - Psychology:        -15p -> 14:45 UTC (21:45 VN) & 21:00 UTC (04:00 VN)
#   - Mysteries:          +0p -> 15:00 UTC (22:00 VN) & 21:15 UTC (04:15 VN)
#   - Paws & Hearts:     +15p -> 15:15 UTC (22:15 VN) & 21:30 UTC (04:30 VN)
#   - Wild Odyssey:      +30p -> 15:30 UTC (22:30 VN) & 21:45 UTC (04:45 VN)
#   [Brazil pt-BR Channels - cùng tài khoản acccount4, giãn cách 45 phút]
#   - Odisseia Selvagem:  +0p -> 13:30 UTC (10:30 BRT / 20:30 VN) & 21:00 UTC (18:00 BRT / 04:00 VN)
#   - Mistérios do Mundo:+45p -> 14:15 UTC (11:15 BRT / 21:15 VN) & 21:45 UTC (18:45 BRT / 04:45 VN)
CHANNEL_STAGGER_MINUTES: Dict[str, int] = {
    "@alternatehistorywhatif-e2x": -30,
    "ch_psychology_of_the_world": -15,
    "channel_maya": -15,
    "channel_psychology": -15,
    "@vv1n-oe9gs": -15,
    "ch_the_mysteries_of_the_world": 0,
    "@themysteriesoftheworld-f3w": 0,
    "ucsrjiil67qxidtxt70z-iwa": 0,
    "ch_paws_hearts_rescue": 15,
    "@pawsandheartsrescue": 15,
    "ch_wild_odyssey": 30,
    "@wildodysseyhq": 30,
    "ch_odisseia_selvagem": 0,
    "@odisseiaselvagemoficial": 0,
    "ucpbqs4h5qugpu3hcqjg-p9w": 0,
    "ch_misterios_do_mundo": 45,
    "@misteriosdomundo-tv": 45,
    "ucvfonq9jcpvvze8e1cgnng": 45,
    "ucvfonq9jcpvvze8e1cgng": 45,
}


def is_brazil_channel(channel_id: str) -> bool:
    """Kiểm tra xem kênh có thuộc thị trường Brazil (pt-BR) hay không."""
    if not channel_id:
        return False
    br_ids = {
        "ch_odisseia_selvagem",
        "@odisseiaselvagemoficial",
        "ucpbqs4h5qugpu3hcqjg-p9w",
        "ch_misterios_do_mundo",
        "@misteriosdomundo-tv",
        "ucvfonq9jcpvvze8e1cgnng",
        "ucvfonq9jcpvvze8e1cgng",
    }
    cid_low = channel_id.strip().lower()
    if cid_low in br_ids:
        return True
    for fam in resolve_channel_family(channel_id):
        if fam.lower() in br_ids:
            return True
    try:
        from app.services.channel_manager import get_channel
        cfg = get_channel(channel_id) or {}
        if str(cfg.get("language") or "").lower().startswith("pt"):
            return True
    except Exception:
        pass
    return False


def get_channel_stagger_minutes(channel_id: str) -> int:
    """Trả về số phút dịch chuyển lệch pha của kênh để tránh 2 kênh cùng Google Account đăng trùng giây."""
    if not channel_id:
        return 0
    cid = channel_id.strip().lower()
    if cid in CHANNEL_STAGGER_MINUTES:
        return CHANNEL_STAGGER_MINUTES[cid]
    for fam_id in resolve_channel_family(channel_id):
        if fam_id.lower() in CHANNEL_STAGGER_MINUTES:
            return CHANNEL_STAGGER_MINUTES[fam_id.lower()]
    return 0


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
    Đảm bảo phần tử đầu tiên trong danh sách trả về luôn là Canonical Channel ID chuẩn.
    """
    if not channel_id:
        return ["ch_psychology_of_the_world"]

    from app.database.models import CANONICAL_CHANNEL_MAP, normalize_channel_id

    cid_clean = channel_id.strip()
    cid_lower = cid_clean.lower()
    canon_id = normalize_channel_id(cid_clean)

    family = {cid_clean, cid_lower, canon_id, canon_id.lower()}
    for alias_k, mapped_canon in CANONICAL_CHANNEL_MAP.items():
        if mapped_canon == canon_id:
            family.add(alias_k)

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

    ordered = [canon_id] + [f for f in family if f != canon_id]
    return ordered




def sync_scheduled_jobs_status() -> int:
    """
    Tự động đồng bộ trạng thái: Quét các VideoJob có status == 'SCHEDULED'.
    Nếu scheduled_time <= now_utc (thời điểm công chiếu đã qua trên máy chủ YouTube),
    tự động cập nhật status = 'PUBLISHED' để database luôn phản ánh đúng thực tế!
    """
    now_utc = datetime.now(timezone.utc)
    updated_count = 0
    just_published_jobs = []
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
                    just_published_jobs.append({
                        "id": job.id,
                        "youtube_video_id": job.youtube_video_id,
                        "target_channel_id": job.target_channel_id,
                        "scenes_json": job.scenes_json,
                        "title": job.title or "",
                    })
                    logger.info(
                        f"🔄 [SmartScheduler] Video #{job.id} ('{job.title[:30] if job.title else job.id}...') "
                        f"đã qua giờ hẹn ({st.strftime('%Y-%m-%d %H:%M UTC')}) -> Chuyển status = PUBLISHED."
                    )
    except Exception as e:
        logger.warning(f"Lỗi khi đồng bộ trạng thái SCHEDULED jobs: {e}")

    # Tự động kiểm tra & khóa chuẩn Category ID ('22'/'15', chặn tuyệt đối '27'), US/BR Geo-Lock và ghim bình luận mồi
    if just_published_jobs:
        try:
            import json
            from app.agents.publisher_agent import PublisherAgent
            from app.services.channel_manager import get_channel
            for p_info in just_published_jobs:
                v_id = p_info.get("youtube_video_id")
                if not v_id:
                    continue
                ch_id = p_info.get("target_channel_id")
                ch_cfg = get_channel(ch_id) or {}
                if not ch_cfg:
                    continue
                p_agent = PublisherAgent(channel_config=ch_cfg)
                if not p_agent.service:
                    continue

                # Tự động kiểm tra & sửa chữa (Self-Healing) nếu categoryId bị lệch hoặc thiếu US/BR Geo-Lock
                p_agent.verify_and_heal_video_metadata(v_id)

                p_comment = None
                if p_info.get("scenes_json"):
                    try:
                        s_data = json.loads(p_info["scenes_json"]) if isinstance(p_info["scenes_json"], str) else p_info["scenes_json"]
                        if isinstance(s_data, dict):
                            p_comment = s_data.get("pinned_comment")
                    except Exception:
                        pass

                p_agent.ensure_pinned_comment(v_id, p_comment)
        except Exception as bait_err:
            logger.warning(f"Lỗi khi tự động post bình luận mồi cho video vừa công chiếu: {bait_err}")

    # Tự động quét và xả hàng đợi các video chờ nạp lại Quota
    try:
        updated_count += process_waiting_quota_jobs()
    except Exception as qe:
        logger.debug(f"Lỗi khi xử lý hàng đợi quota: {qe}")

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

                # Trích xuất pinned_comment từ scenes_json
                pinned_comment = None
                if job_data.get("scenes_json"):
                    try:
                        s_data = json.loads(job_data["scenes_json"]) if isinstance(job_data["scenes_json"], str) else job_data["scenes_json"]
                        if isinstance(s_data, dict):
                            pinned_comment = s_data.get("pinned_comment")
                    except Exception:
                        pass

                raw_lang = str(ch_cfg.get("language") or "en-US")
                def_lang = "pt-BR" if raw_lang.lower().startswith("pt") else "en-US"
                def_audio_lang = "pt-BR" if raw_lang.lower().startswith("pt") else "en-US"
                logger.info(f"🚀 [SmartScheduler] Quota đã được nạp lại! Đang tự động xuất bản Video #{job_id} ('{job_data['title']}')...")
                res = pub_agent.upload_video(
                    video_path=job_data["video_path"],
                    title=job_data["title"] or f"Shorts #{job_id}",
                    description=job_data["description"] or "",
                    tags=job_data["tags"].split(",") if job_data.get("tags") else None,
                    privacy_status="public",
                    default_language=def_lang,
                    default_audio_language=def_audio_lang,
                    pinned_comment=pinned_comment,
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



def get_next_available_golden_slot(
    channel_id: str = "ch_psychology_of_the_world",
    min_lead_minutes: int = 20
) -> Tuple[datetime, str, str, str]:
    """
    Tính toán khe giờ vàng tiếp theo còn trống cho kênh.
    Tự động phân luồng múi giờ & khung giờ vàng theo thị trường mục tiêu:
    - Thị trường Mỹ (US Channels - EDT/EST): GOLDEN_SLOTS_UTC
    - Thị trường Brazil (pt-BR Channels - BRT UTC-3): BRAZIL_GOLDEN_SLOTS_UTC
    """
    # 0. Đồng bộ các video đã qua giờ hẹn trước khi tính toán
    sync_scheduled_jobs_status()

    now_utc = datetime.now(timezone.utc)
    min_allowed_time = now_utc + timedelta(minutes=min_lead_minutes)

    # 1. Tìm tất cả alias của kênh này để quét occupied slots
    channel_family = resolve_channel_family(channel_id)
    is_br = is_brazil_channel(channel_id)
    active_slots_pool = BRAZIL_GOLDEN_SLOTS_UTC if is_br else GOLDEN_SLOTS_UTC
    target_tz = BR_TZ if is_br else US_EASTERN

    occupied_slots_utc = []
    cutoff_48h = now_utc - timedelta(hours=48)
    with get_db() as db:
        scheduled_jobs = db.query(VideoJob).filter(
            VideoJob.target_channel_id.in_(channel_family),
            VideoJob.status.in_(["SCHEDULED", "PUBLISHED"]),
        ).all()

        for j in scheduled_jobs:
            st = j.scheduled_time
            if not st and j.status == "PUBLISHED" and j.youtube_video_id:
                st = j.updated_at or j.created_at
            if st:
                if st.tzinfo is None:
                    st = st.replace(tzinfo=timezone.utc)
                # Chỉ tính các slot đã đăng hoặc lên lịch trong vòng 48h qua hoặc trong tương lai
                if st >= cutoff_48h:
                    occupied_slots_utc.append(st)

        # Bổ sung cả các video đã xuất bản thực tế từ bảng PublishedVideo (bao gồm upload trực tiếp / youtube_sync)
        try:
            from app.database.models import PublishedVideo
            recent_pvs = db.query(PublishedVideo).filter(
                PublishedVideo.channel_id.in_(channel_family)
            ).order_by(PublishedVideo.id.desc()).limit(30).all()
            for pv in recent_pvs:
                pv_dt = pv.published_at
                if isinstance(pv_dt, str) and pv_dt.strip():
                    try:
                        pv_dt = datetime.fromisoformat(pv_dt.replace("Z", "+00:00"))
                    except Exception:
                        pv_dt = None
                if isinstance(pv_dt, datetime):
                    if pv_dt.tzinfo is None:
                        pv_dt = pv_dt.replace(tzinfo=timezone.utc)
                    if pv_dt >= cutoff_48h and not any(abs((pv_dt - ex).total_seconds()) < 120 for ex in occupied_slots_utc):
                        occupied_slots_utc.append(pv_dt)
        except Exception as e_pv:
            logger.debug(f"[SmartScheduler] Không thể quét PublishedVideo cho {channel_id}: {e_pv}")

    logger.debug(f"[SmartScheduler] Kênh {channel_id} (Family: {channel_family}, Brazil={is_br}) hiện có {len(occupied_slots_utc)} khe giờ đã đặt lịch/xuất bản.")

    # 2. Duyệt tìm khe giờ trống bắt đầu từ ngày hôm nay đến 45 ngày tiếp theo
    # Áp dụng triệt để: TỐI ĐA 2 VIDEO / NGÀY / KÊNH
    start_date = now_utc.date()
    stagger_minutes = get_channel_stagger_minutes(channel_id)
    for day_offset in range(45):
        target_date = start_date + timedelta(days=day_offset)

        # Tạo danh sách các khe giờ vàng trong ngày (đã cộng độ lệch pha theo từng kênh)
        candidate_slots = []
        for slot in active_slots_pool:
            base_dt = datetime(
                year=target_date.year,
                month=target_date.month,
                day=target_date.day,
                hour=slot["hour"],
                minute=slot["minute"],
                second=0,
                tzinfo=timezone.utc
            )
            candidate_dt = base_dt + timedelta(minutes=stagger_minutes)
            if candidate_dt > min_allowed_time:
                candidate_slots.append((candidate_dt, slot))

        # Sắp xếp các khe giờ ứng viên theo trình tự thời gian tự nhiên (từ sớm đến muộn trong ngày).
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
            # Đếm số lượng video đã đặt trên cùng chu kỳ ngày của thị trường mục tiêu (US EDT/EST hoặc Brazil BRT)
            target_market_date = candidate_dt.astimezone(target_tz).date()
            market_day_count = sum(1 for occ in occupied_slots_utc if occ.astimezone(target_tz).date() == target_market_date)

            if market_day_count >= MAX_VIDEOS_PER_DAY:
                logger.debug(
                    f"[SmartScheduler] Ngày {target_market_date} ({'BR' if is_br else 'US'}) đã có đủ {market_day_count} video "
                    f"(đạt giới hạn tối đa {MAX_VIDEOS_PER_DAY} video/ngày) -> Chuyển sang ngày tiếp theo."
                )
                continue

            # 3. TÌM THẤY KHE GIỜ VÀNG TRỐNG TỐI ƯU THỎA MÃN MỌI ĐIỀU KIỆN!
            slot_iso = candidate_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Chuyển đổi sang giờ Việt Nam & giờ Mỹ/Brazil thực tế để hiển thị
            candidate_vn = candidate_dt.astimezone(VN_TZ)
            candidate_market = candidate_dt.astimezone(target_tz)
            weekday_name = WEEKDAY_VN_MAP.get(candidate_vn.weekday(), "Ngày")
            vn_period = "Đêm" if candidate_vn.hour >= 18 else "Sáng"
            display_vn = f"{candidate_vn.strftime('%H:%M')} {vn_period} {weekday_name}, {candidate_vn.strftime('%d/%m/%Y')} (Giờ VN)"
            tz_label = "BRT" if is_br else candidate_market.strftime('%Z')
            display_us = f"{candidate_market.strftime('%I:%M %p')} {tz_label} — {slot['label_us']}"

            logger.info(
                f"🎯 [SmartScheduler] Đã xếp lịch tối ưu (Tối đa 2 video/ngày, lệch pha {stagger_minutes:+d}p) cho kênh {channel_id}: "
                f"{display_vn} ({slot_iso}) — {display_us}"
            )
            return candidate_dt, slot_iso, display_vn, display_us

    # Fallback dự phòng nếu lịch quá đầy (sau 45 ngày):
    fallback_dt = min_allowed_time + timedelta(hours=4)
    fallback_iso = fallback_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    fallback_vn = fallback_dt.astimezone(VN_TZ).strftime("%H:%M %d/%m/%Y (Giờ VN)")
    return fallback_dt, fallback_iso, fallback_vn, "Khung giờ dự phòng"



def get_channel_upcoming_schedule(channel_id: str = "ch_psychology_of_the_world", limit: int = 10) -> List[Dict[str, Any]]:
    """Lấy danh sách các video đã lên lịch trong tương lai của kênh để theo dõi."""
    sync_scheduled_jobs_status()
    now_utc = datetime.now(timezone.utc)
    channel_family = resolve_channel_family(channel_id)
    is_br = is_brazil_channel(channel_id)
    active_slots_pool = BRAZIL_GOLDEN_SLOTS_UTC if is_br else GOLDEN_SLOTS_UTC
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

            # Tìm label US/BR tương ứng (chấp nhận biên độ lệch pha +-60 phút)
            slot_label_us = "Giờ hẹn YouTube (Brazil BRT)" if is_br else "Giờ hẹn YouTube"
            st_mins = st.hour * 60 + st.minute
            for s in active_slots_pool:
                s_mins = s["hour"] * 60 + s["minute"]
                if abs(st_mins - s_mins) <= 60:
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


def find_channel_golden_slot_avoiding_collisions(
    channel_id: str,
    preferred_utc_hour: int,
    preferred_utc_minute: int = 0,
    golden_weekdays: Optional[Set[int]] = None,
    min_hours_between_videos: float = 6.0,
    target_start_date: Optional[datetime] = None
) -> Tuple[datetime, str, str]:
    """
    Tính khung giờ vàng tiếp theo cho kênh, bắt đầu từ ngày mai (hoặc ngày chỉ định).
    ĐẶC BIỆT TUÂN THỦ:
    - Nếu ngày mai đã có video lên lịch/xuất bản -> tự động đẩy tiếp sang ngày kế tiếp.
    - Giãn cách tối thiểu min_hours_between_videos (mặc định 6 tiếng) chống nuốt view.
    - Tự động nhận diện Channel Family để không bị va chạm chéo.

    Returns:
        (slot_utc_datetime, slot_iso_str, display_vn_str)
    """
    sync_scheduled_jobs_status()
    now_utc = datetime.now(timezone.utc)
    
    if target_start_date is None:
        start_d = (now_utc + timedelta(days=1)).date()
    elif isinstance(target_start_date, datetime):
        start_d = target_start_date.date()
    else:
        start_d = target_start_date

    channel_family = resolve_channel_family(channel_id)
    occupied_slots_utc: List[datetime] = []

    try:
        with get_db() as db:
            jobs = db.query(VideoJob).filter(
                VideoJob.target_channel_id.in_(channel_family),
                VideoJob.status.in_(["SCHEDULED", "PUBLISHED"]),
                VideoJob.scheduled_time.isnot(None)
            ).all()

            for j in jobs:
                if j.scheduled_time:
                    st = j.scheduled_time
                    if st.tzinfo is None:
                        st = st.replace(tzinfo=timezone.utc)
                    occupied_slots_utc.append(st)
    except Exception as e:
        logger.warning(f"Lỗi khi đọc occupied slots cho {channel_id}: {e}")

    # Duyệt tối đa 30 ngày tiếp theo để tìm khe giờ trống
    min_spacing_sec = min_hours_between_videos * 3600

    for delta in range(30):
        target_day = start_d + timedelta(days=delta)
        if golden_weekdays is not None and target_day.weekday() not in golden_weekdays:
            continue

        candidate_dt = datetime(
            year=target_day.year,
            month=target_day.month,
            day=target_day.day,
            hour=preferred_utc_hour,
            minute=preferred_utc_minute,
            second=0,
            tzinfo=timezone.utc
        )

        if candidate_dt <= now_utc + timedelta(minutes=30):
            continue

        # Kiểm tra khoảng cách với mọi video đã có
        collision = False
        for occ in occupied_slots_utc:
            diff_sec = abs((candidate_dt - occ).total_seconds())
            if diff_sec < min_spacing_sec:
                collision = True
                break

        if not collision:
            slot_iso = candidate_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            cand_vn = candidate_dt.astimezone(VN_TZ)
            w_name = WEEKDAY_VN_MAP.get(cand_vn.weekday(), "Ngày")
            disp_vn = f"{cand_vn.strftime('%H:%M')} {w_name}, {cand_vn.strftime('%d/%m/%Y')} (Giờ VN)"
            logger.info(f"✅ [SmartSlot] Kênh {channel_id} đã chọn slot vàng: {disp_vn} ({slot_iso})")
            return candidate_dt, slot_iso, disp_vn

    # Fallback an toàn nếu lịch kín 30 ngày
    fallback_dt = now_utc + timedelta(days=2, hours=preferred_utc_hour)
    fallback_iso = fallback_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    fb_vn = fallback_dt.astimezone(VN_TZ).strftime("%H:%M %d/%m/%Y (Giờ VN)")
    return fallback_dt, fallback_iso, fb_vn

