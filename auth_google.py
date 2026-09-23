"""
Script xác thực Google OAuth2 1-click cho YouTube Data API v3 và Google Drive 2TB
Tự động mở trình duyệt để người dùng đăng nhập và sinh ra token vĩnh viễn.
"""
import os
import sys
import base64
import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# Scopes yêu cầu cho YouTube Studio và Google Drive
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/drive.file",
]

def main(account_id="account_1"):
    base_dir = Path(__file__).resolve().parent
    account_dir = base_dir / "credentials" / "accounts" / account_id
    account_dir.mkdir(parents=True, exist_ok=True)
    
    client_secret_file = account_dir / "client_secret.json"
    if not client_secret_file.exists():
        print(f"❌ Không tìm thấy file: {client_secret_file}")
        print("Hãy chắc chắn bạn đã tải file client_secret.json vào thư mục credentials/accounts/account_1/")
        return

    print("=" * 65)
    print(f"🔐 BẮT ĐẦU XÁC THỰC GOOGLE OAUTH2 CHO [{account_id}]")
    print("=" * 65)
    print("👉 Trình duyệt web sẽ tự động mở ra ngay bây giờ.")
    print("👉 Hãy chọn tài khoản Google của bạn và bấm 'Cho phép' (Allow).")
    print("💡 Lưu ý: Nếu thấy cảnh báo 'Google chưa xác minh ứng dụng này', hãy bấm:")
    print("   'Nâng cao' (Advanced) -> 'Đi tới AI Studio (không an toàn)' -> Tiếp tục.")
    print("=" * 65)

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_file),
        scopes=SCOPES
    )
    
    # Chạy local server trên cổng ngẫu nhiên để hứng callback
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    token_data = creds.to_json()

    # Lưu token cho YouTube và Drive
    youtube_token_file = account_dir / "youtube_token.json"
    drive_token_file = account_dir / "drive_token.json"
    
    with open(youtube_token_file, "w", encoding="utf-8") as f:
        f.write(token_data)
        
    with open(drive_token_file, "w", encoding="utf-8") as f:
        f.write(token_data)

    print("\n" + "=" * 65)
    print("🎉 XÁC THỰC THÀNH CÔNG RỰC RỠ!")
    print("=" * 65)
    print(f"✅ Đã lưu YouTube token: {youtube_token_file}")
    print(f"✅ Đã lưu Google Drive token: {drive_token_file}")
    
    # Mã hóa Base64 để người dùng copy lên Render nếu cần
    token_b64 = base64.b64encode(token_data.encode("utf-8")).decode("utf-8")
    
    # Lưu file base64 token để dễ copy lên Render
    b64_file = account_dir / "token_base64.txt"
    with open(b64_file, "w", encoding="utf-8") as f:
        f.write(token_b64)
        
    print(f"✅ Đã tạo file token Base64 cho Render: {b64_file}")
    print("=" * 65)

if __name__ == "__main__":
    acc = sys.argv[1] if len(sys.argv) > 1 else "account_1"
    main(acc)
