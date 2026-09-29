import os
import sys
import json
from pathlib import Path

# Ensure UTF-8 console output on Windows
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.publisher_agent import PublisherAgent
from app.services.monetization_engine import enrich_video_monetization

def retrofit_channel_videos(ch_config, dry_run=False):
    publisher = PublisherAgent(channel_config=ch_config)
    service = publisher.service

    if not service:
        print(f"❌ Kênh [{ch_config.get('name')}]: Không thể kết nối YouTube API.")
        return

    # 1. Lấy Uploads Playlist ID
    try:
        ch_res = service.channels().list(mine=True, part="contentDetails,snippet").execute()
        items = ch_res.get("items", [])
        if not items:
            print(f"❌ Kênh [{ch_config.get('name')}]: Không tìm thấy thông tin trên YouTube.")
            return

        channel_title = items[0]["snippet"]["title"]
        uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        print(f"\n🎬 Kênh YouTube: {channel_title} (ID: {ch_config.get('id')})")
        print(f"📦 Uploads Playlist: {uploads_playlist_id}")
    except Exception as e:
        print(f"❌ Kênh [{ch_config.get('name')}]: Lỗi xác thực hoặc không đủ quyền API: {e}")
        return

    # 2. Lấy danh sách video
    pl_req = service.playlistItems().list(
        playlistId=uploads_playlist_id,
        part="snippet,contentDetails",
        maxResults=50
    )
    pl_res = pl_req.execute()
    videos = pl_res.get("items", [])
    print(f"📊 Tìm thấy tổng cộng {len(videos)} video trên kênh.\n")

    updated_count = 0

    for idx, v in enumerate(videos, start=1):
        vid_id = v["contentDetails"]["videoId"]
        v_title = v["snippet"]["title"]
        print(f"[{idx}/{len(videos)}] Đang xử lý: [{vid_id}] {v_title}")

        # Lấy thông tin chi tiết của video (part=snippet)
        vid_detail = service.videos().list(part="snippet", id=vid_id).execute()
        if not vid_detail.get("items"):
            print(f"   ⚠️ Không thể lấy thông tin chi tiết video {vid_id}")
            continue

        snippet = vid_detail["items"][0]["snippet"]
        curr_desc = snippet.get("description", "")

        # Kiểm tra xem mô tả đã có link amazon chưa
        has_amazon = "amazon.com" in curr_desc.lower() or "kizakawa-20" in curr_desc.lower()

        enriched_desc, enriched_comment, prod_info = enrich_video_monetization(
            channel_config=ch_config,
            title=v_title,
            description=curr_desc,
            pinned_comment=None
        )

        print(f"   🎯 Sản phẩm ghép cặp: {prod_info.get('name')}")
        print(f"   🔗 Link: {prod_info.get('affiliate_url')}")

        if dry_run:
            print("   [DRY-RUN] Sẽ cập nhật mô tả và đăng bình luận.")
            continue

        # Cập nhật mô tả nếu chưa có link
        if not has_amazon:
            snippet["description"] = enriched_desc
            if not snippet.get("categoryId"):
                snippet["categoryId"] = "22"
            try:
                service.videos().update(
                    part="snippet",
                    body={
                        "id": vid_id,
                        "snippet": snippet
                    }
                ).execute()
                print("   ✅ Đã cập nhật Mô tả (Description) thành công!")
            except Exception as ex:
                print(f"   ❌ Lỗi khi cập nhật mô tả: {ex}")
        else:
            print("   ℹ️ Video đã có link Amazon trong mô tả.")

        # Đăng bình luận mồi nếu chưa có comment Amazon từ chủ kênh
        try:
            ct_res = service.commentThreads().list(
                part="snippet",
                videoId=vid_id,
                maxResults=20
            ).execute()
            has_comment = False
            for item in ct_res.get("items", []):
                t_comm = item["snippet"]["topLevelComment"]["snippet"]["textOriginal"]
                if "kizakawa" in t_comm or "amazon.com" in t_comm:
                    has_comment = True
                    break

            if not has_comment:
                comment_res = publisher.add_pinned_comment(vid_id, enriched_comment)
                if comment_res:
                    print("   💬 Đã đăng bình luận mồi đính kèm link Amazon!")
                else:
                    print("   ⚠️ Không thể đăng bình luận.")
            else:
                print("   ℹ️ Video đã có bình luận link Amazon.")
        except Exception as ex:
            print(f"   ⚠️ Lỗi kiểm tra / đăng bình luận: {ex}")

        updated_count += 1
        print()
        import time
        time.sleep(1.5)

    print(f"🎉 Hoàn tất xử lý kênh [{ch_config.get('name')}]: {updated_count}/{len(videos)} video!")

if __name__ == "__main__":
    import yaml
    is_dry = "--dry-run" in sys.argv
    cfg_path = Path("config/channels.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    for acc in cfg.get("google_accounts", []):
        acc_id = acc.get("id")
        for ch in acc.get("brand_channels", []):
            ch["google_account"] = acc_id
            retrofit_channel_videos(ch, dry_run=is_dry)

