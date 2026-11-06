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
