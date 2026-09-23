"""Verification script for all implementation commits."""
import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, r"e:\AI Youtube\AI-youtube-main")

print("Testing import chain...")
print()

# Commit 0.1
from app.schemas.contracts import JobStatus, SceneContract, ScriptContract, RenderJobContract, AudioSegment
print("[0.1] contracts.py - OK")

sample = {
    "title": "Dark Psychology Secrets You Need to Know",
    "hook": "Your mind is being manipulated right now",
    "full_narration": "Your mind is being manipulated right now. Here are 3 dark psychology secrets that control your behavior every single day.",
    "scenes": [
        {"scene_id": 1, "narration": "Your mind is being manipulated right now.", "visual_keyword": "dark mysterious eye close up", "estimated_seconds": 4},
        {"scene_id": 2, "narration": "Here are 3 dark psychology secrets.", "visual_keyword": "shadowy figure in hallway", "estimated_seconds": 5},
    ],
    "tags": ["psychology", "darkpsychology", "shorts"],
    "description": "Discover the dark psychology secrets that control your mind #shorts #psychology"
}
script = ScriptContract(**sample)
print(f"      Validated: {script.title[:40]}...")
print(f"      Duration: {script.total_estimated_duration}s, Scenes: {len(script.scenes)}")

json_str = script.to_json_str()
restored = ScriptContract.from_json_str(json_str)
assert restored.title == script.title, "Round-trip failed!"
print("      Serialization round-trip - OK")

try:
    ScriptContract(**{"title": "hi", "hook": "x", "full_narration": "x", "scenes": [], "tags": [], "description": "x"})
    print("      BAD: Should have raised ValidationError!")
    sys.exit(1)
except Exception:
    print("      Rejected bad data - OK")
print()

# Commit 0.2
from app.database.models import get_db, VideoJob, User, init_db
print("[0.2] models.py (WAL + get_db) - OK")

# Commit 0.3
from app.bot.handlers import video_callback
print("[0.3] handlers.py (video_callback) - OK")

# Commit 0.4
from app.agents.voice_agent import VoiceAgent
import inspect
source = inspect.getsource(VoiceAgent)
assert "asyncio.run(" not in source, "asyncio.run still present in VoiceAgent!"
print("[0.4] voice_agent.py (async fix) - OK")

# Commit 0.4b
from app.agents.content_agent import ContentAgent
sig = inspect.signature(ContentAgent.generate_script)
ret = sig.return_annotation
print(f"[0.4b] content_agent.py returns {ret.__name__} - OK")
print()

# Commit 1.1
from app.services.pipeline import VideoPipeline
p = VideoPipeline(job_id=999)
print("[1.1] pipeline.py - OK")

# Commit 1.2
from app.utils.ffmpeg_helper import (
    image_to_video, trim_video, concat_videos,
    mux_audio_video, burn_subtitles, validate_output, is_ffmpeg_installed
)
ffmpeg_status = "INSTALLED" if is_ffmpeg_installed() else "NOT INSTALLED"
print(f"[1.2] ffmpeg_helper.py (6 render functions) - OK [FFmpeg: {ffmpeg_status}]")

# Commit 1.3
from app.agents.video_agent import VideoAgent
va = VideoAgent()
print("[1.3] video_agent.py - OK")

print()
print("=" * 60)
print("ALL COMMITS VERIFIED - Import chain fully working!")
print("=" * 60)
