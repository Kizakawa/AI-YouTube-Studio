# ⚡ working_memory.md — Bộ Nhớ Trạng Thái Hiện Tại ("RAM Ngoài" Của Agent)

> **Nguyên tắc vận hành**:
> - Đọc file này **đầu tiên** (cùng với [`MEMORY.md`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/MEMORY.md) và [`todo.md`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/todo.md)) khi bắt đầu bất kỳ phiên làm việc hoặc yêu cầu mới nào.
> - Cuối mỗi tác vụ (trước khi `git commit & push`), **Main Agent BẮT BUỘC cập nhật lại file này** theo đúng 4 mục: `✅ Đã làm gì`, `❗ Đang kẹt / Lưu ý gì`, `📂 File vừa xử lý`, `➡️ Bước tiếp theo`.

---

## 📌 Snapshot Trạng Thái Hiện Tại (Cập nhật: `2026-11-13 03:10 +07:00`)

- **Nhánh Git hiện tại**: `main` (đã pull hợp nhất `origin/main` `0d4dbdb`, kiểm thử toàn diện `179/179` bài test Pass `100%`, biên dịch sạch `242/242` file Python, render & thẩm định thực tế cả 3 định dạng video đạt chuẩn mượt tuyệt đối, đồng bộ ngược lên GitHub).
- **Trạng thái Auto-Video khi trống lịch**: **ĐANG TẠM NGƯNG THEO LỆNH NGƯỜI DÙNG** (`PAUSE_EMPTY_SCHEDULE_AUTO_VIDEO = True`, `ENABLE_OVERDUE_AUTO_PRODUCTION = False`).
- **Tác vụ vừa hoàn thành**:
  - `TASK-023`: **Đồng bộ GitHub `origin/main`, tích hợp trọn vẹn hệ sinh thái 9 kênh (bổ sung 2 kênh Nhạc Học Tập & Gaming trên `project_pool_7`: `ch_pomodoro_focus_academy` & `ch_lock_in_gaming_mix`), nâng cấp độ mượt hình ảnh/âm thanh toàn cục (2.5x Supersampled Ken Burns, 2.2x Multi-Cut Hook, `tpad=stop_mode=clone`, `bt709` CFR, Pomodoro Living Studio v2.0 portable + English localization) và vượt qua `179/179` pytest + QA video thực tế**.

---

