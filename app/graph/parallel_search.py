"""
app.graph.parallel_search — Bộ máy thực thi song song ĐỘNG N tác vụ độc lập (Dynamic N-Way Parallel Execution)
sử dụng LangGraph `Send` API (`from langgraph.types import Send`) kết hợp `Annotated[List[...], operator.add]`.

Nguyên lý cốt lõi (Map-Reduce Dynamic Fan-Out):
- Không giới hạn cứng ở con số 5 hay bất kỳ số lượng cố định nào.
- Bất cứ khi nào có N tác vụ độc lập cùng loại (gọi N API tạo ảnh AI, tải N bức ảnh/video,
  gọi N nguồn tìm kiếm ảnh/video Pexels/Pixabay/Google/ArtStation/YouTube Shorts...),
  hệ thống tự động phát ra N lệnh `Send("parallel_worker", task_payload)` chạy ĐỒNG THỜI
  trong cùng 1 Superstep và dùng `Annotated[List[Dict[str, Any]], operator.add]` để gom toàn bộ
  N kết quả về đúng thứ tự ban đầu (`task_idx`).
"""

import logging
import operator
import time
from typing import Annotated, Any, Callable, Dict, List, Optional, Set, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# 1. DYNAMIC N-TASK MAP-REDUCE ENGINE (Send API + Annotated[..., operator.add])
# ══════════════════════════════════════════════════════════════════════════════

class DynamicParallelState(TypedDict, total=False):
    """State tổng quát cho đồ thị Map-Reduce chạy song song N tác vụ độc lập."""
    task_group: str
    tasks: List[Dict[str, Any]]
    # Reducer dùng `operator.add` để tự động nối kết quả từ N nhánh `Send` chạy đồng thời
    parallel_results: Annotated[List[Dict[str, Any]], operator.add]
    ordered_results: List[Dict[str, Any]]


def run_langgraph_parallel_tasks(
    tasks: List[Dict[str, Any]],
    worker_fn: Callable[[Dict[str, Any]], Any],
    task_group: str = "parallel_batch",
) -> List[Dict[str, Any]]:
    """
    Thực thi ĐỒNG THỜI N tác vụ độc lập bất kỳ (gọi API tạo ảnh AI, tải file, tìm kiếm đa nguồn...)
    bằng LangGraph `Send` API + `Annotated[List[Dict[str, Any]], operator.add]`.

    - Nếu `tasks` rỗng -> trả về `[]`.
    - Nếu chỉ có 1 task -> thực thi trực tiếp không tốn chi phí khởi tạo graph.
    - Nếu có N >= 2 tasks -> bắn N lệnh `Send("parallel_worker", task)` chạy song song cùng lúc
      và gom kết quả qua `operator.add` theo đúng thứ tự `task_idx`.
    """
    if not tasks:
        return []

    indexed_tasks: List[Dict[str, Any]] = [
        {**t, "task_idx": idx} if "task_idx" not in t else dict(t)
        for idx, t in enumerate(tasks)
    ]

    if len(indexed_tasks) == 1:
        t0_single = time.perf_counter()
        t_item = indexed_tasks[0]
        try:
            out_val = worker_fn(t_item)
        except Exception as e_single:
            logger.debug(f"[LangGraphParallel:{task_group}] Single task error: {e_single}")
            out_val = None
        return [
            {
                **t_item,
                "result": out_val,
                "elapsed_ms": int((time.perf_counter() - t0_single) * 1000),
            }
        ]

    t0 = time.perf_counter()

    def node_prepare(state: DynamicParallelState) -> Dict[str, Any]:
        return {"tasks": indexed_tasks}

    def route_dynamic_sends(state: DynamicParallelState) -> List[Send]:
        return [Send("parallel_worker", t) for t in state.get("tasks", [])]

    def parallel_worker(state: Dict[str, Any]) -> Dict[str, Any]:
        w_t0 = time.perf_counter()
        try:
            from app.agents.curation.scene_context_analyzer_mixin import is_scene_cancelled_ctx
            if is_scene_cancelled_ctx(min_remaining_sec=1.2):
                res_val = None
            else:
                res_val = worker_fn(state)
        except Exception as e_w:
            logger.debug(f"[LangGraphParallel:{task_group}] Worker #{state.get('task_idx')} error: {e_w}")
            res_val = None
        elapsed_ms = int((time.perf_counter() - w_t0) * 1000)
        return {
            "parallel_results": [
                {
                    **state,
                    "result": res_val,
                    "elapsed_ms": elapsed_ms,
                }
            ]
        }

    def node_reduce(state: DynamicParallelState) -> Dict[str, Any]:
        raw_list = list(state.get("parallel_results") or [])
        raw_list.sort(key=lambda x: int(x.get("task_idx", 999)))
        total_ms = int((time.perf_counter() - t0) * 1000)
        sum_ms = sum(int(x.get("elapsed_ms") or 0) for x in raw_list)
        logger.info(
            f"⚡ [LangGraph Send+operator.add | {task_group}] Chạy đồng thời {len(raw_list)} tác vụ "
            f"trong {total_ms}ms (nếu chạy tuần tự tốn ~{sum_ms}ms)"
        )
        return {"ordered_results": raw_list}

    builder = StateGraph(DynamicParallelState)
    builder.add_node("prepare", node_prepare)
    builder.add_node("parallel_worker", parallel_worker)
    builder.add_node("reduce", node_reduce)

    builder.add_edge(START, "prepare")
    builder.add_conditional_edges("prepare", route_dynamic_sends, ["parallel_worker"])
    builder.add_edge("parallel_worker", "reduce")
    builder.add_edge("reduce", END)

    graph = builder.compile()
    final_state = graph.invoke(
        {
            "task_group": task_group,
            "tasks": indexed_tasks,
            "parallel_results": [],
        },
        config={"callbacks": []},
    )
    return list(final_state.get("ordered_results") or [])


