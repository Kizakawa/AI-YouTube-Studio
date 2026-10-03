import logging
from pathlib import Path
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Text, DateTime, ForeignKey, event, text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from contextlib import contextmanager
from config.settings import settings

logger = logging.getLogger(__name__)

def normalize_database_url(url: str) -> str:
    """Chuẩn hóa DATABASE_URL: chuyển postgres:// thành postgresql:// cho SQLAlchemy 2.0"""
    if not url:
        return f"sqlite:///{settings.BASE_DIR}/storage/ai_youtube.db"
    clean_url = url.strip()
    if clean_url.startswith("postgres://"):
        clean_url = "postgresql://" + clean_url[len("postgres://"):]
    return clean_url

def create_configured_engine(raw_url: str):
    """
    Khởi tạo SQLAlchemy engine thông minh:
    - Nếu là PostgreSQL: Cấu hình connection pool (pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=300).
    - Nếu là SQLite: Đảm bảo thư mục tồn tại, cấu hình check_same_thread=False, kích hoạt WAL mode.
    - Fallback: Nếu kết nối PostgreSQL thất bại, tự động chuyển về SQLite cục bộ an toàn.
    """
    db_url = normalize_database_url(raw_url)
    is_postgres = db_url.startswith("postgresql://") or db_url.startswith("postgresql+psycopg2://")

    if is_postgres:
        driver_url = db_url
        if driver_url.startswith("postgresql://"):
            driver_url = "postgresql+psycopg2://" + driver_url[len("postgresql://"):]
        try:
            eng = create_engine(
                driver_url,
                echo=False,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=300,
            )
            # Test ping connection
            with eng.connect() as test_conn:
                test_conn.execute(text("SELECT 1"))
            logger.info("✅ [Database] Đã kết nối thành công tới PostgreSQL Cloud!")
            return eng, db_url
        except Exception as ex:
            logger.warning(
                f"⚠️ [Database] Không thể kết nối PostgreSQL ({ex}). "
                f"Tự động chuyển về SQLite cục bộ (storage/ai_youtube.db) an toàn..."
            )
            db_url = f"sqlite:///{settings.BASE_DIR}/storage/ai_youtube.db"

    # SQLite Configuration
    db_clean_path = db_url.split("sqlite:///")[-1]
    if db_clean_path:
        Path(db_clean_path).resolve().parent.mkdir(parents=True, exist_ok=True)

    eng = create_engine(
        db_url,
        echo=False,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()
        except Exception:
            pass

    return eng, db_url

Base = declarative_base()
engine, ACTIVE_DATABASE_URL = create_configured_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)


