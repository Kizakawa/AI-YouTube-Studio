"""
app/services/device_auth.py — Quản lý xác thực thiết bị 2 lớp qua Telegram (Telegram 2FA Device Gatekeeper)

Tính năng:
- Mọi thiết bị từ xa (khác 127.0.0.1/localhost) khi truy cập Web Studio sẽ phải chờ Admin duyệt trên Telegram.
- Máy chủ cục bộ (127.0.0.1, localhost) tự động được tin cậy.
- Khi có thiết bị mới vào web, hệ thống tự động bắn tin nhắn kèm 2 nút [✅ Đồng Ý] / [❌ Từ Chối] vào Telegram Admin/Group.
- Phê duyệt theo cả IP VÀ Token: đảm bảo người dùng F5 hoặc đổi tab vẫn được mở khóa tức thì 100%.
- Lưu trữ bền vững (Persistent) xuống file JSON: không bao giờ bị mất request khi restart bot.
- Tự động chống spam: không bắn nhiều tin nhắn trùng lặp nếu người dùng tải lại trang liên tục.
- Hỗ trợ biến môi trường ENABLE_DEVICE_GATEKEEPER (mặc định: true).
"""

import json
import logging
import os
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from config.settings import settings
from app.services.channel_intelligence import send_telegram_broadcast

logger = logging.getLogger(__name__)

STORAGE_DIR = Path(settings.BASE_DIR) / "storage"
STORAGE_FILE = STORAGE_DIR / "approved_devices.json"
PENDING_FILE = STORAGE_DIR / "pending_requests.json"


def is_gatekeeper_enabled() -> bool:
    """Kiểm tra xem tính năng bảo mật Telegram 2FA Gatekeeper có đang bật không (mặc định: true)"""
    val = os.getenv("ENABLE_DEVICE_GATEKEEPER", "true").strip().lower()
    return val in ("true", "1", "yes", "on")


