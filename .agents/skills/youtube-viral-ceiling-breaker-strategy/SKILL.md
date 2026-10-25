---
name: youtube-viral-ceiling-breaker-strategy
description: >-
  Chiến lược cốt lõi giúp video YouTube Shorts vượt trần phân phối (1.2K–1.8K views ceiling)
  và duy trì tăng trưởng bền bỉ nhiều ngày (Multi-Day Evergreen Push): Bắt buộc nêu đích danh
  thực thể có lượng tìm kiếm YT_SEARCH cao trong Tiêu đề (cấm giấu tên kiểu Curiosity Gap mù),
  giữ chuẩn kịch bản sâu 34s–40s theo cấu trúc Song Đấu 2 Chiều (5-Act Dual-Peak) và đảm bảo
  tối thiểu 80% cảnh Video chuyển động thật rải đều toàn bài để vượt qua Bài Kiểm Tra Mở Rộng
  Tệp Ngày Thứ 2 (Day-2 Broad Cohort Test) của thuật toán YouTube.
---

# Chiến Lược Vượt Trần Phân Phối & Tăng Trưởng Bền Bỉ YouTube (`youtube-viral-ceiling-breaker-strategy`)

## Overview
Skill này đúc kết quy luật thuật toán thực tế từ dữ liệu **YouTube Analytics API v2** (`dimensions="day"`, `insightTrafficSourceType`, `insightTrafficSourceDetail`) trên hệ thống 7 kênh của dự án: Giải thích lý do tại sao một số video có chỉ số trung bình Ngày 1 rất cao (`APV 93%–125%`) vẫn bị khóa trần ở `1.100 – 1.800 views`, trong khi các video hội tụ đúng **Cơ chế Vượt Trần Kép (`Day-2 Cohort Expansion` + `YT_SEARCH Re-ignition`)** như `imvC7zulBAM` (*Lula-Colossal vs Cachalote*, `3.525+ views`) và `kPEnbE3E3Oo` (*Onça vs Jacaré 1.500 PSI*, `2.504+ views`) lại vượt trần và tăng trưởng đều đặn qua nhiều ngày.

Mọi Agent khi lên ý tưởng chủ đề (Ideation), viết kịch bản/tiêu đề (`ContentAgent`), chọn hình ảnh (`VisualCurationAgent` / `MediaNodes`), hoặc phân tích hiệu suất kênh đều **BẮT BUỘC** phải tuân thủ 2 Trụ Cột Chiến Lược dưới đây.

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

## 2. Hai Quy Tắc Chiến Lược Bắt Buộc Khi Sản Xuất Video (`Mandatory Production Rules`)

### Quy Tắc 1: CẤM Giấu Tên Thực Thể Trong Tiêu Đề — Bắt Buộc Nêu Đích Danh Cặp Thực Thể Có Lượng Tìm Kiếm Cao (`YT_SEARCH` Anchor)
1. **Tuyệt đối KHÔNG đặt tiêu đề "Curiosity Gap mù" (Giấu tên con vật / thực thể chính)**:
   - ❌ **CẤM**:
     - *"Mordida de 1.100 PSI esmaga ossos!"* (Lỗi thực tế của `Brorr-C2VCI`: Không có tên con vật trong tiêu đề → Công cụ tìm kiếm không phân loại được chủ đề, từ khóa tìm kiếm trỏ vào toàn rác, hết 24h đầu là video chết đứng ở `1.799 views` dù `APV = 93,4%`).
     - *"Why Polar Bears Flee From This 4,000lb Arctic Giant!"* (Lỗi thực tế của `noYqQOCxYpE`: Giấu tên **Walrus / Hải mã** thành cụm từ chung chung *"This 4,000lb Arctic Giant"* → Chỉ có `11 views` tìm kiếm, Ngày 2 tụt từ `1.088 views` về `10 views`).
     - *"Why King Cobra Venom Can't Kill This Beast"* (Giấu tên **Honey Badger / Lửng mật** thành *"This Beast"*).
