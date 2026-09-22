import sys
import logging
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler
from config.settings import settings
from app.database import init_db
from app.bot.handlers import start_command, channels_command, status_command, idea_command, channel_callback
from app.utils.ffmpeg_helper import is_ffmpeg_installed, get_ffmpeg_version

# Cấu hình logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("AI-YouTube")

def main():
    print("=" * 65)
    print("🚀 KHỞI ĐỘNG HỆ THỐNG AI YOUTUBE MULTI-CHANNEL & TEAM COLLABORATION")
    print("=" * 65)

    # 1. Khởi tạo Database
    print("[1/4] Đang khởi tạo SQLite Database...")
    init_db()
    print("   -> Database đã sẵn sàng!")

    # 2. Kiểm tra FFmpeg
    print("[2/4] Kiểm tra công cụ FFmpeg...")
    if is_ffmpeg_installed():
        print(f"   -> FFmpeg OK: {get_ffmpeg_version()}")
    else:
        print("   -> [CẢNH BÁO] Chưa tìm thấy FFmpeg trong PATH. Cần cài đặt để render video.")

    # 3. Kiểm tra cụm API Keys
    print("[3/4] Kiểm tra tài khoản Google Ultra & API Pool...")
    keys = settings.get_gemini_keys_pool()
    print(f"   -> Đã nạp {len(keys)} Gemini API Key(s) vào bộ xoay vòng (KeyRotator).")

    # 4. Kiểm tra cấu hình Telegram Bot
    print("[4/4] Khởi động Telegram Bot Workspace...")
    if not settings.TELEGRAM_BOT_TOKEN:
        print("   -> [LỖI] TELEGRAM_BOT_TOKEN chưa được thiết lập trong file .env!")
        print("   -> Hãy copy file .env.example thành .env và điền Token của bạn.")
        print("=" * 65)
        return

    app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).build()

    # Đăng ký các lệnh
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("channels", channels_command))
    app.add_handler(CommandHandler("idea", idea_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CallbackQueryHandler(channel_callback, pattern=r"^select_channel:"))

    print("🤖 Bot đang chạy chế độ Polling (hỗ trợ cả Chat riêng & Nhóm Telegram)...")
    print("=" * 65)
    app.run_polling()

if __name__ == "__main__":
    main()
