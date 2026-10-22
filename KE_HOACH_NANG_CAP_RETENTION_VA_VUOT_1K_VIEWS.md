# 🚀 KẾ HOẠCH NÂNG CẤP HỆ THỐNG: ĐỘT PHÁ NGƯỠNG 1,000 VIEWS, GIẢM LƯỚT BỎ (`SWIPE AWAY < 45%`) & KHÔI PHỤC THỜI GIAN XEM (`AVD >= 25s – 35s`)

> **Tài liệu Đặc tả Kỹ thuật dành cho AI Agent Thực thi (Implementation Agent)**  
> **Dự án:** `d:\Python\Youtube` (AI YouTube Studio)  
> **Ngày lập kế hoạch:** 22/10/2026  
> **Mục tiêu cốt lõi:** Xử lý triệt để 4 nguyên nhân gốc rễ còn lại khiến một số video bị kẹt dưới ngưỡng `1,000 views`, tỷ lệ lướt bỏ (`Swipe Away %`) còn cao (`> 50%`), và thời gian xem trung bình (`AVD` / `APV %`) bị sụt giảm ở đợt video ngày `18/10 – 20/10` so với đợt đỉnh cao `04/10 – 17/10`.

---

## 1. BỐI CẢNH SỐ LIỆU THẬT & CÁC HẠNG MỤC ĐÃ HOÀN TẤT (KHÔNG LÀM TRÙNG)

### 1.1. Chẩn đoán từ dữ liệu thật (`YouTube Data API v3` + `YouTube Analytics API v2`)
Thuật toán YouTube Shorts chỉ đẩy video từ **Tầng 2 (`1,000 – 2,000 views`)** lên **Tầng 3 Viral (`10,000 – 100,000+ views`)** khi video thỏa mãn **ĐỒNG THỜI cả 2 điều kiện**:
1. **Tỷ lệ ở lại xem (`Stayed to Watch`) $\ge 55\% - 65\%$** *(tức `Swipe Away < 35% - 45%`)*.
2. **Thời gian xem trung bình (`AVD`) $\ge 25\text{s}$** *(hoặc `APV >= 75% - 90%`)*.

**Dữ liệu thực tế trên 7 kênh chỉ ra hiện tượng "Được chỉ số này thì mất chỉ số kia" trong đợt `18/10 – 20/10`:**
- **Wild Odyssey (`ch_wild_odyssey`):**
  - Đợt `16/10 – 17/10`: Video `4EBmiwL4rB4` (*Why Lions FLEE From Nile Hippos*, `38s`) đạt **`1,344 views`**, **`AVD = 27.0s` (`APV = 73.1%`)**, `Stayed = 39.6%`. Video `x42-Wj-Xcr4` (*20-Foot Shark Flees From Orcas*, `38s`) đạt **`1,007 views`**, **`AVD = 25.0s` (`APV = 67.1%`)**.
  - Đợt `18/10 – 19/10`: Video `_-ezy5J6qBs` đạt **`Stayed = 60.0%`** (`Swipe Away` giảm xuống chỉ còn `40.0%`), và `XvkgAsi5isY` đạt **`Stayed = 57.3%`**, **NHƯNG `AVD` lại tụt từ `27.0s` xuống chỉ còn `11.0s – 17.0s` (`APV = 31.1% – 45.6%`)**!
  - **Nguyên nhân từ `render_manifest` (`job_id = 519, 521, 525`):** Chỉ có `Scene 1–2` (`3s` đầu) là video thật (`video_scene_count = 1–2`), còn **từ `Scene 3` đến `Scene 8` (`85%` thời lượng thân bài) bị rơi về ảnh tĩnh (`image_scene_count = 6`)**! Người xem bị thu hút ở 3 giây đầu nhưng thoát hàng loạt ở giây thứ `11s – 17s` khi thấy toàn ảnh tĩnh trôi chậm.
- **Paws & Hearts Rescue (`ch_paws_hearts_rescue`):**
  - Đợt `04/10 – 05/10`: `xLgOe6VpOH0` đạt **`2,285 views`, `+32 Subs`**; `ldn3eUkXPTI` đạt **`1,205 views`, `AVD = 47.0s` (`APV = 94.8%`)**.
  - Đợt `14/10 – 18/10`: Khi hệ thống dùng ảnh tĩnh/ảnh AI cho các cảnh cứu hộ chó mèo, **`AVD` sụp đổ xuống `2.0s – 4.0s` (`APV = 4.7% – 11.4%`)**, khiến kênh bị kẹt dưới `10 views`.
