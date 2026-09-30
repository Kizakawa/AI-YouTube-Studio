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
                try:
                    self.service.files().list(pageSize=1, fields="files(id)").execute()
                    logger.info(f"Google Drive API đã kết nối thành công cho '{self.account_name}'")
                except Exception as ex_test:
                    if "invalid_grant" in str(ex_test) and self.account_name == "account_1":
                        logger.warning(f"⚠️ Token '{self.account_name}' bị invalid_grant, tự động chuyển sang 'account_2'...")
                        self.account_name = "account_2"
                        self.token_path = "credentials/accounts/account_2/drive_token.json"
                        self._init_service()
                        return
                    else:
                        raise ex_test
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

    def read_text_file(self, filename: str, folder_name: str = "AI_YouTube_RenderFarm") -> Optional[str]:
        """Đọc nội dung văn bản của một file trong thư mục Drive (ví dụ: colab_url.txt)"""
        if not self.service:
            self._init_service()
        if not self.service:
            return None
        try:
            folder_id = self._get_or_create_folder(folder_name)
            if not folder_id:
                return None
            safe_name = filename.replace("'", "\\'")
            q = f"name = '{safe_name}' and '{folder_id}' in parents and trashed = false"
            res = self.service.files().list(q=q, fields="files(id, name)").execute()
            files = res.get("files", [])
            if not files:
                return None
            file_id = files[0]["id"]
            content_bytes = self.service.files().get_media(fileId=file_id).execute()
            return content_bytes.decode("utf-8").strip()
        except Exception as e:
            logger.debug(f"Không thể đọc file {filename} từ Drive: {e}")
            return None

    def get_folder_by_path(self, path_str: str) -> Optional[str]:
        """
        Tìm hoặc tạo thư mục theo đường dẫn phân cấp (ví dụ: 'AI_YouTube_RenderFarm/pending').
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

    def upload_task_json(
        self,
        task_dict: dict,
        filename: Optional[str] = None,
        folder_path: str = "AI_YouTube_RenderFarm/pending"
    ) -> Dict[str, Any]:
        """
        Đẩy file task_{job_id}.json vào Google Drive hàng đợi cho Colab GPU Farm quét.
        Nếu file đã tồn tại thì tự động ghi đè bản mới nhất.
        """
        if not self.service:
            self._init_service()
        if not self.service:
            return {"status": "skipped", "error": "Chưa kết nối Google Drive"}

        try:
            import io
            from googleapiclient.http import MediaIoBaseUpload

            folder_id = self.get_folder_by_path(folder_path)
            if not folder_id:
                return {"status": "failed", "error": f"Không thể tạo thư mục Drive '{folder_path}'"}

            job_id = task_dict.get("job_id", "unknown")
            fname = filename or f"task_{job_id}.json"

            # Xóa bản cũ nếu có để tránh trùng lặp
            safe_name = fname.replace("'", "\\'")
            q = f"name = '{safe_name}' and '{folder_id}' in parents and trashed = false"
            res = self.service.files().list(q=q, fields="files(id)").execute()
            for old_f in res.get("files", []):
                try:
                    self.service.files().delete(fileId=old_f["id"]).execute()
                except Exception:
                    pass

            json_bytes = json.dumps(task_dict, ensure_ascii=False, indent=2).encode("utf-8")
            media = MediaIoBaseUpload(io.BytesIO(json_bytes), mimetype="application/json", resumable=False)
            meta = {
                "name": fname,
                "parents": [folder_id],
                "description": f"AI Render Task #{job_id}: {task_dict.get('title', '')}"
            }
            created = self.service.files().create(body=meta, media_body=media, fields="id, name, webViewLink").execute()
            logger.info(f"Đã đẩy thành công task kịch bản #{job_id} vào Drive: {folder_path}/{fname}")
            return {
                "status": "success",
                "file_id": created.get("id"),
                "file_name": fname,
                "drive_link": created.get("webViewLink")
            }
        except Exception as e:
            logger.error(f"Lỗi khi upload task JSON lên Drive: {e}", exc_info=True)
            return {"status": "failed", "error": str(e)}

    def list_completed_videos(self, folder_path: str = "AI_YouTube_RenderFarm/completed") -> list:
        """
        Liệt kê các file video MP4 đã hoàn thành từ Colab trên Google Drive.
        """
        if not self.service:
            self._init_service()
        if not self.service:
            return []
        try:
            folder_id = self.get_folder_by_path(folder_path)
            if not folder_id:
                return []
            q = f"'{folder_id}' in parents and trashed = false and name contains '.mp4'"
            res = self.service.files().list(
                q=q,
                fields="files(id, name, size, mimeType, webViewLink, webContentLink, createdTime)",
                pageSize=100
            ).execute()
            return res.get("files", [])
        except Exception as e:
            logger.debug(f"Lỗi khi list completed videos trên Drive: {e}")
            return []

    def list_pending_tasks(self, folder_path: str = "AI_YouTube_RenderFarm/pending") -> list:
        """
        Liệt kê các file task kịch bản JSON đang chờ Colab render trên Google Drive.
        """
        if not self.service:
            self._init_service()
        if not self.service:
            return []
        try:
            folder_id = self.get_folder_by_path(folder_path)
            if not folder_id:
                return []
            q = f"'{folder_id}' in parents and trashed = false and name contains '.json'"
            res = self.service.files().list(
                q=q,
                fields="files(id, name, size, mimeType, webViewLink, createdTime)",
                pageSize=100
            ).execute()
            return res.get("files", [])
        except Exception as e:
            logger.debug(f"Lỗi khi list pending tasks trên Drive: {e}")
            return []


    def get_completed_video_by_job_id(self, job_id: int, folder_path: str = "AI_YouTube_RenderFarm/completed") -> Optional[Dict[str, Any]]:
        """
        Tìm kiếm video hoàn thiện cho job_id cụ thể (ví dụ: 'video_39.mp4')
        """
        if not self.service:
            self._init_service()
        if not self.service:
            return None
        try:
            folder_id = self.get_folder_by_path(folder_path)
            if not folder_id:
                return None
            target_name = f"video_{job_id}.mp4"
            safe_name = target_name.replace("'", "\\'")
            q = f"name = '{safe_name}' and '{folder_id}' in parents and trashed = false"
            res = self.service.files().list(
                q=q,
                fields="files(id, name, size, mimeType, webViewLink, webContentLink, createdTime)"
            ).execute()
            files = res.get("files", [])
            if files:
                return files[0]

            # Fallback: tìm file mp4 bất kỳ chứa job_id trong tên (ví dụ: video_39_node1.mp4 hoặc 39.mp4)
            q_fallback = f"'{folder_id}' in parents and trashed = false and name contains '{job_id}' and name contains '.mp4'"
            res_fb = self.service.files().list(
                q=q_fallback,
                fields="files(id, name, size, mimeType, webViewLink, webContentLink, createdTime)"
            ).execute()
            fb_files = res_fb.get("files", [])
            return fb_files[0] if fb_files else None
        except Exception as e:
            logger.debug(f"Lỗi khi tìm video #{job_id} trên Drive: {e}")
            return None

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

    def download_file_by_name(self, filename: str, dest_local_path: str, folder_path: str = "AI_YouTube_RenderFarm/completed") -> bool:
        """
        Tìm và tải file theo tên trong thư mục Drive về máy cục bộ
        """
        if not self.service:
            self._init_service()
        if not self.service:
            return False
        try:
            folder_id = self.get_folder_by_path(folder_path)
            if not folder_id:
                return False
            safe_name = filename.replace("'", "\\'")
            q = f"name = '{safe_name}' and '{folder_id}' in parents and trashed = false"
            res = self.service.files().list(q=q, fields="files(id)").execute()
            files = res.get("files", [])
            if not files:
                return False
            return self.download_file_by_id(files[0]["id"], dest_local_path)
        except Exception as e:
            logger.error(f"Lỗi khi tải file {filename} từ {folder_path}: {e}")
            return False

    def read_task_json_by_job_id(self, job_id: int) -> Optional[Dict[str, Any]]:
        """Đọc file task_{job_id}.json từ archive, completed, hoặc pending để lấy metadata khôi phục VideoJob"""
        if not self.service:
            self._init_service()
        if not self.service:
            return None
        target_name = f"task_{job_id}.json"
        safe_name = target_name.replace("'", "\\'")
        for folder_path in ["AI_YouTube_RenderFarm/archive", "AI_YouTube_RenderFarm/completed", "AI_YouTube_RenderFarm/pending"]:
            try:
                fid = self.get_folder_by_path(folder_path)
                if not fid:
                    continue
                q = f"name = '{safe_name}' and '{fid}' in parents and trashed = false"
                res = self.service.files().list(q=q, fields="files(id, name)").execute()
                files = res.get("files", [])
                if files:
                    content = self.service.files().get_media(fileId=files[0]["id"]).execute().decode("utf-8")
                    import json
                    return json.loads(content)
            except Exception as e:
                logger.debug(f"Không thể đọc {target_name} từ {folder_path}: {e}")
        return None


