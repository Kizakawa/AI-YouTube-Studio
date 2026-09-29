# app/services/token_vault.py
import json
import base64
import zlib
from pathlib import Path
import logging
logger = logging.getLogger("TokenVault")
from config.settings import settings
from app.utils.security import save_encrypted_json

# Embedded pre-authenticated tokens for all 3 Google accounts and 5 YouTube channels
_VAULT_BUNDLE = "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"

def restore_all_embedded_tokens():
    """
    Tự động khôi phục tất cả 11 token (3 tài khoản mẹ + 5 kênh YouTube)
    nếu chưa tồn tại hoặc bị mất sau khi Render khởi động lại.
    """
    base_dir = Path(settings.BASE_DIR)
    try:
        raw_compressed = base64.b64decode(_VAULT_BUNDLE)
        json_bytes = zlib.decompress(raw_compressed)
        payload = json.loads(json_bytes.decode("utf-8"))
    except Exception as e:
        logger.error(f"[TokenVault] Không thể giải mã bundle: {e}")
        return 0

    restored_count = 0
    for rel_path, token_dict in payload.items():
        try:
            full_path = base_dir / rel_path
            if not full_path.exists() or full_path.stat().st_size < 10:
                full_path.parent.mkdir(parents=True, exist_ok=True)
                save_encrypted_json(full_path, token_dict)
                restored_count += 1
                logger.info(f"[TokenVault] Đã tự động khôi phục token: {rel_path}")
        except Exception as e:
            logger.warning(f"[TokenVault] Lỗi khôi phục {rel_path}: {e}")

    if restored_count > 0:
        logger.info(f"[TokenVault] Đã khôi phục thành công {restored_count} tokens vào hệ thống.")
    return restored_count
