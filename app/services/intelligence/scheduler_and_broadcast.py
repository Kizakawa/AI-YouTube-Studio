"""app.services.intelligence.scheduler_and_broadcast — Thông báo Telegram, tự động tạo video cứu kênh trễ hạn & Worker nền."""
import os
import re
import json
import time
import logging
import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Union

import requests as http_requests

from config.settings import settings
from app.database.models import get_db, VideoJob, User, ActivityLog, log_activity
from app.schemas.contracts import JobStatus, ScriptContract
from app.services.youtube_analytics import fetch_channel_live_stats
from app.services.niche_manager import get_niche, build_script_prompt_instructions
from app.services.smart_scheduler import get_next_available_golden_slot
from app.agents.content_agent import ContentAgent

logger = logging.getLogger(__name__)

# Giới hạn tối đa không có video mới trước khi kích hoạt Auto-Generation khẩn cấp (48 tiếng = 2 ngày)
KPI_CRITICAL_OVERDUE_HOURS = 48.0
# Ngưỡng cảnh báo màu vàng (24 tiếng = 1 ngày)
KPI_WARNING_HOURS = 24.0
# Thời gian chờ duyệt trước khi tự động đăng tạm (mặc định 4 tiếng)
DEFAULT_APPROVAL_GRACE_HOURS = 4.0

from app.services.intelligence.kpi_and_safety import (
    calculate_channel_kpi,
    check_pre_publish_safety,
    run_network_kpi_audit,
)
from app.services.intelligence.topic_suggester import suggest_channel_topics


