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

    jobs = relationship("VideoJob", back_populates="creator")

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
    
    # Đường dẫn file
    video_path = Column(String(500), nullable=True)
    audio_path = Column(String(500), nullable=True)
    
    # YouTube info
    youtube_video_id = Column(String(100), nullable=True)
    scheduled_time = Column(DateTime, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator = relationship("User", back_populates="jobs")

def init_db():
    Base.metadata.create_all(bind=engine)
