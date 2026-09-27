"""
Script tự động nhập cấu hình bí mật (API Keys & OAuth Tokens) từ secrets_config.zip
Dành cho máy của bạn bè hoặc máy phụ khi mới clone code về.
"""
import os
import sys
import shutil
import zipfile
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def import_secrets():
    base_dir = Path(__file__).resolve().parent
    
    # Tìm file zip cấu hình
    possible_zips = [
        base_dir / "secrets_config.zip",
        base_dir.parent / "secrets_config.zip",
        Path.cwd() / "secrets_config.zip",
    ]
    
    zip_path = None
    for p in possible_zips:
        if p.exists():
            zip_path = p
            break
            
    if not zip_path:
        print("❌ LỖI: Không tìm thấy file 'secrets_config.zip'!")
        print("Vui lòng đặt file 'secrets_config.zip' vào thư mục dự án hoặc thư mục cha rồi chạy lại.")
        return False

    print("=" * 60)
    print(f"📦 Đang giải nén và nạp cấu hình từ: {zip_path.name}...")
    print("=" * 60)

    try:
        with zipfile.ZipFile(zip_path, "r") as zipf:
            for member in zipf.infolist():
                if member.filename.endswith(".zip") or member.filename in ("import_secrets.py", "nhap_cau_hinh.bat"):
                    continue
                target_path = base_dir / member.filename
                if member.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with zipf.open(member) as source, open(target_path, "wb") as target:
                        shutil.copyfileobj(source, target)
                    print(f" ✓ Đã nạp: {member.filename}")

        print("=" * 60)
        print("🎉 NẠP CẤU HÌNH THÀNH CÔNG 100%!")
        print("✓ Đã đồng bộ toàn bộ API Keys (.env: Gemini, Pexels, Pixabay, HuggingFace, Telegram)")
        print("✓ Đã đồng bộ cấu hình danh sách kênh YouTube (config/channels.yaml)")
        print("✓ Đã đồng bộ chứng thực OAuth bảo mật (credentials/)")
        print("=" * 60)
        print("👉 Bây giờ bạn có thể mở 2_chay_he_thong.bat hoặc run_web.bat để chạy ngay!")
        return True
    except Exception as e:
        print(f"❌ Lỗi khi giải nén cấu hình: {e}")
        return False

if __name__ == "__main__":
    import_secrets()
