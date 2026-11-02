"""app.bot.modules.video_callbacks — Xử lý nút bấm tương tác Video, Duyệt kịch bản, Chỉnh sửa và Xóa Job."""
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
from app.bot.modules.render_and_queue import (
    start_render_pipeline,
    _enqueue_render,
    _execute_channel_publish,
    _handle_script_revision,
    _is_job_already_completed_in_db,
    pending_command,
)
from app.bot.modules.library_autopilot_and_chat import (
    _SEARCH_CACHE,
    _show_videos_list,
    send_video_file_handler,
    _show_schedule_view,
    kpi_command,
)


async def video_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Xử lý nút bấm trên kịch bản & video: Duyệt Dựng / Đăng YouTube / Góp Ý Sửa / Lên Lịch / Tạo Lại / Hủy
    """
    query = update.callback_query
    if not is_authorized(update):
        if query:
            await safe_query_answer(query, "⛔ Bạn chưa được cấp quyền sử dụng thao tác này.", show_alert=True)
        return
    await safe_query_answer(query)
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
        already_done, existing_mp4 = _is_job_already_completed_in_db(job_id)
        if already_done and existing_mp4:
            await safe_query_answer(
                query,
                f"✅ Video #{job_id} đã được dựng hoàn tất rồi! Không cần dựng lại.",
                show_alert=True,
            )
            return

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

    if data.startswith("back_script_review:"):
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

    elif data.startswith(("render_local:", "check_drive_render:")):
        job_id = int(data.split(":")[1])
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

            # 1. Tải lên Google Drive chỉ khi ENABLE_DRIVE_VIDEO_BACKUP=true (mặc định tắt để tránh lưu trữ video nặng)
            drive_link = None
            folder_link = None
            if os.getenv("ENABLE_DRIVE_VIDEO_BACKUP", "false").lower() in ("1", "true", "yes"):
                drive_agent = DriveAgent(account_name=acc_id)
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
                    pinned_comment = pub_agent.build_default_engagement_comment(title=video_title, target_lang=ch_cfg.get("language", "en"))
                elif any(k in n_key for k in ("rescue", "animal")):
                    pinned_comment = pub_agent.build_default_engagement_comment(title=video_title, target_lang=ch_cfg.get("language", "en"))
                elif any(k in n_key for k in ("deep_sea", "mystery", "history")):
                    pinned_comment = pub_agent.build_default_engagement_comment(title=video_title, target_lang=ch_cfg.get("language", "en"))
                elif any(k in n_key for k in ("predator", "wildlife")):
                    pinned_comment = pub_agent.build_default_engagement_comment(title=video_title, target_lang=ch_cfg.get("language", "en"))
                else:
                    pinned_comment = pub_agent.build_default_engagement_comment(title=video_title, target_lang=ch_cfg.get("language", "en"))

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
                try:
                    from app.services.video_memory_rag import archive_job_to_rag_and_cleanup_files
                    archive_job_to_rag_and_cleanup_files(job_id)
                except Exception as ex_rag:
                    logger.debug(f"[BotSchedule] Mini-RAG archive warning: {ex_rag}")

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
                    f"<b>Web Dashboard ➔ Tab Kênh &amp; Tài Khoản ➔ 🔑 Cấp Quyền YouTube</b>"
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
                channel_id = job.target_channel_id or "ch_psychology_of_the_world"
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
                cur_vf = getattr(revised_script, "video_format", getattr(current_script, "video_format", "short"))
                markup = get_script_review_keyboard(job_id, current_lang=target_lang, video_format=cur_vf)

                reply_text = (
                    f"🎬 **KỊCH BẢN ĐÃ CHUYỂN SANG {target_lang_display.upper()} (Task #{job_id})**\n\n"
                    f"📌 **Tiêu đề:** {revised_script.title}\n"
                    f"🔥 **Hook mở đầu:** \"{revised_script.hook}\"\n\n"
                    f"📝 **Phân cảnh tiêu biểu:**\n{scenes_preview}\n\n"
                    f"⏱️ **Ước lượng:** ~{revised_script.total_estimated_duration:.0f}s | {len(revised_script.scenes)} cảnh\n"
                    f"🗣️ **Người đổi:** {user.first_name} | 📺 **Kênh:** `{channel_name}` | 🌐 **Ngôn ngữ:** {target_lang_display}\n\n"
                    f"👉 Cả nhóm hãy xem kịch bản, bấm **DUYỆT & DỰNG VIDEO NGAY** bên dưới để máy chủ tự động sản xuất!"
                )
                await safe_edit_text(wait_m, reply_text, reply_markup=markup)
            except Exception as ex:
                logger.error(f"Lỗi chuyển ngôn ngữ kịch bản #{job_id}: {ex}", exc_info=True)
                await safe_edit_text(wait_m, f"❌ Lỗi khi chuyển đổi ngôn ngữ: `{ex}`")
        finally:
            _release_action(lang_lock_key, post_cooldown_sec=3.0)

    # 5.2 Đổi chế độ Video Ngắn (9:16 Shorts) <-> Video Dài (16:9 Documentary ~4 phút)
    elif data.startswith("switch_format:"):
        parts = data.split(":")
        job_id = int(parts[1])
        target_format = parts[2] if len(parts) > 2 else "long"
        target_format = "long" if str(target_format).lower() in ("long", "16:9", "documentary") else "short"
        fmt_lock_key = f"script_op:{job_id}"
        acquired = _try_acquire_action(fmt_lock_key, max_inflight_sec=110.0, debounce_sec=5.0)
        if not acquired:
            await safe_reply(update, f"⏳ Kịch bản #{job_id} đang được AI xử lý, vui lòng chờ giây lát!")
            return

        try:
            with get_db() as db:
                job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if not job:
                    await safe_reply(update, f"❌ Không tìm thấy VideoJob #{job_id}")
                    return
                channel_id = job.target_channel_id or "ch_psychology_of_the_world"
                topic = job.topic or job.title or "Viral Concept"
                from app.agents.voice_agent import VoiceAgent
                is_vn = VoiceAgent._is_vietnamese(job.script or job.scenes_json or "")
                current_lang = "vi" if is_vn else "en"

            ch = channel_manager.get_channel(channel_id) or {}
            channel_name = ch.get("name") or channel_id
            channel_niche = ch.get("niche") or "general"
            lang_display = "🇻🇳 Tiếng Việt" if current_lang == "vi" else "🇺🇸 Tiếng Anh"
            fmt_label = "🎬 Video Dài 16:9 (10 chương, 40 cảnh, ~10 phút)" if target_format == "long" else "📱 Video Ngắn 9:16 (Shorts 7–9 cảnh, ~40s)"

            wait_m = await safe_reply(
                update,
                f"🎞️ **{user.first_name}** yêu cầu chuyển kịch bản Task #{job_id} sang chế độ **{fmt_label}**...\n"
                f"📌 Chủ đề: *\"{topic}\"*\n"
                f"⏳ Gemini AI đang tái cấu trúc toàn bộ kịch bản theo chuẩn {fmt_label}..."
            )

            try:
                new_script, eval_report = await asyncio.to_thread(
                    content_agent.generate_best_script,
                    topic=topic,
                    channel_niche=channel_niche,
                    language=current_lang,
                    video_format=target_format,
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
                        log_activity(db, user.id, user.first_name, "SWITCH_FORMAT",
                                     f"Chuyển kịch bản #{job_id} sang {target_format}: '{new_script.title}'",
                                     channel_id=channel_id)

                scenes_preview = "\n".join([
                    f"• Cảnh {s.scene_id}: {s.narration[:65]}..."
                    for s in new_script.scenes[:4]
                ])
                markup = get_script_review_keyboard(job_id, current_lang=current_lang, video_format=target_format)

                reply_text = (
                    f"🎬 **ĐÃ CHUYỂN SANG {fmt_label.upper()} (Task #{job_id})**\n\n"
                    f"📌 **Tiêu đề:** {new_script.title}\n"
                    f"🔥 **Hook mở đầu:** \"{new_script.hook}\"\n\n"
                    f"📝 **Phân cảnh tiêu biểu:**\n{scenes_preview}\n\n"
                    f"⏱️ **Ước lượng:** ~{new_script.total_estimated_duration:.0f}s | {len(new_script.scenes)} cảnh\n"
                    f"🗣️ **Người đổi:** {user.first_name} | 📺 **Kênh:** `{channel_name}` | 🌐 **Ngôn ngữ:** {lang_display}\n\n"
                    f"👉 Bấm **DUYỆT & DỰNG VIDEO NGAY** bên dưới để máy chủ tự động dựng theo đúng chế độ đã chọn!"
                )
                await safe_edit_text(wait_m, reply_text, reply_markup=markup)
            except Exception as ex:
                logger.error(f"Lỗi chuyển chế độ video cho kịch bản #{job_id}: {ex}", exc_info=True)
                await safe_edit_text(wait_m, f"❌ Lỗi khi chuyển chế độ video: `{ex}`")
        finally:
            _release_action(fmt_lock_key, post_cooldown_sec=3.0)

    # 5. Tạo lại kịch bản từ đầu
    elif data.startswith("regenerate:"):
        job_id = int(data.split(":")[1])
        regen_lock_key = f"script_op:{job_id}"
        acquired = _try_acquire_action(regen_lock_key, max_inflight_sec=110.0, debounce_sec=5.0)
        if not acquired:
            await safe_reply(update, f"⏳ Kịch bản #{job_id} đang được AI tái tạo, vui lòng chờ giây lát!")
            return

        try:
            current_vf = "short"
            with get_db() as db:
                job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if not job:
                    await safe_reply(update, f"❌ Không tìm thấy VideoJob #{job_id}")
                    return
                channel_id = job.target_channel_id or "ch_psychology_of_the_world"
                topic = job.topic or job.title or "Viral Concept"
                if job.scenes_json:
                    try:
                        from app.schemas.contracts import ScriptContract
                        sc_tmp = ScriptContract.from_json_str(job.scenes_json)
                        current_vf = getattr(sc_tmp, "video_format", "short")
                    except Exception:
                        pass
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
                    language=current_lang,
                    video_format=current_vf,
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
                markup = get_script_review_keyboard(job_id, current_lang=current_lang, video_format=current_vf)

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

        from app.database.models import normalize_channel_id
        raw_ch = context.chat_data.get("cached_viral_channel", "ch_psychology_of_the_world") if context.chat_data else "ch_psychology_of_the_world"
        channel_id = normalize_channel_id(raw_ch)
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
            f"⏳ Đang kích hoạt Gemini AI lên kịch bản chuẩn giữ chân cho kênh `{channel_name}`..."
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
        from app.database.models import normalize_channel_id
        raw_ch = data.split(":")[1] if ":" in data else "ch_psychology_of_the_world"
        channel_id = normalize_channel_id(raw_ch)
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




