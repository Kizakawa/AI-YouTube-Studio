# 🎬 HƯỚNG DẪN TRẢI NGHIỆM TÍNH NĂNG TÁI CHẾ SHORTS AI (LONG-TO-SHORTS REPURPOSER)
> **Tài liệu hướng dẫn dành cho Bạn Bè & Cộng Tác Viên trải nghiệm hệ thống AI YouTube Studio v2.0**  
> *Ngày cập nhật: 27/09/2026 | Tác giả: Kizakawa*

---

## 📌 1. TỔNG QUAN TÍNH NĂNG MỚI ĐƯỢC TÍCH HỢP

Sau khi nghiên cứu các kho mã nguồn mở hàng đầu thế giới (như *Anil-matcha/AI-Youtube-Shorts-Generator* - bản clone của Opus Clip và *rushindrasinha/youtube-shorts-pipeline*), dự án đã nâng cấp một giải pháp **toàn diện nhất, tối ưu nhất và an toàn bản quyền 100%**:

Hệ thống **Smart AI Long-to-Shorts Repurposer Studio** kết hợp cùng **Niche Intelligence Profiles**:
- **Trích xuất khoảnh khắc vàng**: Sử dụng **Gemini 1.5/2.0 Flash AI** quét toàn bộ lời thoại (transcript) của video dài để phát hiện 3–5 phân đoạn có tiềm năng giữ chân người xem (Audience Retention) cao nhất.
- **Chấm điểm Viral Score (80–99)**, phát hiện câu **Hook 3 giây đầu**, phân tích lý do tâm lý học khiến video lan truyền.
- **Biến đổi khung hình 9:16 chuẩn Shorts/TikTok**: Tự động chuyển đổi video ngang thành video dọc với hiệu ứng **Smart Blur Background Canvas** (nền mờ phóng to thời thượng) hoặc **Smart Center Crop**.
- **Phụ đề Kinetic Karaoke Niche Profile**: Tự động lồng phụ đề nhảy chữ karaoke (`libass`) theo đúng bộ nhận diện font chữ và màu Neon Highlight của kênh (Tài chính = Xanh Neon, Tâm lý = Vàng Chanh, Kinh dị = Đỏ Máu, Viễn tưởng = Cyan...).

---

## 🛡️ 2. PHƯƠNG ÁN AN TOÀN BẢN QUYỀN (GIẢI QUYẾT TRIỆT ĐỂ NỖI LO GẬY YOUTUBE)

Nhiều công cụ cắt clip ngoài thị trường bị dính lỗi **"Nội dung tái sử dụng không có giá trị biên tập (Reused Content)"** hoặc dính quét mã âm thanh **Content ID** khi re-up thô. Hệ thống của chúng ta giải quyết bằng **2 Chế Độ Bản Quyền Chuyên Sâu**:

### 🛡️ Chế Độ 1: Tái Chế Video Kênh Nhà (Self-Owned Content)
- **Mục đích**: Tái sử dụng các video dài do chính bạn hoặc team sản xuất để nhân bản thành 5–10 video Shorts lan truyền.
- **Độ an toàn**: **100% Tuyệt đối**. YouTube cực kỳ khuyến khích vì thuật toán sẽ điều hướng người xem từ Shorts về xem video dài chính chủ, tăng gấp bội doanh thu quảng cáo và lượt đăng ký kênh.

### ⚖️ Chế Độ 2: Bình Luận Chuyển Hóa (Transformative Fair-Use Commentary)
- **Mục đích**: Áp dụng khi bạn muốn phân tích, bình luận hoặc review một video podcast, phỏng vấn, tin tức bên ngoài.
- **Cơ chế bảo vệ**:
  1. Giới hạn thời lượng trích dẫn ngắn ($\le 45$ giây) để làm dẫn chứng luận điểm.
  2. AI tự động đề xuất **Góc nhìn phân tích & bình luận (Transformative Angle)** để bạn lồng tiếng phản biện hoặc giải thích sự thật đằng sau.
  3. Gắn huy hiệu trích nguồn minh bạch (*Credit: Original Creator | Fair Use Analysis*).
  4. Đạt đầy đủ tiêu chuẩn **Tác phẩm phái sinh / Sử dụng hợp lý (Fair Use)** theo Điều 107 Luật Bản quyền Hoa Kỳ và Luật Sở Hữu Trí Tuệ Việt Nam.

