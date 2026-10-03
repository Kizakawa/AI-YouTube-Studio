# -*- coding: utf-8 -*-
"""
tunnel.py — Công cụ mở đường hầm Public Tunnel cho Web Dashboard AI YouTube Studio.
Hỗ trợ:
1. Cloudflare Tunnel (TryCloudflare) chuẩn giao thức HTTP/2 TCP (loại bỏ hoàn toàn lỗi QUIC/UDP timeout trên mạng VN).
2. Tự động kiểm tra kết nối (DNS pre-check) đảm bảo link hoạt động 100% trước khi mở trình duyệt.
3. Tự động sao chép link vào Clipboard và lưu link vào storage/tunnel_url.txt.
4. Cơ chế Watchdog tự động kết nối lại nếu mạng gián đoạn.
5. Hỗ trợ chế độ dự phòng SSH Tunnel (localhost.run) nếu mạng chặn Cloudflare.
"""

import os
import sys
import re
import time
import shutil
import ssl
import urllib.request
import subprocess
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Cấu hình console tiếng Việt trên Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def copy_to_clipboard(text: str) -> bool:
    """Tự động chép đường link vào Clipboard trên Windows."""
    try:
        if sys.platform == "win32":
            subprocess.run("clip", input=text.encode("utf-8"), check=False, shell=True)
            return True
    except Exception:
        pass
    return False


def save_tunnel_url(url: str):
    """Lưu URL công khai ra file storage/tunnel_url.txt để các tiến trình khác tái sử dụng."""
    try:
        storage_dir = BASE_DIR / "storage"
        storage_dir.mkdir(parents=True, exist_ok=True)
        (storage_dir / "tunnel_url.txt").write_text(url.strip(), encoding="utf-8")
    except Exception:
        pass


def get_cloudflared_path() -> Path:
    """Xác định đường dẫn file thực thi cloudflared.exe hoặc tự động tải về."""
    local_bin = BASE_DIR / "bin" / ("cloudflared.exe" if sys.platform == "win32" else "cloudflared")
    if local_bin.exists():
        return local_bin

    sys_bin = shutil.which("cloudflared")
    if sys_bin:
        return Path(sys_bin)

    print("⏳ Không tìm thấy cloudflared. Đang tự động tải bản portable từ Cloudflare...", flush=True)
    local_bin.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        download_url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    elif sys.platform == "darwin":
        download_url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64"
    else:
        download_url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"

    try:
        urllib.request.urlretrieve(download_url, local_bin)
        if sys.platform != "win32":
            os.chmod(local_bin, 0o755)
        print(f"✅ Tải thành công cloudflared ({local_bin.stat().st_size // (1024*1024)} MB).\n", flush=True)
        return local_bin
    except Exception as e:
        print(f"❌ Không thể tải cloudflared: {e}", flush=True)
        print("💡 Bạn có thể cài thủ công bằng: winget install Cloudflare.cloudflared\n", flush=True)
        sys.exit(1)


