import os
import json
import base64
import logging
from pathlib import Path
from typing import Optional, Dict
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

class DriveAgent:
    """
    Agent quản lý kho lưu trữ Google Drive 2TB cho từng tài khoản Google Ultra.
    - Tải video hoàn chỉnh lên Google Drive của tài khoản tương ứng
    - Tạo liên kết chia sẻ (Share link) để gửi vào nhóm Telegram
    - Tiết kiệm dung lượng ổ cứng local sau khi tải lên thành công
    """

    def __init__(self, account_name: str = "account_1", token_path: Optional[str] = None):
        self.account_name = account_name
        self.token_path = token_path or f"credentials/accounts/{account_name}/drive_token.json"
        self.service = None
        self._init_service()

    def _init_service(self):
        """Khởi tạo Google Drive service từ file token hoặc base64 env var"""
        try:
            creds = None
            env_b64 = os.getenv("DRIVE_TOKEN_BASE64")
            if env_b64:
                token_data = json.loads(base64.b64decode(env_b64).decode("utf-8"))
                creds = Credentials.from_authorized_user_info(token_data)
            elif os.path.exists(self.token_path):
                with open(self.token_path, "r", encoding="utf-8") as f:
                    token_data = json.load(f)
                creds = Credentials.from_authorized_user_info(token_data)

            if creds:
                self.service = build("drive", "v3", credentials=creds)
                logger.info(f"Google Drive API đã kết nối thành công cho '{self.account_name}'")
            else:
                logger.warning(f"Chưa tìm thấy drive_token hợp lệ cho '{self.account_name}'")
        except Exception as e:
            logger.error(f"Lỗi khởi tạo Google Drive API cho '{self.account_name}': {e}")

    def _get_or_create_folder(self, folder_name: str = "AI YouTube Shorts") -> Optional[str]:
        """Tìm hoặc tạo thư mục trên Drive"""
        if not self.service:
            return None
        try:
            query = f"mimeType = 'application/vnd.google-apps.folder' and name = '{folder_name}' and trashed = false"
            results = self.service.files().list(q=query, fields="files(id, name)").execute()
            folders = results.get("files", [])
            if folders:
                return folders[0]["id"]

            meta = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
            new_folder = self.service.files().create(body=meta, fields="id").execute()
            return new_folder.get("id")
        except Exception as e:
            logger.error(f"Lỗi tìm/tạo thư mục Drive '{folder_name}': {e}")
            return None

    def upload_video(
        self,
        local_video_path: str,
        video_title: str,
        folder_name: str = "AI YouTube Shorts"
    ) -> Dict[str, str]:
        """
        Tải video lên Google Drive và trả về webViewLink (link xem video trực tiếp).
        """
        if not self.service:
            self._init_service()

        if not self.service:
            logger.warning("Không có Google Drive service hợp lệ. Bỏ qua upload Drive.")
            return {
                "status": "skipped",
                "error": "Chưa kết nối Google Drive token",
                "drive_link": None
            }

        logger.info(f"Đang tải video '{video_title}' lên Google Drive ({self.account_name})...")
        try:
            folder_id = self._get_or_create_folder(folder_name)
            file_name = Path(local_video_path).name
            file_metadata = {
                "name": f"{video_title[:80]}.mp4" if not file_name.endswith(".mp4") else file_name,
                "description": f"AI YouTube Shorts: {video_title}"
            }
            if folder_id:
                file_metadata["parents"] = [folder_id]

            media = MediaFileUpload(
                local_video_path,
                mimetype="video/mp4",
                resumable=True,
                chunksize=2 * 1024 * 1024
            )

            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, name, webViewLink, webContentLink"
            ).execute()

            file_id = file.get("id")
            drive_link = file.get("webViewLink")

            # Cấp quyền đọc link công khai cho thành viên nhóm xem video
            try:
                self.service.permissions().create(
                    fileId=file_id,
                    body={"role": "reader", "type": "anyone"}
                ).execute()
            except Exception as e_perm:
                logger.debug(f"Không thể đặt permission anyone (có thể tổ chức chặn): {e_perm}")

            logger.info(f"Tải video lên Google Drive thành công: {drive_link}")
            return {
                "status": "success",
                "file_id": file_id,
                "drive_link": drive_link,
                "account": self.account_name
            }
        except Exception as e:
            logger.error(f"Lỗi khi upload video lên Google Drive: {e}", exc_info=True)
            return {
                "status": "failed",
                "error": str(e),
                "drive_link": None
            }

    def delete_local_file(self, local_path: str):
        """Xóa file tạm trên máy sau khi đã đẩy lên Drive an toàn"""
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
                logger.info(f"Đã dọn dẹp file tạm local: {local_path}")
        except Exception as e:
            logger.error(f"Không thể xóa file tạm {local_path}: {e}")
