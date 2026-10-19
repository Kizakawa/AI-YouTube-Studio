"""
app.services.video_memory_rag — Hệ thống Mini-RAG Lưu Trữ Kịch Bản & Metadata Hình Ảnh Siêu Nhẹ.

Thay vì giữ lại toàn bộ file video (.mp4 nặng 30-50MB/video) và kho ảnh/clip thô trên ổ đĩa sau khi đã đăng,
hệ thống Mini-RAG này chỉ lưu trữ:
1. Nội dung & Kịch bản (`title`, `topic`, `hook_text`, `full_script`, `summary`, `entities`).
2. Metadata Hình ảnh & Cảnh quay (`visual_metadata`: `scene_id`, `narration`, `visual_keyword`,
   `stock_query`, `visual_prompt`, `media_type`, `media_fingerprint`, `camera_motion`, `visual_strategy`).
3. Chỉ số hiệu suất thật (`views`, `avg_view_percentage`, `stayed_to_watch_pct`, `swipe_away_pct`, `avg_view_duration_sec`).

Ưu điểm:
- Dung lượng siêu nhẹ (~2-4 KB/video thay vì 30-50 MB/video).
- Truy xuất & kiểm tra trùng lặp ngữ nghĩa + từ khóa (Hybrid BM25 + N-gram Cosine Similarity) trong < 3ms,
  kiểm tra sâu cả trong lời dẫn kịch bản (`full_script`) lẫn metadata hình ảnh (`visual_metadata`).
- Tự động dọn dẹp (`slim_storage_and_archive_to_rag`) toàn bộ file `.mp4` đã đăng, file tạm (`storage/temp`)
  và bộ nhớ đệm cũ (`storage/cache`), giải phóng hàng GB dung lượng ổ cứng.
"""

import json
import logging
import math
import os
import re
import threading
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from config.settings import settings
from app.database.models import get_db, VideoJob, PublishedVideo, normalize_channel_id

logger = logging.getLogger(__name__)

_RAG_LOCK = threading.RLock()
_RAG_INDEX_PATH = Path(settings.BASE_DIR) / "storage" / "video_memory_rag.json"

_STOP_WORDS: Set[str] = {
    "the", "and", "for", "you", "your", "with", "that", "this", "from", "are", "was", "how",
    "why", "what", "when", "who", "his", "her", "its", "not", "but", "can", "will", "have",
    "has", "had", "about", "into", "than", "then", "they", "them", "their", "one", "all",
    "video", "videos", "shorts", "short", "most", "more", "just", "like", "been", "out",
    "were", "would", "could", "should", "there", "where", "which", "while", "after", "before",
    "uma", "para", "com", "dos", "das", "nos", "nas", "por", "que", "mais", "como", "isso",
    "của", "và", "là", "một", "những", "các", "cho", "trong", "với", "này", "khi", "được",
}


def _tokenize_rag(text: str) -> List[str]:
    """Tách token chuẩn hóa (1-gram + 2-gram quan trọng) phục vụ Hybrid BM25 + Cosine."""
    if not text:
        return []
    clean = text.lower()
    clean = re.sub(r"#\w+", " ", clean)
    clean = re.sub(r"(\d+)\s*,\s*(\d+)", r"\1\2", clean)
    clean = re.sub(r"[^\w\s]", " ", clean, flags=re.UNICODE)
    words = [w.strip() for w in clean.split() if len(w.strip()) >= 2 and w.strip() not in _STOP_WORDS]
    bigrams = [f"{words[i]}_{words[i+1]}" for i in range(len(words) - 1)]
    return words + bigrams


def _extract_media_fingerprint(media_path: Optional[str]) -> str:
    """Trích xuất mã định danh gọn nhẹ của file ảnh/clip (không lưu đường dẫn ổ đĩa nặng)."""
    if not media_path:
        return ""
    name = Path(str(media_path)).stem.strip()
    # Loại bỏ tiền tố job_123_ để giữ lại ID gốc của clip/ảnh (vd: pexels_12345, yt_clip_abc123)
    name = re.sub(r"^job_\d+_scene_\d+_", "", name)
    return name[:80]


