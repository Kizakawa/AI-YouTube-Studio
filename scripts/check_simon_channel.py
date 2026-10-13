"""
Kiểm tra Simon Channel & các kênh hoạt hình/3D kể chuyện cảm động, chữa lành, bài học cuộc sống
"""
import sys
import os
import json
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from app.utils.security import load_encrypted_json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

def get_yt_service():
    tok_paths = [
        "credentials/channels/ch_wild_odyssey/youtube_token.json",
        "credentials/channels/ucsrjiil67qxidtxt70z-iwa/youtube_token.json",
        "credentials/channels/ch_paws_hearts_rescue/youtube_token.json"
    ]
    for p in tok_paths:
        if os.path.exists(p):
            try:
                t = load_encrypted_json(p)
                creds = Credentials.from_authorized_user_info(t)
                yt = build("youtube", "v3", credentials=creds)
                yt.channels().list(mine=True, part="id").execute()
                return yt
            except Exception:
                continue
    return None

def main():
    yt = get_yt_service()
    # Search SIMON Channel shorts
    res = yt.search().list(
        q="SIMON Channel chuyện cảm động #shorts",
        part="snippet",
        regionCode="VN",
        relevanceLanguage="vi",
        type="video",
        videoDuration="short",
        maxResults=8,
        order="viewCount"
    ).execute()

    v_ids = [it["id"]["videoId"] for it in res.get("items", [])]
    v_res = yt.videos().list(id=",".join(v_ids), part="snippet,statistics").execute()
    print("--- SIMON Channel Chuyện Cảm Động ---")
    for v in v_res.get("items", []):
        st = v["statistics"]
        sn = v["snippet"]
        print(f"{int(st.get('viewCount', 0)):,} views | {int(st.get('likeCount', 0)):,} likes | {sn.get('title')} ({v['id']})")

if __name__ == "__main__":
    main()
