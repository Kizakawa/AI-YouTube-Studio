---
name: security-and-secret-hygiene
description: >-
  Quy chuẩn bảo mật API Key, thông tin xác thực và an toàn kho mã nguồn Git cho mọi dự án: Tuyệt đối
  không hardcode secret trong code, tự động kiểm soát .gitignore và chặn đẩy nhầm các file nhạy cảm
  (.env, *.pickle, client_secret*.json, *.db, token) lên GitHub.
---

# Quy Chuẩn Bảo Mật & Vệ Sinh Kho Mã Nguồn (`security-and-secret-hygiene`)

Áp dụng tự động trong mọi thao tác viết code, cấu hình, và chạy `git add` / `git commit` trên tất cả các dự án.

---

## 1. Nguyên Tắc Bảo Mật Bắt Buộc

1. **Không Bao Giờ Hardcode Khóa Bí Mật (Zero Hardcoded Secrets)**:
   - Tuyệt đối không viết trực tiếp API Keys (`AIza...`, `sk-...`, `ghp_...`), OAuth Client Secrets, mật khẩu DB, hay đường dẫn token cá nhân vào trong các file code (`.py`, `.ts`, `.js`, `.json` được theo dõi bởi Git).
   - Mọi khóa bí mật phải được đọc qua biến môi trường (`os.getenv`, `dotenv`, `config/settings.py`) từ file `.env`.
2. **Chặn Đẩy File Nhạy Cảm & File Tạm Lên Git**:
   - Trước mọi lệnh `git add` hoặc `git commit`, luôn kiểm tra `git status --short`.
   - **CẤM TUYỆT ĐỐI** stage hoặc commit các file:
     - `.env`, `.env.*` (trừ `.env.example` không chứa khóa thật)
     - `*.pickle`, `*.token`, `client_secret*.json`, `credentials*.json`, `*.pem`, `*.key`
     - Database cục bộ & file chạy thử: `*.db`, `*.sqlite`, `*.sqlite3`, `storage/`, `output/`, `logs/`, `temp/`, `__pycache__/`
   - Luôn stage đích danh các file mã nguồn/cấu hình cần thiết (`git add <file1> <file2>`) thay vì chạy `git add .` hoặc `git add -A` một cách mù quáng.
3. **An Toàn Khi Chạy Lệnh Hệ Thống**:
   - Không chạy các lệnh xóa hàng loạt (`rm -rf`, `git reset --hard`, `git clean -fd`) trên thư mục chứa dữ liệu sản xuất hoặc cấu hình xác thực của người dùng khi chưa được phép.
