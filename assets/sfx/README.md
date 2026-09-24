# SFX Assets Directory

Thư mục này chứa các file hiệu ứng âm thanh (SFX) được tự động chèn vào video khi chuyển cảnh.

## Cách sử dụng

Đặt file MP3 hoặc WAV vào thư mục này. Hệ thống sẽ **tự động nhận diện** và sử dụng:

| Tên file | Thời điểm phát | Vai trò |
|----------|---------------|---------|
| `hit.mp3` hoặc `hit.wav` | **Giây đầu tiên (t=0)** | Impact mạnh ngay mở đầu video |
| `whoosh.mp3` hoặc `whoosh.wav` | **Mỗi mốc chuyển cảnh** | Tiếng vút mượt mà giữa các scenes |

## Tải SFX miễn phí (gợi ý)

### Option 1: Pixabay Sound Effects (miễn phí, không cần credit)
- Whoosh: https://pixabay.com/sound-effects/search/whoosh/
- Hit/Impact: https://pixabay.com/sound-effects/search/hit/
- Swoosh: https://pixabay.com/sound-effects/search/swoosh/

### Option 2: Mixkit (miễn phí hoàn toàn)
- https://mixkit.co/free-sound-effects/whoosh/
- https://mixkit.co/free-sound-effects/hit/

### Option 3: Freesound.org
- https://freesound.org/search/?q=whoosh+cinematic

## Ghi chú kỹ thuật

- Hỗ trợ: `.mp3`, `.wav`, `.ogg`
- Volume `hit.mp3`: 50% so với audio chính
- Volume `whoosh.mp3`: 35-40% so với audio chính
- Nếu thư mục rỗng hoặc thiếu file: hệ thống **tự bỏ qua**, video vẫn render bình thường không lỗi
