---
name: verification-before-completion
description: >-
  Quy chuẩn bắt buộc trước khi kết luận hoàn thành bất kỳ tác vụ lập trình hoặc sửa lỗi nào (dựa trên
  obra/superpowers verification-before-completion): Cấm tuyệt đối tuyên bố "Đã xong / Đã sửa được"
  nếu chưa chạy lệnh kiểm chứng thực tế mới nhất (compile, test, smoke check, kiểm tra đầu ra) ngay
  trong phiên hiện tại.
---

# Cổng Xác Minh Bắt Buộc Trước Khi Báo Cáo Hoàn Thành (`verification-before-completion`)

> **Luật Thép (The Iron Law — từ `obra/superpowers`):**
> **TUYỆT ĐỐI KHÔNG TUYÊN BỐ HOÀN THÀNH KHI CHƯA CÓ BẰNG CHỨNG KIỂM CHỨNG MỚI NHẤT (`NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE`).**

---

## 1. Quy Trình Cổng Kiểm Chứng 5 Bước (The 5-Step Verification Gate)

Trước khi viết câu trả lời báo cáo "Đã xong", "Đã fix", hoặc "Hệ thống đã ổn định" cho người dùng, Agent **BẮT BUỘC** phải thực hiện đủ 5 bước:

1. **Xác định lệnh chứng minh (Identify)**:
   - Xác định lệnh terminal cụ thể nào chứng minh thay đổi vừa viết thực sự chạy đúng (ví dụ: `python -m py_compile ...`, `pytest`, `npm run build`, lệnh smoke-test import, hoặc script trích xuất/kiểm tra kết quả thực tế).
2. **Chạy thực tế mới nhất (Run Freshly)**:
   - Chạy lệnh đó **sau** lần sửa file cuối cùng. Kết quả chạy từ trước khi sửa file cuối cùng không còn giá trị.
3. **Đọc toàn bộ kết quả & Mã thoát (Read Full Output & Exit Code)**:
   - Kiểm tra `exit code == 0`, đọc kỹ `stdout` và `stderr`. Không được bỏ qua các cảnh báo `ImportError`, `SyntaxWarning`, `DeprecationWarning`, hoặc lỗi ngầm trong `try/except`.
4. **Kiểm chứng chéo dữ liệu đầu ra (Verify Output Artifact/State)**:
   - Nếu tác vụ tạo ra file (ảnh, video, JSON, bản ghi DB, giao diện UI), phải kiểm tra kích thước file (`> 0 bytes`), cấu trúc dữ liệu, hoặc frame/screenshot thực tế thay vì chỉ tin rằng hàm không văng lỗi.
5. **Báo cáo kèm bằng chứng (Report With Evidence)**:
   - Chỉ khi 4 bước trên vượt qua 100%, mới được báo cáo hoàn thành kèm theo thông số thực tế vừa kiểm chứng.

---

## 2. Các Hành Vi Bị Cấm Tuyệt Đối (Red Flags)

- ❌ Dùng các cụm từ phỏng đoán: *"Lẽ ra giờ nó sẽ chạy đúng"*, *"Tôi nghĩ lỗi đã được giải quyết"*, *"Code nhìn ổn rồi"* mà chưa chạy lệnh xác minh.
- ❌ Sửa thêm 1 dòng code nhỏ ở phút chót rồi báo cáo xong luôn mà không chạy lại `py_compile` / test.
- ❌ Che giấu lỗi bằng cách bọc `try: ... except Exception: pass` để lệnh chạy qua mà không thực sự giải quyết nguyên nhân.

