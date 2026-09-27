"""
app/utils/security.py — Bộ công cụ bảo mật & mã hoá chuẩn AES-256-GCM.

Chức năng:
1. Mã hoá / Giải mã dữ liệu nhạy cảm (Tokens, Credentials, API Keys) theo chuẩn AES-256-GCM (Authenticated Encryption).
2. Hỗ trợ tương thích ngược 100% (Transparent Fallback): tự động giải mã nếu file đã mã hoá, đọc trực tiếp nếu là plain JSON cũ.
3. Kiểm tra an toàn định danh & đường dẫn (Path Traversal & Safe Identifier).
"""

import os
import re
import json
import base64
import hashlib
import logging
from pathlib import Path
from typing import Any, Optional, Union

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

# Tiền tố nhận diện nội dung đã được mã hoá AES-256-GCM
AES_PREFIX = b"AES256GCM:"
AES_PREFIX_STR = "AES256GCM:"

_CACHED_MASTER_KEY: Optional[bytes] = None


def get_master_key() -> bytes:
    """
    Lấy khoá mã hoá chủ AES-256 (32 bytes).
    1. Ưu tiên đọc từ biến môi trường ENCRYPTION_MASTER_KEY.
    2. Nếu chưa có, tự động đọc hoặc khởi tạo file bí mật credentials/.master.key.
    """
    global _CACHED_MASTER_KEY
    if _CACHED_MASTER_KEY is not None:
        return _CACHED_MASTER_KEY

    env_key = os.getenv("ENCRYPTION_MASTER_KEY", "").strip()
    if env_key:
        if len(env_key) == 64:
            try:
                _CACHED_MASTER_KEY = bytes.fromhex(env_key)
                return _CACHED_MASTER_KEY
            except ValueError:
                pass
        # Băm SHA-256 để luôn tạo đúng 32 bytes khoá AES-256 từ chuỗi bất kỳ
        _CACHED_MASTER_KEY = hashlib.sha256(env_key.encode("utf-8")).digest()
        return _CACHED_MASTER_KEY

    # Tìm hoặc tạo file .master.key trong thư mục credentials
    from config.settings import settings
    cred_dir = Path(settings.BASE_DIR) / "credentials"
    cred_dir.mkdir(parents=True, exist_ok=True)
    key_file = cred_dir / ".master.key"

    if key_file.exists():
        try:
            with open(key_file, "rb") as f:
                raw = f.read().strip()
            if len(raw) == 32:
                _CACHED_MASTER_KEY = raw
                return _CACHED_MASTER_KEY
            elif len(raw) == 44:  # Base64 32 bytes
                _CACHED_MASTER_KEY = base64.b64decode(raw)
                return _CACHED_MASTER_KEY
            else:
                _CACHED_MASTER_KEY = hashlib.sha256(raw).digest()
                return _CACHED_MASTER_KEY
        except Exception as e:
            logger.warning(f"Lỗi đọc key từ {key_file}: {e}. Tạo khoá mới...")

    # Tạo khoá AES-256 ngẫu nhiên chuẩn mật mã (32 bytes = 256 bits)
    new_key = AESGCM.generate_key(bit_length=256)
    try:
        with open(key_file, "wb") as f:
            f.write(new_key)
        # Giới hạn quyền đọc file trên hệ thống
        if os.name != "nt":
            try:
                os.chmod(key_file, 0o600)
            except Exception:
                pass
        logger.info(f"🔑 [Security] Đã tạo khoá mã hoá AES-256 mới tại {key_file}")
    except Exception as ex:
        logger.error(f"Không thể ghi {key_file}: {ex}")

    _CACHED_MASTER_KEY = new_key
    return _CACHED_MASTER_KEY


