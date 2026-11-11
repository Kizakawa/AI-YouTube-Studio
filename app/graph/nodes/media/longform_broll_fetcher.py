"""app.graph.nodes.media.longform_broll_fetcher — Thu thập và cắt lát B-Roll 16:9 cho Video Dài."""
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
from app.utils.ffmpeg_helper import trim_video

logger = logging.getLogger(__name__)
from app.graph.nodes.media.media_helpers import (
    _safe_mtime,
    _safe_size,
    _extract_hook_candidate_vid_id,
    _collect_active_scene_source_paths,
    _scene_audio_duration,
)

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
    scenes_per_chapter: int = 4,
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

    eff_spc = max(1, int(scenes_per_chapter or 4))
    slot_in_4 = (int(scene_id) - 1) % eff_spc
    chapter_num = ((int(scene_id) - 1) // eff_spc) + 1

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

            if _is_cancelled():
                return None
            with bvid_lock:
                snap_used = set(used_urls)
            best_vid = media_agent._stock.search_landscape_16x9_video_clip(
                queries=prioritized_q_parts,
                used_urls=snap_used,
                topic=topic,
                narration=narration,
                prefer_pixabay=prefer_pixabay,
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
                    # ưu tiên lấy ngay clip thứ 2 từ `alternate_urls` đã duyệt Vision (0s độ trễ API), nếu hết mới gọi tìm thêm!
                    if req_dur_s >= 8.5 and raw_dur_s < (req_dur_s - 0.8) and not _is_cancelled():
                        cand_u_b = None
                        alt_list_b = [u for u in (best_vid.get("alternate_urls") or []) if u and u != cand_u]
                        if alt_list_b:
                            cand_u_b = _atomic_claim_16x9_stock_url({
                                "url": alt_list_b[0],
                                "alternate_urls": alt_list_b[1:],
                                "title": best_vid.get("title") or query,
                            })
                        if not cand_u_b and not _is_cancelled():
                            with bvid_lock:
                                snap_used_b = set(used_urls)
                            second_vid = media_agent._stock.search_landscape_16x9_video_clip(
                                queries=prioritized_q_parts[1:] + prioritized_q_parts[:1],
                                used_urls=snap_used_b,
                                topic=topic,
                                narration=narration,
                                prefer_pixabay=not prefer_pixabay,
                                primary_entity=primary_entity,
                                cancel_event=cancel_event,
                                deadline_ts=eff_dl,
                                claim_in_used=False,
                            )
                            cand_u_b = _atomic_claim_16x9_stock_url(second_vid)
                        if cand_u_b and not _is_cancelled():
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
                scenes_per_chapter=(1 if output_override_path else eff_spc),
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

    # Điều phối Đa Nguồn (Multi-Source Interleaving) trong mỗi Chương (`eff_spc` cảnh/Chương):
    # - Slot 0, 2, 3: Ưu tiên cắt trực tiếp YouTube/Bilibili 16:9 Documentary Chapter Master trên SSD (~1.2s) -> dự phòng Pexels/Pixabay 16:9
    # - Slot 1: Ưu tiên Pexels 16:9 / Pixabay 16:9 HD -> dự phòng YouTube Chapter Pool
    prefer_stock_first = (slot_in_4 == 1)
    prefer_px = (slot_in_4 == 2) or (chapter_num % 2 == 0)

    now_ts = time.monotonic()
    chapter_num_cur = chapter_num
    ch_pool_dir = media_dir / "chapter_pool"
    existing_ch_master = ch_pool_dir / f"chapter_{chapter_num_cur:02d}_master.mp4"
    from app.services.media.longform_chapter_pool import has_verified_chapter_master_meta as _has_ch_meta
    has_local_ch_master = (
        existing_ch_master.exists()
        and _has_ch_meta(existing_ch_master)
        and _safe_size(existing_ch_master) > 80_000
    )

    def _try_cross_slice_existing_masters(strict_entity: bool = False, max_uses: int = 6) -> Optional[Dict[str, Any]]:
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
                max_master_uses_override=max_uses,
                preferred_entity=primary_entity,
                strict_entity_match=strict_entity,
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

    # Trường hợp 1: Chương này ĐÃ có sẵn Master 16:9 sạch (Bilibili / YouTube Documentary) tải ở Phase A trên SSD (~1.2s cắt local 4 góc máy!)
    if not output_override_path and has_local_ch_master:
        if prefer_stock_first:
            st_slot1 = _try_stock_16x9_landscape(prefer_pixabay=prefer_px, sub_deadline_ts=min(deadline_ts, now_ts + 16.0) if deadline_ts else (now_ts + 16.0))
            if st_slot1:
                return st_slot1
        cp_res = _try_chapter_pool_16x9(sub_deadline_ts=deadline_ts)
        if cp_res:
            return cp_res
        st_res = _try_stock_16x9_landscape(prefer_pixabay=prefer_px, sub_deadline_ts=deadline_ts)
        return (
            st_res
            or _try_cross_slice_existing_masters(strict_entity=True, max_uses=6)
            or _try_cross_slice_existing_masters(strict_entity=False, max_uses=10)
        )

    # Trường hợp 2: Chương chưa có Master sau Phase A (hoặc đang chạy refine):
    # Ưu tiên Pexels/Pixabay 16:9 HD (~10s) -> Cắt chéo lát CÙNG THỰC THỂ -> Tải mới Chapter Master -> Cắt lát dự phòng từ kho Master 16:9 sạch (đảm bảo > 85% video chuyển động, 0% rơi về ảnh tĩnh)
    st_dl = min(deadline_ts - 14.0, now_ts + 24.0) if (deadline_ts is not None and (deadline_ts - now_ts) > 22.0) else deadline_ts
    st_res = _try_stock_16x9_landscape(prefer_pixabay=prefer_px, sub_deadline_ts=st_dl)
    if st_res:
        return st_res
    cs_same_ent = _try_cross_slice_existing_masters(strict_entity=True, max_uses=6)
    if cs_same_ent:
        return cs_same_ent
    rem_for_cp = (deadline_ts - time.monotonic()) if deadline_ts is not None else 35.0
    total_pool_masters = len(list(ch_pool_dir.glob("chapter_*_master.mp4"))) if ch_pool_dir.exists() else 0
    if rem_for_cp >= 12.0 and (bool(output_override_path) or slot_in_4 in (0, 1) or total_pool_masters < 6):
        cp_fresh = _try_chapter_pool_16x9(sub_deadline_ts=deadline_ts)
        if cp_fresh:
            return cp_fresh
    return _try_cross_slice_existing_masters(strict_entity=False, max_uses=10)

