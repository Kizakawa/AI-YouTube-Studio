"""
auth_channel.py — Script xác thực Google OAuth2 riêng cho từng kênh YouTube.
Dễ dàng cấp quyền hoặc làm mới token đã hết hạn cho bất kỳ kênh nào.

Cách sử dụng:
    python auth_channel.py channel_maya
    python auth_channel.py ucsrjiil67qxidtxt70z-iwa
    python auth_channel.py              (Hiển thị menu chọn kênh)
"""
import os
import sys
import json
import base64
from pathlib import Path
import yaml
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Đảm bảo in tiếng Việt và emoji mượt mà trên Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Cho phép OAuth trên localhost (http)
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

def load_channels_config():
    cfg_path = Path("config/channels.yaml")
    if not cfg_path.exists():
        print(f"❌ Không tìm thấy file cấu hình: {cfg_path}")
        return None
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def authenticate_channel(channel_id: str):
    base_dir = Path(__file__).resolve().parent
    cfg = load_channels_config()
    if not cfg:
        return False

    # Tìm thông tin kênh và tài khoản Google tương ứng
    target_channel = None
    target_account = None

    for acc in cfg.get("google_accounts", []):
        for bch in acc.get("brand_channels", []):
            if bch.get("id") == channel_id:
                target_channel = bch
                target_account = acc
                break
        if target_channel:
            break

    if not target_channel:
        # Thử tìm trong danh sách channels phẳng nếu có
        for ch in cfg.get("channels", []):
            if ch.get("id") == channel_id:
                target_channel = ch
                acc_id = ch.get("google_account", "account_1")
                for acc in cfg.get("google_accounts", []):
                    if acc.get("id") == acc_id:
                        target_account = acc
                        break
                break

    if not target_channel:
        print(f"❌ Không tìm thấy kênh có ID '{channel_id}' trong config/channels.yaml!")
        return False

    acc_id = target_account.get("id", "account_1") if target_account else "account_1"
    ch_name = target_channel.get("name", channel_id)

    # Tìm file client_secret
    client_secret_file = base_dir / "credentials" / "accounts" / acc_id / "client_secret.json"
    if not client_secret_file.exists():
        # Fallback về credentials/client_secret.json
        client_secret_file = base_dir / "credentials" / "client_secret.json"
    if not client_secret_file.exists():
        print(f"❌ Không tìm thấy client_secret.json cho tài khoản {acc_id}!")
        return False

    print("\n" + "=" * 68)
    print(f"🔐 BẮT ĐẦU XÁC THỰC YOUTUBE OAUTH CHO KÊNH:")
    print(f"   📺 Tên kênh: {ch_name} (ID: {channel_id})")
    print(f"   👤 Tài khoản Google: {acc_id} ({target_account.get('email_label', 'N/A') if target_account else 'N/A'})")
    print("=" * 68)
    print("👉 Trình duyệt web sẽ tự động mở ra.")
    print("👉 HÃY CHỌN ĐÚNG KÊNH YOUTUBE BẠN MUỐN KẾT NỐI KHI GOOGLE HỎI!")
    print("💡 Nếu có cảnh báo 'Chưa xác minh': Bấm Nâng cao (Advanced) -> Đi tới ứng dụng (Unsafe).")
    print("=" * 68 + "\n")

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secret_file),
            scopes=YOUTUBE_SCOPES
        )
        print("🌐 Đang khởi động máy chủ xác thực OAuth cục bộ...", flush=True)
        creds = flow.run_local_server(
            port=0,
            prompt="consent",
            access_type="offline",
            authorization_prompt_message="👉 Vui lòng mở đường link sau trên trình duyệt nếu không tự mở:\n{url}\n",
            success_message="Xac thuc YouTube thanh cong! Ban co the dong tab nay va quay lai terminal."
        )

        # Lấy thông tin kênh thực tế từ Google YouTube Data API
        real_yt_title = None
        real_yt_id = None
        subs_count = "0"
        try:
            yt = build("youtube", "v3", credentials=creds)
            c_res = yt.channels().list(mine=True, part="snippet,statistics").execute()
            items = c_res.get("items", [])
            if items:
                real_yt_id = items[0].get("id")
                snippet = items[0].get("snippet", {})
                real_yt_title = snippet.get("title")
                subs_count = items[0].get("statistics", {}).get("subscriberCount", "0")
        except Exception as e_yt:
            print(f"⚠️ Không thể đọc snippet kênh: {e_yt}")

        # Lưu token vào credentials/channels/{channel_id}/youtube_token.json (Mã hoá AES-256)
        ch_dir = base_dir / "credentials" / "channels" / channel_id
        ch_dir.mkdir(parents=True, exist_ok=True)
        token_file = ch_dir / "youtube_token.json"

        from app.utils.security import save_encrypted_json
        token_dict = json.loads(creds.to_json())
        token_dict["channel_id"] = real_yt_id or channel_id
        token_dict["channel_title"] = real_yt_title or ch_name

        save_encrypted_json(token_file, token_dict)

        # Đồng bộ vào thư mục tài khoản mẹ để tương thích hoàn toàn
        for acc_folder in [acc_id, "account_2" if "2" in acc_id else acc_id, "account_3" if "3" in acc_id else acc_id]:
            acc_dir = base_dir / "credentials" / "accounts" / acc_folder
            if acc_dir.exists():
                try:
                    save_encrypted_json(acc_dir / "youtube_token.json", token_dict)
                    b64_str = base64.b64encode(json.dumps(token_dict).encode("utf-8")).decode("utf-8")
                    with open(acc_dir / "youtube_token_base64.txt", "w", encoding="utf-8") as f:
                        f.write(b64_str)
                except Exception:
                    pass

        print("\n" + "=" * 68)
        print("🎉 XÁC THỰC THÀNH CÔNG VÀ ĐÃ LƯU TOKEN AN TOÀN (AES-256)!")
        print(f"   📺 Kênh YouTube đã cấp quyền: {real_yt_title or ch_name}")
        print(f"   🆔 YouTube Channel ID: {real_yt_id or 'N/A'}")
        print(f"   👥 Số người đăng ký: {subs_count}")
        print(f"   💾 Đường dẫn token: {token_file}")
        print("=" * 68 + "\n")
        return True
    except Exception as e:
        print(f"\n❌ Lỗi xác thực YouTube cho kênh {channel_id}: {e}\n")
        return False

