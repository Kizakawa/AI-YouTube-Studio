"""
app.graph.nodes.media_nodes — Node tìm kiếm, thẩm định độ sắc nét & sửa chữa media trong LangGraph.

Bao gồm:
- node_fetch_media: Thu thập tư liệu đa nguồn cho từng cảnh.
- node_evaluate_media: Kiểm tra KỸ THUẬT & ĐỘ SẮC NÉT file (VisualCritic).
- node_refine_media: Thay thế file mờ nhòe / lỗi kỹ thuật.
"""
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


async def node_direct_visual_strategy(state: VideoWorkflowState) -> Dict[str, Any]:
    """
    Node LLM Đánh Giá & Quyết Định Chiến Lược Tìm Kiếm (`visual_director`):
    1. Đọc toàn bộ kịch bản và từng câu văn đã chia nhỏ (`scenes`).
    2. Dùng LLM Tool-Calling (`plan_media_tools_with_llm_sync`) + LLM Thẩm Định Phạm Vi (`audit_and_decide_search_scopes_with_llm_sync`)
       để đánh giá từng câu văn nhỏ:
       - Nếu câu văn chia nhỏ ra VẪN CÓ NGHĨA ĐỘC LẬP (`SCENE_SPECIFIC`) -> Duyệt đi search theo ý câu nhỏ đó.
       - Nếu câu văn chia nhỏ ra BỊ VÔ NGHĨA / CỤT NGỦN / LỆCH CHỦ ĐỀ (`SCRIPT_OVERALL`) -> Quyết định đi search
         nội dung xoay quanh TỔNG THỂ KỊCH BẢN (`overall_script_query`).
    """
    topic = state.get("topic", "")
    script_data = state.get("script_data", {})
    primary_entity = state.get("primary_entity") or script_data.get("primary_entity")
    channel_style = state.get("channel_style", "cinematic")
    scenes = script_data.get("scenes", [])
    video_format = str(
        state.get("video_format")
        or (script_data or {}).get("video_format")
        or ("long" if len(scenes) >= 14 else "short")
    ).strip().lower()
    is_long_form = video_format == "long"

    from app.graph.tools.media_tools import (
        plan_media_tools_with_llm_sync,
        audit_and_decide_search_scopes_with_llm_sync,
    )

    existing_plan = state.get("visual_plan")
    if existing_plan and existing_plan.get("scenes_strategy"):
        strat_list = existing_plan.get("scenes_strategy", [])
        if not existing_plan.get("scope_audited") and not all(isinstance(d, dict) and d.get("scope_audited") for d in strat_list):
            strat_list = await asyncio.to_thread(
                audit_and_decide_search_scopes_with_llm_sync,
                topic=topic,
                scenes=scenes,
                proposed_decisions=strat_list,
                primary_entity=primary_entity,
                channel_niche=channel_style,
            )
            existing_plan = {
                **existing_plan,
                "scenes_strategy": strat_list,
                "scope_audited": True,
            }
        summary = ", ".join([
            f"S{s.get('scene_id')}:{s.get('search_scope', 'SCENE_SPECIFIC')}[{s.get('tool_name', '')}]"
            for s in strat_list[:6]
        ])
        logger.info(f"⚖️ [VisualDirector Node] LLM đã đánh giá & chốt quyết định Search Scope: {summary}...")
        return {
            "visual_plan": existing_plan,
            "current_step": "VISUAL_STRATEGY_PLANNED",
            "progress_percent": 55,
            "progress_message": f"⚖️ Node LLM đã đánh giá & quyết định phạm vi tìm kiếm cho {len(strat_list)} cảnh",
        }

    tool_decisions = await asyncio.to_thread(
        plan_media_tools_with_llm_sync,
        topic=topic,
        scenes=scenes,
        primary_entity=primary_entity,
        channel_niche=channel_style,
        is_long_form=is_long_form,
    )
    if tool_decisions and all(isinstance(d, dict) and d.get("scope_audited") for d in tool_decisions):
        audited_decisions = tool_decisions
    else:
        audited_decisions = await asyncio.to_thread(
            audit_and_decide_search_scopes_with_llm_sync,
            topic=topic,
            scenes=scenes,
            proposed_decisions=tool_decisions,
            primary_entity=primary_entity,
            channel_niche=channel_style,
        )
    plan = {
        "topic_classification": "HYBRID_TOOL_CALLING",
        "director_vision": "Adaptive Dual-Scope Search (SCENE_SPECIFIC vs SCRIPT_OVERALL) + YouTube Peak & Google Images",
        "scenes_strategy": audited_decisions,
        "scope_audited": True,
    }

    proposals_summary = ", ".join([
        f"S{s.get('scene_id')}:{s.get('search_scope')}[{s.get('tool_name')}]"
        for s in audited_decisions[:6]
    ])
    logger.info(f"🎬 [VisualDirector Node] LLM đã đánh giá & chọn công cụ: {proposals_summary}...")

    return {
        "visual_plan": plan,
        "current_step": "VISUAL_STRATEGY_PLANNED",
        "progress_percent": 55,
        "progress_message": f"🎬 Node LLM đã đánh giá & chọn công cụ ({len(audited_decisions)} cảnh)",
    }


