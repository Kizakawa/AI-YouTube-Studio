# 📋 CHANGELOG — AI YouTube Multi-Channel Studio

> File này ghi lại tất cả thay đổi của phiên làm việc để khi push lên Git, bạn Giang tải về sẽ biết đã thay đổi gì.

> 💡 **TÓM TẮT DÀNH CHO GIANG (ĐỌC NHANH KHI KÉO CODE VỀ):**
> 1. **Tích hợp kho Google Drive 20TB:** Đã thêm endpoint upload và nút "☁️ Tải lên Drive 20TB" trực tiếp trên Web Dashboard + hiển thị dung lượng trong System Health.
> 2. **Render & Tải Video trực tiếp trên Web:** Đã thêm nút "🚀 Render" và "📥 Tải Video Về Máy" trên Web Dashboard (không bắt buộc phải qua Telegram).
> 3. **Fix 3 Bug giao diện & logic:** Sửa tràn viền bảng Pipeline, sửa lỗi báo sai trạng thái lỗi, và cập nhật badge khi chạy chế độ Web-Only.
> 4. **Xử lý 8 điểm kẹt luồng:** Đã bọc timeout, non-blocking async và auto-recovery cho pipeline render.
> 5. **Về Telegram Bot:** Render của Giang vẫn giữ nguyên cấu hình chạy bot chính `@VV1n_mybot` như cũ, không bị ảnh hưởng hay xung đột gì!

## 🗓️ [2026-09-24] — Quản Lý Đa Tài Khoản OAuth, Fix Lỗi YouTube Upload & Đăng Công Khai (Public)

### ✅ ĐÃ HOÀN THÀNH

#### 1. Quản Lý Kênh & Tài Khoản Google Trực Tiếp Trên Web Studio
- **Tab Kênh & Tài Khoản mới:** Giao diện cho phép xem danh sách tài khoản Google, trạng thái kết nối YouTube/Drive, dung lượng Drive và quota YouTube còn lại.
- **Xác thực OAuth trên Web:** Thêm modal hỗ trợ đăng nhập OAuth trực tiếp cho từng tài khoản (Account 1, 2, 3...) thay vì phải chạy script terminal.
- **Thêm / Xóa tài khoản:** Hỗ trợ upload file `client_secret.json` hoặc tạo mới tài khoản trực tiếp từ giao diện Web Studio.
- **Khắc phục lỗi OAuth insecure_transport:** Đã thêm cấu hình `OAUTHLIB_INSECURE_TRANSPORT = 1` trong môi trường local để tránh lỗi `(insecure_transport) OAuth 2 MUST utilize https`.

#### 2. Sửa Lỗi YouTube Upload (401 Unauthorized / youtubeSignupRequired)
- **Nâng cấp YouTube Scope:** Mở rộng từ `https://www.googleapis.com/auth/youtube.upload` lên `https://www.googleapis.com/auth/youtube` (full scope). Scope cũ quá hẹp khiến YouTube Data API từ chối khi vừa tạo metadata (title, description, tags) vừa upload video.
- **Tự động làm mới Token (Auto-refresh Token):** Bổ sung cơ chế tự động refresh token trong `PublisherAgent` khi access token hết hạn (`creds.refresh(Request())`), tránh lỗi token hết hạn giữa chừng.

#### 3. Chuyển Chế Độ Đăng Video Sang Công Khai (Public)
- **PublisherAgent:** Đổi mặc định `privacy_status` từ `"private"` sang `"public"`. (Nếu có hẹn giờ `schedule_time_iso`, YouTube API vẫn tự động đặt private chờ giờ công chiếu).
- **Web Studio Server:** API endpoint `POST /api/videos/{job_id}/publish-youtube` mặc định đăng video ở chế độ `public`.
- **Telegram Bot:** Cập nhật lệnh xuất bản video sang chế độ Công khai (Public) và cập nhật thông báo phản hồi cho người dùng.

---

## 🗓️ [2026-09-24] — Tích Hợp Google Drive 20TB

### ✅ ĐÃ HOÀN THÀNH

