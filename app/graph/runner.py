"""
app.graph.runner — Runner điều phối thực thi LangGraph Video Workflow tích hợp Database.

Đảm bảo:
1. Kết nối mượt mà với VideoJob trong SQLite / PostgreSQL.
2. Báo cáo tiến độ thời gian thực về Telegram Bot & Web Dashboard qua WebSocket / Polling.
3. Lưu trữ đầy đủ kết quả chấm điểm của 4 Bộ Graders vào trường quality_scores.
4. Hỗ trợ kích hoạt luồng chỉnh sửa vi mô (Granular Edit).
"""
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional, Callable, Awaitable, Dict, Any

from app.database.models import get_db, VideoJob
from app.schemas.contracts import JobStatus, ScriptContract
from app.graph.state import VideoWorkflowState, EditRequest
from app.graph.workflow import video_graph
from config.settings import settings

logger = logging.getLogger(__name__)

ACTIVE_GRAPH_RUNNERS: Dict[int, "GraphRunner"] = {}


async def report_job_progress(job_id: int, stage: str, message: str, percent: int):
    """
    Phát tín hiệu tiến độ thời gian thực đa kênh:
    - Nếu có LangGraph runner đang chạy -> gọi runner._report (đồng bộ tức thì cả SQLite DB và Telegram bot status message)
    - Fallback -> cập nhật trực tiếp vào SQLite DB
    """
    if not job_id:
        return
    runner = ACTIVE_GRAPH_RUNNERS.get(job_id)
    if runner:
        await runner._report(stage, message, percent)
    else:
        try:
            with get_db() as db:
                jb = db.query(VideoJob).filter(VideoJob.id == job_id).first()
                if jb:
                    jb.progress_percent = percent
                    jb.progress_message = message
        except Exception:
            pass


def report_job_progress_sync(job_id: int, stage: str, message: str, percent: int):
    """Gọi report_job_progress an toàn từ synchronous nodes mà không xung đột event loop."""
    if not job_id:
        return
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(report_job_progress(job_id, stage, message, percent))
    except RuntimeError:
        try:
            asyncio.run(report_job_progress(job_id, stage, message, percent))
        except Exception:
            pass


