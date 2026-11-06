"""
app.utils.llm_node_inspector — Bộ Kiểm Định & Chẩn Đoán Trực Tiếp Các LLM Nodes (`generate_script`, `evaluate_script`, `visual_director`, `evaluate_tool_calls`).

Cho phép kiểm tra tức thì (chỉ trong vài giây, không cần đợi tải/render video):
1. Kịch bản do Node LLM `generate_script` viết ra đã đạt chuẩn so với các Kênh Hot Hit Triệu View chưa
   (`HotChannelScriptBenchmarkEvaluator` + `ScriptCritic`):
   - So sánh Tiêu đề, Hook 0.0s, Giọng văn nói YouTuber thật, Nhịp 5-Act Dual-Peak với Zack D. Films, Casual Geographic,
     FossilVideos, Charisma on Command, IFinity What, The Dodo, Você Sabia?.
   - Kiểm tra xem LLM viết kịch bản đã tự sinh `subject_entity` và `search_queries` chuẩn cho từng cảnh 1..N chưa.
2. Node LLM `visual_director` gọi `@tool` gì cho từng phân cảnh (`node_evaluate_llm_tool_calls` / `LLMToolCallCritic`):
   - Kiểm tra tỷ lệ `@tool` Video chuyển động thật (bắt buộc >= 85%, mục tiêu 100% trên Shorts).
   - Kiểm tra độ khớp ngữ cảnh giữa câu thoại (`narration`) và tham số gọi Tool (`subject_entity`, `youtube_search_query`, `fallback_broad_query`).
   - Hiển thị các cảnh được `LLMToolCallCritic` tự động sửa (`AUTO_HEALED`) trước khi đi tải video.

Cách chạy CLI:
  python -m app.utils.llm_node_inspector --job 764
  python -m app.utils.llm_node_inspector --topic "Harpy Eagle vs Sloth: Deadly Canopy Ambush" --niche wildlife_predators
  python -m app.utils.llm_node_inspector --suite
"""
import argparse
import asyncio
import json
import sys
import time
from typing import Any, Dict, List, Optional

from app.graph.nodes.media.evaluate_tool_calls_node import node_evaluate_llm_tool_calls
from app.graph.nodes.media.visual_director_node import node_direct_visual_strategy
from app.graph.nodes.script_nodes import (
    node_evaluate_script,
    node_generate_script,
    node_refine_script,
)


def _configure_utf8_stdout() -> None:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


