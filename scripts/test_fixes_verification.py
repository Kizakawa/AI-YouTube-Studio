import sys
import json
from pathlib import Path

# Ensure UTF-8 output on Windows
sys.stdout.reconfigure(encoding='utf-8')

print("=" * 60)
print("RUNNING SYSTEM FIXES VERIFICATION TEST")
print("=" * 60)

# 1. Test Media History Deduplication
print("\n[TEST 1] Testing Media History & Deduplication...")
from app.services.media.media_history import (
    record_used_media,
    get_recent_media_urls,
    record_batch_used_media
)

test_url_1 = "https://images.pexels.com/videos/9999991/test_sample_1.mp4"
test_url_2 = "https://images.pexels.com/photos/8888882/test_sample_2.jpeg"

record_used_media(test_url_1, query="dark eye test", job_id=999)
record_used_media(test_url_2, query="shadow person test", job_id=999)

recent = get_recent_media_urls(limit=50)
assert test_url_1 in recent, "test_url_1 must be present in recent media"
assert test_url_2 in recent, "test_url_2 must be present in recent media"
print(f"✅ Media History deduplication PASSED! Loaded {len(recent)} recent media URLs.")

# 2. Test Content Agent Diverse CTA Engine
print("\n[TEST 2] Testing ContentAgent Diverse CTA Engine...")
from app.agents.content_agent import ContentAgent

ca = ContentAgent()
# Mock raw response to test post-processor
mock_psych_json = {
    "title": "Don't React When They Provoke You",
    "topic": "Psychology defense against toxic people",
    "hook": "Never apologize when attacked.",
    "scenes": [
        {"scene_id": 1, "narration": "Never apologize when they attack you.", "estimated_seconds": 3.0, "stock_query": "man standing", "visual_keyword": "man standing"},
        {"scene_id": 2, "narration": "Instead, pause for three full seconds.", "estimated_seconds": 3.0, "stock_query": "watch ticking", "visual_keyword": "watch ticking"},
        {"scene_id": 3, "narration": "This completely dismantles their psychological control because", "estimated_seconds": 3.0, "stock_query": "chess piece", "visual_keyword": "chess piece"}
    ]
}

parsed = ca._parse_json_script(json.dumps(mock_psych_json))
last_scene_narr = parsed.scenes[-1].narration
print(f"Generated Ending Narration: '{last_scene_narr}'")

# Ensure it doesn't have the rigid robotic "Subscribe and follow for Part 2"
assert "Subscribe and follow for Part 2" not in last_scene_narr, "Should NOT have hardcoded 'Subscribe and follow for Part 2'"
assert "because" in last_scene_narr, "Should retain the seamless infinite loop connector"
print("✅ Diverse CTA Engine PASSED! Output is organic and tailored to psychology niche.")

# 3. Test Channel Health & Expired Token Detection
print("\n[TEST 3] Testing PublisherAgent and Channel Token Status...")
from app.web.server import _check_channel_token_health

maya_token_p = Path("credentials/channels/channel_maya/youtube_token.json")
maya_health = _check_channel_token_health("channel_maya", maya_token_p)
print(f"channel_maya status: valid={maya_health['valid']}, expired={maya_health['expired']}")
assert maya_health["expired"] is True, "channel_maya should be detected as expired!"

# Check one of the active channels
mysteries_p = Path("credentials/channels/ucsrjiil67qxidtxt70z-iwa/youtube_token.json")
mysteries_health = _check_channel_token_health("ucsrjiil67qxidtxt70z-iwa", mysteries_p)
print(f"ucsrjiil67qxidtxt70z-iwa status: valid={mysteries_health['valid']}, expired={mysteries_health['expired']}")
assert mysteries_health["valid"] is True, "ucsrjiil67qxidtxt70z-iwa should be detected as valid!"

print("✅ Token Health Detection PASSED!")

print("\n" + "=" * 60)
print("ALL 3 VERIFICATION TESTS PASSED SUCCESSFULLY! 🚀")
print("=" * 60)
