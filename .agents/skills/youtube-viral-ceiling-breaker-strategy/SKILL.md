---
name: youtube-viral-ceiling-breaker-strategy
description: >-
  Chiến lược cốt lõi giúp video YouTube Shorts vượt trần phân phối (1.2K–1.8K views ceiling)
  và duy trì tăng trưởng bền bỉ nhiều ngày (Multi-Day Evergreen Push): Bắt buộc nêu đích danh
  thực thể có lượng tìm kiếm YT_SEARCH cao trong Tiêu đề (cấm giấu tên kiểu Curiosity Gap mù),
  phân tách rạch ròi giữa Tiêu đề (ăn tìm kiếm) và Hook Cảnh 1 0.0s (4 Đòn Tâm Lý Thị Giác không lặp lại tiêu đề),
  giữ chuẩn kịch bản sâu 34s–40s theo cấu trúc Song Đấu 2 Chiều (5-Act Dual-Peak) và bắt buộc
  > 85% thời lượng toàn video là Video chuyển động thật (tương đương tải về 5–7 đoạn video độc lập).
---

# Chiến Lược Vượt Trần Phân Phối & Tăng Trưởng Bền Bỉ YouTube (`youtube-viral-ceiling-breaker-strategy`)

## Overview
Skill này đúc kết quy luật thuật toán thực tế từ dữ liệu **YouTube Analytics API v2** (`dimensions="day"`, `insightTrafficSourceType`, `insightTrafficSourceDetail`) trên hệ thống 7 kênh của dự án: Giải thích lý do tại sao một số video có chỉ số trung bình Ngày 1 rất cao (`APV 93%–125%`) vẫn bị khóa trần ở `1.100 – 1.800 views`, trong khi các video hội tụ đúng **Cơ chế Vượt Trần Kép (`Day-2 Cohort Expansion` + `YT_SEARCH Re-ignition`)** như `imvC7zulBAM` (*Lula-Colossal vs Cachalote*, `3.525+ views`) và `kPEnbE3E3Oo` (*Onça vs Jacaré 1.500 PSI*, `2.504+ views`) lại vượt trần và tăng trưởng đều đặn qua nhiều ngày.

Mọi Agent khi lên ý tưởng chủ đề (Ideation), viết kịch bản/tiêu đề (`ContentAgent`), chọn hình ảnh (`VisualCurationAgent` / `MediaNodes`), hoặc phân tích hiệu suất kênh đều **BẮT BUỘC** phải tuân thủ 3 Quy Tắc Chiến Lược dưới đây.

## Dependencies
- **[youtube-real-metrics-reporting](../youtube-real-metrics-reporting/SKILL.md)**: Dùng để truy vấn dữ liệu thật từ YouTube Data API v3 & YouTube Analytics API v2 khi kiểm chứng độ lệch giữ chân giữa Ngày 1 và Ngày 2 (`dimensions="day"`) cũng như lưu lượng từ tìm kiếm (`insightTrafficSourceType==YT_SEARCH`).
- **[systemic-root-cause-bugfix](../systemic-root-cause-bugfix/SKILL.md)**: Đảm bảo mọi tinh chỉnh prompt/logic đều tổng quát hóa cho mọi ngách, không hardcode từ khóa cục bộ.

---

## 1. Chân Tướng Thuật Toán: Vì Sao Video Bị Kẹt Ở Ngưỡng `1.100 – 1.800 Views`?

Thuật toán YouTube Shorts **KHÔNG** quyết định mở phễu lên tầng cao dựa trên con số `APV %` trung bình của Ngày 1. Thay vào đó, thuật toán vận hành qua **2 Cơ Chế Kiểm Duyệt**:

