# app/services/token_vault.py
import os
import json
import base64
import zlib
from pathlib import Path
import logging
logger = logging.getLogger("TokenVault")
from config.settings import settings
from app.utils.security import save_encrypted_json, load_encrypted_json


def _load_vault_bundle_str(base_dir: Path) -> str:
    """Đọc dữ liệu sao lưu token từ biến môi trường hoặc tệp cục bộ không theo dõi bởi Git."""
    env_bundle = os.getenv("TOKEN_VAULT_BUNDLE", "").strip()
    if env_bundle:
        return env_bundle
    local_bundle_file = base_dir / "credentials" / ".vault_bundle.b64"
    if local_bundle_file.exists():
        try:
            return local_bundle_file.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.debug(f"[TokenVault] Không thể đọc {local_bundle_file}: {e}")
    return ""


def restore_all_embedded_tokens():
    """
    Tự động khôi phục tất cả 11 token (3 tài khoản mẹ + 5 kênh YouTube)
    nếu chưa tồn tại, rỗng hoặc bị lỗi sau khi khởi động lại.
    """
    base_dir = Path(settings.BASE_DIR)
    bundle_str = _load_vault_bundle_str(base_dir)
    if not bundle_str:
        return 0
    try:
        raw_compressed = base64.b64decode(bundle_str)
        json_bytes = zlib.decompress(raw_compressed)
        payload = json.loads(json_bytes.decode("utf-8"))
    except Exception as e:
        logger.error(f"[TokenVault] Không thể giải mã bundle: {e}")
        return 0

    restored_count = 0
    for rel_path, token_dict in payload.items():
        try:
            full_path = base_dir / rel_path
            need_restore = False
            if not full_path.exists() or full_path.stat().st_size < 10:
                need_restore = True
            else:
                try:
                    tok = load_encrypted_json(full_path)
                    if not tok.get("refresh_token") and not tok.get("token"):
                        need_restore = True
                except Exception:
                    need_restore = True

            if need_restore:
                full_path.parent.mkdir(parents=True, exist_ok=True)
                save_encrypted_json(full_path, token_dict)
                restored_count += 1
                logger.info(f"[TokenVault] Đã tự động khôi phục token: {rel_path}")
        except Exception as e:
            logger.warning(f"[TokenVault] Lỗi khôi phục {rel_path}: {e}")

    if restored_count > 0:
        logger.info(f"[TokenVault] Đã khôi phục thành công {restored_count} tokens vào hệ thống.")
    return restored_count
