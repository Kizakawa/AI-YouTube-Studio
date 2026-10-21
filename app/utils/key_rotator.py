import itertools
import logging
import threading
import time
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# Thứ tự ưu tiên chuẩn toàn hệ thống (từ Model Gemini Flash/Pro hỗ trợ JSON & Vision gốc siêu tốc -> Gemma dự phòng):
# Hệ thống sẽ bắn kiểm tra ĐỒNG THỜI (Parallel Probe) toàn bộ danh sách này cùng 1 lúc,
# loại bỏ ngay các model bị treo (Timeout) hoặc hết hạn mức (429/404), và chọn Model xịn nhất phản hồi thành công.
GEMINI_PRIORITY_MODELS: List[str] = [
    "gemini-3-flash-preview",        # Tier S+ (#1): Gemini 3.0 Flash Preview (Multimodal & JSON nhanh nhất)
    "gemini-3.1-flash-lite-preview", # Tier S  (#2): Gemini 3.1 Flash-Lite Preview (Siêu tốc, quota độc lập)
    "gemini-flash-latest",           # Tier A+ (#3): Alias Flash chính thức (Hỗ trợ Vision & JSON gốc cực nhanh)
    "gemini-flash-lite-latest",      # Tier A+ (#4): Alias Flash-Lite chính thức (Độ trễ cực thấp ~1.2s)
    "gemini-2.5-flash",              # Tier A  (#5): Gemini 2.5 Flash ổn định
    "gemini-2.5-flash-lite",         # Tier A  (#6): Gemini 2.5 Flash-Lite siêu tốc
    "gemini-3.1-pro-preview",        # Tier A  (#7): Gemini 3.1 Pro Preview
    "gemma-4-26b-a4b-it",            # Tier B+ (#8): Gemma 4 26B A4B IT (MoE nhanh, dự phòng text khi Gemini cạn quota)
    "gemma-4-31b-it",                # Tier B  (#9): Gemma 4 31B IT (Dự phòng text)
    "gemini-robotics-er-2-preview",  # Tier B  (#10): Gemini ER 2 Preview (Bucket quota độc lập)
]

# Điểm xếp hạng độ "xịn" (Quality Tier) của từng model để chọn model mạnh nhất trong số các model phản hồi thành công
_MODEL_QUALITY_RANK: Dict[str, int] = {
    model_name: (len(GEMINI_PRIORITY_MODELS) - idx) * 10
    for idx, model_name in enumerate(GEMINI_PRIORITY_MODELS)
}


