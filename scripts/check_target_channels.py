import sys
import os
from pathlib import Path

# Add project root
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

for ch_id in target_channels:
    cfg = get_channel(ch_id)
    if not cfg:
        print(f"Config not found for {ch_id}")
        continue
    agent = PublisherAgent(channel_config=cfg)
    if not agent.service:
        print(f"❌ YouTube service NOT connected for {ch_id}")
        continue
    try:
        req = agent.service.channels().list(part='snippet,statistics', mine=True)
        res = req.execute()
        item = res['items'][0] if res.get('items') else {}
        snip = item.get('snippet', {})
        subs = item.get('statistics', {}).get('subscriberCount', '0')
        print(f"✅ {ch_id} ({cfg.get('name')}): channel_title='{snip.get('title')}', subs={subs}")
    except Exception as e:
        print(f"❌ {ch_id}: error: {e}")
