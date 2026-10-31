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
from app.utils.langsmith_tracer import trace_custom_span

logger = logging.getLogger(__name__)

_RAG_LOCK = threading.RLock()
_RAG_INDEX_PATH = Path(settings.BASE_DIR) / "storage" / "video_memory_rag.json"

_STOP_WORDS: Set[str] = {
    "the", "and", "for", "you", "your", "with", "that", "this", "from", "are", "was", "how",
    "why", "what", "when", "who", "his", "her", "its", "not", "but", "can", "will", "have",
    "has", "had", "about", "into", "than", "then", "they", "them", "their", "one", "all",
    "video", "videos", "shorts", "short", "most", "more", "just", "like", "been", "out",
    "were", "would", "could", "should", "there", "where", "which", "while", "after", "before",
    "de", "do", "da", "dos", "das", "no", "na", "nos", "nas", "em", "se", "ao", "aos",
    "as", "os", "um", "uma", "uns", "umas", "ou", "já", "só", "há", "até", "pelo", "pela",
    "pelos", "pelas", "num", "numa", "dele", "dela", "este", "esta", "esse", "essa", "isto",
    "isso", "aquele", "aquela", "muito", "muita", "onde", "quando", "quem", "qual", "quais",
    "sobre", "entre", "contra", "sem", "sob", "após", "antes", "depois", "por", "que",
    "para", "com", "mais", "como", "foi", "são", "era", "tem", "têm", "seu", "sua", "seus", "suas",
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
        m_fp = _extract_media_fingerprint(sc.get("media_path")) or str(sc.get("media_fingerprint") or "").strip()
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
        # Bộ nhớ RAG tạm thời theo từng Job (In-Job Ephemeral RAG Session):
        # Chống lặp cảnh cắt / video nguồn 100% BÊN TRONG CÙNG 1 VIDEO và tối ưu xếp hạng chất lượng ứng viên,
        # không ghi rác ra ổ đĩa và không khóa cứng video giữa các bài đăng khác nhau.
        self._active_job_sessions: Dict[int, Dict[str, Any]] = {}

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

                # Xóa mọi bản ghi cũ không có youtube_video_id (chỉ giữ lại "yt:*")
                for k in list(self._docs.keys()):
                    if not k.startswith("yt:") or not self._docs[k].get("youtube_video_id"):
                        self._docs.pop(k, None)

                job_by_yt: Dict[str, VideoJob] = {}
                for j in jobs:
                    if j.youtube_video_id and str(j.youtube_video_id).strip():
                        job_by_yt[str(j.youtube_video_id).strip()] = j

                # 1. Index toàn bộ PublishedVideo đã đăng lên YouTube (ghép kịch bản + metadata ảnh từ VideoJob nếu có)
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

                # 2. Index các VideoJob ĐÃ ĐĂNG LÊN YOUTUBE (có youtube_video_id) nhưng chưa có trong PublishedVideo
                for j in jobs:
                    vid = (j.youtube_video_id or "").strip()
                    if not vid:
                        # Tuyệt đối KHÔNG lưu video nháp / lỗi / test chưa đăng YouTube vào bộ nhớ RAG
                        continue
                    if f"yt:{vid}" in self._docs:
                        continue
                    meta = extract_visual_and_script_metadata(
                        scenes_json_raw=j.scenes_json,
                        render_manifest_raw=None,
                        fallback_script=j.script or j.description,
                        fallback_hook=getattr(j, "hook_text", None) or j.title,
                    )
                    doc_key = f"yt:{vid}"
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
                        youtube_video_id=vid,
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

    @trace_custom_span("MiniRAG.query_similar", run_type="retriever")
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

            # 1. Weighted TF-IDF Cosine trên toàn bộ Kịch bản + Metadata Hình ảnh (loại bỏ stop words tồn đọng trong index cũ)
            filtered_doc_weights = {
                k: v for k, v in doc_weights.items()
                if k not in _STOP_WORDS and not any(part in _STOP_WORDS for part in k.split("_"))
            }
            if not filtered_doc_weights:
                continue

            dot = 0.0
            q_tfidf_sq = 0.0
            for tok, q_tf in q_counts.items():
                idf = 1.0 + math.log((total_docs + 1.0) / (df_map[tok] + 1.0))
                q_w = q_tf * idf
                q_tfidf_sq += q_w * q_w
                d_tf = filtered_doc_weights.get(tok, 0)
                if d_tf > 0:
                    dot += q_w * (d_tf * idf)
            q_tfidf_norm = math.sqrt(q_tfidf_sq) or 1.0
            doc_tfidf_norm = math.sqrt(sum((v * 1.8) ** 2 for v in filtered_doc_weights.values())) or 1.0
            cosine_sim = min(1.0, dot / (q_tfidf_norm * doc_tfidf_norm))

            # 2. Overlap trực tiếp với Tiêu đề + Chủ đề + Hook (phát hiện trùng ý tưởng chính)
            title_hook_tokens = {
                t for t in _tokenize_rag(f"{doc.get('title', '')} {doc.get('topic', '')} {doc.get('hook_text', '')}")
                if "_" not in t and t not in _STOP_WORDS
            }
            title_overlap = 0.0
            shared_unigrams = q_unigrams & title_hook_tokens
            if q_unigrams and title_hook_tokens and shared_unigrams:
                inter = len(shared_unigrams)
                union = len(q_unigrams | title_hook_tokens)
                jaccard = inter / union if union else 0.0
                containment = inter / min(len(q_unigrams), len(title_hook_tokens))
                # Nếu chỉ trùng đúng 1 từ đơn lẻ (vd: 'oceano', 'metros', 'shark'), không được phép vọt điểm containment ảo
                if inter == 1 and min(len(q_unigrams), len(title_hook_tokens)) >= 3:
                    title_overlap = min(0.22, jaccard)
                    cosine_sim = min(0.22, cosine_sim)
                else:
                    title_overlap = max(jaccard, containment * 0.78)

            combined = round(max(title_overlap, cosine_sim * 0.75 + title_overlap * 0.35), 3)
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

    # ══════════════════════════════════════════════════════════════════════════
    # INTRA-VIDEO RAG ENGINE (Chống lặp cảnh cắt 100% TRONG CÙNG 1 VIDEO + Tối ưu chất lượng bằng RAG)
    # Lưu trên RAM theo `job_id` (tự động giải phóng sau khi render xong), không cần lưu lịch sử khóa cứng giữa các bài đăng khác nhau.
    # ══════════════════════════════════════════════════════════════════════════

    def _get_or_create_job_session(self, job_id: int) -> Dict[str, Any]:
        eff_jid = int(job_id or 0)
        now_ts = datetime.utcnow().timestamp()
        with _RAG_LOCK:
            # Tự động dọn các session cũ quá 25 phút trong RAM
            stale = [
                k for k, v in self._active_job_sessions.items()
                if now_ts - float(v.get("updated_ts", now_ts)) > 1500.0
            ]
            for k in stale:
                self._active_job_sessions.pop(k, None)

            sess = self._active_job_sessions.get(eff_jid)
            if sess is None:
                sess = {
                    "job_id": eff_jid,
                    "created_ts": now_ts,
                    "updated_ts": now_ts,
                    "claimed_video_ids": set(),         # Tập hợp yt:<id>, pexels:<id>, URL chuẩn hóa đã claim trong job này
                    "claimed_titles": [],               # List[{"scene_id": int, "vid_id": str, "title": str, "tokens": Set[str]}]
                    "committed_signatures": {},         # Dict[int, List[str]] (scene_id -> list of 64-bit dHash strings)
                    "scene_queries_used": [],           # List[str] các truy vấn đã phân bổ trong cùng 1 video
                }
                self._active_job_sessions[eff_jid] = sess
            else:
                sess["updated_ts"] = now_ts
            return sess

    def reset_job_session(self, job_id: int) -> None:
        """Khởi tạo mới phiên RAG chống lặp cảnh cắt nội bộ cho 1 Job chuẩn bị dựng."""
        eff_jid = int(job_id or 0)
        with _RAG_LOCK:
            self._active_job_sessions.pop(eff_jid, None)
            self._get_or_create_job_session(eff_jid)

    def cleanup_job_session(self, job_id: int) -> None:
        """Giải phóng bộ nhớ RAM của phiên RAG nội bộ sau khi Job hoàn tất."""
        eff_jid = int(job_id or 0)
        with _RAG_LOCK:
            self._active_job_sessions.pop(eff_jid, None)

    @staticmethod
    def _canonical_video_keys(vid_id: str = "", url: str = "") -> Set[str]:
        keys: Set[str] = set()
        if vid_id and str(vid_id).strip():
            cid = str(vid_id).strip()
            keys.add(cid)
            keys.add(f"yt:{cid}")
        if url and str(url).strip():
            u = str(url).strip()
            keys.add(u)
            clean_u = re.sub(r"\?.*$", "", u).strip()
            if clean_u:
                keys.add(clean_u)
            m_yt = (
                re.search(r"(?:v=|/shorts/|youtu\.be/|/embed/)([A-Za-z0-9_-]{11})(?:[?&/#]|$)", u)
                or re.search(r"yt_(?:hook_cand_\d+|peak|short)_([A-Za-z0-9_-]{11})", u)
            )
            if m_yt:
                yid = m_yt.group(1)
                keys.add(yid)
                keys.add(f"yt:{yid}")
        return keys

    @staticmethod
    def compute_text_similarity(text_a: str, text_b: str) -> float:
        """
        Đo độ tương đồng ngữ nghĩa & cụm từ (1-gram + 2-gram Cosine + Containment) siêu tốc (<0.2ms)
        giữa 2 tiêu đề video hoặc 2 câu truy vấn phân cảnh bằng bộ tách từ của Mini-RAG.
        """
        toks_a = _tokenize_rag(text_a)
        toks_b = _tokenize_rag(text_b)
        if not toks_a or not toks_b:
            return 0.0
        ca = Counter(toks_a)
        cb = Counter(toks_b)
        dot = sum(ca[t] * cb[t] for t in (ca.keys() & cb.keys()))
        norm_a = math.sqrt(sum(v * v for v in ca.values())) or 1.0
        norm_b = math.sqrt(sum(v * v for v in cb.values())) or 1.0
        cosine = dot / (norm_a * norm_b)

        uni_a = {t for t in toks_a if "_" not in t}
        uni_b = {t for t in toks_b if "_" not in t}
        if not uni_a or not uni_b:
            return round(cosine, 3)
        inter = len(uni_a & uni_b)
        containment = inter / max(1, min(len(uni_a), len(uni_b)))
        return round(max(cosine, containment * 0.82), 3)

    def get_high_performing_visual_tokens(
        self,
        channel_id: Optional[str] = None,
        topic: str = "",
        top_k: int = 6,
    ) -> Dict[str, float]:
        """
        Khai thác dữ liệu hiệu suất thật trong Mini-RAG (`stayed_to_watch_pct`, `avg_view_percentage`, `views`)
        để trích xuất các từ khóa hình ảnh / hành động từng tạo ra tỷ lệ giữ chân cao nhất trên kênh/hệ thống.
        """
        self._ensure_loaded()
        hits = self.query_similar(query_text=topic, channel_id=channel_id, top_k=top_k, min_score=0.12) if topic else []
        if not hits:
            hits = list(self._docs.values())

        token_boosts: Dict[str, float] = {}
        for d in hits:
            stayed = float(d.get("stayed_to_watch_pct") or 0.0)
            apv = float(d.get("avg_view_percentage") or 0.0)
            views = int(d.get("views") or 0)
            # Điểm chất lượng giữ chân thực tế của video trong RAG
            perf_mult = 1.0
            if stayed >= 65.0 or apv >= 72.0:
                perf_mult = 1.85
            elif stayed >= 52.0 or apv >= 60.0 or views >= 300:
                perf_mult = 1.35
            elif 0 < stayed < 38.0 and 0 < apv < 45.0:
                continue  # Bỏ qua các mẫu hình ảnh có hiệu suất giữ chân thấp

            vis_text = " ".join(d.get("visual_keywords_used") or [])
            for sc_m in (d.get("visual_metadata") or [])[:4]:
                if isinstance(sc_m, dict):
                    vis_text += f" {sc_m.get('visual_keyword', '')} {sc_m.get('stock_query', '')}"
            for tok in _tokenize_rag(vis_text):
                if "_" not in tok and len(tok) >= 4:
                    token_boosts[tok] = max(token_boosts.get(tok, 0.0), round(perf_mult, 2))
        return token_boosts

    def diversify_intra_video_scene_queries(
        self,
        job_id: int,
        scenes_strategy: List[Dict[str, Any]],
        topic: str = "",
        primary_entity: str = "",
        channel_id: Optional[str] = None,
        scenes_raw: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Sử dụng Mini-RAG để kiểm tra và ĐA DẠNG HÓA các câu truy vấn tìm kiếm (`youtube_search_query` / `smart_image_query`)
        giữa các phân cảnh BÊN TRONG CÙNG 1 VIDEO:
        - Nếu Cảnh #i và Cảnh #j trong cùng 1 video có câu truy vấn quá giống nhau (similarity >= 0.55),
          tự động bơm thêm từ khóa hành động/bối cảnh riêng của câu thoại Cảnh #j + góc quay điện ảnh khác biệt
          để tránh việc nhiều phân cảnh tải về cùng một đoạn video cắt!
        """
        if not scenes_strategy:
            return scenes_strategy

        narration_map: Dict[int, str] = {}
        for s in (scenes_raw or []):
            if isinstance(s, dict) and s.get("scene_id"):
                narration_map[int(s["scene_id"])] = str(s.get("narration") or s.get("visual_keyword") or "")

        angle_facets = [
            "extreme closeup action",
            "dramatic wide motion",
            "intense survival moment",
            "slow motion detail",
            "real footage encounter",
            "shocking behavior caught",
            "natural habitat action",
            "climax turning point",
        ]
        seen_queries: List[str] = []
        subj_clean = re.sub(r"[^\w\s]", " ", (primary_entity or topic or "")).strip()
        subj_tokens = {w.lower() for w in subj_clean.split() if len(w) >= 3}

        for idx, item in enumerate(scenes_strategy):
            if not isinstance(item, dict):
                continue
            sid = int(item.get("scene_id") or (idx + 1))
            t_name = str(item.get("tool_name") or "")
            if t_name == "fetch_clean_cinematic_portrait_or_broll":
                # Giữ nguyên các cụm truy vấn hành động con người ngắn gọn của Pexels/Pixabay cho tool chân dung/B-Roll
                continue
            q_key = (
                "action_or_concept_query"
                if t_name == "summon_epic_monster_cinematic_media"
                else "youtube_search_query"
                if t_name == "clone_youtube_short_peak_clip"
                else "primary_stock_query"
                if t_name == "search_pexels_pixabay_stock_video"
                else "smart_image_query"
            )
            cur_q = str(
                item.get(q_key)
                or item.get("action_or_concept_query")
                or item.get("youtube_search_query")
                or item.get("primary_stock_query")
                or item.get("smart_image_query")
                or ""
            ).strip()
            if not cur_q:
                continue

            # Nếu truy vấn đã được LLM Scope Judge thẩm định (scope_audited=True), tôn trọng 100% câu truy vấn của LLM
            # (chỉ can thiệp nếu 2 cảnh có câu truy vấn giống hệt nhau 100%).
            is_exact_dup = any(cur_q.lower() == prev_q.lower() for prev_q in seen_queries)
            if item.get("scope_audited") and not is_exact_dup:
                seen_queries.append(cur_q)
                continue

            max_sim = 1.0 if is_exact_dup else max((self.compute_text_similarity(cur_q, prev_q) for prev_q in seen_queries), default=0.0)
            if (is_exact_dup or (not item.get("scope_audited") and max_sim >= 0.85)) and not item.get("_rag_diversified"):
                clean_modifiers = ["closeup", "slow motion", "wide shot", "action", "habitat", "detail", "encounter", "motion"]
                mod = clean_modifiers[idx % len(clean_modifiers)]
                new_q = f"{cur_q} {mod}".strip() if mod not in cur_q.lower() else cur_q
                item[q_key] = new_q
                if item.get("youtube_search_query") and q_key != "smart_image_query":
                    item["youtube_search_query"] = new_q
                item["search_scope"] = "SCENE_SPECIFIC"
                item["_rag_diversified"] = True
                logger.info(
                    f"🧠 [MiniRAG Intra-Video Diversifier] Cảnh #{sid}: Phát hiện truy vấn trùng lặp 100% "
                    f"-> Thêm góc máy '{new_q}'"
                )
                cur_q = new_q

            seen_queries.append(cur_q)

        return scenes_strategy

    def try_claim_in_job_video(
        self,
        job_id: int,
        vid_id: str = "",
        url: str = "",
        title: str = "",
        scene_id: int = 0,
    ) -> bool:
        """
        Khóa nguyên tử (Atomic Claim) 1 video ứng viên cho Cảnh `scene_id` trong phạm vi `job_id` hiện tại:
        - Trả về `False` ngay lập tức nếu `vid_id` / `url` đã được bất kỳ cảnh nào khác trong CÙNG VIDEO sử dụng
          (bao gồm cả `hook_video_node` hoặc các luồng `asyncio.gather` đang chạy song song).
        - Trả về `False` nếu tiêu đề video mới trùng lặp ngữ nghĩa cao (sim >= 0.70) với video đã chọn ở cảnh khác
          trong cùng Job (ngăn chặn việc tải 2 bản re-upload của cùng 1 clip viral trên YouTube Shorts!).
        """
        keys = self._canonical_video_keys(vid_id=vid_id, url=url)
        if not keys and not title:
            return True

        with _RAG_LOCK:
            sess = self._get_or_create_job_session(job_id)
            claimed_ids: Set[str] = sess["claimed_video_ids"]
            if keys & claimed_ids:
                return False

            if title and len(title.strip()) >= 6:
                for prev in sess["claimed_titles"]:
                    if prev.get("scene_id") == scene_id:
                        continue
                    sim = self.compute_title_reupload_similarity(title, prev.get("title", ""))
                    if sim >= 0.85:
                        logger.info(
                            f"🛡️ [MiniRAG Intra-Video Guard] Từ chối ứng viên '{title[:45]}' tại Cảnh #{scene_id} "
                            f"vì trùng nội dung/reupload (sim={sim:.2f}) với Cảnh #{prev.get('scene_id')} ('{prev.get('title', '')[:40]}')"
                        )
                        return False

            claimed_ids.update(keys)
            if title and len(title.strip()) >= 4:
                sess["claimed_titles"].append({
                    "scene_id": scene_id,
                    "vid_id": vid_id or next(iter(keys), ""),
                    "title": title.strip(),
                })
            return True

    @staticmethod
    def compute_title_reupload_similarity(title_a: str, title_b: str) -> float:
        """
        Đo độ trùng lặp tiêu đề để phát hiện bản Re-upload y hệt (bỏ qua các từ khóa thể loại/tìm kiếm chung
        như shorts, animation, 3d, cgi, thalassophobia, megalophobia để tránh loại nhầm các video 3D khác nhau
        cùng chủ đề).
        """
        if not title_a or not title_b:
            return 0.0
        na = title_a.strip().lower()
        nb = title_b.strip().lower()
        if na == nb:
            return 1.0
        generic_tags = {
            "shorts", "short", "animation", "animated", "3d", "cgi", "vfx",
            "thalassophobia", "megalophobia", "edit", "4k", "hd", "viral",
            "fyp", "trending", "video", "monster", "giant", "massive",
        }
        uni_a = {t for t in _tokenize_rag(na) if "_" not in t}
        uni_b = {t for t in _tokenize_rag(nb) if "_" not in t}
        core_a = uni_a - generic_tags
        core_b = uni_b - generic_tags
        eff_a = core_a if len(core_a) >= 2 else uni_a
        eff_b = core_b if len(core_b) >= 2 else uni_b
        if not eff_a or not eff_b:
            return 0.0
        inter = len(eff_a & eff_b)
        union = len(eff_a | eff_b)
        return round(inter / max(1, union), 3)

    def release_in_job_video_claim(
        self,
        job_id: int,
        vid_id: str = "",
        url: str = "",
        title: str = "",
        scene_id: int = 0,
    ) -> None:
        """Giải phóng tiêu đề đã claim tạm nếu ứng viên tải lỗi hoặc bị loại ở bước kiểm duyệt."""
        with _RAG_LOCK:
            sess = self._get_or_create_job_session(job_id)
            if title:
                t_clean = title.strip()
                sess["claimed_titles"] = [
                    x for x in sess["claimed_titles"]
                    if not (x.get("scene_id") == scene_id and x.get("title") == t_clean)
                ]

    @trace_custom_span("MiniRAG.rank_video_candidates", run_type="retriever")
    def rank_video_candidates_for_scene(
        self,
        job_id: int,
        candidates: List[Dict[str, Any]],
        scene_narration: str = "",
        scene_query: str = "",
        topic: str = "",
        subject_entity: str = "",
        channel_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Tái sử dụng động cơ Hybrid BM25 + Cosine + Dữ liệu hiệu suất của `VideoMemoryRAG` để:
        1. Loại bỏ 100% các video ứng viên đã được dùng hoặc trùng tiêu đề (re-upload) với các cảnh khác TRONG CÙNG 1 VIDEO (`job_id`).
        2. Chấm điểm chất lượng & độ khớp ngữ cảnh (`rag_score`) của từng ứng viên với câu thoại phân cảnh (`scene_narration`),
           từ khóa chủ thể (`subject_entity`), thời lượng Peak lý tưởng (9s-55s), và từ khóa giữ chân cao trong lịch sử RAG.
        """
        if not candidates:
            return []

        with _RAG_LOCK:
            sess = self._get_or_create_job_session(job_id)
            claimed_ids = set(sess["claimed_video_ids"])
            claimed_titles = list(sess["claimed_titles"])

        target_context = f"{subject_entity} {scene_query} {scene_narration} {topic}".strip()
        perf_tokens = self.get_high_performing_visual_tokens(channel_id=channel_id, topic=topic, top_k=5)

        ranked: List[Dict[str, Any]] = []
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            vid_id = str(cand.get("id") or "").strip()
            vid_url = str(cand.get("url") or cand.get("webpage_url") or "").strip()
            title = str(cand.get("title") or "").strip()
            dur = float(cand.get("duration") or 0.0)
            base_score = float(cand.get("score") or 0.0)

            keys = self._canonical_video_keys(vid_id=vid_id, url=vid_url)
            # 1. Loại bỏ tuyệt đối nếu video đã xuất hiện ở bất kỳ cảnh nào khác trong CÙNG 1 VIDEO này
            if keys & claimed_ids:
                continue

            # 2. Kiểm tra chống lặp bản Re-upload (2 kênh YouTube Shorts đăng lại cùng 1 video với tiêu đề giống nhau)
            max_title_dup = max(
                (self.compute_title_reupload_similarity(title, prev.get("title", "")) for prev in claimed_titles),
                default=0.0,
            )
            if max_title_dup >= 0.85:
                continue

            # 3. Tính điểm khớp ngữ nghĩa RAG (BM25/Cosine) giữa tiêu đề video với câu thoại của cảnh
            semantic_sim = self.compute_text_similarity(title, target_context)
            narration_sim = self.compute_text_similarity(title, f"{scene_query} {scene_narration}")

            # 4. Điểm thưởng từ lịch sử hiệu suất cao trong RAG (Stayed-to-watch / APV)
            title_toks = {t for t in _tokenize_rag(title) if "_" not in t}
            rag_perf_bonus = sum(perf_tokens.get(t, 0.0) * 14.0 for t in title_toks if t in perf_tokens)

            # 5. Điểm thưởng cho các video có độ dài lý tưởng (10s - 50s) để lấy trọn đoạn Peak dài tự nhiên mà không bị lặp
            dur_bonus = 25.0 if 10.0 <= dur <= 50.0 else (10.0 if 7.0 <= dur < 10.0 else 0.0)
            novelty_bonus = (1.0 - max_title_dup) * 35.0

            rag_total_score = (
                base_score
                + (semantic_sim * 110.0)
                + (narration_sim * 85.0)
                + min(rag_perf_bonus, 45.0)
                + dur_bonus
                + novelty_bonus
            )
            enriched = dict(cand)
            enriched["rag_score"] = round(rag_total_score, 2)
            enriched["rag_semantic_sim"] = semantic_sim
            ranked.append(enriched)

        ranked.sort(key=lambda x: x.get("rag_score", 0.0), reverse=True)
        return ranked

    def verify_and_commit_in_job_clip(
        self,
        job_id: int,
        scene_id: int,
        media_path: str,
        vid_id: str = "",
        url: str = "",
        title: str = "",
        is_continuation_slice: bool = False,
    ) -> Tuple[bool, Optional[int]]:
        """
        Kiểm tra chữ ký thị giác (3-frame 64-bit dHash) của đoạn video vừa cắt so với toàn bộ các cảnh
        ĐÃ CHỐT TRONG CÙNG 1 VIDEO (`job_id`):
        - Ngăn chặn 100% trường hợp 2 link YouTube khác nhau (và tiêu đề khác nhau) nhưng bên trong lại là
          cùng 1 cảnh quay được re-upload!
        - Nếu `is_continuation_slice=True` (khúc nối tiếp của 1 đoạn Long Peak): kiểm tra xem khúc nối tiếp
          có thực sự chuyển động sang khung hình mới hay không. Nếu video gốc chỉ là 1 bức ảnh đứng yên hoặc
          đoạn loop ngắn lặp đi lặp lại (dHash trùng khớp với khúc trước), từ chối ngay để không bị lặp cảnh!
        """
        if not media_path or not Path(media_path).exists():
            return False, None

        try:
            from app.graph.evaluators.visual_critic import VisualCritic
            sig = VisualCritic._media_signature(str(media_path))
        except Exception as e:
            logger.debug(f"[MiniRAG Visual Verify] Skip signature check ({e})")
            sig = []

        keys = self._canonical_video_keys(vid_id=vid_id, url=url or media_path)

        with _RAG_LOCK:
            sess = self._get_or_create_job_session(job_id)
            seen_sigs: Dict[int, List[str]] = sess["committed_signatures"]

            if sig:
                # Chặn nghiêm ngặt mọi sự trùng lặp khung hình (kể cả trùng lặp 1 phần video hoặc cùng góc máy):
                # - >= 1 khung hình khớp chặt (Hamming <= 7)
                # - Hoặc >= 2 khung hình khớp trung bình (Hamming <= 10)
                strict_thresh = 5 if is_continuation_slice else 7
                multi_thresh = 8 if is_continuation_slice else 10

                for prev_sid, prev_sig in seen_sigs.items():
                    if prev_sid == scene_id or not prev_sig:
                        continue
                    strict_hits = sum(
                        1 for h in sig
                        if any(sum(a != b for a, b in zip(h, ph)) <= strict_thresh for ph in prev_sig)
                    )
                    multi_hits = sum(
                        1 for h in sig
                        if any(sum(a != b for a, b in zip(h, ph)) <= multi_thresh for ph in prev_sig)
                    )
                    if strict_hits >= 1 or multi_hits >= min(2, len(sig), len(prev_sig)):
                        logger.warning(
                            f"🚫 [MiniRAG Intra-Video Visual Guard] Cảnh #{scene_id} ({Path(media_path).name}) "
                            f"bị trùng khung hình thị giác với Cảnh #{prev_sid} (strict={strict_hits}, multi={multi_hits}) -> Loại bỏ để chống lặp cảnh cắt!"
                        )
                        return False, prev_sid

                seen_sigs[scene_id] = sig

            sess["claimed_video_ids"].update(keys)
            if title and len(title.strip()) >= 4:
                if not any(x.get("scene_id") == scene_id and x.get("title") == title.strip() for x in sess["claimed_titles"]):
                    sess["claimed_titles"].append({
                        "scene_id": scene_id,
                        "vid_id": vid_id or next(iter(keys), ""),
                        "title": title.strip(),
                    })
            return True, None

    @staticmethod
    def _has_embedded_text_or_collage_artifacts(image_input: Any) -> Tuple[bool, str]:
        """
        Kiểm tra xem ảnh/khung hình có chứa:
        - Hộp chữ infographic / phụ đề TikTok / tiêu đề đóng cứng ở nửa trên hoặc nửa dưới (`top_white_text_rows`, `bot_white_text_rows`)
        - Bìa sách / Quote Poster / Sơ đồ 2D nền trắng hoặc xám (`white_paper_ratio`, `neutral_grey_bg`)
        - Vách ngăn trắng chia đôi khung hình kiểu meme/collage (`has_white_h_divider`, `has_white_v_divider`)
        """
        import io
        from PIL import Image, ImageFilter, ImageStat

        im_obj = None
        close_after = False
        try:
            if isinstance(image_input, Image.Image):
                im_obj = image_input
            elif isinstance(image_input, (bytes, bytearray)):
                im_obj = Image.open(io.BytesIO(image_input))
                close_after = True
            else:
                im_obj = Image.open(str(image_input))
                close_after = True

            rgb_small = im_obj.convert("RGB").resize((160, 240))
            g_small = rgb_small.convert("L")
            tot_px = 160 * 240
            px_rgb = rgb_small.load()
            px_g = g_small.load()
            # Chỉ coi là nền giấy trắng (white_paper), nền xám phẳng (grey_bg) hoặc nền đen poster kỹ thuật số (flat_black_poster)
            # khi các điểm ảnh PHẲNG TUYỆT ĐỐI kiểu đồ họa 2D/slide chữ (chênh lệch lân cận <= 1, độ bão hòa màu thấp),
            # tránh báo nhầm tuyết trắng, sương mù, hoặc vùng nước sâu/bóng tối rừng đêm trong cảnh quay thật!
            flat_white_px = 0
            flat_grey_px = 0
            flat_black_px = 0
            for y_g in range(240):
                for x_g in range(159):
                    rgb_c = px_rgb[x_g, y_g]
                    sat_c = max(rgb_c) - min(rgb_c)
                    g_c = px_g[x_g, y_g]
                    diff_nx = abs(g_c - px_g[x_g + 1, y_g])
                    if min(rgb_c) >= 242 and sat_c <= 4 and diff_nx <= 1:
                        flat_white_px += 1
                    elif sat_c <= 3 and 110 <= g_c <= 200 and diff_nx <= 1:
                        flat_grey_px += 1
                    elif max(rgb_c) <= 20 and sat_c <= 5 and diff_nx <= 1:
                        flat_black_px += 1
            white_paper_ratio = flat_white_px / tot_px
            neutral_grey_bg = flat_grey_px / tot_px
            flat_black_poster_ratio = flat_black_px / tot_px
            edges = g_small.filter(ImageFilter.FIND_EDGES)
            sharp_mean = float(ImageStat.Stat(edges).mean[0])
            px_e = edges.load()
            text_rows = 0
            zero_edge_rows = 0
            for y_r in range(15, 225):
                row_vals = [px_e[x_c, y_r] for x_c in range(12, 148)]
                high_transitions = sum(1 for k_i in range(len(row_vals) - 1) if abs(row_vals[k_i] - row_vals[k_i + 1]) > 46)
                if high_transitions >= 16:
                    text_rows += 1
                elif high_transitions <= 1 and max(row_vals) <= 18:
                    zero_edge_rows += 1

            # Phát hiện hộp chữ trắng/vàng có viền đen trên nền tối ở nửa dưới hoặc nửa trên (Infographic / TikTok caption / banner 1-2 dòng):
            # Phụ đề thật ở khung 160x240 luôn tạo thành DẢI DÒNG LIỀN MẠCH (>= 4 hàng ngang liên tiếp có nét chữ trắng/vàng trung tính + viền đen sắc),
            # khác hẳn sóng nước lấp lánh, vảy/răng động vật, tán lá hay rạn san hô (vốn phân bố rải rác từng hàng lẻ hoặc phủ đều cả vùng giữa khung hình!).
            def _is_caption_stroke_triplet(x_c: int, y_r: int) -> bool:
                g0, g1, g2 = px_g[x_c, y_r], px_g[x_c + 1, y_r], px_g[x_c + 2, y_r]
                g_hi = max(g0, g1, g2)
                g_lo = min(g0, g1, g2)
                if g_hi < 204 or g_lo > 68 or (g_hi - g_lo) < 126:
                    return False
                # Điểm sáng của chữ phụ đề thường là trắng trung tính hoặc vàng/cam sáng (không phải ánh nước xanh lơ/xanh lục tự nhiên)
                hi_idx = 0 if g0 == g_hi else (1 if g1 == g_hi else 2)
                r_h, g_h, b_h = px_rgb[x_c + hi_idx, y_r]
                is_neutral_white = min(r_h, g_h, b_h) >= 195 and (max(r_h, g_h, b_h) - min(r_h, g_h, b_h)) <= 24
                is_warm_yellow_caption = r_h >= 210 and g_h >= 175 and b_h <= 95
                return is_neutral_white or is_warm_yellow_caption

            def _scan_caption_zone(y_start: int, y_end: int) -> Tuple[int, int, int, int]:
                white_rows = 0
                dense_rows = 0
                cur_white_run = 0
                max_white_run = 0
                cur_dense_run = 0
                max_dense_run = 0
                for y_r in range(y_start, y_end):
                    wt_trans = sum(1 for x_c in range(14, 144) if _is_caption_stroke_triplet(x_c, y_r))
                    if wt_trans >= 10:
                        white_rows += 1
                        cur_white_run += 1
                        if cur_white_run > max_white_run:
                            max_white_run = cur_white_run
                    else:
                        cur_white_run = 0
                    if wt_trans >= 14:
                        dense_rows += 1
                        cur_dense_run += 1
                        if cur_dense_run > max_dense_run:
                            max_dense_run = cur_dense_run
                    else:
                        cur_dense_run = 0
                return white_rows, dense_rows, max_white_run, max_dense_run

            top_white_text_rows, top_dense_banner_rows, top_max_w_run, top_max_d_run = _scan_caption_zone(6, 92)
            mid_white_text_rows, _, _, _ = _scan_caption_zone(92, 148)
            bot_white_text_rows, bot_dense_banner_rows, bot_max_w_run, bot_max_d_run = _scan_caption_zone(148, 238)

            def _row_white_cnt(y_idx: int) -> int:
                return sum(
                    1 for x_c in range(8, 152)
                    if min(px_rgb[x_c, y_idx]) >= 238 and (max(px_rgb[x_c, y_idx]) - min(px_rgb[x_c, y_idx])) <= 4
                )

            def _col_white_cnt(x_idx: int) -> int:
                return sum(
                    1 for y_r in range(12, 228)
                    if min(px_rgb[x_idx, y_r]) >= 238 and (max(px_rgb[x_idx, y_r]) - min(px_rgb[x_idx, y_r])) <= 4
                )

            # Vạch kẻ trắng chia đôi khung ghép (chỉ bắt vạch kẻ mỏng 1-5px, KHÔNG bắt nền tuyết trắng tự nhiên dày!)
            has_white_h_divider = any(
                _row_white_cnt(y_d) >= 136 and _row_white_cnt(max(6, y_d - 6)) < 55 and _row_white_cnt(min(233, y_d + 6)) < 55
                for y_d in range(75, 165)
            )
            has_white_v_divider = any(
                _col_white_cnt(x_d) >= 202 and _col_white_cnt(max(4, x_d - 6)) < 75 and _col_white_cnt(min(155, x_d + 6)) < 75
                for x_d in range(50, 110)
            )

            # Phát hiện đường ghép ngang cắt đôi 2 bức ảnh (2-panel top/bottom split collage thẳng tắp >= 92% chiều ngang)
            def _row_v_jump(y_idx: int) -> int:
                return sum(1 for x_c in range(8, 152) if abs(px_g[x_c, y_idx] - px_g[x_c, y_idx + 1]) >= 42)

            has_split_collage_seam = any(
                _row_v_jump(y_d) >= 132
                and max(_row_v_jump(y_d - 4), _row_v_jump(y_d + 4)) <= 32
                for y_d in range(96, 144)
            )

            # Phát hiện khung ngang 16:9/4:3 bị đóng viền mờ trên & dưới (baked-in letterbox blur bars) trong video dọc 9:16
            orig_w, orig_h = im_obj.size
            has_letterbox_blur_bars = False
            if orig_h > orig_w:
                top_sh = float(ImageStat.Stat(edges.crop((8, 4, 152, 36))).mean[0])
                mid_sh = float(ImageStat.Stat(edges.crop((8, 60, 152, 180))).mean[0])
                bot_sh = float(ImageStat.Stat(edges.crop((8, 204, 152, 236))).mean[0])
                if top_sh < 5.2 and bot_sh < 5.2 and mid_sh >= 11.5:
                    max_top_seam = max(
                        sum(1 for x_c in range(8, 152) if abs(px_g[x_c, y_s] - px_g[x_c, y_s + 1]) >= 18) / 144.0
                        for y_s in range(18, 68)
                    )
                    max_bot_seam = max(
                        sum(1 for x_c in range(8, 152) if abs(px_g[x_c, y_s] - px_g[x_c, y_s + 1]) >= 18) / 144.0
                        for y_s in range(172, 222)
                    )
                    if max_top_seam >= 0.78 and max_bot_seam >= 0.78:
                        has_letterbox_blur_bars = True

            if white_paper_ratio > 0.45:
                return True, f"white_paper={white_paper_ratio:.2f}"
            if neutral_grey_bg > 0.48:
                return True, f"grey_bg={neutral_grey_bg:.2f}"
            if sharp_mean > 72.0 and text_rows >= 28:
                return True, f"sharp_mean={sharp_mean:.1f}"
            if has_white_h_divider or has_white_v_divider or has_split_collage_seam:
                return True, "collage_divider"
            if has_letterbox_blur_bars:
                return True, f"letterbox_blur_bars(top={top_sh:.1f},bot={bot_sh:.1f},mid={mid_sh:.1f})"
            if 28 <= text_rows <= 145 and zero_edge_rows >= 32 and (white_paper_ratio > 0.22 or flat_black_poster_ratio > 0.34):
                return True, f"poster_text_rows={text_rows}"
            is_fullframe_natural_sparkle = (mid_white_text_rows >= 14 and max(top_max_d_run, bot_max_d_run) < 6)
            has_bot_caption_band = (bot_max_d_run >= 4 or (bot_white_text_rows >= 10 and bot_max_w_run >= 4))
            has_top_caption_band = (top_max_d_run >= 4 or (top_white_text_rows >= 10 and top_max_w_run >= 4))
            if not is_fullframe_natural_sparkle and (has_bot_caption_band or has_top_caption_band):
                return (
                    True,
                    f"embedded_caption_box(bot={bot_white_text_rows}/run={bot_max_w_run}:{bot_max_d_run},"
                    f"top={top_white_text_rows}/run={top_max_w_run}:{top_max_d_run})",
                )
            return False, ""
        except Exception as e:
            return False, str(e)
        finally:
            if close_after and im_obj is not None:
                try:
                    im_obj.close()
                except Exception:
                    pass

    def verify_and_commit_in_job_image(
        self,
        job_id: int,
        scene_id: int,
        image_path: str,
        url: str = "",
        context_text: str = "",
    ) -> bool:
        """
        Kiểm tra chữ ký thị giác 64-bit dHash và tính toàn vẹn của từng bức ảnh trong kho 15-17 ảnh/video:
        - Đảm bảo ảnh mới tải về KHÔNG trùng lặp thị giác (Hamming distance <= 6) với BẤT KỲ ảnh nào
          đã chọn trước đó trong cùng phân cảnh (`pose_images`) HOẶC ở các phân cảnh khác trong cùng 1 video (`job_id`).
        - Kiểm tra nhanh kích thước (>= 480px) và loại bỏ ảnh đen/lỗi đơn sắc/infographic có chữ hoặc sai mùa (cỏ xanh trong cảnh tuyết).
        """
        if not image_path or not Path(image_path).exists():
            return False
        try:
            if Path(image_path).stat().st_size < 4000:
                return False
            from PIL import Image
            from app.graph.evaluators.visual_critic import VisualCritic

            with Image.open(str(image_path)) as im:
                w, h = im.size
                if w < 480 or h < 480:
                    logger.info(f"🚫 [MiniRAG LowRes] Loại bỏ ảnh độ phân giải thấp ({w}x{h} < 480px) tại Cảnh #{scene_id}")
                    return False
                has_bad_art, art_reason = self._has_embedded_text_or_collage_artifacts(im)
                if has_bad_art:
                    logger.info(
                        f"🚫 [MiniRAG Anti-Infographic/BookCover] Loại bỏ ảnh lỗi/chứa chữ/sơ đồ 2D/UI game tại Cảnh #{scene_id} "
                        f"({art_reason})"
                    )
                    return False
            if VisualCritic._is_solid_color_or_black(str(image_path)):
                return False
            img_hash = VisualCritic._compute_image_hash(str(image_path))
        except Exception as e:
            logger.debug(f"[MiniRAG Image Verify] Skip invalid image '{image_path}': {e}")
            return False

        keys = self._canonical_video_keys(url=url or image_path)
        abs_p = str(Path(image_path).resolve())

        with _RAG_LOCK:
            sess = self._get_or_create_job_session(job_id)
            committed_paths: Set[str] = sess.setdefault("committed_image_paths", set())
            if abs_p in committed_paths:
                return True

            seen_sigs: Dict[int, List[str]] = sess["committed_signatures"]
            if img_hash:
                for prev_sid, prev_hashes in seen_sigs.items():
                    for ph in (prev_hashes or []):
                        if len(ph) == len(img_hash) and sum(a != b for a, b in zip(img_hash, ph)) <= 9:
                            logger.info(
                                f"🚫 [MiniRAG Multi-Image Guard] Loại bỏ ảnh trùng lặp thị giác tại Cảnh #{scene_id} "
                                f"({Path(image_path).name} trùng dHash với Cảnh #{prev_sid})!"
                            )
                            return False
                seen_sigs.setdefault(scene_id, []).append(img_hash)

            committed_paths.add(abs_p)
            sess["claimed_video_ids"].update(keys)
            return True


_GLOBAL_MINI_RAG: Optional[VideoMemoryRAG] = None


def get_video_memory_rag() -> VideoMemoryRAG:
    """Singleton truy cập nhanh hệ thống Mini-RAG."""
    global _GLOBAL_MINI_RAG
    if _GLOBAL_MINI_RAG is None:
        _GLOBAL_MINI_RAG = VideoMemoryRAG()
    return _GLOBAL_MINI_RAG


def archive_job_to_rag_and_cleanup_files(job_id: int) -> Dict[str, Any]:
    """
    Sau khi 1 VideoJob ĐÃ ĐƯỢC UPLOAD LÊN YOUTUBE (đã có `youtube_video_id`):
    1. Trích xuất toàn bộ Kịch bản + Metadata Hình ảnh lưu vào Mini-RAG (`yt:<youtube_video_id>`),
       đồng thời ghi nhận `UsedTopic` và lịch sử media (`recent_media_history.json`).
       (Nếu job chưa có `youtube_video_id` — ví dụ nháp, lỗi, test nội bộ — TUYỆT ĐỐI KHÔNG lưu vào bộ nhớ).
    2. Xóa gọn `render_manifest` nặng trong DB (chỉ giữ lại `scenes_json` gọn nhẹ chứa lời dẫn + metadata ảnh).
    3. Xóa ngay file `.mp4` nặng trong `storage/outputs/` và các file tạm của job trong `storage/temp/`.
    """
    rag = get_video_memory_rag()
    freed_bytes = 0
    removed_files: List[str] = []
    doc_id: Optional[str] = None

    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            return {"ok": False, "error": f"Job #{job_id} not found"}

        vid = (job.youtube_video_id or "").strip()
        # Chỉ xóa file video nặng khi video ĐÃ ĐƯỢC ĐẨY LÊN YOUTUBE (có youtube_video_id) hoặc đã PUBLISHED/FAILED
        should_cleanup_files = bool(vid or job.status in ("PUBLISHED", "FAILED"))

        meta = extract_visual_and_script_metadata(
            scenes_json_raw=job.scenes_json,
            render_manifest_raw=job.render_manifest,
            fallback_script=job.script or job.description,
            fallback_hook=getattr(job, "hook_text", None) or job.title,
        )

        # CHỈ LƯU VÀO BỘ NHỚ (Mini-RAG + UsedTopic + MediaHistory) KHI VIDEO ĐÃ ĐĂNG LÊN YOUTUBE (có youtube_video_id)
        if vid:
            doc_id = f"yt:{vid}"
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
                youtube_video_id=vid,
                job_id=job.id,
                views=job.views or 0,
                avg_view_percentage=job.avg_view_percentage,
                stayed_to_watch_pct=job.stayed_to_watch_pct,
                swipe_away_pct=job.swipe_away_pct,
                avg_view_duration_sec=job.avg_view_duration_sec,
                published_at=job.created_at.strftime("%Y-%m-%d") if job.created_at else None,
            )

            # Ghi nhận UsedTopic khi và chỉ khi video đã đăng lên YouTube
            try:
                from app.database.models import UsedTopic
                topic_val = (job.topic or job.title or "").strip()
                if topic_val and job.target_channel_id:
                    UsedTopic.register(
                        db,
                        channel_id=job.target_channel_id,
                        topic=topic_val,
                        niche="",
                        job_id=job.id,
                    )
            except Exception as e_ut:
                logger.debug(f"[MiniRAG] Không thể ghi nhận UsedTopic cho Job #{job_id}: {e_ut}")

            # Ghi nhận media URLs của video đã đăng lên YouTube vào kho lịch sử cross-job
            try:
                from app.services.media.media_history import commit_published_media_to_history
                urls_to_commit = [
                    s.get("media_source_url") or s.get("media_fingerprint")
                    for s in (meta.get("visual_metadata") or [])
                    if s.get("media_source_url") or s.get("media_fingerprint")
                ]
                commit_published_media_to_history(urls_to_commit, job_id=job.id, youtube_video_id=vid)
            except Exception as e_mh:
                logger.debug(f"[MiniRAG] Không thể lưu media history cho Job #{job_id}: {e_mh}")

        # Lưu lại bản scenes_json siêu gọn (chỉ chứa lời dẫn + metadata hình ảnh, bỏ word_timings dài dòng)
        compact_scenes_payload = {
            "title": job.title,
            "hook": meta["hook_text"],
            "pinned_comment": meta["pinned_comment"],
            "scenes": meta["visual_metadata"],
        }
        job.scenes_json = json.dumps(compact_scenes_payload, ensure_ascii=False)
        job.render_manifest = None

        if should_cleanup_files:
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
        "indexed_in_memory": bool(vid),
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
    protected_job_ids: Set[int] = set()
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
                    j.status in ("DRAFT", "PROCESSING", "SCRIPT_READY", "AUDIO_READY", "MEDIA_READY", "RENDERED", "AWAITING_APPROVAL", "WAITING_QUOTA_RESET", "RENDERING")
                    or (j.status == "SCHEDULED" and not (j.youtube_video_id and str(j.youtube_video_id).strip()))
                )
                if needs_local_file:
                    protected_job_ids.add(j.id)
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

    # 2. Dọn storage/temp/ (Bảo vệ tuyệt đối các job đang chạy / mới tạo trong 2 giờ qua để tránh xóa mất full_voiceover.mp3 đang render)
    temp_dir = Path(settings.TEMP_DIR)
    if temp_dir.exists():
        temp_cutoff_ts = datetime.now().timestamp() - (max(float(cache_max_age_hours or 0.0), 2.0) * 3600.0)
        active_job_folders = {
            f"job_{jid}" for jid in protected_job_ids
        } | {
            f"render_tmp_{jid}" for jid in protected_job_ids
        }
        for f in temp_dir.rglob("*"):
            if not f.is_file() or f.name == ".gitkeep":
                continue
            if f.name in protected_files:
                continue
            try:
                rel_parts = f.relative_to(temp_dir).parts
                if rel_parts and rel_parts[0] in active_job_folders:
                    continue
                if cache_max_age_hours > 0 and f.stat().st_mtime > temp_cutoff_ts:
                    continue
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

