"""
Kiểm tra chi tiết kênh Lạc Mây (@lacmay-healing) & các video chữa lành hàng triệu view
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
    # Search videos of channel Lạc Mây
    res = yt.search().list(
        q="Lạc Mây chữa lành #shorts",
        part="snippet",
        regionCode="VN",
        relevanceLanguage="vi",
        type="video",
        videoDuration="short",
        maxResults=10,
        order="viewCount"
    ).execute()
    v_ids = [it["id"]["videoId"] for it in res.get("items", []) if "videoId" in it.get("id", {})]
    if v_ids:
        v_res = yt.videos().list(id=",".join(v_ids), part="snippet,statistics").execute()
        print("=== Top Video Lạc Mây (Chữa lành hoạt hình) ===")
        for v in v_res.get("items", []):
            st = v["statistics"]
            sn = v["snippet"]
            views = int(st.get('viewCount', 0))
            likes = int(st.get('likeCount', 0))
            like_rate = (likes / views * 100) if views > 0 else 0
            print(f"  {views:,} views | {likes:,} likes ({like_rate:.2f}%) | {sn.get('title')} ({v['id']})")

if __name__ == "__main__":
    main()
