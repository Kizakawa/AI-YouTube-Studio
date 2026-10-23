import os
import yaml
from pathlib import Path
from typing import Dict, List, Optional
from dotenv import load_dotenv

# Xác định thư mục gốc của dự án
BASE_DIR = Path(__file__).resolve().parent.parent

# Tải biến môi trường từ .env
load_dotenv(BASE_DIR / ".env")

class Settings:
    BASE_DIR: Path = BASE_DIR

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    ALLOWED_USERS_RAW: str = os.getenv("ALLOWED_TELEGRAM_USERS", "")
    ALLOWED_GROUP_IDS_RAW: str = os.getenv("ALLOWED_TELEGRAM_GROUPS", "")
    
    # AI Services (Hỗ trợ cụm tài khoản Google Ultra xoay vòng API Keys)
    GEMINI_API_KEYS_RAW: str = os.getenv("GEMINI_API_KEYS", "")
    GEMINI_API_KEY_SINGLE: str = os.getenv("GEMINI_API_KEY", "")
    PEXELS_API_KEYS_RAW: str = os.getenv("PEXELS_API_KEYS", "")
    PEXELS_API_KEY: str = os.getenv("PEXELS_API_KEY", "")
    
    # Pixabay API (Kho B-Roll 3D, CGI và animation trừu tượng, miễn phí)
    PIXABAY_API_KEY: str = os.getenv("PIXABAY_API_KEY", "")

    # Hugging Face Access Token (Sinh ảnh/video AI Cloud cao cấp FLUX / Wan)
    HUGGINGFACE_TOKEN: str = os.getenv("HUGGINGFACE_TOKEN", "") or os.getenv("HF_TOKEN", "")

    # Google Custom Search (Tùy chọn — Tìm ảnh nhân vật thực, miễn phí 100 queries/ngày)
    GOOGLE_SEARCH_API_KEY: str = os.getenv("GOOGLE_SEARCH_API_KEY", "")
    GOOGLE_SEARCH_CX: str = os.getenv("GOOGLE_SEARCH_CX", "")

    # Render Backend (Chế độ dựng video máy chủ cục bộ / Docker)
    RENDER_BACKEND: str = os.getenv("RENDER_BACKEND", "local")

    # Hardware Acceleration / GPU Video Rendering (Tăng tốc render video bằng GPU)
    ENABLE_GPU_ACCELERATION: bool = os.getenv("ENABLE_GPU_ACCELERATION", "true").lower() in ("true", "1", "yes")
    FORCE_GPU_ENCODER: str = os.getenv("FORCE_GPU_ENCODER", "")  # "auto", "h264_nvenc", "h264_mf", "h264_qsv", "libx264"

    # Open Web Image Search (Tìm ảnh trên toàn mạng Internet & Bỏ qua rào cản bản quyền)
    ENABLE_WEB_IMAGE_SEARCH: bool = os.getenv("ENABLE_WEB_IMAGE_SEARCH", "true").lower() in ("true", "1", "yes")
    BYPASS_IMAGE_COPYRIGHT: bool = os.getenv("BYPASS_IMAGE_COPYRIGHT", "true").lower() in ("true", "1", "yes")

    # Kiếm tiền & Quyên góp ủng hộ (Monetization & PayPal Donations — Mặc định FALSE cho Shorts vì link ngoài không click được)
    PAYPAL_DONATION_URL: str = os.getenv("PAYPAL_DONATION_URL", "")
    ENABLE_DONATION_CTA: bool = os.getenv("ENABLE_DONATION_CTA", "false").lower() in ("true", "1", "yes")
    AMAZON_ASSOCIATE_TAG: str = os.getenv("AMAZON_ASSOCIATE_TAG", "kizakawa-20")
    ENABLE_AMAZON_AFFILIATE: bool = os.getenv("ENABLE_AMAZON_AFFILIATE", "false").lower() in ("true", "1", "yes")

    # Defaults
    DEFAULT_TARGET_LANGUAGE: str = os.getenv("DEFAULT_TARGET_LANGUAGE", "en")
    DEFAULT_TTS_VOICE: str = os.getenv("DEFAULT_TTS_VOICE", "en-US-AndrewNeural")
    USE_LANGGRAPH: bool = os.getenv("USE_LANGGRAPH", "true").lower() in ("true", "1", "yes")
    ENABLE_DEVICE_GATEKEEPER: bool = os.getenv("ENABLE_DEVICE_GATEKEEPER", "true").lower() in ("true", "1", "yes")
    @staticmethod
    def _resolve_agent_api_key(base_dir: Path) -> str:
        env_k = os.getenv("AGENT_API_KEY", "").strip()
        if env_k and env_k != "agy_agent_supersecret_key_2026":
            return env_k
        key_file = base_dir / "credentials" / ".agent.key"
        try:
            if key_file.exists():
                saved = key_file.read_text(encoding="utf-8").strip()
                if len(saved) >= 24:
                    return saved
            import secrets
            key_file.parent.mkdir(parents=True, exist_ok=True)
            generated = f"agy_sec_{secrets.token_urlsafe(32)}"
            key_file.write_text(generated, encoding="utf-8")
            return generated
        except Exception:
            import secrets
            return f"agy_sec_{secrets.token_urlsafe(32)}"

    AGENT_API_KEY: str = _resolve_agent_api_key.__func__(BASE_DIR)
    LANGSMITH_API_KEY: str = os.getenv("LANGSMITH_API_KEY", "") or os.getenv("LANGCHAIN_API_KEY", "")
    LANGSMITH_PROJECT: str = os.getenv("LANGSMITH_PROJECT", "") or os.getenv("LANGCHAIN_PROJECT", "AI-YouTube-Studio")
    LANGSMITH_TRACING: bool = os.getenv("LANGSMITH_TRACING", os.getenv("LANGCHAIN_TRACING_V2", "true")).lower() in ("true", "1", "yes")
    
    # Tự động hóa sản xuất & đăng bài (Mặc định FALSE để tránh bot tự ý sinh video linh tinh)
    ENABLE_OVERDUE_AUTO_PRODUCTION: bool = os.getenv("ENABLE_OVERDUE_AUTO_PRODUCTION", "false").lower() in ("true", "1", "yes")
    ENABLE_FALLBACK_AUTO_PUBLISH: bool = os.getenv("ENABLE_FALLBACK_AUTO_PUBLISH", "false").lower() in ("true", "1", "yes")
    AUTO_ROTATE_CHANNELS_ON_QUOTA: bool = os.getenv("AUTO_ROTATE_CHANNELS_ON_QUOTA", "false").lower() in ("true", "1", "yes")
    AUTOPILOT_ENABLED_CHANNELS: str = os.getenv("AUTOPILOT_ENABLED_CHANNELS", "")
    
    # Storage Paths
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/storage/ai_youtube.db")
    TEMP_DIR: Path = BASE_DIR / "storage" / "temp"
    OUTPUT_DIR: Path = BASE_DIR / "storage" / "outputs"
    JOBS_DIR: Path = BASE_DIR / "storage" / "jobs"
    CREDENTIALS_DIR: Path = BASE_DIR / "credentials"
    ACCOUNTS_DIR: Path = BASE_DIR / "credentials" / "accounts"
    CHANNELS_CONFIG_FILE: Path = BASE_DIR / "config" / "channels.yaml"

    @classmethod
    def ensure_storage_dirs(cls):
        """Tự động đảm bảo tất cả thư mục lưu trữ và SQLite database luôn tồn tại"""
        cls.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        cls.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cls.JOBS_DIR.mkdir(parents=True, exist_ok=True)
        cls.ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
        if "sqlite" in cls.DATABASE_URL:
            db_clean_path = cls.DATABASE_URL.split("sqlite:///")[-1]
            if db_clean_path:
                Path(db_clean_path).resolve().parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_allowed_users(cls) -> Dict[int, str]:
        """Phân tích danh sách người dùng được phép từ biến môi trường dạng ID:ROLE"""
        users = {}
        if not cls.ALLOWED_USERS_RAW:
            return users
        
        for item in cls.ALLOWED_USERS_RAW.split(","):
            parts = item.strip().split(":")
            if len(parts) >= 1 and parts[0].isdigit():
                uid = int(parts[0])
                if uid in (123456789, 987654321):
                    continue
                users[uid] = parts[1].strip().lower() if len(parts) >= 2 else "creator"
        return users

    @classmethod
    def get_allowed_group_ids(cls) -> List[int]:
        """Lấy danh sách ID nhóm chat Telegram được phép gọi bot"""
        if not cls.ALLOWED_GROUP_IDS_RAW:
            return []
        groups = []
        for g in cls.ALLOWED_GROUP_IDS_RAW.split(","):
            g_clean = g.strip()
            if g_clean.lstrip("-").isdigit():
                groups.append(int(g_clean))
        return groups

    @classmethod
    def get_autopilot_enabled_channels(cls) -> List[str]:
        """Lấy danh sách ID các kênh được chỉ định bật Auto-Pilot (nếu để trống tức là áp dụng tất cả các kênh)"""
        raw = getattr(cls, "AUTOPILOT_ENABLED_CHANNELS", "").strip()
        if not raw:
            return []
        return [c.strip() for c in raw.split(",") if c.strip()]

    @classmethod
    def get_gemini_keys_pool(cls) -> List[str]:
        """Lấy danh sách các Gemini API Keys từ cụm tài khoản Google Ultra để xoay vòng"""
        keys = []
        if cls.GEMINI_API_KEYS_RAW:
            for k in cls.GEMINI_API_KEYS_RAW.split(","):
                k_clean = k.strip().strip('"').strip("'")
                if k_clean:
                    keys.append(k_clean)
        elif cls.GEMINI_API_KEY_SINGLE:
            keys.append(cls.GEMINI_API_KEY_SINGLE.strip())
        aiza_keys = [k for k in keys if k.startswith(("AIza", "AQ."))]
        return aiza_keys if aiza_keys else keys

    @classmethod
    def get_pexels_keys_pool(cls) -> List[str]:
        """Lấy danh sách các Pexels API Keys để xoay vòng"""
        keys = []
        if cls.PEXELS_API_KEYS_RAW:
            for k in cls.PEXELS_API_KEYS_RAW.split(","):
                k_clean = k.strip().strip('"').strip("'")
                if k_clean:
                    keys.append(k_clean)
        elif cls.PEXELS_API_KEY:
            keys.append(cls.PEXELS_API_KEY.strip())
        return keys

    _cached_channels: Optional[List[Dict]] = None
    _cached_accounts: Optional[List[Dict]] = None
    _channels_mtime: float = 0.0

    _CANONICAL_CHANNEL_DEFAULTS: Dict[str, Dict[str, str]] = {
        "ch_dark_psychology": {
            "name": "Shadow Mind",
            "niche": "dark_psychology_self_mastery",
            "language": "en",
            "preferred_voice": "en-US-ChristopherNeural",
        },
        "ch_deep_sea": {
            "name": "Abyssal Secrets",
            "niche": "deep_sea_earth_mysteries",
            "language": "en",
            "preferred_voice": "en-US-GuyNeural",
        },
        "ch_heart_rescue": {
            "name": "Paws of Hope",
            "niche": "heartwarming_animal_rescue",
            "language": "en",
            "preferred_voice": "en-US-AriaNeural",
        },
        "ch_wild_odyssey": {
            "name": "Wild Odyssey",
            "niche": "wildlife_predators",
            "language": "en",
            "preferred_voice": "en-US-EricNeural",
        },
        "ch_alternate_history": {
            "name": 'Alternate History & "What If?"',
            "niche": "alternate_history",
            "language": "en",
            "preferred_voice": "en-US-ChristopherNeural",
        },
        "@alternatehistorywhatif-e2x": {
            "name": 'Alternate History & "What If?"',
            "niche": "alternate_history",
            "language": "en",
            "preferred_voice": "en-US-ChristopherNeural",
        },
        "ch_odisseia_selvagem": {
            "name": "Odisseia Selvagem",
            "niche": "wildlife_predators",
            "language": "pt-BR",
            "preferred_voice": "pt-BR-AntonioNeural",
        },
        "ch_misterios_do_mundo": {
            "name": "Mistérios do Mundo",
            "niche": "deep_sea_earth_mysteries",
            "language": "pt-BR",
            "preferred_voice": "pt-BR-AntonioNeural",
        },
    }

    @classmethod
    def _heal_channel_dict(cls, ch: Dict) -> Dict:
        """Tự động phục hồi metadata chuẩn nếu kênh bị mất niche/language hoặc lỗi font tên kênh."""
        if not isinstance(ch, dict):
            return ch
        cid = str(ch.get("id") or "").strip()
        defaults = cls._CANONICAL_CHANNEL_DEFAULTS.get(cid)
        name_val = str(ch.get("name") or "")
        if ("\ufffd" in name_val or name_val.endswith('"What If?')) and defaults:
            ch["name"] = defaults["name"]
        yt_title_val = str(ch.get("youtube_title") or "")
        if yt_title_val.endswith('"What If?') and defaults:
            ch["youtube_title"] = defaults["name"]
        if defaults:
            if not str(ch.get("niche") or "").strip():
                ch["niche"] = defaults["niche"]
            if cid in ("ch_odisseia_selvagem", "ch_misterios_do_mundo"):
                if not str(ch.get("language") or "").lower().startswith("pt"):
                    ch["language"] = defaults["language"]
                if not str(ch.get("preferred_voice") or "").lower().startswith("pt-"):
                    ch["preferred_voice"] = defaults["preferred_voice"]
            else:
                if not str(ch.get("language") or "").strip():
                    ch["language"] = defaults["language"]
                if not str(ch.get("preferred_voice") or "").strip():
                    ch["preferred_voice"] = defaults["preferred_voice"]
        return ch

    @classmethod
    def get_channels_config(cls) -> List[Dict]:
        """Tải cấu hình các kênh YouTube từ file YAML (kết quả được lưu cache in-memory)"""
        if not cls.CHANNELS_CONFIG_FILE.exists():
            example_file = cls.CHANNELS_CONFIG_FILE.with_name("channels.example.yaml")
            if example_file.exists():
                try:
                    import shutil
                    shutil.copyfile(example_file, cls.CHANNELS_CONFIG_FILE)
                except Exception:
                    pass
            else:
                return []
        try:
            mtime = cls.CHANNELS_CONFIG_FILE.stat().st_mtime
            if cls._cached_channels is not None and mtime == cls._channels_mtime:
                return cls._cached_channels
            with open(cls.CHANNELS_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                raw_channels = data.get("channels", []) if data else []
                cls._cached_channels = [cls._heal_channel_dict(c) for c in raw_channels]
                raw_accounts = data.get("google_accounts", []) if data else []
                for acc in raw_accounts:
                    if isinstance(acc, dict) and isinstance(acc.get("brand_channels"), list):
                        acc["brand_channels"] = [cls._heal_channel_dict(bc) for bc in acc["brand_channels"]]
                cls._cached_accounts = raw_accounts
                cls._channels_mtime = mtime
                return cls._cached_channels
        except Exception:
            return cls._cached_channels or []

    @classmethod
    def get_accounts_config(cls) -> List[Dict]:
        """Tải cấu hình danh sách tài khoản Google từ channels.yaml (có cache in-memory)."""
        if not cls.CHANNELS_CONFIG_FILE.exists():
            example_file = cls.CHANNELS_CONFIG_FILE.with_name("channels.example.yaml")
            if example_file.exists():
                try:
                    import shutil
                    shutil.copyfile(example_file, cls.CHANNELS_CONFIG_FILE)
                except Exception:
                    pass
            else:
                return []
        try:
            mtime = cls.CHANNELS_CONFIG_FILE.stat().st_mtime
            if cls._cached_accounts is not None and mtime == cls._channels_mtime:
                return cls._cached_accounts
            cls.get_channels_config()
            return cls._cached_accounts or []
        except Exception:
            return cls._cached_accounts or []

    _CHANNEL_ID_ALIASES: Dict[str, str] = {
        "ch_dark_psychology": "ch_psychology_of_the_world",
        "channel_1": "ch_psychology_of_the_world",
        "channel_maya": "ch_psychology_of_the_world",
        "ch_psychology_of_the_world": "channel_maya",
        "channel_2": "ch_wild_odyssey",
        "ch_deep_sea": "ch_the_mysteries_of_the_world",
        "channel_3": "ch_the_mysteries_of_the_world",
        "ucsrjiil67qxidtxt70z-iwa": "ch_the_mysteries_of_the_world",
        "ch_the_mysteries_of_the_world": "ucsrjiil67qxidtxt70z-iwa",
        "channel_4": "ch_misterios_do_mundo",
        "ch_alternate_history": "@alternatehistorywhatif-e2x",
        "channel_5": "@alternatehistorywhatif-e2x",
        "ch_heart_rescue": "ch_paws_hearts_rescue",
        "channel_6": "ch_paws_hearts_rescue",
        "channel_7": "ch_odisseia_selvagem",
    }

    @classmethod
    def get_channel_by_id(cls, channel_id: str) -> Optional[Dict]:
        """Tìm và trả về dict cấu hình đầy đủ của kênh theo ID hoặc alias (từ danh sách channels hoặc brand_channels)."""
        raw_target = str(channel_id or "").strip()
        if not raw_target:
            return None
        targets = {raw_target, raw_target.lower(), cls._CHANNEL_ID_ALIASES.get(raw_target, raw_target), cls._CHANNEL_ID_ALIASES.get(raw_target.lower(), raw_target)}
        for k, v in cls._CHANNEL_ID_ALIASES.items():
            if k.lower() in targets or v.lower() in targets:
                targets.add(k)
                targets.add(v)
        targets_low = {t.lower() for t in targets if t}
        for ch in cls.get_channels_config() or []:
            if isinstance(ch, dict):
                ch_idents = {
                    str(ch.get("id") or "").strip().lower(),
                    str(ch.get("youtube_channel_id") or "").strip().lower(),
                    str(ch.get("handle") or "").strip().lower(),
                }
                if targets_low & ch_idents:
                    return dict(ch)
        for acc in cls.get_accounts_config() or []:
            if not isinstance(acc, dict):
                continue
            for bc in acc.get("brand_channels") or []:
                if isinstance(bc, dict):
                    bc_idents = {
                        str(bc.get("id") or "").strip().lower(),
                        str(bc.get("youtube_channel_id") or "").strip().lower(),
                        str(bc.get("handle") or "").strip().lower(),
                    }
                    if targets_low & bc_idents:
                        res = dict(bc)
                        if not res.get("google_account") and acc.get("id"):
                            res["google_account"] = acc.get("id")
                        return res
        return None

settings = Settings()
settings.ensure_storage_dirs()
