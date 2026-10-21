"""
app.utils.langsmith_tracer — Bộ công cụ Quan sát, Truy vết & Chẩn đoán hiệu năng LangSmith (Cloud + Local Dual-Mode).

Tính năng chính:
1. Tự động đồng bộ cấu hình LangSmith (`LANGSMITH_API_KEY` / `LANGCHAIN_API_KEY`, `LANGCHAIN_TRACING_V2`, `LANGCHAIN_PROJECT`) từ file `.env`.
2. Cung cấp `build_langgraph_run_config(...)` gắn tên Run, Tags và Metadata rõ ràng cho từng VideoJob trên LangSmith UI.
3. Cung cấp decorator `@trace_custom_span(...)` để ghi nhận các lệnh gọi REST API tùy chỉnh (Gemini Vision, Pexels, Pixabay, FFmpeg) dưới dạng Span con trên cả LangSmith Cloud lẫn bộ ghi Waterfall cục bộ (`storage/traces/`).
4. Cung cấp công cụ CLI chẩn đoán tức thì cho cả Người dùng và AI Agent:
   - `python -m app.utils.langsmith_tracer --status` : Kiểm tra kết nối LangSmith & trạng thái API Key.
   - `python -m app.utils.langsmith_tracer --latest` : Hiển thị biểu đồ Waterfall thời gian chạy từng Node & điểm nghẽn (Bottleneck) của Job mới nhất.
   - `python -m app.utils.langsmith_tracer --job 123`: Phân tích chi tiết Job #123 từ LangSmith Cloud hoặc Local Trace.
"""
import argparse
import functools
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from dotenv import load_dotenv

from config.settings import settings

logger = logging.getLogger(__name__)

TRACES_DIR: Path = settings.BASE_DIR / "storage" / "traces"
TRACES_DIR.mkdir(parents=True, exist_ok=True)

_trace_lock = threading.Lock()
_active_job_traces: Dict[int, Dict[str, Any]] = {}
_current_job_id_ctx: Optional[int] = None


def configure_langsmith_env(reload_dotenv: bool = True) -> bool:
    """
    Đồng bộ biến môi trường LangSmith từ `.env`.
    Trả về True nếu đã có API Key hợp lệ và kích hoạt thành công LangSmith Cloud Tracing.
    Nếu chưa có API Key, tự động tắt cờ `LANGCHAIN_TRACING_V2` để tránh cảnh báo ngầm nhưng vẫn giữ Local Waterfall Trace.
    """
    if reload_dotenv:
        try:
            load_dotenv(settings.BASE_DIR / ".env", override=True)
        except Exception:
            pass

    raw_key = (
        os.getenv("LANGSMITH_API_KEY", "").strip()
        or os.getenv("LANGCHAIN_API_KEY", "").strip()
        or getattr(settings, "LANGSMITH_API_KEY", "").strip()
    )
    project_name = (
        os.getenv("LANGSMITH_PROJECT", "").strip()
        or os.getenv("LANGCHAIN_PROJECT", "").strip()
        or getattr(settings, "LANGSMITH_PROJECT", "AI-YouTube-Studio").strip()
        or "AI-YouTube-Studio"
    )
    endpoint = (
        os.getenv("LANGSMITH_ENDPOINT", "").strip()
        or os.getenv("LANGCHAIN_ENDPOINT", "").strip()
        or "https://api.smith.langchain.com"
    )

    if raw_key and len(raw_key) > 10 and not raw_key.startswith("your_"):
        os.environ["LANGSMITH_API_KEY"] = raw_key
        os.environ["LANGCHAIN_API_KEY"] = raw_key
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGSMITH_PROJECT"] = project_name
        os.environ["LANGCHAIN_PROJECT"] = project_name
        os.environ["LANGSMITH_ENDPOINT"] = endpoint
        os.environ["LANGCHAIN_ENDPOINT"] = endpoint
        return True
    else:
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        return False


