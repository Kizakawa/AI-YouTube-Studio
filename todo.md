# ✅ todo.md — Danh Sách Kế Hoạch & Tiêu Chí Nghiệm Thu Toàn Hệ Thống (AI YouTube Studio)

> **Quy ước vận hành**:
> - File này hợp nhất **100% các kế hoạch và hạng mục kỹ thuật từ tất cả các phiên chat**.
> - Mỗi task đều có **Mã `TASK-xxx`**, **Mô tả mục tiêu**, **File liên quan** và **Tiêu chí hoàn thành (`DoD — Definition of Done`)**.
> - Trạng thái: `[x]` Đã hoàn thành | `[-]` Đang thực hiện | `[ ]` Chờ thực hiện / Định kỳ.

---

## 🟢 Đã Hoàn Thành Gần Đây Từ Tất Cả Các Phiên Chat (Completed Tasks)

- [x] **TASK-001: Khóa cứng luật `> 85%` thời lượng Shorts là Video chuyển động thật (`5–7` clip `.mp4`) & Diệt tận gốc thẻ xanh `_graphic.png`**
  - **Mô tả**: Đảm bảo mọi video Shorts 7–8 cảnh đều tải về 6–7 đoạn video chuyển động thật; cấm hủy sớm nhánh đua YouTube/Bilibili khi nhánh Pexels chưa có video; cấm hạ cấp xuống ảnh tĩnh khi thời lượng video `<= 85%`; chặn tái sử dụng cache `fetch_media` chứa `_graphic.png` và thêm chốt `Pre-Render Zero-Graphic Rescue` cắt trực tiếp từ master `.mp4`.
  - **File liên quan**: [`app/graph/nodes/media/fetch_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/fetch_media_node.py), [`app/graph/nodes/media/post_gather_rescue.py`](file:///d:/Python/Youtube/app/graph/nodes/media/post_gather_rescue.py), [`app/graph/nodes/media/refine_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/refine_media_node.py), [`app/graph/nodes/render_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/render_nodes.py), [`app/graph/checkpoint_store.py`](file:///d:/Python/Youtube/app/graph/checkpoint_store.py).
  - **Tiêu chí hoàn thành**: Render thực tế `Job #764`, `#769`, `#770`, `#772`, `#775` đạt `100%` cảnh là video chuyển động thật, `0` thẻ xanh `_graphic.png`.

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

- [x] **TASK-007: Chuyển đổi 100% cấu hình Ngách về `config/niches/*.yaml` (`YAML Single Source of Truth` — Phiên chat `861ab3b5` & `40c844d4`)**
  - **Mô tả**: Gỡ bỏ hoàn toàn các danh sách `topics` và `director_notes` viết cứng trong `batch_producer.py`; nạp 100% hồ sơ ngách, ngân hàng chủ đề `viral_topic_bank_2026`, công thức Hook, nhịp điệu và chiến lược tìm kiếm video từ `config/niches/*.yaml` qua `app/services/niche_manager.py`.
  - **File liên quan**: [`config/niches/*.yaml`](file:///d:/Python/Youtube/config/niches/psychology.yaml), [`app/services/niche_manager.py`](file:///d:/Python/Youtube/app/services/niche_manager.py), [`app/services/batch_producer.py`](file:///d:/Python/Youtube/app/services/batch_producer.py), [`app/agents/content/prompt_instructions.py`](file:///d:/Python/Youtube/app/agents/content/prompt_instructions.py).
  - **Tiêu chí hoàn thành**: `batch_producer.py` không còn chứa bất kỳ `topics` hay `director_notes` cứng nào (`c61ec52`, `c2e0b22`, `3d51ac9`).

- [x] **TASK-008: Kiến trúc Long-Form `16:9` 10 Chương / 30–40 Cảnh (`>= 9m15s` Mid-Roll Ads) + Single-Pass FFmpeg Concat (Phiên chat `861ab3b5` & `40c844d4`)**
  - **Mô tả**: Nâng cấp trình tạo video dài 16:9 lên 10 chương (`30–40` phân cảnh, `~1,500–1,750` từ, `LONGFORM_MIDROLL_FLOOR_MS = 555_000`) để vượt mốc `08:00` bật quảng cáo giữa giờ; sửa lỗi tràn RAM FFmpeg `xfade` 40 cảnh bằng single-pass `-f concat -safe 0` + `.ass` burn-in; khóa Auto-Thumbnail Hero Frame `88px` ở mốc `25%`, `50%` (`2.jpg`), `75%`.
  - **File liên quan**: [`app/agents/content/longform_10min_generator.py`](file:///d:/Python/Youtube/app/agents/content/longform_10min_generator.py), [`app/services/video_assembler/concat_and_transitions.py`](file:///d:/Python/Youtube/app/services/video_assembler/concat_and_transitions.py), [`app/graph/nodes/audio_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/audio_nodes.py), [`app/services/video_assembler/subtitle_builder.py`](file:///d:/Python/Youtube/app/services/video_assembler/subtitle_builder.py).
  - **Tiêu chí hoàn thành**: Sản xuất & lên lịch thành công 2 phim tài liệu 10 phút (`Job #712` *9m53s* `iTVOTK0IKso` và `Job #714` *9m19s* `L-5xCei7MDU`) ở khung giờ vàng Mỹ `22:30 UTC` (`05:30 PM EST`).

- [x] **TASK-009: Tự động gắn Cầu Nối Shorts $\rightarrow$ Long-Form (`Cross-Promo Comment & Description Funnel` — Commit `8900842`)**
  - **Mô tả**: Tự động đính kèm đường dẫn xem phim tài liệu dài cùng kênh (`🎬 Watch the Full Documentary: https://youtu.be/...`) vào bình luận ghim và phần mô tả của các video Shorts để kéo luồng khán giả từ Shorts sang Long-Form `16:9`.
  - **File liên quan**: [`app/services/youtube_analytics/engagement_comments_mixin.py`](file:///d:/Python/Youtube/app/services/youtube_analytics/engagement_comments_mixin.py), [`app/services/batch_producer.py`](file:///d:/Python/Youtube/app/services/batch_producer.py).
  - **Tiêu chí hoàn thành**: Đã kích hoạt `_build_long_form_cross_promo_line` và backfill bình luận liên kết trên các video Public của `Mysteries` và `Psychology`.

- [x] **TASK-010: Kiểm Định 3-Frame Gemini Vision Trên Mọi Nguồn Video (Kể Cả Pexels) & Khóa Bilibili Safe-Crop (`dd3d124`, `3d19310`)**
  - **Mô tả**: Bắt buộc nhánh Pexels/Pixabay cũng phải đi qua kiểm định thị giác 3 khung hình của Gemini Vision (loại bỏ chữ Trung Quốc/TikTok, sai cờ quốc gia, sai thời đại); sửa lỗi thanh tiêu đề đen của Contact-Sheet che mất phần đầu khung hình khi `VisualCritic` chấm điểm.
  - **File liên quan**: [`app/graph/nodes/media/fetch_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/fetch_media_node.py), [`app/agents/media_vision_curator_mixin.py`](file:///d:/Python/Youtube/app/agents/media_vision_curator_mixin.py), [`app/graph/evaluators/visual_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/visual_critic.py).
  - **Tiêu chí hoàn thành**: Không còn clip Pexels/Bilibili dính chữ Trung Quốc hoặc sai cờ quốc gia lọt qua cổng kiểm định.

- [x] **TASK-011: Tối Ưu Hóa Tốc Độ & Chịu Lỗi Hệ Thống (`h264_qsv` GPU Fallback, `KeyRotator` 30s/30m Isolation, `app.log` Handler — `b45b567`, `4faaa33`)**
  - **Mô tả**: Tự động kiểm tra thực tế khả năng khởi tạo `h264_nvenc` và chuyển mượt sang GPU `h264_qsv` (Intel QuickSync) khi NVIDIA Driver `< 610.0`; hạ bậc model treo `Read timed out` trong `30s` và cách ly key hết quota ngày trong `30 phút`; khôi phục `RotatingFileHandler` ghi log vào `storage/logs/app.log`.
  - **File liên quan**: [`app/services/video_processor.py`](file:///d:/Python/Youtube/app/services/video_processor.py), [`app/utils/key_rotator.py`](file:///d:/Python/Youtube/app/utils/key_rotator.py), [`app/utils/logger.py`](file:///d:/Python/Youtube/app/utils/logger.py), [`app/graph/nodes/media/hook_video_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/hook_video_node.py).
  - **Tiêu chí hoàn thành**: Render phần cứng `h264_qsv` hoạt động ổn định, không còn tốn 14 lần thử tạo tiến trình `nvenc` lỗi.

- [x] **TASK-012: Tổng Hợp Toàn Diện Kế Hoạch & Sổ Đen 14 Lỗi Cấm Lặp Lại Từ Mọi Kênh Chat Vào Hệ Thống Bộ Nhớ Ngoài**
  - **Mô tả**: Quét toàn bộ lịch sử các kênh chat (`861ab3b5`, `40c844d4`, `7cfb8e13`, `1b893567`, `b6af5c48`, `a9b608a3`), hợp nhất 14 lỗi cấm lặp lại và toàn bộ các kế hoạch vào `AGENTS.md`, `MEMORY.md`, `working_memory.md`, `todo.md`, `progress_log.md`.
  - **File liên quan**: [`AGENTS.md`](file:///d:/Python/Youtube/AGENTS.md), [`MEMORY.md`](file:///d:/Python/Youtube/MEMORY.md), [`working_memory.md`](file:///d:/Python/Youtube/working_memory.md), [`todo.md`](file:///d:/Python/Youtube/todo.md), [`progress_log.md`](file:///d:/Python/Youtube/progress_log.md).
  - **Tiêu chí hoàn thành**: 100% lỗi cấm và kế hoạch từ mọi phiên chat được lưu trữ có cấu trúc trong Bộ Nhớ Ngoài và đẩy lên `origin/main`.

---

## ⏳ Danh Sách Kế Hoạch Sẵn Sàng Thực Thi / Đang Theo Dõi (Active Backlog & Roadmap)

- [x] **TASK-013: Sản Xuất, Kiểm Định Video Thực Tế (`End-to-End Video QA`) & Lập Lịch Giờ Vàng Lô 12 Video Shorts (`#773`–`#784`) Cho Cả 2 Trụ Cột (`Entity A vs Entity B` & `"Why / Por Que"` Mystery Explainer)**
  - **Mô tả**: Chạy trọn luồng tạo video, kiểm định lưới khung hình thực tế (`VideoQA 98–99/100`, `> 85%–100%` cảnh video thật), lập lịch Khung Giờ Vàng lên YouTube và gửi về Telegram đủ 12/12 video cho 6 kênh (trừ `Paws & Hearts Rescue`).
  - **File liên quan**: [`app/graph/nodes/render_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/render_nodes.py), [`app/services/intelligence/deduplication.py`](file:///d:/Python/Youtube/app/services/intelligence/deduplication.py), [`app/services/video_library.py`](file:///d:/Python/Youtube/app/services/video_library.py), [`app/services/quota_manager.py`](file:///d:/Python/Youtube/app/services/quota_manager.py).
  - **Tiêu chí hoàn thành**: 12/12 video (`#773`–`#784`) đạt `VideoQA 98–99/100`, `0%` trùng lặp, đã lên lịch `SCHEDULED` trên YouTube và gửi về Telegram (`-1001234567890`).

- [ ] **TASK-014: Vận Hành Chiến Lược Hybrid `Shorts (9:16) + 10-Min Long-Form (16:9)` & Lộ Trình 60 Ngày Bật Kiếm Tiền YPP (`longform_launch_report.md` & `competitive_intelligence_and_60day_roadmap.md`)**
  - **Mô tả**: Duy trì nhịp đăng 2 Shorts/ngày/kênh đúng khung giờ vàng Mỹ (`16:15–18:15 UTC` & `01:00–03:00 UTC`) và Brazil (`13:20/20:15 UTC` & `14:25/21:20 UTC`), kết hợp sản xuất phim tài liệu dài `10 phút` (`>= 9m15s`) vào khung `21:30–22:30 UTC` và đẩy phễu bình luận từ Shorts sang Long-Form.
  - **File liên quan**: [`app/services/batch_producer.py`](file:///d:/Python/Youtube/app/services/batch_producer.py), [`app/services/smart_scheduler.py`](file:///d:/Python/Youtube/app/services/smart_scheduler.py), [`app/services/youtube_analytics/engagement_comments_mixin.py`](file:///d:/Python/Youtube/app/services/youtube_analytics/engagement_comments_mixin.py).
  - **Tiêu chí hoàn thành**: Theo dõi CTR, AVD và giờ xem thực tế từ YouTube Analytics API v2 sau 24–48h cho từng đợt phát hành.

- [ ] **TASK-015: Tối Ưu Hóa Giai Đoạn 2 Cho Tốc Độ Render Long-Form 10 Phút (`performance_audit_report.md` — Giảm từ `~26 phút` xuống `~12–15 phút`)**
  - **Mô tả**: Áp dụng cắt clip nhanh `-c copy` (stream copy khi không cần crop) trước bước đồng nhất cuối cùng và gối đầu tải Media theo từng chương ngay khi TTS chương đó hoàn tất.
  - **File liên quan**: [`app/services/video_assembler/scene_renderer.py`](file:///d:/Python/Youtube/app/services/video_assembler/scene_renderer.py), [`app/services/video_assembler/concat_and_transitions.py`](file:///d:/Python/Youtube/app/services/video_assembler/concat_and_transitions.py).
  - **Tiêu chí hoàn thành**: Thời gian tổng hợp 40 cảnh Long-Form giảm 40–50% mà vẫn giữ nguyên chất lượng `1080p` và phụ đề `.ass`.

- [ ] **TASK-016: Cập Nhật NVIDIA Driver Máy Trạm Lên `>= 610.00` Để Mở Khóa Toàn Lực `h264_nvenc` Trên RTX 3050 (`performance_audit_report.md`)**
  - **Mô tả**: Máy trạm hiện dùng NVIDIA Driver `581.29` trong khi bản build FFmpeg hiện tại yêu cầu NVENC API 13.1 (`Driver >= 610.00`). Khi người dùng cập nhật NVIDIA Game Ready / Studio Driver mới nhất, `video_processor.py` sẽ tự động ưu tiên `h264_nvenc` thay vì `h264_qsv`.
  - **File liên quan**: [`app/services/video_processor.py`](file:///d:/Python/Youtube/app/services/video_processor.py).
  - **Tiêu chí hoàn thành**: `ffmpeg -f lavfi -i color=c=black:s=256x256:d=0.1 -c:v h264_nvenc -f null -` trả về exit code `0`.

- [x] **TASK-017: Làm Mới OAuth Token Cho 3 Kênh (`Wild Odyssey`, `Alternate History & "What If?"`, `Psychology of the world`) & Cân Bằng Tải 3 Dự Án Google Cloud**
  - **Mô tả**: Kiểm tra và xác thực lại OAuth cho các kênh sang `ai-youtube-studio-509521` và `youtube-pool-02-510505` khi `bionic-client-509515-h2` chạm trần 6 video/ngày.
  - **File liên quan**: [`app/agents/publisher/oauth_and_upload_mixin.py`](file:///d:/Python/Youtube/app/agents/publisher/oauth_and_upload_mixin.py), [`app/services/quota_manager.py`](file:///d:/Python/Youtube/app/services/quota_manager.py).
  - **Tiêu chí hoàn thành**: Cả 6/6 kênh sản xuất đều xác thực YouTube Data API v3 thành công và chia tải qua 3 Google Cloud Projects.

- [x] **TASK-020: Sản Xuất, Kiểm Duyệt Contact Sheet & Lập Lịch Giờ Vàng Lô 12 Video Shorts Đợt 2 (`#785`–`#796`) + Tối Ưu `ThreadPoolExecutor(48)` & `2-Tier Documentary B-Roll`**
  - **Mô tả**: Sản xuất và lên lịch 12/12 video chủ đề vàng & hot trend cho 6 kênh (trừ `Paws & Hearts Rescue`); mở rộng ThreadPool lên 48 luồng để triệt tiêu nghẽn khi đua 3 nhánh tải video; đồng bộ luật B-Roll đại diện cùng họ sinh học / bối cảnh địa lý tự nhiên giữa `VisualCurationAgent` và `VisualCritic`.
  - **File liên quan**: [`app/graph/nodes/media/fetch_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/fetch_media_node.py), [`app/graph/nodes/media/refine_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/refine_media_node.py), [`app/agents/curation/media_vision_curator_mixin.py`](file:///d:/Python/Youtube/app/agents/curation/media_vision_curator_mixin.py), [`app/graph/evaluators/visual_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/visual_critic.py), [`app/graph/nodes/render_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/render_nodes.py).
  - **Tiêu chí hoàn thành**: 12/12 video (`#785`–`#796`) đạt `VideoQA 97–99/100` (10/12 video đạt `100%` cảnh video chuyển động thật), lên lịch thành công trên YouTube + gửi Telegram; 5/5 video mở `PUBLIC` đầu tiên vượt `1,000–1,381 views` ngay trong đêm đầu.

- [x] **TASK-021: Chặn Tận Gốc Lỗi `P0D` Stub Recovery & Tiêu Đề Placeholder (`Video #735` $\rightarrow$ `Video #Shorts`) Trong `PublisherAgent` + Cứu Hộ Trực Tiếp Trên `Wild Odyssey`**
  - **Mô tả**: Xác minh `contentDetails.duration != 'P0D'` và `processingDetails` trong `_find_live_duplicate_on_youtube` & bộ lọc trùng DB để không bao giờ tái sử dụng bản tải dở 0 giây sau lỗi đứt kết nối mạng; sửa regex hashtag cuối câu và thêm `is_placeholder_video_title()` khôi phục tiêu đề thực thể chuẩn; sửa trực tiếp `gpNPd09SHd8`, cách ly `QEkELKDcVYY` sang `PRIVATE` và tải lại `Job #785` (`9mu7upNH-Mo`, `PT41S`) vào giờ vàng Mỹ `18:15 UTC`.
  - **File liên quan**: [`app/agents/publisher/oauth_and_upload_mixin.py`](file:///d:/Python/Youtube/app/agents/publisher/oauth_and_upload_mixin.py), [`app/agents/publisher/seo_tags_mixin.py`](file:///d:/Python/Youtube/app/agents/publisher/seo_tags_mixin.py).
  - **Tiêu chí hoàn thành**: `9mu7upNH-Mo` đạt `status=processed, duration=PT41S, publishAt=2026-11-11T18:15:00Z`; `gpNPd09SHd8` cập nhật tiêu đề thực thể chuẩn + link Long-Form; pass `py_compile` & smoke test.

- [ ] **TASK-018: Kiểm Toán Định Kỳ 14 Ngày Hệ Thống, Agent & Model (`biweekly-system-model-upgrader`)**
  - **Mô tả**: Quét phiên bản model Gemini/LLM/TTS/Video mới nhất và trình báo cáo so sánh Tiếng Việt trước khi nâng cấp.
  - **File liên quan**: [`config/settings.py`](file:///d:/Python/Youtube/config/settings.py), [`app/utils/key_rotator.py`](file:///d:/Python/Youtube/app/utils/key_rotator.py), [`.agents/skills/biweekly-system-model-upgrader/SKILL.md`](file:///d:/Python/Youtube/.agents/skills/biweekly-system-model-upgrader/SKILL.md).
  - **Tiêu chí hoàn thành**: Có báo cáo so sánh Tiếng Việt và được người dùng xác nhận trước khi áp dụng.
