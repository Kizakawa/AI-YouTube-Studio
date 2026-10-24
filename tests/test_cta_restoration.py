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


def test_content_agent_clean_strips_spoken_cta_for_infinite_loop():
    print("=== Test 1: ContentAgent strips spoken Subscribe CTA to preserve Infinite Loop ===")
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
    
    # Must strip the spoken Subscribe CTA while keeping the substantive payoff sentence
    assert "Subscribe now" not in last_narr, "Spoken Subscribe CTA should be stripped to prevent 15% APV drop!"
    assert "A real ally respects your boundaries." in last_narr
    assert last_narr.endswith("."), "Sentence must properly end with punctuation."
    assert cleaned.pinned_comment, "Pinned comment CTA must be populated!"
    print("✅ Test 1 PASSED: ContentAgent strips spoken outro CTA and preserves Infinite Loop!\n")


def test_content_agent_clean_strips_vietnamese_spoken_cta():
    print("=== Test 2: ContentAgent strips Vietnamese spoken Subscribe CTA and builds Loop Bridge ===")
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
    
    assert "bấm đăng ký kênh" not in last_narr.lower(), "Vietnamese spoken CTA should be replaced by Infinite Loop bridge!"
    assert last_narr.endswith("."), "Loop bridge must be a complete sentence."
    print("✅ Test 2 PASSED: Vietnamese spoken CTA replaced with Infinite Loop bridge!\n")


def test_visual_cta_badge_generation():
    print("=== Test 3: Visual On-Screen CTABadge Generation in ASS Subtitles (Default False, Optional True) ===")
    scenes_en = [
        SceneContract(scene_id=1, narration="Stop trusting everyone blindly.", visual_keyword="shadows", audio_duration_ms=3000),
        SceneContract(scene_id=2, narration="They observe your every move.", visual_keyword="eyes", audio_duration_ms=3000),
        SceneContract(scene_id=3, narration="And that cold silence shatters their mask.", visual_keyword="chess", audio_duration_ms=4000),
    ]

    # Default should be show_cta_badge=False for Infinite Loop
    ass_default = SubtitleHelper.build_ass_content(scenes=scenes_en, niche_id="psychology", show_cta_badge=False)
    assert not [line for line in ass_default.splitlines() if "CTABadge" in line and line.startswith("Dialogue: 1,")], "Default should not emit CTABadge!"

    ass_en = SubtitleHelper.build_ass_content(
        scenes=scenes_en,
        niche_id="psychology",
        show_cta_badge=True
    )
    
    cta_lines_en = [line for line in ass_en.splitlines() if "CTABadge" in line and line.startswith("Dialogue: 1,")]
    print(f"English CTABadge lines: {cta_lines_en}")
    assert len(cta_lines_en) == 1, "Expected exactly 1 CTABadge line in ASS when show_cta_badge=True!"
    assert "SUBSCRIBE" in cta_lines_en[0], "Expected CTA badge text in English!"

    # Test Vietnamese Niche
    scenes_vi = [
        SceneContract(scene_id=1, narration="Dưới đáy biển sâu mười nghìn mét.", visual_keyword="deep sea", audio_duration_ms=3200),
        SceneContract(scene_id=2, narration="Áp lực nước nghiền nát mọi thứ.", visual_keyword="abyss", audio_duration_ms=3400),
        SceneContract(scene_id=3, narration="Và bí ẩn đó vẫn đang chờ được giải mã.", visual_keyword="ocean", audio_duration_ms=4000),
    ]

    ass_vi = SubtitleHelper.build_ass_content(
        scenes=scenes_vi,
        niche_id="deep_sea",
        show_cta_badge=True
    )
    
    cta_lines_vi = [line for line in ass_vi.splitlines() if "CTABadge" in line and line.startswith("Dialogue: 1,")]
    print(f"Vietnamese CTABadge lines: {cta_lines_vi}")
    assert len(cta_lines_vi) == 1, "Expected exactly 1 CTABadge line in ASS!"
    assert "ĐĂNG KÝ" in cta_lines_vi[0], "Expected CTA badge text in Vietnamese!"

    print("✅ Test 3 PASSED: Visual CTABadge defaults to False (Infinite Loop) and generates when explicitly enabled!\n")


