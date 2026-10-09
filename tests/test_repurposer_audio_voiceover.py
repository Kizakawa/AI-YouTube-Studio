import os
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from fastapi.testclient import TestClient

from app.services.video_repurposer import VideoRepurposerService
from app.utils.subtitle_helper import SubtitleHelper
from app.web.server import app, RepurposeRenderRequest, RepurposeMashupRenderRequest


@pytest.fixture
def repurposer():
    return VideoRepurposerService()


def test_repurpose_render_request_new_fields():
    """Kiểm tra Pydantic model RepurposeRenderRequest chấp nhận các trường âm thanh & thuyết minh mới."""
    req = RepurposeRenderRequest(
        source="test.mp4",
        start_seconds=10.0,
        end_seconds=25.0,
        audio_mode="mute_original_voiceover",
        mute_original=True,
        voice_id="vi-VN-NamMinhNeural",
        custom_voiceover_text="Đây là lời thuyết minh do mình tự sinh ra hoàn toàn mới.",
        bgm_mood="chill",
    )
    assert req.audio_mode == "mute_original_voiceover"
    assert req.mute_original is True
    assert req.voice_id == "vi-VN-NamMinhNeural"
    assert "tự sinh ra" in req.custom_voiceover_text
    assert req.bgm_mood == "chill"


def test_repurpose_mashup_render_request_new_fields():
    """Kiểm tra RepurposeMashupRenderRequest chấp nhận mute_original và voice_id."""
    req = RepurposeMashupRenderRequest(
        source="test.mp4",
        clips=[
            {"start_seconds": 0.0, "end_seconds": 5.0, "role": "HOOK"},
            {"start_seconds": 10.0, "end_seconds": 16.0, "role": "CLIMAX"},
        ],
        audio_mode="mute_original_voiceover",
        mute_original=True,
        voice_id="en-US-AndrewNeural",
    )
    assert req.audio_mode == "mute_original_voiceover"
    assert req.mute_original is True
    assert req.voice_id == "en-US-AndrewNeural"


def test_render_repurposed_short_mute_original_and_voiceover(repurposer, tmp_path):
    """
    Kiểm tra chế độ tắt hoàn toàn âm thanh video cũ + lồng giọng đọc mới:
    - [0:a] (âm thanh video gốc) phải bị triệt tiêu 100%, không được xuất hiện trong filter mix.
    - [1:a] (voiceover MP3 mới) được dùng làm luồng âm thanh chính.
    - Phụ đề Kinetic ASS phải được tạo từ kịch bản mới, không dùng timed_transcript của video cũ.
    """
    dummy_video = tmp_path / "source.mp4"
    dummy_video.write_bytes(b"dummy")

    captured_ffmpeg_args = []

    def mock_run_ffmpeg(cmd, **kwargs):
        captured_ffmpeg_args.append(cmd)
        # Tạo file output giả để hàm không báo lỗi
        out_f = Path(cmd[-1])
        out_f.parent.mkdir(parents=True, exist_ok=True)
        out_f.write_bytes(b"rendered_mp4_bytes")
        return MagicMock(returncode=0)

    fake_voice_path = tmp_path / "fake_voice.mp3"
    fake_voice_path.write_bytes(b"fake_mp3_data_larger_than_800_bytes" * 50)

    with patch("app.services.video_repurposer.run_ffmpeg", side_effect=mock_run_ffmpeg), \
         patch("app.services.video_repurposer.detect_audio_speech_start", return_value=0.0), \
         patch("app.services.video_repurposer.probe_file", return_value={"streams": [{"codec_type": "audio"}]}), \
         patch("app.services.video_repurposer.get_duration_ms", return_value=12000), \
         patch.object(repurposer, "_synthesize_voiceover_sync") as mock_synth:

        def fake_synth(text, output_path, niche_id="general", voice_override=None):
            Path(output_path).write_bytes(b"fake_voice_audio_bytes" * 80)
            return True

        mock_synth.side_effect = fake_synth

        old_timed_transcript = [
            {"start": 10.0, "end": 14.0, "text": "OLD ENGLISH SUBTITLES SHOULD NOT APPEAR"}
        ]
        new_voice_script = "This helpless little puppy was finally rescued after days in the cold."

        result = repurposer.render_repurposed_short(
            source_video_path=str(dummy_video),
            start_seconds=10.0,
            end_seconds=22.0,
            output_filename="test_mute_voiceover.mp4",
            audio_mode="mute_original_voiceover",
            mute_original=True,
            voice_id="en-US-AndrewNeural",
            custom_voiceover_text=new_voice_script,
            timed_transcript=old_timed_transcript,
            add_subtitles=True,
            niche_id="animal_rescue",
        )

        assert result["success"] is True
        assert len(captured_ffmpeg_args) == 1
        cmd = captured_ffmpeg_args[0]
        cmd_str = " ".join(cmd)

        # 1. Âm thanh gốc [0:a] KHÔNG được mix vào [a]
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        assert "[0:a]" not in filter_complex, "Âm thanh gốc [0:a] phải bị tắt 100%!"

        # 2. Luồng giọng đọc mới [1:a] được kết nối vào [a]
        assert "[1:a]" in filter_complex
        assert "[a]" in cmd

        # 3. Giọng đọc được gọi với đúng text và voice_override
        mock_synth.assert_called_once()
        synth_args = mock_synth.call_args[1]
        assert synth_args["text"] == new_voice_script
        assert synth_args["voice_override"] == "en-US-AndrewNeural"

        # 4. Phụ đề ASS chứa kịch bản giọng đọc mới
        assert "ass=" in filter_complex
        import re
        m_ass = re.search(r"ass='([^']+)'", filter_complex)
        assert m_ass is not None
        ass_clean_path = m_ass.group(1).replace("\\:", ":")
        if os.path.exists(ass_clean_path):
            ass_content = Path(ass_clean_path).read_text(encoding="utf-8")
            assert "HELPLESS LITTLE PUPPY" in ass_content.upper()
            assert "OLD ENGLISH SUBTITLES" not in ass_content


