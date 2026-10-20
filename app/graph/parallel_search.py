"""
app.graph.parallel_search — Bộ máy tìm kiếm song song 5 luồng (5-Way Parallel Execution)
sử dụng LangGraph `Send` API (`from langgraph.types import Send`) kết hợp `Annotated[List[...], operator.add]`.

Kiến trúc Map-Reduce chuẩn LangGraph:
1. Map (Fan-Out via `Send` API):
   - Từ 1 yêu cầu tìm kiếm của phân cảnh, node điều phối (`dispatch_5_branches`) phát ra đồng thời
     5 đối tượng `Send("search_worker_node", branch_payload)` chạy song song trong cùng 1 Superstep.
2. Execute (Parallel Worker Nodes):
   - Mỗi nhánh thực thi độc lập trên 1 nguồn / góc truy vấn (Google Top Images, ArtStation 8K, Bing HD,
     Wallhaven 4K / DDGS, Wikimedia / Pexels / Pixabay / YouTube Shorts).
   - Mỗi nhánh trả về `{"aggregated_5_results": [branch_result_dict]}`.
3. Reduce (`Annotated[List[Dict[str, Any]], operator.add]` + `reduce_5_results_node`):
   - LangGraph tự động gom (concat) kết quả của cả 5 nhánh song song vào `state["aggregated_5_results"]`
     bằng `operator.add` mà không bị ghi đè (overwrite) state.
   - Node tổng hợp (`reduce_5_results_node`) sắp xếp theo thứ tự ưu tiên nhánh, khử trùng lặp URL
     và chấm điểm chất lượng ứng viên.
"""

import logging
import operator
import time
from typing import Annotated, Any, Callable, Dict, List, Optional, Set, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# 1. STATE DEFINITIONS VỚI REDUCER `Annotated[List[...], operator.add]`
# ══════════════════════════════════════════════════════════════════════════════

class ParallelSearchState(TypedDict, total=False):
    """State tổng của đồ thị con Map-Reduce tìm kiếm 5 luồng song song."""
    query: str
    subject: str
    per_source: int
    creature_mode: bool
    use_pure_google_search: bool
    extra_queries: List[str]
    preferred_engines: List[str]
    branches: List[Dict[str, Any]]
    # Trái tim của Map-Reduce: `operator.add` tự động nối kết quả từ 5 nhánh `Send` song song
    aggregated_5_results: Annotated[List[Dict[str, Any]], operator.add]
    merged_items: List[Dict[str, Any]]


class SearchBranchPayload(TypedDict, total=False):
    """State đầu vào riêng biệt cho từng Worker được gọi qua `Send(node, payload)`."""
    branch_idx: int
    branch_name: str
    engine_or_source: str
    branch_query: str
    per_source: int
    subject: str
    creature_mode: bool


# ══════════════════════════════════════════════════════════════════════════════
# 2. LANGGRAPH 5-WAY PARALLEL WEB IMAGE SEARCH (Send API + operator.add)
# ══════════════════════════════════════════════════════════════════════════════

