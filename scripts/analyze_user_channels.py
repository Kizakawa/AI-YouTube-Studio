"""
Phân tích toàn diện 4 kênh YouTube của người dùng:
1. Psychology of the world (channel_maya)
2. The mysteries of the world (ucsrjiil67qxidtxt70z-iwa)
3. Paws & Hearts Rescue (ch_paws_hearts_rescue)
4. Wild Odyssey (ch_wild_odyssey)
"""
import sys
import os
import json
from pathlib import Path
from datetime import datetime, timezone

project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

from app.utils.security import load_encrypted_json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

CHANNELS = [
    {
        "id": "channel_maya",
        "youtube_channel_id": "UC1_lVpIlXQ-J4sFrqozfrKw",
        "name": "Psychology of the world",
        "niche": "psychology",
        "account": "account_1",
        "tok": "credentials/channels/channel_maya/youtube_token.json",
    },
    {
        "id": "ucsrjiil67qxidtxt70z-iwa",
        "youtube_channel_id": "UCsRjIIl67QxIDtXT70z-iwA",
        "name": "The mysteries of the world",
        "niche": "deep_sea_earth_mysteries",
        "account": "account_1",
        "tok": "credentials/channels/ucsrjiil67qxidtxt70z-iwa/youtube_token.json",
    },
    {
        "id": "ch_paws_hearts_rescue",
        "youtube_channel_id": "UCv-2dYUQKx1u0CjqdmzDHTA",
        "name": "Paws & Hearts Rescue",
        "niche": "animal_rescue",
        "account": "acount_2",
        "tok": "credentials/channels/ch_paws_hearts_rescue/youtube_token.json",
    },
    {
        "id": "ch_wild_odyssey",
        "youtube_channel_id": "UClsJHbVjjXI-j16VzXXs68A",
        "name": "Wild Odyssey",
        "niche": "wildlife_predators",
        "account": "acount_2",
        "tok": "credentials/channels/ch_wild_odyssey/youtube_token.json",
    },
]

def get_shared_service():
    for ch in CHANNELS:
        tok_path = Path(ch["tok"])
        if tok_path.exists():
            try:
                t_data = load_encrypted_json(str(tok_path))
                creds = Credentials.from_authorized_user_info(t_data)
                yt = build("youtube", "v3", credentials=creds)
                yt.channels().list(mine=True, part="id").execute()
                return yt
            except Exception:
                continue
    return None

