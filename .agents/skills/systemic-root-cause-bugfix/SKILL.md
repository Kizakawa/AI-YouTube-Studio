---
name: systemic-root-cause-bugfix
description: >-
  Quy chuẩn bắt buộc khi sửa lỗi (bugfix) và tối ưu hệ thống AI YouTube Studio: Luôn chẩn đoán và
  giải quyết tận gốc ở cấp độ kiến trúc toàn hệ thống (System Prompts, Multimodal Vision, luồng dữ
  liệu liên tầng và cổng kỹ thuật CV tổng quát). Tuyệt đối CẤM vá lỗi cục bộ bằng cách lọc/chèn từ
  khóa cứng (hardcode keyword lists) theo chủ đề hay chỉ xử lý một vài edge-case nhỏ lẻ.
---

# Quy Chuẩn Sửa Lỗi Tận Gốc Toàn Hệ Thống (`systemic-root-cause-bugfix`)

Mỗi khi phát hiện lỗi trong video đầu ra (`.mp4`), trên LangSmith trace, trong log terminal, hoặc theo phản hồi của người dùng, Agent **BẮT BUỘC** phải áp dụng tư duy **Sửa lỗi hệ thống tổng thể (System-Wide Root-Cause Fix)** theo các nguyên tắc và quy trình dưới đây.

---

## 1. Nguyên Tắc Cốt Lõi: CẤM Vá Lỗi Bằng Từ Khóa Cứng (Zero Keyword-Patching Policy)

1. **Tuyệt đối CẤM hardcode từ khóa theo chủ đề / loài vật / bối cảnh để chữa cháy**:
   - **KHÔNG BAO GIỜ** viết các điều kiện kiểu `if "snow" in topic:`, `if "tiger" in query:`, `if "honey badger" in q:`, hoặc tạo danh sách từ khóa tĩnh theo mùa/loài vật/quái vật trong code Python chỉ để vượt qua bài test hiện tại.
   - **KHÔNG BAO GIỜ** tự ý nối thêm từ khóa cứng (như `+ " snow"`, `+ " hunting"`, `+ " shorts"`) bằng chuỗi tĩnh vào truy vấn của mọi video.
2. **Mọi lỗi cụ thể đều là triệu chứng của một lỗ hổng hệ thống**:
   - Khi một video bị lỗi (ví dụ: cảnh mở đầu thiếu video chuyển động, ảnh giữa bài lệch mùa/môi trường, dính ảnh ghép 2 khung hình, hoặc lọt logo bản quyền), hãy xem đó là **bài test đại diện** chỉ ra lỗ hổng trong kiến trúc tổng thể, chứ không phải lỗi của riêng chủ đề đó.
   - Giải pháp đưa ra **phải hoạt động tổng quát 100%** cho mọi chủ đề bất kỳ trên tất cả các kênh (Động vật hoang dã, Bí ẩn đại dương/Vũ trụ, Lịch sử, Tâm lý học, Giải cứu...).

---

## 2. Phân Tách Trách Nhiệm Đúng Tầng Kiến Trúc (Right-Layer Architecture)

Khi sửa bất kỳ lỗi nào liên quan đến nội dung, truy vấn tìm kiếm, hoặc chất lượng hình ảnh/video, bắt buộc đặt giải pháp vào đúng tầng chịu trách nhiệm:

### A. Tầng Ngữ Nghĩa, Truy Vấn & Kiểm Duyệt Bối Cảnh → **Xử lý bằng LLM System Prompt & Multimodal Vision**
- Áp dụng tại: [`VisualCurationAgent`](../../app/agents/visual_curation_agent.py), [`HookVideoNode`](../../app/graph/nodes/hook_video_node.py), [`ContentAgent`](../../app/agents/content_agent.py), và [`media_tools.py`](../../app/graph/tools/media_tools.py).
- **Thiết kế Truy vấn (Query Planning)**:
  - Dùng System Prompt hướng dẫn LLM hiểu bản chất của từng bộ máy tìm kiếm (YouTube Shorts, Pexels/Pixabay API, Google Images): tạo truy vấn ngắn gọn (2–4 từ), mỗi câu chỉ tập trung vào **1 chủ thể duy nhất** (không ghép `"A vs B"` gây trả về ảnh ghép chia đôi hoặc video edit tổng hợp), và tự động gắn kèm môi trường/bối cảnh cốt lõi từ kịch bản.
