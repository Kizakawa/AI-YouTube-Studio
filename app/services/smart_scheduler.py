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

# Giới hạn tối đa video Shorts trên mỗi kênh trong 1 ngày (chưa tính 1 video Long-Form 16:9 độc lập)
MAX_VIDEOS_PER_DAY = 2
MAX_LONG_FORM_PER_DAY = 1

# Khoảng cách tối thiểu an toàn giữa 2 video cùng định dạng trên cùng kênh (6 giờ = 21,600 giây)
MIN_SPACING_SECONDS = 21600
# Luật 4 (Anti-Collision Law): Khoảng cách tối thiểu giữa 1 video Long-Form (16:9) và 1 video Short (9:16) cùng kênh (>= 3.0 giờ = 10,800 giây)
LONG_SHORT_ANTI_COLLISION_SECONDS = 10800


def _is_long_form_entry(
    title: str = "",
    video_path: str = "",
    scenes_json: Any = None,
    duration_sec: float = 0.0,
) -> bool:
    """Nhận diện chính xác một bản ghi/job video thuộc định dạng Long-Form 16:9 Documentary hay Shorts 9:16."""
    if duration_sec and float(duration_sec) >= 180.0:
        return True
    vp_low = str(video_path or "").lower()
    if "16x9" in vp_low or "longform" in vp_low:
        return True
    if scenes_json:
        try:
            s_obj = json.loads(scenes_json) if isinstance(scenes_json, str) else scenes_json
            if isinstance(s_obj, dict):
                vf = str(s_obj.get("video_format") or "").strip().lower()
                if vf == "long":
                    return True
                sc_list = s_obj.get("scenes") or []
                if isinstance(sc_list, list) and len(sc_list) >= 14:
                    return True
        except Exception:
            pass
    return False

# ──────────────────────────────────────────────────────────────────────────────
# CẤU HÌNH 2 KHUNG GIỜ VÀNG QUỐC TẾ (US DAYTIME & PRIME EVENING PEAK - ZERO VIETNAM SCROLLING WAVE)
# Thực nghiệm YouTube Analytics API v2 (dimensions="country", tính cả độ trễ sóng đẩy +1.5h đến +3h của YouTube):
# - Loại bỏ hoàn toàn 14:00-15:00 UTC (21:00-22:00 Đêm VN) vì sóng nổ lúc 22:00-23:59 Đêm VN (giờ VN lướt ĐT trước khi ngủ).
# - Loại bỏ hoàn toàn 21:00-21:45 UTC (04:00-04:45 Sáng VN) vì sóng nổ sau đó 1.5-2.5h rơi đúng vào 05:30-07:30 Sáng VN
#   (giờ người Việt vừa thức dậy cầm điện thoại lướt trên giường!).
# - Chốt 2 Cửa Sổ Kim Cương cách xa nhau đúng 8 tiếng 45 phút (và 15 tiếng 15 phút):
#   + Khung #1: Base 17:15 UTC (00:15 Rạng Sáng VN / 12:15 PM EST / 1:15 PM EDT) -> Sóng nổ lúc 01:00-04:15 Rạng Sáng VN (100% VN ngủ say).
#   + Khung #2: Base 02:00 UTC (09:00 Sáng VN / 9:00 PM EST / 10:00 PM EDT & 6:00 PM PST) -> Đỉnh Giờ Vàng Tối 8PM-11PM toàn nước Mỹ,
#     lúc Việt Nam đang vào giờ làm việc/học hành buổi sáng (09:30-11:30 Sáng VN).
# ──────────────────────────────────────────────────────────────────────────────
GOLDEN_SLOTS_UTC = [
    {
        "hour": 17,
        "minute": 15,
        "label_vn": "00:15 Sáng",
        "label_us": "12:15 PM EST / 13:15 PM EDT (KHUNG GIỜ KIM CƯƠNG #1 — ĐỈNH NGHỈ TRƯA & CHIỀU MỸ — SÓNG NỔ 01:30-04:15 SÁNG VN NGỦ SAY)",
        "description": "Đăng lúc 23:15-01:15 đêm VN (16:15-18:15 UTC): Sóng phân phối (+1.5h-3h) nổ vào đúng 01:00-04:15 rạng sáng VN khi 100% người dùng VN ngủ say, đón trọn giờ nghỉ trưa & chiều Mỹ.",
        "tag": "US_LUNCH_AFTERNOON_PEAK",
        "rank": 1
    },
    {
        "hour": 2,
        "minute": 0,
        "label_vn": "09:00 Sáng",
        "label_us": "21:00 PM EST / 18:00 PM PST (KHUNG GIỜ KIM CƯƠNG #2 — ĐỈNH TỐI VÀNG 8PM-11PM MỸ — CÁCH KHUNG #1 ĐÚNG 8H45P)",
        "description": "Đăng lúc 08:00-10:00 sáng VN (01:00-03:00 UTC): Sóng phân phối nổ đúng Đỉnh Tối Vàng 8:00 PM - 11:00 PM toàn nước Mỹ (East & West Coast), lúc người dùng VN đang làm việc/học tập buổi sáng.",
        "tag": "US_PRIME_NIGHT_PEAK",
        "rank": 2
    },
]

