import os
import json
import base64
import logging
from pathlib import Path
from typing import Optional, Dict, Any
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
                from app.utils.security import load_encrypted_json
                token_data = load_encrypted_json(self.token_path)
                if token_data:
                    creds = Credentials.from_authorized_user_info(token_data)

            if creds:
                self.service = build("drive", "v3", credentials=creds)
                logger.info(f"Google Drive API đã kết nối thành công cho '{self.account_name}'")
            else:
                logger.warning(f"Chưa tìm thấy drive_token hợp lệ cho '{self.account_name}'")
        except Exception as e:
            logger.error(f"Lỗi khởi tạo Google Drive API cho '{self.account_name}': {e}")

    @staticmethod
    def resolve_channel_name(channel_name_or_id: Optional[str]) -> Optional[str]:
        """Tự động chuyển đổi channel_id (VD: channel_maya) sang tên hiển thị của kênh nếu có."""
        if not channel_name_or_id:
            return None
        try:
            from config.settings import settings
            channels = settings.get_channels_config()
            for ch in channels:
                if ch.get("id") == channel_name_or_id:
                    return ch.get("name") or channel_name_or_id
                if ch.get("name") == channel_name_or_id:
                    return ch.get("name")
        except Exception:
            pass
        return channel_name_or_id

    def _get_or_create_folder(
        self,
        folder_name: str = "AI YouTube Shorts",
        parent_id: Optional[str] = None
    ) -> Optional[str]:
        """Tìm hoặc tạo thư mục trên Drive (hỗ trợ phân cấp theo parent_id)"""
        if not self.service:
            return None
        try:
            safe_name = folder_name.replace("'", "\\'")
            query = f"mimeType = 'application/vnd.google-apps.folder' and name = '{safe_name}' and trashed = false"
            if parent_id:
                query += f" and '{parent_id}' in parents"
            results = self.service.files().list(q=query, fields="files(id, name)").execute()
            folders = results.get("files", [])
            if folders:
                return folders[0]["id"]

            meta = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
            if parent_id:
                meta["parents"] = [parent_id]
            new_folder = self.service.files().create(body=meta, fields="id, name").execute()
            folder_id = new_folder.get("id")

            # Cấp quyền đọc công khai cho thư mục để có thể chia sẻ folder kênh
            try:
                self.service.permissions().create(
                    fileId=folder_id,
                    body={"role": "reader", "type": "anyone"}
                ).execute()
            except Exception as e_p:
                logger.debug(f"Không thể cấp permission anyone cho folder {folder_name}: {e_p}")

            return folder_id
        except Exception as e:
            logger.error(f"Lỗi tìm/tạo thư mục Drive '{folder_name}' (parent={parent_id}): {e}")
            return None

    def get_channel_folder(
        self,
        channel_name_or_id: Optional[str] = None,
        root_folder_name: str = "AI YouTube Shorts"
    ) -> Optional[str]:
        """
        Tìm hoặc tạo cấu trúc thư mục phân cấp theo từng kênh trên Google Drive:
        Root: 'AI YouTube Shorts'
          └── Subfolder: '[Tên Kênh]' (Ví dụ: 'Psychology of the world')
        
        Trả về folder_id của kênh đó (hoặc root_folder_id nếu không có tên kênh).
        """
        resolved_name = self.resolve_channel_name(channel_name_or_id)
        
        # 1. Tìm hoặc tạo thư mục gốc
        root_id = self._get_or_create_folder(root_folder_name)
        if not resolved_name:
            return root_id
        
        if not root_id:
            # Fallback nếu không tạo được root, tạo trực tiếp ở Drive root
            return self._get_or_create_folder(resolved_name)
        
        # 2. Tìm hoặc tạo thư mục con của Kênh bên trong thư mục gốc
        channel_folder_id = self._get_or_create_folder(resolved_name, parent_id=root_id)
        return channel_folder_id or root_id

    def upload_video(
        self,
        local_video_path: str,
        video_title: str,
        channel_name: Optional[str] = None,
        folder_name: str = "AI YouTube Shorts"
    ) -> Dict[str, Any]:
        """
        Tải video lên Google Drive đúng theo thư mục riêng của kênh:
        AI YouTube Shorts / [Tên Kênh] / [Video Title].mp4
        
        Trả về:
          - webViewLink (link xem video trực tiếp)
          - folder_link (link mở thư mục của kênh trên Drive)
          - channel_name (tên kênh đã lưu)
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

        resolved_ch_name = self.resolve_channel_name(channel_name)
        logger.info(
            f"Đang tải video '{video_title}' lên Google Drive ({self.account_name}) "
            f"cho kênh '{resolved_ch_name or 'Chung'}'..."
        )
        try:
            # Lấy đúng thư mục riêng của kênh
            target_folder_id = self.get_channel_folder(
                channel_name_or_id=resolved_ch_name,
                root_folder_name=folder_name
            )

            file_name = Path(local_video_path).name
            clean_title = video_title[:80].strip()
            dest_name = f"{clean_title}.mp4" if not file_name.endswith(".mp4") else file_name
            ch_desc = f" | Kênh: {resolved_ch_name}" if resolved_ch_name else ""
            file_metadata = {
                "name": dest_name,
                "description": f"AI YouTube Shorts: {video_title}{ch_desc}"
            }
            if target_folder_id:
                file_metadata["parents"] = [target_folder_id]

            media = MediaFileUpload(
                local_video_path,
                mimetype="video/mp4",
                resumable=True,
                chunksize=2 * 1024 * 1024
            )

            file = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, name, webViewLink, webContentLink, parents"
            ).execute()

            file_id = file.get("id")
            drive_link = file.get("webViewLink")

            # Cấp quyền đọc link công khai cho video
            try:
                self.service.permissions().create(
                    fileId=file_id,
                    body={"role": "reader", "type": "anyone"}
                ).execute()
            except Exception as e_perm:
                logger.debug(f"Không thể đặt permission anyone (có thể tổ chức chặn): {e_perm}")

            folder_link = f"https://drive.google.com/drive/folders/{target_folder_id}" if target_folder_id else None

            logger.info(
                f"Tải video lên Google Drive thành công: {drive_link} "
                f"(Thư mục kênh: {resolved_ch_name or 'AI YouTube Shorts'})"
            )
            return {
                "status": "success",
                "file_id": file_id,
                "drive_link": drive_link,
                "folder_id": target_folder_id,
                "folder_link": folder_link,
                "channel_name": resolved_ch_name,
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