### Cơ chế A — Bài Kiểm Tra Mở Rộng Tệp Ngày Thứ 2 (`Day-2 Broad Cohort Test`)
1. **Ngày 1 (`0 – 1.300 views` — Core Niche Cohort)**: YouTube phân phối video cho tệp khán giả hạt nhân cực kỳ yêu thích chủ đề đó.
2. **Ngày 2 (`+200 – 400 views` — Broad Audience Cohort)**: YouTube đẩy thử video ra tệp khán giả đại chúng rộng hơn và đo **độ sụt giảm chỉ số giữa Ngày 2 so với Ngày 1**:
   - **Kịch bản BỊ KHÓA TRẦN (Sập chỉ số ở Ngày 2)**:
     - Ví dụ thực tế: `MT3gsVFxBsw` (*Rio 93°C*) có `APV Ngày 1 = 112,5%`, nhưng sang Ngày 2 khi ra tệp rộng, `APV` sập `-45%` xuống còn `67,0%` (`AVD` tụt từ `42s → 25s`). Tương tự, `Brorr-C2VCI` (*Mordida de 1.100 PSI*) sập `APV` từ `99,4%` (Ngày 1) xuống `67,0%` (Ngày 2); `D_sIws9Buto` (*1883 Krakatoa*) sập từ `127,5%` xuống `72,5%`.
     - **Phán quyết của YouTube**: Chủ đề hoặc cấu trúc video chỉ hợp với tệp hẹp ban đầu, khi mở rộng ra đại chúng thì người xem bỏ đi → **Đóng van phân phối ngay tại mốc `1.300 – 1.800 views`**.
   - **Kịch bản VƯỢT TRẦN (Giữ vững hoặc Tăng chỉ số ở Ngày 2)**:
     - Ví dụ thực tế:
       - `imvC7zulBAM` (*Lula-Colossal vs Cachalote*): Ngày 1 đạt `Stayed = 47,7%`, sang Ngày 2 mở rộng tệp thì **`Stayed` TĂNG LÊN `50,5%`**, `AVD` giữ nguyên `24s–26s`, tiếp tục có thêm Sub mới.
       - `kPEnbE3E3Oo` (*Onça vs Jacaré 1.500 PSI*): Ngày 1 đạt `Stayed = 49,5%`, `APV = 59,9%`; sang Ngày 2 **`Stayed` TĂNG LÊN `51,3%`** và **`APV` TĂNG VỌT LÊN `75,7%`** (`AVD` tăng từ `23s → 29s`).
       - `xTySrBJxTps` (*Javali vs Komodo*): Ngày 2 **`Stayed` tăng lên `51,7%`** và **`APV` tăng từ `65,2% → 75,9%`**.
     - **Phán quyết của YouTube**: Càng mở rộng ra tệp đại chúng thì người xem càng ở lại lâu hơn (`Stayed > 50%`) → **Mở van đẩy bền bỉ qua Ngày 3, Ngày 4, Ngày 5 (`Evergreen Push`)**.

### Cơ chế B — Động Cơ Tái Kích Hoạt Từ Tìm Kiếm (`YT_SEARCH Re-ignition`)
- Sau 48 giờ đầu, mọi video Shorts đều giảm tốc độ tự nhiên trên `Shorts Feed`.
- Những video tiếp tục tăng trưởng đều đặn mỗi ngày (`imvC7zulBAM` có **`201 views` từ `YT_SEARCH`**, `kPEnbE3E3Oo` có **`258 views` từ `YT_SEARCH`** với `APV = 74,5%`) đều sở hữu **dòng lưu lượng tìm kiếm tự nhiên chảy vào hàng giờ**.
- Dòng view từ `YT_SEARCH` này đóng vai trò "mồi lửa" báo cho thuật toán biết video có giá trị lâu dài, từ đó kích hoạt các đợt bơm mới trên `Shorts Feed`.

---

## 2. Ba Quy Tắc Chiến Lược Bắt Buộc Khi Sản Xuất Video (`Mandatory Production Rules`)

### Quy Tắc 1: CẤM Giấu Tên Thực Thể Trong Tiêu Đề — Bắt Buộc Nêu Đích Danh Thực Thể Có Lượng Tìm Kiếm Cao Trên Cả 2 Trụ Cột Viral (`YT_SEARCH` Anchor)
1. **Tuyệt đối KHÔNG đặt tiêu đề "Curiosity Gap mù" (Giấu tên con vật / địa danh / thực thể chính)**:
   - ❌ **CẤM**:
     - *"Mordida de 1.100 PSI esmaga ossos!"* (Lỗi thực tế của `Brorr-C2VCI`: Không có tên con vật trong tiêu đề → Công cụ tìm kiếm không phân loại được chủ đề, hết 24h đầu là video chết đứng ở `1.799 views` dù `APV = 93,4%`).
     - *"Why Polar Bears Flee From This 4,000lb Arctic Giant!"* (Lỗi thực tế của `noYqQOCxYpE`: Giấu tên **Walrus / Hải mã** thành cụm từ chung chung *"This 4,000lb Arctic Giant"* → Chỉ có `11 views` tìm kiếm, Ngày 2 tụt từ `1.088 views` về `10 views`).
     - *"Why King Cobra Venom Can't Kill This Beast"* (Giấu tên **Honey Badger / Lửng mật** thành *"This Beast"*).
