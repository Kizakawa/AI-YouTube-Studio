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

- **[2026-11-07 | Tag Audit & Self-Heal] — TASK-013 (SEO Tags & Hashtags Upgrade)**:
  - Kiểm toán trực tiếp qua YouTube Data API v3 toàn bộ 7 kênh (`brandingSettings.channel.keywords`, `country`, `defaultLanguage`) và 12 video Shorts vừa xuất bản (`#773`–`#784`).
  - Nâng cấp tận gốc `build_smart_video_tags_and_hashtags` trong `app/agents/publisher/seo_tags_mixin.py` và `verify_and_heal_video_metadata` trong `app/agents/publisher/metadata_healer_mixin.py`: tự động bóc tách thực thể cấu trúc từ tiêu đề (cả kèo đấu `Entity A vs Entity B`, tiền tố `Entity: ...`, thực thể trong ngoặc đơn `(Macropinna)`, và cụm danh từ chủ ngữ sau `Why / Por Que / How`), nạp `vj.tags` / `vj.topic` từ DB, phân biệt thông minh chủ đề biển sâu vs địa chất trên cạn trong `deep_sea_earth_mysteries`, và đồng bộ (`videos().update`) 100% bộ Tags + Hashtags mới lên cả 12 video trên YouTube.
- `[2026-11-07 | af5977a]` — **Audit 7 Channels, Cross-Promote Long-Form & Enforce Hard VisualCritic Gate**: Audited real YouTube Data API v3 & Analytics API v2 metrics across all 7 channels (`ZnzDxKwlrE4` hit `2,086 views` in `5.9h` @ `355/h`; `88IxuM46wTI` hit `686 views` in `4.8h` @ `143/h`; `HyoOU8_FFyY` hit `7,923 views`), auto-synced pinned debate comments + Long-Form cross-promotion links (`engagement_comments_mixin.py`), switched `DcN_wEKF-Ek` (`Job #776`) to `PRIVATE` on YouTube to protect `jOXGfmsc0w4` (`Job #773` Bismarck vs Swordfish) seed wave, and added a hard `VisualCritic` pass gate in `node_evaluate_video` (`render_nodes.py`).

- `[2026-11-07 | Live-Hot-Script-Benchmark]` — **Direct Live Viral Script Comparison & Benchmark Evaluator Upgrade**: Upgraded `HotChannelScriptBenchmarkEvaluator` (`app/graph/evaluators/hot_channel_script_benchmark.py`) with `_fetch_live_hot_scripts_for_topic` to pull real high-view YouTube transcripts (`yt-dlp` + `YouTubeTranscriptApi`) and verified DB viral winners (`views >= 800`) for direct 1-on-1 script benchmarking, enforcing zero textbook date openers in Scene 1–2, zero adjacent scene repetition, and `<= 24 words` per Short scene.
- `[2026-11-07 | ContentAgent-Hot-Script-Prompt-DNA]` — **System Prompt & YAML Niche Profile Upgrade for Direct Hot-Script Style**: Upgraded `app/agents/content/script_generator_mixin.py`, `app/services/niche_manager.py`, `config/niches/alternate_history.yaml`, and `config/niches/deep_sea_earth_mysteries.yaml` to inject live YouTube viral transcripts directly into the `ContentAgent` writer prompt, eliminate Scene-2 textbook date openers (`"It's May 1941..."`), split Scene 4 (Everyday Contrast Foil) vs Scene 5 (Main-Subject Mechanism Unlock) to eliminate adjacent-scene repetition, and enforce camera-visible physical mic-drop endings.

- **`[2026-11-07 | Native WSL2 Setup on D:\WSL\Ubuntu]` — `TASK-019`**:
  - Chuyển ổ đĩa ảo `ext4.vhdx` của Ubuntu 26.04 LTS sang `D:\WSL\Ubuntu` (giải phóng ổ `C:`), cài đặt `ms-vscode-remote.remote-wsl` + VS Code Server (`~/.vscode-server`) + `uv` + `.venv` Python 3.12 tại `/home/vv1nc/Youtube`.
  - Cập nhật `app/utils/ffmpeg/core_encoder.py` tự động nạp `/usr/lib/wsl/lib` vào `LD_LIBRARY_PATH` trên WSL2, mở khóa trực tiếp card đồ họa rời **`NVIDIA GeForce RTX 3050 (`h264_nvenc`)`**.

- **`[2026-11-11 | Batch 12 Golden Shorts #785–#796 & 48-Worker Media Race]` — `TASK-017` & `TASK-020`**:
  - Sản xuất, kiểm duyệt lưới Contact Sheet thực tế (`VideoQA 97–99/100`, 10/12 video đạt `100%` cảnh video chuyển động thật, `0` thẻ `_graphic.png`), lập lịch Giờ Vàng Mỹ/Brazil và gửi Telegram đủ **12/12 video (`#785`–`#796`)** trên 6 kênh (`Wild Odyssey`, `Odisseia Selvagem`, `The mysteries of the world`, `Mistérios do Mundo`, `Alternate History & "What If?"`, `Psychology of the world`); 5/5 video mở `PUBLIC` đầu tiên (`qPjIurKn31M`, `kxXGbVg-DaI`, `9tGz0HoXvEY`, `Xfu44uwMnzc`, `dJvBwdTaM84`) đều vượt mốc **`1,008–1,381 views`** ngay sau 6.5–14 giờ.
  - Sửa tận gốc nghẽn ThreadPool mặc định 12 luồng bằng `ThreadPoolExecutor(max_workers=48)` trong `fetch_media_node.py` và `refine_media_node.py`; đồng bộ quy tắc `2-Tier Documentary B-Roll` cho phân loài đặc hữu & bối cảnh địa lý biệt lập trong `media_vision_curator_mixin.py` và `visual_critic.py`; khóa kiểm tra `>= 85%` video chuyển động trong `checkpoint_store.py` và bảo vệ trạng thái `VisualCritic` sau `Pre-Render Rescue` trong `render_nodes.py`.
