import sys
import yaml
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.publisher_agent import PublisherAgent

with open('config/channels.yaml', 'r', encoding='utf-8') as f:
    cfg = yaml.safe_load(f)

for acc in cfg.get('google_accounts', []):
    acc_id = acc.get('id')
    for ch in acc.get('brand_channels', []):
        ch['google_account'] = acc_id
        pub = PublisherAgent(channel_config=ch)
        srv = pub.service
        if srv:
            try:
                res = srv.channels().list(mine=True, part='snippet,statistics').execute()
                for item in res.get('items', []):
                    title = item['snippet']['title']
                    stats = item['statistics']
                    print(f"Kênh: {title:30} | VideoCount: {stats.get('videoCount'):>3} | Views: {stats.get('viewCount'):>5} | Subs: {stats.get('subscriberCount')}")
            except Exception as e:
                print(f"Kênh: {ch.get('name'):30} -> Lỗi: {e}")
        else:
            print(f"Kênh: {ch.get('name'):30} -> Chưa có service")