# ══════════════════════════════════════════════════════════════════════════════
# 2. PARALLEL MULTI-SOURCE WEB IMAGE SEARCH (Dynamic N Branches)
# ══════════════════════════════════════════════════════════════════════════════

def build_image_search_branches(
    query: str,
    subject: str = "",
    per_source: int = 8,
    creature_mode: bool = False,
    use_pure_google_search: bool = True,
    extra_queries: Optional[List[str]] = None,
    preferred_engines: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Tự động tạo danh sách N nhánh tìm kiếm ảnh độc lập dựa theo số lượng truy vấn mở rộng (`extra_queries`)
    và chế độ tìm kiếm thực tế (không giới hạn cứng số nhánh).
    """
    clean_q = (query or "").strip()
    clean_subj = (subject or clean_q).strip()
    extras = [
        q.strip()
        for q in (extra_queries or [])
        if isinstance(q, str) and q.strip() and q.strip().lower() != clean_q.lower()
    ]
    if not extras:
        extras = [f"{clean_subj} cinematic HD", f"{clean_subj} close up dramatic"]

    branches: List[Dict[str, Any]] = []

    if creature_mode:
        # 1 nhánh Google Headless + các nhánh HTTP TLS siêu tốc (ArtStation 8K, Bing HD đa góc nhìn, DDGS HD)
        # Tránh gọi nhiều tiến trình Chrome Headless cùng lúc gây nghẽn CPU & loại bỏ Wallhaven (tránh lẫn ảnh anime).
        branches.append({
            "branch_name": "google_primary_8k",
            "engine_or_source": "google",
            "branch_query": clean_q,
            "per_source": max(per_source, 14),
        })
        branches.append({
            "branch_name": "artstation_concept_8k",
            "engine_or_source": "artstation",
            "branch_query": extras[0],
            "per_source": max(per_source, 12),
        })
        branches.append({
            "branch_name": "bing_hd_primary",
            "engine_or_source": "bing",
            "branch_query": clean_q,
            "per_source": max(per_source, 14),
        })
        for idx_x, xq in enumerate(extras[:2]):
            branches.append({
                "branch_name": f"bing_hd_angle_{idx_x + 2}",
                "engine_or_source": "bing",
                "branch_query": xq,
                "per_source": max(per_source, 12),
            })
        branches.append({
            "branch_name": "ddgs_hd_creature",
            "engine_or_source": "ddgs",
            "branch_query": extras[0],
            "per_source": max(per_source, 10),
        })
    else:
        branches.append({
            "branch_name": "google_top_primary",
            "engine_or_source": "google",
            "branch_query": clean_q,
            "per_source": max(per_source, 14),
        })
        branches.append({
            "branch_name": "bing_hd_primary",
            "engine_or_source": "bing",
            "branch_query": clean_q,
            "per_source": max(per_source, 14),
        })
        for idx_x, xq in enumerate(extras[:2]):
            branches.append({
                "branch_name": f"bing_hd_angle_{idx_x + 2}",
                "engine_or_source": "bing",
                "branch_query": xq,
                "per_source": max(per_source, 12),
            })
        branches.append({
            "branch_name": "ddgs_open_web",
            "engine_or_source": "ddgs",
            "branch_query": clean_q,
            "per_source": max(per_source, 10),
        })
        if not use_pure_google_search:
            branches.extend([
                {
                    "branch_name": "wikipedia_commons",
                    "engine_or_source": "wikipedia_commons",
                    "branch_query": clean_subj,
                    "per_source": max(per_source, 8),
                },
                {
                    "branch_name": "openverse_cc",
                    "engine_or_source": "openverse",
                    "branch_query": clean_subj,
                    "per_source": max(per_source, 6),
                },
            ])

    for i, b in enumerate(branches):
        b["task_idx"] = i
        b["branch_idx"] = i
    return branches


def run_langgraph_5way_image_search(
    fetcher: Any,
    query: str,
    per_source: int = 8,
    subject: str = "",
    extra_queries: Optional[List[str]] = None,
    creature_mode: bool = False,
    use_pure_google_search: bool = True,
    preferred_engines: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Thực thi tìm kiếm ảnh đa nguồn ĐỒNG THỜI trên toàn bộ N nhánh bằng `run_langgraph_parallel_tasks`
    (LangGraph `Send` API + `Annotated[List[...], operator.add]`).
    """
    branches = build_image_search_branches(
        query=query,
        subject=subject,
        per_source=per_source,
        creature_mode=creature_mode,
        use_pure_google_search=use_pure_google_search,
        extra_queries=extra_queries,
        preferred_engines=preferred_engines,
    )

    def _exec_image_branch(task: Dict[str, Any]) -> List[Dict[str, Any]]:
        eng = task.get("engine_or_source", "google")
        b_q = task.get("branch_query", query)
        b_ps = int(task.get("per_source") or per_source)
        if eng in ("google", "bing", "artstation", "wallhaven", "ddgs", "serper"):
            return fetcher._run_single_open_engine(eng, b_q, b_ps) or []
        if eng == "wikipedia_commons":
            w1 = fetcher.search_wikipedia_images(b_q, per_page=max(4, b_ps // 2)) or []
            w2 = fetcher.search_wikimedia_commons(b_q, per_page=max(4, b_ps // 2)) or []
            return w1 + w2
        if eng == "openverse":
            return fetcher.search_openverse(b_q, per_page=b_ps) or []
        return []

    completed = run_langgraph_parallel_tasks(
        tasks=branches,
        worker_fn=_exec_image_branch,
        task_group=f"WebImageSearch({len(branches)} branches)",
    )

    merged: List[Dict[str, Any]] = []
    seen_urls: Set[str] = set()
    for b_res in completed:
        for item in (b_res.get("result") or []):
            u = item.get("url")
            if u and u not in seen_urls:
                seen_urls.add(u)
                merged.append(item)

    subj_clean = (subject or "").strip()
    if len(merged) < 3 and subj_clean and subj_clean.lower() != query.lower():
        try:
            for item in (fetcher._run_single_open_engine("google", subj_clean, max(per_source, 10)) or []):
                u = item.get("url")
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    merged.append(item)
        except Exception:
            pass

    try:
        from app.services.media.image_quality import prefilter_candidates
        merged = prefilter_candidates(
            merged,
            subject=subj_clean or query,
            limit=max(18, per_source * 3),
            creature_mode=creature_mode,
            max_per_source=16 if (creature_mode or use_pure_google_search) else 8,
        )
    except Exception as e_pf:
        logger.debug(f"[LangGraphParallelImage] prefilter error: {e_pf}")

    return merged


# ══════════════════════════════════════════════════════════════════════════════
# 3. PARALLEL STOCK VIDEO & PHOTO API SEARCH (Dynamic N Queries/Sources)
# ══════════════════════════════════════════════════════════════════════════════

def run_langgraph_5way_stock_video_search(
    stock_fetcher: Any,
    candidate_queries: List[str],
    include_pixabay: bool = True,
    include_nasa: bool = False,
    nasa_query: str = "",
) -> List[Dict[str, Any]]:
    """
    Bắn ĐỒNG THỜI toàn bộ các nhánh tìm kiếm Video B-Roll (Pexels + Pixabay + NASA)
    qua LangGraph `Send` API + `operator.add` (tự động co giãn theo số câu truy vấn thực tế).
    """
    if not candidate_queries:
        return []

    tasks: List[Dict[str, Any]] = []
    if include_nasa:
        tasks.append({
            "branch_name": "nasa_v_1",
            "source": "nasa",
            "query": nasa_query or candidate_queries[0],
            "per_source": 3,
        })

    for idx, pq in enumerate(candidate_queries[:4]):
        tasks.append({
            "branch_name": f"pexels_v_{idx + 1}",
            "source": "pexels",
            "query": pq,
            "per_source": 6,
        })

    if include_pixabay and getattr(stock_fetcher, "_pixabay_key", ""):
        for p_idx, px_q in enumerate(candidate_queries[:3]):
            tasks.append({
                "branch_name": f"pixabay_v_{p_idx + 1}",
                "source": "pixabay",
                "query": px_q,
                "per_source": 5,
            })

    def _exec_video_branch(t: Dict[str, Any]) -> List[Dict[str, Any]]:
        src = t.get("source", "pexels")
        q_str = t.get("query", "")
        ps = int(t.get("per_source") or 5)
        if src == "pexels":
            return stock_fetcher.search_videos(q_str, per_page=ps, orientation="portrait") or []
        if src == "pixabay":
            return stock_fetcher.search_pixabay_videos(q_str, per_page=ps) or []
        if src == "nasa":
            return stock_fetcher.search_nasa_media(q_str, media_type="video", per_page=ps) or []
        return []

    completed = run_langgraph_parallel_tasks(
        tasks=tasks,
        worker_fn=_exec_video_branch,
        task_group=f"StockVideoSearch({len(tasks)} branches)",
    )
    return [
        {
            "branch_idx": c.get("task_idx", 0),
            "branch_name": c.get("branch_name", ""),
            "source": c.get("source", "pexels"),
            "query": c.get("query", ""),
            "items": c.get("result") or [],
            "elapsed_ms": c.get("elapsed_ms", 0),
        }
        for c in completed
    ]


# ══════════════════════════════════════════════════════════════════════════════
# 4. PARALLEL YOUTUBE SHORTS FLAT SEARCH (Dynamic N Queries)
# ══════════════════════════════════════════════════════════════════════════════

def run_langgraph_parallel_youtube_flat_search(
    search_phrases: List[str],
    fetch_single_phrase_fn: Callable[[str], List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """
    Quét ĐỒNG THỜI toàn bộ danh sách câu truy vấn YouTube Shorts (`ytsearch25:... #shorts`)
    bằng LangGraph `Send` API + `Annotated[List[...], operator.add]`.
    """
    phrases = [p.strip() for p in (search_phrases or []) if isinstance(p, str) and p.strip()]
    if not phrases:
        return []

    tasks = [
        {"branch_name": f"yt_short_q{idx + 1}", "query": sp}
        for idx, sp in enumerate(phrases)
    ]

    completed = run_langgraph_parallel_tasks(
        tasks=tasks,
        worker_fn=lambda t: fetch_single_phrase_fn(t.get("query", "")) or [],
        task_group=f"YouTubeShortsSearch({len(tasks)} queries)",
    )
    return [
        {
            "branch_idx": c.get("task_idx", 0),
            "branch_name": c.get("branch_name", "yt"),
            "query": c.get("query", ""),
            "items": c.get("result") or [],
            "elapsed_ms": c.get("elapsed_ms", 0),
        }
        for c in completed
    ]
