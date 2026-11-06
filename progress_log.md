# 📜 progress_log.md — Nhật Ký Tiến Độ Dự Án (Append-Only Log)

> **Quy tắc bất biến**: File này là nhật ký **chỉ ghi nối thêm (`Append-Only`)**. Tuyệt đối KHÔNG xóa hoặc sửa lịch sử cũ. Cuối mỗi task hoàn thành, nối thêm 1 mục mới ở dưới cùng kèm thời gian, mã `TASK-xxx`, mã commit Git và kết quả kiểm chứng.

---

- **`[2026-11-05 | Commit 066fd6f]` — `TASK-001` & `TASK-002`**:
  - Mở rộng `SceneContract` với `subject_entity` và `search_queries` do chính LLM viết kịch bản sinh ra cho từng cảnh.
  - Khóa cứng luật `> 85%` thời lượng Shorts phải là video chuyển động thật (`5–7` clip `.mp4`); kiểm chứng trên `Job #764` (*Komodo Dragon vs Water Buffalo*) đạt `7/7` cảnh video chạy thật (`100%`), `VisualCritic 8.9/10`.

- **`[2026-11-06 | Commit 5a79a8c]` — `TASK-003`**:
  - Thực thi chính sách `Zero Disguised Keyword/Regex Lists in Python`: Gỡ bỏ toàn bộ các regex danh sách từ khóa cứng trong `script_critic.py`, `content_linter.py`, `script_json_parser_mixin.py` và chuyển 100% quyền kiểm soát ngữ nghĩa/tìm kiếm về LLM System Prompts + `config/niches/*.yaml`.

- **`[2026-11-06 | Commits b3ee1e2 & c502f65]` — `TASK-004`**:
  - Xây dựng mới `HotChannelScriptBenchmarkEvaluator` (`app/graph/evaluators/hot_channel_script_benchmark.py`), `LLMToolCallCritic` (`app/graph/evaluators/llm_tool_call_critic.py`), node LangGraph `evaluate_tool_calls` (`app/graph/nodes/media/evaluate_tool_calls_node.py`) và CLI `app/utils/llm_node_inspector.py`.
  - Kiểm chứng thành công khả năng chấm điểm kịch bản so với kênh Hot Hit triệu view và tự động `AUTO_HEAL` lệnh gọi `@tool` bị lệch chủ thể (Cảnh 5 *Secretary Bird vs Black Mamba* đạt `9.5/10 Tool-Call Audit`).

- **`[2026-11-06 | Commits a1f0afb & 7bc498c]` — `TASK-005`**:
  - Chuẩn hóa **Hai Trụ Cột Viral Ngang Hàng (`Dual-Pillar Viral Architecture`)**: Bổ sung `pillar_2_why_mystery_explainer` (dạng video giải thích bí ẩn `"Why / Por Que / Tại Sao / How"` kiểu *Zack D. Films / Você Sabia?*) song hành cùng `pillar_1_direct_matchup` (`Entity A vs Entity B`).
  - Kiểm chứng thực tế trên chủ đề `"Why Deep-Sea Sharks Paralyze Inside This Underwater Brine Lake"` đạt `8.9/10 Hot Benchmark` (`9.5/10` Title) và `9.5/10 Tool-Call Audit` (`6/6` cảnh = `100%` Video).

- **`[2026-11-06 | External Memory System Setup]` — `TASK-006`**:
  - Tổ chức Workspace thành **Bộ Nhớ Ngoài (`External Memory`)** gồm `AGENTS.md` (Bộ điều phối), `MEMORY.md` (Bản đồ kiến trúc & 6 Bất biến chống lặp lỗi), `working_memory.md` (RAM trạng thái hiện tại), `todo.md` (Danh sách task có checkbox & DoD) và `progress_log.md` (Nhật ký append-only).

- **`[2026-11-06 | Commits b45b567, 4faaa33, c61ec52, c2e0b22, 3d19310, 8900842, 3d51ac9]` — `TASK-007` to `TASK-011` (Parallel Sessions Consolidation)**:
  - Chuyển đổi 100% cấu hình ngách và kho chủ đề về `config/niches/*.yaml` qua `niche_manager.py` (xóa bỏ hoàn toàn `topics` và `director_notes` cứng trong `batch_producer.py`).
  - Nâng cấp luồng Long-Form `16:9` lên 10 Chương / 30–40 Cảnh (`>= 9m15s` Mid-Roll Ads) dùng single-pass `-f concat`, lên lịch 2 phim tài liệu 10 phút (`iTVOTK0IKso`, `L-5xCei7MDU`) ở khung `22:30 UTC`, và tự động gắn link Long-Form vào bình luận Shorts (`8900842`).
  - Bắt buộc kiểm định 3-Frame Gemini Vision trên cả nhánh Pexels (`dd3d124`), sửa lỗi header Contact-Sheet che khung hình (`3d19310`), kích hoạt GPU `h264_qsv` fallback + `KeyRotator` 30s/30m isolation (`b45b567`, `4faaa33`).

- **`[2026-11-06 | Cross-Session Master Synthesis]` — `TASK-012`**:
  - Tổng hợp toàn bộ các kế hoạch và **Sổ Đen 14 Lỗi Cấm Lặp Lại** từ tất cả các kênh chat (`861ab3b5`, `40c844d4`, `7cfb8e13`, `1b893567`, `b6af5c48`, `a9b608a3`) vào hệ thống Bộ Nhớ Ngoài (`AGENTS.md`, `MEMORY.md`, `working_memory.md`, `todo.md`, `progress_log.md`).
  - Hợp nhất và kiểm định cú pháp (`py_compile`) cho `render_nodes.py` (Pre-Render Zero-Graphic Rescue & Hook motion verification), `deduplication.py` và `video_library.py` (lọc `#hashtags` và so khớp 2 vế `A vs B`).

- **`[2026-11-07 | Batch 12 Shorts Golden Hour Production]` — `TASK-007` & `TASK-013`**:
  - Sản xuất, kiểm định trực tiếp lưới frame (`job_773_sheet.jpg`..`job_784_sheet.jpg`, đạt `VideoQA 98–99/100`, `6/7` đến `8/8` cảnh video chuyển động thật), lập lịch Khung Giờ Vàng lên YouTube và gửi về Telegram (`-1001234567890`) trọn bộ **12/12 video Shorts** trên 6 kênh (`#777 Jsh3Q_0ip04`, `#778 AV4R0W8nCBg`, `#779 ZnzDxKwlrE4`, `#782 mRDUKrglCOs`, `#780 1Txbhnr3x5o`, `#783 0cQrpF7MM3Y`, `#781 88IxuM46wTI`, `#784 YaZReDxYyxE`, `#776 DcN_wEKF-Ek`, `#773 jOXGfmsc0w4`, `#775 vyz_-KJzUTM`, `#774 DbmIZwKK7ik`).
  - Sửa tận gốc lỗi nhiễu `#hashtags` & tiền tố `A vs B` trong `deduplication.py` / `video_library.py`, mở khóa cứu hộ file `.mp4` cho cảnh `_graphic.png` + kiểm định chuyển động thật cả Cảnh 1 & Cảnh 2 trong `render_nodes.py`, và bổ sung quét thư mục alias trong `quota_manager.py`.
