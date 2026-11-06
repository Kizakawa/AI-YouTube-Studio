# ⚡ working_memory.md — Bộ Nhớ Trạng Thái Hiện Tại ("RAM Ngoài" Của Agent)

> **Nguyên tắc vận hành**:
> - Đọc file này **đầu tiên** (cùng với [`MEMORY.md`](file:///d:/Python/Youtube/MEMORY.md) và [`todo.md`](file:///d:/Python/Youtube/todo.md)) khi bắt đầu bất kỳ phiên làm việc hoặc yêu cầu mới nào.
> - Cuối mỗi tác vụ (trước khi `git commit & push`), **Main Agent BẮT BUỘC cập nhật lại file này** theo đúng 4 mục: `✅ Đã làm gì`, `❗ Đang kẹt / Lưu ý gì`, `📂 File vừa xử lý`, `➡️ Bước tiếp theo`.

---

## 📌 Snapshot Trạng Thái Hiện Tại (Cập nhật: `2026-11-07 00:36 +07:00`)

- **Nhánh Git hiện tại**: `main` (đồng bộ sạch với `origin/main`).
- **Tác vụ vừa hoàn thành**:
  - `TASK-013` & Kiểm toán/Tự chữa lành SEO Tags (`seo_tags_mixin.py` & `metadata_healer_mixin.py`): Kiểm toán trực tiếp qua YouTube Data API v3 toàn bộ **7 kênh** và **12 video Shorts vừa xuất bản (`#773`–`#784`)**, nâng cấp thuật toán bóc tách thực thể cấu trúc từ tiêu đề (`Pillar A Matchup` + `Pillar B Named Entity / Question NP`), phân tách thông minh `deep_sea_earth_mysteries` (Biển sâu vs Địa chất trên cạn như *Lake Natron*), và đồng bộ (`videos().update`) 100% bộ Tags + Hashtags thực thể lên cả 12 video trên YouTube mà giữ nguyên tuyệt đối giờ hẹn `publishAt`.
  - `TASK-007`: Hoàn tất nghiệm thu thực tế (`End-to-End Video QA`) cho cả 2 trụ cột Viral (`pillar_1_direct_matchup` và `pillar_2_why_mystery_explainer`).

---

## ✅ 1. Đã Làm Gì (Completed In Recent Turns)
1. **Sản xuất, Kiểm định Lưới Hình Ảnh & Lập Lịch Giờ Vàng 12/12 Video Shorts (`#773`–`#784`)**:
   - **Wild Odyssey (`ch_wild_odyssey`)**:
     - `#777` (*Shoebill Stork vs Baby Nile Crocodile: The Machine-Gun Beak 🦤🐊 #Shorts*, `7/7` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/Jsh3Q_0ip04` (`10:00 Sáng T7, 07/11/2026 VN`).
     - `#778` (*Army Ants vs Giant Scorpion: 500,000 Jaws in the Amazon 🐜🦂 #Shorts*, `6/7` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/AV4R0W8nCBg` (`01:15 Sáng CN, 08/11/2026 VN`).
   - **Odisseia Selvagem (`ch_odisseia_selvagem`)**:
     - `#779` (*Ave-Secretário vs Serpente: O Pisão Mortal de 195 Newtons 🦅🐍 #Shorts*, `7/7` video, `VideoQA 98/100`) $\rightarrow$ `https://www.youtube.com/shorts/ZnzDxKwlrE4` (`03:15 Sáng T7, 07/11/2026 VN`).
     - `#782` (*Diabo-da-Tasmânia vs Ossos: A Mordida Mais Forte por Quilo 👹🦴 #Shorts*, `7/7` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/mRDUKrglCOs` (`20:20 Đêm T7, 07/11/2026 VN`).
   - **The mysteries of the world (`ch_the_mysteries_of_the_world`)**:
     - `#780` (*Why This Deep-Sea Brine Pool Paralyzes Sharks in Seconds 🌊🦈 #Shorts*, `7/7` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/1Txbhnr3x5o` (`00:45 Sáng CN, 08/11/2026 VN`).
     - `#783` (*Lake Natron: Why This 140°F Red Lake Turns Birds Into Stone 🦩🪨 #Shorts*, `7/7` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/0cQrpF7MM3Y` (`00:45 Sáng T2, 09/11/2026 VN`).
   - **Mistérios do Mundo (`ch_misterios_do_mundo`)**:
     - `#781` (*Por Que a Piscina do Titanic Continua Cheia de Água a 3.800m? 🚢🌊 #Shorts*, `6/7` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/88IxuM46wTI` (`04:20 Sáng T7, 07/11/2026 VN`).
     - `#784` (*Peixe-Barril (Macropinna): O Peixe Com Cabeça Transparente 🐟👁️ #Shorts*, `6/6` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/YaZReDxYyxE` (`21:25 Đêm T7, 07/11/2026 VN`).
   - **Alternate History & "What If?" (`@alternatehistorywhatif-e2x`)**:
     - `#776` (*1961 Goldsboro B-52 Crash: Two 4-Megaton Nukes vs One Switch ☢️✈️ #Shorts*, `8/8` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/DcN_wEKF-Ek` (`23:15 Đêm T6, 06/11/2026 VN`).
     - `#773` (*Bismarck Battleship vs Swordfish: 1 Torpedo vs 50,000 Tons 🚢✈️ #Shorts*, `7/7` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/jOXGfmsc0w4` (`08:00 Sáng T7, 07/11/2026 VN`).
   - **Psychology of the world (`ch_psychology_of_the_world`)**:
     - `#775` (*Hyperthymesia: Why a Perfect Memory Is a Mental Trap 🧠🔒 #Shorts*, `7/8` video, `VideoQA 98/100`) $\rightarrow$ `https://www.youtube.com/shorts/vyz_-KJzUTM` (`23:45 Đêm T7, 07/11/2026 VN`).
     - `#774` (*Telepathy Paradox: What If You Heard Everyone's Thoughts? 🧠👁️ #Shorts*, `7/7` video, `VideoQA 99/100`) $\rightarrow$ `https://www.youtube.com/shorts/DbmIZwKK7ik` (`08:30 Sáng CN, 08/11/2026 VN`).
2. **Sửa tận gốc 5 lỗi hệ thống phát hiện trong quá trình chạy & kiểm toán Batch 12 video**:
   - [`app/agents/publisher/seo_tags_mixin.py`](file:///d:/Python/Youtube/app/agents/publisher/seo_tags_mixin.py) & [`app/agents/publisher/metadata_healer_mixin.py`](file:///d:/Python/Youtube/app/agents/publisher/metadata_healer_mixin.py): Nâng cấp bóc tách thực thể cấu trúc trực tiếp từ tiêu đề (`[Entity A] vs [Entity B]`, `[Named Entity]: ...`, `(Parenthetical Entity)`, và câu hỏi `Why / Por Que / How [Subject NP]`), nạp `vj.tags` / `vj.topic` từ DB khi `verify_and_heal_video_metadata`, phân tách thông minh ngách `deep_sea_earth_mysteries` (Biển sâu vs Địa chất trên cạn như *Lake Natron*), và đồng bộ 100% Tags + Hashtags thực thể lên cả 12 video trên YouTube.
   - [`app/services/intelligence/deduplication.py`](file:///d:/Python/Youtube/app/services/intelligence/deduplication.py) & [`app/services/video_library.py`](file:///d:/Python/Youtube/app/services/video_library.py): Loại bỏ `#hashtags` ở cuối tiêu đề trước khi tính độ tương đồng từ vựng và yêu cầu trùng cả 2 vế khi so khớp tiền tố kèo đấu `A vs B`.
   - [`app/graph/nodes/render_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/render_nodes.py): Mở khóa cứu hộ file `.mp4` trên ổ đĩa khi phân cảnh bị gán `_graphic.png` (`not _is_placeholder_cur`), đồng thời kiểm tra `verify_hook_clip_genuine_motion` cho cả Cảnh 1 và Cảnh 2 (`sc_id in (1, 2)`).
   - [`app/services/quota_manager.py`](file:///d:/Python/Youtube/app/services/quota_manager.py): Quét đầy đủ cả thư mục alias kênh trong `get_channel_tokens_pool` để xoay dự án Google Cloud dự phòng chính xác.

---

## ❗ 2. Đang Kẹt Ở Đâu / Lưu Ý Quan Trọng (Active Watchpoints & Constraints)
- **Không có lỗi tồn đọng (Zero Blockers)**: Toàn bộ 7 kênh đã gắn chuẩn Channel Keywords + Country (`US`/`BR`), và toàn bộ 12/12 video mới (`#773`–`#784`) đã được đồng bộ 100% Tags thực thể + Hashtags tiêu đề/mô tả + CategoryId (`15`/`22`) trên YouTube mà giữ nguyên lịch hẹn `publishAt`.
- **Chốt chặn bắt buộc phải giữ vững**:
  1. Giữ vững sàn `> 85.0%` thời lượng là video chuyển động thật (`6–7/7` cảnh `.mp4`) và `100%` chuyển động thật ở `5–7s` mở đầu.
  2. Luôn trích xuất và kiểm tra trực tiếp lưới frame (`job_<id>_sheet.jpg`) trước khi xuất bản lên YouTube.

---

## 📂 3. Các File Vừa Xử Lý (Recently Modified Files)
- [`app/agents/publisher/seo_tags_mixin.py`](file:///d:/Python/Youtube/app/agents/publisher/seo_tags_mixin.py)
- [`app/agents/publisher/metadata_healer_mixin.py`](file:///d:/Python/Youtube/app/agents/publisher/metadata_healer_mixin.py)
- [`app/services/intelligence/deduplication.py`](file:///d:/Python/Youtube/app/services/intelligence/deduplication.py)
- [`app/services/video_library.py`](file:///d:/Python/Youtube/app/services/video_library.py)
- [`app/graph/nodes/render_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/render_nodes.py)
- [`app/services/quota_manager.py`](file:///d:/Python/Youtube/app/services/quota_manager.py)
- [`working_memory.md`](file:///d:/Python/Youtube/working_memory.md)
- [`todo.md`](file:///d:/Python/Youtube/todo.md)
- [`progress_log.md`](file:///d:/Python/Youtube/progress_log.md)

---

## ➡️ 4. Bước Tiếp Theo (Immediate Next Steps)
1. Theo dõi hiệu suất thực tế (`Views`, `Like/View %`, `APV %`, `Stayed to watch %`) của 12 video Shorts vừa lên lịch công chiếu qua YouTube Data API v3 & YouTube Analytics API v2 khi người dùng yêu cầu.
2. Tự động đính kèm bình luận ghim mồi tranh luận 2 phe + link dẫn sang video Long-Form 10 phút (`sync_channel_pinned_comments`) khi các video chuyển từ `scheduled` sang `public`.
