---
name: subagent-parallel-execution
description: >-
  Quy chuẩn điều phối Subagents chạy song song và quản lý ngữ cảnh (dựa trên obra/superpowers
  subagent-driven-development & dispatching-parallel-agents): Tự động phân tách các tác vụ độc lập
  (nghiên cứu codebase, kiểm tra nhiều module, chạy tác vụ nền) để tăng tốc độ xử lý và giữ sạch
  Context Window của phiên chính.
---

# Quy Chuẩn Điều Phối Subagent & Thực Thi Song Song (`subagent-parallel-execution`)

Sử dụng skill này khi xử lý các tác vụ phức tạp, khảo sát diện rộng trên codebase lớn, hoặc cần thực hiện nhiều luồng công việc độc lập cùng lúc.

---

## 1. Khi Nào Nên Khởi Chạy Subagent hoặc Chạy Song Song?

1. **Khảo sát & Tìm kiếm diện rộng (`research` subagent)**:
   - Khi cần đọc/quét qua hàng chục file, tra cứu tài liệu lớn, hoặc truy vết lịch sử lỗi dài mà nếu đọc trực tiếp sẽ làm đầy bộ nhớ ngữ cảnh (Context Window) của Agent chính.
2. **Các nhánh công việc độc lập không đụng chạm cùng 1 file**:
   - Khi người dùng yêu cầu thực hiện nhiều việc độc lập (ví dụ: vừa kiểm tra số liệu API, vừa rà soát cấu hình hệ thống, vừa phân tích một module khác), hãy gọi các tool đọc/truy vấn song song trong cùng 1 lượt hoặc giao cho các subagent chuyên biệt.
3. **Cấm Polling Thủ Công (Reactive Wakeup)**:
   - Sau khi khởi chạy một subagent hoặc background task, **tuyệt đối không chạy vòng lặp `sleep` hay kiểm tra trạng thái liên tục**. Hệ thống sẽ tự động đánh thức Agent ngay khi tiến trình nền hoàn tất.

---

## 2. Kiểm Duyệt Chéo Kết Quả Từ Subagent (Trust But Verify)

- Khi một subagent hoàn thành công việc và trả về báo cáo hoặc bản sửa code:
  1. **Kiểm tra tuân thủ kiến trúc**: Đảm bảo subagent không vi phạm quy tắc chống Overfitting / không hardcode từ khóa.
  2. **Chạy xác minh độc lập**: Kiểm tra lại bằng `py_compile` / test trước khi gộp (merge) kết quả vào nhánh `main`.