def send_telegram_broadcast(
    text: str,
    inline_keyboard: Optional[List[List[Dict[str, str]]]] = None,
    target_chat_ids: Optional[List[Union[int, str]]] = None,
    prefer_groups: bool = True
):
    """
    Gửi tin nhắn thông báo/nhắc nhở/báo cáo đến Telegram.
    ƯU TIÊN TUYỆT ĐỐI gửi vào Nhóm Telegram (ALLOWED_TELEGRAM_GROUPS) để cả nhóm cùng theo dõi và phối hợp.
    Chỉ khi không có bất kỳ nhóm nào được cấu hình mới fallback gửi về tin nhắn riêng (DM) cho Admin/Creator.
    """
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.debug("[TelegramAlert] Không có TELEGRAM_BOT_TOKEN, bỏ qua gửi tin.")
        return

    # Lấy danh sách ID phòng chat nhận tin
    chat_ids = set()
    if target_chat_ids:
        for cid in target_chat_ids:
            chat_ids.add(cid)
    else:
        # 1. Ưu tiên gửi vào các nhóm chat Telegram được cấp phép
        allowed_groups = settings.get_allowed_group_ids()
        if prefer_groups and allowed_groups:
            for gid in allowed_groups:
                chat_ids.add(gid)
        else:
            # 2. Fallback nếu chưa có nhóm: gửi cho Admin/Creator
            for uid in settings.get_allowed_users().keys():
                chat_ids.add(uid)
            try:
                with get_db() as db:
                    db_users = db.query(User).filter(User.telegram_id != 0).all()
                    for u in db_users:
                        chat_ids.add(u.telegram_id)
            except Exception:
                pass

    if not chat_ids:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload_base: Dict[str, Any] = {
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    if inline_keyboard:
        payload_base["reply_markup"] = {"inline_keyboard": inline_keyboard}

    for cid in chat_ids:
        try:
            p = dict(payload_base)
            p["chat_id"] = cid
            res = http_requests.post(url, json=p, timeout=8)
            if res.status_code != 200:
                # Thử fallback plain text nếu lỗi markdown
                p["text"] = re.sub(r"[*_`\[\]()<>]", "", text)
                p.pop("parse_mode", None)
                http_requests.post(url, json=p, timeout=8)
        except Exception as ex:
            logger.debug(f"[TelegramAlert] Lỗi gửi tin cho {cid}: {ex}")


def send_telegram_video_broadcast(
    video_path: str,
    caption: str = "",
    inline_keyboard: Optional[List[List[Dict[str, str]]]] = None,
    target_chat_ids: Optional[List[Union[int, str]]] = None,
    prefer_groups: bool = True
) -> bool:
    """
    Gửi trực tiếp file video hoàn chỉnh (MP4) vào Telegram Group để người dùng xem thử.
    Nếu file > 49MB, tự động nén nhanh trong vài giây bằng FFmpeg CRF 28 để đảm bảo dưới hạn mức 50MB của Telegram!
    """
    token = settings.TELEGRAM_BOT_TOKEN
    if not token or not video_path or not Path(video_path).exists():
        logger.debug(f"[TelegramVideoAlert] Không có token hoặc không tìm thấy file video: {video_path}")
        return False

    chat_ids = set()
    if target_chat_ids:
        for cid in target_chat_ids:
            chat_ids.add(cid)
    else:
        allowed_groups = settings.get_allowed_group_ids()
        if prefer_groups and allowed_groups:
            for gid in allowed_groups:
                chat_ids.add(gid)
        else:
            for uid in settings.get_allowed_users().keys():
                chat_ids.add(uid)

    if not chat_ids:
        return False

    # Kiểm tra kích thước file; giữ nguyên bản gốc Full HD 1080p 30fps cho mọi file <= 45.0MB (Telegram giới hạn 50MB).
    # Nếu > 45.0MB (video dài 10 phút), tự động tính toán bitrate mục tiêu theo thời lượng thực tế để file nén luôn <= 44MB gửi mượt vào Telegram!
    upload_path = video_path
    temp_compressed = None
    try:
        size_mb = Path(video_path).stat().st_size / (1024 * 1024)
        if size_mb > 45.0:
            import subprocess
            from app.utils.ffmpeg_helper import get_duration_ms
            dur_s = max(30.0, get_duration_ms(video_path) / 1000.0)
            audio_kbps = 96 if dur_s > 300 else 128
            max_v_kbps = max(320, min(2200, int((28.0 * 8192) / dur_s) - audio_kbps))
            target_w, target_h = (1280, 720) if dur_s > 180 else (1920, 1080)
            crf_val = "26" if dur_s > 180 else "23"
            temp_dir = Path(settings.TEMP_DIR)
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_compressed = str(temp_dir / f"tg_preview_{Path(video_path).stem}.mp4")
            cmd = [
                "ffmpeg", "-y", "-i", video_path,
                "-vf", f"scale='if(gt(iw,ih),{target_w},{target_h})':'if(gt(iw,ih),{target_h},{target_w})':force_original_aspect_ratio=decrease,fps=30",
                "-c:v", "libx264", "-crf", crf_val, "-maxrate", f"{max_v_kbps}k", "-bufsize", f"{max_v_kbps * 2}k", "-preset", "veryfast",
                "-c:a", "aac", "-b:a", f"{audio_kbps}k", "-movflags", "+faststart",
                temp_compressed
            ]
            logger.info(
                f"[TelegramVideoAlert] File gốc {size_mb:.1f}MB ({dur_s:.0f}s > 45MB) -> đang nén thông minh ({target_w}x{target_h}, maxrate={max_v_kbps}k) gửi Telegram..."
            )
            res_comp = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=240)
            if res_comp.returncode == 0 and Path(temp_compressed).exists():
                upload_path = temp_compressed
                logger.info(f"[TelegramVideoAlert] Đã tạo bản nén gửi Telegram thành công: {Path(upload_path).stat().st_size / (1024*1024):.1f}MB")
    except Exception as ex_comp:
        logger.warning(f"[TelegramVideoAlert] Lỗi khi nén video: {ex_comp}")

    url = f"https://api.telegram.org/bot{token}/sendVideo"
    cached_file_id = None
    sent_any = False

    for cid in chat_ids:
        for attempt in range(3):
            try:
                data = {
                    "chat_id": cid,
                    "caption": caption[:1000] if caption else "",
                    "parse_mode": "HTML",
                    "supports_streaming": True
                }
                if inline_keyboard:
                    data["reply_markup"] = json.dumps({"inline_keyboard": inline_keyboard})

                if cached_file_id:
                    # Chat thứ 2 trở đi gửi qua file_id chỉ mất 0.2s!
                    data["video"] = cached_file_id
                    r = http_requests.post(url, data=data, timeout=20)
                    if r.status_code == 200:
                        sent_any = True
                        break
                else:
                    with open(upload_path, "rb") as vf:
                        r = http_requests.post(url, data=data, files={"video": vf}, timeout=240)
                        if r.status_code == 200:
                            sent_any = True
                            res_json = r.json()
                            v_obj = res_json.get("result", {}).get("video")
                            if v_obj and v_obj.get("file_id"):
                                cached_file_id = v_obj.get("file_id")
                            break
                        elif r.status_code != 200:
                            logger.warning(f"[TelegramVideoAlert] sendVideo HTML lỗi ({r.status_code}): {r.text[:200]} -> Thử lại plain text")
                            # Thử lại không parse_mode và lọc bỏ thẻ HTML bị cắt dở
                            data.pop("parse_mode", None)
                            data["caption"] = re.sub(r"<[^>]*>?", "", caption)[:950] if caption else ""
                            with open(upload_path, "rb") as vf2:
                                r2 = http_requests.post(url, data=data, files={"video": vf2}, timeout=120)
                                if r2.status_code == 200:
                                    sent_any = True
                                    res_json2 = r2.json()
                                    v_obj2 = res_json2.get("result", {}).get("video")
                                    if v_obj2 and v_obj2.get("file_id"):
                                        cached_file_id = v_obj2.get("file_id")
                                    break
                                else:
                                    logger.warning(f"[TelegramVideoAlert] sendVideo fallback lỗi ({r2.status_code}): {r2.text[:200]}")
                                    break
            except Exception as ex_send:
                logger.warning(f"[TelegramVideoAlert] Lỗi gửi video cho {cid} (lần {attempt + 1}/3): {ex_send}")
                if attempt < 2:
                    import time as _t
                    _t.sleep(2.0 * (attempt + 1))

    if temp_compressed and Path(temp_compressed).exists():
        try:
            os.remove(temp_compressed)
        except Exception:
            pass

    return sent_any


