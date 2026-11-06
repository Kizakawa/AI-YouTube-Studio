"""
Thư Viện Video Đã Đăng (Published Video Library)

- Đồng bộ danh sách video đã đăng từ YouTube (uploads playlist, tốn rất ít quota)
  và từ bảng VideoJob (video do hệ thống đăng).
- Tóm tắt ngắn + gắn chủ đề bằng Gemini (batch, cache vĩnh viễn), có fallback không AI.
- Tìm kiếm, nhóm theo chủ đề, kiểm tra trùng chủ đề khi viết kịch bản mới.
"""
import json
import logging
import re
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from config.settings import settings
from app.database.models import get_db, PublishedVideo, VideoJob, normalize_channel_id

logger = logging.getLogger(__name__)

_sync_lock = threading.Lock()

_STOPWORDS = {
    "the", "and", "for", "you", "your", "with", "that", "this", "from", "are", "was", "how",
    "why", "what", "when", "who", "his", "her", "its", "not", "but", "can", "will", "have",
    "has", "had", "about", "into", "than", "then", "they", "them", "their", "one", "all",
    "video", "videos", "shorts", "short", "most", "more", "just", "like", "been", "out",
    "của", "và", "là", "một", "những", "các", "cho", "trong", "với", "này", "khi", "được",
}


# ─────────────────────────────────────────────────────────────
# Tiện ích
# ─────────────────────────────────────────────────────────────
def _tokens(text: str) -> set:
    words = re.findall(r"[a-zA-ZÀ-ỹ0-9]{3,}", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS}


def _parse_yt_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def _extract_hook_from_job(job: Optional[VideoJob], fallback_title: str = "") -> str:
    """Trích xuất câu Hook mở đầu (Cảnh 1) từ VideoJob.scenes_json hoặc script."""
    if not job:
        return fallback_title or ""
    if getattr(job, "scenes_json", None):
        try:
            s_data = json.loads(job.scenes_json)
            if isinstance(s_data, dict):
                scenes = s_data.get("scenes") or []
                if scenes and isinstance(scenes[0], dict):
                    s1_narr = (scenes[0].get("narration") or "").strip()
                    if s1_narr:
                        return s1_narr
                if s_data.get("hook"):
                    return str(s_data.get("hook")).strip()
        except Exception:
            pass
    if getattr(job, "script", None):
        first_sent = re.split(r"(?<=[\.\!\?])\s+", job.script.strip())[0]
        if first_sent:
            return first_sent[:180]
    return fallback_title or (job.title or "")


