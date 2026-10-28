"""
tools/standalone_auditor/run_audit.py — Công cụ Báo cáo Kênh Độc lập 100% (Zero-Coupling).

Đặc điểm thiết kế:
- TÁCH BIỆT HOÀN TOÀN với hệ thống code chính (`app/`, `config/`, `database/`).
- Tích hợp kép:
  1. YouTube Data API v3 (Thời gian thực): Views, Velocity (views/h), Likes, Like/View %, Comments, Lịch hẹn SCHEDULED.
  2. YouTube Analytics API v2 (Chuyên sâu): Thời lượng xem trung bình (AVD - giây), Tỷ lệ xem trung bình (APV %),
     Tỷ lệ Ở lại xem (Stay-to-Watch / Viewed %) & Tỷ lệ Bỏ qua / Vuốt đi (Swiped Away %).
- Tự động gọi Gemini 3 Flash phân tích Ưu/Nhược điểm & Đề xuất cải thiện theo chuẩn KPI 10.000 Views và gửi vào Nhóm Telegram.
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
from typing import Dict, List, Any, Optional, Tuple

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


def _candidate_aes_keys() -> List[bytes]:
    """Trả về danh sách các khoá AES-256 ứng viên (env key, credentials/.master.key, legacy key)."""
    keys: List[bytes] = []
    env_key = os.getenv("ENCRYPTION_MASTER_KEY", "").strip()
    if env_key:
        try:
            k = bytes.fromhex(env_key) if len(env_key) == 64 else hashlib.sha256(env_key.encode("utf-8")).digest()
            keys.append(k)
        except Exception:
            keys.append(hashlib.sha256(env_key.encode("utf-8")).digest())

    master_key_file = ROOT_DIR / "credentials" / ".master.key"
    if master_key_file.exists():
        try:
            raw_k = master_key_file.read_bytes().strip()
            if len(raw_k) == 32:
                keys.append(raw_k)
            elif len(raw_k) == 44:
                keys.append(base64.b64decode(raw_k))
            elif len(raw_k) > 0:
                keys.append(hashlib.sha256(raw_k).digest())
        except Exception:
            pass

    legacy_k = bytes.fromhex(DEFAULT_MASTER_KEY_HEX)
    if legacy_k not in keys:
        keys.append(legacy_k)
    return keys


def _decrypt_token_file(file_path: Path) -> Optional[Dict[str, Any]]:
    """Giải mã độc lập file youtube_token.json (chuẩn AES-256-GCM hoặc JSON thường)."""
    if not file_path.exists():
        return None
    try:
        raw = file_path.read_bytes()
        if raw.startswith(AES_PREFIX):
            combined = base64.b64decode(raw[len(AES_PREFIX):])
            nonce, ciphertext = combined[:12], combined[12:]
            decrypted = None
            last_err = None
            for key in _candidate_aes_keys():
                try:
                    decrypted = AESGCM(key).decrypt(nonce, ciphertext, None)
                    break
                except Exception as ex:
                    last_err = ex
            if decrypted is None:
                raise ValueError(f"AES-GCM decrypt failed: {last_err}")
            raw = decrypted
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        logger.warning(f"Không thể giải mã token {file_path}: {e}")
        return None


def _build_youtube_services(channel_cfg: Dict[str, Any], acc_id: str) -> Tuple[Any, Any, Optional[str]]:
    """Khởi tạo đồng thời YouTube Data API v3 và YouTube Analytics API v2 hoàn toàn độc lập."""
    cid = channel_cfg.get("id", "default")
    candidates = [
        channel_cfg.get("token_file"),
        str(ROOT_DIR / "credentials" / "channels" / cid / "youtube_token.json"),
        str(ROOT_DIR / "credentials" / "accounts" / acc_id / "youtube_token.json"),
    ]
    token_path = next((Path(p) for p in candidates if p and Path(p).exists()), None)
    if not token_path:
        return None, None, f"Thiếu file token cho kênh {cid}"

    token_data = _decrypt_token_file(token_path)
    if not token_data:
        return None, None, f"Không đọc được dữ liệu token của {cid}"

    try:
        creds = Credentials.from_authorized_user_info(token_data)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        yt_data = build("youtube", "v3", credentials=creds)
        try:
            yt_analytics = build("youtubeAnalytics", "v2", credentials=creds)
        except Exception:
            yt_analytics = None
        return yt_data, yt_analytics, None
    except Exception as e:
        return None, None, str(e)


def _fetch_analytics_metrics(yta_service: Any) -> Tuple[Dict[str, Any], Dict[str, Dict[str, float]]]:
    """
    Truy vấn YouTube Analytics API v2 trong 28 ngày gần nhất:
    - Trả về (channel_metrics, video_metrics_map):
      + avd_sec: Thời lượng xem trung bình (giây)
      + apv_pct: Tỷ lệ phần trăm xem trung bình (%)
      + stay_pct: Tỷ lệ ở lại xem (Viewed / Stay-to-Watch %)
      + swipe_pct: Tỷ lệ bỏ qua / vuốt đi (Swiped Away %)
    """
    ch_summary = {"avd_sec": 0.0, "apv_pct": 0.0, "stay_pct": 0.0, "swipe_pct": 0.0, "has_data": False}
    vid_map: Dict[str, Dict[str, float]] = {}
    if not yta_service:
        return ch_summary, vid_map

    end_date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    start_date = (datetime.now(timezone.utc) - timedelta(days=28)).strftime("%Y-%m-%d")

    # 1. Chỉ số trung bình toàn kênh
    try:
        c_rep = yta_service.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics="views,engagedViews,averageViewDuration,averageViewPercentage",
        ).execute()
        rows = c_rep.get("rows", [])
        if rows and rows[0]:
            views, engaged, avd, apv = rows[0]
            if views and float(views) > 0:
                stay = round(min(100.0, (float(engaged) / float(views)) * 100.0), 1)
                swipe = round(max(0.0, 100.0 - stay), 1)
                ch_summary = {
                    "avd_sec": round(float(avd or 0.0), 1),
                    "apv_pct": round(float(apv or 0.0), 1),
                    "stay_pct": stay,
                    "swipe_pct": swipe,
                    "has_data": True,
                }
    except Exception as e:
        logger.debug(f"Analytics channel query error: {e}")

    # 2. Chỉ số chi tiết từng video (Top 200 video từ 2024-01-01 để lấy đủ mọi video đã chốt sổ Analytics)
    reliable_rows = []
    try:
        v_rep = yta_service.reports().query(
            ids="channel==MINE",
            startDate="2024-01-01",
            endDate=end_date,
            metrics="views,engagedViews,averageViewDuration,averageViewPercentage",
            dimensions="video",
            sort="-views",
            maxResults=200,
        ).execute()
        for r in v_rep.get("rows", []):
            vid_id, v_views, v_engaged, v_avd, v_apv = r[0], float(r[1] or 0), float(r[2] or 0), float(r[3] or 0), float(r[4] or 0)
            if v_views > 0:
                is_reliable = bool(v_views >= 25 and 1.0 <= v_apv <= 200.0 and v_avd > 0.0)
                v_stay = round(min(100.0, (v_engaged / v_views) * 100.0), 1)
                v_swipe = round(max(0.0, 100.0 - v_stay), 1)
                vid_map[vid_id] = {
                    "yta_views": int(v_views),
                    "engaged_views": int(v_engaged),
                    "avd_sec": round(v_avd, 1) if is_reliable else None,
                    "apv_pct": round(v_apv, 1) if is_reliable else None,
                    "stay_pct": v_stay if is_reliable else None,
                    "swipe_pct": v_swipe if is_reliable else None,
                    "raw_avd_sec": round(v_avd, 1),
                    "raw_apv_pct": round(v_apv, 1),
                    "raw_stay_pct": v_stay,
                    "analytics_reliable": is_reliable,
                }
                if is_reliable:
                    reliable_rows.append((v_views, v_engaged, v_avd, v_apv))
    except Exception as e:
        logger.debug(f"Analytics video query error: {e}")

    # Nếu chỉ số APV/AVD trung bình kênh bị nhiễu do các video ít view treo tab lặp (ví dụ APV > 85% hoặc AVD > 32s trên kênh ít view),
    # tính trung bình có trọng số (weighted average) trên tập các video đủ mẫu tin cậy (yta_views >= 25 & APV <= 200%).
    if reliable_rows and (ch_summary.get("apv_pct", 0.0) > 85.0 or not ch_summary.get("has_data")):
        tot_v = sum(x[0] for x in reliable_rows)
        tot_e = sum(x[1] for x in reliable_rows)
        if tot_v > 0:
            w_stay = round(min(100.0, (tot_e / tot_v) * 100.0), 1)
            w_avd = round(sum(x[0] * x[2] for x in reliable_rows) / tot_v, 1)
            w_apv = round(sum(x[0] * x[3] for x in reliable_rows) / tot_v, 1)
            ch_summary = {
                "avd_sec": w_avd,
                "apv_pct": w_apv,
                "stay_pct": w_stay,
                "swipe_pct": round(max(0.0, 100.0 - w_stay), 1),
                "has_data": True,
                "sample_views": int(tot_v),
            }

    return ch_summary, vid_map


def _parse_iso(iso_str: str) -> Optional[datetime]:
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return None


def _build_audit_engagement_comment(ch: Dict[str, Any], title: str = "", desc: str = "") -> str:
    """Trích xuất câu hỏi Poll từ Dòng 1 Description (💬 ...) hoặc sinh câu hỏi bình chọn chuẩn ngách/khu vực."""
    if desc:
        for ln in str(desc).splitlines():
            ln_s = ln.strip()
            if ln_s.startswith("💬"):
                cand = ln_s.lstrip("💬").strip()
                if len(cand) >= 15:
                    return cand

    lang = str(ch.get("language") or "en").lower().strip()
    is_pt = lang.startswith("pt")
    raw_niche = str(ch.get("niche") or "").lower()

    if any(k in raw_niche for k in ("history", "what_if", "alternate")):
        return (
            "Se você estivesse lá: escolheria (1) Manter nossa linha do tempo ou (2) Reescrever a história? Comente 1 ou 2 abaixo! 👇"
            if is_pt else
            "Quick Poll: Would you choose (1) Keep our timeline or (2) Rewrite history? Drop 1 or 2 below! 👇"
        )
    if "psychology" in raw_niche:
        return (
            "Você já lidou com alguém assim na vida real? Comente (1) SIM ou (2) NÃO abaixo! 👇"
            if is_pt else
            "Have you ever dealt with someone like this in real life? Drop (1) YES or (2) NO in the comments! 👇"
        )
    if any(k in raw_niche for k in ("rescue", "animal", "pet")):
        return (
            "Dê uma nota de 1 a 10 para essa transformação emocionante! 🥺 Deixe um ❤️ nos comentários!"
            if is_pt else
            "Rate this sweet baby's transformation from 1 to 10! 🥺 Drop a ❤️ below if this made your day!"
        )
    if any(k in raw_niche for k in ("predator", "wildlife")):
        return (
            "Quem leva a melhor nesse confronto de sobrevivência: (1) Predador ou (2) Presa? Deixe seu voto abaixo e INSCREVA-SE no canal! 👇🔥"
            if is_pt else
            "Who takes the crown in this survival showdown: (1) Predator or (2) Challenger? Drop 1 or 2 below & SUBSCRIBE for daily Apex Battles! 👇🔥🦁"
        )
    if any(k in raw_niche for k in ("deep_sea", "ocean", "abyss", "mariana", "mystery", "earth")):
        return (
            "O que você acha que realmente se esconde lá: (1) Criatura Desconhecida ou (2) Fenômeno Geológico? Comente 1 ou 2 abaixo! 👇"
            if is_pt else
            "What do you think is really behind this: (1) Undiscovered Species or (2) Natural Anomaly? Drop 1 or 2 below! 👇"
        )
    return (
        "Você já sabia disso antes de assistir? Comente (1) SIM ou (2) NÃO abaixo! 👇🔥"
        if is_pt else
        "Did you know this before watching? Drop (1) YES or (2) NO in the comments below! 👇🔥"
    )


def collect_telemetry(prev_state: Dict[str, Any]) -> Dict[str, Any]:
    """Quét toàn bộ 7 kênh YouTube (Data API v3 + Analytics API v2)."""
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

    fallback_srv = None
    for acc_pre in cfg.get("google_accounts", []):
        acc_pre_id = acc_pre.get("id", "account_1")
        for ch_pre in acc_pre.get("brand_channels", []):
            s_pre, _, _ = _build_youtube_services(ch_pre, acc_pre_id)
            if s_pre:
                fallback_srv = s_pre
                break
        if fallback_srv:
            break

    for acc in cfg.get("google_accounts", []):
        acc_id = acc.get("id", "account_1")
        for ch in acc.get("brand_channels", []):
            cid = ch.get("id", "unknown")
            cname = ch.get("name", cid)
            lang = ch.get("language", "en")
            yt_channel_id = (ch.get("youtube_channel_id") or "").strip()

            srv, yta, err = _build_youtube_services(ch, acc_id)
            using_public_fallback = False
            if not srv and fallback_srv and yt_channel_id:
                srv = fallback_srv
                using_public_fallback = True
                logger.warning(f"Kênh {cname} ({cid}) lỗi OAuth riêng ({err}) -> Dùng Public Data API v3 fallback qua ID {yt_channel_id}")
            elif not srv:
                channels_data.append({"id": cid, "name": cname, "language": lang, "error": err})
                continue

            try:
                ch_analytics, vid_analytics_map = _fetch_analytics_metrics(yta) if not using_public_fallback else (
                    {"avd_sec": 0.0, "apv_pct": 0.0, "stay_pct": 0.0, "swipe_pct": 0.0, "has_data": False},
                    {},
                )

                if using_public_fallback:
                    c_res = srv.channels().list(id=yt_channel_id, part="snippet,statistics,contentDetails").execute()
                else:
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

                pl_items = srv.playlistItems().list(playlistId=uploads_pl, part="contentDetails", maxResults=50).execute().get("items", [])
                vids = [x["contentDetails"]["videoId"] for x in pl_items]
                v_details = srv.videos().list(id=",".join(vids), part="snippet,statistics,status").execute().get("items", []) if vids else []

                recent_72h, all_public, scheduled_queue, top_analytics_videos = [], [], [], []
                auto_commented_in_run = 0
                for v in v_details:
                    vid = v["id"]
                    snip = v.get("snippet", {})
                    vstat = v.get("statistics", {})
                    status = v.get("status", {})

                    title = snip.get("title", "")
                    desc = snip.get("description", "")
                    pub_at_str = snip.get("publishedAt", "")
                    privacy = status.get("privacyStatus", "")
                    sched_at_str = status.get("publishAt", "")

                    views = int(vstat.get("viewCount", 0))
                    likes = int(vstat.get("likeCount", 0))
                    comments = int(vstat.get("commentCount", 0))
                    like_rate = round((likes / views * 100.0), 2) if views > 0 else 0.0

                    prev_v = prev_videos.get(vid, {})
                    dv_views = views - int(prev_v["views"]) if "views" in prev_v else 0

                    # Ghép chỉ số Retention & Tỷ lệ bỏ qua từ YouTube Analytics API v2
                    ya = vid_analytics_map.get(vid, {})

                    if privacy == "private" and sched_at_str:
                        sched_dt = _parse_iso(sched_at_str)
                        sched_vn = sched_dt.astimezone(VN_TZ).strftime("%H:%M %d/%m/%Y") if sched_dt else sched_at_str
                        scheduled_queue.append({"id": vid, "title": title, "publish_at_utc": sched_at_str, "publish_at_vn": sched_vn})
                    elif privacy == "public":
                        pub_dt = _parse_iso(pub_at_str)
                        age_h = max(0.2, (now_utc - pub_dt).total_seconds() / 3600.0) if pub_dt else 999.0
                        pub_vn = pub_dt.astimezone(VN_TZ).strftime("%H:%M %d/%m") if pub_dt else pub_at_str[:16]

                        # Tự động đăng bình luận tương tác của tác giả ngay khi video vừa công chiếu (SCHEDULED -> PUBLIC)
                        if not using_public_fallback and comments == 0 and age_h <= 120.0 and auto_commented_in_run < 2:
                            try:
                                poll_text = _build_audit_engagement_comment(ch, title=title, desc=desc)
                                srv.commentThreads().insert(
                                    part="snippet",
                                    body={
                                        "snippet": {
                                            "videoId": vid,
                                            "topLevelComment": {
                                                "snippet": {
                                                    "textOriginal": poll_text.strip()
                                                }
                                            }
                                        }
                                    },
                                ).execute()
                                comments = 1
                                auto_commented_in_run += 1
                                logger.info(f"💬 [AutoComment] Đã tự động đăng bình luận tương tác tác giả cho video mới công chiếu {vid} ({cname})")
                            except Exception as ce:
                                logger.debug(f"[AutoComment] Bỏ qua {vid} ({cname}): {ce}")

                        v_obj = {
                            "id": vid,
                            "title": title,
                            "description": desc[:500] if desc else "",
                            "published_at_utc": pub_at_str,
                            "published_vn": pub_vn,
                            "age_hours": round(age_h, 1),
                            "views": views,
                            "delta_views": dv_views,
                            "likes": likes,
                            "comments": comments,
                            "like_rate_pct": like_rate,
                            "views_per_hour": round(views / age_h, 1),
                            "yta_views": ya.get("yta_views"),
                            "engaged_views": ya.get("engaged_views"),
                            "analytics_reliable": ya.get("analytics_reliable", False),
                            "avd_sec": ya.get("avd_sec"),
                            "apv_pct": ya.get("apv_pct"),
                            "stay_pct": ya.get("stay_pct"),
                            "swipe_pct": ya.get("swipe_pct"),
                        }
                        all_public.append(v_obj)
                        if age_h <= 72.0:
                            recent_72h.append(v_obj)
                        if ya.get("avd_sec") is not None:
                            top_analytics_videos.append(v_obj)

                    new_videos_state[vid] = {
                        "views": views,
                        "likes": likes,
                        "comments": comments,
                        "privacy": privacy,
                        "avd_sec": ya.get("avd_sec"),
                        "apv_pct": ya.get("apv_pct"),
                        "stay_pct": ya.get("stay_pct"),
                        "swipe_pct": ya.get("swipe_pct"),
                    }

                scheduled_queue.sort(key=lambda x: x["publish_at_utc"])
                effective_total_views = max(total_views, sum(x["views"] for x in all_public))

                # Xếp hạng Top 3 Winners và Bottom 3 Losers theo hiệu suất thật
                ranked_public = sorted(
                    all_public,
                    key=lambda x: (
                        x.get("views", 0),
                        float(x.get("stay_pct") or 0.0),
                        float(x.get("apv_pct") or 0.0),
                    ),
                    reverse=True,
                )
                top_3_winners = ranked_public[:3]
                winner_ids = {w["id"] for w in top_3_winners}
                bottom_3_losers = [x for x in reversed(ranked_public) if x["id"] not in winner_ids][:3]

                prev_ch = prev_channels.get(cid, {})
                delta_views = effective_total_views - int(prev_ch["views"]) if "views" in prev_ch else 0
                delta_subs = total_subs - int(prev_ch["subs"]) if "subs" in prev_ch else 0
                new_channels_state[cid] = {"views": effective_total_views, "subs": total_subs}

                channels_data.append({
                    "id": cid,
                    "name": cname,
                    "handle": ch.get("youtube_handle") or ch.get("handle") or "",
                    "niche": ch.get("niche") or "",
                    "language": lang,
                    "total_views": effective_total_views,
                    "delta_views": delta_views,
                    "total_subs": total_subs,
                    "delta_subs": delta_subs,
                    "total_videos": total_videos,
                    "scheduled_count": len(scheduled_queue),
                    "scheduled_queue": scheduled_queue,
                    "analytics": ch_analytics,
                    "recent_72h": recent_72h,
                    "top_analytics_videos": top_analytics_videos[:10],
                    "all_public_videos": all_public,
                    "top_3_winners": top_3_winners,
                    "bottom_3_losers": bottom_3_losers,
                })
            except Exception as e:
                channels_data.append({"id": cid, "name": cname, "language": lang, "error": str(e)})

    return {
        "timestamp_vn": now_vn.strftime("%H:%M — %d/%m/%Y"),
        "timestamp_utc": now_utc.isoformat(),
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
    """Phân tích sâu Ưu/Nhược điểm (bao gồm AVD, APV%, Tỷ lệ Vuốt bỏ) bằng Gemini 3 Flash."""
    raw_keys = os.getenv("GEMINI_API_KEYS", "")
    keys = [k.strip() for k in raw_keys.split(",") if k.strip().startswith("AIza")]

    payload_summary = [
        {
            "name": c.get("name"),
            "lang": c.get("language"),
            "total_views": c.get("total_views"),
            "delta_views": c.get("delta_views"),
            "subs": c.get("total_subs"),
            "channel_analytics_28d": c.get("analytics"),
            "scheduled_count": c.get("scheduled_count"),
            "recent_72h": c.get("recent_72h", []),
            "videos_with_retention_data": c.get("top_analytics_videos", [])[:3],
        }
        for c in telemetry["channels"]
        if not c.get("error")
    ]

    prompt = f"""Bạn là Chuyên gia Chiến lược YouTube Shorts đang kiểm tra định kỳ (7 lần/ngày) cho hệ thống 7 kênh lúc {telemetry['timestamp_vn']}.