def trigger_auto_generation_for_overdue_channel(
    channel_id: str,
    grace_hours: float = DEFAULT_APPROVAL_GRACE_HOURS,
    force: bool = False
) -> Dict[str, Any]:
    """
    Kích hoạt tự động sản xuất video cứu tương tác khi một kênh bị trễ deadline >= 2 ngày (hoặc force=True):
    1. Kiểm tra xem có video nào đang xử lý dở không (tránh sản xuất thừa lãng phí tài nguyên).
    2. Chọn 1 ý tưởng độc lạ tốt nhất từ suggest_channel_topics (chống trùng lặp 100%).
    3. Tự động sinh kịch bản chi tiết qua ContentAgent.
    4. Tạo VideoJob trong SQLite và kích hoạt Render Pipeline trong background thread.
    5. Thiết lập thời hạn duyệt (approval_deadline = now + grace_hours).
    6. Gửi cảnh báo thúc giục người dùng kiểm tra trên Telegram và Web Dashboard!
    """
    kpi = calculate_channel_kpi(channel_id)
    if not force and not getattr(settings, "ENABLE_OVERDUE_AUTO_PRODUCTION", False):
        logger.info(f"[AutoGeneration] Kênh '{channel_id}' trễ hạn nhưng ENABLE_OVERDUE_AUTO_PRODUCTION đang tắt (False). Không tự ý sinh video.")
        return {
            "success": False,
            "message": f"Tính năng tự động sản xuất video quá hạn đang tắt (ENABLE_OVERDUE_AUTO_PRODUCTION=false). Hãy chủ động gõ lệnh tạo video.",
            "kpi": kpi
        }

    if not kpi["is_overdue_2_days"] and not force:
        return {
            "success": False,
            "message": f"Kênh '{kpi['channel_name']}' chưa trễ hạn 2 ngày (mới trễ {kpi['days_since_last_video']} ngày). Dùng force=True nếu muốn ép buộc tạo ngay.",
            "kpi": kpi
        }

    # Tránh spam: nếu đã có job chờ duyệt hoặc đang render, không tạo thêm
    if kpi.get("pending_job"):
        pj = kpi["pending_job"]
        return {
            "success": False,
            "message": f"Kênh đã có video đang trong hàng đợi xử lý (#{pj['job_id']} - {pj['status']}).",
            "job_id": pj["job_id"],
            "status": pj["status"]
        }

    logger.info(f"[AutoGeneration] 🚨 Kênh '{kpi['channel_name']}' ({channel_id}) trễ {kpi['days_since_last_video']} ngày! Khởi động AI Auto-Production...")

    # 1. Gợi ý ý tưởng không trùng lặp
    topics = suggest_channel_topics(channel_id, count=1)
    if not topics:
        return {"success": False, "error": "Không thể sinh ý tưởng mới cho kênh này."}

    best_topic = topics[0]
    topic_str = best_topic.get("topic", "High Retention Shorts")
    title_str = best_topic.get("title", f"Video_{datetime.utcnow().strftime('%Y%m%d_%H%M')}")
    niche_id = kpi["niche"]
    lang = kpi["language"]

    # 2. Sinh kịch bản phân cảnh chuẩn viral với Entity Cooldown Tracker
    from app.services.entity_tracker import get_channel_cooldown_entities, register_job_entities
    cooldown_ents = get_channel_cooldown_entities(channel_id, limit_jobs=15)

    agent = ContentAgent()
    try:
        script = agent.generate_script(
            topic=topic_str,
            channel_niche=niche_id,
            language=lang,
            director_notes="Kịch bản kích hoạt tự động cứu tương tác kênh. Chuẩn 5-Act 32-42s Golden Depth (88-118 từ, 8-9 cảnh trọn ý): mở đầu khẳng định sắc lẹm, có ví dụ câu nói thật + cơ chế não bộ + chiêu phản đòn cụ thể, kết thúc Seamless Loop nối về Cảnh 1 (tuyệt đối không đọc CTA Subscribe).",
            cooldown_entities=cooldown_ents
        )
    except Exception as e:
        logger.error(f"[AutoGeneration] Lỗi sinh kịch bản cho {channel_id}: {e}")
        return {"success": False, "error": f"Lỗi sinh kịch bản: {e}"}

    # 3. Tạo VideoJob trong Database
    now_utc = datetime.utcnow()
    deadline = now_utc + timedelta(hours=grace_hours)

    with get_db() as db:
        job = VideoJob(
            user_id=0,  # 0 = System Auto-Generator
            target_channel_id=channel_id,
            topic=topic_str,
            title=script.title or title_str,
            script=script.full_narration,
            description=script.description,
            tags=",".join(script.tags),
            scenes_json=script.to_json_str(),
            status="SCRIPT_READY",
            is_auto_generated=1,
            approval_deadline=deadline,
            progress_percent=0,
            progress_message="Hệ thống tự động lên lịch dựng video cứu tương tác..."
        )
        db.add(job)
        db.flush()
        job_id = job.id

        log_activity(
            db,
            telegram_id=0,
            username="AI_Guardian",
            action_type="AUTO_GENERATE_OVERDUE",
            details=f"Tự động sản xuất video cứu tương tác cho kênh '{kpi['channel_name']}' (Job #{job_id}): '{job.title}'",
            channel_id=channel_id
        )
        # Lưu ý: Thực thể, kịch bản và metadata hình ảnh chỉ được ghi vào bộ nhớ sau khi video đăng lên YouTube.

    # 4. Gửi thông báo Telegram để duyệt và render trực tiếp bằng Local Hardware Engine
    def _background_render_worker(jid: int, ch_name: str, ch_id: str, deadl: datetime):
        try:
            with get_db() as _db:
                _j = _db.query(VideoJob).filter(VideoJob.id == jid).first()
                video_title = _j.title if _j else "Video Shorts"

            grace_str = f"{grace_hours:.0f}"
            alert_text = (
                f"🚨 **CẢNH BÁO TỤT TƯƠNG TÁC KÊNH: `{ch_name}`**\n\n"
                f"⚠️ Kênh này đã trễ lịch đăng bài!\n"
                f"🤖 **AI Guardian đã tự động tạo kịch bản sẵn sàng:**\n"
                f"🎬 **Tiêu đề:** *{video_title}*\n"
                f"⏱️ **Hạn chót duyệt:** `{deadl.strftime('%H:%M %d/%m/2026')}` (còn ~{grace_str} tiếng)\n\n"
                f"👉 Bấm nút bên dưới để duyệt và dựng video ngay:"
            )
            inline_kb = [
                [
                    {"text": f"🎬 Duyệt & Dựng Ngay (#{jid})", "callback_data": f"render_video:{jid}"},
                    {"text": f"❌ Huỷ bỏ (#{jid})", "callback_data": f"approve_auto:{jid}:cancel"}
                ]
            ]
            send_telegram_broadcast(alert_text, inline_keyboard=inline_kb)
        except Exception as ex:
            logger.error(f"[AutoGeneration] Lỗi gửi thông báo #{jid}: {ex}", exc_info=True)

    render_thread = threading.Thread(
        target=_background_render_worker,
        args=(job_id, kpi["channel_name"], channel_id, deadline),
        daemon=True
    )
    render_thread.start()

    return {
        "success": True,
        "message": f"Đã tự động khởi tạo và đưa vào hàng đợi dựng video #{job_id} cho kênh '{kpi['channel_name']}'",
        "job_id": job_id,
        "topic": topic_str,
        "title": script.title,
        "approval_deadline": deadline.isoformat(),
        "grace_hours": grace_hours
    }


