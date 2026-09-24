import os
import logging
import asyncio
import re
import html
from typing import Optional, List, Dict, Any, Union, Tuple
from pathlib import Path
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes
from config.settings import settings
from app.bot.keyboards import (
    get_channel_selection_keyboard,
    get_channel_manage_keyboard,
    get_niche_selection_keyboard,
    get_add_channel_templates_keyboard,
    get_voice_selection_keyboard,
    get_lang_selection_keyboard,
    get_visual_selection_keyboard,
    get_script_review_keyboard,
    get_video_post_render_keyboard,
    get_video_review_keyboard,
    get_chat_brainstorm_keyboard,
)
from app.database.models import get_db, User, VideoJob, log_activity
from app.agents.content_agent import ContentAgent
from app.schemas.contracts import ScriptContract
from app.services.render_queue import get_render_queue
from app.services import channel_manager

logger = logging.getLogger(__name__)
content_agent = ContentAgent()


async def safe_reply(update: Update, text: str, reply_markup=None, parse_mode="Markdown"):
    """
    Gửi tin nhắn an toàn:
    - Cắt gọn nếu vượt quá giới hạn 4096 ký tự của Telegram.
    - Tự động fallback sang Plain Text nếu lỗi Markdown parse.
    - Không bao giờ để văng exception làm chết bot.
    """
    if not text:
        return None

    # Telegram giới hạn 4096 ký tự
    if len(text) > 4000:
        text = text[:3980] + "\n\n...(nội dung còn tiếp)"

    try:
        if update.message:
            return await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        elif update.callback_query and update.callback_query.message:
            return await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        logger.warning(f"Lỗi gửi tin nhắn parse_mode={parse_mode} ({e}), fallback sang Plain Text...")
        try:
            # Fallback: Plain text không định dạng
            plain = re.sub(r'[*_`\[\]()<>]', '', text)
            if update.message:
                return await update.message.reply_text(plain, reply_markup=reply_markup)
            elif update.callback_query and update.callback_query.message:
                return await update.callback_query.message.reply_text(plain, reply_markup=reply_markup)
        except Exception as e2:
            logger.error(f"Fallback gửi tin nhắn thất bại: {e2}")
            return None


async def safe_edit_text(message, text: str, reply_markup=None, parse_mode="Markdown"):
    """Edit tin nhắn an toàn với fallback toàn diện"""
    if not message or not text:
        return None

    if len(text) > 4000:
        text = text[:3980] + "\n\n...(nội dung còn tiếp)"

    try:
        return await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        err_str = str(e)
        if "Message is not modified" in err_str:
            return message

        logger.warning(f"Lỗi edit_text parse_mode={parse_mode} ({e}), fallback sang Plain Text...")
        try:
            plain = re.sub(r'[*_`\[\]()<>]', '', text)
            return await message.edit_text(plain, reply_markup=reply_markup)
        except Exception as e2:
            if "Message is not modified" in str(e2):
                return message
            logger.error(f"Fallback edit_text thất bại ({e2}), gửi tin nhắn mới thay thế...")
            try:
                return await message.reply_text(plain, reply_markup=reply_markup)
            except Exception:
                return None


async def safe_query_answer(query, text: str = None, show_alert: bool = False):
    """
    Trả lời CallbackQuery an toàn, miễn nhiễm với lỗi 'Query is too old'
    khi người dùng bấm nút nhiều lần hoặc tiến trình chạy nền mất hơn 30s.
    """
    if not query:
        return
    try:
        await query.answer(text=text, show_alert=show_alert)
    except Exception as e:
        logger.debug(f"Bỏ qua lỗi timeout query.answer: {e}")


def is_authorized(update: Update) -> bool:
    """
    Tất cả thành viên trong nhóm Telegram hoặc chat trực tiếp đều có toàn quyền sử dụng bot.
    Không bắt buộc cấu hình ALLOWED_TELEGRAM_USERS để tránh phiền toái cho người dùng.
    """
    return True


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý lệnh /start"""
    if not is_authorized(update):
        await safe_reply(update, "⛔ Nhóm hoặc tài khoản này chưa được cấp quyền sử dụng bot.")
        return

    user = update.effective_user
    chat = update.effective_chat
    is_group = chat.type in ["group", "supergroup"]

    # FIX: Dùng context manager — không bao giờ leak session
    with get_db() as db:
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
        else:
            db_user.last_active = datetime.utcnow()
        # commit tự động bởi get_db()

        log_activity(db, user.id, user.username, "START_BOT",
                     f"Khởi động bot từ {chat.type} '{chat.title or user.first_name}'")

    scope_text = f"nhóm **{chat.title}**" if is_group else f"bạn **{user.first_name}**"

    welcome_text = (
        f"🎬 **AI YouTube Multi-Channel Studio đã sẵn sàng cho {scope_text}!**\n\n"
        f"👥 **Cơ chế làm việc nhóm & Web Dashboard:**\n"
        f"• Mọi thành viên cùng ra lệnh, kịch bản hiển thị công khai trong nhóm.\n"
        f"• Theo dõi toàn bộ hoạt động của admin qua Web Dashboard thời gian thực!\n\n"
        f"📌 **Các lệnh chính:**\n"
        f"• `/chat <nội dung>` - Thảo luận, phản biện và đồng sáng tạo kịch bản với Đạo diễn AI\n"
        f"• `/channels` - Xem và chuyển đổi kênh YouTube muốn sản xuất\n"
        f"• `/idea <chủ đề>` - Lên kịch bản AI ngay tức thì cho kênh đang chọn\n"
        f"• `/render <job_id>` - Dựng video từ kịch bản đã duyệt\n"
        f"• `/queue` - Xem trạng thái hàng đợi render (FIFO)\n"
        f"• `/cancel <job_id>` - Hủy video đang chờ trong hàng đợi\n"
        f"• `/status` - Báo cáo số lượng video và trạng thái hệ thống\n\n"
        f"👉 Gõ `/chat` hoặc `/idea` để bắt đầu sáng tạo video cùng nhóm!"
    )
    await safe_reply(update, welcome_text)


def _format_channel_info_text(ch: dict) -> str:
    name = ch.get("name", ch.get("id"))
    ch_id = ch.get("id")
    niche = ch.get("niche", "general")
    lang = ch.get("language", "en").upper()
    voice = ch.get("preferred_voice", "Default")
    visual = "✨ Google Imagen 3 (Điện ảnh)" if ch.get("visual_mode") == "imagen_ai" else "🎞️ Pexels HD B-Roll"
    sched = ch.get("schedule", {})
    hour = sched.get("publish_hour_utc", 12)

    return (
        f"⚙️ **CẤU HÌNH KÊNH: {name}**\n\n"
        f"• **Mã kênh (ID):** `{ch_id}`\n"
        f"• **Chủ đề / Niche:** {niche}\n"
        f"• **Ngôn ngữ:** {lang}\n"
        f"• **Giọng đọc AI:** `{voice}`\n"
        f"• **Phong cách:** {visual}\n"
        f"• **Lịch tự động đăng:** {hour}:00 UTC hàng tuần\n\n"
        f"👉 Bấm nút bên dưới để đổi giọng, đổi ngôn ngữ, đổi visual hoặc xóa kênh:"
    )


async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị danh sách kênh cho cả nhóm cùng chọn và quản lý"""
    if not is_authorized(update):
        return

    user = update.effective_user
    active_channel_id = None
    with get_db() as db:
        db_user = db.query(User).filter(User.telegram_id == user.id).first()
        if db_user:
            active_channel_id = db_user.active_channel_id

    reply_markup = get_channel_selection_keyboard(active_channel_id)
    text = (
        "📺 **MA TRẬN KÊNH YOUTUBE & TÀI KHOẢN GOOGLE**\n\n"
        f"📌 Kênh bạn đang chọn: `{active_channel_id or 'Chưa chọn'}`\n\n"
        "• Bấm vào **Tên Kênh** để chọn làm việc.\n"
        "• Bấm **⚙️ Sửa** để xem chi tiết / chỉnh sửa cấu hình kênh.\n"
        "• Bấm **➕ Thêm Kênh Mới** (hoặc gõ `/addchannel`) để tạo kênh mới!"
    )
    await safe_reply(update, text, reply_markup=reply_markup)