- **Kiểm duyệt Đa phương thức (Multimodal Vision Curation)**:
  - Dùng Gemini Vision nhìn trực tiếp vào ảnh/khung hình ứng viên để đánh giá sự đồng nhất giữa **Chủ thể + Hệ sinh thái/Mùa/Thời đại + Bối cảnh tự nhiên** so với kịch bản, thay vì đếm màu pixel hay lọc từ khóa URL thủ công.

### B. Tầng Kỹ Thuật, Độ Nét & Chống Bản Quyền → **Xử lý bằng Thuật Toán CV / FFmpeg Độc Lập Chủ Đề**
- Áp dụng tại: [`VisualCritic`](../../app/graph/evaluators/visual_critic.py), [`VideoMemoryRAG`](../../app/services/video_memory_rag.py), [`ffmpeg_helper.py`](../../app/utils/ffmpeg_helper.py), và [`image_quality.py`](../../app/services/media/image_quality.py).
- Mọi ngưỡng kiểm tra kỹ thuật phải dựa trên đặc trưng vật lý tổng quát của khung hình (không phụ thuộc video đang nói về chủ đề gì):
  - Độ phân giải tối thiểu, độ sắc nét biên cạnh (`FIND_EDGES`), độ tương phản, và biên độ chuyển động thực tế giữa các frame.
  - Nhận diện chữ chèn đè (Text-box morphology / OCR), đường kẻ chia khung ảnh ghép (`1–5px` collage divider line), watermark/logo góc cố định (`persistent corner edges + luminance contrast`), và cắt lề an toàn (`fair_use_transform` / `prepare_vertical`).

---

## 3. Quy Trình 4 Bước Khi Sửa Bug Toàn Hệ Thống

1. **Bước 1 — Truy vết Luồng Dữ Liệu Toàn Tuyến (End-to-End Trace)**:
   - Kiểm tra xuyên suốt chuỗi mắt xích từ đầu đến cuối:
     `ContentAgent` ➔ `HookVideoNode` ➔ `VisualCurationAgent (Batch & Single)` ➔ `StockFetcher / MediaTools` ➔ `VisualCritic / VideoMemoryRAG` ➔ `FFmpegHelper`.
   - Tìm ra chính xác mắt xích nào đã làm mất ngữ cảnh, ghi đè biến sai (ví dụ: hàm hậu xử lý ghi đè chủ thể A lên câu truy vấn của chủ thể B), hoặc bỏ sót bước kiểm duyệt.
2. **Bước 2 — Quét Ngang Toàn Bộ Các Luồng Song Song (Horizontal System Sweep)**:
   - Không bao giờ chỉ sửa ở 1 hàm đơn lẻ nếu hệ thống có các nhánh song song:
     - Nếu sửa logic phân tích ngữ cảnh: kiểm tra đồng bộ cả `analyze_scenes_batch` lẫn `analyze_scene_context`.
     - Nếu sửa quy chuẩn chọn tư liệu: kiểm tra đồng bộ cả luồng chọn **Video** (`HookVideoNode`, `curate_candidate_videos`, `_verify_short_clip_with_gemini_vision`) lẫn luồng chọn **Ảnh** (`curate_candidate_photos`).
   - Dọn sạch các đoạn code "vá từ khóa" cũ nếu phát hiện trong quá trình kiểm tra file.
3. **Bước 3 — Kiểm Tra Tác Động Chéo Đa Kênh (Cross-Channel Impact Check)**:
   - Trước khi áp dụng thay đổi, kiểm chứng xem logic mới có tương thích với tất cả các nhóm kênh (Wildlife/Predator, Ocean/Cosmic Mysteries, History, Psychology, Pet Rescue) hay không, đảm bảo sửa lỗi cho nhóm kênh này không gây false-positive hoặc làm chậm nhóm kênh khác.
4. **Bước 4 — Xác Minh Thực Tế & Tự Động Hóa Sau Tác Vụ**:
   - Kiểm tra lại bằng biên dịch bytecode (`python -m py_compile`), smoke test import, hoặc kiểm định trực tiếp đầu ra, sau đó thực hiện quy trình [`auto-optimize-merge-push`](../auto-optimize-merge-push/SKILL.md).