def process_fallback_publishing() -> List[Dict[str, Any]]:
    """
    Quét tất cả các video tự động sinh (is_auto_generated=1):
    - Đang ở trạng thái AWAITING_APPROVAL.
    - Đã vượt quá thời hạn chờ duyệt (approval_deadline <= now).
    - Người dùng bận không xác nhận.
    => Tự động lên lịch hoặc đăng tạm lên YouTube để duy trì nhịp tương tác thuật toán (nếu ENABLE_FALLBACK_AUTO_PUBLISH=true)!
    """
    if not getattr(settings, "ENABLE_FALLBACK_AUTO_PUBLISH", False):
        return []

    now_utc = datetime.utcnow()
    published_jobs = []

    with get_db() as db:
        expired_jobs = db.query(VideoJob).filter(
            VideoJob.is_auto_generated == 1,
            VideoJob.status == "AWAITING_APPROVAL",
            VideoJob.approval_deadline <= now_utc,
            VideoJob.video_path.isnot(None)
        ).all()

        for job in expired_jobs:
            # Kiểm tra file video có tồn tại
            if not Path(job.video_path).exists():
                logger.warning(f"[FallbackPublish] File video Job #{job.id} không tồn tại trên ổ đĩa!")
                continue

            target_ch = job.target_channel_id
            job_id = job.id
            job_title = job.title or job.topic

            logger.info(f"[FallbackPublish] ⏰ Video #{job_id} ('{job_title}') hết hạn duyệt ({job.approval_deadline}). Tiến hành tự động đăng tạm cho kênh '{target_ch}'...")

            # Tính toán khung giờ vàng gần nhất
            slot_dt, slot_iso, vn_label, us_label = get_next_available_golden_slot(target_ch)

            job.status = "SCHEDULED"
            job.scheduled_time = slot_dt
            job.progress_message = f"Đã tự động đăng tạm (Lên lịch công chiếu lúc {vn_label} để duy trì tương tác)"

            log_activity(
                db,
                telegram_id=0,
                username="AI_Guardian",
                action_type="FALLBACK_AUTO_PUBLISH",
                details=f"Tự động đăng tạm Video #{job_id} '{job_title}' lên lịch công chiếu lúc {vn_label} ({us_label}) do người dùng chưa duyệt sau thời hạn",
                channel_id=target_ch
            )

            published_jobs.append({
                "job_id": job_id,
                "title": job_title,
                "channel_id": target_ch,
                "scheduled_slot_vn": vn_label,
                "scheduled_slot_us": us_label
            })

            # Thông báo Telegram
            notify_text = (
                f"⏰ **TỰ ĐỘNG ĐĂNG TẠM THÀNH CÔNG (GIỮ NHỊP TƯƠNG TÁC)**\n\n"
                f"📺 **Kênh:** `{target_ch}`\n"
                f"🎬 **Video #{job_id}:** *{job_title}*\n"
                f"⌛ **Lý do:** Đã qua thời hạn duyệt nhưng bạn đang bận. Hệ thống đã kích hoạt cơ chế bảo vệ kênh.\n"
                f"📅 **Lịch công chiếu:** `{vn_label}` (Khung giờ vàng chuẩn quốc tế: `{us_label}`)\n\n"
                f"✅ Thuật toán phân phối Shorts Feed của kênh tiếp tục được duy trì ổn định!"
            )
            send_telegram_broadcast(notify_text)

    return published_jobs