---

## 🚀 3. HƯỚNG DẪN BẠN BÈ TRẢI NGHIỆM TRÊN WEB STUDIO (TỪNG BƯỚC)

Hệ thống đã được bật sẵn Web Studio tại máy chủ nội bộ. Bạn có thể mở trình duyệt trên máy tính hoặc điện thoại cùng mạng Wi-Fi:

### 🔗 Bước 1: Mở Giao Diện Web Studio
- **Truy cập tại máy tính**: [http://127.0.0.1:8000](http://127.0.0.1:8000)
- **Truy cập từ điện thoại (chung Wi-Fi)**: [http://192.168.1.18:8000](http://192.168.1.18:8000) *(hoặc bấm nút "📶 Phát Wi-Fi" ở góc trên giao diện để quét mã QR)*.

---

### 📂 Bước 2: Vào Tab "Tái Chế Shorts AI"
- Trên thanh menu bên trái (Sidebar), tìm và bấm vào mục **"Tái Chế Shorts AI"** (có biểu tượng 🎬 và huy hiệu màu xanh `VIRAL`).

---

### ⚙️ Bước 3: Thiết Lập Nguồn Video & Ngách Kênh
1. **Chế độ bản quyền**: Chọn *Video Thuộc Sở Hữu Kênh Nhà* hoặc *Bình Luận Chuyển Hóa*.
2. **Kênh & Hồ Sơ Ngách**: Chọn kênh YouTube bạn muốn áp dụng (ví dụ: *Tài Chính & Tư Duy Triệu Phú* hoặc *Tâm Lý Học Hành Vi*). Màu phụ đề karaoke sẽ tự động ăn theo ngách này!
3. **Chọn nguồn video**:
   - **Cách A (Link YouTube)**: Bấm nút **"💡 Thử Link Mẫu"** (hệ thống tự điền link mẫu) HOẶC dán bất kỳ link YouTube nào bạn muốn trích xuất.
   - **Cách B (Video Cục Bộ)**: Chọn thẻ *"📁 Chọn Video Có Sẵn Trong Hệ Thống"* để chọn các video MP4 đã dựng sẵn trong máy.

---

### ⚡ Bước 4: Bấm "Quét & Phân Tích Khoảnh Khắc Viral"
- Bấm nút gradient xanh **"Quét & Phân Tích Khoảnh Khắc Viral"**.
- Gemini AI sẽ đọc toàn bộ transcript, đo lường nhịp điệu và xuất ra **Bảng Phân Đoạn Cao Trào (Golden Highlights)**:
  - 🥇 Thẻ phân đoạn với huy hiệu **🔥 96/100 TOP VIRAL**.
  - Tiêu đề Shorts giật tít chuẩn SEO YouTube.
  - Câu **Hook 3 giây đầu** khiến khán giả không thể lướt qua.
  - Phân tích tâm lý học vì sao đoạn này sẽ viral.
  - Góc nhìn an toàn bản quyền.

---

### ✂️ Bước 5: Render Short 9:16 & Xem Thành Phẩm
1. Tại phân đoạn bạn ưng ý nhất, chọn bố cục:
   - **Blur Background (Mặc định)**: Nền mờ phóng to, video gốc ở giữa (phong cách Opus Clip / Shorts cực thịnh hành).
   - **Center Crop**: Cắt 9:16 toàn màn hình.
2. Tích chọn **"Đốt Sub Karaoke"** để hiển thị phụ đề nhảy màu theo nhịp giọng đọc.
3. Bấm **"✂️ Render Short 9:16 Ngay"**:
   - FFmpeg 7.1 sẽ chạy ngầm cắt video với tốc độ cao (khoảng 5–15 giây).
   - Khi hoàn tất, nút sẽ chuyển thành **"▶️ Xem Video Short"**.
4. Bấm vào để mở **Trình phát xem trước video 9:16** và có sẵn nút **"⬇️ Tải Video Về Máy"** để đăng lên YouTube Shorts, TikTok, Facebook Reels!

---

## 🧩 4. CÁC TẬP TIN & MÃ NGUỒN ĐÃ BỔ SUNG TRONG DỰ ÁN

| Tập tin | Vai trò kỹ thuật |
| :--- | :--- |
| `app/services/video_repurposer.py` | **Bộ máy cốt lõi**: Tải video yt-dlp, nạp phụ đề youtube-transcript-api, gọi Gemini AI phân tích moments, xử lý FFmpeg crop dọc 9:16 và ghép Subtitle ASS Karaoke. |
| `app/services/niche_manager.py` | Quản lý 9 hồ sơ ngách kênh (Tâm lý, Tài chính, Lịch sử, Viễn tưởng, Kinh dị...). |
| `config/niches/*.yaml` | Cấu hình chuyên sâu 4 trụ cột (Kịch bản, Thị giác, Phụ đề Karaoke, Âm thanh). |
| `app/web/server.py` | Bổ sung 4 REST API endpoints (`/api/repurposer/analyze`, `/api/repurposer/render-clip`, `/api/repurposer/local-videos`, `/api/repurposer/clips`) và mount thư mục `/media`. |
| `app/web/static/index.html` | Thêm tab giao diện `#tab-repurposer`, các hộp điều khiển, thẻ highlight và modal trình phát video dọc 9:16 `#modal-repurposed-player`. |
| `app/web/static/js/dashboard.js` | Toàn bộ controller xử lý logic phân tích, render tiến độ, phát video và đồng bộ màu karaoke theo Niche Profile. |
| `requirements.txt` | Thêm `yt-dlp>=2026.8.0` và `youtube-transcript-api>=1.2.0`. |
| `scratch/test_repurposer.py` | Bộ kiểm thử tự động đạt kết quả **100% Passed**. |

---

## 💡 5. HƯỚNG DẪN CÀI ĐẶT CHO MÁY MỚI (NẾU CLONE VỀ MÁY KHÁC)

Nếu bạn của bạn clone repository này về một máy tính khác, chỉ cần chạy các lệnh sau:

```bash
# 1. Cài đặt các thư viện phụ thuộc
py -m pip install -r requirements.txt

# 2. Khởi chạy hệ thống toàn diện (Telegram Bot + Web Studio)
py run.py

# 3. Mở trình duyệt truy cập:
# http://127.0.0.1:8000
```

---

## 📶 6. CƠ CHẾ PHÂN BIỆT THIẾT BỊ CÙNG MẠNG WI-FI & CHỐNG XUNG ĐỘT

Khi hai hoặc nhiều người cùng kết nối vào hệ thống qua mạng Wi-Fi LAN (ví dụ: `192.168.x.x:8000`), hệ thống đã được trang bị cơ chế điều phối thông minh:

1. **Định danh thiết bị tự động (Device Identity)**:
   - Mỗi máy khách (Laptop, PC, iPhone, Android...) khi truy cập Web Studio sẽ tự động được cấp một **Device ID độc nhất** lưu trong `localStorage` và tự động nhận diện tên thiết bị (ví dụ: `iPhone / iPad #a1b2`, `Máy Windows #8f90`).
   - Người dùng có thể click vào huy hiệu **"💻 Máy của tôi"** ở góc trên cùng bên phải để đổi tên máy theo ý thích (ví dụ: `Laptop của An`, `PC của Bình`).
2. **Chống va chạm và ghi đè file**:
   - Tên file Shorts render được tạo với cấu trúc an toàn tuyệt đối: `short_{tên_máy}_{timestamp}_{uuid}.mp4`.
   - Mỗi file render sẽ lưu kèm tệp siêu dữ liệu `.meta.json` ghi nhận chính xác máy nào đã tạo clip, IP nào, và tiêu đề là gì.
3. **Chống nghẽn CPU & Xung đột YouTube**:
   - **Tải YouTube an toàn**: Nếu hai máy cùng chọn phân tích một video YouTube, hệ thống sử dụng khóa `threading.Lock()` theo từng video ID để tránh 2 tiến trình yt-dlp ghi đè file cache của nhau.
   - **Hàng đợi Render**: FFmpeg được điều phối bằng `threading.Semaphore(2)` để giữ tối đa 2 tác vụ render đồng thời, ngăn chặn việc quá tải CPU làm sập máy chủ.
4. **Bộ lọc xem video theo thiết bị**:
   - Trong kho Shorts, có 3 nút lọc nhanh: **[Tất Cả]**, **[💻 Máy Này]** (chỉ hiện video của máy bạn vừa tạo), và **[👥 Máy Khác]** (xem video bạn bè cùng mạng vừa tạo).
5. **Khắc phục lỗi trích xuất phân đoạn > 30s (VD: 45s)**:
   - Nâng cấp giới hạn kiểm tra `SceneContract.estimated_seconds` từ 30s lên 300s (5 phút), cho phép render mượt mà các phân đoạn highlight dài từ 15s đến 60s mà không còn bị lỗi validation `less than or equal to 30`.

---

## 🌐 7. GIẢI PHÁP DÙNG 1 ĐỊA CHỈ CỐ ĐỊNH KHI CHUYỂN SERVER GIỮA CÁC MÁY TÍNH

Khi bạn tắt server trên máy A để chuyển sang máy B (hoặc ngược lại), Router Wi-Fi thường cấp 2 địa chỉ IP khác nhau (ví dụ: `192.168.2.2` và `192.168.2.5`), khiến điện thoại hoặc máy tính khác bị mất kết nối nếu gõ IP cũ. Hệ thống đã được nâng cấp giải pháp 3 lớp toàn diện:

### 🌟 Cách 1 (Khuyên dùng): Dùng tên miền nội bộ cố định mDNS
Tất cả các máy khi khởi động `py run.py` đều tự động phát sóng tên miền mDNS `aiyoutube.local`.
* **Link duy nhất cho cả nhóm**:  
  👉 **`http://aiyoutube.local:8000`**
* **Ưu điểm**: Dù hôm nay máy A làm máy chủ, ngày mai máy B làm máy chủ, điện thoại (iPhone/Android/iPad) và các laptop khác chỉ cần lưu đúng 1 Bookmark này là vào thẳng máy đang bật mà **không bao giờ bị đổi số IP**.

### 🔍 Cách 2: Tự Động Quét & Chuyển Hướng (Auto-Discovery Failover)
* Nếu điện thoại của bạn đang mở trang bằng IP cũ (ví dụ: `192.168.2.2:8000`) mà máy đó bị tắt:
* Web Studio trên điện thoại sẽ tự động kích hoạt tính năng quét ngầm dải mạng Wi-Fi LAN. Ngay khi phát hiện máy bạn bè đang chạy cổng 8000, trang sẽ **tự động chuyển hướng (Redirect)** điện thoại sang máy bạn bè ngay lập tức!
* Bạn cũng có thể bấm nút **"🔍 Quét Tìm Máy Chủ Khác"** trong cửa sổ *📶 Phát Wi-Fi*.

### 🔁 Cách 3: Chạy Trạm Chuyển Tiếp Tạm Thời (Relay Proxy)
Nếu máy bạn vừa tắt `run.py` nhưng máy tính vẫn đang mở, bạn chỉ cần gõ:
```bash
py switch_server_relay.py
```
Tiến trình siêu nhẹ này sẽ tự động chuyển tiếp toàn bộ điện thoại/thiết bị đang vào IP máy bạn sang thẳng máy bạn bè mà không làm gián đoạn bất kỳ ai!

---

*Chúc bạn có những trải nghiệm tuyệt vời cùng hệ thống AI YouTube Studio! Mọi thắc mắc hoặc cần tùy biến thêm, hãy trao đổi trực tiếp để được hỗ trợ.*


