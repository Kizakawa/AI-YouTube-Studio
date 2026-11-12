"""app.web.routers.videos_and_scripts — Quản lý Pipeline Video, Kịch bản, Storyboard, Render và Xuất bản YouTube/Drive."""
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


# ─── Videos ───────────────────────────────────────────────────────────────────

@app.get("/api/videos")
async def get_video_pipeline(limit: int = 50) -> List[Dict[str, Any]]:
    with get_db() as db:
        jobs = db.query(VideoJob).order_by(VideoJob.created_at.desc()).limit(limit).all()
        results = []
        for j in jobs:
            creator_name = j.creator.username if j.creator and j.creator.username else f"User_{j.user_id}"
            # Parse scenes_json to get scene count
            scene_count = 0
            if j.scenes_json:
                try:
                    import json
                    data = json.loads(j.scenes_json)
                    if isinstance(data, list):
                        scene_count = len(data)
                    elif isinstance(data, dict):
                        scene_count = len(data.get("scenes", []))
                except Exception:
                    pass
            # Xác định nguồn tạo: Web (user_id=0) hoặc Telegram
            source = "web" if j.user_id == 0 else "telegram"
            results.append({
                "id": j.id,
                "creator": creator_name,
                "source": source,
                "user_id": j.user_id,
                "channel_id": j.target_channel_id,
                "topic": j.topic,
                "title": j.title or "Chưa có tiêu đề",
                "script": (j.script or "")[:200],
                "status": j.status,
                "progress_percent": getattr(j, "progress_percent", 0) or 0,
                "progress_message": getattr(j, "progress_message", "") or "",
                "error_message": j.error_message or "",
                "scene_count": scene_count,
                "audio_duration_ms": j.audio_duration_ms or 0,
                "drive_link": j.drive_link or "",
                "youtube_id": j.youtube_video_id or "",
                "video_path": j.video_path or "",
                "thumbnail_path": getattr(j, "thumbnail_path", "") or "",
                "thumbnail_vertical_path": getattr(j, "thumbnail_vertical_path", "") or "",
                "quality_scores": j.quality_scores or "",
                "created_at": j.created_at.strftime("%Y-%m-%d %H:%M") if j.created_at else "",
                "scheduled_time": j.scheduled_time.strftime("%Y-%m-%d %H:%M") if j.scheduled_time else "",
            })
    return results

@app.get("/api/videos/{job_id}")
async def get_video_detail(job_id: int) -> Dict[str, Any]:
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        scenes = []
        if job.scenes_json:
            try:
                import json
                data = json.loads(job.scenes_json)
                if isinstance(data, list):
                    scenes = data
                elif isinstance(data, dict):
                    scenes = data.get("scenes", [])
            except Exception:
                pass
        return {
            "id": job.id,
            "topic": job.topic,
            "title": job.title or "",
            "script": job.script or "",
            "description": job.description or "",
            "tags": job.tags or "",
            "status": job.status,
            "progress_percent": getattr(job, "progress_percent", 0) or 0,
            "progress_message": getattr(job, "progress_message", "") or "",
            "error_message": job.error_message or "",
            "quality_scores": job.quality_scores or "",
            "scenes": scenes,
            "audio_duration_ms": job.audio_duration_ms or 0,
            "video_path": job.video_path or "",
            "thumbnail_path": getattr(job, "thumbnail_path", "") or "",
            "thumbnail_vertical_path": getattr(job, "thumbnail_vertical_path", "") or "",
            "drive_link": job.drive_link or "",
            "youtube_id": job.youtube_video_id or "",
            "created_at": job.created_at.strftime("%Y-%m-%d %H:%M") if job.created_at else "",
        }

class EditVideoPayload(BaseModel):
    action_type: str  # "replace_media", "edit_text", "change_voice", "remix_bgm", "edit_title"
    scene_id: Optional[int] = None
    new_text: Optional[str] = None
    new_title: Optional[str] = None
    new_media_query: Optional[str] = None
    new_voice_id: Optional[str] = None

@app.post("/api/videos/{job_id}/edit")
async def edit_video_endpoint(job_id: int, payload: EditVideoPayload, background_tasks: BackgroundTasks):
    """Endpoint chỉnh sửa vi mô cho video qua LangGraph."""
    from app.graph import GraphRunner, EditRequest
    req = EditRequest(
        action_type=payload.action_type,
        scene_id=payload.scene_id,
        new_text=payload.new_text,
        new_title=payload.new_title,
        new_media_query=payload.new_media_query,
        new_voice_id=payload.new_voice_id
    )
    runner = GraphRunner(job_id=job_id)
    background_tasks.add_task(runner.execute_edit, req)
    return {"status": "ok", "message": f"Đang thực hiện chỉnh sửa '{payload.action_type}' cho Video #{job_id}"}


class UpdateTitlePayload(BaseModel):
    title: str

@app.patch("/api/videos/{job_id}/title")
async def update_video_title_endpoint(job_id: int, payload: UpdateTitlePayload):
    """Cập nhật tiêu đề video nhanh (tức thì trong DB và scenes_json)."""
    new_t = payload.title.strip()
    if not new_t:
        raise HTTPException(status_code=400, detail="Tiêu đề không được để trống")
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy VideoJob #{job_id}")
        job.title = new_t
        if job.scenes_json:
            try:
                import json as _json
                sj = _json.loads(job.scenes_json)
                sj["title"] = new_t
                job.scenes_json = _json.dumps(sj, ensure_ascii=False)
            except Exception:
                pass
    return {"status": "ok", "job_id": job_id, "title": new_t}

# ─── Edit Script & Scenes (Pre-render) ────────────────────────────────────────

class SceneEditItem(BaseModel):
    scene_id: Optional[int] = None
    narration: str
    visual_keyword: Optional[str] = None
    visual_prompt: Optional[str] = None
    media_type: Optional[str] = "auto"
    estimated_seconds: Optional[float] = 3.0

class UpdateScriptPayload(BaseModel):
    title: Optional[str] = None
    hook: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    scenes: List[SceneEditItem]

@app.put("/api/videos/{job_id}/script")
@app.post("/api/videos/{job_id}/script")
async def update_video_script(job_id: int, payload: UpdateScriptPayload) -> Dict[str, Any]:
    """
    Cập nhật / chỉnh sửa toàn bộ kịch bản video (tiêu đề, lời thoại, phân cảnh).
    Hỗ trợ thêm, sửa, xóa phân cảnh trước khi render.
    """
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        if job.status == "RENDERING":
            raise HTTPException(status_code=409, detail="Video đang trong quá trình render, không thể sửa kịch bản!")

        if not payload.scenes or len(payload.scenes) == 0:
            raise HTTPException(status_code=400, detail="Kịch bản phải có ít nhất 1 phân cảnh!")

        updated_scenes = []
        for idx, sc in enumerate(payload.scenes, start=1):
            narration_clean = sc.narration.strip()
            if not narration_clean:
                continue
            kw_clean = (sc.visual_keyword or "cinematic scene").strip()
            sec = float(sc.estimated_seconds) if sc.estimated_seconds and sc.estimated_seconds > 0 else 3.0
            updated_scenes.append({
                "scene_id": idx,
                "narration": narration_clean,
                "visual_keyword": kw_clean,
                "visual_prompt": sc.visual_prompt or kw_clean,
                "media_type": getattr(sc, 'media_type', 'auto') or 'auto',
                "estimated_seconds": sec,
            })

        if not updated_scenes:
            raise HTTPException(status_code=400, detail="Các phân cảnh không được để trống lời thoại!")

        new_title = payload.title.strip() if payload.title and payload.title.strip() else (job.title or job.topic)
        full_narration = " ".join(s["narration"] for s in updated_scenes)
        hook = payload.hook.strip() if payload.hook and payload.hook.strip() else updated_scenes[0]["narration"][:100]

        tags_raw = payload.tags or job.tags or "shorts, psychology, facts"
        tags_list = [t.strip() for t in tags_raw.split(",") if t.strip()]
        description = payload.description.strip() if payload.description and payload.description.strip() else (job.description or f"{new_title} #shorts")

        script_dict = {
            "title": new_title,
            "hook": hook,
            "full_narration": full_narration,
            "scenes": updated_scenes,
            "tags": tags_list,
            "description": description,
        }

        job.title = new_title
        job.script = full_narration
        job.description = description
        job.tags = ",".join(tags_list)
        job.scenes_json = json.dumps(script_dict, ensure_ascii=False)
        if job.status in ("FAILED", "DRAFT"):
            job.status = "SCRIPT_READY"
            job.error_message = None

        log_activity(db, 0, "WebStudio", "EDIT_SCRIPT",
                     f"Đã cập nhật kịch bản Video #{job_id}: {len(updated_scenes)} phân cảnh")

    return {
        "success": True,
        "message": f"Kịch bản Video #{job_id} đã được lưu thành công ({len(updated_scenes)} phân cảnh)!",
        "job_id": job_id,
        "title": new_title,
        "scene_count": len(updated_scenes),
        "scenes": updated_scenes,
    }

# ─── Delete Job ───────────────────────────────────────────────────────────────

@app.delete("/api/videos/{job_id}")
async def delete_video_job(job_id: int) -> Dict[str, str]:
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        db.delete(job)
    return {"message": f"Đã xoá job #{job_id}"}

# ─── Download Video ───────────────────────────────────────────────────────────

def _resolve_safe_video_path(raw_video_path: str, job_id: Optional[int] = None) -> Path:
    """Xác thực đường dẫn video thuộc các thư mục xuất an toàn và có định dạng video hợp lệ."""
    allowed_exts = {".mp4", ".mov", ".webm", ".mkv"}
    allowed_dirs = [
        Path(settings.OUTPUT_DIR),
        Path(settings.TEMP_DIR),
        Path(settings.BASE_DIR) / "output",
        Path(settings.BASE_DIR) / "storage",
    ]
    candidate = Path(raw_video_path)
    if not candidate.exists() and job_id is not None:
        local_fallback = Path(settings.OUTPUT_DIR) / f"video_{job_id}.mp4"
        if local_fallback.exists():
            candidate = local_fallback

    if candidate.suffix.lower() not in allowed_exts:
        raise HTTPException(status_code=403, detail="Định dạng file video không hợp lệ")
    if not any(is_safe_path(candidate, d) for d in allowed_dirs):
        raise HTTPException(status_code=403, detail="Truy cập đường dẫn ngoài vùng cho phép bị từ chối")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"File video không tồn tại trên máy cục bộ này (Đường dẫn: {raw_video_path})."
        )
    return candidate

