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
    "gemini-3-flash-preview",        # Tier S+ (#1): Gemini 3.0 Flash Preview (Multimodal & JSON nhanh ~2.2s)
    "gemini-3.5-flash-lite",         # Tier S  (#2): Gemini 3.5 Flash-Lite (Bucket quota độc lập, ~2.1s)
    "gemini-3.1-flash-lite-preview", # Tier S  (#3): Gemini 3.1 Flash-Lite Preview (Siêu tốc, quota độc lập ~1.8s)
    "gemini-flash-lite-latest",      # Tier A+ (#4): Alias Flash-Lite chính thức (Độ trễ cực thấp ~1.2s–2.0s)
    "gemini-3.6-flash",              # Tier A+ (#5): Gemini 3.6 Flash (Multimodal & JSON, quota độc lập)
    "gemini-3.5-flash",              # Tier A  (#6): Gemini 3.5 Flash (Bucket quota độc lập)
    "gemini-robotics-er-2-preview",  # Tier B  (#7): Gemini ER 2 Preview (Bucket quota độc lập ~1.5s)
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
    _model_ema_latency: Dict[str, float] = {}
    _model_slow_demote_until: Dict[str, float] = {}
    _last_parallel_probe_ts: float = 0.0
    _PARALLEL_PROBE_TTL_SEC: float = 300.0  # Cache kết quả kiểm tra đồng thời trong 5 phút
    SLOW_LATENCY_THRESHOLD_SEC: float = 7.5
    DEMOTE_COOLDOWN_SEC: float = 300.0

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
        expired_slow = [m for m, until in self._model_slow_demote_until.items() if until <= now]
        for m in expired_slow:
            self._model_slow_demote_until.pop(m, None)

    @classmethod
    def _rank_live_model_key(cls, model_name: str, now: float) -> Tuple[int, int, int, int, float]:
        is_demoted = 1 if cls._model_slow_demote_until.get(model_name, 0.0) > now else 0
        ema = cls._model_ema_latency.get(model_name, 3.0)
        speed_tier = 0 if ema <= 4.2 else (1 if ema <= cls.SLOW_LATENCY_THRESHOLD_SEC else 2)
        speed_band = int(ema // 1.5)
        quality_rank = _MODEL_QUALITY_RANK.get(model_name, 0)
        return (is_demoted, speed_tier, speed_band, -quality_rank, round(ema, 3))

    def record_model_success(self, key: str, model: str, latency_sec: float) -> None:
        """
        Ghi nhận độ trễ thực tế của model khi trả về HTTP 200 OK (không tốn thêm bất kỳ ping probe nào):
        - Nếu model phản hồi nhanh (<= 7.5s): xóa án phạt chậm, cập nhật EMA latency và đưa lên nhóm ưu tiên sống (`_verified_live_models`).
        - Nếu model phản hồi quá chậm (> 7.5s): tự động giáng hạng (demote) trong 300s để nhường đường cho các model phản hồi 1.0s–3.7s.
        """
        if not model:
            return
        lat = max(0.05, float(latency_sec or 0.0))
        now = time.monotonic()
        with self._global_lock:
            self._cleanup_expired_locked(now)
            if key:
                self._model_cooldowns.pop(f"{key}:{model}", None)
            prev_ema = KeyRotator._model_ema_latency.get(model)
            ema = lat if prev_ema is None else (0.60 * prev_ema + 0.40 * lat)
            KeyRotator._model_ema_latency[model] = ema

            if lat > self.SLOW_LATENCY_THRESHOLD_SEC:
                KeyRotator._model_slow_demote_until[model] = max(
                    KeyRotator._model_slow_demote_until.get(model, 0.0),
                    now + self.DEMOTE_COOLDOWN_SEC,
                )
                if model in KeyRotator._verified_live_models:
                    KeyRotator._verified_live_models = [
                        m for m in KeyRotator._verified_live_models if m != model
                    ]
                logger.info(
                    f"🐢 [KeyRotator Latency Demote] Model `{model}` phản hồi chậm ({lat:.2f}s > {self.SLOW_LATENCY_THRESHOLD_SEC:.1f}s, "
                    f"EMA={ema:.2f}s) -> Tạm giáng hạng {int(self.DEMOTE_COOLDOWN_SEC)}s để ưu tiên model siêu tốc!"
                )
            else:
                if ema <= self.SLOW_LATENCY_THRESHOLD_SEC:
                    KeyRotator._model_slow_demote_until.pop(model, None)
                live_set = [
                    m for m in KeyRotator._verified_live_models
                    if m != model and self._model_cooldowns.get(f"*:{m}", 0.0) <= now
                ]
                if self._model_cooldowns.get(f"*:{model}", 0.0) <= now:
                    live_set.append(model)
                live_set.sort(key=lambda m_item: self._rank_live_model_key(m_item, now))
                KeyRotator._verified_live_models = live_set

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
            elif (
                "timed out" in err_low
                or "timeout" in err_low
                or "overloaded" in err_low
                or "unavailable" in err_low
                or status_code in (500, 502, 503, 504)
                or "internal error" in err_low
            ):
                wait_t = cooldown_sec if cooldown_sec is not None else self.DEMOTE_COOLDOWN_SEC
                self._model_cooldowns[f"{key}:{model}"] = now + wait_t
                KeyRotator._model_slow_demote_until[model] = max(
                    KeyRotator._model_slow_demote_until.get(model, 0.0),
                    now + wait_t,
                )
                if model in KeyRotator._verified_live_models:
                    KeyRotator._verified_live_models = [
                        m for m in KeyRotator._verified_live_models if m != model
                    ]
                active_t_locks = sum(
                    1 for k in self.keys if self._model_cooldowns.get(f"{k}:{model}", 0.0) > now
                )
                is_hard_timeout = ("timed out" in err_low) or ("timeout" in err_low) or (status_code == 504)
                # Khóa toàn cục *:model 300s ngay khi endpoint bị treo ReadTimeout hoặc >= 2 keys cùng gặp 503 để chặn đứng hiệu ứng treo dây chuyền
                if is_hard_timeout or active_t_locks >= min(2, max(len(self.keys), 1)):
                    self._model_cooldowns[f"*:{model}"] = now + wait_t
            elif (
                status_code == 403
                or "api_key_invalid" in err_low
                or "api key not valid" in err_low
            ):
                self._global_cooldowns[key] = now + (cooldown_sec or 3600.0)
            else:
                retry_match = _re.search(r"retry in (\d+(?:\.\d+)?)\s*s", err_low)
                parsed_retry_s = float(retry_match.group(1)) if retry_match else 0.0
                wait_s = (
                    cooldown_sec
                    if cooldown_sec is not None
                    else max(
                        self.DEMOTE_COOLDOWN_SEC,
                        min(parsed_retry_s + 5.0, 600.0) if parsed_retry_s > 0 else self.DEMOTE_COOLDOWN_SEC,
                    )
                )
                self._model_cooldowns[f"{key}:{model}"] = now + wait_s
                KeyRotator._model_slow_demote_until[model] = max(
                    KeyRotator._model_slow_demote_until.get(model, 0.0),
                    now + wait_s,
                )
                # Khi >= 1 key bị 429 trên một model (do các key chung Google Cloud Project hoặc chạm trần RPM),
                # khóa toàn cục *:model 300s để toàn bộ worker threads nhảy thẳng sang model sống siêu tốc ngay lập tức!
                active_key_locks = sum(
                    1 for k in self.keys if self._model_cooldowns.get(f"{k}:{model}", 0.0) > now
                )
                if active_key_locks >= 1:
                    self._model_cooldowns[f"*:{model}"] = now + wait_s
                    if model in KeyRotator._verified_live_models:
                        KeyRotator._verified_live_models = [
                            m for m in KeyRotator._verified_live_models if m != model
                        ]

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
                elif not KeyRotator._verified_live_models and elapsed_since_probe < 15.0:
                    return []

            raw_candidates = list(candidate_models) if candidate_models else list(GEMINI_PRIORITY_MODELS)
            # Bỏ qua các model đã bị khóa dài hạn (404 Not Found / Daily Quota > 60s) để không tốn thời gian ping lại
            models_to_check = [
                m for m in raw_candidates
                if self._model_cooldowns.get(f"*:{m}", 0.0) <= (now + 60.0)
            ] or raw_candidates[-3:]
            t_start = time.monotonic()
            n_keys = max(len(self.keys), 1)

            def _ping_single_model(idx_and_model: Tuple[int, str]) -> Tuple[str, str, int, float, str]:
                m_idx, model_name = idx_and_model
                p_key = self.keys[m_idx % n_keys]
                t0 = time.monotonic()
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={p_key}"
                payload = {
                    "contents": [{"parts": [{"text": "1"}]}],
                    "generationConfig": {"maxOutputTokens": 2},
                }
                try:
                    resp = requests.post(url, json=payload, timeout=(1.5, probe_timeout_sec))
                    latency = time.monotonic() - t0
                    return (model_name, p_key, resp.status_code, latency, resp.text[:300] if resp.status_code != 200 else "OK")
                except Exception as exc:
                    latency = time.monotonic() - t0
                    return (model_name, p_key, 599, latency, f" timed out / connection error: {exc}")

            live_results: List[Tuple[str, float]] = []
            max_workers = min(max(len(models_to_check), 1), 8)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_ping_single_model, (idx, m)): m for idx, m in enumerate(models_to_check)}
                for fut in as_completed(futures):
                    m_name, used_key, status, lat, err_txt = fut.result()
                    if status == 200:
                        live_results.append((m_name, lat))
                        with self._global_lock:
                            self._model_cooldowns.pop(f"*:{m_name}", None)
                            self._model_cooldowns.pop(f"{used_key}:{m_name}", None)
                    elif status == 599:
                        self.mark_model_rate_limited(used_key, m_name, status_code=503, error_text="timed out", cooldown_sec=15.0)
                    else:
                        self.mark_model_rate_limited(used_key, m_name, status_code=status, error_text=err_txt)

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

        # Bỏ qua việc bắn 8 HTTP probe giả ('1') gây tốn RPM quota; sử dụng danh sách model sống được cập nhật theo độ trễ thực tế (_verified_live_models)
        now_init = time.monotonic()
        with self._global_lock:
            self._cleanup_expired_locked(now_init)
            live_ranked = sorted(
                [
                    m for m in KeyRotator._verified_live_models
                    if self._model_cooldowns.get(f"*:{m}", 0.0) <= now_init
                    and self._model_slow_demote_until.get(m, 0.0) <= now_init
                ],
                key=lambda m_item: self._rank_live_model_key(m_item, now_init),
            )

        # Ghép danh sách ưu tiên:
        # 1) Các model sống phản hồi nhanh (<= 7.5s, xếp theo tốc độ & Quality Tier) lên đầu tiên
        # 2) Các model chưa bị giáng hạng chậm
        # 3) Các model đang bị giáng hạng chậm (> 7.5s trong 300s gần nhất) bị đẩy xuống cuối cùng
        base_models = list(models) if models else list(GEMINI_PRIORITY_MODELS)
        if models:
            allowed_set = set(base_models)
            fast_candidates: List[str] = [m for m in live_ranked if m in allowed_set]
        else:
            fast_candidates = list(live_ranked)

        slow_tail: List[str] = []
        for m in base_models:
            if m in fast_candidates or m in slow_tail:
                continue
            if self._model_slow_demote_until.get(m, 0.0) > now_init:
                slow_tail.append(m)
            else:
                fast_candidates.append(m)
        slow_tail.sort(key=lambda m_item: self._model_ema_latency.get(m_item, 99.0))
        model_list: List[str] = fast_candidates + slow_tail

        n_keys = len(self.keys)
        default_cap = max(8, n_keys * 2)
        limit = max_total_attempts or min(max(n_keys * len(model_list), 2), default_cap)
        yielded = 0
        yielded_pairs = set()

        # Vòng 1 (Diagonal Model+Key Rotation): Mỗi lần thử đổi ĐỒNG THỜI cả Model lẫn API Key
        # để không bao giờ bị kẹt toàn bộ số lần thử (max_total_attempts) trên cùng 1 model hoặc cùng 1 key!
        for pass_idx in range(n_keys):
            for model in model_list:
                if yielded >= limit:
                    return
                now = time.monotonic()
                with self._global_lock:
                    self._cleanup_expired_locked(now)
                    if self._model_cooldowns.get(f"*:{model}", 0.0) > now:
                        continue
                    # Ở lượt quét đầu tiên (pass_idx == 0), bỏ qua model đang bị giáng hạng chậm nếu vẫn còn model nhanh khả dụng
                    if (
                        pass_idx == 0
                        and self._model_slow_demote_until.get(model, 0.0) > now
                        and any(
                            self._model_cooldowns.get(f"*:{fm}", 0.0) <= now
                            and self._model_slow_demote_until.get(fm, 0.0) <= now
                            for fm in model_list
                        )
                    ):
                        continue
                    start_idx = self._global_cursor % n_keys

                for offset in range(n_keys):
                    idx = (start_idx + offset) % n_keys
                    candidate_key = self.keys[idx]
                    pair_id = f"{model}::{candidate_key}"
                    if pair_id in yielded_pairs:
                        continue
                    if not self.is_model_available(candidate_key, model):
                        continue
                    with self._global_lock:
                        self._global_cursor = (idx + 1) % n_keys
                        self._current_key = candidate_key
                    yielded += 1
                    yielded_pairs.add(pair_id)
                    yield model, candidate_key
                    break  # Chuyển ngay sang Model kế tiếp trong pass hiện tại!

        # Fallback dự phòng ưu tiên các dòng siêu tốc (1.0s–3.7s) nếu tất cả cặp trên đang trong thời gian cooldown ngắn
        fallback_order = (
            "gemini-3.1-flash-lite-preview",
            "gemini-flash-lite-latest",
            "gemini-3.6-flash",
            "gemini-robotics-er-2-preview",
            "gemini-3.5-flash-lite",
            "gemini-3-flash-preview",
            "gemini-3.5-flash",
        )
        allowed_fb = set(base_models) if models else None
        for fb_model in fallback_order:
            if yielded >= limit:
                return
            if allowed_fb is not None and fb_model not in allowed_fb:
                continue
            now_fb = time.monotonic()
            if self._model_cooldowns.get(f"*:{fb_model}", 0.0) > now_fb:
                continue
            if self._model_slow_demote_until.get(fb_model, 0.0) > now_fb:
                continue
            fb_key = self._select_next_healthy_key(advance_cursor=True)
            if fb_key and self.is_model_available(fb_key, fb_model) and f"{fb_model}::{fb_key}" not in yielded_pairs:
                yielded += 1
                yielded_pairs.add(f"{fb_model}::{fb_key}")
                yield fb_model, fb_key

        # Cứu hộ chống "0 requests": Nếu toàn bộ model đều đang vướng cooldown tạm thời (<= 360s, không phải lỗi 404/Daily Quota 3600s),
        # tự động chọn tối đa 3 cặp (model, key) sắp hết hạn cooldown sớm nhất để luôn thực hiện được request thật!
        if yielded == 0 and self.keys:
            now_rescue = time.monotonic()
            rescue_max_wait = max(360.0, self.DEMOTE_COOLDOWN_SEC + 60.0)
            rescue_candidates: List[Tuple[float, str, str]] = []
            with self._global_lock:
                for m in model_list:
                    global_until = self._model_cooldowns.get(f"*:{m}", 0.0)
                    if global_until - now_rescue > rescue_max_wait:
                        continue  # Bỏ qua các model bị khóa 404 / Daily Quota 60 phút
                    for k in self.keys:
                        if self._global_cooldowns.get(k, 0.0) - now_rescue > rescue_max_wait:
                            continue
                        km_until = self._model_cooldowns.get(f"{k}:{m}", 0.0)
                        if km_until - now_rescue > rescue_max_wait:
                            continue
                        eff_until = max(global_until, km_until, self._model_slow_demote_until.get(m, 0.0))
                        rescue_candidates.append((eff_until, m, k))
            rescue_candidates.sort(key=lambda item: item[0])
            used_rescue_models: set = set()
            for _, r_model, r_key in rescue_candidates:
                if yielded >= min(limit, 3):
                    break
                if r_model in used_rescue_models and len(rescue_candidates) > 3:
                    continue
                pair_id = f"{r_model}::{r_key}"
                if pair_id not in yielded_pairs:
                    used_rescue_models.add(r_model)
                    yielded += 1
                    yielded_pairs.add(pair_id)
                    yield r_model, r_key

    @staticmethod
    def build_fast_json_gen_config(model_name: str, temperature: float = 0.15) -> dict:
        """
        Tạo generationConfig tối ưu độ trễ cho các tác vụ JSON/phân loại nhanh (kế thừa từ Y2A-Auto ai_fallback_client.py):
        - Tự động gắn `thinkingConfig: {"thinkingBudget": 0}` trên các model Flash hỗ trợ (giảm 35-45% thời gian chờ).
        - Tự động bỏ qua `thinkingConfig` trên các model `-lite` hoặc `gemma-` để tránh lỗi 400 Invalid Argument.
        """
        m_low = str(model_name or "").lower()
        cfg: dict = {"temperature": temperature}
        if not m_low.startswith("gemma-"):
            cfg["responseMimeType"] = "application/json"
            if "lite" not in m_low and "pro" not in m_low:
                cfg["thinkingConfig"] = {"thinkingBudget": 0}
        return cfg

    @staticmethod
    def fast_timeout(read_timeout: float = 12.0, connect_timeout: float = 4.0) -> Tuple[float, float]:
        """Tách biệt connect_timeout ngắn (4s) và read_timeout chuẩn Y2A-Auto để chuyển đổi endpoint tức thì khi nghẽn mạng."""
        return (float(connect_timeout), max(float(connect_timeout), float(read_timeout)))

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

