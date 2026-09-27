import urllib.request
import re
import json
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

def parse_view_count(view_text: str) -> int:
    """Chuyển chuỗi lượt xem (ví dụ: '471 lượt xem', '1,2 N lượt xem', '1.2K views', '100K lượt xem') thành số nguyên."""
    if not view_text:
        return 0
    text = view_text.replace("\xa0", " ").replace(",", ".").lower().strip()
    match = re.search(r"([\d\.]+)\s*(nghìn|triệu|tỷ|tr|k|m|b|\bn\b)?", text)
    if not match:
        return 0
    try:
        num = float(match.group(1))
    except ValueError:
        return 0
    unit = (match.group(2) or "").strip()
    if unit in ("k", "n", "nghìn"):
        return int(num * 1000)
    elif unit in ("m", "tr", "triệu"):
        return int(num * 1000000)
    elif unit in ("b", "tỷ"):
        return int(num * 1000000000)
    return int(num)

def parse_sub_count(sub_text: str) -> int:
    """Chuyển chuỗi sub thành số nguyên."""
    return parse_view_count(sub_text)

def fetch_channel_live_stats(channel_handle_or_url: str) -> Dict[str, Any]:
    """
    Tự động truy cập kênh YouTube và cào dữ liệu thời gian thực:
    - Tên kênh, handle, người đăng ký, số video
    - Danh sách video shorts và lượt xem của từng video
    """
    if not channel_handle_or_url:
        return {"success": False, "error": "Chưa cung cấp đường dẫn hoặc handle kênh"}

    if channel_handle_or_url.startswith("http"):
        url = channel_handle_or_url.rstrip("/")
    else:
        handle = channel_handle_or_url if channel_handle_or_url.startswith("@") else f"@{channel_handle_or_url}"
        url = f"https://www.youtube.com/{handle}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        m = re.search(r"var ytInitialData = ({.*?});</script>", html)
        if not m:
            m = re.search(r'window\["ytInitialData"\] = ({.*?});</script>', html)
        if not m:
            return {"success": False, "error": "Không thể nạp dữ liệu từ YouTube"}

        d = json.loads(m.group(1))

        # 1. Trích xuất thông tin kênh từ Header
        header = d.get("header", {}).get("pageHeaderRenderer", {})
        content_model = header.get("content", {}).get("pageHeaderViewModel", {})
        ch_title = content_model.get("title", {}).get("dynamicTextViewModel", {}).get("text", {}).get("content", "YouTube Channel")
        
        metadata = content_model.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
        meta_parts = []
        for r in metadata:
            for part in r.get("metadataParts", []):
                t = part.get("text", {}).get("content", "").strip()
                if t:
                    meta_parts.append(t)

        handle_text = meta_parts[0] if len(meta_parts) > 0 else "@channel"
        subs_text = meta_parts[1] if len(meta_parts) > 1 else "0 người đăng ký"
        vids_text = meta_parts[2] if len(meta_parts) > 2 else "0 video"

        # Avatar
        avatar_sources = content_model.get("image", {}).get("decoratedAvatarViewModel", {}).get("avatar", {}).get("avatarViewModel", {}).get("image", {}).get("sources", [])
        avatar_url = avatar_sources[-1].get("url") if avatar_sources else ""

        # 2. Tìm kiếm danh sách Video Shorts qua shortsLockupViewModel
        def find_shorts_lockups(obj):
            items = []
            if isinstance(obj, dict):
                if "shortsLockupViewModel" in obj:
                    items.append(obj["shortsLockupViewModel"])
                for v in obj.values():
                    items.extend(find_shorts_lockups(v))
            elif isinstance(obj, list):
                for i in obj:
                    items.extend(find_shorts_lockups(i))
            return items

        lockups = find_shorts_lockups(d)
        videos: List[Dict[str, Any]] = []
        total_views = 0

        for s in lockups:
            om = s.get("overlayMetadata", {})
            title = om.get("primaryText", {}).get("content", "Video Shorts")
            views_text = om.get("secondaryText", {}).get("content", "0 lượt xem")
            views_num = parse_view_count(views_text)
            total_views += views_num

            # Video ID
            vid_id = ""
            tap = s.get("onTap", {})
            cmd = tap.get("innertubeCommand", {})
            reel_cmd = cmd.get("reelWatchEndpoint", {})
            if reel_cmd:
                vid_id = reel_cmd.get("videoId", "")

            # Thumbnail
            thumb_sources = s.get("thumbnailViewModel", {}).get("thumbnailViewModel", {}).get("image", {}).get("sources", [])
            thumb_url = thumb_sources[0].get("url") if thumb_sources else f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"

            videos.append({
                "video_id": vid_id,
                "title": title,
                "views_text": views_text,
                "views_num": views_num,
                "thumbnail": thumb_url,
                "url": f"https://youtube.com/shorts/{vid_id}" if vid_id else url,
            })

        # Sắp xếp video theo lượt xem giảm dần
        videos.sort(key=lambda x: x["views_num"], reverse=True)
        if videos:
            videos[0]["is_top_performer"] = True

        avg_views = int(total_views / len(videos)) if videos else 0

        return {
            "success": True,
            "channel_title": ch_title,
            "channel_handle": handle_text,
            "channel_url": url,
            "avatar_url": avatar_url,
            "subscribers_text": subs_text,
            "subscribers_num": parse_sub_count(subs_text),
            "videos_count_text": vids_text,
            "videos_count_num": len(videos) or parse_view_count(vids_text),
            "total_views": total_views,
            "avg_views": avg_views,
            "videos": videos
        }

    except Exception as e:
        logger.error(f"Lỗi khi lấy stats kênh {channel_handle_or_url}: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }

def calculate_ai_forecast(
    total_views: int,
    total_videos: int,
    avg_views: int,
    daily_uploads: float = 1.0,
    niche: str = "history_mystery"
) -> Dict[str, Any]:
    """
    Dự đoán tăng trưởng và tiềm năng tài chính cho kênh trong 30, 90, 180, 365 ngày.
    """
    # Baseline views per video (tối thiểu 300 views cho ngách này)
    base_avg_views = max(avg_views, 300)

    # Hệ số lan toả tăng dần theo thời gian (khi kênh có độ uy tín thuật toán - Authority)
    # Tháng 1: 1.0x -> Tháng 3: 2.2x -> Tháng 6: 4.5x -> Tháng 12: 8.0x
    stages = [
        {"days": 30, "label": "30 Ngày Tới (Tạo Đà)", "authority_multiplier": 1.1, "breakout_chance": "Cao (1-2 videos bùng nổ)"},
        {"days": 90, "label": "90 Ngày (Bứt Phá)", "authority_multiplier": 2.2, "breakout_chance": "Rất cao (Đạt mốc 100K views)"},
        {"days": 180, "label": "6 Tháng (Bật Kiếm Tiền)", "authority_multiplier": 4.5, "breakout_chance": "Định hình thương hiệu"},
        {"days": 365, "label": "1 Năm (Quy Mô Lớn)", "authority_multiplier": 8.0, "breakout_chance": "Thư viện Shorts đồ sộ"},
    ]

    forecast_results = []
    accumulated_videos = total_videos
    accumulated_views = total_views

    for stg in stages:
        days = stg["days"]
        new_videos = int(days * daily_uploads)
        accumulated_videos = total_videos + new_videos
        
        # Lượt xem trung bình tăng theo hệ số lan toả
        projected_avg = int(base_avg_views * stg["authority_multiplier"])
        # Cộng thêm lượt xem từ video mới + views tích luỹ từ video cũ
        period_views = int(new_videos * projected_avg)
        accumulated_views += period_views

        # Tỷ lệ chuyển đổi Subscribers: ~ 0.5% - 0.8% trên tổng views Shorts
        projected_subs = max(1, int(accumulated_views * 0.0055))

        # Ước tính doanh thu Shorts AdSense: RPM ~$0.06 - $0.12 (khán giả US/Global)
        est_adsense_usd = round(accumulated_views * 0.00008, 1)
        # Doanh thu Affiliate (Sách, khóa học, Blinkist, Audible): ~$0.40 - $1.00 / 1.000 views
        est_affiliate_usd = round(accumulated_views * 0.00045, 1)
        est_total_revenue_vnd = int((est_adsense_usd + est_affiliate_usd) * 25400)

        forecast_results.append({
            "days": days,
            "label": stg["label"],
            "total_videos": accumulated_videos,
            "projected_views": accumulated_views,
            "projected_subs": projected_subs,
            "adsense_usd": est_adsense_usd,
            "affiliate_usd": est_affiliate_usd,
            "total_revenue_usd": round(est_adsense_usd + est_affiliate_usd, 1),
            "total_revenue_vnd_formatted": f"{est_total_revenue_vnd:,.0f} ₫",
            "breakout_chance": stg["breakout_chance"]
        })

    # Đề xuất chủ đề tiềm năng cao dựa trên niche
    recommended_topics = [
        {
            "hook": "Why the quietest people in the room are the most dangerous",
            "topic": "Tâm lý học về người hướng nội & quyền lực thầm lặng",
            "potential_score": 9.8,
            "target_views": "50K - 200K"
        },
        {
            "hook": "The brutal mind games John D. Rockefeller used to crush rivals",
            "topic": "Mẹo tâm lý đen tối của giới tài phiệt lịch sử (Ăn theo video 471 views)",
            "potential_score": 9.6,
            "target_views": "40K - 150K"
        },
        {
            "hook": "How to tell if someone is secretly jealous of you in 3 seconds",
            "topic": "Đọc vị ngôn ngữ cơ thể & dấu hiệu đố kỵ",
            "potential_score": 9.5,
            "target_views": "30K - 120K"
        },
        {
            "hook": "The Dark Psychology trick to make anyone regret losing you",
            "topic": "Tâm lý học đảo ngược & giá trị bản thân",
            "potential_score": 9.3,
            "target_views": "25K - 90K"
        }
    ]

    return {
        "daily_uploads": daily_uploads,
        "base_avg_views": base_avg_views,
        "stages": forecast_results,
        "recommended_topics": recommended_topics
    }
