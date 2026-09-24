from pathlib import Path
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, event
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from contextlib import contextmanager
from config.settings import settings

# Đảm bảo thư mục cha của SQLite database luôn tồn tại trước khi khởi tạo engine!
if "sqlite" in settings.DATABASE_URL:
    db_clean_path = settings.DATABASE_URL.split("sqlite:///")[-1]
    if db_clean_path:
        Path(db_clean_path).resolve().parent.mkdir(parents=True, exist_ok=True)

Base = declarative_base()
engine = create_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},  # Bắt buộc cho SQLite + multi-thread
)

# ──────────────────────────────────────────────
# FIX CRITICAL: Bật WAL mode + busy_timeout
# ──────────────────────────────────────────────
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")   # chờ 5s thay vì crash ngay
    cursor.execute("PRAGMA synchronous=NORMAL")  # an toàn + nhanh hơn FULL
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


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

    telegram_id = Column(Integer, primary_key=True)
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
    user_id = Column(Integer, ForeignKey("users.telegram_id"))
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

    # Đường dẫn file & lưu trữ
    video_path = Column(String(500), nullable=True)
    audio_path = Column(String(500), nullable=True)
    subtitle_path = Column(String(500), nullable=True)  # [NEW]
    drive_link = Column(String(500), nullable=True)

    # YouTube info
    youtube_video_id = Column(String(100), nullable=True)
    scheduled_time = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator = relationship("User", back_populates="jobs")


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(Integer, ForeignKey("users.telegram_id"), nullable=True)
    username = Column(String(100), nullable=True)
    action_type = Column(String(100), nullable=False) # 'CMD_IDEA', 'CHANGE_CHANNEL', 'APPROVE_VIDEO', 'PUBLISH_VIDEO'
    details = Column(Text, nullable=False)
    channel_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="activities")


def log_activity(db, telegram_id: int, username: str, action_type: str, details: str, channel_id: str = None):
    """Hàm tiện ích ghi lại vết hành động của thành viên để theo dõi trên Web Dashboard.
    LƯU Ý: Không gọi db.commit() ở đây — get_db() context manager đã auto-commit.
    """
    try:
        activity = ActivityLog(
            telegram_id=telegram_id,
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
    with engine.connect() as conn:
        try:
            from sqlalchemy import text
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
            }
            for col_name, col_type in columns_to_add.items():
                if col_name not in existing_columns:
                    conn.execute(text(f"ALTER TABLE video_jobs ADD COLUMN {col_name} {col_type}"))
            conn.commit()
        except Exception:
            pass
