"""
Phân tích chuyên sâu các kênh và video HOẠT HÌNH CHỮA LÀNH / TÂM LÝ AN ỦI / ĐỘNG LỰC tại Việt Nam
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

def search_detailed_healing(yt, query, max_results=10):
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
            part="snippet,statistics,contentDetails"
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
                    "title": c.get("snippet", {}).get("title"),
                    "subs": int(c.get("statistics", {}).get("subscriberCount", 0)),
                    "total_views": int(c.get("statistics", {}).get("viewCount", 0)),
                    "video_count": int(c.get("statistics", {}).get("videoCount", 0)),
                    "custom_url": c.get("snippet", {}).get("customUrl", "")
                }

        results = []
        for v in v_res.get("items", []):
            snip = v["snippet"]
            stat = v.get("statistics", {})
            ch_id = snip.get("channelId")
            ch_info = c_stats_map.get(ch_id, {})
            results.append({
                "video_id": v["id"],
                "video_url": f"https://www.youtube.com/shorts/{v['id']}",
                "title": snip.get("title"),
                "channel_id": ch_id,
                "channel_title": snip.get("channelTitle"),
                "channel_custom_url": ch_info.get("custom_url", ""),
                "channel_subs": ch_info.get("subs", 0),
                "published_at": snip.get("publishedAt")[:10],
                "views": int(stat.get("viewCount", 0)),
                "likes": int(stat.get("likeCount", 0)),
                "comments": int(stat.get("commentCount", 0)),
                "tags": v.get("snippet", {}).get("tags", [])[:8]
            })
        results.sort(key=lambda x: x["views"], reverse=True)
        return results
    except Exception as e:
        print(f"Error query '{query}': {e}")
        return []

def main():
    yt = get_yt_service()
    if not yt:
        print("Cannot connect to YT API")
        return

    queries = [
        ("Hoạt hình chữa lành tâm hồn", "hoạt hình chữa lành tâm hồn #shorts"),
        ("Câu chuyện cuộc sống ý nghĩa hoạt hình", "câu chuyện ý nghĩa cuộc sống hoạt hình #shorts"),
        ("Lời an ủi vỗ về tâm trạng hoạt họa", "lời an ủi chữa lành #shorts"),
        ("Hoạt hình truyền cảm hứng động lực", "truyền cảm hứng động lực hoạt hình #shorts"),
        ("Triết lý buông bỏ an yên hoạt hình", "an yên chữa lành triết lý sống #shorts")
    ]

    all_data = {}
    for label, q in queries:
        print(f"\n=======================================================")
        print(f"🔎 QUÉT: {label} (Query: '{q}')")
        print(f"=======================================================")
        items = search_detailed_healing(yt, q, max_results=8)
        all_data[label] = items
        for i, item in enumerate(items):
            like_rate = (item['likes'] / item['views'] * 100) if item['views'] > 0 else 0
            print(f"[{i+1}] {item['views']:,} views | {item['likes']:,} likes ({like_rate:.2f}%) | {item['comments']:,} cmt")
            print(f"    Tiêu đề: {item['title']}")
            print(f"    Kênh: {item['channel_title']} ({item['channel_subs']:,} subs) | Đăng: {item['published_at']}")
            print(f"    Link: {item['video_url']}")

    with open("storage/temp/youtube_vn_detailed_healing.json", "w", encoding="utf-8") as f:
        json.dump(all_data, f, indent=2, ensure_ascii=False)
    print("\n✅ Saved to storage/temp/youtube_vn_detailed_healing.json")

if __name__ == "__main__":
    main()
