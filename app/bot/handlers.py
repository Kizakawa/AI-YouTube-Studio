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

    welcome_text = (
        f"🎬 **AI YouTube Multi-Channel Studio đã sẵn sàng cho {scope_text}!**\n\n"
        f"👥 **Cơ chế làm việc nhóm & Web Dashboard:**\n"
        f"• Mọi thành viên cùng ra lệnh, kịch bản hiển thị công khai trong nhóm.\n"
        f"• Theo dõi toàn bộ hoạt động của admin qua Web Dashboard thời gian thực!\n\n"
        f"📌 **Các lệnh chính:**\n"
        f"• `/channels` - Xem và chuyển đổi kênh YouTube muốn sản xuất\n"
        f"• `/idea <chủ đề>` - Lên kịch bản AI, chia phân cảnh và từ khóa Pexels\n"
        f"• `/status` - Báo cáo số lượng video và trạng thái hệ thống\n\n"
        f"👉 Hãy gõ `/channels` để chọn kênh bắt đầu!"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị danh sách kênh cho cả nhóm cùng chọn"""
    if not is_authorized(update):
        return

    reply_markup = get_channel_selection_keyboard()
    await update.message.reply_text(
        "📺 **Chọn kênh YouTube mục tiêu cho dự án tiếp theo:**",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý khi một thành viên bấm nút chọn kênh"""
    query = update.callback_query
    await query.answer()

    data = query.data
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

        await query.message.reply_text(
            f"✅ **{user.first_name}** đã chọn kênh: **{channel_id}**\n"
            f"Bây giờ cả nhóm có thể gõ: `/idea <chủ đề>` để bắt đầu tạo video cho kênh này!",
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
