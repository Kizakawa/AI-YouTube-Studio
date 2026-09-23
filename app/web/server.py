import os
from pathlib import Path
from typing import Dict, List, Any
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import func
from config.settings import settings
from app.database.models import SessionLocal, User, VideoJob, ActivityLog

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="AI YouTube Multi-Channel Studio Dashboard",
    description="Trung tâm điều khiển và giám sát hoạt động sáng tạo nội dung YouTube tự động đa tài khoản",
    version="1.0.0"
)

# Mount thư mục tĩnh
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def serve_dashboard():
    """Phục vụ giao diện Web Dashboard chính"""
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Giao diện Dashboard chưa được tạo!")
    return FileResponse(index_path)

@app.get("/api/stats")
async def get_overview_stats() -> Dict[str, Any]:
    """Thống kê tổng quan hệ thống"""
    db = SessionLocal()
    total_videos = db.query(VideoJob).count()
    script_ready = db.query(VideoJob).filter(VideoJob.status == "SCRIPT_READY").count()
    published = db.query(VideoJob).filter(VideoJob.status == "PUBLISHED").count()
    total_users = db.query(User).count()
    total_activities = db.query(ActivityLog).count()
    db.close()

    channels = settings.get_channels_config()
    gemini_keys = settings.get_gemini_keys_pool()

    # Đếm số lượng kênh thực tế đã đăng nhập Google OAuth
    auth_count = 0
    for ch in channels:
        token_rel = ch.get("credentials", {}).get("youtube_token")
        if token_rel and (BASE_DIR / token_rel).is_file():
            auth_count += 1

    return {
        "total_videos": total_videos,
        "script_ready": script_ready,
        "published": published,
        "total_members": total_users,
        "total_channels": len(channels),
        "auth_channels": auth_count,
        "gemini_keys_count": len(gemini_keys),
        "total_activities": total_activities,
        "storage_drive_total": f"{auth_count * 2} TB" if auth_count > 0 else "Chưa kết nối OAuth"
    }

@app.get("/api/activities")
async def get_recent_activities(limit: int = 30) -> List[Dict[str, Any]]:
    """Lấy danh sách các hoạt động gần đây của các admin/creator qua Telegram"""
    db = SessionLocal()
    logs = db.query(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(limit).all()
    results = []
    for l in logs:
        results.append({
            "id": l.id,
            "telegram_id": l.telegram_id,
            "username": l.username,
            "action_type": l.action_type,
            "details": l.details,
            "channel_id": l.channel_id,
            "created_at": l.created_at.strftime("%Y-%m-%d %H:%M:%S") if l.created_at else ""
        })
    db.close()
    return results

@app.get("/api/members")
async def get_team_members() -> List[Dict[str, Any]]:
    """Lấy danh sách và thống kê đóng góp của các tài khoản thành viên/admin"""
    db = SessionLocal()
    users = db.query(User).all()
    members = []
    for u in users:
        job_count = db.query(VideoJob).filter(VideoJob.user_id == u.telegram_id).count()
        activity_count = db.query(ActivityLog).filter(ActivityLog.telegram_id == u.telegram_id).count()
        members.append({
            "telegram_id": u.telegram_id,
            "username": u.username or f"User_{u.telegram_id}",
            "full_name": u.full_name or "N/A",
            "role": u.role or "creator",
            "active_channel_id": u.active_channel_id or "Chưa chọn",
            "total_jobs": job_count,
            "total_activities": activity_count,
            "last_active": u.last_active.strftime("%Y-%m-%d %H:%M:%S") if u.last_active else "N/A"
        })
    db.close()
    # Sắp xếp theo mức độ đóng góp (số video và hoạt động)
    members.sort(key=lambda x: (x["total_jobs"], x["total_activities"]), reverse=True)
    return members

@app.get("/api/channels")
async def get_channels_matrix() -> List[Dict[str, Any]]:
    """Ma trận các kênh YouTube và tài khoản Google Ultra tương ứng"""
    channels = settings.get_channels_config()
    db = SessionLocal()
    result = []
    for ch in channels:
        ch_id = ch.get("id")
        video_count = db.query(VideoJob).filter(VideoJob.target_channel_id == ch_id).count()
        published_count = db.query(VideoJob).filter(VideoJob.target_channel_id == ch_id, VideoJob.status == "PUBLISHED").count()
        
        # Kiểm tra token xác thực Google OAuth thực tế
        token_rel = ch.get("credentials", {}).get("youtube_token")
        token_path = (BASE_DIR / token_rel) if token_rel else None
        token_exists = bool(token_path and token_path.is_file())

        result.append({
            "id": ch_id,
            "name": ch.get("name"),
            "niche": ch.get("niche"),
            "language": ch.get("language", "en"),
            "google_account": ch.get("google_account", "account_1"),
            "token_status": "🟢 Đã kết nối" if token_exists else "🔴 Chưa kết nối Google",
            "is_authenticated": token_exists,
            "preferred_voice": ch.get("preferred_voice"),
            "schedule": ch.get("schedule", {}),
            "video_count": video_count,
            "published_count": published_count,
            "drive_quota": "2 TB Google One"
        })
    db.close()
    return result

@app.get("/api/videos")
async def get_video_pipeline(limit: int = 50) -> List[Dict[str, Any]]:
    """Lấy danh sách kịch bản và tiến độ sản xuất video"""
    db = SessionLocal()
    jobs = db.query(VideoJob).order_by(VideoJob.created_at.desc()).limit(limit).all()
    results = []
    for j in jobs:
        creator_name = j.creator.username if j.creator and j.creator.username else f"User_{j.user_id}"
        results.append({
            "id": j.id,
            "creator": creator_name,
            "channel_id": j.target_channel_id,
            "topic": j.topic,
            "title": j.title or "Chưa có tiêu đề",
            "script": j.script or "",
            "status": j.status,
            "drive_link": j.drive_link or "#",
            "youtube_id": j.youtube_video_id or "",
            "created_at": j.created_at.strftime("%Y-%m-%d %H:%M") if j.created_at else "",
            "scheduled_time": j.scheduled_time.strftime("%Y-%m-%d %H:%M") if j.scheduled_time else "Chưa đặt lịch"
        })
    db.close()
    return results
