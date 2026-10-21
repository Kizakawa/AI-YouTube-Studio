# Sổ Tay Kỹ Thuật: Root-Cause Tracing, Defense-in-Depth & Anti-Overfitting

Tài liệu tham chiếu này tổng hợp các nguyên lý cốt lõi từ bộ skill mã nguồn mở **`obra/superpowers`** trên GitHub (`systematic-debugging`, `root-cause-tracing.md`, `defense-in-depth.md`, `verification-before-completion`) và bộ quy tắc **Anti-Reward-Hacking** cho các hệ thống AI Agent hiện đại.

---

## 1. Phân Biệt "Overfitting / Vá Từ Khóa" vs "Sửa Tận Gốc Hệ Thống"

| Tình huống lỗi thực tế | ❌ Cách sửa Overfitting (BỊ CẤM TUYỆT ĐỐI) | ✅ Cách sửa Tận Gốc Hệ Thống (BẮT BUỘC) |
| :--- | :--- | :--- |
| **Video về Hổ Siberia mùa đông nhưng tải nhầm cảnh rừng cỏ xanh mùa hè** | Viết `if "snow" in topic:` rồi ép nối chuỗi `+ " snow"` hoặc đếm tỷ lệ pixel màu xanh lá (`green_ratio > 0.22`) trong Python để loại ảnh. | 1. Nâng cấp **System Prompt** (`analyze_scenes_batch`, `HookVideoNode`) yêu cầu LLM tự động đính kèm từ khóa môi trường/mùa cốt lõi của kịch bản vào câu truy vấn.<br>2. Nâng cấp **Gemini Vision Prompt** (`curate_candidate_videos`, `curate_candidate_photos`) bắt buộc từ chối tư liệu có môi trường/mùa trái ngược với kịch bản. |
| **Chủ đề đối đầu "Loài A vs Loài B" tải nhầm ảnh ghép chia đôi màn hình (2-panel VS collage)** | Chặn cứng từ khóa tên loài hoặc chặn domain trang web cụ thể. | 1. Truy vết ngược phát hiện `_enforce_subject_queries` tự ý ghép đè `core_visual_entity` (Loài A) vào câu truy vấn của Loài B $\rightarrow$ Sửa `_enforce_subject_queries` tôn trọng câu truy vấn đơn chủ thể của AI.<br>2. Cấm dùng `"vs"` trong System Prompt tạo truy vấn.<br>3. Giữ bộ lọc CV phát hiện đường kẻ chia khung mỏng (`1–5px` divider line) tổng quát. |
| **Chủ đề Thủy quái Leviathan / Kraken không có video quay thật ngoài đời** | Tạo danh sách tuple cứng `("leviathan", "bloop", "kraken", "megalodon"...)` trong Python để nối chuỗi `"3d animation"`. | Hướng dẫn trực tiếp trong **System Prompt** của `HookVideoNode` và `VisualCurationAgent`: LLM tự nhận diện chủ thể là sinh vật đời thực hay sinh vật thần thoại/tiền sử/CGI để chọn hậu tố truy vấn phù hợp. |
| **Cảnh hành động nhanh bị bộ lọc chất lượng từ chối nhầm là ảnh mờ** | Tắt luôn bộ lọc độ nét hoặc hạ ngưỡng `min_sharpness` xuống cực thấp cho mọi video. | Phân tích toán học trong `VisualCritic`: Phân biệt **Out-of-Focus Blur** (`avg_sharp` thấp toàn bộ) với **Fast Action Motion Blur** (`avg_sharp` cao nhưng 1 frame lia máy có `min_f_sharp` thấp hơn một chút). |

---

## 2. Quy Trình `Root-Cause-Tracing` 4 Câu Hỏi Trước Khi Sửa Code

Trước khi dùng công cụ `edit_file`, Agent bắt buộc phải trả lời 4 câu hỏi truy vết ngược:

1. **Triệu chứng xuất hiện ở đâu?** (Ví dụ: Frame giây thứ 4 của file `.mp4` cuối cùng bị dính ảnh chuồng sở thú).
2. **Hàm nào đã trực tiếp chọn tư liệu lỗi đó?** (Ví dụ: `curate_candidate_videos` chấm điểm `8.5/10` cho video Pexels đó).
3. **Vì sao hàm đó lại đưa ra quyết định sai?** (Đọc log `rationale` của LLM hoặc kiểm tra dữ liệu đầu vào: Hóa ra trong System Prompt của `curate_candidate_videos` có câu *"ĐỀU LÀ TƯ LIỆU B-ROLL HỢP LỆ VÀ PHẢI ĐƯỢC CHẤM ĐẠT"* quá mạnh khiến LLM bỏ qua điều kiện cấm sở thú/sai môi trường!).
4. **Giải pháp ở tầng gốc có tổng quát cho mọi chủ đề không?** (Sửa lại câu chữ trong System Prompt của `curate_candidate_videos` để điều kiện *Đúng tự nhiên hoang dã & Đúng hệ sinh thái/mùa* trở thành điều kiện bắt buộc tiên quyết).

