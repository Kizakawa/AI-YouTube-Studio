from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from app.services.channel_manager import channel_manager

def get_channel_selection_keyboard() -> InlineKeyboardMarkup:
    """Tạo bàn phím nút bấm chọn kênh YouTube mục tiêu kèm trạng thái kết nối"""
    channels = channel_manager.list_channels()
    keyboard = []
    for ch in channels:
        status_icon = "🟢" if ch.get("is_authenticated") else "🔴"
        btn_text = f"{status_icon} {ch.get('name')} ({ch.get('niche')})"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"select_channel:{ch.get('id')}")])
    
    # Nút phụ trợ
    keyboard.append([
        InlineKeyboardButton("🌐 Mở Web Quản Lý Kênh", url="http://localhost:8000"),
        InlineKeyboardButton("🔄 Làm Mới Trạng Thái", callback_data="refresh_channels")
    ])
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
