"""
app.services.worker_dispatcher — Bộ điều phối trung tâm gửi tác vụ sang Local Hardware-Accelerated Render Pipeline.

Quản lý luồng điều phối rendering:
1. Đóng gói payload chuẩn hóa: job_id, title, scenes, audio, subtitles, render_settings, callback_url.
2. Điều phối sang Local Hardware Acceleration (NVIDIA NVENC / MediaFoundation / Intel QSV / CPU).
"""

import os
import json
import logging
from typing import Dict, Any, Optional, Tuple

from config.settings import settings
from app.database.models import get_db, VideoJob, log_activity

logger = logging.getLogger(__name__)


class WorkerDispatcher:
    """Bộ điều phối tác vụ Local Render cho toàn bộ hệ thống."""

    def prepare_task_payload(self, job_id: int, callback_url: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Đóng gói VideoJob thành payload chuẩn hóa."""
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not job:
                return None

            scenes_list = []
            if job.scenes_json:
                try:
                    s_data = json.loads(job.scenes_json)
                    if isinstance(s_data, dict):
                        scenes_list = s_data.get("scenes", [])
                    elif isinstance(s_data, list):
                        scenes_list = s_data
                except Exception as ex:
                    logger.debug(f"Không thể parse scenes_json #{job_id}: {ex}")

            base_webhook = getattr(settings, 'WEBHOOK_BASE_URL', '') or os.getenv('WEBHOOK_BASE_URL', '')
            effective_callback = callback_url or (f"{base_webhook.rstrip('/')}/api/worker/callback" if base_webhook else "")

            return {
                "job_id": job.id,
                "title": job.title or job.topic or f"Shorts #{job.id}",
                "topic": job.topic or "",
                "scenes": scenes_list,
                "full_narration": job.script or "",
                "audio_path": getattr(job, "audio_path", "") or "",
                "subtitle_path": getattr(job, "subtitle_path", "") or "",
                "target_channel_id": job.target_channel_id,
                "render_settings": {
                    "resolution": "1080x1920",
                    "fps": 30,
                    "use_nvenc": True,
                    "vcodec": "h264_nvenc"
                },
                "callback_url": effective_callback,
                "worker_token": getattr(settings, "AGENT_API_KEY", "") or "",
            }

    @staticmethod
    def _get_local_engine_name() -> str:
        try:
            from app.utils.ffmpeg_helper import detect_hardware_encoder
            info = detect_hardware_encoder()
            return f"FFmpeg Local ({info.get('display_name', 'GPU/CPU')})"
        except Exception:
            return "FFmpeg Local CPU / GPU"

    def get_workers_status(self) -> Dict[str, Any]:
        """Kiểm tra trạng thái Local Hardware Render Engine."""
        return {
            "local": {
                "enabled": True,
                "engine": self._get_local_engine_name(),
            }
        }

    def dispatch_job(self, job_id: int) -> Tuple[bool, str, str]:
        """
        Điều phối tác vụ render cho 1 VideoJob sang Local AI Render Pipeline.
        Returns: (thành_công, thông_báo, worker_type)
        """
        payload = self.prepare_task_payload(job_id)
        if not payload:
            return False, f"Không tìm thấy VideoJob #{job_id}", "none"

        with get_db() as db:
            log_activity(db, 0, "System", "DISPATCH_WORKER",
                         f"Đã điều phối Job #{job_id} sang Local AI Render Pipeline",
                         channel_id=payload.get("target_channel_id"))
        return True, f"Đã điều phối sang Local AI Render Pipeline cho Job #{job_id}", "local"


worker_dispatcher = WorkerDispatcher()
