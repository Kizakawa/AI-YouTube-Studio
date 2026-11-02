"""app.graph.nodes.media.media_helpers — Các hàm tiện ích dùng chung cho các Node Media."""
import asyncio
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple

from app.schemas.contracts import ScriptContract, SceneContract
from app.agents.media_agent import MediaAgent, detect_real_entity, match_best_pool_photo, clean_scene_search_query
from app.agents.image_agent import ImageAgent
from app.agents.visual_director_agent import VisualDirectorAgent
from app.agents.video_strategist_agent import VideoStrategistAgent
from app.agents.visual_curation_agent import VisualCurationAgent
from app.agents.ai_video_agent import AIVideoAgent
from app.graph.evaluators.visual_critic import VisualCritic
from app.graph.state import VideoWorkflowState

logger = logging.getLogger(__name__)

def _safe_mtime(p: Path) -> float:
    """Đọc st_mtime an toàn khi có luồng song song vừa xóa file tạm (.unlink) để chống WinError 2."""
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _safe_size(p: Path) -> int:
    """Đọc st_size an toàn khi có luồng song song vừa xóa file tạm (.unlink) để chống WinError 2."""
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _extract_hook_candidate_vid_id(src_vp: Path) -> str:
    """
    Trích xuất mã định danh gốc (YouTube Video ID 11 ký tự hoặc stem) từ file ứng viên
    trong `hook_candidates/` (`hook_peak_clean_yt_{idx}_{sub}_{yt_id}.mp4`,
    `hook_peak_clean_yt_{idx}_{sub}.mp4` ánh xạ ngược về `yt_hook_cand_{idx}_{yt_id}.*`,
    hoặc `yt_hook_cand_{idx}_{yt_id}.mp4`) để chống dùng trùng cùng 1 video giữa Hook Cảnh 1/2 và Cảnh thân.
    """
    stem = src_vp.stem
    m_direct = re.search(
        r"(?:yt_hook_cand_\d+|hook_peak_clean_(?:yt_\d+_\d+|ext_\d+|alt2_\d+))_([A-Za-z0-9_-]{11})$",
        stem,
    )
    if m_direct:
        return m_direct.group(1)

    m_idx = re.match(r"^hook_peak_clean_(?:yt|ext|alt2)_(\d+)(?:_\d+)?$", stem)
    if m_idx and src_vp.parent.exists():
        cand_idx = m_idx.group(1)
        for sib in sorted(src_vp.parent.glob(f"yt_hook_cand_{cand_idx}_*.*")):
            m_sib = re.search(r"yt_hook_cand_\d+_([A-Za-z0-9_-]{11})$", sib.stem)
            if m_sib:
                return m_sib.group(1)
    return stem


def _collect_active_scene_source_paths(
    scenes_media: List[Dict[str, Any]],
    exclude_scene_ids: Optional[Set[int]] = None,
) -> Tuple[Set[str], Set[str]]:
    """
    Thu thập tập đường dẫn vật lý (`media_path` + `full_peak_path`) và tập YouTube Video ID
    đã được các phân cảnh hợp lệ trong Job chiếm giữ để ngăn chặn việc dùng lại cùng một clip.
    """
    excl = exclude_scene_ids or set()
    used_paths: Set[str] = set()
    used_vid_ids: Set[str] = set()
    for m in scenes_media or []:
        if not isinstance(m, dict):
            continue
        sid = int(m.get("scene_id") or 0)
        if sid in excl:
            continue
        for pk in ("media_path", "full_peak_path"):
            p_raw = m.get(pk)
            if p_raw:
                p_obj = Path(str(p_raw))
                if p_obj.exists():
                    used_paths.add(str(p_obj.resolve()))
                vid_from_p = _extract_hook_candidate_vid_id(p_obj)
                if len(vid_from_p) == 11 and vid_from_p != p_obj.stem:
                    used_vid_ids.add(vid_from_p)
        for uk in ("youtube_id", "source_url", "media_url", "url"):
            u_val = str(m.get(uk) or "").strip()
            if not u_val:
                continue
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", u_val):
                used_vid_ids.add(u_val)
            else:
                m_u = re.search(r"(?:v=|/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})", u_val)
                if m_u:
                    used_vid_ids.add(m_u.group(1))
    return used_paths, used_vid_ids


def _scene_audio_duration(work_dir: Path, scene_id: int, default: float = 6.0) -> float:
    """
    Độ dài thực tế lời thoại của cảnh (giây) để clip thay thế đủ dài, không bị renderer lặp lại (stream_loop).
    Đọc audio/scene_XXX.meta.json → ffprobe mp3 → mặc định.
    """
    try:
        import json as _json
        meta = work_dir / "audio" / f"scene_{scene_id:03d}.meta.json"
        if meta.exists():
            ms = _json.loads(meta.read_text(encoding="utf-8")).get("duration_ms") or 0
            if ms > 300:
                return round(ms / 1000.0 + 0.3, 2)
        mp3 = work_dir / "audio" / f"scene_{scene_id:03d}.mp3"
        if mp3.exists():
            from app.utils.ffmpeg_helper import get_duration_ms
            ms = get_duration_ms(str(mp3))
            if ms > 300:
                return round(ms / 1000.0 + 0.3, 2)
    except Exception:
        pass
    return default