Dữ liệu thực tế từ YouTube Data API v3 + YouTube Analytics API v2:
{json.dumps(payload_summary, ensure_ascii=False, indent=2)}

KPI chuẩn 10.000 Views:
- Tỷ lệ Ở lại xem (Stay-to-Watch %) >= 70% (tức Tỷ lệ Vuốt bỏ / Swiped Away <= 30%).
- Tỷ lệ xem trung bình (APV %) >= 90% - 105%, Thời lượng xem trung bình (AVD) >= 24s - 32s.
- Vận tốc cắn đề xuất (Seed Velocity): >= 40-80 views/giờ trong 12h đầu.
- Tỷ lệ Like/View: >= 4.0% là rất tốt, < 2.0% là cần kích tương tác.

Hãy viết báo cáo phân tích ngắn gọn, sắc bén bằng tiếng Việt gồm đúng 3 phần:
1. ✅ ƯU ĐIỂM & VIDEO ĐANG CẮN ĐỀ XUẤT (nêu rõ số view, tốc độ view/giờ, AVD/APV% và tỷ lệ Vuốt bỏ/Ở lại xem của các kênh/video nổi bật).
2. ⚠️ NHƯỢC ĐIỂM & CẢNH BÁO TỶ LỆ BỎ QUA / CHỮNG VIEW (chỉ rõ kênh/video nào đang bị tỷ lệ Vuốt bỏ Swiped Away cao >55%, AVD thấp hoặc lệch pha giữa kênh pt-BR và en).
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