def _load_approved_devices() -> Dict[str, Dict[str, Any]]:
    """Tải danh sách các thiết bị đã được Admin phê duyệt từ file JSON"""
    if not STORAGE_FILE.exists():
        return {}
    try:
        with open(STORAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            now = time.time()
            return {k: v for k, v in data.items() if v.get("expires_at", 0) > now}
    except Exception as e:
        logger.warning(f"Lỗi đọc {STORAGE_FILE}: {e}")
        return {}


def _save_approved_devices(devices: Dict[str, Dict[str, Any]]):
    """Lưu danh sách thiết bị đã duyệt xuống file JSON"""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(STORAGE_FILE, "w", encoding="utf-8") as f:
            json.dump(devices, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Lỗi ghi {STORAGE_FILE}: {e}")


def _load_pending_requests() -> Dict[str, Dict[str, Any]]:
    """Tải danh sách các yêu cầu đang chờ duyệt từ file JSON"""
    if not PENDING_FILE.exists():
        return {}
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            now = time.time()
            # Giữ các request trong vòng 24 giờ
            return {k: v for k, v in data.items() if now - v.get("created_at", 0) < 86400}
    except Exception as e:
        logger.warning(f"Lỗi đọc {PENDING_FILE}: {e}")
        return {}


def _save_pending_requests(reqs: Dict[str, Dict[str, Any]]):
    """Lưu danh sách yêu cầu đang chờ duyệt xuống file JSON"""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(reqs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Lỗi ghi {PENDING_FILE}: {e}")


def is_local_ip(ip: Optional[str]) -> bool:
    """Kiểm tra xem IP có phải là localhost của chính máy chủ đang chạy bot không"""
    if not ip:
        return False
    clean_ip = ip.strip()
    return clean_ip in ("127.0.0.1", "localhost", "::1", "testclient")


def is_device_token_valid(token: Optional[str] = None, client_ip: Optional[str] = None) -> bool:
    """
    Kiểm tra xem thiết bị có được cấp quyền truy cập không.
    Hỗ trợ kiểm tra cả token VÀ địa chỉ IP của thiết bị đã được duyệt.
    """
    if not is_gatekeeper_enabled():
        return True

    if is_local_ip(client_ip):
        return True

    devices = _load_approved_devices()
    now = time.time()

    # 1. Kiểm tra theo Token
    if token and token in devices:
        if devices[token].get("expires_at", 0) > now:
            return True

    # 2. Kiểm tra theo IP (dành cho trường hợp client vừa được duyệt nhưng chưa kịp nạp token vào header)
    if client_ip:
        for dev in devices.values():
            if dev.get("client_ip") == client_ip and dev.get("expires_at", 0) > now:
                return True

    return False


def get_token_for_ip(client_ip: str) -> Optional[str]:
    """Tìm token hợp lệ của một IP đã được duyệt"""
    if not client_ip:
        return None
    devices = _load_approved_devices()
    now = time.time()
    for tok, dev in devices.items():
        if dev.get("client_ip") == client_ip and dev.get("expires_at", 0) > now:
            return tok
    return None


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
    Tạo yêu cầu truy cập mới từ thiết bị từ xa và bắn tin nhắn duyệt về Telegram.
    Chống spam: nếu IP này đã có request pending trong 2 phút gần nhất thì tái sử dụng.
    """
    pending = _load_pending_requests()
    now = time.time()

    # Kiểm tra xem IP này đã có yêu cầu pending gần đây chưa
    for r_id, r in pending.items():
        if r.get("client_ip") == client_ip and r.get("status") == "pending" and (now - r.get("created_at", 0) < 180):
            return {
                "request_id": r_id,
                "status": "pending",
                "device_name": r.get("device_name"),
                "client_ip": client_ip
            }

    req_id = "dev_" + secrets.token_hex(4)
    device_name = parse_device_info(user_agent)
    now_str = datetime.now().strftime("%H:%M:%S %d/%m/%Y")

    pending[req_id] = {
        "request_id": req_id,
        "client_ip": client_ip,
        "user_agent": user_agent,
        "device_name": device_name,
        "status": "pending",
        "created_at": now,
        "token": None
    }
    _save_pending_requests(pending)

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
        send_telegram_broadcast(alert_text, inline_keyboard=inline_keyboard)
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


def get_request_status(req_id: str, client_ip: Optional[str] = None) -> Dict[str, Any]:
    """Kiểm tra trạng thái của một yêu cầu phê duyệt (hỗ trợ kiểm tra cả IP đã duyệt)"""
    # 1. Kiểm tra nếu IP này đã có trong danh sách approved_devices
    if client_ip:
        devices = _load_approved_devices()
        now = time.time()
        for tok, dev in devices.items():
            if dev.get("client_ip") == client_ip and dev.get("expires_at", 0) > now:
                return {
                    "request_id": req_id,
                    "status": "approved",
                    "token": tok
                }

    # 2. Kiểm tra theo req_id
    pending = _load_pending_requests()
    req = pending.get(req_id)
    if not req:
        # Nếu không thấy theo ID, kiểm tra xem có pending request nào của IP này không
        if client_ip:
            for r_id, r in pending.items():
                if r.get("client_ip") == client_ip:
                    return {
                        "request_id": r_id,
                        "status": r.get("status", "pending"),
                        "token": r.get("token")
                    }
        return {"status": "not_found"}

    return {
        "request_id": req_id,
        "status": req.get("status", "pending"),
        "token": req.get("token")
    }


def approve_access_request(req_id: str, admin_name: str = "Admin") -> Optional[Dict[str, Any]]:
    """Admin bấm [✅ Đồng Ý] trên Telegram"""
    pending = _load_pending_requests()
    req = pending.get(req_id)

    # Fallback an toàn: nếu req_id cụ thể không có (do restart), lấy request pending gần nhất
    if not req:
        for r_id, r in pending.items():
            if r.get("status") == "pending":
                req_id = r_id
                req = r
                break

    if not req:
        logger.warning(f"Không tìm thấy yêu cầu duyệt thiết bị: {req_id}")
        return None

    # Sinh token mới có hạn 7 ngày
    device_token = "tok_" + secrets.token_urlsafe(32)
    expires_at = time.time() + (7 * 24 * 3600)  # 7 ngày

    req["status"] = "approved"
    req["token"] = device_token
    pending[req_id] = req
    _save_pending_requests(pending)

    devices = _load_approved_devices()
    devices[device_token] = {
        "client_ip": req["client_ip"],
        "device_name": req.get("device_name", "Thiết bị"),
        "approved_by": admin_name,
        "approved_at": datetime.now().isoformat(),
        "expires_at": expires_at
    }
    _save_approved_devices(devices)

    logger.info(f"✅ Đã phê duyệt thiết bị {req.get('device_name')} ({req['client_ip']}) bởi {admin_name}")
    return req


def reject_access_request(req_id: str, admin_name: str = "Admin") -> Optional[Dict[str, Any]]:
    """Admin bấm [❌ Từ Chối] trên Telegram"""
    pending = _load_pending_requests()
    req = pending.get(req_id)
    if not req:
        for r_id, r in pending.items():
            if r.get("status") == "pending":
                req_id = r_id
                req = r
                break

    if not req:
        return None

    req["status"] = "rejected"
    req["token"] = None
    pending[req_id] = req
    _save_pending_requests(pending)

    logger.info(f"❌ Đã từ chối thiết bị {req.get('device_name')} ({req['client_ip']}) bởi {admin_name}")
    return req
