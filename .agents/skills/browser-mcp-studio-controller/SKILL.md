---
name: browser-mcp-studio-controller
description: >-
  Quy chuẩn điều khiển trình duyệt thông qua BrowserMCP (BrowserMCP/mcp) và Chrome DevTools MCP để
  tương tác trực tiếp với phiên trình duyệt thật đã đăng nhập của người dùng (YouTube Studio Realtime,
  duyệt OAuth 1-Click, kiểm tra bản quyền/kiếm tiền 7 kênh) cũng như kiểm thử giao diện Web Dashboard
  nội bộ (http://localhost:8000).
---

# BrowserMCP & Chrome DevTools Studio Controller (`browser-mcp-studio-controller`)

Skill này kết hợp sức mạnh của **[BrowserMCP/mcp](https://github.com/BrowserMCP/mcp)** (`@browsermcp/mcp@latest`) và **Chrome DevTools MCP** (`chrome-devtools-mcp`) để giúp AI Agent quản lý trực quan cả **YouTube Studio thật (`studio.youtube.com`)** lẫn **AI YouTube Studio Dashboard nội bộ (`http://localhost:8000`)**.

---

## 1. Phân Tách Vai Trò Giữa 2 Hệ Thống MCP Trình Duyệt

| Tiêu chí | **BrowserMCP (`@browsermcp/mcp`)** | **Chrome DevTools MCP (`chrome-devtools`)** |
| :--- | :--- | :--- |
| **Cơ chế hoạt động** | Kết nối qua Chrome Extension vào **trình duyệt Chrome thật đang mở của anh** (giữ nguyên 100% Gmail, Cookie, phiên đăng nhập YouTube Studio). | Điều khiển trình duyệt qua giao thức CDP (`navigate_page`, `take_snapshot`, `evaluate_script`, `list_console_messages`, `list_network_requests`). |
| **Ứng dụng tốt nhất** | - Xem **YouTube Studio Realtime 48h/60m** của 7 kênh (khi Analytics API v2 đang trễ 24–48h).<br>- Chuyển đổi tài khoản Brand Channel trên `studio.youtube.com`.<br>- Bấm xác nhận **Cho phép (Allow)** khi cấp quyền OAuth Google.<br>- Kiểm tra trạng thái Bản quyền (Copyright) & Bật kiếm tiền. | - Kiểm thử tự động giao diện `http://localhost:8000`.<br>- Bắt lỗi Console JS, kiểm tra request mạng `/api/channels`, `/api/videos`.<br>- Chụp ảnh màn hình (`take_screenshot`), đo hiệu năng UI. |

---

## 2. Hướng Dẫn Cấu Hình & Kích Hoạt Nhanh BrowserMCP

1. **Khởi chạy MCP Server cục bộ**:
   - Đã hỗ trợ chạy trực tiếp qua Node.js (`npx`):
     ```json
     {
       "mcpServers": {
         "browsermcp": {
           "command": "npx",
           "args": ["-y", "@browsermcp/mcp@latest"]
         }
       }
     }
     ```
2. **Kết nối với Chrome Extension (Chỉ cần 1 lần)**:
   - Cài đặt tiện ích **Browser MCP** từ Chrome Web Store trên trình duyệt Chrome chính.
   - Bấm biểu tượng tiện ích **Browser MCP** -> nhấn **Connect** (hoặc xác nhận mã ghép nối 6 chữ số) để cho phép AI điều khiển tab trong nhóm tab được đánh dấu màu riêng biệt.
   - Có nút **Stop** ngay trên tiện ích để ngắt quyền điều khiển bất cứ lúc nào.

---

## 3. Quy Trình 4 Luồng Quản Trị Kênh YouTube Bằng Browser MCP

### Luồng 1: Quét Số Liệu Realtime YouTube Studio (Bù Độ Trễ 24–48h Của API v2)
Khi video mới đăng `< 24–48h` mà YouTube Analytics API v2 báo `⏳ Chờ Analytics chốt sổ (24–48h)`:
1. Điều hướng tới `https://studio.youtube.com` trên phiên trình duyệt đã đăng nhập.
2. Mở tab **Analytics (Số liệu phân tích)** -> xem cột **Realtime (Thời gian thực - 48 giờ qua)** và tỷ lệ **Viewed vs Swiped away (% Đã xem so với Vuốt bỏ)** của video mới nhất.
3. Đối chiếu với số liệu từ `YouTube Data API v3` để báo cáo chính xác 100% cho người dùng.

### Luồng 2: Tự Động Hóa & Kiểm Chứng OAuth 7 Kênh Trên `http://localhost:8000`
1. Dùng `navigate_page` mở `http://localhost:8000`.
2. Dùng `take_snapshot` kiểm tra thẻ kênh trong mục **Kênh & Tài Khoản** (`🟢 Đã cấp quyền YouTube` cho cả 7/7 kênh).
3. Kiểm tra `list_console_messages` và `list_network_requests` đảm bảo `/api/channels` trả về HTTP 200 dưới 100ms, không có lỗi JS.

### Luồng 3: Kiểm Tra Trạng Thái Video Hẹn Giờ (`SCHEDULED`) & Bản Quyền Nhạc Nền
1. Mở mục **Content (Nội dung) -> Shorts** trên YouTube Studio hoặc tab **Pipeline Video** trên `http://localhost:8000`.
2. Xác nhận cột **Restrictions (Hạn chế)** là `None (Không có)` để đảm bảo các bản nhạc nền BGM (`PAWS_BGM`, `WILD_BGM`, `MYST_BGM`, `PSYC_BGM`, `HIST_BGM`) hoàn toàn sạch bản quyền.

### Luồng 4: Nguyên Tắc An Toàn Khi Điều Khiển Trình Duyệt
- **Không bao giờ tự ý xóa video (`Delete forever`)** hoặc hủy xuất bản video đang cắn đề xuất trên YouTube Studio nếu chưa có xác nhận rõ ràng của người dùng.
- **Không lưu mật khẩu hay trích xuất Cookie thô** ra thông báo chat.
