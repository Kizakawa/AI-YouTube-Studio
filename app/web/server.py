import os
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
from app.database.models import init_db, get_db, User, VideoJob, ActivityLog

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

    background_tasks.add_task(_run_web_render, job_id)

    return {
        "success": True,
        "message": f"Video #{job_id} đã được đưa vào hàng đợi render",
        "job_id": job_id
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
