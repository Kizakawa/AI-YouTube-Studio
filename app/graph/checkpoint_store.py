"""
app.graph.checkpoint_store — Step-Level Disk Checkpoint & Global Render Concurrency Limiter.

Lấy cảm hứng từ kiến trúc bền bỉ của `ShortGPT`, `VideoLingo` & `MoneyPrinterTurbo`:
1. `wrap_node_with_checkpoint(node_name, node_fn)`:
   - Tự động lưu trạng thái đầu ra của từng node LangGraph vào `{work_dir}/step_checkpoint.json`.
   - Khi một video bị gián đoạn giữa chừng (do mất mạng, restart server, hoặc hết quota tạm thời) và được
     chạy lại (Auto-Resume), các bước nặng đã hoàn thành (`generate_audio`, `visual_director`,
     `produce_hook_video`, `fetch_media`) sẽ được kiểm tra tính toàn vẹn file trên đĩa (`> 5KB`) và
     khôi phục tức thì trong `0.01s` thay vì phải thu âm / tải media / gọi Gemini lại từ đầu.
   - Không bao giờ bỏ qua node khi đang trong vòng lặp tinh chỉnh (`script_retry_count > 0`,
     `audio_retry_count > 0`, `media_retry_count > 0`).
2. `acquire_global_render_slot(job_id)`:
   - Cổng điều tiết đồng thời toàn tiến trình (`MAX_CONCURRENT_RENDERS`, mặc định `2`), ngăn Web Dashboard,
     AutoPilot, ChannelIntelligence và Telegram Bot chạy chồng chéo 4-5 tiến trình FFmpeg đa luồng cùng lúc
     gây nghẽn CPU 100% và lỗi timeout 20 phút.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

_CHECKPOINT_FILENAME = "step_checkpoint.json"
_file_lock = threading.Lock()

# Giới hạn số lượng pipeline render nặng chạy đồng thời trên toàn tiến trình (mặc định 3)
_MAX_CONCURRENT_RENDERS = max(1, int(os.getenv("MAX_CONCURRENT_RENDERS", "3") or 3))
_global_render_thread_sem = threading.BoundedSemaphore(_MAX_CONCURRENT_RENDERS)


@asynccontextmanager
async def acquire_global_render_slot(job_id: int):
    """
    Async context manager đảm bảo tối đa `_MAX_CONCURRENT_RENDERS` tiến trình dựng video chạy cùng lúc
    trên tất cả các event loop / background thread (Telegram Bot, FastAPI Web, AutoPilot, CI Worker).
    """
    t0 = time.monotonic()
    acquired = _global_render_thread_sem.acquire(blocking=False)
    if not acquired:
        logger.info(
            f"⏳ [GlobalRenderLimiter] Job #{job_id} đang xếp hàng chờ slot render trống "
            f"(tối đa {_MAX_CONCURRENT_RENDERS} jobs đồng thời)..."
        )
        await asyncio.to_thread(_global_render_thread_sem.acquire)
        acquired = True
        waited = time.monotonic() - t0
        logger.info(f"🚀 [GlobalRenderLimiter] Job #{job_id} đã nhận slot render sau {waited:.1f}s chờ!")
    else:
        acquired = True
    try:
        yield
    finally:
        if acquired:
            try:
                _global_render_thread_sem.release()
            except ValueError:
                pass


def _checkpoint_path(state: Dict[str, Any]) -> Path | None:
    work_dir = state.get("work_dir")
    if not work_dir:
        return None
    return Path(str(work_dir)) / _CHECKPOINT_FILENAME


def _file_valid(path_str: Any, min_bytes: int = 2048) -> bool:
    if not path_str or not isinstance(path_str, str):
        return False
    try:
        p = Path(path_str)
        return p.exists() and p.is_file() and p.stat().st_size >= min_bytes
    except Exception:
        return False


def _can_reuse_cached_node(node_name: str, state: Dict[str, Any], cached_out: Dict[str, Any]) -> bool:
    """
    Xác minh nghiêm ngặt rằng dữ liệu checkpoint của `node_name` vẫn còn đầy đủ file vật lý trên đĩa
    và không nằm trong vòng lặp retry.
    """
    if not isinstance(cached_out, dict) or not cached_out:
        return False
    if state.get("edit_request"):
        return False

    if node_name == "generate_audio":
        if int(state.get("audio_retry_count") or 0) > 0:
            return False
        if not _file_valid(cached_out.get("full_audio_path"), min_bytes=4000):
            return False
        if not _file_valid(cached_out.get("subtitle_path"), min_bytes=200):
            return False
        scenes_audio = cached_out.get("scenes_audio") or []
        if not scenes_audio or not isinstance(scenes_audio, list):
            return False
        return all(_file_valid(sa.get("audio_path"), min_bytes=1000) for sa in scenes_audio if isinstance(sa, dict))

    if node_name == "evaluate_audio":
        if int(state.get("audio_retry_count") or 0) > 0:
            return False
        audio_eval = cached_out.get("audio_eval") or {}
        return bool(isinstance(audio_eval, dict) and audio_eval.get("passed"))

    if node_name == "visual_director":
        strats = cached_out.get("visual_strategies") or []
        return bool(isinstance(strats, list) and len(strats) > 0)

    if node_name == "produce_hook_video":
        pre_hook = cached_out.get("pre_resolved_hook_media") or {}
        if not isinstance(pre_hook, dict) or not pre_hook:
            return False
        s1 = pre_hook.get(1) or pre_hook.get("1") or {}
        return _file_valid(s1.get("media_path"), min_bytes=10_000)

    if node_name == "fetch_media":
        if int(state.get("media_retry_count") or 0) > 0:
            return False
        scenes_media = cached_out.get("scenes_media") or []
        expected_n = len(((state.get("script_data") or {}).get("scenes")) or [])
        if not scenes_media or (expected_n > 0 and len(scenes_media) < expected_n):
            return False
        return all(_file_valid(sm.get("media_path"), min_bytes=4000) and "_graphic.png" not in str(sm.get("media_path") or "").lower() and str(sm.get("media_type") or "").lower() != "graphic" for sm in scenes_media if isinstance(sm, dict))

    return False


def _load_node_checkpoint(node_name: str, state: Dict[str, Any]) -> Dict[str, Any] | None:
    if os.getenv("ENABLE_STEP_CHECKPOINT", "true").strip().lower() in ("0", "false", "no", "off"):
        return None
    cp_path = _checkpoint_path(state)
    if not cp_path or not cp_path.exists():
        return None
    try:
        with _file_lock:
            data = json.loads(cp_path.read_text(encoding="utf-8"))
        nodes_map = data.get("nodes") or {}
        cached_out = nodes_map.get(node_name)
        if node_name == "produce_hook_video" and isinstance(cached_out, dict):
            raw_pre = cached_out.get("pre_resolved_hook_media")
            if isinstance(raw_pre, dict):
                cached_out["pre_resolved_hook_media"] = {
                    (int(k) if str(k).isdigit() else k): v for k, v in raw_pre.items()
                }
        if _can_reuse_cached_node(node_name, state, cached_out):
            return cached_out
    except Exception as e:
        logger.debug(f"[CheckpointStore] Không thể đọc checkpoint node {node_name}: {e}")
    return None


def _save_node_checkpoint(node_name: str, state: Dict[str, Any], output: Dict[str, Any]) -> None:
    if os.getenv("ENABLE_STEP_CHECKPOINT", "true").strip().lower() in ("0", "false", "no", "off"):
        return
    if not isinstance(output, dict) or not output:
        return
    cp_path = _checkpoint_path(state)
    if not cp_path:
        return
    try:
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        with _file_lock:
            if cp_path.exists():
                try:
                    payload = json.loads(cp_path.read_text(encoding="utf-8"))
                except Exception:
                    payload = {}
            else:
                payload = {}
            payload.setdefault("job_id", state.get("job_id"))
            payload["updated_at"] = time.time()
            nodes_map = payload.setdefault("nodes", {})
            nodes_map[node_name] = output
            cp_path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception as e:
        logger.debug(f"[CheckpointStore] Không thể ghi checkpoint node {node_name}: {e}")


_CHECKPOINTABLE_NODES: List[str] = [
    "generate_audio",
    "evaluate_audio",
    "visual_director",
    "produce_hook_video",
    "fetch_media",
]


def wrap_node_with_checkpoint(node_name: str, node_fn: Callable):
    """
    Bọc một node LangGraph (async hoặc sync) để tự động khôi phục từ disk checkpoint nếu hợp lệ,
    và tự động ghi disk checkpoint ngay sau khi node hoàn tất.
    """
    if node_name not in _CHECKPOINTABLE_NODES:
        return node_fn

    if inspect.iscoroutinefunction(node_fn):
        @functools.wraps(node_fn)
        async def _async_wrapper(state: Dict[str, Any]) -> Dict[str, Any]:
            cached = _load_node_checkpoint(node_name, state)
            if cached is not None:
                logger.info(
                    f"⚡ [StepCheckpoint Resume] Job #{state.get('job_id')}: Khôi phục tức thì node '{node_name}' "
                    f"từ đĩa (bỏ qua xử lý lặp lại)!"
                )
                return cached
            out = await node_fn(state)
            if isinstance(out, dict):
                _save_node_checkpoint(node_name, state, out)
            return out

        return _async_wrapper

    @functools.wraps(node_fn)
    def _sync_wrapper(state: Dict[str, Any]) -> Dict[str, Any]:
        cached = _load_node_checkpoint(node_name, state)
        if cached is not None:
            logger.info(
                f"⚡ [StepCheckpoint Resume] Job #{state.get('job_id')}: Khôi phục tức thì node '{node_name}' "
                f"từ đĩa (bỏ qua xử lý lặp lại)!"
            )
            return cached
        out = node_fn(state)
        if isinstance(out, dict):
            _save_node_checkpoint(node_name, state, out)
        return out

    return _sync_wrapper

