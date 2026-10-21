---
name: systemic-root-cause-bugfix
description: >-
  Quy chuẩn bắt buộc khi sửa lỗi (bugfix) và tối ưu hệ thống AI YouTube Studio (tích hợp từ chuẩn
  mã nguồn mở GitHub obra/superpowers: systematic-debugging, root-cause-tracing, defense-in-depth,
  verification-before-completion và Anti-Reward-Hacking): Luôn truy vết ngược về tận gốc kiến trúc
  toàn hệ thống (System Prompts, Multimodal Vision, luồng dữ liệu liên tầng và cổng kỹ thuật CV tổng
  quát). Tuyệt đối CẤM Overfitting vào bài test hoặc vá lỗi cục bộ bằng từ khóa cứng (hardcode).
---

# Quy Chuẩn Sửa Lỗi Tận Gốc & Chống Overfitting Toàn Hệ Thống (`systemic-root-cause-bugfix`)

> **Tích hợp & Tùy biến từ chuẩn `SKILL.md` nổi tiếng trên GitHub (`obra/superpowers` — `systematic-debugging`, `root-cause-tracing`, `defense-in-depth`, `verification-before-completion`) kết hợp với kiến trúc Đa Tác Nhân (LangGraph Multi-Agent) của AI YouTube Studio.**

---

## 1. Luật Thép (The Iron Laws — Cấm Vi Phạm Dưới Mọi Hình Thức)

1. **LUẬT THÉP #1 — KHÔNG ĐIỀU TRA GỐC RỄ, CẤM VIẾT CODE SỬA (`No Fixes Without Root-Cause Tracing First`)**:
   - Tuyệt đối không đoán mò, không sửa ngay tại dòng code cuối cùng nơi triệu chứng xuất hiện khi chưa truy vết ngược (`Backward Trace`) toàn bộ luồng dữ liệu.
2. **LUẬT THÉP #2 — CẤM OVERFITTING VÀO BÀI TEST / CẤM VÁ TỪ KHÓA (`Zero Test-Case Overfitting & Zero Keyword-Patching`)**:
   - **CẤM TUYỆT ĐỐI** viết điều kiện `if/else` hoặc danh sách từ khóa theo chủ đề, loài vật, thời tiết, địa danh cụ thể (ví dụ: `if "snow" in ...`, `if "tiger" in ...`, `if "honey badger" in ...`, `if "leviathan" in ...`) hay nối chuỗi tĩnh (`+ " snow"`, `+ " shorts"`) trong code Python để ép 1 bài test vượt qua.
   - **Bài Kiểm Tra Phản Thực Tế Bắt Buộc (The 4-Domain Counterfactual Test)**: Trước khi thêm bất kỳ dòng code nào vào Python, hãy tự hỏi:
     > *"Nếu ngày mai hệ thống chạy 4 chủ đề hoàn toàn khác nhau: (1) Hổ Siberia mùa đông, (2) Ám sát Julius Caesar thời La Mã, (3) Thao túng tâm lý học Dark Noir, (4) Thủy quái đáy vực Mariana — dòng code này có tự động đúng cho cả 4 mà không cần thêm từ khóa mới không?"*
     - Nếu câu trả lời là **Không** $\rightarrow$ Đó là **Overfitting (Reward Hacking)**. Bắt buộc hủy bỏ cách sửa đó và đẩy tư duy lên tầng **LLM System Prompt / Multimodal Vision**!
3. **LUẬT THÉP #3 — QUY TẮC 2 LẦN THỬ (`The 2-Strike Architectural Escalation Rule`)**:
   - Theo chuẩn `obra/superpowers`: Nếu sửa 1 lỗi mà phải thêm ngoại lệ lần thứ 2, hoặc sửa chỗ này lại phình lỗi ở chỗ khác $\rightarrow$ **DỪNG LẠI NGAY**. Vấn đề nằm ở thiết kế luồng dữ liệu hoặc System Prompt giữa các tầng, không phải ở ngưỡng lọc cục bộ.

---

## 2. Kỹ Thuật Truy Vết Ngược Tận Gốc (`Backward Root-Cause Tracing`)

Khi phát hiện video đầu ra (`.mp4`), LangSmith trace, hoặc log bị lỗi (ví dụ: thiếu video Hook, ảnh lệch môi trường, ảnh ghép 2 khung, dính logo), bắt buộc truy ngược từ **Triệu chứng cuối** về **Nguồn phát sinh đầu tiên** qua 6 tầng của AI YouTube Studio:

```
[Tầng 6: FFmpeg Render] ⬅ [Tầng 5: VisualCritic / VideoMemoryRAG] ⬅ [Tầng 4: StockFetcher / MediaTools]
⬅ [Tầng 3: VisualCurationAgent (Vision Audit)] ⬅ [Tầng 2: Query Normalization] ⬅ [Tầng 1: LLM System Prompt]
```

