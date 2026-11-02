"""app.web.routers.analytics_and_autopilot — YouTube Live Analytics, Dự báo AI, AutoPilot và Manual Studio."""
import os
import sys
import time
import secrets
import json
import yaml
import base64
import threading
import logging
import asyncio
import shutil
import re
import requests as http_requests
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Form, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import contextmanager
from sqlalchemy import func
from config.settings import settings
from app.database.models import init_db, get_db, User, VideoJob, ActivityLog, log_activity
from app.utils.security import load_encrypted_json, save_encrypted_json, is_safe_identifier, is_safe_path

logger = logging.getLogger(__name__)

import hmac
import html as html_lib

from app.web.routers.core_and_dashboard import (
    app,
    BASE_DIR,
    STATIC_DIR,
    MEDIA_DIR,
    DEFAULT_DESKTOP_CLIENT,
    DEFAULT_WEB_CLIENT,
    _REVOKED_CLIENT_ID_PREFIXES,
    _safe_env_client_id,
    _is_valid_oauth_client_config,
    _resolve_and_load_client_secret,
    _channel_token_health_cache,
    _check_channel_token_health,
    _format_channel_golden_slots_vn,
)
from app.web.routers.accounts_and_oauth import _read_channels_yaml, _write_channels_yaml

# ==============================================================================
# 11. YOUTUBE LIVE ANALYTICS & AI GROWTH FORECAST
# ==============================================================================
from app.services.youtube_analytics import fetch_channel_live_stats, calculate_ai_forecast

class ForecastRequest(BaseModel):
    channel_id: Optional[str] = "ch_psychology_of_the_world"
    daily_uploads: Optional[float] = 1.0
    custom_avg_views: Optional[int] = None

@app.get("/api/analytics/channels")
async def get_analytics_channels():
    """Lấy danh sách các kênh có hỗ trợ quét số liệu phân tích."""
    cfg = _read_channels_yaml()
    channels = cfg.get("channels", [])
    result = []
    for c in channels:
        result.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "handle": c.get("handle") or "",
            "channel_url": c.get("channel_url") or "",
            "niche": c.get("niche", "general"),
            "google_account": c.get("google_account", "account_1")
        })
    return {"channels": result}

@app.get("/api/analytics/channel/{channel_id}")
async def get_channel_live_analytics(channel_id: str):
    """Cào trực tiếp số liệu thời gian thực và tính toán dự đoán tăng trưởng cho kênh."""
    cfg = _read_channels_yaml()
    channels = cfg.get("channels", [])
    channel = next((c for c in channels if c.get("id") == channel_id), None)
    if not channel and channels:
        channel = channels[0]
    elif not channel:
        raise HTTPException(status_code=404, detail="Chưa có cấu hình kênh trong hệ thống")

    url = channel.get("channel_url") or channel.get("handle")
    if not url:
        raise HTTPException(status_code=400, detail="Kênh này chưa được cấu hình đường dẫn YouTube hoặc handle (@tênkênh)")

    stats = fetch_channel_live_stats(url)
    if not stats.get("success"):
        raise HTTPException(status_code=500, detail=stats.get("error", "Không thể lấy số liệu kênh"))

    forecast = calculate_ai_forecast(
        total_views=stats.get("total_views", 0),
        total_videos=len(stats.get("videos", [])),
        avg_views=stats.get("avg_views", 300),
        daily_uploads=1.0,
        niche=channel.get("niche", "history_mystery"),
        language=channel.get("language", "en"),
        channel_id=channel_id
    )

    stats["forecast"] = forecast
    stats["channel_id"] = channel_id
    stats["channel_niche"] = channel.get("niche", "history_mystery")
    stats["channel_language"] = channel.get("language", "en")
    return stats

