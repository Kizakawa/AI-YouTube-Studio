"""
Automated Test Suite for MoneyPrinterTurbo Inspired Enhancements (October 2026).
Covers:
  1. BT.709 Color Space & Primaries Tagging in FFmpeg (image_to_video, trim_video, concat, subtitles, validate_output)
  2. Video Duration Safety Margin (+0.1s in video_agent and ffmpeg_helper)
  3. 10px Dimension Tolerance in StockFetcher (format_video_candidate and aspect ratio filtering)

100% Local Execution. Zero remote network, zero git push.
"""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from app.utils.ffmpeg_helper import (
    BT709_COLOR_ARGS,
    VIDEO_DURATION_SAFETY_MARGIN,
    image_to_video,
    trim_video,
    concat_videos,
    concat_videos_with_xfade,
    burn_subtitles,
    validate_output,
)
from app.services.media.stock_fetcher import StockFetcher, DIMENSION_TOLERANCE_PX
from app.agents.video_agent import VideoAgent
from app.schemas.contracts import RenderJobContract, SceneContract


# ==============================================================================
# FEATURE 1: BT.709 COLOR SPACE & PRIMARIES TAGGING
# ==============================================================================

def test_bt709_color_args_definition():
    """Kiểm tra hằng số BT709_COLOR_ARGS có đủ 4 tham số chuẩn broadcast cho YouTube Shorts."""
    assert "-colorspace" in BT709_COLOR_ARGS
    assert "-color_primaries" in BT709_COLOR_ARGS
    assert "-color_trc" in BT709_COLOR_ARGS
    assert "-color_range" in BT709_COLOR_ARGS
    
    idx_cs = BT709_COLOR_ARGS.index("-colorspace")
    assert BT709_COLOR_ARGS[idx_cs + 1] == "bt709"
    idx_cp = BT709_COLOR_ARGS.index("-color_primaries")
    assert BT709_COLOR_ARGS[idx_cp + 1] == "bt709"
    idx_trc = BT709_COLOR_ARGS.index("-color_trc")
    assert BT709_COLOR_ARGS[idx_trc + 1] == "bt709"
    idx_cr = BT709_COLOR_ARGS.index("-color_range")
    assert BT709_COLOR_ARGS[idx_cr + 1] == "tv"


@patch("app.utils.ffmpeg_helper.run_ffmpeg")
@patch("app.utils.ffmpeg_helper.Path.unlink")
def test_bt709_in_image_to_video(mock_unlink, mock_run_ffmpeg, tmp_path):
    """Kiểm tra image_to_video áp dụng đầy đủ flag BT.709."""
    # Tạo ảnh giả lập
    from PIL import Image
    dummy_img = str(tmp_path / "test_input.jpg")
    out_video = str(tmp_path / "test_output.mp4")
    Image.new("RGB", (1080, 1920), color="blue").save(dummy_img)

    image_to_video(dummy_img, duration_seconds=3.0, output_path=out_video)

    assert mock_run_ffmpeg.called
    args = mock_run_ffmpeg.call_args[0][0]
    
    # Kiểm tra các cờ BT.709 có trong lệnh FFmpeg
    assert "-colorspace" in args and args[args.index("-colorspace") + 1] == "bt709"
    assert "-color_primaries" in args and args[args.index("-color_primaries") + 1] == "bt709"
    assert "-color_trc" in args and args[args.index("-color_trc") + 1] == "bt709"
    assert "-color_range" in args and args[args.index("-color_range") + 1] == "tv"
    assert "-pix_fmt" in args and args[args.index("-pix_fmt") + 1] == "yuv420p"


@patch("app.utils.ffmpeg_helper.run_ffmpeg")
def test_bt709_in_trim_video(mock_run_ffmpeg, tmp_path):
    """Kiểm tra trim_video áp dụng đầy đủ flag BT.709."""
    in_video = str(tmp_path / "clip.mp4")
    out_video = str(tmp_path / "trimmed.mp4")

    trim_video(input_path=in_video, duration_seconds=4.0, output_path=out_video)

    assert mock_run_ffmpeg.called
    args = mock_run_ffmpeg.call_args[0][0]
    assert "-colorspace" in args and args[args.index("-colorspace") + 1] == "bt709"
    assert "-color_primaries" in args and args[args.index("-color_primaries") + 1] == "bt709"
    assert "-color_trc" in args and args[args.index("-color_trc") + 1] == "bt709"
    assert "-color_range" in args and args[args.index("-color_range") + 1] == "tv"


