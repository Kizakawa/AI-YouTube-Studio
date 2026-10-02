import pytest
import os
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.schemas.contracts import ScriptContract, SceneContract
from app.services.content_linter import ScriptValidationLinter
from app.services.niche_manager import load_all_niches, get_niche, get_niche_subtitle_style
from app.utils.ffmpeg_helper import burn_subtitles, trim_audio_silence

def test_all_12_niches_loaded_and_standardized():
    niches = load_all_niches(force_reload=True)
    assert len(niches) >= 12

    for niche in niches:
        nid = niche.get("id")
        sub_style = get_niche_subtitle_style(niche)
        # MarginV an toàn trên khung 1920 (576 - 672px)
        assert 576 <= sub_style["margin_v"] <= 672, f"Niche {nid} margin_v {sub_style['margin_v']} out of safe zone!"
        
        # Affiliate chỉ bật cho animal_rescue
        monet = niche.get("monetization", {})
        if nid == "animal_rescue":
            assert monet.get("affiliate_enabled") is True
        else:
            assert monet.get("affiliate_enabled") is False, f"Niche {nid} should NOT have affiliate_enabled!"

def test_linter_strict_english_connectors():
    niche = get_niche("deep_sea_earth_mysteries")
    
    # Linter bắt lỗi dangling connector "but..."
    scenes_but = [
        SceneContract(scene_id=1, narration="What if the ocean hides something impossible?", visual_keyword="ocean", estimated_seconds=3.0),
        SceneContract(scene_id=2, narration="Navy sonar detected a massive signal in 1997.", visual_keyword="sonar", estimated_seconds=3.5),
        SceneContract(scene_id=3, narration="It was louder than any living creature.", visual_keyword="whale", estimated_seconds=3.5),
        SceneContract(scene_id=4, narration="Scientists rushed to decode the frequency.", visual_keyword="scientists", estimated_seconds=3.5),
        SceneContract(scene_id=5, narration="The files were abruptly locked away.", visual_keyword="classified", estimated_seconds=3.5),
        SceneContract(scene_id=6, narration="Follow for more deep sea secrets but...", visual_keyword="abyss", estimated_seconds=3.5),
    ]
    script_bad = ScriptContract(
        title="Classified Deep Sea Sonar",
        hook="What if the ocean hides something impossible?",
        full_narration=" ".join(s.narration for s in scenes_but),
        scenes=scenes_but,
        tags=["shorts", "mystery"]
    )
    res = ScriptValidationLinter.lint(script_bad, niche)
    assert not res.is_valid
    assert any("ends abruptly on an incomplete connector" in issue for issue in res.issues)

def test_linter_approves_perfect_script():
    niche = get_niche("deep_sea_earth_mysteries")
    scenes_good = [
        SceneContract(scene_id=1, narration="What if the ocean hides something impossible?", visual_keyword="ocean", estimated_seconds=3.0),
        SceneContract(scene_id=2, narration="Navy sonar detected a massive signal in 1997.", visual_keyword="sonar", estimated_seconds=3.5),
        SceneContract(scene_id=3, narration="It was louder than any living creature.", visual_keyword="whale", estimated_seconds=3.5),
        SceneContract(scene_id=4, narration="Scientists rushed to decode the frequency.", visual_keyword="scientists", estimated_seconds=3.5),
        SceneContract(scene_id=5, narration="The files were abruptly locked away.", visual_keyword="classified", estimated_seconds=3.5),
        SceneContract(scene_id=6, narration="What do you think caused this sound? Drop your theory below.", visual_keyword="abyss", estimated_seconds=3.5),
    ]
    script_good = ScriptContract(
        title="Classified Deep Sea Sonar",
        hook="What if the ocean hides something impossible?",
        full_narration=" ".join(s.narration for s in scenes_good),
        scenes=scenes_good,
        tags=["shorts", "mystery"]
    )
    res = ScriptValidationLinter.lint(script_good, niche)
    assert res.is_valid
    assert len(res.issues) == 0

def test_publisher_agent_selective_affiliate_and_metadata(tmp_path):
    from app.agents.publisher_agent import PublisherAgent
    
    # 1. Kênh Deep Sea (không bật affiliate) -> Mô tả phải sạch 100%
    cfg_deepsea = {
        "id": "ch_deepsea",
        "name": "The mysteries of the world",
        "niche": "deep_sea_earth_mysteries",
        "language": "en",
        "affiliate_enabled": False
    }
    agent_deepsea = PublisherAgent(channel_config=cfg_deepsea)
    
    dummy_video = tmp_path / "dummy_deepsea.mp4"
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
    
    # Kiểm tra metadata truyền vào insert
    insert_args = mock_service.videos().insert.call_args[1]
    body = insert_args["body"]
    assert body["snippet"]["defaultLanguage"] == "en"
    assert body["snippet"]["defaultAudioLanguage"] == "en-US"
    assert body["status"]["containsSyntheticMedia"] is True
    
    # Mô tả KHÔNG được có link amazon
    assert "amazon" not in res["enriched_description"].lower()

def test_publisher_agent_quota_fallback_zip(tmp_path):
    from app.agents.publisher_agent import PublisherAgent
    
    cfg_quota = {
        "id": "ch_quota",
        "name": "Test Channel",
        "niche": "deep_sea_earth_mysteries",
        "language": "en"
    }
    agent = PublisherAgent(channel_config=cfg_quota)
    
    dummy_video = tmp_path / "test_video.mp4"
    dummy_video.write_text("fake video content")
    
    dummy_srt = tmp_path / "test_video.srt"
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
    
    assert res["status"] == "waiting_quota_reset"
    assert "fallback_zip" in res
    assert res["fallback_zip"] is not None
    assert Path(res["fallback_zip"]).exists()
    
    # Clean up created zip
    Path(res["fallback_zip"]).unlink(missing_ok=True)

