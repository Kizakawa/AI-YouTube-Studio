"""
app/services/quota_manager.py — Quản trị hạn mức YouTube Data API v3 thời gian thực.

Tính năng:
1. Theo dõi chính xác số Quota Units đã tiêu hao theo từng Google Cloud Project ID.
2. Tự động tính chu kỳ Reset hạn mức theo giờ chuẩn của Google (00:00 PT = 14:00 giờ Việt Nam UTC+7).
3. Cơ chế Quota Guard (Pre-flight check): Ngăn chặn gọi API nếu số quota còn lại < 1.650 units (tránh lỗi 403).
4. Tự động ghi nhận khi Google báo quotaExceeded (khóa dự án đến 14:00 chiều hôm sau).
5. Đồng bộ trạng thái thời gian thực vào bảng quota_trackers trên CSDL (Supabase Cloud).
"""

import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

from app.database.models import get_db, QuotaTracker
from config.settings import settings

logger = logging.getLogger(__name__)

# Chi phí Quota chuẩn của YouTube Data API v3
COST_VIDEO_INSERT = 1600         # Upload 1 video
COST_COMMENT_INSERT = 50         # Đăng bình luận ghim mồi
COST_FULL_SHORTS = 1650          # 1 Video Shorts hoàn chỉnh = 1600 + 50
DEFAULT_DAILY_LIMIT = 10000      # Giới hạn mặc định của 1 Google Cloud Project
SAFETY_RESERVE = 100             # Dự phòng an toàn chống chạm trần tuyệt đối

# Múi giờ Việt Nam (UTC+7)
VN_TZ = timezone(timedelta(hours=7))