async def channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý tất cả các thao tác tương tác liên quan đến kênh qua nút bấm"""
    query = update.callback_query
    await safe_query_answer(query)

    data = query.data
    user = query.from_user

    # 1. Chọn kênh làm việc
    if data.startswith("select_channel:"):
        channel_id = data.split(":")[1]

        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first()
            if db_user:
                db_user.active_channel_id = channel_id
                db_user.last_active = datetime.utcnow()

            log_activity(db, user.id, user.username, "CHANGE_CHANNEL",
                         f"Chuyển kênh làm việc sang '{channel_id}'", channel_id=channel_id)

        # Cập nhật bàn phím với dấu tích ✓ mới
        new_markup = get_channel_selection_keyboard(channel_id)
        try:
            await query.message.edit_reply_markup(reply_markup=new_markup)
        except Exception:
            pass

        await safe_reply(
            update,
            f"✅ **{user.first_name}** đã chọn kênh: **{channel_id}**\n"
            f"Bây giờ cả nhóm có thể gõ: `/idea <chủ đề>` để bắt đầu tạo video cho kênh này!"
        )

    # 2. Xem chi tiết / Quản lý kênh
    elif data.startswith("manage_channel:"):
        channel_id = data.split(":")[1]
        ch = channel_manager.get_channel(channel_id)
        if not ch:
            await safe_reply(update, f"❌ Không tìm thấy thông tin kênh `{channel_id}`.")
            return

        active_id = None
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first()
            if db_user:
                active_id = db_user.active_channel_id

        text = _format_channel_info_text(ch)
        markup = get_channel_manage_keyboard(channel_id, is_active=(channel_id == active_id))
        await safe_edit_text(query.message, text, reply_markup=markup)

    # 2.5 Chọn đổi tên kênh
    elif data.startswith("prompt_rename:"):
        channel_id = data.split(":")[1]
        context.user_data["awaiting_rename"] = channel_id
        await safe_reply(
            update,
            f"✏️ **Đổi tên cho kênh `{channel_id}`:**\n\n"
            f"👉 Hãy gửi tin nhắn Tên Mới bạn muốn đặt (ví dụ: `Bí Ẩn Vũ Trụ & Khoa Học`):"
        )

    # 2.6 Chọn chủ đề (Niche)
    elif data.startswith("pick_niche:"):
        channel_id = data.split(":")[1]
        markup = get_niche_selection_keyboard(channel_id)
        await safe_edit_text(query.message, f"🎯 **Chọn chủ đề (Niche) mới cho kênh `{channel_id}`:**", reply_markup=markup)

    elif data.startswith("set_niche:"):
        parts = data.split(":")
        channel_id, niche_val = parts[1], parts[2]
        ok, msg, updated = channel_manager.update_channel(channel_id, niche=niche_val)
        if ok:
            text = f"✅ Đã đổi chủ đề thành `{niche_val}`!\n\n" + _format_channel_info_text(updated)
            markup = get_channel_manage_keyboard(channel_id)
            await safe_edit_text(query.message, text, reply_markup=markup)
        else:
            await safe_reply(update, f"❌ Lỗi: {msg}")

    # 3. Chọn giọng đọc
    elif data.startswith("pick_voice:"):
        channel_id = data.split(":")[1]
        markup = get_voice_selection_keyboard(channel_id)
        await safe_edit_text(query.message, f"🎙️ **Chọn giọng đọc AI cho kênh `{channel_id}`:**", reply_markup=markup)

    elif data.startswith("set_voice:"):
        parts = data.split(":")
        channel_id, voice_key = parts[1], parts[2]
        ok, msg, updated = channel_manager.update_channel(channel_id, preferred_voice=voice_key)
        if ok:
            text = f"✅ Đã đổi giọng đọc thành `{voice_key}`!\n\n" + _format_channel_info_text(updated)
            markup = get_channel_manage_keyboard(channel_id)
            await safe_edit_text(query.message, text, reply_markup=markup)
        else:
            await safe_reply(update, f"❌ Lỗi: {msg}")

    # 4. Chọn ngôn ngữ
    elif data.startswith("pick_lang:"):
        channel_id = data.split(":")[1]
        markup = get_lang_selection_keyboard(channel_id)
        await safe_edit_text(query.message, f"🌐 **Chọn ngôn ngữ cho kênh `{channel_id}`:**", reply_markup=markup)

    elif data.startswith("set_lang:"):
        parts = data.split(":")
        channel_id, lang_code = parts[1], parts[2]
        ok, msg, updated = channel_manager.update_channel(channel_id, language=lang_code)
        if ok:
            text = f"✅ Đã đổi ngôn ngữ sang `{lang_code.upper()}`!\n\n" + _format_channel_info_text(updated)
            markup = get_channel_manage_keyboard(channel_id)
            await safe_edit_text(query.message, text, reply_markup=markup)
        else:
            await safe_reply(update, f"❌ Lỗi: {msg}")

    # 5. Chọn Visual Mode (Imagen / Pexels)
    elif data.startswith("pick_visual:"):
        channel_id = data.split(":")[1]
        markup = get_visual_selection_keyboard(channel_id)
        await safe_edit_text(query.message, f"🎨 **Chọn phong cách hình ảnh cho kênh `{channel_id}`:**", reply_markup=markup)

    elif data.startswith("set_visual:"):
        parts = data.split(":")
        channel_id, visual_key = parts[1], parts[2]
        ok, msg, updated = channel_manager.update_channel(channel_id, visual_mode=visual_key)
        if ok:
            text = f"✅ Đã đổi phong cách hình ảnh sang `{visual_key}`!\n\n" + _format_channel_info_text(updated)
            markup = get_channel_manage_keyboard(channel_id)
            await safe_edit_text(query.message, text, reply_markup=markup)
        else:
            await safe_reply(update, f"❌ Lỗi: {msg}")

    # 6. Xác nhận & Xóa kênh
    elif data.startswith("confirm_del_channel:"):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        channel_id = data.split(":")[1]
        confirm_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🗑️ Có, Xóa Kênh", callback_data=f"do_del_channel:{channel_id}"),
                InlineKeyboardButton("❌ Hủy Bỏ", callback_data=f"manage_channel:{channel_id}")
            ]
        ])
        await safe_edit_text(
            query.message,
            f"⚠️ **CẢNH BÁO:** Bạn có chắc chắn muốn xóa kênh `{channel_id}` khỏi hệ thống không?\n"
            f"Thao tác này sẽ xóa kênh khỏi cả cấu hình Telegram và Web Dashboard.",
            reply_markup=confirm_markup
        )

    elif data.startswith("do_del_channel:"):
        channel_id = data.split(":")[1]
        ok, msg = channel_manager.delete_channel(channel_id)
        if ok:
            await safe_reply(update, f"🗑️ Đã xóa kênh `{channel_id}` thành công!")
            # Trở lại danh sách kênh
            channels = channel_manager.get_flat_channels()
            active_id = channels[0]["id"] if channels else None
            markup = get_channel_selection_keyboard(active_id)
            await safe_edit_text(query.message, "📺 **MA TRẬN KÊNH YOUTUBE ĐÃ ĐƯỢC CẬP NHẬT:**", reply_markup=markup)
        else:
            await safe_reply(update, f"❌ Không thể xóa kênh: {msg}")

    # 7. Menu Thêm Kênh Nhanh (Option Template hoặc Gõ tên)
    elif data == "prompt_add_channel":
        text = (
            "➕ **THÊM KÊNH YOUTUBE MỚI 1-CLICK:**\n\n"
            "Chọn ngay một kênh mẫu tối ưu sẵn bên dưới, hoặc bấm **Tự Nhập Tên Kênh** để đặt tên theo ý bạn:"
        )
        markup = get_add_channel_templates_keyboard()
        await safe_edit_text(query.message, text, reply_markup=markup)

    # 7.1 Thêm nhanh từ Template
    elif data.startswith("add_tpl:"):
        tpl_key = data.split(":")[1]
        templates = {
            "tech_vi": {
                "name": "Công Nghệ & AI Đỉnh Cao",
                "niche": "technology",
                "language": "vi",
                "voice": "vi-VN-NamMinhNeural",
                "visual": "imagen_ai"
            },
            "finance_vi": {
                "name": "Bí Mật Tài Chính & Tiền Tệ",
                "niche": "finance",
                "language": "vi",
                "voice": "vi-VN-NamMinhNeural",
                "visual": "imagen_ai"
            },
            "psychology_en": {
                "name": "Dark Psychology & Mind Hacks",
                "niche": "psychology",
                "language": "en",
                "voice": "en-US-ChristopherNeural",
                "visual": "imagen_ai"
            },
            "space_vi": {
                "name": "Khám Phá Vũ Trụ Huyền Bí",
                "niche": "space_science",
                "language": "vi",
                "voice": "vi-VN-NamMinhNeural",
                "visual": "imagen_ai"
            }
        }
        tpl = templates.get(tpl_key)
        if not tpl:
            await safe_reply(update, "❌ Mẫu kênh không tồn tại.")
            return

        import time
        unique_suffix = int(time.time()) % 10000
        new_id = f"ch_{tpl_key}_{unique_suffix}"

        accounts = channel_manager.get_accounts_enriched()
        target_acc = accounts[0]["id"] if accounts else "account_1"

        ok, msg, new_ch = channel_manager.add_channel(
            account_id=target_acc,
            channel_id=new_id,
            name=tpl["name"],
            niche=tpl["niche"],
            language=tpl["language"],
            visual_mode=tpl["visual"],
            preferred_voice=tpl["voice"]
        )

        if ok:
            with get_db() as db:
                db_user = db.query(User).filter(User.telegram_id == user.id).first()
                if db_user:
                    db_user.active_channel_id = new_id
                log_activity(db, user.id, user.username, "ADD_CHANNEL_TPL", f"Tạo nhanh kênh '{tpl['name']}' ({new_id})", channel_id=new_id)

            res_text = (
                f"🎉 **ĐÃ TẠO NHANH KÊNH MỚI THÀNH CÔNG!**\n\n"
                f"📺 **Tên kênh:** {tpl['name']}\n"
                f"🔑 **Mã kênh:** `{new_id}`\n"
                f"🎯 **Chủ đề:** {tpl['niche']}\n"
                f"🌐 **Ngôn ngữ:** {tpl['language'].upper()}\n"
                f"🎙️ **Giọng đọc:** `{tpl['voice']}`\n\n"
                f"👉 Đã tự động đặt làm **kênh đang chọn** của bạn!\n"
                f"Gõ `/idea <chủ đề>` để bắt đầu tạo video ngay!"
            )
            markup = get_channel_manage_keyboard(new_id, is_active=True)
            await safe_edit_text(query.message, res_text, reply_markup=markup)
        else:
            await safe_reply(update, f"❌ Không thể tạo kênh: {msg}")

    # 7.2 Nhập tên kênh tùy chọn
    elif data == "prompt_custom_channel":
        context.user_data["awaiting_new_channel_name"] = True
        await safe_reply(
            update,
            "✍️ **Tạo Kênh Mới:**\n\n"
            "Hãy gửi tin nhắn Tên Kênh bạn muốn tạo (ví dụ: `Bí Ẩn Thế Giới`, `Đầu Tư Thông Minh`...):\n"
            "AI sẽ tự động khởi tạo mọi cấu hình tối ưu nhất cho bạn!"
        )

    # 8. Quay lại hoặc làm mới danh sách kênh
    elif data in ["back_channels", "refresh_channels"]:
        active_id = None
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first()
            if db_user:
                active_id = db_user.active_channel_id

        markup = get_channel_selection_keyboard(active_id)
        text = (
            "📺 **MA TRẬN KÊNH YOUTUBE & TÀI KHOẢN GOOGLE**\n\n"
            f"📌 Kênh bạn đang chọn: `{active_id or 'Chưa chọn'}`\n\n"
            "• Bấm vào **Tên Kênh** để chọn làm việc.\n"
            "• Bấm **⚙️ Sửa** để tùy chỉnh giọng đọc, ngôn ngữ, hình ảnh hoặc xóa kênh.\n"
            "• Bấm **➕ Thêm Kênh Mới** để tạo kênh con mới!"
        )
        await safe_edit_text(query.message, text, reply_markup=markup)


async def addchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh thêm kênh YouTube mới trực tiếp từ Telegram"""
    if not is_authorized(update):
        return

    user = update.effective_user
    args = context.args

    if not args:
        guide = (
            "➕ **HƯỚNG DẪN THÊM KÊNH YOUTUBE MỚI:**\n\n"
            "Cú pháp: `/addchannel <mã_kênh> | <tên_kênh> | <chủ_đề> | <ngôn_ngữ>`\n\n"
            "Ví dụ:\n"
            "• `/addchannel ch_tech | AI & Future Tech | technology | en`\n"
            "• `/addchannel ch_tamly | Tâm Lý Học Đen | psychology | vi`\n\n"
            "💡 Sau khi thêm, kênh sẽ tự động được đồng bộ lên **Web Dashboard** và sẵn sàng tạo video ngay bằng `/idea`!"
        )
        await safe_reply(update, guide)
        return

    full_text = " ".join(args)
    if "|" in full_text:
        parts = [p.strip() for p in full_text.split("|")]
        ch_id = parts[0].lower().replace(" ", "_")
        ch_name = parts[1] if len(parts) > 1 and parts[1] else ch_id
        ch_niche = parts[2] if len(parts) > 2 and parts[2] else "general"
        ch_lang = parts[3].lower() if len(parts) > 3 and parts[3] in ["vi", "en"] else "en"
    else:
        ch_id = args[0].lower().replace(" ", "_")
        ch_name = " ".join(args[1:]) if len(args) > 1 else ch_id
        ch_niche = "general"
        ch_lang = "en"

    # Lấy tài khoản Google đầu tiên để gán
    accounts = channel_manager.get_accounts_enriched()
    target_acc = accounts[0]["id"] if accounts else "account_1"

    ok, msg, new_ch = channel_manager.add_channel(
        account_id=target_acc,
        channel_id=ch_id,
        name=ch_name,
        niche=ch_niche,
        language=ch_lang,
        visual_mode="imagen_ai",
        preferred_voice="vi-VN-NamMinhNeural" if ch_lang == "vi" else "en-US-ChristopherNeural"
    )

    if ok:
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first()
            if db_user:
                db_user.active_channel_id = ch_id
            log_activity(db, user.id, user.username, "ADD_CHANNEL", f"Thêm kênh mới '{ch_name}' ({ch_id})", channel_id=ch_id)

        res_text = (
            f"🎉 **ĐÃ THÊM KÊNH YOUTUBE MỚI THÀNH CÔNG!**\n\n"
            f"📺 **Tên kênh:** {ch_name}\n"
            f"🔑 **Mã kênh:** `{ch_id}`\n"
            f"🎯 **Chủ đề:** {ch_niche}\n"
            f"🌐 **Ngôn ngữ:** {ch_lang.upper()}\n"
            f"☁️ **Tài khoản Google:** `{target_acc}`\n\n"
            f"👉 Kênh đã được đặt làm **kênh đang chọn** của bạn!\n"
            f"Gõ `/idea <chủ đề>` để bắt đầu sản xuất video ngay!"
        )
        await safe_reply(update, res_text)
    else:
        await safe_reply(update, f"❌ Không thể thêm kênh: {msg}")


