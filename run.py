import sys
import threading
import logging
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler
from config.settings import settings
from app.database import init_db
from app.bot.handlers import start_command, channels_command, status_command, idea_command, channel_callback, video_callback
from app.utils.ffmpeg_helper import is_ffmpeg_installed, get_ffmpeg_version

# Cấu hình logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("AI-YouTube")

def run_fastapi_server():
    """Khởi động Web Dashboard bằng Uvicorn trong background thread"""
    try:
        import uvicorn
        print("🌐 [Web Dashboard] Đang chạy tại địa chỉ: http://127.0.0.1:8000")
        uvicorn.run("app.web.server:app", host="0.0.0.0", port=8000, log_level="warning")
    except ImportError:
        print("⚠️ [Web Dashboard] Chưa cài đặt uvicorn. Hãy chạy 'pip install uvicorn fastapi' để bật Dashboard.")
    except Exception as e:
        print(f"❌ [Web Dashboard] Lỗi khi chạy web server: {e}")

def main():
    print("=" * 70)
    print("🚀 KHỞI ĐỘNG HỆ THỐNG AI YOUTUBE STUDIO: TELEGRAM BOT & WEB DASHBOARD")
    print("=" * 70)

    # 1. Khởi tạo Database
    print("[1/4] Đang khởi tạo SQLite Database...")
    init_db()
    print("   -> Database đã sẵn sàng!")

    # 2. Khởi động Web Dashboard song song
    print("[2/4] Khởi động Web Dashboard Server...")
    web_thread = threading.Thread(target=run_fastapi_server, daemon=True)
    web_thread.start()

    # 3. Kiểm tra FFmpeg & API Keys
    print("[3/4] Kiểm tra công cụ & Tài nguyên...")
    if is_ffmpeg_installed():
        print(f"   -> FFmpeg OK: {get_ffmpeg_version()}")
    else:
        print("   -> [CẢNH BÁO] Chưa tìm thấy FFmpeg trong PATH.")

    keys = settings.get_gemini_keys_pool()
    print(f"   -> Cụm Google Ultra: Đã nạp {len(keys)} Gemini API Key(s) vào pool.")

    # 4. Kiểm tra cấu hình Telegram Bot
    print("[4/4] Khởi động Telegram Bot Workspace...")
    if not settings.TELEGRAM_BOT_TOKEN:
        print("   -> [LƯU Ý] TELEGRAM_BOT_TOKEN chưa có trong file .env!")
        print("   -> Web Dashboard vẫn hoạt động bình thường tại http://127.0.0.1:8000")
        print("   -> Điền Token vào file .env để kích hoạt thêm Telegram Bot.")
        print("=" * 70)
        # Giữ process sống để Web Dashboard tiếp tục chạy
        try:
            web_thread.join()
        except KeyboardInterrupt:
            print("\n👋 Đã dừng Web Dashboard.")
        return

    app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # Đăng ký các lệnh Telegram
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("channels", channels_command))
    app.add_handler(CommandHandler("idea", idea_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CallbackQueryHandler(channel_callback, pattern=r"^select_channel:"))
    app.add_handler(CallbackQueryHandler(video_callback, pattern=r"^(publish_now|schedule|regenerate|cancel):"))

    print("🤖 Bot đang chạy Polling... Cả nhóm có thể ra lệnh từ Telegram hoặc xem Web!")
    print("👉 Mở trình duyệt xem Dashboard: http://127.0.0.1:8000")
    print("=" * 70)
    app.run_polling()

if __name__ == "__main__":
    main()
