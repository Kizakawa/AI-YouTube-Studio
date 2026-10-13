"""
Khảo sát và trích xuất dữ liệu THỰC TẾ 100% từ YouTube Data API (regionCode='VN', relevanceLanguage='vi')
Chuyên sâu ngách: HOẠT HỌA / HOẠT HÌNH + TÂM LÝ AN ỦI, ĐỘNG VIÊN, CHỮA LÀNH, BÀI HỌC CUỘC SỐNG.
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

def search_niche_shorts(yt, query, max_results=8, order="viewCount"):
    try:
        res = yt.search().list(
            q=f"{query} #shorts",
            part="snippet",
            regionCode="VN",
            relevanceLanguage="vi",
            type="video",
            videoDuration="short",
            maxResults=max_results,
            order=order
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
                "channel_total_views": ch_info.get("total_views", 0),
                "published_at": snip.get("publishedAt")[:10],
                "views": int(stat.get("viewCount", 0)),
                "likes": int(stat.get("likeCount", 0)),
                "comments": int(stat.get("commentCount", 0)),
                "description": snip.get("description", "")[:200]
            })
        results.sort(key=lambda x: x["views"], reverse=True)
        return results
    except Exception as e:
        print(f"Lỗi search '{query}': {e}")
        return []

def main():
    yt = get_yt_service()
    if not yt:
        print("❌ Không kết nối được YouTube API!")
        return

    queries = [
        ("Hoạt hình Chữa lành & Bình yên", "hoạt hình chữa lành"),
        ("Hoạt hình Động viên & Vượt qua áp lực", "hoạt hình động lực cuộc sống"),
        ("Hoạt hình Bài học làm người & Triết lý", "hoạt hình bài học cuộc sống"),
        ("Hoạt hình Tâm lý & Chữa lành tâm hồn", "hoạt hình tâm lý an ủi"),
        ("Hoạt họa Cảm động & Tình cảm gia đình", "hoạt hình ngắn cảm động rơi nước mắt"),
        ("Lời khuyên cuộc sống Hoạt họa AI", "lời khuyên cuộc sống hoạt hình")
    ]

    all_data = {}
    print("=" * 90)
    print("🔍 ĐANG TRUY VẤN DỮ LIỆU THỰC TẾ YOUTUBE API: NGÁCH HOẠT HÌNH + TÂM LÝ / CHỮA LÀNH / ĐỘNG LỰC")
    print("=" * 90)

    for cat_name, q in queries:
        print(f"\n📡 Đang quét: '{cat_name}' (query: '{q} #shorts')...")
        vids = search_niche_shorts(yt, q, max_results=6)
        all_data[cat_name] = vids
        for idx, v in enumerate(vids):
            like_rate = (v['likes'] / v['views'] * 100) if v['views'] > 0 else 0
            print(f"  [{idx+1}] {v['views']:,} views | {v['likes']:,} likes ({like_rate:.2f}%) | {v['comments']:,} cmt")
            print(f"      Tiêu đề: {v['title']}")
            print(f"      Kênh: {v['channel_title']} ({v['channel_subs']:,} subs) | Đăng: {v['published_at']}")
            print(f"      Link: {v['video_url']}")

    out_file = Path("storage/temp/youtube_vn_healing_animation.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_data, f, indent=2, ensure_ascii=False)
    print(f"\n✅ Đã lưu kết quả khảo sát thực tế vào: {out_file}")

if __name__ == "__main__":
    main()
