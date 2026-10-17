"""
tools/standalone_auditor/run_audit.py — Công cụ Báo cáo Kênh Độc lập 100% (Zero-Coupling).

Đặc điểm thiết kế:
- TÁCH BIỆT HOÀN TOÀN với hệ thống code chính (`app/`, `config/`, `database/`).
- Không import bất kỳ module nào của hệ thống làm video để tránh phụ thuộc hoặc xung đột khi sửa code chính.
- Chỉ dùng thư viện chuẩn + `requests`, `pyyaml`, `cryptography`, `google-api-python-client`.
- Tự giải mã token OAuth, tự quét YouTube Data API v3, tự gọi Gemini REST API phân tích và bắn thẳng vào Nhóm Telegram.
"""

import os
import re
import sys
import json
import yaml
import time
import base64
import hashlib
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("standalone_auditor")

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
VN_TZ = timezone(timedelta(hours=7))
AES_PREFIX = b"AES256GCM:"
DEFAULT_MASTER_KEY_HEX = "27e6b92fef27250bcec8629328c7060bce3da29a9edf98e3d00f63092ff2ba0a"

# File lưu trạng thái tạm để tính độ lệch (Delta) giữa các ca kiểm tra
STATE_PATH = Path(os.getenv("AUDIT_STATE_PATH", str(ROOT_DIR / "tools" / "standalone_auditor" / ".audit_state.json")))
REPORT_PATH = Path(os.getenv("AUDIT_REPORT_PATH", str(ROOT_DIR / "tools" / "standalone_auditor" / "LATEST_AUDIT_REPORT.md")))


def _load_dotenv_if_present() -> None:
    """Đọc nhẹ file .env (nếu chạy ở máy local) mà không cần phụ thuộc config/settings.py."""
    env_file = ROOT_DIR / ".env"
    if not env_file.exists():
        return
    try:
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


def _decrypt_token_file(file_path: Path) -> Optional[Dict[str, Any]]:
    """Giải mã độc lập file youtube_token.json (chuẩn AES-256-GCM hoặc JSON thường)."""
    if not file_path.exists():
        return None
    try:
        raw = file_path.read_bytes()
        if raw.startswith(AES_PREFIX):
            env_key = os.getenv("ENCRYPTION_MASTER_KEY", "").strip() or DEFAULT_MASTER_KEY_HEX
            key = bytes.fromhex(env_key) if len(env_key) == 64 else hashlib.sha256(env_key.encode("utf-8")).digest()
            combined = base64.b64decode(raw[len(AES_PREFIX):])
            nonce, ciphertext = combined[:12], combined[12:]
            raw = AESGCM(key).decrypt(nonce, ciphertext, None)
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        logger.warning(f"Không thể giải mã token {file_path}: {e}")
        return None


def _build_youtube_service(channel_cfg: Dict[str, Any], acc_id: str):
    """Khởi tạo kết nối YouTube Data API v3 hoàn toàn độc lập."""
    cid = channel_cfg.get("id", "default")
    candidates = [
        channel_cfg.get("token_file"),
        str(ROOT_DIR / "credentials" / "channels" / cid / "youtube_token.json"),
        str(ROOT_DIR / "credentials" / "accounts" / acc_id / "youtube_token.json"),
    ]
    token_path = next((Path(p) for p in candidates if p and Path(p).exists()), None)
    if not token_path:
        return None, f"Thiếu file token cho kênh {cid}"

    token_data = _decrypt_token_file(token_path)
    if not token_data:
        return None, f"Không đọc được dữ liệu token của {cid}"

    try:
        creds = Credentials.from_authorized_user_info(token_data)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        return build("youtube", "v3", credentials=creds), None
    except Exception as e:
        return None, str(e)


def _parse_iso(iso_str: str) -> Optional[datetime]:
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return None


