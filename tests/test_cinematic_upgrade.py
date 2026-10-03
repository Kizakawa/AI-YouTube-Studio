"""Test script for 100% Free Level 3/4 Cinematic Visual & Sound Upgrades."""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.utils.ffmpeg_helper import get_niche_color_grading, resolve_smart_sfx_cue
from app.utils.subtitle_helper import SubtitleHelper, POWER_WORDS
from app.schemas.contracts import RenderJobContract, SceneContract

def test_all():
    print("--- Test 1: Niche Color Grading ---")
    for n in ["psychology", "deep_sea_earth_mysteries", "wildlife_predators", "animal_rescue", "history_mystery"]:
        res = get_niche_color_grading(n)
        assert "vf_filter" in res and "vignette" in res
        print(f"Niche [{n}] -> {res['niche_key']}: {res['vf_filter'][:35]}... {res['vignette']}")

    print("\n--- Test 2: Smart Sound Design SFX ---")
    # "secret revealed" -> reveal
    cue1 = resolve_smart_sfx_cue("This is a shocking secret revealed", 1, 5)
    assert cue1 == "reveal", f"Expected reveal, got {cue1}"

    # "tấn công đẫm máu" -> impact_sub
    cue2 = resolve_smart_sfx_cue("Con thú dữ tấn công đẫm máu", 2, 5)
    assert cue2 == "impact_sub", f"Expected impact_sub, got {cue2}"

    # "thao túng giăng bẫy" -> glitch
    cue3 = resolve_smart_sfx_cue("Kẻ thao túng đang giăng bẫy bạn", 3, 5)
    assert cue3 == "glitch", f"Expected glitch, got {cue3}"

    # "vực thẳm bóng tối" -> sub_drop
    cue4 = resolve_smart_sfx_cue("Rơi vào vực thẳm bóng tối sâu thẳm", 1, 5)
    assert cue4 == "sub_drop", f"Expected sub_drop, got {cue4}"

    # "cao trào trước cảnh cuối" -> riser
    cue5 = resolve_smart_sfx_cue("Và điều then chốt trước khi quá muộn", 3, 5)
    assert cue5 == "riser", f"Expected riser, got {cue5}"
    print("All Smart SFX assertions PASSED!")

    print("\n--- Test 3: Hypnotic Subtitle Power Words ---")
    scs = [SceneContract(scene_id=1, narration="ĐỪNG BAO GIỜ tin vào điều này.", visual_keyword="test", estimated_seconds=3)]
    ass = SubtitleHelper.build_ass_content(scs, niche_id="psychology")
    assert r"\1c&H000000FF&" in ass, "Crimson red power color missing in ASS dialogue!"
    print("Subtitle Power Word ASS Generation PASSED!")

    print("\n--- Test 4: RenderJobContract with niche ---")
    job = RenderJobContract(
        job_id=101,
        title="Test Cinematic Video",
        scenes=[SceneContract(scene_id=1, narration="Hello", visual_keyword="hi", estimated_seconds=2, audio_path="audio_001.mp3", media_path="clip_001.mp4", audio_duration_ms=2000)],
        full_audio_path="test.mp3",
        total_audio_duration_ms=2000,
        output_path="out.mp4",
        niche="psychology"
    )
    assert job.niche == "psychology"
    print("RenderJobContract niche serialization PASSED!")

    print("\n--- Test 5: Level 4 35mm Film Grain ---")
    res_grain = get_niche_color_grading("psychology")
    assert "noise=c0s=7:c0f=t+u" in res_grain["vf_filter"], "35mm film grain missing!"
    print("35mm Film Grain in Color Grading PASSED!")

    print("\n--- Test 6: Level 4 Pattern Interrupt Badges ---")
    scs_badge = [
        SceneContract(scene_id=1, narration="ĐỪNG BAO GIỜ tin vào điều này.", visual_keyword="test", estimated_seconds=3, audio_path="a1.mp3", audio_duration_ms=3000),
        SceneContract(scene_id=2, narration="Đây là bằng chứng xác thực số 1.", visual_keyword="test2", estimated_seconds=3, audio_path="a2.mp3", audio_duration_ms=3000)
    ]
    ass_badge = SubtitleHelper.build_ass_content(scs_badge, niche_id="psychology")
    assert "PatternInterruptBadge" in ass_badge, "PatternInterruptBadge style missing!"
    assert "[ 🧠 TÂM LÝ ĐEN TỐI ]" in ass_badge, "Hook Investigative Badge missing!"
    assert "[ 🔍 BẰNG CHỨNG #2 ]" in ass_badge, "Mid-scene Evidence Badge missing!"
    print("Pattern Interrupt Investigative Badges PASSED!")

    print("\n--- Test 7: Level 4 Multi-Layer Micro-SFX Extraction ---")
    scs_sfx = [
        SceneContract(scene_id=1, narration="ĐỪNG BAO GIỜ tin vào bí mật này.", visual_keyword="test", estimated_seconds=3, audio_path="a1.mp3", audio_duration_ms=3000),
        SceneContract(scene_id=2, narration="Đây là nguy hiểm chết người đang rình rập.", visual_keyword="test2", estimated_seconds=3, audio_path="a2.mp3", audio_duration_ms=3000)
    ]
    cues = SubtitleHelper.extract_power_word_cues(scs_sfx, min_interval_s=2.0)
    assert len(cues) >= 2, f"Expected at least 2 micro SFX cues, got {len(cues)}"
    print(f"Extracted {len(cues)} Micro-SFX Cues: {cues}")
    print("Multi-Layer Micro-SFX Extraction PASSED!")

    print("\n🚀 ALL LEVEL 4 CINEMATIC TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_all()
