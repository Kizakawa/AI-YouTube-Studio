import os
import sys
import site
import time
import threading
import logging

# Tự động nạp thư viện từ Python gốc (C:\Python\Lib\site-packages hoặc sys.base_prefix)
# khi IDE tự động kích hoạt môi trường ảo (.venv) rỗng làm thiếu thư viện telegram/fastapi
try:
    import telegram  # noqa: F401
except ModuleNotFoundError:
    _candidate_site_dirs = [
        os.path.join(getattr(sys, "base_prefix", ""), "Lib", "site-packages"),
        r"C:\Python\Lib\site-packages",
    ]
    for _sdir in _candidate_site_dirs:
        if _sdir and os.path.isdir(_sdir) and _sdir not in sys.path:
            site.addsitedir(_sdir)

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
    videos_command, get_video_command, schedule_command,
    kpi_command, auto_resume_pending_jobs,
    autopilot_command, autopilot_callback, library_command
)
from app.utils.ffmpeg_helper import is_ffmpeg_installed, get_ffmpeg_version
from app.utils.log_config import configure_logging

def get_network_info():
    """Lấy thông tin mạng để hiển thị URL truy cập Web Dashboard"""
    port = int(os.getenv("PORT", 8000))
    web_url = os.getenv("WEB_DASHBOARD_URL") or f"http://localhost:{port}"
    return {
        "port": port,
        "lan_ip": "127.0.0.1",
        "local_url": f"http://127.0.0.1:{port}",
        "localhost_url": f"http://localhost:{port}",
        "wifi_url": web_url,
        "mdns_url": web_url,
        "is_lan_available": False
    }

def recover_stuck_jobs():
    """Tự động phân loại các video bị gián đoạn khi khởi động lại:
    - Nếu mới bị ngắt trong vòng 3 giờ gần đây: Giữ trạng thái RENDERING để Auto-Resume tự động dựng tiếp.
    - Nếu đã treo quá 3 giờ: Đưa về SCRIPT_READY để tránh kẹt hàng đợi.
    """
    try:
        from datetime import datetime, timedelta
        from app.database.models import get_db, VideoJob, log_activity
        cutoff = datetime.utcnow() - timedelta(hours=3)
        with get_db() as db:
            stuck_jobs = db.query(VideoJob).filter(
                VideoJob.status.in_(["RENDERING", "AUDIO_READY", "MEDIA_READY"])
            ).all()
            recent_resume_ids = []
            stale_reset_ids = []
            for j in stuck_jobs:
                ref_time = (j.updated_at or j.created_at or datetime.utcnow()).replace(tzinfo=None)
                if ref_time >= cutoff:
                    j.status = "RENDERING"
                    recent_resume_ids.append(j.id)
                else:
                    j.status = "SCRIPT_READY"
                    j.progress_percent = 0
                    j.progress_message = "Đã khôi phục về SCRIPT_READY (tác vụ cũ quá 3 giờ)"
                    log_activity(db, 0, "System", "RECOVER_JOB", f"Khôi phục Video #{j.id} (quá 3h) về SCRIPT_READY")
                    stale_reset_ids.append(j.id)
            if recent_resume_ids:
                print(f"   -> [Auto-Resume] Phát hiện {len(recent_resume_ids)} video đang dựng dở ({recent_resume_ids}) -> Sẽ tự động dựng tiếp ngay khi Bot khởi động!")
            if stale_reset_ids:
                print(f"   -> [Khôi phục] Đã đưa {len(stale_reset_ids)} video treo quá 3h về trạng thái sẵn sàng.")
    except Exception as e:
        print(f"   -> ⚠️ Lỗi khôi phục jobs: {e}")

# Đảm bảo in tiếng Việt và emoji không bị lỗi trên Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# [Phase 3] Cau hinh logging tap trung — thay the logging.basicConfig()
from app.utils.log_config import configure_logging
configure_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    log_format=os.getenv("LOG_FORMAT", "console")
)

logger = logging.getLogger("AI-YouTube")

def print_network_banner():
    """Hiển thị địa chỉ truy cập Web Dashboard chuẩn HTTP"""
    port = int(os.getenv("PORT", 8000))
    web_url = os.getenv("WEB_DASHBOARD_URL") or f"http://localhost:{port}"
    print("\n" + "═" * 70)
    print("🌐 AI YOUTUBE STUDIO - WEB DASHBOARD ĐANG HOẠT ĐỘNG")
    print("═" * 70)
    print(f"💻 Web Dashboard: {web_url}")
    print("💡 Chia sẻ ra Internet (Cloudflare): chạy 'python tunnel.py' hoặc click 'tunnel.bat'")
    print("═" * 70 + "\n")