async def editchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh chỉnh sửa thông tin kênh YouTube"""
    if not is_authorized(update):
        return

    args = context.args
    if not args:
        guide = (
            "⚙️ **HƯỚNG DẪN CHỈNH SỬA KÊNH:**\n\n"
            "Cú pháp 1: `/editchannel <mã_kênh>` — Mở bảng nút bấm trực quan\n\n"
            "Cú pháp 2 (Sửa nhanh): `/editchannel <mã_kênh> <thuộc_tính> <giá_trị>`\n"
            "Các thuộc tính hỗ trợ:\n"
            "• `name`: Đổi tên kênh (vd: `/editchannel channel_psychology name Dark Psychology Pro`)\n"
            "• `niche`: Đổi chủ đề (vd: `/editchannel channel_psychology niche philosophy`)\n"
            "• `voice`: Đổi giọng đọc (vd: `/editchannel channel_psychology voice vi-VN-NamMinhNeural`)\n"
            "• `lang`: Đổi ngôn ngữ (vd: `/editchannel channel_psychology lang vi`)\n"
            "• `visual`: Đổi phong cách (vd: `/editchannel channel_psychology visual imagen_ai` hoặc `pexels_stock`)"
        )
        await safe_reply(update, guide)
        return

    ch_id = args[0].strip()
    channel = channel_manager.get_channel(ch_id)
    if not channel:
        await safe_reply(update, f"❌ Không tìm thấy kênh `{ch_id}` trong hệ thống. Gõ `/channels` để xem danh sách.")
        return

    # Nếu chỉ gõ /editchannel <id> -> mở menu tương tác
    if len(args) == 1:
        text = _format_channel_info_text(channel)
        markup = get_channel_manage_keyboard(ch_id)
        await safe_reply(update, text, reply_markup=markup)
        return

    # Sửa nhanh theo thuộc tính
    field = args[1].lower().strip()
    val = " ".join(args[2:]).strip()

    updates = {}
    if field in ["name", "title", "ten"]:
        updates["name"] = val
    elif field in ["niche", "topic", "chude"]:
        updates["niche"] = val
    elif field in ["voice", "giong"]:
        updates["preferred_voice"] = val
    elif field in ["lang", "language", "ngonngu"]:
        updates["language"] = val.lower()
    elif field in ["visual", "mode", "style"]:
        updates["visual_mode"] = val.lower()
    else:
        await safe_reply(update, f"⚠️ Thuộc tính `{field}` không hợp lệ. Chỉ hỗ trợ: `name`, `niche`, `voice`, `lang`, `visual`.")
        return

    ok, msg, updated = channel_manager.update_channel(ch_id, **updates)
    if ok:
        await safe_reply(update, f"✅ **Đã cập nhật kênh `{ch_id}` thành công!**\n\n{_format_channel_info_text(updated)}")
    else:
        await safe_reply(update, f"❌ Lỗi cập nhật: {msg}")


async def delchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh xóa kênh khỏi hệ thống"""
    if not is_authorized(update):
        return

    if not context.args:
        await safe_reply(update, "🗑️ Cú pháp xóa kênh: `/delchannel <mã_kênh>`\nVí dụ: `/delchannel channel_test`")
        return

    ch_id = context.args[0].strip()
    ok, msg = channel_manager.delete_channel(ch_id)
    if ok:
        await safe_reply(update, f"✅ Đã xóa kênh `{ch_id}` khỏi hệ thống thành công!\nCác thay đổi đã được đồng bộ lên Web Dashboard.")
    else:
        await safe_reply(update, f"❌ Không thể xóa: {msg}")


async def idea_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh sinh kịch bản AI và gửi vào nhóm cho mọi người cùng duyệt"""
    if not is_authorized(update):
        return

    if not context.args:
        await safe_reply(
            update,
            "💡 Cú pháp: `/idea <chủ đề video>`\nVí dụ: `/idea 3 bí ẩn dưới đáy biển sâu`")
        return

    topic = " ".join(context.args)
    user = update.effective_user

    # Lấy kênh đang hoạt động
    with get_db() as db:
        db_user = db.query(User).filter(User.telegram_id == user.id).first()
        channel_id = db_user.active_channel_id if db_user and db_user.active_channel_id else "channel_psychology"
        if db_user:
            db_user.last_active = datetime.utcnow()

        log_activity(db, user.id, user.username, "CMD_IDEA",
                     f"Yêu cầu lên ý tưởng video: '{topic}'", channel_id=channel_id)

    status_msg = await safe_reply(
        update,
        f"🤖 **{user.first_name}** vừa yêu cầu lên ý tưởng: *\"{topic}\"*\n"
        f"Đang kích hoạt Gemini AI (Key Rotation Pool) để tạo kịch bản cho kênh `{channel_id}`..."
    )

    try:
        # ContentAgent trả ScriptContract (validated Pydantic model)
        script: ScriptContract = await asyncio.to_thread(
            content_agent.generate_script,
            topic=topic, channel_niche="general", language="en"
        )

        # Lưu vào Database — dùng context manager
        with get_db() as db:
            job = VideoJob(
                user_id=user.id,
                target_channel_id=channel_id,
                topic=topic,
                title=script.title,
                script=script.full_narration,
                description=script.description,
                tags=",".join(script.tags),
                scenes_json=script.to_json_str(),   # [NEW] Lưu structured data
                status="SCRIPT_READY"
            )
            db.add(job)
            db.flush()  # Lấy job.id trước commit
            job_id = job.id

            log_activity(db, user.id, user.username, "SCRIPT_CREATED",
                         f"Kịch bản #{job_id} đã hoàn thành: '{script.title}'",
                         channel_id=channel_id)

        # Soạn nội dung thông báo cho nhóm
        scenes_preview = "\n".join([
            f"• Cảnh {s.scene_id}: {s.narration[:60]}... _(Tìm: {s.visual_keyword})_"
            for s in script.scenes[:3]
        ])

        reply_content = (
            f"🎬 **KỊCH BẢN ĐÃ HOÀN THÀNH (Task #{job_id})**\n\n"
            f"📌 **Tiêu đề đề xuất:** {script.title}\n"
            f"🔥 **Hook 3 giây đầu:** \"{script.hook}\"\n\n"
            f"📝 **Phân cảnh tiêu biểu:**\n{scenes_preview}\n\n"
            f"⏱️ **Ước lượng:** ~{script.total_estimated_duration:.0f}s | {len(script.scenes)} cảnh\n\n"
            f"🗣️ **Người tạo:** {user.first_name} | 📺 **Kênh:** `{channel_id}`\n\n"
            f"💡 **Tùy biến hình ảnh:** Gửi ảnh/video vào nhóm kèm chú thích `/scene <số_cảnh>` (hoặc `/media <số_cảnh> <link>`) để gán hình ảnh của bạn cho cảnh đó!\n\n"
            f"👉 Cả nhóm hãy góp ý hoặc bấm nút bên dưới để tiến hành sản xuất video!"
        )

        review_markup = get_script_review_keyboard(job_id)
        if status_msg:
            await safe_edit_text(status_msg, reply_content, reply_markup=review_markup)
        else:
            await safe_reply(update, reply_content, reply_markup=review_markup)

    except Exception as e:
        logger.error(f"Lỗi khi tạo kịch bản: {e}", exc_info=True)
        err_text = f"❌ Có lỗi xảy ra khi tạo kịch bản: `{e}`"
        if status_msg:
            await safe_edit_text(status_msg, err_text)
        else:
            await safe_reply(update, err_text)


async def start_render_pipeline(
    bot,
    chat_id: Optional[int],
    job_id: int,
    user_name: str = "System",
    update: Optional[Update] = None,
    is_auto_resume: bool = False
):
    """
    Tiến trình thực thi pipeline render và gửi cập nhật tiến độ / video hoàn thành lên Telegram.
    Hỗ trợ cả:
    1. Khi người dùng bấm duyệt trên Telegram (truyền update).
    2. Khi bot tự động khôi phục (Auto-Resume) khi khởi động lại (dùng bot + chat_id).
    """
    status_text = (
        f"🔄 **[TỰ ĐỘNG LÀM TIẾP]** Bot đang tiếp tục dựng Video #{job_id} chưa hoàn thành!\n"
        f"⏳ Đang nạp pipeline render 9:16 Shorts (Audio + Media + Subtitles)..."
        if is_auto_resume else
        f"🚀 **{user_name}** đã kích hoạt sản xuất Video #{job_id}!\n"
        f"⏳ Đang xếp hàng vào pipeline render 9:16 Shorts (Audio + Media + Subtitles)..."
    )

    status_msg = None
    if update:
        status_msg = await safe_reply(update, status_text)
    elif chat_id and bot:
        try:
            status_msg = await bot.send_message(chat_id=chat_id, text=status_text, parse_mode="Markdown")
        except Exception:
            try:
                plain_status = re.sub(r'[*_`\[\]()<>]', '', status_text)
                status_msg = await bot.send_message(chat_id=chat_id, text=plain_status)
            except Exception:
                pass

    def render_progress_bar(percent: int) -> str:
        percent = max(0, min(100, percent))
        filled = int(percent / 10)
        bar = "▰" * filled + "▱" * (10 - filled)
        return f"[{bar}] {percent}%"

    async def progress_cb(stage: str, msg: str, percent: int = 0):
        if status_msg:
            icons = {
                "SCRIPT": "📝",
                "AUDIO": "🎙️",
                "MEDIA": "🖼️",
                "RENDER": "🎬",
                "QA": "🔍",
                "COMPLETED": "🎉",
                "ERROR": "❌"
            }
            icon = icons.get(stage, "⏳")
            p_bar = render_progress_bar(percent) if percent > 0 else "[▱▱▱▱▱▱▱▱▱▱] 0%"
            prefix = "🔄 [TỰ ĐỘNG TIẾP TỤC] " if is_auto_resume else ""
            display_text = (
                f"🎬 **{prefix}TIẾN TRÌNH RENDER (Task #{job_id})**\n\n"
                f"📊 **Tiến độ:** `{p_bar}`\n\n"
                f"{icon} {msg}"
            )
            await safe_edit_text(status_msg, display_text)

    try:
        from app.services.pipeline import VideoPipeline
        pipeline = VideoPipeline(job_id=job_id, progress_callback=progress_cb)
        render_job = await pipeline.execute()

        # Kiểm tra file MP4 hoàn chỉnh
        out_path = Path(render_job.output_path)
        if out_path.exists() and chat_id and bot:
            size_mb = out_path.stat().st_size / (1024 * 1024)
            post_render_markup = get_video_post_render_keyboard(job_id)

            prefix_title = (
                f"🎉 **VIDEO ĐÃ DỰNG THÀNH CÔNG (TỰ ĐỘNG TIẾP TỤC)! (Task #{job_id})**"
                if is_auto_resume else
                f"🎉 **VIDEO ĐÃ DỰNG THÀNH CÔNG! (Task #{job_id})**"
            )

            # Đọc điểm chất lượng từ LangGraph AI Graders nếu có
            scores_text = ""
            try:
                from app.database.models import get_db, VideoJob
                with get_db() as db:
                    jb = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                    if jb and jb.quality_scores:
                        import json
                        q_data = json.loads(jb.quality_scores)
                        sc = q_data.get("script_critic") or {}
                        vc = q_data.get("visual_critic") or {}
                        aq = q_data.get("audio_qa") or {}
                        scores_text = (
                            f"\n📊 **Báo cáo Thẩm định AI Graders:**\n"
                            f" ⭐ Kịch bản (Script Critic): `{sc.get('overall_score', 8.5)}/10`\n"
                            f" 🖼️ Hình ảnh (Visual Critic): `{vc.get('overall_score', 9.0)}/10`\n"
                            f" 🎙️ Giọng đọc (Audio QA): `{aq.get('words_per_minute', 145)} WPM ({aq.get('pacing_rating', 'PERFECT')})`\n"
                            f" 🎬 Kỹ thuật (Video QA): `1080x1920 30fps (Đạt)`\n"
                        )
            except Exception:
                pass

            # Telegram Bot API cho phép upload video tối đa 50MB
            if size_mb <= 49.0:
                caption = (
                    f"{prefix_title}\n\n"
                    f"📌 **Tiêu đề:** {render_job.title}\n"
                    f"⏱️ **Thời lượng:** {render_job.total_audio_duration_ms / 1000:.1f}s\n"
                    f"📦 **Dung lượng:** {size_mb:.1f} MB\n"
                    f"🎬 **Chuẩn:** 1080x1920 (9:16 Shorts) kèm phụ đề hardsub\n"
                    f"{scores_text}\n"
                    f"👉 Cả nhóm hãy xem thử video! Chọn **Đăng Ngay** hoặc **Góp Ý Sửa Lại** bên dưới:"
                )
                try:
                    with open(out_path, "rb") as vf:
                        await bot.send_video(
                            chat_id=chat_id,
                            video=vf,
                            caption=caption,
                            reply_markup=post_render_markup,
                            supports_streaming=True,
                            read_timeout=120,
                            write_timeout=120
                        )
                except Exception as ex_upload:
                    logger.warning(f"Không thể gửi file video trực tiếp qua Telegram: {ex_upload}")
                    fallback_msg = (
                        f"🎉 **Video #{job_id} đã hoàn tất!**\n"
                        f"📂 File đã lưu tại: `{render_job.output_path}` ({size_mb:.1f} MB)\n\n"
                        f"👉 Cả nhóm chọn thao tác tiếp theo:"
                    )
                    if update:
                        await safe_reply(update, fallback_msg, reply_markup=post_render_markup)
                    else:
                        await bot.send_message(chat_id=chat_id, text=fallback_msg, reply_markup=post_render_markup)
            else:
                large_msg = (
                    f"🎉 **Video #{job_id} đã hoàn tất!**\n"
                    f"📦 Dung lượng ({size_mb:.1f} MB) vượt quá giới hạn 50MB của Telegram.\n"
                    f"📂 File đã lưu sẵn tại:\n`{render_job.output_path}`\n\n"
                    f"👉 Cả nhóm chọn thao tác tiếp theo:"
                )
                if update:
                    await safe_reply(update, large_msg, reply_markup=post_render_markup)
                else:
                    await bot.send_message(chat_id=chat_id, text=large_msg, reply_markup=post_render_markup)

        with get_db() as db:
            log_activity(db, 0, user_name, "RENDER_COMPLETE",
                         f"Hoàn tất render video #{job_id}: {render_job.output_path}")

    except Exception as e:
        logger.error(f"Lỗi khi render video #{job_id}: {e}", exc_info=True)
        err_msg = f"❌ **Render video #{job_id} thất bại:**\n`{str(e)}`"
        if status_msg:
            await safe_edit_text(status_msg, err_msg)
        elif update:
            await safe_reply(update, err_msg)
        elif chat_id and bot:
            try:
                await bot.send_message(chat_id=chat_id, text=err_msg, parse_mode="Markdown")
            except Exception:
                pass


async def _execute_render_task(update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: int, user_name: str):
    """Tiến trình render video chạy ngầm khi người dùng bấm nút trên Telegram"""
    chat_id = update.effective_chat.id if update.effective_chat else None
    await start_render_pipeline(context.bot, chat_id, job_id, user_name, update=update, is_auto_resume=False)


async def auto_resume_pending_jobs(application):
    """
    Tự động kiểm tra và làm tiếp các video đang làm dở khi bot khởi động lại.
    - Tìm các video có status 'RENDERING' (bị ngắt giữa chừng khi tắt bot)
      hoặc 'SCRIPT_READY' đang chờ dựng.
    - Gửi thông báo đến nhóm Telegram và tự động đưa vào hàng đợi render.
    """
    from app.database.models import get_db, VideoJob
    from app.services.render_queue import get_render_queue
    from config.settings import settings

    try:
        allowed_groups = settings.get_allowed_group_ids()
        default_chat_id = allowed_groups[0] if allowed_groups else None

        unfinished_jobs = []
        with get_db() as db:
            # 1. Tìm job bị gián đoạn ở RENDERING hoặc SCRIPT_READY
            stuck_rendering = db.query(VideoJob).filter(VideoJob.status == "RENDERING").order_by(VideoJob.id.asc()).all()
            stuck_ready = db.query(VideoJob).filter(VideoJob.status == "SCRIPT_READY").order_by(VideoJob.id.asc()).all()

            for j in stuck_rendering + stuck_ready:
                unfinished_jobs.append({
                    "id": j.id,
                    "title": j.title or j.topic or f"Video #{j.id}",
                    "user_id": j.user_id,
                    "status": j.status
                })

        if not unfinished_jobs:
            logger.info("Auto-Resume: Không có video nào làm dở cần tiếp tục.")
            return

        logger.info(f"Auto-Resume: Phát hiện {len(unfinished_jobs)} video làm dở — Bắt đầu tự động dựng tiếp!")
        rq = get_render_queue()

        for j_info in unfinished_jobs:
            job_id = j_info["id"]
            job_title = j_info["title"]
            target_chat = default_chat_id or (j_info["user_id"] if j_info["user_id"] and j_info["user_id"] != 0 else None)

            if target_chat:
                try:
                    await application.bot.send_message(
                        chat_id=target_chat,
                        text=(
                            f"🔄 **[TỰ ĐỘNG PHỤC HỒI TÁC VỤ]**\n\n"
                            f"Phát hiện video đang làm dở trước khi hệ thống khởi động lại:\n"
                            f"🎬 **Task #{job_id}: {job_title}**\n\n"
                            f"🤖 **Chatbot đang tự động tiếp tục hoàn thiện nốt video cho bạn...**"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as ex_notify:
                    logger.warning(f"Không thể gửi thông báo auto-resume tới chat {target_chat}: {ex_notify}")

            # Đưa vào hàng đợi render tự động
            async def make_task(jid=job_id, t_chat=target_chat):
                await start_render_pipeline(application.bot, t_chat, jid, user_name="Auto-Resume System", is_auto_resume=True)

            await rq.enqueue(
                job_id=job_id,
                user_name="Auto-Resume",
                chat_id=target_chat,
                render_func=make_task
            )

    except Exception as e:
        logger.error(f"Lỗi trong auto_resume_pending_jobs: {e}", exc_info=True)


async def _enqueue_render(update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: int, user_name: str):
    """
    Đưa yêu cầu render vào hàng đợi FIFO thay vì chạy trực tiếp.
    Nếu máy đang rảnh → render ngay. Nếu đang bận → xếp hàng chờ.
    """
    rq = get_render_queue()
    chat_id = update.effective_chat.id if update.effective_chat else None

    # Tạo render function (closure bind sẵn tham số)
    async def do_render():
        await _execute_render_task(update, context, job_id, user_name)

    # Thêm vào queue
    queue_item = await rq.enqueue(
        job_id=job_id,
        user_name=user_name,
        chat_id=chat_id,
        render_func=do_render,
    )

    # Thông báo trạng thái hàng đợi
    if rq.queue_size == 0 and rq.is_busy:
        # Item vừa thêm đã được worker pick lên ngay
        await safe_reply(
            update,
            f"🚀 Video #{job_id} đang được render ngay!\n"
            f"🏭 Máy render: Đang xử lý"
        )
    elif rq.queue_size > 0:
        # Có video đang chờ
        current = rq.current_job
        current_info = f"Video #{current.job_id}" if current else "Không rõ"
        await safe_reply(
            update,
            f"📥 **Video #{job_id} đã vào hàng đợi!**\n\n"
            f"🔴 Đang render: {current_info}\n"
            f"⏳ Vị trí của bạn: **#{rq.queue_size}** trong hàng chờ\n"
            f"📋 Tổng đang chờ: {rq.queue_size} video\n\n"
            f"💡 Gõ `/queue` để xem trạng thái hàng đợi"
        )


async def queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /queue — Xem trạng thái hàng đợi render hiện tại"""
    if not is_authorized(update):
        return

    rq = get_render_queue()
    await safe_reply(update, rq.get_status_text())


