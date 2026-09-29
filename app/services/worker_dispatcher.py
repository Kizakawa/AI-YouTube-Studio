"""
app.services.worker_dispatcher — Bộ điều phối trung tâm gửi tác vụ sang GPU Workers (P1.1 & P1.2).

Quản lý luồng offload rendering:
1. Đóng gói payload chuẩn hóa: job_id, title, scenes, audio, subtitles, render_settings, callback_url.
2. Điều phối đa kênh GPU:
   - Kênh 1: Google Colab GPU Farm (trực tiếp qua Cloudflare Tunnel HTTP hoặc hàng đợi Google Drive).
   - Kênh 2: Modal.com Serverless GPU (on-demand NVIDIA A10G/T4).
   - Kênh 3: Local Hardware Fallback (Local NVENC hoặc CPU đa luồng).
3. Hỗ trợ Webhook Callback: Nhận thông báo hoàn thành tức thì không cần polling.
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from config.settings import settings
from app.database.models import get_db, VideoJob, log_activity

logger = logging.getLogger(__name__)


class WorkerDispatcher:
    """Bộ điều phối tác vụ GPU Render Farm cho toàn bộ hệ thống."""

    def __init__(self):
        self._colab_client = None

    @property
    def colab_client(self):
        if self._colab_client is None:
            from app.services.colab_client import colab_client
            self._colab_client = colab_client
        return self._colab_client

    def prepare_task_payload(self, job_id: int, callback_url: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Đóng gói VideoJob thành payload chuẩn hóa để gửi tới GPU Worker."""
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
            }

    def get_workers_status(self) -> Dict[str, Any]:
        """Kiểm tra tổng hợp trạng thái các Worker GPU."""
        colab_online, colab_info = self.colab_client.check_health(timeout=1.5)
        modal_configured = bool(os.getenv("MODAL_TOKEN_ID") and os.getenv("MODAL_TOKEN_SECRET"))

        return {
            "colab": {
                "online": colab_online,
                "gpu": colab_info.get("gpu", "NVIDIA GPU") if colab_online else "Offline",
                "worker_url": self.colab_client.worker_url,
            },
            "modal": {
                "configured": modal_configured,
                "gpu": "NVIDIA T4 / A10G" if modal_configured else "Chưa cấu hình",
            },
            "local": {
                "enabled": True,
                "engine": "FFmpeg Local CPU / GPU",
            }
        }

    def dispatch_job(self, job_id: int) -> Tuple[bool, str, str]:
        """
        Điều phối tác vụ render cho 1 VideoJob:
        Ưu tiên: Colab trực tiếp -> Google Drive Task Queue -> Local fallback.
        Returns: (thành_công, thông_báo, worker_type)
        """
        payload = self.prepare_task_payload(job_id)
        if not payload:
            return False, f"Không tìm thấy VideoJob #{job_id}", "none"

        # 1. Nếu Colab đang Online, gửi thông báo hoặc đẩy task
        if self.colab_client.is_online(cache_seconds=5.0):
            logger.info(f"🚀 [WorkerDispatcher] Colab GPU đang trực tuyến. Đẩy task #{job_id} vào Drive Queue...")

        # 2. Đẩy vào Google Drive Queue
        from app.services.colab_queue_service import push_job_to_drive_queue
        ok, msg, drive_link = push_job_to_drive_queue(job_id)
        if ok:
            with get_db() as db:
                log_activity(db, 0, "System", "DISPATCH_WORKER",
                             f"Đã điều phối Job #{job_id} sang GPU Render Farm: {drive_link}",
                             channel_id=payload.get("target_channel_id"))
            return True, f"Đã điều phối sang Colab GPU Farm ({msg})", "colab"

        return False, f"Không thể điều phối tác vụ: {msg}", "local"


worker_dispatcher = WorkerDispatcher()
