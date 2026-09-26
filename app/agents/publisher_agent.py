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
        
        ch_token = f"credentials/channels/{self.channel_id}/youtube_token.json"
        acc_token = f"credentials/accounts/{self.account_name}/youtube_token.json"

        # Kiểm tra xem kênh này có phải kênh đầu tiên (mặc định) của account không
        is_first_ch = False
        try:
            from app.utils.config_loader import load_config
            cfg = load_config("channels.yaml")
            accounts = cfg.get("google_accounts", [])
            for a in accounts:
                if a.get("id") == self.account_name:
                    b_chs = a.get("brand_channels", [])
                    if b_chs and b_chs[0].get("id") == self.channel_id:
                        is_first_ch = True
                    break
        except Exception:
            pass

        if self.channel_config.get("token_file") and os.path.exists(self.channel_config["token_file"]):
            self.token_file = self.channel_config["token_file"]
        elif os.path.exists(ch_token):
            self.token_file = ch_token
        elif is_first_ch and os.path.exists(acc_token):
            # Chỉ kênh đầu tiên được kế thừa token cấp tài khoản cũ
            self.token_file = acc_token
        else:
            # Các kênh khác bắt buộc phải có token riêng, KHÔNG ĐƯỢC dùng chung token
            # để tránh trường hợp đăng nhầm sang kênh cũ!
            self.token_file = None

        self.service = None
        self._init_service()

    def _init_service(self):
        """Khởi tạo YouTube v3 client qua OAuth credentials"""
        if not self.token_file:
            logger.warning(
                f"Kênh '{self.channel_name}' (ID: {self.channel_id}) chưa có token YouTube riêng. "
                f"Hệ thống chặn không dùng token kênh khác để tránh đăng nhầm video!"
            )
            return

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
        privacy_status: str = "public",
        default_language: Optional[str] = "en",
        default_audio_language: Optional[str] = "en",
    ) -> Dict:
        """
        Upload video lên YouTube qua YouTube Data API v3.
        Nếu có schedule_time_iso (ví dụ '2026-09-25T19:00:00Z'), video sẽ được đặt ở chế độ private và lên lịch công chiếu.
        Đã bổ sung defaultLanguage & defaultAudioLanguage để định tuyến thuật toán đề xuất chính xác vùng người xem.
        """
        import re
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

        # Khử hậu tố kênh/quốc gia rò rỉ (ví dụ " - Maya Leader VN" hay " - VN")
        clean_title = re.sub(
            r'[\[\(\s\-_|:]+[\w\s]*?\b(?:VN|VI|Vietnamese|Vietnam)\b.*$',
            '',
            title,
            flags=re.IGNORECASE
        ).strip()
        if not clean_title:
            clean_title = title

        logger.info(f"Bắt đầu upload video '{clean_title}' lên kênh {self.channel_name}...")

        tag_list = []
        if isinstance(tags, list):
            tag_list = tags
        elif isinstance(tags, str):
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]

        body = {
            "snippet": {
                "title": clean_title[:100],
                "description": description[:5000],
                "tags": tag_list[:20],
                "categoryId": "22"  # People & Blogs / Shorts
            },
            "status": {
                "privacyStatus": "private" if schedule_time_iso else privacy_status,
                "selfDeclaredMadeForKids": False
            }
        }

        if default_language:
            body["snippet"]["defaultLanguage"] = default_language
        if default_audio_language:
            body["snippet"]["defaultAudioLanguage"] = default_audio_language

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
