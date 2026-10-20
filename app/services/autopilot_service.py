"""
AutoPilot Service — Động Cơ Tự Động Hóa Kênh Toàn Diện (A to Z) & Giám Sát Nhắc Nhở Thông Minh.

Cung cấp:
1. get_channel_today_summary(channel_id=None):
   - Thống kê tình trạng phát sóng trong ngày của từng kênh hoặc toàn bộ 5 kênh.
   - Phát hiện kênh nào đã có video (khung giờ nào), kênh nào CHƯA CÓ VIDEO NÀO (0 video).
   - Dự toán khe giờ vàng trống tiếp theo (20:00 VN hoặc 04:00 VN).

2. run_autopilot_pipeline_for_channel(channel_id, target_slot_iso=None, user_id=0, notify_telegram=True):
   - Kích hoạt quy trình sản xuất tự động khép kín từ đầu đến cuối:
     + Step 1: Phân tích kênh, sinh ý tưởng độc lạ không trùng lặp (chống trùng lặp 100%).
     + Step 2: Sinh kịch bản chuẩn viral có 3s Subscriber Hook, Visual CTA Badge, Pinned Comment Hook.
     + Step 3: Tính toán và cố định khe giờ vàng tối ưu tiếp theo (strictly 20:00 VN hoặc 04:00 VN).
     + Step 4: Tạo VideoJob trong SQLite và kích hoạt VideoPipeline/LangGraph để render tự động.
     + Step 5: Tự động upload & đặt lịch phát sóng lên YouTube Shorts với pinned comment mồi tương tác.
     + Step 6: Tải bản sao lưu trữ lên Google Drive.
     + Step 7: Báo cáo kết quả chi tiết về Telegram và Web Dashboard.

3. run_autopilot_for_all_empty(user_id=0, notify_telegram=True):
   - Quét và tự động chạy Auto-Pilot tuần tự cho TẤT CẢ các kênh chưa có lịch đăng hôm nay.

4. check_and_send_daily_reminders(bot=None, force=False):
   - Watchdog chủ động: quét định kỳ và gửi báo cáo + câu hỏi nhắc nhở người dùng vào những ngày bận rộn.
   - Đính kèm nút bấm 1 chạm để người dùng bấm là bot tự động làm từ A-Z.
"""

import asyncio
import html
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from config.settings import settings
from app.database.models import get_db, VideoJob, User, log_activity
from app.services.smart_scheduler import (
    VN_TZ,
    US_EASTERN,
    get_next_available_golden_slot,
    resolve_channel_family,
    sync_scheduled_jobs_status,
)

logger = logging.getLogger(__name__)

# Thread-safe cross-event-loop lock cho từng kênh và cho tác vụ chạy toàn bộ kênh trống
_ACTIVE_AUTOPILOT_CHANNELS: set[str] = set()
_LOCKS_MUTEX = threading.Lock()

# Tệp lưu trữ trạng thái nhắc nhở hàng ngày (bảo đảm không bị mất khi khởi động lại bot)
_WATCHDOG_STATE_FILE = Path(settings.BASE_DIR) / "storage" / "autopilot_watchdog_state.json"


