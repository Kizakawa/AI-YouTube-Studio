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
from app.database.models import get_db, PublishedVideo, VideoJob

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


def _serialize(v: PublishedVideo, channel_names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    try:
        tags = json.loads(v.topic_tags) if v.topic_tags else []
    except Exception:
        tags = []
    return {
        "id": v.id,
        "channel_id": v.channel_id,
        "channel_name": (channel_names or {}).get(v.channel_id, v.channel_id),
        "youtube_video_id": v.youtube_video_id,
        "youtube_url": f"https://youtu.be/{v.youtube_video_id}",
        "title": v.title or "",
        "published_at": v.published_at.strftime("%Y-%m-%d") if v.published_at else "",
        "views": v.views or 0,
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
    """Nạp các VideoJob đã có youtube_video_id vào thư viện (không gọi YouTube)."""
    added = 0
    with get_db() as db:
        jobs = db.query(VideoJob).filter(
            VideoJob.youtube_video_id.isnot(None), VideoJob.status == "PUBLISHED"
        ).all()
        for j in jobs:
            vid = (j.youtube_video_id or "").strip()
            if not vid:
                continue
            existing = db.query(PublishedVideo).filter(PublishedVideo.youtube_video_id == vid).first()
            if existing:
                if not existing.job_id:
                    existing.job_id = j.id
                continue
            db.add(PublishedVideo(
                channel_id=j.target_channel_id,
                youtube_video_id=vid,
                title=j.title or j.topic,
                description=j.description or (j.script or "")[:600],
                published_at=j.updated_at or j.created_at,
                source="system",
                job_id=j.id,
            ))
            added += 1
    return added


def register_published_job(job_id: int, channel_id: str, youtube_video_id: str,
                           title: str = "", description: str = "") -> None:
    """Gọi ngay sau khi hệ thống đăng thành công để thư viện luôn mới (không tốn quota)."""
    if not youtube_video_id:
        return
    try:
        with get_db() as db:
            if db.query(PublishedVideo).filter(PublishedVideo.youtube_video_id == youtube_video_id).first():
                return
            db.add(PublishedVideo(
                channel_id=channel_id, youtube_video_id=youtube_video_id,
                title=title, description=description, published_at=datetime.utcnow(),
                source="system", job_id=job_id,
            ))
    except Exception as e:
        logger.warning(f"[VideoLibrary] register_published_job lỗi: {e}")


def sync_channel(channel_cfg: Dict[str, Any], max_videos: int = 200) -> Dict[str, Any]:
    """Đồng bộ video công khai/unlisted của 1 kênh từ YouTube."""
    ch_id = channel_cfg.get("id")
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

        with get_db() as db:
            for d in details:
                if (d.get("status", {}).get("privacyStatus") or "") == "private":
                    continue  # bỏ video riêng tư/đang lên lịch
                vid = d["id"]
                sn = d.get("snippet", {})
                views = int(d.get("statistics", {}).get("viewCount", 0) or 0)
                row = db.query(PublishedVideo).filter(PublishedVideo.youtube_video_id == vid).first()
                if row:
                    row.title = sn.get("title") or row.title
                    row.description = sn.get("description") or row.description
                    row.views = views
                    row.channel_id = ch_id
                    row.last_synced_at = datetime.utcnow()
                    result["updated"] += 1
                else:
                    job = db.query(VideoJob).filter(VideoJob.youtube_video_id == vid).first()
                    db.add(PublishedVideo(
                        channel_id=ch_id, youtube_video_id=vid,
                        title=sn.get("title"), description=sn.get("description"),
                        published_at=_parse_yt_time(sn.get("publishedAt")), views=views,
                        source="system" if job else "youtube_sync",
                        job_id=job.id if job else None,
                    ))
                    result["added"] += 1
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


# ─────────────────────────────────────────────────────────────
# Truy vấn
# ─────────────────────────────────────────────────────────────
def list_videos(channel_id: str = "", q: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    names = _channel_names()
    with get_db() as db:
        query = db.query(PublishedVideo)
        if channel_id:
            query = query.filter(PublishedVideo.channel_id == channel_id)
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
    """Trả về các video đã đăng có chủ đề gần giống `topic` (chỉ cảnh báo, không chặn)."""
    tq = _tokens(topic)
    if not tq:
        return []
    matches = []
    for v in list_videos(channel_id=channel_id, limit=1000):
        vt = _tokens(" ".join([v["title"], v["topic_cluster"], " ".join(v["topic_tags"]), v["summary"]]))
        if not vt:
            continue
        inter = len(tq & vt)
        score = inter / max(1, len(tq))          # tỉ lệ từ khóa của topic mới đã xuất hiện
        if inter >= 2 and score >= threshold:
            matches.append({**v, "similarity": round(score, 2)})
    matches.sort(key=lambda m: -m["similarity"])
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