# ──────────────────────────────────────────────────────────────────────────────
# CẤU HÌNH 2 KHUNG GIỜ VÀNG THỊ TRƯỜNG BRAZIL (BRT = UTC-3) CHO KÊNH pt-BR
# Cách xa nhau 6 tiếng 55 phút (13:20 UTC & 20:15 UTC), giãn cách 2 kênh cùng Gmail đúng 65 phút.
# ──────────────────────────────────────────────────────────────────────────────
BRAZIL_GOLDEN_SLOTS_UTC = [
    {
        "hour": 20,
        "minute": 15,
        "label_vn": "03:15 Sáng",
        "label_us": "17:15 PM BRT (ĐÓN ĐẦU ĐỈNH TỐI VÀNG BRAZIL - HORÁRIO NOBRE #1)",
        "description": "Đăng sớm 90 phút trước giờ vàng giải trí buổi tối lớn nhất Brazil (19:00-22:30 BRT), lúc 100% người dùng VN đang ngủ say.",
        "tag": "BR_EVENING_LEAD",
        "rank": 1
    },
    {
        "hour": 13,
        "minute": 20,
        "label_vn": "20:20 Tối",
        "label_us": "10:20 AM BRT (ĐÓN ĐẦU ĐỈNH TRƯA BRAZIL - HORÁRIO DE ALMOÇO #2)",
        "description": "Đăng sớm 60-90 phút trước giờ nghỉ trưa tại São Paulo/Brasília (11:30-13:30 BRT) để thuật toán YouTube Brazil phân phối đúng tệp pt-BR.",
        "tag": "BR_LUNCH_LEAD",
        "rank": 2
    },
]

# ──────────────────────────────────────────────────────────────────────────────
# CẤU HÌNH 2 KHUNG GIỜ KIM CƯƠNG RIÊNG CHO KÊNH THE MYSTERIES OF THE WORLD (US EN)
# ──────────────────────────────────────────────────────────────────────────────
MYSTERIES_DIAMOND_SLOTS_UTC = [
    {
        "hour": 17,
        "minute": 15,
        "label_vn": "00:15 Sáng",
        "label_us": "12:15 PM EST / 13:15 PM EDT (KHUNG GIỜ KIM CƯƠNG #1 — ĐỈNH NGHỈ TRƯA & CHIỀU MỸ — 100% VN NGỦ SAY)",
        "description": "Khung giờ Kim Cương #1 (17:45 UTC / 00:45 Sáng VN sau lệch pha +30p): Sóng nổ 02:15-04:15 sáng VN (0% VN traffic), đón đỉnh trưa-chiều Mỹ.",
        "tag": "US_MYSTERIES_DIAMOND_PRIME",
        "rank": 1
    },
    {
        "hour": 2,
        "minute": 0,
        "label_vn": "09:00 Sáng",
        "label_us": "21:00 PM EST / 18:00 PM PST (KHUNG GIỜ KIM CƯƠNG #2 — ĐỈNH TỐI VÀNG 8PM-11PM MỸ — CÁCH KHUNG #1 8H45P)",
        "description": "Khung giờ Kim Cương #2 (02:30 UTC / 09:30 Sáng VN sau lệch pha +30p): Cách khung #1 đúng 8 tiếng 45 phút, đón trọn Đỉnh Tối Vàng 8PM-11PM tại Mỹ.",
        "tag": "US_MYSTERIES_NIGHT_PEAK",
        "rank": 2
    },
]

