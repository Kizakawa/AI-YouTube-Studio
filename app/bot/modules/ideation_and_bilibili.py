"""app.bot.modules.ideation_and_bilibili — Lên ý tưởng Viral, Tạo Kịch bản Shorts/Long-Form và Săn Bilibili."""
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
from app.bot.modules.render_and_queue import start_render_pipeline, _enqueue_render



async def viral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lệnh gợi ý 5 chủ đề Shorts triệu view theo công thức giữ chân đỉnh cao (150-160%+ Retention).
    Đánh trúng tâm lý, red flags, vạch trần dối trá, thao túng tâm lý ngầm.
    Cung cấp bàn phím 1-Click để chọn ý tưởng và tự động sản xuất video ngay lập tức!
    """
    if not is_authorized(update):
        return

    user = update.effective_user
    from app.database.models import normalize_channel_id

    # Lấy kênh đang hoạt động của user và lịch sử các video đã sản xuất
    recent_titles = []
    with get_db() as db:
        db_user = db.query(User).filter(User.telegram_id == user.id).first()
        channel_id = normalize_channel_id(db_user.active_channel_id if db_user and db_user.active_channel_id else "ch_psychology_of_the_world")
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

    from app.database.models import normalize_channel_id
    if not context.args:
        user = update.effective_user
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first()
            active_id = normalize_channel_id(db_user.active_channel_id if db_user and db_user.active_channel_id else "ch_psychology_of_the_world")
        ch = channel_manager.get_channel(active_id) or {}
        ch_name = ch.get("name") or active_id
        ch_lang = ch.get("language") or "en"
        ch_lang_name = "Tiếng Việt (vi)" if ch_lang.startswith("vi") else "Tiếng Anh (en)"
        await safe_reply(
            update,
            f"💡 **HƯỚNG DẪN TẠO Ý TƯỞNG & KỊCH BẢN CHO KÊNH `{ch_name}`**\n\n"
            "1️⃣ **Nhập chủ đề tùy ý (Chọn Ngôn ngữ & Chế độ Video Ngắn/Dài):**\n"
            "   • Video Ngắn (Shorts 9:16 ~40s): `/idea en <chủ đề>` hoặc `/idea vi <chủ đề>`\n"
            "   • Video Dài (Documentary 16:9 ~10 phút, 10 chương): `/long <chủ đề>` hoặc `/idea en <chủ đề> --long`\n"
            "   • Thêm ghi chú: `/idea vi 3 bí ẩn đáy biển --long | phong cách rùng rợn, nghẹt thở`\n"
            f"   _(Ngôn ngữ mặc định kênh hiện tại: **{ch_lang_name}**)_\n\n"
            "2️⃣ **AI tự động phân tích kênh & đề xuất 5 ý tưởng triệu view:**\n"
            "   Gõ `/viral` (hoặc `/ideas`) hoặc bấm nút bên dưới để AI đối chiếu video top & flop, loại bỏ bão hòa và đón đầu xu hướng 2026!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"🔥 Đề xuất 5 ý tưởng triệu view cho {ch_name}", callback_data=f"refresh_viral_ideas:{active_id}")
            ]])
        )
        return

    raw_topic = " ".join(context.args)
    video_format = _extract_video_format_from_input(raw_topic)
    clean_topic, director_notes, explicit_lang = _parse_idea_input(raw_topic)
    if not clean_topic:
        clean_topic = raw_topic

    # Dọn dẹp dấu thời gian vô tình bị dính vào cuối câu (ví dụ copy paste dính 22:51 hoặc 222:51)
    clean_topic = re.sub(r'[\s\d]*\d{1,2}:\d{2}\s*$', '', clean_topic).strip()

    user = update.effective_user
    idea_lock_key = f"idea_cmd:{user.id if user else 0}"
    if not _try_acquire_action(idea_lock_key, max_inflight_sec=110.0, debounce_sec=5.0):
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
            active_id = normalize_channel_id(override_ch_id or (db_user.active_channel_id if db_user and db_user.active_channel_id else "ch_psychology_of_the_world"))
            
            # Tự động phát hiện và chuyển đúng kênh nếu chủ đề lệch ngách (Ví dụ: chim muông -> Wild Odyssey)
            channel_id, switch_reason = detect_best_channel_for_topic(clean_topic, active_id)

            if db_user:
                db_user.last_active = datetime.utcnow()
                if not db_user.active_channel_id:
                    db_user.active_channel_id = channel_id

            log_activity(db, user.id, user.username, "CMD_IDEA",
                         f"Yêu cầu lên ý tưởng video ({video_format}): '{clean_topic}'", channel_id=channel_id)

        # Chống tạo đúp nhiều VideoJob nếu người dùng bấm/gửi trùng cùng 1 chủ đề trong 4 phút gần nhất
        dup_job_id = _find_recent_duplicate_job(channel_id, clean_topic, window_seconds=240)
        if dup_job_id and video_format == "short":
            ch_dup = channel_manager.get_channel(channel_id) or {}
            ch_lang_dup = explicit_lang or ch_dup.get("language") or "en"
            await safe_reply(
                update,
                f"🛡️ **Chống tạo trùng:** Chủ đề *\"{clean_topic}\"* vừa mới được tạo ở **Task #{dup_job_id}**!\n"
                f"👉 Bạn có thể bấm nút bên dưới để duyệt & dựng ngay **Task #{dup_job_id}** mà không cần tạo lại:",
                reply_markup=get_script_review_keyboard(dup_job_id, current_lang=ch_lang_dup, video_format=video_format),
            )
            return

        # Lấy cấu hình niche & ngôn ngữ từ kênh (ưu tiên cờ ngôn ngữ explicit nếu có)
        ch = channel_manager.get_channel(channel_id) or {}
        channel_name = ch.get("name") or channel_id
        channel_niche = ch.get("niche") or "history_mystery"
        channel_lang = explicit_lang or ch.get("language") or "en"
        lang_display = "🇻🇳 Tiếng Việt" if channel_lang.lower().startswith("vi") else "🇺🇸 Tiếng Anh"
        format_display = "🎬 Video Dài 16:9 (~10 phút, 10 chương)" if video_format == "long" else "📱 Video Ngắn 9:16 (Shorts ~40s)"

        notes_info = f"\n🎯 **Hướng đạo diễn:** _{director_notes}_" if director_notes else ""
        switch_info = f"\n💡 *{switch_reason}*" if switch_reason else ""
        status_msg = await safe_reply(
            update,
            f"🤖 **{user.first_name}** vừa yêu cầu lên ý tưởng: *\"{clean_topic}\"*{notes_info}{switch_info}\n"
            f"📺 Kênh: `{channel_name}` ({channel_niche}) | 🌐 Ngôn ngữ: **{lang_display}**\n"
            f"🎞️ Chế độ: **{format_display}**\n"
            f"⏳ Đang kích hoạt Gemini AI biên soạn kịch bản..."
        )

        try:
            # ContentAgent tạo kịch bản và tự động chấm điểm qua ScriptCritic (tự tinh chỉnh nếu chưa đạt)
            script, eval_report = await asyncio.to_thread(
                content_agent.generate_best_script,
                topic=clean_topic,
                channel_niche=channel_niche,
                language=channel_lang,
                director_notes=director_notes,
                video_format=video_format,
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
                    topic=clean_topic[:1000],
                    title=(script.title or clean_topic)[:490],
                    script=script.full_narration,
                    description=script.description,
                    tags=",".join(script.tags)[:1500],
                    scenes_json=script.to_json_str(),   # Lưu structured data
                    status="SCRIPT_READY"
                )
                db.add(job)
                db.flush()  # Lấy job.id trước commit
                job_id = job.id

                log_activity(db, user.id, user.username, "SCRIPT_CREATED",
                             f"Kịch bản #{job_id} ({video_format}) đã hoàn thành: '{script.title}'",
                             channel_id=channel_id)
                # Lưu ý: Chỉ lưu vào bộ nhớ (VideoMemoryRAG / UsedTopic) sau khi video thực sự được đăng lên YouTube.

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
                f"🎞️ **Định dạng:** {format_display}\n"
                f"🔥 **Hook mở đầu:** \"{script.hook}\"\n\n"
                f"{formula_display}"
                f"📝 **Phân cảnh tiêu biểu:**\n{scenes_preview}\n\n"
                f"{monetization_card}"
                f"{pinned_display}"
                f"{notes_display}"
                f"⏱️ **Ước lượng:** ~{script.total_estimated_duration:.0f}s | {len(script.scenes)} cảnh\n"
                f"🗣️ **Người tạo:** {user.first_name} | 📺 **Kênh:** `{channel_name}` ({channel_niche}) | 🌐 **Ngôn ngữ:** {lang_display}\n\n"
                f"👉 Cả nhóm hãy xem kịch bản, bấm **DUYỆT & DỰNG VIDEO NGAY** (hoặc chuyển đổi Ngắn/Dài 1 chạm) bên dưới!"
            )

            review_markup = get_script_review_keyboard(job_id, current_lang=channel_lang, video_format=getattr(script, "video_format", video_format))
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


_LONGFORM_PRESET_TOPICS = [
    "Why Colossal Squids And Sperm Whales Fight In Complete Pitch Blackness At 2000 Meters Depth",
    "How A Lone Siberian Tiger Tracks Prey Across Frozen Taiga Blizzards For 72 Hours",
    "The Psychological Trap Of Sunk Cost Fallacy That Keeps Smart People In Toxic Situations",
]

_BILIBILI_QUICK_SEARCHES = [
    "colossal squid vs sperm whale deep sea 3d",
    "siberian tiger hunting in snowstorm documentary",
    "golden retriever rescuing puppy in flood",
]


async def longform_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh tắt /long (/longform, /doc, /16x9) để tạo Phim Tài Liệu Dài 16:9 (~10 phút, 10 chương, 40 cảnh)."""
    if not is_authorized(update):
        return

    from app.database.models import normalize_channel_id
    if not context.args:
        user = update.effective_user
        with get_db() as db:
            db_user = db.query(User).filter(User.telegram_id == user.id).first() if user else None
            active_id = normalize_channel_id(
                db_user.active_channel_id if db_user and db_user.active_channel_id else "ch_psychology_of_the_world"
            )
        ch = channel_manager.get_channel(active_id) or {}
        ch_name = ch.get("name") or active_id
        ch_lang = ch.get("language") or "en"
        ch_lang_name = "Tiếng Việt (vi)" if ch_lang.startswith("vi") else "Tiếng Anh (en)"

        preset_buttons = [
            [InlineKeyboardButton("🦑 Mẫu #1: Đại Chiến Mực Khổng Lồ & Cá Nhà Táng (16:9)", callback_data="bili_long_preset:0")],
            [InlineKeyboardButton("🐅 Mẫu #2: Hổ Siberia Săn Mồi Giữa Bão Tuyết (16:9)", callback_data="bili_long_preset:1")],
            [InlineKeyboardButton("🧠 Mẫu #3: Bẫy Tâm Lý Chi Phí Chìm Đắt Giá (16:9)", callback_data="bili_long_preset:2")],
        ]
        await safe_reply(
            update,
            f"🎬 **TRUNG TÂM SẢN XUẤT PHIM TÀI LIỆU DÀI 16:9 (~10 PHÚT)**\n"
            f"📺 **Kênh đang chọn:** `{ch_name}` | 🌐 **Ngôn ngữ mặc định:** **{ch_lang_name}**\n\n"
            "✨ **Kiến trúc Long-Form 16:9 Đa Nguồn (Zero Loop):**\n"
            "• **10 Chương × 4 Phân cảnh = 40 Cảnh (~9.5 – 10.5 phút)**, ~1,450 từ giọng đọc cuốn hút.\n"
            "• **Đa nguồn 16:9 HD (1920x1080):** Tự động kết hợp **YouTube 16:9** + **Bilibili 16:9 (Safe-Crop sạch watermark)** + **Pexels/Pixabay HD**.\n"
            "• **Chapter-Pool Jump-Cut:** Cắt nối các đoạn hành động không trùng lặp (`used_intervals`), tuyệt đối không lặp cảnh.\n\n"
            "📌 **Cú pháp sử dụng ngay:**\n"
            "• `/long <chủ đề>` — Tạo kịch bản phim dài 16:9 theo ngôn ngữ mặc định của kênh\n"
            "• `/long vi <chủ đề>` — Tạo kịch bản phim dài 16:9 bằng **Tiếng Việt**\n"
            "• `/long en <chủ đề>` — Tạo kịch bản phim dài 16:9 bằng **Tiếng Anh**\n"
            "• `/long <chủ đề> | <ghi chú đạo diễn>` — Kèm chỉ đạo nghệ thuật riêng\n\n"
            "👉 **Hoặc bấm 1 chạm vào các chủ đề mẫu bên dưới để test ngay:**",
            reply_markup=InlineKeyboardMarkup(preset_buttons),
        )
        return

    raw_joined = " ".join(context.args)
    if _extract_video_format_from_input(raw_joined) != "long":
        context.args = list(context.args) + ["--long"]
    await idea_command(update, context)


