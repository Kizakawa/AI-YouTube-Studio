"""
VideoPipeline — Orchestrator trung tâm điều phối luồng sản xuất video.

Luồng chính:
  A (Script) → B (Per-Scene Audio) → C (Per-Scene Media) → D (FFmpeg Render + QA)

Mỗi bước ghi checkpoint vào DB. Nếu crash → resume từ checkpoint cuối.
"""
import asyncio
import logging
import shutil
from pathlib import Path
from typing import Optional, List, Tuple, Callable, Awaitable, Dict, Set

from app.schemas.contracts import (
    JobStatus, ScriptContract, SceneContract, RenderJobContract
)
from app.database.models import get_db, VideoJob
from app.agents.voice_agent import VoiceAgent
from app.agents.media_agent import (
    MediaAgent,
    detect_real_entity,
    detect_video_primary_entity,
    match_best_pool_photo,
    clean_scene_search_query,
)
from app.agents.image_agent import ImageAgent
from app.utils.subtitle_helper import SubtitleHelper
from config.settings import settings

logger = logging.getLogger(__name__)


class VideoPipeline:
    """
    Orchestrator cho 1 VideoJob.

    Usage:
        pipeline = VideoPipeline(job_id=42, progress_callback=my_async_callback)
        result = await pipeline.execute()
    """

    def __init__(
        self,
        job_id: int,
        progress_callback: Optional[Callable[[str, str], Awaitable[None]]] = None
    ):
        self.job_id = job_id
        self.progress_callback = progress_callback
        self.work_dir: Path = Path(settings.TEMP_DIR) / f"job_{self.job_id}"

        # Agents (lazy init)
        self._voice_agent = VoiceAgent()
        self._media_agent = MediaAgent()
        self._image_agent = ImageAgent()

    async def _report_progress(self, stage: str, message: str, percent: int = 0):
        """Báo cáo tiến độ thời gian thực về Telegram/Web Dashboard kèm phần trăm %."""
        logger.info(f"[Job {self.job_id}] [{stage}] [{percent}%] {message}")

        # Đồng bộ tiến độ vào Database để Web Dashboard hiển thị thanh % thời gian thực
        try:
            with get_db() as db:
                job = db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
                if job:
                    job.progress_percent = percent
                    job.progress_message = message
        except Exception as ex:
            logger.debug(f"Không thể ghi nhận progress vào DB: {ex}")

        if self.progress_callback:
            try:
                import inspect
                sig = inspect.signature(self.progress_callback)
                if len(sig.parameters) >= 3:
                    await self.progress_callback(stage, message, percent)
                else:
                    await self.progress_callback(stage, message)
            except Exception as ex:
                logger.warning(f"Error in progress_callback: {ex}")

    # ──────────────────────────────────────────
    # MAIN ENTRY POINT
    # ──────────────────────────────────────────

    async def execute(self) -> RenderJobContract:
        """
        Chạy toàn bộ pipeline A → B → C → D.
        Trả về RenderJobContract nếu thành công.
        Raise exception + set status FAILED nếu thất bại.
        """
        try:
            # Tạo thư mục làm việc riêng cho job
            self.work_dir = Path(settings.TEMP_DIR) / f"job_{self.job_id}"
            self.work_dir.mkdir(parents=True, exist_ok=True)

            # ── [NEW] Ưu tiên điều phối qua LangGraph Multi-Agent Engine với 5 Bộ Graders ──
            if getattr(settings, 'USE_LANGGRAPH', True):
                try:
                    from app.graph.runner import GraphRunner
                    logger.info(f"[Job {self.job_id}] 🌟 Kích hoạt LangGraph Multi-Agent Engine với 5 Bộ Graders (bao gồm AI Vision ContentCritic) & Self-Correction...")
                    runner = GraphRunner(job_id=self.job_id, progress_callback=self._report_progress)
                    state = await runner.run()

                    script_dict = state.get("script_data", {})
                    title = script_dict.get("title", f"Video #{self.job_id}")
                    video_p = state.get("rendered_video_path", "")
                    audio_p = state.get("full_audio_path", "")
                    dur_ms = state.get("total_audio_duration_ms", 0)
                    sub_p = state.get("subtitle_path", "")

                    scenes_list = []
                    for s in state.get("scenes_media", []):
                        scenes_list.append(SceneContract(
                            scene_id=s["scene_id"],
                            narration=s.get("narration", ""),
                            visual_keyword=s.get("visual_keyword", ""),
                            media_path=s.get("media_path", ""),
                            media_type=s.get("media_type", "image"),
                        ))

                    render_job = RenderJobContract(
                        job_id=self.job_id,
                        title=title,
                        scenes=scenes_list,
                        full_audio_path=audio_p,
                        total_duration_ms=dur_ms,
                        subtitle_path=sub_p,
                        output_path=video_p,
                    )
                    self._save_render_manifest(render_job)
                    self._update_status(JobStatus.RENDERED)
                    return render_job
                except Exception as e:
                    logger.warning(f"[Job {self.job_id}] LangGraph engine gặp sự cố ({e}), chuyển sang pipeline tuyến tính dự phòng...")

            # ── Step A: Load script từ DB ──
            script = self._load_script_from_db()
            await self._report_progress(
                "SCRIPT",
                f"Đã nạp kịch bản: \"{script.title}\" ({len(script.scenes)} cảnh)",
                percent=5
            )

            # ── Step B: Generate per-scene audio & sync subtitles ──
            self._update_status(JobStatus.AUDIO_READY, "Đang tạo giọng đọc Edge-TTS...")
            await self._report_progress("AUDIO", "🎙️ Đang tạo giọng đọc AI Edge-TTS và đồng bộ phụ đề...", percent=15)
            enriched_scenes = await self._step_audio(script)
            full_audio_path, total_duration = await self._step_concat_audio(enriched_scenes)

            # Tạo file phụ đề ASS Karaoke (word-level highlight)
            subtitle_path = str(self.work_dir / "subtitles.ass")
            SubtitleHelper.save_ass_file(enriched_scenes, subtitle_path)

            self._save_audio_metadata(full_audio_path, total_duration, enriched_scenes)
            logger.info(f"[Job {self.job_id}] Step B: Audio done — {total_duration}ms total")

            # ── Step C: Fetch per-scene media ──
            self._update_status(JobStatus.MEDIA_READY, "Đang tải media cho từng cảnh...")
            await self._report_progress("MEDIA", "🖼️ Đang tìm kiếm và chuẩn bị hình ảnh/video 9:16 cho từng cảnh...", percent=25)
            media_scenes = await self._step_media(enriched_scenes)
            logger.info(f"[Job {self.job_id}] Step C: Media done — {len(media_scenes)} scenes")

            # ── Step D: Render final MP4 ──
            self._update_status(JobStatus.RENDERING, "Đang render video...")
            await self._report_progress("RENDER", "🎬 Đang dựng video 9:16 Ken Burns, ghép âm thanh & burn phụ đề hardsub...", percent=65)
            output_path = str(Path(settings.OUTPUT_DIR) / f"video_{self.job_id}.mp4")
            Path(settings.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

            render_job = RenderJobContract(
                job_id=self.job_id,
                title=script.title,
                scenes=media_scenes,
                full_audio_path=full_audio_path,
                total_audio_duration_ms=total_duration,
                subtitle_path=subtitle_path,
                bgm_path=self._find_bgm(),
                sfx_dir="assets/sfx",  # Thư mục SFX cho chuyển cảnh (whoosh, hit...)
                output_path=output_path,
            )

            # Gọi VideoAgent render
            await self._step_render(render_job)

            # Step D QA: Validate output bằng ffprobe
            await self._report_progress("QA", "🔍 Đang kiểm định chất lượng video bằng ffprobe...", percent=95)
            self._step_qa(output_path)

            self._update_status(JobStatus.RENDERED)
            self._save_render_manifest(render_job)
            await self._report_progress("COMPLETED", f"🎉 Video #{self.job_id} đã hoàn tất và sẵn sàng phát hành!", percent=100)
            logger.info(f"[Job {self.job_id}] Pipeline complete → {output_path}")
            return render_job

        except Exception as e:
            logger.error(f"[Job {self.job_id}] Pipeline FAILED: {e}", exc_info=True)
            self._update_status(JobStatus.FAILED, error_msg=str(e))
            await self._report_progress("ERROR", f"❌ Quá trình dựng video thất bại: {e}")
            raise

    # ──────────────────────────────────────────
    # STEP IMPLEMENTATIONS
    # ──────────────────────────────────────────

    def _load_script_from_db(self) -> ScriptContract:
        """Step A: Đọc scenes_json từ VideoJob, parse thành ScriptContract."""
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
            if not job:
                raise ValueError(f"VideoJob #{self.job_id} không tồn tại")
            if not job.scenes_json:
                raise ValueError(f"VideoJob #{self.job_id} chưa có scenes_json (status={job.status})")
            return ScriptContract.from_json_str(job.scenes_json)

    async def _step_audio(self, script: ScriptContract) -> List[SceneContract]:
        """Step B: Sinh MP3 per-scene, enrich SceneContract với audio_path + duration."""
        audio_dir = str(self.work_dir / "audio")
        Path(audio_dir).mkdir(exist_ok=True)

        # Lấy voice từ channel config (nếu có)
        voice = self._get_channel_voice()

        return await self._voice_agent.generate_per_scene_audio(
            scenes=script.scenes,
            output_dir=audio_dir,
            voice=voice,
        )

    async def _step_concat_audio(self, scenes: List[SceneContract]) -> Tuple[str, int]:
        """Ghép tất cả scene audio → 1 file full_narration.mp3."""
        full_audio_path = str(self.work_dir / "full_narration.mp3")
        total_ms = await self._voice_agent.concat_scene_audio(scenes, full_audio_path)
        return full_audio_path, total_ms

    async def _step_media(self, scenes: List[SceneContract]) -> List[SceneContract]:
        """Step C: Tải video/ảnh nền cho từng scene kèm báo cáo tiến độ %."""
        import json
        media_dir = str(self.work_dir / "media")
        Path(media_dir).mkdir(exist_ok=True)

        # ── PHÁT HIỆN THỰC THỂ CHÍNH CỦA TOÀN BỘ VIDEO (DOCUMENTARY MODE) ──
        primary_entity = None
        entity_type = "person"
        entity_media_pool = []
        used_pool_urls = set()
        used_asset_paths: set = set()  # ← DEDUP CROSS-SCENE: track URLs đã assign, cấm tái sử dụng

        try:
            with get_db() as db:
                job = db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
                if job:
                    script_obj = None
                    if job.scenes_json:
                        try:
                            script_obj = ScriptContract.from_json_str(job.scenes_json)
                            if getattr(script_obj, 'primary_entity', None):
                                primary_entity = script_obj.primary_entity
                        except Exception:
                            pass

                    if not primary_entity:
                        tags_list = []
                        if job.tags:
                            try:
                                tags_list = json.loads(job.tags) if job.tags.startswith("[") else [t.strip() for t in job.tags.split(",")]
                            except Exception:
                                tags_list = [t.strip() for t in job.tags.split(",")]

                        full_narr = script_obj.full_narration if script_obj else (job.script or "")
                        ent_name, ent_type = detect_video_primary_entity(
                            topic=job.topic or "",
                            title=job.title or "",
                            narration=full_narr,
                            tags=tags_list
                        )
                        if ent_name:
                            primary_entity = ent_name
                            entity_type = ent_type
        except Exception as e:
            logger.warning(f"[Job {self.job_id}] Lỗi khi nhận diện primary_entity: {e}")

        if primary_entity:
            logger.info(
                f"[Job {self.job_id}] 🌟 KÍCH HOẠT CHẾ ĐỘ PHIM TÀI LIỆU (DOCUMENTARY MODE) CHO: '{primary_entity}' ({entity_type})"
            )
            await self._report_progress(
                "MEDIA",
                f"🏛️ Đang thu thập trọn bộ ảnh tài liệu chuẩn HD cho {primary_entity}...",
                percent=26
            )
            entity_media_pool = await asyncio.to_thread(
                self._media_agent.fetch_entity_media_pool,
                primary_entity,
                entity_type,
                max(15, len(scenes) * 2)
            )
            logger.info(f"[Job {self.job_id}] Đã lấy được {len(entity_media_pool)} ảnh tài liệu cho '{primary_entity}'")

        total_scenes = len(scenes)
        result = []
        for idx, scene in enumerate(scenes):
            pct = 25 + int(((idx + 1) / total_scenes) * 35)
            kw = scene.visual_keyword or "Stock 4K"
            label = f"{primary_entity} - {kw[:20]}" if primary_entity else kw[:25]
            await self._report_progress(
                "MEDIA",
                f"🖼️ Chuẩn bị media cảnh {idx+1}/{total_scenes} ({label})...",
                percent=pct
            )
            media_path = await self._fetch_scene_media(
                scene=scene,
                media_dir=media_dir,
                primary_entity=primary_entity,
                entity_type=entity_type,
                entity_media_pool=entity_media_pool,
                used_pool_urls=used_pool_urls,
                used_asset_paths=used_asset_paths,
            )
            enriched = scene.model_copy(update={
                "media_path": media_path,
                "media_type": "video" if media_path.endswith(".mp4") else "image",
            })
            result.append(enriched)
        return result

    @staticmethod
    def _safe_pexels_query(scene, topic_context: str = "space astronomy") -> str:
        """
        Tạo Pexels search query an toàn:
        - Ư u tiên stock_query (2-3 từ thuần chủ đề)
        - Tự động thêm context suffix nếu tô chủ đề vũ trụ/khoa học
        - Bộ lọc: Loại bỏ từng nhà làm phim, tên diễn viên, tên phim nếu lọt vào query
        """
        # Từ khóa tốt nhất: stock_query > visual_keyword
        raw = getattr(scene, 'stock_query', None) or scene.visual_keyword or ""

        # Bộ lọc keyword nguy hiểm (gây ra kết quả sai phong cách trên Pexels)
        _FORBIDDEN_PEXELS_WORDS = {
            "nolan", "interstellar", "inception", "avatar", "hollywood",
            "cinematic", "movie", "film", "hathaway", "mcconaughey",
            "director", "actor", "actress", "red carpet", "award",
            "interview", "premiere", "promotional",
            "rollercoaster", "thrill", "amusement", "park",
            "cartoon", "anime", "animated", "2d animation",
        }
        words = raw.lower().split()
        filtered = [w for w in words if w not in _FORBIDDEN_PEXELS_WORDS]
        clean = " ".join(filtered).strip() if filtered else "space astronomy"

        # Giới hạn 3 từ (2-3 là tốt nhất cho Pexels)
        clean_words = clean.split()
        if len(clean_words) > 3:
            clean = " ".join(clean_words[:3])

        # Thêm context suffix nếu quá ngắn hoặc chủ đề space/science
        space_topics = {"black hole", "galaxy", "nebula", "star", "space", "universe",
                        "cosmos", "astronaut", "neutron", "supernova", "planet", "asteroid"}
        needs_context = any(t in clean.lower() for t in space_topics) or len(clean.split()) < 2
        if needs_context and topic_context not in clean:
            clean = f"{clean} {topic_context}"

        return clean.strip()

    async def _fetch_scene_media(
        self,
        scene: SceneContract,
        media_dir: str,
        primary_entity: Optional[str] = None,
        entity_type: str = "person",
        entity_media_pool: Optional[List[Dict]] = None,
        used_pool_urls: Optional[set] = None,
        used_asset_paths: Optional[set] = None,
    ) -> str:
        """
        Tìm nạp media cho 1 scene với hệ thống phân cấp 7 tầng thông minh:
          Tầng 0: User Custom Media (Ảnh/Video do người dùng gửi trực tiếp qua Telegram)
          Tầng 1: Documentary Entity Pool (Wikipedia + Wikimedia Commons — ƯU TIÊN cho nhân vật thực tế)
          Tầng 2: Smart Entity Search cụ thể cho phân cảnh
          Tầng 3: AI Image Generation (Google Imagen 3 / Pollinations 9:16 gắn ngữ cảnh nhân vật)
          Tầng 4: Pexels Video/Photo portrait 9:16 (Chỉ dùng cho chủ đề trừu tượng / b-roll)
          Tầng 5: Web Image Search tổng quát
          Tầng 6: Dynamic Aesthetic Graphic (Fallback an toàn tuyệt đối)
        """
        import asyncio

        # ── Checkpoint: Tái sử dụng media hợp lệ đã tải từ lần chạy trước ──
        # NẾU ĐANG Ở DOCUMENTARY MODE: Tuyệt đối không tái sử dụng video stock cũ (.mp4 từ Pexels) vì đó là diễn viên hiện đại!
        if not primary_entity:
            for ext in [".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp"]:
                candidate = Path(media_dir) / f"scene_{scene.scene_id:03d}{ext}"
                if candidate.exists() and candidate.stat().st_size > 10240:
                    logger.info(f"Scene {scene.scene_id}: Tái sử dụng media từ checkpoint ({candidate.name})")
                    return str(candidate)

        # Kiểm tra checkpoint các file ảnh tài liệu hợp lệ
        for suffix in ["_doc", "_wiki", "_real", "_web", "_google", "_ai"]:
            for ext in [".jpg", ".jpeg", ".png", ".webp"]:
                candidate = Path(media_dir) / f"scene_{scene.scene_id:03d}{suffix}{ext}"
                if candidate.exists() and candidate.stat().st_size > 3000 and self._is_valid_image_file(str(candidate)):
                    logger.info(f"Scene {scene.scene_id}: Tái sử dụng ảnh tài liệu từ checkpoint ({candidate.name})")
                    return str(candidate)

        # ── Tầng 0: User Custom Media (Người dùng gửi qua Telegram) ──
        custom_dir = Path("storage") / "jobs" / str(self.job_id) / "custom_media"
        if custom_dir.exists():
            for ext in [".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp"]:
                candidate1 = custom_dir / f"scene_{scene.scene_id:03d}{ext}"
                candidate2 = custom_dir / f"scene_{scene.scene_id}{ext}"
                for cand in [candidate1, candidate2]:
                    if cand.exists() and cand.stat().st_size > 0:
                        dest = Path(media_dir) / f"scene_{scene.scene_id:03d}{ext}"
                        import shutil
                        shutil.copy2(str(cand), str(dest))
                        logger.info(f"Scene {scene.scene_id}: Dùng media tùy biến của người dùng ({cand.name})")
                        return str(dest)

        # ── TẦNG 1: SEMANTIC POOL MATCH (DOCUMENTARY MODE) ──
        if primary_entity and entity_media_pool:
            best_pool_photo = match_best_pool_photo(
                scene_narration=scene.narration,
                scene_kw=f"{scene.visual_keyword or ''} {getattr(scene, 'stock_query', '') or ''}",
                pool=entity_media_pool,
                used_urls=used_pool_urls or set(),
                threshold=25,
            )
            if best_pool_photo and best_pool_photo.get("url"):
                cand_url = best_pool_photo["url"]
                try:
                    ext = ".jpg" if any(x in cand_url.lower() for x in ["jpg", "jpeg"]) else ".png"
                    output = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_doc{ext}")
                    await asyncio.to_thread(self._media_agent.download_file, cand_url, output)
                    if Path(output).exists() and Path(output).stat().st_size > 3000 and self._is_valid_image_file(output):
                        if used_pool_urls is not None:
                            used_pool_urls.add(cand_url)
                        logger.info(
                            f"Scene {scene.scene_id}: ✅ Khớp ngữ nghĩa ảnh TÀI LIỆU THẬT cho '{primary_entity}': "
                            f"{best_pool_photo.get('title', '')[:50]}"
                        )
                        return output
                except Exception as ex:
                    logger.warning(f"Scene {scene.scene_id}: Lỗi tải ảnh tài liệu pool ({cand_url[:40]}): {ex}")

        # ── TẦNG 2: MULTI-SOURCE SCENE SEARCH (Wikipedia, Wikimedia, Pexels Photos, Openverse) ──
        try:
            cand = await asyncio.to_thread(
                self._media_agent.search_scene_media_multi_source,
                scene_narration=scene.narration,
                visual_keyword=scene.visual_keyword or "",
                stock_query=getattr(scene, 'stock_query', None),
                visual_prompt=getattr(scene, 'visual_prompt', None),
                primary_entity=primary_entity,
                entity_type=entity_type,
                used_urls=used_pool_urls or set(),
                scene_id=scene.scene_id,
            )
            if cand and cand.get("url"):
                cand_url = cand["url"]
                c_src = cand.get("source", "web")
                c_type = cand.get("media_type", "image")
                ext = ".mp4" if c_type == "video" else (".jpg" if any(x in cand_url.lower() for x in ["jpg", "jpeg"]) else ".png")
                output = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_{c_src}{ext}")
                await asyncio.to_thread(self._media_agent.download_file, cand_url, output)
                if Path(output).exists() and Path(output).stat().st_size > 3000 and self._is_valid_image_file(output):
                    if used_pool_urls is not None:
                        used_pool_urls.add(cand_url)
                    if used_asset_paths is not None:
                        used_asset_paths.add(cand_url)
                    logger.info(
                        f"Scene {scene.scene_id}: ✅ Tải thành công từ {c_src}: "
                        f"{cand.get('title', '')[:50]}"
                    )
                    return output
        except Exception as ex:
            logger.debug(f"Scene {scene.scene_id}: Multi-source search error: {ex}")

        # ── Tầng 3: AI Image Generation (ưu tiên visual_prompt) ──
        # FALLBACK CHÍNH khi stock/wiki đều thất bại — KHÔNG dùng lại clip cũ từ scene khác
        try:
            ai_img_path = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_ai.jpg")
            vp = getattr(scene, 'visual_prompt', None)
            ai_prompt = (vp.strip() if vp and len(vp.strip()) > 10 else scene.visual_keyword)
            if primary_entity and not vp:
                ai_prompt = (
                    f"Historical authentic photorealistic shot of {primary_entity}, "
                    f"{scene.visual_keyword}, period accurate, dramatic volumetric lighting, 8k"
                )
            res_img = await asyncio.to_thread(
                self._image_agent.generate_scene_image,
                ai_prompt,
                ai_img_path,
                "9:16",
                "cinematic",
                scene.scene_id,
                vp,
            )
            if res_img and Path(res_img).exists() and Path(res_img).stat().st_size > 1000 and self._is_valid_image_file(res_img):
                logger.info(f"Scene {scene.scene_id}: ✅ AI Image sinh thành công ({primary_entity or 'generic'})")
                return res_img
        except Exception as e:
            logger.warning(f"Scene {scene.scene_id}: Lỗi sinh ảnh AI: {e}")

        # ── Tầng 4: Pexels Video/Photo (CHỈ DÙNG KHI KHÔNG CÓ primary_entity) ──
        # Dùng safe_q (stock_query đã lọc) thay vì raw visual_keyword để tránh Anne Hathaway, rollercoaster
        if not primary_entity and self._media_agent.api_key:
            safe_q = self._safe_pexels_query(scene)
            logger.info(f"Scene {scene.scene_id}: 🔍 Pexels safe_query = '{safe_q}'")
            try:
                videos = await asyncio.to_thread(
                    self._media_agent.search_videos, safe_q, 3
                )
                if videos:
                    for video_data in videos:
                        video_files = video_data.get("video_files", [])
                        best = next(
                            (f for f in video_files
                             if f.get("height", 0) >= 720
                             and f.get("width", 0) <= f.get("height", 0)),
                            None
                        )
                        if best and best.get("link"):
                            vid_url = best["link"]
                            # DEDUP: Chỉ dùng URL chưa từng assign cho scene khác
                            if used_asset_paths and vid_url in used_asset_paths:
                                logger.debug(f"Scene {scene.scene_id}: Bỏ qua Pexels video trùng: {vid_url[:60]}")
                                continue
                            output = str(Path(media_dir) / f"scene_{scene.scene_id:03d}.mp4")
                            await asyncio.to_thread(
                                self._media_agent.download_file, vid_url, output
                            )
                            if used_asset_paths is not None:
                                used_asset_paths.add(vid_url)
                            logger.info(f"Scene {scene.scene_id}: ✅ Pexels Video (query='{safe_q}')")
                            return output
            except Exception as e:
                logger.warning(f"Scene {scene.scene_id}: Lỗi tìm Pexels Video: {e}")

            try:
                photos = await asyncio.to_thread(
                    self._media_agent.search_photos, safe_q, 3
                )
                if photos:
                    for photo in photos:
                        src = photo.get("src", {})
                        img_url = (
                            src.get("large2x") or src.get("portrait")
                            or src.get("original") or src.get("large")
                        )
                        if img_url:
                            if used_asset_paths and img_url in used_asset_paths:
                                logger.debug(f"Scene {scene.scene_id}: Bỏ qua Pexels photo trùng")
                                continue
                            img_output = str(Path(media_dir) / f"scene_{scene.scene_id:03d}.jpg")
                            await asyncio.to_thread(
                                self._media_agent.download_file, img_url, img_output
                            )
                            if self._is_valid_image_file(img_output):
                                if used_asset_paths is not None:
                                    used_asset_paths.add(img_url)
                                logger.info(f"Scene {scene.scene_id}: ✅ Pexels Photo (query='{safe_q}')")
                                return img_output
            except Exception as e:
                logger.warning(f"Scene {scene.scene_id}: Lỗi tìm Pexels Photo: {e}")

        # ── Tầng 5: Web Image Search (Wikimedia Commons — cho cảnh generic) ──
        try:
            web_photos = await asyncio.to_thread(
                self._media_agent.search_web_photos, scene.visual_keyword, 2
            )
            if web_photos:
                web_photo = web_photos[0]
                web_url = web_photo.get("url")
                if web_url:
                    web_ext = ".jpg" if any(x in web_url.lower() for x in ["jpg", "jpeg"]) else ".png"
                    web_output = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_web{web_ext}")
                    await asyncio.to_thread(
                        self._media_agent.download_file, web_url, web_output
                    )
                    if self._is_valid_image_file(web_output):
                        logger.info(f"Scene {scene.scene_id}: Tải thành công ảnh Web ({web_photo.get('title', '')[:30]})")
                        return web_output
        except Exception as e:
            logger.warning(f"Scene {scene.scene_id}: Lỗi tìm ảnh Web: {e}")

        # ── Tầng 6: Dynamic Aesthetic Graphic (Pillow) ──
        logger.info(f"Scene {scene.scene_id}: Dùng ảnh đồ họa cinematic gradient cho Ken Burns")
        placeholder = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_graphic.png")
        self._create_placeholder_image(placeholder, scene.visual_keyword, scene_id=scene.scene_id)
        return placeholder


    async def _step_render(self, render_job: RenderJobContract):
        """Step D: Gọi VideoAgent để render final MP4 kèm cập nhật %."""
        from app.agents.video_agent import VideoAgent
        agent = VideoAgent()

        async def on_render_progress(msg: str, pct: int):
            await self._report_progress("RENDER", msg, percent=pct)

        await agent.render(render_job, progress_callback=on_render_progress)

    def _step_qa(self, output_path: str):
        """Step D QA: Validate output bằng ffprobe."""
        from app.utils.ffmpeg_helper import validate_output
        result = validate_output(output_path)
        if not result["valid"]:
            errors = "; ".join(result["errors"])
            raise RuntimeError(f"QA FAILED: {errors}")
        logger.info(
            f"[Job {self.job_id}] QA PASSED: "
            f"{result['resolution']}, {result['duration_ms']}ms, "
            f"{result['file_size_mb']}MB, {result['codec_video']}+{result['codec_audio']}"
        )

    # ──────────────────────────────────────────
    # DB CHECKPOINT HELPERS
    # ──────────────────────────────────────────

    def _update_status(self, status: JobStatus, error_msg: str = None):
        """Ghi checkpoint trạng thái vào DB — resume-safe."""
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
            if job:
                job.status = status.value
                if error_msg:
                    job.error_message = error_msg
                elif status in (JobStatus.RENDERED, JobStatus.PUBLISHED):
                    # Clear error_message khi hoàn thành thành công
                    job.error_message = None

    def _save_audio_metadata(self, audio_path: str, duration_ms: int, scenes: List[SceneContract]):
        """Lưu metadata audio vào DB sau Step B."""
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
            if job:
                job.audio_path = audio_path
                job.audio_duration_ms = duration_ms
                # Cập nhật scenes_json với audio enrichment
                if job.scenes_json:
                    script = ScriptContract.from_json_str(job.scenes_json)
                    updated_script = script.model_copy(update={"scenes": scenes})
                    job.scenes_json = updated_script.to_json_str()

    def _save_render_manifest(self, render_job: RenderJobContract):
        """Lưu render manifest snapshot vào DB."""
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
            if job:
                job.render_manifest = render_job.model_dump_json()
                job.video_path = render_job.output_path

    # ──────────────────────────────────────────
    # UTILITY HELPERS
    # ──────────────────────────────────────────

    def _get_channel_voice(self) -> Optional[str]:
        """Tra cứu preferred_voice từ channels.yaml dựa trên target_channel_id."""
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
            if not job:
                return None
            channels = settings.get_channels_config()
            for ch in channels:
                if ch.get("id") == job.target_channel_id:
                    return ch.get("preferred_voice")
        return None

    def _find_bgm(self) -> Optional[str]:
        """Tìm file nhạc nền mặc định."""
        bgm_dir = Path(settings.BASE_DIR) / "assets" / "bgm"
        if bgm_dir.exists():
            for ext in ("*.mp3", "*.wav", "*.m4a"):
                files = list(bgm_dir.glob(ext))
                if files:
                    return str(files[0])
        return None

    @staticmethod
    def _is_valid_image_file(path_str: str) -> bool:
        """Kiểm tra file có tồn tại, kích thước hợp lệ và Pillow mở được định dạng raster (không phải SVG/hỏng)"""
        if not path_str:
            return False
        p = Path(path_str)
        if not p.exists() or p.stat().st_size < 1000:
            return False
        try:
            from PIL import Image
            with Image.open(path_str) as im:
                im.verify()
            return True
        except Exception:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
            return False

    @staticmethod
    def _create_placeholder_image(path: str, text: str, scene_id: int = 1):
        """Tạo ảnh đồ họa 1080x1920 nền gradient điện ảnh cho Ken Burns effect."""
        try:
            from PIL import Image, ImageDraw
            width, height = 1080, 1920
            # Tạo vertical gradient từ xanh đêm huyền bí sang tím điện ảnh
            base = Image.new("RGB", (width, height))
            draw = ImageDraw.Draw(base)

            top_color = (12, 17, 30)
            bottom_color = (30, 27, 75)

            for y in range(0, height, 2):
                ratio = y / height
                r = int(top_color[0] + (bottom_color[0] - top_color[0]) * ratio)
                g = int(top_color[1] + (bottom_color[1] - top_color[1]) * ratio)
                b = int(top_color[2] + (bottom_color[2] - top_color[2]) * ratio)
                draw.line([(0, y), (width, y)], fill=(r, g, b), width=2)

            # Viền subtle border
            draw.rectangle([(40, 60), (width - 40, height - 60)], outline=(60, 70, 110), width=2)

            # Badge Scene ở đỉnh
            badge_text = f"SCENE #{scene_id:02d}"
            draw.rectangle([(width // 2 - 120, 240), (width // 2 + 120, 300)], fill=(40, 50, 80))
            draw.text((width // 2, 270), badge_text, fill=(200, 220, 255), anchor="mm")

            # Keyword ở giữa
            display_text = text[:80]
            draw.text((width // 2, height // 2 - 50), display_text, fill=(240, 240, 255), anchor="mm")

            base.save(path)
        except Exception:
            try:
                from PIL import Image
                Image.new("RGB", (1080, 1920), color=(15, 20, 35)).save(path)
            except Exception:
                Path(path).touch()

    def cleanup_temp(self):
        """Xóa thư mục tạm sau khi pipeline hoàn tất."""
        if self.work_dir and self.work_dir.exists():
            shutil.rmtree(self.work_dir, ignore_errors=True)
            logger.info(f"[Job {self.job_id}] Đã dọn dẹp temp: {self.work_dir}")