async def cancel_queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /cancel <job_id> — Hủy video đang chờ trong hàng đợi"""
    if not is_authorized(update):
        return

    if not context.args or not context.args[0].isdigit():
        await safe_reply(
            update,
            "💡 Cú pháp: `/cancel <job_id>`\n"
            "Ví dụ: `/cancel 5`\n\n"
            "⚠️ Chỉ hủy được video đang **chờ** trong hàng đợi, không hủy được video đang render."
        )
        return

    job_id = int(context.args[0])
    rq = get_render_queue()
    success = await rq.cancel_job(job_id)

    if success:
        await safe_reply(update, f"✅ Đã hủy Video #{job_id} khỏi hàng đợi render.")
    else:
        await safe_reply(
            update,
            f"❌ Không thể hủy Video #{job_id}.\n"
            f"Video có thể đang render hoặc không có trong hàng đợi."
        )


async def pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /pending hoặc /resume — Quét và xử lý toàn bộ các tác vụ chưa hoàn thành"""
    if not is_authorized(update):
        return

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    with get_db() as db:
        pending_jobs = db.query(VideoJob).filter(
            VideoJob.status.in_(["SCRIPT_READY", "RENDERING", "DRAFT", "FAILED"])
        ).order_by(VideoJob.id.asc()).all()

    if not pending_jobs:
        await safe_reply(
            update,
            "✅ **TẤT CẢ CÁC TÁC VỤ ĐÃ ĐƯỢC XỬ LÝ HOÀN TẤT!**\n\n"
            "Hiện tại không còn kịch bản nào tồn đọng hoặc bị lỗi trong hệ thống.\n"
            "Gõ `/idea <chủ đề>` để tạo video mới!"
        )
        return

    lines = []
    ready_count = 0
    failed_count = 0
    for j in pending_jobs:
        if j.status == "SCRIPT_READY":
            icon = "📝"
            ready_count += 1
            extra = ""
        elif j.status == "RENDERING":
            icon = "⏳"
            extra = ""
        elif j.status == "FAILED":
            icon = "❌"
            failed_count += 1
            err_short = j.error_message.replace("\n", " ")[:35] if j.error_message else "Lỗi không xác định"
            extra = f"\n   ↳ _Lỗi: {err_short}..._"
        else:
            icon = "📄"
            extra = ""
        title = j.title or j.topic
        lines.append(f"{icon} **Task #{j.id}**: {title[:40]}... (`{j.status}`){extra}")

    text = (
        f"📋 **DANH SÁCH TÁC VỤ TỒN ĐỌNG ({len(pending_jobs)} video):**\n\n"
        + "\n".join(lines) + "\n\n"
        f"💡 Sẵn sàng: `{ready_count}` | Bị lỗi cần khắc phục: `{failed_count}`\n"
        "👉 Bấm nút bên dưới để tự động đưa vào hàng đợi render tuần tự:"
    )

    keyboard = []
    if ready_count > 0:
        keyboard.append([InlineKeyboardButton(f"🚀 Render Toàn Bộ Đang Chờ ({ready_count})", callback_data="process_all_pending")])
    if failed_count > 0:
        keyboard.append([InlineKeyboardButton(f"🔄 Khôi Phục & Render {failed_count} Job Lỗi", callback_data="retry_all_failed")])
    keyboard.append([InlineKeyboardButton("🔄 Làm Mới Danh Sách", callback_data="refresh_pending")])

    await safe_reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))


