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
from datetime import datetime, timezone
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

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

import hmac
import html as html_lib

_REVOKED_CLIENT_ID_PREFIXES = ("75721912954-",)


def _safe_env_client_id(env_key: str, default_val: str) -> str:
    val = (os.getenv(env_key) or "").strip()
    if not val or any(val.startswith(p) for p in _REVOKED_CLIENT_ID_PREFIXES):
        return default_val
    return val


DEFAULT_DESKTOP_CLIENT = {
    "installed": {
        "client_id": _safe_env_client_id("GOOGLE_DESKTOP_CLIENT_ID", "972622851789-4175b1dquscrk08j2aq4jb8s84llh4uo.apps.googleusercontent.com"),
        "project_id": os.getenv("GOOGLE_OAUTH_PROJECT_ID", "bionic-client-509515-h2"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_secret": os.getenv("GOOGLE_DESKTOP_CLIENT_SECRET", ""),
        "redirect_uris": ["http://localhost"]
    }
}

DEFAULT_WEB_CLIENT = {
    "web": {
        "client_id": _safe_env_client_id("GOOGLE_WEB_CLIENT_ID", "1094548290078-e7hitr7arc2d3qs2tt2h6f267msfah98.apps.googleusercontent.com"),
        "project_id": os.getenv("GOOGLE_OAUTH_PROJECT_ID", "effective-cacao-511414-q3"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_secret": os.getenv("GOOGLE_WEB_CLIENT_SECRET", ""),
        "redirect_uris": [
            os.getenv("GOOGLE_WEB_REDIRECT_URI", "http://localhost:8000/api/oauth/callback")
        ]
    }
}


def _is_valid_oauth_client_config(data: Optional[Dict[str, Any]]) -> bool:
    """Kiểm tra cấu hình client_secret.json có hợp lệ và không phải OAuth client đã bị xóa."""
    if not isinstance(data, dict):
        return False
    info = data.get("installed") or data.get("web")
    if not isinstance(info, dict):
        return False
    cid = str(info.get("client_id") or "").strip()
    csec = str(info.get("client_secret") or "").strip()
    if not cid or not csec:
        return False
    if any(cid.startswith(prefix) for prefix in _REVOKED_CLIENT_ID_PREFIXES):
        return False
    return True


def _resolve_and_load_client_secret(base_dir: Path, preferred_acc_ids: List[str]) -> tuple[Optional[Path], Dict[str, Any]]:
    """
    Tải và giải mã an toàn client_secret.json (hỗ trợ cả AES-256-GCM và JSON thuần).
    Tự động khôi phục từ bản backup riêng (nếu có) trước khi fallback sang các tài khoản dự phòng,
    đảm bảo mỗi Google Cloud Project trong Quota Pool giữ nguyên client_id độc lập.
    """
    search_order: List[str] = []
    for acc in list(preferred_acc_ids) + ["acount_2", "account_1", "accout3", "acccount4", "account_2", "account_3", "project_pool_2"]:
        acc_clean = (acc or "").strip()
        if acc_clean and acc_clean not in search_order:
            search_order.append(acc_clean)

    accounts_root = base_dir / "credentials" / "accounts"
    chosen_path: Optional[Path] = None
    chosen_cfg: Dict[str, Any] = {}

    for acc in search_order:
        acc_folder = accounts_root / acc
        candidate_files = [
            acc_folder / "client_secret.json",
            acc_folder / "client_secret_bionic_backup.json",
            acc_folder / "client_secret.json.json",
        ]
        if acc == "account_1":
            candidate_files.append(base_dir / "credentials" / "client_secret_bionic_backup.json")
        for cand in candidate_files:
            if not cand.exists():
                continue
            try:
                loaded = load_encrypted_json(cand)
                if _is_valid_oauth_client_config(loaded):
                    chosen_path = cand
                    chosen_cfg = loaded
                    primary_secret_file = acc_folder / "client_secret.json"
                    if cand != primary_secret_file:
                        try:
                            primary_secret_file.parent.mkdir(parents=True, exist_ok=True)
                            save_encrypted_json(primary_secret_file, loaded)
                            chosen_path = primary_secret_file
                            logger.info(f"🔄 [OAuth Self-Heal] Đã khôi phục client_secret.json gốc cho '{acc}' từ '{cand.name}'")
                        except Exception:
                            pass
                    break
            except Exception as ex:
                logger.warning(f"Lỗi đọc client_secret tại {cand}: {ex}")
        if chosen_cfg:
            break

    if chosen_cfg and preferred_acc_ids:
        target_acc = (preferred_acc_ids[0] or "").strip()
        if target_acc and is_safe_identifier(target_acc):
            target_file = accounts_root / target_acc / "client_secret.json"
            try:
                existing = load_encrypted_json(target_file) if target_file.exists() else None
                if not _is_valid_oauth_client_config(existing):
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    save_encrypted_json(target_file, chosen_cfg)
                    chosen_path = target_file
                    logger.info(f"🔄 [OAuth Self-Heal] Đã tự động đồng bộ client_secret.json hợp lệ cho '{target_acc}'")
            except Exception as heal_err:
                logger.warning(f"Không thể tự động đồng bộ client_secret cho {target_acc}: {heal_err}")

    return chosen_path, chosen_cfg

app = FastAPI(
    title="AI YouTube Multi-Channel Studio",
    description="Giao diện Web quản lý hệ thống tự động hoá YouTube",
    version="2.0.0"
)

_extra_cors = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_extra_cors,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\]|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?$|^https://[a-zA-Z0-9-]+\.(trycloudflare\.com|onrender\.com|ngrok-free\.app)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def device_gatekeeper_middleware(request: Request, call_next):
    """Bảo mật 2 lớp: Thiết bị từ xa (khác localhost trực tiếp) phải có token đã được Admin duyệt trên Telegram mới được gọi API"""
    from app.services.device_auth import is_gatekeeper_enabled, extract_client_ip, is_device_token_valid

    if not is_gatekeeper_enabled():
        return await call_next(request)

    path = request.url.path
    expected_agent_key = (getattr(settings, "AGENT_API_KEY", "") or "").strip()

    # Cho phép AI Agent có key hợp lệ hoặc truy cập các endpoint /api/agent (nơi có Depends(verify_agent_auth))
    agent_key = (
        request.headers.get("X-Agent-Key")
        or request.headers.get("X-Agent-Secret")
        or request.query_params.get("agent_key")
    )
    auth_hdr = request.headers.get("Authorization", "")
    if auth_hdr.startswith("Bearer "):
        bearer_val = auth_hdr[7:].strip()
        if expected_agent_key and hmac.compare_digest(bearer_val, expected_agent_key):
            agent_key = bearer_val

    if agent_key and expected_agent_key and hmac.compare_digest(agent_key.strip(), expected_agent_key):
        return await call_next(request)

    if path.startswith("/api/agent"):
        return await call_next(request)

    # Chỉ miễn trừ tài nguyên giao diện tĩnh, trang chủ, endpoint kiểm tra trạng thái 2FA và OAuth redirect callback
    if (
        path.startswith("/static")
        or path.startswith("/media")
        or path.startswith("/api/auth")
        or path == "/api/oauth/callback"
        or path.startswith("/api/worker/callback")
        or path.startswith("/api/render-complete")
        or path in ("/", "/health", "/api/health", "/api/system/client-info", "/api/network-info", "/favicon.ico")
    ):
        resp = await call_next(request)
        if path.startswith("/static"):
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

    client_ip, is_direct_local = extract_client_ip(request)

    # Máy chủ localhost kết nối trực tiếp (không qua Proxy/Tunnel) luôn được cho qua
    if is_direct_local:
        return await call_next(request)

    # Thiết bị từ xa phải có token hợp lệ đã được phê duyệt qua Telegram
    token = request.headers.get("X-Device-Token") or request.cookies.get("device_token")
    if not is_device_token_valid(token, client_ip=client_ip, is_direct_local=is_direct_local):
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
                    active_channel_id="ch_psychology_of_the_world"
                )
                db.add(admin)
            elif admin.active_channel_id in ("channel_maya", "ch_maya", "channel_psychology"):
                admin.active_channel_id = "ch_psychology_of_the_world"
    except Exception as e:
        print(f"Warning during startup DB check: {e}")

    # [FIX #7] Chỉ đưa về SCRIPT_READY các job treo quá 3 giờ; các job mới bị ngắt (< 3 giờ) giữ ở RENDERING để Auto-Resume tự động dựng tiếp
    try:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(hours=3)
        with get_db() as db:
            stuck_jobs = db.query(VideoJob).filter(
                VideoJob.status.in_(["RENDERING", "AUDIO_READY", "MEDIA_READY"])
            ).all()
            stale_count = 0
            for j in stuck_jobs:
                ref_time = (j.updated_at or j.created_at or datetime.utcnow()).replace(tzinfo=None)
                if ref_time >= cutoff:
                    j.status = "RENDERING"
                else:
                    j.status = "SCRIPT_READY"
                    j.progress_percent = 0
                    j.progress_message = "Đã khôi phục về SCRIPT_READY (tác vụ cũ quá 3 giờ)"
                    stale_count += 1
            if stale_count:
                logger.info(f"[Startup Recovery] Đã khôi phục {stale_count} video treo quá 3h về SCRIPT_READY")
    except Exception as e:
        logger.warning(f"Lỗi recover stuck jobs on startup: {e}")

    # Tự động nạp hoặc khôi phục credentials/tokens cho Google Account 1 (không ghi đè nếu đã có secret hợp lệ)
    try:
        acc1_dir = BASE_DIR / "credentials" / "accounts" / "account_1"
        acc1_dir.mkdir(parents=True, exist_ok=True)
        secret_p = acc1_dir / "client_secret.json"
        existing_acc1 = load_encrypted_json(secret_p) if secret_p.exists() else None
        if not _is_valid_oauth_client_config(existing_acc1):
            is_render = bool(os.getenv("RENDER") or os.getenv("IS_RENDER"))
            default_secret = DEFAULT_WEB_CLIENT if is_render else DEFAULT_DESKTOP_CLIENT
            if _is_valid_oauth_client_config(default_secret):
                save_encrypted_json(secret_p, default_secret)
                mode_label = "Web Application 1-Click (Render)" if is_render else "Desktop Application (Localhost)"
                logger.info(f"✅ [Startup] Đã tự động kích hoạt client_secret.json {mode_label} cho account_1")
            else:
                _resolve_and_load_client_secret(BASE_DIR, ["account_1"])

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

    # Đồng bộ Thư Viện Video Đã Đăng ngầm (lần đầu sau 60s, rồi mỗi 6 giờ)
    def _library_sync_loop():
        import time as _t
        _t.sleep(60)
        while True:
            try:
                from app.services import video_library
                res = video_library.sync_all()
                logger.info(f"[VideoLibrary] Đồng bộ nền xong: {res.get('backfilled', 0)} từ DB, "
                            f"{sum(c.get('added', 0) for c in res.get('channels', []))} mới từ YouTube")
            except Exception as e_lib:
                logger.warning(f"[VideoLibrary] Lỗi đồng bộ nền: {e_lib}")
            _t.sleep(6 * 3600)

    import threading as _threading
    _threading.Thread(target=_library_sync_loop, daemon=True, name="video-library-sync").start()

    # [Phase 3 Fix 11] Quota Reset Auto-Requeue: Tu dong chuyen QUOTA_EXCEEDED -> PENDING sau khi Google reset quota luc 14:00 VN
    def _quota_reset_watcher():
        import time as _t
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        VN_TZ = _tz(+_td(hours=7))
        _last_cycle = None
        while True:
            try:
                _t.sleep(300)  # Kiem tra moi 5 phut
                from app.services.quota_manager import get_current_cycle_id
                _cycle = get_current_cycle_id()
                if _last_cycle is not None and _cycle != _last_cycle:
                    # Quota da reset! Chuyen tat ca job QUOTA_EXCEEDED ve PENDING
                    with get_db() as _db:
                        _stuck = _db.query(VideoJob).filter(
                            VideoJob.status == "QUOTA_EXCEEDED"
                        ).all()
                        for _j in _stuck:
                            _j.status = "PENDING"
                            _j.progress_message = "Quota da reset, gia han xu ly tu dong."
                        if _stuck:
                            logger.info(
                                "[QuotaWatcher] Quota da reset (chu ky %s). "
                                "Da tu dong chuyen %d job QUOTA_EXCEEDED -> PENDING.",
                                _cycle, len(_stuck)
                            )
                _last_cycle = _cycle
            except Exception as _e:
                logger.warning("[QuotaWatcher] Loi theo doi quota reset: %s", _e)

    _threading.Thread(target=_quota_reset_watcher, daemon=True, name="quota-reset-watcher").start()

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
@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health_check():
    """
    [Phase 3] Production-grade health check.
    Kiem tra: DB connectivity, disk space, autopilot state, pending jobs.
    Tra ve HTTP 503 neu DB down (de Render / UptimeRobot phat hien outage).
    """
    import shutil as _shutil
    from datetime import datetime as _dt, timezone as _tz

    result: Dict[str, Any] = {
        "status": "ok",
        "app": "AI YouTube Multi-Channel Studio v2.0",
        "ts": _dt.now(_tz.utc).isoformat(),
        "checks": {}
    }
    http_status = 200

    # ── 1. Database ping ────────────────────────────────────────
    try:
        from sqlalchemy import text as _text
        with get_db() as _db:
            _db.execute(_text("SELECT 1"))
        result["checks"]["database"] = {"status": "ok"}
    except Exception as _e:
        result["checks"]["database"] = {"status": "error", "detail": str(_e)[:120]}
        result["status"] = "degraded"
        http_status = 503

    # ── 2. Disk space (warn < 10 GB free) ───────────────────────
    try:
        _usage = _shutil.disk_usage(str(BASE_DIR))
        _free_gb = _usage.free / (1024 ** 3)
        _total_gb = _usage.total / (1024 ** 3)
        disk_ok = _free_gb >= 10.0
        result["checks"]["disk"] = {
            "status": "ok" if disk_ok else "warn",
            "free_gb": round(_free_gb, 1),
            "total_gb": round(_total_gb, 1),
        }
        if not disk_ok and result["status"] == "ok":
            result["status"] = "warn"
    except Exception as _e:
        result["checks"]["disk"] = {"status": "unknown", "detail": str(_e)[:80]}

    # ── 3. Pending / rendering jobs ─────────────────────────────
    try:
        with get_db() as _db:
            _pending  = _db.query(VideoJob).filter(VideoJob.status.in_(["PENDING", "SCRIPT_READY"])).count()
            _render   = _db.query(VideoJob).filter(VideoJob.status == "RENDERING").count()
        result["checks"]["jobs"] = {"pending": _pending, "rendering": _render}
    except Exception:
        result["checks"]["jobs"] = {"status": "unknown"}

    # ── 4. Autopilot service state ──────────────────────────────
    try:
        from app.services.autopilot_service import AutopilotService
        _ap = AutopilotService()
        result["checks"]["autopilot"] = {
            "running_channels": list(getattr(_ap, "_running", {}).keys()),
        }
    except Exception:
        result["checks"]["autopilot"] = {"status": "unknown"}

    return Response(
        content=json.dumps(result, ensure_ascii=False),
        media_type="application/json",
        status_code=http_status,
    )

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
    """Kiểm tra quyền truy cập thiết bị (Localhost trực tiếp luôn qua; Thiết bị từ xa phải có token đã duyệt)"""
    from app.services.device_auth import is_gatekeeper_enabled, extract_client_ip, is_device_token_valid, parse_device_info

    client_ip, is_direct_local = extract_client_ip(request)

    if not is_gatekeeper_enabled() or is_direct_local:
        return {"authenticated": True, "is_local": True, "client_ip": client_ip}

    auth_token = request.headers.get("X-Device-Token") or request.cookies.get("device_token")
    if auth_token and is_device_token_valid(auth_token, client_ip=client_ip, is_direct_local=is_direct_local):
        response.set_cookie(
            key="device_token",
            value=auth_token,
            max_age=7 * 24 * 3600,
            path="/",
            samesite="lax",
            httponly=True
        )
        return {"authenticated": True, "is_local": False, "client_ip": client_ip, "token": auth_token}

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
    from app.services.device_auth import extract_client_ip, create_access_request
    client_ip, _ = extract_client_ip(request)
    ua = request.headers.get("user-agent", "")
    return create_access_request(client_ip, ua)

