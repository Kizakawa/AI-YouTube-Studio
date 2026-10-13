"""
Khảo sát thực tế: Các video Chữa lành / Podcast / Tâm sự trên YouTube Shorts & TikTok
Xem lượng view thực tế, tốc độ, cách giữ chân người xem của các kênh đạt hàng triệu view.
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

def query_yt(yt, query, max_results=6):
    try:
        res = yt.search().list(
            q=query,
            part="snippet",
            regionCode="VN",
            relevanceLanguage="vi",
            type="video",
            videoDuration="short",
            maxResults=max_results,
            order="viewCount"
        ).execute()

        video_ids = [it["id"]["videoId"] for it in res.get("items", []) if "videoId" in it.get("id", {})]
        if not video_ids:
            return []

        v_res = yt.videos().list(id=",".join(video_ids), part="snippet,statistics,contentDetails").execute()
        results = []
        for v in v_res.get("items", []):
            st = v["statistics"]
            sn = v["snippet"]
            dur = v.get("contentDetails", {}).get("duration", "")
            views = int(st.get('viewCount', 0))
            likes = int(st.get('likeCount', 0))
            results.append({
                "id": v["id"],
                "title": sn.get("title"),
                "channel": sn.get("channelTitle"),
                "views": views,
                "likes": likes,
                "like_rate": (likes / views * 100) if views > 0 else 0,
                "duration": dur,
                "date": sn.get("publishedAt")[:10]
            })
        return results
    except Exception as e:
        print(f"Error {query}: {e}")
        return []

def main():
    yt = get_yt_service()
    if not yt:
        print("No YT service")
        return

    queries = [
        ("Podcast chữa lành tâm sự viral", "podcast chữa lành #shorts"),
        ("Tâm sự đêm khuya viral", "tâm sự đêm khuya chạm đáy tim #shorts"),
        ("Lời nhắn gửi chữa lành TikTok", "nếu hôm nay bạn mệt mỏi tiktok #shorts"),
        ("Câu chuyện cuộc sống TikTok triệu view", "câu chuyện ý nghĩa cuộc sống tiktok #shorts")
    ]

    for label, q in queries:
        print(f"\n=======================================================")
        print(f"🔎 {label} (Query: '{q}')")
        print(f"=======================================================")
        items = query_yt(yt, q, max_results=5)
        for i, item in enumerate(items):
            print(f"  [{i+1}] {item['views']:,} views | {item['likes']:,} likes ({item['like_rate']:.2f}%) | {item['date']}")
            print(f"      Kênh: {item['channel']} | Thời lượng: {item['duration']}")
            print(f"      Tiêu đề: {item['title']}")
            print(f"      URL: https://www.youtube.com/shorts/{item['id']}")

if __name__ == "__main__":
    main()