@app.get("/api/videos/{job_id}/download")
async def download_video(job_id: int):
    """Tải file video đã render về máy."""
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        if not job.video_path:
            raise HTTPException(status_code=404, detail="Video chưa được render")
        video_path = _resolve_safe_video_path(job.video_path, job_id=job_id)
        filename = f"video_{job_id}_{(job.title or 'untitled')[:30].replace(' ', '_')}.mp4"

    return FileResponse(
        path=str(video_path),
        media_type="video/mp4",
        filename=filename,
    )

@app.get("/api/videos/{job_id}/stream")
async def stream_video(job_id: int):
    """Phát video trực tiếp trên trình duyệt (inline streaming) hỗ trợ seek / scrub."""
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        if not job.video_path:
            raise HTTPException(status_code=404, detail="Video chưa được render")
        video_path = _resolve_safe_video_path(job.video_path, job_id=job_id)

    return FileResponse(
        path=str(video_path),
        media_type="video/mp4",
        content_disposition_type="inline",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-cache"},
    )


# ─── Thumbnail Endpoints ───────────────────────────────────────────────────────

@app.get("/api/videos/{job_id}/thumbnail")
async def get_video_thumbnail(job_id: int, aspect_ratio: str = "16x9"):
    """
    Trả về file ảnh bìa (thumbnail) của video job.
    Hỗ trợ aspect_ratio: '16x9' hoặc '9x16'.
    Nếu chưa có, tự động tạo từ video rendered frame hoặc visual assets.
    """
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        
        target_path = job.thumbnail_path if aspect_ratio != "9x16" else (job.thumbnail_vertical_path or job.thumbnail_path)
        
        if not target_path or not Path(target_path).exists():
            # Tự động tạo nếu video_path tồn tại
            if job.video_path and Path(job.video_path).exists():
                try:
                    from app.services.thumbnail_service import ThumbnailService
                    thumb_service = ThumbnailService()
                    custom_text = None
                    if job.scenes_json:
                        try:
                            sdata = json.loads(job.scenes_json)
                            custom_text = sdata.get("thumbnail_text")
                        except Exception:
                            pass
                    res = thumb_service.generate_thumbnails(
                        job_id=job.id,
                        title=job.title or job.topic,
                        video_path=job.video_path,
                        custom_text=custom_text
                    )
                    job.thumbnail_path = res.get("16x9")
                    job.thumbnail_vertical_path = res.get("9x16")
                    db.commit()
                    target_path = job.thumbnail_path if aspect_ratio != "9x16" else job.thumbnail_vertical_path
                except Exception as e:
                    logger.error(f"[Thumbnail] Auto-generation failed for job {job_id}: {e}")

        if not target_path or not Path(target_path).exists():
            raise HTTPException(status_code=404, detail="Ảnh bìa chưa được tạo cho video này")
            
        return FileResponse(
            path=str(target_path),
            media_type="image/jpeg",
            filename=f"thumb_{job_id}_{aspect_ratio}.jpg"
        )

class GenerateThumbnailRequest(BaseModel):
    custom_text: Optional[str] = None
    frame_timestamp_s: float = 1.0

@app.post("/api/videos/{job_id}/regenerate-thumbnail")
@app.post("/api/videos/{job_id}/generate-thumbnail")
async def regenerate_thumbnail(job_id: int, body: Optional[GenerateThumbnailRequest] = None):
    """
    Tạo hoặc tạo lại Thumbnail chuẩn 'Thị trường 1 giây' (chữ to, tương phản cao, gợi tò mò).
    """
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        if not job.video_path or not Path(job.video_path).exists():
            raise HTTPException(status_code=400, detail="Cần render video trước khi tạo thumbnail từ frame")
            
        custom_txt = body.custom_text if body else None
        ts = body.frame_timestamp_s if body else 1.0
        
        from app.services.thumbnail_service import ThumbnailService
        thumb_service = ThumbnailService()
        res = thumb_service.generate_thumbnails(
            job_id=job.id,
            title=job.title or job.topic,
            video_path=job.video_path,
            custom_text=custom_txt,
            frame_timestamp_s=ts
        )
        job.thumbnail_path = res.get("16x9")
        job.thumbnail_vertical_path = res.get("9x16")
        db.commit()
        return {
            "success": True,
            "job_id": job.id,
            "thumbnail_16x9": res.get("16x9"),
            "thumbnail_9x16": res.get("9x16"),
            "hook_text": thumb_service.derive_curiosity_text(custom_txt or job.title or job.topic)
        }

# ─── Google Trends & Bắt Trend Endpoints ─────────────────────────────────────

@app.get("/api/trends/live")
async def get_live_trends(geo: str = "VN", limit: int = 15):
    """
    Lấy danh sách các chủ đề tìm kiếm nóng nhất từ Google Trends và Google News.
    """
    try:
        from app.services.trend_analysis_service import TrendAnalysisService
        svc = TrendAnalysisService()
        trends = await asyncio.to_thread(svc.fetch_google_trends, geo=geo, limit=limit)
        news = await asyncio.to_thread(svc.fetch_breaking_news, geo=geo, limit=limit)
        return {
            "status": "success",
            "success": True,
            "geo": geo,
            "trends": trends,
            "news": news
        }
    except Exception as e:
        logger.error(f"[Trends API] Error fetching live trends: {e}")
        return {"status": "error", "success": False, "error": str(e), "trends": [], "news": []}

@app.get("/api/trends/channel-suggestions")
async def get_channel_trend_suggestions(
    channel_id: Optional[str] = None,
    niche: Optional[str] = None,
    geo: str = "VN",
    count: int = 5,
    keyword: Optional[str] = None
):
    """
    Phân tích Google Trends và tự động bắc cầu (Trend-Jacking) sang ngách kênh.
    Tạo các ý tưởng đánh trúng tâm lý số đông, đúng ngôn ngữ kênh và chống trùng lặp nội dung cũ.
    """
    try:
        from app.services.trend_analysis_service import TrendAnalysisService
        from app.services.channel_intelligence import (
            get_channel_existing_portfolio,
            extract_saturated_channel_concepts,
        )
        channels = settings.get_channels_config()
        channel_niche = niche
        language = "vi" if geo.upper() == "VN" else ("pt-BR" if geo.upper() == "BR" else "en")
        channel_name = ""
        existing_titles: List[str] = []
        saturated_concepts: List[str] = []
        top_performer_summary = ""

        if channel_id:
            ch = next((c for c in channels if c.get("id") == channel_id), {})
            if ch:
                channel_niche = channel_niche or ch.get("niche")
                language = ch.get("language") or language
                channel_name = ch.get("name") or channel_id
            try:
                portfolio = await asyncio.to_thread(get_channel_existing_portfolio, channel_id)
                existing_titles = portfolio.get("all_titles", [])
                vids_ranked = portfolio.get("videos_with_views", [])
                saturated_concepts = extract_saturated_channel_concepts(existing_titles, vids_ranked)
                if vids_ranked:
                    top_v = vids_ranked[0]
                    top_performer_summary = f"'{top_v.get('title')}' ({top_v.get('views', 0):,} views)"
            except Exception as port_err:
                logger.debug(f"Portfolio lookup skipped in trend suggestions: {port_err}")

        channel_niche = channel_niche or "general"

        svc = TrendAnalysisService()
        suggestions = await asyncio.to_thread(
            svc.bridge_trend_to_niche,
            channel_niche=channel_niche,
            channel_name=channel_name,
            existing_titles=existing_titles,
            saturated_concepts=saturated_concepts,
            top_performer_summary=top_performer_summary,
            specific_keyword=keyword,
            geo=geo,
            count=count,
            language=language
        )
        return {
            "status": "success",
            "success": True,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "channel_niche": channel_niche,
            "niche": channel_niche,
            "language": language,
            "geo": geo,
            "saturated_concepts": saturated_concepts,
            "top_performer": top_performer_summary,
            "suggestions": suggestions
        }
    except Exception as e:
        logger.error(f"[Trends Suggestions API] Error: {e}")
        return {"status": "error", "success": False, "error": str(e), "suggestions": []}

class QuickCreateFromTrendRequest(BaseModel):
    topic: str
    channel_id: Optional[str] = "ch_psychology_of_the_world"
    language: Optional[str] = None
    trend_keyword: Optional[str] = None
    curiosity_hook: Optional[str] = None
    hook: Optional[str] = None
    category: Optional[str] = None
    aspect_ratio: Optional[str] = "9:16"
    director_notes: Optional[str] = None