def test_render_repurposed_short_mute_mode(repurposer, tmp_path):
    """Kiểm tra audio_mode='mute' tạo video câm hoàn toàn (-an, không có luồng audio)."""
    dummy_video = tmp_path / "source.mp4"
    dummy_video.write_bytes(b"dummy")

    captured_ffmpeg_args = []

    def mock_run_ffmpeg(cmd, **kwargs):
        captured_ffmpeg_args.append(cmd)
        out_f = Path(cmd[-1])
        out_f.parent.mkdir(parents=True, exist_ok=True)
        out_f.write_bytes(b"rendered_mp4_bytes")
        return MagicMock(returncode=0)

    with patch("app.services.video_repurposer.run_ffmpeg", side_effect=mock_run_ffmpeg), \
         patch("app.services.video_repurposer.detect_audio_speech_start", return_value=0.0), \
         patch("app.services.video_repurposer.probe_file", return_value={"streams": [{"codec_type": "audio"}]}):

        result = repurposer.render_repurposed_short(
            source_video_path=str(dummy_video),
            start_seconds=5.0,
            end_seconds=15.0,
            output_filename="test_pure_mute.mp4",
            audio_mode="mute",
            add_subtitles=False,
        )

        assert result["success"] is True
        cmd = captured_ffmpeg_args[0]
        assert "-an" in cmd, "Chế độ mute phải có cờ -an để tắt hoàn toàn âm thanh!"
        assert "[a]" not in cmd


def test_render_repurposed_short_mute_original_with_bgm(repurposer, tmp_path):
    """Kiểm tra tắt âm thanh cũ + Lồng giọng đọc AI + Đệm nhạc nền BGM (chill/suspense)."""
    dummy_video = tmp_path / "source.mp4"
    dummy_video.write_bytes(b"dummy")

    captured_ffmpeg_args = []

    def mock_run_ffmpeg(cmd, **kwargs):
        captured_ffmpeg_args.append(cmd)
        out_f = Path(cmd[-1])
        out_f.parent.mkdir(parents=True, exist_ok=True)
        out_f.write_bytes(b"rendered_mp4_bytes")
        return MagicMock(returncode=0)

    with patch("app.services.video_repurposer.run_ffmpeg", side_effect=mock_run_ffmpeg), \
         patch("app.services.video_repurposer.detect_audio_speech_start", return_value=0.0), \
         patch("app.services.video_repurposer.probe_file", return_value={"streams": [{"codec_type": "audio"}]}), \
         patch("app.services.video_repurposer.get_duration_ms", return_value=10000), \
         patch.object(repurposer, "_synthesize_voiceover_sync") as mock_synth:

        def fake_synth(text, output_path, niche_id="general", voice_override=None):
            Path(output_path).write_bytes(b"fake_voice_audio_bytes" * 80)
            return True

        mock_synth.side_effect = fake_synth

        result = repurposer.render_repurposed_short(
            source_video_path=str(dummy_video),
            start_seconds=0.0,
            end_seconds=10.0,
            output_filename="test_voice_bgm.mp4",
            audio_mode="mute_original_voiceover",
            mute_original=True,
            voice_id="en-US-AndrewNeural",
            custom_voiceover_text="Deep in the frozen wilderness, an impossible miracle unfolded.",
            bgm_mood="chill",
            add_subtitles=True,
        )

        assert result["success"] is True
        cmd = captured_ffmpeg_args[0]
        filter_complex = cmd[cmd.index("-filter_complex") + 1]

        # Kiểm tra mix giữa [voice] và [bgm]
        assert "[voice][bgm]amix=inputs=2" in filter_complex
        assert "equalizer=f=3200" in filter_complex
        # Âm thanh gốc [0:a] tuyệt đối không có mặt
        assert "[0:a]" not in filter_complex


