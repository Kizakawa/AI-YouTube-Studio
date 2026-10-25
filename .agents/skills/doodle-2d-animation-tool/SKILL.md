---
name: doodle-2d-animation-tool
description: Công cụ dựng video hoạt họa 2D Doodle / Stickman Explainer (1080x1920 @ 24 FPS) trên nền giấy kem ấm (#F7F3E8) với khóa nhân vật @CHARACTER_TAG (@MODERNYOU, @PLEASER, @MANIPULATOR, @ANCESTOR, @FARMER), 20 chuyển động máy quay EyeCandy và 8 đạo cụ ẩn dụ trực quan. Sử dụng khi làm video thuộc các ngách Tâm lý học Đen (Dark Psychology), Nghịch lý cuộc sống hiện đại, Tài chính cá nhân & Bẫy tiền bạc, Cơ chế não bộ & Dopamine Detox, hoặc khi phân cảnh là khái niệm trừu tượng không thể quay bằng máy ảnh thật.
---

# GOHA 2D Doodle & Stickman Explainer Tool (`doodle_2d_animator`)

## 1. Khi nào Agent PHẢI gọi Tool này (`WHEN TO USE`)
Gọi công cụ [`DoodleAnimationTool`](file:///d:/Python/Youtube/app/services/media/doodle_animation_tool.py) (`tool = "doodle_2d_animator"`) khi chủ đề hoặc phân cảnh thuộc một trong **5 nhóm nội dung trừu tượng / ẩn dụ**:
1. **Tâm lý học Đen & Hành vi Con người (`dark_psychology`, `satirical_psychology_animation`):**
   - *Chủ đề:* Gaslighting, Thao túng tâm lý, Narcissist vs Empath, People-Pleaser, Silent Treatment, Cortisol Spike.
   - *Đạo cụ ẩn dụ:* `puppet_strings` (`@MANIPULATOR` giật dây con rối `@PLEASER`).
2. **Nghịch lý Cuộc sống Hiện đại & Triết học Châm biếm (`modern_paradox`, `psychology_stoicism`):**
   - *Chủ đề:* Modern Worker vs Ancient Hunter (15h/week paradox), 8-Hour Rat-Race, Burnout, Overthinking.
   - *Đạo cụ ẩn dụ:* `alarm` (đồng hồ báo thức thứ Hai), `block` (khối tạ 500-lb đè lên vai), `split` (so sánh 2 nửa màn hình).
3. **Tài chính Cá nhân, Kinh tế & Bẫy Tiền bạc (`personal_finance`, `wealth_traps`):**
   - *Chủ đề:* Bẫy nợ thẻ tín dụng 24% APR, Lạm phát, Lãi kép, Tại sao lương cao vẫn nghèo.
   - *Đạo cụ ẩn dụ:* `money_scale` (cán cân `$` vs `DEBT`), `block` (khối nợ đè nặng).
4. **Cơ chế Não bộ, Dopamine Detox & Thói quen (`brain_hacks`, `dopamine_detox`):**
   - *Chủ đề:* Nghiện TikTok/Dopamine, Thiếu ngủ 72h, Phương pháp học Feynman vs Học vẹt.
   - *Đạo cụ ẩn dụ:* `brain_battery` (cục pin não bộ tụt xuống vùng đỏ `12%`).
5. **Lịch sử Kỳ quặc & Khoa học Giả định (`absurd_history`, `what_if_science`):**
   - *Chủ đề:* Những nghịch lý lịch sử hài hước hoặc câu hỏi giả định "Điều gì xảy ra nếu...".

## 2. Khi nào Agent TUYỆT ĐỐI KHÔNG gọi Tool này (`WHEN NOT TO USE`)
- **KHÔNG** dùng cho kênh **Động vật hoang dã săn mồi (`wildlife_predators`)**, **Cứu hộ động vật (`animal_rescue`)**, hoặc **Thiên nhiên đời thực (`REAL_FILMABLE`)** $\rightarrow$ Các ngách này bắt buộc dùng `pexels_video_api` (`>= 80%` video quay thật).
- **KHÔNG** dùng cho **Bí ẩn đại dương sâu / Thủy quái (`DEEP_SEA_MYSTERY`)** $\rightarrow$ Dùng `creature_horror_art` / `generative_video`.

---

## 3. Bảng Thẻ Nhân Vật Đồng Nhất (`@CHARACTER_TAG`) & 8 Đạo Cụ Ẩn Dụ (`prop_type`)

| Thẻ `@CHARACTER_TAG` | Màu Áo Nhận Diện | Vai trò trong kịch bản |
| :--- | :--- | :--- |
| `@MODERNYOU` | `#4A6FA5` (Xanh xám) | Nhân viên văn phòng hiện đại mệt mỏi, người hay suy nghĩ quá mức (Overthinker) |
| `@PLEASER` | `#5B8C85` (Xanh lục nhạt) | Người luôn chiều lòng người khác (People-Pleaser), nạn nhân bị thao túng |
| `@MANIPULATOR` | `#9E2A2B` (Đỏ thẫm) | Kẻ thao túng tâm lý (Narcissist / Sociopath / Toxic Boss) |
| `@ANCESTOR` | `#C87D32` (Vàng đất) | Thợ săn tiền sử thảnh thơi / Nhà hiền triết Khắc kỷ (Stoic Sage) |
| `@FARMER` | `#8B5E3C` (Nâu đất) | Người lao động quá tải / Con nợ còng lưng dưới khối tạ công việc |

### 8 Đạo cụ `prop_type` hỗ trợ sẵn:
- `"alarm"`: Đồng hồ báo thức đỏ rung lắc dữ dội (áp lực thời gian, sáng thứ Hai).
- `"block"`: Khối tạ đá khổng lồ rơi xuống đè còng lưng nhân vật (burnout, áp lực, nợ nần).
- `"spear_nature"`: Nhân vật cầm giáo gỗ dưới ánh mặt trời vàng (tự do, tối giản, thiên nhiên).
- `"split"`: Chia đôi màn hình đỏ/xanh đối chiếu 2 lối sống (`CHAOS / TRAP` vs `CLARITY`).
- `"puppet_strings"`: `@MANIPULATOR` cười nhếch mép giật dây chữ thập điều khiển `@PLEASER`.
- `"brain_battery"`: Cục pin năng lượng não bộ tụt dần xuống mức báo động đỏ.
- `"money_scale"`: Cán cân tài chính nghiêng lệch giữa đồng vàng `$` và quả tạ `DEBT`.
- `"climax"`: Bóng đèn bừng sáng trên đầu nhân vật (nhận ra sự thật / giải pháp cuối bài).

---

## 4. Cách Gọi Tool Trong Code Python & Pipeline

### A. Tự động qua `VisualToolRouter`, `VideoStrategistAgent` và `AIVideoAgent`
Khi `channel_niche` là `satirical_psychology_animation` / `dark_psychology` / `personal_finance` hoặc trong kịch bản có `@MODERNYOU`, `@PLEASER`, `@MANIPULATOR`, `VisualToolRouter.route_scene()` sẽ tự động gán:
- `reality_class = "DOODLE_PSYCHOLOGY_EXPLAINER"`
- `tool = "doodle_2d_animator"`
Và `AIVideoAgent.generate_image_to_video()` sẽ tự động gọi `DoodleAnimationTool.render_scene_clip()`.

### B. Gọi trực tiếp từ Python Script hoặc Agent
```python
from app.services.media.doodle_animation_tool import DoodleAnimationTool

# 1. Kiểm tra xem chủ đề có nên dùng 2D Doodle không
should_use, reason = DoodleAnimationTool.should_activate(
    topic="Dark Empathetic vs Narcissist: The 3-Second Mirror Trap",
    niche="dark_psychology",
)

# 2. Dựng 1 clip 2D Doodle 24 FPS (1080x1920)
clip_path = DoodleAnimationTool.render_scene_clip(
    narration="A narcissist never argues with your logic; they pull invisible guilt strings until you apologize.",
    output_path="output/scene_01_doodle.mp4",
    duration_seconds=5.0,
    scene_id=1,
    character_tag="@MANIPULATOR",
    prop_type="puppet_strings",
    camera_motion="dolly_in",
)
```
