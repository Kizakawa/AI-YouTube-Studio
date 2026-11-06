# ⚡ working_memory.md — Bộ Nhớ Trạng Thái Hiện Tại ("RAM Ngoài" Của Agent)

> **Nguyên tắc vận hành**:
> - Đọc file này **đầu tiên** (cùng với [`MEMORY.md`](file:///d:/Python/Youtube/MEMORY.md) và [`todo.md`](file:///d:/Python/Youtube/todo.md)) khi bắt đầu bất kỳ phiên làm việc hoặc yêu cầu mới nào.
> - Cuối mỗi tác vụ (trước khi `git commit & push`), **Main Agent BẮT BUỘC cập nhật lại file này** theo đúng 4 mục: `✅ Đã làm gì`, `❗ Đang kẹt / Lưu ý gì`, `📂 File vừa xử lý`, `➡️ Bước tiếp theo`.

---

## 📌 Snapshot Trạng Thái Hiện Tại (Cập nhật: `2026-11-06 22:42 +07:00`)

- **Nhánh Git hiện tại**: `main` (đồng bộ sạch với `origin/main`).
- **Tác vụ vừa hoàn thành**:
  - `TASK-006`: Tổ chức hệ thống **Bộ Nhớ Ngoài (`External Memory System`)** gồm [`AGENTS.md`](file:///d:/Python/Youtube/AGENTS.md), [`MEMORY.md`](file:///d:/Python/Youtube/MEMORY.md), [`working_memory.md`](file:///d:/Python/Youtube/working_memory.md), [`todo.md`](file:///d:/Python/Youtube/todo.md), [`progress_log.md`](file:///d:/Python/Youtube/progress_log.md) để chống trôi ngữ cảnh và chống lặp lại lỗi cũ.
  - `TASK-005`: Chuẩn hóa **Trụ cột Viral số 2 — Giải Mã Bí Ẩn `"Why / Por Que / Tại Sao / How"` (`pillar_2_why_mystery_explainer`)** song hành ngang hàng với trụ cột Đối đầu `Entity A vs Entity B` (`pillar_1_direct_matchup`).
  - `TASK-004`: Xây dựng bộ kiểm định trực tiếp các Node LLM ([`HotChannelScriptBenchmarkEvaluator`](file:///d:/Python/Youtube/app/graph/evaluators/hot_channel_script_benchmark.py), [`LLMToolCallCritic`](file:///d:/Python/Youtube/app/graph/evaluators/llm_tool_call_critic.py), [`evaluate_tool_calls_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/evaluate_tool_calls_node.py) và CLI [`llm_node_inspector.py`](file:///d:/Python/Youtube/app/utils/llm_node_inspector.py)).

---

## ✅ 1. Đã Làm Gì (Completed In Recent Turns)
1. **Xóa bỏ triệt để các bản vá từ khóa/regex cứng ngụy trang trong Python (`5a79a8c`)**:
   - Dọn sạch các regex từ khóa cứng trong [`app/graph/evaluators/script_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/script_critic.py), [`app/services/content_linter.py`](file:///d:/Python/Youtube/app/services/content_linter.py) và [`app/agents/content/script_json_parser_mixin.py`](file:///d:/Python/Youtube/app/agents/content/script_json_parser_mixin.py).
2. **Thiết lập Quyền Tối Thượng Tìm Kiếm Của Script LLM (`b3ee1e2`, `c502f65`)**:
   - `ContentAgent` trực tiếp sinh `subject_entity` và `search_queries` (3–4 truy vấn tiếng Anh cụ thể) cho từng phân cảnh trong [`SceneContract`](file:///d:/Python/Youtube/app/schemas/contracts.py).
   - [`visual_director_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/visual_director_node.py) và [`fetch_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/fetch_media_node.py) ưu tiên số 1 cho danh sách truy vấn này.
3. **Tạo Mới 2 Node/Evaluator Kiểm Định LLM + Công Cụ CLI `llm_node_inspector.py` (`b3ee1e2`, `a1f0afb`)**:
   - [`HotChannelScriptBenchmarkEvaluator`](file:///d:/Python/Youtube/app/graph/evaluators/hot_channel_script_benchmark.py): Đối sánh kịch bản vừa viết với hồ sơ các kênh Hot Hit triệu view (`Zack D. Films`, `Casual Geographic`, `FossilVideos`, `Charisma on Command`, `Você Sabia?`) trên cả 2 trụ cột Viral (`pillar_1_direct_matchup` & `pillar_2_why_mystery_explainer`).
   - [`LLMToolCallCritic`](file:///d:/Python/Youtube/app/graph/evaluators/llm_tool_call_critic.py) & [`node_evaluate_llm_tool_calls`](file:///d:/Python/Youtube/app/graph/nodes/media/evaluate_tool_calls_node.py): Kiểm tra `@tool` của `VisualDirector`, khóa cứng tỷ lệ `@tool` Video chuyển động (`100%` trên Shorts) và tự động `AUTO_HEAL` các cảnh gán sai chủ thể hoặc truy vấn lạc đề trước khi tải video.
   - Đã kiểm chứng thực tế đạt điểm cao trên cả 3 mẫu:
     - `Job #764` (*Komodo Dragon vs Water Buffalo*): `8.7/10 Hot Benchmark`, `9.3/10 Tool-Call Audit` (`100%` Video).
     - *Secretary Bird vs Black Mamba* (`pillar_1_direct_matchup`): `8.1/10 Hot Benchmark`, `9.5/10 Tool-Call Audit` (`Auto-Healed` Cảnh 5).
     - *Why Deep-Sea Sharks Paralyze Inside This Underwater Brine Lake* (`pillar_2_why_mystery_explainer`): `8.9/10 Hot Benchmark` (`9.5/10` Title), `9.5/10 Tool-Call Audit` (`100%` Video).
4. **Thiết lập Hệ Thống Bộ Nhớ Ngoài (`MEMORY.md`, `working_memory.md`, `todo.md`, `progress_log.md`)**:
   - Tinh gọn [`AGENTS.md`](file:///d:/Python/Youtube/AGENTS.md) thành bộ điều phối sắc bén và tích hợp quy trình tự động cập nhật Bộ Nhớ Ngoài vào [`.agents/skills/auto-optimize-merge-push/SKILL.md`](file:///d:/Python/Youtube/.agents/skills/auto-optimize-merge-push/SKILL.md).

---

## ❗ 2. Đang Kẹt Ở Đâu / Lưu Ý Quan Trọng (Active Watchpoints & Constraints)
- **Không có lỗi tồn đọng (Zero Blockers)**: Toàn bộ bài kiểm định compile & live LLM node inspection đều đạt `PASSED`.
- **Chốt chặn bắt buộc phải giữ vững khi làm các task tiếp theo**:
  1. Không bao giờ hạ tỷ lệ thời lượng video chuyển động thật của Shorts xuống `<= 85.0%` (`5–7` đoạn `.mp4` tải về độc lập).
  2. Không bao giờ thêm từ khóa/regex danh sách từ cứng vào file `.py` — mọi tinh chỉnh ngữ nghĩa hoặc tìm kiếm phải sửa ở **LLM System Prompts** hoặc **`config/niches/*.yaml`**.

---

## 📂 3. Các File Vừa Xử Lý (Recently Modified Files)
- [`AGENTS.md`](file:///d:/Python/Youtube/AGENTS.md)
- [`MEMORY.md`](file:///d:/Python/Youtube/MEMORY.md)
- [`working_memory.md`](file:///d:/Python/Youtube/working_memory.md)
- [`todo.md`](file:///d:/Python/Youtube/todo.md)
- [`progress_log.md`](file:///d:/Python/Youtube/progress_log.md)
- [`.agents/skills/auto-optimize-merge-push/SKILL.md`](file:///d:/Python/Youtube/.agents/skills/auto-optimize-merge-push/SKILL.md)
- [`app/graph/evaluators/hot_channel_script_benchmark.py`](file:///d:/Python/Youtube/app/graph/evaluators/hot_channel_script_benchmark.py)
- [`app/graph/evaluators/llm_tool_call_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/llm_tool_call_critic.py)
- [`app/graph/nodes/media/evaluate_tool_calls_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/evaluate_tool_calls_node.py)
- [`app/utils/llm_node_inspector.py`](file:///d:/Python/Youtube/app/utils/llm_node_inspector.py)

---

## ➡️ 4. Bước Tiếp Theo (Immediate Next Steps)
1. Khi bắt đầu yêu cầu mới từ người dùng: đọc nhanh [`MEMORY.md`](file:///d:/Python/Youtube/MEMORY.md) + [`working_memory.md`](file:///d:/Python/Youtube/working_memory.md) + [`todo.md`](file:///d:/Python/Youtube/todo.md).
2. Sẵn sàng thực thi:
   - Sản xuất thử nghiệm hoặc sản xuất hàng loạt (Batch Production) video Shorts/Long-form theo cả 2 trụ cột (`Entity A vs Entity B` và `Why / Por Que / How Mystery Explainer`).
   - Hoặc kiểm toán hiệu suất 7 kênh YouTube bằng số liệu thật từ YouTube Data API v3 & Analytics API v2 khi người dùng yêu cầu.
