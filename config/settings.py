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
    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    ALLOWED_USERS_RAW: str = os.getenv("ALLOWED_TELEGRAM_USERS", "")
    
    # AI Services
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    PEXELS_API_KEY: str = os.getenv("PEXELS_API_KEY", "")
    
    # Defaults
    DEFAULT_TARGET_LANGUAGE: str = os.getenv("DEFAULT_TARGET_LANGUAGE", "en")
    DEFAULT_TTS_VOICE: str = os.getenv("DEFAULT_TTS_VOICE", "en-US-ChristopherNeural")
    
    # Storage Paths
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR}/storage/ai_youtube.db")
    TEMP_DIR: Path = BASE_DIR / "storage" / "temp"
    OUTPUT_DIR: Path = BASE_DIR / "storage" / "outputs"
    CREDENTIALS_DIR: Path = BASE_DIR / "credentials"
    CHANNELS_CONFIG_FILE: Path = BASE_DIR / "config" / "channels.yaml"

    @classmethod
    def get_allowed_users(cls) -> Dict[int, str]:
        """Phân tích danh sách người dùng được phép từ biến môi trường dạng ID:ROLE"""
        users = {}
        if not cls.ALLOWED_USERS_RAW:
            return users
        
        for item in cls.ALLOWED_USERS_RAW.split(","):
            parts = item.strip().split(":")
            if len(parts) == 2 and parts[0].isdigit():
                users[int(parts[0])] = parts[1].strip().lower()
            elif len(parts) == 1 and parts[0].isdigit():
                users[int(parts[0])] = "creator"
        return users

    @classmethod
    def get_channels_config(cls) -> List[Dict]:
        """Tải cấu hình các kênh YouTube từ file YAML"""
        if not cls.CHANNELS_CONFIG_FILE.exists():
            return []
        with open(cls.CHANNELS_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data.get("channels", [])

settings = Settings()
