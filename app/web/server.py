import os
from pathlib import Path
from typing import Dict, List, Any, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import contextmanager
from sqlalchemy import func
from config.settings import settings
from app.database.models import init_db, get_db, User, VideoJob, ActivityLog

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="AI YouTube Multi-Channel Studio",
    description="Giao diện Web quản lý hệ thống tự động hoá YouTube",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.on_event("startup")
def on_startup():
    init_db()
    try:
        with get_db() as db:
            admin = db.query(User).filter(User.telegram_id == 0).first()
            if not admin:
                admin = User(
                    telegram_id=0,
                    username="web_studio",
                    full_name="Web Studio UI",
                    role="admin",
                    active_channel_id="channel_psychology"
                )
                db.add(admin)
    except Exception as e:
        print(f"Warning during startup DB check: {e}")

# ─── Serve UI ─────────────────────────────────────────────────────────────────

@app.get("/")
async def serve_dashboard():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="index.html chưa tồn tại")
    return FileResponse(index_path)

@app.get("/health")
async def health_check():
    """Endpoint kiểm tra sức khỏe hệ thống (cho Render, UptimeRobot, Docker)"""
    return {"status": "ok", "app": "AI YouTube Multi-Channel Studio"}

# ─── Stats ────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def get_overview_stats() -> Dict[str, Any]:
    with get_db() as db:
        total_videos = db.query(VideoJob).count()
        script_ready = db.query(VideoJob).filter(VideoJob.status == "SCRIPT_READY").count()
        rendering = db.query(VideoJob).filter(VideoJob.status == "RENDERING").count()
        rendered = db.query(VideoJob).filter(VideoJob.status == "RENDERED").count()
        published = db.query(VideoJob).filter(VideoJob.status == "PUBLISHED").count()
        failed = db.query(VideoJob).filter(VideoJob.status == "FAILED").count()
        total_users = db.query(User).count()
        total_activities = db.query(ActivityLog).count()

    channels = settings.get_channels_config()
    gemini_keys = settings.get_gemini_keys_pool()

    # Đếm số lượng kênh thực tế đã đăng nhập Google OAuth
    auth_count = 0
    for ch in channels:
        token_rel = ch.get("credentials", {}).get("youtube_token")
        if token_rel and (BASE_DIR / token_rel).is_file():
            auth_count += 1

    from app.utils.ffmpeg_helper import is_ffmpeg_installed
    return {
        "total_videos": total_videos,
        "script_ready": script_ready,
        "rendering": rendering,
        "rendered": rendered,
        "published": published,
        "failed": failed,
        "total_members": total_users,
        "total_channels": len(channels),
        "auth_channels": auth_count,
        "gemini_keys_count": len(gemini_keys),
        "total_activities": total_activities,
        "ffmpeg_installed": is_ffmpeg_installed(),
        "storage_drive_total": f"{auth_count * 2} TB (Đã liên kết)" if auth_count > 0 else "Chờ cấp quyền OAuth",
    }

# ─── Activities ───────────────────────────────────────────────────────────────

@app.get("/api/activities")
async def get_recent_activities(limit: int = 30) -> List[Dict[str, Any]]:
    with get_db() as db:
        logs = db.query(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(limit).all()
        return [
            {
                "id": l.id,
                "telegram_id": l.telegram_id,
                "username": l.username,
                "action_type": l.action_type,
                "details": l.details,
                "channel_id": l.channel_id,
                "created_at": l.created_at.strftime("%Y-%m-%d %H:%M:%S") if l.created_at else "",
            }
            for l in logs
        ]

# ─── Members ──────────────────────────────────────────────────────────────────

@app.get("/api/members")
async def get_team_members() -> List[Dict[str, Any]]:
    with get_db() as db:
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
                "last_active": u.last_active.strftime("%Y-%m-%d %H:%M:%S") if u.last_active else "N/A",
            })
    members.sort(key=lambda x: (x["total_jobs"], x["total_activities"]), reverse=True)
    return members

# ─── Channels ─────────────────────────────────────────────────────────────────

@app.get("/api/channels")
async def get_channels_matrix() -> List[Dict[str, Any]]:
    channels = settings.get_channels_config()
    result = []
    with get_db() as db:
        for ch in channels:
            ch_id = ch.get("id")
            video_count = db.query(VideoJob).filter(VideoJob.target_channel_id == ch_id).count()
            published_count = db.query(VideoJob).filter(
                VideoJob.target_channel_id == ch_id, VideoJob.status == "PUBLISHED"
            ).count()

            # Kiểm tra token xác thực Google OAuth thực tế
            token_rel = (ch.get("credentials") or {}).get("youtube_token")
            token_path = (BASE_DIR / token_rel) if token_rel else None
            is_connected = bool(token_path and token_path.is_file())

            result.append({
                "id": ch_id,
                "name": ch.get("name"),
                "niche": ch.get("niche"),
                "language": ch.get("language", "en"),
                "google_account": ch.get("google_account", "account_1"),
                "token_status": "🟢 Đã kết nối" if is_connected else "🟡 Chờ cấp quyền",
                "is_authenticated": is_connected,
                "preferred_voice": ch.get("preferred_voice", "en-US-ChristopherNeural"),
                "schedule": ch.get("schedule", {}),
                "video_count": video_count,
                "published_count": published_count,
                "drive_quota": "2 TB Google One",
            })
    return result

