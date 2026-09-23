# 🎬 AI-YouTube: Multi-Agent Video, Telegram Bot & Web Dashboard Studio

> 📖 **DÀNH CHO NGƯỜI MỚI THAM GIA DỰ ÁN:**  
> Toàn bộ ý tưởng kinh doanh, tầm nhìn và kiến trúc kỹ thuật chi tiết của hệ thống đã được đóng gói đầy đủ tại:  
> 👉 **[Tài Liệu Toàn Tập: Đặc Tả Ý Tưởng & Kiến Trúc Hệ Thống (PROJECT_WHITEPAPER.md)](PROJECT_WHITEPAPER.md)**

Hệ thống tự động hóa sản xuất video ngắn (Shorts) và quản lý đa kênh YouTube thông qua giao diện kép: **Telegram Bot (Điều khiển di động)** và **Web Dashboard (Giám sát thời gian thực)**, hỗ trợ tối ưu hóa cụm tài khoản **Google Ultra (Google One AI Premium)**.

---

## 🌟 Tính Năng Nổi Bật

* **🤖 Đồng Sáng Tạo Kịch Bản 2 Chiều (Conversational AI Director):**
  * Thảo luận, phản biện ý tưởng qua `/chat <nội dung>` hoặc Tag `@VV1n_mybot` / Reply trực tiếp trong nhóm.
  * AI Director phân tích tâm lý người xem, đề xuất 3 góc nhìn viral và 3 câu Hook 3 giây triệu view.
  * **Chốt kịch bản 1-click:** Bấm nút `[🎬 Chốt Kịch Bản Này & Sản Xuất]` để tự động chuyển đoạn chat thảo luận thành kịch bản phân cảnh chuẩn `ScriptContract` và đưa vào hàng chờ dựng video.
* **🌐 Web Dashboard Quản Trị Trực Quan (FastAPI + Vanilla CSS):**
  * **Nhật ký thời gian thực (Live Activity Feed):** Theo dõi tức thì ai vừa gõ lệnh gì trên Telegram.
  * **Bảng xếp hạng đóng góp (Team Leaderboard):** Thống kê số lượng kịch bản/video từng Admin và Creator đã tạo.
  * **Ma trận Cụm Google Ultra & Kênh YouTube:** Kiểm tra trạng thái token, kho Drive 2TB và hạn ngạch API.
  * **Tiến độ sản xuất (Pipeline Manager):** Theo dõi từng video từ bản nháp đến lúc công chiếu.
* **📱 Không Gian Làm Việc Nhóm Telegram (Group Workspace):** Mọi thành viên cùng ra lệnh bằng `/idea` hoặc `/chat`, cùng đọc kịch bản và cùng bấm nút duyệt video.
* **⚡ Cụm Tài Khoản Google Ultra:** Tự động xoay vòng API Keys (`KeyRotator`), khai thác kho lưu trữ 6TB–10TB Google Drive (`DriveAgent`) và nhân hạn ngạch YouTube API (30,000–50,000 units/ngày).
* **🧠 Kịch Bản AI Chuẩn CTR (Gemini API):** Tự động tạo kịch bản 30-50s với câu Hook 3 giây đầu giữ chân người xem.
* **🗣️ Giọng Đọc Bản Xứ (Edge-TTS):** Giọng đọc AI tiếng Anh/tiếng Việt tự nhiên, hoàn toàn miễn phí.
* **⏰ Đặt Lịch Tự Động (YouTube Data API v3):** Sử dụng tính năng `publishAt` của YouTube để đặt lịch phát sóng cả tuần mà không cần bật máy tính 24/24.

---

## 📁 Cấu Trúc Dự Án

