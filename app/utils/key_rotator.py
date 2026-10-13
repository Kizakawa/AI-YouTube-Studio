import itertools
import logging
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class KeyRotator:
    """
    Quản lý xoay vòng danh sách API Keys (cho Gemini API hoặc các dịch vụ khác)
    với cơ chế Thread-Safe, chia sẻ trạng thái Cooldown toàn cục giữa các Agent
    và phân tải tự động (Smart Load-Balanced Key Pool).
    """

    _global_lock = threading.Lock()
    _global_cooldowns: Dict[str, float] = {}
    _global_cursor: int = 0

    def __init__(self, keys: List[str], default_cooldown_sec: float = 35.0):
        clean_keys = []
        seen = set()
        for k in (keys or []):
            if k and k.strip():
                ks = k.strip()
                if ks not in seen:
                    seen.add(ks)
                    clean_keys.append(ks)
        self.keys: List[str] = clean_keys
        self.default_cooldown_sec = default_cooldown_sec
        self._cycle = itertools.cycle(self.keys) if self.keys else None
        self._current_key: Optional[str] = None
        if self.keys:
            self._current_key = self._select_next_healthy_key(advance_cursor=True)

    @property
    def has_keys(self) -> bool:
        return len(self.keys) > 0

    def _select_next_healthy_key(self, advance_cursor: bool = True) -> Optional[str]:
        if not self.keys:
            return None
        now = time.monotonic()
        n = len(self.keys)
        with self._global_lock:
            # Dọn các cooldown đã hết hạn
            expired = [k for k, until in self._global_cooldowns.items() if until <= now]
            for k in expired:
                self._global_cooldowns.pop(k, None)

            start_idx = self._global_cursor % n
            chosen: Optional[str] = None
            for offset in range(n):
                idx = (start_idx + offset) % n
                candidate = self.keys[idx]
                if self._global_cooldowns.get(candidate, 0.0) <= now:
                    chosen = candidate
                    if advance_cursor:
                        self._global_cursor = (idx + 1) % n
                    break

            if chosen is None:
                # Tất cả keys đều đang trong thời gian chờ -> chọn key sắp hết hạn sớm nhất
                chosen = min(self.keys, key=lambda k: self._global_cooldowns.get(k, 0.0))
                if advance_cursor:
                    self._global_cursor = (self.keys.index(chosen) + 1) % n

            return chosen

    def get_key(self) -> Optional[str]:
        """Lấy key hiện tại (nếu key hiện tại bị đánh dấu cooldown bởi luồng khác thì tự động lấy key khỏe mạnh)."""
        if not self.keys:
            return None
        now = time.monotonic()
        with self._global_lock:
            if (
                self._current_key
                and self._current_key in self.keys
                and self._global_cooldowns.get(self._current_key, 0.0) <= now
            ):
                return self._current_key
        self._current_key = self._select_next_healthy_key(advance_cursor=True)
        return self._current_key

    def mark_rate_limited(self, key: Optional[str] = None, cooldown_sec: Optional[float] = None) -> None:
        """Đánh dấu một key bị giới hạn tốc độ (429/403) để các luồng song song tạm né."""
        target = key or self._current_key
        if not target:
            return
        wait_s = cooldown_sec if cooldown_sec is not None else self.default_cooldown_sec
        with self._global_lock:
            self._global_cooldowns[target] = time.monotonic() + wait_s

    def rotate_key(self, cooldown_sec: Optional[float] = None) -> Optional[str]:
        """Đánh dấu key hiện tại cần nghỉ ngắn hạn và chuyển sang key khỏe mạnh tiếp theo trong pool."""
        if not self.keys:
            return None
        prev_key = self._current_key
        if prev_key and len(self.keys) > 1:
            wait_s = cooldown_sec if cooldown_sec is not None else self.default_cooldown_sec
            with self._global_lock:
                self._global_cooldowns[prev_key] = time.monotonic() + wait_s

        self._current_key = self._select_next_healthy_key(advance_cursor=True)
        logger.info(
            f"🔄 Đã chuyển sang API Key {self._current_key[:8] if self._current_key else 'None'}... "
            f"(Tổng số keys: {len(self.keys)})"
        )
        return self._current_key

