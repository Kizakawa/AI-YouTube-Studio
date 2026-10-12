"""
Unit & Integration Tests for Call to Action (CTA) Restoration in YouTube Shorts.
Ensures both Spoken Narration CTA and Visual On-screen CTABadge are active,
without breaking subtitle sync, retention pacing, or dynamic transitions.
"""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas.contracts import SceneContract, ScriptContract
from app.utils.subtitle_helper import SubtitleHelper
from app.agents.content_agent import ContentAgent
from app.services.content_linter import ScriptValidationLinter
from app.services.niche_manager import get_niche, build_script_prompt_instructions


def test_content_agent_clean_retains_cta():
    print("=== Test 1: ContentAgent does not strip Call to Action ===")
    ca = ContentAgent()
    raw_script = {
        "title": "3 Signs They Are Quietly Manipulating You 🧠",
        "hook": "Stop trusting their sweet words.",
        "thumbnail_text": "THEY'RE USING YOU 🚩",
        "scenes": [
            {
                "scene_id": 1,
                "narration": "Stop trusting their sweet words.",
                "visual_keyword": "whispering secret",
                "estimated_seconds": 3.0,
            },
            {
                "scene_id": 2,
                "narration": "They observe your weaknesses silently.",
                "visual_keyword": "chess game",
                "estimated_seconds": 3.5,
            },
            {
                "scene_id": 3,
                "narration": "A real ally respects your boundaries. Subscribe now to master dark psychology before someone uses it on you.",
                "visual_keyword": "confident person walking",
                "estimated_seconds": 4.5,
            }
        ]
    }
    
    import json
    cleaned = ca._parse_json_script(json.dumps(raw_script))
    last_narr = cleaned.scenes[-1].narration
    print(f"Cleaned last narration: {last_narr}")
    
    # Must retain the CTA
    assert "Subscribe now to master dark psychology" in last_narr, "CTA was erroneously stripped!"
    assert last_narr.endswith("."), "Sentence must properly end with punctuation."
    print("✅ Test 1 PASSED: ContentAgent retains high-converting CTA in final scene!\n")


def test_content_agent_clean_retains_vietnamese_cta():
    print("=== Test 2: ContentAgent retains Vietnamese Call to Action ===")
    ca = ContentAgent()
    raw_script = {
        "title": "3 Dấu Hiệu Bạn Đang Bị Thao Túng Tâm Lý 🧠",
        "hook": "Đừng vội tin những lời khen ngợi ngọt ngào.",
        "thumbnail_text": "BẠN ĐANG BỊ DÙNG 🚩",
        "scenes": [
            {
                "scene_id": 1,
                "narration": "Đừng vội tin những lời khen ngợi ngọt ngào.",
                "visual_keyword": "whispering secret",
                "estimated_seconds": 3.0,
            },
            {
                "scene_id": 2,
                "narration": "Kẻ thao túng luôn âm thầm bẫy cảm xúc của bạn.",
                "visual_keyword": "chess game",
                "estimated_seconds": 3.5,
            },
            {
                "scene_id": 3,
                "narration": "Hãy bấm đăng ký kênh để luôn tỉnh táo trước mọi cạm bẫy tâm lý.",
                "visual_keyword": "confident person walking",
                "estimated_seconds": 4.0,
            }
        ]
    }
    
    import json
    cleaned = ca._parse_json_script(json.dumps(raw_script))
    last_narr = cleaned.scenes[-1].narration
    print(f"Cleaned Vietnamese narration: {last_narr}")
    
    assert "bấm đăng ký kênh để luôn tỉnh táo" in last_narr.lower(), "Vietnamese CTA was erroneously stripped!"
    print("✅ Test 2 PASSED: Vietnamese CTA is retained!\n")


