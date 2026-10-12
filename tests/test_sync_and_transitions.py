"""
Test Suite for Video & Subtitle Synchronization & Dynamic Cinematic Transitions.
"""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas.contracts import SceneContract, RenderJobContract
from app.utils.ffmpeg_helper import resolve_niche_transitions, NICHE_TRANSITIONS_MAP
from app.utils.subtitle_helper import SubtitleHelper
from app.agents.voice_agent import VoiceAgent

def test_dynamic_transitions():
    print("=== Test 1: Dynamic Transitions Resolution ===")
    for niche in ["scifi", "psychology", "deep_sea", "wildlife", "animal_rescue", "horror", "finance", "general"]:
        trans = resolve_niche_transitions(niche, count=6)
        print(f"Niche [{niche}] -> {trans}")
        assert len(trans) == 6, f"Expected 6 transitions, got {len(trans)}"
        # Phải là các chuyển cảnh phong phú, không được toàn bộ là 1 hiệu ứng duy nhất
        unique = set(trans)
        assert len(unique) >= 3, f"Expected diverse transitions for {niche}, got {unique}"
        # Tuyệt đối không được chỉ chứa fadeblack
        assert "fadeblack" not in unique or len(unique) > 1

    print("✅ Test 1 PASSED: Dynamic transitions are varied, engaging, and niche-tailored!\n")


def test_subtitle_word_timings_sync():
    print("=== Test 2: Subtitle Word-Timings Synchronization ===")
    # Mô phỏng word_timings thực tế từ Edge-TTS
    mock_word_timings_s1 = [
        {"word": "BÍ", "start_ms": 100, "end_ms": 300, "duration_ms": 200},
        {"word": "MẬT", "start_ms": 320, "end_ms": 550, "duration_ms": 230},
        {"word": "KINH", "start_ms": 600, "end_ms": 850, "duration_ms": 250},
        {"word": "HOÀNG", "start_ms": 870, "end_ms": 1200, "duration_ms": 330},
        {"word": "DƯỚI", "start_ms": 1300, "end_ms": 1500, "duration_ms": 200},
        {"word": "ĐÁY", "start_ms": 1520, "end_ms": 1750, "duration_ms": 230},
        {"word": "BIỂN.", "start_ms": 1780, "end_ms": 2100, "duration_ms": 320},
    ]

    mock_word_timings_s2 = [
        {"word": "NƠI", "start_ms": 80, "end_ms": 280, "duration_ms": 200},
        {"word": "ÁP", "start_ms": 300, "end_ms": 500, "duration_ms": 200},
        {"word": "LỰC", "start_ms": 520, "end_ms": 750, "duration_ms": 230},
        {"word": "NƯỚC", "start_ms": 780, "end_ms": 1050, "duration_ms": 270},
        {"word": "NGHIỀN", "start_ms": 1150, "end_ms": 1400, "duration_ms": 250},
        {"word": "NÁT", "start_ms": 1420, "end_ms": 1650, "duration_ms": 230},
        {"word": "TẤT", "start_ms": 1700, "end_ms": 1900, "duration_ms": 200},
        {"word": "CẢ.", "start_ms": 1920, "end_ms": 2300, "duration_ms": 380},
    ]

    scene1 = SceneContract(
        scene_id=1,
        narration="Bí mật kinh hoàng dưới đáy biển.",
        visual_keyword="deep sea mystery",
        audio_duration_ms=2400,
        estimated_seconds=2.4,
        word_timings=mock_word_timings_s1
    )

    scene2 = SceneContract(
        scene_id=2,
        narration="Nơi áp lực nước nghiền nát tất cả.",
        visual_keyword="ocean depth pressure",
        audio_duration_ms=2600,
        estimated_seconds=2.6,
        word_timings=mock_word_timings_s2
    )

    ass_content = SubtitleHelper.build_ass_content(
        scenes=[scene1, scene2],
        niche_id="deep_sea",
        hook_title="ĐẠI DƯƠNG ĐÁNG SỢ"
    )

    dialogues = [line for line in ass_content.splitlines() if line.startswith("Dialogue: 0,")]
    print(f"Generated {len(dialogues)} karaoke dialogue chunks:")
    for d in dialogues:
        print(f"  {d}")

    assert len(dialogues) >= 3, f"Expected at least 3 dialogue chunks, got {len(dialogues)}"
    
    # Kiểm tra Scene 1 bắt đầu ở 0:00:00.10 (100ms)
    assert "0:00:00.10" in dialogues[0], f"Expected Scene 1 first chunk start at 0:00:00.10, got {dialogues[0]}"

    # Kiểm tra Scene 2 bắt đầu sau scene 1 (current_offset_ms = 2400ms -> 2400 + 80 = 2480ms = 0:00:02.48)
    scene2_dialogues = [d for d in dialogues if "NƠI" in d]
    assert len(scene2_dialogues) > 0, "Scene 2 dialogue not found!"
    assert "0:00:02.48" in scene2_dialogues[0], f"Expected Scene 2 first chunk start at 0:00:02.48, got {scene2_dialogues[0]}"

    print("✅ Test 2 PASSED: Subtitles are locked to exact word timestamps without drifting!\n")


def test_voice_agent_parse_words():
    print("=== Test 3: VoiceAgent Word Metadata Parser ===")
    test_file = Path("test_words_mock.jsonl")
    test_file.write_text(
        '{"type": "WordBoundary", "offset": 1000000, "duration": 2500000, "text": "Khám"}\n'
        '{"type": "WordBoundary", "offset": 3500000, "duration": 3000000, "text": "phá"}\n',
        encoding="utf-8"
    )
    try:
        parsed = VoiceAgent._parse_words_jsonl(str(test_file))
        assert len(parsed) == 2
        assert parsed[0]["word"] == "Khám"
        assert parsed[0]["start_ms"] == 100
        assert parsed[0]["duration_ms"] == 250
        assert parsed[1]["word"] == "phá"
        assert parsed[1]["start_ms"] == 350
        assert parsed[1]["duration_ms"] == 300
        print(f"Parsed {len(parsed)} words correctly: {parsed}")
    finally:
        if test_file.exists():
            test_file.unlink()

    print("✅ Test 3 PASSED: Word metadata parser works accurately!\n")

if __name__ == "__main__":
    test_dynamic_transitions()
    test_subtitle_word_timings_sync()
    test_voice_agent_parse_words()
    print("🎉 ALL SYNCHRONIZATION & TRANSITION TESTS PASSED!")
