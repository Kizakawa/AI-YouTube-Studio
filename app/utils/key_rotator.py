import itertools
import logging
import threading
import time
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Thứ tự ưu tiên chuẩn toàn hệ thống:
# Ưu tiên các model Flagship Reasoning & Multimodal mạnh nhất có Free Tier ổn định trước,
# khi hết hạn mức (429) hoặc quá tải (503) mới hạ dần xuống dòng Flash-Lite thông lượng cao.
GEMINI_PRIORITY_MODELS: List[str] = [
    "gemini-3.8-flash",          # Tier 1 (#1): Flagship 3.8 Flash mới nhất (Reasoning + Vision thế hệ mới)
    "gemini-3.5-flash",          # Tier 1 (#2): Flagship 3.5 Flash (Reasoning + Vision cực mạnh Free Tier)
    "gemini-3.6-flash",          # Tier 1 (#3): Flagship 3.6 Flash (Fallback tương thích ngược)
    "gemini-3-flash-preview",    # Tier 1 (#4): Gemini 3.0 Flash Reasoning
    "gemini-flash-latest",       # Tier 1 (#5): Alias Flash chính thức
    "gemini-3.5-flash-lite",     # Tier 2 (#6): Bản 3.5 Flash-Lite (Fallback khi toàn bộ Tier 1 hết quota)
    "gemini-3.1-flash-lite",     # Tier 2 (#7): Bản 3.1 Flash-Lite
    "gemini-flash-lite-latest",  # Tier 3 (#8): Fallback cuối siêu tốc, hạn mức dồi dào nhất
]