def test_script_validation_linter_infinite_loop():
    print("=== Test 4: ScriptValidationLinter enforces 35-48s pacing and Zero Spoken Outro CTA ===")
    script = ScriptContract(
        title="Burn The Evidence Before They Knock 🚪🔥",
        hook="Destroying secret records hides the darkest history.",
        full_narration="Destroying secret records hides the darkest history. Secret files burned inside concrete bunkers as elite officers panicked in silence. Thick black ashes filled the midnight sky, swallowing decades of covert operations. Yet a single locked iron safe survived the raging inferno untouched. Inside lay the handwritten ledger that exposed every conspirator by name. Today those declassified pages prove how fragile absolute power truly is. Every empire leaves a paper trail waiting to be found. And that single surviving ledger is why the truth begins right where they tried to burn it.",
        thumbnail_text="BURN EVIDENCE 📄🔥",
        scenes=[
            SceneContract(scene_id=1, narration="Destroying secret records hides the darkest history.", visual_keyword="burning classified documents fireplace", estimated_seconds=3.0),
            SceneContract(scene_id=2, narration="Secret files burned inside concrete bunkers as elite officers panicked in silence.", visual_keyword="bunker", estimated_seconds=3.5),
            SceneContract(scene_id=3, narration="Thick black ashes filled the midnight sky, swallowing decades of covert operations.", visual_keyword="ashes", estimated_seconds=3.5),
            SceneContract(scene_id=4, narration="Yet a single locked iron safe survived the raging inferno untouched.", visual_keyword="iron safe", estimated_seconds=3.5),
            SceneContract(scene_id=5, narration="Inside lay the handwritten ledger that exposed every conspirator by name.", visual_keyword="detective", estimated_seconds=3.5),
            SceneContract(scene_id=6, narration="Today those declassified pages prove how fragile absolute power truly is.", visual_keyword="ancient manuscript", estimated_seconds=3.5),
            SceneContract(scene_id=7, narration="Every empire leaves a paper trail waiting to be found.", visual_keyword=" magnifying glass", estimated_seconds=3.2),
            SceneContract(scene_id=8, narration="And that single surviving ledger is why the truth begins right where they tried to burn it.", visual_keyword="burning document", estimated_seconds=3.8),
        ],
        tags=["history", "shorts"],
        description="Dark history revealed.",
    )

    niche = get_niche("history")
    linter_result = ScriptValidationLinter.lint(script, niche_profile=niche)
    print(f"Linter result: is_valid={linter_result.is_valid}, issues={linter_result.issues}")
    assert linter_result.is_valid, f"Expected linter to pass, but had issues: {linter_result.issues}"
    print("✅ Test 4 PASSED: ScriptValidationLinter passes valid 8-scene Infinite Loop script!\n")


def test_niche_prompt_instructions_cta():
    print("=== Test 5: Niche Prompt Instructions enforce Zero Spoken Outro CTA & Pinned Comment Questions ===")
    niche = get_niche("psychology")
    instructions = build_script_prompt_instructions(niche, language="en")
    assert "ZERO SPOKEN OUTRO CTA" in instructions
    assert "pinned_comment" in instructions
    assert any(k in instructions for k in ("Sign #2", "Quick Poll", "Have you ever", "1 or 2"))
    print("✅ Test 5 PASSED: Niche prompt instructions enforce Zero Spoken Outro CTA and Pinned Comment questions!\n")


if __name__ == "__main__":
    test_content_agent_clean_strips_spoken_cta_for_infinite_loop()
    test_content_agent_clean_strips_vietnamese_spoken_cta()
    test_visual_cta_badge_generation()
    test_script_validation_linter_infinite_loop()
    test_niche_prompt_instructions_cta()
    print("🎉 ALL 5 INFINITE LOOP & PINNED COMMENT CTA TESTS PASSED 100%!")