- **Ví dụ điển hình của Truy Vết Ngược chuẩn**:
  - *Triệu chứng ở Tầng 6*: Cảnh 2 xuất hiện ảnh ghép 2 nửa màn hình (Hổ vs Báo có chữ VS).
  - *Nếu sửa sai (Vá triệu chứng)*: Chặn từ khóa `"tiger"` hoặc viết hàm đếm màu lông hổ ở Tầng 5.
  - *Truy vết ngược đúng chuẩn (`root-cause-tracing`)*:
    1. Vì sao tải phải ảnh ghép 2 nửa màn hình? $\rightarrow$ Vì Google Images nhận câu truy vấn có tên cả 2 loài (`"siberian tiger amur leopard..."`).
    2. Vì sao câu truy vấn lại chứa tên cả 2 loài trong khi LLM ở Tầng 1 chỉ viết `"amur leopard snow"`? $\rightarrow$ Truy ngược lên Tầng 2 (`_enforce_subject_queries`) phát hiện hàm hậu xử lý tự ý lấy `core_visual_entity` (Loài A) ghép đè vào câu truy vấn của Loài B!
    3. Đồng thời ở Tầng 3 (`curate_candidate_photos`), bước kiểm duyệt Gemini Vision từng bị tắt nên không nhìn ra ảnh ghép đôi.
    4. **Sửa tận gốc**: Sửa hàm hậu xử lý ở Tầng 2 tôn trọng truy vấn AI + Bật lại kiểm duyệt ảnh Gemini Vision 1-call ở Tầng 3 + Giữ cổng phát hiện đường kẻ chia khung (`1–5px` divider line) tổng quát ở Tầng 5.

---

## 3. Kỹ Thuật Phòng Thủ Đa Tầng (`Defense-in-Depth` cho Hệ Thống AI Video)

Theo chuẩn `defense-in-depth` từ `obra/superpowers`, một giải pháp chống lỗi bền vững không đặt toàn bộ gánh nặng lên 1 hàm duy nhất, mà phân bổ đúng trách nhiệm cho **4 lớp bảo vệ độc lập chủ đề**:

| Lớp Bảo Vệ | File Chịu Trách Nhiệm | Trách Nhiệm Chuẩn (Tuyệt Đối Không Trộn Lẫn) |
| :--- | :--- | :--- |
| **Lớp 1: Lập Kế Hoạch & Truy Vấn (LLM Prompt Layer)** | [`hook_video_node.py`](../../app/graph/nodes/hook_video_node.py), [`visual_curation_agent.py`](../../app/agents/visual_curation_agent.py), [`content_agent.py`](../../app/agents/content_agent.py) | Dùng **System Prompt** hướng dẫn LLM tạo câu truy vấn ngắn gọn (2–4 từ), **đơn chủ thể** (cấm `"A vs B"`), và **tự động đính kèm từ khóa môi trường/mùa/thời đại cốt lõi** của kịch bản. |
| **Lớp 2: Chuẩn Hóa Dữ Liệu (State & Query Hygiene Layer)** | [`visual_curation_agent.py`](../../app/agents/visual_curation_agent.py), [`media_tools.py`](../../app/graph/tools/media_tools.py), [`stock_fetcher.py`](../../app/services/media/stock_fetcher.py) | Chỉ làm sạch ký tự rác, thuật ngữ góc máy (`8k`, `macro shot`), chặn kênh thương mại chèn watermark (`Pond5`, `Shutterstock`, `NatureFootage`). **Không tự ý ghép đè thực thể làm biến dạng câu truy vấn của AI.** |
| **Lớp 3: Giám Khảo Thị Giác Đa Phương Thức (Multimodal Vision Layer)** | [`visual_curation_agent.py`](../../app/agents/visual_curation_agent.py) (`curate_candidate_videos`, `curate_candidate_photos`), [`hook_video_node.py`](../../app/graph/nodes/hook_video_node.py) | Dùng **Gemini Vision** nhìn trực tiếp vào ảnh/frame ứng viên để kiểm duyệt ngữ nghĩa: đúng loài/chủ thể, đúng hệ sinh thái/mùa/thời đại, ngoài tự nhiên thật (không phải chuồng sở thú), không phải ảnh ghép chia đôi. |
| **Lớp 4: Cổng Kỹ Thuật & Thị Giác Máy Tính (Topic-Agnostic CV & FFmpeg Gate)** | [`visual_critic.py`](../../app/graph/evaluators/visual_critic.py), [`video_memory_rag.py`](../../app/services/video_memory_rag.py), [`ffmpeg_helper.py`](../../app/utils/ffmpeg_helper.py) | Thuật toán toán học/CV **độc lập 100% với chủ đề**: đo độ phân giải, độ sắc nét biên cạnh (`FIND_EDGES`), biên độ chuyển động thực tế, phát hiện vùng chữ/đường kẻ chia khung (`1–5px`), watermark góc cố định, và cắt lề an toàn (`fair_use_transform`). |

---

## 4. Cổng Xác Minh Trước Khi Báo Cáo Hoàn Thành (`Verification-Before-Completion`)

Theo chuẩn `verification-before-completion`:
1. **Quét Ngang Toàn Hệ Thống (Horizontal Sweep)**:
   - Sửa ở `analyze_scenes_batch` thì bắt buộc kiểm tra luôn `analyze_scene_context`.
   - Sửa ở `curate_candidate_videos` thì bắt buộc kiểm tra luôn `curate_candidate_photos` và `HookVideoNode`.
   - Kiểm tra `git diff` để chắc chắn **0% từ khóa chủ đề/loài vật/thời tiết cứng** bị lọt vào code Python.
2. **Chứng Minh Bằng Lệnh Thực Thi Mới Nhất**:
   - Chạy `python -m py_compile` và smoke test import trên toàn bộ các file vừa sửa.
   - Khi test tạo video, kiểm tra cả log quyết định của LLM/Vision lẫn các frame trích xuất thực tế từ file `.mp4` trước khi kết luận hoàn tất và chạy [`auto-optimize-merge-push`](../auto-optimize-merge-push/SKILL.md).

Xem thêm tài liệu chi tiết các mẫu Anti-Pattern cần tránh tại: [references/root-cause-tracing-and-defense-in-depth.md](references/root-cause-tracing-and-defense-in-depth.md).