class KeyRotator:
    """
    Quản lý xoay vòng danh sách API Keys (cho Gemini API hoặc các dịch vụ khác)
    với cơ chế Thread-Safe, chia sẻ trạng thái Cooldown toàn cục giữa các Agent,
    theo dõi hạn mức độc lập theo từng cặp (Key, Model) và ưu tiên Model mạnh nhất trước.
    """

    _global_lock = threading.Lock()
    _global_cooldowns: Dict[str, float] = {}
    _model_cooldowns: Dict[str, float] = {}
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

    def _cleanup_expired_locked(self, now: float) -> None:
        expired_k = [k for k, until in self._global_cooldowns.items() if until <= now]
        for k in expired_k:
            self._global_cooldowns.pop(k, None)
        expired_m = [mk for mk, until in self._model_cooldowns.items() if until <= now]
        for mk in expired_m:
            self._model_cooldowns.pop(mk, None)

    def _select_next_healthy_key(self, advance_cursor: bool = True) -> Optional[str]:
        if not self.keys:
            return None
        now = time.monotonic()
        n = len(self.keys)
        with self._global_lock:
            self._cleanup_expired_locked(now)

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

    def is_model_available(self, key: str, model: str) -> bool:
        """Kiểm tra xem cặp (key, model) hoặc bản thân model có đang còn hạn mức khả dụng không."""
        now = time.monotonic()
        with self._global_lock:
            if self._global_cooldowns.get(key, 0.0) > now:
                return False
            if self._model_cooldowns.get(f"*:{model}", 0.0) > now:
                return False
            if self._model_cooldowns.get(f"{key}:{model}", 0.0) > now:
                return False
            return True

    def mark_model_rate_limited(
        self,
        key: str,
        model: str,
        status_code: int = 429,
        error_text: str = "",
        cooldown_sec: Optional[float] = None,
    ) -> None:
        """
        Đánh dấu hạn mức riêng cho từng cặp (key, model) thay vì khóa toàn bộ key:
        - Nếu 404 hoặc 'limit: 0' (model không mở Free Tier / đã ngừng hỗ trợ): khóa model trên mọi key 1 giờ.
        - Nếu 429 (hết quota model đó trên key hiện tại): khóa cặp (key, model) 90s-1800s để xoay sang key khác hoặc hạ xuống model nhẹ hơn.
        - Nếu 503 (model đang quá tải trên server Google): tạm né model đó 25s.
        - Nếu 403 (key bị từ chối quyền): khóa toàn bộ key 180s.
        """
        now = time.monotonic()
        err_low = (error_text or "").lower()
        with self._global_lock:
            if status_code == 404 or "limit: 0" in err_low or "no longer available" in err_low:
                self._model_cooldowns[f"*:{model}"] = now + (cooldown_sec or 3600.0)
            elif status_code == 503:
                self._model_cooldowns[f"*:{model}"] = now + (cooldown_sec or 25.0)
            elif status_code == 403 or "api_key_invalid" in err_low:
                self._global_cooldowns[key] = now + (cooldown_sec or 180.0)
            else:
                # 429 Quota Exceeded cho riêng model này trên key này
                if "perday" in err_low or "retry in 1" in err_low or "retry in 2" in err_low:
                    wait_s = cooldown_sec or 1800.0
                else:
                    wait_s = cooldown_sec or 90.0
                self._model_cooldowns[f"{key}:{model}"] = now + wait_s

    def iter_best_model_key_pairs(
        self,
        models: Optional[Sequence[str]] = None,
        max_total_attempts: Optional[int] = None,
    ) -> Iterator[Tuple[str, str]]:
        """
        Bộ sinh (generator) thông minh theo chiến lược:
        1. Ưu tiên tuyệt đối Model tốt nhất trong danh sách (`GEMINI_PRIORITY_MODELS`).
        2. Với mỗi Model từ mạnh nhất -> yếu hơn, thử qua các API Key đang còn hạn mức cho Model đó.
        3. Khi tất cả Key đều hết hạn mức ở Model mạnh, tự động hạ xuống Model tiếp theo ngay lập tức.
        """
        if not self.keys:
            return
        model_list = list(models) if models else list(GEMINI_PRIORITY_MODELS)
        n_keys = len(self.keys)
        limit = max_total_attempts or max(n_keys * len(model_list), 6)
        yielded = 0

        for model in model_list:
            now = time.monotonic()
            with self._global_lock:
                self._cleanup_expired_locked(now)
                if self._model_cooldowns.get(f"*:{model}", 0.0) > now:
                    continue
                start_idx = self._global_cursor % n_keys

            for offset in range(n_keys):
                if yielded >= limit:
                    return
                idx = (start_idx + offset) % n_keys
                candidate_key = self.keys[idx]
                if not self.is_model_available(candidate_key, model):
                    continue
                with self._global_lock:
                    self._global_cursor = (idx + 1) % n_keys
                    self._current_key = candidate_key
                yielded += 1
                yield model, candidate_key

        # Fallback khẩn cấp nếu tất cả cặp đều đang trong cooldown: thử model cuối (flash-lite-latest) với key khỏe nhất
        if yielded == 0 and model_list:
            fallback_model = model_list[-1]
            fallback_key = self._select_next_healthy_key(advance_cursor=True)
            if fallback_key:
                yield fallback_model, fallback_key

    def mark_rate_limited(self, key: Optional[str] = None, cooldown_sec: Optional[float] = None) -> None:
        """Đánh dấu một key bị giới hạn tốc độ (429/403) để các luồng song song tạm né."""
        target = key or self._current_key
        if not target:
            return
        wait_s = cooldown_sec if cooldown_sec is not None else self.default_cooldown_sec
        with self._global_lock:
            self._global_cooldowns[target] = time.monotonic() + wait_s

    def mark_failed(self, key: Optional[str] = None, cooldown_sec: Optional[float] = None) -> None:
        """Alias tương thích ngược cho mark_rate_limited."""
        self.mark_rate_limited(key=key, cooldown_sec=cooldown_sec)

    def rotate_key(self, cooldown_sec: Optional[float] = None) -> Optional[str]:
        """Đánh dấu key hiện tại cần nghỉ ngắn hạn và chuyển sang key khỏe mạnh tiếp theo trong pool."""
        if not self.keys:
            return None
        prev_key = self._current_key
        if prev_key and len(self.keys) > 1 and cooldown_sec is not None and cooldown_sec > 0:
            with self._global_lock:
                self._global_cooldowns[prev_key] = time.monotonic() + cooldown_sec

        self._current_key = self._select_next_healthy_key(advance_cursor=True)
        logger.info(
            f"🔄 Đã chuyển sang API Key {self._current_key[:8] if self._current_key else 'None'}... "
            f"(Tổng số keys: {len(self.keys)})"
        )
        return self._current_key