def collect_telemetry(prev_state: Dict[str, Any]) -> Dict[str, Any]:
    """Quét toàn bộ 7 kênh YouTube."""
    cfg_path = ROOT_DIR / "config" / "channels.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    now_utc = datetime.now(timezone.utc)
    now_vn = now_utc.astimezone(VN_TZ)

    prev_channels = prev_state.get("channels", {})
    prev_videos = prev_state.get("videos", {})
    prev_ts = _parse_iso(prev_state.get("timestamp_utc", ""))
    hours_since_prev = max(0.1, (now_utc - prev_ts).total_seconds() / 3600.0) if prev_ts else None

    channels_data = []
    new_channels_state = {}
    new_videos_state = {}

    for acc in cfg.get("google_accounts", []):
        acc_id = acc.get("id", "account_1")
        for ch in acc.get("brand_channels", []):
            cid = ch.get("id", "unknown")
            cname = ch.get("name", cid)
            lang = ch.get("language", "en")

            srv, err = _build_youtube_service(ch, acc_id)
            if not srv:
                channels_data.append({"id": cid, "name": cname, "language": lang, "error": err})
                continue

            try:
                c_res = srv.channels().list(mine=True, part="snippet,statistics,contentDetails").execute()
                items = c_res.get("items", [])
                if not items:
                    channels_data.append({"id": cid, "name": cname, "language": lang, "error": "Kênh trống"})
                    continue

                item = items[0]
                stats = item.get("statistics", {})
                total_views = int(stats.get("viewCount", 0))
                total_subs = int(stats.get("subscriberCount", 0))
                total_videos = int(stats.get("videoCount", 0))
                uploads_pl = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")

                prev_ch = prev_channels.get(cid, {})
                delta_views = total_views - int(prev_ch["views"]) if "views" in prev_ch else 0
                delta_subs = total_subs - int(prev_ch["subs"]) if "subs" in prev_ch else 0

                pl_items = srv.playlistItems().list(playlistId=uploads_pl, part="contentDetails", maxResults=12).execute().get("items", [])
                vids = [x["contentDetails"]["videoId"] for x in pl_items]
                v_details = srv.videos().list(id=",".join(vids), part="snippet,statistics,status").execute().get("items", []) if vids else []

                recent_72h, all_public, scheduled_queue, unscheduled_private = [], [], [], []
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
                    dv_views = views - int(prev_v["views"]) if "views" in prev_v else 0

                    new_videos_state[vid] = {"views": views, "likes": likes, "privacy": privacy}

                    if privacy == "private" and sched_at_str:
                        sched_dt = _parse_iso(sched_at_str)
                        sched_vn = sched_dt.astimezone(VN_TZ).strftime("%H:%M %d/%m/%Y") if sched_dt else sched_at_str
                        scheduled_queue.append({"id": vid, "title": title, "publish_at_utc": sched_at_str, "publish_at_vn": sched_vn})
                    elif privacy == "private":
                        unscheduled_private.append({"id": vid, "title": title})
                    elif privacy == "public":
                        pub_dt = _parse_iso(pub_at_str)
                        age_h = max(0.2, (now_utc - pub_dt).total_seconds() / 3600.0) if pub_dt else 999.0
                        pub_vn = pub_dt.astimezone(VN_TZ).strftime("%H:%M %d/%m") if pub_dt else pub_at_str[:16]
                        v_obj = {
                            "id": vid,
                            "title": title,
                            "published_vn": pub_vn,
                            "age_hours": round(age_h, 1),
                            "views": views,
                            "delta_views": dv_views,
                            "likes": likes,
                            "comments": comments,
                            "like_rate_pct": like_rate,
                            "views_per_hour": round(views / age_h, 1),
                        }
                        all_public.append(v_obj)
                        if age_h <= 72.0:
                            recent_72h.append(v_obj)

                scheduled_queue.sort(key=lambda x: x["publish_at_utc"])
                effective_total_views = max(total_views, sum(x["views"] for x in all_public))
                new_channels_state[cid] = {"views": effective_total_views, "subs": total_subs}

                channels_data.append({
                    "id": cid,
                    "name": cname,
                    "language": lang,
                    "total_views": effective_total_views,
                    "delta_views": delta_views,
                    "total_subs": total_subs,
                    "delta_subs": delta_subs,
                    "total_videos": total_videos,
                    "scheduled_count": len(scheduled_queue),
                    "scheduled_queue": scheduled_queue,
                    "recent_72h": recent_72h,
                    "unscheduled_private": unscheduled_private,
                })
            except Exception as e:
                channels_data.append({"id": cid, "name": cname, "language": lang, "error": str(e)})

    return {
        "timestamp_vn": now_vn.strftime("%H:%M — %d/%m/%Y"),
        "hours_since_prev": round(hours_since_prev, 2) if hours_since_prev else None,
        "prev_timestamp_vn": prev_state.get("timestamp_vn"),
        "channels": channels_data,
        "new_state": {
            "timestamp_utc": now_utc.isoformat(),
            "timestamp_vn": now_vn.strftime("%H:%M %d/%m/%Y"),
            "channels": new_channels_state,
            "videos": new_videos_state,
        },
    }