# ─── Videos ───────────────────────────────────────────────────────────────────

@app.get("/api/videos")
async def get_video_pipeline(limit: int = 50) -> List[Dict[str, Any]]:
    with get_db() as db:
        jobs = db.query(VideoJob).order_by(VideoJob.created_at.desc()).limit(limit).all()
        results = []
        for j in jobs:
            creator_name = j.creator.username if j.creator and j.creator.username else f"User_{j.user_id}"
            # Parse scenes_json to get scene count
            scene_count = 0
            if j.scenes_json:
                try:
                    import json
                    data = json.loads(j.scenes_json)
                    scene_count = len(data.get("scenes", []))
                except Exception:
                    pass
            results.append({
                "id": j.id,
                "creator": creator_name,
                "channel_id": j.target_channel_id,
                "topic": j.topic,
                "title": j.title or "Chưa có tiêu đề",
                "script": (j.script or "")[:200],
                "status": j.status,
                "error_message": j.error_message or "",
                "scene_count": scene_count,
                "audio_duration_ms": j.audio_duration_ms or 0,
                "drive_link": j.drive_link or "",
                "youtube_id": j.youtube_video_id or "",
                "video_path": j.video_path or "",
                "created_at": j.created_at.strftime("%Y-%m-%d %H:%M") if j.created_at else "",
                "scheduled_time": j.scheduled_time.strftime("%Y-%m-%d %H:%M") if j.scheduled_time else "",
            })
    return results

@app.get("/api/videos/{job_id}")
async def get_video_detail(job_id: int) -> Dict[str, Any]:
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        scenes = []
        if job.scenes_json:
            try:
                import json
                data = json.loads(job.scenes_json)
                scenes = data.get("scenes", [])
            except Exception:
                pass
        return {
            "id": job.id,
            "topic": job.topic,
            "title": job.title or "",
            "script": job.script or "",
            "description": job.description or "",
            "tags": job.tags or "",
            "status": job.status,
            "error_message": job.error_message or "",
            "scenes": scenes,
            "audio_duration_ms": job.audio_duration_ms or 0,
            "video_path": job.video_path or "",
            "drive_link": job.drive_link or "",
            "youtube_id": job.youtube_video_id or "",
            "created_at": job.created_at.strftime("%Y-%m-%d %H:%M") if job.created_at else "",
        }

# ─── Delete Job ───────────────────────────────────────────────────────────────

@app.delete("/api/videos/{job_id}")
async def delete_video_job(job_id: int) -> Dict[str, str]:
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        db.delete(job)
    return {"message": f"Đã xoá job #{job_id}"}

# ─── Create Script (Web UI trigger) ──────────────────────────────────────────

class CreateScriptRequest(BaseModel):
    topic: str
    channel_id: str = "channel_psychology"
    language: str = "en"

@app.post("/api/scripts/create")
async def create_script_from_web(body: CreateScriptRequest) -> Dict[str, Any]:
    """
    Tạo kịch bản AI trực tiếp từ Web Studio.
    Lưu vào DB dưới dạng user_id=0 (Web UI trigger).
    """
    try:
        from app.agents.content_agent import ContentAgent
        agent = ContentAgent()
        script = agent.generate_script(
            topic=body.topic,
            channel_niche="general",
            language=body.language
        )
        with get_db() as db:
            job = VideoJob(
                user_id=0,  # 0 = Web UI
                target_channel_id=body.channel_id,
                topic=body.topic,
                title=script.title,
                script=script.full_narration,
                description=script.description,
                tags=",".join(script.tags),
                scenes_json=script.to_json_str(),
                status="SCRIPT_READY",
            )
            db.add(job)
            db.flush()
            job_id = job.id
        return {
            "success": True,
            "job_id": job_id,
            "title": script.title,
            "hook": script.hook,
            "scene_count": len(script.scenes),
            "total_duration": script.total_estimated_duration,
            "scenes": [
                {
                    "scene_id": s.scene_id,
                    "narration": s.narration,
                    "visual_keyword": s.visual_keyword,
                    "estimated_seconds": s.estimated_seconds,
                }
                for s in script.scenes
            ],
        }
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"Lỗi tạo kịch bản: {str(e)}\n{traceback.format_exc()}")

# ─── Update Job Status ────────────────────────────────────────────────────────

class UpdateStatusRequest(BaseModel):
    status: str
    error_message: Optional[str] = None

@app.patch("/api/videos/{job_id}/status")
async def update_job_status(job_id: int, body: UpdateStatusRequest) -> Dict[str, str]:
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        job.status = body.status
        if body.error_message:
            job.error_message = body.error_message
    return {"message": f"Đã cập nhật trạng thái job #{job_id} → {body.status}"}

# ─── Health Check ─────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check() -> Dict[str, Any]:
    from app.utils.ffmpeg_helper import is_ffmpeg_installed, get_ffmpeg_version
    keys = settings.get_gemini_keys_pool()
    return {
        "status": "online",
        "ffmpeg": get_ffmpeg_version() if is_ffmpeg_installed() else "not installed",
        "gemini_keys": len(keys),
        "telegram_token": bool(settings.TELEGRAM_BOT_TOKEN),
    }