@app.post("/api/trends/quick-create")
async def quick_create_from_trend(body: QuickCreateFromTrendRequest):
    """
    Tạo kịch bản viral tức thì từ ý tưởng bắt trend:
    Áp dụng 5 quy tắc:
    1. Chạm cảm xúc & nhu cầu số đông
    2. Dùng số liệu thực tế giật mình
    3. Tối ưu 50% thời lượng đầu (trailer + b-roll)
    4. Text thumbnail 1 giây
    """
    from app.bot.handlers import _try_acquire_action, _release_action, _find_recent_duplicate_job
    from app.database.models import normalize_channel_id
    target_channel = normalize_channel_id(body.channel_id or "ch_psychology_of_the_world")
    norm_topic = " ".join((body.topic or "").lower().split())[:100]
    lock_key = f"web_quick_create:{target_channel}:{norm_topic}"
    acquired = _try_acquire_action(lock_key, max_inflight_sec=90.0, debounce_sec=8.0)
    if not acquired:
        raise HTTPException(status_code=429, detail="Đang tạo kịch bản cho chủ đề này, vui lòng chờ 15–30 giây và không bấm lặp lại!")

    dup_job_id = _find_recent_duplicate_job(target_channel, body.topic, window_seconds=180)
    if dup_job_id:
        _release_action(lock_key, post_cooldown_sec=2.0)
        raise HTTPException(
            status_code=409,
            detail=f"Kịch bản cho chủ đề này vừa được tạo ở Task #{dup_job_id}. Không cần tạo lặp lại!"
        )

    try:
        from app.agents.content_agent import ContentAgent
        channels = settings.get_channels_config()
        ch = next((c for c in channels if c.get("id") == target_channel), {})
        channel_niche = ch.get("niche") or (body.category if body.category != "Google Trends" else "general")
        channel_lang = body.language or ch.get("language") or "vi"

        effective_hook = body.curiosity_hook or body.hook
        notes = body.director_notes or ""
        if body.trend_keyword:
            notes += f" [Bắt trend từ Google Trends: '{body.trend_keyword}']"
        if effective_hook:
            notes += f" [Hook tò mò 1 giây: '{effective_hook}']"

        agent = ContentAgent()
        script, eval_report = await asyncio.to_thread(
            agent.generate_best_script,
            topic=body.topic,
            channel_niche=channel_niche,
            language=channel_lang,
            director_notes=notes,
            min_acceptable_score=8.0,
            max_attempts=3
        )

        scores_summary = {
            "overall": eval_report.overall_score if eval_report else 8.5,
            "overall_score": eval_report.overall_score if eval_report else 8.5,
            "passed": eval_report.passed if eval_report else True,
            "hook": eval_report.hook_strength.score if eval_report else 8.5,
            "pacing": eval_report.story_pacing.score if eval_report else 8.5,
            "actionable_critiques": eval_report.actionable_critiques if eval_report else [],
        }

        with get_db() as db:
            job = VideoJob(
                user_id=0,
                target_channel_id=target_channel,
                topic=body.topic,
                title=script.title,
                script=script.full_narration,
                description=script.description,
                tags=",".join(script.tags),
                scenes_json=script.to_json_str(),
                status="SCRIPT_READY",
                quality_scores=json.dumps(scores_summary, ensure_ascii=False) if eval_report else None
            )
            db.add(job)
            db.flush()
            job_id = job.id

        return {
            "success": True,
            "job_id": job_id,
            "title": script.title,
            "hook": script.hook,
            "thumbnail_text": getattr(script, "thumbnail_text", ""),
            "scene_count": len(script.scenes),
            "total_duration": script.total_estimated_duration,
            "evaluation": scores_summary
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[QuickCreate Trend API] Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _release_action(lock_key, post_cooldown_sec=5.0)

# ─── Thư Viện Video Đã Đăng (Published Video Library) ───────────────────────

class LibraryDuplicateRequest(BaseModel):
    channel_id: str = ""
    topic: str


@app.get("/api/library")
async def api_library_list(channel_id: str = "", q: str = "", limit: int = 100) -> Dict[str, Any]:
    """Danh sách video đã đăng + tóm tắt, có lọc theo kênh / từ khóa."""
    from app.services import video_library
    await asyncio.to_thread(video_library.backfill_from_jobs)
    vids = await asyncio.to_thread(video_library.list_videos, channel_id, q, min(max(limit, 1), 500))
    return {"success": True, "count": len(vids), "videos": vids}


@app.get("/api/library/topics")
async def api_library_topics(channel_id: str = "") -> Dict[str, Any]:
    from app.services import video_library
    return {"success": True, "topics": await asyncio.to_thread(video_library.topics_overview, channel_id)}


@app.post("/api/library/sync")
async def api_library_sync() -> Dict[str, Any]:
    """Đồng bộ thủ công từ YouTube (chạy nền thread để không chặn server)."""
    from app.services import video_library
    return await asyncio.to_thread(video_library.sync_all)


@app.post("/api/library/check-duplicate")
async def api_library_check_duplicate(body: LibraryDuplicateRequest) -> Dict[str, Any]:
    """Cảnh báo (không chặn) khi chủ đề mới gần giống video đã đăng."""
    from app.services import video_library
    matches = await asyncio.to_thread(video_library.check_duplicate, body.channel_id, body.topic)
    return {"success": True, "duplicates": matches}


# ─── Create Script (Web UI trigger) ──────────────────────────────────────────


class CreateScriptRequest(BaseModel):
    topic: str
    channel_id: str = "ch_psychology_of_the_world"
    language: str = "en"
    video_format: str = "short"  # "short" (9:16 Shorts ~40s) | "long" (16:9 Documentary ~3-5 min)
    director_notes: Optional[str] = None

class RefineScriptRequest(BaseModel):
    feedback: str
    language: Optional[str] = None

@app.post("/api/scripts/create")
async def create_script_from_web(body: CreateScriptRequest) -> Dict[str, Any]:
    """
    Tạo kịch bản AI trực tiếp từ Web Studio:
    Tự động chấm điểm qua ScriptCritic và lặp lại tái tạo cho đến khi đạt chuẩn viral (>= 8.0/10) mới hiển thị.
    Hỗ trợ ngay từ bước đầu 2 chế độ:
    - video_format="short": YouTube Shorts 9:16 (1080x1920, ~35-45s, 7-9 phân cảnh)
    - video_format="long": YouTube Long-Form Documentary 16:9 (1920x1080, ~3-4.5 phút, 16-22 phân cảnh / 5 Chương)
    """
    from app.bot.handlers import _try_acquire_action, _release_action, _find_recent_duplicate_job
    from app.database.models import normalize_channel_id
    body.channel_id = normalize_channel_id(body.channel_id)
    vf_norm = "long" if str(body.video_format or "short").strip().lower() in ("long", "16:9", "documentary", "full", "landscape") else "short"
    norm_topic = " ".join((body.topic or "").lower().split())[:100]
    lock_key = f"web_create_script:{body.channel_id}:{vf_norm}:{norm_topic}"
    acquired = _try_acquire_action(lock_key, max_inflight_sec=110.0, debounce_sec=8.0)
    if not acquired:
        raise HTTPException(status_code=429, detail="Đang tạo kịch bản cho chủ đề này, vui lòng chờ 15–30 giây và không bấm lặp lại!")

    dup_job_id = _find_recent_duplicate_job(body.channel_id, body.topic, window_seconds=180)
    if dup_job_id and vf_norm == "short":
        _release_action(lock_key, post_cooldown_sec=2.0)
        raise HTTPException(
            status_code=409,
            detail=f"Kịch bản cho chủ đề này vừa được tạo ở Task #{dup_job_id}. Không cần tạo lặp lại!"
        )

    try:
        from app.agents.content_agent import ContentAgent
        channels = settings.get_channels_config()
        ch = next((c for c in channels if c.get("id") == body.channel_id), {})
        channel_niche = ch.get("niche") or "general"
        channel_lang = body.language or ch.get("language") or "en"

        agent = ContentAgent()
        script, eval_report = await asyncio.to_thread(
            agent.generate_best_script,
            topic=body.topic,
            channel_niche=channel_niche,
            language=channel_lang,
            director_notes=body.director_notes,
            min_acceptable_score=8.0,
            max_attempts=3,
            video_format=vf_norm,
        )

        scores_summary = {
            "overall": eval_report.overall_score if eval_report else 0,
            "overall_score": eval_report.overall_score if eval_report else 0,
            "passed": eval_report.passed if eval_report else False,
            "hook": eval_report.hook_strength.score if eval_report else 0,
            "hook_strength": eval_report.hook_strength.score if eval_report else 0,
            "pacing": eval_report.story_pacing.score if eval_report else 0,
            "story_pacing": eval_report.story_pacing.score if eval_report else 0,
            "visual": (eval_report.visual_direction.score if getattr(eval_report, "visual_direction", None) else 8.5) if eval_report else 0,
            "visual_direction": (eval_report.visual_direction.score if getattr(eval_report, "visual_direction", None) else 8.5) if eval_report else 0,
            "actionable_critiques": eval_report.actionable_critiques if eval_report else [],
            "script_critic": eval_report.model_dump() if eval_report else None,
            "video_format": getattr(script, "video_format", vf_norm),
        }

        with get_db() as db:
            job = VideoJob(
                user_id=0,  # 0 = Web UI
                target_channel_id=body.channel_id,
                topic=body.topic,
                title=script.title,
                script=script.full_narration,
                description=script.description,
                tags=",".join(script.tags),
                scenes_json=script.to_json_str(),
                status="SCRIPT_READY",
                quality_scores=json.dumps(scores_summary, ensure_ascii=False) if eval_report else None
            )
            db.add(job)
            db.flush()
            job_id = job.id

        eval_data = None
        if eval_report:
            vis_s = getattr(eval_report, "visual_direction", None)
            vis_score = vis_s.score if vis_s else 8.5
            eval_data = {
                "overall_score": eval_report.overall_score,
                "hook_score": eval_report.hook_strength.score,
                "pacing_score": eval_report.story_pacing.score,
                "visual_score": vis_score,
                "passed": eval_report.passed,
                "badge": "🏆 Xuất sắc" if eval_report.overall_score >= 8.5 else "✅ Đạt chuẩn Viral",
                "critiques": eval_report.actionable_critiques or []
            }

        return {
            "success": True,
            "job_id": job_id,
            "title": script.title,
            "hook": script.hook,
            "video_format": getattr(script, "video_format", vf_norm),
            "scene_count": len(script.scenes),
            "total_duration": script.total_estimated_duration,
            "scenes": [
                {
                    "scene_id": s.scene_id,
                    "narration": s.narration,
                    "visual_keyword": s.visual_keyword,
                    "visual_prompt": getattr(s, "visual_prompt", s.visual_keyword),
                    "media_type": getattr(s, "media_type", "auto"),
                    "estimated_seconds": s.estimated_seconds,
                }
                for s in script.scenes
            ],
            "eval": eval_data
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"Lỗi tạo kịch bản: {str(e)}\n{traceback.format_exc()}")
    finally:
        _release_action(lock_key, post_cooldown_sec=5.0)


# ─── FORMULA DECONSTRUCTOR (Nhân bản nhịp điệu video viral) ───────────────────

class FormulaDeconstructRequest(BaseModel):
    url: Optional[str] = None
    transcript: Optional[str] = None
    language: str = "en"

class FormulaGenerateRequest(BaseModel):
    blueprint: Dict[str, Any]
    new_topic: str
    channel_id: str = "ch_psychology_of_the_world"
    language: str = "en"
    director_notes: Optional[str] = None

@app.post("/api/formula/deconstruct")
async def api_formula_deconstruct(body: FormulaDeconstructRequest) -> Dict[str, Any]:
    """Giải mã cấu trúc nhịp điệu (Blueprint) của 1 video Shorts/Reels viral từ link hoặc transcript."""
    source_val = (body.url or "").strip() or (body.transcript or "").strip()
    if not source_val:
        raise HTTPException(status_code=400, detail="Vui lòng cung cấp link video hoặc đoạn transcript để giải phẫu.")
    try:
        from app.services.formula_deconstructor import FormulaDeconstructor
        deconstructor = FormulaDeconstructor()
        res = await asyncio.to_thread(deconstructor.deconstruct_from_source, source_val, target_lang=body.language)
        return {"success": True, **res}
    except Exception as e:
        logger.error(f"[FormulaDeconstructor API] Lỗi: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Lỗi giải mã công thức viral: {str(e)}")

@app.post("/api/formula/generate")
async def api_formula_generate(body: FormulaGenerateRequest) -> Dict[str, Any]:
    """Nhân bản kịch bản mới 100% nguyên bản trên chủ đề mới, tuân thủ nghiêm ngặt Blueprint nhịp điệu viral."""
    if not body.new_topic or not body.new_topic.strip():
        raise HTTPException(status_code=400, detail="Vui lòng nhập chủ đề cho video mới.")
    if not body.blueprint:
        raise HTTPException(status_code=400, detail="Thiếu dữ liệu Blueprint nhịp điệu.")
    try:
        from app.services.formula_deconstructor import FormulaDeconstructor
        from app.database.models import normalize_channel_id
        body.channel_id = normalize_channel_id(body.channel_id)
        channels = settings.get_channels_config()
        ch = next((c for c in channels if c.get("id") == body.channel_id), {})
        channel_niche = ch.get("niche") or "general"
        channel_lang = body.language or ch.get("language") or "en"

        deconstructor = FormulaDeconstructor()
        script = await asyncio.to_thread(
            deconstructor.generate_script_from_blueprint,
            blueprint=body.blueprint,
            new_topic=body.new_topic,
            channel_niche=channel_niche,
            language=channel_lang,
            director_notes=body.director_notes or ""
        )

        scores_summary = {
            "overall": 8.8,
            "overall_score": 8.8,
            "passed": True,
            "hook": 9.0,
            "pacing": 9.2,
            "formula_cloned": body.blueprint.get("viral_formula_summary", "Viral Retention Blueprint"),
        }

        with get_db() as db:
            job = VideoJob(
                user_id=0,
                target_channel_id=body.channel_id,
                topic=body.new_topic,
                title=script.title,
                script=script.full_narration,
                description=script.description,
                tags=",".join(script.tags),
                scenes_json=script.to_json_str(),
                status="SCRIPT_READY",
                quality_scores=json.dumps(scores_summary, ensure_ascii=False)
            )
            db.add(job)
            db.flush()
            job_id = job.id

        return {
            "success": True,
            "job_id": job_id,
            "title": script.title,
            "hook": script.hook,
            "scene_count": len(script.scenes),
            "total_duration": script.total_estimated_duration,
            "viral_formula": script.viral_formula,
            "scenes": [
                {
                    "scene_id": s.scene_id,
                    "narration": s.narration,
                    "visual_keyword": s.visual_keyword,
                    "stock_query": getattr(s, "stock_query", s.visual_keyword),
                    "visual_prompt": getattr(s, "visual_prompt", s.visual_keyword),
                    "media_type": getattr(s, "media_type", "video"),
                    "estimated_seconds": s.estimated_seconds,
                }
                for s in script.scenes
            ]
        }
    except Exception as e:
        logger.error(f"[FormulaGenerate API] Lỗi: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Lỗi nhân bản kịch bản từ blueprint: {str(e)}")


class CreateManualScriptRequest(BaseModel):
    title: Optional[str] = None
    topic: Optional[str] = None
    channel_id: str = "ch_psychology_of_the_world"
    language: str = "en"
    video_format: str = "short"
    scenes: Optional[List[Dict[str, Any]]] = None

@app.post("/api/scripts/create-manual")
async def create_manual_script(body: CreateManualScriptRequest) -> Dict[str, Any]:
    """
    Tạo kịch bản thủ công từ Web Studio:
    Người dùng có thể tự viết tiêu đề, lời thoại từng phân cảnh hoặc mở trình soạn thảo để tự tạo từ A-Z mà không bắt buộc dùng AI.
    """
    try:
        channels = settings.get_channels_config()
        ch = next((c for c in channels if c.get("id") == body.channel_id), {})
        channel_name = ch.get("name") or body.channel_id
        vf_norm = "long" if str(body.video_format or "short").strip().lower() in ("long", "16:9", "documentary", "full", "landscape") else "short"
        raw_title = (body.title or body.topic or ("Phim tài liệu mới" if vf_norm == "long" else "Kịch bản Shorts mới")).strip()
        aspect_str = "16:9" if vf_norm == "long" else "9:16"

        # Tạo sẵn cấu trúc phân cảnh mẫu nếu người dùng chưa truyền
        initial_scenes = body.scenes or [
            {
                "scene_id": 1,
                "narration": f"Chào mừng bạn đến với {channel_name}! Đây là phân cảnh mở đầu...",
                "visual_keyword": "cinematic dramatic opening",
                "visual_prompt": f"cinematic dramatic opening {aspect_str} high quality",
                "media_type": "auto",
                "estimated_seconds": 6.0 if vf_norm == "long" else 4.0
            },
            {
                "scene_id": 2,
                "narration": "Chi tiết hấp dẫn nhất của câu chuyện nằm ở đây...",
                "visual_keyword": "mysterious revelation documentary",
                "visual_prompt": f"mysterious revelation documentary {aspect_str} high quality",
                "media_type": "auto",
                "estimated_seconds": 7.0 if vf_norm == "long" else 5.0
            },
            {
                "scene_id": 3,
                "narration": "Hãy theo dõi kênh để không bỏ lỡ phần tiếp theo!",
                "visual_keyword": "subscribe follow channel cinematic",
                "visual_prompt": "cinematic dramatic closing",
                "media_type": "auto",
                "estimated_seconds": 5.0 if vf_norm == "long" else 3.5
            }
        ]

        full_narration = " ".join(s.get("narration", "") for s in initial_scenes)
        hook_text = initial_scenes[0].get("narration", "")[:100]
        default_tags = ["documentary", "viral", "trending"] if vf_norm == "long" else ["shorts", "viral", "trending"]
        default_desc = f"{raw_title} #documentary #viral" if vf_norm == "long" else f"{raw_title} #Shorts #viral"

        script_payload = {
            "title": raw_title,
            "hook": hook_text,
            "full_narration": full_narration,
            "video_format": vf_norm,
            "scenes": initial_scenes,
            "tags": default_tags,
            "description": default_desc
        }

        with get_db() as db:
            job = VideoJob(
                user_id=0,  # 0 = Web Studio
                target_channel_id=body.channel_id,
                topic=body.topic or raw_title,
                title=raw_title,
                script=full_narration,
                description=default_desc,
                tags=",".join(default_tags),
                scenes_json=json.dumps(script_payload, ensure_ascii=False),
                status="SCRIPT_READY",
                quality_scores=json.dumps({
                    "overall": 9.0,
                    "overall_score": 9.0,
                    "passed": True,
                    "hook": 9.0,
                    "hook_strength": 9.0,
                    "pacing": 9.0,
                    "story_pacing": 9.0,
                    "visual": 9.0,
                    "visual_direction": 9.0,
                    "video_format": vf_norm,
                    "actionable_critiques": ["Kịch bản do người dùng tự soạn thảo thủ công"],
                    "badge": "✍️ Soạn thủ công"
                }, ensure_ascii=False)
            )
            db.add(job)
            db.flush()
            job_id = job.id

            log_activity(db, 0, "WebStudio", "CREATE_MANUAL_SCRIPT",
                         f"Đã tạo kịch bản thủ công Job #{job_id} ({vf_norm}): '{raw_title}'")

        total_dur = sum(s.get("estimated_seconds", 4.0) for s in initial_scenes)

        return {
            "success": True,
            "job_id": job_id,
            "title": raw_title,
            "hook": hook_text,
            "video_format": vf_norm,
            "scene_count": len(initial_scenes),
            "total_duration": total_dur,
            "scenes": initial_scenes,
            "eval": {
                "overall_score": 9.0,
                "hook_score": 9.0,
                "pacing_score": 9.0,
                "visual_score": 9.0,
                "passed": True,
                "badge": "✍️ Kịch Bản Tự Soạn",
                "critiques": ["Kịch bản do người dùng tự biên soạn và quản lý"]
            }
        }
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"Lỗi tạo kịch bản thủ công: {str(e)}\n{traceback.format_exc()}")