async def inspect_llm_nodes_for_topic(
    topic: str,
    channel_style: str = "wildlife_predators",
    language: str = "en",
    existing_script_data: Optional[Dict[str, Any]] = None,
    job_id: Optional[int] = None,
    allow_refine: bool = True,
) -> Dict[str, Any]:
    """
    Chạy chuỗi LLM Nodes (`generate_script` -> `evaluate_script` [kèm `HotChannelScriptBenchmark`]
    -> [tùy chọn `refine_script`] -> `visual_director` -> `evaluate_tool_calls`) và trả về báo cáo chi tiết.
    """
    t0 = time.monotonic()
    state: Dict[str, Any] = {
        "job_id": job_id or 0,
        "topic": topic,
        "channel_style": channel_style,
        "language": language,
        "video_format": "short",
        "script_retry_count": 0,
    }
    if existing_script_data and existing_script_data.get("scenes"):
        state["script_data"] = existing_script_data
        if existing_script_data.get("primary_entity"):
            state["primary_entity"] = existing_script_data.get("primary_entity")

    # 1. Node LLM sinh kịch bản (kèm subject_entity + search_queries từng cảnh)
    t_gen0 = time.monotonic()
    gen_out = await asyncio.to_thread(node_generate_script, state)
    state.update(gen_out)
    t_gen_sec = round(time.monotonic() - t_gen0, 2)

    # 2. Node LLM chấm điểm kịch bản & đối sánh Kênh Hot Hit (chạy song song ScriptCritic + HotChannelScriptBenchmark)
    t_eval0 = time.monotonic()
    eval_out = await asyncio.to_thread(node_evaluate_script, state)
    state.update(eval_out)
    t_eval_sec = round(time.monotonic() - t_eval0, 2)

    # 2b. Nếu chưa đạt 8.0+ và cho phép refine, chạy vòng `node_refine_script` -> `node_evaluate_script` và giữ lại ứng viên tốt nhất
    script_eval = state.get("script_eval") or {}
    hot_eval = state.get("hot_channel_benchmark_eval") or script_eval.get("hot_channel_benchmark") or {}
    if allow_refine and (not script_eval.get("passed") or not hot_eval.get("passed", True)):
        ref_out = await asyncio.to_thread(node_refine_script, state)
        state.update(ref_out)
        state["script_retry_count"] = 2
        eval_out2 = await asyncio.to_thread(node_evaluate_script, state)
        state.update(eval_out2)
        script_eval = state.get("script_eval") or {}
        hot_eval = state.get("hot_channel_benchmark_eval") or script_eval.get("hot_channel_benchmark") or {}

    # 3. Node LLM Đạo diễn hình ảnh (`visual_director` - gọi @tool cho từng cảnh)
    t_dir0 = time.monotonic()
    dir_out = await node_direct_visual_strategy(state)
    state.update(dir_out)
    t_dir_sec = round(time.monotonic() - t_dir0, 2)

    # 4. Node LLM Kiểm định & Tự chữa lành Tool-Calling (`evaluate_tool_calls`)
    t_tc0 = time.monotonic()
    tc_out = await node_evaluate_llm_tool_calls(state)
    state.update(tc_out)
    t_tc_sec = round(time.monotonic() - t_tc0, 2)

    total_sec = round(time.monotonic() - t0, 2)
    return {
        "topic": topic,
        "channel_style": channel_style,
        "language": language,
        "timings_sec": {
            "generate_script": t_gen_sec,
            "evaluate_script_and_hot_benchmark": t_eval_sec,
            "visual_director_tool_calling": t_dir_sec,
            "evaluate_tool_calls": t_tc_sec,
            "total": total_sec,
        },
        "script_data": state.get("script_data") or {},
        "script_eval": script_eval,
        "hot_channel_benchmark_eval": hot_eval,
        "visual_plan": state.get("visual_plan") or {},
        "tool_call_eval": state.get("tool_call_eval") or {},
    }


