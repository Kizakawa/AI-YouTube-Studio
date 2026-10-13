"""
Nghiên cứu dữ liệu THỰC TẾ từ YouTube Data API cho thị trường Việt Nam (regionCode='VN', relevanceLanguage='vi').
Lấy:
1. Chart mostPopular tại Việt Nam
2. Các ngách nội dung Shorts phổ biến nhất tại VN: Lịch sử, Vụ án, Sự thật thú vị, Kinh dị/Tâm linh, Đời sống/Mẹo
3. Thống kê view, like, comment, tên kênh, số sub thực tế
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

def search_niche_shorts(yt, query, max_results=6):
    try:
        res = yt.search().list(
            q=f"{query} #shorts",
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
                    "subs": int(c.get("statistics", {}).get("subscriberCount", 0)),
                    "total_views": int(c.get("statistics", {}).get("viewCount", 0))
                }

        results = []
        for v in v_res.get("items", []):
            snip = v["snippet"]
            stat = v.get("statistics", {})
            ch_id = snip.get("channelId")
            ch_stat = c_stats_map.get(ch_id, {})
            results.append({
                "video_id": v["id"],
                "title": snip.get("title"),
                "channel_title": snip.get("channelTitle"),
                "channel_subs": ch_stat.get("subs", 0),
                "published_at": snip.get("publishedAt")[:10],
                "views": int(stat.get("viewCount", 0)),
                "likes": int(stat.get("likeCount", 0)),
                "comments": int(stat.get("commentCount", 0)),
            })
        results.sort(key=lambda x: x["views"], reverse=True)
        return results
    except Exception as e:
        print(f"Lỗi search '{query}': {e}")
        return []

def main():
    yt = get_yt_service()
    if not yt:
        print("Không kết nối được YouTube API!")
        return

    niches = [
        ("Lịch sử Việt Nam & Chiến trận", "lịch sử việt nam"),
        ("Kỳ án & Phá án trinh thám", "kỳ án có thật"),
        ("Sự thật thú vị & Khám phá khoa học", "sự thật thú vị ít người biết"),
        ("Truyện kinh dị & Tâm linh dân gian", "chuyện tâm linh bí ẩn"),
        ("Cảnh báo lừa đảo & Mẹo tài chính", "chiêu trò lừa đảo cảnh giác"),
        ("Thế giới động vật hoang dã VN", "động vật săn mồi đối đầu"),
    ]

    all_data = {}
    print("=" * 80)
    print("🔍 ĐANG TRUY VẤN DỮ LIỆU THỰC TẾ TRỰC TIẾP TỪ YOUTUBE API TẠI VIỆT NAM (regionCode=VN)...")
    print("=" * 80)

    for niche_name, q in niches:
        print(f"\n📡 Đang quét ngách: '{niche_name}' (query: '{q} #shorts')...")
        vids = search_niche_shorts(yt, q, max_results=5)
        all_data[niche_name] = vids
        for idx, v in enumerate(vids):
            print(f"  [{idx+1}] {v['views']:,} views | {v['likes']:,} likes | Kênh: {v['channel_title']} ({v['channel_subs']:,} subs)")
            print(f"      Tiêu đề: {v['title']}")
            print(f"      Ngày đăng: {v['published_at']} | ID: {v['video_id']}")

    out_file = Path("storage/temp/youtube_vn_real_data.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_data, f, indent=2, ensure_ascii=False)
    print(f"\n✅ Đã lưu toàn bộ dữ liệu thực tế vào: {out_file}")

if __name__ == "__main__":
    main()
