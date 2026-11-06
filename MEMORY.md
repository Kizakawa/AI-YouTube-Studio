# 🧠 MEMORY.md — Bản Đồ Kiến Trúc & Sổ Đen 14 Lỗi Cấm Lặp Lại (AI YouTube Studio)

> **Vai trò file này**: Bản tóm tắt (ROM) giúp AI Agent nắm trọn kiến trúc, phân quyền file (`Single Source of Truth`), **14 Lỗi Cấm Lặp Lại tổng hợp từ tất cả các phiên chat** và bộ lệnh kiểm định nhanh chỉ trong **3–5 giây** đầu phiên làm việc mà không cần đọc mò toàn bộ codebase.

---

## 1. Mục Tiêu Dự Án & Tech Stack
- **Mục tiêu**: Hệ thống Multi-Agent tự động sản xuất, kiểm định chất lượng (Kịch bản + Hình ảnh + Âm thanh) và lập lịch đăng video **YouTube Shorts (`9:16`, `34s–40s`)** & **Long-Form Documentary (`16:9`, `10 Chương / 30–40 Cảnh`, `>= 9m15s` mở khóa Mid-Roll Ads)** đạt chuẩn triệu view trên **7 kênh YouTube** (5 kênh Mỹ `en-US`, 2 kênh Brazil `pt-BR`).
- **Tech Stack cốt lõi**:
  - **Orchestration**: `LangGraph` ([`app/graph/workflow.py`](file:///d:/Python/Youtube/app/graph/workflow.py)) + `FastAPI` + `SQLite/PostgreSQL` ([`app/database/models.py`](file:///d:/Python/Youtube/app/database/models.py)).
  - **LLM & Multimodal Vision**: Gemini Priority Pool qua [`KeyRotator`](file:///d:/Python/Youtube/app/utils/key_rotator.py) (hạ bậc ngay model bị `Read timed out 504` trong `30s`; cách ly key cạn quota ngày trong `1800s` / `30 phút`).
  - **Media Engine**: Đua song song `Pexels/Pixabay` + `YouTube Peak Extractor` (`yt-dlp`) + `Bilibili Safe-Crop` + `2D Doodle Explainer` (`@CHARACTER_TAG` trên nền giấy kem `#F7F3E8`) + `Pollinations/Flux AI`.
  - **Render & Audio**: `FFmpeg` (tự động chọn `h264_qsv` Intel QuickSync khi NVIDIA Driver `581.29 < 610.0` chặn `h264_nvenc` API 13.1; single-pass `-f concat` cho Long-Form 40 cảnh) + `Edge-TTS / ElevenLabs`.

---

## 2. Luồng LangGraph Chuẩn (`app/graph/workflow.py`)
```text
generate_script ──► evaluate_script (ScriptCritic + HotChannelScriptBenchmark) ──► [refine_script (Best-Score Restore)]
      │
      ▼
synthesize_voice ──► visual_director (@tool Calling) ──► evaluate_tool_calls (LLMToolCallCritic Auto-Heal)
      │
      ▼
produce_hook_video ──► fetch_media (Parallel Pexels + YT + Bilibili) ──► evaluate_media (VisualCritic Vision)
      │
      ▼
[refine_media] ──► assemble_video (Pre-Render Zero-Graphic Rescue + FFmpeg Render) ──► quality_gate
```

---

## 3. Bản Đồ Phân Quyền File (`Single Source of Truth` — Sửa Gì Vào Đâu?)

| Tầng chức năng | File chịu trách nhiệm chính (Click để mở) | Quy tắc bất khả xâm phạm |
| :--- | :--- | :--- |
| **1. Hồ sơ Ngách (Niche DNA, Chủ đề, Hook, Luật Search)** | [`config/niches/*.yaml`](file:///d:/Python/Youtube/config/niches/wildlife_predators.yaml), [`app/services/niche_manager.py`](file:///d:/Python/Youtube/app/services/niche_manager.py), [`config/channels.yaml`](file:///d:/Python/Youtube/config/channels.yaml) | **100% quy tắc ngách nằm ở YAML**. CẤM hardcode `director_notes` hay `cfg["topics"]` trong `batch_producer.py` hoặc file `.py`. |
| **2. Săn Chủ Đề & Chống Trùng Lặp** | [`app/agents/content/viral_ideation_mixin.py`](file:///d:/Python/Youtube/app/agents/content/viral_ideation_mixin.py), [`app/services/intelligence/deduplication.py`](file:///d:/Python/Youtube/app/services/intelligence/deduplication.py), [`app/services/video_library.py`](file:///d:/Python/Youtube/app/services/video_library.py) | Luôn chuẩn hóa `normalize_channel_id()`, lọc bỏ `#hashtags` trước khi tính độ trùng, và kiểm tra cả 2 vế `A vs B` (`threshold <= 0.40`). |
| **3. Sinh Kịch Bản (Shorts 5 Hồi & Long-Form 10 Chương) + Gắn Truy Vấn Cảnh** | [`app/agents/content/script_generator_mixin.py`](file:///d:/Python/Youtube/app/agents/content/script_generator_mixin.py), [`app/agents/content/longform_10min_generator.py`](file:///d:/Python/Youtube/app/agents/content/longform_10min_generator.py), [`app/agents/content/prompt_instructions.py`](file:///d:/Python/Youtube/app/agents/content/prompt_instructions.py), [`app/schemas/contracts.py`](file:///d:/Python/Youtube/app/schemas/contracts.py) | LLM viết kịch bản trực tiếp xuất `subject_entity` + `search_queries` (3–4 truy vấn tiếng Anh vật lý cụ thể) cho từng `SceneContract`. |
| **4. Chấm Kịch Bản & Đối Sánh Kênh Hot Hit** | [`app/graph/nodes/script_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/script_nodes.py), [`app/graph/evaluators/script_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/script_critic.py), [`app/graph/evaluators/hot_channel_script_benchmark.py`](file:///d:/Python/Youtube/app/graph/evaluators/hot_channel_script_benchmark.py) | Chạy song song `ScriptCritic` + `HotChannelScriptBenchmarkEvaluator` trên cả 2 trụ cột (`Entity A vs B` & `Why/How Mystery`); giữ bản có điểm cao nhất. |
| **5. Đạo Diễn Hình Ảnh Gọi `@tool` & Kiểm Định Tool** | [`app/graph/nodes/media/visual_director_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/visual_director_node.py), [`app/graph/tools/llm_visual_director.py`](file:///d:/Python/Youtube/app/graph/tools/llm_visual_director.py), [`app/graph/nodes/media/evaluate_tool_calls_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/evaluate_tool_calls_node.py), [`app/graph/evaluators/llm_tool_call_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/llm_tool_call_critic.py) | Ưu tiên số 1 cho `scene.search_queries` của Script LLM; tự động `AUTO_HEAL` các cảnh lệch chủ thể hoặc thiếu `@tool` video. |
| **6. Tải Video Chạy Thật (`>85%`) & Chấm Vision 3-Frame** | [`app/graph/nodes/media/hook_video_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/hook_video_node.py), [`app/graph/nodes/media/fetch_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/fetch_media_node.py), [`app/graph/nodes/media/post_gather_rescue.py`](file:///d:/Python/Youtube/app/graph/nodes/media/post_gather_rescue.py), [`app/graph/nodes/media/refine_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/refine_media_node.py), [`app/agents/visual_curator.py`](file:///d:/Python/Youtube/app/agents/visual_curator.py), [`app/graph/evaluators/visual_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/visual_critic.py) | Bắt buộc **`> 85.0%` thời lượng là video chuyển động thật (`5–7` clip `.mp4`)** và **0% thẻ xanh `_graphic.png`**. Mọi nhánh (kể cả Pexels) đều phải qua 3-Frame Gemini Vision. |
| **7. Render FFmpeg, Phụ Đề, Bình Luận & Lập Lịch** | [`app/graph/nodes/render_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/render_nodes.py), [`app/services/video_assembler/concat_and_transitions.py`](file:///d:/Python/Youtube/app/services/video_assembler/concat_and_transitions.py), [`app/services/smart_scheduler.py`](file:///d:/Python/Youtube/app/services/smart_scheduler.py), [`app/services/youtube_analytics/engagement_comments_mixin.py`](file:///d:/Python/Youtube/app/services/youtube_analytics/engagement_comments_mixin.py) | Lịch Mỹ Shorts `16:15–18:15` & `01:00–03:00 UTC`; Long-Form Mỹ `21:30–22:30 UTC`; Auto-Thumbnail `50%` (`2.jpg`); tự động gắn link Long-Form vào bình luận Shorts. |

---

## 4. Sổ Đen 14 Lỗi Cấm Lặp Lại Tổng Hợp Từ Tất Cả Các Phiên Chat (`Cross-Session Anti-Regression Locks`)

| # | Lỗi Bị Cấm Tuyệt Đối (Forbidden Error) | Chốt Chặn Kỹ Thuật Đã Khóa (Technical Lock) |
| :--- | :--- | :--- |
| **1** | **Cấm để Shorts rơi về ảnh tĩnh (`<= 85%` video) hoặc hiện thẻ chữ xanh `_graphic.png`** | Shorts bắt buộc `> 85.0%` thời lượng là video chạy thật (`5–7` clip `.mp4`). `render_nodes.py` có `Pre-Render Zero-Graphic Rescue` cắt trực tiếp từ master `.mp4`; `checkpoint_store.py` cấm tái sử dụng cache `fetch_media` có `_graphic.png`. Cấm hủy sớm nhánh đua YouTube/Bilibili khi chưa có video thắng. |
| **2** | **Cấm ghi đè cấu hình YAML bằng code Python (`Zero Hardcoded Niche Prompts/Topics`)** | 100% chủ đề, công thức Hook, nhịp điệu và luật tìm kiếm nằm trong `config/niches/*.yaml` qua `niche_manager.py`. Cấm hardcode `cfg["topics"]` / `cfg["director_notes"]` trong `batch_producer.py` hoặc ép khuôn `[Animal A] vs [Animal B]` / `"Human Brain"` sang ngách Tâm lý học. |
| **3** | **Cấm vá lỗi cục bộ bằng danh sách từ khóa / Regex cứng trong Python (`Zero Disguised Keyword Lists`)** | Cấm viết `if "snow" in ...`, regex danh sách từ (`re.compile(r"\b(word1|word2)\b")`), hay tuple tên loài trong `.py`. Mọi tư duy ngữ nghĩa nằm ở **LLM System Prompts** (`SceneContract.subject_entity` + `search_queries`) và `config/niches/*.yaml`. |
| **4** | **Cấm giấu tên thực thể ở Tiêu đề & Cấm lặp lại câu hỏi Tiêu đề ở Cảnh 1 (`0.0s`)** | Hỗ trợ ngang hàng **2 Trụ cột Viral**: (1) `[Entity A] vs [Entity B]: [Stakes]` và (2) `Why / Por Que / How [Thực Thể/Địa Danh] [Bí Ẩn Ngược Đời]`. Cấm giấu tên kiểu *"This 4,000lb Giant / This Place"*; cấm lặp câu hỏi `"Why..."` ở Cảnh 1; cấm chia `"Round 1, Round 2"` trong 35s. |
| **5** | **Cấm rò rỉ chữ Tiếng Việt / Phụ đề Tiếng Trung / Watermark / Thanh tiêu đề vàng đầu video** | Cấm lọt từ Tiếng Việt vào kịch bản `en-US`/`pt-BR` hoặc nhãn `⏱️ Chapters:` tiếng Anh vào kênh Brazil (`⏱️ Capítulos:`). Mọi clip (kể cả Pexels/Bilibili) phải qua `3-Frame Gemini Vision` và `Subtitle-Safe Bilibili Crop`. Cấm vẽ thanh progress/header màu vàng trên đỉnh video ở `0.0s`. |
| **6** | **Cấm nhân bản chủ đề trùng lặp (`Zero Topic-Cloning`) & Cấm đè chủ đề tĩnh khi cạn kho** | Chuẩn hóa `normalize_channel_id()`, lọc bỏ `#hashtags` (`#shorts`, `#humanbehavior`) trước khi đo độ trùng, và kiểm tra cả 2 vế `A vs B`. Khi cạn chủ đề tĩnh, phải gọi LLM sáng tạo chủ đề mới từ YAML — cấm lấy `remaining[0]` hoặc đè bài tĩnh (`Arkhipov`, `Petrov`, `Franklin`, `Kraken`). |
| **7** | **Cấm sai lệch Cờ Quốc Gia / Thời Đại Lịch Sử & Cấm tách danh từ ghép đồng âm** | `VisualCurationAgent` chấm `< 4.0` (loại) clip có cờ/phù hiệu ngoại quốc sai lịch sử (cờ Thổ Nhĩ Kỳ/Mỹ trong chuyện Liên Xô) hoặc xe hơi/du khách hiện đại trong bối cảnh cổ đại. Cấm tách từ ghép đồng âm (`"pelican"` chim bồ nông cho `"pelican eel"` cá chình biển sâu) hoặc ghép tên riêng lịch sử (`"Phineas Gage"`) vào truy vấn Pexels. |
| **8** | **Cấm đọc thông số vật lý khô khan (`PSI`, `Newtons`, `kPa`) & Cấm văn mẫu AI / Sinh hóa phân tử** | Chuyển mọi thông số lực/áp suất thành phép ví von đời thường `$1` kèm từ viết tắt nói tự nhiên (`it's`, `doesn't`, `you'd`; `conversa de bar` ở `pt-BR`). Cấm giải thích bằng sinh hóa học tế bào (`molecules`, `proteins`). |
| **9** | **Cấm đăng sai khung giờ vàng Mỹ/Brazil (tránh nhiễm `81%` view VN) & Cấm đụng độ giờ đăng** | Shorts Mỹ khóa ở `16:15–18:15 UTC` & `01:00–03:00 UTC` (cách `8h45p`, lệch `60p` cùng Gmail; **CẤM `14:00–15:00 UTC` & `21:00–21:45 UTC`**). Long-Form Mỹ khóa ở **`21:30–22:30 UTC` (`05:30 PM EST`)**. Cấm đặt lịch Long-Form và Short cách nhau `< 3.0 giờ` (`10,800s`) trên cùng kênh. |
| **10** | **Cấm để video Public `0 bình luận` & Bắt buộc gắn cầu nối Shorts $\rightarrow$ Long-Form** | Tự động backfill bình luận tranh luận 2 phe (Cunningham's Law) ngay khi video chuyển sang `public`, kèm dòng dẫn nguồn `🎬 Watch the Full Documentary: https://youtu.be/...` trỏ về video 16:9 cùng kênh. Khóa `categoryId` là `'15'` hoặc `'22'` (cấm `'27'`). |
| **11** | **Cấm làm Long-Form (`16:9`) ngắn hơn 8 phút hoặc dùng `xfade` 40 cảnh gây tràn RAM** | Long-Form bắt buộc theo kiến trúc **10 Chương / 30–40 Cảnh (`>= 9m15s` / `LONGFORM_MIDROLL_FLOOR_MS = 555_000`)** để bật quảng cáo giữa giờ (`>= 08:00`). Dùng single-pass `-f concat -safe 0` trong `concat_and_transitions.py` thay vì 40-input `xfade`. |
| **12** | **Cấm đóng dấu chữ Thumbnail ở `0.0s` cho kênh chưa xác minh SĐT** | YouTube lấy khung hình giữa video **`50.0%` (`2.jpg`)** làm `hqdefault.jpg`. Với Long-Form `16:9`, khóa cảnh Hero sắc nét + dải nền tối + Banner 2 tầng `88px` ở đúng 3 mốc `25%`, `50%`, `75%` và giữ tiêu đề `< 55–60 ký tự`. |
| **13** | **Cấm để Model treo làm đứt chuỗi & Cấm xóa hàng loạt video đã đăng** | Hạ bậc model bị `Read timed out` (`504`) trong `30s` và cách ly key hết quota ngày trong `1800s` (`30p`). Khi cần ẩn video lỗi/trùng trên kênh, chuyển trạng thái sang **`PRIVATE`** qua API thay vì xóa trắng hàng loạt. |
| **14** | **Cấm báo cáo số liệu ước lượng & Cấm sửa/xóa nội dung của đồng tác giả** | Báo cáo 100% số liệu thật từ YouTube Data API v3 & Analytics API v2. Trong các tài liệu/code dùng chung, chỉ sửa đúng phạm vi của người dùng (`Scoped Editing Rule`). |

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