2. **BẮT BUỘC nêu đích danh Tên Thực Thể Chính theo 1 trong 2 Trụ Cột Viral Triệu View (`Dual-Pillar Viral Architecture`)**:
   - ✅ **Trụ cột A — Đối Đầu Trực Diện (`[Thực thể A] vs [Thực thể B]: [Cược Sinh Tử]`)**:
     - `"Lula-Colossal vs Cachalote: O Duelo Mortal A 2.000m No Oceano 🦑🐋"` (`imvC7zulBAM` — `201 views YT_SEARCH`, tổng `3.525+ views`).
     - `"Onça-Pintada vs Jacaré: O Bote Que Fura o Crânio 🐆🐊"` (`kPEnbE3E3Oo` — `258 views YT_SEARCH`, tổng `2.504+ views`).
   - ✅ **Trụ cột B — Giải Mã Bí Ẩn / Nghịch Lý `"Why / Por Que / Tại Sao / How"` (`Why [Tên Thực Thể / Địa Danh] [Bí Ẩn Ngược Đời]`)** (*Zack D. Films / Você Sabia? / Fern*):
     - `"Why This Underwater Lake Paralyzes Deep-Sea Sharks 💀🦈"` / `"Por Que Esse Lago No Fundo Do Mar Paralisa Tubarões? 💀🦈"`.
     - `"Why Great White Sharks Panic When Orcas Show Up 🐋🦈"`.
     - `"Why Titanic's Swimming Pool Is Still Full Of Water 🚢🌊"`.
     - Khi Tiêu đề mở bằng câu hỏi `"Why / Por Que / How..."` nêu đích danh thực thể/hiện tượng, Cảnh 1 (`0.0s`) **TUYỆT ĐỐI KHÔNG lặp lại câu hỏi `"Why..."`** mà phải thả người xem rơi thẳng vào hiện tượng vật lý kỳ bí hoặc cú sốc đang diễn ra trên màn hình ở `0.0s`, sau đó dẫn dắt qua **5 Hồi Giải Mã Bí Ẩn** (Hồi 1: Hiện tượng kỳ bí ở `0.0s` $\rightarrow$ Hồi 2: Dấu hiệu quan sát & vì sao suy đoán thông thường sai $\rightarrow$ Hồi 3 Cảnh 4: Bóc tách cơ chế vật lý/sinh học/lịch sử ẩn giấu bằng phép so sánh đời thường `$1` $\rightarrow$ Hồi 4: Hệ quả thực tế $\rightarrow$ Hồi 5: Chi tiết vật lý trớ trêu khóa vòng lặp).

---

