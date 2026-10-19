"""
auth_all.py — Trình Hướng Dẫn Cấp Quyền Đa Dự Án Cho Toàn Bộ Kênh YouTube (Master Matrix Wizard).
Cho phép liên kết tất cả 5 kênh YouTube với toàn bộ 3 Dự án Google Cloud để xoay vòng Quota tối đa (30.000 Quota/ngày/kênh).
"""
import os
import sys
from pathlib import Path

# Đảm bảo UTF-8 trên Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auth_channel import authenticate_channel, load_channels_config
from app.utils.security import load_encrypted_json
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request


def check_live_channel_status(all_channels):
    """Kiểm tra trực tiếp xem token chính của từng kênh còn sống (🟢), đúng chủ sở hữu kênh hay đã hết hạn/chưa có (🔴)."""
    print("\n" + "═" * 78)
    print("🩺 TRẠNG THÁI XÁC THỰC TRỰC TIẾP (LIVE OAUTH) CỦA 7 KÊNH YOUTUBE:")
    print("═" * 78)
    print(f"{'STT':<4} | {'Tên Kênh YouTube':<30} | {'Project':<10} | {'Trạng Thái Token'}")
    print("─" * 78)

    expired_or_missing = []
    for idx, ch in enumerate(all_channels, 1):
        cid = ch.get("id")
        name = ch.get("name", cid)[:28]
        acc = ch.get("account", "account_1")
        exp_yt = (ch.get("youtube_channel_id") or "").strip()
        prim_token = Path("credentials/channels") / cid / "youtube_token.json"

        status_str = "🔴 Chưa có token"
        is_valid = False
        if prim_token.exists():
            try:
                data = load_encrypted_json(prim_token)
                tok_yt = (data.get("channel_id") or "").strip() if data else ""
                if exp_yt and tok_yt and exp_yt.lower() != tok_yt.lower():
                    status_str = f"🔴 Lệch kênh ({tok_yt[:12]}...)"
                elif data and data.get("refresh_token"):
                    creds = Credentials.from_authorized_user_info(data)
                    creds.refresh(Request())
                    is_valid = True
                    status_str = "🟢 Đang hoạt động tốt (VALID)"
                else:
                    status_str = "🔴 Thiếu refresh_token"
            except Exception as e:
                err_short = "Hết hạn (invalid_grant)" if "invalid_grant" in str(e) else str(e)[:28]
                status_str = f"🔴 Cần cấp lại ({err_short})"

        print(f"[{idx}]  | {name:<30} | {acc:<10} | {status_str}")
        if not is_valid:
            expired_or_missing.append(ch)

    print("═" * 78 + "\n")
    return expired_or_missing


def print_matrix(all_channels, accounts, cid_to_acc):
    print("\n" + "═" * 72)
    print("📊 MA TRẬN LIÊN KẾT GOOGLE CLOUD PROJECT CỦA TẤT CẢ CÁC KÊNH:")
    print("═" * 72)
    header = f"{'STT':<4} | {'Tên Kênh YouTube':<30} | {'account_1':^9} | {'acount_2':^9} | {'accout3':^9}"
    print(header)
    print("─" * 72)

    missing_tasks = []
    for idx, ch in enumerate(all_channels, 1):
        cid = ch.get("id")
        name = ch.get("name", cid)[:28]
        ch_dir = Path("credentials/channels") / cid

        st = {a: "❌" for a in accounts}
        if ch_dir.exists():
            for tf in ch_dir.glob("*.json"):
                try:
                    data = load_encrypted_json(tf)
                    if data and "client_id" in data:
                        acc = cid_to_acc.get(data["client_id"])
                        if acc:
                            st[acc] = "🟢"
                except Exception:
                    pass

        row = f"[{idx}]  | {name:<30} | {st['account_1']:^9} | {st['acount_2']:^9} | {st['accout3']:^9}"
        print(row)

        for a in accounts:
            if st[a] == "❌":
                missing_tasks.append((ch, a))

    print("─" * 72)
    print("  🟢 = Đã liên kết OAuth (Có thể tự động xoay Quota)")
    print("  ❌ = Chưa liên kết (Cần cấp quyền để mở rộng Quota)")
    print("═" * 72 + "\n")
    return missing_tasks