#### 1. Xác thực Google Drive OAuth (auth_google.py)
- ✅ Thêm fix UTF-8 encoding cho Windows console (hết lỗi `UnicodeEncodeError`)
- ✅ Cải tiến `run_local_server()` hiển thị link xác thực rõ ràng thay vì âm thầm mở browser
- ✅ File `drive_token.json` được quản lý trong thư mục `credentials/accounts/account_1/` (đã được .gitignore bảo vệ an toàn)
- 🔑 Kho lưu trữ Google Drive Ultra: Đã kết nối và xác thực OAuth thành công (dung lượng 20TB)

#### 2. System Health trên Web Dashboard
**File thay đổi:** `app/web/static/index.html`, `app/web/static/js/dashboard.js`, `app/web/server.py`
- ✅ Thêm ô **"Google Drive 20TB"** vào bảng Trạng Thái Hệ Thống
- ✅ API `/api/health` trả thêm `drive_connected` và `drive_quota_label` (kiểm tra token thật)
- ✅ Hiển thị `🟢 20 TB Google One (Đã kết nối)` nếu token hợp lệ, `🟡 Chưa xác thực` nếu chưa

#### 3. Nút "☁️ Tải lên Drive 20TB" trên Popup Video
**File thay đổi:** `app/web/static/js/dashboard.js`, `app/web/server.py`
- ✅ Endpoint mới `POST /api/videos/{job_id}/upload-drive` — upload thực tế lên Drive, trả về link ngay
- ✅ Nút xuất hiện trong popup chi tiết video khi status = RENDERED hoặc PUBLISHED
- ✅ Sau khi upload thành công, nút tự chuyển thành `📂 Mở Link Drive` (link thật)
- ✅ Tự tạo thư mục `AI YouTube Shorts` trên Drive nếu chưa có
- ✅ Tự cấp quyền xem công khai (`anyone reader`) để cả nhóm click link là xem được video gốc

#### 4. Cập nhật lệnh /status trên Telegram Bot
**File thay đổi:** `app/bot/handlers.py`
- ✅ Không còn báo hardcoded "Sẵn sàng 🟢" giả tạo nữa
- ✅ Kiểm tra file `drive_token.json` thật sự để báo chính xác: `Sẵn sàng 🟢 (20TB)` hoặc `Chưa xác thực ⚠️`

---



### 📌 LƯU Ý QUAN TRỌNG VỀ TELEGRAM BOT GIỮA LOCAL & RENDER
- **Vấn đề gặp phải:** Telegram Bot API không cho phép 2 máy chủ cùng chạy Polling trên 1 Bot Token. Nếu máy Local (Vương) và Render (Giang) cùng poll token `@VV1n_mybot`, Telegram sẽ ngắt kết nối liên tục (lỗi `409 Conflict`).
- **Giải pháp đã thống nhất:**
  1. **Production Bot (Render của Giang):** Vẫn dùng bot chính **`@VV1n_mybot`** (Token cấu hình trên Render Environment).
  2. **Development Bot (Local của Vương):** Dùng bot phụ **`@Megatron_Deception_bot`** (Token cấu hình trong `.env` local).
  3. **Cách thức test:** Vương chat riêng 1-1 với `@Megatron_Deception_bot`. Dữ liệu sẽ ghi thẳng vào SQLite local và đồng bộ tức thì lên Web Dashboard `http://127.0.0.1:8000`.
  4. **Lưu ý khi Giang pull code về:** Render của Giang vẫn giữ nguyên token bot chính trong biến môi trường của Render, không cần thay đổi gì. Cả 2 có thể cùng chạy và cùng phát triển 24/7 mà không sợ xung đột!

---

## 🗓️ [2026-09-24] — Fix 3 Bug: Tràn viền, Xuất Video, Bot Telegram

### 🐛 ĐÃ SỬA

---

### Bug #1: Tràn viền bảng Pipeline
**File thay đổi:** `app/web/static/css/dashboard.css`
- ✅ Thêm `overflow-x: auto` cho `#pipeline-table-container` → cuộn ngang khi bảng rộng
- ✅ Thêm `overflow: hidden` cho `.panel-card` → ngăn content tràn card
- ✅ Thêm `min-width: 0; overflow-x: hidden` cho `.content-body` → fix flex item overflow

