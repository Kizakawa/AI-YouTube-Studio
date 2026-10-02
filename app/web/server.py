import os
import sys
import time
import secrets
import json
import yaml
import base64
import threading
import logging
import asyncio
import shutil
import re
import requests as http_requests
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from typing import Dict, List, Any, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Form, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import contextmanager
from sqlalchemy import func
from config.settings import settings
from app.database.models import init_db, get_db, User, VideoJob, ActivityLog, log_activity
from app.utils.security import load_encrypted_json, save_encrypted_json, is_safe_identifier, is_safe_path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"

DEFAULT_DESKTOP_CLIENT = {
    "installed": {
        "client_id": "972622851789-4175b1dquscrk08j2aq4jb8s84llh4uo.apps.googleusercontent.com",
        "project_id": "bionic-client-509515-h2",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_secret": "GOCSPX-REDACTED_OAUTH_CLIENT_SECRET",
        "redirect_uris": ["http://localhost"]
    }
}

DEFAULT_WEB_CLIENT = {
    "web": {
        "client_id": "972622851789-l65i20vr94lp5mnc504oc58gdgumbr42.apps.googleusercontent.com",
        "project_id": "bionic-client-509515-h2",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_secret": "GOCSPX-REDACTED_OAUTH_CLIENT_SECRET",
        "redirect_uris": [
            "https://ai-youtube-studio-8kbu.onrender.com/api/oauth/callback"
        ]
    }
}

app = FastAPI(
    title="AI YouTube Multi-Channel Studio",
    description="Giao diện Web quản lý hệ thống tự động hoá YouTube",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def device_gatekeeper_middleware(request: Request, call_next):
    """Bảo mật 2 lớp: Thiết bị từ xa (khác localhost) phải có token đã được Admin duyệt trên Telegram mới được gọi API"""
    from app.services.device_auth import is_gatekeeper_enabled, is_local_ip, is_device_token_valid

    if not is_gatekeeper_enabled():
        return await call_next(request)

    path = request.url.path

    # Cho phép AI Agent có key hợp lệ hoặc truy cập các endpoint /api/agent
    agent_key = (
        request.headers.get("X-Agent-Key")
        or request.headers.get("X-Agent-Secret")
        or request.query_params.get("agent_key")
    )
    auth_hdr = request.headers.get("Authorization", "")
    if auth_hdr.startswith("Bearer "):
        bearer_val = auth_hdr[7:].strip()
        if bearer_val == getattr(settings, "AGENT_API_KEY", "agy_agent_supersecret_key_2026"):
            agent_key = bearer_val

    if agent_key and agent_key == getattr(settings, "AGENT_API_KEY", "agy_agent_supersecret_key_2026"):
        return await call_next(request)

    if path.startswith("/api/agent"):
        return await call_next(request)

    # Cho phép trang chủ HTML, tài nguyên tĩnh, media video, health check, oauth callback, và các endpoint xác thực thiết bị
    if (
        path.startswith("/static")
        or path.startswith("/media")
        or path.startswith("/api/auth")
        or path.startswith("/api/oauth")
        or path.startswith("/api/colab")
        or path.startswith("/api/worker")
        or path.startswith("/api/render-complete")
        or "/start-auth" in path
        or "/auth-start" in path
        or "/auth-status" in path
        or path in ("/", "/health", "/api/health", "/api/system/client-info", "/api/network-info", "/favicon.ico")
    ):
        return await call_next(request)

    client_ip = request.client.host if request.client else "127.0.0.1"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()

    # Máy chủ localhost luôn được cho qua
    if is_local_ip(client_ip):
        return await call_next(request)

    # Thiết bị từ xa phải có token hợp lệ đã được phê duyệt qua Telegram
    token = request.headers.get("X-Device-Token") or request.cookies.get("device_token")
    if not is_device_token_valid(token, client_ip=client_ip):
        from starlette.responses import JSONResponse
        return JSONResponse(
            status_code=401,
            content={"detail": "Thiết bị chưa được Quản trị viên Telegram phê duyệt truy cập. Vui lòng nhờ Quản trị viên bấm [Đồng Ý] trên nhóm Telegram."}
        )

    return await call_next(request)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

MEDIA_DIR = BASE_DIR / "output"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")

# Đăng ký Router dành riêng cho AI Agent
from app.web.agent_api import agent_router
app.include_router(agent_router)

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
                    active_channel_id="channel_maya"
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

    # Tự động nạp hoặc khôi phục credentials/tokens cho Google Account 1
    try:
        import base64
        import json
        acc1_dir = BASE_DIR / "credentials" / "accounts" / "account_1"
        acc1_dir.mkdir(parents=True, exist_ok=True)
        secret_p = acc1_dir / "client_secret.json"
        is_render = bool(os.getenv("RENDER") or os.getenv("IS_RENDER"))
        default_secret = DEFAULT_WEB_CLIENT if is_render else DEFAULT_DESKTOP_CLIENT
        secret_p.write_text(json.dumps(default_secret, indent=2), encoding="utf-8")
        mode_label = "Web Application 1-Click (Render)" if is_render else "Desktop Application (Localhost)"
        logger.info(f"✅ [Startup] Đã tự động kích hoạt client_secret.json {mode_label} cho account_1")

        # Tự động nạp toàn bộ 11 tokens của cả 3 tài khoản mẹ và 5 kênh YouTube từ Token Vault
        try:
            from app.services.token_vault import restore_all_embedded_tokens
            restored = restore_all_embedded_tokens()
            if restored > 0:
                logger.info(f"✅ [Startup] Token Vault đã khôi phục {restored} tokens thành công!")
        except Exception as e_vault:
            logger.warning(f"Lỗi khởi động Token Vault: {e_vault}")
    except Exception as e_sec:
        logger.warning(f"Lỗi khôi phục credentials startup: {e_sec}")


    # Khởi động ChannelIntelligenceWorker ngầm (giám sát KPI, chống trùng ý tưởng, tự động đăng tạm)
    try:
        from app.services.channel_intelligence import ChannelIntelligenceWorker
        ChannelIntelligenceWorker.start(check_interval_minutes=30)
    except Exception as e:
        logger.warning(f"Lỗi khởi động ChannelIntelligenceWorker on startup: {e}")

# ─── Serve UI ─────────────────────────────────────────────────────────────────

_cached_index_html: Optional[str] = None
_cached_index_mtime: float = 0.0

@app.api_route("/", methods=["GET", "HEAD"])
def serve_dashboard():
    """Phục vụ file index.html tức thì (cache in-memory tự cập nhật theo mtime của file, 1ms)"""
    global _cached_index_html, _cached_index_mtime
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="index.html chưa tồn tại")
    mtime = index_path.stat().st_mtime
    if _cached_index_html is None or mtime != _cached_index_mtime:
        _cached_index_html = index_path.read_text(encoding="utf-8")
        _cached_index_mtime = mtime
    return Response(
        content=_cached_index_html,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache"}
    )

@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    """Endpoint kiểm tra sức khỏe hệ thống (cho Render, UptimeRobot, Docker)"""
    return {"status": "ok", "app": "AI YouTube Multi-Channel Studio"}

@app.get("/api/network-info")
async def get_network_info_endpoint():
    """Lấy thông tin địa chỉ URL truy cập Web Dashboard"""
    port = int(os.getenv("PORT", 8000))
    web_url = os.getenv("WEB_DASHBOARD_URL") or f"http://localhost:{port}"
    return {
        "port": port,
        "lan_ip": "127.0.0.1",
        "local_url": f"http://127.0.0.1:{port}",
        "localhost_url": f"http://localhost:{port}",
        "wifi_url": web_url,
        "mdns_url": web_url,
        "fixed_domain": "localhost",
        "is_lan_available": False
    }

# ─── Device Authentication & Telegram 2FA Gatekeeper ─────────────────────────

@app.get("/api/auth/check")
async def check_device_auth(request: Request, response: Response):
    """Kiểm tra quyền truy cập thiết bị (Localhost luôn qua; Thiết bị từ xa phải có token hoặc IP đã duyệt)"""
    client_ip = request.client.host if request.client else "127.0.0.1"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()

    from app.services.device_auth import is_gatekeeper_enabled, is_local_ip, is_device_token_valid, get_token_for_ip, parse_device_info

    if not is_gatekeeper_enabled() or is_local_ip(client_ip):
        return {"authenticated": True, "is_local": True, "client_ip": client_ip}

    auth_token = request.headers.get("X-Device-Token") or request.cookies.get("device_token")
    if is_device_token_valid(auth_token, client_ip=client_ip):
        token = auth_token or get_token_for_ip(client_ip)
        if token:
            response.set_cookie(
                key="device_token",
                value=token,
                max_age=7 * 24 * 3600,
                path="/",
                samesite="lax",
                httponly=False
            )
        return {"authenticated": True, "is_local": False, "client_ip": client_ip, "token": token}

    ua = request.headers.get("user-agent", "")
    device_name = parse_device_info(ua)
    return {
        "authenticated": False,
        "is_local": False,
        "client_ip": client_ip,
        "device_name": device_name
    }

@app.post("/api/auth/request-access")
async def request_device_access(request: Request):
    """Gửi yêu cầu phê duyệt thiết bị mới đến nhóm Telegram"""
    client_ip = request.client.host if request.client else "127.0.0.1"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()

    from app.services.device_auth import create_access_request
    ua = request.headers.get("user-agent", "")
    return create_access_request(client_ip, ua)

@app.get("/api/auth/status/{request_id}")
async def check_device_request_status(request_id: str, request: Request, response: Response):
    """Frontend polling kiểm tra xem Admin đã bấm [Đồng Ý] trên Telegram chưa"""
    client_ip = request.client.host if request.client else "127.0.0.1"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()

    from app.services.device_auth import get_request_status
    res = get_request_status(request_id, client_ip=client_ip)
    if res.get("status") == "approved" and res.get("token"):
        response.set_cookie(
            key="device_token",
            value=res["token"],
            max_age=7 * 24 * 3600,
            path="/",
            samesite="lax",
            httponly=False
        )
    return res

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
        cfg = _read_channels_yaml()
        for acc in cfg.get("google_accounts", []):
            acc_id = acc.get("id")
            if not acc_id:
                continue
            drive_token_path = BASE_DIR / "credentials" / "accounts" / acc_id / "drive_token.json"
            if drive_token_path.exists():
                tok = load_encrypted_json(drive_token_path)
                if tok and (tok.get("token") or tok.get("refresh_token")):
                    drive_connected = True
                    storage_label = acc.get("drive_storage") or "Google One"
                    drive_quota_str = f"{storage_label} (Đã kết nối ✓)"
                    break
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

_channel_token_health_cache: Dict[str, Dict[str, Any]] = {}

def _check_channel_token_health(ch_id: str, token_file: Path) -> Dict[str, Any]:
    """Kiểm tra xem token của kênh có thực sự dùng được hay bị invalid_grant (cache 60s)."""
    now = time.time()
    cached = _channel_token_health_cache.get(ch_id)
    if cached and (now - cached.get("checked_at", 0) < 60):
        return cached

    if not token_file.exists():
        res = {"valid": False, "expired": False, "title": None, "error": "No token file", "checked_at": now}
        _channel_token_health_cache[ch_id] = res
        return res

    try:
        t_data = load_encrypted_json(token_file)
        if not t_data or "refresh_token" not in t_data:
            res = {"valid": False, "expired": False, "title": None, "error": "Malformed token", "checked_at": now}
            _channel_token_health_cache[ch_id] = res
            return res

        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        creds = Credentials.from_authorized_user_info(t_data)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed token
            save_encrypted_json(token_file, json.loads(creds.to_json()))

        res = {"valid": True, "expired": False, "title": t_data.get("channel_title"), "error": None, "checked_at": now}
        _channel_token_health_cache[ch_id] = res
        return res
    except Exception as e:
        err_str = str(e)
        is_exp = "invalid_grant" in err_str or "expired" in err_str or "revoked" in err_str
        res = {"valid": False, "expired": is_exp, "title": None, "error": err_str, "checked_at": now}
        _channel_token_health_cache[ch_id] = res
        return res

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

            # Kiểm tra token xác thực Google OAuth riêng của kênh này
            base_dir = Path(settings.BASE_DIR)
            ch_token_file = base_dir / "credentials" / "channels" / str(ch_id) / "youtube_token.json"
            has_ch_token = ch_token_file.exists()
            token_ch_title = None
            if has_ch_token:
                try:
                    t_data = load_encrypted_json(ch_token_file)
                    token_ch_title = t_data.get("channel_title")
                except Exception:
                    pass

            brand_channels = parent_acc.get("brand_channels", [])
            is_first_ch = len(brand_channels) > 0 and brand_channels[0].get("id") == ch_id

            token_status = _check_account_tokens(acc_id)
            has_client_secret = token_status["has_client_secret"]
            drive_connected = token_status["drive_connected"]

            ch_health = _check_channel_token_health(str(ch_id), ch_token_file) if has_ch_token else {"valid": False, "expired": False}
            token_expired = ch_health.get("expired", False)

            if has_ch_token:
                if ch_health.get("valid"):
                    is_connected = True
                    status_label = f"🟢 Đã liên kết ({token_ch_title or ch.get('name')})"
                elif token_expired:
                    is_connected = False
                    status_label = f"🔴 Token đã hết hạn ({token_ch_title or ch.get('name')}) - Cần cấp lại"
                else:
                    is_connected = False
                    status_label = f"🟡 Lỗi token ({token_ch_title or ch.get('name')})"
            elif is_first_ch and token_status["youtube_connected"]:
                is_connected = True
                status_label = "🟢 Dùng quyền Gmail mẹ"
            else:
                is_connected = False
                status_label = "🟡 Cần cấp quyền riêng"

            result.append({
                "id": ch_id,
                "name": ch.get("name"),
                "niche": ch.get("niche"),
                "language": ch.get("language", "en"),
                "google_account": acc_id,
                "gmail": gmail,
                "email_label": gmail,
                "token_status": status_label,
                "token_expired": token_expired,
                "is_authenticated": is_connected,
                "youtube_connected": is_connected,
                "has_own_token": has_ch_token,
                "token_channel_title": token_ch_title,
                "drive_connected": drive_connected,
                "has_client_secret": has_client_secret,
                "visual_mode": ch.get("visual_mode", "auto"),
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
                    if isinstance(data, list):
                        scene_count = len(data)
                    elif isinstance(data, dict):
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
                if isinstance(data, list):
                    scenes = data
                elif isinstance(data, dict):
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

# ─── Edit Script & Scenes (Pre-render) ────────────────────────────────────────

class SceneEditItem(BaseModel):
    scene_id: Optional[int] = None
    narration: str
    visual_keyword: Optional[str] = None
    visual_prompt: Optional[str] = None
    media_type: Optional[str] = "auto"
    estimated_seconds: Optional[float] = 3.0

class UpdateScriptPayload(BaseModel):
    title: Optional[str] = None
    hook: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    scenes: List[SceneEditItem]

@app.put("/api/videos/{job_id}/script")
@app.post("/api/videos/{job_id}/script")
async def update_video_script(job_id: int, payload: UpdateScriptPayload) -> Dict[str, Any]:
    """
    Cập nhật / chỉnh sửa toàn bộ kịch bản video (tiêu đề, lời thoại, phân cảnh).
    Hỗ trợ thêm, sửa, xóa phân cảnh trước khi render.
    """
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        if job.status == "RENDERING":
            raise HTTPException(status_code=409, detail="Video đang trong quá trình render, không thể sửa kịch bản!")

        if not payload.scenes or len(payload.scenes) == 0:
            raise HTTPException(status_code=400, detail="Kịch bản phải có ít nhất 1 phân cảnh!")

        updated_scenes = []
        for idx, sc in enumerate(payload.scenes, start=1):
            narration_clean = sc.narration.strip()
            if not narration_clean:
                continue
            kw_clean = (sc.visual_keyword or "cinematic scene").strip()
            sec = float(sc.estimated_seconds) if sc.estimated_seconds and sc.estimated_seconds > 0 else 3.0
            updated_scenes.append({
                "scene_id": idx,
                "narration": narration_clean,
                "visual_keyword": kw_clean,
                "visual_prompt": sc.visual_prompt or kw_clean,
                "media_type": getattr(sc, 'media_type', 'auto') or 'auto',
                "estimated_seconds": sec,
            })

        if not updated_scenes:
            raise HTTPException(status_code=400, detail="Các phân cảnh không được để trống lời thoại!")

        new_title = payload.title.strip() if payload.title and payload.title.strip() else (job.title or job.topic)
        full_narration = " ".join(s["narration"] for s in updated_scenes)
        hook = payload.hook.strip() if payload.hook and payload.hook.strip() else updated_scenes[0]["narration"][:100]

        tags_raw = payload.tags or job.tags or "shorts, psychology, facts"
        tags_list = [t.strip() for t in tags_raw.split(",") if t.strip()]
        description = payload.description.strip() if payload.description and payload.description.strip() else (job.description or f"{new_title} #shorts")

        script_dict = {
            "title": new_title,
            "hook": hook,
            "full_narration": full_narration,
            "scenes": updated_scenes,
            "tags": tags_list,
            "description": description,
        }

        job.title = new_title
        job.script = full_narration
        job.description = description
        job.tags = ",".join(tags_list)
        job.scenes_json = json.dumps(script_dict, ensure_ascii=False)
        if job.status in ("FAILED", "DRAFT"):
            job.status = "SCRIPT_READY"
            job.error_message = None

        log_activity(db, 0, "WebStudio", "EDIT_SCRIPT",
                     f"Đã cập nhật kịch bản Video #{job_id}: {len(updated_scenes)} phân cảnh")

    return {
        "success": True,
        "message": f"Kịch bản Video #{job_id} đã được lưu thành công ({len(updated_scenes)} phân cảnh)!",
        "job_id": job_id,
        "title": new_title,
        "scene_count": len(updated_scenes),
        "scenes": updated_scenes,
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
    channel_id: str = "channel_maya"
    language: str = "en"
    director_notes: Optional[str] = None

class RefineScriptRequest(BaseModel):
    feedback: str
    language: Optional[str] = None

@app.post("/api/scripts/create")
async def create_script_from_web(body: CreateScriptRequest) -> Dict[str, Any]:
    """
    Tạo kịch bản AI trực tiếp từ Web Studio:
    Tự động chấm điểm qua ScriptCritic và lặp lại tái tạo cho đến khi đạt chuẩn viral (>= 8.0/10) mới hiển thị.
    """
    try:
        from app.agents.content_agent import ContentAgent
        channels = settings.get_channels_config()
        ch = next((c for c in channels if c.get("id") == body.channel_id), {})
        channel_niche = ch.get("niche") or "general"
        channel_lang = body.language or ch.get("language") or "en"

        agent = ContentAgent()
        script, eval_report = await asyncio.to_thread(
            agent.generate_best_script,
            topic=body.topic,
            channel_niche=channel_niche,
            language=channel_lang,
            director_notes=body.director_notes,
            min_acceptable_score=8.0,
            max_attempts=3
        )

        scores_summary = {
            "overall": eval_report.overall_score if eval_report else 0,
            "overall_score": eval_report.overall_score if eval_report else 0,
            "passed": eval_report.passed if eval_report else False,
            "hook": eval_report.hook_strength.score if eval_report else 0,
            "hook_strength": eval_report.hook_strength.score if eval_report else 0,
            "pacing": eval_report.story_pacing.score if eval_report else 0,
            "story_pacing": eval_report.story_pacing.score if eval_report else 0,
            "visual": (eval_report.visual_direction.score if getattr(eval_report, "visual_direction", None) else 8.5) if eval_report else 0,
            "visual_direction": (eval_report.visual_direction.score if getattr(eval_report, "visual_direction", None) else 8.5) if eval_report else 0,
            "actionable_critiques": eval_report.actionable_critiques if eval_report else [],
            "script_critic": eval_report.model_dump() if eval_report else None,
        }

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
                quality_scores=json.dumps(scores_summary, ensure_ascii=False) if eval_report else None
            )
            db.add(job)
            db.flush()
            job_id = job.id

        eval_data = None
        if eval_report:
            vis_s = getattr(eval_report, "visual_direction", None)
            vis_score = vis_s.score if vis_s else 8.5
            eval_data = {
                "overall_score": eval_report.overall_score,
                "hook_score": eval_report.hook_strength.score,
                "pacing_score": eval_report.story_pacing.score,
                "visual_score": vis_score,
                "passed": eval_report.passed,
                "badge": "🏆 Xuất sắc" if eval_report.overall_score >= 8.5 else "✅ Đạt chuẩn Viral",
                "critiques": eval_report.actionable_critiques or []
            }

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
                    "visual_prompt": getattr(s, "visual_prompt", s.visual_keyword),
                    "media_type": getattr(s, "media_type", "auto"),
                    "estimated_seconds": s.estimated_seconds,
                }
                for s in script.scenes
            ],
            "eval": eval_data
        }
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"Lỗi tạo kịch bản: {str(e)}\n{traceback.format_exc()}")


