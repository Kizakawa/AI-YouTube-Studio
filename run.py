import os
import sys
import time
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
    pending_command, addchannel_command, editchannel_command, delchannel_command,
    search_image_command, search_video_command,
    viral_command,
    videos_command, get_video_command, web_command, schedule_command,
    kpi_command, colab_command, auto_resume_pending_jobs
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

            # 2. Tự động khôi phục các job FAILED có kịch bản sẵn (đang làm dở bị lỗi hoặc hủy) để bot tự động dựng lại
            failed_jobs = db.query(VideoJob).filter(
                VideoJob.status == "FAILED",
                VideoJob.scenes_json.isnot(None)
            ).order_by(VideoJob.id.desc()).all()
            if failed_jobs:
                print(f"   -> [Tự động khôi phục] Phát hiện {len(failed_jobs)} video lỗi dở có kịch bản — Chuyển sang SCRIPT_READY để bot dựng tiếp:")
                for fj in failed_jobs:
                    fj.status = "SCRIPT_READY"
                    fj.progress_percent = 0
                    fj.error_message = None
                    fj.progress_message = "Tự động khôi phục khi khởi động lại hệ thống"
                    log_activity(db, 0, "System", "AUTO_RECOVER_FAILED", f"Khôi phục Video lỗi #{fj.id} để tự động render lại")
                    title_clean = fj.title[:35] if fj.title else (fj.topic or f"Job #{fj.id}")
                    print(f"      * Job #{fj.id}: {title_clean} -> Đã chuyển sang SCRIPT_READY để tự động render!")
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
# Tắt log spam getUpdates của httpx để màn hình console sạch sẽ, dễ nhìn
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("AI-YouTube")

def print_network_banner():
    """Hiển thị địa chỉ truy cập Web Dashboard chuẩn HTTP"""
    port = int(os.getenv("PORT", 8000))
    web_url = os.getenv("WEB_DASHBOARD_URL") or f"http://localhost:{port}"
    print("\n" + "═" * 70)
    print("🌐 AI YOUTUBE STUDIO - WEB DASHBOARD ĐANG HOẠT ĐỘNG")
    print("═" * 70)
    print(f"💻 Web Dashboard: {web_url}")
    print("═" * 70 + "\n")

