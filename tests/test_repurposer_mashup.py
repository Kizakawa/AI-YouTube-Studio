"""
Automated Test Suite for Video Repurposer Multi-Clip Mashup (Nấu Lại Video Siêu Cấp).
Inspired by Auto-Editor, VideoCutterAI, and Lossless-Cut.

Verifies:
  1. Multi-clip fallback generation with golden ratio cut points & roles (HOOK, SETUP, TENSION, CLIMAX, LOOP).
  2. Gemini AI mashup storyboard extraction and graceful error handling.
  3. render_mashup_short pipeline:
     - Subtitle stripping with `-sn` flag (LosslessCut concept)
     - Dynamic Zoom Punch per micro-clip
     - FFmpeg concat demuxer execution
     - BGM ducking & SFX Whoosh transition mixing
     - ASS Karaoke subtitle burning & fallback
     - Metadata persistence (.meta.json) with is_mashup flag
  4. Validation rules (minimum 2 clips, file existence).
  5. FastAPI endpoints:
     - /api/repurposer/analyze includes "mashups" key
     - /api/repurposer/render-mashup handles request validation and processing
"""
import os
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from fastapi.testclient import TestClient

from app.services.video_repurposer import VideoRepurposerService, CLIPS_DIR
from app.web.server import app, RepurposeMashupRenderRequest


@pytest.fixture
def repurposer():
    return VideoRepurposerService()


def test_niche_fallback_mashups(repurposer):
    """Kiểm tra fallback đa phân đoạn thông minh tính toán đúng 5 micro-clips và các vai trò chuẩn."""
    from app.services.niche_manager import get_niche
    niche_profile = get_niche("general")
    video_title = "The Secret Life of Ocean Giants"
    video_dur = 600.0

    mashups = repurposer._build_niche_fallback_mashups(niche_profile, video_title, video_dur)
    assert isinstance(mashups, list)
    assert len(mashups) >= 1

    m = mashups[0]
    assert "clips" in m
    assert len(m["clips"]) == 5

    roles = [c["role"] for c in m["clips"]]
    assert roles == ["HOOK", "SETUP", "TENSION", "CLIMAX", "LOOP"]

    # Kiểm tra zoom_punch
    assert m["clips"][0]["zoom_punch"] == 1.15  # Cold open hook
    assert m["clips"][1]["zoom_punch"] == 1.0   # Setup
    assert m["clips"][2]["zoom_punch"] == 1.12  # Tension
    assert m["clips"][3]["zoom_punch"] == 1.08  # Climax
    assert m["clips"][4]["zoom_punch"] == 1.15  # Loop

    # Kiểm tra context_badge chống loạn mạch đảo cảnh
    for c in m["clips"]:
        assert "context_badge" in c
        assert len(c["context_badge"]) > 5

    # Kiểm tra tổng thời lượng nằm trong chuẩn Shorts (35s - 55s)
    total_dur = sum(c["duration"] for c in m["clips"])
    assert 30.0 <= total_dur <= 60.0
    assert m["total_duration"] == round(total_dur, 1)


def test_save_mashup_ass_file_dual_layer(tmp_path):
    """Kiểm tra sinh phụ đề ASS 2 tầng: Top Context Badges (\\an8) và Bottom Kinetic Captions (\\an2)."""
    from app.utils.subtitle_helper import SubtitleHelper

    clips = [
        {
            "clip_id": 1,
            "role": "HOOK",
            "context_badge": "🚨 [ COLD OPEN: THE CLIMAX ]",
            "start_seconds": 10.0,
            "end_seconds": 14.0,
            "duration": 4.0,
            "transcript_snippet": "The puppies were finally pulled out to safety!",
        },
        {
            "clip_id": 2,
            "role": "SETUP",
            "context_badge": "⏪ [ 3 HOURS EARLIER (FLASHBACK) ]",
            "start_seconds": 0.0,
            "end_seconds": 8.0,
            "duration": 8.0,
            "transcript_snippet": "An emergency call came in about crying under the foundation.",
        }
    ]

    out_ass = tmp_path / "test_mashup.ass"
    SubtitleHelper.save_mashup_ass_file(clips, str(out_ass), niche_id="animal_rescue")

    assert out_ass.exists()
    content = out_ass.read_text(encoding="utf-8")

    # 1. Kiểm tra có Top Context Badge
    assert "PatternInterruptBadge" in content
    assert r"\an8" in content
    assert "🚨 [ COLD OPEN: THE CLIMAX ]" in content
    assert "⏪ [ 3 HOURS EARLIER (FLASHBACK) ]" in content

    # 2. Kiểm tra có Bottom Kinetic Karaoke với \kf tags
    assert "Karaoke" in content
    assert r"\kf" in content


