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

    # Colab GPU Render Farm (Tăng tốc NVENC GPU 20s/video & WhisperX)
    COLAB_WORKER_URL: str = os.getenv("COLAB_WORKER_URL", "")
    RENDER_BACKEND: str = os.getenv("RENDER_BACKEND", "auto")  # "auto", "colab", "local"
    COLAB_TIMEOUT_MINUTES: int = int(os.getenv("COLAB_TIMEOUT_MINUTES", "60"))
    
    # Defaults
    DEFAULT_TARGET_LANGUAGE: str = os.getenv("DEFAULT_TARGET_LANGUAGE", "en")
    DEFAULT_TTS_VOICE: str = os.getenv("DEFAULT_TTS_VOICE", "en-US-AndrewNeural")
    USE_LANGGRAPH: bool = os.getenv("USE_LANGGRAPH", "true").lower() in ("true", "1", "yes")
    ENABLE_DEVICE_GATEKEEPER: bool = os.getenv("ENABLE_DEVICE_GATEKEEPER", "true").lower() in ("true", "1", "yes")
    AGENT_API_KEY: str = os.getenv("AGENT_API_KEY", "agy_agent_supersecret_key_2026")
    
    # Tự động hóa sản xuất & đăng bài (Mặc định FALSE để tránh bot tự ý sinh video linh tinh)
    ENABLE_OVERDUE_AUTO_PRODUCTION: bool = os.getenv("ENABLE_OVERDUE_AUTO_PRODUCTION", "false").lower() in ("true", "1", "yes")
    ENABLE_FALLBACK_AUTO_PUBLISH: bool = os.getenv("ENABLE_FALLBACK_AUTO_PUBLISH", "false").lower() in ("true", "1", "yes")
    
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
    def set_colab_url(cls, url: str) -> bool:
        """Cập nhật Colab Worker URL trong runtime và tự động lưu vào file .env"""
        import re
        clean_url = url.strip().rstrip("/")
        cls.COLAB_WORKER_URL = clean_url
        os.environ["COLAB_WORKER_URL"] = clean_url
        env_file = cls.BASE_DIR / ".env"
        try:
            content = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
            if "COLAB_WORKER_URL=" in content:
                new_content = re.sub(r'COLAB_WORKER_URL=.*', f'COLAB_WORKER_URL="{clean_url}"', content)
            else:
                new_content = content.rstrip() + f'\nCOLAB_WORKER_URL="{clean_url}"\n'
            env_file.write_text(new_content, encoding="utf-8")
            return True
        except Exception as e:
            return False

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
    def get_gemini_keys_pool(cls) -> List[str]:
        """Lấy danh sách các Gemini API Keys từ cụm tài khoản Google Ultra để xoay vòng"""
        keys = []
        if cls.GEMINI_API_KEYS_RAW:
            for k in cls.GEMINI_API_KEYS_RAW.split(","):
                k_clean = k.strip()
                if k_clean:
                    keys.append(k_clean)
        elif cls.GEMINI_API_KEY_SINGLE:
            keys.append(cls.GEMINI_API_KEY_SINGLE.strip())
        return keys

    @classmethod
    def get_pexels_keys_pool(cls) -> List[str]:
        """Lấy danh sách các Pexels API Keys để xoay vòng"""
        keys = []
        if cls.PEXELS_API_KEYS_RAW:
            for k in cls.PEXELS_API_KEYS_RAW.split(","):
                k_clean = k.strip()
                if k_clean:
                    keys.append(k_clean)
        elif cls.PEXELS_API_KEY:
            keys.append(cls.PEXELS_API_KEY.strip())
        return keys

    _cached_channels: Optional[List[Dict]] = None
    _cached_accounts: Optional[List[Dict]] = None
    _channels_mtime: float = 0.0

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
                cls._cached_channels = data.get("channels", []) if data else []
                cls._cached_accounts = data.get("google_accounts", []) if data else []
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
            with open(cls.CHANNELS_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                cls._cached_channels = data.get("channels", []) if data else []
                cls._cached_accounts = data.get("google_accounts", []) if data else []
                cls._channels_mtime = mtime
                return cls._cached_accounts
        except Exception:
            return cls._cached_accounts or []

settings = Settings()
settings.ensure_storage_dirs()
