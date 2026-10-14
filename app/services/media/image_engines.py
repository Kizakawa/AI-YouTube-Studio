"""
app.services.media.image_engines — Chuỗi engine sinh ảnh AI (adapter + circuit breaker + health-check).

Thứ tự mặc định (chỉ engine ĐÃ CẤU HÌNH mới được gọi):
  1. cloudflare_flux2   : Cloudflare Workers AI FLUX.2 [klein] (multipart, hỗ trợ width/height 9:16)
  2. cloudflare_schnell : Cloudflare Workers AI FLUX.1 [schnell] (miễn phí 10k neuron/ngày, ảnh vuông → crop dọc)
  3. gemini_image       : Gemini Image (cần bật Billing; free tier quota = 0)
  4. pollinations       : Pollinations (bắt buộc POLLINATIONS_API_KEY từ 2026)

Biến môi trường:
  CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN   → bật engine Cloudflare
  CF_FLUX2_MODEL (mặc định @cf/black-forest-labs/flux-2-klein-4b), CF_FLUX2_ENABLED=1/0
  GEMINI_IMAGE_ENABLED=1                         → bật Gemini Image (đã bật Billing)
  GEMINI_IMAGE_PREMIUM_ONLY=1                    → chỉ dùng Gemini cho cảnh Hook / ảnh bị chấm trượt
  POLLINATIONS_API_KEY                           → bật Pollinations
  IMAGE_ENGINE_ORDER=cloudflare_flux2,cloudflare_schnell,gemini_image,pollinations
"""
from __future__ import annotations

import base64
import io
import logging
import os
import random
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

TARGET_W, TARGET_H = 1080, 1920          # khung Shorts chuẩn
GEN_W, GEN_H = 768, 1344                 # kích thước sinh (9:16, bội số 64)

# Giới hạn số lệnh gọi AI đồng thời trên toàn tiến trình (tránh 429 khi 8 cảnh chạy song song)
_AI_CONCURRENCY = threading.BoundedSemaphore(int(os.getenv("IMAGE_ENGINE_CONCURRENCY", "2") or 2))


class EngineError(Exception):
    """Lỗi có phân loại để circuit breaker quyết định."""

    def __init__(self, msg: str, fatal: bool = False):
        super().__init__(msg)
        self.fatal = fatal  # True = sai cấu hình / hết quota → mở breaker lâu


# ──────────────────────────────────────────────────────────────────────────────
# Circuit breaker: engine lỗi liên tiếp sẽ bị tạm bỏ qua, không tốn thời gian chờ timeout
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class _Breaker:
    failures: int = 0
    open_until: float = 0.0
    last_error: str = ""
    success: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def available(self) -> bool:
        return time.time() >= self.open_until

    def ok(self):
        with self.lock:
            self.failures = 0
            self.open_until = 0.0
            self.success += 1

    def fail(self, err: str, fatal: bool):
        with self.lock:
            self.failures += 1
            self.last_error = err[:200]
            if fatal:
                self.open_until = time.time() + 60 * 60      # sai cấu hình / hết quota: nghỉ 1 giờ
            elif self.failures >= 3:
                self.open_until = time.time() + 15 * 60      # lỗi tạm thời lặp lại: nghỉ 15 phút


_BREAKERS: Dict[str, _Breaker] = {}


def _breaker(name: str) -> _Breaker:
    if name not in _BREAKERS:
        _BREAKERS[name] = _Breaker()
    return _BREAKERS[name]