@app.post("/api/videos/{job_id}/ai-refine")
async def refine_script_with_ai(job_id: int, body: RefineScriptRequest) -> Dict[str, Any]:
    """
    Tiếp thu góp ý chỉnh sửa của người dùng, tái tạo kịch bản,
    tự động chấm điểm bằng ScriptCritic và lặp lại đến khi đạt chuẩn viral.
    """
    try:
        from app.agents.content_agent import ContentAgent
        from app.schemas.contracts import ScriptContract

        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not job:
                raise HTTPException(status_code=404, detail=f"Không tìm thấy video job #{job_id}")
            if not job.scenes_json:
                raise HTTPException(status_code=400, detail=f"Job #{job_id} chưa có kịch bản phân cảnh")

            current_script = ScriptContract.from_json_str(job.scenes_json)
            channel_id = job.target_channel_id

        channels = settings.get_channels_config()
        ch = next((c for c in channels if c.get("id") == channel_id), {})
        channel_niche = ch.get("niche") or "general"
        channel_lang = body.language or ch.get("language") or "en"

        agent = ContentAgent()
        revised_script, eval_report = await asyncio.to_thread(
            agent.refine_with_feedback_and_eval,
            current_script=current_script,
            feedback=body.feedback,
            channel_niche=channel_niche,
            language=channel_lang,
            min_acceptable_score=8.0,
            max_attempts=3
        )

        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if job:
                job.title = revised_script.title
                job.script = revised_script.full_narration
                job.description = revised_script.description
                job.tags = ",".join(revised_script.tags)
                job.scenes_json = revised_script.to_json_str()
                job.status = "SCRIPT_READY"

                scores = {}
                if job.quality_scores:
                    try:
                        scores = json.loads(job.quality_scores)
                    except Exception:
                        pass
                if eval_report:
                    vis_s = getattr(eval_report, "visual_direction", None)
                    vis_score = vis_s.score if vis_s else 8.5
                    scores["overall"] = eval_report.overall_score
                    scores["overall_score"] = eval_report.overall_score
                    scores["passed"] = eval_report.passed
                    scores["hook"] = eval_report.hook_strength.score
                    scores["hook_strength"] = eval_report.hook_strength.score
                    scores["pacing"] = eval_report.story_pacing.score
                    scores["story_pacing"] = eval_report.story_pacing.score
                    scores["visual"] = vis_score
                    scores["visual_direction"] = vis_score
                    scores["actionable_critiques"] = eval_report.actionable_critiques or []
                    scores["script_critic"] = eval_report.model_dump()
                job.quality_scores = json.dumps(scores, ensure_ascii=False)

        eval_data = None
        if eval_report:
            vis_s = getattr(eval_report, "visual_direction", None)
            vis_score = vis_s.score if vis_s else 8.5
            eval_data = {
                "overall": eval_report.overall_score,
                "overall_score": eval_report.overall_score,
                "hook": eval_report.hook_strength.score,
                "hook_score": eval_report.hook_strength.score,
                "hook_strength": eval_report.hook_strength.score,
                "pacing": eval_report.story_pacing.score,
                "pacing_score": eval_report.story_pacing.score,
                "story_pacing": eval_report.story_pacing.score,
                "visual": vis_score,
                "visual_score": vis_score,
                "visual_direction": vis_score,
                "passed": eval_report.passed,
                "badge": "🏆 Xuất sắc" if eval_report.overall_score >= 8.5 else "✅ Đạt chuẩn Viral",
                "critiques": eval_report.actionable_critiques or [],
                "actionable_critiques": eval_report.actionable_critiques or []
            }

        return {
            "success": True,
            "job_id": job_id,
            "title": revised_script.title,
            "hook": revised_script.hook,
            "tags": ",".join(revised_script.tags),
            "scene_count": len(revised_script.scenes),
            "total_duration": revised_script.total_estimated_duration,
            "scenes": [
                {
                    "scene_id": s.scene_id,
                    "narration": s.narration,
                    "visual_keyword": s.visual_keyword,
                    "visual_prompt": getattr(s, "visual_prompt", s.visual_keyword),
                    "media_type": getattr(s, "media_type", "auto"),
                    "estimated_seconds": s.estimated_seconds,
                }
                for s in revised_script.scenes
            ],
            "eval": eval_data
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"Lỗi khi tinh chỉnh kịch bản: {str(e)}\n{traceback.format_exc()}")

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

@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health_check() -> Dict[str, Any]:
    import sys
    from app.utils.ffmpeg_helper import is_ffmpeg_installed, get_ffmpeg_version
    keys = settings.get_gemini_keys_pool()
    web_only = "--web-only" in sys.argv

    # Kiểm tra Drive token thật sự
    drive_connected = False
    drive_quota_label = "Chưa kết nối"
    try:
        cfg = _read_channels_yaml()
        for acc in cfg.get("google_accounts", []):
            acc_id = acc.get("id")
            if not acc_id:
                continue
            drive_token_path = BASE_DIR / "credentials" / "accounts" / acc_id / "drive_token.json"
            if drive_token_path.exists():
                tok = load_encrypted_json(drive_token_path)
                if tok and (tok.get("token") or tok.get("refresh_token")):
                    drive_connected = True
                    storage_label = acc.get("drive_storage") or "Google One"
                    drive_quota_label = f"{storage_label} (Đã kết nối ✓)"
                    break
    except Exception:
        pass

    render_info = {
        "backend": "local",
        "engine": "FFmpeg Local GPU / CPU",
        "status": "ready"
    }

    return {
        "status": "online",
        "ffmpeg": get_ffmpeg_version() if is_ffmpeg_installed() else "not installed",
        "gemini_keys": len(keys),
        "telegram_token": bool(settings.TELEGRAM_BOT_TOKEN),
        "web_only": web_only,
        "drive_connected": drive_connected,
        "drive_quota_label": drive_quota_label,
        "render": render_info,
    }


# ─── Colab GPU Farm Multi-Account Endpoints ─────────────────────────────────

COLAB_SHARED_NOTEBOOK_ID = "1Ud-gsXBteHJATziinfVOCMNAndXzK2Qo"
COLAB_SHARED_FOLDER_ID = "1J4pW7WNwFvIKpNjMqW8qkdOMf4gXTDC0"
COLAB_BASE_URL = f"https://colab.research.google.com/drive/{COLAB_SHARED_NOTEBOOK_ID}"

import urllib.parse

def make_account_chooser_url(email: str, base_url: str = COLAB_BASE_URL) -> str:
    """Tạo URL Google AccountChooser để ép trình duyệt chọn đúng tài khoản Google mong muốn."""
    target = f"{base_url}?authuser={email}"
    enc = urllib.parse.quote(target, safe="")
    return f"https://accounts.google.com/AccountChooser?Email={email}&continue={enc}"

COLAB_ACCOUNTS = [
    {
        "id": "account_1",
        "name": "Tài khoản 1 (Chính)",
        "email": "pool1.studio@example.com",
        "storage": "20 TB Google One",
        "role": "Thư mục Render Farm gốc & Lưu trữ",
        "badge": "Host 20TB",
        "badge_color": "green",
        "is_primary": True,
        "colab_url": make_account_chooser_url("pool1.studio@example.com")
    },
    {
        "id": "acount_2",
        "name": "Tài khoản 2 (Dự phòng 1)",
        "email": "pool2.studio@example.com",
        "storage": "2 TB Google One",
        "role": "Worker GPU xoay vòng (Đã cấp quyền Editor)",
        "badge": "Dự phòng 1",
        "badge_color": "blue",
        "is_primary": False,
        "colab_url": make_account_chooser_url("pool2.studio@example.com")
    },
    {
        "id": "accout3",
        "name": "Tài khoản 3 (Dự phòng 2)",
        "email": "pool3.studio@example.com",
        "storage": "2 TB Google One",
        "role": "Worker GPU xoay vòng (Đã cấp quyền Editor)",
        "badge": "Dự phòng 2",
        "badge_color": "purple",
        "is_primary": False,
        "colab_url": make_account_chooser_url("pool3.studio@example.com")
    }
]

@app.get("/api/colab/farm-status")
async def get_colab_farm_status() -> Dict[str, Any]:
    return {"success": True, "colab_disabled": True, "online": False, "message": "Đã tắt tính năng Google Colab, render cục bộ 100%"}

async def _old_get_colab_farm_status() -> Dict[str, Any]:
    from app.services.colab_client import colab_client
    is_online, info = colab_client.check_health(timeout=1.5)
    
    pending_count = 0
    try:
        with get_db() as db:
            pending_count = db.query(VideoJob).filter(VideoJob.status.in_(["SCRIPT_READY", "RENDERING", "PENDING_COLAB"])).count()
    except Exception:
        pass

    return {
        "success": True,
        "online": is_online,
        "gpu": info.get("gpu", "NVIDIA GPU") if is_online else None,
        "worker_url": colab_client.worker_url,
        "shared_notebook_id": COLAB_SHARED_NOTEBOOK_ID,
        "shared_notebook_url": COLAB_BASE_URL,
        "shared_folder_id": COLAB_SHARED_FOLDER_ID,
        "pending_count": pending_count,
        "nodes": [
            {
                "node_num": 1,
                "name": "Node 1 (Chính)",
                "id": "1Ud-gsXBteHJATziinfVOCMNAndXzK2Qo",
                "url": make_account_chooser_url("pool1.studio@example.com", "https://colab.research.google.com/drive/1Ud-gsXBteHJATziinfVOCMNAndXzK2Qo"),
                "desc": "Worker GPU mặc định"
            },
            {
                "node_num": 2,
                "name": "Node 2 (Chạy song song)",
                "id": "10kBAHrC_dfubSfBUhU1yBK_9z8FruGK7",
                "url": make_account_chooser_url("pool1.studio@example.com", "https://colab.research.google.com/drive/10kBAHrC_dfubSfBUhU1yBK_9z8FruGK7"),
                "desc": "Colab Pro+ chạy đồng thời không xung đột"
            },
            {
                "node_num": 3,
                "name": "Node 3 (Chạy song song)",
                "id": "1ubdsL5iN0cPal4hP4LllhVjKSs_QGl6u",
                "url": make_account_chooser_url("pool1.studio@example.com", "https://colab.research.google.com/drive/1ubdsL5iN0cPal4hP4LllhVjKSs_QGl6u"),
                "desc": "Colab Pro+ chạy đồng thời không xung đột"
            }
        ],
        "accounts": COLAB_ACCOUNTS,
        "rotation_strategy": {
            "title": "Cơ chế Render Song Song Colab Pro+ & Xoay Vòng Tài Khoản",
            "description": "Colab Pro+ cho phép chạy nhiều GPU độc lập. Mở Node 1 cho video thứ nhất và Node 2 cho video thứ hai trong tab mới để render song song không bao giờ xung đột."
        }
    }


# ─── Worker Webhook Callback & Dispatch Endpoints (P1.2 Architecture) ────────

@app.post("/api/worker/callback")
@app.post("/api/colab/callback")
@app.post("/api/render-complete")
async def worker_render_callback(request: Request) -> Dict[str, Any]:
    """
    Webhook tiếp nhận kết quả hoàn thành từ GPU Worker (Modal, Colab, RunPod).
    Tự động cập nhật VideoJob sang RENDERED ngay lập tức mà không cần polling Drive.
    """
    try:
        body = await request.json()
    except Exception as e:
        return {"status": "error", "message": f"Invalid JSON payload: {e}"}

    job_id = body.get("job_id")
    if not job_id:
        return {"status": "error", "message": "job_id is required"}

    status = body.get("status", "completed")
    video_path = body.get("video_path")
    drive_link = body.get("drive_link")
    render_time = body.get("render_time_s", 0)
    error_msg = body.get("error")

    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            return {"status": "error", "message": f"VideoJob #{job_id} not found"}

        if status == "completed":
            job.status = "RENDERED"
            if video_path:
                job.video_path = video_path
            if drive_link:
                job.drive_link = drive_link
            job.progress_percent = 100
            job.progress_message = f"🎉 Video đã render hoàn tất trên GPU Worker ({render_time}s)!"
            job.error_message = None

            log_activity(db, job.user_id or 0, "System", "WORKER_RENDER_COMPLETE",
                         f"GPU Worker hoàn tất render Video #{job_id} trong {render_time}s: {drive_link or video_path}",
                         channel_id=job.target_channel_id)

            logger.info(f"✅ [Worker Callback] VideoJob #{job_id} đã cập nhật thành công sang RENDERED.")

            # Gửi thông báo Telegram
            try:
                from app.services.channel_intelligence import send_telegram_broadcast
                notify_text = (
                    f"🎉 **VIDEO #{job_id} ĐÃ RENDER XONG TRÊN GPU WORKER!**\n\n"
                    f"🎬 **Tiêu đề:** `{job.title or job.topic}`\n"
                    f"⚡ **Thời gian render:** {render_time}s\n"
                    f"📺 **Kênh:** `{job.target_channel_id}`\n"
                )
                if drive_link:
                    notify_text += f"🔗 [Xem file trên Google Drive]({drive_link})\n"
                send_telegram_broadcast(notify_text)
            except Exception as e_tg:
                logger.debug(f"Không thể gửi thông báo Telegram cho job #{job_id}: {e_tg}")

        else:
            job.status = "FAILED"
            job.error_message = error_msg or "Worker báo lỗi không xác định"
            job.progress_message = f"❌ GPU Worker render thất bại: {job.error_message}"
            log_activity(db, job.user_id or 0, "System", "WORKER_RENDER_FAILED",
                         f"GPU Worker thất bại khi render Video #{job_id}: {error_msg}",
                         channel_id=job.target_channel_id)
            logger.warning(f"❌ [Worker Callback] VideoJob #{job_id} render thất bại: {error_msg}")

    return {
        "status": "success",
        "message": f"Đã xử lý callback cho VideoJob #{job_id} thành công",
        "job_status": status
    }


