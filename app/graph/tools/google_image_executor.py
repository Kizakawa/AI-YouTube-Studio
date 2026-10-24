"""app.graph.tools.google_image_executor — Thực thi tìm kiếm đa ảnh Top Google Images & Stock HD."""
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from langchain_core.tools import tool
from app.utils.langsmith_tracer import trace_custom_span
from app.agents.media_agent import MediaAgent, clean_scene_search_query
from app.services.media import license_policy
from config.settings import settings

logger = logging.getLogger(__name__)
from app.graph.tools.tool_schemas import (
    _clean_yt_search_phrase,
    _resolve_adaptive_search_queries,
)


@trace_custom_span("MediaTools.execute_google_cinematic_image", run_type="tool")
def execute_google_cinematic_image_sync(
    smart_query: str,
    output_path: str,
    narration: str = "",
    topic: str = "",
    used_urls: Optional[Set[str]] = None,
    is_hook_scene: bool = False,
    subject_entity: str = "",
    search_scope: str = "SCENE_SPECIFIC",
    overall_script_query: str = "",
    job_id: int = 0,
    scene_id: int = 0,
    target_image_count: int = 2,
) -> Optional[Dict[str, Any]]:
    """
    Thực thi công cụ tìm ảnh Top Search Google Images theo cơ chế kép (`SCENE_SPECIFIC` vs `SCRIPT_OVERALL`)
    kết hợp Multi-Image Harvest (tải 2-3 ảnh thật độc lập/phân cảnh để đạt mật độ 15-17 ảnh/video ~40s):
    - Nếu câu nhỏ có nghĩa độc lập (`SCENE_SPECIFIC`): Tìm trực tiếp theo ý câu nhỏ (`smart_query`), dự phòng bằng `overall_script_query`.
    - Nếu câu nhỏ khi tách lẻ bị vô nghĩa / cụt ngủn / vướng đạo cụ phụ (`SCRIPT_OVERALL`):
      Tìm trực tiếp các hình ảnh xoay quanh TỔNG THỂ KỊCH BẢN (`overall_script_query`) và lọc trùng lặp (`used_urls` + Mini-RAG 64-bit dHash)
      để ghép vào video mượt mà, 100% đúng chủ đề kịch bản!
    """
    from concurrent.futures import ThreadPoolExecutor
    from app.services.media.media_history import extract_media_identity, is_media_already_used, record_used_media
    from app.services.video_memory_rag import get_video_memory_rag

    rag = get_video_memory_rag()
    used = used_urls if used_urls is not None else set()
    effective_scope, search_qs = _resolve_adaptive_search_queries(
        search_scope=search_scope,
        scene_query=smart_query,
        overall_script_query=overall_script_query,
        fallback_query=f"{subject_entity} {topic}",
        subject_entity=subject_entity,
        topic=topic,
        narration=narration,
        max_words=6,
    )

    subj = (subject_entity or "").strip()
    if not subj:
        subj = _clean_yt_search_phrase(overall_script_query or topic, max_words=3) or (search_qs[0] if search_qs else smart_query)

    primary_q = search_qs[0] if search_qs else smart_query
    logger.info(
        f"🖼️ [GoogleImageTool] Scene #{scene_id or '?'} | Scope={effective_scope} | Primary='{primary_q}' | "
        f"TargetImages={target_image_count} | Pool={search_qs[:3]}"
    )

    va_photo = {
        "subject": subj,
        "literal_subject": (overall_script_query or subj) if effective_scope == "SCRIPT_OVERALL" else subj,
        "search_queries": search_qs[:3],
        "google_image_queries": search_qs[:3],
        "is_hook_scene": is_hook_scene,
    }

    effective_narration = (
        f"Overall story: {overall_script_query or topic} ({subj}). Context: {narration}"
        if effective_scope == "SCRIPT_OVERALL"
        else narration
    )

    media_agent = MediaAgent()
    best_photo = media_agent.search_best_photo(
        query=primary_q,
        used_urls=used,
        visual_analysis=va_photo,
        narration=effective_narration,
        topic=overall_script_query or topic,
    )
    if not best_photo or not best_photo.get("url"):
        return None

    all_candidate_urls: List[str] = []
    for u_cand in [best_photo.get("url"), *(best_photo.get("alternate_urls") or [])]:
        if u_cand and u_cand not in all_candidate_urls:
            all_candidate_urls.append(u_cand)

    # Khi cần >= 2 ảnh/cảnh, bồi thêm nhanh ứng viên góc nhìn phụ thứ 2 (ưu tiên Pexels/Pixabay HD trước khi gọi WebImageFetcher)
    if target_image_count >= 2 and len(all_candidate_urls) < max(8, target_image_count * 2):
        sec_q = search_qs[1] if len(search_qs) > 1 else f"{subj} wildlife photography"
        try:
            for q_extra in [sec_q, *(search_qs[2:3] if len(search_qs) > 2 else [])]:
                for px_it in (media_agent.search_photos(q_extra, per_page=5, orientation="portrait", topic=overall_script_query or topic, narration=narration, used_urls=used) or []):
                    u_px = px_it.get("url")
                    if u_px and u_px not in all_candidate_urls and not is_media_already_used(u_px, used):
                        all_candidate_urls.append(u_px)
                for pxb_it in (media_agent._stock_fetcher.search_pixabay_photos(q_extra, per_page=5) or []):
                    u_pxb = pxb_it.get("url")
                    if u_pxb and u_pxb not in all_candidate_urls and not is_media_already_used(u_pxb, used):
                        all_candidate_urls.append(u_pxb)
        except Exception:
            pass
        try:
            from app.services.media.web_image_fetcher import WebImageFetcher, bypass_copyright_enabled
            if bypass_copyright_enabled() or len(all_candidate_urls) < target_image_count + 2:
                extra_web = WebImageFetcher().search_all(
                    query=sec_q,
                    per_source=6,
                    subject=subj,
                    extra_queries=search_qs[2:3] if len(search_qs) > 2 else None,
                )
                for item_ex in (extra_web or []):
                    u_ex = item_ex.get("url")
                    if u_ex and u_ex not in all_candidate_urls and not is_media_already_used(u_ex, used):
                        all_candidate_urls.append(u_ex)
        except Exception:
            pass

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    primary_selected_url = ""
    remaining_urls: List[str] = []

    for idx_u, cand_u in enumerate(all_candidate_urls):
        if idx_u > 0 and is_media_already_used(cand_u, used):
            continue
        try:
            media_agent.download_file(cand_u, output_path)
        except Exception as _dl_err:
            logger.debug(f"[GoogleImageTool] Bỏ qua URL ảnh lỗi ({cand_u[:70]}): {_dl_err}")
            Path(output_path).unlink(missing_ok=True)
            continue
        if Path(output_path).exists() and Path(output_path).stat().st_size > 3000:
            if rag.verify_and_commit_in_job_image(
                job_id=job_id,
                scene_id=scene_id,
                image_path=output_path,
                url=cand_u,
                context_text=f"{primary_q} {topic} {narration}",
            ):
                primary_selected_url = cand_u
                remaining_urls = [u for u in all_candidate_urls[idx_u + 1:] if u != cand_u and not is_media_already_used(u, used)]
                break
            else:
                Path(output_path).unlink(missing_ok=True)

    if not primary_selected_url or not Path(output_path).exists():
        return None

    used.update(extract_media_identity(primary_selected_url))
    record_used_media(
        url=primary_selected_url,
        query=primary_q,
        job_id=job_id,
        source=best_photo.get("source", "google_image"),
        media_path=output_path,
    )

    pose_images: List[str] = [output_path]
    desired_extra = max(0, int(target_image_count) - 1)

    if desired_extra > 0 and remaining_urls:
        out_p = Path(output_path)
        stem = out_p.stem
        parent = out_p.parent
        cand_slice = remaining_urls[: max(desired_extra + 6, 8)]

        def _dl_cand(pair: Tuple[int, str]) -> Optional[Tuple[str, str]]:
            c_idx, c_url = pair
            tmp_path = str(parent / f"{stem}_cand{c_idx + 2}.jpg")
            try:
                media_agent.download_file(c_url, tmp_path)
                if Path(tmp_path).exists() and Path(tmp_path).stat().st_size > 3000:
                    return tmp_path, c_url
            except Exception:
                pass
            Path(tmp_path).unlink(missing_ok=True)
            return None

        with ThreadPoolExecutor(max_workers=min(4, len(cand_slice))) as pool:
            dl_results = list(pool.map(_dl_cand, enumerate(cand_slice)))

        unused_after_multi: List[str] = []
        for res_pair in dl_results:
            if not res_pair:
                continue
            tmp_p, c_url = res_pair
            if len(pose_images) < target_image_count:
                final_cut_p = str(parent / f"{stem}_cut{len(pose_images) + 1}.jpg")
                try:
                    Path(tmp_p).replace(final_cut_p)
                except Exception:
                    final_cut_p = tmp_p
                if rag.verify_and_commit_in_job_image(
                    job_id=job_id,
                    scene_id=scene_id,
                    image_path=final_cut_p,
                    url=c_url,
                    context_text=f"{primary_q} {topic} {narration}",
                ):
                    pose_images.append(final_cut_p)
                    used.update(extract_media_identity(c_url))
                    record_used_media(
                        url=c_url,
                        query=primary_q,
                        job_id=job_id,
                        source=best_photo.get("source", "google_image"),
                        media_path=final_cut_p,
                    )
                else:
                    Path(final_cut_p).unlink(missing_ok=True)
            else:
                Path(tmp_p).unlink(missing_ok=True)
                unused_after_multi.append(c_url)

        remaining_urls = unused_after_multi + remaining_urls[len(cand_slice):]

    return {
        "media_path": output_path,
        "media_type": "image",
        "pose_images": pose_images,
        "source_url": primary_selected_url,
        "title": best_photo.get("title", ""),
        "curation_score": best_photo.get("curation_score", 9.0),
        "alternate_urls": remaining_urls,
        "search_scope": effective_scope,
    }
