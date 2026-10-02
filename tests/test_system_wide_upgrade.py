import unittest
import os
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.schemas.contracts import ScriptContract, SceneContract
from app.services.content_linter import ScriptValidationLinter
from app.services.niche_manager import load_all_niches, get_niche, get_niche_subtitle_style
from app.utils.ffmpeg_helper import burn_subtitles, trim_audio_silence

class TestSystemWideUpgrade(unittest.TestCase):

    def test_all_12_niches_loaded_and_standardized(self):
        niches = load_all_niches(force_reload=True)
        self.assertGreaterEqual(len(niches), 12)

        for niche in niches:
            nid = niche.get("id")
            sub_style = get_niche_subtitle_style(niche)
            # MarginV an toàn trên khung 1920 (576 - 672px)
            self.assertTrue(576 <= sub_style["margin_v"] <= 672, f"Niche {nid} margin_v {sub_style['margin_v']} out of safe zone!")
            
            # Affiliate chỉ bật cho animal_rescue
            monet = niche.get("monetization", {})
            if nid == "animal_rescue":
                self.assertTrue(monet.get("affiliate_enabled"))
            else:
                self.assertFalse(monet.get("affiliate_enabled", False), f"Niche {nid} should NOT have affiliate_enabled!")

    def test_linter_strict_english_connectors(self):
        niche = get_niche("deep_sea_earth_mysteries")
        
        # Linter bắt lỗi dangling connector "but..."
        scenes_but = [
            SceneContract(scene_id=1, narration="What if the ocean abyss hides something truly impossible?", visual_keyword="ocean", estimated_seconds=3.0),
            SceneContract(scene_id=2, narration="Navy sonar detected a massive underwater acoustic signal back in 1997.", visual_keyword="sonar", estimated_seconds=3.5),
            SceneContract(scene_id=3, narration="It was measured to be vastly louder than any known living creature on Earth.", visual_keyword="whale", estimated_seconds=3.5),
            SceneContract(scene_id=4, narration="Specialized oceanographic scientists rushed to the location to decode the frequency.", visual_keyword="scientists", estimated_seconds=3.5),
            SceneContract(scene_id=5, narration="The hydrophone telemetry recordings were abruptly locked away inside classified vaults.", visual_keyword="classified", estimated_seconds=3.5),
            SceneContract(scene_id=6, narration="Naval vessels were ordered to turn back immediately without any official explanation.", visual_keyword="sub", estimated_seconds=3.5),
            SceneContract(scene_id=7, narration="To this day no marine biologist has been allowed near the coordinates.", visual_keyword="ocean", estimated_seconds=3.5),
            SceneContract(scene_id=8, narration="Follow for more deep sea secrets but...", visual_keyword="abyss", estimated_seconds=3.5),
        ]
        script_bad = ScriptContract(
            title="Classified Deep Sea Sonar",
            hook="What if the ocean abyss hides something truly impossible?",
            full_narration=" ".join(s.narration for s in scenes_but),
            scenes=scenes_but,
            tags=["shorts", "mystery"]
        )
        res = ScriptValidationLinter.lint(script_bad, niche)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("ends abruptly on an incomplete connector" in issue for issue in res.issues))

    def test_linter_approves_perfect_script(self):
        niche = get_niche("deep_sea_earth_mysteries")
        scenes_good = [
            SceneContract(scene_id=1, narration="What if the ocean abyss hides something truly impossible?", visual_keyword="ocean", estimated_seconds=3.0),
            SceneContract(scene_id=2, narration="Navy sonar detected a massive underwater acoustic signal back in 1997.", visual_keyword="sonar", estimated_seconds=3.5),
            SceneContract(scene_id=3, narration="It was measured to be vastly louder than any known living creature on Earth.", visual_keyword="whale", estimated_seconds=3.5),
            SceneContract(scene_id=4, narration="Specialized oceanographic scientists rushed to the location to decode the frequency.", visual_keyword="scientists", estimated_seconds=3.5),
            SceneContract(scene_id=5, narration="The hydrophone telemetry recordings were abruptly locked away inside classified vaults.", visual_keyword="classified", estimated_seconds=3.5),
            SceneContract(scene_id=6, narration="Naval vessels were ordered to turn back immediately without any official explanation.", visual_keyword="sub", estimated_seconds=3.5),
            SceneContract(scene_id=7, narration="To this day no marine biologist has been allowed near the coordinates.", visual_keyword="ocean", estimated_seconds=3.5),
            SceneContract(scene_id=8, narration="What do you think is hiding in that trench? The mystery remains unsolved.", visual_keyword="abyss", estimated_seconds=3.5),
        ]
        script_good = ScriptContract(
            title="Classified Deep Sea Sonar",
            hook="What if the ocean abyss hides something truly impossible?",
            full_narration=" ".join(s.narration for s in scenes_good),
            scenes=scenes_good,
            tags=["shorts", "mystery"]
        )
        res = ScriptValidationLinter.lint(script_good, niche)
        self.assertTrue(res.is_valid)
        self.assertEqual(len(res.issues), 0)

    def test_publisher_agent_selective_affiliate_and_metadata(self):
        from app.agents.publisher_agent import PublisherAgent
        
        cfg_deepsea = {
            "id": "ch_deepsea",
            "name": "The mysteries of the world",
            "niche": "deep_sea_earth_mysteries",
            "language": "en",
            "affiliate_enabled": False
        }
        agent_deepsea = PublisherAgent(channel_config=cfg_deepsea)
        
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            dummy_video = Path(td) / "dummy_deepsea.mp4"
            dummy_video.write_text("fake video content")

            mock_service = MagicMock()
            mock_request = MagicMock()
            mock_request.next_chunk.return_value = (None, {"id": "fake_vid_123"})
            mock_service.videos().insert.return_value = mock_request
            agent_deepsea.service = mock_service

            with patch("app.services.quota_manager.QuotaManager.can_upload", return_value=True), \
                 patch("app.services.quota_manager.QuotaManager.record_consumption", return_value=True):
                res = agent_deepsea.upload_video(
                    video_path=str(dummy_video),
                    title="Mysterious Ocean Sounds #Shorts",
                    description="Exploring the 1997 Navy sonar sound.",
                    tags=["mystery", "shorts"],
                    topic="Ocean Sonar",
                    script="Exploring the 1997 Navy sonar sound."
                )
            
            insert_args = mock_service.videos().insert.call_args[1]
            body = insert_args["body"]
            self.assertEqual(body["snippet"]["defaultLanguage"], "en")
            self.assertEqual(body["snippet"]["defaultAudioLanguage"], "en-US")
            self.assertTrue(body["status"]["containsSyntheticMedia"])
            self.assertNotIn("amazon", res["enriched_description"].lower())

    def test_publisher_agent_quota_fallback_zip(self):
        from app.agents.publisher_agent import PublisherAgent
        
        cfg_quota = {
            "id": "ch_quota",
            "name": "Test Channel",
            "niche": "deep_sea_earth_mysteries",
            "language": "en"
        }
        agent = PublisherAgent(channel_config=cfg_quota)
        
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            dummy_video = Path(td) / "test_video.mp4"
            dummy_video.write_text("fake video content")
            
            dummy_srt = Path(td) / "test_video.srt"
            dummy_srt.write_text("1\n00:00:00,000 --> 00:00:03,000\nHello")

            mock_service = MagicMock()
            mock_service.videos().insert.side_effect = Exception("The user has exceeded your quota. quotaExceeded")
            agent.service = mock_service
            
            with patch("app.services.quota_manager.QuotaManager.can_upload", return_value=True):
                res = agent.upload_video(
                    video_path=str(dummy_video),
                    title="Test Video #Shorts",
                    description="Test Description",
                    tags=["shorts"]
                )
            
            self.assertEqual(res["status"], "waiting_quota_reset")
            self.assertIn("fallback_zip", res)
            self.assertIsNotNone(res["fallback_zip"])
            self.assertTrue(Path(res["fallback_zip"]).exists())
            Path(res["fallback_zip"]).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
