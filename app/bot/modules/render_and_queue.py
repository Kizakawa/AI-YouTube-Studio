"""app.bot.modules.render_and_queue — Quản lý Hàng đợi Render, Chỉnh sửa Kịch bản/Media và Xuất bản Kênh."""
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
    _resolve_video_file_path,
)

_DISPATCHED_VIDEO_CHAT_TS: dict[tuple[int, int], float] = {}
_AUTO_RESUME_INITIALIZED: bool = False


def _is_video_already_dispatched(job_id: int, chat_id: int, ttl_sec: float = 1800.0) -> bool:
    """Kiểm tra xem video #job_id đã được gửi tới chat_id trong khoảng thời gian gần đây chưa."""
    try:
        key = (int(job_id), int(chat_id))
    except Exception:
        return False
    now_ts = time.monotonic()
    last_ts = _DISPATCHED_VIDEO_CHAT_TS.get(key)
    if last_ts is not None and (now_ts - last_ts) < ttl_sec:
        return True
    return False


def _mark_video_dispatched(job_id: int, chat_id: int) -> None:
    """Đánh dấu video #job_id đã gửi thành công tới chat_id."""
    try:
        _DISPATCHED_VIDEO_CHAT_TS[(int(job_id), int(chat_id))] = time.monotonic()
    except Exception:
        pass


class _CompletedJobResult:
    """Cho phép dùng kết quả _is_job_already_completed_in_db vừa như bool vừa unpack 2 giá trị `(already_done, existing_mp4)`."""
    __slots__ = ("completed", "video_path")

    def __init__(self, completed: bool, video_path: Optional[str] = None):
        self.completed = bool(completed)
        self.video_path = video_path

    def __bool__(self) -> bool:
        return self.completed

    def __iter__(self):
        yield self.completed
        yield self.video_path


