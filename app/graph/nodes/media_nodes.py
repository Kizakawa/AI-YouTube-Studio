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
from app.agents.media_agent import MediaAgent, detect_real_entity
from app.agents.image_agent import ImageAgent
from app.graph.evaluators.visual_critic import VisualCritic
from app.graph.evaluators.content_quality_critic import ContentQualityCritic
from app.graph.state import VideoWorkflowState

logger = logging.getLogger(__name__)


async def node_fetch_media(state: VideoWorkflowState) -> Dict[str, Any]:
    """Node thu thập tư liệu hình ảnh/video chất lượng cao cho từng phân cảnh."""
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

        # Tầng 1: Documentary Pool (Ưu tiên tuyệt đối cho nhân vật thực tế)
        if primary_entity and entity_media_pool:
            for cand_photo in entity_media_pool:
                cand_url = cand_photo.get("url")
                if not cand_url or cand_url in used_pool_urls:
                    continue
                try:
                    ext = ".jpg" if any(x in cand_url.lower() for x in ["jpg", "jpeg"]) else ".png"
                    output = str(media_dir / f"scene_{scene.scene_id:03d}_doc{ext}")
                    await asyncio.to_thread(media_agent.download_file, cand_url, output)
                    if Path(output).exists() and Path(output).stat().st_size > 3000:
                        used_pool_urls.add(cand_url)
                        media_path = output
                        media_type = "image"
                        break
                except Exception as ex:
                    logger.debug(f"Scene {scene.scene_id}: Pool download error: {ex}")

        # Tầng 2: Wikipedia chân dung hoặc Wikimedia
        if not media_path and primary_entity:
            try:
                wiki_img = await asyncio.to_thread(media_agent.search_wikipedia_entity_image, primary_entity)
                if wiki_img and wiki_img.get("url") and wiki_img["url"] not in used_pool_urls:
                    url = wiki_img["url"]
                    ext = ".jpg" if any(x in url.lower() for x in ["jpg", "jpeg"]) else ".png"
                    output = str(media_dir / f"scene_{scene.scene_id:03d}_wiki{ext}")
                    await asyncio.to_thread(media_agent.download_file, url, output)
                    if Path(output).exists() and Path(output).stat().st_size > 3000:
                        used_pool_urls.add(url)
                        media_path = output
                        media_type = "image"
            except Exception:
                pass

        # Tầng 2.5: Pexels Vertical HD Video Clip — dùng stock_query thay visual_keyword để tránh sai phong cách
        if not media_path and media_agent.api_key:
            try:
                # Ưu tiên stock_query (2-3 từ thuần topic) > visual_keyword thô
                raw_q = getattr(scene, 'stock_query', None) or scene.visual_keyword or ""
                # Lọc bỏ từ nguy hiểm: tên đạo diễn, tên phim, diễn viên
                _FORBIDDEN = {
                    "nolan", "interstellar", "inception", "avatar", "hollywood",
                    "cinematic", "movie", "film", "hathaway", "mcconaughey",
                    "director", "actor", "actress", "red carpet", "rollercoaster",
                    "cartoon", "anime", "animated",
                }
                clean_words = [w for w in raw_q.lower().split() if w not in _FORBIDDEN]
                safe_q = " ".join(clean_words[:3]).strip() if clean_words else "space astronomy"
                # Thêm context nếu chủ đề space
                space_topics = ["black hole", "galaxy", "nebula", "star", "space", "universe", "supernova"]
                if any(t in safe_q.lower() for t in space_topics) and "astronomy" not in safe_q:
                    safe_q = f"{safe_q} astronomy"

                best_vid = await asyncio.to_thread(media_agent.search_best_video_clip, safe_q)
                if best_vid and best_vid.get("url"):
                    vid_url = best_vid["url"]
                    # DEDUP: không dùng lại cùng URL cho scene khác
                    if vid_url not in used_pool_urls:
                        out_v = str(media_dir / f"scene_{scene.scene_id:03d}_pexels.mp4")
                        await asyncio.to_thread(media_agent.download_file, vid_url, out_v)
                        if Path(out_v).exists() and Path(out_v).stat().st_size > 10000:
                            used_pool_urls.add(vid_url)
                            media_path = out_v
                            media_type = "video"
                    else:
                        logger.debug(f"Scene {scene.scene_id}: Bỏ qua Pexels video trùng URL")
            except Exception as e:
                logger.debug(f"Scene {scene.scene_id}: Video clip search error: {e}")

        # Tầng 3: AI Image Generation — ưu tiên visual_prompt (Director description) hơn visual_keyword
        if not media_path:
            try:
                ai_img_path = str(media_dir / f"scene_{scene.scene_id:03d}_ai.jpg")
                # visual_prompt: mô tả cảnh quay chi tiết từ Director (không bị ảnh hưởng bởi ẩn dụ)
                # visual_keyword: tag ngắn fallback
                vp = getattr(scene, 'visual_prompt', None)
                kw = scene.visual_keyword
                if primary_entity and not vp:
                    kw = f"Historical authentic photorealistic portrait of {primary_entity}, {kw}, period accurate 8k"
                res_img = await asyncio.to_thread(
                    image_agent.generate_scene_image,
                    kw,
                    ai_img_path,
                    "9:16",
                    "cinematic",
                    scene.scene_id,
                    vp,
                )
                if res_img and Path(res_img).exists() and Path(res_img).stat().st_size > 2000:
                    media_path = res_img
                    media_type = "image"
            except Exception as e:
                logger.warning(f"Scene {scene.scene_id}: AI Image error: {e}")

        # Tầng 4: Openverse Free Archival Search (Dự phòng ảnh lưu trữ tự do)
        if not media_path:
            try:
                ov_items = await asyncio.to_thread(media_agent.search_openverse, scene.visual_keyword, 2)
                if ov_items:
                    ov_url = ov_items[0].get("url")
                    if ov_url and ov_url not in used_pool_urls:
                        ext = ".jpg" if "jpg" in ov_url.lower() else ".png"
                        out_ov = str(media_dir / f"scene_{scene.scene_id:03d}_openverse{ext}")
                        await asyncio.to_thread(media_agent.download_file, ov_url, out_ov)
                        if Path(out_ov).exists() and Path(out_ov).stat().st_size > 3000:
                            used_pool_urls.add(ov_url)
                            media_path = out_ov
                            media_type = "image"
            except Exception as e:
                logger.debug(f"Scene {scene.scene_id}: Openverse fallback error: {e}")

        # Fallback an toàn: Gradient graphic
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

        # Chiến lược 1: Tìm ảnh Wikipedia/Wikimedia với từ khoá gợi ý
        if primary_entity and not replaced:
            try:
                search_query = f"{primary_entity} {suggestion}" if suggestion else primary_entity
                wiki_img = await asyncio.to_thread(
                    media_agent.search_wikipedia_entity_image, search_query
                )
                if wiki_img and wiki_img.get("url"):
                    url = wiki_img["url"]
                    ext = ".jpg" if "jpg" in url.lower() else ".png"
                    out = str(media_dir / f"scene_{scene_id:03d}_content_fix{ext}")
                    await asyncio.to_thread(media_agent.download_file, url, out)
                    if Path(out).exists() and Path(out).stat().st_size > 3000:
                        item["media_path"] = out
                        item["media_type"] = "image"
                        replaced = True
                        logger.info(f"Scene {scene_id}: ✅ Thay bằng ảnh tư liệu Wikipedia")
            except Exception as e:
                logger.debug(f"Scene {scene_id}: Wiki search for content fix failed: {e}")

        # Chiến lược 2: Openverse archival search với gợi ý
        if not replaced:
            try:
                search_q = suggestion if suggestion else kw
                ov_items = await asyncio.to_thread(media_agent.search_openverse, search_q, 3)
                if ov_items:
                    ov_url = ov_items[0].get("url")
                    ext = ".jpg" if "jpg" in ov_url.lower() else ".png"
                    out = str(media_dir / f"scene_{scene_id:03d}_content_ov{ext}")
                    await asyncio.to_thread(media_agent.download_file, ov_url, out)
                    if Path(out).exists() and Path(out).stat().st_size > 3000:
                        item["media_path"] = out
                        item["media_type"] = "image"
                        replaced = True
                        logger.info(f"Scene {scene_id}: ✅ Thay bằng ảnh Openverse archival")
            except Exception:
                pass

        # Chiến lược 3: AI Image Generation với prompt tinh chỉnh từ gợi ý
        if not replaced:
            try:
                ai_path = str(media_dir / f"scene_{scene_id:03d}_content_ai.jpg")
                if primary_entity:
                    prompt = (
                        f"Historically accurate dramatic portrait of {primary_entity}, "
                        f"{suggestion or kw}, period-correct clothing and setting, "
                        f"8k cinematic photorealistic, sharp focus, documentary style"
                    )
                else:
                    prompt = (
                        f"Dramatic cinematic photorealistic: {suggestion or kw}, "
                        f"8k sharp focus, professional documentary photography"
                    )
                res = await asyncio.to_thread(image_agent.generate_scene_image, prompt, ai_path)
                if res and Path(res).exists() and Path(res).stat().st_size > 2000:
                    item["media_path"] = res
                    item["media_type"] = "image"
                    replaced = True
                    logger.info(f"Scene {scene_id}: ✅ Thay bằng ảnh AI với prompt tinh chỉnh")
            except Exception as e:
                logger.warning(f"Scene {scene_id}: Content refine AI generation error: {e}")

        if replaced:
            logger.info(f"  📝 Gợi ý AI đã dùng: '{suggestion}'")
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

