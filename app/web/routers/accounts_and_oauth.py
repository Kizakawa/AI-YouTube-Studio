"""app.web.routers.accounts_and_oauth — Quản lý Tài khoản Google, OAuth2, Kênh Thương hiệu và Hồ sơ Ngách."""
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
from typing import Dict, List, Any, Optional, Tuple
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

import hmac
import html as html_lib

from app.web.routers.core_and_dashboard import (
    app,
    BASE_DIR,
    STATIC_DIR,
    MEDIA_DIR,
    DEFAULT_DESKTOP_CLIENT,
    DEFAULT_WEB_CLIENT,
    _REVOKED_CLIENT_ID_PREFIXES,
    _safe_env_client_id,
    _is_valid_oauth_client_config,
    _resolve_and_load_client_secret,
    _channel_token_health_cache,
    _check_channel_token_health,
    _format_channel_golden_slots_vn,
)

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
        safe_error = html_lib.escape(str(error))
        return HTMLResponse(
            content=f"""<!DOCTYPE html>
            <html><head><meta charset="utf-8"><title>Lỗi Xác Thực</title></head>
            <body style="font-family:system-ui,sans-serif; background:#0f172a; color:#f8fafc; text-align:center; padding:60px 20px;">
                <div style="font-size:50px; margin-bottom:12px;">❌</div>
                <h2 style="color:#ef4444;">Lỗi Xác Thực Từ Google</h2>
                <p style="color:#94a3b8;">Chi tiết: {safe_error}</p>
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
            pool_token_file = ch_dir / f"youtube_token_{acc_id}.json"

            expected_yt_id = (st_info.get("expected_yt_id") or "").strip()
            if expected_yt_id and real_yt_id and expected_yt_id.lower() != real_yt_id.lower():
                err_msg = (
                    f"⚠️ Bạn vừa chọn nhầm kênh '{real_yt_title or real_yt_id}' trên Google, "
                    f"trong khi đang cần cấp quyền cho kênh '{ch_name}' ({expected_yt_id})! "
                    f"Vui lòng quay lại Dashboard và chọn đúng kênh '{ch_name}'."
                )
                _oauth_sessions[session_key] = {
                    "status": "error",
                    "error": err_msg,
                    "message": err_msg,
                }
                raise ValueError(err_msg)

            token_dict = json.loads(creds.to_json())
            token_dict["channel_id"] = real_yt_id or channel_id
            token_dict["channel_title"] = real_yt_title or ch_name
            if custom_url:
                token_dict["custom_url"] = custom_url

            save_encrypted_json(pool_token_file, token_dict)
            save_encrypted_json(token_file, token_dict)
            _channel_token_health_cache.pop(channel_id, None)

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
            display_name = html_lib.escape(f"Kênh YouTube: {succ_title}")
        else:
            account_id = st_info["account_id"]
            service = st_info["service"]
            acc_dir = Path(st_info["acc_dir"])
            token_filename = st_info["token_filename"]

            token_data = creds.to_json()
            token_file = acc_dir / token_filename
            token_dict_to_save = json.loads(token_data)
            save_encrypted_json(token_file, token_dict_to_save)

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
            if account_id == "project_pool_2" and service == "youtube":
                # Tự động đồng bộ token pool này vào tất cả các kênh để kích hoạt Quota 20.000
                ch_base = base_dir / "credentials" / "channels"
                if ch_base.exists():
                    for ch_folder in ch_base.iterdir():
                        if ch_folder.is_dir():
                            pool_tok_file = ch_folder / "youtube_token_project_pool_2.json"
                            try:
                                save_encrypted_json(pool_tok_file, token_dict_to_save)
                                logger.info(f"✅ Đã đồng bộ token pool 2 (effective-cacao) vào kênh: {ch_folder.name}")
                            except Exception as e_sync:
                                logger.warning(f"Lỗi đồng bộ token pool vào {ch_folder.name}: {e_sync}")
            _oauth_sessions[session_key] = {
                "status": "success",
                "message": succ_msg
            }
            display_name = html_lib.escape(f"Tài khoản Google: {account_id}")

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
                window.opener.postMessage({{ type: 'oauth_complete', success: true }}, window.location.origin);
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
        safe_ex = html_lib.escape(str(ex))
        return HTMLResponse(
            content=f"""<!DOCTYPE html>
            <html><head><meta charset="utf-8"><title>Lỗi</title></head>
            <body style="font-family:system-ui,sans-serif; background:#0f172a; color:#f8fafc; text-align:center; padding:60px 20px;">
                <div style="font-size:50px; margin-bottom:12px;">❌</div>
                <h2 style="color:#ef4444;">Không Thể Xác Thực Mã Code</h2>
                <p style="color:#94a3b8;">Lỗi: {safe_ex}</p>
                <p style="color:#64748b; font-size:13px;">Bạn có thể đóng tab này và thử lại từ Dashboard.</p>
            </body></html>""",
            status_code=500
        )

def _sync_flat_channels_from_accounts(data: Dict[str, Any]) -> None:
    """Đồng bộ trường schedule và google_account từ google_accounts[*].brand_channels xuống danh sách phẳng channels."""
    if not isinstance(data, dict):
        return
    accounts = data.get("google_accounts")
    flat_channels = data.get("channels")
    if not isinstance(accounts, list) or not isinstance(flat_channels, list):
        return
    brand_map: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        acc_id = acc.get("id", "")
        for bch in acc.get("brand_channels", []) or []:
            if isinstance(bch, dict) and bch.get("id"):
                brand_map[str(bch["id"])] = (acc_id, bch)
    for fc in flat_channels:
        if not isinstance(fc, dict):
            continue
        cid = str(fc.get("id") or "")
        if cid in brand_map:
            acc_id, bch = brand_map[cid]
            if acc_id:
                fc["google_account"] = acc_id
            if bch.get("schedule"):
                fc["schedule"] = dict(bch["schedule"])


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
            if isinstance(data, dict):
                _sync_flat_channels_from_accounts(data)
                return data
            return {}
    except Exception as e:
        logger.error(f"Lỗi đọc channels.yaml: {e}")
        return {"google_accounts": [], "channels": []}

def _write_channels_yaml(data: Dict[str, Any]) -> bool:
    yaml_path = settings.CHANNELS_CONFIG_FILE
    try:
        _sync_flat_channels_from_accounts(data)
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False, indent=2)
        settings._cached_channels = None
        settings._cached_accounts = None
        settings._channels_mtime = 0.0
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
    publish_hour_utc: Optional[int] = 21
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
            has_own = False
            token_expired = False
            ch_title = None
            if ch_token_file.exists():
                try:
                    ch_tok = load_encrypted_json(ch_token_file)
                    ch_title = ch_tok.get("channel_title")
                except Exception:
                    pass
            ch_health = _check_channel_token_health(str(bch_id), ch_token_file)
            if ch_health.get("valid"):
                has_own = True
                ch_title = ch_health.get("title") or ch_title
            elif ch_health.get("expired"):
                token_expired = True

            is_conn = has_own or (not token_expired and idx == 0 and token_status["youtube_connected"])
            bch_copy = dict(settings._heal_channel_dict(dict(bch)))
            bch_copy["has_own_token"] = has_own
            bch_copy["youtube_connected"] = is_conn
            bch_copy["token_expired"] = token_expired
            bch_copy["token_channel_title"] = ch_title
            bch_copy["golden_slots_vn"] = _format_channel_golden_slots_vn(str(bch_id))
            try:
                from app.services.smart_scheduler import get_channel_upcoming_schedule
                up_list = get_channel_upcoming_schedule(str(bch_id), limit=5, _skip_sync=True)
                bch_copy["upcoming_schedule"] = up_list
                bch_copy["scheduled_count"] = len(up_list)
            except Exception:
                bch_copy["upcoming_schedule"] = []
                bch_copy["scheduled_count"] = 0
            enriched_bchannels.append(bch_copy)

        enriched_accounts.append({
            "id": acc_id,
            "email_label": acc.get("email_label", ""),
            "drive_storage": acc.get("drive_storage", "2 TB Google One"),
            "youtube_quota_daily": acc.get("youtube_quota_daily", 10000),
            "has_client_secret": token_status["has_client_secret"],
            "youtube_connected": token_status["youtube_connected"] or any(b.get("youtube_connected") for b in enriched_bchannels),
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

    save_encrypted_json(secret_path, secret_data)

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

    secret_file, sec_json = _resolve_and_load_client_secret(base_dir, [account_id])
    if not secret_file or not sec_json:
        raise HTTPException(status_code=400, detail="Chưa có file client_secret.json hợp lệ! Vui lòng nạp file trước.")
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
    callback_url = f"{scheme}://{host}/api/oauth/callback"

    # Kiểm tra xem client_secret có phải loại Web application không và có khớp redirect_uris không
    is_web = False

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
        auth_url, _ = flow.authorization_url(prompt="select_account consent", access_type="offline", state=state_token)
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
        req_uri = wsgiref.util.request_uri(environ)
        parsed_req = urlparse(req_uri)
        if parsed_req.path == "/favicon.ico" or ("code=" not in req_uri and "error=" not in req_uri):
            start_response("204 No Content", [])
            return [b""]
        received_uri["value"] = req_uri
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
    auth_url, _ = flow.authorization_url(prompt="select_account consent", access_type="offline")

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
                        norm_yt_id = (real_yt_id or "").strip().lower()
                        norm_custom = (custom_url or "").strip().lower()
                        norm_title = (real_yt_title or "").strip(' "\'').lower()
                        for c in all_chs:
                            c_id = (c.get("id") or "").strip().lower()
                            c_yt_id = (c.get("youtube_channel_id") or "").strip().lower()
                            c_handle = (c.get("handle") or "").strip().lower()
                            c_name = (c.get("name") or "").strip(' "\'').lower()
                            if norm_yt_id and (c_id == norm_yt_id or c_yt_id == norm_yt_id):
                                matched_ch = c
                                break
                            if norm_custom and (c_id == norm_custom or c_handle == norm_custom):
                                matched_ch = c
                                break
                            if norm_title and c_name == norm_title:
                                matched_ch = c
                                break

                        # Nếu không match chính xác, ưu tiên kênh thuộc account_id này (nếu tài khoản chỉ quản lý 1 kênh)
                        if not matched_ch:
                            acc_chs = [c for c in all_chs if c.get("google_account") == account_id]
                            if len(acc_chs) == 1:
                                matched_ch = acc_chs[0]

                        # Nếu vẫn chưa tìm thấy, chọn kênh chưa có token
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
                            pool_token_file = ch_token_dir / f"youtube_token_{account_id}.json"

                            ch_t_dict = json.loads(token_data)
                            ch_t_dict["channel_id"] = real_yt_id
                            ch_t_dict["channel_title"] = real_yt_title
                            if custom_url:
                                ch_t_dict["custom_url"] = custom_url
                            save_encrypted_json(ch_token_file, ch_t_dict)
                            save_encrypted_json(pool_token_file, ch_t_dict)
                            _channel_token_health_cache.pop(ch_target_id, None)
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
            "publish_hour_utc": req.publish_hour_utc or 21,
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

    if req.name and req.name.strip():
        channel["name"] = req.name.strip()
    if req.niche and req.niche.strip():
        channel["niche"] = req.niche.strip()
    if req.language and req.language.strip():
        channel["language"] = req.language.strip()
    channel["visual_mode"] = req.visual_mode or channel.get("visual_mode", "auto")
    if req.preferred_voice and req.preferred_voice.strip():
        channel["preferred_voice"] = req.preferred_voice.strip()
    channel["schedule"] = {
        "publish_hour_utc": req.publish_hour_utc or 21,
        "days": req.days or ["monday", "wednesday", "friday", "sunday"]
    }
    settings._heal_channel_dict(channel)

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
            fc["niche"] = channel.get("niche", fc.get("niche", ""))
            fc["language"] = channel.get("language", fc.get("language", "en"))
            fc["preferred_voice"] = channel.get("preferred_voice", fc.get("preferred_voice", "en-US-ChristopherNeural"))
            fc["visual_mode"] = channel["visual_mode"]
            fc["google_account"] = target_account_id
            fc["schedule"] = dict(channel.get("schedule") or {})
            settings._heal_channel_dict(fc)

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



@app.get("/api/channels/oauth-live-check")
def check_all_channels_live_oauth() -> Dict[str, Any]:
    """
    Kiểm tra trực tiếp sức khỏe Token OAuth Live (Google refresh check) của toàn bộ các kênh
    và lập Ma trận Liên kết Đa Dự án (Quota Pool 7x3) ngay trên Web Dashboard.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleAuthRequest

    base_dir = Path(settings.BASE_DIR)
    cfg = _read_channels_yaml()
    accounts = ["account_1", "acount_2", "accout3"]
    acc_dir = base_dir / "credentials" / "accounts"
    cid_to_acc: Dict[str, str] = {}

    for acc in accounts:
        cs = acc_dir / acc / "client_secret.json"
        if cs.exists():
            try:
                d = load_encrypted_json(cs) or {}
                info = d.get("installed") or d.get("web") or {}
                client_id_val = info.get("client_id")
                if client_id_val:
                    cid_to_acc[client_id_val] = acc
            except Exception:
                pass

    all_channels: List[Dict[str, Any]] = []
    for acc in cfg.get("google_accounts", []):
        for bch in acc.get("brand_channels", []):
            all_channels.append({
                "id": bch.get("id"),
                "name": bch.get("name", bch.get("id")),
                "account": acc.get("id", "account_1"),
                "youtube_channel_id": (bch.get("youtube_channel_id") or "").strip(),
            })
    for ch in cfg.get("channels", []):
        if not any(c["id"] == ch.get("id") for c in all_channels):
            all_channels.append({
                "id": ch.get("id"),
                "name": ch.get("name", ch.get("id")),
                "account": ch.get("google_account", "account_1"),
                "youtube_channel_id": (ch.get("youtube_channel_id") or "").strip(),
            })

    results: List[Dict[str, Any]] = []
    valid_count = 0
    now_ts = time.time()
    for ch in all_channels:
        cid = ch["id"]
        ch_dir = base_dir / "credentials" / "channels" / cid
        prim_token = ch_dir / "youtube_token.json"
        exp_yt = ch["youtube_channel_id"]

        is_valid = False
        status_code = "missing"
        status_text = "🔴 Chưa có token"
        token_channel_title = ""

        if prim_token.exists():
            try:
                data = load_encrypted_json(prim_token)
                tok_yt = (data.get("channel_id") or "").strip() if data else ""
                token_channel_title = (data.get("channel_title") or "").strip() if data else ""
                if exp_yt and tok_yt and exp_yt.lower() != tok_yt.lower():
                    status_code = "mismatch"
                    status_text = f"🔴 Lệch kênh ({tok_yt[:12]}...)"
                elif data and data.get("refresh_token"):
                    creds = Credentials.from_authorized_user_info(data)
                    creds.refresh(GoogleAuthRequest())
                    merged_prim = {**data, **json.loads(creds.to_json())}
                    save_encrypted_json(prim_token, merged_prim)
                    is_valid = True
                    valid_count += 1
                    status_code = "valid"
                    status_text = "🟢 Đang hoạt động tốt (VALID)"
                else:
                    status_code = "invalid"
                    status_text = "🔴 Thiếu refresh_token"
            except Exception as ex_tok:
                err_s = "Hết hạn (invalid_grant)" if "invalid_grant" in str(ex_tok) else str(ex_tok)[:36]
                status_code = "expired"
                status_text = f"🔴 Cần cấp lại ({err_s})"

        matrix = {a: False for a in accounts}
        if ch_dir.exists():
            for tf in sorted(ch_dir.glob("*.json")):
                try:
                    t_data = load_encrypted_json(tf)
                    if not t_data or not t_data.get("refresh_token"):
                        continue
                    tok_yt_pool = (t_data.get("channel_id") or "").strip()
                    if exp_yt and tok_yt_pool and exp_yt.lower() != tok_yt_pool.lower():
                        continue
                    matched_acc_list: List[str] = []
                    if "client_id" in t_data:
                        m_acc = cid_to_acc.get(t_data["client_id"])
                        if m_acc:
                            matched_acc_list.append(m_acc)
                    for a in accounts:
                        if tf.name == f"youtube_token_{a}.json" and a not in matched_acc_list:
                            matched_acc_list.append(a)
                    if not matched_acc_list and not is_valid:
                        pass
                    # Kiểm tra refresh thật cho token pool
                    if tf == prim_token and is_valid:
                        for ma in matched_acc_list:
                            matrix[ma] = True
                        continue
                    p_creds = Credentials.from_authorized_user_info(t_data)
                    p_creds.refresh(GoogleAuthRequest())
                    merged_pool = {**t_data, **json.loads(p_creds.to_json())}
                    save_encrypted_json(tf, merged_pool)
                    for ma in matched_acc_list:
                        matrix[ma] = True
                    # Tự động hồi phục youtube_token.json chính nếu token chính bị hỏng/hết hạn
                    if not is_valid:
                        save_encrypted_json(prim_token, merged_pool)
                        is_valid = True
                        valid_count += 1
                        status_code = "valid"
                        status_text = "🟢 Đang hoạt động tốt (VALID)"
                        token_channel_title = (merged_pool.get("channel_title") or token_channel_title).strip()
                        logger.info(f"🔄 [Live OAuth Matrix] Đã tự động hồi phục youtube_token.json cho '{cid}' từ '{tf.name}'")
                except Exception:
                    pass

        if prim_token.exists():
            _channel_token_health_cache[cid] = {
                "valid": is_valid,
                "expired": status_code == "expired",
                "title": token_channel_title or ch["name"],
                "error": None if is_valid else status_text,
                "checked_at": now_ts,
                "mtime": prim_token.stat().st_mtime,
            }

        results.append({
            "id": cid,
            "name": ch["name"],
            "primary_account": ch["account"],
            "youtube_channel_id": exp_yt,
            "token_channel_title": token_channel_title,
            "is_valid": is_valid,
            "status_code": status_code,
            "status_text": status_text,
            "project_matrix": matrix,
        })

    return {
        "success": True,
        "accounts": accounts,
        "valid_count": valid_count,
        "total_count": len(results),
        "channels": results,
    }


@app.post("/api/channels/{channel_id}/start-auth")
async def start_channel_oauth(
    channel_id: str,
    request: Request,
    service: str = "youtube",
    account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Bắt đầu phiên xác thực OAuth riêng biệt cho một kênh YouTube cụ thể (hỗ trợ chọn Google Cloud Project để xoay Quota)."""
    client_ip = request.client.host if request.client else "127.0.0.1"
    is_local_client = client_ip in ("127.0.0.1", "localhost", "::1", "testclient")

    cfg = _read_channels_yaml()
    channels = cfg.get("channels", [])
    ch = next((c for c in channels if c.get("id") == channel_id), None)
    primary_acc_id = "account_1"
    expected_yt_id = ""
    expected_gmail = ""
    if ch:
        primary_acc_id = ch.get("google_account", "account_1")
        expected_yt_id = (ch.get("youtube_channel_id") or "").strip()

    for acc in cfg.get("google_accounts", []):
        if acc.get("id") == primary_acc_id and acc.get("email_label"):
            expected_gmail = acc.get("email_label", "")
        for bc in acc.get("brand_channels", []):
            if bc.get("id") == channel_id:
                if not ch:
                    ch = bc
                    primary_acc_id = acc.get("id", "account_1")
                if not expected_yt_id and bc.get("youtube_channel_id"):
                    expected_yt_id = (bc.get("youtube_channel_id") or "").strip()
                if not expected_gmail and acc.get("email_label"):
                    expected_gmail = acc.get("email_label", "")
                break
    if not ch:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh")

    acc_id = (account_id or primary_acc_id or "account_1").strip()
    ch_name = ch.get("name", channel_id)
    session_key = f"channel:{channel_id}:{service}"

    base_dir = Path(settings.BASE_DIR)
    secret_file, sec_json = _resolve_and_load_client_secret(base_dir, [acc_id, primary_acc_id])
    if not secret_file or not sec_json:
        raise HTTPException(status_code=400, detail=f"Chưa có file client_secret.json hợp lệ cho dự án {acc_id}!")

    import webbrowser
    import wsgiref.simple_server
    import wsgiref.util
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = [
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.force-ssl",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
    ]
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
    callback_url = f"{scheme}://{host}/api/oauth/callback"

    # Kiểm tra xem client_secret có phải loại Web application không và có khớp redirect_uris không
    is_web = False

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
        auth_url, _ = flow.authorization_url(prompt="select_account consent", access_type="offline", state=state_token)
        _oauth_web_states[state_token] = {
            "flow": flow,
            "session_key": session_key,
            "type": "channel",
            "channel_id": channel_id,
            "channel_name": ch_name,
            "expected_yt_id": expected_yt_id,
            "acc_id": acc_id,
            "service": service,
            "created_at": time.time()
        }
        _oauth_sessions[session_key] = {
            "status": "in_progress",
            "channel_id": channel_id,
            "channel_name": ch_name,
            "expected_gmail": expected_gmail,
            "service": service,
            "auth_url": auth_url,
            "message": f"Phiên xác thực 1-Click tự động cho kênh '{ch_name}' đã sẵn sàng."
        }
        return {
            "success": True,
            "auth_url": auth_url,
            "expected_gmail": expected_gmail,
            "expected_yt_id": expected_yt_id,
            "session_key": session_key,
            "status": "in_progress"
        }

    desktop_cfg = sec_json if "installed" in sec_json else DEFAULT_DESKTOP_CLIENT
    flow = InstalledAppFlow.from_client_config(desktop_cfg, scopes=scopes)
    received_uri = {"value": None}
    verify_event = threading.Event()
    server_active = {"running": True}
    ch_name = ch.get("name", channel_id)
    safe_ch_name_html = html_lib.escape(str(ch_name))
    default_success_page = (
        f"<html><head><meta charset='utf-8'><title>Cấp Quyền Kênh YouTube</title></head>"
        f"<body style='font-family:system-ui,sans-serif;text-align:center;padding:60px;"
        f"background:#0f172a;color:#f8fafc'>"
        f"<h2 style='color:#34d399'>✅ Đã kết nối kênh '{safe_ch_name_html}' thành công!</h2>"
        f"<p style='color:#94a3b8'>Bạn có thể đóng tab này và quay lại Web Studio để đăng video.</p>"
        f"<script>setTimeout(function(){{try{{window.close();}}catch(e){{}}}},2000);</script>"
        f"</body></html>"
    )
    verify_page = {"html": default_success_page}

    def _wsgi_app(environ, start_response):
        req_uri = wsgiref.util.request_uri(environ)
        parsed_req = urlparse(req_uri)
        if parsed_req.path == "/favicon.ico" or ("code=" not in req_uri and "error=" not in req_uri):
            start_response("204 No Content", [])
            return [b""]
        verify_event.clear()
        received_uri["value"] = req_uri
        verify_event.wait(timeout=12.0)
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [verify_page["html"].encode("utf-8")]

    class _SilentHandler(wsgiref.simple_server.WSGIRequestHandler):
        def log_message(self, fmt, *args):
            pass

    local_server = wsgiref.simple_server.make_server("localhost", 0, _wsgi_app, handler_class=_SilentHandler)
    local_server.timeout = 1.0
    port = local_server.server_port
    flow.redirect_uri = f"http://localhost:{port}/"
    auth_url, _ = flow.authorization_url(prompt="select_account consent", access_type="offline")

    session_key = f"channel:{channel_id}:{service}"
    _oauth_sessions[session_key] = {
        "status": "starting",
        "channel_id": channel_id,
        "channel_name": ch_name,
        "expected_gmail": expected_gmail,
        "expected_yt_id": expected_yt_id,
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

    def _serve_local_callbacks():
        while server_active["running"]:
            try:
                local_server.handle_request()
            except Exception:
                break

    def _channel_oauth_worker():
        nonlocal channel_id, ch_name, primary_acc_id
        try:
            srv_thread = threading.Thread(target=_serve_local_callbacks, daemon=True)
            srv_thread.start()

            start_t = time.time()
            creds = None
            real_yt_title = None
            real_yt_id = None
            custom_url = None

            while time.time() - start_t < 180:
                auth_input = None
                if received_uri["value"]:
                    auth_input = received_uri["value"]
                    received_uri["value"] = None
                elif session_key in _oauth_manual_responses:
                    auth_input = _oauth_manual_responses.pop(session_key)

                if not auth_input:
                    time.sleep(0.35)
                    continue

                try:
                    cand_creds = exchange_oauth_code_for_credentials(flow, auth_input)
                    if not cand_creds:
                        verify_event.set()
                        continue
                except Exception as ex:
                    logger.warning(f"Thử đổi mã OAuth kênh thất bại: {ex}")
                    retry_url, _ = flow.authorization_url(prompt="select_account consent", access_type="offline")
                    _oauth_sessions[session_key]["status"] = "error"
                    _oauth_sessions[session_key]["auth_url"] = retry_url
                    _oauth_sessions[session_key]["error"] = str(ex)
                    verify_page["html"] = (
                        f"<html><head><meta charset='utf-8'><title>Lỗi Xác Thực</title></head>"
                        f"<body style='font-family:system-ui,sans-serif;text-align:center;padding:60px;background:#0f172a;color:#f8fafc'>"
                        f"<h2 style='color:#ef4444'>❌ Không thể đổi mã xác thực</h2>"
                        f"<p style='color:#94a3b8'>{html_lib.escape(str(ex))}</p>"
                        f"<a href='{html_lib.escape(retry_url)}' style='display:inline-block;margin-top:16px;padding:12px 24px;background:#3b82f6;color:#fff;text-decoration:none;border-radius:8px;font-weight:700;'>🔄 Thử Đăng Nhập Lại</a>"
                        f"</body></html>"
                    )
                    verify_event.set()
                    continue

                # Đọc thông tin kênh YouTube thật từ token vừa cấp
                cand_yt_title = None
                cand_yt_id = None
                cand_custom_url = None
                try:
                    from googleapiclient.discovery import build
                    yt = build("youtube", "v3", credentials=cand_creds)
                    c_res = yt.channels().list(mine=True, part="snippet,id").execute()
                    items = c_res.get("items", [])
                    if items:
                        cand_yt_id = items[0].get("id")
                        snippet = items[0].get("snippet", {})
                        cand_yt_title = snippet.get("title")
                        cand_custom_url = snippet.get("customUrl")
                except Exception as e:
                    logger.warning(f"Không thể đọc thông tin kênh YouTube từ token: {e}")

                # Kiểm tra nếu người dùng chọn nhầm Brand Channel khác với kênh đang yêu cầu
                if expected_yt_id and (not cand_yt_id or expected_yt_id.lower() != cand_yt_id.lower()):
                    other_ch_id = None
                    other_ch_name = None
                    other_primary_acc = None
                    if cand_yt_id:
                        cfg_check = _read_channels_yaml()
                        for acc_item in cfg_check.get("google_accounts", []):
                            for bch_item in acc_item.get("brand_channels", []):
                                if (bch_item.get("youtube_channel_id") or "").strip().lower() == cand_yt_id.lower():
                                    other_ch_id = bch_item.get("id")
                                    other_ch_name = bch_item.get("name", other_ch_id)
                                    other_primary_acc = acc_item.get("id", "account_1")
                                    break
                            if other_ch_id:
                                break
                        if not other_ch_id:
                            for c_item in cfg_check.get("channels", []):
                                if (c_item.get("youtube_channel_id") or "").strip().lower() == cand_yt_id.lower():
                                    other_ch_id = c_item.get("id")
                                    other_ch_name = c_item.get("name", other_ch_id)
                                    other_primary_acc = c_item.get("google_account", "account_1")
                                    break

                    # Nếu kênh chọn nhầm thuộc hệ thống, lưu hộ token cho kênh đó để không lãng phí
                    if other_ch_id and cand_yt_id:
                        try:
                            other_dir = base_dir / "credentials" / "channels" / other_ch_id
                            other_dir.mkdir(parents=True, exist_ok=True)
                            other_tok_file = other_dir / "youtube_token.json"
                            other_pool_file = other_dir / f"youtube_token_{acc_id}.json"
                            other_dict = json.loads(cand_creds.to_json())
                            other_dict["channel_id"] = cand_yt_id
                            other_dict["channel_title"] = cand_yt_title or other_ch_name
                            if cand_custom_url:
                                other_dict["custom_url"] = cand_custom_url
                            save_encrypted_json(other_pool_file, other_dict)
                            if (
                                not other_tok_file.exists()
                                or acc_id == other_primary_acc
                                or not _check_channel_token_health(other_ch_id, other_tok_file).get("valid")
                            ):
                                save_encrypted_json(other_tok_file, other_dict)
                            _channel_token_health_cache.pop(other_ch_id, None)
                            logger.info(f"🔄 [OAuth Bonus Save] Đã lưu hộ token cho kênh '{other_ch_name}' ({other_ch_id}), tiếp tục chờ người dùng chọn đúng kênh '{ch_name}'.")
                        except Exception as save_other_err:
                            logger.warning(f"Lỗi lưu hộ token cho kênh {other_ch_id}: {save_other_err}")

                    retry_url, _ = flow.authorization_url(prompt="select_account consent", access_type="offline")
                    picked_label = f"'{cand_yt_title or cand_yt_id}'" if cand_yt_id else "Tài khoản Gmail chưa có kênh YouTube"
                    saved_bonus_note = f" (Hệ thống đã lưu hộ token này cho '{other_ch_name}')" if other_ch_name else ""
                    gmail_hint = f" thuộc Gmail {expected_gmail}" if expected_gmail else ""
                    err_msg = (
                        f"⚠️ Bạn vừa bấm chọn nhầm kênh {picked_label} trên Google, "
                        f"trong khi đang cần cấp quyền cho kênh '{ch_name}'{gmail_hint}!{saved_bonus_note} "
                        f"👉 Kênh '{ch_name}' vẫn CHƯA được cấp quyền. Hãy bấm nút mở lại trang đăng nhập Google và chọn đúng kênh '{ch_name}'!"
                    )
                    _oauth_sessions[session_key] = {
                        "status": "error",
                        "channel_id": channel_id,
                        "channel_name": ch_name,
                        "expected_gmail": expected_gmail,
                        "auth_url": retry_url,
                        "error": err_msg,
                        "message": err_msg,
                    }
                    verify_page["html"] = (
                        f"<html><head><meta charset='utf-8'><title>Chọn Nhầm Kênh YouTube</title></head>"
                        f"<body style='font-family:system-ui,sans-serif;text-align:center;padding:50px 20px;background:#0f172a;color:#f8fafc'>"
                        f"<div style='max-width:560px;margin:0 auto;background:#1e293b;border:2px solid #f59e0b;border-radius:16px;padding:32px;'>"
                        f"<div style='font-size:52px;margin-bottom:12px;'>⚠️</div>"
                        f"<h2 style='color:#fbbf24;margin:0 0 12px 0;'>Bạn Vừa Chọn Nhầm Kênh Trên Google!</h2>"
                        f"<p style='color:#e2e8f0;font-size:15px;line-height:1.6;'>"
                        f"Bạn vừa chọn: <strong style='color:#f87171;'>{html_lib.escape(picked_label)}</strong><br>"
                        f"Kênh đang cần cấp quyền: <strong style='color:#34d399;'>{safe_ch_name_html}</strong>"
                        f"{f' <span style=\"color:#93c5fd;\">({html_lib.escape(expected_gmail)})</span>' if expected_gmail else ''}"
                        f"</p>"
                        f"<p style='color:#94a3b8;font-size:13.5px;line-height:1.5;'>"
                        f"Khi Google hiện danh sách tài khoản, hãy chọn Gmail quản lý rồi <strong>bấm vào đúng dòng tên kênh '{safe_ch_name_html}'</strong> (Brand Account) ở bước tiếp theo."
                        f"</p>"
                        f"<a href='{html_lib.escape(retry_url)}' style='display:inline-block;margin-top:16px;padding:14px 28px;background:linear-gradient(135deg,#f59e0b,#d97706);color:#0f172a;text-decoration:none;border-radius:10px;font-weight:800;font-size:15px;'>"
                        f"🔄 BẤM VÀO ĐÂY ĐỂ CHỌN LẠI ĐÚNG KÊNH '{safe_ch_name_html}'"
                        f"</a>"
                        f"</div></body></html>"
                    )
                    verify_event.set()
                    start_t = time.time()  # Gia hạn thêm 3 phút để người dùng chọn lại kênh đúng
                    continue

                # Đã chọn đúng kênh cần cấp quyền!
                creds = cand_creds
                real_yt_title = cand_yt_title
                real_yt_id = cand_yt_id
                custom_url = cand_custom_url
                final_title_html = html_lib.escape(str(real_yt_title or ch_name))
                verify_page["html"] = (
                    f"<html><head><meta charset='utf-8'><title>Cấp Quyền Thành Công</title></head>"
                    f"<body style='font-family:system-ui,sans-serif;text-align:center;padding:60px;background:#0f172a;color:#f8fafc'>"
                    f"<div style='max-width:480px;margin:0 auto;background:#1e293b;border:1px solid rgba(52,211,153,0.4);border-radius:16px;padding:36px;'>"
                    f"<div style='font-size:54px;margin-bottom:12px;'>🎉</div>"
                    f"<h2 style='color:#34d399;margin:0 0 10px 0;'>Đã Kết Nối Đúng Kênh '{final_title_html}'!</h2>"
                    f"<p style='color:#94a3b8;font-size:14px;'>Token OAuth mới đã được lưu và kích hoạt thành công.<br>Cửa sổ này sẽ tự động đóng sau 2 giây...</p>"
                    f"</div>"
                    f"<script>setTimeout(function(){{try{{window.close();}}catch(e){{}}}},2000);</script>"
                    f"</body></html>"
                )
                verify_event.set()
                break

            server_active["running"] = False
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

            # Lưu token vào credentials/channels/{channel_id}/ (gồm cả youtube_token_{acc_id}.json cho Quota Pool)
            ch_dir = base_dir / "credentials" / "channels" / channel_id
            ch_dir.mkdir(parents=True, exist_ok=True)
            token_file = ch_dir / "youtube_token.json"
            pool_token_file = ch_dir / f"youtube_token_{acc_id}.json"

            token_dict = json.loads(creds.to_json())
            token_dict["channel_id"] = real_yt_id or expected_yt_id or channel_id
            token_dict["channel_title"] = real_yt_title or ch_name
            if custom_url:
                token_dict["custom_url"] = custom_url

            save_encrypted_json(pool_token_file, token_dict)
            if (
                not token_file.exists()
                or acc_id == primary_acc_id
                or not account_id
                or not _check_channel_token_health(channel_id, token_file).get("valid")
            ):
                save_encrypted_json(token_file, token_dict)

            # Xóa cache sức khỏe token ngay lập tức để UI và PublisherAgent nhận token mới
            _channel_token_health_cache.pop(channel_id, None)
            try:
                from app.agents.publisher.agent import PublisherAgent
                PublisherAgent._token_health_cache.clear()
            except Exception:
                pass

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
            _write_channels_yaml(cfg)

            succ_msg = f"Đã kết nối thành công kênh '{ch_name}'"
            if real_yt_title:
                succ_msg += f" (Kênh YouTube: {real_yt_title})"

            _oauth_sessions[session_key] = {
                "status": "success",
                "channel_id": channel_id,
                "channel_title": real_yt_title or ch_name,
                "real_title": real_yt_title or ch_name,
                "real_id": real_yt_id,
                "message": succ_msg
            }
            logger.info(f"OAuth thành công cho kênh {channel_id}: {succ_msg}")

        except Exception as ex:
            server_active["running"] = False
            verify_event.set()
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
        "expected_gmail": expected_gmail,
        "expected_yt_id": expected_yt_id,
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
    _channel_token_health_cache.pop(channel_id, None)
    return {"success": True, "message": f"Đã ngắt kết nối YouTube cho kênh '{channel_id}'"}


