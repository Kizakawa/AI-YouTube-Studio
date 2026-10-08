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
    videos_command, get_video_command, schedule_command,
    kpi_command, auto_resume_pending_jobs,
    autopilot_command, autopilot_callback, library_command
)
from app.utils.ffmpeg_helper import is_ffmpeg_installed, get_ffmpeg_version

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
    """Tự động khôi phục các video bị gián đoạn do tắt bot đột ngột khi đang render và cảnh báo các job lỗi tồn đọng"""
    try:
        from app.database.models import get_db, VideoJob, log_activity
        with get_db() as db:
            # 1. Chỉ khôi phục các job thực sự bị ngắt giữa chừng khi đang RENDERING
            stuck_jobs = db.query(VideoJob).filter(VideoJob.status == "RENDERING").all()
            for j in stuck_jobs:
                j.status = "SCRIPT_READY"
                j.progress_percent = 0
                j.progress_message = "Đã khôi phục sau khi hệ thống khởi động lại"
                log_activity(db, 0, "System", "RECOVER_JOB", f"Khôi phục Video #{j.id} về SCRIPT_READY")
            if stuck_jobs:
                print(f"   -> [Khôi phục] Đã tự động khôi phục {len(stuck_jobs)} video bị gián đoạn về trạng thái sẵn sàng.")
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

