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
from pydantic import BaseModel, Field, field_validator, model_validator


class JobStatus(str, Enum):
    """Máy trạng thái (State Machine) của 1 VideoJob xuyên suốt pipeline."""
    DRAFT             = "DRAFT"
    SCRIPT_READY      = "SCRIPT_READY"
    AUDIO_READY       = "AUDIO_READY"
    MEDIA_READY       = "MEDIA_READY"
    RENDERING         = "RENDERING"
    RENDERED          = "RENDERED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"  # Video tự động tạo khi trễ KPI, chờ người dùng duyệt hoặc đăng tạm
    UPLOADING         = "UPLOADING"
    SCHEDULED         = "SCHEDULED"
    PUBLISHED         = "PUBLISHED"
    FAILED            = "FAILED"


class SceneContract(BaseModel):
    """
    Đại diện cho 1 phân cảnh trong kịch bản.

    Luồng data enrichment:
      ContentAgent tạo → VoiceAgent bổ sung audio_* → MediaAgent bổ sung media_*
    """
    scene_id: int = Field(ge=1)
    narration: str = Field(min_length=1, description="Lời thoại của cảnh này")
    visual_keyword: str = Field(min_length=1, description="Từ khóa tìm kiếm media trên Pexels/Wikimedia (ngắn gọn)")
    visual_prompt: Optional[str] = Field(
        default=None,
        description=(
            "Mô tả cảnh quay điện ảnh CHI TIẾT độc lập với lời thoại. "
            "Phải là hiện tượng khoa học/vũ trụ/lịch sử THỰC TẠ — "
            "không bao giờ mô tả nghĩa đen của phép ẩn dụ trong lời thoại. "
            "Ví dụ: '[Vật thể chính], [Góc máy & Chuyển động], [Ánh sáng điện ảnh]'"
        )
    )
    stock_query: Optional[str] = Field(
        default=None,
        description=(
            "Query TỐI ĐA 2-3 TỪ TIẾNG ANH thuần túy gửi sang Pexels/Pixabay. "
            "CHỈ là danh từ chủ đề — TUYỆT ĐỐI KHÔNG chứa tên đạo diễn, "
            "tên phim, tên diễn viên, từ tính từ phong cách (như 'Nolan', 'Interstellar', 'cinematic'). "
            "Ví dụ đúng: 'black hole', 'galaxy nebula', 'neutron star'. "
            "Ví dụ sai: 'Interstellar voyage cinematic', 'Nolan dark atmosphere'."
        )
    )
    estimated_seconds: float = Field(default=3.0, gt=0, le=300, description="Ước lượng thô từ Gemini hoặc thời lượng clip (tham khảo)")

    # --- Populated bởi VoiceAgent (Step B) ---
    audio_path: Optional[str] = Field(default=None, description="Đường dẫn file MP3 của riêng scene này")
    audio_duration_ms: Optional[int] = Field(default=None, ge=0, description="Thời lượng thực tế (ms) sau TTS")
    start_offset_ms: Optional[int] = Field(default=None, description="Offset bắt đầu cố định (ms) trong timeline video nếu có")

    # --- Populated bởi MediaAgent/ImageAgent (Step C) ---
    media_path: Optional[str] = Field(default=None, description="Đường dẫn file ảnh/video nền cho scene")
    media_type: Optional[str] = Field(default=None, pattern=r"^(image|video|ai_image|ai_video|i2v|t2v|photo|documentary|auto)$")

    # ── Cinematic Enhancement Fields (Optional — sinh bởi ContentAgent hoặc để mặc định) ──
    camera_motion: Optional[str] = Field(
        default=None,
        description="Loại chuyển động camera Ken Burns: 'zoom_in', 'zoom_out', 'pan_left', 'pan_right', 'pan_up', 'pan_down'. None = tự động xoay vòng.",
        pattern=r"^(zoom_in|zoom_out|pan_left|pan_right|pan_up|pan_down)$"
    )
    sfx_cue: Optional[str] = Field(
        default=None,
        description="Tên file SFX (không có extension) trong assets/sfx/. Ví dụ: 'whoosh', 'hit', 'transition_swoosh'. None = không có SFX."
    )
    cinematic_prompt: Optional[str] = Field(
        default=None,
        description="Prompt chi tiết cho AI image generation theo phong cách dark cinematic (dùng thay visual_keyword khi cần ảnh AI). Ví dụ: 'dramatic dark cinematic portrait Vladimir Putin 1990s cold war era, volumetric fog, film grain'."
    )
    visual_strategy: Optional[str] = Field(
        default=None,
        description="Chiến lược do Visual Director phê duyệt: REAL_DOCUMENTARY, STOCK_VIDEO, AI_VIDEO, AI_IMAGE."
    )
    production_method: Optional[str] = Field(
        default=None,
        description="Phương pháp sản xuất: REAL_ARCHIVE, IMAGE_TO_VIDEO, TEXT_TO_VIDEO, STOCK_FOOTAGE."
    )
    tool: Optional[str] = Field(
        default=None,
        description="Công cụ được chọn: wikimedia_archive, imagen3_i2v, wan_t2v, pexels_video_api."
    )
    strategy_rationale: Optional[str] = Field(
        default=None,
        description="Lý do Visual Director chọn chiến lược và phương pháp thị giác này."
    )

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

    @property
    def resolved_camera_motion(self) -> str:
        """
        Trả về camera_motion thực tế.
        Nếu không set, tự động chọn theo scene_id (xoay vòng 6 loại để đa dạng).
        """
        if self.camera_motion:
            return self.camera_motion
        motions = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"]
        return motions[(self.scene_id - 1) % len(motions)]



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
    description: Optional[str] = Field(default=None, description="Mô tả video chuẩn SEO với hashtag")
    primary_entity: Optional[str] = Field(default=None, description="Tên thực thể/nhân vật chính nếu có")
    pinned_comment: Optional[str] = Field(
        default=None,
        description="Bình luận mồi ghim đầu trang (Bait comment) để kích thích tranh luận tăng tương tác và thời lượng xem"
    )
    viral_formula: Optional[str] = Field(
        default=None,
        description="Công thức viral áp dụng (Double Tap & Protect, Debate in Comments, Curiosity Part 2)"
    )

    @model_validator(mode="after")
    def ensure_description_and_defaults(self) -> "ScriptContract":
        """Đảm bảo description luôn tồn tại, không bao giờ None hoặc rỗng."""
        if not self.description or not str(self.description).strip():
            tag_str = " ".join(f"#{t.strip().replace(' ', '')}" for t in self.tags if t.strip()) if self.tags else "#shorts #viral"
            self.description = f"{self.title}\n\n{tag_str}"
        else:
            self.description = self.description.strip()
        return self

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

    def to_dict(self) -> dict:
        """Chuyển đổi thành dictionary (Pydantic model_dump)."""
        return self.model_dump()

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
    subtitle_path: Optional[str] = Field(default=None, description="ASS/SRT subtitle file path")
    bgm_path: Optional[str] = Field(default=None, description="Background music MP3")
    sfx_dir: Optional[str] = Field(default=None, description="Thư mục chứa file SFX (whoosh, hit...). None = bỏ qua SFX.")
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
