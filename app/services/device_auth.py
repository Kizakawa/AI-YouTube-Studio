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

import hmac
import hashlib
import base64
import json
import logging
import os
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from config.settings import settings
from app.services.channel_intelligence import send_telegram_broadcast
from app.utils.security import get_master_key

logger = logging.getLogger(__name__)

STORAGE_DIR = Path(settings.BASE_DIR) / "storage"
STORAGE_FILE = STORAGE_DIR / "approved_devices.json"
PENDING_FILE = STORAGE_DIR / "pending_requests.json"


def _get_signing_key() -> bytes:
    """Khóa bí mật tạo chữ ký HMAC cho device token, ưu tiên khóa cấu hình hoặc Master Key độc lập."""
    secret = os.getenv("DEVICE_GATEKEEPER_SECRET", "").strip() or (settings.TELEGRAM_BOT_TOKEN or "").strip()
    if secret:
        return secret.encode("utf-8")
    return get_master_key()


def generate_signed_token(client_ip: str, device_name: str, approver: str = "Admin", duration_days: int = 7) -> str:
    """
    Tạo token chữ ký số HMAC-SHA256 lưu giữ trực tiếp trên thiết bị (Client-Side State).
    Đặc tính:
    - Chứa thời hạn hết hạn (exp = 7 ngày).
    - Có chữ ký bí mật HMAC-SHA256.
    - Miễn nhiễm hoàn toàn khi Render ngủ đông, khởi động lại hoặc redeploy!
    """
    now = time.time()
    expires_at = now + (duration_days * 86400)
    payload = {
        "ip": client_ip,
        "dev": device_name,
        "by": approver,
        "iat": int(now),
        "exp": int(expires_at),
        "v": 2
    }
    payload_json = json.dumps(payload, separators=(',', ':'))
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("utf-8").rstrip("=")
    signature = hmac.new(_get_signing_key(), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"dtk_{payload_b64}.{signature}"


def verify_signed_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Kiểm tra tính hợp lệ của token chữ ký số HMAC.
    Nếu chữ ký khớp và thời hạn exp > hiện tại: thiết bị được tin cậy 100% trong 7 ngày.
    """
    if not token or not isinstance(token, str):
        return None
    token = token.strip()
    if not token.startswith("dtk_"):
        return None
    try:
        parts = token[4:].split(".")
        if len(parts) != 2:
            return None
        payload_b64, signature = parts

        expected_sig = hmac.new(_get_signing_key(), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected_sig):
            logger.warning("Device token signature mismatch!")
            return None

        padding = 4 - (len(payload_b64) % 4)
        if padding != 4:
            payload_b64 += "=" * padding

        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("utf-8")).decode("utf-8"))

        if payload.get("exp", 0) < time.time():
            logger.info("Device token has expired (> 7 days)")
            return None

        return payload
    except Exception as e:
        logger.debug(f"Error verifying signed token: {e}")
        return None


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
    clean_ip = ip.strip().lower()
    return clean_ip in ("127.0.0.1", "localhost", "::1", "testclient")


def extract_client_ip(request: Any) -> Tuple[str, bool]:
    """
    Trích xuất địa chỉ IP thực của client và xác định xem kết nối có phải là truy cập cục bộ trực tiếp hay không.
    Chống hoàn toàn tấn công giả mạo X-Forwarded-For:
    - Chỉ coi là local trực tiếp (is_direct_local=True) khi socket kết nối trực tiếp từ loopback VÀ không có bất kỳ header Proxy/Tunnel nào.
    - Nếu kết nối đi qua local Reverse Proxy / Cloudflare Tunnel (direct_host là loopback nhưng có header proxy), ưu tiên CF-Connecting-IP hoặc phần tử cuối của X-Forwarded-For, và luôn đặt is_direct_local=False.
    - Nếu kết nối trực tiếp từ mạng ngoài/LAN (direct_host không phải loopback), bỏ qua hoàn toàn X-Forwarded-For do client tự chèn.
    """
    direct_host = (request.client.host if getattr(request, "client", None) else "unknown").strip()
    headers = getattr(request, "headers", {}) or {}
    cf_ip = headers.get("CF-Connecting-IP", "").strip()
    x_real_ip = headers.get("X-Real-IP", "").strip()
    xff = headers.get("X-Forwarded-For", "").strip()
    cf_ray = headers.get("CF-Ray", "").strip()

    has_proxy_headers = bool(cf_ip or x_real_ip or xff or cf_ray)
    direct_is_loopback = is_local_ip(direct_host)

    if direct_is_loopback and not has_proxy_headers:
        return direct_host, True

    if direct_is_loopback and has_proxy_headers:
        if cf_ip:
            return cf_ip, False
        if x_real_ip:
            return x_real_ip, False
        if xff:
            # Lấy IP cuối cùng do Reverse Proxy cục bộ gắn vào, không lấy phần tử đầu do client kiểm soát
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if parts:
                return parts[-1], False
        return direct_host, False

    # Kết nối trực tiếp từ IP bên ngoài -> Không tin tưởng X-Forwarded-For của client
    return direct_host, False


def is_device_token_valid(
    token: Optional[str] = None,
    client_ip: Optional[str] = None,
    is_direct_local: Optional[bool] = None
) -> bool:
    """
    Kiểm tra xem thiết bị có được cấp quyền truy cập không.
    1. Kết nối trực tiếp từ Localhost (không qua proxy/tunnel) luôn hợp lệ.
    2. Token chữ ký số HMAC (dtk_...) hợp lệ và còn hạn 7 ngày -> Luôn hợp lệ.
    3. Token đã lưu trong approved_devices.json -> Hợp lệ.
    """
    if not is_gatekeeper_enabled():
        return True

    if is_direct_local is True:
        return True
    if is_direct_local is None and is_local_ip(client_ip):
        return True

    # 1. Kiểm tra Signed Cryptographic Token (Chống mất khi Render restart / ngủ đông)
    if token:
        signed_payload = verify_signed_token(token)
        if signed_payload is not None:
            return True

    # 2. Kiểm tra danh sách thiết bị lưu tạm thời trên đĩa theo Token
    devices = _load_approved_devices()
    now = time.time()

    if token and token in devices:
        if devices[token].get("expires_at", 0) > now:
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
    Chống spam: nếu IP này đã có request pending trong 1 phút gần nhất thì tái sử dụng.
    """
    pending = _load_pending_requests()
    now = time.time()

    # Kiểm tra xem thiết bị này (cùng IP và cùng User-Agent) đã có yêu cầu pending gần đây chưa
    for r_id, r in pending.items():
        if (
            r.get("client_ip") == client_ip
            and r.get("user_agent") == user_agent
            and r.get("status") == "pending"
            and (now - r.get("created_at", 0) < 60)
        ):
            return {
                "request_id": r_id,
                "status": "pending",
                "device_name": r.get("device_name"),
                "client_ip": client_ip
            }

    req_id = "dev_" + secrets.token_hex(8)
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
    """Kiểm tra trạng thái của một yêu cầu phê duyệt theo đúng req_id đã cấp."""
    if not req_id:
        return {"status": "not_found"}

    pending = _load_pending_requests()
    req = pending.get(req_id)
    if not req:
        return {"status": "not_found"}

    return {
        "request_id": req_id,
        "status": req.get("status", "pending"),
        "token": req.get("token") if req.get("status") == "approved" else None
    }


def approve_access_request(req_id: str, admin_name: str = "Admin") -> Optional[Dict[str, Any]]:
    """Admin bấm [✅ Đồng Ý] trên Telegram (chỉ duyệt đúng req_id được chỉ định)"""
    if not req_id:
        return None
    pending = _load_pending_requests()
    req = pending.get(req_id)

    if not req:
        logger.warning(f"Không tìm thấy yêu cầu duyệt thiết bị: {req_id}")
        return None

    # Sinh signed token có chữ ký số bí mật, hạn 7 ngày
    device_token = generate_signed_token(
        client_ip=req.get("client_ip", ""),
        device_name=req.get("device_name", "Thiết bị"),
        approver=admin_name,
        duration_days=7
    )
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

    logger.info(f"✅ Đã phê duyệt thiết bị {req.get('device_name')} ({req['client_ip']}) bởi {admin_name} (Signed Token)")
    return req


def reject_access_request(req_id: str, admin_name: str = "Admin") -> Optional[Dict[str, Any]]:
    """Admin bấm [❌ Từ Chối] trên Telegram (chỉ từ chối đúng req_id được chỉ định)"""
    if not req_id:
        return None
    pending = _load_pending_requests()
    req = pending.get(req_id)
    if not req:
        return None

    req["status"] = "rejected"
    req["token"] = None
    pending[req_id] = req
    _save_pending_requests(pending)

    logger.info(f"❌ Đã từ chối thiết bị {req.get('device_name')} ({req['client_ip']}) bởi {admin_name}")
    return req
