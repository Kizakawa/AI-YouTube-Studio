# ⚡ working_memory.md — Bộ Nhớ Trạng Thái Hiện Tại ("RAM Ngoài" Của Agent)

> **Nguyên tắc vận hành**:
> - Đọc file này **đầu tiên** (cùng với [`MEMORY.md`](file:///d:/Python/Youtube/MEMORY.md) và [`todo.md`](file:///d:/Python/Youtube/todo.md)) khi bắt đầu bất kỳ phiên làm việc hoặc yêu cầu mới nào.
> - Cuối mỗi tác vụ (trước khi `git commit & push`), **Main Agent BẮT BUỘC cập nhật lại file này** theo đúng 4 mục: `✅ Đã làm gì`, `❗ Đang kẹt / Lưu ý gì`, `📂 File vừa xử lý`, `➡️ Bước tiếp theo`.

---

## 📌 Snapshot Trạng Thái Hiện Tại (Cập nhật: `2026-11-11 15:40 +07:00`)

- **Nhánh Git hiện tại**: `main` (đồng bộ sạch với `origin/main`).
- **Tác vụ vừa hoàn thành**:
  - `TASK-021`: **Sửa trực tiếp trên kênh `Wild Odyssey` (`gpNPd09SHd8`, `QEkELKDcVYY` $\rightarrow$ `9mu7upNH-Mo`, `iTVOTK0IKso`), chặn tận gốc lỗi `P0D` Stub Recovery & Tiêu Đề Placeholder (`Video #735` $\rightarrow$ `Video #Shorts`) trong `PublisherAgent`, và kiểm toán toàn bộ số liệu YPP 90 ngày / 365 ngày trên 6 kênh**.
  - **Số liệu thực tế mới nhất (`15:25 — 11/11/2026 VN`)**:
    - 5/5 video mở `PUBLIC` từ lô `#786`–`#791` đạt `1,150 – 1,568 views` (`Odisseia #786`: `1,568 views`, `42 likes`; `Odisseia #787`: `1,252 views`; `Mistérios #790`: `1,227 views`; `Mysteries EN #788`: `1,155 views`; `Mistérios #791`: `1,150 views`).
    - Kênh tiến sát YPP nhất: **`Odisseia Selvagem`** (`223/500 Subs` — `44.6%` Tier-1 YPP, `153,020 views / 90d`, `635.6h` tổng giờ xem, `95.5% Brazil`) và **`Mistérios do Mundo`** (`87/500 Subs`, `57,139 views / 90d`, `97.4% Brazil`).

---

## ✅ 1. Đã Làm Gì (Completed In Recent Turns)
1. **Xử lý triệt để các video lỗi trực tiếp trên kênh `Wild Odyssey` qua YouTube Data API v3**:
   - **`gpNPd09SHd8` (`Job #735`, `PT47S`, đang `PUBLIC` với `133 views`)**: Sửa trực tiếp tiêu đề lỗi `"Video #Shorts #wildlife #animals #nature"` thành **`Harpy Eagle Canopy Raid: Sloth Claws vs 530-PSI Talons 🦅🦥 #Shorts #harpyeagle #wildlife`** và gắn link dẫn phễu sang phim tài liệu dài `8m57s` cùng chủ đề (`https://youtu.be/iTVOTK0IKso`).
   - **`QEkELKDcVYY` $\rightarrow$ `9mu7upNH-Mo` (`Job #785` — *Giant Anteater vs Jaguar*)**: Phát hiện `QEkELKDcVYY` bị kẹt `0 views` do đứt mạng giữa chừng tạo stub `duration=P0D`; đã cách ly `QEkELKDcVYY` sang `PRIVATE` (`[PRIVATE-P0D] ...`), tải lại bản chuẩn `40.07s` (`PT41S`, `status=processed`) lên ID mới **`9mu7upNH-Mo`** hẹn đúng Khung Giờ Vàng Mỹ `2026-11-11T18:15:00Z` (`01:15 Sáng T5, 12/11/2026 VN` — cách `jyGdt27Xwtg` đúng `8h 45m`).
   - **`iTVOTK0IKso` (Phim tài liệu dài `8m57s` trên `Wild Odyssey`)**: Mở khóa `embeddable=True` và `publicStatsViewable=True` để giải phóng phân phối đề xuất.
2. **Sửa tận gốc 2 lỗi kiến trúc trong `PublisherAgent` (chống tái diễn trên toàn hệ thống)**:
   - [`app/agents/publisher/oauth_and_upload_mixin.py`](file:///d:/Python/Youtube/app/agents/publisher/oauth_and_upload_mixin.py): Bổ sung `_is_live_youtube_video_playable()` và `_quarantine_broken_p0d_stub()` kiểm tra `contentDetails.duration != 'P0D'` và `processingDetails` trong `_find_live_duplicate_on_youtube` & vòng đối chiếu DB (`VideoJob` / `PublishedVideo`), chặn tuyệt đối việc `UploadRetryRecovery` nhận nhầm stub rỗng `P0D` sau khi đứt mạng SSL là bản đã tải xong.
   - [`app/agents/publisher/seo_tags_mixin.py`](file:///d:/Python/Youtube/app/agents/publisher/seo_tags_mixin.py) & [`app/agents/publisher/oauth_and_upload_mixin.py`](file:///d:/Python/Youtube/app/agents/publisher/oauth_and_upload_mixin.py): Sửa regex cắt hashtag cuối câu chỉ cắt `#[a-zA-Z...]` (không cắt nhầm `#735` hay `O #1`), đồng thời thêm `is_placeholder_video_title()` tự động khôi phục tiêu đề thực thể từ `script`/`topic` khi đầu vào là `'Video #735'` / `'Video'` / `'Shorts Video'`.

---

## ❗ 2. Đang Kẹt Ở Đâu / Lưu Ý Quan Trọng (Active Watchpoints & Constraints)
- **Hàng đợi hẹn giờ trên 2 kênh chủ lực Brazil (`Odisseia Selvagem` & `Mistérios do Mundo`) hiện bằng `0`**: Cả 4 video hôm qua đã mở `PUBLIC` hết (`1.15K–1.57K views`), cần lên lịch ngay đợt Shorts tiếp theo + khởi động Phim tài liệu dài `>= 9m15s` tiếng Bồ Đào Nha (`pt-BR`) cho `Odisseia Selvagem` để gom đủ `3,000h / 4,000h` giờ xem công khai bật YPP (vì giờ xem từ Shorts Feed không được YouTube tính vào mốc `3,000h / 4,000h`).
- Lưu ý OAuth: Chuyển OAuth Consent Screen của 3 dự án Google Cloud từ `Testing` sang `In production` để không phải cấp lại token sau mỗi 7 ngày.

---

## 📂 3. Các File Vừa Xử Lý (Recently Modified Files)
- [`app/agents/publisher/oauth_and_upload_mixin.py`](file:///d:/Python/Youtube/app/agents/publisher/oauth_and_upload_mixin.py)
- [`app/agents/publisher/seo_tags_mixin.py`](file:///d:/Python/Youtube/app/agents/publisher/seo_tags_mixin.py)
- [`working_memory.md`](file:///d:/Python/Youtube/working_memory.md)
- [`todo.md`](file:///d:/Python/Youtube/todo.md)
- [`progress_log.md`](file:///d:/Python/Youtube/progress_log.md)

---

## ➡️ 4. Bước Tiếp Theo (Immediate Next Steps)
1. Sản xuất ngay Phim tài liệu dài `10 phút` (`>= 9m15s`, `16:9`) + nạp hàng đợi 2 Shorts/ngày cho **`Odisseia Selvagem`** (`223/500 Subs`) và **`Mistérios do Mundo`** (`87/500 Subs`) để rút ngắn tối đa thời gian chạm mốc YPP (`500 Subs + 3,000h` và `1,000 Subs + 4,000h`).
2. Đồng bộ bình luận ghim + link dẫn sang Long-Form cho 6 video hẹn giờ tối nay/sáng mai (`9mu7upNH-Mo`, `jyGdt27Xwtg`, `Bqa3zoWwjyU`, `6mvhSZds13U`, `bFmlknLSTlU`, `e7nfVRO_FRc`, `9mPfUnGHx0c`) ngay khi chuyển sang `PUBLIC`.