@app.post("/api/analytics/forecast")
async def calculate_forecast_endpoint(req: ForecastRequest):
    """Tính toán dự đoán tăng trưởng tùy biến theo tốc độ xuất bản và mức views mục tiêu."""
    cfg = _read_channels_yaml()
    channels = cfg.get("channels", [])
    channel = next((c for c in channels if c.get("id") == req.channel_id), {})
    url = channel.get("channel_url") or channel.get("handle")
    if not url and channels:
        url = channels[0].get("channel_url") or channels[0].get("handle")

    stats = fetch_channel_live_stats(url) if url else {}
    total_views = stats.get("total_views", 500) if stats.get("success") else 500
    total_vids = len(stats.get("videos", [])) if stats.get("success") else 2
    avg_views = req.custom_avg_views or (stats.get("avg_views", 300) if stats.get("success") else 300)

    forecast = calculate_ai_forecast(
        total_views=total_views,
        total_videos=total_vids,
        avg_views=avg_views,
        daily_uploads=req.daily_uploads or 1.0,
        niche=channel.get("niche", "history_mystery"),
        language=channel.get("language", "en"),
        channel_id=req.channel_id
    )
    return {
        "success": True,
        "forecast": forecast
    }


# ═════════════════════════════════════════════════════════════════════════════
# ─── CHANNEL INTELLIGENCE & KPI MANAGEMENT API ──────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/api/channels/{channel_id}/topics/suggest")
async def suggest_channel_topics_endpoint(channel_id: str, count: int = 5, video_format: str = "short"):
    """
    Gợi ý ý tưởng video Shorts (9:16) hoặc Video Dài 5 Chương (16:9 Documentary) cho kênh
    dựa trên phân tích DNA Top Performer & Flop, chống trùng lặp 100% với video cũ và tự động khớp ngôn ngữ kênh.
    """
    try:
        from app.services.channel_intelligence import (
            suggest_channel_topics,
            get_channel_existing_portfolio,
            extract_saturated_channel_concepts,
        )
        vf_norm = "long" if str(video_format or "short").strip().lower() in ("long", "16:9", "documentary") else "short"
        portfolio = await asyncio.to_thread(get_channel_existing_portfolio, channel_id)
        ideas = await asyncio.to_thread(suggest_channel_topics, channel_id=channel_id, count=count, video_format=vf_norm)
        vids_ranked = portfolio.get("videos_with_views", [])
        saturated = extract_saturated_channel_concepts(portfolio.get("all_titles", []), vids_ranked)
        top_video = vids_ranked[0] if vids_ranked else None
        flop_video = vids_ranked[-1] if len(vids_ranked) > 1 else None

        return {
            "success": True,
            "channel_id": channel_id,
            "channel_name": portfolio.get("channel_name", channel_id),
            "niche": portfolio.get("niche", "general"),
            "language": portfolio.get("language", "en"),
            "published_count": portfolio.get("published_count", 0),
            "saturated_concepts": saturated,
            "top_performer": top_video,
            "flop_performer": flop_video,
            "count": len(ideas),
            "ideas": ideas
        }
    except Exception as e:
        logger.error(f"Lỗi gợi ý ý tưởng kênh {channel_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/channels/kpi/report")