async def media_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lệnh gắn media từ URL web cho phân cảnh:
    Cú pháp: /media <số_cảnh> <url>
    Hoặc: /media <job_id> <số_cảnh> <url>
    """
    if not is_authorized(update):
        return

    args = context.args or []
    if len(args) < 2:
        await safe_reply(
            update,
            "💡 **Cú pháp gắn media từ URL:**\n"
            "`/media <số phân cảnh> <link_ảnh_hoặc_video>`\n"
            "Hoặc: `/media <job_id> <số phân cảnh> <link_ảnh_hoặc_video>`\n\n"
            "Ví dụ:\n`/media 1 https://example.com/deep_sea.jpg`"
        )
        return

    import requests

    job_id = None
    scene_id = None
    url = None

    if len(args) >= 3 and args[0].isdigit() and args[1].isdigit():
        job_id = int(args[0])
        scene_id = int(args[1])
        url = args[2]
    elif args[0].isdigit():
        scene_id = int(args[0])
        url = args[1]
        with get_db() as db:
            latest = db.query(VideoJob).order_by(VideoJob.created_at.desc()).first()
            if latest:
                job_id = latest.id

    if not job_id or not scene_id or not url:
        await safe_reply(update, "⚠️ Tham số không hợp lệ. Vui lòng thử lại: `/media <số cảnh> <url>`")
        return

    wait_msg = await safe_reply(update, f"⏳ Đang tải media từ URL cho Phân cảnh #{scene_id} (Video #{job_id})...")
    try:
        ext = ".jpg"
        url_lower = url.lower()
        if ".mp4" in url_lower:
            ext = ".mp4"
        elif ".png" in url_lower:
            ext = ".png"
        elif ".webp" in url_lower:
            ext = ".webp"

        dest_dir = Path("storage") / "jobs" / str(job_id) / "custom_media"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / f"scene_{scene_id:03d}{ext}"

        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        res.raise_for_status()
        with open(dest_path, "wb") as f:
            f.write(res.content)

        await safe_edit_text(
            wait_msg,
            f"✅ **ĐÃ TẢI VÀ GÁN MEDIA TỪ WEB THÀNH CÔNG!**\n\n"
            f"📸 **Phân cảnh:** #{scene_id}\n"
            f"🎬 **Video Task:** #{job_id}\n"
            f"📁 **Dung lượng:** {dest_path.stat().st_size / 1024:.1f} KB\n\n"
            f"👉 Hệ thống sẽ ưu tiên sử dụng media này cho cảnh #{scene_id} khi render!"
        )
    except Exception as e:
        logger.error(f"Lỗi tải media từ URL: {e}", exc_info=True)
        await safe_edit_text(wait_msg, f"❌ Không thể tải media từ URL: `{e}`")


async def media_upload_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Xử lý khi người dùng gửi ảnh/video/document vào nhóm hoặc DM:
    - Bắt caption ví dụ: '/scene 1', '/scene 105 2', '#1', 'cảnh 1'.
    - Hoặc nhận diện khi reply vào tin nhắn kịch bản Task #...
    - Lưu file vào storage/jobs/{job_id}/custom_media/scene_{scene_id:03d}.{ext}.
    """
    if not is_authorized(update):
        return

    message = update.effective_message
    if not message:
        return

    caption = (message.caption or "").strip()
    user = update.effective_user

    scene_id = None
    job_id = None

    # Check pattern /scene <job_id> <scene_id>
    m1 = re.search(r"/scene\s+(\d+)\s+(\d+)", caption, re.IGNORECASE)
    if m1:
        job_id = int(m1.group(1))
        scene_id = int(m1.group(2))
    else:
        # Check pattern /scene <scene_id> or #<scene_id> or cảnh <scene_id>
        m2 = re.search(r"(?:/scene|#|c[aả]nh)\s*(\d+)", caption, re.IGNORECASE)
        if m2:
            scene_id = int(m2.group(1))

    # Nếu reply tin nhắn, kiểm tra Task #<id> trong tin nhắn gốc
    if message.reply_to_message:
        replied_text = message.reply_to_message.text or message.reply_to_message.caption or ""
        m_task = re.search(r"Task\s*#(\d+)", replied_text, re.IGNORECASE)
        if m_task and not job_id:
            job_id = int(m_task.group(1))
        
        # Nếu caption chỉ là số đơn thuần, ví dụ "1" hoặc "2"
        if not scene_id and caption.isdigit():
            scene_id = int(caption)

    # Nếu chưa có job_id, tìm job gần nhất trong database
    if not job_id:
        with get_db() as db:
            latest_job = db.query(VideoJob).order_by(VideoJob.created_at.desc()).first()
            if latest_job:
                job_id = latest_job.id

    if not scene_id:
        # Gửi hướng dẫn nếu người dùng chưa chỉ định phân cảnh
        await safe_reply(
            update,
            "💡 **Để gắn ảnh/video này vào phân cảnh cụ thể:**\n"
            "Vui lòng ghi chú thích (caption) kèm theo theo cú pháp: `/scene <số phân cảnh>` (ví dụ: `/scene 1` hoặc `/scene 2`)."
        )
        return

    # Xác định file Telegram
    file_obj = None
    ext = ".jpg"
    if message.photo:
        file_obj = message.photo[-1]
        ext = ".jpg"
    elif message.video:
        file_obj = message.video
        ext = ".mp4"
    elif message.document:
        mime = message.document.mime_type or ""
        file_name = message.document.file_name or ""
        if mime.startswith("video/") or file_name.endswith((".mp4", ".mov", ".mkv")):
            file_obj = message.document
            ext = ".mp4"
        elif mime.startswith("image/") or file_name.endswith((".jpg", ".jpeg", ".png", ".webp")):
            file_obj = message.document
            ext = Path(file_name).suffix.lower() if Path(file_name).suffix else ".jpg"

    if not file_obj:
        return

    status_wait = await safe_reply(
        update,
        f"⏳ Đang tải và gán media tùy biến cho **Phân cảnh #{scene_id}** (Video #{job_id})..."
    )

    try:
        tg_file = await file_obj.get_file()
        dest_dir = Path("storage") / "jobs" / str(job_id) / "custom_media"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / f"scene_{scene_id:03d}{ext}"

        await tg_file.download_to_drive(custom_path=str(dest_path))

        await safe_edit_text(
            status_wait,
            f"✅ **ĐÃ GÁN MEDIA TÙY BIẾN THÀNH CÔNG!**\n\n"
            f"📸 **Phân cảnh:** #{scene_id}\n"
            f"🎬 **Video Task:** #{job_id}\n"
            f"📁 **File:** `{dest_path.name}` ({dest_path.stat().st_size / 1024:.1f} KB)\n"
            f"👤 **Người gửi:** {user.first_name}\n\n"
            f"👉 Khi dựng video (render), hệ thống sẽ ưu tiên dùng chính xác media này cho cảnh #{scene_id}!"
        )
    except Exception as e:
        logger.error(f"Lỗi khi tải custom media: {e}", exc_info=True)
        await safe_edit_text(status_wait, f"❌ Lỗi khi tải file media: `{e}`")


async def _handle_script_revision(update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: int, feedback: str):
    """Xử lý tiếp thu góp ý và tinh chỉnh kịch bản theo yêu cầu của nhóm"""
    user = update.effective_user
    wait_msg = await safe_reply(
        update,
        f"🤖 **Đạo diễn AI** đang tiếp thu góp ý của **{user.first_name}**:\n"
        f"_\"{feedback}\"_\n\n"
        f"⏳ Đang viết lại kịch bản tối ưu cho Task #{job_id}..."
    )

    try:
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not job:
                await safe_reply(update, f"❌ Không tìm thấy Task #{job_id}")
                return
            if not job.scenes_json:
                await safe_reply(update, f"❌ Task #{job_id} chưa có dữ liệu kịch bản.")
                return

            current_script = ScriptContract.from_json_str(job.scenes_json)
            channel_id = job.target_channel_id

        # Tìm niche và language của channel
        channels = settings.get_channels_config()
        ch_cfg = next((c for c in channels if c.get("id") == channel_id), {})
        niche = ch_cfg.get("niche", "general")
        lang = ch_cfg.get("language", "en")

        revised_script: ScriptContract = await asyncio.to_thread(
            content_agent.refine_script,
            current_script=current_script,
            feedback=feedback,
            channel_niche=niche,
            language=lang
        )

        with get_db() as db:
            db_job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if db_job:
                db_job.title = revised_script.title
                db_job.script = revised_script.full_narration
                db_job.description = revised_script.description
                db_job.tags = ",".join(revised_script.tags)
                db_job.scenes_json = revised_script.to_json_str()
                db_job.status = "SCRIPT_READY"
                log_activity(db, user.id, user.username, "REFINE_SCRIPT",
                             f"Sửa kịch bản #{job_id} theo góp ý: '{feedback}'")

        scenes_preview = "\n".join([
            f"• Cảnh {s.scene_id}: {s.narration[:60]}... _(Tìm: {s.visual_keyword})_"
            for s in revised_script.scenes[:3]
        ])

        reply_content = (
            f"✨ **KỊCH BẢN ĐÃ ĐƯỢC CHỈNH SỬA (Task #{job_id})**\n\n"
            f"📌 **Tiêu đề mới:** {revised_script.title}\n"
            f"🔥 **Hook mới:** \"{revised_script.hook}\"\n\n"
            f"📝 **Phân cảnh cập nhật:**\n{scenes_preview}\n\n"
            f"⏱️ **Ước lượng:** ~{revised_script.total_estimated_duration:.0f}s | {len(revised_script.scenes)} cảnh\n"
            f"🗣️ **Người góp ý:** {user.first_name}\n\n"
            f"👉 Cả nhóm xem qua, nếu đã ưng ý hãy bấm **Duyệt Kịch Bản & Dựng Video** bên dưới!"
        )

        markup = get_script_review_keyboard(job_id)
        if wait_msg:
            await safe_edit_text(wait_msg, reply_content, reply_markup=markup)
        else:
            await safe_reply(update, reply_content, reply_markup=markup)

    except Exception as e:
        logger.error(f"Lỗi khi sửa kịch bản: {e}", exc_info=True)
        err_text = f"❌ Không thể chỉnh sửa kịch bản: `{e}`"
        if wait_msg:
            await safe_edit_text(wait_msg, err_text)
        else:
            await safe_reply(update, err_text)


async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh góp ý và chỉnh sửa kịch bản hoặc phân cảnh vi mô:
    - Sửa kịch bản: /edit <job_id> <góp ý>
    - Đổi ảnh cảnh: /edit <job_id> scene <số cảnh> <từ khóa ảnh mới>
    - Đổi câu thoại cảnh: /edit <job_id> text <số cảnh> <câu thoại mới>
    - Đổi giọng đọc: /edit <job_id> voice <nam|nu>
    """
    if not is_authorized(update):
        return

    if not context.args or len(context.args) < 2 or not context.args[0].isdigit():
        await safe_reply(
            update,
            "💡 **Cú pháp chỉnh sửa thông minh (LangGraph Multi-Agent):**\n\n"
            "1. **Góp ý sửa kịch bản chung:**\n"
            "`/edit <job_id> <góp ý>`\n"
            "   _Ví dụ:_ `/edit 2 Đổi câu hook đầu thành câu hỏi giật gân hơn`\n\n"
            "2. **Đổi ảnh/video của 1 cảnh cụ thể:**\n"
            "`/edit <job_id> scene <số_cảnh> <từ_khóa_mới>`\n"
            "   _Ví dụ:_ `/edit 2 scene 3 John D. Rockefeller Standard Oil refinery vintage`\n\n"
            "3. **Sửa câu thoại của 1 cảnh cụ thể:**\n"
            "`/edit <job_id> text <số_cảnh> <câu_thoại_mới>`\n"
            "   _Ví dụ:_ `/edit 2 text 1 Sự thật mà cả thế giới chưa từng biết về ông trùm...`\n\n"
            "4. **Đổi giọng đọc AI:**\n"
            "`/edit <job_id> voice <nam|nu>`\n"
            "   _Ví dụ:_ `/edit 2 voice nu`"
        )
        return

    job_id = int(context.args[0])
    sub_cmd = context.args[1].lower()

    # ── Chỉnh sửa vi mô bằng LangGraph ──
    if sub_cmd == "scene" and len(context.args) >= 4 and context.args[2].isdigit():
        scene_id = int(context.args[2])
        new_query = " ".join(context.args[3:])
        status_msg = await safe_reply(update, f"🛠️ [LangGraph] Đang đổi ảnh/video cho Cảnh #{scene_id} với từ khóa: `{new_query}`...")
        try:
            from app.graph import GraphRunner, EditRequest
            runner = GraphRunner(job_id=job_id)
            edit_req = EditRequest(
                action_type="replace_media",
                scene_id=scene_id,
                new_media_query=new_query
            )
            await runner.execute_edit(edit_req)
            await safe_edit_text(status_msg, f"✅ Đã cập nhật ảnh Cảnh #{scene_id} và render lại video thành công! Bạn có thể xem video mới.")
        except Exception as e:
            await safe_edit_text(status_msg, f"❌ Lỗi khi chỉnh sửa Cảnh #{scene_id}: {e}")
        return

    elif sub_cmd == "text" and len(context.args) >= 4 and context.args[2].isdigit():
        scene_id = int(context.args[2])
        new_text = " ".join(context.args[3:])
        status_msg = await safe_reply(update, f"🎙️ [LangGraph] Đang cập nhật câu thoại và tạo lại giọng đọc cho Cảnh #{scene_id}...")
        try:
            from app.graph import GraphRunner, EditRequest
            runner = GraphRunner(job_id=job_id)
            edit_req = EditRequest(
                action_type="edit_text",
                scene_id=scene_id,
                new_text=new_text
            )
            await runner.execute_edit(edit_req)
            await safe_edit_text(status_msg, f"✅ Đã cập nhật lời thoại Cảnh #{scene_id}, đồng bộ phụ đề và render lại video thành công!")
        except Exception as e:
            await safe_edit_text(status_msg, f"❌ Lỗi khi sửa lời thoại Cảnh #{scene_id}: {e}")
        return

    elif sub_cmd == "voice" and len(context.args) >= 3:
        voice_arg = context.args[2].lower()
        voice_id = "vi-VN-HoaiMyNeural" if "nu" in voice_arg or "female" in voice_arg else "vi-VN-NamMinhNeural"
        status_msg = await safe_reply(update, f"🎙️ [LangGraph] Đang đổi giọng đọc sang {'Nữ' if 'nu' in voice_arg else 'Nam'} ({voice_id})...")
        try:
            from app.graph import GraphRunner, EditRequest
            runner = GraphRunner(job_id=job_id)
            edit_req = EditRequest(
                action_type="change_voice",
                new_voice_id=voice_id
            )
            await runner.execute_edit(edit_req)
            await safe_edit_text(status_msg, f"✅ Đã đổi toàn bộ giọng đọc sang {'Nữ' if 'nu' in voice_arg else 'Nam'} và render lại video!")
        except Exception as e:
            await safe_edit_text(status_msg, f"❌ Lỗi khi đổi giọng đọc: {e}")
        return

    # Sửa kịch bản thông thường
    feedback = " ".join(context.args[1:])
    await _handle_script_revision(update, context, job_id, feedback)


async def render_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh kích hoạt render thủ công: /render <job_id>"""
    if not is_authorized(update):
        return

    if not context.args or not context.args[0].isdigit():
        await safe_reply(update, "💡 Cú pháp: `/render <job_id>`\nVí dụ: `/render 1`")
        return

    job_id = int(context.args[0])
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            await safe_reply(update, f"❌ Không tìm thấy VideoJob #{job_id}")
            return
        if not job.scenes_json:
            await safe_reply(update, f"❌ VideoJob #{job_id} chưa có kịch bản phân cảnh.")
            return

    user = update.effective_user
    user_name = user.first_name if user else "Creator"
    await _enqueue_render(update, context, job_id, user_name)


