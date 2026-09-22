# 🗺️ BẢN ĐỒ LỘ TRÌNH & Ý TƯỞNG DỰ ÁN (PROJECT ROADMAP)
> **Dự án:** AI YouTube Multi-Channel Studio & Automation Hub  
> **Người sáng lập:** `@Giangvv1n` & **Cộng sự (Bạn cùng nhóm)**  
> **Kho mã nguồn:** [https://github.com/Giangvv1n/AI-youtube](https://github.com/Giangvv1n/AI-youtube)

---

## 🎯 1. TỔNG QUAN TẦM NHÌN DỰ ÁN

Mục tiêu của dự án là xây dựng một **"Cỗ máy sản xuất nội dung tự động"** nhắm vào thị trường **Mỹ và Quốc tế (Tiếng Anh)** với doanh thu quảng cáo (RPM) cao gấp 5–10 lần so với thị trường nội địa:
* **Giao diện kép (Dual Interface):** Điều khiển di động tức thì qua **Telegram Bot Nhóm** + Giám sát trực quan thời gian thực qua **Web Dashboard** (`http://localhost:8000`).
* **Theo dõi đóng góp của từng thành viên:** Web Dashboard ghi lại toàn bộ lịch sử ai vừa ra lệnh gì trên Telegram, xếp hạng ai tạo nhiều kịch bản nhất.
* **Không cần lộ mặt (Faceless Channels):** Dùng AI viết kịch bản, giọng đọc bản xứ và footage bản quyền miễn phí.
* **Tự động đăng & Đặt lịch:** Tận dụng tính năng `publishAt` của YouTube để lên lịch phát sóng cả tuần mà không cần bật máy tính 24/24.

---

## ⚡ 2. CHIẾN LƯỢC TỐI ƯU CỤM TÀI KHOẢN GOOGLE ULTRA

Hệ thống được thiết kế độc quyền để tận dụng tối đa lợi thế của các tài khoản **Google Ultra (Google One AI Premium)** mà nhóm đang sở hữu:

```mermaid
graph TD
    subgraph "Hai Cổng Điều Khiển & Giám Sát"
        TG[Telegram Bot Nhóm\nRa lệnh /idea, /channels, duyệt bài]
        WEB[FastAPI Web Dashboard\nTheo dõi thành viên, kênh & tiến độ]
    end

    subgraph "Cơ Sở Dữ Liệu Trung Tâm"
        DB[(SQLite Database\nLưu Users, VideoJobs, ActivityLogs)]
    end

    subgraph "Cụm Tài Khoản Google Ultra"
        ACC1[Tài khoản Ultra 1\nKênh Tâm Lý Học]
        ACC2[Tài khoản Ultra 2\nKênh Triết Lý & Stoic]
        ACC3[Tài khoản Ultra 3\nKênh Khoa Học / Tech]
    end

    subgraph "Tài Nguyên Tối Ưu Hóa"
        D1[(Google Drive 2TB #1)]
        D2[(Google Drive 2TB #2)]
        D3[(Google Drive 2TB #3)]
        
        Q1[YouTube Quota: 10,000/ngày]
        Q2[YouTube Quota: 10,000/ngày]
        Q3[YouTube Quota: 10,000/ngày]

        POOL[Gemini API Key Rotation Pool\n(Tự động đổi key khi hết lượt)]
    end

    TG <--> DB
    WEB <--> DB

    ACC1 --> D1
    ACC1 --> Q1
    ACC1 --> POOL

    ACC2 --> D2
    ACC2 --> Q2
    ACC2 --> POOL

    ACC3 --> D3
    ACC3 --> Q3
    ACC3 --> POOL
```

1. **Kho Lưu Trữ 6TB – 10TB (Multi-Drive Pool):**
   * Mỗi kênh YouTube gắn liền với 1 Drive 2TB riêng. Video render xong tự động đẩy lên Drive và xóa file tạm trên máy tính.
   * Thư mục Drive được **Share cho cả nhóm**, bất kỳ ai cũng có thể tải video về để đăng chéo lên TikTok / Facebook Reels.
2. **Nhân Gấp Nhiều Lần Hạn Ngạch YouTube API (Quota Multiplication):**
   * Mỗi tài khoản Ultra sở hữu riêng 10,000 units/ngày $\to$ Nhóm có tổng cộng **30,000 – 50,000 units/ngày** (thoải mái đăng 20–30 video/ngày).
3. **Bộ Xoay Vòng Gemini API (KeyRotator):**
   * Tất cả API Keys của các tài khoản được gộp thành 1 Pool (`GEMINI_API_KEYS`).
   * Bot tự động đổi key nếu gặp giới hạn tốc độ (Rate Limit), đảm bảo không bao giờ bị nghẽn lệnh.
4. **Phân Lập Rủi Ro (Risk Isolation):**
   * Mỗi kênh nằm trên một tài khoản Google độc lập. Nếu 1 kênh gặp sự cố bản quyền thì các kênh khác hoàn toàn an toàn!

---

## 👥 3. MÔ HÌNH LÀM VIỆC NHÓM TRÊN TELEGRAM & WEB DASHBOARD

* **Không cần sửa code để thêm/bớt người:** Bạn chỉ cần thêm bạn của bạn vào Nhóm chat Telegram (Group Chat) là bạn ấy có thể gõ lệnh và duyệt video ngay.
* **Theo dõi trên Web Dashboard (`http://localhost:8000`):**
  * **Tab 1 - Nhật ký hoạt động:** Cập nhật thời gian thực từng câu lệnh của các thành viên.
  * **Tab 2 - Bảng đóng góp (Leaderboard):** Thống kê ai là người tích cực nhất nhóm.
  * **Tab 3 - Ma trận Kênh & Ultra:** Trực quan hóa kênh nào thuộc tài khoản nào, tình trạng Drive 2TB.
  * **Tab 4 - Video Pipeline:** Xem kịch bản, link Drive và trạng thái xuất bản.
* **Chia sẻ từ xa cho nhóm:** Dùng lệnh `cloudflared tunnel --url http://localhost:8000` để chia sẻ link web bảo mật cho bạn bè vào xem.

---

## 📋 4. MA TRẬN PHÂN CHIA CÔNG VIỆC (TASK CHECKLIST)

Hai bạn hãy thảo luận và điền tên người phụ trách vào chỗ `Phụ trách: @...`:

### 🟢 Giai Đoạn 1: Nền Tảng, Telegram Group & Web Dashboard
- [x] Khởi tạo mã nguồn, Git và liên kết GitHub Repository.
- [x] Thiết lập cấu hình đa kênh (`config/channels.yaml`) và cụm Google Ultra.
- [x] Tạo `KeyRotator` xoay vòng API Keys tự động cho Gemini.
- [x] Xây dựng Telegram Bot hỗ trợ cả tin nhắn riêng và **Nhóm chat chung (Group)**.
- [x] **Xây dựng Web Dashboard thời gian thực (FastAPI + Vanilla CSS Dark Mode).**
- [x] Bảng lưu vết nhật ký (`ActivityLog`) ghi lại từng hành động của thành viên.
- [ ] **Task 1.1:** Tạo group Telegram của nhóm, add bot vào và test lệnh `/start`, `/channels`, `/idea`. *(Phụ trách: `@...`)*
- [ ] **Task 1.2:** Lấy Telegram Group ID và cấu hình vào `ALLOWED_TELEGRAM_GROUPS`. *(Phụ trách: `@...`)*
- [ ] **Task 1.3:** Chạy `python run.py`, mở trình duyệt `http://localhost:8000` kiểm tra Dashboard. *(Phụ trách: `@...`)*

### 🔵 Giai Đoạn 2: Trí Tuệ Kịch Bản (Gemini) & Giọng Đọc (Edge-TTS)
- [x] Viết `ContentAgent` sinh kịch bản chuẩn SEO + Hook 3 giây + phân cảnh JSON.
- [x] Viết `VoiceAgent` tạo giọng đọc AI tiếng Anh bản xứ miễn phí bằng Edge-TTS.
- [ ] **Task 2.1:** Thêm tính năng bot gửi file ghi âm mẫu giọng đọc (`.mp3`) vào Telegram để cả nhóm nghe thử trước khi dựng video. *(Phụ trách: `@...`)*
- [ ] **Task 2.2:** Tinh chỉnh prompt của Gemini để kịch bản bám sát ngách Tâm lý học và Triết lý khắc kỷ (Stoicism). *(Phụ trách: `@...`)*

### 🟡 Giai Đoạn 3: Dựng Video Tự Động & Phụ Đề Nổi Bật (FFmpeg & Pexels)
- [x] Viết `MediaAgent` tự động tìm kiếm video dọc 9:16 từ Pexels API theo từ khóa cảnh.
- [ ] **Task 3.1:** Cài đặt FFmpeg trên máy tính và kiểm tra kết nối qua `ffmpeg_helper.py`. *(Phụ trách: `@...`)*
- [ ] **Task 3.2:** Hoàn thiện `video_agent.py`: Ghép các video nền Pexels khớp với thời lượng giọng đọc MP3. *(Phụ trách: `@...`)*
- [ ] **Task 3.3:** Tự động tạo phụ đề chữ to nhảy từng từ (Phong cách Alex Hormozi) bằng `whisper-timestamped`. *(Phụ trách: `@...`)*
- [ ] **Task 3.4:** Tự động lồng nhạc nền (BGM) kịch tính và giảm âm lượng khi có giọng nói (Audio Ducking). *(Phụ trách: `@...`)*

### 🟣 Giai Đoạn 4: Lưu Trữ Google Drive 2TB & Đăng Tải Đa Kênh YouTube
- [x] Viết khung `DriveAgent` tải video lên đúng Drive của tài khoản Google Ultra tương ứng.
- [ ] **Task 4.1:** Tải file `client_secret.json` cho từng tài khoản Google Ultra đặt vào thư mục `credentials/accounts/account_X/`. *(Phụ trách: `@...`)*
- [ ] **Task 4.2:** Viết hàm xác thực OAuth2 cho từng kênh YouTube và lưu token riêng biệt. *(Phụ trách: `@...`)*
- [ ] **Task 4.3:** Hoàn thiện hàm upload video lên YouTube với trạng thái lên lịch (`publishAt`) theo khung giờ vàng của Mỹ (18h - 21h giờ VN). *(Phụ trách: `@...`)*
- [ ] **Task 4.4:** Bot tự động gửi link chia sẻ Google Drive của video hoàn chỉnh vào group chat và cập nhật lên Dashboard. *(Phụ trách: `@...`)*

### 🔴 Giai Đoạn 5: Tự Động Hóa 100% & Mở Rộng
- [ ] **Task 5.1:** Tự động quét tin tức trend từ Reddit/X mỗi sáng và gợi ý 3 chủ đề cho nhóm duyệt. *(Phụ trách: `@...`)*
- [ ] **Task 5.2:** Tự động tạo ảnh bìa Thumbnail nổi bật bằng thư viện Pillow. *(Phụ trách: `@...`)*
- [ ] **Task 5.3:** Đăng đồng thời video lên TikTok qua TikTok API hoặc Webhook. *(Phụ trách: `@...`)*

---

## 💡 5. KHU VỰC ĐỂ CỘNG SỰ / BẠN CỦA BẠN GHI THÊM Ý TƯỞNG

> ✍️ **Dành cho bạn của Giang:** Bạn hãy mở file này trên máy tính của mình, viết thêm các ý tưởng hoặc đề xuất tính năng bạn muốn có vào bên dưới, sau đó `git commit` và `git push` lên GitHub nhé!

### Ý Tưởng Mới Đề Xuất:
1. *[Ý tưởng 1]: (Ví dụ: Thêm tính năng bot gửi thông báo khi một video trên kênh đạt mốc 10.000 views...)*
2. *[Ý tưởng 2]: (Ví dụ: Tạo hiệu ứng âm thanh Sound Effects - SFX như tiếng "woosh", tiếng gõ chuông khi chuyển cảnh...)*
3. *[Ý tưởng 3]: (Viết ý tưởng của bạn vào đây)*

---

## 🛠️ 6. HƯỚNG DẪN CỘNG TÁC QUA GIT CHO NHÓM

```bash
# 1. Kéo code mới nhất từ GitHub về máy
git pull origin main

# 2. Tạo nhánh riêng để làm tính năng (tránh đụng độ code)
git checkout -b feature/ten-tinh-nang

# 3. Làm xong thì lưu lại
git add .
git commit -m "feat: mo ta ngan gon tinh nang da lam"

# 4. Đẩy lên GitHub
git push origin feature/ten-tinh-nang
```
