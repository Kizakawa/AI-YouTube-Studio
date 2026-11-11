# ⚡ working_memory.md — Bộ Nhớ Trạng Thái Hiện Tại ("RAM Ngoài" Của Agent)

> **Nguyên tắc vận hành**:
> - Đọc file này **đầu tiên** (cùng với [`MEMORY.md`](file:///d:/Python/Youtube/MEMORY.md) và [`todo.md`](file:///d:/Python/Youtube/todo.md)) khi bắt đầu bất kỳ phiên làm việc hoặc yêu cầu mới nào.
> - Cuối mỗi tác vụ (trước khi `git commit & push`), **Main Agent BẮT BUỘC cập nhật lại file này** theo đúng 4 mục: `✅ Đã làm gì`, `❗ Đang kẹt / Lưu ý gì`, `📂 File vừa xử lý`, `➡️ Bước tiếp theo`.

---

## 📌 Snapshot Trạng Thái Hiện Tại (Cập nhật: `2026-11-11 10:52 +07:00`)

- **Nhánh Git hiện tại**: `main` (đồng bộ sạch với `origin/main`).
- **Tác vụ vừa hoàn thành**:
  - `TASK-020` & `TASK-017`: **Sản xuất, kiểm duyệt Contact Sheet, làm mới OAuth 3 dự án Google Cloud & lập lịch Giờ Vàng hoàn tất 12/12 Video Shorts (`#785`–`#796`) cho 6 kênh** (trừ `Paws & Hearts Rescue`), gửi đầy đủ 12 file video + bảng tổng kết về Telegram (`-1001234567890`, `message_id=2470..2498`).
  - **Hiệu suất thực tế vượt trội ngay sau đêm đầu tiên (YouTube Data API v3 lúc `10:50 AM 11/11/2026`)**: **5/5 video đầu tiên vừa mở `PUBLIC` qua đêm đều đã vượt mốc `1,000+ views` chỉ sau 6.5–14 giờ**:
    - `#786` (`Odisseia Selvagem` — `qPjIurKn31M`): **`1,381 views`, `34 likes` (`2.46%`)**
    - `#787` (`Odisseia Selvagem` — `kxXGbVg-DaI`): **`1,111 views`, `19 likes` (`1.71%`)**
    - `#790` (`Mistérios do Mundo` — `Xfu44uwMnzc`): **`1,078 views`, `19 likes` (`1.76%`)**
    - `#788` (`The mysteries of the world` — `9tGz0HoXvEY`): **`1,056 views`, `7 likes`**
    - `#791` (`Mistérios do Mundo` — `dJvBwdTaM84`): **`1,008 views`, `12 likes`**

---

## ✅ 1. Đã Làm Gì (Completed In Recent Turns)
1. **Sản xuất, Kiểm định Lưới Hình Ảnh & Lập Lịch Giờ Vàng 12/12 Video Shorts (`#785`–`#796`)**:
   - **Wild Odyssey (`ch_wild_odyssey`)**:
     - `#785` (*Giant Anteater vs Jaguar: Why 4-Inch Claws Make Big Cats Back Off 🐜🐆 #Shorts*, `40.07s`, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/QEkELKDcVYY` (`PUBLIC`).
     - `#794` (*Why Woodpeckers Slam Their Heads at 1,200 Gs Without Brain Damage 🐦🪵 #Shorts*, `38.27s`, `6/6` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/jyGdt27Xwtg` (`10:00 Sáng T5, 12/11/2026 VN`).
   - **Odisseia Selvagem (`ch_odisseia_selvagem`)**:
     - `#786` (*Bico-de-Sapato vs Crocodilo-do-Nilo: O Bico de Guilhotina no Pântano 🦤🐊 #Shorts*, `36.27s`, `7/8` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/qPjIurKn31M` (`PUBLIC` — `1,381 views`).
     - `#787` (*Por Que o Pica-Pau Bate o Crânio a 1.200G Sem Desmaiar? 🐦🪵 #Shorts*, `25.83s`, `7/7` video, `VideoQA 97/100`) $\rightarrow$ `https://www.youtube.com/shorts/kxXGbVg-DaI` (`PUBLIC` — `1,111 views`).
   - **The mysteries of the world (`ch_the_mysteries_of_the_world`)**:
     - `#788` (*Snake Island Brazil: Why the Navy Forbids Any Human From Entering 🐍🏝️ #Shorts*, `37.87s`, `8/8` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/9tGz0HoXvEY` (`PUBLIC` — `1,056 views`).
     - `#789` (*Blood Falls Antarctica: Why a White Glacier Bleeds Deep Red Water 🩸🧊 #Shorts*, `31.03s`, `7/7` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/Bqa3zoWwjyU` (`00:45 Sáng T5, 12/11/2026 VN`).
   - **Mistérios do Mundo (`ch_misterios_do_mundo`)**:
     - `#790` (*Cachoeira de Sangue na Antártida: Por Que Essa Geleira Sangra a -17°C? 🩸🧊 #Shorts*, `31.53s`, `6/6` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/Xfu44uwMnzc` (`PUBLIC` — `1,078 views`).
     - `#791` (*Costa dos Esqueletos: Por Que Mil Navios Estão no Meio do Deserto? 🚢🏜️ #Shorts*, `34.03s`, `7/7` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/dJvBwdTaM84` (`PUBLIC` — `1,008 views`).
   - **Alternate History & "What If?" (`@alternatehistorywhatif-e2x`)**:
     - `#795` (*1932 Great Emu War: 10,000 Machine Gun Rounds vs 20,000 Birds 🦤🔫 #Shorts*, `43.90s`, `6/6` video, `VideoQA 98/100`) $\rightarrow$ `https://www.youtube.com/shorts/6mvhSZds13U` (`23:15 Đêm T4, 11/11/2026 VN`).
     - `#796` (*Project Habakkuk: 2-Million-Ton Ice Aircraft Carrier vs U-Boats 🧊🚢 #Shorts*, `33.43s`, `8/8` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/bFmlknLSTlU` (`08:00 Sáng T5, 12/11/2026 VN`).
   - **Psychology of the world (`ch_psychology_of_the_world`)**:
     - `#792` (*Cognitive Load Stare: Why Liars Stare Straight Without Blinking 🧠👁️ #Shorts*, `39.17s`, `7/7` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/e7nfVRO_FRc` (`23:45 Đêm T4, 11/11/2026 VN`).
     - `#793` (*The Decoy Pricing Trap: How Movie Theaters Trick Your Brain 🍿🧠 #Shorts*, `31.03s`, `7/7` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/9mPfUnGHx0c` (`08:30 Sáng T5, 12/11/2026 VN`).
2. **Sửa tận gốc 5 điểm nghẽn kiến trúc để đạt tỷ lệ `100%` cảnh video chuyển động thật ở 10/12 video**:
   - [`app/graph/nodes/media/fetch_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/fetch_media_node.py) & [`app/graph/nodes/media/refine_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/refine_media_node.py): Mở rộng `ThreadPoolExecutor(max_workers=48)` cho toàn bộ Shorts & Long-Form (triệt tiêu nghẽn thread pool mặc định 12 luồng khi đua song song 3 nhánh video), và cấp lại context `bind_scene_cancellation` mới 20s trước khi fallback ảnh để không bao giờ rớt về `_graphic.png`.
   - [`app/agents/curation/media_vision_curator_mixin.py`](file:///d:/Python/Youtube/app/agents/curation/media_vision_curator_mixin.py) & [`app/graph/evaluators/visual_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/visual_critic.py): Đồng bộ **Quy tắc B-Roll Phim Tài Liệu 2 Tầng (`2-Tier Documentary B-Roll Rule`)** cho phân loài đặc hữu và địa danh tự nhiên biệt lập (đảo, sông băng, bờ biển sa mạc), đồng thời chặn tuyệt đối ảnh có dán sticker/emoji mặt cười (`visual_integrity_score <= 2.0`).
   - [`app/graph/checkpoint_store.py`](file:///d:/Python/Youtube/app/graph/checkpoint_store.py): Tự động vô hiệu hóa checkpoint `fetch_media` cũ nếu tỷ lệ cảnh video chuyển động `< 85%`.
   - [`app/graph/nodes/media/hook_video_helpers.py`](file:///d:/Python/Youtube/app/graph/nodes/media/hook_video_helpers.py) & [`app/graph/nodes/render_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/render_nodes.py): Hiệu chuẩn ngưỡng chuyển động sau nén x264 cho cảnh flycam thiên nhiên thật và giữ nguyên kết quả `VisualCritic` đã `passed=True` trước khi render.

---

## ❗ 2. Đang Kẹt Ở Đâu / Lưu Ý Quan Trọng (Active Watchpoints & Constraints)
- **Không có lỗi tồn đọng (Zero Blockers)**: Toàn bộ 6/6 kênh đang hoạt động (`Wild Odyssey`, `Odisseia Selvagem`, `The mysteries of the world`, `Mistérios do Mundo`, `Alternate History & "What If?"`, `Psychology of the world`) đều đã có Token OAuth sống trên 3 dự án Google Cloud (`bionic-client-509515-h2`, `ai-youtube-studio-509521`, `youtube-pool-02-510505`).
- Lưu ý: Các token dự án ở chế độ Google Cloud Testing có chu kỳ sống 7 ngày; nếu sang tuần sau gặp `invalid_grant`, chỉ cần bấm `🔑 Cấp Quyền YouTube` trên Web Dashboard (hoặc chuyển OAuth Consent Screen sang `In production`).

---

## 📂 3. Các File Vừa Xử Lý (Recently Modified Files)
- [`app/agents/curation/media_vision_curator_mixin.py`](file:///d:/Python/Youtube/app/agents/curation/media_vision_curator_mixin.py)
- [`app/agents/publisher/oauth_and_upload_mixin.py`](file:///d:/Python/Youtube/app/agents/publisher/oauth_and_upload_mixin.py)
- [`app/graph/checkpoint_store.py`](file:///d:/Python/Youtube/app/graph/checkpoint_store.py)
- [`app/graph/evaluators/visual_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/visual_critic.py)
- [`app/graph/nodes/media/fetch_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/fetch_media_node.py)
- [`app/graph/nodes/media/hook_video_helpers.py`](file:///d:/Python/Youtube/app/graph/nodes/media/hook_video_helpers.py)
- [`app/graph/nodes/media/refine_media_node.py`](file:///d:/Python/Youtube/app/graph/nodes/media/refine_media_node.py)
- [`app/graph/nodes/render_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/render_nodes.py)
- [`config/niches/deep_sea_earth_mysteries.yaml`](file:///d:/Python/Youtube/config/niches/deep_sea_earth_mysteries.yaml)
- [`config/niches/wildlife_predators.yaml`](file:///d:/Python/Youtube/config/niches/wildlife_predators.yaml)

---

## ➡️ 4. Bước Tiếp Theo (Immediate Next Steps)
1. Theo dõi đà vượt trần phân phối (`1.5K–5K+ views`) và chỉ số giữ chân thật (`APV %`, `Stayed to watch %`) từ YouTube Analytics API v2 sau 24–48h cho 12 video `#785`–`#796`.
2. Đồng bộ bình luận ghim mồi tranh luận 2 phe + link dẫn sang video Long-Form 10 phút (`sync_channel_pinned_comments`) cho 6 video hẹn giờ (`#789`, `#792`–`#796`) sau khi mở `PUBLIC`.