def analyze_with_gemini(telemetry: Dict[str, Any]) -> str:
    """Phân tích Ưu/Nhược điểm & Đề xuất cải thiện bằng Gemini REST API."""
    raw_keys = os.getenv("GEMINI_API_KEYS", "")
    keys = [k.strip() for k in raw_keys.split(",") if k.strip().startswith("AIza")]

    payload_summary = [
        {
            "name": c.get("name"),
            "lang": c.get("language"),
            "total_views": c.get("total_views"),
            "delta_views": c.get("delta_views"),
            "subs": c.get("total_subs"),
            "scheduled_count": c.get("scheduled_count"),
            "scheduled_next": c.get("scheduled_queue", [])[:2],
            "recent_72h": c.get("recent_72h", []),
        }
        for c in telemetry["channels"]
        if not c.get("error")
    ]

    prompt = f"""Bạn là Chuyên gia Chiến lược YouTube Shorts đang kiểm tra định kỳ (7 lần/ngày) cho hệ thống 7 kênh lúc {telemetry['timestamp_vn']}.
Dữ liệu thực tế từ YouTube API:
{json.dumps(payload_summary, ensure_ascii=False, indent=2)}

KPI chuẩn 10.000 Views:
- Vận tốc cắn đề xuất (Seed Velocity): >= 40-80 views/giờ trong 12h đầu.
- Tỷ lệ Like/View: >= 4.0% là rất tốt, 2.5%-3.9% là đạt, < 2.0% là cần kích tương tác.
- 12 video đang SCHEDULED vừa được áp dụng cấu trúc 3-Cut Opening Hook (4 cảnh cắt nhanh trong 3.5s đầu).

Hãy viết báo cáo phân tích ngắn gọn, sắc bén bằng tiếng Việt gồm đúng 3 phần:
1. ✅ ƯU ĐIỂM & VIDEO ĐANG CẮN ĐỀ XUẤT (nêu rõ số view, tốc độ view/giờ, tỷ lệ Like/View).
2. ⚠️ NHƯỢC ĐIỂM & CẢNH BÁO SỚM (video nào chững view, tỷ lệ like thấp hoặc lệch pha giữa kênh pt-BR và en).
3. 🛠️ ĐỀ XUẤT CẢI THIỆN CỤ THỂ NGAY.
Đi thẳng vào 3 mục, không chào hỏi rườm rà."""

    models = ["gemini-3-flash-preview", "gemini-flash-latest", "gemini-2.5-flash-lite"]
    for key in keys[:4]:
        for m in models:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={key}"
                r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=25)
                if r.status_code == 200:
                    cands = r.json().get("candidates", [])
                    if cands:
                        txt = "".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", [])).strip()
                        if len(txt) > 100:
                            return txt
            except Exception:
                continue
    return "*(Hệ thống AI đang bận, vui lòng xem bảng số liệu chi tiết phía trên)*"