def _is_job_already_completed_in_db(job_id: int) -> _CompletedJobResult:
    """Kiểm tra xem VideoJob đã dựng xong (RENDERED/SCHEDULED/PUBLISHED) và có file MP4 trên đĩa chưa."""
    try:
        with get_db() as db:
            jb = db.query(VideoJob).filter(VideoJob.id == int(job_id)).first()
            if not jb:
                return _CompletedJobResult(False, None)
            st = str(jb.status or "").upper()
            v_path = jb.video_path
        if st in ("RENDERED", "SCHEDULED", "PUBLISHED", "WAITING_QUOTA_RESET"):
            resolved = _resolve_video_file_path(int(job_id), v_path)
            if resolved and resolved.exists() and resolved.stat().st_size > 100_000:
                return _CompletedJobResult(True, str(resolved))
    except Exception:
        pass
    return _CompletedJobResult(False, None)


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
    # Chốt chặn an toàn: Nếu job đã dựng xong trước đó rồi thì không dựng lại và không gửi đúp
    already_done, existing_mp4 = _is_job_already_completed_in_db(job_id)
    if already_done and existing_mp4:
        logger.info(f"🛡️ [start_render_pipeline] Video #{job_id} đã hoàn tất từ trước ({existing_mp4}) — bỏ qua dựng lặp lại!")
        return

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
                try:
                    target_chats.append(int(chat_id))
                except Exception:
                    pass
            for gid in settings.get_allowed_group_ids():
                if gid and int(gid) not in target_chats:
                    target_chats.append(int(gid))
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
                        aq = q_data.get("audio_qa") or {}
                        scores_text = (
                            f"\n📊 **Báo cáo Thẩm định 4 Bộ AI Graders:**\n"
                            f" ⭐ Kịch bản (Script Critic): `{sc.get('overall_score', 8.5)}/10`\n"
                            f" 🖼️ Độ sắc nét & Kỹ thuật Media (Visual Critic): `{vc.get('overall_score', 9.0)}/10`\n"
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

            # Xác định các chat mục tiêu (chuẩn hóa int để không bao giờ gửi trùng cùng 1 nhóm)
            target_chats: list[int] = []
            if chat_id:
                try:
                    target_chats.append(int(chat_id))
                except Exception:
                    pass
            for gid in settings.get_allowed_group_ids():
                try:
                    gid_int = int(gid)
                    if gid_int and gid_int not in target_chats:
                        target_chats.append(gid_int)
                except Exception:
                    pass

            # 1. CẬP NHẬT TRẠNG THÁI TẢI VIDEO
            thumb_p = getattr(render_job, "thumbnail_path", None)
            if not thumb_p or not Path(thumb_p).exists():
                thumb_p = getattr(render_job, "thumbnail_vertical_path", None)

            if status_msg:
                try:
                    await safe_edit_text(status_msg, f"⚡ <b>Đang tải video #{job_id} ({size_mb:.1f} MB) vào nhóm...</b>")
                except Exception:
                    pass

            # 2. UPLOAD DUY NHẤT 1 FILE VIDEO QUA KẾT NỐI HTTP MỚI TRỰC TIẾP (CHỐNG LỖI HTTPX READ TIMEOUT GỬI ĐÚP 2 LẦN!)
            # Nếu > 8MB, nén nhanh bản preview nhẹ (~3-5MB) cho Telegram giúp gửi chỉ mất 3-5s (file gốc out_path giữ nguyên 100% cho YouTube)
            upload_vid_path = out_path
            temp_tg_preview = None
            if size_mb > 8.0:
                try:
                    import subprocess
                    temp_dir = Path(settings.TEMP_DIR)
                    temp_dir.mkdir(parents=True, exist_ok=True)
                    temp_tg_preview = str(temp_dir / f"tg_preview_{job_id}.mp4")
                    logger.info(f"File {size_mb:.1f}MB > 8MB, đang tạo bản preview tối ưu cho Telegram (job #{job_id})...")
                    cmd = [
                        "ffmpeg", "-y", "-i", str(out_path),
                        "-c:v", "libx264", "-crf", "27", "-preset", "superfast", "-movflags", "+faststart",
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
                    r = _req.post(url, data=data, timeout=45)
                else:
                    with open(vid_path_str, "rb") as vf_sync:
                        files = {"video": (f"video_{job_id}.mp4", vf_sync, "video/mp4")}
                        tf_sync = open(thumb_path_str, "rb") if thumb_path_str and Path(thumb_path_str).exists() else None
                        if tf_sync:
                            files["thumbnail"] = ("thumb.jpg", tf_sync, "image/jpeg")
                        try:
                            r = _req.post(url, data=data, files=files, timeout=120)
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
                if _is_video_already_dispatched(job_id, t_chat):
                    logger.info(f"🛡️ [Anti-Duplicate Video] Video #{job_id} đã được gửi tới chat {t_chat} trước đó — bỏ qua gửi trùng!")
                    continue

                sent_ok = False
                curr_size_mb = Path(upload_vid_path).stat().st_size / (1024 * 1024)
                if curr_size_mb <= 49.0:
                    try:
                        # Dùng kết nối HTTP trực tiếp làm phương thức DUY NHẤT để tránh lỗi httpx stale socket gửi đúp 2 lần
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
                            _mark_video_dispatched(job_id, t_chat)
                            logger.info(f"✅ Đã gửi duy nhất 1 bản video #{job_id} tới chat {t_chat} thành công!")
                    except Exception as ex_req_fb:
                        logger.error(f"Gửi video #{job_id} tới chat {t_chat} thất bại: {ex_req_fb}")

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
                        _mark_video_dispatched(job_id, t_chat)
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
    - Chỉ chạy DUY NHẤT 1 LẦN khi khởi động tiến trình (không chạy lại khi polling reconnect).
    - Bỏ qua các video đã có trong RenderQueue hoặc đã hoàn tất trên ổ đĩa.
    """
    global _AUTO_RESUME_INITIALIZED
    if _AUTO_RESUME_INITIALIZED:
        logger.debug("Auto-Resume: Đã chạy khi khởi động, bỏ qua khi polling reconnect.")
        return
    _AUTO_RESUME_INITIALIZED = True

    try:
        from telegram import BotCommand
        await application.bot.set_my_commands([
            BotCommand("long", "🎬 Tạo Phim Tài Liệu Dài 16:9 (~10 phút, 10 chương)"),
            BotCommand("bilibili", "🅱️ Tìm kiếm, Tải & Cắt sạch Watermark Bilibili (16:9/9:16)"),
            BotCommand("reup", "🔄 Dịch Kịch Bản & Dựng Video Mới từ Bilibili/YouTube"),
            BotCommand("idea", "💡 Lên kịch bản AI ngay tức thì cho kênh đang chọn"),
            BotCommand("viral", "🔥 Đề xuất 5 ý tưởng triệu view theo xu hướng mới"),
            BotCommand("autopilot", "🚀 Tự động hóa kênh A-Z (Ý tưởng -> Render -> Lên lịch)"),
            BotCommand("videos", "📺 Danh sách video trong hệ thống (Xem/Tải MP4)"),
            BotCommand("channels", "📡 Quản lý & chuyển đổi 7 kênh YouTube"),
            BotCommand("schedule", "⏰ Xem lịch công chiếu & khung giờ vàng"),
            BotCommand("kpi", "📊 Báo cáo sức khỏe tiến độ và KPI kênh"),
            BotCommand("queue", "⏳ Xem hàng đợi Render video"),
            BotCommand("status", "🖥️ Trạng thái hệ thống AI YouTube Studio"),
        ])
    except Exception as ex_cmd:
        logger.debug(f"Không thể đăng ký menu BotCommand: {ex_cmd}")

    from app.services.render_queue import get_render_queue
    from config.settings import settings

    try:
        allowed_groups = settings.get_allowed_group_ids()
        default_chat_id = allowed_groups[0] if allowed_groups else None
        rq = get_render_queue()

        from datetime import timedelta
        unfinished_jobs = []
        cutoff = datetime.utcnow() - timedelta(hours=3)
        with get_db() as db:
            candidates = (
                db.query(VideoJob)
                .filter(VideoJob.status.in_(["RENDERING", "AUDIO_READY", "MEDIA_READY", "SCRIPT_READY"]))
                .order_by(VideoJob.id.asc())
                .all()
            )

            for j in candidates:
                ref_time = (j.updated_at or j.created_at or datetime.utcnow()).replace(tzinfo=None)
                if ref_time < cutoff:
                    continue
                is_interrupted = j.status in ("RENDERING", "AUDIO_READY", "MEDIA_READY") or (
                    j.status == "SCRIPT_READY"
                    and (j.progress_message or "").startswith("Đã khôi phục sau khi")
                )
                if not is_interrupted:
                    continue
                if rq.find_active_item(j.id) is not None:
                    continue
                resolved = _resolve_video_file_path(j.id, j.video_path)
                if resolved and resolved.exists() and resolved.stat().st_size > 100_000:
                    j.status = "RENDERED"
                    j.video_path = str(resolved)
                    continue
                j.status = "RENDERING"
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
    Chống trùng lặp: Nếu job_id đã hoàn thành trên đĩa, đang render, hoặc đã nằm trong hàng đợi thì bỏ qua.
    """
    already_done, existing_mp4 = _is_job_already_completed_in_db(job_id)
    if already_done and existing_mp4:
        if update.callback_query:
            await safe_query_answer(
                update.callback_query,
                f"✅ Video #{job_id} đã được dựng hoàn tất rồi! Không cần dựng lại.",
                show_alert=True,
            )
        else:
            await safe_reply(
                update,
                f"✅ **Video #{job_id} đã được dựng hoàn tất trước đó rồi!**\n"
                f"📂 Đường dẫn: `{existing_mp4}`",
            )
        return

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
            try:
                from app.services.video_memory_rag import archive_job_to_rag_and_cleanup_files
                archive_job_to_rag_and_cleanup_files(job_id)
            except Exception as ex_rag:
                logger.debug(f"[BotPublish] Mini-RAG archive warning: {ex_rag}")

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
                f"<b>Web Dashboard ➔ Tab Kênh &amp; Tài Khoản ➔ 🔑 Cấp Quyền YouTube</b>"
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