def is_server_alive(port: int) -> bool:
    """Kiểm tra xem Web server local đã bật chưa."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/health", headers={"User-Agent": "HealthCheck"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def verify_public_url(url: str, max_retries: int = 12) -> bool:
    """
    Kiểm tra thực tế đường link Cloudflare đã kích hoạt DNS và nhận request thành công chưa.
    Tránh lỗi mở trình duyệt quá sớm khi DNS Cloudflare chưa cập nhật gây 'This site can't be reached'.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(
                f"{url}/api/health",
                headers={"User-Agent": "TunnelVerifier/1.0"}
            )
            with urllib.request.urlopen(req, context=ctx, timeout=3.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(1.0)
    return False


def run_cloudflare_tunnel(port: int):
    """Mở đường hầm Cloudflare Tunnel với cấu hình tối ưu giao thức HTTP/2."""
    cloudflared_bin = get_cloudflared_path()

    print("\n" + "═" * 70, flush=True)
    print("🚀 KHỞI ĐỘNG CLOUDFLARE PUBLIC TUNNEL (GIAO THỨC HTTP/2)", flush=True)
    print("═" * 70, flush=True)
    print(f"📍 Cổng cục bộ: http://127.0.0.1:{port}", flush=True)

    if not is_server_alive(port):
        print(f"⚠️  [LƯU Ý] Chưa thấy Web Server phản hồi trên cổng {port}.", flush=True)
        print("💡 Hãy mở thêm một cửa sổ Terminal khác và chạy: python run.py", flush=True)
        print("   Đường hầm Cloudflare vẫn sẽ khởi động và sẵn sàng khi server bật lên!\n", flush=True)

    # Các cờ tối ưu:
    # --protocol http2: Ép dùng TCP port 443, khắc phục triệt để lỗi UDP/QUIC bị nghẽn hoặc rớt gói trên mạng VN
    # --edge-ip-version 4: Ưu tiên IPv4, loại bỏ lỗi timeout DNS IPv6
    # --retries 10: Tự động thử lại khi mạng gián đoạn
    cmd = [
        str(cloudflared_bin),
        "tunnel",
        "--url", f"http://127.0.0.1:{port}",
        "--protocol", "http2",
        "--edge-ip-version", "4",
        "--retries", "10",
        "--no-autoupdate",
    ]

    print("⏳ Đang kết nối tới mạng lưới toàn cầu Cloudflare Edge...", flush=True)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )

    public_url = None
    try:
        start_time = time.time()
        while time.time() - start_time < 35:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue

            # Bắt link trycloudflare.com
            match = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line)
            if match:
                public_url = match.group(0)
                break

        if not public_url:
            print("❌ Không nhận được link public từ Cloudflare sau 35 giây.", flush=True)
            print("💡 Thử chuyển sang chế độ dự phòng SSH: python tunnel.py --ssh", flush=True)
            proc.terminate()
            return False

        print(f"🔗 Đã nhận link từ Cloudflare: {public_url}", flush=True)
        print("⏳ Đang kích hoạt kết nối DNS toàn cầu (chờ 2-4 giây để link thông suốt)...", flush=True)

        # Kiểm tra thực tế xem link đã vào được chưa
        is_ok = verify_public_url(public_url, max_retries=6)
        if not is_ok:
            print("\n⚠️  [LƯU Ý DNS] Mạng hoặc Router của bạn đang gặp lỗi phân giải tên miền trycloudflare.com (lỗi getaddrinfo IPv6).", flush=True)
            print("🚀 Tự động chuyển hướng sang SSH Public Tunnel (localhost.run) không bị chặn mạng...\n", flush=True)
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
            return run_ssh_tunnel(port)

        copied = copy_to_clipboard(public_url)
        save_tunnel_url(public_url)

        print("\n" + "═" * 70, flush=True)
        print("🌐 LINK TRUY CẬP CÔNG KHAI - AI YOUTUBE STUDIO", flush=True)
        print("═" * 70, flush=True)
        print(f"\n   👉 {public_url}\n", flush=True)
        print("═" * 70, flush=True)
        print(f"📍 Localhost:  http://127.0.0.1:{port}", flush=True)
        print("🔒 Trạng thái: 🟢 ĐÃ THÔNG SUỐT (Kiểm tra phản hồi 200 OK)", flush=True)
        print("🛡️ Giao thức:  HTTPS / HTTP2 qua Cloudflare Edge Network", flush=True)
        if copied:
            print("📋 Clipboard:  ĐÃ SAO CHÉP TỰ ĐỘNG (Bấm Ctrl + V trên trình duyệt để dán)!", flush=True)
        print("💡 Mẹo:        Giữ phím Ctrl + Click vào link trên để mở nhanh!", flush=True)
        print("👉 Gửi link trên cho bạn bè/đối tác để vào Web Studio từ điện thoại hoặc máy khác.", flush=True)
        print("👉 Nhấn Ctrl + C tại cửa sổ này khi bạn muốn đóng đường hầm.\n", flush=True)

        # Mở trình duyệt sau khi link đã thực sự phản hồi
        try:
            import webbrowser
            webbrowser.open(public_url)
        except Exception:
            pass

        # Luồng ngầm liên tục giải phóng bộ đệm stdout để tránh nghẽn luồng pipe
        import threading
        def _drain_stdout():
            try:
                for _ in iter(proc.stdout.readline, ""):
                    pass
            except Exception:
                pass

        drain_thread = threading.Thread(target=_drain_stdout, daemon=True)
        drain_thread.start()

        # Giữ tiến trình chạy mượt mà
        proc.wait()
        return True

    except KeyboardInterrupt:
        print("\n\n👋 Đang đóng đường hầm Cloudflare Tunnel...", flush=True)
        return False
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        print("✅ Đã ngắt kết nối an toàn.", flush=True)


