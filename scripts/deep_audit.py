import sys
import json
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

with open('storage/temp/channels_audit.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

for ch_id, d in data.items():
    title = d.get('title')
    niche = d.get('niche')
    handle = d.get('custom_url')
    subs = d.get('subscribers')
    t_views = d.get('total_views')
    created = (d.get('created_at') or '')[:10]
    
    print('=' * 75)
    print(f"CHANNEL: {title} | Ngách: {niche} | Handle: {handle}")
    print(f"Ngày tạo kênh: {created} | Subs: {subs:,} | Tổng Views: {t_views:,}")
    
    videos = d.get('videos', [])
    if not videos:
        print("  Không có video nào!")
        continue
        
    by_date = sorted(videos, key=lambda x: x.get('published_at', ''))
    views_list = [v['views'] for v in videos]
    likes_list = [v['likes'] for v in videos]
    cmts_list = [v['comments'] for v in videos]
    
    avg_v = sum(views_list) / len(views_list)
    med_v = sorted(views_list)[len(views_list) // 2]
    first_up = (by_date[0].get('published_at') or '')[:10]
    latest_up = (by_date[-1].get('published_at') or '')[:10]
    
    print(f"Tổng video audit: {len(videos)} | Từ ngày: {first_up} -> {latest_up}")
    print(f"Views trung bình/video: {avg_v:.1f} | Views Trung vị: {med_v} | Cao nhất: {max(views_list):,} | Thấp nhất: {min(views_list)}")
    print(f"Tỷ lệ tương tác (Likes/Views): {(sum(likes_list)/max(1, sum(views_list))*100):.2f}% | Tổng bình luận: {sum(cmts_list)}")
    
    # Phân tích danh sách video gần đây
    print("\nChi tiết 6 video gần nhất:")
    for v in by_date[-6:]:
        p_date = (v.get('published_at') or '')[:16].replace('T', ' ')
        title_v = v.get('title', '')
        views = v.get('views', 0)
        likes = v.get('likes', 0)
        cmts = v.get('comments', 0)
        status = v.get('privacy_status')
        pub_at = v.get('publish_at')
        stat_str = f"[{status}]" if not pub_at else f"[Scheduled: {pub_at[:16]}]"
        print(f"  • {p_date} {stat_str:<15} | {views:>5} views | {likes:>3} likes | {cmts:>2} cmts | {title_v}")

    # Top video nổi bật nhất
    top_3 = sorted(videos, key=lambda x: x['views'], reverse=True)[:3]
    print("\nTop 3 video bùng nổ nhất:")
    for tv in top_3:
        p_date = (tv.get('published_at') or '')[:10]
        print(f"  ⭐ {tv['views']:>5} views ({tv['likes']} likes, {tv['comments']} cmts) - Đăng ngày {p_date}: \"{tv['title']}\"")