@patch("app.utils.ffmpeg_helper.run_ffmpeg")
def test_bt709_in_concat_videos(mock_run_ffmpeg, tmp_path):
    """Kiểm tra concat_videos áp dụng đầy đủ flag BT.709."""
    v1 = str(tmp_path / "v1.mp4")
    v2 = str(tmp_path / "v2.mp4")
    out = str(tmp_path / "concat.mp4")

    concat_videos([v1, v2], output_path=out)

    assert mock_run_ffmpeg.called
    args = mock_run_ffmpeg.call_args[0][0]
    assert "-colorspace" in args and args[args.index("-colorspace") + 1] == "bt709"
    assert "-color_primaries" in args and args[args.index("-color_primaries") + 1] == "bt709"
    assert "-color_trc" in args and args[args.index("-color_trc") + 1] == "bt709"


@patch("app.utils.ffmpeg_helper.run_ffmpeg")
def test_bt709_in_burn_subtitles(mock_run_ffmpeg, tmp_path):
    """Kiểm tra burn_subtitles áp dụng cả -pix_fmt yuv420p và BT.709."""
    v = str(tmp_path / "in.mp4")
    sub = str(tmp_path / "sub.srt")
    out = str(tmp_path / "subbed.mp4")

    burn_subtitles(video_path=v, srt_path=sub, output_path=out)

    assert mock_run_ffmpeg.called
    args = mock_run_ffmpeg.call_args[0][0]
    assert "-pix_fmt" in args and args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert "-colorspace" in args and args[args.index("-colorspace") + 1] == "bt709"
    assert "-color_primaries" in args and args[args.index("-color_primaries") + 1] == "bt709"


@patch("app.utils.ffmpeg_helper.Path.exists", return_value=True)
@patch("app.utils.ffmpeg_helper.Path.stat")
@patch("app.utils.ffmpeg_helper.probe_file")
@patch("app.utils.ffmpeg_helper.get_duration_ms", return_value=15000)
def test_bt709_in_validate_output(mock_dur, mock_probe, mock_stat, mock_exists):
    """Kiểm tra validate_output đọc và ghi nhận metadata BT.709 từ ffprobe."""
    mock_stat.return_value.st_size = 5 * 1024 * 1024
    mock_probe.return_value = {
        "streams": [
            {
                "codec_type": "video",
                "width": 1080,
                "height": 1920,
                "codec_name": "h264",
                "colorspace": "bt709",
                "color_primaries": "bt709",
                "color_transfer": "bt709",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
            }
        ]
    }

    qa = validate_output("test_final.mp4")
    assert qa["valid"] is True
    assert qa["color_space"] == "bt709"
    assert qa["color_primaries"] == "bt709"
    assert qa["color_trc"] == "bt709"


# ==============================================================================
# FEATURE 2: VIDEO DURATION SAFETY MARGIN (+0.1s)
# ==============================================================================

def test_video_duration_safety_margin_value():
    """Kiểm tra biên độ an toàn thời lượng video chuẩn MoneyPrinterTurbo là 0.1s."""
    assert VIDEO_DURATION_SAFETY_MARGIN == 0.1


def test_video_agent_applies_safety_margin(tmp_path):
    """Kiểm tra VideoAgent._prepare_scene_clips đệm thêm VIDEO_DURATION_SAFETY_MARGIN vào phân cảnh cuối."""
    agent = VideoAgent()
    dummy_media = str(tmp_path / "dummy.jpg")
    Path(dummy_media).write_text("fake image")

    job = RenderJobContract(
        job_id=999,
        title="Test Title",
        total_audio_duration_ms=7000,
        scenes=[
            SceneContract(
                scene_id=1,
                narration="Scene 1",
                visual_keyword="keyword 1",
                media_path=dummy_media,
                audio_path=str(tmp_path / "a1.mp3"),
                audio_duration_ms=3000,
            ),
            SceneContract(
                scene_id=2,
                narration="Scene 2",
                visual_keyword="keyword 2",
                media_path=dummy_media,
                audio_path=str(tmp_path / "a2.mp3"),
                audio_duration_ms=4000,
            ),
        ],
        output_path=str(tmp_path / "final.mp4"),
        full_audio_path=str(tmp_path / "audio.mp3"),
    )

    with patch("app.agents.video_agent.image_to_video") as mock_i2v, \
         patch("app.agents.video_agent.trim_video") as mock_trim:
        mock_i2v.return_value = "dummy.mp4"
        
        # Gọi _prepare_scene_clips
        clips = agent._prepare_scene_clips(job.scenes, str(tmp_path), job)
        
        assert len(clips) == 2
        # Cảnh 1: base (3.0) + transition_duration (0.3) = 3.3s
        # Cảnh 2 (cảnh cuối): base (4.0) + tail_buffer (0.6) + VIDEO_DURATION_SAFETY_MARGIN (0.1) = 4.7s
        call_durations = [call[1]["duration_seconds"] for call in mock_i2v.call_args_list]
        assert abs(call_durations[0] - 3.3) < 0.001
        assert abs(call_durations[1] - (4.0 + 0.6 + VIDEO_DURATION_SAFETY_MARGIN)) < 0.001


