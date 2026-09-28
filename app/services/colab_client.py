"""
Colab GPU Render Farm Client
Kết nối hệ thống cục bộ tới GPU Worker chạy trên Google Colab Pro+ qua Cloudflare Tunnel / Ngrok.
Tăng tốc độ render video lên gấp 10 lần (NVENC Hardware Acceleration) và hỗ trợ WhisperX phụ đề 1 giây.
Có cơ chế Smart Failover: Tự động phát hiện khi Colab offline để chuyển về Render Local an toàn.
"""
import os
import json
import time
import asyncio
import logging
import requests
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List, Callable

from config.settings import settings
from app.schemas.contracts import RenderJobContract

logger = logging.getLogger(__name__)


class ColabClient:
    """
    Client kết nối và điều phối tác vụ sang Google Colab GPU Farm.
    """

    def __init__(self, worker_url: Optional[str] = None):
        self._custom_url = worker_url
        self._last_health_check_time = 0.0
        self._cached_health_status = False
        self._cached_gpu_info = {}

    @property
    def worker_url(self) -> str:
        """Lấy URL của Colab Worker từ cấu hình runtime hoặc .env"""
        url = self._custom_url or settings.COLAB_WORKER_URL or os.getenv("COLAB_WORKER_URL", "")
        return url.strip().rstrip("/")

    def set_worker_url(self, url: str) -> bool:
        """Cập nhật URL Worker Colab mới và lưu vào .env"""
        clean_url = url.strip().rstrip("/")
        self._custom_url = clean_url
        self._last_health_check_time = 0.0
        return settings.set_colab_url(clean_url)

    def check_health(self, timeout: float = 3.5) -> Tuple[bool, Dict[str, Any]]:
        """
        Kiểm tra trạng thái kết nối tới Colab GPU Worker.
        Trả về: (is_online, gpu_info)
        """
        url = self.worker_url
        if not url or not url.startswith("http"):
            return False, {"error": "Chưa cấu hình URL Colab Worker"}

        try:
            resp = requests.get(f"{url}/health", timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                self._cached_health_status = True
                self._cached_gpu_info = data
                self._last_health_check_time = time.time()
                return True, data
            else:
                self._cached_health_status = False
                return False, {"error": f"HTTP {resp.status_code}"}
        except Exception as e:
            self._cached_health_status = False
            return False, {"error": str(e)}

    def is_online(self, cache_seconds: float = 15.0) -> bool:
        """
        Kiểm tra nhanh xem Colab có đang Online không (có cache ngắn hạn để tránh spam request).
        """
        if not self.worker_url:
            return False

        now = time.time()
        if (now - self._last_health_check_time) < cache_seconds and self._cached_health_status:
            return True

        online, _ = self.check_health(timeout=3.0)
        return online

    def get_gpu_name(self) -> str:
        """Lấy tên GPU đang chạy trên Colab (ví dụ: NVIDIA Tesla T4, A100, L4)"""
        if self._cached_gpu_info and "gpu" in self._cached_gpu_info:
            return self._cached_gpu_info.get("gpu", "NVIDIA GPU")
        online, info = self.check_health(timeout=2.0)
        if online:
            return info.get("gpu", "NVIDIA GPU")
        return "Offline"

    async def transcribe_audio(self, audio_path: str) -> Optional[List[Dict[str, Any]]]:
        """
        Gửi file audio sang Colab để trích xuất timestamp phụ đề chính xác từng từ bằng WhisperX GPU.
        """
        url = self.worker_url
        if not self.is_online():
            return None

        p = Path(audio_path)
        if not p.exists():
            return None

        def _do_upload():
            try:
                with open(p, "rb") as f:
                    files = {"file": (p.name, f, "audio/mpeg")}
                    res = requests.post(f"{url}/api/whisper", files=files, timeout=60)
                    if res.status_code == 200:
                        return res.json().get("words", [])
            except Exception as e:
                logger.warning(f"Lỗi khi gọi WhisperX trên Colab: {e}")
            return None

        return await asyncio.to_thread(_do_upload)

    async def render_remote(
        self,
        job: RenderJobContract,
        progress_callback: Optional[Callable[[str, int], Any]] = None
    ) -> Optional[str]:
        """
        Gửi toàn bộ hợp đồng dựng hình (RenderJobContract) sang Colab GPU Farm để render NVENC.
        Nếu thành công: Trả về đường dẫn file MP4 hoàn chỉnh.
        Nếu Colab gặp lỗi hoặc ngắt kết nối: Trả về None để hệ thống tự động fallback về máy Local.
        """
        url = self.worker_url
        if not self.is_online():
            logger.info("Colab GPU không trực tuyến. Bỏ qua remote render, chuyển sang Local.")
            return None

        if progress_callback:
            try:
                await progress_callback(f"🚀 Đang gửi tác vụ #{job.job_id} sang Colab GPU Farm ({self.get_gpu_name()})...", 5)
            except Exception:
                pass

        # Chuẩn bị payload dữ liệu
        payload = {
            "job_id": job.job_id,
            "title": job.title,
            "total_duration_ms": job.total_audio_duration_ms,
            "scenes": [
                {
                    "scene_id": s.scene_id,
                    "narration": s.narration,
                    "media_path": s.media_path,
                    "media_type": s.media_type,
                    "estimated_seconds": s.estimated_seconds,
                    "camera_motion": s.camera_motion,
                    "sfx_cue": s.sfx_cue
                }
                for s in job.scenes
            ],
            "audio_path": job.full_audio_path,
            "subtitle_path": job.subtitle_path
        }

        # Kiểm tra xem có file audio cần upload lên Colab không
        files_to_send = {}
        if job.full_audio_path and os.path.exists(job.full_audio_path):
            files_to_send["audio"] = (Path(job.full_audio_path).name, open(job.full_audio_path, "rb"), "audio/mpeg")
        if job.subtitle_path and os.path.exists(job.subtitle_path):
            files_to_send["subtitles"] = (Path(job.subtitle_path).name, open(job.subtitle_path, "rb"), "text/plain")

        def _execute_colab_request():
            try:
                data_field = {"task_json": json.dumps(payload, ensure_ascii=False)}
                res = requests.post(
                    f"{url}/api/render",
                    data=data_field,
                    files=files_to_send,
                    timeout=300  # Timeout 5 phút
                )

                # Đóng các file upload
                for _, f_tuple in files_to_send.items():
                    try:
                        f_tuple[1].close()
                    except Exception:
                        pass

                if res.status_code == 200:
                    # Kiểm tra xem Colab trả về file video nhị phân hay JSON link Drive
                    content_type = res.headers.get("content-type", "")
                    out_path = Path(job.output_path)
                    out_path.parent.mkdir(parents=True, exist_ok=True)

                    if "video/mp4" in content_type or "application/octet-stream" in content_type:
                        # Ghi trực tiếp stream video vào output_path
                        with open(out_path, "wb") as f_out:
                            f_out.write(res.content)
                        if out_path.exists() and out_path.stat().st_size > 1000:
                            return str(out_path)
                    else:
                        resp_data = res.json()
                        download_url = resp_data.get("download_url")
                        drive_path = resp_data.get("drive_path")

                        if download_url:
                            # Tải video từ download_url của Colab
                            d_res = requests.get(download_url, timeout=120)
                            if d_res.status_code == 200:
                                with open(out_path, "wb") as f_out:
                                    f_out.write(d_res.content)
                                if out_path.exists() and out_path.stat().st_size > 1000:
                                    return str(out_path)

                        if drive_path and os.path.exists(drive_path):
                            return drive_path

                logger.warning(f"Colab trả về mã lỗi: {res.status_code} - {res.text[:200]}")
            except Exception as ex:
                logger.error(f"Lỗi trong quá trình render trên Colab: {ex}")
            return None

        result_path = await asyncio.to_thread(_execute_colab_request)

        if result_path:
            if progress_callback:
                try:
                    await progress_callback(f"🎉 Colab GPU Farm đã hoàn tất xuất sắc Video #{job.job_id}!", 100)
                except Exception:
                    pass
            return result_path
        else:
            logger.warning(f"Remote render thất bại cho Job #{job.job_id}. Tự động chuyển tiếp sang Local FFmpeg Engine...")
            return None


# Singleton instance toàn hệ thống
colab_client = ColabClient()
