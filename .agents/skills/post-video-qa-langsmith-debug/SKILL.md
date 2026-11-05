---
name: post-video-qa-langsmith-debug
description: >-
  Quy chuẩn bắt buộc sau mỗi lần tạo video (Shorts 9:16 hoặc Long-Form 16:9) hoặc nâng cấp luồng dựng video:
  Bắt buộc tự động kiểm tra chất lượng video đầu ra (thông số kỹ thuật + trích xuất frame hình ảnh thực tế),
  truy vết và debug sâu qua LangSmith Waterfall Trace (`app.utils.langsmith_tracer`) & Log hệ thống (`storage/logs/app.log`),
  tự động sửa triệt để mọi lỗi/cảnh báo/điểm nghẽn ở tận gốc (root-cause) rồi mới hoàn thiện và báo cáo.
---

# Quy Chuẩn Hậu Kiểm Chất Lượng Video, Debug LangSmith/Log & Tự Động Hoàn Thiện (`post-video-qa-langsmith-debug`)

> **Luật Thép (The Closed-Loop Video QA & Telemetry Law):**
> **MỖI KHI TẠO XONG VIDEO HOẶC NÂNG CẤP LUỒNG DỰNG VIDEO, TUYỆT ĐỐI KHÔNG BÁO CÁO XONG NGAY KHI FILE `.MP4` VỪA XUẤT RA. BẮT BUỘC PHẢI KIỂM TRA CHẤT LƯỢNG HÌNH ẢNH/KỸ THUẬT CỦA VIDEO + QUÉT TRUY VẾT LANGSMITH & LOG HỆ THỐNG + SỬA TRIỆT ĐỂ MỌI LỖI PHÁT SINH RỒI MỚI HOÀN THIỆN.**

---

## 1. Quy Trình 4 Bước Khép Kín Sau Khi Tạo Video (The 4-Stage Post-Video Audit & Auto-Heal Loop)

### Bước 1 — Kiểm Định Chất Lượng Kỹ Thuật & Thị Giác Thực Tế Của File `.mp4` (Technical & Multimodal Frame QA)
Ngay sau khi một `VideoJob` hoàn thành:
1. **Kiểm tra 5 Bộ Điểm Grader & Thông số Kỹ thuật (`VideoJob.quality_scores`)**:
   - Đọc trực tiếp từ DB (`quality_scores` & `scenes_json`):
     - `script_critic`: `overall_score >= 8.0/10`
     - `hook_video_grader`: `best_score >= 8.0/10`, thời lượng Hook mở màn chuẩn (`>= 4.0s` cho Shorts; Chapter 1 Hook cuốn hút cho Long-form).
     - `visual_critic`: `overall_score >= 8.0/10`, **tỷ lệ thời lượng video chuyển động thật bắt buộc đạt `> 85.0%` (`video_duration_ms / total_duration_ms > 0.85`, tương đương `5–7` đoạn video `.mp4` độc lập tải về; với video 7 cảnh phải đạt `6–7/7` cảnh là video chạy thật, tối đa chỉ được phép có 0 hoặc 1 cảnh ảnh tĩnh `< 15%` thời lượng)**, không có cảnh bị lệch chủ đề. Nếu thời lượng video chạy `<= 85.0%` hoặc có từ 2 cảnh ảnh tĩnh trở lên -> Kết luận **FAIL** ngay lập tức và bắt buộc sửa tận gốc trước khi hoàn thành!
     - `audio_qa`: `passed == True`, tốc độ đọc tự nhiên (`145–185 WPM`), không bị cắt cụt đuôi câu.
     - `video_qa`: `score >= 95/100`, đúng độ phân giải mục tiêu (`1080x1920` cho Shorts `9:16` hoặc `1920x1080` cho Long-form `16:9`), `black_max_sec == 0`, `freeze_max_sec == 0`, `av_drift_ms < 150ms`, `hook_frame_sharpness >= 12.0`.
2. **Trích xuất Contact Sheet Frames & Nhìn Trực Tiếp Bằng Mắt AI (`read_file`)**:
   - Dùng `ffmpeg` trích xuất dải khung hình đại diện (`tile=4x2` hoặc `4x1`) ở đầu, giữa và cuối video `.mp4`, sau đó gọi `read_file` lên ảnh vừa trích xuất để kiểm tra:
     - Có bị viền đen bất thường, méo tỷ lệ khung hình (`16:9` bị bóp dọc hoặc `9:16` bị bóp ngang) không?
     - Có bị dính watermark/logo kênh khác, phụ đề TikTok/Shorts cũ đè chồng, hoặc rò rỉ chữ Tiếng Việt trên kênh Tiếng Anh/Bồ Đào Nha không?
     - Hình ảnh các cảnh có ăn khớp với chủ thể kịch bản không?

### Bước 2 — Truy Vết LangSmith Waterfall & Quét Log Hệ Thống (`LangSmith & Log Deep-Debug`)
1. **Chạy công cụ chẩn đoán LangSmith Waterfall cho Job vừa tạo**:
   ```powershell
   python -m app.utils.langsmith_tracer --job <JOB_ID>
   ```
   *(Hoặc kiểm tra file `storage/traces/job_<JOB_ID>.json`)*.
2. **Phân tích các chỉ số trong LangSmith Trace & Log (`storage/logs/app.log` / Task Output)**:
   - Kiểm tra thời gian chạy của từng Node (`generate_script`, `generate_audio`, `produce_hook_video`, `fetch_media`, `refine_media`, `render_video`) và từng Sub-Span (`MediaTools.*`, `VisualCurationAgent.*`, `ContentAgent.*`).
   - Quét tìm mọi dòng `ERROR`, `WARNING`, `Traceback`, `Exception`, `Failed`, `429`, `503`, `Read timed out`, hoặc nhánh fallback bất thường xảy ra trong quá trình chạy.

### Bước 3 — Truy Vết Ngược & Sửa Triệt Để Lỗi Phát Sinh (`Root-Cause Fix`)
Nếu ở **Bước 1** hoặc **Bước 2** phát hiện bất kỳ vấn đề nào (ví dụ: cảnh video bị lỗi tỷ lệ `1080x1920` trong video ngang `1920x1080`, một `@tool` bị lỗi ngầm rơi về fallback kém chất lượng, phụ đề bị lệch, hoặc một bước bị nghẽn cổ chai bất thường):
1. Tuân thủ nghiêm ngặt kỹ năng [`systemic-root-cause-bugfix`](../systemic-root-cause-bugfix/SKILL.md): Truy vết ngược từ triệu chứng trong log/trace về đúng file code hoặc cấu hình YAML gây lỗi.
2. **Sửa ngay tận gốc** (không dùng từ khóa vá tạm bợ, không che giấu lỗi bằng `except: pass`).

### Bước 4 — Xác Minh Lại, Dựng Lại (Nếu Cần) & Đồng Bộ GitHub (`Re-Verify & Push`)
1. Chạy `python -m py_compile` cho toàn bộ các file vừa sửa.
2. Nếu lỗi vừa sửa ảnh hưởng trực tiếp đến chất lượng hiển thị/âm thanh của video vừa tạo, chạy lại bước render/cập nhật để đảm bảo bản `.mp4` cuối cùng gửi cho người dùng đạt chất lượng hoàn hảo.
3. Thực thi [`auto-optimize-merge-push`](../auto-optimize-merge-push/SKILL.md) (`git add` -> `git commit` -> `git push origin main`) trước khi gửi báo cáo tổng kết kèm số liệu LangSmith + QA thực tế cho người dùng.