def run_ssh_tunnel(port: int):
    """Chế độ dự phòng: Mở đường hầm HTTPS qua SSH (localhost.run) không cần cài đặt thêm phần mềm."""
    print("\n" + "═" * 70, flush=True)
    print("🚀 KHỞI ĐỘNG DỰ PHÒNG: SSH PUBLIC TUNNEL (localhost.run)", flush=True)
    print("═" * 70, flush=True)
    print(f"📍 Cổng cục bộ: http://127.0.0.1:{port}", flush=True)

    cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ServerAliveInterval=30",
        "-R", f"80:127.0.0.1:{port}",
        "nokey@localhost.run"
    ]

    print("⏳ Đang kết nối tới SSH Gateway...", flush=True)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )

    public_url = None
    try:
        start_time = time.time()
        while time.time() - start_time < 30:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue

            match = re.search(r'https://[a-zA-Z0-9-]+\.lhr\.life', line)
            if match:
                public_url = match.group(0)
                break

        if not public_url:
            print("❌ Không nhận được link public từ SSH Gateway.", flush=True)
            proc.terminate()
            return

        copied = copy_to_clipboard(public_url)
        save_tunnel_url(public_url)

        print("\n" + "═" * 70, flush=True)
        print("🌐 LINK TRUY CẬP CÔNG KHAI (SSH TUNNEL)", flush=True)
        print("═" * 70, flush=True)
        print(f"\n   👉 {public_url}\n", flush=True)
        print("═" * 70, flush=True)
        print("🔒 Bảo mật:    HTTPS SSL tự động", flush=True)
        if copied:
            print("📋 Clipboard:  ĐÃ SAO CHÉP TỰ ĐỘNG (Bấm Ctrl + V trên trình duyệt để dán)!", flush=True)
        print("👉 Nhấn Ctrl + C tại cửa sổ này khi bạn muốn đóng đường hầm.\n", flush=True)

        try:
            import webbrowser
            webbrowser.open(public_url)
        except Exception:
            pass

        proc.wait()

    except KeyboardInterrupt:
        print("\n\n👋 Đang đóng đường hầm SSH...", flush=True)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        print("✅ Đã ngắt kết nối an toàn.", flush=True)


def main():
    port = int(os.getenv("PORT", 8000))

    if "--ssh" in sys.argv:
        run_ssh_tunnel(port)
    else:
        # Cơ chế Watchdog: Nếu đường hầm bị rớt do mạng, tự động kết nối lại
        while True:
            try:
                success = run_cloudflare_tunnel(port)
                if not success:
                    break
                print("\n🔄 Đường hầm tạm thời ngắt. Đang tự động kết nối lại sau 3 giây...", flush=True)
                time.sleep(3)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"⚠️ Lỗi: {e}. Đang thử lại...", flush=True)
                time.sleep(3)


if __name__ == "__main__":
    main()