### Bug #2: Nút "Xuất Video" hiện sai LỖI + không tải được
**File thay đổi:** `app/web/static/js/dashboard.js`, `app/web/server.py`, `app/services/pipeline.py`
- ✅ Modal chỉ hiện "LỖI" khi status = FAILED (trước đây hiện cả dirty data)
- ✅ Thêm section "🎬 Video Đã Sẵn Sàng!" + nút "📥 Tải Video Về Máy" khi RENDERED
- ✅ Endpoint mới `GET /api/videos/{id}/download` → serve file MP4 trực tiếp
- ✅ Pipeline tự clear `error_message = None` khi status = RENDERED/PUBLISHED
- ✅ Cleanup DB: xóa dirty data "Đang render video..." cho job #1

### Bug #3: Bot Telegram không phản hồi (khi chạy web-only)
**File thay đổi:** `app/web/server.py`, `app/web/static/js/dashboard.js`
- ✅ Health API trả thêm `web_only: true/false`
- ✅ Sidebar badge đổi thành "⚠️ Chỉ Web — Bot Tele tắt" (màu vàng) khi web-only
- ✅ System Health panel Telegram Bot hiện "⚠️ Web-Only mode — Bot polling tắt"

---

## 🗓️ [2026-09-24] — Phần 4: Giám Sát API Token + Phần 5: Rà Soát Logic Kẹt Luồng

### ✅ ĐÃ HOÀN THÀNH

---

### 4. Giám Sát API Token (Gemini & Pexels)
**File thay đổi:** `app/web/server.py`, `app/web/static/index.html`, `app/web/static/js/dashboard.js`, `app/web/static/css/dashboard.css`
- ✨ **Endpoint mới** `GET /api/token-health` — kiểm tra thực tế từng API key
  - Gemini: gọi `list models` (lightweight, 0 quota cost) → phát hiện key invalid, hết quota
  - Pexels: gọi search nhẹ → đọc `X-Ratelimit-Remaining` header → biết quota còn lại
  - Kiểm tra song song bằng `ThreadPoolExecutor` → nhanh hơn tuần tự
- ✨ **Widget Token Monitor trên Dashboard** — hiển thị gauge % tròn (radial SVG)
  - Vòng tròn màu xanh → vàng → đỏ tùy theo % key hoạt động
  - Chi tiết từng key: prefix, trạng thái (✅/⚠️/❌), quota còn lại
  - Nút "Kiểm Tra" gọi API on-demand (không tự poll để tiết kiệm quota)

### 5. Rà Soát & Sửa 8 Lỗi Logic Kẹt Luồng
**File thay đổi:** `app/web/server.py`, `app/agents/content_agent.py`, `app/services/pipeline.py`, `app/database/models.py`

| # | Lỗi đã sửa | File |
|---|-----------|------|
| 1 | 🔴 Web render crash → status kẹt RENDERING vĩnh viễn | `server.py` — thêm `try/finally` + `_mark_job_failed()` |
| 2 | 🟡 `_call_api()` kẹt 180s khi tất cả keys fail | `content_agent.py` — thêm tổng timeout 60s |
| 3 | 🟡 Blocking HTTP trong async event loop | `pipeline.py` — bọc `asyncio.to_thread()` |
| 5 | 🔴 Pipeline không có timeout tổng | `server.py` — `asyncio.wait_for(timeout=600)` |
| 6 | 🟡 Spam nút Render trên Web → nhiều thread song song | `server.py` — check RENDERING count trước khi cho phép |
| 7 | 🟡 Stuck jobs không recover khi web-only restart | `server.py` — `on_startup()` gọi recover |
| 8 | 🟢 `log_activity()` double commit gây SQLite busy | `models.py` — bỏ `db.commit()` thừa |

---

## 🗓️ [2026-09-24] — Đồng bộ DB Web ↔ Telegram & Dashboard tương tác

### ✅ ĐÃ HOÀN THÀNH

---

### 1. Dashboard Stat Cards — Tất cả đều CLICK ĐƯỢC
**File thay đổi:** `app/web/static/index.html`, `app/web/static/css/dashboard.css`, `app/web/static/js/dashboard.js`
- ✨ **Tổng Số Video** → click → nhảy sang tab Pipeline (filter: Tất cả)
- ✨ **Kịch Bản Chờ Render** → click → nhảy sang Pipeline (filter: SCRIPT_READY)
- ✨ **Kênh Hoạt Động** → click → nhảy sang tab Kênh & Tài Khoản
- ✨ **Thành Viên & Hoạt Động** → click → nhảy sang tab Thành Viên
- Thêm CSS hover glow, hint text "Xem Pipeline →" hiện ra khi hover

---