- **Psychology of the world (`ch_psychology_of_the_world`):**
  - Đợt `04/10 – 13/10`: Video có thời lượng ngắn gọn `21s – 25s`, tốc độ đọc nhanh (`s0zONTvfzVA` đạt **`952 views`**, `Stayed = 39.1%`; `u14a81G4q2c` đạt **`APV = 75.5%`**).
  - Đợt `14/10 – 19/10`: Sau khi tốc độ đọc TTS bị ép về `+0% / +2%`, thời lượng video bị kéo giãn lên `40s – 48s` với giọng đọc chậm rãi, làm **`Swipe Away` trung bình toàn kênh vọt lên `80.6%`**.
- **The mysteries of the world (`ch_mysteries_of_the_world`):**
  - Các video có tiêu đề chứa **Thực thể cụ thể + Con số** như *Why Do **600 Dogs** Jump Off This **Scottish Bridge**?* đạt **`953 views` (`APV = 74.5%`)**, trong khi các tiêu đề ẩn dụ mơ hồ giấu tên chủ thể (*Invisible Monsters of the Deep*, *One Bite Is All It Takes*, *The Point in the Ocean Where Spacecraft Go to Die*) bị kẹt ở **`0 – 50 views`**.

---

### 1.2. Các hạng mục ĐÃ ĐƯỢC SỬA trong phiên trước (TUYỆT ĐỐI GIỮ NGUYÊN, KHÔNG GHI ĐÈ)
Agent thực thi cần lưu ý **3 commit vừa được đẩy lên `origin/main` hôm nay** để bảo toàn code:
1. **`commit b569883` (Hook Video `3–7s` & Chống Bản Quyền 4 Lớp):**
   - Đã nâng cấp [`app/graph/nodes/hook_video_node.py`](file:///d:/Python/Youtube/app/graph/nodes/hook_video_node.py), [`app/utils/ffmpeg_helper.py`](file:///d:/Python/Youtube/app/utils/ffmpeg_helper.py), [`app/services/media/stock_fetcher.py`](file:///d:/Python/Youtube/app/services/media/stock_fetcher.py) để ghép nối 2 cảnh đầu (`hook_motion_001.mp4` + `hook_motion_002.mp4`, tổng `3–7s` video thật), triệt tiêu độ trễ `+0.18s`/`+0.25s`, thêm 4 lớp lách bản quyền (`hflip` + `1.18x` safe crop + `1.04x` tempo + 2-Shot Beat Punch-In) và mở rộng 5 nguồn video (có `Wikimedia Commons HD Video` + `deno` cho `yt-dlp`).
2. **`commit 79a140a` (Xóa bỏ 100% Âm thanh phụ SFX điện tử):**
   - Đã xóa toàn bộ 9 file `.mp3` trong `assets/sfx/` và đặt `enable_sfx: bool = False` mặc định trên toàn hệ thống. **Tuyệt đối không bật lại SFX.**
3. **`commit b53f152` (Đồng bộ `channel_keywords` lên 7 kênh YouTube):**
   - Đã cập nhật `brandingSettings.channel.keywords` trên YouTube Data API v3 và trong [`config/channels.yaml`](file:///d:/Python/Youtube/config/channels.yaml).

---

## 2. QUY CHUẨN BẮT BUỘC DÀNH CHO AGENT THỰC THI (`MANDATORY GUARDRAILS`)

Trước và trong khi viết code, Agent thực thi **BẮT BUỘC** đọc và tuân thủ nghiêm ngặt các quy tắc trong [`AGENTS.md`](file:///d:/Python/Youtube/AGENTS.md):
1. **`systemic-root-cause-bugfix` (Cấm Overfitting & Cấm Hardcode Từ Khóa):**
   - **TUYỆT ĐỐI CẤM** vá lỗi bằng cách hardcode danh sách từ khóa loài vật/chủ đề hẹp (`if "tiger" in ...`, `if "snow" in ...`, `if "bridge" in ...`).
   - Mọi giải pháp phải tổng quát hóa qua cấu hình ngách (`niche`), phân tích ngữ nghĩa của LLM Prompt và các cổng toán học/thị giác tổng quát (vượt qua **4-Domain Counterfactual Test**: *Wildlife*, *Ancient/Alternate History*, *Dark Psychology*, *Sci-Fi/Ocean Mysteries*).
2. **`architecture-first-design`:** Giữ nguyên chữ ký hàm tương thích ngược (`backward-compatible signatures` với giá trị mặc định an toàn), không làm gãy các test hoặc luồng gọi hiện hành.
3. **`verification-before-completion` & `auto-optimize-merge-push`:**
   - Sau khi sửa xong cả 4 Pha, bắt buộc chạy `python -m py_compile` cho toàn bộ các file đã sửa + viết script smoke test kiểm chứng thực tế từng pha.
   - Tự động `git add`, `git commit` (Conventional Commit) và `git push origin main` sau khi kiểm증 thành công 100%.

---

## 3. CHI TIẾT KỸ THUẬT 4 PHA NÂNG CẤP CỐT LÕI

---

### 🔥 PHA 1: NÂNG TỶ LỆ VIDEO THẬT TRONG THÂN BÀI LÊN `>= 60%` (`4–5/8 CẢNH`) & SỬA LỖI HẠ CẤP VIDEO XUỐNG ẢNH TĨNH

#### A. Truy vết Nguyên nhân Gốc rễ trong Code hiện tại
Tại sao trong các job gần đây (`job_id = 519, 521, 525`), dù hàm `plan_media_tools_with_llm_sync` đã gán `clone_youtube_short_peak_clip` cho các cảnh thân bài, nhưng kết quả cuối cùng vẫn bị rơi về `image` (`6/8 cảnh là ảnh tĩnh`)?
1. **Lỗi 1 tại [`app/graph/tools/media_tools.py`](file:///d:/Python/Youtube/app/graph/tools/media_tools.py#L1011-L1020) (Hàm `execute_youtube_short_peak_clone_sync`):**
   - Khi tải YouTube Shorts cho các cảnh thân bài (`scene_id > 1`), biến `dl_opts` tại dòng `1011–1020` **thiếu cấu hình `extractor_args: {"youtube": {"player_client": ["android_vr", "web_safari", "ios"]}}`** (trong khi `hook_video_node.py` đã có). Điều này khiến `yt-dlp` dễ gặp lỗi format hoặc chậm khi tải các cảnh Peak giữa bài.
2. **Lỗi 2 tại [`app/graph/tools/media_tools.py`](file:///d:/Python/Youtube/app/graph/tools/media_tools.py#L1238-L1250) (Tầng 2 Stock Video Fallback trong `execute_youtube_short_peak_clone_sync`):**
   - Tại dòng `1239`: `for st_q in search_phrases[:1]:` chỉ thử đúng **1 câu truy vấn đầu tiên**!
   - Khi câu truy vấn đầu tiên dài 4–5 từ hoặc chứa động từ/tính từ phức tạp, Pexels/Pixabay trả về `0` kết quả. Hàm lập tức trả về `None` mà không thử các truy vấn ngắn hơn (2–3 từ danh từ gốc) và cũng không gọi kho video thứ 5 (`Wikimedia Commons HD Video`).
   - Khi `execute_youtube_short_peak_clone_sync` trả về `None`, [`app/graph/nodes/media_nodes.py`](file:///d:/Python/Youtube/app/graph/nodes/media_nodes.py#L526-L554) tại dòng `529` lập tức **hạ cấp phân cảnh đó xuống `execute_google_cinematic_image_sync` (Ảnh tĩnh)**!
3. **Lỗi 3 tại [`app/graph/tools/media_tools.py`](file:///d:/Python/Youtube/app/graph/tools/media_tools.py#L2335-L2345) (Hàm `plan_media_tools_with_llm_sync`):**
   - Kênh `animal_rescue` (*Paws & Hearts Rescue*) đang dùng chung hạn ngạch `peak_indices = {1, 2, 3, 5, 6}` với `wildlife`, và khi Cảnh 4, 7, 8 dùng ảnh tĩnh/AI thì người xem ngách thú cưng thoát ngay lập tức.

#### B. Hướng dẫn Sửa Code Cụ thể (Step-by-Step)

1. **Tại [`app/graph/tools/media_tools.py`](file:///d:/Python/Youtube/app/graph/tools/media_tools.py):**
   - **Bước 1.1 (Dòng `955–965` và `1011–1021` trong `execute_youtube_short_peak_clone_sync`):**
     Thêm `"extractor_args": {"youtube": {"player_client": ["android_vr", "web_safari", "ios"]}}` vào cả `info_opts` và `dl_opts` để đồng bộ tốc độ tải nhanh `1–2s` và chống lỗi 403/JS challenge như `hook_video_node.py`.
   - **Bước 1.2 (Dòng `1088–1100` và `1157–1169` trong `execute_youtube_short_peak_clone_sync`):**
     Đảm bảo khi gọi `trim_video` cho các cảnh YouTube Shorts ở thân bài (`clip_role == "PEAK"`), luôn bật `fair_use_transform=True` và `is_hook_scene=True` (hoặc kích hoạt 2-Shot Dynamic Beat Punch-In) để mọi clip YouTube Shorts chèn vào giữa bài cũng được bảo vệ bản quyền 4 lớp và đổi góc máy sống động.
   - **Bước 1.3 (Dòng `1215–1380` — Nâng cấp Tầng 2 Stock & Wikimedia Video Fallback trong `execute_youtube_short_peak_clone_sync`):**
     - Thay vì chỉ lặp `for st_q in search_phrases[:1]:`, hãy tạo danh sách ứng viên truy vấn `stock_query_candidates` gồm tối đa **3 truy vấn từ cụ thể đến cô đọng (2–3 từ danh từ chủ thể gốc)**:
       - Truy vấn 1: `search_phrases[0]`
       - Truy vấn 2: `search_phrases[1]` (nếu có) hoặc `_clean_yt_search_phrase(subject_entity or topic, max_words=2, strip_distractor_props=True)`
       - Truy vấn 3: Danh từ gốc 1–2 từ của `subject_entity` (đảm bảo Pexels/Pixabay/Wikimedia luôn tìm thấy tư liệu thật).
     - Nếu `media_agent.search_best_video_clip` trên truy vấn đầu chưa có kết quả, thử tiếp truy vấn rút gọn thứ 2 và thứ 3, đồng thời gọi thêm `media_agent._stock_fetcher.search_wikimedia_videos(st_q, per_page=6)` làm nguồn video tư liệu thật bổ sung trước khi trả về `None`.
   - **Bước 1.4 (Dòng `2335–2345` và `2420–2480` trong `plan_media_tools_with_llm_sync`):**
     - Phân tách rõ hạn ngạch Video chuyển động thật theo ngách:
       - Nếu là kênh cứu hộ thú cưng (`any(k in niche_norm_dir for k in ("rescue", "pet", "paws", "wholesome"))`):
         Đặt `peak_indices = {1, 2, 3, 4, 5, 6, 7}` và `max_video_peak_scenes = max(6, total_scenes - 1)` để **75% – 85% thời lượng video là cảnh quay thật liên tục**, khôi phục `AVD 47s` cho kênh *Paws & Hearts Rescue*.
       - Nếu là kênh Động vật hoang dã / Bí ẩn / Lịch sử giả định (`wildlife`, `predator`, `mystery`, `deep_sea`, `history`, `alternate`):
         Đặt `peak_indices = {1, 2, 4, 5, 6}` và `max_video_peak_scenes = max(5, (total_scenes * 2) // 3)` — bắt buộc có **Hook Video (`Cảnh 1 + 2`)**, **Mid-Video Re-Hook (`Cảnh 4`, giây thứ 10–15 — đúng điểm người xem hay rời bỏ nhất)**, và **Climax Video (`Cảnh 5 + 6`)**.
       - Nếu là kênh Tâm lý học (`psychology`, `mind`, `stoic`) dùng tool `fetch_clean_cinematic_portrait_or_broll` (dòng `447` của `media_nodes.py` và dòng `2426, 2515` của `media_tools.py`):
         Nâng danh sách cảnh ưu tiên `VIDEO_BROLL` từ `sid in (1, 2, 4, 6)` lên **`sid in (1, 2, 3, 5, 6)`** (tối thiểu 5 cảnh là Video B-Roll diễn viên thật chuyển động chuẩn 9:16).

---

### ⚡ PHA 2: TỐI ƯU TỐC ĐỘ GIỌNG ĐỌC (`TTS RATE`) THEO NGÁCH & TRIỆT TIÊU KHOẢNG NGHỈ CHẾT (`ZERO DEAD-AIR`)

#### A. Truy vết Nguyên nhân Gốc rễ trong Code hiện tại
1. **Tại [`app/agents/voice_agent.py`](file:///d:/Python/Youtube/app/agents/voice_agent.py#L337-L366) (Hàm `VoiceAgent.resolve_optimal_tts_rate`):**
   ```python
   if -5 <= val <= 3:
       return f"{val:+d}%"
   elif val > 3:
       return "+2%"
   ```
   Đoạn code trên đang **kẹp trần cứng mọi tốc độ giọng đọc ở mức `+2%`**, bất chấp file cấu hình `config/niches/*.yaml` đặt `+10%` hay `+15%`! Hệ quả là tất cả các kênh đều đọc với tốc độ rề rà như nhau, kéo thời lượng kịch bản 85 từ lên tới `42s – 58s`, làm tụt `APV %` và đẩy `Swipe Away` của kênh *Psychology* lên `80.6%`.
2. **Tại [`app/agents/voice_agent.py`](file:///d:/Python/Youtube/app/agents/voice_agent.py#L827-L855) (Hàm `VoiceAgent._master_and_trim_scene_audio`):**
   Bộ lọc âm thanh cuối mỗi phân cảnh đang để `apad=pad_dur=0.06` (60ms khoảng lặng cuối mỗi câu), khi cộng dồn qua 8 cảnh làm giảm độ dồn dập của mạch kể chuyện Shorts.

#### B. Hướng dẫn Sửa Code Cụ thể (Step-by-Step)

1. **Cập nhật hàm `VoiceAgent.resolve_optimal_tts_rate` tại [`app/agents/voice_agent.py`](file:///d:/Python/Youtube/app/agents/voice_agent.py#L337-L366):**
   - Cho phép nhận giá trị cấu hình từ `profile.get("audio", {}).get("tts_rate")` trong dải hợp lệ `-5 <= val <= 16`.
   - Thiết lập bảng tốc độ mặc định thông minh theo ngách (`nid = (niche or "").strip().lower()`) khi không truyền `explicit_rate`:
     - **Ngách Tâm lý học (`"psych"`, `"mind"`, `"stoic"`, `"dark"`):** Trả về **`"+11%"`** (~175–180 WPM — nhịp đọc sắc lạnh, cuốn hút, giữ tổng thời lượng video ở khung vàng `26s – 33s`).
     - **Ngách Động vật hoang dã, Bí ẩn & Lịch sử giả định (`"wildlife"`, `"predator"`, `"mystery"`, `"deep_sea"`, `"ocean"`, `"history"`, `"alternate"`):** Trả về **`"+9%"`** (~170–175 WPM — nhịp phim tài liệu kịch tính, giữ thời lượng vàng `31s – 37s`).
     - **Ngách Cứu hộ thú cưng (`"animal"`, `"rescue"`, `"pet"`, `"wholesome"`):** Trả về **`"+5%"`** (~164 WPM — giữ sự truyền cảm, ấm áp nhưng không bị chậm).
     - **Mặc định (`general`):** Trả về **`"+8%"`**.
2. **Giảm khoảng đệm cuối câu (`Zero Dead-Air`) tại [`app/agents/voice_agent.py`](file:///d:/Python/Youtube/app/agents/voice_agent.py#L840-L855):**
   - Trong `VoiceAgent._master_and_trim_scene_audio`:
     Đổi `"apad=pad_dur=0.06"` thành **`"apad=pad_dur=0.02"`** (chỉ giữ 20ms đệm mượt chống click âm thanh, giúp các câu thoại nối tiếp nhau dồn dập không có khoảng trống chết).
   - Tại dòng `444` và nơi ghi file `.meta.json` trong `_process_single_scene`: Đổi cờ cache từ `"mastered_v5"` sang **`"mastered_v6"`** để hệ thống tự động tổng hợp lại audio với tốc độ mới thay vì lấy lại file MP3 cũ trong cache.

---

### 🎯 PHA 3: KHÓA CỔNG KIỂM DUYỆT TIÊU ĐỀ & HOOK "THỰC THỂ CỤ THỂ + CON SỐ / XUNG ĐỘT TRỰC DIỆN"

#### A. Truy vết Nguyên nhân Gốc rễ trong Code hiện tại
1. **Tại [`app/agents/content_agent.py`](file:///d:/Python/Youtube/app/agents/content_agent.py#L760-L799) (Hàm `_score_title_ctr` trong `_parse_json_script`):**
   Hàm chấm điểm chọn tiêu đề (`title_candidates`) hiện chỉ cộng điểm cho độ dài và các từ kích thích tò mò chung chung (`"why"`, `"secret"`, `"truth"`, `"never"`), nhưng **chưa có bộ lọc phạt các tiêu đề ẩn dụ mơ hồ giấu tên chủ thể** (như *"Invisible Monsters of the Deep"*, *"One Bite Is All It Takes"*, *"Why You Should Never Touch This Cute Creature"*, *"The Point in the Ocean Where Spacecraft Go to Die"*). Dữ liệu thật chứng minh 100% các tiêu đề giấu chủ thể này đều bị kẹt ở `0 – 50 views`!
2. **Tại [`app/graph/evaluators/script_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/script_critic.py#L99-L185) (Hàm `_calibrate_hook_score_with_real_data`):**
   Hàm mới chỉ gộp `combined = f"{eff_title} {s1_narr}"` để kiểm tra chung, dẫn tới trường hợp `s1_narr` có con số nhưng bản thân `title` lại mơ hồ vẫn được chấm `>= 8.6 điểm` và vượt qua cổng kiểm duyệt!

#### B. Hướng dẫn Sửa Code Cụ thể (Step-by-Step)

1. **Nâng cấp thuật toán chấm điểm `_score_title_ctr` tại [`app/agents/content_agent.py`](file:///d:/Python/Youtube/app/agents/content_agent.py#L760-L799):**
   - **Thưởng điểm cao (`+18.0` đến `+25.0 điểm`)** cho ứng viên tiêu đề có:
     1. **Con số định lượng cụ thể** (`\b\d+[\d,\.]*\s*(?:psi|ft|feet|km|miles|mph|lbs|pounds|tons|kg|meters|seconds|days|years|%)\b` hoặc bất kỳ con số `\d+` đi kèm danh từ).
     2. **Cấu trúc đối đầu trực diện hoặc hành động cực hạn** (`vs`, `versus`, `contra`, `flee`, `flees`, `temem`, `odeiam`, `crushes`, `esmagam`, `aniquila`, `terrified`, `tremem`, `smile`, `jump`).
     3. **Chứa tên thực thể chính (`primary_entity`)** hoặc danh từ riêng xác định ngay trong 6 từ đầu tiên.
   - **Phạt nặng (`-30.0 điểm`)** cho ứng viên tiêu đề mắc lỗi **"Vague Clickbait / Giấu Chủ Thể"**:
     - Dùng các cụm đại từ chỉ định mơ hồ thay cho tên thực thể: `r"\b(?:this\s+(?:cute\s+|deadly\s+|terrifying\s+)?(?:creature|animal|beast|place|point|bird|thing|monster)|invisible\s+monsters|one\s+bite\s+is\s+all)\b"` mà **không có** tên loài/địa danh cụ thể hoặc con số vật lý đi kèm trong tiêu đề.
2. **Bổ sung Cổng kiểm duyệt Tiêu đề độc lập trong [`app/graph/evaluators/script_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/script_critic.py#L119-L185):**
   - Trong `ScriptCritic._calibrate_hook_score_with_real_data`:
     Kiểm tra riêng `eff_title`: Nếu `eff_title` chứa các cụm đại từ mơ hồ giấu tên chủ thể (`this creature`, `this animal`, `this place`, `este animal`, `esta criatura`...) mà không nêu tên thực thể chính VÀ không có con số định lượng trong `eff_title`, lập tức giới hạn `score = min(score, 6.8)` và đẩy thông báo vào `extra_critiques`:
     `"CHẶN TIÊU ĐỀ MƠ HỒ: Tiêu đề đang giấu tên chủ thể ('{eff_title}'). Bắt buộc nêu đích danh Tên loài/Thực thể/Địa danh cụ thể + Con số định lượng hoặc cặp đối đầu 'A vs B' ngay trên tiêu đề!"`

---

### 🎬 PHA 4: TĂNG NHỊP CẮT CẢNH THỊ GIÁC (`VISUAL BEAT PACING 1.5s – 2.0s`) CHO CÁC CẢNH THÂN BÀI DÙNG ẢNH

#### A. Truy vết Nguyên nhân Gốc rễ trong Code hiện tại
1. **Tại [`app/agents/video_agent.py`](file:///d:/Python/Youtube/app/agents/video_agent.py#L263-L315) (Hàm `VideoAgent._prepare_scene_clips`) và [`app/utils/ffmpeg_helper.py`](file:///d:/Python/Youtube/app/utils/ffmpeg_helper.py#L543-L706) (Hàm `image_to_video`):**
   - Khi một phân cảnh thân bài dùng ảnh (`media_type == "image"`):
     - Nếu có `pose_images >= 2`: Hệ thống gọi `render_opening_hook_multi_cut` với `min_sec_per_img = 1.35s`.
     - **Tuy nhiên**, nếu phân cảnh đó chỉ có **1 bức ảnh duy nhất** (`len(pose_images) <= 1` hoặc sau khi lọc chỉ còn 1 ảnh hợp lệ) mà câu thoại dài `3.5s – 5.5s`, code rơi xuống nhánh `image_to_video` và chỉ chạy **1 hiệu ứng zoom chậm đơn điệu suốt 4–5 giây**.
     - Trên YouTube Shorts, bất kỳ khung hình ảnh tĩnh nào giữ nguyên góc máy quá `2.2 giây` ở giữa video đều làm mắt người xem bị chững lại và kích hoạt hành vi vuốt bỏ ở giây `11–17s`.

#### B. Hướng dẫn Sửa Code Cụ thể (Step-by-Step)

1. **Nâng cấp xử lý ảnh đơn dài (`duration >= 3.0s`) tại [`app/utils/ffmpeg_helper.py`](file:///d:/Python/Youtube/app/utils/ffmpeg_helper.py#L543-L706) hoặc [`app/agents/video_agent.py`](file:///d:/Python/Youtube/app/agents/video_agent.py#L263-L315):**
   - Trong `VideoAgent._prepare_scene_clips` (khi `raw_type == "image"` và chỉ có 1 ảnh nhưng `duration >= 3.0s`) **HOẶC** trực tiếp bên trong `render_opening_hook_multi_cut` (`app/utils/ffmpeg_helper.py` dòng `747`):
     - Khi chỉ có `1 ảnh hợp lệ` (`len(valid_paths) == 1`) nhưng `duration_seconds >= 3.0`:
       Thay vì trả về 1 cú zoom chậm đơn điệu suốt `3.5s – 5.5s`, hãy tự động tạo **2-Shot Dynamic Camera Cut (2 góc máy điện ảnh từ bức ảnh gốc độ phân giải cao)**:
       - **Shot 1 (`0.0s` $\rightarrow$ `50% duration`):** Khung hình 9:16 toàn cảnh với chuyển động `camera_motion` mượt mà (`zoom_in` nhẹ hoặc `pan_right`).
       - **Shot 2 (`50% duration` $\rightarrow$ `100% duration`):** Cú cắt **Focal Punch-In `1.26x`** tập trung vào vùng chủ thể chính (`center_y_ratio = 0.42`, tăng nhẹ độ tương phản `contrast = 1.05`) kèm chuyển động ngược chiều nhẹ (`pan_left` hoặc `zoom_out`).
   - Nhờ đó, **100% các phân cảnh trong toàn bộ video** (dù là Video thật, Đa ảnh hay Ảnh đơn) đều đảm bảo có **nhịp biến đổi thị giác mới mỗi `1.5s – 2.2s`**, xóa bỏ hoàn toàn hiện tượng "slideshow đứng hình" ở giữa video!

---

## 4. DANH SÁCH FILE CẦN CHỈNH SỬA & QUY TRÌNH KIỂM THỬ (VERIFICATION CHECKLIST)

### 4.1. Bảng tổng hợp các file tác động
| STT | File cần sửa | Nội dung thay đổi chính |
| :-: | :--- | :--- |
| 1 | [`app/graph/tools/media_tools.py`](file:///d:/Python/Youtube/app/graph/tools/media_tools.py) | • Thêm `extractor_args` (`android_vr`, `web_safari`, `ios`) cho `yt-dlp` trong `execute_youtube_short_peak_clone_sync`.<br>• Mở rộng Tầng 2 Stock Video Fallback thử tối đa 3 truy vấn rút gọn + tích hợp `Wikimedia Commons HD Video`.<br>• Tăng hạn ngạch `peak_indices` & `max_video_peak_scenes` trong `plan_media_tools_with_llm_sync` (đặc biệt `75–85%` video thật cho `animal_rescue` và bắt buộc `Mid-Video Re-Hook` ở Cảnh 4 + `Climax Video` ở Cảnh 5–6 cho mọi ngách). |
| 2 | [`app/agents/voice_agent.py`](file:///d:/Python/Youtube/app/agents/voice_agent.py) | • Mở khóa hàm `resolve_optimal_tts_rate` theo từng ngách (`+11%` cho Psychology, `+9%` cho Wildlife/Mysteries/History, `+5%` cho Pet Rescue).<br>• Giảm khoảng lặng cuối câu trong `_master_and_trim_scene_audio` từ `pad_dur=0.06` xuống `pad_dur=0.02` (`Zero Dead-Air`).<br>• Nâng cờ cache audio lên `mastered_v6`. |
| 3 | [`app/agents/content_agent.py`](file:///d:/Python/Youtube/app/agents/content_agent.py) | • Nâng cấp `_score_title_ctr`: Thưởng điểm lớn cho tiêu đề có `[Thực thể cụ thể] + [A vs B / Con số định lượng]` và phạt `-30 điểm` cho các tiêu đề ẩn dụ mơ hồ giấu tên chủ thể (`"this creature"`, `"invisible monsters"`...). |
| 4 | [`app/graph/evaluators/script_critic.py`](file:///d:/Python/Youtube/app/graph/evaluators/script_critic.py) | • Bổ sung kiểm định riêng cho `eff_title` trong `_calibrate_hook_score_with_real_data`: Chặn mọi kịch bản có tiêu đề mơ hồ thiếu tên thực thể cụ thể và con số/xung đột. |
| 5 | [`app/utils/ffmpeg_helper.py`](file:///d:/Python/Youtube/app/utils/ffmpeg_helper.py) & [`app/agents/video_agent.py`](file:///d:/Python/Youtube/app/agents/video_agent.py) | • Tự động kích hoạt **2-Shot Dynamic Camera Cut (Mid-Scene Focal Punch-In)** khi cảnh thân bài chỉ có 1 ảnh nhưng kéo dài `>= 3.0s`, đảm bảo nhịp cắt hình mỗi `1.5s – 2.2s`. |

---

### 4.2. Lệnh Kiểm Chứng Bắt Buộc trước khi Kết thúc (`Verification Commands`)

Sau khi hoàn thành chỉnh sửa cả 5 file trên, Agent thực thi **BẮT BUỘC** chạy các lệnh kiểm chứng sau trong terminal PowerShell tại `d:\Python\Youtube`:

```powershell
# 1. Kiểm tra cú pháp biên dịch Python trên toàn bộ các file đã sửa
python -m py_compile app/graph/tools/media_tools.py app/agents/voice_agent.py app/agents/content_agent.py app/graph/evaluators/script_critic.py app/utils/ffmpeg_helper.py app/agents/video_agent.py

# 2. Kiểm tra Smoke Test thực tế cho cả 4 Pha:
python -c "
from app.agents.voice_agent import VoiceAgent
from app.graph.evaluators.script_critic import ScriptCritic
from app.graph.tools.media_tools import plan_media_tools_with_llm_sync

# Test Pha 2: TTS Rate theo ngách
assert VoiceAgent.resolve_optimal_tts_rate('en-US-ChristopherNeural', niche='psychology') in ('+10%', '+11%', '+12%'), 'Lỗi TTS rate psychology'
assert VoiceAgent.resolve_optimal_tts_rate('en-US-EricNeural', niche='wildlife_predators') in ('+8%', '+9%', '+10%'), 'Lỗi TTS rate wildlife'
assert VoiceAgent.resolve_optimal_tts_rate('en-US-AvaMultilingualNeural', niche='animal_rescue') in ('+5%', '+6%'), 'Lỗi TTS rate rescue'

# Test Pha 3: Cổng chặn tiêu đề mơ hồ
sc_bad, critiques_bad = ScriptCritic._calibrate_hook_score_with_real_data(
    9.0,
    scene_1_narration='One bite can end it all in seconds.',
    title='Why You Should Never Touch This Cute Creature',
)
assert sc_bad <= 7.0 and critiques_bad, f'Chưa chặn được tiêu đề mơ hồ: {sc_bad}'

sc_good, _ = ScriptCritic._calibrate_hook_score_with_real_data(
    8.5,
    scene_1_narration='A Nile hippo snaps a 3,700 PSI jaw shut on a lion.',
    title='Why Lions FLEE From 3,700 PSI Nile Hippos',
)
assert sc_good >= 8.6, f'Tiêu đề chuẩn bị chấm thấp: {sc_good}'
print('ALL SMOKE TESTS PASSED SUCCESSFULLY!')
"

# 3. Đồng bộ Git & Đẩy lên GitHub theo quy chuẩn auto-optimize-merge-push
git status -s
git add app/graph/tools/media_tools.py app/agents/voice_agent.py app/agents/content_agent.py app/graph/evaluators/script_critic.py app/utils/ffmpeg_helper.py app/agents/video_agent.py
git commit -m "feat(retention): boost body video ratio, optimize niche TTS pacing, enforce concrete entity titles, and add 2-shot camera cuts"
git push origin main
```