### Quy Tắc 2: Quy Tắc Vàng Phân Vai Giữa `Tiêu Đề` (Ăn Tìm Kiếm) vs. `Hook Cảnh 1 (0.0s)` (4 Đòn Tâm Lý Thị Giác — CẤM Lặp Lại Tiêu Đề)
- **Sai lầm làm giảm `Stayed to watch`**: Khi Tiêu đề đã ghi `"Onça-Pintada vs Jacaré: A Mordida de 1.500 PSI"`, nếu câu mở đầu ở Cảnh 1 (0.0s) lại đọc lặp lại như cái máy: *"Com 1.500 PSI, a onça-pintada perfura o crânio do jacaré!"*, người xem biết luôn kết quả ngay ở giây thứ 1.5 và vuốt bỏ!
- **Phân vai chuẩn**:
  - **`Title` (Tiêu đề)**: Đảm nhiệm 100% việc chứa tên thực thể + thông số cực hạn để ăn `YT_SEARCH` và CTR.
  - **`Scene 1 Hook (0.0s–2.2s | 6–10 từ)`**: **TUYỆT ĐỐI KHÔNG đọc lặp lại tiêu đề hay tiết lộ luôn con số kết quả ở giây 0.0s!** Hãy để dành con số vật lý (`1.500 PSI`, `230 dB`, `2.000m`) cho Cảnh 2–3 và Cảnh 4 Twist, còn Cảnh 1 bắt buộc dùng **1 trong 4 Đòn Tâm Lý Thị Giác Triệu View**:
    1. **Archetype 1 — Chỉ Tay Vào Chi Tiết Lạ / Ngụy Trang (`Visual Micro-Observation`)**: Bắt mắt người xem phải căng ra tìm một chi tiết trên màn hình trong 3 giây đầu (*"Olhe bem para essas marcas redondas na pele desse cachalote."*, *"O que parece um tronco podre nessa água rasa acabou de abrir os olhos."*, *"Look closely at the circular scars on this whale's head."*).
    2. **Archetype 2 — Sai Lầm Cuối Cùng (`The Fatal Mistake / Hunter Becomes Hunted`)**: Mở vào khoảnh khắc một kẻ săn mồi hoặc nhân vật vừa tính sai một nước cờ chí mạng (*"O último erro desse jacaré foi subir nesse barranco sem olhar para cima."*, *"This caiman's last mistake was climbing onto this riverbank alone."*).
    3. **Archetype 3 — Cảnh Báo Sinh Tồn Ngôi Thứ 2 (`2nd-Person Survival Simulation`)**: Đặt người xem làm nạn nhân trực tiếp bằng *"Se você..." / "If you..."* (*"Se um submarino tocar nessa linha branca no fundo do mar, ele não volta."*, *"If someone touches their neck while apologizing, watch their eyes next."*).
    4. **Archetype 4 — Phá Vỡ Định Kiến (`The Illusion Breaker / Role Reversal`)**: Đưa ra một cảnh tượng ngược hẳn với lẽ thường (*"Seis leões famintos cercaram esse pequeno animal, mas os leões estão fugindo."*, *"Six starving lions surrounded this tiny animal, and the lions are running away."*).

---

### Quy Tắc 3: Giữ Chuẩn Kịch Bản Sâu `34s – 40s` Theo Cấu Trúc "Song Đấu 2 Chiều" (`5-Act Dual-Peak`) + Bắt Buộc `> 85%` Thời Lượng Là Video Chạy Thật (`5–7` Đoạn Video Tải Về Độc Lập)
1. **Không cắt ngắn thời lượng cơ học làm nông kịch bản**:
   - Khung thời lượng chuẩn cho Shorts là **`34s – 40s` (`7 phân cảnh`, `92 – 118 từ`)**.
2. **Kịch bản phải đi theo cấu trúc "Ăn Miếng Trả Miếng / Song Đấu 2 Chiều" (`Ping-Pong Escalation`) để không bị sập `APV` ở Ngày 2**:
   - **Hồi 1 (Cảnh 1 — `0.0s–2.2s`, 6–10 từ)**: Mở màn bằng 1 trong 4 Đòn Tâm Lý Thị Giác (`hook_frame_sharpness >= 12.0`).
   - **Hồi 2 (Cảnh 2–3)**: Lộ diện Đối thủ A cùng thông số vật lý thứ nhất (VD: Mực khổng lồ 14m với giác hút gắn móc xoay 360 độ).
   - **Hồi 3 (Cảnh 4–5 — Đỉnh Cao Trào Thứ 2 / Cú Twist Lật Kèo ở giây 15)**: Đối thủ B tung đòn phản công bằng cơ chế sinh học/vật lý thứ hai (VD: Cá nhà táng 50 tấn phóng xung âm thanh Sonar `230 dB`).
   - **Hồi 4 (Cảnh 6–7)**: Kết cục sinh tử xác đáng giải quyết cú Twist.
   - **Hồi 5 (Cảnh 7 — Vòng lặp hoàn chỉnh)**: Câu kết hoàn chỉnh 100% về ngữ pháp nối thẳng tư duy quay lại Cảnh 1.
