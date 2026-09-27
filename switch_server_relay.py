"""
AI YOUTUBE STUDIO — SMART RELAY & AUTO-FAILOVER PROXY
-------------------------------------------------------------------
Công cụ này được dùng khi bạn muốn tắt server xử lý nặng trên máy này
để chuyển sang máy bạn bè, nhưng vẫn muốn các thiết bị khách (điện thoại, iPad)
truy cập vào IP máy này được tự động chuyển hướng / proxy mượt mà sang máy bạn!

Cách dùng:
    py switch_server_relay.py
    Hoặc:
    py switch_server_relay.py --to 192.168.2.5
"""

import sys
import time
import socket
import argparse
import http.server
import socketserver
import urllib.request
from app.utils.network_helper import get_lan_ip

def find_active_server_in_lan(port=8000):
    lan_ip = get_lan_ip()
    parts = lan_ip.split('.')
    if len(parts) != 4 or lan_ip.startswith("127."):
        return None
    
    prefix = f"{parts[0]}.{parts[1]}.{parts[2]}"
    my_last = int(parts[3])
    
    print(f"🔍 Đang quét tìm máy bạn trong mạng Wi-Fi ({prefix}.x:{port})...")
    candidates = [f"{prefix}.{i}" for i in range(2, 55) if i != my_last]
    for ip in candidates:
        try:
            url = f"http://{ip}:{port}/api/system/client-info"
            req = urllib.request.Request(url, headers={'User-Agent': 'RelayScanner'})
            with urllib.request.urlopen(req, timeout=0.35) as resp:
                if resp.status == 200:
                    print(f"🎉 Đã tìm thấy máy chủ bạn bè đang chạy tại: {ip}:{port}")
                    return f"http://{ip}:{port}"
        except Exception:
            pass
    return None

def run_relay(target_url, port=8000):
    print("\n" + "═" * 70)
    print("🔁 TRẠM CHUYỂN TIẾP THÔNG MINH (AI YOUTUBE STUDIO RELAY)")
    print("═" * 70)
    print(f"📍 Máy này đang lắng nghe cổng: {port}")
    print(f"🎯 Tự động chuyển tiếp toàn bộ thiết bị sang: {target_url}")
    print("💡 Tất cả điện thoại đang lưu IP máy này vẫn vào được bình thường!")
    print("═" * 70 + "\n")

    class RelayHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            redirect_to = f"{target_url}{self.path}"
            self.send_response(307) # Temporary Redirect
            self.send_header('Location', redirect_to)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(f"Đang chuyển tiếp sang máy chủ: {redirect_to}".encode('utf-8'))

        def do_POST(self):
            redirect_to = f"{target_url}{self.path}"
            self.send_response(307)
            self.send_header('Location', redirect_to)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

        def log_message(self, format, *args):
            print(f"   [Relay] Chuyển tiếp client {self.client_address[0]} -> {target_url}")

    with socketserver.TCPServer(("", port), RelayHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 Đã dừng trạm chuyển tiếp.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI YouTube Relay Forwarder")
    parser.add_argument("--to", dest="target", default="", help="Địa chỉ IP đích (VD: 192.168.2.5:8000)")
    args = parser.parse_args()

    target = args.target.strip()
    if not target:
        found = find_active_server_in_lan()
        if found:
            target = found
        else:
            print("⚠️ Không tự động dò thấy máy bạn đang bật cổng 8000.")
            target = input("👉 Hãy nhập IP máy bạn (VD: 192.168.2.5 hoặc http://192.168.2.5:8000): ").strip()

    if not target.startswith("http://") and not target.startswith("https://"):
        if ":" not in target:
            target = f"http://{target}:8000"
        else:
            target = f"http://{target}"

    run_relay(target)
