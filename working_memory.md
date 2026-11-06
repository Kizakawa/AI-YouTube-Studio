# ⚡ working_memory.md — Bộ Nhớ Trạng Thái Hiện Tại ("RAM Ngoài" Của Agent)

> **Nguyên tắc vận hành**:
> - Đọc file này **đầu tiên** (cùng với [`MEMORY.md`](file:///d:/Python/Youtube/MEMORY.md) và [`todo.md`](file:///d:/Python/Youtube/todo.md)) khi bắt đầu bất kỳ phiên làm việc hoặc yêu cầu mới nào.
> - Cuối mỗi tác vụ (trước khi `git commit & push`), **Main Agent BẮT BUỘC cập nhật lại file này** theo đúng 4 mục: `✅ Đã làm gì`, `❗ Đang kẹt / Lưu ý gì`, `📂 File vừa xử lý`, `➡️ Bước tiếp theo`.

---

## 📌 Snapshot Trạng Thái Hiện Tại (Cập nhật: `2026-11-06 23:12 +07:00`)

- **Nhánh Git hiện tại**: `main` (đồng bộ sạch với `origin/main`).
- **Tác vụ vừa hoàn thành**:
  - `TASK-012`: **Tổng hợp 100% các kế hoạch tối ưu và Sổ Đen 14 Lỗi Cấm Lặp Lại từ toàn bộ các kênh chat song song** (`861ab3b5`, `40c844d4`, `7cfb8e13`, `1b893567`, `b6af5c48`, `a9b608a3`) vào hệ thống Bộ Nhớ Ngoài ([`AGENTS.md`](file:///d:/Python/Youtube/AGENTS.md), [`MEMORY.md`](file:///d:/Python/Youtube/MEMORY.md), [`working_memory.md`](file:///d:/Python/Youtube/working_memory.md), [`todo.md`](file:///d:/Python/Youtube/todo.md), [`progress_log.md`](file:///d:/Python/Youtube/progress_log.md)).
  - Đồng thời kiểm định (`py_compile`) và hợp nhất sạch sẽ 3 bản sửa lỗi từ phiên chat song song vào nhánh `main`:
    1. [`app/graph/nodes/render_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/render_nodes.py): Cứu hộ trực tiếp từ file video gốc (`.mp4`) trước khi render để diệt tận gốc thẻ xanh `_graphic.png` và kiểm định chuyển động thật (`verify_hook_clip_genuine_motion`) cho Cảnh 1 & 2.
    2. [`app/services/intelligence/deduplication.py`](file:///d:/Python/Youtube/app/services/intelligence/deduplication.py) & [`app/services/video_library.py`](file:///d:/Python/Youtube/app/services/video_library.py): Lọc bỏ `#hashtags` (`#shorts`, `#humanbehavior`) trước khi tính độ trùng lặp chủ đề và yêu cầu khớp cả 2 vế `A vs B` khi kiểm tra tiền tố.

---

## ✅ 1. Đã Làm Gì (Completed Across All Recent Sessions)
1. **Tổng hợp "Sổ Đen 14 Lỗi Cấm Lặp Lại" từ tất cả các phiên chat vào [`MEMORY.md`](file:///d:/Python/Youtube/MEMORY.md) và [`AGENTS.md`](file:///d:/Python/Youtube/AGENTS.md)**:
   - Khóa cứng luật `> 85%` thời lượng Shorts là video chuyển động thật (`5–7` clip `.mp4`) + **0% thẻ xanh `_graphic.png`** (`Pre-Render Zero-Graphic Rescue` + chặn tái sử dụng checkpoint `fetch_media` có `_graphic.png`).
   - Khóa cứng **100% YAML Single Source of Truth** (`config/niches/*.yaml` qua `niche_manager.py`; cấm hardcode `cfg["topics"]` hay `cfg["director_notes"]` trong `batch_producer.py`).
   - Khóa cứng **Zero Disguised Keyword/Regex Lists in Python** (`5a79a8c`) + **Script-LLM Scene Search Authority** (`SceneContract.subject_entity` + `search_queries`).
   - Khóa cứng **Hai Trụ Cột Viral Ngang Hàng**: `pillar_1_direct_matchup` (`Entity A vs Entity B`) & `pillar_2_why_mystery_explainer` (`Why / Por Que / How [Thực Thể/Địa Danh] [Bí Ẩn Ngược Đời]`).
   - Khóa cứng kiến trúc **Long-Form `16:9` 10 Chương / 30–40 Cảnh (`>= 9m15s` / `LONGFORM_MIDROLL_FLOOR_MS = 555_000`)** mở khóa quảng cáo giữa giờ `>= 08:00`, khung giờ vàng Long-Form Mỹ **`21:30–22:30 UTC`**, và tự động gắn link Long-Form vào bình luận Shorts cùng kênh (`8900842`).
   - Khóa cứng **3-Frame Gemini Vision trên mọi nguồn video (kể cả Pexels `dd3d124`)**, `Subtitle-Safe Bilibili Crop`, cấm thanh tiêu đề vàng trên đỉnh video ở `0.0s`, cấm cờ quốc gia/thời đại sai lệch (`< 4.0`), và chuyển trạng thái **`PRIVATE`** thay vì xóa hàng loạt video lỗi.
2. **Hợp nhất đầy đủ các kế hoạch từ mọi phiên chat vào [`todo.md`](file:///d:/Python/Youtube/todo.md)**:
   - Đã ghi nhận đầy đủ 12 hạng mục lớn đã hoàn thành (`TASK-001` $\rightarrow$ `TASK-012`) và 6 kế hoạch sẵn sàng/đang theo dõi (`TASK-013` $\rightarrow$ `TASK-018`: Kiểm định Video `"Why..."` End-to-End, Theo dõi hiệu suất & Cross-Promo Long-Form 10 phút, Nâng cấp NVIDIA Driver `>= 610.0` mở khóa `h264_nvenc`, Tối ưu tốc độ render Long-Form từ `26p` xuống `12–15p`, Cấp lại OAuth cho kênh `Wild Odyssey` khi cần, và Kiểm toán định kỳ 14 ngày).

---

## ❗ 2. Đang Kẹt Ở Đâu / Lưu Ý Quan Trọng (Active Watchpoints & Constraints)
- **Không có lỗi code tồn đọng (Zero Code Blockers)**: Toàn bộ file Python và hệ thống Bộ Nhớ Ngoài đã đồng bộ sạch sẽ trên nhánh `main`.
- **3 Lưu ý vận hành từ các phiên chat song song cần nhớ**:
  1. **Driver GPU trên máy trạm**: Hiện NVIDIA Driver đang ở bản `581.29` (FFmpeg `h264_nvenc` SDK 13.1 yêu cầu `>= 610.00`), nên hệ thống đang tự động chuyển sang **`h264_qsv` (Intel QuickSync GPU)** để giữ tốc độ render nhanh mà không bị lỗi tạo tiến trình giả `nvenc` (`b45b567`). Khi người dùng cập nhật NVIDIA Driver lên `>= 610.00`, `h264_nvenc` trên RTX 3050 sẽ tự động kích hoạt trở lại.
  2. **OAuth Token kênh `Wild Odyssey` (`ch_wild_odyssey`)**: Các kênh khác (`Mysteries`, `Psychology`, `Alternate History`, `Paws & Hearts`, `Odisseia`, `Mistérios`) đều đang hoạt động OAuth bình thường; riêng token `account_1_wild_odyssey_token.pickle` nếu báo `invalid_grant` khi chạy batch upload thì cần làm mới OAuth trước khi đẩy lịch kênh này.
  3. **Bất biến chống lặp lỗi**: Luôn đọc Bảng 14 Lỗi Cấm Lặp Lại trong [`MEMORY.md`](file:///d:/Python/Youtube/MEMORY.md) trước khi sửa bất kỳ dòng code hay prompt nào.

---

## 📂 3. Các File Vừa Xử Lý (Recently Modified Files)
- [`AGENTS.md`](file:///d:/Python/Youtube/AGENTS.md)
- [`MEMORY.md`](file:///d:/Python/Youtube/MEMORY.md)
- [`working_memory.md`](file:///d:/Python/Youtube/working_memory.md)
- [`todo.md`](file:///d:/Python/Youtube/todo.md)
- [`progress_log.md`](file:///d:/Python/Youtube/progress_log.md)
- [`app/graph/nodes/render_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/render_nodes.py)
- [`app/services/intelligence/deduplication.py`](file:///d:/Python/Youtube/app/services/intelligence/deduplication.py)
- [`app/services/video_library.py`](file:///d:/Python/Youtube/app/services/video_library.py)

---

## ➡️ 4. Bước Tiếp Theo (Immediate Next Steps)
1. Khi bắt đầu yêu cầu mới từ người dùng: chỉ cần đọc [`MEMORY.md`](file:///d:/Python/Youtube/MEMORY.md) + [`working_memory.md`](file:///d:/Python/Youtube/working_memory.md) + [`todo.md`](file:///d:/Python/Youtube/todo.md) trong `< 5s`.
2. Sẵn sàng thực thi ngay bất kỳ tác vụ nào trong Backlog [`todo.md`](file:///d:/Python/Youtube/todo.md) (`TASK-013` $\rightarrow$ `TASK-018`) theo yêu cầu của người dùng mà không bao giờ lặp lại 14 lỗi cũ.
