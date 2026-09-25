import os
import json
import yaml
import base64
import threading
import logging
import requests as http_requests
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
from app.database.models import init_db, get_db, User, VideoJob, ActivityLog, log_activity

logger = logging.getLogger(__name__)

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
    # Đảm bảo có user web_studio
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

    # [FIX #7] Khôi phục jobs bị kẹt ở RENDERING khi restart (web-only mode)
    try:
        with get_db() as db:
            stuck_jobs = db.query(VideoJob).filter(VideoJob.status == "RENDERING").all()
            for j in stuck_jobs:
                j.status = "SCRIPT_READY"
                j.progress_percent = 0
                j.progress_message = "Đã khôi phục sau khi Web Dashboard khởi động lại"
            if stuck_jobs:
                logger.info(f"[Startup Recovery] Đã khôi phục {len(stuck_jobs)} video bị kẹt về SCRIPT_READY")
    except Exception as e:
        logger.warning(f"Lỗi recover stuck jobs on startup: {e}")

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

    # Kiểm tra Drive token thật sự
    drive_connected = False
    drive_quota_str = "Chưa kết nối"
    try:
        drive_token_path = BASE_DIR / "credentials" / "accounts" / "account_1" / "drive_token.json"
        if drive_token_path.exists():
            drive_connected = True
            drive_quota_str = "20 TB (Đã kết nối ✓)"
    except Exception:
        pass

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
        "storage_drive_total": drive_quota_str,
        "drive_connected": drive_connected,
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
    cfg = _read_channels_yaml()
    channels = cfg.get("channels", [])
    raw_accounts = cfg.get("google_accounts", [])
    accounts_map = {a.get("id"): a for a in raw_accounts}

    result = []
    with get_db() as db:
        for ch in channels:
            ch_id = ch.get("id")
            video_count = db.query(VideoJob).filter(VideoJob.target_channel_id == ch_id).count()
            published_count = db.query(VideoJob).filter(
                VideoJob.target_channel_id == ch_id, VideoJob.status == "PUBLISHED"
            ).count()

            # Lấy thông tin tài khoản Google mẹ
            acc_id = ch.get("google_account", "account_1")
            parent_acc = accounts_map.get(acc_id, {})
            gmail = parent_acc.get("email_label") or "Chưa cấu hình Gmail"

            # Kiểm tra token xác thực Google OAuth thực tế của tài khoản mẹ
            token_status = _check_account_tokens(acc_id)
            is_connected = token_status["youtube_connected"]
            drive_connected = token_status["drive_connected"]
            has_client_secret = token_status["has_client_secret"]

            result.append({
                "id": ch_id,
                "name": ch.get("name"),
                "niche": ch.get("niche"),
                "language": ch.get("language", "en"),
                "google_account": acc_id,
                "gmail": gmail,
                "email_label": gmail,
                "token_status": "🟢 Đã kết nối" if is_connected else "🟡 Chờ cấp quyền",
                "is_authenticated": is_connected,
                "youtube_connected": is_connected,
                "drive_connected": drive_connected,
                "has_client_secret": has_client_secret,
                "visual_mode": ch.get("visual_mode", "imagen_ai"),
                "preferred_voice": ch.get("preferred_voice", "en-US-ChristopherNeural"),
                "schedule": ch.get("schedule", {}),
                "video_count": video_count,
                "published_count": published_count,
                "drive_quota": parent_acc.get("drive_storage", "2 TB Google One"),
                "youtube_quota_daily": parent_acc.get("youtube_quota_daily", 10000),
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
            # Xác định nguồn tạo: Web (user_id=0) hoặc Telegram
            source = "web" if j.user_id == 0 else "telegram"
            results.append({
                "id": j.id,
                "creator": creator_name,
                "source": source,
                "user_id": j.user_id,
                "channel_id": j.target_channel_id,
                "topic": j.topic,
                "title": j.title or "Chưa có tiêu đề",
                "script": (j.script or "")[:200],
                "status": j.status,
                "progress_percent": getattr(j, "progress_percent", 0) or 0,
                "progress_message": getattr(j, "progress_message", "") or "",
                "error_message": j.error_message or "",
                "scene_count": scene_count,
                "audio_duration_ms": j.audio_duration_ms or 0,
                "drive_link": j.drive_link or "",
                "youtube_id": j.youtube_video_id or "",
                "video_path": j.video_path or "",
                "quality_scores": j.quality_scores or "",
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
            "progress_percent": getattr(job, "progress_percent", 0) or 0,
            "progress_message": getattr(job, "progress_message", "") or "",
            "error_message": job.error_message or "",
            "quality_scores": job.quality_scores or "",
            "scenes": scenes,
            "audio_duration_ms": job.audio_duration_ms or 0,
            "video_path": job.video_path or "",
            "drive_link": job.drive_link or "",
            "youtube_id": job.youtube_video_id or "",
            "created_at": job.created_at.strftime("%Y-%m-%d %H:%M") if job.created_at else "",
        }

class EditVideoPayload(BaseModel):
    action_type: str  # "replace_media", "edit_text", "change_voice", "remix_bgm"
    scene_id: Optional[int] = None
    new_text: Optional[str] = None
    new_media_query: Optional[str] = None
    new_voice_id: Optional[str] = None

@app.post("/api/videos/{job_id}/edit")
async def edit_video_endpoint(job_id: int, payload: EditVideoPayload, background_tasks: BackgroundTasks):
    """Endpoint chỉnh sửa vi mô cho video qua LangGraph."""
    from app.graph import GraphRunner, EditRequest
    req = EditRequest(
        action_type=payload.action_type,
        scene_id=payload.scene_id,
        new_text=payload.new_text,
        new_media_query=payload.new_media_query,
        new_voice_id=payload.new_voice_id
    )
    runner = GraphRunner(job_id=job_id)
    background_tasks.add_task(runner.execute_edit, req)
    return {"status": "ok", "message": f"Đang thực hiện chỉnh sửa '{payload.action_type}' cho Video #{job_id}"}

# ─── Delete Job ───────────────────────────────────────────────────────────────

@app.delete("/api/videos/{job_id}")
async def delete_video_job(job_id: int) -> Dict[str, str]:
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        db.delete(job)
    return {"message": f"Đã xoá job #{job_id}"}

# ─── Download Video ───────────────────────────────────────────────────────────

@app.get("/api/videos/{job_id}/download")
async def download_video(job_id: int):
    """Tải file video đã render về máy."""
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        if not job.video_path:
            raise HTTPException(status_code=404, detail="Video chưa được render")
        video_path = Path(job.video_path)
        if not video_path.exists():
            raise HTTPException(status_code=404, detail=f"File video không tồn tại: {job.video_path}")
        filename = f"video_{job_id}_{(job.title or 'untitled')[:30].replace(' ', '_')}.mp4"

    return FileResponse(
        path=str(video_path),
        media_type="video/mp4",
        filename=filename,
    )

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

# ─── Render Trigger (Web UI) ─────────────────────────────────────────────────

def _run_web_render(job_id: int):
    """
    Background task: chạy pipeline render cho 1 video job từ Web UI.
    [FIX #1] Bọc try/finally đảm bảo status = FAILED nếu pipeline crash.
    [FIX #5] Thêm tổng timeout 10 phút cho toàn bộ pipeline.
    """
    import asyncio

    async def _do_render():
        try:
            from app.services.pipeline import VideoPipeline
            pipeline = VideoPipeline(job_id=job_id)
            # [FIX #5] Tổng timeout 10 phút cho toàn pipeline
            await asyncio.wait_for(pipeline.execute(), timeout=600)
        except asyncio.TimeoutError:
            error_msg = "Pipeline render vượt quá thời gian cho phép (10 phút)"
            logger.error(f"[Web Render] Job #{job_id}: {error_msg}")
            _mark_job_failed(job_id, error_msg)
            raise
        except Exception as e:
            logger.error(f"[Web Render] Job #{job_id} FAILED: {e}", exc_info=True)
            # [FIX #1] Đảm bảo status = FAILED khi crash
            _mark_job_failed(job_id, str(e))
            raise

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_do_render())
    except Exception:
        pass  # Đã xử lý bên trong _do_render
    finally:
        loop.close()


def _mark_job_failed(job_id: int, error_msg: str):
    """Helper: đánh dấu job FAILED trong DB khi render crash."""
    try:
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if job and job.status == "RENDERING":
                job.status = "FAILED"
                job.error_message = error_msg[:500]
                job.progress_message = f"❌ Thất bại: {error_msg[:100]}"
    except Exception as ex:
        logger.error(f"Không thể đánh dấu job #{job_id} FAILED: {ex}")


@app.post("/api/videos/{job_id}/render")
async def trigger_render_from_web(job_id: int, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """
    Kích hoạt render video từ Web Dashboard.
    [FIX #6] Chống spam: kiểm tra không có job nào đang RENDERING trước khi cho phép.
    """
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        if not job.scenes_json:
            raise HTTPException(status_code=400, detail="Video chưa có kịch bản phân cảnh")
        if job.status == "RENDERING":
            raise HTTPException(status_code=409, detail="Video đang được render")

        # [FIX #6] Kiểm tra có video nào đang render không (tránh chạy song song)
        rendering_count = db.query(VideoJob).filter(VideoJob.status == "RENDERING").count()
        if rendering_count > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Đang có {rendering_count} video đang render. Vui lòng đợi hoàn tất trước khi render tiếp."
            )

        # Cập nhật trạng thái ngay để UI phản ánh
        job.status = "RENDERING"
        job.progress_percent = 0
        job.progress_message = "Đang khởi tạo pipeline render từ Web..."
        job.error_message = None

    background_tasks.add_task(_run_web_render, job_id)

    return {
        "success": True,
        "message": f"Video #{job_id} đã được đưa vào hàng đợi render",
        "job_id": job_id
    }


@app.post("/api/videos/recover-failed")
async def recover_all_failed_jobs() -> Dict[str, Any]:
    """
    Tự động khôi phục tất cả các video bị lỗi (FAILED) về trạng thái SCRIPT_READY,
    làm sạch thông báo lỗi và chuẩn bị sẵn sàng cho lượt render tiếp theo.
    """
    with get_db() as db:
        failed_jobs = db.query(VideoJob).filter(VideoJob.status == "FAILED").all()
        count = len(failed_jobs)
        for j in failed_jobs:
            j.status = "SCRIPT_READY"
            j.error_message = None
            j.progress_percent = 0
            j.progress_message = "Đã khôi phục về hàng đợi sẵn sàng render lại"
            log_activity(db, 0, "Web Studio", "RECOVER_FAILED", f"Khôi phục Video #{j.id} về SCRIPT_READY")

    return {
        "success": True,
        "recovered_count": count,
        "message": f"Đã khôi phục thành công {count} video bị lỗi về trạng thái sẵn sàng!"
    }

# ─── Health Check ─────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check() -> Dict[str, Any]:
    import sys
    from app.utils.ffmpeg_helper import is_ffmpeg_installed, get_ffmpeg_version
    keys = settings.get_gemini_keys_pool()
    web_only = "--web-only" in sys.argv

    # Kiểm tra Drive token thật sự
    drive_connected = False
    drive_quota_label = "Chưa kết nối"
    try:
        drive_token_path = BASE_DIR / "credentials" / "accounts" / "account_1" / "drive_token.json"
        if drive_token_path.exists():
            import json as _json
            with open(drive_token_path, "r", encoding="utf-8") as f:
                tok = _json.load(f)
            if tok.get("token") or tok.get("refresh_token"):
                drive_connected = True
                drive_quota_label = "20 TB Google One (Đã kết nối)"
    except Exception:
        pass

    return {
        "status": "online",
        "ffmpeg": get_ffmpeg_version() if is_ffmpeg_installed() else "not installed",
        "gemini_keys": len(keys),
        "telegram_token": bool(settings.TELEGRAM_BOT_TOKEN),
        "web_only": web_only,
        "drive_connected": drive_connected,
        "drive_quota_label": drive_quota_label,
    }


# ─── Upload to Google Drive ───────────────────────────────────────────────────

@app.post("/api/videos/{job_id}/upload-drive")
async def upload_to_drive(job_id: int, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """
    Đẩy video đã render lên Google Drive 20TB.
    Trả về link Drive ngay khi upload xong.
    """
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        if job.status not in ("RENDERED", "PUBLISHED"):
            raise HTTPException(status_code=400, detail=f"Video chưa render xong (status: {job.status})")
        if not job.video_path or not Path(job.video_path).exists():
            raise HTTPException(status_code=404, detail="File video không tồn tại trên máy")
        if job.drive_link:
            return {"success": True, "drive_link": job.drive_link, "message": "Video đã có sẵn trên Drive rồi!"}
        video_path = job.video_path
        video_title = job.title or job.topic or f"Video_{job_id}"

    # Upload đồng bộ (không chạy background để có link ngay)
    try:
        from app.agents.drive_agent import DriveAgent
        from app.database.models import get_db as _get_db
        agent = DriveAgent(account_name="account_1")
        if not agent.service:
            raise HTTPException(status_code=503, detail="Google Drive chưa kết nối. Chạy py auth_google.py drive để xác thực.")
        result = agent.upload_video(video_path, video_title)
        if result.get("status") == "success" and result.get("drive_link"):
            drive_link = result["drive_link"]
            with _get_db() as db:
                j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if j:
                    j.drive_link = drive_link
                    from app.database.models import log_activity
                    log_activity(db, 0, "WebStudio", "UPLOAD_DRIVE",
                                 f"Video #{job_id} đã tải lên Google Drive 20TB: {drive_link}")
            return {"success": True, "drive_link": drive_link, "message": "Tải lên Google Drive 20TB thành công!"}
        else:
            raise HTTPException(status_code=500, detail=result.get("error", "Upload thất bại"))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi upload Drive job #{job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# ─── Publish to YouTube ───────────────────────────────────────────────────────

class PublishYouTubeRequest(BaseModel):
    channel_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    privacy: str = "public"
    schedule_time_iso: Optional[str] = None

@app.post("/api/videos/{job_id}/publish-youtube")
async def publish_to_youtube(
    job_id: int,
    body: Optional[PublishYouTubeRequest] = None,
    privacy: str = "public"
) -> Dict[str, Any]:
    """
    Đăng video đã render lên YouTube qua PublisherAgent.
    Hỗ trợ người dùng chủ động chọn kênh YouTube, tinh chỉnh tiêu đề, mô tả và chế độ hiển thị (privacy).
    """
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        if job.status not in ("RENDERED", "PUBLISHED"):
            raise HTTPException(status_code=400, detail=f"Video chưa render xong (status: {job.status})")
        if not job.video_path or not Path(job.video_path).exists():
            raise HTTPException(status_code=404, detail="File video không tồn tại trên máy")
        
        # Cập nhật thông tin nếu người dùng tùy chỉnh trước khi xuất bản
        target_channel = (body.channel_id if body and body.channel_id else None) or job.target_channel_id or "default"
        chosen_title = (body.title.strip() if body and body.title and body.title.strip() else None) or job.title or job.topic or f"Video_{job_id}"
        chosen_desc = (body.description if body and body.description is not None else None) or job.description or ""
        chosen_tags_str = (body.tags if body and body.tags is not None else None) or job.tags or ""
        chosen_privacy = (body.privacy if body and body.privacy else None) or privacy or "public"
        schedule_time = body.schedule_time_iso if body else None

        # Đồng bộ lại job DB
        job.target_channel_id = target_channel
        job.title = chosen_title
        job.description = chosen_desc
        job.tags = chosen_tags_str

        video_path = job.video_path
        video_tags = [t.strip() for t in chosen_tags_str.split(",") if t.strip()]

    try:
        from app.agents.publisher_agent import PublisherAgent
        from app.database.models import get_db as _get_db, log_activity

        cfg = _read_channels_yaml()
        channels = cfg.get("channels", [])
        ch_cfg = next((c for c in channels if c.get("id") == target_channel), {"id": target_channel, "name": target_channel})
        ch_lang = ch_cfg.get("language", "en")
        acc_id = ch_cfg.get("google_account", "account_1")

        # Gán token path chính xác theo tài khoản Google mẹ của kênh
        token_file = f"credentials/accounts/{acc_id}/youtube_token.json"
        ch_cfg["token_file"] = token_file

        pub_agent = PublisherAgent(channel_config=ch_cfg, account_name=acc_id)
        if not pub_agent.service:
            ch_name = ch_cfg.get("name", target_channel)
            raise HTTPException(
                status_code=503,
                detail=f"Kênh '{ch_name}' (tài khoản '{acc_id}') chưa kết nối YouTube OAuth. Vui lòng vào tab 'Kênh & Tài Khoản' bấm 'Đăng Nhập Google (YouTube)' để cấp quyền."
            )

        pub_res = pub_agent.upload_video(
            video_path=video_path,
            title=chosen_title,
            description=chosen_desc,
            tags=video_tags,
            privacy_status=chosen_privacy,
            schedule_time_iso=schedule_time,
            default_language=ch_lang,
            default_audio_language=ch_lang,
        )

        if pub_res.get("status") == "success":
            yt_url = pub_res.get("youtube_url")
            yt_id = pub_res.get("video_id")
            ch_name = ch_cfg.get("name", target_channel)
            with _get_db() as db:
                j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if j:
                    j.status = "PUBLISHED"
                    j.youtube_video_id = yt_id
                    log_activity(db, 0, "WebStudio", "PUBLISH_YOUTUBE",
                                 f"Video #{job_id} đã đăng lên kênh '{ch_name}': {yt_url}",
                                 channel_id=target_channel)
            return {
                "success": True,
                "video_id": yt_id,
                "youtube_url": yt_url,
                "channel_id": target_channel,
                "channel_name": ch_name,
                "message": f"Đã đăng lên kênh '{ch_name}' thành công! Link: {yt_url}"
            }
        else:
            raise HTTPException(status_code=500, detail=pub_res.get("error", "Đăng YouTube thất bại"))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi đăng YouTube job #{job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# ─── Token Health Monitor (Part 4) ───────────────────────────────────────────

def _check_gemini_key(key: str) -> Dict[str, Any]:
    """Kiểm tra 1 Gemini API Key bằng cách gọi list models (lightweight, 0 quota cost)."""
    prefix = key[:8] + "..." + key[-4:] if len(key) > 12 else key[:4] + "..."
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        r = http_requests.get(url, timeout=8)
        if r.status_code == 200:
            models = r.json().get("models", [])
            return {"prefix": prefix, "status": "ok", "models_count": len(models)}
        elif r.status_code == 429:
            return {"prefix": prefix, "status": "quota_exceeded", "error": "429 - Hết quota"}
        elif r.status_code in (400, 403):
            err_msg = ""
            try:
                err_msg = r.json().get("error", {}).get("message", "")[:80]
            except Exception:
                err_msg = r.text[:80]
            return {"prefix": prefix, "status": "invalid", "error": f"{r.status_code} - {err_msg}"}
        else:
            return {"prefix": prefix, "status": "error", "error": f"HTTP {r.status_code}"}
    except http_requests.Timeout:
        return {"prefix": prefix, "status": "timeout", "error": "Timeout (8s)"}
    except Exception as e:
        return {"prefix": prefix, "status": "error", "error": str(e)[:80]}


def _check_pexels_key(key: str) -> Dict[str, Any]:
    """Kiểm tra 1 Pexels API Key bằng cách tìm kiếm nhẹ, đọc quota từ response header."""
    prefix = key[:8] + "..." + key[-4:] if len(key) > 12 else key[:4] + "..."
    if not key or key == "your_pexels_api_key_here":
        return {"prefix": prefix, "status": "not_configured", "error": "Key chưa cấu hình"}
    try:
        headers = {"Authorization": key}
        r = http_requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": "nature", "per_page": 1},
            headers=headers,
            timeout=8
        )
        if r.status_code == 200:
            remaining = r.headers.get("X-Ratelimit-Remaining", "?")
            limit = r.headers.get("X-Ratelimit-Limit", "?")
            reset = r.headers.get("X-Ratelimit-Reset", "?")
            return {
                "prefix": prefix,
                "status": "ok",
                "remaining": int(remaining) if str(remaining).isdigit() else remaining,
                "limit": int(limit) if str(limit).isdigit() else limit,
                "reset_epoch": reset,
            }
        elif r.status_code == 401:
            return {"prefix": prefix, "status": "invalid", "error": "401 - Key không hợp lệ"}
        elif r.status_code == 429:
            return {"prefix": prefix, "status": "quota_exceeded", "error": "429 - Hết quota"}
        else:
            return {"prefix": prefix, "status": "error", "error": f"HTTP {r.status_code}"}
    except http_requests.Timeout:
        return {"prefix": prefix, "status": "timeout", "error": "Timeout (8s)"}
    except Exception as e:
        return {"prefix": prefix, "status": "error", "error": str(e)[:80]}


@app.get("/api/token-health")
async def get_token_health() -> Dict[str, Any]:
    """
    Kiểm tra sức khỏe tất cả API Keys (Gemini + Pexels).
    Gọi lightweight probe cho từng key, trả về % khả dụng.
    """
    import concurrent.futures

    gemini_keys = settings.get_gemini_keys_pool()
    pexels_keys = settings.get_pexels_keys_pool()

    # Kiểm tra song song để nhanh hơn
    gemini_results = []
    pexels_results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        gemini_futures = {
            executor.submit(_check_gemini_key, k): i
            for i, k in enumerate(gemini_keys)
        }
        pexels_futures = {
            executor.submit(_check_pexels_key, k): i
            for i, k in enumerate(pexels_keys)
        }

        for future in concurrent.futures.as_completed(gemini_futures):
            idx = gemini_futures[future]
            result = future.result()
            result["index"] = idx + 1
            gemini_results.append(result)

        for future in concurrent.futures.as_completed(pexels_futures):
            idx = pexels_futures[future]
            result = future.result()
            result["index"] = idx + 1
            pexels_results.append(result)

    # Sort by index
    gemini_results.sort(key=lambda x: x["index"])
    pexels_results.sort(key=lambda x: x["index"])

    gemini_valid = sum(1 for r in gemini_results if r["status"] == "ok")
    pexels_valid = sum(1 for r in pexels_results if r["status"] == "ok")

    gemini_total = max(len(gemini_results), 1)
    pexels_total = max(len(pexels_results), 1)

    return {
        "gemini": {
            "total": len(gemini_results),
            "valid": gemini_valid,
            "invalid": len(gemini_results) - gemini_valid,
            "percent": round(gemini_valid / gemini_total * 100),
            "keys": gemini_results,
        },
        "pexels": {
            "total": len(pexels_results),
            "valid": pexels_valid,
            "invalid": len(pexels_results) - pexels_valid,
            "percent": round(pexels_valid / pexels_total * 100) if pexels_results else 0,
            "keys": pexels_results,
        },
    }


# ═════════════════════════════════════════════════════════════════════════════
# ─── GOOGLE ACCOUNTS & MULTI-CHANNEL STUDIO API ──────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════

_oauth_sessions: Dict[str, Dict[str, Any]] = {}

def _read_channels_yaml() -> Dict[str, Any]:
    yaml_path = settings.CHANNELS_CONFIG_FILE
    if not yaml_path.exists():
        example_path = yaml_path.with_name("channels.example.yaml")
        if example_path.exists():
            try:
                import shutil
                shutil.copyfile(example_path, yaml_path)
            except Exception:
                pass
        else:
            return {"google_accounts": [], "channels": []}
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.error(f"Lỗi đọc channels.yaml: {e}")
        return {"google_accounts": [], "channels": []}

def _write_channels_yaml(data: Dict[str, Any]) -> bool:
    yaml_path = settings.CHANNELS_CONFIG_FILE
    try:
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False, indent=2)
        settings._cached_channels = None  # Reset in-memory cache
        return True
    except Exception as e:
        logger.error(f"Lỗi ghi channels.yaml: {e}")
        return False

def _check_account_tokens(account_id: str) -> Dict[str, Any]:
    base_dir = Path(settings.BASE_DIR)
    acc_dir = base_dir / "credentials" / "accounts" / account_id

    client_secret_path = acc_dir / "client_secret.json"
    has_client_secret = client_secret_path.exists() and client_secret_path.stat().st_size > 10

    # Fallback nếu account_1 có secret thì các account khác có thể dùng chung
    if not has_client_secret:
        fallback = base_dir / "credentials" / "accounts" / "account_1" / "client_secret.json"
        if fallback.exists() and fallback.stat().st_size > 10:
            has_client_secret = True

    yt_token_path = acc_dir / "youtube_token.json"
    youtube_connected = False
    if yt_token_path.exists():
        try:
            with open(yt_token_path, "r", encoding="utf-8") as f:
                tok = json.load(f)
                if tok.get("token") or tok.get("refresh_token"):
                    youtube_connected = True
        except Exception:
            pass

    drive_token_path = acc_dir / "drive_token.json"
    drive_connected = False
    if drive_token_path.exists():
        try:
            with open(drive_token_path, "r", encoding="utf-8") as f:
                tok = json.load(f)
                if tok.get("token") or tok.get("refresh_token"):
                    drive_connected = True
        except Exception:
            pass

    return {
        "has_client_secret": has_client_secret,
        "youtube_connected": youtube_connected,
        "drive_connected": drive_connected
    }

class AccountCreateRequest(BaseModel):
    id: str
    email_label: str
    drive_storage: Optional[str] = "2 TB Google One"
    youtube_quota_daily: Optional[int] = 10000

class AccountUpdateRequest(BaseModel):
    email_label: Optional[str] = None
    drive_storage: Optional[str] = None
    youtube_quota_daily: Optional[int] = None

class ChannelCreateRequest(BaseModel):
    id: str
    name: str
    niche: Optional[str] = "general"
    language: Optional[str] = "en"
    preferred_voice: Optional[str] = "en-US-ChristopherNeural"
    visual_mode: Optional[str] = "imagen_ai"
    publish_hour_utc: Optional[int] = 12
    days: Optional[List[str]] = ["monday", "wednesday", "friday", "sunday"]
    google_account: Optional[str] = None

class AuthStartRequest(BaseModel):
    service: str = "youtube"  # "youtube" hoặc "drive"

class UploadSecretRequest(BaseModel):
    content: str

@app.get("/api/accounts")
async def get_accounts_and_channels() -> Dict[str, Any]:
    """Lấy danh sách tất cả tài khoản Google và các kênh YouTube con kèm trạng thái token."""
    cfg = _read_channels_yaml()
    raw_accounts = cfg.get("google_accounts", [])

    enriched_accounts = []
    for acc in raw_accounts:
        acc_id = acc.get("id", "")
        token_status = _check_account_tokens(acc_id)
        brand_channels = acc.get("brand_channels", [])

        enriched_accounts.append({
            "id": acc_id,
            "email_label": acc.get("email_label", ""),
            "drive_storage": acc.get("drive_storage", "2 TB Google One"),
            "youtube_quota_daily": acc.get("youtube_quota_daily", 10000),
            "has_client_secret": token_status["has_client_secret"],
            "youtube_connected": token_status["youtube_connected"],
            "drive_connected": token_status["drive_connected"],
            "brand_channels": brand_channels,
            "channels_count": len(brand_channels)
        })

    return {
        "accounts": enriched_accounts,
        "total_accounts": len(enriched_accounts),
        "total_channels": sum(a["channels_count"] for a in enriched_accounts)
    }

@app.post("/api/accounts")
async def create_google_account(req: AccountCreateRequest) -> Dict[str, Any]:
    """Tạo một tài khoản Google mới trong hệ thống."""
    clean_id = req.id.strip().lower().replace(" ", "_")
    if not clean_id:
        raise HTTPException(status_code=400, detail="Mã tài khoản không hợp lệ")

    cfg = _read_channels_yaml()
    accounts = cfg.setdefault("google_accounts", [])

    if any(a.get("id") == clean_id for a in accounts):
        raise HTTPException(status_code=400, detail=f"Tài khoản '{clean_id}' đã tồn tại!")

    acc_dir = Path(settings.BASE_DIR) / "credentials" / "accounts" / clean_id
    acc_dir.mkdir(parents=True, exist_ok=True)

    new_account = {
        "id": clean_id,
        "email_label": req.email_label.strip(),
        "drive_storage": req.drive_storage or "2 TB Google One",
        "youtube_quota_daily": req.youtube_quota_daily or 10000,
        "credentials": {
            "client_secret": f"credentials/accounts/{clean_id}/client_secret.json",
            "youtube_token": f"credentials/accounts/{clean_id}/youtube_token.json",
            "drive_token": f"credentials/accounts/{clean_id}/drive_token.json"
        },
        "brand_channels": []
    }

    accounts.append(new_account)
    _write_channels_yaml(cfg)

    return {
        "success": True,
        "message": f"Đã tạo tài khoản Google '{clean_id}' thành công!",
        "account": new_account
    }

@app.put("/api/accounts/{account_id}")
async def update_google_account(account_id: str, req: AccountUpdateRequest) -> Dict[str, Any]:
    """Cập nhật thông tin tài khoản Google (Gmail, Quota, Storage)."""
    cfg = _read_channels_yaml()
    accounts = cfg.get("google_accounts", [])
    account = next((a for a in accounts if a.get("id") == account_id), None)
    if not account:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")

    if req.email_label is not None:
        account["email_label"] = req.email_label.strip()
    if req.drive_storage is not None:
        account["drive_storage"] = req.drive_storage.strip()
    if req.youtube_quota_daily is not None:
        account["youtube_quota_daily"] = req.youtube_quota_daily

    _write_channels_yaml(cfg)
    return {
        "success": True,
        "message": f"Đã cập nhật thông tin tài khoản '{account_id}' thành công!",
        "account": account
    }

@app.delete("/api/accounts/{account_id}")
async def delete_google_account(account_id: str) -> Dict[str, Any]:
    """Xóa tài khoản Google khỏi hệ thống."""
    cfg = _read_channels_yaml()
    accounts = cfg.get("google_accounts", [])

    target = next((a for a in accounts if a.get("id") == account_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")

    cfg["google_accounts"] = [a for a in accounts if a.get("id") != account_id]
    flat_channels = cfg.get("channels", [])
    cfg["channels"] = [c for c in flat_channels if c.get("google_account") != account_id]

    _write_channels_yaml(cfg)
    return {
        "success": True,
        "message": f"Đã xóa tài khoản '{account_id}' khỏi hệ thống"
    }

@app.post("/api/accounts/{account_id}/disconnect")
async def disconnect_account_service(account_id: str, req: AuthStartRequest) -> Dict[str, Any]:
    """Ngắt kết nối YouTube hoặc Drive token của tài khoản."""
    base_dir = Path(settings.BASE_DIR)
    acc_dir = base_dir / "credentials" / "accounts" / account_id

    service = req.service.lower()
    files_to_remove = []
    if service in ("youtube", "all"):
        files_to_remove.extend([acc_dir / "youtube_token.json", acc_dir / "youtube_token_base64.txt"])
    if service in ("drive", "all"):
        files_to_remove.extend([acc_dir / "drive_token.json", acc_dir / "drive_token_base64.txt"])

    for f in files_to_remove:
        if f.exists():
            try:
                f.unlink()
            except Exception as ex:
                logger.warning(f"Không thể xóa file {f}: {ex}")

    return {
        "success": True,
        "message": f"Đã ngắt kết nối {service.upper()} cho tài khoản '{account_id}'"
    }

@app.post("/api/accounts/{account_id}/upload-secret")
async def upload_client_secret(account_id: str, req: UploadSecretRequest) -> Dict[str, Any]:
    """Nạp file client_secret.json cho tài khoản."""
    try:
        secret_data = json.loads(req.content)
        if "installed" not in secret_data and "web" not in secret_data:
            raise ValueError("File client_secret.json không đúng định dạng của Google")
    except Exception as ex:
        raise HTTPException(status_code=400, detail=f"Nội dung file JSON không hợp lệ: {ex}")

    acc_dir = Path(settings.BASE_DIR) / "credentials" / "accounts" / account_id.strip()
    acc_dir.mkdir(parents=True, exist_ok=True)
    secret_path = acc_dir / "client_secret.json"

    with open(secret_path, "w", encoding="utf-8") as f:
        json.dump(secret_data, f, indent=2)

    return {
        "success": True,
        "message": f"Đã lưu file client_secret.json cho tài khoản '{account_id}' thành công!"
    }

@app.post("/api/accounts/{account_id}/auth-start")
async def start_google_oauth(account_id: str, req: AuthStartRequest) -> Dict[str, Any]:
    """Khởi chạy luồng xác thực Google OAuth trong luồng nền và tự động mở trình duyệt."""
    service = req.service.lower()
    session_key = f"{account_id}:{service}"

    _oauth_sessions[session_key] = {
        "status": "in_progress",
        "account_id": account_id,
        "service": service,
        "message": f"Đang mở trình duyệt để xác thực {service.upper()} cho {account_id}...",
        "error": None
    }

    def _oauth_worker():
        try:
            import webbrowser
            import wsgiref.simple_server
            import wsgiref.util
            from google_auth_oauthlib.flow import InstalledAppFlow

            scopes = [
                "https://www.googleapis.com/auth/youtube",
                "https://www.googleapis.com/auth/userinfo.email",
                "openid"
            ] if service == "youtube" else [
                "https://www.googleapis.com/auth/drive.file",
                "https://www.googleapis.com/auth/userinfo.email",
                "openid"
            ]
            token_filename = "youtube_token.json" if service == "youtube" else "drive_token.json"

            base_dir = Path(settings.BASE_DIR)
            acc_dir = base_dir / "credentials" / "accounts" / account_id
            acc_dir.mkdir(parents=True, exist_ok=True)

            secret_file = acc_dir / "client_secret.json"
            if not secret_file.exists():
                fallback = base_dir / "credentials" / "accounts" / "account_1" / "client_secret.json"
                if fallback.exists():
                    secret_file = fallback
                else:
                    _oauth_sessions[session_key] = {
                        "status": "error",
                        "error": "Chưa có file client_secret.json! Vui lòng nạp file trước."
                    }
                    return

            flow = InstalledAppFlow.from_client_secrets_file(str(secret_file), scopes=scopes)

            # ── Tạo HTTP server cục bộ để nhận callback từ Google ──
            received_uri = {"value": None}
            success_page = (
                "<html><body style='font-family:system-ui,sans-serif;text-align:center;padding:60px;"
                "background:#1a1a2e;color:#e0e0e0'>"
                "<h2 style='color:#34d399'>✅ Xác thực Google thành công!</h2>"
                "<p>Bạn có thể đóng tab này và quay lại Web Studio.</p>"
                "</body></html>"
            )

            def _wsgi_app(environ, start_response):
                received_uri["value"] = wsgiref.util.request_uri(environ)
                start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
                return [success_page.encode("utf-8")]

            # Tắt log mặc định của wsgiref (tránh spam console)
            class _SilentHandler(wsgiref.simple_server.WSGIRequestHandler):
                def log_message(self, fmt, *args):
                    pass

            local_server = wsgiref.simple_server.make_server(
                "localhost", 0, _wsgi_app, handler_class=_SilentHandler
            )
            port = local_server.server_port

            # Cấu hình redirect URI trỏ về server cục bộ vừa tạo
            flow.redirect_uri = f"http://localhost:{port}/"
            auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

            # Lưu URL xác thực để frontend hiển thị trên modal (giải quyết lỗi spinner xoay mãi)
            _oauth_sessions[session_key].update({
                "auth_url": auth_url,
                "message": "Link xác thực đã sẵn sàng. Hãy đăng nhập Google trên trình duyệt."
            })
            logger.info(f"OAuth URL created for {session_key} on port {port}")

            # Tự động mở trình duyệt (nếu được)
            try:
                webbrowser.open(auth_url)
            except Exception:
                logger.info("Không thể tự mở trình duyệt, user cần bấm link thủ công trên modal.")

            # Chờ callback từ Google với timeout 3 phút
            local_server.timeout = 180
            local_server.handle_request()
            local_server.server_close()

            if not received_uri["value"]:
                _oauth_sessions[session_key] = {
                    "status": "error",
                    "error": "Hết thời gian chờ xác thực (3 phút). Vui lòng thử lại."
                }
                return

            # Trao đổi authorization code lấy token
            # Note: oauthlib bắt buộc https scheme đối với URL phản hồi
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
            auth_response = received_uri["value"]
            if auth_response.startswith("http:"):
                auth_response = auth_response.replace("http:", "https:", 1)
            flow.fetch_token(authorization_response=auth_response)
            creds = flow.credentials

            # Lưu token file
            token_data = creds.to_json()
            token_file = acc_dir / token_filename
            with open(token_file, "w", encoding="utf-8") as f:
                f.write(token_data)

            # Lưu thêm file Base64 cho Render deployment
            b64_str = base64.b64encode(token_data.encode("utf-8")).decode("utf-8")
            b64_file = acc_dir / f"{Path(token_filename).stem}_base64.txt"
            with open(b64_file, "w", encoding="utf-8") as f:
                f.write(b64_str)

            # Tự động nhận diện email thật của tài khoản Google vừa đăng nhập
            detected_email = None
            try:
                import urllib.request
                u_req = urllib.request.Request(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {creds.token}"}
                )
                with urllib.request.urlopen(u_req, timeout=10) as resp:
                    uinfo = json.loads(resp.read().decode())
                    detected_email = uinfo.get("email")
                if detected_email:
                    cfg = _read_channels_yaml()
                    for acc in cfg.get("google_accounts", []):
                        if acc.get("id") == account_id:
                            acc["email_label"] = detected_email
                            break
                    _write_channels_yaml(cfg)
                    logger.info(f"Đã tự động nhận diện Gmail cho {account_id}: {detected_email}")
            except Exception as ex:
                logger.info(f"Không thể tự động đọc Google UserInfo: {ex}")

            succ_msg = f"Đã kết nối {service.upper()} thành công!"
            if detected_email:
                succ_msg += f" (Gmail: {detected_email})"

            _oauth_sessions[session_key] = {
                "status": "success",
                "account_id": account_id,
                "service": service,
                "message": succ_msg,
                "email": detected_email
            }
        except Exception as ex:
            logger.error(f"OAuth Worker Error: {ex}")
            _oauth_sessions[session_key] = {
                "status": "error",
                "error": str(ex)
            }

    t = threading.Thread(target=_oauth_worker, daemon=True)
    t.start()

    return {
        "success": True,
        "session_key": session_key,
        "status": "in_progress",
        "message": "Trình duyệt Google đang được mở để cấp quyền. Hãy bấm 'Cho Phép' trên trình duyệt."
    }

@app.get("/api/accounts/{account_id}/auth-status")
async def get_google_oauth_status(account_id: str, service: str = "youtube") -> Dict[str, Any]:
    """Kiểm tra tiến độ xác thực Google OAuth."""
    session_key = f"{account_id}:{service.lower()}"
    status_info = _oauth_sessions.get(session_key, {"status": "idle", "message": "Chưa có phiên xác thực nào"})
    return status_info

# ─── Brand Channels CRUD Endpoints ───────────────────────────────────────────

@app.post("/api/accounts/{account_id}/channels")
async def add_brand_channel(account_id: str, req: ChannelCreateRequest) -> Dict[str, Any]:
    """Thêm một kênh thương hiệu mới trực thuộc tài khoản Google."""
    clean_id = req.id.strip().lower().replace(" ", "_")
    if not clean_id:
        raise HTTPException(status_code=400, detail="Mã kênh không hợp lệ")

    cfg = _read_channels_yaml()
    accounts = cfg.get("google_accounts", [])
    account = next((a for a in accounts if a.get("id") == account_id), None)
    if not account:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản mẹ")

    brand_channels = account.setdefault("brand_channels", [])
    if any(c.get("id") == clean_id for c in brand_channels):
        raise HTTPException(status_code=400, detail=f"Kênh '{clean_id}' đã tồn tại trong tài khoản này!")

    new_channel = {
        "id": clean_id,
        "name": req.name.strip(),
        "niche": req.niche.strip(),
        "language": req.language.strip(),
        "visual_mode": req.visual_mode or "imagen_ai",
        "preferred_voice": req.preferred_voice or "en-US-ChristopherNeural",
        "schedule": {
            "publish_hour_utc": req.publish_hour_utc or 12,
            "days": req.days or ["monday", "wednesday", "friday", "sunday"]
        }
    }

    brand_channels.append(new_channel)

    flat_channels = cfg.setdefault("channels", [])
    flat_entry = next((c for c in flat_channels if c.get("id") == clean_id), None)
    if not flat_entry:
        flat_channels.append({
            "id": clean_id,
            "name": new_channel["name"],
            "niche": new_channel["niche"],
            "language": new_channel["language"],
            "google_account": account_id,
            "visual_mode": new_channel["visual_mode"],
            "preferred_voice": new_channel["preferred_voice"]
        })

    _write_channels_yaml(cfg)

    return {
        "success": True,
        "message": f"Đã thêm kênh '{new_channel['name']}' thành công!",
        "channel": new_channel
    }

@app.put("/api/accounts/{account_id}/channels/{channel_id}")
async def update_brand_channel(account_id: str, channel_id: str, req: ChannelCreateRequest) -> Dict[str, Any]:
    """Chỉnh sửa thông tin kênh thương hiệu."""
    cfg = _read_channels_yaml()
    accounts = cfg.get("google_accounts", [])
    account = next((a for a in accounts if a.get("id") == account_id), None)
    if not account:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản mẹ")

    brand_channels = account.get("brand_channels", [])
    channel = next((c for c in brand_channels if c.get("id") == channel_id), None)
    if not channel:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh cần sửa")

    channel["name"] = req.name.strip()
    channel["niche"] = req.niche.strip()
    channel["language"] = req.language.strip()
    channel["visual_mode"] = req.visual_mode or channel.get("visual_mode", "imagen_ai")
    channel["preferred_voice"] = req.preferred_voice or channel.get("preferred_voice", "en-US-ChristopherNeural")
    channel["schedule"] = {
        "publish_hour_utc": req.publish_hour_utc or 12,
        "days": req.days or ["monday", "wednesday", "friday", "sunday"]
    }

    target_account_id = req.google_account or account_id
    if target_account_id != account_id:
        target_account = next((a for a in accounts if a.get("id") == target_account_id), None)
        if target_account:
            # Di chuyển channel từ tài khoản cũ sang tài khoản mới
            account["brand_channels"] = [c for c in brand_channels if c.get("id") != channel_id]
            target_account.setdefault("brand_channels", []).append(channel)

    flat_channels = cfg.get("channels", [])
    for fc in flat_channels:
        if fc.get("id") == channel_id:
            fc["name"] = channel["name"]
            fc["niche"] = channel["niche"]
            fc["language"] = channel["language"]
            fc["preferred_voice"] = channel["preferred_voice"]
            fc["visual_mode"] = channel["visual_mode"]
            fc["google_account"] = target_account_id

    _write_channels_yaml(cfg)

    return {
        "success": True,
        "message": f"Đã cập nhật kênh '{channel['name']}' thành công!",
        "channel": channel
    }

@app.delete("/api/accounts/{account_id}/channels/{channel_id}")
async def delete_brand_channel(account_id: str, channel_id: str) -> Dict[str, Any]:
    """Xóa một kênh khỏi tài khoản."""
    cfg = _read_channels_yaml()
    accounts = cfg.get("google_accounts", [])
    account = next((a for a in accounts if a.get("id") == account_id), None)
    if not account:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")

    account["brand_channels"] = [c for c in account.get("brand_channels", []) if c.get("id") != channel_id]
    cfg["channels"] = [c for c in cfg.get("channels", []) if c.get("id") != channel_id]

    _write_channels_yaml(cfg)
    return {
        "success": True,
        "message": f"Đã xóa kênh '{channel_id}' thành công!"
    }


# ==============================================================================
# 11. YOUTUBE LIVE ANALYTICS & AI GROWTH FORECAST
# ==============================================================================
from app.services.youtube_analytics import fetch_channel_live_stats, calculate_ai_forecast

class ForecastRequest(BaseModel):
    channel_id: Optional[str] = "channel_maya"
    daily_uploads: Optional[float] = 1.0
    custom_avg_views: Optional[int] = None

@app.get("/api/analytics/channels")
async def get_analytics_channels():
    """Lấy danh sách các kênh có hỗ trợ quét số liệu phân tích."""
    cfg = _read_channels_yaml()
    channels = cfg.get("channels", [])
    result = []
    for c in channels:
        result.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "handle": c.get("handle") or "",
            "channel_url": c.get("channel_url") or "",
            "niche": c.get("niche", "general"),
            "google_account": c.get("google_account", "account_1")
        })
    return {"channels": result}

@app.get("/api/analytics/channel/{channel_id}")
async def get_channel_live_analytics(channel_id: str):
    """Cào trực tiếp số liệu thời gian thực và tính toán dự đoán tăng trưởng cho kênh."""
    cfg = _read_channels_yaml()
    channels = cfg.get("channels", [])
    channel = next((c for c in channels if c.get("id") == channel_id), None)
    if not channel and channels:
        channel = channels[0]
    elif not channel:
        raise HTTPException(status_code=404, detail="Chưa có cấu hình kênh trong hệ thống")

    url = channel.get("channel_url") or channel.get("handle")
    if not url:
        raise HTTPException(status_code=400, detail="Kênh này chưa được cấu hình đường dẫn YouTube hoặc handle (@tênkênh)")

    stats = fetch_channel_live_stats(url)
    if not stats.get("success"):
        raise HTTPException(status_code=500, detail=stats.get("error", "Không thể lấy số liệu kênh"))

    forecast = calculate_ai_forecast(
        total_views=stats.get("total_views", 0),
        total_videos=len(stats.get("videos", [])),
        avg_views=stats.get("avg_views", 300),
        daily_uploads=1.0,
        niche=channel.get("niche", "history_mystery")
    )

    stats["forecast"] = forecast
    stats["channel_id"] = channel_id
    stats["channel_niche"] = channel.get("niche", "history_mystery")
    return stats

@app.post("/api/analytics/forecast")
async def calculate_forecast_endpoint(req: ForecastRequest):
    """Tính toán dự đoán tăng trưởng tùy biến theo tốc độ xuất bản và mức views mục tiêu."""
    cfg = _read_channels_yaml()
    channels = cfg.get("channels", [])
    channel = next((c for c in channels if c.get("id") == req.channel_id), {})
    url = channel.get("channel_url") or channel.get("handle")
    if not url and channels:
        url = channels[0].get("channel_url") or channels[0].get("handle")

    stats = fetch_channel_live_stats(url) if url else {}
    total_views = stats.get("total_views", 500) if stats.get("success") else 500
    total_vids = len(stats.get("videos", [])) if stats.get("success") else 2
    avg_views = req.custom_avg_views or (stats.get("avg_views", 300) if stats.get("success") else 300)

    forecast = calculate_ai_forecast(
        total_views=total_views,
        total_videos=total_vids,
        avg_views=avg_views,
        daily_uploads=req.daily_uploads or 1.0,
        niche=channel.get("niche", "history_mystery")
    )
    return {
        "success": True,
        "forecast": forecast
    }