def test_render_mashup_short_mute_original(repurposer, tmp_path):
    """Kiểm tra chế độ Mashup Multi-Clip khi bật tắt tiếng gốc: từng clip con không lấy [0:a]."""
    dummy_video = tmp_path / "source.mp4"
    dummy_video.write_bytes(b"dummy")

    captured_cmds = []

    def mock_run_ffmpeg(cmd, **kwargs):
        captured_cmds.append(cmd)
        out_f = Path(cmd[-1])
        out_f.parent.mkdir(parents=True, exist_ok=True)
        out_f.write_bytes(b"rendered_mp4_bytes")
        return MagicMock(returncode=0)

    clips = [
        {"start_seconds": 1.0, "end_seconds": 4.0, "role": "HOOK", "summary": "A stranded puppy crying"},
        {"start_seconds": 10.0, "end_seconds": 14.0, "role": "CLIMAX", "summary": "The dramatic rescue moment"},
    ]

    with patch("app.services.video_repurposer.run_ffmpeg", side_effect=mock_run_ffmpeg), \
         patch("app.services.video_repurposer.probe_file", return_value={"streams": [{"codec_type": "audio"}]}), \
         patch("app.services.video_repurposer.get_duration_ms", return_value=3000), \
         patch("app.utils.ffmpeg_helper.mix_sfx_transitions") as mock_sfx, \
         patch.object(repurposer, "_synthesize_voiceover_sync") as mock_synth:

        def fake_synth(text, output_path, niche_id="general", voice_override=None):
            Path(output_path).write_bytes(b"fake_voice_audio_bytes" * 80)
            return True

        mock_synth.side_effect = fake_synth

        result = repurposer.render_mashup_short(
            source_video_path=str(dummy_video),
            clips=clips,
            output_filename="test_mashup_mute.mp4",
            audio_mode="mute_original_voiceover",
            mute_original=True,
            voice_id="vi-VN-NamMinhNeural",
            bgm_mood="suspense",
            add_subtitles=True,
        )

        assert result["success"] is True
        # Kiểm tra các lệnh cắt clip con (tên file dạng _c0.mp4, _c1.mp4)
        subclip_cmds = [c for c in captured_cmds if any(c[-1].endswith(f"_c{i}.mp4") for i in range(len(clips)))]
        assert len(subclip_cmds) == 2
        for sc in subclip_cmds:
            fc = sc[sc.index("-filter_complex") + 1]
            assert "[0:a]" not in fc, "Clip con không được chứa âm thanh gốc [0:a] khi mute_original=True!"
            assert "[1:a]" in fc, "Clip con phải lấy âm thanh thuyết minh [1:a]!"


def test_api_render_clip_endpoint_receives_voiceover_options(tmp_path):
    """Kiểm tra API /api/repurposer/render-clip nhận đầy đủ các tham số mới từ UI."""
    client = TestClient(app)

    dummy_video = tmp_path / "api_test.mp4"
    dummy_video.write_bytes(b"dummy")

    with patch("app.services.video_repurposer.repurposer_service.render_repurposed_short") as mock_render:
        mock_render.return_value = {
            "success": True,
            "filename": "short_test.mp4",
            "relative_url": "/media/repurposer/clips/short_test.mp4",
        }

        payload = {
            "source": str(dummy_video),
            "start_seconds": 12.0,
            "end_seconds": 24.0,
            "title": "Test Title",
            "niche_id": "animal_rescue",
            "audio_mode": "mute_original_voiceover",
            "mute_original": True,
            "voice_id": "vi-VN-NamMinhNeural",
            "custom_voiceover_text": "Kịch bản tự nhập của người dùng",
            "bgm_mood": "chill",
            "add_subtitles": True,
        }

        res = client.post("/api/repurposer/render-clip", json=payload)
        assert res.status_code == 200
        mock_render.assert_called_once()
        kwargs = mock_render.call_args[1]
        assert kwargs["audio_mode"] == "mute_original_voiceover"
        assert kwargs["mute_original"] is True
        assert kwargs["voice_id"] == "vi-VN-NamMinhNeural"
        assert kwargs["custom_voiceover_text"] == "Kịch bản tự nhập của người dùng"
        assert kwargs["bgm_mood"] == "chill"


