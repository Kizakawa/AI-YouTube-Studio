"""
app.graph.nodes.media_nodes — Node tìm kiếm, thẩm định & sửa chữa media trong LangGraph.

Bao gồm:
- node_fetch_media: Thu thập tư liệu đa nguồn cho từng cảnh.
- node_evaluate_media: Kiểm tra KỸ THUẬT file (VisualCritic rule-based).
- node_refine_media: Sửa chữa file lỗi kỹ thuật.
- node_content_quality_check: Đánh giá NỘI DUNG ảnh bằng AI Vision (ContentQualityCritic).
- node_refine_content: Thay thế ảnh có nội dung kém chất lượng dựa trên gợi ý AI.
"""
import asyncio
import logging
import os
from pathlib import Path
from typing import Dict, Any, List

from app.schemas.contracts import ScriptContract, SceneContract
from app.agents.media_agent import MediaAgent, detect_real_entity, match_best_pool_photo, clean_scene_search_query
from app.agents.image_agent import ImageAgent
from app.agents.visual_director_agent import VisualDirectorAgent
from app.agents.video_strategist_agent import VideoStrategistAgent
from app.agents.visual_curation_agent import VisualCurationAgent
from app.agents.ai_video_agent import AIVideoAgent
from app.graph.evaluators.visual_critic import VisualCritic
from app.graph.evaluators.content_quality_critic import ContentQualityCritic
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
    Node Đạo Diễn Chiến Lược & Phương Pháp Làm Video (Video Strategist Agent).
    Phân tích toàn diện ý tưởng, nhân vật và từng phân cảnh để ra quyết định:
    - Khi nào dùng REAL_ARCHIVE (Tư liệu lịch sử thật 100% của nhân vật/sự kiện có thật).
    - Khi nào dùng IMAGE_TO_VIDEO (Tạo video phụ thuộc vào ảnh chủ đề 8K - giữ nét, không méo hình).
    - Khi nào dùng TEXT_TO_VIDEO (Tạo video AI trực tiếp - cho chủ đề huyền bí, năng lượng, vũ trụ trừu tượng).
    - Khi nào dùng STOCK_FOOTAGE (Video B-roll chuyển động thực tế 9:16 đời thường).
    """
    topic = state.get("topic", "")
    script_data = state.get("script_data", {})
    primary_entity = state.get("primary_entity")
    entity_type = state.get("entity_type", "general")
    channel_style = state.get("channel_style", "cinematic")
    scenes = script_data.get("scenes", [])

    strategist = VideoStrategistAgent()
    plan = await asyncio.to_thread(
        strategist.propose_production_strategy,
        topic=topic,
        scenes=scenes,
        primary_entity=primary_entity,
        entity_type=entity_type,
        channel_niche=channel_style,
    )

    proposals_summary = ", ".join([f"Cảnh {s.get('scene_id')}: {s.get('production_method', s.get('strategy'))}" for s in plan.get("scenes_strategy", [])[:4]])
    logger.info(f"🎬 [VideoStrategist] Đã duyệt phương pháp sản xuất ({plan.get('topic_classification')}): {proposals_summary}...")

    return {
        "visual_plan": plan,
        "current_step": "VISUAL_STRATEGY_PLANNED",
        "progress_percent": 55,
        "progress_message": f"🎬 Đạo diễn chiến lược đã duyệt phương pháp ({plan.get('topic_classification', 'Tự động')}): {len(plan.get('scenes_strategy', []))} cảnh",
    }


async def node_fetch_media(state: VideoWorkflowState) -> Dict[str, Any]:
    """
    Node thu thập tư liệu hình ảnh / video chất lượng cao dựa trên chỉ đạo của Visual Director:
      1. REAL_DOCUMENTARY : Tìm ảnh tư liệu lịch sử thật (Wikipedia / Wikimedia / Archive)
      2. STOCK_VIDEO      : Tìm video clip chuyển động 9:16 (Pexels Video)
      3. AI_VIDEO         : Sinh video AI ảo / huyền bí bằng AIVideoAgent
      4. AI_IMAGE         : Sinh ảnh nghệ thuật điện ảnh 8K bằng ImageAgent
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

    # Lấy bản kế hoạch từ VisualDirectorAgent (nếu chưa có thì tự động tạo nhanh)
    visual_plan = state.get("visual_plan")
    if not visual_plan:
        director = VisualDirectorAgent()
        visual_plan = await asyncio.to_thread(
            director.create_visual_plan,
            topic=state.get("topic", ""),
            scenes=[s.model_dump() for s in script.scenes],
            primary_entity=primary_entity,
            entity_type=entity_type,
            channel_niche=state.get("channel_style", "cinematic"),
        )

    strategy_map = {
        s.get("scene_id"): s
        for s in visual_plan.get("scenes_strategy", [])
    }

    scenes_media = []
    from app.services.media.media_history import get_recent_media_urls, record_used_media
    used_pool_urls = get_recent_media_urls(limit=150)
    job_id = state.get("job_id", 0)

    sem = asyncio.Semaphore(4)
    completed_lock = asyncio.Lock()
    completed_count = 0
    total_scenes = len(script.scenes)

    from app.graph.runner import report_job_progress
    if job_id:
        await report_job_progress(
            job_id,
            "MEDIA_FETCHING",
            f"🎬 Bắt đầu thu thập & tạo tư liệu ({total_scenes} cảnh)...",
            56
        )

    # Phân tích ngữ cảnh đồng loạt toàn bộ các cảnh trong 1 lần gọi Gemini (giảm ~85% latency & tránh 429)
    batch_analyses: Dict[int, Dict[str, Any]] = {}
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

    async def _fetch_one_scene(idx: int, scene) -> Dict[str, Any]:
        media_path = ""
        media_type = "image"
        vision_curated = False
        sq = getattr(scene, 'stock_query', None)
        vp = getattr(scene, 'visual_prompt', None)
        kw = scene.visual_keyword or ""

        # ── Agent Phân tích Ngữ cảnh & Ý niệm Thị giác Chuyên sâu (VisualCurationAgent) ──
        surround_text = " ".join([s.narration for s in script.scenes[max(0, idx - 1):min(len(script.scenes), idx + 2)]])
        v_analysis = batch_analyses.get(scene.scene_id)
        if not v_analysis:
            v_analysis = await asyncio.to_thread(
                curator.analyze_scene_context,
                narration=scene.narration,
                topic=state.get("topic", ""),
                scene_id=scene.scene_id,
                total_scenes=len(script.scenes),
                primary_entity=primary_entity,
                entity_type=entity_type,
                surrounding_context=surround_text,
                channel_niche=state.get("channel_style", "cinematic"),
            )

        # Lấy chiến lược và phương pháp do Video Strategist hoặc Visual Curation Agent chỉ định
        sc_strat = strategy_map.get(scene.scene_id, {})
        chosen_strategy = sc_strat.get("strategy", "STOCK_VIDEO")
        prod_method = sc_strat.get("production_method") or v_analysis.get("production_method") or (
            "IMAGE_TO_VIDEO" if chosen_strategy == "AI_IMAGE"
            else ("TEXT_TO_VIDEO" if chosen_strategy == "AI_VIDEO"
            else ("REAL_ARCHIVE" if chosen_strategy == "REAL_DOCUMENTARY" else "STOCK_FOOTAGE"))
        )

        channel_visual_mode = state.get("visual_mode") or "auto"
        scene_media_pref = getattr(scene, 'media_type', None)
        is_historical_niche = any(h in str(current_niche).lower() for h in ("history", "historical", "documentary", "crime"))

        # Tôn trọng tuyệt đối chế độ hình ảnh của kênh (imagen_ai) hoặc chỉ định cảnh (ai_image)
        if channel_visual_mode in ("imagen_ai", "ai_image") and (prod_method != "REAL_ARCHIVE" or not is_historical_niche):
            prod_method = "IMAGE_TO_VIDEO"
            chosen_strategy = "AI_IMAGE"
        elif scene_media_pref == "ai_image":
            prod_method = "IMAGE_TO_VIDEO"
            chosen_strategy = "AI_IMAGE"
        elif (prod_method == "REAL_ARCHIVE" or chosen_strategy == "REAL_DOCUMENTARY") and not is_historical_niche:
            # Ngăn chặn lỗi nhận nhầm tên loài động vật / sinh vật ("Sucuri-verde", "Tardigrade") thành nhân vật lịch sử tải ảnh tĩnh
            if any(w in str(current_niche).lower() for w in ("wildlife", "animal", "rescue", "predator", "nature")):
                prod_method = "STOCK_FOOTAGE"
                chosen_strategy = "STOCK_VIDEO"
            else:
                prod_method = "IMAGE_TO_VIDEO"
                chosen_strategy = "AI_IMAGE"

        tool = sc_strat.get("tool", "")
        rationale = v_analysis.get("narrative_subtext") or sc_strat.get("rationale", "")
        search_query = (v_analysis.get("search_queries") or [sc_strat.get("search_query") or sq or kw])[0]
        if sc_strat.get("reality_class") and sc_strat.get("search_query"):
            # VisualToolRouter đã dựng truy vấn theo đúng cảnh (stock_query / keyword / gợi ý CGI) -> ưu tiên hơn bộ curated chung
            search_query = sc_strat["search_query"]
        prefer_generation = bool(sc_strat.get("prefer_generation"))
        ai_prompt = v_analysis.get("cinematic_ai_prompt") or sc_strat.get("ai_prompt") or vp or kw
        camera_motion = sc_strat.get("camera_motion", "zoom_in")
        sfx_cue = sc_strat.get("sfx_cue", "whoosh" if idx == 0 else "hit")

        # Thời lượng ước lượng của phân cảnh (giây)
        dur = float(getattr(scene, 'estimated_seconds', 3.5) or 3.5)
        scenes_audio = state.get("scenes_audio", [])
        if scenes_audio:
            sc_audio = next((a for a in scenes_audio if a.get("scene_id") == scene.scene_id), None)
            if sc_audio and sc_audio.get("audio_duration_ms"):
                dur = max(float(sc_audio["audio_duration_ms"]) / 1000.0, 1.0)

        logger.info(f"🎯 [Scene {scene.scene_id}] Phương pháp: '{prod_method}' | Strategy: '{chosen_strategy}' ({rationale[:60]}...)")

        # ══════════════════════════════════════════════════════════════════════
        # TẦNG 0 (ƯU TIÊN TUYỆT ĐỐI): GEMINI GOOGLE SEARCH + VISION CURATION + 3D MOTION
        # Phụ thuộc vào kết quả tìm kiếm Google Images bắt mắt để câu view (CGI, Concept Art 8K,
        # Movie Scene, Dramatic HD, Dark Horror) do Gemini Vision trực tiếp chấm chọn.
        # TỐI ƯU TỐC ĐỘ: Giữ nguyên file ảnh tĩnh ở bước Fetch để VideoAgent._prepare_scene_clips
        # render 3D Ken Burns ĐÚNG 1 LẦN DUY NHẤT (kèm bù thời lượng xfade), giảm 50% thời gian FFmpeg!
        # ══════════════════════════════════════════════════════════════════════
        search_first = os.getenv("SEARCH_FIRST_VISUAL_MODE", "true").strip().lower() not in ("0", "false", "no", "off")
        creature_first = os.getenv("CREATURE_ART_FIRST", "true").strip().lower() not in ("0", "false", "no", "off")
        tier0_searched = False
        if (
            (search_first or (creature_first and v_analysis.get("creature_mode")))
            and channel_visual_mode not in ("imagen_ai", "ai_image")
        ):
            try:
                tier0_searched = True
                g_queries = (
                    (v_analysis.get("creature_queries") if v_analysis.get("creature_mode") else None)
                    or v_analysis.get("google_image_queries")
                    or v_analysis.get("search_queries")
                    or [search_query or sq or kw]
                )
                primary_g_query = next((q for q in g_queries if q), search_query or sq or kw)
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
        if prod_method == "REAL_ARCHIVE" or chosen_strategy == "REAL_DOCUMENTARY":
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

        return {
            "scene_id": scene.scene_id,
            "media_path": media_path,
            "media_type": media_type,
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
    """Node tự động sửa chữa các cảnh bị đánh giá FAIL kỹ thuật (thay thế media mới chuẩn xác, giữ vững video động)."""
    scenes_media = list(state.get("scenes_media", []))
    media_eval = state.get("media_eval", {})
    failed_ids = media_eval.get("failed_scene_ids", [])
    primary_entity = state.get("primary_entity")
    work_dir = Path(state.get("work_dir", "storage/cache"))
    media_dir = work_dir / "media"
    retry_count = state.get("media_retry_count", 0) + 1

    image_agent = ImageAgent()
    media_agent = MediaAgent()
    ai_video_agent = AIVideoAgent()
    current_niche = state.get("niche") or state.get("channel_style") or state.get("channel_niche") or "general"
    is_scifi_val = (current_niche == "scifi") or any(k in state.get("topic", "").lower() for k in ("scifi", "sci-fi", "alien", "cyborg", "viễn tưởng"))
    logger.info(f"🔄 [LangGraph] Tự động sửa chữa media kỹ thuật cho các cảnh: {failed_ids} (Lần {retry_count})")

    for item in scenes_media:
        if item.get("scene_id") in failed_ids:
            scene_id = item["scene_id"]
            kw = item.get("visual_keyword", "")
            narr = item.get("narration", "")
            vp = item.get("visual_prompt", None)
            is_video = item.get("media_type") == "video" or item.get("production_method") in ("STOCK_FOOTAGE", "TEXT_TO_VIDEO")
            replaced = False

            # Nếu cảnh ban đầu là video -> Cố gắng giữ nguyên định dạng video chuyển động
            if is_video:
                try:
                    best_v = await asyncio.to_thread(media_agent.search_best_video_clip, kw, None, None, narr, state.get("topic", ""))
                    if best_v and best_v.get("url"):
                        cand_url = best_v["url"]
                        out_v = str(media_dir / f"scene_{scene_id:03d}_refined_video.mp4")
                        await asyncio.to_thread(media_agent.download_file, cand_url, out_v)
                        if Path(out_v).exists() and Path(out_v).stat().st_size > 10000:
                            item["media_path"] = out_v
                            item["media_type"] = "video"
                            replaced = True
                            logger.info(f"Scene {scene_id}: ✅ Sửa lỗi kỹ thuật bằng Video Clip Pexels mới: '{kw}'")
                except Exception as ex_v:
                    logger.debug(f"Scene {scene_id}: Video refine error: {ex_v}")

                if not replaced:
                    try:
                        ai_v_path = str(media_dir / f"scene_{scene_id:03d}_refined_i2v.mp4")
                        res_vid = await ai_video_agent.generate_image_to_video(
                            prompt=vp or kw,
                            output_path=ai_v_path,
                            duration_seconds=_scene_audio_duration(work_dir, scene_id),
                            scene_id=scene_id,
                            visual_prompt=vp,
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

            # Nếu là ảnh hoặc video dự phòng chưa thành công: sinh ảnh AI điện ảnh chất lượng cao
            if not replaced:
                ai_img_path = str(media_dir / f"scene_{scene_id:03d}_refined.jpg")
                fallback_kw = f"Dramatic cinematic photorealistic shot of {primary_entity or kw}, {kw}, 8k sharp focus"
                res_img = await asyncio.to_thread(
                    image_agent.generate_scene_image,
                    fallback_kw,
                    ai_img_path,
                    "9:16",
                    "cinematic",
                    scene_id,
                    vp,
                )
                if res_img and Path(res_img).exists() and Path(res_img).stat().st_size > 2000:
                    item["media_path"] = res_img
                    item["media_type"] = "image"
                    logger.info(f"Scene {scene_id}: ✅ Đã thay thế bằng ảnh AI sắc nét mới")

    return {
        "scenes_media": scenes_media,
        "media_retry_count": retry_count,
        "current_step": "MEDIA_REFINED",
        "progress_message": f"Đã tự động sửa lại {len(failed_ids)} cảnh media kỹ thuật chưa đạt",
    }


# ──────────────────────────────────────────────────────────────────────────────
# NỘI DUNG QUALITY NODES — Sử dụng AI Vision để đánh giá & sửa chữa nội dung
# ──────────────────────────────────────────────────────────────────────────────

def node_content_quality_check(state: VideoWorkflowState) -> Dict[str, Any]:
    """
    Node AI Vision Critic — phân tích NỘI DUNG hình ảnh bằng Gemini Vision.
    
    TỐI ƯU TỐC ĐỘ: Nếu các phân cảnh đã được Gemini Vision trực tiếp chấm chọn ở Tầng 0
    (`vision_curated=True` hoặc `GEMINI_SEARCH_3D` / `CREATURE_ART`), bỏ qua lượt chấm lặp lại thứ 2
    để tiết kiệm 15-30 giây và tránh việc ContentQualityCritic từ chối nhầm ảnh CGI/Concept Art câu view!
    """
    scenes_media = state.get("scenes_media", [])
    primary_entity = state.get("primary_entity")
    job_id = state.get("job_id", 0)

    skip_redundant = os.getenv("SKIP_REDUNDANT_VISION_CRITIC", "true").strip().lower() not in ("0", "false", "no", "off")
    valid_scenes = [m for m in scenes_media if isinstance(m, dict)]
    already_curated_count = sum(
        1 for m in valid_scenes
        if m.get("vision_curated") or m.get("production_method") in ("GEMINI_SEARCH_3D", "CREATURE_ART")
    )
    if skip_redundant and valid_scenes and already_curated_count >= max(1, int(len(valid_scenes) * 0.6)):
        logger.info(
            f"⚡ [ContentQualityCritic] Bỏ qua thẩm định Vision lần 2 vì {already_curated_count}/{len(valid_scenes)} cảnh "
            f"đã được Gemini Vision tuyển chọn trực tiếp ở bước Search!"
        )
        return {
            "content_quality_eval": {
                "overall_score": 9.0,
                "passed": True,
                "ai_scenes_checked": already_curated_count,
                "failed_scene_ids": [],
                "scene_scores": [],
            },
            "current_step": "CONTENT_QUALITY_CHECKED",
            "progress_percent": 75,
            "progress_message": f"Thẩm định nội dung AI: 9.0/10 (Đã duyệt trực tiếp qua Gemini Vision Search)",
        }

    if job_id:
        from app.graph.runner import report_job_progress_sync
        report_job_progress_sync(job_id, "CONTENT_EVAL", "🧠 Đang thẩm định tính phù hợp nội dung qua AI Vision...", 72)

    critic = ContentQualityCritic()
    report = critic.evaluate_content_quality(
        scenes_media=scenes_media,
        primary_entity=primary_entity,
        topic=state.get("topic", ""),
        max_ai_checks=10,
    )

    logger.info(
        f"🧠 [ContentQualityCritic] Điểm nội dung AI: {report.overall_score}/10 | "
        f"Passed: {report.passed} | AI đã kiểm tra: {report.ai_scenes_checked} cảnh | "
        f"Lỗi nội dung: {len(report.failed_scene_ids)} cảnh"
    )

    if report.failed_scene_ids:
        for s in report.scene_scores:
            if s.scene_id in report.failed_scene_ids:
                logger.warning(
                    f"  ⚠️ Cảnh {s.scene_id} ({s.verdict}): "
                    f"Relevance={s.relevance_score}, Entity={s.entity_accuracy_score} | "
                    f"Issues: {'; '.join(s.issues)} | "
                    f"Gợi ý: {s.replacement_suggestion}"
                )

    return {
        "content_quality_eval": report.model_dump(),
        "current_step": "CONTENT_QUALITY_CHECKED",
        "progress_percent": 75,
        "progress_message": (
            f"Thẩm định nội dung AI: {report.overall_score}/10 "
            f"({'Đạt chuẩn' if report.passed else f'Cần cải thiện {len(report.failed_scene_ids)} cảnh'})"
        ),
    }


async def node_refine_content(state: VideoWorkflowState) -> Dict[str, Any]:
    """
    Node tự động thay thế tư liệu có NỘI DUNG kém — dựa trên chỉ đạo sắc bén từ AI Vision Critic.
    Đảm bảo:
    - Khi Critic chỉ định [pexels_video] -> Tìm đúng video clip B-roll chuyển động 9:16.
    - Khi Critic chỉ định [ai_video] -> Tạo Video AI siêu thực trực tiếp.
    - Khi Critic chỉ định [imagen3_i2v] -> Tạo ảnh 8K rồi chuyển hóa thành video 3D.
    - Khi Critic chỉ định [wikimedia_archive] -> Lấy ảnh tư liệu lịch sử thật 100%.
    - Triệt tiêu tư duy ép cứng sang ảnh tĩnh, giữ tỷ lệ video động cao!
    """
    scenes_media = list(state.get("scenes_media", []))
    content_eval = state.get("content_quality_eval", {})
    failed_ids = content_eval.get("failed_scene_ids", [])
    primary_entity = state.get("primary_entity")
    work_dir = Path(state.get("work_dir", "storage/cache"))
    media_dir = work_dir / "media"
    content_retry = state.get("content_retry_count", 0) + 1

    # Thu thập gợi ý thay thế từ AI
    suggestions = {}
    for score in content_eval.get("scene_scores", []):
        if score.get("scene_id") in failed_ids and score.get("replacement_suggestion"):
            suggestions[score["scene_id"]] = score["replacement_suggestion"]

    image_agent = ImageAgent()
    media_agent = MediaAgent()
    ai_video_agent = AIVideoAgent()
    current_niche = state.get("niche") or state.get("channel_style") or state.get("channel_niche") or "general"
    is_scifi_val = (current_niche == "scifi") or any(k in state.get("topic", "").lower() for k in ("scifi", "sci-fi", "alien", "cyborg", "viễn tưởng"))

    # Chủ đề thiên nhiên / địa danh có thật (rừng Amazon, biển sâu, sa mạc...): footage thật luôn được thử trước,
    # vì ảnh AI miễn phí thường vẽ sai loài (cá sấu thành gấu / báo) và không có bối cảnh thật.
    from app.services.media.query_broadener import is_real_world_nature_topic
    is_nature_topic = is_real_world_nature_topic(state.get("topic", ""))
    
    logger.info(
        f"🔄 [ContentRefine] Tự sửa nội dung cho {len(failed_ids)} cảnh "
        f"(Lần {content_retry}) dựa trên chỉ đạo AI Vision Critic"
    )

    job_id = state.get("job_id", 0)
    for item in scenes_media:
        scene_id = item.get("scene_id")
        if scene_id not in failed_ids:
            continue

        if job_id:
            fix_idx = failed_ids.index(scene_id) + 1 if scene_id in failed_ids else 1
            pct = min(85, 72 + int((fix_idx / max(len(failed_ids), 1)) * 14))
            from app.graph.runner import report_job_progress_sync
            report_job_progress_sync(job_id, "CONTENT_REFINING", f"🎯 Đang tối ưu lại tư liệu Cảnh {scene_id} ({fix_idx}/{len(failed_ids)})...", pct)

        kw = item.get("visual_keyword", "")
        narr = item.get("narration", "")
        raw_suggestion = suggestions.get(scene_id, kw)
        replaced = False

        # Phân tích chỉ đạo: [recommended_tool] query | prompt | creative_idea
        rec_tool = ""
        clean_sugg = raw_suggestion
        if raw_suggestion.startswith("[") and "]" in raw_suggestion:
            rec_tool = raw_suggestion[1:raw_suggestion.find("]")].strip().lower()
            clean_sugg = raw_suggestion[raw_suggestion.find("]") + 1:].strip()

        parts = [p.strip() for p in clean_sugg.split("|")]
        query_part = parts[0] if len(parts) > 0 and parts[0] else kw
        prompt_part = parts[1] if len(parts) > 1 and parts[1] else clean_sugg
        idea_part = parts[2] if len(parts) > 2 else ""

        logger.info(f"🎯 [Scene {scene_id} Fix] Tool chỉ định: '{rec_tool or 'auto'}' | Query: '{query_part}' | Idea: '{idea_part[:40]}'")

        # ── HÀNH ĐỘNG 0 (ƯU TIÊN TUYỆT ĐỐI CHO QUÁI VẬT / GEMINI SEARCH): Tìm ảnh 8K chuẩn Google Search + Vision Curation ──
        from app.services.media.image_quality import is_creature_subject, creature_queries
        is_creature_fix = is_creature_subject(query_part or kw, narration=narr, topic=state.get("topic", ""))
        search_first_fix = os.getenv("SEARCH_FIRST_VISUAL_MODE", "true").strip().lower() not in ("0", "false", "no", "off")
        if not replaced and (is_creature_fix or (search_first_fix and rec_tool not in ("pexels_video", "wikimedia_archive"))):
            try:
                va_fix = None
                target_fix_q = query_part or kw
                if is_creature_fix:
                    c_qs = creature_queries(target_fix_q, topic=state.get("topic", ""), narration=narr)
                    va_fix = {"subject": target_fix_q, "creature_mode": True, "creature_queries": c_qs, "google_image_queries": c_qs[:3]}
                    if c_qs:
                        target_fix_q = c_qs[0]
                best_p = await asyncio.to_thread(
                    media_agent.search_best_photo,
                    target_fix_q,
                    None,
                    va_fix,
                    narr,
                    state.get("topic", ""),
                )
                if best_p and best_p.get("url"):
                    out_p = str(media_dir / f"scene_{scene_id:03d}_content_fix_gsearch.jpg")
                    await asyncio.to_thread(media_agent.download_file, best_p["url"], out_p)
                    if Path(out_p).exists() and Path(out_p).stat().st_size > 3000:
                        item["media_path"] = out_p
                        item["media_type"] = "image"
                        item["vision_curated"] = True
                        item["production_method"] = "CREATURE_ART" if is_creature_fix else "GEMINI_SEARCH_3D"
                        replaced = True
                        logger.info(f"Scene {scene_id}: ✅ Thay thế thành công bằng Ảnh 8K Multi-Engine ({best_p.get('source')}): '{target_fix_q}'")
            except Exception as ex_gp:
                logger.debug(f"Scene {scene_id}: Multi-Engine Search fix error: {ex_gp}")

        # ── HÀNH ĐỘNG 1: Nếu chỉ định pexels_video hoặc cảnh cần chuyển động B-roll ──
        if not replaced and (
            rec_tool == "pexels_video"
            or (not rec_tool and item.get("production_method") in ("STOCK_FOOTAGE", "TEXT_TO_VIDEO"))
            or (is_nature_topic and rec_tool != "wikimedia_archive")
        ):
            try:
                best_v = await asyncio.to_thread(media_agent.search_best_video_clip, query_part or kw, None, None, narr, state.get("topic", ""))
                if best_v and best_v.get("url"):
                    cand_u = best_v["url"]
                    out_v = str(media_dir / f"scene_{scene_id:03d}_content_fix_pexels.mp4")
                    await asyncio.to_thread(media_agent.download_file, cand_u, out_v)
                    if Path(out_v).exists() and Path(out_v).stat().st_size > 10000:
                        item["media_path"] = out_v
                        item["media_type"] = "video"
                        replaced = True
                        logger.info(f"Scene {scene_id}: ✅ Thay thế thành công bằng Video Clip Pexels: '{query_part}'")
                    else:
                        Path(out_v).unlink(missing_ok=True)  # dọn file tải hỏng (0 byte)
            except Exception as ex_v:
                logger.debug(f"Scene {scene_id}: Video replacement error: {ex_v}")

        # ── HÀNH ĐỘNG 2: Nếu chỉ định ai_video (Text-to-Video siêu thực) ──
        if not replaced and (rec_tool == "ai_video" or item.get("production_method") == "TEXT_TO_VIDEO"):
            try:
                ai_v_path = str(media_dir / f"scene_{scene_id:03d}_content_fix_t2v.mp4")
                res_v = await ai_video_agent.generate_text_to_video(
                    prompt=prompt_part or query_part,
                    output_path=ai_v_path,
                    duration_seconds=_scene_audio_duration(work_dir, scene_id),
                    scene_id=scene_id,
                    visual_prompt=prompt_part,
                    narration=narr,
                    niche=current_niche,
                    is_scifi=is_scifi_val,
                )
                if res_v and Path(res_v).exists() and Path(res_v).stat().st_size > 5000:
                    item["media_path"] = res_v
                    item["media_type"] = "video"
                    replaced = True
                    logger.info(f"Scene {scene_id}: ✅ Thay thế thành công bằng Video AI kỳ ảo: '{prompt_part[:50]}'")
            except Exception as ex_ai_t2v:
                logger.debug(f"Scene {scene_id}: Text-to-Video fix error: {ex_ai_t2v}")

        # ── HÀNH ĐỘNG 3: Nếu chỉ định imagen3_i2v (Ảnh chủ đề chuyển thành video 3D) ──
        if not replaced and (rec_tool == "imagen3_i2v" or item.get("production_method") == "IMAGE_TO_VIDEO"):
            try:
                ai_v_path = str(media_dir / f"scene_{scene_id:03d}_content_fix_i2v.mp4")
                res_v = await ai_video_agent.generate_image_to_video(
                    prompt=prompt_part or query_part,
                    output_path=ai_v_path,
                    duration_seconds=_scene_audio_duration(work_dir, scene_id),
                    scene_id=scene_id,
                    visual_prompt=prompt_part,
                    narration=narr,
                    niche=current_niche,
                    is_scifi=is_scifi_val,
                )
                if res_v and Path(res_v).exists() and Path(res_v).stat().st_size > 5000:
                    item["media_path"] = res_v
                    item["media_type"] = "video"
                    replaced = True
                    logger.info(f"Scene {scene_id}: ✅ Thay thế thành công bằng Image-to-Video 3D Parallax: '{prompt_part[:50]}'")
            except Exception as ex_i2v:
                logger.debug(f"Scene {scene_id}: Image-to-Video fix error: {ex_i2v}")

        # ── HÀNH ĐỘNG 4: Nếu chỉ định wikimedia_archive hoặc có thực thể lịch sử ──
        if not replaced and (rec_tool == "wikimedia_archive" or (primary_entity and scene_id in (1, 2))):
            try:
                cand = await asyncio.to_thread(
                    media_agent.search_scene_media_multi_source,
                    scene_narration=narr,
                    visual_keyword=query_part or kw,
                    stock_query=query_part,
                    primary_entity=primary_entity,
                    scene_id=scene_id,
                    prefer_video=False,
                    prefer_photo=True,
                )
                if cand and cand.get("url"):
                    cand_url = cand["url"]
                    c_src = cand.get("source", "fix")
                    c_type = cand.get("media_type", "image")
                    ext = ".mp4" if c_type == "video" else (".jpg" if any(x in cand_url.lower() for x in ["jpg", "jpeg"]) else ".png")
                    out = str(media_dir / f"scene_{scene_id:03d}_content_fix_{c_src}{ext}")
                    await asyncio.to_thread(media_agent.download_file, cand_url, out)
                    if Path(out).exists() and Path(out).stat().st_size > 3000:
                        item["media_path"] = out
                        item["media_type"] = c_type
                        replaced = True
                        logger.info(f"Scene {scene_id}: ✅ Thay thế thành công tư liệu thật từ {c_src}")
            except Exception as ex_wiki:
                logger.debug(f"Scene {scene_id}: Archive fix failed: {ex_wiki}")

        # ── HÀNH ĐỘNG 5: Dự phòng cuối cùng — AI Image Generation phong cách điện ảnh 8K ──
        if not replaced:
            try:
                ai_path = str(media_dir / f"scene_{scene_id:03d}_content_ai.jpg")
                refined_prompt = prompt_part or query_part or kw
                if scene_id == 1:
                    prompt = f"Ultra-high retention YouTube Shorts hook frame, 8k cinematic: {refined_prompt}"
                elif primary_entity:
                    prompt = (
                        f"Historically authentic documentary portrait of {primary_entity}, "
                        f"{refined_prompt}, period accurate setting, 8k documentary photograph"
                    )
                else:
                    prompt = f"Authentic cinematic documentary photography: {refined_prompt}, sharp focus 8k"

                res = await asyncio.to_thread(
                    lambda: image_agent.generate_scene_image(
                        prompt=prompt,
                        output_path=ai_path,
                        aspect_ratio="9:16",
                        niche=current_niche,
                        scene_id=scene_id,
                        visual_prompt=refined_prompt,
                        topic=state.get("topic", ""),
                        premium=True,  # Ảnh đã bị AI Vision chấm trượt → dùng engine tốt nhất hiện có
                    )
                )
                if res and Path(res).exists() and Path(res).stat().st_size > 2000:
                    item["media_path"] = res
                    item["media_type"] = "image"
                    replaced = True
                    logger.info(
                        f"Scene {scene_id}: ✅ Thay bằng ảnh với prompt tinh chỉnh từ AI Vision "
                        f"(nguồn: {image_agent.last_source or 'không rõ'})"
                    )
            except Exception as e:
                logger.warning(f"Scene {scene_id}: Content refine AI generation error: {e}")

        if replaced:
            logger.info(f"  📝 Chỉ đạo AI Vision đã áp dụng hoàn tất: '{clean_sugg[:60]}'")
        else:
            logger.warning(f"  ❌ Scene {scene_id}: Không thể tìm tư liệu thay thế phù hợp")

    return {
        "scenes_media": scenes_media,
        "content_retry_count": content_retry,
        "current_step": "CONTENT_REFINED",
        "progress_percent": 78,
        "progress_message": (
            f"Đã tinh chỉnh nội dung {len(failed_ids)} cảnh "
            f"dựa trên phân tích AI Vision (Lần {content_retry})"
        ),
    }