# Độ lệch thông minh theo từng kênh (phút) đảm bảo:
# 1. Hai kênh chung 1 tài khoản Gmail luôn cách nhau đúng 60 - 65 phút!
# 2. Toàn bộ 7 kênh cách nhau ít nhất 30 phút (không có bất kỳ 2 kênh nào trùng giờ):
#   [US Channels - Base 17:15 UTC & 02:00 UTC, 2 video cùng kênh cách xa 8 tiếng 45 phút]
#   - Alternate History (accout3):      -60p -> 16:15 UTC (23:15 Đêm VN) & 01:00 UTC (08:00 Sáng VN)
#   - Psychology (account_1 - Kênh A):  -30p -> 16:45 UTC (23:45 Đêm VN) & 01:30 UTC (08:30 Sáng VN)
#   - Paws & Hearts (acount_2 - Kênh A): +0p -> 17:15 UTC (00:15 Sáng VN) & 02:00 UTC (09:00 Sáng VN)
#   - Mysteries EN (account_1 - Kênh B):+30p -> 17:45 UTC (00:45 Sáng VN) & 02:30 UTC (09:30 Sáng VN) [Cách Psychology 60p]
#   - Wild Odyssey (acount_2 - Kênh B): +60p -> 18:15 UTC (01:15 Sáng VN) & 03:00 UTC (10:00 Sáng VN) [Cách Paws & Hearts 60p]
#   [Brazil pt-BR Channels - cùng tài khoản acccount4, 2 video cùng kênh cách 6h55p, 2 kênh cách nhau 65 phút]
#   - Odisseia Selvagem (acccount4 A):   +0p -> 13:20 UTC (20:20 Tối VN)  & 20:15 UTC (03:15 Sáng VN)
#   - Mistérios do Mundo (acccount4 B): +65p -> 14:25 UTC (21:25 Tối VN)  & 21:20 UTC (04:20 Sáng VN) [Cách Odisseia 65p]
CHANNEL_STAGGER_MINUTES: Dict[str, int] = {
    "@alternatehistorywhatif-e2x": -60,
    "ch_psychology_of_the_world": -30,
    "channel_maya": -30,
    "channel_psychology": -30,
    "@vv1n-oe9gs": -30,
    "ch_paws_hearts_rescue": 0,
    "@pawsandheartsrescue": 0,
    "ch_the_mysteries_of_the_world": 30,
    "@themysteriesoftheworld-f3w": 30,
    "ucsrjiil67qxidtxt70z-iwa": 30,
    "ch_wild_odyssey": 60,
    "@wildodysseyhq": 60,
    "ch_odisseia_selvagem": 0,
    "@odisseiaselvagemoficial": 0,
    "ucpbqs4h5qugpu3hcqjg-p9w": 0,
    "ch_misterios_do_mundo": 65,
    "@misteriosdomundo-tv": 65,
    "ucvfonq9jcpvvze8e1cgnng": 65,
    "ucvfonq9jcpvvze8e1cgng": 65,
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


def is_mysteries_diamond_channel(channel_id: str) -> bool:
    """Kiểm tra xem kênh có phải là The mysteries of the world (sử dụng Khung Giờ Kim Cương 04:00 Sáng VN / 21:00 UTC) hay không."""
    if not channel_id:
        return False
    myst_ids = {
        "ch_the_mysteries_of_the_world",
        "@themysteriesoftheworld-f3w",
        "ucsrjiil67qxidtxt70z-iwa",
    }
    cid_low = channel_id.strip().lower()
    if cid_low in myst_ids:
        return True
    for fam in resolve_channel_family(channel_id):
        if fam.lower() in myst_ids:
            return True
    return False


def get_active_slots_pool(channel_id: str) -> List[Dict[str, Any]]:
    """Trả về danh sách khung giờ vàng tối ưu tương ứng với từng kênh."""
    if is_brazil_channel(channel_id):
        return BRAZIL_GOLDEN_SLOTS_UTC
    if is_mysteries_diamond_channel(channel_id):
        return MYSTERIES_DIAMOND_SLOTS_UTC
    return GOLDEN_SLOTS_UTC


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




_COMMENT_VERIFIED_VIDEOS: set = set()
_LAST_SCHEDULE_SYNC_TS: float = 0.0
_SCHEDULE_SYNC_LOCK = __import__("threading").Lock()


def sync_scheduled_jobs_status(force: bool = False) -> int:
    """
    Tự động đồng bộ trạng thái: Quét các VideoJob có status == 'SCHEDULED'.
    Nếu scheduled_time <= now_utc (thời điểm công chiếu đã qua trên máy chủ YouTube),
    tự động cập nhật status = 'PUBLISHED' để database luôn phản ánh đúng thực tế!
    Đồng thời đảm bảo các video vừa chuyển từ SCHEDULED -> PUBLISHED (kể cả khi YouTube cần 30-90s
    để chuyển từ private sang public) luôn được kiểm tra và đăng bình luận tác giả (Author Poll Comment).
    """
    import time as _time
    global _LAST_SCHEDULE_SYNC_TS
    now_ts = _time.time()
    if not force and (now_ts - _LAST_SCHEDULE_SYNC_TS) < 180.0:
        return 0
    if not _SCHEDULE_SYNC_LOCK.acquire(blocking=False):
        return 0
    _LAST_SCHEDULE_SYNC_TS = now_ts
    now_utc = datetime.now(timezone.utc)
    updated_count = 0
    just_published_jobs = []
    unverified_published_jobs = []
    try:
        with get_db() as db:
            scheduled_jobs = db.query(VideoJob).filter(
                VideoJob.status == "SCHEDULED",
                VideoJob.scheduled_time.isnot(None)
            ).all()

            just_published_vids = set()
            for job in scheduled_jobs:
                st = job.scheduled_time
                if st.tzinfo is None:
                    st = st.replace(tzinfo=timezone.utc)
                if st <= now_utc:
                    job.status = "PUBLISHED"
                    updated_count += 1
                    if job.youtube_video_id:
                        just_published_vids.add(job.youtube_video_id)
                    just_published_jobs.append({
                        "id": job.id,
                        "youtube_video_id": job.youtube_video_id,
                        "target_channel_id": job.target_channel_id,
                        "scenes_json": job.scenes_json,
                        "title": job.title or "",
                        "is_just_published": True,
                    })
                    logger.info(
                        f"🔄 [SmartScheduler] Video #{job.id} ('{job.title[:30] if job.title else job.id}...') "
                        f"đã qua giờ hẹn ({st.strftime('%Y-%m-%d %H:%M UTC')}) -> Chuyển status = PUBLISHED."
                    )

            recent_pub_jobs = (
                db.query(VideoJob)
                .filter(
                    VideoJob.status == "PUBLISHED",
                    VideoJob.youtube_video_id.isnot(None),
                )
                .order_by(VideoJob.id.desc())
                .limit(25)
                .all()
            )
            for r_job in recent_pub_jobs:
                r_vid = r_job.youtube_video_id
                if not r_vid or r_vid in _COMMENT_VERIFIED_VIDEOS or r_vid in just_published_vids:
                    continue
                unverified_published_jobs.append({
                    "id": r_job.id,
                    "youtube_video_id": r_vid,
                    "target_channel_id": r_job.target_channel_id,
                    "scenes_json": r_job.scenes_json,
                    "title": r_job.title or "",
                    "is_just_published": False,
                })
                if len(unverified_published_jobs) >= 10:
                    break
    except Exception as e:
        logger.warning(f"Lỗi khi đồng bộ trạng thái SCHEDULED jobs: {e}")

    # Tự động kiểm tra & khóa chuẩn Category ID ('22'/'15', chặn tuyệt đối '27'), US/BR Geo-Lock và ghim bình luận mồi
    comment_check_jobs = just_published_jobs + unverified_published_jobs[:10]
    if comment_check_jobs:
        try:
            import json
            from app.agents.publisher_agent import PublisherAgent
            from app.services.channel_manager import get_channel
            for p_info in comment_check_jobs:
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

                c_res = p_agent.ensure_pinned_comment(v_id, p_comment)
                if isinstance(c_res, dict) and (
                    c_res.get("id") or c_res.get("status") in ("already_exists", "comments_disabled")
                ):
                    _COMMENT_VERIFIED_VIDEOS.add(v_id)

                if p_info.get("is_just_published"):
                    try:
                        from app.services.video_memory_rag import archive_job_to_rag_and_cleanup_files
                        archive_job_to_rag_and_cleanup_files(p_info["id"])
                    except Exception:
                        pass
        except Exception as bait_err:
            logger.warning(f"Lỗi khi tự động post bình luận mồi cho video vừa công chiếu: {bait_err}")

    # Tự động quét và xả hàng đợi các video chờ nạp lại Quota
    try:
        updated_count += process_waiting_quota_jobs()
    except Exception as qe:
        logger.debug(f"Lỗi khi xử lý hàng đợi quota: {qe}")

    try:
        return updated_count
    finally:
        try:
            _SCHEDULE_SYNC_LOCK.release()
        except Exception:
            pass


import threading as _threading

_WAITING_QUOTA_LOCK = _threading.Lock()


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
    
    if not _WAITING_QUOTA_LOCK.acquire(blocking=False):
        return 0
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
                    "scheduled_time": j.scheduled_time,
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
                now_utc = datetime.now(timezone.utc)
                sched_dt = job_data.get("scheduled_time")
                if sched_dt and sched_dt.tzinfo is None:
                    sched_dt = sched_dt.replace(tzinfo=timezone.utc)
                # Nếu giờ hẹn vẫn còn ở tương lai (> 15 phút nữa) thì giữ đúng lịch hẹn giờ vàng (SCHEDULED),
                # nếu đã qua giờ hẹn thì tìm khung giờ vàng tiếp theo cho kênh thay vì đăng bừa giờ xấu!
                is_lf_wait = _is_long_form_entry(
                    title=job_data.get("title") or "",
                    video_path=job_data.get("video_path") or "",
                    scenes_json=job_data.get("scenes_json"),
                )
                if not sched_dt or sched_dt <= now_utc + timedelta(minutes=15):
                    sched_dt, _, _, _ = get_next_available_golden_slot(
                        job_data["target_channel_id"],
                        _skip_sync=True,
                        is_long_form=is_lf_wait,
                    )
                sched_iso = sched_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                thumb_p = str(Path("storage/thumbnails") / f"thumb_{job_id}_9x16.jpg")
                if not Path(thumb_p).exists():
                    thumb_p = None

                logger.info(
                    f"🚀 [SmartScheduler] Quota đã được nạp lại! Đang tự động xuất bản & hẹn lịch Video #{job_id} "
                    f"('{job_data['title']}') vào slot {sched_iso}..."
                )
                res = pub_agent.upload_video(
                    video_path=job_data["video_path"],
                    title=job_data["title"] or f"Shorts #{job_id}",
                    description=job_data["description"] or "",
                    tags=[t.strip() for t in job_data["tags"].split(",") if t.strip()] if job_data.get("tags") else None,
                    privacy_status="private",
                    schedule_time_iso=sched_iso,
                    default_language=def_lang,
                    default_audio_language=def_audio_lang,
                    pinned_comment=pinned_comment,
                    topic=job_data.get("topic") or "",
                    script=job_data.get("scenes_json") or "",
                    thumbnail_path=thumb_p,
                )

                # Bước 3: Mở session MỚI để ghi kết quả (session sống ngắn)
                if res.get("status") == "success":
                    yt_id = res.get("video_id")
                    if yt_id:
                        try:
                            pub_agent.verify_and_heal_video_metadata(yt_id)
                        except Exception:
                            pass
                    with get_db() as db:
                        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                        if job:
                            job.status = "SCHEDULED"
                            job.youtube_video_id = yt_id
                            job.scheduled_time = sched_dt
                            job.error_message = None
                            job.progress_percent = 100
                            job.progress_message = f"Đã tự động hẹn lịch phát sóng ({sched_iso})"
                    try:
                        from app.services.video_memory_rag import archive_job_to_rag_and_cleanup_files
                        archive_job_to_rag_and_cleanup_files(job_id)
                    except Exception:
                        pass
                    processed_count += 1
                    logger.info(f"✅ [SmartScheduler] Tự động xuất bản thành công Video #{job_id}: {res.get('shorts_url')}")

            except Exception as job_ex:
                logger.warning(f"[SmartScheduler] Lỗi khi xử lý job #{job_id}: {job_ex}")
                continue

    except Exception as ex:
        logger.warning(f"Lỗi khi xử lý waiting_quota_jobs: {ex}")
    finally:
        _WAITING_QUOTA_LOCK.release()

    return processed_count



def get_next_available_golden_slot(
    channel_id: str = "ch_psychology_of_the_world",
    min_lead_minutes: int = 20,
    _skip_sync: bool = False,
    is_long_form: bool = False,
) -> Tuple[datetime, str, str, str]:
    """
    Tính toán khe giờ vàng tiếp theo còn trống cho kênh.
    Tự động phân luồng múi giờ & khung giờ vàng theo thị trường mục tiêu:
    - Thị trường Mỹ (US Channels - EDT/EST): GOLDEN_SLOTS_UTC
    - Thị trường Brazil (pt-BR Channels - BRT UTC-3): BRAZIL_GOLDEN_SLOTS_UTC
    - Hỗ trợ lịch Hybrid Tier-2 (2 Shorts 9:16 + 1 Long-Form 16:9 / ngày) với Anti-Collision >= 3.0h (10,800s).
    """
    # 0. Đồng bộ các video đã qua giờ hẹn trước khi tính toán
    if not _skip_sync:
        sync_scheduled_jobs_status()

    now_utc = datetime.now(timezone.utc)
    min_allowed_time = now_utc + timedelta(minutes=min_lead_minutes)

    # 1. Tìm tất cả alias của kênh này để quét occupied slots
    channel_family = resolve_channel_family(channel_id)
    is_br = is_brazil_channel(channel_id)
    active_slots_pool = list(get_active_slots_pool(channel_id))
    if is_long_form:
        # Thêm khe giờ vàng chuyên biệt cho Long-Form 16:9 Documentary (cách khe Shorts >= 3h20p theo Luật 4 Anti-Collision)
        lf_slot = (
            {
                "hour": 23,
                "minute": 40,
                "label_vn": "06:40 Sáng",
                "label_us": "20:40 PM BRT (KHUNG GIỜ VÀNG PHIM TÀI LIỆU 16:9 BRAZIL — CÁCH SHORTS >= 3H25P)",
                "description": "Khung giờ vàng tối 20:40 BRT dành riêng cho Phim tài liệu 16:9 tích lũy 4.000h xem (cách khe Shorts >= 3h25p).",
                "tag": "BR_LONGFORM_PRIME",
                "rank": 0,
            }
            if is_br
            else {
                "hour": 20,
                "minute": 45,
                "label_vn": "03:45 Sáng",
                "label_us": "15:45 PM EST / 16:45 PM EDT (KHUNG GIỜ VÀNG PHIM TÀI LIỆU 16:9 MỸ — CÁCH SHORTS >= 3H30P)",
                "description": "Khung giờ vàng chiều-tối Mỹ dành riêng cho Phim tài liệu 16:9 tích lũy 4.000h xem (cách cả 2 khe Shorts >= 3h30p, 100% VN ngủ say).",
                "tag": "US_LONGFORM_PRIME",
                "rank": 0,
            }
        )
        active_slots_pool = [lf_slot] + active_slots_pool
    target_tz = BR_TZ if is_br else US_EASTERN

    occupied_slots_utc: List[datetime] = []
    occupied_short_slots_utc: List[datetime] = []
    occupied_long_slots_utc: List[datetime] = []
    cutoff_48h = now_utc - timedelta(hours=48)
    with get_db() as db:
        scheduled_rows = db.query(
            VideoJob.scheduled_time,
            VideoJob.status,
            VideoJob.youtube_video_id,
            VideoJob.updated_at,
            VideoJob.created_at,
            VideoJob.title,
            VideoJob.video_path,
            VideoJob.scenes_json,
            VideoJob.audio_duration_ms,
        ).filter(
            VideoJob.target_channel_id.in_(channel_family),
            VideoJob.status.in_(["SCHEDULED", "PUBLISHED", "WAITING_QUOTA_RESET", "RENDERED", "RENDERING"]),
        ).order_by(VideoJob.id.desc()).limit(60).all()

        for st_val, status_val, yt_val, upd_val, cre_val, t_val, vp_val, sj_val, adur_val in scheduled_rows:
            st = st_val
            if not st and status_val == "PUBLISHED" and yt_val:
                st = upd_val or cre_val
            if st:
                if st.tzinfo is None:
                    st = st.replace(tzinfo=timezone.utc)
                # Chỉ tính các slot đã đăng hoặc lên lịch trong vòng 48h qua hoặc trong tương lai
                if st >= cutoff_48h:
                    occupied_slots_utc.append(st)
                    is_lf_row = _is_long_form_entry(
                        title=t_val or "",
                        video_path=vp_val or "",
                        scenes_json=sj_val,
                        duration_sec=float(adur_val or 0) / 1000.0,
                    )
                    if is_lf_row:
                        occupied_long_slots_utc.append(st)
                    else:
                        occupied_short_slots_utc.append(st)

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
                        pv_dur = float(getattr(pv, "duration_seconds", 0) or 0)
                        if _is_long_form_entry(title=getattr(pv, "title", "") or "", duration_sec=pv_dur):
                            occupied_long_slots_utc.append(pv_dt)
                        else:
                            occupied_short_slots_utc.append(pv_dt)
        except Exception as e_pv:
            logger.debug(f"[SmartScheduler] Không thể quét PublishedVideo cho {channel_id}: {e_pv}")

        # Bổ sung thêm các video thực tế trên YouTube từ storage/channel_audit_latest.json (cả public và scheduled_queue)
        try:
            audit_file = Path(__file__).resolve().parent.parent.parent / "storage" / "channel_audit_latest.json"
            if audit_file.exists():
                audit_data = json.loads(audit_file.read_text(encoding="utf-8"))
                for ch_k, ch_v in (audit_data or {}).items():
                    if not isinstance(ch_v, dict):
                        continue
                    if ch_k in channel_family or ch_v.get("id") in channel_family or ch_v.get("handle") in channel_family:
                        for sq in ch_v.get("scheduled_queue", []):
                            sq_iso = sq.get("publish_at_utc")
                            if sq_iso:
                                sq_dt = datetime.fromisoformat(sq_iso.replace("Z", "+00:00"))
                                if sq_dt >= cutoff_48h and not any(abs((sq_dt - ex).total_seconds()) < 120 for ex in occupied_slots_utc):
                                    occupied_slots_utc.append(sq_dt)
                                    if _is_long_form_entry(title=sq.get("title", ""), duration_sec=float(sq.get("duration_sec") or 0)):
                                        occupied_long_slots_utc.append(sq_dt)
                                    else:
                                        occupied_short_slots_utc.append(sq_dt)
                        for vd in ch_v.get("videos", []):
                            vd_iso = vd.get("published_at_utc")
                            if vd_iso:
                                vd_dt = datetime.fromisoformat(vd_iso.replace("Z", "+00:00"))
                                if vd_dt >= cutoff_48h and not any(abs((vd_dt - ex).total_seconds()) < 120 for ex in occupied_slots_utc):
                                    occupied_slots_utc.append(vd_dt)
                                    if _is_long_form_entry(title=vd.get("title", ""), duration_sec=float(vd.get("duration_sec") or 0)):
                                        occupied_long_slots_utc.append(vd_dt)
                                    else:
                                        occupied_short_slots_utc.append(vd_dt)
        except Exception as e_aud:
            logger.debug(f"[SmartScheduler] Không thể quét channel_audit_latest.json cho {channel_id}: {e_aud}")

    logger.debug(f"[SmartScheduler] Kênh {channel_id} (Family: {channel_family}, Brazil={is_br}) hiện có {len(occupied_slots_utc)} khe giờ đã đặt lịch/xuất bản.")

    # 2. Duyệt tìm khe giờ trống bắt đầu từ ngày hôm nay đến 45 ngày tiếp theo
    # Áp dụng triệt để: TỐI ĐA 2 SHORTS / NGÀY / KÊNH (+ 1 LONG-FORM 16:9 ĐỘC LẬP CÁCH >= 3.0 GIỜ)
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

        if is_long_form or is_mysteries_diamond_channel(channel_id):
            candidate_slots.sort(key=lambda item: (item[1].get("rank", 99), item[0]))
        else:
            candidate_slots.sort(key=lambda item: item[0])

        same_format_slots = occupied_long_slots_utc if is_long_form else occupied_short_slots_utc
        cross_format_slots = occupied_short_slots_utc if is_long_form else occupied_long_slots_utc
        max_for_format = MAX_LONG_FORM_PER_DAY if is_long_form else MAX_VIDEOS_PER_DAY

        for candidate_dt, slot in candidate_slots:
            # 1. Kiểm tra va chạm thời gian:
            # - Cùng định dạng (Short vs Short hoặc Long vs Long): cách nhau >= 6.0 giờ (21,600s)
            # - Khác định dạng (Long 16:9 vs Short 9:16): cách nhau >= 3.0 giờ (10,800s theo Luật 4 Anti-Collision)
            is_too_close = False
            for occ_same in same_format_slots:
                if abs((candidate_dt - occ_same).total_seconds()) < MIN_SPACING_SECONDS:
                    is_too_close = True
                    break
            if not is_too_close:
                for occ_cross in cross_format_slots:
                    if abs((candidate_dt - occ_cross).total_seconds()) < LONG_SHORT_ANTI_COLLISION_SECONDS:
                        is_too_close = True
                        break
            if is_too_close:
                continue

            # 2. KIỂM TRA QUOTA TRONG NGÀY THEO ĐỊNH DẠNG (2 Shorts/ngày + 1 Long-Form/ngày):
            target_market_date = candidate_dt.astimezone(target_tz).date()
            market_day_count = sum(1 for occ in same_format_slots if occ.astimezone(target_tz).date() == target_market_date)

            if market_day_count >= max_for_format:
                logger.debug(
                    f"[SmartScheduler] Ngày {target_market_date} ({'BR' if is_br else 'US'}) đã có đủ {market_day_count} video "
                    f"{'Long-Form' if is_long_form else 'Shorts'} (giới hạn {max_for_format}/ngày) -> Chuyển sang ngày tiếp theo."
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



def get_channel_upcoming_schedule(channel_id: str = "ch_psychology_of_the_world", limit: int = 10, _skip_sync: bool = False) -> List[Dict[str, Any]]:
    """Lấy danh sách các video đã lên lịch trong tương lai của kênh để theo dõi."""
    if not _skip_sync:
        sync_scheduled_jobs_status()
    now_utc = datetime.now(timezone.utc)
    channel_family = resolve_channel_family(channel_id)
    is_br = is_brazil_channel(channel_id)
    active_slots_pool = get_active_slots_pool(channel_id)
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
    target_start_date: Optional[datetime] = None,
    _skip_sync: bool = False,
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
    if not _skip_sync:
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

