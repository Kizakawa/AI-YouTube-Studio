import logging
import re
import html
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes
from config.settings import settings
from app.bot.keyboards import get_channel_selection_keyboard, get_video_review_keyboard, get_chat_brainstorm_keyboard
from app.database.models import get_db, User, VideoJob, log_activity
from app.agents.content_agent import ContentAgent
from app.schemas.contracts import ScriptContract

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
        f"• `/status` - Báo cáo số lượng video và trạng thái hệ thống\n\n"
        f"👉 Gõ `/chat` hoặc `/idea` để bắt đầu sáng tạo video cùng nhóm!"
    )
    await safe_reply(update, welcome_text)


async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị danh sách kênh cho cả nhóm cùng chọn"""
    if not is_authorized(update):
        return

    reply_markup = get_channel_selection_keyboard()
    await safe_reply(
        update,
        "📺 **Chọn kênh YouTube mục tiêu cho dự án tiếp theo:**",
        reply_markup=reply_markup
    )


async def channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý khi một thành viên bấm nút chọn kênh"""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("select_channel:"):
        channel_id = data.split(":")[1]
        user = query.from_user

        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first()
            if db_user:
                db_user.active_channel_id = channel_id
                db_user.last_active = datetime.utcnow()

            log_activity(db, user.id, user.username, "CHANGE_CHANNEL",
                         f"Chuyển kênh làm việc sang '{channel_id}'", channel_id=channel_id)

        await safe_reply(
            update,
            f"✅ **{user.first_name}** đã chọn kênh: **{channel_id}**\n"
            f"Bây giờ cả nhóm có thể gõ: `/idea <chủ đề>` để bắt đầu tạo video cho kênh này!"
        )


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
        script: ScriptContract = content_agent.generate_script(
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
            f"👉 Cả nhóm hãy góp ý hoặc bấm nút bên dưới để tiến hành sản xuất video!"
        )

        review_markup = get_video_review_keyboard(job_id)
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


async def video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    [NEW] Xử lý khi thành viên bấm nút: Đăng Ngay / Lên Lịch / Tạo Lại / Hủy
    """
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data.startswith("publish_now:"):
        job_id = int(data.split(":")[1])
        # TODO Phase 2: Gọi pipeline.execute() → PublisherAgent
        await safe_reply(
            update,
            f"🚀 **{user.first_name}** yêu cầu đăng video #{job_id} ngay!\n"
            f"⏳ Đang xếp hàng vào pipeline render..."
        )

    elif data.startswith("schedule:"):
        job_id = int(data.split(":")[1])
        await safe_reply(
            update,
            f"⏰ **{user.first_name}** yêu cầu lên lịch video #{job_id}!\n"
            f"⏳ Chức năng đặt lịch sẽ hoạt động sau khi render xong."
        )

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


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Báo cáo tổng hợp số lượng video và tài nguyên"""
    if not is_authorized(update):
        return

    with get_db() as db:
        total_jobs = db.query(VideoJob).count()
        ready_jobs = db.query(VideoJob).filter(VideoJob.status == "SCRIPT_READY").count()

    text = (
        f"📊 **BÁO CÁO NHÓM LÀM VIỆC (AI STUDIO)**\n\n"
        f"• Tổng số video trong hệ thống: `{total_jobs}`\n"
        f"• Kịch bản đang chờ duyệt/render: `{ready_jobs}`\n"
        f"• Kho Google Drive 2TB: Sẵn sàng 🟢\n"
        f"• Cụm Google Ultra Quota: Đang hoạt động 🟢\n"
        f"• Web Dashboard: Đang trực tuyến tại cổng `http://localhost:8000` 🌐"
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

    ai_reply = content_agent.chat_brainstorm(
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

    ai_reply = content_agent.chat_brainstorm(
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
    await query.answer()
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
            script: ScriptContract = content_agent.convert_discussion_to_script(
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
                f"👉 Bấm nút bên dưới để tiến hành đưa vào hàng chờ render video!"
            )

            review_markup = get_video_review_keyboard(job_id)
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
        msg = content_agent.chat_brainstorm(
            session_id=session_id,
            user_message="Hãy cho mình thêm 3 góc nhìn khác thật độc lạ, gây tò mò cực độ cho chủ đề này!",
            language="vi"
        )
        markup = get_chat_brainstorm_keyboard(session_id)
        await safe_reply(update, f"💡 **3 GÓC NHÌN MỚI TỪ ĐẠO DIỄN:**\n\n{msg}", reply_markup=markup)

    elif data.startswith("change_hooks:"):
        session_id = data.split(":")[1]
        msg = content_agent.chat_brainstorm(
            session_id=session_id,
            user_message="Hãy viết cho mình 3 câu Hook 3 giây đầu hoàn toàn mới: 1 câu gây sốc, 1 câu tâm lý tò mò, 1 câu đánh trúng nỗi sợ của người xem!",
            language="vi"
        )
        markup = get_chat_brainstorm_keyboard(session_id)
        await safe_reply(update, f"🔥 **3 CÂU HOOK MỚI:**\n\n{msg}", reply_markup=markup)