async def check_drive_and_notify(bot_instance=None):
    """Quét thư mục Google Drive (/AI_YouTube_RenderFarm/completed/) và tự động tải video gửi về Telegram"""
    from app.services.colab_queue_service import check_and_import_completed_video
    from app.database.models import get_db, VideoJob, ActivityLog, log_activity
    from app.bot.keyboards import get_video_post_render_keyboard
    from app.agents.drive_agent import DriveAgent
    from telegram import Bot
    from config.settings import settings
    import re

    bot = bot_instance
    if bot is None:
        if not settings.TELEGRAM_BOT_TOKEN:
            return
        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)

    try:
        from app.services.colab_coordinator import colab_coordinator
        colab_coordinator.sync_cluster_state()
    except Exception as ex_coord:
        logger.debug(f"[Coordinator Sync error]: {ex_coord}")

    try:
        da = DriveAgent()
        completed_files = da.list_completed_videos()
        candidate_job_ids = set()
        for cf in completed_files:
            name = cf.get("name", "")
            m = re.search(r'video_(\d+)\.mp4', name)
            if m:
                candidate_job_ids.add(int(m.group(1)))

        with get_db() as db:
            waiting = db.query(VideoJob.id).filter(
                VideoJob.status.in_(["PENDING_COLAB", "SCRIPT_READY", "RENDERING"])
            ).all()
            for (wid,) in waiting:
                candidate_job_ids.add(wid)

        if not candidate_job_ids:
            return

        with get_db() as db:
            already_notified = set()
            logs = db.query(ActivityLog.action_type).filter(
                ActivityLog.action_type.like("TELEGRAM_NOTIFIED_VIDEO_%")
            ).all()
            for (act,) in logs:
                try:
                    already_notified.add(int(act.split("_")[-1]))
                except Exception:
                    pass

        for jid in sorted(list(candidate_job_ids)):
            if jid in already_notified:
                continue

            ok, local_p, d_link = check_and_import_completed_video(jid)
            if not ok:
                continue

            logger.info(f"🎉 [Drive Watcher] Đã tự động đồng bộ Video #{jid} từ Google Drive: {local_p}")

            with get_db() as db:
                jb = db.query(VideoJob).filter(VideoJob.id == jid).first()
                title = jb.title or jb.topic if jb else f"Video #{jid}"
                uid = jb.user_id if jb else 0
                ch_id = jb.target_channel_id if jb else None

            target_chats = set()
            if uid and uid != 0:
                target_chats.add(uid)
            for gid in settings.get_allowed_group_ids():
                target_chats.add(gid)
            if not target_chats:
                for u in settings.get_allowed_users().keys():
                    target_chats.add(u)

            from app.services.monetization_engine import find_best_amazon_product, build_amazon_url
            channels = settings.get_channels_config()
            ch_cfg = next((c for c in channels if c.get("id") == ch_id), {}) if ch_id else {}
            ch_niche = ch_cfg.get("niche", "psychology")
            ch_tag = ch_cfg.get("amazon_tag", "kizakawa-20")

            aff_prod = find_best_amazon_product(
                niche=ch_niche,
                title=title,
                topic=jb.topic if jb else "",
                script=jb.script if jb else ""
            )
            aff_url = build_amazon_url(aff_prod["asin"], tracking_tag=ch_tag)
            aff_name = aff_prod.get("name", "Sản phẩm đề xuất")
            aff_cta = aff_prod.get("cta", "")

            post_markup = get_video_post_render_keyboard(
                jid,
                affiliate_url=aff_url,
                affiliate_name=aff_name
            )
            import html
            safe_title = html.escape(str(title))
            safe_aff_name = html.escape(str(aff_name))
            safe_aff_cta = html.escape(str(aff_cta))
            aff_url_esc = html.escape(str(aff_url))
            size_mb = os.path.getsize(local_p) / (1024 * 1024) if (local_p and os.path.exists(local_p)) else 0
            drive_html = f'<a href="{d_link}">Mở file gốc trên Google Drive</a>' if d_link else "Đã lưu an toàn"
            cap = (
                f"🎉 <b>VIDEO #{jid} ĐÃ RENDER HOÀN TẤT &amp; ĐÃ GỬI VỀ NHÓM!</b>\n\n"
                f"📌 <b>Tiêu đề:</b> {safe_title}\n"
                f"📦 <b>Dung lượng:</b> {size_mb:.1f} MB\n"
                f"⚡ <b>Công nghệ:</b> GPU NVENC 1080p Siêu Nét (1080x1920 30fps)\n"
                f"☁️ <b>Google Drive:</b> {drive_html}\n\n"
                f"🛒 <b>SẢN PHẨM TIẾP THỊ LIÊN KẾT (AFFILIATE):</b>\n"
                f"• <b>Sản phẩm:</b> {safe_aff_name}\n"
                f"• <b>Gợi ý:</b> <i>{safe_aff_cta}</i>\n"
                f"👉 <b>Link mua ngay:</b> <a href=\"{aff_url_esc}\">{aff_url_esc}</a>\n\n"
                f"👉 Bấm <b>Đăng Ngay</b>, <b>Lên Lịch</b>, <b>Sửa</b> hoặc <b>Xóa</b> bên dưới:"
            )
            if len(cap) > 1000:
                cap = cap[:990] + "..."

            any_sent = False
            for t_chat in target_chats:
                sent = False
                if size_mb <= 49.0 and local_p and os.path.exists(local_p):
                    try:
                        with open(local_p, "rb") as vf:
                            await bot.send_video(
                                chat_id=t_chat,
                                video=vf,
                                caption=cap,
                                parse_mode="HTML",
                                reply_markup=post_markup,
                                supports_streaming=True,
                                read_timeout=180,
                                write_timeout=180
                            )
                        sent = True
                        any_sent = True
                        logger.info(f"✅ Đã tự động gửi video #{jid} đến Telegram chat {t_chat}")
                    except Exception as ex_v:
                        logger.warning(f"Lỗi gửi video #{jid} qua Telegram chat {t_chat}: {ex_v}")
                        try:
                            with open(local_p, "rb") as vf_doc:
                                await bot.send_document(
                                    chat_id=t_chat,
                                    document=vf_doc,
                                    caption=cap,
                                    parse_mode="HTML",
                                    reply_markup=post_markup,
                                    read_timeout=180,
                                    write_timeout=180
                                )
                            sent = True
                            any_sent = True
                            logger.info(f"✅ Đã gửi video dạng document #{jid} đến Telegram chat {t_chat}")
                        except Exception as ex_doc:
                            logger.warning(f"Lỗi gửi document video #{jid}: {ex_doc}")

                if not sent:
                    try:
                        await bot.send_message(
                            chat_id=t_chat,
                            text=cap,
                            parse_mode="HTML",
                            disable_web_page_preview=False,
                            reply_markup=post_markup
                        )
                        logger.info(f"✅ Đã gửi thông báo text #{jid} đến Telegram chat {t_chat}")
                        any_sent = True
                    except Exception as ex_m:
                        logger.warning(f"Lỗi gửi thông báo #{jid} qua Telegram chat {t_chat}: {ex_m}")

            if any_sent:
                with get_db() as db:
                    log_activity(db, uid, "System", f"TELEGRAM_NOTIFIED_VIDEO_{jid}",
                                 f"Đã gửi video #{jid} thành công đến các kênh Telegram",
                                 channel_id=ch_id)
                    db.commit()
    except Exception as e_w:
        logger.error(f"[Drive Watcher error]: {e_w}", exc_info=True)

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
    disable_polling = (
        "--web-only" in sys.argv or "--web" in sys.argv or "--no-bot" in sys.argv
        or os.getenv("WEB_ONLY")
        or os.getenv("DISABLE_TELEGRAM_POLLING", "0").lower() in ("1", "true", "yes")
        or os.getenv("ENABLE_TELEGRAM_POLLING", "1").lower() in ("0", "false", "no")
    )
    if disable_polling:
        print("[4/4] 🌐 [CHẾ ĐỘ WEB & DRIVE WATCHER] Khởi động Web Dashboard & Quét Drive ngầm (Không chiếm Polling)!")
        net = get_network_info()
        print(f"👉 Trên máy tính này: {net['local_url']}")
        if net['is_lan_available']:
            print(f"👉 Trên điện thoại / máy khác (cùng Wi-Fi): {net['wifi_url']}")
        print("🔒 Không chiếm quyền getUpdates (tránh 409 Conflict). Vẫn tự động quét Drive & bắn video về Telegram!")
        print("=" * 70)

        # Khởi động Drive Watcher ngầm ngay cả khi không polling
        def _standalone_drive_loop():
            import asyncio
            async def _run():
                from telegram import Bot
                bot = Bot(token=settings.TELEGRAM_BOT_TOKEN) if settings.TELEGRAM_BOT_TOKEN else None
                while True:
                    try:
                        if bot:
                            await check_drive_and_notify(bot)
                    except Exception as ex:
                        logger.debug(f"[Standalone Drive Watcher] {ex}")
                    await asyncio.sleep(15)
            asyncio.run(_run())
        threading.Thread(target=_standalone_drive_loop, daemon=True).start()

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