def test_analyze_viral_mashups_gemini_success(repurposer):
    """Kiểm tra analyze_viral_mashups phân tích kịch bản Gemini thành công."""
    mock_gemini_response = json.dumps([
        {
            "mashup_id": 1,
            "title": "Unbelievable Survival in Deep Sea #Shorts",
            "hook_sentence": "No one expected this creature to survive!",
            "story_arc": "Climax Hook -> Origin -> Escalation -> Shocking Truth -> Loop",
            "viral_score": 98,
            "total_duration": 45.0,
            "bgm_mood": "suspense",
            "summary": "Epic montage of survival against all odds.",
            "pinned_comment": "Did you see that ending coming? Tell us below! 👇",
            "description": "Crazy deep sea survival! #shorts #viral",
            "clips": [
                {
                    "clip_id": 1,
                    "role": "HOOK",
                    "start_seconds": 120.0,
                    "end_seconds": 123.5,
                    "duration": 3.5,
                    "zoom_punch": 1.15,
                    "transition": "whoosh",
                    "summary": "Monster appears",
                    "transcript_snippet": "This was the single closest call ever recorded!"
                },
                {
                    "clip_id": 2,
                    "role": "SETUP",
                    "start_seconds": 10.0,
                    "end_seconds": 18.0,
                    "duration": 8.0,
                    "zoom_punch": 1.0,
                    "transition": "cut",
                    "summary": "Diver enters cave",
                    "transcript_snippet": "He was swimming completely unaware of what lurked inside."
                },
                {
                    "clip_id": 3,
                    "role": "CLIMAX",
                    "start_seconds": 124.0,
                    "end_seconds": 140.0,
                    "duration": 16.0,
                    "zoom_punch": 1.08,
                    "transition": "whoosh",
                    "summary": "Narrow escape",
                    "transcript_snippet": "And with zero oxygen left, he made the impossible sprint!"
                }
            ]
        }
    ])

    with patch.object(repurposer.content_agent, "_call_api", return_value=mock_gemini_response):
        res = repurposer.analyze_viral_mashups(
            transcript_entries=[{"start": 10.0, "duration": 5.0, "text": "Testing"}],
            video_title="Deep Sea Exploration",
            video_duration=300.0,
            niche_id="ocean_mysteries",
        )

        assert len(res) == 1
        assert res[0]["title"] == "Unbelievable Survival in Deep Sea #Shorts"
        assert len(res[0]["clips"]) == 3
        assert res[0]["clips"][0]["role"] == "HOOK"


def test_analyze_viral_mashups_fallback_on_error(repurposer):
    """Kiểm tra khi Gemini API gặp lỗi thì tự động fallback về kịch bản thông minh mà không làm sập luồng."""
    with patch.object(repurposer.content_agent, "_call_api", side_effect=RuntimeError("Gemini Quota Exceeded")):
        res = repurposer.analyze_viral_mashups(
            transcript_entries=[],
            video_title="Failsafe Video Test",
            video_duration=240.0,
            niche_id="general",
        )
        assert isinstance(res, list)
        assert len(res) >= 1
        assert len(res[0]["clips"]) == 5