def analyze_channel(ch_info, fallback_yt=None):
    tok_path = Path(ch_info["tok"])
    yt = None
    is_owner = False

    if tok_path.exists():
        try:
            t_data = load_encrypted_json(str(tok_path))
            creds = Credentials.from_authorized_user_info(t_data)
            temp_yt = build("youtube", "v3", credentials=creds)
            # test mine=True
            c_test = temp_yt.channels().list(mine=True, part="id").execute()
            if c_test.get("items"):
                yt = temp_yt
                is_owner = True
        except Exception as e_tok:
            pass

    if yt is None:
        yt = fallback_yt

    if yt is None:
        return {"error": "Không thể kết nối YouTube API (hết token khả dụng)"}

    try:
        if is_owner:
            c_res = yt.channels().list(mine=True, part="snippet,statistics,contentDetails,brandingSettings").execute()
        else:
            c_res = yt.channels().list(id=ch_info["youtube_channel_id"], part="snippet,statistics,contentDetails,brandingSettings").execute()

        items = c_res.get("items", [])
        if not items:
            return {"error": f"Không tìm thấy channel {ch_info['name']}"}

        c = items[0]
        channel_id = c.get("id")
        snip = c.get("snippet", {})
        stats = c.get("statistics", {})
        branding = c.get("brandingSettings", {})
        uploads_id = c.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if not uploads_id and channel_id and channel_id.startswith("UC"):
            uploads_id = "UU" + channel_id[2:]

        videos = []
        if uploads_id:
            try:
                p_res = yt.playlistItems().list(
                    playlistId=uploads_id,
                    part="snippet,status,contentDetails",
                    maxResults=30
                ).execute()
                
                v_ids = [item.get("contentDetails", {}).get("videoId") for item in p_res.get("items", []) if item.get("contentDetails", {}).get("videoId")]
                
                v_stats_map = {}
                if v_ids:
                    v_res = yt.videos().list(
                        id=",".join(v_ids),
                        part="snippet,statistics,status,contentDetails"
                    ).execute()
                    for v in v_res.get("items", []):
                        v_stats_map[v["id"]] = v

                for item in p_res.get("items", []):
                    vid_id = item.get("contentDetails", {}).get("videoId")
                    v_detail = v_stats_map.get(vid_id, {})
                    v_snip = v_detail.get("snippet", item.get("snippet", {}))
                    v_stat = v_detail.get("statistics", {})
                    v_status = v_detail.get("status", item.get("status", {}))
                    
                    videos.append({
                        "video_id": vid_id,
                        "title": v_snip.get("title"),
                        "description": v_snip.get("description", ""),
                        "published_at": v_snip.get("publishedAt"),
                        "privacy_status": v_status.get("privacyStatus"),
                        "publish_at": v_status.get("publishAt"),
                        "views": int(v_stat.get("viewCount", 0)),
                        "likes": int(v_stat.get("likeCount", 0)),
                        "comments": int(v_stat.get("commentCount", 0)),
                        "tags": v_snip.get("tags", []),
                        "has_shorts_tag": "#shorts" in (v_snip.get("title", "") + " " + v_snip.get("description", "")).lower(),
                        "duration": v_detail.get("contentDetails", {}).get("duration"),
                    })
            except Exception as e_play:
                print(f"  Warning fetching videos for {ch_info['name']}: {e_play}")

        return {
            "channel_id": channel_id,
            "title": snip.get("title"),
            "custom_url": snip.get("customUrl"),
            "country": snip.get("country"),
            "description": snip.get("description"),
            "created_at": snip.get("publishedAt"),
            "subscribers": int(stats.get("subscriberCount", 0)),
            "total_views": int(stats.get("viewCount", 0)),
            "total_videos": int(stats.get("videoCount", 0)),
            "is_token_owner": is_owner,
            "videos": videos
        }
    except Exception as e:
        return {"error": str(e)}

def main():
    shared_yt = get_shared_service()
    results = {}
    for ch in CHANNELS:
        print(f"Fetching data for: {ch['name']}...")
        data = analyze_channel(ch, fallback_yt=shared_yt)
        data["niche"] = ch["niche"]
        data["account"] = ch["account"]
        results[ch["id"]] = data

    out_file = Path("storage/temp/channels_audit.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 75)
    print("📊 BÁO CÁO PHÂN TÍCH TOÀN BỘ 4 KÊNH YOUTUBE:")
    print("=" * 75)
    for ch_id, d in results.items():
        if "error" in d:
            print(f"❌ {ch_id}: Lỗi -> {d['error']}")
            continue
        v_list = d.get("videos", [])
        public_v = [v for v in v_list if v.get("privacy_status") == "public"]
        sched_v = [v for v in v_list if v.get("privacy_status") == "private" and v.get("publish_at")]
        priv_v = [v for v in v_list if v.get("privacy_status") == "private" and not v.get("publish_at")]
        owner_str = "🔑 OAuth Token Hợp lệ" if d.get("is_token_owner") else "⚠️ Token hết hạn (Lấy dữ liệu Public ID)"
        print(f"📺 Kênh: {d.get('title')} ({d.get('custom_url')}) | Ngách: {d.get('niche')} | [{owner_str}]")
        print(f"   👥 Subscribers: {d.get('subscribers'):,} | 👁️ Tổng Views: {d.get('total_views'):,} | 🎬 Tổng Video: {d.get('total_videos')}")
        print(f"   📹 Danh sách lấy mẫu ({len(v_list)} videos): {len(public_v)} Public, {len(sched_v)} Scheduled, {len(priv_v)} Private")
        if v_list:
            top_v = sorted(v_list, key=lambda x: x["views"], reverse=True)[:5]
            print("   🔥 Top video có view cao nhất:")
            for tv in top_v:
                print(f"      - [{tv['views']:,} views | {tv['likes']:,} likes | {tv['comments']} cmts] {tv['title']} (ID: {tv['video_id']})")
        print("-" * 75)

if __name__ == "__main__":
    main()
