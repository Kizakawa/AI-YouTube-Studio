"""
Khảo sát các kênh hoạt hình AI, câu chuyện chữa lành/tâm lý tại VN
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

def search_query(yt, query, max_results=6):
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

        video_ids = [item["id"]["videoId"] for item in res.get("items", []) if "videoId" in item.get("id", {})]
        if not video_ids:
            return []

        v_res = yt.videos().list(
            id=",".join(video_ids),
            part="snippet,statistics"
        ).execute()

        channel_ids = list(set([v["snippet"]["channelId"] for v in v_res.get("items", [])]))
        c_stats_map = {}
        if channel_ids:
            c_res = yt.channels().list(
                id=",".join(channel_ids[:50]),
                part="snippet,statistics"
            ).execute()
            for c in c_res.get("items", []):
                c_stats_map[c["id"]] = {
                    "subs": int(c.get("statistics", {}).get("subscriberCount", 0)),
                    "views": int(c.get("statistics", {}).get("viewCount", 0))
                }

        results = []
        for v in v_res.get("items", []):
            snip = v["snippet"]
            stat = v.get("statistics", {})
            ch_id = snip.get("channelId")
            results.append({
                "video_id": v["id"],
                "video_url": f"https://www.youtube.com/shorts/{v['id']}",
                "title": snip.get("title"),
                "channel_title": snip.get("channelTitle"),
                "channel_subs": c_stats_map.get(ch_id, {}).get("subs", 0),
                "published_at": snip.get("publishedAt")[:10],
                "views": int(stat.get("viewCount", 0)),
                "likes": int(stat.get("likeCount", 0)),
                "comments": int(stat.get("commentCount", 0)),
            })
        results.sort(key=lambda x: x["views"], reverse=True)
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
        "hoạt hình AI #shorts",
        "phim hoạt hình ngắn ý nghĩa cuộc sống #shorts",
        "hoạt hình chữa lành tâm trạng mỗi ngày #shorts",
        "bài học làm người hoạt hình #shorts",
        "an ủi động viên hoạt hình #shorts",
        "câu chuyện cảm động hoạt hình 3d #shorts"
    ]

    all_res = {}
    for q in queries:
        print(f"\n--- Search: {q} ---")
        items = search_query(yt, q, max_results=5)
        all_res[q] = items
        for it in items:
            print(f"  {it['views']:,} views | {it['likes']:,} likes | {it['channel_title']} ({it['channel_subs']:,} subs): {it['title']}")

    with open("storage/temp/youtube_vn_ai_animation_data.json", "w", encoding="utf-8") as f:
        json.dump(all_res, f, indent=2, ensure_ascii=False)
    print("Done!")

if __name__ == "__main__":
    main()
