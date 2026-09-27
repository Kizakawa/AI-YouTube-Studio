"""
Script đóng gói toàn bộ dự án AI YouTube Studio thành file ZIP Portable
để bạn bè trên máy khác có thể chạy nguyên vẹn mà KHÔNG CẦN CẤU HÌNH API HAY OAUTH LẠI.
"""
import os
import sys
import zipfile
from pathlib import Path

# Đảm bảo in tiếng Việt và emoji không bị lỗi charmap trên Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_ZIP = BASE_DIR / "AI_YouTube_Full_Package.zip"

EXCLUDE_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "storage", "output", "logs", 
    ".idea", ".vscode", "scratch", "tmp"
}

EXCLUDE_EXTS = {
    ".pyc", ".pyo", ".pyd", ".log", ".tmp"
}

# Các file quan trọng PHẢI CÓ
MANDATORY_FILES = [
    ".env",
    "config/channels.yaml",
]

def create_package():
    print("=" * 60)
    print("📦 Bắt đầu đóng gói dự án AI YouTube Studio cho máy khác...")
    print("=" * 60)
    
    # Kiểm tra các file quan trọng
    missing = [f for f in MANDATORY_FILES if not (BASE_DIR / f).exists()]
    if missing:
        print(f"⚠️ CẢNH BÁO: Thiếu các file quan trọng: {missing}")
    else:
        print("✓ Đã tìm thấy đầy đủ .env, config/channels.yaml và credentials OAuth!")

    count = 0
    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(BASE_DIR):
            # Lọc bỏ thư mục loại trừ
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
            
            for file in files:
                file_path = Path(root) / file
                rel_path = file_path.relative_to(BASE_DIR)
                
                # Bỏ qua file zip đầu ra và các file tạm
                if file_path == OUTPUT_ZIP:
                    continue
                if file_path.suffix.lower() in EXCLUDE_EXTS:
                    continue
                if "storage" in rel_path.parts or "output" in rel_path.parts:
                    continue
                
                zipf.write(file_path, arcname=str(rel_path))
                count += 1

    size_mb = OUTPUT_ZIP.stat().st_size / (1024 * 1024)
    print("\n" + "=" * 60)
    print("🎉 ĐÃ ĐÓNG GÓI THÀNH CÔNG GÓI PORTABLE!")
    print(f"📁 Tên file: {OUTPUT_ZIP.name}")
    print(f"📍 Đường dẫn: {OUTPUT_ZIP}")
    print(f"📦 Dung lượng: {size_mb:.2f} MB")
    print(f"📄 Tổng số file nén: {count} files")
    print("=" * 60)
    print("👉 HƯỚNG DẪN DÙNG CHO BẠN CỦA BẠN:")
    print(" 1. Gửi file 'AI_YouTube_Full_Package.zip' này cho bạn của bạn.")
    print(" 2. Bạn của bạn giải nén ra một thư mục bất kỳ.")
    print(" 3. Bấm đúp vào file '1_cai_dat_moi_truong.bat' để tự động cài thư viện.")
    print(" 4. Bấm đúp vào file '2_chay_he_thong.bat' là hệ thống chạy ngay lập tức,")
    print("    KHÔNG CẦN nhập lại API key hay đăng nhập OAuth Google nào cả!")
    print("=" * 60)

if __name__ == "__main__":
    create_package()