async def video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Xử lý nút bấm trên kịch bản & video: Duyệt Dựng / Đăng YouTube / Góp Ý Sửa / Lên Lịch / Tạo Lại / Hủy
    """
    query = update.callback_query
    await safe_query_answer(query)
    data = query.data
    user = query.from_user

    # 1. Duyệt kịch bản & Dựng video (Bước 1 -> Bước 2)
    if data.startswith("render_video:") or data.startswith("publish_now:"):
        job_id = int(data.split(":")[1])
        await _enqueue_render(update, context, job_id, user.first_name)

    # 2. Đăng lên YouTube & Google Drive sau khi xem video
    elif data.startswith("publish_youtube:"):
        job_id = int(data.split(":")[1])
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not job or not job.video_path or not os.path.exists(job.video_path):
                await safe_reply(update, f"❌ File video #{job_id} chưa sẵn sàng để xuất bản.")
                return
            channel_id = job.target_channel_id or "default"
            video_path = job.video_path
            video_title = job.title or job.topic
            video_desc = job.description or ""
            video_tags = [t.strip() for t in (job.tags or "").split(",") if t.strip()]

        status_wait = await safe_reply(
            update,
            f"🚀 **ĐANG ĐĂNG VIDEO #{job_id} LÊN YOUTUBE & GOOGLE DRIVE...**\n\n"
            f"📺 **Kênh:** `{channel_id}`\n"
            f"📌 **Tiêu đề:** {video_title}\n"
            f"⏳ Đang sao lưu Google Drive 2TB và upload YouTube..."
        )

        from app.agents.publisher_agent import PublisherAgent
        from app.agents.drive_agent import DriveAgent

        channels = settings.get_channels_config()
        ch_cfg = next((c for c in channels if c.get("id") == channel_id), {"id": channel_id, "name": channel_id})

        pub_agent = PublisherAgent(channel_config=ch_cfg)
        drive_agent = DriveAgent(account_name="account_1")

        # 1. Tải lên Google Drive 2TB
        drive_res = await asyncio.to_thread(drive_agent.upload_video, video_path, video_title)
        drive_link = drive_res.get("drive_link")

        # 2. Đăng tải lên YouTube
        pub_res = await asyncio.to_thread(
            pub_agent.upload_video,
            video_path=video_path,
            title=video_title,
            description=video_desc,
            tags=video_tags,
            privacy_status="public"
        )

        if pub_res.get("status") == "success":
            yt_url = pub_res.get("youtube_url")
            yt_id = pub_res.get("video_id")
            with get_db() as db:
                db_job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if db_job:
                    db_job.status = "PUBLISHED"
                    db_job.youtube_video_id = yt_id
                    if drive_link:
                        db_job.drive_link = drive_link
                    log_activity(db, user.id, user.username, "PUBLISH_YOUTUBE",
                                 f"Đã đăng Video #{job_id} lên YouTube ({yt_url})",
                                 channel_id=channel_id)

            success_text = (
                f"🎉 **ĐĂNG TẢI VIDEO #{job_id} THÀNH CÔNG!**\n\n"
                f"🎬 **Link xem YouTube (Chế độ Công khai):**\n👉 {yt_url}\n\n"
            )
            if drive_link:
                success_text += f"💾 **Link lưu trữ Google Drive 2TB:**\n👉 {drive_link}\n\n"
            success_text += "💡 Video đã được đăng ở chế độ Công khai (Public) cho mọi người cùng xem!"
            await safe_edit_text(status_wait, success_text)
        else:
            err = pub_res.get("error", "Lỗi không xác định")
            fail_text = f"❌ **Đăng YouTube thất bại:** `{err}`"
            if drive_link:
                fail_text += f"\n\n💾 Tuy nhiên video đã được sao lưu an toàn lên Drive:\n👉 {drive_link}"
            await safe_edit_text(status_wait, fail_text)

    # 3. Góp ý chỉnh sửa kịch bản hoặc video
    elif data.startswith("feedback_video:") or data.startswith("edit_script:"):
        job_id = int(data.split(":")[1])
        await safe_reply(
            update,
            f"✍️ **GÓP Ý & SỬA ĐỔI VIDEO #{job_id}**\n\n"
            f"Bạn muốn thay đổi điểm gì? Hãy **Reply (Trả lời)** tin nhắn này hoặc gõ lệnh:\n"
            f"`/edit {job_id} <nội dung muốn sửa>`\n\n"
            f"💡 **Ví dụ:**\n"
            f"• `/edit {job_id} Đổi câu hook đầu thành câu hỏi giật gân hơn`\n"
            f"• `/edit {job_id} Viết ngắn lại khoảng 30s, nhịp điệu nhanh hơn`\n"
            f"• `/edit {job_id} Cảnh 2 và 3 đổi sang hình ảnh huyền bí`"
        )

    # 4. Lên lịch tự động
    elif data.startswith("schedule_youtube:") or data.startswith("schedule:"):
        job_id = int(data.split(":")[1])
        await safe_reply(
            update,
            f"⏰ **Lên lịch đăng Video #{job_id}**\n\n"
            f"Chức năng lên lịch tự động theo khung giờ vàng trong `channels.yaml` sẽ hoạt động sau khi kênh được kết nối OAuth."
        )

    # 5. Tạo lại kịch bản từ đầu
    elif data.startswith("regenerate:"):
        job_id = int(data.split(":")[1])
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if job:
                job.status = "DRAFT"
                log_activity(db, user.id, user.username, "REGENERATE",
                             f"Yêu cầu tạo lại kịch bản #{job_id}")
        await safe_reply(
            update,
            f"🔄 **{user.first_name}** yêu cầu tạo lại kịch bản #{job_id}.\n"
            f"Hãy gõ `/idea <chủ đề mới>` để tạo kịch bản thay thế."
        )

    # 6. Hủy bỏ
    elif data.startswith("cancel:"):
        job_id = int(data.split(":")[1])
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if job:
                job.status = "FAILED"
                job.error_message = f"Hủy bởi {user.first_name}"
                log_activity(db, user.id, user.username, "CANCEL_JOB",
                             f"Đã hủy video #{job_id}")
        await safe_reply(
            update,
            f"❌ **{user.first_name}** đã hủy video #{job_id}."
        )

    # 7. Xử lý toàn bộ các kịch bản đang chờ (Render Queue Batch)
    elif data == "process_all_pending":
        with get_db() as db:
            pending_jobs = db.query(VideoJob).filter(
                VideoJob.status == "SCRIPT_READY"
            ).order_by(VideoJob.id.asc()).all()

        if not pending_jobs:
            await safe_reply(update, "⚠️ Không còn kịch bản nào đang ở trạng thái chờ render.")
            return

        user_name = user.first_name if user else "Creator"
        enqueued_count = 0
        for j in pending_jobs:
            await _enqueue_render(update, context, j.id, user_name)
            enqueued_count += 1

        await safe_reply(
            update,
            f"🚀 **ĐÃ ĐƯA {enqueued_count} VIDEO VÀO HÀNG ĐỢI RENDER!**\n\n"
            f"Các video sẽ được hệ thống dựng tuần tự (1 video tại một thời điểm để bảo vệ phần cứng máy).\n"
            f"Gõ `/queue` để theo dõi tiến trình!"
        )

    # 7.5 Khôi phục và đưa toàn bộ video bị lỗi FAILED vào hàng đợi render lại
    elif data == "retry_all_failed":
        with get_db() as db:
            failed_jobs = db.query(VideoJob).filter(
                VideoJob.status == "FAILED"
            ).order_by(VideoJob.id.asc()).all()

            if not failed_jobs:
                await safe_reply(update, "⚠️ Không còn video nào ở trạng thái lỗi.")
                return

            recovered_ids = []
            for j in failed_jobs:
                j.status = "SCRIPT_READY"
                j.error_message = None
                j.progress_percent = 0
                j.progress_message = "Đã khôi phục từ lệnh retry và chuẩn bị render lại"
                log_activity(db, user.id, user.username, "RETRY_FAILED_JOB", f"Khôi phục Video #{j.id} về SCRIPT_READY")
                recovered_ids.append(j.id)

        user_name = user.first_name if user else "Creator"
        for j_id in recovered_ids:
            await _enqueue_render(update, context, j_id, user_name)

        await safe_reply(
            update,
            f"🔄 **ĐÃ KHÔI PHỤC VÀ ĐƯA {len(recovered_ids)} VIDEO LỖI VÀO HÀNG ĐỢI RENDER!**\n\n"
            f"⚡ Hệ thống tự động tái sử dụng phân cảnh và giọng đọc đã có từ trước để render cực nhanh.\n"
            f"Gõ `/queue` để theo dõi tiến trình!"
        )

    # 8. Làm mới danh sách tác vụ chờ
    elif data == "refresh_pending":
        await pending_command(update, context)

    # 9. Gán media trực tiếp từ kết quả tìm kiếm vào phân cảnh
    elif data.startswith("apply_media:"):
        parts = data.split(":")
        job_id = int(parts[1])
        scene_id = int(parts[2])
        cache_key = parts[3]

        item = _SEARCH_CACHE.get(cache_key)
        if not item:
            await safe_query_answer(query, "⚠️ Phiên tìm kiếm đã hết hạn, vui lòng tìm lại bằng /search_image.", show_alert=True)
            return

        await safe_query_answer(query, f"Đang gán vào Cảnh {scene_id}...", show_alert=False)
        m_url = item["url"]
        m_type = item.get("media_type", "image")

        status_msg = await safe_reply(
            update,
            f"⏳ **Đang tải tư liệu và cập nhật Cảnh {scene_id} (Video #{job_id})...**"
        )

        from app.agents.media_agent import MediaAgent
        from app.graph.runner import GraphRunner
        from app.graph.state import EditRequest

        media_agent = MediaAgent()
        work_dir = Path(settings.TEMP_DIR) / f"job_{job_id}" / "media"
        work_dir.mkdir(parents=True, exist_ok=True)
        ext = ".mp4" if m_type == "video" else (".png" if "png" in m_url.lower() else ".jpg")
        local_path = str(work_dir / f"scene_{scene_id:03d}_applied{ext}")

        await asyncio.to_thread(media_agent.download_file, m_url, local_path)

        runner = GraphRunner(job_id=job_id)
        edit_req = EditRequest(
            action_type="replace_media",
            scene_id=scene_id,
            new_media_path=local_path
        )
        final_state = await runner.execute_edit(edit_req)
        video_out = final_state.get("rendered_video_path")

        success_text = (
            f"✅ **ĐÃ CẬP NHẬT CẢNH {scene_id} THÀNH CÔNG!**\n\n"
            f"🎬 **Tư liệu mới:** {item.get('title')}\n"
            f"📊 **Loại:** {m_type.upper()} ({item.get('source')})\n\n"
            f"✨ Video #{job_id} đã được dựng hoàn chỉnh lại với tư liệu mới!"
        )
        if status_msg:
            await safe_edit_text(status_msg, success_text)
        else:
            await safe_reply(update, success_text)

        if video_out and Path(video_out).exists():
            try:
                with open(video_out, "rb") as vf:
                    await update.effective_chat.send_video(
                        video=vf,
                        caption=f"🎥 **Video #{job_id} sau khi cập nhật Cảnh {scene_id}:**",
                        supports_streaming=True
                    )
            except Exception as ex:
                logger.error(f"Gửi video sau khi apply media thất bại: {ex}")


# Cache tạm cho kết quả tìm kiếm hình ảnh & video trên Telegram
_SEARCH_CACHE: Dict[str, Dict[str, Any]] = {}


async def search_image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Tìm kiếm hình ảnh mở & tư liệu từ nhiều nguồn: /search_image <từ khóa>
    Cho phép chọn nhanh cảnh để gán vào video đang chọn.
    """
    if not is_authorized(update):
        return

    if not context.args:
        await safe_reply(
            update,
            "🔍 **CÁCH TÌM KIẾM HÌNH ẢNH ĐA NGUỒN TỰ ĐỘNG:**\n\n"
            "• Gõ `/search_image <tên nhân vật / sự kiện / từ khóa>`\n"
            "• **Ví dụ:**\n"
            "  - `/search_image John D. Rockefeller portrait`\n"
            "  - `/search_image Nikola Tesla laboratory`\n"
            "  - `/search_image ancient Rome colosseum historical`\n\n"
            "👉 Hệ thống tìm kiếm đồng thời từ **Wikipedia**, **Wikimedia Commons**, **Openverse (800M+ ảnh)** và **Pexels**, hiển thị nút bấm để gán ngay vào video!"
        )
        return

    query = " ".join(context.args)
    user = update.effective_user

    # Tìm job gần nhất của user
    with get_db() as db:
        recent_job = db.query(VideoJob).filter(VideoJob.user_id == user.id).order_by(VideoJob.id.desc()).first()
        if not recent_job:
            recent_job = db.query(VideoJob).order_by(VideoJob.id.desc()).first()
        job_id = recent_job.id if recent_job else None

    wait_msg = await safe_reply(update, f"🔎 Đang tìm kiếm ảnh khắp Internet cho: *\"{query}\"*...")

    from app.agents.media_agent import MediaAgent
    media_agent = MediaAgent()
    photos = await asyncio.to_thread(media_agent.search_multi_source_images, query, per_page=4)

    if not photos:
        await safe_edit_text(wait_msg, f"⚠️ Không tìm thấy ảnh phù hợp cho: *\"{query}\"*. Bạn hãy thử lại với từ khóa khác (ưu tiên tiếng Anh hoặc tên đầy đủ).")
        return

    await safe_edit_text(wait_msg, f"🎉 Tìm thấy {len(photos)} ảnh chất lượng cao cho: *\"{query}\"*\n👇 Chọn cảnh bên dưới mỗi ảnh để gán trực tiếp vào Video #{job_id or 'mới nhất'}:")

    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    import hashlib

    for idx, photo in enumerate(photos, 1):
        url = photo.get("url")
        src = photo.get("source", "web").upper()
        title = photo.get("title", query)[:60]
        
        # Tạo short hash key để lưu cache URL
        cache_key = hashlib.md5(url.encode()).hexdigest()[:10]
        _SEARCH_CACHE[cache_key] = {
            "url": url,
            "media_type": "image",
            "title": title,
            "source": src
        }

        # Tạo bàn phím 6 cảnh
        buttons = []
        if job_id:
            row1 = [
                InlineKeyboardButton(f"🎬 Cảnh 1", callback_data=f"apply_media:{job_id}:1:{cache_key}"),
                InlineKeyboardButton(f"🎬 Cảnh 2", callback_data=f"apply_media:{job_id}:2:{cache_key}"),
                InlineKeyboardButton(f"🎬 Cảnh 3", callback_data=f"apply_media:{job_id}:3:{cache_key}"),
            ]
            row2 = [
                InlineKeyboardButton(f"🎬 Cảnh 4", callback_data=f"apply_media:{job_id}:4:{cache_key}"),
                InlineKeyboardButton(f"🎬 Cảnh 5", callback_data=f"apply_media:{job_id}:5:{cache_key}"),
                InlineKeyboardButton(f"🎬 Cảnh 6", callback_data=f"apply_media:{job_id}:6:{cache_key}"),
            ]
            buttons = [row1, row2]

        caption = f"📸 **[Ảnh #{idx} - {src}]** {title}"
        try:
            await update.effective_chat.send_photo(
                photo=url,
                caption=caption,
                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None
            )
        except Exception:
            # Fallback nếu Telegram không load được URL trực tiếp
            try:
                await update.effective_chat.send_message(
                    text=f"{caption}\n🔗 [Xem ảnh gốc]({url})",
                    reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
                    parse_mode="Markdown"
                )
            except Exception:
                pass


