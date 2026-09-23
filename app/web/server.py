import os
import shutil
from pathlib import Path
from typing import Dict, List, Any, Optional
from fastapi import FastAPI, HTTPException, Request, Form, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func
from config.settings import settings, BASE_DIR
from app.database.models import SessionLocal, User, VideoJob, ActivityLog, log_activity
from app.services.channel_manager import channel_manager

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="AI YouTube Multi-Channel Studio Dashboard",
    description="Trung tâm điều khiển và giám sát hoạt động sáng tạo nội dung YouTube tự động đa tài khoản",
    version="1.1.0"
)

# Mount thư mục tĩnh
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

class ChannelCreateSchema(BaseModel):
    id: str
    name: str
    niche: str
    google_account: str = "account_1"
    language: str = "en"
    visual_mode: str = "imagen_ai"
    preferred_voice: str = "en-US-ChristopherNeural"

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

    channels = channel_manager.list_channels()
    gemini_keys = settings.get_gemini_keys_pool()

    return {
        "total_videos": total_videos,
        "script_ready": script_ready,
        "published": published,
        "total_members": total_users,
        "total_channels": len(channels),
        "gemini_keys_count": len(gemini_keys),
        "total_activities": total_activities,
        "storage_drive_total": f"{max(1, len(channels)) * 2} TB (Cụm Ultra)"
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
    members.sort(key=lambda x: (x["total_jobs"], x["total_activities"]), reverse=True)
    return members

# ==============================================================================
# QUẢN LÝ KÊNH YOUTUBE & XÁC THỰC GOOGLE OAUTH2
# ==============================================================================

@app.get("/api/channels")
async def get_channels_matrix() -> List[Dict[str, Any]]:
    """Lấy danh sách các kênh YouTube kèm trạng thái Live thật từ YouTube API"""
    return channel_manager.list_channels()

@app.post("/api/channels/add")
async def add_new_channel(data: ChannelCreateSchema):
    """Thêm một kênh YouTube mới vào hệ thống qua Web Dashboard"""
    try:
        new_ch = channel_manager.add_channel(
            channel_id=data.id,
            name=data.name,
            niche=data.niche,
            google_account=data.google_account,
            language=data.language,
            visual_mode=data.visual_mode,
            preferred_voice=data.preferred_voice
        )
        
        # Ghi log hoạt động
        db = SessionLocal()
        log_activity(db, 0, "WebAdmin", "ADD_CHANNEL", f"Thêm kênh mới: '{data.name}' ({data.id})", channel_id=data.id)
        db.close()

        return {"status": "success", "channel": new_ch}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/channels/{channel_id}")
async def delete_channel(channel_id: str):
    """Xóa kênh YouTube khỏi hệ thống"""
    success = channel_manager.delete_channel(channel_id)
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh để xóa.")
    
    db = SessionLocal()
    log_activity(db, 0, "WebAdmin", "DELETE_CHANNEL", f"Đã xóa kênh '{channel_id}'", channel_id=channel_id)
    db.close()

    return {"status": "success", "deleted_channel": channel_id}

@app.get("/auth/youtube/{channel_id}")
async def start_youtube_auth(channel_id: str, request: Request):
    """Bắt đầu luồng Google OAuth2 cho kênh được chọn"""
    # Xây dựng redirect_uri dựa trên host hiện tại
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/oauth2callback"
    
    try:
        auth_url = channel_manager.start_oauth_flow(channel_id, redirect_uri)
        return RedirectResponse(auth_url)
    except FileNotFoundError as e:
        return RedirectResponse(f"/?auth_error={str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi khởi tạo OAuth2: {e}")

@app.get("/oauth2callback")
async def oauth2_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    """Xử lý phản hồi từ Google sau khi người dùng đăng nhập cấp quyền cho kênh"""
    if error or not code:
        return RedirectResponse(f"/?auth_error={error or 'Khong_co_code'}")

    channel_id = state or "channel_default"
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/oauth2callback"

    try:
        status_info = channel_manager.finish_oauth_flow(channel_id, code, redirect_uri)
        
        db = SessionLocal()
        log_activity(db, 0, "GoogleOAuth", "AUTH_SUCCESS", f"Kết nối thành công kênh YouTube: '{status_info.get('real_title') or channel_id}'", channel_id=channel_id)
        db.close()

        return RedirectResponse(f"/?auth_success=1&channel={channel_id}")
    except Exception as e:
        return RedirectResponse(f"/?auth_error={str(e)}")

@app.post("/api/credentials/upload_secret")
async def upload_client_secret(file: UploadFile = File(...)):
    """Tải lên file client_secret.json từ Google Cloud Console trực tiếp trên web"""
    dest_path = BASE_DIR / "credentials" / "client_secret.json"
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"status": "success", "message": "Đã lưu file client_secret.json thành công!"}

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
