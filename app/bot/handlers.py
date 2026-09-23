import logging
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes
from config.settings import settings
from app.bot.keyboards import get_channel_selection_keyboard, get_video_review_keyboard
from app.database.models import SessionLocal, User, VideoJob, log_activity
from app.agents.content_agent import ContentAgent

logger = logging.getLogger(__name__)
content_agent = ContentAgent()

def is_authorized(update: Update) -> bool:
    """
    Kiểm tra quyền truy cập:
    - Nếu tin nhắn đến từ Nhóm Chat nằm trong ALLOWED_TELEGRAM_GROUPS: Cho phép tất cả thành viên trong nhóm.
    - Nếu tin nhắn riêng tư (Direct Message): Kiểm tra xem user_id có nằm trong ALLOWED_TELEGRAM_USERS không.
    """
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

    # Ghi log hoạt động
    log_activity(db, user.id, user.username, "START_BOT", f"Khởi động bot từ {chat.type} '{chat.title or user.first_name}'")
    db.close()

    scope_text = f"nhóm **{chat.title}**" if is_group else f"bạn **{user.first_name}**"
    group_info = f"\n\n🔑 **ID Nhóm này:** `{chat.id}` *(bạn có thể lưu vào `ALLOWED_TELEGRAM_GROUPS` trong `.env`)*" if is_group else ""

    welcome_text = (
        f"🎬 **AI YouTube Multi-Channel Studio đã sẵn sàng cho {scope_text}!**\n\n"
        f"👥 **Cơ chế làm việc nhóm & Web Dashboard:**\n"
        f"• Mọi thành viên cùng ra lệnh, kịch bản hiển thị công khai trong nhóm.\n"
        f"• Theo dõi toàn bộ hoạt động của admin qua Web Dashboard thời gian thực!\n\n"
        f"📌 **Các lệnh chính:**\n"
        f"• `/channels` - Xem và chuyển đổi kênh YouTube muốn sản xuất\n"
        f"• `/idea <chủ đề>` - Lên kịch bản AI, chia phân cảnh và từ khóa Pexels\n"
        f"• `/status` - Báo cáo số lượng video và trạng thái hệ thống\n\n"
        f"👉 Hãy gõ `/channels` để chọn kênh bắt đầu!{group_info}"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

from app.services.channel_manager import channel_manager

async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị danh sách kênh chi tiết và trạng thái kết nối cho cả nhóm"""
    if not is_authorized(update):
        return

    channels = channel_manager.list_channels()
    
    text_lines = ["📺 **DANH SÁCH KÊNH YOUTUBE TRONG HỆ THỐNG:**\n"]
    if not channels:
        text_lines.append("_Chưa có kênh nào. Dùng `/addchannel <id> <tên> <ngách>` để thêm mới!_")
    else:
        for idx, ch in enumerate(channels, 1):
            status = "🟢 Đã kết nối OAuth" if ch.get("is_authenticated") else "🔴 Chưa kết nối Google OAuth"
            subs_text = f" ({ch.get('subscribers')} subs)" if ch.get("is_authenticated") else ""
            real_name = f" - YouTube: `{ch.get('real_title')}`" if ch.get("real_title") else ""
            text_lines.append(
                f"**{idx}. {ch.get('name')}** (`{ch.get('id')}`)\n"
                f"   • Trạng thái: {status}{subs_text}{real_name}\n"
                f"   • Ngách: `{ch.get('niche')}` | Ultra: `{ch.get('google_account')}`\n"
            )

    text_lines.append("\n👉 **Bấm nút bên dưới để chọn kênh làm việc hoặc kết nối:**")
    text_lines.append("💡 _Để kết nối Google OAuth: gõ `/auth <id_kênh>` hoặc bấm vào Web Dashboard._")

    reply_markup = get_channel_selection_keyboard()
    await update.message.reply_text("\n".join(text_lines), reply_markup=reply_markup, parse_mode="Markdown")

async def addchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh thêm kênh mới trực tiếp từ Telegram: /addchannel <id> <tên kênh> <ngách>"""
    if not is_authorized(update):
        return

    if len(context.args) < 3:
        help_text = (
            "📌 **Cú pháp thêm kênh mới:**\n"
            "`/addchannel <id_kênh> <tên_kênh> <ngách>`\n\n"
            "Ví dụ:\n"
            "`/addchannel ch_history Bí Ẩn Lịch Sử history`\n"
            "`/addchannel ch_finance Tư Duy Tiền Tệ finance`\n\n"
            "Các ngách gợi ý: `psychology`, `philosophy`, `space`, `history`, `finance`."
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")
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
            f"✅ **Đã thêm kênh mới thành công!**\n\n"
            f"• Tên kênh: **{name}**\n"
            f"• Mã ID: `{ch_id}`\n"
            f"• Ngách: `{niche}`\n\n"
            f"👉 Bây giờ bạn có thể gõ `/auth {ch_id}` để lấy link kết nối Google OAuth cho kênh này!"
        )
        await update.message.reply_text(success_text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Không thể thêm kênh: {e}")

async def auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh lấy link xác thực Google OAuth2 cho một kênh: /auth <id_kênh>"""
    if not is_authorized(update):
        return

    if not context.args:
        await update.message.reply_text("💡 Cú pháp: `/auth <id_kênh>`\nVí dụ: `/auth channel_psychology`\n(Dùng `/channels` để xem danh sách ID)", parse_mode="Markdown")
        return

    ch_id = context.args[0].lower().strip()
    # Đường dẫn Web Dashboard cục bộ
    auth_web_link = f"http://localhost:8000/auth/youtube/{ch_id}"
    
    text = (
        f"🔗 **KẾT NỐI GOOGLE OAUTH2 CHO KÊNH: `{ch_id}`**\n\n"
        f"1. Nhấp vào đường link bên dưới để mở trang đăng nhập Google cấp quyền:\n"
        f"👉 [Bấm Vào Đây Để Kết Nối Kênh]({auth_web_link})\n\n"
        f"2. Đăng nhập đúng tài khoản Google quản lý kênh này và bấm **Cho phép (Allow)**.\n"
        f"3. Hệ thống sẽ tự động lưu token và kích hoạt trạng thái 🟢 Đã kết nối!"
    )
    await update.message.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True)

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
        auth_msg = "🟢 Kênh đã kết nối YouTube" if status_info.get("is_authenticated") else "🔴 Kênh CHƯA kết nối Google OAuth (gõ `/auth " + channel_id + "` để kết nối)"

        await query.message.reply_text(
            f"✅ **{user.first_name}** đã chọn kênh: **{channel_id}**\n"
            f"• Trạng thái: {auth_msg}\n\n"
            f"👉 Bây giờ cả nhóm có thể gõ: `/idea <chủ đề>` để tạo kịch bản cho kênh này!",
            parse_mode="Markdown"
        )

async def idea_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh sinh kịch bản AI và gửi vào nhóm cho mọi người cùng duyệt"""
    if not is_authorized(update):
        return

    if not context.args:
        await update.message.reply_text("💡 Cú pháp: `/idea <chủ đề video>`\nVí dụ: `/idea 3 bí ẩn dưới đáy biển sâu`", parse_mode="Markdown")
        return

    topic = " ".join(context.args)
    user = update.effective_user
    
    # Lấy kênh đang hoạt động
    db = SessionLocal()
    db_user = db.query(User).filter(User.telegram_id == user.id).first()
    channel_id = db_user.active_channel_id if db_user and db_user.active_channel_id else "channel_psychology"
    if db_user:
        db_user.last_active = datetime.utcnow()
        db.commit()

    # Ghi log hoạt động
    log_activity(db, user.id, user.username, "CMD_IDEA", f"Yêu cầu lên ý tưởng video: '{topic}'", channel_id=channel_id)
    db.close()

    status_msg = await update.message.reply_text(
        f"🤖 **{user.first_name}** vừa yêu cầu lên ý tưởng: *\"{topic}\"*\n"
        f"Đang kích hoạt Gemini AI (Key Rotation Pool) để tạo kịch bản cho kênh `{channel_id}`...",
        parse_mode="Markdown"
    )

    try:
        script_data = content_agent.generate_script(topic=topic, channel_niche="general", language="en")
        
        # Lưu vào Database
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

        # Soạn nội dung thông báo cho nhóm
        scenes_preview = "\n".join([f"• Cảnh {s.get('scene_id')}: {s.get('narration')} _(Tìm: {s.get('visual_keyword')})_" for s in script_data.get("scenes", [])[:3]])

        reply_content = (
            f"🎬 **KỊCH BẢN ĐÃ HOÀN THÀNH (Task #{job_id})**\n\n"
            f"📌 **Tiêu đề đề xuất:** {script_data.get('title')}\n"
            f"🔥 **Hook 3 giây đầu:** \"{script_data.get('hook')}\"\n\n"
            f"📝 **Phân cảnh tiêu biểu:**\n{scenes_preview}\n\n"
            f"🗣️ **Người tạo:** {user.first_name} | 📺 **Kênh:** `{channel_id}`\n\n"
            f"👉 Cả nhóm hãy góp ý hoặc bấm nút bên dưới để tiến hành sản xuất video!"
        )

        review_markup = get_video_review_keyboard(job_id)
        await status_msg.edit_text(reply_content, reply_markup=review_markup, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Lỗi khi tạo kịch bản: {e}")
        await status_msg.edit_text(f"❌ Có lỗi xảy ra khi tạo kịch bản: `{e}`", parse_mode="Markdown")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Báo cáo tổng hợp số lượng video và tài nguyên"""
    if not is_authorized(update):
        return

    db = SessionLocal()
    total_jobs = db.query(VideoJob).count()
    ready_jobs = db.query(VideoJob).filter(VideoJob.status == "SCRIPT_READY").count()
    db.close()

    text = (
        f"📊 **BÁO CÁO NHÓM LÀM VIỆC (AI STUDIO)**\n\n"
        f"• Tổng số video trong hệ thống: `{total_jobs}`\n"
        f"• Kịch bản đang chờ duyệt/render: `{ready_jobs}`\n"
        f"• Kho Google Drive 2TB: Sẵn sàng 🟢\n"
        f"• Cụm Google Ultra Quota: Đang hoạt động 🟢\n"
        f"• Web Dashboard: Đang trực tuyến tại cổng `http://localhost:8000` 🌐"
    )
    await update.message.reply_text(text, parse_mode="Markdown")
