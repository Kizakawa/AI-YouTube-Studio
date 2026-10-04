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
                "account": acc.get("id")
            })
    for ch in cfg.get("channels", []):
        if not any(c["id"] == ch.get("id") for c in all_channels):
            all_channels.append({
                "id": ch.get("id"),
                "name": ch.get("name"),
                "account": ch.get("google_account", "account_1")
            })

    missing = print_matrix(all_channels, accounts, cid_to_acc)

    if not missing:
        print("🎉 TẤT CẢ CÁC KÊNH ĐỀU ĐÃ LIÊN KẾT ĐỦ 3 DỰ ÁN GOOGLE CLOUD! (30.000 Quota/kênh/ngày)")
        return

    print(f"💡 Hệ thống phát hiện còn {len(missing)} liên kết chưa cấp quyền (❌).")
    print("👉 Bạn muốn làm gì:")
    print("   [1] 🚀 Cấp quyền liên hoàn tất cả các ô còn thiếu (tự động mở lần lượt)")
    print("   [2] 🎯 Chọn 1 kênh để cấp quyền cả 3 dự án (--all)")
    print("   [3] ❌ Thoát")
    print("-" * 72)

    choice = input("👉 Nhập lựa chọn [1, 2, 3]: ").strip()

    if choice == "1":
        print("\n" + "🚀" * 35)
        print("BẮT ĐẦU CẤP QUYỀN LIÊN HOÀN CÁC LIÊN KẾT CÒN THIẾU:")
        print("Với mỗi liên kết, trình duyệt sẽ mở ra trang Google OAuth.")
        print("Bạn chỉ cần chọn đúng kênh và bấm 'Cho phép' (Allow).")
        print("Nhấn Enter để tiếp tục, gõ 's' để bỏ qua, 'q' để dừng.")
        print("🚀" * 35 + "\n")

        for i, (ch, acc) in enumerate(missing, 1):
            ch_name = ch.get("name", ch.get("id"))
            print(f"\n[{i}/{len(missing)}] 📺 KÊNH: '{ch_name}' ➔ 👤 DỰ ÁN: '{acc}'")
            user_in = input("👉 Bấm [Enter] để mở trình duyệt ('s' bỏ qua, 'q' thoát): ").strip().lower()
            if user_in == "q":
                print("Đã dừng tiến trình cấp quyền.")
                break
            elif user_in == "s":
                print("Đã bỏ qua.")
                continue

            authenticate_channel(ch.get("id"), acc)

        print("\nHoàn tất! Bảng trạng thái sau khi cập nhật:")
        print_matrix(all_channels, accounts, cid_to_acc)

    elif choice == "2":
        print("\n📋 DANH SÁCH KÊNH:")
        for idx, ch in enumerate(all_channels, 1):
            print(f"  [{idx}] {ch['name']} (ID: {ch['id']})")
        c_idx = input("👉 Chọn số thứ tự kênh muốn cấp cả 3 dự án: ").strip()
        if c_idx.isdigit() and 1 <= int(c_idx) <= len(all_channels):
            sel_ch = all_channels[int(c_idx) - 1]
            print(f"\nBắt đầu cấp quyền cả 3 dự án cho kênh: {sel_ch['name']}...")
            for acc in accounts:
                authenticate_channel(sel_ch["id"], acc)
            print("\nBảng trạng thái cập nhật:")
            print_matrix(all_channels, accounts, cid_to_acc)
    else:
        print("Đã hủy.")

if __name__ == "__main__":
    main()
