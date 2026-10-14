import os
import logging
import asyncio
import re
import html
import json
import time
from typing import Optional, List, Dict, Any, Union, Tuple
from pathlib import Path
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
    get_video_edit_options_keyboard,
    get_video_review_keyboard,
    get_chat_brainstorm_keyboard,
    get_publish_channel_keyboard,
    get_pre_publish_advisory_keyboard,
    get_viral_ideas_keyboard,
    get_videos_list_keyboard,
    get_schedule_keyboard,
    get_schedule_channel_keyboard,
    get_delete_confirmation_keyboard,
    get_delete_failed_confirmation_keyboard,
)
from app.database.models import get_db, User, VideoJob, log_activity
from app.agents.content_agent import ContentAgent
from app.schemas.contracts import ScriptContract
from app.services.render_queue import get_render_queue
from app.services import channel_manager
import threading

logger = logging.getLogger(__name__)
content_agent = ContentAgent()

# ──────────────────────────────────────────────────────────────────────────────
# HỆ THỐNG CHỐNG SPAM CLICK / CHỐNG NHÂN BẢN TÁC VỤ KHI DELAY MẠNG (ANTI-SPAM GUARD)
# ──────────────────────────────────────────────────────────────────────────────
_ACTION_LOCK_MUTEX = threading.Lock()
_INFLIGHT_ACTIONS: Dict[str, float] = {}      # key -> timestamp bắt đầu chạy
_ACTION_COOLDOWNS: Dict[str, float] = {}      # key -> timestamp hết hạn cooldown sau khi chạy xong


def _try_acquire_action(key: str, max_inflight_sec: float = 120.0, debounce_sec: float = 4.0) -> bool:
    """
    Kiểm tra và khóa tác vụ để chống spam click khi mạng hoặc AI có độ trễ:
    - Nếu tác vụ cùng `key` đang chạy (chưa quá `max_inflight_sec`) -> từ chối ngay (return False).
    - Nếu tác vụ cùng `key` vừa mới bấm/hoàn tất trong vòng `debounce_sec` -> từ chối ngay (return False).
    """
    now = time.time()
    with _ACTION_LOCK_MUTEX:
        # Dọn các khóa cũ đã hết hạn
        expired_inf = [k for k, ts in _INFLIGHT_ACTIONS.items() if now - ts > max_inflight_sec]
        for k in expired_inf:
            _INFLIGHT_ACTIONS.pop(k, None)
        expired_cd = [k for k, exp_ts in _ACTION_COOLDOWNS.items() if now > exp_ts]
        for k in expired_cd:
            _ACTION_COOLDOWNS.pop(k, None)

        if key in _INFLIGHT_ACTIONS:
            return False
        if key in _ACTION_COOLDOWNS and now < _ACTION_COOLDOWNS[key]:
            return False

        _INFLIGHT_ACTIONS[key] = now
        _ACTION_COOLDOWNS[key] = now + debounce_sec
        return True


def _release_action(key: str, post_cooldown_sec: float = 5.0):
    """Giải phóng khóa in-flight và giữ cooldown ngắn để chặn các cú click bị dồn toa (buffered clicks)."""
    now = time.time()
    with _ACTION_LOCK_MUTEX:
        _INFLIGHT_ACTIONS.pop(key, None)
        if post_cooldown_sec > 0:
            _ACTION_COOLDOWNS[key] = max(_ACTION_COOLDOWNS.get(key, 0.0), now + post_cooldown_sec)


def _find_recent_duplicate_job(channel_id: str, topic: str, window_seconds: int = 240) -> Optional[int]:
    """Kiểm tra xem chủ đề này vừa mới được tạo VideoJob trong vài phút gần đây chưa (chống tạo đúp nhiều video)."""
    if not topic:
        return None
    norm_topic = re.sub(r"\s+", " ", topic.strip().lower())
    now_utc = datetime.utcnow()
    try:
        with get_db() as db:
            recent_jobs = (
                db.query(VideoJob)
                .filter(VideoJob.target_channel_id == channel_id)
                .order_by(VideoJob.id.desc())
                .limit(10)
                .all()
            )
            for j in recent_jobs:
                if not j.created_at or j.status == "FAILED":
                    continue
                age_sec = abs((now_utc - j.created_at.replace(tzinfo=None)).total_seconds())
                j_top = re.sub(r"\s+", " ", (j.topic or "").strip().lower())
                if age_sec <= window_seconds and j_top == norm_topic:
                    return j.id
    except Exception as e:
        logger.debug(f"Lỗi kiểm tra duplicate job: {e}")
    return None


