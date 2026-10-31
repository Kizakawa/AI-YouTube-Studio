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
    cancel_event: Optional[threading.Event] = None,
    deadline_ts: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Thực thi công cụ `@tool fetch_clean_cinematic_portrait_or_broll`:
    - Bỏ qua 100% Open Web Google Images (tránh triệt để Quote Card có chữ, bìa sách, infographic).
    - Khi `media_preference == "VIDEO_BROLL"` (hoặc cảnh Hook):
      Truy xuất trực tiếp Video B-Roll quay diễn viên thật chuẩn dọc 9:16 từ Pexels HD / Pixabay HD,
      cắt đoạn Peak cảm xúc và trả về cả `full_peak_path` nếu đoạn Peak dài.
    - Khi `media_preference == "CLEAN_PORTRAIT_PHOTO"` (hoặc dự phòng):
      Truy xuất bộ 3-4 bức ảnh chân dung nghệ thuật ánh sáng tối (Chiaroscuro / Moody Portrait) sạch chữ 100%
      từ Pexels & Pixabay Photo API.
    """
    from app.services.media.media_history import extract_media_identity, is_media_already_used, record_used_media
    from app.services.video_memory_rag import get_video_memory_rag

    rag = get_video_memory_rag()
    used = used_urls if used_urls is not None else set()
    media_agent = MediaAgent()
    clean_act_q = (human_action_query or subject_entity or "dramatic portrait dark lighting").strip()
    pref_norm = (media_preference or "VIDEO_BROLL").strip().upper()

    # 1. Ưu tiên #1: Săn đoạn Peak từ YouTube Shorts (Băng thẩm vấn FBI/CIA thật, Tòa án, 3D Não bộ, hoặc Nhân vật điện ảnh biểu tượng)
    if pref_norm in ("YOUTUBE_FORENSIC_OR_CHARACTER_SHORT", "VIDEO_BROLL") or is_hook_scene:
        vid_out = (
            output_path
            if output_path.lower().endswith(".mp4")
            else str(Path(output_path).parent / f"scene_{scene_id:03d}_clean_broll.mp4")
        )
        try:
            from app.graph.tools.youtube_peak_executor import execute_youtube_short_peak_clone_sync
            yt_forensic_res = execute_youtube_short_peak_clone_sync(
                search_query=clean_act_q,
                output_path=vid_out,
                target_duration_sec=max(3.0, target_duration_sec),
                clip_role="HOOK" if is_hook_scene else "PEAK",
                used_urls=used,
                fallback_query=overall_script_query or "fbi interrogation suspect dark",
                subject_entity=subject_entity or clean_act_q,
                narration=narration,
                topic=overall_script_query or topic,
                search_scope=search_scope,
                overall_script_query=overall_script_query,
                max_peak_duration_sec=14.0,
                job_id=job_id,
                scene_id=scene_id,
                skip_youtube_search=False,
                cancel_event=cancel_event,
                deadline_ts=deadline_ts,
            )
            if yt_forensic_res and yt_forensic_res.get("media_path") and Path(yt_forensic_res["media_path"]).exists():
                logger.info(
                    f"🎬 [CleanPortraitTool YT-Shorts Priority #1] Scene #{scene_id}: Đã cắt đoạn Peak YouTube Short "
                    f"cho '{clean_act_q}' ({yt_forensic_res.get('title', '')[:45]})"
                )
                return yt_forensic_res
        except Exception as e_yt_f:
            logger.debug(f"[CleanPortraitTool] YouTube Shorts priority skip on scene {scene_id}: {e_yt_f}")

        if (cancel_event is not None and cancel_event.is_set()) or (
            deadline_ts is not None and (deadline_ts - time.monotonic()) <= 2.5
        ):
            return None

        # Ưu tiên #2: Dự phòng Video B-Roll Dark Noir diễn viên thật Pexels / Pixabay HD sạch chữ 100%
        va_clean_vid = {
            "subject": subject_entity or clean_act_q,
            "search_queries": [
                clean_act_q,
                "detective interrogation dark room",
                "close up eyes dark lighting",
                "silhouette person walking night",
            ],
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

    # 2. Nhánh Bộ 3-4 Ảnh Chân Dung Nghệ Thuật Pexels / Pixabay HD Sạch Chữ 100% (`clean_stock_only=True`)
    img_out = (
        output_path
        if output_path.lower().endswith((".jpg", ".jpeg", ".png"))
        else str(Path(output_path).parent / f"scene_{scene_id:03d}_clean_portrait.jpg")
    )
    va_clean_photo = {
        "subject": subject_entity or clean_act_q,
        "search_queries": [
            clean_act_q,
            "dramatic portrait dark lighting",
            "man intense eyes dark shadow",
            "person silhouette shadow",
        ],
        "clean_stock_only": True,
        "is_hook_scene": is_hook_scene,
    }
    best_photo = media_agent.search_best_photo(
        query=clean_act_q,
        used_urls=used,
        visual_analysis=va_clean_photo,
        narration=narration,
        topic="dramatic portrait",
    )
    if not best_photo or not best_photo.get("url"):
        return None

    all_candidate_urls: List[str] = []
    for u_cand in [best_photo.get("url"), *(best_photo.get("alternate_urls") or [])]:
        if u_cand and u_cand not in all_candidate_urls:
            all_candidate_urls.append(u_cand)

    if len(all_candidate_urls) < max(8, target_image_count * 2):
        try:
            from app.graph.parallel_search import run_langgraph_parallel_tasks
            supp_queries = [clean_act_q, "dramatic portrait dark shadow", "intense eyes close up dark", "person silhouette night"]
            px_tasks = [{"q": q_s} for q_s in supp_queries]
            px_completed = run_langgraph_parallel_tasks(
                tasks=px_tasks,
                worker_fn=lambda t: media_agent.search_photos(
                    t["q"], per_page=5, orientation="portrait", topic="dramatic portrait", narration="", used_urls=used
                ) or [],
                task_group=f"PortraitPexelsSearch({len(px_tasks)} queries)",
            )
            for px_res in px_completed:
                for p_it in (px_res.get("result") or []):
                    u_px = p_it.get("url")
                    if u_px and u_px not in all_candidate_urls and not is_media_already_used(u_px, used):
                        all_candidate_urls.append(u_px)
        except Exception:
            pass

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
