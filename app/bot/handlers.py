import logging
from telegram import Update
from telegram.ext import ContextTypes
from config.settings import settings
from app.bot.keyboards import get_channel_selection_keyboard, get_video_review_keyboard
from app.database.models import SessionLocal, User, VideoJob

logger = logging.getLogger(__name__)

def is_authorized(user_id: int) -> bool:
    """Kiểm tra xem user_id có nằm trong danh sách được cấp quyền không"""
    allowed_users = settings.get_allowed_users()
    if not allowed_users:
        # Nếu chưa cấu hình danh sách thì tạm thời cho phép để test
        return True
    return user_id in allowed_users

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý lệnh /start"""
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text("⛔ Bạn không có quyền truy cập hệ thống bot này.")
        return

    # Lưu hoặc cập nhật user vào database
    db = SessionLocal()
    db_user = db.query(User).filter(User.telegram_id == user.id).first()
    if not db_user:
        role = settings.get_allowed_users().get(user.id, "creator")
        db_user = User(
            telegram_id=user.id,
            username=user.username,
            full_name=user.full_name,
            role=role
        )
        db.add(db_user)
        db.commit()
    db.close()

    welcome_text = (
        f"👋 Xin chào **{user.first_name}**!\n\n"
        f"Chào mừng bạn đến với **AI YouTube Multi-Channel Studio**.\n\n"
        f"📌 **Các lệnh cơ bản:**\n"
        f"• `/channels` - Xem và chuyển đổi kênh YouTube muốn làm việc\n"
        f"• `/idea <chủ đề>` - Lên kịch bản và chuẩn bị sản xuất video\n"
        f"• `/status` - Xem tiến độ công việc và hạn ngạch API\n\n"
        f"👉 Bấm `/channels` để chọn kênh bắt đầu!"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị danh sách kênh để người dùng chọn"""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    reply_markup = get_channel_selection_keyboard()
    await update.message.reply_text(
        "📺 **Chọn kênh YouTube bạn muốn làm nội dung:**",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý khi người dùng bấm chọn kênh từ nút bấm"""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("select_channel:"):
        channel_id = data.split(":")[1]
        user_id = query.from_user.id
        
        db = SessionLocal()
        user = db.query(User).filter(User.telegram_id == user_id).first()
        if user:
            user.active_channel_id = channel_id
            db.commit()
        db.close()

        await query.edit_message_text(
            f"✅ Đã chuyển sang kênh mục tiêu: **{channel_id}**\n"
            f"Bây giờ bạn có thể gõ: `/idea <chủ đề>` để bắt đầu tạo video cho kênh này!",
            parse_mode="Markdown"
        )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kiểm tra trạng thái hệ thống"""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    db = SessionLocal()
    total_jobs = db.query(VideoJob).count()
    my_jobs = db.query(VideoJob).filter(VideoJob.user_id == user.id).count()
    db.close()

    text = (
        f"📊 **Báo Cáo Trạng Thái Hệ Thống**\n\n"
        f"• Người dùng: `{user.username or user.first_name}`\n"
        f"• Tổng số video đã tạo trong hệ thống: `{total_jobs}`\n"
        f"• Video do bạn tạo: `{my_jobs}`\n"
        f"• Trạng thái API: Sẵn sàng 🟢"
    )
    await update.message.reply_text(text, parse_mode="Markdown")