def send_telegram_direct(telemetry: Dict[str, Any], insights: str) -> None:
    """Bắn báo cáo thẳng vào Nhóm Telegram bằng HTTP REST thuần túy (không phụ thuộc code app/)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return

    groups_raw = os.getenv("ALLOWED_TELEGRAM_GROUPS", "").strip()
    users_raw = os.getenv("ALLOWED_TELEGRAM_USERS", "").strip()

    chat_ids = []
    for item in groups_raw.split(","):
        part = item.split(":")[0].strip()
        if part and (part.lstrip("-").isdigit()):
            chat_ids.append(int(part))
    if not chat_ids:
        for item in users_raw.split(","):
            part = item.split(":")[0].strip()
            if part and part.isdigit():
                chat_ids.append(int(part))

    if not chat_ids:
        return

    lines = [f"📊 *BÁO CÁO ĐỊNH KỲ 7 KÊNH ({telemetry['timestamp_vn']} VN)*"]
    if telemetry.get("prev_timestamp_vn"):
        lines.append(f"🔄 _So với lần check trước ({telemetry['hours_since_prev']}h trước)_")
    lines.append("\n📌 *1. TỔNG QUAN 7 KÊNH:*")
    for c in telemetry["channels"]:
        if c.get("error"):
            lines.append(f"• *{c['name']}*: ❌ Lỗi kết nối")
            continue
        dv = f" (+{c['delta_views']})" if c["delta_views"] > 0 else ""
        ds = f" (+{c['delta_subs']})" if c["delta_subs"] > 0 else ""
        lines.append(
            f"• *{c['name']}* (`{c['language']}`): *{c['total_views']:,}* views{dv} | *{c['total_subs']}* subs{ds} | ⏳ Lịch: *{c['scheduled_count']}*"
        )

    lines.append("\n🔥 *2. VIDEO MỚI ĐĂNG (72H QUA):*")
    for c in telemetry["channels"]:
        if c.get("error") or not c.get("recent_72h"):
            continue
        for v in c["recent_72h"][:2]:
            dv = f" (+{v['delta_views']})" if v["delta_views"] > 0 else ""
            lines.append(
                f"• *{c['name']}* — _{v['title'][:36]}_: *{v['views']:,}* views{dv} ({v['views_per_hour']}/h) | 👍 {v['likes']} ({v['like_rate_pct']}%)"
            )

    msg1 = "\n".join(lines)
    msg2 = f"🧠 *3. ĐÁNH GIÁ ƯU / NHƯỢC & ĐỀ XUẤT:*\n\n{insights}"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for cid in chat_ids:
        for text_chunk in [msg1[:3900], msg2[:3900]]:
            try:
                res = requests.post(url, json={"chat_id": cid, "text": text_chunk, "parse_mode": "Markdown", "disable_web_page_preview": True}, timeout=10)
                if res.status_code != 200:
                    clean_txt = re.sub(r"[*_`\[\]()<>]", "", text_chunk)
                    requests.post(url, json={"chat_id": cid, "text": clean_txt, "disable_web_page_preview": True}, timeout=10)
                time.sleep(0.5)
            except Exception as e:
                logger.warning(f"Lỗi gửi Telegram tới {cid}: {e}")


def format_full_markdown(telemetry: Dict[str, Any], insights: str) -> str:
    """Tạo nội dung báo cáo Markdown đầy đủ."""
    lines = [f"# 📊 BÁO CÁO ĐỊNH KỲ 7 KÊNH YOUTUBE ({telemetry['timestamp_vn']} VN)", ""]
    lines.append("| Kênh | Ngôn ngữ | Tổng Views (Biến động) | Subscribers | Video Public | Đang Lên Lịch (`SCHEDULED`) |")
    lines.append("| :--- | :---: | :---: | :---: | :---: | :---: |")
    for c in telemetry["channels"]:
        if c.get("error"):
            lines.append(f"| **{c['name']}** | `{c['language']}` | ❌ {c['error'][:35]} | - | - | - |")
            continue
        dv = f" *(+{c['delta_views']})*" if c["delta_views"] > 0 else ""
        ds = f" *(+{c['delta_subs']})*" if c["delta_subs"] > 0 else ""
        lines.append(f"| **{c['name']}** | `{c['language']}` | **{c['total_views']:,}**{dv} | **{c['total_subs']}**{ds} | {c['total_videos']} | **{c['scheduled_count']} video** |")

    lines.append("\n## Hiệu suất Video mới (72h qua)\n")
    for c in telemetry["channels"]:
        if c.get("error") or not c.get("recent_72h"):
            continue
        lines.append(f"### 📺 {c['name']} (`{c['language']}`)")
        for v in c["recent_72h"]:
            dv = f" (+{v['delta_views']} mới)" if v["delta_views"] > 0 else ""
            lines.append(f"- [{v['title']}](https://www.youtube.com/shorts/{v['id']}) ({v['age_hours']}h trước): **{v['views']:,} views**{dv} | ⚡ `{v['views_per_hour']} views/h` | 👍 {v['likes']} (`{v['like_rate_pct']}%`)")
        lines.append("")

    lines.append("## Đánh giá Chiến lược (AI Strategist)\n")
    lines.append(insights)
    return "\n".join(lines)


def main():
    _load_dotenv_if_present()
    prev_state = {}
    if STATE_PATH.exists():
        try:
            prev_state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass

    telemetry = collect_telemetry(prev_state)
    insights = analyze_with_gemini(telemetry)
    md_report = format_full_markdown(telemetry, insights)

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(telemetry["new_state"], ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.write_text(md_report, encoding="utf-8")

    step_summary = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary:
        try:
            with open(step_summary, "a", encoding="utf-8") as f:
                f.write(md_report + "\n")
        except Exception:
            pass

    if "--no-telegram" not in sys.argv:
        send_telegram_direct(telemetry, insights)
        logger.info("📲 Đã gửi báo cáo độc lập vào Nhóm Telegram!")

    print(md_report)


if __name__ == "__main__":
    main()
