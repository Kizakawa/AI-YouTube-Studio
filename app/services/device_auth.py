"""
app/services/device_auth.py — Quản lý xác thực thiết bị 2 lớp qua Telegram (Telegram 2FA Device Gatekeeper)

Tính năng:
- Mọi thiết bị từ xa (khác 127.0.0.1/localhost) khi truy cập Web Studio sẽ phải chờ Admin duyệt trên Telegram.
- Máy chủ (127.0.0.1) tự động được tin cậy.
- Khi có thiết bị mới vào web, hệ thống tự động bắn tin nhắn kèm 2 nút [✅ Đồng Ý] / [❌ Từ Chối] vào nhóm Telegram.
- Admin bấm [✅ Đồng Ý], thiết bị đó lập tức được cấp token có hạn 7 ngày và tự động mở khóa Web Dashboard.
- Dữ liệu thiết bị đã duyệt được lưu tại storage/approved_devices.json để không bị mất khi restart server.
"""

import json
import logging
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List

from config.settings import settings
from app.services.channel_intelligence import send_telegram_broadcast

logger = logging.getLogger(__name__)

STORAGE_FILE = Path(settings.BASE_DIR) / "storage" / "approved_devices.json"

# Bộ nhớ tạm lưu các yêu cầu đang chờ duyệt (Pending Requests)
# request_id -> { "ip": str, "user_agent": str, "device_name": str, "status": "pending"|"approved"|"rejected", "created_at": float, "token": str|None }
_pending_requests: Dict[str, Dict[str, Any]] = {}


