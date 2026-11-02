"""app.web.routers.repurpose_and_batch — Tái chế Video Dài thành Shorts & Sản xuất Hàng loạt (Batch Producer)."""
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

# ─── Smart AI Shorts Repurposer Endpoints (Tái chế video dài thành Shorts) ─

class RepurposeAnalyzeRequest(BaseModel):
    url_or_path: str
    niche_id: str = "general"
    mode: str = "self_owned"
    channel_name: str = ""
    channel_id: Optional[str] = None
    target_lang: str = "en"
    video_description: str = ""
    device_id: Optional[str] = None
    device_name: Optional[str] = None

class RepurposeRenderRequest(BaseModel):
    source: str
    start_seconds: float
    end_seconds: float
    title: str = ""
    description: str = ""
    channel_name: str = ""
    niche_id: str = "general"
    layout_mode: str = "smart_crop_full"
    audio_mode: str = "original"
    add_subtitles: bool = True
    subtitle_text: str = ""
    timed_transcript: Optional[List[Dict[str, Any]]] = None
    credit_text: Optional[str] = None
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    source_url: str = ""
    author: str = ""
    mode: str = "self_owned"
    crop_focus: str = "center"
    mute_original: bool = False
    voice_id: Optional[str] = None
    custom_voiceover_text: Optional[str] = None
    bgm_mood: Optional[str] = "none"
    blur_watermark: bool = False
    watermark_position: str = "top_right"

class RepurposeMashupRenderRequest(BaseModel):
    source: str
    clips: List[Dict[str, Any]]
    title: str = ""
    description: str = ""
    channel_name: str = ""
    niche_id: str = "general"
    layout_mode: str = "smart_crop_full"
    crop_focus: str = "center"
    audio_mode: str = "original"
    add_subtitles: bool = True
    enable_sfx: bool = False
    bgm_mood: Optional[str] = "suspense"
    device_id: Optional[str] = None
    device_name: Optional[str] = None
    source_url: str = ""
    author: str = ""
    mode: str = "self_owned"
    mute_original: bool = False
    voice_id: Optional[str] = None
    blur_watermark: bool = False
    watermark_position: str = "top_right"

class RepurposeTranslateRequest(BaseModel):
    text: str
    niche_id: str = "general"

@app.post("/api/repurposer/translate-text")
def repurpose_translate_text(req: RepurposeTranslateRequest) -> Dict[str, Any]:
    """Dịch nhanh kịch bản thuyết minh sang tiếng Anh viral chuẩn YouTube Shorts."""
    from app.services.video_repurposer import repurposer_service
    english_text = repurposer_service._ensure_english_narration(req.text, req.niche_id)
    return {"success": True, "english_text": english_text}

@app.get("/api/system/client-info")
def get_client_system_info(request: Request) -> Dict[str, Any]:
    """Trả về thông tin kết nối mạng LAN của máy khách đang truy cập."""
    client_ip = request.client.host if request.client else "127.0.0.1"
    return {
        "client_ip": client_ip,
        "is_local": client_ip in ("127.0.0.1", "localhost", "::1"),
    }

@app.get("/api/repurposer/local-videos")
def list_local_videos_for_repurpose() -> Dict[str, Any]:
    """Lấy danh sách các video có sẵn trong hệ thống để tái chế tức thì."""
    from app.services.video_repurposer import repurposer_service
    videos = repurposer_service.list_local_videos()
    return {"success": True, "count": len(videos), "videos": videos}