3. **Luật Bất Khả Xâm Phạm `> 85%` Thời Lượng Là Video Chuyển Động Thật (`5–7` Đoạn Video Tải Về Độc Lập)**:
   - **Mỗi video Shorts (7 cảnh) BẮT BUỘC phải có `> 85.0%` tổng thời lượng (`duration_ms`) là video chuyển động thật (`media_type in ("video", "hook_ready_video")`)**, tương đương việc **tải về `6–7` đoạn video `.mp4` độc lập** (tối đa chỉ được phép có 0 hoặc 1 cảnh ảnh tĩnh `< 15%` thời lượng; tuyệt đối cấm để `>= 2` cảnh rơi về `BODY DYNAMIC MULTI-IMAGE`).
   - **4 Chốt Chặn Kỹ Thuật Chống Hồi Quy (`Zero Image-Downgrade Guardrails`)**:
     1. **Đua Song Song Pexels + YouTube + Bilibili Thực Thụ (`fetch_media_node`, `post_gather_rescue`, `refine_media_node`)**: Mọi phân cảnh đều chạy song song cả nhánh Pexels/Pixabay lẫn nhánh YouTube/Bilibili. **TUYỆT ĐỐI CẤM** phát tín hiệu hủy (`yt_cancel_ev.set()`) khi nhánh Pexels trả về `None` (`win_res is None`) chỉ để dành thời gian tải ảnh tĩnh Google! Chỉ hủy nhánh còn lại khi đã có 1 nhánh thắng và trả về video thật (`win_res is not None`).
     2. **Bảo Vệ Clip Đã Tải Về Khi Chấm Gemini Vision (`_verify_short_clip_with_gemini_vision`)**: Khi file video `.mp4` đã tải và cắt xong trên ổ cứng (`Path(video_path).exists()`), **CẤM** hủy ngang bước kiểm định Vision vì `deadline_ts` (`Deadline exceeded before vision check` / `after CV pre-gate`).
     3. **Luôn Mở Tầng 3 Bilibili Safe-Crop (`youtube_peak_executor.py`)**: Cờ `skip_stock_fallback=True` chỉ dùng để không lặp lại Tầng 2 Pexels/Pixabay (vì đã chạy song song), **tuyệt đối không được tắt Tầng 3 Bilibili (`_try_bilibili_broll_fallback`)**.
     4. **Cấm Hạ Cấp Xuống Ảnh Tĩnh Khi Chưa Đạt `> 85%` Thời Lượng Video (`evaluate_media_node` + `refine_media_node` + `render_nodes`)**: Trong `evaluate_media_node` và `refine_media_node`, bắt buộc kiểm tra cả số lượng cảnh video (`>= ceil(0.85 * total_scenes)`) lẫn tỷ lệ thời lượng video (`video_duration_ms / total_duration_ms > 0.85`). Khi tỷ lệ thời lượng video `<= 85%`, khóa cứng `can_downgrade_to_image = False` để bắt buộc nâng cấp cảnh ảnh tĩnh thành video chuyển động thật trước khi render.

---

### Quy Tắc 4: Bình Luận Đầu Luồng Của Chủ Kênh (`pinned_comment`) — Giả Thuyết Nâng Độ Khó 50/50, CẤM Đuôi Quảng Cáo & Tự Động Đăng Bù Khi Công Chiếu (`Backfill Law`)
1. **Không bao giờ hỏi lại kết quả đã giải quyết xong trong video**: Nếu video đã cho thấy Cầy Mangut thắng Rắn Mamba Đen, tuyệt đối không hỏi *"Ai thắng: (1) Cầy Mangut hay (2) Mamba Đen?"* vì khán giả đã biết kết quả nên không cần tranh luận.
2. **Không bao giờ gắn đuôi quảng cáo công nghiệp (`"🔔 INSCREVA-SE no canal..."`, `"🔔 SUBSCRIBE..."`)**: Làm lộ mùi bot tự động khiến người xem bỏ qua.
3. **Đặt Câu Hỏi Giả Định Mở Nâng Độ Khó (`Counterfactual 50/50 Debate`, 12–22 từ)**: Đặt giả thuyết *2 đánh 1*, *đổi địa hình/bóng tối*, *kèo đấu tiếp theo*, hoặc *thử thách 1 triệu USD* kết thúc bằng `(1) ... hay (2) ... 👇` để chia đôi phe bình luận.
4. **Bắt buộc Tự Động Đăng Bù Bình Luận (`Comment Backfill`) Sau Khi Video Hẹn Giờ Chuyển Sang `Public`**: Do YouTube Data API v3 chặn `commentThreads().insert` khi video đang ở trạng thái `private` (`scheduled`), hệ thống (`sync_scheduled_jobs_status` và `sync_channel_pinned_comments`) **BẮT BUỘC** phải tự động quét và đăng bù bình luận tranh luận ngay khi video chuyển sang `public` (tuyệt đối không để video `public` bị trống `0` bình luận).

---

