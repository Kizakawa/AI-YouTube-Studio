import os
import logging
from pathlib import Path
from typing import Optional, Dict

logger = logging.getLogger(__name__)

class DriveAgent:
    """
    Agent quản lý kho lưu trữ Google Drive 2TB cho từng tài khoản Google Ultra.
    - Tải video hoàn chỉnh lên Google Drive của tài khoản tương ứng
    - Tạo liên kết chia sẻ (Share link) để gửi vào nhóm Telegram
    - Tiết kiệm dung lượng ổ cứng local sau khi tải lên thành công
    """

    def __init__(self, account_name: str, token_path: Optional[str] = None):
        self.account_name = account_name
        self.token_path = token_path
        self.service = None
        # Khởi tạo Google Drive service khi có token_path hợp lệ

    def upload_video(
        self,
        local_video_path: str,
        video_title: str,
        folder_id: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Tải video lên Google Drive và trả về webViewLink (link xem video trực tiếp).
        """
        logger.info(f"Đang chuẩn bị tải video '{video_title}' lên Google Drive (Tài khoản: {self.account_name})...")
        
        # Mô phỏng / Khung trả về kết quả
        # Khi kết nối credentials/accounts/<account>/drive_token.json, hàm sẽ upload file thực tế
        return {
            "status": "success",
            "account": self.account_name,
            "title": video_title,
            "drive_link": f"https://drive.google.com/file/d/mock_{Path(local_video_path).stem}/view"
        }

    def delete_local_file(self, local_path: str):
        """Xóa file tạm trên máy sau khi đã đẩy lên Drive an toàn"""
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
                logger.info(f"Đã dọn dẹp file tạm local: {local_path}")
        except Exception as e:
            logger.error(f"Không thể xóa file tạm {local_path}: {e}")
