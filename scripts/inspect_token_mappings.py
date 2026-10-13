import sys, os
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from app.utils.security import load_encrypted_json

target_channels = ['ch_paws_hearts_rescue', 'ch_wild_odyssey', 'ch_odisseia_selvagem', 'ch_misterios_do_mundo']

for ch in target_channels:
    p = Path(f'credentials/channels/{ch}/youtube_token.json')
    if not p.exists():
        print(f"{ch}: No token file")
        continue
    data = load_encrypted_json(str(p))
    if not data:
        print(f"{ch}: Failed to load data")
        continue
    creds = Credentials(
        token=data.get('token'),
        refresh_token=data.get('refresh_token'),
        token_uri=data.get('token_uri', 'https://oauth2.googleapis.com/token'),
        client_id=data.get('client_id'),
        client_secret=data.get('client_secret'),
        scopes=data.get('scopes')
    )
    svc = build('youtube', 'v3', credentials=creds)
    res = svc.channels().list(part='snippet', mine=True).execute()
    item = res['items'][0] if res.get('items') else {}
    cid = item.get("id")
    title = item.get("snippet", {}).get("title")
    print(f"{ch} -> channel id: {cid}, title: {title}")