async def get_kpi_report_endpoint():
    """
    Báo cáo sức khỏe KPI toàn bộ các kênh YouTube:
    - Danh sách kênh đúng hạn, cảnh báo, trễ deadline >= 2 ngày.
    """
    try:
        from app.services.channel_intelligence import run_network_kpi_audit
        audit = run_network_kpi_audit()
        return {
            "success": True,
            "report": audit
        }
    except Exception as e:
        logger.error(f"Lỗi tạo báo cáo KPI: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

class AutoGenerateKPIRequest(BaseModel):
    grace_hours: Optional[float] = 4.0
    force: Optional[bool] = False

@app.post("/api/channels/{channel_id}/kpi/auto-generate")
async def auto_generate_kpi_video(channel_id: str, req: Optional[AutoGenerateKPIRequest] = None):
    """
    Tự động sản xuất video cứu tương tác cho kênh bị trễ deadline 2 ngày (hoặc force=True).
    """
    try:
        from app.services.channel_intelligence import trigger_auto_generation_for_overdue_channel
        grace = req.grace_hours if req and req.grace_hours else 4.0
        force = req.force if req and req.force is not None else False
        res = trigger_auto_generation_for_overdue_channel(channel_id, grace_hours=grace, force=force)
        return res
    except Exception as e:
        logger.error(f"Lỗi tự động sản xuất video cho kênh {channel_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

class ApproveJobRequest(BaseModel):
    action: str = "schedule"  # "now", "schedule", "cancel"

@app.post("/api/videos/{job_id}/approve")
async def approve_video_job(job_id: int, req: Optional[ApproveJobRequest] = None):
    """
    Duyệt video tự động tạo (AWAITING_APPROVAL):
    - action='now': Duyệt để đăng ngay.
    - action='schedule': Xếp lịch vào khung giờ vàng chuẩn quốc tế.
    - action='cancel': Huỷ bỏ và chuyển trạng thái về DRAFT.
    """
    action = (req.action if req else "schedule").lower()
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")

        target_ch = job.target_channel_id

        if action == "cancel":
            job.status = "DRAFT"
            job.progress_message = "Người dùng đã từ chối/hủy duyệt video tự động"
            log_activity(db, 0, "Web_User", "CANCEL_AUTO_JOB", f"Người dùng hủy duyệt Video #{job_id}", channel_id=target_ch)
            return {"success": True, "message": f"Đã hủy duyệt video #{job_id}"}

        if action == "schedule":
            from app.services.smart_scheduler import get_next_available_golden_slot
            slot_dt, slot_iso, vn_label, us_label = get_next_available_golden_slot(target_ch)
            job.status = "SCHEDULED"
            job.scheduled_time = slot_dt
            job.progress_message = f"Đã duyệt: Lên lịch công chiếu lúc {vn_label} ({us_label})"
            log_activity(db, 0, "Web_User", "APPROVE_SCHEDULE", f"Duyệt Video #{job_id} lên lịch lúc {vn_label}", channel_id=target_ch)
            return {
                "success": True,
                "message": f"Đã duyệt và lên lịch công chiếu Video #{job_id} lúc {vn_label}",
                "scheduled_slot_vn": vn_label,
                "scheduled_slot_us": us_label
            }

        elif action == "now":
            job.status = "RENDERED"
            log_activity(db, 0, "Web_User", "APPROVE_NOW", f"Duyệt Video #{job_id} để xuất bản", channel_id=target_ch)
            return {
                "success": True,
                "message": f"Đã duyệt Video #{job_id} sẵn sàng xuất bản!"
            }
        else:
            raise HTTPException(status_code=400, detail=f"Hành động '{action}' không hợp lệ (hỗ trợ: now, schedule, cancel)")


# ═════════════════════════════════════════════════════════════════════════════
# ─── AUTOPILOT A-Z & SMART SCHEDULE ENGINE API ──────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════

class RunAutoPilotPayload(BaseModel):
    channel_id: Optional[str] = None
    all_empty: Optional[bool] = False
    target_slot_iso: Optional[str] = None
    target_videos_per_channel: Optional[int] = None

@app.get("/api/autopilot/status")
async def get_autopilot_status():
    """
    Lấy thông tin trạng thái phát sóng hôm nay của 5 kênh YouTube:
    - Kênh nào đã có video (khung giờ nào), kênh nào CHƯA CÓ LỊCH (trống).
    - Khe giờ vàng tiếp theo cho từng kênh.
    """
    try:
        from app.services.autopilot_service import get_channel_today_summary
        return get_channel_today_summary()
    except Exception as e:
        logger.error(f"Lỗi lấy trạng thái Auto-Pilot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/autopilot/run")
async def run_autopilot_endpoint(payload: RunAutoPilotPayload, background_tasks: BackgroundTasks):
    """
    Kích hoạt quy trình Auto-Pilot A-Z:
    - Nếu target_videos_per_channel > 0: Tự động sản xuất & lên lịch đủ N video/kênh/ngày cho tất cả kênh.
    - Nếu all_empty=True: Tự động chạy tuần tự cho tất cả kênh chưa có video hôm nay.
    - Nếu có channel_id: Tự động chạy cho kênh được chỉ định.
    Quy trình chạy ngầm: Tự động sinh kịch bản viral -> Render 1080p -> Lên lịch khung giờ vàng YouTube Shorts.
    """
    try:
        from app.services.autopilot_service import (
            run_autopilot_pipeline_for_channel,
            run_autopilot_for_all_empty,
            run_autopilot_target_per_channel,
        )

        def _bg_run_channel(cid: str, slot_iso: Optional[str]):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(run_autopilot_pipeline_for_channel(cid, target_slot_iso=slot_iso, user_id=0, notify_telegram=True))
            finally:
                loop.close()

        def _bg_run_all():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(run_autopilot_for_all_empty(user_id=0, notify_telegram=True))
            finally:
                loop.close()

        def _bg_run_target(target_cnt: int):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(run_autopilot_target_per_channel(target_videos_per_channel=target_cnt, user_id=0, notify_telegram=True))
            finally:
                loop.close()

        if payload.target_videos_per_channel and payload.target_videos_per_channel > 0:
            background_tasks.add_task(_bg_run_target, int(payload.target_videos_per_channel))
            return {
                "success": True,
                "message": f"Đã kích hoạt chiến dịch Auto-Pilot {payload.target_videos_per_channel} video/kênh cho toàn bộ hệ thống!"
            }
        elif payload.all_empty:
            background_tasks.add_task(_bg_run_all)
            return {
                "success": True,
                "message": "Đã kích hoạt Auto-Pilot A-Z cho tất cả kênh trống hôm nay! Tiến trình đang chạy ngầm."
            }
        elif payload.channel_id:
            background_tasks.add_task(_bg_run_channel, payload.channel_id, payload.target_slot_iso)
            return {
                "success": True,
                "message": f"Đã kích hoạt Auto-Pilot A-Z cho kênh '{payload.channel_id}'! Tiến trình đang chạy ngầm."
            }
        else:
            raise HTTPException(status_code=400, detail="Vui lòng cung cấp channel_id, all_empty=true hoặc target_videos_per_channel")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi kích hoạt Auto-Pilot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/autopilot/remind")
async def trigger_autopilot_reminder_endpoint():
    """Chủ động kích hoạt quét báo cáo và gửi nhắc nhở kênh trống qua Telegram."""
    try:
        from app.services.autopilot_service import check_and_send_daily_reminders
        res = check_and_send_daily_reminders(force=True)
        return {
            "success": True,
            "result": res
        }
    except Exception as e:
        logger.error(f"Lỗi gửi nhắc nhở Auto-Pilot: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ═════════════════════════════════════════════════════════════════════════════
# ─── MANUAL STUDIO (GHÉP CẢNH TÙY CHỈNH) API ─────────────────────────────────
# ═════════════════════════════════════════════════════════════════════════════


def _execute_manual_render(job_id: int, mode: str, video_path: Optional[str], script_text: str, scenes_items: list, config_item: Any):
    """Worker chạy ngầm cho tác vụ ghép / lồng tiếng video tùy chỉnh."""
    from app.services.manual_studio_service import ManualStudioService
    from app.database.models import get_db, VideoJob, log_activity

    service = ManualStudioService()

    def db_progress(percent: int, message: str):
        try:
            with get_db() as db:
                j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if j:
                    j.progress_percent = percent
                    j.progress_message = message
        except Exception:
            pass

    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        if mode == "single_video" and video_path:
            final_video_path = loop.run_until_complete(
                service.render_single_video_studio(
                    job_id=job_id,
                    video_path=video_path,
                    script_text=script_text,
                    config=config_item,
                    progress_callback=db_progress
                )
            )
        else:
            final_video_path = loop.run_until_complete(
                service.render_manual_video(
                    job_id=job_id,
                    scenes=scenes_items,
                    config=config_item,
                    progress_callback=db_progress
                )
            )
        loop.close()

        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if j:
                j.status = "RENDERED"
                j.video_path = final_video_path
                j.progress_percent = 100
                j.progress_message = "Lồng tiếng & gắn phụ đề hoàn tất!"
                log_activity(db, 0, "Web Studio", "MANUAL_RENDER", f"Hoàn tất video #{job_id}: {j.title}")

    except Exception as e:
        logger.error(f"Lỗi render manual video #{job_id}: {e}", exc_info=True)
        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if j:
                j.status = "FAILED"
                j.progress_message = f"Lỗi: {str(e)[:100]}"
                j.error_message = str(e)
                log_activity(db, 0, "Web Studio", "MANUAL_RENDER_FAILED", f"Lỗi video #{job_id}: {e}")


@app.get("/api/manual-studio/sfx")
async def get_manual_studio_sfx():
    """Lấy danh sách tất cả hiệu ứng âm thanh SFX có sẵn trong hệ thống."""
    try:
        from app.services.manual_studio_service import ManualStudioService
        svc = ManualStudioService()
        available = svc.get_available_sfx()
        options = [
            {"id": "auto", "name": "🤖 AI Tự Chọn Thông Minh", "filename": "auto"},
            {"id": "none", "name": "🔇 Tắt SFX Ở Cảnh Này", "filename": "none"}
        ] + available
        return {"success": True, "sfx": options}
    except Exception as e:
        logger.warning(f"Lỗi đọc sfx list: {e}")
        return {
            "success": True,
            "sfx": [
                {"id": "auto", "name": "🤖 AI Tự Chọn Thông Minh", "filename": "auto"},
                {"id": "none", "name": "🔇 Tắt SFX Ở Cảnh Này", "filename": "none"},
                {"id": "whoosh", "name": "Whoosh", "filename": "whoosh.mp3"},
                {"id": "hit", "name": "Hit", "filename": "hit.wav"}
            ]
        }


@app.get("/api/manual-studio/voices")
async def get_manual_studio_voices():
    """Lấy danh sách giọng đọc AI được hỗ trợ tốt nhất."""
    voices = [
        {"id": "en-US-AndrewNeural", "name": "🇺🇸 English - Andrew (Dynamic & Natural - Default)", "lang": "en"},
        {"id": "en-US-ChristopherNeural", "name": "🇺🇸 English - Christopher (Deep Cinematic)", "lang": "en"},
        {"id": "en-US-GuyNeural", "name": "🇺🇸 English - Guy (Dynamic Storyteller)", "lang": "en"},
        {"id": "en-US-JennyNeural", "name": "🇺🇸 English - Jenny (Warm & Natural)", "lang": "en"},
        {"id": "en-US-AriaNeural", "name": "🇺🇸 English - Aria (Engaging & Clear)", "lang": "en"},
        {"id": "vi-VN-NamMinhNeural", "name": "🇻🇳 Tiếng Việt - Nam Minh (Nam trầm ấm)", "lang": "vi"},
        {"id": "vi-VN-HoaiMyNeural", "name": "🇻🇳 Tiếng Việt - Hoài My (Nữ truyền cảm)", "lang": "vi"}
    ]
    return {"success": True, "voices": voices}


class VoicePreviewRequest(BaseModel):
    voice: str = "en-US-AndrewNeural"
    rate: str = "+0%"
    pitch: str = "+0Hz"
    text: Optional[str] = None


@app.post("/api/manual-studio/preview-voice")
async def preview_voice_sample(req: VoicePreviewRequest):
    """
    Sinh đoạn audio nghe thử giọng đọc AI theo nội dung kịch bản hoặc văn bản mẫu,
    trả về trực tiếp stream MP3 để trình duyệt phát ra loa ngay lập tức.
    """
    import edge_tts

    voice = req.voice or "en-US-AndrewNeural"
    rate = req.rate if (req.rate and req.rate != "+0%") else None
    pitch = req.pitch if (req.pitch and req.pitch != "+0Hz") else None

    # Lấy văn bản nghe thử
    raw_text = (req.text or "").strip()
    if not raw_text:
        if voice.startswith("vi"):
            text = "Xin chào! Đây là giọng đọc thử nghiệm của tôi cho video của bạn. Hãy cùng bắt đầu sáng tạo nội dung ngay bây giờ!"
        else:
            text = "Hello! This is a preview of my voice for your video. Let's create something captivating together!"
    else:
        # Nếu kịch bản dài, trích xuất câu đầu tiên (hoặc tối đa 25 từ) để tạo preview siêu nhanh
        first_line = raw_text.split("\n")[0].strip()
        sentences = [s.strip() for s in first_line.split(".") if s.strip()]
        snippet = sentences[0] if sentences else first_line
        words = snippet.split()
        if len(words) > 25:
            text = " ".join(words[:25]) + "..."
        else:
            text = snippet

    audio_bytes = bytearray()
    last_err = None

    for attempt in range(1, 3):
        try:
            kwargs = {"text": text, "voice": voice}
            if rate and attempt == 1:
                kwargs["rate"] = rate
            if pitch and attempt == 1:
                kwargs["pitch"] = pitch

            communicate = edge_tts.Communicate(**kwargs)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_bytes.extend(chunk["data"])
            if len(audio_bytes) > 0:
                break
        except Exception as e:
            last_err = e
            logger.warning(f"Preview voice attempt {attempt} error: {e}")

    if len(audio_bytes) == 0:
        try:
            import tempfile
            from app.agents.voice_agent import VoiceAgent
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
                tmp_preview = tf.name
            va = VoiceAgent(default_voice=voice)
            await va.synthesize_single_clip(
                text=text,
                output_path=tmp_preview,
                voice=voice,
                rate=rate or "+0%",
                pitch=pitch or "+0Hz",
            )
            if Path(tmp_preview).exists() and Path(tmp_preview).stat().st_size > 256:
                audio_bytes.extend(Path(tmp_preview).read_bytes())
            Path(tmp_preview).unlink(missing_ok=True)
        except Exception as fb_err:
            last_err = fb_err

    if len(audio_bytes) == 0:
        logger.error(f"Preview voice failed: {last_err}")
        raise HTTPException(status_code=500, detail=f"Không thể sinh giọng đọc mẫu: {last_err}")

    return Response(content=bytes(audio_bytes), media_type="audio/mpeg")


@app.get("/api/manual-studio/bgm")
async def get_manual_studio_bgm():
    """Lấy danh sách nhạc nền (BGM) có sẵn."""
    bgm_list = []
    base_dir = BASE_DIR
    for candidate in [base_dir / "assets" / "bgm", base_dir / "assets" / "audio" / "bgm"]:
        if candidate.exists():
            for f in sorted(candidate.iterdir(), key=lambda x: x.name.lower()):
                if f.is_file() and f.suffix.lower() in [".mp3", ".wav", ".ogg", ".m4a"]:
                    bgm_list.append({
                        "id": f.stem,
                        "name": f.stem.replace("_", " ").replace("-", " ").title(),
                        "path": str(f)
                    })
    return {"success": True, "bgm": bgm_list}


@app.post("/api/manual-studio/create")
async def create_manual_studio_video(
    background_tasks: BackgroundTasks,
    payload: str = Form(...),
    files: List[UploadFile] = File(...)
):
    """
    Tiếp nhận video tải lên (từ Google Flow, CapCut...) và lời thoại kịch bản,
    khởi tạo VideoJob trong SQLite và bắt đầu tiến trình lồng tiếng + phụ đề ngầm.
    """
    try:
        data = json.loads(payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Dữ liệu payload JSON không hợp lệ: {e}")

    title = data.get("title", "").strip() or "Shorts Lồng Tiếng"
    channel_id = data.get("target_channel_id", "channel_1")
    mode = data.get("mode", "single_video")
    script_text = data.get("script", "").strip()
    scenes_spec = data.get("scenes", [])

    if not script_text and scenes_spec:
        script_text = "\n".join([s.get("narration", "") for s in scenes_spec if s.get("narration")])

    if not script_text:
        raise HTTPException(status_code=400, detail="Vui lòng nhập lời thoại kịch bản cho video")

    if len(files) == 0:
        raise HTTPException(status_code=400, detail="Chưa có file media nào được tải lên")

    # 1. Tạo bản ghi VideoJob trong Database
    with get_db() as db:
        job = VideoJob(
            user_id=0,
            target_channel_id=channel_id,
            topic=f"[Lồng Tiếng] {title}",
            title=title,
            script=script_text[:500],
            status="RENDERING",
            progress_percent=5,
            progress_message="Đang tiếp nhận tệp tin và chuẩn bị lồng tiếng..."
        )
        db.add(job)
        db.flush()
        job_id = job.id
        log_activity(db, 0, "Web Studio", "MANUAL_STUDIO_CREATE", f"Khởi tạo tác vụ lồng tiếng video #{job_id}: {title}")

    # 2. Lưu file upload
    from app.services.manual_studio_service import ManualSceneItem, ManualStudioConfig
    base_dir = BASE_DIR
    upload_dir = base_dir / "storage" / "temp" / f"manual_{job_id}" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    ALLOWED_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".jpg", ".jpeg", ".png", ".webp", ".mp3", ".wav"}
    MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB

    saved_file_paths = []
    for idx, f in enumerate(files):
        ext = Path(f.filename).suffix.lower() if f.filename else ".mp4"
        if ext not in ALLOWED_EXTS:
            raise HTTPException(status_code=400, detail=f"Định dạng tệp '{ext}' không được phép. Chỉ chấp nhận video, hình ảnh hoặc âm thanh.")
        save_path = upload_dir / f"input_media_{idx:03d}{ext}"
        contents = await f.read()
        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"Tệp '{f.filename}' vượt quá dung lượng tối đa cho phép 500MB.")
        with open(save_path, "wb") as out_f:
            out_f.write(contents)
        saved_file_paths.append(str(save_path))

    # 3. Cấu hình studio
    bgm_path = data.get("bgm_path")
    if bgm_path:
        bgm_cand = Path(str(bgm_path))
        allowed_bgm_dirs = [
            Path(settings.BASE_DIR) / "assets",
            Path(settings.BASE_DIR) / "storage",
            Path(settings.OUTPUT_DIR),
            Path(settings.TEMP_DIR),
        ]
        if not bgm_cand.exists() or not bgm_cand.is_file() or not any(is_safe_path(bgm_cand, d) for d in allowed_bgm_dirs):
            bgm_path = None
        else:
            bgm_path = str(bgm_cand.resolve())

    raw_font = str(data.get("subtitle_font", "Arial") or "Arial")
    safe_font = "".join(c for c in raw_font if c.isalnum() or c in (" ", "-", "_")).strip() or "Arial"

    config = ManualStudioConfig(
        title=title,
        voice=data.get("voice", "en-US-AndrewNeural"),
        voice_rate=data.get("voice_rate", "+0%"),
        voice_pitch=data.get("voice_pitch", "+0Hz"),
        bgm_path=bgm_path,
        bgm_volume=float(data.get("bgm_volume", 0.12)),
        use_ducking=bool(data.get("use_ducking", True)),
        subtitle_enabled=bool(data.get("subtitle_enabled", True)),
        subtitle_font=safe_font,
        subtitle_font_size=int(data.get("subtitle_font_size", 40)),
        subtitle_margin_v=int(data.get("subtitle_margin_v", 210)),
        transition_type=data.get("transition_type", "fade"),
        transition_duration=float(data.get("transition_duration", 0.25)),
        use_sfx=False,
        timing_mode=str(data.get("timing_mode", "auto"))
    )

    # 4. Đưa vào luồng render ngầm
    if mode == "single_video":
        primary_video_path = saved_file_paths[0]
        background_tasks.add_task(
            _execute_manual_render,
            job_id,
            "single_video",
            primary_video_path,
            script_text,
            [],
            config
        )
    else:
        # Chế độ nhiều scene
        all_narrations_empty = all(not (s.get("narration") or "").strip() for s in scenes_spec)
        if all_narrations_empty and script_text:
            from app.services.manual_studio_service import ManualStudioService
            parsed_parts = ManualStudioService().parse_multi_scene_script(script_text, len(scenes_spec), data.get("timing_mode", "auto"))
            for idx, part in enumerate(parsed_parts):
                if idx < len(scenes_spec):
                    scenes_spec[idx]["narration"] = part.get("narration", "")
                    if part.get("duration"):
                        scenes_spec[idx]["manual_duration_seconds"] = part["duration"]

        manual_scenes = []
        for i, sc in enumerate(scenes_spec):
            file_idx = sc.get("file_index", i)
            if file_idx >= len(saved_file_paths):
                file_idx = 0
            media_path = saved_file_paths[file_idx]

            manual_scenes.append(ManualSceneItem(
                scene_id=i + 1,
                media_path=media_path,
                narration=sc.get("narration", "").strip(),
                camera_motion=sc.get("camera_motion", "zoom_in"),
                transition=sc.get("transition", "fade"),
                sfx_cue=sc.get("sfx_cue", "auto"),
                manual_duration_seconds=float(sc.get("manual_duration_seconds")) if sc.get("manual_duration_seconds") else None
            ))
        background_tasks.add_task(
            _execute_manual_render,
            job_id,
            "multi_scene",
            None,
            script_text,
            manual_scenes,
            config
        )

    return {
        "success": True,
        "job_id": job_id,
        "message": f"Tác vụ #{job_id} đã được khởi tạo! Hệ thống đang lồng giọng đọc AI và phụ đề trong nền..."
    }

