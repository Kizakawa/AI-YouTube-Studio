"""
VideoPipeline — Orchestrator trung tâm điều phối luồng sản xuất video.

Luồng chính:
  A (Script) → B (Per-Scene Audio) → C (Per-Scene Media) → D (FFmpeg Render + QA)

Mỗi bước ghi checkpoint vào DB. Nếu crash → resume từ checkpoint cuối.
"""
import asyncio
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, List, Tuple, Callable, Awaitable, Dict, Set, Any

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
from app.agents.media_relevance_agent import MediaRelevanceAgent
from app.agents.visual_curation_agent import VisualCurationAgent
from app.utils.subtitle_helper import SubtitleHelper
from config.settings import settings

logger = logging.getLogger(__name__)

_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}

def _infer_media_type(media_path: Optional[str]) -> str:
    """Xác định media type chính xác từ extension file."""
    if not media_path:
        return "image"
    suffix = Path(media_path).suffix.lower()
    return "video" if suffix in _VIDEO_EXTENSIONS else "image"


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
        self.niche: Optional[str] = None

        # Agents (lazy init)
        self._voice_agent = VoiceAgent()
        self._media_agent = MediaAgent()
        self._image_agent = ImageAgent()
        self._media_relevance_agent = MediaRelevanceAgent()
        self._visual_curation_agent = VisualCurationAgent()

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
            # [FIX] Kiểm tra dung lượng đĩa trước khi bắt đầu render — tránh crash giữa chừng do đĩa đầy
            import shutil as _shutil
            _temp_dir_for_check = Path(settings.TEMP_DIR)
            _temp_dir_for_check.mkdir(parents=True, exist_ok=True)
            _free_gb = _shutil.disk_usage(str(_temp_dir_for_check)).free / (1024 ** 3)
            if _free_gb < 2.0:
                raise RuntimeError(
                    f"💾 Không đủ dung lượng đĩa (đang còn {_free_gb:.1f}GB). "
                    f"Yêu cầu tối thiểu 2GB cho thư mục temp để dựng video. "
                    f"Vui lòng xóa các file cũ trong '{settings.TEMP_DIR}' hoặc '{settings.OUTPUT_DIR}'."
                )
            logger.info(f"[Job {self.job_id}] ✅ Kiểm tra disk OK: {_free_gb:.1f}GB trống.")

            # Tạo thư mục làm việc riêng cho job
            self.work_dir = Path(settings.TEMP_DIR) / f"job_{self.job_id}"
            self.work_dir.mkdir(parents=True, exist_ok=True)

            render_succeeded = False
            self._update_status(JobStatus.RENDERING, "Đang khởi tạo tiến trình dựng video...")
            # ── [HARD NICHE FIREWALL] Tự động phát hiện & điều hướng nếu Job bị gán nhầm kênh ──
            try:
                from app.services.niche_manager import validate_channel_niche_alignment
                with get_db() as _db:
                    _j = _db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
                    if _j and _j.target_channel_id:
                        _check = validate_channel_niche_alignment(
                            channel_id=_j.target_channel_id,
                            title=_j.title or "",
                            topic=_j.topic or "",
                            script=_j.full_narration or "",
                        )
                        if not _check.get("passed", True) and _check.get("suggested_channel_id"):
                            _old_ch = _j.target_channel_id
                            _new_ch = _check["suggested_channel_id"]
                            _j.target_channel_id = _new_ch
                            _db.commit()
                            logger.warning(
                                f"🛡️ [HardNicheFirewall] Job #{self.job_id} ('{_j.title or _j.topic}') "
                                f"lệch ngách trên '{_old_ch}' -> Đã tự động chuyển sang kênh đúng ngách '{_new_ch}'!"
                            )
            except Exception as _e_fw:
                logger.debug(f"[Job {self.job_id}] Niche firewall pre-check warning: {_e_fw}")

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
                    scenes_audio = state.get("scenes_audio", [])
                    audio_map = {s.get("scene_id"): s for s in scenes_audio if isinstance(s, dict)}
                    for s in state.get("scenes_media", []):
                        if not isinstance(s, dict):
                            continue
                        sc_id = s.get("scene_id", 1)
                        a_info = audio_map.get(sc_id, {})
                        scenes_list.append(SceneContract(
                            scene_id=sc_id,
                            narration=s.get("narration") or a_info.get("narration") or " ",
                            visual_keyword=s.get("visual_keyword", ""),
                            visual_prompt=s.get("visual_prompt"),
                            media_path=s.get("media_path", ""),
                            media_type=s.get("media_type", "image"),
                            audio_path=s.get("audio_path") or a_info.get("audio_path") or audio_p or "",
                            audio_duration_ms=s.get("audio_duration_ms") or a_info.get("audio_duration_ms") or a_info.get("duration_ms") or 0,
                            camera_motion=s.get("camera_motion") or a_info.get("camera_motion"),
                            sfx_cue=s.get("sfx_cue") or a_info.get("sfx_cue"),
                            highlight_words=s.get("highlight_words") or a_info.get("highlight_words"),
                            overlay_badge=s.get("overlay_badge") or a_info.get("overlay_badge"),
                        ))

                    # Đảm bảo video được lưu trữ vĩnh viễn trong storage/outputs/video_{self.job_id}.mp4
                    import shutil
                    outputs_dir = Path(settings.OUTPUT_DIR)
                    outputs_dir.mkdir(parents=True, exist_ok=True)
                    perm_video = str(outputs_dir / f"video_{self.job_id}.mp4")

                    if video_p and Path(video_p).exists() and str(Path(video_p).resolve()) != str(Path(perm_video).resolve()):
                        shutil.copy2(video_p, perm_video)
                        video_p = perm_video
                    elif not video_p or not Path(video_p).exists():
                        if Path(perm_video).exists():
                            video_p = perm_video

                    # Step D2: Tạo bộ Thumbnail 1 giây chuẩn 16:9 và 9:16
                    t_16x9 = None
                    t_9x16 = None
                    try:
                        from app.services.thumbnail_service import ThumbnailService
                        ts = ThumbnailService()
                        base_img = next((s.media_path for s in scenes_list if s.media_path and s.media_path.endswith((".jpg", ".png", ".webp"))), None)
                        resolved_niche_id = state.get("channel_style") or getattr(self, "niche", None) or "general"
                        thumb_results = ts.generate_viral_thumbnail(
                            job_id=self.job_id,
                            topic=title,
                            viral_title=title,
                            video_path=video_p,
                            base_image_path=base_img,
                            niche_id=resolved_niche_id,
                        )
                        t_16x9 = thumb_results.get("thumb_16x9")
                        t_9x16 = thumb_results.get("thumb_9x16")
                        logger.info(f"[Job {self.job_id}] 🎨 Đã tạo bộ Thumbnail 1 giây thành công: 16:9={t_16x9} | 9:16={t_9x16}")
                    except Exception as ex_thumb:
                        logger.warning(f"[Job {self.job_id}] Lỗi tạo Thumbnail trong LangGraph: {ex_thumb}")

                    render_job = RenderJobContract(
                        job_id=self.job_id,
                        title=title,
                        scenes=scenes_list,
                        full_audio_path=audio_p,
                        total_audio_duration_ms=dur_ms or 30000,
                        subtitle_path=sub_p,
                        output_path=video_p,
                        thumbnail_path=t_16x9,
                        thumbnail_vertical_path=t_9x16,
                    )

                    # Đồng bộ lại đường dẫn video vĩnh viễn & thumbnail vào Database
                    try:
                        with get_db() as db:
                            j_rec = db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
                            if j_rec:
                                j_rec.video_path = video_p
                                if t_16x9:
                                    j_rec.thumbnail_path = t_16x9
                                if t_9x16:
                                    j_rec.thumbnail_vertical_path = t_9x16
                    except Exception as e_db:
                        logger.warning(f"[Job {self.job_id}] Không thể cập nhật video_path/thumbnail vào DB: {e_db}")

                    self._save_render_manifest(render_job)
                    self._update_status(JobStatus.RENDERED)
                    await self._report_progress("COMPLETED", f"🎉 Video #{self.job_id} đã hoàn tất và sẵn sàng phát hành!", percent=100)
                    render_succeeded = True
                    return render_job
                except Exception as e:
                    _lg_err_msg = (
                        f"⚠️ [Job {self.job_id}] LangGraph engine gặp sự cố: {e}\n"
                        f"Hệ thống tự động chuyển sang pipeline tuyến tính dự phòng..."
                    )
                    logger.warning(_lg_err_msg)
                    # [FIX] Gửi alert Telegram để thông báo LangGraph đang có vấn đề
                    try:
                        from app.services.channel_intelligence import send_telegram_broadcast
                        send_telegram_broadcast(
                            f"🚨 **[HỆ THốNG CẢNH BÁO]** LangGraph Multi-Agent Engine gặp lỗi cho Job #{self.job_id}!\n"
                            f"`{str(e)[:200]}`\n\n"
                            f"_↳ Đang tự động chuyển sang Linear Pipeline dự phòng. Kiểm tra log server ngay!_"
                        )
                    except Exception:
                        pass  # Không làm gán đoạn pipeline dự phòng nếu alert thất bại

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

            # Tự động nhận diện chủ đề Viễn tưởng để gán Niche chuyên sâu & màu sắc phụ đề Cyber Cyan
            from app.agents.scifi_visual_dispatcher_agent import SciFiVisualDispatcherAgent
            scifi_agent = SciFiVisualDispatcherAgent()
            is_scifi_video, scifi_conf, scifi_subgenre, _ = scifi_agent.detect_scifi_elements(
                topic=script.title or getattr(self, "topic", "") or "",
                narration=script.full_narration or "",
                channel_niche=self.niche or "",
            )
            if is_scifi_video and (not self.niche or self.niche in ("general", "auto")):
                self.niche = "scifi_future_tech"
                logger.info(f"[Job {self.job_id}] 🚀 Tự động kích hoạt Niche 'scifi_future_tech' cho video viễn tưởng [{scifi_subgenre}]!")
            elif (not self.niche or self.niche in ("general", "auto")):
                from app.agents.wildlife_visual_dispatcher_agent import WildlifeVisualDispatcherAgent
                w_agent = WildlifeVisualDispatcherAgent()
                is_wl_video, _, wl_subg, _ = w_agent.detect_wildlife_elements(
                    topic=script.title or getattr(self, "topic", "") or "",
                    narration=script.full_narration or "",
                    channel_niche=self.niche or "",
                )
                if is_wl_video:
                    self.niche = "animal_rescue" if wl_subg == "animal_rescue_pets" else "wildlife_predators"
                    logger.info(f"[Job {self.job_id}] 🦁 Tự động kích hoạt Niche '{self.niche}' cho video động vật hoang dã [{wl_subg}]!")

            # Tạo file phụ đề ASS Karaoke (word-level kinetic highlight theo Niche & Hook Headline Banner, tắt CTA badge cuối video để bảo toàn Infinite Loop)
            subtitle_path = str(self.work_dir / "subtitles.ass")
            hook_banner_text = getattr(script, "thumbnail_text", None) or getattr(script, "hook", None) or getattr(script, "title", None)
            SubtitleHelper.save_ass_file(
                enriched_scenes,
                subtitle_path,
                niche_id=self.niche,
                hook_title=hook_banner_text,
                show_cta_badge=False,
            )

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

            # Kiểm tra cờ viễn tưởng cho RenderJobContract
            if not is_scifi_video:
                scifi_scenes_count = sum(1 for s in media_scenes if getattr(s, "production_method", "") in ("TEXT_TO_VIDEO", "IMAGE_TO_VIDEO") or any(k in (getattr(s, "visual_keyword", "") or "").lower() for k in ["hố đen", "lỗ đen", "vũ trụ", "space", "cyberpunk", "alien", "robot", "quantum", "viễn tưởng"]))
                if scifi_scenes_count >= max(1, len(media_scenes) // 2):
                    is_scifi_video = True
                    scifi_subgenre = "general_scifi_fantasy"

            render_job = RenderJobContract(
                job_id=self.job_id,
                title=script.title,
                scenes=media_scenes,
                full_audio_path=full_audio_path,
                total_audio_duration_ms=total_duration,
                subtitle_path=subtitle_path,
                bgm_path=self._find_bgm(niche=self.niche),
                niche=self.niche or "general",
                sfx_dir="assets/sfx",  # Thư mục SFX cho chuyển cảnh (whoosh, hit, impact...)
                output_path=output_path,
                is_scifi=is_scifi_video,
                scifi_subgenre=scifi_subgenre,
            )

            # Gọi VideoAgent render
            await self._step_render(render_job)

            # Step D QA: Validate output bằng ffprobe
            await self._report_progress("QA", "🔍 Đang kiểm định chất lượng video bằng ffprobe...", percent=95)
            self._step_qa(output_path)

            # ── Step D2: Tối ưu ảnh bìa Thumbnail ("Thị trường 1 giây" - 12:15 - 17:55) ──
            try:
                from app.services.thumbnail_service import ThumbnailService
                ts = ThumbnailService()
                thumb_results = ts.generate_viral_thumbnail(
                    job_id=self.job_id,
                    topic=script.title or getattr(self, "topic", ""),
                    viral_title=getattr(script, "title", ""),
                    video_path=output_path,
                    base_image_path=next((s.media_path for s in media_scenes if s.media_path and s.media_path.endswith((".jpg", ".png"))), None),
                    niche_id=self.niche or "general",
                )
                render_job.thumbnail_path = thumb_results.get("thumb_16x9")
                render_job.thumbnail_vertical_path = thumb_results.get("thumb_9x16")
                logger.info(f"[Job {self.job_id}] 🎨 Đã tạo bộ Thumbnail 1 giây thành công: 16:9={render_job.thumbnail_path} | 9:16={render_job.thumbnail_vertical_path}")
            except Exception as ex_thumb:
                logger.warning(f"[Job {self.job_id}] Lỗi tạo Thumbnail: {ex_thumb}")

            self._save_render_manifest(render_job)
            self._update_status(JobStatus.RENDERED)
            await self._report_progress("COMPLETED", f"🎉 Video #{self.job_id} đã hoàn tất và sẵn sàng phát hành!", percent=100)
            logger.info(f"[Job {self.job_id}] Pipeline complete → {output_path}")
            render_succeeded = True
            return render_job

        except Exception as e:
            logger.error(f"[Job {self.job_id}] Pipeline FAILED: {e}", exc_info=True)
            self._update_status(JobStatus.FAILED, error_msg=str(e))
            await self._report_progress("ERROR", f"❌ Quá trình dựng video thất bại: {e}")
            raise
        finally:
            # Chỉ xóa thư mục tạm khi job đã hoàn thành thành công (giữ lại audio & media cache nếu lỗi để retry tức thì!)
            if render_succeeded:
                try:
                    self.cleanup_temp()
                except Exception as cleanup_err:
                    logger.warning(f"[Job {self.job_id}] Lỗi cleanup temp: {cleanup_err}")

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
            
            # P2.4: Tự động trích xuất Niche từ VideoJob hoặc Channels config
            self.niche = getattr(job, "niche", None)
            if not self.niche and job.target_channel_id:
                try:
                    channels = settings.get_channels_config()
                    for ch in channels:
                        if ch.get("id") == job.target_channel_id:
                            self.niche = ch.get("niche")
                            break
                except Exception:
                    pass

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
            niche=getattr(self, "niche", None),
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
        from app.services.media.media_history import get_recent_media_urls, record_used_media
        used_pool_urls = get_recent_media_urls(limit=150)
        used_asset_paths: set = set(used_pool_urls)  # ← DEDUP CROSS-JOB & CROSS-SCENE: track URLs đã dùng

        topic = ""
        channel_niche = "general"
        target_channel_id = None

        try:
            with get_db() as db:
                job = db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
                if job:
                    topic = job.topic or job.title or ""
                    target_channel_id = job.target_channel_id
                    channel_niche = getattr(job, "niche", "general") or "general"
                    script_obj = None
                    if job.scenes_json:
                        try:
                            script_obj = ScriptContract.from_json_str(job.scenes_json)
                            if getattr(script_obj, 'primary_entity', None):
                                primary_entity = script_obj.primary_entity
                        except Exception as parse_err:
                            logger.warning(f"[Job {self.job_id}] Không thể parse ScriptContract từ scenes_json: {parse_err}")

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

        # Khởi tạo mặc định tránh UnboundLocalError khi không có primary_entity
        entity_media_pool: List[Dict[str, Any]] = []

        channel_visual_mode = "auto"
        channels = settings.get_channels_config()
        if target_channel_id:
            for ch in channels:
                if ch.get("id") == target_channel_id:
                    channel_visual_mode = ch.get("visual_mode") or "auto"
                    if not channel_niche or channel_niche == "general":
                        channel_niche = ch.get("niche") or ch.get("style") or "general"
                    break

        search_first_mode = os.getenv("SEARCH_FIRST_VISUAL_MODE", "true").strip().lower() not in ("0", "false", "no", "off")
        if primary_entity and not search_first_mode:
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

        from app.agents.video_strategist_agent import VideoStrategistAgent
        from app.agents.ai_video_agent import AIVideoAgent

        ai_video_agent = AIVideoAgent()
        strategist = VideoStrategistAgent()

        visual_plan = await asyncio.to_thread(
            strategist.propose_production_strategy,
            topic=topic,
            scenes=[s.model_dump() for s in scenes],
            primary_entity=primary_entity,
            entity_type=entity_type,
            channel_niche=channel_niche,
        )
        proposals = visual_plan.get("scenes_strategy") or visual_plan.get("scene_proposals") or []
        strategy_map = {
            s.get("scene_id"): s
            for s in proposals
        }

        # Phân tích ngữ cảnh đồng loạt cho toàn bộ các cảnh trong 1 lệnh Gemini duy nhất (giảm ~85% độ trễ LLM)
        batch_analyses: Dict[int, Dict[str, Any]] = {}
        try:
            batch_analyses = await asyncio.to_thread(
                self._visual_curation_agent.analyze_scenes_batch,
                scenes=[s.model_dump() for s in scenes],
                topic=topic,
                primary_entity=primary_entity,
                entity_type=entity_type,
                channel_niche=channel_niche,
            )
        except Exception as e_batch:
            logger.debug(f"[Job {self.job_id}] analyze_scenes_batch fallback: {e_batch}")

        total_scenes = len(scenes)
        sem = asyncio.Semaphore(4)
        completed_count = 0

        async def _fetch_scene_worker(idx: int, scene: SceneContract) -> SceneContract:
            nonlocal completed_count
            async with sem:
                sc_strat = strategy_map.get(scene.scene_id, {})

                # ── Agent Phân tích Ngữ cảnh & Ý niệm Thị giác Chuyên sâu (VisualCurationAgent) ──
                surround_text = " ".join([s.narration for s in scenes[max(0, idx - 1):min(total_scenes, idx + 2)]])
                v_analysis = batch_analyses.get(scene.scene_id)
                if not v_analysis:
                    v_analysis = await asyncio.to_thread(
                        self._visual_curation_agent.analyze_scene_context,
                        narration=scene.narration,
                        topic=topic,
                        scene_id=scene.scene_id,
                        total_scenes=total_scenes,
                        primary_entity=primary_entity,
                        entity_type=entity_type,
                        surrounding_context=surround_text,
                        channel_niche=channel_niche,
                    )

                prod_method = sc_strat.get("production_method") or v_analysis.get("production_method", "STOCK_FOOTAGE")
                scene_pref = getattr(scene, 'media_type', None)

                # ── Kiểm tra phân cảnh Viễn Tưởng (Ưu tiên phán đoán AI Agent, chỉ dùng từ khóa khi AI offline) ──
                from app.agents.scifi_visual_dispatcher_agent import SciFiVisualDispatcherAgent
                scifi_agent = SciFiVisualDispatcherAgent()
                is_scifi_scene = bool(sc_strat.get("is_scifi"))
                if not is_scifi_scene and v_analysis.get("analysis_source") != "ai":
                    is_scifi_scene, _, _, _ = scifi_agent.detect_scifi_elements(
                        topic=topic,
                        narration=scene.narration,
                        visual_keyword=scene.visual_keyword or "",
                        visual_prompt=scene.visual_prompt or "",
                        channel_niche=channel_niche,
                    )

                if is_scifi_scene:
                    # Video viễn tưởng: Bắt buộc TEXT_TO_VIDEO (AI Video) hoặc STOCK_FOOTAGE (Kho video 9:16), cấm ép về IMAGE_TO_VIDEO
                    if prod_method == "IMAGE_TO_VIDEO":
                        prod_method = "TEXT_TO_VIDEO"
                    logger.info(f"[Job {self.job_id}] 🚀 Cảnh {idx+1}/{total_scenes}: YẾU TỐ VIỄN TƯỞNG -> Khóa chế độ Video Động ({prod_method}), chống chuyển ảnh đơn điệu!")
                else:
                    if channel_visual_mode in ("imagen_ai", "ai_image") and prod_method != "REAL_ARCHIVE":
                        prod_method = "IMAGE_TO_VIDEO"
                    elif scene_pref == "ai_image":
                        prod_method = "IMAGE_TO_VIDEO"

                label = f"[{prod_method}] Cảnh {idx+1}/{total_scenes}"
                rationale_text = v_analysis.get("narrative_subtext") or sc_strat.get("rationale", "")
                completed_count += 1
                pct = 25 + int((completed_count / total_scenes) * 35)
                await self._report_progress("MEDIA", f"🎬 {label}: {rationale_text[:40]}...", percent=pct)

                media_path = ""
                dur = scene.actual_duration_seconds

                # ── ƯU TIÊN TUYỆT ĐỐI: NẾU PHÂN CẢNH ĐÃ CÓ MEDIA ĐƯỢC CHỌN TỪ STORYBOARD HOẶC CACHE TRƯỚC ĐÓ ──
                pre_media = getattr(scene, 'media_path', None)
                if pre_media and Path(pre_media).exists() and self._is_valid_media_file(pre_media):
                    media_path = str(Path(pre_media).resolve())
                    used_asset_paths.add(media_path)
                    logger.info(f"[Job {self.job_id}] Scene {scene.scene_id}: 🎯 Sử dụng Media đã duyệt trước từ Storyboard: {media_path}")
                else:
                    cached_candidates = sorted(
                        Path(media_dir).glob(f"scene_{scene.scene_id:03d}_*"),
                        key=lambda p: p.stat().st_mtime if p.exists() else 0,
                        reverse=True,
                    )
                    for cand_p in cached_candidates:
                        if cand_p.is_file() and cand_p.stat().st_size > 3000 and self._is_valid_media_file(str(cand_p)):
                            media_path = str(cand_p.resolve())
                            used_asset_paths.add(media_path)
                            logger.info(f"[Job {self.job_id}] Scene {scene.scene_id}: ⚡ Tái sử dụng Media đã tải sẵn trong cache: {cand_p.name}")
                            break

                p_ai = v_analysis.get("cinematic_ai_prompt") or sc_strat.get("ai_prompt") or scene.visual_prompt or scene.visual_keyword

                # 1. ƯU TIÊN SỐ 1: Tải Media qua _fetch_scene_media (Tầng 0: Gemini Google Search ảnh tĩnh -> VideoAgent render 1 lần song song)
                if not media_path:
                    media_path = await self._fetch_scene_media(
                        scene=scene,
                        media_dir=media_dir,
                        primary_entity=primary_entity,
                        entity_type=entity_type,
                        entity_media_pool=entity_media_pool,
                        used_pool_urls=used_pool_urls,
                        used_asset_paths=used_asset_paths,
                        sc_strat=sc_strat,
                        v_analysis=v_analysis,
                        topic=topic,
                        channel_visual_mode=channel_visual_mode,
                        channel_niche=channel_niche,
                    )
                    if media_path:
                        used_asset_paths.add(media_path)
                        from app.services.media.media_history import compute_file_md5
                        m_md5 = compute_file_md5(media_path)
                        if m_md5:
                            used_asset_paths.add(f"md5:{m_md5}")
                            used_pool_urls.add(f"md5:{m_md5}")

                # 2. Dự phòng IMAGE_TO_VIDEO / TEXT_TO_VIDEO nếu _fetch_scene_media chưa lấy được file
                if not media_path and prod_method == "IMAGE_TO_VIDEO":
                    ai_vid_path = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_i2v.mp4")
                    res_v = await ai_video_agent.generate_image_to_video(
                        prompt=p_ai,
                        output_path=ai_vid_path,
                        duration_seconds=dur,
                        scene_id=scene.scene_id,
                        camera_motion=sc_strat.get("camera_motion", "zoom_in"),
                        visual_prompt=scene.visual_prompt,
                        used_urls=used_asset_paths,
                        visual_analysis=v_analysis,
                        narration=scene.narration,
                        skip_web_search=True,
                    )
                    if res_v and Path(res_v).exists():
                        media_path = res_v
                        used_asset_paths.add(res_v)
                elif not media_path and prod_method == "TEXT_TO_VIDEO":
                    ai_vid_path = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_t2v.mp4")
                    res_v = await ai_video_agent.generate_text_to_video(
                        prompt=p_ai,
                        output_path=ai_vid_path,
                        duration_seconds=dur,
                        scene_id=scene.scene_id,
                        camera_motion=sc_strat.get("camera_motion", "zoom_in"),
                        visual_prompt=scene.visual_prompt,
                        used_urls=used_asset_paths,
                        visual_analysis=v_analysis,
                        narration=scene.narration,
                        skip_web_search=True,
                    )
                    if res_v and Path(res_v).exists():
                        media_path = res_v
                        used_asset_paths.add(res_v)

                # ── Thẩm định & Chấm điểm độ liên quan với MediaRelevanceAgent (bỏ qua nếu đã qua Gemini Vision Search) ──
                already_vision_curated = bool(
                    media_path and any(tag in Path(media_path).name.lower() for tag in ("_gsearch", "_creature", "_photo"))
                )
                if media_path and Path(media_path).exists() and not already_vision_curated:
                    relevance_eval = await asyncio.to_thread(
                        self._media_relevance_agent.evaluate_media,
                        media_path=media_path,
                        narration=scene.narration,
                        visual_keyword=scene.visual_keyword or "",
                        topic=topic,
                        scene_id=scene.scene_id,
                        primary_entity=primary_entity,
                        production_method=prod_method,
                    )
                    if not relevance_eval.get("is_relevant", True) and relevance_eval.get("verdict") == "FAIL":
                        logger.warning(
                            f"[Job {self.job_id}] ⚠️ Cảnh {scene.scene_id} media chưa đạt chuẩn nội dung "
                            f"(Score: {relevance_eval.get('overall_score')}/10). Vấn đề: {relevance_eval.get('issues')}. "
                            f"Tự động sửa chữa theo gợi ý: '{relevance_eval.get('replacement_query')}'..."
                        )
                        fix_query = relevance_eval.get("replacement_query") or scene.visual_keyword or topic
                        fixed_media = None

                        # Ưu tiên tìm video nếu phương pháp cần video hoặc cảnh cần chuyển động
                        if prod_method in ("STOCK_FOOTAGE", "TEXT_TO_VIDEO") or scene.scene_id == 1:
                            best_vid = await asyncio.to_thread(
                                self._media_agent.search_best_video_clip,
                                fix_query,
                                used_pool_urls,
                                v_analysis,
                                scene.narration,
                            )
                            if best_vid and best_vid.get("url"):
                                cand_url = best_vid["url"]
                                new_v_p = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_relevance_fix.mp4")
                                await asyncio.to_thread(self._media_agent.download_file, cand_url, new_v_p)
                                if Path(new_v_p).exists() and Path(new_v_p).stat().st_size > 5000:
                                    fixed_media = new_v_p
                                    from app.services.media.media_history import extract_media_identity, record_used_media
                                    ids = extract_media_identity(cand_url)
                                    used_pool_urls.update(ids)
                                    used_asset_paths.update(ids)
                                    used_asset_paths.add(new_v_p)
                                    record_used_media(cand_url, query=fix_query, source="relevance_fix", media_path=new_v_p)
                                    logger.info(f"[Job {self.job_id}] ✅ Đã thay thế Cảnh {scene.scene_id} bằng video clip chuẩn: {fix_query}")

                        # Nếu chưa tìm được video, thử sinh ảnh AI chất lượng cao từ refined_prompt
                        if not fixed_media:
                            fix_prompt = relevance_eval.get("refined_prompt") or p_ai or f"Cinematic photorealistic 8k vertical shot of {fix_query}, {topic}"
                            new_img_p = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_relevance_fix.jpg")
                            res_ai = await asyncio.to_thread(
                                self._image_agent.generate_scene_image,
                                fix_prompt,
                                new_img_p,
                                "9:16",
                                "cinematic",
                                scene.scene_id,
                                fix_prompt,
                                used_asset_paths,
                                topic,
                            )
                            if res_ai and Path(res_ai).exists() and Path(res_ai).stat().st_size > 2000:
                                fixed_media = res_ai
                                from app.services.media.media_history import extract_media_identity, compute_file_md5
                                used_asset_paths.add(fixed_media)
                                md5_h = compute_file_md5(fixed_media)
                                if md5_h:
                                    used_asset_paths.add(f"md5:{md5_h}")
                                    used_pool_urls.add(f"md5:{md5_h}")
                                logger.info(f"[Job {self.job_id}] ✅ Đã thay thế Cảnh {scene.scene_id} bằng ảnh/video AI chuẩn xác từ đạo diễn")

                        if fixed_media:
                            media_path = fixed_media

                _motion_choices = ["zoom_in", "pan_right", "zoom_out", "pan_left"]
                sfx = sc_strat.get("sfx_cue") or getattr(scene, 'sfx_cue', None) or ("hit" if idx == 0 else "whoosh")
                cam = sc_strat.get("camera_motion") or getattr(scene, 'camera_motion', None) or ("hyper_zoom" if idx == 0 else _motion_choices[idx % len(_motion_choices)])

                return scene.model_copy(update={
                    "media_path": media_path,
                    "media_type": _infer_media_type(media_path),
                    "visual_strategy": sc_strat.get("strategy"),
                    "production_method": prod_method,
                    "tool": sc_strat.get("tool"),
                    "strategy_rationale": rationale_text,
                    "sfx_cue": sfx,
                    "camera_motion": cam,
                })

        tasks = [_fetch_scene_worker(i, s) for i, s in enumerate(scenes)]
        result = list(await asyncio.gather(*tasks))

        # ── VÒNG KIỂM TOÁN CHỐNG TRÙNG LẶP HÌNH ẢNH TOÀN DIỆN (CROSS-SCENE PERCEPTUAL DEDUPLICATION AUDIT) ──
        # Đảm bảo 100% không có bất kỳ 2 phân cảnh nào dùng chung 1 ảnh hoặc frame video giống nhau
        from app.services.media.media_history import compute_file_md5, compute_image_dhash, hamming_distance, record_batch_used_media

        seen_scene_md5s: Dict[str, int] = {}
        seen_scene_dhashes: Dict[str, int] = {}
        final_used_urls = []

        for enriched_idx, enriched_scene in enumerate(result):
            m_path = enriched_scene.media_path
            if not m_path or not Path(m_path).exists():
                continue

            final_used_urls.append(m_path)
            cur_md5 = compute_file_md5(m_path)
            cur_dhash = compute_image_dhash(m_path)

            duplicate_scene_id = None
            if cur_md5 and cur_md5 in seen_scene_md5s:
                duplicate_scene_id = seen_scene_md5s[cur_md5]
            elif cur_dhash:
                for prev_dh, prev_sc_id in seen_scene_dhashes.items():
                    if hamming_distance(cur_dhash, prev_dh) <= 3:
                        duplicate_scene_id = prev_sc_id
                        break

            if duplicate_scene_id is not None:
                logger.warning(
                    f"[Job {self.job_id}] ⚠️ PHÁT HIỆN TRÙNG HÌNH ẢNH: Cảnh {enriched_scene.scene_id} "
                    f"trùng với Cảnh #{duplicate_scene_id}! Đang tự động sinh ảnh/video AI độc bản thay thế..."
                )
                unique_ai_prompt = (
                    f"Cinematic 8k vertical shot, {enriched_scene.visual_prompt or enriched_scene.visual_keyword or topic}, "
                    f"scene {enriched_scene.scene_id} unique composition, photorealistic, dramatic atmosphere, 9:16 composition"
                )
                replacement_path = str(Path(media_dir) / f"scene_{enriched_scene.scene_id:03d}_dedup_unique.jpg")
                res_unique = await asyncio.to_thread(
                    self._image_agent.generate_scene_image,
                    unique_ai_prompt,
                    replacement_path,
                    "9:16",
                    "cinematic",
                    enriched_scene.scene_id,
                    unique_ai_prompt,
                    used_asset_paths,
                    topic,
                )
                if res_unique and Path(res_unique).exists() and Path(res_unique).stat().st_size > 2000:
                    final_path = res_unique
                    anim_p = str(Path(media_dir) / f"scene_{enriched_scene.scene_id:03d}_dedup_anim.mp4")
                    res_anim = await asyncio.to_thread(
                        ai_video_agent.animate_thematic_image,
                        image_path=res_unique,
                        output_path=anim_p,
                        duration_seconds=max(float(getattr(enriched_scene, 'actual_duration_seconds', 3.5) or 3.5), 2.0),
                        camera_motion=getattr(enriched_scene, 'camera_motion', None) or "zoom_out",
                        is_hook=(enriched_scene.scene_id == 1),
                    )
                    if res_anim and Path(res_anim).exists() and Path(res_anim).stat().st_size > 5000:
                        final_path = res_anim

                    result[enriched_idx] = enriched_scene.model_copy(update={
                        "media_path": final_path,
                        "media_type": _infer_media_type(final_path),
                    })
                    cur_md5 = compute_file_md5(final_path)
                    cur_dhash = compute_image_dhash(final_path)
                    logger.info(f"[Job {self.job_id}] ✅ Đã khử trùng thành công cho Cảnh {enriched_scene.scene_id}")

            if cur_md5:
                seen_scene_md5s[cur_md5] = enriched_scene.scene_id
            if cur_dhash:
                seen_scene_dhashes[cur_dhash] = enriched_scene.scene_id

        # Lưu lại vào lịch sử cross-job
        record_batch_used_media(final_used_urls, job_id=self.job_id)

        return result


    @staticmethod
    def _safe_pexels_query(scene, topic_context: str = "") -> str:
        """
        Tạo Pexels search query an toàn:
        - Ưu tiên stock_query (2-3 từ thuần chủ đề)
        - Tự động gắn kết ngữ cảnh topic_context nếu từ khóa quá ngắn hoặc trừu tượng
        - Bộ lọc: Loại bỏ tên diễn viên, đạo diễn, từ ngữ rác kỹ thuật
        """
        raw = getattr(scene, 'stock_query', None) or scene.visual_keyword or ""

        _FORBIDDEN_PEXELS_WORDS = {
            "nolan", "interstellar", "inception", "avatar", "hollywood",
            "cinematic", "movie", "film", "hathaway", "mcconaughey",
            "director", "actor", "actress", "red carpet", "award",
            "interview", "premiere", "promotional",
            "rollercoaster", "thrill", "amusement", "park",
            "cartoon", "anime", "animated", "2d animation",
        }
        # Nếu chủ đề không phải động vật hoang dã, cấm các từ ẩn dụ dễ gây kéo nhầm thú săn mồi (cougar/wolf)
        is_wildlife = any(w in topic_context.lower() for w in ["wildlife", "animal", "predator", "safari", "jungle", "rescue", "dog", "cat"])
        if not is_wildlife:
            _FORBIDDEN_PEXELS_WORDS.update({"primal", "predator", "prey", "cougar", "wolf", "apex", "hunting"})

        words = raw.lower().split()
        filtered = [w for w in words if w not in _FORBIDDEN_PEXELS_WORDS]
        clean = " ".join(filtered).strip()

        # Giới hạn 3 từ
        clean_words = clean.split()
        if len(clean_words) > 3:
            clean = " ".join(clean_words[:3])

        if not clean:
            clean = topic_context or "dramatic scene"

        # Tự động chuẩn hóa từ khóa nếu là chủ đề Động vật hoang dã
        try:
            from app.agents.wildlife_visual_dispatcher_agent import WildlifeVisualDispatcherAgent
            w_agent = WildlifeVisualDispatcherAgent()
            is_wl, _, subg, _ = w_agent.detect_wildlife_elements(topic=topic_context, narration=getattr(scene, 'narration', ''))
            if is_wl:
                clean = w_agent.sanitize_animal_query(clean, subgenre=subg)
        except Exception:
            pass

        # Nếu từ khóa quá ngắn (1 từ) và có topic_context, kết hợp để tránh trả về ảnh lạc đề
        if len(clean.split()) < 2 and topic_context and topic_context.lower() not in clean.lower():
            clean = f"{topic_context} {clean}".strip()

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
        sc_strat: Optional[Dict[str, Any]] = None,
        v_analysis: Optional[Dict[str, Any]] = None,
        topic: str = "",
        channel_visual_mode: str = "auto",
        channel_niche: str = "general",
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
        from app.services.media.media_history import (
            extract_media_identity,
            is_media_already_used,
            compute_file_md5,
            record_used_media,
        )

        # ── Checkpoint: Chỉ tái sử dụng media nếu CHƯA từng gán cho cảnh nào trong video hiện tại ──
        # NẾU ĐANG Ở DOCUMENTARY MODE: Tuyệt đối không tái sử dụng video stock cũ (.mp4 từ Pexels) vì đó là diễn viên hiện đại!
        if not primary_entity:
            for ext in [".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp"]:
                candidate = Path(media_dir) / f"scene_{scene.scene_id:03d}{ext}"
                cand_str = str(candidate)
                if candidate.exists() and candidate.stat().st_size > 10240 and not is_media_already_used(cand_str, used_asset_paths or set()):
                    logger.info(f"Scene {scene.scene_id}: Tái sử dụng media từ checkpoint ({candidate.name})")
                    if used_asset_paths is not None:
                        used_asset_paths.add(cand_str)
                    return cand_str

        # Kiểm tra checkpoint các file ảnh tài liệu hợp lệ
        allowed_suffixes = ["_doc", "_wiki", "_real", "_web", "_google", "_ai"] if primary_entity else ["_web", "_google", "_ai"]
        for suffix in allowed_suffixes:
            for ext in [".jpg", ".jpeg", ".png", ".webp"]:
                candidate = Path(media_dir) / f"scene_{scene.scene_id:03d}{suffix}{ext}"
                cand_str = str(candidate)
                if candidate.exists() and candidate.stat().st_size > 3000 and self._is_valid_media_file(cand_str) and not is_media_already_used(cand_str, used_asset_paths or set()):
                    logger.info(f"Scene {scene.scene_id}: Tái sử dụng ảnh tài liệu từ checkpoint ({candidate.name})")
                    if used_asset_paths is not None:
                        used_asset_paths.add(cand_str)
                    return cand_str

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
                        if used_asset_paths is not None:
                            used_asset_paths.add(str(dest))
                        return str(dest)

        # ── Quyết định chính sách Smart Hybrid theo Đạo Diễn Chiến Lược (sc_strat) & Scene Preference ──
        strat_method = (sc_strat or {}).get("production_method") or (v_analysis or {}).get("production_method", "STOCK_FOOTAGE")
        scene_pref = getattr(scene, 'media_type', None) or "auto"
        prefer_video = False
        prefer_ai = False
        prefer_photo = False

        from app.agents.scifi_visual_dispatcher_agent import SciFiVisualDispatcherAgent
        is_scifi_scene = bool((sc_strat or {}).get("is_scifi"))
        if not is_scifi_scene and (v_analysis or {}).get("analysis_source") != "ai":
            is_scifi_scene, _, _, _ = SciFiVisualDispatcherAgent().detect_scifi_elements(
                narration=scene.narration,
                visual_keyword=scene.visual_keyword or "",
                visual_prompt=getattr(scene, 'visual_prompt', None) or "",
            )

        if is_scifi_scene:
            # Video viễn tưởng: Bắt buộc ưu tiên Full Video 9:16 chuyển động (Kho video hoặc AI Video), cấm dùng ảnh tĩnh
            prefer_video = True
            prefer_ai = False
            prefer_photo = False
        elif channel_visual_mode in ("pexels_stock", "video") or channel_niche in ("animal_rescue", "wildlife_predators", "nature"):
            # Các ngách thực tế / kênh cấu hình Stock: Ưu tiên video chuyển động thật 9:16
            prefer_video = True
            prefer_ai = False
            prefer_photo = False
        elif strat_method == "REAL_ARCHIVE" or scene_pref == "documentary":
            prefer_photo = True
        elif strat_method in ("STOCK_FOOTAGE", "TEXT_TO_VIDEO") or scene_pref == "video" or scene.scene_id == 1:
            prefer_video = True
        elif strat_method == "IMAGE_TO_VIDEO" or scene_pref == "ai_image":
            prefer_ai = True
        elif scene_pref == "photo":
            prefer_photo = True
        else:
            if primary_entity and entity_type in ("person", "event") and scene_pref == "documentary":
                prefer_photo = True
            else:
                # Mặc định cho Shorts: Ưu tiên Full Video 9:16 động để giữ retention cao!
                prefer_video = True


        # ── TẦNG 0 (ƯU TIÊN TUYỆT ĐỐI): GEMINI GOOGLE SEARCH + VISION CURATION + 3D MOTION ──
        # Phụ thuộc vào kết quả tìm kiếm Google Images bắt mắt để câu view (CGI, Concept Art 8K,
        # Movie Scene, Dramatic HD, Dark Horror) do Gemini Vision trực tiếp chấm chọn + Chuyển động 3D!
        search_first = os.getenv("SEARCH_FIRST_VISUAL_MODE", "true").strip().lower() not in ("0", "false", "no", "off")
        creature_first = os.getenv("CREATURE_ART_FIRST", "true").strip().lower() not in ("0", "false", "no", "off")
        is_creature = bool(v_analysis and v_analysis.get("creature_mode"))
        is_hook_video_priority = (
            scene.scene_id == 1
            and not is_creature
            and (channel_visual_mode in ("stock_footage", "pexels_stock", "video") or channel_niche in ("wildlife_predators", "animal_rescue"))
        )
        def _audit_scene1_media_wow(candidate_path: str, v_score_hint: float = 0.0) -> bool:
            """
            Chốt chặn kiểm định bắt buộc cho MỌI tài nguyên Cảnh 1 (cả Ảnh lẫn Video):
            - Nếu là Ảnh: Đo trực tiếp độ sáng, tương phản, độ sắc nét viền bằng assess_image_wow_factor.
            - Nếu là Video (.mp4/.mov): Trích xuất đúng khung hình tại điểm Peak-Action (Frame 0.0s sau khi cắt)
              và chấm điểm assess_image_wow_factor. Nếu video bị mờ, mù sương, tối đen hoặc phẳng lỳ -> Bác bỏ ngay!
            """
            if scene.scene_id != 1 or is_creature:
                return True
            try:
                from app.services.media.image_quality import assess_image_wow_factor
                ext_low = Path(candidate_path).suffix.lower()
                if ext_low in (".mp4", ".mov", ".webm", ".mkv"):
                    from app.utils.ffmpeg_helper import find_hook_peak_action_offset, get_ffmpeg_cmd
                    import subprocess
                    seek_s = find_hook_peak_action_offset(candidate_path, max(scene.actual_duration_seconds, 1.8))
                    probe_jpg = f"{candidate_path}_frame0_probe.jpg"
                    try:
                        cmd = [
                            get_ffmpeg_cmd(), "-y", "-v", "quiet",
                            "-ss", f"{seek_s:.2f}", "-i", candidate_path,
                            "-frames:v", "1", "-q:v", "2", probe_jpg
                        ]
                        subprocess.run(cmd, capture_output=True, timeout=5)
                        if Path(probe_jpg).exists() and Path(probe_jpg).stat().st_size > 1000:
                            wow_info = assess_image_wow_factor(probe_jpg)
                            if not wow_info.get("is_wow", True):
                                logger.info(
                                    f"Scene 1: ⚠️ Video ứng viên không đạt chuẩn WOW tại Frame 0 "
                                    f"(score={wow_info.get('wow_score')}/10, lum={wow_info.get('luminance')}, "
                                    f"contrast={wow_info.get('contrast')}) → Bác bỏ để lấy Visual 8K đỉnh cao hơn!"
                                )
                                return False
                    finally:
                        Path(probe_jpg).unlink(missing_ok=True)
                    return True
                else:
                    wow_info = assess_image_wow_factor(candidate_path)
                    if not wow_info.get("is_wow", True) and v_score_hint < 7.2:
                        logger.info(
                            f"Scene 1: ⚠️ Ảnh ứng viên chưa đạt ngưỡng WOW bitmap (score={wow_info.get('wow_score')}/10, "
                            f"lum={wow_info.get('luminance')}, contrast={wow_info.get('contrast')}) → Bác bỏ!"
                        )
                        return False
                    return True
            except Exception as _e_aud:
                logger.debug(f"Scene 1 WOW audit skipped: {_e_aud}")
                return True

        if (
            (search_first or (creature_first and is_creature) or scene.scene_id == 1)
            and channel_visual_mode not in ("imagen_ai", "ai_image")
        ):
            try:
                va_dict = dict(v_analysis or {})
                va_dict["scene_id"] = scene.scene_id
                va_dict["is_hook_scene"] = (scene.scene_id == 1)
                g_queries = (
                    (va_dict.get("creature_queries") if is_creature else None)
                    or va_dict.get("google_image_queries")
                    or va_dict.get("search_queries")
                    or [getattr(scene, 'stock_query', None) or scene.visual_keyword or topic]
                )
                c_query = next((q for q in g_queries if q), getattr(scene, 'stock_query', None) or scene.visual_keyword or topic)
                searched_photo = await asyncio.to_thread(
                    self._media_agent.search_best_photo,
                    c_query,
                    used_pool_urls or used_asset_paths or set(),
                    va_dict,
                    scene.narration,
                    topic,
                )
                if searched_photo and searched_photo.get("url"):
                    c_url = searched_photo["url"]
                    tag_suffix = "creature" if is_creature else "gsearch"
                    out_img = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_{tag_suffix}.jpg")
                    await asyncio.to_thread(self._media_agent.download_file, c_url, out_img)
                    if Path(out_img).exists() and Path(out_img).stat().st_size > 3000 and self._is_valid_image_file(out_img):
                        v_score = float(searched_photo.get("vision_score") or 0.0)
                        if not _audit_scene1_media_wow(out_img, v_score_hint=v_score):
                            Path(out_img).unlink(missing_ok=True)
                            out_img = ""
                        if out_img and Path(out_img).exists():
                            ids = extract_media_identity(c_url)
                            if used_pool_urls is not None:
                                used_pool_urls.update(ids)
                            if used_asset_paths is not None:
                                used_asset_paths.update(ids)
                                used_asset_paths.add(out_img)
                            record_used_media(c_url, query=c_query, source=searched_photo.get("source", "google_search"), media_path=out_img)
                            badge = "🐙 [Quái vật Rùng Rợn 3D]" if is_creature else "🔎 [Gemini Search 3D]"
                            logger.info(f"Scene {scene.scene_id}: {badge} {searched_photo.get('source')}: {str(searched_photo.get('title', ''))[:55]}")
                            return out_img
            except Exception as e_cr:
                logger.debug(f"Scene {scene.scene_id}: Gemini Search visual error: {e_cr}")

        # ── TẦNG 0.5: Ưu tiên AI Image nếu prefer_ai và KHÔNG PHẢI VIỄN TƯỞNG ──
        if not is_scifi_scene and prefer_ai and getattr(scene, 'visual_prompt', None) and len(scene.visual_prompt.strip()) > 15:
            try:
                ai_img_path = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_ai.jpg")
                res_img = await asyncio.to_thread(
                    self._image_agent.generate_scene_image,
                    scene.visual_prompt,
                    ai_img_path,
                    "9:16",
                    "cinematic",
                    scene.scene_id,
                    scene.visual_prompt,
                    used_asset_paths,
                    topic,
                )
                if res_img and Path(res_img).exists() and Path(res_img).stat().st_size > 2000 and self._is_valid_image_file(res_img):
                    if used_asset_paths is not None:
                        used_asset_paths.add(res_img)
                    logger.info(f"Scene {scene.scene_id}: ✨ Tạo thành công ảnh AI điện ảnh: {scene.visual_prompt[:45]}...")
                    return res_img
            except Exception as e:
                logger.warning(f"Scene {scene.scene_id} AI Image error: {e}")

        # ── TẦNG 1: SEMANTIC POOL MATCH (DOCUMENTARY MODE - Bắt buộc qua cổng WOW nếu là Cảnh 1) ──
        if primary_entity and entity_media_pool and scene.scene_id != 1:
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
                        ids = extract_media_identity(cand_url)
                        if used_pool_urls is not None:
                            used_pool_urls.update(ids)
                        if used_asset_paths is not None:
                            used_asset_paths.update(ids)
                            used_asset_paths.add(output)
                        record_used_media(cand_url, query=primary_entity, source="wiki_pool", media_path=output)
                        logger.info(
                            f"Scene {scene.scene_id}: ✅ Khớp ngữ nghĩa ảnh TÀI LIỆU THẬT cho '{primary_entity}': "
                            f"{best_pool_photo.get('title', '')[:50]}"
                        )
                        return output
                except Exception as ex:
                    logger.warning(f"Scene {scene.scene_id}: Lỗi tải ảnh tài liệu pool ({cand_url[:40]}): {ex}")

        # ── TẦNG 2: MULTI-SOURCE SCENE SEARCH (Pexels 9:16 Video, Wikipedia, Pexels Photos, Openverse) ──
        try:
            cand = await asyncio.to_thread(
                self._media_agent.search_scene_media_multi_source,
                scene_narration=scene.narration,
                visual_keyword=scene.visual_keyword or "",
                stock_query=(sc_strat.get("search_query") if sc_strat and sc_strat.get("search_query") else getattr(scene, 'stock_query', None)),
                visual_prompt=getattr(scene, 'visual_prompt', None),
                primary_entity=primary_entity,
                entity_type=entity_type,
                used_urls=used_pool_urls or set(),
                scene_id=scene.scene_id,
                prefer_video=prefer_video,
                prefer_photo=prefer_photo,
                visual_analysis=v_analysis,
                topic=topic,
            )
            if cand and cand.get("url"):
                cand_url = cand["url"]
                c_src = cand.get("source", "web")
                c_type = cand.get("media_type", "image")
                ext = ".mp4" if c_type == "video" else (".jpg" if any(x in cand_url.lower() for x in ["jpg", "jpeg"]) else ".png")
                output = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_{c_src}{ext}")
                await asyncio.to_thread(self._media_agent.download_file, cand_url, output)
                if Path(output).exists() and Path(output).stat().st_size > 3000 and self._is_valid_media_file(output):
                    if not _audit_scene1_media_wow(output):
                        Path(output).unlink(missing_ok=True)
                        output = ""
                    if output and Path(output).exists():
                        ids = extract_media_identity(cand_url)
                        if used_pool_urls is not None:
                            used_pool_urls.update(ids)
                        if used_asset_paths is not None:
                            used_asset_paths.update(ids)
                            used_asset_paths.add(output)
                        record_used_media(cand_url, query=scene.visual_keyword or topic, source=c_src, media_path=output)

                        logger.info(
                            f"Scene {scene.scene_id}: ✅ Tải thành công từ {c_src}: "
                            f"{cand.get('title', '')[:50]}"
                        )
                        return output
        except Exception as ex:
            logger.warning(f"Scene {scene.scene_id}: Multi-source search error: {ex}")

        # ── Tầng 3: AI Media Generation (AI Video hoặc AI Image theo chỉ đạo Agent) ──
        # FALLBACK KHI STOCK/WIKI THẤT BẠI HOẶC AGENT TỪ CHỐI
        try:
            vp = getattr(scene, 'visual_prompt', None)
            ai_prompt = (
                (v_analysis.get("cinematic_ai_prompt") if v_analysis else None)
                or (vp.strip() if vp and len(vp.strip()) > 10 else None)
                or scene.visual_keyword
            )
            if primary_entity and not vp:
                ai_prompt = (
                    f"Historical authentic photorealistic shot of {primary_entity}, "
                    f"{scene.visual_keyword}, period accurate, dramatic volumetric lighting, 8k"
                )

            # Nếu phân cảnh cần chuyển động (prefer_video hoặc phương pháp là TEXT_TO_VIDEO/STOCK_FOOTAGE)
            is_dynamic_scene = prefer_video or (v_analysis and v_analysis.get("production_method") in ("TEXT_TO_VIDEO", "STOCK_FOOTAGE")) or scene.scene_id == 1
            if is_dynamic_scene:
                try:
                    from app.agents.ai_video_agent import AIVideoAgent
                    ai_vid_agent = AIVideoAgent()
                    ai_vid_path = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_t2v_fb.mp4")
                    res_v = await ai_vid_agent.generate_text_to_video(
                        prompt=ai_prompt,
                        output_path=ai_vid_path,
                        duration_seconds=scene.actual_duration_seconds,
                        scene_id=scene.scene_id,
                        visual_prompt=ai_prompt,
                        used_urls=used_asset_paths,
                        visual_analysis=v_analysis,
                        narration=scene.narration,
                    )
                    if res_v and Path(res_v).exists() and Path(res_v).stat().st_size > 5000:
                        if used_asset_paths is not None:
                            used_asset_paths.add(res_v)
                        logger.info(f"Scene {scene.scene_id}: ✅ Tạo thành công Video AI theo chỉ đạo Agent")
                        return res_v
                except Exception as e_v:
                    logger.warning(f"Scene {scene.scene_id} AI Video fallback error: {e_v}")

            # Tạo ảnh AI điện ảnh chất lượng cao 8K
            ai_img_path = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_ai.jpg")
            res_img = await asyncio.to_thread(
                self._image_agent.generate_scene_image,
                ai_prompt,
                ai_img_path,
                "9:16",
                "cinematic",
                scene.scene_id,
                vp,
                used_asset_paths,
                topic,
            )
            if res_img and Path(res_img).exists() and Path(res_img).stat().st_size > 1000 and self._is_valid_image_file(res_img):
                if used_asset_paths is not None:
                    used_asset_paths.add(res_img)
                logger.info(f"Scene {scene.scene_id}: ✅ AI Image sinh thành công theo phân tích ngữ cảnh ({primary_entity or 'generic'})")
                return res_img
        except Exception as e:
            logger.warning(f"Scene {scene.scene_id}: Lỗi sinh media AI: {e}")

        # ── Tầng 4: Pexels Video/Photo (CHỈ DÙNG KHI KHÔNG CÓ primary_entity) ──
        # Dùng safe_q (stock_query đã lọc có gắn neo topic) thay vì raw visual_keyword
        if not primary_entity and self._media_agent.api_key:
            safe_q = self._safe_pexels_query(scene, topic_context=topic)
            logger.info(f"Scene {scene.scene_id}: 🔍 Pexels safe_query = '{safe_q}'")
            try:
                videos = await asyncio.to_thread(
                    self._media_agent.search_videos, safe_q, 4
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
                            if used_asset_paths and is_media_already_used(vid_url, used_asset_paths):
                                logger.debug(f"Scene {scene.scene_id}: Bỏ qua Pexels video trùng: {vid_url[:60]}")
                                continue
                            output = str(Path(media_dir) / f"scene_{scene.scene_id:03d}.mp4")
                            await asyncio.to_thread(
                                self._media_agent.download_file, vid_url, output
                            )
                            if Path(output).exists() and Path(output).stat().st_size > 5000:
                                ids = extract_media_identity(vid_url)
                                if used_asset_paths is not None:
                                    used_asset_paths.update(ids)
                                    used_asset_paths.add(output)
                                if used_pool_urls is not None:
                                    used_pool_urls.update(ids)
                                record_used_media(vid_url, query=safe_q, source="pexels_video", media_path=output)
                                logger.info(f"Scene {scene.scene_id}: ✅ Pexels Video (query='{safe_q}')")
                                return output
            except Exception as e:
                logger.warning(f"Scene {scene.scene_id}: Lỗi tìm Pexels Video: {e}")

            try:
                photos = await asyncio.to_thread(
                    self._media_agent.search_photos, safe_q, 4, "portrait", topic, scene.narration, used_asset_paths
                )
                if photos:
                    from app.agents.visual_curation_agent import VisualCurationAgent
                    curator = VisualCurationAgent()
                    winner_photo, p_score, p_rationale, should_ai_photo = await asyncio.to_thread(
                        curator.curate_candidate_photos,
                        photos,
                        None,
                        scene.narration,
                        topic,
                        scene.visual_keyword or safe_q,
                        used_asset_paths,
                    )
                    eval_photos = [winner_photo] if winner_photo and not should_ai_photo else []
                    for photo in eval_photos:
                        img_url = photo.get("url") or photo.get("src", {}).get("large2x") or photo.get("src", {}).get("portrait")
                        if img_url:
                            if used_asset_paths and is_media_already_used(img_url, used_asset_paths):
                                logger.debug(f"Scene {scene.scene_id}: Bỏ qua Pexels photo trùng")
                                continue
                            img_output = str(Path(media_dir) / f"scene_{scene.scene_id:03d}.jpg")
                            await asyncio.to_thread(
                                self._media_agent.download_file, img_url, img_output
                            )
                            if self._is_valid_image_file(img_output):
                                ids = extract_media_identity(img_url)
                                if used_asset_paths is not None:
                                    used_asset_paths.update(ids)
                                    used_asset_paths.add(img_output)
                                if used_pool_urls is not None:
                                    used_pool_urls.update(ids)
                                record_used_media(img_url, query=safe_q, source="pexels_photo", media_path=img_output)
                                if prefer_video:
                                    try:
                                        from app.agents.ai_video_agent import AIVideoAgent
                                        ai_vid_agent = AIVideoAgent()
                                        anim_path = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_px_anim.mp4")
                                        res_anim = ai_vid_agent.animate_thematic_image(
                                            image_path=img_output,
                                            output_path=anim_path,
                                            duration_seconds=scene.actual_duration_seconds,
                                            camera_motion=sc_strat.get("camera_motion", "zoom_in") if sc_strat else "zoom_in",
                                            is_hook=(scene.scene_id == 1),
                                        )
                                        if res_anim and Path(res_anim).exists() and Path(res_anim).stat().st_size > 5000:
                                            logger.info(f"Scene {scene.scene_id}: 🎬 Đã chuyển hóa Pexels Photo thành Video Clip MP4: {anim_path}")
                                            if used_asset_paths is not None:
                                                used_asset_paths.add(anim_path)
                                            return anim_path
                                    except Exception as ex_anim:
                                        logger.debug(f"Scene {scene.scene_id} animation error: {ex_anim}")
                                logger.info(f"Scene {scene.scene_id}: ✅ Pexels Photo đã qua Agent thẩm định (Score: {p_score}/10 | query='{safe_q}')")
                                return img_output
            except Exception as e:
                logger.warning(f"Scene {scene.scene_id}: Lỗi tìm Pexels Photo: {e}")

        # ── Tầng 5: Web Image Search (Wikimedia Commons — cho cảnh generic) ──
        try:
            web_photos = await asyncio.to_thread(
                self._media_agent.search_web_photos, scene.visual_keyword, 4
            )
            if web_photos:
                from app.agents.visual_curation_agent import VisualCurationAgent
                curator = VisualCurationAgent()
                winner_web, w_score, w_rationale, should_ai_web = await asyncio.to_thread(
                    curator.curate_candidate_photos,
                    web_photos,
                    None,
                    scene.narration,
                    topic,
                    scene.visual_keyword,
                    used_asset_paths,
                )
                eval_web = [winner_web] if winner_web and not should_ai_web else []
                for web_photo in eval_web:
                    web_url = web_photo.get("url")
                    if web_url and not is_media_already_used(web_url, used_asset_paths or set()):
                        from app.services.media.entity_detector import is_incongruent_stock_image
                        if is_incongruent_stock_image(web_photo.get("title", ""), topic=topic, narration=scene.narration):
                            continue
                        web_ext = ".jpg" if any(x in web_url.lower() for x in ["jpg", "jpeg"]) else ".png"
                        web_output = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_web{web_ext}")
                        await asyncio.to_thread(
                            self._media_agent.download_file, web_url, web_output
                        )
                        if self._is_valid_image_file(web_output):
                            ids = extract_media_identity(web_url)
                            if used_asset_paths is not None:
                                used_asset_paths.update(ids)
                                used_asset_paths.add(web_output)
                            if used_pool_urls is not None:
                                used_pool_urls.update(ids)
                            record_used_media(web_url, query=scene.visual_keyword or topic, source="wikimedia_web", media_path=web_output)
                            logger.info(f"Scene {scene.scene_id}: ✅ Tải thành công ảnh Web qua Agent thẩm định ({web_photo.get('title', '')[:30]})")
                            return web_output
        except Exception as e:
            logger.warning(f"Scene {scene.scene_id}: Lỗi tìm ảnh Web: {e}")

        # ── Tầng 6: Dynamic Aesthetic Graphic (Pillow) ──
        logger.info(f"Scene {scene.scene_id}: Dùng ảnh đồ họa cinematic gradient cho Ken Burns")
        placeholder = str(Path(media_dir) / f"scene_{scene.scene_id:03d}_graphic.png")
        self._create_placeholder_image(placeholder, scene.visual_keyword, scene_id=scene.scene_id)
        if used_asset_paths is not None:
            used_asset_paths.add(placeholder)
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
        """Lưu render manifest snapshot và thumbnail vào DB."""
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
            if job:
                job.render_manifest = render_job.model_dump_json()
                job.video_path = render_job.output_path
                if getattr(render_job, 'thumbnail_path', None):
                    job.thumbnail_path = render_job.thumbnail_path
                if getattr(render_job, 'thumbnail_vertical_path', None):
                    job.thumbnail_vertical_path = render_job.thumbnail_vertical_path

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

    @staticmethod
    def find_bgm_for_niche(
        niche: Optional[str] = None,
        is_scifi: bool = False,
        scifi_subgenre: Optional[str] = None,
        **kwargs,
    ) -> Optional[str]:
        """
        P2.4: Tìm file nhạc nền phù hợp theo Niche (Sound Design Automation) hoặc fallback về mặc định:
        - animal_rescue, wholesome -> chill_ambient / emotional / gentle
        - wildlife_predators, survival, sport -> dark_suspense / inspiring_epic
        - luxury, motivation, finance -> inspiring_epic / energetic
        - psychology, philosophy, history, alternate_history -> deep_ambient / thoughtful
        - scifi, tech, future -> scifi_cyber / futuristic
        - horror, mystery, deep_sea, crime -> dark_suspense / eerie
        - default / chill / general -> chill_ambient
        """
        bgm_dir = Path(settings.BASE_DIR) / "assets" / "bgm"
        if not bgm_dir.exists():
            return None

        # Niche keyword mapping (hỗ trợ đầy đủ 5 kênh hiện tại + mở rộng đa ngách + scifi subgenres)
        niche_keywords = {
            "rescue": ["chill_ambient", "emotional", "piano", "warm", "chill", "ambient"],
            "animal": ["chill_ambient", "emotional", "chill", "ambient"],
            "wildlife": ["dark_suspense", "inspiring_epic", "suspense", "epic", "percussion"],
            "predator": ["dark_suspense", "inspiring_epic", "suspense", "dark"],
            "luxury": ["inspiring_epic", "epic", "motivation", "luxury"],
            "motivation": ["inspiring_epic", "epic", "motivation"],
            "finance": ["inspiring_epic", "wealth", "finance", "deep_ambient"],
            "psychology": ["deep_ambient", "ambient", "psychology", "dark_suspense"],
            "philosophy": ["deep_ambient", "thoughtful", "ambient"],
            "stoic": ["deep_ambient", "thoughtful", "ambient"],
            "history": ["deep_ambient", "history", "ancient", "inspiring_epic"],
            "scifi": ["scifi_cyber", "cyber", "scifi", "tech", "deep_ambient"],
            "future": ["scifi_cyber", "cyber", "future"],
            "space": ["scifi_cyber", "deep_ambient", "cyber"],
            "cosmic": ["dark_suspense", "scifi_cyber", "deep_ambient"],
            "cyberpunk": ["scifi_cyber", "cyber"],
            "horror": ["dark_suspense", "suspense", "dark", "horror"],
            "crime": ["dark_suspense", "suspense", "deep_ambient"],
            "mystery": ["dark_suspense", "deep_ambient", "mystery"],
            "deep_sea": ["dark_suspense", "deep_ambient"],
            "general": ["chill_ambient", "chill", "ambient"],
        }

        # Quét tất cả file audio trong assets/bgm
        all_bgms = []
        for ext in ("*.mp3", "*.wav", "*.m4a"):
            all_bgms.extend(bgm_dir.glob(ext))

        if not all_bgms:
            return None

        target_keys: List[str] = []

        # 0. Nếu cờ is_scifi bật, ưu tiên tìm theo scifi_subgenre trước
        if is_scifi:
            sub_low = str(scifi_subgenre or "").lower()
            if any(k in sub_low for k in ("horror", "dystop", "alien", "invasion")):
                target_keys.extend(["dark_suspense", "scifi_cyber", "suspense", "cyber"])
            else:
                target_keys.extend(["scifi_cyber", "cyber", "scifi", "deep_ambient"])

        # 1. Tìm theo từ khóa niche
        if niche:
            clean_niche = str(niche).lower().replace("-", "_")
            for k, keys in niche_keywords.items():
                if k in clean_niche:
                    for kw_item in keys:
                        if kw_item not in target_keys:
                            target_keys.append(kw_item)

        # Khớp file BGM
        for key in target_keys:
            for f in all_bgms:
                if key in f.stem.lower():
                    logger.info(f"🎵 BGM khớp Niche '{niche}' (is_scifi={is_scifi}) → {f.name}")
                    return str(f)

        # 2. Fallback: file đầu tiên có sẵn
        return str(all_bgms[0])

    def _find_bgm(self, niche: Optional[str] = None, **kwargs) -> Optional[str]:
        effective_niche = niche or getattr(self, "niche", None)
        return self.find_bgm_for_niche(effective_niche, **kwargs)

    @staticmethod
    def _is_valid_media_file(path_str: str) -> bool:
        """Kiểm tra tính hợp lệ của file media (cả video clip MP4 lẫn ảnh raster)."""
        if not path_str:
            return False
        p = Path(path_str)
        if not p.exists() or p.stat().st_size < 1000:
            return False
        if p.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv"):
            return p.stat().st_size > 5000
        return VideoPipeline._is_valid_image_file(path_str)

    @staticmethod
    def _is_valid_image_file(path_str: str) -> bool:
        """Kiểm tra file có tồn tại, kích thước hợp lệ và Pillow mở được định dạng raster (không phải SVG/hỏng/dải banner hẹp). Tuyệt đối không xóa file video."""
        if not path_str:
            return False
        p = Path(path_str)
        if not p.exists() or p.stat().st_size < 1000:
            return False
        if p.suffix.lower() in (".mp4", ".mov", ".webm", ".mkv"):
            return False
        try:
            from PIL import Image
            with Image.open(path_str) as im:
                im.verify()
            with Image.open(path_str) as im:
                w, h = im.size
                if w < 300 or h < 300:
                    return False
                if max(w / h, h / w) > 2.8:
                    return False
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
        """Xóa thư mục tạm sau khi pipeline hoàn tất, bảo vệ thư mục storage/outputs."""
        if self.work_dir and self.work_dir.exists():
            w_str = str(self.work_dir).lower()
            if "outputs" not in w_str and "thumbnails" not in w_str:
                shutil.rmtree(self.work_dir, ignore_errors=True)
                logger.info(f"[Job {self.job_id}] Đã dọn dẹp temp: {self.work_dir}")
