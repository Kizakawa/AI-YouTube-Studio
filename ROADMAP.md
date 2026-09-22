# 🗺️ LỘ TRÌNH PHÁT TRIỂN & Ý TƯỞNG DỰ ÁN (PROJECT ROADMAP)

> **Dành cho:** Nhóm phát triển dự án AI-YouTube (`@Giangvv1n` & Cộng sự).  
> **Mục tiêu:** Xây dựng hệ thống tự động hóa từ khâu lên ý tưởng bằng Telegram đến lúc video công chiếu trên nhiều kênh YouTube.

---

## 👥 Ma Trận Phân Chia Công Việc (Task Checklist)

Đánh dấu `[x]` khi hoàn thành và gán tag tên người phụ trách (`@Giang` hoặc `@Ban`):

### Giai Đoạn 1: Nền Tảng & Điều Khiển Đa Người Dùng Qua Telegram
- [x] Khởi tạo khung dự án & Git repository (`https://github.com/Giangvv1n/AI-youtube.git`)
- [x] Thiết lập cấu hình đa kênh (`config/channels.yaml`) và quản lý môi trường (`.env`)
- [ ] **Task 1.1:** Hoàn thiện Bot Telegram nhận lệnh `/idea <chủ đề>` và lưu vào SQLite Database. *(Phụ trách: `@...`)*
- [ ] **Task 1.2:** Thiết lập logic phân quyền đa người dùng (`admin` vs `creator`) và chuyển đổi kênh hoạt động bằng lệnh `/channels`. *(Phụ trách: `@...`)*
- [ ] **Task 1.3:** Thêm tính năng chat tự do với Bot để Brainstorm ý tưởng (Bot đóng vai trò trợ lý nội dung). *(Phụ trách: `@...`)*

### Giai Đoạn 2: Pipeline Sáng Tạo Nội Dung & Giọng Đọc AI
- [ ] **Task 2.1:** Kết nối Google Gemini API để tạo kịch bản Shorts 30-50s chuẩn cấu trúc Hook 3s + JSON phân cảnh. *(Phụ trách: `@...`)*
- [ ] **Task 2.2:** Tích hợp `edge-tts` tạo file âm thanh MP3 tự động theo giọng đọc từng kênh (`en-US-ChristopherNeural`, `vi-VN-HoaiMyNeural`...). *(Phụ trách: `@...`)*
- [ ] **Task 2.3:** Tạo tính năng gửi bản ghi âm giọng đọc thử (Voice sample) qua tin nhắn thoại Telegram để nghe trước. *(Phụ trách: `@...`)*

### Giai Đoạn 3: Tự Động Hóa Dựng Video (Media & Subtitles)
- [ ] **Task 3.1:** Kết nối Pexels API tải video nền chất lượng cao tỷ lệ 9:16 tương ứng với từ khóa từng phân cảnh. *(Phụ trách: `@...`)*
- [ ] **Task 3.2:** Dùng MoviePy / FFmpeg ghép video nền nối tiếp nhau khớp với độ dài giọng đọc. *(Phụ trách: `@...`)*
- [ ] **Task 3.3:** Tự động tạo phụ đề động nhảy từng chữ (Phong cách Alex Hormozi) bằng `whisper-timestamped`. *(Phụ trách: `@...`)*
- [ ] **Task 3.4:** Thêm kho nhạc nền (BGM) lofi/kịch tính và xử lý tự động giảm âm lượng nhạc khi có tiếng nói (Audio Ducking). *(Phụ trách: `@...`)*
- [ ] **Task 3.5:** Gửi file video `.mp4` hoàn chỉnh kèm nút bấm duyệt (`[Đăng Ngay]`, `[Lên Lịch]`, `[Làm Lại]`) vào Telegram. *(Phụ trách: `@...`)*

### Giai Đoạn 4: Quản Lý Đa Kênh YouTube & Đặt Lịch
- [ ] **Task 4.1:** Thiết lập luồng Google OAuth2 cho từng kênh YouTube và lưu token riêng (`credentials/tokens/channel_{id}.json`). *(Phụ trách: `@...`)*
- [ ] **Task 4.2:** Viết hàm tải video lên YouTube với trạng thái `private` hoặc `scheduled` (tham số `publishAt`). *(Phụ trách: `@...`)*
- [ ] **Task 4.3:** Tự động sinh thẻ Tags, mô tả và gắn link affiliate vào phần mô tả. *(Phụ trách: `@...`)*
- [ ] **Task 4.4:** Cơ chế tự động tính toán giờ vàng đăng bài (Khung giờ Mỹ: 18h - 21h theo giờ Việt Nam). *(Phụ trách: `@...`)*

### Giai Đoạn 5: Tính Năng Nâng Cao & Tự Động Hoá 100%
- [ ] **Task 5.1:** Tự động cào tin tức nóng từ các nguồn RSS / X / Reddit để tự đề xuất ý tưởng mỗi sáng cho bạn duyệt.
- [ ] **Task 5.2:** Tự động tạo Thumbnail bắt mắt bằng Pillow (chữ to, nền ấn tượng).
- [ ] **Task 5.3:** Báo cáo phân tích (Analytics): Bot tự báo cáo số view, sub tăng của từng kênh mỗi tối.

---

## 💡 Ý Tưởng Nội Dung Triển Khai Ban Đầu (Content Niches)

Nhóm có thể bắt đầu với 2 ngách có RPM cao nhất tại thị trường Mỹ:

### 1. Kênh: Dark Psychology & Human Behavior (Tâm lý học hành vi)
* **Phong cách:** Bí ẩn, giật gân, khơi gợi sự tò mò.
* **Hình ảnh:** Tông màu tối, điện ảnh, các cảnh quay thành phố đêm, khuôn mặt mờ ảo.
* **Nhạc nền:** Trầm, căng thẳng, kịch tính.
* **Mẫu tiêu đề:** *"3 Psychological Signs Someone Is Secretly Jealous of You"*, *"The Dark Mirror Effect You Need to Know"*.

### 2. Kênh: Daily Stoic & Wisdom (Triết lý sống & Động lực)
* **Phong cách:** Điềm đạm, sâu sắc, truyền cảm hứng mạnh mẽ.
* **Hình ảnh:** Tượng điêu khắc La Mã/Hy Lạp cổ đại, thiên nhiên hùng vĩ, núi non.
* **Nhạc nền:** Nhạc piano sâu lắng hoặc cinematic epic.
* **Mẫu tiêu đề:** *"Marcus Aurelius on How to Deal with Fake Friends"*, *"Why Discipline Will Always Beat Motivation"*.

---

## 🛠️ Quy Trình Làm Việc Nhóm Với Git

1. **Clone dự án về máy:**
   ```bash
   git clone https://github.com/Giangvv1n/AI-youtube.git
   ```
2. **Tạo nhánh (Branch) mới khi làm tính năng:**
   ```bash
   # Ví dụ làm phần voice agent:
   git checkout -b feature/voice-agent
   ```
3. **Commit rõ ràng:**
   ```bash
   git add .
   git commit -m "feat: tich hop edge-tts tao giong doc tieng anh"
   ```
4. **Push lên GitHub và tạo Pull Request (PR):**
   ```bash
   git push origin feature/voice-agent
   ```
5. **LƯU Ý BẢO MẬT:** Tuyệt đối không commit file `.env`, file `client_secret.json` hoặc thư mục `credentials/tokens/` lên GitHub! (Đã được cấu hình trong `.gitignore`).