class KeyRotator:
    """
    Quản lý xoay vòng danh sách API Keys (cho Gemini API hoặc các dịch vụ khác)
    với cơ chế Thread-Safe, kiểm tra song song (Concurrent Parallel Probe) toàn bộ Model cùng 1 lúc,
    tự động lọc bỏ các Model bị treo/hết quota và chọn Model API xịn nhất đang sống thực tế.
    """

    _global_lock = threading.Lock()
    _probe_lock = threading.Lock()
    _global_cooldowns: Dict[str, float] = {}
    _model_cooldowns: Dict[str, float] = {}
    _global_cursor: int = 0
    _verified_live_models: List[str] = []
    _last_parallel_probe_ts: float = 0.0
    _PARALLEL_PROBE_TTL_SEC: float = 300.0  # Cache kết quả kiểm tra đồng thời trong 5 phút

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
        Đánh dấu hạn mức riêng cho từng cặp (key, model) và tự động khóa toàn model (*:model)
        khi phát hiện cạn quota ngày (Free Tier Daily Quota), lỗi 404, hoặc bị treo Timeout:
        - Nếu 404, 'limit: 0', 'free_tier_requests', hoặc 'retry in Xh/Xm': khóa model trên mọi key 60 phút.
        - Nếu Timeout ('timed out' / 'readtimeout'): khóa model tạm 25s để tránh treo dây chuyền.
        - Nếu 429 lặp lại trên >= 2 key (hoặc khi chỉ có 1 key trong pool): khóa model 45s để nhảy sang model kế tiếp ngay.
        - Nếu 503 (model đang quá tải trên server Google): tạm né model đó 20s.
        - Nếu 403 (key bị từ chối quyền): khóa toàn bộ key 180s.
        """
        import re as _re
        now = time.monotonic()
        err_low = (error_text or "").lower()
        with self._global_lock:
            if (
                status_code == 404
                or "no longer available" in err_low
                or "not found" in err_low
            ):
                # Model không tồn tại hoặc đã ngừng hỗ trợ -> khóa toàn cục *:model
                self._model_cooldowns[f"*:{model}"] = now + (cooldown_sec or 3600.0)
            elif (
                "limit: 0" in err_low
                or "generate_content_free_tier_requests" in err_low
                or "perday" in err_low
                or bool(_re.search(r"retry in \d+\s*[hm]", err_low))
            ):
                # Hết hạn mức ngày theo từng Project/Key -> chỉ khóa cặp (key:model) này trong 60 phút
                self._model_cooldowns[f"{key}:{model}"] = now + (cooldown_sec or 3600.0)
                # Chỉ khóa toàn cục *:model nếu TẤT CẢ các key trong pool đều đã cạn model này
                all_keys_exhausted = all(
                    self._model_cooldowns.get(f"{k}:{model}", 0.0) > now for k in self.keys
                )
                if all_keys_exhausted:
                    self._model_cooldowns[f"*:{model}"] = now + (cooldown_sec or 3600.0)
            elif "timed out" in err_low or "timeout" in err_low:
                self._model_cooldowns[f"*:{model}"] = now + (cooldown_sec or 25.0)
            elif status_code == 503:
                self._model_cooldowns[f"*:{model}"] = now + (cooldown_sec or 20.0)
            elif status_code == 403 or "api_key_invalid" in err_low:
                self._global_cooldowns[key] = now + (cooldown_sec or 180.0)
            else:
                wait_s = cooldown_sec or 45.0
                self._model_cooldowns[f"{key}:{model}"] = now + wait_s
                # Nếu toàn bộ key hiện có trong pool đều bị 429 trên cùng model, khóa luôn *:model 45s
                active_key_locks = sum(
                    1 for k in self.keys if self._model_cooldowns.get(f"{k}:{model}", 0.0) > now
                )
                if active_key_locks >= max(len(self.keys), 1):
                    self._model_cooldowns[f"*:{model}"] = now + 45.0

    def probe_all_models_concurrently(
        self,
        candidate_models: Optional[Sequence[str]] = None,
        probe_timeout_sec: float = 2.6,
        force: bool = False,
    ) -> List[str]:
        """
        Bắn kiểm tra ĐỒNG THỜI (Parallel Probe) tất cả các Model ứng viên cùng 1 lúc bằng ThreadPoolExecutor.
        Đợi phản hồi trong `probe_timeout_sec` (~4.0 giây):
        - Các model phản hồi 200 OK được mở khóa ngay khỏi `_model_cooldowns` và xếp hạng theo Độ Xịn (Quality Tier).
        """
        if not self.keys:
            return list(candidate_models or GEMINI_PRIORITY_MODELS)

        now = time.monotonic()
        with self._probe_lock:
            # Nếu đã probe trong vòng TTL -> dùng luôn kết quả cache (kể cả khi tất cả model đang 429 tạm thời, tránh bắn lại 14 HTTP probes liên tục mỗi giây)
            if not force and KeyRotator._last_parallel_probe_ts > 0:
                elapsed_since_probe = now - KeyRotator._last_parallel_probe_ts
                if KeyRotator._verified_live_models and elapsed_since_probe < self._PARALLEL_PROBE_TTL_SEC:
                    healthy_cached = [
                        m for m in KeyRotator._verified_live_models
                        if self._model_cooldowns.get(f"*:{m}", 0.0) <= now
                    ]
                    if healthy_cached:
                        return healthy_cached
                elif not KeyRotator._verified_live_models and elapsed_since_probe < 30.0:
                    return []

            raw_candidates = list(candidate_models) if candidate_models else list(GEMINI_PRIORITY_MODELS)
            # Bỏ qua các model đã bị khóa dài hạn (404 Not Found / Daily Quota > 60s) để không tốn thời gian ping lại
            models_to_check = [
                m for m in raw_candidates
                if self._model_cooldowns.get(f"*:{m}", 0.0) <= (now + 60.0)
            ] or raw_candidates[-3:]
            probe_key = self._select_next_healthy_key(advance_cursor=False) or self.keys[0]
            t_start = time.monotonic()

            def _ping_single_model(model_name: str) -> Tuple[str, int, float, str]:
                t0 = time.monotonic()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={probe_key}"
                payload = {
                    "contents": [{"parts": [{"text": "1"}]}],
                    "generationConfig": {"maxOutputTokens": 2},
                }
                try:
                    resp = requests.post(url, json=payload, timeout=(1.5, probe_timeout_sec))
                    latency = time.monotonic() - t0
                    return (model_name, resp.status_code, latency, resp.text[:300] if resp.status_code != 200 else "OK")
                except Exception as exc:
                    latency = time.monotonic() - t0
                    return (model_name, 599, latency, f" timed out / connection error: {exc}")

            live_results: List[Tuple[str, float]] = []
            max_workers = min(max(len(models_to_check), 1), 8)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_ping_single_model, m): m for m in models_to_check}
                for fut in as_completed(futures):
                    m_name, status, lat, err_txt = fut.result()
                    if status == 200:
                        live_results.append((m_name, lat))
                        with self._global_lock:
                            self._model_cooldowns.pop(f"*:{m_name}", None)
                            self._model_cooldowns.pop(f"{probe_key}:{m_name}", None)
                    elif status == 599:
                        self.mark_model_rate_limited(probe_key, m_name, status_code=503, error_text="timed out", cooldown_sec=15.0)
                    else:
                        self.mark_model_rate_limited(probe_key, m_name, status_code=status, error_text=err_txt)

            # Xếp hạng các model sống (200 OK): Ưu tiên Độ Xịn (Quality Tier) cao nhất trước, nếu cùng Tier thì ưu tiên phản hồi nhanh hơn
            live_results.sort(
                key=lambda item: (-_MODEL_QUALITY_RANK.get(item[0], 0), item[1])
            )
            ranked_live_models = [m for m, _ in live_results]

            elapsed_probe = time.monotonic() - t_start
            KeyRotator._last_parallel_probe_ts = time.monotonic()
            KeyRotator._verified_live_models = ranked_live_models
            if ranked_live_models:
                logger.info(
                    f"⚡ [KeyRotator Parallel Probe] Đã test đồng thời {len(models_to_check)} models trong {elapsed_probe:.2f}s "
                    f"-> Chọn Model xịn nhất: {ranked_live_models[0]} (Danh sách sống: {ranked_live_models})"
                )
                return ranked_live_models
            else:
                logger.warning(
                    f"⚠️ [KeyRotator Parallel Probe] Không có model nào trả 200 OK tức thì sau {elapsed_probe:.2f}s. Chuyển nhanh sang nhánh dự phòng."
                )
                return []

    def iter_best_model_key_pairs(
        self,
        models: Optional[Sequence[str]] = None,
        max_total_attempts: Optional[int] = None,
    ) -> Iterator[Tuple[str, str]]:
        """
        Bộ sinh (generator) thông minh theo chiến lược:
        1. Tự động chạy `probe_all_models_concurrently()` để kiểm tra TẤT CẢ các model cùng 1 lúc (nếu chưa có cache)
           và chọn ngay Model API xịn nhất đang hoạt động thực tế (tránh hoàn toàn việc chờ tuần tự từng model bị treo/429).
        2. Ưu tiên các model đã xác thực sống (`_verified_live_models`) từ mạnh nhất -> nhẹ hơn.
        3. Ngay khi phát hiện Model bị khóa toàn cục (`*:model`), lập tức ngắt vòng lặp key để nhảy sang Model kế tiếp!
        """
        if not self.keys:
            return

        # Bước 1: Kiểm tra đồng thời toàn bộ model (hoặc lấy từ cache 5 phút) để lấy danh sách Model xịn nhất đang sống
        live_ranked = self.probe_all_models_concurrently()

        # Ghép danh sách ưu tiên: Đưa các model sống (đã xếp hạng từ xịn nhất) lên đầu tiên.
        # Nếu caller truyền danh sách `models` cụ thể (ví dụ chỉ các model hỗ trợ Vision), ưu tiên lọc trong `models` đó trước.
        base_models = list(models) if models else list(GEMINI_PRIORITY_MODELS)
        if models:
            allowed_set = set(base_models)
            model_list: List[str] = [m for m in live_ranked if m in allowed_set]
        else:
            model_list = list(live_ranked)
        for m in base_models:
            if m not in model_list:
                model_list.append(m)

        n_keys = len(self.keys)
        default_cap = 6 if live_ranked else 2
        limit = max_total_attempts or min(max(n_keys * len(model_list), 2), default_cap)
        yielded = 0
        yielded_models = set()

        for model in model_list:
            now = time.monotonic()
            with self._global_lock:
                self._cleanup_expired_locked(now)
                if self._model_cooldowns.get(f"*:{model}", 0.0) > now and model not in live_ranked:
                    continue
                start_idx = self._global_cursor % n_keys

            for offset in range(n_keys):
                if yielded >= limit:
                    return
                if self._model_cooldowns.get(f"*:{model}", 0.0) > time.monotonic() and model in yielded_models:
                    break
                idx = (start_idx + offset) % n_keys
                candidate_key = self.keys[idx]
                if not self.is_model_available(candidate_key, model) and model not in live_ranked:
                    continue
                with self._global_lock:
                    self._global_cursor = (idx + 1) % n_keys
                    self._current_key = candidate_key
                yielded += 1
                yielded_models.add(model)
                yield model, candidate_key

        # Fallback dự phòng cho các dòng Flash/Flash-Lite ổn định nếu các model trên gặp 503 tạm thời
        for fb_model in ("gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-flash-lite-latest"):
            if fb_model not in yielded_models:
                fb_key = self._select_next_healthy_key(advance_cursor=True)
                if fb_key:
                    yielded += 1
                    yield fb_model, fb_key

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

