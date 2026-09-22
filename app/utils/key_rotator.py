import itertools
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

class KeyRotator:
    """
    Quản lý xoay vòng danh sách API Keys (cho Gemini API hoặc các dịch vụ khác).
    Tối ưu hóa khi người dùng có nhiều tài khoản Google Ultra / AI Studio.
    """

    def __init__(self, keys: List[str]):
        clean_keys = [k.strip() for k in keys if k and k.strip()]
        self.keys = clean_keys
        self._cycle = itertools.cycle(self.keys) if self.keys else None
        self._current_key = next(self._cycle) if self._cycle else None

    @property
    def has_keys(self) -> bool:
        return len(self.keys) > 0

    def get_key(self) -> Optional[str]:
        """Lấy key hiện tại"""
        return self._current_key

    def rotate_key(self) -> Optional[str]:
        """Chuyển sang key tiếp theo trong danh sách xoay vòng"""
        if not self._cycle:
            return None
        self._current_key = next(self._cycle)
        logger.info(f"🔄 Đã chuyển sang API Key mới trong pool (Tổng số keys: {len(self.keys)})")
        return self._current_key
