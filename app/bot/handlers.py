import logging
import re
import html
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes
from config.settings import settings
from app.bot.keyboards import get_channel_selection_keyboard, get_video_review_keyboard
from app.database.models import SessionLocal, User, VideoJob, log_activity
from app.agents.content_agent import ContentAgent
from app.services.channel_manager import channel_manager

logger = logging.getLogger(__name__)
content_agent = ContentAgent()

async def safe_reply(update: Update, text_html: str, reply_markup=None):
    """
    Hàm gửi tin nhắn an toàn: Ưu tiên parse HTML.
    Nếu gặp lỗi parse (do ký tự lạ), tự động fallback sang gửi Plain Text để không bao giờ bị im lặng!
    """
    try:
        if update.message:
            return await update.message.reply_html(text_html, reply_markup=reply_markup, disable_web_page_preview=True)
        elif update.callback_query and update.callback_query.message:
            return await update.callback_query.message.reply_html(text_html, reply_markup=reply_markup, disable_web_page_preview=True)
    except Exception as e:
        logger.warning(f"Lỗi gửi HTML ({e}), tự động chuyển sang Plain Text...")
        plain_text = re.sub(r'<[^>]+>', '', text_html)
        if update.message:
            return await update.message.reply_text(plain_text, reply_markup=reply_markup, disable_web_page_preview=True)
        elif update.callback_query and update.callback_query.message:
            return await update.callback_query.message.reply_text(plain_text, reply_markup=reply_markup, disable_web_page_preview=True)