def _fetch_longform_16x9_broll_clip_sync(
    scene_id: int,
    query: str,
    primary_entity: str,
    topic: str,
    narration: str,
    target_duration_sec: float,
    media_dir: Path,
    used_bvids: set,
    used_urls: set,
    bvid_lock,
    media_agent: MediaAgent,
    is_scifi: bool = False,
    job_id: int = 0,
    output_override_path: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
    deadline_ts: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Săn B-Roll 16:9 Widescreen (1920x1080) chuyên biệt cho Video Dài (Long-Form Documentary):
    0. Ưu tiên số 1: Bộ máy Chapter-Pool & Multi-Segment Local Cut (`longform_chapter_pool.py`) —
       Chỉ tải 1 video nguồn YouTube/Bilibili 16:9 cho mỗi Cụm Chương (4 cảnh/Chương) rồi cắt trực tiếp trên ổ cứng
       kèm Internal Jump-Cut + Dynamic Documentary Zoom (`102% Wide -> 110% Close-up`) chống Content ID 100%.
    1. Dự phòng / Đan xen: Pexels / Pixabay 16:9 Landscape HD Video chạy SONG SONG KHÔNG KHÓA (lock-free Gemini Vision
       kèm khóa nguyên tử <0.05ms khi claim URL) và hủy hợp tác (`cancel_event` / `deadline_ts`) chống treo luồng zombie.
    """
    from app.utils.ffmpeg_helper import get_duration_ms, is_completed_clean_mp4

    def _is_cancelled() -> bool:
        return (cancel_event is not None and cancel_event.is_set()) or (
            deadline_ts is not None and (deadline_ts - time.monotonic()) <= 2.0
        )

    if _is_cancelled():
        return None

    out_path = output_override_path or str(media_dir / f"scene_{scene_id:03d}_16x9.mp4")
    if not output_override_path and Path(out_path).exists():
        if is_completed_clean_mp4(out_path, min_size_bytes=40_000, min_duration_ms=1200):
            return {
                "media_path": out_path,
                "media_type": "video",
                "production_method": "CHAPTER_POOL_16X9_CACHED",
                "source_url": out_path,
                "rationale": f"16:9 Cached Documentary Clip ({Path(out_path).name})",
            }
        else:
            Path(out_path).unlink(missing_ok=True)

    slot_in_4 = (int(scene_id) - 1) % 4
    chapter_num = ((int(scene_id) - 1) // 4) + 1

    # Khi gọi từ node_refine_media / rescue (có output_override_path), chỉ xóa cache refine tạm của cảnh này
    # (TUYỆT ĐỐI KHÔNG xóa scene_XXX_16x9.mp4 đang dùng hoặc master dùng chung của Chương!)
    if output_override_path:
        try:
            (media_dir / f"scene_{scene_id:03d}_refine_16x9.mp4").unlink(missing_ok=True)
            (media_dir / "chapter_pool" / f"refine_{scene_id:02d}_master.mp4").unlink(missing_ok=True)
            (media_dir / "chapter_pool" / f"refine_{scene_id:02d}_failed.txt").unlink(missing_ok=True)
        except Exception:
            pass

    def _atomic_claim_16x9_stock_url(vid_info: Optional[Dict[str, Any]]) -> str:
        """Khóa nguyên tử <0.05ms để claim URL thắng cuộc (hoặc ứng viên dự phòng Vision đã duyệt) mà không giữ lock khi gọi Gemini Vision."""
        if not vid_info or not vid_info.get("url"):
            return ""
        from app.services.media.media_history import (
            extract_media_identity,
            is_media_already_used,
            record_used_media,
        )
        cand_list = [str(vid_info["url"]), *[str(u) for u in (vid_info.get("alternate_urls") or []) if u]]
        claimed = ""
        web_u = str(vid_info.get("web_url") or "")
        with bvid_lock:
            for c_u in cand_list:
                if c_u and not is_media_already_used(c_u, used_urls):
                    ids = extract_media_identity(c_u)
                    used_urls.update(ids)
                    used_urls.add(c_u)
                    if c_u == str(vid_info["url"]) and web_u:
                        used_urls.add(web_u)
                    claimed = c_u
                    break
        if claimed:
            try:
                record_used_media(
                    claimed,
                    query=str(vid_info.get("query_used") or query or topic or "16x9_broll")[:120],
                    source=str(vid_info.get("source") or "pexels_16x9_video"),
                )
            except Exception:
                pass
        return claimed

    def _try_stock_16x9_landscape(prefer_pixabay: bool = False, sub_deadline_ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
        eff_dl = sub_deadline_ts if sub_deadline_ts is not None else deadline_ts
        if _is_cancelled() or (eff_dl is not None and (eff_dl - time.monotonic()) <= 2.0):
            return None
        try:
            from app.services.media.longform_chapter_pool import (
                extract_longform_scene_with_internal_jumpcut,
                is_clip_visually_unique_in_job,
                stitch_two_16x9_clips_no_loop,
                _is_clip_window_clean,
            )
            q_parts = [p.strip() for p in str(query or "").split("|||") if p.strip()]
            if not q_parts:
                q_parts = [query or primary_entity or topic]
            from app.graph.tools.tool_schemas import build_single_entity_stock_queries
            narr_clean = clean_scene_search_query(narration or "", max_words=4)
            if narr_clean and narr_clean not in q_parts:
                q_parts.append(narr_clean)
            entity_stock_fallbacks = build_single_entity_stock_queries(
                raw_queries=q_parts + [primary_entity or "", topic or ""],
                subject_entity=primary_entity or topic or "",
                topic=topic or "",
                max_words=3,
            )
            # Đẩy các truy vấn đơn thực thể ('Jaguar', 'Black Caiman') lên ngay sau truy vấn chính của cảnh
            # để Pexels/Pixabay tìm thấy B-Roll động vật/nhân vật thật ngay ở vòng đầu!
            prioritized_q_parts: List[str] = []
            for p_head in q_parts[:2]:
                if p_head and p_head not in prioritized_q_parts:
                    prioritized_q_parts.append(p_head)
            for ef_q in entity_stock_fallbacks:
                if ef_q and ef_q not in prioritized_q_parts:
                    prioritized_q_parts.append(ef_q)
            for p_tail in q_parts[2:]:
                if p_tail and p_tail not in prioritized_q_parts:
                    prioritized_q_parts.append(p_tail)

            for try_idx in range(2):
                if _is_cancelled():
                    return None
                with bvid_lock:
                    snap_used = set(used_urls)
                best_vid = media_agent._stock.search_landscape_16x9_video_clip(
                    queries=prioritized_q_parts[try_idx:] + prioritized_q_parts[:try_idx],
                    used_urls=snap_used,
                    topic=topic,
                    narration=narration,
                    prefer_pixabay=(prefer_pixabay ^ (try_idx == 1)),
                    primary_entity=primary_entity,
                    cancel_event=cancel_event,
                    deadline_ts=eff_dl,
                    claim_in_used=False,
                )
                cand_u = _atomic_claim_16x9_stock_url(best_vid)
                if best_vid and cand_u and not _is_cancelled():
                    src_tag = str(best_vid.get("source") or "pexels_16x9_video").lower()
                    prod_lbl = "PIXABAY_16X9_LANDSCAPE_HD" if "pixabay" in src_tag else "PEXELS_16X9_LANDSCAPE_HD"
                    raw_stock = str(media_dir / f"scene_{scene_id:03d}_stock16x9_raw.mp4")
                    dl_ok = media_agent.download_file(cand_u, raw_stock)
                    if dl_ok and not _is_cancelled() and is_completed_clean_mp4(raw_stock, min_size_bytes=40_000, min_duration_ms=1500, allow_raw_source=True):
                        raw_dur_s = get_duration_ms(raw_stock) / 1000.0
                        req_dur_s = max(2.5, float(target_duration_sec))
                        cut_ok = False
                        # Chống lặp cảnh nội bộ (-stream_loop -1): Nếu clip Stock ngắn hơn thời lượng lời thoại >= 0.8s (VD: clip 7s cho cảnh 15s),
                        # tự động tải thêm 1 clip Stock thứ 2 khác hoàn toàn và nối mượt 2 cú máy (Shot A + Shot B)!
                        if req_dur_s >= 8.5 and raw_dur_s < (req_dur_s - 0.8) and not _is_cancelled():
                            with bvid_lock:
                                snap_used_b = set(used_urls)
                            second_vid = media_agent._stock.search_landscape_16x9_video_clip(
                                queries=prioritized_q_parts[1:] + prioritized_q_parts[:1],
                                used_urls=snap_used_b,
                                topic=topic,
                                narration=narration,
                                prefer_pixabay=not (prefer_pixabay ^ (try_idx == 1)),
                                primary_entity=primary_entity,
                                cancel_event=cancel_event,
                                deadline_ts=eff_dl,
                                claim_in_used=False,
                            )
                            cand_u_b = _atomic_claim_16x9_stock_url(second_vid)
                            if second_vid and cand_u_b and not _is_cancelled():
                                raw_stock_b = str(media_dir / f"scene_{scene_id:03d}_stock16x9_raw_b.mp4")
                                if (
                                    media_agent.download_file(cand_u_b, raw_stock_b)
                                    and not _is_cancelled()
                                    and is_completed_clean_mp4(raw_stock_b, min_size_bytes=40_000, min_duration_ms=1500, allow_raw_source=True)
                                ):
                                    cut_ok = stitch_two_16x9_clips_no_loop(
                                        clip_a=raw_stock,
                                        clip_b=raw_stock_b,
                                        output_path=out_path,
                                        target_duration_sec=req_dur_s,
                                        scene_id=scene_id,
                                    )
                                    if cut_ok:
                                        prod_lbl = f"{prod_lbl}_MULTI_SHOT"
                                Path(raw_stock_b).unlink(missing_ok=True)
                        if not cut_ok and not _is_cancelled():
                            cut_ok = extract_longform_scene_with_internal_jumpcut(
                                master_path=raw_stock,
                                output_path=out_path,
                                start_offset_sec=0.0,
                                target_duration_sec=req_dur_s,
                                scene_id=scene_id,
                            )
                        if not cut_ok and not _is_cancelled() and get_duration_ms(raw_stock) >= 2500:
                            try:
                                trim_video(
                                    input_path=raw_stock,
                                    duration_seconds=req_dur_s,
                                    output_path=out_path,
                                    width=1920,
                                    height=1080,
                                    fps=30,
                                    apply_grading=True,
                                    is_hook_scene=(scene_id == 1),
                                    fair_use_transform=True,
                                    preserve_continuous_flow=True,
                                )
                            except Exception:
                                pass
                            Path(raw_stock).unlink(missing_ok=True)
                        else:
                            Path(raw_stock).unlink(missing_ok=True)
                        if (
                            not _is_cancelled()
                            and is_completed_clean_mp4(out_path, min_size_bytes=35_000, min_duration_ms=1200)
                            and _is_clip_window_clean(out_path, [0.8, 2.2])
                        ):
                            ok_s1_stock_sh = True
                            if scene_id == 1:
                                try:
                                    from app.graph.nodes.hook_video_node import _ensure_hook_clip_sharpness_and_luma
                                    ok_s1_stock_sh, _ = _ensure_hook_clip_sharpness_and_luma(out_path, min_sharpness=13.2)
                                except Exception:
                                    pass
                            if ok_s1_stock_sh and is_clip_visually_unique_in_job(out_path, job_id, scene_id=scene_id):
                                logger.info(f"Scene {scene_id}: 🎥 [Long-Form {prod_lbl}] Đã tải & cắt Landscape B-Roll -> {Path(out_path).name}")
                                return {
                                    "media_path": out_path,
                                    "media_type": "video",
                                    "production_method": prod_lbl,
                                    "source_url": cand_u,
                                    "rationale": f"{prod_lbl} ({str(best_vid.get('title') or query)[:45]})",
                                }
                        Path(out_path).unlink(missing_ok=True)
                    Path(raw_stock).unlink(missing_ok=True)
        except Exception as e_stock_16x9:
            logger.warning(f"Scene {scene_id}: Long-form Stock 16:9 lookup error: {e_stock_16x9}")
        return None

    def _try_chapter_pool_16x9(sub_deadline_ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
        eff_dl = sub_deadline_ts if sub_deadline_ts is not None else deadline_ts
        if _is_cancelled() or (eff_dl is not None and (eff_dl - time.monotonic()) <= 2.2):
            return None
        try:
            from app.services.media.longform_chapter_pool import fetch_or_slice_longform_chapter_clip_sync
            pool_res = fetch_or_slice_longform_chapter_clip_sync(
                job_id=job_id,
                scene_id=scene_id,
                query=query,
                primary_entity=primary_entity,
                topic=topic,
                narration=narration,
                target_duration_sec=target_duration_sec,
                media_dir=media_dir,
                used_bvids=used_bvids,
                used_urls=used_urls,
                bvid_lock=bvid_lock,
                media_agent=media_agent,
                is_scifi=is_scifi,
                scenes_per_chapter=(1 if output_override_path else 3),
                cancel_event=cancel_event,
                deadline_ts=eff_dl,
                chapter_key_prefix=("refine" if output_override_path else "chapter"),
            )
            if pool_res and pool_res.get("media_path") and not _is_cancelled():
                if output_override_path and str(Path(pool_res["media_path"]).resolve()) != str(Path(output_override_path).resolve()):
                    import shutil as _sh_cp
                    _sh_cp.copy2(pool_res["media_path"], output_override_path)
                    pool_res["media_path"] = output_override_path
                if scene_id == 1 and Path(pool_res["media_path"]).exists():
                    try:
                        from app.graph.nodes.hook_video_node import _ensure_hook_clip_sharpness_and_luma
                        ok_cp_s1, _ = _ensure_hook_clip_sharpness_and_luma(pool_res["media_path"], min_sharpness=13.2)
                        if not ok_cp_s1:
                            Path(pool_res["media_path"]).unlink(missing_ok=True)
                            return None
                    except Exception:
                        pass
                return pool_res
        except Exception as e_pool_16x9:
            logger.warning(f"Scene {scene_id}: Long-form ChapterPool 16:9 error: {e_pool_16x9}")
        return None

    # Điều phối Đa Nguồn (Multi-Source Interleaving) trong mỗi Chương:
    # - Slot 0 & 2: Ưu tiên YouTube 16:9 Documentary Chapter Master -> dự phòng Pexels/Pixabay 16:9
    # - Slot 1 & 3: Ưu tiên Pexels 16:9 / Pixabay 16:9 HD -> dự phòng YouTube Chapter Pool
    prefer_stock_first = (slot_in_4 in (1, 3))
    prefer_px = (slot_in_4 == 3) or (chapter_num % 2 == 0)

    now_ts = time.monotonic()
    chapter_num_3 = ((int(scene_id) - 1) // 3) + 1
    ch_pool_dir = media_dir / "chapter_pool"
    existing_ch3_master = ch_pool_dir / f"chapter_{chapter_num_3:02d}_master.mp4"
    has_local_ch3_master = (
        existing_ch3_master.exists()
        and existing_ch3_master.with_suffix(".meta").exists()
        and _safe_size(existing_ch3_master) > 80_000
    )

    def _try_cross_slice_existing_masters() -> Optional[Dict[str, Any]]:
        if _is_cancelled():
            return None
        try:
            from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
            cs_res = slice_any_available_chapter_master_for_scene(
                media_dir=media_dir,
                scene_id=scene_id,
                target_duration_sec=target_duration_sec,
                job_id=job_id,
                output_override_path=output_override_path,
                max_master_uses_override=5,
            )
            if cs_res and cs_res.get("media_path") and Path(cs_res["media_path"]).exists():
                if scene_id == 1:
                    try:
                        from app.graph.nodes.hook_video_node import _ensure_hook_clip_sharpness_and_luma
                        ok_cs1, _ = _ensure_hook_clip_sharpness_and_luma(cs_res["media_path"], min_sharpness=13.2)
                        if not ok_cs1:
                            Path(cs_res["media_path"]).unlink(missing_ok=True)
                            return None
                    except Exception:
                        pass
                return cs_res
        except Exception as _e_cs:
            logger.debug(f"Scene {scene_id}: Cross-slice existing masters skip: {_e_cs}")
        return None

    # Trường hợp 1: Chương này ĐÃ có sẵn Master 16:9 sạch tải ở Phase A trên SSD (~1.2s cắt local!)
    if not output_override_path and has_local_ch3_master:
        if slot_in_4 == 1:
            st_first_dl = min(deadline_ts - 16.0, now_ts + 22.0) if (deadline_ts is not None and (deadline_ts - now_ts) > 24.0) else deadline_ts
            st_res = _try_stock_16x9_landscape(prefer_pixabay=prefer_px, sub_deadline_ts=st_first_dl)
            if st_res:
                return st_res
            return _try_chapter_pool_16x9(sub_deadline_ts=deadline_ts) or _try_cross_slice_existing_masters()
        cp_res = _try_chapter_pool_16x9(sub_deadline_ts=deadline_ts)
        if cp_res:
            return cp_res
        st_res = _try_stock_16x9_landscape(prefer_pixabay=prefer_px, sub_deadline_ts=deadline_ts)
        return st_res or _try_cross_slice_existing_masters()

    # Trường hợp 2: Chương chưa có Master sau Phase A (hoặc đang chạy refine):
    # KHÔNG tải lại yt-dlp 60-90s trên từng cảnh ở Phase B gây nghẽn 40 cảnh!
    # Ưu tiên Pexels/Pixabay 16:9 HD (~10s) -> Cắt chéo lát độc lập từ kho Chapter Master sẵn có (~1.2s)!
    st_dl = min(deadline_ts - 14.0, now_ts + 26.0) if (deadline_ts is not None and (deadline_ts - now_ts) > 22.0) else deadline_ts
    st_res = _try_stock_16x9_landscape(prefer_pixabay=prefer_px, sub_deadline_ts=st_dl)
    if st_res:
        return st_res
    cs_res = _try_cross_slice_existing_masters()
    if cs_res:
        return cs_res
    total_pool_masters = len(list(ch_pool_dir.glob("chapter_*_master.mp4"))) if ch_pool_dir.exists() else 0
    if bool(output_override_path) or total_pool_masters < 2:
        return _try_chapter_pool_16x9(sub_deadline_ts=deadline_ts)
    return None


async def node_fetch_media(state: VideoWorkflowState) -> Dict[str, Any]:
    """
    Node thực thi SONG SONG toàn bộ các lệnh `@tool` do LLM Đạo diễn chỉ định cho từng cảnh:
      1. `clone_youtube_short_peak_clip` : Clone YouTube Shorts viral & cắt đúng đoạn Peak (Most Replayed / Action Peak) cho Hook & 2-3 cảnh cao trào.
      2. `search_google_cinematic_image` : Tìm ảnh Top Search Google Images sắc nét theo từ khóa đã được LLM cụ thể hóa.
    Tất cả các phân cảnh được chạy đồng loạt qua `asyncio.gather` (Semaphore 8) để đạt tốc độ tối đa.
    """
    script_data = state.get("script_data", {})
    primary_entity = state.get("primary_entity") or script_data.get("primary_entity")
    entity_type = state.get("entity_type", "person")
    entity_media_pool = state.get("entity_media_pool", [])
    work_dir = Path(state.get("work_dir", "storage/cache"))
    media_dir = work_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    current_niche = state.get("niche") or state.get("channel_style") or state.get("channel_niche") or "general"
    is_scifi_val = (current_niche == "scifi") or any(k in state.get("topic", "").lower() for k in ("scifi", "sci-fi", "alien", "cyborg", "viễn tưởng"))

    script = ScriptContract(**script_data)
    video_format_val = str(
        state.get("video_format")
        or (script_data or {}).get("video_format")
        or getattr(script, "video_format", "short")
        or "short"
    ).strip().lower()
    is_long_form = (video_format_val == "long") or (len(script.scenes) >= 14)
    import threading as _threading
    longform_bvid_lock = _threading.Lock()
    longform_used_bvids: set = set()

    media_agent = MediaAgent()
    image_agent = ImageAgent()
    ai_video_agent = AIVideoAgent()
    curator = VisualCurationAgent()

    # Lấy bản kế hoạch Tool từ state (hoặc khởi tạo nhanh nếu chưa có)
    visual_plan = state.get("visual_plan")
    if not visual_plan or not visual_plan.get("scenes_strategy"):
        from app.graph.tools.media_tools import plan_media_tools_with_llm_sync
        tool_decisions = await asyncio.to_thread(
            plan_media_tools_with_llm_sync,
            topic=state.get("topic", ""),
            scenes=[s.model_dump() for s in script.scenes],
            primary_entity=primary_entity,
            channel_niche=state.get("channel_style", "cinematic"),
        )
        visual_plan = {
            "topic_classification": "HYBRID_TOOL_CALLING",
            "scenes_strategy": tool_decisions,
        }

    from app.services.video_memory_rag import get_video_memory_rag
    rag = get_video_memory_rag()
    job_id = int(state.get("job_id") or 0)
    if job_id:
        try:
            from app.services.media.longform_chapter_pool import reset_longform_job_dedup
            reset_longform_job_dedup(job_id)
        except Exception:
            pass
    channel_id_val = str(state.get("target_channel_id") or state.get("channel_id") or "")

    raw_strategies = list(visual_plan.get("scenes_strategy") or [])
    if raw_strategies:
        try:
            raw_strategies = rag.diversify_intra_video_scene_queries(
                job_id=job_id,
                scenes_strategy=raw_strategies,
                topic=state.get("topic", ""),
                primary_entity=primary_entity or "",
                channel_id=channel_id_val,
                scenes_raw=[s.model_dump() for s in script.scenes],
            )
        except Exception as e_div:
            logger.debug(f"[MediaNodes] RAG query diversification skip: {e_div}")

    strategy_map = {
        int(s.get("scene_id") or 0): s
        for s in raw_strategies
        if isinstance(s, dict) and s.get("scene_id")
    }

    scenes_media = []
    from app.services.media.media_history import get_recent_media_urls, record_used_media
    used_pool_urls = get_recent_media_urls(limit=150)

    # Điều phối Semaphore 5 luồng sạch cho Video Dài (khớp dưới ngưỡng _VISION_SEM=7, triệt tiêu 100% nghẽn hàng đợi Vision)
    sem = asyncio.Semaphore(5 if is_long_form else 8)
    completed_lock = asyncio.Lock()
    completed_count = 0
    total_scenes = len(script.scenes)

    from app.graph.runner import report_job_progress
    if job_id:
        await report_job_progress(
            job_id,
            "MEDIA_FETCHING",
            f"🎬 Đang chạy song song bộ Tools thu thập Video Peak & Ảnh Google ({total_scenes} cảnh)...",
            56
        )

    is_hybrid_tool_mode = visual_plan.get("topic_classification") in ("HYBRID_TOOL_CALLING", "LONGFORM_16X9_HYBRID")
    batch_analyses: Dict[int, Dict[str, Any]] = {}
    if not is_hybrid_tool_mode:
        try:
            batch_analyses = await asyncio.to_thread(
                curator.analyze_scenes_batch,
                scenes=[s.model_dump() for s in script.scenes],
                topic=state.get("topic", ""),
                primary_entity=primary_entity,
                entity_type=entity_type,
                channel_niche=state.get("channel_style", "cinematic"),
            )
        except Exception as e_batch:
            logger.debug(f"[MediaNodes] analyze_scenes_batch fallback: {e_batch}")

    pre_hook_map = state.get("pre_resolved_hook_media") or {}
    for _ph_key, _ph_val in pre_hook_map.items():
        if isinstance(_ph_val, dict):
            _ph_sid = int(_ph_key) if str(_ph_key).isdigit() else 1
            _ph_yt = str(_ph_val.get("youtube_id") or "").strip()
            _ph_src = str(_ph_val.get("source_url") or "").strip()
            _ph_mp = str(_ph_val.get("media_path") or "").strip()
            _ph_fp = str(_ph_val.get("full_peak_path") or "").strip()
            if _ph_yt or _ph_src:
                if _ph_yt:
                    used_pool_urls.add(_ph_yt)
                    used_pool_urls.add(f"https://www.youtube.com/watch?v={_ph_yt}")
                rag.try_claim_in_job_video(
                    job_id=job_id,
                    vid_id=_ph_yt or _ph_src,
                    url=_ph_src or (f"https://www.youtube.com/watch?v={_ph_yt}" if _ph_yt else _ph_mp),
                    title="Pre-Resolved Hook Scene 1",
                    scene_id=_ph_sid,
                )
            if _ph_mp and Path(_ph_mp).exists():
                try:
                    rag.verify_and_commit_in_job_clip(
                        job_id=job_id,
                        scene_id=_ph_sid,
                        media_path=_ph_mp,
                        vid_id=_ph_yt or _ph_src or _ph_mp,
                        url=_ph_src or _ph_mp,
                        title="Pre-Resolved Hook Scene 1",
                    )
                    from app.services.media.longform_chapter_pool import is_clip_visually_unique_in_job as _reg_hook_uniq
                    _reg_hook_uniq(_ph_mp, job_id, scene_id=_ph_sid)
                except Exception:
                    pass
            for _p_item in (_ph_src, _ph_mp, _ph_fp):
                if _p_item:
                    used_pool_urls.add(_p_item)

    async def _fetch_one_scene(
        idx: int,
        scene,
        cancel_event: Optional[threading.Event] = None,
        deadline_ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        def _check_fetch_deadline() -> None:
            if (cancel_event is not None and cancel_event.is_set()) or (
                deadline_ts is not None and (deadline_ts - time.monotonic()) <= 1.8
            ):
                raise asyncio.TimeoutError("Cooperative scene fetch deadline reached")

        # ── ƯU TIÊN TUYỆT ĐỐI CHO NODE HOOK VIDEO: Nếu Cảnh này đã được cắt sẵn Video thật ──
        pre_hook = pre_hook_map.get(scene.scene_id) or pre_hook_map.get(str(scene.scene_id))
        if (
            isinstance(pre_hook, dict)
            and pre_hook.get("media_path")
            and Path(pre_hook["media_path"]).exists()
            and "CLIMAX_IMAGES_LIVE_FX" not in str(pre_hook.get("production_method") or "").upper()
        ):
            return {
                "scene_id": scene.scene_id,
                "media_path": pre_hook["media_path"],
                "media_type": pre_hook.get("media_type", "hook_ready_video"),
                "pose_images": pre_hook.get("pose_images"),
                "vision_curated": True,
                "visual_keyword": scene.visual_keyword,
                "visual_prompt": getattr(scene, "visual_prompt", None),
                "narration": scene.narration,
                "camera_motion": getattr(scene, "camera_motion", None) or "hyper_zoom",
                "sfx_cue": getattr(scene, "sfx_cue", None) or ("whoosh" if idx == 0 else "hit"),
                "highlight_words": getattr(scene, "highlight_words", None),
                "overlay_badge": getattr(scene, "overlay_badge", None),
                "visual_strategy": pre_hook.get("visual_strategy", "HOOK_PEAK_SHORT_VIDEO"),
                "production_method": pre_hook.get("production_method", "CLIMAX_REAL_VIDEO_CONTINUOUS_PEAK"),
                "full_peak_path": pre_hook.get("full_peak_path"),
                "full_peak_duration_sec": pre_hook.get("full_peak_duration_sec", 0.0),
                "youtube_id": pre_hook.get("youtube_id"),
                "source_url": pre_hook.get("source_url"),
                "hook_verified": bool(pre_hook.get("hook_verified", False)),
                "hook_overall_score": pre_hook.get("hook_overall_score"),
                "hook_clean_score": pre_hook.get("hook_clean_score"),
                "hook_sharpness": pre_hook.get("hook_sharpness"),
                "tool": "hook_video_node",
                "strategy_rationale": pre_hook.get("strategy_rationale", "Peak segment extracted from online Short"),
            }

        media_path = ""
        media_type = "image"
        vision_curated = False
        pose_images: List[str] = []
        hook_alternate_urls: List[str] = []
        full_peak_path: Optional[str] = None
        full_peak_duration_sec: float = 0.0
        source_url_used: str = ""
        sq = getattr(scene, 'stock_query', None)
        vp = getattr(scene, 'visual_prompt', None)
        kw = scene.visual_keyword or ""

        sc_strat = strategy_map.get(scene.scene_id, {})
        tool_name = sc_strat.get("tool_name", "")

        # Thời lượng thực tế của phân cảnh (giây) từ Audio (đã chạy xong trước đó)
        dur = float(getattr(scene, 'estimated_seconds', 4.0) or 4.0)
        scenes_audio = state.get("scenes_audio", [])
        if scenes_audio:
            sc_audio = next((a for a in scenes_audio if a.get("scene_id") == scene.scene_id), None)
            if sc_audio and sc_audio.get("audio_duration_ms"):
                dur = max(float(sc_audio["audio_duration_ms"]) / 1000.0, 1.2)

        # ══════════════════════════════════════════════════════════════════════
        # TẦNG -1 (REUSE CACHE): NẾU ĐÃ CÓ FILE MEDIA HỢP LỆ TỪ STORYBOARD HOẶC LẦN CHẠY TRƯỚC
        # ══════════════════════════════════════════════════════════════════════
        if state.get("media_retry_count", 0) == 0 and state.get("content_retry_count", 0) == 0:
            from app.utils.ffmpeg_helper import is_completed_clean_mp4 as _is_clean_mp4_cache
            pre_m = getattr(scene, "media_path", None)
            if pre_m and Path(pre_m).exists() and Path(pre_m).stat().st_size > 3000:
                is_vid_pre = str(pre_m).lower().endswith((".mp4", ".mov", ".webm"))
                if (
                    is_vid_pre and _is_clean_mp4_cache(str(pre_m), min_size_bytes=15_000, min_duration_ms=1200)
                ) or (not is_vid_pre and scene.scene_id != 1):
                    media_path = str(Path(pre_m).resolve())
                    media_type = ("hook_ready_video" if scene.scene_id == 1 else "video") if is_vid_pre else "image"
                    vision_curated = True
                    prod_method = chosen_strategy = (
                        "DOODLE_2D_EXPLAINER_VIDEO"
                        if "doodle" in Path(media_path).name.lower()
                        else "CHAPTER_POOL_16X9_JUMPCUT_HD"
                        if "_16x9" in Path(media_path).name.lower()
                        else "CACHED_MEDIA"
                    )
                    logger.info(f"Scene {scene.scene_id}: ⚡ Tái sử dụng Media có sẵn: {Path(media_path).name}")
                elif is_vid_pre:
                    Path(pre_m).unlink(missing_ok=True)
            if not media_path:
                cached_files = sorted(
                    media_dir.glob(f"scene_{scene.scene_id:03d}_*"),
                    key=lambda p: (0 if "_cut" not in p.name else 1, p.name),
                )
                valid_cached_imgs: List[str] = []
                for cf in cached_files:
                    if cf.is_file() and cf.stat().st_size > 3000 and cf.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".mp4"):
                        if cf.suffix.lower() == ".mp4" and not media_path:
                            if any(tok in cf.name.lower() for tok in ("_fftmp_", ".tmp", "_tmp", ".stitch", ".vtrim", "_raw", ".part", "_fullpeak")):
                                continue
                            if _is_clean_mp4_cache(str(cf), min_size_bytes=15_000, min_duration_ms=1200):
                                media_path = str(cf.resolve())
                                media_type = "hook_ready_video" if scene.scene_id == 1 else "video"
                                vision_curated = True
                                prod_method = chosen_strategy = (
                                    "DOODLE_2D_EXPLAINER_VIDEO"
                                    if "doodle" in cf.name.lower()
                                    else "CHAPTER_POOL_16X9_JUMPCUT_HD"
                                    if "_16x9" in cf.name.lower()
                                    else "CACHED_MEDIA"
                                )
                                logger.info(f"Scene {scene.scene_id}: ⚡ Tái sử dụng Video đã tải trong cache: {cf.name}")
                                break
                            else:
                                logger.warning(f"Scene {scene.scene_id}: 🗑️ Xóa file video cache lỗi/thiếu moov atom: {cf.name}")
                                cf.unlink(missing_ok=True)
                        elif (
                            scene.scene_id != 1
                            and cf.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                            and "_cand" not in cf.name.lower()
                            and "_graphic.png" not in cf.name.lower()
                        ):
                            valid_cached_imgs.append(str(cf.resolve()))
                if not media_path and valid_cached_imgs and scene.scene_id != 1:
                    media_path = valid_cached_imgs[0]
                    media_type = "image"
                    pose_images = valid_cached_imgs
                    vision_curated = True
                    prod_method = chosen_strategy = "CACHED_MEDIA"
                    logger.info(
                        f"Scene {scene.scene_id}: ⚡ Tái sử dụng {len(valid_cached_imgs)} ảnh đã tải trong cache: {Path(media_path).name}"
                    )

        # ══════════════════════════════════════════════════════════════════════
        # NHÁNH ĐẶC QUYỀN VIDEO DÀI 16:9 (LONG-FORM WIDESCREEN DOCUMENTARY):
        # Kết hợp Chapter-Pool 16:9 Multi-Segment Jump-Cut + Hỗ trợ @tool Doodle 2D 16:9
        # ══════════════════════════════════════════════════════════════════════
        if not media_path and is_long_form and tool_name != "render_doodle_2d_explainer_scene":
            lf_parts = [
                p for p in (
                    sc_strat.get("youtube_search_query"),
                    sc_strat.get("action_or_concept_query"),
                    sq,
                    kw,
                    sc_strat.get("human_action_query"),
                    sc_strat.get("smart_image_query"),
                    state.get("topic", ""),
                )
                if p
            ]
            lf_q = "|||".join(dict.fromkeys(str(p).strip() for p in lf_parts if str(p).strip()))
            lf_res = await asyncio.to_thread(
                _fetch_longform_16x9_broll_clip_sync,
                scene_id=scene.scene_id,
                query=lf_q,
                primary_entity=primary_entity or state.get("topic", ""),
                topic=state.get("topic", ""),
                narration=scene.narration,
                target_duration_sec=dur,
                media_dir=media_dir,
                used_bvids=longform_used_bvids,
                used_urls=used_pool_urls,
                bvid_lock=longform_bvid_lock,
                media_agent=media_agent,
                is_scifi=is_scifi_val,
                job_id=job_id,
                cancel_event=cancel_event,
                deadline_ts=deadline_ts,
            )
            if not (lf_res and lf_res.get("media_path")):
                from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                lf_res = await asyncio.to_thread(
                    slice_any_available_chapter_master_for_scene,
                    media_dir=media_dir,
                    scene_id=scene.scene_id,
                    target_duration_sec=dur,
                    job_id=job_id,
                )
            if lf_res and lf_res.get("media_path"):
                media_path = lf_res["media_path"]
                media_type = "video"
                vision_curated = True
                prod_method = lf_res.get("production_method", "CHAPTER_POOL_16X9_JUMPCUT_HD")
                chosen_strategy = "LONGFORM_16X9_DOCUMENTARY_VIDEO"
                tool = "longform_chapter_pool_16x9"
                source_url_used = str(lf_res.get("source_url") or "")
                rationale = str(lf_res.get("rationale") or "16:9 Documentary Chapter-Pool Jump-Cut")
            else:
                _check_fetch_deadline()
                from app.graph.tools.media_tools import execute_google_cinematic_image_sync
                lf_img_q = (
                    sc_strat.get("smart_image_query")
                    or sq
                    or kw
                    or primary_entity
                    or state.get("topic", "")
                )
                out_lf_img = str(media_dir / f"scene_{scene.scene_id:03d}_lf16x9_gsearch.jpg")
                img_sub_ev = threading.Event()
                img_sub_dl = min(deadline_ts, time.monotonic() + 20.5) if deadline_ts is not None else (time.monotonic() + 20.5)
                from app.agents.curation.scene_context_analyzer_mixin import bind_scene_cancellation
                bind_scene_cancellation(img_sub_ev, img_sub_dl)
                try:
                    lf_img_res = await asyncio.wait_for(
                        asyncio.to_thread(
                            execute_google_cinematic_image_sync,
                            smart_query=lf_img_q,
                            output_path=out_lf_img,
                            narration=scene.narration,
                            topic=state.get("topic", ""),
                            used_urls=used_pool_urls,
                            is_hook_scene=(scene.scene_id == 1),
                            subject_entity=primary_entity or state.get("topic", ""),
                            search_scope="SCENE_SPECIFIC",
                            overall_script_query=primary_entity or state.get("topic", ""),
                            job_id=job_id,
                            scene_id=scene.scene_id,
                            target_image_count=1,
                        ),
                        timeout=22.0,
                    )
                except Exception:
                    img_sub_ev.set()
                    lf_img_res = None
                finally:
                    bind_scene_cancellation(cancel_event, deadline_ts)
                if lf_img_res and lf_img_res.get("media_path"):
                    media_path = lf_img_res["media_path"]
                    media_type = "image"
                    vision_curated = True
                    pose_images = list(lf_img_res.get("pose_images") or [media_path])
                    source_url_used = str(lf_img_res.get("source_url") or "")
                    prod_method = "LONGFORM_16X9_VISION_PHOTO_FALLBACK"
                    chosen_strategy = "GEMINI_SEARCH_3D"
                    tool = "search_google_cinematic_image"
                    rationale = f"16:9 Vision-Curated Documentary Photo ({lf_img_q})"

        # ══════════════════════════════════════════════════════════════════════
        # NHÁNH ƯU TIÊN MỚI: THỰC THI BỘ 5 @TOOL CHUYÊN BIỆT CỦA LLM ĐẠO DIỄN
        # (`summon_epic_monster_cinematic_media` | `fetch_clean_cinematic_portrait_or_broll` | `clone_youtube_short_peak_clip` | `search_pexels_pixabay_stock_video` | `search_google_cinematic_image`)
        # ══════════════════════════════════════════════════════════════════════
        if tool_name == "render_doodle_2d_explainer_scene":
            tool_name = "fetch_clean_cinematic_portrait_or_broll"
        if scene.scene_id == 1 and not is_long_form and tool_name in ("search_google_cinematic_image", ""):
            tool_name = "clone_youtube_short_peak_clip"
        allowed_tools = (
            ()
            if is_long_form
            else (
                "clone_youtube_short_peak_clip",
                "search_pexels_pixabay_stock_video",
                "search_google_cinematic_image",
                "summon_epic_monster_cinematic_media",
                "fetch_clean_cinematic_portrait_or_broll",
            )
        )
        if not media_path and tool_name in allowed_tools:
            from app.graph.tools.media_tools import (
                execute_youtube_short_peak_clone_sync,
                execute_google_cinematic_image_sync,
                execute_epic_monster_cinematic_media_sync,
                execute_clean_cinematic_portrait_or_broll_sync,
            )
            camera_motion = sc_strat.get("camera_motion") or ("hyper_zoom" if idx == 0 else "zoom_in")
            sfx_cue = sc_strat.get("sfx_cue") or ("whoosh" if idx == 0 else "hit")
            chosen_strategy = (
                "EPIC_MONSTER_ENGINE"
                if tool_name == "summon_epic_monster_cinematic_media"
                else "CLEAN_PORTRAIT_BROLL"
                if tool_name == "fetch_clean_cinematic_portrait_or_broll"
                else "YOUTUBE_PEAK_SHORT"
                if tool_name == "clone_youtube_short_peak_clip"
                else "GOOGLE_TOP_IMAGE"
            )
            prod_method = chosen_strategy
            tool = tool_name
            rationale = f"LLM Tool Call: {tool_name}"
            search_query = (
                sc_strat.get("action_or_concept_query")
                or sc_strat.get("human_action_query")
                or sc_strat.get("scene_concept_summary")
                or sc_strat.get("youtube_search_query")
                or sc_strat.get("smart_image_query")
                or kw
            )
            ai_prompt = vp or search_query
            v_analysis = batch_analyses.get(scene.scene_id) or {}
            tier0_searched = True
            channel_visual_mode = state.get("visual_mode") or "auto"
            scene_media_pref = getattr(scene, 'media_type', None)

            from app.graph.tools.media_tools import _sanitize_tool_arg
            subj_ent = _sanitize_tool_arg(
                sc_strat.get("creature_name") or sc_strat.get("subject_entity"),
                fallback=primary_entity or state.get("topic", ""),
            )
            sc_scope = sc_strat.get("search_scope") or "SCENE_SPECIFIC"
            overall_q = sc_strat.get("overall_script_query") or state.get("topic", "")
            import math as _math
            scene_target_imgs = 2 if dur >= 4.0 else 1

            if tool_name == "summon_epic_monster_cinematic_media":
                c_name = _sanitize_tool_arg(sc_strat.get("creature_name"), fallback=subj_ent or "colossal sea monster")
                s_anchor = _sanitize_tool_arg(sc_strat.get("scale_anchor_object"), fallback="submarine")
                m_mode = (
                    "3D_CGI_VIDEO"
                    if scene.scene_id == 1
                    else (sc_strat.get("monster_media_mode") or ("3D_CGI_VIDEO" if scene.scene_id in (2, 3, 4, 5, 6) else "CONCEPT_ART_8K"))
                )
                act_q = sc_strat.get("action_or_concept_query") or kw or f"{c_name} {s_anchor}"
                out_m = str(
                    media_dir / f"scene_{scene.scene_id:03d}_monster.mp4"
                    if m_mode.upper() == "3D_CGI_VIDEO" or scene.scene_id == 1
                    else media_dir / f"scene_{scene.scene_id:03d}_monster_8k.jpg"
                )
                m_res = await asyncio.to_thread(
                    execute_epic_monster_cinematic_media_sync,
                    creature_name=c_name,
                    scale_anchor_object=s_anchor,
                    output_path=out_m,
                    monster_media_mode=m_mode,
                    action_or_concept_query=act_q,
                    overall_script_query=overall_q,
                    search_scope=sc_scope,
                    narration=scene.narration,
                    topic=state.get("topic", ""),
                    target_duration_sec=dur + 0.25,
                    used_urls=used_pool_urls,
                    is_hook_scene=(scene.scene_id == 1),
                    job_id=job_id,
                    scene_id=scene.scene_id,
                    channel_id=channel_id_val,
                    target_image_count=scene_target_imgs,
                    cancel_event=cancel_event,
                    deadline_ts=deadline_ts,
                )
                if m_res and m_res.get("media_path"):
                    media_path = m_res["media_path"]
                    media_type = m_res.get("media_type", "image")
                    vision_curated = True
                    source_url_used = str(m_res.get("source_url") or "")
                    if media_type in ("video", "hook_ready_video"):
                        full_peak_path = m_res.get("full_peak_path")
                        full_peak_duration_sec = float(m_res.get("full_peak_duration_sec") or dur)
                        prod_method = "MONSTER_3D_CGI_VIDEO_PEAK"
                        rationale = f"Monster 3D CGI Video ({c_name} vs {s_anchor}): {m_res.get('title', '')[:45]}"
                    else:
                        pose_images = list(m_res.get("pose_images") or [media_path])
                        hook_alternate_urls = list(m_res.get("alternate_urls") or [])
                        prod_method = "MONSTER_CONCEPT_ART_8K"
                        rationale = f"Monster Concept Art 8K x{len(pose_images)} ({c_name} vs {s_anchor}): {m_res.get('title', '')[:45]}"
                else:
                    _check_fetch_deadline()
                    # Tầng 3 Fallback (Abyssal Silhouette & Scale Anchor AI): Tránh lộ chi tiết giải phẫu méo của model free
                    abyssal_prompt = (
                        f"Colossal silhouette of {c_name} emerging from pitch black fog and deep shadows, "
                        f"towering over a tiny illuminated {s_anchor} in the foreground for massive scale comparison, "
                        f"backlit volumetric xenon spotlight, megalophobia cinematic movie still, 8k vertical 9:16"
                    )
                    out_ai_m = str(media_dir / f"scene_{scene.scene_id:03d}_monster_silhouette.jpg")
                    gen_m = await asyncio.to_thread(
                        image_agent.generate_scene_image,
                        abyssal_prompt,
                        out_ai_m,
                        "9:16",
                        "scifi",
                        scene.scene_id,
                        abyssal_prompt,
                    )
                    if gen_m and Path(gen_m).exists():
                        media_path = gen_m
                        media_type = "image"
                        pose_images = [gen_m]
                        prod_method = "MONSTER_ABYSSAL_SILHOUETTE_AI"
                        rationale = f"Abyssal Silhouette AI ({c_name} vs {s_anchor})"

            elif tool_name == "fetch_clean_cinematic_portrait_or_broll":
                h_query = sc_strat.get("human_action_query") or kw or "fbi interrogation body language eye contact"
                fb_p_q = sc_strat.get("fallback_broad_query") or sq or ""
                story_p_qs = list(sc_strat.get("storyline_search_queries") or [])
                m_pref = (
                    "YOUTUBE_FORENSIC_OR_CHARACTER_SHORT"
                    if scene.scene_id == 1
                    else (
                        sc_strat.get("media_preference")
                        or ("YOUTUBE_FORENSIC_OR_CHARACTER_SHORT" if scene.scene_id in (2, 3, 4, 5, 6, 7) else "CLEAN_PORTRAIT_PHOTO")
                    )
                )
                out_clean = str(
                    media_dir / f"scene_{scene.scene_id:03d}_clean_broll.mp4"
                    if m_pref.upper() in ("VIDEO_BROLL", "YOUTUBE_FORENSIC_OR_CHARACTER_SHORT")
                    else media_dir / f"scene_{scene.scene_id:03d}_clean_portrait.jpg"
                )
                c_res = await asyncio.to_thread(
                    execute_clean_cinematic_portrait_or_broll_sync,
                    subject_entity=subj_ent,
                    human_action_query=h_query,
                    output_path=out_clean,
                    media_preference=m_pref,
                    overall_script_query=overall_q,
                    search_scope=sc_scope,
                    narration=scene.narration,
                    topic=state.get("topic", ""),
                    target_duration_sec=dur + 0.25,
                    used_urls=used_pool_urls,
                    is_hook_scene=(scene.scene_id == 1),
                    job_id=job_id,
                    scene_id=scene.scene_id,
                    target_image_count=scene_target_imgs,
                    fallback_query=fb_p_q,
                    storyline_queries=story_p_qs,
                    cancel_event=cancel_event,
                    deadline_ts=deadline_ts,
                )
                if c_res and c_res.get("media_path"):
                    media_path = c_res["media_path"]
                    media_type = c_res.get("media_type", "image")
                    vision_curated = True
                    source_url_used = str(c_res.get("source_url") or "")
                    if media_type in ("video", "hook_ready_video"):
                        full_peak_path = c_res.get("full_peak_path")
                        full_peak_duration_sec = float(c_res.get("full_peak_duration_sec") or dur)
                        prod_method = "CLEAN_ACTOR_VIDEO_BROLL"
                        rationale = f"Clean Actor Video B-Roll ({h_query}): {c_res.get('title', '')[:45]}"
                    else:
                        pose_images = list(c_res.get("pose_images") or [media_path])
                        hook_alternate_urls = list(c_res.get("alternate_urls") or [])
                        prod_method = "CLEAN_PORTRAIT_PHOTO_8K"
                        rationale = f"Clean Portrait Photo x{len(pose_images)} ({h_query})"

            elif tool_name in ("clone_youtube_short_peak_clip", "search_pexels_pixabay_stock_video"):
                yt_q = sc_strat.get("youtube_search_query") or sc_strat.get("primary_stock_query") or kw or state.get("topic", "")
                fb_q = sc_strat.get("fallback_broad_query") or sc_strat.get("secondary_stock_query") or sq or overall_q or subj_ent
                story_yt_qs = list(sc_strat.get("storyline_search_queries") or [])
                clip_role = sc_strat.get("clip_role") or ("HOOK" if scene.scene_id == 1 else "PEAK")
                out_vid = str(
                    work_dir / "hook_motion_001.mp4"
                    if scene.scene_id == 1
                    else media_dir / f"scene_{scene.scene_id:03d}_yt_peak.mp4"
                )
                # Với các cảnh thân bài (Scene >= 2), ưu tiên gọi API Pexels/Pixabay HD (phản hồi 2–4s) trước
                # để tránh 6 luồng yt-dlp cạo YouTube cùng lúc gây nghẽn băng thông 65s!
                if scene.scene_id >= 2 and not media_path:
                    _check_fetch_deadline()
                    try:
                        from app.services.video_memory_rag import get_video_memory_rag
                        from app.utils.ffmpeg_helper import trim_video
                        rag_vf = get_video_memory_rag()
                        vf_q = (
                            yt_q
                            or (story_yt_qs[0] if story_yt_qs else "")
                            or fb_q
                            or str(getattr(scene, "stock_query", "") or "").strip()
                        )
                        va_vf = dict(v_analysis or {})
                        va_vf["search_queries"] = [
                            q_x for q_x in dict.fromkeys([vf_q, *story_yt_qs, yt_q, fb_q, overall_q]) if q_x
                        ][:4]
                        va_vf["skip_establishing_pass"] = False
                        best_hk_vid = await asyncio.to_thread(
                            media_agent.search_best_video_clip,
                            vf_q,
                            used_pool_urls,
                            va_vf,
                            scene.narration,
                            state.get("topic", ""),
                        )
                        if best_hk_vid and best_hk_vid.get("url"):
                            vf_cand_urls = [
                                best_hk_vid["url"],
                                *(best_hk_vid.get("_vision_alternates") or []),
                            ]
                            vf_title = str(best_hk_vid.get("title") or vf_q)
                            for cand_hk_url in vf_cand_urls[:4]:
                                if not cand_hk_url:
                                    continue
                                if not rag_vf.try_claim_in_job_video(
                                    job_id=job_id,
                                    vid_id=str(cand_hk_url),
                                    url=str(cand_hk_url),
                                    title=vf_title,
                                    scene_id=scene.scene_id,
                                ):
                                    used_pool_urls.add(str(cand_hk_url))
                                    continue
                                raw_hk_out = str(media_dir / f"scene_{scene.scene_id:03d}_vfirst_raw.mp4")
                                vid_hk_out = str(media_dir / f"scene_{scene.scene_id:03d}_vfirst.mp4")
                                await asyncio.to_thread(media_agent.download_file, str(cand_hk_url), raw_hk_out)
                                if Path(raw_hk_out).exists() and Path(raw_hk_out).stat().st_size > 15_000:
                                    await asyncio.to_thread(
                                        trim_video,
                                        input_path=raw_hk_out,
                                        duration_seconds=max(3.0, dur + 0.25),
                                        output_path=vid_hk_out,
                                        width=1920 if is_long_form else 1080,
                                        height=1080 if is_long_form else 1920,
                                        fps=30,
                                        is_scifi=is_scifi_val,
                                        niche=current_niche,
                                        apply_grading=True,
                                        is_hook_scene=False,
                                        fair_use_transform=True,
                                    )
                                    Path(raw_hk_out).unlink(missing_ok=True)
                                    if Path(vid_hk_out).exists() and Path(vid_hk_out).stat().st_size > 15_000:
                                        ok_vf_sig, _ = rag_vf.verify_and_commit_in_job_clip(
                                            job_id=job_id,
                                            scene_id=scene.scene_id,
                                            media_path=vid_hk_out,
                                            vid_id=str(cand_hk_url),
                                            url=str(cand_hk_url),
                                            title=vf_title,
                                        )
                                        used_pool_urls.add(str(cand_hk_url))
                                        if ok_vf_sig:
                                            media_path = vid_hk_out
                                            media_type = "video"
                                            vision_curated = True
                                            prod_method = "CLIMAX_STOCK_VIDEO_REAL_MOTION"
                                            chosen_strategy = "STOCK_VIDEO"
                                            source_url_used = str(cand_hk_url)
                                            rationale = f"Video-First Stock Video ({vf_title[:45]})"
                                            logger.info(
                                                f"Scene {scene.scene_id}: 🎬 [Video-First Real-Motion] "
                                                f"Đã nạp Video B-Roll chuyển động thật cho '{vf_q}'"
                                            )
                                            break
                                        else:
                                            Path(vid_hk_out).unlink(missing_ok=True)
                                else:
                                    Path(raw_hk_out).unlink(missing_ok=True)
                                    rag_vf.release_in_job_video_claim(
                                        job_id=job_id, vid_id=str(cand_hk_url), url=str(cand_hk_url), title=vf_title, scene_id=scene.scene_id
                                    )
                    except Exception as hk_vf_err:
                        logger.debug(f"Scene {scene.scene_id}: Video-first lookup fallback: {hk_vf_err}")

                if not media_path:
                    _check_fetch_deadline()
                    # Riêng Cảnh 1 (Opening Hook) được cấp ngân sách ưu tiên 52s để chạy đủ 3 tầng Video (YouTube Peak + Stock HD + Bilibili Documentary);
                    # các cảnh thân bài được cấp 38s để cân bằng tốc độ song song.
                    _prefer_non_yt_node = os.getenv("PREFER_NON_YOUTUBE_SOURCES", "false").strip().lower() in ("1", "true", "yes", "on")
                    _yt_budget_sec = (
                        52.0
                        if scene.scene_id == 1
                        else (38.0 if _prefer_non_yt_node else 36.0)
                    )
                    _reserve_tail_sec = 4.0 if scene.scene_id == 1 else 12.0
                    yt_sub_deadline = (
                        min(deadline_ts - _reserve_tail_sec, time.monotonic() + _yt_budget_sec)
                        if deadline_ts is not None
                        else (time.monotonic() + _yt_budget_sec)
                    )
                    if yt_sub_deadline > time.monotonic() + 4.0:
                        yt_res = await asyncio.to_thread(
                            execute_youtube_short_peak_clone_sync,
                            search_query=yt_q,
                            output_path=out_vid,
                            target_duration_sec=dur,
                            clip_role=clip_role,
                            used_urls=used_pool_urls,
                            fallback_query=fb_q,
                            subject_entity=subj_ent,
                            narration=scene.narration,
                            topic=state.get("topic", ""),
                            search_scope=sc_scope,
                            overall_script_query=overall_q,
                            max_peak_duration_sec=14.0,
                            job_id=job_id,
                            scene_id=scene.scene_id,
                            channel_id=channel_id_val,
                            skip_youtube_search=(tool_name == "search_pexels_pixabay_stock_video"),
                            storyline_queries=story_yt_qs,
                            cancel_event=cancel_event,
                            deadline_ts=yt_sub_deadline,
                        )
                        if yt_res and yt_res.get("media_path"):
                            media_path = yt_res["media_path"]
                            media_type = yt_res.get("media_type", "video")
                            vision_curated = True
                            full_peak_path = yt_res.get("full_peak_path")
                            full_peak_duration_sec = float(yt_res.get("full_peak_duration_sec") or dur)
                            source_url_used = str(yt_res.get("source_url") or "")
                            prod_method = "CLIMAX_REAL_VIDEO_CONTINUOUS_PEAK" if scene.scene_id == 1 else "CLIMAX_PEAK_SHORT_SPLICE"
                            rationale = (
                                f"YouTube Shorts Peak [{yt_res.get('search_scope', sc_scope)}] "
                                f"({yt_res.get('window')}, full_peak={full_peak_duration_sec:.1f}s): {yt_res.get('title', '')[:45]}"
                            )
                if not media_path:
                    _check_fetch_deadline()
                    logger.info(f"Scene {scene.scene_id}: ⚡ YouTube Shorts & Stock Vision không có clip đạt chuẩn cho '{yt_q}' -> Chuyển sang tìm bộ {scene_target_imgs} ảnh Top Google Images!")
                    out_img = str(media_dir / f"scene_{scene.scene_id:03d}_gsearch.jpg")
                    img_res = await asyncio.to_thread(
                        execute_google_cinematic_image_sync,
                        smart_query=yt_q,
                        output_path=out_img,
                        narration=scene.narration,
                        topic=state.get("topic", ""),
                        used_urls=used_pool_urls,
                        is_hook_scene=(scene.scene_id == 1),
                        subject_entity=subj_ent,
                        search_scope=sc_scope,
                        overall_script_query=overall_q,
                        job_id=job_id,
                        scene_id=scene.scene_id,
                        target_image_count=scene_target_imgs,
                    )
                    if img_res and img_res.get("media_path"):
                        media_path = img_res["media_path"]
                        media_type = "image"
                        vision_curated = True
                        pose_images = list(img_res.get("pose_images") or [media_path])
                        source_url_used = str(img_res.get("source_url") or "")
                        hook_alternate_urls = list(img_res.get("alternate_urls") or [])
                        prod_method = "GOOGLE_TOP_IMAGE_FALLBACK"
            else:
                img_q = sc_strat.get("smart_image_query") or kw or state.get("topic", "")
                if (
                    scene.scene_id >= 2
                    and channel_visual_mode not in ("imagen_ai", "ai_image", "image")
                    and scene_media_pref != "image"
                ):
                    try:
                        pre_used_urls = set(used_pool_urls)
                        best_vid = await asyncio.to_thread(
                            media_agent.search_best_video_clip,
                            img_q,
                            used_pool_urls,
                            v_analysis,
                            scene.narration,
                            state.get("topic", ""),
                        )
                        if best_vid and best_vid.get("url") and best_vid["url"] not in pre_used_urls:
                            v_score = float(best_vid.get("curation_score") or best_vid.get("vision_score") or 0.0)
                            is_valid_hd = bool(
                                v_score >= 6.8
                                or best_vid.get("vision_verified")
                                or best_vid.get("vision_curated")
                                or (v_score == 0.0 and int(best_vid.get("height") or 0) >= 720)
                            )
                            if is_valid_hd:
                                cand_url = str(best_vid["url"])
                                vid_out = str(media_dir / f"scene_{scene.scene_id:03d}_vfirst.mp4")
                                await asyncio.to_thread(media_agent.download_file, cand_url, vid_out)
                                if Path(vid_out).exists() and Path(vid_out).stat().st_size > 15000:
                                    media_path = vid_out
                                    media_type = "video"
                                    vision_curated = True
                                    prod_method = "VIDEO_FIRST_BODY_BROLL"
                                    source_url_used = cand_url
                                    used_pool_urls.add(cand_url)
                                    rationale = (
                                        f"Video-First Body B-Roll ({v_score or 'HD'}): "
                                        f"{str(best_vid.get('title', img_q))[:45]}"
                                    )
                                    logger.info(
                                        f"Scene {scene.scene_id}: 🎥 [Video-First Body Curation] "
                                        f"Ưu tiên chọn Video B-Roll thực tế thay ảnh tĩnh cho '{img_q}'"
                                    )
                    except Exception as vf_err:
                        logger.debug(f"Scene {scene.scene_id}: Video-First body stock lookup fallback: {vf_err}")

                if not media_path and scene.scene_id in (1, 2):
                    try:
                        pre_used_hk = set(used_pool_urls)
                        best_hk_vid = await asyncio.to_thread(
                            media_agent.search_best_video_clip,
                            img_q,
                            used_pool_urls,
                            v_analysis,
                            scene.narration,
                            state.get("topic", ""),
                        )
                        if best_hk_vid and best_hk_vid.get("url") and best_hk_vid["url"] not in pre_used_hk:
                            cand_hk_url = str(best_hk_vid["url"])
                            vid_hk_out = str(media_dir / f"scene_{scene.scene_id:03d}_vfirst.mp4")
                            await asyncio.to_thread(media_agent.download_file, cand_hk_url, vid_hk_out)
                            if Path(vid_hk_out).exists() and Path(vid_hk_out).stat().st_size > 15_000:
                                media_path = vid_hk_out
                                media_type = "hook_ready_video" if scene.scene_id == 1 else "video"
                                vision_curated = True
                                prod_method = "CLIMAX_STOCK_VIDEO_REAL_MOTION"
                                chosen_strategy = "STOCK_VIDEO"
                                source_url_used = cand_hk_url
                                used_pool_urls.add(cand_hk_url)
                                rationale = f"Opening Hook Stock Video ({best_hk_vid.get('title', img_q)[:45]})"
                                logger.info(
                                    f"Scene {scene.scene_id}: 🎬 [Opening Real-Motion Video] "
                                    f"Đã nạp Video B-Roll chuyển động thật cho '{img_q}'"
                                )
                    except Exception as hk_vf_err:
                        logger.debug(f"Scene {scene.scene_id}: Opening video-first lookup fallback: {hk_vf_err}")
                if not media_path:
                    out_img = str(media_dir / f"scene_{scene.scene_id:03d}_gsearch.jpg")
                    img_res = await asyncio.to_thread(
                        execute_google_cinematic_image_sync,
                        smart_query=img_q,
                        output_path=out_img,
                        narration=scene.narration,
                        topic=state.get("topic", ""),
                        used_urls=used_pool_urls,
                        is_hook_scene=(scene.scene_id == 1),
                        subject_entity=subj_ent,
                        search_scope=sc_scope,
                        overall_script_query=overall_q,
                        job_id=job_id,
                        scene_id=scene.scene_id,
                        target_image_count=scene_target_imgs,
                    )
                    if img_res and img_res.get("media_path"):
                        media_path = img_res["media_path"]
                        media_type = "image"
                        vision_curated = True
                        pose_images = list(img_res.get("pose_images") or [media_path])
                        source_url_used = str(img_res.get("source_url") or "")
                        hook_alternate_urls = list(img_res.get("alternate_urls") or [])
                        rationale = (
                            f"Google Top Image x{len(pose_images)} [{img_res.get('search_scope', sc_scope)}] "
                            f"({img_res.get('curation_score')}): {img_res.get('title', '')[:45]}"
                        )
        else:
            # ── Khởi tạo biến cho cảnh dùng cache hoặc fallback tương thích ngược ──
            surround_text = " ".join([s.narration for s in script.scenes[max(0, idx - 1):min(len(script.scenes), idx + 2)]])
            v_analysis = batch_analyses.get(scene.scene_id) or {}
            if not media_path:
                if is_long_form:
                    chosen_strategy = "LONGFORM_16X9_DOCUMENTARY_VIDEO"
                    prod_method = "LONGFORM_16X9"
                else:
                    chosen_strategy = sc_strat.get("strategy", "STOCK_VIDEO")
                    prod_method = sc_strat.get("production_method") or "STOCK_FOOTAGE"
            channel_visual_mode = state.get("visual_mode") or "auto"
            scene_media_pref = getattr(scene, 'media_type', None)
            tool = sc_strat.get("tool") or tool_name or ""
            rationale = sc_strat.get("rationale", "")
            search_query = sc_strat.get("search_query") or sc_strat.get("youtube_search_query") or sc_strat.get("smart_image_query") or sq or kw
            ai_prompt = sc_strat.get("ai_prompt") or vp or kw
            camera_motion = sc_strat.get("camera_motion") or ("hyper_zoom" if idx == 0 else "zoom_in")
            sfx_cue = sc_strat.get("sfx_cue") or ("whoosh" if idx == 0 else "hit")
            tier0_searched = False

        # ══════════════════════════════════════════════════════════════════════
        # TẦNG 0 (ƯU TIÊN TUYỆT ĐỐI): GEMINI GOOGLE SEARCH + VISION CURATION + 3D MOTION
        # Phụ thuộc vào kết quả tìm kiếm Google Images bắt mắt để câu view (CGI, Concept Art 8K,
        # Movie Scene, Dramatic HD, Dark Horror) do Gemini Vision trực tiếp chấm chọn.
        # TỐI ƯU TỐC ĐỘ: Giữ nguyên file ảnh tĩnh ở bước Fetch để VideoAgent._prepare_scene_clips
        # render 3D Ken Burns ĐÚNG 1 LẦN DUY NHẤT (kèm bù thời lượng xfade), giảm 50% thời gian FFmpeg!
        # ══════════════════════════════════════════════════════════════════════
        search_first = os.getenv("SEARCH_FIRST_VISUAL_MODE", "true").strip().lower() not in ("0", "false", "no", "off")
        creature_first = os.getenv("CREATURE_ART_FIRST", "true").strip().lower() not in ("0", "false", "no", "off")
        if (
            not media_path
            and not is_long_form
            and not tier0_searched
            and scene.scene_id not in (1, 2)
            and (search_first or (creature_first and v_analysis.get("creature_mode")))
            and channel_visual_mode not in ("imagen_ai", "ai_image")
        ):
            try:
                tier0_searched = True
                g_queries = (
                    (v_analysis.get("creature_queries") if v_analysis.get("creature_mode") else None)
                    or v_analysis.get("google_image_queries")
                    or v_analysis.get("search_queries")
                    or [kw or search_query or sq]
                )
                primary_g_query = next((q for q in g_queries if q), kw or search_query or sq)
                searched_photo = await asyncio.to_thread(
                    media_agent.search_best_photo,
                    primary_g_query,
                    used_pool_urls,
                    v_analysis,
                    scene.narration,
                    state.get("topic", ""),
                )
                if searched_photo and searched_photo.get("url"):
                    tag_suffix = "creature" if v_analysis.get("creature_mode") else "gsearch"
                    out_img = str(media_dir / f"scene_{scene.scene_id:03d}_{tag_suffix}.jpg")
                    await asyncio.to_thread(media_agent.download_file, searched_photo["url"], out_img)
                    if Path(out_img).exists() and Path(out_img).stat().st_size > 3000:
                        used_pool_urls.add(searched_photo["url"])
                        source_url_used = searched_photo["url"]
                        media_path = out_img
                        media_type = "image"
                        vision_curated = True
                        hook_alternate_urls = list(searched_photo.get("alternate_urls") or [])
                        prod_method = chosen_strategy = "CREATURE_ART" if v_analysis.get("creature_mode") else "GEMINI_SEARCH_3D"
                        if idx == 0 and (not camera_motion or camera_motion == "zoom_in"):
                            camera_motion = "hyper_zoom"
                        badge = "🐙 [Quái vật Rùng Rợn]" if v_analysis.get("creature_mode") else "🔎 [Gemini Search 3D]"
                        logger.info(
                            f"Scene {scene.scene_id}: {badge} {searched_photo.get('source')} "
                            f"điểm {searched_photo.get('curation_score', '-')}: {str(searched_photo.get('title', ''))[:55]}"
                        )
            except Exception as e:
                logger.debug(f"Scene {scene.scene_id}: Gemini Search visual error: {e}")

        # ══════════════════════════════════════════════════════════════════════
        # PHƯƠNG PHÁP 1: REAL_ARCHIVE (Tư liệu lịch sử thật 100%)
        # ══════════════════════════════════════════════════════════════════════
        if not media_path and (prod_method == "REAL_ARCHIVE" or chosen_strategy == "REAL_DOCUMENTARY"):
            # A. Thử Semantic Pool Matcher
            if primary_entity and entity_media_pool:
                best_pool_photo = match_best_pool_photo(
                    scene_narration=scene.narration,
                    scene_kw=f"{kw} {search_query}",
                    pool=entity_media_pool,
                    used_urls=used_pool_urls,
                    threshold=25,
                )
                if best_pool_photo and best_pool_photo.get("url"):
                    cand_url = best_pool_photo["url"]
                    try:
                        ext = ".jpg" if any(x in cand_url.lower() for x in ["jpg", "jpeg"]) else ".png"
                        output = str(media_dir / f"scene_{scene.scene_id:03d}_doc{ext}")
                        await asyncio.to_thread(media_agent.download_file, cand_url, output)
                        if Path(output).exists() and Path(output).stat().st_size > 3000:
                            used_pool_urls.add(cand_url)
                            source_url_used = cand_url
                            media_path = output
                            media_type = "image"
                            logger.info(f"Scene {scene.scene_id}: 📸 [Tư liệu thật] Khớp từ Pool: {best_pool_photo.get('title', '')[:45]}")
                    except Exception as ex:
                        logger.debug(f"Scene {scene.scene_id}: Pool photo download error: {ex}")

            # B. Thử Wikipedia & Wikimedia Commons
            if not media_path:
                try:
                    candidate_media = await asyncio.to_thread(
                        media_agent.search_scene_media_multi_source,
                        scene_narration=scene.narration,
                        visual_keyword=kw,
                        stock_query=search_query,
                        visual_prompt=vp,
                        primary_entity=primary_entity,
                        entity_type=entity_type,
                        used_urls=used_pool_urls,
                        scene_id=scene.scene_id,
                        prefer_video=False,
                        prefer_photo=True,
                        visual_analysis=v_analysis,
                    )
                    if candidate_media and candidate_media.get("url"):
                        cand_url = candidate_media["url"]
                        c_src = candidate_media.get("source", "web")
                        ext = ".jpg" if any(x in cand_url.lower() for x in ["jpg", "jpeg"]) else ".png"
                        output = str(media_dir / f"scene_{scene.scene_id:03d}_{c_src}{ext}")
                        await asyncio.to_thread(media_agent.download_file, cand_url, output)
                        if Path(output).exists() and Path(output).stat().st_size > 3000:
                            used_pool_urls.add(cand_url)
                            source_url_used = cand_url
                            media_path = output
                            media_type = "image"
                            hook_alternate_urls = list(candidate_media.get("alternate_urls") or [])
                            logger.info(f"Scene {scene.scene_id}: 📸 [Tư liệu thật] Tải từ {c_src}: {candidate_media.get('title', '')[:40]}")
                except Exception as e:
                    logger.debug(f"Scene {scene.scene_id}: Doc search error: {e}")

            # C. Ảnh Internet có giấy phép (Openverse/Flickr, Commons, Unsplash) + stock do AI Vision tuyển chọn (nếu chưa tìm ở Tier 0)
            if not media_path and not tier0_searched:
                try:
                    best_photo = await asyncio.to_thread(
                        media_agent.search_best_photo,
                        search_query or sq or kw,
                        used_pool_urls,
                        v_analysis,
                        scene.narration,
                        state.get("topic", ""),
                    )
                    if best_photo and best_photo.get("url"):
                        src = best_photo.get("source", "web")
                        output = str(media_dir / f"scene_{scene.scene_id:03d}_{src}_doc.jpg")
                        await asyncio.to_thread(media_agent.download_file, best_photo["url"], output)
                        if Path(output).exists() and Path(output).stat().st_size > 3000:
                            used_pool_urls.add(best_photo["url"])
                            source_url_used = best_photo["url"]
                            media_path = output
                            media_type = "image"
                            hook_alternate_urls = list(best_photo.get("alternate_urls") or [])
                            logger.info(
                                f"Scene {scene.scene_id}: 📸 [Tư liệu web có giấy phép] {src} "
                                f"điểm {best_photo.get('curation_score', '-')}: {str(best_photo.get('title', ''))[:45]}"
                            )
                except Exception as e:
                    logger.debug(f"Scene {scene.scene_id}: Curated web photo error: {e}")

        # ══════════════════════════════════════════════════════════════════════
        # PHƯƠNG PHÁP 2: IMAGE_TO_VIDEO (Tạo video phụ thuộc vào ảnh chủ đề 8K)
        # Giữ nét tuyệt đối, không méo mặt, chuyển hóa ảnh thành video 3D
        # ══════════════════════════════════════════════════════════════════════
        elif prod_method == "IMAGE_TO_VIDEO":
            try:
                ai_vid_path = str(media_dir / f"scene_{scene.scene_id:03d}_i2v.mp4")
                prompt_ai = ai_prompt if len(ai_prompt.strip()) > 10 else f"Cinematic 8k dramatic vertical photo of {kw}"
                res_vid = await ai_video_agent.generate_image_to_video(
                    prompt=prompt_ai,
                    output_path=ai_vid_path,
                    duration_seconds=dur,
                    scene_id=scene.scene_id,
                    camera_motion=camera_motion,
                    visual_prompt=vp,
                    used_urls=used_pool_urls,
                    visual_analysis=v_analysis,
                    narration=scene.narration,
                    niche=current_niche,
                    is_scifi=is_scifi_val,
                    skip_web_search=tier0_searched,
                )
                if res_vid and Path(res_vid).exists() and Path(res_vid).stat().st_size > 5000:
                    media_path = res_vid
                    media_type = "video"
                    logger.info(f"Scene {scene.scene_id}: 🎨 [Image-to-Video] Tạo video động từ ảnh chủ đề 8K ({dur:.1f}s)")
            except Exception as e:
                logger.warning(f"Scene {scene.scene_id}: Image-to-Video error: {e}")

            # Cứu hộ ngay lập tức nếu AI chưa sinh được video
            if not media_path:
                logger.info(f"Scene {scene.scene_id}: ⚡ Image-to-Video không thành công, tự động cứu hộ bằng Pexels Video B-Roll...")
                try:
                    best_vid = await asyncio.to_thread(media_agent.search_best_video_clip, search_query or sq or kw, used_pool_urls, v_analysis, scene.narration, state.get("topic", ""))
                    if not best_vid and kw:
                        clean_kw = clean_scene_search_query(kw, max_words=2)
                        best_vid = await asyncio.to_thread(media_agent.search_best_video_clip, clean_kw, used_pool_urls, v_analysis, scene.narration, state.get("topic", ""))
                    if best_vid and best_vid.get("url"):
                        cand_url = best_vid["url"]
                        vid_out = str(media_dir / f"scene_{scene.scene_id:03d}_pexels_fb.mp4")
                        await asyncio.to_thread(media_agent.download_file, cand_url, vid_out)
                        if Path(vid_out).exists() and Path(vid_out).stat().st_size > 10000:
                            used_pool_urls.add(cand_url)
                            source_url_used = cand_url
                            media_path = vid_out
                            media_type = "video"
                            logger.info(f"Scene {scene.scene_id}: 🎥 Cứu hộ thành công bằng Video Pexels: '{search_query or kw}'")
                except Exception as ex:
                    logger.debug(f"Scene {scene.scene_id}: Video fallback error: {ex}")

        # ══════════════════════════════════════════════════════════════════════
        # PHƯƠNG PHÁP 3: TEXT_TO_VIDEO (Tạo video AI trực tiếp cho cảnh kỳ ảo)
        # ══════════════════════════════════════════════════════════════════════
        elif prod_method == "TEXT_TO_VIDEO" or chosen_strategy == "AI_VIDEO":
            try:
                ai_vid_path = str(media_dir / f"scene_{scene.scene_id:03d}_t2v.mp4")
                prompt_ai = ai_prompt if len(ai_prompt.strip()) > 10 else f"Mysterious glowing {kw}, 8k vertical cinematic video"
                res_vid = await ai_video_agent.generate_text_to_video(
                    prompt=prompt_ai,
                    output_path=ai_vid_path,
                    duration_seconds=dur,
                    scene_id=scene.scene_id,
                    camera_motion=camera_motion,
                    visual_prompt=vp,
                    used_urls=used_pool_urls,
                    visual_analysis=v_analysis,
                    narration=scene.narration,
                    skip_stock_lookup=bool(v_analysis.get("prefer_generation", False)),
                    niche=current_niche,
                    is_scifi=is_scifi_val,
                    skip_web_search=tier0_searched,
                )
                if res_vid and Path(res_vid).exists() and Path(res_vid).stat().st_size > 5000:
                    media_path = res_vid
                    media_type = "video"
                    logger.info(f"Scene {scene.scene_id}: 🔮 [Text-to-Video] Tạo video AI trực tiếp kỳ ảo ({dur:.1f}s)")
            except Exception as e:
                logger.warning(f"Scene {scene.scene_id}: Text-to-Video error: {e}")

            # Cứu hộ ngay lập tức nếu AI video chưa có kết quả
            if not media_path:
                logger.info(f"Scene {scene.scene_id}: ⚡ Text-to-Video không thành công, tự động cứu hộ bằng Pexels Video B-Roll...")
                try:
                    best_vid = await asyncio.to_thread(media_agent.search_best_video_clip, search_query or sq or kw, used_pool_urls, v_analysis, scene.narration, state.get("topic", ""))
                    if not best_vid and kw:
                        clean_kw = clean_scene_search_query(kw, max_words=2)
                        best_vid = await asyncio.to_thread(media_agent.search_best_video_clip, clean_kw, used_pool_urls, v_analysis, scene.narration, state.get("topic", ""))
                    if best_vid and best_vid.get("url"):
                        cand_url = best_vid["url"]
                        vid_out = str(media_dir / f"scene_{scene.scene_id:03d}_pexels_fb.mp4")
                        await asyncio.to_thread(media_agent.download_file, cand_url, vid_out)
                        if Path(vid_out).exists() and Path(vid_out).stat().st_size > 10000:
                            used_pool_urls.add(cand_url)
                            source_url_used = cand_url
                            media_path = vid_out
                            media_type = "video"
                            logger.info(f"Scene {scene.scene_id}: 🎥 Cứu hộ thành công bằng Video Pexels: '{search_query or kw}'")
                except Exception as ex:
                    logger.debug(f"Scene {scene.scene_id}: Video fallback error: {ex}")

        # ══════════════════════════════════════════════════════════════════════
        # PHƯƠNG PHÁP 4: STOCK_FOOTAGE (Video B-Roll chuyển động thực tế 9:16)
        # ══════════════════════════════════════════════════════════════════════
        elif prod_method == "STOCK_FOOTAGE" or chosen_strategy == "STOCK_VIDEO":
            try:
                best_vid = await asyncio.to_thread(
                    media_agent.search_best_video_clip,
                    search_query,
                    used_pool_urls,
                    v_analysis,
                    scene.narration,
                    state.get("topic", ""),
                )
                if not best_vid and sq:
                    best_vid = await asyncio.to_thread(
                        media_agent.search_best_video_clip,
                        sq,
                        used_pool_urls,
                        v_analysis,
                        scene.narration,
                    )
                if not best_vid:
                    clean_kw = clean_scene_search_query(kw, max_words=2)
                    best_vid = await asyncio.to_thread(
                        media_agent.search_best_video_clip,
                        clean_kw,
                        used_pool_urls,
                        v_analysis,
                        scene.narration,
                    )

                if best_vid and best_vid.get("url"):
                    cand_url = best_vid["url"]
                    vid_out = str(media_dir / f"scene_{scene.scene_id:03d}_pexels.mp4")
                    await asyncio.to_thread(media_agent.download_file, cand_url, vid_out)
                    if Path(vid_out).exists() and Path(vid_out).stat().st_size > 10000:
                        used_pool_urls.add(cand_url)
                        source_url_used = cand_url
                        media_path = vid_out
                        media_type = "video"
                        logger.info(f"Scene {scene.scene_id}: 🎥 [Video B-Roll Tuyển chọn] Tải thành công video: '{best_vid.get('title')}'")
            except Exception as e:
                logger.debug(f"Scene {scene.scene_id}: Pexels video search error: {e}")

            # Nếu Agent từ chối kho stock hoặc không có video phù hợp -> Tự động sinh Video AI kỳ ảo
            if not media_path:
                logger.info(f"Scene {scene.scene_id}: ⚡ Agent từ chối kho stock, tự động chuyển hướng tạo Video AI chuyển động...")
                try:
                    ai_vid_path = str(media_dir / f"scene_{scene.scene_id:03d}_t2v.mp4")
                    res_vid = await ai_video_agent.generate_text_to_video(
                        prompt=ai_prompt,
                        output_path=ai_vid_path,
                        duration_seconds=dur,
                        scene_id=scene.scene_id,
                        camera_motion=camera_motion,
                        visual_prompt=ai_prompt,
                        used_urls=used_pool_urls,
                        visual_analysis=v_analysis,
                        narration=scene.narration,
                        skip_web_search=tier0_searched,
                    )
                    if res_vid and Path(res_vid).exists() and Path(res_vid).stat().st_size > 5000:
                        media_path = res_vid
                        media_type = "video"
                        logger.info(f"Scene {scene.scene_id}: 🔮 [AI Video Cứu hộ] Tạo video AI trực tiếp thành công ({dur:.1f}s)")
                except Exception as e_ai:
                    logger.debug(f"Scene {scene.scene_id}: AI video fallback error: {e_ai}")

        # ══════════════════════════════════════════════════════════════════════
        # CHIẾN LƯỢC 4: AI_IMAGE (Ảnh nghệ thuật AI điện ảnh 8K)
        # ══════════════════════════════════════════════════════════════════════
        elif chosen_strategy == "AI_IMAGE":
            try:
                ai_img_path = str(media_dir / f"scene_{scene.scene_id:03d}_ai_image.jpg")
                prompt_to_use = ai_prompt if len(ai_prompt.strip()) > 10 else f"Cinematic 8k dramatic vertical photo of {kw}"
                res_img = await asyncio.to_thread(
                    image_agent.generate_scene_image,
                    prompt_to_use,
                    ai_img_path,
                    "9:16",
                    "cinematic",
                    scene.scene_id,
                    vp,
                )
                if res_img and Path(res_img).exists() and Path(res_img).stat().st_size > 2000:
                    media_path = res_img
                    media_type = "image"
                    logger.info(f"Scene {scene.scene_id}: ✨ [Ảnh AI 8K] Tạo thành công ảnh điện ảnh nghệ thuật")
            except Exception as e:
                logger.warning(f"Scene {scene.scene_id}: AI Image error: {e}")

        # ══════════════════════════════════════════════════════════════════════
        # HỆ THỐNG DỰ PHÒNG THÔNG MINH (MULTI-TIER GRACEFUL FALLBACK)
        # Nếu chiến lược chính ở trên chưa lấy được file, tự động fallback theo thứ tự
        # ══════════════════════════════════════════════════════════════════════
        if not media_path and not is_long_form:
            _check_fetch_deadline()
            logger.info(f"Scene {scene.scene_id}: ⚠️ Chiến lược '{chosen_strategy}' chưa có file, kích hoạt Multi-Tier Fallback...")

            # Fallback 1: Thử tìm Video B-Roll hoặc ảnh Pexels HD thực tế
            try:
                candidate_media = await asyncio.to_thread(
                    media_agent.search_scene_media_multi_source,
                    scene_narration=scene.narration,
                    visual_keyword=kw,
                    stock_query=sq or search_query,
                    visual_prompt=vp,
                    primary_entity=primary_entity,
                    entity_type=entity_type,
                    used_urls=used_pool_urls,
                    scene_id=scene.scene_id,
                    prefer_video=(chosen_strategy != "REAL_DOCUMENTARY"),
                    prefer_photo=(chosen_strategy == "REAL_DOCUMENTARY"),
                    visual_analysis=v_analysis,
                )
                if candidate_media and candidate_media.get("url"):
                    cand_url = candidate_media["url"]
                    c_src = candidate_media.get("source", "web")
                    c_type = candidate_media.get("media_type", "image")
                    ext = ".mp4" if c_type == "video" else (".jpg" if any(x in cand_url.lower() for x in ["jpg", "jpeg"]) else ".png")
                    output = str(media_dir / f"scene_{scene.scene_id:03d}_{c_src}{ext}")
                    await asyncio.to_thread(media_agent.download_file, cand_url, output)
                    if Path(output).exists() and Path(output).stat().st_size > 3000:
                        used_pool_urls.add(cand_url)
                        source_url_used = cand_url
                        media_path = output
                        media_type = c_type
                        if c_type == "image":
                            hook_alternate_urls = list(candidate_media.get("alternate_urls") or [])
            except Exception:
                pass

        # Fallback 2: Nếu là chế độ VIDEO (STOCK_FOOTAGE hoặc TEXT_TO_VIDEO) -> Ưu tiên tạo Video AI chuyển động 3D thay vì hạ cấp xuống ảnh tĩnh
        is_video_mode = prod_method in ("STOCK_FOOTAGE", "TEXT_TO_VIDEO") or chosen_strategy in ("STOCK_VIDEO", "AI_VIDEO")
        if not media_path and is_video_mode:
            _check_fetch_deadline()
            try:
                ai_v_path = str(media_dir / f"scene_{scene.scene_id:03d}_fb_motion.mp4")
                prompt_fb = ai_prompt or vp or kw or "cinematic dramatic scene"
                res_v = await ai_video_agent.generate_image_to_video(
                    prompt=prompt_fb,
                    output_path=ai_v_path,
                    duration_seconds=dur,
                    scene_id=scene.scene_id,
                    camera_motion=camera_motion,
                    visual_prompt=vp,
                    narration=scene.narration,
                    niche=current_niche,
                    is_scifi=is_scifi_val,
                    skip_web_search=tier0_searched,
                )
                if res_v and Path(res_v).exists() and Path(res_v).stat().st_size > 5000:
                    media_path = res_v
                    media_type = "video"
                    logger.info(f"Scene {scene.scene_id}: 🎥 Fallback 2 thành công với AI Motion Video 9:16 (giữ nhịp video động)")
            except Exception as fb_v_e:
                logger.debug(f"Scene {scene.scene_id}: Fallback 2 AI video error: {fb_v_e}")

        # Fallback 3: Với chủ đề Tâm lý học / Khắc kỷ / Bí ẩn hoặc kênh chế độ AI -> Ưu tiên sinh ảnh AI 8K nghệ thuật
        is_psych_or_ai = (
            channel_visual_mode in ("imagen_ai", "ai_image")
            or scene_media_pref == "ai_image"
            or state.get("channel_style") in ("psychology", "philosophy_stoicism", "horror_urban_legends", "deep_sea_earth_mysteries")
        )

        if not media_path and not is_long_form and is_psych_or_ai:
            _check_fetch_deadline()
            try:
                ai_img_path = str(media_dir / f"scene_{scene.scene_id:03d}_fb_ai.jpg")
                clean_kw = clean_scene_search_query(kw, max_words=4)
                prompt_fb = vp if (vp and len(vp.strip()) > 10) else (clean_kw or "cinematic scene")
                res_img = await asyncio.to_thread(
                    image_agent.generate_scene_image,
                    prompt_fb,
                    ai_img_path,
                    "9:16",
                    state.get("channel_style", "cinematic"),
                    scene.scene_id,
                    vp,
                )
                if res_img and Path(res_img).exists() and Path(res_img).stat().st_size > 2000:
                    media_path = res_img
                    media_type = "image"
                    logger.info(f"Scene {scene.scene_id}: ✨ [Ảnh AI 8K Cứu hộ] Sinh thành công ảnh nghệ thuật ({state.get('channel_style')})")
            except Exception as e_fb_ai:
                logger.debug(f"Scene {scene.scene_id}: AI image fallback error: {e_fb_ai}")

        # Fallback 4: Ảnh tĩnh HD đa nguồn (Pexels + Pixabay + Openverse) do AI Vision tuyển chọn
        if not media_path:
            _check_fetch_deadline()
            try:
                clean_q = search_query or sq or kw
                best_photo = await asyncio.to_thread(
                    media_agent.search_best_photo,
                    clean_q,
                    used_pool_urls,
                    v_analysis,
                    scene.narration,
                    state.get("topic", ""),
                )
                if best_photo and best_photo.get("url"):
                    img_url = best_photo["url"]
                    src = best_photo.get("source", "stock")
                    out_img = str(media_dir / f"scene_{scene.scene_id:03d}_{src}_photo.jpg")
                    await asyncio.to_thread(media_agent.download_file, img_url, out_img)
                    if Path(out_img).exists() and Path(out_img).stat().st_size > 3000:
                        used_pool_urls.add(img_url)
                        source_url_used = img_url
                        media_path = out_img
                        media_type = "image"
                        vision_curated = True
                        hook_alternate_urls = list(best_photo.get("alternate_urls") or [])
                        logger.info(
                            f"Scene {scene.scene_id}: 📸 Ảnh tuyển chọn ({src}) "
                            f"điểm {best_photo.get('curation_score', '-')}: {str(best_photo.get('title', ''))[:50]}"
                        )
            except Exception as fb3_e:
                logger.debug(f"Scene {scene.scene_id}: Fallback photo error: {fb3_e}")

        # Fallback 5: Sinh ảnh AI cứu hộ tổng quát nếu vẫn chưa có (hoặc cắt chéo từ ChapterPool cho Video Dài 16:9)
        if not media_path and is_long_form:
            _check_fetch_deadline()
            try:
                from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                cross_res = await asyncio.to_thread(
                    slice_any_available_chapter_master_for_scene,
                    media_dir=media_dir,
                    scene_id=scene.scene_id,
                    target_duration_sec=dur,
                    job_id=job_id,
                )
                if cross_res and cross_res.get("media_path"):
                    media_path = cross_res["media_path"]
                    media_type = "video"
                    vision_curated = True
                    prod_method = cross_res.get("production_method", "CHAPTER_POOL_16X9_JUMPCUT_HD")
                    chosen_strategy = "LONGFORM_16X9_DOCUMENTARY_VIDEO"
                    tool = "longform_chapter_pool_16x9"
                    source_url_used = str(cross_res.get("source_url") or "")
                    rationale = str(cross_res.get("rationale") or "Verified 16:9 Master Cross-Slice")
            except Exception:
                pass

        if not media_path and not is_long_form:
            _check_fetch_deadline()
            try:
                ai_img_path = str(media_dir / f"scene_{scene.scene_id:03d}_fb_ai_gen.jpg")
                clean_kw = clean_scene_search_query(kw, max_words=4)
                prompt_fb = vp if (vp and len(vp.strip()) > 10) else (clean_kw or "cinematic scene")
                res_img = await asyncio.to_thread(
                    image_agent.generate_scene_image,
                    prompt_fb,
                    ai_img_path,
                    "16:9" if is_long_form else "9:16",
                    "cinematic",
                    scene.scene_id,
                    vp,
                )
                if res_img and Path(res_img).exists() and Path(res_img).stat().st_size > 2000:
                    media_path = res_img
                    media_type = "image"
            except Exception:
                pass

        # Fallback 4: Graphic placeholder an toàn
        if not media_path:
            out_g = str(media_dir / f"scene_{scene.scene_id:03d}_graphic.png")
            from app.utils.ffmpeg_helper import create_placeholder_image
            create_placeholder_image(out_g, text=scene.visual_keyword or f"Scene {scene.scene_id}", scene_id=scene.scene_id)
            media_path = out_g
            media_type = "image"

        # ── HOOK MULTI-CUT (3 LẦN CHUYỂN CẢNH TRONG KHỞI ĐẦU VIDEO - SCENE 1) ──
        # Nếu Scene 1 là ảnh tĩnh, tải SONG SONG tối đa 3 ảnh ứng viên chất lượng cao (alternate_urls)
        # để tạo đủ 4 góc máy / 4 khung hình thật (3 nhát cắt chuyển cảnh dồn dập trong 3–4s đầu).
        if media_path and media_type == "image":
            if not pose_images:
                pose_images = [media_path]
            rag.verify_and_commit_in_job_image(
                job_id=job_id,
                scene_id=scene.scene_id,
                image_path=media_path,
                url=source_url_used or media_path,
            )

        if scene.scene_id == 1 and media_path and media_type == "image":
            need_hook_extra = max(0, 3 - len(pose_images))
            valid_alts = [u for u in hook_alternate_urls if u and u not in used_pool_urls][: max(need_hook_extra + 2, 3)]

            async def _dl_hook_alt(idx_alt: int, u_alt: str) -> Optional[Tuple[str, str]]:
                alt_out = str(media_dir / f"scene_001_hook_cand{idx_alt + 2}.jpg")
                try:
                    await asyncio.to_thread(media_agent.download_file, u_alt, alt_out)
                    if Path(alt_out).exists() and Path(alt_out).stat().st_size > 3000:
                        return alt_out, u_alt
                    Path(alt_out).unlink(missing_ok=True)
                except Exception:
                    Path(alt_out).unlink(missing_ok=True)
                return None

            if need_hook_extra > 0 and valid_alts:
                alt_results = await asyncio.gather(*[_dl_hook_alt(i, u) for i, u in enumerate(valid_alts)])
                for pair in alt_results:
                    if not pair:
                        continue
                    alt_p, u_alt = pair
                    if len(pose_images) < 4:
                        final_hk_p = str(media_dir / f"scene_001_hook_cut{len(pose_images) + 1}.jpg")
                        try:
                            Path(alt_p).replace(final_hk_p)
                        except Exception:
                            final_hk_p = alt_p
                        if rag.verify_and_commit_in_job_image(
                            job_id=job_id, scene_id=1, image_path=final_hk_p, url=u_alt
                        ):
                            pose_images.append(final_hk_p)
                            used_pool_urls.add(u_alt)
                        else:
                            Path(final_hk_p).unlink(missing_ok=True)
                    else:
                        Path(alt_p).unlink(missing_ok=True)

            if len(pose_images) >= 1:
                logger.info(
                    f"Scene 1: ⚡ [Hook Multi-Cut Ảnh Truyền Thống] Đã chuẩn bị {len(pose_images)} khung hình thật độc lập "
                    f"-> Dựng hook_motion_001.mp4 (≥3 lần chuyển cảnh dồn dập)"
                )
                try:
                    from app.utils.ffmpeg_helper import render_opening_hook_multi_cut, VIDEO_DURATION_SAFETY_MARGIN
                    hook_out_p = str(work_dir / "hook_motion_001.mp4")
                    trans_dur = 0.18 if len(script.scenes) > 1 else (0.15 + VIDEO_DURATION_SAFETY_MARGIN)
                    hook_dur_sec = max(dur, 1.0) + trans_dur
                    render_opening_hook_multi_cut(
                        image_paths=pose_images,
                        duration_seconds=hook_dur_sec,
                        output_path=hook_out_p,
                        width=1920 if is_long_form else 1080,
                        height=1080 if is_long_form else 1920,
                        fps=30,
                        min_cuts=3,
                        is_scifi=is_scifi_val,
                        niche=current_niche,
                        is_hook_scene=True,
                    )
                    if Path(hook_out_p).exists() and Path(hook_out_p).stat().st_size > 10_000:
                        media_path = hook_out_p
                        media_type = "hook_ready_video"
                        prod_method = f"CLIMAX_IMAGES_LIVE_FX_{str(current_niche).upper()}_3CUT"
                except Exception as e_hk_img:
                    logger.debug(f"Scene 1: Lỗi dựng hook_motion_001 từ ảnh truyền thống: {e_hk_img}")

        if source_url_used and media_path:
            try:
                record_used_media(
                    url=source_url_used,
                    query=search_query or kw,
                    job_id=job_id,
                    source=str(prod_method or chosen_strategy or "media_fetch").lower(),
                    media_path=media_path,
                )
            except Exception:
                pass

        return {
            "scene_id": scene.scene_id,
            "media_path": media_path,
            "media_type": media_type,
            "pose_images": pose_images if pose_images else None,
            "alternate_urls": hook_alternate_urls,
            "vision_curated": vision_curated,
            "visual_keyword": search_query or scene.visual_keyword,
            "raw_visual_keyword": scene.visual_keyword,
            "fallback_broad_query": str(sc_strat.get("fallback_broad_query") or getattr(scene, "stock_query", "") or ""),
            "storyline_search_queries": list(sc_strat.get("storyline_search_queries") or []),
            "visual_prompt": getattr(scene, 'visual_prompt', None),
            "narration": scene.narration,
            "camera_motion": getattr(scene, "camera_motion", None) or camera_motion,
            "sfx_cue": getattr(scene, "sfx_cue", None) or sfx_cue,
            "highlight_words": getattr(scene, "highlight_words", None),
            "overlay_badge": getattr(scene, "overlay_badge", None),
            "visual_strategy": chosen_strategy,
            "production_method": prod_method,
            "tool": tool,
            "strategy_rationale": rationale,
            "source_url": source_url_used,
            "full_peak_path": full_peak_path,
            "full_peak_duration_sec": full_peak_duration_sec,
        }

    async def _guarded_fetch(idx: int, scene) -> Dict[str, Any]:
        from app.utils.ffmpeg_helper import is_completed_clean_mp4
        from app.agents.curation.scene_context_analyzer_mixin import bind_scene_cancellation
        scene_timeout_sec = 105.0 if is_long_form else 65.0
        async with sem:
            scene_cancel_ev = threading.Event()
            scene_deadline_ts = time.monotonic() + (scene_timeout_sec - 2.5)
            bind_scene_cancellation(scene_cancel_ev, scene_deadline_ts)
            try:
                res = await asyncio.wait_for(
                    _fetch_one_scene(
                        idx,
                        scene,
                        cancel_event=scene_cancel_ev,
                        deadline_ts=scene_deadline_ts,
                    ),
                    timeout=scene_timeout_sec,
                )
            except (asyncio.TimeoutError, Exception) as _e_gf_to:
                scene_cancel_ev.set()
                fb_cancel_ev = threading.Event()
                bind_scene_cancellation(fb_cancel_ev, time.monotonic() + 20.0)
                logger.warning(
                    f"Scene {scene.scene_id}: ⏱️ Quá hạn/chuyển nguồn nhanh khi tải media ({_e_gf_to}) -> Kiểm tra file đã hoàn tất trên đĩa hoặc chuyển nguồn độc lập mới!"
                )
                res = None
                # Chỉ phục hồi các file .mp4 đã ghi hoàn tất (có moov atom & duration >= 1.2s, tuyệt đối bỏ qua file tạm _fftmp_ / .tmp*)
                early_vids = sorted(
                    [
                        p for p in media_dir.glob(f"scene_{scene.scene_id:03d}_*.mp4")
                        if is_completed_clean_mp4(p, min_size_bytes=15_000, min_duration_ms=1200)
                    ],
                    key=_safe_mtime,
                    reverse=True,
                )
                early_imgs = sorted(
                    [
                        p for p in media_dir.glob(f"scene_{scene.scene_id:03d}_*.jpg")
                        if _safe_size(p) > 3_000 and "_cand" not in p.name
                    ],
                    key=lambda p: (0 if "_cut" not in p.name else 1, p.name),
                )
                if early_vids:
                    res = {
                        "scene_id": scene.scene_id,
                        "media_path": str(early_vids[0]),
                        "media_type": "hook_ready_video" if scene.scene_id == 1 else "video",
                        "pose_images": None,
                        "vision_curated": True,
                        "visual_keyword": getattr(scene, "visual_keyword", "") or primary_entity,
                        "visual_prompt": getattr(scene, "visual_prompt", None),
                        "narration": scene.narration,
                        "camera_motion": getattr(scene, "camera_motion", None) or "zoom_in",
                        "sfx_cue": getattr(scene, "sfx_cue", None) or "hit",
                        "highlight_words": getattr(scene, "highlight_words", None),
                        "overlay_badge": getattr(scene, "overlay_badge", None),
                        "visual_strategy": "STOCK_VIDEO",
                        "production_method": "VIDEO_FIRST_BODY_BROLL",
                        "tool": "search_pexels_pixabay_stock_video",
                        "strategy_rationale": f"Recovered completed video from disk ({early_vids[0].name})",
                        "source_url": str(early_vids[0]),
                    }
                elif early_imgs and scene.scene_id != 1:
                    rec_early_poses = [str(p) for p in early_imgs[:2]]
                    res = {
                        "scene_id": scene.scene_id,
                        "media_path": rec_early_poses[0],
                        "media_type": "image",
                        "pose_images": rec_early_poses,
                        "vision_curated": True,
                        "visual_keyword": getattr(scene, "visual_keyword", "") or primary_entity,
                        "visual_prompt": getattr(scene, "visual_prompt", None),
                        "narration": scene.narration,
                        "camera_motion": getattr(scene, "camera_motion", None) or "zoom_in",
                        "sfx_cue": getattr(scene, "sfx_cue", None) or "hit",
                        "highlight_words": getattr(scene, "highlight_words", None),
                        "overlay_badge": getattr(scene, "overlay_badge", None),
                        "visual_strategy": "GEMINI_SEARCH_3D",
                        "production_method": "GOOGLE_TOP_IMAGE_FALLBACK",
                        "tool": "search_google_top_cinematic_image",
                        "strategy_rationale": f"Recovered completed image(s) from disk ({len(rec_early_poses)} shots)",
                        "source_url": rec_early_poses[0],
                    }
                if not res and is_long_form:
                    try:
                        from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                        lf_to_dur = float(getattr(scene, "estimated_seconds", 8.0) or 8.0)
                        for _sa_to in (state.get("scenes_audio") or []):
                            if isinstance(_sa_to, dict) and int(_sa_to.get("scene_id") or 0) == int(scene.scene_id):
                                if _sa_to.get("audio_duration_ms"):
                                    lf_to_dur = max(float(_sa_to["audio_duration_ms"]) / 1000.0, 2.5)
                                break
                        lf_slice_res = await asyncio.to_thread(
                            slice_any_available_chapter_master_for_scene,
                            media_dir=media_dir,
                            scene_id=scene.scene_id,
                            target_duration_sec=lf_to_dur,
                            job_id=job_id,
                        )
                        if lf_slice_res and lf_slice_res.get("media_path") and Path(lf_slice_res["media_path"]).exists():
                            res = {
                                "scene_id": scene.scene_id,
                                "media_path": lf_slice_res["media_path"],
                                "media_type": "hook_ready_video" if scene.scene_id == 1 else "video",
                                "pose_images": None,
                                "vision_curated": True,
                                "visual_keyword": getattr(scene, "visual_keyword", "") or primary_entity,
                                "visual_prompt": getattr(scene, "visual_prompt", None),
                                "narration": scene.narration,
                                "camera_motion": getattr(scene, "camera_motion", None) or "zoom_in",
                                "sfx_cue": getattr(scene, "sfx_cue", None) or "hit",
                                "highlight_words": getattr(scene, "highlight_words", None),
                                "overlay_badge": getattr(scene, "overlay_badge", None),
                                "visual_strategy": "LONGFORM_16X9_DOCUMENTARY_VIDEO",
                                "production_method": lf_slice_res.get("production_method", "CHAPTER_POOL_16X9_JUMPCUT_HD"),
                                "tool": "longform_chapter_pool_cross_slice",
                                "strategy_rationale": lf_slice_res.get("rationale", "ChapterPool Cross-Slice on Timeout"),
                                "source_url": str(lf_slice_res.get("source_url") or lf_slice_res["media_path"]),
                            }
                    except Exception as _e_lf_to:
                        logger.debug(f"Scene {scene.scene_id}: Timeout ChapterPool cross-slice skip: {_e_lf_to}")
                if not res:
                    try:
                        sc_s = strategy_map.get(scene.scene_id) or {}
                        fresh_q = clean_scene_search_query(
                            sc_s.get("youtube_search_query")
                            or sc_s.get("smart_image_query")
                            or getattr(scene, "stock_query", "")
                            or getattr(scene, "visual_keyword", "")
                            or primary_entity
                            or state.get("topic", ""),
                            max_words=4,
                        )
                        prim_toks = [
                            t.lower() for t in re.findall(r"[a-zA-Z]{3,}", str(primary_entity or ""))
                            if t.lower() not in {"the", "and", "with", "from"}
                        ]
                        if prim_toks and not any(pt in fresh_q.lower() for pt in prim_toks[:3]):
                            fresh_q = clean_scene_search_query(f"{primary_entity} {fresh_q}", max_words=4) or fresh_q
                        fast_va = dict(batch_analyses.get(scene.scene_id) or {})
                        if is_long_form:
                            fast_va["video_format"] = "long"
                        best_fresh_vid = await asyncio.wait_for(
                            asyncio.to_thread(
                                media_agent.search_best_video_clip,
                                fresh_q,
                                used_pool_urls,
                                fast_va,
                                scene.narration,
                                state.get("topic", ""),
                            ),
                            timeout=18.0,
                        )
                        if best_fresh_vid and best_fresh_vid.get("url"):
                            cand_u_list = [
                                str(best_fresh_vid["url"]),
                                *[str(u) for u in (best_fresh_vid.get("_vision_alternates") or []) if u],
                            ]
                            cand_fresh_title = str(best_fresh_vid.get("title") or fresh_q)
                            out_fresh_v = str(media_dir / f"scene_{scene.scene_id:03d}_fresh_stock.mp4")
                            from app.services.media.longform_chapter_pool import is_clip_visually_unique_in_job as _chk_fresh_uniq
                            for cand_u in cand_u_list[:4]:
                                if not cand_u:
                                    continue
                                if not rag.try_claim_in_job_video(
                                    job_id=job_id,
                                    vid_id=cand_u,
                                    url=cand_u,
                                    title=cand_fresh_title,
                                    scene_id=scene.scene_id,
                                ):
                                    used_pool_urls.add(cand_u)
                                    continue
                                await asyncio.wait_for(
                                    asyncio.to_thread(media_agent.download_file, cand_u, out_fresh_v),
                                    timeout=12.0,
                                )
                                if is_completed_clean_mp4(out_fresh_v, min_size_bytes=15_000, min_duration_ms=1000):
                                    ok_f_sig, _ = rag.verify_and_commit_in_job_clip(
                                        job_id=job_id,
                                        scene_id=scene.scene_id,
                                        media_path=out_fresh_v,
                                        vid_id=cand_u,
                                        url=cand_u,
                                        title=cand_fresh_title,
                                    )
                                    if ok_f_sig and _chk_fresh_uniq(out_fresh_v, job_id, scene_id=scene.scene_id):
                                        used_pool_urls.add(cand_u)
                                        res = {
                                            "scene_id": scene.scene_id,
                                            "media_path": out_fresh_v,
                                            "media_type": "video",
                                            "pose_images": None,
                                            "vision_curated": True,
                                            "visual_keyword": fresh_q or scene.visual_keyword,
                                            "visual_prompt": getattr(scene, "visual_prompt", None),
                                            "narration": scene.narration,
                                            "camera_motion": getattr(scene, "camera_motion", None) or "zoom_in",
                                            "sfx_cue": getattr(scene, "sfx_cue", None) or "hit",
                                            "highlight_words": getattr(scene, "highlight_words", None),
                                            "overlay_badge": getattr(scene, "overlay_badge", None),
                                            "visual_strategy": "STOCK_VIDEO",
                                            "production_method": "VIDEO_FIRST_BODY_BROLL",
                                            "tool": "search_pexels_pixabay_stock_video",
                                            "strategy_rationale": f"Fast-switch fresh stock video ({fresh_q})",
                                            "source_url": cand_u,
                                        }
                                        break
                                    else:
                                        Path(out_fresh_v).unlink(missing_ok=True)
                                        rag.release_in_job_video_claim(
                                            job_id=job_id, vid_id=cand_u, url=cand_u, title=cand_fresh_title, scene_id=scene.scene_id
                                        )
                                else:
                                    Path(out_fresh_v).unlink(missing_ok=True)
                                    rag.release_in_job_video_claim(
                                        job_id=job_id, vid_id=cand_u, url=cand_u, title=cand_fresh_title, scene_id=scene.scene_id
                                    )
                        if not res:
                            from app.graph.tools.media_tools import execute_google_cinematic_image_sync
                            out_fresh_img = str(media_dir / f"scene_{scene.scene_id:03d}_fresh_gsearch.jpg")
                            img_f_res = await asyncio.wait_for(
                                asyncio.to_thread(
                                    execute_google_cinematic_image_sync,
                                    smart_query=fresh_q,
                                    output_path=out_fresh_img,
                                    narration=scene.narration,
                                    topic=state.get("topic", ""),
                                    used_urls=used_pool_urls,
                                    is_hook_scene=(scene.scene_id == 1),
                                    subject_entity=primary_entity or state.get("topic", ""),
                                    search_scope="SCENE_SPECIFIC",
                                    overall_script_query=primary_entity or state.get("topic", ""),
                                    job_id=job_id,
                                    scene_id=scene.scene_id,
                                    target_image_count=1,
                                ),
                                timeout=18.0,
                            )
                            if img_f_res and img_f_res.get("media_path"):
                                res = {
                                    "scene_id": scene.scene_id,
                                    "media_path": img_f_res["media_path"],
                                    "media_type": "image",
                                    "pose_images": list(img_f_res.get("pose_images") or [img_f_res["media_path"]]),
                                    "vision_curated": True,
                                    "visual_keyword": fresh_q or scene.visual_keyword,
                                    "visual_prompt": getattr(scene, "visual_prompt", None),
                                    "narration": scene.narration,
                                    "camera_motion": getattr(scene, "camera_motion", None) or "zoom_in",
                                    "sfx_cue": getattr(scene, "sfx_cue", None) or "hit",
                                    "highlight_words": getattr(scene, "highlight_words", None),
                                    "overlay_badge": getattr(scene, "overlay_badge", None),
                                    "visual_strategy": "GEMINI_SEARCH_3D",
                                    "production_method": "GOOGLE_TOP_IMAGE_FALLBACK",
                                    "tool": "search_google_top_cinematic_image",
                                    "strategy_rationale": f"Fast-switch fresh Google Top Image ({fresh_q})",
                                    "source_url": str(img_f_res.get("source_url") or ""),
                                }
                    except Exception:
                        res = None
            if not res or not isinstance(res, dict):
                # Kiểm tra xem _fetch_one_scene đã kịp tải xong file video/ảnh thật nào cho cảnh này trên đĩa trước khi timeout chưa!
                existing_disk_vids = sorted(
                    [
                        p for p in media_dir.glob(f"scene_{scene.scene_id:03d}_*.mp4")
                        if is_completed_clean_mp4(p, min_size_bytes=15_000, min_duration_ms=1200)
                    ],
                    key=_safe_mtime,
                    reverse=True,
                )
                existing_disk_imgs = sorted(
                    [
                        p for p in media_dir.glob(f"scene_{scene.scene_id:03d}_*.jpg")
                        if _safe_size(p) > 3_000 and "_cand" not in p.name
                    ],
                    key=lambda p: (0 if "_cut" not in p.name else 1, p.name),
                )
                if existing_disk_vids:
                    v_disk = str(existing_disk_vids[0])
                    res = {
                        "scene_id": scene.scene_id,
                        "media_path": v_disk,
                        "media_type": "video",
                        "pose_images": None,
                        "vision_curated": True,
                        "visual_keyword": scene.visual_keyword,
                        "visual_prompt": getattr(scene, "visual_prompt", None),
                        "narration": scene.narration,
                        "camera_motion": getattr(scene, "camera_motion", None) or "zoom_in",
                        "sfx_cue": getattr(scene, "sfx_cue", None) or "hit",
                        "highlight_words": getattr(scene, "highlight_words", None),
                        "overlay_badge": getattr(scene, "overlay_badge", None),
                        "visual_strategy": "STOCK_VIDEO",
                        "production_method": "VIDEO_FIRST_BODY_BROLL",
                        "tool": "recovered_disk_video",
                        "strategy_rationale": "Recovered completed video from disk on timeout",
                    }
                elif existing_disk_imgs:
                    img_list_disk = [str(p) for p in existing_disk_imgs[:2]]
                    res = {
                        "scene_id": scene.scene_id,
                        "media_path": img_list_disk[0],
                        "media_type": "image",
                        "pose_images": img_list_disk,
                        "vision_curated": True,
                        "visual_keyword": scene.visual_keyword,
                        "visual_prompt": getattr(scene, "visual_prompt", None),
                        "narration": scene.narration,
                        "camera_motion": getattr(scene, "camera_motion", None) or "zoom_in",
                        "sfx_cue": getattr(scene, "sfx_cue", None) or "hit",
                        "highlight_words": getattr(scene, "highlight_words", None),
                        "overlay_badge": getattr(scene, "overlay_badge", None),
                        "visual_strategy": "GEMINI_SEARCH_3D",
                        "production_method": "GOOGLE_TOP_IMAGE_FALLBACK",
                        "tool": "recovered_disk_image",
                        "strategy_rationale": "Recovered completed Google Top Image from disk on timeout",
                    }
            if not res or not isinstance(res, dict):
                logger.warning(f"Scene {scene.scene_id}: _fetch_one_scene không trả về dict hợp lệ, kích hoạt fallback đồ họa an toàn.")
                out_g = str(media_dir / f"scene_{scene.scene_id:03d}_graphic.png")
                from app.utils.ffmpeg_helper import create_placeholder_image
                create_placeholder_image(out_g, text=scene.visual_keyword or f"Scene {scene.scene_id}", scene_id=scene.scene_id)
                res = {
                    "scene_id": scene.scene_id,
                    "media_path": out_g,
                    "media_type": "image",
                    "pose_images": [out_g],
                    "visual_keyword": scene.visual_keyword,
                    "visual_prompt": getattr(scene, 'visual_prompt', None),
                    "narration": scene.narration,
                    "camera_motion": getattr(scene, "camera_motion", None) or "zoom_in",
                    "sfx_cue": getattr(scene, "sfx_cue", None) or "hit",
                    "highlight_words": getattr(scene, "highlight_words", None),
                    "overlay_badge": getattr(scene, "overlay_badge", None),
                    "visual_strategy": "FALLBACK",
                    "production_method": "GRAPHIC_FALLBACK",
                    "tool": "ffmpeg_placeholder",
                    "strategy_rationale": "Fallback safety guard",
                }
            nonlocal completed_count
            async with completed_lock:
                completed_count += 1
                cur_done = completed_count
                cur_pct = min(64, 55 + int((cur_done / max(total_scenes, 1)) * 10))

            if job_id:
                from app.graph.runner import report_job_progress
                await report_job_progress(
                    job_id,
                    "MEDIA_FETCHING",
                    f"🎬 Đang thu thập/tạo tư liệu Cảnh {scene.scene_id} ({cur_done}/{total_scenes})...",
                    cur_pct
                )
            return res

    # ── PHA A (CHỈ DÀNH CHO VIDEO DÀI 16:9): TẢI TRƯỚC BỘ MASTER 16:9 THEO CHƯƠNG (3 CẢNH/CHƯƠNG, 7 LUỒNG SẠCH, 0 XUNG ĐỘT) ──
    if is_long_form:
        try:
            from concurrent.futures import ThreadPoolExecutor as _LFThreadPool
            _cur_loop = asyncio.get_running_loop()
            if not getattr(_cur_loop, "_lf_pool_expanded", False):
                _cur_loop.set_default_executor(_LFThreadPool(max_workers=36, thread_name_prefix="lf_io"))
                setattr(_cur_loop, "_lf_pool_expanded", True)
        except Exception:
            pass
    if is_long_form and str(current_niche or "").lower() not in ("psychology", "satirical_psychology_animation", "doodle", "philosophy"):
        from app.services.media.longform_chapter_pool import fetch_or_slice_longform_chapter_clip_sync
        prewarm_sem = asyncio.Semaphore(7)
        scenes_audio_pw = state.get("scenes_audio", [])

        async def _prewarm_one_chapter(lead_scene) -> None:
            sc_st = strategy_map.get(lead_scene.scene_id, {})
            if sc_st.get("tool_name") == "render_doodle_2d_explainer_scene":
                return
            pw_parts = [
                p for p in (
                    sc_st.get("youtube_search_query"),
                    sc_st.get("action_or_concept_query"),
                    getattr(lead_scene, "stock_query", None),
                    getattr(lead_scene, "visual_keyword", None),
                    state.get("topic", ""),
                )
                if p
            ]
            pw_q = "|||".join(dict.fromkeys(str(p).strip() for p in pw_parts if str(p).strip()))
            pw_dur = float(getattr(lead_scene, "estimated_seconds", 8.0) or 8.0)
            if scenes_audio_pw:
                sa_m = next((a for a in scenes_audio_pw if a.get("scene_id") == lead_scene.scene_id), None)
                if sa_m and sa_m.get("audio_duration_ms"):
                    pw_dur = max(float(sa_m["audio_duration_ms"]) / 1000.0, 2.5)
            async with prewarm_sem:
                pw_cancel = threading.Event()
                pw_dl = time.monotonic() + 63.0
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            fetch_or_slice_longform_chapter_clip_sync,
                            job_id=job_id,
                            scene_id=lead_scene.scene_id,
                            query=pw_q,
                            primary_entity=primary_entity or state.get("topic", ""),
                            topic=state.get("topic", ""),
                            narration=lead_scene.narration,
                            target_duration_sec=pw_dur,
                            media_dir=media_dir,
                            used_bvids=longform_used_bvids,
                            used_urls=used_pool_urls,
                            bvid_lock=longform_bvid_lock,
                            media_agent=media_agent,
                            is_scifi=is_scifi_val,
                            scenes_per_chapter=3,
                            cancel_event=pw_cancel,
                            deadline_ts=pw_dl,
                            chapter_key_prefix="chapter",
                        ),
                        timeout=65.0,
                    )
                except Exception as e_pw:
                    pw_cancel.set()
                    logger.debug(f"[ChapterPool Prewarm] Chapter lead scene {lead_scene.scene_id} skip: {e_pw}")

        chapter_lead_scenes = [s for s in script.scenes if ((int(s.scene_id) - 1) % 3) == 0]
        if chapter_lead_scenes:
            logger.info(f"🎬 [LongForm Phase-A] Đang tiền tải {len(chapter_lead_scenes)} Chapter Masters 16:9 (3 cảnh/chương, 6 luồng sạch)...")
            await asyncio.gather(*[_prewarm_one_chapter(ls) for ls in chapter_lead_scenes])

    tasks = [_guarded_fetch(i, s) for i, s in enumerate(script.scenes)]
    scenes_media = list(await asyncio.gather(*tasks))

    # ── Bước A.0: CỔNG RÀ SOÁT ĐỒNG BỘ CHỐNG TRÙNG VIDEO LIÊN TIẾP TRONG CÙNG 1 JOB (POST-GATHER INTRA-VIDEO DEDUP GATE) ──
    # Đăng ký toàn bộ các clip video đã thu thập từ các luồng song song vào bộ nhớ dHash 3-mốc (Đầu-Giữa-Cuối).
    # Nếu phát hiện bất kỳ phân cảnh sau nào vô tình trùng khung hình/nguồn với phân cảnh trước,
    # lập tức thu hồi phân cảnh trùng đó để Bước B (`Real-Motion Floor & Ending Rescue`) tự động lấy clip mới 100% khác biệt!
    try:
        from app.services.media.longform_chapter_pool import is_clip_visually_unique_in_job as _post_gather_uniq
        seen_real_video_paths: Set[str] = set()
        for idx_pg, sc_pg in enumerate(scenes_media):
            if not isinstance(sc_pg, dict):
                continue
            sid_pg = int(sc_pg.get("scene_id") or (idx_pg + 1))
            mp_pg = str(sc_pg.get("media_path") or "").strip()
            if not mp_pg or not mp_pg.lower().endswith((".mp4", ".mov", ".webm")) or not Path(mp_pg).exists():
                continue
            abs_mp_pg = str(Path(mp_pg).resolve())
            is_dup_pg = False
            if abs_mp_pg in seen_real_video_paths:
                is_dup_pg = True
            elif not _post_gather_uniq(abs_mp_pg, job_id, scene_id=sid_pg):
                is_dup_pg = True
            if is_dup_pg and sid_pg > 1:
                logger.warning(
                    f"🚫 [Post-Gather Intra-Video Dedup] Phát hiện Cảnh #{sid_pg} ('{Path(mp_pg).name}') "
                    f"bị trùng lặp khung hình với cảnh trước -> Thu hồi ngay để tải video mới độc lập ở Bước B!"
                )
                sc_pg["media_path"] = ""
                sc_pg["media_type"] = "image"
                sc_pg["production_method"] = "DUPLICATE_DEMOTED_FOR_RESCUE"
            else:
                seen_real_video_paths.add(abs_mp_pg)
    except Exception as _e_pg_dedup:
        logger.debug(f"[Post-Gather Intra-Video Dedup] warning: {_e_pg_dedup}")

    # ══════════════════════════════════════════════════════════════════════════
    # BỘ LẮP GHÉP TIMELINE ƯU TIÊN VIDEO PEAK DÀI -> ẢNH TĨNH (VIDEO-FIRST TIMELINE ASSEMBLER)
    # 1. Mở rộng các đoạn Peak dài (`full_peak_path` 6s-14s) chảy liền mạch qua các phân cảnh kế tiếp
    #    thay vì khóa cứng 3 giây rồi cắt cụt sang ảnh tĩnh!
    # 2. Tổng hợp & ưu tiên xếp các phân đoạn Video lên trước, các phân đoạn Ảnh tĩnh ở phía sau
    #    để tạo luồng video liền mạch trước khi ghép giọng đọc (voiceover) và phụ đề (subtitles).
    # ══════════════════════════════════════════════════════════════════════════
    scenes_audio_list = state.get("scenes_audio", [])

    def _get_slot_dur(slot_idx: int) -> float:
        sid = slot_idx + 1
        if scenes_audio_list:
            sc_a = next((a for a in scenes_audio_list if isinstance(a, dict) and int(a.get("scene_id") or 0) == sid), None)
            if sc_a and sc_a.get("audio_duration_ms"):
                return max(float(sc_a["audio_duration_ms"]) / 1000.0, 1.2)
        if 0 <= slot_idx < len(script.scenes):
            return max(float(getattr(script.scenes[slot_idx], "estimated_seconds", 4.0) or 4.0), 1.2)
        return 3.8

    # ── Bước A: ĐÃ TẮT CƠ CHẾ CẮT NỐI TIẾP 1 VIDEO SANG CẢNH KẾ TIẾP (`LONG_PEAK_CONTINUOUS_FLOW`) ──
    # Quy tắc Vàng: 1 Cảnh = 1 Nguồn Video Độc Lập 100% (Tuyệt đối không cắt phần dư của Cảnh N sang Cảnh N+1
    # để ngăn chặn triệt để hiện tượng lặp lại cùng 1 bối cảnh/chuyển động máy quay giữa các phân cảnh).

    # ── Bước B: CỔNG BẢO ĐẢM 100% VIDEO CHUYỂN ĐỘNG THẬT Ở ĐẦU (CẢNH 1, 2) & CUỐI VIDEO (CẢNH CUỐI, ÁP CUỐI) + TỶ LỆ VIDEO >= 75% ──
    try:
        def _is_real_video_slot(sc_dict: Any) -> bool:
            if not isinstance(sc_dict, dict):
                return False
            mt = str(sc_dict.get("media_type") or "image").lower()
            mp = str(sc_dict.get("media_path") or "").lower()
            pm = str(sc_dict.get("production_method") or "").upper()
            vs = str(sc_dict.get("visual_strategy") or "").upper()
            # Không coi các file ảnh tĩnh zoompan (_i2v, _fb_motion, _anim, _motion3d) hoặc multi-cut ảnh tĩnh là video chuyển động thật
            if any(tag in mp for tag in ("_i2v", "_fb_motion", "_anim", "_motion3d", "_gsearch_3d", "_creature_3d")):
                return False
            if any(
                tag in pm
                for tag in (
                    "CLIMAX_IMAGES_LIVE_FX",
                    "IMAGE_TO_VIDEO",
                    "STOCK_PHOTO_I2V",
                    "GOOGLE_TOP_IMAGE",
                    "GEMINI_SEARCH_3D",
                    "CREATURE_ART",
                    "REFINED_IMAGE_TO_VIDEO",
                    "REFINED_CLEAN_MULTI_IMAGE",
                )
            ):
                return False
            if vs in ("HOOK_MULTI_IMAGE_RAPID_PEAK", "GEMINI_SEARCH_3D", "CREATURE_ART", "AI_IMAGE"):
                return False
            return mt in ("video", "hook_ready_video") and mp.endswith((".mp4", ".mov", ".webm"))

        n_slots = len(scenes_media)
        min_vid_ratio = 0.80 if is_long_form else 0.85
        min_video_slots = max(5, int((n_slots * min_vid_ratio) + 0.999)) if n_slots >= 5 else n_slots
        current_video_slots = sum(1 for s_it in scenes_media if _is_real_video_slot(s_it))

        # Bắt buộc 100% Cảnh 1 (0), Cảnh 2 (1), Cảnh cuối (n_slots - 1) và Cảnh áp cuối (n_slots - 2) phải là Video chuyển động thật
        mandatory_slot_indices: List[int] = []
        for m_idx in [n_slots - 1, n_slots - 2, 0, 1]:
            if 0 <= m_idx < n_slots and m_idx not in mandatory_slot_indices:
                if not _is_real_video_slot(scenes_media[m_idx]):
                    mandatory_slot_indices.append(m_idx)

        rescue_candidates_idx: List[int] = list(mandatory_slot_indices)
        for idx_mid in range(n_slots - 1, -1, -1):
            if idx_mid not in rescue_candidates_idx and not _is_real_video_slot(scenes_media[idx_mid]):
                rescue_candidates_idx.append(idx_mid)

        needed_rescues = max(
            len(mandatory_slot_indices),
            min_video_slots - current_video_slots,
        )
        if needed_rescues > 0 and rescue_candidates_idx:
            from app.graph.tools.media_tools import execute_youtube_short_peak_clone_sync
            from app.utils.ffmpeg_helper import get_duration_ms, trim_video
            logger.info(
                f"🎬 [Real-Motion Floor & Ending Rescue] Hiện có {current_video_slots}/{n_slots} cảnh video thật "
                f"(bắt buộc 100% đầu & cuối video là video thật, tối thiểu {min_video_slots}/{n_slots} cảnh >= 85%). "
                f"Đang nâng cấp {min(needed_rescues, len(rescue_candidates_idx))} cảnh sang Video chuyển động thật độc lập..."
            )
            overall_rescue_q = clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=4)
            rescue_angle_facets = [
                "cinematic motion closeup",
                "dramatic real footage",
                "intense slow motion",
                "atmospheric closeup",
            ]
            portrait_broll_facets = [
                "close up eyes dark lighting",
                "silhouette person walking night",
                "dramatic portrait shadow",
                "person thinking dark room",
                "man walking rain night",
                "mysterious face shadow closeup",
            ]
            is_psych_niche = str(current_niche).lower() in (
                "psychology",
                "satirical_psychology_animation",
                "doodle",
                "philosophy",
                "self_improvement",
            )
            rescue_sem = asyncio.Semaphore(6 if is_long_form else 3)

            async def _rescue_single_scene_motion(
                r_pos: int,
                r_idx: int,
                cancel_event: Optional[threading.Event] = None,
                deadline_ts: Optional[float] = None,
            ) -> None:
                def _rescue_cancelled() -> bool:
                    return (cancel_event is not None and cancel_event.is_set()) or (
                        deadline_ts is not None and (deadline_ts - time.monotonic()) <= 1.8
                    )

                if True:
                    r_sc = scenes_media[r_idx]
                    r_sid = int(r_sc.get("scene_id") or (r_idx + 1))
                    r_dur = _get_slot_dur(r_idx)
                    sc_obj_r = script.scenes[r_idx] if (script and hasattr(script, "scenes") and 0 <= r_idx < len(script.scenes)) else None
                    r_stock_q = clean_scene_search_query(
                        getattr(sc_obj_r, "stock_query", "") or r_sc.get("fallback_broad_query") or "",
                        max_words=4,
                    )
                    r_kw = clean_scene_search_query(
                        r_sc.get("visual_keyword") or r_stock_q or primary_entity or state.get("topic", ""),
                        max_words=4,
                    ) or overall_rescue_q
                    r_out_mp4 = str(media_dir / f"scene_{r_sid:03d}_motion_rescue.mp4")
                    r_subj = clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=2) or r_kw
                    r_tool = str(r_sc.get("tool") or "")
                    is_portrait_tool = (r_tool == "fetch_clean_cinematic_portrait_or_broll") or is_psych_niche
                    already_tried_scene_kw = r_tool in (
                        "clone_youtube_short_peak_clip",
                        "search_pexels_pixabay_stock_video",
                    )
                    rescued_ok = False

                    # 0A. Nếu là Video Dài 16:9 (Long-Form), ưu tiên cắt/tải qua Chapter-Pool 16:9 Widescreen
                    if not rescued_ok and is_long_form and not _rescue_cancelled():
                        try:
                            lf_resc = await asyncio.to_thread(
                                _fetch_longform_16x9_broll_clip_sync,
                                scene_id=r_sid,
                                query=r_kw,
                                primary_entity=primary_entity or state.get("topic", ""),
                                topic=state.get("topic", ""),
                                narration=str(r_sc.get("narration") or state.get("topic", "")),
                                target_duration_sec=r_dur,
                                media_dir=media_dir,
                                used_bvids=longform_used_bvids,
                                used_urls=used_pool_urls,
                                bvid_lock=longform_bvid_lock,
                                media_agent=media_agent,
                                is_scifi=is_scifi_val,
                                job_id=job_id,
                                output_override_path=r_out_mp4,
                                cancel_event=cancel_event,
                                deadline_ts=deadline_ts,
                            )
                            if not (lf_resc and lf_resc.get("media_path") and Path(lf_resc["media_path"]).exists()):
                                from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                                lf_resc = await asyncio.to_thread(
                                    slice_any_available_chapter_master_for_scene,
                                    media_dir=media_dir,
                                    scene_id=r_sid,
                                    target_duration_sec=r_dur,
                                    job_id=job_id,
                                    output_override_path=r_out_mp4,
                                )
                            if lf_resc and lf_resc.get("media_path") and Path(lf_resc["media_path"]).exists():
                                r_sc["media_path"] = lf_resc["media_path"]
                                r_sc["media_type"] = "hook_ready_video" if r_sid == 1 else "video"
                                r_sc["pose_images"] = []
                                r_sc["production_method"] = lf_resc.get("production_method", "MOTION_FLOOR_RESCUE_16X9")
                                r_sc["visual_strategy"] = "LONGFORM_16X9_DOCUMENTARY_VIDEO"
                                rescued_ok = True
                                logger.info(f"  ✅ [Real-Motion Floor Rescue 16:9] Scene {r_sid}: Đã nâng cấp sang Video 16:9 ({Path(lf_resc['media_path']).name})")
                        except Exception as _re_lf:
                            logger.debug(f"[Real-Motion Floor Rescue] Scene {r_sid} 16:9 rescue skip: {_re_lf}")

                    # 0B. Nếu là phân cảnh Tâm lý / Triết học (`fetch_clean_cinematic_portrait_or_broll`), lấy Video Forensic / B-Roll diễn viên thật sạch chữ
                    if not rescued_ok and is_portrait_tool and not is_long_form:
                        try:
                            from app.graph.tools.media_tools import execute_clean_cinematic_portrait_or_broll_sync
                            r_story_qs = list(
                                r_sc.get("storyline_search_queries")
                                or strategy_map.get(r_sid, {}).get("storyline_search_queries")
                                or []
                            )
                            p_facet = portrait_broll_facets[r_pos % len(portrait_broll_facets)]
                            cand_p_queries = list(dict.fromkeys([q for q in (*r_story_qs, r_kw, r_stock_q, p_facet) if q]))[:3]
                            for p_q in cand_p_queries:
                                if rescued_ok or not p_q:
                                    break
                                c_vid_res = await asyncio.to_thread(
                                    execute_clean_cinematic_portrait_or_broll_sync,
                                    subject_entity=r_subj,
                                    human_action_query=p_q,
                                    output_path=r_out_mp4,
                                    media_preference="YOUTUBE_FORENSIC_OR_CHARACTER_SHORT" if r_pos % 2 == 0 else "VIDEO_BROLL",
                                    overall_script_query=overall_rescue_q,
                                    search_scope="SCENE_SPECIFIC",
                                    narration=str(r_sc.get("narration") or state.get("topic", "")),
                                    topic=state.get("topic", ""),
                                    target_duration_sec=r_dur + 0.25,
                                    used_urls=used_pool_urls,
                                    is_hook_scene=(r_sid == 1),
                                    job_id=job_id,
                                    scene_id=r_sid,
                                    fallback_query=r_stock_q,
                                    storyline_queries=cand_p_queries,
                                    cancel_event=cancel_event,
                                    deadline_ts=deadline_ts,
                                )
                                if c_vid_res and c_vid_res.get("media_path") and Path(c_vid_res["media_path"]).exists():
                                    if str(c_vid_res.get("media_type") or "").lower() in ("video", "hook_ready_video"):
                                        r_sc["media_path"] = c_vid_res["media_path"]
                                        r_sc["media_type"] = "video"
                                        r_sc["pose_images"] = []
                                        r_sc["production_method"] = "MOTION_FLOOR_RESCUE_PORTRAIT_BROLL"
                                        r_sc["visual_strategy"] = "CLEAN_ACTOR_VIDEO_BROLL"
                                        rescued_ok = True
                                        logger.info(f"  ✅ [Real-Motion Floor Rescue] Scene {r_sid}: Đã nâng cấp sang Portrait B-Roll Video độc lập ({Path(c_vid_res['media_path']).name})")
                        except Exception as _re_pb:
                            logger.debug(f"[Real-Motion Floor Rescue] Scene {r_sid} Portrait B-Roll rescue skip: {_re_pb}")

                    # Chuẩn bị truy vấn theo sát thực thể riêng của phân cảnh (không gộp cứng mọi cảnh về cùng 1 từ khóa chung!)
                    facet_tag = (
                        portrait_broll_facets[r_pos % len(portrait_broll_facets)]
                        if is_portrait_tool
                        else rescue_angle_facets[r_pos % len(rescue_angle_facets)]
                    )
                    from app.graph.tools.tool_schemas import build_single_entity_stock_queries
                    scene_single_queries = build_single_entity_stock_queries(
                        [q_c for q_c in (r_stock_q, r_kw, overall_rescue_q) if q_c],
                        subject_entity=r_subj,
                        topic=state.get("topic", ""),
                        max_words=3,
                    )

                    async def _run_stock_video_rescue_step() -> None:
                        nonlocal rescued_ok
                        if rescued_ok or _rescue_cancelled():
                            return
                        stock_queries: List[str] = []
                        cand_sq_list = (
                            (portrait_broll_facets[r_pos % len(portrait_broll_facets)], r_stock_q, overall_rescue_q)
                            if is_portrait_tool
                            else (
                                r_stock_q,
                                r_kw,
                                *(scene_single_queries[:2]),
                                overall_rescue_q,
                                f"{r_subj} {facet_tag}".strip(),
                            )
                        )
                        for sq in cand_sq_list:
                            sq_clean = (sq or "").strip()
                            if sq_clean and sq_clean not in stock_queries:
                                stock_queries.append(sq_clean)
                        for sq_try in stock_queries[:2]:
                            if rescued_ok or _rescue_cancelled():
                                break
                            try:
                                va_resc = {
                                    "video_format": "long" if is_long_form else "short",
                                    "search_queries": stock_queries[:3],
                                    "subject": (primary_entity or sq_try) if is_long_form else sq_try,
                                    "core_visual_entity": (primary_entity or sq_try) if is_long_form else sq_try,
                                    "topic": state.get("topic", ""),
                                    "skip_establishing_pass": (r_sid == 1),
                                }
                                best_v_fb = await asyncio.to_thread(
                                    media_agent.search_best_video_clip,
                                    sq_try,
                                    used_pool_urls,
                                    va_resc,
                                    str(r_sc.get("narration") or state.get("topic", "")),
                                    state.get("topic", ""),
                                )
                                if best_v_fb and best_v_fb.get("url"):
                                    cand_fb_urls = [
                                        best_v_fb["url"],
                                        *(best_v_fb.get("_vision_alternates") or []),
                                    ]
                                    cand_title_fb = str(best_v_fb.get("title") or sq_try)
                                    for cand_u_fb_raw in cand_fb_urls[:4]:
                                        cand_u_fb = str(cand_u_fb_raw or "").strip()
                                        if not cand_u_fb:
                                            continue
                                        if not rag.try_claim_in_job_video(
                                            job_id=job_id, vid_id=cand_u_fb, url=cand_u_fb, title=cand_title_fb, scene_id=r_sid
                                        ):
                                            used_pool_urls.add(cand_u_fb)
                                            continue
                                        raw_dl_mp4 = str(media_dir / f"scene_{r_sid:03d}_stock_raw.mp4")
                                        await asyncio.to_thread(media_agent.download_file, cand_u_fb, raw_dl_mp4)
                                        if Path(raw_dl_mp4).exists() and Path(raw_dl_mp4).stat().st_size > 15_000:
                                            await asyncio.to_thread(
                                                trim_video,
                                                input_path=raw_dl_mp4,
                                                duration_seconds=r_dur + 0.25,
                                                output_path=r_out_mp4,
                                                width=1920 if is_long_form else 1080,
                                                height=1080 if is_long_form else 1920,
                                                fps=30,
                                                is_scifi=is_scifi_val,
                                                niche=current_niche,
                                                apply_grading=True,
                                                is_hook_scene=False,
                                                fair_use_transform=True,
                                            )
                                            Path(raw_dl_mp4).unlink(missing_ok=True)
                                            if Path(r_out_mp4).exists() and Path(r_out_mp4).stat().st_size > 15_000:
                                                if any(k_wm in cand_u_fb.lower() for k_wm in ("wikimedia.org", "archive.org")):
                                                    from app.graph.evaluators.visual_critic import VisualCritic
                                                    vc_wm = await asyncio.to_thread(
                                                        VisualCritic.inspect_clip_before_concat,
                                                        r_out_mp4,
                                                        min_short_edge=720,
                                                        min_sharpness=5.2,
                                                        check_watermark=True,
                                                        allow_snow=True,
                                                        auto_sanitize=False,
                                                    )
                                                    if not vc_wm.get("passed", False):
                                                        Path(r_out_mp4).unlink(missing_ok=True)
                                                        rag.release_in_job_video_claim(
                                                            job_id=job_id, vid_id=cand_u_fb, url=cand_u_fb, title=cand_title_fb, scene_id=r_sid
                                                        )
                                                        continue
                                                ok_sig, _ = rag.verify_and_commit_in_job_clip(
                                                    job_id=job_id,
                                                    scene_id=r_sid,
                                                    media_path=r_out_mp4,
                                                    vid_id=cand_u_fb,
                                                    url=cand_u_fb,
                                                    title=cand_title_fb,
                                                )
                                                used_pool_urls.add(cand_u_fb)
                                                if ok_sig:
                                                    r_sc["media_path"] = r_out_mp4
                                                    r_sc["media_type"] = "video"
                                                    r_sc["pose_images"] = []
                                                    r_sc["production_method"] = "MOTION_FLOOR_RESCUE_STOCK_VIDEO"
                                                    r_sc["visual_strategy"] = "REAL_DOCUMENTARY_VIDEO"
                                                    rescued_ok = True
                                                    logger.info(
                                                        f"  ✅ [Real-Motion Floor Rescue] Scene {r_sid}: Đã nâng cấp sang Stock Video độc lập ('{sq_try}') -> {Path(r_out_mp4).name}"
                                                    )
                                                    break
                                                else:
                                                    Path(r_out_mp4).unlink(missing_ok=True)
                                        else:
                                            Path(raw_dl_mp4).unlink(missing_ok=True)
                                            rag.release_in_job_video_claim(
                                                job_id=job_id, vid_id=cand_u_fb, url=cand_u_fb, title=cand_title_fb, scene_id=r_sid
                                            )
                            except Exception as _re_doc:
                                logger.debug(f"[Real-Motion Floor Rescue] Scene {r_sid} Stock rescue ({sq_try}) skip: {_re_doc}")

                    async def _run_yt_peak_rescue_step() -> None:
                        nonlocal rescued_ok
                        if rescued_ok or is_long_form or _rescue_cancelled():
                            return
                        yt_rescue_queries = [
                            (f"{r_stock_q or r_kw} {facet_tag}".strip(), r_stock_q or overall_rescue_q, "SCENE_SPECIFIC"),
                        ]
                        if not already_tried_scene_kw:
                            yt_rescue_queries.insert(0, (r_kw, r_stock_q or overall_rescue_q, "SCENE_SPECIFIC"))
                        for q_main, q_fb, q_scope in yt_rescue_queries[:1]:
                            if rescued_ok or is_long_form or _rescue_cancelled():
                                break
                            try:
                                yt_sub_dl = (
                                    min(deadline_ts, time.monotonic() + 22.0)
                                    if deadline_ts is not None
                                    else (time.monotonic() + 22.0)
                                )
                                yt_rescue = await asyncio.to_thread(
                                    execute_youtube_short_peak_clone_sync,
                                    search_query=q_main,
                                    output_path=r_out_mp4,
                                    target_duration_sec=r_dur,
                                    clip_role="PEAK",
                                    used_urls=used_pool_urls,
                                    fallback_query=q_fb,
                                    subject_entity=r_subj,
                                    narration=str(r_sc.get("narration") or state.get("topic", "")),
                                    topic=state.get("topic", ""),
                                    search_scope=q_scope,
                                    overall_script_query=overall_rescue_q,
                                    max_peak_duration_sec=14.0,
                                    job_id=job_id,
                                    scene_id=r_sid,
                                    channel_id=channel_id_val,
                                    cancel_event=cancel_event,
                                    deadline_ts=yt_sub_dl,
                                )
                                if yt_rescue and yt_rescue.get("media_path") and Path(yt_rescue["media_path"]).exists():
                                    r_sc["media_path"] = yt_rescue["media_path"]
                                    r_sc["media_type"] = "video"
                                    r_sc["pose_images"] = []
                                    r_sc["production_method"] = "MOTION_FLOOR_RESCUE_YT_PEAK"
                                    r_sc["visual_strategy"] = "YOUTUBE_SHORT_PEAK_CLONE"
                                    rescued_ok = True
                                    logger.info(
                                        f"  ✅ [Real-Motion Floor Rescue] Scene {r_sid}: Đã nâng cấp sang Video Peak độc lập ({Path(yt_rescue['media_path']).name})"
                                    )
                            except Exception as _re_yt:
                                logger.debug(f"[Real-Motion Floor Rescue] Scene {r_sid} YT peak rescue ({q_main}) skip: {_re_yt}")

                    # Luôn ưu tiên chạy Stock Video Pexels/Pixabay 1080x1920 HD (~2s) trước khi gọi yt-dlp để tăng tốc độ dựng gấp 5 lần!
                    await _run_stock_video_rescue_step()
                    if not rescued_ok:
                        await _run_yt_peak_rescue_step()

                    # 3. Cứu hộ từ các ứng viên `hook_candidates` ĐỘC LẬP chưa dùng (khác hoàn toàn video ID với Cảnh 1)
                    if not rescued_ok and not _rescue_cancelled():
                        try:
                            from app.graph.evaluators.visual_critic import VisualCritic
                            hc_dir = work_dir / "hook_candidates"
                            active_paths_r, active_vids_r = _collect_active_scene_source_paths(
                                scenes_media, exclude_scene_ids={r_sid}
                            )
                            unused_hook_cands = sorted(
                                [
                                    *list(hc_dir.glob("hook_peak_clean_*.mp4")),
                                    *list(hc_dir.glob("s1_stock_rescue_*.mp4")),
                                ],
                                key=_safe_size,
                                reverse=True,
                            )
                            for src_vp in unused_hook_cands:
                                if not src_vp.exists() or src_vp.stat().st_size < 25_000:
                                    continue
                                if str(src_vp.resolve()) in active_paths_r:
                                    continue
                                cand_yt_id = _extract_hook_candidate_vid_id(src_vp)
                                if cand_yt_id in active_vids_r:
                                    continue
                                if not rag.try_claim_in_job_video(
                                    job_id=job_id,
                                    vid_id=cand_yt_id,
                                    url=f"https://www.youtube.com/watch?v={cand_yt_id}" if len(cand_yt_id) == 11 else str(src_vp),
                                    title=f"Hook Candidate {cand_yt_id}",
                                    scene_id=r_sid,
                                ):
                                    continue
                                src_total_s = get_duration_ms(str(src_vp)) / 1000.0
                                offset_s = max(0.5, min(src_total_s - r_dur - 0.3, src_total_s * 0.35)) if src_total_s > r_dur + 1.5 else 0.0
                                trim_video(
                                    input_path=str(src_vp),
                                    duration_seconds=r_dur + 0.3,
                                    output_path=r_out_mp4,
                                    width=1920 if is_long_form else 1080,
                                    height=1080 if is_long_form else 1920,
                                    fps=30,
                                    is_scifi=is_scifi_val,
                                    niche=current_niche,
                                    apply_grading=True,
                                    is_hook_scene=False,
                                    fair_use_transform=True,
                                    start_offset_sec=offset_s,
                                    preserve_continuous_flow=True,
                                )
                                if Path(r_out_mp4).exists() and Path(r_out_mp4).stat().st_size > 15_000:
                                    vc_res = VisualCritic.inspect_clip_before_concat(
                                        r_out_mp4,
                                        min_short_edge=720,
                                        min_sharpness=5.4,
                                        check_watermark=True,
                                        allow_snow=True,
                                        auto_sanitize=True,
                                    )
                                    if not vc_res.get("passed", False):
                                        logger.warning(
                                            f"  🚫 [Ending Video Guarantee] Scene {r_sid}: Loại ứng viên '{src_vp.name}' do dính chữ/watermark ({vc_res.get('reason')})"
                                        )
                                        Path(r_out_mp4).unlink(missing_ok=True)
                                        continue
                                    ok_cand_sig, _ = rag.verify_and_commit_in_job_clip(
                                        job_id=job_id,
                                        scene_id=r_sid,
                                        media_path=r_out_mp4,
                                        vid_id=cand_yt_id,
                                        url=str(src_vp),
                                        title=f"Unused Hook Candidate {cand_yt_id}",
                                    )
                                    if ok_cand_sig:
                                        r_sc["media_path"] = r_out_mp4
                                        r_sc["media_type"] = "video"
                                        r_sc["pose_images"] = []
                                        r_sc["production_method"] = "ENDING_UNUSED_CANDIDATE_VIDEO"
                                        r_sc["visual_strategy"] = "ENDING_UNUSED_CANDIDATE_VIDEO"
                                        rescued_ok = True
                                        logger.info(
                                            f"  🔥 [Ending Video Guarantee] Scene {r_sid}: Đã sử dụng ứng viên video độc lập chưa dùng '{src_vp.name}' (@{offset_s:.1f}s)!"
                                        )
                                        break
                                    else:
                                        Path(r_out_mp4).unlink(missing_ok=True)
                        except Exception as _re_slice:
                            logger.debug(f"[Ending Video Guarantee] Scene {r_sid} unused candidate skip: {_re_slice}")

            async def _guarded_rescue_single_scene_motion(r_pos: int, r_idx: int) -> None:
                from app.agents.curation.scene_context_analyzer_mixin import bind_scene_cancellation
                async with rescue_sem:
                    r_cancel_ev = threading.Event()
                    r_deadline_ts = time.monotonic() + 38.5
                    bind_scene_cancellation(r_cancel_ev, r_deadline_ts)
                    try:
                        await asyncio.wait_for(
                            _rescue_single_scene_motion(
                                r_pos,
                                r_idx,
                                cancel_event=r_cancel_ev,
                                deadline_ts=r_deadline_ts,
                            ),
                            timeout=40.0,
                        )
                    except Exception as _e_r_to:
                        r_cancel_ev.set()
                        logger.debug(f"[Real-Motion Floor Rescue] Slot {r_idx} timeout/skip: {_e_r_to}")
                        try:
                            r_sc_to = scenes_media[r_idx]
                            r_sid_to = int(r_sc_to.get("scene_id") or (r_idx + 1))
                            r_out_to = media_dir / f"scene_{r_sid_to:03d}_motion_rescue.mp4"
                            if not _is_real_video_slot(r_sc_to) and r_out_to.exists() and r_out_to.stat().st_size > 15_000:
                                r_sc_to["media_path"] = str(r_out_to)
                                r_sc_to["media_type"] = "video"
                                r_sc_to["pose_images"] = []
                                r_sc_to["production_method"] = "MOTION_FLOOR_RESCUE_LATE_RECOVERED"
                                r_sc_to["visual_strategy"] = "REAL_DOCUMENTARY_VIDEO"
                                logger.info(
                                    f"  ♻️ [Real-Motion Floor Rescue] Scene {r_sid_to}: Khôi phục file video '{r_out_to.name}' vừa cắt xong trên đĩa!"
                                )
                        except Exception:
                            pass

            await asyncio.gather(
                *[
                    _guarded_rescue_single_scene_motion(r_pos, r_idx)
                    for r_pos, r_idx in enumerate(rescue_candidates_idx[:needed_rescues])
                ]
            )
    except Exception as _step_b_err:
        logger.debug(f"[Real-Motion Floor Rescue] Step B warning: {_step_b_err}")

    # ══════════════════════════════════════════════════════════════════════════
    # Bước C: BỘ ĐIỀU PHỐI MẬT ĐỘ ẢNH TĨNH MƯỢT MÀ (TỐI ĐA 1-2 ẢNH / CẢNH ẢNH, MỖI ẢNH >= 2.0 GIÂY)
    # - Chấm dứt hoàn toàn hiện tượng nhồi 3 ảnh vào 1 cảnh 3.5s gây nháy hình (strobe) làm rớt người xem!
    # - Cảnh < 4.0s chỉ dùng 1 ảnh Ken Burns mượt mà; cảnh >= 4.0s dùng tối đa 2 ảnh (mỗi ảnh 2.0s - 2.5s).
    # ══════════════════════════════════════════════════════════════════════════
    try:
        hook_img_count = 0
        if scenes_media and isinstance(scenes_media[0], dict):
            sc0 = scenes_media[0]
            if str(sc0.get("production_method") or "").startswith("CLIMAX_IMAGES_LIVE_FX_"):
                hook_img_count = len([p for p in (sc0.get("pose_images") or []) if p and Path(p).exists()])

        body_img_indices = [
            idx_b for idx_b, m_b in enumerate(scenes_media)
            if isinstance(m_b, dict) and str(m_b.get("media_type") or "image").lower() == "image"
        ]

        if body_img_indices:
            scene_quotas: Dict[int, int] = {
                i_slot: (2 if _get_slot_dur(i_slot) >= 3.4 else 1)
                for i_slot in body_img_indices
            }

            angle_facets = [
                "dramatic close up portrait",
                "intense action moment",
                "cinematic wide angle",
                "emotional eye contact",
                "dark atmospheric detail",
                "natural habitat behavior",
                "turning point scene",
                "vivid high contrast shot",
            ]

            async def _fill_scene_image_quota(slot_idx: int) -> None:
                sc_item = scenes_media[slot_idx]
                sid = int(sc_item.get("scene_id") or (slot_idx + 1))
                quota = scene_quotas.get(slot_idx, 2)
                primary_p = sc_item.get("media_path")
                existing_poses = [
                    p for p in (sc_item.get("pose_images") or ([primary_p] if primary_p else []))
                    if p and not str(p).endswith("_graphic.png") and Path(p).exists() and Path(p).stat().st_size > 2500
                ]
                if not existing_poses and primary_p and not str(primary_p).endswith("_graphic.png") and Path(primary_p).exists():
                    existing_poses = [primary_p]

                verified_poses: List[str] = []
                for ep in existing_poses:
                    if rag.verify_and_commit_in_job_image(job_id=job_id, scene_id=sid, image_path=ep, url=ep):
                        verified_poses.append(ep)
                if not verified_poses and existing_poses:
                    verified_poses = [existing_poses[0]]

                # Nếu phân cảnh ĐÃ có ít nhất 1 ảnh thật đã qua Vision Curation -> giữ nguyên, TUYỆT ĐỐI KHÔNG tải ảnh web thô chưa qua Vision!
                if len(verified_poses) >= 1:
                    if str(sc_item.get("media_path") or "").endswith("_graphic.png"):
                        sc_item["media_path"] = verified_poses[0]
                    sc_item["pose_images"] = verified_poses[:quota]
                    return

                short_subj = clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=3)
                raw_sc_kw = sc_item.get("visual_keyword") or sc_item.get("visual_prompt") or short_subj
                sc_kw = clean_scene_search_query(raw_sc_kw, max_words=4) or short_subj

                # Nếu phân cảnh đang rơi vào _graphic.png (chưa có ảnh thật nào), gọi công cụ chuẩn có Vision Curation
                try:
                    from app.graph.tools.google_image_executor import execute_google_cinematic_image_sync
                    resc_img_p = str(media_dir / f"scene_{sid:03d}_vision_rescue.jpg")
                    g_res = await asyncio.to_thread(
                        execute_google_cinematic_image_sync,
                        smart_query=sc_kw,
                        output_path=resc_img_p,
                        narration=str(sc_item.get("narration") or state.get("topic", "")),
                        topic=state.get("topic", ""),
                        used_urls=used_pool_urls,
                        is_hook_scene=(sid == 1),
                        subject_entity=short_subj or state.get("topic", ""),
                        search_scope="SCENE_SPECIFIC",
                        overall_script_query=primary_entity or state.get("topic", ""),
                        job_id=job_id,
                        scene_id=sid,
                        target_image_count=1,
                    )
                    if g_res and g_res.get("media_path") and Path(g_res["media_path"]).exists():
                        verified_poses.append(str(g_res["media_path"]))
                except Exception as _g_resc_err:
                    logger.debug(f"[Visual Density Engine] Scene {sid} vision photo rescue skip: {_g_resc_err}")

                if not verified_poses:
                    try:
                        ai_cut_p = str(media_dir / f"scene_{sid:03d}_ai_rescue.jpg")
                        ar_str = "16:9" if is_long_form else "9:16"
                        ai_q = f"{sc_kw}, dramatic {'widescreen 16:9' if is_long_form else 'vertical 9:16'} photography, 8k sharp focus"
                        gen_p = await asyncio.to_thread(
                            image_agent.generate_scene_image,
                            ai_q,
                            ai_cut_p,
                            ar_str,
                            state.get("channel_style", "cinematic"),
                            sid,
                            sc_item.get("visual_prompt"),
                        )
                        if gen_p and Path(gen_p).exists() and Path(gen_p).stat().st_size > 2500:
                            verified_poses.append(gen_p)
                    except Exception:
                        pass

                real_poses = [p for p in verified_poses if p and not str(p).endswith("_graphic.png")]
                if real_poses:
                    verified_poses = real_poses
                    if str(sc_item.get("media_path") or "").endswith("_graphic.png"):
                        sc_item["media_path"] = real_poses[0]
                    sc_item["pose_images"] = verified_poses[:quota]

            density_sem = asyncio.Semaphore(5)

            async def _guarded_fill_scene_image_quota(i_slot: int) -> None:
                from app.agents.curation.scene_context_analyzer_mixin import bind_scene_cancellation
                async with density_sem:
                    d_cancel_ev = threading.Event()
                    bind_scene_cancellation(d_cancel_ev, time.monotonic() + 28.0)
                    try:
                        await asyncio.wait_for(_fill_scene_image_quota(i_slot), timeout=30.0)
                    except Exception as _e_dens_to:
                        d_cancel_ev.set()
                        logger.debug(f"[Visual Density Engine] Slot {i_slot} timeout/skip: {_e_dens_to}")

            await asyncio.gather(*[_guarded_fill_scene_image_quota(i_slot) for i_slot in body_img_indices])

            total_video_images = hook_img_count + sum(
                len(scenes_media[i_s].get("pose_images") or [scenes_media[i_s].get("media_path")])
                for i_s in body_img_indices
            )
            per_scene_log = ", ".join(
                f"S{scenes_media[i_s].get('scene_id')}:{len(scenes_media[i_s].get('pose_images') or [])}img"
                for i_s in body_img_indices
            )
            logger.info(
                f"🖼️ [Visual Density Engine] Đã chuẩn bị tổng cộng {total_video_images} ảnh thật độc lập cho video "
                f"(Hook={hook_img_count}img | Body: {per_scene_log})"
            )
    except Exception as e_dens:
        logger.warning(f"[MediaNodes] Visual Density Engine warning: {e_dens}")

    # ══════════════════════════════════════════════════════════════════════════
    # Bước D: BẢO HIỂM TRIỆT TIÊU 100% THẺ XANH `_graphic.png` (`SCENE #X` PLACEHOLDER CARD)
    # - Không bao giờ để lọt thẻ placeholder `_graphic.png` vào bản dựng cuối!
    # ══════════════════════════════════════════════════════════════════════════
    try:
        from app.utils.ffmpeg_helper import get_duration_ms, trim_video, is_completed_clean_mp4
        graphic_slots = [
            idx_g for idx_g, sc_g in enumerate(scenes_media)
            if isinstance(sc_g, dict) and (
                str(sc_g.get("media_path") or "").endswith("_graphic.png")
                or not sc_g.get("media_path")
                or not Path(str(sc_g.get("media_path"))).exists()
            )
        ]
        if graphic_slots:
            logger.warning(
                f"🛡️ [Zero-Graphic-Card Guard] Phát hiện {len(graphic_slots)} phân cảnh còn thẻ placeholder `_graphic.png` -> Thay thế bằng Video/Animation thật ngay!"
            )
            is_psych_guard = str(current_niche).lower() in (
                "psychology", "satirical_psychology_animation", "doodle", "philosophy", "self_improvement"
            )
            from app.services.media.longform_chapter_pool import has_verified_chapter_master_meta
            hc_dir_g = work_dir / "hook_candidates"
            ch_pool_dir_g = media_dir / "chapter_pool"
            active_paths_zg, active_vids_zg = _collect_active_scene_source_paths(scenes_media)
            # TUYỆT ĐỐI KHÔNG đưa các clip phân cảnh ngắn đã gán (`media_dir/*.mp4`, `hook_motion_*.mp4`) vào kho cắt lát,
            # chỉ lấy các file Master dài (`chapter_*_master.mp4`) hoặc các ứng viên `hook_candidates` chưa dùng!
            all_real_mp4s = sorted(
                [
                    p for p in [
                        *(
                            [m_p for m_p in ch_pool_dir_g.glob("chapter_*_master.mp4") if has_verified_chapter_master_meta(m_p)]
                            if ch_pool_dir_g.exists()
                            else []
                        ),
                        *(
                            [
                                c_p for c_p in (
                                    *list(hc_dir_g.glob("hook_peak_clean_*.mp4")),
                                    *list(hc_dir_g.glob("s1_stock_rescue_*.mp4")),
                                )
                                if str(c_p.resolve()) not in active_paths_zg
                                and _extract_hook_candidate_vid_id(c_p) not in active_vids_zg
                            ]
                            if hc_dir_g.exists()
                            else []
                        ),
                    ]
                    if is_completed_clean_mp4(p, min_size_bytes=25_000, min_duration_ms=4500)
                ],
                key=_safe_size,
                reverse=True,
            )
            d_chars_g = ["@MANIPULATOR", "@MODERNYOU", "@PLEASER", "@ANCESTOR"]
            d_props_g = ["puppet_strings", "mask", "cage", "broken_compass", "scroll_feed", "treadmill"]

            def _anchor_zg_query(raw_kw: str) -> str:
                kw_clean = clean_scene_search_query(raw_kw or primary_entity or state.get("topic", ""), max_words=4)
                anchor_src = f"{primary_entity or ''} {state.get('topic', '')}".lower()
                anchor_toks = {
                    t for t in re.findall(r"[a-zA-ZÀ-ỹ]{4,}", anchor_src)
                    if t not in {"inside", "with", "from", "into", "that", "this", "what", "when", "high", "speed", "apex", "hunt", "most", "over"}
                }
                kw_toks = set(re.findall(r"[a-zA-ZÀ-ỹ]{4,}", (kw_clean or "").lower()))
                if primary_entity and anchor_toks and not (kw_toks & anchor_toks):
                    ent_pref = clean_scene_search_query(primary_entity, max_words=2)
                    return clean_scene_search_query(f"{ent_pref} {kw_clean}", max_words=4) or ent_pref
                return kw_clean

            zg_sem = asyncio.Semaphore(5)

            async def _resolve_one_graphic_slot(g_pos: int, g_idx: int) -> None:
                from app.services.media.longform_chapter_pool import is_clip_visually_unique_in_job
                async with zg_sem:
                    await _resolve_one_graphic_slot_inner(g_pos, g_idx, is_clip_visually_unique_in_job)

            async def _resolve_one_graphic_slot_inner(
                g_pos: int,
                g_idx: int,
                is_clip_visually_unique_in_job: Any,
            ) -> None:
                from app.agents.curation.scene_context_analyzer_mixin import bind_scene_cancellation
                if True:
                    sc_g = scenes_media[g_idx]
                    sid_g = int(sc_g.get("scene_id") or (g_idx + 1))
                    dur_g = _get_slot_dur(g_idx)
                    out_g_mp4 = str(media_dir / f"scene_{sid_g:03d}_zero_graphic.mp4")
                    replaced_g = False

                    # 0. Phục hồi tức thì file video/ảnh thật của chính phân cảnh này vừa ghi xong trên đĩa (< 1ms)
                    disk_g_vids = sorted(
                        [
                            p for p in media_dir.glob(f"scene_{sid_g:03d}_*.mp4")
                            if is_completed_clean_mp4(p, min_size_bytes=20_000, min_duration_ms=1200)
                        ],
                        key=_safe_mtime,
                        reverse=True,
                    )
                    if disk_g_vids:
                        for dv_p in disk_g_vids:
                            if is_clip_visually_unique_in_job(str(dv_p), job_id, scene_id=sid_g):
                                sc_g["media_path"] = str(dv_p)
                                sc_g["media_type"] = "video"
                                sc_g["pose_images"] = []
                                sc_g["production_method"] = "ZERO_GRAPHIC_RECOVERED_VIDEO"
                                sc_g["visual_strategy"] = "REAL_DOCUMENTARY_VIDEO"
                                replaced_g = True
                                break
                    if not replaced_g:
                        disk_g_jpgs = sorted(
                            [
                                p for p in media_dir.glob(f"scene_{sid_g:03d}_*.jpg")
                                if _safe_size(p) > 4_000 and "_cand" not in p.name
                            ],
                            key=_safe_mtime,
                            reverse=True,
                        )
                        if disk_g_jpgs:
                            sc_g["media_path"] = str(disk_g_jpgs[0])
                            sc_g["media_type"] = "image"
                            sc_g["pose_images"] = [str(p) for p in disk_g_jpgs[:2]]
                            sc_g["production_method"] = "ZERO_GRAPHIC_RECOVERED_PHOTO"
                            sc_g["visual_strategy"] = "GEMINI_SEARCH_3D"
                            replaced_g = True

                    if not replaced_g and is_long_form:
                        try:
                            from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                            zg_pool = await asyncio.to_thread(
                                slice_any_available_chapter_master_for_scene,
                                media_dir=media_dir,
                                scene_id=sid_g,
                                target_duration_sec=dur_g,
                                job_id=job_id,
                                output_override_path=out_g_mp4,
                                max_master_uses_override=15,
                            )
                            if zg_pool and zg_pool.get("media_path") and Path(zg_pool["media_path"]).exists():
                                sc_g["media_path"] = zg_pool["media_path"]
                                sc_g["media_type"] = "video"
                                sc_g["pose_images"] = []
                                sc_g["production_method"] = "ZERO_GRAPHIC_CHAPTER_POOL_UNIQUE"
                                sc_g["visual_strategy"] = "LONGFORM_16X9_DOCUMENTARY_VIDEO"
                                sc_g["source_url"] = str(zg_pool.get("source_url") or zg_pool["media_path"])
                                replaced_g = True
                                logger.info(
                                    f"  ✅ [Zero-Graphic-Card Guard] Scene {sid_g}: Cắt lát 16:9 độc lập (0% trùng) -> '{Path(zg_pool['media_path']).name}'"
                                )
                        except Exception as _e_zgp:
                            logger.debug(f"[Zero-Graphic-Card Guard] Scene {sid_g} chapter pool rescue skip: {_e_zgp}")

                    zg_q = _anchor_zg_query(str(sc_g.get("visual_keyword") or ""))
                    zg_narr = (
                        f"{primary_entity} — {sc_g.get('narration') or ''}".strip()
                        if primary_entity
                        else str(sc_g.get("narration") or state.get("topic", ""))
                    )
                    if not replaced_g:
                        t2_ev = threading.Event()
                        try:
                            def _run_zg_vid():
                                with bind_scene_cancellation(t2_ev, time.monotonic() + 14.0):
                                    return media_agent.search_best_video_clip(
                                        zg_q,
                                        used_pool_urls,
                                        {"video_format": "long", "subject": primary_entity or zg_q} if is_long_form else None,
                                        zg_narr,
                                        state.get("topic", ""),
                                    )
                            best_zg_v = await asyncio.wait_for(asyncio.to_thread(_run_zg_vid), timeout=14.0)
                            if best_zg_v and best_zg_v.get("url"):
                                cand_zg_urls = [
                                    str(best_zg_v["url"]),
                                    *[str(u) for u in (best_zg_v.get("_vision_alternates") or []) if u],
                                ]
                                zg_v_title = str(best_zg_v.get("title") or zg_q)
                                raw_zg_mp4 = str(media_dir / f"scene_{sid_g:03d}_zero_graphic_raw.mp4")
                                for zg_v_url in cand_zg_urls[:4]:
                                    if not zg_v_url:
                                        continue
                                    if not rag.try_claim_in_job_video(
                                        job_id=job_id,
                                        vid_id=zg_v_url,
                                        url=zg_v_url,
                                        title=zg_v_title,
                                        scene_id=sid_g,
                                    ):
                                        used_pool_urls.add(zg_v_url)
                                        continue
                                    await asyncio.wait_for(
                                        asyncio.to_thread(media_agent.download_file, zg_v_url, raw_zg_mp4),
                                        timeout=10.0,
                                    )
                                    if Path(raw_zg_mp4).exists() and _safe_size(Path(raw_zg_mp4)) > 15_000:
                                        await asyncio.to_thread(
                                            trim_video,
                                            input_path=raw_zg_mp4,
                                            duration_seconds=dur_g + 0.25,
                                            output_path=out_g_mp4,
                                            width=1920 if is_long_form else 1080,
                                            height=1080 if is_long_form else 1920,
                                            fps=30,
                                            is_scifi=is_scifi_val,
                                            niche=current_niche,
                                            apply_grading=True,
                                            is_hook_scene=False,
                                            fair_use_transform=True,
                                        )
                                        Path(raw_zg_mp4).unlink(missing_ok=True)
                                        if Path(out_g_mp4).exists() and _safe_size(Path(out_g_mp4)) > 15_000:
                                            ok_zg_sig, _ = rag.verify_and_commit_in_job_clip(
                                                job_id=job_id,
                                                scene_id=sid_g,
                                                media_path=out_g_mp4,
                                                vid_id=zg_v_url,
                                                url=zg_v_url,
                                                title=zg_v_title,
                                            )
                                            if ok_zg_sig and is_clip_visually_unique_in_job(out_g_mp4, job_id, scene_id=sid_g):
                                                used_pool_urls.add(zg_v_url)
                                                sc_g["media_path"] = out_g_mp4
                                                sc_g["media_type"] = "video"
                                                sc_g["pose_images"] = []
                                                sc_g["production_method"] = "ZERO_GRAPHIC_FRESH_STOCK_VIDEO"
                                                sc_g["visual_strategy"] = "REAL_DOCUMENTARY_VIDEO"
                                                sc_g["source_url"] = zg_v_url
                                                replaced_g = True
                                                logger.info(
                                                    f"  ✅ [Zero-Graphic-Card Guard] Scene {sid_g}: Đã thay thẻ placeholder bằng Video Stock HD độc lập mới ('{zg_q}')"
                                                )
                                                break
                                            else:
                                                Path(out_g_mp4).unlink(missing_ok=True)
                                                rag.release_in_job_video_claim(
                                                    job_id=job_id, vid_id=zg_v_url, url=zg_v_url, title=zg_v_title, scene_id=sid_g
                                                )
                                    else:
                                        Path(raw_zg_mp4).unlink(missing_ok=True)
                                        rag.release_in_job_video_claim(
                                            job_id=job_id, vid_id=zg_v_url, url=zg_v_url, title=zg_v_title, scene_id=sid_g
                                        )
                        except Exception as _e_zgv:
                            logger.debug(f"[Zero-Graphic-Card Guard] Scene {sid_g} stock video rescue skip: {_e_zgv}")
                        finally:
                            t2_ev.set()

                    if not replaced_g:
                        t3_ev = threading.Event()
                        try:
                            def _run_zg_ph():
                                with bind_scene_cancellation(t3_ev, time.monotonic() + 16.0):
                                    return media_agent.search_best_photo(
                                        zg_q,
                                        used_pool_urls,
                                        {"video_format": "long", "subject": primary_entity or zg_q} if is_long_form else None,
                                        zg_narr,
                                        state.get("topic", ""),
                                    )
                            best_zg_ph = await asyncio.wait_for(asyncio.to_thread(_run_zg_ph), timeout=16.0)
                            if best_zg_ph and best_zg_ph.get("url"):
                                out_zg_jpg = str(media_dir / f"scene_{sid_g:03d}_zero_graphic.jpg")
                                await asyncio.wait_for(
                                    asyncio.to_thread(media_agent.download_file, str(best_zg_ph["url"]), out_zg_jpg),
                                    timeout=10.0,
                                )
                                if Path(out_zg_jpg).exists() and _safe_size(Path(out_zg_jpg)) > 4_000:
                                    used_pool_urls.add(str(best_zg_ph["url"]))
                                    sc_g["media_path"] = out_zg_jpg
                                    sc_g["media_type"] = "image"
                                    sc_g["pose_images"] = [out_zg_jpg]
                                    sc_g["production_method"] = "ZERO_GRAPHIC_STOCK_PHOTO_HD"
                                    sc_g["visual_strategy"] = "GEMINI_SEARCH_3D"
                                    sc_g["source_url"] = str(best_zg_ph["url"])
                                    replaced_g = True
                                    logger.info(
                                        f"  ✅ [Zero-Graphic-Card Guard] Scene {sid_g}: Đã thay thẻ placeholder bằng ảnh tài liệu HD độc lập ('{zg_q}')"
                                    )
                        except Exception as _e_zgph:
                            logger.debug(f"[Zero-Graphic-Card Guard] Scene {sid_g} stock photo rescue skip: {_e_zgph}")
                        finally:
                            t3_ev.set()

                    if not replaced_g:
                        t4_ev = threading.Event()
                        try:
                            from app.graph.tools.media_tools import execute_google_cinematic_image_sync
                            out_zg_gimg = str(media_dir / f"scene_{sid_g:03d}_zero_graphic_gsearch.jpg")
                            def _run_zg_gimg():
                                with bind_scene_cancellation(t4_ev, time.monotonic() + 18.0):
                                    return execute_google_cinematic_image_sync(
                                        smart_query=zg_q,
                                        output_path=out_zg_gimg,
                                        narration=zg_narr,
                                        topic=state.get("topic", ""),
                                        used_urls=used_pool_urls,
                                        is_hook_scene=(sid_g == 1),
                                        subject_entity=primary_entity or state.get("topic", ""),
                                        search_scope="SCENE_SPECIFIC",
                                        overall_script_query=primary_entity or state.get("topic", ""),
                                        job_id=job_id,
                                        scene_id=sid_g,
                                        target_image_count=1,
                                    )
                            g_zg_res = await asyncio.wait_for(asyncio.to_thread(_run_zg_gimg), timeout=18.0)
                            if g_zg_res and g_zg_res.get("media_path") and Path(g_zg_res["media_path"]).exists():
                                sc_g["media_path"] = str(g_zg_res["media_path"])
                                sc_g["media_type"] = "image"
                                sc_g["pose_images"] = list(g_zg_res.get("pose_images") or [g_zg_res["media_path"]])
                                sc_g["production_method"] = "ZERO_GRAPHIC_GOOGLE_PHOTO_HD"
                                sc_g["visual_strategy"] = "GEMINI_SEARCH_3D"
                                sc_g["source_url"] = str(g_zg_res.get("source_url") or "")
                                replaced_g = True
                                logger.info(
                                    f"  ✅ [Zero-Graphic-Card Guard] Scene {sid_g}: Đã thay thẻ placeholder bằng ảnh Google Vision HD ('{zg_q}')"
                                )
                        except Exception as _e_zgg:
                            logger.debug(f"[Zero-Graphic-Card Guard] Scene {sid_g} google image rescue skip: {_e_zgg}")
                        finally:
                            t4_ev.set()

                    # Tầng bảo hiểm Video Cắt Lát Độc Lập từ kho Master/MP4 dài trên đĩa (0% trùng dHash/signature)
                    if not replaced_g and all_real_mp4s:
                        for cand_m_i, src_mp4_g in enumerate(all_real_mp4s[:12]):
                            try:
                                src_dur_s = get_duration_ms(str(src_mp4_g)) / 1000.0
                                if src_dur_s < max(4.0, dur_g + 1.5):
                                    continue
                                max_off_g = max(0.5, src_dur_s - dur_g - 0.5)
                                off_g = round(((sid_g * 7.3 + cand_m_i * 5.7 + g_pos * 4.1) % max_off_g), 2)
                                cand_slice_mp4 = str(media_dir / f"scene_{sid_g:03d}_zg_slice_{cand_m_i}.mp4")
                                await asyncio.wait_for(
                                    asyncio.to_thread(
                                        trim_video,
                                        input_path=str(src_mp4_g),
                                        duration_seconds=dur_g + 0.15,
                                        output_path=cand_slice_mp4,
                                        width=1920 if is_long_form else 1080,
                                        height=1080 if is_long_form else 1920,
                                        fps=30,
                                        is_scifi=is_scifi_val,
                                        niche=current_niche,
                                        apply_grading=True,
                                        is_hook_scene=False,
                                        fair_use_transform=False,
                                        start_offset_sec=off_g,
                                        preserve_continuous_flow=True,
                                    ),
                                    timeout=16.0,
                                )
                                if is_completed_clean_mp4(cand_slice_mp4, min_size_bytes=25_000, min_duration_ms=1200):
                                    if is_clip_visually_unique_in_job(
                                        cand_slice_mp4,
                                        job_id,
                                        scene_id=sid_g,
                                        master_key=str(src_mp4_g.resolve()),
                                        max_master_uses=5,
                                    ):
                                        Path(cand_slice_mp4).replace(out_g_mp4)
                                        sc_g["media_path"] = out_g_mp4
                                        sc_g["media_type"] = "video"
                                        sc_g["pose_images"] = []
                                        sc_g["production_method"] = "ZERO_GRAPHIC_UNIQUE_MASTER_SLICE"
                                        sc_g["visual_strategy"] = "LONGFORM_16X9_DOCUMENTARY_VIDEO" if is_long_form else "REAL_DOCUMENTARY_VIDEO"
                                        sc_g["source_url"] = f"{src_mp4_g.name}#t={off_g:.1f}"
                                        replaced_g = True
                                        logger.info(
                                            f"  ✅ [Zero-Graphic-Card Guard] Scene {sid_g}: Cắt lát độc lập từ '{src_mp4_g.name}' (@{off_g:.1f}s)"
                                        )
                                        break
                                Path(cand_slice_mp4).unlink(missing_ok=True)
                            except Exception:
                                pass

                    # Tầng bảo hiểm cuối: Ảnh Stock ngang/dọc HD trực tiếp qua StockFetcher (không để sót bất kỳ _graphic.png nào)
                    if not replaced_g:
                        try:
                            from app.services.media.stock_fetcher import StockFetcher
                            sf_zg = StockFetcher()
                            broad_ph_q = clean_scene_search_query(primary_entity or zg_q or state.get("topic", ""), max_words=3)
                            ph_orient = "landscape" if is_long_form else "portrait"
                            raw_phs = await asyncio.wait_for(
                                asyncio.to_thread(
                                    lambda: (
                                        (sf_zg.search_photos(
                                            zg_q,
                                            per_page=10,
                                            orientation=ph_orient,
                                            topic=state.get("topic", ""),
                                            narration=zg_narr,
                                            used_urls=used_pool_urls,
                                        ) or [])
                                        + (sf_zg.search_photos(
                                            broad_ph_q,
                                            per_page=10,
                                            orientation=ph_orient,
                                            topic=state.get("topic", ""),
                                            narration=zg_narr,
                                            used_urls=used_pool_urls,
                                        ) or [])
                                        + (sf_zg.search_pixabay_photos(
                                            broad_ph_q,
                                            per_page=10,
                                            orientation="horizontal" if is_long_form else "vertical",
                                        ) or [])
                                    )
                                ),
                                timeout=12.0,
                            )
                            for ph_item in raw_phs:
                                ph_u = str(ph_item.get("url") or "").strip()
                                ph_w = int(ph_item.get("width") or 0)
                                ph_h = int(ph_item.get("height") or 0)
                                if not ph_u or ph_u in used_pool_urls:
                                    continue
                                if is_long_form and ph_w > 0 and ph_h > 0 and ph_w < ph_h:
                                    continue
                                out_zg_direct = str(media_dir / f"scene_{sid_g:03d}_zero_graphic_direct.jpg")
                                await asyncio.wait_for(
                                    asyncio.to_thread(media_agent.download_file, ph_u, out_zg_direct),
                                    timeout=8.0,
                                )
                                if Path(out_zg_direct).exists() and _safe_size(Path(out_zg_direct)) > 8_000:
                                    used_pool_urls.add(ph_u)
                                    sc_g["media_path"] = out_zg_direct
                                    sc_g["media_type"] = "image"
                                    sc_g["pose_images"] = [out_zg_direct]
                                    sc_g["production_method"] = "ZERO_GRAPHIC_DIRECT_STOCK_PHOTO"
                                    sc_g["visual_strategy"] = "GEMINI_SEARCH_3D"
                                    sc_g["source_url"] = ph_u
                                    replaced_g = True
                                    logger.info(
                                        f"  ✅ [Zero-Graphic-Card Guard] Scene {sid_g}: Đã thay thẻ placeholder bằng ảnh Stock HD trực tiếp ('{broad_ph_q}')"
                                    )
                                    break
                        except Exception as _e_zgd:
                            logger.debug(f"[Zero-Graphic-Card Guard] Scene {sid_g} direct photo skip: {_e_zgd}")

            await asyncio.gather(*[_resolve_one_graphic_slot(gp, gi) for gp, gi in enumerate(graphic_slots)])
    except Exception as _e_guard_d:
        logger.debug(f"[Zero-Graphic-Card Guard] Step D warning: {_e_guard_d}")

    # Đa dạng hóa chuyển động máy quay (Camera Motion Alternation) giữa các phân cảnh liền kề
    _motion_pool = ["zoom_in", "pan_right", "zoom_out", "pan_left", "pan_up", "pan_down"]
    prev_motion = None
    for idx_m, m_item in enumerate(scenes_media):
        if not isinstance(m_item, dict):
            continue
        cur_m = m_item.get("camera_motion") or "zoom_in"
        if idx_m > 0 and cur_m == prev_motion:
            alt_options = [m for m in _motion_pool if m != prev_motion]
            cur_m = alt_options[idx_m % len(alt_options)]
            m_item["camera_motion"] = cur_m
        prev_motion = cur_m

    # Báo cáo nguồn hình ảnh từng cảnh để kiểm tra chất lượng (storage/temp/job_x/visual_report.json)
    try:
        import json as _json
        report = [
            {
                "scene_id": m.get("scene_id") if isinstance(m, dict) else None,
                "file": Path(m.get("media_path") or "").name if isinstance(m, dict) else "",
                "media_type": m.get("media_type") if isinstance(m, dict) else "image",
                "image_count": len(m.get("pose_images") or ([m.get("media_path")] if m.get("media_type") == "image" else [])) if isinstance(m, dict) else 0,
                "pose_files": [Path(p).name for p in (m.get("pose_images") or [])] if isinstance(m, dict) else [],
                "production_method": m.get("production_method") if isinstance(m, dict) else "unknown",
                "narration": (m.get("narration") or "")[:120] if isinstance(m, dict) else "",
            }
            for m in scenes_media if isinstance(m, dict)
        ]
        (work_dir / "visual_report.json").write_text(_json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as rep_err:
        logger.debug(f"Không ghi được visual_report.json: {rep_err}")

    return {
        "scenes_media": scenes_media,
        "current_step": "MEDIA_READY",
        "progress_percent": 65,
        "progress_message": f"Đã thu thập trọn bộ media theo chiến lược đạo diễn ({len(scenes_media)} cảnh)",
    }


def node_evaluate_media(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node Visual Critic thẩm định chất lượng KỸ THUẬT hình ảnh (file, resolution, hash)."""
    scenes_media = [m for m in state.get("scenes_media", []) if isinstance(m, dict)]
    primary_entity = state.get("primary_entity")

    critic = VisualCritic()
    eval_report = critic.evaluate_media(scenes_media=scenes_media, primary_entity=primary_entity)

    # Thu hồi tức thì chứng chỉ (`*_meta.txt` / `*.meta`) của mọi Chapter Master bị VisualCritic ContactSheet Vision bắt lỗi ngữ nghĩa,
    # đảm bảo cả `node_refine_media` lẫn `FinalVisualGate` không bao giờ cắt lại từ Master lỗi trên bất kỳ cảnh nào!
    try:
        from app.services.media.longform_chapter_pool import get_chapter_master_meta_paths
        ch_pool_eval = Path(state.get("work_dir", "storage/cache")) / "media" / "chapter_pool"
        if ch_pool_eval.is_dir() and eval_report.failed_scene_ids:
            sc_map_eval = {int(m.get("scene_id") or 0): m for m in scenes_media}
            for sc_score_obj in (eval_report.scene_scores or []):
                sid_ev = int(getattr(sc_score_obj, "scene_id", 0) or 0)
                iss_ev = [str(x) for x in (getattr(sc_score_obj, "issues", None) or [])]
                if sid_ev and any("Gemini Vision ContactSheet" in x for x in iss_ev):
                    m_ev = sc_map_eval.get(sid_ev) or {}
                    blob_ev = f"{m_ev.get('source_url') or ''} {m_ev.get('strategy_rationale') or ''}".lower()
                    for m_name in set(re.findall(r"chapter_\d+_master\.mp4", blob_ev)):
                        m_file_ev = ch_pool_eval / m_name
                        revoked_any_meta = False
                        for m_meta_ev in get_chapter_master_meta_paths(m_file_ev):
                            if m_meta_ev.exists():
                                m_meta_ev.unlink(missing_ok=True)
                                revoked_any_meta = True
                        if revoked_any_meta:
                            logger.warning(
                                f"🛡️ [MasterQuarantine] Thu hồi chứng chỉ '{m_name}' do Cảnh #{sid_ev} bị ContactSheet Vision bắt lỗi ngữ nghĩa!"
                            )
                        try:
                            m_file_ev.unlink(missing_ok=True)
                        except OSError:
                            pass
    except Exception as _e_mq:
        logger.debug(f"[MasterQuarantine] Skip: {_e_mq}")

    # Chặn cứng tuyệt đối mọi phân cảnh còn sót `_graphic.png` hoặc thiếu file trên ổ đĩa trước khi vào render_video
    for sc_chk in scenes_media:
        sid_chk = int(sc_chk.get("scene_id") or 0)
        mp_chk = str(sc_chk.get("media_path") or "")
        if sid_chk > 0 and (
            not mp_chk
            or mp_chk.lower().endswith("_graphic.png")
            or str(sc_chk.get("production_method") or "").upper() == "EMERGENCY_GRAPHIC_CARD"
            or not Path(mp_chk).exists()
        ):
            if sid_chk not in eval_report.failed_scene_ids:
                eval_report.failed_scene_ids.append(sid_chk)
            eval_report.passed = False

    # Cổng kiểm toán tỷ lệ Video chuyển động thật (>= 80% trên Shorts):
    # Nếu video Shorts vẫn còn quá nhiều cảnh ảnh tĩnh (.jpg) sau node_fetch_media,
    # tự động đưa các cảnh ảnh tĩnh đó vào danh sách cần nâng cấp (`failed_scene_ids`) để node_refine_media / FinalVisualGate nạp Video thật!
    is_lf_check = (
        str(state.get("video_format") or (state.get("script_data") or {}).get("video_format") or "short").strip().lower() == "long"
        or len(scenes_media) >= 14
    )
    if not is_lf_check and len(scenes_media) >= 5:
        def _is_eval_real_video(sc_d: Dict[str, Any]) -> bool:
            mp_s = str(sc_d.get("media_path") or "").lower()
            mt_s = str(sc_d.get("media_type") or "").lower()
            pm_s = str(sc_d.get("production_method") or "").upper()
            if any(t_k in mp_s for t_k in ("_i2v", "_fb_motion", "_anim", "_motion3d", "_gsearch_3d", "_creature_3d")):
                return False
            if any(t_p in pm_s for t_p in ("IMAGE_TO_VIDEO", "STOCK_PHOTO_I2V", "GOOGLE_TOP_IMAGE", "CLIMAX_IMAGES_LIVE_FX")):
                return False
            return mt_s in ("video", "hook_ready_video") and mp_s.endswith((".mp4", ".mov", ".webm"))

        n_sc_eval = len(scenes_media)
        min_req_vid = max(5, int((n_sc_eval * 0.80) + 0.999))
        real_vid_count = sum(1 for sc_x in scenes_media if _is_eval_real_video(sc_x))
        if real_vid_count < min_req_vid:
            deficit_v = min_req_vid - real_vid_count
            static_priority_order = [0, 1, n_sc_eval - 1, n_sc_eval - 2] + [
                i_m for i_m in range(2, max(2, n_sc_eval - 2))
            ]
            added_static_sids: List[int] = []
            for st_idx in static_priority_order:
                if 0 <= st_idx < n_sc_eval and not _is_eval_real_video(scenes_media[st_idx]):
                    st_sid = int(scenes_media[st_idx].get("scene_id") or (st_idx + 1))
                    if st_sid not in eval_report.failed_scene_ids:
                        eval_report.failed_scene_ids.append(st_sid)
                        added_static_sids.append(st_sid)
                    if len(added_static_sids) >= deficit_v:
                        break
            if eval_report.failed_scene_ids:
                eval_report.passed = False
                logger.warning(
                    f"🎬 [MotionFloorGate] Chỉ có {real_vid_count}/{n_sc_eval} cảnh là video chuyển động thật (< {min_req_vid} cảnh chuẩn >=80%) "
                    f"-> Đưa thêm các cảnh ảnh tĩnh {added_static_sids} vào luồng nâng cấp Video B-Roll!"
                )

    # Cổng chặn cuối cùng trước khi vào render_video: Nếu đã hết lượt retry refine_media mà vẫn còn cảnh lỗi
    # (ví dụ: lệch loài/chủ thể do ContactSheet Vision phát hiện ở lần refine cuối), tự động thay thế ngay
    # bằng clip chuyển động sạch đạt chuẩn trong cùng Job để tuyệt đối không lọt cảnh lỗi sang render_video!
    max_media_retries_gate = max(0, int(os.getenv("MAX_MEDIA_RETRIES", "1")))
    if not eval_report.passed and eval_report.failed_scene_ids and int(state.get("media_retry_count", 0)) >= max_media_retries_gate:
        try:
            import subprocess
            from app.utils.ffmpeg_helper import get_duration_ms, trim_video, get_ffmpeg_exe
            from app.graph.nodes.hook_video_node import _ensure_hook_clip_sharpness_and_luma
            work_dir = Path(state.get("work_dir", "storage/cache"))
            media_dir = work_dir / "media"
            failed_set = set(eval_report.failed_scene_ids)
            used_active_paths, used_active_vids = _collect_active_scene_source_paths(
                scenes_media, exclude_scene_ids=failed_set
            )
            # Quy tắc Zero-Duplicate: Tuyệt đối KHÔNG lấy `hook_candidates`, `*_fullpeak.mp4`, hay video của cảnh khác (`clean_donors`)
            # để đắp sang cảnh lỗi! Thay vào đó, tải 1 nguồn Stock Video độc lập mới tinh từ Pexels/Pixabay.
            from app.agents.media_agent import MediaAgent
            from app.services.video_memory_rag import get_video_memory_rag
            ma_fg = MediaAgent()
            rag_fg = get_video_memory_rag()
            job_id_fg = int(state.get("job_id") or 0)
            current_niche = state.get("niche") or state.get("channel_style") or state.get("channel_niche") or "general"
            is_scifi_val = (current_niche == "scifi")
            is_lf_eval = (
                str(state.get("video_format") or (state.get("script_data") or {}).get("video_format") or "short").strip().lower() == "long"
                or len(scenes_media) >= 14
            )
            rescued_any = False
            fg_lock = threading.Lock()
            fg_facets = ["cinematic motion closeup", "dramatic nature footage", "intense slow motion", "atmospheric b-roll"]
            failed_items_fg = [x for x in scenes_media if int(x.get("scene_id") or 0) in failed_set]

            def _anchor_fg_query(raw_kw: str, max_w: int = 5) -> str:
                kw_clean = clean_scene_search_query(raw_kw or primary_entity or state.get("topic", ""), max_words=max_w)
                anchor_src = f"{primary_entity or ''} {state.get('topic', '')}".lower()
                anchor_toks = {
                    t for t in re.findall(r"[a-zA-ZÀ-ỹ]{4,}", anchor_src)
                    if t not in {"inside", "with", "from", "into", "that", "this", "what", "when", "high", "speed", "apex", "hunt", "most", "over"}
                }
                kw_toks = set(re.findall(r"[a-zA-ZÀ-ỹ]{4,}", (kw_clean or "").lower()))
                if primary_entity and anchor_toks and not (kw_toks & anchor_toks):
                    ent_pref = clean_scene_search_query(primary_entity, max_words=2)
                    return clean_scene_search_query(f"{ent_pref} {kw_clean}", max_words=max_w) or ent_pref
                return kw_clean

            def _rescue_final_gate_item(pair_fg: Tuple[int, Dict[str, Any]]) -> bool:
                from app.services.media.longform_chapter_pool import is_clip_visually_unique_in_job
                rescue_idx, m = pair_fg
                sid = int(m.get("scene_id") or 0)
                sc_dur = _scene_audio_duration(work_dir, sid)
                anchored_base_kw = _anchor_fg_query(str(m.get("visual_keyword") or ""), max_w=4)
                fg_narr = (
                    f"{primary_entity} — {m.get('narration') or ''}".strip()
                    if primary_entity
                    else str(m.get("narration") or state.get("topic", ""))
                )
                # Ưu tiên số 1 cho Video Dài 16:9 (< 2s trên ổ cứng): cắt lát 16:9 độc lập từ ChapterPool đã duyệt Vision!
                if is_lf_eval:
                    try:
                        from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                        tainted_fg: set = set()
                        prev_src_fg = str(m.get("source_url") or "")
                        if prev_src_fg:
                            tainted_fg.add(Path(prev_src_fg.split("#", 1)[0]).name.lower())
                        for m_tok in re.findall(r"chapter_\d+_master\.mp4", str(m.get("strategy_rationale") or "").lower()):
                            tainted_fg.add(m_tok)
                        out_fg_pool0 = str(media_dir / f"scene_{sid:03d}_final_pool_rescue.mp4")
                        pool_fg_res0 = slice_any_available_chapter_master_for_scene(
                            media_dir=media_dir,
                            scene_id=sid,
                            target_duration_sec=sc_dur,
                            job_id=job_id_fg,
                            output_override_path=out_fg_pool0,
                            exclude_masters=tainted_fg if tainted_fg else None,
                            max_master_uses_override=5,
                        )
                        if pool_fg_res0 and pool_fg_res0.get("media_path") and Path(pool_fg_res0["media_path"]).exists():
                            with fg_lock:
                                used_active_paths.add(str(Path(pool_fg_res0["media_path"]).resolve()))
                            m["media_path"] = pool_fg_res0["media_path"]
                            m["media_type"] = "hook_ready_video" if sid == 1 else "video"
                            m["pose_images"] = []
                            m["production_method"] = "FINAL_GATE_CHAPTER_POOL_16X9_RESCUE"
                            logger.info(
                                f"🛡️ [FinalVisualGate 16:9] Đã cắt Video 16:9 độc lập từ ChapterPool cho Cảnh #{sid} -> {Path(pool_fg_res0['media_path']).name}"
                            )
                            return True
                    except Exception as _e_fg_pool0:
                        logger.debug(f"[FinalVisualGate 16:9] ChapterPool priority rescue skip: {_e_fg_pool0}")

                q_fg = clean_scene_search_query(
                    f"{anchored_base_kw} {fg_facets[rescue_idx % len(fg_facets)]}",
                    max_words=5,
                )
                with fg_lock:
                    snap_fg_used = set(used_active_paths | used_active_vids)
                va_fg = {
                    "video_format": "long" if is_lf_eval else "short",
                    "subject": primary_entity or q_fg,
                    "core_visual_entity": primary_entity or q_fg,
                    "topic": state.get("topic", ""),
                    "search_queries": [q_fg],
                }
                if not is_lf_eval:
                    try:
                        from app.graph.tools.youtube_peak_executor import execute_youtube_short_peak_clone_sync
                        out_bili_fg = str(media_dir / f"scene_{sid:03d}_final_bili_rescue.mp4")
                        bili_fg_res = execute_youtube_short_peak_clone_sync(
                            search_query=clean_scene_search_query(m.get("visual_keyword") or primary_entity or q_fg, max_words=4),
                            output_path=out_bili_fg,
                            target_duration_sec=sc_dur,
                            clip_role="HOOK" if sid == 1 else "PEAK",
                            used_urls=snap_fg_used,
                            fallback_query=clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=3),
                            subject_entity=primary_entity or state.get("topic", ""),
                            narration=str(m.get("narration") or state.get("topic", "")),
                            topic=state.get("topic", ""),
                            search_scope="SCRIPT_OVERALL",
                            overall_script_query=clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=3),
                            max_peak_duration_sec=10.0,
                            job_id=job_id_fg,
                            scene_id=sid,
                            deadline_ts=time.monotonic() + 24.0,
                        )
                        if bili_fg_res and bili_fg_res.get("media_path") and Path(bili_fg_res["media_path"]).exists():
                            with fg_lock:
                                if bili_fg_res.get("source_url"):
                                    used_active_vids.add(str(bili_fg_res["source_url"]))
                                used_active_paths.add(str(Path(bili_fg_res["media_path"]).resolve()))
                            m["media_path"] = bili_fg_res["media_path"]
                            m["media_type"] = "hook_ready_video" if sid == 1 else "video"
                            m["pose_images"] = []
                            m["production_method"] = "FINAL_GATE_BILIBILI_PEAK_RESCUE"
                            logger.info(
                                f"🛡️ [FinalVisualGate] Đã tải video Bilibili/Peak chuẩn chủ thể cho Cảnh #{sid} -> {Path(bili_fg_res['media_path']).name}"
                            )
                            return True
                    except Exception as _e_bfg:
                        logger.debug(f"[FinalVisualGate] Bilibili/Peak rescue skip: {_e_bfg}")
                best_fg_v = ma_fg.search_best_video_clip(
                    q_fg,
                    snap_fg_used,
                    va_fg,
                    fg_narr,
                    state.get("topic", ""),
                )
                if not best_fg_v and primary_entity:
                    q_fg_single = clean_scene_search_query(f"{primary_entity} {fg_facets[rescue_idx % len(fg_facets)]}", max_words=4)
                    best_fg_v = ma_fg.search_best_video_clip(
                        q_fg_single,
                        snap_fg_used,
                        va_fg,
                        str(primary_entity),
                        str(primary_entity),
                    )
                if best_fg_v and best_fg_v.get("url"):
                    fg_cand_urls = [
                        best_fg_v["url"],
                        *(best_fg_v.get("_vision_alternates") or []),
                        *(best_fg_v.get("alternate_urls") or []),
                    ]
                    fg_title = str(best_fg_v.get("title") or q_fg)
                    for fg_u_raw in fg_cand_urls[:4]:
                        fg_url = str(fg_u_raw or "").strip()
                        if not fg_url:
                            continue
                        if not rag_fg.try_claim_in_job_video(job_id=job_id_fg, vid_id=fg_url, url=fg_url, title=fg_title, scene_id=sid):
                            with fg_lock:
                                used_active_vids.add(fg_url)
                            continue
                        raw_fg_dl = str(media_dir / f"scene_{sid:03d}_fg_raw.mp4")
                        out_final_rescue = str(media_dir / f"scene_{sid:03d}_final_clean_rescue.mp4")
                        ma_fg.download_file(fg_url, raw_fg_dl)
                        if Path(raw_fg_dl).exists() and _safe_size(Path(raw_fg_dl)) > 15_000:
                            trim_video(
                                input_path=raw_fg_dl,
                                duration_seconds=sc_dur,
                                output_path=out_final_rescue,
                                width=1920 if is_lf_eval else 1080,
                                height=1080 if is_lf_eval else 1920,
                                fps=30,
                                is_scifi=is_scifi_val,
                                niche=current_niche,
                                apply_grading=True,
                                is_hook_scene=(sid == 1),
                                fair_use_transform=True,
                                start_offset_sec=0.0,
                                preserve_continuous_flow=True,
                            )
                            Path(raw_fg_dl).unlink(missing_ok=True)
                            if Path(out_final_rescue).exists() and _safe_size(Path(out_final_rescue)) > 12_000:
                                if is_clip_visually_unique_in_job(out_final_rescue, job_id_fg, scene_id=sid):
                                    with fg_lock:
                                        used_active_vids.add(fg_url)
                                        used_active_paths.add(str(Path(out_final_rescue).resolve()))
                                    m["media_path"] = out_final_rescue
                                    m["media_type"] = "hook_ready_video" if sid == 1 else "video"
                                    m["pose_images"] = []
                                    m["production_method"] = "FINAL_GATE_FRESH_STOCK_RESCUE"
                                    logger.info(
                                        f"🛡️ [FinalVisualGate] Đã tải video Stock độc lập mới cho Cảnh #{sid} ('{q_fg}') -> {Path(out_final_rescue).name}"
                                    )
                                    return True
                                Path(out_final_rescue).unlink(missing_ok=True)
                                rag_fg.release_in_job_video_claim(job_id=job_id_fg, vid_id=fg_url, url=fg_url, title=fg_title, scene_id=sid)
                        else:
                            Path(raw_fg_dl).unlink(missing_ok=True)
                            rag_fg.release_in_job_video_claim(job_id=job_id_fg, vid_id=fg_url, url=fg_url, title=fg_title, scene_id=sid)
                # Bảo vệ tuyệt đối Cảnh 1 (Opening Hook): Nếu Cảnh 1 đã có Video chuyển động thật hợp lệ trên đĩa,
                # TUYỆT ĐỐI KHÔNG ghi đè bằng ảnh tĩnh Google/Stock trong FinalVisualGate!
                existing_fg_mp = str(m.get("media_path") or "")
                existing_fg_pm = str(m.get("production_method") or "").upper()
                if (
                    sid == 1
                    and existing_fg_mp
                    and Path(existing_fg_mp).exists()
                    and existing_fg_mp.lower().endswith((".mp4", ".mov", ".webm"))
                    and "CLIMAX_IMAGES_LIVE_FX" not in existing_fg_pm
                    and is_completed_clean_mp4(existing_fg_mp, min_size_bytes=12_000, min_duration_ms=1000)
                ):
                    m["media_type"] = "hook_ready_video"
                    logger.info(
                        f"🛡️ [FinalVisualGate] Giữ nguyên Video chuyển động thật cho Cảnh #1 ({Path(existing_fg_mp).name}) — cấm hạ cấp xuống ảnh tĩnh!"
                    )
                    return True
                # Fallback ảnh thật Google Top Image nếu kho video không trả về clip cho cảnh lỗi (Scene >= 2 hoặc chưa có video)
                from app.graph.tools.google_image_executor import execute_google_cinematic_image_sync
                out_fg_img = str(media_dir / f"scene_{sid:03d}_final_gimg_rescue.jpg")
                with fg_lock:
                    snap_fg_vids = set(used_active_vids)
                g_fg_res = execute_google_cinematic_image_sync(
                    smart_query=anchored_base_kw,
                    output_path=out_fg_img,
                    narration=fg_narr,
                    topic=state.get("topic", ""),
                    used_urls=snap_fg_vids,
                    is_hook_scene=(sid == 1),
                    subject_entity=primary_entity or state.get("topic", ""),
                    job_id=job_id_fg,
                    scene_id=sid,
                    target_image_count=1,
                )
                if g_fg_res and g_fg_res.get("media_path") and Path(g_fg_res["media_path"]).exists():
                    if g_fg_res.get("source_url"):
                        with fg_lock:
                            used_active_vids.add(str(g_fg_res["source_url"]))
                    m["media_path"] = g_fg_res["media_path"]
                    m["media_type"] = "image"
                    m["pose_images"] = g_fg_res.get("pose_images") or [g_fg_res["media_path"]]
                    m["production_method"] = "FINAL_GATE_GOOGLE_IMAGE_RESCUE"
                    logger.info(
                        f"🛡️ [FinalVisualGate] Đã thay thế Cảnh #{sid} bằng Ảnh thật Top Google độc lập -> {Path(g_fg_res['media_path']).name}"
                    )
                    return True
                # Tầng cuối cùng: Ảnh Stock ngang/dọc HD trực tiếp qua StockFetcher (tuyệt đối không để sót _graphic.png)
                try:
                    from app.services.media.stock_fetcher import StockFetcher
                    sf_fg = StockFetcher()
                    fg_broad_q = clean_scene_search_query(primary_entity or anchored_base_kw or state.get("topic", ""), max_words=3)
                    with fg_lock:
                        snap_fg_ph_used = set(used_active_vids)
                    fg_phs = (
                        sf_fg.search_photos(
                            anchored_base_kw,
                            per_page=8,
                            orientation="landscape" if is_lf_eval else "portrait",
                            topic=state.get("topic", ""),
                            narration=fg_narr,
                            used_urls=snap_fg_ph_used,
                        ) or []
                    ) + (
                        sf_fg.search_photos(
                            fg_broad_q,
                            per_page=8,
                            orientation="landscape" if is_lf_eval else "portrait",
                            topic=state.get("topic", ""),
                            narration=fg_narr,
                            used_urls=snap_fg_ph_used,
                        ) or []
                    )
                    for ph_fg in fg_phs:
                        ph_u_fg = str(ph_fg.get("url") or "").strip()
                        if not ph_u_fg or ph_u_fg in snap_fg_ph_used:
                            continue
                        out_fg_stock_jpg = str(media_dir / f"scene_{sid:03d}_final_stock_rescue.jpg")
                        ma_fg.download_file(ph_u_fg, out_fg_stock_jpg)
                        if Path(out_fg_stock_jpg).exists() and _safe_size(Path(out_fg_stock_jpg)) > 8_000:
                            with fg_lock:
                                used_active_vids.add(ph_u_fg)
                            m["media_path"] = out_fg_stock_jpg
                            m["media_type"] = "image"
                            m["pose_images"] = [out_fg_stock_jpg]
                            m["production_method"] = "FINAL_GATE_STOCK_PHOTO_RESCUE"
                            logger.info(
                                f"🛡️ [FinalVisualGate] Đã thay thế Cảnh #{sid} bằng Ảnh Stock HD trực tiếp -> {Path(out_fg_stock_jpg).name}"
                            )
                            return True
                except Exception as _e_fg_sf:
                    logger.debug(f"[FinalVisualGate] Stock photo fallback skip: {_e_fg_sf}")
                return False

            if failed_items_fg:
                from concurrent.futures import ThreadPoolExecutor as _FGPool
                with _FGPool(max_workers=min(4, len(failed_items_fg))) as fg_pool:
                    fg_results = list(fg_pool.map(_rescue_final_gate_item, enumerate(failed_items_fg)))
                rescued_any = any(fg_results)
            if rescued_any:
                eval_report = critic.evaluate_media(scenes_media=scenes_media, primary_entity=primary_entity)
        except Exception as e_fg:
            logger.debug(f"[FinalVisualGate] Rescue skip: {e_fg}")

    logger.info(
        f"🖼️ [VisualCritic] Điểm kỹ thuật media: {eval_report.overall_score}/10 | "
        f"Passed: {eval_report.passed} | Lỗi: {len(eval_report.failed_scene_ids)} cảnh"
    )

    return {
        "scenes_media": scenes_media,
        "media_eval": eval_report.model_dump(),
        "current_step": "MEDIA_EVALUATED",
        "progress_percent": 70,
        "progress_message": f"Thẩm định kỹ thuật hình ảnh: {eval_report.overall_score}/10 ({'Đạt chuẩn' if eval_report.passed else f'Cần sửa {len(eval_report.failed_scene_ids)} cảnh'})",
    }