### 2. Pipeline — Thêm cột "Nguồn" & Nút Render/Xuất video
**File thay đổi:** `app/web/static/js/dashboard.js`, `app/web/static/index.html`
- ✨ Cột **Nguồn** hiển thị badge: `📱 Telegram` hoặc `🌐 Web` — phân biệt rõ video nào tạo từ đâu
- ✨ Nút **🚀 Render** hiển thị cho các job trạng thái `SCRIPT_READY` → Kích hoạt render ngay từ web
- ✨ Nút **📤 Xuất** hiển thị cho các job trạng thái `RENDERED` → Xem chi tiết & xuất bản

---

### 3. API Backend — Endpoint render video từ Web
**File thay đổi:** `app/web/server.py`
- ✨ `POST /api/videos/{job_id}/render` — Trigger render từ Web Dashboard
  - Validate job: phải có `scenes_json`, không đang render, phải tồn tại
  - Chạy `VideoPipeline` trong `BackgroundTasks` (non-blocking)
  - Tự động cập nhật `status=RENDERING`, `progress_percent=0` ngay khi gọi
- ✨ API `/api/videos` thêm field `source` (`web` / `telegram`) và `user_id` — Web biết chính xác nguồn tạo video

---

### 4. Đồng bộ Database — Xác nhận Web & Telegram dùng chung 1 DB
**Kết quả rà soát:**
- Web Dashboard (`server.py`) và Telegram Bot (`handlers.py`) đều import `get_db()` từ cùng `app/database/models.py`
- Cùng đọc/ghi bảng `video_jobs`, `activity_logs`, `users` — đảm bảo lịch sử video đồng bộ 100%
- Video tạo từ Telegram (user_id > 0) và Web (user_id = 0) đều hiển thị đầy đủ trong Pipeline
- Tất cả hoạt động (activity logs) từ Telegram đều hiển thị trong tab Nhật Ký Hoạt Động trên web

---

## 🗓️ [2026-09-23] — Phiên tích hợp API & Nâng cấp hệ thống

### ✅ ĐÃ HOÀN THÀNH

---

### 1. Tích hợp API Keys vào `.env`
**File thay đổi:** `.env`
- Thêm `TELEGRAM_BOT_TOKEN` — Bot Telegram đã kết nối
- Thêm `ALLOWED_TELEGRAM_GROUPS` — Chỉ cho phép nhóm cụ thể
- Thêm `PEXELS_API_KEY` — API key tìm video/ảnh stock miễn phí
- Thêm `GEMINI_API_KEYS` — Pool 3 API key Gemini (xoay vòng tự động)

---

### 2. Nâng cấp MediaAgent — Pool xoay vòng Pexels API Key
**File thay đổi:** `app/agents/media_agent.py`, `config/settings.py`
- MediaAgent hỗ trợ nhiều Pexels API key xoay vòng bằng `itertools.cycle`
- Tương tự cơ chế xoay vòng Gemini Key đã có
- Thêm `search_photos()` — tìm ảnh Pexels (dùng cho Ken Burns effect)
- Thêm `search_videos()` — tìm video Pexels portrait 9:16
- Settings mới: `PEXELS_API_KEYS_RAW`, `get_pexels_keys_pool()`

---

### 3. ⭐ HỆ THỐNG HÀNG ĐỢI RENDER (FIFO Queue) — MỚI
**File mới:** `app/services/render_queue.py`
**File thay đổi:** `app/bot/handlers.py`, `run.py`

#### Vấn đề trước đây:
- Khi nhiều người cùng bấm "Dựng Video", hệ thống chạy render song song
- Gây quá tải CPU/RAM, crash FFmpeg, xung đột file

#### Giải pháp:
- **RenderQueue** — Singleton FIFO queue dùng `asyncio.Queue`
- Tại mỗi thời điểm, chỉ có **1 video** đang render
- Các yêu cầu tiếp theo tự động **xếp hàng chờ** và render tuần tự
- Worker loop tự động nghỉ khi queue trống, tự động khởi động lại khi có item mới

#### Lệnh Telegram mới:
| Lệnh | Mô tả |
|-------|--------|
| `/queue` | Xem trạng thái hàng đợi render (đang render gì, đang chờ bao nhiêu) |
| `/cancel <job_id>` | Hủy video đang chờ trong hàng đợi (không hủy được video đang render) |

