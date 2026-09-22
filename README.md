# 🎬 AI-YouTube: Multi-Agent Video & Channel Automation

Hệ thống tự động hóa sản xuất video ngắn (Shorts) và quản lý đa kênh YouTube thông qua giao diện điều khiển từ xa bằng **Telegram Bot**, hỗ trợ **nhiều tài khoản cộng tác** và **nhiều kênh YouTube mục tiêu**.

---

## 🌟 Tính Năng Nổi Bật

* **📱 Điều Khiển Từ Xa Bằng Telegram:** Ra lệnh kịch bản, xem trước video và duyệt đăng bài ngay trên điện thoại di động.
* **👥 Hỗ Trợ Đa Người Dùng (Multi-User):** Bạn và cộng sự có thể cùng đăng nhập bot với quyền `admin` hoặc `creator`, mỗi người có phiên làm việc riêng.
* **📺 Quản Lý Đa Kênh YouTube (Multi-Channel):** Đăng tải và lên lịch video vào nhiều kênh khác nhau từ cùng một hệ sinh thái (quản lý token độc lập).
* **🧠 Kịch Bản AI Chuẩn CTR (Gemini API):** Tự động tạo kịch bản 30-50s với câu Hook 3 giây đầu giữ chân người xem.
* **🗣️ Giọng Đọc Tự Nhiên (Edge-TTS):** Giọng đọc bản xứ tiếng Anh (hoặc tiếng Việt) mượt mà, hoàn toàn miễn phí.
* **🎞️ Tự Động Ghép Media & Phụ Đề (Pexels & FFmpeg):** Tự động tải cảnh quay minh họa, lồng tiếng, chèn phụ đề động và nhạc nền.
* **⏰ Đặt Lịch Tự Động (YouTube Data API v3):** Sử dụng tính năng `publishAt` của YouTube để đặt lịch phát sóng cả tuần mà không cần treo máy.

---

## 📁 Cấu Trúc Dự Án

```text
Youtube/
├── .gitignore                     # Bỏ qua venv, file tạm, credentials, token
├── README.md                      # Tài liệu tổng quan & hướng dẫn cài đặt
├── ROADMAP.md                     # Kế hoạch tính năng & Phân chia công việc cho team
├── requirements.txt               # Danh sách thư viện Python
├── .env.example                   # File mẫu biến môi trường
├── run.py                         # Điểm khởi chạy ứng dụng
│
├── config/                        # Cấu hình hệ thống
│   ├── settings.py                # Quản lý cấu hình & phân quyền
│   └── channels.yaml              # Cấu hình danh sách các kênh YouTube
│
├── credentials/                   # Thư mục chứa khóa bảo mật (Không push lên Git)
│   ├── client_secret.json         # File OAuth 2.0 từ Google Cloud Console
│   └── tokens/                    # Token đăng nhập của từng kênh
│
├── app/                           # Mã nguồn chính
│   ├── agents/                    # Các AI Agent chuyên môn
│   │   ├── content_agent.py       # Viết kịch bản & SEO (Gemini)
│   │   ├── voice_agent.py         # Giọng đọc AI (Edge-TTS)
│   │   ├── media_agent.py         # Tải video/ảnh nền (Pexels API)
│   │   ├── video_agent.py         # Dựng video & phụ đề (FFmpeg/MoviePy)
│   │   └── publisher_agent.py     # Upload & Đặt lịch (YouTube API)
│   │
│   ├── bot/                       # Giao diện Telegram Bot
│   │   ├── handlers.py            # Xử lý lệnh (/start, /channels, /idea...)
│   │   └── keyboards.py           # Nút bấm tương tác trên Telegram
│   │
│   ├── database/                  # Quản lý cơ sở dữ liệu SQLite
│   │   └── models.py              # Bảng Users, VideoJobs...
│   │
│   └── utils/                     # Công cụ hỗ trợ (FFmpeg helper...)
│
├── assets/                        # Tài nguyên dùng chung (Fonts, Audio BGM)
└── storage/                       # Lưu trữ file tạm và video thành phẩm
```

---

## 🚀 Hướng Dẫn Cài Đặt Cho Nhóm

### Bước 1: Clone Repository
```bash
git clone https://github.com/Giangvv1n/AI-youtube.git
cd AI-youtube
```

### Bước 2: Tạo Môi Trường Ảo & Cài Đặt Thư Viện
```bash
# Tạo môi trường ảo
python -m venv venv

# Kích hoạt trên Windows:
.\venv\Scripts\activate

# Cài đặt thư viện:
pip install -r requirements.txt
```

### Bước 3: Cài Đặt FFmpeg
1. Tải bản build FFmpeg cho Windows tại [gyan.dev](https://www.gyan.dev/ffmpeg/builds/).
2. Giải nén và thêm thư mục `bin` vào biến môi trường `PATH` của Windows.
3. Kiểm tra bằng lệnh: `ffmpeg -version`.

### Bước 4: Thiết Lập Biến Môi Trường (.env)
Sao chép `.env.example` thành `.env`:
```bash
copy .env.example .env
```
Điền các thông tin:
* `TELEGRAM_BOT_TOKEN`: Lấy từ bot `@BotFather` trên Telegram.
* `ALLOWED_TELEGRAM_USERS`: Danh sách Telegram User ID của bạn và bạn của bạn (ví dụ: `123456789:admin,987654321:creator`). Lấy ID bằng cách chat với bot `@userinfobot`.
* `GEMINI_API_KEY`: Lấy tại [aistudio.google.com](https://aistudio.google.com).
* `PEXELS_API_KEY`: Lấy tại [pexels.com/api](https://www.pexels.com/api/).

### Bước 5: Chạy Ứng Dụng
```bash
python run.py
```
Sau khi chạy, hãy mở Telegram và gửi `/start` cho con bot của bạn để bắt đầu!
