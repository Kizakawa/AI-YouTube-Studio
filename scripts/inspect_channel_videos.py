import sys
import json
from pathlib import Path

# Ensure UTF-8 output
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.publisher_agent import PublisherAgent

ch = {
    'id': 'ucsrjiil67qxidtxt70z-iwa',
    'name': 'The mysteries of the world',
    'niche': 'deep_sea_earth_mysteries',
    'amazon_tag': 'kizakawa-20'
}

publisher = PublisherAgent(channel_config=ch)
service = publisher.service

if not service:
    print("Error: Could not connect to YouTube API")
    sys.exit(1)

# 1. Get channel's uploads playlist ID
res = service.channels().list(mine=True, part="contentDetails,snippet").execute()
items = res.get("items", [])
if not items:
    print("No channel found for current credentials")
    sys.exit(1)

channel_info = items[0]
title = channel_info["snippet"]["title"]
uploads_playlist_id = channel_info["contentDetails"]["relatedPlaylists"]["uploads"]
print(f"Connected Channel: {title}")
print(f"Uploads Playlist ID: {uploads_playlist_id}")

# 2. Get all uploaded videos
playlist_items_req = service.playlistItems().list(
    playlistId=uploads_playlist_id,
    part="snippet,contentDetails",
    maxResults=50
)
playlist_items_res = playlist_items_req.execute()
videos = playlist_items_res.get("items", [])
print(f"Total videos found: {len(videos)}")

for v in videos:
    vid_id = v["contentDetails"]["videoId"]
    v_title = v["snippet"]["title"]
    published_at = v["snippet"]["publishedAt"]
    desc = v["snippet"]["description"]
    has_amazon = "amazon.com" in desc.lower() or "kizakawa" in desc.lower()
    print(f"\n--- [{vid_id}] {v_title} ({published_at}) ---")
    print(f"Has Amazon Link: {has_amazon}")
    print("Description preview (first 100 chars):", repr(desc[:100]))
