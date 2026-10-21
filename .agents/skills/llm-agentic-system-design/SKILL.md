---
name: llm-agentic-system-design
description: >-
  Quy chuẩn kiến trúc chuyên sâu cho mọi dự án AI, LLM, RAG, Multimodal Vision và Multi-Agent
  (LangGraph, AutoGen, CrewAI, FastAPI AI Services): Phân tách rạch ròi giữa tầng tư duy ngữ nghĩa
  của LLM và tầng điều phối kỹ thuật, tối ưu Batch Prompting giảm 80% độ trễ/chi phí, chống Overfitting
  và đảm bảo khả năng chịu lỗi (Fault-Tolerance / Key Rotation).
---

# Quy Chuẩn Thiết Kế Hệ Thống AI / LLM / Multi-Agent (`llm-agentic-system-design`)

Sử dụng skill này cho tất cả các dự án có tích hợp LLM, Vision AI, Audio/TTS, RAG, hoặc hệ thống Đa Tác Nhân (Multi-Agent).

---

## 1. Ranh Giới Vàng: Tầng Ngữ Nghĩa (LLM) vs Tầng Kỹ Thuật (Code)

1. **Tầng Ngữ Nghĩa (Semantic Layer — Do LLM / Multimodal Vision đảm nhiệm)**:
   - Mọi tác vụ đòi hỏi hiểu ngôn ngữ, bối cảnh, sắc thái cảm xúc, phân loại chủ đề, tạo câu truy vấn tìm kiếm, hoặc đánh giá sự phù hợp của hình ảnh/video **BẮT BUỘC phải đặt trong System Prompt + Structured JSON Output**.
   - **Cấm dùng regex/từ khóa cứng trong code** để cố bắt chước khả năng hiểu ngôn ngữ của LLM (vì sẽ gây Overfitting vào một vài từ khóa hẹp).
2. **Tầng Điều Phối & Kỹ Thuật (Deterministic Engineering Layer — Do Code đảm nhiệm)**:
   - Code Python/TypeScript chỉ đảm nhiệm các việc cần tính chính xác toán học 100%:
     - Điều phối luồng (LangGraph State, Async/ThreadPool concurrency).
     - Kiểm tra tính hợp lệ của JSON/Schema (Pydantic validation, regex trích xuất JSON an toàn).
     - Đo lường vật lý/kỹ thuật độc lập chủ đề (độ phân giải, bitrate, thời lượng, độ nét biên cạnh, cắt ghép media).
     - Quản lý bộ nhớ đệm (Caching / RAG deduplication) và xoay vòng API Key.

---

## 2. Tối Ưu Tốc Độ, Chi Phí & Độ Tin Cậy (Performance & Fault-Tolerance)

1. **Ưu tiên Gộp Lô (`Batch Prompting` Over Sequential Loops)**:
   - Nếu cần phân tích hoặc chấm điểm $N$ mục (ví dụ: 6 phân cảnh kịch bản hoặc 5 bức ảnh ứng viên), hãy gộp tất cả vào **1 lần gọi LLM/Vision duy nhất** trả về mảng JSON thay vì gọi LLM $N$ lần trong vòng lặp `for` (giúp giảm 75–85% độ trễ và tránh chạm giới hạn RPM/Quota).
2. **Luôn Có `Timeout`, Xoay Vòng Key/Model & Cơ Chế Dự Phòng (Graceful Degradation)**:
   - Mọi lệnh gọi API ngoại vi (LLM, Search, Stock, TTS) **bắt buộc phải có `timeout` rõ ràng**.
   - Khi gặp lỗi `429 Rate Limit`, `503 Overloaded`, hoặc `Timeout`, tự động đánh dấu cooldown cho cặp `(model, api_key)` đó và chuyển ngay sang model/key tiếp theo mà không làm đứt gãy toàn bộ pipeline.
3. **Truy Vết & Quan Sát Được (Observability & Tracing)**:
   - Gắn span/log rõ ràng tại đầu vào và đầu ra của mỗi Agent/Node (ghi lại câu truy vấn, điểm số, và lý do `rationale` mà LLM đưa ra) để khi cần debug trên LangSmith hoặc terminal có thể nhìn thấy ngay nguyên nhân gốc rễ.