def main():
    cfg = load_channels_config()
    all_channels = []
    if cfg:
        for acc in cfg.get("google_accounts", []):
            for bch in acc.get("brand_channels", []):
                all_channels.append({
                    "id": bch.get("id"),
                    "name": bch.get("name"),
                    "account": acc.get("id"),
                    "email": acc.get("email_label")
                })
        for ch in cfg.get("channels", []):
            if not any(c["id"] == ch.get("id") for c in all_channels):
                all_channels.append({
                    "id": ch.get("id"),
                    "name": ch.get("name"),
                    "account": ch.get("google_account", "account_1"),
                    "email": ""
                })

    if len(sys.argv) > 1:
        target_id = sys.argv[1].strip()
        authenticate_channel(target_id)
        return

    # Nếu không truyền tham số, hiển thị danh sách để người dùng chọn
    print("\n" + "=" * 68)
    print("📋 DANH SÁCH KÊNH YOUTUBE HIỆN CÓ:")
    print("=" * 68)
    for idx, c in enumerate(all_channels, 1):
        token_path = Path(f"credentials/channels/{c['id']}/youtube_token.json")
        status = "🟢 Có token" if token_path.exists() else "🟡 Chưa cấp quyền"
        print(f"  [{idx}] {c['name']} (ID: {c['id']}) ── {status} ── [{c['email'] or c['account']}]")
    print("=" * 68)
    print("Nhập số thứ tự kênh muốn xác thực (hoặc nhập 'channel_maya'): ")
    try:
        choice = input("👉 Lựa chọn: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(all_channels):
            selected = all_channels[int(choice) - 1]["id"]
            authenticate_channel(selected)
        elif choice:
            authenticate_channel(choice)
    except (KeyboardInterrupt, EOFError):
        print("\nĐã hủy thao tác.")

if __name__ == "__main__":
    main()