def _load_watchdog_state() -> Dict[str, Any]:
    try:
        if _WATCHDOG_STATE_FILE.exists():
            return json.loads(_WATCHDOG_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_watchdog_state(state: Dict[str, Any]):
    try:
        _WATCHDOG_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _WATCHDOG_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Lỗi lưu watchdog state: {e}")


def _try_acquire_channel_autopilot(channel_id: str) -> bool:
    """Đánh dấu kênh đang chạy Auto-Pilot (an toàn giữa nhiều thread/event loop)."""
    with _LOCKS_MUTEX:
        if channel_id in _ACTIVE_AUTOPILOT_CHANNELS:
            return False
        _ACTIVE_AUTOPILOT_CHANNELS.add(channel_id)
        return True


def _release_channel_autopilot(channel_id: str) -> None:
    """Giải phóng khóa Auto-Pilot của kênh."""
    with _LOCKS_MUTEX:
        _ACTIVE_AUTOPILOT_CHANNELS.discard(channel_id)


# ──────────────────────────────────────────────────────────────────────────────
# 1. BÁO CÁO & THỐNG KÊ TRẠNG THÁI CÁC KÊNH TRONG NGÀY
# ──────────────────────────────────────────────────────────────────────────────

def get_channel_today_summary(channel_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Rà soát tình trạng lên lịch và phát sóng hôm nay của 1 kênh hoặc toàn bộ các kênh đang quản lý.
    Sử dụng múi giờ Việt Nam (VN_TZ) để xác định ngày hiện tại.
    """
    # 0. Đồng bộ các video đã qua giờ hẹn sang PUBLISHED
    try:
        sync_scheduled_jobs_status()
    except Exception as e_sync:
        logger.debug(f"[AutoPilot] Đồng bộ schedule trước khi quét gặp lỗi nhẹ: {e_sync}")

    now_vn = datetime.now(VN_TZ)
    today_vn_date = now_vn.date()
    today_vn_str = now_vn.strftime("%d/%m/%Y")

    all_channels = settings.get_channels_config()
    target_channels = [c for c in all_channels if c.get("id") == channel_id] if channel_id else all_channels

    channels_report = []
    empty_channels = []
    scheduled_channels = []

    with get_db() as db:
        for ch in target_channels:
            cid = ch.get("id", "")
            cname = ch.get("name", cid)
            handle = ch.get("handle", "")
            niche = ch.get("niche", "general")
            family = resolve_channel_family(cid)

            # 1. Tìm các video đã SCHEDULED hoặc PUBLISHED trong ngày hôm nay (tính theo giờ VN)
            jobs = db.query(VideoJob).filter(
                VideoJob.target_channel_id.in_(family),
                VideoJob.status.in_(["SCHEDULED", "PUBLISHED"])
            ).order_by(VideoJob.scheduled_time.asc()).all()

            today_videos = []
            upcoming_scheduled_videos = []
            now_utc = datetime.now(timezone.utc)
            for j in jobs:
                t = j.scheduled_time or j.created_at
                if t:
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=timezone.utc)
                    t_vn = t.astimezone(VN_TZ)
                    v_item = {
                        "job_id": j.id,
                        "title": j.title or j.topic or f"Video #{j.id}",
                        "status": j.status,
                        "time_vn": t_vn.strftime("%H:%M"),
                        "time_full_vn": t_vn.strftime("%H:%M %d/%m/%Y"),
                        "youtube_video_id": j.youtube_video_id,
                        "shorts_url": f"https://www.youtube.com/shorts/{j.youtube_video_id}" if j.youtube_video_id else None,
                    }
                    if t_vn.date() == today_vn_date:
                        today_videos.append(v_item)
                    if j.status == "SCHEDULED" and t >= now_utc - timedelta(minutes=15):
                        upcoming_scheduled_videos.append(v_item)

            # Đếm số video đã dựng xong (RENDERED) đang chờ tự động đăng
            rendered_ready_count = db.query(VideoJob).filter(
                VideoJob.target_channel_id.in_(family),
                VideoJob.status.in_(["RENDERED", "WAITING_QUOTA_RESET"]),
                VideoJob.video_path.isnot(None),
            ).count()

            # 2. Tìm job đang trong tiến trình xử lý dở (chỉ tính các job mới trong vòng 3 giờ qua để tránh job cũ làm kẹt Auto-Pilot)
            recent_pending_cutoff = datetime.utcnow() - timedelta(hours=3)
            pending_job_rec = db.query(VideoJob).filter(
                VideoJob.target_channel_id.in_(family),
                VideoJob.status.in_(["SCRIPT_READY", "AUDIO_READY", "RENDERING", "AWAITING_APPROVAL"]),
                VideoJob.created_at >= recent_pending_cutoff,
            ).order_by(VideoJob.id.desc()).first()

            pending_job = None
            if pending_job_rec:
                pending_job = {
                    "job_id": pending_job_rec.id,
                    "title": pending_job_rec.title or pending_job_rec.topic,
                    "status": pending_job_rec.status,
                    "progress_percent": pending_job_rec.progress_percent or 0,
                    "progress_message": pending_job_rec.progress_message or ""
                }

            # 3. Lấy khe giờ vàng tiếp theo khả dụng cho kênh
            try:
                next_slot_dt, next_slot_iso, next_display_vn, next_display_us = get_next_available_golden_slot(cid)
            except Exception as e_slot:
                logger.warning(f"[AutoPilot] Không thể tính khe giờ vàng cho {cid}: {e_slot}")
                next_slot_iso = ""
                next_display_vn = "20:00 Tối Nay (Dự kiến)"
                next_display_us = ""

            count = len(today_videos)
            scheduled_queue_count = len(upcoming_scheduled_videos)
            queue_deficit = max(0, 3 - scheduled_queue_count)
            is_schedule_empty = (scheduled_queue_count == 0)
            is_empty = ((count == 0 or scheduled_queue_count == 0) and pending_job is None)

            ch_data = {
                "channel_id": cid,
                "channel_name": cname,
                "handle": handle,
                "niche": niche,
                "today_video_count": count,
                "today_videos": today_videos,
                "scheduled_queue_count": scheduled_queue_count,
                "upcoming_scheduled_videos": upcoming_scheduled_videos,
                "queue_deficit": queue_deficit,
                "rendered_ready_count": rendered_ready_count,
                "is_schedule_empty": is_schedule_empty,
                "pending_job": pending_job,
                "is_empty": is_empty,
                "has_video_today": count > 0,
                "next_golden_slot": {
                    "iso": next_slot_iso,
                    "display_vn": next_display_vn,
                    "display_us": next_display_us
                }
            }

            channels_report.append(ch_data)
            if is_empty:
                empty_channels.append(ch_data)
            else:
                scheduled_channels.append(ch_data)

    return {
        "success": True,
        "date_vn": today_vn_str,
        "total_channels": len(channels_report),
        "empty_count": len(empty_channels),
        "scheduled_count": len(scheduled_channels),
        "empty_channels": empty_channels,
        "scheduled_channels": scheduled_channels,
        "channels": channels_report
    }


# ──────────────────────────────────────────────────────────────────────────────
# 2. TIẾN TRÌNH SẢN XUẤT & LÊN LỊCH TỰ ĐỘNG CHO 1 KÊNH (AUTO-PILOT A-Z)
# ──────────────────────────────────────────────────────────────────────────────

async def run_autopilot_pipeline_for_channel(
    channel_id: str,
    target_slot_iso: Optional[str] = None,
    user_id: int = 0,
    notify_telegram: bool = True
) -> Dict[str, Any]:
    """
    Quy trình sản xuất A-Z tự động hoàn toàn cho 1 kênh:
    1. Ý tưởng viral độc quyền chống trùng lặp.
    2. Kịch bản 3s CTA hook + visual badge + pinned comment hook.
    3. Phân bổ khe giờ vàng (20:00 VN hoặc 04:00 VN).
    4. Render video MP4 1080x1920 (Voice Edge-TTS, Subtitles Karaoke CTA, Background Music, Thumbnails).
    5. Đặt lịch YouTube Shorts (status.publishAt) & post pinned comment.
    6. Backup Google Drive & gửi báo cáo hoàn tất qua Telegram.
    """
    if not _try_acquire_channel_autopilot(channel_id):
        return {
            "success": False,
            "error": f"Kênh '{channel_id}' đang trong tiến trình Auto-Pilot khác. Vui lòng chờ hoàn tất!"
        }

    if True:
     try:
        from app.services.channel_manager import get_channel
        ch_cfg = get_channel(channel_id) or {}
        if not ch_cfg:
            channels = settings.get_channels_config()
            ch_cfg = next((c for c in channels if c.get("id") == channel_id), {})

        ch_name = ch_cfg.get("name", channel_id)
        ch_niche = ch_cfg.get("niche", "general")
        ch_lang = ch_cfg.get("language", "en")
        ch_handle = ch_cfg.get("handle", "")

        logger.info(f"🚀 [AutoPilot A-Z] Khởi động cho kênh '{ch_name}' ({channel_id}) [Niche: {ch_niche}]...")

        # ── Step 1: Chọn khe giờ vàng phát sóng trước ──
        if target_slot_iso:
            slot_iso = target_slot_iso
            try:
                slot_dt = datetime.fromisoformat(slot_iso.replace("Z", "+00:00"))
                dt_vn = slot_dt.astimezone(VN_TZ)
                display_vn = f"{dt_vn.strftime('%H:%M %d/%m/%Y')} (Giờ VN)"
                display_us = ""
            except Exception:
                slot_dt = datetime.now(timezone.utc) + timedelta(hours=4)
                display_vn = "Giờ hẹn tự động"
                display_us = ""
        else:
            slot_dt, slot_iso, display_vn, display_us = get_next_available_golden_slot(channel_id)

        # ── Step 2: Chọn ý tưởng viral mới nhất chống trùng lặp ──
        from app.services.channel_intelligence import suggest_channel_topics, ensure_unique_title
        topics = await asyncio.to_thread(suggest_channel_topics, channel_id, count=3)
        if not topics:
            return {"success": False, "error": f"Không thể tạo ý tưởng viral mới cho kênh {ch_name}."}

        best_topic = topics[0]
        topic_str = best_topic.get("topic", "High Retention Shorts")
        title_str = best_topic.get("title", f"Video_{datetime.utcnow().strftime('%Y%m%d_%H%M')}")
        topic_angle = best_topic.get("angle", "")
        topic_hook = best_topic.get("hook", "")
        topic_pinned = best_topic.get("pinned_comment", "")
        topic_fixes = best_topic.get("fixes_flaw", "")

        # ── Step 3: Tạo kịch bản chuẩn viral với Entity Cooldown, ScriptCritic & 5-Act Depth ──
        from app.services.entity_tracker import get_channel_cooldown_entities, register_job_entities
        from app.agents.content_agent import ContentAgent

        cooldown_ents = get_channel_cooldown_entities(channel_id, limit_jobs=15)
        agent = ContentAgent()

        director_notes = (
            f"Kịch bản Auto-Pilot A-Z phát hành vào khung giờ vàng {display_vn}. "
            f"Chuẩn 5-Act 32-42s Golden Depth (88-118 từ, 8-9 cảnh trọn ý). "
            f"Góc khai thác triệu view: {topic_angle}. "
            f"Gợi ý Hook mở đầu: '{topic_hook}'. "
            f"Khắc phục nhược điểm cũ: {topic_fixes}. "
            f"Bắt buộc: (1) Cảnh 1 mở đầu bằng cấu trúc đối đầu 'A vs B' hoặc câu khẳng định gây sốc 5-8 từ có số liệu/thông số vật lý cụ thể (công thức pt-BR & Wild Odyssey), CẤM mở đầu bằng câu hỏi Yes/No thụ động; "
            f"(2) Có câu nói trích dẫn đời thực (Real-life verbal quote) hoặc chi tiết định lượng cụ thể, giải mã cơ chế ngầm (Mechanism) và chiêu phản đòn/cao trào cụ thể (Surgical counter-move / Climax); "
            f"(3) Cảnh cuối dùng Seamless Infinite Loop nối mượt về Cảnh 1, TUYỆT ĐỐI KHÔNG đọc lời kêu gọi Subscribe/Like bằng giọng đọc (chỉ đặt câu hỏi tranh luận ở trường pinned_comment)."
        )

        try:
            script_res = await asyncio.to_thread(
                agent.generate_best_script,
                topic=topic_str,
                channel_niche=ch_niche,
                language=ch_lang,
                director_notes=director_notes,
                min_acceptable_score=8.2,
                max_attempts=3,
                cooldown_entities=cooldown_ents,
                channel_id=channel_id,
                strict_block=True,
            )
            script = script_res[0] if isinstance(script_res, tuple) else script_res
        except Exception as e_script:
            logger.error(f"[AutoPilot] Lỗi/Chặn QA khi sinh kịch bản cho {channel_id}: {e_script}", exc_info=True)
            return {"success": False, "error": f"Kịch bản bị chặn hoặc lỗi: {e_script}"}

        # ── Step 3.5: Quality Gate — Hiệu chỉnh theo tỷ lệ vuốt bỏ thật & CHẶN CỨNG nếu không đạt ──
        from app.services.quality_evaluator import QualityEvaluator
        _qa = QualityEvaluator()
        _max_rewrites = 2
        _qa_passed = False
        _qa_score = 0
        _qa_result: Dict[str, Any] = {}
        for _rewrite_attempt in range(_max_rewrites + 1):
            _script_dict = script.to_dict() if hasattr(script, "to_dict") else {"title": script.title, "scenes": [{"scene_id": i+1, "narration": s.narration} for i, s in enumerate(getattr(script, "scenes", []))]}
            _qa_result = await asyncio.to_thread(
                _qa.evaluate_script, _script_dict, topic_str, ch_niche, ch_lang, channel_id
            )
            _qa_score = _qa_result.get("score", 0)
            _qa_passed = bool(_qa_result.get("passed", False))
            logger.info(
                f"[AutoPilot #{channel_id}] 📊 Script QA Lần {_rewrite_attempt + 1}: "
                f"Score={_qa_score}/100 (Hook={_qa_result.get('hook_score')}/{_qa_result.get('min_hook_required')}) | "
                f"{'✅ ĐẠT' if _qa_passed else '❌ CHƯA ĐẠT'}"
            )
            if _qa_passed:
                break
            if _rewrite_attempt >= _max_rewrites:
                raise RuntimeError(
                    f"🚫 [HardBlock AutoPilot ScriptQA] Kịch bản kênh '{ch_name}' ({channel_id}) không đạt ngưỡng giữ chân "
                    f"sau {_max_rewrites} lần viết lại (Score={_qa_score}/{_qa_result.get('min_score_required')}, "
                    f"Hook={_qa_result.get('hook_score')}/{_qa_result.get('min_hook_required')}). "
                    f"Đã chặn sản xuất để bảo vệ tỷ lệ ở lại xem của kênh!"
                )
            _weaknesses = "; ".join(_qa_result.get("weaknesses", []))
            _rewrite_notes = (
                f"{director_notes} "
                f"[Rewrite #{_rewrite_attempt + 1}] Kịch bản trước đạt {_qa_score}/100 (Hook={_qa_result.get('hook_score')}). "
                f"BẮT BUỘC KHẮC PHỤC: {_weaknesses}. "
                f"Áp dụng công thức pt-BR / Wild Odyssey: đối đầu 'A vs B' + câu khẳng định gây sốc 5-8 từ có số liệu vật lý ngay Cảnh 1."
            )
            logger.info(f"[AutoPilot] 🔄 Tự động rewrite kịch bản lần {_rewrite_attempt + 1}...")
            script = await asyncio.to_thread(
                agent.generate_script,
                topic=topic_str,
                channel_niche=ch_niche,
                language=ch_lang,
                director_notes=_rewrite_notes,
                cooldown_entities=cooldown_ents,
                channel_id=channel_id,
            )

        # Đảm bảo tiêu đề độc bản 100% và có bình luận mồi chuẩn
        try:
            unique_title, _, _ = await asyncio.to_thread(
                ensure_unique_title,
                script.title or title_str,
                channel_id,
                topic_str,
                ch_lang,
            )
            if unique_title:
                script.title = unique_title
        except Exception as _ut_err:
            logger.debug(f"[AutoPilot] ensure_unique_title warning: {_ut_err}")

        if not getattr(script, "pinned_comment", None) and topic_pinned:
            try:
                script.pinned_comment = topic_pinned
            except Exception:
                pass

        # Trích xuất hook_text từ Cảnh 1 để lưu vào DB phục vụ đối chiếu Winner/Loser hàng tuần
        scene_1_hook_text = ""
        try:
            if getattr(script, "scenes", None) and len(script.scenes) > 0:
                scene_1_hook_text = getattr(script.scenes[0], "narration", "") or ""
            if not scene_1_hook_text:
                scene_1_hook_text = getattr(script, "hook", "") or ""
        except Exception:
            pass

        # ── Step 4: Tạo VideoJob trong Database ──
        with get_db() as db:
            job = VideoJob(
                user_id=user_id,
                target_channel_id=channel_id,
                topic=topic_str,
                title=script.title or title_str,
                script=script.full_narration,
                hook_text=scene_1_hook_text[:500] if scene_1_hook_text else None,
                description=script.description,
                tags=",".join(script.tags),
                scenes_json=script.to_json_str(),
                status="RENDERING",
                scheduled_time=slot_dt,
                is_auto_generated=1,
                progress_percent=10,
                progress_message=f"Auto-Pilot A-Z: Đang bắt đầu dựng video cho khe giờ {display_vn}..."
            )
            db.add(job)
            db.flush()
            job_id = job.id
            initial_job_title = job.title

            log_activity(
                db,
                telegram_id=user_id,
                username="AutoPilot_Engine",
                action_type="AUTOPILOT_START",
                details=f"Tự động sản xuất A-Z cho kênh '{ch_name}' (Job #{job_id}): '{job.title}'",
                channel_id=channel_id
            )
            # Lưu ý: Kịch bản, chủ đề (UsedTopic) và metadata hình ảnh chỉ được ghi vào bộ nhớ (Mini-RAG)
            # ở Step 6 SAU KHI video đã đăng lên YouTube thành công (có youtube_video_id).

        # ── Step 5: Render Video Pipeline (LangGraph / Local Render Engine) ──
        logger.info(f"[AutoPilot #{job_id}] 🎬 Bắt đầu render video cho kênh '{ch_name}'...")
        from app.services.pipeline import VideoPipeline

        async def _progress_cb(stage: str, msg: str, percent: int = 0):
            try:
                with get_db() as db_p:
                    j_p = db_p.query(VideoJob).filter(VideoJob.id == job_id).first()
                    if j_p:
                        j_p.progress_percent = percent
                        j_p.progress_message = f"AutoPilot [{stage}]: {msg}"
            except Exception:
                pass

        pipeline = VideoPipeline(job_id=job_id, progress_callback=_progress_cb)
        pipeline.niche = ch_niche
        render_job = await pipeline.execute()

        video_path = render_job.output_path
        if not video_path or not Path(video_path).exists():
            raise RuntimeError(f"Render pipeline kết thúc nhưng không tìm thấy file video tại: {video_path}")

        # ── Step 5.5: Kiểm định cứng VideoQA trước khi đăng lên YouTube (Hard-Block nếu không đạt) ──
        vid_eval = await asyncio.to_thread(
            _qa.evaluate_video,
            video_path,
            getattr(render_job, "total_audio_duration_ms", 0) or 0,
        )
        if not vid_eval.get("passed", False):
            with get_db() as db_fail:
                j_fail = db_fail.query(VideoJob).filter(VideoJob.id == job_id).first()
                if j_fail:
                    j_fail.status = "FAILED"
                    j_fail.error_message = f"HardBlock VideoQA ({vid_eval.get('score')}/100): {'; '.join(vid_eval.get('issues', []))}"
            raise RuntimeError(
                f"🚫 [HardBlock VideoQA] Video #{job_id} không đạt chuẩn kỹ thuật/độ nét "
                f"(Score={vid_eval.get('score')}/100): {'; '.join(vid_eval.get('issues', []))}. Đã chặn đăng tải!"
            )

        # Đọc lại dữ liệu mới nhất của Job từ DB (sau khi LangGraph có thể đã tinh chỉnh kịch bản/tiêu đề)
        latest_title = script.title or initial_job_title
        latest_desc = script.description or ""
        latest_tags = list(script.tags) if script.tags else []
        latest_topic = topic_str
        latest_scenes_json = script.to_json_str()
        latest_hook_text = scene_1_hook_text
        latest_voice_engine = None
        with get_db() as db_read:
            j_latest = db_read.query(VideoJob).filter(VideoJob.id == job_id).first()
            if j_latest:
                latest_title = j_latest.title or latest_title
                latest_desc = j_latest.description or latest_desc
                if j_latest.tags:
                    latest_tags = [t.strip() for t in j_latest.tags.split(",") if t.strip()]
                latest_topic = j_latest.topic or latest_topic
                latest_scenes_json = j_latest.scenes_json or latest_scenes_json
                latest_hook_text = j_latest.hook_text or latest_hook_text
                latest_voice_engine = j_latest.voice_engine

        # ── Step 6: Lên lịch phát sóng tự động lên YouTube Shorts (status.publishAt) ──
        logger.info(f"[AutoPilot #{job_id}] 📤 Đang lên lịch công chiếu lên YouTube Shorts vào {display_vn}...")
        from app.agents.publisher_agent import PublisherAgent

        pub_agent = PublisherAgent(channel_config=ch_cfg)

        # Trích xuất bình luận mồi (pinned_comment) từ kịch bản
        pinned_comment = getattr(script, "pinned_comment", None) or topic_pinned
        try:
            s_dict = json.loads(latest_scenes_json) if isinstance(latest_scenes_json, str) else latest_scenes_json
            if isinstance(s_dict, dict) and s_dict.get("pinned_comment"):
                pinned_comment = s_dict.get("pinned_comment")
        except Exception:
            pass

        pub_res = await asyncio.to_thread(
            pub_agent.upload_video,
            video_path=video_path,
            title=latest_title,
            description=latest_desc,
            tags=latest_tags,
            privacy_status="private",
            schedule_time_iso=slot_iso,
            default_language=ch_lang,
            default_audio_language=ch_lang,
            pinned_comment=pinned_comment,
            topic=latest_topic,
            script=latest_scenes_json or "",
        )

        drive_link = None
        # Mặc định KHÔNG lưu trữ file video .mp4 nặng lên Drive (chỉ lưu Kịch bản + Metadata Hình ảnh vào Mini-RAG)
        if os.getenv("ENABLE_DRIVE_VIDEO_BACKUP", "false").strip().lower() in ("1", "true", "yes"):
            try:
                from app.agents.drive_agent import DriveAgent
                drive_agent = DriveAgent()
                drive_res = await asyncio.to_thread(
                    drive_agent.upload_video,
                    video_path,
                    latest_title,
                    channel_name=ch_name
                )
                if isinstance(drive_res, dict):
                    drive_link = drive_res.get("link") or drive_res.get("drive_link")
            except Exception as e_drive:
                logger.debug(f"[AutoPilot #{job_id}] Sao lưu Drive gặp lỗi nhẹ: {e_drive}")

        # ── Step 7: Cập nhật kết quả vào Database ──
        yt_id = pub_res.get("video_id")
        yt_shorts_url = pub_res.get("shorts_url") or (f"https://www.youtube.com/shorts/{yt_id}" if yt_id else None)
        yt_url = pub_res.get("youtube_url") or yt_shorts_url

        with get_db() as db:
            final_job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if final_job:
                if pub_res.get("status") == "success":
                    final_job.status = "SCHEDULED"
                    final_job.scheduled_time = slot_dt
                    final_job.youtube_video_id = yt_id
                    if drive_link:
                        final_job.drive_link = drive_link
                    if pub_res.get("enriched_description"):
                        final_job.description = pub_res.get("enriched_description")
                    final_job.progress_percent = 100
                    final_job.progress_message = f"Đã lên lịch phát sóng thành công vào {display_vn}!"
                elif pub_res.get("status") == "waiting_quota_reset":
                    final_job.status = "WAITING_QUOTA_RESET"
                    final_job.progress_message = "Đã dựng xong video, đang chờ Google cấp lại hạn mức Quota để tự động upload."
                else:
                    final_job.status = "RENDERED"
                    final_job.error_message = pub_res.get("error", "Lỗi upload lên YouTube")

                log_activity(
                    db,
                    telegram_id=user_id,
                    username="AutoPilot_Engine",
                    action_type="AUTOPILOT_COMPLETE",
                    details=f"Hoàn tất Auto-Pilot cho kênh '{ch_name}' (Job #{job_id}): {yt_shorts_url or 'Rendered'}",
                    channel_id=channel_id
                )

        # ── Step 8: Gửi thông báo & UPLOAD VIDEO TRỰC TIẾP VÀO NHÓM TELEGRAM ──
        if notify_telegram:
            from app.services.channel_intelligence import send_telegram_broadcast, send_telegram_video_broadcast
            
            status_title = "🎉 <b>AUTOPILOT ĐÃ HOÀN TẤT A-Z & LÊN LỊCH THÀNH CÔNG!</b>"
            status_title_md = "🎉 **AUTOPILOT ĐÃ HOÀN TẤT A-Z & LÊN LỊCH THÀNH CÔNG!**"
            if pub_res.get("status") == "waiting_quota_reset":
                status_title = "⏳ <b>AUTOPILOT ĐÃ DỰNG XONG (CHỜ RESET QUOTA UPLOAD)</b>"
                status_title_md = "⏳ **AUTOPILOT ĐÃ DỰNG XONG (CHỜ RESET QUOTA UPLOAD)**"

            caption_html = (
                f"{status_title}\n\n"
                f"📺 <b>Kênh:</b> {html.escape(ch_name)} (<code>{ch_handle or channel_id}</code>)\n"
                f"🎬 <b>Tiêu đề:</b> <i>{html.escape(latest_title)}</i>\n"
                f"⏰ <b>Công chiếu:</b> 👉 <b>{display_vn}</b>\n"
                f"🇺🇸 <i>({display_us})</i>\n"
            )
            if yt_shorts_url:
                caption_html += f"\n⚡ <b>Link YouTube Shorts:</b>\n👉 {yt_shorts_url}\n"
            if pinned_comment:
                caption_html += f"\n💬 <b>Bình luận mồi:</b> <i>{html.escape(pinned_comment[:150])}</i>\n"
            if drive_link:
                caption_html += f"\n📂 <a href=\"{drive_link}\">Mở Google Drive</a>\n"

            alert_text = (
                f"{status_title_md}\n\n"
                f"📺 **Kênh:** `{ch_name}` (`{ch_handle or channel_id}`)\n"
                f"🎬 **Tiêu đề:** *{html.escape(latest_title)}*\n"
                f"⏰ **Giờ công chiếu:** 👉 **{display_vn}**\n"
                f"🇺🇸 *({display_us})*\n\n"
                f"⚡ **Link YouTube Shorts (Đã khóa hẹn giờ):**\n"
                f"👉 {yt_shorts_url or 'Đang lưu nội bộ'}\n\n"
            )

            if pinned_comment:
                alert_text += f"💬 **Bình luận mồi tự động (Pinned Bait):**\n> _{pinned_comment}_\n\n"

            if drive_link:
                alert_text += f"📂 **Bản gốc Google Drive:** [Mở thư mục Drive]({drive_link})\n\n"

            alert_text += "💡 *Hệ thống hoàn toàn tự động. Máy chủ YouTube sẽ tự động công chiếu đúng giờ mà không cần mở máy!*"

            inline_kb = []
            if yt_shorts_url:
                inline_kb.append([{"text": "⚡ Mở YouTube Shorts", "url": yt_shorts_url}])
            else:
                inline_kb.append([
                    {"text": "🚀 Đăng Ngay", "callback_data": f"publish_youtube:{job_id}"},
                    {"text": "⏰ Đăng Theo Lịch", "callback_data": f"schedule_youtube:{job_id}"}
                ])
            inline_kb.append([
                {"text": "✏️ Sửa Video", "callback_data": f"feedback_video:{job_id}"},
                {"text": "🗑️ Xóa Video", "callback_data": f"delete_video:{job_id}"}
            ])
            inline_kb.append([
                {"text": "📅 Xem Toàn Bộ Lịch Đăng", "callback_data": f"schedule:{channel_id}"},
                {"text": "🔄 Trạng Thái Auto-Pilot", "callback_data": "autopilot_status"}
            ])

            # Ưu tiên upload trực tiếp file video vào nhóm để người dùng xem thử
            sent_video = False
            if video_path and Path(video_path).exists():
                try:
                    sent_video = send_telegram_video_broadcast(
                        video_path=video_path,
                        caption=caption_html,
                        inline_keyboard=inline_kb
                    )
                except Exception as ex_vid:
                    logger.warning(f"[AutoPilot #{job_id}] Gửi video broadcast thất bại: {ex_vid}")

            # Nếu không gửi được video (hoặc video_path không tồn tại), fallback gửi tin nhắn văn bản
            if not sent_video:
                send_telegram_broadcast(alert_text, inline_keyboard=inline_kb)

        # ── Step 9: Lưu Kịch bản + Metadata Hình ảnh vào Mini-RAG & Xóa ngay file .mp4 nặng (nếu đã đẩy lên YouTube) ──
        if pub_res.get("status") == "success":
            try:
                from app.services.video_memory_rag import archive_job_to_rag_and_cleanup_files
                rag_clean = await asyncio.to_thread(archive_job_to_rag_and_cleanup_files, job_id)
                logger.info(
                    f"[MiniRAG #{job_id}] ✅ Đã lưu kịch bản + metadata ảnh vào Mini-RAG & giải phóng "
                    f"{rag_clean.get('freed_mb', 0)} MB file video/temp!"
                )
            except Exception as e_rag:
                logger.debug(f"[MiniRAG #{job_id}] Lỗi lưu Mini-RAG/cleanup: {e_rag}")

        return {
            "success": True,
            "job_id": job_id,
            "channel_id": channel_id,
            "channel_name": ch_name,
            "title": latest_title,
            "scheduled_time_vn": display_vn,
            "scheduled_time_iso": slot_iso,
            "youtube_video_id": yt_id,
            "shorts_url": yt_shorts_url,
            "drive_link": drive_link,
            "status": "SCHEDULED" if pub_res.get("status") == "success" else pub_res.get("status")
        }

     except Exception as ex:
        logger.error(f"[AutoPilot] Thất bại khi chạy quy trình cho kênh {channel_id}: {ex}", exc_info=True)
        return {
            "success": False,
            "channel_id": channel_id,
            "error": str(ex)
        }
     finally:
        _release_channel_autopilot(channel_id)


# ──────────────────────────────────────────────────────────────────────────────
# 3. CHẠY AUTOPILOT CHO TẤT CẢ KÊNH TRỐNG HÔM NAY (ALL EMPTY CHANNELS)
# ──────────────────────────────────────────────────────────────────────────────

async def run_autopilot_for_all_empty(user_id: int = 0, notify_telegram: bool = True) -> Dict[str, Any]:
    """
    Rà soát toàn bộ các kênh đang quản lý.
    Nếu kênh nào chưa có video hôm nay (is_empty == True),
    tự động chạy quy trình Auto-Pilot A-Z tuần tự cho kênh đó.
    """
    if not _try_acquire_channel_autopilot("__ALL_EMPTY__"):
        return {
            "success": False,
            "message": "Tiến trình Auto-Pilot cho các kênh trống đang chạy rồi, vui lòng chờ hoàn tất!",
            "processed_count": 0,
            "results": [],
        }

    try:
        summary = get_channel_today_summary()
        empty_channels = summary.get("empty_channels", [])

        # Lọc theo danh sách kênh chỉ định (nếu có cấu hình AUTOPILOT_ENABLED_CHANNELS)
        from config.settings import settings
        allowed_cids = settings.get_autopilot_enabled_channels()
        if allowed_cids:
            empty_channels = [c for c in empty_channels if c["channel_id"] in allowed_cids]

        if not empty_channels:
            msg = "✅ **TẤT CẢ CÁC KÊNH ĐÃ CÓ LỊCH PHÁT SÓNG ĐẦY ĐỦ HÔM NAY!**\n\nKhông có kênh nào bị bỏ trống."
            if notify_telegram:
                from app.services.channel_intelligence import send_telegram_broadcast
                send_telegram_broadcast(msg)
            return {
                "success": True,
                "message": "Tất cả kênh hôm nay đã có video, không cần sản xuất thêm.",
                "processed_count": 0,
                "results": []
            }

        logger.info(f"🔥 [AutoPilot All] Bắt đầu chạy tự động cho {len(empty_channels)} kênh trống hôm nay...")
        
        if notify_telegram:
            from app.services.channel_intelligence import send_telegram_broadcast
            ch_names = ", ".join([f"`{c['channel_name']}`" for c in empty_channels])
            start_msg = (
                f"🚀 **ĐÃ KÍCH HOẠT AUTOPILOT CHO {len(empty_channels)} KÊNH TRỐNG HÔM NAY!**\n\n"
                f"Các kênh đang sản xuất: {ch_names}\n"
                f"🤖 *Hệ thống đang tiến hành sinh kịch bản viral, render video 1080p và tự động lên lịch khung giờ vàng...*"
            )
            send_telegram_broadcast(start_msg)

        results = []
        for ech in empty_channels:
            cid = ech["channel_id"]
            res = await run_autopilot_pipeline_for_channel(
                channel_id=cid,
                user_id=user_id,
                notify_telegram=notify_telegram
            )
            results.append(res)
            # Giãn cách 3 giây giữa các kênh
            await asyncio.sleep(3)

        return {
            "success": True,
            "processed_count": len(results),
            "results": results
        }
    finally:
        _release_channel_autopilot("__ALL_EMPTY__")


MIN_SCHEDULED_QUEUE_PER_CHANNEL = 3


def auto_publish_rendered_jobs(notify_telegram: bool = True) -> List[Dict[str, Any]]:
    """
    Tự động đăng/lên lịch tất cả các video đã dựng xong (`RENDERED` hoặc `WAITING_QUOTA_RESET`)
    mà chưa được đẩy lên YouTube (`youtube_video_id IS NULL`).
    Kiểm định cứng VideoQA trước khi upload; nếu đạt thì gán khe giờ vàng tiếp theo của kênh và upload.
    """
    from app.services.channel_manager import get_channel
    from app.services.quality_evaluator import QualityEvaluator
    from app.agents.publisher_agent import PublisherAgent

    qa = QualityEvaluator()
    published_results: List[Dict[str, Any]] = []

    with get_db() as db:
        pending_rendered = (
            db.query(VideoJob)
            .filter(
                VideoJob.status.in_(["RENDERED", "WAITING_QUOTA_RESET"]),
                VideoJob.video_path.isnot(None),
                VideoJob.youtube_video_id.is_(None),
            )
            .order_by(VideoJob.id.asc())
            .all()
        )
        candidate_ids = [j.id for j in pending_rendered]

    for jid in candidate_ids:
        try:
            with get_db() as db:
                job = db.query(VideoJob).filter(VideoJob.id == jid).first()
                if not job or not job.video_path or not Path(job.video_path).exists():
                    continue
                v_path = job.video_path
                ch_id = job.target_channel_id or "ch_the_mysteries_of_the_world"
                title = job.title or job.topic or f"Shorts #{job.id}"
                desc = job.description or ""
                tags = [t.strip() for t in (job.tags or "").split(",") if t.strip()]
                topic = job.topic or ""
                scenes_json = job.scenes_json or ""
                audio_dur_ms = job.audio_duration_ms or 0

            # 1. Kiểm định VideoQA (Chặn cứng nếu lỗi)
            vid_qa = qa.evaluate_video(v_path, expected_duration_ms=audio_dur_ms)
            if not vid_qa.get("passed", False):
                with get_db() as db_f:
                    jf = db_f.query(VideoJob).filter(VideoJob.id == jid).first()
                    if jf:
                        jf.status = "FAILED"
                        jf.error_message = f"HardBlock VideoQA ({vid_qa.get('score')}/100): {'; '.join(vid_qa.get('issues', []))}"
                logger.warning(f"[AutoPublishRendered] 🚫 Chặn cứng Job #{jid} vì không đạt VideoQA ({vid_qa.get('score')}/100)")
                continue

            # 2. Lấy cấu hình kênh và khe giờ vàng tiếp theo
            ch_cfg = get_channel(ch_id) or {}
            if not ch_cfg:
                channels = settings.get_channels_config()
                ch_cfg = next((c for c in channels if c.get("id") == ch_id), {})
            ch_name = ch_cfg.get("name", ch_id)
            ch_lang = ch_cfg.get("language", "en")

            slot_dt, slot_iso, display_vn, display_us = get_next_available_golden_slot(ch_id)

            pinned_comment = None
            try:
                s_dict = json.loads(scenes_json) if scenes_json else {}
                if isinstance(s_dict, dict):
                    pinned_comment = s_dict.get("pinned_comment")
            except Exception:
                pass

            pub_agent = PublisherAgent(channel_config=ch_cfg)
            pub_res = pub_agent.upload_video(
                video_path=v_path,
                title=title,
                description=desc,
                tags=tags,
                privacy_status="private",
                schedule_time_iso=slot_iso,
                default_language=ch_lang,
                default_audio_language=ch_lang,
                pinned_comment=pinned_comment,
                topic=topic,
                script=scenes_json,
            )

            yt_id = pub_res.get("video_id")
            yt_url = pub_res.get("shorts_url") or (f"https://www.youtube.com/shorts/{yt_id}" if yt_id else None)

            with get_db() as db_u:
                ju = db_u.query(VideoJob).filter(VideoJob.id == jid).first()
                if ju:
                    if pub_res.get("status") == "success":
                        ju.status = "SCHEDULED"
                        ju.scheduled_time = slot_dt
                        ju.youtube_video_id = yt_id
                        ju.progress_percent = 100
                        ju.progress_message = f"Đã tự động lên lịch phát sóng vào {display_vn}!"
                        published_results.append({
                            "job_id": jid,
                            "channel_id": ch_id,
                            "channel_name": ch_name,
                            "title": title,
                            "scheduled_time_vn": display_vn,
                            "youtube_video_id": yt_id,
                            "shorts_url": yt_url,
                        })
                        if notify_telegram:
                            from app.services.channel_intelligence import send_telegram_broadcast
                            send_telegram_broadcast(
                                f"📤 **TỰ ĐỘNG ĐĂNG VIDEO ĐÃ DỰNG XONG (#{jid})**\n\n"
                                f"📺 **Kênh:** `{ch_name}` (`{ch_id}`)\n"
                                f"🎬 **Tiêu đề:** *{title}*\n"
                                f"⏰ **Lịch công chiếu:** `{display_vn}`\n"
                                f"🔗 **Link Shorts:** {yt_url or 'Đã khóa lịch'}"
                            )
                    elif pub_res.get("status") == "waiting_quota_reset":
                        ju.status = "WAITING_QUOTA_RESET"
                        break
            if pub_res.get("status") == "success":
                try:
                    from app.services.video_memory_rag import archive_job_to_rag_and_cleanup_files
                    archive_job_to_rag_and_cleanup_files(jid)
                except Exception:
                    pass
        except Exception as ex_pub:
            logger.warning(f"[AutoPublishRendered] Lỗi khi tự động đăng Job #{jid}: {ex_pub}")

    return published_results


async def ensure_minimum_channel_queue(
    min_queue: int = MIN_SCHEDULED_QUEUE_PER_CHANNEL,
    notify_telegram: bool = True,
    auto_produce: bool = True,
    user_id: int = 0,
) -> Dict[str, Any]:
    """
    Tự động hóa thật sự:
    1. Tự đăng ngay các video đã dựng xong (`RENDERED`) vào lịch.
    2. Kiểm tra hàng đợi `SCHEDULED` của từng kênh.
    3. Báo Telegram ngay khi phát hiện một kênh bị TRỐNG LỊCH (`scheduled_queue_count == 0`).
    4. Bảo đảm mỗi kênh luôn có ít nhất `min_queue` (mặc định = 3) video trong lịch đăng (`SCHEDULED`).
    """
    # Bước 1: Tự động đăng các video đã dựng xong (RENDERED)
    published_rendered = await asyncio.to_thread(auto_publish_rendered_jobs, notify_telegram)

    # Bước 2: Rà soát hàng đợi lịch đăng của tất cả các kênh
    summary = get_channel_today_summary()
    channels_list = summary.get("channels", [])
    allowed_cids = settings.get_autopilot_enabled_channels()
    if allowed_cids:
        channels_list = [c for c in channels_list if c["channel_id"] in allowed_cids]

    # Bước 3: Báo Telegram ngay khi phát hiện kênh trống lịch (0 video trong lịch đăng)
    empty_schedule_channels = [c for c in channels_list if int(c.get("scheduled_queue_count", 0)) == 0]
    if empty_schedule_channels and notify_telegram:
        state = _load_watchdog_state()
        empty_alerts = state.get("empty_schedule_alerts", {})
        now_ts = datetime.now(timezone.utc).timestamp()
        newly_empty = []
        for ch in empty_schedule_channels:
            cid = ch["channel_id"]
            last_alert_ts = float(empty_alerts.get(cid, 0.0))
            if now_ts - last_alert_ts >= 6 * 3600:
                newly_empty.append(ch)
                empty_alerts[cid] = now_ts

        if newly_empty:
            state["empty_schedule_alerts"] = empty_alerts
            _save_watchdog_state(state)
            from app.services.channel_intelligence import send_telegram_broadcast
            lines = [
                f"• 🔴 **{c['channel_name']}** (`{c['channel_id']}`) — **0 video trong lịch** (Khe tiếp theo: `{c.get('next_golden_slot', {}).get('display_vn', '')}`)"
                for c in newly_empty
            ]
            send_telegram_broadcast(
                f"🚨 **CẢNH BÁO KÊNH TRỐNG LỊCH ĐĂNG (0 VIDEO TRONG HÀNG ĐỢI)!**\n\n"
                + "\n".join(lines)
                + f"\n\n🤖 *Hệ thống đang tự động kích hoạt sản xuất để lấp đầy tối thiểu {min_queue} video/kênh trong lịch đăng!*"
            )

    # Bước 4: Lập kế hoạch sản xuất bù cho các kênh có scheduled_queue_count < min_queue (3 video)
    deficit_plan: List[Tuple[str, str, int]] = []
    for target_level in range(1, min_queue + 1):
        for ch in channels_list:
            q_count = int(ch.get("scheduled_queue_count", 0))
            if q_count < target_level:
                deficit_plan.append((ch["channel_id"], ch["channel_name"], target_level))

    produced_results = []
    if auto_produce and deficit_plan:
        if _try_acquire_channel_autopilot("__QUEUE_GUARD__"):
            try:
                for cid, cname, slot_num in deficit_plan:
                    logger.info(
                        f"🛡️ [QueueGuard min={min_queue}] Đang sản xuất video lấp hàng đợi #{slot_num}/{min_queue} cho kênh '{cname}' ({cid})..."
                    )
                    res = await run_autopilot_pipeline_for_channel(
                        channel_id=cid,
                        user_id=user_id,
                        notify_telegram=notify_telegram,
                    )
                    produced_results.append(res)
                    await asyncio.sleep(2)
            finally:
                _release_channel_autopilot("__QUEUE_GUARD__")

    return {
        "success": True,
        "min_queue_target": min_queue,
        "auto_published_rendered_count": len(published_rendered),
        "empty_schedule_channels": [c["channel_id"] for c in empty_schedule_channels],
        "deficit_tasks_count": len(deficit_plan),
        "produced_count": len(produced_results),
        "produced_results": produced_results,
    }


async def run_autopilot_target_per_channel(
    target_videos_per_channel: int = MIN_SCHEDULED_QUEUE_PER_CHANNEL,
    channel_ids: Optional[List[str]] = None,
    user_id: int = 0,
    notify_telegram: bool = True,
) -> Dict[str, Any]:
    """
    Rà soát toàn bộ các kênh:
    1. Tự đăng các video đã dựng xong (`RENDERED`).
    2. Tự động sản xuất + đặt lịch khung giờ vàng để mỗi kênh có ít nhất `target_videos_per_channel` (mặc định 3 video) trong lịch đăng (`SCHEDULED`).
    """
    if not _try_acquire_channel_autopilot("__TARGET_BATCH__"):
        return {
            "success": False,
            "message": "Tiến trình Auto-Pilot Batch đang chạy rồi, vui lòng chờ hoàn tất!",
            "processed_count": 0,
            "results": [],
        }

    try:
        await asyncio.to_thread(auto_publish_rendered_jobs, notify_telegram)
        summary = get_channel_today_summary()
        channels_list = summary.get("channels", [])
        if channel_ids:
            channels_list = [c for c in channels_list if c["channel_id"] in channel_ids]

        # Xây dựng hàng đợi theo từng vòng để mỗi kênh có ít nhất target_videos_per_channel (3) video trong lịch SCHEDULED
        production_plan: List[Tuple[str, str, int]] = []
        for round_idx in range(1, target_videos_per_channel + 1):
            for ch in channels_list:
                curr_q = int(ch.get("scheduled_queue_count", 0))
                if curr_q < round_idx:
                    production_plan.append((ch["channel_id"], ch["channel_name"], round_idx))

        if not production_plan:
            return {
                "success": True,
                "message": f"Tất cả kênh đã có đủ >= {target_videos_per_channel} video trong lịch đăng!",
                "processed_count": 0,
                "results": [],
            }

        logger.info(
            f"🔥 [AutoPilot Target={target_videos_per_channel} scheduled/kênh] "
            f"Bắt đầu sản xuất tổng cộng {len(production_plan)} video cho {len(channels_list)} kênh..."
        )

        if notify_telegram:
            from app.services.channel_intelligence import send_telegram_broadcast
            plan_desc = ", ".join([f"`{name}` (#{r_idx})" for _, name, r_idx in production_plan])
            send_telegram_broadcast(
                f"🚀 **KÍCH HOẠT TỰ ĐỘNG DUY TRÌ HÀNG ĐỢI (>= {target_videos_per_channel} VIDEO / KÊNH)!**\n\n"
                f"📋 **Tổng số video cần sản xuất & lên lịch:** `{len(production_plan)} video`\n"
                f"🎯 **Thứ tự ưu tiên:** {plan_desc}\n"
                f"🤖 *Hệ thống đang lấy Top 3 Thắng / 3 Thua của từng kênh, chấm điểm Hook theo tỷ lệ vuốt bỏ thật, dựng 1080p và khóa lịch Giờ Vàng...*"
            )

        results = []
        for idx, (cid, cname, round_idx) in enumerate(production_plan, start=1):
            logger.info(
                f"🎬 [{idx}/{len(production_plan)}] Đang sản xuất video hàng đợi #{round_idx}/{target_videos_per_channel} cho kênh '{cname}' ({cid})..."
            )
            res = await run_autopilot_pipeline_for_channel(
                channel_id=cid,
                user_id=user_id,
                notify_telegram=notify_telegram,
            )
            results.append(res)
            await asyncio.sleep(3)

        return {
            "success": True,
            "processed_count": len(results),
            "results": results,
        }
    finally:
        _release_channel_autopilot("__TARGET_BATCH__")


# ──────────────────────────────────────────────────────────────────────────────
# 4. CHỦ ĐỘNG BÁO CÁO & NHẮC NHỞ HÀNG NGÀY CHO NGƯỜI DÙNG (DAILY WATCHDOG)
# ──────────────────────────────────────────────────────────────────────────────

def check_and_send_daily_reminders(bot=None, force: bool = False) -> Dict[str, Any]:
    """
    Tiến trình Watchdog kiểm tra & báo cáo theo LỊCH HẸN ĐỊNH KỲ:
    - Ca Sáng (Morning Briefing): 09:00 - 09:30 VN (Lên kế hoạch đầu ngày cho 5 kênh).
    - Ca Chiều (Afternoon Review): 16:30 - 17:00 VN (Rà soát trước khung giờ vàng 20:00 VN).
    - Nếu có lưu ý cần chú ý (kênh chưa có lịch đăng hôm nay):
      Gửi báo cáo và câu hỏi nhắc nhở trực tiếp vào NHÓM TELEGRAM kèm nút bấm 1 chạm.
    - Trạng thái được lưu cố định vào tệp json để không bao giờ bị reset/spam khi khởi động lại bot.
    """
    now_vn = datetime.now(VN_TZ)
    today_date_str = now_vn.strftime("%Y-%m-%d")
    current_hour = now_vn.hour
    current_minute = now_vn.minute

    state = _load_watchdog_state()

    # Kiểm tra xem nhóm đã bấm 'Bỏ qua hôm nay' chưa
    if not force and state.get("dismissed_date") == today_date_str:
        return {"notified": False, "reason": "Nhóm đã bấm bỏ qua nhắc nhở hôm nay."}

    # Định nghĩa lịch hẹn cố định trong ngày (Giờ Việt Nam):
    # - Ca Sáng: 09:00 - 09:30 VN
    # - Ca Chiều: 16:30 - 17:00 VN
    is_morning_window = (current_hour == 9 and 0 <= current_minute <= 30)
    is_afternoon_window = (current_hour == 16 and 30 <= current_minute <= 59) or (current_hour == 17 and current_minute == 0)

    if not force:
        if is_morning_window and state.get("last_morning_date") == today_date_str:
            return {"notified": False, "reason": "Đã gửi báo cáo ca sáng hôm nay rồi."}
        if is_afternoon_window and state.get("last_afternoon_date") == today_date_str:
            return {"notified": False, "reason": "Đã gửi báo cáo ca chiều hôm nay rồi."}
        if not (is_morning_window or is_afternoon_window):
            return {"notified": False, "reason": f"Hiện tại ({now_vn.strftime('%H:%M')}) nằm ngoài khung giờ lịch hẹn báo cáo (09:00 và 16:30 VN)."}

    # Lấy thông tin trạng thái các kênh hôm nay
    summary = get_channel_today_summary()
    empty_channels = summary.get("empty_channels", [])
    scheduled_channels = summary.get("scheduled_channels", [])

    # Nếu tất cả các kênh hôm nay đã có lịch đăng đầy đủ:
    if not empty_channels and not force:
        logger.debug(f"[AutoPilot Watchdog] Tất cả {summary['total_channels']} kênh hôm nay đã có lịch đăng. Không có lưu ý cần gửi.")
        # Vẫn cập nhật cờ ngày để không lặp lại kiểm tra
        if is_morning_window:
            state["last_morning_date"] = today_date_str
            _save_watchdog_state(state)
        elif is_afternoon_window:
            state["last_afternoon_date"] = today_date_str
            _save_watchdog_state(state)
        return {"notified": False, "reason": "Tất cả kênh hôm nay đã có lịch đăng đầy đủ."}

    logger.info(f"📢 [AutoPilot Watchdog] Phát hiện {len(empty_channels)} kênh cần chú ý hôm nay! Gửi báo cáo định kỳ vào Nhóm Telegram...")

    # Xây dựng nội dung báo cáo trực quan
    period_title = "BUỔI SÁNG (09:00)" if current_hour < 13 else "BUỔI CHIỀU (16:30)"
    header_icon = "☀️" if current_hour < 13 else "🌇"

    report_lines = []
    for ch in summary.get("channels", []):
        cname = ch["channel_name"]
        handle = ch.get("handle") or ""
        v_count = ch["today_video_count"]
        p_job = ch.get("pending_job")
        next_slot = ch.get("next_golden_slot", {}).get("display_vn", "")

        if v_count > 0:
            times = ", ".join([v["time_vn"] for v in ch["today_videos"]])
            report_lines.append(f"• 🟢 **{cname}**: `{v_count} video` (Lúc {times})")
        elif p_job:
            report_lines.append(f"• ⏳ **{cname}**: Đang dựng video (#{p_job['job_id']} - {p_job['progress_percent']}%)")
        else:
            report_lines.append(f"• 🔴 **{cname}**: **0 video (CHƯA CÓ LỊCH)**\n  ↳ _Khung giờ trống đề xuất: {next_slot}_")

    channels_str = "\n".join(report_lines)

    alert_text = (
        f"{header_icon} **BÁO CÁO KÊNH THEO LỊCH ĐỊNH KỲ ({period_title} - {summary['date_vn']})**\n\n"
        f"📊 **Tình hình phát sóng các kênh của nhóm:**\n"
        f"{channels_str}\n\n"
        f"⚠️ **LƯU Ý CẦN CHÚ Ý:**\n"
        f"• Phát hiện **{len(empty_channels)} kênh chưa có lịch phát sóng** hôm nay!\n"
        f"💡 *Nếu hôm nay cả nhóm bận hoặc chưa kịp dựng video, hãy bấm nút dưới đây để bot tự động sản xuất A-Z (lên kịch bản, render 1080p và đặt lịch đúng khung giờ vàng 20:00/04:00)!*"
    )

    # Xây dựng Inline Keyboard tiện lợi
    inline_kb = [
        [
            {"text": f"🔥 Bật Auto-Pilot Tất Cả Kênh Trống ({len(empty_channels)} Kênh)", "callback_data": "autopilot_run_all"}
        ]
    ]

    for ech in empty_channels:
        cid = ech["channel_id"]
        cname = ech["channel_name"]
        short_name = cname.split(" - ")[0][:20]
        inline_kb.append([
            {"text": f"🚀 Auto-Pilot: {short_name}", "callback_data": f"autopilot_run_channel:{cid}"}
        ])

    inline_kb.append([
        {"text": "🎯 Chỉ Định Chọn Kênh...", "callback_data": "autopilot_pick_channel"},
        {"text": "📅 Xem Lịch Đầy Đủ", "callback_data": "schedule:all"}
    ])
    inline_kb.append([
        {"text": "💤 Hôm Nay Nghỉ (Bỏ Qua)", "callback_data": "autopilot_dismiss"}
    ])

    from app.services.channel_intelligence import send_telegram_broadcast
    send_telegram_broadcast(alert_text, inline_keyboard=inline_kb)

    if is_morning_window or force:
        state["last_morning_date"] = today_date_str
    if is_afternoon_window or force:
        state["last_afternoon_date"] = today_date_str
    _save_watchdog_state(state)

    return {
        "notified": True,
        "date_vn": summary["date_vn"],
        "empty_count": len(empty_channels),
        "empty_channels": [c["channel_id"] for c in empty_channels]
    }


def dismiss_today_reminder():
    """Đánh dấu nhóm đã bấm 'Bỏ qua hôm nay' để không làm phiền thêm trong ngày."""
    now_vn = datetime.now(VN_TZ)
    today_str = now_vn.strftime("%Y-%m-%d")
    state = _load_watchdog_state()
    state["dismissed_date"] = today_str
    _save_watchdog_state(state)
    logger.info(f"[AutoPilot] Đã ghi nhận bỏ qua nhắc nhở cho ngày {today_str}")
