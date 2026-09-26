from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config.settings import settings

def get_channel_selection_keyboard(active_channel_id: str = None) -> InlineKeyboardMarkup:
    """Tạo bàn phím nút bấm chọn kênh YouTube mục tiêu và quản lý kênh"""
    channels = settings.get_channels_config()
    keyboard = []

    for ch in channels:
        ch_id = ch.get("id", "")
        ch_name = ch.get("name", ch_id)
        is_active = (ch_id == active_channel_id)
        active_mark = "✓ " if is_active else ""

        # Nút chọn kênh & nút quản lý bên cạnh
        keyboard.append([
            InlineKeyboardButton(
                f"{active_mark}📺 {ch_name}",
                callback_data=f"select_channel:{ch_id}"
            ),
            InlineKeyboardButton(
                "⚙️ Tùy Chỉnh",
                callback_data=f"manage_channel:{ch_id}"
            )
        ])

    # Hàng tiện ích: Thêm kênh & Làm mới
    keyboard.append([
        InlineKeyboardButton("➕ Thêm Kênh Mới", callback_data="prompt_add_channel"),
        InlineKeyboardButton("🔄 Làm Mới", callback_data="refresh_channels")
    ])

    return InlineKeyboardMarkup(keyboard)

def get_channel_manage_keyboard(channel_id: str, is_active: bool = False) -> InlineKeyboardMarkup:
    """Bàn phím quản lý cấu hình chi tiết của một kênh"""
    keyboard = []

    if not is_active:
        keyboard.append([
            InlineKeyboardButton("🎯 Chọn Làm Kênh Đang Sử Dụng", callback_data=f"select_channel:{channel_id}")
        ])

    keyboard.extend([
        [
            InlineKeyboardButton("✏️ Đổi Tên Kênh", callback_data=f"prompt_rename:{channel_id}"),
            InlineKeyboardButton("🎯 Đổi Chủ Đề", callback_data=f"pick_niche:{channel_id}")
        ],
        [
            InlineKeyboardButton("🎙️ Đổi Giọng Đọc AI", callback_data=f"pick_voice:{channel_id}"),
            InlineKeyboardButton("🌐 Đổi Ngôn Ngữ", callback_data=f"pick_lang:{channel_id}")
        ],
        [
            InlineKeyboardButton("🎨 Phong Cách (Imagen / Pexels)", callback_data=f"pick_visual:{channel_id}")
        ],
        [
            InlineKeyboardButton("🗑️ Xóa Kênh Này", callback_data=f"confirm_del_channel:{channel_id}"),
            InlineKeyboardButton("⬅️ Quay Lại Danh Sách", callback_data="back_channels")
        ]
    ])
    return InlineKeyboardMarkup(keyboard)

def get_niche_selection_keyboard(channel_id: str) -> InlineKeyboardMarkup:
    """Bàn phím 1-Click chọn chủ đề kênh (Niche)"""
    niches = [
        ("🧠 Tâm lý học & Hành vi", "psychology"),
        ("📜 Lịch sử & Bí ẩn nhân loại", "history_mystery"),
        ("🌌 Vũ trụ & Khoa học kỳ thú", "space_science"),
        ("💰 Kinh tế & Tài chính thị trường", "finance"),
        ("💪 Động lực & Triết lý sống", "philosophy"),
        ("🤖 Công nghệ & Trí tuệ nhân tạo (AI)", "technology"),
        ("👻 Bí ẩn & Kỳ lạ thế giới", "mystery_horror")
    ]
    keyboard = []
    for label, niche_val in niches:
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"set_niche:{channel_id}:{niche_val}")
        ])
    keyboard.append([
        InlineKeyboardButton("⬅️ Quay Lại", callback_data=f"manage_channel:{channel_id}")
    ])
    return InlineKeyboardMarkup(keyboard)

