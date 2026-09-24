"""
VideoPipeline — Orchestrator trung tâm điều phối luồng sản xuất video.

Luồng chính:
  A (Script) → B (Per-Scene Audio) → C (Per-Scene Media) → D (FFmpeg Render + QA)

Mỗi bước ghi checkpoint vào DB. Nếu crash → resume từ checkpoint cuối.
"""
import logging
import shutil
from pathlib import Path
from typing import Optional, List, Tuple, Callable, Awaitable

from app.schemas.contracts import (
    JobStatus, ScriptContract, SceneContract, RenderJobContract
)
from app.database.models import get_db, VideoJob
from app.agents.voice_agent import VoiceAgent
from app.agents.media_agent import MediaAgent, detect_real_entity
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
        self.work_dir: Optional[Path] = None

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

            # Tạo file phụ đề SRT đồng bộ chính xác
            subtitle_path = str(self.work_dir / "subtitles.srt")
            SubtitleHelper.save_srt_file(enriched_scenes, subtitle_path)

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
        """Step C: Tải video/ảnh nền Pexels cho từng scene kèm báo cáo tiến độ %."""
        media_dir = str(self.work_dir / "media")
        Path(media_dir).mkdir(exist_ok=True)

        total_scenes = len(scenes)
        result = []
        for idx, scene in enumerate(scenes):
            pct = 25 + int(((idx + 1) / total_scenes) * 35)
            kw = scene.visual_keyword or "Stock 4K"
            await self._report_progress(
                "MEDIA",
                f"🖼️ Chuẩn bị media cảnh {idx+1}/{total_scenes} ({kw})...",
                percent=pct
            )
            media_path = await self._fetch_scene_media(scene, media_dir)
            enriched = scene.model_copy(update={
                "media_path": media_path,
                "media_type": "video" if media_path.endswith(".mp4") else "image",
            })
            result.append(enriched)
        return result

    async def _fetch_scene_media(self, scene: SceneContract, media_dir: str) -> str:
        """
        Tìm nạp media cho 1 scene với hệ thống phân cấp 7 tầng thông minh:
          Tầng 0: User Custom Media (Ảnh/Video do người dùng gửi trực tiếp qua Telegram)
          Tầng 1: Smart Entity Search (Wikipedia + Wikimedia + Google — ƯU TIÊN cho nhân vật/sự kiện thật)
          Tầng 2: Pexels Video portrait 9:16 (nếu có key Pexels)
          Tầng 3: Pexels Photo portrait 9:16 (nếu có key Pexels, dùng cho Ken Burns)
          Tầng 4: Web Image Search (Wikimedia Commons search nâng cao)
          Tầng 5: AI Image Generation (Google Imagen 3 / Pollinations 9:16 Cinematic AI)
          Tầng 6: Dynamic Aesthetic Graphic (Fallback cuối cùng khi offline)

        [v2.0] Tích hợp smart_search_media() với AI entity detection + Wikipedia API
        [FIX #3] Tất cả blocking HTTP calls được bọc trong asyncio.to_thread()
        """
        import asyncio

        # ── Checkpoint: Tái sử dụng media hợp lệ đã tải từ lần chạy trước (> 10KB) ──
        for ext in [".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp"]:
            candidate = Path(media_dir) / f"scene_{scene.scene_id:03d}{ext}"
            if candidate.exists() and candidate.stat().st_size > 10240:
                logger.info(f"Scene {scene.scene_id}: Tái sử dụng media từ checkpoint ({candidate.name})")
                return str(candidate)
        # Cũng check các file _real, _web, _wiki, _ai
        for suffix in ["_real", "_web", "_wiki", "_google"]:
            for ext in [".jpg", ".jpeg", ".png", ".webp"]:
                candidate = Path(media_dir) / f"scene_{scene.scene_id:03d}{suffix}{ext}"
                if candidate.exists() and candidate.stat().st_size > 10240:
                    logger.info(f"Scene {scene.scene_id}: Tái sử dụng media từ checkpoint ({candidate.name})")
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

        # ── Tầng 1: Smart Entity Search (Wikipedia + Wikimedia + Google) ──
        # Phát hiện entity thông minh bằng heuristic + AI
        is_entity, entity_name, entity_type = detect_real_entity(
            scene.visual_keyword, scene.narration
        )
        if is_entity and entity_name:
            logger.info(
                f"Scene {scene.scene_id}: 🔎 Phát hiện thực thể '{entity_name}' "
                f"(type={entity_type}) — Bắt đầu tìm ảnh tài liệu thật..."
            )
            try:
                # A. Wikipedia — Ảnh chân dung chính thức
                wiki_img = await asyncio.to_thread(
                    self._media_agent.search_wikipedia_entity_image, entity_name
                )
                if wiki_img and wiki_img.get("url"):
                    url = wiki_img["url"]
                    ext = ".jpg" if any(x in url.lower() for x in ["jpg", "jpeg"]) else ".png"
                    output = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_wiki{ext}")
                    await asyncio.to_thread(self._media_agent.download_file, url, output)
                    if Path(output).exists() and Path(output).stat().st_size > 3000 and self._is_valid_image_file(output):
                        logger.info(
                            f"Scene {scene.scene_id}: ✅ Tải thành công ảnh WIKIPEDIA chính thức "
                            f"({entity_name}): {wiki_img.get('title', '')[:50]}"
                        )
                        return output
            except Exception as ex:
                logger.warning(f"Scene {scene.scene_id}: Wikipedia search error for {entity_name}: {ex}")

            try:
                # B. Wikimedia Commons — Ảnh tài liệu lịch sử
                search_terms = [
                    f"{entity_name} official portrait" if entity_type == "person" else entity_name,
                    f"{entity_name} photograph",
                    entity_name,
                ]
                for term in search_terms:
                    web_photos = await asyncio.to_thread(
                        self._media_agent.search_web_photos, term, 3
                    )
                    if web_photos:
                        web_photo = web_photos[0]
                        web_url = web_photo.get("url")
                        if web_url:
                            ext = ".jpg" if any(x in web_url.lower() for x in ["jpg", "jpeg"]) else ".png"
                            output = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_real{ext}")
                            await asyncio.to_thread(self._media_agent.download_file, web_url, output)
                            if Path(output).exists() and Path(output).stat().st_size > 3000 and self._is_valid_image_file(output):
                                logger.info(
                                    f"Scene {scene.scene_id}: ✅ Tải thành công ảnh Wikimedia Commons "
                                    f"({entity_name}): {web_photo.get('title', '')[:50]}"
                                )
                                return output
                            break
            except Exception as ex:
                logger.warning(f"Scene {scene.scene_id}: Wikimedia search error for {entity_name}: {ex}")

            try:
                # C. Google Custom Search (nếu có key)
                google_results = await asyncio.to_thread(
                    self._media_agent.search_google_images, f"{entity_name} photo", 2
                )
                if google_results:
                    g_photo = google_results[0]
                    g_url = g_photo.get("url")
                    if g_url:
                        ext = ".jpg"
                        output = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_google{ext}")
                        await asyncio.to_thread(self._media_agent.download_file, g_url, output)
                        if Path(output).exists() and Path(output).stat().st_size > 3000 and self._is_valid_image_file(output):
                            logger.info(
                                f"Scene {scene.scene_id}: ✅ Tải ảnh Google Search ({entity_name})"
                            )
                            return output
            except Exception as ex:
                logger.debug(f"Scene {scene.scene_id}: Google search not available: {ex}")

        # ── Tầng 2: Pexels Video (nếu có key) ──
        if self._media_agent.api_key:
            try:
                videos = await asyncio.to_thread(
                    self._media_agent.search_videos, scene.visual_keyword, 2
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
                            output = str(Path(media_dir) / f"scene_{scene.scene_id:03d}.mp4")
                            await asyncio.to_thread(
                                self._media_agent.download_file, best["link"], output
                            )
                            logger.info(f"Scene {scene.scene_id}: Tải thành công Pexels Video")
                            return output
            except Exception as e:
                logger.warning(f"Scene {scene.scene_id}: Lỗi tìm Pexels Video: {e}")

        # ── Tầng 3: Pexels Photo (nếu có key, dùng cho Ken Burns) ──
        if self._media_agent.api_key:
            try:
                photos = await asyncio.to_thread(
                    self._media_agent.search_photos, scene.visual_keyword, 2
                )
                if photos:
                    photo = photos[0]
                    src = photo.get("src", {})
                    img_url = src.get("large2x") or src.get("portrait") or src.get("original") or src.get("large")
                    if img_url:
                        img_output = str(Path(media_dir) / f"scene_{scene.scene_id:03d}.jpg")
                        await asyncio.to_thread(
                            self._media_agent.download_file, img_url, img_output
                        )
                        if self._is_valid_image_file(img_output):
                            logger.info(f"Scene {scene.scene_id}: Tải thành công Pexels Photo cho Ken Burns")
                            return img_output
            except Exception as e:
                logger.warning(f"Scene {scene.scene_id}: Lỗi tìm Pexels Photo: {e}")

        # ── Tầng 4: Web Image Search (Wikimedia Commons — cho cảnh generic) ──
        try:
            web_photos = await asyncio.to_thread(
                self._media_agent.search_web_photos, scene.visual_keyword, 2
            )
            if web_photos:
                web_photo = web_photos[0]
                web_url = web_photo.get("url")
                if web_url:
                    web_ext = ".jpg" if "jpg" in web_url.lower() or "jpeg" in web_url.lower() else ".png"
                    web_output = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_web{web_ext}")
                    await asyncio.to_thread(
                        self._media_agent.download_file, web_url, web_output
                    )
                    if self._is_valid_image_file(web_output):
                        logger.info(f"Scene {scene.scene_id}: Tải thành công ảnh Web ({web_photo.get('title', '')[:30]})")
                        return web_output
        except Exception as e:
            logger.warning(f"Scene {scene.scene_id}: Lỗi tìm ảnh Web: {e}")

        # ── Tầng 5: AI Image Generation (Google Imagen 3 / Pollinations 9:16) ──
        try:
            ai_img_path = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_ai.jpg")
            res_img = await asyncio.to_thread(
                self._image_agent.generate_scene_image, scene.visual_keyword, ai_img_path
            )
            if res_img and Path(res_img).exists() and Path(res_img).stat().st_size > 1000 and self._is_valid_image_file(res_img):
                logger.info(f"Scene {scene.scene_id}: Sinh thành công ảnh AI điện ảnh 9:16")
                return res_img
        except Exception as e:
            logger.warning(f"Scene {scene.scene_id}: Lỗi sinh ảnh AI: {e}")

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