class ChannelIntelligenceWorker:
    """Worker chạy ngầm định kỳ (mỗi 30 phút):
    - Đồng bộ dữ liệu thật (avg_view_percentage, stayed_to_watch_pct, swipe_away_pct) vào DB.
    - Tự động đăng các video đã dựng xong (`RENDERED`).
    - Báo Telegram ngay khi phát hiện kênh trống lịch (0 video) & bảo đảm mỗi kênh luôn có >= 3 video trong lịch đăng.
    - Kiểm tra fallback publish và KPI sức khỏe các kênh.
    """
    _instance = None
    _thread = None
    _running = False

    @classmethod
    def start(cls, check_interval_minutes: int = 30):
        if cls._running:
            return
        cls._running = True

        def _loop():
            logger.info(f"🚀 [ChannelIntelligence] Background Worker đã khởi động (chu kỳ kiểm tra: {check_interval_minutes} phút)!")
            while cls._running:
                try:
                    # 0. Đồng bộ số liệu retention thật (tỷ lệ xem hết, ở lại xem, vuốt bỏ) vào DB & nén kịch bản/metadata hình ảnh vào Mini-RAG
                    try:
                        from app.services.video_library import backfill_from_jobs
                        backfill_from_jobs()
                        from app.services.video_memory_rag import slim_storage_and_archive_to_rag
                        slim_storage_and_archive_to_rag(cache_max_age_hours=2.0)
                    except Exception as ex_bf:
                        logger.debug(f"[ChannelIntelligence] Đồng bộ retention metrics / Mini-RAG định kỳ warning: {ex_bf}")

                    # 1. Xử lý đăng tạm cho các video hết hạn duyệt (nếu người dùng bật)
                    if getattr(settings, "ENABLE_FALLBACK_AUTO_PUBLISH", False):
                        process_fallback_publishing()

                    # 2. Tự động đăng các video đã dựng xong (RENDERED), báo Telegram nếu kênh trống lịch,
                    # và duy trì tối thiểu 3 video trong lịch đăng cho mỗi kênh
                    try:
                        from app.services.autopilot_service import ensure_minimum_channel_queue
                        auto_prod_enabled = bool(getattr(settings, "ENABLE_OVERDUE_AUTO_PRODUCTION", True))
                        asyncio.run(
                            ensure_minimum_channel_queue(
                                min_queue=3,
                                notify_telegram=True,
                                auto_produce=auto_prod_enabled,
                            )
                        )
                    except Exception as ex_q:
                        logger.warning(f"[ChannelIntelligence] Lỗi kiểm tra hàng đợi 3 video/kênh: {ex_q}")

                    # 3. Quét KPI toàn mạng lưới kênh
                    audit = run_network_kpi_audit()
                    overdue_channels = [c for c in audit.get("channels", []) if c.get("kpi_status") == "OVERDUE_CRITICAL"]

                    if getattr(settings, "ENABLE_OVERDUE_AUTO_PRODUCTION", False):
                        for ch in overdue_channels:
                            cid = ch["channel_id"]
                            try:
                                if not ch.get("pending_job"):
                                    logger.info(f"[ChannelIntelligence] Phát hiện kênh '{cid}' trễ deadline 2 ngày! Tự động sản xuất video cứu nguy...")
                                    trigger_auto_generation_for_overdue_channel(cid)
                            except Exception as e_ch:
                                logger.error(f"[ChannelIntelligence] Lỗi tự động sản xuất cho kênh '{cid}': {e_ch}")
                    else:
                        if overdue_channels:
                            logger.info(f"[ChannelIntelligence] Có {len(overdue_channels)} kênh trễ hạn nhưng ENABLE_OVERDUE_AUTO_PRODUCTION đang tắt (False). Không tự sinh video.")

                except Exception as ex:
                    logger.error(f"[ChannelIntelligence] Lỗi chu kỳ worker: {ex}")

                import time
                for _ in range(check_interval_minutes * 6):
                    if not cls._running:
                        break
                    time.sleep(10)

        cls._thread = threading.Thread(target=_loop, daemon=True, name="ChannelIntelligenceWorker")
        cls._thread.start()

    @classmethod
    def stop(cls):
        cls._running = False
