"""
app.utils.ffmpeg_helper — Facade điều phối bộ công cụ FFmpeg (giữ tương thích ngược 100%).

Đã được tách thành 6 module chuyên biệt trong `app/utils/ffmpeg/` (~200–560 dòng/file):
- `core_encoder.py`: Khởi tạo PATH, GPU Encoder detection, ffprobe & hàm chạy lệnh cơ sở.
- `color_and_motion.py`: Chỉnh màu điện ảnh (Color Grading), hiệu ứng Ken Burns & Multi-Cut Hook.
- `peak_and_trim.py`: Quét đoạn cao trào (Peak Action Scanner) & cắt cúp chuẩn 9:16 (`trim_video`).
- `concat_and_transitions.py`: Ghép nối phân cảnh & hiệu ứng chuyển cảnh (`concat_videos`, `concat_videos_with_xfade`).
- `audio_and_sfx.py`: Trộn giọng đọc + BGM + SFX & đo chuẩn âm lượng LUFS (`mux_audio_video`, `measure_audio_loudness_and_silence`).
- `subtitles_and_thumbnail.py`: Đốt phụ đề ASS/SRT, kiểm định QA video & trích xuất ảnh bìa (`burn_subtitles`, `validate_output`, `extract_shock_thumbnail`).
"""
from pathlib import Path
from app.utils.ffmpeg.core_encoder import (
    BT709_COLOR_ARGS,
    FFMPEG_TIMEOUT,
    FFPROBE_TIMEOUT,
    VIDEO_DURATION_SAFETY_MARGIN,
    _cached_ffmpeg_installed,
    _cached_ffmpeg_version,
    _cached_hardware_encoder,
    _cached_supports_fps_mode,
    _ensure_ffmpeg_in_path,
    _supports_fps_mode,
    build_atempo_filter,
    detect_hardware_encoder,
    get_duration_ms,
    get_ffmpeg_exe,
    get_ffmpeg_version,
    get_video_dimensions,
    get_video_encoder_args,
    is_completed_clean_mp4,
    is_ffmpeg_installed,
    probe_file,
    run_ffmpeg,
    run_ffmpeg_with_gpu_fallback,
)
from app.utils.ffmpeg.color_and_motion import (
    get_hook_live_fx_filter,
    get_niche_color_grading,
    image_to_video,
    multi_pose_loop_to_video,
    render_opening_hook_multi_cut,
)
from app.utils.ffmpeg.peak_and_trim import (
    _scan_video_peak_metrics,
    find_hook_peak_action_offset,
    find_hook_peak_window,
    trim_video,
)
from app.utils.ffmpeg.concat_and_transitions import (
    NICHE_TRANSITIONS_MAP,
    concat_videos,
    concat_videos_with_xfade,
    resolve_niche_transitions,
)
from app.utils.ffmpeg.audio_and_sfx import (
    _find_sfx_file,
    detect_audio_speech_start,
    list_available_sfx,
    measure_audio_loudness_and_silence,
    mix_sfx_transitions,
    mux_audio_video,
    resolve_smart_sfx_cue,
)
from app.utils.ffmpeg.subtitles_and_thumbnail import (
    burn_subtitles,
    create_placeholder_image,
    extract_shock_thumbnail,
    validate_output,
)

__all__ = [
    "BT709_COLOR_ARGS",
    "FFMPEG_TIMEOUT",
    "FFPROBE_TIMEOUT",
    "NICHE_TRANSITIONS_MAP",
    "VIDEO_DURATION_SAFETY_MARGIN",
    "build_atempo_filter",
    "burn_subtitles",
    "concat_videos",
    "concat_videos_with_xfade",
    "create_placeholder_image",
    "detect_audio_speech_start",
    "detect_hardware_encoder",
    "extract_shock_thumbnail",
    "find_hook_peak_action_offset",
    "find_hook_peak_window",
    "get_duration_ms",
    "get_ffmpeg_exe",
    "get_ffmpeg_version",
    "get_hook_live_fx_filter",
    "get_niche_color_grading",
    "get_video_dimensions",
    "get_video_encoder_args",
    "image_to_video",
    "is_completed_clean_mp4",
    "is_ffmpeg_installed",
    "list_available_sfx",
    "measure_audio_loudness_and_silence",
    "mix_sfx_transitions",
    "multi_pose_loop_to_video",
    "mux_audio_video",
    "probe_file",
    "render_opening_hook_multi_cut",
    "resolve_niche_transitions",
    "resolve_smart_sfx_cue",
    "run_ffmpeg",
    "run_ffmpeg_with_gpu_fallback",
    "trim_video",
    "validate_output",
    "verify_hook_clip_genuine_motion",
]


def verify_hook_clip_genuine_motion(video_path: str) -> tuple[bool, float, str]:
    """Kiểm định video mở đầu 5-7s có chuyển động thực sự (không phải ảnh tĩnh/slideshow hay bị khựng frame)."""
    from app.graph.nodes.hook_video_node import verify_hook_clip_genuine_motion as _verify_hook_mot
    return _verify_hook_mot(video_path)