def _compute_calibrated_retention(
    views: int = 0,
    likes: int = 0,
    comments: int = 0,
    channel_avg_views: float = 150.0,
    duration_sec: float = 36.0,
    hook_text: str = "",
    raw_apv: Optional[float] = None,
    raw_stayed: Optional[float] = None,
    raw_avd: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    """
    Chỉ trả về bộ chỉ số giữ chân THẬT 100% từ YouTube Analytics API v2:
    - avg_view_percentage (tỷ lệ xem hết %)
    - stayed_to_watch_pct (tỷ lệ ở lại xem %)
    - swipe_away_pct (tỷ lệ vuốt bỏ % = 100 - stayed_to_watch_pct)
    - avg_view_duration_sec (thời lượng xem trung bình giây)
    TUYỆT ĐỐI KHÔNG ước lượng hay nội suy giả định khi YouTube Analytics API chưa chốt sổ (trả về None).
    """
    if raw_stayed is not None and float(raw_stayed) > 0:
        stayed_pct: Optional[float] = round(max(0.0, min(100.0, float(raw_stayed))), 1)
        swipe_pct: Optional[float] = round(max(0.0, 100.0 - stayed_pct), 1)
    else:
        stayed_pct = None
        swipe_pct = None

    if raw_apv is not None and float(raw_apv) > 0:
        apv_pct: Optional[float] = round(float(raw_apv), 1)
    else:
        apv_pct = None

    if raw_avd is not None and float(raw_avd) > 0:
        avg_dur_sec: Optional[float] = round(float(raw_avd), 1)
    else:
        avg_dur_sec = None

    return {
        "avg_view_percentage": apv_pct,
        "stayed_to_watch_pct": stayed_pct,
        "swipe_away_pct": swipe_pct,
        "avg_view_duration_sec": avg_dur_sec,
    }


def _fetch_yt_analytics_retention_map(creds, video_ids: List[str]) -> Dict[str, Dict[str, Optional[float]]]:
    """
    Truy vấn trực tiếp YouTube Analytics API v2 (nếu OAuth token có quyền yt-analytics.readonly)
    để lấy dữ liệu THẬT 100%: averageViewPercentage, averageViewDuration, engagedViews, views cho từng video.
    """
    if not creds or not video_ids:
        return {}
    out: Dict[str, Dict[str, Optional[float]]] = {}
    try:
        from datetime import timedelta
        from googleapiclient.discovery import build
        yt_analytics = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
        end_date = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
        resp = yt_analytics.reports().query(
            ids="channel==MINE",
            startDate="2024-01-01",
            endDate=end_date,
            metrics="views,engagedViews,averageViewDuration,averageViewPercentage",
            dimensions="video",
            sort="-views",
            maxResults=200,
        ).execute()
        for row in resp.get("rows") or []:
            if len(row) >= 5:
                v_id = str(row[0])
                v_cnt = float(row[1] or 0)
                eng_v = float(row[2] or 0)
                avd = float(row[3] or 0)
                apv = float(row[4] or 0)
                stayed = round(min(100.0, (eng_v / v_cnt) * 100.0), 1) if v_cnt > 0 and eng_v > 0 else None
                swipe = round(max(0.0, 100.0 - stayed), 1) if stayed is not None else None
                out[v_id] = {
                    "avg_view_percentage": round(apv, 1) if apv > 0 else None,
                    "avg_view_duration_sec": round(avd, 1) if avd > 0 else None,
                    "stayed_to_watch_pct": stayed,
                    "swipe_away_pct": swipe,
                }
    except Exception as e:
        logger.debug(f"[VideoLibrary] YouTube Analytics API query warning: {e}")
    return out


def _serialize(v: PublishedVideo, channel_names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    from app.database.models import normalize_channel_id
    canon_cid = normalize_channel_id(v.channel_id)
    try:
        tags = json.loads(v.topic_tags) if v.topic_tags else []
    except Exception:
        tags = []
    return {
        "id": v.id,
        "channel_id": canon_cid,
        "channel_name": (channel_names or {}).get(canon_cid, canon_cid),
        "youtube_video_id": v.youtube_video_id,
        "youtube_url": f"https://youtu.be/{v.youtube_video_id}",
        "title": v.title or "",
        "published_at": v.published_at.strftime("%Y-%m-%d") if v.published_at else "",
        "views": v.views or 0,
        "likes": getattr(v, "likes", 0) or 0,
        "comments": getattr(v, "comments", 0) or 0,
        "avg_view_percentage": getattr(v, "avg_view_percentage", None),
        "stayed_to_watch_pct": getattr(v, "stayed_to_watch_pct", None),
        "swipe_away_pct": getattr(v, "swipe_away_pct", None),
        "avg_view_duration_sec": getattr(v, "avg_view_duration_sec", None),
        "hook_text": getattr(v, "hook_text", None) or "",
        "voice_engine": getattr(v, "voice_engine", None) or "",
        "source": v.source,
        "summary": v.summary or "",
        "topic_tags": tags,
        "topic_cluster": v.topic_cluster or "",
    }


def _channel_names() -> Dict[str, str]:
    try:
        return {c.get("id"): c.get("name") for c in settings.get_channels_config()}
    except Exception:
        return {}


def _fallback_summary(title: str, description: str) -> str:
    desc = re.sub(r"https?://\S+|#\w+", "", description or "")
    desc = re.sub(r"\s+", " ", desc).strip()
    return (desc[:220] + "…") if len(desc) > 220 else (desc or (title or ""))


# ─────────────────────────────────────────────────────────────
# Đồng bộ
# ─────────────────────────────────────────────────────────────
def backfill_from_jobs() -> int:
    """
    Nạp các VideoJob đã có youtube_video_id vào thư viện và đồng bộ hai chiều
    chỉ số giữ chân thật (avg_view_percentage, stayed_to_watch_pct, swipe_away_pct, hook_text, voice_engine).
    """
    from app.database.models import normalize_channel_id
    added = 0
    with get_db() as db:
        jobs = db.query(VideoJob).filter(
            VideoJob.youtube_video_id.isnot(None), VideoJob.status == "PUBLISHED"
        ).all()
        job_by_vid = {(j.youtube_video_id or "").strip(): j for j in jobs if (j.youtube_video_id or "").strip()}

        for vid, j in job_by_vid.items():
            canon_cid = normalize_channel_id(j.target_channel_id)
            j.target_channel_id = canon_cid
            hook_txt = _extract_hook_from_job(j, j.title or j.topic)
            existing = db.query(PublishedVideo).filter(PublishedVideo.youtube_video_id == vid).first()
            if existing:
                existing.channel_id = canon_cid
                if not existing.job_id:
                    existing.job_id = j.id
                if not existing.hook_text and hook_txt:
                    existing.hook_text = hook_txt
                if not existing.voice_engine and j.voice_engine:
                    existing.voice_engine = j.voice_engine
                continue
            db.add(PublishedVideo(
                channel_id=canon_cid,
                youtube_video_id=vid,
                title=j.title or j.topic,
                description=j.description or (j.script or "")[:600],
                published_at=j.updated_at or j.created_at,
                views=j.views or 0,
                likes=j.likes or 0,
                comments=j.comments or 0,
                avg_view_percentage=j.avg_view_percentage,
                stayed_to_watch_pct=j.stayed_to_watch_pct,
                swipe_away_pct=j.swipe_away_pct,
                avg_view_duration_sec=j.avg_view_duration_sec,
                hook_text=hook_txt,
                voice_engine=j.voice_engine,
                source="system",
                job_id=j.id,
            ))
            added += 1

        db.flush()

        # Đọc dữ liệu YouTube Analytics v2 thực tế đã thu thập trong storage/channel_audit_latest.json (nếu có)
        audit_vid_map: Dict[str, Dict[str, Any]] = {}
        try:
            from pathlib import Path as _Path
            audit_p = _Path(settings.BASE_DIR) / "storage" / "channel_audit_latest.json"
            if audit_p.exists():
                audit_json = json.loads(audit_p.read_text(encoding="utf-8", errors="ignore"))
                if isinstance(audit_json, dict):
                    for _ck, ch_obj in audit_json.items():
                        if not isinstance(ch_obj, dict):
                            continue
                        for list_key in ("videos", "top_3_winners", "bottom_3_losers"):
                            for v_item in (ch_obj.get(list_key) or []):
                                if not isinstance(v_item, dict):
                                    continue
                                vid_key = str(v_item.get("id") or v_item.get("video_id") or "").strip()
                                if vid_key and (v_item.get("stay_pct") or v_item.get("apv_pct")):
                                    audit_vid_map[vid_key] = v_item
        except Exception as _e_aud:
            logger.debug(f"[VideoLibrary] Không đọc được channel_audit_latest.json: {_e_aud}")

        # Tính toán & điền đầy đủ tỷ lệ xem hết / ở lại / vuốt bỏ cho tất cả PublishedVideo & VideoJob của từng kênh
        all_pvs = db.query(PublishedVideo).all()
        by_ch: Dict[str, List[PublishedVideo]] = {}
        for pv in all_pvs:
            c_id = normalize_channel_id(pv.channel_id)
            pv.channel_id = c_id
            by_ch.setdefault(c_id, []).append(pv)

        for c_id, pv_list in by_ch.items():
            valid_views = [p.views or 0 for p in pv_list if (p.views or 0) > 0]
            ch_avg = (sum(valid_views) / len(valid_views)) if valid_views else 100.0
            for pv in pv_list:
                linked_job = db.query(VideoJob).filter(VideoJob.id == pv.job_id).first() if pv.job_id else job_by_vid.get(pv.youtube_video_id)
                if linked_job and not pv.job_id:
                    pv.job_id = linked_job.id
                if linked_job and linked_job.status == "SCHEDULED" and (pv.views or 0) == 0:
                    pv.avg_view_percentage = None
                    pv.stayed_to_watch_pct = None
                    pv.swipe_away_pct = None
                    pv.avg_view_duration_sec = None
                    linked_job.avg_view_percentage = None
                    linked_job.stayed_to_watch_pct = None
                    linked_job.swipe_away_pct = None
                    linked_job.avg_view_duration_sec = None
                    continue
                hook_txt = pv.hook_text or _extract_hook_from_job(linked_job, pv.title or "")
                if hook_txt and not pv.hook_text:
                    pv.hook_text = hook_txt
                dur_sec = (linked_job.audio_duration_ms / 1000.0) if (linked_job and linked_job.audio_duration_ms) else 36.0
                real_audit = audit_vid_map.get(pv.youtube_video_id, {})
                if real_audit.get("views") is not None and int(real_audit.get("views") or 0) > (pv.views or 0):
                    pv.views = int(real_audit["views"])
                if real_audit.get("likes") is not None and int(real_audit.get("likes") or 0) > (pv.likes or 0):
                    pv.likes = int(real_audit["likes"])
                if real_audit.get("comments") is not None and int(real_audit.get("comments") or 0) > (pv.comments or 0):
                    pv.comments = int(real_audit["comments"])
                if real_audit:
                    cal = _compute_calibrated_retention(
                        views=pv.views or 0,
                        likes=pv.likes or 0,
                        comments=pv.comments or 0,
                        channel_avg_views=ch_avg,
                        duration_sec=dur_sec,
                        hook_text=hook_txt,
                        raw_apv=real_audit.get("apv_pct"),
                        raw_stayed=real_audit.get("stay_pct"),
                        raw_avd=real_audit.get("avd_sec"),
                    )
                    pv.avg_view_percentage = cal["avg_view_percentage"]
                    pv.stayed_to_watch_pct = cal["stayed_to_watch_pct"]
                    pv.swipe_away_pct = cal["swipe_away_pct"]
                    pv.avg_view_duration_sec = cal["avg_view_duration_sec"]

                if linked_job:
                    linked_job.views = pv.views or 0
                    linked_job.likes = pv.likes or 0
                    linked_job.comments = pv.comments or 0
                    linked_job.avg_view_percentage = pv.avg_view_percentage
                    linked_job.stayed_to_watch_pct = pv.stayed_to_watch_pct
                    linked_job.swipe_away_pct = pv.swipe_away_pct
                    linked_job.avg_view_duration_sec = pv.avg_view_duration_sec
                    if linked_job.voice_engine and not pv.voice_engine:
                        pv.voice_engine = linked_job.voice_engine

    try:
        from app.services.video_memory_rag import get_video_memory_rag
        get_video_memory_rag().sync_all_from_db()
    except Exception as ex_rag:
        logger.debug(f"[VideoLibrary] Mini-RAG sync warning: {ex_rag}")
    return added


def register_published_job(job_id: int, channel_id: str, youtube_video_id: str,
                           title: str = "", description: str = "") -> None:
    """Gọi ngay sau khi hệ thống đăng thành công để thư viện luôn mới (không tốn quota)."""
    if not youtube_video_id:
        return
    from app.database.models import normalize_channel_id
    canon_cid = normalize_channel_id(channel_id)
    try:
        with get_db() as db:
            if db.query(PublishedVideo).filter(PublishedVideo.youtube_video_id == youtube_video_id).first():
                return
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first() if job_id else None
            hook_txt = _extract_hook_from_job(job, title)
            db.add(PublishedVideo(
                channel_id=canon_cid, youtube_video_id=youtube_video_id,
                title=title, description=description, published_at=datetime.utcnow(),
                hook_text=hook_txt,
                voice_engine=job.voice_engine if job else None,
                source="system", job_id=job_id,
            ))
    except Exception as e:
        logger.warning(f"[VideoLibrary] register_published_job lỗi: {e}")


def sync_channel(channel_cfg: Dict[str, Any], max_videos: int = 200) -> Dict[str, Any]:
    """Đồng bộ video công khai/unlisted + số liệu giữ chân thật (APV, Stayed, Swipe-Away) của 1 kênh từ YouTube."""
    from app.database.models import normalize_channel_id
    ch_id = normalize_channel_id(channel_cfg.get("id", ""))
    result = {"channel_id": ch_id, "ok": False, "added": 0, "updated": 0, "error": ""}
    try:
        from app.agents.publisher_agent import PublisherAgent
        agent = PublisherAgent(channel_config=channel_cfg)
        yt = agent.service
        if not yt:
            result["error"] = agent.init_error or "Chưa có token YouTube hợp lệ (cần cấp quyền lại)"
            return result

        ch_resp = yt.channels().list(part="contentDetails", mine=True).execute()
        items = ch_resp.get("items") or []
        if not items:
            result["error"] = "Token không trỏ tới kênh YouTube nào"
            return result
        uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        video_ids: List[str] = []
        page_token = None
        while len(video_ids) < max_videos:
            resp = yt.playlistItems().list(
                part="contentDetails", playlistId=uploads, maxResults=50, pageToken=page_token
            ).execute()
            video_ids += [i["contentDetails"]["videoId"] for i in resp.get("items", [])]
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        details = []
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            r = yt.videos().list(part="snippet,statistics,status", id=",".join(batch)).execute()
            details += r.get("items", [])

        analytics_map = _fetch_yt_analytics_retention_map(getattr(agent, "creds", None), video_ids)

        public_details = [
            d for d in details
            if (d.get("status", {}).get("privacyStatus") or "") == "public"
        ]
        public_ids = {d["id"] for d in public_details}
        all_views = [
            int(d.get("statistics", {}).get("viewCount", 0) or 0)
            for d in public_details
            if int(d.get("statistics", {}).get("viewCount", 0) or 0) > 0
        ]
        ch_avg_views = (sum(all_views) / len(all_views)) if all_views else 100.0

        with get_db() as db:
            if public_ids:
                db.query(PublishedVideo).filter(
                    PublishedVideo.channel_id == ch_id,
                    ~PublishedVideo.youtube_video_id.in_(public_ids),
                ).delete(synchronize_session=False)
            for d in public_details:
                vid = d["id"]
                sn = d.get("snippet", {})
                stats = d.get("statistics", {}) or {}
                views = int(stats.get("viewCount", 0) or 0)
                likes = int(stats.get("likeCount", 0) or 0)
                comments = int(stats.get("commentCount", 0) or 0)
                an_data = analytics_map.get(vid, {})

                job = db.query(VideoJob).filter(VideoJob.youtube_video_id == vid).first()
                hook_txt = _extract_hook_from_job(job, sn.get("title") or "")
                dur_sec = (job.audio_duration_ms / 1000.0) if (job and job.audio_duration_ms) else 36.0
                cal = _compute_calibrated_retention(
                    views=views,
                    likes=likes,
                    comments=comments,
                    channel_avg_views=ch_avg_views,
                    duration_sec=dur_sec,
                    hook_text=hook_txt,
                    raw_apv=an_data.get("avg_view_percentage"),
                    raw_stayed=an_data.get("stayed_to_watch_pct"),
                    raw_avd=an_data.get("avg_view_duration_sec"),
                )

                row = db.query(PublishedVideo).filter(PublishedVideo.youtube_video_id == vid).first()
                if row:
                    row.title = sn.get("title") or row.title
                    row.description = sn.get("description") or row.description
                    row.views = views
                    row.likes = likes
                    row.comments = comments
                    row.avg_view_percentage = cal["avg_view_percentage"]
                    row.stayed_to_watch_pct = cal["stayed_to_watch_pct"]
                    row.swipe_away_pct = cal["swipe_away_pct"]
                    row.avg_view_duration_sec = cal["avg_view_duration_sec"]
                    if hook_txt and not row.hook_text:
                        row.hook_text = hook_txt
                    if job and job.voice_engine and not row.voice_engine:
                        row.voice_engine = job.voice_engine
                    row.channel_id = ch_id
                    row.last_synced_at = datetime.utcnow()
                    result["updated"] += 1
                else:
                    db.add(PublishedVideo(
                        channel_id=ch_id, youtube_video_id=vid,
                        title=sn.get("title"), description=sn.get("description"),
                        published_at=_parse_yt_time(sn.get("publishedAt")),
                        views=views, likes=likes, comments=comments,
                        avg_view_percentage=cal["avg_view_percentage"],
                        stayed_to_watch_pct=cal["stayed_to_watch_pct"],
                        swipe_away_pct=cal["swipe_away_pct"],
                        avg_view_duration_sec=cal["avg_view_duration_sec"],
                        hook_text=hook_txt,
                        voice_engine=job.voice_engine if job else None,
                        source="system" if job else "youtube_sync",
                        job_id=job.id if job else None,
                    ))
                    result["added"] += 1

                if job:
                    job.target_channel_id = ch_id
                    if job.status == "SCHEDULED":
                        job.status = "PUBLISHED"
                    job.views = views
                    job.likes = likes
                    job.comments = comments
                    job.avg_view_percentage = cal["avg_view_percentage"]
                    job.stayed_to_watch_pct = cal["stayed_to_watch_pct"]
                    job.swipe_away_pct = cal["swipe_away_pct"]
                    job.avg_view_duration_sec = cal["avg_view_duration_sec"]
        result["ok"] = True
    except Exception as e:
        logger.warning(f"[VideoLibrary] sync_channel({ch_id}) lỗi: {e}")
        result["error"] = str(e)[:300]
    return result


def summarize_pending(limit: int = 30) -> int:
    """Tóm tắt (AI, batch) các video chưa có tóm tắt AI. Hết quota → dùng fallback."""
    with get_db() as db:
        rows = db.query(PublishedVideo).filter(PublishedVideo.summary_is_ai == 0).limit(limit).all()
        payload = [{"id": r.id, "title": r.title or "", "description": (r.description or "")[:500]} for r in rows]
    if not payload:
        return 0

    ai_map: Dict[int, Dict[str, Any]] = {}
    chunk_size = 10
    try:
        from app.agents.content_agent import ContentAgent
        agent = ContentAgent()
        for i in range(0, len(payload), chunk_size):
            chunk = payload[i:i + chunk_size]
            prompt = (
                "Bạn là biên tập viên kênh YouTube Shorts. Với MỖI video dưới đây hãy trả về:\n"
                "- summary: tóm tắt nội dung 1-2 câu NGẮN bằng TIẾNG VIỆT\n"
                "- tags: 3-5 từ khóa chủ đề bằng TIẾNG ANH, chữ thường\n"
                "- cluster: tên nhóm chủ đề ngắn (tiếng Anh, 2-4 từ)\n"
                "Chỉ trả về JSON hợp lệ dạng: "
                '{"items":[{"id":<id>,"summary":"...","tags":["..."],"cluster":"..."}]}\n\n'
                f"VIDEOS:\n{json.dumps(chunk, ensure_ascii=False)}"
            )
            raw = agent._call_api(prompt, json_mode=True)
            m = re.search(r"\{.*\}", raw or "", re.DOTALL)
            if not m:
                continue
            for it in json.loads(m.group(0)).get("items", []):
                try:
                    ai_map[int(it["id"])] = it
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"[VideoLibrary] Tóm tắt AI lỗi, dùng fallback: {e}")

    done = 0
    with get_db() as db:
        for p in payload:
            r = db.query(PublishedVideo).filter(PublishedVideo.id == p["id"]).first()
            if not r:
                continue
            it = ai_map.get(p["id"])
            if it and it.get("summary"):
                r.summary = str(it["summary"]).strip()
                r.topic_tags = json.dumps([str(t).lower() for t in (it.get("tags") or [])][:5], ensure_ascii=False)
                r.topic_cluster = str(it.get("cluster") or "")[:150]
                r.summary_is_ai = 1
            elif not r.summary:
                r.summary = _fallback_summary(r.title, r.description)
                r.topic_tags = json.dumps(sorted(_tokens(r.title))[:5], ensure_ascii=False)
            done += 1
    return done


def sync_all(summarize: bool = True) -> Dict[str, Any]:
    """Đồng bộ toàn bộ kênh. Kênh lỗi token không làm hỏng kênh khác."""
    if not _sync_lock.acquire(blocking=False):
        return {"ok": False, "error": "Đang có một lần đồng bộ khác chạy", "channels": []}
    try:
        backfilled = backfill_from_jobs()
        results = [sync_channel(c) for c in settings.get_channels_config() if c.get("id")]
        summarized = summarize_pending() if summarize else 0
        return {"ok": True, "backfilled": backfilled, "summarized": summarized, "channels": results}
    finally:
        _sync_lock.release()


# Cache hàng tuần (ISO Year-Week) cho Top 3 Winners & Bottom 3 Losers của từng kênh
_WEEKLY_PERFORMANCE_CACHE: Dict[str, Dict[str, Any]] = {}



def get_weekly_winners_and_losers(channel_id: str, top_k: int = 3, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Trích xuất tự động mỗi tuần:
    - Top 3 video thắng (Winners): Điểm tổng hợp cao nhất từ Views, Tỷ lệ ở lại xem (Stayed %) & Tỷ lệ xem hết (APV %).
    - Bottom 3 video thua (Losers): Các video đã đăng có lượt xem thấp nhất / tỷ lệ vuốt bỏ (Swipe Away %) cao nhất.
    - Thống kê trung bình của kênh (avg_views, avg_stayed_pct, avg_swipe_away_pct, avg_apv_pct) để hiệu chỉnh ngưỡng chấm Hook.
    """
    from app.database.models import normalize_channel_id
    canon_cid = normalize_channel_id(channel_id)
    iso_year, iso_week, _ = datetime.utcnow().isocalendar()
    cache_key = f"{canon_cid}:{iso_year}-W{iso_week:02d}:{top_k}"

    if not force_refresh and cache_key in _WEEKLY_PERFORMANCE_CACHE:
        return _WEEKLY_PERFORMANCE_CACHE[cache_key]

    winners: List[Dict[str, Any]] = []
    losers: List[Dict[str, Any]] = []
    avg_views = 0.0
    avg_stayed = 70.0
    avg_swipe = 30.0
    avg_apv = 85.0

    try:
        with get_db() as db:
            rows = (
                db.query(PublishedVideo)
                .filter(PublishedVideo.channel_id == canon_cid)
                .order_by(PublishedVideo.published_at.desc())
                .all()
            )
            records: List[Dict[str, Any]] = []
            for r in rows:
                records.append({
                    "youtube_video_id": r.youtube_video_id,
                    "title": (r.title or "").strip(),
                    "hook_text": (r.hook_text or r.title or "").strip(),
                    "views": int(r.views or 0),
                    "likes": int(r.likes or 0),
                    "comments": int(r.comments or 0),
                    "avg_view_percentage": float(r.avg_view_percentage) if r.avg_view_percentage is not None else None,
                    "stayed_to_watch_pct": float(r.stayed_to_watch_pct) if r.stayed_to_watch_pct is not None else None,
                    "swipe_away_pct": float(r.swipe_away_pct) if r.swipe_away_pct is not None else None,
                    "voice_engine": r.voice_engine or "",
                })

            if records:
                avg_views = round(sum(x["views"] for x in records) / len(records), 1)
                real_stayed = [x["stayed_to_watch_pct"] for x in records if x["stayed_to_watch_pct"] is not None]
                real_swipe = [x["swipe_away_pct"] for x in records if x["swipe_away_pct"] is not None]
                real_apv = [x["avg_view_percentage"] for x in records if x["avg_view_percentage"] is not None]
                if real_stayed:
                    avg_stayed = round(sum(real_stayed) / len(real_stayed), 1)
                if real_swipe:
                    avg_swipe = round(sum(real_swipe) / len(real_swipe), 1)
                elif real_stayed:
                    avg_swipe = round(max(0.0, 100.0 - avg_stayed), 1)
                if real_apv:
                    avg_apv = round(sum(real_apv) / len(real_apv), 1)

                # Xếp hạng theo dữ liệu thật: Views + Stayed% (nếu đã chốt sổ Analytics) + APV%
                scored = sorted(
                    records,
                    key=lambda x: (
                        x["views"],
                        x["stayed_to_watch_pct"] if x["stayed_to_watch_pct"] is not None else -1.0,
                        x["avg_view_percentage"] if x["avg_view_percentage"] is not None else -1.0,
                    ),
                    reverse=True,
                )
                winners = scored[:top_k]
                winner_ids = {w["youtube_video_id"] for w in winners}
                remaining = [x for x in reversed(scored) if x["youtube_video_id"] not in winner_ids]
                losers = remaining[:top_k]
    except Exception as e:
        logger.debug(f"[VideoLibrary] get_weekly_winners_and_losers({canon_cid}) warning: {e}")

    auditor_insights = ""
    avg_avd_sec = 25.0
    try:
        from pathlib import Path
        audit_path = Path(settings.BASE_DIR) / "storage" / "channel_audit_latest.json"
        if audit_path.exists():
            audit_json = json.loads(audit_path.read_text(encoding="utf-8"))
            ch_audit = audit_json.get(canon_cid) if isinstance(audit_json, dict) else None
            if ch_audit and isinstance(ch_audit, dict):
                auditor_insights = str(ch_audit.get("ai_insights") or "").strip()
                ya = ch_audit.get("analytics") or {}
                if ya.get("has_data"):
                    if ya.get("avd_sec"):
                        avg_avd_sec = float(ya["avd_sec"])
                    if ya.get("apv_pct"):
                        avg_apv = float(ya["apv_pct"])
                    if ya.get("stay_pct"):
                        avg_stayed = float(ya["stay_pct"])
                        avg_swipe = float(ya.get("swipe_pct") or (100.0 - avg_stayed))
    except Exception:
        pass

    def _fmt_rec_metrics(rec: Dict[str, Any]) -> str:
        if rec.get("stayed_to_watch_pct") is not None and rec.get("avg_view_percentage") is not None:
            return (
                f"{rec['views']:,} views | Stayed: {rec['stayed_to_watch_pct']:.1f}% | "
                f"Swipe-Away: {rec.get('swipe_away_pct', round(100.0 - rec['stayed_to_watch_pct'], 1)):.1f}% | "
                f"APV: {rec['avg_view_percentage']:.1f}%"
            )
        return f"{rec['views']:,} views | ⏳ Analytics đang chốt sổ 24-48h đầu"

    prompt_lines = [
        f"📊 LIVE CHANNEL RETENTION BASELINE ({canon_cid} — {iso_year}-W{iso_week:02d}): "
        f"Avg Views: {avg_views:,.0f} | Stayed-to-Watch: {avg_stayed:.1f}% | "
        f"Swipe-Away: {avg_swipe:.1f}% | Avg Completion (APV): {avg_apv:.1f}% | Avg AVD: {avg_avd_sec:.1f}s"
    ]
    if winners:
        prompt_lines.append("🏆 TOP 3 PROVEN WINNERS FROM DB (Replicate their hook structure & curiosity mechanism):")
        for idx, w in enumerate(winners[:top_k], 1):
            prompt_lines.append(
                f"  #{idx}. \"{w['title']}\" | Hook: \"{w.get('hook_text', '')[:110]}\" -> {_fmt_rec_metrics(w)}"
            )
    if losers:
        prompt_lines.append("⚠️ BOTTOM 3 LOSERS FROM DB (High Swipe-Away — Avoid their slow opening & fatigued angle):")
        for idx, l in enumerate(losers[:top_k], 1):
            prompt_lines.append(
                f"  #{idx}. \"{l['title']}\" | Hook: \"{l.get('hook_text', '')[:110]}\" -> {_fmt_rec_metrics(l)}"
            )

    res = {
        "channel_id": canon_cid,
        "week_key": f"{iso_year}-W{iso_week:02d}",
        "winners": winners,
        "losers": losers,
        "avg_views": avg_views,
        "avg_stayed_to_watch_pct": avg_stayed,
        "avg_swipe_away_pct": avg_swipe,
        "avg_view_percentage": avg_apv,
        "avg_view_duration_sec": avg_avd_sec,
        "auditor_ai_insights": auditor_insights,
        "prompt_block": "\n".join(prompt_lines),
    }
    _WEEKLY_PERFORMANCE_CACHE[cache_key] = res
    return res



# ─────────────────────────────────────────────────────────────
# Truy vấn
# ─────────────────────────────────────────────────────────────
def list_videos(channel_id: str = "", q: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    names = _channel_names()
    norm_cid = normalize_channel_id(channel_id) if channel_id else ""
    with get_db() as db:
        query = db.query(PublishedVideo)
        if norm_cid:
            if norm_cid != channel_id:
                query = query.filter(PublishedVideo.channel_id.in_([channel_id, norm_cid]))
            else:
                query = query.filter(PublishedVideo.channel_id == norm_cid)
        rows = query.order_by(PublishedVideo.published_at.desc()).all()
        out = [_serialize(r, names) for r in rows]
    if q:
        qt = _tokens(q)
        ql = q.lower().strip()

        def hit(v):
            hay = " ".join([v["title"], v["summary"], v["topic_cluster"], " ".join(v["topic_tags"])]).lower()
            return ql in hay or (qt and len(qt & _tokens(hay)) > 0)
        out = [v for v in out if hit(v)]
    return out[:limit]


def topics_overview(channel_id: str = "") -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for v in list_videos(channel_id=channel_id, limit=1000):
        groups.setdefault(v["topic_cluster"] or "Chưa phân loại", []).append(v)
    return sorted(
        [{"cluster": k, "count": len(vs), "titles": [x["title"] for x in vs[:5]]} for k, vs in groups.items()],
        key=lambda g: -g["count"],
    )


def check_duplicate(channel_id: str, topic: str, threshold: float = 0.34) -> List[Dict[str, Any]]:
    """
    Trả về các video đã đăng có chủ đề / kịch bản / hình ảnh gần giống `topic`
    bằng cách ưu tiên truy vấn tức thời (0ms) trên hệ thống Mini-RAG (`VideoMemoryRAG`)
    kết hợp với bảng `PublishedVideo`.
    """
    channel_id = normalize_channel_id(channel_id) if channel_id else ""
    tq = _tokens(topic)
    if not tq:
        return []
    matches_by_id: Dict[str, Dict[str, Any]] = {}
    names = _channel_names()

    # 1. Truy vấn ngữ nghĩa siêu tốc từ Mini-RAG (truy xuất cả nội dung kịch bản full_script, hook_text & visual_metadata)
    try:
        from app.services.video_memory_rag import get_video_memory_rag
        rag_hits = get_video_memory_rag().query_similar(
            query_text=topic,
            channel_id=channel_id if channel_id else None,
            top_k=5,
            min_score=max(0.24, threshold * 0.78),
        )
        for hit in rag_hits:
            yt_id = hit.get("youtube_video_id") or ""
            key = yt_id or f"rag_{hit.get('doc_id')}"
            sim_score = round(float(hit.get("similarity_score", 0.0)), 2)
            cid = hit.get("channel_id") or channel_id
            matches_by_id[key] = {
                "id": hit.get("job_id") or 0,
                "channel_id": cid,
                "channel_name": names.get(cid, cid),
                "youtube_video_id": yt_id,
                "youtube_url": f"https://youtu.be/{yt_id}" if yt_id else "",
                "title": hit.get("title") or hit.get("topic") or "",
                "published_at": str(hit.get("published_at") or "")[:10],
                "views": int(hit.get("views") or 0),
                "hook_text": hit.get("hook_text") or "",
                "summary": (hit.get("full_script") or "")[:180],
                "topic_tags": [
                    vm.get("visual_keyword") for vm in (hit.get("visual_metadata") or []) if vm.get("visual_keyword")
                ][:5] or hit.get("visual_keywords_used", [])[:5],
                "topic_cluster": "Mini-RAG Script & Visual Memory",
                "similarity": sim_score,
            }
    except Exception as ex_rag:
        logger.debug(f"[VideoLibrary] check_duplicate Mini-RAG warning: {ex_rag}")

    # 2. Bổ sung kiểm tra trên PublishedVideo (nếu có bản ghi mới chưa kịp nạp vào RAG)
    try:
        for v in list_videos(channel_id=channel_id, limit=1000):
            vt = _tokens(" ".join([v["title"], v["topic_cluster"], " ".join(v["topic_tags"]), v["summary"], v.get("hook_text", "")]))
            if not vt:
                continue
            inter = len(tq & vt)
            score = round(inter / max(1, len(tq)), 2)
            if inter >= 2 and score >= threshold:
                key = v.get("youtube_video_id") or f"id_{v.get('id')}"
                if key in matches_by_id:
                    if score > matches_by_id[key]["similarity"]:
                        matches_by_id[key]["similarity"] = score
                else:
                    matches_by_id[key] = {**v, "similarity": score}
    except Exception as ex_db:
        logger.debug(f"[VideoLibrary] check_duplicate DB fallback warning: {ex_db}")

    matches = sorted(matches_by_id.values(), key=lambda m: -m["similarity"])
    return matches[:5]


def format_library_text(videos: List[Dict[str, Any]], title: str = "", max_items: int = 15) -> str:
    """Định dạng văn bản thuần cho Telegram."""
    if not videos:
        return "📭 Chưa có video nào trong thư viện. Gõ /library sync để đồng bộ từ YouTube."
    lines = [title] if title else []
    by_channel: Dict[str, List[Dict[str, Any]]] = {}
    for v in videos[:max_items]:
        by_channel.setdefault(v["channel_name"], []).append(v)
    for ch, vs in by_channel.items():
        lines.append(f"\n📺 {ch} ({len(vs)} video)")
        for v in vs:
            cl = f" [{v['topic_cluster']}]" if v["topic_cluster"] else ""
            lines.append(f"• {v['title']}{cl}\n   {v['published_at']} · 👁 {v['views']}\n   📝 {v['summary'] or '—'}\n   🔗 {v['youtube_url']}")
    if len(videos) > max_items:
        lines.append(f"\n… và {len(videos) - max_items} video khác (dùng Web để xem đầy đủ).")
    return "\n".join(lines)
