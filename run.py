import os
import sys
import threading
import logging
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from config.settings import settings
from app.database import init_db
from app.bot.handlers import (
    start_command, channels_command, status_command, idea_command,
    chat_command, render_command, edit_command, media_command,
    media_upload_handler, text_message_handler, channel_callback,
    video_callback, chat_callback, queue_command, cancel_queue_command,
    pending_command, addchannel_command, editchannel_command, delchannel_command
)
from app.utils.ffmpeg_helper import is_ffmpeg_installed, get_ffmpeg_version

def recover_stuck_jobs():
    """Tự động khôi phục các video bị gián đoạn do tắt bot đột ngột khi đang render và cảnh báo các job lỗi tồn đọng"""
    try:
        from app.database.models import get_db, VideoJob, log_activity
        with get_db() as db:
            # 1. Khôi phục các job bị kẹt ở RENDERING
            stuck_jobs = db.query(VideoJob).filter(VideoJob.status == "RENDERING").all()
            for j in stuck_jobs:
                j.status = "SCRIPT_READY"
                j.progress_percent = 0
                j.progress_message = "Đã khôi phục sau khi hệ thống khởi động lại"
                log_activity(db, 0, "System", "RECOVER_JOB", f"Khôi phục Video #{j.id} về SCRIPT_READY")
            if stuck_jobs:
                print(f"   -> [Khôi phục] Đã tự động khôi phục {len(stuck_jobs)} video bị gián đoạn về trạng thái sẵn sàng.")

            # 2. Cảnh báo các job FAILED tồn đọng (để người dùng dễ dàng khôi phục trên Web hoặc Telegram)
            failed_jobs = db.query(VideoJob).filter(VideoJob.status == "FAILED").all()
            if failed_jobs:
                print(f"   -> [Tồn đọng] Phát hiện {len(failed_jobs)} video bị lỗi (FAILED) trong hàng đợi:")
                for fj in failed_jobs:
                    err_preview = fj.error_message.replace("\n", " ")[:50] if fj.error_message else "N/A"
                    print(f"      * Job #{fj.id}: {fj.title[:30] if fj.title else fj.topic} -> Lỗi: {err_preview}...")
                print("   -> [Khuyên dùng] Trên Web Dashboard: bấm nút '🔄 Khôi phục Job Lỗi', hoặc trên Telegram: gõ /pending để render lại!")
    except Exception as e:
        print(f"   -> ⚠️ Lỗi khôi phục jobs: {e}")

# Đảm bảo in tiếng Việt và emoji không bị lỗi trên Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

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
        port = int(os.getenv("PORT", 8000))
        print(f"🌐 [Web Dashboard] Đang chạy tại địa chỉ: http://0.0.0.0:{port}")
        uvicorn.run("app.web.server:app", host="0.0.0.0", port=port, log_level="warning")
    except ImportError:
        print("⚠️ [Web Dashboard] Chưa cài đặt uvicorn. Hãy chạy 'pip install uvicorn fastapi' để bật Dashboard.")
    except Exception as e:
        print(f"❌ [Web Dashboard] Lỗi khi chạy web server: {e}")

def main():
    print("=" * 70)
    print("🚀 KHỞI ĐỘNG HỆ THỐNG AI YOUTUBE STUDIO: TELEGRAM BOT & WEB DASHBOARD")
    print("=" * 70)

    # 1. Khởi tạo Database & Khôi phục trạng thái
    print("[1/4] Đang khởi tạo SQLite Database & Khôi phục tác vụ...")
    init_db()
    recover_stuck_jobs()
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

    # 4. Kiểm tra chế độ chạy
    if "--web-only" in sys.argv or "--web" in sys.argv or os.getenv("WEB_ONLY"):
        print("[4/4] 🌐 [CHẾ ĐỘ WEB-ONLY] Chỉ khởi động Web Dashboard, KHÔNG chạy Telegram Bot!")
        print("👉 Thoải mái chỉnh sửa UI và xem thay đổi tại: http://127.0.0.1:8000")
        print("🔒 Không bị xung đột hay chập bot với máy bạn Giang / Render.")
        print("=" * 70)
        try:
            web_thread.join()
        except KeyboardInterrupt:
            print("\n👋 Đã dừng Web Dashboard.")
        return

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

    app = (
        ApplicationBuilder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .read_timeout(120)
        .write_timeout(120)
        .connect_timeout(60)
        .build()
    )

    # Đăng ký các lệnh Telegram
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("channels", channels_command))
    app.add_handler(CommandHandler("addchannel", addchannel_command))
    app.add_handler(CommandHandler(["editchannel", "managechannel"], editchannel_command))
    app.add_handler(CommandHandler(["delchannel", "removechannel"], delchannel_command))
    app.add_handler(CommandHandler(["chat", "ai"], chat_command))
    app.add_handler(CommandHandler("idea", idea_command))
    app.add_handler(CommandHandler("edit", edit_command))
    app.add_handler(CommandHandler("media", media_command))
    app.add_handler(CommandHandler("render", render_command))
    app.add_handler(CommandHandler("queue", queue_command))
    app.add_handler(CommandHandler("cancel", cancel_queue_command))
    app.add_handler(CommandHandler(["pending", "resume"], pending_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CallbackQueryHandler(channel_callback, pattern=r"^(select_channel|manage_channel|pick_niche|set_niche|prompt_rename|add_tpl|prompt_custom_channel|pick_voice|set_voice|pick_lang|set_lang|pick_visual|set_visual|confirm_del_channel|do_del_channel|prompt_add_channel|back_channels|refresh_channels)"))
    app.add_handler(CallbackQueryHandler(video_callback, pattern=r"^(render_video|publish_now|publish_youtube|feedback_video|edit_script|schedule|schedule_youtube|regenerate|cancel|process_all_pending|refresh_pending|retry_all_failed)"))
    app.add_handler(CallbackQueryHandler(chat_callback, pattern=r"^(convert_chat|suggest_more|change_hooks):"))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, media_upload_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    async def error_handler(update, context):
        err_msg = str(context.error) if context.error else ""
        # Bỏ qua các cảnh báo mạng vô hại của Telegram (bấm nút trễ, query quá hạn, text không đổi)
        if any(ign in err_msg for ign in ["Query is too old", "query id is invalid", "Message is not modified"]):
            logger.debug(f"Bỏ qua cảnh báo vô hại của Telegram: {err_msg}")
            return

        logger.error(f"⚠️ Lỗi Telegram xảy ra: {context.error}", exc_info=context.error)
        if update and update.effective_message:
            try:
                await update.effective_message.reply_text(f"⚠️ Đã có lỗi xảy ra: {context.error}")
            except Exception:
                pass

    app.add_error_handler(error_handler)

    print("🤖 Bot đang chạy Polling... Cả nhóm có thể ra lệnh từ Telegram hoặc xem Web!")
    print("👉 Mở trình duyệt xem Dashboard: http://127.0.0.1:8000")
    print("=" * 70)
    app.run_polling(drop_pending_updates=False)

if __name__ == "__main__":
    main()
