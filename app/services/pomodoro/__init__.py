"""
Pomodoro Living Studio v2.0 Package (`app/services/pomodoro`)
=============================================================
Hệ thống mô-đun hóa hoàn chỉnh sản xuất Video Nhạc Học Tập Dài (1–4 Giờ, Full HD 1080p)
theo đúng 6 Module & 4 Phase của Study Video Upgrade Plan:
  - `MascotEngine`: 5 linh vật có cả tranh Ngủ/Học & tranh Thức dậy giờ nghỉ + 6 vùng cử động
  - `VFXPipeline`: 7 hiệu ứng Retro Lo-Fi & VHS Glitch định kỳ
  - `HUDRenderer`: Bong bóng hội thoại song ngữ (`vi`/`en`/`bilingual`) + Tần số đài 87.5 FM + Milestone 50%
  - `SceneComposer`: 6 chủ đề mùa có tranh riêng (`spring_sakura`, `summer_breeze`, `autumn_cozy`, `winter_cabin`, `christmas_lofi`, `halloween_study`)
  - `AudioMixer`: 3 lớp âm thanh (Google Flow WAV + Singing Bowl Bell + Ambience `rain`/`fireplace`/`vinyl`)
  - `ShortsCutter` & `BatchProducer`: Tự cắt YouTube Shorts Teaser 9:16 và chạy Batch 7-Day Challenge.
"""

from app.services.pomodoro.audio_mixer import (
    AMBIENCE_FILTER_PRESETS,
    build_master_audio_stream,
    render_phase_audio_clip,
)
from app.services.pomodoro.batch_producer import BATCH_PRESETS, run_pomodoro_batch
from app.services.pomodoro.cli import build_pomodoro_study_video, main
from app.services.pomodoro.hud_renderer import (
    SPEECH_BANK,
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
    select_scene_artwork,
)
from app.services.pomodoro.seo_generator import generate_youtube_seo_package
from app.services.pomodoro.shorts_cutter import cut_pomodoro_short_teaser
from app.services.pomodoro.thumbnail_generator import generate_youtube_thumbnail
from app.services.pomodoro.vfx_pipeline import (
    ALL_VFX_NAMES,
    VFXConfig,
    apply_frame_vfx_numpy,
    build_ffmpeg_vfx_post_filter,
    is_in_glitch_burst,
)
from app.services.pomodoro.video_renderer import (
    concat_video_segments,
    detect_optimal_h264_encoder,
    render_phase_video_segment,
)

__all__ = [
    "ALL_VFX_NAMES",
    "AMBIENCE_FILTER_PRESETS",
    "AnimationController",
    "BATCH_PRESETS",
    "DEFAULT_CATALOG_BGM_ROOT",
    "DEFAULT_MASCOTS_ROOT",
    "DEFAULT_MASTER_BGM_ROOT",
    "DEFAULT_OUTPUT_ROOT",
    "DEFAULT_THEMES_ROOT",
    "MODE_DISPLAY_META",
    "MODE_FOLDER_WEIGHTS",
    "MascotPack",
    "PhaseSegment",
    "SPEECH_BANK",
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
    "cut_pomodoro_short_teaser",
    "detect_optimal_h264_encoder",
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
    "run_pomodoro_batch",
    "select_scene_artwork",
    "select_tracks_for_duration",
]