## ✅ 1. Đã Làm Gì (Completed In Recent Turns)
1. **Đồng bộ GitHub & Hợp nhất Kiến Trúc 9 Kênh (`project_pool_1`..`project_pool_7`)**:
   - Pull & rebase sạch các commit mới nhất từ `origin/main` (`2886d2d..0d4dbdb`) và hợp nhất không xung đột với cấu hình cục bộ.
   - Đồng bộ Canonical ID & bí danh cho 2 kênh mới trên `project_pool_7`:
     - **`Pomodoro Focus Academy`** (`ch_pomodoro_focus_academy` / `UCchbsYVxypzr3CAFI1svyMQ` / `@PomodoroFocusAcademy`) — ngách `study_pomodoro_music`, Category `10` (Music).
     - **`LOCK IN // Gaming Mix`** (`ch_lock_in_gaming_mix` / `UC83iaowxxjavnGZ107WendQ` / `@LOCKINGamingMix`) — ngách `gaming_lockin_music`, Category `10` (Music).
   - Hoàn thiện [`config/niches/study_pomodoro_music.yaml`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/config/niches/study_pomodoro_music.yaml), [`config/niches/gaming_lockin_music.yaml`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/config/niches/gaming_lockin_music.yaml), [`assets/bgm/audio_catalog.json`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/assets/bgm/audio_catalog.json) (O(1) lookup cho cả `06_study_music_lofi` và `07_gaming_lockin_hud`), [`app/services/niche_manager.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/services/niche_manager.py), [`app/services/smart_scheduler.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/services/smart_scheduler.py) (tách lệch giờ vàng `-90` / `+90` phút) và [`app/agents/publisher/seo_tags_mixin.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/agents/publisher/seo_tags_mixin.py) (bảo toàn khối Chapters `00:00` / `H:MM:SS` và lọc bỏ `#Shorts` / `#documentary` trên kênh nhạc dài).
2. **Nâng cấp Độ Mượt Hình Ảnh & Chất Lượng Render Video Toàn Hệ Thống**:
   - [`app/utils/ffmpeg/color_and_motion.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/utils/ffmpeg/color_and_motion.py): Chuẩn hóa `2.5x` Supersampled Sub-Pixel `zoompan` + `BT709_COLOR_ARGS` cho toàn bộ 8 chế độ chuyển động camera (`zoom_in`, `zoom_out`, `pan_left`, `pan_right`, `pan_up`, `pan_down`, `hyper_zoom`, `cinematic_drift`) và `2.2x` Supersampled `zoompan` cho `render_opening_hook_multi_cut` (triệt tiêu hoàn toàn hiện tượng giật bậc thang 2px trên `yuv420p`).
   - [`app/utils/ffmpeg/concat_and_transitions.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/utils/ffmpeg/concat_and_transitions.py) & [`app/agents/video_agent.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/agents/video_agent.py): Thay `-stream_loop -1` bằng `tpad=stop_mode=clone` để chống tua giật về giây `0` khi clip ngắn hơn lời thoại.
   - [`app/services/pomodoro/`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/services/pomodoro/video_renderer.py): Sửa nhảy số giây đầu bằng `trunc(t)`, tối ưu bubble cột mốc bằng `setpts=PTS+{m_st}/TB`, rút ngắn fade chuyển bài về `0.35s`, mặc định hóa tiếng Anh (`speech_lang="en"`) cho kênh Mỹ, và đóng gói trọn bộ 16 tranh linh vật/mùa (`assets/mascots/**`, `assets/themes/**`) kèm fallback thư mục phẳng `assets/bgm/06_study_music_lofi`.
3. **Kiểm thử Không Xung Đột & Thẩm Định Video Thực Tế (`verification-before-completion`)**:
   - `py_compile`: `242/242` file Python (`0` lỗi).
   - `pytest`: **`179 passed, 0 failed`** trên toàn bộ kho test (`tests/`).
   - **QA Render Video Thực Tế**:
     - Shorts `9:16` (`1080x1920 @ 30/1` CFR, `bt709`, `48000 Hz` Stereo, `AV Drift = 33.33ms`, `0` frozen frames trên 120 frame pan, `0` lỗi giải mã).
     - Pomodoro Living Studio v2.0 `16:9` (`1920x1080 @ 24/1` CFR, `bt709`, `48000 Hz` Stereo, `AV Drift = 21.33ms`, đếm ngược chuẩn `00:05` ở `t=0.08s`, linh vật thức giấc ở giờ nghỉ, `0` lỗi giải mã).
     - Pomodoro Short Teaser `9:16` (`1080x1920 @ 30/1` CFR, `bt709`, `48000 Hz` Stereo, `AV Drift = 0.0ms`, `0` lỗi giải mã).

---

## ❗ 2. Đang Kẹt Ở Đâu / Lưu Ý Quan Trọng (Active Watchpoints & Constraints)
- **Giữ nguyên khóa tạm ngưng tự động sản xuất video khi trống lịch**: Tuyệt đối không bật lại `PAUSE_EMPTY_SCHEDULE_AUTO_VIDEO` hoặc `ENABLE_OVERDUE_AUTO_PRODUCTION` khi người dùng chưa yêu cầu mở lại.
- Không commit các file nhạy cảm / cục bộ: `.env`, `config/channels.yaml`, `credentials/*`, `*.db`, `*.pickle`, `output/*`, `storage/*`, `**/loops_cache/`.

---

## 📂 3. Các File Vừa Xử Lý (Recently Modified Files)
- [`.gitignore`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/.gitignore), [`config/settings.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/config/settings.py), [`app/database/models.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/database/models.py)
- [`config/niches/study_pomodoro_music.yaml`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/config/niches/study_pomodoro_music.yaml), [`config/niches/gaming_lockin_music.yaml`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/config/niches/gaming_lockin_music.yaml), [`assets/bgm/audio_catalog.json`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/assets/bgm/audio_catalog.json)
- [`app/utils/ffmpeg/color_and_motion.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/utils/ffmpeg/color_and_motion.py), [`app/utils/ffmpeg/concat_and_transitions.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/utils/ffmpeg/concat_and_transitions.py), [`app/utils/ffmpeg/core_encoder.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/utils/ffmpeg/core_encoder.py), [`app/utils/ffmpeg/subtitles_and_thumbnail.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/utils/ffmpeg/subtitles_and_thumbnail.py)
- [`app/services/pomodoro/video_renderer.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/services/pomodoro/video_renderer.py), [`app/services/pomodoro/audio_mixer.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/services/pomodoro/audio_mixer.py), [`app/services/pomodoro/scheduler.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/services/pomodoro/scheduler.py), [`app/services/pomodoro/shorts_cutter.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/services/pomodoro/shorts_cutter.py), [`app/services/pomodoro/seo_generator.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/services/pomodoro/seo_generator.py), [`app/services/pomodoro/thumbnail_generator.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/services/pomodoro/thumbnail_generator.py), [`app/services/pomodoro/cli.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/services/pomodoro/cli.py), [`app/services/pomodoro/batch_producer.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/services/pomodoro/batch_producer.py)
- [`app/agents/publisher/seo_tags_mixin.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/agents/publisher/seo_tags_mixin.py), [`app/agents/publisher/oauth_and_upload_mixin.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/agents/publisher/oauth_and_upload_mixin.py), [`app/services/niche_manager.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/services/niche_manager.py), [`app/services/smart_scheduler.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/services/smart_scheduler.py), [`app/services/video_recycler.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/services/video_recycler.py)
- [`app/graph/tools/tool_schemas.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/graph/tools/tool_schemas.py), [`app/agents/curation/media_vision_curator_mixin.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/agents/curation/media_vision_curator_mixin.py), [`app/services/content_linter.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/services/content_linter.py), [`app/web/routers/videos_and_scripts.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/web/routers/videos_and_scripts.py), [`app/agents/content/script_fallback_and_refine_mixin.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/app/agents/content/script_fallback_and_refine_mixin.py)
- [`tests/test_pomodoro_and_channel_sync.py`](file:///d:/AI_YouTube_Project_Ready/AI-youtube-main/tests/test_pomodoro_and_channel_sync.py)

---

## ➡️ 4. Bước Tiếp Theo (Immediate Next Steps)
1. Khi người dùng cần tạo video cho **`Pomodoro Focus Academy`** hoặc **`LOCK IN // Gaming Mix`**, chạy trực tiếp qua Web Dashboard hoặc `python -m app.services.pomodoro.cli` với kho nhạc đã sẵn sàng.
2. Chỉ mở lại cơ chế tự động bù lịch (`PAUSE_EMPTY_SCHEDULE_AUTO_VIDEO = False`) khi có chỉ đạo trực tiếp từ người dùng.
