"""
Test Suite for VoiceStudio-Inspired Timed Speech Sync, Dynamic Sidechain Ducking,
and US Voice Persona Registry.
Verifies:
  1. US Export Mode: Vietnamese voice completely disabled / converted to US neural voices.
  2. US Voice Persona Registry: Cast archetype and niche mapping.
  3. FFmpeg build_atempo_filter: Millisecond-accurate tempo calculation and filter chaining.
  4. VideoRepurposer: ai_voiceover integration with Timed Speech Sync and sidechaincompress.
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path

from app.agents.voice_agent import VoiceAgent
from app.utils.ffmpeg_helper import build_atempo_filter
from app.services.video_repurposer import VideoRepurposerService, CLIPS_DIR


def test_disable_vietnamese_voice_flag():
    """Kiểm tra cờ DISABLE_VIETNAMESE_VOICE triệt tiêu hoàn toàn giọng Việt khi xuất sang Hoa Kỳ."""
    agent = VoiceAgent(default_voice="en-US-AndrewNeural")
    assert agent.DISABLE_VIETNAMESE_VOICE is True

    # 1. Yêu cầu giọng Việt trực tiếp -> Phải tự chuyển sang giọng Mỹ chuẩn
    resolved = agent._resolve_safe_voice("Văn bản tiếng Việt kiểm tra", requested_voice="vi-VN-NamMinhNeural")
    assert resolved == "en-US-AndrewNeural"
    assert not resolved.startswith("vi-")

    # 2. Text tiếng Việt nhưng không truyền voice -> Vẫn phải ra giọng Mỹ chuẩn
    resolved_vn_text = agent._resolve_safe_voice("Chào mừng các bạn đến với video ngày hôm nay")
    assert resolved_vn_text.startswith("en-US-")
    assert not resolved_vn_text.startswith("vi-")

    # 3. Text tiếng Anh -> Ra giọng Mỹ chuẩn
    resolved_en = agent._resolve_safe_voice("Welcome back to the channel, today we explore space")
    assert resolved_en == "en-US-AndrewNeural"


def test_us_voice_persona_registry_archetypes():
    """Kiểm tra ánh xạ Cast Archetype sang US Voice Persona."""
    # 1. Nhân vật lịch sử / Triết gia (Marcus, Tesla, Einstein, Caesar) -> Deep Baritone Eric
    assert VoiceAgent.get_persona_voice(archetype="Nikola Tesla") == "en-US-EricNeural"
    assert VoiceAgent.get_persona_voice(archetype="Albert Einstein") == "en-US-EricNeural"
    assert VoiceAgent.get_persona_voice(archetype="Marcus Aurelius") == "en-US-EricNeural"
    assert VoiceAgent.get_persona_voice(archetype="Julius Caesar") == "en-US-EricNeural"

    # 2. Thám tử / Noir / Điều tra -> Guy
    assert VoiceAgent.get_persona_voice(archetype="The Detective") == "en-US-GuyNeural"

    # 3. Công nghệ / Hacker / Netrunner / Executive -> Christopher
    assert VoiceAgent.get_persona_voice(archetype="The Netrunner") == "en-US-ChristopherNeural"
    assert VoiceAgent.get_persona_voice(archetype="The Executive") == "en-US-ChristopherNeural"

    # 4. Phụ nữ / Cô gái trẻ / Nữ hoàng -> Jenny
    assert VoiceAgent.get_persona_voice(archetype="Young Woman") == "en-US-JennyNeural"
    assert VoiceAgent.get_persona_voice(archetype="Cleopatra") == "en-US-JennyNeural"


def test_us_voice_persona_registry_niches():
    """Kiểm tra ánh xạ Niche Profile sang US Voice Persona."""
    # 1. SciFi & Future Tech -> Christopher
    assert VoiceAgent.get_persona_voice(niche_id="scifi_future_tech") == "en-US-ChristopherNeural"

    # 2. Historical & Deep Sea -> Eric
    assert VoiceAgent.get_persona_voice(niche_id="historical_figures") == "en-US-EricNeural"
    assert VoiceAgent.get_persona_voice(niche_id="deep_sea_mysteries") == "en-US-EricNeural"

    # 3. Animal Rescue (Dialogue vs Narration)
    assert VoiceAgent.get_persona_voice(niche_id="animal_rescue", is_dialogue=True) == "en-US-JennyNeural"
    assert VoiceAgent.get_persona_voice(niche_id="animal_rescue", is_dialogue=False) == "en-US-BrianNeural"

    # 4. Fallback Default -> Andrew
    assert VoiceAgent.get_persona_voice(niche_id="general") == "en-US-AndrewNeural"


def test_build_atempo_filter_precision_and_chaining():
    """Kiểm tra hàm build_atempo_filter co giãn tốc độ chuẩn xác và ghép chuỗi chaining khi vượt ngưỡng."""
    # 1. Tỷ lệ 1.0 (không đổi) -> anull
    assert build_atempo_filter(1.0) == "anull"
    assert build_atempo_filter(1.01) == "anull"  # Sai số < 2% bỏ qua

    # 2. Tốc độ thường (0.5 <= ratio <= 2.0)
    assert build_atempo_filter(1.15) == "atempo=1.1500"
    assert build_atempo_filter(0.85) == "atempo=0.8500"

    # 3. Nén tốc độ > 2.0x -> Ghép chuỗi chaining
    # Ví dụ: 2.4x -> atempo=2.0,atempo=1.2000
    filter_fast = build_atempo_filter(2.4)
    assert "atempo=2.0" in filter_fast
    assert "atempo=1.2" in filter_fast

    # 4. Kéo giãn tốc độ < 0.5x -> Ghép chuỗi chaining
    # Ví dụ: 0.4x -> atempo=0.5,atempo=0.8000
    filter_slow = build_atempo_filter(0.4)
    assert "atempo=0.5" in filter_slow
    assert "atempo=0.8" in filter_slow

    # 5. Giá trị không hợp lệ <= 0 -> anull
    assert build_atempo_filter(0.0) == "anull"
    assert build_atempo_filter(-1.5) == "anull"


def test_render_repurposed_short_ai_voiceover_dubbing_sync(tmp_path):
    """Kiểm tra VideoRepurposer tích hợp Timed Speech Sync và Dynamic Sidechain Ducking trong render_repurposed_short."""
    service = VideoRepurposerService()

    # Tạo file dummy video nguồn
    fake_video = str(tmp_path / "dummy_source.mp4")
    Path(fake_video).write_bytes(b"\x00" * 4096)

    captured_ffmpeg_cmd = []

    def fake_run_ffmpeg(cmd, timeout=300):
        captured_ffmpeg_cmd.append(cmd)
        # Giả lập tạo file output
        out_file = cmd[-1]
        Path(out_file).parent.mkdir(parents=True, exist_ok=True)
        Path(out_file).write_bytes(b"\x00" * 8192)
        return True

    def fake_probe(path):
        return {
            "format": {"duration": "30.0"},
            "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]
        }

    def fake_preflight_qa(*args, **kwargs):
        return {"passed": True, "score": 95, "checks": {}}

    async def fake_comm_save(out_path):
        Path(out_path).write_bytes(b"\x00" * 5000)

    mock_comm = MagicMock()
    mock_comm.save = AsyncMock(side_effect=fake_comm_save)

    with patch("os.path.exists", return_value=True), \
         patch("app.services.video_repurposer.probe_file", side_effect=fake_probe), \
         patch("app.services.video_repurposer.run_ffmpeg", side_effect=fake_run_ffmpeg), \
         patch("app.services.video_repurposer.preflight_repurpose_qa", side_effect=fake_preflight_qa), \
         patch("app.services.video_repurposer.detect_audio_speech_start", return_value=0.0), \
         patch("app.services.video_repurposer.get_duration_ms", return_value=32000), \
         patch("edge_tts.Communicate", return_value=mock_comm) as mock_edge_cls:

        res = service.render_repurposed_short(
            source_video_path=fake_video,
            start_seconds=0.0,
            end_seconds=28.0,  # 28s video clip
            output_filename="test_dubbed_short.mp4",
            niche_id="historical_figures",
            audio_mode="ai_voiceover",
            subtitle_text="Marcus Aurelius was one of the greatest Roman emperors.",
        )

        assert res is not None
        assert res["success"] is True

        # 1. Kiểm tra giọng được chọn là US Persona (Eric cho historical_figures)
        mock_edge_cls.assert_called_once()
        called_voice = mock_edge_cls.call_args[0][1]
        assert called_voice == "en-US-EricNeural"

        # 2. Kiểm tra câu lệnh FFmpeg có chứa bộ lọc sidechaincompress và atempo
        assert len(captured_ffmpeg_cmd) > 0
        cmd_args = captured_ffmpeg_cmd[0]
        cmd_str = " ".join(cmd_args)

        # Dynamic sidechain ducking filter with asplit=2
        assert "sidechaincompress=" in cmd_str
        assert "asplit=2" in cmd_str
        assert "v_sc" in cmd_str
        # Timed speech sync atempo filter (32.0s voice / 28.0s clip = 1.1429x)
        assert "atempo=" in cmd_str
