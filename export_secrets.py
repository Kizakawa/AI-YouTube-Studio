"""
Script đóng gói nhanh các file cấu hình bí mật (Secrets & OAuth Tokens)
để gửi cho đồng đội / bạn bè sửa chung code qua Git.
"""
import os
import sys
import zipfile
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_ZIP = BASE_DIR / "secrets_config.zip"

FILES_AND_DIRS_TO_PACK = [
    ".env",
    "config/channels.yaml",
    "credentials",
    "import_secrets.py",
    "nhap_cau_hinh.bat",
    "HUONG_DAN_CHO_BAN_BE.txt",
]

def export_secrets():
    print("=" * 60)
    print("🔐 Đang đóng gói file cấu hình bí mật (API Keys & OAuth Tokens)...")
    print("=" * 60)
    
    count = 0
    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zipf:
        for item in FILES_AND_DIRS_TO_PACK:
            item_path = BASE_DIR / item
            if not item_path.exists():
                print(f"⚠️ Bỏ qua (không tồn tại): {item}")
                continue
            
            if item_path.is_file():
                zipf.write(item_path, arcname=item)
                count += 1
                print(f" ✓ Đã thêm file: {item}")
            elif item_path.is_dir():
                for root, dirs, files in os.walk(item_path):
                    for file in files:
                        fp = Path(root) / file
                        rel = fp.relative_to(BASE_DIR)
                        zipf.write(fp, arcname=str(rel))
                        count += 1
                print(f" ✓ Đã thêm thư mục: {item}/ ({count} files)")

    # Sao chép thêm 1 bản ra thư mục cha ngoài cùng (E:\AI Youtube\secrets_config.zip)
    outer_zip = BASE_DIR.parent / "secrets_config.zip"
    try:
        import shutil
        shutil.copyfile(OUTPUT_ZIP, outer_zip)
        print(f" ✓ Đã sao chép 1 bản ra phía ngoài cùng: {outer_zip}")
    except Exception as e:
        print(f"⚠️ Lỗi khi sao chép ra thư mục ngoài: {e}")

    size_kb = OUTPUT_ZIP.stat().st_size / 1024
    print("=" * 60)
    print("🎉 ĐÃ TẠO THÀNH CÔNG: secrets_config.zip")
    print(f"📍 File trong repo: {OUTPUT_ZIP}")
    print(f"📍 File ngoài cùng: {outer_zip}")
    print(f"📦 Dung lượng: {size_kb:.1f} KB (siêu nhẹ, gửi qua Zalo/Telegram trong 1 giây)")
    print("=" * 60)
    print("👉 CÁCH DÙNG CHO BẠN BÈ SỬA CODE CHUNG:")
    print(" 1. Bạn của bạn clone code từ GitHub: git clone https://github.com/Giangvv1n/AI-youtube.git")
    print(" 2. Gửi file 'secrets_config.zip' này cho bạn.")
    print(" 3. Bạn của bạn giải nén file này thẳng vào thư mục dự án vừa clone.")
    print(" 4. Cả 2 có thể cùng git pull / git push sửa code thoải mái,")
    print("    và chạy Web Studio (run_web.bat) độc lập mà KHÔNG cần cài lại API!")
    print("=" * 60)

if __name__ == "__main__":
    export_secrets()
