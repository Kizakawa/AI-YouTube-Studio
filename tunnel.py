# -*- coding: utf-8 -*-
"""
tunnel.py — Công cụ mở đường hầm Cloudflare Tunnel (TryCloudflare) độc lập.
Cho phép chia sẻ Web Dashboard local ra Internet với HTTPS bảo mật, miễn phí 100%,
không cần tài khoản Cloudflare, không cần mở port Router.
"""

import os
import sys
import re
import time
import shutil
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


def copy_to_clipboard(text: str):
    """Tự động chép đường link vào Clipboard trên Windows."""
    try:
        if sys.platform == "win32":
            subprocess.run("clip", input=text.encode("utf-8"), check=False, shell=True)
            return True
    except Exception:
        pass
    return False


def get_cloudflared_path() -> Path:
    """Xác định đường dẫn file thực thi cloudflared.exe hoặc tự động tải về."""
    local_bin = BASE_DIR / "bin" / ("cloudflared.exe" if sys.platform == "win32" else "cloudflared")
    if local_bin.exists():
        return local_bin

    sys_bin = shutil.which("cloudflared")
    if sys_bin:
        return Path(sys_bin)

    # Tự động tải bản portable nếu chưa có
    print("⏳ Không tìm thấy cloudflared. Đang tự động tải bản portable từ Cloudflare...")
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
        print(f"✅ Tải thành công cloudflared ({local_bin.stat().st_size // (1024*1024)} MB).")
        return local_bin
    except Exception as e:
        print(f"❌ Không thể tải cloudflared: {e}")
        print("💡 Bạn có thể cài thủ công bằng: winget install Cloudflare.cloudflared")
        sys.exit(1)


def is_server_alive(port: int) -> bool:
    """Kiểm tra xem Web server local đã bật chưa."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/health")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_tunnel():
    port = int(os.getenv("PORT", 8000))
    cloudflared_bin = get_cloudflared_path()

    print("\n" + "═" * 70, flush=True)
    print("🚀 KHỞI ĐỘNG CLOUDFLARE PUBLIC TUNNEL", flush=True)
    print("═" * 70, flush=True)
    print(f"📍 Cổng cục bộ: http://127.0.0.1:{port}", flush=True)

    if not is_server_alive(port):
        print(f"⚠️  [LƯU Ý] Chưa thấy Web Server phản hồi trên cổng {port}.", flush=True)
        print("💡 Hãy mở thêm một cửa sổ Terminal khác và chạy: python run.py", flush=True)
        print("   Đường hầm Cloudflare vẫn sẽ khởi động và sẵn sàng khi server bật lên!\n", flush=True)

    cmd = [
        str(cloudflared_bin),
        "tunnel",
        "--url", f"http://127.0.0.1:{port}",
        "--http-host-header", f"localhost:{port}",
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
        while time.time() - start_time < 30:
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
            print("❌ Không nhận được link public từ Cloudflare sau 30 giây.", flush=True)
            proc.terminate()
            return

        copied = copy_to_clipboard(public_url)

        print("\n" + "╔" + "═" * 68 + "╗", flush=True)
        print("║" + " " * 18 + "🌐 LINK TRUY CẬP CÔNG KHAI ĐÃ SẴN SÀNG" + " " * 12 + "║", flush=True)
        print("╠" + "═" * 68 + "╣", flush=True)
        print(f"║  🔗 Public URL:  {public_url:<49}║", flush=True)
        print(f"║  🏠 Local URL:   http://localhost:{port:<39}║", flush=True)
        print("║  🔒 Bảo mật:     HTTPS / SSL tự động bởi Cloudflare Edge          ║", flush=True)
        if copied:
            print("║  📋 Clipboard:   ĐÃ TỰ ĐỘNG SAO CHÉP LINK (Chỉ cần Ctrl + V)!     ║", flush=True)
        print("╚" + "═" * 68 + "╝\n", flush=True)
        print("👉 Gửi link này cho bạn bè/đối tác để họ truy cập Web Studio từ xa!", flush=True)
        print("👉 Nhấn Ctrl + C để dừng đóng gói và đóng đường hầm khi dùng xong.\n", flush=True)

        # Luồng ngầm liên tục giải phóng bộ đệm stdout để tránh nghẽn luồng mạng OS pipe
        import threading
        def _drain_stdout():
            try:
                for _ in iter(proc.stdout.readline, ""):
                    pass
            except Exception:
                pass

        drain_thread = threading.Thread(target=_drain_stdout, daemon=True)
        drain_thread.start()

        # Giữ tiến trình chạy mượt mà không block IO
        proc.wait()

    except KeyboardInterrupt:
        print("\n\n👋 Đang đóng đường hầm Cloudflare Tunnel...", flush=True)
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


if __name__ == "__main__":
    start_tunnel()
