import sys
from pathlib import Path
from datetime import datetime, timezone

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from app.agents.publisher_agent import PublisherAgent
from app.services.channel_manager import get_channel

target_channels = ['ch_paws_hearts_rescue', 'ch_wild_odyssey', 'ch_odisseia_selvagem', 'ch_misterios_do_mundo']

now_utc = datetime.now(timezone.utc)
print(f"Current UTC: {now_utc.isoformat()}")

for ch_id in target_channels:
    cfg = get_channel(ch_id)
    agent = PublisherAgent(channel_config=cfg)
    print(f"\n--- Checking {ch_id} ({cfg.get('name')}) ---")
    try:
        # Get channel's uploads playlist
        ch_resp = agent.service.channels().list(part='contentDetails', mine=True).execute()
        uploads_id = ch_resp['items'][0]['contentDetails']['relatedPlaylists']['uploads']
        
        # Get recent 10 videos
        pl_resp = agent.service.playlistItems().list(
            part='snippet,contentDetails',
            playlistId=uploads_id,
            maxResults=10
        ).execute()
        
        v_ids = [item['contentDetails']['videoId'] for item in pl_resp.get('items', [])]
        if v_ids:
            v_resp = agent.service.videos().list(
                part='snippet,status',
                id=','.join(v_ids)
            ).execute()
            
            for v in v_resp.get('items', []):
                vid = v['id']
                v_title = v['snippet']['title']
                v_status = v['status']['privacyStatus']
                pub_at = v['status'].get('publishAt')
                published_at = v['snippet'].get('publishedAt')
                print(f"  [{vid}] status: {v_status} | publishAt: {pub_at} | publishedAt: {published_at} | title: {v_title[:45]}")
        else:
            print("  No videos found in uploads playlist.")
    except Exception as e:
        print(f"  Error querying YouTube: {e}")