def main():
    cfg = load_channels_config()
    if not cfg:
        print("❌ Không tìm thấy config/channels.yaml")
        return

    accounts = ["account_1", "acount_2", "accout3"]
    acc_dir = Path("credentials/accounts")
    cid_to_acc = {}

    import json
    for acc in accounts:
        cs = acc_dir / acc / "client_secret.json"
        if cs.exists():
            try:
                with open(cs, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    info = d.get("installed") or d.get("web") or {}
                    cid = info.get("client_id")
                    if cid:
                        cid_to_acc[cid] = acc
            except Exception:
                pass

    all_channels = []
    for acc in cfg.get("google_accounts", []):
        for bch in acc.get("brand_channels", []):
            all_channels.append({
                "id": bch.get("id"),
                "name": bch.get("name"),
                "account": acc.get("id"),
                "youtube_channel_id": bch.get("youtube_channel_id"),
            })
    for ch in cfg.get("channels", []):
        if not any(c["id"] == ch.get("id") for c in all_channels):
            all_channels.append({
                "id": ch.get("id"),
                "name": ch.get("name"),
                "account": ch.get("google_account", "account_1"),
                "youtube_channel_id": ch.get("youtube_channel_id"),
            })

    # 1. Kiểm tra trực tiếp trạng thái sống/chết của token chính trên 7 kênh
    broken_channels = check_live_channel_status(all_channels)

    if not broken_channels:
        print("🎉 TẤT CẢ 7/7 KÊNH ĐỀU ĐANG HOẠT ĐỘNG TỐT (🟢 VALID TOKEN)!")
    else:
        print(f"⚠️ Phát hiện {len(broken_channels)}/{len(all_channels)} kênh cần cấp quyền lại:")
        for ch in broken_channels:
            print(f"   👉 {ch['name']} (ID: {ch['id']} ── Project: {ch['account']})")
        print()

    print("👉 Bạn muốn làm gì:")
    print(f"   [1] ⚡ Cấp quyền ngay cho {len(broken_channels)} kênh đang thiếu/hết hạn (KHUYÊN DÙNG)")
    print("   [2] 🎯 Chọn 1 kênh cụ thể theo số thứ tự để cấp quyền lại")
    print("   [3] 📊 Mở Ma trận 7x3 Dự án (Xoay vòng Quota nâng cao)")
    print("   [4] ❌ Thoát")
    print("-" * 78)

    choice = input("👉 Nhập lựa chọn [1-4, mặc định 1]: ").strip() or "1"

    if choice == "1":
        if not broken_channels:
            print("✅ Tất cả các kênh đã xanh, không cần cấp quyền thêm!")
            return
        for i, ch in enumerate(broken_channels, 1):
            print(f"\n[{i}/{len(broken_channels)}] 📺 Đang mở trình duyệt cho kênh: '{ch['name']}' (ID: {ch['id']})...")
            authenticate_channel(ch.get("id"), ch.get("account"))
        print("\n🔄 Kiểm tra lại trạng thái sau khi cấp quyền:")
        check_live_channel_status(all_channels)

    elif choice == "2":
        c_idx = input(f"👉 Chọn số thứ tự kênh [1-{len(all_channels)}]: ").strip()
        if c_idx.isdigit() and 1 <= int(c_idx) <= len(all_channels):
            sel_ch = all_channels[int(c_idx) - 1]
            authenticate_channel(sel_ch["id"], sel_ch["account"])
            check_live_channel_status(all_channels)

    elif choice == "3":
        missing = print_matrix(all_channels, accounts, cid_to_acc)
        if not missing:
            print("🎉 TẤT CẢ CÁC KÊNH ĐỀU ĐÃ LIÊN KẾT ĐỦ 3 DỰ ÁN GOOGLE CLOUD!")
            return
        sub = input(f"👉 Bấm [1] để cấp quyền {len(missing)} ô còn thiếu, hoặc [Enter] để thoát: ").strip()
        if sub == "1":
            for i, (ch, acc) in enumerate(missing, 1):
                ch_name = ch.get("name", ch.get("id"))
                print(f"\n[{i}/{len(missing)}] 📺 KÊNH: '{ch_name}' ➔ 👤 DỰ ÁN: '{acc}'")
                user_in = input("👉 Bấm [Enter] để mở trình duyệt ('s' bỏ qua, 'q' thoát): ").strip().lower()
                if user_in == "q":
                    break
                elif user_in == "s":
                    continue
                authenticate_channel(ch.get("id"), acc)
            print_matrix(all_channels, accounts, cid_to_acc)
    else:
        print("Đã thoát.")

if __name__ == "__main__":
    main()