def run_fastapi_server():
    """Khởi động Web Dashboard bằng Uvicorn trong background thread"""
    try:
        import uvicorn
        port = int(os.getenv("PORT", 8000))
        print_network_banner()
        if "--tunnel" in sys.argv:
            try:
                import threading
                from tunnel import start_tunnel
                threading.Thread(target=start_tunnel, daemon=True).start()
            except Exception as e_tun:
                print(f"⚠️ [Tunnel] Không thể khởi động Cloudflare Tunnel: {e_tun}")
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

    # Trạng thái engine sinh ảnh AI (đọc cấu hình, không tốn quota)
    try:
        from app.services.media.image_engines import health_check
        img_status = health_check(live=False)
        ready = [k for k, v in img_status.items() if v == "sẵn sàng"]
        print(f"   -> Engine sinh ảnh AI sẵn sàng: {', '.join(ready) if ready else 'KHÔNG CÓ'}")
        if not ready:
            print("   -> [CẢNH BÁO] Mọi cảnh AI sẽ dùng ảnh web/stock (dễ lạc đề). "
                  "Thêm CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN vào .env để bật FLUX miễn phí.")
    except Exception as img_err:
        print(f"   -> [CẢNH BÁO] Không kiểm tra được engine ảnh: {img_err}")

    # 4. Kiểm tra chế độ chạy
    disable_polling = (
        "--web-only" in sys.argv or "--web" in sys.argv or "--no-bot" in sys.argv
        or os.getenv("WEB_ONLY")
        or os.getenv("DISABLE_TELEGRAM_POLLING", "0").lower() in ("1", "true", "yes")
        or os.getenv("ENABLE_TELEGRAM_POLLING", "1").lower() in ("0", "false", "no")
    )
    if disable_polling:
        print("[4/4] 🌐 [CHẾ ĐỘ WEB DASHBOARD] Khởi động Web Dashboard (Không chiếm Polling Telegram)!")
        net = get_network_info()
        print(f"👉 Trên máy tính này: {net['local_url']}")
        if net['is_lan_available']:
            print(f"👉 Trên điện thoại / máy khác (cùng Wi-Fi): {net['wifi_url']}")
        print("=" * 70)

        # Khởi động ChannelIntelligenceWorker ngầm
        try:
            from app.services.channel_intelligence import ChannelIntelligenceWorker
            ChannelIntelligenceWorker.start(check_interval_minutes=15)
        except Exception as ex_ci:
            logger.debug(f"ChannelIntelligenceWorker: {ex_ci}")

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
        .concurrent_updates(True)
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
    app.add_handler(CommandHandler(["viral", "trending", "xuhuong", "trend", "trends", "googletrends", "google_trends"], viral_command))
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
    app.add_handler(CommandHandler(["schedule", "lich", "lichdang", "lich_dang"], schedule_command))
    app.add_handler(CommandHandler(["library", "dadang", "da_dang", "thuvien"], library_command))
    app.add_handler(CommandHandler(["kpi", "baocao", "tiendo", "report"], kpi_command))
    app.add_handler(CommandHandler(["autopilot", "auto", "tudong", "autopilots"], autopilot_command))
    app.add_handler(CallbackQueryHandler(channel_callback, pattern=r"^(select_channel|manage_channel|pick_niche|set_niche|prompt_rename|add_tpl|prompt_custom_channel|pick_voice|set_voice|pick_lang|set_lang|pick_visual|set_visual|confirm_del_channel|do_del_channel|prompt_add_channel|back_channels|refresh_channels)"))
    app.add_handler(CallbackQueryHandler(video_callback, pattern=r"^(render_video|check_drive_render|render_local|publish_now|publish_youtube|publish_to_channel|force_publish_to_channel|rotate_publish_to|wait_quota_job|pick_publish_channel|schedule_to_channel|cancel_publish|feedback_video|edit_script|edit_lang|edit_voice|cancel_edit|delete_video|schedule|schedule_youtube|switch_lang|regenerate|cancel|process_all_pending|refresh_pending|retry_all_failed|apply_media|pick_viral_topic|refresh_viral_ideas|close_viral|send_video_file|videos_page|refresh_videos|refresh_schedule|approve_auto|auto_gen_kpi|refresh_kpi_report|approve_device|reject_device|confirm_del_vid|cancel_del_vid|confirm_del_failed|back_script_review)"))
    app.add_handler(CallbackQueryHandler(chat_callback, pattern=r"^(convert_chat|suggest_more|change_hooks):"))
    app.add_handler(CallbackQueryHandler(autopilot_callback, pattern=r"^(autopilot_)"))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, media_upload_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    async def _auto_sync_scheduler(context):
        try:
            from app.services.smart_scheduler import sync_scheduled_jobs_status
            updated = sync_scheduled_jobs_status()
            if updated > 0:
                logger.info(f"🔄 [AutoScheduler] Đã tự động đồng bộ {updated} tác vụ (SCHEDULED -> PUBLISHED, quota flush).")
        except Exception as e_s:
            logger.error(f"[AutoScheduler error]: {e_s}", exc_info=True)

    async def _auto_check_autopilot_reminder(context):
        try:
            from app.services.autopilot_service import check_and_send_daily_reminders
            check_and_send_daily_reminders(bot=context.bot)
        except Exception as e_ar:
            logger.error(f"[AutoPilot Watchdog error]: {e_ar}", exc_info=True)

    if app.job_queue:
        app.job_queue.run_repeating(_auto_sync_scheduler, interval=60, first=10)
        # Giám sát báo cáo & nhắc nhở kênh theo lịch cố định (09:00 & 16:30 VN - gửi thẳng vào nhóm)
        app.job_queue.run_repeating(_auto_check_autopilot_reminder, interval=60, first=30)

    # Khởi động ChannelIntelligenceWorker ngầm (giám sát KPI, tự động tạo video cứu nguy khi trễ deadline)
    try:
        from app.services.channel_intelligence import ChannelIntelligenceWorker
        ChannelIntelligenceWorker.start(check_interval_minutes=15)
        print("   -> ChannelIntelligenceWorker: Đang giám sát KPI 4 kênh để tự động sản xuất video khi trễ hạn.")
    except Exception as e_ci:
        logger.warning(f"Lỗi khởi động ChannelIntelligenceWorker trong run.py: {e_ci}")


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

        # Bỏ qua các lỗi mạng chập chờn / Timed out tạm thời của Telegram (tránh bắn tin nhắn lỗi cộc lốc vào nhóm)
        if any(ign in err_msg for ign in ["Timed out", "timed out", "NetworkError", "ReadTimeout", "ConnectTimeout", "ReadError", "RemoteProtocolError"]):
            logger.warning(f"⚠️ [TELEGRAM NETWORK] Kết nối tới Telegram bị gián đoạn/timeout tạm thời ({err_msg}). Đang tự động kết nối lại...")
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
    # drop_pending_updates=False: Không xóa tin nhắn của người dùng gửi trong lúc server khởi động/deploy lại
    # Tự động kết nối lại nếu mạng WiFi/máy tính bị chập chờn hoặc rớt kết nối tạm thời
    conflict_count = 0
    while True:
        try:
            app.run_polling(drop_pending_updates=False, bootstrap_retries=-1)
            break
        except (KeyboardInterrupt, SystemExit):
            print("\n👋 Đã dừng Telegram Bot theo yêu cầu của bạn.")
            break
        except Exception as e:
            err_str = str(e)
            if "Conflict" in err_str or "terminated by other getUpdates" in err_str:
                conflict_count += 1
                wait_sec = min(60, 10 * conflict_count)
                logger.warning(
                    f"⚠️ [TELEGRAM 409 CONFLICT] Đang có một tiến trình Bot khác (trên Render hoặc máy khác) chạy cùng lúc với cùng 1 Token!\n"
                    f"   -> Đang tạm dừng {wait_sec}s trước khi kết nối lại để không gây nghẽn Telegram...\n"
                    f"   💡 Gợi ý: Nếu Bot đã chạy trên Render 24/7, bạn có thể chạy local bằng: 'python run.py --web' để tránh tranh chấp Token!"
                )
                time.sleep(wait_sec)
            else:
                conflict_count = 0
                logger.warning(f"⚠️ Mạng chập chờn / Mất kết nối tạm thời tới Telegram ({e}). Đang tự động kết nối lại sau 5 giây...")
                time.sleep(5)

if __name__ == "__main__":
    main()