def encrypt_bytes(data: bytes) -> bytes:
    """Mã hoá dữ liệu dạng bytes bằng AES-256-GCM với 12-byte random nonce."""
    if not data:
        return data
    key = get_master_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 96-bit nonce theo chuẩn NIST SP 800-38D
    ciphertext = aesgcm.encrypt(nonce, data, None)
    payload = base64.b64encode(nonce + ciphertext)
    return AES_PREFIX + payload


def decrypt_bytes(data: bytes) -> bytes:
    """
    Giải mã dữ liệu AES-256-GCM.
    Nếu dữ liệu chưa được mã hoá (không có tiền tố AES256GCM:), trả về nguyên bản (Fallback).
    """
    if not data:
        return data
    if not data.startswith(AES_PREFIX):
        return data  # Dữ liệu dạng thô cũ, trả về nguyên bản

    try:
        raw_b64 = data[len(AES_PREFIX):]
        combined = base64.b64decode(raw_b64)
        if len(combined) < 28:  # 12-byte nonce + 16-byte GCM tag
            return data
        nonce = combined[:12]
        ciphertext = combined[12:]
        key = get_master_key()
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as e:
        logger.error(f"Lỗi giải mã AES-256-GCM: {e}")
        return data


def encrypt_string(text: str) -> str:
    """Mã hoá chuỗi ký tự bằng AES-256-GCM, trả về chuỗi có tiền tố 'AES256GCM:'."""
    if not text:
        return text
    enc = encrypt_bytes(text.encode("utf-8"))
    return enc.decode("ascii")


def decrypt_string(text: str) -> str:
    """Giải mã chuỗi ký tự AES-256-GCM, tự động fallback nếu là chuỗi thô thông thường."""
    if not text:
        return text
    if not text.startswith(AES_PREFIX_STR):
        return text
    dec = decrypt_bytes(text.encode("ascii"))
    return dec.decode("utf-8")


def save_encrypted_json(file_path: Union[str, Path], data: Any) -> bool:
    """Lưu dữ liệu cấu trúc (dict/list) xuống file sau khi mã hoá AES-256-GCM."""
    try:
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        encrypted_bytes = encrypt_bytes(json_bytes)
        with open(p, "wb") as f:
            f.write(encrypted_bytes)
        return True
    except Exception as e:
        logger.error(f"Lỗi lưu file mã hoá {file_path}: {e}")
        return False


def load_encrypted_json(file_path: Union[str, Path]) -> Optional[Any]:
    """
    Đọc dữ liệu từ file đã mã hoá AES-256-GCM.
    Tương thích ngược: Nếu file vẫn là JSON thô chưa mã hoá, vẫn nạp bình thường 100%.
    """
    p = Path(file_path)
    if not p.exists():
        return None
    try:
        with open(p, "rb") as f:
            raw_bytes = f.read()
        decrypted_bytes = decrypt_bytes(raw_bytes)
        return json.loads(decrypted_bytes.decode("utf-8"))
    except Exception as e:
        logger.error(f"Lỗi đọc file JSON (giải mã AES) từ {file_path}: {e}")
        return None


# ─── BỘ LỌC ĐẦU VÀO VÀ ĐƯỜNG DẪN AN TOÀN (PATH TRAVERSAL SANITIZATION) ──────────

SAFE_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+$")


def is_safe_identifier(identifier: Optional[str]) -> bool:
    """Kiểm tra mã định danh (channel_id, account_id...) chỉ chứa chữ cái, số, gạch nối hoặc gạch dưới."""
    if not identifier:
        return False
    clean = str(identifier).strip()
    return bool(SAFE_ID_REGEX.match(clean)) and (".." not in clean)


def is_safe_path(target_path: Union[str, Path], allowed_root: Union[str, Path]) -> bool:
    """Kiểm tra đường dẫn đích có nằm hoàn toàn bên trong thư mục gốc cho phép hay không."""
    try:
        target = Path(target_path).resolve()
        root = Path(allowed_root).resolve()
        return target.is_relative_to(root)
    except Exception:
        return False