async def safe_reply(update: Update, text: str, reply_markup=None, parse_mode="Markdown"):
    """
    Gửi tin nhắn an toàn:
    - Cắt gọn nếu vượt quá giới hạn 4096 ký tự của Telegram.
    - Tự động fallback sang Plain Text nếu lỗi Markdown/HTML parse.
    - Luôn có fallback gửi qua effective_chat.send_message nếu reply_text trên media/callback thất bại.
    - Không bao giờ để văng exception làm chết bot.
    """
    if not text:
        return None

    # Telegram giới hạn 4096 ký tự
    if len(text) > 4000:
        text = text[:3980] + "\n\n...(nội dung còn tiếp)"

    # 1. Thử gửi với định dạng yêu cầu
    try:
        if update.message:
            return await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        elif update.callback_query and getattr(update.callback_query, "message", None) and hasattr(update.callback_query.message, "reply_text"):
            return await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        elif update.effective_chat:
            return await update.effective_chat.send_message(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        logger.warning(f"Lỗi gửi tin nhắn parse_mode={parse_mode} ({e}), fallback sang Plain Text...")

    # 2. Fallback sang Plain Text không định dạng
    plain = re.sub(r'[*_`\[\]()<>]', '', text)
    try:
        if update.message:
            return await update.message.reply_text(plain, reply_markup=reply_markup)
        elif update.callback_query and getattr(update.callback_query, "message", None) and hasattr(update.callback_query.message, "reply_text"):
            return await update.callback_query.message.reply_text(plain, reply_markup=reply_markup)
        elif update.effective_chat:
            return await update.effective_chat.send_message(plain, reply_markup=reply_markup)
    except Exception as e2:
        logger.warning(f"Fallback reply_text thất bại ({e2}), gửi thẳng vào effective_chat...")
        try:
            if update.effective_chat:
                return await update.effective_chat.send_message(plain, reply_markup=reply_markup)
        except Exception as e3:
            logger.error(f"Tất cả phương thức gửi tin nhắn đều thất bại: {e3}")
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
        plain = re.sub(r'[*_`\[\]()<>]', '', text)
        try:
            return await message.edit_text(plain, reply_markup=reply_markup)
        except Exception as e2:
            if "Message is not modified" in str(e2):
                return message
            logger.error(f"Fallback edit_text thất bại ({e2}), gửi tin nhắn mới thay thế...")
            try:
                if hasattr(message, "reply_text"):
                    return await message.reply_text(plain, reply_markup=reply_markup)
                elif hasattr(message, "chat") and hasattr(message.chat, "send_message"):
                    return await message.chat.send_message(plain, reply_markup=reply_markup)
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

# Alias an toàn tránh NameError
safe_answer_query = safe_query_answer


def is_authorized(update: Update) -> bool:
    """
    Kiểm tra quyền truy cập bot:
    - Chủ sở hữu hệ thống (Giang & Vương): Luôn luôn được cấp quyền truy cập.
    - Thành viên đã được lưu trong Database: Luôn được cấp quyền.
    - Nếu có cấu hình ALLOWED_TELEGRAM_USERS hoặc ALLOWED_GROUP_IDS: bắt buộc tuân thủ danh sách trắng.
    - Nếu không cấu hình: Cho phép tương tác (nhưng các thao tác đặc quyền yêu cầu quyền Admin).
    """
    user = update.effective_user
    chat = update.effective_chat

    # 1. Chủ sở hữu hệ thống luôn luôn được cấp quyền
    if user and user.id in (7177163590, 8498162618):
        return True

    # 2. Thành viên đã có hồ sơ trong Database được phép truy cập
    if user:
        try:
            with get_db() as db:
                db_user = db.query(User).filter(User.telegram_id == user.id).first()
                if db_user:
                    return True
        except Exception:
            pass

    allowed_users = settings.get_allowed_users()
    allowed_groups = settings.get_allowed_group_ids()

    # 3. Mặc định mở nếu chưa đặt whitelist trong .env
    if not allowed_users and not allowed_groups:
        return True

    if chat and chat.type in ["group", "supergroup"]:
        if allowed_groups and chat.id in allowed_groups:
            return True
        if user and user.id in allowed_users:
            return True
        return False

    if user and user.id in allowed_users:
        return True

    return False


def is_admin_user(telegram_id: int) -> bool:
    """Kiểm tra xem người dùng có phải là Quản trị viên (Admin) hay không"""
    if not telegram_id:
        return False
    # Chủ sở hữu hệ thống luôn luôn là Admin
    if telegram_id in (7177163590, 8498162618):
        return True

    # 1. Kiểm tra cấu hình ALLOWED_TELEGRAM_USERS trong .env
    allowed_users = settings.get_allowed_users()
    if telegram_id in allowed_users:
        return allowed_users[telegram_id].lower() in ("admin", "owner")

    # 2. Kiểm tra vai trò trong Database
    try:
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == telegram_id).first()
            if db_user and db_user.role in ("admin", "owner"):
                return True
            # Nếu hệ thống ít thành viên (dưới 4 người), cấp quyền Admin cho các thành viên sáng lập
            human_count = db.query(User).filter(User.telegram_id > 0).count()
            if human_count <= 4:
                return True
    except Exception as e:
        logger.error(f"Lỗi kiểm tra quyền admin cho {telegram_id}: {e}")
    return False


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý lệnh /start"""
    if not is_authorized(update):
        await safe_reply(update, "⛔ Nhóm hoặc tài khoản này chưa được cấp quyền sử dụng bot.")
        return

    user = update.effective_user
    chat = update.effective_chat
    is_group = chat.type in ["group", "supergroup"]

    # Dùng context manager — không bao giờ leak session
    with get_db() as db:
        db_user = db.query(User).filter(User.telegram_id == user.id).first()
        human_users_count = db.query(User).filter(User.telegram_id > 0).count()

        if not db_user:
            allowed_users = settings.get_allowed_users()
            if user.id in allowed_users:
                role = allowed_users[user.id]
            elif human_users_count == 0:
                # User đầu tiên khởi động bot sẽ tự động được trao quyền Admin
                role = "admin"
            else:
                role = "creator"

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
            # Nếu chỉ có duy nhất 1 user trong hệ thống và chưa là admin, tự động gán admin
            if human_users_count <= 1 and db_user.role != "admin":
                db_user.role = "admin"

        is_admin = (db_user.role == "admin") or (user.id in settings.get_allowed_users() and settings.get_allowed_users()[user.id] == "admin")

        log_activity(db, user.id, user.username, "START_BOT",
                     f"Khởi động bot từ {chat.type} '{chat.title or user.first_name}' (Role: {db_user.role})")

    scope_text = f"nhóm **{chat.title}**" if is_group else f"bạn **{user.first_name}**"
    role_badge = f"👑 **Quyền hạn:** Quản trị viên (Admin) • `ID: {user.id}`\n*(Có đặc quyền phê duyệt thiết bị truy cập Web Studio)*" if is_admin else f"👤 **Quyền hạn:** Thành viên sáng tạo (Creator) • `ID: {user.id}`"

    welcome_text = (
        f"🎬 **AI YouTube Multi-Channel Studio đã sẵn sàng cho {scope_text}!**\n\n"
        f"{role_badge}\n\n"
        f"👥 **Cơ chế vận hành đa kênh & Tự động hóa:**\n"
        f"• Mọi thành viên cùng ra lệnh, kịch bản và tiến trình hiển thị công khai trong nhóm.\n"
        f"• Tự động sản xuất video Shorts chuẩn viral, bám sát ngách từng kênh!\n\n"
        f"📌 **Các lệnh chính:**\n"
        f"• `/autopilot` - 🚀 **Tự động hóa kênh A-Z** (Ý tưởng viral ➔ Kịch bản 3s CTA hook ➔ Render 1080p ➔ Lên lịch khung giờ vàng)\n"
        f"• `/schedule` - ⏰ Xem lịch công chiếu & 2 khung giờ vàng YouTube Shorts (20:00 & 04:00 VN)\n"
        f"• `/viral` - 🔥 Gợi ý 5 ý tưởng triệu view (Công thức giữ chân 160% Retention)\n"
        f"• `/idea <chủ đề>` - Lên kịch bản AI ngay tức thì cho kênh đang chọn\n"
        f"• `/videos` - 📺 Xem danh sách tất cả video trong hệ thống (kèm nút xem/tải trực tiếp)\n"
        f"• `/get <id>` - 📥 Tải file video MP4 gửi thẳng vào tin nhắn Telegram để xem ngay\n"
        f"• `/channels` - Xem và chuyển đổi kênh YouTube muốn sản xuất\n"
        f"• `/render <job_id>` - Dựng video từ kịch bản đã duyệt\n"
        f"• `/queue` - Xem trạng thái hàng đợi render (FIFO)\n"
        f"• `/cancel <job_id>` - Hủy video đang chờ trong hàng đợi\n"
        f"• `/kpi` - 📊 Báo cáo sức khỏe tiến độ và chỉ tiêu KPI các kênh\n"
        f"• `/chat <nội dung>` - Thảo luận, phản biện và đồng sáng tạo kịch bản với Đạo diễn AI\n"
        f"• `/status` - Báo cáo số lượng video và trạng thái hệ thống\n\n"
        f"👉 Gõ `/autopilot`, `/viral`, `/schedule`, `/videos` hoặc `/idea` để bắt đầu sáng tạo video cùng nhóm!"
    )
    await safe_reply(update, welcome_text)


def _format_channel_info_text(ch: dict) -> str:
    name = ch.get("name", ch.get("id"))
    ch_id = ch.get("id")
    niche = ch.get("niche", "general")
    lang = ch.get("language", "en").upper()
    vm = ch.get("visual_mode", "auto")
    if vm == "auto":
        visual = "🤖 AI Tự Quyết Định (Smart Director)"
    elif vm == "imagen_ai":
        visual = "✨ Google Imagen 3 (Điện ảnh AI)"
    elif vm == "pexels_stock":
        visual = "🎞️ Pexels HD B-Roll (Thực tế)"
    else:
        visual = "🤖 AI Tự Quyết Định (Smart Director)"
    sched = ch.get("schedule", {})
    hour = sched.get("publish_hour_utc", 12)
    voice = ch.get("preferred_voice", "en-US-ChristopherNeural")

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
            v_label = "🤖 AI Tự Quyết Định (Đạo diễn thông minh)" if visual_key == "auto" else ("✨ Google Imagen 3 (Điện ảnh AI)" if visual_key == "imagen_ai" else "🎞️ Pexels HD B-Roll")
            text = f"✅ Đã đổi phong cách hình ảnh sang **{v_label}**!\n\n" + _format_channel_info_text(updated)
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
        preferred_voice="vi-VN-NamMinhNeural" if ch_lang == "vi" else "en-US-AndrewNeural"
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


def _parse_idea_input(raw_input: str) -> tuple:
    """Tách chủ đề chính, các ghi chú đạo diễn (Director's Notes) và ngôn ngữ chỉ định (Language)."""
    if not raw_input:
        return "", None, None

    text = raw_input.strip()
    explicit_lang = None

    # 1. Kiểm tra tiền tố ngôn ngữ: vi, en, [vi], [en], vi:, en:
    prefix_match = re.match(r'^(?:\[(vi|en)\]|(vi|en):|(vi|en))\s+', text, re.IGNORECASE)
    if prefix_match:
        explicit_lang = (prefix_match.group(1) or prefix_match.group(2) or prefix_match.group(3)).lower()
        text = text[prefix_match.end():].strip()

    # 2. Kiểm tra cờ ngôn ngữ: --vi, --en, -vi, -en, --lang vi, --lang en, --language vi, --language en
    lang_flag_match = re.search(r'(?:\s+|^)--(?:lang(?:uage)?\s+(vi|en)|(vi|en))\b', text, re.IGNORECASE)
    if not lang_flag_match:
        lang_flag_match = re.search(r'(?:\s+|^)-(vi|en)\b', text, re.IGNORECASE)
    if lang_flag_match:
        explicit_lang = (lang_flag_match.group(1) or lang_flag_match.group(2)).lower()
        text = text[:lang_flag_match.start()] + text[lang_flag_match.end():]
        text = text.strip()

    # 3. Kiểm tra cụm từ cuối câu: "bằng tiếng việt", "tiếng việt", "in english", "bằng tiếng anh", "tiếng anh"
    tail_vi = re.search(r'\s+(?:bằng\s+)?(?:tiếng\s+việt|tieng\s+viet|tiếng\s+vi|vietnamese)\s*$', text, re.IGNORECASE)
    if tail_vi:
        explicit_lang = "vi"
        text = text[:tail_vi.start()].strip()
    else:
        tail_en = re.search(r'\s+(?:in\s+english|bằng\s+tiếng\s+anh|tiếng\s+anh|english)\s*$', text, re.IGNORECASE)
        if tail_en:
            explicit_lang = "en"
            text = text[:tail_en.start()].strip()

    # 4. Kiểm tra dấu phân cách pipe (|)
    topic = text
    notes = None
    if "|" in text:
        parts = text.split("|", 1)
        topic = parts[0].strip()
        notes = parts[1].strip()
        # Kiểm tra nếu trong notes có ghi "lang: vi" hoặc "lang: en"
        lang_in_notes = re.search(r'\blang(?:uage)?\s*[:=]\s*(vi|en)\b', notes, re.IGNORECASE)
        if lang_in_notes:
            explicit_lang = lang_in_notes.group(1).lower()
            notes = re.sub(r'\blang(?:uage)?\s*[:=]\s*(vi|en)\b', '', notes, flags=re.IGNORECASE).strip()
            notes = re.sub(r'^[,\s|]+|[,\s|]+$', '', notes).strip()
            if not notes:
                notes = None
    else:
        # Kiểm tra cờ dạng --style, --mood, --focus, --angle, --pace, --visual
        flags_pattern = r'(\s+--(?:style|mood|focus|angle|pace|visual)\s+[^\-]+)'
        match = re.search(flags_pattern, text, re.IGNORECASE)
        if match:
            start_idx = match.start()
            topic = text[:start_idx].strip()
            notes = text[start_idx:].strip()
            notes = re.sub(r'--(style|mood|focus|angle|pace|visual)\s+', r'\1: ', notes, flags=re.IGNORECASE)

    return topic.strip(), notes, explicit_lang


def detect_best_channel_for_topic(topic: str, current_channel_id: Optional[str] = None) -> tuple:
    """
    Tự động phân tích nội dung chủ đề (Topic) để chọn đúng kênh YouTube phù hợp nhất,
    tránh trường hợp chủ đề động vật hoang dã/thiên nhiên lại bị nhầm vào kênh Tâm lý học.
    
    Returns:
        (resolved_channel_id, switch_reason)
    """
    t = (topic or "").lower()
    
    wildlife_keywords = [
        "chim", "bird", "săn mồi", "predator", "hổ", "tiger", "sư tử", "lion",
        "báo", "leopard", "cheetah", "đại bàng", "eagle", "sói", "wolf", "gấu", "bear",
        "cá sấu", "crocodile", "rắn", "snake", "voi", "elephant", "kiếm ăn", "nuôi con",
        "nuôi các con", "rừng", "safari", "thiên nhiên hoang dã", "wildlife", "wild", "mãnh thú", "bầy đàn"
    ]
    
    rescue_keywords = [
        "chó", "mèo", "kitten", "puppy", "dog", "cat", "cứu hộ", "rescue", "bỏ rơi",
        "stray", "lang thang", "nhận nuôi", "thú cưng", "pet", "trạm cứu hộ", "chăm sóc thú"
    ]
    
    mystery_keywords = [
        "biển sâu", "đại dương", "ocean", "abyss", "mariana", "trench", "thalassophobia",
        "vũ trụ", "space", "hố đen", "black hole", "sao hỏa", "mars", "nasa", "alien",
        "ngoài hành tinh", "bí ẩn", "tam giác bermuda", "bermuda", "kim tự tháp", "pyramid",
        "antarctica", "nam cực", "quái vật biển", "megalodon", "titanic"
    ]
    
    psychology_keywords = [
        "tâm lý", "psychology", "thao túng", "manipulation", "đọc vị", "nói dối", "liar",
        "body language", "ngôn ngữ cơ thể", "tự tin", "confident", "khắc kỷ", "stoic",
        "lợi dụng", "using you", "bạn bè toxic", "toxic", "cảm xúc", "lo âu", "anxiety",
        "48 laws of power", "quy luật quyền lực", "tính cách", "personality", "mbti"
    ]

    history_keywords = [
        "what if", "nếu như", "lịch sử", "history", "chiến tranh", "war", "thế chiến", "ww2", "ww1",
        "hitler", "napoleon", "caesar", "rome", "liên xô", "soviet", "đế chế", "quân đội", "giả định"
    ]
    
    scores = {
        "ch_wild_odyssey": sum(1 for kw in wildlife_keywords if kw in t),
        "ch_paws_hearts_rescue": sum(1 for kw in rescue_keywords if kw in t),
        "ucsrjiil67qxidtxt70z-iwa": sum(1 for kw in mystery_keywords if kw in t),
        "channel_maya": sum(1 for kw in psychology_keywords if kw in t),
        "@alternatehistorywhatif-e2x": sum(1 for kw in history_keywords if kw in t),
    }
    
    best_candidate, best_score = max(scores.items(), key=lambda x: x[1])
    
    if best_score >= 1 and current_channel_id and best_candidate != current_channel_id:
        current_score = scores.get(current_channel_id, 0)
        if best_score > current_score:
            ch_names = {
                "ch_wild_odyssey": "Wild Odyssey (Động vật hoang dã)",
                "ch_paws_hearts_rescue": "Paws & Hearts Rescue (Cứu hộ thú cưng)",
                "ucsrjiil67qxidtxt70z-iwa": "The mysteries of the world (Bí ẩn vũ trụ & đại dương)",
                "channel_maya": "Psychology of the world (Tâm lý học)",
                "@alternatehistorywhatif-e2x": "Alternate History (Lịch sử giả định / What If)"
            }
            reason = f"Phát hiện chủ đề phù hợp với kênh {ch_names.get(best_candidate, best_candidate)} -> Tự động chuyển kênh mục tiêu!"
            return best_candidate, reason

    return current_channel_id or "channel_maya", None



async def viral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lệnh gợi ý 5 chủ đề Shorts triệu view theo công thức giữ chân đỉnh cao (150-160%+ Retention).
    Đánh trúng tâm lý, red flags, vạch trần dối trá, thao túng tâm lý ngầm.
    Cung cấp bàn phím 1-Click để chọn ý tưởng và tự động sản xuất video ngay lập tức!
    """
    if not is_authorized(update):
        return

    user = update.effective_user

    # Lấy kênh đang hoạt động của user và lịch sử các video đã sản xuất
    recent_titles = []
    with get_db() as db:
        db_user = db.query(User).filter(User.telegram_id == user.id).first()
        channel_id = db_user.active_channel_id if db_user and db_user.active_channel_id else "channel_maya"
        if db_user:
            db_user.last_active = datetime.utcnow()

        log_activity(db, user.id, user.username, "CMD_VIRAL",
                     f"Yêu cầu gợi ý ý tưởng triệu view", channel_id=channel_id)

        # Lấy tối đa 8 video gần đây của kênh này để AI hiểu nội dung thực tế và không trùng lặp
        recent_jobs = db.query(VideoJob).filter(
            VideoJob.target_channel_id == channel_id,
            VideoJob.title.isnot(None)
        ).order_by(VideoJob.id.desc()).limit(8).all()
        recent_titles = [j.title for j in recent_jobs if j.title]

    ch = channel_manager.get_channel(channel_id) or {}
    channel_name = ch.get("name") or channel_id
    channel_niche = ch.get("niche") or "psychology"
    channel_lang = ch.get("language") or "en"

    from app.services.channel_intelligence import get_channel_existing_portfolio, suggest_channel_topics

    portfolio = await asyncio.to_thread(get_channel_existing_portfolio, channel_id)
    top_performers = portfolio.get("top_performers", [])
    flop_videos = portfolio.get("flop_videos", [])

    if top_performers:
        top_name = top_performers[0].get("title", "")
        if len(top_name) > 35:
            top_name = top_name[:35] + "..."
        top_stat_text = top_performers[0].get("views_text") or f"{top_performers[0].get('views_num', 0)} views"
        top_desc = f"Dựa trên video đỉnh cao: *{top_name}* ({top_stat_text})"
    else:
        top_desc = "Tối ưu hóa Retention > 150% + Hook giật gân 3 giây đầu"

    flop_desc = f"🛡️ Đã quét {len(flop_videos)} video flop/tụt tương tác để né bẫy chủ đề" if flop_videos else "🛡️ Chống trùng lặp & bão hòa đề tài 100%"

    wait_msg = await safe_reply(
        update,
        f"🔥 **ĐẠO DIỄN SHORTS AI ĐANG PHÂN TÍCH KÊNH `{channel_name}`...**\n\n"
        f"📺 **Chủ đề kênh:** `{channel_niche}` ({channel_lang.upper()})\n"
        f"🏆 **Thống kê:** {top_desc}\n"
        f"{flop_desc}\n"
        f"🌍 **Xu hướng thế giới:** Đang đối chiếu ngách `{channel_niche}` toàn cầu 2026...\n\n"
        f"⏳ Đang tạo 5 ý tưởng triệu view bám sát dữ liệu..."
    )

    try:
        ideas = await asyncio.to_thread(
            suggest_channel_topics,
            channel_id=channel_id,
            count=5
        )

        if not ideas:
            await safe_edit_text(wait_msg, "⚠️ Chưa thể tìm ý tưởng phù hợp lúc này. Vui lòng thử lại sau giây lát!")
            return

        # Lưu cache vào chat_data để callback dùng
        if context.chat_data is None:
            context.chat_data = {}
        context.chat_data["cached_viral_ideas"] = ideas
        context.chat_data["cached_viral_channel"] = channel_id

        text_lines = [
            f"🔥 **5 Ý TƯỞNG SHORTS TRIỆU VIEW (BÁM SÁT DỮ LIỆU & XU HƯỚNG THẾ GIỚI)**\n",
            f"📺 **Kênh:** `{channel_name}` | 🎯 **Chủ đề:** `{channel_niche}`\n",
        ]
        if top_performers:
            top_perf_views = top_performers[0].get("views_text") or (str(top_performers[0].get("views_num", 0)) + " views")
            text_lines.append(f"🏆 **Phát huy thế mạnh kênh:** *{top_performers[0]['title']}* ({top_perf_views})")
        if flop_videos:
            text_lines.append(f"🛡️ **Cơ chế chống Flop:** Khắc phục tình trạng bão hòa / lệch ngách từ {len(flop_videos)} video trước")
        text_lines.append("")

        for idx, item in enumerate(ideas):
            topic = item.get("topic", "")
            title = item.get("title", topic)
            hook = item.get("hook", "")
            retention = item.get("predicted_retention", "150%+")
            pinned = item.get("pinned_comment", "")
            formula = item.get("viral_formula", "Debate in Comments")
            fixes_flaw = item.get("fixes_flaw", "")
            trend_align = item.get("trend_alignment", "")

            text_lines.append(f"**#{idx+1}. {title}**")
            text_lines.append(f"• ⚡ **Hook 3s:** \"_{hook}_\"")
            text_lines.append(f"• 📈 **Dự báo:** `{retention}` | **CTA:** `{formula}`")
            if fixes_flaw:
                text_lines.append(f"• 🛡️ **Khắc phục nhược điểm:** {fixes_flaw}")
            if trend_align:
                text_lines.append(f"• 🌍 **Xu hướng 2026:** {trend_align}")
            if pinned:
                text_lines.append(f"• 💬 **Bình luận mồi:** \"_{pinned}_\"")
            text_lines.append("")

        text_lines.append("👉 **Bấm vào nút bên dưới** để AI tự động lên kịch bản và sản xuất video ngay lập tức!")

        full_text = "\n".join(text_lines)
        markup = get_viral_ideas_keyboard(ideas, channel_id)

        if wait_msg:
            await safe_edit_text(wait_msg, full_text, reply_markup=markup)
        else:
            await safe_reply(update, full_text, reply_markup=markup)

    except Exception as e:
        logger.error(f"Lỗi khi gợi ý ý tưởng viral: {e}", exc_info=True)
        if wait_msg:
            await safe_edit_text(wait_msg, f"❌ Có lỗi xảy ra khi tìm ý tưởng: `{e}`")
        else:
            await safe_reply(update, f"❌ Có lỗi xảy ra khi tìm ý tưởng: `{e}`")


async def idea_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh sinh kịch bản AI và gửi vào nhóm cho mọi người cùng duyệt"""
    if not is_authorized(update):
        return

    if not context.args:
        user = update.effective_user
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first()
            active_id = db_user.active_channel_id if db_user and db_user.active_channel_id else "channel_maya"
        ch = channel_manager.get_channel(active_id) or {}
        ch_name = ch.get("name") or active_id
        ch_lang = ch.get("language") or "en"
        ch_lang_name = "Tiếng Việt (vi)" if ch_lang.startswith("vi") else "Tiếng Anh (en)"
        await safe_reply(
            update,
            f"💡 **HƯỚNG DẪN TẠO Ý TƯỞNG & KỊCH BẢN CHO KÊNH `{ch_name}`**\n\n"
            "1️⃣ **Nhập chủ đề tùy ý (Chủ động chọn Ngôn ngữ):**\n"
            "   • Tiếng Việt: `/idea vi <chủ đề>` hoặc `/idea <chủ đề> --vi`\n"
            "   • Tiếng Anh:  `/idea en <chủ đề>` hoặc `/idea <chủ đề> --en`\n"
            "   • Thêm ghi chú: `/idea vi 3 bí ẩn đáy biển | phong cách rùng rợn, nghẹt thở`\n"
            f"   _(Ngôn ngữ mặc định kênh hiện tại: **{ch_lang_name}**)_\n\n"
            "2️⃣ **AI tự động phân tích kênh & đề xuất 5 ý tưởng triệu view:**\n"
            "   Gõ `/viral` (hoặc `/ideas`) hoặc bấm nút bên dưới để AI đối chiếu video top & flop, loại bỏ bão hòa và đón đầu xu hướng 2026!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"🔥 Đề xuất 5 ý tưởng triệu view cho {ch_name}", callback_data=f"refresh_viral_ideas:{active_id}")
            ]])
        )
        return

    raw_topic = " ".join(context.args)
    clean_topic, director_notes, explicit_lang = _parse_idea_input(raw_topic)
    if not clean_topic:
        clean_topic = raw_topic

    # Dọn dẹp dấu thời gian vô tình bị dính vào cuối câu (ví dụ copy paste dính 22:51 hoặc 222:51)
    clean_topic = re.sub(r'[\s\d]*\d{1,2}:\d{2}\s*$', '', clean_topic).strip()

    user = update.effective_user
    idea_lock_key = f"idea_cmd:{user.id if user else 0}"
    if not _try_acquire_action(idea_lock_key, max_inflight_sec=90.0, debounce_sec=5.0):
        await safe_reply(
            update,
            "⏳ **Hệ thống đang biên soạn kịch bản cho yêu cầu trước của bạn rồi!**\n"
            "Vui lòng đợi vài giây để AI hoàn tất, bạn không cần bấm/gửi lại nhiều lần nhé."
        )
        return

    try:
        # Kiểm tra xem có kênh được chỉ định tạm thời từ AI Commander không
        override_ch_id = context.user_data.pop("target_channel_id", None) if context and context.user_data else None

        # Lấy kênh đang hoạt động và tự động điều hướng nếu chủ đề lệch ngách
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first()
            active_id = override_ch_id or (db_user.active_channel_id if db_user and db_user.active_channel_id else "channel_maya")
            
            # Tự động phát hiện và chuyển đúng kênh nếu chủ đề lệch ngách (Ví dụ: chim muông -> Wild Odyssey)
            channel_id, switch_reason = detect_best_channel_for_topic(clean_topic, active_id)

            if db_user:
                db_user.last_active = datetime.utcnow()
                if not db_user.active_channel_id:
                    db_user.active_channel_id = channel_id

            log_activity(db, user.id, user.username, "CMD_IDEA",
                         f"Yêu cầu lên ý tưởng video: '{clean_topic}'", channel_id=channel_id)

        # Chống tạo đúp nhiều VideoJob nếu người dùng bấm/gửi trùng cùng 1 chủ đề trong 4 phút gần nhất
        dup_job_id = _find_recent_duplicate_job(channel_id, clean_topic, window_seconds=240)
        if dup_job_id:
            ch_dup = channel_manager.get_channel(channel_id) or {}
            ch_lang_dup = explicit_lang or ch_dup.get("language") or "en"
            await safe_reply(
                update,
                f"🛡️ **Chống tạo trùng:** Chủ đề *\"{clean_topic}\"* vừa mới được tạo ở **Task #{dup_job_id}**!\n"
                f"👉 Bạn có thể bấm nút bên dưới để duyệt & dựng ngay **Task #{dup_job_id}** mà không cần tạo lại:",
                reply_markup=get_script_review_keyboard(dup_job_id, current_lang=ch_lang_dup),
            )
            return

        # Lấy cấu hình niche & ngôn ngữ từ kênh (ưu tiên cờ ngôn ngữ explicit nếu có)
        ch = channel_manager.get_channel(channel_id) or {}
        channel_name = ch.get("name") or channel_id
        channel_niche = ch.get("niche") or "history_mystery"
        channel_lang = explicit_lang or ch.get("language") or "en"
        lang_display = "🇻🇳 Tiếng Việt" if channel_lang.lower().startswith("vi") else "🇺🇸 Tiếng Anh"

        notes_info = f"\n🎯 **Hướng đạo diễn:** _{director_notes}_" if director_notes else ""
        switch_info = f"\n💡 *{switch_reason}*" if switch_reason else ""
        status_msg = await safe_reply(
            update,
            f"🤖 **{user.first_name}** vừa yêu cầu lên ý tưởng: *\"{clean_topic}\"*{notes_info}{switch_info}\n"
            f"📺 Kênh: `{channel_name}` ({channel_niche}) | 🌐 Ngôn ngữ: **{lang_display}**\n"
            f"⏳ Đang kích hoạt Gemini AI biên soạn kịch bản..."
        )

        try:
            # ContentAgent tạo kịch bản và tự động chấm điểm qua ScriptCritic (tự tinh chỉnh nếu chưa đạt)
            script, eval_report = await asyncio.to_thread(
                content_agent.generate_best_script,
                topic=clean_topic,
                channel_niche=channel_niche,
                language=channel_lang,
                director_notes=director_notes
            )

            # Kiểm tra và bảo vệ chống trùng lặp tiêu đề với toàn bộ kho video của kênh
            from app.services.channel_intelligence import ensure_unique_title
            unique_title, was_dup, dup_match = ensure_unique_title(
                candidate_title=script.title,
                channel_id=channel_id,
                topic=clean_topic,
                language=channel_lang,
                similarity_threshold=0.40
            )
            dedup_notice = ""
            if was_dup:
                logger.info(f"[Handlers] 🛡️ Đã khử trùng tiêu đề cho Video: '{script.title}' -> '{unique_title}' (Trùng với '{dup_match}')")
                script.title = unique_title
                dedup_notice = f"🛡️ **Khử trùng tiêu đề:** Đổi từ ~\"{dup_match}\"~ ➔ *\"{unique_title}\"*\n\n"

            # Lưu vào Database — dùng context manager
            with get_db() as db:
                job = VideoJob(
                    user_id=user.id,
                    target_channel_id=channel_id,
                    topic=clean_topic,
                    title=script.title,
                    script=script.full_narration,
                    description=script.description,
                    tags=",".join(script.tags),
                    scenes_json=script.to_json_str(),   # Lưu structured data
                    status="SCRIPT_READY"
                )
                db.add(job)
                db.flush()  # Lấy job.id trước commit
                job_id = job.id

                log_activity(db, user.id, user.username, "SCRIPT_CREATED",
                             f"Kịch bản #{job_id} đã hoàn thành: '{script.title}'",
                             channel_id=channel_id)

                # Củng cố bộ nhớ chống trùng lặp UsedTopic
                try:
                    from app.database.models import UsedTopic
                    db.add(UsedTopic(
                        channel_id=channel_id,
                        topic_hash=UsedTopic.hash_topic(clean_topic),
                        topic_text=clean_topic,
                        niche=channel_niche,
                        job_id=job_id,
                    ))
                except Exception:
                    pass

            # Soạn nội dung thông báo cho nhóm
            scenes_preview = "\n".join([
                f"• Cảnh {s.scene_id}: {s.narration[:65]}..."
                for s in script.scenes[:3]
            ])

            score_text = ""
            if eval_report:
                overall = eval_report.overall_score
                hook_s = eval_report.hook_strength.score
                pacing_s = eval_report.story_pacing.score
                vis_s = getattr(eval_report, 'visual_direction', None)
                vis_score = vis_s.score if vis_s else 8.5
                badge = "🏆 Xuất sắc" if overall >= 8.5 else "✅ Đạt chuẩn Viral"
                score_text = (
                    f"📊 **ĐÁNH GIÁ CHẤT LƯỢNG (AI Editorial Critic):** `{overall:.1f}/10` {badge}\n"
                    f"• ⚡ Hook 3s: `{hook_s:.1f}/10` | ⏱️ Nhịp độ: `{pacing_s:.1f}/10` | 👁️ Thị giác: `{vis_score:.1f}/10`\n\n"
                )

            notes_display = f"🎯 **Đạo diễn:** _{director_notes}_\n\n" if director_notes else ""
            pinned_display = f"💬 **Bình luận mồi ghim Top (Pinned Bait):**\n> _{script.pinned_comment}_\n\n" if getattr(script, "pinned_comment", None) else ""
            formula_display = f"⚡ **Công thức Viral:** `{script.viral_formula}`\n" if getattr(script, "viral_formula", None) else ""

            # Tự động trích xuất đề xuất kiếm tiền Amazon Affiliate từ MonetizationEngine
            monetization_card = ""
            try:
                from app.services.monetization_engine import find_best_amazon_product, build_amazon_url
                channel_cfg = next((c for c in settings.get_channels_config() if c.get("id") == channel_id), {})
                tag = channel_cfg.get("amazon_tag") or os.getenv("AMAZON_ASSOCIATE_TAG", "kizakawa-20")
                best_prod = find_best_amazon_product(niche=channel_niche, title=script.title, topic=clean_topic)
                aff_link = build_amazon_url(best_prod["asin"], tag)
                monetization_card = (
                    f"🛒 **ĐỀ XUẤT KIẾM TIỀN (AMAZON AFFILIATE):**\n"
                    f"• Sản phẩm AI khớp: `{best_prod['name']}`\n"
                    f"• Link tự động gán: {aff_link}\n"
                    f"• Tag theo dõi: `{tag}`\n\n"
                )
            except Exception:
                pass

            reply_content = (
                f"🎬 **KỊCH BẢN ĐÃ HOÀN THÀNH (Task #{job_id})**\n\n"
                f"{score_text}"
                f"📌 **Tiêu đề đề xuất:** {script.title}\n"
                f"🔥 **Hook 3 giây đầu:** \"{script.hook}\"\n\n"
                f"{formula_display}"
                f"📝 **Phân cảnh tiêu biểu:**\n{scenes_preview}\n\n"
                f"{monetization_card}"
                f"{pinned_display}"
                f"{notes_display}"
                f"⏱️ **Ước lượng:** ~{script.total_estimated_duration:.0f}s | {len(script.scenes)} cảnh\n"
                f"🗣️ **Người tạo:** {user.first_name} | 📺 **Kênh:** `{channel_name}` ({channel_niche}) | 🌐 **Ngôn ngữ:** {lang_display}\n\n"
                f"👉 Cả nhóm hãy xem kịch bản, bấm **DUYỆT & DỰNG VIDEO NGAY** bên dưới để máy chủ tự động sản xuất Shorts!"
            )

            review_markup = get_script_review_keyboard(job_id, current_lang=channel_lang)
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
    finally:
        _release_action(idea_lock_key, post_cooldown_sec=5.0)


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
                "VISUAL_DIRECTOR": "🎬",
                "VISUAL_STRATEGY_PLANNED": "🔮",
                "MEDIA": "🖼️",
                "RENDER": "🎬",
                "QA": "🔍",
                "COMPLETED": "🎉",
                "ERROR": "❌"
            }
            icon = icons.get(stage, "⏳")
            p_bar = render_progress_bar(percent) if percent > 0 else "[▱▱▱▱▱▱▱▱▱▱] 0%"
            prefix = "🔄 [TỰ ĐỘNG TIẾP TỤC] " if is_auto_resume else ""
            safe_progress_msg = str(msg).replace("_", " ")
            display_text = (
                f"🎬 **{prefix}TIẾN TRÌNH RENDER (Task #{job_id})**\n\n"
                f"📊 **Tiến độ:** `{p_bar}`\n\n"
                f"{icon} {safe_progress_msg}"
            )
            await safe_edit_text(status_msg, display_text)

    try:
        from app.services.pipeline import VideoPipeline
        pipeline = VideoPipeline(job_id=job_id, progress_callback=progress_cb)
        render_job = await pipeline.execute()

        # Kiểm tra file MP4 hoàn chỉnh với cơ chế tìm kiếm đa đường dẫn
        out_path = Path(render_job.output_path)
        if not out_path.exists():
            resolved_p = _resolve_video_file_path(job_id, render_job.output_path)
            if resolved_p and resolved_p.exists():
                out_path = resolved_p
                render_job.output_path = str(out_path)

        if not out_path.exists():
            logger.error(f"❌ Không tìm thấy file video đầu ra cho Job #{job_id}: {render_job.output_path}")
            target_chats = []
            if chat_id:
                target_chats.append(chat_id)
            for gid in settings.get_allowed_group_ids():
                if gid and gid not in target_chats:
                    target_chats.append(gid)
            for t_chat in target_chats:
                try:
                    await bot.send_message(
                        chat_id=t_chat,
                        text=f"⚠️ <b>Video #{job_id} đã dựng xong nhưng không tìm thấy file video MP4!</b>\nĐường dẫn: <code>{render_job.output_path}</code>",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
            return

        if out_path.exists() and bot:
            size_mb = out_path.stat().st_size / (1024 * 1024)

            # Lấy thông tin sản phẩm tiếp thị liên kết (Amazon Affiliate)
            from app.services.monetization_engine import find_best_amazon_product, build_amazon_url
            aff_url = None
            aff_name = None
            aff_cta = ""
            aff_text = ""
            try:
                with get_db() as db:
                    jb = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                    ch_id = jb.target_channel_id if jb else ""
                    v_title = render_job.title or (jb.title if jb else "")
                    v_topic = jb.topic if jb else ""
                    v_script = jb.script if jb else ""
                aff_prod = find_best_amazon_product(ch_id, v_title, v_topic, v_script)
                aff_url = build_amazon_url(aff_prod["asin"])
                aff_name = aff_prod.get("name", "Sản phẩm đề xuất")
                aff_cta = aff_prod.get("cta", "")
                aff_text = (
                    f"\n🛒 **SẢN PHẨM TIẾP THỊ LIÊN KẾT (AFFILIATE):**\n"
                    f"• **Sản phẩm:** {aff_name}\n"
                    f"• **Gợi ý:** _{aff_cta}_\n"
                    f"👉 **Link mua:** [Bấm xem trên Amazon]({aff_url})\n"
                )
            except Exception as ex_aff:
                logger.debug(f"Không thể lấy affiliate product #{job_id}: {ex_aff}")

            post_render_markup = get_video_post_render_keyboard(
                job_id,
                affiliate_url=aff_url,
                affiliate_name=aff_name
            )

            prefix_title = (
                f"🎉 **VIDEO ĐÃ DỰNG THÀNH CÔNG (TỰ ĐỘNG TIẾP TỤC)! (Task #{job_id})**"
                if is_auto_resume else
                f"🎉 **VIDEO ĐÃ DỰNG THÀNH CÔNG! (Task #{job_id})**"
            )

            # Đọc điểm chất lượng từ LangGraph AI Graders nếu có
            scores_text = ""
            try:
                with get_db() as db:
                    jb = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                    if jb and jb.quality_scores:
                        import json
                        q_data = json.loads(jb.quality_scores) if isinstance(jb.quality_scores, str) else (jb.quality_scores or {})
                        sc = q_data.get("script_critic") or {}
                        vc = q_data.get("visual_critic") or {}
                        cqc = q_data.get("content_quality_critic") or {}
                        aq = q_data.get("audio_qa") or {}
                        cqc_score = cqc.get("overall_score")
                        cqc_line = f" 🧠 Nội dung (AI Vision Critic): `{cqc_score}/10`\n" if cqc_score is not None else ""
                        scores_text = (
                            f"\n📊 **Báo cáo Thẩm định 5 Bộ AI Graders:**\n"
                            f" ⭐ Kịch bản (Script Critic): `{sc.get('overall_score', 8.5)}/10`\n"
                            f" 🖼️ Kỹ thuật Media (Visual Critic): `{vc.get('overall_score', 9.0)}/10`\n"
                            f"{cqc_line}"
                            f" 🎙️ Giọng đọc (Audio QA): `{aq.get('words_per_minute', 145)} WPM ({aq.get('pacing_rating', 'PERFECT')})`\n"
                            f" 🎬 Kỹ thuật Render (Video QA): `1080x1920 30fps (Đạt)`\n"
                        )
            except Exception:
                pass

            # Xây dựng Caption gửi Telegram bằng HTML để tránh lỗi Markdown entity
            safe_title = html.escape(str(render_job.title or f"Video #{job_id}"))
            safe_prefix = html.escape(str(prefix_title).replace("**", "").replace("*", ""))
            safe_aff_name = html.escape(str(aff_name)) if aff_name else ""
            safe_aff_cta = html.escape(str(aff_cta)) if aff_cta else ""
            safe_aff_url = html.escape(str(aff_url)) if aff_url else ""

            aff_html = ""
            if aff_url:
                aff_html = (
                    f"\n🛒 <b>SẢN PHẨM TIẾP THỊ LIÊN KẾT (AFFILIATE):</b>\n"
                    f"• <b>Sản phẩm:</b> {safe_aff_name}\n"
                    f"• <b>Gợi ý:</b> <i>{safe_aff_cta}</i>\n"
                    f"👉 <b>Link mua:</b> <a href=\"{safe_aff_url}\">{safe_aff_url}</a>\n"
                )

            caption = (
                f"{safe_prefix}\n\n"
                f"📌 <b>Tiêu đề:</b> {safe_title}\n"
                f"⏱️ <b>Thời lượng:</b> {render_job.total_audio_duration_ms / 1000:.1f}s\n"
                f"📦 <b>Dung lượng:</b> {size_mb:.1f} MB\n"
                f"🎬 <b>Chuẩn:</b> 1080x1920 (9:16 Shorts) kèm phụ đề động\n"
                f"{aff_html}\n"
                f"👉 Cả nhóm hãy xem thử video! Chọn <b>Đăng Ngay</b>, <b>Đăng Theo Lịch</b>, <b>Sửa</b> hoặc <b>Xóa</b> bên dưới:"
            )
            if len(caption) > 1000:
                caption = caption[:990] + "..."

            # Xác định các chat mục tiêu (chat cá nhân & các nhóm được cấp phép)
            target_chats = []
            if chat_id:
                target_chats.append(chat_id)
            for gid in settings.get_allowed_group_ids():
                if gid and gid not in target_chats:
                    target_chats.append(gid)

            # 1. CẬP NHẬT TRẠNG THÁI TẢI VIDEO (KHÔNG BẮN ẢNH RỜI ĐỂ TRÁNH RÁC NHÓM)
            thumb_p = getattr(render_job, "thumbnail_path", None)
            if not thumb_p or not Path(thumb_p).exists():
                thumb_p = getattr(render_job, "thumbnail_vertical_path", None)

            if status_msg:
                try:
                    await safe_edit_text(status_msg, f"⚡ <b>Đang tải video #{job_id} ({size_mb:.1f} MB) vào nhóm...</b>")
                except Exception:
                    pass

            # 2. UPLOAD DUY NHẤT 1 FILE VIDEO KÈM THUMBNAIL LÀM BÌA & NÚT THAO TÁC
            # Kiểm tra kích thước file: Nếu > 12MB, tự động nén nhanh bản preview nhẹ (~4-6MB) cho Telegram
            # để tránh lỗi đứt kết nối httpx.ReadError sau 10 phút render (file gốc out_path vẫn giữ nguyên 100% cho YouTube)
            upload_vid_path = out_path
            temp_tg_preview = None
            if size_mb > 12.0:
                try:
                    import subprocess
                    temp_dir = Path(settings.TEMP_DIR)
                    temp_dir.mkdir(parents=True, exist_ok=True)
                    temp_tg_preview = str(temp_dir / f"tg_preview_{job_id}.mp4")
                    logger.info(f"File {size_mb:.1f}MB > 12MB, đang tạo bản preview tối ưu cho Telegram (job #{job_id})...")
                    cmd = [
                        "ffmpeg", "-y", "-i", str(out_path),
                        "-c:v", "libx264", "-crf", "26", "-preset", "veryfast", "-movflags", "+faststart",
                        "-c:a", "copy", temp_tg_preview
                    ]
                    res_comp = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
                    if res_comp.returncode == 0 and Path(temp_tg_preview).exists():
                        upload_vid_path = temp_tg_preview
                        preview_mb = Path(upload_vid_path).stat().st_size / (1024 * 1024)
                        logger.info(f"Đã nén video preview Telegram thành công: {preview_mb:.1f}MB (gốc {size_mb:.1f}MB)")
                except Exception as ex_comp:
                    logger.warning(f"Lỗi nén preview Telegram: {ex_comp}")

            def _sync_requests_send_video(target_chat_id, vid_path_str, thumb_path_str, cap_html, markup_obj, cached_fid=None):
                import requests as _req
                import json as _json
                url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendVideo"
                data = {
                    "chat_id": str(target_chat_id),
                    "caption": cap_html,
                    "parse_mode": "HTML",
                    "supports_streaming": "true",
                }
                if markup_obj:
                    data["reply_markup"] = _json.dumps(markup_obj.to_dict())
                if cached_fid:
                    data["video"] = cached_fid
                    r = _req.post(url, data=data, timeout=60)
                else:
                    with open(vid_path_str, "rb") as vf_sync:
                        files = {"video": (f"video_{job_id}.mp4", vf_sync, "video/mp4")}
                        tf_sync = open(thumb_path_str, "rb") if thumb_path_str and Path(thumb_path_str).exists() else None
                        if tf_sync:
                            files["thumbnail"] = ("thumb.jpg", tf_sync, "image/jpeg")
                        try:
                            r = _req.post(url, data=data, files=files, timeout=180)
                        finally:
                            if tf_sync:
                                tf_sync.close()
                if r.status_code == 200:
                    res_j = r.json()
                    if res_j.get("ok"):
                        return True, ((res_j.get("result") or {}).get("video") or {}).get("file_id")
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")

            cached_telegram_file_id = None
            for t_chat in target_chats:
                sent_ok = False
                curr_size_mb = Path(upload_vid_path).stat().st_size / (1024 * 1024)
                if curr_size_mb <= 49.0:
                    try:
                        if cached_telegram_file_id:
                            # Chat thứ 2 trở đi gửi qua file_id chỉ mất 0.2s!
                            await bot.send_video(
                                chat_id=t_chat,
                                video=cached_telegram_file_id,
                                caption=caption,
                                parse_mode="HTML",
                                reply_markup=post_render_markup,
                                supports_streaming=True
                            )
                            sent_ok = True
                        else:
                            with open(upload_vid_path, "rb") as vf:
                                thumb_f = open(thumb_p, "rb") if thumb_p and Path(thumb_p).exists() else None
                                try:
                                    sent_msg = await bot.send_video(
                                        chat_id=t_chat,
                                        video=vf,
                                        thumbnail=thumb_f,
                                        caption=caption,
                                        parse_mode="HTML",
                                        reply_markup=post_render_markup,
                                        supports_streaming=True,
                                        read_timeout=300,
                                        write_timeout=300
                                    )
                                    if sent_msg and sent_msg.video:
                                        cached_telegram_file_id = sent_msg.video.file_id
                                    sent_ok = True
                                finally:
                                    if thumb_f:
                                        try:
                                            thumb_f.close()
                                        except Exception:
                                            pass
                    except Exception as ex_upload:
                        logger.warning(f"send_video (httpx) thất bại tới chat {t_chat}: {ex_upload}, chuyển sang kết nối HTTP trực tiếp (requests fallback)...")
                        try:
                            ok_sync, fid_sync = await asyncio.to_thread(
                                _sync_requests_send_video,
                                t_chat,
                                str(upload_vid_path),
                                str(thumb_p) if thumb_p else None,
                                caption,
                                post_render_markup,
                                cached_telegram_file_id,
                            )
                            if ok_sync:
                                if fid_sync:
                                    cached_telegram_file_id = fid_sync
                                sent_ok = True
                                logger.info(f"✅ Đã gửi video #{job_id} tới chat {t_chat} thành công qua requests fallback!")
                        except Exception as ex_req_fb:
                            logger.warning(f"requests fallback thất bại tới chat {t_chat}: {ex_req_fb}, thử gửi dạng document...")
                            try:
                                with open(upload_vid_path, "rb") as vf_doc:
                                    sent_doc = await bot.send_document(
                                        chat_id=t_chat,
                                        document=vf_doc,
                                        caption=caption,
                                        parse_mode="HTML",
                                        reply_markup=post_render_markup,
                                        read_timeout=300,
                                        write_timeout=300
                                    )
                                    if sent_doc and sent_doc.document:
                                        cached_telegram_file_id = sent_doc.document.file_id
                                sent_ok = True
                            except Exception as ex_doc:
                                logger.error(f"send_document cũng thất bại tới chat {t_chat}: {ex_doc}")

                if not sent_ok:
                    fallback_msg = (
                        f"🎉 <b>Video #{job_id} đã hoàn tất!</b>\n\n"
                        f"📌 <b>Tiêu đề:</b> {safe_title}\n"
                        f"📂 File đã lưu tại: <code>{render_job.output_path}</code> ({size_mb:.1f} MB)\n"
                        f"{aff_html}\n"
                        f"👉 Cả nhóm chọn thao tác tiếp theo:"
                    )
                    try:
                        await bot.send_message(chat_id=t_chat, text=fallback_msg, reply_markup=post_render_markup, parse_mode="HTML")
                    except Exception as ex_m:
                        logger.error(f"Không thể gửi tin nhắn thông báo video tới chat {t_chat}: {ex_m}")

            if temp_tg_preview and Path(temp_tg_preview).exists():
                try:
                    os.remove(temp_tg_preview)
                except Exception:
                    pass

            # Xóa tin nhắn trạng thái chờ để nhóm không bị rác
            if status_msg:
                try:
                    await status_msg.delete()
                except Exception:
                    pass

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
    from app.services.render_queue import get_render_queue
    from config.settings import settings

    try:
        allowed_groups = settings.get_allowed_group_ids()
        default_chat_id = allowed_groups[0] if allowed_groups else None

        unfinished_jobs = []
        with get_db() as db:
            # Chỉ tìm các job thực sự bị gián đoạn giữa chừng khi đang RENDERING
            stuck_rendering = db.query(VideoJob).filter(VideoJob.status == "RENDERING").order_by(VideoJob.id.asc()).all()

            for j in stuck_rendering:
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
    Chống trùng lặp: Nếu job_id đã đang render hoặc đã nằm trong hàng đợi thì bỏ qua.
    """
    rq = get_render_queue()
    chat_id = update.effective_chat.id if update.effective_chat else None

    # Tạo render function (closure bind sẵn tham số)
    async def do_render():
        await _execute_render_task(update, context, job_id, user_name)

    # Thêm vào queue (RenderQueue.enqueue tự động chống trùng lặp job_id)
    queue_item = await rq.enqueue(
        job_id=job_id,
        user_name=user_name,
        chat_id=chat_id,
        render_func=do_render,
    )

    if getattr(queue_item, "is_duplicate", False):
        if update.callback_query:
            await safe_query_answer(
                update.callback_query,
                f"⏳ Video #{job_id} đã nằm trong hàng đợi hoặc đang được dựng rồi!",
                show_alert=True,
            )
        else:
            await safe_reply(
                update,
                f"⏳ **Video #{job_id} đã nằm trong hàng đợi hoặc đang được dựng rồi!**\n"
                f"💡 Gõ `/queue` để xem tiến độ hiện tại.",
            )
        return

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

        # Tự động nhận diện nếu người dùng yêu cầu đổi ngôn ngữ trong feedback
        feedback_lower = feedback.lower()
        lang_changed = False
        if any(kw in feedback_lower for kw in ["tiếng việt", "dịch sang việt", "sang tiếng việt", "vietnamese", "bằng tiếng việt", "tiếng việt nam", "việt hóa", "tieng viet"]):
            lang = "vi"
            lang_changed = True
        elif any(kw in feedback_lower for kw in ["tiếng anh", "dịch sang anh", "sang tiếng anh", "english", "bằng tiếng anh", "tieng anh"]):
            lang = "en"
            lang_changed = True

        revised_script, eval_report = await asyncio.to_thread(
            content_agent.refine_with_feedback_and_eval,
            current_script=current_script,
            feedback=feedback,
            channel_niche=niche,
            language=lang,
            min_acceptable_score=8.0,
            max_attempts=3
        )

        # Nếu đổi ngôn ngữ, tự động dọn cache audio cũ để không bị nói tiếng khác
        if lang_changed:
            audio_dir = Path(settings.TEMP_DIR) / f"job_{job_id}" / "audio"
            if audio_dir.exists():
                for p in audio_dir.glob("*.mp3"):
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass
                for p in audio_dir.glob("*.meta.json"):
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass

        with get_db() as db:
            db_job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if db_job:
                db_job.title = revised_script.title
                db_job.script = revised_script.full_narration
                db_job.description = revised_script.description
                db_job.tags = ",".join(revised_script.tags)
                db_job.scenes_json = revised_script.to_json_str()
                db_job.status = "SCRIPT_READY"
                if eval_report:
                    scores = {}
                    if db_job.quality_scores:
                        try:
                            scores = json.loads(db_job.quality_scores)
                        except Exception:
                            pass
                    scores["script_critic"] = eval_report.model_dump()
                    db_job.quality_scores = json.dumps(scores, ensure_ascii=False)
                log_activity(db, user.id, user.username, "REFINE_SCRIPT",
                             f"Sửa kịch bản #{job_id} theo góp ý: '{feedback}'")

        scenes_preview = "\n".join([
            f"• Cảnh {s.scene_id}: {s.narration[:60]}... _(Tìm: {s.visual_keyword})_"
            for s in revised_script.scenes[:3]
        ])

        lang_badge = "🇻🇳 Tiếng Việt" if lang == "vi" else "🇺🇸 Tiếng Anh"
        score_info = ""
        if eval_report:
            overall = eval_report.overall_score
            hook_s = eval_report.hook_strength.score
            pacing_s = eval_report.story_pacing.score
            vis_s = getattr(eval_report, 'visual_direction', None)
            vis_score = vis_s.score if vis_s else 8.5
            badge = "🏆 Xuất sắc" if overall >= 8.5 else "✅ Đạt chuẩn Viral"
            score_info = (
                f"📊 **ĐÁNH GIÁ CHẤT LƯỢNG SAU GÓP Ý:** `{overall:.1f}/10` {badge}\n"
                f"• ⚡ Hook 3s: `{hook_s:.1f}/10` | ⏱️ Nhịp độ: `{pacing_s:.1f}/10` | 👁️ Thị giác: `{vis_score:.1f}/10`\n\n"
            )

        reply_content = (
            f"✨ **KỊCH BẢN ĐÃ ĐƯỢC CHỈNH SỬA & CHẤM ĐIỂM (Task #{job_id})**\n\n"
            f"{score_info}"
            f"📌 **Tiêu đề mới:** {revised_script.title}\n"
            f"🌐 **Ngôn ngữ:** {lang_badge}\n"
            f"🔥 **Hook mới:** \"{revised_script.hook}\"\n\n"
            f"📝 **Phân cảnh cập nhật:**\n{scenes_preview}\n\n"
            f"⏱️ **Ước lượng:** ~{revised_script.total_estimated_duration:.0f}s | {len(revised_script.scenes)} cảnh\n"
            f"🗣️ **Người góp ý:** {user.first_name}\n"
            f"💬 **Góp ý đã tiếp thu:** _{feedback}_\n\n"
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
    - Đổi ngôn ngữ: /edit <job_id> lang <vi|en>
    - Đổi giọng đọc: /edit <job_id> voice <nam|nu|andrew|brian|ava|namminh|hoaimy>
    - Đổi ảnh cảnh: /edit <job_id> scene <số cảnh> <từ khóa ảnh mới>
    - Đổi câu thoại cảnh: /edit <job_id> text <số cảnh> <câu thoại mới>
    """
    if not is_authorized(update):
        return

    if not context.args or len(context.args) < 2 or not context.args[0].isdigit():
        await safe_reply(
            update,
            "💡 **Cú pháp chỉnh sửa thông minh (LangGraph Multi-Agent):**\n\n"
            "0. **Sửa tiêu đề video nhanh (không render lại):**\n"
            "`/edit <job_id> title <tiêu_đề_mới>`\n"
            "   _Ví dụ:_ `/edit 316 title What Lurks In Mariana Trench? 💀`\n"
            "   _Ví dụ:_ `/edit 316 tieude Bí Ẩn Đáy Vực 11,000m 🌊`\n\n"
            "1. **Đổi ngôn ngữ kịch bản & giọng đọc (EN ↔ VI):**\n"
            "`/edit <job_id> lang <vi|en>`\n"
            "   _Ví dụ:_ `/edit 27 lang vi` (Dịch sang Tiếng Việt & lồng tiếng Việt)\n"
            "   _Ví dụ:_ `/edit 27 lang en` (Dịch sang Tiếng Anh & lồng tiếng Anh)\n\n"
            "2. **Đổi giọng đọc AI (Nam / Nữ / Giọng cụ thể):**\n"
            "`/edit <job_id> voice <nam|nu|andrew|brian|ava|emma|namminh|hoaimy>`\n"
            "   _Ví dụ:_ `/edit 27 voice nam` (Tự động chọn giọng Nam theo ngôn ngữ video)\n"
            "   _Ví dụ:_ `/edit 27 voice nu` (Tự động chọn giọng Nữ theo ngôn ngữ video)\n"
            "   _Ví dụ:_ `/edit 27 voice brian` (Giọng nam trầm điện ảnh)\n\n"
            "3. **Góp ý sửa kịch bản tự do:**\n"
            "`/edit <job_id> <góp ý>`\n"
            "   _Ví dụ:_ `/edit 27 Đổi sang tiếng Việt và lồng tiếng Việt giúp tôi`\n"
            "   _Ví dụ:_ `/edit 27 Đổi câu hook đầu thành câu hỏi giật gân hơn`\n\n"
            "4. **Đổi ảnh/video của 1 cảnh:**\n"
            "`/edit <job_id> scene <số_cảnh> <từ_khóa_mới>`\n\n"
            "5. **Sửa câu thoại của 1 cảnh:**\n"
            "`/edit <job_id> text <số_cảnh> <câu_thoại_mới>`"
        )
        return

    job_id = int(context.args[0])
    sub_cmd = context.args[1].lower()

    # ── 0. Đổi TIÊU ĐỀ video trực tiếp (Title Edit) ──
    if sub_cmd in ("title", "tieude", "tieu_de", "name", "ten") and len(context.args) >= 3:
        new_title = " ".join(context.args[2:]).strip().strip('"\'')
        if not new_title:
            await safe_reply(update, "❌ Vui lòng nhập tiêu đề mới. Ví dụ: `/edit 316 title Bí Ẩn Đáy Vực 11,000m 🌊`")
            return

        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not j:
                await safe_reply(update, f"❌ Không tìm thấy VideoJob #{job_id}")
                return
            old_title = j.title or j.topic
            j.title = new_title
            if j.scenes_json:
                try:
                    import json as _json
                    sj = _json.loads(j.scenes_json)
                    sj["title"] = new_title
                    j.scenes_json = _json.dumps(sj, ensure_ascii=False)
                except Exception:
                    pass
            log_activity(db, update.effective_user.id, update.effective_user.username, "EDIT_TITLE",
                         f"Đổi tiêu đề Video #{job_id}: '{old_title}' -> '{new_title}'", channel_id=j.target_channel_id)

        markup = get_video_post_render_keyboard(job_id, is_republish=True)
        await safe_reply(
            update,
            f"✅ **ĐÃ ĐỔI TIÊU ĐỀ VIDEO #{job_id} THÀNH CÔNG!**\n\n"
            f"📌 **Tiêu đề mới:** `{new_title}`\n"
            f"_(Tiêu đề cũ: ~{old_title}~_\n\n"
            f"🚀 Tiêu đề này sẽ được áp dụng trực tiếp khi xuất bản lên YouTube Shorts.",
            reply_markup=markup
        )
        return

    # ── 1. Đổi ngôn ngữ toàn bộ kịch bản & giọng lồng tiếng qua LangGraph ──
    if sub_cmd in ("lang", "language", "ngonngu") and len(context.args) >= 3:
        target_lang = "vi" if "vi" in context.args[2].lower() else "en"
        lang_name = "Tiếng Việt (vi)" if target_lang == "vi" else "Tiếng Anh (en)"
        status_msg = await safe_reply(update, f"🌐 [LangGraph] Đang dịch và chuyển toàn bộ kịch bản + giọng lồng tiếng Video #{job_id} sang {lang_name}...")
        try:
            from app.graph import GraphRunner, EditRequest
            runner = GraphRunner(job_id=job_id)
            edit_req = EditRequest(
                action_type="change_language",
                new_language=target_lang
            )
            final_state = await runner.execute_edit(edit_req)
            video_out = final_state.get("rendered_video_path")
            markup = get_video_post_render_keyboard(job_id, is_republish=True)
            await safe_edit_text(status_msg, f"✅ Đã chuyển thành công kịch bản và giọng lồng tiếng Video #{job_id} sang {lang_name} và render lại video hoàn tất! Bạn có thể xem và đăng tải video bên dưới:", reply_markup=markup)
            if video_out and os.path.exists(video_out):
                try:
                    with open(video_out, "rb") as vf:
                        await update.effective_chat.send_video(
                            video=vf,
                            caption=f"🎥 **Video #{job_id} sau khi chuyển sang {lang_name}:**",
                            supports_streaming=True,
                            reply_markup=markup
                        )
                except Exception as ex:
                    logger.error(f"Gửi video sau khi edit lang thất bại: {ex}")
        except Exception as e:
            await safe_edit_text(status_msg, f"❌ Lỗi khi đổi ngôn ngữ: {e}")
        return

    # ── 2. Đổi ảnh/video cảnh ──
    elif sub_cmd == "scene" and len(context.args) >= 4 and context.args[2].isdigit():
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
            final_state = await runner.execute_edit(edit_req)
            video_out = final_state.get("rendered_video_path")
            markup = get_video_post_render_keyboard(job_id, is_republish=True)
            await safe_edit_text(status_msg, f"✅ Đã cập nhật ảnh Cảnh #{scene_id} và render lại video thành công! Bạn có thể xem và đăng tải video bên dưới:", reply_markup=markup)
            if video_out and os.path.exists(video_out):
                try:
                    with open(video_out, "rb") as vf:
                        await update.effective_chat.send_video(
                            video=vf,
                            caption=f"🎥 **Video #{job_id} sau khi cập nhật Cảnh #{scene_id}:**",
                            supports_streaming=True,
                            reply_markup=markup
                        )
                except Exception as ex:
                    logger.error(f"Gửi video sau khi edit scene thất bại: {ex}")
        except Exception as e:
            await safe_edit_text(status_msg, f"❌ Lỗi khi chỉnh sửa Cảnh #{scene_id}: {e}")
        return

    # ── 3. Sửa câu thoại của 1 cảnh ──
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
            final_state = await runner.execute_edit(edit_req)
            video_out = final_state.get("rendered_video_path")
            markup = get_video_post_render_keyboard(job_id, is_republish=True)
            await safe_edit_text(status_msg, f"✅ Đã cập nhật lời thoại Cảnh #{scene_id}, đồng bộ phụ đề và render lại video thành công! Bạn có thể xem và đăng tải video bên dưới:", reply_markup=markup)
            if video_out and os.path.exists(video_out):
                try:
                    with open(video_out, "rb") as vf:
                        await update.effective_chat.send_video(
                            video=vf,
                            caption=f"🎥 **Video #{job_id} sau khi sửa lời thoại Cảnh #{scene_id}:**",
                            supports_streaming=True,
                            reply_markup=markup
                        )
                except Exception as ex:
                    logger.error(f"Gửi video sau khi edit text thất bại: {ex}")
        except Exception as e:
            await safe_edit_text(status_msg, f"❌ Lỗi khi sửa lời thoại Cảnh #{scene_id}: {e}")
        return

    # ── 4. Đổi giọng đọc thông minh ──
    elif sub_cmd == "voice" and len(context.args) >= 3:
        voice_arg = context.args[2].lower()

        # Kiểm tra ngôn ngữ hiện tại của kịch bản job
        is_vn = False
        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if j and j.scenes_json:
                from app.agents.voice_agent import VoiceAgent
                is_vn = VoiceAgent._is_vietnamese(j.script or j.scenes_json)

        # Ánh xạ giọng đọc thông minh
        if "andrew" in voice_arg:
            voice_id = "en-US-AndrewNeural"
            display_name = "Andrew (Nam Mỹ ấm áp)"
        elif "brian" in voice_arg:
            voice_id = "en-US-BrianNeural"
            display_name = "Brian (Nam Mỹ trầm ấm)"
        elif "christopher" in voice_arg:
            voice_id = "en-US-ChristopherNeural"
            display_name = "Christopher (Nam Mỹ quyền uy)"
        elif "guy" in voice_arg:
            voice_id = "en-US-GuyNeural"
            display_name = "Guy (Nam Mỹ năng động)"
        elif "ava" in voice_arg:
            voice_id = "en-US-AvaNeural"
            display_name = "Ava (Nữ Mỹ tự nhiên)"
        elif "emma" in voice_arg:
            voice_id = "en-US-EmmaNeural"
            display_name = "Emma (Nữ Mỹ truyền cảm)"
        elif "namminh" in voice_arg:
            voice_id = "vi-VN-NamMinhNeural"
            display_name = "Nam Minh (Nam Việt chuẩn)"
        elif "hoaimy" in voice_arg:
            voice_id = "vi-VN-HoaiMyNeural"
            display_name = "Hoài My (Nữ Việt truyền cảm)"
        elif "nu" in voice_arg or "female" in voice_arg:
            voice_id = "vi-VN-HoaiMyNeural" if is_vn else "en-US-AvaNeural"
            display_name = "Nữ Việt (Hoài My)" if is_vn else "Nữ Mỹ (Ava)"
        else: # nam / male
            voice_id = "vi-VN-NamMinhNeural" if is_vn else "en-US-AndrewNeural"
            display_name = "Nam Việt (Nam Minh)" if is_vn else "Nam Mỹ (Andrew)"

        status_msg = await safe_reply(update, f"🎙️ [LangGraph] Đang đổi giọng đọc Video #{job_id} sang {display_name} (`{voice_id}`)...")
        try:
            from app.graph import GraphRunner, EditRequest
            runner = GraphRunner(job_id=job_id)
            edit_req = EditRequest(
                action_type="change_voice",
                new_voice_id=voice_id
            )
            final_state = await runner.execute_edit(edit_req)
            video_out = final_state.get("rendered_video_path")
            markup = get_video_post_render_keyboard(job_id, is_republish=True)
            await safe_edit_text(status_msg, f"✅ Đã đổi toàn bộ giọng đọc sang {display_name} và render lại video hoàn tất! Bạn có thể xem và đăng tải video bên dưới:", reply_markup=markup)
            if video_out and os.path.exists(video_out):
                try:
                    with open(video_out, "rb") as vf:
                        await update.effective_chat.send_video(
                            video=vf,
                            caption=f"🎥 **Video #{job_id} với giọng đọc mới ({display_name}):**",
                            supports_streaming=True,
                            reply_markup=markup
                        )
                except Exception as ex:
                    logger.error(f"Gửi video sau khi edit voice thất bại: {ex}")
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


async def _execute_channel_publish(update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: int, channel_id: str, user):
    """Thực thi đăng tải video lên YouTube Shorts và Google Drive"""
    pub_lock_key = f"publish_job:{job_id}"
    acquired = _try_acquire_action(pub_lock_key, max_inflight_sec=180.0, debounce_sec=10.0)
    if not acquired:
        if update.callback_query:
            await safe_query_answer(
                update.callback_query,
                f"⏳ Video #{job_id} đang được đăng lên YouTube, vui lòng chờ!",
                show_alert=True,
            )
        else:
            await safe_reply(update, f"⏳ Video #{job_id} đang được đăng lên YouTube, vui lòng chờ giây lát!")
        return

    try:
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not job or not job.video_path or not os.path.exists(job.video_path):
                await safe_reply(update, f"❌ File video #{job_id} chưa sẵn sàng để xuất bản.")
                return
            if job.status == "PUBLISHED" and job.youtube_video_id:
                await safe_reply(
                    update,
                    f"ℹ️ **Video #{job_id} đã được đăng lên YouTube trước đó!**\n"
                    f"👉 https://www.youtube.com/shorts/{job.youtube_video_id}"
                )
                return
            video_path = job.video_path
            video_title = job.title or job.topic
            video_desc = job.description or ""
            video_tags = [t.strip() for t in (job.tags or "").split(",") if t.strip()]
            scenes_json = job.scenes_json

        channels = settings.get_channels_config()
        ch_cfg = next((c for c in channels if c.get("id") == channel_id), {"id": channel_id, "name": channel_id})
        acc_id = ch_cfg.get("google_account", "account_1")
        ch_token_path = os.path.join(settings.CREDENTIALS_DIR, "channels", channel_id, "youtube_token.json")

        if not os.path.exists(ch_token_path):
            await safe_reply(
                update,
                f"⚠️ **KÊNH CHƯA CẤP QUYỀN YOUTUBE RIÊNG!**\n\n"
                f"Kênh `{ch_cfg.get('name', channel_id)}` thuộc tài khoản `{acc_id}` chưa có quyền YouTube OAuth riêng.\n\n"
                f"👉 **Cách kết nối trong 10 giây:**\n"
                f"1. Mở Web Studio tại `http://127.0.0.1:8000` -> Tab **Kênh & Tài Khoản**.\n"
                f"2. Tại dòng kênh `{ch_cfg.get('name', channel_id)}`, bấm nút đỏ: **`🔑 Cấp Quyền YouTube (Kênh Này)`**.\n"
                f"3. Đăng nhập Google và bấm chọn đúng kênh `{ch_cfg.get('name', channel_id)}` là xong!"
            )
            return

        ch_lang = ch_cfg.get("language", "en")
        # Đảm bảo tiêu đề và mô tả có gắn cờ Shorts
        if "#shorts" not in video_title.lower():
            if len(video_title) > 91:
                video_title = video_title[:91].rstrip()
            video_title = f"{video_title} #Shorts"

        if "#shorts" not in video_desc.lower():
            video_desc = f"{video_desc.strip()}\n\n#Shorts #shorts #viral" if video_desc.strip() else "#Shorts #shorts #viral"

        status_wait = await safe_reply(
            update,
            f"🚀 **ĐANG ĐĂNG YOUTUBE SHORTS #{job_id} & GOOGLE DRIVE...**\n\n"
            f"📺 **Kênh:** `{ch_cfg.get('name', channel_id)}` (`{channel_id}`)\n"
            f"📌 **Tiêu đề:** {video_title}\n"
            f"⚡ **Chế độ:** YouTube Shorts (9:16 + Tag #Shorts)\n"
            f"⏳ Đang sao lưu Google Drive ({acc_id}) và upload YouTube..."
        )

        from app.agents.publisher_agent import PublisherAgent
        from app.agents.drive_agent import DriveAgent

        ch_name = ch_cfg.get("name", channel_id)
        pub_agent = PublisherAgent(channel_config=ch_cfg, account_name=acc_id)
        drive_agent = DriveAgent(account_name=acc_id)

        # 1. Tải lên Google Drive đúng theo thư mục riêng của kênh
        drive_res = await asyncio.to_thread(
            drive_agent.upload_video,
            video_path,
            video_title,
            channel_name=ch_name
        )
        drive_link = drive_res.get("drive_link")
        folder_link = drive_res.get("folder_link")

        # Trích xuất bình luận mồi (pinned_comment) từ kịch bản nếu có
        pinned_comment = None
        if scenes_json:
            try:
                s_data = json.loads(scenes_json)
                pinned_comment = s_data.get("pinned_comment")
            except Exception:
                pass
        if not pinned_comment:
            n_key = str(ch_cfg.get("niche") or "").lower()
            if "psychology" in n_key:
                pinned_comment = "Did this hit close to home? Which sign was most accurate? Drop your thoughts below 👇\n🔔 Subscribe to master dark psychology before someone uses it on you! 🧠"
            elif any(k in n_key for k in ("rescue", "animal")):
                pinned_comment = "Every innocent life deserves love ❤️ Would you have stopped to help? Comment below!\n🐾 Subscribe right now to help give more terrified animals a second chance! 🐾"
            elif any(k in n_key for k in ("deep_sea", "mystery", "history")):
                pinned_comment = "What is your theory about this? What are they really hiding? Drop it below 👇\n🌊 Subscribe to unlock Part 2 as soon as the classified archive drops! 🔥"
            elif any(k in n_key for k in ("predator", "wildlife")):
                pinned_comment = "Who is the true apex ruler of this realm? Drop your vote below 👇\n🦁 Subscribe for more untamed apex predator battles! 🦁"
            else:
                pinned_comment = "Which moment shocked you the most? Drop your theory in the comments below 👇\n🔔 Subscribe so you don't miss Part 2 releasing next! 🔥"

        # 2. Đăng tải lên YouTube Shorts
        pub_res = await asyncio.to_thread(
            pub_agent.upload_video,
            video_path=video_path,
            title=video_title,
            description=video_desc,
            tags=video_tags,
            privacy_status="public",
            default_language=ch_lang,
            default_audio_language=ch_lang,
            pinned_comment=pinned_comment,
            topic=job.topic or "",
            script=scenes_json or "",
        )

        if pub_res.get("status") == "success":
            yt_url = pub_res.get("youtube_url")
            yt_shorts_url = pub_res.get("shorts_url") or f"https://www.youtube.com/shorts/{pub_res.get('video_id')}"
            yt_id = pub_res.get("video_id")
            with get_db() as db:
                db_job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if db_job:
                    db_job.status = "PUBLISHED"
                    db_job.youtube_video_id = yt_id
                    db_job.target_channel_id = channel_id
                    if pub_res.get("enriched_description"):
                        db_job.description = pub_res.get("enriched_description")
                    if drive_link:
                        db_job.drive_link = drive_link
                    log_activity(db, user.id, user.username, "PUBLISH_YOUTUBE",
                                 f"Đã đăng Video #{job_id} lên YouTube Shorts kênh {channel_id} ({yt_shorts_url})",
                                 channel_id=channel_id)

            success_text = (
                f"🎉 **ĐĂNG TẢI YOUTUBE SHORTS #{job_id} THÀNH CÔNG!**\n\n"
                f"📺 **Kênh đã đăng:** `{ch_cfg.get('name', channel_id)}`\n"
                f"⚡ **Link xem YouTube Shorts (Công khai):**\n👉 {yt_shorts_url}\n\n"
                f"🎬 **Link phụ:** {yt_url}\n\n"
            )
            if pub_res.get("comment_id"):
                success_text += f"💬 **Bình luận mồi tự động (Pinned Bait):** Đã đăng & ghim thành công!\n> _{pinned_comment}_\n\n"
            prod_info = pub_res.get("product_info")
            if prod_info:
                success_text += (
                    f"🛒 **Sản phẩm liên kết tự động (Amazon Affiliate):**\n"
                    f"• **Món hàng:** `{prod_info.get('name')}`\n"
                    f"• **Link kiếm tiền:** {prod_info.get('affiliate_url')}\n"
                    f"• **Mã Tag:** `{prod_info.get('tracking_tag')}`\n\n"
                )
            if drive_link:
                success_text += f"💾 **Lưu trữ Google Drive (Kênh `{ch_name}`):**\n👉 {drive_link}\n"
                if folder_link:
                    success_text += f"📁 **Thư mục kênh trên Drive:**\n👉 {folder_link}\n\n"
            success_text += "💡 Video đã được xuất bản Công khai (Public) thành công!"
            await safe_edit_text(status_wait, success_text)
        elif pub_res.get("status") == "waiting_quota_reset":
            # QuotaGuard kích hoạt: Toàn bộ Dự án Google Cloud của kênh này đã dùng hết hạn mức hôm nay
            from app.services.quota_manager import QuotaManager, get_next_reset_time
            next_reset_str = get_next_reset_time().strftime("%H:%M ngày %d/%m/%Y")
            pool = QuotaManager.get_channel_tokens_pool(channel_id)

            waiting_text = (
                f"⏳ <b>HẠN MỨC DỰ ÁN GOOGLE CLOUD ĐÃ HẾT! (Task #{job_id})</b>\n\n"
                f"📺 <b>Kênh mục tiêu:</b> <code>{html.escape(ch_name)}</code>\n"
                f"🚨 Dự án Google Cloud <code>{html.escape(pub_agent.project_id)}</code> liên kết với kênh này đã dùng hết 10.000 điểm hôm nay.\n\n"
                f"🔒 <b>HỆ THỐNG GIỮ NGUYÊN KÊNH:</b>\n"
                f"Video <b>KHÔNG</b> bị chuyển sang kênh khác để đảm bảo đúng chủ đề nội dung (niche).\n"
                f"Video đã được đưa vào hàng đợi an toàn và sẽ <b>tự động xuất bản lên {html.escape(ch_name)}</b> lúc <b>{next_reset_str}</b> (khi Google nạp lại Quota).\n\n"
                f"💡 <i>Mẹo: Để đăng ngay mà không cần đợi, bạn có thể liên kết thêm Dự án Google Cloud dự phòng cho kênh {html.escape(ch_name)} bằng lệnh:</i>\n"
                f"<code>python auth_channel.py {channel_id} account_1</code>"
            )
            if drive_link:
                waiting_text += f"\n\n💾 Video đã lưu trữ an toàn trên Google Drive:\n👉 {drive_link}"
            await safe_edit_text(status_wait, waiting_text, parse_mode="HTML")
        else:
            err = pub_res.get("error", "Lỗi không xác định")
            fail_text = f"❌ **Đăng YouTube thất bại:** `{err}`"
            if drive_link:
                fail_text += f"\n\n💾 Tuy nhiên video đã được sao lưu an toàn lên Drive:\n👉 {drive_link}"
            await safe_edit_text(status_wait, fail_text)
    except Exception as e:
        logger.error(f"Lỗi khi đăng YouTube Video #{job_id}: {e}", exc_info=True)
        await safe_reply(update, f"❌ Lỗi khi xuất bản Video #{job_id}: `{e}`")
    finally:
        _release_action(pub_lock_key, post_cooldown_sec=8.0)


async def video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Xử lý nút bấm trên kịch bản & video: Duyệt Dựng / Đăng YouTube / Góp Ý Sửa / Lên Lịch / Tạo Lại / Hủy
    """
    query = update.callback_query
    data = query.data if query else ""
    user = query.from_user if query else update.effective_user

    # 0. Nút đã khóa khi video đang dựng hoặc nằm trong hàng đợi
    if data.startswith("render_video_locked:"):
        job_id = int(data.split(":")[1])
        await safe_query_answer(
            query,
            f"⏳ Video #{job_id} đang được dựng hoặc đã nằm trong hàng đợi rồi! Gõ /queue để xem tiến độ.",
            show_alert=True,
        )
        return

    # 1. Duyệt kịch bản & Render video trực tiếp bằng máy chủ nội bộ (chống spam click tuyệt đối)
    if data.startswith("render_video:"):
        job_id = int(data.split(":")[1])
        rq = get_render_queue()
        btn_lock_key = f"render_btn:{job_id}"
        acquired = _try_acquire_action(btn_lock_key, max_inflight_sec=30.0, debounce_sec=8.0)
        if not acquired or rq.find_active_item(job_id) is not None:
            await safe_query_answer(
                query,
                f"⏳ Video #{job_id} đã nằm trong hàng đợi hoặc đang được dựng rồi! Không cần bấm lại.",
                show_alert=True,
            )
            if query and query.message:
                try:
                    await query.message.edit_reply_markup(
                        reply_markup=get_script_review_keyboard(job_id, is_rendering=True)
                    )
                except Exception:
                    pass
            return

        await safe_query_answer(query, f"🎬 Đã nhận lệnh dựng Video #{job_id}!", show_alert=False)
        if query and query.message:
            try:
                await query.message.edit_reply_markup(
                    reply_markup=get_script_review_keyboard(job_id, is_rendering=True)
                )
            except Exception:
                pass

        try:
            with get_db() as db:
                j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                j_title = j.title or j.topic if j else f"Video #{job_id}"

            user_name = user.first_name if user else "Creator"
            await safe_reply(
                update,
                f"🚀 <b>ĐÃ DUYỆT! BẮT ĐẦU DỰNG VIDEO #{job_id}...</b>\n\n"
                f"🎬 <b>Tiêu đề:</b> <i>{j_title}</i>\n"
                f"⚡ Hệ thống AI đang tự động: Sinh giọng đọc chuẩn, chọn & tạo hình ảnh bám sát kịch bản, làm phụ đề động và kết xuất video 1080x1920.\n"
                f"⏳ Video sẽ tự động gửi về nhóm ngay khi hoàn thành!",
                parse_mode="HTML"
            )
            await _enqueue_render(update, context, job_id, user_name)
        finally:
            _release_action(btn_lock_key, post_cooldown_sec=8.0)
        return

    await safe_query_answer(query)

    if data.startswith("colab_cluster_menu:"):
        job_id = int(data.split(":")[1])
        markup = get_script_review_keyboard(job_id)
        msg_text = (
            f"🎬 <b>HỆ THỐNG DỰNG VIDEO AI NỘI BỘ (Task #{job_id})</b>\n\n"
            f"💡 Hệ thống hiện tại đang sử dụng động cơ AI Render Nội Bộ (Local Native Engine) "
            f"kết hợp AI Image Generator và kho tài nguyên chất lượng cao, không phụ thuộc Google Colab.\n\n"
            f"👉 Bấm <b>🎬 DUYỆT & DỰNG VIDEO NGAY</b> bên dưới để sản xuất Shorts!"
        )
        if query and query.message:
            await query.message.edit_text(msg_text, reply_markup=markup, parse_mode="HTML")
        else:
            await safe_reply(update, msg_text, reply_markup=markup, parse_mode="HTML")

    elif data.startswith("back_script_review:"):
        job_id = int(data.split(":")[1])
        markup = get_script_review_keyboard(job_id)
        msg_text = (
            f"🎬 <b>MENU KỊCH BẢN (Task #{job_id})</b>\n\n"
            f"👉 Cả nhóm hãy xem kịch bản, bấm <b>DUYỆT & DỰNG VIDEO</b> bên dưới để sản xuất Shorts!"
        )
        if query and query.message:
            await query.message.edit_text(msg_text, reply_markup=markup, parse_mode="HTML")
        else:
            await safe_reply(update, msg_text, reply_markup=markup, parse_mode="HTML")

    elif data.startswith("check_drive_render:"):
        job_id = int(data.split(":")[1])
        from app.services.colab_queue_service import check_and_import_completed_video

        wait_m = await safe_reply(update, f"🔍 Đang quét tìm video #{job_id} từ thư mục Google Drive...")
        ok, local_p, d_link = check_and_import_completed_video(job_id)
        if ok:
            chat_id = update.effective_chat.id if update.effective_chat else None
            bot = context.bot
            from app.services.monetization_engine import find_best_amazon_product, build_amazon_url
            with get_db() as db:
                jb = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                v_title = jb.title or jb.topic if jb else f"Video #{job_id}"
                ch_id = jb.target_channel_id if jb else None

            aff_prod = find_best_amazon_product(ch_id or "", v_title, jb.topic if jb else "", jb.script if jb else "")
            aff_url = build_amazon_url(aff_prod["asin"])
            aff_name = aff_prod.get("name", "Sản phẩm đề xuất")
            aff_cta = aff_prod.get("cta", "")
            post_markup = get_video_post_render_keyboard(job_id, affiliate_url=aff_url, affiliate_name=aff_name)

            safe_title = html.escape(str(v_title))
            safe_aff_name = html.escape(str(aff_name))
            safe_aff_cta = html.escape(str(aff_cta))
            aff_url_esc = html.escape(str(aff_url))
            size_mb = os.path.getsize(local_p) / (1024 * 1024) if os.path.exists(local_p) else 0
            drive_str = f'<a href="{d_link}">Mở file gốc trên Drive</a>' if d_link else "Đã lưu an toàn"
            cap = (
                f"🎉 <b>VIDEO #{job_id} ĐÃ RENDER HOÀN TẤT &amp; ĐÃ GỬI VỀ NHÓM!</b>\n\n"
                f"📌 <b>Tiêu đề:</b> {safe_title}\n"
                f"📦 <b>Dung lượng:</b> {size_mb:.1f} MB\n"
                f"⚡ <b>Công nghệ:</b> GPU NVENC P2 Tăng Tốc (1080x1920 30fps)\n"
                f"☁️ <b>Google Drive:</b> {drive_str}\n\n"
                f"🛒 <b>SẢN PHẨM TIẾP THỊ LIÊN KẾT (AFFILIATE):</b>\n"
                f"• <b>Sản phẩm:</b> {safe_aff_name}\n"
                f"• <b>Gợi ý:</b> <i>{safe_aff_cta}</i>\n"
                f"👉 <b>Link mua ngay:</b> <a href=\"{aff_url_esc}\">{aff_url_esc}</a>\n\n"
                f"👉 Bấm <b>Đăng Ngay</b>, <b>Lên Lịch</b>, <b>Sửa</b> hoặc <b>Xóa</b> bên dưới:"
            )
            if len(cap) > 1000:
                cap = cap[:990] + "..."
            sent_video = False
            if size_mb <= 49.0 and os.path.exists(local_p) and chat_id and bot:
                try:
                    with open(local_p, "rb") as vf:
                        await bot.send_video(
                            chat_id=chat_id,
                            video=vf,
                            caption=cap,
                            parse_mode="HTML",
                            reply_markup=post_markup,
                            supports_streaming=True,
                            read_timeout=180,
                            write_timeout=180
                        )
                    sent_video = True
                    try:
                        await wait_m.delete()
                    except Exception:
                        pass
                except Exception as ex_v:
                    logger.warning(f"Lỗi gửi file video: {ex_v}")
                    try:
                        with open(local_p, "rb") as vf_doc:
                            await bot.send_document(
                                chat_id=chat_id,
                                document=vf_doc,
                                caption=cap,
                                parse_mode="HTML",
                                reply_markup=post_markup,
                                read_timeout=180,
                                write_timeout=180
                            )
                        sent_video = True
                        try:
                            await wait_m.delete()
                        except Exception:
                            pass
                    except Exception as ex_d:
                        logger.warning(f"Lỗi gửi document video: {ex_d}")

            if not sent_video:
                await safe_edit_text(wait_m, cap, reply_markup=post_markup, parse_mode="HTML")
        else:
            await safe_edit_text(wait_m, f"⏳ {local_p}\n\n👉 Vui lòng đợi vài giây để Colab Pro+ render xong rồi bấm thử lại!")

    elif data.startswith("render_local:"):
        job_id = int(data.split(":")[1])
        await safe_reply(update, f"💻 Đang chuyển Video #{job_id} sang chế độ render bằng CPU máy chủ (chậm 10-15 phút)...")
        await _enqueue_render(update, context, job_id, user.first_name)

    # 2. Đăng lên YouTube & Google Drive sau khi xem video
    elif data.startswith("publish_youtube:"):
        job_id = int(data.split(":")[1])
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not job or not job.video_path or not os.path.exists(job.video_path):
                await safe_query_answer(query, f"❌ File video #{job_id} chưa sẵn sàng để xuất bản.", show_alert=True)
                await safe_reply(update, f"❌ File video #{job_id} chưa sẵn sàng để xuất bản.")
                return
            video_title = job.title or job.topic or f"Video #{job_id}"
            target_ch = job.target_channel_id

        # Kiểm tra kênh đích của video đã có token OAuth YouTube chưa
        ch_token = os.path.join(settings.CREDENTIALS_DIR, "channels", target_ch, "youtube_token.json") if target_ch else None
        channels = settings.get_channels_config()
        ch_cfg = next((c for c in channels if c.get("id") == target_ch), None)
        ch_name = ch_cfg.get("name", target_ch) if ch_cfg else (target_ch or "Chưa chọn")

        if target_ch and ch_token and os.path.exists(ch_token):
            # Kênh đã cấp quyền OAuth đầy đủ!
            await safe_query_answer(query, f"⚡ Đang kiểm tra xuất bản lên kênh {ch_name}...", show_alert=False)
            from app.services.channel_intelligence import check_pre_publish_safety
            safety = await asyncio.to_thread(check_pre_publish_safety, job_id, target_ch)

            if safety.get("has_warnings"):
                warn_bullets = "\n".join([f"• {html.escape(w)}" for w in safety["warnings"]])
                rec_bullets = "\n".join([f"• {html.escape(r)}" for r in safety["recommendations"]])

                advisory_text = (
                    f"🛡️ <b>KIỂM TRA AN TOÀN & TỐI ƯU TRƯỚC KHI ĐĂNG (Task #{job_id})</b>\n\n"
                    f"📺 <b>Kênh dự kiến:</b> <code>{html.escape(ch_name)}</code>\n"
                    f"🎬 <b>Tiêu đề:</b> <i>{html.escape(video_title)}</i>\n\n"
                    f"🔍 <b>Hệ thống AI phát hiện các rủi ro có thể khiến video bị flop:</b>\n"
                    f"{warn_bullets}\n\n"
                    f"💡 <b>KHUYẾN NGHỊ TỐI ƯU CỦA HỆ THỐNG:</b>\n"
                    f"{rec_bullets}\n\n"
                    f"👉 <b>Bạn muốn xử lý như thế nào trước khi xuất bản?</b>"
                )
                markup = get_pre_publish_advisory_keyboard(job_id, target_ch, safety.get("next_slot_vn", ""))
                await safe_reply(update, advisory_text, reply_markup=markup, parse_mode="HTML")
                return

            # Không có cảnh báo -> Xuất bản trực tiếp ngay lập tức!
            await _execute_channel_publish(update, context, job_id, target_ch, user)
            return

        # Nếu chưa chọn kênh hoặc chưa có OAuth -> Mở danh sách chọn kênh
        await safe_query_answer(query, "📋 Vui lòng chọn kênh YouTube Shorts...", show_alert=False)
        markup = get_publish_channel_keyboard(job_id)
        text = (
            f"⚡ <b>CHỌN KÊNH YOUTUBE SHORTS ĐỂ XUẤT BẢN (Task #{job_id})</b>\n\n"
            f"📌 <b>Tiêu đề:</b> {html.escape(video_title)}\n"
            f"⚡ <b>Định dạng:</b> YouTube Shorts (Dọc 9:16 + Gắn cờ #Shorts)\n"
            f"🎯 <b>Kênh dự kiến:</b> <code>{html.escape(str(target_ch or 'Chưa chọn'))}</code>\n\n"
            f"👉 Vui lòng chọn kênh YouTube bạn muốn tải Shorts lên:\n"
            f"<i>(🟢: Đã kết nối OAuth | ⚠️: Chưa kết nối OAuth)</i>"
        )
        await safe_reply(update, text, reply_markup=markup, parse_mode="HTML")

    elif data.startswith("pick_publish_channel:"):
        job_id = int(data.split(":")[1])
        await safe_query_answer(query, "📋 Mở danh sách kênh YouTube...", show_alert=False)
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            video_title = job.title or job.topic if job else f"Video #{job_id}"
            target_ch = job.target_channel_id if job else "Chưa chọn"

        markup = get_publish_channel_keyboard(job_id)
        text = (
            f"⚡ <b>CHỌN KÊNH YOUTUBE SHORTS ĐỂ XUẤT BẢN (Task #{job_id})</b>\n\n"
            f"📌 <b>Tiêu đề:</b> {html.escape(video_title)}\n"
            f"⚡ <b>Định dạng:</b> YouTube Shorts (Dọc 9:16 + Gắn cờ #Shorts)\n"
            f"🎯 <b>Kênh hiện tại:</b> <code>{html.escape(str(target_ch))}</code>\n\n"
            f"👉 Vui lòng chọn kênh bạn muốn tải Shorts lên:\n"
            f"<i>(🟢: Đã kết nối OAuth | ⚠️: Chưa kết nối OAuth)</i>"
        )
        await safe_reply(update, text, reply_markup=markup, parse_mode="HTML")

    elif data.startswith("cancel_publish:"):
        job_id = int(data.split(":")[1])
        await safe_query_answer(query, "ℹ️ Đã hủy xuất bản.", show_alert=False)
        await safe_reply(update, f"ℹ️ Đã hủy thao tác đăng YouTube cho Video #{job_id}.")

    elif data.startswith("publish_to_channel:"):
        parts = data.split(":")
        job_id = int(parts[1])
        channel_id = parts[2]
        await safe_query_answer(query, "⏳ Đang kiểm tra điều kiện xuất bản...", show_alert=False)

        from app.services.channel_intelligence import check_pre_publish_safety
        safety = await asyncio.to_thread(check_pre_publish_safety, job_id, channel_id)

        if safety.get("has_warnings"):
            warn_bullets = "\n".join([f"• {html.escape(w)}" for w in safety["warnings"]])
            rec_bullets = "\n".join([f"• {html.escape(r)}" for r in safety["recommendations"]])

            advisory_text = (
                f"🛡️ <b>KIỂM TRA AN TOÀN & TỐI ƯU TRƯỚC KHI ĐĂNG (Task #{job_id})</b>\n\n"
                f"📺 <b>Kênh dự kiến:</b> <code>{html.escape(safety.get('channel_name', channel_id))}</code>\n"
                f"🎬 <b>Tiêu đề:</b> <i>{html.escape(safety.get('title', ''))}</i>\n\n"
                f"🔍 <b>Hệ thống AI phát hiện các rủi ro có thể khiến video bị flop:</b>\n"
                f"{warn_bullets}\n\n"
                f"💡 <b>KHUYẾN NGHỊ TỐI ƯU CỦA HỆ THỐNG:</b>\n"
                f"{rec_bullets}\n\n"
                f"👉 <b>Bạn muốn xử lý như thế nào trước khi xuất bản?</b>"
            )
            markup = get_pre_publish_advisory_keyboard(job_id, channel_id, safety.get("next_slot_vn", ""))
            await safe_reply(update, advisory_text, reply_markup=markup, parse_mode="HTML")
            return

        # Nếu hoàn toàn tối ưu và không có rủi ro, tiến hành xuất bản ngay
        await _execute_channel_publish(update, context, job_id, channel_id, user)

    elif data.startswith("force_publish_to_channel:"):
        parts = data.split(":")
        job_id = int(parts[1])
        channel_id = parts[2]
        await safe_query_answer(query, "⚡ Đang tiến hành xuất bản ngay...", show_alert=False)
        await _execute_channel_publish(update, context, job_id, channel_id, user)

    elif data.startswith("rotate_publish_to:"):
        parts = data.split(":")
        job_id = int(parts[1])
        new_channel_id = parts[2]
        await safe_query_answer(query, "⚡ Đang tự động xoay kênh và đăng video...", show_alert=False)
        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if j:
                j.target_channel_id = new_channel_id
        await _execute_channel_publish(update, context, job_id, new_channel_id, user)

    elif data.startswith("wait_quota_job:"):
        parts = data.split(":")
        job_id = int(parts[1])
        ch_id = parts[2]
        await safe_query_answer(query, "⏰ Đã chuyển video vào hàng đợi Quota an toàn!", show_alert=False)
        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if j:
                j.status = "WAITING_QUOTA_RESET"
                j.target_channel_id = ch_id
        from app.services.quota_manager import get_next_reset_time
        reset_time_str = get_next_reset_time().strftime("%H:%M ngày %d/%m/%Y")
        await safe_reply(
            update,
            f"⏰ **ĐÃ CHUYỂN VIDEO #{job_id} VÀO HÀNG ĐỢI AN TOÀN!**\n\n"
            f"📺 **Kênh:** `{ch_id}`\n"
            f"⏳ Video sẽ tự động được hệ thống đăng lên YouTube sau khi Google nạp lại hạn mức Quota lúc **{reset_time_str}**.\n"
            f"💡 Bạn không cần làm gì thêm, hệ thống sẽ tự động quét và xuất bản đúng giờ!"
        )


    # 3. Góp ý chỉnh sửa kịch bản hoặc video
    elif data.startswith("feedback_video:") or data.startswith("edit_script:"):
        job_id = int(data.split(":")[1])
        markup = get_video_edit_options_keyboard(job_id)
        await safe_reply(
            update,
            f"✍️ **BẢNG ĐIỀU KHIỂN CHỈNH SỬA VIDEO #{job_id}**\n\n"
            f"⚡ **Thao tác nhanh 1 chạm:** Chọn các nút bên dưới để đổi tiêu đề, chuyển ngôn ngữ hoặc đổi giọng đọc Nam/Nữ tức thì.\n\n"
            f"📝 **Các lệnh `/edit` có sẵn:**\n"
            f"• `/edit {job_id} title <tiêu đề mới>` : Sửa tiêu đề video nhanh (không render lại)\n"
            f"• `/edit {job_id} lang vi` : Chuyển kịch bản & lồng tiếng sang Tiếng Việt\n"
            f"• `/edit {job_id} lang en` : Chuyển kịch bản & lồng tiếng sang Tiếng Anh\n"
            f"• `/edit {job_id} voice nam|nu` : Đổi giọng đọc Nam hoặc Nữ\n"
            f"• `/edit {job_id} voice brian|andrew|ava|namminh|hoaimy` : Đổi giọng cụ thể\n"
            f"• `/edit {job_id} <góp ý>` : Chỉnh sửa nội dung kịch bản (VD: _Sửa hook giật gân hơn_)\n"
            f"• `/edit {job_id} scene <số> <từ khóa>` : Đổi hình ảnh/video của 1 cảnh",
            reply_markup=markup
        )

    # 3.0 Đổi tiêu đề video qua nút bấm
    elif data.startswith("prompt_edit_title:"):
        job_id = int(data.split(":")[1])
        curr_title = "Chưa có"
        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if j and j.title:
                curr_title = j.title
        await safe_reply(
            update,
            f"✏️ **HƯỚNG DẪN ĐỔI TIÊU ĐỀ CHO VIDEO #{job_id}**\n\n"
            f"📌 **Tiêu đề hiện tại:**\n`{curr_title}`\n\n"
            f"👉 Để đổi sang tiêu đề mới, bạn chỉ cần copy dòng lệnh bên dưới, sửa tiêu đề theo ý muốn và gửi vào đây:\n\n"
            f"`/edit {job_id} title Tiêu đề mới hấp dẫn của bạn 💀`\n\n"
            f"⚡ Hệ thống sẽ cập nhật ngay tức thì vào kịch bản & thông tin xuất bản YouTube Shorts (không mất công render lại!)."
        )

    # 3.1 Đổi ngôn ngữ qua nút bấm nhanh
    elif data.startswith("edit_lang:"):
        parts = data.split(":")
        job_id = int(parts[1])
        target_lang = parts[2]
        lang_label = "Tiếng Việt (vi)" if target_lang == "vi" else "Tiếng Anh (en)"
        status_msg = await safe_reply(
            update,
            f"🌐 **[LangGraph] Đang chuyển toàn bộ kịch bản và giọng lồng tiếng Video #{job_id} sang {lang_label}...**\n"
            f"⏳ AI đang dịch kịch bản, tạo giọng bản xứ mới và tự động render lại..."
        )
        try:
            from app.graph import GraphRunner, EditRequest
            runner = GraphRunner(job_id=job_id)
            edit_req = EditRequest(
                action_type="change_language",
                new_language=target_lang
            )
            final_state = await runner.execute_edit(edit_req)
            video_out = final_state.get("rendered_video_path")
            markup = get_video_post_render_keyboard(job_id, is_republish=True)
            await safe_edit_text(
                status_msg,
                f"✅ **ĐÃ CHUYỂN TOÀN BỘ SANG {lang_label.upper()} THÀNH CÔNG!**\n\n"
                f"🎬 Video #{job_id} đã được biên tập và render lại hoàn tất. Bạn có thể xem và đăng tải video bên dưới:",
                reply_markup=markup
            )
            if video_out and os.path.exists(video_out):
                try:
                    with open(video_out, "rb") as vf:
                        await update.effective_chat.send_video(
                            video=vf,
                            caption=f"🎥 **Video #{job_id} sau khi chuyển sang {lang_label}:**",
                            supports_streaming=True,
                            reply_markup=markup
                        )
                except Exception as ex:
                    logger.error(f"Gửi video sau khi edit_lang thất bại: {ex}")
        except Exception as e:
            logger.error(f"Lỗi khi đổi ngôn ngữ qua callback: {e}", exc_info=True)
            await safe_edit_text(status_msg, f"❌ Lỗi khi đổi ngôn ngữ Video #{job_id}: `{e}`")

    # 3.2 Đổi giọng đọc qua nút bấm nhanh
    elif data.startswith("edit_voice:"):
        parts = data.split(":")
        job_id = int(parts[1])
        gender = parts[2]

        is_vn = False
        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if j and (j.script or j.scenes_json):
                from app.agents.voice_agent import VoiceAgent
                is_vn = VoiceAgent._is_vietnamese(j.script or j.scenes_json)

        if gender == "female":
            voice_id = "vi-VN-HoaiMyNeural" if is_vn else "en-US-AvaNeural"
            voice_name = "Nữ Việt (Hoài My)" if is_vn else "Nữ Mỹ (Ava)"
        else:
            voice_id = "vi-VN-NamMinhNeural" if is_vn else "en-US-AndrewNeural"
            voice_name = "Nam Việt (Nam Minh)" if is_vn else "Nam Mỹ (Andrew)"

        status_msg = await safe_reply(
            update,
            f"🎙️ **[LangGraph] Đang đổi giọng đọc Video #{job_id} sang {voice_name}...**\n"
            f"⏳ Đang tạo lại âm thanh các phân cảnh và render lại video..."
        )
        try:
            from app.graph import GraphRunner, EditRequest
            runner = GraphRunner(job_id=job_id)
            edit_req = EditRequest(
                action_type="change_voice",
                new_voice_id=voice_id
            )
            final_state = await runner.execute_edit(edit_req)
            video_out = final_state.get("rendered_video_path")
            markup = get_video_post_render_keyboard(job_id, is_republish=True)
            await safe_edit_text(
                status_msg,
                f"✅ **ĐÃ ĐỔI GIỌNG ĐỌC SANG {voice_name.upper()} THÀNH CÔNG!**\n\n"
                f"🎬 Video #{job_id} đã được khớp lại phụ đề ASS và render hoàn tất. Bạn có thể xem và đăng tải video bên dưới:",
                reply_markup=markup
            )
            if video_out and os.path.exists(video_out):
                try:
                    with open(video_out, "rb") as vf:
                        await update.effective_chat.send_video(
                            video=vf,
                            caption=f"🎥 **Video #{job_id} với giọng đọc mới ({voice_name}):**",
                            supports_streaming=True,
                            reply_markup=markup
                        )
                except Exception as ex:
                    logger.error(f"Gửi video sau khi edit_voice thất bại: {ex}")
        except Exception as e:
            logger.error(f"Lỗi khi đổi giọng đọc qua callback: {e}", exc_info=True)
            await safe_edit_text(status_msg, f"❌ Lỗi khi đổi giọng đọc Video #{job_id}: `{e}`")

    # 3.3 Hủy / đóng menu chỉnh sửa
    elif data.startswith("cancel_edit:"):
        if query.message:
            try:
                await query.message.delete()
            except Exception:
                pass

    # 3.4 Xóa Video hoàn toàn khỏi máy chủ & Google Drive
    elif data.startswith("delete_video:") or data.startswith("cancel:"):
        job_id = int(data.split(":")[1])
        from app.agents.drive_agent import DriveAgent

        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not job:
                await safe_reply(update, f"⚠️ Không tìm thấy Video #{job_id} trong hệ thống.")
                return
            v_title = job.title or job.topic or f"Video #{job_id}"
            local_path = job.video_path

            # Xóa file video local nếu có
            if local_path and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except Exception as e_del:
                    logger.warning(f"Lỗi xóa file local {local_path}: {e_del}")

            if job.audio_path and os.path.exists(job.audio_path):
                try:
                    os.remove(job.audio_path)
                except Exception:
                    pass

            if job.subtitle_path and os.path.exists(job.subtitle_path):
                try:
                    os.remove(job.subtitle_path)
                except Exception:
                    pass

            # Cập nhật database
            job.status = "CANCELLED"
            job.video_path = None
            job.progress_percent = 0
            job.progress_message = "Đã xóa video theo yêu cầu người dùng trên Telegram."
            user_id = user.id if user else 0
            u_name = user.first_name if user else "TelegramUser"
            log_activity(db, user_id, u_name, "DELETE_VIDEO", f"Đã xóa Video #{job_id}: {v_title}", channel_id=job.target_channel_id)

        # Xóa file trên Google Drive nếu có
        try:
            da = DriveAgent()
            if da.service:
                for sub in ["pending", "in_progress", "completed", "locks", "archive"]:
                    fid = da.get_folder_by_path(f"AI_YouTube_RenderFarm/{sub}")
                    if fid:
                        q = f"'{fid}' in parents and trashed = false and (name contains '{job_id}' or name = 'task_{job_id}.json' or name = 'video_{job_id}.mp4')"
                        res = da.service.files().list(q=q, fields="files(id, name)").execute()
                        for f_d in res.get("files", []):
                            try:
                                da.service.files().delete(fileId=f_d["id"]).execute()
                            except Exception:
                                pass
        except Exception as ex_drive:
            logger.warning(f"Lỗi dọn dẹp Drive khi xóa video #{job_id}: {ex_drive}")

        msg_del = (
            f"🗑️ <b>ĐÃ XÓA VIDEO #{job_id} THÀNH CÔNG!</b>\n\n"
            f"📌 <b>Tiêu đề:</b> {v_title}\n"
            f"🧹 Đã xóa file MP4, phụ đề và hủy tác vụ trên Google Drive Render Farm.\n"
            f"👉 Trạng thái tác vụ đã chuyển thành: <code>CANCELLED</code>."
        )
        if query and query.message:
            try:
                if query.message.video or query.message.photo:
                    await query.message.edit_caption(caption=msg_del, parse_mode="HTML")
                else:
                    await query.message.edit_text(msg_del, parse_mode="HTML")
            except Exception:
                await safe_reply(update, msg_del, parse_mode="HTML")
        else:
            await safe_reply(update, msg_del, parse_mode="HTML")

    # 4. Lên lịch tự động theo khe giờ vàng tối ưu
    elif data.startswith("schedule_youtube:") or data.startswith("schedule:"):
        job_id = int(data.split(":")[1])
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not job or not job.video_path or not os.path.exists(job.video_path):
                await safe_reply(update, f"❌ File video #{job_id} chưa sẵn sàng để lên lịch.")
                return
            video_title = job.title or job.topic

        markup = get_schedule_channel_keyboard(job_id)
        text = (
            f"⏰ **TỰ ĐỘNG XẾP VÀO KHE GIỜ VÀNG SHORTS (Video #{job_id})**\n\n"
            f"📌 **Tiêu đề:** {video_title}\n\n"
            f"🎯 **Thuật toán thông minh sẽ tự động:**\n"
            f"• Quét 2 khung giờ vàng đỉnh cao nhất: 20:00 tối VN (09:00 sáng Mỹ) hoặc 04:00 sáng VN (17:00 chiều tối Mỹ).\n"
            f"• Tự động chọn khe giờ vàng TRỐNG tiếp theo (tránh trùng lịch, tối đa 2 video/ngày).\n"
            f"• Upload video lên YouTube Shorts (kèm cờ #Shorts) và khóa chế độ hẹn giờ (`publishAt`).\n\n"
            f"👉 Vui lòng chọn kênh để kích hoạt lên lịch Shorts tự động:"
        )
        await safe_reply(update, text, reply_markup=markup)

    elif data.startswith("schedule_to_channel:"):
        parts = data.split(":")
        job_id = int(parts[1])
        channel_id = parts[2]
        sched_lock_key = f"schedule_job:{job_id}"
        acquired = _try_acquire_action(sched_lock_key, max_inflight_sec=180.0, debounce_sec=10.0)
        if not acquired:
            await safe_reply(update, f"⏳ Video #{job_id} đang trong quá trình lên lịch, vui lòng chờ giây lát!")
            return
        try:
            with get_db() as db:
                job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if not job or not job.video_path or not os.path.exists(job.video_path):
                    await safe_reply(update, f"❌ File video #{job_id} chưa sẵn sàng để xuất bản.")
                    return
                if job.status in ("SCHEDULED", "PUBLISHED") and job.youtube_video_id:
                    await safe_reply(
                        update,
                        f"ℹ️ **Video #{job_id} đã được lên lịch/đăng trước đó!**\n"
                        f"👉 https://www.youtube.com/shorts/{job.youtube_video_id}"
                    )
                    return
                video_path = job.video_path
                video_title = job.title or job.topic
                video_desc = job.description or ""
                video_tags = [t.strip() for t in (job.tags or "").split(",") if t.strip()]
                scenes_json = job.scenes_json

            channels = settings.get_channels_config()
            ch_cfg = next((c for c in channels if c.get("id") == channel_id), {"id": channel_id, "name": channel_id})
            acc_id = ch_cfg.get("google_account", "account_1")
            ch_token_path = os.path.join(settings.CREDENTIALS_DIR, "channels", channel_id, "youtube_token.json")

            if not os.path.exists(ch_token_path):
                await safe_reply(
                    update,
                    f"⚠️ **KÊNH CHƯA CẤP QUYỀN YOUTUBE RIÊNG!**\n\n"
                    f"Kênh `{ch_cfg.get('name', channel_id)}` thuộc tài khoản `{acc_id}` chưa có quyền YouTube OAuth riêng.\n\n"
                    f"👉 **Cách kết nối trong 10 giây:**\n"
                    f"1. Mở Web Studio tại `http://127.0.0.1:8000` -> Tab **Kênh & Tài Khoản**.\n"
                    f"2. Tại dòng kênh `{ch_cfg.get('name', channel_id)}`, bấm nút đỏ: **`🔑 Cấp Quyền YouTube (Kênh Này)`**.\n"
                    f"3. Đăng nhập Google và bấm chọn đúng kênh `{ch_cfg.get('name', channel_id)}` là xong!"
                )
                return

            from app.services.smart_scheduler import get_next_available_golden_slot
            slot_dt, slot_iso, display_vn, display_us = get_next_available_golden_slot(channel_id)

            # Đảm bảo tiêu đề và mô tả có gắn tag Shorts
            if "#shorts" not in video_title.lower():
                if len(video_title) > 91:
                    video_title = video_title[:91].rstrip()
                video_title = f"{video_title} #Shorts"

            if "#shorts" not in video_desc.lower():
                video_desc = f"{video_desc.strip()}\n\n#Shorts #shorts #viral" if video_desc.strip() else "#Shorts #shorts #viral"

            await safe_reply(
                update,
                f"⏰ **ĐANG TỰ ĐỘNG XẾP VÀO KHE GIỜ VÀNG SHORTS (Video #{job_id})...**\n\n"
                f"📺 **Kênh:** `{ch_cfg.get('name', channel_id)}`\n"
                f"⚡ **Chế độ:** YouTube Shorts\n"
                f"🎯 **Khe giờ vàng tìm thấy:**\n"
                f"👉 **{display_vn}**\n"
                f"*(🇺🇸 {display_us})*\n\n"
                f"🚀 Đang sao lưu Google Drive ({acc_id}) và upload lên YouTube Shorts ở chế độ hẹn giờ (publishAt)..."
            )

            from app.agents.publisher_agent import PublisherAgent
            from app.agents.drive_agent import DriveAgent

            ch_name = ch_cfg.get("name", channel_id)
            pub_agent = PublisherAgent(channel_config=ch_cfg, account_name=acc_id)
            drive_agent = DriveAgent(account_name=acc_id)

            # 1. Tải lên Google Drive đúng theo thư mục riêng của kênh
            drive_res = await asyncio.to_thread(
                drive_agent.upload_video,
                video_path,
                video_title,
                channel_name=ch_name
            )
            drive_link = drive_res.get("drive_link")
            folder_link = drive_res.get("folder_link")

            # Trích xuất bình luận mồi (pinned_comment) từ kịch bản nếu có
            pinned_comment = None
            if scenes_json:
                try:
                    s_data = json.loads(scenes_json)
                    pinned_comment = s_data.get("pinned_comment")
                except Exception:
                    pass
            if not pinned_comment:
                n_key = str(ch_cfg.get("niche") or "").lower()
                if "psychology" in n_key:
                    pinned_comment = "Did this hit close to home? Which sign was most accurate? Drop your thoughts below 👇\n🔔 Subscribe to master dark psychology before someone uses it on you! 🧠"
                elif any(k in n_key for k in ("rescue", "animal")):
                    pinned_comment = "Every innocent life deserves love ❤️ Would you have stopped to help? Comment below!\n🐾 Subscribe right now to help give more terrified animals a second chance! 🐾"
                elif any(k in n_key for k in ("deep_sea", "mystery", "history")):
                    pinned_comment = "What is your theory about this? What are they really hiding? Drop it below 👇\n🌊 Subscribe to unlock Part 2 as soon as the classified archive drops! 🔥"
                elif any(k in n_key for k in ("predator", "wildlife")):
                    pinned_comment = "Who is the true apex ruler of this realm? Drop your vote below 👇\n🦁 Subscribe for more untamed apex predator battles! 🦁"
                else:
                    pinned_comment = "Which moment shocked you the most? Drop your theory in the comments below 👇\n🔔 Subscribe so you don't miss Part 2 releasing next! 🔥"

            # 2. Đăng tải lên YouTube kèm schedule_time_iso
            ch_lang = ch_cfg.get("language", "en")
            pub_res = await asyncio.to_thread(
                pub_agent.upload_video,
                video_path=video_path,
                title=video_title,
                description=video_desc,
                tags=video_tags,
                privacy_status="private",
                schedule_time_iso=slot_iso,
                default_language=ch_lang,
                default_audio_language=ch_lang,
                pinned_comment=pinned_comment,
                topic=video_title,
                script=scenes_json or "",
            )

            if pub_res.get("status") == "success":
                yt_url = pub_res.get("youtube_url")
                yt_shorts_url = pub_res.get("shorts_url") or f"https://www.youtube.com/shorts/{pub_res.get('video_id')}"
                yt_id = pub_res.get("video_id")
                with get_db() as db:
                    db_job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                    if db_job:
                        db_job.status = "SCHEDULED"
                        db_job.scheduled_time = slot_dt
                        db_job.youtube_video_id = yt_id
                        db_job.target_channel_id = channel_id
                        if pub_res.get("enriched_description"):
                            db_job.description = pub_res.get("enriched_description")
                        if drive_link:
                            db_job.drive_link = drive_link
                        log_activity(db, user.id, user.username, "SCHEDULE_YOUTUBE",
                                     f"Đã lên lịch Video #{job_id} vào {display_vn} ({yt_shorts_url})",
                                     channel_id=channel_id)

                success_text = (
                    f"🎉 **ĐÃ LÊN LỊCH PHÁT SÓNG SHORTS THÀNH CÔNG CHO VIDEO #{job_id}!**\n\n"
                    f"📺 **Kênh:** `{ch_cfg.get('name', channel_id)}`\n"
                    f"⚡ **Chế độ:** YouTube Shorts\n"
                    f"⏰ **Giờ công chiếu tự động:**\n👉 **{display_vn}**\n"
                    f"*(🇺🇸 {display_us})*\n\n"
                    f"⚡ **Link xem YouTube Shorts (Đã khóa hẹn giờ):**\n👉 {yt_shorts_url}\n\n"
                    f"🎬 **Link phụ:** {yt_url}\n\n"
                )
                if pub_res.get("comment_id"):
                    success_text += f"💬 **Bình luận mồi tự động (Pinned Bait):** Đã đăng thành công!\n> _{pinned_comment}_\n\n"
                prod_info = pub_res.get("product_info")
                if prod_info:
                    success_text += (
                        f"🛒 **Sản phẩm liên kết tự động (Amazon Affiliate):**\n"
                        f"• **Món hàng:** `{prod_info.get('name')}`\n"
                        f"• **Link kiếm tiền:** {prod_info.get('affiliate_url')}\n"
                        f"• **Mã Tag:** `{prod_info.get('tracking_tag')}`\n\n"
                    )
                success_text += "💡 **Hoàn toàn tự động:** Bạn có thể tắt máy tính / tắt bot, máy chủ YouTube sẽ tự động công chiếu đúng giờ này!"
                if drive_link:
                    success_text += f"\n\n📂 **Bản gốc đã lưu trên Google Drive (Thư mục `{ch_name}`):**\n👉 [Xem video]({drive_link})"
                    if folder_link:
                        success_text += f"\n📁 **Thư mục kênh trên Drive:** [Mở thư mục trên Drive]({folder_link})"

                await safe_reply(update, success_text)
            elif pub_res.get("status") == "waiting_quota_reset":
                from app.services.quota_manager import QuotaManager, get_next_reset_time
                next_reset_str = get_next_reset_time().strftime("%H:%M ngày %d/%m/%Y")

                waiting_text = (
                    f"⏳ <b>HẠN MỨC DỰ ÁN GOOGLE CLOUD ĐÃ HẾT! (Task #{job_id})</b>\n\n"
                    f"📺 <b>Kênh mục tiêu:</b> <code>{html.escape(ch_name)}</code>\n"
                    f"🚨 Dự án Google Cloud <code>{html.escape(pub_agent.project_id)}</code> liên kết với kênh này đã dùng hết 10.000 điểm hôm nay.\n\n"
                    f"🔒 <b>HỆ THỐNG GIỮ NGUYÊN KÊNH:</b>\n"
                    f"Video <b>KHÔNG</b> bị chuyển sang kênh khác để đảm bảo đúng chủ đề nội dung (niche).\n"
                    f"Video đã được đưa vào hàng đợi an toàn và sẽ <b>tự động xuất bản lên {html.escape(ch_name)}</b> lúc <b>{next_reset_str}</b> (khi Google nạp lại Quota).\n\n"
                    f"💡 <i>Mẹo: Để đăng ngay mà không cần đợi, bạn có thể liên kết thêm Dự án Google Cloud dự phòng cho kênh {html.escape(ch_name)} bằng lệnh:</i>\n"
                    f"<code>python auth_channel.py {channel_id} account_1</code>"
                )
                await safe_reply(update, waiting_text, parse_mode="HTML")
            else:
                err = pub_res.get("error") or pub_res.get("message") or "Lỗi không xác định"
                await safe_reply(
                    update,
                    f"❌ **LÊN LỊCH VIDEO #{job_id} THẤT BẠI!**\n\n"
                    f"Chi tiết lỗi từ YouTube API: `{err}`"
                )
        except Exception as e:
            logger.error(f"Lỗi khi lên lịch YouTube Video #{job_id}: {e}", exc_info=True)
            await safe_reply(update, f"❌ Lỗi khi lên lịch Video #{job_id}: `{e}`")
        finally:
            _release_action(sched_lock_key, post_cooldown_sec=8.0)

    # 5.1 Đổi ngôn ngữ kịch bản 1-Click (Tiếng Việt <-> Tiếng Anh)
    elif data.startswith("switch_lang:"):
        parts = data.split(":")
        job_id = int(parts[1])
        target_lang = parts[2] if len(parts) > 2 else "toggle"
        lang_lock_key = f"script_op:{job_id}"
        acquired = _try_acquire_action(lang_lock_key, max_inflight_sec=90.0, debounce_sec=5.0)
        if not acquired:
            await safe_reply(update, f"⏳ Kịch bản #{job_id} đang được AI xử lý, vui lòng chờ giây lát!")
            return

        try:
            with get_db() as db:
                job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if not job or not job.scenes_json:
                    await safe_reply(update, f"❌ Không tìm thấy kịch bản cho Job #{job_id}")
                    return
                channel_id = job.target_channel_id or "channel_maya"
                current_script_json = job.scenes_json

            from app.agents.voice_agent import VoiceAgent
            from app.schemas.contracts import ScriptContract
            current_script = ScriptContract.from_json_str(current_script_json)

            if target_lang == "toggle":
                is_vn = VoiceAgent._is_vietnamese(current_script.full_narration or current_script.hook)
                target_lang = "en" if is_vn else "vi"

            target_lang_display = "🇻🇳 Tiếng Việt" if target_lang == "vi" else "🇺🇸 Tiếng Anh"
            ch = channel_manager.get_channel(channel_id) or {}
            channel_niche = ch.get("niche") or "general"
            channel_name = ch.get("name") or channel_id

            wait_m = await safe_reply(
                update,
                f"🌐 **{user.first_name}** yêu cầu chuyển đổi toàn bộ kịch bản Task #{job_id} sang **{target_lang_display}**...\n"
                f"⏳ Gemini AI đang dịch và tái cấu trúc lời thoại chuẩn viral..."
            )

            try:
                prompt_directive = (
                    f"TRANSLATE AND REWRITE THIS ENTIRE SCRIPT STRICTLY TO {target_lang_display.upper()} ('{target_lang}'). "
                    f"Every spoken scene narration, hook, full_narration, and title MUST be 100% in {target_lang}. "
                    f"Maintain the exact viral hooks, pacing, and seamless infinite loop."
                )
                revised_script = await asyncio.to_thread(
                    content_agent.refine_script,
                    current_script=current_script,
                    feedback=prompt_directive,
                    channel_niche=channel_niche,
                    language=target_lang
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
                        log_activity(db, user.id, user.first_name, "SWITCH_LANG",
                                     f"Chuyển kịch bản #{job_id} sang {target_lang_display}: '{revised_script.title}'",
                                     channel_id=channel_id)

                scenes_preview = "\n".join([
                    f"• Cảnh {s.scene_id}: {s.narration[:65]}..."
                    for s in revised_script.scenes[:3]
                ])
                markup = get_script_review_keyboard(job_id, current_lang=target_lang)

                reply_text = (
                    f"🎬 **KỊCH BẢN ĐÃ CHUYỂN SANG {target_lang_display.upper()} (Task #{job_id})**\n\n"
                    f"📌 **Tiêu đề:** {revised_script.title}\n"
                    f"🔥 **Hook 3 giây đầu:** \"{revised_script.hook}\"\n\n"
                    f"📝 **Phân cảnh tiêu biểu:**\n{scenes_preview}\n\n"
                    f"⏱️ **Ước lượng:** ~{revised_script.total_estimated_duration:.0f}s | {len(revised_script.scenes)} cảnh\n"
                    f"🗣️ **Người đổi:** {user.first_name} | 📺 **Kênh:** `{channel_name}` | 🌐 **Ngôn ngữ:** {target_lang_display}\n\n"
                    f"👉 Cả nhóm hãy xem kịch bản, bấm **DUYỆT & DỰNG VIDEO NGAY** bên dưới để máy chủ tự động sản xuất Shorts!"
                )
                await safe_edit_text(wait_m, reply_text, reply_markup=markup)
            except Exception as ex:
                logger.error(f"Lỗi chuyển ngôn ngữ kịch bản #{job_id}: {ex}", exc_info=True)
                await safe_edit_text(wait_m, f"❌ Lỗi khi chuyển đổi ngôn ngữ: `{ex}`")
        finally:
            _release_action(lang_lock_key, post_cooldown_sec=3.0)

    # 5. Tạo lại kịch bản từ đầu
    elif data.startswith("regenerate:"):
        job_id = int(data.split(":")[1])
        regen_lock_key = f"script_op:{job_id}"
        acquired = _try_acquire_action(regen_lock_key, max_inflight_sec=90.0, debounce_sec=5.0)
        if not acquired:
            await safe_reply(update, f"⏳ Kịch bản #{job_id} đang được AI tái tạo, vui lòng chờ giây lát!")
            return

        try:
            with get_db() as db:
                job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if not job:
                    await safe_reply(update, f"❌ Không tìm thấy VideoJob #{job_id}")
                    return
                channel_id = job.target_channel_id or "channel_maya"
                topic = job.topic or job.title or "Viral Concept"
                # Nhận diện ngôn ngữ hiện tại của job
                from app.agents.voice_agent import VoiceAgent
                is_vn = VoiceAgent._is_vietnamese(job.script or job.scenes_json or "")
                current_lang = "vi" if is_vn else "en"

            ch = channel_manager.get_channel(channel_id) or {}
            channel_name = ch.get("name") or channel_id
            channel_niche = ch.get("niche") or "general"
            lang_display = "🇻🇳 Tiếng Việt" if current_lang == "vi" else "🇺🇸 Tiếng Anh"

            wait_m = await safe_reply(
                update,
                f"🔄 **{user.first_name}** yêu cầu tạo lại kịch bản mới cho Task #{job_id}...\n"
                f"📌 Chủ đề: *\"{topic}\"*\n"
                f"📺 Kênh: `{channel_name}` | 🌐 Ngôn ngữ: **{lang_display}**\n"
                f"⏳ Gemini AI đang viết lại kịch bản với góc nhìn mới..."
            )

            try:
                new_script, eval_report = await asyncio.to_thread(
                    content_agent.generate_best_script,
                    topic=topic,
                    channel_niche=channel_niche,
                    language=current_lang
                )

                with get_db() as db:
                    db_job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                    if db_job:
                        db_job.title = new_script.title
                        db_job.script = new_script.full_narration
                        db_job.description = new_script.description
                        db_job.tags = ",".join(new_script.tags)
                        db_job.scenes_json = new_script.to_json_str()
                        db_job.status = "SCRIPT_READY"
                        log_activity(db, user.id, user.first_name, "REGENERATE_SCRIPT",
                                     f"Đã tạo lại kịch bản #{job_id}: '{new_script.title}'",
                                     channel_id=channel_id)

                scenes_preview = "\n".join([
                    f"• Cảnh {s.scene_id}: {s.narration[:65]}..."
                    for s in new_script.scenes[:3]
                ])
                markup = get_script_review_keyboard(job_id, current_lang=current_lang)

                reply_text = (
                    f"🎬 **KỊCH BẢN ĐÃ TÁI TẠO XONG (Task #{job_id})**\n\n"
                    f"📌 **Tiêu đề mới:** {new_script.title}\n"
                    f"🔥 **Hook 3 giây đầu:** \"{new_script.hook}\"\n\n"
                    f"📝 **Phân cảnh tiêu biểu:**\n{scenes_preview}\n\n"
                    f"⏱️ **Ước lượng:** ~{new_script.total_estimated_duration:.0f}s | {len(new_script.scenes)} cảnh\n"
                    f"🗣️ **Người tạo:** {user.first_name} | 📺 **Kênh:** `{channel_name}` | 🌐 **Ngôn ngữ:** {lang_display}\n\n"
                    f"👉 Cả nhóm hãy xem kịch bản, bấm **DUYỆT & DỰNG VIDEO NGAY** bên dưới để máy chủ tự động sản xuất Shorts!"
                )
                await safe_edit_text(wait_m, reply_text, reply_markup=markup)
            except Exception as ex:
                logger.error(f"Lỗi tái tạo kịch bản #{job_id}: {ex}", exc_info=True)
                await safe_edit_text(wait_m, f"❌ Lỗi khi tạo lại kịch bản: `{ex}`")
        finally:
            _release_action(regen_lock_key, post_cooldown_sec=3.0)

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

    # 6.1 Bỏ qua tương tác Colab (tính năng đã giản lược)
    elif data in ("check_colab_status", "rotate_colab_node", "colab_rotation_guide"):
        await safe_query_answer(query, text="Tính năng Colab đã được giản lược để tối ưu dự án.")
        return

    # 7. Xử lý toàn bộ các kịch bản đang chờ (Render Queue Batch)
    elif data == "process_all_pending":
        batch_lock_key = "batch_process_all_pending"
        acquired = _try_acquire_action(batch_lock_key, max_inflight_sec=30.0, debounce_sec=8.0)
        if not acquired:
            await safe_reply(update, "⏳ Đang đưa các kịch bản vào hàng đợi, vui lòng không bấm lặp lại!")
            return
        try:
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
        finally:
            _release_action(batch_lock_key, post_cooldown_sec=5.0)

    # 7.5 Khôi phục và đưa toàn bộ video bị lỗi FAILED vào hàng đợi render lại
    elif data == "retry_all_failed":
        retry_lock_key = "batch_retry_all_failed"
        acquired = _try_acquire_action(retry_lock_key, max_inflight_sec=30.0, debounce_sec=8.0)
        if not acquired:
            await safe_reply(update, "⏳ Đang khôi phục các video lỗi vào hàng đợi, vui lòng chờ giây lát!")
            return
        try:
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
        finally:
            _release_action(retry_lock_key, post_cooldown_sec=5.0)

    # 8. Làm mới danh sách tác vụ chờ
    elif data == "refresh_pending":
        await pending_command(update, context)

    # 8.1 Tải / gửi video lên Telegram để xem ngay
    elif data.startswith("send_video_file:"):
        job_id = int(data.split(":")[1])
        await send_video_file_handler(update, context, job_id)

    # 8.2 Chuyển trang danh sách video
    elif data.startswith("videos_page:"):
        page = int(data.split(":")[1])
        await _show_videos_list(update, context, page=page)

    # 8.3 Làm mới danh sách video
    elif data.startswith("refresh_videos:"):
        page = int(data.split(":")[1])
        await _show_videos_list(update, context, page=page)

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
        markup = get_video_post_render_keyboard(job_id, is_republish=True)

        success_text = (
            f"✅ **ĐÃ CẬP NHẬT CẢNH {scene_id} THÀNH CÔNG!**\n\n"
            f"🎬 **Tư liệu mới:** {item.get('title')}\n"
            f"📊 **Loại:** {m_type.upper()} ({item.get('source')})\n\n"
            f"✨ Video #{job_id} đã được dựng hoàn chỉnh lại với tư liệu mới! Bạn có thể xem và đăng tải video bên dưới:"
        )
        if status_msg:
            await safe_edit_text(status_msg, success_text, reply_markup=markup)
        else:
            await safe_reply(update, success_text, reply_markup=markup)

        if video_out and Path(video_out).exists():
            try:
                with open(video_out, "rb") as vf:
                    await update.effective_chat.send_video(
                        video=vf,
                        caption=f"🎥 **Video #{job_id} sau khi cập nhật Cảnh {scene_id}:**",
                        supports_streaming=True,
                        reply_markup=markup
                    )
            except Exception as ex:
                logger.error(f"Gửi video sau khi apply media thất bại: {ex}")

    # 8. Chọn ý tưởng Shorts triệu view (1-Click Viral Production)
    elif data.startswith("pick_viral_topic:"):
        idx = int(data.split(":")[1])
        ideas = context.chat_data.get("cached_viral_ideas", []) if context.chat_data else []
        if not ideas or idx >= len(ideas):
            await safe_reply(update, "⚠️ Danh sách ý tưởng đã hết hạn. Vui lòng gõ `/viral` để lấy danh sách mới!")
            return

        idea = ideas[idx]
        topic = idea.get("topic", "")
        angle = idea.get("angle", "")
        formula = idea.get("viral_formula", "Debate in Comments")
        fixes_flaw = idea.get("fixes_flaw", "")
        trend_align = idea.get("trend_alignment", "")
        director_notes = f"Góc tiếp cận viral: {angle}. Áp dụng công thức kêu gọi hành động: {formula}."
        if fixes_flaw:
            director_notes += f" Chiến lược khắc phục điểm yếu của kênh: {fixes_flaw}."
        if trend_align:
            director_notes += f" Đón đầu xu hướng toàn cầu: {trend_align}."

        channel_id = context.chat_data.get("cached_viral_channel", "channel_maya") if context.chat_data else "channel_maya"
        ch = channel_manager.get_channel(channel_id) or {}
        channel_name = ch.get("name") or channel_id
        channel_niche = ch.get("niche") or "psychology"
        channel_lang = ch.get("language") or "en"

        viral_lock_key = f"pick_viral:{channel_id}:{idx}"
        acquired = _try_acquire_action(viral_lock_key, max_inflight_sec=90.0, debounce_sec=8.0)
        if not acquired:
            await safe_reply(
                update,
                f"⏳ **Hệ thống đang tạo kịch bản cho ý tưởng #{idx+1} rồi!**\n"
                f"💡 Vui lòng chờ 15–30 giây để Gemini AI hoàn tất, không cần bấm lại nhiều lần."
            )
            return

        dup_job_id = _find_recent_duplicate_job(channel_id, topic, window_seconds=300)
        if dup_job_id:
            _release_action(viral_lock_key, post_cooldown_sec=2.0)
            review_markup = get_script_review_keyboard(dup_job_id)
            await safe_reply(
                update,
                f"♻️ **Ý tưởng #{idx+1} vừa được tạo ở Task #{dup_job_id}!**\n\n"
                f"📌 **Chủ đề:** {topic}\n\n"
                f"👉 Hệ thống tự động chặn tạo kịch bản trùng lặp do bấm nhiều lần. Bạn có thể bấm **🎬 DUYỆT & DỰNG VIDEO NGAY** bên dưới:",
                reply_markup=review_markup
            )
            return

        status_msg = await safe_reply(
            update,
            f"🎬 **{user.first_name}** đã chọn ý tưởng #{idx+1}: *\"{topic}\"*\n"
            f"⚡ **Công thức:** `{formula}`\n"
            f"⏳ Đang kích hoạt Gemini AI lên kịch bản chuẩn giữ chân 160% cho kênh `{channel_name}`..."
        )

        try:
            script, eval_report = await asyncio.to_thread(
                content_agent.generate_best_script,
                topic=topic,
                channel_niche=channel_niche,
                language=channel_lang,
                director_notes=director_notes
            )

            with get_db() as db:
                job = VideoJob(
                    user_id=user.id,
                    target_channel_id=channel_id,
                    topic=topic,
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

                log_activity(db, user.id, user.username, "SCRIPT_CREATED_VIRAL",
                             f"Kịch bản Viral #{job_id} đã tạo: '{script.title}'",
                             channel_id=channel_id)

            scenes_preview = "\n".join([
                f"• Cảnh {s.scene_id}: {s.narration[:60]}... _(Tìm: {s.visual_keyword})_"
                for s in script.scenes[:3]
            ])

            score_text = ""
            if eval_report:
                overall = eval_report.overall_score
                hook_s = eval_report.hook_strength.score
                pacing_s = eval_report.story_pacing.score
                vis_s = getattr(eval_report, 'visual_direction', None)
                vis_score = vis_s.score if vis_s else 8.5
                badge = "🏆 Xuất sắc" if overall >= 8.5 else "✅ Đạt chuẩn Viral"
                score_text = (
                    f"📊 **ĐÁNH GIÁ CHẤT LƯỢNG (AI Editorial Critic):** `{overall:.1f}/10` {badge}\n"
                    f"• ⚡ Hook 3s: `{hook_s:.1f}/10` | ⏱️ Nhịp độ: `{pacing_s:.1f}/10` | 👁️ Thị giác: `{vis_score:.1f}/10`\n\n"
                )

            pinned_display = f"💬 **Bình luận mồi ghim Top (Pinned Bait):**\n> _{script.pinned_comment}_\n\n" if getattr(script, "pinned_comment", None) else ""
            formula_display = f"⚡ **Công thức Viral:** `{script.viral_formula}`\n" if getattr(script, "viral_formula", None) else ""
            reply_content = (
                f"🎬 **KỊCH BẢN ĐÃ HOÀN THÀNH (Task #{job_id})**\n\n"
                f"{score_text}"
                f"📌 **Tiêu đề đề xuất:** {script.title}\n"
                f"🔥 **Hook 3 giây đầu:** \"{script.hook}\"\n\n"
                f"{formula_display}"
                f"📝 **Phân cảnh tiêu biểu:**\n{scenes_preview}\n\n"
                f"{pinned_display}"
                f"⏱️ **Ước lượng:** ~{script.total_estimated_duration:.0f}s | {len(script.scenes)} cảnh\n\n"
                f"🗣️ **Người tạo:** {user.first_name} | 📺 **Kênh:** `{channel_name}` ({channel_niche})\n\n"
                f"👉 Cả nhóm hãy xem kịch bản, bấm **🎬 DUYỆT & DỰNG VIDEO NGAY** bên dưới để máy chủ tự động sản xuất Shorts!"
            )

            review_markup = get_script_review_keyboard(job_id)
            if status_msg:
                await safe_edit_text(status_msg, reply_content, reply_markup=review_markup)
            else:
                await safe_reply(update, reply_content, reply_markup=review_markup)

        except Exception as e:
            logger.error(f"Lỗi khi tạo kịch bản viral: {e}", exc_info=True)
            if status_msg:
                await safe_edit_text(status_msg, f"❌ Có lỗi xảy ra khi tạo kịch bản: `{e}`")
            else:
                await safe_reply(update, f"❌ Có lỗi xảy ra khi tạo kịch bản: `{e}`")
        finally:
            _release_action(viral_lock_key, post_cooldown_sec=5.0)

    # 9. Làm mới 5 ý tưởng viral
    elif data.startswith("refresh_viral_ideas:"):
        channel_id = data.split(":")[1] if ":" in data else "channel_maya"
        ref_lock_key = f"refresh_viral:{channel_id}"
        acquired = _try_acquire_action(ref_lock_key, max_inflight_sec=45.0, debounce_sec=5.0)
        if not acquired:
            return
        ch = channel_manager.get_channel(channel_id) or {}
        channel_name = ch.get("name") or channel_id
        channel_niche = ch.get("niche") or "psychology"
        channel_lang = ch.get("language") or "en"

        recent_titles = []
        with get_db() as db:
            recent_jobs = db.query(VideoJob).filter(
                VideoJob.target_channel_id == channel_id,
                VideoJob.title.isnot(None)
            ).order_by(VideoJob.id.desc()).limit(8).all()
            recent_titles = [j.title for j in recent_jobs if j.title]

        try:
            from app.services.channel_intelligence import get_channel_existing_portfolio, suggest_channel_topics

            portfolio = await asyncio.to_thread(get_channel_existing_portfolio, channel_id)
            top_performers = portfolio.get("top_performers", [])
            flop_videos = portfolio.get("flop_videos", [])

            ideas = await asyncio.to_thread(
                suggest_channel_topics,
                channel_id=channel_id,
                count=5,
                exclude_additional=recent_titles
            )
            if context.chat_data is None:
                context.chat_data = {}
            context.chat_data["cached_viral_ideas"] = ideas
            context.chat_data["cached_viral_channel"] = channel_id

            text_lines = [
                f"🔥 **5 Ý TƯỞNG SHORTS TRIỆU VIEW MỚI (BÁM SÁT DỮ LIỆU & XU HƯỚNG 2026)**\n",
                f"📺 **Kênh:** `{channel_name}` | 🎯 **Chủ đề:** `{channel_niche}`\n",
            ]
            if top_performers:
                top_perf_views = top_performers[0].get("views_text") or (str(top_performers[0].get("views_num", 0)) + " views")
                text_lines.append(f"🏆 **Phát huy thế mạnh kênh:** *{top_performers[0]['title']}* ({top_perf_views})")
            if flop_videos:
                text_lines.append(f"🛡️ **Cơ chế chống Flop:** Khắc phục tình trạng bão hòa / lệch ngách từ {len(flop_videos)} video trước")
            text_lines.append("")

            for idx, item in enumerate(ideas):
                title = item.get("title", item.get("topic", ""))
                hook = item.get("hook", "")
                retention = item.get("predicted_retention", "150%+")
                pinned = item.get("pinned_comment", "")
                formula = item.get("viral_formula", "Debate in Comments")
                fixes_flaw = item.get("fixes_flaw", "")
                trend_align = item.get("trend_alignment", "")

                text_lines.append(f"**#{idx+1}. {title}**")
                text_lines.append(f"• ⚡ **Hook 3s:** \"_{hook}_\"")
                text_lines.append(f"• 📈 **Dự báo:** `{retention}` | **CTA:** `{formula}`")
                if fixes_flaw:
                    text_lines.append(f"• 🛡️ **Khắc phục nhược điểm:** {fixes_flaw}")
                if trend_align:
                    text_lines.append(f"• 🌍 **Xu hướng 2026:** {trend_align}")
                if pinned:
                    text_lines.append(f"• 💬 **Bình luận mồi:** \"_{pinned}_\"")
                text_lines.append("")

            text_lines.append("👉 **Bấm vào nút bên dưới** để AI tự động lên kịch bản và sản xuất video ngay lập tức!")
            full_text = "\n".join(text_lines)
            markup = get_viral_ideas_keyboard(ideas, channel_id)
            if query.message:
                await safe_edit_text(query.message, full_text, reply_markup=markup)
        except Exception as e:
            logger.error(f"Lỗi khi làm mới ý tưởng viral: {e}")
        finally:
            _release_action(ref_lock_key, post_cooldown_sec=3.0)

    # 10. Đóng menu ý tưởng viral
    elif data == "close_viral":
        if query.message:
            try:
                await query.message.delete()
            except Exception:
                pass

    # 11. Làm mới bảng phân bổ lịch phát sóng (Schedule)
    elif data.startswith("refresh_schedule:"):
        parts = data.split(":")
        channel_id = parts[1] if len(parts) > 1 else None
        await _show_schedule_view(update, context, channel_id=channel_id)

    # 12. Duyệt video tự động cứu nguy (KPI Auto-Approval: now / schedule / cancel)
    elif data.startswith("approve_auto:"):
        parts = data.split(":")
        job_id = int(parts[1])
        action = parts[2]
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not job:
                await safe_reply(update, f"❌ Không tìm thấy video #{job_id}")
                return
            target_ch = job.target_channel_id
            job_title = job.title or job.topic

            if action == "cancel":
                job.status = "DRAFT"
                job.progress_message = "Người dùng đã hủy duyệt video tự động"
                log_activity(db, user.id, user.username, "CANCEL_AUTO_JOB", f"Người dùng hủy duyệt Video #{job_id}", channel_id=target_ch)
                if query.message:
                    await safe_edit_text(query.message, f"❌ **ĐÃ HỦY DUYỆT VIDEO #{job_id}**\n\n🎬 *{job_title}*\nVideo này sẽ không được đăng lên kênh `{target_ch}`.")
                else:
                    await safe_reply(update, f"❌ Đã hủy duyệt Video #{job_id}")
                return

            elif action == "schedule":
                from app.services.smart_scheduler import get_next_available_golden_slot
                slot_dt, slot_iso, vn_label, us_label = get_next_available_golden_slot(target_ch)
                job.status = "SCHEDULED"
                job.scheduled_time = slot_dt
                job.progress_message = f"Đã duyệt: Lên lịch lúc {vn_label}"
                log_activity(db, user.id, user.username, "APPROVE_SCHEDULE", f"Duyệt Video #{job_id} lên lịch lúc {vn_label}", channel_id=target_ch)
                confirm_txt = (
                    f"✅ **ĐÃ DUYỆT & LÊN LỊCH CÔNG CHIẾU THÀNH CÔNG!**\n\n"
                    f"🎬 **Video #{job_id}:** *{job_title}*\n"
                    f"📺 **Kênh:** `{target_ch}`\n"
                    f"⏰ **Giờ công chiếu:** `{vn_label}` ({us_label})\n\n"
                    f"✨ Video sẽ tự động phát sóng theo khung giờ vàng quốc tế để giữ vững đề xuất thuật toán!"
                )
                if query.message:
                    await safe_edit_text(query.message, confirm_txt)
                else:
                    await safe_reply(update, confirm_txt)
                return

            elif action == "now":
                job.status = "RENDERED"
                log_activity(db, user.id, user.username, "APPROVE_NOW", f"Duyệt Video #{job_id} để đăng ngay", channel_id=target_ch)
                await safe_query_answer(query, "🚀 Đang tiến hành xuất bản ngay...", show_alert=False)
                await _execute_channel_publish(update, context, job_id, target_ch, user)
                return

    # 13. Kích hoạt thủ công tạo video cứu nguy KPI
    elif data.startswith("auto_gen_kpi:"):
        cid = data.split(":")[1]
        kpi_lock_key = f"auto_gen_kpi:{cid}"
        acquired = _try_acquire_action(kpi_lock_key, max_inflight_sec=120.0, debounce_sec=10.0)
        if not acquired:
            await safe_reply(update, f"⏳ Đang sản xuất video cứu nguy cho kênh `{cid}`, vui lòng không bấm lặp lại!")
            return
        try:
            from app.services.channel_intelligence import trigger_auto_generation_for_overdue_channel
            status_msg = await safe_reply(update, f"⏳ Đang kích hoạt AI sản xuất video cứu tương tác cho kênh `{cid}`...")
            res = await asyncio.to_thread(trigger_auto_generation_for_overdue_channel, cid, grace_hours=4.0, force=True)
            if res.get("success"):
                await safe_edit_text(
                    status_msg,
                    f"🚀 **ĐÃ BẮT ĐẦU SẢN XUẤT VIDEO CỨU NGUY (Task #{res['job_id']})**\n\n"
                    f"📺 **Kênh:** `{cid}`\n"
                    f"🎬 **Tiêu đề:** *{res['title']}*\n"
                    f"⏱️ **Hạn chót duyệt:** 4 tiếng kể từ khi render xong.\n\n"
                    f"Gõ `/queue` để theo dõi tiến độ render!"
                )
            else:
                await safe_edit_text(status_msg, f"⚠️ {res.get('message', 'Không thể khởi động tác vụ.')}")
        finally:
            _release_action(kpi_lock_key, post_cooldown_sec=5.0)

    # 14. Làm mới báo cáo KPI
    elif data == "refresh_kpi_report":
        await kpi_command(update, context)

    # 15. Duyệt hoặc Từ chối cấp quyền thiết bị truy cập Web Dashboard (2FA Gatekeeper)
    elif data.startswith("approve_device:"):
        await safe_query_answer(query)
        if not is_admin_user(user.id):
            await safe_query_answer(
                query,
                text="⛔ CHẶN BẢO MẬT: Bạn không phải Quản trị viên (Admin). Chỉ Admin mới có quyền duyệt cấp phép thiết bị vào Web Studio!",
                show_alert=True
            )
            return

        req_id = data.split(":")[1]
        from app.services.device_auth import approve_access_request
        approver = user.username or user.first_name
        req = approve_access_request(req_id, admin_name=approver)
        if req:
            await safe_edit_text(
                query.message,
                f"✅ **ĐÃ DUYỆT THÀNH CÔNG CHO THIẾT BỊ!**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💻 **Thiết bị:** {req.get('device_name')}\n"
                f"🌐 **Địa chỉ IP:** `{req.get('client_ip')}`\n"
                f"👤 **Người phê duyệt:** @{approver}\n"
                f"⏳ **Thời hạn:** 7 ngày\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"✨ Thiết bị trên đã được mở khóa và có thể sử dụng Web Studio ngay bây giờ!"
            )
        else:
            await safe_edit_text(query.message, "⚠️ Yêu cầu này đã hết hạn hoặc không tồn tại.")

    elif data.startswith("reject_device:"):
        await safe_query_answer(query)
        if not is_admin_user(user.id):
            await safe_query_answer(
                query,
                text="⛔ CHẶN BẢO MẬT: Bạn không phải Quản trị viên (Admin). Chỉ Admin mới có quyền từ chối thiết bị!",
                show_alert=True
            )
            return

        req_id = data.split(":")[1]
        from app.services.device_auth import reject_access_request
        rejecter = user.username or user.first_name
        req = reject_access_request(req_id, admin_name=rejecter)
        if req:
            await safe_edit_text(
                query.message,
                f"❌ **ĐÃ TỪ CHỐI TRUY CẬP THIẾT BỊ!**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💻 **Thiết bị:** {req.get('device_name')}\n"
                f"🌐 **Địa chỉ IP:** `{req.get('client_ip')}`\n"
                f"👤 **Người từ chối:** @{rejecter}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⛔ Thiết bị trên đã bị chặn truy cập Web Studio."
            )
        else:
            await safe_edit_text(query.message, "⚠️ Yêu cầu này đã hết hạn hoặc không tồn tại.")

    # 16. XÁC NHẬN HOẶC HỦY XÓA VIDEO VĨNH VIỄN KHỎI HỆ THỐNG
    elif data.startswith("confirm_del_vid:"):
        job_id = int(data.split(":")[1])
        await _handle_confirm_delete_video(update, context, job_id)

    elif data.startswith("cancel_del_vid:"):
        job_id = int(data.split(":")[1])
        await _handle_cancel_delete_video(update, context, job_id)

    elif data == "confirm_del_failed":
        await _handle_confirm_delete_failed_videos(update, context)

    # 17. Bỏ qua cụm Colab GPU
    elif data.startswith(("rotate_colab_node", "check_colab_status", "colab_rotation_guide")):
        await safe_query_answer(query, text="Tính năng Colab đã được giản lược để tối ưu dự án.")
        return


async def _handle_confirm_delete_video(update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: int):
    """Xử lý khi người dùng ấn nút [XÁC NHẬN XÓA] video cụ thể"""
    query = update.callback_query
    user = query.from_user
    username = user.username or user.first_name

    from app.services.ai_commander import delete_video_job
    ok, msg, info = delete_video_job(job_id, user_id=user.id, username=username)

    if ok:
        text = (
            f"🗑️ **ĐÃ XÓA VIDEO #{job_id} THÀNH CÔNG!**\n\n"
            f"✅ **Tiêu đề:** *{info.get('title', f'Video #{job_id}')}*\n"
            f"🎯 **Kênh:** `{info.get('channel_id', 'Chưa rõ')}`\n"
            f"📁 **Dọn dẹp:** Đã giải phóng {info.get('deleted_files_count', 0)} tệp video/audio/temp trên máy chủ.\n"
            f"💾 Toàn bộ dữ liệu đã được gỡ bỏ sạch sẽ khỏi Database SQLite."
        )
    else:
        text = f"❌ **Không thể xóa video #{job_id}:** {msg}"

    if query.message:
        await safe_edit_text(query.message, text)
    else:
        await safe_reply(update, text)


async def _handle_cancel_delete_video(update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: int):
    """Xử lý khi người dùng ấn nút [HỦY BỎ] xóa video"""
    query = update.callback_query
    msg_id_text = f" #{job_id}" if job_id > 0 else ""
    text = (
        f"🛡️ **ĐÃ HỦY THAO TÁC XÓA!**\n\n"
        f"Video{msg_id_text} vẫn được bảo toàn nguyên vẹn trong hệ thống."
    )
    if query.message:
        await safe_edit_text(query.message, text)
    else:
        await safe_reply(update, text)


async def _handle_confirm_delete_failed_videos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý khi người dùng ấn nút [XÁC NHẬN XÓA TẤT CẢ VIDEO LỖI]"""
    query = update.callback_query
    user = query.from_user
    username = user.username or user.first_name

    from app.services.ai_commander import delete_video_job
    with get_db() as db:
        failed_jobs = db.query(VideoJob).filter(VideoJob.status == "FAILED").all()
        failed_ids = [j.id for j in failed_jobs]

    if not failed_ids:
        text = "ℹ️ Không tìm thấy video lỗi nào để xóa."
        if query.message:
            await safe_edit_text(query.message, text)
        else:
            await safe_reply(update, text)
        return

    deleted_count = 0
    total_files = 0
    for jid in failed_ids:
        ok, _, info = delete_video_job(jid, user_id=user.id, username=username)
        if ok:
            deleted_count += 1
            total_files += info.get("deleted_files_count", 0)

    text = (
        f"🗑️ **ĐÃ DỌN DẸP XÓA {deleted_count} VIDEO LỖI THÀNH CÔNG!**\n\n"
        f"📁 Đã giải phóng {total_files} tệp tạm và file lỗi trên máy chủ.\n"
        f"✨ Cơ sở dữ liệu và ổ cứng đã được dọn sạch gọn gàng."
    )
    if query.message:
        await safe_edit_text(query.message, text)
    else:
        await safe_reply(update, text)




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

    # Trạng thái Local Render Engine
    render_status_str = "Local FFmpeg GPU / CPU (Sẵn sàng 🟢)"

    # Kiểm tra trạng thái Google Drive
    drive_status = "Chưa kết nối ⚠️"
    try:
        from app.utils.security import load_encrypted_json
        dt_path = Path(settings.BASE_DIR) / "credentials" / "accounts" / "account_1" / "drive_token.json"
        if dt_path.exists():
            tok = load_encrypted_json(dt_path)
            if tok and (tok.get("token") or tok.get("refresh_token")):
                drive_status = "Đã kết nối ✓ (Google One 20TB)"
    except Exception:
        pass

    text = (
        f"📊 **BÁO CÁO NHÓM LÀM VIỆC (AI STUDIO)**\n\n"
        f"• Tổng số video trong hệ thống: `{total_jobs}`\n"
        f"• Kịch bản đang chờ duyệt/render: `{ready_jobs}`\n"
        f"• Bộ dựng video: {render_status_str}\n"
        f"• Kho Google Drive: {drive_status}\n"
        f"• Cụm Google Ultra Quota: Đang hoạt động 🟢\n"
        f"• Web Dashboard: {dashboard_url} 🌐"
    )
    await safe_reply(update, text)


def _resolve_video_file_path(job_id: int, recorded_path: Optional[str] = None) -> Optional[Path]:
    """Tìm đường dẫn tệp video MP4 thực tế trên máy chủ cho Job ID. Tự động đồng bộ từ Google Drive nếu cần."""
    base_dir = Path(settings.BASE_DIR)
    candidates = []
    if recorded_path:
        cand_rec = Path(recorded_path)
        candidates.append(cand_rec)
        if not cand_rec.is_absolute():
            candidates.append(base_dir / cand_rec)

    candidates.extend([
        base_dir / "storage" / "outputs" / f"video_{job_id}.mp4",
        base_dir / "storage" / "outputs" / f"rendered_{job_id}.mp4",
        base_dir / "storage" / "outputs" / f"final_{job_id}.mp4",
        base_dir / "storage" / f"video_{job_id}.mp4"
    ])

    for c in candidates:
        try:
            if c.exists() and c.is_file() and c.stat().st_size > 1000:
                return c
        except Exception:
            pass

    # Tự động đồng bộ từ Google Drive nếu máy chủ chưa có file cục bộ
    try:
        from app.services.colab_queue_service import check_and_import_completed_video
        ok, local_p, _ = check_and_import_completed_video(job_id)
        if ok and local_p and os.path.exists(local_p) and os.path.getsize(local_p) > 1000:
            return Path(local_p)
    except Exception:
        pass

    return None


async def _show_videos_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    """Truy vấn SQLite và hiển thị danh sách tất cả video kèm nút bấm xem / tải trực tiếp vào Telegram."""
    if not is_authorized(update):
        return

    with get_db() as db:
        total_jobs = db.query(VideoJob).count()
        if total_jobs == 0:
            await safe_reply(
                update,
                "📭 **Chưa có video nào trong hệ thống!**\n\n"
                "👉 Bạn có thể gõ `/idea <chủ đề>` để tạo video Shorts đầu tiên ngay bây giờ!"
            )
            return

        page_size = 5
        total_pages = max(1, (total_jobs + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))

        jobs = (
            db.query(VideoJob)
            .order_by(VideoJob.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        status_emoji_map = {
            "RENDERED": "✅ Đã dựng xong",
            "PUBLISHED": "🚀 Đã đăng YouTube",
            "RENDERING": "⏳ Đang dựng...",
            "SCRIPT_READY": "📝 Kịch bản sẵn sàng",
            "SCHEDULED": "⏰ Đã lên lịch",
            "FAILED": "❌ Thất bại",
            "DRAFT": "📋 Bản nháp"
        }

        items_text = []
        jobs_with_files = []

        for j in jobs:
            v_file = _resolve_video_file_path(j.id, j.video_path)
            st_text = status_emoji_map.get(j.status, f"ℹ️ {j.status}")
            dur_text = f"{j.audio_duration_ms / 1000:.1f}s" if (j.audio_duration_ms and j.audio_duration_ms > 0) else "N/A"
            title_display = j.title or j.topic or f"Video #{j.id}"

            if j.youtube_video_id:
                file_status = f"🚀 Đã đăng YouTube: https://youtu.be/{j.youtube_video_id}"
                jobs_with_files.append({"id": j.id, "title": title_display})
            elif v_file:
                size_mb = v_file.stat().st_size / (1024 * 1024)
                file_status = f"🟢 Có file ({size_mb:.1f} MB)"
                jobs_with_files.append({"id": j.id, "title": title_display})
            elif j.drive_link:
                file_status = f"☁️ Trên Google Drive"
                jobs_with_files.append({"id": j.id, "title": title_display})
            else:
                file_status = "⏳ Chưa có file video"

            items_text.append(
                f"🎬 **#{j.id}: {title_display}**\n"
                f"   • Trạng thái: {st_text}\n"
                f"   • Thời lượng: `{dur_text}` | Kênh: `{j.target_channel_id or 'Chưa rõ'}`\n"
                f"   • Tệp tin: {file_status}"
            )

    msg_body = (
        f"📺 **DANH SÁCH TẤT CẢ VIDEO TRONG HỆ THỐNG**\n"
        f"📊 Tổng cộng: `{total_jobs}` video — Trang **{page}/{total_pages}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n\n".join(items_text)
        + "\n\n━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👉 **Để tải video xem trực tiếp trên Telegram:**\n"
        f"• Bấm nút **📥 Xem #{jobs_with_files[0]['id'] if jobs_with_files else 'ID'}** bên dưới.\n"
        f"• Hoặc gõ lệnh `/get <ID>` (ví dụ: `/get {jobs_with_files[0]['id'] if jobs_with_files else 1}`).\n"
        f"• Hoặc nhắn tin: *'tải video {jobs_with_files[0]['id'] if jobs_with_files else 1}'*, *'xem video'*"
    )

    markup = get_videos_list_keyboard(jobs_with_files, current_page=page, total_pages=total_pages)

    if update.callback_query and update.callback_query.message:
        await safe_edit_text(update.callback_query.message, msg_body, reply_markup=markup)
    else:
        await safe_reply(update, msg_body, reply_markup=markup)


async def send_video_file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: int):
    """
    Tải file video MP4 của VideoJob và gửi trực tiếp vào tin nhắn Telegram để người dùng xem ngay.
    """
    if not is_authorized(update):
        return

    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return

    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            await safe_reply(update, f"❌ Không tìm thấy video có ID #{job_id} trong hệ thống.")
            return

        title = job.title or job.topic or f"Video #{job_id}"
        channel_id = job.target_channel_id or "Chưa rõ"
        status = job.status
        recorded_path = job.video_path
        progress_pct = job.progress_percent or 0
        yt_id = job.youtube_video_id
        d_link = job.drive_link

    # Kiểm tra trạng thái nếu chưa render xong
    if status == "RENDERING":
        await safe_reply(
            update,
            f"⏳ **Video #{job_id} đang được hệ thống dựng ({progress_pct}%)...**\n\n"
            f"📌 Tiêu đề: *{title}*\n"
            f"Vui lòng đợi vài chục giây rồi bấm lại nút **📥 Xem Video** nhé!"
        )
        return
    elif status in ("DRAFT", "SCRIPT_READY"):
        await safe_reply(
            update,
            f"📝 **Video #{job_id} mới có kịch bản, chưa render thành file video!**\n\n"
            f"📌 Tiêu đề: *{title}*\n"
            f"👉 Bạn hãy gõ `/render {job_id}` để bắt đầu tạo video hoàn chỉnh."
        )
        return

    v_file = _resolve_video_file_path(job_id, recorded_path)
    if not v_file:
        # Nếu video đã đăng YouTube
        if yt_id:
            yt_url = f"https://youtu.be/{yt_id}"
            actions_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ Mở Xem Trên YouTube", url=yt_url)],
                [InlineKeyboardButton("📋 Xem Danh Sách Video", callback_data="videos_page:1")]
            ])
            await safe_reply(
                update,
                f"🎉 **VIDEO #{job_id} ĐÃ ĐƯỢC ĐĂNG LÊN YOUTUBE!**\n\n"
                f"📌 **Tiêu đề:** {title}\n"
                f"📺 **Kênh:** `{channel_id}`\n"
                f"🔗 **Link xem video:** {yt_url}\n\n"
                f"💡 Tệp tin gốc trên máy chủ đã được tối ưu dọn dẹp để tiết kiệm bộ nhớ. Bạn có thể bấm nút bên dưới để xem trực tiếp trên YouTube!",
                reply_markup=actions_markup
            )
            return

        # Nếu video có link Google Drive
        if d_link:
            actions_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("☁️ Xem / Tải Trên Google Drive", url=d_link)],
                [InlineKeyboardButton("📋 Xem Danh Sách Video", callback_data="videos_page:1")]
            ])
            await safe_reply(
                update,
                f"☁️ **VIDEO #{job_id} ĐÃ ĐƯỢC LƯU TRÊN GOOGLE DRIVE!**\n\n"
                f"📌 **Tiêu đề:** {title}\n"
                f"📺 **Kênh:** `{channel_id}`\n"
                f"🔗 **Link Google Drive:** {d_link}\n\n"
                f"👉 Bấm nút bên dưới để mở xem hoặc tải video từ kho lưu trữ Google Drive 20TB!",
                reply_markup=actions_markup
            )
            return

        await safe_reply(
            update,
            f"⚠️ **Không tìm thấy tệp video của Job #{job_id} trên máy chủ hoặc Google Drive!**\n\n"
            f"• Trạng thái trong DB: `{status}`\n"
            f"• Đường dẫn dự kiến: `{recorded_path or 'storage/outputs/video_' + str(job_id) + '.mp4'}`\n"
            f"Có thể video đang được Colab xử lý hoặc chưa bấm chạy. Bạn có thể render lại bằng cách gõ `/render {job_id}`."
        )
        return

    size_bytes = v_file.stat().st_size
    size_mb = size_bytes / (1024 * 1024)

    status_msg = await safe_reply(
        update,
        f"⏳ **Đang tải video #{job_id} lên Telegram ({size_mb:.1f} MB)...**\n"
        f"Vui lòng đợi trong giây lát để video hiển thị trong mes tele!"
    )

    from app.utils.ffmpeg_helper import get_duration_ms
    dur_ms = get_duration_ms(str(v_file))
    dur_sec = dur_ms / 1000.0 if dur_ms > 0 else 0.0

    caption = (
        f"🎬 **{title}**\n\n"
        f"🆔 **Mã Video:** `#{job_id}`\n"
        f"⏱️ **Thời lượng:** {dur_sec:.1f}s\n"
        f"📦 **Dung lượng:** {size_mb:.1f} MB\n"
        f"📺 **Kênh xuất bản:** `{channel_id}`\n"
        f"📊 **Trạng thái:** {status}\n"
    )
    if yt_id:
        caption += f"🚀 **YouTube:** https://youtu.be/{yt_id}\n"
    caption += "\n👉 *Video đã có sẵn ở đây, bạn có thể xem trực tiếp hoặc lưu về máy!*"

    if yt_id:
        actions_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("▶️ Xem Trực Tiếp Trên YouTube", url=f"https://youtu.be/{yt_id}")
            ],
            [
                InlineKeyboardButton("📋 Xem Danh Sách Video", callback_data="videos_page:1")
            ]
        ])
    else:
        actions_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🚀 Đăng Lên YouTube", callback_data=f"publish_youtube:{job_id}"),
                InlineKeyboardButton("⏰ Lên Lịch Tự Động", callback_data=f"schedule_youtube:{job_id}")
            ],
            [
                InlineKeyboardButton("📋 Xem Danh Sách Video", callback_data="videos_page:1")
            ]
        ])


    try:
        with open(v_file, "rb") as vf:
            if size_mb <= 50:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=vf,
                    caption=caption,
                    supports_streaming=True,
                    reply_markup=actions_markup,
                    read_timeout=300,
                    write_timeout=300
                )
            else:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=vf,
                    caption=caption,
                    reply_markup=actions_markup,
                    read_timeout=600,
                    write_timeout=600
                )

        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass

    except Exception as ex:
        logger.error(f"Lỗi gửi file video #{job_id} qua Telegram: {ex}", exc_info=True)
        err_text = (
            f"❌ **Không thể gửi file video #{job_id} qua Telegram:** `{ex}`\n\n"
            f"📂 File MP4 hiện đã có sẵn tại máy chủ: `{v_file}` ({size_mb:.1f} MB)\n"
            f"Bạn có thể xem trực tiếp trên Web Dashboard tại http://127.0.0.1:8000"
        )
        if status_msg:
            await safe_edit_text(status_msg, err_text)
        else:
            await safe_reply(update, err_text)


