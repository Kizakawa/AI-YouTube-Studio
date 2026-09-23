"""
Data Contracts — Nguồn sự thật duy nhất cho toàn bộ data flow giữa các Agents.
Mọi agent PHẢI nhận/trả các model này, KHÔNG dùng Dict[str, Any] thô.

Chiến lược Scene Timing: "Audio-First"
  - VoiceAgent sinh MP3 per-scene → ffprobe đo duration thực → ghi vào audio_duration_ms
  - estimated_seconds từ Gemini chỉ là tham khảo ban đầu
  - FFmpeg dùng actual_duration_seconds (property) để quyết định timeline render
"""
import json
from enum import Enum
from typing import List, Optional
from pathlib import Path
from pydantic import BaseModel, Field, field_validator


class JobStatus(str, Enum):
    """Máy trạng thái (State Machine) của 1 VideoJob xuyên suốt pipeline."""
    DRAFT          = "DRAFT"
    SCRIPT_READY   = "SCRIPT_READY"
    AUDIO_READY    = "AUDIO_READY"
    MEDIA_READY    = "MEDIA_READY"
    RENDERING      = "RENDERING"
    RENDERED       = "RENDERED"
    UPLOADING      = "UPLOADING"
    SCHEDULED      = "SCHEDULED"
    PUBLISHED      = "PUBLISHED"
    FAILED         = "FAILED"


class SceneContract(BaseModel):
    """
    Đại diện cho 1 phân cảnh trong kịch bản.

    Luồng data enrichment:
      ContentAgent tạo → VoiceAgent bổ sung audio_* → MediaAgent bổ sung media_*
    """
    scene_id: int = Field(ge=1)
    narration: str = Field(min_length=1, description="Lời thoại của cảnh này")
    visual_keyword: str = Field(min_length=1, description="Từ khóa tìm media trên Pexels/Imagen")
    estimated_seconds: float = Field(gt=0, le=20, description="Ước lượng thô từ Gemini (tham khảo)")

    # --- Populated bởi VoiceAgent (Step B) ---
    audio_path: Optional[str] = Field(default=None, description="Đường dẫn file MP3 của riêng scene này")
    audio_duration_ms: Optional[int] = Field(default=None, ge=0, description="Thời lượng thực tế (ms) sau TTS")

    # --- Populated bởi MediaAgent/ImageAgent (Step C) ---
    media_path: Optional[str] = Field(default=None, description="Đường dẫn file ảnh/video nền cho scene")
    media_type: Optional[str] = Field(default=None, pattern=r"^(image|video)$")

    @field_validator("narration")
    @classmethod
    def narration_must_not_be_stage_direction(cls, v: str) -> str:
        """Bảo vệ: Gemini đôi khi trả stage direction thay vì lời thoại."""
        if v.strip().startswith("[") and v.strip().endswith("]"):
            raise ValueError(f"narration là stage direction, không phải lời thoại: {v}")
        return v.strip()

    @property
    def actual_duration_seconds(self) -> float:
        """Thời lượng THỰC TẾ dựa trên audio TTS. Fallback = estimated_seconds."""
        if self.audio_duration_ms is not None:
            return self.audio_duration_ms / 1000.0
        return self.estimated_seconds


class ScriptContract(BaseModel):
    """
    Output chuẩn của ContentAgent — kịch bản đã validate.
    Là nguồn sự thật cho title, hook, narration, SEO metadata.
    """
    title: str = Field(min_length=5, max_length=100)
    hook: str = Field(min_length=5, description="Câu hook 3 giây đầu")
    full_narration: str = Field(min_length=20, description="Toàn bộ lời thoại nối liền")
    scenes: List[SceneContract] = Field(min_length=1, max_length=15)
    tags: List[str] = Field(min_length=1, max_length=30)
    description: str = Field(min_length=10)

    @field_validator("scenes")
    @classmethod
    def scenes_must_have_sequential_ids(cls, v: List[SceneContract]) -> List[SceneContract]:
        """Đảm bảo scene_id liên tục 1, 2, 3, ..."""
        for i, scene in enumerate(v):
            if scene.scene_id != i + 1:
                raise ValueError(f"scene_id không liên tục: expected {i+1}, got {scene.scene_id}")
        return v

    @property
    def total_estimated_duration(self) -> float:
        return sum(s.estimated_seconds for s in self.scenes)

    def to_json_str(self) -> str:
        """Serialize để lưu vào cột scenes_json của VideoJob."""
        return self.model_dump_json()

    @classmethod
    def from_json_str(cls, json_str: str) -> "ScriptContract":
        """Deserialize từ cột scenes_json của VideoJob."""
        return cls.model_validate_json(json_str)


class AudioSegment(BaseModel):
    """Metadata của 1 file audio sau khi VoiceAgent tạo xong."""
    scene_id: int
    file_path: str
    duration_ms: int = Field(ge=0)
    voice_name: str


class RenderJobContract(BaseModel):
    """
    Input cho FFmpeg render pipeline.
    Chỉ được tạo SAU KHI tất cả scenes đã có audio_path và media_path.
    """
    job_id: int
    title: str
    scenes: List[SceneContract]

    # Paths
    full_audio_path: str = Field(description="MP3 concat toàn bộ narration")
    total_audio_duration_ms: int = Field(ge=0)
    subtitle_path: Optional[str] = Field(default=None, description="SRT file path")
    bgm_path: Optional[str] = Field(default=None, description="Background music MP3")
    output_path: str = Field(description="Đường dẫn MP4 đầu ra")

    # Render settings
    resolution_w: int = Field(default=1080)
    resolution_h: int = Field(default=1920)
    fps: int = Field(default=30)
    bgm_volume: float = Field(default=0.08, ge=0, le=1.0, description="Volume nhạc nền (0-1)")

    @field_validator("scenes")
    @classmethod
    def all_scenes_must_have_media(cls, v: List[SceneContract]) -> List[SceneContract]:
        """Gate-check: Không cho render nếu còn scene thiếu media."""
        for scene in v:
            if not scene.audio_path:
                raise ValueError(f"Scene {scene.scene_id} chưa có audio_path")
            if not scene.media_path:
                raise ValueError(f"Scene {scene.scene_id} chưa có media_path")
            if scene.audio_duration_ms is None:
                raise ValueError(f"Scene {scene.scene_id} chưa có audio_duration_ms")
        return v

    @property
    def resolution(self) -> str:
        return f"{self.resolution_w}x{self.resolution_h}"