# ==============================================================================
# FEATURE 3: 10PX DIMENSION TOLERANCE IN STOCK FETCHER
# ==============================================================================

def test_dimension_tolerance_constant():
    """Kiểm tra hằng số DIMENSION_TOLERANCE_PX được định nghĩa là 10px."""
    assert DIMENSION_TOLERANCE_PX == 10
    assert StockFetcher.DIMENSION_TOLERANCE_PX == 10


def test_10px_tolerance_full_hd_selection():
    """
    Kiểm tra video có kích thước bị lệch nhẹ (ví dụ 1076x1916 thay vì 1080x1920)
    vẫn được nhận diện chuẩn xác là Full HD (score 3000) nhờ dung sai 10px.
    """
    candidate = {
        "id": 202,
        "url": "https://www.pexels.com/video/cyber-city-202/",
        "duration": 12,
        "image": "https://images.pexels.com/city.jpg",
        "video_files": [
            {
                "id": 1,
                "file_type": "video/mp4",
                "width": 720,
                "height": 1280,
                "link": "https://videos.pexels.com/portrait_720p.mp4"  # score 2780
            },
            {
                "id": 2,
                "file_type": "video/mp4",
                "width": 1076,  # Lệch 4px so với 1080
                "height": 1916,  # Lệch 4px so với 1920
                "link": "https://videos.pexels.com/compressed_1080p.mp4"  # dung sai 10px -> score 3000 (Top 1)
            }
        ]
    }

    result = StockFetcher.format_video_candidate(candidate, query="city")
    assert result is not None
    # Phải chọn bản 1076x1916 thay vì bị giáng cấp xuống 720p!
    assert result["url"] == "https://videos.pexels.com/compressed_1080p.mp4"
    assert result["width"] == 1076
    assert result["height"] == 1916


def test_10px_tolerance_720p_selection():
    """Kiểm tra video 720x1276 (lệch 4px so với 1280) vẫn được nhận diện chuẩn HD (score 2780)."""
    candidate = {
        "id": 203,
        "url": "https://www.pexels.com/video/nature-203/",
        "duration": 10,
        "video_files": [
            {
                "id": 1,
                "file_type": "video/mp4",
                "width": 540,
                "height": 960,
                "link": "https://videos.pexels.com/sd_960p.mp4"  # score ~1460
            },
            {
                "id": 2,
                "file_type": "video/mp4",
                "width": 718,   # Lệch 2px
                "height": 1276,  # Lệch 4px so với 1280
                "link": "https://videos.pexels.com/compressed_720p.mp4"  # dung sai 10px -> score 2780 (Top 1)
            }
        ]
    }

    result = StockFetcher.format_video_candidate(candidate, query="nature")
    assert result is not None
    assert result["url"] == "https://videos.pexels.com/compressed_720p.mp4"


def test_10px_tolerance_near_portrait_aspect_ratio():
    """
    Kiểm tra video cận vuông (width hơi lớn hơn height 5px do encoder rounding, vd 725x720)
    vẫn không bị loại bỏ hoàn toàn khỏi danh sách portrait_files nhờ dung sai 10px.
    """
    candidate = {
        "id": 204,
        "url": "https://www.pexels.com/video/abstract-204/",
        "duration": 8,
        "video_files": [
            {
                "id": 1,
                "file_type": "video/mp4",
                "width": 725,   # width > height bởi 5px (<= 10px tolerance)
                "height": 720,
                "link": "https://videos.pexels.com/near_square.mp4"
            }
        ]
    }

    result = StockFetcher.format_video_candidate(candidate, query="abstract")
    assert result is not None
    assert result["url"] == "https://videos.pexels.com/near_square.mp4"
