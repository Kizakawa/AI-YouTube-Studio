"""
scripts/cloud_channel_audit.py — Hệ thống Kiểm tra & Báo cáo Thông minh 7 lần/ngày (Cloud + Local).

Chức năng:
1. Quét trực tiếp YouTube Data API v3 cho toàn bộ 7 kênh trong config/channels.yaml.
2. So sánh biến động (Delta Views, Delta Subs, Tốc độ tăng view/giờ) so với kỳ kiểm tra liền trước
   thông qua bộ nhớ chung `reports/audit_state.json`.
3. Chấm điểm từng video mới đăng (trong 72h qua) và kiểm tra hàng đợi `SCHEDULED` theo bộ chuẩn KPI 10.000 Views:
   - Tỷ lệ Like/View >= 4.0%
   - Vận tốc cắn đề xuất (Seed Velocity >= 50 views/h trong 6h đầu, Expansion >= 100 views/h)
   - Đối chiếu hiệu quả cấu trúc 3-Cut Opening Hook mới giữa thị trường Brazil (pt-BR) và Mỹ (en).
4. Kết hợp AI Strategist (Gemini 2.5 Flash) + Hệ chuyên gia (Rule-based Diagnostics) để viết báo cáo
   Ưu điểm / Nhược điểm / Đề xuất cải thiện cụ thể bằng tiếng Việt.
5. Tự động gửi báo cáo về Telegram (Group + Admin DM), ghi ra `reports/LATEST_AUDIT_REPORT.md`
   và `$GITHUB_STEP_SUMMARY` (trên GitHub Actions).
"""

import os
import sys
import json
import yaml
import time
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

sys.stdout.reconfigure(encoding="utf-8")
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Luôn ưu tiên SQLite cục bộ khi chạy script kiểm tra để tránh treo 30s nếu Supabase timeout
if not os.getenv("DATABASE_URL") or "supabase" in os.getenv("DATABASE_URL", "").lower():
    sqlite_path = BASE_DIR / "storage" / "ai_youtube.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{sqlite_path.as_posix()}"

from config.settings import settings
from app.agents.publisher_agent import PublisherAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cloud_audit")

VN_TZ = timezone(timedelta(hours=7))
REPORTS_DIR = BASE_DIR / "reports"
STATE_FILE = REPORTS_DIR / "audit_state.json"
LATEST_REPORT_MD = REPORTS_DIR / "LATEST_AUDIT_REPORT.md"


def load_previous_state() -> Dict[str, Any]:
    """Đọc trạng thái của kỳ kiểm tra trước để tính Delta tăng trưởng."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Không thể đọc {STATE_FILE}: {e}")
    return {}


def save_current_state(state: Dict[str, Any]) -> None:
    """Lưu trạng thái hiện tại vào reports/audit_state.json."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Không thể lưu {STATE_FILE}: {e}")


def parse_iso_utc(iso_str: str) -> Optional[datetime]:
    """Chuyển chuỗi ISO 8601 của YouTube sang datetime UTC."""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return None