def test_strip_sandwich_square_and_watermark_blur(tmp_path):
    """Kiểm tra chế độ bóc tách 1:1 (strip_sandwich_square) và xóa watermark logo góc phải."""
    repurposer = VideoRepurposerService()
    dummy_video = tmp_path / "sandwich_video.mp4"
    dummy_video.write_bytes(b"dummy_video_content")

    captured_cmds = []

    def mock_run_ffmpeg(cmd, **kwargs):
        captured_cmds.append(cmd)
        out_f = Path(cmd[-1])
        out_f.parent.mkdir(parents=True, exist_ok=True)
        out_f.write_bytes(b"rendered_short_square_bytes")
        return MagicMock(returncode=0)

    def fake_synth(text, output_path, niche_id="general", voice_override=None):
        Path(output_path).write_bytes(b"fake_voice_audio_bytes" * 80)
        return True

    with patch("app.services.video_repurposer.run_ffmpeg", side_effect=mock_run_ffmpeg), \
         patch("app.services.video_repurposer.probe_file", return_value={"streams": [{"codec_type": "audio"}]}), \
         patch.object(repurposer, "_synthesize_voiceover_sync", side_effect=fake_synth):

        res = repurposer.render_repurposed_short(
            source_video_path=str(dummy_video),
            start_seconds=5.0,
            end_seconds=15.0,
            output_filename="test_strip_square.mp4",
            layout_mode="strip_sandwich_square",
            blur_watermark=True,
            watermark_position="top_right",
            audio_mode="mute_original_voiceover",
            mute_original=True,
            add_subtitles=True,
        )

        assert res["success"] is True
        render_cmd = captured_cmds[-1]
        fc = render_cmd[render_cmd.index("-filter_complex") + 1]

        # 1. Khung hình phải có bộ lọc bóc tách sạch lõi (loại bỏ hoàn toàn banner trên và phụ đề dưới)
        assert "crop=in_w:in_h*0.51:0:in_h*0.205" in fc
        assert "scale=1080:1080" in fc

        # 2. Khung hình phải có bộ lọc làm mờ watermark góc phải với tọa độ W và H hợp lệ trong FFmpeg
        assert "boxblur=18:5" in fc
        assert "overlay=W*0.60:H*0.02" in fc

        # 3. Âm thanh gốc bị tắt hoàn toàn
        assert "[0:a]" not in fc


def test_ensure_english_narration_vietnamese_conversion():
    """Kiểm tra hàm _ensure_english_narration tự động chuyển đổi tiếng Việt sang tiếng Anh chuẩn viral."""
    repurposer = VideoRepurposerService()

    # Trường hợp 1: Văn bản tiếng Anh có sẵn -> Giữ nguyên
    en_input = "Watch this hero save a freezing kitten from the bitter cold!"
    res_en = repurposer._ensure_english_narration(en_input, niche_id="animal_rescue")
    assert res_en == en_input

    # Trường hợp 2: Văn bản tiếng Việt -> Tự động chuyển ngữ sang tiếng Anh (mock ContentAgent)
    vn_input = "Chàng trai cứu chú mèo con bị cứng đơ trong giá rét"
    with patch("app.agents.content_agent.ContentAgent._call_api", return_value="A brave rescuer saves a frozen kitten in the bitter cold"):
        res_vn = repurposer._ensure_english_narration(vn_input, niche_id="animal_rescue")
        assert "saves a frozen kitten" in res_vn
        from app.agents.voice_agent import VoiceAgent
        assert not VoiceAgent._is_vietnamese(res_vn)


def test_api_repurposer_translate_endpoint():
    """Kiểm tra API /api/repurposer/translate-text dịch kịch bản sang tiếng Anh."""
    client = TestClient(app)
    with patch("app.agents.content_agent.ContentAgent._call_api", return_value="Hero rescuer saves a frozen kitten!"):
        res = client.post("/api/repurposer/translate-text", json={
            "text": "Chàng trai cứu chú mèo bị cứng đơ",
            "niche_id": "animal_rescue"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "Hero rescuer saves" in data["english_text"]

