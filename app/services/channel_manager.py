import os
import json
import yaml
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from config.settings import settings

logger = logging.getLogger(__name__)

def read_channels_yaml() -> Dict[str, Any]:
    """Đọc file channels.yaml an toàn."""
    yaml_path = settings.CHANNELS_CONFIG_FILE
    if not yaml_path.exists():
        return {"google_accounts": [], "channels": []}
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {"google_accounts": [], "channels": []}
    except Exception as e:
        logger.error(f"Lỗi đọc channels.yaml: {e}")
        return {"google_accounts": [], "channels": []}

def write_channels_yaml(data: Dict[str, Any]) -> bool:
    """Ghi đè channels.yaml và xóa cache in-memory của settings."""
    yaml_path = settings.CHANNELS_CONFIG_FILE
    try:
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False, indent=2)
        settings._cached_channels = None
        settings._cached_accounts = None
        return True
    except Exception as e:
        logger.error(f"Lỗi ghi channels.yaml: {e}")
        return False

def check_account_tokens(account_id: str) -> Dict[str, Any]:
    """Kiểm tra sự tồn tại và tính hợp lệ của token trong credentials/accounts/{account_id}/"""
    base_dir = Path(settings.BASE_DIR)
    acc_dir = base_dir / "credentials" / "accounts" / account_id

    client_secret_path = acc_dir / "client_secret.json"
    has_client_secret = client_secret_path.exists() and client_secret_path.stat().st_size > 10

    # Fallback nếu account_1 có secret thì có thể dùng chung
    if not has_client_secret:
        fallback = base_dir / "credentials" / "accounts" / "account_1" / "client_secret.json"
        if fallback.exists() and fallback.stat().st_size > 10:
            has_client_secret = True

    yt_token_path = acc_dir / "youtube_token.json"
    youtube_connected = False
    if yt_token_path.exists():
        try:
            with open(yt_token_path, "r", encoding="utf-8") as f:
                tok = json.load(f)
                if tok.get("token") or tok.get("refresh_token"):
                    youtube_connected = True
        except Exception:
            pass

    drive_token_path = acc_dir / "drive_token.json"
    drive_connected = False
    if drive_token_path.exists():
        try:
            with open(drive_token_path, "r", encoding="utf-8") as f:
                tok = json.load(f)
                if tok.get("token") or tok.get("refresh_token"):
                    drive_connected = True
        except Exception:
            pass

    return {
        "has_client_secret": has_client_secret,
        "youtube_connected": youtube_connected,
        "drive_connected": drive_connected
    }

def get_accounts_enriched() -> List[Dict[str, Any]]:
    """Trả về danh sách tài khoản Google kèm trạng thái token và các kênh con."""
    cfg = read_channels_yaml()
    raw_accounts = cfg.get("google_accounts", [])
    enriched = []
    for acc in raw_accounts:
        acc_id = acc.get("id", "")
        token_info = check_account_tokens(acc_id)
        brand_channels = acc.get("brand_channels", [])
        enriched.append({
            "id": acc_id,
            "email_label": acc.get("email_label", ""),
            "drive_storage": acc.get("drive_storage", "2 TB Google One"),
            "youtube_quota_daily": acc.get("youtube_quota_daily", 10000),
            "has_client_secret": token_info["has_client_secret"],
            "youtube_connected": token_info["youtube_connected"],
            "drive_connected": token_info["drive_connected"],
            "brand_channels": brand_channels,
            "channels_count": len(brand_channels)
        })
    return enriched

def get_flat_channels() -> List[Dict[str, Any]]:
    """Trả về danh sách phẳng tất cả các kênh YouTube."""
    cfg = read_channels_yaml()
    return cfg.get("channels", [])

def get_channel(channel_id: str) -> Optional[Dict[str, Any]]:
    """Tìm thông tin kênh theo channel_id trong cả flat list và brand_channels."""
    cfg = read_channels_yaml()
    for c in cfg.get("channels", []):
        if c.get("id") == channel_id:
            # Tìm thêm thông tin chi tiết trong brand_channels nếu có
            acc_id = c.get("google_account")
            if acc_id:
                for a in cfg.get("google_accounts", []):
                    if a.get("id") == acc_id:
                        for bc in a.get("brand_channels", []):
                            if bc.get("id") == channel_id:
                                merged = {**c, **bc}
                                merged["google_account"] = acc_id
                                return merged
            return c
    return None