async def bilibili_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lệnh /bilibili (/bili, /reup, /repurpose): Tìm kiếm Bilibili, tải & cắt sạch watermark 16:9/9:16, hoặc dịch & dựng video mới."""
    if not is_authorized(update):
        return

    from app.services.media.bilibili_fetcher import (
        is_bilibili_source,
        extract_bilibili_id,
        translate_query_to_chinese,
        search_bilibili_videos,
        get_bilibili_video_info_and_streams,
    )

    if not context.args:
        sample_buttons = [
            [InlineKeyboardButton("🦑 Tìm Bilibili #1: Mực Khổng Lồ vs Cá Nhà Táng 3D", callback_data="bili_quick_search:0")],
            [InlineKeyboardButton("🐅 Tìm Bilibili #2: Hổ Siberia Săn Mồi Bão Tuyết", callback_data="bili_quick_search:1")],
            [InlineKeyboardButton("🐕 Tìm Bilibili #3: Giải Cứu Chó Golden Cảm Động", callback_data="bili_quick_search:2")],
        ]
        await safe_reply(
            update,
            "🅱️ **TRUNG TÂM KHAI THÁC & REUP VIDEO BILIBILI / ĐA NGUỒN (16:9 & 9:16)**\n\n"
            "Hệ thống hỗ trợ 3 chế độ tự động (0đ phí API, tự động dịch sang tiếng Trung & cắt sạch 100% watermark góc):\n\n"
            "1️⃣ **Tìm kiếm nguồn video Bilibili theo từ khóa bất kỳ (Việt / Anh / Trung):**\n"
            "   • `/bilibili đại chiến mực khổng lồ và cá nhà táng`\n"
            "   • `/bili siberian tiger hunting in snowstorm`\n\n"
            "2️⃣ **Tải & Cắt Sạch Watermark (16:9 HD hoặc 9:16 Shorts) từ Link / Mã BV:**\n"
            "   • `/bilibili https://www.bilibili.com/video/BV1...`\n"
            "   • `/bili BV1xx411c7mD`\n\n"
            "3️⃣ **Dịch Kịch Bản & Dựng Thành Video Mới (AI Lồng Tiếng + Phối Đa Nguồn):**\n"
            "   • `/reup <link Bilibili hoặc YouTube>`\n"
            "   *(Hoặc dán trực tiếp link `bilibili.com` / `b23.tv` vào khung chat)*\n\n"
            "👉 **Bấm thử 1 chạm bên dưới để test tìm kiếm & cắt video Bilibili ngay:**",
            reply_markup=InlineKeyboardMarkup(sample_buttons),
        )
        return

    raw_query = " ".join(context.args).strip()

    # Trường hợp 1: Người dùng gửi trực tiếp Link Bilibili hoặc mã BV1... / av...
    if is_bilibili_source(raw_query):
        wait_msg = await safe_reply(
            update,
            f"🅱️ **Đang kết nối Bilibili API & trích xuất luồng gốc (DASH/MP4)...**\n"
            f"🔗 Nguồn: `{raw_query}`"
        )
        try:
            info = await asyncio.to_thread(get_bilibili_video_info_and_streams, raw_query)
            if not info:
                parsed = extract_bilibili_id(raw_query) or {}
                bvid_fallback = parsed.get("id") or raw_query
                await safe_edit_text(
                    wait_msg,
                    f"⚠️ Không lấy được luồng trực tiếp cho `{bvid_fallback}` (video có thể bị giới hạn vùng hoặc cần tài khoản VIP Bilibili)."
                )
                return

            bvid = info.get("bvid") or (extract_bilibili_id(raw_query) or {}).get("id") or raw_query
            title = info.get("title") or bvid
            desc = (info.get("description") or "").strip()
            dur_s = float(info.get("duration") or 0.0)
            w = int(info.get("width") or 1920)
            h = int(info.get("height") or 1080)
            stream_type = str(info.get("stream_type") or "dash").upper()

            if context.chat_data is None:
                context.chat_data = {}
            bili_meta_cache = context.chat_data.setdefault("bili_meta_cache", {})
            bili_meta_cache[bvid] = {
                "bvid": bvid,
                "title": title,
                "description": desc[:500],
                "duration": dur_s,
                "width": w,
                "height": h,
                "webpage_url": f"https://www.bilibili.com/video/{bvid}",
            }

            desc_preview = f"\n📝 **Mô tả gốc:** _{desc[:180]}..._" if desc else ""
            text = (
                f"✅ **ĐÃ BÓC TÁCH THÀNH CÔNG VIDEO BILIBILI (`{bvid}`)**\n\n"
                f"📌 **Tiêu đề gốc:** {title}\n"
                f"📐 **Độ phân giải gốc:** `{w}x{h}` ({stream_type}) | ⏱️ **Thời lượng:** `{dur_s:.0f}s`\n"
                f"🔗 **Link gốc:** https://www.bilibili.com/video/{bvid}"
                f"{desc_preview}\n\n"
                f"👉 **Chọn thao tác xử lý 1 chạm bên dưới:**"
            )
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🖥️ Tải & Cắt Sạch Watermark 16:9 HD", callback_data=f"bili_dl_16x9:{bvid}"),
                ],
                [
                    InlineKeyboardButton("📱 Tải & Cắt Sạch Watermark 9:16 Shorts", callback_data=f"bili_dl_9x16:{bvid}"),
                ],
                [
                    InlineKeyboardButton("🎬 Dịch & Lên Phim Dài 16:9 (~10 Phút)", callback_data=f"bili_reup_long:{bvid}"),
                ],
                [
                    InlineKeyboardButton("⚡ Dịch & Lên Video Shorts 9:16 (~45s)", callback_data=f"bili_reup_short:{bvid}"),
                ],
            ])
            await safe_edit_text(wait_msg, text, reply_markup=kb)
        except Exception as e:
            logger.error(f"[BilibiliCmd] Lỗi trích xuất Bilibili {raw_query}: {e}", exc_info=True)
            await safe_edit_text(wait_msg, f"❌ Lỗi khi phân tích nguồn Bilibili: `{e}`")
        return

    # Trường hợp 2: Người dùng gửi Link YouTube / TikTok / nền tảng khác qua lệnh /reup
    if raw_query.startswith("http://") or raw_query.startswith("https://"):
        wait_msg = await safe_reply(
            update,
            f"🌐 **Đang trích xuất dữ liệu & kịch bản từ đường dẫn...**\n"
            f"🔗 URL: `{raw_query}`"
        )
        try:
            from app.services.video_repurposer import video_repurposer
            import hashlib
            meta = await asyncio.to_thread(video_repurposer.get_video_metadata, raw_query)
            if not meta:
                await safe_edit_text(wait_msg, "❌ Không thể trích xuất dữ liệu từ URL này. Vui lòng kiểm tra lại đường dẫn!")
                return
            url_key = "url_" + hashlib.md5(raw_query.encode("utf-8")).hexdigest()[:10]
            if context.chat_data is None:
                context.chat_data = {}
            bili_meta_cache = context.chat_data.setdefault("bili_meta_cache", {})
            bili_meta_cache[url_key] = {
                "bvid": url_key,
                "title": meta.get("title") or "Viral Video",
                "description": (meta.get("description") or "")[:500],
                "duration": float(meta.get("duration") or 0.0),
                "webpage_url": raw_query,
            }
            text = (
                f"✅ **ĐÃ BÓC TÁCH NỘI DUNG VIDEO GỐC**\n\n"
                f"📌 **Tiêu đề gốc:** {meta.get('title')}\n"
                f"👤 **Kênh gốc:** `{meta.get('uploader', 'Unknown')}` | ⏱️ **Thời lượng:** `{meta.get('duration', 0)}s`\n\n"
                f"👉 **Bạn muốn AI dịch & biên tập lại thành định dạng nào?**"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎬 Dịch & Dựng Phim Dài 16:9 (~10 Phút)", callback_data=f"bili_reup_long:{url_key}")],
                [InlineKeyboardButton("⚡ Dịch & Dựng Video Shorts 9:16 (~45s)", callback_data=f"bili_reup_short:{url_key}")],
            ])
            await safe_edit_text(wait_msg, text, reply_markup=kb)
        except Exception as e:
            logger.error(f"[ReupCmd] Lỗi trích xuất URL {raw_query}: {e}", exc_info=True)
            await safe_edit_text(wait_msg, f"❌ Lỗi khi phân tích URL: `{e}`")
        return

    # Trường hợp 3: Tìm kiếm Bilibili theo từ khóa (Việt / Anh / Trung)
    await _execute_bilibili_search_and_reply(update, context, raw_query)


async def _execute_bilibili_search_and_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_query: str):
    """Dịch từ khóa sang tiếng Trung giản thể, tìm top 5 video Bilibili và hiển thị nút bấm 1 chạm."""
    from app.services.media.bilibili_fetcher import (
        translate_query_to_chinese,
        search_bilibili_videos,
    )

    wait_msg = await safe_reply(
        update,
        f"🅱️ **Đang dịch từ khóa sang tiếng Trung & quét kho video HD Bilibili...**\n"
        f"🔍 Từ khóa: *\"{raw_query}\"*"
    )
    try:
        query_zh = await asyncio.to_thread(translate_query_to_chinese, raw_query)
        results = await asyncio.to_thread(
            search_bilibili_videos,
            query_zh,
            5,
            10.0,
            1800.0,
        )
        if not results and query_zh != raw_query:
            results = await asyncio.to_thread(
                search_bilibili_videos,
                raw_query,
                5,
                10.0,
                1800.0,
            )

        if not results:
            await safe_edit_text(
                wait_msg,
                f"⚠️ Không tìm thấy video Bilibili phù hợp cho từ khóa *\"{raw_query}\"* (Tiếng Trung: `{query_zh}`).\n"
                f"👉 Hãy thử từ khóa ngắn gọn hơn (VD: `/bilibili colossal squid` hoặc `/bilibili siberian tiger`)."
            )
            return

        if context.chat_data is None:
            context.chat_data = {}
        bili_meta_cache = context.chat_data.setdefault("bili_meta_cache", {})

        lines = [
            f"🅱️ **TOP {len(results)} VIDEO BILIBILI HD KHỚP VỚI: \"{raw_query}\"**",
            f"🌐 **Truy vấn tiếng Trung:** `{query_zh}`\n",
        ]
        kb_rows = []
        for idx, item in enumerate(results, 1):
            bvid = item.get("bvid") or ""
            title = item.get("title") or bvid
            dur = float(item.get("duration") or 0.0)
            play = int(item.get("view_count") or item.get("play") or 0)
            author = item.get("uploader") or item.get("author") or "Bilibili Creator"
            url = item.get("url") or item.get("webpage_url") or f"https://www.bilibili.com/video/{bvid}"
            bili_meta_cache[bvid] = {
                "bvid": bvid,
                "title": title,
                "description": (item.get("description") or "")[:500],
                "duration": dur,
                "webpage_url": url,
                "search_query": raw_query,
            }
            mins = int(dur // 60)
            secs = int(dur % 60)
            dur_str = f"{mins}:{secs:02d}" if mins > 0 else f"{secs}s"
            lines.append(
                f"**#{idx}. {title[:75]}**\n"
                f"• 🆔 `{bvid}` | ⏱️ `{dur_str}` | 👁️ `{play:,}` views | 👤 `{author}`\n"
                f"• 🔗 {url}"
            )
            kb_rows.append([
                InlineKeyboardButton(f"🖥️ Cắt 16:9 #{idx}", callback_data=f"bili_dl_16x9:{bvid}"),
                InlineKeyboardButton(f"📱 Cắt 9:16 #{idx}", callback_data=f"bili_dl_9x16:{bvid}"),
                InlineKeyboardButton(f"🎬 Dựng Phim 16:9 #{idx}", callback_data=f"bili_reup_long:{bvid}"),
            ])

        lines.append(
            "\n👉 **Bấm nút tương ứng bên dưới:**\n"
            "• **🖥️ Cắt 16:9 / 📱 Cắt 9:16**: Tải luồng HD từ Bilibili, tự động Safe-Crop 14% sạch watermark góc & gửi file MP4 thẳng vào Telegram!\n"
            "• **🎬 Dựng Phim 16:9**: Dùng chủ đề & nội dung video này để lên kịch bản Phim Tài Liệu Dài 16:9 (~10 phút)!"
        )
        await safe_edit_text(wait_msg, "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb_rows))
    except Exception as e:
        logger.error(f"[BilibiliSearch] Lỗi tìm kiếm Bilibili '{raw_query}': {e}", exc_info=True)
        await safe_edit_text(wait_msg, f"❌ Lỗi khi tìm kiếm trên Bilibili: `{e}`")


async def bilibili_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý các nút bấm tương tác Bilibili & Long-Form 16:9 (bili_quick_search, bili_long_preset, bili_dl_16x9, bili_dl_9x16, bili_reup_long, bili_reup_short)."""
    query = update.callback_query
    if not is_authorized(update):
        await safe_query_answer(query, "⛔ Bạn chưa được cấp quyền sử dụng thao tác này.", show_alert=True)
        return

    data = query.data or ""
    user = query.from_user

    # 1. Nút bấm tìm kiếm nhanh Bilibili mẫu
    if data.startswith("bili_quick_search:"):
        await safe_query_answer(query, "🔍 Đang quét kho video Bilibili...")
        idx = int(data.split(":")[1])
        sample_q = _BILIBILI_QUICK_SEARCHES[idx] if 0 <= idx < len(_BILIBILI_QUICK_SEARCHES) else _BILIBILI_QUICK_SEARCHES[0]
        await _execute_bilibili_search_and_reply(update, context, sample_q)
        return

    # 2. Nút bấm tạo nhanh Phim Dài 16:9 theo chủ đề mẫu
    if data.startswith("bili_long_preset:"):
        await safe_query_answer(query, "🎬 Đang khởi tạo kịch bản Phim Dài 16:9...")
        idx = int(data.split(":")[1])
        preset_topic = _LONGFORM_PRESET_TOPICS[idx] if 0 <= idx < len(_LONGFORM_PRESET_TOPICS) else _LONGFORM_PRESET_TOPICS[0]
        context.args = [preset_topic, "--long"]
        await idea_command(update, context)
        return

    # 3. Tải & Safe-Crop sạch watermark 16:9 hoặc 9:16 từ Bilibili và gửi thẳng file MP4 vào Telegram
    if data.startswith("bili_dl_16x9:") or data.startswith("bili_dl_9x16:"):
        is_16x9 = data.startswith("bili_dl_16x9:")
        bvid = data.split(":", 1)[1].strip()
        aspect = "16:9" if is_16x9 else "9:16"
        res_label = "1920x1080 (16:9 Ngang)" if is_16x9 else "1080x1920 (9:16 Dọc Shorts)"

        dl_lock_key = f"bili_dl:{bvid}:{aspect}"
        if not _try_acquire_action(dl_lock_key, max_inflight_sec=120.0, debounce_sec=4.0):
            await safe_query_answer(query, f"⏳ Đang tải & cắt {bvid} ({aspect}), vui lòng đợi giây lát!", show_alert=True)
            return

        await safe_query_answer(query, f"📥 Đang tải & Safe-Crop {aspect} cho {bvid}...")
        wait_msg = await safe_reply(
            update,
            f"🅱️ **Đang tải luồng HD từ Bilibili & Cắt Sạch Watermark ({res_label})...**\n"
            f"🆔 Mã video: `{bvid}`\n"
            f"✂️ Chế độ: **Safe-Crop 14% viền góc + Lọc phụ đề cứng + Chuẩn hóa 30fps**"
        )
        try:
            from app.services.media.bilibili_fetcher import download_and_crop_bilibili_broll_clip
            out_dir = Path(settings.TEMP_DIR) / "bilibili_telegram_previews"
            out_dir.mkdir(parents=True, exist_ok=True)
            tag_ar = "16x9" if is_16x9 else "9x16"
            out_file = out_dir / f"bili_{bvid}_{tag_ar}_{ int(time.time()) }.mp4"

            clip_res = await asyncio.to_thread(
                download_and_crop_bilibili_broll_clip,
                bvid,
                str(out_file),
                20.0,
                28 * 1024 * 1024,
                aspect,
                False,
            )
            if not clip_res or not out_file.exists() or out_file.stat().st_size < 50_000:
                await safe_edit_text(
                    wait_msg,
                    f"⚠️ Không thể tải hoặc cắt video `{bvid}` ở chế độ `{aspect}` (luồng video có thể yêu cầu tài khoản Bilibili hoặc độ phân giải gốc không đủ chuẩn)."
                )
                return

            size_mb = out_file.stat().st_size / (1024 * 1024)
            title = clip_res.get("title") or bvid
            dur_s = float(clip_res.get("clipped_duration") or clip_res.get("duration") or 20.0)

            caption = (
                f"✅ **ĐÃ CẮT SẠCH WATERMARK BILIBILI ({res_label})**\n\n"
                f"📌 **Tiêu đề gốc:** {title[:100]}\n"
                f"🆔 **Mã Bilibili:** `{bvid}`\n"
                f"📦 **Dung lượng:** `{size_mb:.1f} MB` | ⏱️ **Đoạn trích mẫu:** `~{dur_s:.0f}s`\n"
                f"🔗 **Nguồn:** https://www.bilibili.com/video/{bvid}\n\n"
                f"👉 Bạn có thể bấm nút bên dưới để AI tự động lên kịch bản & dựng thành bộ phim hoàn chỉnh!"
            )
            followup_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎬 Dựng Phim Dài 16:9 (~10 Phút) Từ Video Này", callback_data=f"bili_reup_long:{bvid}")],
                [InlineKeyboardButton("⚡ Dựng Video Shorts 9:16 (~45s) Từ Video Này", callback_data=f"bili_reup_short:{bvid}")],
            ])
            chat_id = update.effective_chat.id
            with open(out_file, "rb") as vf:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=vf,
                    caption=caption,
                    parse_mode="Markdown",
                    supports_streaming=True,
                    reply_markup=followup_kb,
                )
            try:
                await wait_msg.delete()
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[BilibiliCallback] Lỗi tải/cắt {bvid} ({aspect}): {e}", exc_info=True)
            await safe_edit_text(wait_msg, f"❌ Lỗi khi tải & cắt video Bilibili `{bvid}`: `{e}`")
        finally:
            _release_action(dl_lock_key, post_cooldown_sec=3.0)
        return

    # 4. Dịch & Lên Kịch Bản Mới (16:9 Long-Form hoặc 9:16 Shorts) từ Video Bilibili / URL
    if data.startswith("bili_reup_long:") or data.startswith("bili_reup_short:"):
        is_long = data.startswith("bili_reup_long:")
        bvid = data.split(":", 1)[1].strip()
        await safe_query_answer(
            query,
            "🎬 Đang dịch & biên soạn kịch bản Phim Dài 16:9..." if is_long else "⚡ Đang dịch & biên soạn kịch bản Shorts 9:16...",
        )

        meta = {}
        if context.chat_data and "bili_meta_cache" in context.chat_data:
            meta = context.chat_data["bili_meta_cache"].get(bvid) or {}

        if not meta and not bvid.startswith("url_"):
            try:
                from app.services.media.bilibili_fetcher import get_bilibili_video_info_and_streams
                info = await asyncio.to_thread(get_bilibili_video_info_and_streams, bvid)
                if info:
                    meta = {
                        "bvid": bvid,
                        "title": info.get("title") or bvid,
                        "description": (info.get("description") or "")[:400],
                        "webpage_url": f"https://www.bilibili.com/video/{bvid}",
                    }
            except Exception:
                pass

        raw_title = meta.get("search_query") or meta.get("title") or bvid
        raw_desc = (meta.get("description") or "").strip()
        src_url = meta.get("webpage_url") or f"https://www.bilibili.com/video/{bvid}"

        notes = f"Lấy cảm hứng từ nguồn video ({src_url}). Tựa gốc: {meta.get('title', raw_title)}."
        if raw_desc:
            notes += f" Tóm tắt bối cảnh gốc: {raw_desc[:220]}"

        composed_arg = f"{raw_title} | {notes}"
        if is_long:
            composed_arg += " --long"

        context.args = composed_arg.split()
        await idea_command(update, context)
        return


# Bộ nhớ chống gửi trùng video vào nhóm Telegram: (job_id, int(chat_id)) -> timestamp