@patch("app.services.video_repurposer.probe_file", return_value={"streams": [{"codec_type": "audio"}]})
@patch("app.services.video_repurposer.preflight_repurpose_qa", return_value={"passed": True, "score": 98})
@patch("app.services.video_repurposer.run_ffmpeg")
@patch("app.utils.ffmpeg_helper.mix_sfx_transitions")
def test_render_mashup_short_pipeline(mock_mix_sfx, mock_run_ffmpeg, mock_qa, mock_probe, repurposer, tmp_path):
    """Kiểm tra toàn bộ pipeline render_mashup_short: bóc tách -sn, concat, mix BGM/SFX, phụ đề, meta.json."""
    # Tạo fake source video
    fake_source = tmp_path / "fake_input.mp4"
    fake_source.write_bytes(b"dummy_mp4_bytes")

    clips_data = [
        {
            "clip_id": 1,
            "role": "HOOK",
            "start_seconds": 50.0,
            "end_seconds": 53.0,
            "duration": 3.0,
            "zoom_punch": 1.15,
            "transcript_snippet": "Look at what happens here!"
        },
        {
            "clip_id": 2,
            "role": "SETUP",
            "start_seconds": 10.0,
            "end_seconds": 18.0,
            "duration": 8.0,
            "zoom_punch": 1.0,
            "transcript_snippet": "It all started very innocently."
        },
        {
            "clip_id": 3,
            "role": "CLIMAX",
            "start_seconds": 54.0,
            "end_seconds": 70.0,
            "duration": 16.0,
            "zoom_punch": 1.08,
            "transcript_snippet": "The final explosion was incredible!"
        },
    ]

    out_name = f"test_mashup_{tmp_path.name}.mp4"
    out_file = CLIPS_DIR / out_name
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_bytes(b"fake_output_mashup_mp4_content_123456789")

    try:
        result = repurposer.render_mashup_short(
            source_video_path=str(fake_source),
            clips=clips_data,
            output_filename=out_name,
            niche_id="ocean_mysteries",
            layout_mode="smart_crop_full",
            crop_focus="center",
            add_subtitles=True,
            enable_sfx=True,
            bgm_mood="suspense",
            title="The Shocking Ocean Secret Revealed",
            device_id="test_dev_01",
            device_name="Test Device",
        )

        assert result["success"] is True
        assert result["filename"] == out_name
        assert result["clips_count"] == 3
        assert result["duration"] == 27.0  # 3 + 8 + 16

        # Kiểm tra run_ffmpeg được gọi và có cờ -sn (Drop phụ đề cũ)
        ffmpeg_calls = mock_run_ffmpeg.call_args_list
        assert len(ffmpeg_calls) >= 3  # Ít nhất 3 subclips + concat + sub burn

        first_clip_cmd = ffmpeg_calls[0][0][0]
        assert "-sn" in first_clip_cmd, "Bắt buộc phải có cờ -sn để triệt tiêu phụ đề nhúng gốc"
        assert "-pix_fmt" in first_clip_cmd
        assert first_clip_cmd[first_clip_cmd.index("-pix_fmt") + 1] == "yuv420p"

        # Kiểm tra concat demuxer được gọi
        concat_call = any("-f" in call[0][0] and "concat" in call[0][0] for call in ffmpeg_calls)
        assert concat_call, "Concat demuxer (-f concat) bắt buộc phải được gọi để ghép nối các clips"

        # Kiểm tra SFX transition được gọi
        assert mock_mix_sfx.called, "mix_sfx_transitions phải được gọi khi enable_sfx=True"

        # Kiểm tra file metadata .meta.json được ghi
        meta_file = CLIPS_DIR / f"{Path(out_name).stem}.meta.json"
        assert meta_file.exists(), "File .meta.json phải được tạo cho mashup clip"
        with open(meta_file, "r", encoding="utf-8") as mf:
            meta = json.load(mf)
            assert meta["is_mashup"] is True
            assert meta["clips_count"] == 3
            assert meta["device_id"] == "test_dev_01"
            assert "amazon_product" in meta
            assert "qa_report" in meta

    finally:
        if out_file.exists():
            out_file.unlink()
        meta_path = CLIPS_DIR / f"{Path(out_name).stem}.meta.json"
        if meta_path.exists():
            meta_path.unlink()


def test_render_mashup_short_validation(repurposer):
    """Kiểm tra các quy tắc kiểm tra tính hợp lệ dữ liệu đầu vào."""
    # 1. File nguồn không tồn tại
    with pytest.raises(FileNotFoundError):
        repurposer.render_mashup_short(
            source_video_path="non_existent_file_xyz123.mp4",
            clips=[{"start_seconds": 1.0, "end_seconds": 4.0}],
            output_filename="dummy.mp4"
        )

    # 2. Ít hơn 2 clips
    with pytest.raises(ValueError, match="tối thiểu 2 phân đoạn"):
        repurposer.render_mashup_short(
            source_video_path=__file__,  # Dùng file có thật
            clips=[{"start_seconds": 1.0, "end_seconds": 4.0}],
            output_filename="dummy.mp4"
        )