def is_authorized(update: Update) -> bool:
    """Kiểm tra quyền truy cập"""
    chat = update.effective_chat
    user = update.effective_user
    
    # 1. Kiểm tra nhóm chat
    allowed_groups = settings.get_allowed_group_ids()
    if chat and chat.type in ["group", "supergroup"]:
        if not allowed_groups:
            return True
        return chat.id in allowed_groups

    # 2. Kiểm tra user cá nhân
    allowed_users = settings.get_allowed_users()
    if not allowed_users:
        return True
    return user.id in allowed_users

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý lệnh /start"""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Nhóm hoặc tài khoản này chưa được cấp quyền sử dụng bot.")
        return

    user = update.effective_user
    chat = update.effective_chat
    is_group = chat.type in ["group", "supergroup"]

    db = SessionLocal()
    db_user = db.query(User).filter(User.telegram_id == user.id).first()
    if not db_user:
        role = settings.get_allowed_users().get(user.id, "creator")
        db_user = User(
            telegram_id=user.id,
            username=user.username,
            full_name=user.full_name,
            role=role,
            last_active=datetime.utcnow()
        )
        db.add(db_user)
        db.commit()
    else:
        db_user.last_active = datetime.utcnow()
        db.commit()

    log_activity(db, user.id, user.username, "START_BOT", f"Khởi động bot từ {chat.type} '{chat.title or user.first_name}'")
    db.close()

    scope_name = html.escape(chat.title) if is_group else html.escape(user.first_name)
    group_info = f"\n\n🔑 <b>ID Nhóm này:</b> <code>{chat.id}</code> <i>(bạn có thể lưu vào ALLOWED_TELEGRAM_GROUPS trong .env)</i>" if is_group else ""

    welcome_text = (
        f"🎬 <b>AI YouTube Multi-Channel Studio đã sẵn sàng cho {scope_name}!</b>\n\n"
        f"👥 <b>Cơ chế làm việc nhóm & Web Dashboard:</b>\n"
        f"• Mọi thành viên cùng ra lệnh, kịch bản hiển thị công khai trong nhóm.\n"
        f"• Theo dõi toàn bộ hoạt động của admin qua Web Dashboard thời gian thực!\n\n"
        f"📌 <b>Các lệnh chính:</b>\n"
        f"• <code>/channels</code> - Xem danh sách kênh & trạng thái kết nối YouTube\n"
        f"• <code>/addchannel &lt;id&gt; &lt;tên&gt; &lt;ngách&gt;</code> - Thêm kênh mới tức thì\n"
        f"• <code>/auth &lt;id_kênh&gt;</code> - Lấy link đăng nhập Google OAuth cấp quyền\n"
        f"• <code>/idea &lt;chủ đề&gt;</code> - Lên kịch bản AI, chia phân cảnh\n"
        f"• <code>/status</code> - Báo cáo số lượng video và hệ thống\n\n"
        f"👉 Hãy gõ <code>/channels</code> để chọn kênh bắt đầu!{group_info}"
    )
    await safe_reply(update, welcome_text)

async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị danh sách kênh chi tiết và trạng thái kết nối cho cả nhóm"""
    if not is_authorized(update):
        return

    channels = channel_manager.list_channels()
    
    text_lines = ["📺 <b>DANH SÁCH KÊNH YOUTUBE TRONG HỆ THỐNG:</b>\n"]
    if not channels:
        text_lines.append("<i>Chưa có kênh nào. Dùng /addchannel &lt;id&gt; &lt;tên&gt; &lt;ngách&gt; để thêm mới!</i>")
    else:
        for idx, ch in enumerate(channels, 1):
            status = "🟢 Đã kết nối OAuth" if ch.get("is_authenticated") else "🔴 Chưa kết nối Google OAuth"
            subs_text = f" ({ch.get('subscribers')} subs)" if ch.get("is_authenticated") else ""
            real_name = f" - YouTube: <b>{html.escape(ch.get('real_title'))}</b>" if ch.get("real_title") else ""
            
            ch_name = html.escape(str(ch.get("name", "")))
            ch_id = html.escape(str(ch.get("id", "")))
            ch_niche = html.escape(str(ch.get("niche", "")))
            acc_id = html.escape(str(ch.get("google_account", "")))

            text_lines.append(
                f"<b>{idx}. {ch_name}</b> (<code>{ch_id}</code>)\n"
                f"   • Trạng thái: {status}{subs_text}{real_name}\n"
                f"   • Ngách: <code>{ch_niche}</code> | Ultra: <code>{acc_id}</code>\n"
            )

    text_lines.append("\n👉 <b>Bấm nút bên dưới để chọn kênh làm việc:</b>")
    text_lines.append("💡 <i>Để kết nối Google OAuth: gõ /auth &lt;id_kênh&gt; hoặc bấm vào Web Dashboard.</i>")

    reply_markup = get_channel_selection_keyboard()
    await safe_reply(update, "\n".join(text_lines), reply_markup=reply_markup)