def add_account(account_id: str, email_label: str, drive_storage: str = "2 TB Google One", youtube_quota_daily: int = 10000) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Tạo mới một tài khoản Google mẹ."""
    clean_id = account_id.strip().lower().replace(" ", "_")
    if not clean_id:
        return False, "Mã tài khoản không được để trống", None

    cfg = read_channels_yaml()
    accounts = cfg.setdefault("google_accounts", [])
    if any(a.get("id") == clean_id for a in accounts):
        return False, f"Tài khoản '{clean_id}' đã tồn tại", None

    acc_dir = Path(settings.BASE_DIR) / "credentials" / "accounts" / clean_id
    acc_dir.mkdir(parents=True, exist_ok=True)

    new_acc = {
        "id": clean_id,
        "email_label": email_label.strip(),
        "drive_storage": drive_storage or "2 TB Google One",
        "youtube_quota_daily": int(youtube_quota_daily) if youtube_quota_daily else 10000,
        "credentials": {
            "client_secret": f"credentials/accounts/{clean_id}/client_secret.json",
            "youtube_token": f"credentials/accounts/{clean_id}/youtube_token.json",
            "drive_token": f"credentials/accounts/{clean_id}/drive_token.json"
        },
        "brand_channels": []
    }
    accounts.append(new_acc)
    write_channels_yaml(cfg)
    return True, f"Đã thêm tài khoản Google '{clean_id}' thành công", new_acc

def delete_account(account_id: str) -> Tuple[bool, str]:
    """Xóa tài khoản Google và các kênh con của nó."""
    cfg = read_channels_yaml()
    accounts = cfg.get("google_accounts", [])
    target = next((a for a in accounts if a.get("id") == account_id), None)
    if not target:
        return False, f"Không tìm thấy tài khoản '{account_id}'"

    cfg["google_accounts"] = [a for a in accounts if a.get("id") != account_id]
    flat_channels = cfg.get("channels", [])
    cfg["channels"] = [c for c in flat_channels if c.get("google_account") != account_id]

    write_channels_yaml(cfg)
    return True, f"Đã xóa tài khoản '{account_id}' và các kênh liên kết"

def add_channel(account_id: str, channel_id: str, name: str, niche: str, language: str = "en", visual_mode: str = "imagen_ai", preferred_voice: str = "en-US-ChristopherNeural", publish_hour_utc: int = 12, days: list = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Thêm một kênh thương hiệu mới trực thuộc tài khoản Google."""
    clean_id = channel_id.strip().lower().replace(" ", "_")
    if not clean_id:
        return False, "Mã kênh không được để trống", None

    cfg = read_channels_yaml()
    accounts = cfg.get("google_accounts", [])
    account = next((a for a in accounts if a.get("id") == account_id), None)
    if not account:
        # Nếu chưa có tài khoản nào, tự động tạo account_1 làm mặc định
        if not accounts:
            _, _, account = add_account("account_1", "default_hub@gmail.com")
            cfg = read_channels_yaml()
            account = cfg["google_accounts"][0]
        else:
            account = accounts[0]
            account_id = account.get("id")

    brand_channels = account.setdefault("brand_channels", [])
    if any(c.get("id") == clean_id for c in brand_channels):
        return False, f"Kênh '{clean_id}' đã tồn tại trong tài khoản '{account_id}'", None

    new_channel = {
        "id": clean_id,
        "name": name.strip(),
        "niche": niche.strip() if niche else "general",
        "language": language.strip() if language else "en",
        "visual_mode": visual_mode or "imagen_ai",
        "preferred_voice": preferred_voice or "en-US-ChristopherNeural",
        "schedule": {
            "publish_hour_utc": int(publish_hour_utc) if publish_hour_utc is not None else 12,
            "days": days or ["monday", "wednesday", "friday", "sunday"]
        }
    }
    brand_channels.append(new_channel)

    flat_channels = cfg.setdefault("channels", [])
    flat_entry = next((c for c in flat_channels if c.get("id") == clean_id), None)
    if not flat_entry:
        flat_channels.append({
            "id": clean_id,
            "name": new_channel["name"],
            "niche": new_channel["niche"],
            "language": new_channel["language"],
            "google_account": account_id,
            "visual_mode": new_channel["visual_mode"],
            "preferred_voice": new_channel["preferred_voice"]
        })
    else:
        flat_entry.update({
            "name": new_channel["name"],
            "niche": new_channel["niche"],
            "language": new_channel["language"],
            "google_account": account_id,
            "visual_mode": new_channel["visual_mode"],
            "preferred_voice": new_channel["preferred_voice"]
        })

    write_channels_yaml(cfg)
    return True, f"Đã thêm kênh '{new_channel['name']}' thành công", new_channel

