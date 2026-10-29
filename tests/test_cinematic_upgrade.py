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
    cue1 = resolve_smart_sfx_cue("This is a shocking secret revealed", 1, 5)
    assert cue1 in ("impact_sub", "whoosh_fast", "none"), f"Unexpected SFX cue: {cue1}"
    print(f"Smart SFX assertion PASSED ({cue1})!")

    print("\n--- Test 3: Hypnotic Subtitle Power Words ---")
    scs = [SceneContract(scene_id=1, narration="ĐỪNG BAO GIỜ tin vào điều này.", visual_keyword="test", estimated_seconds=3)]
    ass = SubtitleHelper.build_ass_content(scs, niche_id="psychology")
    assert (r"\1c&H002222FF&" in ass or r"\1c&H000000FF&" in ass), "Crimson red power color missing in ASS dialogue!"
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

    print("\n--- Test 5: Color Grading Filter Verification ---")
    res_grain = get_niche_color_grading("psychology")
    assert "unsharp=" in res_grain["vf_filter"], "Color grading unsharp filter missing!"
    print("Color Grading in FFmpeg PASSED!")

    print("\n--- Test 6: Safe-Zone Clean Subtitles & Zero Burned-in CTA ---")
    scs_badge = [
        SceneContract(scene_id=1, narration="ĐỪNG BAO GIỜ tin vào điều này.", visual_keyword="test", estimated_seconds=3, audio_path="a1.mp3", audio_duration_ms=3000),
        SceneContract(scene_id=2, narration="Đây là bằng chứng xác thực số 1.", visual_keyword="test2", estimated_seconds=3, audio_path="a2.mp3", audio_duration_ms=3000)
    ]
    ass_badge = SubtitleHelper.build_ass_content(scs_badge, niche_id="psychology", show_cta_badge=True)
    assert "CTABadge" not in ass_badge and "ĐĂNG KÝ" not in ass_badge, "Burned-in CTA must be completely removed!"
    assert ",2,115,115,420,1" in ass_badge, "Safe-zone MarginV=420 and MarginL/R=115 missing!"
    print("Safe-Zone Clean Subtitles & Zero Burned-in CTA PASSED!")

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
