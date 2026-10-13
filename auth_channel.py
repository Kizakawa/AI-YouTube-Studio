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
from typing import Optional, List, Dict, Any, Tuple
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

def authenticate_channel(channel_id: str, account_id: Optional[str] = None):
    base_dir = Path(__file__).resolve().parent
    cfg = load_channels_config()
    if not cfg:
        return False

    # Chuẩn hoá alias cho các kênh
    alias_map = {
        # Kênh Psychology of the world (account_1)
        "maya": "channel_maya",
        "psychology": "channel_maya",
        "uc1_lvpilxq-j4sfrqozfrkw": "channel_maya",
        "@vv1n-oe9gs": "channel_maya",
        # Kênh The mysteries of the world (account_1) — ID thực tế là chuỗi dài này
        "the_mysteries": "ucsrjiil67qxidtxt70z-iwa",
        "mysteries": "ucsrjiil67qxidtxt70z-iwa",
        "mystery": "ucsrjiil67qxidtxt70z-iwa",
        "ch_the_mysteries_of_the_world": "ucsrjiil67qxidtxt70z-iwa",
        "ucsrjiil67qxidtxt70z-iwa": "ucsrjiil67qxidtxt70z-iwa",  # keep as-is
        "@themysteriesoftheworld-f3w": "ucsrjiil67qxidtxt70z-iwa",
        # Kênh Paws & Hearts Rescue (acount_2)
        "paws": "ch_paws_hearts_rescue",
        "rescue": "ch_paws_hearts_rescue",
        "@pawsandheartsrescue": "ch_paws_hearts_rescue",
        # Kênh Wild Odyssey (acount_2)
        "wild": "ch_wild_odyssey",
        "odyssey": "ch_wild_odyssey",
        "@wildodysseyhq": "ch_wild_odyssey",
        # Kênh Odisseia Selvagem (acount_2)
        "odisseia": "ch_odisseia_selvagem",
        "odisseia_selvagem": "ch_odisseia_selvagem",
        "ch_odisseia": "ch_odisseia_selvagem",
        "ucpbqs4h5qugpu3hcqjg-p9w": "ch_odisseia_selvagem",
        "@odisseiaselvagemoficial": "ch_odisseia_selvagem",
        # Kênh Mistérios do Mundo (acount_2)
        "misterios": "ch_misterios_do_mundo",
        "misterios_do_mundo": "ch_misterios_do_mundo",
        "ch_misterios": "ch_misterios_do_mundo",
        "ucvfonq9jcpvvze8e1cgng": "ch_misterios_do_mundo",
        "ucvfonq9jcpvvvze8e1cgnng": "ch_misterios_do_mundo",  # typo variant
        "@misteriosdomundo-tv": "ch_misterios_do_mundo",
        # Kênh cũ
        "whatif": "@alternatehistorywhatif-e2x",
        "history": "@alternatehistorywhatif-e2x",
    }

    channel_id = alias_map.get(channel_id.lower(), channel_id)

    # Tìm thông tin kênh và tài khoản Google tương ứng
    target_channel = None
    target_account = None
    is_primary_channel = False

    for acc in cfg.get("google_accounts", []):
        bchs = acc.get("brand_channels", [])
        for idx, bch in enumerate(bchs):
            if bch.get("id") == channel_id:
                target_channel = bch
                target_account = acc
                if idx == 0:
                    is_primary_channel = True
                break
        if target_channel:
            break

    if not target_channel:
        # Thử tìm trong danh sách channels phẳng nếu có
        for ch in cfg.get("channels", []):
            if ch.get("id") == channel_id:
                target_channel = ch
                acc_id_cfg = ch.get("google_account", "account_1")
                for acc in cfg.get("google_accounts", []):
                    if acc.get("id") == acc_id_cfg:
                        target_account = acc
                        break
                break

    if not target_channel:
        print(f"❌ Không tìm thấy kênh có ID '{channel_id}' trong config/channels.yaml!")
        return False

    # Ưu tiên account_id được truyền vào (cho phép xoay Project Quota)
    acc_id = account_id or (target_account.get("id", "account_1") if target_account else "account_1")
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
    print(f"   👤 Tài khoản Google / Project: {acc_id} (Dự án: {client_secret_file.parent.name})")
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

        # Kiểm tra xem người dùng có chọn nhầm kênh trên trình duyệt không
        expected_yt_id = target_channel.get("youtube_channel_id")
        if expected_yt_id and real_yt_id and expected_yt_id.lower() != real_yt_id.lower():
            print("\n" + "!" * 68)
            print("⚠️ CẢNH BÁO: BẠN CÓ THỂ ĐÃ CHỌN NHẦM KÊNH TRÊN TRÌNH DUYỆT!")
            print(f"   👉 Kênh bạn vừa chọn cấp quyền: '{real_yt_title}' ({real_yt_id})")
            print(f"   👉 Kênh dự kiến trong cấu hình: '{ch_name}' ({expected_yt_id})")
            print("   Vui lòng kiểm tra lại nếu video bị đăng nhầm kênh!")
            print("!" * 68 + "\n")

        # Lưu token vào credentials/channels/{channel_id}/ (Mã hoá AES-256)
        ch_dir = base_dir / "credentials" / "channels" / channel_id
        ch_dir.mkdir(parents=True, exist_ok=True)
        primary_token_file = ch_dir / "youtube_token.json"
        acc_token_file = ch_dir / f"youtube_token_{acc_id}.json"

        from app.utils.security import save_encrypted_json
        token_dict = json.loads(creds.to_json())
        token_dict["channel_id"] = real_yt_id or channel_id
        token_dict["channel_title"] = real_yt_title or ch_name

        # 1. Lưu token theo mã dự án / account để xoay vòng Quota (Project Pool)
        save_encrypted_json(acc_token_file, token_dict)
        # 2. Cập nhật token chính nếu chưa có, hoặc nếu đang xác thực tài khoản chính của kênh
        is_main_account = target_account and (acc_id == target_account.get("id"))
        if not primary_token_file.exists() or is_main_account:
            save_encrypted_json(primary_token_file, token_dict)
            logger.info(f"Đã cập nhật token chính {primary_token_file.name}")
        else:
            logger.info(f"Kênh đã có token chính, lưu thêm token dự án pool: {acc_token_file.name}")

        # Chỉ đồng bộ vào thư mục tài khoản mẹ nếu đây là kênh chính (kênh đầu tiên)
        # Tránh ghi đè token của kênh khác trong cùng một Gmail!
        if is_primary_channel:
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
        print(f"   💾 Token Pool dự án: {acc_token_file}")
        print(f"   💾 Token kênh chính: {primary_token_file}")
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
        target_acc = sys.argv[2].strip() if len(sys.argv) > 2 else None

        if target_id in ("--all-channels", "--matrix", "all"):
            from auth_all import main as auth_all_main
            auth_all_main()
            return

        if target_acc in ("--all", "-a", "all"):
            print("\n" + "═" * 68)
            print(f"🔥 BẮT ĐẦU CẤP QUYỀN CÁC DỰ ÁN CHO KÊNH: {target_id}")
            print("   Hệ thống sẽ lần lượt mở trình duyệt cho từng Dự án Google Cloud.")
            print("   Bạn chỉ cần chọn đúng kênh và bấm 'Cho phép' (Allow) cho từng dự án.")
            print("═" * 68 + "\n")
            accounts = ["account_1", "project_pool_2"]
            success_count = 0
            for idx, acc in enumerate(accounts, 1):
                print(f"\n[Bước {idx}/{len(accounts)}] Đang cấp quyền Dự án: {acc}...")
                ok = authenticate_channel(target_id, acc)
                if ok:
                    success_count += 1
            print("\n" + "=" * 68)
            print(f"🎉 HOÀN TẤT LIÊN KẾT {success_count}/{len(accounts)} DỰ ÁN CHO KÊNH: {target_id}!")
            print("   Kênh này hiện đã có Quota Pool đa dự án. Khi bất kỳ dự án nào chạm trần,")
            print("   hệ thống sẽ tự động xoay sang dự án tiếp theo để tiếp tục xuất bản!")
            print("=" * 68 + "\n")
            return
        authenticate_channel(target_id, target_acc)
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
            print("\n👉 Chọn tài khoản / Dự án Google Cloud muốn cấp quyền cho kênh:")
            print("  [0] Mặc định theo channels.yaml")
            print("  [1] account_1 (Dự án: bionic-client-509515-h2)")
            print("  [2] project_pool_2 (Dự án mới: effective-cacao-511414-q3 ── Thêm 10.000 Quota/ngày)")
            print("  [3] acount_2 (Tài khoản Google thứ 2)")
            print("  [4] accout3 (Tài khoản Google thứ 3)")
            print("  [all] 🔥 CẤP QUYỀN CẢ DỰ ÁN CHÍNH & DỰ ÁN POOL để tự động xoay Quota")
            acc_c = input("👉 Lựa chọn tài khoản [0-4, all, mặc định 0]: ").strip()
            if acc_c in ("all", "--all", "5"):
                for acc in ["account_1", "project_pool_2"]:
                    authenticate_channel(selected, acc)
            else:
                acc_map = {"1": "account_1", "2": "project_pool_2", "3": "acount_2", "4": "accout3"}
                authenticate_channel(selected, acc_map.get(acc_c))
        elif choice:
            authenticate_channel(choice)
    except (KeyboardInterrupt, EOFError):
        print("\nĐã hủy thao tác.")

if __name__ == "__main__":
    main()