# ──────────────────────────────────────────────────────────────────────────────
# Hậu xử lý: đưa mọi ảnh về đúng khung dọc 9:16 mà KHÔNG kéo giãn
# ──────────────────────────────────────────────────────────────────────────────
def fit_vertical(img_bytes: bytes, out_path: str) -> Tuple[int, int]:
    """Crop tâm về 9:16 rồi resize Lanczos lên 1080x1920. Trả về kích thước gốc."""
    from PIL import Image

    with Image.open(io.BytesIO(img_bytes)) as im:
        im = im.convert("RGB")
        w, h = im.size
        target_ratio = TARGET_W / TARGET_H
        if abs((w / h) - target_ratio) > 0.01:
            if w / h > target_ratio:            # ảnh quá rộng → cắt hai bên
                new_w = int(h * target_ratio)
                left = (w - new_w) // 2
                im = im.crop((left, 0, left + new_w, h))
            else:                               # ảnh quá cao → cắt trên/dưới (giữ phần trên nhiều hơn)
                new_h = int(w / target_ratio)
                top = max(0, (h - new_h) // 3)
                im = im.crop((0, top, w, top + new_h))
        im = im.resize((TARGET_W, TARGET_H), Image.Resampling.LANCZOS)
        im.save(out_path, "JPEG", quality=94)
        return w, h


# ──────────────────────────────────────────────────────────────────────────────
# Adapters
# ──────────────────────────────────────────────────────────────────────────────
class BaseEngine:
    name = "base"
    premium = False

    def configured(self) -> bool:
        return False

    def generate(self, prompt: str, seed: int) -> bytes:
        raise NotImplementedError


class CloudflareFlux2Engine(BaseEngine):
    name = "cloudflare_flux2"

    def __init__(self):
        self.account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
        self.token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
        self.model = os.getenv("CF_FLUX2_MODEL", "@cf/black-forest-labs/flux-2-klein-4b").strip()

    def configured(self) -> bool:
        return bool(self.account and self.token) and os.getenv("CF_FLUX2_ENABLED", "1") != "0"

    def generate(self, prompt: str, seed: int) -> bytes:
        url = f"https://api.cloudflare.com/client/v4/accounts/{self.account}/ai/run/{self.model}"
        form = {
            "prompt": (None, prompt[:2000]),
            "width": (None, str(GEN_W)),
            "height": (None, str(GEN_H)),
            "seed": (None, str(seed)),
        }
        r = requests.post(url, headers={"Authorization": f"Bearer {self.token}"}, files=form, timeout=90)
        return _parse_cloudflare(r)


class CloudflareSchnellEngine(BaseEngine):
    name = "cloudflare_schnell"

    def __init__(self):
        self.account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
        self.token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()

    def configured(self) -> bool:
        return bool(self.account and self.token)

    def generate(self, prompt: str, seed: int) -> bytes:
        url = f"https://api.cloudflare.com/client/v4/accounts/{self.account}/ai/run/@cf/black-forest-labs/flux-1-schnell"
        payload = {"prompt": prompt[:2000], "steps": 8, "seed": seed}
        r = requests.post(url, headers={"Authorization": f"Bearer {self.token}"}, json=payload, timeout=90)
        return _parse_cloudflare(r)


def _parse_cloudflare(r: requests.Response) -> bytes:
    if r.status_code in (401, 403):
        raise EngineError(f"Cloudflare {r.status_code}: token sai hoặc thiếu quyền Workers AI", fatal=True)
    body_l = r.text.lower() if r.status_code >= 400 else ""
    if r.status_code == 429 or "daily free allocation" in body_l or "neurons" in body_l:
        raise EngineError(f"Cloudflare hết quota neuron ngày: {r.text[:120]}", fatal=True)
    if r.status_code >= 400:
        raise EngineError(f"Cloudflare HTTP {r.status_code}: {r.text[:160]}", fatal=r.status_code in (400, 404))
    ctype = r.headers.get("content-type", "")
    if ctype.startswith("image/"):
        return r.content
    data = r.json()
    img_b64 = (data.get("result") or {}).get("image") or data.get("image")
    if not img_b64:
        raise EngineError(f"Cloudflare không trả ảnh: {str(data)[:160]}")
    return base64.b64decode(img_b64)


class GeminiImageEngine(BaseEngine):
    name = "gemini_image"
    premium = True
    MODELS = ["gemini-3-pro-image", "gemini-3.1-flash-image", "gemini-3.1-flash-lite-image", "gemini-2.5-flash-image"]

    def __init__(self):
        try:
            from config.settings import settings
            keys = settings.get_gemini_keys_pool()
        except Exception:
            keys = [k.strip() for k in os.getenv("GEMINI_API_KEYS", "").split(",") if k.strip()]
        self.keys = list(keys or [])
        custom = os.getenv("GEMINI_IMAGE_MODELS", "").strip()
        if custom:
            self.MODELS = [m.strip() for m in custom.split(",") if m.strip()]

    def configured(self) -> bool:
        return bool(self.keys) and os.getenv("GEMINI_IMAGE_ENABLED", "0") in ("1", "true", "yes")

    def generate(self, prompt: str, seed: int) -> bytes:
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "9:16"}},
        }
        last = ""
        quota_hits = 0
        shuffled_keys = random.sample(self.keys, len(self.keys))
        for model in self.MODELS:
            for key in shuffled_keys:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                r = requests.post(url, json=payload, timeout=90)
                if r.status_code == 200:
                    for cand in r.json().get("candidates", []):
                        for part in cand.get("content", {}).get("parts", []):
                            inline = part.get("inlineData") or part.get("inline_data")
                            if inline and inline.get("data"):
                                return base64.b64decode(inline["data"])
                    last = f"{model}: 200 nhưng không có ảnh (có thể bị lọc an toàn)"
                    continue
                last = f"{model}: HTTP {r.status_code} {r.text[:100]}"
                if r.status_code == 429:
                    quota_hits += 1
                    if "limit: 0" in r.text:
                        break  # Model này không mở Free Tier -> chuyển sang model kế tiếp ngay
                    continue
                if r.status_code == 404:
                    break  # Model đã ngừng hỗ trợ -> chuyển sang model kế tiếp ngay
        raise EngineError(f"Gemini Image thất bại ({last})", fatal=quota_hits >= len(self.keys) * len(self.MODELS))