def format_inspection_report(result: Dict[str, Any]) -> str:
    """Định dạng báo cáo kiểm tra các LLM Nodes thành bảng Markdown rõ ràng."""
    script_data = result.get("script_data") or {}
    script_eval = result.get("script_eval") or {}
    hot_eval = result.get("hot_channel_benchmark_eval") or script_eval.get("hot_channel_benchmark") or {}
    tc_eval = result.get("tool_call_eval") or {}
    timings = result.get("timings_sec") or {}
    scenes = script_data.get("scenes") or []

    lines: List[str] = []
    lines.append("=" * 96)
    lines.append(f"🔎 BÁO CÁO KIỂM ĐỊNH CÁC NODE LLM (SCRIPT vs KÊNH HOT HIT & TOOL-CALLING AUDIT)")
    lines.append("=" * 96)
    lines.append(f"- Chủ đề (Topic)       : {result.get('topic')}")
    lines.append(f"- Ngách (Niche)        : {result.get('channel_style')} | Ngôn ngữ: {result.get('language')}")
    lines.append(f"- Tiêu đề sinh ra      : {script_data.get('title')}")
    lines.append(f"- Thực thể chính       : {script_data.get('primary_entity')}")
    lines.append(f"- Số phân cảnh         : {len(scenes)} cảnh | Thời lượng ước tính: {script_data.get('total_duration_estimate')}s")
    lines.append(
        f"- Thời gian chạy LLM   : Tổng {timings.get('total')}s "
        f"(GenScript={timings.get('generate_script')}s | HotBench+Critic={timings.get('evaluate_script_and_hot_benchmark')}s | "
        f"VisualDirector={timings.get('visual_director_tool_calling')}s | ToolCallAudit={timings.get('evaluate_tool_calls')}s)"
    )
    lines.append("")

    # PHẦN 1: SO SÁNH KỊCH BẢN VỚI CÁC KÊNH HOT HIT TRIỆU VIEW
    bench_channels = ", ".join(hot_eval.get("benchmark_channels_compared") or ["Top Viral Channels"])
    lines.append(f"## 1. ĐỐI SÁNH KỊCH BẢN VỚI KÊNH HOT HIT ({bench_channels})")
    lines.append(
        f"- Điểm ScriptCritic tổng thể : {script_eval.get('overall_score')}/10 "
        f"(Hook 0.0s: {(script_eval.get('hook_strength') or {}).get('score')}/10 | "
        f"Pacing/Voice: {(script_eval.get('story_pacing') or {}).get('score')}/10 | "
        f"Visual Dir: {(script_eval.get('visual_direction') or {}).get('score')}/10)"
    )
    lines.append(
        f"- Điểm Chuẩn Kênh Hot Hit    : {hot_eval.get('overall_hot_benchmark_score')}/10 "
        f"(Đạt {hot_eval.get('hot_parity_percent')}% công lực kênh triệu view | Passed={hot_eval.get('passed')})"
    )
    for dim_key, dim_label in (
        ("title_vs_hot_channel", "Tiêu đề (YT_SEARCH & CTR)"),
        ("hook_0s_vs_hot_channel", "Hook Cảnh 1 (0.0s Thumb-Stopper)"),
        ("spoken_voice_dna_vs_hot_channel", "Giọng kể YouTuber đời thường"),
        ("dual_peak_5act_arc_vs_hot_channel", "Cấu trúc 5-Act Dual-Peak 34s-40s"),
    ):
        d_obj = hot_eval.get(dim_key) or {}
        if d_obj:
            lines.append(
                f"  * {dim_label:<32}: {d_obj.get('score')}/10 — {d_obj.get('comparison_notes')}"
            )
    lines.append(
        f"  * {'Truy vấn từng cảnh của Script LLM':<32}: {hot_eval.get('script_llm_scene_search_score')}/10 "
        f"(Cảnh được tự động sửa ngay từ gốc: {list((hot_eval.get('repaired_scene_search_map') or {}).keys()) or 'Không'})"
    )
    strengths = hot_eval.get("winning_strengths_vs_hot_channels") or []
    if strengths:
        lines.append("  + Điểm mạnh nổi bật so với kênh Hot : " + " | ".join(strengths[:3]))
    gaps = hot_eval.get("gaps_vs_hot_channels") or []
    if gaps:
        lines.append("  ! Điểm cần lưu ý so với kênh Hot    : " + " | ".join(gaps[:3]))
    lines.append("")

    # PHẦN 2: KIỂM ĐỊNH CÁC NODE LLM GỌI TOOL & TRUY VẤN TÌM KIẾM TỪNG CẢNH
    lines.append("## 2. KIỂM ĐỊNH NODE LLM GỌI `@tool` & TRUY VẤN TỪNG PHÂN CẢNH (`evaluate_tool_calls`)")
    lines.append(
        f"- Điểm Tool-Calling tổng thể : {tc_eval.get('overall_tool_call_score')}/10 | "
        f"Tỷ lệ Tool Video chuyển động: {tc_eval.get('moving_video_tool_scenes')}/{tc_eval.get('total_scenes')} cảnh "
        f"({tc_eval.get('moving_video_planned_ratio_pct')}%) | "
        f"Cảnh Auto-Healed: {tc_eval.get('auto_healed_scene_ids') or 'Không'}"
    )
    lines.append("-" * 96)
    for a_item in (tc_eval.get("scene_audits") or []):
        sid = a_item.get("scene_id")
        lines.append(
            f"[Cảnh {sid}] Tool: `{a_item.get('selected_tool')}` ({'VIDEO' if a_item.get('is_moving_video_tool') else 'IMAGE'}) | "
            f"Scope: {a_item.get('search_scope')} | Status: {a_item.get('status')} "
            f"(Fit={a_item.get('tool_fit_score')}/10, Relevance={a_item.get('query_relevance_score')}/10)"
        )
        lines.append(f"  - Lời thoại (Narration)   : \"{a_item.get('narration')}\"")
        lines.append(f"  - Chủ thể (`subject`)     : {a_item.get('subject_entity')}")
        lines.append(f"  - Truy vấn chính (`prim`) : {a_item.get('primary_query')}  |  Dự phòng (`stock`): {a_item.get('fallback_query')}")
        lines.append(f"  - Danh sách truy vấn LLM  : {a_item.get('storyline_search_queries')}")
        lines.append(f"  - Nhận xét Giám khảo Tool : {a_item.get('audit_notes')}")
    lines.append("=" * 96)
    return "\n".join(lines)


