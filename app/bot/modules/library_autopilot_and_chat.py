"""app.bot.modules.library_autopilot_and_chat — Kho Video, Tìm kiếm Media, Lịch đăng, KPI, AutoPilot và Trợ lý Chat AI."""
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

from app.bot.modules.core_auth_and_channels import (
    content_agent,
    _ActionPermit,
    _try_acquire_action,
    _release_action,
    _find_recent_duplicate_job,
    safe_reply,
    safe_edit_text,
    safe_query_answer,
    safe_answer_query,
    is_authorized,
    is_admin_user,
    start_command,
    _format_channel_info_text,
    channels_command,
    channel_callback,
    addchannel_command,
    editchannel_command,
    delchannel_command,
    _parse_idea_input,
    _extract_video_format_from_input,
    detect_best_channel_for_topic,
)
from app.bot.modules.ideation_and_bilibili import (
    idea_command,
    viral_command,
    longform_command,
    bilibili_command,
    _execute_bilibili_search_and_reply,
)
from app.bot.modules.render_and_queue import (
    start_render_pipeline,
    _enqueue_render,
    _is_video_already_dispatched,
    _mark_video_dispatched,
    render_command,
    edit_command,
    media_command,
    queue_command,
    pending_command,
    _handle_script_revision,
)

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
                f"👉 Bấm nút bên dưới để mở xem hoặc tải video từ kho lưu trữ Google Drive!",
                reply_markup=actions_markup
            )
            return

        await safe_reply(
            update,
            f"⚠️ **Không tìm thấy tệp video của Job #{job_id} trên máy chủ!**\n\n"
            f"• Trạng thái trong DB: `{status}`\n"
            f"• Đường dẫn dự kiến: `{recorded_path or 'storage/outputs/video_' + str(job_id) + '.mp4'}`\n"
            f"Bạn có thể render lại bằng cách gõ `/render {job_id}`."
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
    from app.database.models import normalize_channel_id
    channels = settings.get_channels_config()
    default_ch_id = channels[0].get("id") if channels else "ch_psychology_of_the_world"

    if not channel_id:
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first()
            channel_id = db_user.active_channel_id if db_user and db_user.active_channel_id else default_ch_id
    channel_id = normalize_channel_id(channel_id)

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
    if not is_authorized(update):
        if query:
            await safe_query_answer(query, "⛔ Bạn chưa được cấp quyền sử dụng thao tác này.", show_alert=True)
        return
    await safe_query_answer(query)
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
        from app.database.models import normalize_channel_id
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first()
            channels = settings.get_channels_config()
            default_ch = channels[0].get("id") if channels else "ch_psychology_of_the_world"
            channel_id = normalize_channel_id(db_user.active_channel_id if db_user and db_user.active_channel_id else default_ch)
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

    # 3.5. Tự động nhận diện khi người dùng dán trực tiếp link Bilibili (bilibili.com, b23.tv) hoặc mã BV1...
    from app.services.media.bilibili_fetcher import is_bilibili_source
    if is_bilibili_source(clean_text):
        context.args = [clean_text]
        await bilibili_command(update, context)
        return

    norm_txt = " ".join(clean_text.lower().split())[:100]
    txt_lock_key = f"text_msg:{user.id}:{norm_txt}"
    acquired = _try_acquire_action(txt_lock_key, max_inflight_sec=90.0, debounce_sec=6.0)
    if not acquired:
        await safe_reply(update, "⏳ AI Tổng Quản đang xử lý tin nhắn vừa rồi của bạn, vui lòng chờ giây lát!")
        return

    try:
        from app.database.models import normalize_channel_id
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first()
            channels = settings.get_channels_config()
            default_ch = channels[0].get("id") if channels else "ch_psychology_of_the_world"
            channel_id = normalize_channel_id(db_user.active_channel_id if db_user and db_user.active_channel_id else default_ch)
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
    if not is_authorized(update):
        if query:
            await safe_query_answer(query, "⛔ Bạn chưa được cấp quyền sử dụng thao tác này.", show_alert=True)
        return
    await safe_query_answer(query)
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
            from app.database.models import normalize_channel_id
            with get_db() as db:
                db_user = db.query(User).filter(User.telegram_id == user.id).first()
                channels = settings.get_channels_config()
                default_ch = channels[0].get("id") if channels else "ch_psychology_of_the_world"
                raw_ch = db_user.active_channel_id if db_user and db_user.active_channel_id else default_ch
                channel_id = normalize_channel_id(raw_ch)

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
        is_long_job = '"video_format": "long"' in (job.scenes_json or "") or '"aspect_ratio": "16:9"' in (job.scenes_json or "")

    v_file = _resolve_video_file_path(job_id, v_path)
    if not v_file or not v_file.exists():
        logger.warning(f"[TelegramDispatch] Không tìm thấy file video #{job_id}: {v_path}")
        return

    size_bytes = v_file.stat().st_size
    size_mb = size_bytes / (1024 * 1024)

    # Xác định các chat nhận tin (chuẩn hóa sang int để chống trùng lặp str/int)
    target_chats = []
    if u_id and int(u_id) != 0:
        target_chats.append(int(u_id))
    for gid in settings.get_allowed_group_ids():
        if gid and int(gid) not in target_chats:
            target_chats.append(int(gid))
    if not target_chats:
        for uid in settings.get_allowed_users().keys():
            if uid and int(uid) not in target_chats:
                target_chats.append(int(uid))

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
    video_caption = (
        f"🎬 <b>{safe_title}</b>\n\n"
        f"🆔 <b>Task:</b> #{job_id}\n"
        f"📦 <b>Dung lượng:</b> {size_mb:.1f} MB ({'Chuẩn 1920x1080 16:9 Documentary' if is_long_job else 'Chuẩn 1080x1920 9:16 Shorts'})\n"
        f"👉 <i>Video đã sẵn sàng bên dưới để bạn xem thử hoặc lưu về máy!</i>"
    )

    upload_v_file = v_file
    temp_dispatch_preview = None
    if size_mb > 8.0:
        try:
            import subprocess
            temp_dir = Path(settings.TEMP_DIR)
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_dispatch_preview = temp_dir / f"tg_dispatch_preview_{job_id}.mp4"
            res_c = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(v_file),
                    "-c:v", "libx264", "-crf", "27", "-preset", "superfast", "-movflags", "+faststart",
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

    import requests as _req
    import json as _json
    cached_file_id = None
    for cid in target_chats:
        if _is_video_already_dispatched(job_id, cid):
            logger.info(f"🛡️ [TelegramDispatch] Bỏ qua gửi trùng Video #{job_id} tới chat {cid}")
            continue
        _mark_video_dispatched(job_id, cid)
        try:
            def _dispatch_req_upload(target_cid=cid, fid=cached_file_id):
                url = f"https://api.telegram.org/bot{token}/sendVideo"
                data = {
                    "chat_id": str(target_cid),
                    "caption": video_caption,
                    "parse_mode": "HTML",
                    "supports_streaming": "true",
                    "reply_markup": _json.dumps(actions_markup.to_dict()),
                }
                if fid:
                    data["video"] = fid
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

            r_fb = await asyncio.to_thread(_dispatch_req_upload)
            if r_fb.status_code == 200 and r_fb.json().get("ok"):
                fid = ((r_fb.json().get("result") or {}).get("video") or {}).get("file_id")
                if fid:
                    cached_file_id = fid
            else:
                logger.warning(f"[TelegramDispatch] Telegram API trả lỗi cho #{job_id} tới {cid}: {r_fb.text[:200]}")
        except Exception as ex_fb:
            logger.error(f"[TelegramDispatch] Lỗi gửi video #{job_id} tới {cid}: {ex_fb}")

    if temp_dispatch_preview and temp_dispatch_preview.exists():
        try:
            temp_dispatch_preview.unlink(missing_ok=True)
        except Exception:
            pass
