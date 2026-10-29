---
name: agent-reach-youtube-intelligence
description: >-
  Hệ thống "Đôi Mắt Internet" tích hợp Panniantong/Agent-Reach CLI (YouTube, Reddit, Twitter/X, GitHub,
  Jina Web Reader — 0đ phí API) kết hợp trực tiếp với YouTube Data API v3 & Analytics API v2 để quản trị
  7 kênh YouTube, săn chủ đề Viral theo 5 ngách, bóc tách kịch bản/phụ đề đối thủ và kiểm toán hiệu suất kênh.
---

# Agent-Reach & YouTube 7-Channel Intelligence (`agent-reach-youtube-intelligence`)

Skill này tích hợp **[Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach)** (`agent-reach v1.6.0`) cùng bộ công cụ quản trị 7 kênh YouTube của hệ thống **AI YouTube Studio**, giúp Agent có khả năng đọc & tìm kiếm trực tiếp trên YouTube, Reddit, Twitter/X, GitHub, Web mà không tốn phí API, đồng thời tự động đối chiếu với số liệu thực của 7 kênh.

---

## 1. Đường Dẫn Công Cụ Đã Cài Đặt Trong Hệ Thống

- **CLI gốc `agent-reach`**:
  ```powershell
  D:\AI_YouTube_Project_Ready\AI-youtube-main\venv\Scripts\agent-reach.exe
  ```
- **CLI Tích Hợp Quản Trị 7 Kênh & Săn Trend 5 Ngách (`reach_channel_intel.py`)**:
  ```powershell
  D:\AI_YouTube_Project_Ready\AI-youtube-main\venv\Scripts\python.exe d:\AI_YouTube_Project_Ready\.agents\skills\agent-reach-youtube-intelligence\scripts\reach_channel_intel.py
  ```

---

## 2. Các Luồng Tác Vụ Chính Khi Quản Lý 7 Kênh YouTube

### A. Kiểm Toán Nhanh Toàn Bộ 7 Kênh (100% Số Liệu Thật Data API v3 + Analytics API v2)
Chạy lệnh một chạm để lấy toàn bộ số liệu thật (`Subs`, `Views`, `Likes`, `AVD`, `APV %`, `Stayed %`, `Swipe Away %`) của cả 7 kênh:
```powershell
D:\AI_YouTube_Project_Ready\AI-youtube-main\venv\Scripts\python.exe d:\AI_YouTube_Project_Ready\.agents\skills\agent-reach-youtube-intelligence\scripts\reach_channel_intel.py --mode audit
```
- **Tự động tuân thủ quy chuẩn `youtube-real-metrics-reporting`**:
  - Video mới `< 24–48h` chưa chốt sổ Analytics được gắn cờ `PENDING_24_48H` (`⏳ Chờ Analytics chốt sổ (24–48h)`).
  - Hiển thị đầy đủ tỷ lệ giữ chân thật 28 ngày (`overview_28d`) và từng video gần nhất (`recent_videos`).

