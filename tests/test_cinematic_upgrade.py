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

    print("\n🚀 ALL CINEMATIC TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    test_all()
