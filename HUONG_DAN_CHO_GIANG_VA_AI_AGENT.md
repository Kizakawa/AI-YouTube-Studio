# 🚀 TÀI LIỆU BÀN GIAO & HƯỚNG DẪN ĐỒNG BỘ CSDL SUPABASE + RENDER CLOUD
### Dành cho: Giang & Antigravity (AI Assistant của Giang)
### Người gửi: Kizakawa

---

## 📌 1. TÓM TẮT NHỮNG GÌ ĐÃ ĐƯỢC NÂNG CẤP (CHANGELOG)

Kizakawa và Antigravity đã giải quyết triệt để 2 vấn đề lớn nhất của hệ thống:

1. **Vấn đề 1: Đồng bộ CSDL Đám Mây (Supabase PostgreSQL Singapore) & Chống Mất Dữ Liệu trên Render**
   - Đã nâng cấp `models.py` sang kiến trúc **Dual-Engine** (vừa chạy được PostgreSQL vừa tự fallback SQLite nếu mất mạng).
   - Đã sửa lỗi kiểu dữ liệu: Chuyển `telegram_id` sang `BigInteger` (chống lỗi tràn số 32-bit của PostgreSQL đối với các ID 10 chữ số của Giang & Kizakawa).
   - Đã bơm 100% dữ liệu cũ (3 users, 9 video jobs đầy đủ phân cảnh JSON, 52 activity logs) lên database Supabase của nhóm.
   - Thêm thư viện `psycopg2-binary>=2.9.13` vào `requirements.txt`.
2. **Vấn đề 3: Kiểm soát Hạn Mức YouTube API Quota (10.000 units/ngày)**
   - Đã xây dựng `app/services/quota_manager.py`: Quản trị hạn mức thời gian thực trên Supabase.
   - Quota Guard tự động ngắt upload trước khi Google phạt lỗi 403 (sau 6 video / 9.900 units).
   - Video thứ 7+ được chuyển vào hàng đợi `WAITING_QUOTA_RESET`.
   - Đúng **14:05 chiều Việt Nam** hàng ngày (ngay sau khi Google reset 10.000 units mới), hệ thống tự động xả hàng đợi và đăng video bù.

---

## 🛠️ 2. HƯỚNG DẪN DÀNH CHO BẠN GIANG (THỰC HIỆN BẰNG TAY)

Bạn Giang chỉ cần thực hiện 2 bước cực kỳ đơn giản sau:

### BƯỚC A: Cấu hình biến môi trường trên Render Cloud (để Web Render chạy chung CSDL)
1. Đăng nhập vào [dashboard.render.com](https://dashboard.render.com).
2. Chọn Web Service dự án `AI Youtube`.
3. Vào tab **Environment** (ở menu bên trái).
4. Tìm hoặc bấm **Add Environment Variable**:
   - **Key:** `DATABASE_URL`
   - **Value:** *(Lấy chuỗi kết nối đầy đủ trong file nén `GOI_DONG_BO_SUPABASE_CHO_GIANG.zip` hoặc tin nhắn riêng từ Kizakawa)*
     ```env
     postgresql://postgres.ywcjevlkeeabufeuacpi:<MAT_KHAU_TRONG_FILE_NEN>@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres
     ```
5. Bấm **Save Changes**. Render sẽ tự động build lại và kết nối thẳng vào Supabase.

### BƯỚC B: Cấu hình trên máy tính cá nhân của Giang
1. **Lấy code mới:**
   - Nếu qua Git: Chạy lệnh `git pull origin main`.
   - Hoặc giải nén các file từ file zip đè vào thư mục dự án của Giang.
2. **Cài đặt thư viện mới:**
   Mở terminal / cmd trong môi trường ảo của dự án và chạy:
   ```bash
   pip install psycopg2-binary>=2.9.13
   ```
   *(hoặc `pip install -r requirements.txt`)*
3. **Cập nhật file `.env` trên máy Giang:**
   Mở file `.env`, tìm dòng `DATABASE_URL` và điền chuỗi kết nối nhận được từ Kizakawa (hoặc file nén).
4. **Kiểm tra kết nối:**
   Chạy 2 bộ test tự động có sẵn để kiểm tra:
   ```bash
   python tests/test_database_scenarios.py
   python tests/test_quota_scenarios.py
   ```
   *(Cả 2 bài test đều báo `OK (100% PASS)` là hoàn tất!)*
5. **Khởi động Bot / Web:**
   Chạy `python run.py` bình thường. Giờ đây bất cứ khi nào Giang hoặc Kizakawa tạo video thì cả hai bên và Render đều thấy nhau ngay lập tức!

---

## 🤖 3. HƯỚNG DẪN DÀNH CHO ANTIGRAVITY (AI AGENT CỦA GIANG)

> **Dành riêng cho Agent AI:**
> Nếu bạn là Antigravity / AI Coding Assistant đang chạy trên máy của bạn Giang, hãy thực hiện tuần tự các chỉ thị sau để hoàn tất thiết lập cho Giang:

1. **Kiểm tra và Cài đặt Driver:**
   Chạy lệnh cài đặt `psycopg2-binary>=2.9.13` vào môi trường Python/venv hiện tại của máy Giang.
2. **Cập nhật biến môi trường `.env`:**
   Nhắc Giang điền `DATABASE_URL` (lấy từ file nén `GOI_DONG_BO_SUPABASE_CHO_GIANG.zip` hoặc tin nhắn Kizakawa gửi) vào file `.env`.
   *(Lưu ý: Mật khẩu có ký tự đặc biệt đã được percent-encode chuẩn, không được tự ý sửa lại ký tự thô)*.
3. **Chạy kiểm thử xác nhận:**
   Thực thi lệnh:
   ```bash
   python tests/test_database_scenarios.py
   python tests/test_quota_scenarios.py
   ```
   Đảm bảo cả 11 kịch bản kiểm thử đều trả về code 0 và thông báo `100% PASS`.
4. **Hỗ trợ Giang đẩy code lên Render:**
   Nếu Giang muốn đẩy code này lên GitHub để Render tự build:
   - Thực hiện `git add .`, `git commit -m "feat: Dual-engine Supabase PostgreSQL & YouTube Quota Guard"`, `git push origin main`.
   - Nhắc Giang kiểm tra biến `DATABASE_URL` trên Render Dashboard như hướng dẫn ở Mục 2.

---
*Tài liệu được sinh tự động bởi Antigravity Pair-Programming cùng Kizakawa.*