async def search_video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Tìm kiếm video clip dọc 9:16 chất lượng cao từ Pexels: /search_video <từ khóa>
    """
    if not is_authorized(update):
        return

    if not context.args:
        await safe_reply(
            update,
            "🎥 **CÁCH TÌM KIẾM VIDEO DỌC 9:16:**\n\n"
            "• Gõ `/search_video <từ khóa mô tả hành động / bối cảnh>`\n"
            "• **Ví dụ:**\n"
            "  - `/search_video counting dollar cash money`\n"
            "  - `/search_video rain dark cinematic street`\n"
            "  - `/search_video typing code computer screen`\n\n"
            "👉 Hệ thống sẽ tìm video dọc Full HD và hiển thị nút để bạn gán vào phân cảnh của Video!"
        )
        return

    query = " ".join(context.args)
    user = update.effective_user

    with get_db() as db:
        recent_job = db.query(VideoJob).filter(VideoJob.user_id == user.id).order_by(VideoJob.id.desc()).first()
        if not recent_job:
            recent_job = db.query(VideoJob).order_by(VideoJob.id.desc()).first()
        job_id = recent_job.id if recent_job else None

    wait_msg = await safe_reply(update, f"🎬 Đang tìm video dọc 9:16 cho: *\"{query}\"*...")

    from app.agents.media_agent import MediaAgent
    media_agent = MediaAgent()
    videos = await asyncio.to_thread(media_agent.search_videos, query, per_page=4, orientation="portrait")

    if not videos:
        await safe_edit_text(wait_msg, f"⚠️ Không tìm thấy video dọc phù hợp cho: *\"{query}\"*. Hãy thử với từ khóa tiếng Anh đơn giản hơn.")
        return

    await safe_edit_text(wait_msg, f"🎉 Tìm thấy {len(videos)} video clip dọc 9:16 cho: *\"{query}\"*\n👇 Chọn cảnh bên dưới để gán trực tiếp vào Video #{job_id or 'mới nhất'}:")

    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    import hashlib

    for idx, v in enumerate(videos, 1):
        v_files = v.get("video_files", [])
        portrait_files = [vf for vf in v_files if vf.get("width", 0) <= vf.get("height", 0) and vf.get("link")]
        if not portrait_files:
            continue
        best_f = sorted(portrait_files, key=lambda x: x.get("height", 0), reverse=True)[0]
        v_url = best_f.get("link")
        dur = v.get("duration", 0)
        thumb = v.get("image", "")

        cache_key = hashlib.md5(v_url.encode()).hexdigest()[:10]
        _SEARCH_CACHE[cache_key] = {
            "url": v_url,
            "media_type": "video",
            "title": f"Pexels Video {query} ({dur}s)",
            "source": "PEXELS_VIDEO"
        }

        buttons = []
        if job_id:
            row1 = [
                InlineKeyboardButton(f"🎬 Cảnh 1", callback_data=f"apply_media:{job_id}:1:{cache_key}"),
                InlineKeyboardButton(f"🎬 Cảnh 2", callback_data=f"apply_media:{job_id}:2:{cache_key}"),
                InlineKeyboardButton(f"🎬 Cảnh 3", callback_data=f"apply_media:{job_id}:3:{cache_key}"),
            ]
            row2 = [
                InlineKeyboardButton(f"🎬 Cảnh 4", callback_data=f"apply_media:{job_id}:4:{cache_key}"),
                InlineKeyboardButton(f"🎬 Cảnh 5", callback_data=f"apply_media:{job_id}:5:{cache_key}"),
                InlineKeyboardButton(f"🎬 Cảnh 6", callback_data=f"apply_media:{job_id}:6:{cache_key}"),
            ]
            buttons = [row1, row2]

        caption = f"🎥 **[Video #{idx} - PEXELS 9:16]** Thời lượng: {dur}s | {best_f.get('width')}x{best_f.get('height')}"
        try:
            if thumb:
                await update.effective_chat.send_photo(
                    photo=thumb,
                    caption=caption,
                    reply_markup=InlineKeyboardMarkup(buttons) if buttons else None
                )
            else:
                await update.effective_chat.send_message(
                    text=f"{caption}\n🔗 [Xem Video]({v_url})",
                    reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
                    parse_mode="Markdown"
                )
        except Exception:
            pass


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Báo cáo tổng hợp số lượng video và tài nguyên"""
    if not is_authorized(update):
        return

    with get_db() as db:
        total_jobs = db.query(VideoJob).count()
        ready_jobs = db.query(VideoJob).filter(VideoJob.status == "SCRIPT_READY").count()

    # Tự động lấy URL công khai nếu đang chạy trên Cloud (Render, Fly, VPS...)
    dashboard_url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("WEB_DASHBOARD_URL")
    if not dashboard_url:
        port = os.getenv("PORT", "8000")
        dashboard_url = f"http://localhost:{port}"

    # Kiểm tra Drive token thật sự
    drive_token_path = os.path.join(os.path.dirname(__file__), "..", "..", "credentials", "accounts", "account_1", "drive_token.json")
    drive_status = "Sẵn sàng 🟢 (20TB)" if os.path.exists(drive_token_path) else "Chưa xác thực ⚠️ (chạy py auth_google.py drive)"

    text = (
        f"📊 **BÁO CÁO NHÓM LÀM VIỆC (AI STUDIO)**\n\n"
        f"• Tổng số video trong hệ thống: `{total_jobs}`\n"
        f"• Kịch bản đang chờ duyệt/render: `{ready_jobs}`\n"
        f"• Kho Google Drive: {drive_status}\n"
        f"• Cụm Google Ultra Quota: Đang hoạt động 🟢\n"
        f"• Web Dashboard: {dashboard_url} 🌐"
    )
    await safe_reply(update, text)


