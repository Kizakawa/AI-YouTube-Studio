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

def get_script_review_keyboard(job_id: int) -> InlineKeyboardMarkup:
    """Bàn phím Bước 1: Khi kịch bản vừa tạo xong, nhóm duyệt kịch bản trước khi render"""
    keyboard = [
        [
            InlineKeyboardButton("🎬 Duyệt Kịch Bản & Dựng Video", callback_data=f"render_video:{job_id}"),
        ],
        [
            InlineKeyboardButton("✍️ Góp Ý Sửa Kịch Bản", callback_data=f"edit_script:{job_id}"),
            InlineKeyboardButton("🔄 Tạo Lại", callback_data=f"regenerate:{job_id}"),
        ],
        [
            InlineKeyboardButton("❌ Hủy Bỏ", callback_data=f"cancel:{job_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# Backward-compatible alias
get_video_review_keyboard = get_script_review_keyboard


def get_video_post_render_keyboard(job_id: int) -> InlineKeyboardMarkup:
    """Bàn phím Bước 2: Khi video đã render xong và gửi vào nhóm, chọn Đăng luôn hoặc Góp ý sửa lại"""
    keyboard = [
        [
            InlineKeyboardButton("🚀 Đăng Lên YouTube Ngay", callback_data=f"publish_youtube:{job_id}"),
        ],
        [
            InlineKeyboardButton("✍️ Góp Ý Chỉnh Sửa & Dựng Lại", callback_data=f"feedback_video:{job_id}"),
            InlineKeyboardButton("⏰ Lên Lịch Tự Động", callback_data=f"schedule_youtube:{job_id}"),
        ],
        [
            InlineKeyboardButton("❌ Hủy Video", callback_data=f"cancel:{job_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_chat_brainstorm_keyboard(session_id: str) -> InlineKeyboardMarkup:
    """Tạo bàn phím nút bấm thao tác khi đang thảo luận kịch bản với AI"""
    keyboard = [
        [
            InlineKeyboardButton("🎬 Chốt Kịch Bản Này & Sản Xuất", callback_data=f"convert_chat:{session_id}"),
        ],
        [
            InlineKeyboardButton("💡 Gợi Ý 3 Góc Nhìn Khác", callback_data=f"suggest_more:{session_id}"),
            InlineKeyboardButton("🔥 Đổi 3 Câu Hook Mới", callback_data=f"change_hooks:{session_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