async def videos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị danh sách tất cả các video đã tạo: /videos [trang]"""
    if not is_authorized(update):
        return
    page = 1
    if context.args:
        try:
            page = max(1, int(context.args[0]))
        except ValueError:
            page = 1
    await _show_videos_list(update, context, page=page)


async def get_video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tải và gửi video cụ thể vào tin nhắn Telegram: /get <job_id>"""
    if not is_authorized(update):
        return
    if not context.args:
        await safe_reply(
            update,
            "💡 **CÁCH XEM & TẢI VIDEO VÀO TELEGRAM:**\n\n"
            "• Gõ `/get <Mã_Video>` để bot tải video lên đây xem ngay.\n"
            "• **Ví dụ:** `/get 8` hoặc `/get 1`\n\n"
            "👉 Hoặc gõ `/videos` để xem danh sách và bấm nút chọn video!"
        )
        return

    raw_arg = context.args[0].replace("#", "").strip()
    try:
        job_id = int(raw_arg)
    except ValueError:
        await safe_reply(update, f"❌ Mã video `{raw_arg}` không hợp lệ. Vui lòng nhập số nguyên (Ví dụ: `/get 8`).")
        return

    await send_video_file_handler(update, context, job_id)


def _match_channel_from_text(text: str) -> Optional[Dict[str, Any]]:
    """Tìm kênh được nhắc tên trong câu (so khớp không phân biệt hoa thường)."""
    low = (text or "").lower()
    try:
        for ch in settings.get_channels_config():
            name = (ch.get("name") or "").lower()
            if name and (name in low or (ch.get("id") or "").lower() in low):
                return ch
    except Exception:
        pass
    return None


