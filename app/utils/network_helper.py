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


_zeroconf_instance = None
_service_info = None

def start_mdns_broadcaster(port: int = 8000, host_name: str = "aiyoutube") -> bool:
    """
    Phát sóng mDNS (Multicast DNS) trong mạng Wi-Fi LAN.
    Cho phép mọi thiết bị (iPhone, Android, PC) chỉ cần truy cập:
    http://aiyoutube.local:8000
    Dù chuyển đổi chạy server giữa máy A và máy B thì địa chỉ này vẫn giữ nguyên!
    """
    global _zeroconf_instance, _service_info
    try:
        from zeroconf import Zeroconf, ServiceInfo
    except ImportError:
        logger.warning("[mDNS] Chưa cài thư viện zeroconf. Bỏ qua phát sóng tên miền .local")
        return False

    try:
        if _zeroconf_instance is not None:
            stop_mdns_broadcaster()

        lan_ip = get_lan_ip()
        if lan_ip == "127.0.0.1":
            return False

        server_name = f"{host_name}.local."
        service_name = f"AI-YouTube-Studio._http._tcp.local."
        
        _zeroconf_instance = Zeroconf()
        _service_info = ServiceInfo(
            type_="_http._tcp.local.",
            name=service_name,
            addresses=[socket.inet_aton(lan_ip)],
            port=port,
            server=server_name,
            properties={
                "path": "/",
                "app": "ai-youtube-studio",
                "version": "2.0"
            }
        )
        _zeroconf_instance.register_service(_service_info)
        logger.info(f"[mDNS] Đã phát sóng tên miền cố định: http://{host_name}.local:{port} -> IP {lan_ip}")
        return True
    except Exception as e:
        logger.warning(f"[mDNS] Không thể phát sóng mDNS: {e}")
        return False


def stop_mdns_broadcaster():
    """Hủy đăng ký dịch vụ mDNS khi server tắt"""
    global _zeroconf_instance, _service_info
    try:
        if _zeroconf_instance and _service_info:
            _zeroconf_instance.unregister_service(_service_info)
            _zeroconf_instance.close()
            _zeroconf_instance = None
            _service_info = None
            logger.info("[mDNS] Đã hủy phát sóng tên miền mDNS.")
    except Exception as e:
        logger.debug(f"[mDNS] Lỗi khi hủy mDNS: {e}")


def get_network_info() -> dict:
    """Trả về chi tiết các URL truy cập Web Dashboard"""
    port = int(os.getenv("PORT", 8000))
    lan_ip = get_lan_ip()
    host_name = os.getenv("MDNS_HOSTNAME", "aiyoutube")
    mdns_url = f"http://{host_name}.local:{port}"
    return {
        "port": port,
        "lan_ip": lan_ip,
        "local_url": f"http://127.0.0.1:{port}",
        "localhost_url": f"http://localhost:{port}",
        "wifi_url": f"http://{lan_ip}:{port}",
        "mdns_url": mdns_url,
        "fixed_domain": f"{host_name}.local",
        "is_lan_available": lan_ip != "127.0.0.1"
    }
