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
from app.graph.evaluators.visual_critic import VisualCritic
from app.graph.evaluators.content_quality_critic import ContentQualityCritic
from app.graph.state import VideoWorkflowState

logger = logging.getLogger(__name__)


async def node_fetch_media(state: VideoWorkflowState) -> Dict[str, Any]:
    """
    Node thu thập tư liệu hình ảnh/video chất lượng cao cho từng phân cảnh.
    
    Quy trình tìm nạp thông minh 4 tầng:
      Tầng 1: Semantic Pool Matcher (Khớp ngữ nghĩa ảnh tài liệu từ entity_media_pool)
      Tầng 2: Multi-Source Scene Search (Wikipedia -> Wikimedia Commons -> Pexels HD Photos -> Openverse)
      Tầng 3: AI Image Generation (Style thích ứng theo từng chủ đề kịch bản)
      Tầng 4: Pexels Video (Chỉ dùng cho phong cảnh/không gian/thành phố trừu tượng)
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

    scenes_media = []
    used_pool_urls = set()

    for scene in script.scenes:
        media_path = ""
        media_type = "image"
        sq = getattr(scene, 'stock_query', None)
        vp = getattr(scene, 'visual_prompt', None)
        kw = scene.visual_keyword or ""

        # ── CẢNH 1 ĐẶC BIỆT: PRIMAL VISUAL HOOK ENGINE (3 GIÂY ĐẦU) ──
        # Nếu không phải nhân vật lịch sử cần ảnh tư liệu gốc, ưu tiên tạo ảnh AI điện ảnh 8K
        # theo đúng visual_prompt do AI Director đo ni đóng giày riêng cho chủ đề này.
        if scene.scene_id == 1 and not primary_entity and vp and len(vp.strip()) > 15:
            try:
                ai_hook_path = str(media_dir / f"scene_{scene.scene_id:03d}_hook_ai.jpg")
                res_img = await asyncio.to_thread(
                    image_agent.generate_scene_image,
                    vp,
                    ai_hook_path,
                    "9:16",
                    "cinematic",
                    scene.scene_id,
                    vp,
                )
                if res_img and Path(res_img).exists() and Path(res_img).stat().st_size > 2000:
                    media_path = ai_hook_path
                    media_type = "image"
                    logger.info(f"Scene 1 (Primal Hook): 🎯 Tạo thành công ảnh AI mở màn gây sốc: {vp[:50]}...")
            except Exception as e:
                logger.warning(f"Scene 1 Hook generation error: {e}")

        # ── TẦNG 1: SEMANTIC POOL MATCHER (DOCUMENTARY MODE) ──
        # Khớp ngữ nghĩa theo năm, sự kiện, từ khóa cụ thể thay vì lấy tuần tự
        if not media_path and primary_entity and entity_media_pool:
            best_pool_photo = match_best_pool_photo(
                scene_narration=scene.narration,
                scene_kw=f"{kw} {sq or ''}",
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
                        logger.info(
                            f"Scene {scene.scene_id}: ✅ Khớp ngữ nghĩa ảnh tài liệu: "
                            f"{best_pool_photo.get('title', '')[:50]}"
                        )
                except Exception as ex:
                    logger.debug(f"Scene {scene.scene_id}: Pool photo download error: {ex}")

        # ── TẦNG 2: MULTI-SOURCE SCENE SEARCH (Wikipedia, Wikimedia, Pexels Photos, Openverse) ──
        if not media_path:
            try:
                candidate_media = await asyncio.to_thread(
                    media_agent.search_scene_media_multi_source,
                    scene_narration=scene.narration,
                    visual_keyword=kw,
                    stock_query=sq,
                    visual_prompt=vp,
                    primary_entity=primary_entity,
                    entity_type=entity_type,
                    used_urls=used_pool_urls,
                    scene_id=scene.scene_id,
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
                        logger.info(
                            f"Scene {scene.scene_id}: ✅ Tải thành công từ {c_src} "
                            f"({candidate_media.get('title', '')[:45]})"
                        )
            except Exception as e:
                logger.debug(f"Scene {scene.scene_id}: Multi-source search error: {e}")

        # ── TẦNG 3: AI IMAGE GENERATION (Thích ứng theo chủ đề, tỷ lệ dọc 9:16) ──
        if not media_path:
            try:
                ai_img_path = str(media_dir / f"scene_{scene.scene_id:03d}_ai.jpg")
                clean_kw = clean_scene_search_query(kw, max_words=4)
                prompt_to_use = vp if (vp and len(vp.strip()) > 10) else (clean_kw or "cinematic documentary")
                if primary_entity and not vp:
                    prompt_to_use = f"Historical authentic documentary portrait of {primary_entity}, {clean_kw}, 8k"

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
                    logger.info(f"Scene {scene.scene_id}: ✅ Sinh ảnh AI chất lượng cao thành công")
            except Exception as e:
                logger.warning(f"Scene {scene.scene_id}: AI Image error: {e}")

        # ── TẦNG 4: FALLBACK GRAPHIC ──
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
            "narration": scene.narration
        })

    return {
        "scenes_media": scenes_media,
        "current_step": "MEDIA_READY",
        "progress_percent": 65,
        "progress_message": f"Đã thu thập trọn bộ media ({len(scenes_media)} cảnh)",
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