@app.post("/api/repurposer/analyze")
def analyze_repurpose_video(req: RepurposeAnalyzeRequest) -> Dict[str, Any]:
    """Phân tích video và phát hiện 3-5 khoảnh khắc highlight viral bằng Gemini AI."""
    from app.services.video_repurposer import repurposer_service, extract_youtube_video_id
    try:
        meta = repurposer_service.get_video_metadata(req.url_or_path)
        transcript_entries = []
        video_id = meta.get("video_id")

        # Xác định ngôn ngữ đích (ưu tiên tiếng Anh cho US/Global channel)
        resolved_lang = (req.target_lang or "en").strip().lower()
        if req.channel_id or req.channel_name:
            try:
                cfg = _read_channels_yaml()
                for ch in cfg.get("channels", []):
                    if (req.channel_id and ch.get("id") == req.channel_id) or (req.channel_name and (ch.get("name") in req.channel_name or ch.get("id") in req.channel_name)):
                        resolved_lang = ch.get("language", "en")
                        break
            except Exception:
                pass

        if meta.get("source_type") == "youtube" and video_id:
            transcript_entries = repurposer_service.fetch_transcript(video_id, target_lang=resolved_lang)
        
        # Lấy mô tả từ request hoặc tự động từ yt-dlp metadata
        video_desc = req.video_description or meta.get("description", "")

        moments = repurposer_service.analyze_viral_moments(
            transcript_entries=transcript_entries,
            video_title=meta.get("title", "Video"),
            video_duration=meta.get("duration_seconds", 60.0),
            niche_id=req.niche_id,
            mode=req.mode,
            channel_name=req.channel_name,
            video_description=video_desc,
            target_lang=resolved_lang,
        )

        mashups = repurposer_service.analyze_viral_mashups(
            transcript_entries=transcript_entries,
            video_title=meta.get("title", "Video"),
            video_duration=meta.get("duration_seconds", 60.0),
            niche_id=req.niche_id,
            mode=req.mode,
            channel_name=req.channel_name,
            video_description=video_desc,
            target_lang=resolved_lang,
        )
        
        return {
            "success": True,
            "metadata": meta,
            "has_transcript": len(transcript_entries) > 0,
            "transcript_count": len(transcript_entries),
            "moments": moments,
            "mashups": mashups,
            "niche_id": req.niche_id,
            "mode": req.mode,
        }
    except Exception as e:
        logger.error(f"Lỗi phân tích repurpose video: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def _validate_repurpose_local_source(source_path: str) -> str:
    """Đảm bảo đường dẫn file nguồn cục bộ chỉ nằm trong thư mục output/temp/storage/tests."""
    import tempfile
    allowed_dirs = [
        Path(settings.OUTPUT_DIR),
        Path(settings.TEMP_DIR),
        Path(settings.BASE_DIR) / "output",
        Path(settings.BASE_DIR) / "storage",
        Path(settings.BASE_DIR) / "tests",
        Path(tempfile.gettempdir()),
    ]
    cand = Path(source_path)
    if not any(is_safe_path(cand, d) for d in allowed_dirs):
        raise HTTPException(status_code=403, detail="Đường dẫn file nguồn nằm ngoài vùng cho phép")
    return str(cand.resolve())

@app.post("/api/repurposer/render-clip")
def render_repurposed_clip(req: RepurposeRenderRequest, request: Request) -> Dict[str, Any]:
    """Cắt đoạn highlight, chuyển tỉ lệ 9:16 và ghép phụ đề Karaoke chuẩn Niche Profile."""
    import time
    import uuid
    from app.services.video_repurposer import repurposer_service, extract_youtube_video_id
    try:
        client_ip = request.client.host if request.client else "unknown"
        source_path = req.source.strip()
        effective_source_url = req.source_url.strip() if req.source_url else (source_path if source_path.startswith("http") else "")
        if os.path.exists(source_path):
            source_path = _validate_repurpose_local_source(source_path)
        else:
            video_id = extract_youtube_video_id(source_path)
            if not video_id:
                raise HTTPException(status_code=400, detail="Không tìm thấy file nguồn hoặc link YouTube hợp lệ")
            if not effective_source_url:
                effective_source_url = f"https://www.youtube.com/watch?v={video_id}"
            source_path = repurposer_service.download_segment_or_full(source_path)
            
        timestamp_int = int(time.time())
        # Tạo tên file độc nhất tuyệt đối, có định danh thiết bị chống xung đột mạng LAN
        raw_dev = (req.device_name or req.device_id or "dev").strip()
        safe_dev = "".join(c for c in raw_dev if c.isalnum() or c in ("-", "_"))[:10] or "dev"
        unique_tag = uuid.uuid4().hex[:6]
        out_filename = f"short_{safe_dev}_{timestamp_int}_{unique_tag}.mp4"
        
        result = repurposer_service.render_repurposed_short(
            source_video_path=source_path,
            start_seconds=req.start_seconds,
            end_seconds=req.end_seconds,
            output_filename=out_filename,
            niche_id=req.niche_id,
            layout_mode=req.layout_mode,
            audio_mode=req.audio_mode,
            add_subtitles=req.add_subtitles,
            subtitle_text=req.subtitle_text or req.title,
            timed_transcript=req.timed_transcript,
            credit_text=req.credit_text,
            device_id=req.device_id,
            device_name=req.device_name,
            client_ip=client_ip,
            title=req.title,
            description=req.description,
            channel_name=req.channel_name,
            source_url=effective_source_url,
            author=req.author,
            mode=req.mode,
            crop_focus=req.crop_focus,
            mute_original=req.mute_original,
            voice_id=req.voice_id,
            custom_voiceover_text=req.custom_voiceover_text,
            bgm_mood=req.bgm_mood,
            blur_watermark=req.blur_watermark,
            watermark_position=req.watermark_position,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi render repurposed short: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/repurposer/render-mashup")
def render_repurposed_mashup(req: RepurposeMashupRenderRequest, request: Request) -> Dict[str, Any]:
    """Nấu lại video đa phân đoạn (Multi-Clip Mashup), lồng SFX Whoosh, BGM ducking và phụ đề Karaoke."""
    import time
    import uuid
    from app.services.video_repurposer import repurposer_service, extract_youtube_video_id
    try:
        client_ip = request.client.host if request.client else "unknown"
        source_path = req.source.strip()
        effective_source_url = req.source_url.strip() if req.source_url else (source_path if source_path.startswith("http") else "")
        if os.path.exists(source_path):
            source_path = _validate_repurpose_local_source(source_path)
        else:
            video_id = extract_youtube_video_id(source_path)
            if not video_id:
                raise HTTPException(status_code=400, detail="Không tìm thấy file nguồn hoặc link YouTube hợp lệ")
            if not effective_source_url:
                effective_source_url = f"https://www.youtube.com/watch?v={video_id}"
            source_path = repurposer_service.download_segment_or_full(source_path)

        timestamp_int = int(time.time())
        raw_dev = (req.device_name or req.device_id or "dev").strip()
        safe_dev = "".join(c for c in raw_dev if c.isalnum() or c in ("-", "_"))[:10] or "dev"
        unique_tag = uuid.uuid4().hex[:6]
        output_filename = f"short_mashup_{timestamp_int}_{safe_dev}_{unique_tag}.mp4"

        result = repurposer_service.render_mashup_short(
            source_video_path=source_path,
            clips=req.clips,
            output_filename=output_filename,
            niche_id=req.niche_id,
            layout_mode=req.layout_mode,
            crop_focus=req.crop_focus,
            audio_mode=req.audio_mode,
            add_subtitles=req.add_subtitles,
            enable_sfx=req.enable_sfx,
            bgm_mood=req.bgm_mood,
            title=req.title,
            device_id=req.device_id or "default_device",
            device_name=req.device_name or "Thiết bị khách",
            client_ip=client_ip,
            source_url=effective_source_url,
            author=req.author,
            mode=req.mode,
            channel_name=req.channel_name,
            description=req.description,
            mute_original=req.mute_original,
            voice_id=req.voice_id,
            blur_watermark=req.blur_watermark,
            watermark_position=req.watermark_position,
        )
        return result
    except Exception as e:
        logger.error(f"Lỗi render mashup short: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/repurposer/clips")
def list_repurposed_clips() -> Dict[str, Any]:
    """
    Lấy danh sách các Shorts đã được trích xuất thành công kèm thông tin thiết bị tạo, chỉ số QA và link Amazon.
    Tương thích ngược cả 2 định dạng file: [name].meta.json và [stem].meta.json.
    """
    from app.services.video_repurposer import CLIPS_DIR
    clips = []
    if CLIPS_DIR.exists():
        for f in CLIPS_DIR.glob("*.mp4"):
            try:
                # Tương thích ngược: Ưu tiên [stem].meta.json, nếu không thấy thì thử [name].meta.json
                meta_path = f.with_name(f"{f.stem}.meta.json")
                if not meta_path.exists():
                    meta_path = f.with_name(f"{f.name}.meta.json")

                meta_info = {}
                if meta_path.exists():
                    try:
                        with open(meta_path, "r", encoding="utf-8") as mf:
                            meta_info = json.load(mf)
                    except Exception:
                        pass
                
                from app.services.monetization_engine import (
                    build_repurposed_pinned_comment,
                    build_repurposed_video_description,
                    find_best_amazon_product,
                    build_amazon_url,
                    verify_amazon_product_alive
                )
                amz_prod = meta_info.get("amazon_product")
                needs_save = False

                # Tự động kiểm tra và làm mới nếu sản phẩm Amazon bị chết hoặc chưa kiểm tra
                if not amz_prod or not amz_prod.get("is_verified_alive"):
                    prod_asin = (amz_prod.get("asin") if amz_prod else "") or ""
                    if not prod_asin or not verify_amazon_product_alive(prod_asin):
                        niche_cur = meta_info.get("niche_id", "general")
                        title_cur = meta_info.get("title") or f.stem
                        amz_prod = find_best_amazon_product(niche=niche_cur, title=title_cur)
                        amz_prod["affiliate_url"] = build_amazon_url(
                            amz_prod["asin"],
                            "kizakawa-20",
                            is_search=amz_prod.get("is_search", False)
                        )
                        meta_info["amazon_product"] = amz_prod
                        meta_info["pinned_comment"] = build_repurposed_pinned_comment(
                            title=title_cur,
                            niche_id=niche_cur,
                            source_url=meta_info.get("source_url", ""),
                            author=meta_info.get("author", ""),
                            tracking_tag="kizakawa-20"
                        )
                        meta_info["description"] = build_repurposed_video_description(
                            title=title_cur,
                            summary=meta_info.get("subtitle_text", ""),
                            niche_id=niche_cur,
                            channel_name="",
                            source_url=meta_info.get("source_url", ""),
                            author=meta_info.get("author", ""),
                            mode=meta_info.get("mode", "self_owned"),
                            tracking_tag="kizakawa-20"
                        )
                        needs_save = True

                # Tự động dọn dẹp các mô tả còn dính tiếng Việt từ các lần test trước cho kênh tiếng Anh
                cur_desc = meta_info.get("description", "")
                if cur_desc and re.search(r'[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]', cur_desc, re.IGNORECASE):
                    niche_cur = meta_info.get("niche_id", "general")
                    title_cur = meta_info.get("title") or f.stem
                    meta_info["description"] = build_repurposed_video_description(
                        title=title_cur,
                        summary=title_cur,
                        niche_id=niche_cur,
                        channel_name="",
                        source_url=meta_info.get("source_url", ""),
                        author=meta_info.get("author", ""),
                        mode=meta_info.get("mode", "self_owned"),
                        tracking_tag="kizakawa-20"
                    )
                    needs_save = True

                clip_pinned = meta_info.get("pinned_comment")
                if not clip_pinned:
                    clip_pinned = build_repurposed_pinned_comment(
                        title=meta_info.get("title") or f.stem,
                        niche_id=meta_info.get("niche_id", "general"),
                        source_url=meta_info.get("source_url", ""),
                        author=meta_info.get("author", ""),
                        tracking_tag="kizakawa-20"
                    )
                    meta_info["pinned_comment"] = clip_pinned
                    needs_save = True

                if needs_save:
                    try:
                        with open(meta_path, "w", encoding="utf-8") as mf:
                            json.dump(meta_info, mf, ensure_ascii=False, indent=2)
                    except Exception:
                        pass

                clips.append({
                    "filename": f.name,
                    "url": f"/media/repurposer/clips/{f.name}",
                    "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
                    "created_at": meta_info.get("created_at") or f.stat().st_mtime,
                    "device_id": meta_info.get("device_id", ""),
                    "device_name": meta_info.get("device_name", ""),
                    "client_ip": meta_info.get("client_ip", ""),
                    "title": meta_info.get("title") or f.stem,
                    "description": meta_info.get("description", ""),
                    "pinned_comment": meta_info.get("pinned_comment") or clip_pinned,
                    "niche_id": meta_info.get("niche_id", "general"),
                    "layout_mode": meta_info.get("layout_mode", "smart_crop_full"),
                    "audio_mode": meta_info.get("audio_mode", "original"),
                    "qa_report": meta_info.get("qa_report"),
                    "amazon_product": amz_prod,
                    "source_url": meta_info.get("source_url", ""),
                    "author": meta_info.get("author", ""),
                    "mode": meta_info.get("mode", "self_owned"),
                    "publish_status": meta_info.get("publish_status") or "draft",
                    "youtube_url": meta_info.get("youtube_url") or "",
                    "shorts_url": meta_info.get("shorts_url") or "",
                    "video_id": meta_info.get("video_id") or "",
                    "scheduled_at": meta_info.get("scheduled_at") or "",
                    "published_at": meta_info.get("published_at") or "",
                    "published_channel_name": meta_info.get("published_channel_name") or "",
                })
            except Exception:
                pass
    clips.sort(key=lambda x: x["created_at"], reverse=True)
    return {"success": True, "count": len(clips), "clips": clips}

@app.delete("/api/repurposer/clips/{filename}")
def delete_repurposed_clip(filename: str) -> Dict[str, Any]:
    """
    Xóa video Shorts đã tạo cùng toàn bộ file metadata (.meta.json) và subtitle (.ass) đi kèm.
    Bảo vệ chống Path Traversal (kiểm tra an toàn đường dẫn).
    """
    from app.services.video_repurposer import CLIPS_DIR, SUBS_DIR
    # Kiểm tra an toàn: cấm path traversal
    safe_filename = os.path.basename(filename).strip()
    if not safe_filename or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Tên file không hợp lệ")

    if not safe_filename.endswith(".mp4"):
        raise HTTPException(status_code=400, detail="Chỉ cho phép xóa file định dạng .mp4")

    target_video = CLIPS_DIR / safe_filename
    if not target_video.exists():
        raise HTTPException(status_code=404, detail=f"Không tìm thấy video: {safe_filename}")

    try:
        # Xóa file video MP4
        target_video.unlink(missing_ok=True)

        # Xóa cả 2 định dạng file metadata để dọn sạch hoàn toàn
        meta_stem = CLIPS_DIR / f"{Path(safe_filename).stem}.meta.json"
        meta_full = CLIPS_DIR / f"{safe_filename}.meta.json"
        meta_stem.unlink(missing_ok=True)
        meta_full.unlink(missing_ok=True)

        # Xóa file phụ đề ASS nếu có
        ass_file = SUBS_DIR / f"sub_{Path(safe_filename).stem}.ass"
        ass_file.unlink(missing_ok=True)

        logger.info(f"Đã xóa thành công clip và metadata: {safe_filename}")
        return {"success": True, "message": f"Đã xóa video {safe_filename} thành công"}
    except Exception as e:
        logger.error(f"Lỗi khi xóa clip {safe_filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Lỗi xóa file: {str(e)}")

# ─── Publish Repurposed Short to YouTube ─────────────────────────────────────

class RepurposePublishRequest(BaseModel):
    clip_filename: str           # Tên file clip trong CLIPS_DIR, VD: short_dev_17...abc123.mp4
    channel_id: str              # ID kênh YouTube đích (VD: ch_paws_hearts_rescue)
    title: str                   # Tiêu đề video khi đăng
    description: str = ""        # Mô tả video (để trống = dùng mô tả mặc định kênh)
    tags: str = ""               # Tags phân cách bởi dấu phẩy
    auto_schedule: bool = False  # True = hẹn giờ vàng tự động (private scheduled)
    pinned_comment: Optional[str] = None # Bình luận ghim kèm affiliate Store ID kizakawa-20

@app.post("/api/repurposer/publish-clip")
async def publish_repurposed_clip(req: RepurposePublishRequest) -> Dict[str, Any]:
    """
    Đăng Short đã được render (9:16) thẳng lên YouTube không cần VideoJob.
    Hỗ trợ: đăng ngay (public) hoặc hẹn giờ vàng tự động (auto_schedule=true).
    """
    from app.services.video_repurposer import CLIPS_DIR
    from pathlib import Path as _Path

    safe_clip_name = os.path.basename(req.clip_filename.strip())
    clip_path = CLIPS_DIR / safe_clip_name
    if not is_safe_path(clip_path, CLIPS_DIR) or not clip_path.exists() or not clip_path.is_file():
        raise HTTPException(status_code=404, detail=f"Không tìm thấy clip hợp lệ: {req.clip_filename}")

    try:
        cfg = _read_channels_yaml()
        channels = cfg.get("channels", [])
        ch_cfg = next((c for c in channels if c.get("id") == req.channel_id), None)
        if not ch_cfg:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy kênh: {req.channel_id}")

        acc_id = ch_cfg.get("google_account", "account_1")
        ch_lang = ch_cfg.get("language", "en")
        ch_name = ch_cfg.get("name", req.channel_id)

        # Tìm token file cho kênh
        base_dir = Path(settings.BASE_DIR)
        ch_token = base_dir / "credentials" / "channels" / req.channel_id / "youtube_token.json"
        acc_token = base_dir / "credentials" / "accounts" / acc_id / "youtube_token.json"
        token_file = str(ch_token) if ch_token.exists() else str(acc_token)

        ch_cfg["token_file"] = token_file

        from app.agents.publisher_agent import PublisherAgent
        pub_agent = PublisherAgent(channel_config=ch_cfg, account_name=acc_id)
        if not pub_agent.service:
            init_err = getattr(pub_agent, "init_error", None) or "Chưa kết nối OAuth"
            raise HTTPException(
                status_code=503,
                detail=(
                    f"⚠️ Kênh '{ch_name}' chưa kết nối YouTube OAuth ({init_err}). "
                    f"Vui lòng vào tab 'Kênh & Tài Khoản' → '🔑 Cấp Quyền YouTube (Kênh Này)' để xác thực lại."
                )
            )

        # Tính giờ vàng nếu auto_schedule
        schedule_time = None
        display_slot_vn = None
        privacy = "public"
        if req.auto_schedule:
            from app.services.smart_scheduler import get_next_available_golden_slot
            _, schedule_time, display_slot_vn, _ = get_next_available_golden_slot(req.channel_id)
            privacy = "private"

        # Chuẩn bị tags
        video_tags = [t.strip() for t in req.tags.split(",") if t.strip()] if req.tags else []

        # Mô tả mặc định nếu để trống
        description = req.description.strip() if req.description else (
            f"Short viral được trích xuất bởi AI từ video gốc của kênh {ch_name}. "
            f"Subscribe để xem thêm nội dung độc đáo! 🔔"
        )

        # Bình luận ghim tự động (kèm store ID kizakawa-20)
        pinned_comment = req.pinned_comment.strip() if req.pinned_comment else None
        if not pinned_comment:
            meta_stem = CLIPS_DIR / f"{_Path(req.clip_filename).stem}.meta.json"
            if meta_stem.exists():
                try:
                    with open(meta_stem, "r", encoding="utf-8") as mf:
                        m_data = json.load(mf)
                        pinned_comment = m_data.get("pinned_comment")
                except Exception:
                    pass

        if not pinned_comment:
            from app.services.monetization_engine import build_repurposed_pinned_comment
            pinned_comment = build_repurposed_pinned_comment(
                title=req.title,
                channel_name=ch_name,
                tracking_tag="kizakawa-20"
            )

        pub_res = pub_agent.upload_video(
            video_path=str(clip_path),
            title=req.title,
            description=description,
            tags=video_tags,
            privacy_status=privacy,
            schedule_time_iso=schedule_time,
            default_language=ch_lang,
            default_audio_language=ch_lang,
            pinned_comment=pinned_comment,
            topic=req.title,
            script="",
        )

        if pub_res.get("status") == "success":
            yt_url = pub_res.get("youtube_url", "")
            yt_id = pub_res.get("video_id", "")
            shorts_url = f"https://www.youtube.com/shorts/{yt_id}" if yt_id else yt_url
            pub_status = "scheduled" if req.auto_schedule else "published"

            # Cập nhật metadata file của clip để lưu trạng thái
            meta_stem = CLIPS_DIR / f"{_Path(req.clip_filename).stem}.meta.json"
            if meta_stem.exists():
                try:
                    with open(meta_stem, "r", encoding="utf-8") as mf:
                        m_data = json.load(mf)
                    m_data["publish_status"] = pub_status
                    m_data["youtube_url"] = yt_url
                    m_data["shorts_url"] = shorts_url
                    m_data["video_id"] = yt_id
                    m_data["scheduled_at"] = display_slot_vn or None
                    m_data["published_at"] = datetime.now(timezone.utc).isoformat()
                    m_data["published_channel_name"] = ch_name
                    with open(meta_stem, "w", encoding="utf-8") as mf:
                        json.dump(m_data, mf, ensure_ascii=False, indent=2)
                except Exception as ex:
                    logger.warning(f"Không thể ghi nhận publish_status vào meta.json: {ex}")

            msg = (
                f"✅ Đã lên lịch đăng tại khe vàng {display_slot_vn}!"
                if req.auto_schedule
                else f"✅ Đã đăng công khai lên YouTube!"
            )
            return {
                "success": True,
                "message": msg,
                "publish_status": pub_status,
                "youtube_url": yt_url,
                "shorts_url": shorts_url,
                "video_id": yt_id,
                "scheduled_at": display_slot_vn or None,
                "channel_name": ch_name,
            }
        else:
            if pub_res.get("status") == "waiting_quota_reset":
                meta_stem = CLIPS_DIR / f"{_Path(req.clip_filename).stem}.meta.json"
                if meta_stem.exists():
                    try:
                        with open(meta_stem, "r", encoding="utf-8") as mf:
                            m_data = json.load(mf)
                        m_data["publish_status"] = "queued"
                        m_data["scheduled_at"] = "Chờ cấp hạn mức 14:00"
                        m_data["published_channel_name"] = ch_name
                        with open(meta_stem, "w", encoding="utf-8") as mf:
                            json.dump(m_data, mf, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
            raise HTTPException(status_code=500, detail=pub_res.get("error", "Lỗi không xác định từ YouTube API"))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi publish repurposed clip: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ─── Channel-Level OAuth Endpoints & Live Matrix Wizard (Tích hợp auth_all/auth_channel/auth_google lên Web) ─


_batch_produce_state: Dict[str, Any] = {
    "running": False,
    "started_at": None,
    "completed_at": None,
    "total_target": 0,
    "completed_count": 0,
    "current_channel": "",
    "results": [],
    "error": None,
}

class BatchProduceRequest(BaseModel):
    channels: Optional[str] = "all_6"
    count_per_channel: int = 2

@app.post("/api/channels/batch-auto-produce")
async def start_batch_auto_produce_endpoint(req: Optional[BatchProduceRequest] = None):
    """
    Khởi chạy sản xuất & hẹn lịch tự động hàng loạt cho 6 kênh chủ lực (mặc định loại trừ Paws & Hearts Rescue)
    ngay trên nền Web Dashboard.
    """
    if _batch_produce_state.get("running"):
        return {
            "success": False,
            "message": f"Đang có tiến trình sản xuất hàng loạt chạy ({_batch_produce_state.get('completed_count')}/{_batch_produce_state.get('total_target')}).",
            "state": _batch_produce_state,
        }

    from app.services.batch_producer import SIX_ACTIVE_CHANNELS, CHANNELS_CONFIG, produce_channel_video

    ch_arg = (req.channels if req and req.channels else "all_6").strip()
    count_per_ch = max(1, min(4, int(req.count_per_channel if req and req.count_per_channel else 2)))

    if ch_arg == "all_6":
        targets = list(SIX_ACTIVE_CHANNELS)
    elif ch_arg == "all":
        targets = list(SIX_ACTIVE_CHANNELS) + ["ch_paws_hearts_rescue"]
    else:
        targets = [c.strip() for c in ch_arg.split(",") if c.strip() in CHANNELS_CONFIG]

    if not targets:
        raise HTTPException(status_code=400, detail="Không có kênh hợp lệ để sản xuất")

    _batch_produce_state.update({
        "running": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "total_target": len(targets) * count_per_ch,
        "completed_count": 0,
        "current_channel": targets[0],
        "results": [],
        "error": None,
    })

    def _run_batch_bg():
        import asyncio as _aio
        loop = _aio.new_event_loop()
        _aio.set_event_loop(loop)
        try:
            for t in targets:
                for v_idx in range(1, count_per_ch + 1):
                    _batch_produce_state["current_channel"] = f"{CHANNELS_CONFIG.get(t, {}).get('name', t)} (#{v_idx}/{count_per_ch})"
                    try:
                        res = loop.run_until_complete(
                            produce_channel_video(
                                t,
                                dry_run=False,
                                video_index=v_idx,
                                total_per_channel=count_per_ch,
                            )
                        )
                        if res:
                            _batch_produce_state["results"].append(res)
                    except Exception as ex_item:
                        logger.error(f"[BatchAutoProduce] Lỗi kênh {t} #{v_idx}: {ex_item}", exc_info=True)
                        _batch_produce_state["results"].append({
                            "channel": CHANNELS_CONFIG.get(t, {}).get("name", t),
                            "channel_id": t,
                            "title": f"FAILED (#{v_idx})",
                            "error": str(ex_item),
                        })
                    _batch_produce_state["completed_count"] = len(_batch_produce_state["results"])
        except Exception as ex_all:
            _batch_produce_state["error"] = str(ex_all)
        finally:
            _batch_produce_state["running"] = False
            _batch_produce_state["current_channel"] = ""
            _batch_produce_state["completed_at"] = datetime.now(timezone.utc).isoformat()
            loop.close()

    threading.Thread(target=_run_batch_bg, daemon=True).start()
    return {
        "success": True,
        "message": f"Đã khởi chạy sản xuất tự động {len(targets) * count_per_ch} video cho {len(targets)} kênh!",
        "state": _batch_produce_state,
    }

@app.get("/api/channels/batch-auto-produce/status")
async def get_batch_auto_produce_status():
    """Lấy trạng thái tiến trình sản xuất tự động hàng loạt trên Web."""
    return {"success": True, "state": _batch_produce_state}