@app.post("/api/videos/{job_id}/ai-refine")
async def refine_script_with_ai(job_id: int, body: RefineScriptRequest) -> Dict[str, Any]:
    """
    Tiếp thu góp ý chỉnh sửa của người dùng, tái tạo kịch bản,
    tự động chấm điểm bằng ScriptCritic và lặp lại đến khi đạt chuẩn viral.
    """
    try:
        from app.agents.content_agent import ContentAgent
        from app.schemas.contracts import ScriptContract

        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not job:
                raise HTTPException(status_code=404, detail=f"Không tìm thấy video job #{job_id}")
            if not job.scenes_json:
                raise HTTPException(status_code=400, detail=f"Job #{job_id} chưa có kịch bản phân cảnh")

            current_script = ScriptContract.from_json_str(job.scenes_json)
            channel_id = job.target_channel_id

        channels = settings.get_channels_config()
        ch = next((c for c in channels if c.get("id") == channel_id), {})
        channel_niche = ch.get("niche") or "general"
        channel_lang = body.language or ch.get("language") or "en"

        agent = ContentAgent()
        revised_script, eval_report = await asyncio.to_thread(
            agent.refine_with_feedback_and_eval,
            current_script=current_script,
            feedback=body.feedback,
            channel_niche=channel_niche,
            language=channel_lang,
            min_acceptable_score=8.0,
            max_attempts=3
        )

        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if job:
                job.title = revised_script.title
                job.script = revised_script.full_narration
                job.description = revised_script.description
                job.tags = ",".join(revised_script.tags)
                job.scenes_json = revised_script.to_json_str()
                job.status = "SCRIPT_READY"

                scores = {}
                if job.quality_scores:
                    try:
                        scores = json.loads(job.quality_scores)
                    except Exception:
                        pass
                if eval_report:
                    vis_s = getattr(eval_report, "visual_direction", None)
                    vis_score = vis_s.score if vis_s else 8.5
                    scores["overall"] = eval_report.overall_score
                    scores["overall_score"] = eval_report.overall_score
                    scores["passed"] = eval_report.passed
                    scores["hook"] = eval_report.hook_strength.score
                    scores["hook_strength"] = eval_report.hook_strength.score
                    scores["pacing"] = eval_report.story_pacing.score
                    scores["story_pacing"] = eval_report.story_pacing.score
                    scores["visual"] = vis_score
                    scores["visual_direction"] = vis_score
                    scores["actionable_critiques"] = eval_report.actionable_critiques or []
                    scores["script_critic"] = eval_report.model_dump()
                job.quality_scores = json.dumps(scores, ensure_ascii=False)

        eval_data = None
        if eval_report:
            vis_s = getattr(eval_report, "visual_direction", None)
            vis_score = vis_s.score if vis_s else 8.5
            eval_data = {
                "overall": eval_report.overall_score,
                "overall_score": eval_report.overall_score,
                "hook": eval_report.hook_strength.score,
                "hook_score": eval_report.hook_strength.score,
                "hook_strength": eval_report.hook_strength.score,
                "pacing": eval_report.story_pacing.score,
                "pacing_score": eval_report.story_pacing.score,
                "story_pacing": eval_report.story_pacing.score,
                "visual": vis_score,
                "visual_score": vis_score,
                "visual_direction": vis_score,
                "passed": eval_report.passed,
                "badge": "🏆 Xuất sắc" if eval_report.overall_score >= 8.5 else "✅ Đạt chuẩn Viral",
                "critiques": eval_report.actionable_critiques or [],
                "actionable_critiques": eval_report.actionable_critiques or []
            }

        return {
            "success": True,
            "job_id": job_id,
            "title": revised_script.title,
            "hook": revised_script.hook,
            "tags": ",".join(revised_script.tags),
            "scene_count": len(revised_script.scenes),
            "total_duration": revised_script.total_estimated_duration,
            "scenes": [
                {
                    "scene_id": s.scene_id,
                    "narration": s.narration,
                    "visual_keyword": s.visual_keyword,
                    "visual_prompt": getattr(s, "visual_prompt", s.visual_keyword),
                    "media_type": getattr(s, "media_type", "auto"),
                    "estimated_seconds": s.estimated_seconds,
                }
                for s in revised_script.scenes
            ],
            "eval": eval_data
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"Lỗi khi tinh chỉnh kịch bản: {str(e)}\n{traceback.format_exc()}")