def collect_all_channels_telemetry(prev_state: Dict[str, Any]) -> Dict[str, Any]:
    """Thu thập dữ liệu trực tiếp từ YouTube Data API v3 cho toàn bộ các kênh."""
    cfg_path = BASE_DIR / "config" / "channels.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file cấu hình kênh: {cfg_path}")

    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    now_utc = datetime.now(timezone.utc)
    now_vn = now_utc.astimezone(VN_TZ)

    prev_channels = prev_state.get("channels", {})
    prev_videos = prev_state.get("videos", {})
    prev_ts = parse_iso_utc(prev_state.get("timestamp_utc", ""))
    hours_since_prev = (
        max(0.1, (now_utc - prev_ts).total_seconds() / 3600.0) if prev_ts else None
    )

    channels_data: List[Dict[str, Any]] = []
    new_channels_state: Dict[str, Any] = {}
    new_videos_state: Dict[str, Any] = {}

    for acc in cfg.get("google_accounts", []):
        acc_id = acc.get("id")
        for ch in acc.get("brand_channels", []):
            ch_copy = dict(ch)
            ch_copy["google_account"] = acc_id
            cid = ch_copy.get("id", "unknown")
            cname = ch_copy.get("name", cid)
            lang = ch_copy.get("language", "en")

            pub = PublisherAgent(channel_config=ch_copy)
            srv = pub.service
            if not srv:
                channels_data.append({
                    "id": cid,
                    "name": cname,
                    "language": lang,
                    "error": pub.init_error or "Không khởi tạo được YouTube service",
                })
                continue

            try:
                c_res = srv.channels().list(
                    mine=True, part="snippet,statistics,contentDetails"
                ).execute()
                items = c_res.get("items", [])
                if not items:
                    channels_data.append({
                        "id": cid,
                        "name": cname,
                        "language": lang,
                        "error": "Không tìm thấy kênh trên tài khoản OAuth",
                    })
                    continue

                item = items[0]
                stats = item.get("statistics", {})
                total_views = int(stats.get("viewCount", 0))
                total_subs = int(stats.get("subscriberCount", 0))
                total_videos = int(stats.get("videoCount", 0))
                uploads_pl = (
                    item.get("contentDetails", {})
                    .get("relatedPlaylists", {})
                    .get("uploads")
                )

                prev_ch = prev_channels.get(cid, {})
                delta_channel_views = (
                    total_views - int(prev_ch["views"])
                    if "views" in prev_ch
                    else 0
                )
                delta_channel_subs = (
                    total_subs - int(prev_ch["subs"])
                    if "subs" in prev_ch
                    else 0
                )

                new_channels_state[cid] = {
                    "name": cname,
                    "views": total_views,
                    "subs": total_subs,
                    "video_count": total_videos,
                }

                # Lấy 12 video gần nhất từ playlist uploads
                pl_items = (
                    srv.playlistItems()
                    .list(playlistId=uploads_pl, part="contentDetails", maxResults=12)
                    .execute()
                    .get("items", [])
                )
                vids = [x["contentDetails"]["videoId"] for x in pl_items]

                v_details = (
                    srv.videos()
                    .list(id=",".join(vids), part="snippet,statistics,status")
                    .execute()
                    .get("items", [])
                    if vids
                    else []
                )

                recent_public_72h: List[Dict[str, Any]] = []
                all_public: List[Dict[str, Any]] = []
                scheduled_queue: List[Dict[str, Any]] = []
                unscheduled_private: List[Dict[str, Any]] = []

                for v in v_details:
                    vid = v["id"]
                    snip = v.get("snippet", {})
                    vstat = v.get("statistics", {})
                    status = v.get("status", {})

                    title = snip.get("title", "")
                    pub_at_str = snip.get("publishedAt", "")
                    privacy = status.get("privacyStatus", "")
                    sched_at_str = status.get("publishAt", "")

                    views = int(vstat.get("viewCount", 0))
                    likes = int(vstat.get("likeCount", 0))
                    comments = int(vstat.get("commentCount", 0))
                    like_rate = round((likes / views * 100.0), 2) if views > 0 else 0.0

                    prev_v = prev_videos.get(vid, {})
                    delta_v_views = (
                        views - int(prev_v["views"]) if "views" in prev_v else 0
                    )
                    delta_v_likes = (
                        likes - int(prev_v["likes"]) if "likes" in prev_v else 0
                    )

                    new_videos_state[vid] = {
                        "channel_id": cid,
                        "title": title,
                        "views": views,
                        "likes": likes,
                        "comments": comments,
                        "privacy": privacy,
                    }

                    if privacy == "private" and sched_at_str:
                        sched_dt = parse_iso_utc(sched_at_str)
                        sched_vn = (
                            sched_dt.astimezone(VN_TZ).strftime("%H:%M %d/%m/%Y")
                            if sched_dt
                            else sched_at_str
                        )
                        scheduled_queue.append({
                            "id": vid,
                            "title": title,
                            "publish_at_utc": sched_at_str,
                            "publish_at_vn": sched_vn,
                        })
                    elif privacy == "private" and not sched_at_str:
                        unscheduled_private.append({
                            "id": vid,
                            "title": title,
                            "created_at": pub_at_str[:16],
                        })
                    elif privacy == "public":
                        pub_dt = parse_iso_utc(pub_at_str)
                        age_hours = (
                            max(0.2, (now_utc - pub_dt).total_seconds() / 3600.0)
                            if pub_dt
                            else 999.0
                        )
                        pub_vn = (
                            pub_dt.astimezone(VN_TZ).strftime("%H:%M %d/%m")
                            if pub_dt
                            else pub_at_str[:16]
                        )
                        v_obj = {
                            "id": vid,
                            "title": title,
                            "published_vn": pub_vn,
                            "age_hours": round(age_hours, 1),
                            "views": views,
                            "delta_views": delta_v_views,
                            "likes": likes,
                            "delta_likes": delta_v_likes,
                            "comments": comments,
                            "like_rate_pct": like_rate,
                            "views_per_hour": round(views / age_hours, 1),
                        }
                        all_public.append(v_obj)
                        if age_hours <= 72.0:
                            recent_public_72h.append(v_obj)

                # Sắp xếp lịch hẹn theo thời gian tăng dần
                scheduled_queue.sort(key=lambda x: x["publish_at_utc"])
                # Tính tổng view thực tế của các video gần nhất (tránh độ trễ API trang chủ)
                sum_recent_views = sum(x["views"] for x in all_public)

                channels_data.append({
                    "id": cid,
                    "name": cname,
                    "language": lang,
                    "total_views": max(total_views, sum_recent_views),
                    "api_channel_views": total_views,
                    "delta_views": delta_channel_views,
                    "total_subs": total_subs,
                    "delta_subs": delta_channel_subs,
                    "total_videos": total_videos,
                    "scheduled_count": len(scheduled_queue),
                    "scheduled_queue": scheduled_queue,
                    "recent_72h": recent_public_72h,
                    "top_recent": all_public[:5],
                    "unscheduled_private": unscheduled_private,
                })
            except Exception as e:
                logger.error(f"Lỗi quét kênh {cname}: {e}")
                channels_data.append({
                    "id": cid,
                    "name": cname,
                    "language": lang,
                    "error": str(e),
                })

    current_state = {
        "timestamp_utc": now_utc.isoformat(),
        "timestamp_vn": now_vn.strftime("%Y-%m-%d %H:%M:%S"),
        "channels": new_channels_state,
        "videos": new_videos_state,
    }

    return {
        "timestamp_vn": now_vn.strftime("%H:%M — %d/%m/%Y"),
        "hours_since_prev": round(hours_since_prev, 2) if hours_since_prev else None,
        "prev_timestamp_vn": prev_state.get("timestamp_vn"),
        "channels": channels_data,
        "new_state": current_state,
    }


