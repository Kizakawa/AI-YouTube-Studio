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
from typing import Dict, Any, List, Optional, Tuple

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
    primary_entity = state.get("primary_entity") or script_data.get("primary_entity")
    channel_style = state.get("channel_style", "cinematic")
    scenes = script_data.get("scenes", [])

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
    if tool_decisions and all(isinstance(d, dict) and d.get("scope_audited") for d in tool_decisions):
        audited_decisions = tool_decisions
    else:
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
    primary_entity = state.get("primary_entity") or script_data.get("primary_entity")
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

    from app.services.video_memory_rag import get_video_memory_rag
    rag = get_video_memory_rag()
    job_id = int(state.get("job_id") or 0)
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
        # ── ƯU TIÊN TUYỆT ĐỐI CHO NODE HOOK VIDEO: Nếu Cảnh này đã được cắt sẵn (bao gồm cả Long Peak chảy liền mạch) ──
        pre_hook = pre_hook_map.get(scene.scene_id) or pre_hook_map.get(str(scene.scene_id))
        if isinstance(pre_hook, dict) and pre_hook.get("media_path") and Path(pre_hook["media_path"]).exists():
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
                    key=lambda p: (0 if "_cut" not in p.name else 1, p.name),
                )
                valid_cached_imgs: List[str] = []
                for cf in cached_files:
                    if cf.is_file() and cf.stat().st_size > 3000 and cf.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".mp4"):
                        if cf.suffix.lower() == ".mp4" and not media_path:
                            media_path = str(cf.resolve())
                            media_type = "video"
                            vision_curated = True
                            prod_method = chosen_strategy = "CACHED_MEDIA"
                            logger.info(f"Scene {scene.scene_id}: ⚡ Tái sử dụng Video đã tải trong cache: {cf.name}")
                            break
                        elif cf.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                            valid_cached_imgs.append(str(cf.resolve()))
                if not media_path and valid_cached_imgs:
                    media_path = valid_cached_imgs[0]
                    media_type = "image"
                    pose_images = valid_cached_imgs
                    vision_curated = True
                    prod_method = chosen_strategy = "CACHED_MEDIA"
                    logger.info(
                        f"Scene {scene.scene_id}: ⚡ Tái sử dụng {len(valid_cached_imgs)} ảnh đã tải trong cache: {Path(media_path).name}"
                    )

        # ══════════════════════════════════════════════════════════════════════
        # NHÁNH ƯU TIÊN MỚI: THỰC THI BỘ 4 @TOOL CHUYÊN BIỆT CỦA LLM ĐẠO DIỄN
        # (`summon_epic_monster_cinematic_media` | `fetch_clean_cinematic_portrait_or_broll` | `clone_youtube_short_peak_clip` | `search_google_cinematic_image`)
        # ══════════════════════════════════════════════════════════════════════
        if not media_path and tool_name in (
            "clone_youtube_short_peak_clip",
            "search_pexels_pixabay_stock_video",
            "search_google_cinematic_image",
            "summon_epic_monster_cinematic_media",
            "fetch_clean_cinematic_portrait_or_broll",
        ):
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
                or sc_strat.get("youtube_search_query")
                or sc_strat.get("smart_image_query")
                or kw
            )
            ai_prompt = vp or search_query
            v_analysis = {}
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
                m_mode = sc_strat.get("monster_media_mode") or ("3D_CGI_VIDEO" if scene.scene_id == 1 else "CONCEPT_ART_8K")
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
                h_query = sc_strat.get("human_action_query") or kw or "dramatic portrait dark lighting"
                m_pref = sc_strat.get("media_preference") or ("VIDEO_BROLL" if scene.scene_id in (1, 2, 3, 5, 6) else "CLEAN_PORTRAIT_PHOTO")
                out_clean = str(
                    media_dir / f"scene_{scene.scene_id:03d}_clean_broll.mp4"
                    if m_pref.upper() == "VIDEO_BROLL"
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
                fb_q = sc_strat.get("fallback_broad_query") or sc_strat.get("secondary_stock_query") or overall_q or subj_ent
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
                    # Lưu ý: execute_youtube_short_peak_clone_sync đã bao gồm cả Tầng 1 (YouTube Peak) và Tầng 2 (Pexels/Pixabay có kiểm duyệt Gemini Vision).
                    # Tuyệt đối không tải lại Stock Video mù (không qua Gemini Vision) để tránh lọt clip mèo nhà / thú trong sở thú!
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
                    if best_vid and best_vid.get("url") and best_vid["url"] not in used_pool_urls:
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

                if best_vid and best_vid.get("url") and best_vid["url"] not in used_pool_urls:
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

        # Fallback 4: Ảnh tĩnh HD đa nguồn (Pexels + Pixabay + Openverse) do AI Vision tuyển chọn
        if not media_path:
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
                        width=1080,
                        height=1920,
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
            "source_url": source_url_used,
            "full_peak_path": full_peak_path,
            "full_peak_duration_sec": full_peak_duration_sec,
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

    tasks = [_guarded_fetch(i, s) for i, s in enumerate(script.scenes)]
    scenes_media = list(await asyncio.gather(*tasks))

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

    # ── Bước A: MỞ RỘNG ĐOẠN PEAK DÀI (`full_peak_path` 6s-15s) CHẢY LIỀN MẠCH SANG CẢNH KẾ TIẾP (GIỮ 1:1 THỨ TỰ KỊCH BẢN) ──
    try:
        from app.utils.ffmpeg_helper import get_duration_ms, trim_video
        n_total_slots = len(scenes_media)
        for idx_v in range(n_total_slots):
            sc_v = scenes_media[idx_v]
            if not isinstance(sc_v, dict):
                continue
            full_p = sc_v.get("full_peak_path")
            if not full_p or not Path(full_p).exists():
                continue
            full_dur = float(sc_v.get("full_peak_duration_sec") or 0.0)
            if full_dur <= 0.5:
                full_dur = get_duration_ms(full_p) / 1000.0
            slot_v_dur = _get_slot_dur(idx_v)
            offset_cursor = slot_v_dur
            rem_sec = full_dur - offset_cursor
            if rem_sec < 2.0:
                continue

            for next_idx in range(idx_v + 1, min(idx_v + 3, n_total_slots)):
                if rem_sec < 2.0:
                    break
                next_sc = scenes_media[next_idx]
                if not isinstance(next_sc, dict):
                    break
                next_mtype = str(next_sc.get("media_type") or "image").lower()
                if next_mtype in ("video", "hook_ready_video", "ai_video", "i2v", "t2v"):
                    break
                next_sid = int(next_sc.get("scene_id") or (next_idx + 1))
                next_dur = _get_slot_dur(next_idx)
                clip_target_dur = next_dur + (0.16 if next_idx < n_total_slots - 1 else 0.35)
                cont_clip_path = str(media_dir / f"scene_{next_sid:03d}_long_peak_flow.mp4")
                try:
                    trim_video(
                        input_path=full_p,
                        duration_seconds=clip_target_dur,
                        output_path=cont_clip_path,
                        width=1080,
                        height=1920,
                        fps=30,
                        is_scifi=is_scifi,
                        niche=niche,
                        apply_grading=False,
                        is_hook_scene=False,
                        fair_use_transform=False,
                        start_offset_sec=offset_cursor,
                        preserve_continuous_flow=True,
                    )
                    if Path(cont_clip_path).exists() and Path(cont_clip_path).stat().st_size > 10_000:
                        ok_flow, _ = rag.verify_and_commit_in_job_clip(
                            job_id=job_id,
                            scene_id=next_sid,
                            media_path=cont_clip_path,
                            vid_id=f"flow_from_s{idx_v + 1}",
                            url=full_p,
                            title=f"Long Peak Flow from Scene {idx_v + 1}",
                            is_continuation_slice=True,
                        )
                        if ok_flow:
                            next_sc["media_path"] = cont_clip_path
                            next_sc["media_type"] = "video"
                            next_sc["pose_images"] = []
                            next_sc["production_method"] = "LONG_PEAK_CONTINUOUS_FLOW"
                            next_sc["visual_strategy"] = "LONG_PEAK_CONTINUOUS_FLOW"
                            offset_cursor += next_dur
                            rem_sec = full_dur - offset_cursor
                            logger.info(
                                f"🌊 [VideoFlow Assembler] Đã nối liền mạch đoạn Peak dài từ Cảnh {idx_v + 1} sang Cảnh {next_sid} "
                                f"(@{offset_cursor - next_dur:.1f}s -> @{offset_cursor:.1f}s, còn dư {rem_sec:.1f}s)"
                            )
                        else:
                            Path(cont_clip_path).unlink(missing_ok=True)
                            break
                except Exception as _flow_err:
                    logger.debug(f"[VideoFlow Assembler] Long peak slice Scene {next_sid} skip: {_flow_err}")
                    break
    except Exception as _step_a_err:
        logger.debug(f"[VideoFlow Assembler] Step A warning: {_step_a_err}")

    # ── Bước B: CỔNG BẢO ĐẢM TỶ LỆ VIDEO CHUYỂN ĐỘNG THẬT >= 50% (VÀ 100% CẢNH 1 + CẢNH 2 LÀ VIDEO) ──
    try:
        def _is_real_video_slot(sc_dict: Any) -> bool:
            if not isinstance(sc_dict, dict):
                return False
            mt = str(sc_dict.get("media_type") or "image").lower()
            mp = str(sc_dict.get("media_path") or "").lower()
            return mt in ("video", "hook_ready_video", "ai_video", "i2v", "t2v") or mp.endswith((".mp4", ".mov", ".webm"))

        n_slots = len(scenes_media)
        min_video_slots = max(3, (n_slots + 1) // 2) if n_slots >= 4 else n_slots
        current_video_slots = sum(1 for s_it in scenes_media if _is_real_video_slot(s_it))

        # Luôn ưu tiên giải cứu Cảnh 2 (idx=1) trước nếu Cảnh 2 vẫn đang là ảnh tĩnh, sau đó đến các cảnh giữa thân bài
        rescue_candidates_idx: List[int] = []
        if n_slots >= 2 and not _is_real_video_slot(scenes_media[1]):
            rescue_candidates_idx.append(1)
        for idx_mid in range(2, n_slots):
            if not _is_real_video_slot(scenes_media[idx_mid]):
                rescue_candidates_idx.append(idx_mid)

        needed_rescues = max(
            1 if (n_slots >= 2 and not _is_real_video_slot(scenes_media[1])) else 0,
            min_video_slots - current_video_slots,
        )
        if needed_rescues > 0 and rescue_candidates_idx:
            logger.info(
                f"🎬 [Real-Motion Floor Rescue] Hiện có {current_video_slots}/{n_slots} cảnh video (mục tiêu tối thiểu {min_video_slots} cảnh video & Cảnh 2 bắt buộc là video). "
                f"Đang nâng cấp {min(needed_rescues, len(rescue_candidates_idx))} cảnh ảnh tĩnh sang Video chuyển động thật..."
            )
            for r_idx in rescue_candidates_idx[:needed_rescues]:
                r_sc = scenes_media[r_idx]
                r_sid = int(r_sc.get("scene_id") or (r_idx + 1))
                r_dur = _get_slot_dur(r_idx)
                r_kw = clean_scene_search_query(
                    r_sc.get("visual_keyword") or primary_entity or state.get("topic", ""),
                    max_words=4,
                )
                r_out_mp4 = str(media_dir / f"scene_{r_sid:03d}_motion_rescue.mp4")
                r_subj = clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=2) or r_kw
                rescued_ok = False
                try:
                    yt_rescue = await asyncio.to_thread(
                        execute_youtube_short_peak_clone_sync,
                        search_query=r_kw,
                        subject_entity=r_subj,
                        narration=str(r_sc.get("narration") or state.get("topic", "")),
                        duration_sec=r_dur,
                        output_path=r_out_mp4,
                        is_scifi=is_scifi,
                        niche=niche,
                        used_urls=used_pool_urls,
                        job_id=job_id,
                        scene_id=r_sid,
                        search_scope="OVERALL_SCRIPT",
                        overall_script_query=clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=4),
                    )
                    if yt_rescue.get("ok") and yt_rescue.get("media_path") and Path(yt_rescue["media_path"]).exists():
                        r_sc["media_path"] = yt_rescue["media_path"]
                        r_sc["media_type"] = "video"
                        r_sc["pose_images"] = []
                        r_sc["production_method"] = f"MOTION_FLOOR_RESCUE_{yt_rescue.get('method', 'YT_PEAK')}"
                        r_sc["visual_strategy"] = "YOUTUBE_SHORT_PEAK_CLONE"
                        rescued_ok = True
                        logger.info(f"  ✅ [Real-Motion Floor Rescue] Scene {r_sid}: Đã nâng cấp sang Video Peak ({Path(yt_rescue['media_path']).name})")
                except Exception as _re_yt:
                    logger.debug(f"[Real-Motion Floor Rescue] Scene {r_sid} YT peak rescue skip: {_re_yt}")

                if not rescued_ok:
                    try:
                        doc_rescue = await asyncio.to_thread(
                            execute_real_documentary_action_video_sync,
                            action_query=r_kw,
                            subject_entity=r_subj,
                            narration=str(r_sc.get("narration") or state.get("topic", "")),
                            duration_sec=r_dur,
                            output_path=r_out_mp4,
                            used_urls=used_pool_urls,
                            job_id=job_id,
                            scene_id=r_sid,
                            search_scope="OVERALL_SCRIPT",
                            overall_script_query=clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=4),
                        )
                        if doc_rescue.get("ok") and doc_rescue.get("media_path") and Path(doc_rescue["media_path"]).exists():
                            r_sc["media_path"] = doc_rescue["media_path"]
                            r_sc["media_type"] = "video"
                            r_sc["pose_images"] = []
                            r_sc["production_method"] = f"MOTION_FLOOR_RESCUE_{doc_rescue.get('method', 'STOCK_VIDEO')}"
                            r_sc["visual_strategy"] = "REAL_DOCUMENTARY_VIDEO"
                            rescued_ok = True
                            logger.info(f"  ✅ [Real-Motion Floor Rescue] Scene {r_sid}: Đã nâng cấp sang Stock Video ({Path(doc_rescue['media_path']).name})")
                    except Exception as _re_doc:
                        logger.debug(f"[Real-Motion Floor Rescue] Scene {r_sid} Stock rescue skip: {_re_doc}")
    except Exception as _step_b_err:
        logger.debug(f"[Real-Motion Floor Rescue] Step B warning: {_step_b_err}")

    # ══════════════════════════════════════════════════════════════════════════
    # Bước C: BỘ ĐIỀU PHỐI MẬT ĐỘ ẢNH TĨNH MƯỢT MÀ (TỐI ĐA 1-2 ẢNH / CẢNH ẢNH, MỖI ẢNH >= 2.0 GIÂY)
    # - Chấm dứt hoàn toàn hiện tượng nhồi 3 ảnh vào 1 cảnh 3.5s gây nháy hình (strobe) làm rớt người xem!
    # - Cảnh < 4.0s chỉ dùng 1 ảnh Ken Burns mượt mà; cảnh >= 4.0s dùng tối đa 2 ảnh (mỗi ảnh 2.0s - 2.5s).
    # ══════════════════════════════════════════════════════════════════════════
    try:
        body_img_indices = [
            idx_b for idx_b, m_b in enumerate(scenes_media)
            if isinstance(m_b, dict) and str(m_b.get("media_type") or "image").lower() == "image"
        ]

        if body_img_indices:
            scene_quotas: Dict[int, int] = {
                i_slot: (2 if _get_slot_dur(i_slot) >= 4.0 else 1)
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
                quota = scene_quotas.get(slot_idx, 3)
                primary_p = sc_item.get("media_path")
                existing_poses = [
                    p for p in (sc_item.get("pose_images") or ([primary_p] if primary_p else []))
                    if p and Path(p).exists() and Path(p).stat().st_size > 2500
                ]
                if not existing_poses and primary_p and Path(primary_p).exists():
                    existing_poses = [primary_p]

                verified_poses: List[str] = []
                for ep in existing_poses:
                    if rag.verify_and_commit_in_job_image(job_id=job_id, scene_id=sid, image_path=ep, url=ep):
                        verified_poses.append(ep)
                if not verified_poses and existing_poses:
                    verified_poses = [existing_poses[0]]

                if len(verified_poses) >= quota:
                    sc_item["pose_images"] = verified_poses[:quota]
                    return

                alt_pool = [
                    u for u in (sc_item.get("alternate_urls") or [])
                    if u
                ]

                short_subj = clean_scene_search_query(primary_entity or state.get("topic", ""), max_words=2)
                raw_sc_kw = sc_item.get("visual_keyword") or sc_item.get("visual_prompt") or short_subj
                sc_kw = clean_scene_search_query(raw_sc_kw, max_words=4) or short_subj

                # Nếu kho alternate_urls của cảnh chưa đủ lấp đầy quota, tìm bổ sung theo đúng công cụ `@tool` mà LLM đã chọn
                if len(verified_poses) + len(alt_pool) < quota + 1:
                    facet = angle_facets[slot_idx % len(angle_facets)]
                    facet2 = angle_facets[(slot_idx + 3) % len(angle_facets)]
                    supp_q = clean_scene_search_query(f"{sc_kw} {facet}", max_words=6) or sc_kw
                    supp_q2 = clean_scene_search_query(f"{short_subj} {facet2}", max_words=5) or supp_q
                    sc_tool = str(sc_item.get("tool") or "")
                    if sc_tool == "fetch_clean_cinematic_portrait_or_broll":
                        try:
                            portrait_qs = ["dramatic portrait dark shadow", "intense eyes close up dark", "person silhouette night", "man thinking dark room"]
                            px_batches = await asyncio.gather(
                                *[
                                    asyncio.to_thread(media_agent.search_photos, pq_d, 5, "portrait", "dramatic portrait", "", used_pool_urls)
                                    for pq_d in portrait_qs
                                ],
                                return_exceptions=True,
                            )
                            for px_list in px_batches:
                                if isinstance(px_list, list):
                                    for p_it in px_list:
                                        u_px = p_it.get("url") or (p_it.get("src") or {}).get("large2x") or (p_it.get("src") or {}).get("portrait")
                                        if u_px and u_px not in alt_pool:
                                            alt_pool.append(u_px)
                        except Exception as _px_err:
                            logger.debug(f"[Visual Density Engine] Scene {sid} pexels portrait error: {_px_err}")
                    else:
                        try:
                            from app.services.media.web_image_fetcher import WebImageFetcher
                            wf = WebImageFetcher()
                            is_monster_tool = (sc_tool == "summon_epic_monster_cinematic_media")
                            monster_supp_q = (
                                f"colossal {short_subj} underwater submarine concept art 8k"
                                if is_monster_tool
                                else supp_q
                            )
                            ranked_web = await asyncio.to_thread(
                                wf.search_all,
                                monster_supp_q,
                                10,
                                short_subj,
                                [f"giant {short_subj} movie scene cgi"] if is_monster_tool else [supp_q2],
                                is_monster_tool,
                            )
                            for c_item in (ranked_web or []):
                                u_supp = c_item.get("url")
                                if u_supp and u_supp not in alt_pool:
                                    alt_pool.append(u_supp)
                        except Exception as _wf_err:
                            logger.debug(f"[Visual Density Engine] Scene {sid} web search error: {_wf_err}")

                needed_web_dl = max(0, quota - len(verified_poses))
                if needed_web_dl > 0 and alt_pool:
                    cand_batch = alt_pool[: max(needed_web_dl + 4, 6)]

                    async def _dl_density_cand(idx_c: int, cand_u: str) -> Optional[Tuple[str, str]]:
                        tmp_cut = str(media_dir / f"scene_{sid:03d}_density_cand{idx_c + 2}.jpg")
                        try:
                            await asyncio.to_thread(media_agent.download_file, cand_u, tmp_cut)
                            if Path(tmp_cut).exists() and Path(tmp_cut).stat().st_size > 3000:
                                return tmp_cut, cand_u
                        except Exception:
                            pass
                        Path(tmp_cut).unlink(missing_ok=True)
                        return None

                    dl_outcomes = await asyncio.gather(
                        *[_dl_density_cand(i_c, u_c) for i_c, u_c in enumerate(cand_batch)],
                        return_exceptions=True,
                    )
                    for out_pair in dl_outcomes:
                        if not isinstance(out_pair, tuple):
                            continue
                        tmp_cut, cand_u = out_pair
                        if len(verified_poses) < quota:
                            final_cut = str(media_dir / f"scene_{sid:03d}_cut{len(verified_poses) + 1}.jpg")
                            try:
                                Path(tmp_cut).replace(final_cut)
                            except Exception:
                                final_cut = tmp_cut
                            if rag.verify_and_commit_in_job_image(
                                job_id=job_id, scene_id=sid, image_path=final_cut, url=cand_u
                            ):
                                verified_poses.append(final_cut)
                                used_pool_urls.add(cand_u)
                                record_used_media(
                                    url=cand_u,
                                    query=sc_item.get("visual_keyword") or state.get("topic", ""),
                                    job_id=job_id,
                                    source="density_multi_image",
                                    media_path=final_cut,
                                )
                            else:
                                Path(final_cut).unlink(missing_ok=True)
                        else:
                            Path(tmp_cut).unlink(missing_ok=True)

                # Nếu sau khi lọc dHash từ web vẫn còn thiếu N ảnh để đạt quota -> Gọi ĐỒNG THỜI N API tạo ảnh AI 8K qua LangGraph Send+operator.add!
                missing_ai_shots = max(0, quota - len(verified_poses))
                if missing_ai_shots > 0:
                    from app.graph.parallel_search import run_langgraph_parallel_tasks

                    base_shot_idx = len(verified_poses)
                    ai_tasks = [
                        {
                            "shot_num": base_shot_idx + i_shot + 1,
                            "ai_angle": angle_facets[(slot_idx + base_shot_idx + i_shot + 1) % len(angle_facets)],
                            "ai_cut_p": str(media_dir / f"scene_{sid:03d}_cut{base_shot_idx + i_shot + 1}.jpg"),
                        }
                        for i_shot in range(missing_ai_shots)
                    ]

                    def _gen_single_ai_shot(task_obj: Dict[str, Any]) -> Optional[str]:
                        if str(sc_item.get("tool") or "") == "summon_epic_monster_cinematic_media":
                            try:
                                from app.services.media.monster_art_synthesizer import MonsterArtSynthesizer
                                synth_p = MonsterArtSynthesizer().generate_epic_monster_image(
                                    creature_name=short_subj or "colossal leviathan",
                                    scale_anchor_object="nuclear submarine",
                                    action_description=f"{sc_kw}, {task_obj['ai_angle']}",
                                    output_path=task_obj["ai_cut_p"],
                                )
                                if synth_p and Path(synth_p).exists():
                                    return synth_p
                            except Exception:
                                pass
                        ai_q = f"{sc_kw}, {task_obj['ai_angle']}, dramatic vertical 9:16 photography, 8k sharp focus"
                        return image_agent.generate_scene_image(
                            ai_q,
                            task_obj["ai_cut_p"],
                            "9:16",
                            state.get("channel_style", "cinematic"),
                            sid,
                            sc_item.get("visual_prompt"),
                        )

                    ai_completed = await asyncio.to_thread(
                        run_langgraph_parallel_tasks,
                        ai_tasks,
                        _gen_single_ai_shot,
                        f"AIImageGen_Scene{sid}({len(ai_tasks)} shots)",
                    )
                    for ai_res in ai_completed:
                        gen_p = ai_res.get("result")
                        if gen_p and Path(gen_p).exists() and Path(gen_p).stat().st_size > 2500:
                            rag.verify_and_commit_in_job_image(job_id=job_id, scene_id=sid, image_path=gen_p, url=gen_p)
                            verified_poses.append(gen_p)

                real_poses = [p for p in verified_poses if p and not str(p).endswith("_graphic.png")]
                if real_poses:
                    verified_poses = real_poses
                    if str(sc_item.get("media_path") or "").endswith("_graphic.png"):
                        sc_item["media_path"] = real_poses[0]
                sc_item["pose_images"] = verified_poses[:quota]

            await asyncio.gather(*[_fill_scene_image_quota(i_slot) for i_slot in body_img_indices])

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
                f"(Mục tiêu: 15-17 ảnh/video ~40s | Hook={hook_img_count}img | Body: {per_scene_log})"
            )
    except Exception as e_dens:
        logger.warning(f"[MediaNodes] Visual Density Engine warning: {e_dens}")

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

    async def _refine_single_item(item: Dict[str, Any]) -> None:
        if item.get("scene_id") not in failed_ids:
            return
        scene_id = item["scene_id"]
        kw = item.get("visual_keyword", "")
        narr = item.get("narration", "")
        vp = item.get("visual_prompt", None)
        clean_kw_q = clean_search_query_from_prompt(kw or vp or "", narration=narr, topic=state.get("topic", ""), max_words=6) or kw
        is_video = item.get("media_type") in ("video", "hook_ready_video") or item.get("production_method") in (
            "STOCK_FOOTAGE", "TEXT_TO_VIDEO", "YOUTUBE_PEAK_SHORT", "CLIMAX_PEAK_SHORT_SPLICE", "LONG_PEAK_CONTINUOUS_FLOW"
        )
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
                old_poses = [p for p in (item.get("pose_images") or [])[1:] if p and Path(p).exists()]
                item["media_path"] = res_img
                item["media_type"] = "image"
                item["pose_images"] = [res_img, *old_poses]
                logger.info(f"Scene {scene_id}: ✅ Đã thay thế bằng ảnh sắc nét mới (giữ {len(item['pose_images'])} ảnh/cảnh)")
            else:
                try:
                    best_ph = await asyncio.to_thread(
                        media_agent.search_best_photo,
                        clean_kw_q,
                        used_refine_urls,
                        None,
                        narr,
                        state.get("topic", ""),
                    )
                    if best_ph and best_ph.get("url"):
                        ph_url = best_ph["url"]
                        out_ph = str(media_dir / f"scene_{scene_id:03d}_refined_stock.jpg")
                        await asyncio.to_thread(media_agent.download_file, ph_url, out_ph)
                        if Path(out_ph).exists() and Path(out_ph).stat().st_size > 3000:
                            used_refine_urls.add(ph_url)
                            item["media_path"] = out_ph
                            item["media_type"] = "image"
                            item["pose_images"] = [out_ph]
                            logger.info(f"Scene {scene_id}: ✅ Đã thay thế bằng ảnh Stock HD mới: '{clean_kw_q}'")
                except Exception as ex_ph:
                    logger.debug(f"Scene {scene_id}: Stock photo refine error: {ex_ph}")

    await asyncio.gather(*[_refine_single_item(item) for item in scenes_media if isinstance(item, dict)])

    return {
        "scenes_media": scenes_media,
        "media_retry_count": retry_count,
        "current_step": "MEDIA_REFINED",
        "progress_message": f"Đã tự động sửa lại song song {len(failed_ids)} cảnh media kỹ thuật chưa đạt",
    }
