import sys
import yaml
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.publisher_agent import PublisherAgent

cfg_path = Path("config/channels.yaml")
with open(cfg_path, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

print("=" * 60)
print("KIỂM TRA TẤT CẢ 4 KÊNH YOUTUBE VÀ DANH SÁCH VIDEO HIỆN CÓ")
print("=" * 60)

for acc in cfg.get("google_accounts", []):
    acc_id = acc.get("id")
    email = acc.get("email_label")
    print(f"\n📁 Tài khoản: {acc_id} ({email})")

    for ch in acc.get("brand_channels", []):
        ch_id = ch.get("id")
        ch_name = ch.get("name")
        niche = ch.get("niche")
        tag = ch.get("amazon_tag")
        ch["google_account"] = acc_id

        print(f"\n  📺 Kênh: {ch_name} (ID: {ch_id})")
        print(f"     Ngách: {niche} | Amazon Tag: {tag}")

        pub = PublisherAgent(channel_config=ch)
        srv = pub.service

        if not srv:
            print("     ❌ Token chưa sẵn sàng hoặc không kết nối được YouTube API")
            continue

        try:
            res = srv.channels().list(mine=True, part="snippet,contentDetails").execute()
            items = res.get("items", [])
            if not items:
                print("     ⚠️ Không tìm thấy thông tin kênh trên YouTube")
                continue

            yt_title = items[0]["snippet"]["title"]
            uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
            print(f"     ✅ YouTube API OK -> Tên thật trên YouTube: '{yt_title}'")

            # Lấy tất cả video (hỗ trợ phân trang nếu > 50 videos)
            videos = []
            page_token = None
            while True:
                pl_req = srv.playlistItems().list(
                    playlistId=uploads_id,
                    part="snippet,contentDetails",
                    maxResults=50,
                    pageToken=page_token
                )
                pl_res = pl_req.execute()
                videos.extend(pl_res.get("items", []))
                page_token = pl_res.get("nextPageToken")
                if not page_token:
                    break

            print(f"     🎥 Tổng số video đã tải lên: {len(videos)}")
            for v in videos[:5]:
                vid_id = v["contentDetails"]["videoId"]
                v_title = v["snippet"]["title"]
                print(f"        - [{vid_id}] {v_title}")
            if len(videos) > 5:
                print(f"        ... và {len(videos) - 5} video khác")

        except Exception as e:
            print(f"     ❌ Lỗi truy vấn kênh: {e}")