#### Cách hoạt động:
```
Người A: /render 1  →  🟢 Render ngay (máy rảnh)
Người B: /render 2  →  ⏳ Xếp hàng #1 (đang chờ Video #1)
Người C: /render 3  →  ⏳ Xếp hàng #2
                    ...Video #1 xong → Tự động render Video #2 → rồi Video #3
```

---

### 4. Merge code từ Upstream (Giang)
**File resolve conflict:**
- `app/web/server.py` — Gộp UI updates + features của Giang
- `app/web/static/js/dashboard.js` — Thống kê + channel cards
- `app/utils/ffmpeg_helper.py` — Logic tìm đường dẫn FFmpeg tự động
- `app/agents/media_agent.py` — Pexels integration

---

### 5. Cố định Model AI
**File thay đổi:** `app/agents/content_agent.py`
- Force dùng `models/gemini-3.6-flash` thay vì `gemini-2.5-flash` (không khả dụng)
- Đảm bảo tất cả máy trong nhóm dùng cùng 1 model

---

### 6. Database Auto-Migration
**File thay đổi:** `app/database/models.py`
- Tự động thêm cột mới khi schema thay đổi (không cần xóa DB cũ)
- Các cột mới: `scenes_json`, `audio_path`, `audio_duration_ms`, `render_manifest`, `video_path`

---

### 7. 🌟 TÍNH NĂNG MỚI BẠN GIANG VỪA BỔ SUNG (ĐÃ GỘP THÀNH CÔNG)
**File thay đổi:** `app/agents/content_agent.py`, `app/agents/image_agent.py`, `app/agents/media_agent.py`, `app/services/pipeline.py`, `app/bot/handlers.py`

#### A. Tùy biến Media trực tiếp qua Telegram (Custom Media Upload):
- **Gửi ảnh/video vào chat:** Kèm caption `/scene <số_cảnh>` (ví dụ: `/scene 1` hoặc `#1`) để gán chính xác ảnh/video đó cho phân cảnh bạn muốn.
- **Lệnh URL:** `/media <số_cảnh> <link_ảnh_hoặc_video>` để tải ảnh/video từ link web về gán cho cảnh.
- File được lưu vào `storage/jobs/{job_id}/custom_media/scene_{scene_id}.{ext}` và được ưu tiên cao nhất khi dựng video!

#### B. Tìm kiếm ảnh Web miễn phí (Wikimedia Commons):
- Bổ sung hàm `search_web_photos()` trong `MediaAgent`.
- 100% miễn phí, không cần API Key, tự động chạy khi không có key Pexels hoặc Pexels hết hạn.

#### C. Hệ thống phân cấp Media 5 tầng thông minh trong Pipeline:
1. **Tầng 0:** User Custom Media (Ảnh/Video người dùng gửi qua Telegram hoặc URL)
2. **Tầng 1:** Pexels Video 9:16 portrait
3. **Tầng 2:** Pexels Photo (cho hiệu ứng Ken Burns)
4. **Tầng 3:** Web Image Search (Wikimedia Commons)
5. **Tầng 4:** AI Image Generation (Google Imagen 3 / Gemini Flash Image)
6. **Tầng 5:** Dynamic Aesthetic Graphic (ảnh gradient điện ảnh offline)

#### D. Nâng cấp AI Image & Kịch bản Viral:
- Tích hợp Imagen 3 / Gemini Flash Image với cơ chế fallback thông minh.
- Tối ưu hóa prompt kịch bản Viral Shorts trong `ContentAgent`.

---

### 8. 🚀 ĐỢT CẬP NHẬT MỚI: CLOUD DEPLOYMENT, AUTH GOOGLE & FIX RENDER
**Commits:** `156a441`, `cf134ae`, `1e52b59`, `6845c57`, `72888ea`, `8778cb6`, `ecc9ffe`
**File mới:** `Dockerfile`, `.dockerignore`, `auth_google.py`
**File thay đổi:** `run.py`, `app/web/server.py`, `config/settings.py`, `app/utils/ffmpeg_helper.py`, `app/bot/handlers.py`, `app/database/models.py`

#### A. Triển khai Cloud & Docker:
- Thêm `Dockerfile` chuẩn hóa chạy song song Telegram Bot + Web FastAPI trên nền Python 3.11-slim + FFmpeg + fonts.
- Hỗ trợ biến môi trường `PORT` động cho Render.com, VPS, Cloud Docker.
- Endpoint `/health` mới cho Render Uptime và Health Checks.
- Tự động lấy URL công khai qua `RENDER_EXTERNAL_URL` trong lệnh Telegram `/status`.

