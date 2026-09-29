import sys
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from app.utils.security import load_encrypted_json

# Test with channel_maya token first
token_maya = load_encrypted_json("credentials/channels/channel_maya/youtube_token.json")
creds = Credentials.from_authorized_user_info(token_maya)
srv = build("youtube", "v3", credentials=creds)

vid = "Ek4mr52jcRI"
try:
    res = srv.videos().list(part="snippet", id=vid).execute()
    items = res.get("items", [])
    if items:
        print(f"channel_maya token: videos().list SUCCESS for {vid}")
        snippet = items[0]["snippet"]
        print(f"Current description: {repr(snippet.get('description', '')[:80])}")
        
        # Test if update works
        print("Testing videos().update with channel_maya token...")
        srv.videos().update(part="snippet", body={"id": vid, "snippet": snippet}).execute()
        print("✅ videos().update SUCCESS with channel_maya token!")
    else:
        print("Video item empty")
except Exception as e:
    print(f"❌ Error with channel_maya token: {e}")

# If that failed, test with account_1 master token
print("\n--- Testing with account_1 master token ---")
token_acc1 = load_encrypted_json("credentials/accounts/account_1/youtube_token.json")
creds1 = Credentials.from_authorized_user_info(token_acc1)
srv1 = build("youtube", "v3", credentials=creds1)

try:
    res = srv1.videos().list(part="snippet", id=vid).execute()
    items = res.get("items", [])
    if items:
        print(f"account_1 token: videos().list SUCCESS for {vid}")
        snippet = items[0]["snippet"]
        print("Testing videos().update with account_1 token...")
        srv1.videos().update(part="snippet", body={"id": vid, "snippet": snippet}).execute()
        print("✅ videos().update SUCCESS with account_1 token!")
except Exception as e:
    print(f"❌ Error with account_1 token: {e}")