def run_fastapi_server():
    """Khởi động Web Dashboard bằng Uvicorn trong background thread"""
    try:
        import uvicorn
        port = int(os.getenv("PORT", 8000))
        print_network_banner()
        uvicorn.run("app.web.server:app", host="0.0.0.0", port=port, log_level="warning", timeout_keep_alive=30)
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
        net = get_network_info()
        print(f"👉 Trên máy tính này: {net['local_url']}")
        if net['is_lan_available']:
            print(f"👉 Trên điện thoại / máy khác (cùng Wi-Fi): {net['wifi_url']}")
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
        net = get_network_info()
        print(f"   -> Web Dashboard vẫn hoạt động bình thường tại {net['local_url']}")
        if net['is_lan_available']:
            print(f"   -> Hoặc mở trên điện thoại (cùng Wi-Fi): {net['wifi_url']}")
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
        .post_init(auto_resume_pending_jobs)
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
    app.add_handler(CommandHandler(["viral", "trending", "xuhuong"], viral_command))
    app.add_handler(CommandHandler("idea", idea_command))
    app.add_handler(CommandHandler("edit", edit_command))
    app.add_handler(CommandHandler("media", media_command))
    app.add_handler(CommandHandler("render", render_command))
    app.add_handler(CommandHandler("queue", queue_command))
    app.add_handler(CommandHandler("cancel", cancel_queue_command))
    app.add_handler(CommandHandler(["search_image", "find_image", "tim_anh"], search_image_command))
    app.add_handler(CommandHandler(["search_video", "find_video", "tim_video"], search_video_command))
    app.add_handler(CommandHandler(["pending", "resume"], pending_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler(["videos", "list_videos", "dsvideo", "ds_video", "list"], videos_command))
    app.add_handler(CommandHandler(["get", "getvideo", "xem", "download", "tai_video"], get_video_command))
    app.add_handler(CommandHandler(["web", "dashboard", "wifi", "link"], web_command))
    app.add_handler(CommandHandler(["schedule", "lich", "lichdang", "lich_dang"], schedule_command))
    app.add_handler(CommandHandler(["kpi", "baocao", "tiendo", "report"], kpi_command))
    app.add_handler(CommandHandler(["colab", "gpu", "renderfarm"], colab_command))
    app.add_handler(CallbackQueryHandler(channel_callback, pattern=r"^(select_channel|manage_channel|pick_niche|set_niche|prompt_rename|add_tpl|prompt_custom_channel|pick_voice|set_voice|pick_lang|set_lang|pick_visual|set_visual|confirm_del_channel|do_del_channel|prompt_add_channel|back_channels|refresh_channels)"))
    app.add_handler(CallbackQueryHandler(video_callback, pattern=r"^(render_video|check_drive_render|render_local|publish_now|publish_youtube|publish_to_channel|schedule_to_channel|cancel_publish|feedback_video|edit_script|edit_lang|edit_voice|cancel_edit|schedule|schedule_youtube|regenerate|cancel|process_all_pending|refresh_pending|retry_all_failed|apply_media|pick_viral_topic|refresh_viral_ideas|close_viral|send_video_file|videos_page|refresh_videos|refresh_schedule|approve_auto|auto_gen_kpi|refresh_kpi_report|approve_device|reject_device|confirm_del_vid|cancel_del_vid|confirm_del_failed|check_colab_status|colab_rotation_guide)"))
    app.add_handler(CallbackQueryHandler(chat_callback, pattern=r"^(convert_chat|suggest_more|change_hooks):"))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, media_upload_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    # Khởi động luồng ngầm tự động theo dõi video Colab hoàn thành trên Google Drive
    def _drive_watcher():
        import time
        from app.services.colab_queue_service import check_and_import_completed_video
        from app.database.models import get_db, VideoJob
        time.sleep(10)
        while True:
            try:
                with get_db() as db:
                    waiting = db.query(VideoJob).filter(VideoJob.status.in_(["PENDING_COLAB", "SCRIPT_READY"])).all()
                    j_ids = [w.id for w in waiting]
                for jid in j_ids:
                    ok, local_p, d_link = check_and_import_completed_video(jid)
                    if ok:
                        logger.info(f"🎉 [Drive Watcher] Đã tự động đồng bộ Video #{jid} từ Google Drive: {local_p}")
            except Exception as e_w:
                logger.debug(f"[Drive Watcher error]: {e_w}")
            time.sleep(25)

    threading.Thread(target=_drive_watcher, daemon=True).start()

    async def error_handler(update, context):
        err_msg = str(context.error) if context.error else ""
        # Bỏ qua các cảnh báo mạng vô hại của Telegram (bấm nút trễ, query quá hạn, text không đổi)
        if any(ign in err_msg for ign in ["Query is too old", "query id is invalid", "Message is not modified"]):
            logger.debug(f"Bỏ qua cảnh báo vô hại của Telegram: {err_msg}")
            return

        # Bỏ qua và chờ tự phục hồi nếu gặp xung đột getUpdates (khi Render deploy rolling hoặc chạy song song local/server)
        if any(ign in err_msg for ign in ["Conflict", "terminated by other getUpdates request"]):
            logger.warning("⚠️ [TELEGRAM CONFLICT] Đang có một tiến trình Bot khác chạy cùng lúc (local hoặc Render rolling deploy). Đang chờ kết nối ổn định...")
            return

        logger.error(f"⚠️ Lỗi Telegram xảy ra: {context.error}", exc_info=context.error)
        if update and update.effective_message:
            try:
                await update.effective_message.reply_text(f"⚠️ Đã có lỗi xảy ra: {context.error}")
            except Exception:
                pass

    app.add_error_handler(error_handler)

    port = int(os.getenv("PORT", 8000))
    web_url = os.getenv("WEB_DASHBOARD_URL") or f"http://localhost:{port}"
    print("🤖 Bot đang chạy Polling... Cả nhóm có thể ra lệnh từ Telegram hoặc xem Web!")
    print(f"👉 Địa chỉ Web Studio: {web_url}")
    print("=" * 70)
    # drop_pending_updates=True: Xóa sạch các update lỗi tồn đọng cũ khi khởi động lại
    # Tự động kết nối lại nếu mạng WiFi/máy tính bị chập chờn hoặc rớt kết nối tạm thời
    while True:
        try:
            app.run_polling(drop_pending_updates=True, bootstrap_retries=-1)
            break
        except (KeyboardInterrupt, SystemExit):
            print("\n👋 Đã dừng Telegram Bot theo yêu cầu của bạn.")
            break
        except Exception as e:
            logger.warning(f"⚠️ Mạng chập chờn / Mất kết nối tạm thời tới Telegram ({e}). Đang tự động kết nối lại sau 5 giây...")
            import time
            time.sleep(5)

if __name__ == "__main__":
    main()