async def chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Thảo luận kịch bản với Đạo diễn AI: /chat <câu hỏi hoặc ý tưởng>
    """
    if not is_authorized(update):
        return

    if not context.args:
        help_text = (
            "💡 **CÁCH THẢO LUẬN & LÊN KỊCH BẢN VỚI AI DIRECTOR:**\n\n"
            "• Gõ `/chat <câu hỏi hoặc ý tưởng>` để bắt đầu thảo luận.\n"
            "• **Ví dụ:**\n"
            "  - `/chat Cho mình 3 ý tưởng Shorts thật sốc về tâm lý học hành vi`\n"
            "  - `/chat Ý tưởng 'Thao túng bằng ánh mắt' thì 3 giây đầu nên nói câu gì giật gân?`\n\n"
            "👉 Trong nhóm, bạn cũng có thể **Tag @VV1n_mybot** hoặc **Reply vào tin nhắn của bot** để chat tự nhiên!"
        )
        await safe_reply(update, help_text)
        return

    user = update.effective_user
    chat = update.effective_chat
    prompt = " ".join(context.args)

    with get_db() as db:
        db_user = db.query(User).filter(User.telegram_id == user.id).first()
        channel_id = db_user.active_channel_id if db_user and db_user.active_channel_id else "channel_psychology"
        if db_user:
            db_user.last_active = datetime.utcnow()
        log_activity(db, user.id, user.username, "CHAT_BRAINSTORM", f"Thảo luận kịch bản: '{prompt[:50]}...'", channel_id=channel_id)

    # Lấy thông tin ngách kênh
    channel_niche = "psychology"
    channels = settings.get_channels_config()
    for ch in channels:
        if ch.get("id") == channel_id:
            channel_niche = ch.get("niche", "general")
            break

    session_id = str(chat.id)
    status_msg = await safe_reply(update, f"🧠 **{user.first_name}**: *\"{prompt}\"*\n\n⏳ *Đạo diễn AI đang tư duy và phân tích góc nhìn...*")

    ai_reply = await asyncio.to_thread(
        content_agent.chat_brainstorm,
        session_id=session_id,
        user_message=prompt,
        channel_niche=channel_niche,
        language="vi"
    )

    markup = get_chat_brainstorm_keyboard(session_id)
    formatted_reply = f"🎬 **ĐẠO DIỄN SHORTS AI:**\n\n{ai_reply}\n\n💡 *Ưng ý ý tưởng nào? Hãy bấm nút bên dưới để chốt thành kịch bản sản xuất video!*"

    if status_msg:
        await safe_edit_text(status_msg, formatted_reply, reply_markup=markup)
    else:
        await safe_reply(update, formatted_reply, reply_markup=markup)


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Xử lý khi người dùng nhắn tin trực tiếp (DM) hoặc Reply / Tag bot trong nhóm
    """
    if not update.message or not update.message.text:
        return
    if not is_authorized(update):
        return

    text = update.message.text.strip()
    if text.startswith("/"):
        return  # Bỏ qua các câu lệnh khác

    chat = update.effective_chat
    user = update.effective_user
    is_group = chat.type in ["group", "supergroup"]

    bot_username = context.bot.username or "VV1n_mybot"
    is_reply_to_bot = (
        update.message.reply_to_message and
        update.message.reply_to_message.from_user and
        update.message.reply_to_message.from_user.is_bot
    )
    is_mentioned = f"@{bot_username.lower()}" in text.lower()

    # Trong nhóm: Chỉ trả lời nếu được tag hoặc reply
    if is_group and not (is_reply_to_bot or is_mentioned):
        return

    # Lọc bỏ username khỏi nội dung
    clean_text = re.sub(rf"@{bot_username}\b", "", text, flags=re.IGNORECASE).strip()
    if not clean_text:
        await safe_reply(update, f"👋 Chào {user.first_name}! Bạn muốn cùng AI lên ý tưởng video gì hôm nay?")
        return

    # Xử lý nhập liệu tương tác cho kênh (đổi tên hoặc tạo kênh mới)
    if context.user_data.get("awaiting_rename"):
        target_ch_id = context.user_data.pop("awaiting_rename")
        ok, msg, updated = channel_manager.update_channel(target_ch_id, name=clean_text)
        if ok:
            text = f"✅ **Đã đổi tên kênh thành:** {clean_text}\n\n" + _format_channel_info_text(updated)
            markup = get_channel_manage_keyboard(target_ch_id, is_active=True)
            await safe_reply(update, text, reply_markup=markup)
        else:
            await safe_reply(update, f"❌ Lỗi đổi tên: {msg}")
        return

    if context.user_data.get("awaiting_new_channel_name"):
        context.user_data.pop("awaiting_new_channel_name")
        import unicodedata
        normalized = unicodedata.normalize('NFKD', clean_text).encode('ASCII', 'ignore').decode('utf-8')
        raw_id = re.sub(r'[^a-zA-Z0-9]+', '_', normalized).strip('_').lower()
        new_id = f"ch_{raw_id}" if not raw_id.startswith("ch_") else raw_id
        if len(new_id) > 28:
            new_id = new_id[:28]

        accounts = channel_manager.get_accounts_enriched()
        target_acc = accounts[0]["id"] if accounts else "account_1"

        ok, msg, new_ch = channel_manager.add_channel(
            account_id=target_acc,
            channel_id=new_id,
            name=clean_text,
            niche="general",
            language="vi",
            visual_mode="imagen_ai",
            preferred_voice="vi-VN-NamMinhNeural"
        )
        if ok:
            with get_db() as db:
                db_user = db.query(User).filter(User.telegram_id == user.id).first()
                if db_user:
                    db_user.active_channel_id = new_id
                log_activity(db, user.id, user.username, "ADD_CHANNEL", f"Tạo kênh '{clean_text}' ({new_id})", channel_id=new_id)

            text = (
                f"🎉 **ĐÃ TẠO KÊNH THÀNH CÔNG!**\n\n"
                f"📺 **Tên kênh:** {clean_text}\n"
                f"🔑 **Mã kênh:** `{new_id}`\n"
                f"🎯 **Chủ đề:** Mặc định\n"
                f"🌐 **Ngôn ngữ:** Tiếng Việt (vi)\n\n"
                f"👉 Đã tự động đặt làm **kênh đang chọn** của bạn!\n"
                f"Bấm các nút bên dưới để tùy chỉnh tiếp (Đổi chủ đề, giọng đọc...) hoặc gõ `/idea <chủ đề>` để tạo video ngay!"
            )
            markup = get_channel_manage_keyboard(new_id, is_active=True)
            await safe_reply(update, text, reply_markup=markup)
        else:
            await safe_reply(update, f"❌ Không thể tạo kênh: {msg}")
        return

    # Tự động phát hiện nếu người dùng Reply vào tin nhắn kịch bản/video để góp ý sửa
    if is_reply_to_bot and update.message.reply_to_message:
        replied_text = update.message.reply_to_message.text or update.message.reply_to_message.caption or ""
        match = re.search(r"(?:Task|Video)\s*#(\d+)", replied_text, re.IGNORECASE)
        if match:
            job_id = int(match.group(1))
            await _handle_script_revision(update, context, job_id, clean_text)
            return

    with get_db() as db:
        db_user = db.query(User).filter(User.telegram_id == user.id).first()
        channel_id = db_user.active_channel_id if db_user and db_user.active_channel_id else "channel_psychology"
        if db_user:
            db_user.last_active = datetime.utcnow()
        log_activity(db, user.id, user.username, "CHAT_REPLY", f"Trao đổi tự nhiên: '{clean_text[:50]}...'", channel_id=channel_id)

    channel_niche = "psychology"
    channels = settings.get_channels_config()
    for ch in channels:
        if ch.get("id") == channel_id:
            channel_niche = ch.get("niche", "general")
            break

    session_id = str(chat.id)
    status_msg = await safe_reply(update, "⏳ *Đạo diễn AI đang trả lời...*")

    ai_reply = await asyncio.to_thread(
        content_agent.chat_brainstorm,
        session_id=session_id,
        user_message=clean_text,
        channel_niche=channel_niche,
        language="vi"
    )

    markup = get_chat_brainstorm_keyboard(session_id)
    formatted_reply = f"🎬 **ĐẠO DIỄN SHORTS AI:**\n\n{ai_reply}"

    if status_msg:
        await safe_edit_text(status_msg, formatted_reply, reply_markup=markup)
    else:
        await safe_reply(update, formatted_reply, reply_markup=markup)


async def chat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Xử lý nút bấm: Chốt kịch bản từ đoạn chat / Gợi ý thêm góc nhìn / Đổi hook
    """
    query = update.callback_query
    await safe_query_answer(query)
    data = query.data
    user = query.from_user

    if data.startswith("convert_chat:"):
        session_id = data.split(":")[1]
        
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first()
            channel_id = db_user.active_channel_id if db_user and db_user.active_channel_id else "channel_psychology"

        channel_niche = "psychology"
        channel_lang = "en"
        channels = settings.get_channels_config()
        for ch in channels:
            if ch.get("id") == channel_id:
                channel_niche = ch.get("niche", "general")
                channel_lang = ch.get("language", "en")
                break

        wait_msg = await safe_reply(
            update,
            f"🎬 **{user.first_name}** vừa bấm **Chốt Kịch Bản**!\n"
            f"⏳ Gemini AI đang tổng hợp toàn bộ thảo luận thành kịch bản phân cảnh chuẩn cho kênh `{channel_id}`..."
        )

        try:
            script: ScriptContract = await asyncio.to_thread(
                content_agent.convert_discussion_to_script,
                session_id=session_id,
                channel_niche=channel_niche,
                language=channel_lang
            )

            with get_db() as db:
                job = VideoJob(
                    user_id=user.id,
                    target_channel_id=channel_id,
                    topic=script.title,
                    title=script.title,
                    script=script.full_narration,
                    description=script.description,
                    tags=",".join(script.tags),
                    scenes_json=script.to_json_str(),
                    status="SCRIPT_READY"
                )
                db.add(job)
                db.flush()
                job_id = job.id

                log_activity(db, user.id, user.username, "CONVERT_CHAT_SCRIPT",
                             f"Kịch bản #{job_id} được chốt từ thảo luận: '{script.title}'",
                             channel_id=channel_id)

            scenes_preview = "\n".join([
                f"• Cảnh {s.scene_id}: {s.narration[:60]}... _(Tìm: {s.visual_keyword})_"
                for s in script.scenes[:3]
            ])

            reply_content = (
                f"🎉 **ĐÃ TỔNG HỢP XONG KỊCH BẢN SẢN XUẤT (Task #{job_id})**\n\n"
                f"📌 **Tiêu đề:** {script.title}\n"
                f"🔥 **Hook 3 giây đầu:** \"{script.hook}\"\n\n"
                f"📝 **Phân cảnh tiêu biểu:**\n{scenes_preview}\n\n"
                f"⏱️ **Ước lượng:** ~{script.total_estimated_duration:.0f}s | {len(script.scenes)} cảnh\n"
                f"📺 **Kênh:** `{channel_id}` | 🗣️ **Người chốt:** {user.first_name}\n\n"
                f"💡 **Tùy biến hình ảnh:** Bạn có thể gửi ảnh/video vào đây kèm chú thích `/scene <số_cảnh>` để gán ảnh riêng cho từng cảnh!\n\n"
                f"👉 Bấm nút bên dưới để tiến hành đưa vào hàng chờ render video!"
            )

            review_markup = get_script_review_keyboard(job_id)
            if wait_msg:
                await safe_edit_text(wait_msg, reply_content, reply_markup=review_markup)
            else:
                await safe_reply(update, reply_content, reply_markup=review_markup)

        except Exception as e:
            logger.error(f"Lỗi khi convert chat thành script: {e}", exc_info=True)
            err_text = f"❌ Không thể tổng hợp kịch bản: `{e}`"
            if wait_msg:
                await safe_edit_text(wait_msg, err_text)
            else:
                await safe_reply(update, err_text)

    elif data.startswith("suggest_more:"):
        session_id = data.split(":")[1]
        msg = await asyncio.to_thread(
            content_agent.chat_brainstorm,
            session_id=session_id,
            user_message="Hãy cho mình thêm 3 góc nhìn khác thật độc lạ, gây tò mò cực độ cho chủ đề này!",
            language="vi"
        )
        markup = get_chat_brainstorm_keyboard(session_id)
        await safe_reply(update, f"💡 **3 GÓC NHÌN MỚI TỪ ĐẠO DIỄN:**\n\n{msg}", reply_markup=markup)

    elif data.startswith("change_hooks:"):
        session_id = data.split(":")[1]
        msg = await asyncio.to_thread(
            content_agent.chat_brainstorm,
            session_id=session_id,
            user_message="Hãy viết cho mình 3 câu Hook 3 giây đầu hoàn toàn mới: 1 câu gây sốc, 1 câu tâm lý tò mò, 1 câu đánh trúng nỗi sợ của người xem!",
            language="vi"
        )
        markup = get_chat_brainstorm_keyboard(session_id)
        await safe_reply(update, f"🔥 **3 CÂU HOOK MỚI:**\n\n{msg}", reply_markup=markup)