class PollinationsEngine(BaseEngine):
    name = "pollinations"

    def __init__(self):
        self.key = os.getenv("POLLINATIONS_API_KEY", "").strip()

    def configured(self) -> bool:
        return os.getenv("POLLINATIONS_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")

    def generate(self, prompt: str, seed: int) -> bytes:
        enc = urllib.parse.quote(prompt[:900])
        headers = {"User-Agent": "Mozilla/5.0"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        # Ưu tiên thử model flux (15s), nếu nghẽn mạng tự động chuyển tức thì sang turbo (~7s, 100% miễn phí không cần key)
        last_err = ""
        for model_name, t_out in (("flux", 15), ("turbo", 22)):
            url = f"https://image.pollinations.ai/prompt/{enc}?width={GEN_W}&height={GEN_H}&model={model_name}&nologo=true&seed={seed}"
            try:
                r = requests.get(url, headers=headers, timeout=t_out)
                if r.status_code == 200 and len(r.content) >= 5000:
                    return r.content
                last_err = f"{model_name}: HTTP {r.status_code}"
            except Exception as e:
                last_err = f"{model_name}: {type(e).__name__}"
                continue
        raise EngineError(f"Pollinations thất bại ({last_err})")


_ENGINE_CLASSES = {
    "cloudflare_flux2": CloudflareFlux2Engine,
    "cloudflare_schnell": CloudflareSchnellEngine,
    "gemini_image": GeminiImageEngine,
    "pollinations": PollinationsEngine,
}
_DEFAULT_ORDER = "cloudflare_flux2,cloudflare_schnell,gemini_image,pollinations"


def _engines(premium: bool) -> List[BaseEngine]:
    order = [x.strip() for x in os.getenv("IMAGE_ENGINE_ORDER", _DEFAULT_ORDER).split(",") if x.strip()]
    engines: List[BaseEngine] = []
    for name in order:
        cls = _ENGINE_CLASSES.get(name)
        if not cls:
            continue
        eng = cls()
        if not eng.configured():
            continue
        if eng.premium and os.getenv("GEMINI_IMAGE_PREMIUM_ONLY", "1") in ("1", "true", "yes") and not premium:
            continue
        engines.append(eng)
    if premium:
        # Cảnh Hook: đưa engine cao cấp lên đầu
        engines.sort(key=lambda e: 0 if e.premium else 1)
    return engines


def any_engine_configured() -> bool:
    return bool(_engines(premium=True))


def generate_image(prompt: str, out_path: str, premium: bool = False, seed: Optional[int] = None) -> Tuple[Optional[str], str]:
    """
    Sinh ảnh qua chuỗi engine. Trả về (đường_dẫn | None, tên_engine | lý_do_thất_bại).
    Ảnh luôn được chuẩn hóa về 1080x1920 (crop, không kéo giãn).
    """
    engines = _engines(premium)
    if not engines:
        return None, "no_engine_configured"
    seed = seed if seed is not None else random.randint(1, 2_000_000_000)
    errors = []
    for eng in engines:
        br = _breaker(eng.name)
        if not br.available():
            errors.append(f"{eng.name}=breaker_open")
            continue
        t0 = time.time()
        try:
            with _AI_CONCURRENCY:
                raw = eng.generate(prompt, seed)
            if not raw or len(raw) < 5000:
                raise EngineError("ảnh rỗng/quá nhỏ")
            ow, oh = fit_vertical(raw, out_path)
            br.ok()
            logger.info(f"🎨 [ImageEngine] ✅ {eng.name} ({ow}x{oh} → 1080x1920, {time.time() - t0:.1f}s) → {out_path}")
            return out_path, eng.name
        except EngineError as e:
            br.fail(str(e), e.fatal)
            errors.append(f"{eng.name}={e}")
            logger.warning(f"🎨 [ImageEngine] ❌ {eng.name}: {e}")
        except Exception as e:
            br.fail(str(e), False)
            errors.append(f"{eng.name}={type(e).__name__}")
            logger.warning(f"🎨 [ImageEngine] ❌ {eng.name}: {type(e).__name__}: {e}")
    return None, "; ".join(errors)[:400]


def health_check(live: bool = False) -> Dict[str, str]:
    """
    Kiểm tra trạng thái engine. live=False chỉ đọc cấu hình (không tốn quota);
    live=True sinh thử 1 ảnh nhỏ cho mỗi engine đã cấu hình.
    """
    report: Dict[str, str] = {}
    for name, cls in _ENGINE_CLASSES.items():
        eng = cls()
        if not eng.configured():
            report[name] = "chưa cấu hình"
            continue
        br = _breaker(name)
        if not live:
            report[name] = "sẵn sàng" if br.available() else f"tạm khóa ({br.last_error[:60]})"
            continue
        try:
            raw = eng.generate("a red fox sitting in fresh snow, wildlife photography", 42)
            report[name] = f"OK ({len(raw) // 1024} KB)"
            br.ok()
        except Exception as e:
            report[name] = f"LỖI: {str(e)[:120]}"
            br.fail(str(e), getattr(e, "fatal", False))
    return report


def log_health_on_startup() -> None:
    rep = health_check(live=False)
    ready = [k for k, v in rep.items() if v == "sẵn sàng"]
    for k, v in rep.items():
        logger.info(f"🎨 [ImageEngine] {k:<20} : {v}")
    if not ready:
        logger.warning(
            "⚠️ [ImageEngine] KHÔNG có engine sinh ảnh AI nào được cấu hình → mọi cảnh AI sẽ dùng ảnh web/stock. "
            "Thêm CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN vào .env để bật FLUX miễn phí."
        )