def _fmt_retention_badge(v: Dict[str, Any]) -> str:
    """Hiển thị chuỗi AVD, APV% và Tỷ lệ Bỏ qua (nếu YouTube Analytics đã tổng hợp xong cho video đó)."""
    if v.get("avd_sec") is not None and v.get("apv_pct") is not None:
        return (
            f"⏱️ AVD: `{v['avd_sec']}s` (`{v['apv_pct']}%`) | "
            f"👁️ Ở lại: `{v['stay_pct']}%` / ⏭️ Bỏ qua: `{v['swipe_pct']}%`"
        )
    return "⏳ *(Analytics đang chốt sổ 24h đầu)*"


def send_telegram_direct(telemetry: Dict[str, Any], insights: str) -> None:
    """Bắn báo cáo kèm chỉ số AVD, APV% & Tỷ lệ bỏ qua thẳng vào Nhóm Telegram."""
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
    lines.append("\n📌 *1. TỔNG QUAN & TỶ LỆ GIỮ CHÂN / BỎ QUA:*")
    for c in telemetry["channels"]:
        if c.get("error"):
            lines.append(f"• *{c['name']}*: ❌ Lỗi kết nối")
            continue
        dv = f" (+{c['delta_views']})" if c["delta_views"] > 0 else ""
        ya = c.get("analytics", {})
        ya_str = (
            f" | ⏱️ AVD: `{ya['avd_sec']}s` (`{ya['apv_pct']}%`) | ⏭️ Bỏ qua: `{ya['swipe_pct']}%`"
            if ya.get("has_data")
            else ""
        )
        lines.append(
            f"• *{c['name']}* (`{c['language']}`): *{c['total_views']:,}* views{dv} | *{c['total_subs']}* subs{ya_str} | ⏳ Lịch: *{c['scheduled_count']}*"
        )

    lines.append("\n🔥 *2. VIDEO MỚI ĐĂNG (72H QUA):*")
    for c in telemetry["channels"]:
        if c.get("error") or not c.get("recent_72h"):
            continue
        for v in c["recent_72h"][:2]:
            dv = f" (+{v['delta_views']})" if v["delta_views"] > 0 else ""
            ret = f" | AVD {v['avd_sec']}s ({v['apv_pct']}%), Bỏ qua {v['swipe_pct']}%" if v.get("avd_sec") is not None else ""
            lines.append(
                f"• *{c['name']}* — _{v['title'][:34]}_: *{v['views']:,}* views{dv} ({v['views_per_hour']}/h) | 👍 {v['likes']} ({v['like_rate_pct']}%){ret}"
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
    """Tạo nội dung báo cáo Markdown đầy đủ kèm AVD, APV%, Stay-to-Watch & Tỷ lệ bỏ qua."""
    lines = [f"# 📊 BÁO CÁO ĐỊNH KỲ 7 KÊNH YOUTUBE ({telemetry['timestamp_vn']} VN)", ""]
    lines.append("## 1. Tổng quan Kênh & Chỉ số Giữ chân / Tỷ lệ Bỏ qua (YouTube Analytics)")
    lines.append("")
    lines.append("| Kênh | Ngôn ngữ | Tổng Views (Biến động) | Subs | Thời lượng xem TB (`AVD`) | Tỷ lệ xem TB (`APV%`) | Ở lại xem (`Viewed%`) | Tỷ lệ Bỏ qua (`Swiped Away%`) | Lịch `SCHEDULED` |")
    lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    for c in telemetry["channels"]:
        if c.get("error"):
            lines.append(f"| **{c['name']}** | `{c['language']}` | ❌ {c['error'][:30]} | - | - | - | - | - | - |")
            continue
        dv = f" *(+{c['delta_views']})*" if c["delta_views"] > 0 else ""
        ya = c.get("analytics", {})
        avd_s = f"**{ya['avd_sec']}s**" if ya.get("has_data") else "-"
        apv_s = f"**{ya['apv_pct']}%**" if ya.get("has_data") else "-"
        stay_s = f"**{ya['stay_pct']}%**" if ya.get("has_data") else "-"
        swipe_s = f"`{ya['swipe_pct']}%`" if ya.get("has_data") else "-"
        lines.append(
            f"| **{c['name']}** | `{c['language']}` | **{c['total_views']:,}**{dv} | **{c['total_subs']}** | {avd_s} | {apv_s} | {stay_s} | {swipe_s} | **{c['scheduled_count']} video** |"
        )

    lines.append("\n## 2. Hiệu suất Video mới (72h qua) & Chi tiết Tỷ lệ Bỏ qua từng Video\n")
    for c in telemetry["channels"]:
        if c.get("error"):
            continue
        lines.append(f"### 📺 {c['name']} (`{c['language']}`)")
        if c.get("recent_72h"):
            for v in c["recent_72h"]:
                dv = f" (+{v['delta_views']} mới)" if v["delta_views"] > 0 else ""
                ret_str = _fmt_retention_badge(v)
                lines.append(
                    f"- [{v['title']}](https://www.youtube.com/shorts/{v['id']}) ({v['age_hours']}h trước): "
                    f"**{v['views']:,} views**{dv} | ⚡ `{v['views_per_hour']} views/h` | 👍 {v['likes']} (`{v['like_rate_pct']}%`) | {ret_str}"
                )
        # Hiển thị thêm các video gần nhất đã có dữ liệu Analytics chốt sổ để đối chiếu AVD/Swipe
        shown_ids = {v["id"] for v in c.get("recent_72h", [])}
        extra_ya = [v for v in c.get("top_analytics_videos", []) if v["id"] not in shown_ids][:2]
        for v in extra_ya:
            ret_str = _fmt_retention_badge(v)
            lines.append(
                f"- *(Đã chốt Analytics)* [{v['title']}](https://www.youtube.com/shorts/{v['id']}): "
                f"**{v['views']:,} views** | 👍 {v['likes']} (`{v['like_rate_pct']}%`) | {ret_str}"
            )
        lines.append("")

    lines.append("## 3. Đánh giá Chiến lược (AI Strategist)\n")
    lines.append(insights)
    return "\n".join(lines)


def sync_telemetry_to_production(telemetry: Dict[str, Any], insights: str) -> Dict[str, Any]:
    """
    Đóng vòng lặp phản hồi (Closed-Loop Production Feedback):
    Đưa toàn bộ dữ liệu thực tế từ Auditor (views, likes, comments, avd_sec, apv_pct, stay_pct, swipe_pct,
    top_3_winners, bottom_3_losers, ai_insights) ngược lại vào:
    1. File `storage/channel_audit_latest.json` (để ChannelIntelligence & ContentAgent đọc tức thì).
    2. Bảng `PublishedVideo` & `VideoJob` trong Production DB (PostgreSQL / SQLite).
    """
    summary = {"json_updated": False, "db_upserted": 0, "channels_synced": 0}
    audit_json_path = ROOT_DIR / "storage" / "channel_audit_latest.json"
    audit_json_path.parent.mkdir(parents=True, exist_ok=True)

    existing_json: Dict[str, Any] = {}
    if audit_json_path.exists():
        try:
            existing_json = json.loads(audit_json_path.read_text(encoding="utf-8"))
            if not isinstance(existing_json, dict):
                existing_json = {}
        except Exception:
            existing_json = {}

    # 1. Cập nhật storage/channel_audit_latest.json
    for c in telemetry.get("channels", []):
        cid = c.get("id")
        if not cid or c.get("error"):
            continue
        prev_entry = existing_json.get(cid) if isinstance(existing_json.get(cid), dict) else {}
        pub_vids = c.get("all_public_videos") or prev_entry.get("videos") or []
        existing_json[cid] = {
            **prev_entry,
            "id": cid,
            "name": c.get("name", cid),
            "handle": c.get("handle") or prev_entry.get("handle") or "",
            "niche": c.get("niche") or prev_entry.get("niche") or "",
            "language": c.get("language", "en"),
            "audited_at_utc": telemetry.get("timestamp_utc"),
            "audited_at_vn": telemetry.get("timestamp_vn"),
            "total_views": c.get("total_views", 0),
            "delta_views": c.get("delta_views", 0),
            "total_subs": c.get("total_subs", 0),
            "delta_subs": c.get("delta_subs", 0),
            "total_videos": c.get("total_videos", 0),
            "scheduled_count": c.get("scheduled_count", 0),
            "scheduled_queue": c.get("scheduled_queue", []),
            "analytics": c.get("analytics", {}),
            "videos": pub_vids,
            "top_3_winners": c.get("top_3_winners", []),
            "bottom_3_losers": c.get("bottom_3_losers", []),
            "ai_insights": insights,
        }
        summary["channels_synced"] += 1

    try:
        audit_json_path.write_text(json.dumps(existing_json, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["json_updated"] = True
    except Exception as e:
        logger.warning(f"Không thể ghi channel_audit_latest.json: {e}")

    # 2. Đồng bộ trực tiếp vào bảng PublishedVideo & VideoJob trong Production DB
    try:
        if str(ROOT_DIR) not in sys.path:
            sys.path.insert(0, str(ROOT_DIR))
        from app.database.models import get_db, PublishedVideo, VideoJob, normalize_channel_id
        from app.services.smart_scheduler import sync_scheduled_jobs_status
        from app.services.video_library import (
            _compute_calibrated_retention,
            _extract_hook_from_job,
            _parse_yt_time,
            _WEEKLY_PERFORMANCE_CACHE,
        )

        try:
            sync_scheduled_jobs_status()
        except Exception as e_sched:
            logger.debug(f"[Auditor->Production] sync_scheduled_jobs_status warning: {e_sched}")

        now_naive = datetime.utcnow()
        upserted = 0
        with get_db() as db:
            jobs = db.query(VideoJob).filter(VideoJob.youtube_video_id.isnot(None)).all()
            job_by_vid = {(j.youtube_video_id or "").strip(): j for j in jobs if (j.youtube_video_id or "").strip()}

            for c in telemetry.get("channels", []):
                if c.get("error"):
                    continue
                canon_cid = normalize_channel_id(c.get("id"))
                pub_vids = c.get("all_public_videos") or []
                if not pub_vids:
                    continue

                public_ids = {(v.get("id") or "").strip() for v in pub_vids if (v.get("id") or "").strip()}
                if public_ids:
                    db.query(PublishedVideo).filter(
                        PublishedVideo.channel_id == canon_cid,
                        ~PublishedVideo.youtube_video_id.in_(public_ids),
                    ).delete(synchronize_session=False)

                for v in pub_vids:
                    vid = (v.get("id") or "").strip()
                    if not vid:
                        continue
                    v_views = int(v.get("views") or 0)
                    v_likes = int(v.get("likes") or 0)
                    v_comments = int(v.get("comments") or 0)
                    raw_apv = v.get("apv_pct")
                    raw_stay = v.get("stay_pct")
                    raw_swipe = v.get("swipe_pct")
                    raw_avd = v.get("avd_sec")

                    matched_job = job_by_vid.get(vid)
                    if matched_job:
                        matched_job.target_channel_id = canon_cid
                        if matched_job.status == "SCHEDULED":
                            matched_job.status = "PUBLISHED"
                    hook_txt = _extract_hook_from_job(matched_job, v.get("title") or "")

                    # CHỈ LƯU SỐ LIỆU THẬT TỪ YOUTUBE ANALYTICS API v2 (Không ước lượng nội suy!)
                    final_apv = round(float(raw_apv), 1) if raw_apv is not None else None
                    final_stay = round(float(raw_stay), 1) if raw_stay is not None else None
                    final_swipe = (
                        round(float(raw_swipe), 1)
                        if raw_swipe is not None
                        else (round(max(0.0, 100.0 - final_stay), 1) if final_stay is not None else None)
                    )
                    final_avd = round(float(raw_avd), 1) if raw_avd is not None else None

                    existing_pv = db.query(PublishedVideo).filter(PublishedVideo.youtube_video_id == vid).first()
                    if existing_pv:
                        existing_pv.channel_id = canon_cid
                        existing_pv.title = v.get("title") or existing_pv.title
                        existing_pv.views = v_views
                        existing_pv.likes = v_likes
                        existing_pv.comments = v_comments
                        if final_apv is not None:
                            existing_pv.avg_view_percentage = final_apv
                        if final_stay is not None:
                            existing_pv.stayed_to_watch_pct = final_stay
                        if final_swipe is not None:
                            existing_pv.swipe_away_pct = final_swipe
                        if final_avd is not None:
                            existing_pv.avg_view_duration_sec = final_avd
                        existing_pv.last_synced_at = now_naive
                        if matched_job and not existing_pv.job_id:
                            existing_pv.job_id = matched_job.id
                        if hook_txt and not existing_pv.hook_text:
                            existing_pv.hook_text = hook_txt
                        if matched_job and matched_job.voice_engine and not existing_pv.voice_engine:
                            existing_pv.voice_engine = matched_job.voice_engine
                    else:
                        db.add(
                            PublishedVideo(
                                channel_id=canon_cid,
                                youtube_video_id=vid,
                                title=v.get("title") or "",
                                description=v.get("description") or "",
                                published_at=_parse_yt_time(v.get("published_at_utc")) or now_naive,
                                views=v_views,
                                likes=v_likes,
                                comments=v_comments,
                                avg_view_percentage=final_apv,
                                stayed_to_watch_pct=final_stay,
                                swipe_away_pct=final_swipe,
                                avg_view_duration_sec=final_avd,
                                hook_text=hook_txt,
                                voice_engine=matched_job.voice_engine if matched_job else None,
                                last_synced_at=now_naive,
                                source="auditor_sync",
                                job_id=matched_job.id if matched_job else None,
                            )
                        )
                    if matched_job:
                        matched_job.views = v_views
                        matched_job.likes = v_likes
                        matched_job.comments = v_comments
                        if final_apv is not None:
                            matched_job.avg_view_percentage = final_apv
                        if final_stay is not None:
                            matched_job.stayed_to_watch_pct = final_stay
                        if final_swipe is not None:
                            matched_job.swipe_away_pct = final_swipe
                        if final_avd is not None:
                            matched_job.avg_view_duration_sec = final_avd
                    upserted += 1

        _WEEKLY_PERFORMANCE_CACHE.clear()
        summary["db_upserted"] = upserted
        logger.info(
            f"🔄 [Auditor->Production] Đã đồng bộ {upserted} video từ {summary['channels_synced']} kênh "
            f"vào PublishedVideo DB & channel_audit_latest.json!"
        )
    except Exception as e:
        logger.warning(f"Đồng bộ Auditor vào Production DB tạm thời bỏ qua (chế độ độc lập): {e}")

    return summary


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

    # Đưa ngược toàn bộ chỉ số thật (AVD, APV%, Stay%, Swipe%, Winners/Losers, AI Insights) vào khâu sản xuất
    sync_telemetry_to_production(telemetry, insights)

    step_summary = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary:
        try:
            with open(step_summary, "a", encoding="utf-8") as f:
                f.write(md_report + "\n")
        except Exception:
            pass

    if "--no-telegram" not in sys.argv:
        send_telegram_direct(telemetry, insights)
        logger.info("📲 Đã gửi báo cáo độc lập (kèm AVD, APV% & Tỷ lệ bỏ qua) vào Nhóm Telegram!")

    print(md_report)


if __name__ == "__main__":
    main()