### Quy Tắc 5: Luật Khung Giờ Vàng Định Tuyến Địa Lý & Giãn Cách Rộng `8h45p` (`US Geo-Routing & Wide-Spacing Law`) — Tính Chuẩn Độ Trễ Sóng `+1.5h–3h` Của YouTube
1. **Bằng chứng thực nghiệm từ YouTube Analytics API v2 (`dimensions="country"`) về Độ Trễ Sóng Đẩy (`+1.5h – 3.0h` Seed Wave Delay)**:
   - YouTube Shorts mất **`1,5 đến 3 tiếng` sau thời điểm `publishAt`** để phân tích và bơm sóng thử nghiệm hạt giống (*Initial Seed Wave*):
     - ❌ **CẤM đăng kênh `US` lúc `14:00–15:00 UTC` (`21:00–22:00` Đêm VN)**: Sóng nổ lúc `22:00–23:59` Đêm VN (giờ người Việt nằm lướt điện thoại trước khi ngủ → dính `45%–68%` view VN).
     - ❌ **CẤM đăng kênh `US` lúc `21:00–21:45 UTC` (`04:00–04:45` Sáng VN)**: Sóng nổ sau đó 1.5–2.5 tiếng, rơi đúng vào **`05:30–07:30` Sáng VN** (giờ người Việt vừa ngủ dậy cầm điện thoại lướt trên giường → thực tế `7_2o0yEjPeU` đăng `21:00 UTC` bị dính tới `778/958 views` = `81,2%` từ VN!).
2. **Luật Bất Khả Xâm Phạm trong `app/services/smart_scheduler.py` (`MIN_SPACING_SECONDS = 21600`)**:
   - **5 Kênh Tiếng Anh (`US`) — 2 Khung Giờ Cách Xa Nhau Đúng `8 Tiếng 45 Phút` (và `15 Tiếng 15 Phút`), Hai Kênh Cùng Gmail Cách Nhau Đúng `60 Phút`**:
     - **Khung Mỹ #1 (`Base 17:15 UTC` -> trải từ `16:15 – 18:15 UTC` / `23:15 Đêm – 01:15 Sáng VN`)**: Sóng đẩy `+1.5h–3h` nổ vào đúng **`01:00 – 04:15` Rạng Sáng VN** (100% người Việt ngủ say, thực tế `PVoVipDbLt8` và `pZgYrAcRaAE` đạt **`0` view VN**, `APV Mỹ/Anh = 93%–125%`), đón trọn Đỉnh Nghỉ Trưa & Chiều Mỹ (`12:15 PM – 04:00 PM EST/EDT`).
     - **Khung Mỹ #2 (`Base 02:00 UTC` -> trải từ `01:00 – 03:00 UTC` / `08:00 – 10:00 Sáng VN`)**: Cách Khung #1 đúng **`8 tiếng 45 phút`**; sóng đẩy nổ đúng vào **Đỉnh Giờ Vàng Buổi Tối Số 1 Toàn Nước Mỹ (`08:00 PM – 11:00 PM EST/EDT` Bờ Đông & `05:00 PM – 08:00 PM PST/PDT` Bờ Tây)**, trong khi Việt Nam đang vào giờ làm việc/học hành buổi sáng (`09:30 – 11:30` Sáng VN).
     - **Bảng giờ cố định cho từng kênh**:
       - `Alternate History` (`accout3`, `-60p`): **`16:15 UTC` (`23:15` Đêm VN)** & **`01:00 UTC` (`08:00` Sáng VN)**
       - `Psychology of the world` (`account_1` A, `-30p`): **`16:45 UTC` (`23:45` Đêm VN)** & **`01:30 UTC` (`08:30` Sáng VN)**
       - `Paws & Hearts Rescue` (`acount_2` A, `0p`): **`17:15 UTC` (`00:15` Sáng VN)** & **`02:00 UTC` (`09:00` Sáng VN)**
       - `The mysteries of the world` (`account_1` B, `+30p` — cách Psychology `60p`): **`17:45 UTC` (`00:45` Sáng VN)** & **`02:30 UTC` (`09:30` Sáng VN)**
       - `Wild Odyssey` (`acount_2` B, `+60p` — cách Paws & Hearts `60p`): **`18:15 UTC` (`01:15` Sáng VN)** & **`03:00 UTC` (`10:00` Sáng VN)**
   - **2 Kênh Tiếng Bồ Đào Nha (`Brazil pt-BR` trên `acccount4`) — 2 Video Cùng Kênh Cách Xa `6 Tiếng 55 Phút`, Hai Kênh Cách Nhau `65 Phút`**:
     - `Odisseia Selvagem` (`0p`): **`13:20 UTC` (`20:20` Tối VN / `10:20 AM BRT`)** & **`20:15 UTC` (`03:15` Sáng VN / `05:15 PM BRT`)**
     - `Mistérios do Mundo` (`+65p`): **`14:25 UTC` (`21:25` Tối VN / `11:25 AM BRT`)** & **`21:20 UTC` (`04:20` Sáng VN / `06:20 PM BRT`)**