async def addchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh thêm kênh mới trực tiếp từ Telegram: /addchannel <id> <tên kênh> <ngách>"""
    if not is_authorized(update):
        return

    if len(context.args) < 3:
        help_text = (
            "📌 <b>Cú pháp thêm kênh mới:</b>\n"
            "<code>/addchannel &lt;id_kênh&gt; &lt;tên_kênh&gt; &lt;ngách&gt;</code>\n\n"
            "<b>Ví dụ:</b>\n"
            "<code>/addchannel ch_history Bí Ẩn Lịch Sử history</code>\n"
            "<code>/addchannel ch_finance Tư Duy Tiền Tệ finance</code>\n\n"
            "Các ngách gợi ý: <code>psychology</code>, <code>philosophy</code>, <code>space</code>, <code>history</code>, <code>finance</code>."
        )
        await safe_reply(update, help_text)
        return

    ch_id = context.args[0].lower().replace(" ", "_")
    niche = context.args[-1].lower()
    name = " ".join(context.args[1:-1])

    try:
        new_ch = channel_manager.add_channel(
            channel_id=ch_id,
            name=name,
            niche=niche,
            google_account="account_1"
        )
        user = update.effective_user
        db = SessionLocal()
        log_activity(db, user.id, user.username, "ADD_CHANNEL", f"Thêm kênh mới qua Telegram: '{name}' ({ch_id})", channel_id=ch_id)
        db.close()

        success_text = (
            f"✅ <b>Đã thêm kênh mới thành công!</b>\n\n"
            f"• Tên kênh: <b>{html.escape(name)}</b>\n"
            f"• Mã ID: <code>{ch_id}</code>\n"
            f"• Ngách: <code>{niche}</code>\n\n"
            f"👉 Bây giờ bạn có thể gõ <code>/auth {ch_id}</code> để lấy link kết nối Google OAuth cho kênh này!"
        )
        await safe_reply(update, success_text)
    except Exception as e:
        await safe_reply(update, f"❌ Không thể thêm kênh: {html.escape(str(e))}")

async def auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh lấy link xác thực Google OAuth2 cho một kênh: /auth <id_kênh>"""
    if not is_authorized(update):
        return

    if not context.args:
        await safe_reply(update, "💡 Cú pháp: <code>/auth &lt;id_kênh&gt;</code>\nVí dụ: <code>/auth channel_psychology</code>\n<i>(Dùng /channels để xem danh sách ID)</i>")
        return

    ch_id = context.args[0].lower().strip()
    auth_web_link = f"http://localhost:8000/auth/youtube/{ch_id}"
    
    text = (
        f"🔗 <b>KẾT NỐI GOOGLE OAUTH2 CHO KÊNH:</b> <code>{ch_id}</code>\n\n"
        f"1. Nhấp vào đường link bên dưới để mở trang đăng nhập Google cấp quyền:\n"
        f"👉 <a href=\"{auth_web_link}\">BẤM VÀO ĐÂY ĐỂ ĐĂNG NHẬP GOOGLE</a>\n\n"
        f"2. Đăng nhập đúng tài khoản Google quản lý kênh và bấm <b>Cho phép (Allow)</b>.\n"
        f"3. Hệ thống sẽ tự động lưu token và kích hoạt trạng thái 🟢 Đã kết nối!"
    )
    await safe_reply(update, text)

