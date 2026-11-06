"""app.graph.nodes.media.fetch_media_node — Node thực thi song song các công cụ thu thập Media cho toàn bộ phân cảnh."""
import asyncio
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from app.schemas.contracts import ScriptContract
from app.agents.media_agent import MediaAgent, match_best_pool_photo, clean_scene_search_query
from app.agents.image_agent import ImageAgent
from app.agents.visual_curation_agent import VisualCurationAgent
from app.agents.ai_video_agent import AIVideoAgent
from app.graph.state import VideoWorkflowState

logger = logging.getLogger(__name__)
from app.graph.nodes.media.media_helpers import (
    _safe_mtime,
    _safe_size,
    _extract_hook_candidate_vid_id,
    _collect_active_scene_source_paths,
    _scene_audio_duration,
)
from app.graph.nodes.media.longform_broll_fetcher import _fetch_longform_16x9_broll_clip_sync
from app.graph.nodes.media.post_gather_rescue import run_post_gather_media_rescues

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

    # Điều phối Semaphore 8 luồng song song cho cả Video Dài (30 cảnh) và Shorts (chia tải trên dàn Gemini API Key Pool)
    sem = asyncio.Semaphore(8)
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
            if not media_path and state.get("edit_request"):
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
                from app.services.media.longform_chapter_pool import (
                    slice_any_available_chapter_master_for_scene,
                    extract_longform_scene_with_internal_jumpcut as _lf_jumpcut_fb,
                    is_clip_visually_unique_in_job as _lf_uniq_fb,
                )
                lf_res = await asyncio.to_thread(
                    slice_any_available_chapter_master_for_scene,
                    media_dir=media_dir,
                    scene_id=scene.scene_id,
                    target_duration_sec=dur,
                    job_id=job_id,
                )
                # Nếu ChapterMaster chưa sẵn sàng, tìm nhanh 1 clip Video Stock/Archive 16:9 trực tiếp (14s timeout)
                # để có ngay Video chuyển động thật, tránh tải Ảnh tĩnh rồi lại phải chạy Step B Motion Floor!
                if not (lf_res and lf_res.get("media_path")):
                    try:
                        _check_fetch_deadline()
                        fb_vid_q = clean_scene_search_query(
                            sc_strat.get("action_or_concept_query") or sq or kw or primary_entity or state.get("topic", ""),
                            max_words=4,
                        )
                        with longform_bvid_lock:
                            snap_lf_u = set(used_pool_urls)
                        va_lf_fb = {
                            "video_format": "long",
                            "subject": str(primary_entity or state.get("topic", "")),
                            "core_visual_entity": str(primary_entity or state.get("topic", "")),
                            "topic": state.get("topic", ""),
                            "search_queries": [fb_vid_q, clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=3)],
                        }
                        fb_v_hit = await asyncio.wait_for(
                            asyncio.to_thread(
                                media_agent.search_best_video_clip,
                                fb_vid_q,
                                snap_lf_u,
                                va_lf_fb,
                                scene.narration,
                                state.get("topic", ""),
                            ),
                            timeout=14.0,
                        )
                        if fb_v_hit and fb_v_hit.get("url"):
                            fb_v_url = str(fb_v_hit["url"]).strip()
                            if rag.try_claim_in_job_video(job_id=job_id, vid_id=fb_v_url, url=fb_v_url, title=str(fb_v_hit.get("title") or fb_vid_q), scene_id=scene.scene_id):
                                raw_lf_fb = str(media_dir / f"scene_{scene.scene_id:03d}_lffast_raw.mp4")
                                out_lf_fb = str(media_dir / f"scene_{scene.scene_id:03d}_lf16x9.mp4")
                                dl_fb_ok = await asyncio.to_thread(media_agent.download_file, fb_v_url, raw_lf_fb)
                                if dl_fb_ok and Path(raw_lf_fb).exists():
                                    jc_ok = await asyncio.to_thread(
                                        _lf_jumpcut_fb,
                                        raw_lf_fb,
                                        out_lf_fb,
                                        0.0,
                                        max(2.5, float(dur)),
                                        scene.scene_id,
                                    )
                                    Path(raw_lf_fb).unlink(missing_ok=True)
                                    if jc_ok and Path(out_lf_fb).exists() and _lf_uniq_fb(out_lf_fb, job_id, scene_id=scene.scene_id):
                                        with longform_bvid_lock:
                                            used_pool_urls.add(fb_v_url)
                                        lf_res = {
                                            "media_path": out_lf_fb,
                                            "media_type": "video",
                                            "production_method": "LONGFORM_16X9_FAST_STOCK_VIDEO",
                                            "source_url": fb_v_url,
                                            "rationale": f"16:9 Direct Stock Video ({fb_vid_q})",
                                        }
                                    else:
                                        Path(out_lf_fb).unlink(missing_ok=True)
                                        rag.release_in_job_video_claim(job_id=job_id, vid_id=fb_v_url, url=fb_v_url, title=fb_vid_q, scene_id=scene.scene_id)
                                else:
                                    Path(raw_lf_fb).unlink(missing_ok=True)
                                    rag.release_in_job_video_claim(job_id=job_id, vid_id=fb_v_url, url=fb_v_url, title=fb_vid_q, scene_id=scene.scene_id)
                    except Exception:
                        pass
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
        if scene.scene_id in (1, 2) and not is_long_form and tool_name in ("search_google_cinematic_image", ""):
            tool_name = "clone_youtube_short_peak_clip" if scene.scene_id == 1 else "search_pexels_pixabay_stock_video"
        subj_ent = str(
            getattr(scene, "subject_entity", "")
            or sc_strat.get("creature_name")
            or sc_strat.get("subject_entity")
            or primary_entity
            or state.get("topic", "")
        ).strip()
        sc_scope = str(sc_strat.get("search_scope") or "SCENE_SPECIFIC")
        overall_q = str(sc_strat.get("overall_script_query") or state.get("topic", ""))
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
            from app.graph.tools.media_tools import _sanitize_tool_arg
            from app.graph.tools.tool_schemas import resolve_scene_active_entity
            script_llm_subj = str(getattr(scene, "subject_entity", "") or "").strip()
            script_llm_sqs = [
                str(q_x).strip()
                for q_x in (getattr(scene, "search_queries", None) or [])
                if str(q_x).strip()
            ]
            raw_subj_ent = _sanitize_tool_arg(
                script_llm_subj or sc_strat.get("creature_name") or sc_strat.get("subject_entity"),
                fallback=primary_entity or state.get("topic", ""),
            )
            subj_ent = (
                script_llm_subj
                or resolve_scene_active_entity(
                    scene_query=search_query or kw or sq or "",
                    narration=scene.narration,
                    subject_entity=raw_subj_ent,
                    topic=state.get("topic", ""),
                )
                or raw_subj_ent
            )
            sc_scope = sc_strat.get("search_scope") or "SCENE_SPECIFIC"
            overall_q = sc_strat.get("overall_script_query") or state.get("topic", "")
            v_analysis = dict(batch_analyses.get(scene.scene_id) or {})
            if subj_ent:
                v_analysis["subject"] = subj_ent
                v_analysis["core_visual_entity"] = subj_ent
            if sc_strat.get("resolved_scene_intent") and not v_analysis.get("narrative_subtext"):
                v_analysis["narrative_subtext"] = str(sc_strat.get("resolved_scene_intent") or "")
            elif sc_strat.get("scope_audit_reason") and not v_analysis.get("narrative_subtext"):
                v_analysis["narrative_subtext"] = str(sc_strat.get("scope_audit_reason") or "")
            if sc_strat.get("holistic_script_summary") and not v_analysis.get("holistic_script_summary"):
                v_analysis["holistic_script_summary"] = str(sc_strat.get("holistic_script_summary") or "")
            if sc_strat.get("canonical_protagonists") and not v_analysis.get("canonical_protagonists"):
                v_analysis["canonical_protagonists"] = list(sc_strat.get("canonical_protagonists") or [])
            merged_sc_sqs = list(
                dict.fromkeys([
                    *script_llm_sqs,
                    *(sc_strat.get("storyline_search_queries") or []),
                    *(v_analysis.get("search_queries") or []),
                ])
            )
            if merged_sc_sqs:
                v_analysis["search_queries"] = merged_sc_sqs[:4]
                sc_strat["storyline_search_queries"] = merged_sc_sqs[:4]
            tier0_searched = True
            channel_visual_mode = state.get("visual_mode") or "auto"
            scene_media_pref = getattr(scene, 'media_type', None)
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
                # Chạy SONG SONG đồng thời cả 2 nhánh Pexels/Pixabay HD và YouTube Shorts Peak ngay từ t=0.0s:
                # Nhánh nào tìm được Video đạt chuẩn Vision trước sẽ lập tức hủy nhánh còn lại để giải phóng tài nguyên;
                # nếu 1 nhánh trả về None (không có clip khớp kịch bản), nhánh kia đã chạy sẵn từ đầu không bị trễ 15–30s!
                if not media_path:
                    _check_fetch_deadline()
                    from app.services.video_memory_rag import get_video_memory_rag
                    from app.utils.ffmpeg_helper import trim_video

                    class _BranchCancelEvent:
                        def __init__(self, parent_ev: Optional[threading.Event]) -> None:
                            self._parent = parent_ev
                            self._local = threading.Event()

                        def is_set(self) -> bool:
                            return self._local.is_set() or (self._parent is not None and self._parent.is_set())

                        def set(self) -> None:
                            self._local.set()

                    px_cancel_ev = _BranchCancelEvent(cancel_event)
                    yt_cancel_ev = _BranchCancelEvent(cancel_event)
                    bili_cancel_ev = _BranchCancelEvent(cancel_event)

                    async def _run_pexels_parallel_branch() -> Optional[Dict[str, Any]]:
                        try:
                            from app.graph.tools.tool_schemas import build_single_entity_stock_queries
                            rag_vf = get_video_memory_rag()
                            raw_px_seeds = [
                                str(getattr(scene, "stock_query", "") or "").strip(),
                                fb_q,
                                yt_q,
                                *story_yt_qs,
                                overall_q,
                            ]
                            single_ent_px_qs = build_single_entity_stock_queries(
                                [q_s for q_s in raw_px_seeds if q_s],
                                subject_entity=subj_ent or str((v_analysis or {}).get("subject") or ""),
                                topic=state.get("topic", ""),
                                narration=scene.narration,
                                max_words=3,
                            )
                            vf_q = (
                                (single_ent_px_qs[0] if single_ent_px_qs else "")
                                or fb_q
                                or str(getattr(scene, "stock_query", "") or "").strip()
                                or yt_q
                            )
                            va_vf = dict(v_analysis or {})
                            va_vf["search_queries"] = (
                                single_ent_px_qs[:4]
                                if single_ent_px_qs
                                else [q_x for q_x in dict.fromkeys([vf_q, fb_q, *story_yt_qs, yt_q]) if q_x][:4]
                            )
                            va_vf["skip_establishing_pass"] = True
                            vf_deadline = (
                                min(deadline_ts - 8.0, time.monotonic() + 26.0)
                                if deadline_ts is not None
                                else (time.monotonic() + 26.0)
                            )
                            best_hk_vid = await asyncio.to_thread(
                                media_agent.search_best_video_clip,
                                vf_q,
                                used_pool_urls,
                                va_vf,
                                scene.narration,
                                state.get("topic", ""),
                                cancel_event=px_cancel_ev,
                                deadline_ts=vf_deadline,
                            )
                            if px_cancel_ev.is_set() or not (best_hk_vid and best_hk_vid.get("url")):
                                return None
                            vf_cand_urls = [
                                best_hk_vid["url"],
                                *(best_hk_vid.get("_vision_alternates") or []),
                            ]
                            vf_title = str(best_hk_vid.get("title") or vf_q)
                            for cand_hk_url in vf_cand_urls[:4]:
                                if px_cancel_ev.is_set() or not cand_hk_url:
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
                                if px_cancel_ev.is_set():
                                    Path(raw_hk_out).unlink(missing_ok=True)
                                    rag_vf.release_in_job_video_claim(
                                        job_id=job_id, vid_id=str(cand_hk_url), url=str(cand_hk_url), title=vf_title, scene_id=scene.scene_id
                                    )
                                    return None
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
                                        from app.graph.evaluators.visual_critic import VisualCritic
                                        from app.graph.nodes.media.hook_video_helpers import verify_hook_clip_genuine_motion
                                        from app.graph.tools.youtube_search_ranker import _verify_short_clip_with_gemini_vision
                                        vf_cv_gate = VisualCritic.inspect_clip_before_concat(
                                            vid_hk_out, min_short_edge=540, min_sharpness=5.2, check_watermark=True, allow_snow=True, auto_sanitize=True
                                        )
                                        ok_vf_mot, _, _ = await asyncio.to_thread(verify_hook_clip_genuine_motion, vid_hk_out)
                                        is_vf_vis_ok = False
                                        if vf_cv_gate.get("passed", False) and ok_vf_mot:
                                            is_vf_vis_ok, _, _ = await asyncio.to_thread(
                                                _verify_short_clip_with_gemini_vision,
                                                vid_hk_out,
                                                state.get("topic", ""),
                                                scene.narration,
                                                subj_ent,
                                                overall_q,
                                                sc_scope,
                                                vf_title,
                                                False,
                                                vf_q,
                                                True,
                                                px_cancel_ev,
                                                max(vf_deadline, time.monotonic() + 12.0),
                                            )
                                        ok_vf_sig = False
                                        if vf_cv_gate.get("passed", False) and ok_vf_mot and is_vf_vis_ok:
                                            ok_vf_sig, _ = rag_vf.verify_and_commit_in_job_clip(
                                                job_id=job_id,
                                                scene_id=scene.scene_id,
                                                media_path=vid_hk_out,
                                                vid_id=str(cand_hk_url),
                                                url=str(cand_hk_url),
                                                title=vf_title,
                                            )
                                        used_pool_urls.add(str(cand_hk_url))
                                        if ok_vf_sig and is_vf_vis_ok:
                                            logger.info(
                                                f"Scene {scene.scene_id}: 🎬 [Parallel Pexels+YT -> Pexels Won] "
                                                f"Đã nạp Video B-Roll Pexels chuyển động thật cho '{vf_q}'"
                                            )
                                            return {
                                                "branch": "pexels",
                                                "media_path": vid_hk_out,
                                                "media_type": "video",
                                                "prod_method": "CLIMAX_STOCK_VIDEO_REAL_MOTION",
                                                "chosen_strategy": "STOCK_VIDEO",
                                                "source_url": str(cand_hk_url),
                                                "vid_id": str(cand_hk_url),
                                                "title": vf_title,
                                                "rationale": f"Video-First Stock Video ({vf_title[:45]})",
                                            }
                                        else:
                                            Path(vid_hk_out).unlink(missing_ok=True)
                                            rag_vf.release_in_job_video_claim(
                                                job_id=job_id, vid_id=str(cand_hk_url), url=str(cand_hk_url), title=vf_title, scene_id=scene.scene_id
                                            )
                                else:
                                    Path(raw_hk_out).unlink(missing_ok=True)
                                    rag_vf.release_in_job_video_claim(
                                        job_id=job_id, vid_id=str(cand_hk_url), url=str(cand_hk_url), title=vf_title, scene_id=scene.scene_id
                                    )
                        except Exception as hk_vf_err:
                            logger.debug(f"Scene {scene.scene_id}: Parallel Pexels lookup fallback: {hk_vf_err}")
                        return None

                    async def _run_youtube_parallel_branch() -> Optional[Dict[str, Any]]:
                        if tool_name == "search_pexels_pixabay_stock_video" and scene.scene_id != 1:
                            # Cho nhánh Pexels lợi thế khởi chạy 1.5s, nhưng vẫn kích hoạt nhánh YouTube/Bilibili dự phòng
                            # để tuyệt đối không làm rớt cảnh xuống ảnh tĩnh khi Pexels không có clip khớp chủ thể!
                            await asyncio.sleep(1.5)
                            if yt_cancel_ev.is_set():
                                return None
                        try:
                            _prefer_non_yt_node = os.getenv("PREFER_NON_YOUTUBE_SOURCES", "false").strip().lower() in ("1", "true", "yes", "on")
                            _yt_budget_sec = (
                                50.0
                                if scene.scene_id == 1
                                else (44.0 if _prefer_non_yt_node else 46.0)
                            )
                            _reserve_tail_sec = 10.0 if scene.scene_id == 1 else 12.0
                            yt_sub_deadline = (
                                min(deadline_ts - _reserve_tail_sec, time.monotonic() + _yt_budget_sec)
                                if deadline_ts is not None
                                else (time.monotonic() + _yt_budget_sec)
                            )
                            if yt_sub_deadline <= time.monotonic() + 4.0:
                                return None
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
                                skip_youtube_search=False,
                                skip_stock_fallback=True,
                                allow_bili_fallback=False,
                                storyline_queries=story_yt_qs,
                                cancel_event=yt_cancel_ev,
                                deadline_ts=yt_sub_deadline,
                            )
                            if yt_res and yt_res.get("media_path") and Path(yt_res["media_path"]).exists():
                                fp_dur = float(yt_res.get("full_peak_duration_sec") or dur)
                                return {
                                    "branch": "youtube",
                                    "media_path": yt_res["media_path"],
                                    "media_type": yt_res.get("media_type", "video"),
                                    "full_peak_path": yt_res.get("full_peak_path"),
                                    "full_peak_duration_sec": fp_dur,
                                    "source_url": str(yt_res.get("source_url") or ""),
                                    "vid_id": str(yt_res.get("youtube_id") or yt_res.get("source_url") or ""),
                                    "title": str(yt_res.get("title") or yt_q),
                                    "prod_method": "CLIMAX_REAL_VIDEO_CONTINUOUS_PEAK" if scene.scene_id == 1 else "CLIMAX_PEAK_SHORT_SPLICE",
                                    "chosen_strategy": "YOUTUBE_SHORT_PEAK_CLONE",
                                    "rationale": (
                                        f"YouTube Shorts Peak [{yt_res.get('search_scope', sc_scope)}] "
                                        f"({yt_res.get('window')}, full_peak={fp_dur:.1f}s): {yt_res.get('title', '')[:45]}"
                                    ),
                                }
                        except Exception as yt_p_err:
                            logger.debug(f"Scene {scene.scene_id}: Parallel YouTube lookup fallback: {yt_p_err}")
                        return None

                    async def _run_bilibili_parallel_branch() -> Optional[Dict[str, Any]]:
                        await asyncio.sleep(2.5)
                        if bili_cancel_ev.is_set():
                            return None
                        try:
                            from app.graph.tools.video_fallback_executors import _try_bilibili_broll_fallback
                            bili_sub_deadline = (
                                min(deadline_ts - 10.0, time.monotonic() + 50.0)
                                if deadline_ts is not None
                                else (time.monotonic() + 50.0)
                            )
                            if bili_sub_deadline <= time.monotonic() + 6.0:
                                return None
                            out_bili_par = str(
                                work_dir / "hook_motion_001_bili.mp4"
                                if scene.scene_id == 1
                                else media_dir / f"scene_{scene.scene_id:03d}_bili_parallel.mp4"
                            )
                            bili_phrases = [
                                q_x for q_x in dict.fromkeys([yt_q, *story_yt_qs, fb_q, overall_q]) if q_x
                            ][:4]
                            bili_res = await asyncio.to_thread(
                                _try_bilibili_broll_fallback,
                                search_phrases=bili_phrases,
                                search_query=yt_q or overall_q,
                                subject_entity=subj_ent or primary_entity or "",
                                topic=state.get("topic", ""),
                                narration=scene.narration,
                                overall_script_query=overall_q,
                                effective_scope=sc_scope,
                                output_path=out_bili_par,
                                target_duration_sec=dur,
                                clip_role=clip_role,
                                allow_3d_cgi_monster=False,
                                used=used_pool_urls,
                                job_id=job_id,
                                eff_scene_id=scene.scene_id,
                                cancel_event=bili_cancel_ev,
                                deadline_ts=bili_sub_deadline,
                            )
                            if bili_res and bili_res.get("media_path") and Path(bili_res["media_path"]).exists():
                                return {
                                    "branch": "bilibili",
                                    "media_path": bili_res["media_path"],
                                    "media_type": "hook_ready_video" if scene.scene_id == 1 else "video",
                                    "full_peak_path": bili_res.get("full_peak_path"),
                                    "full_peak_duration_sec": float(bili_res.get("full_peak_duration_sec") or dur),
                                    "source_url": str(bili_res.get("source_url") or bili_res["media_path"]),
                                    "vid_id": str(bili_res.get("youtube_id") or bili_res.get("source_url") or ""),
                                    "title": str(bili_res.get("title") or yt_q),
                                    "prod_method": "CLIMAX_REAL_VIDEO_CONTINUOUS_PEAK" if scene.scene_id == 1 else "CLIMAX_PEAK_SHORT_SPLICE",
                                    "chosen_strategy": "BILIBILI_DOCUMENTARY_PEAK_CLONE",
                                    "rationale": f"Bilibili Documentary Parallel Video: {str(bili_res.get('title') or yt_q)[:45]}",
                                }
                        except Exception as bili_p_err:
                            logger.debug(f"Scene {scene.scene_id}: Parallel Bilibili lookup fallback: {bili_p_err}")
                        return None

                    t_px = asyncio.create_task(_run_pexels_parallel_branch())
                    t_yt = asyncio.create_task(_run_youtube_parallel_branch())
                    t_bili = asyncio.create_task(_run_bilibili_parallel_branch())
                    pending_tasks = {t_px, t_yt, t_bili}
                    win_res: Optional[Dict[str, Any]] = None
                    extra_results: List[Dict[str, Any]] = []
                    race_max_sec = 56.0 if scene.scene_id == 1 else 52.0
                    race_deadline_ts = (
                        min(deadline_ts - 14.0, time.monotonic() + race_max_sec)
                        if deadline_ts is not None
                        else (time.monotonic() + race_max_sec)
                    )

                    while pending_tasks and win_res is None:
                        wait_rem = race_deadline_ts - time.monotonic()
                        if wait_rem <= 0.15:
                            break
                        done_tasks, pending_tasks = await asyncio.wait(
                            pending_tasks,
                            return_when=asyncio.FIRST_COMPLETED,
                            timeout=wait_rem,
                        )
                        if not done_tasks:
                            break
                        for d_t in done_tasks:
                            try:
                                r_val = d_t.result()
                            except Exception:
                                r_val = None
                            if r_val and r_val.get("media_path") and Path(r_val["media_path"]).exists():
                                if win_res is None:
                                    win_res = r_val
                                else:
                                    extra_results.append(r_val)

                    # [RULE 2] Không bao giờ bật yt_cancel_ev sớm nếu chưa có nhánh nào trả về video thật (win_res is None)
                    # — chờ thêm tối đa 8s (nhưng luôn chừa 12s trước scene_deadline_ts) cho tác vụ đang tải/kiểm duyệt hoàn tất.
                    if win_res is None and pending_tasks:
                        grace_rem = (
                            max(0.5, min(8.0, deadline_ts - time.monotonic() - 12.0))
                            if deadline_ts is not None
                            else 8.0
                        )
                        if grace_rem > 0.5:
                            done_grace, pending_tasks = await asyncio.wait(
                                pending_tasks,
                                return_when=asyncio.FIRST_COMPLETED,
                                timeout=grace_rem,
                            )
                            for d_g in done_grace:
                                try:
                                    r_g = d_g.result()
                                except Exception:
                                    r_g = None
                                if r_g and r_g.get("media_path") and Path(r_g["media_path"]).exists():
                                    if win_res is None:
                                        win_res = r_g
                                    else:
                                        extra_results.append(r_g)

                    px_cancel_ev.set()
                    yt_cancel_ev.set()
                    bili_cancel_ev.set()
                    for p_t in pending_tasks:
                        p_t.cancel()

                    if extra_results:
                        rag_cleanup = get_video_memory_rag()
                        for ex_r in extra_results:
                            try:
                                Path(ex_r["media_path"]).unlink(missing_ok=True)
                                rag_cleanup.release_in_job_video_claim(
                                    job_id=job_id,
                                    vid_id=str(ex_r.get("vid_id") or ex_r.get("source_url") or ""),
                                    url=str(ex_r.get("source_url") or ""),
                                    title=str(ex_r.get("title") or ""),
                                    scene_id=scene.scene_id,
                                )
                            except Exception:
                                pass

                    if win_res is not None:
                        media_path = win_res["media_path"]
                        media_type = win_res.get("media_type", "video")
                        vision_curated = True
                        full_peak_path = win_res.get("full_peak_path")
                        full_peak_duration_sec = float(win_res.get("full_peak_duration_sec") or dur)
                        source_url_used = str(win_res.get("source_url") or "")
                        prod_method = win_res["prod_method"]
                        chosen_strategy = win_res["chosen_strategy"]
                        rationale = win_res["rationale"]
                if not media_path and scene.scene_id in (1, 2):
                    # 100% Vision-Verified Pure Video Guarantee cho 5-7s đầu (Cảnh 1 & Cảnh 2):
                    # Tuyệt đối KHÔNG tải bừa clip Pexels chưa qua kiểm định Vision!
                    try:
                        from app.graph.tools.video_fallback_executors import _try_bilibili_broll_fallback
                        from app.utils.ffmpeg_helper import trim_video, get_duration_ms
                        from app.graph.nodes.hook_video_node import verify_hook_clip_genuine_motion
                        out_op_mp4 = str(
                            work_dir / "hook_motion_001.mp4"
                            if scene.scene_id == 1
                            else media_dir / f"scene_{scene.scene_id:03d}_bili_open.mp4"
                        )
                        bili_op_res = await asyncio.to_thread(
                            _try_bilibili_broll_fallback,
                            search_phrases=[q_x for q_x in dict.fromkeys([yt_q, *story_yt_qs, fb_q, overall_q]) if q_x][:4],
                            search_query=yt_q or overall_q,
                            subject_entity=subj_ent or primary_entity or "",
                            topic=state.get("topic", ""),
                            narration=scene.narration,
                            overall_script_query=overall_q,
                            effective_scope=sc_scope,
                            output_path=out_op_mp4,
                            target_duration_sec=dur,
                            clip_role=clip_role,
                            allow_3d_cgi_monster=False,
                            used=used_pool_urls,
                            job_id=job_id,
                            eff_scene_id=scene.scene_id,
                            cancel_event=cancel_event,
                            deadline_ts=deadline_ts,
                        )
                        if bili_op_res and bili_op_res.get("media_path") and Path(bili_op_res["media_path"]).exists():
                            media_path = bili_op_res["media_path"]
                            media_type = "hook_ready_video" if scene.scene_id == 1 else "video"
                            vision_curated = True
                            prod_method = "BILIBILI_DOCUMENTARY_BROLL_CLONE"
                            chosen_strategy = "YOUTUBE_SHORT_PEAK_CLONE"
                            source_url_used = str(bili_op_res.get("source_url") or media_path)
                            rationale = f"Opening Vision-Verified Bilibili Documentary ({bili_op_res.get('title', yt_q)[:45]})"
                            logger.info(
                                f"Scene {scene.scene_id}: 🔥 [Opening 5-7s Bilibili Documentary] Đã nạp Video chuyển động thật kiểm định Vision -> {Path(media_path).name}"
                            )
                        if not media_path and scene.scene_id == 1:
                            reserve_cand = work_dir / "hook_motion_001_fullpeak.mp4"
                            if reserve_cand.exists() and reserve_cand.stat().st_size > 20_000:
                                res_dur_s = get_duration_ms(str(reserve_cand)) / 1000.0
                                if res_dur_s >= max(2.4, dur * 0.75):
                                    out_res_s = str(work_dir / "hook_motion_001.mp4")
                                    await asyncio.to_thread(
                                        trim_video,
                                        input_path=str(reserve_cand),
                                        duration_seconds=max(3.0, dur + 0.25),
                                        output_path=out_res_s,
                                        width=1920 if is_long_form else 1080,
                                        height=1080 if is_long_form else 1920,
                                        fps=30,
                                        is_scifi=is_scifi_val,
                                        niche=current_niche,
                                        apply_grading=True,
                                        is_hook_scene=True,
                                        fair_use_transform=False,
                                        start_offset_sec=0.0,
                                        preserve_continuous_flow=True,
                                    )
                                    if Path(out_res_s).exists() and Path(out_res_s).stat().st_size > 15_000:
                                        ok_r_mot, _, _ = await asyncio.to_thread(verify_hook_clip_genuine_motion, out_res_s)
                                        if ok_r_mot:
                                            media_path = out_res_s
                                            media_type = "hook_ready_video"
                                            vision_curated = True
                                            prod_method = "CLIMAX_PEAK_SHORT_SPLICE"
                                            chosen_strategy = "YOUTUBE_SHORT_PEAK_CLONE"
                                            source_url_used = str(reserve_cand)
                                            rationale = f"Vision-Verified Hook Video ({reserve_cand.name})"
                                            logger.info(
                                                f"Scene 1: 🔥 [Scene 1 Hook Recovery] Đã nạp Video chuyển động thật đã duyệt Vision ('{reserve_cand.name}') -> {Path(out_res_s).name}"
                                            )
                                        else:
                                            Path(out_res_s).unlink(missing_ok=True)
                    except Exception as _e_op_v:
                        logger.debug(f"Scene {scene.scene_id}: Opening pure video rescue skip: {_e_op_v}")

                if not media_path and scene.scene_id not in (1, 2):
                    _check_fetch_deadline()
                    g_target_cnt = scene_target_imgs
                    logger.info(f"Scene {scene.scene_id}: ⚡ YouTube Shorts & Stock Vision không có clip đạt chuẩn cho '{yt_q}' -> Chuyển sang tìm bộ {g_target_cnt} ảnh Top Google Images theo các chunk LLM!")
                    out_img = str(media_dir / f"scene_{scene.scene_id:03d}_gsearch.jpg")
                    img_res = await asyncio.to_thread(
                        execute_google_cinematic_image_sync,
                        smart_query=yt_q,
                        output_path=out_img,
                        narration=scene.narration,
                        topic=state.get("topic", ""),
                        used_urls=used_pool_urls,
                        is_hook_scene=False,
                        subject_entity=subj_ent,
                        search_scope=sc_scope,
                        overall_script_query=overall_q,
                        job_id=job_id,
                        scene_id=scene.scene_id,
                        target_image_count=g_target_cnt,
                        storyline_queries=story_yt_qs,
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
                            )
                            if is_valid_hd:
                                cand_url = str(best_vid["url"])
                                raw_vid_out = str(media_dir / f"scene_{scene.scene_id:03d}_vfirst_raw.mp4")
                                vid_out = str(media_dir / f"scene_{scene.scene_id:03d}_vfirst.mp4")
                                await asyncio.to_thread(media_agent.download_file, cand_url, raw_vid_out)
                                if Path(raw_vid_out).exists() and Path(raw_vid_out).stat().st_size > 15000:
                                    await asyncio.to_thread(
                                        trim_video,
                                        input_path=raw_vid_out,
                                        duration_seconds=max(3.0, dur + 0.25),
                                        output_path=vid_out,
                                        width=1920 if is_long_form else 1080,
                                        height=1080 if is_long_form else 1920,
                                        fps=30,
                                        is_scifi=is_scifi_val,
                                        niche=current_niche,
                                        apply_grading=True,
                                        is_hook_scene=False,
                                        fair_use_transform=True,
                                    )
                                    Path(raw_vid_out).unlink(missing_ok=True)
                                    if Path(vid_out).exists() and Path(vid_out).stat().st_size > 15000:
                                        from app.graph.evaluators.visual_critic import VisualCritic
                                        from app.graph.tools.youtube_search_ranker import _verify_short_clip_with_gemini_vision
                                        body_gate = VisualCritic.inspect_clip_before_concat(
                                            vid_out, min_short_edge=540, min_sharpness=5.2, check_watermark=True, allow_snow=True, auto_sanitize=True
                                        )
                                        is_body_vis_ok = bool(best_vid.get("vision_curated"))
                                        if body_gate.get("passed", False) and not is_body_vis_ok:
                                            is_body_vis_ok, _, _ = await asyncio.to_thread(
                                                _verify_short_clip_with_gemini_vision,
                                                vid_out,
                                                state.get("topic", ""),
                                                scene.narration,
                                                subj_ent,
                                                overall_q,
                                                sc_scope,
                                                str(best_vid.get("title") or img_q),
                                                False,
                                                img_q,
                                                True,
                                                cancel_event,
                                                max(deadline_ts or 0.0, time.monotonic() + 12.0),
                                            )
                                        ok_b_sig, _ = rag.verify_and_commit_in_job_clip(
                                            job_id=job_id,
                                            scene_id=scene.scene_id,
                                            media_path=vid_out,
                                            vid_id=cand_url,
                                            url=cand_url,
                                            title=str(best_vid.get("title") or img_q),
                                        )
                                        if body_gate.get("passed", False) and is_body_vis_ok and ok_b_sig:
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
                                        else:
                                            Path(vid_out).unlink(missing_ok=True)
                                else:
                                    Path(raw_vid_out).unlink(missing_ok=True)
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
                            raw_hk_out2 = str(media_dir / f"scene_{scene.scene_id:03d}_vfirst_raw.mp4")
                            vid_hk_out = str(media_dir / f"scene_{scene.scene_id:03d}_vfirst.mp4")
                            await asyncio.to_thread(media_agent.download_file, cand_hk_url, raw_hk_out2)
                            if Path(raw_hk_out2).exists() and Path(raw_hk_out2).stat().st_size > 15_000:
                                await asyncio.to_thread(
                                    trim_video,
                                    input_path=raw_hk_out2,
                                    duration_seconds=max(3.0, dur + 0.25),
                                    output_path=vid_hk_out,
                                    width=1920 if is_long_form else 1080,
                                    height=1080 if is_long_form else 1920,
                                    fps=30,
                                    is_scifi=is_scifi_val,
                                    niche=current_niche,
                                    apply_grading=True,
                                    is_hook_scene=(scene.scene_id == 1),
                                    fair_use_transform=True,
                                )
                                Path(raw_hk_out2).unlink(missing_ok=True)
                                if Path(vid_hk_out).exists() and Path(vid_hk_out).stat().st_size > 15_000:
                                    from app.graph.evaluators.visual_critic import VisualCritic
                                    from app.graph.tools.youtube_search_ranker import _verify_short_clip_with_gemini_vision
                                    hk2_gate = VisualCritic.inspect_clip_before_concat(
                                        vid_hk_out, min_short_edge=540, min_sharpness=5.2, check_watermark=True, allow_snow=True, auto_sanitize=True
                                    )
                                    is_hk2_vis_ok = bool(best_hk_vid.get("vision_curated"))
                                    if hk2_gate.get("passed", False) and not is_hk2_vis_ok:
                                        is_hk2_vis_ok, _, _ = await asyncio.to_thread(
                                            _verify_short_clip_with_gemini_vision,
                                            vid_hk_out,
                                            state.get("topic", ""),
                                            scene.narration,
                                            subj_ent,
                                            overall_q,
                                            sc_scope,
                                            str(best_hk_vid.get("title") or img_q),
                                            False,
                                            img_q,
                                            True,
                                            cancel_event,
                                            max(deadline_ts or 0.0, time.monotonic() + 12.0),
                                        )
                                    ok_hk2_sig, _ = rag.verify_and_commit_in_job_clip(
                                        job_id=job_id,
                                        scene_id=scene.scene_id,
                                        media_path=vid_hk_out,
                                        vid_id=cand_hk_url,
                                        url=cand_hk_url,
                                        title=str(best_hk_vid.get("title") or img_q),
                                    )
                                    if hk2_gate.get("passed", False) and is_hk2_vis_ok and ok_hk2_sig:
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
                                    else:
                                        Path(vid_hk_out).unlink(missing_ok=True)
                            else:
                                Path(raw_hk_out2).unlink(missing_ok=True)
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
                        target_image_count=3 if scene.scene_id == 1 else scene_target_imgs,
                        storyline_queries=list(sc_strat.get("storyline_search_queries") or []),
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
            v_analysis = batch_analyses.get(scene.scene_id) or {}
            if not media_path:
                if is_long_form:
                    chosen_strategy = "LONGFORM_16X9_DOCUMENTARY_VIDEO"
                    prod_method = "LONGFORM_16X9"
                else:
                    chosen_strategy = sc_strat.get("strategy", "STOCK_VIDEO")
                    prod_method = sc_strat.get("production_method") or "STOCK_FOOTAGE"
            tool = sc_strat.get("tool") or tool_name or ""
            rationale = sc_strat.get("rationale", "")
            search_query = sc_strat.get("search_query") or sc_strat.get("youtube_search_query") or sc_strat.get("smart_image_query") or sq or kw
            camera_motion = sc_strat.get("camera_motion") or ("hyper_zoom" if idx == 0 else "zoom_in")
            sfx_cue = sc_strat.get("sfx_cue") or ("whoosh" if idx == 0 else "hit")

        # ══════════════════════════════════════════════════════════════════════
        # DỰ PHÒNG GỌN NHẸ DUY NHẤT KHI TOOL CHÍNH CHƯA TRẢ VỀ FILE (`not media_path`)
        # ══════════════════════════════════════════════════════════════════════
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

        if not media_path and not is_long_form and scene.scene_id not in (1, 2):
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
            except Exception as fb_ph_e:
                logger.debug(f"Scene {scene.scene_id}: Fallback photo error: {fb_ph_e}")

        if not media_path:
            try:
                from app.graph.tools.media_tools import execute_google_cinematic_image_sync
                out_last_gimg = str(media_dir / f"scene_{scene.scene_id:03d}_gimg_fallback.jpg")
                g_last_res = await asyncio.to_thread(
                    execute_google_cinematic_image_sync,
                    smart_query=search_query or getattr(scene, "visual_keyword", "") or primary_entity or state.get("topic", ""),
                    output_path=out_last_gimg,
                    narration=scene.narration,
                    topic=state.get("topic", ""),
                    is_hook_scene=(scene.scene_id == 1),
                    subject_entity=subj_ent,
                    used_urls=used_pool_urls,
                    job_id=job_id,
                    scene_id=scene.scene_id,
                )
                if g_last_res and g_last_res.get("media_path") and Path(g_last_res["media_path"]).exists():
                    media_path = g_last_res["media_path"]
                    media_type = "image"
                    pose_images = g_last_res.get("pose_images") or [media_path]
                    vision_curated = True
                    prod_method = "GOOGLE_TOP_IMAGE_FALLBACK"
                    chosen_strategy = "GEMINI_SEARCH_3D"
                    source_url_used = str(g_last_res.get("source_url") or media_path)
            except Exception as _e_last_g:
                logger.debug(f"Scene {scene.scene_id}: Pre-placeholder Google image rescue error: {_e_last_g}")

        if not media_path:
            out_g = str(media_dir / f"scene_{scene.scene_id:03d}_graphic.png")
            from app.utils.ffmpeg_helper import create_placeholder_image
            create_placeholder_image(out_g, text=scene.visual_keyword or f"Scene {scene.scene_id}", scene_id=scene.scene_id)
            media_path = out_g
            media_type = "image"

        if media_path and media_type == "image":
            if not pose_images:
                pose_images = [media_path]
            rag.verify_and_commit_in_job_image(
                job_id=job_id,
                scene_id=scene.scene_id,
                image_path=media_path,
                url=source_url_used or media_path,
            )

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
        scene_timeout_sec = 105.0 if is_long_form else 92.0
        async with sem:
            scene_cancel_ev = threading.Event()
            scene_deadline_ts = time.monotonic() + (scene_timeout_sec - 3.0)
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
                logger.warning(
                    f"Scene {scene.scene_id}: ⏱️ Quá hạn khi tải media ({_e_gf_to}) -> Phục hồi file trên đĩa hoặc chuyển cho Post-Gather Rescue!"
                )
                res = None
                _cand_early_vids = list(media_dir.glob(f"scene_{scene.scene_id:03d}_*.mp4"))
                if scene.scene_id == 1:
                    _cand_early_vids.extend([
                        p for p in (work_dir / "hook_motion_001.mp4", work_dir / "hook_motion_001_fullpeak.mp4")
                        if p.exists()
                    ])
                early_vids = sorted(
                    [
                        p for p in _cand_early_vids
                        if "_raw" not in p.name.lower()
                        and "_cand" not in p.name.lower()
                        and "_stage" not in p.name.lower()
                        and is_completed_clean_mp4(p, min_size_bytes=15_000, min_duration_ms=1200)
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
                elif early_imgs:
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
            if not res or not isinstance(res, dict) or str(res.get("media_path") or "").endswith("_graphic.png"):
                try:
                    from app.graph.tools.media_tools import execute_google_cinematic_image_sync
                    out_to_gimg = str(media_dir / f"scene_{scene.scene_id:03d}_timeout_gimg.jpg")
                    g_to_res = await asyncio.wait_for(
                        asyncio.to_thread(
                            execute_google_cinematic_image_sync,
                            smart_query=getattr(scene, "visual_keyword", "") or primary_entity or state.get("topic", ""),
                            output_path=out_to_gimg,
                            narration=scene.narration,
                            topic=state.get("topic", ""),
                            is_hook_scene=(scene.scene_id == 1),
                            subject_entity=primary_entity,
                            used_urls=used_pool_urls,
                            job_id=job_id,
                            scene_id=scene.scene_id,
                        ),
                        timeout=14.0,
                    )
                    if g_to_res and g_to_res.get("media_path") and Path(g_to_res["media_path"]).exists():
                        rec_to_poses = g_to_res.get("pose_images") or [g_to_res["media_path"]]
                        res = {
                            "scene_id": scene.scene_id,
                            "media_path": g_to_res["media_path"],
                            "media_type": "image",
                            "pose_images": rec_to_poses,
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
                            "strategy_rationale": "Timeout Google Image Rescue",
                            "source_url": str(g_to_res.get("source_url") or g_to_res["media_path"]),
                        }
                except Exception as _e_to_g:
                    logger.debug(f"Scene {scene.scene_id}: Timeout Google image rescue skip: {_e_to_g}")
            if not res or not isinstance(res, dict):
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
            if isinstance(res, dict):
                _sc_st = strategy_map.get(scene.scene_id) or {}
                _sc_llm_subj = str(getattr(scene, "subject_entity", "") or _sc_st.get("subject_entity") or "").strip()
                if _sc_llm_subj and not res.get("subject_entity"):
                    res["subject_entity"] = _sc_llm_subj
                if getattr(scene, "stock_query", None) and not res.get("stock_query"):
                    res["stock_query"] = str(getattr(scene, "stock_query") or "").strip()
                _st_qs = list(
                    dict.fromkeys([
                        *(getattr(scene, "search_queries", None) or []),
                        *(_sc_st.get("storyline_search_queries") or []),
                        *(getattr(scene, "storyline_search_queries", None) or []),
                    ])
                )
                if _st_qs:
                    res["search_queries"] = _st_qs[:4]
                    if not res.get("storyline_search_queries"):
                        res["storyline_search_queries"] = _st_qs[:4]
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
        prewarm_sem = asyncio.Semaphore(10)
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
                pw_dl = time.monotonic() + 46.0
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
                        timeout=48.0,
                    )
                except Exception as e_pw:
                    pw_cancel.set()
                    logger.debug(f"[ChapterPool Prewarm] Chapter lead scene {lead_scene.scene_id} skip: {e_pw}")

        # Tiền tải trọn vẹn tối đa 10 Chapter Masters (5 Bilibili 16:9 + 5 YouTube 16:9) trong 1 đợt song song duy nhất (<= 48s)
        all_lead_scenes = [s for s in script.scenes if ((int(s.scene_id) - 1) % 3) == 0]
        chapter_lead_scenes = all_lead_scenes[:10]
        if chapter_lead_scenes:
            logger.info(f"🎬 [LongForm Phase-A] Đang tiền tải {len(chapter_lead_scenes)} Chapter Masters 16:9 (Bilibili + YouTube song song <= 48s)...")
            await asyncio.gather(*[_prewarm_one_chapter(ls) for ls in chapter_lead_scenes])

    tasks = [_guarded_fetch(i, s) for i, s in enumerate(script.scenes)]
    scenes_media = list(await asyncio.gather(*tasks))

    scenes_media = await run_post_gather_media_rescues(
        state=state,
        script=script,
        scenes_media=scenes_media,
        work_dir=work_dir,
        media_dir=media_dir,
        job_id=job_id,
        channel_id_val=channel_id_val,
        primary_entity=primary_entity,
        current_niche=current_niche,
        is_long_form=is_long_form,
        is_scifi_val=is_scifi_val,
        media_agent=media_agent,
        image_agent=image_agent,
        rag=rag,
        strategy_map=strategy_map,
        used_pool_urls=used_pool_urls,
        longform_used_bvids=longform_used_bvids,
        longform_bvid_lock=longform_bvid_lock,
    )
    return {
        "scenes_media": scenes_media,
        "current_step": "MEDIA_READY",
        "progress_percent": 65,
        "progress_message": f"Đã thu thập trọn bộ media theo chiến lược đạo diễn ({len(scenes_media)} cảnh)",
    }
