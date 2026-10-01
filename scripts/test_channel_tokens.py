import yaml, json, os
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from app.utils.security import load_encrypted_json

with open('config/channels.yaml', 'r', encoding='utf-8') as f:
    cfg = yaml.safe_load(f)

import sys
sys.stdout.reconfigure(encoding='utf-8')

for ch in cfg.get('channels', []):
    cid = ch.get('id')
    name = ch.get('name')
    token_p = Path(f'credentials/channels/{cid}/youtube_token.json')
    if not token_p.exists():
        print(f"[-] {cid}: No token file")
        continue
    token_data = load_encrypted_json(token_p)
    try:
        creds = Credentials.from_authorized_user_info(token_data)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        yt = build('youtube', 'v3', credentials=creds)
        res = yt.channels().list(mine=True, part='snippet,statistics').execute()
        items = res.get('items', [])
        if items:
            title = items[0]['snippet']['title']
            subs = items[0]['statistics'].get('subscriberCount', '0')
            print(f"[OK] {cid} ({name}) -> LIVE: '{title}' (Subs: {subs})")
        else:
            print(f"[WARN] {cid} ({name}) -> Connected, but mine=True returned 0 channels")
    except Exception as e:
        print(f"[EXPIRED/ERROR] {cid} ({name}) -> {type(e).__name__}: {e}")