---

### Quy Tắc 6: Luật Chống Đụng Lịch Giữa Video Dài (`16:9`) và Video Shorts (`9:16`) Cùng Kênh (`Anti-Collision Law`)
- **TUYỆT ĐỐI CẤM** hẹn lịch xuất bản một Video Dài (`16:9`) và một Video Shorts (`9:16`) trùng giờ/phút trên cùng một kênh (gây tự nuốt hiển thị / *self-cannibalization* trong 2 giờ đầu).
- Mọi video xuất bản trong cùng một ngày trên cùng một kênh phải cách nhau tối thiểu **`3.0 giờ` (`MIN_SPACING_SECONDS = 10800`)** (ví dụ trên `Wild Odyssey`: Shorts đăng lúc `18:30 UTC` / `01:30 Sáng VN`, Long-Form đăng lúc `21:45 UTC` / `04:45 Sáng VN`).
- Khóa cứng `categoryId = "15"` (*Pets & Animals*) cho kênh Động vật (`Odisseia Selvagem`, `Wild Odyssey`, `Paws & Hearts Rescue`) và `categoryId = "22"` (*People & Blogs*) cho các kênh Bí ẩn/Lịch sử/Tâm lý; **TUYỆT ĐỐI CẤM** dùng `categoryId = "27"` (*Education*) vì gây bóp hiển thị Shorts Feed.

---

### Quy Tắc 7: Luật Auto-Thumbnail Mốc `50%` Cho Kênh Chưa Xác Thực (`50% Midpoint Hero-Frame Law`)
1. **Quy luật máy chủ YouTube (`i.ytimg.com`)**: Với kênh chưa xác thực SĐT để đẩy `customThumbnail` (`403 Forbidden`), YouTube **100% LUÔN LẤY `2.jpg` (khung hình tại đúng mốc `50.0%` thời lượng video)** làm ảnh bìa mặc định (`hqdefault.jpg` / `maxresdefault.jpg`), và lấy `1.jpg` (`25%`), `3.jpg` (`75%`) làm 2 lựa chọn phụ.
2. **TUYỆT ĐỐI CẤM** đóng chữ thumbnail vào giây `0.0s–0.5s` đầu video (làm người xem tưởng video là ảnh tĩnh và vuốt bỏ, gây sập `Stayed to watch %`).
3. **Chuẩn kỹ thuật bắt buộc cho Video Dài `16:9` (`bilibili_longform_compiler.py` & `subtitle_helper.py`)**:
   - Tự động chọn clip cận cảnh sắc nét nhất toàn bài (`select_best_hero_timestamp`) và đóng dấu khung hình Hero + dải nền tối đáy màn hình + **Banner Chữ Khổng Lồ `88px` 2 Màu Vàng–Trắng (2–4 từ)** ở sát đáy (`Alignment=2`, `MarginV=48`, giữ 80% diện tích phía trên trong trẻo 100%) tại đúng 3 mốc **`25%`, `50%`, và `75%`** (`stamp_hero_auto_thumbnails_on_mp4`).
   - Tiêu đề Video Dài `16:9` phải gọn dưới **`55–60 ký tự`**: `[Thực thể A] vs [Thực thể B]: [Cơ chế sốc] ([Thông số])` để hiển thị trọn vẹn 100% trên màn hình điện thoại mà không bị cắt bằng dấu `...`.

---

