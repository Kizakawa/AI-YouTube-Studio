import unittest
from app.schemas.contracts import ScriptContract, SceneContract
from app.services.content_linter import ScriptValidationLinter

def make_dummy_script(narration: str, last_narration: str = "This mystery remains entirely unsolved."):
    scenes = [
        SceneContract(scene_id=1, narration="What if the deep ocean hides something truly ancient?", visual_keyword="ocean abyss", estimated_seconds=3.5),
        SceneContract(scene_id=2, narration=narration, visual_keyword="deep sea creature", estimated_seconds=4.0),
        SceneContract(scene_id=3, narration="Scientists around the globe cannot explain the strange acoustic signal.", visual_keyword="sonar anomaly", estimated_seconds=3.5),
        SceneContract(scene_id=4, narration="Navy logs and radar recordings were immediately classified under top secret protocols.", visual_keyword="classified submarine", estimated_seconds=3.5),
        SceneContract(scene_id=5, narration="The measured low frequency was vastly louder than any living creature ever discovered.", visual_keyword="soundwave radar", estimated_seconds=3.5),
        SceneContract(scene_id=6, narration="Oceanographers traveled deep into the Mariana Trench searching for physical evidence.", visual_keyword="deep submarine", estimated_seconds=3.5),
        SceneContract(scene_id=7, narration="Yet every instrument deployed into the black depths glitched within seconds.", visual_keyword="glitch instruments", estimated_seconds=3.5),
        SceneContract(scene_id=8, narration=last_narration, visual_keyword="dark abyss", estimated_seconds=3.5),
    ]
    return ScriptContract(
        title="The Classified Ocean Signal",
        hook="What if the deep ocean hides something truly ancient?",
        full_narration=" ".join(s.narration for s in scenes),
        scenes=scenes,
        tags=["shorts", "mystery", "ocean"]
    )

class TestContentLinter(unittest.TestCase):

    def test_linter_detects_trailing_connector(self):
        niche_profile = {"name": "Deep Sea", "script_blueprint": {"banned_lexicon": ["cigarette"]}}
        
        # Kịch bản bị cắt cụt ở đuôi "because..."
        script_bad = make_dummy_script(
            narration="In 1997, deep hydrophones captured an impossible sound echoing across thousands of miles.",
            last_narration="Follow to unlock the truth behind Part 2 — because..."
        )
        result = ScriptValidationLinter.lint(script_bad, niche_profile)
        self.assertFalse(result.is_valid)
        self.assertTrue(any("ends abruptly on an incomplete connector" in issue for issue in result.issues))

        # Kịch bản bị cắt cụt ở "which is why..."
        script_bad_which = make_dummy_script(
            narration="In 1997, deep hydrophones captured an impossible sound echoing across thousands of miles.",
            last_narration="Subscribe before Part 2 drops tomorrow — which..."
        )
        result_which = ScriptValidationLinter.lint(script_bad_which, niche_profile)
        self.assertFalse(result_which.is_valid)

        # Kịch bản bị cắt cụt ở "with..."
        script_bad_with = make_dummy_script(
            narration="In 1997, deep hydrophones captured an impossible sound echoing across thousands of miles.",
            last_narration="The deep hides mysteries beyond comprehension with..."
        )
        result_with = ScriptValidationLinter.lint(script_bad_with, niche_profile)
        self.assertFalse(result_with.is_valid)

        # Kịch bản hoàn chỉnh ngữ pháp
        script_good = make_dummy_script(
            narration="In 1997, deep hydrophones captured an impossible sound echoing across thousands of miles.",
            last_narration="What do you think is hiding down there? Drop your theory below."
        )
        result_good = ScriptValidationLinter.lint(script_good, niche_profile)
        self.assertTrue(result_good.is_valid)

    def test_linter_detects_banned_lexicon(self):
        niche_profile = {"name": "Deep Sea", "banned_lexicon": ["cigarette", "office cubicle"]}
        script_with_anomaly = make_dummy_script(
            narration="A diver dropped a cigarette into the dark waters of the Mariana Trench.",
            last_narration="Drop your theory in the comments below."
        )
        result = ScriptValidationLinter.lint(script_with_anomaly, niche_profile)
        self.assertFalse(result.is_valid)
        self.assertIn("cigarette", result.detected_banned_words)

    def test_linter_detects_cooldown_entities(self):
        niche_profile = {"name": "Deep Sea"}
        cooldown = ["The Bloop", "Mariana Trench"]
        script_repeated = make_dummy_script(
            narration="The Bloop occurred right near the deep trenches of the Pacific.",
            last_narration="What do you think caused this sound?"
        )
        result = ScriptValidationLinter.lint(script_repeated, niche_profile, cooldown_entities=cooldown)
        self.assertFalse(result.is_valid)
        self.assertIn("the bloop", result.detected_cooldown_entities)
        self.assertIn("mariana trench", result.detected_cooldown_entities)


if __name__ == "__main__":
    unittest.main()