@app.post("/api/worker/dispatch/{job_id}")
async def dispatch_job_to_worker(job_id: int) -> Dict[str, Any]:
    """Điều phối tác vụ render của VideoJob sang GPU Worker."""
    from app.services.worker_dispatcher import worker_dispatcher
    ok, msg, worker_type = worker_dispatcher.dispatch_job(job_id)
    return {
        "status": "success" if ok else "error",
        "message": msg,
        "worker_type": worker_type,
        "job_id": job_id
    }


@app.get("/api/worker/status")
async def get_worker_status_api() -> Dict[str, Any]:
    """Trả về trạng thái chi tiết của toàn bộ hệ thống GPU Workers (Colab, Modal, Local)."""
    from app.services.worker_dispatcher import worker_dispatcher
    return worker_dispatcher.get_workers_status()


@app.post("/api/colab/send-telegram-guide")
async def send_colab_telegram_guide(request: Request) -> Dict[str, Any]:
    return {"success": False, "message": "Tính năng Colab đã được tắt. Hệ thống render cục bộ bằng FFmpeg."}

async def _old_send_colab_telegram_guide(request: Request) -> Dict[str, Any]:
    """
    Gửi link Colab và hướng dẫn mở đúng tài khoản về Telegram của người dùng.
    Hỗ trợ gửi cho 1 tài khoản cụ thể hoặc toàn bộ danh sách 3 tài khoản.
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    
    acc_id = body.get("account_id", "all")
    selected_acc = next((a for a in COLAB_ACCOUNTS if a["id"] == acc_id), None)
    
    from app.services.channel_intelligence import send_telegram_broadcast
    
    if selected_acc:
        msg = (
            f"🚀 **ĐỀ XUẤT MỞ COLAB GPU FARM — {selected_acc['name'].upper()}**\n\n"
            f"🎯 **Tài khoản mục tiêu:** `{selected_acc['email']}`\n"
            f"📦 **Gói Drive:** {selected_acc['storage']} ({selected_acc['role']})\n"
            f"🔗 **Link Colab 1-chạm:**\n{selected_acc['colab_url']}\n\n"
            f"🧭 **HƯỚNG DẪN 3 BƯỚC MỞ ĐÚNG TÀI KHOẢN:**\n"
            f"1️⃣ **Mở link:** Bấm nút bên dưới để mở Colab. Link đã kèm `?authuser={selected_acc['email']}` để Google tự chọn profile này.\n"
            f"2️⃣ **Kiểm tra Avatar:** Nhìn góc trên bên phải màn hình Colab:\n"
            f"   • Nếu đúng email `{selected_acc['email']}`: Sẵn sàng!\n"
            f"   • Nếu hiện email khác: Nhấp vào avatar ➡️ Chọn `{selected_acc['email']}` (hoặc bấm Thêm tài khoản).\n"
            f"3️⃣ **Bấm Chạy tất cả:** Vào menu **Runtime** ➡️ Chọn **Run all** (hoặc nhấn `Ctrl + F9`) ➡️ Bấm Cho phép Drive.\n\n"
            f"✨ **Bật xong bạn có thể thoát tab hoặc tắt màn hình!** GPU Colab sẽ tự quét hàng đợi Drive và render video 20 giây/video."
        )
        inline_kb = [
            [{"text": f"🚀 Mở Colab ({selected_acc['name']})", "url": selected_acc["colab_url"]}],
            [{"text": "🔄 Quét lại kết nối GPU", "callback_data": "check_colab_status"}]
        ]
    else:
        msg = (
            f"🚀 **TRẠM RENDER COLAB GPU FARM — XOAY VÒNG 3 TÀI KHOẢN**\n\n"
            f"Giải pháp render video siêu tốc 20s/video, tự động xoay tài khoản khi chạm hạn mức GPU miễn phí của Google Colab.\n\n"
            f"📌 **DANH SÁCH 3 TÀI KHOẢN GPU:**\n"
            f"1️⃣ **Tài khoản 1 (Chính):** `pool1.studio@example.com` (20 TB)\n"
            f"2️⃣ **Tài khoản 2 (Dự phòng 1):** `pool2.studio@example.com` (2 TB)\n"
            f"3️⃣ **Tài khoản 3 (Dự phòng 2):** `pool3.studio@example.com` (2 TB)\n\n"
            f"💡 **HƯỚNG DẪN XOAY TÀI KHOẢN KHI HẾT QUOTA:**\n"
            f"• Bạn mở Tài khoản 1 chạy trước.\n"
            f"• Khi Colab báo *'Cannot connect to GPU backend'* (hết hạn mức trong ngày), bạn chỉ cần mở nút **Tài khoản 2** và bấm **Run all**.\n"
            f"• Cả 3 tài khoản đều chung thư mục Google Drive `/AI_YouTube_RenderFarm/` nên video vẫn tiếp tục render bình thường!\n\n"
            f"👇 Chọn tài khoản bạn muốn mở ngay bên dưới:"
        )
        inline_kb = [
            [{"text": "🚀 Mở Acc 1: giangnguyenviet38 (20TB)", "url": COLAB_ACCOUNTS[0]["colab_url"]}],
            [{"text": "🚀 Mở Acc 2: tonyabcd1234567890 (2TB)", "url": COLAB_ACCOUNTS[1]["colab_url"]}],
            [{"text": "🚀 Mở Acc 3: maychuvv1n1 (2TB)", "url": COLAB_ACCOUNTS[2]["colab_url"]}],
            [{"text": "🔄 Quét lại kết nối GPU", "callback_data": "check_colab_status"}]
        ]
        
    try:
        send_telegram_broadcast(msg, inline_keyboard=inline_kb)
        return {"success": True, "message": "Đã gửi link Colab và hướng dẫn mở tài khoản về Telegram thành công!"}
    except Exception as e:
        return {"success": False, "message": f"Lỗi gửi Telegram: {str(e)}"}


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
        target_channel = job.target_channel_id

    # Xác định đúng kênh và tài khoản Google lưu trữ của kênh đó
    cfg = _read_channels_yaml()
    channels = cfg.get("channels", [])
    ch_cfg = next((c for c in channels if c.get("id") == target_channel), {})
    ch_name = ch_cfg.get("name") or target_channel or "Psychology of the world"
    acc_id = ch_cfg.get("google_account", "account_1")

    # Upload đồng bộ (không chạy background để có link ngay)
    try:
        from app.agents.drive_agent import DriveAgent
        from app.database.models import get_db as _get_db
        agent = DriveAgent(account_name=acc_id)
        if not agent.service:
            raise HTTPException(
                status_code=503,
                detail=f"Google Drive của tài khoản '{acc_id}' chưa kết nối. Chạy py auth_google.py drive để xác thực."
            )
        result = agent.upload_video(video_path, video_title, channel_name=ch_name)
        if result.get("status") == "success" and result.get("drive_link"):
            drive_link = result["drive_link"]
            folder_link = result.get("folder_link")
            with _get_db() as db:
                j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if j:
                    j.drive_link = drive_link
                    from app.database.models import log_activity
                    log_activity(db, 0, "WebStudio", "UPLOAD_DRIVE",
                                 f"Video #{job_id} đã tải lên thư mục kênh '{ch_name}' trên Google Drive: {drive_link}",
                                 channel_id=target_channel)
            return {
                "success": True,
                "drive_link": drive_link,
                "folder_link": folder_link,
                "channel_name": ch_name,
                "message": f"Tải lên Google Drive thành công vào thư mục kênh '{ch_name}'!"
            }
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
    auto_schedule: bool = False
    pinned_comment: Optional[str] = None


@app.get("/api/videos/{job_id}/monetization-preview")
def get_video_monetization_preview(job_id: int, channel_id: Optional[str] = None):
    """
    Sinh gói xem trước sản phẩm Affiliate Amazon (link kèm tag kizakawa-20, mô tả & bình luận mồi)
    cho video trước khi đăng lên YouTube.
    """
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")

        target_ch = channel_id or job.target_channel_id or "channel_maya"
        cfg = _read_channels_yaml()
        channels = cfg.get("channels", [])
        ch_cfg = next((c for c in channels if c.get("id") == target_ch), {"id": target_ch, "name": target_ch})

        title = job.title or job.topic or f"Video #{job_id}"
        description = job.description or ""
        pinned_comment = None
        topic = job.topic or ""
        script_text = ""

        if job.scenes_json:
            try:
                s_data = json.loads(job.scenes_json)
                if isinstance(s_data, list):
                    scenes = s_data
                    pinned_comment = None
                elif isinstance(s_data, dict):
                    pinned_comment = s_data.get("pinned_comment")
                    scenes = s_data.get("scenes", [])
                script_text = " ".join([sc.get("narration", "") for sc in scenes if isinstance(sc, dict)])
            except Exception:
                pass

        from app.services.monetization_engine import preview_monetization_bundle
        bundle = preview_monetization_bundle(
            channel_config=ch_cfg,
            title=title,
            description=description,
            pinned_comment=pinned_comment,
            topic=topic,
            script=script_text
        )
        return {"success": True, **bundle}

@app.get("/api/channels/{channel_id}/next-golden-slot")
def get_channel_next_golden_slot(channel_id: str):
    """Tính toán khe giờ vàng tiếp theo trống lịch cho kênh"""
    try:
        from app.services.smart_scheduler import (
            get_next_available_golden_slot,
            get_channel_upcoming_schedule,
            get_golden_slots_overview,
            sync_scheduled_jobs_status
        )
        sync_scheduled_jobs_status()
        slot_dt, slot_iso, display_vn, display_us = get_next_available_golden_slot(channel_id)
        upcoming = get_channel_upcoming_schedule(channel_id, limit=5)
        overview = get_golden_slots_overview()
        return {
            "success": True,
            "channel_id": channel_id,
            "slot_iso": slot_iso,
            "display_vn": display_vn,
            "display_us": display_us,
            "upcoming": upcoming,
            "golden_slots": overview
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/channels/{channel_id}/schedule")
def get_channel_full_schedule(channel_id: str):
    """Lấy chi tiết toàn bộ lịch phát sóng, khe tiếp theo và 4 khung giờ vàng quốc tế"""
    try:
        from app.services.smart_scheduler import (
            get_next_available_golden_slot,
            get_channel_upcoming_schedule,
            get_golden_slots_overview,
            sync_scheduled_jobs_status
        )
        sync_scheduled_jobs_status()
        slot_dt, slot_iso, display_vn, display_us = get_next_available_golden_slot(channel_id)
        upcoming = get_channel_upcoming_schedule(channel_id, limit=10)
        overview = get_golden_slots_overview()
        return {
            "success": True,
            "channel_id": channel_id,
            "next_slot": {
                "iso": slot_iso,
                "display_vn": display_vn,
                "display_us": display_us
            },
            "upcoming": upcoming,
            "golden_slots": overview
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/videos/{job_id}/publish-youtube")
async def publish_to_youtube(
    job_id: int,
    body: Optional[PublishYouTubeRequest] = None,
    privacy: str = "public"
) -> Dict[str, Any]:
    """
    Đăng hoặc tự động lên lịch video đã render lên YouTube qua PublisherAgent.
    Hỗ trợ chế độ auto_schedule tự động xếp vào khe giờ vàng còn trống tiếp theo.
    """
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        if job.status not in ("RENDERED", "SCHEDULED", "PUBLISHED"):
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
        auto_schedule = (body.auto_schedule if body else False) or (schedule_time == "auto")

        display_vn_slot = None
        display_us_slot = None
        slot_dt = None

        # Tự động tính toán khe giờ vàng nếu bật auto_schedule
        if auto_schedule:
            from app.services.smart_scheduler import get_next_available_golden_slot
            slot_dt, schedule_time, display_vn_slot, display_us_slot = get_next_available_golden_slot(target_channel)
            chosen_privacy = "private"
        elif schedule_time:
            chosen_privacy = "private"

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

        # ── Kiểm tra token OAuth riêng của kênh để tránh đăng nhầm sang kênh cũ ──
        base_dir = Path(settings.BASE_DIR)
        ch_token = base_dir / "credentials" / "channels" / target_channel / "youtube_token.json"
        acc_token = base_dir / "credentials" / "accounts" / acc_id / "youtube_token.json"

        if ch_token.exists():
            token_file = str(ch_token)
        else:
            # Kiểm tra xem đây có phải là kênh phụ chưa cấp quyền riêng
            brand_channels = []
            for a in cfg.get("google_accounts", []):
                if a.get("id") == acc_id:
                    brand_channels = a.get("brand_channels", [])
                    break
            is_first_ch = len(brand_channels) > 0 and brand_channels[0].get("id") == target_channel
            if not is_first_ch and len(brand_channels) > 1:
                ch_name = ch_cfg.get("name", target_channel)
                first_name = brand_channels[0].get("name", brand_channels[0].get("id"))
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"⚠️ Kênh '{ch_name}' chưa được cấp quyền YouTube riêng! "
                        f"Nếu đăng ngay, video sẽ bị đăng nhầm vào kênh '{first_name}'. "
                        f"Vui lòng vào tab 'Kênh & Tài Khoản', tìm kênh '{ch_name}' và bấm '🔑 Cấp Quyền YouTube (Kênh Này)' "
                        f"để chọn đúng kênh trên Google."
                    )
                )
            token_file = str(acc_token)

        ch_cfg["token_file"] = token_file
        pub_agent = PublisherAgent(channel_config=ch_cfg, account_name=acc_id)
        if not pub_agent.service:
            ch_name = ch_cfg.get("name", target_channel)
            init_err = getattr(pub_agent, "init_error", None) or "Chưa kết nối OAuth"
            if "invalid_grant" in init_err or "expired" in init_err:
                detail_msg = (
                    f"⚠️ Phiên đăng nhập YouTube của kênh '{ch_name}' đã hết hạn (Google OAuth token expired/revoked). "
                    f"Vui lòng chạy lệnh 'python auth_channel.py {target_channel}' trong terminal hoặc vào tab 'Kênh & Tài Khoản' bấm '🔑 Cấp Quyền YouTube (Kênh Này)' để kết nối lại."
                )
            else:
                detail_msg = f"Kênh '{ch_name}' chưa kết nối YouTube OAuth ({init_err}). Vui lòng vào tab 'Kênh & Tài Khoản' bấm '🔑 Đăng Nhập YouTube (Kênh Này)' hoặc chạy 'python auth_channel.py {target_channel}'."
            raise HTTPException(
                status_code=503,
                detail=detail_msg
            )

        pinned_comment = (body.pinned_comment.strip() if body and body.pinned_comment else None)
        if not pinned_comment and job.scenes_json:
            try:
                s_data = json.loads(job.scenes_json)
                pinned_comment = s_data.get("pinned_comment")
            except Exception:
                pass
        if not pinned_comment:
            if ch_cfg.get("niche") in ("deep_sea_earth_mysteries", "history_mystery"):
                pinned_comment = "What's your theory about this? Comment below and Subscribe for Part 2! 👇🌊"
            elif ch_cfg.get("niche") == "psychology":
                pinned_comment = "Did this hit close to home? Subscribe and comment which sign is most accurate! 👇🧠"
            else:
                pinned_comment = "What do you think about this? Comment below and subscribe for more! 👇"

        pub_res = pub_agent.upload_video(
            video_path=video_path,
            title=chosen_title,
            description=chosen_desc,
            tags=video_tags,
            privacy_status=chosen_privacy,
            schedule_time_iso=schedule_time,
            default_language=ch_lang,
            default_audio_language=ch_lang,
            pinned_comment=pinned_comment,
            topic=job.topic or "",
            script=job.scenes_json or "",
        )

        if pub_res.get("status") == "success":
            yt_url = pub_res.get("youtube_url")
            yt_id = pub_res.get("video_id")
            yt_shorts_url = pub_res.get("shorts_url") or f"https://www.youtube.com/shorts/{yt_id}"
            ch_name = ch_cfg.get("name", target_channel)

            # Tự động sao lưu lên Google Drive vào đúng folder của kênh
            drive_link = None
            folder_link = None
            try:
                from app.agents.drive_agent import DriveAgent
                drive_agent = DriveAgent(account_name=acc_id)
                if drive_agent.service:
                    drive_res = drive_agent.upload_video(video_path, chosen_title, channel_name=ch_name)
                    if drive_res.get("status") == "success":
                        drive_link = drive_res.get("drive_link")
                        folder_link = drive_res.get("folder_link")
            except Exception as e_drv:
                logger.warning(f"Không thể tự động backup Drive cho Video #{job_id}: {e_drv}")

            with _get_db() as db:
                j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if j:
                    j.status = "SCHEDULED" if schedule_time else "PUBLISHED"
                    j.youtube_video_id = yt_id
                    if pub_res.get("enriched_description"):
                        j.description = pub_res.get("enriched_description")
                    if drive_link:
                        j.drive_link = drive_link
                    if slot_dt:
                        j.scheduled_time = slot_dt
                    elif schedule_time:
                        try:
                            from datetime import datetime as _dt
                            j.scheduled_time = _dt.fromisoformat(schedule_time.replace("Z", "+00:00"))
                        except Exception:
                            pass
                    
                    prod_info = pub_res.get("product_info")
                    prod_name = prod_info.get("name") if prod_info else ""
                    affiliate_note = f" (Amazon: {prod_name})" if prod_name else ""
                    msg = (
                        f"Video #{job_id} đã lên lịch công chiếu vào {display_vn_slot} trên kênh '{ch_name}'{affiliate_note}: {yt_shorts_url}"
                        if schedule_time and display_vn_slot
                        else f"Video #{job_id} đã xuất bản lên kênh '{ch_name}'{affiliate_note}: {yt_shorts_url}"
                    )
                    log_activity(db, 0, "WebStudio", "PUBLISH_YOUTUBE", msg, channel_id=target_channel)

            success_msg = (
                f"Đã lên lịch thành công cho Video #{job_id}! Tự động công chiếu vào {display_vn_slot} ({display_us_slot}). Link: {yt_shorts_url}"
                if schedule_time and display_vn_slot
                else f"Đã đăng lên Shorts kênh '{ch_name}' thành công! Link: {yt_shorts_url}"
            )
            return {
                "success": True,
                "video_id": yt_id,
                "youtube_url": yt_url,
                "shorts_url": yt_shorts_url,
                "drive_link": drive_link,
                "folder_link": folder_link,
                "channel_id": target_channel,
                "channel_name": ch_name,
                "scheduled": bool(schedule_time),
                "scheduled_vn": display_vn_slot,
                "scheduled_iso": schedule_time,
                "product_info": pub_res.get("product_info"),
                "pinned_comment": pub_res.get("pinned_comment"),
                "comment_id": pub_res.get("comment_id"),
                "enriched_description": pub_res.get("enriched_description"),
                "message": success_msg
            }
        else:
            raise HTTPException(status_code=500, detail=pub_res.get("error", "Đăng YouTube thất bại"))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi đăng YouTube job #{job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# ─── Token Health Monitor (Part 4) ───────────────────────────────────────────

_token_health_cache: Dict[str, Any] = {}
_token_health_timestamp: float = 0.0

def _check_gemini_key(key: str) -> Dict[str, Any]:
    """Kiểm tra 1 Gemini API Key bằng cách gọi list models (lightweight, 0 quota cost, timeout 2.5s)."""
    prefix = key[:8] + "..." + key[-4:] if len(key) > 12 else key[:4] + "..."
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        r = http_requests.get(url, timeout=2.5)
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
        return {"prefix": prefix, "status": "timeout", "error": "Timeout (2.5s)"}
    except Exception as e:
        return {"prefix": prefix, "status": "error", "error": str(e)[:80]}


def _check_pexels_key(key: str) -> Dict[str, Any]:
    """Kiểm tra 1 Pexels API Key bằng cách tìm kiếm nhẹ, đọc quota từ response header (timeout 2.5s)."""
    prefix = key[:8] + "..." + key[-4:] if len(key) > 12 else key[:4] + "..."
    if not key or key == "your_pexels_api_key_here":
        return {"prefix": prefix, "status": "not_configured", "error": "Key chưa cấu hình"}
    try:
        headers = {"Authorization": key}
        r = http_requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": "nature", "per_page": 1},
            headers=headers,
            timeout=2.5
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
        return {"prefix": prefix, "status": "timeout", "error": "Timeout (2.5s)"}
    except Exception as e:
        return {"prefix": prefix, "status": "error", "error": str(e)[:80]}


@app.get("/api/token-health")
def get_token_health(force: bool = False) -> Dict[str, Any]:
    """
    Kiểm tra sức khỏe tất cả API Keys (Gemini + Pexels).
    Có in-memory cache 5 phút để tránh nghẽn server và lag giao diện.
    Chạy trong thread pool của FastAPI nên không bao giờ làm đơ event loop.
    """
    global _token_health_cache, _token_health_timestamp
    import time
    now = time.time()
    if not force and _token_health_cache and (now - _token_health_timestamp < 300):
        return _token_health_cache

    import concurrent.futures

    gemini_keys = settings.get_gemini_keys_pool()
    pexels_keys = settings.get_pexels_keys_pool()

    gemini_results = []
    pexels_results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        gemini_futures = {
            executor.submit(_check_gemini_key, k): i
            for i, k in enumerate(gemini_keys)
        }
        pexels_futures = {
            executor.submit(_check_pexels_key, k): i
            for i, k in enumerate(pexels_keys)
        }

        for future in concurrent.futures.as_completed(gemini_futures):
            try:
                idx = gemini_futures[future]
                result = future.result()
                result["index"] = idx + 1
                gemini_results.append(result)
            except Exception:
                pass

        for future in concurrent.futures.as_completed(pexels_futures):
            try:
                idx = pexels_futures[future]
                result = future.result()
                result["index"] = idx + 1
                pexels_results.append(result)
            except Exception:
                pass

    gemini_results.sort(key=lambda x: x.get("index", 0))
    pexels_results.sort(key=lambda x: x.get("index", 0))

    gemini_valid = sum(1 for r in gemini_results if r.get("status") == "ok")
    pexels_valid = sum(1 for r in pexels_results if r.get("status") == "ok")

    gemini_total = max(len(gemini_results), 1)
    pexels_total = max(len(pexels_results), 1)

    result_data = {
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
    _token_health_cache = result_data
    _token_health_timestamp = now
    return result_data


# ═════════════════════════════════════════════════════════════════════════════
# ─── GOOGLE ACCOUNTS & MULTI-CHANNEL STUDIO API ──────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════

_oauth_sessions: Dict[str, Dict[str, Any]] = {}
_oauth_manual_responses: Dict[str, str] = {}
_oauth_web_states: Dict[str, Dict[str, Any]] = {}


class OAuthSubmitCallbackRequest(BaseModel):
    session_key: str
    callback_url_or_code: str


@app.post("/api/oauth/submit-callback")
async def submit_oauth_callback(req: OAuthSubmitCallbackRequest) -> Dict[str, Any]:
    """Nhận link chuyển hướng hoặc mã code xác thực từ thiết bị khách / mạng LAN."""
    session_key = req.session_key.strip()
    raw = req.callback_url_or_code.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Vui lòng cung cấp link chuyển hướng hoặc mã xác thực từ Google")
    _oauth_manual_responses[session_key] = raw
    logger.info(f"Đã nhận mã callback thủ công cho session '{session_key}'")
    return {
        "success": True,
        "message": "Đã tiếp nhận mã xác thực! Hệ thống đang xử lý và lưu token..."
    }


@app.get("/api/oauth/callback")
async def oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None
):
    """
    Endpoint tiếp nhận chuyển hướng tự động 1-Click từ Google OAuth (Web application).
    Người dùng không cần copy-paste hay đụng vào bàn phím.
    """
    if error:
        return HTMLResponse(
            content=f"""<!DOCTYPE html>
            <html><head><meta charset="utf-8"><title>Lỗi Xác Thực</title></head>
            <body style="font-family:system-ui,sans-serif; background:#0f172a; color:#f8fafc; text-align:center; padding:60px 20px;">
                <div style="font-size:50px; margin-bottom:12px;">❌</div>
                <h2 style="color:#ef4444;">Lỗi Xác Thực Từ Google</h2>
                <p style="color:#94a3b8;">Chi tiết: {error}</p>
                <p style="color:#64748b; font-size:13px;">Bạn có thể đóng cửa sổ này và thử lại từ Web Dashboard.</p>
            </body></html>""",
            status_code=400
        )

    if not code or not state or state not in _oauth_web_states:
        return HTMLResponse(
            content="""<!DOCTYPE html>
            <html><head><meta charset="utf-8"><title>Hết Hạn</title></head>
            <body style="font-family:system-ui,sans-serif; background:#0f172a; color:#f8fafc; text-align:center; padding:60px 20px;">
                <div style="font-size:50px; margin-bottom:12px;">⚠️</div>
                <h2 style="color:#f59e0b;">Phiên Đăng Nhập Không Hợp Lệ Hoặc Đã Hết Hạn</h2>
                <p style="color:#94a3b8;">Vui lòng quay lại Web Dashboard và bấm Kết Nối lại.</p>
            </body></html>""",
            status_code=400
        )

    st_info = _oauth_web_states.pop(state)
    flow = st_info["flow"]
    session_key = st_info["session_key"]
    base_dir = Path(settings.BASE_DIR)

    try:
        flow.fetch_token(code=code)
        creds = flow.credentials

        if st_info.get("type") == "channel":
            channel_id = st_info["channel_id"]
            ch_name = st_info.get("channel_name", channel_id)
            acc_id = st_info.get("acc_id", "account_1")

            real_yt_title = None
            real_yt_id = None
            custom_url = None
            try:
                from googleapiclient.discovery import build
                yt = build("youtube", "v3", credentials=creds)
                c_res = yt.channels().list(mine=True, part="snippet,id").execute()
                items = c_res.get("items", [])
                if items:
                    real_yt_id = items[0].get("id")
                    snippet = items[0].get("snippet", {})
                    real_yt_title = snippet.get("title")
                    custom_url = snippet.get("customUrl")
            except Exception as e:
                logger.warning(f"Không thể đọc thông tin kênh YouTube từ token: {e}")

            ch_dir = base_dir / "credentials" / "channels" / channel_id
            ch_dir.mkdir(parents=True, exist_ok=True)
            token_file = ch_dir / "youtube_token.json"

            token_dict = json.loads(creds.to_json())
            token_dict["channel_id"] = real_yt_id or channel_id
            token_dict["channel_title"] = real_yt_title or ch_name
            if custom_url:
                token_dict["custom_url"] = custom_url

            save_encrypted_json(token_file, token_dict)

            cfg = _read_channels_yaml()
            for c in cfg.get("channels", []):
                if c.get("id") == channel_id:
                    if real_yt_title:
                        c["youtube_title"] = real_yt_title
                    if real_yt_id:
                        c["youtube_channel_id"] = real_yt_id
                        c["channel_url"] = f"https://www.youtube.com/channel/{real_yt_id}"
                    if custom_url:
                        c["handle"] = custom_url
                    break

            for acc in cfg.get("google_accounts", []):
                if acc.get("id") == acc_id:
                    for bch in acc.get("brand_channels", []):
                        if bch.get("id") == channel_id:
                            if real_yt_title:
                                bch["youtube_title"] = real_yt_title
                            if real_yt_id:
                                bch["youtube_channel_id"] = real_yt_id
                            break
                    break
            _write_channels_yaml(cfg)

            succ_title = real_yt_title or ch_name
            succ_msg = f"Đã cấp quyền YouTube thành công cho kênh '{succ_title}'!"
            _oauth_sessions[session_key] = {
                "status": "success",
                "channel_title": succ_title,
                "message": succ_msg
            }
            display_name = f"Kênh YouTube: {succ_title}"
        else:
            account_id = st_info["account_id"]
            service = st_info["service"]
            acc_dir = Path(st_info["acc_dir"])
            token_filename = st_info["token_filename"]

            token_data = creds.to_json()
            token_file = acc_dir / token_filename
            token_dict_to_save = json.loads(token_data)
            save_encrypted_json(token_file, token_dict_to_save)

            b64_str = base64.b64encode(token_data.encode("utf-8")).decode("utf-8")
            b64_file = acc_dir / f"{Path(token_filename).stem}_base64.txt"
            with open(b64_file, "w", encoding="utf-8") as f:
                f.write(b64_str)

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
                if detected_email and not detected_email.endswith("@pages.plusgoogle.com"):
                    cfg = _read_channels_yaml()
                    for acc in cfg.get("google_accounts", []):
                        if acc.get("id") == account_id:
                            acc["email_label"] = detected_email
                            break
                    _write_channels_yaml(cfg)
            except Exception:
                pass

            succ_msg = f"Đã kết nối {service.upper()} thành công cho tài khoản '{account_id}'!"
            _oauth_sessions[session_key] = {
                "status": "success",
                "message": succ_msg
            }
            display_name = f"Tài khoản Google: {account_id}"

        return HTMLResponse(content=f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Cấp Quyền Thành Công</title>
    <style>
        body {{ font-family: system-ui, -apple-system, sans-serif; background: #0b1120; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
        .card {{ background: #1e293b; border: 1px solid rgba(56,189,248,0.3); border-radius: 16px; padding: 40px; text-align: center; max-width: 440px; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.7); }}
        .icon {{ font-size: 56px; margin-bottom: 16px; }}
        h2 {{ color: #34d399; margin: 0 0 10px 0; font-size: 24px; font-weight: 800; }}
        p {{ color: #94a3b8; font-size: 14.5px; line-height: 1.6; margin: 0 0 24px 0; }}
        .badge {{ display: inline-block; padding: 6px 14px; background: rgba(52,211,153,0.15); color: #34d399; border: 1px solid rgba(52,211,153,0.3); border-radius: 20px; font-size: 13px; font-weight: 600; margin-bottom: 20px; }}
        .progress-bar {{ width: 100%; height: 5px; background: #334155; border-radius: 3px; overflow: hidden; }}
        .progress-fill {{ width: 100%; height: 100%; background: linear-gradient(90deg, #34d399, #38bdf8); animation: shrink 2.2s linear forwards; }}
        @keyframes shrink {{ from {{ width: 100%; }} to {{ width: 0%; }} }}
    </style>
</head>
<body>
    <div class="card">
        <div class="icon">🎉</div>
        <div class="badge">✓ ĐÃ KẾT NỐI 1-CLICK THÀNH CÔNG</div>
        <h2>Cấp Quyền Google Hoàn Tất!</h2>
        <p>Hệ thống đã nhận diện và đồng bộ <strong>{display_name}</strong>.<br>Cửa sổ này sẽ <strong>tự động đóng sau 2 giây</strong>...</p>
        <div class="progress-bar"><div class="progress-fill"></div></div>
    </div>
    <script>
        try {{
            if (window.opener) {{
                window.opener.postMessage({{ type: 'oauth_complete', success: true }}, '*');
            }}
        }} catch(e) {{}}
        setTimeout(function() {{
            try {{ window.close(); }} catch(e) {{}}
        }}, 2200);
    </script>
</body>
</html>""")

    except Exception as ex:
        logger.error(f"Lỗi trao đổi code OAuth qua callback: {ex}")
        _oauth_sessions[session_key] = {"status": "error", "error": str(ex)}
        return HTMLResponse(
            content=f"""<!DOCTYPE html>
            <html><head><meta charset="utf-8"><title>Lỗi</title></head>
            <body style="font-family:system-ui,sans-serif; background:#0f172a; color:#f8fafc; text-align:center; padding:60px 20px;">
                <div style="font-size:50px; margin-bottom:12px;">❌</div>
                <h2 style="color:#ef4444;">Không Thể Xác Thực Mã Code</h2>
                <p style="color:#94a3b8;">Lỗi: {ex}</p>
                <p style="color:#64748b; font-size:13px;">Bạn có thể đóng tab này và thử lại từ Dashboard.</p>
            </body></html>""",
            status_code=500
        )