### Quy Tắc 8: Luật Chống Nhân Bản Chủ Đề, Xoay Vòng Model Khi Timeout & Kiểm Định Quốc Kỳ / Niên Đại Lịch Sử (`Zero Repetitive-Content & Anti-Anachronism Law`)
1. **Chống Nhân Bản Chủ Đề Đa Tầng (`Zero Topic-Cloning`)**:
   - Mọi hàm truy vấn lịch sử kênh (`list_videos`, `check_duplicate`, `check_idea_similarity`) **BẮT BUỘC** gọi `normalize_channel_id(channel_id)` để không bao giờ bỏ sót lịch sử đăng bài khi truyền alias (`ch_alternate_history` vs `@alternatehistorywhatif-e2x`).
   - `check_idea_similarity` phải so khớp cả toàn câu lẫn **cụm Thực thể cốt lõi đứng trước dấu `:` (`Subject-Prefix Containment`)** với ngưỡng `<= 0.40` để chặn triệt để việc một câu prompt dài 18 từ trùng sự kiện với tiêu đề ngắn 6 từ đã đăng trên YouTube.
   - **TUYỆT ĐỐI CẤM** lấy lại `remaining[0]` khi danh sách tĩnh đã dùng hết, và **TUYỆT ĐỐI CẤM** để hàm dự phòng `_build_curated_5act_script_fallback` ghi đè tiêu đề tĩnh (*Vasili Arkhipov*, *Stanislav Petrov*, *Benjamin Franklin*) lên một chủ đề khác (vì đăng trùng 2–4 lần cùng một sự kiện sẽ kích hoạt bộ lọc **Spam / Repetitive Content** của YouTube, khóa cứng video ở `0 views`).
2. **Hạ Bậc Ngay Model Bị Treo `Read timed out` (`Immediate Timeout Demotion`)**:
   - Trong `script_json_parser_mixin.py`, khi bất kỳ model Gemini nào gặp `Read timed out`, bắt buộc gọi ngay `self.rotator.mark_model_rate_limited(key, model, status_code=504, error_text=err_s)` để giáng hạng model đó 30 giây và nhảy sang model khỏe mạnh kế tiếp trong `0ms`, không bao giờ để treo dây chuyền dẫn tới kịch bản fallback.
3. **Cổng Thị Giác Chống Sai Quốc Kỳ & Lệch Niên Đại Lịch Sử (`Historical & Geopolitical Flag Integrity`)**:
   - Trong `media_vision_curator_mixin.py`, Gemini Vision bắt buộc chấm `entity_identity_score < 5.0` và `visual_integrity_score < 5.0` (từ chối ngay) đối với bất kỳ clip Stock nào xuất hiện **Quốc kỳ / Quân hiệu của quốc gia ngoại lai mâu thuẫn với chủ thể lịch sử trong kịch bản** (ví dụ: kịch bản tàu ngầm Liên Xô/Mỹ nhưng clip Pexels treo cờ Thổ Nhĩ Kỳ/Kyrgyzstan) hoặc lẫn khách du lịch/xe hơi hiện đại vào bối cảnh cổ đại/thế chiến.

---

## Workflow Áp Dụng Cho Agent Trong Mọi Phiên Làm Việc

### 1. Khi Lên Ý Tưởng Chủ Đề & Đặt Tiêu Đề (`Ideation & Title Generation`)
- Kiểm tra xem tiêu đề đã nêu đích danh **Tên Thực Thể Có Lượng Tìm Kiếm Cao** (ưu tiên cặp đối đầu `[Thực thể A] vs [Thực thể B]`) + **Con Số Vật Lý Cụ Thể** (`PSI`, `°C`, `m`, `km/h`, `Volts`, `dB`, `Năm`) chưa.

### 2. Khi Soạn Thảo & Duyệt Kịch Bản (`Script Generation & Linting`)
- Kiểm tra Cảnh 1 (`scenes[0].narration`) đã dùng 1 trong 4 Đòn Tâm Lý Thị Giác mà **không đọc lặp lại nguyên văn Tiêu đề** chưa.
- Giữ thời lượng `34s – 40s` (`7–8 phân cảnh`, `92–118 từ`), đảm bảo Cảnh 4–5 (giây 14–20) luôn có **Cú Twist Phản Công / Cơ Chế Tầng Sâu Thứ 2**.
- Kiểm tra câu cuối cùng của kịch bản (`Scene 7/8`) phải là một câu hoàn chỉnh 100%, không bị cụt lủn hoặc treo từ nối.

### 3. Khi Kiểm Toán & Phân Tích Video Đã Đăng (`Performance Audit`)
- Kết hợp với **[youtube-real-metrics-reporting](../youtube-real-metrics-reporting/SKILL.md)**, truy vấn `dimensions="day"` và `insightTrafficSourceType` (`YT_SEARCH`) để đánh giá khả năng vượt trần thực tế.
