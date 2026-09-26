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

        # Lấy chiến lược và phương pháp do Video Strategist chỉ định
        sc_strat = strategy_map.get(scene.scene_id, {})
        chosen_strategy = sc_strat.get("strategy", "STOCK_VIDEO")
        prod_method = sc_strat.get("production_method") or (
            "IMAGE_TO_VIDEO" if chosen_strategy == "AI_IMAGE"
            else ("TEXT_TO_VIDEO" if chosen_strategy == "AI_VIDEO"
            else ("REAL_ARCHIVE" if chosen_strategy == "REAL_DOCUMENTARY" else "STOCK_FOOTAGE"))
        )
        tool = sc_strat.get("tool", "")
        rationale = sc_strat.get("rationale", "")
        search_query = sc_strat.get("search_query") or sq or kw
        ai_prompt = sc_strat.get("ai_prompt") or vp or kw
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
                )
                if res_vid and Path(res_vid).exists() and Path(res_vid).stat().st_size > 5000:
                    media_path = res_vid
                    media_type = "video"
                    logger.info(f"Scene {scene.scene_id}: 🎨 [Image-to-Video] Tạo video động từ ảnh chủ đề 8K ({dur:.1f}s)")
            except Exception as e:
                logger.warning(f"Scene {scene.scene_id}: Image-to-Video error: {e}")

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
                )
                if res_vid and Path(res_vid).exists() and Path(res_vid).stat().st_size > 5000:
                    media_path = res_vid
                    media_type = "video"
                    logger.info(f"Scene {scene.scene_id}: 🔮 [Text-to-Video] Tạo video AI trực tiếp kỳ ảo ({dur:.1f}s)")
            except Exception as e:
                logger.warning(f"Scene {scene.scene_id}: Text-to-Video error: {e}")

        # ══════════════════════════════════════════════════════════════════════
        # PHƯƠNG PHÁP 4: STOCK_FOOTAGE (Video B-Roll chuyển động thực tế 9:16)
        # ══════════════════════════════════════════════════════════════════════
        elif prod_method == "STOCK_FOOTAGE" or chosen_strategy == "STOCK_VIDEO":
            try:
                best_vid = await asyncio.to_thread(media_agent.search_best_video_clip, search_query)
                if not best_vid and sq:
                    best_vid = await asyncio.to_thread(media_agent.search_best_video_clip, sq)
                if not best_vid:
                    clean_kw = clean_scene_search_query(kw, max_words=2)
                    best_vid = await asyncio.to_thread(media_agent.search_best_video_clip, clean_kw)

                if best_vid and best_vid.get("url") and best_vid["url"] not in used_pool_urls:
                    cand_url = best_vid["url"]
                    vid_out = str(media_dir / f"scene_{scene.scene_id:03d}_pexels.mp4")
                    await asyncio.to_thread(media_agent.download_file, cand_url, vid_out)
                    if Path(vid_out).exists() and Path(vid_out).stat().st_size > 10000:
                        used_pool_urls.add(cand_url)
                        media_path = vid_out
                        media_type = "video"
                        logger.info(f"Scene {scene.scene_id}: 🎥 [Video B-Roll] Tải thành công video Pexels '{search_query}'")
            except Exception as e:
                logger.debug(f"Scene {scene.scene_id}: Pexels video search error: {e}")

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

            # Fallback 1: Thử tìm ảnh Pexels HD thực tế
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
                    prefer_video=(chosen_strategy in ("STOCK_VIDEO", "AI_VIDEO")),
                    prefer_photo=(chosen_strategy == "REAL_DOCUMENTARY"),
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

        # Fallback 2: Sinh ảnh AI cứu hộ
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

        # Fallback 3: Graphic placeholder
        if not media_path:
            out_g = str(media_dir / f"scene_{scene.scene_id:03d}_graphic.png")
            from app.utils.ffmpeg_helper import create_placeholder_image
            create_placeholder_image(scene.visual_keyword, out_g)
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
    """Node tự động sửa chữa các cảnh bị đánh giá FAIL kỹ thuật (thay thế ảnh mới hoặc sinh ảnh AI)."""
    scenes_media = list(state.get("scenes_media", []))
    media_eval = state.get("media_eval", {})
    failed_ids = media_eval.get("failed_scene_ids", [])
    primary_entity = state.get("primary_entity")
    work_dir = Path(state.get("work_dir", "storage/cache"))
    media_dir = work_dir / "media"
    retry_count = state.get("media_retry_count", 0) + 1

    image_agent = ImageAgent()
    logger.info(f"🔄 [LangGraph] Tự động sửa chữa media kỹ thuật cho các cảnh: {failed_ids} (Lần {retry_count})")

    for item in scenes_media:
        if item.get("scene_id") in failed_ids:
            scene_id = item["scene_id"]
            kw = item.get("visual_keyword", "")
            narr = item.get("narration", "")
            
            # Sinh ảnh AI điện ảnh chất lượng cao — truyền cả visual_prompt nếu có
            ai_img_path = str(media_dir / f"scene_{scene_id:03d}_refined.jpg")
            vp = item.get("visual_prompt", None)
            kw = item.get("visual_keyword", "")
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

    critic = ContentQualityCritic()
    report = critic.evaluate_content_quality(
        scenes_media=scenes_media,
        primary_entity=primary_entity,
        max_ai_checks=6,
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
    Node tự động thay thế ảnh có NỘI DUNG kém — dựa trên gợi ý AI Vision.
    
    Khác với node_refine_media (sửa file hỏng kỹ thuật):
    - Sử dụng replacement_suggestion từ ContentQualityCritic để tìm ảnh thay thế phù hợp hơn.
    - Ưu tiên tìm ảnh tư liệu thực tế trước, sau đó mới dùng AI Image Generation.
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
    
    logger.info(
        f"🔄 [ContentRefine] Tự sửa nội dung cho {len(failed_ids)} cảnh "
        f"(Lần {content_retry}) dựa trên gợi ý AI Vision"
    )

    for item in scenes_media:
        scene_id = item.get("scene_id")
        if scene_id not in failed_ids:
            continue

        kw = item.get("visual_keyword", "")
        narr = item.get("narration", "")
        suggestion = suggestions.get(scene_id, kw)
        replaced = False

        # ── Nếu là Scene 1 hoặc suggestion là prompt miêu tả điện ảnh dài (> 5 từ):
        # Trực tiếp sinh ảnh AI (vì stock search không thể tìm theo prompt miêu tả điện ảnh chi tiết)
        if scene_id == 1 or len(suggestion.split()) > 5:
            try:
                ai_path = str(media_dir / f"scene_{scene_id:03d}_content_ai.jpg")
                refined_prompt = suggestion or kw
                res = await asyncio.to_thread(
                    image_agent.generate_scene_image,
                    refined_prompt,
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
                    logger.info(f"Scene {scene_id}: ✅ Tái tạo thành công ảnh AI Primal Hook chuẩn xác từ gợi ý AI Vision")
            except Exception as e:
                logger.warning(f"Scene {scene_id}: Lỗi tạo ảnh AI từ gợi ý: {e}")

        # ── Chiến lược 1: Multi-Source Scene Search với từ khóa ngắn gọn tinh chỉnh từ AI Vision ──
        if not replaced:
            try:
                cand = await asyncio.to_thread(
                    media_agent.search_scene_media_multi_source,
                    scene_narration=narr,
                    visual_keyword=suggestion or kw,
                    stock_query=suggestion if len(suggestion.split()) <= 4 else kw,
                    primary_entity=primary_entity,
                    scene_id=scene_id,
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
                        logger.info(f"Scene {scene_id}: ✅ Thay thế thành công từ {c_src} theo gợi ý AI Vision")
            except Exception as e:
                logger.debug(f"Scene {scene_id}: Multi-source content fix failed: {e}")

        # ── Chiến lược 2: AI Image Generation với prompt tinh chỉnh từ gợi ý ──
        if not replaced:
            try:
                ai_path = str(media_dir / f"scene_{scene_id:03d}_content_ai.jpg")
                refined_prompt = suggestion or kw
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
            logger.info(f"  📝 Gợi ý AI Vision đã áp dụng: '{suggestion}'")
        else:
            logger.warning(f"  ❌ Scene {scene_id}: Không thể tìm ảnh thay thế phù hợp")

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

