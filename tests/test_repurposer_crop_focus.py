"""
Automated Test Suite for Repurposer Crop Focus (Left / Center / Right) Upgrade.
Verifies:
  1. smart_crop_full with center, left, right crop_focus
  2. center_crop with center, left, right crop_focus
  3. Safe fallback for invalid crop_focus values
  4. Inclusion of -pix_fmt yuv420p and BT.709 color tags
  5. FastAPI RepurposeRenderRequest validation
"""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from fastapi.testclient import TestClient

from app.services.video_repurposer import VideoRepurposerService
from app.web.server import app, RepurposeRenderRequest


@pytest.fixture
def repurposer():
    return VideoRepurposerService()


@patch("app.services.video_repurposer.detect_audio_speech_start", return_value=0.0)
@patch("app.services.video_repurposer.probe_file", return_value={"streams": [{"codec_type": "audio"}]})
@patch("app.services.video_repurposer.preflight_repurpose_qa", return_value={"passed": True, "feedback": "OK"})
@patch("app.services.video_repurposer.run_ffmpeg")
@patch("os.path.exists", return_value=True)
@patch("os.path.getsize", return_value=1024 * 1024)
def test_crop_focus_smart_crop_full(mock_getsize, mock_exists, mock_run_ffmpeg, mock_qa, mock_probe, mock_vad, repurposer):
    """Kiểm tra smart_crop_full sinh đúng tọa độ X cho center, left, right."""
    cases = [
        ("center", "(in_w-out_w)/2"),
        ("left", "(in_w-out_w)*0.18"),
        ("right", "(in_w-out_w)*0.82"),
        ("invalid_value", "(in_w-out_w)/2"),  # Fallback an toàn về center
    ]

    for focus, expected_expr in cases:
        mock_run_ffmpeg.reset_mock()
        repurposer.render_repurposed_short(
            source_video_path="dummy.mp4",
            start_seconds=10.0,
            end_seconds=25.0,
            output_filename=f"test_{focus}.mp4",
            layout_mode="smart_crop_full",
            crop_focus=focus,
            add_subtitles=False,
        )

        assert mock_run_ffmpeg.called
        args = mock_run_ffmpeg.call_args[0][0]
        filter_idx = args.index("-filter_complex")
        filter_str = args[filter_idx + 1]

        # Kiểm tra biểu thức crop chứa tọa độ X mong muốn
        assert f"crop=1080:1920:{expected_expr}:(in_h-out_h)/2" in filter_str

        # Kiểm tra cờ BT.709 và yuv420p
        assert "-pix_fmt" in args and args[args.index("-pix_fmt") + 1] == "yuv420p"
        assert "-colorspace" in args and args[args.index("-colorspace") + 1] == "bt709"


@patch("app.services.video_repurposer.detect_audio_speech_start", return_value=0.0)
@patch("app.services.video_repurposer.probe_file", return_value={"streams": [{"codec_type": "audio"}]})
@patch("app.services.video_repurposer.preflight_repurpose_qa", return_value={"passed": True, "feedback": "OK"})
@patch("app.services.video_repurposer.run_ffmpeg")
@patch("os.path.exists", return_value=True)
@patch("os.path.getsize", return_value=1024 * 1024)
def test_crop_focus_center_crop(mock_getsize, mock_exists, mock_run_ffmpeg, mock_qa, mock_probe, mock_vad, repurposer):
    """Kiểm tra center_crop sinh đúng tọa độ X cho center, left, right."""
    cases = [
        ("center", "(iw-ih*9/16)/2"),
        ("left", "(iw-ih*9/16)*0.18"),
        ("right", "(iw-ih*9/16)*0.82"),
    ]

    for focus, expected_expr in cases:
        mock_run_ffmpeg.reset_mock()
        repurposer.render_repurposed_short(
            source_video_path="dummy.mp4",
            start_seconds=5.0,
            end_seconds=15.0,
            output_filename=f"test_cc_{focus}.mp4",
            layout_mode="center_crop",
            crop_focus=focus,
            add_subtitles=False,
        )

        assert mock_run_ffmpeg.called
        args = mock_run_ffmpeg.call_args[0][0]
        filter_idx = args.index("-filter_complex")
        filter_str = args[filter_idx + 1]

        assert f"crop=ih*9/16:ih:{expected_expr}:0" in filter_str


def test_repurpose_render_request_schema():
    """Kiểm tra schema RepurposeRenderRequest nhận crop_focus mặc định là center."""
    req = RepurposeRenderRequest(
        source="video.mp4",
        start_seconds=0.0,
        end_seconds=30.0,
    )
    assert req.crop_focus == "center"

    req_left = RepurposeRenderRequest(
        source="video.mp4",
        start_seconds=0.0,
        end_seconds=30.0,
        crop_focus="left",
    )
    assert req_left.crop_focus == "left"


def test_api_render_clip_receives_crop_focus(tmp_path):
    """Kiểm tra FastAPI endpoint /api/repurposer/render-clip nhận và truyền crop_focus."""
    client = TestClient(app)
    
    with patch("os.path.exists", return_value=True), \
         patch("app.services.video_repurposer.repurposer_service.render_repurposed_short") as mock_render:
        
        mock_render.return_value = {
            "status": "success",
            "output_path": "output/clip.mp4",
            "relative_url": "/media/clip.mp4",
        }

        payload = {
            "source": "d:/test.mp4",
            "start_seconds": 1.0,
            "end_seconds": 15.0,
            "crop_focus": "right",
            "title": "Test Right Speaker Clip",
        }

        res = client.post("/api/repurposer/render-clip", json=payload)
        assert res.status_code == 200
        assert mock_render.called
        assert mock_render.call_args[1]["crop_focus"] == "right"
