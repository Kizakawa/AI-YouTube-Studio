"""
Pomodoro Study Music & Living Studio Builder (`app/services/pomodoro_studio_builder.py`)
========================================================================================
Facade tương thích ngược (Backward-Compatible Entrypoint) cho gói mô-đun hóa
`app.services.pomodoro` (Living Studio v2.0 — Full 6-Module & 4-Phase Implementation).
"""

import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.services.pomodoro import (
    ALL_VFX_NAMES,
    AMBIENCE_FILTER_PRESETS,
    AnimationController,
    BATCH_PRESETS,
    DEFAULT_CATALOG_BGM_ROOT,
    DEFAULT_MASCOTS_ROOT,
    DEFAULT_MASTER_BGM_ROOT,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_THEMES_ROOT,
    MODE_DISPLAY_META,
    MODE_FOLDER_WEIGHTS,
    MascotPack,
    PhaseSegment,
    SPEECH_BANK,
    ThemePack,
    VFXConfig,
    apply_frame_vfx_numpy,
    build_ffmpeg_vfx_post_filter,
    build_master_audio_stream,
    build_pomodoro_schedule,
    build_pomodoro_study_video,
    clean_track_display_name,
    concat_video_segments,
    create_phase_hud_overlay_png,
    create_speech_bubble_overlay_png,
    cut_pomodoro_short_teaser,
    detect_optimal_h264_encoder,
    discover_study_library,
    ensure_cozy_dog_cinemagraph_loop,
    ensure_mascot_cinemagraph_loop,
    format_timestamp,
    generate_cozy_dog_study_background,
    generate_youtube_seo_package,
    generate_youtube_thumbnail,
    get_wav_duration,
    is_in_glitch_burst,
    list_available_mascots,
    list_available_themes,
    load_mascot_pack,
    load_theme_pack,
    main,
    render_phase_audio_clip,
    render_phase_video_segment,
    resolve_bell_sfx_paths,
    run_pomodoro_batch,
    select_scene_artwork,
    select_tracks_for_duration,
)

if __name__ == "__main__":
    main()