def _read_channels_yaml() -> Dict[str, Any]:
    yaml_path = settings.CHANNELS_CONFIG_FILE
    example_path = yaml_path.with_name("channels.example.yaml")
    if not yaml_path.exists():
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
            if isinstance(data, dict) and example_path.exists():
                ch_list = data.get("channels", [])
                # Nếu file hiện tại chỉ có 1 kênh dummy 'channel_1' từ template cũ thì tự cập nhật từ example
                if len(ch_list) <= 1 and any(c.get("id") == "channel_1" for c in ch_list):
                    try:
                        with open(example_path, "r", encoding="utf-8") as ef:
                            ex_data = yaml.safe_load(ef)
                            if isinstance(ex_data, dict) and len(ex_data.get("channels", [])) > 1:
                                import shutil
                                shutil.copyfile(example_path, yaml_path)
                                data = ex_data
                    except Exception:
                        pass
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
            tok = load_encrypted_json(yt_token_path)
            if tok.get("token") or tok.get("refresh_token"):
                youtube_connected = True
        except Exception:
            pass

    drive_token_path = acc_dir / "drive_token.json"
    drive_connected = False
    if drive_token_path.exists():
        try:
            tok = load_encrypted_json(drive_token_path)
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
    niche: Optional[str] = ""
    language: Optional[str] = "en"
    preferred_voice: Optional[str] = "en-US-ChristopherNeural"
    visual_mode: Optional[str] = "auto"
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
    base_dir = Path(settings.BASE_DIR)
    for acc in raw_accounts:
        acc_id = acc.get("id", "")
        token_status = _check_account_tokens(acc_id)
        raw_bchannels = acc.get("brand_channels", [])

        enriched_bchannels = []
        for idx, bch in enumerate(raw_bchannels):
            bch_id = bch.get("id", "")
            ch_token_file = base_dir / "credentials" / "channels" / str(bch_id) / "youtube_token.json"
            has_own = ch_token_file.exists()
            ch_title = None
            if has_own:
                try:
                    ch_title = load_encrypted_json(ch_token_file).get("channel_title")
                except Exception:
                    pass
            is_conn = has_own or (idx == 0 and token_status["youtube_connected"])
            bch_copy = dict(bch)
            bch_copy["has_own_token"] = has_own
            bch_copy["youtube_connected"] = is_conn
            bch_copy["token_channel_title"] = ch_title
            enriched_bchannels.append(bch_copy)

        enriched_accounts.append({
            "id": acc_id,
            "email_label": acc.get("email_label", ""),
            "drive_storage": acc.get("drive_storage", "2 TB Google One"),
            "youtube_quota_daily": acc.get("youtube_quota_daily", 10000),
            "has_client_secret": token_status["has_client_secret"],
            "youtube_connected": token_status["youtube_connected"],
            "drive_connected": token_status["drive_connected"],
            "brand_channels": enriched_bchannels,
            "channels_count": len(enriched_bchannels)
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
    if not is_safe_identifier(account_id):
        raise HTTPException(status_code=400, detail="Mã tài khoản không hợp lệ.")

    base_dir = Path(settings.BASE_DIR)
    accounts_root = (base_dir / "credentials" / "accounts").resolve()
    acc_dir = (accounts_root / account_id).resolve()
    if not is_safe_path(acc_dir, accounts_root):
        raise HTTPException(status_code=400, detail="Đường dẫn không hợp lệ.")

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
    if not is_safe_identifier(account_id):
        raise HTTPException(status_code=400, detail="Mã tài khoản không hợp lệ.")

    try:
        secret_data = json.loads(req.content)
        if "installed" not in secret_data and "web" not in secret_data:
            raise ValueError("File client_secret.json không đúng định dạng của Google")
    except Exception as ex:
        raise HTTPException(status_code=400, detail=f"Nội dung file JSON không hợp lệ: {ex}")

    base_dir = Path(settings.BASE_DIR)
    accounts_root = (base_dir / "credentials" / "accounts").resolve()
    acc_dir = (accounts_root / account_id.strip()).resolve()
    if not is_safe_path(acc_dir, accounts_root):
        raise HTTPException(status_code=400, detail="Đường dẫn không hợp lệ.")

    acc_dir.mkdir(parents=True, exist_ok=True)
    secret_path = acc_dir / "client_secret.json"

    with open(secret_path, "w", encoding="utf-8") as f:
        json.dump(secret_data, f, indent=2)

    return {
        "success": True,
        "message": f"Đã lưu file client_secret.json cho tài khoản '{account_id}' thành công!"
    }

def extract_auth_code_and_redirect_uri(raw_val: str, default_redirect_uri: str):
    raw_val = (raw_val or "").strip()
    if not raw_val:
        raise ValueError("Vui lòng dán đường link chuyển hướng hoặc mã code xác thực từ Google.")

    # Gỡ bỏ dấu ngoặc kép nếu user lỡ copy cả cặp dấu ngoặc
    if (raw_val.startswith('"') and raw_val.endswith('"')) or (raw_val.startswith("'") and raw_val.endswith("'")):
        raw_val = raw_val[1:-1].strip()

    # Bổ sung scheme http:// nếu copy từ thanh địa chỉ Edge/Chrome (vốn ẩn http://)
    if raw_val.startswith("localhost") or raw_val.startswith("127.0.0.1"):
        raw_val = "http://" + raw_val

    # 1. Cảnh báo nếu dán nhầm link đăng nhập/ủy quyền Google ban đầu
    is_login_page = False
    if "code=" not in raw_val:
        if raw_val.startswith("https://accounts.google.com") or raw_val.startswith("http://accounts.google.com"):
            is_login_page = True
        elif "accounts.google.com/o/oauth2" in raw_val or "accounts.google.com/signin" in raw_val:
            is_login_page = True

    if is_login_page:
        raise ValueError(
            "Bạn vừa dán link đăng nhập Google thay vì link kết quả! "
            "Hãy mở link đó trên trình duyệt, đăng nhập Gmail và bấm [Tiếp tục] / [Cho Phép] "
            "đến khi màn hình báo 'Không thể truy cập trang web này (localhost)', "
            "sau đó nhấn tổ hợp phím Ctrl + L (hoặc click thanh địa chỉ), nhấn Ctrl + A rồi Ctrl + C để sao chép toàn bộ link."
        )

    # 2. Cảnh báo nếu dán link localhost nhưng hoàn toàn KHÔNG có mã code
    if (raw_val.startswith("http://") or raw_val.startswith("https://")) and "code=" not in raw_val:
        raise ValueError(
            "Đường link bạn dán không chứa mã xác thực ('code=')! "
            "Có thể bạn chỉ mới copy tên miền 'http://localhost' mà chưa bôi đen toàn bộ đường link. "
            "👉 Cách khắc phục: Tại tab báo lỗi 'localhost refused to connect', click chuột vào thanh địa chỉ, "
            "nhấn tổ hợp phím Ctrl + A (chọn toàn bộ) rồi Ctrl + C (sao chép), sau đó dán lại vào đây."
        )

    # 3. Trích xuất redirect_uri
    redirect_uri = default_redirect_uri
    if raw_val.startswith("http://") or raw_val.startswith("https://"):
        parsed = urlparse(raw_val)
        if parsed.netloc:
            redirect_uri = f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"
            if not redirect_uri.endswith("/"):
                redirect_uri += "/"

    # 4. Trích xuất mã code bằng Regex
    code = None
    import re
    code_match = re.search(r"code=([^&?\s#]+)", raw_val)
    if code_match:
        code = code_match.group(1)
    elif "code=" in raw_val:
        qs_str = raw_val.split("?", 1)[-1] if "?" in raw_val else raw_val
        qs = parse_qs(qs_str)
        code = qs.get("code", [None])[0]
    else:
        clean_val = raw_val.split("&")[0].split("?")[0].strip()
        code = clean_val

    if not code:
        code = raw_val.strip()

    if code and "#" in code:
        code = code.split("#")[0].strip()

    if code:
        code = unquote(code).strip()

    if code.startswith("code="):
        code = code[5:].strip()

    # Cảnh báo nếu mã bị cắt ngắn do trình duyệt Edge hiển thị dấu '...'
    if code and (code.endswith("...") or code.endswith("…") or "..." in code):
        raise ValueError(
            "Đường link / mã xác thực bạn copy bị thiếu (có dấu '...' ở cuối do trình duyệt thu gọn)! "
            "👉 Cách khắc phục: Tại tab 'localhost', click chuột vào thanh địa chỉ, nhấn Ctrl + A (chọn tất cả) rồi Ctrl + C (copy) để lấy đầy đủ toàn bộ link."
        )

    if code and not code.startswith("4/") and code.startswith("0A"):
        code = "4/" + code

    # Kiểm tra tính hợp lệ của mã xác thực Google
    if not code.startswith("4/"):
        raise ValueError(
            f"Mã xác thực không đúng định dạng của Google (mã Google hợp lệ bắt buộc phải bắt đầu bằng '4/'). "
            f"Bạn vừa dán: '{code[:35]}...'. Vui lòng copy lại toàn bộ đường link bắt đầu bằng http://localhost..."
        )

    if len(code) < 35:
        raise ValueError(
            f"Mã xác thực quá ngắn (chỉ có {len(code)} ký tự, mã Google hợp lệ dài từ 50-100 ký tự). "
            f"Có thể bạn đã bôi đen thiếu phần đuôi. Hãy click vào thanh địa chỉ, nhấn Ctrl + A rồi Ctrl + C để sao chép toàn bộ."
        )

    return code, redirect_uri


def exchange_oauth_code_for_credentials(flow, raw_val: str):
    """
    Trao đổi OAuth code hoặc Redirect URL lấy Credentials an toàn tuyệt đối.
    Gửi trực tiếp 1 lần duy nhất tới Google Token Endpoint, loại bỏ hoàn toàn các lỗi
    mismatching_state của oauthlib và tự động sửa các lỗi sao chép URL của người dùng.
    Hỗ trợ đầy đủ chuẩn PKCE (code_verifier).
    """
    code, redirect_uri = extract_auth_code_and_redirect_uri(raw_val, flow.redirect_uri)

    cfg = flow.client_config or {}
    if "installed" in cfg:
        cfg = cfg["installed"]
    elif "web" in cfg:
        cfg = cfg["web"]

    client_id = cfg.get("client_id") or getattr(flow, "client_id", None)
    client_secret = cfg.get("client_secret") or getattr(flow, "client_secret", None)
    token_uri = cfg.get("token_uri", "https://oauth2.googleapis.com/token")
    code_verifier = getattr(flow, "code_verifier", None)
    if not code_verifier and hasattr(flow, "oauth2session") and hasattr(flow.oauth2session, "code_verifier"):
        code_verifier = flow.oauth2session.code_verifier

    token_payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }
    if code_verifier:
        token_payload["code_verifier"] = code_verifier

    resp = http_requests.post(
        token_uri,
        data=token_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30
    )

    # Thử lại nếu có lỗi redirect_uri_mismatch với flow.redirect_uri
    if resp.status_code != 200 and "redirect_uri_mismatch" in resp.text and redirect_uri != flow.redirect_uri:
        logger.info(f"Thử lại token exchange với flow.redirect_uri: {flow.redirect_uri}")
        token_payload["redirect_uri"] = flow.redirect_uri
        resp = http_requests.post(
            token_uri,
            data=token_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30
        )

    if resp.status_code != 200:
        err_msg = resp.text
        try:
            err_json = resp.json()
            err_desc = err_json.get("error_description", "")
            err_code = err_json.get("error", "")
            if "Malformed auth code" in err_desc:
                err_msg = (
                    "Mã xác thực Google không hợp lệ hoặc đã hết hạn (Malformed auth code). "
                    "Lưu ý: Mỗi mã code chỉ dùng được 1 lần trong vòng 2 phút. "
                    "Vui lòng đóng cửa sổ này, bấm 'Kết Nối' lại từ đầu và sao chép toàn bộ đường link mới."
                )
            else:
                err_msg = err_desc or err_code or resp.text
        except Exception:
            pass
        logger.warning(f"Google Token Exchange lỗi ({resp.status_code}): {err_msg}")
        raise ValueError(f"Google Token Exchange thất bại: {err_msg}")

    token_resp = resp.json()
    from google.oauth2.credentials import Credentials
    creds = Credentials(
        token=token_resp.get("access_token"),
        refresh_token=token_resp.get("refresh_token"),
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=list(flow.oauth2session.scope) if hasattr(flow.oauth2session, "scope") and flow.oauth2session.scope else None
    )
    return creds


@app.post("/api/accounts/{account_id}/auth-start")
async def start_google_oauth(account_id: str, req: AuthStartRequest, request: Request) -> Dict[str, Any]:
    """Khởi chạy luồng xác thực Google OAuth và trả về auth_url ngay lập tức để mở trang đăng nhập."""
    if not is_safe_identifier(account_id):
        raise HTTPException(status_code=400, detail="Mã tài khoản không hợp lệ.")
    service = req.service.lower()
    session_key = f"{account_id}:{service}"
    client_ip = request.client.host if request.client else "127.0.0.1"
    is_local_client = client_ip in ("127.0.0.1", "localhost", "::1", "testclient")

    import webbrowser
    import wsgiref.simple_server
    import wsgiref.util
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = [
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.force-ssl",
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
            raise HTTPException(status_code=400, detail="Chưa có file client_secret.json! Vui lòng nạp file trước.")
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
    callback_url = f"{scheme}://{host}/api/oauth/callback"

    # Kiểm tra xem client_secret có phải loại Web application không và có khớp redirect_uris không
    is_web = False
    sec_json = {}
    try:
        with open(secret_file, "r", encoding="utf-8") as f:
            sec_json = json.load(f)
    except Exception:
        pass

    if "web" in sec_json:
        allowed_uris = sec_json.get("web", {}).get("redirect_uris", [])
        if callback_url in allowed_uris:
            is_web = True
        else:
            logger.info(f"Callback URL '{callback_url}' không nằm trong web redirect_uris ({allowed_uris}). Tự động dùng Desktop Flow.")
            is_web = False

    if is_web:
        from google_auth_oauthlib.flow import Flow
        import secrets
        flow = Flow.from_client_config(sec_json, scopes=scopes, redirect_uri=callback_url)
        state_token = secrets.token_urlsafe(32)
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline", state=state_token)
        _oauth_web_states[state_token] = {
            "flow": flow,
            "session_key": session_key,
            "type": "account",
            "account_id": account_id,
            "service": service,
            "acc_dir": str(acc_dir),
            "token_filename": token_filename,
            "created_at": time.time()
        }
        _oauth_sessions[session_key] = {
            "status": "in_progress",
            "account_id": account_id,
            "service": service,
            "auth_url": auth_url,
            "message": "Phiên xác thực 1-Click tự động đã sẵn sàng. Hãy bấm đăng nhập trên trang Google.",
            "error": None
        }
        return {
            "success": True,
            "auth_url": auth_url,
            "session_key": session_key,
            "status": "in_progress"
        }

    desktop_cfg = sec_json if "installed" in sec_json else DEFAULT_DESKTOP_CLIENT
    flow = InstalledAppFlow.from_client_config(desktop_cfg, scopes=scopes)

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

    # Lưu URL xác thực để frontend hiển thị trên modal
    _oauth_sessions[session_key] = {
        "status": "in_progress",
        "account_id": account_id,
        "service": service,
        "auth_url": auth_url,
        "message": "Link xác thực đã sẵn sàng. Hãy đăng nhập Google trên trình duyệt.",
        "error": None
    }
    logger.info(f"OAuth URL created for {session_key} on port {port}")

    if is_local_client:
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass

    def _oauth_worker():
        try:
            # Chờ callback từ Google hoặc từ người dùng dán vào modal (timeout 3 phút)
            srv_thread = threading.Thread(target=local_server.handle_request, daemon=True)
            srv_thread.start()

            import time
            start_t = time.time()
            creds = None
            while time.time() - start_t < 180:
                auth_input = None
                if received_uri["value"]:
                    auth_input = received_uri["value"]
                    received_uri["value"] = None
                elif session_key in _oauth_manual_responses:
                    auth_input = _oauth_manual_responses.pop(session_key)

                if auth_input:
                    try:
                        creds = exchange_oauth_code_for_credentials(flow, auth_input)
                        if creds:
                            break
                    except Exception as ex:
                        logger.warning(f"Thử đổi mã OAuth thất bại: {ex}")
                        _oauth_sessions[session_key]["status"] = "error"
                        _oauth_sessions[session_key]["error"] = str(ex)
                        break

                time.sleep(0.5)

            try:
                local_server.server_close()
            except Exception:
                pass

            if not creds:
                if _oauth_sessions.get(session_key, {}).get("status") != "error":
                    _oauth_sessions[session_key] = {
                        "status": "error",
                        "error": "Hết thời gian chờ xác thực (3 phút). Vui lòng thử lại."
                    }
                return

            # Lưu token file mã hoá AES-256-GCM
            token_data = creds.to_json()
            token_file = acc_dir / token_filename
            token_dict_to_save = json.loads(token_data)
            save_encrypted_json(token_file, token_dict_to_save)

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
                # Chỉ cập nhật email thật (không lưu email dạng pages.plusgoogle.com của Brand Account)
                if detected_email and not detected_email.endswith("@pages.plusgoogle.com"):
                    cfg = _read_channels_yaml()
                    for acc in cfg.get("google_accounts", []):
                        if acc.get("id") == account_id:
                            acc["email_label"] = detected_email
                            break
                    _write_channels_yaml(cfg)
                    logger.info(f"Đã tự động nhận diện Gmail cho {account_id}: {detected_email}")
            except Exception as ex:
                logger.info(f"Không thể tự động đọc Google UserInfo: {ex}")

            # Nếu là service YouTube, kiểm tra xem kênh cụ thể nào vừa được cấp quyền
            matched_channel_name = None
            if service == "youtube":
                try:
                    from googleapiclient.discovery import build
                    yt = build("youtube", "v3", credentials=creds)
                    c_res = yt.channels().list(mine=True, part="snippet,id").execute()
                    items = c_res.get("items", [])
                    if items:
                        real_yt_id = items[0].get("id")
                        snippet = items[0].get("snippet", {})
                        real_yt_title = snippet.get("title")
                        custom_url = snippet.get("customUrl")
                        logger.info(f"YouTube Channel detected from OAuth: {real_yt_title} ({real_yt_id})")

                        # Tìm kênh tương ứng trong channels.yaml
                        cfg = _read_channels_yaml()
                        all_chs = cfg.get("channels", [])
                        matched_ch = None
                        for c in all_chs:
                            if c.get("id", "").lower() == (real_yt_id or "").lower():
                                matched_ch = c
                                break
                            if c.get("name", "").strip().lower() == (real_yt_title or "").strip().lower():
                                matched_ch = c
                                break

                        # Nếu không match chính xác, nếu chỉ có 1 kênh chưa có token thì match với kênh đó
                        if not matched_ch:
                            for c in all_chs:
                                ch_t_path = base_dir / "credentials" / "channels" / c.get("id", "") / "youtube_token.json"
                                if not ch_t_path.exists():
                                    matched_ch = c
                                    break

                        if matched_ch:
                            ch_target_id = matched_ch.get("id")
                            matched_channel_name = real_yt_title or matched_ch.get("name")
                            ch_token_dir = base_dir / "credentials" / "channels" / ch_target_id
                            ch_token_dir.mkdir(parents=True, exist_ok=True)
                            ch_token_file = ch_token_dir / "youtube_token.json"

                            ch_t_dict = json.loads(token_data)
                            ch_t_dict["channel_id"] = real_yt_id
                            ch_t_dict["channel_title"] = real_yt_title
                            if custom_url:
                                ch_t_dict["custom_url"] = custom_url
                            save_encrypted_json(ch_token_file, ch_t_dict)
                            logger.info(f"Đã tự động đồng bộ token cho kênh '{ch_target_id}' ({matched_channel_name})")

                            # Cập nhật channels.yaml
                            for c in cfg.get("channels", []):
                                if c.get("id") == ch_target_id:
                                    if real_yt_title: c["youtube_title"] = real_yt_title
                                    if real_yt_id: c["youtube_channel_id"] = real_yt_id
                                    if custom_url: c["handle"] = custom_url
                                    break
                            for acc in cfg.get("google_accounts", []):
                                if acc.get("id") == account_id:
                                    for bch in acc.get("brand_channels", []):
                                        if bch.get("id") == ch_target_id:
                                            if real_yt_title: bch["youtube_title"] = real_yt_title
                                            if real_yt_id: bch["youtube_channel_id"] = real_yt_id
                                            if custom_url: bch["handle"] = custom_url
                                            break
                            _write_channels_yaml(cfg)
                except Exception as ex:
                    logger.warning(f"Lỗi tự động phân loại kênh YouTube sau OAuth: {ex}")

            succ_msg = f"Đã kết nối {service.upper()} thành công!"
            if matched_channel_name:
                succ_msg += f" (Kênh: {matched_channel_name})"
            elif detected_email and not detected_email.endswith("@pages.plusgoogle.com"):
                succ_msg += f" (Gmail: {detected_email})"

            _oauth_sessions[session_key] = {
                "status": "success",
                "account_id": account_id,
                "service": service,
                "message": succ_msg,
                "channel_title": matched_channel_name,
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
        "auth_url": auth_url,
        "session_key": session_key,
        "status": "in_progress",
        "message": "Trang đăng nhập Google đã sẵn sàng. Hãy bấm 'Cho Phép' trên trình duyệt."
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
    raw_id = (req.id or "").strip()
    if not raw_id:
        import unicodedata
        slug = unicodedata.normalize('NFKD', req.name).encode('ascii', 'ignore').decode('ascii')
        raw_id = re.sub(r'[^\w\s-]', '', slug).strip().lower()
        raw_id = re.sub(r'[-\s]+', '_', raw_id)
        if not raw_id:
            raw_id = f"channel_{int(time.time())}"

    clean_id = raw_id.strip().lower().replace(" ", "_")

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
        "niche": (req.niche or "").strip(),
        "language": (req.language or "en").strip(),
        "visual_mode": req.visual_mode or "auto",
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
    channel["niche"] = (req.niche or "").strip()
    channel["language"] = (req.language or "en").strip()
    channel["visual_mode"] = req.visual_mode or channel.get("visual_mode", "auto")
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

# ─── Niche Intelligence Profiles Endpoints ─────────────────────────────────

@app.get("/api/niches")
async def list_niches() -> Dict[str, Any]:
    """Lấy danh sách tất cả các Hồ Sơ Ngách Kênh (Niche Intelligence Profiles)."""
    from app.services.niche_manager import load_all_niches
    niches = load_all_niches()
    return {"success": True, "count": len(niches), "niches": niches}

@app.get("/api/niches/{niche_id}")
async def get_niche_endpoint(niche_id: str) -> Dict[str, Any]:
    """Lấy chi tiết một Hồ Sơ Ngách theo ID."""
    from app.services.niche_manager import get_niche
    niche = get_niche(niche_id)
    return {"success": True, "niche": niche}

@app.post("/api/niches")
async def save_niche_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Tạo hoặc cập nhật một Hồ Sơ Ngách (Niche Intelligence Profile)."""
    from app.services.niche_manager import save_niche
    ok, msg = save_niche(payload)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg, "niche_id": payload.get("id")}

@app.delete("/api/niches/{niche_id}")
async def delete_niche_endpoint(niche_id: str) -> Dict[str, Any]:
    """Xóa một Hồ Sơ Ngách tùy biến."""
    from app.services.niche_manager import delete_niche
    ok, msg = delete_niche(niche_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}

@app.get("/api/channels/{channel_id}/niche")
async def get_channel_niche_endpoint(channel_id: str) -> Dict[str, Any]:
    """Lấy hồ sơ ngách đã phân giải cho một kênh YouTube cụ thể."""
    from app.services.niche_manager import get_channel_niche
    profile = get_channel_niche(channel_id)
    return {"success": True, "profile": profile}

# ─── Smart AI Shorts Repurposer Endpoints (Tái chế video dài thành Shorts) ─

class RepurposeAnalyzeRequest(BaseModel):
    url_or_path: str
    niche_id: str = "general"
    mode: str = "self_owned"
    channel_name: str = ""
    video_description: str = ""
    device_id: Optional[str] = None
    device_name: Optional[str] = None

class RepurposeRenderRequest(BaseModel):
    source: str
    start_seconds: float
    end_seconds: float
    title: str = ""
    description: str = ""
    channel_name: str = ""
    niche_id: str = "general"
    layout_mode: str = "smart_crop_full"
    audio_mode: str = "original"
    add_subtitles: bool = True
    subtitle_text: str = ""
    timed_transcript: Optional[List[Dict[str, Any]]] = None
    credit_text: Optional[str] = None
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    source_url: str = ""
    author: str = ""
    mode: str = "self_owned"

@app.get("/api/system/client-info")
def get_client_system_info(request: Request) -> Dict[str, Any]:
    """Trả về thông tin kết nối mạng LAN của máy khách đang truy cập."""
    client_ip = request.client.host if request.client else "127.0.0.1"
    return {
        "client_ip": client_ip,
        "is_local": client_ip in ("127.0.0.1", "localhost", "::1"),
    }

@app.get("/api/repurposer/local-videos")
def list_local_videos_for_repurpose() -> Dict[str, Any]:
    """Lấy danh sách các video có sẵn trong hệ thống để tái chế tức thì."""
    from app.services.video_repurposer import repurposer_service
    videos = repurposer_service.list_local_videos()
    return {"success": True, "count": len(videos), "videos": videos}

@app.post("/api/repurposer/analyze")
def analyze_repurpose_video(req: RepurposeAnalyzeRequest) -> Dict[str, Any]:
    """Phân tích video và phát hiện 3-5 khoảnh khắc highlight viral bằng Gemini AI."""
    from app.services.video_repurposer import repurposer_service, extract_youtube_video_id
    try:
        meta = repurposer_service.get_video_metadata(req.url_or_path)
        transcript_entries = []
        video_id = meta.get("video_id")
        if meta.get("source_type") == "youtube" and video_id:
            transcript_entries = repurposer_service.fetch_transcript(video_id)
        
        # Lấy mô tả từ request hoặc tự động từ yt-dlp metadata
        video_desc = req.video_description or meta.get("description", "")

        moments = repurposer_service.analyze_viral_moments(
            transcript_entries=transcript_entries,
            video_title=meta.get("title", "Video"),
            video_duration=meta.get("duration_seconds", 60.0),
            niche_id=req.niche_id,
            mode=req.mode,
            channel_name=req.channel_name,
            video_description=video_desc,
        )
        
        return {
            "success": True,
            "metadata": meta,
            "has_transcript": len(transcript_entries) > 0,
            "transcript_count": len(transcript_entries),
            "moments": moments,
            "niche_id": req.niche_id,
            "mode": req.mode,
        }
    except Exception as e:
        logger.error(f"Lỗi phân tích repurpose video: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/repurposer/render-clip")
def render_repurposed_clip(req: RepurposeRenderRequest, request: Request) -> Dict[str, Any]:
    """Cắt đoạn highlight, chuyển tỉ lệ 9:16 và ghép phụ đề Karaoke chuẩn Niche Profile."""
    import time
    import uuid
    from app.services.video_repurposer import repurposer_service, extract_youtube_video_id
    try:
        client_ip = request.client.host if request.client else "unknown"
        source_path = req.source.strip()
        effective_source_url = req.source_url.strip() if req.source_url else (source_path if source_path.startswith("http") else "")
        if not os.path.exists(source_path):
            video_id = extract_youtube_video_id(source_path)
            if not video_id:
                raise HTTPException(status_code=400, detail="Không tìm thấy file nguồn hoặc link YouTube hợp lệ")
            if not effective_source_url:
                effective_source_url = f"https://www.youtube.com/watch?v={video_id}"
            source_path = repurposer_service.download_segment_or_full(source_path)
            
        timestamp_int = int(time.time())
        # Tạo tên file độc nhất tuyệt đối, có định danh thiết bị chống xung đột mạng LAN
        raw_dev = (req.device_name or req.device_id or "dev").strip()
        safe_dev = "".join(c for c in raw_dev if c.isalnum() or c in ("-", "_"))[:10] or "dev"
        unique_tag = uuid.uuid4().hex[:6]
        out_filename = f"short_{safe_dev}_{timestamp_int}_{unique_tag}.mp4"
        
        result = repurposer_service.render_repurposed_short(
            source_video_path=source_path,
            start_seconds=req.start_seconds,
            end_seconds=req.end_seconds,
            output_filename=out_filename,
            niche_id=req.niche_id,
            layout_mode=req.layout_mode,
            audio_mode=req.audio_mode,
            add_subtitles=req.add_subtitles,
            subtitle_text=req.subtitle_text or req.title,
            timed_transcript=req.timed_transcript,
            credit_text=req.credit_text,
            device_id=req.device_id,
            device_name=req.device_name,
            client_ip=client_ip,
            title=req.title,
            description=req.description,
            channel_name=req.channel_name,
            source_url=effective_source_url,
            author=req.author,
            mode=req.mode,
        )
        return result
    except Exception as e:
        logger.error(f"Lỗi render repurposed short: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/repurposer/clips")
def list_repurposed_clips() -> Dict[str, Any]:
    """
    Lấy danh sách các Shorts đã được trích xuất thành công kèm thông tin thiết bị tạo, chỉ số QA và link Amazon.
    Tương thích ngược cả 2 định dạng file: [name].meta.json và [stem].meta.json.
    """
    from app.services.video_repurposer import CLIPS_DIR
    clips = []
    if CLIPS_DIR.exists():
        for f in CLIPS_DIR.glob("*.mp4"):
            try:
                # Tương thích ngược: Ưu tiên [stem].meta.json, nếu không thấy thì thử [name].meta.json
                meta_path = f.with_name(f"{f.stem}.meta.json")
                if not meta_path.exists():
                    meta_path = f.with_name(f"{f.name}.meta.json")

                meta_info = {}
                if meta_path.exists():
                    try:
                        with open(meta_path, "r", encoding="utf-8") as mf:
                            meta_info = json.load(mf)
                    except Exception:
                        pass
                
                from app.services.monetization_engine import (
                    build_repurposed_pinned_comment,
                    build_repurposed_video_description,
                    find_best_amazon_product,
                    build_amazon_url,
                    verify_amazon_product_alive
                )
                amz_prod = meta_info.get("amazon_product")
                needs_save = False

                # Tự động kiểm tra và làm mới nếu sản phẩm Amazon bị chết hoặc chưa kiểm tra
                if not amz_prod or not amz_prod.get("is_verified_alive"):
                    prod_asin = (amz_prod.get("asin") if amz_prod else "") or ""
                    if not prod_asin or not verify_amazon_product_alive(prod_asin):
                        niche_cur = meta_info.get("niche_id", "general")
                        title_cur = meta_info.get("title") or f.stem
                        amz_prod = find_best_amazon_product(niche=niche_cur, title=title_cur)
                        amz_prod["affiliate_url"] = build_amazon_url(
                            amz_prod["asin"],
                            "kizakawa-20",
                            is_search=amz_prod.get("is_search", False)
                        )
                        meta_info["amazon_product"] = amz_prod
                        meta_info["pinned_comment"] = build_repurposed_pinned_comment(
                            title=title_cur,
                            niche_id=niche_cur,
                            source_url=meta_info.get("source_url", ""),
                            author=meta_info.get("author", ""),
                            tracking_tag="kizakawa-20"
                        )
                        meta_info["description"] = build_repurposed_video_description(
                            title=title_cur,
                            summary=meta_info.get("subtitle_text", ""),
                            niche_id=niche_cur,
                            channel_name="",
                            source_url=meta_info.get("source_url", ""),
                            author=meta_info.get("author", ""),
                            mode=meta_info.get("mode", "self_owned"),
                            tracking_tag="kizakawa-20"
                        )
                        needs_save = True

                clip_pinned = meta_info.get("pinned_comment")
                if not clip_pinned:
                    clip_pinned = build_repurposed_pinned_comment(
                        title=meta_info.get("title") or f.stem,
                        niche_id=meta_info.get("niche_id", "general"),
                        source_url=meta_info.get("source_url", ""),
                        author=meta_info.get("author", ""),
                        tracking_tag="kizakawa-20"
                    )
                    meta_info["pinned_comment"] = clip_pinned
                    needs_save = True

                if needs_save:
                    try:
                        with open(meta_path, "w", encoding="utf-8") as mf:
                            json.dump(meta_info, mf, ensure_ascii=False, indent=2)
                    except Exception:
                        pass

                clips.append({
                    "filename": f.name,
                    "url": f"/media/repurposer/clips/{f.name}",
                    "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
                    "created_at": meta_info.get("created_at") or f.stat().st_mtime,
                    "device_id": meta_info.get("device_id", ""),
                    "device_name": meta_info.get("device_name", ""),
                    "client_ip": meta_info.get("client_ip", ""),
                    "title": meta_info.get("title") or f.stem,
                    "description": meta_info.get("description", ""),
                    "pinned_comment": meta_info.get("pinned_comment") or clip_pinned,
                    "niche_id": meta_info.get("niche_id", "general"),
                    "layout_mode": meta_info.get("layout_mode", "smart_crop_full"),
                    "audio_mode": meta_info.get("audio_mode", "original"),
                    "qa_report": meta_info.get("qa_report"),
                    "amazon_product": amz_prod,
                    "source_url": meta_info.get("source_url", ""),
                    "author": meta_info.get("author", ""),
                    "mode": meta_info.get("mode", "self_owned"),
                    "publish_status": meta_info.get("publish_status") or "draft",
                    "youtube_url": meta_info.get("youtube_url") or "",
                    "shorts_url": meta_info.get("shorts_url") or "",
                    "video_id": meta_info.get("video_id") or "",
                    "scheduled_at": meta_info.get("scheduled_at") or "",
                    "published_at": meta_info.get("published_at") or "",
                    "published_channel_name": meta_info.get("published_channel_name") or "",
                })
            except Exception:
                pass
    clips.sort(key=lambda x: x["created_at"], reverse=True)
    return {"success": True, "count": len(clips), "clips": clips}

@app.delete("/api/repurposer/clips/{filename}")
def delete_repurposed_clip(filename: str) -> Dict[str, Any]:
    """
    Xóa video Shorts đã tạo cùng toàn bộ file metadata (.meta.json) và subtitle (.ass) đi kèm.
    Bảo vệ chống Path Traversal (kiểm tra an toàn đường dẫn).
    """
    from app.services.video_repurposer import CLIPS_DIR, SUBS_DIR
    # Kiểm tra an toàn: cấm path traversal
    safe_filename = os.path.basename(filename).strip()
    if not safe_filename or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Tên file không hợp lệ")

    if not safe_filename.endswith(".mp4"):
        raise HTTPException(status_code=400, detail="Chỉ cho phép xóa file định dạng .mp4")

    target_video = CLIPS_DIR / safe_filename
    if not target_video.exists():
        raise HTTPException(status_code=404, detail=f"Không tìm thấy video: {safe_filename}")

    try:
        # Xóa file video MP4
        target_video.unlink(missing_ok=True)

        # Xóa cả 2 định dạng file metadata để dọn sạch hoàn toàn
        meta_stem = CLIPS_DIR / f"{Path(safe_filename).stem}.meta.json"
        meta_full = CLIPS_DIR / f"{safe_filename}.meta.json"
        meta_stem.unlink(missing_ok=True)
        meta_full.unlink(missing_ok=True)

        # Xóa file phụ đề ASS nếu có
        ass_file = SUBS_DIR / f"sub_{Path(safe_filename).stem}.ass"
        ass_file.unlink(missing_ok=True)

        logger.info(f"Đã xóa thành công clip và metadata: {safe_filename}")
        return {"success": True, "message": f"Đã xóa video {safe_filename} thành công"}
    except Exception as e:
        logger.error(f"Lỗi khi xóa clip {safe_filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi xóa file: {str(e)}")

# ─── Publish Repurposed Short to YouTube ─────────────────────────────────────

class RepurposePublishRequest(BaseModel):
    clip_filename: str           # Tên file clip trong CLIPS_DIR, VD: short_dev_17...abc123.mp4
    channel_id: str              # ID kênh YouTube đích (VD: ch_paws_hearts_rescue)
    title: str                   # Tiêu đề video khi đăng
    description: str = ""        # Mô tả video (để trống = dùng mô tả mặc định kênh)
    tags: str = ""               # Tags phân cách bởi dấu phẩy
    auto_schedule: bool = False  # True = hẹn giờ vàng tự động (private scheduled)
    pinned_comment: Optional[str] = None # Bình luận ghim kèm affiliate Store ID kizakawa-20

@app.post("/api/repurposer/publish-clip")
async def publish_repurposed_clip(req: RepurposePublishRequest) -> Dict[str, Any]:
    """
    Đăng Short đã được render (9:16) thẳng lên YouTube không cần VideoJob.
    Hỗ trợ: đăng ngay (public) hoặc hẹn giờ vàng tự động (auto_schedule=true).
    """
    from app.services.video_repurposer import CLIPS_DIR
    from pathlib import Path as _Path

    clip_path = CLIPS_DIR / req.clip_filename
    if not clip_path.exists():
        raise HTTPException(status_code=404, detail=f"Không tìm thấy clip: {req.clip_filename}")

    try:
        cfg = _read_channels_yaml()
        channels = cfg.get("channels", [])
        ch_cfg = next((c for c in channels if c.get("id") == req.channel_id), None)
        if not ch_cfg:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy kênh: {req.channel_id}")

        acc_id = ch_cfg.get("google_account", "account_1")
        ch_lang = ch_cfg.get("language", "en")
        ch_name = ch_cfg.get("name", req.channel_id)

        # Tìm token file cho kênh
        base_dir = Path(settings.BASE_DIR)
        ch_token = base_dir / "credentials" / "channels" / req.channel_id / "youtube_token.json"
        acc_token = base_dir / "credentials" / "accounts" / acc_id / "youtube_token.json"
        token_file = str(ch_token) if ch_token.exists() else str(acc_token)

        ch_cfg["token_file"] = token_file

        from app.agents.publisher_agent import PublisherAgent
        pub_agent = PublisherAgent(channel_config=ch_cfg, account_name=acc_id)
        if not pub_agent.service:
            init_err = getattr(pub_agent, "init_error", None) or "Chưa kết nối OAuth"
            raise HTTPException(
                status_code=503,
                detail=(
                    f"⚠️ Kênh '{ch_name}' chưa kết nối YouTube OAuth ({init_err}). "
                    f"Vui lòng vào tab 'Kênh & Tài Khoản' → '🔑 Cấp Quyền YouTube (Kênh Này)' để xác thực lại."
                )
            )

        # Tính giờ vàng nếu auto_schedule
        schedule_time = None
        display_slot_vn = None
        privacy = "public"
        if req.auto_schedule:
            from app.services.smart_scheduler import get_next_available_golden_slot
            _, schedule_time, display_slot_vn, _ = get_next_available_golden_slot(req.channel_id)
            privacy = "private"

        # Chuẩn bị tags
        video_tags = [t.strip() for t in req.tags.split(",") if t.strip()] if req.tags else []

        # Mô tả mặc định nếu để trống
        description = req.description.strip() if req.description else (
            f"Short viral được trích xuất bởi AI từ video gốc của kênh {ch_name}. "
            f"Subscribe để xem thêm nội dung độc đáo! 🔔"
        )

        # Bình luận ghim tự động (kèm store ID kizakawa-20)
        pinned_comment = req.pinned_comment.strip() if req.pinned_comment else None
        if not pinned_comment:
            meta_stem = CLIPS_DIR / f"{_Path(req.clip_filename).stem}.meta.json"
            if meta_stem.exists():
                try:
                    with open(meta_stem, "r", encoding="utf-8") as mf:
                        m_data = json.load(mf)
                        pinned_comment = m_data.get("pinned_comment")
                except Exception:
                    pass

        if not pinned_comment:
            from app.services.monetization_engine import build_repurposed_pinned_comment
            pinned_comment = build_repurposed_pinned_comment(
                title=req.title,
                channel_name=ch_name,
                tracking_tag="kizakawa-20"
            )

        pub_res = pub_agent.upload_video(
            video_path=str(clip_path),
            title=req.title,
            description=description,
            tags=video_tags,
            privacy_status=privacy,
            schedule_time_iso=schedule_time,
            default_language=ch_lang,
            default_audio_language=ch_lang,
            pinned_comment=pinned_comment,
            topic=req.title,
            script="",
        )

        if pub_res.get("status") == "success":
            yt_url = pub_res.get("youtube_url", "")
            yt_id = pub_res.get("video_id", "")
            shorts_url = f"https://www.youtube.com/shorts/{yt_id}" if yt_id else yt_url
            pub_status = "scheduled" if req.auto_schedule else "published"

            # Cập nhật metadata file của clip để lưu trạng thái
            meta_stem = CLIPS_DIR / f"{_Path(req.clip_filename).stem}.meta.json"
            if meta_stem.exists():
                try:
                    with open(meta_stem, "r", encoding="utf-8") as mf:
                        m_data = json.load(mf)
                    m_data["publish_status"] = pub_status
                    m_data["youtube_url"] = yt_url
                    m_data["shorts_url"] = shorts_url
                    m_data["video_id"] = yt_id
                    m_data["scheduled_at"] = display_slot_vn or None
                    m_data["published_at"] = datetime.now(timezone.utc).isoformat()
                    m_data["published_channel_name"] = ch_name
                    with open(meta_stem, "w", encoding="utf-8") as mf:
                        json.dump(m_data, mf, ensure_ascii=False, indent=2)
                except Exception as ex:
                    logger.warning(f"Không thể ghi nhận publish_status vào meta.json: {ex}")

            msg = (
                f"✅ Đã lên lịch đăng tại khe vàng {display_slot_vn}!"
                if req.auto_schedule
                else f"✅ Đã đăng công khai lên YouTube!"
            )
            return {
                "success": True,
                "message": msg,
                "publish_status": pub_status,
                "youtube_url": yt_url,
                "shorts_url": shorts_url,
                "video_id": yt_id,
                "scheduled_at": display_slot_vn or None,
                "channel_name": ch_name,
            }
        else:
            if pub_res.get("status") == "waiting_quota_reset":
                meta_stem = CLIPS_DIR / f"{_Path(req.clip_filename).stem}.meta.json"
                if meta_stem.exists():
                    try:
                        with open(meta_stem, "r", encoding="utf-8") as mf:
                            m_data = json.load(mf)
                        m_data["publish_status"] = "queued"
                        m_data["scheduled_at"] = "Chờ cấp hạn mức 14:00"
                        m_data["published_channel_name"] = ch_name
                        with open(meta_stem, "w", encoding="utf-8") as mf:
                            json.dump(m_data, mf, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
            raise HTTPException(status_code=500, detail=pub_res.get("error", "Lỗi không xác định từ YouTube API"))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi publish repurposed clip: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ─── Channel-Level OAuth Endpoints (Đảm bảo mỗi kênh YouTube có Token riêng) ─

@app.post("/api/channels/{channel_id}/start-auth")
async def start_channel_oauth(channel_id: str, request: Request, service: str = "youtube") -> Dict[str, Any]:
    """Bắt đầu phiên xác thực OAuth riêng biệt cho một kênh YouTube cụ thể."""
    client_ip = request.client.host if request.client else "127.0.0.1"
    is_local_client = client_ip in ("127.0.0.1", "localhost", "::1", "testclient")

    cfg = _read_channels_yaml()
    channels = cfg.get("channels", [])
    ch = next((c for c in channels if c.get("id") == channel_id), None)
    acc_id = "account_1"
    if ch:
        acc_id = ch.get("google_account", "account_1")
    else:
        # Tìm trong brand_channels của từng google_account
        for acc in cfg.get("google_accounts", []):
            for bc in acc.get("brand_channels", []):
                if bc.get("id") == channel_id:
                    ch = bc
                    acc_id = acc.get("id", "account_1")
                    break
            if ch:
                break
    if not ch:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh")

    ch_name = ch.get("name", channel_id)
    session_key = f"channel:{channel_id}:{service}"

    base_dir = Path(settings.BASE_DIR)
    secret_file = base_dir / "credentials" / "accounts" / acc_id / "client_secret.json"
    if not secret_file.exists():
        secret_file = base_dir / "credentials" / "accounts" / "account_1" / "client_secret.json"
    if not secret_file.exists():
        raise HTTPException(status_code=400, detail="Chưa có file client_secret.json trong tài khoản mẹ!")

    import webbrowser
    import wsgiref.simple_server
    import wsgiref.util
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = [
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.force-ssl",
        "https://www.googleapis.com/auth/userinfo.email",
        "openid"
    ]
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
    callback_url = f"{scheme}://{host}/api/oauth/callback"

    # Kiểm tra xem client_secret có phải loại Web application không và có khớp redirect_uris không
    is_web = False
    sec_json = {}
    try:
        with open(secret_file, "r", encoding="utf-8") as f:
            sec_json = json.load(f)
    except Exception:
        pass

    if "web" in sec_json:
        allowed_uris = sec_json.get("web", {}).get("redirect_uris", [])
        if callback_url in allowed_uris:
            is_web = True
        else:
            logger.info(f"Callback URL '{callback_url}' không nằm trong web redirect_uris ({allowed_uris}). Tự động dùng Desktop Flow cho kênh '{ch_name}'.")
            is_web = False

    if is_web:
        from google_auth_oauthlib.flow import Flow
        import secrets
        flow = Flow.from_client_config(sec_json, scopes=scopes, redirect_uri=callback_url)
        state_token = secrets.token_urlsafe(32)
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline", state=state_token)
        _oauth_web_states[state_token] = {
            "flow": flow,
            "session_key": session_key,
            "type": "channel",
            "channel_id": channel_id,
            "channel_name": ch_name,
            "acc_id": acc_id,
            "service": service,
            "created_at": time.time()
        }
        _oauth_sessions[session_key] = {
            "status": "in_progress",
            "channel_id": channel_id,
            "channel_name": ch_name,
            "service": service,
            "auth_url": auth_url,
            "message": f"Phiên xác thực 1-Click tự động cho kênh '{ch_name}' đã sẵn sàng."
        }
        return {
            "success": True,
            "auth_url": auth_url,
            "session_key": session_key,
            "status": "in_progress"
        }

    desktop_cfg = sec_json if "installed" in sec_json else DEFAULT_DESKTOP_CLIENT
    flow = InstalledAppFlow.from_client_config(desktop_cfg, scopes=scopes)
    received_uri = {"value": None}
    ch_name = ch.get("name", channel_id)
    success_page = (
        f"<html><body style='font-family:system-ui,sans-serif;text-align:center;padding:60px;"
        f"background:#1a1a2e;color:#e0e0e0'>"
        f"<h2 style='color:#34d399'>✅ Đã kết nối kênh '{ch_name}' thành công!</h2>"
        f"<p>Bạn có thể đóng tab này và quay lại Web Studio để đăng video.</p>"
        f"</body></html>"
    )

    def _wsgi_app(environ, start_response):
        received_uri["value"] = wsgiref.util.request_uri(environ)
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [success_page.encode("utf-8")]

    class _SilentHandler(wsgiref.simple_server.WSGIRequestHandler):
        def log_message(self, fmt, *args):
            pass

    local_server = wsgiref.simple_server.make_server("localhost", 0, _wsgi_app, handler_class=_SilentHandler)
    port = local_server.server_port
    flow.redirect_uri = f"http://localhost:{port}/"
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

    session_key = f"channel:{channel_id}:{service}"
    _oauth_sessions[session_key] = {
        "status": "starting",
        "channel_id": channel_id,
        "channel_name": ch.get("name", channel_id),
        "service": service,
        "auth_url": auth_url,
        "message": f"Link đăng nhập đã sẵn sàng. Khi Google hiển thị danh sách kênh, hãy chọn đúng kênh '{ch_name}'!"
    }

    if is_local_client:
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass
    else:
        logger.info(f"Yêu cầu OAuth kênh từ thiết bị khách ({client_ip}), không mở trình duyệt trên máy chủ.")

    def _channel_oauth_worker():
        try:
            # Chờ callback từ Google hoặc từ người dùng dán vào modal (timeout 3 phút)
            srv_thread = threading.Thread(target=local_server.handle_request, daemon=True)
            srv_thread.start()

            import time
            start_t = time.time()
            creds = None
            while time.time() - start_t < 180:
                auth_input = None
                if received_uri["value"]:
                    auth_input = received_uri["value"]
                    received_uri["value"] = None
                elif session_key in _oauth_manual_responses:
                    auth_input = _oauth_manual_responses.pop(session_key)

                if auth_input:
                    try:
                        creds = exchange_oauth_code_for_credentials(flow, auth_input)
                        if creds:
                            break
                    except Exception as ex:
                        logger.warning(f"Thử đổi mã OAuth kênh thất bại: {ex}")
                        _oauth_sessions[session_key]["status"] = "error"
                        _oauth_sessions[session_key]["error"] = str(ex)
                        break

                time.sleep(0.5)

            try:
                local_server.server_close()
            except Exception:
                pass

            if not creds:
                if _oauth_sessions.get(session_key, {}).get("status") != "error":
                    _oauth_sessions[session_key] = {
                        "status": "error",
                        "error": "Hết thời gian chờ xác thực (3 phút). Vui lòng thử lại."
                    }
                return

            # Đọc thông tin kênh YouTube thật từ token trả về
            real_yt_title = None
            real_yt_id = None
            custom_url = None
            try:
                from googleapiclient.discovery import build
                yt = build("youtube", "v3", credentials=creds)
                c_res = yt.channels().list(mine=True, part="snippet,id").execute()
                items = c_res.get("items", [])
                if items:
                    real_yt_id = items[0].get("id")
                    snippet = items[0].get("snippet", {})
                    real_yt_title = snippet.get("title")
                    custom_url = snippet.get("customUrl")
            except Exception as e:
                logger.warning(f"Không thể đọc thông tin kênh YouTube từ token: {e}")

            # Lưu token vào credentials/channels/{channel_id}/youtube_token.json
            ch_dir = base_dir / "credentials" / "channels" / channel_id
            ch_dir.mkdir(parents=True, exist_ok=True)
            token_file = ch_dir / "youtube_token.json"

            token_dict = json.loads(creds.to_json())
            token_dict["channel_id"] = real_yt_id or channel_id
            token_dict["channel_title"] = real_yt_title or ch_name
            if custom_url:
                token_dict["custom_url"] = custom_url

            save_encrypted_json(token_file, token_dict)

            # Cập nhật channels.yaml với thông tin thật
            cfg = _read_channels_yaml()
            for c in cfg.get("channels", []):
                if c.get("id") == channel_id:
                    if real_yt_title:
                        c["youtube_title"] = real_yt_title
                    if real_yt_id:
                        c["youtube_channel_id"] = real_yt_id
                        c["channel_url"] = f"https://www.youtube.com/channel/{real_yt_id}"
                    if custom_url:
                        c["handle"] = custom_url
                    break

            for acc in cfg.get("google_accounts", []):
                if acc.get("id") == acc_id:
                    for bch in acc.get("brand_channels", []):
                        if bch.get("id") == channel_id:
                            if real_yt_title:
                                bch["youtube_title"] = real_yt_title
                            if real_yt_id:
                                bch["youtube_channel_id"] = real_yt_id
                                bch["channel_url"] = f"https://www.youtube.com/channel/{real_yt_id}"
                            if custom_url:
                                bch["handle"] = custom_url
                            break
                    break
            _write_channels_yaml(cfg)

            succ_msg = f"Đã kết nối thành công kênh '{ch_name}'"
            if real_yt_title:
                succ_msg += f" (Kênh YouTube: {real_yt_title})"

            _oauth_sessions[session_key] = {
                "status": "success",
                "channel_id": channel_id,
                "real_title": real_yt_title or ch_name,
                "real_id": real_yt_id,
                "message": succ_msg
            }
            logger.info(f"OAuth thành công cho kênh {channel_id}: {succ_msg}")

        except Exception as ex:
            logger.error(f"Channel OAuth Worker Error: {ex}")
            _oauth_sessions[session_key] = {
                "status": "error",
                "error": str(ex)
            }

    t = threading.Thread(target=_channel_oauth_worker, daemon=True)
    t.start()

    return {
        "success": True,
        "auth_url": auth_url,
        "session_key": session_key,
        "status": "in_progress",
        "message": f"Đang mở trình duyệt. Khi Google hiển thị danh sách kênh, hãy chọn đúng kênh '{ch.get('name')}'!"
    }

@app.get("/api/channels/{channel_id}/auth-status")
async def get_channel_oauth_status(channel_id: str) -> Dict[str, Any]:
    """Kiểm tra tiến độ xác thực OAuth cho kênh riêng."""
    session_key = f"channel:{channel_id}:youtube"
    return _oauth_sessions.get(session_key, {"status": "idle", "message": "Chưa có phiên xác thực nào"})

@app.post("/api/channels/{channel_id}/disconnect")
async def disconnect_channel(channel_id: str) -> Dict[str, Any]:
    """Ngắt kết nối YouTube riêng cho kênh."""
    if not is_safe_identifier(channel_id):
        raise HTTPException(status_code=400, detail="Mã kênh không hợp lệ.")

    base_dir = Path(settings.BASE_DIR)
    channels_root = (base_dir / "credentials" / "channels").resolve()
    ch_dir = (channels_root / channel_id).resolve()
    if not is_safe_path(ch_dir, channels_root):
        raise HTTPException(status_code=400, detail="Đường dẫn không hợp lệ.")

    ch_token = ch_dir / "youtube_token.json"
    if ch_token.exists():
        ch_token.unlink()
    return {"success": True, "message": f"Đã ngắt kết nối YouTube cho kênh '{channel_id}'"}


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


# ═════════════════════════════════════════════════════════════════════════════
# ─── CHANNEL INTELLIGENCE & KPI MANAGEMENT API ──────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/api/channels/{channel_id}/topics/suggest")
async def suggest_channel_topics_endpoint(channel_id: str, count: int = 5):
    """
    Gợi ý ý tưởng video Shorts độc lạ cho kênh, chống trùng lặp 100% với video cũ.
    """
    try:
        from app.services.channel_intelligence import suggest_channel_topics
        ideas = suggest_channel_topics(channel_id=channel_id, count=count)
        return {
            "success": True,
            "channel_id": channel_id,
            "count": len(ideas),
            "ideas": ideas
        }
    except Exception as e:
        logger.error(f"Lỗi gợi ý ý tưởng kênh {channel_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/channels/kpi/report")
async def get_kpi_report_endpoint():
    """
    Báo cáo sức khỏe KPI toàn bộ các kênh YouTube:
    - Danh sách kênh đúng hạn, cảnh báo, trễ deadline >= 2 ngày.
    """
    try:
        from app.services.channel_intelligence import run_network_kpi_audit
        audit = run_network_kpi_audit()
        return {
            "success": True,
            "report": audit
        }
    except Exception as e:
        logger.error(f"Lỗi tạo báo cáo KPI: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

class AutoGenerateKPIRequest(BaseModel):
    grace_hours: Optional[float] = 4.0
    force: Optional[bool] = False

@app.post("/api/channels/{channel_id}/kpi/auto-generate")
async def auto_generate_kpi_video(channel_id: str, req: Optional[AutoGenerateKPIRequest] = None):
    """
    Tự động sản xuất video cứu tương tác cho kênh bị trễ deadline 2 ngày (hoặc force=True).
    """
    try:
        from app.services.channel_intelligence import trigger_auto_generation_for_overdue_channel
        grace = req.grace_hours if req and req.grace_hours else 4.0
        force = req.force if req and req.force is not None else False
        res = trigger_auto_generation_for_overdue_channel(channel_id, grace_hours=grace, force=force)
        return res
    except Exception as e:
        logger.error(f"Lỗi tự động sản xuất video cho kênh {channel_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

class ApproveJobRequest(BaseModel):
    action: str = "schedule"  # "now", "schedule", "cancel"

@app.post("/api/videos/{job_id}/approve")
async def approve_video_job(job_id: int, req: Optional[ApproveJobRequest] = None):
    """
    Duyệt video tự động tạo (AWAITING_APPROVAL):
    - action='now': Duyệt để đăng ngay.
    - action='schedule': Xếp lịch vào khung giờ vàng chuẩn quốc tế.
    - action='cancel': Huỷ bỏ và chuyển trạng thái về DRAFT.
    """
    action = (req.action if req else "schedule").lower()
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")

        target_ch = job.target_channel_id

        if action == "cancel":
            job.status = "DRAFT"
            job.progress_message = "Người dùng đã từ chối/hủy duyệt video tự động"
            log_activity(db, 0, "Web_User", "CANCEL_AUTO_JOB", f"Người dùng hủy duyệt Video #{job_id}", channel_id=target_ch)
            return {"success": True, "message": f"Đã hủy duyệt video #{job_id}"}

        if action == "schedule":
            from app.services.smart_scheduler import get_next_available_golden_slot
            slot_dt, slot_iso, vn_label, us_label = get_next_available_golden_slot(target_ch)
            job.status = "SCHEDULED"
            job.scheduled_time = slot_dt
            job.progress_message = f"Đã duyệt: Lên lịch công chiếu lúc {vn_label} ({us_label})"
            log_activity(db, 0, "Web_User", "APPROVE_SCHEDULE", f"Duyệt Video #{job_id} lên lịch lúc {vn_label}", channel_id=target_ch)
            return {
                "success": True,
                "message": f"Đã duyệt và lên lịch công chiếu Video #{job_id} lúc {vn_label}",
                "scheduled_slot_vn": vn_label,
                "scheduled_slot_us": us_label
            }

        elif action == "now":
            job.status = "RENDERED"
            log_activity(db, 0, "Web_User", "APPROVE_NOW", f"Duyệt Video #{job_id} để xuất bản", channel_id=target_ch)
            return {
                "success": True,
                "message": f"Đã duyệt Video #{job_id} sẵn sàng xuất bản!"
            }
        else:
            raise HTTPException(status_code=400, detail=f"Hành động '{action}' không hợp lệ (hỗ trợ: now, schedule, cancel)")


# ═════════════════════════════════════════════════════════════════════════════
# ─── MANUAL STUDIO (GHÉP CẢNH TÙY CHỈNH) API ─────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════

def _execute_manual_render(job_id: int, mode: str, video_path: Optional[str], script_text: str, scenes_items: list, config_item: Any):
    """Worker chạy ngầm cho tác vụ ghép / lồng tiếng video tùy chỉnh."""
    from app.services.manual_studio_service import ManualStudioService
    from app.database.models import get_db, VideoJob, log_activity

    service = ManualStudioService()

    def db_progress(percent: int, message: str):
        try:
            with get_db() as db:
                j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if j:
                    j.progress_percent = percent
                    j.progress_message = message
        except Exception:
            pass

    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        if mode == "single_video" and video_path:
            final_video_path = loop.run_until_complete(
                service.render_single_video_studio(
                    job_id=job_id,
                    video_path=video_path,
                    script_text=script_text,
                    config=config_item,
                    progress_callback=db_progress
                )
            )
        else:
            final_video_path = loop.run_until_complete(
                service.render_manual_video(
                    job_id=job_id,
                    scenes=scenes_items,
                    config=config_item,
                    progress_callback=db_progress
                )
            )
        loop.close()

        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if j:
                j.status = "RENDERED"
                j.video_path = final_video_path
                j.progress_percent = 100
                j.progress_message = "Lồng tiếng & gắn phụ đề hoàn tất!"
                log_activity(db, 0, "Web Studio", "MANUAL_RENDER", f"Hoàn tất video #{job_id}: {j.title}")

    except Exception as e:
        logger.error(f"Lỗi render manual video #{job_id}: {e}", exc_info=True)
        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if j:
                j.status = "FAILED"
                j.progress_message = f"Lỗi: {str(e)[:100]}"
                j.error_message = str(e)
                log_activity(db, 0, "Web Studio", "MANUAL_RENDER_FAILED", f"Lỗi video #{job_id}: {e}")


@app.get("/api/manual-studio/sfx")
async def get_manual_studio_sfx():
    """Lấy danh sách tất cả hiệu ứng âm thanh SFX có sẵn trong hệ thống."""
    try:
        from app.services.manual_studio_service import ManualStudioService
        svc = ManualStudioService()
        available = svc.get_available_sfx()
        options = [
            {"id": "auto", "name": "🤖 AI Tự Chọn Thông Minh", "filename": "auto"},
            {"id": "none", "name": "🔇 Tắt SFX Ở Cảnh Này", "filename": "none"}
        ] + available
        return {"success": True, "sfx": options}
    except Exception as e:
        logger.warning(f"Lỗi đọc sfx list: {e}")
        return {
            "success": True,
            "sfx": [
                {"id": "auto", "name": "🤖 AI Tự Chọn Thông Minh", "filename": "auto"},
                {"id": "none", "name": "🔇 Tắt SFX Ở Cảnh Này", "filename": "none"},
                {"id": "whoosh", "name": "Whoosh", "filename": "whoosh.mp3"},
                {"id": "hit", "name": "Hit", "filename": "hit.wav"}
            ]
        }


@app.get("/api/manual-studio/voices")
async def get_manual_studio_voices():
    """Lấy danh sách giọng đọc AI được hỗ trợ tốt nhất."""
    voices = [
        {"id": "en-US-AndrewNeural", "name": "🇺🇸 English - Andrew (Dynamic & Natural - Default)", "lang": "en"},
        {"id": "en-US-ChristopherNeural", "name": "🇺🇸 English - Christopher (Deep Cinematic)", "lang": "en"},
        {"id": "en-US-GuyNeural", "name": "🇺🇸 English - Guy (Dynamic Storyteller)", "lang": "en"},
        {"id": "en-US-JennyNeural", "name": "🇺🇸 English - Jenny (Warm & Natural)", "lang": "en"},
        {"id": "en-US-AriaNeural", "name": "🇺🇸 English - Aria (Engaging & Clear)", "lang": "en"},
        {"id": "vi-VN-NamMinhNeural", "name": "🇻🇳 Tiếng Việt - Nam Minh (Nam trầm ấm)", "lang": "vi"},
        {"id": "vi-VN-HoaiMyNeural", "name": "🇻🇳 Tiếng Việt - Hoài My (Nữ truyền cảm)", "lang": "vi"}
    ]
    return {"success": True, "voices": voices}


class VoicePreviewRequest(BaseModel):
    voice: str = "en-US-AndrewNeural"
    rate: str = "+0%"
    pitch: str = "+0Hz"
    text: Optional[str] = None


@app.post("/api/manual-studio/preview-voice")
async def preview_voice_sample(req: VoicePreviewRequest):
    """
    Sinh đoạn audio nghe thử giọng đọc AI theo nội dung kịch bản hoặc văn bản mẫu,
    trả về trực tiếp stream MP3 để trình duyệt phát ra loa ngay lập tức.
    """
    import edge_tts

    voice = req.voice or "en-US-AndrewNeural"
    rate = req.rate if (req.rate and req.rate != "+0%") else None
    pitch = req.pitch if (req.pitch and req.pitch != "+0Hz") else None

    # Lấy văn bản nghe thử
    raw_text = (req.text or "").strip()
    if not raw_text:
        if voice.startswith("vi"):
            text = "Xin chào! Đây là giọng đọc thử nghiệm của tôi cho video của bạn. Hãy cùng bắt đầu sáng tạo nội dung ngay bây giờ!"
        else:
            text = "Hello! This is a preview of my voice for your video. Let's create something captivating together!"
    else:
        # Nếu kịch bản dài, trích xuất câu đầu tiên (hoặc tối đa 25 từ) để tạo preview siêu nhanh
        first_line = raw_text.split("\n")[0].strip()
        sentences = [s.strip() for s in first_line.split(".") if s.strip()]
        snippet = sentences[0] if sentences else first_line
        words = snippet.split()
        if len(words) > 25:
            text = " ".join(words[:25]) + "..."
        else:
            text = snippet

    audio_bytes = bytearray()
    last_err = None

    for attempt in range(1, 3):
        try:
            kwargs = {"text": text, "voice": voice}
            if rate and attempt == 1:
                kwargs["rate"] = rate
            if pitch and attempt == 1:
                kwargs["pitch"] = pitch

            communicate = edge_tts.Communicate(**kwargs)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_bytes.extend(chunk["data"])
            if len(audio_bytes) > 0:
                break
        except Exception as e:
            last_err = e
            logger.warning(f"Preview voice attempt {attempt} error: {e}")

    if len(audio_bytes) == 0:
        logger.error(f"Preview voice failed: {last_err}")
        raise HTTPException(status_code=500, detail=f"Không thể sinh giọng đọc mẫu: {last_err}")

    return Response(content=bytes(audio_bytes), media_type="audio/mpeg")


@app.get("/api/manual-studio/bgm")
async def get_manual_studio_bgm():
    """Lấy danh sách nhạc nền (BGM) có sẵn."""
    bgm_list = []
    base_dir = Path(__file__).resolve().parent.parent.parent
    for candidate in [base_dir / "assets" / "bgm", base_dir / "assets" / "audio" / "bgm"]:
        if candidate.exists():
            for f in sorted(candidate.iterdir(), key=lambda x: x.name.lower()):
                if f.is_file() and f.suffix.lower() in [".mp3", ".wav", ".ogg", ".m4a"]:
                    bgm_list.append({
                        "id": f.stem,
                        "name": f.stem.replace("_", " ").replace("-", " ").title(),
                        "path": str(f)
                    })
    return {"success": True, "bgm": bgm_list}


@app.post("/api/manual-studio/create")
async def create_manual_studio_video(
    background_tasks: BackgroundTasks,
    payload: str = Form(...),
    files: List[UploadFile] = File(...)
):
    """
    Tiếp nhận video tải lên (từ Google Flow, CapCut...) và lời thoại kịch bản,
    khởi tạo VideoJob trong SQLite và bắt đầu tiến trình lồng tiếng + phụ đề ngầm.
    """
    try:
        data = json.loads(payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Dữ liệu payload JSON không hợp lệ: {e}")

    title = data.get("title", "").strip() or "Shorts Lồng Tiếng"
    channel_id = data.get("target_channel_id", "channel_1")
    mode = data.get("mode", "single_video")
    script_text = data.get("script", "").strip()
    scenes_spec = data.get("scenes", [])

    if not script_text and scenes_spec:
        script_text = "\n".join([s.get("narration", "") for s in scenes_spec if s.get("narration")])

    if not script_text:
        raise HTTPException(status_code=400, detail="Vui lòng nhập lời thoại kịch bản cho video")

    if len(files) == 0:
        raise HTTPException(status_code=400, detail="Chưa có file media nào được tải lên")

    # 1. Tạo bản ghi VideoJob trong Database
    with get_db() as db:
        job = VideoJob(
            user_id=0,
            target_channel_id=channel_id,
            topic=f"[Lồng Tiếng] {title}",
            title=title,
            script=script_text[:500],
            status="RENDERING",
            progress_percent=5,
            progress_message="Đang tiếp nhận tệp tin và chuẩn bị lồng tiếng..."
        )
        db.add(job)
        db.flush()
        job_id = job.id
        log_activity(db, 0, "Web Studio", "MANUAL_STUDIO_CREATE", f"Khởi tạo tác vụ lồng tiếng video #{job_id}: {title}")

    # 2. Lưu file upload
    from app.services.manual_studio_service import ManualSceneItem, ManualStudioConfig
    base_dir = Path(__file__).resolve().parent.parent.parent
    upload_dir = base_dir / "storage" / "temp" / f"manual_{job_id}" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    ALLOWED_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".jpg", ".jpeg", ".png", ".webp", ".mp3", ".wav"}
    MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB

    saved_file_paths = []
    for idx, f in enumerate(files):
        ext = Path(f.filename).suffix.lower() if f.filename else ".mp4"
        if ext not in ALLOWED_EXTS:
            raise HTTPException(status_code=400, detail=f"Định dạng tệp '{ext}' không được phép. Chỉ chấp nhận video, hình ảnh hoặc âm thanh.")
        save_path = upload_dir / f"input_media_{idx:03d}{ext}"
        contents = await f.read()
        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"Tệp '{f.filename}' vượt quá dung lượng tối đa cho phép 500MB.")
        with open(save_path, "wb") as out_f:
            out_f.write(contents)
        saved_file_paths.append(str(save_path))

    # 3. Cấu hình studio
    bgm_path = data.get("bgm_path")
    if bgm_path and not Path(bgm_path).exists():
        bgm_path = None

    config = ManualStudioConfig(
        title=title,
        voice=data.get("voice", "en-US-AndrewNeural"),
        voice_rate=data.get("voice_rate", "+0%"),
        voice_pitch=data.get("voice_pitch", "+0Hz"),
        bgm_path=bgm_path,
        bgm_volume=float(data.get("bgm_volume", 0.12)),
        use_ducking=bool(data.get("use_ducking", True)),
        subtitle_enabled=bool(data.get("subtitle_enabled", True)),
        subtitle_font=data.get("subtitle_font", "Arial"),
        subtitle_font_size=int(data.get("subtitle_font_size", 40)),
        subtitle_margin_v=int(data.get("subtitle_margin_v", 210)),
        transition_type=data.get("transition_type", "fade"),
        transition_duration=float(data.get("transition_duration", 0.25)),
        use_sfx=bool(data.get("use_sfx", True)),
        timing_mode=str(data.get("timing_mode", "auto"))
    )

    # 4. Đưa vào luồng render ngầm
    if mode == "single_video":
        primary_video_path = saved_file_paths[0]
        background_tasks.add_task(
            _execute_manual_render,
            job_id,
            "single_video",
            primary_video_path,
            script_text,
            [],
            config
        )
    else:
        # Chế độ nhiều scene
        all_narrations_empty = all(not (s.get("narration") or "").strip() for s in scenes_spec)
        if all_narrations_empty and script_text:
            from app.services.manual_studio_service import ManualStudioService
            parsed_parts = ManualStudioService().parse_multi_scene_script(script_text, len(scenes_spec), data.get("timing_mode", "auto"))
            for idx, part in enumerate(parsed_parts):
                if idx < len(scenes_spec):
                    scenes_spec[idx]["narration"] = part.get("narration", "")
                    if part.get("duration"):
                        scenes_spec[idx]["manual_duration_seconds"] = part["duration"]

        manual_scenes = []
        for i, sc in enumerate(scenes_spec):
            file_idx = sc.get("file_index", i)
            if file_idx >= len(saved_file_paths):
                file_idx = 0
            media_path = saved_file_paths[file_idx]

            manual_scenes.append(ManualSceneItem(
                scene_id=i + 1,
                media_path=media_path,
                narration=sc.get("narration", "").strip(),
                camera_motion=sc.get("camera_motion", "zoom_in"),
                transition=sc.get("transition", "fade"),
                sfx_cue=sc.get("sfx_cue", "auto"),
                manual_duration_seconds=float(sc.get("manual_duration_seconds")) if sc.get("manual_duration_seconds") else None
            ))
        background_tasks.add_task(
            _execute_manual_render,
            job_id,
            "multi_scene",
            None,
            script_text,
            manual_scenes,
            config
        )

    return {
        "success": True,
        "job_id": job_id,
        "message": f"Tác vụ #{job_id} đã được khởi tạo! Hệ thống đang lồng giọng đọc AI và phụ đề trong nền..."
    }