def update_channel(channel_id: str, name: str = None, niche: str = None, language: str = None, visual_mode: str = None, preferred_voice: str = None, publish_hour_utc: int = None, days: list = None, target_account_id: str = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Cập nhật thông tin kênh YouTube."""
    cfg = read_channels_yaml()
    found_channel = None
    parent_account = None

    for acc in cfg.get("google_accounts", []):
        for c in acc.get("brand_channels", []):
            if c.get("id") == channel_id:
                found_channel = c
                parent_account = acc
                break
        if found_channel:
            break

    if not found_channel:
        # Kiểm tra trong flat channels
        flat_ch = next((c for c in cfg.get("channels", []) if c.get("id") == channel_id), None)
        if not flat_ch:
            return False, f"Không tìm thấy kênh '{channel_id}' để sửa", None
        # Đưa vào account đầu tiên nếu chưa có
        accounts = cfg.setdefault("google_accounts", [])
        if not accounts:
            add_account("account_1", "default_hub@gmail.com")
            cfg = read_channels_yaml()
            accounts = cfg["google_accounts"]
        parent_account = accounts[0]
        found_channel = {
            "id": channel_id,
            "name": flat_ch.get("name", channel_id),
            "niche": flat_ch.get("niche", "general"),
            "language": flat_ch.get("language", "en"),
            "visual_mode": flat_ch.get("visual_mode", "imagen_ai"),
            "preferred_voice": flat_ch.get("preferred_voice", "en-US-ChristopherNeural"),
            "schedule": {"publish_hour_utc": 12, "days": ["monday", "wednesday", "friday", "sunday"]}
        }
        parent_account.setdefault("brand_channels", []).append(found_channel)

    if name is not None and name.strip():
        found_channel["name"] = name.strip()
    if niche is not None and niche.strip():
        found_channel["niche"] = niche.strip()
    if language is not None and language.strip():
        found_channel["language"] = language.strip()
    if visual_mode is not None and visual_mode.strip():
        found_channel["visual_mode"] = visual_mode.strip()
    if preferred_voice is not None and preferred_voice.strip():
        found_channel["preferred_voice"] = preferred_voice.strip()

    if publish_hour_utc is not None or days is not None:
        sched = found_channel.setdefault("schedule", {})
        if publish_hour_utc is not None:
            sched["publish_hour_utc"] = int(publish_hour_utc)
        if days is not None:
            sched["days"] = days

    # Cập nhật trong flat channels
    for fc in cfg.setdefault("channels", []):
        if fc.get("id") == channel_id:
            fc["name"] = found_channel["name"]
            fc["niche"] = found_channel["niche"]
            fc["language"] = found_channel["language"]
            fc["preferred_voice"] = found_channel["preferred_voice"]
            fc["visual_mode"] = found_channel["visual_mode"]
            if parent_account:
                fc["google_account"] = parent_account.get("id")

    write_channels_yaml(cfg)
    return True, f"Đã cập nhật kênh '{found_channel['name']}' thành công", found_channel

def delete_channel(channel_id: str) -> Tuple[bool, str]:
    """Xóa kênh khỏi cả tài khoản mẹ và flat channels."""
    cfg = read_channels_yaml()
    removed = False

    for acc in cfg.get("google_accounts", []):
        before = len(acc.get("brand_channels", []))
        acc["brand_channels"] = [c for c in acc.get("brand_channels", []) if c.get("id") != channel_id]
        if len(acc.get("brand_channels", [])) < before:
            removed = True

    flat_before = len(cfg.get("channels", []))
    cfg["channels"] = [c for c in cfg.get("channels", []) if c.get("id") != channel_id]
    if len(cfg.get("channels", [])) < flat_before:
        removed = True

    if not removed:
        return False, f"Không tìm thấy kênh '{channel_id}' để xóa"

    write_channels_yaml(cfg)
    return True, f"Đã xóa kênh '{channel_id}' thành công"
