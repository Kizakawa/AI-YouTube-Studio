"""app.graph.tools.portrait_broll_executor — Thực thi công cụ Video B-Roll & Ảnh Chân Dung Sạch Chữ cho Tâm Lý Học."""
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain_core.tools import tool
from app.utils.langsmith_tracer import trace_custom_span
from app.agents.media_agent import MediaAgent, clean_scene_search_query
from app.services.media import license_policy
from config.settings import settings

logger = logging.getLogger(__name__)


def execute_clean_cinematic_portrait_or_broll_sync(
    subject_entity: str,
    human_action_query: str,
    output_path: str,
    media_preference: str = "VIDEO_BROLL",
    overall_script_query: str = "",
    search_scope: str = "SCENE_SPECIFIC",
    narration: str = "",
    topic: str = "",
    target_duration_sec: float = 4.5,
    used_urls: Optional[Set[str]] = None,
    is_hook_scene: bool = False,
    job_id: int = 0,
    scene_id: int = 0,
    target_image_count: int = 3,
    fallback_query: str = "",
    storyline_queries: Optional[List[str]] = None,
    cancel_event: Optional[threading.Event] = None,
    deadline_ts: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Thực thi công cụ `@tool fetch_clean_cinematic_portrait_or_broll`:
    - Bỏ qua 100% Open Web Google Images (tránh triệt để Quote Card có chữ, bìa sách, infographic).
    - Sử dụng trực tiếp bộ câu truy vấn phân cảnh (`human_action_query` + `storyline_queries` + `fallback_query`)
      do LLM phân rã từ kịch bản gốc để tìm kiếm khung hình khớp 100% với mạch truyện.
    """
    from app.services.media.media_history import extract_media_identity, is_media_already_used, record_used_media
    from app.services.video_memory_rag import get_video_memory_rag

    rag = get_video_memory_rag()
    used = used_urls if used_urls is not None else set()
    media_agent = MediaAgent()
    clean_act_q = (human_action_query or fallback_query or subject_entity or "dramatic portrait dark lighting").strip()
    pref_norm = (media_preference or "VIDEO_BROLL").strip().upper()
    story_q_list = [
        str(q).strip()
        for q in dict.fromkeys([
            clean_act_q,
            *(storyline_queries or []),
            fallback_query,
            overall_script_query,
        ])
        if str(q or "").strip()
    ]
    eff_fb_q = (
        fallback_query
        or (story_q_list[1] if len(story_q_list) > 1 else "")
        or overall_script_query
        or clean_act_q
    )

    # 1. Nhánh Video B-Roll & YouTube Shorts Peak:
    # - Với `VIDEO_BROLL` (cảnh thân bài >= 2): Ưu tiên #1 là Video B-Roll Pexels / Pixabay HD sạch chữ (~2-4s)
    #   theo đúng bộ câu truy vấn phân cảnh của LLM, giúp tránh nghẽn mạng khi 7 cảnh cùng gọi yt-dlp.
    #   Chỉ gọi YouTube Shorts Peak Clone khi kho Pexels/Pixabay không có clip đạt chuẩn.
    # - Với `YOUTUBE_FORENSIC_OR_CHARACTER_SHORT` hoặc Cảnh 1 (`is_hook_scene=True`): Ưu tiên #1 là YouTube Shorts Peak
    #   (với sub-deadline giới hạn để luôn chừa ngân sách cho tầng Pexels/Pixabay B-Roll).
    if pref_norm in ("YOUTUBE_FORENSIC_OR_CHARACTER_SHORT", "VIDEO_BROLL") or is_hook_scene:
        vid_out = (
            output_path
            if output_path.lower().endswith(".mp4")
            else str(Path(output_path).parent / f"scene_{scene_id:03d}_clean_broll.mp4")
        )

        def _try_stock_video_broll() -> Optional[Dict[str, Any]]:
            if (cancel_event is not None and cancel_event.is_set()) or (
                deadline_ts is not None and (deadline_ts - time.monotonic()) <= 2.5
            ):
                return None
            va_clean_vid = {
                "subject": subject_entity or clean_act_q,
                "search_queries": story_q_list[:4] if story_q_list else [clean_act_q],
                "clean_stock_only": True,
                "is_hook_scene": is_hook_scene,
            }
            best_stock_v = media_agent.search_best_video_clip(
                query=clean_act_q,
                used_urls=used,
                visual_analysis=va_clean_vid,
                narration=narration,
                topic=overall_script_query or topic,
            )
            if best_stock_v and best_stock_v.get("url"):
                v_url = best_stock_v["url"]
                st_title = str(best_stock_v.get("title") or "")
                if rag.try_claim_in_job_video(job_id=job_id, url=v_url, title=st_title, scene_id=scene_id):
                    with tempfile.TemporaryDirectory() as tmp_s:
                        raw_stock = str(Path(tmp_s) / "stock_raw.mp4")
                        media_agent.download_file(v_url, raw_stock)
                        if Path(raw_stock).exists() and Path(raw_stock).stat().st_size > 20000:
                            Path(vid_out).parent.mkdir(parents=True, exist_ok=True)
                            from app.utils.ffmpeg_helper import find_hook_peak_window, get_duration_ms, trim_video
                            raw_st_dur = max(0.0, get_duration_ms(raw_stock) / 1000.0)
                            min_st_dur = max(3.0, target_duration_sec)
                            st_start, st_peak_len = find_hook_peak_window(
                                raw_stock,
                                min_duration=min_st_dur,
                                max_duration=min(14.0, max(min_st_dur, raw_st_dur)),
                            )
                            trim_video(
                                input_path=raw_stock,
                                duration_seconds=min_st_dur,
                                output_path=vid_out,
                                width=1080,
                                height=1920,
                                fps=30,
                                apply_grading=True,
                                is_hook_scene=is_hook_scene,
                                fair_use_transform=False,
                                start_offset_sec=st_start,
                                preserve_continuous_flow=True,
                            )
                            if Path(vid_out).exists() and Path(vid_out).stat().st_size > 20000:
                                from app.graph.evaluators.visual_critic import VisualCritic
                                from app.graph.nodes.media.hook_video_helpers import verify_hook_clip_genuine_motion
                                from app.graph.tools.youtube_search_ranker import _verify_short_clip_with_gemini_vision
                                gate_p = VisualCritic.inspect_clip_before_concat(
                                    vid_out, min_short_edge=720, min_sharpness=5.2, check_watermark=True, allow_snow=True
                                )
                                ok_p_mot, _, _ = verify_hook_clip_genuine_motion(vid_out)
                                is_p_vis_ok = False
                                if gate_p.get("passed", False) and ok_p_mot:
                                    is_p_vis_ok, _, _ = _verify_short_clip_with_gemini_vision(
                                        video_path=vid_out,
                                        topic=topic,
                                        narration=narration,
                                        subject_entity=subject_entity or clean_act_q,
                                        overall_script_query=overall_script_query or clean_act_q,
                                        target_visual_query=clean_act_q,
                                        search_scope=search_scope,
                                        video_title=st_title,
                                        strict_no_fallback=True,
                                        cancel_event=cancel_event,
                                        deadline_ts=max(deadline_ts or 0.0, time.monotonic() + 12.0),
                                    )
                                ok_st_sig = False
                                if gate_p.get("passed", False) and ok_p_mot and is_p_vis_ok:
                                    ok_st_sig, _ = rag.verify_and_commit_in_job_clip(
                                        job_id=job_id,
                                        scene_id=scene_id,
                                        media_path=vid_out,
                                        url=v_url,
                                        title=st_title,
                                        is_continuation_slice=False,
                                    )
                                if ok_st_sig:
                                    full_peak_path = None
                                    if st_peak_len >= min_st_dur + 1.8 and raw_st_dur >= st_start + min_st_dur + 1.5:
                                        cand_full_st = str(Path(vid_out).parent / f"{Path(vid_out).stem}_fullpeak.mp4")
                                        try:
                                            trim_video(
                                                input_path=raw_stock,
                                                duration_seconds=st_peak_len,
                                                output_path=cand_full_st,
                                                width=1080,
                                                height=1920,
                                                fps=30,
                                                apply_grading=True,
                                                is_hook_scene=False,
                                                fair_use_transform=False,
                                                start_offset_sec=st_start,
                                                preserve_continuous_flow=True,
                                            )
                                            if Path(cand_full_st).exists() and Path(cand_full_st).stat().st_size > 25000:
                                                full_peak_path = cand_full_st
                                        except Exception:
                                            pass
                                    used.update(extract_media_identity(v_url))
                                    record_used_media(
                                        url=v_url,
                                        query=clean_act_q,
                                        job_id=job_id,
                                        source="clean_portrait_broll_video",
                                        media_path=vid_out,
                                    )
                                    logger.info(
                                        f"🎭 [CleanPortraitTool Video] Scene #{scene_id}: Đã tải B-Roll diễn viên thật sạch chữ "
                                        f"cho '{clean_act_q}' ({st_title[:45]})"
                                    )
                                    return {
                                        "media_path": vid_out,
                                        "media_type": "hook_ready_video" if is_hook_scene else "video",
                                        "source_url": v_url,
                                        "title": st_title or "Clean Portrait B-Roll Video",
                                        "window": f"{st_start:.1f}s-{st_start + (st_peak_len if full_peak_path else min_st_dur):.1f}s",
                                        "search_scope": search_scope,
                                        "full_peak_path": full_peak_path,
                                        "full_peak_duration_sec": round(st_peak_len if full_peak_path else min_st_dur, 2),
                                        "scene_clip_duration_sec": round(min_st_dur, 2),
                                        "clean_stock_only": True,
                                    }
                                else:
                                    Path(vid_out).unlink(missing_ok=True)
                                    rag.release_in_job_video_claim(job_id=job_id, vid_id=v_url, url=v_url, title=st_title, scene_id=scene_id)
                            else:
                                rag.release_in_job_video_claim(job_id=job_id, vid_id=v_url, url=v_url, title=st_title, scene_id=sid if "sid" in locals() else scene_id)
            return None

        def _try_youtube_short_peak() -> Optional[Dict[str, Any]]:
            if (cancel_event is not None and cancel_event.is_set()) or (
                deadline_ts is not None and (deadline_ts - time.monotonic()) <= 6.0
            ):
                return None
            yt_sub_deadline = (
                min(deadline_ts - 6.0, time.monotonic() + 42.0)
                if deadline_ts is not None
                else (time.monotonic() + 42.0)
            )
            if yt_sub_deadline - time.monotonic() <= 5.0:
                return None
            try:
                from app.graph.tools.youtube_peak_executor import execute_youtube_short_peak_clone_sync
                yt_forensic_res = execute_youtube_short_peak_clone_sync(
                    search_query=clean_act_q,
                    output_path=vid_out,
                    target_duration_sec=max(3.0, target_duration_sec),
                    clip_role="HOOK" if is_hook_scene else "PEAK",
                    used_urls=used,
                    fallback_query=eff_fb_q,
                    subject_entity=subject_entity or clean_act_q,
                    narration=narration,
                    topic=overall_script_query or topic,
                    search_scope=search_scope,
                    overall_script_query=overall_script_query,
                    max_peak_duration_sec=14.0,
                    job_id=job_id,
                    scene_id=scene_id,
                    storyline_queries=story_q_list,
                    skip_youtube_search=False,
                    skip_stock_fallback=(pref_norm == "VIDEO_BROLL" and not is_hook_scene),
                    cancel_event=cancel_event,
                    deadline_ts=yt_sub_deadline,
                )
                if yt_forensic_res and yt_forensic_res.get("media_path") and Path(yt_forensic_res["media_path"]).exists():
                    logger.info(
                        f"🎬 [CleanPortraitTool YT-Shorts Peak] Scene #{scene_id}: Đã cắt đoạn Peak YouTube Short "
                        f"cho '{clean_act_q}' ({yt_forensic_res.get('title', '')[:45]})"
                    )
                    return yt_forensic_res
            except Exception as e_yt_f:
                logger.debug(f"[CleanPortraitTool] YouTube Shorts priority skip on scene {scene_id}: {e_yt_f}")
            return None

        if pref_norm == "VIDEO_BROLL" and not is_hook_scene:
            st_res = _try_stock_video_broll()
            if st_res:
                return st_res
            yt_res = _try_youtube_short_peak()
            if yt_res:
                return yt_res
        else:
            yt_res = _try_youtube_short_peak()
            if yt_res:
                return yt_res
            st_res = _try_stock_video_broll()
            if st_res:
                return st_res

    # 2. Nhánh Bộ 3-4 Ảnh Chân Dung Nghệ Thuật Pexels / Pixabay HD Sạch Chữ 100% (`clean_stock_only=True`)
    # [RULE 2]: Tuyệt đối CẤM hạ cấp Cảnh 1 & Cảnh 2 (5-7s đầu tiên) xuống Ảnh tĩnh!
    if is_hook_scene or int(scene_id or 0) in (1, 2):
        return None
    img_out = (
        output_path
        if output_path.lower().endswith((".jpg", ".jpeg", ".png"))
        else str(Path(output_path).parent / f"scene_{scene_id:03d}_clean_portrait.jpg")
    )
    va_clean_photo = {
        "subject": subject_entity or clean_act_q,
        "search_queries": story_q_list[:4] if story_q_list else [clean_act_q],
        "clean_stock_only": True,
        "is_hook_scene": is_hook_scene,
    }
    best_photo = media_agent.search_best_photo(
        query=clean_act_q,
        used_urls=used,
        visual_analysis=va_clean_photo,
        narration=narration,
        topic=overall_script_query or topic or clean_act_q,
    )
    if not best_photo or not best_photo.get("url"):
        return None

    all_candidate_urls: List[str] = []
    for u_cand in [best_photo.get("url"), *(best_photo.get("alternate_urls") or [])]:
        if u_cand and u_cand not in all_candidate_urls:
            all_candidate_urls.append(u_cand)

    Path(img_out).parent.mkdir(parents=True, exist_ok=True)
    primary_selected_url = ""
    remaining_urls: List[str] = []
    for idx_u, cand_u in enumerate(all_candidate_urls):
        if idx_u > 0 and is_media_already_used(cand_u, used):
            continue
        try:
            media_agent.download_file(cand_u, img_out)
        except Exception:
            Path(img_out).unlink(missing_ok=True)
            continue
        if Path(img_out).exists() and Path(img_out).stat().st_size > 3000:
            if rag.verify_and_commit_in_job_image(job_id=job_id, scene_id=scene_id, image_path=img_out, url=cand_u):
                primary_selected_url = cand_u
                remaining_urls = [u for u in all_candidate_urls[idx_u + 1:] if u != cand_u and not is_media_already_used(u, used)]
                break
            else:
                Path(img_out).unlink(missing_ok=True)

    if not primary_selected_url or not Path(img_out).exists():
        return None

    used.update(extract_media_identity(primary_selected_url))
    record_used_media(
        url=primary_selected_url,
        query=clean_act_q,
        job_id=job_id,
        source="clean_portrait_photo",
        media_path=img_out,
    )

    pose_images: List[str] = [img_out]
    out_p = Path(img_out)
    desired_extra = max(0, int(target_image_count) - 1)
    if desired_extra > 0 and remaining_urls:
        from app.graph.parallel_search import run_langgraph_parallel_tasks
        cand_slice = remaining_urls[: max(desired_extra + 5, 6)]
        dl_tasks = [
            {"idx_r": idx_r, "c_url": c_url, "tmp_p": str(out_p.parent / f"{out_p.stem}_cand{idx_r + 2}.jpg")}
            for idx_r, c_url in enumerate(cand_slice)
        ]

        def _dl_portrait_item(t: Dict[str, Any]) -> Optional[Tuple[str, str]]:
            tmp_p = t["tmp_p"]
            c_url = t["c_url"]
            try:
                media_agent.download_file(c_url, tmp_p)
                if Path(tmp_p).exists() and Path(tmp_p).stat().st_size > 3000:
                    return tmp_p, c_url
            except Exception:
                pass
            Path(tmp_p).unlink(missing_ok=True)
            return None

        dl_completed = run_langgraph_parallel_tasks(
            tasks=dl_tasks,
            worker_fn=_dl_portrait_item,
            task_group=f"PortraitDownload({len(dl_tasks)} images)",
        )
        for dl_res in dl_completed:
            pair_out = dl_res.get("result")
            if not pair_out:
                continue
            tmp_p, c_url = pair_out
            if len(pose_images) < target_image_count:
                final_cut_p = str(out_p.parent / f"{out_p.stem}_cut{len(pose_images) + 1}.jpg")
                try:
                    Path(tmp_p).replace(final_cut_p)
                except Exception:
                    final_cut_p = tmp_p
                if rag.verify_and_commit_in_job_image(
                    job_id=job_id,
                    scene_id=scene_id,
                    image_path=final_cut_p,
                    url=c_url,
                    context_text=f"{clean_act_q} {topic} {narration}",
                ):
                    pose_images.append(final_cut_p)
                    used.update(extract_media_identity(c_url))
                    record_used_media(
                        url=c_url,
                        query=clean_act_q,
                        job_id=job_id,
                        source="clean_portrait_photo",
                        media_path=final_cut_p,
                    )
                else:
                    Path(final_cut_p).unlink(missing_ok=True)
            else:
                Path(tmp_p).unlink(missing_ok=True)

    logger.info(
        f"🎭 [CleanPortraitTool Photo] Scene #{scene_id}: Đã thu thập {len(pose_images)} ảnh chân dung sạch chữ "
        f"cho '{clean_act_q}'!"
    )
    return {
        "media_path": img_out,
        "media_type": "image",
        "pose_images": pose_images,
        "source_url": primary_selected_url,
        "title": best_photo.get("title", ""),
        "curation_score": best_photo.get("curation_score", 9.0),
        "alternate_urls": remaining_urls[len(pose_images):],
        "search_scope": search_scope,
        "clean_stock_only": True,
    }
