import os
import socket
import logging

logger = logging.getLogger(__name__)

def get_lan_ip() -> str:
    """
    Tự động phát hiện địa chỉ IPv4 nội bộ của máy trong mạng Wi-Fi / LAN.
    Đảm bảo trả về đúng IP của card mạng Wi-Fi/Ethernet đang kết nối Internet.
    """
    # 1. Thử qua socket UDP kết nối tới IP công cộng (không thực sự gửi gói tin)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    # 2. Thử qua socket kết nối Gateway hoặc DNS nội bộ
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    # 3. Quét danh sách IP từ hostname
    try:
        hostname = socket.gethostname()
        _, _, ips = socket.gethostbyname_ex(hostname)
        for ip in ips:
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except Exception:
        pass

    # Fallback mặc định
    return "127.0.0.1"


def get_network_info() -> dict:
    """Trả về chi tiết các URL truy cập Web Dashboard"""
    port = int(os.getenv("PORT", 8000))
    lan_ip = get_lan_ip()
    return {
        "port": port,
        "lan_ip": lan_ip,
        "local_url": f"http://127.0.0.1:{port}",
        "localhost_url": f"http://localhost:{port}",
        "wifi_url": f"http://{lan_ip}:{port}",
        "is_lan_available": lan_ip != "127.0.0.1"
    }