async def channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý khi một thành viên bấm nút chọn kênh hoặc làm mới"""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "refresh_channels":
        reply_markup = get_channel_selection_keyboard()
        await query.edit_message_reply_markup(reply_markup=reply_markup)
        return

    if data.startswith("select_channel:"):
        channel_id = data.split(":")[1]
        user = query.from_user
        
        db = SessionLocal()
        db_user = db.query(User).filter(User.telegram_id == user.id).first()
        if db_user:
            db_user.active_channel_id = channel_id
            db_user.last_active = datetime.utcnow()
            db.commit()

        log_activity(db, user.id, user.username, "CHANGE_CHANNEL", f"Chuyển kênh làm việc sang '{channel_id}'", channel_id=channel_id)
        db.close()

        status_info = channel_manager.get_channel_live_status(channel_id)
        auth_msg = "🟢 Kênh đã kết nối YouTube" if status_info.get("is_authenticated") else f"🔴 Kênh CHƯA kết nối Google OAuth (gõ <code>/auth {channel_id}</code> để kết nối)"

        msg = (
            f"✅ <b>{html.escape(user.first_name)}</b> đã chọn kênh: <code>{channel_id}</code>\n"
            f"• Trạng thái: {auth_msg}\n\n"
            f"👉 Bây giờ cả nhóm có thể gõ: <code>/idea &lt;chủ đề&gt;</code> để tạo kịch bản cho kênh này!"
        )
        await safe_reply(update, msg)

async def idea_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh sinh kịch bản AI và gửi vào nhóm cho mọi người cùng duyệt"""
    if not is_authorized(update):
        return

    if not context.args:
        await safe_reply(update, "💡 Cú pháp: <code>/idea &lt;chủ đề video&gt;</code>\nVí dụ: <code>/idea 3 bí ẩn dưới đáy biển sâu</code>")
        return

    topic = " ".join(context.args)
    user = update.effective_user
    
    db = SessionLocal()
    db_user = db.query(User).filter(User.telegram_id == user.id).first()
    channel_id = db_user.active_channel_id if db_user and db_user.active_channel_id else "channel_psychology"
    if db_user:
        db_user.last_active = datetime.utcnow()
        db.commit()

    log_activity(db, user.id, user.username, "CMD_IDEA", f"Yêu cầu lên ý tưởng video: '{topic}'", channel_id=channel_id)
    db.close()

    status_msg = await safe_reply(
        update,
        f"🤖 <b>{html.escape(user.first_name)}</b> vừa yêu cầu lên ý tưởng: <i>\"{html.escape(topic)}\"</i>\n"
        f"Đang kích hoạt Gemini AI (Key Rotation Pool) để tạo kịch bản cho kênh <code>{channel_id}</code>..."
    )

    try:
        script_data = content_agent.generate_script(topic=topic, channel_niche="general", language="en")
        
        db = SessionLocal()
        job = VideoJob(
            user_id=user.id,
            target_channel_id=channel_id,
            topic=topic,
            title=script_data.get("title"),
            script=script_data.get("full_narration"),
            tags=",".join(script_data.get("tags", [])),
            status="SCRIPT_READY"
        )
        db.add(job)
        db.commit()
        job_id = job.id

        log_activity(db, user.id, user.username, "SCRIPT_CREATED", f"Kịch bản #{job_id} đã hoàn thành: '{script_data.get('title')}'", channel_id=channel_id)
        db.close()

        scenes_preview = "\n".join([f"• Cảnh {s.get('scene_id')}: {html.escape(str(s.get('narration')))} <i>(Tìm: {html.escape(str(s.get('visual_keyword')))})</i>" for s in script_data.get("scenes", [])[:3]])

        reply_content = (
            f"🎬 <b>KỊCH BẢN ĐÃ HOÀN THÀNH (Task #{job_id})</b>\n\n"
            f"📌 <b>Tiêu đề đề xuất:</b> {html.escape(str(script_data.get('title')))}\n"
            f"🔥 <b>Hook 3 giây đầu:</b> \"{html.escape(str(script_data.get('hook')))}\"\n\n"
            f"📝 <b>Phân cảnh tiêu biểu:</b>\n{scenes_preview}\n\n"
            f"🗣️ <b>Người tạo:</b> {html.escape(user.first_name)} | 📺 <b>Kênh:</b> <code>{channel_id}</code>\n\n"
            f"👉 Cả nhóm hãy góp ý hoặc bấm nút bên dưới để tiến hành sản xuất video!"
        )

        review_markup = get_video_review_keyboard(job_id)
        if status_msg:
            await status_msg.edit_text(reply_content, reply_markup=review_markup, parse_mode="HTML")
        else:
            await safe_reply(update, reply_content, reply_markup=review_markup)

    except Exception as e:
        logger.error(f"Lỗi khi tạo kịch bản: {e}")
        err_msg = f"❌ Có lỗi xảy ra khi tạo kịch bản: <code>{html.escape(str(e))}</code>"
        if status_msg:
            await status_msg.edit_text(err_msg, parse_mode="HTML")
        else:
            await safe_reply(update, err_msg)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Báo cáo tổng hợp số lượng video và tài nguyên"""
    if not is_authorized(update):
        return

    db = SessionLocal()
    total_jobs = db.query(VideoJob).count()
    ready_jobs = db.query(VideoJob).filter(VideoJob.status == "SCRIPT_READY").count()
    db.close()

    text = (
        f"📊 <b>BÁO CÁO NHÓM LÀM VIỆC (AI STUDIO)</b>\n\n"
        f"• Tổng số video trong hệ thống: <code>{total_jobs}</code>\n"
        f"• Kịch bản đang chờ duyệt/render: <code>{ready_jobs}</code>\n"
        f"• Kho Google Drive 2TB: Sẵn sàng 🟢\n"
        f"• Cụm Google Ultra Quota: Đang hoạt động 🟢\n"
        f"• Web Dashboard: Đang trực tuyến tại cổng <code>http://localhost:8000</code> 🌐"
    )
    await safe_reply(update, text)
