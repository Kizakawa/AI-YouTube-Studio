import os
import json
import base64
import logging
from pathlib import Path
from typing import Optional, Dict, List
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

class PublisherAgent:
    """Agent chịu trách nhiệm xác thực OAuth2 và đăng tải/lên lịch video lên các kênh YouTube."""

    def __init__(self, channel_config: Optional[Dict] = None, account_name: str = "account_1"):
        self.channel_config = channel_config or {}
        self.channel_id = self.channel_config.get("id", "default")
        self.channel_name = self.channel_config.get("name", "YouTube Channel")
        self.account_name = self.channel_config.get("google_account", account_name)
        
        self.token_file = (
            self.channel_config.get("token_file") or
            f"credentials/accounts/{self.account_name}/youtube_token.json"
        )
        self.service = None
        self._init_service()

    def _init_service(self):
        """Khởi tạo YouTube v3 client qua OAuth credentials"""
        try:
            creds = None
            env_b64 = os.getenv("YOUTUBE_TOKEN_BASE64")
            if env_b64:
                token_data = json.loads(base64.b64decode(env_b64).decode("utf-8"))
                creds = Credentials.from_authorized_user_info(token_data)
            elif os.path.exists(self.token_file):
                with open(self.token_file, "r", encoding="utf-8") as f:
                    token_data = json.load(f)
                creds = Credentials.from_authorized_user_info(token_data)

            if creds:
                # Tự động refresh token nếu hết hạn
                if creds.expired and creds.refresh_token:
                    from google.auth.transport.requests import Request
                    creds.refresh(Request())
                    # Lưu lại token đã refresh
                    if os.path.exists(self.token_file):
                        with open(self.token_file, "w", encoding="utf-8") as f:
                            f.write(creds.to_json())
                        logger.info(f"Đã tự động refresh YouTube token cho '{self.channel_name}'")

                self.service = build("youtube", "v3", credentials=creds)
                logger.info(f"YouTube Data API v3 đã kết nối thành công cho '{self.channel_name}'")
            else:
                logger.warning(f"Chưa tìm thấy youtube_token hợp lệ cho '{self.token_file}'")
        except Exception as e:
            logger.error(f"Lỗi khởi tạo YouTube API cho '{self.channel_name}': {e}")

    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: Optional[List[str]] = None,
        schedule_time_iso: Optional[str] = None,
        privacy_status: str = "public"
    ) -> Dict:
        """
        Upload video lên YouTube qua YouTube Data API v3.
        Nếu có schedule_time_iso (ví dụ '2026-09-25T19:00:00Z'), video sẽ được đặt ở chế độ private và lên lịch công chiếu.
        """
        if not self.service:
            self._init_service()

        if not self.service:
            logger.error(f"Không thể kết nối YouTube API để upload: {self.token_file}")
            return {
                "status": "failed",
                "error": "Chưa kết nối YouTube OAuth token",
                "video_id": None
            }

        if not os.path.exists(video_path):
            return {
                "status": "failed",
                "error": f"File video không tồn tại: {video_path}",
                "video_id": None
            }

        logger.info(f"Bắt đầu upload video '{title}' lên kênh {self.channel_name}...")

        tag_list = []
        if isinstance(tags, list):
            tag_list = tags
        elif isinstance(tags, str):
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tag_list[:20],
                "categoryId": "22"  # People & Blogs / Shorts
            },
            "status": {
                "privacyStatus": "private" if schedule_time_iso else privacy_status,
                "selfDeclaredMadeForKids": False
            }
        }

        if schedule_time_iso:
            body["status"]["publishAt"] = schedule_time_iso

        try:
            media = MediaFileUpload(
                video_path,
                mimetype="video/mp4",
                chunksize=1024 * 1024,
                resumable=True
            )

            request = self.service.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    logger.debug(f"Đang upload video lên YouTube: {progress}%")

            video_id = response.get("id")
            youtube_url = f"https://youtu.be/{video_id}"
            logger.info(f"Đã upload video thành công lên YouTube: {youtube_url}")

            return {
                "status": "success",
                "video_id": video_id,
                "youtube_url": youtube_url,
                "channel": self.channel_name,
                "scheduled_at": schedule_time_iso
            }

        except Exception as e:
            logger.error(f"Lỗi khi upload video lên YouTube: {e}", exc_info=True)
            return {
                "status": "failed",
                "error": str(e),
                "video_id": None
            }
