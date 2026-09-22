from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config.settings import settings

def get_channel_selection_keyboard() -> InlineKeyboardMarkup:
    """Tạo bàn phím nút bấm chọn kênh YouTube mục tiêu"""
    channels = settings.get_channels_config()
    keyboard = []
    for ch in channels:
        btn_text = f"📺 {ch.get('name')} ({ch.get('niche')})"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"select_channel:{ch.get('id')}")])
    return InlineKeyboardMarkup(keyboard)

def get_video_review_keyboard(job_id: int) -> InlineKeyboardMarkup:
    """Tạo bàn phím nút bấm duyệt video khi render xong"""
    keyboard = [
        [
            InlineKeyboardButton("🚀 Đăng Ngay", callback_data=f"publish_now:{job_id}"),
            InlineKeyboardButton("⏰ Lên Lịch Tự Động", callback_data=f"schedule:{job_id}"),
        ],
        [
            InlineKeyboardButton("🔄 Tạo Lại Kịch Bản", callback_data=f"regenerate:{job_id}"),
            InlineKeyboardButton("❌ Hủy Bỏ", callback_data=f"cancel:{job_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)
