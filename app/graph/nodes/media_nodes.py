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
    used_pool_urls = set()

    for idx, scene in enumerate(script.scenes):
        media_path = ""
        media_type = "image"
        sq = getattr(scene, 'stock_query', None)
        vp = getattr(scene, 'visual_prompt', None)
        kw = scene.visual_keyword or ""

        # ── Agent Phân tích Ngữ cảnh & Ý niệm Thị giác Chuyên sâu (VisualCurationAgent) ──
        surround_text = " ".join([s.narration for s in script.scenes[max(0, idx - 1):min(len(script.scenes), idx + 2)]])
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
        tool = sc_strat.get("tool", "")
        rationale = v_analysis.get("narrative_subtext") or sc_strat.get("rationale", "")
        search_query = (v_analysis.get("search_queries") or [sc_strat.get("search_query") or sq or kw])[0]
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
                    best_vid = await asyncio.to_thread(media_agent.search_best_video_clip, search_query or sq or kw, used_pool_urls, v_analysis, scene.narration)
                    if not best_vid and kw:
                        clean_kw = clean_scene_search_query(kw, max_words=2)
                        best_vid = await asyncio.to_thread(media_agent.search_best_video_clip, clean_kw, used_pool_urls, v_analysis, scene.narration)
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
                    best_vid = await asyncio.to_thread(media_agent.search_best_video_clip, search_query or sq or kw, used_pool_urls)
                    if not best_vid and kw:
                        clean_kw = clean_scene_search_query(kw, max_words=2)
                        best_vid = await asyncio.to_thread(media_agent.search_best_video_clip, clean_kw, used_pool_urls)
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
                )
                if res_v and Path(res_v).exists() and Path(res_v).stat().st_size > 5000:
                    media_path = res_v
                    media_type = "video"
                    logger.info(f"Scene {scene.scene_id}: 🎥 Fallback 2 thành công với AI Motion Video 9:16 (giữ nhịp video động)")
            except Exception as fb_v_e:
                logger.debug(f"Scene {scene.scene_id}: Fallback 2 AI video error: {fb_v_e}")

        # Fallback 3: Thử tìm ảnh Pexels Photo HD chân thực 9:16
        if not media_path:
            try:
                clean_q = search_query or sq or kw
                clean_kw = clean_scene_search_query(clean_q, max_words=3)
                px_photos = await asyncio.to_thread(media_agent.search_photos, clean_kw, per_page=3, orientation="portrait")
                for p in px_photos:
                    img_url = p.get("src", {}).get("large2x") or p.get("src", {}).get("portrait")
                    if img_url and img_url not in used_pool_urls:
                        out_img = str(media_dir / f"scene_{scene.scene_id:03d}_pexels_photo.jpg")
                        await asyncio.to_thread(media_agent.download_file, img_url, out_img)
                        if Path(out_img).exists() and Path(out_img).stat().st_size > 3000:
                            used_pool_urls.add(img_url)
                            media_path = out_img
                            media_type = "image"
                            logger.info(f"Scene {scene.scene_id}: 📸 Fallback 3 thành công với Pexels Photo: {clean_kw}")
                            break
            except Exception as fb3_e:
                logger.debug(f"Scene {scene.scene_id}: Fallback 3 photo error: {fb3_e}")

        # Fallback 4: Sinh ảnh AI cứu hộ
        if not media_path:
            try:
                ai_img_path = str(media_dir / f"scene_{scene.scene_id:03d}_fb_ai.jpg")
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

        scenes_media.append({
            "scene_id": scene.scene_id,
            "media_path": media_path,
            "media_type": media_type,
            "visual_keyword": scene.visual_keyword,
            "visual_prompt": getattr(scene, 'visual_prompt', None),
            "narration": scene.narration,
            "camera_motion": camera_motion,
            "sfx_cue": sfx_cue,
            "visual_strategy": chosen_strategy,
            "production_method": prod_method,
            "tool": tool,
            "strategy_rationale": rationale,
        })

    return {
        "scenes_media": scenes_media,
        "current_step": "MEDIA_READY",
        "progress_percent": 65,
        "progress_message": f"Đã thu thập trọn bộ media theo chiến lược đạo diễn ({len(scenes_media)} cảnh)",
    }