async def inspect_existing_job(job_id: int, allow_refine: bool = False) -> Dict[str, Any]:
    """Nạp kịch bản của một `VideoJob` có sẵn trong DB và chạy kiểm định đầy đủ các LLM Nodes."""
    from app.database.models import VideoJob, get_db

    with get_db() as db:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job:
            raise ValueError(f"Không tìm thấy VideoJob #{job_id} trong cơ sở dữ liệu.")
        raw_scenes = json.loads(job.scenes_json) if job.scenes_json else {}
        topic = str(job.topic or raw_scenes.get("title") or f"Job #{job_id}")
        niche = str(getattr(job, "channel_style", None) or raw_scenes.get("channel_niche") or "wildlife_predators")
        lang = str(raw_scenes.get("language") or "en")

    return await inspect_llm_nodes_for_topic(
        topic=topic,
        channel_style=niche,
        language=lang,
        existing_script_data=raw_scenes if raw_scenes.get("scenes") else None,
        job_id=job_id,
        allow_refine=allow_refine,
    )


async def _async_main() -> None:
    _configure_utf8_stdout()
    parser = argparse.ArgumentParser(description="Kiểm định trực tiếp các LLM Nodes (Script vs Kênh Hot Hit & Tool-Calling Audit).")
    parser.add_argument("--job", type=int, default=None, help="Kiểm tra trên một VideoJob ID có sẵn trong DB (ví dụ: --job 764)")
    parser.add_argument("--refine", action="store_true", help="Cho phép chạy vòng lặp refine_script nếu kịch bản chưa đạt chuẩn Kênh Hot Hit")
    parser.add_argument("--topic", type=str, default=None, help="Chủ đề thử nghiệm mới")
    parser.add_argument("--niche", type=str, default="wildlife_predators", help="Hồ sơ ngách (wildlife_predators, psychology, alternate_history, deep_sea_earth_mysteries, animal_rescue)")
    parser.add_argument("--lang", type=str, default="en", help="Ngôn ngữ kịch bản (en, pt, vi)")
    parser.add_argument("--suite", action="store_true", help="Chạy kiểm định trên bộ chủ đề mẫu đa ngách")
    args = parser.parse_args()

    if args.job:
        res = await inspect_existing_job(args.job, allow_refine=args.refine)
        print(format_inspection_report(res))
        return

    if args.suite:
        suite_cases = [
            ("Jaguar vs Black Caiman: Skull-Crushing River Ambush", "wildlife_predators", "en"),
            ("When Someone Says 'Relax, I'm Just Joking' To Disrespect You", "psychology", "en"),
            ("1961 Goldsboro B-52 Crash: Two 4-Megaton Nukes vs One Switch", "alternate_history", "en"),
        ]
        for topic_s, niche_s, lang_s in suite_cases:
            res = await inspect_llm_nodes_for_topic(topic=topic_s, channel_style=niche_s, language=lang_s)
            print(format_inspection_report(res))
            print("\n")
        return

    topic_to_run = args.topic or "Secretary Bird vs Black Mamba: Skull-Shattering Stomp Faster Than A Blink"
    res = await inspect_llm_nodes_for_topic(topic=topic_to_run, channel_style=args.niche, language=args.lang)
    print(format_inspection_report(res))


if __name__ == "__main__":
    asyncio.run(_async_main())
