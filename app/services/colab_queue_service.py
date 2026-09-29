"""
app.services.colab_queue_service — Điều phối hàng đợi Render giữa Bot Telegram và Google Colab GPU Farm.

Chức năng:
1. Đóng gói kịch bản thành task_{job_id}.json và tự động đẩy vào Google Drive (/AI_YouTube_RenderFarm/pending/).
2. Quét kiểm tra video đã hoàn thành (/AI_YouTube_RenderFarm/completed/video_{job_id}.mp4) để tải về và cập nhật SQLite.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from app.agents.drive_agent import DriveAgent
from app.database.models import get_db, VideoJob, log_activity
from config.settings import settings

logger = logging.getLogger(__name__)


def push_job_to_drive_queue(job_id: int) -> Tuple[bool, str, Optional[str]]:
    """
    Đóng gói kịch bản của VideoJob thành task_{job_id}.json và tải lên Google Drive.
    Trả về: (thành_công, thông_báo, link_drive)
    """
    try:
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not job:
                return False, f"Không tìm thấy VideoJob #{job_id}", None

            # Phân giải kịch bản & phân cảnh
            scenes_data = []
            full_narration = job.script or ""
            if job.scenes_json:
                try:
                    s_json = json.loads(job.scenes_json)
                    if isinstance(s_json, dict):
                        scenes_data = s_json.get("scenes", [])
                        full_narration = s_json.get("full_narration", full_narration)
                    elif isinstance(s_json, list):
                        scenes_data = s_json
                except Exception as ex_json:
                    logger.debug(f"Không thể parse scenes_json #{job_id}: {ex_json}")

            # Đọc cấu hình kênh (ngôn ngữ, giọng đọc)
            ch_cfg = {}
            try:
                import yaml
                with open("config/channels.yaml", "r", encoding="utf-8") as f_cfg:
                    cfg_data = yaml.safe_load(f_cfg) or {}
                    for ch in cfg_data.get("channels", []):
                        if ch.get("id") == job.target_channel_id:
                            ch_cfg = ch
                            break
            except Exception:
                pass

            ch_name = ch_cfg.get("name") or job.target_channel_id or "YouTube Shorts"
            ch_lang = ch_cfg.get("language") or "en"
            ch_voice = ch_cfg.get("voice") or ("en-US-AndrewNeural" if ch_lang == "en" else "vi-VN-NamMinhNeural")

            # Lấy 1 Pexels Key hợp lệ để Colab tải stock B-roll
            pexels_keys = settings.get_pexels_keys_pool()
            valid_px = [k for k in pexels_keys if k and k != "your_pexels_api_key_here"]
            active_px_key = valid_px[0] if valid_px else ""

            # Thu thập danh sách chat telegram để Colab có thể bắn tin nhắn báo hoàn thành ngay
            notify_chats = set()
            if job.user_id:
                notify_chats.add(job.user_id)
            for gid in settings.get_allowed_group_ids():
                notify_chats.add(gid)
            if not notify_chats:
                for u in settings.get_allowed_users().keys():
                    notify_chats.add(u)

            task_payload = {
                "job_id": job.id,
                "topic": job.topic,
                "title": job.title or job.topic,
                "target_channel_id": job.target_channel_id,
                "channel_name": ch_name,
                "language": ch_lang,
                "voice": ch_voice,
                "full_narration": full_narration,
                "description": job.description or "",
                "tags": job.tags or "",
                "scenes": scenes_data,
                "pexels_api_key": active_px_key,
                "pixabay_api_key": settings.PIXABAY_API_KEY,
                "telegram_bot_token": settings.TELEGRAM_BOT_TOKEN,
                "telegram_chat_ids": list(notify_chats),
                "created_at": datetime.utcnow().isoformat(),
                "status": "pending"
            }

            # Đẩy lên Drive
            drive_agent = DriveAgent()
            res = drive_agent.upload_task_json(task_payload, filename=f"task_{job.id}.json")

            if res.get("status") == "success":
                job.status = "PENDING_COLAB"
                job.progress_percent = 25
                job.progress_message = "Đã lưu kịch bản vào Google Drive, chờ Colab Pro+ GPU render"
                log_activity(db, job.user_id or 0, "System", "PUSH_COLAB_QUEUE",
                             f"Đã đẩy task kịch bản #{job_id} vào Google Drive Render Farm: {res.get('drive_link')}",
                             channel_id=job.target_channel_id)
                db.commit()
                return True, "Đã đẩy kịch bản vào hàng đợi Google Drive thành công!", res.get("drive_link")
            else:
                err = res.get("error", "Lỗi không xác định khi kết nối Drive")
                return False, f"Không thể đẩy task lên Drive: {err}", None

    except Exception as e:
        logger.error(f"Lỗi push_job_to_drive_queue #{job_id}: {e}", exc_info=True)
        return False, str(e), None


def check_and_import_completed_video(job_id: int) -> Tuple[bool, str, Optional[str]]:
    """
    Kiểm tra thư mục /AI_YouTube_RenderFarm/completed/ trên Drive.
    Nếu thấy video_{job_id}.mp4:
    - Tải bản sao về storage/outputs/video_{job_id}.mp4
    - Cập nhật (hoặc tự động khôi phục) VideoJob trong DB sang RENDERED
    - Trả về: (thành_công, đường_dẫn_local, link_drive)
    """
    try:
        drive_agent = DriveAgent()
        video_info = drive_agent.get_completed_video_by_job_id(job_id)
        if not video_info:
            return False, f"Chưa tìm thấy video_{job_id}.mp4 trên Google Drive (Colab đang xử lý hoặc chưa chạy).", None

        drive_file_id = video_info.get("id")
        drive_link = video_info.get("webViewLink")

        # Tải file về thư mục outputs cục bộ
        local_out_dir = Path("storage/outputs")
        local_out_dir.mkdir(parents=True, exist_ok=True)
        local_video_path = str(local_out_dir / f"video_{job_id}.mp4")

        # Nếu file local chưa có hoặc nhỏ hơn 1000 bytes, tải từ Drive
        if not os.path.exists(local_video_path) or os.path.getsize(local_video_path) < 1000:
            ok_down = drive_agent.download_file_by_id(drive_file_id, local_video_path)
            if not ok_down:
                return False, f"Tìm thấy file trên Drive nhưng tải về thất bại: {drive_link}", drive_link

        # Cập nhật cơ sở dữ liệu
        with get_db() as db:
            job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
            if not job:
                # Tự động đọc lại metadata từ task_{job_id}.json trên Drive để tái tạo VideoJob nếu DB bị rỗng
                meta_info = drive_agent.read_task_json_by_job_id(job_id) or {}
                job = VideoJob(
                    id=job_id,
                    user_id=meta_info.get("user_id") or 0,
                    target_channel_id=meta_info.get("target_channel_id", "channel_maya"),
                    topic=meta_info.get("topic", f"Video #{job_id}"),
                    title=meta_info.get("title", f"Video #{job_id}"),
                    script=meta_info.get("full_narration", ""),
                    scenes_json=json.dumps(meta_info.get("scenes", [])) if meta_info.get("scenes") else None,
                    status="RENDERED",
                    video_path=local_video_path,
                    drive_link=drive_link,
                    progress_percent=100,
                    progress_message="🎉 Video đã render hoàn tất từ Colab GPU Farm!"
                )
                db.add(job)
                log_activity(db, 0, "System", "COLAB_RENDER_COMPLETE",
                             f"Đã tự động khôi phục và đồng bộ video #{job_id} từ Colab GPU Farm: {local_video_path}",
                             channel_id=job.target_channel_id)
                db.commit()
            else:
                job.status = "RENDERED"
                job.video_path = local_video_path
                job.drive_link = drive_link
                job.progress_percent = 100
                job.progress_message = "🎉 Video đã render hoàn tất từ Colab GPU Farm!"
                log_activity(db, job.user_id or 0, "System", "COLAB_RENDER_COMPLETE",
                             f"Hoàn tất đồng bộ video #{job_id} từ Colab GPU Farm: {local_video_path}",
                             channel_id=job.target_channel_id)
                db.commit()

        return True, local_video_path, drive_link

    except Exception as e:
        logger.error(f"Lỗi check_and_import_completed_video #{job_id}: {e}", exc_info=True)
        return False, str(e), None