def get_current_cycle_id(dt: Optional[datetime] = None) -> str:
    """
    Tính mã ngày chu kỳ hạn mức hiện tại.
    Google reset Quota vào 00:00 Pacific Time = 14:00 Chiều giờ Việt Nam (UTC+7).
    - Từ 14:00 hôm nay đến 13:59 ngày mai được tính chung 1 chu kỳ.
    - Trả về chuỗi dạng 'YYYY-MM-DD' ứng với mốc 14:00 bắt đầu chu kỳ.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    vn_time = dt.astimezone(VN_TZ)
    if vn_time.hour < 14:
        cycle_date = (vn_time - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        cycle_date = vn_time.strftime("%Y-%m-%d")
    return cycle_date


def get_next_reset_time(dt: Optional[datetime] = None) -> datetime:
    """Trả về mốc thời gian (có timezone VN) của đợt reset Quota tiếp theo lúc 14:00 VN."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    vn_time = dt.astimezone(VN_TZ)
    if vn_time.hour < 14:
        reset_today = vn_time.replace(hour=14, minute=0, second=0, microsecond=0)
        return reset_today
    else:
        reset_tomorrow = (vn_time + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
        return reset_tomorrow


def get_project_id_from_client_secret(client_secret_path: str) -> str:
    """Trích xuất Google Cloud Project ID từ file client_secret.json (hỗ trợ cả bản mã hóa AES-256-GCM)."""
    if not client_secret_path or not os.path.exists(client_secret_path):
        return "default_project"
    try:
        from app.utils.security import load_encrypted_json
        data = load_encrypted_json(Path(client_secret_path)) or {}
        for key in ["installed", "web"]:
            if key in data and isinstance(data[key], dict) and "project_id" in data[key]:
                return data[key]["project_id"]
    except Exception as e:
        logger.warning(f"Không thể đọc project_id từ {client_secret_path}: {e}")
    return "default_project"


class QuotaManager:
    """Quản trị hạn mức YouTube API tập trung."""

    PROJECT_FRIENDLY_LABELS: Dict[str, str] = {
        "bionic-client-509515-h2": "YT-Pool-01-US-Main (Gmail G)",
        "youtube-pool-02-510505": "YT-Pool-02-US-Backup (Gmail G)",
        "astute-arcanum-509506-d7": "YT-Pool-03-History (Gmail G)",
        "ai-youtube-studio-509521": "YT-Pool-04-Brazil (Gmail T)",
        "effective-cacao-511414-q3": "YT-Pool-05-Wildlife-Rescue (Gmail T)",
        "yt-pool-06-backup-br": "YT-Pool-06-Backup-BR (Gmail T)",
    }

    @classmethod
    def get_project_label(cls, project_id: str) -> str:
        pid = str(project_id or "").strip()
        if pid in cls.PROJECT_FRIENDLY_LABELS:
            return cls.PROJECT_FRIENDLY_LABELS[pid]
        if "pool-06" in pid.lower() or "pool6" in pid.lower():
            return f"YT-Pool-06-Backup-BR ({pid})"
        return pid or "default_project"

    @staticmethod
    def get_tracker(project_id: str, cycle_date: Optional[str] = None) -> Dict[str, Any]:
        """Lấy thông tin tiêu thụ quota hiện tại của một Google Cloud Project."""
        cycle = cycle_date or get_current_cycle_id()
        label = QuotaManager.get_project_label(project_id)
        with get_db() as db:
            tracker = db.query(QuotaTracker).filter(
                QuotaTracker.project_id == project_id,
                QuotaTracker.cycle_date == cycle
            ).first()

            if not tracker:
                return {
                    "project_id": project_id,
                    "project_label": label,
                    "cycle_date": cycle,
                    "used_units": 0,
                    "upload_count": 0,
                    "max_daily_quota": DEFAULT_DAILY_LIMIT,
                    "remaining_units": DEFAULT_DAILY_LIMIT,
                    "can_upload": True,
                    "next_reset_in": str(get_next_reset_time() - datetime.now(VN_TZ)).split(".")[0]
                }

            remaining = max(0, tracker.max_daily_quota - tracker.used_units)
            can_up = remaining >= (COST_FULL_SHORTS + SAFETY_RESERVE)

            return {
                "project_id": tracker.project_id,
                "project_label": label,
                "cycle_date": tracker.cycle_date,
                "used_units": tracker.used_units,
                "upload_count": tracker.upload_count,
                "max_daily_quota": tracker.max_daily_quota,
                "remaining_units": remaining,
                "can_upload": can_up,
                "next_reset_in": str(get_next_reset_time() - datetime.now(VN_TZ)).split(".")[0]
            }

    @staticmethod
    def can_upload(project_id: str, required_units: int = COST_FULL_SHORTS) -> bool:
        """Kiểm tra xem dự án còn đủ quota để thực hiện upload không."""
        info = QuotaManager.get_tracker(project_id)
        return info["remaining_units"] >= (required_units + SAFETY_RESERVE)

    @staticmethod
    def record_consumption(project_id: str, units: int = COST_FULL_SHORTS, is_upload: bool = True) -> int:
        """Ghi nhận số units đã tiêu hao vào database."""
        cycle = get_current_cycle_id()
        with get_db() as db:
            tracker = db.query(QuotaTracker).filter(
                QuotaTracker.project_id == project_id,
                QuotaTracker.cycle_date == cycle
            ).first()

            if not tracker:
                tracker = QuotaTracker(
                    project_id=project_id,
                    cycle_date=cycle,
                    used_units=units,
                    upload_count=1 if is_upload else 0,
                    max_daily_quota=DEFAULT_DAILY_LIMIT,
                    last_updated=datetime.utcnow()
                )
                db.add(tracker)
            else:
                tracker.used_units += units
                if is_upload:
                    tracker.upload_count += 1
                tracker.last_updated = datetime.utcnow()

            logger.info(
                f"📊 [QuotaManager] Project '{project_id}' chu kỳ {cycle}: "
                f"Đã dùng {tracker.used_units}/{tracker.max_daily_quota} units "
                f"({tracker.upload_count} videos). Còn lại: {tracker.max_daily_quota - tracker.used_units} units."
            )
            return tracker.used_units

    @staticmethod
    def mark_exhausted(project_id: str):
        """Đánh dấu dự án đã cạn kiệt quota (khi nhận lỗi 403 quotaExceeded từ Google)."""
        cycle = get_current_cycle_id()
        with get_db() as db:
            tracker = db.query(QuotaTracker).filter(
                QuotaTracker.project_id == project_id,
                QuotaTracker.cycle_date == cycle
            ).first()

            if not tracker:
                tracker = QuotaTracker(
                    project_id=project_id,
                    cycle_date=cycle,
                    used_units=DEFAULT_DAILY_LIMIT,
                    upload_count=6,
                    max_daily_quota=DEFAULT_DAILY_LIMIT,
                    last_updated=datetime.utcnow()
                )
                db.add(tracker)
            else:
                tracker.used_units = max(tracker.used_units, DEFAULT_DAILY_LIMIT)
                tracker.last_updated = datetime.utcnow()

            logger.warning(
                f"🚨 [QuotaManager] Project '{project_id}' đã chạm trần Quota Google! "
                f"Tất cả upload tiếp theo của project này sẽ được chuyển vào hàng đợi cho đến 14:00 chiều."
            )

    @staticmethod
    def get_all_status() -> List[Dict[str, Any]]:
        """Lấy danh sách trạng thái của tất cả các Google Cloud Project trong hệ thống."""
        import yaml
        cfg_path = Path(settings.BASE_DIR) / "config" / "channels.yaml"
        projects = set()

        if cfg_path.exists():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                for acc in cfg.get("google_accounts", []):
                    c_sec = acc.get("credentials", {}).get("client_secret")
                    if c_sec:
                        full_p = Path(settings.BASE_DIR) / c_sec
                        pid = get_project_id_from_client_secret(str(full_p))
                        if pid and pid != "default_project":
                            projects.add(pid)
            except Exception as e:
                logger.warning(f"Lỗi đọc config channels.yaml: {e}")

        acc_dir = Path(settings.CREDENTIALS_DIR) / "accounts"
        if acc_dir.exists():
            for cs_file in acc_dir.rglob("*secret*.json"):
                pid = get_project_id_from_client_secret(str(cs_file))
                if pid and pid != "default_project":
                    projects.add(pid)

        if not projects:
            projects.add("bionic-client-509515-h2")

        return [QuotaManager.get_tracker(pid) for pid in sorted(projects)]

    @staticmethod
    def _resolve_channel_cred_dir(channel_id: str) -> Path:
        """Tìm thư mục credentials/channels/<id> theo channel_id hoặc alias tương đương."""
        base_ch_dir = Path(settings.CREDENTIALS_DIR) / "channels"
        direct = base_ch_dir / str(channel_id or "").strip()
        if direct.exists():
            return direct
        alias_map = {
            "ch_psychology_of_the_world": ["channel_maya"],
            "channel_maya": ["ch_psychology_of_the_world"],
            "ch_the_mysteries_of_the_world": ["ucsrjiil67qxidtxt70z-iwa"],
            "ucsrjiil67qxidtxt70z-iwa": ["ch_the_mysteries_of_the_world"],
            "ch_alternate_history": ["@alternatehistorywhatif-e2x"],
        }
        for alt in alias_map.get(str(channel_id or "").strip().lower(), []):
            cand = base_ch_dir / alt
            if cand.exists():
                return cand
        try:
            ch_cfg = settings.get_channel_by_id(channel_id) or {}
            cfg_id = str(ch_cfg.get("id") or "").strip()
            if cfg_id and (base_ch_dir / cfg_id).exists():
                return base_ch_dir / cfg_id
        except Exception:
            pass
        return direct

    @staticmethod
    def get_channel_assigned_project_id(channel_id: str) -> str:
        """
        Xác định Google Cloud Project ID được phân bổ chính thức cho kênh trong config/channels.yaml.
        """
        try:
            ch_cfg = settings.get_channel_by_id(channel_id) or {}
            if not ch_cfg:
                channels = settings.get_channels_config()
                ch_cfg = next((c for c in channels if c.get("id") == channel_id), {})
            assigned = str(ch_cfg.get("assigned_project_id") or "").strip()
            if assigned:
                return assigned
            acc_name = ch_cfg.get("google_account", "account_1")
            cs_rel = ch_cfg.get("client_secret") or f"credentials/accounts/{acc_name}/client_secret.json"
            cs_path = Path(settings.BASE_DIR) / cs_rel if not Path(cs_rel).is_absolute() else Path(cs_rel)
            pid = get_project_id_from_client_secret(str(cs_path))
            if pid and pid != "default_project":
                return pid
        except Exception as e:
            logger.debug(f"Lỗi đọc assigned_project_id cho kênh {channel_id}: {e}")
        return "bionic-client-509515-h2"

    @staticmethod
    def get_channel_tokens_pool(channel_id: str) -> List[Dict[str, Any]]:
        """
        Quét toàn bộ danh sách các token OAuth liên kết với kênh này (Project Quota Pool).
        Cho phép 1 kênh liên kết với nhiều Google Cloud Project để tự động xoay Project khi hết hạn mức.
        """
        ch_dir = QuotaManager._resolve_channel_cred_dir(channel_id)
        if not ch_dir.exists():
            return []

        assigned_pid = QuotaManager.get_channel_assigned_project_id(channel_id)
        from app.utils.security import load_encrypted_json
        acc_dir = Path(settings.CREDENTIALS_DIR) / "accounts"
        cid_map = {}
        if acc_dir.exists():
            for acc in os.listdir(acc_dir):
                acc_path = acc_dir / acc
                if not acc_path.is_dir():
                    continue
                for cs in acc_path.glob("*secret*.json"):
                    try:
                        d = load_encrypted_json(cs) or {}
                        info = d.get("installed") or d.get("web") or {}
                        cid = info.get("client_id")
                        pid = info.get("project_id")
                        if cid and pid:
                            cid_map[cid] = {"account_id": acc, "project_id": pid}
                    except Exception:
                        pass
        pool = []
        seen_projects = set()

        token_files = []
        primary_tok = ch_dir / "youtube_token.json"
        if primary_tok.exists():
            token_files.append(primary_tok)

        for p in ch_dir.glob("*.json"):
            if p != primary_tok and ("token" in p.name or "youtube" in p.name):
                token_files.append(p)
        tokens_subdir = ch_dir / "tokens"
        if tokens_subdir.exists():
            for p in tokens_subdir.glob("*.json"):
                token_files.append(p)

        for tf in token_files:
            try:
                t_data = load_encrypted_json(tf)
                if not t_data:
                    continue
                cid = t_data.get("client_id")
                p_info = cid_map.get(cid, {"account_id": "unknown", "project_id": "default_project"})
                pid = p_info["project_id"]
                if pid in seen_projects:
                    continue
                seen_projects.add(pid)

                tracker = QuotaManager.get_tracker(pid)
                can_up = QuotaManager.can_upload(pid, required_units=COST_FULL_SHORTS)
                is_primary = (Path(tf).resolve() == primary_tok.resolve())
                is_assigned = (pid == assigned_pid)
                pool.append({
                    "token_file": str(tf),
                    "is_primary": is_primary,
                    "is_assigned_pool": is_assigned,
                    "project_id": pid,
                    "project_label": QuotaManager.get_project_label(pid),
                    "account_id": p_info["account_id"],
                    "client_id": cid,
                    "used_units": tracker.get("used_units", 0),
                    "remaining_units": tracker.get("remaining_units", 0),
                    "upload_count": tracker.get("upload_count", 0),
                    "can_upload": can_up,
                })
            except Exception as e:
                logger.debug(f"Lỗi kiểm tra token {tf}: {e}")

        # Sắp xếp token:
        # 1. can_upload = True lên đầu
        # 2. Ưu tiên Project được phân bổ riêng cho kênh (is_assigned_pool)
        # 3. Ưu tiên Token chính (is_primary) nếu còn quota
        # 4. Sau đó mới đến remaining_units cao nhất
        pool.sort(
            key=lambda x: (
                x["can_upload"],
                x.get("is_assigned_pool", False),
                x.get("is_primary", False),
                x["remaining_units"],
            ),
            reverse=True,
        )
        return pool

    @staticmethod
    def get_active_token_for_channel(channel_id: str, required_units: int = COST_FULL_SHORTS) -> Tuple[Optional[str], str]:
        """
        Lấy file token và Google Cloud Project ID tối ưu nhất cho kênh.
        Tự động XOAY PROJECT (Project Rotation) nếu project hiện tại hết quota và kênh có dự án dự phòng.
        Nếu tất cả đều hết quota, trả về token chính để hệ thống đưa vào hàng đợi an toàn cho chính kênh đó.
        """
        pool = QuotaManager.get_channel_tokens_pool(channel_id)
        if not pool:
            ch_dir = QuotaManager._resolve_channel_cred_dir(channel_id)
            def_token = str(ch_dir / "youtube_token.json")
            proj_id = QuotaManager.get_channel_project_id(channel_id)
            return (def_token if os.path.exists(def_token) else None, proj_id)

        # Ưu tiên token có thể upload
        for item in pool:
            if QuotaManager.can_upload(item["project_id"], required_units=required_units):
                return (item["token_file"], item["project_id"])

        # Nếu tất cả token đều hết quota, trả về token đầu tiên
        return (pool[0]["token_file"], pool[0]["project_id"])

    @staticmethod
    def get_channel_project_id(channel_id: str) -> str:
        """
        Xác định Google Cloud Project ID hiện tại của kênh.
        Nếu kênh có nhiều token, ưu tiên Project đang còn Quota.
        """
        ch_dir = QuotaManager._resolve_channel_cred_dir(channel_id)
        if ch_dir.exists():
            pool = QuotaManager.get_channel_tokens_pool(channel_id)
            if pool:
                return pool[0]["project_id"]

        ch_token = str(ch_dir / "youtube_token.json")
        if os.path.exists(ch_token):
            try:
                from app.utils.security import load_encrypted_json
                t_data = load_encrypted_json(ch_token)
                tok_cid = t_data.get("client_id") if t_data else None
                if tok_cid:
                    acc_dir = Path(settings.CREDENTIALS_DIR) / "accounts"
                    if acc_dir.exists():
                        for cs in acc_dir.rglob("*secret*.json"):
                            try:
                                d = load_encrypted_json(cs) or {}
                                info = d.get("installed") or d.get("web") or {}
                                if info.get("client_id") == tok_cid:
                                    return info.get("project_id", "default_project")
                            except Exception:
                                pass
            except Exception as e:
                logger.debug(f"Không thể giải mã token kênh {channel_id} để lấy project_id: {e}")

        # Fallback: Đọc từ cấu hình channels.yaml
        return QuotaManager.get_channel_assigned_project_id(channel_id)

    @staticmethod
    def get_channel_quota_info(channel_id: str) -> Dict[str, Any]:
        """Lấy thông tin chi tiết hạn mức Quota của một kênh cụ thể."""
        channels = settings.get_channels_config()
        ch_cfg = next((c for c in channels if c.get("id") == channel_id), {"id": channel_id, "name": channel_id})
        acc_id = ch_cfg.get("google_account", "account_1")
        assigned_pid = QuotaManager.get_channel_assigned_project_id(channel_id)
        assigned_label = QuotaManager.get_project_label(assigned_pid)

        token_path, project_id = QuotaManager.get_active_token_for_channel(channel_id)
        has_token = token_path is not None and os.path.exists(token_path)
        tracker = QuotaManager.get_tracker(project_id)
        assigned_tracker = QuotaManager.get_tracker(assigned_pid)
        can_up = has_token and QuotaManager.can_upload(project_id)
        pool = QuotaManager.get_channel_tokens_pool(channel_id)

        return {
            "channel_id": channel_id,
            "channel_name": ch_cfg.get("name", channel_id),
            "account_id": acc_id,
            "assigned_project_id": assigned_pid,
            "assigned_project_label": assigned_label,
            "assigned_remaining_units": assigned_tracker.get("remaining_units", 0),
            "project_id": project_id,
            "project_label": QuotaManager.get_project_label(project_id),
            "has_token": has_token,
            "token_file": token_path,
            "pool_size": len(pool),
            "used_units": tracker.get("used_units", 0),
            "remaining_units": tracker.get("remaining_units", 0),
            "upload_count": tracker.get("upload_count", 0),
            "can_upload": can_up,
            "next_reset_in": tracker.get("next_reset_in", "")
        }

    @staticmethod
    def get_available_channels(exclude_channel_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Trả về danh sách các kênh ĐÃ CẤP QUYỀN OAUTH và CÒN ĐỦ HẠN MỨC QUOTA để upload video Shorts.
        Sắp xếp ưu tiên kênh có Quota nhiều nhất lên đầu.
        """
        channels = settings.get_channels_config()
        available = []
        for ch in channels:
            cid = ch.get("id")
            if exclude_channel_id and cid == exclude_channel_id:
                continue
            info = QuotaManager.get_channel_quota_info(cid)
            if info["can_upload"]:
                available.append(info)

        # Sắp xếp kênh theo số Quota còn lại nhiều nhất
        available.sort(key=lambda x: x["remaining_units"], reverse=True)
        return available

    @staticmethod
    def get_next_available_channel(current_channel_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Lấy kênh tối ưu tiếp theo để tự động xoay kênh khi kênh hiện tại hết Quota."""
        avail = QuotaManager.get_available_channels(exclude_channel_id=current_channel_id)
        return avail[0] if avail else None
