---
name: architecture-first-design
description: >-
  Quy chuẩn thiết kế kiến trúc phần mềm sạch, mở rộng tốt và chống phình code (God-file anti-pattern)
  cho mọi dự án hiện tại và tương lai: Bắt buộc phân tích luồng dữ liệu, đảm bảo Single Responsibility,
  tương thích ngược (backward compatibility) và mô-đun hóa rõ ràng trước khi viết hoặc mở rộng tính năng.
---

# Quy Chuẩn Thiết Kế Kiến Trúc Trước Khi Viết Code (`architecture-first-design`)

Sử dụng skill này mỗi khi xây dựng tính năng mới, thiết kế dự án mới, hoặc tái cấu trúc (refactor) bất kỳ hệ thống phần mềm nào.

---

## 1. Nguyên Tắc Thiết Kế Cốt Lõi (Clean & Scalable Architecture)

1. **Chống Phình File ("No God-Files" Rule)**:
   - Mỗi file / class chỉ đảm nhận **1 trách nhiệm cốt lõi duy nhất (Single Responsibility Principle)**.
   - Khi một file vượt quá `600–800 dòng` hoặc bắt đầu chứa nhiều logic không liên quan (vừa gọi API, vừa xử lý chuỗi, vừa dựng giao diện/render), hãy tách các hàm chuyên biệt sang module con (`services/`, `utils/`, `evaluators/`) thay vì nhồi nhét thêm vào một hàm khổng lồ.
2. **Thiết Kế Luồng Dữ Liệu Rõ Ràng (Explicit Data-Flow Mapping)**:
   - Trước khi viết code, xác định rõ 3 câu hỏi:
     - **Input**: Dữ liệu đầu vào có kiểu/schema gì? Lấy từ đâu (State, DB, API, Env)?
     - **Transformation**: Tầng nào xử lý? Nếu tầng đó thất bại thì fallback về đâu?
     - **Output**: Trả về kiểu dữ liệu gì? Có làm thay đổi (mutate) biến đầu vào ngoài ý muốn không?
3. **Bảo Toàn Tương Thích Ngược (Backward Compatibility)**:
   - Khi thêm tham số mới vào các hàm/method đang được gọi bởi nhiều nơi trong hệ thống, **luôn gán giá trị mặc định (`default=...`)** và chấp nhận `**kwargs` ở các hàm giao tiếp liên tầng để không bao giờ gây lỗi `TypeError: unexpected keyword argument`.
4. **Không Để Lại Rác Kỹ Thuật (Zero Dead Code & Zero Duplicate Logic)**:
   - Khi thay thế một cơ chế cũ bằng cơ chế mới, dọn sạch các biến không còn sử dụng, các lệnh import thừa và các hàm chết ngay trong lần sửa đó.
   - Nếu một đoạn logic xuất hiện từ 2 nơi trở lên, gom thành hàm dùng chung (DRY — Don't Repeat Yourself).

