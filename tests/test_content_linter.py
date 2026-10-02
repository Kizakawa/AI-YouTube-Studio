import pytest
from app.schemas.contracts import ScriptContract, SceneContract
from app.services.content_linter import ScriptValidationLinter

def make_dummy_script(narration: str, last_narration: str = "Subscribe for Part 2."):
    scenes = [
        SceneContract(scene_id=1, narration="What if the ocean hides something ancient?", visual_keyword="ocean abyss", estimated_seconds=3.5),
        SceneContract(scene_id=2, narration=narration, visual_keyword="deep sea creature", estimated_seconds=4.0),
        SceneContract(scene_id=3, narration="Scientists cannot explain the signal.", visual_keyword="sonar anomaly", estimated_seconds=3.5),
        SceneContract(scene_id=4, narration="Navy logs were immediately classified.", visual_keyword="classified submarine", estimated_seconds=3.5),
        SceneContract(scene_id=5, narration="The frequency was louder than anything known.", visual_keyword="soundwave radar", estimated_seconds=3.5),
        SceneContract(scene_id=6, narration=last_narration, visual_keyword="dark abyss", estimated_seconds=3.5),
    ]
    return ScriptContract(
        title="The Classified Ocean Signal",
        hook="What if the ocean hides something ancient?",
        full_narration=" ".join(s.narration for s in scenes),
        scenes=scenes,
        tags=["shorts", "mystery", "ocean"]
    )

def test_linter_detects_trailing_connector():
    niche_profile = {"name": "Deep Sea", "script_blueprint": {"banned_lexicon": ["cigarette"]}}
    
    # Kịch bản bị cắt cụt ở đuôi "because..."
    script_bad = make_dummy_script(
        narration="In 1997, deep hydrophones captured an impossible sound.",
        last_narration="Follow to unlock the truth behind Part 2 — because..."
    )
    result = ScriptValidationLinter.lint(script_bad, niche_profile)
    assert not result.is_valid
    assert any("ends abruptly on an incomplete connector" in issue for issue in result.issues)

    # Kịch bản bị cắt cụt ở "which is why..."
    script_bad_which = make_dummy_script(
        narration="In 1997, deep hydrophones captured an impossible sound.",
        last_narration="Subscribe before Part 2 drops tomorrow — which..."
    )
    result_which = ScriptValidationLinter.lint(script_bad_which, niche_profile)
    assert not result_which.is_valid

    # Kịch bản bị cắt cụt ở "with..."
    script_bad_with = make_dummy_script(
        narration="In 1997, deep hydrophones captured an impossible sound.",
        last_narration="The deep hides mysteries beyond comprehension with..."
    )
    result_with = ScriptValidationLinter.lint(script_bad_with, niche_profile)
    assert not result_with.is_valid

    # Kịch bản hoàn chỉnh ngữ pháp
    script_good = make_dummy_script(
        narration="In 1997, deep hydrophones captured an impossible sound.",
        last_narration="What do you think is hiding down there? Drop your theory below."
    )
    result_good = ScriptValidationLinter.lint(script_good, niche_profile)
    assert result_good.is_valid

def test_linter_detects_banned_lexicon():
    niche_profile = {"name": "Deep Sea", "banned_lexicon": ["cigarette", "office cubicle"]}
    script_with_anomaly = make_dummy_script(
        narration="A diver dropped a cigarette into the Mariana Trench.",
        last_narration="Drop your theory in the comments below."
    )
    result = ScriptValidationLinter.lint(script_with_anomaly, niche_profile)
    assert not result.is_valid
    assert "cigarette" in result.detected_banned_words

def test_linter_detects_cooldown_entities():
    niche_profile = {"name": "Deep Sea"}
    cooldown = ["The Bloop", "Mariana Trench"]
    script_repeated = make_dummy_script(
        narration="The Bloop occurred right near the Mariana Trench.",
        last_narration="What do you think caused this sound?"
    )
    result = ScriptValidationLinter.lint(script_repeated, niche_profile, cooldown_entities=cooldown)
    assert not result.is_valid
    assert "the bloop" in result.detected_cooldown_entities
    assert "mariana trench" in result.detected_cooldown_entities
