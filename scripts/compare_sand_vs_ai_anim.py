"""
Khảo sát thực tế YouTube API:
1. Hoạt hình Điêu khắc cát / Sa điêu (Shadiao animation - 沙雕动画)
2. Hoạt hình đồ họa AI (AI 3D / AI Pixar / AI Anime)
So sánh lượng view, ngày đăng gần đây (2025-2026) xem thể loại nào đang chiếm ưu thế!
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

def query_yt(yt, query, max_results=8, order="relevance"):
    try:
        res = yt.search().list(
            q=query,
            part="snippet",
            regionCode="VN",
            relevanceLanguage="vi",
            type="video",
            videoDuration="short",
            maxResults=max_results,
            order=order
        ).execute()

        video_ids = [it["id"]["videoId"] for it in res.get("items", []) if "videoId" in it.get("id", {})]
        if not video_ids:
            return []

        v_res = yt.videos().list(id=",".join(video_ids), part="snippet,statistics").execute()
        results = []
        for v in v_res.get("items", []):
            st = v["statistics"]
            sn = v["snippet"]
            views = int(st.get('viewCount', 0))
            likes = int(st.get('likeCount', 0))
            results.append({
                "id": v["id"],
                "title": sn.get("title"),
                "channel": sn.get("channelTitle"),
                "date": sn.get("publishedAt")[:10],
                "views": views,
                "likes": likes,
                "like_rate": (likes / views * 100) if views > 0 else 0
            })
        return results
    except Exception as e:
        print(f"Error {query}: {e}")
        return []

def main():
    yt = get_yt_service()
    if not yt:
        print("Cannot connect to YT")
        return

    searches = [
        ("Hoạt hình điêu khắc cát / Sa điêu (Mới nhất/Phổ biến)", "hoạt hình điêu khắc cát #shorts"),
        ("Hoạt hình sa điêu hài hước (Mới nhất)", "hoạt hình sa điêu #shorts"),
        ("Hoạt hình AI 3D đồ họa", "hoạt hình 3d ai #shorts"),
        ("Hoạt hình AI cảm động chữa lành", "hoạt hình ai cảm động #shorts")
    ]

    for label, q in searches:
        print(f"\n=======================================================")
        print(f"🔍 {label} (Query: '{q}')")
        print(f"=======================================================")
        # Check by viewCount and by date
        vids_by_views = query_yt(yt, q, max_results=5, order="viewCount")
        print("--- Top Views ---")
        for v in vids_by_views:
            print(f"  {v['views']:,} views | {v['likes']:,} likes ({v['like_rate']:.2f}%) | {v['date']} | {v['channel']}: {v['title']}")

        vids_by_date = query_yt(yt, q, max_results=4, order="date")
        print("--- Video Mới Nhất Gần Đây ---")
        for v in vids_by_date:
            print(f"  {v['views']:,} views | {v['likes']:,} likes | {v['date']} | {v['channel']}: {v['title']}")

if __name__ == "__main__":
    main()