def extract_visual_and_script_metadata(
    scenes_json_raw: Optional[str],
    render_manifest_raw: Optional[str] = None,
    fallback_script: Optional[str] = None,
    fallback_hook: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Trích xuất toàn bộ nội dung kịch bản + metadata hình ảnh của từng phân cảnh từ VideoJob
    thành cấu trúc JSON siêu nhẹ để nạp vào Mini-RAG.
    """
    scenes_data: List[Dict[str, Any]] = []
    pinned_comment = ""
    hook_text = (fallback_hook or "").strip()

    # 1. Ưu tiên đọc từ render_manifest (vì có đầy đủ media_path, media_type sau khi tải ảnh/video)
    parsed_scenes: List[Dict[str, Any]] = []
    for raw_src in (render_manifest_raw, scenes_json_raw):
        if not raw_src:
            continue
        try:
            obj = json.loads(raw_src) if isinstance(raw_src, str) else raw_src
            if isinstance(obj, dict):
                if not pinned_comment and obj.get("pinned_comment"):
                    pinned_comment = str(obj.get("pinned_comment")).strip()
                if not hook_text and obj.get("hook"):
                    hook_text = str(obj.get("hook")).strip()
                sc_list = obj.get("scenes") or []
                if sc_list and isinstance(sc_list, list) and not parsed_scenes:
                    parsed_scenes = [s for s in sc_list if isinstance(s, dict)]
            elif isinstance(obj, list) and not parsed_scenes:
                parsed_scenes = [s for s in obj if isinstance(s, dict)]
        except Exception:
            continue

    visual_keywords_used: List[str] = []
    media_fingerprints_used: List[str] = []
    narration_parts: List[str] = []

    for idx, sc in enumerate(parsed_scenes, start=1):
        narr = str(sc.get("narration") or sc.get("text") or "").strip()
        if narr:
            narration_parts.append(narr)
            if idx == 1 and not hook_text:
                hook_text = narr

        v_kw = str(sc.get("visual_keyword") or "").strip()
        s_q = str(sc.get("stock_query") or "").strip()
        v_pr = str(sc.get("visual_prompt") or sc.get("cinematic_prompt") or "").strip()
        m_type = str(sc.get("media_type") or "auto").strip()
        m_fp = _extract_media_fingerprint(sc.get("media_path"))
        cam = str(sc.get("camera_motion") or "").strip()
        v_strat = str(sc.get("visual_strategy") or sc.get("production_method") or "").strip()

        for kw in (s_q, v_kw):
            if kw and kw.lower() not in [x.lower() for x in visual_keywords_used]:
                visual_keywords_used.append(kw[:80])
        if m_fp and m_fp not in media_fingerprints_used:
            media_fingerprints_used.append(m_fp)

        scenes_data.append({
            "scene_id": sc.get("scene_id") or idx,
            "narration": narr[:260],
            "visual_keyword": v_kw[:80],
            "stock_query": s_q[:80],
            "visual_prompt": v_pr[:220],
            "media_type": m_type,
            "media_fingerprint": m_fp,
            "camera_motion": cam,
            "visual_strategy": v_strat,
            "duration_ms": sc.get("audio_duration_ms"),
        })

    full_script = " ".join(narration_parts).strip() or (fallback_script or "").strip()
    if not hook_text and full_script:
        hook_text = re.split(r"(?<=[\.\!\?])\s+", full_script)[0][:180]

    return {
        "hook_text": hook_text[:300],
        "full_script": full_script[:3500],
        "pinned_comment": pinned_comment[:300],
        "scene_count": len(scenes_data),
        "visual_metadata": scenes_data,
        "visual_keywords_used": visual_keywords_used[:20],
        "media_fingerprints_used": media_fingerprints_used[:20],
    }


class VideoMemoryRAG:
    """
    Hệ thống Mini-RAG lưu trữ & truy vấn Kịch bản + Metadata Hình ảnh siêu nhẹ.
    """

    def __init__(self, index_path: Optional[Path] = None):
        self.index_path = index_path or _RAG_INDEX_PATH
        self._docs: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        with _RAG_LOCK:
            if self._loaded:
                return
            self._loaded = True
            if self.index_path.exists():
                try:
                    raw = json.loads(self.index_path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict) and isinstance(raw.get("documents"), dict):
                        self._docs = raw["documents"]
                except Exception as e:
                    logger.warning(f"[MiniRAG] Không thể đọc file index {self.index_path}: {e}")
                    self._docs = {}

    def save_index(self) -> None:
        with _RAG_LOCK:
            self._ensure_loaded()
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": "2.0-mini-rag",
                "updated_at_utc": datetime.utcnow().isoformat(),
                "total_documents": len(self._docs),
                "documents": self._docs,
            }
            tmp_path = self.index_path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(self.index_path)

    def upsert_document(
        self,
        doc_id: str,
        channel_id: str,
        title: str,
        topic: str = "",
        hook_text: str = "",
        full_script: str = "",
        summary: str = "",
        visual_metadata: Optional[List[Dict[str, Any]]] = None,
        visual_keywords_used: Optional[List[str]] = None,
        media_fingerprints_used: Optional[List[str]] = None,
        youtube_video_id: Optional[str] = None,
        job_id: Optional[int] = None,
        views: int = 0,
        avg_view_percentage: Optional[float] = None,
        stayed_to_watch_pct: Optional[float] = None,
        swipe_away_pct: Optional[float] = None,
        avg_view_duration_sec: Optional[float] = None,
        published_at: Optional[str] = None,
        auto_save: bool = True,
    ) -> Dict[str, Any]:
        """Thêm hoặc cập nhật 1 bản ghi Kịch bản + Metadata Hình ảnh vào Mini-RAG."""
        with _RAG_LOCK:
            self._ensure_loaded()
            canon_cid = normalize_channel_id(channel_id)
            existing = self._docs.get(doc_id, {})

            merged_title = (title or existing.get("title") or "").strip()
            merged_topic = (topic or existing.get("topic") or merged_title).strip()
            merged_hook = (hook_text or existing.get("hook_text") or "").strip()
            merged_script = (full_script or existing.get("full_script") or "").strip()
            merged_summary = (summary or existing.get("summary") or merged_script[:260]).strip()
            merged_vis_meta = visual_metadata if visual_metadata is not None else existing.get("visual_metadata", [])
            merged_vis_kw = visual_keywords_used if visual_keywords_used is not None else existing.get("visual_keywords_used", [])
            merged_fps = media_fingerprints_used if media_fingerprints_used is not None else existing.get("media_fingerprints_used", [])

            # Xây dựng văn bản hợp nhất để đánh chỉ mục BM25 / Cosine Vector
            vis_prompts_str = " ".join(
                f"{s.get('visual_keyword', '')} {s.get('stock_query', '')} {s.get('visual_prompt', '')}"
                for s in (merged_vis_meta or [])
                if isinstance(s, dict)
            )
            searchable_text = f"{merged_title} {merged_topic} {merged_hook} {merged_script} {vis_prompts_str}".strip()
            tokens = _tokenize_rag(searchable_text)
            token_counts = dict(Counter(tokens).most_common(80))

            doc = {
                "doc_id": doc_id,
                "channel_id": canon_cid,
                "youtube_video_id": youtube_video_id or existing.get("youtube_video_id"),
                "job_id": job_id or existing.get("job_id"),
                "title": merged_title,
                "topic": merged_topic,
                "hook_text": merged_hook,
                "full_script": merged_script,
                "summary": merged_summary,
                "visual_metadata": merged_vis_meta,
                "visual_keywords_used": merged_vis_kw,
                "media_fingerprints_used": merged_fps,
                "views": max(int(views or 0), int(existing.get("views") or 0)),
                "avg_view_percentage": avg_view_percentage if avg_view_percentage is not None else existing.get("avg_view_percentage"),
                "stayed_to_watch_pct": stayed_to_watch_pct if stayed_to_watch_pct is not None else existing.get("stayed_to_watch_pct"),
                "swipe_away_pct": swipe_away_pct if swipe_away_pct is not None else existing.get("swipe_away_pct"),
                "avg_view_duration_sec": avg_view_duration_sec if avg_view_duration_sec is not None else existing.get("avg_view_duration_sec"),
                "published_at": published_at or existing.get("published_at") or datetime.utcnow().strftime("%Y-%m-%d"),
                "token_weights": token_counts,
                "updated_at": datetime.utcnow().isoformat(),
            }
            self._docs[doc_id] = doc
            if auto_save:
                self.save_index()
            return doc

    def sync_all_from_db(self) -> Dict[str, int]:
        """
        Đồng bộ toàn bộ VideoJob và PublishedVideo từ DB vào Mini-RAG
        (gộp kịch bản chi tiết + metadata hình ảnh từ VideoJob với số liệu thật từ PublishedVideo).
        """
        from sqlalchemy.orm import defer

        with _RAG_LOCK:
            self._ensure_loaded()
            jobs_indexed = 0
            pub_indexed = 0

            with get_db() as db:
                jobs: List[VideoJob] = []
                batch_size = 25
                offset = 0
                while True:
                    chunk = (
                        db.query(VideoJob)
                        .options(defer(VideoJob.render_manifest))
                        .order_by(VideoJob.id.asc())
                        .offset(offset)
                        .limit(batch_size)
                        .all()
                    )
                    if not chunk:
                        break
                    jobs.extend(chunk)
                    offset += batch_size
                pvs = db.query(PublishedVideo).all()

                job_by_yt: Dict[str, VideoJob] = {}
                for j in jobs:
                    if j.youtube_video_id:
                        job_by_yt[j.youtube_video_id.strip()] = j

                # 1. Index toàn bộ PublishedVideo (ghép kịch bản + metadata ảnh từ VideoJob nếu có)
                for pv in pvs:
                    vid = (pv.youtube_video_id or "").strip()
                    if not vid:
                        continue
                    matched_job = job_by_yt.get(vid)
                    meta = extract_visual_and_script_metadata(
                        scenes_json_raw=matched_job.scenes_json if matched_job else None,
                        render_manifest_raw=None,
                        fallback_script=(matched_job.script if matched_job else None) or pv.description or pv.summary,
                        fallback_hook=pv.hook_text or getattr(matched_job, "hook_text", None) or pv.title,
                    )
                    # Lưu tóm tắt kịch bản & metadata ảnh gọn nhẹ vào PublishedVideo.summary nếu chưa có
                    compact_summary = pv.summary or meta["full_script"][:320]
                    if meta["visual_keywords_used"] and not pv.topic_tags:
                        pv.topic_tags = json.dumps(meta["visual_keywords_used"][:8], ensure_ascii=False)

                    self.upsert_document(
                        doc_id=f"yt:{vid}",
                        channel_id=pv.channel_id,
                        title=pv.title or (matched_job.title if matched_job else ""),
                        topic=(matched_job.topic if matched_job else "") or pv.topic_cluster or pv.title or "",
                        hook_text=meta["hook_text"],
                        full_script=meta["full_script"],
                        summary=compact_summary,
                        visual_metadata=meta["visual_metadata"],
                        visual_keywords_used=meta["visual_keywords_used"],
                        media_fingerprints_used=meta["media_fingerprints_used"],
                        youtube_video_id=vid,
                        job_id=pv.job_id or (matched_job.id if matched_job else None),
                        views=pv.views or 0,
                        avg_view_percentage=pv.avg_view_percentage,
                        stayed_to_watch_pct=pv.stayed_to_watch_pct,
                        swipe_away_pct=pv.swipe_away_pct,
                        avg_view_duration_sec=pv.avg_view_duration_sec,
                        published_at=pv.published_at.strftime("%Y-%m-%d") if pv.published_at else None,
                        auto_save=False,
                    )
                    pub_indexed += 1

                # 2. Index các VideoJob chưa có trong PublishedVideo (đã dựng hoặc đã lên lịch)
                for j in jobs:
                    vid = (j.youtube_video_id or "").strip()
                    if vid and f"yt:{vid}" in self._docs:
                        continue
                    meta = extract_visual_and_script_metadata(
                        scenes_json_raw=j.scenes_json,
                        render_manifest_raw=None,
                        fallback_script=j.script or j.description,
                        fallback_hook=getattr(j, "hook_text", None) or j.title,
                    )
                    doc_key = f"yt:{vid}" if vid else f"job:{j.id}"
                    self.upsert_document(
                        doc_id=doc_key,
                        channel_id=j.target_channel_id,
                        title=j.title or j.topic,
                        topic=j.topic or j.title or "",
                        hook_text=meta["hook_text"],
                        full_script=meta["full_script"],
                        summary=meta["full_script"][:320],
                        visual_metadata=meta["visual_metadata"],
                        visual_keywords_used=meta["visual_keywords_used"],
                        media_fingerprints_used=meta["media_fingerprints_used"],
                        youtube_video_id=vid or None,
                        job_id=j.id,
                        views=j.views or 0,
                        avg_view_percentage=j.avg_view_percentage,
                        stayed_to_watch_pct=j.stayed_to_watch_pct,
                        swipe_away_pct=j.swipe_away_pct,
                        avg_view_duration_sec=j.avg_view_duration_sec,
                        published_at=j.created_at.strftime("%Y-%m-%d") if j.created_at else None,
                        auto_save=False,
                    )
                    jobs_indexed += 1

            self.save_index()
            return {
                "published_indexed": pub_indexed,
                "jobs_indexed": jobs_indexed,
                "total_rag_docs": len(self._docs),
            }

    def query_similar(
        self,
        query_text: str = "",
        channel_id: Optional[str] = None,
        top_k: int = 5,
        min_score: float = 0.18,
    ) -> List[Dict[str, Any]]:
        """
        Truy vấn Mini-RAG (Hybrid Cosine TF-IDF + Title/Hook Token Containment):
        Tìm các video cũ có kịch bản, chủ đề, hoặc hình ảnh tương đồng với `query_text`.
        """
        self._ensure_loaded()
        if not query_text:
            return []
        canon_cid = normalize_channel_id(channel_id) if channel_id else ""
        q_tokens = _tokenize_rag(query_text)
        if not q_tokens:
            return []
        q_counts = Counter(q_tokens)
        q_norm = math.sqrt(sum(v * v for v in q_counts.values())) or 1.0
        q_unigrams = {t for t in q_tokens if "_" not in t}

        # Tính IDF nhẹ trên tập tài liệu
        total_docs = max(1, len(self._docs))
        df_map: Counter = Counter()
        for d in self._docs.values():
            tw = d.get("token_weights") or {}
            for tok in q_counts:
                if tok in tw:
                    df_map[tok] += 1

        results: List[Dict[str, Any]] = []
        for doc in self._docs.values():
            if canon_cid and doc.get("channel_id") != canon_cid:
                continue
            doc_weights: Dict[str, int] = doc.get("token_weights") or {}
            if not doc_weights:
                continue

            # 1. Weighted TF-IDF Cosine trên toàn bộ Kịch bản + Metadata Hình ảnh
            dot = 0.0
            doc_norm = math.sqrt(sum(v * v for v in doc_weights.values())) or 1.0
            for tok, q_tf in q_counts.items():
                d_tf = doc_weights.get(tok, 0)
                if d_tf > 0:
                    idf = 1.0 + math.log((total_docs + 1.0) / (df_map[tok] + 1.0))
                    dot += (q_tf * idf) * (d_tf * idf)
            cosine_sim = min(1.0, dot / (q_norm * doc_norm * 2.2))

            # 2. Overlap trực tiếp với Tiêu đề + Chủ đề + Hook (phát hiện trùng ý tưởng chính)
            title_hook_tokens = {
                t for t in _tokenize_rag(f"{doc.get('title', '')} {doc.get('topic', '')} {doc.get('hook_text', '')}")
                if "_" not in t
            }
            title_overlap = 0.0
            if q_unigrams and title_hook_tokens:
                inter = len(q_unigrams & title_hook_tokens)
                union = len(q_unigrams | title_hook_tokens)
                jaccard = inter / union if union else 0.0
                containment = inter / min(len(q_unigrams), len(title_hook_tokens))
                title_overlap = max(jaccard, containment * 0.78)

            combined = round(max(title_overlap, cosine_sim * 0.85 + title_overlap * 0.35), 3)
            if combined >= min_score:
                results.append({
                    "doc_id": doc["doc_id"],
                    "channel_id": doc["channel_id"],
                    "youtube_video_id": doc.get("youtube_video_id"),
                    "job_id": doc.get("job_id"),
                    "title": doc.get("title", ""),
                    "topic": doc.get("topic", ""),
                    "hook_text": doc.get("hook_text", ""),
                    "full_script": doc.get("full_script", ""),
                    "script_excerpt": (doc.get("full_script") or doc.get("summary") or "")[:240],
                    "visual_metadata": doc.get("visual_metadata", []),
                    "visual_keywords_used": doc.get("visual_keywords_used", [])[:8],
                    "views": doc.get("views", 0),
                    "stayed_to_watch_pct": doc.get("stayed_to_watch_pct"),
                    "avg_view_percentage": doc.get("avg_view_percentage"),
                    "published_at": doc.get("published_at"),
                    "similarity": combined,
                    "similarity_score": combined,
                })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:top_k]

    def get_channel_used_visuals(
        self,
        channel_id: Optional[str] = None,
        limit_videos: int = 15,
        limit_docs: Optional[int] = None,
    ) -> Dict[str, List[str]]:
        """
        Trả về danh sách các từ khóa hình ảnh (`visual_keywords`) và mã nhận diện ảnh/clip (`media_fingerprints`)
        đã dùng gần đây trên kênh (hoặc toàn mạng lưới nếu channel_id=None) để tránh lấy trùng hình ảnh/video giữa các bài đăng.
        """
        self._ensure_loaded()
        eff_limit = limit_docs if limit_docs is not None else limit_videos
        if channel_id:
            canon_cid = normalize_channel_id(channel_id)
            ch_docs = [d for d in self._docs.values() if d.get("channel_id") == canon_cid]
        else:
            ch_docs = list(self._docs.values())
        ch_docs.sort(key=lambda x: str(x.get("published_at") or ""), reverse=True)

        used_keywords: List[str] = []
        used_fingerprints: List[str] = []
        for d in ch_docs[:eff_limit]:
            for kw in d.get("visual_keywords_used") or []:
                if kw and kw not in used_keywords:
                    used_keywords.append(kw)
            for fp in d.get("media_fingerprints_used") or []:
                if fp and fp not in used_fingerprints:
                    used_fingerprints.append(fp)

        return {
            "visual_keywords": used_keywords[:40],
            "media_fingerprints": used_fingerprints[:60],
        }

    def build_rag_prompt_context(self, channel_id: str, query_topic: str, top_k: int = 4) -> str:
        """
        Sinh khối ngữ cảnh Mini-RAG (chứa kịch bản tương tự đã làm + từ khóa hình ảnh đã dùng)
        để truyền vào ContentAgent giúp chống trùng lặp kịch bản & hình ảnh 100%.
        """
        hits = self.query_similar(query_text=query_topic, channel_id=channel_id, top_k=top_k, min_score=0.15)
        used_vis = self.get_channel_used_visuals(channel_id=channel_id, limit_videos=10)

        lines: List[str] = []
        if hits:
            lines.append("📚 MINI-RAG RETRIEVED PAST SCRIPTS (DO NOT REPEAT THESE SCRIPTS, HOOKS, OR EXAMPLES):")
            for idx, h in enumerate(hits, 1):
                vis_str = ", ".join(h.get("visual_keywords_used") or [])
                lines.append(
                    f"  #{idx} [Sim={h['similarity']:.2f}] Title: \"{h['title']}\" | Hook: \"{h['hook_text'][:100]}\"\n"
                    f"      Script Excerpt: \"{h['script_excerpt'][:180]}...\"\n"
                    f"      Visual Metadata Used: [{vis_str or 'N/A'}]"
                )
        if used_vis.get("visual_keywords"):
            lines.append(
                "🖼️ RECENTLY USED VISUAL KEYWORDS ON THIS CHANNEL (Pick FRESH visual scenes & stock queries): "
                + ", ".join(f'"{k}"' for k in used_vis["visual_keywords"][:15])
            )
        return "\n".join(lines)


_GLOBAL_MINI_RAG: Optional[VideoMemoryRAG] = None


def get_video_memory_rag() -> VideoMemoryRAG:
    """Singleton truy cập nhanh hệ thống Mini-RAG."""
    global _GLOBAL_MINI_RAG
    if _GLOBAL_MINI_RAG is None:
        _GLOBAL_MINI_RAG = VideoMemoryRAG()
    return _GLOBAL_MINI_RAG


def archive_job_to_rag_and_cleanup_files(job_id: int) -> Dict[str, Any]:
    """
    Sau khi 1 VideoJob đã được upload lên YouTube (`PUBLISHED` hoặc `SCHEDULED` đã có `youtube_video_id`):
    1. Trích xuất toàn bộ Kịch bản + Metadata Hình ảnh lưu vào Mini-RAG.
    2. Xóa gọn `render_manifest` nặng trong DB (chỉ giữ lại `scenes_json` gọn nhẹ chứa lời dẫn + metadata ảnh).
    3. Xóa ngay file `.mp4` nặng trong `storage/outputs/` và các file tạm của job trong `storage/temp/`.
    """
    rag = get_video_memory_rag()
    freed_bytes = 0
    removed_files: List[str] = []

    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            return {"ok": False, "error": f"Job #{job_id} not found"}

        # Chỉ xóa file video nặng khi video ĐÃ ĐƯỢC ĐẨY LÊN YOUTUBE (có youtube_video_id) hoặc đã PUBLISHED/FAILED
        is_uploaded_to_youtube = bool(
            (job.youtube_video_id and str(job.youtube_video_id).strip())
            or job.status in ("PUBLISHED", "FAILED")
        )

        meta = extract_visual_and_script_metadata(
            scenes_json_raw=job.scenes_json,
            render_manifest_raw=job.render_manifest,
            fallback_script=job.script or job.description,
            fallback_hook=getattr(job, "hook_text", None) or job.title,
        )

        vid = (job.youtube_video_id or "").strip()
        doc_id = f"yt:{vid}" if vid else f"job:{job.id}"
        rag.upsert_document(
            doc_id=doc_id,
            channel_id=job.target_channel_id,
            title=job.title or job.topic,
            topic=job.topic or job.title or "",
            hook_text=meta["hook_text"],
            full_script=meta["full_script"],
            summary=meta["full_script"][:320],
            visual_metadata=meta["visual_metadata"],
            visual_keywords_used=meta["visual_keywords_used"],
            media_fingerprints_used=meta["media_fingerprints_used"],
            youtube_video_id=vid or None,
            job_id=job.id,
            views=job.views or 0,
            avg_view_percentage=job.avg_view_percentage,
            stayed_to_watch_pct=job.stayed_to_watch_pct,
            swipe_away_pct=job.swipe_away_pct,
            avg_view_duration_sec=job.avg_view_duration_sec,
            published_at=job.created_at.strftime("%Y-%m-%d") if job.created_at else None,
        )

        # Lưu lại bản scenes_json siêu gọn (chỉ chứa lời dẫn + metadata hình ảnh, bỏ word_timings dài dòng)
        compact_scenes_payload = {
            "title": job.title,
            "hook": meta["hook_text"],
            "pinned_comment": meta["pinned_comment"],
            "scenes": meta["visual_metadata"],
        }
        job.scenes_json = json.dumps(compact_scenes_payload, ensure_ascii=False)
        job.render_manifest = None

        if is_uploaded_to_youtube:
            for path_attr in ("video_path", "audio_path", "subtitle_path", "thumbnail_path", "thumbnail_vertical_path"):
                p_val = getattr(job, path_attr, None)
                if p_val:
                    p_obj = Path(p_val)
                    if p_obj.exists() and p_obj.is_file():
                        try:
                            sz = p_obj.stat().st_size
                            p_obj.unlink()
                            freed_bytes += sz
                            removed_files.append(p_obj.name)
                        except Exception:
                            pass
                    setattr(job, path_attr, None)

    # Dọn các file tạm của job_id trong storage/temp
    temp_dir = Path(settings.TEMP_DIR)
    if temp_dir.exists():
        for tf in temp_dir.glob(f"*{job_id}*"):
            if tf.is_file():
                try:
                    sz = tf.stat().st_size
                    tf.unlink()
                    freed_bytes += sz
                    removed_files.append(tf.name)
                except Exception:
                    pass

    return {
        "ok": True,
        "job_id": job_id,
        "doc_id": doc_id,
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
        "removed_files_count": len(removed_files),
    }


def slim_storage_and_archive_to_rag(cache_max_age_hours: float = 6.0) -> Dict[str, Any]:
    """
    Quy trình dọn dẹp toàn diện:
    1. Đồng bộ 100% kịch bản + metadata hình ảnh từ DB vào Mini-RAG (`storage/video_memory_rag.json`).
    2. Thu gọn `scenes_json` & xóa `render_manifest` nặng trong DB cho các job đã hoàn tất.
    3. Xóa toàn bộ file `.mp4` trong `storage/outputs/` của các video đã đăng/đã lên lịch trên YouTube
       (chỉ giữ lại file `.mp4` của các job `RENDERED` / `AWAITING_APPROVAL` chưa đẩy lên YouTube).
    4. Xóa các file tạm trong `storage/temp/` và cache media cũ trong `storage/cache/`.
    """
    from sqlalchemy.orm import defer

    rag = get_video_memory_rag()
    sync_stats = rag.sync_all_from_db()

    freed_bytes = 0
    deleted_mp4_count = 0
    deleted_temp_count = 0
    deleted_cache_count = 0

    # Xác định danh sách các file MP4 CẦN GIỮ LẠI (chưa đẩy lên YouTube)
    protected_files: Set[str] = set()
    completed_ids: List[int] = []
    with get_db() as db:
        batch_size = 25
        offset = 0
        while True:
            chunk = (
                db.query(VideoJob)
                .options(defer(VideoJob.render_manifest))
                .order_by(VideoJob.id.asc())
                .offset(offset)
                .limit(batch_size)
                .all()
            )
            if not chunk:
                break
            offset += batch_size
            for j in chunk:
                needs_local_file = (
                    j.status in ("RENDERED", "AWAITING_APPROVAL", "WAITING_QUOTA_RESET", "RENDERING")
                    or (j.status == "SCHEDULED" and not (j.youtube_video_id and str(j.youtube_video_id).strip()))
                )
                if needs_local_file:
                    for p_attr in ("video_path", "thumbnail_path", "thumbnail_vertical_path"):
                        p_val = getattr(j, p_attr, None)
                        if p_val:
                            protected_files.add(Path(p_val).name)
                    protected_files.add(f"thumb_{j.id}.jpg")
                    protected_files.add(f"thumb_{j.id}.png")
                    protected_files.add(f"thumb_vert_{j.id}.jpg")
                    protected_files.add(f"thumb_vert_{j.id}.png")
                else:
                    completed_ids.append(j.id)
                    meta = extract_visual_and_script_metadata(
                        scenes_json_raw=j.scenes_json,
                        render_manifest_raw=None,
                        fallback_script=j.script or j.description,
                        fallback_hook=getattr(j, "hook_text", None) or j.title,
                    )
                    if meta["visual_metadata"]:
                        compact_str = json.dumps(
                            {
                                "title": j.title,
                                "hook": meta["hook_text"],
                                "pinned_comment": meta["pinned_comment"],
                                "scenes": meta["visual_metadata"],
                            },
                            ensure_ascii=False,
                        )
                        if j.scenes_json != compact_str:
                            j.scenes_json = compact_str
            db.flush()

        if completed_ids:
            db.query(VideoJob).filter(VideoJob.id.in_(completed_ids)).update(
                {
                    VideoJob.render_manifest: None,
                    VideoJob.video_path: None,
                    VideoJob.audio_path: None,
                    VideoJob.subtitle_path: None,
                    VideoJob.thumbnail_path: None,
                    VideoJob.thumbnail_vertical_path: None,
                },
                synchronize_session=False,
            )

    # 1. Dọn storage/outputs/ & storage/thumbnails/ (xóa mọi file .mp4, .jpg, .png không nằm trong protected_files)
    deleted_thumb_count = 0
    for target_dir_path, is_thumb_dir in [
        (Path(settings.OUTPUT_DIR), False),
        (Path(settings.BASE_DIR) / "storage" / "thumbnails", True),
    ]:
        if target_dir_path.exists():
            for f in target_dir_path.rglob("*"):
                if not f.is_file() or f.name == ".gitkeep":
                    continue
                if f.name in protected_files or any(f"_{pf_stem}" in f.name for pf_stem in [Path(x).stem for x in protected_files]):
                    continue
                try:
                    sz = f.stat().st_size
                    f.unlink()
                    freed_bytes += sz
                    if f.suffix.lower() == ".mp4":
                        deleted_mp4_count += 1
                    elif is_thumb_dir:
                        deleted_thumb_count += 1
                except Exception:
                    pass

    # Xóa file test_dummy_quota.mp4 nếu còn sót
    dummy_mp4 = Path(settings.BASE_DIR) / "storage" / "test_dummy_quota.mp4"
    if dummy_mp4.exists():
        try:
            freed_bytes += dummy_mp4.stat().st_size
            dummy_mp4.unlink()
        except Exception:
            pass

    # 2. Dọn storage/temp/
    temp_dir = Path(settings.TEMP_DIR)
    if temp_dir.exists():
        for f in temp_dir.rglob("*"):
            if not f.is_file() or f.name == ".gitkeep":
                continue
            if f.name in protected_files:
                continue
            try:
                sz = f.stat().st_size
                f.unlink()
                freed_bytes += sz
                deleted_temp_count += 1
            except Exception:
                pass

    # 3. Dọn storage/cache/ (giữ lại các file .json cấu hình/history, chỉ xóa media cache cũ)
    cache_dir = Path(settings.BASE_DIR) / "storage" / "cache"
    if cache_dir.exists():
        cutoff_ts = datetime.now().timestamp() - (cache_max_age_hours * 3600.0)
        for f in cache_dir.rglob("*"):
            if not f.is_file() or f.name == ".gitkeep" or f.suffix.lower() == ".json":
                continue
            try:
                if cache_max_age_hours <= 0 or f.stat().st_mtime <= cutoff_ts:
                    sz = f.stat().st_size
                    f.unlink()
                    freed_bytes += sz
                    deleted_cache_count += 1
            except Exception:
                pass

    return {
        **sync_stats,
        "protected_local_videos": sorted(protected_files),
        "deleted_mp4_count": deleted_mp4_count,
        "deleted_thumb_count": deleted_thumb_count,
        "deleted_temp_count": deleted_temp_count,
        "deleted_cache_count": deleted_cache_count,
        "freed_mb": round(freed_bytes / (1024 * 1024), 2),
    }

