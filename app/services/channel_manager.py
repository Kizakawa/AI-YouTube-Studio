import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
import yaml
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow, Flow
from googleapiclient.discovery import build
from config.settings import settings, BASE_DIR

logger = logging.getLogger(__name__)

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/drive.file"
]

class ChannelManager:
    """
    Quản lý danh sách các kênh YouTube, kiểm tra kết nối và xử lý Google OAuth2.
    """

    def __init__(self):
        self.config_path = settings.CHANNELS_CONFIG_FILE
        self.credentials_dir = BASE_DIR / "credentials"
        self.tokens_dir = self.credentials_dir / "tokens"
        self.tokens_dir.mkdir(parents=True, exist_ok=True)

    def _load_raw_config(self) -> Dict:
        if not self.config_path.exists():
            return {"channels": [], "google_accounts": []}
        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {"channels": [], "google_accounts": []}

    def _save_raw_config(self, data: Dict):
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    def get_client_secret_path(self, account_id: str = "account_1") -> Optional[Path]:
        """Tìm file client_secret.json trong credentials hoặc theo account"""
        candidates = [
            self.credentials_dir / "client_secret.json",
            self.credentials_dir / "accounts" / account_id / "client_secret.json",
            BASE_DIR / "client_secret.json"
        ]
        for c in candidates:
            if c.exists():
                return c
        return None

    def get_token_path(self, channel_id: str, google_account: str = "account_1") -> Path:
        """Đường dẫn lưu token của kênh"""
        # Ưu tiên lưu theo channel ID
        return self.tokens_dir / f"channel_{channel_id}.json"

    def list_channels(self) -> List[Dict[str, Any]]:
        """Lấy danh sách tất cả các kênh kèm trạng thái kết nối thực tế"""
        config = self._load_raw_config()
        raw_channels = config.get("channels", [])
        
        # Đồng thời kiểm tra brand_channels trong google_accounts
        for acc in config.get("google_accounts", []):
            acc_id = acc.get("id", "account_1")
            for b in acc.get("brand_channels", []):
                if not any(c.get("id") == b.get("id") for c in raw_channels):
                    b_copy = b.copy()
                    b_copy["google_account"] = acc_id
                    raw_channels.append(b_copy)

        results = []
        for ch in raw_channels:
            ch_id = ch.get("id")
            acc_id = ch.get("google_account", "account_1")
            status_info = self.get_channel_live_status(ch_id, acc_id)
            
            merged = {
                "id": ch_id,
                "name": ch.get("name", ch_id),
                "niche": ch.get("niche", "general"),
                "language": ch.get("language", "en"),
                "google_account": acc_id,
                "visual_mode": ch.get("visual_mode", "imagen_ai"),
                "preferred_voice": ch.get("preferred_voice", "en-US-ChristopherNeural"),
                **status_info
            }
            results.append(merged)
        return results

    def add_channel(
        self,
        channel_id: str,
        name: str,
        niche: str,
        google_account: str = "account_1",
        language: str = "en",
        visual_mode: str = "imagen_ai",
        preferred_voice: str = "en-US-ChristopherNeural"
    ) -> Dict[str, Any]:
        """Thêm một kênh YouTube mới vào hệ thống"""
        clean_id = channel_id.strip().lower().replace(" ", "_")
        config = self._load_raw_config()
        channels = config.get("channels", [])

        # Kiểm tra trùng lặp
        for c in channels:
            if c.get("id") == clean_id:
                raise ValueError(f"Kênh có ID '{clean_id}' đã tồn tại trong hệ thống!")

        new_channel = {
            "id": clean_id,
            "name": name.strip(),
            "niche": niche.strip().lower(),
            "language": language.strip().lower(),
            "google_account": google_account.strip(),
            "visual_mode": visual_mode,
            "preferred_voice": preferred_voice
        }
        channels.append(new_channel)
        config["channels"] = channels
        self._save_raw_config(config)
        logger.info(f"Đã thêm kênh mới thành công: {clean_id} ({name})")
        return new_channel

    def delete_channel(self, channel_id: str) -> bool:
        """Xóa kênh khỏi cấu hình"""
        config = self._load_raw_config()
        channels = config.get("channels", [])
        initial_len = len(channels)
        channels = [c for c in channels if c.get("id") != channel_id]
        
        # Cũng xóa trong brand_channels nếu có
        for acc in config.get("google_accounts", []):
            acc["brand_channels"] = [b for b in acc.get("brand_channels", []) if b.get("id") != channel_id]

        if len(channels) < initial_len:
            config["channels"] = channels
            self._save_raw_config(config)
            # Xóa file token nếu có
            token_file = self.get_token_path(channel_id)
            if token_file.exists():
                token_file.unlink()
            return True
        return False

    def get_channel_live_status(self, channel_id: str, google_account: str = "account_1") -> Dict[str, Any]:
        """
        Kiểm tra token OAuth2 và truy vấn YouTube Data API v3 thực tế để lấy:
        - Tên kênh thật, Avatar, Số lượng subscribers, Số video đã đăng
        """
        token_path = self.get_token_path(channel_id, google_account)
        if not token_path.exists():
            return {
                "is_authenticated": False,
                "status_badge": "Chưa kết nối 🔴",
                "avatar_url": None,
                "subscribers": 0,
                "video_count": 0,
                "real_title": None,
                "custom_url": None
            }

        try:
            creds = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())

            youtube = build("youtube", "v3", credentials=creds)
            res = youtube.channels().list(mine=True, part="snippet,statistics").execute()
            items = res.get("items", [])
            if items:
                snippet = items[0].get("snippet", {})
                stats = items[0].get("statistics", {})
                return {
                    "is_authenticated": True,
                    "status_badge": "Đã kết nối 🟢",
                    "avatar_url": snippet.get("thumbnails", {}).get("default", {}).get("url"),
                    "subscribers": int(stats.get("subscriberCount", 0)),
                    "video_count": int(stats.get("videoCount", 0)),
                    "real_title": snippet.get("title"),
                    "custom_url": snippet.get("customUrl")
                }
        except Exception as e:
            logger.warning(f"Không thể kiểm tra YouTube API cho kênh {channel_id}: {e}")

        return {
            "is_authenticated": False,
            "status_badge": "Hết hạn / Lỗi token 🟡",
            "avatar_url": None,
            "subscribers": 0,
            "video_count": 0,
            "real_title": None,
            "custom_url": None
        }

    def start_oauth_flow(self, channel_id: str, redirect_uri: str) -> str:
        """Tạo đường link xác thực Google OAuth2 để người dùng click đăng nhập"""
        secret_path = self.get_client_secret_path()
        if not secret_path:
            raise FileNotFoundError(
                "Chưa tìm thấy file 'client_secret.json'! "
                "Hãy tải file OAuth 2.0 từ Google Cloud Console và đặt vào thư mục credentials/client_secret.json"
            )

        flow = Flow.from_client_secrets_file(
            str(secret_path),
            scopes=YOUTUBE_SCOPES,
            redirect_uri=redirect_uri
        )
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=channel_id
        )
        return auth_url

    def finish_oauth_flow(self, channel_id: str, code: str, redirect_uri: str) -> Dict[str, Any]:
        """Nhận Authorization Code từ Google và lưu thành file token"""
        secret_path = self.get_client_secret_path()
        flow = Flow.from_client_secrets_file(
            str(secret_path),
            scopes=YOUTUBE_SCOPES,
            redirect_uri=redirect_uri
        )
        flow.fetch_token(code=code)
        creds = flow.credentials

        token_path = self.get_token_path(channel_id)
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

        logger.info(f"Đã lưu token thành công cho kênh: {channel_id}")
        return self.get_channel_live_status(channel_id)

channel_manager = ChannelManager()
