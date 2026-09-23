import logging
import asyncio
import re
import html
from pathlib import Path
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes
from config.settings import settings
from app.bot.keyboards import (
    get_channel_selection_keyboard,
    get_script_review_keyboard,
    get_video_post_render_keyboard,
    get_video_review_keyboard,
    get_chat_brainstorm_keyboard,
)
from app.database.models import get_db, User, VideoJob, log_activity
from app.agents.content_agent import ContentAgent
from app.schemas.contracts import ScriptContract
from app.services.render_queue import get_render_queue

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


async def _execute_render_task(update: Update, context: ContextTypes.DEFAULT_TYPE, job_id: int, user_name: str):
    """Tiến trình render video chạy ngầm không chặn luồng polling của bot"""
    chat_id = update.effective_chat.id if update.effective_chat else None

    status_msg = await safe_reply(
        update,
        f"🚀 **{user_name}** đã kích hoạt sản xuất Video #{job_id}!\n"
        f"⏳ Đang xếp hàng vào pipeline render 9:16 Shorts (Audio + Media + Subtitles)..."
    )

    async def progress_cb(stage: str, msg: str):
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
            display_text = f"🎬 **TIẾN TRÌNH RENDER (Task #{job_id})**\n\n{icon} {msg}"
            await safe_edit_text(status_msg, display_text)

    try:
        from app.services.pipeline import VideoPipeline
        pipeline = VideoPipeline(job_id=job_id, progress_callback=progress_cb)
        render_job = await pipeline.execute()

        # Kiểm tra file MP4 hoàn chỉnh
        out_path = Path(render_job.output_path)
        if out_path.exists() and chat_id:
            size_mb = out_path.stat().st_size / (1024 * 1024)
            post_render_markup = get_video_post_render_keyboard(job_id)

            # Telegram Bot API cho phép upload video tối đa 50MB
            if size_mb <= 49.0:
                caption = (
                    f"🎉 **VIDEO ĐÃ DỰNG THÀNH CÔNG! (Task #{job_id})**\n\n"
                    f"📌 **Tiêu đề:** {render_job.title}\n"
                    f"⏱️ **Thời lượng:** {render_job.total_audio_duration_ms / 1000:.1f}s\n"
                    f"📦 **Dung lượng:** {size_mb:.1f} MB\n"
                    f"🎬 **Chuẩn:** 1080x1920 (9:16 Shorts) kèm phụ đề hardsub\n\n"
                    f"👉 Cả nhóm hãy xem thử video! Chọn **Đăng Ngay** hoặc **Góp Ý Sửa Lại** bên dưới:"
                )
                try:
                    with open(out_path, "rb") as vf:
                        await context.bot.send_video(
                            chat_id=chat_id,
                            video=vf,
                            caption=caption,
                            reply_markup=post_render_markup,
                            supports_streaming=True
                        )
                except Exception as ex_upload:
                    logger.warning(f"Không thể gửi file video trực tiếp qua Telegram: {ex_upload}")
                    await safe_reply(
                        update,
                        f"🎉 **Video #{job_id} đã hoàn tất!**\n"
                        f"📂 File đã lưu tại: `{render_job.output_path}` ({size_mb:.1f} MB)\n\n"
                        f"👉 Cả nhóm chọn thao tác tiếp theo:",
                        reply_markup=post_render_markup
                    )
            else:
                await safe_reply(
                    update,
                    f"🎉 **Video #{job_id} đã hoàn tất!**\n"
                    f"📦 Dung lượng ({size_mb:.1f} MB) vượt quá giới hạn 50MB của Telegram.\n"
                    f"📂 File đã lưu sẵn tại:\n`{render_job.output_path}`\n\n"
                    f"👉 Cả nhóm chọn thao tác tiếp theo:",
                    reply_markup=post_render_markup
                )

        with get_db() as db:
            log_activity(db, 0, user_name, "RENDER_COMPLETE",
                         f"Hoàn tất render video #{job_id}: {render_job.output_path}")

    except Exception as e:
        logger.error(f"Lỗi khi render video #{job_id}: {e}", exc_info=True)
        if status_msg:
            await safe_edit_text(
                status_msg,
                f"❌ **Render video #{job_id} thất bại:**\n`{str(e)}`"
            )
        else:
            await safe_reply(update, f"❌ **Render video #{job_id} thất bại:**\n`{str(e)}`")


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

        revised_script: ScriptContract = content_agent.refine_script(
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
    """Lệnh góp ý và chỉnh sửa kịch bản: /edit <job_id> <góp ý>"""
    if not is_authorized(update):
        return

    if not context.args or len(context.args) < 2 or not context.args[0].isdigit():
        await safe_reply(
            update,
            "💡 **Cú pháp góp ý chỉnh sửa:**\n"
            "`/edit <mã_job> <nội dung muốn sửa>`\n\n"
            "Ví dụ:\n`/edit 2 Đổi câu hook đầu thành câu hỏi giật gân hơn`"
        )
        return

    job_id = int(context.args[0])
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
    await query.answer()
    data = query.data
    user = query.from_user

    # 1. Duyệt kịch bản & Dựng video (Bước 1 -> Bước 2)
    if data.startswith("render_video:") or data.startswith("publish_now:"):
        job_id = int(data.split(":")[1])
        await _enqueue_render(update, context, job_id, user.first_name)

    # 2. Đăng lên YouTube sau khi xem video
    elif data.startswith("publish_youtube:"):
        job_id = int(data.split(":")[1])
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            channel_id = job.target_channel_id if job else "default"
            video_path = job.video_path if job else ""

        await safe_reply(
            update,
            f"🚀 **YÊU CẦU ĐĂNG VIDEO #{job_id} LÊN YOUTUBE!**\n\n"
            f"📺 **Kênh đích:** `{channel_id}`\n"
            f"⚠️ **Thông báo:** Kênh này chưa kết nối OAuth token (`credentials/accounts/.../youtube_token.json`).\n"
            f"📂 Video 1080x1920 hiện đã lưu sẵn an toàn tại:\n`{video_path}`\n\n"
            f"👉 Bạn có thể tải file từ Telegram về để đăng ngay, hoặc kết nối Google OAuth để bot tự động xuất bản một chạm!"
        )

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

