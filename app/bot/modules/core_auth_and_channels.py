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


class _ActionPermit:
    """Cho phép dùng kết quả _try_acquire_action vừa như bool (`if not acquired:`) vừa unpack 2 giá trị (`acquired, wait = ...`)."""
    __slots__ = ("acquired", "wait_sec")

    def __init__(self, acquired: bool, wait_sec: float = 0.0):
        self.acquired = bool(acquired)
        self.wait_sec = float(wait_sec)

    def __bool__(self) -> bool:
        return self.acquired

    def __iter__(self):
        yield self.acquired
        yield self.wait_sec


def _try_acquire_action(key: str, max_inflight_sec: float = 120.0, debounce_sec: float = 4.0) -> _ActionPermit:
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
            return _ActionPermit(False, max(0.0, max_inflight_sec - (now - _INFLIGHT_ACTIONS[key])))
        if key in _ACTION_COOLDOWNS and now < _ACTION_COOLDOWNS[key]:
            return _ActionPermit(False, max(0.0, _ACTION_COOLDOWNS[key] - now))

        _INFLIGHT_ACTIONS[key] = now
        _ACTION_COOLDOWNS[key] = now + debounce_sec
        return _ActionPermit(True, 0.0)


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
                if not j.created_at or str(j.status or "").upper() in ("FAILED", "CANCELLED", "DELETED"):
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

    # 1. Thử gửi với định dạng yêu cầu (có retry 1 lần nếu lỗi mạng tạm thời httpx.ReadError/ConnectError)
    for attempt in range(2):
        try:
            if update.message:
                return await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            elif update.callback_query and getattr(update.callback_query, "message", None) and hasattr(update.callback_query.message, "reply_text"):
                return await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            elif update.effective_chat:
                return await update.effective_chat.send_message(text, reply_markup=reply_markup, parse_mode=parse_mode)
            break
        except Exception as e:
            err_cls = type(e).__name__
            is_net_err = any(k in err_cls or k in str(e) for k in ("ReadError", "ConnectError", "TimedOut", "NetworkError", "RemoteProtocolError"))
            if attempt == 0 and is_net_err:
                await asyncio.sleep(0.6)
                continue
            logger.warning(f"Lỗi gửi tin nhắn parse_mode={parse_mode} ({err_cls}: {e}), fallback sang Plain Text...")
            break

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
    """Edit tin nhắn an toàn với fallback toàn diện (retry giữ nguyên định dạng nếu đứt kết nối tạm thời)"""
    if not message or not text:
        return None

    if len(text) > 4000:
        text = text[:3980] + "\n\n...(nội dung còn tiếp)"

    for attempt in range(2):
        try:
            return await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e:
            err_str = str(e)
            if "Message is not modified" in err_str:
                return message
            err_cls = type(e).__name__
            is_net_err = any(k in err_cls or k in err_str for k in ("ReadError", "ConnectError", "TimedOut", "NetworkError", "RemoteProtocolError"))
            if attempt == 0 and is_net_err:
                await asyncio.sleep(0.6)
                continue
            logger.warning(f"Lỗi edit_text parse_mode={parse_mode} ({err_cls}: {e}), fallback sang Plain Text...")
            break

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
    - Nếu có cấu hình ALLOWED_TELEGRAM_USERS hoặc ALLOWED_GROUP_IDS: bắt buộc kiểm tra danh sách trắng hoặc thành viên đã được Admin cấp quyền trong Database.
    - Nếu không cấu hình danh sách trắng: Chỉ cho phép thành viên đã có trong Database (hoặc khởi tạo lần đầu khi DB trống).
    """
    user = update.effective_user
    chat = update.effective_chat

    # 1. Chủ sở hữu hệ thống luôn luôn được cấp quyền (đồng thời tự động lưu chat.id nếu đang ở trong Group/Supergroup mới)
    if user and user.id in (7177163590, 8498162618):
        if chat and chat.type in ("group", "supergroup") and getattr(chat, "id", 0) < 0:
            try:
                is_new_grp = settings.register_discovered_group(chat.id, getattr(chat, "title", "") or "")
                if is_new_grp:
                    logger.info(f"✨ [AutoDiscovery] Đã tự động ghi nhận nhóm Telegram mới '{getattr(chat, 'title', '')}' (chat_id={chat.id})")
                pend_file = settings.BASE_DIR / "storage" / "pending_group_preview.json"
                if pend_file.exists():
                    import json as _j_p
                    import requests as _r_p
                    p_data = _j_p.loads(pend_file.read_text(encoding="utf-8"))
                    sent_c = [str(x) for x in (p_data.get("sent_chats") or [])]
                    if str(chat.id) not in sent_c and p_data.get("file_id") and settings.TELEGRAM_BOT_TOKEN:
                        r_fw = _r_p.post(
                            f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendVideo",
                            data={
                                "chat_id": str(chat.id),
                                "video": p_data["file_id"],
                                "caption": p_data.get("caption", ""),
                                "parse_mode": "HTML",
                                "supports_streaming": "true",
                            },
                            timeout=15,
                        )
                        if r_fw.status_code == 200:
                            sent_c.append(str(chat.id))
                            p_data["sent_chats"] = sent_c
                            pend_file.write_text(_j_p.dumps(p_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        return True

    allowed_users = settings.get_allowed_users()
    allowed_groups = settings.get_allowed_group_ids()

    if chat and chat.type in ["group", "supergroup"]:
        if allowed_groups and chat.id in allowed_groups:
            return True
        if user and user.id in allowed_users:
            return True

    if user and user.id in allowed_users:
        return True

    # 2. Thành viên đã được lưu chính thức trong Database (và có vai trò hợp lệ)
    if user:
        try:
            with get_db() as db:
                db_user = db.query(User).filter(User.telegram_id == user.id).first()
                if db_user and db_user.role in ("admin", "owner", "editor", "creator"):
                    return True
                # Cho phép bootstrap duy nhất cho người dùng đầu tiên khi chưa cấu hình whitelist và DB hoàn toàn trống
                if not allowed_users and not allowed_groups:
                    human_count = db.query(User).filter(User.telegram_id > 0).count()
                    if human_count == 0:
                        return True
        except Exception:
            pass

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

    # 2. Kiểm tra vai trò trong Database (đã loại bỏ hoàn toàn cơ chế tự nâng quyền theo số lượng thành viên)
    try:
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == telegram_id).first()
            if db_user and db_user.role in ("admin", "owner"):
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
                     f"Khởi động bot từ {chat.type} '{chat.title or user.first_name}' (chat_id={chat.id}, Role: {db_user.role})")

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
        f"• `/idea <chủ đề>` - Lên kịch bản AI ngay tức thì (thêm `--long` nếu muốn tạo video dài 16:9)\n"
        f"• `/long <chủ đề>` - 🎬 **Tạo Phim Tài Liệu Dài 16:9 (~10 Phút)** (10 Chương, 40 Cảnh, Đa nguồn YouTube + Bilibili Safe-Crop + Pexels)\n"
        f"• `/bilibili <từ khóa/link>` - 🅱️ **Tìm kiếm, Tải & Cắt sạch Watermark Bilibili (16:9 / 9:16)** hoặc Dịch & Reup tự động (`/bili`, `/reup`)\n"
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
    if not is_authorized(update):
        await safe_query_answer(query, "⛔ Bạn chưa được cấp quyền sử dụng thao tác này.", show_alert=True)
        return
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
            "• `name`: Đổi tên kênh (vd: `/editchannel ch_psychology_of_the_world name Dark Psychology Pro`)\n"
            "• `niche`: Đổi chủ đề (vd: `/editchannel ch_psychology_of_the_world niche philosophy`)\n"
            "• `voice`: Đổi giọng đọc (vd: `/editchannel ch_psychology_of_the_world voice vi-VN-NamMinhNeural`)\n"
            "• `lang`: Đổi ngôn ngữ (vd: `/editchannel ch_psychology_of_the_world lang vi`)\n"
            "• `visual`: Đổi phong cách (vd: `/editchannel ch_psychology_of_the_world visual imagen_ai` hoặc `pexels_stock`)"
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
    """
    Tách chủ đề chính, các ghi chú đạo diễn (Director's Notes), ngôn ngữ chỉ định (Language)
    và có thể đọc thêm chế độ video (video_format: "short" | "long") thông qua cờ --long / --short.
    Giữ nguyên chữ ký trả về 3 phần tử (topic, notes, explicit_lang) để tương thích ngược,
    đồng thời hỗ trợ _extract_video_format_from_input(raw_input).
    """
    if not raw_input:
        return "", None, None

    text = raw_input.strip()
    explicit_lang = None

    # Loại bỏ cờ định dạng video khỏi chủ đề để tránh dính từ khóa --long / --short vào topic
    text = re.sub(r'(?:\s+|^)--(?:long|16:9|documentary|short|shorts|9:16)\b', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'^(?:\[(?:long|short)\]|(?:long|short):)\s*', '', text, flags=re.IGNORECASE).strip()

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


def _extract_video_format_from_input(raw_input: str) -> str:
    """Nhận diện chế độ video ngắn ('short') hay dài ('long') từ câu lệnh /idea của người dùng."""
    if not raw_input:
        return "short"
    raw_low = raw_input.strip().lower()
    if re.search(r'(?:\s+|^)--(?:long|16:9|documentary)\b', raw_low):
        return "long"
    if re.match(r'^(?:\[long\]|long:)\s*', raw_low):
        return "long"
    if re.search(r'\b(?:video\s+dài|phim\s+tài\s+liệu\s+dài|long\s+video|16:9)\b', raw_low):
        return "long"
    return "short"


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
    
    from app.database.models import normalize_channel_id
    current_channel_id = normalize_channel_id(current_channel_id) if current_channel_id else "ch_psychology_of_the_world"

    scores = {
        "ch_wild_odyssey": sum(1 for kw in wildlife_keywords if kw in t),
        "ch_paws_hearts_rescue": sum(1 for kw in rescue_keywords if kw in t),
        "ch_the_mysteries_of_the_world": sum(1 for kw in mystery_keywords if kw in t),
        "ch_psychology_of_the_world": sum(1 for kw in psychology_keywords if kw in t),
        "@alternatehistorywhatif-e2x": sum(1 for kw in history_keywords if kw in t),
    }
    
    best_candidate, best_score = max(scores.items(), key=lambda x: x[1])
    
    if best_score >= 1 and current_channel_id and best_candidate != current_channel_id:
        current_score = scores.get(current_channel_id, 0)
        if best_score > current_score:
            ch_names = {
                "ch_wild_odyssey": "Wild Odyssey (Động vật hoang dã)",
                "ch_paws_hearts_rescue": "Paws & Hearts Rescue (Cứu hộ thú cưng)",
                "ch_the_mysteries_of_the_world": "The mysteries of the world (Bí ẩn vũ trụ & đại dương)",
                "ch_psychology_of_the_world": "Psychology of the world (Tâm lý học)",
                "@alternatehistorywhatif-e2x": "Alternate History (Lịch sử giả định / What If)"
            }
            reason = f"Phát hiện chủ đề phù hợp với kênh {ch_names.get(best_candidate, best_candidate)} -> Tự động chuyển kênh mục tiêu!"
            return best_candidate, reason

    return current_channel_id or "ch_psychology_of_the_world", None


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

    return None