def generate_ai_strategic_insights(telemetry: Dict[str, Any]) -> str:
    """
    Sử dụng Gemini 2.5 Flash với bộ tiêu chuẩn KPI 10.000 Views của Antigravity
    để phân tích sâu Ưu điểm, Nhược điểm và Đề xuất cải thiện cụ thể.
    Tự động fallback sang bộ chẩn đoán Rule-based nếu API key bận.
    """
    raw_keys = (
        os.getenv("GEMINI_API_KEYS", "")
        or getattr(settings, "GEMINI_API_KEYS_RAW", "")
    )
    keys = [k.strip() for k in raw_keys.split(",") if k.strip().startswith("AIza")]
    if not keys and hasattr(settings, "get_gemini_keys"):
        keys = [k for k in settings.get_gemini_keys() if k.startswith("AIza")]

    summary_for_llm = {
        "check_time_vn": telemetry["timestamp_vn"],
        "hours_since_previous_check": telemetry["hours_since_prev"],
        "channels": [
            {
                "name": c.get("name"),
                "lang": c.get("language"),
                "total_views": c.get("total_views"),
                "delta_views": c.get("delta_views"),
                "subs": c.get("total_subs"),
                "delta_subs": c.get("delta_subs"),
                "scheduled_count": c.get("scheduled_count"),
                "scheduled_queue": c.get("scheduled_queue", [])[:2],
                "recent_72h_videos": c.get("recent_72h", []),
                "unscheduled_private": c.get("unscheduled_private", []),
            }
            for c in telemetry["channels"]
            if not c.get("error")
        ],
    }

    prompt = f"""Bạn là Chuyên gia Chiến lược YouTube Shorts (Antigravity AI Strategist) đang quản lý hệ thống 7 kênh của người dùng.
Dưới đây là dữ liệu Telemetry thời gian thực vừa quét từ YouTube Data API v3 lúc {telemetry['timestamp_vn']}:

```json
{json.dumps(summary_for_llm, ensure_ascii=False, indent=2)}
```

Bộ chuẩn KPI 10.000 Views mà hệ thống đang áp dụng:
- Vận tốc cắn đề xuất (Seed Velocity): >= 40-80 views/giờ trong 12h đầu là tín hiệu cắn luồng Shorts Feed tốt.
- Tỷ lệ Like/View: >= 4.0% là rất tốt, 2.5% - 3.9% là đạt, < 2.0% là tương tác yếu.
- Hệ thống vừa nâng cấp kỹ thuật "3-Cut Opening Hook" (4 cú cắt cảnh trong 3.5s đầu + Full 9:16 + Google Top Search) áp dụng cho 12 video đang SCHEDULED.

Hãy viết phần phân tích chiến lược ngắn gọn, sắc bén bằng tiếng Việt (dùng Markdown rõ ràng) gồm đúng 3 mục:
1. **✅ ƯU ĐIỂM & ĐIỂM SÁNG (Video nào đang cắn đề xuất tốt nhất, tốc độ tăng view/giờ và tỷ lệ Like/View ra sao):** Nêu đích danh tên video, số view, tốc độ tăng trưởng.
2. **⚠️ NHƯỢC ĐIỂM & ĐIỂM NGHẼN (Kênh/video nào đang chững view, lệch pha giữa thị trường Brazil pt-BR và Mỹ en, hoặc thiếu lịch đăng):** Nêu rõ nguyên nhân kỹ thuật/thuật toán.
3. **🛠️ HÀNH ĐỘNG & ĐỀ XUẤT CẢI THIỆN NGAY:** Nêu 3 đề xuất cụ thể, thực tế nhất cho ca tiếp theo.
Không viết lời chào rườm rà, đi thẳng vào 3 mục trên."""

    if keys:
        import requests as http_req
        candidate_models = ["gemini-3-flash-preview", "gemini-flash-latest", "gemini-2.5-flash-lite"]
        for key in keys[:4]:
            for model_name in candidate_models:
                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={key}"
                    resp = http_req.post(
                        url,
                        json={"contents": [{"parts": [{"text": prompt}]}]},
                        timeout=25,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        cands = data.get("candidates", [])
                        if cands:
                            parts = cands[0].get("content", {}).get("parts", [])
                            txt = "".join(p.get("text", "") for p in parts).strip()
                            if len(txt) > 100:
                                logger.info(f"🧠 Đã sinh phân tích chiến lược thành công bằng model {model_name}")
                                return txt
                    elif resp.status_code == 404:
                        continue
                    else:
                        logger.warning(f"Gemini {model_name} ({key[:8]}...): HTTP {resp.status_code}")
                        break
                except Exception as e:
                    logger.warning(f"Gemini key {key[:8]}... lỗi: {e}")
                    break

    # Fallback Rule-Based Strategist nếu không gọi được Gemini
    hot_videos = []
    stalled_videos = []
    empty_sched_channels = []
    for c in telemetry["channels"]:
        if c.get("error"):
            continue
        if c.get("scheduled_count", 0) == 0 and c.get("id") != "paws_hearts_rescue":
            empty_sched_channels.append(c["name"])
        for v in c.get("recent_72h", []):
            if v["views"] >= 300 or v["views_per_hour"] >= 25:
                hot_videos.append(f"- **{c['name']}**: *{v['title']}* ({v['views']} views, {v['views_per_hour']} views/h, Like/View {v['like_rate_pct']}%)")
            elif v["age_hours"] >= 4.0 and v["views"] < 60:
                stalled_videos.append(f"- **{c['name']}**: *{v['title']}* ({v['views']} views sau {v['age_hours']}h)")

    lines = [
        "### ✅ 1. ƯU ĐIỂM & ĐIỂM SÁNG",
        *(hot_videos if hot_videos else ["- Các video mới đang trong giai đoạn tích lũy tín hiệu ban đầu."]),
        "",
        "### ⚠️ 2. NHƯỢC ĐIỂM & ĐIỂM NGHẼN",
        *(stalled_videos[:6] if stalled_videos else ["- Không có video mới nào bị kẹt dưới ngưỡng Seed trong 72h qua."]),
        "",
        "### 🛠️ 3. HÀNH ĐỘNG & ĐỀ XUẤT CẢI THIỆN NGAY",
        "- Theo dõi sát vận tốc view trong 2 giờ đầu của 12 video chuẩn **3-Cut Opening Hook** khi đến giờ công chiếu.",
        *(
            [f"- Cần chạy bổ sung lịch đăng cho các kênh đã cạn hàng đợi: **{', '.join(empty_sched_channels)}**."]
            if empty_sched_channels
            else ["- Toàn bộ 6 kênh chủ lực đều đã có đủ video `SCHEDULED` cho khung giờ vàng tiếp theo."]
        ),
    ]
    return "\n".join(lines)


def build_markdown_report(telemetry: Dict[str, Any], ai_insights: str) -> str:
    """Xây dựng báo cáo Markdown hoàn chỉnh (cho GitHub & Antigravity)."""
    lines = [
        f"# 📊 BÁO CÁO ĐỊNH KỲ TÌNH HÌNH 7 KÊNH YOUTUBE ({telemetry['timestamp_vn']} VN)",
    ]
    if telemetry.get("prev_timestamp_vn"):
        lines.append(
            f"> 🔄 *So sánh biến động với kỳ kiểm tra trước lúc `{telemetry['prev_timestamp_vn']}` ({telemetry['hours_since_prev']} giờ trước)*"
        )
    lines.append("")
    lines.append("## 1. Tổng quan chỉ số 7 kênh")
    lines.append("")
    lines.append("| Kênh | Ngôn ngữ | Tổng Views (Biến động) | Subscribers | Video Public | Đang Lên Lịch (`SCHEDULED`) |")
    lines.append("| :--- | :---: | :---: | :---: | :---: | :---: |")

    for c in telemetry["channels"]:
        if c.get("error"):
            lines.append(f"| **{c['name']}** | `{c['language']}` | ❌ Lỗi: {c['error'][:40]} | - | - | - |")
            continue
        dv = c["delta_views"]
        ds = c["delta_subs"]
        dv_str = f" *(+{dv})*" if dv > 0 else ""
        ds_str = f" *(+{ds})*" if ds > 0 else ""
        lines.append(
            f"| **{c['name']}** | `{c['language']}` | **{c['total_views']:,}**{dv_str} | **{c['total_subs']}**{ds_str} | {c['total_videos']} | **{c['scheduled_count']} video** |"
        )

    lines.append("")
    lines.append("## 2. Hiệu suất Video mới đăng trong 72h qua")
    lines.append("")

    any_recent = False
    for c in telemetry["channels"]:
        if c.get("error") or not c.get("recent_72h"):
            continue
        any_recent = True
        lines.append(f"### 📺 {c['name']} (`{c['language']}`)")
        for v in c["recent_72h"]:
            dv_str = f" (+{v['delta_views']} mới)" if v["delta_views"] > 0 else ""
            lines.append(
                f"- [{v['title']}](https://www.youtube.com/shorts/{v['id']}) *(Đăng {v['published_vn']} — {v['age_hours']}h trước)*: "
                f"**{v['views']:,} views**{dv_str} | ⚡ `{v['views_per_hour']} views/h` | 👍 **{v['likes']} likes** (`{v['like_rate_pct']}%`) | 💬 {v['comments']} cmt"
            )
        lines.append("")

    if not any_recent:
        lines.append("- *Không có video nào mới chuyển sang Public trong 72h qua.*")
        lines.append("")

    lines.append("## 3. Hàng đợi Video đang hẹn giờ (`SCHEDULED`)")
    lines.append("")
    for c in telemetry["channels"]:
        if c.get("error") or not c.get("scheduled_queue"):
            continue
        q_strs = [
            f"[`{s['publish_at_vn']}` — {s['title'][:45]}](https://www.youtube.com/shorts/{s['id']})"
            for s in c["scheduled_queue"]
        ]
        lines.append(f"- **{c['name']}** ({len(q_strs)} video): " + " ; ".join(q_strs))

    lines.append("")
    lines.append("## 4. Đánh giá Ưu / Nhược điểm & Đề xuất Cải thiện (AI Strategist)")
    lines.append("")
    lines.append(ai_insights)
    lines.append("")
    return "\n".join(lines)


def send_report_to_telegram(telemetry: Dict[str, Any], ai_insights: str) -> None:
    """Định dạng bản tóm tắt gọn gàng và bắn trực tiếp về Telegram của người dùng."""
    from app.services.channel_intelligence import send_telegram_broadcast

    header_lines = [
        f"📊 *BÁO CÁO ĐỊNH KỲ 7 KÊNH YOUTUBE ({telemetry['timestamp_vn']} VN)*",
    ]
    if telemetry.get("prev_timestamp_vn"):
        header_lines.append(f"🔄 _So với lần check trước ({telemetry['hours_since_prev']}h trước)_")
    header_lines.append("")
    header_lines.append("📌 *1. TỔNG QUAN 7 KÊNH:*")

    for c in telemetry["channels"]:
        if c.get("error"):
            header_lines.append(f"• *{c['name']}*: ❌ Lỗi kết nối")
            continue
        dv = f" (+{c['delta_views']})" if c["delta_views"] > 0 else ""
        ds = f" (+{c['delta_subs']})" if c["delta_subs"] > 0 else ""
        header_lines.append(
            f"• *{c['name']}* (`{c['language']}`): *{c['total_views']:,}* views{dv} | *{c['total_subs']}* subs{ds} | ⏳ Lịch: *{c['scheduled_count']}* video"
        )

    header_lines.append("")
    header_lines.append("🔥 *2. VIDEO MỚI ĐĂNG (72H QUA):*")
    for c in telemetry["channels"]:
        if c.get("error") or not c.get("recent_72h"):
            continue
        for v in c["recent_72h"][:2]:
            dv = f" (+{v['delta_views']})" if v["delta_views"] > 0 else ""
            header_lines.append(
                f"• *{c['name']}* — _{v['title'][:38]}_: *{v['views']:,}* views{dv} ({v['views_per_hour']}/h) | 👍 {v['likes']} ({v['like_rate_pct']}%)"
            )

    msg_part_1 = "\n".join(header_lines)
    msg_part_2 = f"🧠 *3. PHÂN TÍCH ƯU / NHƯỢC & ĐỀ XUẤT CẢI THIỆN:*\n\n{ai_insights}"

    send_telegram_broadcast(msg_part_1[:3900])
    time.sleep(1.0)
    # Nếu phần AI phân tích dài quá 3800 ký tự, chia nhỏ để không bị lỗi giới hạn Telegram
    if len(msg_part_2) <= 3900:
        send_telegram_broadcast(msg_part_2)
    else:
        chunks = [msg_part_2[i:i + 3800] for i in range(0, len(msg_part_2), 3800)]
        for ch_text in chunks:
            send_telegram_broadcast(ch_text)
            time.sleep(0.8)


def main():
    logger.info("🚀 Bắt đầu quét dữ liệu định kỳ 7 kênh YouTube...")
    prev_state = load_previous_state()
    telemetry = collect_all_channels_telemetry(prev_state)
    ai_insights = generate_ai_strategic_insights(telemetry)
    md_report = build_markdown_report(telemetry, ai_insights)

    # 1. Lưu file báo cáo mới nhất & trạng thái lịch sử vào thư mục reports/
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(LATEST_REPORT_MD, "w", encoding="utf-8") as f:
        f.write(md_report)
    save_current_state(telemetry["new_state"])

    # 2. Nếu chạy trên GitHub Actions, ghi trực tiếp vào GITHUB_STEP_SUMMARY
    step_summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary_path:
        try:
            with open(step_summary_path, "a", encoding="utf-8") as f:
                f.write(md_report + "\n")
        except Exception as e:
            logger.warning(f"Không thể ghi GITHUB_STEP_SUMMARY: {e}")

    # 3. Gửi báo cáo trực tiếp qua Telegram (nếu có TELEGRAM_BOT_TOKEN)
    if "--no-telegram" not in sys.argv:
        try:
            send_report_to_telegram(telemetry, ai_insights)
            logger.info("📲 Đã gửi báo cáo định kỳ về Telegram thành công!")
        except Exception as e:
            logger.warning(f"Lỗi gửi Telegram: {e}")

    print("\n" + "=" * 75)
    print(md_report)
    print("=" * 75)


if __name__ == "__main__":
    main()
