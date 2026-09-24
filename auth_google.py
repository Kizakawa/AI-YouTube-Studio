"""
Script xác thực Google OAuth2 riêng biệt cho YouTube Data API và Google Drive 2TB
Tách riêng từng quyền để tránh lỗi xung đột Scopes của Google (Lỗi 400: invalid_request)
"""
import os
import sys
import base64
import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

# Đảm bảo in tiếng Việt và emoji không bị lỗi trên Windows console (cp1258)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Cho phép OAuth trên localhost (http)
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# Tách riêng Scope để Google không báo lỗi "scopes that cannot be requested together"
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube",
]

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
]

def authenticate(service_name: str, scopes: list, token_filename: str, account_id: str = "account_1"):
    base_dir = Path(__file__).resolve().parent
    account_dir = base_dir / "credentials" / "accounts" / account_id
    account_dir.mkdir(parents=True, exist_ok=True)

    client_secret_file = account_dir / "client_secret.json"
    if not client_secret_file.exists():
        print(f"❌ Không tìm thấy file: {client_secret_file}")
        return False

    print("\n" + "=" * 65)
    print(f"🔐 ĐANG KẾT NỐI: [{service_name.upper()}] CHO TÀI KHOẢN [{account_id}]")
    print("=" * 65)
    print(f"👉 Trình duyệt web sẽ mở ra để cấp quyền cho {service_name}.")
    print("👉 Hãy chọn Gmail của bạn và bấm 'Cho phép' (Allow).")
    print("💡 Nếu có cảnh báo 'Chưa xác minh': Bấm Nâng cao -> Đi tới AI Studio.")
    print("=" * 65)

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secret_file),
            scopes=scopes
        )
        print("🌐 Đang khởi động máy chủ xác thực cục bộ...", flush=True)
        creds = flow.run_local_server(
            port=0,
            prompt="consent",
            access_type="offline",
            authorization_prompt_message="👉 Vui lòng mở đường link sau trên trình duyệt:\n{url}\n",
            success_message="Xac thuc thanh cong! Ban co the dong tab trinh duyet nay."
        )
        token_data = creds.to_json()

        token_file = account_dir / token_filename
        with open(token_file, "w", encoding="utf-8") as f:
            f.write(token_data)

        # Lưu thêm file Base64 cho Render
        b64_str = base64.b64encode(token_data.encode("utf-8")).decode("utf-8")
        b64_file = account_dir / f"{Path(token_filename).stem}_base64.txt"
        with open(b64_file, "w", encoding="utf-8") as f:
            f.write(b64_str)

        print(f"✅ ĐÃ KẾT NỐI {service_name.upper()} THÀNH CÔNG!")
        print(f"   -> Đã lưu token: {token_file}")
        print(f"   -> Đã tạo file Base64 cho Render: {b64_file}")
        return True
    except Exception as e:
        print(f"❌ Lỗi khi xác thực {service_name}: {e}")
        return False

def main():
    account_id = "account_1"
    
    # Lựa chọn dịch vụ muốn xác thực
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    if mode in ["youtube", "yt"]:
        authenticate("YouTube Studio", YOUTUBE_SCOPES, "youtube_token.json", account_id)
    elif mode in ["drive"]:
        authenticate("Google Drive 2TB", DRIVE_SCOPES, "drive_token.json", account_id)
    else:
        # Chạy YouTube trước
        print("📌 [BƯỚC 1/2] Kết nối Kênh YouTube để đăng và lên lịch video...")
        yt_success = authenticate("YouTube Studio", YOUTUBE_SCOPES, "youtube_token.json", account_id)

        if yt_success:
            print("\n📌 [BƯỚC 2/2] Kết nối Google Drive 2TB để lưu trữ video thành phẩm...")
            authenticate("Google Drive 2TB", DRIVE_SCOPES, "drive_token.json", account_id)

    print("\n" + "=" * 65)
    print("🎉 HOÀN TẤT TOÀN BỘ TIẾN TRÌNH XÁC THỰC GOOGLE!")
    print("=" * 65)

if __name__ == "__main__":
    main()