```text
Youtube/
├── PROJECT_WHITEPAPER.md          # TÀI LIỆU ĐẶC TẢ TOÀN BỘ Ý TƯỞNG & KIẾN TRÚC DỰ ÁN
├── README.md                      # Tài liệu tổng quan & hướng dẫn cài đặt nhanh
├── ROADMAP.md                     # Bản đồ lộ trình & Phân chia công việc cho team
├── requirements.txt               # Danh sách thư viện Python
├── .env.example                   # File mẫu cấu hình biến môi trường
├── run.py                         # Khởi chạy song song Telegram Bot & Web Dashboard
│
├── config/                        # Cấu hình hệ thống
│   ├── settings.py                # Quản lý cấu hình, nạp pool API keys
│   └── channels.yaml              # Cấu hình cụm kênh và tài khoản Google Ultra
│
├── credentials/                   # Thư mục chứa khóa bảo mật (Không push Git)
│   └── accounts/                  # Thư mục token riêng của từng tài khoản Ultra
│
├── app/                           # Mã nguồn chính
│   ├── agents/                    # Các AI Agent chuyên môn
│   │   ├── content_agent.py       # Viết kịch bản & SEO (Gemini KeyRotator)
│   │   ├── voice_agent.py         # Giọng đọc AI (Edge-TTS)
│   │   ├── media_agent.py         # Tải video/ảnh nền (Pexels API)
│   │   ├── video_agent.py         # Dựng video & phụ đề (FFmpeg/MoviePy)
│   │   ├── drive_agent.py         # Quản lý kho Google Drive 2TB
│   │   └── publisher_agent.py     # Upload & Đặt lịch (YouTube API)
│   │
│   ├── bot/                       # Giao diện Telegram Bot Nhóm
│   │   ├── handlers.py            # Xử lý lệnh (/start, /channels, /idea...) & ghi ActivityLog
│   │   └── keyboards.py           # Nút bấm tương tác trên Telegram
│   │
│   ├── database/                  # Quản lý cơ sở dữ liệu SQLite
│   │   └── models.py              # Bảng Users, VideoJobs, ActivityLogs
│   │
│   ├── web/                       # Giao diện Web Dashboard thời gian thực
│   │   ├── server.py              # FastAPI Backend REST APIs
│   │   └── static/                # Giao diện Dark Mode (HTML, CSS, JS)
│   │
│   └── utils/                     # Công cụ hỗ trợ (KeyRotator, FFmpeg...)
│
├── assets/                        # Tài nguyên dùng chung (Fonts, Audio BGM)
└── storage/                       # Lưu trữ file tạm và video thành phẩm
```

---

## 🚀 Hướng Dẫn Cài Đặt & Khởi Động

### Bước 1: Clone Repository
```bash
git clone https://github.com/Giangvv1n/AI-youtube.git
cd AI-youtube
```

### Bước 2: Tạo Môi Trường Ảo & Cài Thư Viện
```bash
python -m venv venv

# Kích hoạt trên Windows:
.\venv\Scripts\activate

# Cài đặt thư viện:
pip install -r requirements.txt
```

### Bước 3: Cấu Hình File .env
Sao chép `.env.example` thành `.env`:
```bash
copy .env.example .env
```
Điền các thông tin:
* `TELEGRAM_BOT_TOKEN`: Token bot từ `@BotFather`.
* `ALLOWED_TELEGRAM_GROUPS`: ID nhóm chat Telegram của bạn và nhóm bạn.
* `GEMINI_API_KEYS`: Danh sách các API Key từ cụm tài khoản Google Ultra, phân cách bởi dấu phẩy.
* `PEXELS_API_KEY`: Lấy miễn phí tại [pexels.com/api](https://www.pexels.com/api/).

### Bước 4: Chạy Hệ Thống
```bash
python run.py
```
* **Web Dashboard:** Mở trình duyệt truy cập: `http://localhost:8000`
* **Telegram Bot:** Gửi `/start` trong nhóm chat của bạn để kích hoạt!

### 📱 Các Lệnh Telegram Tiêu Biểu:
* `/chat <ý tưởng / câu hỏi>`: Thảo luận, phản biện kịch bản với Đạo diễn AI *(hoặc chỉ cần Tag `@VV1n_mybot` / Reply tin nhắn bot)*.
* `/channels`: Xem danh sách kênh YouTube và bấm nút chọn kênh muốn làm việc.
* `/idea <chủ đề>`: Tự động viết kịch bản AI phân cảnh ngay lập tức cho kênh đang chọn.
* `/status`: Báo cáo số lượng video trong hệ thống, kho Drive 2TB và trạng thái cụm Ultra.

---

### 🌐 Cách Chia Sẻ Web Dashboard Cho Bạn Bè Xem Từ Xa (Miễn Phí 100%)

Nếu bạn muốn bạn bè ở nơi khác cùng xem Dashboard mà không cần ngồi chung mạng Wi-Fi:
1. Tải công cụ **Cloudflare Tunnel (`cloudflared`)** miễn phí.
2. Chạy lệnh:
   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```
3. Cloudflare sẽ cấp ngay một đường link HTTPS công khai (ví dụ: `https://something.trycloudflare.com`) để bạn gửi cho bạn bè truy cập tức thì!
