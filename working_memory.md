# ⚡ working_memory.md — Bộ Nhớ Trạng Thái Hiện Tại ("RAM Ngoài" Của Agent)

> **Nguyên tắc vận hành**:
> - Đọc file này **đầu tiên** (cùng với [`MEMORY.md`](file:///d:/Python/Youtube/MEMORY.md) và [`todo.md`](file:///d:/Python/Youtube/todo.md)) khi bắt đầu bất kỳ phiên làm việc hoặc yêu cầu mới nào.
> - Cuối mỗi tác vụ (trước khi `git commit & push`), **Main Agent BẮT BUỘC cập nhật lại file này** theo đúng 4 mục: `✅ Đã làm gì`, `❗ Đang kẹt / Lưu ý gì`, `📂 File vừa xử lý`, `➡️ Bước tiếp theo`.

---

## 📌 Snapshot Trạng Thái Hiện Tại (Cập nhật: `2026-11-11 22:42 +07:00`)

- **Nhánh Git hiện tại**: `main` (đồng bộ sạch với `origin/main`).
- **Tác vụ vừa hoàn thành**:
  - `TASK-022`: **Hoàn thiện toàn diện hệ thống sản xuất Video Dài 10 phút (`16:9` — 10 Chương × 4 Cảnh = 40 Cảnh, `>= 9m15s` Mid-Roll Ads) và kiểm chứng End-to-End thực tế đạt chuẩn xuất sắc (`Job #806` trên `Odisseia Selvagem`: `10m14s / 614.37s`, `ScriptCritic 9.2/10`, `VisualCritic 9.5/10` — `40/40` cảnh Pass `100%`, `VideoQA 98/100`, `Sharpness 42.56`, `AV Drift 19.9ms`, `0` cảnh trùng lặp, `0` chồng chữ phụ đề ở mốc `25%/50%/75%`)**.
  - `TASK-021`: Sửa trực tiếp trên kênh `Wild Odyssey` (`gpNPd09SHd8`, `QEkELKDcVYY` $\rightarrow$ `9mu7upNH-Mo`, `iTVOTK0IKso`), chặn tận gốc lỗi `P0D` Stub Recovery & Tiêu Đề Placeholder trong `PublisherAgent`.

---

## ✅ 1. Đã Làm Gì (Completed In Recent Turns)
1. **Hoàn thiện toàn diện kiến trúc sản xuất Phim Tài Liệu Dài 10 Phút (`16:9` — `TASK-022`)**:
   - **Kiến trúc 10 Chương × 4 Cảnh = 40 Cảnh (`>= 9m15s` Mid-Roll Ads) & 100% Bản Địa Hóa (`pt-BR` / `en-US`)**:
     - Nâng cấp [`app/agents/content/longform_10min_generator.py`](file:///d:/Python/Youtube/app/agents/content/longform_10min_generator.py), [`app/agents/content/script_json_parser_mixin.py`](file:///d:/Python/Youtube/app/agents/content/script_json_parser_mixin.py), [`app/services/intelligence/deduplication.py`](file:///d:/Python/Youtube/app/services/intelligence/deduplication.py), [`app/services/batch_producer.py`](file:///d:/Python/Youtube/app/services/batch_producer.py) và bổ sung chủ đề Long-Form tiếng Bồ Đào Nha bản địa vào [`config/niches/wildlife_predators.yaml`](file:///d:/Python/Youtube/config/niches/wildlife_predators.yaml) & [`config/niches/deep_sea_earth_mysteries.yaml`](file:///d:/Python/Youtube/config/niches/deep_sea_earth_mysteries.yaml).
     - Chặn vĩnh viễn lỗi ghép nhầm tiền tố chủ đề tiếng Anh vào tiêu đề `pt-BR`, khóa tiêu đề `< 58` ký tự không treo giới từ/mạo từ cuối câu, và đồng bộ duy nhất 1 khối `⏱️ Capítulos:` khớp chính xác từng giây theo `audio_duration_ms` thực tế của TTS (giữ nguyên tên ghép có gạch nối như `Jacaré-Açu`, `Onça-Pintada`).
   - **Chống Trùng Lặp Thị Giác Tuyệt Đối Giữa 40 Cảnh, Safe-Crop Bilibili 16:9 Tốc Độ Cao & Diệt Tận Gốc `_graphic.png`**:
     - [`app/services/media/bilibili_fetcher.py`](file:///d:/Python/Youtube/app/services/media/bilibili_fetcher.py) & [`app/services/media/longform_chapter_pool.py`](file:///d:/Python/Youtube/app/services/media/longform_chapter_pool.py): Gọt sạch `12.5%` mép trên (logo góc) + `13.5%` mép dưới (phụ đề) ngay trong 1 lượt `bicubic` 4 luồng trên file tạm `_staging_*.mp4` trước khi `os.replace` sang `*_master.mp4`, triệt tiêu lỗi `FFmpeg Safe-Crop timed out after 45s` và `[WinError 32]`.
     - [`app/services/media/longform_chapter_pool.py`](file:///d:/Python/Youtube/app/services/media/longform_chapter_pool.py), [`app/services/video_memory_rag.py`](file:///d:/Python/Youtube/app/services/video_memory_rag.py), [`app/graph/nodes/render_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/render_nodes.py) & [`app/agents/video_agent.py`](file:///d:/Python/Youtube/app/agents/video_agent.py): Bổ sung `sync_and_detect_duplicate_or_missing_scenes()`, `clear_scene_commitments()`, ghi tạm `stage_pre_resc` an toàn và cổng khóa chống trùng lặp ngay trước FFmpeg Render.
   - **Chất Lượng Hình Ảnh True-1080p, Triệt Tiêu Chồng Lấp Phụ Đề Trên `AutoThumbBanner` & Lịch Vàng Long-Form**:
     - [`app/utils/subtitle_helper.py`](file:///d:/Python/Youtube/app/utils/subtitle_helper.py): Tách khoảng thời gian phụ đề `Karaoke` quanh cửa sổ `AutoThumbBanner` (`±1400ms` tại `25%`, `50%`, `75%`), đảm bảo `0%` chồng chữ trên ảnh đại diện `2.jpg`.
     - [`app/utils/ffmpeg/core_encoder.py`](file:///d:/Python/Youtube/app/utils/ffmpeg/core_encoder.py) & [`app/services/smart_scheduler.py`](file:///d:/Python/Youtube/app/services/smart_scheduler.py): Nâng bitrate Long-Form lên `3.8M` target (`maxrate 5.2M`, đạt `3.64–4.74 Mbps` thực tế) và khóa Khung Giờ Vàng riêng cho Long-Form Mỹ (`21:30–22:30 UTC`) & Brazil (`23:30 UTC` cho `Odisseia Selvagem`, `00:35 UTC` cho `Mistérios do Mundo`, cách Shorts `>= 3.0h`).
2. **Kiểm chứng End-to-End thực tế (`Job #806` — `Odisseia Selvagem`, đang chờ duyệt `AWAITING_APPROVAL`)**:
   - Tiêu đề: **`O Que Acontece Se Você Cair no Rio Amazonas Agora?`** (`50` ký tự, 100% `pt-BR`).
   - Điểm số 5 Grader trên LangSmith Cloud: `ScriptCritic = 9.2/10`, **`VisualCritic = 9.5/10` (`40/40` cảnh đạt chuẩn, `failed_scene_ids = []`)**, `HookGrader = 9.57/10`, `AudioQA = 10.0/10`, **`VideoQA = 98/100`** (`1920x1080 @ 30fps`, `614.37s = 10m14s`, `279.47 MB / 3.64 Mbps`, `AV Drift = 19.9ms`, `Sharpness = 42.56`).
   - Đã tự động sao lưu lên Google Drive (`https://drive.google.com/file/d/1KfP09nVn02E4TkvGoIEBSFYM5kRbbCHI/view?usp=drivesdk`) và gửi bản xem trước + Thumbnail `16:9` qua Telegram.

---

## ❗ 2. Đang Kẹt Ở Đâu / Lưu Ý Quan Trọng (Active Watchpoints & Constraints)
- **`Job #806` (`storage/outputs/video_806.mp4` — `10m14s`) đã sẵn sàng ở trạng thái `AWAITING_APPROVAL`**: Có thể upload & hẹn giờ ngay vào Khung Giờ Vàng tối Brazil (`23:30 UTC` / `06:30 Sáng Giờ VN`) ngay khi người dùng xác nhận đăng.
- **Hàng đợi Shorts trên 2 kênh Brazil (`Odisseia Selvagem` & `Mistérios do Mundo`)**: Cần nạp tiếp đợt Shorts mới và gắn link dẫn phễu sang Phim Tài Liệu Dài `#806` để kéo tối đa `3,000h / 4,000h` giờ xem công khai bật YPP.
- Lưu ý OAuth: Chuyển OAuth Consent Screen của 3 dự án Google Cloud từ `Testing` sang `In production` để tránh hết hạn token 7 ngày.

---

## 📂 3. Các File Vừa Xử Lý (Recently Modified Files)
- [`app/agents/content/longform_10min_generator.py`](file:///d:/Python/Youtube/app/agents/content/longform_10min_generator.py)
- [`app/agents/content/script_json_parser_mixin.py`](file:///d:/Python/Youtube/app/agents/content/script_json_parser_mixin.py)
- [`app/agents/publisher/seo_tags_mixin.py`](file:///d:/Python/Youtube/app/agents/publisher/seo_tags_mixin.py)
- [`app/agents/video_agent.py`](file:///d:/Python/Youtube/app/agents/video_agent.py)
- [`app/graph/nodes/media/fetch_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/fetch_media_node.py)
- [`app/graph/nodes/media/refine_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/refine_media_node.py)
- [`app/graph/nodes/render_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/render_nodes.py)
- [`app/services/batch_producer.py`](file:///d:/Python/Youtube/app/services/batch_producer.py)
- [`app/services/intelligence/deduplication.py`](file:///d:/Python/Youtube/app/services/intelligence/deduplication.py)
- [`app/services/media/bilibili_fetcher.py`](file:///d:/Python/Youtube/app/services/media/bilibili_fetcher.py)
- [`app/services/media/longform_chapter_pool.py`](file:///d:/Python/Youtube/app/services/media/longform_chapter_pool.py)
- [`app/services/smart_scheduler.py`](file:///d:/Python/Youtube/app/services/smart_scheduler.py)
- [`app/services/video_memory_rag.py`](file:///d:/Python/Youtube/app/services/video_memory_rag.py)
- [`app/utils/ffmpeg/core_encoder.py`](file:///d:/Python/Youtube/app/utils/ffmpeg/core_encoder.py)
- [`app/utils/subtitle_helper.py`](file:///d:/Python/Youtube/app/utils/subtitle_helper.py)
- [`config/niches/wildlife_predators.yaml`](file:///d:/Python/Youtube/config/niches/wildlife_predators.yaml) & [`config/niches/deep_sea_earth_mysteries.yaml`](file:///d:/Python/Youtube/config/niches/deep_sea_earth_mysteries.yaml)

---

## ➡️ 4. Bước Tiếp Theo (Immediate Next Steps)
1. Theo xác nhận của người dùng: Phát hành ngay `Job #806` (`O Que Acontece Se Você Cair no Rio Amazonas Agora?`, `10m14s`) lên **`Odisseia Selvagem`** ở Khung Giờ Vàng Brazil `23:30 UTC` và sản xuất tiếp Phim Tài Liệu Dài `10 phút` cho **`Mistérios do Mundo`** (`00:35 UTC`) + các kênh Mỹ (`21:30–22:30 UTC`).
2. Lên lịch bổ sung lô Shorts tiếp theo cho 6 kênh và tự động gắn link dẫn phễu (`🎬 Assista ao Documentário Completo` / `🎬 Watch the Full Documentary`) trỏ trực tiếp về các video dài mới để tăng tốc cán mốc `3,000h / 4,000h` YPP.
