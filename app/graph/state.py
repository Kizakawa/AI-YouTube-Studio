"""
app.graph.state — Định nghĩa cấu trúc State cho LangGraph Video Workflow.

Bao gồm:
- VideoWorkflowState: Trạng thái tổng thể xuyên suốt vòng đời sản xuất video.
- ScriptEvaluationReport: Báo cáo chấm điểm kịch bản của Script Critic.
- VisualEvaluationReport: Báo cáo thẩm định chất lượng & độ chuẩn xác hình ảnh.
- AudioEvaluationReport: Báo cáo kiểm định giọng đọc & tốc độ WPM.
- VideoQAReport: Báo cáo kiểm định kỹ thuật video thành phẩm.
- EditRequest: Yêu cầu chỉnh sửa vi mô cho từng phân cảnh / thuộc tính video.
"""
from typing import TypedDict, List, Dict, Optional, Any
from pydantic import BaseModel, Field


class ScriptCriteriaScore(BaseModel):
    score: float = Field(ge=1.0, le=10.0, description="Điểm đánh giá từ 1 đến 10")
    feedback: str = Field(description="Nhận xét chi tiết và điểm cần cải thiện")


class ScriptEvaluationReport(BaseModel):
    hook_strength: ScriptCriteriaScore = Field(description="Sức hút giữ chân 3s đầu tiên")
    story_pacing: ScriptCriteriaScore = Field(description="Nhịp điệu kể chuyện & phân bổ từ ngữ")
    entity_coherence: ScriptCriteriaScore = Field(description="Độ gắn kết với nhân vật & bối cảnh lịch sử")
    visual_direction: ScriptCriteriaScore = Field(description="Tính khả thi & độ sắc nét của từ khóa hình ảnh")
    viral_loop: ScriptCriteriaScore = Field(description="Khả năng tạo vòng lặp vô tận (Shorts loop)")
    overall_score: float = Field(description="Điểm trung bình có trọng số (1-10)")
    passed: bool = Field(description="Đạt chuẩn chất lượng (>= 8.0/10)")
    actionable_critiques: List[str] = Field(default_factory=list, description="Hướng dẫn cụ thể để viết lại nếu chưa đạt")


class SceneVisualScore(BaseModel):
    scene_id: int
    media_path: str
    relevance_score: float = Field(ge=1.0, le=10.0, description="Độ liên quan với lời thoại")
    entity_match_score: float = Field(ge=1.0, le=10.0, description="Độ khớp với nhân vật chính (10 nếu chuẩn nhân vật)")
    aesthetic_score: float = Field(ge=1.0, le=10.0, description="Chất lượng độ nét, độ tương phản")
    is_valid_bitmap: bool = True
    issues: List[str] = Field(default_factory=list)


class VisualEvaluationReport(BaseModel):
    scene_scores: List[SceneVisualScore] = Field(default_factory=list)
    overall_score: float = 10.0
    failed_scene_ids: List[int] = Field(default_factory=list)
    passed: bool = True
    summary: str = ""


class SceneContentScore(BaseModel):
    """Đánh giá NỘI DUNG hình ảnh bằng AI Vision cho từng cảnh."""
    scene_id: int
    media_path: str
    relevance_score: float = Field(ge=1.0, le=10.0, description="Ảnh có khớp với lời thoại không?")
    entity_accuracy_score: float = Field(ge=1.0, le=10.0, description="Nhân vật/sự vật có đúng không?")
    historical_fidelity_score: float = Field(ge=1.0, le=10.0, description="Bối cảnh, trang phục có phù hợp thời đại?")
    visual_quality_score: float = Field(ge=1.0, le=10.0, description="Chất lượng nghệ thuật, bố cục, màu sắc")
    scene_overall: float = Field(description="Điểm tổng có trọng số (1-10)")
    verdict: str = Field(description="PASS | FAIL | MARGINAL")
    issues: List[str] = Field(default_factory=list)
    replacement_suggestion: str = Field(default="", description="Từ khoá tìm ảnh thay thế nếu FAIL")
    ai_evaluated: bool = Field(default=False, description="Có được AI Vision chấm hay chỉ heuristic?")