async def node_refine_media(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node tự động sửa chữa SONG SONG các cảnh bị đánh giá FAIL kỹ thuật hoặc TRÙNG LẶP CẢNH CẮT trong cùng video."""
    scenes_media = list(state.get("scenes_media", []))
    media_eval = state.get("media_eval", {})
    failed_ids = set(media_eval.get("failed_scene_ids", []))
    primary_entity = state.get("primary_entity")
    work_dir = Path(state.get("work_dir", "storage/cache"))
    media_dir = work_dir / "media"
    retry_count = state.get("media_retry_count", 0) + 1

    image_agent = ImageAgent()
    media_agent = MediaAgent()
    ai_video_agent = AIVideoAgent()
    current_niche = state.get("niche") or state.get("channel_style") or state.get("channel_niche") or "general"
    is_scifi_val = (current_niche == "scifi") or any(k in state.get("topic", "").lower() for k in ("scifi", "sci-fi", "alien", "cyborg", "viễn tưởng"))
    logger.info(f"🔄 [LangGraph] Tự động sửa chữa SONG SONG media kỹ thuật / chống trùng cảnh cắt cho: {sorted(failed_ids)} (Lần {retry_count})")

    from app.services.media.media_history import get_recent_media_urls
    from app.services.media.image_quality import clean_search_query_from_prompt
    used_refine_urls = get_recent_media_urls(limit=150)
    for _m in scenes_media:
        if isinstance(_m, dict) and _m.get("source_url"):
            used_refine_urls.add(_m["source_url"])

    total_scenes_refine = len(scenes_media)
    mandatory_video_refine_sids = {1, 2, max(1, total_scenes_refine - 1), total_scenes_refine}
    job_id_refine = int(state.get("job_id") or 0)
    channel_id_refine = str(state.get("target_channel_id") or state.get("channel_id") or "")
    is_lf_refine = (
        str(state.get("video_format") or (state.get("script_data") or {}).get("video_format") or "short").strip().lower() == "long"
        or total_scenes_refine >= 14
    )
    w_ref = 1920 if is_lf_refine else 1080
    h_ref = 1080 if is_lf_refine else 1920
    ar_ref = "16:9" if is_lf_refine else "9:16"
    refine_url_lock = threading.Lock()
    refine_used_bvids: set = set()

    def _verify_and_sanitize_refined_media(
        cand_path: str,
        is_video_file: bool,
        scene_id: int,
    ) -> tuple[bool, str]:
        """
        Cổng kiểm định & tự làm sạch bắt buộc cho mọi media thay thế trong `node_refine_media`:
        - Chặn tuyệt đối ảnh/video tối đen, mờ nhòe (`sharpness < 12.0` cho Cảnh 1, `< 6.0` cho cảnh thân),
          hoặc còn sót chữ poster/báo chí (`poster_text_rows`).
        - Tự động crop lề chữ poster hoặc nâng nét UnsharpMask/FFmpeg trước khi kết luận loại bỏ.
        """
        from app.graph.evaluators.visual_critic import VisualCritic
        from app.services.video_memory_rag import VideoMemoryRAG

        p_cand = Path(cand_path)
        if not p_cand.exists():
            return False, "missing_file"

        req_sharp = 12.0 if scene_id == 1 else 6.0
        if is_video_file:
            if p_cand.stat().st_size < 10_000:
                return False, "video_too_small"
            if scene_id == 1:
                from app.graph.nodes.hook_video_node import _ensure_hook_clip_sharpness_and_luma
                ok_hk_sh, hk_sh_val = _ensure_hook_clip_sharpness_and_luma(str(p_cand), min_sharpness=12.0)
                if not ok_hk_sh:
                    return False, f"hook_sharpness={hk_sh_val:.2f}<12.0"
            gate = VisualCritic().inspect_clip_before_concat(
                str(p_cand),
                min_short_edge=540,
                min_sharpness=12.0 if scene_id == 1 else 5.8,
                check_watermark=True,
                auto_sanitize=True,
            )
            if not gate.get("passed", False):
                return False, "; ".join(gate.get("issues") or ["clip_gate_failed"])
            return True, "clean_video"

        if p_cand.stat().st_size < 2_500:
            return False, "image_too_small"
        if VisualCritic._is_solid_color_or_black(str(p_cand)):
            return False, "solid_or_black_image"

        try:
            has_bad_art, art_reason = VideoMemoryRAG._has_embedded_text_or_collage_artifacts(p_cand.read_bytes())
            if has_bad_art:
                from PIL import Image
                with Image.open(str(p_cand)) as im_raw:
                    im_rgb = im_raw.convert("RGB")
                    w_i, h_i = im_rgb.size
                    if min(w_i, h_i) >= 600:
                        cropped = im_rgb.crop((int(w_i * 0.08), int(h_i * 0.14), int(w_i * 0.92), int(h_i * 0.86)))
                        cropped = cropped.resize((w_i, h_i), Image.Resampling.LANCZOS)
                        cropped.save(str(p_cand), format="JPEG", quality=94)
                has_bad_art, art_reason = VideoMemoryRAG._has_embedded_text_or_collage_artifacts(p_cand.read_bytes())
                if has_bad_art:
                    return False, f"embedded_artifact:{art_reason}"
        except Exception as e_art:
            return False, f"image_verify_err:{e_art}"

        sharp_val = VisualCritic._measure_sharpness(str(p_cand))
        if sharp_val < req_sharp:
            try:
                from PIL import Image, ImageEnhance, ImageFilter
                with Image.open(str(p_cand)) as im_sh:
                    im_rgb = im_sh.convert("RGB")
                    im_enh = ImageEnhance.Contrast(im_rgb).enhance(1.14)
                    im_enh = im_enh.filter(ImageFilter.UnsharpMask(radius=2, percent=155, threshold=2))
                    im_enh.save(str(p_cand), format="JPEG", quality=95)
                sharp_val = VisualCritic._measure_sharpness(str(p_cand))
            except Exception:
                pass
            if sharp_val < req_sharp:
                return False, f"low_sharpness:{sharp_val:.2f}<{req_sharp:.1f}"

        return True, "clean_image"

    refine_visual_plan = state.get("visual_plan") or {}
    refine_strategy_map = {
        int(s.get("scene_id") or 0): s
        for s in (refine_visual_plan.get("scenes_strategy") or [])
        if isinstance(s, dict) and s.get("scene_id")
    }
    scene_eval_issues_map: Dict[int, List[str]] = {}
    for sc_sc in (media_eval.get("scene_scores") or []):
        if isinstance(sc_sc, dict) and sc_sc.get("scene_id"):
            scene_eval_issues_map[int(sc_sc["scene_id"])] = [str(x) for x in (sc_sc.get("issues") or [])]

    async def _refine_single_item(
        item: Dict[str, Any],
        cancel_event: Optional[threading.Event] = None,
        deadline_ts: Optional[float] = None,
    ) -> None:
        def _refine_cancelled() -> bool:
            return (cancel_event is not None and cancel_event.is_set()) or (
                deadline_ts is not None and (deadline_ts - time.monotonic()) <= 1.8
            )

        if item.get("scene_id") not in failed_ids:
            return
        scene_id = int(item["scene_id"])
        kw = item.get("visual_keyword", "")
        narr = item.get("narration", "")
        vp = item.get("visual_prompt", None)
        sc_strat_ref = refine_strategy_map.get(scene_id) or {}
        sc_subj_raw = sc_strat_ref.get("creature_name") or sc_strat_ref.get("subject_entity") or ""
        audited_yt_q = (
            sc_strat_ref.get("youtube_search_query")
            or sc_strat_ref.get("action_or_concept_query")
            or sc_strat_ref.get("human_action_query")
            or kw
        )
        audited_fb_q = str(
            sc_strat_ref.get("fallback_broad_query")
            or sc_strat_ref.get("secondary_stock_query")
            or item.get("fallback_broad_query")
            or ""
        ).strip()
        clean_kw_q = clean_search_query_from_prompt(audited_yt_q or kw or vp or "", narration=narr, topic=state.get("topic", ""), max_words=5) or kw
        overall_refine_q = clean_scene_search_query(
            sc_strat_ref.get("overall_script_query") or primary_entity or state.get("topic", ""),
            max_words=4,
        ) or clean_kw_q
        subj_refine = clean_scene_search_query(sc_subj_raw or primary_entity or state.get("topic", ""), max_words=2) or overall_refine_q
        refine_angle_mods = ("close up action", "real footage", "documentary clip", "dramatic angle")
        angle_mod = refine_angle_mods[(scene_id + retry_count) % len(refine_angle_mods)]
        if audited_fb_q and audited_fb_q.lower() != (clean_kw_q or "").lower():
            clean_kw_q = clean_scene_search_query(audited_fb_q, max_words=4) or clean_kw_q
        if subj_refine and subj_refine.lower().split()[0] not in (clean_kw_q or "").lower():
            clean_kw_q = clean_scene_search_query(f"{subj_refine} {angle_mod}", max_words=4) or clean_kw_q
        if (
            subj_refine
            and len((clean_kw_q or "").split()) < 2
            and subj_refine.lower().split()[0] not in (clean_kw_q or "").lower()
        ):
            clean_kw_q = f"{subj_refine} {clean_kw_q}".strip()
        is_mandatory_vid = scene_id in mandatory_video_refine_sids
        is_video = True
        replaced = False
        sc_dur_sec = _scene_audio_duration(work_dir, scene_id)
        sc_issues_list = scene_eval_issues_map.get(scene_id) or []
        has_semantic_vision_defect = any("Gemini Vision ContactSheet" in iss or "trùng lặp" in iss.lower() for iss in sc_issues_list)

        # 0. Bảo vệ Video Hook thật ở Cảnh 1 & Cảnh 2 (CHỈ khi không bị Gemini Vision ContactSheet bắt lỗi AI dị dạng/chữ rác/trùng lặp):
        # Lưu ý: File video Hook thật của Cảnh 1 có tên `hook_motion_001.mp4` (`CLIMAX_REAL_VIDEO_CONTINUOUS_PEAK` / `CLIMAX_STOCK_VIDEO_REAL_MOTION`),
        # nên chỉ loại trừ `CLIMAX_IMAGES_LIVE_FX` (slide ảnh), TUYỆT ĐỐI không loại trừ tên file `hook_motion_`!
        existing_mp = str(item.get("media_path") or "")
        existing_pm = str(item.get("production_method") or "").upper()
        if (
            scene_id in (1, 2)
            and not has_semantic_vision_defect
            and existing_mp
            and Path(existing_mp).exists()
            and existing_mp.lower().endswith((".mp4", ".mov", ".webm"))
            and "_i2v" not in existing_mp.lower()
            and "CLIMAX_IMAGES_LIVE_FX" not in existing_pm
        ):
            try:
                ok_inp_clean, _ = await asyncio.to_thread(
                    _verify_and_sanitize_refined_media, existing_mp, True, scene_id
                )
                if ok_inp_clean:
                    item["media_type"] = "hook_ready_video" if scene_id == 1 else "video"
                    replaced = True
                    logger.info(f"Scene {scene_id}: 🛡️ Đã làm sạch/nâng nét tại chỗ Video chuyển động thật '{Path(existing_mp).name}' (giữ nguyên Hook Video thật)")
            except Exception as _e_inp:
                logger.debug(f"Scene {scene_id}: In-place hook video sanitize skip: {_e_inp}")

        if is_video:
            if is_lf_refine and not _refine_cancelled():
                try:
                    out_lf_ref = str(media_dir / f"scene_{scene_id:03d}_refined_16x9_r{retry_count}.mp4")
                    lf_ref_res = None
                    tainted_masters: set = set()
                    if has_semantic_vision_defect:
                        src_u_prev = str(item.get("source_url") or "")
                        if src_u_prev:
                            tainted_masters.add(Path(src_u_prev.split("#", 1)[0]).name.lower())
                        tainted_masters.add(f"chapter_{((int(scene_id) - 1) // 3) + 1:02d}_master.mp4")
                        for m_tok in re.findall(r"chapter_\d+_master\.mp4", str(item.get("strategy_rationale") or "").lower()):
                            tainted_masters.add(m_tok)

                    # Ưu tiên số 1 (~1.5–2.5s trên ổ cứng): Cắt lát mới không trùng từ kho Chapter Master 16:9 SẠCH (loại trừ Master bị lỗi ngữ nghĩa)
                    from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                    lf_local_res = await asyncio.to_thread(
                        slice_any_available_chapter_master_for_scene,
                        media_dir=media_dir,
                        scene_id=scene_id,
                        target_duration_sec=sc_dur_sec,
                        job_id=job_id_refine,
                        output_override_path=out_lf_ref,
                        exclude_masters=tainted_masters if tainted_masters else None,
                        max_master_uses_override=5,
                    )
                    if lf_local_res and lf_local_res.get("media_path") and Path(lf_local_res["media_path"]).exists():
                        ok_loc_clean, _ = await asyncio.to_thread(
                            _verify_and_sanitize_refined_media, lf_local_res["media_path"], True, scene_id
                        )
                        if ok_loc_clean:
                            lf_ref_res = lf_local_res
                        else:
                            Path(lf_local_res["media_path"]).unlink(missing_ok=True)

                    # Ưu tiên số 2 (chỉ khi cắt local chưa đạt, dành riêng 26s để luôn chừa >= 18s cho kho Stock/Ảnh HD dự phòng):
                    if not lf_ref_res and not _refine_cancelled():
                        now_ref_ts = time.monotonic()
                        lf_vid_dl = (
                            min(deadline_ts - 18.0, now_ref_ts + 26.0)
                            if (deadline_ts is not None and (deadline_ts - now_ref_ts) > 22.0)
                            else deadline_ts
                        )
                        lf_ref_res = await asyncio.to_thread(
                            _fetch_longform_16x9_broll_clip_sync,
                            scene_id=scene_id,
                            query=clean_kw_q or overall_refine_q,
                            primary_entity=primary_entity or subj_refine,
                            topic=state.get("topic", ""),
                            narration=narr or state.get("topic", ""),
                            target_duration_sec=sc_dur_sec,
                            media_dir=media_dir,
                            used_bvids=refine_used_bvids,
                            used_urls=used_refine_urls,
                            bvid_lock=refine_url_lock,
                            media_agent=media_agent,
                            is_scifi=is_scifi_val,
                            job_id=job_id_refine,
                            output_override_path=out_lf_ref,
                            cancel_event=cancel_event,
                            deadline_ts=lf_vid_dl,
                        )
                    if lf_ref_res and lf_ref_res.get("media_path") and Path(lf_ref_res["media_path"]).exists():
                        ok_lf_clean, _ = await asyncio.to_thread(
                            _verify_and_sanitize_refined_media, lf_ref_res["media_path"], True, scene_id
                        )
                        if ok_lf_clean:
                            item["media_path"] = lf_ref_res["media_path"]
                            item["media_type"] = "video"
                            item["pose_images"] = []
                            item["production_method"] = "REFINED_LONGFORM_16X9_VIDEO"
                            replaced = True
                            logger.info(f"Scene {scene_id}: ✅ Sửa lỗi kỹ thuật bằng Video 16:9 Widescreen mới: '{Path(lf_ref_res['media_path']).name}'")
                        else:
                            Path(lf_ref_res["media_path"]).unlink(missing_ok=True)
                except Exception as ex_lf_r:
                    logger.debug(f"Scene {scene_id}: Long-form 16:9 refine error: {ex_lf_r}")

            # 1. Tầng 1: Với cảnh thân bài (Scene >= 2), ưu tiên tìm kiếm Video Clip Pexels/Pixabay HD mới (~2s) trước để tăng tốc độ dựng
            if not replaced and not _refine_cancelled():
                try:
                    from app.services.video_memory_rag import get_video_memory_rag
                    from app.utils.ffmpeg_helper import trim_video
                    rag_ref = get_video_memory_rag()
                    best_v = await asyncio.to_thread(
                        media_agent.search_best_video_clip,
                        clean_kw_q,
                        used_refine_urls,
                        {"video_format": "long"} if is_lf_refine else None,
                        narr,
                        state.get("topic", ""),
                    )
                    if best_v and best_v.get("url"):
                        ref_cand_urls = [
                            best_v["url"],
                            *(best_v.get("_vision_alternates") or []),
                            *(best_v.get("alternate_urls") or []),
                        ]
                        cand_title = str(best_v.get("title") or clean_kw_q)
                        for cand_u_raw in ref_cand_urls[:4]:
                            cand_url = str(cand_u_raw or "").strip()
                            if not cand_url:
                                continue
                            if not rag_ref.try_claim_in_job_video(
                                job_id=job_id_refine, vid_id=cand_url, url=cand_url, title=cand_title, scene_id=scene_id
                            ):
                                used_refine_urls.add(cand_url)
                                continue
                            raw_v = str(media_dir / f"scene_{scene_id:03d}_refined_raw_r{retry_count}.mp4")
                            out_v = str(media_dir / f"scene_{scene_id:03d}_refined_video_r{retry_count}.mp4")
                            await asyncio.to_thread(media_agent.download_file, cand_url, raw_v)
                            if Path(raw_v).exists() and Path(raw_v).stat().st_size > 15_000:
                                await asyncio.to_thread(
                                    trim_video,
                                    input_path=raw_v,
                                    duration_seconds=sc_dur_sec,
                                    output_path=out_v,
                                    width=w_ref,
                                    height=h_ref,
                                    fps=30,
                                    is_scifi=is_scifi_val,
                                    niche=current_niche,
                                    apply_grading=True,
                                    is_hook_scene=(scene_id == 1),
                                    fair_use_transform=True,
                                    start_offset_sec=0.0,
                                    preserve_continuous_flow=True,
                                )
                                Path(raw_v).unlink(missing_ok=True)
                                if Path(out_v).exists() and Path(out_v).stat().st_size > 12_000:
                                    used_refine_urls.add(cand_url)
                                    ok_v_clean, _ = await asyncio.to_thread(
                                        _verify_and_sanitize_refined_media, out_v, True, scene_id
                                    )
                                    ok_v_sig, _ = (
                                        rag_ref.verify_and_commit_in_job_clip(
                                            job_id=job_id_refine,
                                            scene_id=scene_id,
                                            media_path=out_v,
                                            vid_id=cand_url,
                                            url=cand_url,
                                            title=cand_title,
                                        )
                                        if ok_v_clean
                                        else (False, "failed_refine_gate")
                                    )
                                    if ok_v_clean and ok_v_sig:
                                        item["media_path"] = out_v
                                        item["media_type"] = "video"
                                        item["pose_images"] = []
                                        item["production_method"] = "REFINED_STOCK_VIDEO"
                                        replaced = True
                                        logger.info(f"Scene {scene_id}: ✅ Sửa lỗi kỹ thuật bằng Video Clip Pexels mới độc lập: '{clean_kw_q}'")
                                        break
                                    else:
                                        Path(out_v).unlink(missing_ok=True)
                                        rag_ref.release_in_job_video_claim(
                                            job_id=job_id_refine, vid_id=cand_url, url=cand_url, title=cand_title, scene_id=scene_id
                                        )
                            else:
                                Path(raw_v).unlink(missing_ok=True)
                                rag_ref.release_in_job_video_claim(
                                    job_id=job_id_refine, vid_id=cand_url, url=cand_url, title=cand_title, scene_id=scene_id
                                )
                except Exception as ex_v:
                    logger.debug(f"Scene {scene_id}: Video refine error: {ex_v}")

            # 2. Tầng 2: Săn YouTube Shorts dọc 9:16 mới (qua execute_youtube_short_peak_clone_sync — chỉ khi Stock Video chưa có)
            if not replaced and not is_lf_refine and not _refine_cancelled():
                try:
                    from app.graph.tools.media_tools import execute_youtube_short_peak_clone_sync
                    from app.services.video_memory_rag import get_video_memory_rag
                    rag_yt_ref = get_video_memory_rag()
                    yt_ref_dl = (
                        min(deadline_ts, time.monotonic() + 20.0)
                        if deadline_ts is not None
                        else (time.monotonic() + 20.0)
                    )
                    out_yt_ref = str(media_dir / f"scene_{scene_id:03d}_refined_yt_peak_r{retry_count}.mp4")
                    yt_ref_res = await asyncio.to_thread(
                        execute_youtube_short_peak_clone_sync,
                        search_query=clean_kw_q or overall_refine_q,
                        output_path=out_yt_ref,
                        target_duration_sec=sc_dur_sec,
                        clip_role="HOOK" if scene_id == 1 else "PEAK",
                        used_urls=used_refine_urls,
                        fallback_query=overall_refine_q,
                        subject_entity=subj_refine,
                        narration=narr or state.get("topic", ""),
                        topic=state.get("topic", ""),
                        search_scope="SCRIPT_OVERALL" if (is_mandatory_vid or has_semantic_vision_defect) else "SCENE_SPECIFIC",
                        overall_script_query=overall_refine_q,
                        max_peak_duration_sec=14.0,
                        job_id=job_id_refine,
                        scene_id=scene_id,
                        channel_id=channel_id_refine,
                        cancel_event=cancel_event,
                        deadline_ts=yt_ref_dl,
                    )
                    if yt_ref_res and yt_ref_res.get("media_path") and Path(yt_ref_res["media_path"]).exists():
                        ok_yt_clean, _ = await asyncio.to_thread(
                            _verify_and_sanitize_refined_media, yt_ref_res["media_path"], True, scene_id
                        )
                        ok_yt_sig, _ = (
                            rag_yt_ref.verify_and_commit_in_job_clip(
                                job_id=job_id_refine,
                                scene_id=scene_id,
                                media_path=yt_ref_res["media_path"],
                                vid_id=str(yt_ref_res.get("source_url") or yt_ref_res["media_path"]),
                                url=str(yt_ref_res.get("source_url") or yt_ref_res["media_path"]),
                                title=str(yt_ref_res.get("title") or clean_kw_q),
                            )
                            if ok_yt_clean
                            else (False, "failed_refine_gate")
                        )
                        if ok_yt_clean and ok_yt_sig:
                            if yt_ref_res.get("source_url"):
                                used_refine_urls.add(str(yt_ref_res["source_url"]))
                            item["media_path"] = yt_ref_res["media_path"]
                            item["media_type"] = "video"
                            item["pose_images"] = []
                            item["production_method"] = "REFINED_YOUTUBE_SHORT_PEAK"
                            replaced = True
                            logger.info(f"Scene {scene_id}: ✅ Sửa lỗi kỹ thuật bằng YouTube Short Peak 9:16 mới: '{Path(yt_ref_res['media_path']).name}'")
                        else:
                            Path(yt_ref_res["media_path"]).unlink(missing_ok=True)
                except Exception as ex_yt_r:
                    logger.debug(f"Scene {scene_id}: YouTube Short refine error: {ex_yt_r}")

            # 3. Cắt từ ứng viên `hook_candidates` chưa từng được cảnh nào sử dụng (Zero-Duplicate Guard)
            if not replaced and not _refine_cancelled():
                try:
                    from app.utils.ffmpeg_helper import get_duration_ms, trim_video
                    from app.services.video_memory_rag import get_video_memory_rag
                    rag_ref = get_video_memory_rag()
                    out_slice_v = str(media_dir / f"scene_{scene_id:03d}_refined_peak_slice_r{retry_count}.mp4")
                    active_paths_ref, active_vids_ref = _collect_active_scene_source_paths(
                        scenes_media, exclude_scene_ids=failed_ids
                    )
                    cand_vids = sorted(
                        (work_dir / "hook_candidates").glob("hook_peak_clean_*.mp4"),
                        key=_safe_size,
                        reverse=True,
                    )
                    for src_vp in cand_vids:
                        if _refine_cancelled():
                            break
                        if not src_vp.exists() or src_vp.stat().st_size < 25_000:
                            continue
                        if str(src_vp.resolve()) in active_paths_ref:
                            continue
                        cand_yt_id = _extract_hook_candidate_vid_id(src_vp)
                        if cand_yt_id in active_vids_ref:
                            continue
                        if not rag_ref.try_claim_in_job_video(
                            job_id=job_id_refine,
                            vid_id=cand_yt_id,
                            url=f"https://www.youtube.com/watch?v={cand_yt_id}" if len(cand_yt_id) == 11 else str(src_vp),
                            title=f"Refine Hook Candidate {cand_yt_id}",
                            scene_id=scene_id,
                        ):
                            continue
                        src_total_s = get_duration_ms(str(src_vp)) / 1000.0
                        offset_s = max(0.5, min(src_total_s - sc_dur_sec - 0.3, src_total_s * 0.35)) if src_total_s > sc_dur_sec + 1.5 else 0.0
                        trim_video(
                            input_path=str(src_vp),
                            duration_seconds=sc_dur_sec,
                            output_path=out_slice_v,
                            width=w_ref,
                            height=h_ref,
                            fps=30,
                            is_scifi=is_scifi_val,
                            niche=current_niche,
                            apply_grading=True,
                            is_hook_scene=(scene_id == 1),
                            fair_use_transform=True,
                            start_offset_sec=offset_s,
                            preserve_continuous_flow=True,
                        )
                        if Path(out_slice_v).exists() and Path(out_slice_v).stat().st_size > 15_000:
                            ok_sl_clean, _ = await asyncio.to_thread(
                                _verify_and_sanitize_refined_media, out_slice_v, True, scene_id
                            )
                            ok_sl_sig, _ = (
                                rag_ref.verify_and_commit_in_job_clip(
                                    job_id=job_id_refine,
                                    scene_id=scene_id,
                                    media_path=out_slice_v,
                                    vid_id=cand_yt_id,
                                    url=str(src_vp),
                                    title=f"Refine Hook Candidate {cand_yt_id}",
                                )
                                if ok_sl_clean
                                else (False, "failed_refine_gate")
                            )
                            if ok_sl_clean and ok_sl_sig:
                                item["media_path"] = out_slice_v
                                item["media_type"] = "video"
                                item["pose_images"] = []
                                item["production_method"] = "REFINED_PEAK_VIDEO_SLICE"
                                replaced = True
                                logger.info(f"Scene {scene_id}: ✅ Sửa lỗi kỹ thuật bằng ứng viên Video độc lập chưa dùng '{src_vp.name}' (@{offset_s:.1f}s)")
                                break
                            else:
                                Path(out_slice_v).unlink(missing_ok=True)
                except Exception as ex_sl:
                    logger.debug(f"Scene {scene_id}: Peak slice refine error: {ex_sl}")

        if not replaced and not is_mandatory_vid and not _refine_cancelled():
            try:
                for ph_attempt in range(3):
                    if _refine_cancelled():
                        break
                    q_ph = clean_kw_q if ph_attempt == 0 else (overall_refine_q if ph_attempt == 1 else f"{subj_refine} cinematic")
                    best_ph = await asyncio.to_thread(
                        media_agent.search_best_photo,
                        q_ph,
                        used_refine_urls,
                        None,
                        narr,
                        state.get("topic", ""),
                    )
                    if best_ph and best_ph.get("url"):
                        ph_url = best_ph["url"]
                        used_refine_urls.add(ph_url)
                        out_ph = str(media_dir / f"scene_{scene_id:03d}_refined_stock_{ph_attempt}_r{retry_count}.jpg")
                        await asyncio.to_thread(media_agent.download_file, ph_url, out_ph)
                        if Path(out_ph).exists() and Path(out_ph).stat().st_size > 3000:
                            ok_ph_clean, ph_reason = await asyncio.to_thread(
                                _verify_and_sanitize_refined_media, out_ph, False, scene_id
                            )
                            if ok_ph_clean:
                                item["media_path"] = out_ph
                                item["media_type"] = "image"
                                item["pose_images"] = [out_ph]
                                replaced = True
                                logger.info(f"Scene {scene_id}: ✅ Đã thay thế bằng ảnh Stock HD sạch mới: '{q_ph}'")
                                break
                            else:
                                logger.debug(f"Scene {scene_id}: Ảnh Stock dự phòng #{ph_attempt} chưa đạt ({ph_reason})")
                                Path(out_ph).unlink(missing_ok=True)
            except Exception as ex_ph:
                logger.debug(f"Scene {scene_id}: Stock photo refine error: {ex_ph}")

        # 7. Tầng 7 (Zero-Failed-Scene Guarantee): Tuyệt đối KHÔNG lấy video/ảnh của cảnh khác (`clean_video_donors`)
        # để tránh trùng lặp cảnh 100%! Thay vào đó, tải 1 nguồn Stock Video hoặc Ảnh Google độc lập hoàn toàn mới.
        if not replaced and not _refine_cancelled():
            try:
                from app.utils.ffmpeg_helper import trim_video
                from app.graph.tools.media_tools import execute_google_cinematic_image_sync
                out_resc_v = str(media_dir / f"scene_{scene_id:03d}_refined_clean_rescue_r{retry_count}.mp4")
                q_broad_r = clean_scene_search_query(f"{subj_refine or primary_entity or clean_kw_q} cinematic motion", max_words=4)
                best_v_t7 = await asyncio.to_thread(
                    media_agent.search_best_video_clip,
                    q_broad_r,
                    used_refine_urls,
                    {"video_format": "long"} if is_lf_refine else None,
                    narr,
                    state.get("topic", ""),
                )
                if best_v_t7 and best_v_t7.get("url"):
                    u_t7 = str(best_v_t7["url"])
                    used_refine_urls.add(u_t7)
                    raw_t7 = str(media_dir / f"scene_{scene_id:03d}_t7_raw_r{retry_count}.mp4")
                    await asyncio.to_thread(media_agent.download_file, u_t7, raw_t7)
                    if Path(raw_t7).exists() and Path(raw_t7).stat().st_size > 15_000:
                        await asyncio.to_thread(
                            trim_video,
                            input_path=raw_t7,
                            duration_seconds=sc_dur_sec,
                            output_path=out_resc_v,
                            width=w_ref,
                            height=h_ref,
                            fps=30,
                            is_scifi=is_scifi_val,
                            niche=current_niche,
                            apply_grading=True,
                            is_hook_scene=(scene_id == 1),
                            fair_use_transform=True,
                            start_offset_sec=0.0,
                            preserve_continuous_flow=True,
                        )
                        Path(raw_t7).unlink(missing_ok=True)
                        if Path(out_resc_v).exists() and Path(out_resc_v).stat().st_size > 12_000:
                            item["media_path"] = out_resc_v
                            item["media_type"] = "video"
                            item["pose_images"] = []
                            item["production_method"] = "REFINED_FRESH_STOCK_VIDEO_RESCUE"
                            replaced = True
                            logger.info(f"Scene {scene_id}: 🛡️ [Zero-Failed-Scene Guarantee] Đã tải nguồn Stock Video độc lập mới ('{q_broad_r}')")
                if not replaced and not _refine_cancelled():
                    if (
                        scene_id == 1
                        and existing_mp
                        and Path(existing_mp).exists()
                        and existing_mp.lower().endswith((".mp4", ".mov", ".webm"))
                        and "CLIMAX_IMAGES_LIVE_FX" not in existing_pm
                    ):
                        item["media_path"] = existing_mp
                        item["media_type"] = "hook_ready_video"
                        replaced = True
                        logger.info(
                            f"Scene 1: 🛡️ [Hook Real-Video Lock] Giữ nguyên Video chuyển động thật '{Path(existing_mp).name}' — cấm ghi đè Cảnh 1 bằng ảnh tĩnh Google!"
                        )
                    else:
                        out_resc_img = str(media_dir / f"scene_{scene_id:03d}_refined_gimg_rescue_r{retry_count}.jpg")
                        g_res = await asyncio.to_thread(
                            execute_google_cinematic_image_sync,
                            smart_query=clean_kw_q or q_broad_r,
                            output_path=out_resc_img,
                            narration=narr,
                            topic=state.get("topic", ""),
                            is_hook_scene=(scene_id == 1),
                            subject_entity=subj_refine or primary_entity,
                            used_urls=used_refine_urls,
                            job_id=job_id_refine,
                            scene_id=scene_id,
                        )
                        if g_res and g_res.get("media_path") and Path(g_res["media_path"]).exists():
                            item["media_path"] = g_res["media_path"]
                            item["media_type"] = "image"
                            item["pose_images"] = g_res.get("pose_images") or [g_res["media_path"]]
                            item["production_method"] = "REFINED_GOOGLE_IMAGE_RESCUE"
                            replaced = True
                            logger.info(f"Scene {scene_id}: 🛡️ [Zero-Failed-Scene Guarantee] Đã tải nguồn Ảnh Google độc lập mới ('{clean_kw_q}')")
            except Exception as ex_resc:
                logger.debug(f"Scene {scene_id}: Clean motion rescue error: {ex_resc}")

        if scene_id == 1 and replaced and str(item.get("media_path") or "") != existing_mp:
            item["hook_verified"] = False
            item.pop("hook_sharpness", None)

    refine_sem = asyncio.Semaphore(8 if is_lf_refine else 6)
    refine_item_timeout = 48.0 if is_lf_refine else 38.0

    async def _guarded_refine_single_item(item: Dict[str, Any]) -> None:
        if item.get("scene_id") not in failed_ids:
            return
        async with refine_sem:
            ref_cancel_ev = threading.Event()
            ref_deadline_ts = time.monotonic() + (refine_item_timeout - 2.0)
            from app.agents.curation.scene_context_analyzer_mixin import bind_scene_cancellation
            bind_scene_cancellation(ref_cancel_ev, ref_deadline_ts)
            try:
                await asyncio.wait_for(
                    _refine_single_item(
                        item,
                        cancel_event=ref_cancel_ev,
                        deadline_ts=ref_deadline_ts,
                    ),
                    timeout=refine_item_timeout,
                )
            except Exception as _e_ref_to:
                ref_cancel_ev.set()
                logger.debug(f"[RefineMedia] Scene {item.get('scene_id')} timeout/skip: {_e_ref_to}")
                if is_lf_refine and str(item.get("media_path") or "").lower().endswith("_graphic.png"):
                    try:
                        from app.services.media.longform_chapter_pool import slice_any_available_chapter_master_for_scene
                        sid_to = int(item.get("scene_id") or 0)
                        dur_to = _scene_audio_duration(work_dir, sid_to)
                        out_to_ref = str(media_dir / f"scene_{sid_to:03d}_refined_timeout_pool_r{retry_count}.mp4")
                        to_pool_res = await asyncio.to_thread(
                            slice_any_available_chapter_master_for_scene,
                            media_dir=media_dir,
                            scene_id=sid_to,
                            target_duration_sec=dur_to,
                            job_id=job_id_refine,
                            output_override_path=out_to_ref,
                            max_master_uses_override=20,
                        )
                        if to_pool_res and to_pool_res.get("media_path") and Path(to_pool_res["media_path"]).exists():
                            item["media_path"] = to_pool_res["media_path"]
                            item["media_type"] = "video"
                            item["pose_images"] = []
                            item["production_method"] = "REFINED_LONGFORM_16X9_TIMEOUT_RESCUE"
                            logger.info(f"Scene {sid_to}: 🛡️ [RefineMedia Timeout Rescue] Thay _graphic.png bằng lát cắt ChapterPool 16:9 -> {Path(to_pool_res['media_path']).name}")
                    except Exception:
                        pass

    await asyncio.gather(*[_guarded_refine_single_item(item) for item in scenes_media if isinstance(item, dict)])

    return {
        "scenes_media": scenes_media,
        "media_retry_count": retry_count,
        "current_step": "MEDIA_REFINED",
        "progress_message": f"Đã tự động sửa lại song song {len(failed_ids)} cảnh media kỹ thuật chưa đạt",
    }