def _load_approved_devices() -> Dict[str, Dict[str, Any]]:
    """Tải danh sách các thiết bị đã được Admin phê duyệt từ file JSON"""
    if not STORAGE_FILE.exists():
        return {}
    try:
        with open(STORAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Dọn dẹp các token đã quá hạn
            now = time.time()
            valid_devices = {k: v for k, v in data.items() if v.get("expires_at", 0) > now}
            return valid_devices
    except Exception as e:
        logger.warning(f"Lỗi đọc {STORAGE_FILE}: {e}")
        return {}


def _save_approved_devices(devices: Dict[str, Dict[str, Any]]):
    """Lưu danh sách thiết bị đã duyệt xuống file JSON"""
    STORAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(STORAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(devices, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Lỗi ghi {STORAGE_FILE}: {e}")


def is_local_ip(ip: str) -> bool:
    """Kiểm tra xem IP có phải là localhost của chính máy chủ đang chạy bot không"""
    if not ip:
        return False
    clean_ip = ip.strip()
    return clean_ip in ("127.0.0.1", "localhost", "::1", "testclient")


def is_device_token_valid(token: Optional[str]) -> bool:
    """Kiểm tra token thiết bị có hợp lệ và còn hạn không"""
    if not token:
        return False
    devices = _load_approved_devices()
    device_info = devices.get(token)
    if not device_info:
        return False
    if device_info.get("expires_at", 0) < time.time():
        return False
    return True


def parse_device_info(user_agent: str) -> str:
    """Trích xuất tên thiết bị và trình duyệt ngắn gọn, thân thiện"""
    ua = (user_agent or "").lower()
    os_name = "Thiết bị"
    if "windows" in ua:
        os_name = "Máy tính Windows"
    elif "macintosh" in ua or "mac os" in ua:
        os_name = "Máy tính Mac"
    elif "iphone" in ua:
        os_name = "iPhone"
    elif "ipad" in ua:
        os_name = "iPad"
    elif "android" in ua:
        os_name = "Điện thoại Android"
    elif "linux" in ua:
        os_name = "Máy tính Linux"

    browser_name = "Trình duyệt"
    if "edg/" in ua:
        browser_name = "Edge"
    elif "chrome" in ua and "safari" in ua and "edg" not in ua:
        browser_name = "Chrome"
    elif "safari" in ua and "chrome" not in ua:
        browser_name = "Safari"
    elif "firefox" in ua:
        browser_name = "Firefox"
    elif "coccoc" in ua:
        browser_name = "Cốc Cốc"

    return f"{os_name} ({browser_name})"


def create_access_request(client_ip: str, user_agent: str) -> Dict[str, Any]:
    """
    Tạo yêu cầu truy cập mới từ thiết bị từ xa và bắn tin nhắn duyệt về nhóm Telegram
    """
    req_id = "dev_" + secrets.token_hex(4)
    device_name = parse_device_info(user_agent)
    now_str = datetime.now().strftime("%H:%M:%S %d/%m/%Y")

    _pending_requests[req_id] = {
        "request_id": req_id,
        "client_ip": client_ip,
        "user_agent": user_agent,
        "device_name": device_name,
        "status": "pending",
        "created_at": time.time(),
        "token": None
    }

    # Nội dung thông báo gửi vào nhóm Telegram
    alert_text = (
        f"🛡️ **YÊU CẦU TRUY CẬP THIẾT BỊ MỚI**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💻 **Thiết bị:** {device_name}\n"
        f"🌐 **Địa chỉ IP:** `{client_ip}`\n"
        f"⏰ **Thời gian:** {now_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👉 Có người dùng đang mở Web Studio từ thiết bị này.\n"
        f"Bạn có đồng ý cấp quyền truy cập hệ thống không?"
    )

    inline_keyboard = [
        [
            {"text": "✅ Đồng Ý Cấp Quyền (7 ngày)", "callback_data": f"approve_device:{req_id}"},
            {"text": "❌ Từ Chối", "callback_data": f"reject_device:{req_id}"}
        ]
    ]

    try:
        # Gửi đến cả admin và các nhóm chat đã đăng ký
        send_telegram_broadcast(alert_text, inline_keyboard=inline_keyboard)
        # Bổ sung gửi trực tiếp vào allowed_groups nếu có
        _broadcast_to_allowed_groups(alert_text, inline_keyboard)
    except Exception as e:
        logger.error(f"Lỗi gửi yêu cầu duyệt thiết bị qua Telegram: {e}")

    return {
        "request_id": req_id,
        "status": "pending",
        "device_name": device_name,
        "client_ip": client_ip
    }


def _broadcast_to_allowed_groups(text: str, inline_keyboard: list):
    """Gửi trực tiếp tin nhắn thông báo vào tất cả các nhóm trong ALLOWED_GROUP_IDS"""
    import urllib.request
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        return
    groups = settings.get_allowed_group_ids()
    for gid in groups:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": gid,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": {"inline_keyboard": inline_keyboard}
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as ex:
            logger.debug(f"Không thể gửi tin duyệt thiết bị tới nhóm {gid}: {ex}")


def get_request_status(req_id: str) -> Dict[str, Any]:
    """Kiểm tra trạng thái của một yêu cầu phê duyệt"""
    req = _pending_requests.get(req_id)
    if not req:
        return {"status": "not_found"}
    return {
        "request_id": req_id,
        "status": req.get("status", "pending"),
        "token": req.get("token")
    }


def approve_access_request(req_id: str, admin_name: str = "Admin") -> Optional[Dict[str, Any]]:
    """Admin bấm [✅ Đồng Ý] trên Telegram"""
    req = _pending_requests.get(req_id)
    if not req:
        return None

    # Sinh token mới có hạn 7 ngày
    device_token = "tok_" + secrets.token_urlsafe(32)
    expires_at = time.time() + (7 * 24 * 3600)  # 7 ngày

    req["status"] = "approved"
    req["token"] = device_token

    devices = _load_approved_devices()
    devices[device_token] = {
        "client_ip": req["client_ip"],
        "device_name": req["device_name"],
        "approved_by": admin_name,
        "approved_at": datetime.now().isoformat(),
        "expires_at": expires_at
    }
    _save_approved_devices(devices)

    logger.info(f"✅ Đã phê duyệt thiết bị {req['device_name']} ({req['client_ip']}) bởi {admin_name}")
    return req


def reject_access_request(req_id: str, admin_name: str = "Admin") -> Optional[Dict[str, Any]]:
    """Admin bấm [❌ Từ Chối] trên Telegram"""
    req = _pending_requests.get(req_id)
    if not req:
        return None

    req["status"] = "rejected"
    req["token"] = None
    logger.info(f"❌ Đã từ chối thiết bị {req['device_name']} ({req['client_ip']}) bởi {admin_name}")
    return req
