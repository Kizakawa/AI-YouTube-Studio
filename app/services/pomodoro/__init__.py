"""
Pomodoro Living Studio v2.0 Package (`app/services/pomodoro`)
=============================================================
Hệ thống mô-đun hóa sản xuất Video Nhạc Học Tập Dài (1–4 Giờ, Full HD 1080p)
với 5+ Linh Vật động (`MascotEngine`), 6 Hiệu ứng Retro VHS Glitch (`VFXPipeline`),
Bong bóng hội thoại tương tác giờ nghỉ (`SpeechBubble`), và 6 Chủ đề theo mùa (`ThemePack`).
"""

from app.services.pomodoro.audio_mixer import (
    build_master_audio_stream,
    render_phase_audio_clip,
)
from app.services.pomodoro.cli import build_pomodoro_study_video, main
from app.services.pomodoro.hud_renderer import (
    create_phase_hud_overlay_png,
    create_speech_bubble_overlay_png,
)
from app.services.pomodoro.mascot_engine import (
    AnimationController,
    MascotPack,
    generate_cozy_dog_study_background,
    list_available_mascots,
    load_mascot_pack,
)
from app.services.pomodoro.scheduler import (
    DEFAULT_CATALOG_BGM_ROOT,
    DEFAULT_MASCOTS_ROOT,
    DEFAULT_MASTER_BGM_ROOT,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_THEMES_ROOT,
    MODE_DISPLAY_META,
    MODE_FOLDER_WEIGHTS,
    PhaseSegment,
    build_pomodoro_schedule,
    clean_track_display_name,
    discover_study_library,
    format_timestamp,
    get_wav_duration,
    resolve_bell_sfx_paths,
    select_tracks_for_duration,
)
from app.services.pomodoro.scene_composer import (
    ThemePack,
    ensure_cozy_dog_cinemagraph_loop,
    ensure_mascot_cinemagraph_loop,
    list_available_themes,
    load_theme_pack,
)
from app.services.pomodoro.seo_generator import generate_youtube_seo_package
from app.services.pomodoro.thumbnail_generator import generate_youtube_thumbnail
from app.services.pomodoro.vfx_pipeline import (
    VFXConfig,
    apply_frame_vfx_numpy,
    build_ffmpeg_vfx_post_filter,
    is_in_glitch_burst,
)
from app.services.pomodoro.video_renderer import (
    concat_video_segments,
    render_phase_video_segment,
)

__all__ = [
    "AnimationController",
    "DEFAULT_CATALOG_BGM_ROOT",
    "DEFAULT_MASCOTS_ROOT",
    "DEFAULT_MASTER_BGM_ROOT",
    "DEFAULT_OUTPUT_ROOT",
    "DEFAULT_THEMES_ROOT",
    "MODE_DISPLAY_META",
    "MODE_FOLDER_WEIGHTS",
    "MascotPack",
    "PhaseSegment",
    "ThemePack",
    "VFXConfig",
    "apply_frame_vfx_numpy",
    "build_ffmpeg_vfx_post_filter",
    "build_master_audio_stream",
    "build_pomodoro_schedule",
    "build_pomodoro_study_video",
    "clean_track_display_name",
    "concat_video_segments",
    "create_phase_hud_overlay_png",
    "create_speech_bubble_overlay_png",
    "discover_study_library",
    "ensure_cozy_dog_cinemagraph_loop",
    "ensure_mascot_cinemagraph_loop",
    "format_timestamp",
    "generate_cozy_dog_study_background",
    "generate_youtube_seo_package",
    "generate_youtube_thumbnail",
    "get_wav_duration",
    "is_in_glitch_burst",
    "list_available_mascots",
    "list_available_themes",
    "load_mascot_pack",
    "load_theme_pack",
    "main",
    "render_phase_audio_clip",
    "render_phase_video_segment",
    "resolve_bell_sfx_paths",
    "select_tracks_for_duration",
]
