"""
tests/test_repurposer_niche.py
Test suite tu dong kiem tra tinh nang Tai Che Shorts AI -- Niche-Aware Intelligence.
15 test cases chia 5 nhom:
  A: Niche Prompt Injection (TC-01 to TC-04)
  B: Niche-Aware Fallback   (TC-05 to TC-08)
  C: No-Transcript Intel    (TC-09 to TC-12)
  D: Channel-Niche Binding  (TC-13 to TC-14)
  E: Edge Cases             (TC-15)
Chay: python -m pytest tests/test_repurposer_niche.py -v
"""
import sys
import os
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _get_niche(niche_id: str):
    from app.services.niche_manager import get_niche
    return get_niche(niche_id)

def _get_channel_niche(channel_id: str):
    from app.services.niche_manager import get_channel_niche
    return get_channel_niche(channel_id)

def _build_prompt_instructions(niche_id: str) -> str:
    from app.services.niche_manager import get_niche, build_script_prompt_instructions
    return build_script_prompt_instructions(get_niche(niche_id), language="en")

def _build_fallback(niche_id: str, video_title: str = "Test Video", duration: float = 300.0) -> list:
    from app.services.video_repurposer import VideoRepurposerService
    svc = VideoRepurposerService()
    niche_profile = _get_niche(niche_id)
    if hasattr(svc, "_build_niche_fallback_moments"):
        return svc._build_niche_fallback_moments(niche_profile, video_title, duration)
    # fallback: simulate API error to force hardcoded/niche fallback path
    with patch.object(svc.content_agent, "_call_api", side_effect=Exception("Simulated")):
        return svc.analyze_viral_moments(
            transcript_entries=[],
            video_title=video_title,
            video_duration=duration,
            niche_id=niche_id,
        )

# ---------------------------------------------------------------------------
# GROUP A: Niche Prompt Injection (TC-01 -> TC-04)
# ---------------------------------------------------------------------------

class TestNichePromptInjection(unittest.TestCase):
    def test_TC01_animal_rescue_hooks_in_prompt(self):
        p = _build_prompt_instructions("animal_rescue")
        keywords = ["trembling", "heartbeat", "rescue", "abandoned", "cardboard", "stray", "Heartbreaking"]
        self.assertTrue(any(k.lower() in p.lower() for k in keywords),
            f"TC-01 FAIL: animal_rescue prompt missing hook keywords. Got:\n{p[:400]}")

    def test_TC02_wildlife_predators_hooks_in_prompt(self):
        p = _build_prompt_instructions("wildlife_predators")
        keywords = ["apex", "predator", "PSI", "bite", "lethal", "roar", "lion"]
        self.assertTrue(any(k.lower() in p.lower() for k in keywords),
            f"TC-02 FAIL: wildlife_predators prompt missing hook keywords. Got:\n{p[:400]}")

    def test_TC03_psychology_hooks_in_prompt(self):
        p = _build_prompt_instructions("psychology")
        keywords = ["manipulation", "Gaslighting", "99%", "toxic", "dark", "warning"]
        self.assertTrue(any(k.lower() in p.lower() for k in keywords),
            f"TC-03 FAIL: psychology prompt missing hook keywords. Got:\n{p[:400]}")

    def test_TC04_deep_sea_hooks_in_prompt(self):
        p = _build_prompt_instructions("deep_sea_earth_mysteries")
        keywords = ["classified", "thalassophobia", "Mariana", "sonar", "NASA", "deep"]
        self.assertTrue(any(k.lower() in p.lower() for k in keywords),
            f"TC-04 FAIL: deep_sea prompt missing hook keywords. Got:\n{p[:400]}")

# ---------------------------------------------------------------------------
# GROUP B: Niche-Aware Dynamic Fallback (TC-05 -> TC-08)
# ---------------------------------------------------------------------------