def build_5_image_search_branches(
    query: str,
    subject: str = "",
    per_source: int = 8,
    creature_mode: bool = False,
    use_pure_google_search: bool = True,
    extra_queries: Optional[List[str]] = None,
    preferred_engines: Optional[List[str]] = None,
) -> List[SearchBranchPayload]:
    """
    Thiết lập 5 nhánh tìm kiếm song song (5-Way Fan-Out) để truyền qua LangGraph `Send` API:
    - Chế độ Quái vật / Megalophobia (`creature_mode=True`):
      1. Google Images Top Search (truy vấn chính)
      2. ArtStation 8K Concept Art (truy vấn đại cảnh / concept art)
      3. Bing HD Images (truy vấn đối chiếu tỷ lệ scale anchor)
      4. Wallhaven 4K Wallpaper (truy vấn chủ thể quái vật)
      5. Google Images VFX / Movie Still (góc máy phụ thứ 2)
    - Chế độ Tiêu chuẩn / Documentary / Đời thực:
      1. Google Images Top Search (truy vấn chính)
      2. Google Images Angle #2 (góc máy phụ / hành động cụ thể)
      3. Bing HD Images (truy vấn chính)
      4. DuckDuckGo HD Images (`ddgs`)
      5. Wikimedia Commons / Wikipedia (hoặc Google Angle #3)
    """
    clean_q = (query or "").strip()
    clean_subj = (subject or clean_q).strip()
    extras = [q.strip() for q in (extra_queries or []) if isinstance(q, str) and q.strip() and q.strip().lower() != clean_q.lower()]

    q_alt_1 = extras[0] if len(extras) >= 1 else f"{clean_subj} cinematic HD"
    q_alt_2 = extras[1] if len(extras) >= 2 else f"{clean_subj} close up dramatic"

    if creature_mode:
        branches: List[SearchBranchPayload] = [
            {
                "branch_idx": 0,
                "branch_name": "1_google_primary_8k",
                "engine_or_source": "google",
                "branch_query": clean_q,
                "per_source": max(per_source, 12),
                "subject": clean_subj,
                "creature_mode": True,
            },
            {
                "branch_idx": 1,
                "branch_name": "2_artstation_concept_8k",
                "engine_or_source": "artstation",
                "branch_query": q_alt_1,
                "per_source": max(per_source, 10),
                "subject": clean_subj,
                "creature_mode": True,
            },
            {
                "branch_idx": 2,
                "branch_name": "3_bing_hd_scale",
                "engine_or_source": "bing",
                "branch_query": clean_q,
                "per_source": max(per_source, 10),
                "subject": clean_subj,
                "creature_mode": True,
            },
            {
                "branch_idx": 3,
                "branch_name": "4_wallhaven_4k_vfx",
                "engine_or_source": "wallhaven",
                "branch_query": clean_subj,
                "per_source": max(per_source, 8),
                "subject": clean_subj,
                "creature_mode": True,
            },
            {
                "branch_idx": 4,
                "branch_name": "5_google_cgi_angle2",
                "engine_or_source": "google",
                "branch_query": q_alt_2,
                "per_source": max(per_source, 10),
                "subject": clean_subj,
                "creature_mode": True,
            },
        ]
    else:
        fifth_source = "google" if use_pure_google_search else "wikipedia_commons"
        branches = [
            {
                "branch_idx": 0,
                "branch_name": "1_google_top_primary",
                "engine_or_source": "google",
                "branch_query": clean_q,
                "per_source": max(per_source, 12),
                "subject": clean_subj,
                "creature_mode": False,
            },
            {
                "branch_idx": 1,
                "branch_name": "2_google_angle_secondary",
                "engine_or_source": "google",
                "branch_query": q_alt_1,
                "per_source": max(per_source, 10),
                "subject": clean_subj,
                "creature_mode": False,
            },
            {
                "branch_idx": 2,
                "branch_name": "3_bing_hd_visual",
                "engine_or_source": "bing",
                "branch_query": clean_q,
                "per_source": max(per_source, 10),
                "subject": clean_subj,
                "creature_mode": False,
            },
            {
                "branch_idx": 3,
                "branch_name": "4_ddgs_open_web",
                "engine_or_source": "ddgs",
                "branch_query": clean_q,
                "per_source": max(per_source, 8),
                "subject": clean_subj,
                "creature_mode": False,
            },
            {
                "branch_idx": 4,
                "branch_name": f"5_{fifth_source}",
                "engine_or_source": fifth_source,
                "branch_query": q_alt_2 if fifth_source == "google" else clean_subj,
                "per_source": max(per_source, 8),
                "subject": clean_subj,
                "creature_mode": False,
            },
        ]
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
    Thực thi đồ thị LangGraph Map-Reduce 5 nhánh tìm kiếm ảnh song song:
    - Dùng `Send("image_branch_worker", branch)` để bắn 5 nhánh tìm kiếm cùng lúc.
    - Dùng `Annotated[List[Dict[str, Any]], operator.add]` để gom cả 5 kết quả về `aggregated_5_results`.
    """
    t0 = time.perf_counter()
    branches = build_5_image_search_branches(
        query=query,
        subject=subject,
        per_source=per_source,
        creature_mode=creature_mode,
        use_pure_google_search=use_pure_google_search,
        extra_queries=extra_queries,
        preferred_engines=preferred_engines,
    )

    def node_prepare_fanout(state: ParallelSearchState) -> Dict[str, Any]:
        return {"branches": branches}

    def route_5_parallel_sends(state: ParallelSearchState) -> List[Send]:
        return [
            Send("image_branch_worker", b)
            for b in state.get("branches", [])
        ]

    def image_branch_worker(state: SearchBranchPayload) -> Dict[str, Any]:
        b_t0 = time.perf_counter()
        eng = state.get("engine_or_source", "google")
        b_q = state.get("branch_query", query)
        b_ps = int(state.get("per_source") or per_source)
        items: List[Dict[str, Any]] = []
        try:
            if eng in ("google", "bing", "artstation", "wallhaven", "ddgs", "serper"):
                items = fetcher._run_single_open_engine(eng, b_q, b_ps)
            elif eng == "wikipedia_commons":
                wiki_items = fetcher.search_wikipedia_images(b_q, per_page=max(4, b_ps // 2))
                comm_items = fetcher.search_wikimedia_commons(b_q, per_page=max(4, b_ps // 2))
                items = (wiki_items or []) + (comm_items or [])
            elif eng == "openverse":
                items = fetcher.search_openverse(b_q, per_page=b_ps)
        except Exception as e:
            logger.debug(f"[LangGraph5WayImage] Worker {state.get('branch_name')} error: {e}")
            items = []

        elapsed_ms = int((time.perf_counter() - b_t0) * 1000)
        return {
            "aggregated_5_results": [
                {
                    "branch_idx": int(state.get("branch_idx", 0)),
                    "branch_name": state.get("branch_name", eng),
                    "engine": eng,
                    "query": b_q,
                    "count": len(items),
                    "elapsed_ms": elapsed_ms,
                    "items": items,
                }
            ]
        }

    def node_reduce_5_results(state: ParallelSearchState) -> Dict[str, Any]:
        raw_5 = list(state.get("aggregated_5_results") or [])
        # Sắp xếp đúng thứ tự ưu tiên của 5 nhánh (0 -> 4)
        raw_5.sort(key=lambda x: int(x.get("branch_idx", 99)))

        merged: List[Dict[str, Any]] = []
        seen_urls: Set[str] = set()
        for b_res in raw_5:
            for item in (b_res.get("items") or []):
                u = item.get("url")
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    merged.append(item)

        # Dự phòng nếu cả 5 nhánh trả về < 3 ảnh và có subject ngắn gọn hơn query
        subj_clean = (state.get("subject") or "").strip()
        if len(merged) < 3 and subj_clean and subj_clean.lower() != query.lower():
            try:
                extra_fallback = fetcher._run_single_open_engine("google", subj_clean, max(per_source, 10))
                for item in (extra_fallback or []):
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
            logger.debug(f"[LangGraph5WayImage] prefilter error: {e_pf}")

        total_ms = int((time.perf_counter() - t0) * 1000)
        branch_stats = ", ".join(
            f"{r.get('branch_name')}={r.get('count')}({r.get('elapsed_ms')}ms)"
            for r in raw_5
        )
        logger.info(
            f"⚡ [LangGraph Send+operator.add 5-Way Image] '{query}' (creature={creature_mode}) -> "
            f"Gom {len(raw_5)}/5 nhánh = {len(merged)} ảnh sạch trong {total_ms}ms [{branch_stats}]"
        )
        return {"merged_items": merged}

    builder = StateGraph(ParallelSearchState)
    builder.add_node("prepare_fanout", node_prepare_fanout)
    builder.add_node("image_branch_worker", image_branch_worker)
    builder.add_node("reduce_5_results", node_reduce_5_results)

    builder.add_edge(START, "prepare_fanout")
    builder.add_conditional_edges("prepare_fanout", route_5_parallel_sends, ["image_branch_worker"])
    builder.add_edge("image_branch_worker", "reduce_5_results")
    builder.add_edge("reduce_5_results", END)

    graph = builder.compile()
    final_state = graph.invoke(
        {
            "query": query,
            "subject": subject or query,
            "per_source": per_source,
            "creature_mode": creature_mode,
            "use_pure_google_search": use_pure_google_search,
            "extra_queries": list(extra_queries or []),
            "preferred_engines": list(preferred_engines or []),
            "aggregated_5_results": [],
        }
    )
    return list(final_state.get("merged_items") or [])


# ══════════════════════════════════════════════════════════════════════════════
# 3. LANGGRAPH 5-WAY PARALLEL STOCK VIDEO SEARCH (Pexels + Pixabay + NASA)
# ══════════════════════════════════════════════════════════════════════════════

def run_langgraph_5way_stock_video_search(
    stock_fetcher: Any,
    candidate_queries: List[str],
    include_pixabay: bool = True,
    include_nasa: bool = False,
    nasa_query: str = "",
) -> List[Dict[str, Any]]:
    """
    Sử dụng LangGraph `Send` API + `Annotated[List[Dict[str, Any]], operator.add]` để bắn song song
    5 nhánh tìm kiếm Video B-Roll dọc (Pexels + Pixabay + NASA) cùng lúc thay vì chạy vòng lặp tuần tự.
    Trả về danh sách `aggregated_5_results` đã sắp xếp theo thứ tự nhánh.
    """
    if not candidate_queries:
        return []

    t0 = time.perf_counter()
    branches: List[SearchBranchPayload] = []

    # Chọn tối đa 5 nhánh tìm kiếm video song song (3 nhánh Pexels + 2 nhánh Pixabay/NASA)
    pexels_qs = candidate_queries[:3]
    for idx, pq in enumerate(pexels_qs):
        branches.append({
            "branch_idx": idx,
            "branch_name": f"pexels_v_{idx + 1}",
            "engine_or_source": "pexels",
            "branch_query": pq,
            "per_source": 6,
        })

    if include_pixabay and getattr(stock_fetcher, "_pixabay_key", ""):
        for p_idx, px_q in enumerate(candidate_queries[:2]):
            if len(branches) >= 5:
                break
            branches.append({
                "branch_idx": len(branches),
                "branch_name": f"pixabay_v_{p_idx + 1}",
                "engine_or_source": "pixabay",
                "branch_query": px_q,
                "per_source": 5,
            })

    if include_nasa and len(branches) < 5:
        branches.append({
            "branch_idx": len(branches),
            "branch_name": "nasa_v_1",
            "engine_or_source": "nasa",
            "branch_query": nasa_query or candidate_queries[0],
            "per_source": 3,
        })

    # Nếu chưa đủ 5 nhánh và còn câu truy vấn Pexels dự phòng thì lấp đầy đủ 5 nhánh song song
    extra_idx = 3
    while len(branches) < 5 and extra_idx < len(candidate_queries):
        branches.append({
            "branch_idx": len(branches),
            "branch_name": f"pexels_v_{extra_idx + 1}",
            "engine_or_source": "pexels",
            "branch_query": candidate_queries[extra_idx],
            "per_source": 6,
        })
        extra_idx += 1

    def node_prepare(state: ParallelSearchState) -> Dict[str, Any]:
        return {"branches": branches}

    def route_sends(state: ParallelSearchState) -> List[Send]:
        return [Send("video_branch_worker", b) for b in state.get("branches", [])]

    def video_branch_worker(state: SearchBranchPayload) -> Dict[str, Any]:
        b_t0 = time.perf_counter()
        src = state.get("engine_or_source", "pexels")
        b_q = state.get("branch_query", "")
        b_ps = int(state.get("per_source") or 5)
        vids: List[Dict[str, Any]] = []
        try:
            if src == "pexels":
                vids = stock_fetcher.search_videos(b_q, per_page=b_ps, orientation="portrait")
            elif src == "pixabay":
                vids = stock_fetcher.search_pixabay_videos(b_q, per_page=b_ps)
            elif src == "nasa":
                vids = stock_fetcher.search_nasa_media(b_q, media_type="video", per_page=b_ps)
        except Exception as e:
            logger.debug(f"[LangGraph5WayVideo] Worker {state.get('branch_name')} error: {e}")
            vids = []

        elapsed_ms = int((time.perf_counter() - b_t0) * 1000)
        return {
            "aggregated_5_results": [
                {
                    "branch_idx": int(state.get("branch_idx", 0)),
                    "branch_name": state.get("branch_name", src),
                    "source": src,
                    "query": b_q,
                    "count": len(vids or []),
                    "elapsed_ms": elapsed_ms,
                    "items": vids or [],
                }
            ]
        }

    def node_reduce(state: ParallelSearchState) -> Dict[str, Any]:
        raw_res = list(state.get("aggregated_5_results") or [])
        raw_res.sort(key=lambda x: int(x.get("branch_idx", 99)))
        total_ms = int((time.perf_counter() - t0) * 1000)
        stats_str = ", ".join(f"{r.get('branch_name')}={r.get('count')}({r.get('elapsed_ms')}ms)" for r in raw_res)
        logger.info(
            f"⚡ [LangGraph Send+operator.add {len(raw_res)}-Way StockVideo] "
            f"Hoàn tất trong {total_ms}ms [{stats_str}]"
        )
        return {"merged_items": raw_res}

    builder = StateGraph(ParallelSearchState)
    builder.add_node("prepare", node_prepare)
    builder.add_node("video_branch_worker", video_branch_worker)
    builder.add_node("reduce", node_reduce)

    builder.add_edge(START, "prepare")
    builder.add_conditional_edges("prepare", route_sends, ["video_branch_worker"])
    builder.add_edge("video_branch_worker", "reduce")
    builder.add_edge("reduce", END)

    graph = builder.compile()
    final_state = graph.invoke({"branches": branches, "aggregated_5_results": []})
    return list(final_state.get("merged_items") or [])


# ══════════════════════════════════════════════════════════════════════════════
# 4. LANGGRAPH PARALLEL YOUTUBE SHORTS FLAT SEARCH (Send API + operator.add)
# ══════════════════════════════════════════════════════════════════════════════

def run_langgraph_parallel_youtube_flat_search(
    search_phrases: List[str],
    fetch_single_phrase_fn: Callable[[str], List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """
    Sử dụng LangGraph `Send` API + `Annotated[List[Dict[str, Any]], operator.add]` để quét song song
    tối đa 5 câu truy vấn YouTube Shorts (`ytsearch25:... #shorts`) cùng lúc và gom kết quả bằng `operator.add`.
    """
    phrases = [p.strip() for p in (search_phrases or []) if isinstance(p, str) and p.strip()][:5]
    if not phrases:
        return []

    t0 = time.perf_counter()
    branches: List[SearchBranchPayload] = [
        {
            "branch_idx": idx,
            "branch_name": f"yt_short_q{idx + 1}",
            "engine_or_source": "youtube_shorts",
            "branch_query": sp,
            "per_source": 25,
        }
        for idx, sp in enumerate(phrases)
    ]

    def node_prepare(state: ParallelSearchState) -> Dict[str, Any]:
        return {"branches": branches}

    def route_sends(state: ParallelSearchState) -> List[Send]:
        return [Send("yt_branch_worker", b) for b in state.get("branches", [])]

    def yt_branch_worker(state: SearchBranchPayload) -> Dict[str, Any]:
        b_t0 = time.perf_counter()
        sp = state.get("branch_query", "")
        entries = fetch_single_phrase_fn(sp) if sp else []
        elapsed_ms = int((time.perf_counter() - b_t0) * 1000)
        return {
            "aggregated_5_results": [
                {
                    "branch_idx": int(state.get("branch_idx", 0)),
                    "branch_name": state.get("branch_name", "yt"),
                    "query": sp,
                    "count": len(entries or []),
                    "elapsed_ms": elapsed_ms,
                    "items": entries or [],
                }
            ]
        }

    def node_reduce(state: ParallelSearchState) -> Dict[str, Any]:
        raw_res = list(state.get("aggregated_5_results") or [])
        raw_res.sort(key=lambda x: int(x.get("branch_idx", 99)))
        total_ms = int((time.perf_counter() - t0) * 1000)
        stats_str = ", ".join(f"'{r.get('query')}'={r.get('count')}({r.get('elapsed_ms')}ms)" for r in raw_res)
        logger.info(
            f"⚡ [LangGraph Send+operator.add YouTubeFlatSearch] "
            f"Quét song song {len(raw_res)} nhánh Shorts trong {total_ms}ms [{stats_str}]"
        )
        return {"merged_items": raw_res}

    builder = StateGraph(ParallelSearchState)
    builder.add_node("prepare", node_prepare)
    builder.add_node("yt_branch_worker", yt_branch_worker)
    builder.add_node("reduce", node_reduce)

    builder.add_edge(START, "prepare")
    builder.add_conditional_edges("prepare", route_sends, ["yt_branch_worker"])
    builder.add_edge("yt_branch_worker", "reduce")
    builder.add_edge("reduce", END)

    graph = builder.compile()
    final_state = graph.invoke({"branches": branches, "aggregated_5_results": []})
    return list(final_state.get("merged_items") or [])