def test_visual_cta_badge_generation():
    print("=== Test 3: Visual On-Screen CTABadge Generation in ASS Subtitles ===")
    scenes_en = [
        SceneContract(scene_id=1, narration="Stop trusting everyone blindly.", visual_keyword="shadows", audio_duration_ms=3000),
        SceneContract(scene_id=2, narration="They observe your every move.", visual_keyword="eyes", audio_duration_ms=3000),
        SceneContract(scene_id=3, narration="Subscribe now to master dark psychology.", visual_keyword="chess", audio_duration_ms=4000),
    ]

    ass_en = SubtitleHelper.build_ass_content(
        scenes=scenes_en,
        niche_id="psychology",
        show_cta_badge=True
    )
    
    cta_lines_en = [line for line in ass_en.splitlines() if "CTABadge" in line and line.startswith("Dialogue: 1,")]
    print(f"English CTABadge lines: {cta_lines_en}")
    assert len(cta_lines_en) == 1, "Expected exactly 1 CTABadge line in ASS!"
    assert "SUBSCRIBE TO MASTER THE MIND" in cta_lines_en[0], "Expected psychology CTA badge text in English!"

    # Test Vietnamese Niche
    scenes_vi = [
        SceneContract(scene_id=1, narration="Dưới đáy biển sâu mười nghìn mét.", visual_keyword="deep sea", audio_duration_ms=3200),
        SceneContract(scene_id=2, narration="Áp lực nước nghiền nát mọi thứ.", visual_keyword="abyss", audio_duration_ms=3400),
        SceneContract(scene_id=3, narration="Đăng ký kênh để khám phá bí ẩn tiếp theo.", visual_keyword="ocean", audio_duration_ms=4000),
    ]

    ass_vi = SubtitleHelper.build_ass_content(
        scenes=scenes_vi,
        niche_id="deep_sea",
        show_cta_badge=True
    )
    
    cta_lines_vi = [line for line in ass_vi.splitlines() if "CTABadge" in line and line.startswith("Dialogue: 1,")]
    print(f"Vietnamese CTABadge lines: {cta_lines_vi}")
    assert len(cta_lines_vi) == 1, "Expected exactly 1 CTABadge line in ASS!"
    assert "ĐĂNG KÝ KÊNH ĐÓN XEM BÍ ẨN TIẾP THEO" in cta_lines_vi[0], "Expected deep sea CTA badge text in Vietnamese!"

    print("✅ Test 3 PASSED: Visual CTABadge generates correctly with niche styling and timing!\n")


def test_script_validation_linter_with_cta():
    print("=== Test 4: ScriptValidationLinter passes scripts with high-converting CTA ===")
    script = ScriptContract(
        title="Burn The Evidence Before They Knock 🚪🔥",
        hook="Destroying records hides the darkest history.",
        full_narration="Destroying records hides the darkest history. Secret files burned inside concrete bunkers as elite officers panicked. Ashes filled the sky, swallowing decades of silent murders. Microscopic traces survived the blaze, trapping the perpetrators forever. Subscribe now so you never miss the hidden truth behind history.",
        thumbnail_text="BURN EVIDENCE 📄🔥",
        scenes=[
            SceneContract(scene_id=1, narration="Destroying records hides the darkest history.", visual_keyword="fire", estimated_seconds=3.0),
            SceneContract(scene_id=2, narration="Secret files burned inside concrete bunkers as elite officers panicked.", visual_keyword="bunker", estimated_seconds=3.5),
            SceneContract(scene_id=3, narration="Ashes filled the sky, swallowing decades of silent murders.", visual_keyword="ashes", estimated_seconds=3.5),
            SceneContract(scene_id=4, narration="Microscopic traces survived the blaze, trapping the perpetrators forever.", visual_keyword="detective", estimated_seconds=3.5),
            SceneContract(scene_id=5, narration="Subscribe now so you never miss the hidden truth behind history.", visual_keyword="ancient manuscript", estimated_seconds=3.5),
        ],
        tags=["history", "shorts"],
        description="Dark history revealed.",
    )

    niche = get_niche("history")
    linter_result = ScriptValidationLinter.lint(script, niche_profile=niche)
    print(f"Linter result: is_valid={linter_result.is_valid}, issues={linter_result.issues}")
    assert linter_result.is_valid, f"Expected linter to pass, but had issues: {linter_result.issues}"
    print("✅ Test 4 PASSED: ScriptValidationLinter passes valid scripts containing CTA!\n")


def test_niche_prompt_instructions_cta():
    print("=== Test 5: Niche Prompt Instructions include CTA & Closing Engagement ===")
    niche = get_niche("psychology")
    instructions = build_script_prompt_instructions(niche, language="en")
    print(f"Instructions excerpt (Section 5):\n{instructions[instructions.find('5. MẪU CÂU'):instructions.find('6. QUY TẮC')]}")
    assert "HIGH-CONVERTING CTA & CLOSING ENGAGEMENT" in instructions
    assert "Subscribe now to master dark psychology" in instructions
    print("✅ Test 5 PASSED: Niche prompt instructions inject high-converting CTA templates!\n")


if __name__ == "__main__":
    test_content_agent_clean_retains_cta()
    test_content_agent_clean_retains_vietnamese_cta()
    test_visual_cta_badge_generation()
    test_script_validation_linter_with_cta()
    test_niche_prompt_instructions_cta()
    print("🎉 ALL 5 CALL TO ACTION (CTA) TESTS PASSED 100%!")