class TestNicheAwareFallback(unittest.TestCase):
    def setUp(self):
        self.animal_moments = _build_fallback("animal_rescue", "Rescue Puppy", 300.0)
        self.wildlife_moments = _build_fallback("wildlife_predators", "Lion Hunt", 300.0)

    def test_TC05_animal_rescue_no_generic_shock_type(self):
        for m in self.animal_moments:
            mt = m.get("moment_type", "")
            self.assertNotIn("GÂY SỐC", mt, f"TC-05 FAIL: found 'GÂY SOC' in animal_rescue: {mt}")
            self.assertNotIn("BÍ MẬT", mt, f"TC-05 FAIL: found 'BI MAT' in animal_rescue: {mt}")

    def test_TC06_animal_rescue_has_niche_relevant_type(self):
        keys = ["PHÉP MÀU", "RESCUE", "HỒI SINH", "CỨU", "MIRACLE", "LOVE", "PAWS", "HEARTWARMING"]
        for m in self.animal_moments:
            mt = m.get("moment_type", "").upper()
            self.assertTrue(any(k.upper() in mt for k in keys),
                f"TC-06 FAIL: animal moment_type not relevant: '{mt}'")

    def test_TC07_animal_rescue_subtitle_highlights_not_generic(self):
        banned = ["BÍ MẬT", "SAI LẦM", "NGUY HIỂM", "CẢNH BÁO"]
        for m in self.animal_moments:
            highlights = m.get("edit_blueprint", {}).get("subtitle_highlights", [])
            for b in banned:
                self.assertNotIn(b, highlights,
                    f"TC-07 FAIL: animal subtitle has banned word '{b}': {highlights}")

    def test_TC08_wildlife_subtitle_has_power_keywords(self):
        power = {"APEX", "POWER", "SPEED", "LETHAL", "HUNT", "PREDATOR", "BITE", "STRENGTH"}
        for m in self.wildlife_moments:
            highlights = [h.upper() for h in m.get("edit_blueprint", {}).get("subtitle_highlights", [])]
            joined = " ".join(highlights)
            self.assertTrue(any(k in joined for k in power),
                f"TC-08 FAIL: wildlife subtitle lacks power keywords. Got: {highlights}")

# ---------------------------------------------------------------------------
# GROUP C: No-Transcript Intelligence (TC-09 -> TC-12)
# ---------------------------------------------------------------------------

class TestNoTranscriptIntelligence(unittest.TestCase):
    def test_TC09_animal_rescue_no_transcript_returns_moments(self):
        moments = _build_fallback("animal_rescue", "Hard Rescue", 600.0)
        self.assertGreater(len(moments), 0, "TC-09 FAIL: No moments returned for animal_rescue")
        for m in moments:
            self.assertIn("start_seconds", m)
            self.assertIn("end_seconds", m)
            self.assertIn("moment_type", m)

    def test_TC10_wildlife_no_transcript_not_hardcoded_generic(self):
        moments = _build_fallback("wildlife_predators", "Predator Hunt", 600.0)
        for m in moments:
            self.assertNotEqual(m.get("moment_type",""), "🔥 BÍ MẬT GÂY SỐC",
                "TC-10 FAIL: wildlife returned hardcoded generic fallback")

    def test_TC11_psychology_no_transcript_relevant_type(self):
        moments = _build_fallback("psychology", "Dark Psychology", 600.0)
        keys = ["TÂM LÝ", "ĐỌC VỊ", "THAO TÚNG", "PSYCHOLOGY", "MIND", "BEHAVIOR", "WARNING"]
        for m in moments:
            mt = m.get("moment_type","").upper()
            self.assertTrue(any(k.upper() in mt for k in keys),
                f"TC-11 FAIL: psychology moment_type not relevant: '{mt}'")

    def test_TC12_deep_sea_no_transcript_relevant_type(self):
        moments = _build_fallback("deep_sea_earth_mysteries", "Deep Ocean Mystery", 600.0)
        keys = ["BÍ ẨN", "LỜI GIẢI", "DEEP", "MYSTERY", "CLASSIFIED", "OCEAN", "UNKNOWN"]
        for m in moments:
            mt = m.get("moment_type","").upper()
            self.assertTrue(any(k.upper() in mt for k in keys),
                f"TC-12 FAIL: deep_sea moment_type not relevant: '{mt}'")

