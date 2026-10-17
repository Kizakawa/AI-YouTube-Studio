"""
app.web.agent_api — Router API chuyên dụng dành riêng cho AI Agent (Antigravity).
Cung cấp quyền truy cập toàn diện, thống kê siêu tốc, giám sát và điều khiển Web Studio từ xa
mà không cần thông qua xác thực thủ công của người dùng trên Telegram.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from fastapi import APIRouter, HTTPException, Request, Depends, Query, Body
from pydantic import BaseModel

from config.settings import settings
from app.utils.key_rotator import GEMINI_PRIORITY_MODELS
from app.database.models import get_db, VideoJob, ActivityLog, User, log_activity
from app.services.device_auth import is_local_ip

logger = logging.getLogger(__name__)


def verify_agent_auth(request: Request):
    """
    Xác thực quyền hạn của AI Agent:
    - Chấp nhận header 'X-Agent-Key' hoặc 'X-Agent-Secret'
    - Chấp nhận 'Authorization: Bearer <key>'
    - Chấp nhận query parameter '?agent_key=<key>'
    - Tự động cho phép localhost (127.0.0.1) gọi nội bộ
    """
    client_ip = request.client.host if request.client else "127.0.0.1"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()

    if is_local_ip(client_ip):
        return True

    key = (
        request.headers.get("X-Agent-Key")
        or request.headers.get("X-Agent-Secret")
        or request.query_params.get("agent_key")
    )

    auth_hdr = request.headers.get("Authorization", "")
    if auth_hdr.startswith("Bearer "):
        key = auth_hdr[7:].strip()

    expected_key = getattr(settings, "AGENT_API_KEY", "agy_agent_supersecret_key_2026")
    if key and key == expected_key:
        return True

    raise HTTPException(
        status_code=403,
        detail="Quyền truy cập AI Agent bị từ chối! Vui lòng cung cấp khóa bí mật 'X-Agent-Key'."
    )


# ─── 1. BẢNG THỐNG KÊ TOÀN DIỆN HỆ THỐNG ────────────────────────────────────

agent_router = APIRouter(prefix="/api/agent", tags=["AI Agent Super-Control"], dependencies=[Depends(verify_agent_auth)])

@agent_router.get("/health")
async def agent_health_check():
    """Kiểm tra kết nối và quyền hạn của Agent"""
    return {
        "status": "ok",
        "agent_access": "granted",
        "timestamp": datetime.utcnow().isoformat(),
        "environment": "online",
        "device_gatekeeper_bypassed": True
    }


@agent_router.get("/stats")
async def get_system_stats():
    """
    Trả về toàn bộ bức tranh trạng thái của hệ thống trong 1 API duy nhất:
    - Số lượng video theo từng trạng thái (SCRIPT_READY, RENDERING, PUBLISHED, FAILED,...)
    - Kênh đang hoạt động và danh sách cấu hình
    - Số lượng API Keys khả dụng
    """
    with get_db() as db:
        # Thống kê jobs
        all_jobs = db.query(VideoJob.status).all()
        status_counts = {}
        for (st,) in all_jobs:
            status_counts[st] = status_counts.get(st, 0) + 1

        total_jobs = len(all_jobs)
        published_count = status_counts.get("PUBLISHED", 0)
        rendered_count = status_counts.get("RENDERED", 0)
        rendering_count = status_counts.get("RENDERING", 0)
        failed_count = status_counts.get("FAILED", 0)
        script_ready_count = status_counts.get("SCRIPT_READY", 0)

        # Lấy user active channel
        user = db.query(User).filter(User.telegram_id == 0).first()
        active_channel_id = user.active_channel_id if user else "channel_1"

    # Lấy thông tin kênh
    channels_cfg = settings.get_channels_config()

    return {
        "success": True,
        "timestamp": datetime.utcnow().isoformat(),
        "summary": {
            "total_jobs": total_jobs,
            "published": published_count,
            "rendered": rendered_count,
            "rendering_or_pending": rendering_count,
            "script_ready": script_ready_count,
            "failed": failed_count,
            "status_breakdown": status_counts
        },
        "channels": {
            "active_channel_id": active_channel_id,
            "total_configured": len(channels_cfg),
            "list": [{"id": ch.get("id"), "name": ch.get("name"), "niche": ch.get("niche"), "lang": ch.get("language")} for ch in channels_cfg]
        },
        "gemini_api": {
            "total_keys_in_pool": len(settings.get_gemini_keys_pool()),
            "priority": list(GEMINI_PRIORITY_MODELS)
        }
    }


# ─── 2. QUẢN LÝ VÀ TRUY VẤN DANH SÁCH JOBS ──────────────────────────────────

@agent_router.get("/jobs")
async def list_jobs(
    status: Optional[str] = None,
    channel_id: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0)
):
    """Lấy danh sách các jobs kịch bản & video với bộ lọc nhanh"""
    with get_db() as db:
        query = db.query(VideoJob)
        if status:
            query = query.filter(VideoJob.status == status.upper())
        if channel_id:
            query = query.filter(VideoJob.target_channel_id == channel_id)

        total = query.count()
        jobs = query.order_by(VideoJob.id.desc()).offset(offset).limit(limit).all()

        results = []
        for j in jobs:
            results.append({
                "id": j.id,
                "title": j.title or j.topic,
                "topic": j.topic,
                "status": j.status,
                "channel_id": j.target_channel_id,
                "progress_percent": j.progress_percent,
                "progress_message": j.progress_message,
                "has_video": bool(j.video_path and os.path.exists(j.video_path)),
                "drive_link": j.drive_link,
                "youtube_video_id": j.youtube_video_id,
                "error_message": j.error_message,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "updated_at": j.updated_at.isoformat() if j.updated_at else None
            })

    return {
        "success": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "jobs": results
    }


@agent_router.get("/jobs/{job_id}")
async def get_job_detail(job_id: int):
    """Lấy chi tiết kịch bản, các phân cảnh, phụ đề và metadata của 1 job"""
    with get_db() as db:
        j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not j:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy VideoJob #{job_id}")

        scenes = []
        full_narration = j.script or ""
        if j.scenes_json:
            try:
                parsed = json.loads(j.scenes_json)
                if isinstance(parsed, dict):
                    scenes = parsed.get("scenes", [])
                    full_narration = parsed.get("full_narration", full_narration)
                elif isinstance(parsed, list):
                    scenes = parsed
            except Exception:
                pass

        return {
            "success": True,
            "job": {
                "id": j.id,
                "title": j.title or j.topic,
                "topic": j.topic,
                "status": j.status,
                "target_channel_id": j.target_channel_id,
                "full_narration": full_narration,
                "scenes_count": len(scenes),
                "scenes": scenes,
                "description": j.description,
                "tags": j.tags,
                "quality_scores": json.loads(j.quality_scores) if j.quality_scores else None,
                "progress_percent": j.progress_percent,
                "progress_message": j.progress_message,
                "error_message": j.error_message,
                "video_path": j.video_path,
                "video_file_exists": bool(j.video_path and os.path.exists(j.video_path)),
                "audio_path": j.audio_path,
                "subtitle_path": j.subtitle_path,
                "drive_link": j.drive_link,
                "youtube_video_id": j.youtube_video_id,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "updated_at": j.updated_at.isoformat() if j.updated_at else None
            }
        }


# ─── 3. TẠO & ĐIỀU KHIỂN TÁC VỤ (CREATE & ACTIONS) ─────────────────────────

class CreateJobRequest(BaseModel):
    topic: str
    channel_id: Optional[str] = None
    language: Optional[str] = None
    voice: Optional[str] = None
    custom_script: Optional[str] = None
    auto_push_drive: bool = False


@agent_router.post("/jobs/create")
async def create_new_job(req: CreateJobRequest):
    """
    AI Agent tự động tạo 1 VideoJob mới từ chủ đề (Topic).
    Hệ thống sẽ dùng ContentAgent viết kịch bản giật gân, phân cảnh 9:16 và lưu vào DB.
    Nếu auto_push_drive = True, kịch bản sẽ được đẩy thẳng lên Google Drive Render Farm ngay lập tức!
    """
    channel_id = req.channel_id
    if not channel_id:
        channels = settings.get_channels_config()
        channel_id = channels[0].get("id", "channel_1") if channels else "channel_1"

    ch_cfg = next((c for c in settings.get_channels_config() if c.get("id") == channel_id), {})
    lang = req.language or ch_cfg.get("language", "en")

    from app.agents.content_agent import ContentAgent
    content_agent = ContentAgent()

    if req.custom_script:
        title = req.topic[:80]
        script_contract = None
        narration = req.custom_script
        scenes_json = json.dumps([{"scene_id": 1, "narration": narration, "stock_query": req.topic}], ensure_ascii=False)
    else:
        logger.info(f"Agent creating script for topic: '{req.topic}' (lang: {lang})")
        contract = content_agent.generate_script(req.topic, language=lang)
        title = contract.title
        narration = contract.full_narration
        scenes_json = contract.to_json_str()

    with get_db() as db:
        job = VideoJob(
            user_id=0,
            target_channel_id=channel_id,
            topic=req.topic,
            title=title,
            script=narration,
            scenes_json=scenes_json,
            status="SCRIPT_READY",
            progress_percent=20,
            progress_message="Kịch bản AI đã sẵn sàng để render",
            description=f"{title}\n\n#Shorts #viral #trending",
            tags="shorts, viral, facts, psychology, documentary"
        )
        db.add(job)
        db.flush()
        job_id = job.id
        log_activity(db, 0, "AI Agent", "AGENT_CREATE_JOB", f"Tạo tự động VideoJob #{job_id}: {title}", channel_id=channel_id)

    return {
        "success": True,
        "job_id": job_id,
        "title": title,
        "channel_id": channel_id,
        "status": "SCRIPT_READY",
    }


class JobActionRequest(BaseModel):
    action: str  # "publish", "retry", "cancel", "delete"


@agent_router.post("/jobs/{job_id}/action")
async def execute_job_action(job_id: int, req: JobActionRequest):
    """
    Thực thi hành động ngay lập tức lên 1 VideoJob:
    - publish: Xuất bản video lên YouTube Shorts
    - retry: Đưa trạng thái về SCRIPT_READY để làm lại
    - cancel: Đánh dấu Hủy (FAILED)
    - delete: Xóa vĩnh viễn khỏi Database
    """
    act = req.action.lower().strip()

    if act == "retry":
        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not j:
                raise HTTPException(status_code=404, detail="Không tìm thấy job")
            j.status = "SCRIPT_READY"
            j.error_message = None
            j.progress_percent = 20
            j.progress_message = "Đã đặt lại trạng thái chờ render"
            log_activity(db, 0, "AI Agent", "RETRY_JOB", f"Đặt lại Video #{job_id} về SCRIPT_READY")
        return {"success": True, "action": act, "message": f"Video #{job_id} đã được reset về SCRIPT_READY"}

    elif act == "cancel":
        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not j:
                raise HTTPException(status_code=404, detail="Không tìm thấy job")
            j.status = "FAILED"
            j.error_message = "Hủy bởi AI Agent"
            log_activity(db, 0, "AI Agent", "CANCEL_JOB", f"AI Agent hủy Video #{job_id}")
        return {"success": True, "action": act, "message": f"Đã hủy Video #{job_id}"}

    elif act == "delete":
        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not j:
                raise HTTPException(status_code=404, detail="Không tìm thấy job")
            db.delete(j)
            log_activity(db, 0, "AI Agent", "DELETE_JOB", f"Xóa VideoJob #{job_id}")
        return {"success": True, "action": act, "message": f"Đã xóa vĩnh viễn VideoJob #{job_id}"}

    elif act == "publish":
        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not j:
                raise HTTPException(status_code=404, detail="Không tìm thấy job")
            if not j.video_path or not os.path.exists(j.video_path):
                raise HTTPException(status_code=400, detail="Video chưa được render hoặc file không tồn tại!")

            channel_id = j.target_channel_id
            ch_cfg = next((c for c in settings.get_channels_config() if c.get("id") == channel_id), {})
            v_title = j.title or j.topic
            v_desc = j.description or f"{v_title}\n\n#Shorts #viral"
            v_tags = j.tags or "shorts, viral"
            v_path = j.video_path

        from app.agents.publisher_agent import PublisherAgent
        pub_agent = PublisherAgent(channel_config=ch_cfg)
        pub_res = pub_agent.upload_video(
            video_path=v_path,
            title=v_title,
            description=v_desc,
            tags=v_tags,
            privacy_status="public",
            default_language=ch_cfg.get("language", "en"),
            default_audio_language=ch_cfg.get("language", "en")
        )

        if pub_res.get("status") == "success":
            yt_id = pub_res.get("video_id")
            yt_shorts = pub_res.get("shorts_url") or f"https://www.youtube.com/shorts/{yt_id}"
            with get_db() as db:
                j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if j:
                    j.status = "PUBLISHED"
                    j.youtube_video_id = yt_id
                    log_activity(db, 0, "AI Agent", "PUBLISH_YOUTUBE", f"Đã đăng Video #{job_id} lên YouTube Shorts ({yt_shorts})")
            return {"success": True, "action": act, "youtube_video_id": yt_id, "shorts_url": yt_shorts}
        else:
            err = pub_res.get("error", "Lỗi upload YouTube")
            return {"success": False, "action": act, "error": err}

    else:
        raise HTTPException(status_code=400, detail=f"Hành động '{act}' không hợp lệ.")


# ─── 4. KÊNH & HỆ THỐNG ─────────────────────────────────────────────────────

@agent_router.get("/channels")
async def list_channels():
    """Lấy danh sách kênh và thông số cấu hình"""
    cfg = settings.get_channels_config()
    with get_db() as db:
        u = db.query(User).filter(User.telegram_id == 0).first()
        active = u.active_channel_id if u else "channel_1"

    return {
        "success": True,
        "active_channel_id": active,
        "channels": cfg
    }


@agent_router.post("/channels/{channel_id}/select")
async def select_active_channel(channel_id: str):
    """Đặt kênh được chọn làm kênh mặc định"""
    cfg = settings.get_channels_config()
    exists = any(c.get("id") == channel_id for c in cfg)
    if not exists:
        raise HTTPException(status_code=404, detail=f"Kênh '{channel_id}' không tồn tại trong cấu hình channels.yaml")

    with get_db() as db:
        u = db.query(User).filter(User.telegram_id == 0).first()
        if not u:
            u = User(telegram_id=0, username="web_studio", role="admin")
            db.add(u)
        u.active_channel_id = channel_id
        log_activity(db, 0, "AI Agent", "SWITCH_CHANNEL", f"Chuyển kênh mục tiêu sang '{channel_id}'", channel_id=channel_id)

    return {"success": True, "active_channel_id": channel_id}


@agent_router.get("/logs")
async def get_activity_logs(limit: int = Query(default=30, ge=1, le=100)):
    """Lấy danh sách nhật ký hành động gần nhất của hệ thống"""
    with get_db() as db:
        logs = db.query(ActivityLog).order_by(ActivityLog.id.desc()).limit(limit).all()
        return {
            "success": True,
            "logs": [
                {
                    "id": l.id,
                    "username": l.username,
                    "action_type": l.action_type,
                    "details": l.details,
                    "channel_id": l.channel_id,
                    "created_at": l.created_at.isoformat() if l.created_at else None
                }
                for l in logs
            ]
        }


@agent_router.post("/system/reset-stuck-jobs")
async def reset_stuck_jobs(threshold_minutes: int = Query(default=30, ge=5)):
    """Khôi phục các jobs bị treo ở RENDERING quá thời gian quy định"""
    cutoff = datetime.utcnow() - timedelta(minutes=threshold_minutes)
    with get_db() as db:
        stuck = db.query(VideoJob).filter(
            VideoJob.status == "RENDERING",
            VideoJob.updated_at < cutoff
        ).all()

        reset_ids = []
        for j in stuck:
            j.status = "SCRIPT_READY"
            j.progress_message = f"Tự động reset từ {j.status} do vượt quá {threshold_minutes} phút"
            reset_ids.append(j.id)

        if reset_ids:
            log_activity(db, 0, "AI Agent", "RESET_STUCK_JOBS", f"Reset {len(reset_ids)} jobs bị treo: {reset_ids}")

    return {
        "success": True,
        "reset_count": len(reset_ids),
        "reset_job_ids": reset_ids
    }