class ContentQualityReport(BaseModel):
    """Báo cáo thẩm định nội dung hình ảnh bằng AI Vision toàn bộ video."""
    scene_scores: List[SceneContentScore] = Field(default_factory=list)
    overall_score: float = 0.0
    failed_scene_ids: List[int] = Field(default_factory=list)
    passed: bool = True
    ai_scenes_checked: int = Field(default=0, description="Số cảnh đã phân tích bằng AI Vision")
    summary: str = ""


class AudioEvaluationReport(BaseModel):
    total_duration_ms: int = 0
    total_words: int = 0
    words_per_minute: float = 0.0
    pacing_rating: str = "PERFECT"  # "TOO_FAST", "PERFECT", "TOO_SLOW"
    has_clipping_or_silence: bool = False
    passed: bool = True
    feedback: str = ""


class VideoQAReport(BaseModel):
    video_path: str = ""
    file_size_bytes: int = 0
    duration_seconds: float = 0.0
    width: int = 1080
    height: int = 1920
    aspect_ratio: str = "9:16"
    fps: float = 30.0
    video_codec: str = "h264"
    audio_codec: str = "aac"
    is_playable: bool = True
    av_sync_offset_seconds: float = 0.0
    passed: bool = True
    issues: List[str] = Field(default_factory=list)


class SceneVisualStrategy(BaseModel):
    scene_id: int
    strategy: str = Field(description="REAL_DOCUMENTARY | STOCK_VIDEO | AI_VIDEO | AI_IMAGE")
    rationale: str = Field(description="Lý do đạo diễn lựa chọn chiến lược này")
    search_query: Optional[str] = None
    ai_prompt: Optional[str] = None
    camera_motion: Optional[str] = "zoom_in"
    sfx_cue: Optional[str] = None


class VisualPlanReport(BaseModel):
    topic_classification: str = "DAILY_REALITY"
    director_vision: str = ""
    scenes_strategy: List[SceneVisualStrategy] = Field(default_factory=list)


class EditRequest(BaseModel):
    action_type: str = Field(description="replace_media | edit_text | change_voice | change_language | remix_bgm")
    scene_id: Optional[int] = None
    new_text: Optional[str] = None
    new_media_query: Optional[str] = None
    new_media_path: Optional[str] = None
    new_voice_id: Optional[str] = None
    new_language: Optional[str] = None
    new_bgm_track: Optional[str] = None


class VideoWorkflowState(TypedDict, total=False):
    # Metadata cơ bản
    job_id: int
    topic: str
    target_channel_id: str
    channel_style: str
    language: Optional[str]
    preferred_voice: Optional[str]
    director_notes: Optional[str]
    work_dir: str
    
    # Nhận diện thực thể
    primary_entity: Optional[str]
    entity_type: str
    entity_media_pool: List[Dict[str, Any]]
    
    # Bước 1: Kịch bản (Script)
    script_data: Optional[Dict[str, Any]]  # ScriptContract dict
    script_eval: Optional[Dict[str, Any]]  # ScriptEvaluationReport dict
    script_retry_count: int
    
    # Bước 2: Giọng đọc (Audio)
    scenes_audio: List[Dict[str, Any]]
    full_audio_path: Optional[str]
    total_audio_duration_ms: int
    subtitle_path: Optional[str]
    audio_eval: Optional[Dict[str, Any]]

    # Bước 2.5: Đạo diễn Thị giác (Visual Director Agent)
    visual_plan: Optional[Dict[str, Any]]  # VisualPlanReport dict
    
    # Bước 3: Hình ảnh / Video (Media)
    scenes_media: List[Dict[str, Any]]
    media_eval: Optional[Dict[str, Any]]  # VisualEvaluationReport (kiểm tra kỹ thuật)
    content_quality_eval: Optional[Dict[str, Any]]  # ContentQualityReport (AI Vision đánh giá nội dung)
    media_retry_count: int
    content_retry_count: int
    
    # Bước 4: Video thành phẩm (Render & QA)
    rendered_video_path: Optional[str]
    video_eval: Optional[Dict[str, Any]]
    render_retry_count: int
    
    # Chỉnh sửa vi mô (Granular Edit)
    edit_request: Optional[Dict[str, Any]]
    
    # Tiến độ & Điều phối
    status: str
    current_step: str
    progress_percent: int
    progress_message: str
    error: Optional[str]
    logs: List[str]