class GraphRunner:
    """Orchestrator thực thi LangGraph Video Workflow cho VideoJob."""

    def __init__(
        self,
        job_id: int,
        progress_callback: Optional[Callable[[str, str, int], Awaitable[None]]] = None,
        video_format: Optional[str] = None,
    ):
        try:
            from app.utils.log_config import ensure_file_logging_attached
            ensure_file_logging_attached()
        except Exception:
            pass
        self.job_id = job_id
        self.progress_callback = progress_callback
        self.video_format = video_format
        self.work_dir = Path(settings.TEMP_DIR) / f"job_{self.job_id}"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._last_db_pct: int = -20
        self._node_timer_ts: float = 0.0

    def _record_node_done(self, node_name: str, step: str, pct: int) -> None:
        try:
            import time as _time
            from app.utils.langsmith_tracer import record_node_span
            _now = _time.monotonic()
            _dur = max(0.001, _now - (self._node_timer_ts or _now))
            self._node_timer_ts = _now
            record_node_span(self.job_id, node_name, _dur, status="OK", details={"step": step, "percent": pct})
        except Exception:
            pass

    def _finish_trace(
        self,
        status: str,
        error: Optional[str] = None,
        final_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            from app.utils.langsmith_tracer import finish_job_trace, log_job_quality_feedback
            if isinstance(final_state, dict):
                scores_summary = {
                    "script_critic": final_state.get("script_eval"),
                    "visual_critic": final_state.get("media_eval"),
                    "hook_video_grader": final_state.get("hook_video_eval"),
                    "audio_qa": final_state.get("audio_eval"),
                    "video_qa": final_state.get("video_eval"),
                }
                log_job_quality_feedback(self.job_id, scores_summary)
            finish_job_trace(self.job_id, status=status, error=error)
        except Exception:
            pass

    def _get_trace_config(self, state: Dict[str, Any]) -> Dict[str, Any]:
        import time as _time
        from app.utils.langsmith_tracer import build_langgraph_run_config, start_job_trace
        self._node_timer_ts = _time.monotonic()
        mode = "edit" if state.get("edit_request") else "full_pipeline"
        start_job_trace(
            job_id=self.job_id,
            channel_id=str(state.get("target_channel_id") or "default"),
            topic=str(state.get("topic") or state.get("title") or ""),
            niche=str(state.get("channel_style") or "general"),
            mode=mode,
        )
        return build_langgraph_run_config(
            job_id=self.job_id,
            channel_id=str(state.get("target_channel_id") or "default"),
            topic=str(state.get("topic") or state.get("title") or ""),
            niche=str(state.get("channel_style") or "general"),
            mode=mode,
        )

    async def _report(self, stage: str, message: str, percent: int):
        """Đồng bộ tiến độ vào Database & gọi callback Telegram/Dashboard (chống nghẽn kết nối Cloud DB)."""
        logger.info(f"[LangGraph Job {self.job_id}] [{stage}] [{percent}%] {message}")
        if not self.progress_callback and (abs(percent - self._last_db_pct) >= 15 or percent in (5, 100)):
            self._last_db_pct = percent
            try:
                with get_db() as db:
                    job = db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
                    if job:
                        job.progress_percent = percent
                        job.progress_message = message
            except Exception as e:
                logger.debug(f"Không thể cập nhật tiến độ DB: {e}")

        if self.progress_callback:
            try:
                import inspect
                sig = inspect.signature(self.progress_callback)
                coro = (
                    self.progress_callback(stage, message, percent)
                    if len(sig.parameters) >= 3
                    else self.progress_callback(stage, message)
                )
                if percent >= 100:
                    await asyncio.wait_for(coro, timeout=3.5)
                else:
                    async def _fire_cb(_c):
                        try:
                            await asyncio.wait_for(_c, timeout=3.0)
                        except Exception:
                            pass
                    asyncio.create_task(_fire_cb(coro))
            except Exception as e:
                logger.warning(f"Lỗi khi gọi progress_callback: {e}")

    async def run(self) -> Dict[str, Any]:
        """Chạy toàn bộ quy trình sản xuất video bằng LangGraph (có Global Render Concurrency Semaphore)."""
        from app.graph.checkpoint_store import acquire_global_render_slot

        ACTIVE_GRAPH_RUNNERS[self.job_id] = self
        try:
            async with acquire_global_render_slot(self.job_id):
                return await self._run_internal()
        finally:
            ACTIVE_GRAPH_RUNNERS.pop(self.job_id, None)

    async def _run_internal(self) -> Dict[str, Any]:
        # 1. Nạp Job từ DB
        topic = ""
        title = ""
        channel_id = ""
        channel_style = "general"
        existing_script = None

        for _db_try in range(5):
            try:
                with get_db() as db:
                    job = db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
                    if not job:
                        raise ValueError(f"Không tìm thấy VideoJob với ID {self.job_id}")
                    
                    topic = job.topic or ""
                    title = job.title or ""
                    channel_id = job.target_channel_id or "default"
                    job.status = "RENDERING"
                    job.progress_percent = 5
                    job.progress_message = "Bắt đầu điều phối qua LangGraph Multi-Agent Engine..."
                    
                    if job.scenes_json:
                        try:
                            existing_script = json.loads(job.scenes_json)
                        except Exception:
                            pass
                break
            except ValueError:
                raise
            except Exception as _e_db_init:
                try:
                    from app.database.models import engine
                    engine.dispose()
                except Exception:
                    pass
                if _db_try >= 4:
                    cp_init = self.work_dir / "step_checkpoint.json"
                    ls_init = Path("storage/logs/langsmith") / f"job_{self.job_id}.json"
                    if cp_init.exists():
                        try:
                            cp_d = json.loads(cp_init.read_text(encoding="utf-8"))
                            topic = str(cp_d.get("topic") or "")
                            if not topic and ls_init.exists():
                                ls_d = json.loads(ls_init.read_text(encoding="utf-8"))
                                topic = str(ls_d.get("topic") or "")
                                channel_id = str(ls_d.get("channel_id") or "ch_wild_odyssey")
                            logger.warning(
                                f"[GraphRunner] Cloud DB tạm ngắt ({_e_db_init}) -> Khôi phục metadata Job #{self.job_id} từ checkpoint đĩa (topic='{topic}')!"
                            )
                            break
                        except Exception:
                            pass
                    raise
                logger.warning(f"[GraphRunner] Thử kết nối lại DB cho Job #{self.job_id} ({_e_db_init})...")
                await asyncio.sleep(0.8 * (_db_try + 1))

        if not existing_script:
            cp_file = self.work_dir / "step_checkpoint.json"
            cp_valid_for_topic = False
            if cp_file.exists():
                try:
                    cp_payload = json.loads(cp_file.read_text(encoding="utf-8"))
                    cp_topic = str(cp_payload.get("topic") or "").strip().lower()
                    if not cp_topic or cp_topic == topic.strip().lower():
                        cp_valid_for_topic = True
                        cp_nodes = cp_payload.get("nodes") or {}
                        cp_script = (cp_nodes.get("generate_audio") or {}).get("script_data")
                        if not (isinstance(cp_script, dict) and cp_script.get("scenes")):
                            cp_sm = (cp_nodes.get("fetch_media") or {}).get("scenes_media") or (cp_nodes.get("generate_audio") or {}).get("scenes_audio") or []
                            if isinstance(cp_sm, list) and len(cp_sm) >= 3:
                                cp_script = {
                                    "title": title or topic or f"Video #{self.job_id}",
                                    "hook": str(cp_sm[0].get("narration") or topic),
                                    "full_narration": " ".join(str(s.get("narration") or "") for s in cp_sm if isinstance(s, dict)),
                                    "scenes": [
                                        {
                                            "scene_id": int(s.get("scene_id") or (i + 1)),
                                            "narration": str(s.get("narration") or ""),
                                            "visual_keyword": str(s.get("visual_keyword") or topic),
                                            "estimated_seconds": 5.0,
                                            "camera_motion": s.get("camera_motion"),
                                            "sfx_cue": s.get("sfx_cue"),
                                            "highlight_words": s.get("highlight_words"),
                                            "overlay_badge": s.get("overlay_badge"),
                                        }
                                        for i, s in enumerate(cp_sm)
                                        if isinstance(s, dict)
                                    ],
                                    "tags": ["shorts", "viral"],
                                    "video_format": str((cp_nodes.get("generate_audio") or {}).get("video_format") or "short"),
                                }
                        if isinstance(cp_script, dict) and cp_script.get("scenes"):
                            existing_script = cp_script
                            logger.info(
                                f"⚡ [StepCheckpoint Resume] Job #{self.job_id}: Khôi phục script_data từ step_checkpoint.json "
                                f"('{cp_script.get('title', '')}', {len(cp_script.get('scenes', []))} cảnh)"
                            )
                except Exception:
                    pass
            if not cp_valid_for_topic:
                # Dọn sạch media/clips tồn đọng từ Job cũ trùng ID trước khi reset DB để tuyệt đối không tái sử dụng nhầm cảnh!
                import shutil as _sh_clean
                for _stale_sub in ("media", "clips", "hook_candidates", "render"):
                    _stale_dir = self.work_dir / _stale_sub
                    if _stale_dir.exists():
                        _sh_clean.rmtree(_stale_dir, ignore_errors=True)
                for _stale_f in self.work_dir.glob("hook_motion_*.mp4"):
                    _stale_f.unlink(missing_ok=True)
                if cp_file.exists():
                    cp_file.unlink(missing_ok=True)

        # Lấy channel niche / style, language và preferred_voice từ config (hỗ trợ cả canonical alias ch_*)
        channels = settings.get_channels_config()
        channel_lang = "en"
        preferred_voice = None
        channel_visual_mode = "auto"
        from app.services.smart_scheduler import resolve_channel_family
        from app.database.models import normalize_channel_id
        family_set = {str(x).strip().lower() for x in resolve_channel_family(channel_id) if x}
        norm_target = normalize_channel_id(channel_id).lower()
        for ch in channels:
            ch_raw_id = str(ch.get("id") or "").strip()
            ch_norm_id = normalize_channel_id(ch_raw_id).lower()
            ch_handle = str(ch.get("handle") or "").strip().lower()
            ch_name_norm = normalize_channel_id(str(ch.get("name") or "")).lower()
            if (
                ch_raw_id.lower() in family_set
                or ch_norm_id in family_set
                or ch_norm_id == norm_target
                or (ch_handle and ch_handle in family_set)
                or (ch_name_norm and ch_name_norm == norm_target)
            ):
                channel_style = ch.get("niche") or ch.get("style") or "general"
                channel_lang = ch.get("language") or "en"
                preferred_voice = ch.get("preferred_voice")
                channel_visual_mode = ch.get("visual_mode") or "auto"
                break

        # 2. Khởi tạo trạng thái ban đầu cho LangGraph
        resolved_video_format = "short"
        raw_vf_override = str(
            self.video_format
            or (existing_script.get("video_format") if isinstance(existing_script, dict) else "")
            or ""
        ).strip().lower()
        if raw_vf_override in ("long", "long_form", "long-form", "longform", "16:9", "landscape", "horizontal", "documentary"):
            resolved_video_format = "long"
        elif isinstance(existing_script, dict) and len(existing_script.get("scenes") or []) >= 14:
            resolved_video_format = "long"

        initial_state: VideoWorkflowState = {
            "job_id": self.job_id,
            "topic": topic,
            "target_channel_id": channel_id,
            "channel_style": channel_style,
            "language": channel_lang,
            "preferred_voice": preferred_voice,
            "visual_mode": channel_visual_mode,
            "video_format": resolved_video_format,
            "work_dir": str(self.work_dir),
            "script_data": existing_script,
            "script_retry_count": 0,
            "audio_retry_count": 0,
            "media_retry_count": 0,
            "content_retry_count": 0,
            "render_retry_count": 0,
            "status": "PROCESSING",
        }

        await self._report("GRAPH_INIT", "🚀 Khởi động đồ thị đa tác nhân LangGraph...", 5)

        # 3. Chạy luồng LangGraph với streaming để cập nhật tiến độ mượt mà
        final_state = initial_state
        try:
            async for event in video_graph.astream(initial_state, config=self._get_trace_config(initial_state)):
                for node_name, node_output in event.items():
                    if not isinstance(node_output, dict):
                        continue
                    final_state.update(node_output)
                    step = node_output.get("current_step", node_name.upper())
                    pct = node_output.get("progress_percent", 50)
                    msg = node_output.get("progress_message", f"Hoàn tất node: {node_name}")
                    self._record_node_done(node_name, step, pct)
                    await self._report(step, msg, pct)

        except Exception as ex:
            self._finish_trace("FAILED", str(ex))
            logger.error(f"[LangGraph Job {self.job_id}] Thất bại tại đồ thị: {ex}", exc_info=True)
            for _err_db_att in range(3):
                try:
                    with get_db() as db:
                        j = db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
                        if j:
                            j.status = "FAILED"
                            j.error_message = str(ex)
                            if not j.scenes_json and isinstance(final_state.get("script_data"), dict) and final_state["script_data"].get("scenes"):
                                try:
                                    j.scenes_json = json.dumps(final_state["script_data"], ensure_ascii=False)
                                except Exception:
                                    pass
                    break
                except Exception as _e_err_db:
                    try:
                        from app.database.models import engine
                        engine.dispose()
                    except Exception:
                        pass
                    if _err_db_att < 2:
                        import time as _t_err_s
                        _t_err_s.sleep(0.5 * (_err_db_att + 1))
                    else:
                        logger.warning(f"[LangGraph Job {self.job_id}] Không thể cập nhật trạng thái FAILED vào DB: {_e_err_db}")
            raise ex

        # 3.5. Tự động đẩy lên Google Drive đối với Video Dài 16:9 (hoặc khi bật ENABLE_DRIVE_VIDEO_BACKUP)
        rendered_video_path = final_state.get("rendered_video_path")
        final_video_format = str(
            final_state.get("video_format")
            or (final_state.get("script_data") or {}).get("video_format")
            or resolved_video_format
            or "short"
        ).strip().lower()
        drive_upload_info: Optional[Dict[str, Any]] = None
        auto_drive_long = os.getenv("AUTO_UPLOAD_DRIVE_LONGFORM", "true").lower() in ("1", "true", "yes")
        auto_drive_all = os.getenv("ENABLE_DRIVE_VIDEO_BACKUP", "false").lower() in ("1", "true", "yes")
        if rendered_video_path and Path(rendered_video_path).exists() and (final_video_format == "long" and auto_drive_long or auto_drive_all):
            try:
                await self._report("DRIVE_UPLOAD", "☁️ Đang tự động đẩy Video lên Google Drive...", 95)
                from app.agents.drive_agent import DriveAgent
                v_title = (final_state.get("script_data") or {}).get("title") or title or topic or f"Video_{self.job_id}"
                folder_root = "AI YouTube Long-Form" if final_video_format == "long" else "AI YouTube Videos"
                drv_agent = DriveAgent()
                if drv_agent.service:
                    drive_upload_info = await asyncio.to_thread(
                        drv_agent.upload_video,
                        local_video_path=rendered_video_path,
                        video_title=v_title,
                        channel_name=channel_id,
                        folder_name=folder_root,
                    )
                    if drive_upload_info and drive_upload_info.get("drive_link"):
                        final_state["drive_link"] = drive_upload_info["drive_link"]
                        final_state["drive_folder_link"] = drive_upload_info.get("folder_link")
                        logger.info(f"☁️ [LangGraph Job {self.job_id}] Đã tự động tải lên Google Drive: {drive_upload_info['drive_link']}")
            except Exception as e_drv:
                logger.warning(f"[LangGraph Job {self.job_id}] Tự động tải lên Google Drive không thành công: {e_drv}")

        # 4. Lưu toàn bộ kết quả vào Database (tự động thử lại tối đa 3 lần nếu Cloud DB tạm ngắt kết nối)
        for _db_save_att in range(3):
            try:
                with get_db() as db:
                    j = db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
                    if j:
                        j.video_path = final_state.get("rendered_video_path")
                        j.audio_path = final_state.get("full_audio_path")
                        j.subtitle_path = final_state.get("subtitle_path")
                        j.audio_duration_ms = final_state.get("total_audio_duration_ms")
                        if final_state.get("thumbnail_path"):
                            j.thumbnail_path = final_state.get("thumbnail_path")
                        if final_state.get("drive_link"):
                            j.drive_link = final_state.get("drive_link")
                        
                        if final_state.get("script_data"):
                            s_data = dict(final_state["script_data"])
                            media_map = {m["scene_id"]: m for m in final_state.get("scenes_media", [])}
                            audio_map = {a["scene_id"]: a for a in final_state.get("scenes_audio", [])}
                            enriched_scenes_list = []
                            for sc in s_data.get("scenes", []):
                                sc_copy = dict(sc)
                                sc_id = sc_copy.get("scene_id")
                                if sc_id in media_map:
                                    sc_copy["media_path"] = media_map[sc_id].get("media_path")
                                    sc_copy["media_type"] = media_map[sc_id].get("media_type")
                                    sc_copy["pose_images"] = media_map[sc_id].get("pose_images")
                                    sc_copy["visual_strategy"] = media_map[sc_id].get("visual_strategy")
                                    sc_copy["production_method"] = media_map[sc_id].get("production_method")
                                    sc_copy["tool"] = media_map[sc_id].get("tool")
                                    sc_copy["strategy_rationale"] = media_map[sc_id].get("strategy_rationale")
                                if sc_id in audio_map:
                                    sc_copy["audio_path"] = audio_map[sc_id].get("audio_path")
                                    sc_copy["audio_duration_ms"] = audio_map[sc_id].get("duration_ms")
                                    sc_copy["word_timings"] = audio_map[sc_id].get("word_timings")
                                enriched_scenes_list.append(sc_copy)
                            s_data["scenes"] = enriched_scenes_list
                            j.scenes_json = json.dumps(s_data, ensure_ascii=False)
                            j.title = s_data.get("title", j.title)
                            j.description = s_data.get("description", j.description)
                            # Tự động ghi nguồn các clip Creative Commons (CC BY) đã dùng vào cuối mô tả video
                            try:
                                from app.services.media.license_policy import append_credits_to_description
                                j.description = append_credits_to_description(j.description, self.job_id)
                            except Exception as e_cred:
                                logger.warning(f"[LangGraph Job {self.job_id}] Không thể nối credits CC BY: {e_cred}")
                            if s_data.get("full_narration"):
                                j.script = s_data["full_narration"]
                        
                        # Tổng hợp điểm số chất lượng từ 4 Graders
                        scores_summary = {
                            "script_critic": final_state.get("script_critic") or final_state.get("script_eval"),
                            "visual_critic": final_state.get("media_eval"),
                            "hook_video_grader": final_state.get("hook_video_eval"),
                            "audio_qa": final_state.get("audio_eval"),
                            "video_qa": final_state.get("video_eval"),
                            "video_format": final_video_format,
                            "drive_upload": drive_upload_info,
                        }
                        j.quality_scores = json.dumps(scores_summary, ensure_ascii=False)
                        
                        j.status = "RENDERED"
                        j.progress_percent = 100
                        if final_state.get("drive_link"):
                            j.progress_message = f"✅ Video hoàn thành & đã tải lên Google Drive: {final_state['drive_link']}"
                        else:
                            j.progress_message = "✅ Video hoàn thành xuất sắc! Đã qua 5 tầng kiểm định chất lượng."
                        j.error_message = None
                break
            except Exception as _e_save_final:
                try:
                    from app.database.models import engine
                    engine.dispose()
                except Exception:
                    pass
                if _db_save_att < 2:
                    import time as _t_s
                    _t_s.sleep(0.8 * (_db_save_att + 1))
                else:
                    logger.warning(f"[LangGraph Job {self.job_id}] Không thể lưu metadata cuối vào DB (video vẫn an toàn trên đĩa): {_e_save_final}")

        self._finish_trace("COMPLETED", final_state=final_state)
        logger.info(f"🎉 [LangGraph Job {self.job_id}] Video hoàn thành: {final_state.get('rendered_video_path')}")
        return final_state

    async def execute_edit(self, edit_request: EditRequest) -> Dict[str, Any]:
        """Thực thi chỉnh sửa vi mô (thay ảnh, sửa thoại, đổi giọng) bằng sub-graph."""
        ACTIVE_GRAPH_RUNNERS[self.job_id] = self
        try:
            return await self._execute_edit_internal(edit_request)
        finally:
            ACTIVE_GRAPH_RUNNERS.pop(self.job_id, None)

    async def _execute_edit_internal(self, edit_request: EditRequest) -> Dict[str, Any]:
        await self._report("EDIT_INIT", f"🛠️ Đang thực hiện chỉnh sửa: {edit_request.action_type}...", 10)

        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
            if not job:
                raise ValueError(f"Job {self.job_id} không tồn tại")
            
            # Khôi phục trạng thái gần nhất từ DB
            script_data = json.loads(job.scenes_json) if job.scenes_json else {}
            
            # Tái tạo scenes_media và scenes_audio từ script hoặc đĩa
            scenes = script_data.get("scenes", [])
            scenes_audio = []
            scenes_media = []
            media_dir = self.work_dir / "media"
            audio_dir = self.work_dir / "audio"

            for s in scenes:
                sc_id = s["scene_id"]
                
                # Khôi phục audio path
                a_path = s.get("audio_path")
                if not a_path or not Path(a_path).exists():
                    candidate_a = audio_dir / f"scene_{sc_id:03d}.mp3"
                    if candidate_a.exists():
                        a_path = str(candidate_a)

                scenes_audio.append({
                    "scene_id": sc_id,
                    "narration": s["narration"],
                    "duration_ms": s.get("audio_duration_ms") or s.get("duration_ms", 3000),
                    "audio_path": a_path or "",
                })

                # Khôi phục media path
                m_path = s.get("media_path")
                m_type = s.get("media_type", "image")
                if not m_path or not Path(m_path).exists():
                    found_media = list(media_dir.glob(f"scene_{sc_id:03d}*"))
                    if found_media:
                        # Ưu tiên mp4 > jpg > png
                        sorted_media = sorted(found_media, key=lambda x: (x.suffix == ".mp4", x.stat().st_size), reverse=True)
                        m_path = str(sorted_media[0])
                        m_type = "video" if m_path.lower().endswith(".mp4") else "image"

                scenes_media.append({
                    "scene_id": sc_id,
                    "media_path": m_path or "",
                    "media_type": m_type,
                    "visual_keyword": s.get("visual_keyword", ""),
                })

            initial_state: VideoWorkflowState = {
                "job_id": self.job_id,
                "topic": job.topic or "",
                "title": job.title or "",
                "target_channel_id": job.target_channel_id or "default",
                "video_format": str((script_data if isinstance(script_data, dict) else {}).get("video_format") or "short"),
                "work_dir": str(self.work_dir),
                "script_data": script_data,
                "scenes_audio": scenes_audio,
                "scenes_media": scenes_media,
                "full_audio_path": job.audio_path or str(self.work_dir / "full_voiceover.mp3"),
                "total_audio_duration_ms": job.audio_duration_ms or 0,
                "subtitle_path": job.subtitle_path or str(self.work_dir / "subtitles.srt"),
                "edit_request": edit_request.model_dump(),
            }

        # Kích hoạt đồ thị bắt đầu từ entry point execute_edit
        final_state = initial_state
        async for event in video_graph.astream(initial_state, config=self._get_trace_config(initial_state)):
            for node_name, node_output in event.items():
                if isinstance(node_output, dict):
                    final_state.update(node_output)
                    step = node_output.get("current_step", node_name.upper())
                    pct = node_output.get("progress_percent", 50)
                    msg = node_output.get("progress_message", f"Đang xử lý: {node_name}")
                    await self._report(step, msg, pct)

        # Cập nhật lại video_path & scenes_json sau khi chỉnh sửa
        with get_db() as db:
            j = db.query(VideoJob).filter(VideoJob.id == self.job_id).first()
            if j:
                if final_state.get("rendered_video_path"):
                    j.video_path = final_state["rendered_video_path"]
                if final_state.get("full_audio_path"):
                    j.audio_path = final_state["full_audio_path"]
                if final_state.get("subtitle_path"):
                    j.subtitle_path = final_state["subtitle_path"]
                if final_state.get("total_audio_duration_ms"):
                    j.audio_duration_ms = final_state["total_audio_duration_ms"]

                # Cập nhật scenes_json & thông tin kịch bản
                if final_state.get("script_data"):
                    s_data = dict(final_state["script_data"])
                    if s_data.get("title"):
                        j.title = s_data["title"]
                    if s_data.get("description"):
                        j.description = s_data["description"]
                        try:
                            from app.services.media.license_policy import append_credits_to_description
                            j.description = append_credits_to_description(j.description, self.job_id)
                        except Exception as e_cred:
                            logger.warning(f"[LangGraph Edit {self.job_id}] Không thể nối credits CC BY: {e_cred}")
                    if s_data.get("tags"):
                        j.tags = ",".join(s_data["tags"]) if isinstance(s_data["tags"], list) else s_data["tags"]
                    if s_data.get("full_narration"):
                        j.script = s_data["full_narration"]
                else:
                    s_data = json.loads(j.scenes_json) if j.scenes_json else {}

                media_map = {m["scene_id"]: m for m in final_state.get("scenes_media", [])}
                audio_map = {a["scene_id"]: a for a in final_state.get("scenes_audio", [])}
                for sc in s_data.get("scenes", []):
                    sc_id = sc.get("scene_id")
                    if sc_id in media_map:
                        sc["media_path"] = media_map[sc_id].get("media_path")
                        sc["media_type"] = media_map[sc_id].get("media_type")
                        if media_map[sc_id].get("visual_strategy"):
                            sc["visual_strategy"] = media_map[sc_id].get("visual_strategy")
                        if media_map[sc_id].get("production_method"):
                            sc["production_method"] = media_map[sc_id].get("production_method")
                        if media_map[sc_id].get("tool"):
                            sc["tool"] = media_map[sc_id].get("tool")
                        if media_map[sc_id].get("strategy_rationale"):
                            sc["strategy_rationale"] = media_map[sc_id].get("strategy_rationale")
                    if sc_id in audio_map:
                        sc["audio_path"] = audio_map[sc_id].get("audio_path")
                        sc["audio_duration_ms"] = audio_map[sc_id].get("duration_ms")
                        if audio_map[sc_id].get("narration"):
                            sc["narration"] = audio_map[sc_id]["narration"]

                j.scenes_json = json.dumps(s_data, ensure_ascii=False)
                j.status = "RENDERED"
                j.progress_percent = 100
                j.progress_message = "✅ Đã cập nhật chỉnh sửa video thành công!"

        return final_state