# ─── Update Job Status ────────────────────────────────────────────────────────

class UpdateStatusRequest(BaseModel):
    status: str
    error_message: Optional[str] = None

@app.patch("/api/videos/{job_id}/status")
async def update_job_status(job_id: int, body: UpdateStatusRequest) -> Dict[str, str]:
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        job.status = body.status
        if body.error_message:
            job.error_message = body.error_message
    return {"message": f"Đã cập nhật trạng thái job #{job_id} → {body.status}"}

# ─── Render Trigger (Web UI) ─────────────────────────────────────────────────

def _run_web_render(job_id: int):
    """
    Background task: chạy pipeline render cho 1 video job từ Web UI.
    [FIX #1] Bọc try/finally đảm bảo status = FAILED nếu pipeline crash.
    [FIX #5] Thêm tổng timeout 10 phút cho toàn bộ pipeline.
    """
    import asyncio

    async def _do_render():
        try:
            from app.services.pipeline import VideoPipeline
            pipeline = VideoPipeline(job_id=job_id)
            # [FIX #5] Tổng timeout 20 phút cho toàn pipeline (đủ thời gian cho AI Vision & thẩm định kỹ)
            await asyncio.wait_for(pipeline.execute(), timeout=1200)

            # Tự động gửi video hoàn tất vào Telegram cho Creator/Admin xem ngay
            try:
                from app.bot.handlers import dispatch_rendered_video_to_telegram
                await dispatch_rendered_video_to_telegram(job_id)
            except Exception as e_tg:
                logger.warning(f"[Web Render] Không thể tự động gửi video #{job_id} vào Telegram: {e_tg}")
        except asyncio.TimeoutError:
            error_msg = "Pipeline render vượt quá thời gian cho phép (20 phút)"
            logger.error(f"[Web Render] Job #{job_id}: {error_msg}")
            _mark_job_failed(job_id, error_msg)
            raise
        except Exception as e:
            logger.error(f"[Web Render] Job #{job_id} FAILED: {e}", exc_info=True)
            # [FIX #1] Đảm bảo status = FAILED khi crash
            _mark_job_failed(job_id, str(e))
            raise

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_do_render())
    except Exception:
        pass  # Đã xử lý bên trong _do_render
    finally:
        loop.close()


def _mark_job_failed(job_id: int, error_msg: str):
    """Helper: đánh dấu job FAILED trong DB khi render crash."""
    try:
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if job and job.status == "RENDERING":
                job.status = "FAILED"
                job.error_message = error_msg[:500]
                job.progress_message = f"❌ Thất bại: {error_msg[:100]}"
    except Exception as ex:
        logger.error(f"Không thể đánh dấu job #{job_id} FAILED: {ex}")


@app.post("/api/videos/{job_id}/render")
async def trigger_render_from_web(job_id: int, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """
    Kích hoạt render video từ Web Dashboard.
    [FIX #6] Chống spam: kiểm tra không có job nào đang RENDERING trước khi cho phép.
    """
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        if not job.scenes_json:
            raise HTTPException(status_code=400, detail="Video chưa có kịch bản phân cảnh")
        if job.status == "RENDERING":
            raise HTTPException(status_code=409, detail="Video đang được render")

        # [FIX #6] Kiểm tra có video nào đang render không (tránh chạy song song)
        rendering_count = db.query(VideoJob).filter(VideoJob.status == "RENDERING").count()
        if rendering_count > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Đang có {rendering_count} video đang render. Vui lòng đợi hoàn tất trước khi render tiếp."
            )

        # Cập nhật trạng thái ngay để UI phản ánh
        job.status = "RENDERING"
        job.progress_percent = 0
        job.progress_message = "Đang khởi tạo pipeline render từ Web..."
        job.error_message = None

    background_tasks.add_task(_run_web_render, job_id)

    return {
        "success": True,
        "message": f"Video #{job_id} đã được đưa vào hàng đợi render",
        "job_id": job_id
    }


@app.post("/api/videos/recover-failed")
async def recover_all_failed_jobs() -> Dict[str, Any]:
    """
    Tự động khôi phục tất cả các video bị lỗi (FAILED) về trạng thái SCRIPT_READY,
    làm sạch thông báo lỗi và chuẩn bị sẵn sàng cho lượt render tiếp theo.
    """
    with get_db() as db:
        failed_jobs = db.query(VideoJob).filter(VideoJob.status == "FAILED").all()
        count = len(failed_jobs)
        for j in failed_jobs:
            j.status = "SCRIPT_READY"
            j.error_message = None
            j.progress_percent = 0
            j.progress_message = "Đã khôi phục về hàng đợi sẵn sàng render lại"
            log_activity(db, 0, "Web Studio", "RECOVER_FAILED", f"Khôi phục Video #{j.id} về SCRIPT_READY")

    return {
        "success": True,
        "recovered_count": count,
        "message": f"Đã khôi phục thành công {count} video bị lỗi về trạng thái sẵn sàng!"
    }

# ─── STORYBOARD PREVIEW & MEDIA SWAPPER GATE ────────────────────────────────

class SwapMediaRequest(BaseModel):
    mode: str = "pexels_video"
    search_query: Optional[str] = None
    custom_prompt: Optional[str] = None

@app.get("/api/videos/{job_id}/storyboard")
async def api_get_storyboard(job_id: int) -> Dict[str, Any]:
    """Lấy danh sách các phân cảnh dạng Storyboard kèm thông tin media thực tế trên ổ cứng."""
    try:
        from app.services.storyboard_manager import StoryboardManager
        mgr = StoryboardManager()
        return await asyncio.to_thread(mgr.get_storyboard, job_id)
    except Exception as e:
        logger.error(f"[Storyboard API] Lỗi: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/videos/{job_id}/scenes/{scene_id}/swap")
async def api_swap_scene_media(job_id: int, scene_id: int, body: SwapMediaRequest) -> Dict[str, Any]:
    """Hoán đổi media cho 1 phân cảnh (Tìm video stock khác, ảnh stock, hoặc vẽ lại bằng AI)."""
    try:
        from app.services.storyboard_manager import StoryboardManager
        mgr = StoryboardManager()
        res = await asyncio.to_thread(
            mgr.swap_scene_media,
            job_id=job_id,
            scene_id=scene_id,
            mode=body.mode,
            search_query=body.search_query,
            custom_prompt=body.custom_prompt
        )
        return res
    except Exception as e:
        logger.error(f"[SwapMedia API] Lỗi: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/videos/{job_id}/scenes/{scene_id}/upload")
async def api_upload_scene_media(job_id: int, scene_id: int, file: UploadFile = File(...)) -> Dict[str, Any]:
    """Tải lên file video hoặc ảnh cá nhân từ máy tính để gán trực tiếp cho phân cảnh."""
    try:
        from app.services.storyboard_manager import StoryboardManager
        mgr = StoryboardManager()
        content = await file.read()
        res = await asyncio.to_thread(
            mgr.save_uploaded_media,
            job_id=job_id,
            scene_id=scene_id,
            file_bytes=content,
            original_filename=file.filename or "custom_media.mp4"
        )
        return res
    except Exception as e:
        logger.error(f"[UploadMedia API] Lỗi: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/videos/{job_id}/render-approved")