### B. Săn Chủ Đề Viral & Phân Tích Đối Thủ Theo 5 Ngách (Zero API Fee qua `agent-reach`)
Quét các video Shorts đối thủ đang thịnh hành trên YouTube cho từng ngách trong 5 ngách của hệ thống:
```powershell
# Ngách 1: Cứu hộ động vật (Paws & Hearts Rescue)
D:\AI_YouTube_Project_Ready\AI-youtube-main\venv\Scripts\python.exe d:\AI_YouTube_Project_Ready\.agents\skills\agent-reach-youtube-intelligence\scripts\reach_channel_intel.py --mode radar --niche 01_animal_rescue_paws

# Ngách 2: Động vật hoang dã & Săn mồi (Wild Odyssey / Odisseia Selvagem)
D:\AI_YouTube_Project_Ready\AI-youtube-main\venv\Scripts\python.exe d:\AI_YouTube_Project_Ready\.agents\skills\agent-reach-youtube-intelligence\scripts\reach_channel_intel.py --mode radar --niche 02_wildlife_predators

# Ngách 3: Bí ẩn đại dương & Hiện tượng cực đoan (The Mysteries of the World / Mistérios do Mundo)
D:\AI_YouTube_Project_Ready\AI-youtube-main\venv\Scripts\python.exe d:\AI_YouTube_Project_Ready\.agents\skills\agent-reach-youtube-intelligence\scripts\reach_channel_intel.py --mode radar --niche 03_deep_sea_mysteries

# Ngách 4: Tâm lý học tối & Hành vi con người (Psychology of the World)
D:\AI_YouTube_Project_Ready\AI-youtube-main\venv\Scripts\python.exe d:\AI_YouTube_Project_Ready\.agents\skills\agent-reach-youtube-intelligence\scripts\reach_channel_intel.py --mode radar --niche 04_dark_psychology

# Ngách 5: Giả thuyết lịch sử & Thảm họa (Alternate History & "What If?")
D:\AI_YouTube_Project_Ready\AI-youtube-main\venv\Scripts\python.exe d:\AI_YouTube_Project_Ready\.agents\skills\agent-reach-youtube-intelligence\scripts\reach_channel_intel.py --mode radar --niche 05_alternate_history
```

### C. Bóc Tách Phụ Đề / Kịch Bản Video Viral Bất Kỳ Trên YouTube
Khi người dùng gửi 1 link YouTube Shorts của đối thủ (hoặc tìm thấy qua `radar`), dùng `agent-reach` để trích xuất toàn bộ lời thoại/phụ đề (`subtitle`) về làm tư liệu phân tích Hook 3 giây đầu:
```powershell
D:\AI_YouTube_Project_Ready\AI-youtube-main\venv\Scripts\agent-reach.exe youtube read "<URL_HOẶC_VIDEO_ID>"
D:\AI_YouTube_Project_Ready\AI-youtube-main\venv\Scripts\agent-reach.exe youtube subtitle "<URL_HOẶC_VIDEO_ID>" --lang "en,pt,vi"
```

### D. Khai Thác Đa Nền Tảng (Reddit, Twitter/X, GitHub, Web/Jina Reader)
Sử dụng trực tiếp CLI `agent-reach.exe` để đọc nội dung đa nền tảng:
```powershell
# Kiểm tra danh sách nền tảng và tham số
D:\AI_YouTube_Project_Ready\AI-youtube-main\venv\Scripts\agent-reach.exe channels

# Đọc nội dung sạch (Markdown) từ bất kỳ bài báo / URL nào qua Jina Reader
D:\AI_YouTube_Project_Ready\AI-youtube-main\venv\Scripts\agent-reach.exe web read "<URL>"

# Tìm kiếm trên Reddit / Twitter / GitHub
D:\AI_YouTube_Project_Ready\AI-youtube-main\venv\Scripts\agent-reach.exe reddit search "natureismetal Komodo dragon" --limit 5
D:\AI_YouTube_Project_Ready\AI-youtube-main\venv\Scripts\agent-reach.exe github search "youtube shorts automation" --limit 5
```

---

## 3. Quy Tắc An Toàn & Xử Lý Lỗi (`agent-reach`)
1. **Chế độ Chỉ Đọc (Read-Only)**: `agent-reach` chỉ đọc/tìm kiếm dữ liệu công khai, tuyệt đối không tự ý đăng bài hay bình luận.
2. **Xử lý mã lỗi chuẩn JSON**:
   - `NEED_SETUP`: Chạy `agent-reach.exe add <channel>` nếu người dùng muốn mở thêm kênh phụ (`reddit`, `twitter`, v.v.).
   - `NEED_LOGIN`: Giữ nguyên kết quả công khai đã thu thập được; chỉ chạy `agent-reach.exe login <channel>` khi người dùng yêu cầu đăng nhập trình duyệt riêng.
   - `RATE_LIMITED` / `PLATFORM_BLOCKED`: Dừng ngay, không lặp vòng retry để bảo vệ IP máy chủ.
