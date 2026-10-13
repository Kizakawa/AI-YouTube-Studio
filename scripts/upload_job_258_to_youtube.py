"""
Script upload video_258.mp4 lên YouTube cho kênh Wild Odyssey và hẹn lịch 5h sáng (05:00 AM VN)
"""
import sys
import os
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from config.settings import settings
from app.database.models import get_db, VideoJob, log_activity
from app.agents.publisher_agent import PublisherAgent
from app.services.quota_manager import QuotaManager, COST_FULL_SHORTS

CHANNEL_ID = "ch_wild_odyssey"
CHANNEL_NAME = "Wild Odyssey"
CHANNEL_NICHE = "wildlife_predators"
CHANNEL_LANG = "en"
GOOGLE_ACCOUNT = "acount_2"

VN_TZ = timezone(timedelta(hours=7))
SCHEDULE_SLOT_UTC = datetime(2026, 10, 9, 22, 0, 0, tzinfo=timezone.utc)
SCHEDULE_SLOT_ISO = "2026-10-09T22:00:00Z"
_t_vn = SCHEDULE_SLOT_UTC.astimezone(VN_TZ)
DISPLAY_VN = f"{_t_vn.strftime('%H:%M')} ngày {_t_vn.strftime('%d/%m/%Y')} (Giờ VN)"

def main():
    print("=" * 70)
    print(f"🚀 BẮT ĐẦU UPLOAD VIDEO CHO KÊNH: {CHANNEL_NAME} ({CHANNEL_ID})")
    print(f"⏰ LỊCH CÔNG CHIẾU YOUTUBE SHORTS: {DISPLAY_VN} ({SCHEDULE_SLOT_ISO})")
    print("=" * 70)

    # Đọc thông tin từ DB Job 258
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == 258).first()
        if not job:
            print("❌ Không tìm thấy Job 258 trong Database!")
            return

        title = job.title or "Don't Blink: The 70MPH Living Bullet 🐆💨"
        description = job.description or ""
        tags = [t.strip() for t in (job.tags or "").split(",") if t.strip()]
        video_path = job.video_path or str(Path(settings.BASE_DIR) / "storage" / "outputs" / "video_258.mp4")
        
        pinned_comment = None
        if job.scenes_json:
            try:
                s_data = json.loads(job.scenes_json)
                pinned_comment = s_data.get("pinned_comment")
            except Exception:
                pass
        if not pinned_comment:
            pinned_comment = "Could you outrun this living bullet for even one second? Let me know below! 👇\nHit subscribe for more wild predator secrets! 🦁"

    if not Path(video_path).exists():
        print(f"❌ File video không tồn tại: {video_path}")
        return

    print(f"📁 Video: {video_path} ({os.path.getsize(video_path) / 1024 / 1024:.2f} MB)")
    print(f"📌 Tiêu đề: {title}")

    ch_cfg = {
        "id": CHANNEL_ID,
        "name": CHANNEL_NAME,
        "niche": CHANNEL_NICHE,
        "language": CHANNEL_LANG,
        "google_account": GOOGLE_ACCOUNT,
    }

    pub_agent = PublisherAgent(channel_config=ch_cfg)

    # Thumbnail paths
    thumb_path = str(Path(settings.BASE_DIR) / "storage" / "thumbnails" / "thumb_258_16x9.jpg")
    if not os.path.exists(thumb_path):
        thumb_path = None

    print("\n📤 Đang tải video lên YouTube Data API v3...")
    pub_res = pub_agent.upload_video(
        video_path=str(video_path),
        title=title,
        description=description,
        tags=tags,
        privacy_status="private",
        schedule_time_iso=SCHEDULE_SLOT_ISO,
        default_language="en-US",
        default_audio_language="en-US",
        pinned_comment=pinned_comment,
        topic="Cheetah Elastic Spine 70mph Acceleration",
        thumbnail_path=thumb_path
    )

    print("Kết quả PublisherAgent:", json.dumps(pub_res, indent=2, ensure_ascii=False))

    if pub_res.get("status") == "failed":
        print(f"❌ Upload thất bại: {pub_res.get('error')}")
        return

    yt_id = pub_res.get("video_id")
    if not yt_id:
        print(f"⚠️ Trạng thái phản hồi: {pub_res.get('status')} - Message: {pub_res.get('message')}")
        return

    yt_shorts_url = pub_res.get("shorts_url") or f"https://www.youtube.com/shorts/{yt_id}"

    # Cập nhật Job trong Database
    with get_db() as db:
        j = db.query(VideoJob).filter(VideoJob.id == 258).first()
        if j:
            j.status = "SCHEDULED"
            j.youtube_video_id = yt_id
            j.scheduled_time = SCHEDULE_SLOT_UTC
            j.progress_percent = 100
            j.progress_message = f"✅ Đã lên lịch phát sóng thành công lúc {DISPLAY_VN}"
            db.commit()

        log_activity(
            db,
            telegram_id=1,
            username="System_Director",
            action_type="SCHEDULED_VIDEO_SUCCESS",
            details=f"Đã lên lịch thành công video #258 ('{title}') cho kênh {CHANNEL_NAME} vào {DISPLAY_VN}. YouTube ID: {yt_id}",
            channel_id=CHANNEL_ID
        )

    print("\n" + "=" * 70)
    print("🎉 TẢI LÊN VÀ HẸN LỊCH THÀNH CÔNG RỰC RỠ!")
    print(f"👉 Kênh: {CHANNEL_NAME} (@wildodysseyhq)")
    print(f"👉 Tiêu đề: {title}")
    print(f"👉 Video ID: {yt_id}")
    print(f"👉 Link YouTube Shorts: {yt_shorts_url}")
    print(f"👉 Lịch công chiếu: {DISPLAY_VN}")
    print("=" * 70)

if __name__ == "__main__":
    main()