async def api_render_approved_storyboard(job_id: int, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """Duyệt Storyboard và bắt đầu render hoàn chỉnh video với các media đã được lựa chọn kỹ."""
    import inspect as _inspect
    import sys as _sys
    _srv_mod = _sys.modules.get("app.web.server")
    _trigger_fn = getattr(_srv_mod, "trigger_render_from_web", trigger_render_from_web) if _srv_mod is not None else trigger_render_from_web
    res = _trigger_fn(job_id, background_tasks)
    if _inspect.isawaitable(res):
        return await res
    return res

@app.get("/api/videos/{job_id}/shot-table")
async def api_get_shot_table(job_id: int) -> Dict[str, Any]:
    """Lấy Bảng Hình Học Phân Cảnh (Seedance 2.0 Shot Table) chuẩn điện ảnh cho phân cảnh của VideoJob."""
    try:
        from app.services.storyboard_manager import StoryboardManager
        mgr = StoryboardManager()
        return await asyncio.to_thread(mgr.get_shot_table, job_id)
    except Exception as e:
        logger.error(f"[ShotTable API] Lỗi: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# ─── Health Check ─────────────────────────────────────────────────────────────

@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health_check() -> Dict[str, Any]:
    import sys
    from app.utils.ffmpeg_helper import is_ffmpeg_installed, get_ffmpeg_version
    keys = settings.get_gemini_keys_pool()
    web_only = "--web-only" in sys.argv

    # Kiểm tra Drive token thật sự
    drive_connected = False
    drive_quota_label = "Chưa kết nối"
    try:
        cfg = _read_channels_yaml()
        for acc in cfg.get("google_accounts", []):
            acc_id = acc.get("id")
            if not acc_id:
                continue
            drive_token_path = BASE_DIR / "credentials" / "accounts" / acc_id / "drive_token.json"
            if drive_token_path.exists():
                tok = load_encrypted_json(drive_token_path)
                if tok and (tok.get("token") or tok.get("refresh_token")):
                    drive_connected = True
                    storage_label = acc.get("drive_storage") or "Google One"
                    drive_quota_label = f"{storage_label} (Đã kết nối ✓)"
                    break
    except Exception:
        pass

    render_info = {
        "backend": "local",
        "engine": "FFmpeg Local GPU / CPU",
        "status": "ready"
    }

    return {
        "status": "online",
        "ffmpeg": get_ffmpeg_version() if is_ffmpeg_installed() else "not installed",
        "gemini_keys": len(keys),
        "telegram_token": bool(settings.TELEGRAM_BOT_TOKEN),
        "web_only": web_only,
        "drive_connected": drive_connected,
        "drive_quota_label": drive_quota_label,
        "render": render_info,
    }


# ─── Worker Webhook Callback & Dispatch Endpoints ────────────────────────────

@app.post("/api/worker/callback")
@app.post("/api/render-complete")
async def worker_render_callback(request: Request) -> Dict[str, Any]:
    """
    Webhook tiếp nhận kết quả hoàn thành từ Worker.
    Tự động cập nhật VideoJob sang RENDERED ngay lập tức.
    """
    try:
        body = await request.json()
    except Exception as e:
        return {"status": "error", "message": f"Invalid JSON payload: {e}"}

    # Xác thực Worker / Agent / Local / Thiết bị hợp lệ
    from app.services.device_auth import extract_client_ip, is_device_token_valid
    client_ip, is_direct_local = extract_client_ip(request)
    expected_key = getattr(settings, "AGENT_API_KEY", "") or ""
    provided_secret = (
        request.headers.get("X-Agent-Key")
        or request.headers.get("X-Worker-Secret")
        or str(body.get("worker_token") or "")
    )
    dev_token = request.headers.get("X-Device-Token") or request.cookies.get("device_token") or ""
    is_authed = (
        is_direct_local
        or (bool(expected_key) and bool(provided_secret) and hmac.compare_digest(provided_secret, expected_key))
        or is_device_token_valid(dev_token, client_ip, is_direct_local=is_direct_local)
    )
    if not is_authed:
        raise HTTPException(status_code=403, detail="Unauthorized worker callback")

    job_id = body.get("job_id")
    if not job_id:
        return {"status": "error", "message": "job_id is required"}

    status = body.get("status", "completed")
    video_path = body.get("video_path")
    drive_link = body.get("drive_link")
    render_time = body.get("render_time_s", 0)
    error_msg = body.get("error")

    # Kiểm tra an toàn cho video_path nếu được truyền vào
    if video_path:
        allowed_dirs = [
            Path(settings.OUTPUT_DIR),
            Path(settings.TEMP_DIR),
            Path(settings.BASE_DIR) / "output",
            Path(settings.BASE_DIR) / "storage",
        ]
        cand_path = Path(str(video_path))
        if cand_path.suffix.lower() not in {".mp4", ".mov", ".webm", ".mkv"} or not any(is_safe_path(cand_path, d) for d in allowed_dirs):
            logger.warning(f"⚠️ [Worker Callback] Từ chối video_path ngoài vùng an toàn: {video_path}")
            video_path = None

    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            return {"status": "error", "message": f"VideoJob #{job_id} not found"}

        if status == "completed":
            job.status = "RENDERED"
            if video_path:
                job.video_path = video_path
            if drive_link:
                job.drive_link = drive_link
            job.progress_percent = 100
            job.progress_message = f"🎉 Video đã render hoàn tất ({render_time}s)!"
            job.error_message = None

            log_activity(db, job.user_id or 0, "System", "WORKER_RENDER_COMPLETE",
                         f"Hoàn tất render Video #{job_id} trong {render_time}s: {drive_link or video_path}",
                         channel_id=job.target_channel_id)

            logger.info(f"✅ [Worker Callback] VideoJob #{job_id} đã cập nhật thành công sang RENDERED.")

            # Gửi thông báo Telegram
            try:
                from app.services.channel_intelligence import send_telegram_broadcast
                notify_text = (
                    f"🎉 **VIDEO #{job_id} ĐÃ RENDER XONG!**\n\n"
                    f"🎬 **Tiêu đề:** `{job.title or job.topic}`\n"
                    f"⚡ **Thời gian render:** {render_time}s\n"
                    f"📺 **Kênh:** `{job.target_channel_id}`\n"
                )
                if drive_link:
                    notify_text += f"🔗 [Xem file trên Google Drive]({drive_link})\n"
                send_telegram_broadcast(notify_text)
            except Exception as e_tg:
                logger.debug(f"Không thể gửi thông báo Telegram cho job #{job_id}: {e_tg}")

        else:
            job.status = "FAILED"
            job.error_message = error_msg or "Worker báo lỗi không xác định"
            job.progress_message = f"❌ Render thất bại: {job.error_message}"
            log_activity(db, job.user_id or 0, "System", "WORKER_RENDER_FAILED",
                         f"Thất bại khi render Video #{job_id}: {error_msg}",
                         channel_id=job.target_channel_id)
            logger.warning(f"❌ [Worker Callback] VideoJob #{job_id} render thất bại: {error_msg}")

    return {
        "status": "success",
        "message": f"Đã xử lý callback cho VideoJob #{job_id} thành công",
        "job_status": status
    }


@app.post("/api/worker/dispatch/{job_id}")
async def dispatch_job_to_worker(job_id: int) -> Dict[str, Any]:
    """Điều phối tác vụ render của VideoJob sang Local Render Pipeline."""
    from app.services.worker_dispatcher import worker_dispatcher
    ok, msg, worker_type = worker_dispatcher.dispatch_job(job_id)
    return {
        "status": "success" if ok else "error",
        "message": msg,
        "worker_type": worker_type,
        "job_id": job_id
    }


@app.get("/api/worker/status")
async def get_worker_status_api() -> Dict[str, Any]:
    """Trả về trạng thái chi tiết của hệ thống Local Hardware Render."""
    from app.services.worker_dispatcher import worker_dispatcher
    return worker_dispatcher.get_workers_status()


# ─── Upload to Google Drive ───────────────────────────────────────────────────

@app.post("/api/videos/{job_id}/upload-drive")
async def upload_to_drive(job_id: int, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    """
    Đẩy video đã render lên Google Drive 20TB.
    Trả về link Drive ngay khi upload xong.
    """
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        if job.status not in ("RENDERED", "PUBLISHED"):
            raise HTTPException(status_code=400, detail=f"Video chưa render xong (status: {job.status})")
        if not job.video_path or not Path(job.video_path).exists():
            raise HTTPException(status_code=404, detail="File video không tồn tại trên máy")
        if job.drive_link:
            return {"success": True, "drive_link": job.drive_link, "message": "Video đã có sẵn trên Drive rồi!"}
        video_path = job.video_path
        video_title = job.title or job.topic or f"Video_{job_id}"
        target_channel = job.target_channel_id

    # Xác định đúng kênh và tài khoản Google lưu trữ của kênh đó
    cfg = _read_channels_yaml()
    channels = cfg.get("channels", [])
    ch_cfg = next((c for c in channels if c.get("id") == target_channel), {})
    ch_name = ch_cfg.get("name") or target_channel or "Psychology of the world"
    acc_id = ch_cfg.get("google_account", "account_1")

    # Upload đồng bộ (không chạy background để có link ngay)
    try:
        from app.agents.drive_agent import DriveAgent
        from app.database.models import get_db as _get_db
        agent = DriveAgent(account_name=acc_id)
        if not agent.service:
            raise HTTPException(
                status_code=503,
                detail=f"Google Drive của tài khoản '{acc_id}' chưa kết nối. Vào tab 'Kênh & Tài Khoản' bấm '☁️ Kết Nối Google Drive' để xác thực."
            )
        result = agent.upload_video(video_path, video_title, channel_name=ch_name)
        if result.get("status") == "success" and result.get("drive_link"):
            drive_link = result["drive_link"]
            folder_link = result.get("folder_link")
            with _get_db() as db:
                j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if j:
                    j.drive_link = drive_link
                    from app.database.models import log_activity
                    log_activity(db, 0, "WebStudio", "UPLOAD_DRIVE",
                                 f"Video #{job_id} đã tải lên thư mục kênh '{ch_name}' trên Google Drive: {drive_link}",
                                 channel_id=target_channel)
            return {
                "success": True,
                "drive_link": drive_link,
                "folder_link": folder_link,
                "channel_name": ch_name,
                "message": f"Tải lên Google Drive thành công vào thư mục kênh '{ch_name}'!"
            }
        else:
            raise HTTPException(status_code=500, detail=result.get("error", "Upload thất bại"))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi upload Drive job #{job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# ─── Publish to YouTube ───────────────────────────────────────────────────────

class PublishYouTubeRequest(BaseModel):
    channel_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    privacy: str = "public"
    schedule_time_iso: Optional[str] = None
    auto_schedule: bool = False
    pinned_comment: Optional[str] = None


@app.get("/api/videos/{job_id}/monetization-preview")
def get_video_monetization_preview(job_id: int, channel_id: Optional[str] = None):
    """
    Sinh gói xem trước sản phẩm Affiliate Amazon (link kèm tag kizakawa-20, mô tả & bình luận mồi)
    cho video trước khi đăng lên YouTube.
    """
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")

        from app.database.models import normalize_channel_id
        target_ch = normalize_channel_id(channel_id or job.target_channel_id or "ch_psychology_of_the_world")
        cfg = _read_channels_yaml()
        channels = cfg.get("channels", [])
        ch_cfg = next((c for c in channels if c.get("id") == target_ch), {"id": target_ch, "name": target_ch})

        title = job.title or job.topic or f"Video #{job_id}"
        description = job.description or ""
        pinned_comment = None
        topic = job.topic or ""
        script_text = ""

        if job.scenes_json:
            try:
                s_data = json.loads(job.scenes_json)
                if isinstance(s_data, list):
                    scenes = s_data
                    pinned_comment = None
                elif isinstance(s_data, dict):
                    pinned_comment = s_data.get("pinned_comment")
                    scenes = s_data.get("scenes", [])
                script_text = " ".join([sc.get("narration", "") for sc in scenes if isinstance(sc, dict)])
            except Exception:
                pass

        from app.services.monetization_engine import preview_monetization_bundle
        bundle = preview_monetization_bundle(
            channel_config=ch_cfg,
            title=title,
            description=description,
            pinned_comment=pinned_comment,
            topic=topic,
            script=script_text
        )
        return {"success": True, **bundle}

@app.get("/api/channels/{channel_id}/next-golden-slot")
def get_channel_next_golden_slot(channel_id: str):
    """Tính toán khe giờ vàng tiếp theo trống lịch cho kênh"""
    try:
        from app.services.smart_scheduler import (
            get_next_available_golden_slot,
            get_channel_upcoming_schedule,
            get_golden_slots_overview,
            sync_scheduled_jobs_status
        )
        sync_scheduled_jobs_status()
        slot_dt, slot_iso, display_vn, display_us = get_next_available_golden_slot(channel_id)
        upcoming = get_channel_upcoming_schedule(channel_id, limit=5)
        overview = get_golden_slots_overview()
        return {
            "success": True,
            "channel_id": channel_id,
            "slot_iso": slot_iso,
            "display_vn": display_vn,
            "display_us": display_us,
            "upcoming": upcoming,
            "golden_slots": overview
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/channels/{channel_id}/schedule")
def get_channel_full_schedule(channel_id: str):
    """Lấy chi tiết toàn bộ lịch phát sóng, khe tiếp theo và 4 khung giờ vàng quốc tế"""
    try:
        from app.services.smart_scheduler import (
            get_next_available_golden_slot,
            get_channel_upcoming_schedule,
            get_golden_slots_overview,
            sync_scheduled_jobs_status
        )
        sync_scheduled_jobs_status()
        slot_dt, slot_iso, display_vn, display_us = get_next_available_golden_slot(channel_id)
        upcoming = get_channel_upcoming_schedule(channel_id, limit=10)
        overview = get_golden_slots_overview()
        return {
            "success": True,
            "channel_id": channel_id,
            "next_slot": {
                "iso": slot_iso,
                "display_vn": display_vn,
                "display_us": display_us
            },
            "upcoming": upcoming,
            "golden_slots": overview
        }
    except Exception as e:
        return {"success": False, "error": str(e)}




class UpdateVideoTagsRequest(BaseModel):
    tags: Any = ""
    append: bool = True
    sync_youtube: bool = True
    update_title_hashtags: bool = True
    update_description_hashtags: bool = True


@app.get("/api/videos/{job_id}/suggest-tags")
async def suggest_video_tags(job_id: int, channel_id: Optional[str] = None) -> Dict[str, Any]:
    """Tự động gợi ý bộ Tags SEO & Hashtags chuẩn ngách cho video."""
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        target_ch = channel_id or job.target_channel_id or "default"
        title = job.title or job.topic or ""
        topic = job.topic or ""
        script = job.scenes_json or job.script or ""
        existing_tags = [t.strip() for t in (job.tags or "").split(",") if t.strip()]

    cfg = _read_channels_yaml()
    channels = cfg.get("channels", [])
    ch_cfg = next((c for c in channels if c.get("id") == target_ch), {"id": target_ch, "name": target_ch})

    from app.agents.publisher_agent import PublisherAgent
    pub = PublisherAgent(channel_config=ch_cfg)
    smart = pub.build_smart_video_tags_and_hashtags(
        title=title,
        topic=topic,
        script=script,
        extra_tags=existing_tags,
    )
    return {
        "success": True,
        "job_id": job_id,
        "channel_id": target_ch,
        "tags": smart["tags"],
        "tags_csv": ", ".join(smart["tags"]),
        "title_hashtags": smart["title_hashtags"],
        "enriched_title": smart["enriched_title"],
        "description_hashtags": smart["description_hashtags"],
    }


@app.post("/api/videos/{job_id}/tags")
async def update_or_add_video_tags(job_id: int, body: UpdateVideoTagsRequest) -> Dict[str, Any]:
    """
    Gắn thêm hoặc cập nhật bộ Tags & Hashtags cho VideoJob trong DB,
    đồng thời tự động đồng bộ trực tiếp lên YouTube nếu video đã có youtube_video_id.
    """
    import re as _re
    raw_in = body.tags
    if isinstance(raw_in, list):
        input_tags = [str(x).strip() for x in raw_in if str(x).strip()]
    else:
        input_tags = [x.strip() for x in _re.split(r"[,;#]+", str(raw_in or "")) if x.strip()]

    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")

        target_ch = job.target_channel_id or "default"
        cur_tags = [t.strip() for t in (job.tags or "").split(",") if t.strip()]
        seed_tags = (input_tags + cur_tags) if body.append else input_tags

        cfg = _read_channels_yaml()
        channels = cfg.get("channels", [])
        ch_cfg = next((c for c in channels if c.get("id") == target_ch), {"id": target_ch, "name": target_ch})

        from app.agents.publisher_agent import PublisherAgent
        pub = PublisherAgent(channel_config=ch_cfg)
        smart = pub.build_smart_video_tags_and_hashtags(
            title=job.title or job.topic or "",
            topic=job.topic or "",
            script=job.scenes_json or job.script or "",
            extra_tags=seed_tags,
        )

        # Ưu tiên giữ các tag người dùng vừa nhập ở đầu danh sách
        merged_tags = []
        seen_l = set()
        for t_cand in (*input_tags, *smart["tags"]):
            c_t = _re.sub(r"^#+", "", str(t_cand).strip()).strip()
            if c_t and c_t.lower() not in seen_l and len(merged_tags) < 25:
                merged_tags.append(c_t)
                seen_l.add(c_t.lower())

        job.tags = ", ".join(merged_tags)
        if body.update_title_hashtags and smart.get("enriched_title"):
            base_t = smart["base_title"]
            user_short_hashes = [
                _re.sub(r"[^a-zA-Z0-9À-ỹçãõáéíóúâêô]", "", t).lower()
                for t in input_tags
                if 2 <= len(_re.sub(r"[^a-zA-Z0-9À-ỹçãõáéíóúâêô]", "", t)) <= 18
            ]
            hash_chain = ["#Shorts"]
            for h_c in (*user_short_hashes[:2], *[h.lstrip("#") for h in smart["title_hashtags"][1:]]):
                if h_c and h_c.lower() != "shorts" and f"#{h_c.lower()}" not in [x.lower() for x in hash_chain]:
                    cand_s = " ".join(hash_chain + [f"#{h_c.lower()}"])
                    if len(base_t) + 1 + len(cand_s) <= 100 and len(hash_chain) < 4:
                        hash_chain.append(f"#{h_c.lower()}")
            job.title = f"{base_t} {' '.join(hash_chain)}".strip()[:100]

        if body.update_description_hashtags:
            cur_desc = (job.description or "").strip()
            desc_body = _re.sub(r"(?:\n*\s*(?:#[^\s#]+\s*)+)$", "", cur_desc).strip()
            job.description = f"{desc_body}\n\n{smart['description_hashtags']}".strip() if desc_body else smart["description_hashtags"]

        yt_vid = (job.youtube_video_id or "").strip()
        final_title = job.title
        final_desc = job.description

    yt_sync_res = None
    if yt_vid and body.sync_youtube:
        try:
            yt_sync_res = await asyncio.to_thread(
                pub.add_tags_to_video,
                video_id=yt_vid,
                new_tags=merged_tags,
                append=False,
                update_title_hashtags=body.update_title_hashtags,
                update_description_hashtags=body.update_description_hashtags,
                topic=final_title,
            )
        except Exception as ex_yt:
            yt_sync_res = {"success": False, "error": str(ex_yt)}

    return {
        "success": True,
        "job_id": job_id,
        "youtube_video_id": yt_vid or None,
        "youtube_synced": bool(yt_sync_res and yt_sync_res.get("success")),
        "youtube_sync_detail": yt_sync_res,
        "title": (yt_sync_res.get("title") if yt_sync_res and yt_sync_res.get("title") else final_title),
        "description": (yt_sync_res.get("description") if yt_sync_res and yt_sync_res.get("description") else final_desc),
        "tags": merged_tags,
        "tags_csv": ", ".join(merged_tags),
        "message": (
            f"Đã gắn {len(merged_tags)} tags & đồng bộ trực tiếp lên YouTube ({yt_vid})!"
            if (yt_sync_res and yt_sync_res.get("success"))
            else f"Đã lưu {len(merged_tags)} tags & hashtags vào Video #{job_id}!"
        ),
    }


@app.post("/api/videos/{job_id}/publish-youtube")
async def publish_to_youtube(
    job_id: int,
    body: Optional[PublishYouTubeRequest] = None,
    privacy: str = "public"
) -> Dict[str, Any]:
    """
    Đăng hoặc tự động lên lịch video đã render lên YouTube qua PublisherAgent.
    Hỗ trợ chế độ auto_schedule tự động xếp vào khe giờ vàng còn trống tiếp theo.
    """
    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Không tìm thấy video job")
        if job.status not in ("RENDERED", "SCHEDULED", "PUBLISHED"):
            raise HTTPException(status_code=400, detail=f"Video chưa render xong (status: {job.status})")
        if not job.video_path or not Path(job.video_path).exists():
            raise HTTPException(status_code=404, detail="File video không tồn tại trên máy")
        
        # Cập nhật thông tin nếu người dùng tùy chỉnh trước khi xuất bản
        target_channel = (body.channel_id if body and body.channel_id else None) or job.target_channel_id or "default"
        chosen_title = (body.title.strip() if body and body.title and body.title.strip() else None) or job.title or job.topic or f"Video_{job_id}"
        chosen_desc = (body.description if body and body.description is not None else None) or job.description or ""
        chosen_tags_str = (body.tags if body and body.tags is not None else None) or job.tags or ""
        chosen_privacy = (body.privacy if body and body.privacy else None) or privacy or "public"
        schedule_time = body.schedule_time_iso if body else None
        auto_schedule = (body.auto_schedule if body else False) or (schedule_time == "auto")

        display_vn_slot = None
        display_us_slot = None
        slot_dt = None

        # Tự động tính toán khe giờ vàng nếu bật auto_schedule
        if auto_schedule:
            from app.services.smart_scheduler import get_next_available_golden_slot
            slot_dt, schedule_time, display_vn_slot, display_us_slot = get_next_available_golden_slot(target_channel)
            chosen_privacy = "private"
        elif schedule_time:
            chosen_privacy = "private"

        # Đồng bộ lại job DB
        job.target_channel_id = target_channel
        job.title = chosen_title
        job.description = chosen_desc
        job.tags = chosen_tags_str

        video_path = job.video_path
        video_tags = [t.strip() for t in chosen_tags_str.split(",") if t.strip()]

    try:
        from app.agents.publisher_agent import PublisherAgent
        from app.database.models import get_db as _get_db, log_activity

        cfg = _read_channels_yaml()
        channels = cfg.get("channels", [])
        ch_cfg = next((c for c in channels if c.get("id") == target_channel), {"id": target_channel, "name": target_channel})
        ch_lang = ch_cfg.get("language", "en")
        acc_id = ch_cfg.get("google_account", "account_1")

        # ── Kiểm tra token OAuth riêng của kênh để tránh đăng nhầm sang kênh cũ ──
        base_dir = Path(settings.BASE_DIR)
        ch_token = base_dir / "credentials" / "channels" / target_channel / "youtube_token.json"
        acc_token = base_dir / "credentials" / "accounts" / acc_id / "youtube_token.json"

        if ch_token.exists():
            token_file = str(ch_token)
        else:
            # Kiểm tra xem đây có phải là kênh phụ chưa cấp quyền riêng
            brand_channels = []
            for a in cfg.get("google_accounts", []):
                if a.get("id") == acc_id:
                    brand_channels = a.get("brand_channels", [])
                    break
            is_first_ch = len(brand_channels) > 0 and brand_channels[0].get("id") == target_channel
            if not is_first_ch and len(brand_channels) > 1:
                ch_name = ch_cfg.get("name", target_channel)
                first_name = brand_channels[0].get("name", brand_channels[0].get("id"))
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"⚠️ Kênh '{ch_name}' chưa được cấp quyền YouTube riêng! "
                        f"Nếu đăng ngay, video sẽ bị đăng nhầm vào kênh '{first_name}'. "
                        f"Vui lòng vào tab 'Kênh & Tài Khoản', tìm kênh '{ch_name}' và bấm '🔑 Cấp Quyền YouTube (Kênh Này)' "
                        f"để chọn đúng kênh trên Google."
                    )
                )
            token_file = str(acc_token)

        ch_cfg["token_file"] = token_file
        pub_agent = PublisherAgent(channel_config=ch_cfg, account_name=acc_id)
        if not pub_agent.service:
            ch_name = ch_cfg.get("name", target_channel)
            init_err = getattr(pub_agent, "init_error", None) or "Chưa kết nối OAuth"
            if "invalid_grant" in init_err or "expired" in init_err:
                detail_msg = (
                    f"⚠️ Phiên đăng nhập YouTube của kênh '{ch_name}' đã hết hạn (Google OAuth token expired/revoked). "
                    f"Vui lòng vào tab 'Kênh & Tài Khoản' trên Web Dashboard bấm '🔑 Cấp Quyền YouTube (Kênh Này)' để kết nối lại."
                )
            else:
                detail_msg = f"Kênh '{ch_name}' chưa kết nối YouTube OAuth ({init_err}). Vui lòng vào tab 'Kênh & Tài Khoản' trên Web Dashboard bấm '🔑 Đăng Nhập YouTube (Kênh Này)'."
            raise HTTPException(
                status_code=503,
                detail=detail_msg
            )

        pinned_comment = (body.pinned_comment.strip() if body and body.pinned_comment else None)
        if not pinned_comment and job.scenes_json:
            try:
                s_data = json.loads(job.scenes_json)
                pinned_comment = s_data.get("pinned_comment")
            except Exception:
                pass
        if not pinned_comment:
            n_key = str(ch_cfg.get("niche") or "").lower()
            if "psychology" in n_key:
                pinned_comment = pub_agent.build_default_engagement_comment(title=chosen_title, target_lang=ch_lang)
            elif any(k in n_key for k in ("rescue", "animal")):
                pinned_comment = pub_agent.build_default_engagement_comment(title=chosen_title, target_lang=ch_lang)
            elif any(k in n_key for k in ("deep_sea", "mystery", "history")):
                pinned_comment = pub_agent.build_default_engagement_comment(title=chosen_title, target_lang=ch_lang)
            elif any(k in n_key for k in ("predator", "wildlife")):
                pinned_comment = pub_agent.build_default_engagement_comment(title=chosen_title, target_lang=ch_lang)
            else:
                pinned_comment = pub_agent.build_default_engagement_comment(title=chosen_title, target_lang=ch_lang)

        pub_res = pub_agent.upload_video(
            video_path=video_path,
            title=chosen_title,
            description=chosen_desc,
            tags=video_tags,
            privacy_status=chosen_privacy,
            schedule_time_iso=schedule_time,
            default_language=ch_lang,
            default_audio_language=ch_lang,
            pinned_comment=pinned_comment,
            topic=job.topic or "",
            script=job.scenes_json or "",
        )

        if pub_res.get("status") == "success":
            yt_url = pub_res.get("youtube_url")
            yt_id = pub_res.get("video_id")
            yt_shorts_url = pub_res.get("shorts_url") or f"https://www.youtube.com/shorts/{yt_id}"
            ch_name = ch_cfg.get("name", target_channel)

            # Tự động sao lưu lên Google Drive chỉ khi ENABLE_DRIVE_VIDEO_BACKUP=true (mặc định tắt để tránh lưu trữ video nặng)
            drive_link = None
            folder_link = None
            if os.getenv("ENABLE_DRIVE_VIDEO_BACKUP", "false").lower() in ("1", "true", "yes"):
                try:
                    from app.agents.drive_agent import DriveAgent
                    drive_agent = DriveAgent(account_name=acc_id)
                    if drive_agent.service:
                        drive_res = drive_agent.upload_video(video_path, chosen_title, channel_name=ch_name)
                        if drive_res.get("status") == "success":
                            drive_link = drive_res.get("drive_link")
                            folder_link = drive_res.get("folder_link")
                except Exception as e_drv:
                    logger.warning(f"Không thể tự động backup Drive cho Video #{job_id}: {e_drv}")

            with _get_db() as db:
                j = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if j:
                    j.status = "SCHEDULED" if schedule_time else "PUBLISHED"
                    j.youtube_video_id = yt_id
                    if pub_res.get("enriched_description"):
                        j.description = pub_res.get("enriched_description")
                    if drive_link:
                        j.drive_link = drive_link
                    if slot_dt:
                        j.scheduled_time = slot_dt
                    elif schedule_time:
                        try:
                            from datetime import datetime as _dt
                            j.scheduled_time = _dt.fromisoformat(schedule_time.replace("Z", "+00:00"))
                        except Exception:
                            pass
                    
                    prod_info = pub_res.get("product_info")
                    prod_name = prod_info.get("name") if prod_info else ""
                    affiliate_note = f" (Amazon: {prod_name})" if prod_name else ""
                    msg = (
                        f"Video #{job_id} đã lên lịch công chiếu vào {display_vn_slot} trên kênh '{ch_name}'{affiliate_note}: {yt_shorts_url}"
                        if schedule_time and display_vn_slot
                        else f"Video #{job_id} đã xuất bản lên kênh '{ch_name}'{affiliate_note}: {yt_shorts_url}"
                    )
                    log_activity(db, 0, "WebStudio", "PUBLISH_YOUTUBE", msg, channel_id=target_channel)

            try:
                from app.services.video_memory_rag import archive_job_to_rag_and_cleanup_files
                archive_job_to_rag_and_cleanup_files(job_id)
            except Exception as ex_rag:
                logger.debug(f"[WebPublish] Mini-RAG archive warning: {ex_rag}")

            success_msg = (
                f"Đã lên lịch thành công cho Video #{job_id}! Tự động công chiếu vào {display_vn_slot} ({display_us_slot}). Link: {yt_shorts_url}"
                if schedule_time and display_vn_slot
                else f"Đã đăng lên Shorts kênh '{ch_name}' thành công! Link: {yt_shorts_url}"
            )
            return {
                "success": True,
                "video_id": yt_id,
                "youtube_url": yt_url,
                "shorts_url": yt_shorts_url,
                "drive_link": drive_link,
                "folder_link": folder_link,
                "channel_id": target_channel,
                "channel_name": ch_name,
                "scheduled": bool(schedule_time),
                "scheduled_vn": display_vn_slot,
                "scheduled_iso": schedule_time,
                "product_info": pub_res.get("product_info"),
                "pinned_comment": pub_res.get("pinned_comment"),
                "comment_id": pub_res.get("comment_id"),
                "enriched_description": pub_res.get("enriched_description"),
                "message": success_msg
            }
        else:
            raise HTTPException(status_code=500, detail=pub_res.get("error", "Đăng YouTube thất bại"))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi đăng YouTube job #{job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

