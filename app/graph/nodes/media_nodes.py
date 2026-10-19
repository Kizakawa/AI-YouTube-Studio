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
from pathlib import Path
from typing import Dict, Any, List, Optional

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
    primary_entity = state.get("primary_entity")
    channel_style = state.get("channel_style", "cinematic")
    scenes = script_data.get("scenes", [])

    from app.graph.tools.media_tools import (
        plan_media_tools_with_llm_sync,
        audit_and_decide_search_scopes_with_llm_sync,
    )

    existing_plan = state.get("visual_plan")
    if existing_plan and existing_plan.get("scenes_strategy"):
        strat_list = existing_plan.get("scenes_strategy", [])
        if not existing_plan.get("scope_audited"):
            strat_list = await asyncio.to_thread(
                audit_and_decide_search_scopes_with_llm_sync,
                topic=topic,
                scenes=scenes,
                proposed_decisions=strat_list,
                primary_entity=primary_entity,
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
    )
    audited_decisions = await asyncio.to_thread(
        audit_and_decide_search_scopes_with_llm_sync,
        topic=topic,
        scenes=scenes,
        proposed_decisions=tool_decisions,
        primary_entity=primary_entity,
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


async def node_fetch_media(state: VideoWorkflowState) -> Dict[str, Any]:
    """
    Node thực thi SONG SONG toàn bộ các lệnh `@tool` do LLM Đạo diễn chỉ định cho từng cảnh:
      1. `clone_youtube_short_peak_clip` : Clone YouTube Shorts viral & cắt đúng đoạn Peak (Most Replayed / Action Peak) cho Hook & 2-3 cảnh cao trào.
      2. `search_google_cinematic_image` : Tìm ảnh Top Search Google Images sắc nét theo từ khóa đã được LLM cụ thể hóa.
    Tất cả các phân cảnh được chạy đồng loạt qua `asyncio.gather` (Semaphore 8) để đạt tốc độ tối đa.
    """
    script_data = state.get("script_data", {})
    primary_entity = state.get("primary_entity")
    entity_type = state.get("entity_type", "person")
    entity_media_pool = state.get("entity_media_pool", [])
    work_dir = Path(state.get("work_dir", "storage/cache"))
    media_dir = work_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    current_niche = state.get("niche") or state.get("channel_style") or state.get("channel_niche") or "general"
    is_scifi_val = (current_niche == "scifi") or any(k in state.get("topic", "").lower() for k in ("scifi", "sci-fi", "alien", "cyborg", "viễn tưởng"))

    script = ScriptContract(**script_data)
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

    strategy_map = {
        int(s.get("scene_id") or 0): s
        for s in visual_plan.get("scenes_strategy", [])
        if isinstance(s, dict) and s.get("scene_id")
    }

    scenes_media = []
    from app.services.media.media_history import get_recent_media_urls, record_used_media
    used_pool_urls = get_recent_media_urls(limit=150)
    job_id = state.get("job_id", 0)

    # Mở rộng Semaphore lên 8 để toàn bộ 8-10 phân cảnh của Shorts chạy SONG SONG 100% cùng lúc
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

    is_hybrid_tool_mode = (visual_plan.get("topic_classification") == "HYBRID_TOOL_CALLING")
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

    async def _fetch_one_scene(idx: int, scene) -> Dict[str, Any]:
        # ── ƯU TIÊN TUYỆT ĐỐI CHO NODE HOOK VIDEO: Nếu Cảnh này đã được cắt sẵn ──
        pre_hook = pre_hook_map.get(scene.scene_id) or pre_hook_map.get(str(scene.scene_id))
        if isinstance(pre_hook, dict) and pre_hook.get("media_path") and Path(pre_hook["media_path"]).exists():
            return {
                "scene_id": scene.scene_id,
                "media_path": pre_hook["media_path"],
                "media_type": pre_hook.get("media_type", "hook_ready_video"),
                "pose_images": None,
                "vision_curated": True,
                "visual_keyword": scene.visual_keyword,
                "visual_prompt": getattr(scene, "visual_prompt", None),
                "narration": scene.narration,
                "camera_motion": getattr(scene, "camera_motion", None) or "hyper_zoom",
                "sfx_cue": getattr(scene, "sfx_cue", None) or ("whoosh" if idx == 0 else "hit"),
                "highlight_words": getattr(scene, "highlight_words", None),
                "overlay_badge": getattr(scene, "overlay_badge", None),
                "visual_strategy": pre_hook.get("visual_strategy", "HOOK_PEAK_SHORT_VIDEO"),
                "production_method": pre_hook.get("production_method", "CLIMAX_REAL_VIDEO_FAIR_USE_3CUT"),
                "tool": "hook_video_node",
                "strategy_rationale": pre_hook.get("strategy_rationale", "Peak segment extracted from online Short"),
            }

        media_path = ""
        media_type = "image"
        vision_curated = False
        hook_alternate_urls: List[str] = []
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
            pre_m = getattr(scene, "media_path", None)
            if pre_m and Path(pre_m).exists() and Path(pre_m).stat().st_size > 3000:
                media_path = str(Path(pre_m).resolve())
                media_type = "video" if media_path.lower().endswith((".mp4", ".mov", ".webm")) else "image"
                vision_curated = True
                prod_method = chosen_strategy = "CACHED_MEDIA"
                logger.info(f"Scene {scene.scene_id}: ⚡ Tái sử dụng Media có sẵn: {Path(media_path).name}")
            else:
                cached_files = sorted(
                    media_dir.glob(f"scene_{scene.scene_id:03d}_*"),
                    key=lambda p: p.stat().st_mtime if p.exists() else 0,
                    reverse=True,
                )
                for cf in cached_files:
                    if cf.is_file() and cf.stat().st_size > 3000 and cf.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".mp4"):
                        media_path = str(cf.resolve())
                        media_type = "video" if cf.suffix.lower() == ".mp4" else "image"
                        vision_curated = True
                        prod_method = chosen_strategy = "CACHED_MEDIA"
                        logger.info(f"Scene {scene.scene_id}: ⚡ Tái sử dụng Media đã tải trong cache: {cf.name}")
                        break

        # ══════════════════════════════════════════════════════════════════════
        # NHÁNH ƯU TIÊN MỚI: THỰC THI BỘ @TOOL CỦA LLM (`clone_youtube_short_peak_clip` / `search_google_cinematic_image`)
        # ══════════════════════════════════════════════════════════════════════
        if not media_path and tool_name in ("clone_youtube_short_peak_clip", "search_google_cinematic_image"):
            from app.graph.tools.media_tools import (
                execute_youtube_short_peak_clone_sync,
                execute_google_cinematic_image_sync,
            )
            camera_motion = sc_strat.get("camera_motion") or ("hyper_zoom" if idx == 0 else "zoom_in")
            sfx_cue = sc_strat.get("sfx_cue") or ("whoosh" if idx == 0 else "hit")
            chosen_strategy = "YOUTUBE_PEAK_SHORT" if tool_name == "clone_youtube_short_peak_clip" else "GOOGLE_TOP_IMAGE"
            prod_method = chosen_strategy
            tool = tool_name
            rationale = f"LLM Tool Call: {tool_name}"
            search_query = sc_strat.get("youtube_search_query") or sc_strat.get("smart_image_query") or kw
            ai_prompt = vp or search_query
            v_analysis = {}
            tier0_searched = True
            channel_visual_mode = state.get("visual_mode") or "auto"
            scene_media_pref = getattr(scene, 'media_type', None)

            subj_ent = sc_strat.get("subject_entity") or primary_entity or state.get("topic", "")
            sc_scope = sc_strat.get("search_scope") or "SCENE_SPECIFIC"
            overall_q = sc_strat.get("overall_script_query") or state.get("topic", "")
            if tool_name == "clone_youtube_short_peak_clip":
                yt_q = sc_strat.get("youtube_search_query") or kw or state.get("topic", "")
                fb_q = sc_strat.get("fallback_broad_query") or overall_q or subj_ent
                clip_role = sc_strat.get("clip_role") or ("HOOK" if scene.scene_id == 1 else "PEAK")
                out_vid = str(
                    work_dir / "hook_motion_001.mp4"
                    if scene.scene_id == 1
                    else media_dir / f"scene_{scene.scene_id:03d}_yt_peak.mp4"
                )
                yt_res = await asyncio.to_thread(
                    execute_youtube_short_peak_clone_sync,
                    search_query=yt_q,
                    output_path=out_vid,
                    target_duration_sec=dur + 0.25,
                    clip_role=clip_role,
                    used_urls=used_pool_urls,
                    fallback_query=fb_q,
                    subject_entity=subj_ent,
                    narration=scene.narration,
                    topic=state.get("topic", ""),
                    search_scope=sc_scope,
                    overall_script_query=overall_q,
                )
                if yt_res and yt_res.get("media_path"):
                    media_path = yt_res["media_path"]
                    media_type = yt_res.get("media_type", "video")
                    vision_curated = True
                    prod_method = "CLIMAX_REAL_VIDEO_FAIR_USE_3CUT" if scene.scene_id == 1 else "CLIMAX_PEAK_SHORT_SPLICE"
                    rationale = f"YouTube Shorts Peak [{yt_res.get('search_scope', sc_scope)}] ({yt_res.get('window')}): {yt_res.get('title', '')[:45]}"
                else:
                    # Nếu chủ đề khó/không tìm được Shorts -> tự động gọi Tool 2 (search_google_cinematic_image) ngay lập tức!
                    logger.info(f"Scene {scene.scene_id}: ⚡ YouTube Shorts Peak không có clip phù hợp cho '{yt_q}' -> Chuyển sang tìm ảnh Top Google Images!")
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
                    )
                    if img_res and img_res.get("media_path"):
                        media_path = img_res["media_path"]
                        media_type = "image"
                        vision_curated = True
                        hook_alternate_urls = list(img_res.get("alternate_urls") or [])
                        prod_method = "GOOGLE_TOP_IMAGE_FALLBACK"
            else:
                img_q = sc_strat.get("smart_image_query") or kw or state.get("topic", "")
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
                )
                if img_res and img_res.get("media_path"):
                    media_path = img_res["media_path"]
                    media_type = "image"
                    vision_curated = True
                    hook_alternate_urls = list(img_res.get("alternate_urls") or [])
                    rationale = f"Google Top Image [{img_res.get('search_scope', sc_scope)}] ({img_res.get('curation_score')}): {img_res.get('title', '')[:45]}"
        else:
            # ── Khởi tạo biến cho cảnh dùng cache hoặc fallback tương thích ngược ──
            surround_text = " ".join([s.narration for s in script.scenes[max(0, idx - 1):min(len(script.scenes), idx + 2)]])
            v_analysis = batch_analyses.get(scene.scene_id) or {}
            if not media_path:
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
            and not tier0_searched
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
                    if best_vid and best_vid.get("url") and best_vid["url"] not in used_pool_urls:
                        cand_url = best_vid["url"]
                        vid_out = str(media_dir / f"scene_{scene.scene_id:03d}_pexels_fb.mp4")
                        await asyncio.to_thread(media_agent.download_file, cand_url, vid_out)
                        if Path(vid_out).exists() and Path(vid_out).stat().st_size > 10000:
                            used_pool_urls.add(cand_url)
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
                    skip_stock_lookup=prefer_generation,
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
                    if best_vid and best_vid.get("url") and best_vid["url"] not in used_pool_urls:
                        cand_url = best_vid["url"]
                        vid_out = str(media_dir / f"scene_{scene.scene_id:03d}_pexels_fb.mp4")
                        await asyncio.to_thread(media_agent.download_file, cand_url, vid_out)
                        if Path(vid_out).exists() and Path(vid_out).stat().st_size > 10000:
                            used_pool_urls.add(cand_url)
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

                if best_vid and best_vid.get("url") and best_vid["url"] not in used_pool_urls:
                    cand_url = best_vid["url"]
                    vid_out = str(media_dir / f"scene_{scene.scene_id:03d}_pexels.mp4")
                    await asyncio.to_thread(media_agent.download_file, cand_url, vid_out)
                    if Path(vid_out).exists() and Path(vid_out).stat().st_size > 10000:
                        used_pool_urls.add(cand_url)
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
        if not media_path:
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
                        media_path = output
                        media_type = c_type
                        if c_type == "image":
                            hook_alternate_urls = list(candidate_media.get("alternate_urls") or [])
            except Exception:
                pass

        # Fallback 2: Nếu là chế độ VIDEO (STOCK_FOOTAGE hoặc TEXT_TO_VIDEO) -> Ưu tiên tạo Video AI chuyển động 3D thay vì hạ cấp xuống ảnh tĩnh
        is_video_mode = prod_method in ("STOCK_FOOTAGE", "TEXT_TO_VIDEO") or chosen_strategy in ("STOCK_VIDEO", "AI_VIDEO")
        if not media_path and is_video_mode:
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

        if not media_path and is_psych_or_ai:
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

        # Fallback 4: Ảnh tĩnh HD đa nguồn (Pexels + Pixabay + Openverse) do AI Vision tuyển chọn (nếu chưa tìm ở Tier 0)
        if not media_path and not tier0_searched:
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

        # Fallback 5: Sinh ảnh AI cứu hộ tổng quát nếu vẫn chưa có
        if not media_path:
            try:
                ai_img_path = str(media_dir / f"scene_{scene.scene_id:03d}_fb_ai_gen.jpg")
                clean_kw = clean_scene_search_query(kw, max_words=4)
                prompt_fb = vp if (vp and len(vp.strip()) > 10) else (clean_kw or "cinematic scene")
                res_img = await asyncio.to_thread(
                    image_agent.generate_scene_image,
                    prompt_fb,
                    ai_img_path,
                    "9:16",
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
        pose_images: List[str] = []
        if scene.scene_id == 1 and media_path and media_type == "image":
            pose_images = [media_path]
            valid_alts = [u for u in hook_alternate_urls if u and u not in used_pool_urls][:3]

            async def _dl_hook_alt(idx_alt: int, u_alt: str) -> Optional[str]:
                alt_out = str(media_dir / f"scene_001_hook_cut{idx_alt + 2}.jpg")
                try:
                    await asyncio.to_thread(media_agent.download_file, u_alt, alt_out)
                    if Path(alt_out).exists() and Path(alt_out).stat().st_size > 3000:
                        used_pool_urls.add(u_alt)
                        return alt_out
                    Path(alt_out).unlink(missing_ok=True)
                except Exception:
                    Path(alt_out).unlink(missing_ok=True)
                return None

            if valid_alts:
                alt_results = await asyncio.gather(*[_dl_hook_alt(i, u) for i, u in enumerate(valid_alts)])
                pose_images.extend([p for p in alt_results if p])
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
                        width=1080,
                        height=1920,
                        fps=30,
                        min_cuts=3,
                        is_scifi=is_scifi_val,
                        niche=current_niche,
                    )
                    if Path(hook_out_p).exists() and Path(hook_out_p).stat().st_size > 10_000:
                        media_path = hook_out_p
                        media_type = "hook_ready_video"
                        prod_method = f"CLIMAX_IMAGES_LIVE_FX_{str(current_niche).upper()}_3CUT"
                except Exception as e_hk_img:
                    logger.debug(f"Scene 1: Lỗi dựng hook_motion_001 từ ảnh truyền thống: {e_hk_img}")

        return {
            "scene_id": scene.scene_id,
            "media_path": media_path,
            "media_type": media_type,
            "pose_images": pose_images if pose_images else None,
            "vision_curated": vision_curated,
            "visual_keyword": scene.visual_keyword,
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
        }

    async def _guarded_fetch(idx: int, scene) -> Dict[str, Any]:
        async with sem:
            res = await _fetch_one_scene(idx, scene)
            if not res or not isinstance(res, dict):
                logger.warning(f"Scene {scene.scene_id}: _fetch_one_scene không trả về dict hợp lệ, kích hoạt fallback đồ họa an toàn.")
                out_g = str(media_dir / f"scene_{scene.scene_id:03d}_graphic.png")
                from app.utils.ffmpeg_helper import create_placeholder_image
                create_placeholder_image(out_g, text=scene.visual_keyword or f"Scene {scene.scene_id}", scene_id=scene.scene_id)
                res = {
                    "scene_id": scene.scene_id,
                    "media_path": out_g,
                    "media_type": "image",
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

    tasks = [_guarded_fetch(i, s) for i, s in enumerate(script.scenes)]
    scenes_media = list(await asyncio.gather(*tasks))

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

    logger.info(
        f"🖼️ [VisualCritic] Điểm kỹ thuật media: {eval_report.overall_score}/10 | "
        f"Passed: {eval_report.passed} | Lỗi: {len(eval_report.failed_scene_ids)} cảnh"
    )

    return {
        "media_eval": eval_report.model_dump(),
        "current_step": "MEDIA_EVALUATED",
        "progress_percent": 70,
        "progress_message": f"Thẩm định kỹ thuật hình ảnh: {eval_report.overall_score}/10 ({'Đạt chuẩn' if eval_report.passed else f'Cần sửa {len(eval_report.failed_scene_ids)} cảnh'})",
    }


async def node_refine_media(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node tự động sửa chữa SONG SONG các cảnh bị đánh giá FAIL kỹ thuật (thay thế media mới chuẩn xác)."""
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
    logger.info(f"🔄 [LangGraph] Tự động sửa chữa SONG SONG media kỹ thuật cho các cảnh: {sorted(failed_ids)} (Lần {retry_count})")

    from app.services.media.media_history import get_recent_media_urls
    from app.services.media.image_quality import clean_search_query_from_prompt
    used_refine_urls = get_recent_media_urls(limit=150)
    for _m in scenes_media:
        if isinstance(_m, dict) and _m.get("source_url"):
            used_refine_urls.add(_m["source_url"])

    async def _refine_single_item(item: Dict[str, Any]) -> None:
        if item.get("scene_id") not in failed_ids:
            return
        if item.get("media_type") == "hook_ready_video" or item.get("production_method") in ("CLIMAX_REAL_VIDEO_FAIR_USE_3CUT", "CLIMAX_PEAK_SHORT_SPLICE"):
            return
        scene_id = item["scene_id"]
        kw = item.get("visual_keyword", "")
        narr = item.get("narration", "")
        vp = item.get("visual_prompt", None)
        clean_kw_q = clean_search_query_from_prompt(kw or vp or "", narration=narr, topic=state.get("topic", ""), max_words=6) or kw
        is_video = item.get("media_type") == "video" or item.get("production_method") in ("STOCK_FOOTAGE", "TEXT_TO_VIDEO", "YOUTUBE_PEAK_SHORT")
        replaced = False

        if is_video:
            try:
                best_v = await asyncio.to_thread(
                    media_agent.search_best_video_clip,
                    clean_kw_q,
                    used_refine_urls,
                    None,
                    narr,
                    state.get("topic", ""),
                )
                if best_v and best_v.get("url") and best_v["url"] not in used_refine_urls:
                    cand_url = best_v["url"]
                    out_v = str(media_dir / f"scene_{scene_id:03d}_refined_video.mp4")
                    await asyncio.to_thread(media_agent.download_file, cand_url, out_v)
                    if Path(out_v).exists() and Path(out_v).stat().st_size > 10000:
                        used_refine_urls.add(cand_url)
                        item["media_path"] = out_v
                        item["media_type"] = "video"
                        replaced = True
                        logger.info(f"Scene {scene_id}: ✅ Sửa lỗi kỹ thuật bằng Video Clip Pexels mới: '{clean_kw_q}'")
            except Exception as ex_v:
                logger.debug(f"Scene {scene_id}: Video refine error: {ex_v}")

            if not replaced:
                try:
                    ai_v_path = str(media_dir / f"scene_{scene_id:03d}_refined_i2v.mp4")
                    res_vid = await ai_video_agent.generate_image_to_video(
                        prompt=vp or clean_kw_q,
                        output_path=ai_v_path,
                        duration_seconds=_scene_audio_duration(work_dir, scene_id),
                        scene_id=scene_id,
                        visual_prompt=vp,
                        used_urls=used_refine_urls,
                        narration=narr,
                        niche=current_niche,
                        is_scifi=is_scifi_val,
                    )
                    if res_vid and Path(res_vid).exists() and Path(res_vid).stat().st_size > 5000:
                        item["media_path"] = res_vid
                        item["media_type"] = "video"
                        replaced = True
                        logger.info(f"Scene {scene_id}: ✅ Sửa lỗi kỹ thuật bằng Image-to-Video chuyển động 3D")
                except Exception as ex_ai_v:
                    logger.debug(f"Scene {scene_id}: AI video refine error: {ex_ai_v}")

        if not replaced:
            ai_img_path = str(media_dir / f"scene_{scene_id:03d}_refined.jpg")
            fallback_kw = f"Dramatic cinematic photorealistic shot of {primary_entity or clean_kw_q}, {clean_kw_q}, 8k sharp focus"
            res_img = await asyncio.to_thread(
                lambda: image_agent.generate_scene_image(
                    prompt=fallback_kw,
                    output_path=ai_img_path,
                    aspect_ratio="9:16",
                    niche="cinematic",
                    scene_id=scene_id,
                    visual_prompt=vp,
                    used_urls=used_refine_urls,
                    topic=state.get("topic", ""),
                )
            )
            if res_img and Path(res_img).exists() and Path(res_img).stat().st_size > 2000:
                item["media_path"] = res_img
                item["media_type"] = "image"
                logger.info(f"Scene {scene_id}: ✅ Đã thay thế bằng ảnh sắc nét mới")

    await asyncio.gather(*[_refine_single_item(item) for item in scenes_media if isinstance(item, dict)])

    return {
        "scenes_media": scenes_media,
        "media_retry_count": retry_count,
        "current_step": "MEDIA_REFINED",
        "progress_message": f"Đã tự động sửa lại song song {len(failed_ids)} cảnh media kỹ thuật chưa đạt",
    }
