import os
from typing import Optional, List, Dict, Any
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
        ("🇺🇸 Andrew (Nam - Truyền cảm, tự nhiên)", "en-US-AndrewNeural"),
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
            InlineKeyboardButton("🤖 AI Tự Quyết Định (Khuyên dùng - Đạo diễn thông minh)", callback_data=f"set_visual:{channel_id}:auto"),
        ],
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

def get_script_review_keyboard(
    job_id: int,
    current_lang: Optional[str] = None,
    is_rendering: bool = False,
    video_format: str = "short",
) -> InlineKeyboardMarkup:
    """Bàn phím Bước 1: Khi kịch bản vừa tạo xong, tinh gọn, chuẩn SaaS cao cấp (hỗ trợ 1-Click chuyển Ngắn 9:16 <-> Dài 16:9)"""
    if current_lang and current_lang.lower().startswith("vi"):
        switch_btn = InlineKeyboardButton("🇺🇸 Đổi Sang Tiếng Anh", callback_data=f"switch_lang:{job_id}:en")
    elif current_lang and current_lang.lower().startswith("en"):
        switch_btn = InlineKeyboardButton("🇻🇳 Đổi Sang Tiếng Việt", callback_data=f"switch_lang:{job_id}:vi")
    else:
        switch_btn = InlineKeyboardButton("🌐 Đổi Ngôn Ngữ (VI/EN)", callback_data=f"switch_lang:{job_id}:toggle")

    vf_norm = "long" if str(video_format or "short").lower() in ("long", "16:9", "documentary") else "short"
    if vf_norm == "long":
        format_btn = InlineKeyboardButton("📱 Chuyển Sang Video Ngắn (9:16 Shorts)", callback_data=f"switch_format:{job_id}:short")
    else:
        format_btn = InlineKeyboardButton("🎬 Chuyển Sang Video Dài (16:9 ~4 Phút)", callback_data=f"switch_format:{job_id}:long")

    if is_rendering:
        render_btn = InlineKeyboardButton(
            f"⏳ Đang dựng Video #{job_id}... (Gõ /queue)",
            callback_data=f"render_video_locked:{job_id}",
        )
    else:
        mode_tag = "16:9 DÀI" if vf_norm == "long" else "9:16 SHORTS"
        render_btn = InlineKeyboardButton(
            f"🎬 DUYỆT & DỰNG VIDEO NGAY ({mode_tag})",
            callback_data=f"render_video:{job_id}",
        )

    keyboard = [
        [render_btn],
        [format_btn],
        [
            InlineKeyboardButton("✍️ Góp Ý Sửa", callback_data=f"edit_script:{job_id}"),
            switch_btn,
        ],
        [
            InlineKeyboardButton("🔄 Tạo Lại Kịch Bản", callback_data=f"regenerate:{job_id}"),
            InlineKeyboardButton("❌ Hủy Bỏ", callback_data=f"cancel:{job_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

get_video_review_keyboard = get_script_review_keyboard

def get_video_post_render_keyboard(
    job_id: int,
    is_republish: bool = False,
    affiliate_url: Optional[str] = None,
    affiliate_name: Optional[str] = None
) -> InlineKeyboardMarkup:
    """Bàn phím sau khi video hoàn thành: Đăng luôn, Đăng theo lịch, Sửa, Xóa và Link Tiếp Thị Sản Phẩm"""
    # Nếu chưa truyền affiliate_url, tự động tìm sản phẩm tối ưu từ DB
    if not affiliate_url:
        try:
            from app.database.models import get_db, VideoJob
            from app.services.monetization_engine import find_best_amazon_product, build_amazon_url
            with get_db() as db:
                jb = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if jb:
                    prod = find_best_amazon_product(jb.target_channel_id or "", jb.title or "", jb.topic or "", jb.script or "")
                    affiliate_url = build_amazon_url(prod["asin"])
                    affiliate_name = prod.get("name")
        except Exception:
            pass

    pub_label = "🔄 Đăng Lại Bản Mới (Shorts)" if is_republish else "🚀 Đăng Ngay (Shorts)"
    keyboard = []

    # Hàng 1: Nút Mua Sản Phẩm Tiếp Thị (Affiliate URL Button)
    if affiliate_url:
        btn_label = f"🛒 Mua Ngay: {affiliate_name[:26]}..." if affiliate_name else "🛒 Mua Sản Phẩm Đề Xuất (Affiliate)"
        keyboard.append([
            InlineKeyboardButton(btn_label, url=affiliate_url)
        ])

    # Hàng 2: Đăng Luôn & Đăng Theo Lịch
    keyboard.append([
        InlineKeyboardButton(pub_label, callback_data=f"publish_youtube:{job_id}"),
        InlineKeyboardButton("⏰ Đăng Theo Lịch", callback_data=f"schedule_youtube:{job_id}"),
    ])

    # Hàng 3: Sửa Video & Xóa Video
    keyboard.append([
        InlineKeyboardButton("✏️ Sửa Video", callback_data=f"feedback_video:{job_id}"),
        InlineKeyboardButton("🗑️ Xóa Video", callback_data=f"delete_video:{job_id}"),
    ])

    return InlineKeyboardMarkup(keyboard)

def get_video_edit_options_keyboard(job_id: int) -> InlineKeyboardMarkup:
    """Bàn phím menu chọn nhanh khi bấm 'Góp Ý Chỉnh Sửa & Dựng Lại'"""
    keyboard = [
        [
            InlineKeyboardButton("🚀 Đăng Lên YouTube Shorts Ngay", callback_data=f"publish_youtube:{job_id}"),
            InlineKeyboardButton("⏰ Lên Lịch Tự Động (Shorts)", callback_data=f"schedule_youtube:{job_id}"),
        ],
        [
            InlineKeyboardButton("✏️ Đổi Tiêu Đề Video", callback_data=f"prompt_edit_title:{job_id}"),
        ],
        [
            InlineKeyboardButton("🇺🇸 Chuyển Sang Tiếng Anh", callback_data=f"edit_lang:{job_id}:en"),
            InlineKeyboardButton("🇻🇳 Chuyển Sang Tiếng Việt", callback_data=f"edit_lang:{job_id}:vi"),
        ],
        [
            InlineKeyboardButton("🎙️ Đổi Giọng Nam (Mỹ/Việt)", callback_data=f"edit_voice:{job_id}:male"),
            InlineKeyboardButton("🎙️ Đổi Giọng Nữ (Mỹ/Việt)", callback_data=f"edit_voice:{job_id}:female"),
        ],
        [
            InlineKeyboardButton("🔄 Render Lại Toàn Bộ", callback_data=f"render_video:{job_id}"),
            InlineKeyboardButton("🗑️ Xóa Video Này", callback_data=f"delete_video:{job_id}"),
        ],
        [
            InlineKeyboardButton("⬅️ Đóng Menu", callback_data=f"cancel_edit:{job_id}"),
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
    """Tạo bàn phím nút bấm chọn kênh YouTube mục tiêu kèm hiển thị hạn mức Quota thời gian thực"""
    import os
    from app.services.quota_manager import QuotaManager
    channels = settings.get_channels_config()
    accounts = settings.get_accounts_config()
    acc_map = {a.get("id"): a.get("email_label") or a.get("email") or a.get("id") for a in accounts}
    keyboard = []

    channel_items = []
    for ch in channels:
        ch_id = ch.get("id", "")
        info = QuotaManager.get_channel_quota_info(ch_id)
        channel_items.append((ch, info))

    # Ưu tiên các kênh đang có sẵn Quota và đã cấp quyền lên đầu danh sách
    channel_items.sort(key=lambda x: (1 if x[1]["can_upload"] else 0, x[1]["remaining_units"]), reverse=True)

    for ch, info in channel_items:
        ch_id = ch.get("id", "")
        ch_name = ch.get("name", ch_id)
        acc_id = ch.get("google_account", "account_1")
        acc_email = acc_map.get(acc_id, acc_id)

        if info["can_upload"]:
            label = f"🟢⚡ {ch_name} [{info['remaining_units']:,} Quota]"
        elif not info["has_token"]:
            label = f"⚠️ {ch_name} [Chưa kết nối OAuth]"
        else:
            label = f"🔴⏳ {ch_name} [Hết Quota - Chờ 14:00]"

        keyboard.append([
            InlineKeyboardButton(
                label,
                callback_data=f"publish_to_channel:{job_id}:{ch_id}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("❌ Hủy Đăng", callback_data=f"cancel_publish:{job_id}")
    ])
    return InlineKeyboardMarkup(keyboard)


def get_quota_failover_keyboard(job_id: int, current_channel_id: str) -> InlineKeyboardMarkup:
    """Tạo bàn phím tự động xoay kênh thông minh (1-Click Failover) khi kênh hiện tại hết Quota API"""
    from app.services.quota_manager import QuotaManager
    available_channels = QuotaManager.get_available_channels(exclude_channel_id=current_channel_id)
    keyboard = []

    for ch in available_channels:
        cid = ch["channel_id"]
        cname = ch["channel_name"]
        rem = ch["remaining_units"]
        keyboard.append([
            InlineKeyboardButton(
                f"🚀 Xoay & Đăng Sang: {cname} ({rem:,} Quota)",
                callback_data=f"rotate_publish_to:{job_id}:{cid}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⏰ Chờ Nạp Lại Quota (Tự Đăng Lúc 14:00)",
            callback_data=f"wait_quota_job:{job_id}:{current_channel_id}"
        )
    ])
    keyboard.append([
        InlineKeyboardButton("❌ Hủy Đăng", callback_data=f"cancel_publish:{job_id}")
    ])
    return InlineKeyboardMarkup(keyboard)


def get_pre_publish_advisory_keyboard(job_id: int, channel_id: str, next_slot_label: str) -> InlineKeyboardMarkup:
    """Tạo bàn phím tư vấn cảnh báo rủi ro & đề xuất phương án trước khi xuất bản Shorts"""
    slot_text = f"⏰ Đăng Vào Giờ Vàng ({next_slot_label})" if next_slot_label else "⏰ Đăng Vào Giờ Vàng Khuyến Nghị"
    keyboard = [
        [
            InlineKeyboardButton(
                "⚡ Vẫn Đăng Ngay Lên Kênh Này",
                callback_data=f"force_publish_to_channel:{job_id}:{channel_id}"
            )
        ],
        [
            InlineKeyboardButton(
                slot_text,
                callback_data=f"schedule_to_channel:{job_id}:{channel_id}"
            )
        ],
        [
            InlineKeyboardButton("🔄 Đổi Sang Kênh Khác", callback_data=f"pick_publish_channel:{job_id}"),
            InlineKeyboardButton("✍️ Sửa Video", callback_data=f"feedback_video:{job_id}"),
        ],
        [
            InlineKeyboardButton("❌ Hủy Đăng", callback_data=f"cancel_publish:{job_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_schedule_channel_keyboard(job_id: int) -> InlineKeyboardMarkup:
    """Tạo bàn phím nút bấm chọn kênh khi lên lịch tự động theo khung giờ vàng Shorts tối ưu"""
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
        ch_token = os.path.join(settings.CREDENTIALS_DIR, "channels", ch_id, "youtube_token.json")
        auth_status = "🟢" if os.path.exists(ch_token) else "⚠️"

        keyboard.append([
            InlineKeyboardButton(
                f"{auth_status} ⚡ Shorts: {ch_name} [{acc_email}]",
                callback_data=f"schedule_to_channel:{job_id}:{ch_id}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("❌ Hủy Lên Lịch", callback_data=f"cancel_publish:{job_id}")
    ])
    return InlineKeyboardMarkup(keyboard)


def get_viral_ideas_keyboard(ideas: list, channel_id: str = "ch_psychology_of_the_world") -> InlineKeyboardMarkup:
    """Bàn phím 1-Click chọn ý tưởng triệu view để sản xuất kịch bản ngay lập tức"""
    keyboard = []
    for idx, idea in enumerate(ideas):
        topic_short = idea.get("topic", f"Ý tưởng {idx+1}")
        if len(topic_short) > 34:
            topic_short = topic_short[:32] + ".."
        keyboard.append([
            InlineKeyboardButton(
                f"🎬 #{idx+1}: {topic_short}",
                callback_data=f"pick_viral_topic:{idx}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton("🔄 Sinh 5 Ý Tưởng Khác", callback_data=f"refresh_viral_ideas:{channel_id}"),
        InlineKeyboardButton("❌ Đóng", callback_data="close_viral")
    ])
    return InlineKeyboardMarkup(keyboard)


def get_videos_list_keyboard(
    jobs_with_files: list,
    current_page: int = 1,
    total_pages: int = 1
) -> InlineKeyboardMarkup:
    """
    Tạo bàn phím hiển thị danh sách video:
    - Nút bấm tải / xem trực tiếp cho từng video có sẵn file
    - Nút phân trang (Trang trước, Trang sau)
    - Nút làm mới và tạo video mới
    """
    keyboard = []

    # 1. Các nút xem / tải video (sắp xếp tối đa 2 nút trên 1 hàng)
    download_buttons = []
    for item in jobs_with_files:
        j_id = item["id"]
        title_snip = item.get("title", f"#{j_id}")
        if len(title_snip) > 18:
            title_snip = title_snip[:16] + ".."
        btn_text = f"📥 Xem #{j_id}: {title_snip}"
        download_buttons.append(
            InlineKeyboardButton(btn_text, callback_data=f"send_video_file:{j_id}")
        )

    for i in range(0, len(download_buttons), 2):
        keyboard.append(download_buttons[i:i+2])

    # 2. Hàng phân trang
    nav_row = []
    if current_page > 1:
        nav_row.append(
            InlineKeyboardButton("⬅️ Trước", callback_data=f"videos_page:{current_page - 1}")
        )
    nav_row.append(
        InlineKeyboardButton(f"📄 Trang {current_page}/{total_pages}", callback_data=f"refresh_videos:{current_page}")
    )
    if current_page < total_pages:
        nav_row.append(
            InlineKeyboardButton("Sau ➡️", callback_data=f"videos_page:{current_page + 1}")
        )
    if nav_row:
        keyboard.append(nav_row)

    # 3. Tiện ích: Làm mới
    keyboard.append([
        InlineKeyboardButton("🔄 Làm Mới Danh Sách", callback_data=f"refresh_videos:{current_page}")
    ])

    return InlineKeyboardMarkup(keyboard)


def get_schedule_keyboard(channel_id: str) -> InlineKeyboardMarkup:
    """Bàn phím tương tác xem và quản lý lịch đăng khe giờ vàng"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Làm Mới Lịch", callback_data=f"refresh_schedule:{channel_id}"),
            InlineKeyboardButton("📺 Đổi Kênh", callback_data="refresh_channels")
        ],
        [
            InlineKeyboardButton("🎬 Xem Danh Sách Video", callback_data="videos_page:1"),
            InlineKeyboardButton("⏳ Hàng Đợi Render", callback_data="refresh_pending")
        ]
    ])


def get_delete_confirmation_keyboard(job_id: int) -> InlineKeyboardMarkup:
    """Bàn phím xác nhận xóa video vĩnh viễn khỏi Database và ổ cứng"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑️ XÁC NHẬN XÓA", callback_data=f"confirm_del_vid:{job_id}"),
            InlineKeyboardButton("❌ HỦY BỎ", callback_data=f"cancel_del_vid:{job_id}")
        ]
    ])


def get_delete_failed_confirmation_keyboard(count: int) -> InlineKeyboardMarkup:
    """Bàn phím xác nhận xóa hàng loạt tất cả video lỗi"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🗑️ XÓA HẾT {count} VIDEO LỖI", callback_data="confirm_del_failed"),
            InlineKeyboardButton("❌ HỦY BỎ", callback_data="cancel_del_vid:0")
        ]
    ])


def get_autopilot_keyboard(summary: Dict[str, Any]) -> InlineKeyboardMarkup:
    """Bàn phím điều khiển hệ thống Auto-Pilot A-Z cho các kênh"""
    keyboard = []
    empty_channels = summary.get("empty_channels", [])
    all_channels = summary.get("channels", [])

    # 1. Hàng ưu tiên: Bật cho tất cả kênh trống
    if empty_channels:
        keyboard.append([
            InlineKeyboardButton(
                f"🔥 Bật Auto-Pilot Tất Cả Kênh Trống ({len(empty_channels)} Kênh)",
                callback_data="autopilot_run_all"
            )
        ])

    # 2. Hàng từng kênh trống
    for ech in empty_channels:
        cid = ech["channel_id"]
        cname = ech["channel_name"].split(" - ")[0][:20]
        keyboard.append([
            InlineKeyboardButton(f"🚀 Chạy Auto-Pilot: {cname}", callback_data=f"autopilot_run_channel:{cid}")
        ])

    # 3. Kênh đã có 1 video nhưng chưa đủ 2 video tối đa/ngày
    can_add_more = [c for c in all_channels if c["today_video_count"] == 1 and not c.get("pending_job")]
    if can_add_more and not empty_channels:
        for c in can_add_more:
            cid = c["channel_id"]
            cname = c["channel_name"].split(" - ")[0][:20]
            keyboard.append([
                InlineKeyboardButton(f"➕ Thêm Video #2: {cname}", callback_data=f"autopilot_run_channel:{cid}")
            ])

    # 4. Hàng chỉ định chọn kênh bất kỳ
    keyboard.append([
        InlineKeyboardButton("🎯 Chỉ Định Chọn Kênh...", callback_data="autopilot_pick_channel")
    ])

    # 5. Hàng tiện ích
    keyboard.append([
        InlineKeyboardButton("🔄 Làm Mới Trạng Thái", callback_data="autopilot_status"),
        InlineKeyboardButton("📅 Xem Lịch Đăng", callback_data="refresh_schedule:default")
    ])

    return InlineKeyboardMarkup(keyboard)


def get_autopilot_channel_picker_keyboard() -> InlineKeyboardMarkup:
    """Bàn phím cho phép người dùng chỉ định chọn bất kỳ kênh nào trong danh sách để chạy Auto-Pilot A-Z"""
    from config.settings import settings
    channels = settings.get_channels_config()
    keyboard = []
    for idx, ch in enumerate(channels, 1):
        cid = ch.get("id", "")
        cname = ch.get("name", cid)
        handle = ch.get("handle", "")
        short_name = cname.split(" - ")[0][:22]
        label = f"{idx}. 🎯 {short_name}"
        if handle:
            label += f" ({handle})"
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"autopilot_run_channel:{cid}")
        ])
    keyboard.append([
        InlineKeyboardButton("🔙 Quay Lại Bảng Auto-Pilot", callback_data="autopilot_status")
    ])
    return InlineKeyboardMarkup(keyboard)