def get_add_channel_templates_keyboard() -> InlineKeyboardMarkup:
    """Bàn phím thêm kênh nhanh 1-Click từ mẫu có sẵn hoặc tự gõ tên"""
    keyboard = [
        [
            InlineKeyboardButton("🤖 Kênh Công Nghệ AI (Tiếng Việt)", callback_data="add_tpl:tech_vi")
        ],
        [
            InlineKeyboardButton("💰 Kênh Kinh Tế & Tiền Tệ (Tiếng Việt)", callback_data="add_tpl:finance_vi")
        ],
        [
            InlineKeyboardButton("🧠 Kênh Dark Psychology (English)", callback_data="add_tpl:psychology_en")
        ],
        [
            InlineKeyboardButton("🌌 Kênh Khám Phá Vũ Trụ (Tiếng Việt)", callback_data="add_tpl:space_vi")
        ],
        [
            InlineKeyboardButton("✍️ Tự Nhập Tên Kênh Theo Ý Muốn", callback_data="prompt_custom_channel")
        ],
        [
            InlineKeyboardButton("⬅️ Hủy / Quay Lại", callback_data="back_channels")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_voice_selection_keyboard(channel_id: str) -> InlineKeyboardMarkup:
    """Bàn phím chọn giọng đọc Edge-TTS"""
    voices = [
        ("🇺🇸 Christopher (Nam - Trầm, cuốn hút)", "en-US-ChristopherNeural"),
        ("🇺🇸 Guy (Nam - Sâu sắc, tự tin)", "en-US-GuyNeural"),
        ("🇺🇸 Jenny (Nữ - Truyền cảm hứng)", "en-US-JennyNeural"),
        ("🇻🇳 Nam Minh (Nam - Giọng chuẩn truyền thông)", "vi-VN-NamMinhNeural"),
        ("🇻🇳 Hoài My (Nữ - Ngọt ngào, kịch tính)", "vi-VN-HoaiMyNeural"),
    ]
    keyboard = []
    for label, voice_key in voices:
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"set_voice:{channel_id}:{voice_key}")
        ])
    keyboard.append([
        InlineKeyboardButton("⬅️ Quay Lại", callback_data=f"manage_channel:{channel_id}")
    ])
    return InlineKeyboardMarkup(keyboard)

def get_lang_selection_keyboard(channel_id: str) -> InlineKeyboardMarkup:
    """Bàn phím chọn ngôn ngữ"""
    keyboard = [
        [
            InlineKeyboardButton("🇺🇸 English (en)", callback_data=f"set_lang:{channel_id}:en"),
            InlineKeyboardButton("🇻🇳 Tiếng Việt (vi)", callback_data=f"set_lang:{channel_id}:vi"),
        ],
        [
            InlineKeyboardButton("⬅️ Quay Lại", callback_data=f"manage_channel:{channel_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_visual_selection_keyboard(channel_id: str) -> InlineKeyboardMarkup:
    """Bàn phím chọn Visual Mode"""
    keyboard = [
        [
            InlineKeyboardButton("✨ Google Imagen 3 (Điện ảnh AI)", callback_data=f"set_visual:{channel_id}:imagen_ai"),
        ],
        [
            InlineKeyboardButton("🎞️ Pexels HD B-Roll (Thực tế)", callback_data=f"set_visual:{channel_id}:pexels_stock"),
        ],
        [
            InlineKeyboardButton("⬅️ Quay Lại", callback_data=f"manage_channel:{channel_id}")
        ]
    ]
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

def get_publish_channel_keyboard(job_id: int) -> InlineKeyboardMarkup:
    """Tạo bàn phím nút bấm chọn kênh YouTube mục tiêu khi xuất bản video"""
    import os
    channels = settings.get_channels_config()
    accounts = settings.get_accounts_config()
    acc_map = {a.get("id"): a.get("email_label") or a.get("email") or a.get("id") for a in accounts}
    keyboard = []

    for ch in channels:
        ch_id = ch.get("id", "")
        ch_name = ch.get("name", ch_id)
        acc_id = ch.get("google_account", "account_1")
        acc_email = acc_map.get(acc_id, acc_id)
        token_path = os.path.join(settings.CREDENTIALS_DIR, "accounts", acc_id, "youtube_token.json")
        auth_status = "🟢" if os.path.exists(token_path) else "⚠️"

        keyboard.append([
            InlineKeyboardButton(
                f"{auth_status} {ch_name} [{acc_email}]",
                callback_data=f"publish_to_channel:{job_id}:{ch_id}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("❌ Hủy Đăng", callback_data=f"cancel_publish:{job_id}")
    ])
    return InlineKeyboardMarkup(keyboard)


def get_schedule_channel_keyboard(job_id: int) -> InlineKeyboardMarkup:
    """Tạo bàn phím nút bấm chọn kênh khi lên lịch tự động theo khung giờ vàng tối ưu"""
    import os
    channels = settings.get_channels_config()
    accounts = settings.get_accounts_config()
    acc_map = {a.get("id"): a.get("email_label") or a.get("email") or a.get("id") for a in accounts}
    keyboard = []

    for ch in channels:
        ch_id = ch.get("id", "")
        ch_name = ch.get("name", ch_id)
        acc_id = ch.get("google_account", "account_1")
        acc_email = acc_map.get(acc_id, acc_id)
        token_path = os.path.join(settings.CREDENTIALS_DIR, "accounts", acc_id, "youtube_token.json")
        auth_status = "🟢" if os.path.exists(token_path) else "⚠️"

        keyboard.append([
            InlineKeyboardButton(
                f"{auth_status} {ch_name} [{acc_email}]",
                callback_data=f"schedule_to_channel:{job_id}:{ch_id}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("❌ Hủy Lên Lịch", callback_data=f"cancel_publish:{job_id}")
    ])
    return InlineKeyboardMarkup(keyboard)

