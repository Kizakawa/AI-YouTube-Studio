---
name: youtube-real-metrics-reporting
description: >-
  Quy chuẩn báo cáo và phân tích hiệu suất kênh/video YouTube 100% bằng số liệu thật trực tiếp từ
  YouTube Data API v3 và YouTube Analytics API v2 (tuyệt đối không dùng số liệu ước lượng/nội suy
  hoặc cộng dồn bản ghi cũ trong DB). Sử dụng skill này mỗi khi người dùng yêu cầu tóm tắt tình hình
  kênh, báo cáo lượt xem, kiểm tra video vừa đăng, hoặc đánh giá hiệu suất hệ thống.
---

# Quy Chuẩn Báo Cáo & Phân Tích Số Liệu Thật YouTube (`youtube-real-metrics-reporting`)

Mỗi khi người dùng yêu cầu **tóm tắt tình hình các video vừa đăng**, **báo cáo thống kê kênh**, hoặc **phân tích hiệu suất YouTube**, Agent **BẮT BUỘC** phải tuân thủ nghiêm ngặt quy trình 4 bước dưới đây.

---

## 1. Nguyên Tắc Bất Di Bất Dịch: 100% Số Liệu Thật (Zero Estimation Policy)

1. **Tuyệt đối KHÔNG dùng số liệu ước lượng / nội suy**:
   - Không bao giờ tự tính toán hoặc suy diễn các chỉ số giữ chân (`Average Percentage Viewed - APV %`, `Stayed to Watch / Viewed %`, `Swipe Away %`, `Average View Duration - AVD`) bằng công thức toán học từ số view/like/hook.
   - Chỉ sử dụng dữ liệu trả về trực tiếp từ:
     - **YouTube Data API v3** (`channels().list`, `playlistItems().list`, `videos().list`): Lấy `viewCount`, `likeCount`, `commentCount`, `subscriberCount`, `videoCount`, và lịch hẹn `publishAt` của các video `private`.
     - **YouTube Analytics API v2** (`youtubeAnalytics.reports().query` với `metrics="views,engagedViews,averageViewDuration,averageViewPercentage"`): Lấy `AVD (giây)`, `APV (%)`, `Stayed (%) = engagedViews / views * 100`, và `Swipe Away (%) = 100 - Stayed (%)`.
2. **Xử lý minh bạch độ trễ 24–48h của YouTube Analytics API v2**:
   - Đối với các video mới công chiếu dưới **24–48 giờ** chưa được Google chốt sổ trên YouTube Analytics API v2 (API trả về `None` / chưa có dòng dữ liệu):
     - **BẮT BUỘC** ghi rõ trong bảng báo cáo là: **`⏳ Chờ Analytics chốt sổ (24–48h)`** (hoặc ghi nhận theo ảnh chụp màn hình **YouTube Studio Realtime** nếu người dùng vừa cung cấp).
     - Không được lấy trung bình kênh hay số nội suy điền vào ô của video mới.
3. **Hiển thị song song Số Chính Xác (API v3) & Số Làm Tròn (YouTube Studio)**:
   - Giao diện YouTube Studio làm tròn lượt xem từ `1.000+` thành dạng `1.4K` (ví dụ `1.353 views` hiển thị là `1.4K`) và có thể nhanh hơn Data API v3 vài phút khi video đang cắn luồng Shorts Feed.
   - Trong bảng báo cáo, luôn hiển thị dạng: **`1.353` *(~1.4K Studio)*** để người dùng đối chiếu khớp ngay với màn hình YouTube Studio của họ.
4. **Không cộng dồn bản ghi rác từ Database nội bộ**:
   - Chỉ thống kê các video đang thực sự **`public`** trên kênh YouTube (và tách riêng bảng các video đang **`private` / `SCHEDULED`** chờ công chiếu), tuyệt đối không cộng gộp các bản ghi cũ đã xóa/ẩn trong bảng `PublishedVideo`.

---

## 2. Quy Trình Truy Vấn Trước Khi Báo Cáo

Trước khi trả lời người dùng, chạy truy vấn trực tiếp qua `PublisherAgent` (Data API v3 + Analytics API v2) hoặc chạy đồng bộ thực tế từ [`app/services/video_library.py`](../../app/services/video_library.py) / [`tools/standalone_auditor/run_audit.py`](../../tools/standalone_auditor/run_audit.py) để lấy:

1. **Tổng quan 7 kênh**:
   - Số video công khai (`public`), Tổng Views kênh (kèm tổng cộng dồn các video công khai nếu bộ đếm kênh của YouTube cập nhật trễ), Số người đăng ký (`Subs`), và Trung bình giữ chân 28 ngày (`AVD`, `APV %`, `Stayed %`, `Swipe Away %`) từ YouTube Analytics API v2.
2. **Danh sách các video vừa công chiếu gần nhất (24h – 72h qua)**:
   - Mã video (`video_id`), Tiêu đề, Giờ công chiếu (quy đổi sang **Giờ Việt Nam `UTC+7`**), Số giờ đã đăng (`age_hours`), Lượt xem thật (`views` + tốc độ `views/giờ`), Lượt thích (`likes` + tỷ lệ `Like/View %`), Bình luận (`comments`), và Chỉ số giữ chân thật từ Analytics API v2 (nếu đã chốt sổ).
3. **Danh sách các video đã tải lên và đang hẹn giờ (`SCHEDULED`)**:
   - Mã video (`video_id`), Kênh, Tiêu đề, Giờ hẹn công chiếu theo **Giờ Việt Nam (`UTC+7`)**.
4. **Các Job đang chờ trong Pipeline nội bộ (`VideoJob`)**:
   - Các Job ở trạng thái `RENDERED`, `RENDERING`, hoặc `FAILED` cần chú ý.

---

## 3. Cấu Trúc Nhận Xét & Phân Tích Chiến Lược (Bắt Buộc Có Trong Báo Cáo)

Sau khi trình bày bảng số liệu thật, **BẮT BUỘC** phải đưa ra phần nhận xét chuyên sâu dựa trên các con số thật đó theo 3 mục:

1. **🔥 Điểm sáng & Video đang cắn luồng đề xuất (Winners / Realtime Push)**:
   - Chỉ rõ video nào đang có tốc độ tăng trưởng (`views/h`) cao nhất, tỷ lệ tương tác (`Like/View % >= 3.0%`) tốt nhất, hoặc có chỉ số giữ chân thật (`APV %`, `Stayed %`) vượt trội.
   - Giải thích ngắn gọn **vì sao** video đó thắng dựa trên dữ liệu thật (ví dụ: cấu trúc đối đầu trực diện *A vs B*, con số vật lý cụ thể như `1.100 PSI`, `93°C`, `1883 Krakatoa`).
2. **📈 Nhóm đang phục hồi / Giữ nhịp ổn định**:
   - So sánh các video mới đăng với các video liền trước của chính kênh đó (ví dụ: kênh vừa thoát khỏi chuỗi view thấp 10–30 views lên 168–362 views).
3. **⚠️ Cảnh báo & Đề xuất hành động dựa trên dữ liệu thật**:
   - Chỉ ra các kênh/video có tỷ lệ vuốt bỏ thật (`Swipe Away %`) đã chốt sổ còn cao (`> 55%`) hoặc bị kẹt luồng phân phối (`0–20 views` sau >24h), kèm đề xuất điều chỉnh cụ thể cho đợt sản xuất tiếp theo.