# ---------------------------------------------------------------------------
# GROUP D: Channel-Niche Binding (TC-13 -> TC-14)
# ---------------------------------------------------------------------------

class TestChannelNicheBinding(unittest.TestCase):
    def test_TC13_paws_hearts_rescue_maps_to_animal_rescue(self):
        niche = _get_channel_niche("ch_paws_hearts_rescue")
        self.assertEqual(niche.get("id"), "animal_rescue",
            f"TC-13 FAIL: ch_paws_hearts_rescue -> niche='{niche.get('id')}' (expected 'animal_rescue')")

    def test_TC14_wild_odyssey_maps_to_wildlife_predators(self):
        niche = _get_channel_niche("ch_wild_odyssey")
        self.assertEqual(niche.get("id"), "wildlife_predators",
            f"TC-14 FAIL: ch_wild_odyssey -> niche='{niche.get('id')}' (expected 'wildlife_predators')")

# ---------------------------------------------------------------------------
# GROUP E: Edge Cases (TC-15)
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):
    def test_TC15_short_video_fallback_valid_segment(self):
        moments = _build_fallback("animal_rescue", "Short clip", 20.0)
        self.assertGreater(len(moments), 0, "TC-15 FAIL: No moments for short video")
        for m in moments:
            s = m.get("start_seconds", 0)
            e = m.get("end_seconds", 0)
            d = m.get("duration_seconds", 0)
            self.assertLess(s, e, f"TC-15 FAIL: start ({s}) >= end ({e})")
            self.assertGreaterEqual(d, 10.0, f"TC-15 FAIL: duration ({d}) < 10s")
            self.assertLessEqual(e, 25.0, f"TC-15 FAIL: end ({e}) > video length 20s+buffer")

# ---------------------------------------------------------------------------
# GROUP F: Video Description & Request Model (TC-16 -> TC-17)
# ---------------------------------------------------------------------------

class TestVideoDescriptionContext(unittest.TestCase):
    def test_TC16_video_description_injected_in_prompt(self):
        from app.services.video_repurposer import VideoRepurposerService
        svc = VideoRepurposerService()
        captured_prompt = []

        def mock_call_api(prompt, **kwargs):
            captured_prompt.append(prompt)
            raise Exception("Stop after prompt capture")

        with patch.object(svc.content_agent, "_call_api", side_effect=mock_call_api):
            try:
                svc.analyze_viral_moments(
                    transcript_entries=[],
                    video_title="Puppy Rescue Story",
                    video_duration=120.0,
                    niche_id="animal_rescue",
                    video_description="Chú cún con bị kẹt dưới giếng hoang 3 ngày được cứu sống",
                )
            except Exception:
                pass

        self.assertTrue(len(captured_prompt) > 0, "TC-16 FAIL: Prompt was not generated")
        prompt_text = captured_prompt[0]
        self.assertIn("giếng hoang 3 ngày", prompt_text,
            f"TC-16 FAIL: video_description was not injected into prompt. Got:\n{prompt_text[:500]}")

    def test_TC17_repurpose_analyze_request_model(self):
        from app.web.server import RepurposeAnalyzeRequest
        req = RepurposeAnalyzeRequest(
            url_or_path="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            niche_id="animal_rescue",
            video_description="Video cứu hộ chú mèo mắc kẹt trên cây cao"
        )
        self.assertEqual(req.video_description, "Video cứu hộ chú mèo mắc kẹt trên cây cao")
        self.assertEqual(req.niche_id, "animal_rescue")

# ---------------------------------------------------------------------------
# RUNNER
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("TEST SUITE: Tai Che Shorts AI -- Niche-Aware Intelligence (17 TCs)")
    print("=" * 70)
    unittest.main(verbosity=2)

