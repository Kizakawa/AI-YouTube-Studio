# Mascot Asset Pack Template — Living Studio v2.0

Để thêm một linh vật mới vào hệ thống sản xuất video nhạc học tập:
1. Tạo thư mục mới tại `assets/mascots/<mascot_id>/`
2. Tạo file `mascot.json` dựa theo mẫu dưới đây và đặt ảnh nền vào `assets/bgm/06_study_music_lofi/<background_image>`.
3. Chạy lệnh:
   ```bash
   python -m app.services.pomodoro_studio_builder --mascot <mascot_id>
   ```
