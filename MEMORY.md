# 🧠 MEMORY.md — Bản Đồ Kiến Trúc & Bất Biến Hệ Thống (AI YouTube Studio)

> **Vai trò file này**: Bản tóm tắt 1 trang (ROM) giúp AI Agent nắm trọn kiến trúc, phân quyền file (Single Source of Truth), các chốt chặn bất biến chống lặp lỗi và lệnh kiểm định nhanh chỉ trong **3 giây** đầu phiên làm việc mà không cần đọc mò toàn bộ codebase.

---

## 1. Mục Tiêu Dự Án & Tech Stack
- **Mục tiêu**: Hệ thống Multi-Agent tự động sản xuất, kiểm định chất lượng (Kịch bản + Hình ảnh + Âm thanh) và lập lịch đăng video **YouTube Shorts (`9:16`, `34s–40s`)** & **Long-Form Documentary (`16:9`, `8–10.5 phút`)** đạt chuẩn triệu view trên **7 kênh YouTube** (5 kênh Mỹ `en-US`, 2 kênh Brazil `pt-BR`).
- **Tech Stack cốt lõi**:
  - **Orchestration**: `LangGraph` ([`app/graph/workflow.py`](file:///d:/Python/Youtube/app/graph/workflow.py)) + `FastAPI` + `SQLite/PostgreSQL` ([`app/database/models.py`](file:///d:/Python/Youtube/app/database/models.py)).
  - **LLM & Multimodal Vision**: Gemini Priority Pool qua [`KeyRotator`](file:///d:/Python/Youtube/app/utils/key_rotator.py) (tự động xoay key/model và hạ bậc ngay lập tức model bị `Read timed out` `504` trong `30s`).
  - **Media Engine**: Đua song song `Pexels/Pixabay` + `YouTube Peak Extractor` (`yt-dlp`) + `Bilibili Safe-Crop` + `2D Doodle Explainer` + `Pollinations/Flux AI`.
  - **Render & Audio**: `FFmpeg` (xfade/concat, dynamic zoompan, color grading, ASS Subtitles) + `Edge-TTS / ElevenLabs`.

---

## 2. Luồng LangGraph Chuẩn (`app/graph/workflow.py`)
```text
generate_script ──► evaluate_script (ScriptCritic + HotChannelScriptBenchmark) ──► [refine_script]
      │
      ▼
synthesize_voice ──► visual_director (@tool Calling) ──► evaluate_tool_calls (LLMToolCallCritic Auto-Heal)
      │
      ▼
produce_hook_video ──► fetch_media (Parallel Pexels + YT + Bilibili) ──► evaluate_media (VisualCritic Vision)
      │
      ▼
[refine_media] ──► assemble_video (FFmpeg Render) ──► quality_gate
```

---

## 3. Bản Đồ Phân Quyền File (`Single Source of Truth` — Sửa Gì Vào Đâu?)

| Tầng chức năng | File chịu trách nhiệm chính (Click để mở) | Quy tắc bất khả xâm phạm |
| :--- | :--- | :--- |
| **1. Hồ sơ Ngách (Niche DNA, Chủ đề, Hook, Luật Search)** | [`config/niches/*.yaml`](file:///d:/Python/Youtube/config/niches/wildlife_predators.yaml), [`app/services/niche_manager.py`](file:///d:/Python/Youtube/app/services/niche_manager.py), [`config/channels.yaml`](file:///d:/Python/Youtube/config/channels.yaml) | **100% quy tắc ngách nằm ở YAML**. CẤM hardcode `director_notes` hay danh sách chủ đề trong file `.py`. |
| **2. Săn Chủ Đề & Chống Trùng Lặp** | [`app/agents/content/viral_ideation_mixin.py`](file:///d:/Python/Youtube/app/agents/content/viral_ideation_mixin.py), [`app/services/deduplication.py`](file:///d:/Python/Youtube/app/services/deduplication.py), [`app/services/video_library.py`](file:///d:/Python/Youtube/app/services/video_library.py) | Luôn chuẩn hóa `normalize_channel_id()`, so khớp cả tiền tố Chủ thể trước dấu `:` (`threshold <= 0.40`). |
| **3. Sinh Kịch Bản 5 Hồi + Gắn Truy Vấn Từng Cảnh** | [`app/agents/content/script_generator_mixin.py`](file:///d:/Python/Youtube/app/agents/content/script_generator_mixin.py), [`app/agents/content/script_fallback_and_refine_mixin.py`](file:///d:/Python/Youtube/app/agents/content/script_fallback_and_refine_mixin.py), [`app/agents/content/prompt_instructions.py`](file:///d:/Python/Youtube/app/agents/content/prompt_instructions.py), [`app/schemas/contracts.py`](file:///d:/Python/Youtube/app/schemas/contracts.py) | LLM viết kịch bản trực tiếp xuất `subject_entity` + `search_queries` (3–4 truy vấn tiếng Anh cụ thể) cho từng `SceneContract`. |
| **4. Chấm Kịch Bản & Đối Sánh Kênh Hot Hit** | [`app/graph/nodes/script_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/script_nodes.py), [`app/graph/evaluators/script_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/script_critic.py), [`app/graph/evaluators/hot_channel_script_benchmark.py`](file:///d:/Python/Youtube/app/graph/evaluators/hot_channel_script_benchmark.py) | Chạy song song `ScriptCritic` + `HotChannelScriptBenchmarkEvaluator`; giữ ứng viên có điểm cao nhất qua các vòng `refine_script`. |
| **5. Đạo Diễn Hình Ảnh Gọi `@tool` & Kiểm Định Tool** | [`app/graph/nodes/media/visual_director_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/visual_director_node.py), [`app/graph/tools/llm_visual_director.py`](file:///d:/Python/Youtube/app/graph/tools/llm_visual_director.py), [`app/graph/nodes/media/evaluate_tool_calls_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/evaluate_tool_calls_node.py), [`app/graph/evaluators/llm_tool_call_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/llm_tool_call_critic.py) | Ưu tiên số 1 cho `scene.search_queries` của Script LLM; tự động `AUTO_HEAL` các cảnh lệch chủ thể hoặc thiếu `@tool` video. |
| **6. Tải Video Chạy Thật (`>85%`) & Chấm Vision** | [`app/graph/nodes/media/hook_video_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/hook_video_node.py), [`app/graph/nodes/media/fetch_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/fetch_media_node.py), [`app/graph/nodes/media/post_gather_rescue.py`](file:///d:/Python/Youtube/app/graph/nodes/media/post_gather_rescue.py), [`app/graph/nodes/media/refine_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/refine_media_node.py), [`app/graph/tools/youtube_peak_executor.py`](file:///d:/Python/Youtube/app/graph/tools/youtube_peak_executor.py), [`app/graph/tools/youtube_search_ranker.py`](file:///d:/Python/Youtube/app/graph/tools/youtube_search_ranker.py), [`app/agents/visual_curator.py`](file:///d:/Python/Youtube/app/agents/visual_curator.py), [`app/graph/evaluators/visual_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/visual_critic.py) | Bắt buộc **`> 85.0%` thời lượng là video chuyển động thật (`5–7` clip `.mp4`)**. Cấm hủy sớm nhánh đua khi chưa có video thắng; cấm hạ cấp xuống ảnh tĩnh khi `<= 85%`. |
| **7. Render FFmpeg, Phụ Đề & Lập Lịch YouTube** | [`app/graph/nodes/render_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/render_nodes.py), [`app/services/video_assembler.py`](file:///d:/Python/Youtube/app/services/video_assembler.py), [`app/services/smart_scheduler.py`](file:///d:/Python/Youtube/app/services/smart_scheduler.py), [`app/services/youtube_analytics.py`](file:///d:/Python/Youtube/app/services/youtube_analytics.py) | Lịch Mỹ `16:15–18:15 UTC` & `01:00–03:00 UTC` (cách `8h45p`); Auto-Thumbnail Long-Form khóa ở mốc `50%` (`2.jpg`). |

---

## 4. Sáu Chốt Chặn Bất Biến Chống Lặp Lỗi (`Anti-Regression Locks`)
1. **Luật `> 85%` Thời Lượng Là Video Chuyển Động Thật (`5–7` Clip `.mp4`)**: Mọi video Shorts (`7–8` cảnh) phải có ít nhất `6–7` cảnh là video chạy thật. Cấm tắt Tầng 3 Bilibili (`_try_bilibili_broll_fallback`), cấm hủy Gemini Vision trên clip đã tải về vì `deadline_ts`, và khóa `can_downgrade_to_image = False` nếu tổng thời lượng video `<= 85%`.
2. **Luật `ZERO` Từ Khóa / Regex Vá Lỗi Cứng Trong Python**: Tuyệt đối CẤM viết `if "snow" in ...`, regex danh sách từ khóa (`re.compile(r"\b(word1|word2)\b")`), hay tuple tên loài trong `.py` để chữa cháy. Mọi phán quyết ngữ nghĩa phải nằm trong **LLM System Prompts** và **`config/niches/*.yaml`**.
3. **Luật Hai Trụ Cột Viral Ngang Hàng (`Dual-Pillar Viral Architecture`)**:
   - **Trụ cột 1 (`pillar_1_direct_matchup`)**: `[Entity A] vs [Entity B]: [Cược Sinh Tử]` + Song đấu 2 chiều.
   - **Trụ cột 2 (`pillar_2_why_mystery_explainer`)**: `Why / Por Que / How [Tên Thực Thể / Địa Danh] [Bí Ẩn Ngược Đời]` + Hook `0.0s` thả thẳng vào hiện tượng kỳ bí (không lặp câu hỏi tiêu đề) + 5 Hồi Giải Mã Bí Ẩn bằng phép ví von đời thường `$1`.
4. **Luật Quyền Tối Thượng Tìm Kiếm Của Script LLM (`Script-LLM Scene Search Authority`)**: LLM viết kịch bản là người hiểu rõ nhất mỗi cảnh nói về ai/cái gì, nên phải trực tiếp xuất `subject_entity` và `search_queries` trong từng cảnh.
5. **Luật Báo Cáo Số Liệu Thật 100%**: Chỉ báo cáo số liệu từ **YouTube Data API v3** và **YouTube Analytics API v2** (nếu video `< 24–48h` chưa chốt sổ Analytics thì ghi `⏳ Chờ Analytics chốt sổ (24–48h)`).
6. **Luật Kiểm Chứng Thực Tế Trước Khi Kết Luận (`Verification Before Completion`)**: Luôn chạy lệnh kiểm định trước khi báo cáo hoàn thành.

---

## 5. Bộ Lệnh Kiểm Định & Debug Tức Thì (`Quick CLI Commands`)
- **Kiểm tra nhanh các Node LLM (Kịch bản vs Kênh Hot Hit + Kiểm định gọi `@tool` trong ~20–40s)**:
  ```powershell
  python -m app.utils.llm_node_inspector --topic "Why Deep-Sea Sharks Paralyze Inside This Underwater Brine Lake" --niche deep_sea_earth_mysteries
  python -m app.utils.llm_node_inspector --job 764
  python -m app.utils.llm_node_inspector --suite
  ```
- **Truy vết LangSmith Waterfall & Thời gian chạy từng tầng sau khi render video**:
  ```powershell
  python -m app.utils.langsmith_tracer --job <job_id>
  ```
- **Kiểm tra biên dịch cú pháp Python trước khi Commit**:
  ```powershell
  python -m py_compile <danh_sách_file_vừa_sửa.py>
  ```