2. **BẮT BUỘC nêu đích danh Tên Thực Thể Chính (hoặc Cặp Đối Đầu `Thực thể A vs Thực thể B`) + Thông Số Vật Lý Cực Hạn ngay trong Tiêu đề**:
   - ✅ **CHUẨN VƯỢT TRẦN**:
     - `"Lula-Colossal vs Cachalote: O Duelo Mortal A 2.000m No Oceano 🦑🐋"` (`imvC7zulBAM` — Khớp 100% truy vấn tìm kiếm `"cachalote"`, `"lula colossal"`, `"cachalote vs lula gigante"` → `201 views YT_SEARCH`, tổng `3.525+ views`).
     - `"Mordida de 1.500 PSI: Onça-Pintada vs Jacaré 💀🔥"` (`kPEnbE3E3Oo` — Khớp 100% truy vấn `"onça"`, `"jacaré"`, `"onça vs jacare"` → `258 views YT_SEARCH`, tổng `2.504+ views`).
     - `"Harpia vs Preguiça: A Garra de 530 PSI Que Esmaga Ossos! 🦅💀"` (`dojq59j-5Ns` — `1.952+ views` sau 31h).
     - `"35lb Wolverine vs 800lb Grizzly Bear! 🦡🐻"` (`FVO_dNBqvoQ`).
3. **Nguyên lý tạo sự tò mò đúng cách**:
   - Không tạo tò mò bằng cách giấu tên con vật/thực thể.
   - Hãy **công khai danh tính 2 đối thủ khổng lồ ngay trên tiêu đề** để ăn trọn lưu lượng `YT_SEARCH` và thu hút tệp đại chúng, còn sự tò mò nằm ở **"Cơ chế vũ khí sinh học / vật lý nào sẽ quyết định kẻ thắng người thua?"**.

---

### Quy Tắc 2: Giữ Chuẩn Kịch Bản Sâu `34s – 40s` Theo Cấu Trúc "Song Đấu 2 Chiều" (`5-Act Dual-Peak`) + Tối Thiểu `80%` Cảnh Video Chuyển Động Thật
1. **Không cắt ngắn thời lượng cơ học làm nông kịch bản**:
   - Khung thời lượng chuẩn cho Shorts là **`34s – 40s` (`7 – 8 phân cảnh`, `92 – 118 từ`)**.
   - Nếu cắt ép xuống `20–24s`, kịch bản chỉ kịp nêu bề nổi mà mất đi phần giải phẫu cơ chế sâu bên trong, làm giảm tỷ lệ Like/Sub và khiến người xem cảm thấy nội dung bị loãng/cụt.
2. **Kịch bản phải đi theo cấu trúc "Ăn Miếng Trả Miếng / Song Đấu 2 Chiều" (`Ping-Pong Escalation`) để không bị sập `APV` ở Ngày 2**:
   - Khi mở rộng ra tệp khán giả đại chúng ở Ngày 2, các kịch bản thuyết minh địa chất/vật lý tĩnh 1 chiều rất dễ bị sập `-40% APV`.
   - Ngược lại, cấu trúc **Song Đấu 2 Chiều** (như `imvC7zulBAM` `Job #506`) giữ chân tệp đại chúng cực tốt:
     - **Hồi 1 (Cảnh 1 — `0.0s–2.2s`, 5–8 từ)**: Mở màn bằng bằng chứng vật lý hoặc đòn tấn công trực diện + thông số cực hạn (`hook_frame_sharpness >= 12.0`).
     - **Hồi 2 (Cảnh 2–3)**: Lộ diện Đối thủ A cùng kích thước và vũ khí sát thương đặc trưng (VD: Mực khổng lồ 14m với giác hút gắn móc xoay 360 độ).
     - **Hồi 3 (Cảnh 4–5 — Đỉnh Cao Trào Thứ 2 / Cú Twist Lật Kèo ở giây 15)**: Đối thủ B tung đòn phản công bằng một cơ chế sinh học/vật lý bất ngờ (VD: Cá nhà táng 50 tấn không chỉ dùng răng mà phóng xung âm thanh Sonar `230 dB` như bom xung kích làm tê liệt con mồi).
     - **Hồi 4 (Cảnh 6–7)**: Kết cục sinh tử xác đáng giải quyết cú Twist.
     - **Hồi 5 (Cảnh 7/8 — Vòng lặp hoàn chỉnh)**: Câu kết hoàn chỉnh 100% về ngữ pháp (tuyệt đối không bị cắt cụt vị ngữ) nối thẳng tư duy quay lại Cảnh 1.