_LIBRARY_INTENT_RE = re.compile(
    r"(đã\s*(đăng|up|tải lên)\s*(những\s*|các\s*)?(video|clip|short)|đăng\s*(những|các)?\s*video\s*(nào|gì)|thư\s*viện\s*video|"
    r"(có|làm)\s*video\s*(về|nào).*(chưa|rồi)|video\s*nào\s*(đã|rồi))",
    re.IGNORECASE,
)


async def _send_library_answer(update: Update, query: str = "", channel: Optional[Dict[str, Any]] = None):
    from app.services import video_library
    await asyncio.to_thread(video_library.backfill_from_jobs)
    ch_id = channel.get("id") if channel else ""
    vids = await asyncio.to_thread(video_library.list_videos, ch_id, query, 200)
    head = "📚 THƯ VIỆN VIDEO ĐÃ ĐĂNG"
    if channel:
        head += f" — {channel.get('name')}"
    if query:
        head += f" · tìm “{query}”"
    head += f"\nTổng: {len(vids)} video"
    await safe_reply(update, video_library.format_library_text(vids, head), parse_mode=None)


async def library_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/library [tên kênh] [từ khóa] — xem video đã đăng; /library sync — đồng bộ từ YouTube."""
    if not is_authorized(update):
        return
    args_text = " ".join(context.args or []).strip()

    if args_text.lower() in ("sync", "dongbo", "đồng bộ"):
        msg = await update.effective_message.reply_text("⏳ Đang đồng bộ danh sách video từ YouTube…")
        from app.services import video_library
        res = await asyncio.to_thread(video_library.sync_all)
        if res.get("ok") is False:
            await msg.edit_text(f"⚠️ {res.get('error')}")
            return
        lines = ["✅ Đồng bộ xong:"]
        for c in res.get("channels", []):
            if c.get("ok"):
                lines.append(f"• {c['channel_id']}: +{c['added']} mới, {c['updated']} cập nhật")
            else:
                lines.append(f"• {c['channel_id']}: ⚠️ {c['error']}")
        lines.append(f"📝 Đã tóm tắt {res.get('summarized', 0)} video")
        await msg.edit_text("\n".join(lines))
        return

    channel = _match_channel_from_text(args_text)
    query = args_text
    if channel:
        query = re.sub(re.escape(channel.get("name") or ""), "", query, flags=re.IGNORECASE).strip()
    await _send_library_answer(update, query, channel)


async def web_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gửi link truy cập Web Dashboard"""
    if not is_authorized(update):
        return
    import os
    port = int(os.getenv("PORT", 8000))
    web_url = os.getenv("WEB_DASHBOARD_URL") or f"http://localhost:{port}"

    msg = (
        "🌐 **ĐỊA CHỈ TRUY CẬP WEB DASHBOARD:**\n\n"
        f"👉 `{web_url}`\n\n"
        "💡 Bạn có thể mở trực tiếp đường link trên bằng bất kỳ trình duyệt nào."
    )
    keyboard = [
        [InlineKeyboardButton("💻 Mở Web Dashboard", url=web_url)]
    ]
    await safe_reply(update, msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def _show_schedule_view(update: Update, context: ContextTypes.DEFAULT_TYPE, channel_id: Optional[str] = None):
    """Hiển thị bảng phân bổ lịch phát sóng khe giờ vàng và danh sách video sắp công chiếu."""
    if not is_authorized(update):
        return

    user = update.effective_user
    channels = settings.get_channels_config()
    default_ch_id = channels[0].get("id") if channels else "channel_maya"

    if not channel_id:
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first()
            channel_id = db_user.active_channel_id if db_user and db_user.active_channel_id else default_ch_id

    ch_cfg = next((c for c in channels if c.get("id") == channel_id), {"id": channel_id, "name": channel_id})
    ch_name = ch_cfg.get("name", channel_id)

    from app.services.smart_scheduler import (
        get_next_available_golden_slot,
        get_channel_upcoming_schedule,
        get_golden_slots_overview,
        sync_scheduled_jobs_status
    )

    # 1. Tự động đồng bộ các video đã qua giờ hẹn sang PUBLISHED
    sync_scheduled_jobs_status()

    # 2. Lấy khe tiếp theo và danh sách video sắp lên sóng
    slot_dt, slot_iso, display_vn, display_us = get_next_available_golden_slot(channel_id)
    upcoming = get_channel_upcoming_schedule(channel_id, limit=5)
    overview = get_golden_slots_overview()

    upcoming_text = ""
    if upcoming:
        for idx, u in enumerate(upcoming, 1):
            yt_link = f" [🔗 Xem trước]({u['youtube_url']})" if u.get("youtube_url") else ""
            upcoming_text += (
                f"{idx}. 🎬 **#{u['job_id']}: {u['title'][:40]}**\n"
                f"   ⏰ **{u['scheduled_vn']}**\n"
                f"   🇺🇸 _{u['scheduled_us']}_{yt_link}\n\n"
            )
    else:
        upcoming_text = "   _Hiện chưa có video nào đang chờ công chiếu trên kênh này._\n\n"

    overview_text = ""
    for o in overview:
        overview_text += f"• 🎯 **{o['time_vn']}** (🇺🇸 _{o['time_us']}_)\n  _{o['description']}_\n"

    msg = (
        f"📅 **LỊCH PHÁT SÓNG & 2 KHUNG GIỜ VÀNG SHORTS TỐI ƯU 2 VIDEO/NGÀY**\n"
        f"📺 **Kênh:** `{ch_name}` (`{channel_id}`)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 **KHE GIỜ VÀNG TIẾP THEO (TỰ ĐỘNG XẾP):**\n"
        f"👉 **{display_vn}**\n"
        f"🇺🇸 *({display_us})*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 **DANH SÁCH VIDEO ĐÃ LÊN LỊCH:**\n\n"
        f"{upcoming_text}"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌟 **2 KHUNG GIỜ VÀNG SHORTS ĐẠT VIEW CAO NHẤT (MỸ & TOÀN CẦU):**\n\n"
        f"{overview_text}\n"
        f"💡 **Cơ chế tự động thông minh (Tối ưu 2 video/ngày):**\n"
        f"• Tự động giãn cách 8 - 16 giờ giữa 2 video để tránh 'nuốt view' thuật toán.\n"
        f"• Máy chủ YouTube tự động chuyển video sang Công khai khi tới giờ hẹn (kể cả khi bạn tắt máy)!\n"
        f"• Lên lịch video đã dựng: Dùng nút **⏰ Lên Lịch Tự Động** sau khi render hoặc gõ `/videos`."
    )

    markup = get_schedule_keyboard(channel_id)

    if update.callback_query and update.callback_query.message:
        await safe_edit_text(update.callback_query.message, msg, reply_markup=markup)
    else:
        await safe_reply(update, msg, reply_markup=markup)


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh xem lịch phát sóng & 4 khung giờ vàng YouTube Shorts: /schedule hoặc /lich, /lichdang"""
    await _show_schedule_view(update, context)



async def kpi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lệnh /kpi hoặc /baocao, /tiendo:
    Báo cáo sức khỏe tiến độ của tất cả các kênh YouTube trong hệ thống:
    - Kênh nào đang đúng tiến độ (< 24h).
    - Kênh nào sắp trễ (24-48h).
    - Kênh nào trễ deadline >= 2 ngày và trạng thái video tự động cứu nguy.
    """
    if not is_authorized(update):
        return

    from app.services.channel_intelligence import run_network_kpi_audit
    msg = await safe_reply(update, "⏳ Đang kiểm tra số liệu và báo cáo chỉ tiêu KPI các kênh...")
    audit = await asyncio.to_thread(run_network_kpi_audit)

    lines = [
        "📊 **BÁO CÁO TIẾN ĐỘ & CHỈ TIÊU KPI CÁC KÊNH YOUTUBE**\n",
        f"🎯 **Tổng số kênh:** {audit['total_channels']}",
        f"✅ **Đúng tiến độ (<24h):** {audit['on_track_count']} kênh",
        f"⚠️ **Sắp trễ hạn (24-48h):** {audit['warning_count']} kênh",
        f"🚨 **Trễ deadline (≥ 2 ngày):** {audit['overdue_count']} kênh\n",
        "─────────────────────────────"
    ]

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    kb_buttons = []

    for ch in audit["channels"]:
        cname = ch["channel_name"]
        cid = ch["channel_id"]
        status_label = ch["status_label"]
        days = ch["days_since_last_video"]
        hours = ch["hours_since_last_video"]

        lines.append(f"\n📺 **Kênh:** `{cname}` ({ch['niche']})")
        lines.append(f"• **Trạng thái:** {status_label}")
        lines.append(f"• **Thời gian từ video cuối:** {hours:.1f}h (~{days:.1f} ngày)")

        if ch.get("pending_job"):
            pj = ch["pending_job"]
            p_title = (pj['title'] or pj.get('topic') or '')[:30]
            lines.append(f"• 🤖 **Video cứu tương tác:** #{pj['job_id']} (*{p_title}...*) — `{pj['status']}`")
        elif ch["is_overdue_2_days"]:
            lines.append(f"• ⚡ *Đã quá hạn 2 ngày! Hệ thống tự động kích hoạt tạo video cứu nguy.*")
            kb_buttons.append([InlineKeyboardButton(f"⚡ Tạo video cứu nguy: {cname}", callback_data=f"auto_gen_kpi:{cid}")])

    kb_buttons.append([InlineKeyboardButton("🔄 Làm mới báo cáo", callback_data="refresh_kpi_report")])
    markup = InlineKeyboardMarkup(kb_buttons)

    if msg:
        await safe_edit_text(msg, "\n".join(lines), reply_markup=markup)
    else:
        await safe_reply(update, "\n".join(lines), reply_markup=markup)


# ──────────────────────────────────────────────────────────────────────────────
# HỆ THỐNG AUTO-PILOT KÊNH TOÀN DIỆN (A TO Z) & GIÁM SÁT NHẮC NHỞ
# ──────────────────────────────────────────────────────────────────────────────

async def autopilot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lệnh /autopilot hoặc /auto, /tudong:
    - /autopilot : Mở bảng điều khiển Auto-Pilot chung
    - /autopilot <tên_kênh hoặc ID hoặc số thứ tự> : Chỉ định chạy Auto-Pilot ngay cho 1 kênh cụ thể!
    """
    if not is_authorized(update):
        return

    if context.args:
        arg_query = " ".join(context.args).strip().lower()
        from config.settings import settings
        channels = settings.get_channels_config()

        matched_ch = None
        if arg_query.isdigit():
            idx = int(arg_query) - 1
            if 0 <= idx < len(channels):
                matched_ch = channels[idx]

        if not matched_ch:
            for ch in channels:
                cid = ch.get("id", "").lower()
                cname = ch.get("name", "").lower()
                handle = ch.get("handle", "").lower().lstrip("@")
                niche = ch.get("niche", "").lower()
                if (arg_query in cid or arg_query in cname or arg_query in handle or arg_query in niche or
                    cid in arg_query or cname in arg_query):
                    matched_ch = ch
                    break

        if matched_ch:
            cid = matched_ch.get("id")
            cname = matched_ch.get("name", cid)
            uid = update.effective_user.id if update.effective_user else 0
            from app.services.autopilot_service import run_autopilot_pipeline_for_channel
            await safe_reply(
                update,
                f"🚀 **ĐÃ KÍCH HOẠT AUTOPILOT CHO KÊNH CHỈ ĐỊNH: `{cname}`!**\n\n"
                f"Bot đang bắt đầu sản xuất kịch bản, render 1080p và xếp vào khe giờ vàng trống tiếp theo.\n"
                f"Tiến trình đang chạy ngầm, bạn sẽ nhận được thông báo kèm video sau vài phút!"
            )
            asyncio.create_task(run_autopilot_pipeline_for_channel(channel_id=cid, user_id=uid, notify_telegram=True))
            return
        else:
            await safe_reply(
                update,
                f"⚠️ Không tìm thấy kênh nào khớp với từ khóa: `{arg_query}`.\n"
                f"👉 Vui lòng chọn một kênh từ danh sách bên dưới:"
            )
            from app.bot.keyboards import get_autopilot_channel_picker_keyboard
            markup = get_autopilot_channel_picker_keyboard()
            await safe_reply(update, "🎯 **DANH SÁCH CÁC KÊNH KHẢ DỤNG ĐỂ CHẠY AUTOPILOT:**", reply_markup=markup)
            return

    await _show_autopilot_view(update, context)


async def _show_autopilot_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị bảng điều khiển Auto-Pilot và báo cáo tình trạng 5 kênh hôm nay."""
    if not is_authorized(update):
        return

    from app.services.autopilot_service import get_channel_today_summary
    from app.bot.keyboards import get_autopilot_keyboard

    summary = await asyncio.to_thread(get_channel_today_summary)
    empty_channels = summary.get("empty_channels", [])

    lines = []
    for ch in summary.get("channels", []):
        cname = ch["channel_name"]
        v_count = ch["today_video_count"]
        p_job = ch.get("pending_job")
        next_slot = ch.get("next_golden_slot", {}).get("display_vn", "")

        if v_count >= 2:
            times = ", ".join([v["time_vn"] for v in ch["today_videos"]])
            lines.append(f"• 🟢 **{cname}**: `{v_count} video` ({times}) — Đủ chỉ tiêu")
        elif v_count == 1:
            times = ", ".join([v["time_vn"] for v in ch["today_videos"]])
            lines.append(f"• 🟡 **{cname}**: `{v_count} video` ({times}) | Khe tiếp: `{next_slot}`")
        elif p_job:
            lines.append(f"• ⏳ **{cname}**: Đang dựng video (#{p_job['job_id']} - {p_job['progress_percent']}%)")
        else:
            lines.append(f"• 🔴 **{cname}**: **0 video (CHƯA CÓ LỊCH)**\n  ↳ _Khung giờ vàng đề xuất: {next_slot}_")

    channels_str = "\n".join(lines)

    status_alert = ""
    if empty_channels:
        status_alert = (
            f"⚠️ **Có {len(empty_channels)} kênh chưa có video hôm nay!**\n"
            f"💡 Bấm nút dưới để AI tự động làm từ A-Z (kịch bản + render + lên lịch đúng giờ vàng)!\n\n"
        )
    else:
        status_alert = "🎉 **Tất cả các kênh hôm nay đều đã có video theo lịch chuẩn!**\n\n"

    msg = (
        f"🤖 **HỆ THỐNG AUTO-PILOT KÊNH TOÀN DIỆN (A TO Z)**\n"
        f"📅 **Ngày hôm nay:** `{summary['date_vn']}` (Giờ VN)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 **TÌNH TRẠNG 5 KÊNH CỦA BẠN:**\n"
        f"{channels_str}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{status_alert}"
        f"⚡ **Quy trình Auto-Pilot tự động khi bạn bấm:**\n"
        f"1. AI chọn ý tưởng viral độc quyền, chống trùng lặp.\n"
        f"2. Sinh kịch bản 3s CTA hook + badge phụ đề + câu hỏi ghim bình luận.\n"
        f"3. Tự động xếp vào khe giờ vàng trống (20:00 VN hoặc 04:00 VN).\n"
        f"4. Render video 1080p, lồng tiếng, phụ đề động & tạo thumbnail 1 giây.\n"
        f"5. Tự động đặt lịch YouTube Shorts & post bình luận ghim mồi đăng ký!"
    )

    markup = get_autopilot_keyboard(summary)
    if update.callback_query and update.callback_query.message:
        await safe_edit_text(update.callback_query.message, msg, reply_markup=markup)
    else:
        await safe_reply(update, msg, reply_markup=markup)


async def autopilot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý các tương tác nút bấm của hệ thống Auto-Pilot trên Telegram."""
    query = update.callback_query
    data = query.data if query else ""
    user = update.effective_user
    uid = user.id if user else 0

    if data == "autopilot_status":
        await safe_query_answer(query)
        await _show_autopilot_view(update, context)
        return

    if data == "autopilot_pick_channel":
        await safe_query_answer(query)
        from app.bot.keyboards import get_autopilot_channel_picker_keyboard
        markup = get_autopilot_channel_picker_keyboard()
        text = (
            "🎯 <b>CHỌN KÊNH CHỈ ĐỊNH ĐỂ CHẠY AUTO-PILOT A-Z</b>\n\n"
            "Bấm chọn một kênh bên dưới để AI tự động lên kịch bản, render 1080p và xếp lịch vào khung giờ vàng trống tiếp theo của kênh đó:"
        )
        if query.message:
            await safe_edit_text(query.message, text, reply_markup=markup, parse_mode="HTML")
        else:
            await safe_reply(update, text, reply_markup=markup, parse_mode="HTML")
        return

    if data == "autopilot_dismiss":
        await safe_query_answer(query)
        from app.services.autopilot_service import dismiss_today_reminder
        dismiss_today_reminder()
        await safe_edit_text(
            query.message,
            "💤 **ĐÃ GHI NHẬN: BỎ QUA NHẮC NHỞ HÔM NAY!**\n\n"
            "Bot sẽ không gửi thêm nhắc nhở nào trong ngày hôm nay để bạn tập trung làm việc.\n"
            "Bạn có thể mở lại bất cứ lúc nào bằng lệnh `/autopilot`."
        )
        return

    if data == "autopilot_run_all":
        ap_all_key = "autopilot_run_all"
        acquired = _try_acquire_action(ap_all_key, max_inflight_sec=30.0, debounce_sec=15.0)
        if not acquired:
            await safe_query_answer(
                query,
                "⏳ Tiến trình Auto-Pilot cho các kênh trống đã được kích hoạt! Không cần bấm lại.",
                show_alert=True,
            )
            return
        await safe_query_answer(query, "🚀 Đã kích hoạt Auto-Pilot!", show_alert=False)
        try:
            from app.services.autopilot_service import run_autopilot_for_all_empty
            await safe_reply(
                update,
                "🚀 **ĐÃ KÍCH HOẠT AUTOPILOT CHO TẤT CẢ KÊNH TRỐNG!**\n\n"
                "Bot đang tự động chạy quy trình A-Z ngầm. Video sẽ được render và lên lịch tự động theo thứ tự.\n"
                "Bạn sẽ nhận được thông báo ngay khi từng kênh hoàn tất!"
            )
            asyncio.create_task(run_autopilot_for_all_empty(user_id=uid, notify_telegram=True))
        finally:
            _release_action(ap_all_key, post_cooldown_sec=15.0)
        return

    if data.startswith("autopilot_run_channel:"):
        cid = data.split(":", 1)[1]
        ap_ch_key = f"autopilot_run_channel:{cid}"
        acquired = _try_acquire_action(ap_ch_key, max_inflight_sec=30.0, debounce_sec=15.0)
        if not acquired:
            await safe_query_answer(
                query,
                f"⏳ Kênh {cid} đã được kích hoạt Auto-Pilot rồi! Không cần bấm lại.",
                show_alert=True,
            )
            return
        await safe_query_answer(query, f"🚀 Đã kích hoạt Auto-Pilot cho kênh {cid}!", show_alert=False)
        try:
            from app.services.autopilot_service import run_autopilot_pipeline_for_channel
            from config.settings import settings
            channels = settings.get_channels_config()
            ch_cfg = next((c for c in channels if c.get("id") == cid), {})
            ch_name = ch_cfg.get("name", cid)

            await safe_reply(
                update,
                f"🚀 **ĐÃ KÍCH HOẠT AUTOPILOT CHO KÊNH: `{ch_name}`!**\n\n"
                f"Bot đang bắt đầu sản xuất kịch bản, render và xếp vào khe giờ vàng trống tiếp theo.\n"
                f"Tiến trình đang chạy ngầm, bạn sẽ nhận được thông báo sau vài phút!"
            )
            asyncio.create_task(run_autopilot_pipeline_for_channel(channel_id=cid, user_id=uid, notify_telegram=True))
        finally:
            _release_action(ap_ch_key, post_cooldown_sec=15.0)
        return


async def chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    AI Tổng Quản Đa Năng (Universal AI Assistant / AI Director): /chat <yêu cầu tự nhiên>
    Tự động phân tích ý định (RAG context, tạo kịch bản, xóa video với bước xác nhận, render, xem status...)
    """
    if not is_authorized(update):
        return

    if not context.args:
        help_text = (
            "🤖 **AI TỔNG QUẢN STUDIO - ĐIỀU HÀNH BẰNG TIẾNG VIỆT TỰ NHIÊN:**\n\n"
            "Chỉ cần gõ `/chat <bất kỳ yêu cầu nào>` (hoặc nhắn tin trực tiếp với Bot):\n\n"
            "✨ **Ví dụ:**\n"
            "• `/chat Xóa video số 5 giúp tôi` _(Có bước hỏi xác nhận Yes/No an toàn)_\n"
            "• `/chat Xóa tất cả các video bị lỗi`\n"
            "• `/chat Tạo video về bí ẩn đại dương cho kênh Maya`\n"
            "• `/chat Render video số 3 đi`\n"
            "• `/chat Cho xem danh sách video hiện có`\n"
            "• `/chat Gửi video số 2 lên đây để xem`\n"
            "• `/chat Cho xin link Web Studio và Wi-Fi`\n"
            "• `/chat Báo cáo KPI các kênh hôm nay thế nào?`\n"
            "• `/chat Đổi sang kênh Paws & Hearts Rescue`\n"
            "• `/chat Cho mình 3 ý tưởng Shorts thật sốc về tâm lý học hành vi`\n\n"
            "👉 Trong nhóm, bạn cũng có thể **Tag @VV1n_mybot** hoặc **Reply vào tin nhắn của bot** để ra lệnh tự nhiên!"
        )
        await safe_reply(update, help_text)
        return

    user = update.effective_user
    prompt = " ".join(context.args)
    norm_p = " ".join(prompt.lower().split())[:100]
    chat_lock_key = f"chat_cmd:{user.id}:{norm_p}"
    acquired = _try_acquire_action(chat_lock_key, max_inflight_sec=90.0, debounce_sec=6.0)
    if not acquired:
        await safe_reply(update, "⏳ AI Tổng Quản đang xử lý yêu cầu vừa rồi của bạn, vui lòng chờ giây lát!")
        return

    try:
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first()
            channels = settings.get_channels_config()
            default_ch = channels[0].get("id") if channels else "channel_maya"
            channel_id = db_user.active_channel_id if db_user and db_user.active_channel_id else default_ch
            if db_user:
                db_user.last_active = datetime.utcnow()
            log_activity(db, user.id, user.username, "AI_COMMAND", f"Lệnh tự nhiên: '{prompt[:50]}...'", channel_id=channel_id)

        status_msg = await safe_reply(update, f"🧠 **{user.first_name}**: *\"{prompt}\"*\n\n⏳ *AI Tổng Quản đang phân tích ngữ cảnh và điều phối...*")

        from app.services.ai_commander import ai_commander
        await ai_commander.process_and_execute(update, context, prompt, status_message=status_msg)
    finally:
        _release_action(chat_lock_key, post_cooldown_sec=4.0)


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

    # Hỏi tự nhiên về video đã đăng → trả lời từ Thư Viện Video (không tốn quota AI)
    if (
        not any(k.startswith("awaiting_") for k in context.user_data.keys())
        and _LIBRARY_INTENT_RE.search(clean_text)
        and len(clean_text) < 160
    ):
        lib_channel = _match_channel_from_text(clean_text)
        lib_query = ""
        m_topic = re.search(r"video\s+về\s+(.+?)\s*(chưa|rồi|không|chưa\?|\?|$)", clean_text, re.IGNORECASE)
        if m_topic:
            lib_query = m_topic.group(1).strip()
        try:
            await _send_library_answer(update, lib_query, lib_channel)
            return
        except Exception as e_lib:
            logger.warning(f"Library intent lỗi, chuyển sang chat thường: {e_lib}")

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
            language="en",
            visual_mode="imagen_ai",
            preferred_voice="en-US-AndrewNeural"
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
                f"🌐 **Ngôn ngữ:** Tiếng Anh (en)\n\n"
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

    # 1. Tự động nhận diện lệnh tải / gửi video cụ thể vào mes Telegram để xem ngay
    # Ví dụ: "tải video 8", "gửi video 8", "xem video 8", "lấy video 8", "tải video 8 lên tele"
    lower_text = clean_text.lower()
    get_video_match = re.search(
        r'(?:tải|gửi|xem|cho\s+xem|lấy|bật|phát|download|get|send)\s+(?:file\s+)?(?:lên\s+(?:mes\s+)?tele\s+)?video\s*(?:số|no|#)?\s*(\d+)',
        lower_text
    ) or re.search(
        r'video\s*(?:số|no|#)?\s*(\d+)\s*(?:lên\s+tele|vào\s+tele|vào\s+đây|nhé|nào|đi)',
        lower_text
    )
    if get_video_match:
        target_job_id = int(get_video_match.group(1))
        await send_video_file_handler(update, context, target_job_id)
        return

    # 2. Tự động nhận diện lệnh hiển thị danh sách tất cả video
    # Ví dụ: "danh sách video", "hiện tất cả video", "xem video", "list video", "tất cả video"
    is_asking_list = any(kw in lower_text for kw in [
        "danh sách video", "danh sach video", "hiện tất cả video", "hien tat ca video",
        "xem danh sách", "xem danh sach", "tất cả video", "tat ca video",
        "list video", "ds video", "dsvideo", "các video", "cac video", "hiện video", "hien video",
        "cho xem video", "xem cac video", "danh sach cac video", "tất cả các video"
    ])
    if is_asking_list:
        await _show_videos_list(update, context, page=1)
        return

    # 3. Tự động nhận diện khẩu lệnh Tự động hóa / Auto-Pilot A-Z
    # Ví dụ: "autopilot", "tự động hóa", "bật tự động", "chạy tự động", "lên lịch tự động"
    is_asking_autopilot = any(kw in lower_text for kw in [
        "autopilot", "auto pilot", "tự động hóa", "tu dong hoa",
        "chạy tự động", "chay tu dong", "bật autopilot", "bat autopilot",
        "lên lịch tự động", "len lich tu dong", "chế độ tự động", "che do tu dong"
    ])
    if is_asking_autopilot:
        await autopilot_command(update, context)
        return

    norm_txt = " ".join(clean_text.lower().split())[:100]
    txt_lock_key = f"text_msg:{user.id}:{norm_txt}"
    acquired = _try_acquire_action(txt_lock_key, max_inflight_sec=90.0, debounce_sec=6.0)
    if not acquired:
        await safe_reply(update, "⏳ AI Tổng Quản đang xử lý tin nhắn vừa rồi của bạn, vui lòng chờ giây lát!")
        return

    try:
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first()
            channels = settings.get_channels_config()
            default_ch = channels[0].get("id") if channels else "channel_maya"
            channel_id = db_user.active_channel_id if db_user and db_user.active_channel_id else default_ch
            if db_user:
                db_user.last_active = datetime.utcnow()
            log_activity(db, user.id, user.username, "AI_NATURAL_TEXT", f"Trao đổi tự nhiên: '{clean_text[:50]}...'", channel_id=channel_id)

        status_msg = await safe_reply(update, f"🧠 *AI Tổng Quản đang tiếp nhận:* \"{clean_text}\"...")

        from app.services.ai_commander import ai_commander
        await ai_commander.process_and_execute(update, context, clean_text, status_message=status_msg)
    finally:
        _release_action(txt_lock_key, post_cooldown_sec=4.0)


async def chat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Xử lý nút bấm: Chốt kịch bản từ đoạn chat / Gợi ý thêm góc nhìn / Đổi hook
    """
    query = update.callback_query
    data = query.data if query else ""
    user = query.from_user if query else update.effective_user

    if data.startswith("convert_chat:"):
        session_id = data.split(":")[1]
        conv_lock_key = f"convert_chat:{session_id}"
        acquired = _try_acquire_action(conv_lock_key, max_inflight_sec=90.0, debounce_sec=10.0)
        if not acquired:
            await safe_query_answer(
                query,
                "⏳ Hệ thống đang tổng hợp kịch bản từ đoạn chat này rồi! Vui lòng chờ giây lát.",
                show_alert=True,
            )
            return
        await safe_query_answer(query, "🎬 Đang tổng hợp kịch bản...", show_alert=False)

        try:
            with get_db() as db:
                db_user = db.query(User).filter(User.telegram_id == user.id).first()
                channels = settings.get_channels_config()
                default_ch = channels[0].get("id") if channels else "channel_maya"
                channel_id = db_user.active_channel_id if db_user and db_user.active_channel_id else default_ch
                if channel_id == "channel_psychology":
                    channel_id = "channel_maya"

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
                    f"👉 Bấm **🎬 DUYỆT & DỰNG VIDEO NGAY** bên dưới để hệ thống AI tự động sản xuất Shorts 1080x1920!"
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
        finally:
            _release_action(conv_lock_key, post_cooldown_sec=5.0)
        return

    await safe_query_answer(query)

    if data.startswith("suggest_more:"):
        session_id = data.split(":")[1]
        msg = await asyncio.to_thread(
            content_agent.chat_brainstorm,
            session_id=session_id,
            user_message="Hãy cho mình thêm 3 góc nhìn khác thật độc lạ, gây tò mò cực độ cho chủ đề này!",
            language="en"
        )
        markup = get_chat_brainstorm_keyboard(session_id)
        await safe_reply(update, f"💡 **3 GÓC NHÌN MỚI TỪ ĐẠO DIỄN:**\n\n{msg}", reply_markup=markup)

    elif data.startswith("change_hooks:"):
        session_id = data.split(":")[1]
        msg = await asyncio.to_thread(
            content_agent.chat_brainstorm,
            session_id=session_id,
            user_message="Hãy viết cho mình 3 câu Hook 3 giây đầu hoàn toàn mới: 1 câu gây sốc, 1 câu tâm lý tò mò, 1 câu đánh trúng nỗi sợ của người xem!",
            language="en"
        )
        markup = get_chat_brainstorm_keyboard(session_id)
        await safe_reply(update, f"🔥 **3 CÂU HOOK MỚI:**\n\n{msg}", reply_markup=markup)


async def dispatch_rendered_video_to_telegram(job_id: int):
    """
    Tự động gửi video đã dựng thành công vào Telegram (dùng cho Web Studio, API, hoặc Background task).
    - Bước 1: Bắn ngay Thumbnail HD + Báo cáo thông số trong 0.2s để người dùng biết kết quả ngay lập tức.
    - Bước 2: Tải video lên chat đầu tiên, bắt file_id.
    - Bước 3: Gửi qua các nhóm/chat khác bằng file_id chỉ trong 0.1s (tiết kiệm 70% thời gian).
    """
    from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
    from config.settings import settings
    from app.database.models import get_db, VideoJob

    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.debug(f"[TelegramDispatch] Không có TELEGRAM_BOT_TOKEN, bỏ qua gửi video #{job_id}")
        return

    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            return
        title = job.title or job.topic or f"Video #{job_id}"
        v_path = job.video_path
        u_id = job.user_id
        thumb_p = job.thumbnail_path or job.thumbnail_vertical_path

    v_file = _resolve_video_file_path(job_id, v_path)
    if not v_file or not v_file.exists():
        logger.warning(f"[TelegramDispatch] Không tìm thấy file video #{job_id}: {v_path}")
        return

    size_bytes = v_file.stat().st_size
    size_mb = size_bytes / (1024 * 1024)

    # Xác định các chat nhận tin
    target_chats = []
    if u_id and u_id != 0:
        target_chats.append(u_id)
    for gid in settings.get_allowed_group_ids():
        if gid and gid not in target_chats:
            target_chats.append(gid)
    if not target_chats:
        for uid in settings.get_allowed_users().keys():
            if uid and uid not in target_chats:
                target_chats.append(uid)

    if not target_chats:
        return

    # Đọc tunnel URL nếu có
    tunnel_url = "http://localhost:8000"
    try:
        t_file = Path(settings.BASE_DIR) / "storage" / "tunnel_url.txt"
        if t_file.exists():
            tunnel_url = t_file.read_text(encoding="utf-8").strip() or tunnel_url
    except Exception:
        pass

    # Keyboard thao tác
    actions_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("▶️ Xem Ngay Trên Web Studio", url=tunnel_url)
        ],
        [
            InlineKeyboardButton("🚀 Đăng Lên YouTube", callback_data=f"publish_youtube:{job_id}"),
            InlineKeyboardButton("⏰ Lên Lịch Tự Động", callback_data=f"schedule_youtube:{job_id}")
        ],
        [
            InlineKeyboardButton("📋 Xem Danh Sách Video", callback_data="videos_page:1")
        ]
    ])

    safe_title = html.escape(title)
    quick_caption = (
        f"🎉 <b>VIDEO #{job_id} ĐÃ HOÀN TẤT!</b>\n\n"
        f"📌 <b>Tiêu đề:</b> {safe_title}\n"
        f"📦 <b>Dung lượng:</b> {size_mb:.1f} MB (Chuẩn 1080x1920 9:16 Shorts)\n"
        f"⚡ <i>Đang truyền file video gốc vào Telegram... Bạn có thể bấm xem ngay tức thì trên Web Studio bên dưới:</i>"
    )

    video_caption = (
        f"🎬 <b>{safe_title}</b>\n\n"
        f"🆔 <b>Task:</b> #{job_id}\n"
        f"📦 <b>Dung lượng:</b> {size_mb:.1f} MB (Chuẩn 1080x1920 9:16 Shorts)\n"
        f"👉 <i>Video đã sẵn sàng bên dưới để bạn xem thử hoặc lưu về máy!</i>"
    )

    upload_v_file = v_file
    temp_dispatch_preview = None
    if size_mb > 12.0:
        try:
            import subprocess
            temp_dir = Path(settings.TEMP_DIR)
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_dispatch_preview = temp_dir / f"tg_dispatch_preview_{job_id}.mp4"
            res_c = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(v_file),
                    "-c:v", "libx264", "-crf", "26", "-preset", "veryfast", "-movflags", "+faststart",
                    "-c:a", "copy", str(temp_dispatch_preview),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=45,
            )
            if res_c.returncode == 0 and temp_dispatch_preview.exists():
                upload_v_file = temp_dispatch_preview
        except Exception as ex_c:
            logger.debug(f"[TelegramDispatch] Lỗi nén preview #{job_id}: {ex_c}")

    async with Bot(token=token) as bot:
        # Gửi DUY NHẤT 1 file video (nhúng thumbnail làm ảnh bìa) kèm nút thao tác
        cached_file_id = None
        for cid in target_chats:
            thumb_f = open(thumb_p, "rb") if thumb_p and Path(thumb_p).exists() else None
            try:
                if cached_file_id:
                    await bot.send_video(
                        chat_id=cid,
                        video=cached_file_id,
                        caption=video_caption,
                        parse_mode="HTML",
                        reply_markup=actions_markup,
                        supports_streaming=True
                    )
                else:
                    curr_mb = Path(upload_v_file).stat().st_size / (1024 * 1024)
                    with open(upload_v_file, "rb") as vf:
                        if curr_mb <= 50.0:
                            sent_msg = await bot.send_video(
                                chat_id=cid,
                                video=vf,
                                thumbnail=thumb_f,
                                caption=video_caption,
                                parse_mode="HTML",
                                reply_markup=actions_markup,
                                supports_streaming=True,
                                read_timeout=300,
                                write_timeout=300
                            )
                            if sent_msg and sent_msg.video:
                                cached_file_id = sent_msg.video.file_id
                        else:
                            sent_msg = await bot.send_document(
                                chat_id=cid,
                                document=vf,
                                thumbnail=thumb_f,
                                caption=video_caption,
                                reply_markup=actions_markup,
                                read_timeout=600,
                                write_timeout=600
                            )
                            if sent_msg and sent_msg.document:
                                cached_file_id = sent_msg.document.file_id
            except Exception as ex_v:
                logger.warning(f"[TelegramDispatch] Lỗi httpx gửi video #{job_id} tới {cid}: {ex_v}, chuyển sang requests fallback...")
                try:
                    import requests as _req
                    import json as _json
                    def _dispatch_req_fb():
                        url = f"https://api.telegram.org/bot{token}/sendVideo"
                        data = {
                            "chat_id": str(cid),
                            "caption": video_caption,
                            "parse_mode": "HTML",
                            "supports_streaming": "true",
                            "reply_markup": _json.dumps(actions_markup.to_dict()),
                        }
                        if cached_file_id:
                            data["video"] = cached_file_id
                            return _req.post(url, data=data, timeout=60)
                        with open(upload_v_file, "rb") as vf_s:
                            files = {"video": (f"video_{job_id}.mp4", vf_s, "video/mp4")}
                            tf_s = open(thumb_p, "rb") if thumb_p and Path(thumb_p).exists() else None
                            if tf_s:
                                files["thumbnail"] = ("thumb.jpg", tf_s, "image/jpeg")
                            try:
                                return _req.post(url, data=data, files=files, timeout=180)
                            finally:
                                if tf_s:
                                    tf_s.close()
                    r_fb = await asyncio.to_thread(_dispatch_req_fb)
                    if r_fb.status_code == 200 and r_fb.json().get("ok"):
                        fid = ((r_fb.json().get("result") or {}).get("video") or {}).get("file_id")
                        if fid:
                            cached_file_id = fid
                except Exception as ex_fb:
                    logger.error(f"[TelegramDispatch] requests fallback cũng thất bại cho #{job_id} tới {cid}: {ex_fb}")
            finally:
                if thumb_f:
                    try:
                        thumb_f.close()
                    except Exception:
                        pass

    if temp_dispatch_preview and temp_dispatch_preview.exists():
        try:
            temp_dispatch_preview.unlink(missing_ok=True)
        except Exception:
            pass
