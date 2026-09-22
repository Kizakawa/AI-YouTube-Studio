import sys
import logging
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler
from config.settings import settings
from app.database import init_db
from app.bot.handlers import start_command, channels_command, status_command, channel_callback
from app.utils.ffmpeg_helper import is_ffmpeg_installed, get_ffmpeg_version

# Cấu hình logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("AI-YouTube")

def main():
    print("=" * 60)
    print("🚀 KHỞI ĐỘNG HỆ THỐNG AI YOUTUBE MULTI-CHANNEL STUDIO")
    print("=" * 60)

    # 1. Khởi tạo Database
    print("[1/3] Đang khởi tạo SQLite Database...")
    init_db()
    print("   -> Database đã sẵn sàng!")

    # 2. Kiểm tra FFmpeg
    print("[2/3] Kiểm tra công cụ FFmpeg...")
    if is_ffmpeg_installed():
        print(f"   -> FFmpeg OK: {get_ffmpeg_version()}")
    else:
        print("   -> [CẢNH BÁO] Chưa tìm thấy FFmpeg trong PATH. Bạn cần cài đặt FFmpeg để render video.")

    # 3. Kiểm tra cấu hình Telegram Bot
    print("[3/3] Kiểm tra cấu hình Telegram Bot...")
    if not settings.TELEGRAM_BOT_TOKEN:
        print("   -> [LỖI] TELEGRAM_BOT_TOKEN chưa được thiết lập trong file .env!")
        print("   -> Hãy copy file .env.example thành .env và điền Token của bạn.")
        print("=" * 60)
        return

    print("   -> Bắt đầu kết nối Telegram Bot...")
    app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # Đăng ký các lệnh
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("channels", channels_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CallbackQueryHandler(channel_callback, pattern=r"^select_channel:"))

    print("🤖 Bot đang chạy chế độ Polling... Nhấn Ctrl+C để dừng.")
    print("=" * 60)
    app.run_polling()

if __name__ == "__main__":
    main()