3. **Bắt buộc rải đều Cảnh Video Chuyển Động Thật (`>= 80% Video Clips`) xuyên suốt thân bài**:
   - Pháp y dữ liệu cho thấy các video bị kẹt cứng ở `1.125 – 1.210 views` (`CX8Elp5KDzU`, `p4YpOWPmM6U`, `F1avBb_LC50`) đều mắc lỗi: **Chỉ có Cảnh 1 là Video, còn từ Cảnh 2 đến Cảnh 8 hoàn toàn là Ảnh Tĩnh (`image`)**, khiến người xem thoát hàng loạt ở giây thứ 17.
   - Trong khi đó, các video vượt trần (`imvC7zulBAM`) luôn có các cảnh **Video chuyển động thật** rải đều ở các nhịp cao trào giữa bài (Cảnh 1, Cảnh 2, Cảnh 4, Cảnh 6).
   - Luôn duy trì chế độ **Video-First Media Director** và tự động thay thế 100% cảnh trượt `VisualCritic` bằng video chuyển động sạch.

---

## Workflow Áp Dụng Cho Agent Trong Mọi Phiên Làm Việc

### 1. Khi Lên Ý Tưởng Chủ Đề & Đặt Tiêu Đề (`Ideation & Title Generation`)
- Kiểm tra xem tiêu đề đã nêu đích danh **Tên Thực Thể Có Lượng Tìm Kiếm Cao** (ưu tiên cặp đối đầu `[Thực thể A] vs [Thực thể B]`) + **Con Số Vật Lý Cụ Thể** (`PSI`, `°C`, `m`, `km/h`, `Volts`, `dB`, `Năm`) chưa.
- Nếu tiêu đề dùng từ ẩn danh kiểu *"This Beast"*, *"This Giant"*, *"This Creature"* hoặc chỉ nêu lực cắn mà giấu tên con vật → **Bắt buộc viết lại tiêu đề có tên thực thể rõ ràng**.

### 2. Khi Soạn Thảo & Duyệt Kịch Bản (`Script Generation & Linting`)
- Giữ thời lượng `34s – 40s` (`7–8 phân cảnh`, `92–118 từ`), đảm bảo Cảnh 4–5 (giây 14–20) luôn có **Cú Twist Phản Công / Cơ Chế Tầng Sâu Thứ 2** để chống tụt `APV` khi YouTube mở rộng tệp ở Ngày 2.
- Kiểm tra câu cuối cùng của kịch bản (`Scene 7/8`) phải là một câu hoàn chỉnh 100%, không bị cụt lủn hoặc treo từ nối.

### 3. Khi Kiểm Toán & Phân Tích Video Đã Đăng (`Performance Audit`)
- Kết hợp với **[youtube-real-metrics-reporting](../youtube-real-metrics-reporting/SKILL.md)**, khi người dùng hỏi vì sao một video tăng trưởng tốt hoặc bị chững:
  1. Truy vấn `dimensions="day"` để so sánh `Stayed %` và `APV %` của **Ngày 1 vs Ngày 2** (kiểm tra xem video vượt qua hay trượt bài test mở rộng tệp Ngày 2).
  2. Truy vấn `insightTrafficSourceType` và `insightTrafficSourceDetail` (`YT_SEARCH`) để kiểm tra xem tiêu đề có đang hút được lưu lượng tìm kiếm tự nhiên nuôi sống video hay không.
  3. Kiểm tra tỷ lệ `video` vs `image` trong `scenes_json` và `hook_frame_sharpness >= 12.0`.

## Common Mistakes
- **Sai lầm 1 (Giấu tên thực thể để câu kéo tò mò)**: Đặt tiêu đề kiểu *"Con vật này có lực cắn 1.100 PSI"* thay vì *"Hiena vs Leão: A Mordida de 1.100 PSI Que Esmaga Ossos"*, làm mất trắng `200+ views` từ `YT_SEARCH` và khiến video tắt thở ngay sau 24 giờ đầu.
- **Sai lầm 2 (Cắt ngắn kịch bản xuống `< 25s` để ép `APV` ảo)**: Làm mất chiều sâu giải thích khoa học/sinh tồn, khiến kịch bản bị loãng và không chuyển đổi được người xem thành Subscriber.
- **Sai lầm 3 (Chỉ nhìn `APV` trung bình mà bỏ qua độ dốc Ngày 2)**: Không tách dữ liệu `Ngày 1 vs Ngày 2`, dẫn đến không phát hiện được các chủ đề kén khách bị sập `-40% APV` khi bước ra tệp khán giả đại chúng.