# ──────────────────────────────────────────────
# FIX CRITICAL: Context manager chống session leak
# ──────────────────────────────────────────────
@contextmanager
def get_db():
    """
    Dùng: with get_db() as db:
    Tự động commit nếu thành công, rollback + close nếu exception.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class User(Base):
    __tablename__ = "users"

    telegram_id = Column(BigInteger, primary_key=True)
    username = Column(String(100), nullable=True)
    full_name = Column(String(150), nullable=True)
    role = Column(String(50), default="creator") # 'admin' hoặc 'creator'
    active_channel_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    jobs = relationship("VideoJob", back_populates="creator")
    activities = relationship("ActivityLog", back_populates="user")


class VideoJob(Base):
    __tablename__ = "video_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id"))
    target_channel_id = Column(String(100), nullable=False)

    # Nội dung
    topic = Column(String(255), nullable=False)
    title = Column(String(255), nullable=True)
    script = Column(Text, nullable=True)          # full_narration text (backward compat)
    description = Column(Text, nullable=True)
    tags = Column(String(500), nullable=True)

    # ──────────────────────────────────────────
    # [NEW] Structured data — Giải quyết đứt gãy contract
    # ──────────────────────────────────────────
    scenes_json = Column(Text, nullable=True)      # ScriptContract.to_json_str()
    audio_duration_ms = Column(Integer, nullable=True)
    render_manifest = Column(Text, nullable=True)  # RenderJobContract JSON snapshot
    quality_scores = Column(Text, nullable=True)   # JSON lưu kết quả chấm điểm từ 4 Graders (LangGraph)

    # Trạng thái — Mở rộng với thêm trạng thái trung gian
    # DRAFT -> SCRIPT_READY -> AUDIO_READY -> MEDIA_READY -> RENDERING -> RENDERED -> SCHEDULED -> PUBLISHED -> FAILED
    status = Column(String(50), default="DRAFT")
    progress_percent = Column(Integer, default=0)         # [NEW] Phần trăm tiến độ 0-100%
    progress_message = Column(String(255), nullable=True) # [NEW] Chi tiết bước đang thực hiện
    error_message = Column(Text, nullable=True)    # [NEW] Lưu traceback khi FAILED

    video_path = Column(String(500), nullable=True)
    audio_path = Column(String(500), nullable=True)
    subtitle_path = Column(String(500), nullable=True)  # [NEW]
    thumbnail_path = Column(String(500), nullable=True) # [NEW] Thumbnail ngang 16:9
    thumbnail_vertical_path = Column(String(500), nullable=True) # [NEW] Thumbnail dọc 9:16
    drive_link = Column(String(500), nullable=True)

    # YouTube & Auto-Publish info
    youtube_video_id = Column(String(100), nullable=True)
    scheduled_time = Column(DateTime, nullable=True)
    is_auto_generated = Column(Integer, default=0)        # 1 = Hệ thống tự động sinh khi trễ KPI
    approval_deadline = Column(DateTime, nullable=True)   # Hạn chót duyệt video trước khi tự động đăng tạm

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator = relationship("User", back_populates="jobs")


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=True)
    username = Column(String(100), nullable=True)
    action_type = Column(String(100), nullable=False) # 'CMD_IDEA', 'CHANGE_CHANNEL', 'APPROVE_VIDEO', 'PUBLISH_VIDEO'
    details = Column(Text, nullable=False)
    channel_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="activities")


class QuotaTracker(Base):
    __tablename__ = "quota_trackers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String(100), nullable=False, index=True)
    cycle_date = Column(String(20), nullable=False, index=True)  # Ngày chu kỳ PT (reset lúc 14:00 VN)
    used_units = Column(Integer, default=0)
    upload_count = Column(Integer, default=0)
    max_daily_quota = Column(Integer, default=10000)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)



def log_activity(db, telegram_id: int, username: str, action_type: str, details: str, channel_id: str = None):
    """Hàm tiện ích ghi lại vết hành động của thành viên để theo dõi trên Web Dashboard.
    LƯU Ý: Không gọi db.commit() ở đây — get_db() context manager đã auto-commit.
    """
    try:
        valid_user_id = telegram_id
        if valid_user_id:
            # Kiểm tra xem telegram_id có tồn tại trong bảng users không để tránh Foreign Key Violation trên PostgreSQL
            user_exists = db.query(User.telegram_id).filter(User.telegram_id == valid_user_id).first()
            if not user_exists:
                valid_user_id = None

        activity = ActivityLog(
            telegram_id=valid_user_id,
            username=username or f"User_{telegram_id}",
            action_type=action_type,
            details=details,
            channel_id=channel_id,
            created_at=datetime.utcnow()
        )
        db.add(activity)
        # Không commit ở đây — get_db() context manager sẽ auto-commit
        # Tránh double commit gây SQLite busy khi multi-thread
    except Exception as e:
        logging.getLogger(__name__).warning(f"Lỗi ghi activity log: {e}")


def init_db():
    Base.metadata.create_all(bind=engine)
    
    # Tự động bổ sung các cột mới cho SQLite nếu bảng đã tồn tại từ phiên bản trước
    if engine.dialect.name == "sqlite":
        with engine.connect() as conn:
            try:
                result = conn.execute(text("PRAGMA table_info(video_jobs)")).fetchall()
                existing_columns = {row[1] for row in result}
                
                columns_to_add = {
                    "description": "TEXT",
                    "scenes_json": "TEXT",
                    "audio_duration_ms": "INTEGER",
                    "render_manifest": "TEXT",
                    "error_message": "TEXT",
                    "subtitle_path": "VARCHAR(255)",
                    "updated_at": "DATETIME",
                    "progress_percent": "INTEGER DEFAULT 0",
                    "progress_message": "VARCHAR(255)",
                    "quality_scores": "TEXT",
                    "is_auto_generated": "INTEGER DEFAULT 0",
                    "approval_deadline": "DATETIME",
                    "thumbnail_path": "VARCHAR(500)",
                    "thumbnail_vertical_path": "VARCHAR(500)",
                }
                for col_name, col_type in columns_to_add.items():
                    if col_name not in existing_columns:
                        conn.execute(text(f"ALTER TABLE video_jobs ADD COLUMN {col_name} {col_type}"))
                conn.commit()
            except Exception as migration_err:
                logger.error(
                    f"❌ [DB Migration] Lỗi khi tự động nâng cấp schema SQLite: {migration_err}. "
                    f"Kiểm tra quyền truy cập file CSDL và dung lượng ổ cứng.",
                    exc_info=True
                )
    elif engine.dialect.name == "postgresql":
        with engine.connect() as conn:
            try:
                result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='video_jobs'")).fetchall()
                existing_columns = {row[0] for row in result}
                columns_to_add = {
                    "description": "TEXT",
                    "scenes_json": "TEXT",
                    "audio_duration_ms": "INTEGER",
                    "render_manifest": "TEXT",
                    "error_message": "TEXT",
                    "subtitle_path": "VARCHAR(255)",
                    "updated_at": "TIMESTAMP",
                    "progress_percent": "INTEGER DEFAULT 0",
                    "progress_message": "VARCHAR(255)",
                    "quality_scores": "TEXT",
                    "is_auto_generated": "INTEGER DEFAULT 0",
                    "approval_deadline": "TIMESTAMP",
                    "thumbnail_path": "VARCHAR(500)",
                    "thumbnail_vertical_path": "VARCHAR(500)",
                }
                for col_name, col_type in columns_to_add.items():
                    if col_name not in existing_columns:
                        conn.execute(text(f"ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS {col_name} {col_type}"))
                conn.commit()
            except Exception as migration_err:
                logger.error(
                    f"❌ [DB Migration] Lỗi khi tự động nâng cấp schema PostgreSQL: {migration_err}",
                    exc_info=True
                )

