# 📋 CHANGELOG — AI YouTube Multi-Channel Studio

> File này ghi lại tất cả thay đổi của phiên làm việc để khi push lên Git, bạn Giang tải về sẽ biết đã thay đổi gì.

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
