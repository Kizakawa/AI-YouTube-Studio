"""app.graph.tools.monster_media_executor — Thực thi công cụ 3D CGI & 8K Concept Art cho Quái Vật/Megalophobia."""
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
    _sanitize_tool_arg,
)
from app.graph.tools.youtube_peak_executor import execute_youtube_short_peak_clone_sync


def execute_epic_monster_cinematic_media_sync(
    creature_name: str,
    scale_anchor_object: str,
    output_path: str,
    monster_media_mode: str = "3D_CGI_VIDEO",
    action_or_concept_query: str = "",
    overall_script_query: str = "",
    search_scope: str = "SCENE_SPECIFIC",
    narration: str = "",
    topic: str = "",
    target_duration_sec: float = 4.5,
    used_urls: Optional[Set[str]] = None,
    is_hook_scene: bool = False,
    job_id: int = 0,
    scene_id: int = 0,
    channel_id: str = "",
    target_image_count: int = 3,
) -> Optional[Dict[str, Any]]:
    """
    Thực thi công cụ `@tool summon_epic_monster_cinematic_media` (3-Tier Epic Monster & Megalophobia Engine):
    - Tầng 1 (`monster_media_mode == "3D_CGI_VIDEO"`):
      Tìm kiếm video 3D CGI Unreal Engine 5 / Blender Megalophobia / VFX Movie Scene trên YouTube Shorts
      (VD: "{creature_name} {scale_anchor_object} 3d animation", "{creature_name} megalophobia cgi"),
      kiểm duyệt với `allow_3d_cgi_monster=True` và cắt đúng đoạn Peak hoành tráng nhất.
    - Tầng 2 (`monster_media_mode == "CONCEPT_ART_8K"` hoặc khi Tầng 1 không có clip):
      Truy xuất bộ 3-4 bức Đại cảnh Concept Art 8K trên ArtStation / Wallhaven / Movie Stills có vật đối chiếu tỷ lệ
      (`scale_anchor_object`) với `creature_mode=True` để dựng 3D Multi-Cut Parallax.
    """
    from concurrent.futures import ThreadPoolExecutor
    from app.services.media.image_quality import creature_queries
    from app.services.media.media_history import extract_media_identity, is_media_already_used, record_used_media
    from app.services.video_memory_rag import get_video_memory_rag

    rag = get_video_memory_rag()
    used = used_urls if used_urls is not None else set()
    fallback_creature = _clean_yt_search_phrase(topic, max_words=3, strip_distractor_props=True) or "colossal sea monster"
    c_name = _sanitize_tool_arg(creature_name, fallback=fallback_creature)
    s_anchor = _sanitize_tool_arg(scale_anchor_object, fallback="submarine")
    action_or_concept_query = _sanitize_tool_arg(action_or_concept_query, fallback="")
    mode_norm = (monster_media_mode or "3D_CGI_VIDEO").strip().upper()

    # 1. TẦNG 1: Săn Video 3D CGI / Unreal Engine 5 / Megalophobia Shorts Peak
    if mode_norm == "3D_CGI_VIDEO" or is_hook_scene:
        yt_monster_q = (
            action_or_concept_query
            if action_or_concept_query and any(k in action_or_concept_query.lower() for k in ("3d", "cgi", "animation", "megalophobia"))
            else f"{c_name} {s_anchor} 3d animation"
        )
        yt_fallback_q = f"{c_name} megalophobia cgi"
        vid_out = (
            output_path
            if output_path.lower().endswith(".mp4")
            else str(Path(output_path).parent / f"scene_{scene_id:03d}_monster_cgi.mp4")
        )
        yt_res = execute_youtube_short_peak_clone_sync(
            search_query=yt_monster_q,
            output_path=vid_out,
            target_duration_sec=target_duration_sec,
            clip_role="HOOK" if is_hook_scene else "PEAK",
            used_urls=used,
            fallback_query=yt_fallback_q,
            subject_entity=c_name,
            narration=narration,
            topic=topic,
            search_scope=search_scope,
            overall_script_query=overall_script_query or f"{c_name} {s_anchor}",
            max_peak_duration_sec=14.0,
            job_id=job_id,
            scene_id=scene_id,
            channel_id=channel_id,
            allow_3d_cgi_monster=True,
        )
        if yt_res and yt_res.get("media_path") and Path(yt_res["media_path"]).exists():
            logger.info(
                f"🐉 [MonsterTool 3D CGI Video] Scene #{scene_id}: Đã triệu hồi clip 3D CGI Peak cho '{c_name}' "
                f"(anchor='{s_anchor}'): {yt_res.get('title', '')[:50]}"
            )
            return yt_res

    # 2. TẦNG 2: Săn bộ 3-4 bức Đại cảnh Concept Art 8K (ArtStation / Wallhaven / Movie VFX Stills) có vật đối chiếu tỷ lệ
    c_qs = creature_queries(
        subject=c_name,
        topic=topic,
        narration=narration,
        scale_anchor=s_anchor,
        action_query=action_or_concept_query,
    )
    img_out = (
        output_path
        if output_path.lower().endswith((".jpg", ".jpeg", ".png"))
        else str(Path(output_path).parent / f"scene_{scene_id:03d}_monster_8k.jpg")
    )
    va_monster = {
        "subject": c_name,
        "creature_mode": True,
        "creature_queries": c_qs,
        "google_image_queries": c_qs[:4],
        "search_queries": c_qs[:3],
        "is_hook_scene": is_hook_scene,
        "preferred_engines": ["google"],
    }
    media_agent = MediaAgent()
    best_photo = media_agent.search_best_photo(
        query=c_qs[0] if c_qs else f"giant {c_name} {s_anchor} concept art 8k",
        used_urls=used,
        visual_analysis=va_monster,
        narration=f"Colossal {c_name} towering over {s_anchor}. {narration}",
        topic=overall_script_query or topic,
    )
    if not best_photo or not best_photo.get("url"):
        return None

    all_candidate_urls: List[str] = []
    for u_cand in [best_photo.get("url"), *(best_photo.get("alternate_urls") or [])]:
        if u_cand and u_cand not in all_candidate_urls:
            all_candidate_urls.append(u_cand)

    if target_image_count >= 2 and len(all_candidate_urls) < max(10, target_image_count * 3):
        try:
            from app.services.media.web_image_fetcher import WebImageFetcher
            extra_web = WebImageFetcher().search_all(
                query=c_qs[1] if len(c_qs) > 1 else f"giant {c_name} movie scene cgi",
                per_source=8,
                subject=c_name,
                extra_queries=c_qs[2:4],
                creature_mode=True,
            )
            for item_ex in (extra_web or []):
                u_ex = item_ex.get("url")
                if u_ex and u_ex not in all_candidate_urls and not is_media_already_used(u_ex, used):
                    all_candidate_urls.append(u_ex)
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
        query=c_qs[0] if c_qs else c_name,
        job_id=job_id,
        source=best_photo.get("source", "monster_concept_art_8k"),
        media_path=img_out,
    )

    pose_images: List[str] = [img_out]
    desired_extra = max(0, int(target_image_count) - 1)
    if desired_extra > 0 and remaining_urls:
        out_p = Path(img_out)
        stem = out_p.stem
        parent = out_p.parent
        cand_slice = remaining_urls[: max(desired_extra + 6, 8)]

        def _dl_m_cand(pair: Tuple[int, str]) -> Optional[Tuple[str, str]]:
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
            dl_results = list(pool.map(_dl_m_cand, enumerate(cand_slice)))

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
                    context_text=f"{c_qs[0] if c_qs else c_name} {topic} {narration}",
                ):
                    pose_images.append(final_cut_p)
                    used.update(extract_media_identity(c_url))
                    record_used_media(
                        url=c_url,
                        query=c_qs[0] if c_qs else c_name,
                        job_id=job_id,
                        source="monster_concept_art_8k",
                        media_path=final_cut_p,
                    )
                else:
                    Path(final_cut_p).unlink(missing_ok=True)
            else:
                Path(tmp_p).unlink(missing_ok=True)
                unused_after_multi.append(c_url)
        remaining_urls = unused_after_multi + remaining_urls[len(cand_slice):]

    logger.info(
        f"🐉 [MonsterTool ConceptArt 8K] Scene #{scene_id}: Đã thu thập {len(pose_images)} ảnh đại cảnh '{c_name}' "
        f"(anchor='{s_anchor}')!"
    )
    return {
        "media_path": img_out,
        "media_type": "image",
        "pose_images": pose_images,
        "source_url": primary_selected_url,
        "title": best_photo.get("title", ""),
        "curation_score": best_photo.get("curation_score", 9.2),
        "alternate_urls": remaining_urls,
        "search_scope": search_scope,
        "creature_mode": True,
    }
