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

    DEFAULT_ACCOUNT = "account_2"

    def __init__(self, account_name: Optional[str] = None, token_path: Optional[str] = None):
        self.account_name = account_name or self.DEFAULT_ACCOUNT
        self.token_path = token_path or f"credentials/accounts/{self.account_name}/drive_token.json"
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
                srv = build("drive", "v3", credentials=creds)
                try:
                    srv.files().list(pageSize=1, fields="files(id)").execute()
                    self.service = srv
                    logger.info(f"Google Drive API đã kết nối thành công cho '{self.account_name}'")
                except Exception as ex_test:
                    self.service = None
                    if "invalid_grant" in str(ex_test):
                        if self.account_name == "account_1":
                            logger.warning(f"⚠️ Token '{self.account_name}' bị invalid_grant, tự động chuyển sang 'account_2'...")
                            self.account_name = "account_2"
                            self.token_path = "credentials/accounts/account_2/drive_token.json"
                            self._init_service()
                            return
                        elif self.account_name == "account_2":
                            logger.warning(f"⚠️ Token '{self.account_name}' bị invalid_grant, tự động chuyển sang 'account_3'...")
                            self.account_name = "account_3"
                            self.token_path = "credentials/accounts/account_3/drive_token.json"
                            self._init_service()
                            return
                        else:
                            logger.error(f"⚠️ Tất cả token Google Drive đều đã hết hạn hoặc bị thu hồi (invalid_grant). Hãy vào Web Dashboard -> Tab 'Kênh & Tài Khoản' -> Bấm '☁️ Kết Nối Google Drive' để cấp lại quyền.")
                    else:
                        raise ex_test
            else:
                self.service = None
                logger.warning(f"Chưa tìm thấy drive_token hợp lệ cho '{self.account_name}'")
        except Exception as e:
            self.service = None
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

    def get_folder_by_path(self, path_str: str) -> Optional[str]:
        """
        Tìm hoặc tạo thư mục theo đường dẫn phân cấp.
        Tự động duyệt qua từng cấp thư mục và tạo mới nếu chưa tồn tại.
        """
        if not self.service:
            self._init_service()
        if not self.service:
            return None
        parts = [p.strip() for p in path_str.replace("\\", "/").split("/") if p.strip()]
        parent_id = None
        for part in parts:
            parent_id = self._get_or_create_folder(part, parent_id=parent_id)
            if not parent_id:
                return None
        return parent_id

    def download_file_by_id(self, file_id: str, dest_local_path: str) -> bool:
        """
        Tải file từ Google Drive theo file_id về đường dẫn cục bộ
        """
        if not self.service:
            self._init_service()
        if not self.service:
            return False
        try:
            from googleapiclient.http import MediaIoBaseDownload
            Path(dest_local_path).parent.mkdir(parents=True, exist_ok=True)
            request = self.service.files().get_media(fileId=file_id)
            with open(dest_local_path, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, request, chunksize=1024 * 1024 * 5)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
            return os.path.exists(dest_local_path) and os.path.getsize(dest_local_path) > 0
        except Exception as e:
            logger.error(f"Lỗi khi tải file (ID: {file_id}) về {dest_local_path}: {e}")
            return False


