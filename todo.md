# ✅ todo.md — Danh Sách Tác Vụ & Tiêu Chí Nghiệm Thu (AI YouTube Studio)

> **Quy ước vận hành**:
> - Mỗi task đều có **Mã `TASK-xxx`**, **Mô tả mục tiêu**, **File liên quan** và **Tiêu chí hoàn thành (`DoD — Definition of Done`)**.
> - Trạng thái: `[x]` Đã hoàn thành | `[-]` Đang thực hiện | `[ ]` Chờ thực hiện.

---

## 🟢 Đã Hoàn Thành Gần Đây (Completed Tasks)

- [x] **TASK-001: Khóa cứng luật `> 85%` thời lượng Shorts là Video chuyển động thật (`5–7` clip `.mp4` độc lập)**
  - **Mô tả**: Đảm bảo mọi video Shorts 7–8 cảnh đều tải về 6–7 đoạn video chuyển động thật; cấm hủy sớm nhánh đua YouTube/Bilibili khi nhánh Pexels chưa có video và cấm hạ cấp xuống ảnh tĩnh khi thời lượng video `<= 85%`.
  - **File liên quan**: [`app/graph/nodes/media/fetch_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/fetch_media_node.py), [`app/graph/nodes/media/post_gather_rescue.py`](file:///d:/Python/Youtube/app/graph/nodes/media/post_gather_rescue.py), [`app/graph/nodes/media/refine_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/refine_media_node.py), [`app/graph/nodes/render_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/render_nodes.py).
  - **Tiêu chí hoàn thành**: Render thực tế `Job #764` đạt `7/7` cảnh là video chuyển động thật (`100%` thời lượng video).

- [x] **TASK-002: Chuyển 100% trách nhiệm chống lạc đề từng cảnh về LLM viết kịch bản (`Script-LLM Scene Search Authority`)**
  - **Mô tả**: Mở rộng `SceneContract` để `ContentAgent` trực tiếp xuất `subject_entity` và `search_queries` (3–4 truy vấn tiếng Anh cụ thể, không dịch ẩn dụ nghĩa đen) cho từng phân cảnh 1..N.
  - **File liên quan**: [`app/schemas/contracts.py`](file:///d:/Python/Youtube/app/schemas/contracts.py), [`app/agents/content/script_generator_mixin.py`](file:///d:/Python/Youtube/app/agents/content/script_generator_mixin.py), [`app/graph/nodes/media/visual_director_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/visual_director_node.py), [`app/graph/nodes/media/fetch_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/fetch_media_node.py).
  - **Tiêu chí hoàn thành**: Mọi phân cảnh sinh ra đều có `subject_entity` + `search_queries` sát ngữ cảnh câu thoại, ưu tiên số 1 khi gọi Tool tìm video.

- [x] **TASK-003: Xóa bỏ triệt để các regex/danh sách từ khóa vá lỗi cứng ngụy trang trong Python (`Zero Disguised Keyword Lists`)**
  - **Mô tả**: Rà soát và gỡ bỏ toàn bộ các danh sách từ khóa/regex cứng trong Python, đưa 100% hướng dẫn ngữ nghĩa về LLM System Prompts và `config/niches/*.yaml`.
  - **File liên quan**: [`app/graph/evaluators/script_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/script_critic.py), [`app/services/content_linter.py`](file:///d:/Python/Youtube/app/services/content_linter.py), [`app/agents/content/script_json_parser_mixin.py`](file:///d:/Python/Youtube/app/agents/content/script_json_parser_mixin.py), [`.agents/skills/systemic-root-cause-bugfix/SKILL.md`](file:///d:/Python/Youtube/.agents/skills/systemic-root-cause-bugfix/SKILL.md).
  - **Tiêu chí hoàn thành**: Không còn bất kỳ regex danh sách loài/đồ vật/từ sáo rỗng cứng nào trong code Python; vượt qua bài kiểm tra 4 miền (Wildlife, History, Psychology, Deep-Sea Mysteries).

- [x] **TASK-004: Xây dựng Bộ Kiểm Định Các Node LLM (`HotChannelScriptBenchmarkEvaluator` + `LLMToolCallCritic` + CLI `llm_node_inspector.py`)**
  - **Mô tả**: Tạo các thành phần mới để kiểm tra trực tiếp kịch bản sinh ra so với các kênh Hot Hit triệu view và kiểm tra + tự chữa lành (`AUTO_HEAL`) cách Node LLM `visual_director` gọi `@tool` tìm kiếm từng cảnh.
  - **File liên quan**: [`app/graph/evaluators/hot_channel_script_benchmark.py`](file:///d:/Python/Youtube/app/graph/evaluators/hot_channel_script_benchmark.py), [`app/graph/evaluators/llm_tool_call_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/llm_tool_call_critic.py), [`app/graph/nodes/media/evaluate_tool_calls_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/evaluate_tool_calls_node.py), [`app/graph/workflow.py`](file:///d:/Python/Youtube/app/graph/workflow.py), [`app/utils/llm_node_inspector.py`](file:///d:/Python/Youtube/app/utils/llm_node_inspector.py).
  - **Tiêu chí hoàn thành**: Chạy `python -m app.utils.llm_node_inspector` chấm điểm chi tiết từng chiều đối sánh Kênh Hot Hit và từng lệnh gọi `@tool` trong `< 45s`.

- [x] **TASK-005: Chuẩn hóa Trụ Cột Viral Số 2 — Giải Mã Bí Ẩn `"Why / Por Que / Tại Sao / How"` (`pillar_2_why_mystery_explainer`)**
  - **Mô tả**: Đưa dạng video giải thích bí ẩn `"Why / Por Que / How [Thực Thể / Địa Danh] [Hiện Tượng Ngược Đời]"` trở thành trụ cột Viral ngang hàng với `Entity A vs Entity B` trong cả System Prompt viết kịch bản lẫn Bộ chấm điểm Kênh Hot Hit.
  - **File liên quan**: [`app/graph/evaluators/hot_channel_script_benchmark.py`](file:///d:/Python/Youtube/app/graph/evaluators/hot_channel_script_benchmark.py), [`app/graph/evaluators/script_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/script_critic.py), [`app/agents/content/script_generator_mixin.py`](file:///d:/Python/Youtube/app/agents/content/script_generator_mixin.py), [`.agents/skills/youtube-viral-ceiling-breaker-strategy/SKILL.md`](file:///d:/Python/Youtube/.agents/skills/youtube-viral-ceiling-breaker-strategy/SKILL.md).
  - **Tiêu chí hoàn thành**: Kiểm định thực tế chủ đề `"Why Deep-Sea Sharks Paralyze Inside This Underwater Brine Lake"` đạt `8.9/10 Hot Benchmark` (`9.5/10` Title) và `9.5/10 Tool-Call Audit` (`100%` Video).

- [x] **TASK-006: Triển khai Hệ Thống Bộ Nhớ Ngoài (`External Memory Architecture`) cho Workspace**
  - **Mô tả**: Tổ chức bộ 5 file điều phối & bộ nhớ ngoài (`AGENTS.md`, `MEMORY.md`, `working_memory.md`, `todo.md`, `progress_log.md`) kết hợp với `.agents/skills/` để chống quên ngữ cảnh và chống lặp lại lỗi cũ.
  - **File liên quan**: [`AGENTS.md`](file:///d:/Python/Youtube/AGENTS.md), [`MEMORY.md`](file:///d:/Python/Youtube/MEMORY.md), [`working_memory.md`](file:///d:/Python/Youtube/working_memory.md), [`todo.md`](file:///d:/Python/Youtube/todo.md), [`progress_log.md`](file:///d:/Python/Youtube/progress_log.md), [`.agents/skills/auto-optimize-merge-push/SKILL.md`](file:///d:/Python/Youtube/.agents/skills/auto-optimize-merge-push/SKILL.md).
  - **Tiêu chí hoàn thành**: Cả 5 file được khởi tạo chuẩn xác, đồng bộ vào quy trình `auto-optimize-merge-push` và đẩy lên `origin/main`.

---

## ⏳ Danh Sách Tác Vụ Sẵn Sàng / Định Kỳ (Backlog & Standing Tasks)

- [ ] **TASK-007: Tạo & Kiểm định Video Hoàn Chỉnh (`End-to-End Video QA`) cho Trụ Cột `"Why / Por Que"` Mystery Explainer**
  - **Mô tả**: Khi người dùng yêu cầu tạo video mới, chạy trọn luồng từ `generate_script` $\rightarrow$ `evaluate_tool_calls` $\rightarrow$ `fetch_media` $\rightarrow$ `assemble_video` cho chủ đề `"Why..."` và trích xuất lưới frame kiểm định.
  - **File liên quan**: [`app/graph/workflow.py`](file:///d:/Python/Youtube/app/graph/workflow.py), [`app/utils/llm_node_inspector.py`](file:///d:/Python/Youtube/app/utils/llm_node_inspector.py), [`app/utils/langsmith_tracer.py`](file:///d:/Python/Youtube/app/utils/langsmith_tracer.py).
  - **Tiêu chí hoàn thành**: Video đầu ra đạt `> 85%` thời lượng là video chạy thật, khớp chủ thể 100%, điểm `HotChannelBenchmark >= 8.5/10`.

- [ ] **TASK-008: Kiểm Toán Định Kỳ 14 Ngày Hệ Thống, Agent & Model (`biweekly-system-model-upgrader`)**
  - **Mô tả**: Quét phiên bản model Gemini/LLM/TTS/Video mới nhất và trình báo cáo so sánh Tiếng Việt trước khi nâng cấp.
  - **File liên quan**: [`config/settings.py`](file:///d:/Python/Youtube/config/settings.py), [`app/utils/key_rotator.py`](file:///d:/Python/Youtube/app/utils/key_rotator.py), [`.agents/skills/biweekly-system-model-upgrader/SKILL.md`](file:///d:/Python/Youtube/.agents/skills/biweekly-system-model-upgrader/SKILL.md).
  - **Tiêu chí hoàn thành**: Có báo cáo so sánh Tiếng Việt và được người dùng xác nhận trước khi áp dụng.