def is_langsmith_enabled() -> bool:
    """Kiểm tra nhanh xem LangSmith Cloud Tracing có đang bật với API key hợp lệ hay không."""
    return configure_langsmith_env(reload_dotenv=False)


def build_langgraph_run_config(
    job_id: int,
    channel_id: str = "default",
    topic: str = "",
    niche: str = "general",
    mode: str = "full_pipeline",
) -> Dict[str, Any]:
    """
    Tạo cấu hình `RunnableConfig` truyền vào `video_graph.astream(..., config=...)`.
    Giúp hiển thị tên Job, kênh, ngách và chủ đề trực quan trên giao diện LangSmith (`smith.langchain.com`).
    """
    cloud_enabled = configure_langsmith_env(reload_dotenv=True)
    short_topic = (topic or "Untitled").strip()[:48]
    run_name = f"VideoJob #{job_id} [{channel_id}] - {short_topic}"
    return {
        "run_name": run_name,
        "tags": [
            f"job:{job_id}",
            f"channel:{channel_id}",
            f"niche:{niche or 'general'}",
            f"mode:{mode}",
            "langsmith:cloud" if cloud_enabled else "langsmith:local_only",
        ],
        "metadata": {
            "job_id": job_id,
            "channel_id": channel_id,
            "niche": niche or "general",
            "topic": topic,
            "mode": mode,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# BỘ GHI WATERFALL CỤC BỘ & SPAN TRACER (Chạy song song với LangSmith Cloud)
# ══════════════════════════════════════════════════════════════════════════════

def start_job_trace(
    job_id: int,
    channel_id: str = "default",
    topic: str = "",
    niche: str = "general",
    mode: str = "full_pipeline",
) -> None:
    """Khởi tạo phiên đo lường Waterfall cho một VideoJob."""
    global _current_job_id_ctx
    cloud_enabled = configure_langsmith_env(reload_dotenv=True)
    with _trace_lock:
        _current_job_id_ctx = job_id
        _active_job_traces[job_id] = {
            "job_id": job_id,
            "channel_id": channel_id,
            "topic": topic,
            "niche": niche,
            "mode": mode,
            "langsmith_cloud_enabled": cloud_enabled,
            "langsmith_project": os.environ.get("LANGCHAIN_PROJECT", "AI-YouTube-Studio"),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "start_mono": time.monotonic(),
            "nodes": [],
            "custom_spans": [],
            "status": "RUNNING",
        }


def record_node_span(
    job_id: int,
    node_name: str,
    duration_sec: float,
    status: str = "OK",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Ghi nhận thời gian thực thi của một Node trong LangGraph."""
    with _trace_lock:
        tr = _active_job_traces.get(job_id)
        if not tr:
            return
        tr["nodes"].append({
            "node": node_name,
            "duration_sec": round(duration_sec, 3),
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": details or {},
        })
        _flush_job_trace_locked(job_id)


def record_custom_span(
    name: str,
    run_type: str,
    duration_sec: float,
    status: str = "OK",
    metadata: Optional[Dict[str, Any]] = None,
    job_id: Optional[int] = None,
) -> None:
    """Ghi nhận thời gian của một lệnh gọi con (Gemini API, Pexels API, FFmpeg...)."""
    with _trace_lock:
        jid = job_id or _current_job_id_ctx
        if not jid or jid not in _active_job_traces:
            return
        _active_job_traces[jid]["custom_spans"].append({
            "name": name,
            "run_type": run_type,
            "duration_sec": round(duration_sec, 3),
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        })


def finish_job_trace(job_id: int, status: str = "COMPLETED", error: Optional[str] = None) -> Optional[Path]:
    """Hoàn tất phiên đo lường Waterfall của Job và lưu ra `storage/traces/job_{job_id}_trace.json`."""
    with _trace_lock:
        tr = _active_job_traces.get(job_id)
        if not tr:
            return None
        total_sec = time.monotonic() - tr.get("start_mono", time.monotonic())
        tr["total_duration_sec"] = round(total_sec, 3)
        tr["finished_at"] = datetime.now(timezone.utc).isoformat()
        tr["status"] = status
        if error:
            tr["error"] = error
        out_path = _flush_job_trace_locked(job_id)
        return out_path


def _flush_job_trace_locked(job_id: int) -> Optional[Path]:
    tr = _active_job_traces.get(job_id)
    if not tr:
        return None
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    payload = {k: v for k, v in tr.items() if k != "start_mono"}
    if "total_duration_sec" not in payload:
        payload["total_duration_sec"] = round(time.monotonic() - tr.get("start_mono", time.monotonic()), 3)
    out_file = TRACES_DIR / f"job_{job_id}_trace.json"
    try:
        out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return out_file
    except Exception as e:
        logger.debug(f"[LangSmithTracer] Không thể ghi file trace cục bộ: {e}")
        return None


def trace_custom_span(name: str, run_type: str = "llm"):
    """
    Decorator kép:
    - Nếu `LANGSMITH_API_KEY` được cấu hình, tự động bọc hàm bằng `langsmith.traceable(name=name, run_type=run_type)`
      để hiển thị thành Span con trên cây Waterfall của LangSmith Cloud.
    - Đồng thời luôn đo thời gian thực thi chính xác vào `storage/traces/job_<id>_trace.json`.
    """
    def decorator(func: Callable):
        # Bọc bằng langsmith.traceable nếu thư viện có sẵn
        try:
            from langsmith import traceable
            ls_wrapped = traceable(name=name, run_type=run_type)(func)
        except Exception:
            ls_wrapped = func

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            t0 = time.monotonic()
            status = "OK"
            try:
                target_fn = ls_wrapped if is_langsmith_enabled() else func
                return target_fn(*args, **kwargs)
            except Exception as exc:
                status = f"ERR:{type(exc).__name__}"
                raise
            finally:
                dur = time.monotonic() - t0
                record_custom_span(name=name, run_type=run_type, duration_sec=dur, status=status)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            t0 = time.monotonic()
            status = "OK"
            try:
                target_fn = ls_wrapped if is_langsmith_enabled() else func
                return await target_fn(*args, **kwargs)
            except Exception as exc:
                status = f"ERR:{type(exc).__name__}"
                raise
            finally:
                dur = time.monotonic() - t0
                record_custom_span(name=name, run_type=run_type, duration_sec=dur, status=status)

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


# ══════════════════════════════════════════════════════════════════════════════
# CÔNG CỤ CHẨN ĐOÁN CLI CHO AI AGENT & NGƯỜI DÙNG
# ══════════════════════════════════════════════════════════════════════════════

def _render_bar(pct: float, width: int = 24) -> str:
    filled = int(round(( max(0.0, min(100.0, pct)) / 100.0 ) * width))
    return "█" * filled + "░" * (width - filled)


def _ensure_utf8_stdout() -> None:
    import sys
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def print_trace_report(trace_data: Dict[str, Any]) -> None:
    """In bảng phân tích Waterfall trực quan ra màn hình console."""
    _ensure_utf8_stdout()
    job_id = trace_data.get("job_id", "?")
    channel = trace_data.get("channel_id", "default")
    topic = trace_data.get("topic", "")
    status = trace_data.get("status", "UNKNOWN")
    total_sec = float(trace_data.get("total_duration_sec") or 0.0)
    nodes = trace_data.get("nodes", [])
    spans = trace_data.get("custom_spans", [])
    cloud = trace_data.get("langsmith_cloud_enabled", False)

    if total_sec <= 0 and nodes:
        total_sec = sum(float(n.get("duration_sec", 0.0)) for n in nodes)

    print("=" * 84)
    print(f"📊 BÁO CÁO TRUY VẾT LANGGRAPH / LANGSMITH — VIDEO JOB #{job_id} [{channel}]")
    print("=" * 84)
    print(f"• Chủ đề       : {topic}")
    print(f"• Trạng thái   : {status} | Tổng thời gian: {total_sec:.2f}s ({total_sec/60:.1f} phút)")
    print(f"• LangSmith UI : {'🟢 ĐÃ ĐỒNG BỘ LÊN CLOUD (' + str(trace_data.get('langsmith_project')) + ')' if cloud else '🟡 CHẾ ĐỘ CỤC BỘ (Chưa điền LANGSMITH_API_KEY trong .env)'}")
    print("-" * 84)

    if nodes:
        print(f"{'NODE LANGGRAPH':<28} | {'THỜI GIAN':>10} | {'TỈ TRỌNG':>8} | BIỂU ĐỒ WATERFALL")
        print("-" * 84)
        slowest_node = max(nodes, key=lambda x: float(x.get("duration_sec", 0.0)))
        for n in nodes:
            n_name = str(n.get("node", ""))
            dur = float(n.get("duration_sec", 0.0))
            pct = (dur / total_sec * 100.0) if total_sec > 0 else 0.0
            bar = _render_bar(pct)
            flag = " 🔥 [ĐIỂM NGHẼN]" if n is slowest_node and dur > 10.0 else ""
            print(f"{n_name:<28} | {dur:>8.2f}s | {pct:>6.1f}% | {bar}{flag}")
        print("-" * 84)

    if spans:
        # Gom nhóm các custom spans theo tên hàm
        grouped: Dict[str, Dict[str, Any]] = {}
        for s in spans:
            s_name = s.get("name", "unknown")
            dur = float(s.get("duration_sec", 0.0))
            g = grouped.setdefault(s_name, {"count": 0, "total_sec": 0.0, "max_sec": 0.0, "errors": 0})
            g["count"] += 1
            g["total_sec"] += dur
            g["max_sec"] = max(g["max_sec"], dur)
            if str(s.get("status", "OK")) != "OK":
                g["errors"] += 1

        print(f"{'LỆNH GỌI CON (SUB-SPANS)':<28} | {'SỐ LẦN':>6} | {'TỔNG TG':>9} | {'MAX/LẦN':>9} | {'LỖI':>4}")
        print("-" * 84)
        for s_name, stats in sorted(grouped.items(), key=lambda item: -item[1]["total_sec"]):
            print(
                f"{s_name[:28]:<28} | {stats['count']:>6} | {stats['total_sec']:>7.2f}s | "
                f"{stats['max_sec']:>7.2f}s | {stats['errors']:>4}"
            )
        print("=" * 84)


def check_langsmith_status() -> int:
    """Kiểm tra trạng thái cài đặt LangSmith SDK, API Key và kết nối tới máy chủ LangSmith."""
    _ensure_utf8_stdout()
    cloud_ready = configure_langsmith_env(reload_dotenv=True)
    print("=" * 78)
    print("🔍 KIỂM TRA TRẠNG THÁI HỆ THỐNG QUAN SÁT LANGSMITH (OBSERVABILITY STATUS)")
    print("=" * 78)
    try:
        import langsmith
        print(f"✅ Thư viện Python `langsmith` : Đã cài đặt (v{getattr(langsmith, '__version__', 'installed')})")
    except ImportError:
        print("❌ Thư viện Python `langsmith` : Chưa cài đặt")
        return 1

    print(f"📁 Thư mục Trace cục bộ        : {TRACES_DIR}")
    local_files = sorted(TRACES_DIR.glob("job_*_trace.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    print(f"📊 Số bản ghi Trace cục bộ     : {len(local_files)} file")

    if not cloud_ready:
        print("🟡 LangSmith Cloud API Key     : CHƯA CẤU HÌNH trong file `.env`")
        print("   -> Hiện tại hệ thống đang tự động ghi Waterfall Trace cục bộ tại `storage/traces/`.")
        print("   -> Ngay khi bạn thêm `LANGSMITH_API_KEY=lsv2_pt_...` vào file `.env`,")
        print("      mọi lần chạy VideoJob sẽ tự động đẩy lên https://smith.langchain.com!")
        print("=" * 78)
        return 0

    project_name = os.environ.get("LANGCHAIN_PROJECT", "AI-YouTube-Studio")
    print(f"🟢 LangSmith Cloud API Key     : ĐÃ KÍCH HOẠT (Project: {project_name})")
    try:
        from langsmith import Client
        client = Client()
        runs = list(client.list_runs(project_name=project_name, is_root=True, limit=5))
        print(f"🌐 Kết nối LangSmith Cloud     : THÀNH CÔNG (Tìm thấy {len(runs)} runs gần nhất)")
        for r in runs:
            dur = (
                (r.end_time - r.start_time).total_seconds()
                if (r.end_time and r.start_time)
                else 0.0
            )
            print(f"   • [{r.status}] {r.name} | Thời lượng: {dur:.1f}s | ID: {r.id}")
    except Exception as exc:
        print(f"⚠️ Kết nối LangSmith Cloud     : Chưa có project '{project_name}' hoặc lỗi mạng ({exc})")
        print("   (Project sẽ tự động được tạo ngay trong lần chạy VideoJob đầu tiên!)")
    print("=" * 78)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Công cụ chẩn đoán LangSmith & LangGraph Waterfall Tracer")
    parser.add_argument("--status", action="store_true", help="Kiểm tra trạng thái kết nối LangSmith & API Key")
    parser.add_argument("--latest", action="store_true", help="Xem biểu đồ Waterfall của VideoJob mới nhất")
    parser.add_argument("--job", type=int, default=None, help="Xem biểu đồ Waterfall của một Job ID cụ thể")
    parser.add_argument("--cloud", action="store_true", help="Truy vấn trực tiếp 5 Runs gần nhất trên LangSmith Cloud")
    args = parser.parse_args()

    if args.status:
        check_langsmith_status()
        return

    if args.cloud:
        if not configure_langsmith_env(reload_dotenv=True):
            print("⚠️ Chưa có `LANGSMITH_API_KEY` trong `.env`. Hãy cấu hình key trước khi dùng `--cloud`.")
            return
        from langsmith import Client
        project_name = os.environ.get("LANGCHAIN_PROJECT", "AI-YouTube-Studio")
        client = Client()
        runs = list(client.list_runs(project_name=project_name, is_root=True, limit=5))
        if not runs:
            print(f"Chưa có Run nào trên project '{project_name}'.")
            return
        for r in runs:
            dur = (r.end_time - r.start_time).total_seconds() if (r.end_time and r.start_time) else 0.0
            print(f"\n🌐 [LangSmith Cloud Run] {r.name} | Status: {r.status} | Tổng: {dur:.2f}s")
            children = list(client.list_runs(project_name=project_name, parent_run_id=r.id))
            for ch in sorted(children, key=lambda x: x.start_time or datetime.min.replace(tzinfo=timezone.utc)):
                cdur = (ch.end_time - ch.start_time).total_seconds() if (ch.end_time and ch.start_time) else 0.0
                pct = (cdur / dur * 100.0) if dur > 0 else 0.0
                print(f"   ├─ {ch.name:<28} ({ch.run_type:<5}) : {cdur:>7.2f}s ({pct:>5.1f}%) [{ch.status}]")
        return

    target_file: Optional[Path] = None
    if args.job is not None:
        candidate = TRACES_DIR / f"job_{args.job}_trace.json"
        if candidate.exists():
            target_file = candidate
        else:
            print(f"❌ Không tìm thấy bản ghi trace cục bộ cho Job #{args.job} tại {candidate}")
            return
    else:
        files = sorted(TRACES_DIR.glob("job_*_trace.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            target_file = files[0]

    if target_file and target_file.exists():
        data = json.loads(target_file.read_text(encoding="utf-8"))
        print_trace_report(data)
    else:
        check_langsmith_status()


if __name__ == "__main__":
    main()
