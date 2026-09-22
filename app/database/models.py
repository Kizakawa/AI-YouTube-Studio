from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from config.settings import settings

Base = declarative_base()
engine = create_engine(settings.DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

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
    script = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    tags = Column(String(500), nullable=True)
    
    # Trạng thái quy trình: DRAFT -> SCRIPT_READY -> RENDERING -> RENDERED -> SCHEDULED -> PUBLISHED -> FAILED
    status = Column(String(50), default="DRAFT")
    
    # Đường dẫn file & lưu trữ
    video_path = Column(String(500), nullable=True)
    audio_path = Column(String(500), nullable=True)
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
    """Hàm tiện ích ghi lại vết hành động của thành viên để theo dõi trên Web Dashboard"""
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
        db.commit()
    except Exception as e:
        db.rollback()

def init_db():
    Base.metadata.create_all(bind=engine)