def node_evaluate_media(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node Visual Critic thẩm định chất lượng KỸ THUẬT hình ảnh (file, resolution, hash)."""
    scenes_media = state.get("scenes_media", [])
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
                    best_v = await asyncio.to_thread(media_agent.search_best_video_clip, kw, None, None, narr)
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
                            duration_seconds=3.5,
                            scene_id=scene_id,
                            visual_prompt=vp,
                            narration=narr,
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
    
    Kiểm tra:
    1. Ảnh có liên quan đến lời thoại (relevance) không?
    2. Nhân vật/sự vật có đúng (entity_accuracy) không?
    3. Bối cảnh lịch sử có phù hợp (historical_fidelity) không?
    4. Chất lượng thị giác (visual_quality) có đạt không?
    
    Khác với VisualCritic (rule-based), node này NHÌN THẬT vào ảnh bằng AI.
    """
    scenes_media = state.get("scenes_media", [])
    primary_entity = state.get("primary_entity")
    job_id = state.get("job_id", 0)

    if job_id:
        try:
            from app.database.models import get_db, VideoJob
            with get_db() as db:
                jb = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if jb:
                    jb.progress_percent = 72
                    jb.progress_message = "🧠 Đang thẩm định tính phù hợp nội dung qua AI Vision..."
        except Exception:
            pass

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
            try:
                from app.database.models import get_db, VideoJob
                with get_db() as db:
                    jb = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                    if jb:
                        fix_idx = failed_ids.index(scene_id) + 1 if scene_id in failed_ids else 1
                        jb.progress_percent = min(85, 72 + int((fix_idx / max(len(failed_ids), 1)) * 14))
                        jb.progress_message = f"🎯 Đang tối ưu lại tư liệu Cảnh {scene_id} ({fix_idx}/{len(failed_ids)})..."
            except Exception:
                pass

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

        # ── HÀNH ĐỘNG 1: Nếu chỉ định pexels_video hoặc cảnh cần chuyển động B-roll ──
        if rec_tool == "pexels_video" or (not rec_tool and item.get("production_method") in ("STOCK_FOOTAGE", "TEXT_TO_VIDEO")):
            try:
                best_v = await asyncio.to_thread(media_agent.search_best_video_clip, query_part or kw, None, None, narr)
                if best_v and best_v.get("url"):
                    cand_u = best_v["url"]
                    out_v = str(media_dir / f"scene_{scene_id:03d}_content_fix_pexels.mp4")
                    await asyncio.to_thread(media_agent.download_file, cand_u, out_v)
                    if Path(out_v).exists() and Path(out_v).stat().st_size > 10000:
                        item["media_path"] = out_v
                        item["media_type"] = "video"
                        replaced = True
                        logger.info(f"Scene {scene_id}: ✅ Thay thế thành công bằng Video Clip Pexels: '{query_part}'")
            except Exception as ex_v:
                logger.debug(f"Scene {scene_id}: Video replacement error: {ex_v}")

        # ── HÀNH ĐỘNG 2: Nếu chỉ định ai_video (Text-to-Video siêu thực) ──
        if not replaced and (rec_tool == "ai_video" or item.get("production_method") == "TEXT_TO_VIDEO"):
            try:
                ai_v_path = str(media_dir / f"scene_{scene_id:03d}_content_fix_t2v.mp4")
                res_v = await ai_video_agent.generate_text_to_video(
                    prompt=prompt_part or query_part,
                    output_path=ai_v_path,
                    duration_seconds=3.5,
                    scene_id=scene_id,
                    visual_prompt=prompt_part,
                    narration=narr,
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
                    duration_seconds=3.5,
                    scene_id=scene_id,
                    visual_prompt=prompt_part,
                    narration=narr,
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
                    image_agent.generate_scene_image,
                    prompt,
                    ai_path,
                    "9:16",
                    "cinematic",
                    scene_id,
                    refined_prompt,
                )
                if res and Path(res).exists() and Path(res).stat().st_size > 2000:
                    item["media_path"] = res
                    item["media_type"] = "image"
                    replaced = True
                    logger.info(f"Scene {scene_id}: ✅ Thay bằng ảnh AI với prompt tinh chỉnh từ AI Vision")
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