#### B. Xác thực Google OAuth 1-Click (`auth_google.py`):
- Tách riêng quyền YouTube (`youtube.upload`) và Google Drive 2TB (`drive.file`) để giải quyết lỗi Google Error 400: `scopes that cannot be requested together`.
- Tự động sinh file mã hóa Base64 cho biến môi trường trên Render/Cloud.

#### C. Khắc phục lỗi FFmpeg Render & Đứng hình (Freeze frames):
- Chuẩn hóa video fps 30 CFR (`-fps_mode cfr`), preset `veryfast`.
- Thêm `-stream_loop -1` cho video ngắn hơn âm thanh để lặp mượt mà.
- Re-encode concat thay vì `-c copy` để chống lệch timebase giữa các stock clips.
- Bổ sung tham số `-threads` (qua biến `FFMPEG_THREADS=2`) chống tràn RAM (OOM) trên Cloud.

#### D. Tối ưu hóa Web Dashboard & In-Memory Caching (Khắc phục giật lag):
- Cache `is_ffmpeg_installed()` và `get_ffmpeg_version()` vào bộ nhớ RAM (không gọi `subprocess.run` lặp đi lặp lại mỗi lần web polling).
- Cache danh sách `channels.yaml` theo file mtime để không parse đĩa liên tục.
- Bổ sung `safe_query_answer` chống timeout Telegram `Query is too old`.

#### ⚠️ LÝ DO WEB TRÊN RENDER BỊ "ĐƠ / LOAD KHÁ LÂU":
1. **Cold Start (Ngủ đông) của Render Free:** Render tự động tắt máy ảo sau 15 phút không có người dùng. Lần đầu mở lại web, Render mất từ **50s - 2 phút** để khởi động lại container Docker.
2. **Cấu hình 0.1 CPU / 512MB RAM của gói Free:** Quá ít tài nguyên khi chạy cùng lúc cả Bot polling + Web Uvicorn + SQLite. Cần dùng UptimeRobot ping URL `/health` mỗi 5-10 phút để giữ server không ngủ đông.

---

## 📁 DANH SÁCH FILE THAY ĐỔI

```
Thay đổi (Modified):
├── .env                          ← Thêm API keys
├── config/settings.py            ← Thêm Pexels key pool
├── app/agents/media_agent.py     ← Pexels search + key rotation
├── app/agents/content_agent.py   ← Force gemini-3.6-flash
├── app/bot/handlers.py           ← Tích hợp RenderQueue + lệnh /queue, /cancel
├── app/database/models.py        ← Auto-migration schema
├── app/web/server.py             ← Merge UI updates
├── app/web/static/js/dashboard.js ← Merge thống kê
├── app/utils/ffmpeg_helper.py    ← Merge tìm FFmpeg tự động
└── run.py                        ← Đăng ký lệnh /queue, /cancel

Thêm mới (Added):
├── app/services/render_queue.py  ← ⭐ Hệ thống hàng đợi FIFO
└── CHANGELOG.md                  ← File này
```

---

## 🔧 HƯỚNG DẪN SAU KHI PULL CODE

### Bước 1: Pull code mới
```bash
git pull origin main
```

### Bước 2: Kiểm tra `.env`
Đảm bảo file `.env` có đầy đủ:
```env
TELEGRAM_BOT_TOKEN=...
ALLOWED_TELEGRAM_GROUPS=...
PEXELS_API_KEY=...
GEMINI_API_KEYS=key1,key2,key3
```

### Bước 3: Cài dependencies (nếu có thêm)
```bash
pip install -r requirements.txt
```

### Bước 4: Chạy bot
```bash
python run.py
```

---

## ⚠️ LƯU Ý QUAN TRỌNG

1. **Mỗi máy chạy 1 instance bot** — Không chạy 2 instance cùng 1 Token
2. **Hàng đợi render** chỉ hoạt động trong 1 instance (per-machine)
3. **Pexels API miễn phí** — 200 requests/giờ, không cần thẻ tín dụng
4. **Gemini API key xoay vòng** — Nếu 1 key hết quota, tự chuyển key tiếp theo