@app.get("/api/auth/status/{request_id}")
async def check_device_request_status(request_id: str, request: Request, response: Response):
    """Frontend polling kiểm tra xem Admin đã bấm [Đồng Ý] trên Telegram chưa"""
    from app.services.device_auth import extract_client_ip, get_request_status
    client_ip, _ = extract_client_ip(request)
    res = get_request_status(request_id, client_ip=client_ip)
    if res.get("status") == "approved" and res.get("token"):
        response.set_cookie(
            key="device_token",
            value=res["token"],
            max_age=7 * 24 * 3600,
            path="/",
            samesite="lax",
            httponly=True
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
        scheduled = db.query(VideoJob).filter(VideoJob.status == "SCHEDULED").count()
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
        from app.web.routers.accounts_and_oauth import _read_channels_yaml
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
        "scheduled": scheduled,
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
    """Kiểm tra xem token của kênh có thực sự dùng được hay bị invalid_grant (tự động hồi phục từ Pool token nếu có)."""
    now = time.time()
    ch_dir = token_file.parent

    def _try_heal_from_pool() -> Optional[Dict[str, Any]]:
        if not ch_dir.exists():
            return None
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        for cand_tf in sorted(ch_dir.glob("youtube_token_*.json")):
            if cand_tf.name == token_file.name:
                continue
            try:
                c_data = load_encrypted_json(cand_tf)
                if not c_data or not c_data.get("refresh_token"):
                    continue
                c_creds = Credentials.from_authorized_user_info(c_data)
                c_creds.refresh(Request())
                merged = {**c_data, **json.loads(c_creds.to_json())}
                save_encrypted_json(cand_tf, merged)
                save_encrypted_json(token_file, merged)
                new_mtime = token_file.stat().st_mtime
                logger.info(f"🔄 [OAuth Pool Self-Heal] Đã tự động hồi phục youtube_token.json cho '{ch_id}' từ '{cand_tf.name}'")
                return {
                    "valid": True,
                    "expired": False,
                    "title": merged.get("channel_title"),
                    "error": None,
                    "checked_at": now,
                    "mtime": new_mtime,
                }
            except Exception:
                continue
        return None

    if not token_file.exists():
        healed = _try_heal_from_pool()
        if healed:
            _channel_token_health_cache[ch_id] = healed
            return healed
        res = {"valid": False, "expired": False, "title": None, "error": "No token file", "checked_at": now, "mtime": 0}
        _channel_token_health_cache[ch_id] = res
        return res

    mtime = token_file.stat().st_mtime
    cached = _channel_token_health_cache.get(ch_id)
    ttl = 600 if (cached and cached.get("valid")) else 60
    if cached and cached.get("mtime") == mtime and (now - cached.get("checked_at", 0) < ttl):
        return cached

    try:
        t_data = load_encrypted_json(token_file)
        if not t_data or "refresh_token" not in t_data:
            healed = _try_heal_from_pool()
            if healed:
                _channel_token_health_cache[ch_id] = healed
                return healed
            res = {"valid": False, "expired": False, "title": None, "error": "Malformed token", "checked_at": now, "mtime": mtime}
            _channel_token_health_cache[ch_id] = res
            return res

        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        creds = Credentials.from_authorized_user_info(t_data)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            merged = {**t_data, **json.loads(creds.to_json())}
            save_encrypted_json(token_file, merged)
            mtime = token_file.stat().st_mtime

        res = {"valid": True, "expired": False, "title": t_data.get("channel_title"), "error": None, "checked_at": now, "mtime": mtime}
        _channel_token_health_cache[ch_id] = res
        return res
    except Exception as e:
        healed = _try_heal_from_pool()
        if healed:
            _channel_token_health_cache[ch_id] = healed
            return healed
        err_str = str(e)
        is_exp = "invalid_grant" in err_str or "expired" in err_str or "revoked" in err_str
        res = {"valid": False, "expired": is_exp, "title": None, "error": err_str, "checked_at": now, "mtime": mtime}
        _channel_token_health_cache[ch_id] = res
        return res

def _format_channel_golden_slots_vn(channel_id: str) -> str:
    """Tính chuỗi hiển thị 2 Khung Giờ Vàng hàng ngày (Giờ VN) đã cộng độ lệch pha riêng của kênh."""
    try:
        from app.services.smart_scheduler import (
            get_active_slots_pool,
            get_channel_stagger_minutes,
        )
        pool = get_active_slots_pool(str(channel_id or ""))
        stagger = get_channel_stagger_minutes(str(channel_id or ""))
        vn_times = []
        for s in pool:
            total_utc_mins = (int(s["hour"]) * 60 + int(s["minute"]) + stagger) % 1440
            vn_mins = (total_utc_mins + 7 * 60) % 1440
            vn_times.append(f"{vn_mins // 60:02d}:{vn_mins % 60:02d}")
        if vn_times:
            return " & ".join(vn_times) + " (Giờ VN)"
    except Exception:
        pass
    return "Khung Giờ Vàng Tự Động"


@app.get("/api/channels")
def get_channels_matrix() -> List[Dict[str, Any]]:
    from app.services.smart_scheduler import (
        resolve_channel_family,
        sync_scheduled_jobs_status,
        get_channel_upcoming_schedule,
        get_next_available_golden_slot,
    )
    try:
        sync_scheduled_jobs_status()
    except Exception:
        pass

    from app.web.routers.accounts_and_oauth import _read_channels_yaml, _check_account_tokens
    cfg = _read_channels_yaml()
    channels = cfg.get("channels", [])
    raw_accounts = cfg.get("google_accounts", [])
    accounts_map = {a.get("id"): a for a in raw_accounts}

    result = []
    with get_db() as db:
        for ch in channels:
            ch_id = ch.get("id")
            fam_ids = resolve_channel_family(str(ch_id or ""))
            video_count = db.query(VideoJob).filter(VideoJob.target_channel_id.in_(fam_ids)).count()
            published_count = db.query(VideoJob).filter(
                VideoJob.target_channel_id.in_(fam_ids), VideoJob.status == "PUBLISHED"
            ).count()
            scheduled_count = db.query(VideoJob).filter(
                VideoJob.target_channel_id.in_(fam_ids), VideoJob.status == "SCHEDULED"
            ).count()
            rendered_count = db.query(VideoJob).filter(
                VideoJob.target_channel_id.in_(fam_ids), VideoJob.status == "RENDERED"
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
            brand_match = next((b for b in brand_channels if b.get("id") == ch_id), {})
            effective_schedule = ch.get("schedule") or brand_match.get("schedule") or {}

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

            upcoming_list = []
            next_slot_obj = None
            try:
                upcoming_list = get_channel_upcoming_schedule(str(ch_id), limit=6, _skip_sync=True)
                _, nxt_iso, nxt_vn, nxt_us = get_next_available_golden_slot(str(ch_id), _skip_sync=True)
                next_slot_obj = {
                    "iso": nxt_iso,
                    "display_vn": nxt_vn,
                    "display_us": nxt_us,
                }
            except Exception as sched_err:
                logger.debug(f"[ChannelsMatrix] Schedule enrichment skip for {ch_id}: {sched_err}")

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
                "schedule": effective_schedule,
                "golden_slots_vn": _format_channel_golden_slots_vn(str(ch_id)),
                "video_count": video_count,
                "published_count": published_count,
                "scheduled_count": scheduled_count,
                "rendered_count": rendered_count,
                "upcoming_schedule": upcoming_list,
                "next_golden_slot": next_slot_obj,
                "drive_quota": parent_acc.get("drive_storage", "2 TB Google One"),
                "youtube_quota_daily": parent_acc.get("youtube_quota_daily", 10000),
            })
    return result