def test_api_repurposer_mashup_endpoints():
    """Kiểm tra các API endpoint /api/repurposer/analyze và /api/repurposer/render-mashup."""
    client = TestClient(app)

    # 1. Kiểm tra /api/repurposer/analyze trả về cả "mashups"
    with patch("app.services.video_repurposer.VideoRepurposerService.analyze_viral_moments", return_value=[{"segment_id": 1}]), \
         patch("app.services.video_repurposer.VideoRepurposerService.analyze_viral_mashups", return_value=[{"mashup_id": 1, "title": "Test Mashup"}]), \
         patch("app.services.video_repurposer.VideoRepurposerService.get_video_metadata", return_value={"title": "Test", "duration_seconds": 120.0, "source_type": "local"}):

        res = client.post("/api/repurposer/analyze", json={
            "url_or_path": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "niche_id": "general",
            "mode": "self_owned"
        })
        assert res.status_code == 200
        data = res.json()
        assert "moments" in data
        assert "mashups" in data
        assert len(data["mashups"]) == 1
        assert data["mashups"][0]["title"] == "Test Mashup"

    # 2. Kiểm tra /api/repurposer/render-mashup validation
    render_req = {
        "source": __file__,
        "clips": [
            {"clip_id": 1, "role": "HOOK", "start_seconds": 0.0, "end_seconds": 3.0, "zoom_punch": 1.15},
            {"clip_id": 2, "role": "SETUP", "start_seconds": 5.0, "end_seconds": 10.0, "zoom_punch": 1.0},
        ],
        "title": "API Test Mashup",
        "niche_id": "general",
        "layout_mode": "smart_crop_full",
        "crop_focus": "center",
        "audio_mode": "original",
        "add_subtitles": False,
        "enable_sfx": True,
        "bgm_mood": "suspense"
    }

    mock_render_result = {
        "success": True,
        "output_path": "fake/path/short_mashup_123.mp4",
        "relative_url": "/media/repurposer/clips/short_mashup_123.mp4",
        "filename": "short_mashup_123.mp4",
        "duration": 8.0,
        "clips_count": 2,
    }

    with patch("app.services.video_repurposer.VideoRepurposerService.render_mashup_short", return_value=mock_render_result):
        res = client.post("/api/repurposer/render-mashup", json=render_req)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["filename"] == "short_mashup_123.mp4"
        assert data["clips_count"] == 2


@patch("app.services.video_repurposer.probe_file", return_value={"streams": [{"codec_type": "audio"}]})
@patch("app.services.video_repurposer.preflight_repurpose_qa", return_value={"passed": True, "score": 98})
@patch("app.services.video_repurposer.run_ffmpeg")
@patch("app.utils.ffmpeg_helper.mix_sfx_transitions")
def test_render_mashup_short_ai_voiceover(mock_mix_sfx, mock_run_ffmpeg, mock_qa, mock_probe, repurposer, tmp_path):
    """Kiểm tra chế độ audio_mode='ai_voiceover' trong render_mashup_short sinh giọng đọc và mix audio."""
    fake_source = tmp_path / "fake_input.mp4"
    fake_source.write_bytes(b"dummy_mp4_bytes")

    clips_data = [
        {
            "clip_id": 1,
            "role": "HOOK",
            "start_seconds": 10.0,
            "end_seconds": 14.0,
            "duration": 4.0,
            "zoom_punch": 1.25,
            "transcript_snippet": "A tiny puppy was found buried alive!"
        },
        {
            "clip_id": 2,
            "role": "CLIMAX",
            "start_seconds": 80.0,
            "end_seconds": 86.0,
            "duration": 6.0,
            "zoom_punch": 1.20,
            "transcript_snippet": "Finally pulled out into safety and love!"
        },
    ]

    out_name = f"test_mashup_vo_{tmp_path.name}.mp4"
    out_file = CLIPS_DIR / out_name
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_bytes(b"fake_output_mashup_mp4_content_ai_voice")

    def fake_synthesize(text, output_path, niche_id="general", voice_override=None):
        Path(output_path).write_bytes(b"dummy_mp3_data_1234567890" * 100)
        return True

    with patch.object(repurposer, "_synthesize_voiceover_sync", side_effect=fake_synthesize) as mock_synth, \
         patch("app.services.video_repurposer.get_duration_ms", return_value=4200):

        try:
            res = repurposer.render_mashup_short(
                source_video_path=str(fake_source),
                clips=clips_data,
                output_filename=out_name,
                niche_id="animal_rescue",
                audio_mode="ai_voiceover",
                add_subtitles=True,
                enable_sfx=False,
                title="Heartbreaking Puppy Rescue Miracle",
            )
            assert res["success"] is True
            # Kiểm tra hàm tạo TTS được gọi cho cả 2 clip
            assert mock_synth.call_count == 2

            # Kiểm tra câu lệnh FFmpeg có chứa input voice MP3 và filter mix
            ffmpeg_calls = mock_run_ffmpeg.call_args_list
            voice_mixed = any(
                any("temp_" in arg and "voice" in arg for arg in call[0][0])
                for call in ffmpeg_calls
            )
            assert voice_mixed, "FFmpeg bắt buộc phải nhận voiceover mp3 làm input cho từng clip"

            meta_file = CLIPS_DIR / f"{Path(out_name).stem}.meta.json"
            assert meta_file.exists()
            with open(meta_file, "r", encoding="utf-8") as mf:
                meta = json.load(mf)
                assert meta["audio_mode"] == "ai_voiceover"

        finally:
            if out_file.exists():
                out_file.unlink()
            meta_path = CLIPS_DIR / f"{Path(out_name).stem}.meta.json"
            if meta_path.exists():
                meta_path.unlink()

